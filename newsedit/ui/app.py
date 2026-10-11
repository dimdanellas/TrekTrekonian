"""Main window: menus, layout, project lifecycle, live validation, jobs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .. import APP_NAME, __version__
from ..config import AppConfig, autosave_dir, cache_root
from ..jobs import JobRunner
from ..media import MediaError, check_environment
from ..model import Project, ProjectLoadError, load_project, project_from_json_dict, project_to_json_dict, save_project
from ..parser import Diagnostic, parse_script
from ..preflight import run_preflight
from ..timeline import compile_timeline
from ..util import atomic_write_json, open_in_file_manager
from .automation_panel import AutomationPanel
from .dialogs import HELP_TEXT, CueEditor, PathsDialog, PromptsDialog, TakeMapper
from .media_panel import MediaPanel
from .preview_panel import PreviewPanel
from .script_panel import ScriptPanel
from .widgets import BG, apply_dark_theme, text_dialog

PROJECT_EXT = ".newsproj.json"
KEYRING_SERVICE = "news_package_editor.elevenlabs"


class App:
    def __init__(self, root: tk.Tk, open_path: Optional[str] = None):
        self.root = root
        self.config, cfg_warning = AppConfig.load()
        apply_dark_theme(root)
        root.title(APP_NAME)
        root.geometry(self.config.ui_geometry or "1100x700")
        root.minsize(960, 600)
        self.project = Project()
        self.project.wpm = self.config.default_wpm
        self.project.target_seconds = self.config.default_target_seconds
        self.project_path: Optional[Path] = None
        self.dirty = False
        self.parse = None
        self.timeline = None
        self.diagnostics: list[Diagnostic] = []
        self.jobs = JobRunner()
        self.recorder = None
        self._rec_block = None
        self._refresh_job = None
        self._api_key = ""
        self._remember_key = False
        self._stop_audio = None
        self._job_callbacks = {}
        self._build_menu()
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var, style="Dim.TLabel", anchor="w").pack(fill="x", side="bottom")
        self._build_layout()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._bind_keys()
        self.load_project_into_ui()
        if cfg_warning:
            root.after(300, lambda: messagebox.showwarning("Settings", cfg_warning, parent=root))
        root.after(200, self._startup_checks)
        root.after(100, self._poll_jobs)
        root.after(self.config.autosave_seconds * 1000, self._autosave_tick)
        if open_path:
            root.after(400, lambda: self.open_project(open_path))
        else:
            root.after(500, self._offer_recovery)

    # ------------------------------------------------------------------ layout
    def _build_menu(self):
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="New", accelerator="Ctrl+N", command=self.new_project)
        fm.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_project)
        self.recent_menu = tk.Menu(fm, tearoff=0)
        fm.add_cascade(label="Open recent", menu=self.recent_menu)
        fm.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        fm.add_command(label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as)
        fm.add_command(label="Recover autosave…", command=lambda: self._offer_recovery(explicit=True))
        fm.add_separator()
        fm.add_command(label="Import script text…", command=self.import_script)
        fm.add_command(label="Export prompter TXT", command=lambda: self.export_companion("prompter"))
        fm.add_command(label="Export rundown CSV", command=lambda: self.export_companion("rundown"))
        fm.add_command(label="Export segment rundown CSV", command=lambda: self.export_companion("segments"))
        fm.add_command(label="Export subtitle sidecars", command=lambda: self.export_companion("subtitles"))
        fm.add_separator()
        fm.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        m.add_cascade(label="File", menu=fm)
        em = tk.Menu(m, tearoff=0)
        em.add_command(label="Undo", accelerator="Ctrl+Z", command=lambda: self.script.undo())
        em.add_command(label="Redo", accelerator="Ctrl+Y", command=lambda: self.script.redo())
        em.add_separator()
        em.add_command(label="Cut", accelerator="Ctrl+X", command=lambda: self._edit("<<Cut>>"))
        em.add_command(label="Copy", accelerator="Ctrl+C", command=lambda: self._edit("<<Copy>>"))
        em.add_command(label="Paste", accelerator="Ctrl+V", command=lambda: self._edit("<<Paste>>"))
        em.add_command(label="Select all", accelerator="Ctrl+A", command=lambda: self._edit("<<SelectAll>>"))
        em.add_separator()
        em.add_command(label="Insert editorial note", command=lambda: self.script.insert_note())
        em.add_command(label="Normalize legacy tags", command=lambda: self.script.normalize())
        m.add_cascade(label="Edit", menu=em)
        sm = tk.Menu(m, tearoff=0)
        sm.add_command(label="Library paths & defaults…", command=lambda: PathsDialog(self))
        sm.add_command(label="Environment check…", command=self.show_environment)
        m.add_cascade(label="Settings/Paths", menu=sm)
        am = tk.Menu(m, tearoff=0)
        am.add_command(label="Edit templates…", command=lambda: PromptsDialog(self))
        self.prompts_menu = tk.Menu(am, tearoff=0, postcommand=self._fill_prompts_menu)
        am.add_cascade(label="Copy template", menu=self.prompts_menu)
        m.add_cascade(label="AI Prompts", menu=am)
        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="Help", accelerator="F1", command=self.show_help)
        hm.add_command(label="About", command=self.show_about)
        m.add_cascade(label="Help/About", menu=hm)
        self.root.config(menu=m)
        self._fill_recent()

    def _build_layout(self):
        outer = ttk.PanedWindow(self.root, orient="vertical")
        outer.pack(fill="both", expand=True, padx=4, pady=4)
        top = ttk.PanedWindow(outer, orient="horizontal")
        self.script = ScriptPanel(top, self)
        top.add(self.script, weight=5)
        right = ttk.Notebook(top)
        self.media = MediaPanel(right, self)
        self.automation = AutomationPanel(right, self)
        right.add(self.media, text="Media & Properties")
        right.add(self.automation, text="Automation & Settings")
        top.add(right, weight=4)
        outer.add(top, weight=3)
        self.preview = PreviewPanel(outer, self)
        outer.add(self.preview, weight=2)

    def _bind_keys(self):
        r = self.root
        r.bind_all("<Control-n>", lambda e: self.new_project())
        r.bind_all("<Control-o>", lambda e: self.open_project())
        r.bind_all("<Control-s>", lambda e: self.save())
        r.bind_all("<Control-S>", lambda e: self.save_as())
        r.bind_all("<Control-q>", lambda e: self.on_close())
        r.bind_all("<F1>", lambda e: self.show_help())

    def _edit(self, ev):
        w = self.root.focus_get() or self.script.text
        try:
            w.event_generate(ev)
        except tk.TclError:
            pass

    def status(self, msg: str):
        self.status_var.set(msg)

    # ------------------------------------------------------------------ state
    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self):
        name = self.project_path.name if self.project_path else "Untitled"
        self.root.title(f"{'*' if self.dirty else ''}{self.project.title} — {name} — {APP_NAME}")

    def script_changed(self, text: str, user: bool):
        if self.project.script != text:
            self.project.script = text
            if user:
                self.mark_dirty()
        self.schedule_refresh(80)

    def set_project_value(self, fn):
        fn(self.project)
        self.mark_dirty()
        self.schedule_refresh()

    def schedule_refresh(self, delay: int = 200):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = self.root.after(delay, lambda: self.recompute(full=False))

    def refresh_all(self):
        self.recompute(full=True)

    def recompute(self, full: bool = False):
        self._refresh_job = None
        self.parse = parse_script(self.project.script)
        self.timeline, self.diagnostics = run_preflight(self.project, for_render=False,
                                                        tl=compile_timeline(self.project, self.parse))
        self.script.update_stats(self.timeline, self.project)
        self.script.mark_errors(self.diagnostics)
        self.preview.show_diagnostics(self.diagnostics)
        self.media.refresh(full=full)
        self.automation.refresh()
        errs = sum(1 for d in self.diagnostics if d.is_error)
        warns = sum(1 for d in self.diagnostics if d.severity == "warning")
        self.status(f"{len(self.timeline.segments)} segments · {self.timeline.total_frames / self.timeline.fps:.1f} s "
                    f"timeline · {errs} error(s), {warns} warning(s)")

    def load_project_into_ui(self):
        self.script.v_wpm.set(self.project.wpm)
        self.script.v_target.set(self.project.target_seconds)
        self.script.set(self.project.script)
        self.automation.load()
        self.preview.close_player()
        self.refresh_all()
        self._update_title()

    def goto_script(self, start: int, end: int):
        self.script.goto(start, end)

    def navigate_to(self, d: Diagnostic):
        if d.char_start is not None:
            self.goto_script(d.char_start, d.char_end or d.char_start + 1)
        if d.label:
            self.media.select_label(d.label)
        elif d.segment.startswith("Narration"):
            self.automation.nb.select(self.automation.narr)
        elif d.segment == "Export":
            self.automation.nb.select(self.automation.export_tab)

    # ------------------------------------------------------------------ dirs
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

    # ------------------------------------------------------------------ lifecycle
    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel("Unsaved changes", "Save changes to the current project first?",
                                        parent=self.root)
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    def new_project(self):
        if self.jobs.busy():
            messagebox.showinfo(APP_NAME, "Wait for or cancel the running job first.", parent=self.root)
            return
        if not self.confirm_discard():
            return
        self._clear_autosave()
        self.project = Project()
        self.project.wpm = self.config.default_wpm
        self.project.target_seconds = self.config.default_target_seconds
        self.project.output.profile = self.config.default_profile
        self.project.visual.resize_mode = self.config.default_resize_mode
        if self.config.paths.exports:
            self.project.export_directory = self.config.paths.exports
        self.project_path = None
        self.dirty = False
        self.load_project_into_ui()

    def open_project(self, path: Optional[str] = None):
        if self.jobs.busy():
            messagebox.showinfo(APP_NAME, "Wait for or cancel the running job first.", parent=self.root)
            return
        if not self.confirm_discard():
            return
        if not path:
            path = filedialog.askopenfilename(parent=self.root, title="Open project",
                                              filetypes=[("News package project", "*" + PROJECT_EXT),
                                                         ("JSON", "*.json"), ("All", "*")])
            if not path:
                return
        try:
            proj = load_project(path)
        except ProjectLoadError as exc:
            messagebox.showerror("Open project", str(exc), parent=self.root)
            return
        self._clear_autosave()
        self.project = proj
        self.project_path = Path(path)
        self.dirty = False
        self.config.add_recent(str(self.project_path))
        self.config.last_project = str(self.project_path)
        self._save_config()
        self._fill_recent()
        self.load_project_into_ui()
        from ..assets import missing_assets

        miss = missing_assets(self.project)
        if miss:
            if messagebox.askyesno("Missing media", f"{len(miss)} media file(s) are missing:\n" +
                                   "\n".join(a.name for a in miss[:8]) +
                                   "\n\nSearch the library folders and the project folder for same-named files?",
                                   parent=self.root):
                self.media.assets_tab.auto_relink()

    def save(self) -> bool:
        if self.project_path is None:
            return self.save_as()
        return self._write(self.project_path)

    def save_as(self) -> bool:
        path = filedialog.asksaveasfilename(parent=self.root, title="Save project", defaultextension=PROJECT_EXT,
                                            filetypes=[("News package project", "*" + PROJECT_EXT)],
                                            initialfile=f"{self.project.title}{PROJECT_EXT}")
        if not path:
            return False
        if not path.endswith(PROJECT_EXT):
            path = path.rsplit(".json", 1)[0] + PROJECT_EXT if path.endswith(".json") else path + PROJECT_EXT
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        try:
            save_project(self.project, path)
        except OSError as exc:
            messagebox.showerror("Save", f"Could not save {path.name}: {exc.strerror or exc}\n"
                                         "The previous version of the file was left unchanged.", parent=self.root)
            return False
        self.project_path = path
        self.dirty = False
        self._clear_autosave()
        self.config.add_recent(str(path))
        self.config.last_project = str(path)
        self._save_config()
        self._fill_recent()
        self._update_title()
        self.status(f"Saved {path}")
        return True

    def _save_config(self):
        try:
            self.config.save()
        except OSError as exc:
            self.status(f"Settings could not be saved: {exc}")

    def _fill_recent(self):
        self.recent_menu.delete(0, "end")
        for p in self.config.recent_projects:
            self.recent_menu.add_command(label=p, command=lambda p=p: self.open_project(p))
        if not self.config.recent_projects:
            self.recent_menu.add_command(label="(none)", state="disabled")

    def import_script(self):
        f = filedialog.askopenfilename(parent=self.root, filetypes=[("Text", "*.txt"), ("All", "*")])
        if not f:
            return
        try:
            text = Path(f).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror("Import", str(exc), parent=self.root)
            return
        self.script.text.edit_separator()
        self.script.text.delete("1.0", "end")
        self.script.text.insert("1.0", text)
        self.script.text.edit_separator()
        res = parse_script(text)
        if res.needs_normalization and messagebox.askyesno(
                "Legacy markers", "The script uses legacy markers ([INSERT_…] or START/END pairs). Normalise them "
                                  "now? (Undo is available.)", parent=self.root):
            self.script.normalize()

    # -- autosave ---------------------------------------------------------
    def _autosave_path(self) -> Path:
        return autosave_dir() / f"{self.project.id}.autosave.json"

    def _autosave_tick(self):
        try:
            if self.dirty:
                d = project_to_json_dict(self.project, None)
                d["_autosave"] = {"original_path": str(self.project_path or ""), "saved_at": time.time()}
                atomic_write_json(self._autosave_path(), d)
                self.status(f"Autosaved at {time.strftime('%H:%M:%S')}")
        except OSError as exc:
            self.status(f"Autosave failed: {exc}")
        finally:
            self.root.after(max(15, self.config.autosave_seconds) * 1000, self._autosave_tick)

    def _clear_autosave(self):
        try:
            self._autosave_path().unlink()
        except OSError:
            pass

    def _offer_recovery(self, explicit: bool = False):
        d = autosave_dir()
        files = sorted(d.glob("*.autosave.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        if not files:
            if explicit:
                messagebox.showinfo("Recover", "No autosaved projects found.", parent=self.root)
            return
        f = files[0]
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.pop("_autosave", {})
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("saved_at", f.stat().st_mtime)))
            title = data.get("title", "?")
        except (OSError, json.JSONDecodeError):
            return
        if not messagebox.askyesno("Recover autosave",
                                   f"An autosaved version of “{title}” from {when} was found "
                                   "(unsaved changes from a previous session). Recover it?", parent=self.root):
            if messagebox.askyesno("Recover autosave", "Delete this autosave?", parent=self.root):
                f.unlink(missing_ok=True)
            return
        try:
            proj = project_from_json_dict(data, None)
        except ProjectLoadError as exc:
            messagebox.showerror("Recover", f"The autosave is unreadable: {exc}", parent=self.root)
            return
        self.project = proj
        orig = meta.get("original_path")
        self.project_path = Path(orig) if orig else None
        self.dirty = True
        self.load_project_into_ui()
        self.status("Recovered autosave — save to keep it.")

    def on_close(self):
        if self.jobs.busy():
            if not messagebox.askyesno("Job running", "A job is still running. Cancel it and quit?",
                                       parent=self.root):
                return
            self.jobs.cancel()
            self.jobs.wait_all(20)
        if self.recorder is not None:
            self.recorder.stop()
        if not self.confirm_discard():
            return
        self.preview.close_player()
        if self._stop_audio:
            try:
                self._stop_audio()
            except Exception:
                pass
        self.config.ui_geometry = self.root.geometry().split("+")[0]
        self._save_config()
        self._clear_autosave()
        self.root.destroy()

    # ------------------------------------------------------------------ checks
    def _startup_checks(self):
        env = check_environment()
        errors = [m for s, m in env.problems if s == "error"]
        problems = self.config.validate_paths()
        if errors:
            messagebox.showerror("Environment", "\n\n".join(errors) + "\n\nSee Settings → Environment check.",
                                 parent=self.root)
        if problems:
            self.status(" · ".join(problems))

    def show_environment(self):
        env = check_environment()
        lines = [f"FFmpeg: {env.ffmpeg or 'NOT FOUND'}", f"  {env.ffmpeg_version}", f"ffprobe: {env.ffprobe or 'NOT FOUND'}",
                 "Encoders: " + ", ".join(e for e in ("prores_ks", "dnxhd", "libx264", "aac", "pcm_s24le")
                                          if env.has_encoder(e)),
                 f"Subtitle font: {env.font or 'NONE'}", "", "Python modules:"]
        for k, v in env.modules.items():
            lines.append(f"  {k:15s} {v or 'not available'}")
        lines.append("")
        for s, m in env.problems:
            lines.append(f"[{s}] {m}")
        lines.append("")
        lines.append(f"Settings: {AppConfig.path()}")
        lines.append(f"Cache: {cache_root()}")
        text_dialog(self.root, "Environment check", "\n".join(lines), 90, 28)

    def show_help(self):
        text_dialog(self.root, "Help", HELP_TEXT, 100, 40)

    def show_about(self):
        messagebox.showinfo("About", f"{APP_NAME} {__version__}\n\nPython/Tkinter desktop editor for short "
                                     "television news packages.\nMedia: FFmpeg + MoviePy. Local Whisper "
                                     "transcription. TTS: ElevenLabs, gTTS.", parent=self.root)

    def _fill_prompts_menu(self):
        self.prompts_menu.delete(0, "end")
        for p in self.config.prompts:
            self.prompts_menu.add_command(label=p.get("name", "?"), command=lambda t=p.get("text", ""): (
                self.root.clipboard_clear(), self.root.clipboard_append(t), self.status("Template copied")))

    # ------------------------------------------------------------------ jobs
    def start_job(self, name: str, label: str, fn, on_done=None, on_fail=None):
        if self.jobs.busy():
            messagebox.showinfo(APP_NAME, f"Another job is running ({self.jobs.running()[0].label}). "
                                          "Wait for it or cancel it first.", parent=self.root)
            return False
        self._job_callbacks[name] = (on_done, on_fail)
        self.jobs.start(name, label, fn)
        self.preview.job_state(True, label, None)
        return True

    def _poll_jobs(self):
        try:
            while True:
                ev = self.jobs.events.get_nowait()
                done_cb, fail_cb = self._job_callbacks.get(ev.job, (None, None))
                if ev.kind == "progress":
                    self.preview.job_state(True, ev.message, ev.fraction, ev.elapsed)
                elif ev.kind == "done":
                    self.preview.job_state(False, f"{ev.message} in {ev.elapsed:.1f} s")
                    if done_cb:
                        done_cb(ev.result)
                elif ev.kind == "failed":
                    self.preview.job_state(False, "Job failed")
                    if fail_cb and fail_cb(ev.message):
                        pass
                    else:
                        messagebox.showerror("Job failed", ev.message[:2000], parent=self.root)
                elif ev.kind == "cancelled":
                    self.preview.job_state(False, ev.message)
                    messagebox.showinfo(APP_NAME, f"{ev.message}. No partial output was kept; the project is "
                                                  "unchanged.", parent=self.root)
        except Exception as exc:  # queue.Empty or UI errors
            if exc.__class__.__name__ != "Empty":
                self.status(f"UI error: {exc}")
        finally:
            if self.recorder is not None:
                self._poll_recorder()
            self.root.after(100, self._poll_jobs)

    def cancel_jobs(self):
        if self.jobs.busy():
            self.jobs.cancel()
            self.preview.job_state(True, "Cancelling…", None)

    # ------------------------------------------------------------------ audio helpers
    def play_audio_file(self, path: str, start: float = 0.0, duration: Optional[float] = None):
        from ..narration import play_audio_file

        if self._stop_audio:
            try:
                self._stop_audio()
            except Exception:
                pass
        try:
            self._stop_audio = play_audio_file(path, start, duration)
        except MediaError as exc:
            ffplay = shutil.which("ffplay")
            if ffplay:
                cmd = [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", "-ss", f"{start:.3f}"]
                if duration:
                    cmd += ["-t", f"{duration:.3f}"]
                subprocess.Popen(cmd + [path])
                self.status("Playing through ffplay (no audio device for embedded playback).")
            else:
                messagebox.showwarning("Playback", f"{exc}", parent=self.root)

    def play_narration(self, na):
        if na.source == "take":
            take = next((t for t in self.project.narration.takes if t.id == na.take_id), None)
            if take:
                self.play_audio_file(take.path, na.take_in + na.trim_head,
                                     na.take_out - na.take_in - na.trim_head - na.trim_tail)
        elif na.wav_path:
            self.play_audio_file(na.wav_path, na.trim_head, na.duration - na.trim_head - na.trim_tail)

    # ------------------------------------------------------------------ narration
    def _narr_seg(self, idx):
        tl = self.timeline
        return next((s for s in tl.segments if s.kind == "narration" and s.block_index == idx), None) if tl else None

    def record_start(self):
        from ..narration import Recorder

        if self.recorder is not None:
            return
        sel = self.automation.narr.selected_blocks()
        if len(sel) != 1:
            self.automation.nb.select(self.automation.narr)
            messagebox.showinfo("Record", "Select one narration block in Automation → Narration, then press Record.",
                                parent=self.root)
            return
        seg = self._narr_seg(sel[0])
        out = self.narration_dir() / f"rec_{sel[0] + 1:02d}_{int(time.time())}.wav"
        self.recorder = Recorder(out, self.automation.narr.device_index())
        try:
            self.recorder.start()
        except MediaError as exc:
            self.recorder = None
            messagebox.showerror("Record", str(exc), parent=self.root)
            return
        self._rec_block = (sel[0], seg.text)
        self.automation.narr.rec_btn.state(["disabled"])
        self.automation.narr.stop_btn.state(["!disabled"])

    def _poll_recorder(self):
        r = self.recorder
        peak = None
        while not r.events.empty():
            ev = r.events.get_nowait()
            if ev.kind == "level":
                peak = ev.value
        if peak is not None:
            self.automation.narr.level["value"] = min(1.0, peak)
        self.automation.narr.rec_lbl.configure(text=f"● Recording block {self._rec_block[0] + 1}: "
                                                    f"{r.elapsed():.1f} s" + ("  CLIPPING!" if peak and peak >= 0.99 else ""))

    def record_stop(self):
        from ..narration import register_recording

        r = self.recorder
        if r is None:
            return
        path = r.stop()
        self.recorder = None
        self.automation.narr.rec_btn.state(["!disabled"])
        self.automation.narr.stop_btn.state(["disabled"])
        self.automation.narr.level["value"] = 0
        idx, text = self._rec_block
        try:
            na = register_recording(self.project, idx, text, path)
        except MediaError as exc:
            messagebox.showerror("Record", f"The recording could not be read: {exc}", parent=self.root)
            return
        self.automation.narr.rec_lbl.configure(text=f"Recorded block {idx + 1}: {na.duration:.1f} s")
        self.mark_dirty()
        self.schedule_refresh()

    def import_narration_file(self, idx: int, path: str):
        from ..narration import import_block_file

        seg = self._narr_seg(idx)
        try:
            import_block_file(self.project, idx, seg.text, path, self.narration_dir())
        except MediaError as exc:
            messagebox.showerror("Import narration", str(exc), parent=self.root)
            return
        self.mark_dirty()
        self.schedule_refresh()

    def import_take(self, path: str):
        from ..narration import import_take

        try:
            take = import_take(self.project, path, self.narration_dir())
        except MediaError as exc:
            messagebox.showerror("Import take", str(exc), parent=self.root)
            return
        self.mark_dirty()
        self.schedule_refresh()
        self.status(f"Imported take {take.duration:.1f} s — map its ranges to blocks.")
        self.root.after(300, self.open_take_mapper)

    def open_take_mapper(self):
        self.recompute()
        TakeMapper(self)

    def set_api_key(self, key: str):
        self._api_key = key.strip()
        if self._remember_key:
            self._store_key()

    def remember_api_key(self, on: bool):
        self._remember_key = on
        if on:
            self._store_key()
        else:
            try:
                import keyring

                keyring.delete_password(KEYRING_SERVICE, "api_key")
            except Exception:
                pass

    def _store_key(self):
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, "api_key", self._api_key)
        except Exception as exc:
            self.status(f"OS keychain unavailable: {exc.__class__.__name__}")

    def _get_key(self) -> str:
        if self._api_key:
            return self._api_key
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, "api_key") or ""
        except Exception:
            return ""

    def load_voices(self):
        from ..narration import ElevenLabsProvider

        key = self._get_key()
        self.start_job("voices", "Loading ElevenLabs voices",
                       lambda cancel, progress: ElevenLabsProvider.list_voices(key),
                       on_done=lambda v: (self.automation.narr.set_voices(v),
                                          self.status(f"{len(v)} voices loaded")))

    def generate_tts(self, blocks: Optional[list[int]], provider: Optional[str] = None):
        from ..narration import make_provider

        self.recompute()
        tl = self.timeline
        segs = [s for s in tl.segments if s.kind == "narration"]
        if blocks is None:
            segs = [s for s in segs if s.narration is None or s.stale]
        else:
            segs = [s for s in segs if s.block_index in blocks]
        if not segs:
            messagebox.showinfo("Narration", "Every narration block already has current audio.", parent=self.root)
            return
        provider = provider or self.project.narration.provider
        if self.project.narration.mode != "tts" and blocks is None:
            if not messagebox.askyesno("Narration", "Narration mode is Manual Recording. Generate TTS for the "
                                                    f"{len(segs)} block(s) without audio anyway?", parent=self.root):
                return
        if not messagebox.askokcancel("Online text-to-speech",
                                      f"{len(segs)} narration block(s) will be sent to "
                                      f"{'ElevenLabs' if provider == 'elevenlabs' else 'Google (gTTS)'} for "
                                      "synthesis. Continue?", parent=self.root):
            return
        prov = make_provider(self.project, provider, self._get_key())
        ndir = self.narration_dir()
        items = [(s.block_index, s.text) for s in segs]

        def work(cancel, progress):
            from ..media import media_duration

            out = []
            ndir.mkdir(parents=True, exist_ok=True)
            for k, (idx, text) in enumerate(items):
                if cancel():
                    break
                progress(f"TTS block {idx + 1} ({k + 1}/{len(items)})", k / len(items))
                wav = ndir / f"tts_{prov.name}_{prov.cache_key(text)[:16]}.wav"
                if not wav.exists():
                    prov.synthesize(text, wav)
                out.append((idx, text, str(wav), media_duration(wav)))
            return out

        def done(results):
            from ..model import NarrationAudio
            from ..narration import upsert_block_audio
            from ..timeline import block_hash

            for idx, text, wav, dur in results:
                upsert_block_audio(self.project, NarrationAudio(
                    idx, block_hash(text), text, "tts", wav, provider=prov.name,
                    voice=getattr(prov, "voice_id", "") or getattr(prov, "language", ""), duration=dur))
            self.mark_dirty()
            self.schedule_refresh()
            self.status(f"Generated narration for {len(results)} block(s).")

        def fail(msg):
            if provider == "elevenlabs":
                if messagebox.askyesno("ElevenLabs failed",
                                       f"{msg}\n\nUse gTTS instead? The narration text will be sent to Google's "
                                       "speech service.", parent=self.root):
                    self.root.after(100, lambda: self.generate_tts(blocks, "gtts"))
                return True
            return False

        self.start_job("tts", "Generating narration", work, done, fail)

    # ------------------------------------------------------------------ subtitles
    def transcribe(self, labels: Optional[list[str]]):
        from ..model import SubtitleTrack
        from ..subtitles import format_segments, transcribe_clip
        from ..timeline import binding_trim

        self.recompute()
        p = self.project
        tl = self.timeline
        todo = []
        for s in tl.segments:
            if s.kind in ("soundbite", "standup") and (labels is None or s.label in labels):
                if s.label in [t[0] for t in todo]:
                    continue
                b = p.bindings.get(s.label)
                a = p.asset(b.asset_id) if b else None
                tin, tout, _ = binding_trim(p, b)
                if a is None or tin is None or tout is None or tout <= tin or b.silent or not a.meta.get("has_audio"):
                    continue
                tr = p.subtitle_tracks.get(s.label)
                if tr is not None and tr.cues and tr.reviewed and labels is None:
                    continue  # keep reviewed edits
                todo.append((s.label, a.path, tin, tout, b.language))
        if not todo:
            messagebox.showinfo("Subtitles", "No bound statements need transcription (reviewed tracks are kept).",
                                parent=self.root)
            return
        st = p.subtitles
        cache = self.cache_dir() / "whisper"

        def work(cancel, progress):
            out = []
            for k, (label, path, tin, tout, lang) in enumerate(todo):
                progress(f"Transcribing {label} locally ({k + 1}/{len(todo)})", None)
                segs = transcribe_clip(path, tin, tout, lang, st.whisper_model, st.whisper_device, cache, cancel)
                out.append((label, tin, tout, lang, segs))
            return out

        def done(results):
            for label, tin, tout, lang, segs in results:
                existing = p.subtitle_tracks.get(label)
                if existing is not None and existing.cues and existing.reviewed:
                    if not messagebox.askyesno("Subtitles", f"{label} has reviewed cues. Replace them with the new "
                                                            "transcription?", parent=self.root):
                        continue
                cues = format_segments(segs, st)
                p.subtitle_tracks[label] = SubtitleTrack(label, lang, "whisper", st.whisper_model, tin, tout, False, cues)
            self.mark_dirty()
            self.schedule_refresh()
            self.open_cue_editor(results[0][0] if results else None)

        self.start_job("transcribe", "Transcribing statements", work, done)

    def open_cue_editor(self, label: Optional[str]):
        self.recompute()
        CueEditor(self, label)

    # ------------------------------------------------------------------ preview
    def build_preview(self):
        from ..preview import build_proxy

        self.recompute()
        tl = self.timeline
        if not tl.segments or tl.total_frames == 0:
            messagebox.showinfo("Preview", "Nothing to preview yet.", parent=self.root)
            return
        blocking = [d for d in self.diagnostics if d.is_error and d.code in (
            "unbound", "bad_trim", "asset_missing", "asset_removed", "missing_end", "mismatched_end", "nested_tag",
            "stray_end", "unknown_tag", "no_visual", "statement_no_audio", "font", "environment")]
        if blocking:
            messagebox.showerror("Preview", "Fix these first:\n" + "\n".join(f"• {d.message}" for d in blocking[:8]),
                                 parent=self.root)
            return
        fr = None
        if self.preview.scope.get() == "segment":
            sel = self.media.timeline_tab.tree.selection()
            if not sel or sel[0] == "total":
                messagebox.showinfo("Preview", "Select a segment in Media → Timeline first.", parent=self.root)
                return
            s = tl.segments[int(sel[0])]
            fr = (s.start_frame, s.end_frame)
        snap = self.project.snapshot()
        snap_tl = compile_timeline(snap)
        cache = self.cache_dir() / "preview"
        self.preview.close_player()
        self.start_job("preview", "Building preview proxy",
                       lambda cancel, progress: build_proxy(snap, snap_tl, cache, cancel, progress, fr),
                       on_done=self.preview.load_proxy)

    def open_external_preview(self):
        from ..preview import open_external

        info = self.preview.proxy_info
        if not info:
            messagebox.showinfo("Preview", "Build a preview first.", parent=self.root)
            return
        try:
            open_external(info["path"], self.config.external_player)
        except OSError as exc:
            messagebox.showerror("Preview", f"Could not start the external player: {exc}", parent=self.root)

    def inspect_frame(self):
        """Full-resolution still at the playhead, rendered by the final renderer."""
        from ..profiles import get_profile
        from ..render import FrameRenderer
        from ..subtitles import build_timeline_cues, place_cues

        self.recompute()
        tl = self.timeline
        if not tl.segments:
            return
        frame = min(tl.total_frames - 1, self.preview.playhead_frame()) if self.preview.player else 0
        snap = self.project.snapshot()
        prof = get_profile(snap.output.profile, snap.output.overrides)

        def work(cancel, progress):
            stl = compile_timeline(snap)
            cues, _ = build_timeline_cues(snap, stl)
            place_cues(snap, stl, cues, prof["width"], prof["height"])
            r = FrameRenderer(snap, stl, prof["width"], prof["height"], cues)
            try:
                return frame, r.frame(frame)
            finally:
                r.close()

        def done(res):
            from PIL import Image, ImageTk

            f, img = res
            top = tk.Toplevel(self.root)
            top.title(f"Frame {f} ({img.shape[1]}×{img.shape[0]})")
            im = Image.fromarray(img)
            im.thumbnail((1280, 720))
            ph = ImageTk.PhotoImage(im)
            lbl = tk.Label(top, image=ph, bg=BG)
            lbl.image = ph
            lbl.pack()

        self.start_job("inspect", "Rendering full-resolution frame", work, done)

    # ------------------------------------------------------------------ delivery
    def validate(self):
        self.recompute()
        snap = self.project.snapshot()

        def work(cancel, progress):
            progress("Validating (including encoder test)", None)
            return run_preflight(snap, for_render=True, test_encoder=True,
                                 custom_profiles=self.config.custom_profiles)[1]

        def done(diags):
            self.preview.show_diagnostics(diags)
            errs = [d for d in diags if d.is_error]
            if errs:
                messagebox.showwarning("Validate", f"{len(errs)} blocking error(s). Double-click a diagnostic to go "
                                                   "to it.", parent=self.root)
            else:
                warns = sum(1 for d in diags if d.severity == "warning")
                messagebox.showinfo("Validate", f"Ready to render. {warns} warning(s) to review.", parent=self.root)

        self.start_job("validate", "Validating", work, done)

    def render(self):
        from ..export import export_package, timeline_hash

        self.recompute()
        tl, diags = run_preflight(self.project, for_render=True, custom_profiles=self.config.custom_profiles)
        errs = [d for d in diags if d.is_error]
        self.preview.show_diagnostics(diags)
        if errs:
            messagebox.showerror("Render blocked", f"{len(errs)} blocking error(s):\n" +
                                 "\n".join(f"• {d.message}" for d in errs[:10]), parent=self.root)
            return
        warns = [d for d in diags if d.severity == "warning"]
        if warns and not messagebox.askokcancel(
                "Render", f"{len(warns)} warning(s) will be recorded as acknowledged in the export manifest:\n" +
                          "\n".join(f"• {d.message}" for d in warns[:8]) + "\n\nRender now?", parent=self.root):
            return
        snap = self.project.snapshot()  # changes during rendering apply to the next job
        th = timeline_hash(tl)

        def done(res):
            self.project.last_render = {"frames": int(round(res.duration * tl.fps)), "fps": tl.fps,
                                        "filename": res.video.name, "timeline_hash": th,
                                        "profile": snap.output.profile}
            self.project.acknowledged_warnings = [d.message for d in res.diagnostics if d.severity == "warning"]
            self.mark_dirty()
            v = res.verification
            messagebox.showinfo("Render complete",
                                f"Verified {res.video.name}\n{v.get('width')}×{v.get('height')} {v.get('video_codec')} "
                                f"@ {v.get('frame_rate')}, {v.get('frames')} frames ({res.duration:.2f} s), audio "
                                f"{v.get('audio_codec')} {v.get('audio_sample_rate')} Hz.\n\nFiles:\n" +
                                "\n".join(p.name for p in res.files), parent=self.root)

        self.start_job("render", "Rendering", lambda cancel, progress: export_package(
            snap, cancel, progress, self.config.custom_profiles), done)

    def test_profile(self):
        from ..profiles import get_profile, validate_profile_static
        from ..render import test_encoder

        prof = get_profile(self.project.output.profile, self.project.output.overrides)
        errs = validate_profile_static(prof)
        if errs:
            messagebox.showerror("Profile", "\n".join(errs), parent=self.root)
            return
        self.start_job("enctest", "Testing encoder", lambda c, p: test_encoder(prof),
                       lambda err: messagebox.showinfo("Profile", "FFmpeg accepted these settings." if not err else
                                                       f"FFmpeg rejected these settings:\n{err}", parent=self.root))

    def export_companion(self, which: str):
        from ..export import export_companions_only

        d = self.project.export_directory or self.config.paths.exports
        if not d:
            d = filedialog.askdirectory(parent=self.root, title="Export folder")
            if not d:
                return
            self.project.export_directory = d
            self.mark_dirty()
        try:
            path = export_companions_only(self.project, which, Path(d))
        except (OSError, KeyError, ValueError) as exc:
            messagebox.showerror("Export", str(exc), parent=self.root)
            return
        note = ""
        if which == "rundown" and not (self.project.last_render.get("filename")):
            note = "\nDuration is labelled estimated/measured because the package has not been rendered."
        messagebox.showinfo("Export", f"Wrote {path.name}{note}", parent=self.root)

    def open_export_folder(self):
        d = self.project.export_directory or self.config.paths.exports
        if not d or not os.path.isdir(d):
            messagebox.showinfo("Export folder", "No export folder exists yet.", parent=self.root)
            return
        try:
            open_in_file_manager(d)
        except OSError as exc:
            messagebox.showerror("Export folder", str(exc), parent=self.root)


def main(argv: Optional[list[str]] = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    root = tk.Tk()
    App(root, argv[0] if argv else None)
    root.mainloop()


if __name__ == "__main__":
    main()
