"""Dialogs: library paths, AI prompt templates, help, cue editor, take mapper."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Optional

from .. import APP_NAME, __version__
from ..model import Cue, SubtitleTrack
from ..subtitles import apply_min_duration, format_segments, parse_srt, validate_track, wrap_cue_text
from ..timeline import binding_trim
from ..util import parse_time
from .widgets import BG2, FG, RED, SCRIPT_BG, YELLOW

if TYPE_CHECKING:
    from .app import App


class PathsDialog(tk.Toplevel):
    NAMES = [("sfx", "Sound effects (SFX)"), ("music", "Background music"), ("broll", "Stock / B-roll"),
             ("images", "Image assets"), ("soundbites", "Soundbites (optional)"), ("exports", "Default exports")]

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title("Settings — Library paths")
        self.vars = {}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        for i, (key, label) in enumerate(self.NAMES):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=getattr(app.config.paths, key))
            self.vars[key] = v
            ttk.Entry(f, textvariable=v, width=55).grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Button(f, text="Browse…", command=lambda v=v: self._browse(v)).grid(row=i, column=2)
        n = len(self.NAMES)
        ttk.Label(f, text="Default WPM").grid(row=n, column=0, sticky="w", pady=3)
        self.wpm = tk.StringVar(value=f"{app.config.default_wpm:g}")
        ttk.Spinbox(f, from_=120, to=160, increment=5, textvariable=self.wpm, width=6).grid(row=n, column=1,
                                                                                           sticky="w", padx=4)
        ttk.Label(f, text="Autosave every (s)").grid(row=n + 1, column=0, sticky="w", pady=3)
        self.autosave = tk.StringVar(value=str(app.config.autosave_seconds))
        ttk.Spinbox(f, from_=15, to=600, increment=15, textvariable=self.autosave, width=6).grid(
            row=n + 1, column=1, sticky="w", padx=4)
        ttk.Label(f, text="External player (optional)").grid(row=n + 2, column=0, sticky="w", pady=3)
        self.player = tk.StringVar(value=app.config.external_player)
        ttk.Entry(f, textvariable=self.player, width=55).grid(row=n + 2, column=1, sticky="ew", padx=4)
        self.msg = ttk.Label(f, text="", foreground=RED, wraplength=520)
        self.msg.grid(row=n + 3, column=0, columnspan=3, sticky="w")
        bf = ttk.Frame(f)
        bf.grid(row=n + 4, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        self.transient(app.root)

    def _browse(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)

    def save(self):
        problems = []
        for key, v in self.vars.items():
            val = v.get().strip()
            if val and not Path(val).is_dir():
                if key == "exports":
                    try:
                        Path(val).mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        problems.append(f"Cannot create export folder {val}: {exc.strerror}")
                        continue
                else:
                    problems.append(f"Folder does not exist: {val}")
                    continue
            setattr(self.app.config.paths, key, val)
        try:
            wpm = float(self.wpm.get())
            if not 120 <= wpm <= 160:
                raise ValueError
            self.app.config.default_wpm = wpm
            self.app.config.autosave_seconds = max(15, int(self.autosave.get()))
        except ValueError:
            problems.append("Default WPM must be 120–160 and autosave a whole number of seconds.")
        self.app.config.external_player = self.player.get().strip()
        if problems:
            self.msg.configure(text="\n".join(problems))
            return
        try:
            self.app.config.save()
        except OSError as exc:
            messagebox.showerror("Settings", f"Could not save settings: {exc}", parent=self)
            return
        self.destroy()


class PromptsDialog(tk.Toplevel):
    """Editable editorial text templates. No external API calls are made."""

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title("AI Prompts — editorial templates")
        self.geometry("760x460")
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        self.lb = tk.Listbox(left, width=28)
        self.lb.pack(fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", lambda e: self.show())
        lbf = ttk.Frame(left)
        lbf.pack(fill="x")
        ttk.Button(lbf, text="New", command=self.new).pack(side="left")
        ttk.Button(lbf, text="Delete", command=self.delete).pack(side="left", padx=2)
        pw.add(left, weight=1)
        right = ttk.Frame(pw)
        self.name = tk.StringVar()
        ttk.Entry(right, textvariable=self.name).pack(fill="x", padx=4, pady=4)
        self.text = tk.Text(right, wrap="word", bg=SCRIPT_BG, fg=FG, insertbackground=FG)
        self.text.pack(fill="both", expand=True, padx=4)
        bf = ttk.Frame(right)
        bf.pack(fill="x", pady=4)
        ttk.Label(bf, text="Templates are stored locally and copied to the clipboard; nothing is sent anywhere.",
                  style="Dim.TLabel").pack(side="left", padx=4)
        ttk.Button(bf, text="Copy", command=self.copy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        pw.add(right, weight=3)
        self.cur: Optional[int] = None
        self.reload()
        self.transient(app.root)

    def reload(self):
        self.lb.delete(0, "end")
        for p in self.app.config.prompts:
            self.lb.insert("end", p.get("name", "?"))

    def show(self):
        sel = self.lb.curselection()
        if not sel:
            return
        self.cur = sel[0]
        p = self.app.config.prompts[self.cur]
        self.name.set(p.get("name", ""))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", p.get("text", ""))

    def new(self):
        self.app.config.prompts.append({"name": "Νέο πρότυπο", "text": ""})
        self.reload()
        self.lb.selection_set("end")
        self.show()

    def delete(self):
        if self.cur is not None:
            del self.app.config.prompts[self.cur]
            self.cur = None
            self.app.config.save()
            self.reload()

    def save(self):
        if self.cur is None:
            return
        self.app.config.prompts[self.cur] = {"name": self.name.get(), "text": self.text.get("1.0", "end-1c")}
        self.app.config.save()
        self.reload()

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))


HELP_TEXT = f"""{APP_NAME} {__version__}

