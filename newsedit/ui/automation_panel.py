"""Automation & settings: narration (manual/TTS), visual, subtitles, audio, export profile."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable

from ..model import RESIZE_LABELS
from ..narration import GTTS_NOTE, ONLINE_NOTE
from ..profiles import DEFAULT_PROFILES
from ..subtitles import WHISPER_MODELS
from .widgets import FG_DIM, GREEN, RED, YELLOW, ScrollableFrame

if TYPE_CHECKING:
    from .app import App


def _get_path(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set_path(obj, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


class Binder:
    """Two-way binding between Tk variables and dotted project attributes."""

    def __init__(self, app: "App"):
        self.app = app
        self.items: list[tuple[tk.Variable, str, Callable[[Any], Any], Callable[[Any], Any]]] = []
        self._loading = False

    def bind(self, var: tk.Variable, path: str, cast: Callable[[Any], Any] = lambda x: x,
             show: Callable[[Any], Any] = lambda x: x) -> tk.Variable:
        self.items.append((var, path, cast, show))
        var.trace_add("write", lambda *_: self._commit(var, path, cast))
        return var

    def _commit(self, var, path, cast):
        if self._loading:
            return
        try:
            value = cast(var.get())
        except (ValueError, tk.TclError):
            return
        if _get_path(self.app.project, path) != value:
            _set_path(self.app.project, path, value)
            self.app.mark_dirty()
            self.app.schedule_refresh()

    def load(self):
        self._loading = True
        try:
            for var, path, _cast, show in self.items:
                try:
                    var.set(show(_get_path(self.app.project, path)))
                except tk.TclError:
                    pass
        finally:
            self._loading = False


def clamp(lo, hi, cast=float):
    def f(v):
        x = cast(v)
        if not (lo <= x <= hi):
            raise ValueError
        return x
    return f


class AutomationPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.binder = Binder(app)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.narr = NarrationTab(self.nb, app, self.binder)
        self.nb.add(self.narr, text="Narration")
        self.nb.add(self._visual_tab(), text="Visual")
        self.nb.add(self._subs_tab(), text="Subtitles")
        self.nb.add(self._audio_tab(), text="Audio")
        self.export_tab = ExportTab(self.nb, app, self.binder)
        self.nb.add(self.export_tab, text="Export")

    def load(self):
        self.binder.load()
        self.narr.load()
        self.export_tab.load()

    def refresh(self):
        self.narr.refresh_blocks()

    # ------------------------------------------------------------------
    def _form(self):
        sf = ScrollableFrame(self.nb)
        sf.body.columnconfigure(1, weight=1, minsize=120)
        return sf, sf.body

    @staticmethod
    def _row(f, r, label, widget, hint=""):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=2)
        widget.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        if hint:
            ttk.Label(f, text=hint, style="Dim.TLabel", wraplength=150).grid(row=r, column=2, sticky="w")
        return r + 1

    def _visual_tab(self):
        sf, f = self._form()
        B = self.binder
        r = 0
        ttk.Label(f, text="Smart resize (global; per-marker overrides in Markers)", style="Head.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 2))
        r += 1
        v = B.bind(tk.StringVar(), "visual.resize_mode")
        for key in ("blur", "crop", "stretch"):
            ttk.Radiobutton(f, text=RESIZE_LABELS[key], value=key, variable=v).grid(row=r, column=0, columnspan=2,
                                                                                    sticky="w", padx=16)
            r += 1
        ttk.Label(f, text="Stretch distorts footage; it is never the default.", style="Dim.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", padx=16)
        r += 1
        ttk.Label(f, text="Transitions", style="Head.TLabel").grid(row=r, column=0, sticky="w", padx=6, pady=(8, 2))
        r += 1
        tv = B.bind(tk.StringVar(), "visual.transition")
        tfr = ttk.Frame(f)
        ttk.Radiobutton(tfr, text="Cut", value="cut", variable=tv).pack(side="left")
        ttk.Radiobutton(tfr, text="Fade (through black)", value="fade", variable=tv).pack(side="left", padx=8)
        r = self._row(f, r, "Style", tfr, "Fades are visual only and never move speech.")
        r = self._row(f, r, "Fade length (frames)", ttk.Spinbox(f, from_=2, to=25, width=5,
                      textvariable=B.bind(tk.StringVar(), "visual.fade_frames", clamp(2, 25, int))))
        r = self._row(f, r, "B-roll shot preference (s)", ttk.Spinbox(f, from_=1, to=20, increment=0.5, width=5,
                      textvariable=B.bind(tk.StringVar(), "visual.broll_shot_pref_seconds", clamp(0, 60))),
                      "Editorial hint only; maps/cards may stay longer.")
        r = self._row(f, r, "Safe-area inset", ttk.Spinbox(f, from_=0.0, to=0.15, increment=0.01, width=5,
                      textvariable=B.bind(tk.StringVar(), "visual.safe_inset", clamp(0, 0.2))), "fraction of frame")
        r = self._row(f, r, "Lower-third colour", ttk.Entry(f, width=10,
                      textvariable=B.bind(tk.StringVar(), "visual.lower_third_color")))
        return sf

    def _subs_tab(self):
        sf, f = self._form()
        B = self.binder
        r = 0
        r = self._row(f, r, "", ttk.Checkbutton(f, text="Burn subtitles into the picture",
                                                variable=B.bind(tk.BooleanVar(), "subtitles.burn_in")))
        r = self._row(f, r, "", ttk.Checkbutton(f, text="Also caption narration (optional extension)",
                                                variable=B.bind(tk.BooleanVar(), "subtitles.narration_captions")))
        r = self._row(f, r, "Max characters/line", ttk.Spinbox(f, from_=37, to=42, width=5, textvariable=B.bind(
            tk.StringVar(), "subtitles.max_chars", clamp(37, 42, int))), "37–42, max two lines")
        r = self._row(f, r, "Min duration (s)", ttk.Spinbox(f, from_=0.5, to=3, increment=0.1, width=5,
                      textvariable=B.bind(tk.StringVar(), "subtitles.min_duration", clamp(0.3, 3))))
        r = self._row(f, r, "Max duration (s)", ttk.Spinbox(f, from_=2, to=7, increment=0.5, width=5,
                      textvariable=B.bind(tk.StringVar(), "subtitles.max_duration", clamp(2, 7))), "never above 7")
        r = self._row(f, r, "Reading speed flag (cps)", ttk.Spinbox(f, from_=10, to=30, width=5, textvariable=B.bind(
            tk.StringVar(), "subtitles.max_cps", clamp(5, 40))), "heuristic, not a broadcast rule")
        sv = B.bind(tk.StringVar(), "subtitles.style")
        sfr = ttk.Frame(f)
        ttk.Radiobutton(sfr, text="Black box", value="box", variable=sv).pack(side="left")
        ttk.Radiobutton(sfr, text="Black outline", value="outline", variable=sv).pack(side="left", padx=8)
        r = self._row(f, r, "Style", sfr)
        r = self._row(f, r, "Box opacity", ttk.Spinbox(f, from_=0.5, to=0.6, increment=0.05, width=5,
                      textvariable=B.bind(tk.StringVar(), "subtitles.box_opacity", clamp(0.3, 0.9))), "50–60 %")
        r = self._row(f, r, "Outline px @1080", ttk.Spinbox(f, from_=1, to=4, increment=0.5, width=5,
                      textvariable=B.bind(tk.StringVar(), "subtitles.outline_px_1080", clamp(0.5, 6))))
        r = self._row(f, r, "Font size @1080", ttk.Spinbox(f, from_=30, to=64, width=5, textvariable=B.bind(
            tk.StringVar(), "subtitles.font_size_1080", clamp(20, 80, int))))
        ff = ttk.Frame(f)
        fv = B.bind(tk.StringVar(), "subtitles.font_path", lambda s: s or None, lambda v: v or "")
        ttk.Entry(ff, textvariable=fv, width=24).pack(side="left")
        ttk.Button(ff, text="…", width=3, command=lambda: fv.set(
            filedialog.askopenfilename(parent=self, filetypes=[("Fonts", "*.ttf *.otf *.ttc")]) or fv.get())).pack(
            side="left")
        r = self._row(f, r, "Font file", ff, "blank = Arial or Greek-capable substitute")
        ttk.Label(f, text="Local transcription", style="Head.TLabel").grid(row=r, column=0, sticky="w", padx=6,
                                                                            pady=(8, 2))
        r += 1
        r = self._row(f, r, "Whisper model", ttk.Combobox(f, values=WHISPER_MODELS, width=10, state="readonly",
                      textvariable=B.bind(tk.StringVar(), "subtitles.whisper_model")),
                      "larger = slower, more accurate")
        r = self._row(f, r, "Device", ttk.Combobox(f, values=("cpu", "cuda"), width=6, state="readonly",
                      textvariable=B.bind(tk.StringVar(), "subtitles.whisper_device")), "CUDA is never assumed")
        bf = ttk.Frame(f)
        ttk.Button(bf, text="Transcribe all statements", command=lambda: self.app.transcribe(None)).pack(side="left")
        ttk.Button(bf, text="Edit cues…", command=lambda: self.app.open_cue_editor(None)).pack(side="left", padx=4)
        r = self._row(f, r, "", bf)
        r = self._row(f, r, "", ttk.Checkbutton(f, text="Write SRT sidecar",
                                                variable=B.bind(tk.BooleanVar(), "subtitles.write_srt")))
        r = self._row(f, r, "", ttk.Checkbutton(f, text="Write ASS sidecar",
                                                variable=B.bind(tk.BooleanVar(), "subtitles.write_ass")))
        return sf

    def _audio_tab(self):
        sf, f = self._form()
        B = self.binder
        r = 0
        ttk.Label(f, text="Music under speech", style="Head.TLabel").grid(row=r, column=0, sticky="w", padx=6,
                                                                          pady=(8, 2))
        r += 1
        pv = B.bind(tk.StringVar(), "music.soundbite_policy")
        pf = ttk.Frame(f)
        ttk.Radiobutton(pf, text="Duck under all speech", value="duck", variable=pv).pack(anchor="w")
        ttk.Radiobutton(pf, text="Mute during soundbites (fade out before)", value="mute", variable=pv).pack(anchor="w")
        r = self._row(f, r, "Policy", pf)
        r = self._row(f, r, "Ducking (dB)", ttk.Spinbox(f, from_=6, to=30, width=5, textvariable=B.bind(
            tk.StringVar(), "music.duck_db", clamp(0, 40))), "default 20 dB below base")
        r = self._row(f, r, "Attack (ms)", ttk.Spinbox(f, from_=10, to=1000, increment=10, width=6,
                      textvariable=B.bind(tk.StringVar(), "music.attack_ms", clamp(0, 5000))))
        r = self._row(f, r, "Release (ms)", ttk.Spinbox(f, from_=10, to=3000, increment=10, width=6,
                      textvariable=B.bind(tk.StringVar(), "music.release_ms", clamp(0, 5000))))
        r = self._row(f, r, "Mute pre-roll (s)", ttk.Spinbox(f, from_=0, to=3, increment=0.1, width=5,
                      textvariable=B.bind(tk.StringVar(), "music.mute_preroll", clamp(0, 5))))
        r = self._row(f, r, "Music fade in (s)", ttk.Spinbox(f, from_=0, to=5, increment=0.1, width=5,
                      textvariable=B.bind(tk.StringVar(), "music.fade_in", clamp(0, 10))))
        r = self._row(f, r, "Music fade out (s)", ttk.Spinbox(f, from_=0, to=10, increment=0.5, width=5,
                      textvariable=B.bind(tk.StringVar(), "music.fade_out", clamp(0, 20))))
        ttk.Label(f, text="Levels", style="Head.TLabel").grid(row=r, column=0, sticky="w", padx=6, pady=(8, 2))
        r += 1
        r = self._row(f, r, "Narration gain (dB)", ttk.Spinbox(f, from_=-12, to=12, width=5, textvariable=B.bind(
            tk.StringVar(), "audio.narration_gain_db", clamp(-30, 20))))
        r = self._row(f, r, "Peak target (dBFS)", ttk.Spinbox(f, from_=-6, to=0, increment=0.5, width=5,
                      textvariable=B.bind(tk.StringVar(), "audio.peak_target_db", clamp(-20, 0))))
        r = self._row(f, r, "", ttk.Checkbutton(f, text="Reduce overall gain automatically if peaks exceed target",
                                                variable=B.bind(tk.BooleanVar(), "audio.auto_fix_peaks")))
        lv = B.bind(tk.StringVar(), "audio.loudness_target_lufs",
                    lambda s: None if str(s).strip() == "" else clamp(-40, -5)(str(s).replace(",", ".")),
                    lambda v: "" if v is None else f"{v:g}")
        r = self._row(f, r, "Loudness target (LUFS)", ttk.Entry(f, textvariable=lv, width=6),
                      "blank = off. Enter your station's value; none is assumed.")
        return sf


class NarrationTab(ScrollableFrame):
    def __init__(self, master, app: "App", binder: Binder):
        super().__init__(master)
        self.app = app
        B = binder
        f = self.body
        f.columnconfigure(0, weight=1)
        mv = B.bind(tk.StringVar(), "narration.mode")
        mf = ttk.Frame(f)
        mf.grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(mf, text="Narration mode:", style="Head.TLabel").pack(side="left")
        ttk.Radiobutton(mf, text="Manual Recording", value="manual", variable=mv,
                        command=self.update_mode).pack(side="left", padx=6)
        ttk.Radiobutton(mf, text="AI Text-to-Speech", value="tts", variable=mv,
                        command=self.update_mode).pack(side="left")
        self.mode_var = mv
        cols = ("words", "est", "audio", "dur")
        self.tree = ttk.Treeview(f, columns=cols, show="tree headings", height=6, selectmode="extended")
        self.tree.heading("#0", text="Block")
        self.tree.column("#0", width=200)
        for c, t, w in zip(cols, ("Words", "Est. s", "Audio", "Dur. s"), (50, 55, 90, 60)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.grid(row=1, column=0, sticky="ew", padx=6)
        self.tree.tag_configure("stale", foreground=RED)
        self.tree.tag_configure("ok", foreground=GREEN)
        self.tree.bind("<Double-1>", self._goto)
        cf = ttk.Frame(f)
        cf.grid(row=2, column=0, sticky="w", padx=6, pady=2)
        ttk.Button(cf, text="▶ Play", command=self.play).pack(side="left")
        ttk.Button(cf, text="Discard", command=self.discard).pack(side="left", padx=2)
        ttk.Button(cf, text="Trim silence", command=lambda: self.trim(True)).pack(side="left", padx=2)
        ttk.Button(cf, text="Undo trim", command=lambda: self.trim(False)).pack(side="left", padx=2)

        # manual
        self.manual = ttk.LabelFrame(f, text="Manual recording")
        self.manual.grid(row=3, column=0, sticky="ew", padx=6, pady=4)
        self.manual.columnconfigure(1, weight=1)
        ttk.Label(self.manual, text="Microphone").grid(row=0, column=0, sticky="w", padx=4)
        self.dev_var = tk.StringVar()
        self.dev_combo = ttk.Combobox(self.manual, textvariable=self.dev_var, state="readonly")
        self.dev_combo.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(self.manual, text="↻", width=3, command=self.load_devices).grid(row=0, column=2)
        self.level = ttk.Progressbar(self.manual, style="Level.Horizontal.TProgressbar", maximum=1.0)
        self.level.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=2)
        self.rec_lbl = ttk.Label(self.manual, text="Idle")
        self.rec_lbl.grid(row=2, column=0, columnspan=3, sticky="w", padx=4)
        bf = ttk.Frame(self.manual)
        bf.grid(row=3, column=0, columnspan=3, sticky="w", padx=4, pady=2)
        self.rec_btn = ttk.Button(bf, text="● Record block", style="Danger.TButton", command=self.app.record_start)
        self.rec_btn.pack(side="left")
        self.stop_btn = ttk.Button(bf, text="■ Stop", command=self.app.record_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=2)
        ttk.Button(bf, text="Import file for block…", command=self.import_block).pack(side="left", padx=2)
        bf2 = ttk.Frame(self.manual)
        bf2.grid(row=4, column=0, columnspan=3, sticky="w", padx=4, pady=2)
        ttk.Button(bf2, text="Import continuous take…", command=self.import_take).pack(side="left")
        ttk.Button(bf2, text="Map take ranges…", command=self.app.open_take_mapper).pack(side="left", padx=2)
        ttk.Label(self.manual, text="A continuous take is never split automatically: assign explicit in/out ranges.",
                  style="Dim.TLabel", wraplength=380).grid(row=5, column=0, columnspan=3, sticky="w", padx=4)

        # tts
        self.tts = ttk.LabelFrame(f, text="Text-to-speech")
        self.tts.grid(row=4, column=0, sticky="ew", padx=6, pady=4)
        self.tts.columnconfigure(1, weight=1)
        r = 0
        pv = B.bind(tk.StringVar(), "narration.provider")
        ttk.Label(self.tts, text="Provider").grid(row=r, column=0, sticky="w", padx=4)
        pf = ttk.Frame(self.tts)
        pf.grid(row=r, column=1, sticky="w")
        ttk.Radiobutton(pf, text="gTTS", value="gtts", variable=pv, command=self.update_mode).pack(side="left")
        ttk.Radiobutton(pf, text="ElevenLabs", value="elevenlabs", variable=pv, command=self.update_mode).pack(
            side="left", padx=6)
        self.provider_var = pv
        r += 1
        ttk.Label(self.tts, text=ONLINE_NOTE, foreground=YELLOW, wraplength=380).grid(row=r, column=0, columnspan=3,
                                                                                     sticky="w", padx=4)
        r += 1
        ttk.Label(self.tts, text="Language").grid(row=r, column=0, sticky="w", padx=4)
        ttk.Combobox(self.tts, values=("el", "en"), width=6, textvariable=B.bind(tk.StringVar(), "narration.language")
                     ).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        self.g_frame = ttk.Frame(self.tts)
        self.g_frame.grid(row=r, column=0, columnspan=3, sticky="ew")
        ttk.Label(self.g_frame, text=GTTS_NOTE, style="Dim.TLabel", wraplength=380).grid(row=0, column=0, columnspan=3,
                                                                                         sticky="w", padx=4)
        ttk.Label(self.g_frame, text="Accent domain").grid(row=1, column=0, sticky="w", padx=4)
        ttk.Combobox(self.g_frame, values=("com", "gr"), width=6,
                     textvariable=B.bind(tk.StringVar(), "narration.gtts_tld")).grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(self.g_frame, text="Slow", variable=B.bind(tk.BooleanVar(), "narration.gtts_slow")).grid(
            row=1, column=2, sticky="w")
        self.e_frame = ttk.Frame(self.tts)
        self.e_frame.grid(row=r + 1, column=0, columnspan=3, sticky="ew")
        self.e_frame.columnconfigure(1, weight=1)
        ttk.Label(self.e_frame, text="API key").grid(row=0, column=0, sticky="w", padx=4)
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(self.e_frame, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.key_var.trace_add("write", lambda *_: self.app.set_api_key(self.key_var.get()))
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.e_frame, text="Show", variable=self.show_key,
                        command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "•")).grid(
            row=0, column=2)
        self.remember = tk.BooleanVar(value=False)
        self.remember_chk = ttk.Checkbutton(self.e_frame, text="Remember in OS keychain", variable=self.remember,
                                            command=lambda: self.app.remember_api_key(self.remember.get()))
        self.remember_chk.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(self.e_frame, text="Keys stay in memory (or the OS keychain); they are never written to project "
                                     "files, exports or logs.", style="Dim.TLabel", wraplength=380).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=4)
        ttk.Label(self.e_frame, text="Voice").grid(row=3, column=0, sticky="w", padx=4)
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(self.e_frame, textvariable=self.voice_var, state="readonly")
        self.voice_combo.grid(row=3, column=1, sticky="ew", padx=4)
        self.voice_combo.bind("<<ComboboxSelected>>", self._voice_selected)
        ttk.Button(self.e_frame, text="Load voices", command=self.app.load_voices).grid(row=3, column=2)
        ttk.Label(self.e_frame, text="Model").grid(row=4, column=0, sticky="w", padx=4)
        ttk.Combobox(self.e_frame, values=("eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5"),
                     textvariable=B.bind(tk.StringVar(), "narration.model_id")).grid(row=4, column=1, sticky="ew",
                                                                                     padx=4)
        ttk.Label(self.e_frame, text="Stability / similarity").grid(row=5, column=0, sticky="w", padx=4)
        sf = ttk.Frame(self.e_frame)
        sf.grid(row=5, column=1, sticky="w")
        ttk.Spinbox(sf, from_=0, to=1, increment=0.05, width=5,
                    textvariable=B.bind(tk.StringVar(), "narration.stability", clamp(0, 1))).pack(side="left")
        ttk.Spinbox(sf, from_=0, to=1, increment=0.05, width=5,
                    textvariable=B.bind(tk.StringVar(), "narration.similarity_boost", clamp(0, 1))).pack(side="left",
                                                                                                       padx=4)
        gf = ttk.Frame(self.tts)
        gf.grid(row=r + 2, column=0, columnspan=3, sticky="w", padx=4, pady=4)
        ttk.Button(gf, text="Generate selected", command=lambda: self.app.generate_tts(self.selected_blocks())).pack(
            side="left")
        ttk.Button(gf, text="Generate missing/stale", style="Accent.TButton",
                   command=lambda: self.app.generate_tts(None)).pack(side="left", padx=4)
        self.voices: dict[str, str] = {}

    def load(self):
        self.load_devices()
        n = self.app.project.narration
        if n.voice_id:
            label = f"{n.voice_name or n.voice_id}"
            self.voices = {label: n.voice_id}
            self.voice_combo.configure(values=list(self.voices))
            self.voice_var.set(label)
        else:
            self.voice_combo.configure(values=[])
            self.voice_var.set("")
        try:
            import keyring  # noqa: F401
            self.remember_chk.state(["!disabled"])
        except Exception:
            self.remember_chk.state(["disabled"])
        self.update_mode()
        self.refresh_blocks()

    def set_voices(self, voices):
        self.voices = {f"{name} ({vid[:6]})": vid for vid, name in voices}
        self.voice_combo.configure(values=list(self.voices))

    def _voice_selected(self, _e=None):
        label = self.voice_var.get()
        vid = self.voices.get(label, "")
        n = self.app.project.narration
        n.voice_id, n.voice_name = vid, label
        self.app.mark_dirty()

    def load_devices(self):
        from ..narration import list_input_devices

        devs = list_input_devices()
        self.devices = {f"{i}: {name}": i for i, name in devs}
        self.dev_combo.configure(values=list(self.devices) or ["(no input devices found)"])
        if self.devices:
            cur = self.app.project.narration.input_device
            self.dev_var.set(cur if cur in self.devices else next(iter(self.devices)))
        else:
            self.dev_var.set("(no input devices found)")

    def device_index(self):
        return self.devices.get(self.dev_var.get()) if getattr(self, "devices", None) else None

    def update_mode(self):
        manual = self.mode_var.get() == "manual"
        if manual:
            self.manual.grid()
            self.tts.grid_remove()
        else:
            self.manual.grid_remove()
            self.tts.grid()
        if self.provider_var.get() == "elevenlabs":
            self.g_frame.grid_remove()
            self.e_frame.grid()
        else:
            self.e_frame.grid_remove()
            self.g_frame.grid()

    def refresh_blocks(self):
        tl = self.app.timeline
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        if tl is None:
            return
        for s in tl.segments:
            if s.kind != "narration":
                continue
            na = s.narration
            if na is None:
                status, tag = "none", ()
            elif s.stale:
                status, tag = "STALE", ("stale",)
            else:
                status, tag = na.source, ("ok",)
            dur = ""
            if na is not None and not s.stale:
                dur = f"{s.seconds:.2f}"
                if na.trim_head or na.trim_tail:
                    dur += " ✂"
            preview = s.text.replace("\n", " ")
            self.tree.insert("", "end", iid=str(s.block_index), text=f"{s.block_index + 1}. {preview[:40]}",
                             values=(s.words, f"{s.words / self.app.project.wpm * 60:.1f}", status, dur), tags=tag)
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)

    def selected_blocks(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def _goto(self, _e=None):
        sel = self.selected_blocks()
        tl = self.app.timeline
        if sel and tl:
            seg = next((s for s in tl.segments if s.kind == "narration" and s.block_index == sel[0]), None)
            if seg:
                self.app.goto_script(seg.char_start, seg.char_end)

    def _seg(self, idx):
        tl = self.app.timeline
        return next((s for s in tl.segments if s.kind == "narration" and s.block_index == idx), None) if tl else None

    def play(self):
        sel = self.selected_blocks()
        if not sel:
            return
        seg = self._seg(sel[0])
        if seg is None or seg.narration is None:
            messagebox.showinfo("Narration", "This block has no audio yet.", parent=self)
            return
        self.app.play_narration(seg.narration)

    def discard(self):
        sel = self.selected_blocks()
        if not sel or not messagebox.askyesno("Discard", f"Discard audio for {len(sel)} block(s)?", parent=self):
            return
        from ..narration import remove_block_audio

        for i in sel:
            remove_block_audio(self.app.project, i)
        self.app.mark_dirty()
        self.app.schedule_refresh()

    def trim(self, on: bool):
        from ..narration import auto_trim

        for i in self.selected_blocks() or [int(x) for x in self.tree.get_children()]:
            seg = self._seg(i)
            if seg is None or seg.narration is None:
                continue
            if on:
                try:
                    auto_trim(self.app.project, seg.narration)
                except Exception as exc:
                    messagebox.showerror("Trim", str(exc), parent=self)
                    return
            else:
                seg.narration.trim_head = seg.narration.trim_tail = 0.0
        self.app.mark_dirty()
        self.app.schedule_refresh()

    def import_block(self):
        sel = self.selected_blocks()
        if len(sel) != 1:
            messagebox.showinfo("Import", "Select exactly one narration block.", parent=self)
            return
        f = filedialog.askopenfilename(parent=self, title="Narration recording",
                                       filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac"), ("All", "*")])
        if f:
            self.app.import_narration_file(sel[0], f)

    def import_take(self):
        f = filedialog.askopenfilename(parent=self, title="Continuous narration take",
                                       filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac"), ("All", "*")])
        if f:
            self.app.import_take(f)


class ExportTab(ScrollableFrame):
    FIELDS = {
        "prores_ks": [("vprofile", "ProRes profile (0–5, 2 = 422)", str)],
        "dnxhd": [("vbitrate_mbps", "DNxHD bitrate (Mb/s: 36/120/185)", int), ("pix_fmt", "Pixel format", str)],
        "libx264": [("crf", "H.264 CRF", int), ("preset", "x264 preset", str)],
    }

    def __init__(self, master, app: "App", binder: Binder):
        super().__init__(master)
        self.app = app
        f = self.body
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Export profile").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.labels = {v["label"]: k for k, v in DEFAULT_PROFILES.items()}
        self.prof_var = tk.StringVar()
        cb = ttk.Combobox(f, values=list(self.labels), textvariable=self.prof_var, state="readonly", width=28)
        cb.grid(row=0, column=1, sticky="w", padx=4)
        cb.bind("<<ComboboxSelected>>", self._profile_selected)
        self.notes = ttk.Label(f, text="", style="Dim.TLabel", wraplength=380, justify="left")
        self.notes.grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
        self.ov_frame = ttk.LabelFrame(f, text="Overrides (this project)")
        self.ov_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        self.status = ttk.Label(f, text="", wraplength=380)
        self.status.grid(row=3, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Button(f, text="Test encoder with these settings", command=self.app.test_profile).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        ttk.Label(f, text="Companion outputs", style="Head.TLabel").grid(row=5, column=0, sticky="w", padx=6,
                                                                          pady=(8, 2))
        ttk.Checkbutton(f, text="voice_over.wav (narration only, timeline aligned)",
                        variable=binder.bind(tk.BooleanVar(), "export.voice_over_wav")).grid(row=6, column=0,
                                                                                          columnspan=2, sticky="w",
                                                                                          padx=6)
        ttk.Checkbutton(f, text="Per-segment narration WAVs", variable=binder.bind(
            tk.BooleanVar(), "export.segment_wavs")).grid(row=7, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Segment-level rundown CSV", variable=binder.bind(
            tk.BooleanVar(), "export.segment_rundown")).grid(row=8, column=0, columnspan=2, sticky="w", padx=6)
        self.ov_vars: dict[str, tk.StringVar] = {}

    def load(self):
        p = self.app.project
        prof = DEFAULT_PROFILES.get(p.output.profile, DEFAULT_PROFILES["web_horizontal"])
        self.prof_var.set(prof["label"])
        self._build_overrides()

    def _profile_selected(self, _e=None):
        key = self.labels[self.prof_var.get()]
        p = self.app.project
        prof = DEFAULT_PROFILES[key]
        p.output.profile = key
        p.output.width, p.output.height, p.output.fps = prof["width"], prof["height"], prof["fps"]
        p.output.overrides = {}
        self.app.mark_dirty()
        self._build_overrides()
        self.app.schedule_refresh()

    def _build_overrides(self):
        for w in self.ov_frame.winfo_children():
            w.destroy()
        p = self.app.project
        from ..profiles import get_profile, validate_profile_static

        prof = get_profile(p.output.profile, p.output.overrides)
        self.notes.configure(text=f"{prof['notes']}\n{prof['width']}×{prof['height']} @ {prof['fps']} fps "
                                  f"{prof['scan']}. Not guaranteed to match any specific control room's spec.")
        self.ov_vars = {}
        fields = self.FIELDS.get(prof["vcodec"], []) + [("scan", "Scan (progressive / interlaced_tff)", str)]
        for i, (key, label, cast) in enumerate(fields):
            ttk.Label(self.ov_frame, text=label).grid(row=i, column=0, sticky="w", padx=4)
            v = tk.StringVar(value=str(prof.get(key, "")))
            ttk.Entry(self.ov_frame, textvariable=v, width=14).grid(row=i, column=1, sticky="w", padx=4, pady=1)
            v.trace_add("write", lambda *_, k=key, c=cast, var=v: self._set_override(k, c, var))
            self.ov_vars[key] = v
        errs = validate_profile_static(prof)
        self.status.configure(text=("\n".join(errs) if errs else "Profile settings are consistent."),
                              foreground=RED if errs else GREEN)

    def _set_override(self, key, cast, var):
        p = self.app.project
        try:
            val = cast(var.get())
        except ValueError:
            return
        base = DEFAULT_PROFILES[p.output.profile].get(key)
        if val == base:
            p.output.overrides.pop(key, None)
        else:
            p.output.overrides[key] = val
        from ..profiles import get_profile, validate_profile_static

        errs = validate_profile_static(get_profile(p.output.profile, p.output.overrides))
        self.status.configure(text=("\n".join(errs) if errs else "Profile settings are consistent."),
                              foreground=RED if errs else GREEN)
        self.app.mark_dirty()
        self.app.schedule_refresh()
