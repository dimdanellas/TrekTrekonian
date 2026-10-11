"""Script editor with marker insertion and live timing counters."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import simpledialog, ttk
from typing import TYPE_CHECKING

from ..parser import next_free_number
from ..timeline import OVERLAP_WARNING
from .widgets import BG2, FG, FG_DIM, MONO, OVERRUN_BG, RED, SCRIPT_BG, YELLOW

if TYPE_CHECKING:
    from .app import App

_TAG_RE = re.compile(r"\[(?:BROLL|IMAGE|CARD|SOUNDBITE|STANDUP|INSERT_[A-Z]+|START_SOUNDBITE|END_SOUNDBITE)_\d+\]"
                     r"|\[PAUSE\s*=\s*[0-9.,]+\s*\]", re.IGNORECASE)
_NOTE_RE = re.compile(r"\[NOTE\s*:.*?\]", re.DOTALL | re.IGNORECASE)


class ScriptPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self._suppress = False
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(2, 2))
        ttk.Label(bar, text="Script & timing", style="Head.TLabel").pack(side="left", padx=4)
        for kind, label in (("BROLL", "+B-roll"), ("IMAGE", "+Image"), ("SOUNDBITE", "+Soundbite"),
                            ("STANDUP", "+Stand-up"), ("CARD", "+Card")):
            ttk.Button(bar, text=label, style="Small.TButton", width=len(label),
                       command=lambda k=kind: self.insert_marker(k)).pack(side="left", padx=1)
        ttk.Button(bar, text="+Pause", style="Small.TButton", width=6, command=self.insert_pause).pack(side="left", padx=1)
        ttk.Button(bar, text="+Note", style="Small.TButton", width=5, command=self.insert_note).pack(side="left", padx=1)
        self.normalize_btn = ttk.Button(bar, text="Normalize legacy tags", command=self.normalize)

        self.overlap_lbl = ttk.Label(self, text="", style="Warn.TLabel", wraplength=520)
        self.overlap_lbl.pack(side="bottom", fill="x", padx=4)
        stats = ttk.Frame(self, style="Card.TFrame", padding=6)
        stats.pack(side="bottom", fill="x", pady=(4, 0))
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", undo=True, autoseparators=True, maxundo=-1, bg=SCRIPT_BG, fg=FG,
                            insertbackground=FG, selectbackground="#3d5a99", relief="flat", padx=8, pady=8,
                            font=("DejaVu Sans", 12))
        sb = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_configure("marker", foreground="#7fb3ff", font=MONO)
        self.text.tag_configure("note", foreground="#8fbf7f", font=("DejaVu Sans", 11, "italic"))
        self.text.tag_configure("bad", underline=True, foreground=RED)
        self.text.tag_configure("focus", background="#5a4a10")
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Control-z>", lambda e: (self.undo(), "break")[1])
        self.text.bind("<Control-y>", lambda e: (self.redo(), "break")[1])
        self.text.bind("<Control-Shift-Z>", lambda e: (self.redo(), "break")[1])

        # timing counters
        self.v_words = tk.StringVar(value="0")
        self.v_est = tk.StringVar(value="0.0 s")
        self.v_total = tk.StringVar(value="0.0 s")
        self.v_measured = tk.StringVar(value="—")
        self.v_remaining = tk.StringVar(value="")
        self.v_allow = tk.StringVar(value="")
        self.v_wpm = tk.DoubleVar(value=140)
        self.v_target = tk.DoubleVar(value=120)
        grid = [
            ("Narration words", self.v_words), ("Narration estimate", self.v_est),
            ("Estimated total", self.v_total), ("Measured total", self.v_measured),
            ("Word allowance", self.v_allow),
        ]
        for i, (lab, var) in enumerate(grid):
            ttk.Label(stats, text=lab, style="Card.TLabel").grid(row=i // 3 * 2, column=i % 3, sticky="w", padx=6)
            ttk.Label(stats, textvariable=var, style="Card.TLabel", font=("TkDefaultFont", 11, "bold")).grid(
                row=i // 3 * 2 + 1, column=i % 3, sticky="w", padx=6)
        self.remaining_lbl = ttk.Label(stats, textvariable=self.v_remaining, style="Green.TLabel")
        self.remaining_lbl.grid(row=2, column=2, rowspan=2, sticky="w", padx=6)
        ctl = ttk.Frame(stats, style="Card.TFrame")
        ctl.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(ctl, text="WPM", style="Card.TLabel").pack(side="left", padx=(6, 2))
        self.wpm_spin = ttk.Spinbox(ctl, from_=120, to=160, increment=5, width=5, textvariable=self.v_wpm,
                                    command=self._wpm_changed)
        self.wpm_spin.pack(side="left")
        self.wpm_spin.bind("<FocusOut>", lambda e: self._wpm_changed())
        self.wpm_spin.bind("<Return>", lambda e: self._wpm_changed())
        ttk.Label(ctl, text="Target (s)", style="Card.TLabel").pack(side="left", padx=(12, 2))
        self.target_spin = ttk.Spinbox(ctl, from_=10, to=900, increment=5, width=6, textvariable=self.v_target,
                                       command=self._target_changed)
        self.target_spin.pack(side="left")
        self.target_spin.bind("<FocusOut>", lambda e: self._target_changed())
        self.target_spin.bind("<Return>", lambda e: self._target_changed())
        self.override_var = tk.BooleanVar(value=False)
        self.override_chk = ttk.Checkbutton(ctl, text="Longer-duration override", variable=self.override_var,
                                            command=self._override_changed)
        self.override_chk.pack(side="left", padx=(12, 2))
        for c in range(3):
            stats.columnconfigure(c, weight=1)

    # ------------------------------------------------------------------
    def get(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set(self, script: str, reset_undo: bool = True) -> None:
        self._suppress = True
        self.text.delete("1.0", "end")
        self.text.insert("1.0", script)
        if reset_undo:
            self.text.edit_reset()
        self.text.edit_modified(False)
        self._suppress = False
        self.highlight()
        self.app.script_changed(self.get(), user=False)

    def _on_modified(self, _e=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._suppress:
            return
        self.highlight()
        self.app.script_changed(self.get(), user=True)

    def undo(self):
        try:
            self.text.edit_undo()
        except tk.TclError:
            pass

    def redo(self):
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass

    def insert_text(self, s: str) -> None:
        self.text.edit_separator()
        try:
            self.text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.text.insert("insert", s)
        self.text.edit_separator()
        self.text.focus_set()

    def insert_marker(self, kind: str) -> None:
        n = next_free_number(self.get(), kind)
        prefix = "" if self.text.get("insert linestart", "insert") == "" else "\n"
        self.insert_text(f"{prefix}[{kind}_{n}]\n")

    def insert_pause(self) -> None:
        v = simpledialog.askfloat("Insert pause", "Pause length in seconds:", parent=self, minvalue=0.1,
                                  maxvalue=60, initialvalue=1.0)
        if v:
            self.insert_text(f"\n[PAUSE={v:g}]\n")

    def insert_note(self) -> None:
        v = simpledialog.askstring("Insert editorial note",
                                   "Note text (never spoken, counted or exported as narration):", parent=self)
        if v:
            self.insert_text(f"[NOTE: {v.replace(']', ')')}]")

    def normalize(self) -> None:
        res = self.app.parse
        if res is None or not res.needs_normalization:
            return
        self.text.edit_separator()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", res.normalized_script)
        self.text.edit_separator()

    def highlight(self) -> None:
        s = self.get()
        for tag in ("marker", "note"):
            self.text.tag_remove(tag, "1.0", "end")
        for m in _NOTE_RE.finditer(s):
            self.text.tag_add("note", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        for m in _TAG_RE.finditer(s):
            self.text.tag_add("marker", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def mark_errors(self, diags) -> None:
        self.text.tag_remove("bad", "1.0", "end")
        for d in diags:
            if d.is_error and d.char_start is not None and d.char_end is not None and d.code not in (
                    "over_target", "stale_narration", "narration_missing"):
                self.text.tag_add("bad", f"1.0+{d.char_start}c", f"1.0+{d.char_end}c")

    def goto(self, start: int, end: int) -> None:
        self.text.tag_remove("focus", "1.0", "end")
        a, b = f"1.0+{start}c", f"1.0+{max(end, start + 1)}c"
        self.text.tag_add("focus", a, b)
        self.text.see(a)
        self.text.mark_set("insert", a)
        self.text.focus_set()
        self.after(2500, lambda: self.text.tag_remove("focus", "1.0", "end"))

    # ------------------------------------------------------------------
    def update_stats(self, tl, project) -> None:
        self.v_words.set(str(tl.narration_words))
        self.v_est.set(f"{tl.narration_estimate_seconds:.1f} s @ {project.wpm:g} WPM")
        self.v_total.set(f"{tl.estimated_total:.1f} s / {project.target_seconds:g} s")
        if tl.narration_measured_seconds > 0 or (tl.all_measured and tl.segments):
            label = "all measured" if tl.all_measured else "partly estimated"
            self.v_measured.set(f"{tl.total_seconds:.2f} s ({label}, {tl.total_frames} fr)")
        else:
            self.v_measured.set("— (no narration audio yet)")
        self.v_allow.set(f"{tl.narration_words} / {tl.available_words} words")
        over = tl.over_target
        rem = tl.remaining_seconds
        self.v_remaining.set(f"Remaining: {rem:+.1f} s" if not over else f"OVER by {-rem:.1f} s")
        self.remaining_lbl.configure(style="Red.TLabel" if over else "Green.TLabel")
        # Overrun background; restored automatically once corrected.
        self.text.configure(bg=OVERRUN_BG if over else SCRIPT_BG)
        overlap = [d for d in tl.diagnostics + list(tl.parse.diagnostics)
                   if d.code in ("vo_overlap", "paired_reference_text")]
        if overlap:
            extra = "" if any(d.code == "vo_overlap" for d in overlap) else \
                " (κείμενο μέσα σε ζεύγος ετικετών δήλωσης — εξαιρείται από την εκφώνηση)"
            self.overlap_lbl.configure(text=OVERLAP_WARNING + extra)
        else:
            self.overlap_lbl.configure(text="")
        if tl.parse.needs_normalization:
            self.normalize_btn.pack(side="right", padx=4)
        else:
            self.normalize_btn.pack_forget()
        self.override_var.set(project.duration_override)

    def _wpm_changed(self):
        try:
            v = float(self.v_wpm.get())
        except (tk.TclError, ValueError):
            return
        v = max(120.0, min(160.0, v))
        self.v_wpm.set(v)
        self.app.set_project_value(lambda p: setattr(p, "wpm", v))

    def _target_changed(self):
        try:
            v = float(self.v_target.get())
        except (tk.TclError, ValueError):
            return
        if v > 0:
            self.app.set_project_value(lambda p: setattr(p, "target_seconds", v))

    def _override_changed(self):
        on = self.override_var.get()
        reason = ""
        if on:
            reason = simpledialog.askstring("Longer-duration override",
                                            "Record why this package may exceed the target (saved in the project and "
                                            "export manifest):", parent=self) or ""
            if not reason.strip():
                self.override_var.set(False)
                return

        def apply(p):
            p.duration_override = on
            p.duration_override_reason = reason.strip()

        self.app.set_project_value(apply)