SCRIPT MARKERS
  [BROLL_n]      Visual for the following narration block (adds no time by itself).
  [IMAGE_n]      Still image for the following narration block.
  [CARD_n]       Manually supplied title/data card. Covers following narration, or stands alone for its seconds.
  [SOUNDBITE_n]  Inserts the trimmed statement with its original audio. Narration stops before it and resumes after.
  [STANDUP_n]    On-camera/stand-up clip with its original audio and no narration.
  [PAUSE=1.5]    Explicit pause in seconds, holding the current visual.
  [NOTE: …]      Editorial note: never spoken, counted, sent to TTS or exported to the prompter.
  Legacy forms [INSERT_SOUNDBITE_n], [INSERT_IMAGE_n] and [START_SOUNDBITE_n] … [END_SOUNDBITE_n] are accepted;
  use “Normalize legacy tags”. Text inside START/END is a reference transcript, never narration. Missing or
  mismatched END tags, nesting and stray END tags block rendering.
  Parentheses are ordinary narration.

BINDING
  Every marker is bound in the Markers tab to a stable asset ID with in/out trims (HH:MM:SS:FF or seconds).
  Re-using a marker twice in the script creates two independent timeline instances.

TIMING
  estimated total = narration words ÷ WPM × 60 + soundbites + standalone cards + pauses.
  Words are counted with a Unicode-aware tokenizer (letters/digits; markers and notes excluded).
  280 words is only the narration-only allowance at 140 WPM; soundbites reduce it.
  Once a block has recorded or TTS audio its real duration replaces the estimate. Changing the WPM only changes
  estimates. Timeline segments are quantised to whole frames. An over-target package can be edited and previewed,
  but export is blocked until it is shortened or a longer-duration override is recorded.

