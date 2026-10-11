"""Script editor with marker buttons and live timing counters."""
from __future__ import annotations

from pathlib import Path

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ...parser import next_free_number, parse_script
from ..widgets import (DIM, FONT, GREEN, OVERRUN_BG, RED, ROW, SCRIPT_BG, WHITE, FilePicker, ask_text, button,
                       checkbox, confirm, heading, label, message, text_input, vstack)
from .base import Tab, fill_diagnostics, float_or

OVERLAP_WARNING = "Narration overlaps a statement: VO text is inside a soundbite span."


class ScriptTab(Tab):
    def __init__(self, app):
        super().__init__(app)
        self._loading = False
        bar_sv = ScrollView(do_scroll_y=False, size_hint_y=None, height=ROW + dp(6), bar_width=dp(4))
        bar = BoxLayout(size_hint_x=None, spacing=dp(4))
        bar.bind(minimum_width=bar.setter("width"))
        for kind in ("BROLL", "IMAGE", "SOUNDBITE", "STANDUP", "CARD"):
            bar.add_widget(button(f"+ {kind}", lambda k=kind: self.insert_marker(k), width=dp(140)))
        bar.add_widget(button("+ PAUSE", self.insert_pause, width=dp(110)))
        bar.add_widget(button("+ NOTE", self.insert_note, width=dp(100)))
        bar.add_widget(button("Undo", lambda: self.text.do_undo(), width=dp(80)))
        bar.add_widget(button("Redo", lambda: self.text.do_redo(), width=dp(80)))
        bar.add_widget(button("Import .txt", self.import_script, width=dp(130)))
        self.normalize_btn = button("Normalize legacy tags", self.normalize, width=dp(220))
        self.normalize_btn.disabled = True
        bar.add_widget(self.normalize_btn)
        bar_sv.add_widget(bar)
        self.add_widget(bar_sv)

        body = BoxLayout(spacing=dp(6))
        self.text = TextInput(font_size=FONT, background_color=SCRIPT_BG, foreground_color=WHITE,
                              cursor_color=WHITE, hint_text="Write or paste the script here…", size_hint_x=0.66,
                              do_wrap=True)
        self.text.bind(text=self._on_text)
        body.add_widget(self.text)

        side = vstack(spacing=dp(4))
        side.add_widget(heading("Timing"))
        self.v_target = text_input("", self._target_changed, input_filter="float")
        self.v_wpm = text_input("", self._wpm_changed, input_filter="float")
        side.add_widget(self._pair("Target (s)", self.v_target))
        side.add_widget(self._pair("WPM (120–160)", self.v_wpm))
        self.stats = {}
        for key, cap in (("words", "Narration words"), ("est", "Narration estimate"), ("total", "Estimated total"),
                         ("measured", "Measured total"), ("allow", "Word allowance")):
            v = label("—")
            side.add_widget(self._pair(cap, v))
            self.stats[key] = v
        self.remaining = label("", bold=True)
        side.add_widget(self.remaining)
        self.overlap = label("", color=RED)
        side.add_widget(self.overlap)
        self.override = checkbox("Allow longer than target", False, self._override_changed)
        side.add_widget(self.override)
        side.add_widget(heading("Diagnostics"))
        self.diag_box = vstack(spacing=dp(2))
        side.add_widget(self.diag_box)
        sv = ScrollView(do_scroll_x=False, size_hint_x=0.34, bar_width=dp(8))
        sv.add_widget(side)
        body.add_widget(sv)
        self.add_widget(body)

    @staticmethod
    def _pair(caption, widget):
        row = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(4))
        cap = label(caption, color=DIM)
        cap.size_hint_y = 1
        row.add_widget(cap)
        widget.size_hint_y = 1
        row.add_widget(widget)
        return row

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._loading = True
        p = self.app.project
        self.text.text = p.script
        self.text._undo, self.text._redo = [], []  # a loaded project starts a fresh undo history
        self.v_target.text = f"{p.target_seconds:g}"
        self.v_wpm.text = f"{p.wpm:g}"
        self.override.checkbox.active = p.duration_override
        self._loading = False

    def _on_text(self, _w, value):
        if not self._loading:
            self.app.script_changed(value)

    def refresh(self, full: bool) -> None:
        tl, p = self.app.timeline, self.app.project
        s = self.stats
        s["words"].text = str(tl.narration_words)
        s["est"].text = f"{tl.narration_estimate_seconds:.1f} s @ {p.wpm:g} WPM"
        s["total"].text = f"{tl.estimated_total:.1f} s / {p.target_seconds:g} s"
        if tl.narration_measured_seconds > 0 or (tl.all_measured and tl.segments):
            kind = "all measured" if tl.all_measured else "partly estimated"
            s["measured"].text = f"{tl.total_seconds:.2f} s ({kind})"
        else:
            s["measured"].text = "— (no narration audio yet)"
        s["allow"].text = f"{tl.narration_words} / {tl.available_words} words"
        over, rem = tl.over_target, tl.remaining_seconds
        self.remaining.text = f"OVER by {-rem:.1f} s" if over else f"Remaining: {rem:+.1f} s"
        self.remaining.color = RED if over else GREEN
        self.text.background_color = OVERRUN_BG if over else SCRIPT_BG
        overlap = [d for d in tl.diagnostics + list(tl.parse.diagnostics)
                   if d.code in ("vo_overlap", "paired_reference_text")]
        self.overlap.text = OVERLAP_WARNING if any(d.code == "vo_overlap" for d in overlap) else ""
        self.normalize_btn.disabled = not tl.parse.needs_normalization
        self._loading = True
        self.override.checkbox.active = p.duration_override
        self._loading = False
        script_diags = [d for d in self.app.diagnostics if d.char_start is not None or d.code == "vo_overlap"]
        fill_diagnostics(self.diag_box, script_diags, self.app.navigate_to, limit=25, empty="Script is valid.")

    # ------------------------------------------------------------------ editing
    def goto(self, start: int, end: int) -> None:
        self.text.focus = True
        self.text.cursor = self.text.get_cursor_from_index(start)
        self.text.select_text(start, end)

    def insert_text(self, s: str) -> None:
        ti = self.text
        if ti.selection_text:
            ti.delete_selection()
        ti.insert_text(s)
        ti.focus = True

    def _at_line_start(self) -> bool:
        return self.text.cursor_col == 0

    def insert_marker(self, kind: str) -> None:
        n = next_free_number(self.text.text, kind)
        prefix = "" if self._at_line_start() else "\n"
        self.insert_text(f"{prefix}[{kind}_{n}]\n")

    def insert_pause(self) -> None:
        def ok(v):
            sec = float_or(v)
            if sec is None or not 0.1 <= sec <= 60:
                message("Pause", "Enter a length between 0.1 and 60 seconds.")
                return
            self.insert_text(f"\n[PAUSE={sec:g}]\n")

        ask_text("Insert pause", "Pause length in seconds:", "1.0", ok, input_filter="float")

    def insert_note(self) -> None:
        ask_text("Editorial note", "Note text (never spoken, counted or exported as narration):", "",
                 lambda v: v.strip() and self.insert_text(f"[NOTE: {v.strip().replace(']', ')')}]"), multiline=True)

    def normalize(self) -> None:
        res = self.app.parse
        if res is None or not res.needs_normalization:
            return
        self.text.text = res.normalized_script

    def import_script(self) -> None:
        def chosen(files):
            try:
                content = Path(files[0]).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                message("Import", str(exc))
                return

            def replace():
                self.text.text = content
                if parse_script(content).needs_normalization:
                    confirm("Legacy markers", "The script uses legacy markers ([INSERT_…] or START/END pairs). "
                                              "Normalise them now? (Undo is available.)", self.normalize)

            if self.text.text.strip():
                confirm("Import script", "Replace the current script with this file?", replace, yes="Replace")
            else:
                replace()

        FilePicker("Import script text", chosen, exts=[".txt", ".md"], start=self.app.last_folder())

    # ------------------------------------------------------------------ settings
    def _wpm_changed(self, v):
        if self._loading:
            return
        val = float_or(v)
        if val is not None and 120 <= val <= 160:
            self.app.set_value(lambda p: setattr(p, "wpm", val))

    def _target_changed(self, v):
        if self._loading:
            return
        val = float_or(v)
        if val is not None and val > 0:
            self.app.set_value(lambda p: setattr(p, "target_seconds", val))

    def _override_changed(self, on: bool):
        if self._loading or on == self.app.project.duration_override:
            return
        if not on:
            def off(p):
                p.duration_override = False
                p.duration_override_reason = ""

            self.app.set_value(off)
            return

        def apply(reason: str):
            if not reason.strip():
                self.override.checkbox.active = False
                return

            def set_(p):
                p.duration_override = True
                p.duration_override_reason = reason.strip()

            self.app.set_value(set_)

        ask_text("Longer-duration override", "Record why this package may exceed the target (saved in the project "
                                             "and export manifest):", "", apply)
