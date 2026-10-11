"""Kivy application: project lifecycle, live validation, background jobs."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.progressbar import ProgressBar
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem

from .. import APP_NAME, __version__
from ..config import AppConfig, autosave_dir, cache_root
from ..jobs import JobRunner
from ..media import check_environment
from ..model import Project, ProjectLoadError, load_project, project_from_json_dict, project_to_json_dict, save_project
from ..parser import Diagnostic, parse_script
from ..preflight import run_preflight
from ..timeline import compile_timeline
from ..util import atomic_write_json
from . import platform as plat
from .widgets import DIM, FONT, ROW, FilePicker, button, choice, confirm, label, message

PROJECT_EXT = ".newsproj.json"

HELP = f"""{APP_NAME} {__version__} for tablets

1. Package: set the title, target, export profile and export folder.
2. Script: write or paste the script and insert markers with the buttons. The counters update as you type; the script turns red when the package is over target.
3. Media: import clips, stills, music and logos from the tablet's storage.
4. Markers: bind each marker to media, set in/out points, lower thirds, cards and statement subtitles.
5. Narration: record each block with the microphone, import a file, or generate text-to-speech (gTTS or ElevenLabs; needs internet).
6. Render: validate, look at single frames, build a low-resolution preview, then render. Exports go to the export folder with their subtitle, prompter, rundown and manifest files.

Markers: [BROLL_n] [IMAGE_n] visual for the following narration · [SOUNDBITE_n] statement with its own audio · [STANDUP_n] on-camera piece · [CARD_n] title/data card · [PAUSE=1.5] pause · [NOTE: …] never spoken or exported.

