"""Preview & delivery: proxy player, transport, delivery buttons, job progress, diagnostics."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageTk

from ..util import seconds_to_tc
from .widgets import FG_DIM, RED, YELLOW

if TYPE_CHECKING:
    from .app import App


class PreviewPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.player = None
        self.proxy_info: Optional[dict] = None
        self._img = None
        self._last_idx = -1
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # --- player -----------------------------------------------------
        left = ttk.Frame(pw)
        tr = ttk.Frame(left)
        tr.pack(fill="x", pady=2)
        ttk.Button(tr, text="⏮", width=3, command=lambda: self.seek_seconds(0)).pack(side="left")
        ttk.Button(tr, text="◀|", width=3, command=lambda: self.step(-1)).pack(side="left")
        self.play_btn = ttk.Button(tr, text="▶", width=3, command=self.toggle)
        self.play_btn.pack(side="left")
        ttk.Button(tr, text="|▶", width=3, command=lambda: self.step(1)).pack(side="left")
        self.tc = ttk.Label(tr, text="00:00:00:00", font=("DejaVu Sans Mono", 11))
        self.tc.pack(side="left", padx=6)
        self.seg_lbl = ttk.Label(tr, text="", style="Dim.TLabel")
        self.seg_lbl.pack(side="left", padx=4)
        self.pos = tk.DoubleVar(value=0)
        self.scale = ttk.Scale(left, from_=0, to=1, variable=self.pos, command=self._scrub)
        self.scale.pack(fill="x")
        pr = ttk.Frame(left)
        pr.pack(fill="x", pady=2)
        self.scope = tk.StringVar(value="full")
        ttk.Radiobutton(pr, text="Full", value="full", variable=self.scope).pack(side="left")
        ttk.Radiobutton(pr, text="Selected segment", value="segment", variable=self.scope).pack(side="left", padx=4)
        ttk.Button(pr, text="Build preview", style="Accent.TButton", command=self.app.build_preview).pack(
            side="left", padx=4)
        ttk.Button(pr, text="External player", command=self.app.open_external_preview).pack(side="left")
        ttk.Button(pr, text="Inspect frame", command=self.app.inspect_frame).pack(side="left", padx=4)
        self.note = ttk.Label(left, text="Build a preview to play the package with speech, music, captions, "
                                         "lower thirds and watermark.", style="Dim.TLabel", wraplength=420)
        self.note.pack(fill="x")
        for w in (self.note, pr, self.scale, tr):
            w.pack_forget()
        for w in (self.note, pr, self.scale, tr):
            w.pack(side="bottom", fill="x", pady=1)
        self.canvas = tk.Canvas(left, bg="#000", highlightthickness=0, width=400, height=160)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        pw.add(left, weight=3)

        # --- delivery + diagnostics --------------------------------------
        right = ttk.Frame(pw)
        bf = ttk.Frame(right)
        bf.pack(fill="x")
        buttons = [
            ("● Record", self.app.record_start), ("■ Stop", self.app.record_stop),
            ("Generate Narration", lambda: self.app.generate_tts(None)),
            ("Generate Subtitles", lambda: self.app.transcribe(None)),
            ("Edit Subtitles", lambda: self.app.open_cue_editor(None)),
            ("Validate", self.app.validate), ("Render", self.app.render),
            ("Cancel Render", self.app.cancel_jobs), ("Export Prompter", lambda: self.app.export_companion("prompter")),
            ("Export Rundown", lambda: self.app.export_companion("rundown")),
            ("Open Export Folder", self.app.open_export_folder),
        ]
        self.buttons = {}
        for i, (t, cmd) in enumerate(buttons):
            b = ttk.Button(bf, text=t, command=cmd, style="Accent.TButton" if t == "Render" else "TButton")
            b.grid(row=i // 4, column=i % 4, sticky="ew", padx=1, pady=1)
            self.buttons[t] = b
        for c in range(4):
            bf.columnconfigure(c, weight=1)
        self.buttons["Cancel Render"].state(["disabled"])
        jf = ttk.Frame(right)
        jf.pack(fill="x", pady=2)
        self.job_lbl = ttk.Label(jf, text="No job running", style="Dim.TLabel")
        self.job_lbl.pack(side="left")
        self.elapsed_lbl = ttk.Label(jf, text="", style="Dim.TLabel")
        self.elapsed_lbl.pack(side="right")
        self.progress = ttk.Progressbar(right, maximum=1.0)
        self.progress.pack(fill="x")
        cols = ("segment", "message")
        self.diag = ttk.Treeview(right, columns=cols, show="tree headings", height=6)
        self.diag.heading("#0", text="")
        self.diag.column("#0", width=86, stretch=False)
        self.diag.heading("segment", text="Segment")
        self.diag.column("segment", width=110, stretch=False)
        self.diag.heading("message", text="Diagnostics (double-click to go to it)")
        self.diag.column("message", width=420)
        self.diag.tag_configure("error", foreground=RED)
        self.diag.tag_configure("warning", foreground=YELLOW)
        self.diag.tag_configure("info", foreground=FG_DIM)
        sb = ttk.Scrollbar(right, command=self.diag.yview)
        self.diag.configure(yscrollcommand=sb.set)
        self.diag.pack(side="left", fill="both", expand=True, pady=(4, 0))
        sb.pack(side="right", fill="y", pady=(4, 0))
        self.diag.bind("<Double-1>", self._goto_diag)
        self.diag.bind("<Motion>", self._diag_tip)
        self._diags = []
        pw.add(right, weight=4)
        self.after(40, self._tick)

    # --- diagnostics -------------------------------------------------------
    def show_diagnostics(self, diags) -> None:
        self._diags = diags
        self.diag.delete(*self.diag.get_children())
        for i, d in enumerate(diags):
            self.diag.insert("", "end", iid=str(i), text=d.severity.upper(), values=(d.segment, d.message),
                             tags=(d.severity,))

    def _goto_diag(self, _e=None):
        sel = self.diag.selection()
        if not sel:
            return
        d = self._diags[int(sel[0])]
        self.app.navigate_to(d)

    def _diag_tip(self, e):
        iid = self.diag.identify_row(e.y)
        if iid and iid.isdigit() and int(iid) < len(self._diags):
            self.app.status(self._diags[int(iid)].message)

    # --- jobs ----------------------------------------------------------------
    def job_state(self, running: bool, label: str = "", fraction: Optional[float] = None, elapsed: float = 0.0):
        if running:
            self.job_lbl.configure(text=label, style="TLabel")
            self.elapsed_lbl.configure(text=f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
            if fraction is None:
                if str(self.progress.cget("mode")) != "indeterminate":
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(15)
            else:
                if str(self.progress.cget("mode")) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress["value"] = fraction
            self.buttons["Cancel Render"].state(["!disabled"])
            self.buttons["Render"].state(["disabled"])
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress["value"] = 0
            self.job_lbl.configure(text=label or "No job running", style="Dim.TLabel")
            self.buttons["Cancel Render"].state(["disabled"])
            self.buttons["Render"].state(["!disabled"])

    # --- player ----------------------------------------------------------------
    def load_proxy(self, info: dict) -> None:
        from ..preview import ProxyPlayer

        self.close_player()
        self.proxy_info = info
        self.player = ProxyPlayer(info["path"], info["fps"], info["width"], info["height"])
        self.scale.configure(to=max(0.04, self.player.duration))
        if self.player.audio_ok:
            self.note.configure(text="Preview proxy loaded (same timeline and edit points as the final render).")
        else:
            self.note.configure(text="No audio output device is available, so embedded playback is SILENT "
                                     f"({self.player.audio_error[:80]}). Use “External player” to hear the preview.",
                                foreground=YELLOW)
        self.seek_seconds(0)

    def close_player(self):
        if self.player is not None:
            self.player.close()
            self.player = None

    def toggle(self):
        if self.player is None:
            self.app.build_preview()
            return
        if self.player.playing:
            self.player.pause()
        else:
            self.player.play()

    def step(self, n: int):
        if self.player is not None:
            self.player.step(n)
            self._show_latest(force=True)

    def seek_seconds(self, t: float):
        if self.player is None:
            return
        off = self.proxy_info["first_frame"] / self.proxy_info["fps"]
        self.player.seek(max(0.0, t - off))
        self._show_latest(force=True)

    def _scrub(self, _v=None):
        if self.player is None or self.player.playing:
            return
        if getattr(self, "_scrub_job", None):
            self.after_cancel(self._scrub_job)
        self._scrub_job = self.after(120, lambda: (self.player.seek(float(self.pos.get())),
                                                   self._show_latest(force=True)))

    def playhead_frame(self) -> int:
        if self.player is None or self.proxy_info is None:
            return 0
        return self.proxy_info["first_frame"] + int(round(self.player.clock() * self.proxy_info["fps"]))

    def _tick(self):
        try:
            if self.player is not None:
                self._show_latest()
                self.play_btn.configure(text="❚❚" if self.player.playing else "▶")
        finally:
            self.after(30, self._tick)

    def _show_latest(self, force=False):
        p = self.player
        if p is None or p.latest is None:
            return
        idx, frame = p.latest
        if idx == self._last_idx and not force:
            return
        self._last_idx = idx
        self._frame = frame
        self._redraw()
        fps = self.proxy_info["fps"]
        abs_frame = self.proxy_info["first_frame"] + idx
        self.tc.configure(text=seconds_to_tc(abs_frame / fps, fps))
        if p.playing:
            self.pos.set(idx / fps)
        tl = self.app.timeline
        seg = tl.segment_at_frame(abs_frame) if tl else None
        self.seg_lbl.configure(text=seg.name() if seg else "")

    def _redraw(self):
        frame = getattr(self, "_frame", None)
        if frame is None:
            return
        cw, ch = max(10, self.canvas.winfo_width()), max(10, self.canvas.winfo_height())
        img = Image.fromarray(frame)
        s = min(cw / img.width, ch / img.height)
        img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
        self._img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._img)

    def show_still(self, frame):
        self._frame = frame
        self._redraw()