NARRATION
  Manual: record each block, import a file per block, or import one continuous take and map explicit in/out
  ranges (never split by estimates). TTS: gTTS (one voice per language) or ElevenLabs (your account's voices).
  Online providers receive the narration text. If ElevenLabs fails you are asked before gTTS is used.
  Changing a block's text marks its audio stale until regenerated/re-recorded.

AUDIO
  Narration is silent during statements. B-roll audio is muted unless a binding sets an ambience level.
  Music ducks by the configured dB (default 20 dB, i.e. ×{10 ** (-20 / 20):.2f} amplitude) under narration AND
  statements, or fades out before statements (Mute policy). Peaks are checked against your target.

SUBTITLES
  Whisper runs locally on each trimmed statement. Review and correct cues in the cue editor (max 2 lines × 37–42
  characters, 1–7 s). Captions move above lower thirds when they would collide. SRT/ASS sidecars use the same
  reviewed cues as the burnt-in captions.

EXPORT
  Profiles: ProRes 422 MOV, DNxHD MOV, H.264 MP4 horizontal and vertical. Encoders are tested before rendering.
  Output is written to a temporary file, verified with ffprobe and a full decode, then renamed. Companion files:
  prompter TXT, rundown CSV, segment CSV, SRT/ASS, voice_over.wav, manifest JSON.

PREVIEW
  “Build preview” renders a low-resolution proxy through the final renderer (same frames, audio, captions,
  overlays). Without an audio output device embedded playback is silent and says so; use “External player”.

TROUBLESHOOTING
  • “FFmpeg not found”: install FFmpeg and make sure ffmpeg/ffprobe are on PATH.
  • Microphone missing: install PortAudio (Linux: libportaudio2) or import recordings instead.
  • Whisper model cannot load: the first use downloads it; check the connection or pre-download the model.
  • Missing media: Assets → Relink or Auto-relink.
"""


class CueEditor(tk.Toplevel):
    def __init__(self, app: "App", label: Optional[str]):
        super().__init__(app.root)
        self.app = app
        self.title("Subtitle cue editor")
        self.geometry("980x600")
        p = app.project
        tl = app.timeline
        self.labels = []
        for s in tl.segments if tl else []:
            if s.kind in ("soundbite", "standup") and s.label not in self.labels:
                self.labels.append(s.label)
        top = ttk.Frame(self, padding=4)
        top.pack(fill="x")
        ttk.Label(top, text="Statement").pack(side="left")
        self.label_var = tk.StringVar(value=label if label in self.labels else (self.labels[0] if self.labels else ""))
        cb = ttk.Combobox(top, values=self.labels, textvariable=self.label_var, state="readonly", width=16)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.load())
        self.reviewed = tk.BooleanVar()
        ttk.Checkbutton(top, text="Reviewed", variable=self.reviewed, command=self._set_reviewed).pack(side="left",
                                                                                                    padx=8)
        ttk.Button(top, text="Import SRT…", command=self.import_srt).pack(side="right")
        ttk.Button(top, text="Re-format all", command=self.reformat).pack(side="right", padx=4)
        ttk.Button(top, text="Apply minimum durations", command=self.min_dur).pack(side="right")
        self.info = ttk.Label(self, text="", style="Dim.TLabel")
        self.info.pack(fill="x", padx=6)
        mid = ttk.PanedWindow(self, orient="horizontal")
        mid.pack(fill="both", expand=True)
        cols = ("start", "end", "dur", "cps", "text")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (70, 70, 50, 45, 420)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, stretch=(c == "text"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.select())
        mid.add(self.tree, weight=3)
        ed = ttk.Frame(mid, padding=4)
        self.start = tk.StringVar()
        self.end = tk.StringVar()
        ttk.Label(ed, text="Start (s, clip-relative)").grid(row=0, column=0, sticky="w")
        ttk.Entry(ed, textvariable=self.start, width=10).grid(row=0, column=1, sticky="w")
        ttk.Label(ed, text="End (s)").grid(row=1, column=0, sticky="w")
        ttk.Entry(ed, textvariable=self.end, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(ed, text="Text (Enter = manual line break)").grid(row=2, column=0, columnspan=2, sticky="w")
        self.text = tk.Text(ed, width=44, height=4, bg=BG2, fg=FG, insertbackground=FG, wrap="word",
                            font=("DejaVu Sans", 12))
        self.text.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.wrap_lbl = ttk.Label(ed, text="", font=("DejaVu Sans Mono", 10))
        self.wrap_lbl.grid(row=4, column=0, columnspan=2, sticky="w")
        self.text.bind("<KeyRelease>", lambda e: self._preview_wrap())
        bf = ttk.Frame(ed)
        bf.grid(row=5, column=0, columnspan=2, sticky="w", pady=4)
        for t, cmd in (("Apply", self.apply), ("Add after", self.add), ("Delete", self.delete),
                       ("Split", self.split), ("Merge next", self.merge), ("▶ Play", self.play)):
            ttk.Button(bf, text=t, command=cmd).pack(side="left", padx=1)
        mid.add(ed, weight=2)
        self.diag = tk.Listbox(self, height=6, fg=YELLOW)
        self.diag.pack(fill="x", padx=4, pady=4)
        self.cur: Optional[int] = None
        self.load()
        self.transient(app.root)

    # ------------------------------------------------------------------
    def track(self) -> Optional[SubtitleTrack]:
        label = self.label_var.get()
        if not label:
            return None
        p = self.app.project
        tr = p.subtitle_tracks.get(label)
        if tr is None:
            b = p.bindings.get(label)
            tin, tout, _ = binding_trim(p, b)
            tr = SubtitleTrack(label, language=b.language if b else "el", source="manual", clip_in=tin or 0.0,
                               clip_out=tout or 0.0)
            p.subtitle_tracks[label] = tr
        return tr

    def clip_len(self) -> float:
        p = self.app.project
        tin, tout, _ = binding_trim(p, p.bindings.get(self.label_var.get()))
        return (tout - tin) if tin is not None and tout is not None else 0.0

    def load(self):
        tr = self.track()
        self.tree.delete(*self.tree.get_children())
        if tr is None:
            self.info.configure(text="No statements in the script.")
            return
        st = self.app.project.subtitles
        for i, c in enumerate(tr.cues):
            dur = c.end - c.start
            cps = len(c.text.replace("\n", " ")) / dur if dur > 0 else 0
            self.tree.insert("", "end", iid=str(i), values=(f"{c.start:.2f}", f"{c.end:.2f}", f"{dur:.2f}",
                                                            f"{cps:.0f}", c.text.replace("\n", " ⏎ ")))
        self.reviewed.set(tr.reviewed)
        p = self.app.project
        b = p.bindings.get(tr.label)
        self.info.configure(text=f"Source: {tr.source}{' / ' + tr.model if tr.model else ''}. Times are relative to the "
                                 f"trimmed clip (0–{self.clip_len():.2f} s). Transcribed trim "
                                 f"{tr.clip_in:.2f}–{tr.clip_out:.2f} s; current in-point "
                                 f"{b.in_seconds if b else 0:.2f} s.")
        self.diag.delete(0, "end")
        for d in validate_track(tr, self.clip_len(), st, tr.label):
            self.diag.insert("end", f"{d.severity.upper()}: {d.message}")
        if self.cur is not None and self.tree.exists(str(self.cur)):
            self.tree.selection_set(str(self.cur))

    def changed(self):
        tr = self.track()
        tr.reviewed = False
        self.reviewed.set(False)
        self.app.mark_dirty()
        self.app.schedule_refresh()
        self.load()

    def _set_reviewed(self):
        tr = self.track()
        if tr:
            tr.reviewed = self.reviewed.get()
            self.app.mark_dirty()
            self.app.schedule_refresh()
            self.load()

    def select(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.cur = int(sel[0])
        c = self.track().cues[self.cur]
        self.start.set(f"{c.start:.3f}")
        self.end.set(f"{c.end:.3f}")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", c.text)
        self._preview_wrap()

    def _preview_wrap(self):
        st = self.app.project.subtitles
        t = self.text.get("1.0", "end-1c")
        lines = [l for l in t.split("\n")] if "\n" in t else wrap_cue_text(t, st.max_chars, st.max_lines)
        if not lines:
            self.wrap_lbl.configure(text=f"Does not fit {st.max_lines}×{st.max_chars}: split it", foreground=RED)
        else:
            bad = len(lines) > st.max_lines or any(len(l) > st.max_chars for l in lines)
            self.wrap_lbl.configure(text="\n".join(f"{len(l):2d}| {l}" for l in lines),
                                    foreground=RED if bad else FG)

    def apply(self):
        if self.cur is None:
            return
        try:
            s, e = parse_time(self.start.get()), parse_time(self.end.get())
        except ValueError as exc:
            messagebox.showerror("Cue", str(exc), parent=self)
            return
        c = self.track().cues[self.cur]
        c.start, c.end, c.text = s, e, self.text.get("1.0", "end-1c").strip()
        c.words = []
        self.track().cues.sort(key=lambda x: x.start)
        self.changed()

    def add(self):
        tr = self.track()
        if tr is None:
            return
        if self.cur is not None and tr.cues:
            prev = tr.cues[self.cur]
            s = prev.end
        else:
            s = tr.cues[-1].end if tr.cues else 0.0
        e = min(self.clip_len(), s + 2.0)
        if e <= s:
            messagebox.showinfo("Cue", "No room after this cue inside the clip.", parent=self)
            return
        tr.cues.append(Cue(round(s, 3), round(e, 3), "…"))
        tr.cues.sort(key=lambda x: x.start)
        self.cur = tr.cues.index(next(c for c in tr.cues if c.start == round(s, 3)))
        self.changed()

    def delete(self):
        if self.cur is None:
            return
        del self.track().cues[self.cur]
        self.cur = None
        self.changed()

    def split(self):
        if self.cur is None:
            return
        tr = self.track()
        c = tr.cues[self.cur]
        words = c.text.replace("\n", " ").split()
        if len(words) < 2:
            return
        if c.words and len(c.words) == len(words):
            mid = len(words) // 2
            t = c.words[mid][0]
            a = Cue(c.start, t, " ".join(words[:mid]), c.words[:mid])
            b = Cue(t, c.end, " ".join(words[mid:]), c.words[mid:])
        else:
            mid = len(words) // 2
            first = " ".join(words[:mid])
            frac = len(first) / max(1, len(" ".join(words)))
            t = round(c.start + (c.end - c.start) * frac, 3)
            a, b = Cue(c.start, t, first), Cue(t, c.end, " ".join(words[mid:]))
        tr.cues[self.cur:self.cur + 1] = [a, b]
        self.changed()

    def merge(self):
        tr = self.track()
        if self.cur is None or self.cur + 1 >= len(tr.cues):
            return
        a, b = tr.cues[self.cur], tr.cues[self.cur + 1]
        tr.cues[self.cur:self.cur + 2] = [Cue(a.start, b.end, f"{a.text} {b.text}", a.words + b.words)]
        self.changed()

    def reformat(self):
        tr = self.track()
        if tr is None or not tr.cues:
            return
        segs = [{"start": c.start, "end": c.end, "text": c.text.replace("\n", " "), "words": c.words} for c in tr.cues]
        tr.cues = format_segments(segs, self.app.project.subtitles)
        self.changed()

    def min_dur(self):
        tr = self.track()
        if tr is None:
            return
        apply_min_duration(tr.cues, self.app.project.subtitles, self.clip_len())
        self.changed()

    def play(self):
        if self.cur is None:
            return
        p = self.app.project
        b = p.bindings.get(self.label_var.get())
        a = p.asset(b.asset_id) if b else None
        if a is None:
            return
        c = self.track().cues[self.cur]
        self.app.play_audio_file(a.path, b.in_seconds + c.start, c.end - c.start)

    def import_srt(self):
        f = filedialog.askopenfilename(parent=self, filetypes=[("SRT", "*.srt"), ("All", "*")])
        if not f:
            return
        try:
            rows = parse_srt(Path(f).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror("Import SRT", str(exc), parent=self)
            return
        tr = self.track()
        tr.cues = [Cue(s, e, t) for s, e, t in rows]
        tr.source = "manual"
        self.changed()


class TakeMapper(tk.Toplevel):
    """Assign explicit in/out ranges of continuous takes to narration blocks."""

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title("Map continuous take to narration blocks")
        p = app.project
        tl = app.timeline
        f = ttk.Frame(self, padding=8)
        f.pack(fill="both", expand=True)
        takes = {f"{Path(t.path).name} ({t.duration:.1f}s)": t for t in p.narration.takes}
        if not takes:
            ttk.Label(f, text="Import a continuous take first (Narration → Import continuous take).").pack()
            return
        self.takes = takes
        ttk.Label(f, text="Enter explicit in/out times (seconds or HH:MM:SS:FF) for every block. Ranges of one take "
                          "must not overlap. Nothing is split automatically.", wraplength=640).grid(
            row=0, column=0, columnspan=6, sticky="w", pady=4)
        for c, t in enumerate(("Block", "Take", "In", "Out", "")):
            ttk.Label(f, text=t, style="Head.TLabel").grid(row=1, column=c, sticky="w")
        self.rows = []
        blocks = [s for s in (tl.segments if tl else []) if s.kind == "narration"]
        for i, s in enumerate(blocks):
            na = s.narration if (s.narration and s.narration.source == "take" and not s.stale) else None
            take_label = next((k for k, t in takes.items() if na and t.id == na.take_id), next(iter(takes)))
            tv, iv, ov = tk.StringVar(value=take_label), tk.StringVar(value=f"{na.take_in:.2f}" if na else ""), \
                tk.StringVar(value=f"{na.take_out:.2f}" if na else "")
            ttk.Label(f, text=f"{s.block_index + 1}. {s.text[:40]}…").grid(row=i + 2, column=0, sticky="w")
            ttk.Combobox(f, values=list(takes), textvariable=tv, state="readonly", width=24).grid(row=i + 2, column=1)
            ttk.Entry(f, textvariable=iv, width=9).grid(row=i + 2, column=2, padx=2)
            ttk.Entry(f, textvariable=ov, width=9).grid(row=i + 2, column=3, padx=2)
            ttk.Button(f, text="▶", width=3, command=lambda tv=tv, iv=iv, ov=ov: self.play(tv, iv, ov)).grid(
                row=i + 2, column=4)
            self.rows.append((s, tv, iv, ov))
        self.msg = ttk.Label(f, text="", foreground=RED, wraplength=640)
        self.msg.grid(row=len(blocks) + 2, column=0, columnspan=6, sticky="w")
        ttk.Button(f, text="Apply mapping", style="Accent.TButton", command=self.apply).grid(
            row=len(blocks) + 3, column=3, columnspan=2, sticky="e", pady=6)
        self.transient(app.root)

    def play(self, tv, iv, ov):
        try:
            tin, tout = parse_time(iv.get()), parse_time(ov.get())
        except ValueError:
            return
        self.app.play_audio_file(self.takes[tv.get()].path, tin, max(0.1, tout - tin))

    def apply(self):
        from ..narration import map_take_range

        errs = []
        ranges = []
        for s, tv, iv, ov in self.rows:
            if not iv.get().strip() and not ov.get().strip():
                continue
            try:
                tin, tout = parse_time(iv.get()), parse_time(ov.get())
            except ValueError as exc:
                errs.append(f"Block {s.block_index + 1}: {exc}")
                continue
            take = self.takes[tv.get()]
            ranges.append((take.id, tin, tout, s))
        for a in ranges:
            for b in ranges:
                if a is not b and a[0] == b[0] and a[1] < b[2] and b[1] < a[2] and a[3].block_index < b[3].block_index:
                    errs.append(f"Blocks {a[3].block_index + 1} and {b[3].block_index + 1} overlap in the same take.")
        if errs:
            self.msg.configure(text="\n".join(errs))
            return
        try:
            for take_id, tin, tout, s in ranges:
                map_take_range(self.app.project, s.block_index, s.text, take_id, tin, tout)
        except ValueError as exc:
            self.msg.configure(text=str(exc))
            return
        unassigned = len(self.rows) - len(ranges)
        self.app.mark_dirty()
        self.app.schedule_refresh()
        if unassigned:
            messagebox.showwarning("Mapping", f"{unassigned} block(s) are still unassigned; rendering stays blocked "
                                              "until every block has audio.", parent=self)
        self.destroy()