Not on the tablet version: local Whisper transcription (type statement subtitles in Markers instead), continuous-take mapping and SFX placement. Projects are the same files as the desktop app, so those can be done on a PC.
"""


class NewsEditorApp(App):
    title = APP_NAME

    def __init__(self, open_path: Optional[str] = None, env_problems: Optional[list[str]] = None, **kw):
        super().__init__(**kw)
        self.open_path = open_path
        self.env_problems = env_problems or []
        self.settings_cfg, self._cfg_warning = AppConfig.load()
        self.project = self._fresh_project()
        self.project_path: Optional[Path] = None
        self.dirty = False
        self.parse = None
        self.timeline = None
        self.diagnostics: list[Diagnostic] = []
        self.jobs = JobRunner()
        self._job_callbacks: dict[str, tuple] = {}
        self._refresh_ev = None
        self.api_key = ""
        self._sound = None

    # ------------------------------------------------------------------ build
    def build(self):
        from .tabs import MarkersTab, MediaTab, NarrationTab, PackageTab, RenderTab, ScriptTab

        Window.clearcolor = (0.08, 0.09, 0.1, 1)
        Window.softinput_mode = "below_target"
        Window.bind(on_keyboard=self._on_key)
        root = BoxLayout(orientation="vertical")
        top = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(4), padding=(dp(4), 0))
        self.title_lbl = label("", bold=True)
        self.title_lbl.size_hint_y = 1
        top.add_widget(self.title_lbl)
        for text, fn in (("New", self.new_project), ("Open", self.open_project), ("Save", self.save),
                         ("Save as", self.save_as), ("Help", self.show_help)):
            top.add_widget(button(text, fn, width=dp(92)))
        root.add_widget(top)

        self.tabs = TabbedPanel(do_default_tab=False, tab_width=dp(130), tab_height=ROW)
        self.script_tab = ScriptTab(self)
        self.media_tab = MediaTab(self)
        self.markers_tab = MarkersTab(self)
        self.narration_tab = NarrationTab(self)
        self.package_tab = PackageTab(self)
        self.render_tab = RenderTab(self)
        self._tab_items = {}
        for name, w in (("Script", self.script_tab), ("Media", self.media_tab), ("Markers", self.markers_tab),
                        ("Narration", self.narration_tab), ("Package", self.package_tab), ("Render", self.render_tab)):
            item = TabbedPanelItem(text=name, font_size=FONT)
            item.add_widget(w)
            self.tabs.add_widget(item)
            self._tab_items[name] = item
        root.add_widget(self.tabs)
        Clock.schedule_once(lambda *_: self.tabs.switch_to(self._tab_items["Script"]), 0)

        status = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6), padding=(dp(6), dp(2)))
        self.status_lbl = label("Ready", color=DIM, size=sp(14), shorten=True)
        self.status_lbl.size_hint_y = 1
        self.progress = ProgressBar(max=1.0, value=0, size_hint_x=None, width=dp(220), opacity=0)
        self.cancel_btn = button("Cancel job", self.cancel_jobs, width=dp(130), disabled=True)
        self.cancel_btn.height = dp(36)
        status.add_widget(self.status_lbl)
        status.add_widget(self.progress)
        status.add_widget(self.cancel_btn)
        root.add_widget(status)

        self.load_project_into_ui()
        Clock.schedule_interval(self._poll_jobs, 0.1)
        Clock.schedule_interval(self._autosave_tick, max(15, self.settings_cfg.autosave_seconds))
        Clock.schedule_once(self._startup, 0.6)
        return root

    def show_tab(self, name: str) -> None:
        self.tabs.switch_to(self._tab_items[name])

    # ------------------------------------------------------------------ state
    def _fresh_project(self) -> Project:
        p = Project()
        c = self.settings_cfg
        p.wpm = c.default_wpm
        p.target_seconds = c.default_target_seconds
        p.output.profile = c.default_profile
        p.visual.resize_mode = c.default_resize_mode
        p.export_directory = c.paths.exports or os.path.join(plat.default_work_dir(), "exports")
        return p

    def status(self, msg: str) -> None:
        self.status_lbl.text = msg

    def mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else "unsaved"
        self.title_lbl.text = f"{'* ' if self.dirty else ''}{self.project.title}  —  {name}"

    def script_changed(self, text: str) -> None:
        if self.project.script != text:
            self.project.script = text
            self.mark_dirty()
            self.schedule_refresh(0.3)

    def set_value(self, fn: Callable[[Project], None]) -> None:
        fn(self.project)
        self.mark_dirty()
        self.schedule_refresh()

    def schedule_refresh(self, delay: float = 0.25) -> None:
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
        self._refresh_ev = Clock.schedule_once(lambda *_: self.recompute(False), delay)

    def refresh_all(self) -> None:
        self.recompute(True)

    def recompute(self, full: bool = False) -> None:
        self._refresh_ev = None
        self.parse = parse_script(self.project.script)
        self.timeline, self.diagnostics = run_preflight(self.project, for_render=False,
                                                        tl=compile_timeline(self.project, self.parse))
        for tab in (self.script_tab, self.media_tab, self.markers_tab, self.narration_tab, self.package_tab,
                    self.render_tab):
            tab.refresh(full)
        errs = sum(1 for d in self.diagnostics if d.is_error)
        warns = sum(1 for d in self.diagnostics if d.severity == "warning")
        tl = self.timeline
        self._update_title()
        if not self.jobs.busy():
            self.status(f"{len(tl.segments)} segments · {tl.total_frames / tl.fps:.1f} s timeline · "
                        f"{errs} error(s), {warns} warning(s)")

    def load_project_into_ui(self) -> None:
        for tab in (self.script_tab, self.media_tab, self.markers_tab, self.narration_tab, self.package_tab,
                    self.render_tab):
            tab.load()
        self.refresh_all()

    def navigate_to(self, d: Diagnostic) -> None:
        if d.char_start is not None:
            self.show_tab("Script")
            self.script_tab.goto(d.char_start, d.char_end or d.char_start + 1)
        elif d.label:
            self.show_tab("Markers")
            self.markers_tab.select(d.label)
        elif d.segment.startswith("Narration"):
            self.show_tab("Narration")
        elif d.segment == "Export":
            self.show_tab("Package")

    # ------------------------------------------------------------------ folders
    def project_dir(self) -> Path:
        if self.project_path:
            return self.project_path.parent
        return cache_root() / "unsaved" / self.project.id

    def narration_dir(self) -> Path:
        if self.project_path:
            return self.project_path.parent / (self.project_path.name.replace(PROJECT_EXT, "") + "_narration")
        return self.project_dir() / "narration"

    def cache_dir(self) -> Path:
        return cache_root() / "projects" / self.project.id

    def last_folder(self) -> str:
        if self.project_path:
            return str(self.project_path.parent)
        return plat.default_work_dir()

    # ------------------------------------------------------------------ lifecycle
    def _guard_busy(self) -> bool:
        if self.jobs.busy():
            message(APP_NAME, "Wait for or cancel the running job first.")
            return True
        return False

    def _with_discard_check(self, then: Callable[[], None]) -> None:
        if not self.dirty:
            then()
            return
        choice("Unsaved changes", "Save changes to the current project first?",
               [("Cancel", lambda: None), ("Discard", then), ("Save", lambda: self.save(after=then))])

    def new_project(self) -> None:
        if self._guard_busy():
            return

        def go():
            self._clear_autosave()
            self.project = self._fresh_project()
            self.project_path = None
            self.dirty = False
            self.load_project_into_ui()

        self._with_discard_check(go)

    def open_project(self, path: Optional[str] = None) -> None:
        if self._guard_busy():
            return

        def pick():
            if path:
                self._open(path)
                return
            recent = [p for p in self.settings_cfg.recent_projects if os.path.exists(p)]
            if recent:
                opts = [(Path(p).name.replace(PROJECT_EXT, "")[:18], (lambda p=p: self._open(p))) for p in recent[:3]]
                opts.append(("Browse…", self._browse_open))
                choice("Open project", "Recent projects:\n" + "\n".join(recent[:3]), opts, size=(0.9, 0.5))
            else:
                self._browse_open()

        self._with_discard_check(pick)

    def _browse_open(self) -> None:
        FilePicker("Open project", lambda files: self._open(files[0]), exts=[".json"], start=self.last_folder())

    def _open(self, path: str) -> None:
        try:
            proj = load_project(path)
        except ProjectLoadError as exc:
            message("Open project", str(exc))
            return
        self._clear_autosave()
        self.project = proj
        self.project_path = Path(path)
        self.dirty = False
        self.settings_cfg.add_recent(str(self.project_path))
        self.settings_cfg.last_project = str(self.project_path)
        self._save_config()
        self.load_project_into_ui()
        from ..assets import missing_assets

        miss = missing_assets(self.project)
        if miss:
            confirm("Missing media", f"{len(miss)} media file(s) are missing:\n" +
                    "\n".join(f"• {a.name}" for a in miss[:8]) +
                    "\n\nSearch the project folder for files with the same names? (Projects made on a PC refer to "
                    "PC paths; copy the media next to the project file on the tablet.)",
                    self.media_tab.auto_relink, yes="Search", no="Later")

    def save(self, after: Optional[Callable[[], None]] = None) -> None:
        if self.project_path is None:
            self.save_as(after)
            return
        if self._write(self.project_path) and after:
            after()

    def save_as(self, after: Optional[Callable[[], None]] = None) -> None:
        from ..util import safe_filename

        def chosen(files):
            path = files[0]
            if not path.endswith(PROJECT_EXT):
                path = path[:-5] + PROJECT_EXT if path.endswith(".json") else path + PROJECT_EXT
            if self._write(Path(path)) and after:
                after()

        FilePicker("Save project", chosen, mode="save", exts=[".json"], start=self.last_folder(),
                   default_name=safe_filename(self.project.title) + PROJECT_EXT)

    def _write(self, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            save_project(self.project, path)
        except OSError as exc:
            message("Save", f"Could not save {path.name}: {exc.strerror or exc}\n"
                            "The previous version of the file was left unchanged.")
            return False
        self.project_path = path
        self.dirty = False
        self._clear_autosave()
        self.settings_cfg.add_recent(str(path))
        self.settings_cfg.last_project = str(path)
        self._save_config()
        self._update_title()
        self.status(f"Saved {path}")
        return True

    def _save_config(self) -> None:
        try:
            self.settings_cfg.save()
        except OSError as exc:
            self.status(f"Settings could not be saved: {exc}")

    # -- autosave -----------------------------------------------------------
    def _autosave_path(self) -> Path:
        return autosave_dir() / f"{self.project.id}.autosave.json"

    def _autosave_tick(self, *_):
        try:
            if self.dirty:
                d = project_to_json_dict(self.project, None)
                d["_autosave"] = {"original_path": str(self.project_path or ""), "saved_at": time.time()}
                atomic_write_json(self._autosave_path(), d)
                self.status(f"Autosaved at {time.strftime('%H:%M:%S')}")
        except OSError as exc:
            self.status(f"Autosave failed: {exc}")

    def _clear_autosave(self) -> None:
        try:
            self._autosave_path().unlink()
        except OSError:
            pass

    def _offer_recovery(self) -> None:
        d = autosave_dir()
        files = sorted(d.glob("*.autosave.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        if not files:
            return
        f = files[0]
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.pop("_autosave", {})
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("saved_at", f.stat().st_mtime)))
            title = data.get("title", "?")
        except (OSError, json.JSONDecodeError):
            return

        def recover():
            try:
                proj = project_from_json_dict(data, None)
            except ProjectLoadError as exc:
                message("Recover", f"The autosave is unreadable: {exc}")
                return
            self.project = proj
            orig = meta.get("original_path")
            self.project_path = Path(orig) if orig else None
            self.dirty = True
            self.load_project_into_ui()
            self.status("Recovered autosave. Save to keep it.")

        choice("Recover autosave", f"An autosaved version of “{title}” from {when} was found (unsaved changes from "
                                   "a previous session). Recover it?",
               [("Delete it", lambda: f.unlink(missing_ok=True)), ("Not now", lambda: None), ("Recover", recover)])

    # -- Android lifecycle -------------------------------------------------------
    def on_pause(self):
        self._autosave_tick()
        return True  # keep running (renders continue in the background)

    def on_stop(self):
        self.jobs.cancel()
        self._autosave_tick()

    def _on_key(self, _window, key, *_args):
        if key == 27:  # Android back button: never quit silently
            if self.jobs.busy():
                self.status("A job is running. Cancel it before leaving.")
                return True

            def leave():
                if not self.dirty:
                    self._clear_autosave()
                self.stop()

            if self.dirty:
                choice("Leave", "Save changes before closing?",
                       [("Stay", lambda: None), ("Discard", leave), ("Save", lambda: self.save(after=leave))])
            else:
                confirm("Leave", "Close the editor?", leave, yes="Close", no="Stay")
            return True
        return False

    # ------------------------------------------------------------------ checks
    def _startup(self, *_):
        if self._cfg_warning:
            message("Settings", self._cfg_warning)
        problems = list(self.env_problems)
        try:
            env = check_environment()
            problems += [m for s, m in env.problems if s == "error"]
        except Exception as exc:  # never block start-up on a diagnostic
            problems.append(f"Environment check failed: {exc}")
        if problems:
            message("Environment", "\n\n".join(problems) + "\n\nSee Help, then Environment.")
        if not plat.has_all_files_access():
            confirm("Storage access",
                    "To open media and save projects anywhere on the tablet, allow “All files access” for this app "
                    "on the next screen. Without it the app can only use its own private folder.",
                    plat.request_all_files_access, yes="Open settings", no="Not now")
        if self.open_path:
            self._open(self.open_path)
        else:
            self._offer_recovery()

    def show_help(self) -> None:
        choice("Help", HELP, [("Environment", self.show_environment), ("Close", lambda: None)], size=(0.9, 0.85))

    def show_environment(self) -> None:
        env = check_environment()
        lines = [f"Platform: {plat.platform_name()}", f"FFmpeg: {env.ffmpeg or 'NOT FOUND'}", f"  {env.ffmpeg_version}",
                 f"ffprobe: {env.ffprobe or 'NOT FOUND'}",
                 "Encoders: " + ", ".join(e for e in ("prores_ks", "dnxhd", "libx264", "aac", "pcm_s24le")
                                          if env.has_encoder(e)),
                 f"Subtitle font: {env.font or 'NONE'}", "", "Python modules:"]
        for k, v in env.modules.items():
            lines.append(f"  {k}: {v or 'not available'}")
        lines.append("")
        for s, m in env.problems:
            lines.append(f"[{s}] {m}")
        lines += ["", f"Settings: {AppConfig.path()}", f"Cache: {cache_root()}"]
        message("Environment", "\n".join(lines), size=(0.9, 0.85))

    # ------------------------------------------------------------------ jobs
    def start_job(self, name: str, label_: str, fn, on_done=None, on_fail=None) -> bool:
        if self.jobs.busy():
            message(APP_NAME, f"Another job is running ({self.jobs.running()[0].label}). Wait for it or cancel it.")
            return False
        self._job_callbacks[name] = (on_done, on_fail)
        self.jobs.start(name, label_, fn)
        self._job_ui(True, label_, None)
        plat.keep_screen_on(True)
        return True

    def _job_ui(self, running: bool, msg: str, frac: Optional[float] = None, elapsed: float = 0.0) -> None:
        self.progress.opacity = 1 if running else 0
        self.cancel_btn.disabled = not running
        if running:
            self.progress.value = frac if frac is not None else (time.monotonic() * 0.5) % 1.0
            self.status(f"{msg}  ({elapsed:.0f} s)" if elapsed else msg)
        else:
            self.status(msg)

    def _poll_jobs(self, *_):
        import queue

        try:
            while True:
                ev = self.jobs.events.get_nowait()
                done_cb, fail_cb = self._job_callbacks.get(ev.job, (None, None))
                if ev.kind == "progress":
                    self._job_ui(True, ev.message, ev.fraction, ev.elapsed)
                elif ev.kind == "done":
                    self._job_ui(False, f"{ev.message} in {ev.elapsed:.1f} s")
                    plat.keep_screen_on(False)
                    if done_cb:
                        done_cb(ev.result)
                elif ev.kind == "failed":
                    self._job_ui(False, "Job failed")
                    plat.keep_screen_on(False)
                    if not (fail_cb and fail_cb(ev.message)):
                        message("Job failed", ev.message[:2000])
                elif ev.kind == "cancelled":
                    self._job_ui(False, ev.message)
                    plat.keep_screen_on(False)
                    message(APP_NAME, f"{ev.message}. No partial output was kept; the project is unchanged.")
        except queue.Empty:
            pass

    def cancel_jobs(self) -> None:
        if self.jobs.busy():
            self.jobs.cancel()
            self._job_ui(True, "Cancelling…", None)

    # ------------------------------------------------------------------ audio
    def play_range(self, path: str, start: float = 0.0, duration: Optional[float] = None) -> None:
        """Play part of a media file: FFmpeg cuts it to a WAV, Kivy plays that."""
        from kivy.core.audio import SoundLoader

        from ..media import MediaError, ffmpeg_bin, run

        self.stop_audio()
        out = cache_root() / "playback.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", path]
        if duration:
            cmd += ["-t", f"{max(0.05, duration):.3f}"]
        cmd += ["-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out)]
        try:
            run(cmd, timeout=120)
        except MediaError as exc:
            message("Playback", str(exc))
            return
        snd = SoundLoader.load(str(out))
        if snd is None:
            message("Playback", "No audio output is available.")
            return
        self._sound = snd
        snd.play()
        self.status("Playing… (tap Stop audio to stop)")

    def stop_audio(self) -> None:
        if self._sound is not None:
            try:
                self._sound.stop()
                self._sound.unload()
            except Exception:
                pass
            self._sound = None

    def play_narration(self, na) -> None:
        if na.source == "take":
            take = next((t for t in self.project.narration.takes if t.id == na.take_id), None)
            if take:
                self.play_range(take.path, na.take_in + na.trim_head,
                                na.take_out - na.take_in - na.trim_head - na.trim_tail)
        elif na.wav_path:
            self.play_range(na.wav_path, na.trim_head, na.duration - na.trim_head - na.trim_tail)


def run_app(open_path: Optional[str] = None, env_problems: Optional[list[str]] = None) -> None:
    NewsEditorApp(open_path, env_problems).run()

