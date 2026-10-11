"""Bind each script marker to media: trims, framing, lower thirds, cards, subtitles."""
from __future__ import annotations

from typing import Optional

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KImage
from kivy.uix.slider import Slider

from ... import assets as A
from ...model import FILL_LABELS, RESIZE_LABELS, Binding, Cue, LowerThird, SubtitleTrack
from ...subtitles import validate_track
from ...timeline import binding_trim, trim_error
from ...util import parse_time, seconds_to_tc
from ..widgets import (DIM, GREEN, RED, WHITE, Form, SelectList, array_to_texture, button, checkbox, confirm, heading,
                       hbox, label, scroll, spinner, text_input, thumbnail_array)
from .base import Tab, float_or
from .media import KIND_LABELS

NONE = "— none —"
DEFAULT = "Project default"


def time_text(seconds: float, fps: int) -> str:
    """Timecode when frame-aligned, otherwise exact seconds (never silently re-quantised)."""
    if abs(seconds * fps - round(seconds * fps)) < 1e-6:
        return seconds_to_tc(seconds, fps)
    return f"{seconds:.3f}"


def asset_choices(project, kinds) -> tuple[list[str], dict[str, Optional[str]]]:
    names = [NONE]
    mapping: dict[str, Optional[str]] = {NONE: None}
    for a in project.assets:
        if not kinds or a.kind in kinds:
            text = f"{a.name}  [{a.id}]"
            names.append(text)
            mapping[text] = a.id
    return names, mapping


def asset_text(project, asset_id) -> str:
    a = project.asset(asset_id)
    return f"{a.name}  [{a.id}]" if a else NONE


def cues_to_text(cues: list[Cue]) -> str:
    """Editable form: one block per cue, 'start - end' then its text, blank line between."""
    return "\n\n".join(f"{c.start:.2f} - {c.end:.2f}\n{c.text}" for c in cues)


def text_to_cues(text: str) -> list[Cue]:
    cues = []
    for n, block in enumerate([b for b in text.replace("\r", "").split("\n\n") if b.strip()], 1):
        rows = block.strip().split("\n")
        head = rows[0].replace("–", "-").replace("-->", "-")
        if "-" not in head:
            raise ValueError(f"Cue {n}: the first line must be 'start - end' in seconds (e.g. 0.00 - 2.50).")
        s_txt, e_txt = head.split("-", 1)
        try:
            s, e = parse_time(s_txt), parse_time(e_txt)
        except ValueError as exc:
            raise ValueError(f"Cue {n}: {exc}") from exc
        body = "\n".join(r.strip() for r in rows[1:]).strip()
        if not body:
            raise ValueError(f"Cue {n} has no text.")
        cues.append(Cue(round(s, 3), round(e, 3), body))
    return sorted(cues, key=lambda c: c.start)


class MarkersTab(Tab):
    def __init__(self, app):
        super().__init__(app, orientation="horizontal", spacing=dp(8))
        left = BoxLayout(orientation="vertical", size_hint_x=0.32, spacing=dp(4))
        left.add_widget(heading("Script markers"))
        self.list = SelectList(self.build_form)
        left.add_widget(self.list)
        self.add_widget(left)
        self.form_host = BoxLayout(orientation="vertical", size_hint_x=0.68)
        self.add_widget(self.form_host)
        self.current: Optional[str] = None
        self._building = False
        self._labels: list[str] = []
        self._placeholder()

    def _placeholder(self):
        self.form_host.clear_widgets()
        self.form_host.add_widget(label("Select a marker on the left. Markers come from the script "
                                        "([BROLL_1], [SOUNDBITE_1], …).", color=DIM))

    # ------------------------------------------------------------------ list
    def _status(self, label_: str) -> tuple[str, bool]:
        p = self.app.project
        b = p.bindings.get(label_)
        if label_.startswith("CARD_"):
            return ("card: " + (b.card_title[:18] if b and b.card_title else "(empty)")), not (b and b.card_title)
        if b is None or p.asset(b.asset_id) is None:
            return "unbound", True
        a = p.asset(b.asset_id)
        if a.meta.get("is_image"):
            return "still", False
        tin, tout, src = binding_trim(p, b)
        err = trim_error(tin, tout, src)
        return (f"{tout - tin:.2f} s" if not err else "bad trim"), bool(err)

    def load(self) -> None:
        self.current = None
        self._labels = []
        self._placeholder()

    def refresh(self, full: bool) -> None:
        labels = list(self.app.parse.labels) if self.app.parse else []
        items = []
        for lab in labels:
            st, bad = self._status(lab)
            items.append((lab, f"{lab}   ·   {st}", RED if bad else None))
        if labels != self._labels or full:
            self._labels = labels
            self.list.set_items(items)
            if self.current and self.current not in labels:
                self.current = None
                self._placeholder()
            elif self.current and full:
                self.build_form(self.current)
        else:
            for key, text, color in items:
                self.list.update_item(key, text, color or WHITE)
        self._refresh_values()

    def select(self, label_: str) -> None:
        self.list.select(label_)

    # ------------------------------------------------------------------ form
    def build_form(self, label_: str) -> None:
        self._building = True
        self.current = label_
        p = self.app.project
        b = p.bindings.get(label_) or Binding(label_)
        kind = label_.split("_")[0]
        form = Form(label_width=dp(200))
        self.v: dict = {}
        form.full(heading(label_))
        if kind == "CARD":
            self.v["card_title"] = form.row("Title", text_input(b.card_title, self._c))
            self.v["card_body"] = form.row("Body / data", text_input(b.card_body, self._c, multiline=True),
                                           "manually supplied")
            self.v["card_seconds"] = form.row("Seconds", text_input(f"{b.card_seconds:g}", self._c,
                                                                    input_filter="float"), "when it stands alone")
            self.v["card_color"] = form.row("Background colour", text_input(b.card_color, self._c), "#RRGGBB")
            names, self.bg_map = asset_choices(p, ("image",))
            self.v["card_bg"] = form.row("Background image", spinner(names, asset_text(p, b.card_background_asset_id),
                                                                     self._c))
        else:
            kinds = A.kind_for_label(label_)
            names, self.asset_map = asset_choices(p, kinds)
            self.v["asset"] = form.row("Media", spinner(names, asset_text(p, b.asset_id), self._c),
                                       "/".join(KIND_LABELS[k] for k in kinds))
            self.src_lbl = form.row("Source", label("", color=DIM))
            fps = p.output.fps
            self.v["in"] = form.row("In (source)", text_input(time_text(b.in_seconds, fps), self._c),
                                    "HH:MM:SS:FF or seconds")
            self.v["out"] = form.row("Out (source)", text_input(
                "" if b.out_seconds is None else time_text(b.out_seconds, fps), self._c), "blank = end of source")
            self.dur_lbl = form.row("Selected duration", label("", bold=True))
            self.thumb = KImage(size_hint_y=None, height=dp(200), fit_mode="contain")
            form.full(self.thumb)
            row = hbox(button("Show in", lambda: self._show("in")), button("Show out", lambda: self._show("out")))
            if kind in ("SOUNDBITE", "STANDUP"):
                row.add_widget(button("Play range", self._play_range))
                row.add_widget(button("Stop", self.app.stop_audio))
            form.full(row)
            self.v["resize"] = form.row("Resize", spinner([DEFAULT] + list(RESIZE_LABELS.values()),
                                                          RESIZE_LABELS.get(b.resize_mode, DEFAULT)
                                                          if b.resize_mode else DEFAULT, self._c))
            self.v["crop_x"] = form.row("Crop framing X", self._slider(b.crop_x), "-1 left, +1 right")
            self.v["crop_y"] = form.row("Crop framing Y", self._slider(b.crop_y), "-1 top, +1 bottom")
            if kind in ("BROLL", "IMAGE"):
                self.v["fill"] = form.row("If narration outlasts it", spinner(list(FILL_LABELS.values()),
                                                                              FILL_LABELS[b.fill_policy], self._c))
                self.v["amb_on"] = form.row("B-roll audio", checkbox("Use source ambience", b.ambience_db is not None,
                                                                     self._c))
                self.v["amb_db"] = form.row("Ambience level (dB)", text_input(
                    f"{b.ambience_db if b.ambience_db is not None else -18:g}", self._c), "muted by default")
            if kind in ("SOUNDBITE", "STANDUP"):
                self.v["gain"] = form.row("Speech gain (dB)", text_input(f"{b.gain_db:g}", self._c))
                self.v["silent"] = form.row("", checkbox("Deliberately silent statement", b.silent, self._c))
                self.v["lang"] = form.row("Speech language", spinner(["el", "en", "fr", "de", "it", "es", "tr"],
                                                                     b.language, self._c))
                names, self.bg_map = asset_choices(p, ("image",))
                self.v["card_bg"] = form.row("Still (audio-only source)",
                                             spinner(names, asset_text(p, b.card_background_asset_id), self._c))
                lt = p.lower_third_for(label_) or LowerThird(label_)
                form.full(heading("Lower third"))
                self.v["lt_name"] = form.row("Name", text_input(lt.name, self._c))
                self.v["lt_role"] = form.row("Role", text_input(lt.role, self._c))
                self.v["lt_start"] = form.row("Show from (s)", text_input(f"{lt.start_offset:g}", self._c),
                                              "after statement start")
                self.v["lt_end"] = form.row("Hide at (s)", text_input(
                    "" if lt.end_offset is None else f"{lt.end_offset:g}", self._c), "blank = statement end")
                self.v["lt_fade"] = form.row("Fade (s)", text_input(f"{lt.fade_seconds:g}", self._c))
                self._subtitle_section(form, label_)
        self.err_lbl = label("", color=RED)
        form.full(self.err_lbl)
        form.full(hbox(button("Unbind / reset", self.unbind_marker)))
        self.form_host.clear_widgets()
        self.form_host.add_widget(scroll(form))
        self._building = False
        self._refresh_values()
        if "asset" in self.v:
            self._show("in")

    def _slider(self, value: float) -> Slider:
        s = Slider(min=-1, max=1, value=value, size_hint_y=None, height=dp(46))
        s.bind(value=self._c)
        return s

    def _subtitle_section(self, form: Form, label_: str) -> None:
        p = self.app.project
        tr = p.subtitle_tracks.get(label_)
        form.full(heading("Subtitles for this statement"))
        form.full(label("One cue per block: first line 'start - end' in seconds from the trimmed clip start, then "
                        "up to two lines of text. Leave a blank line between cues. (Whisper transcription is "
                        "available in the desktop app.)", color=DIM))
        self.sub_text = text_input(cues_to_text(tr.cues) if tr else "", multiline=True, height=dp(220))
        form.full(self.sub_text)
        self.sub_reviewed = checkbox("Reviewed", bool(tr and tr.reviewed))
        form.full(hbox(button("Apply cues", self._apply_cues), self.sub_reviewed))
        self.sub_diag = label("", color=DIM)
        form.full(self.sub_diag)
        self.sub_reviewed.checkbox.bind(active=lambda _w, v: self._set_reviewed(bool(v)))
        self._show_cue_diags()

    def _track(self, create: bool = True) -> Optional[SubtitleTrack]:
        p = self.app.project
        tr = p.subtitle_tracks.get(self.current)
        if tr is None and create:
            b = p.bindings.get(self.current)
            tin, tout, _ = binding_trim(p, b)
            tr = SubtitleTrack(self.current, language=b.language if b else "el", source="manual",
                               clip_in=tin or 0.0, clip_out=tout or 0.0)
            p.subtitle_tracks[self.current] = tr
        return tr

    def _clip_len(self) -> float:
        p = self.app.project
        tin, tout, _ = binding_trim(p, p.bindings.get(self.current))
        return (tout - tin) if tin is not None and tout is not None else 0.0

    def _apply_cues(self) -> None:
        try:
            cues = text_to_cues(self.sub_text.text)
        except ValueError as exc:
            self.sub_diag.text = str(exc)
            self.sub_diag.color = RED
            return
        tr = self._track()
        tr.cues = cues
        tr.source = tr.source or "manual"
        tr.reviewed = False
        self._building = True
        self.sub_reviewed.checkbox.active = False
        self._building = False
        self.app.mark_dirty()
        self.app.schedule_refresh()
        self._show_cue_diags()

    def _set_reviewed(self, on: bool) -> None:
        if self._building or not self.current:
            return
        tr = self._track()
        tr.reviewed = on
        self.app.mark_dirty()
        self.app.schedule_refresh()
        self._show_cue_diags()

    def _show_cue_diags(self) -> None:
        tr = self._track(create=False)
        if tr is None or not tr.cues:
            self.sub_diag.text = "No cues yet."
            self.sub_diag.color = DIM
            return
        diags = validate_track(tr, self._clip_len(), self.app.project.subtitles, tr.label)
        if diags:
            self.sub_diag.text = "\n".join(f"{d.severity.upper()}: {d.message}" for d in diags)
            self.sub_diag.color = RED if any(d.is_error for d in diags) else DIM
        else:
            self.sub_diag.text = f"{len(tr.cues)} cue(s), no problems."
            self.sub_diag.color = GREEN

    # ------------------------------------------------------------------ commit
    def _c(self, *_):
        self.commit()

    def commit(self) -> None:
        if self._building or not self.current:
            return
        p = self.app.project
        label_ = self.current
        b = p.binding(label_)
        v = self.v
        errs = []
        fps = p.output.fps
        if "card_title" in v:
            b.card_title = v["card_title"].text
            b.card_body = v["card_body"].text
            s = float_or(v["card_seconds"].text)
            if s is None or s <= 0:
                errs.append("Card seconds must be a positive number.")
            else:
                b.card_seconds = s
            b.card_color = v["card_color"].text.strip() or "#102040"
            b.card_background_asset_id = self.bg_map.get(v["card_bg"].text)
        if "asset" in v:
            new_id = self.asset_map.get(v["asset"].text)
            if new_id != b.asset_id:
                b.asset_id = new_id
                a = p.asset(new_id)
                if a is not None and b.out_seconds is not None and a.meta.get("duration") \
                        and b.out_seconds > a.meta["duration"]:
                    errs.append("Out-point is beyond the new source; it was kept for you to correct.")
            try:
                b.in_seconds = parse_time(v["in"].text or "0", fps)
            except ValueError as exc:
                errs.append(f"In: {exc}")
            out_txt = v["out"].text.strip()
            try:
                b.out_seconds = parse_time(out_txt, fps) if out_txt else None
            except ValueError as exc:
                errs.append(f"Out: {exc}")
            inv = {lab: k for k, lab in RESIZE_LABELS.items()}
            b.resize_mode = inv.get(v["resize"].text)
            b.crop_x = round(float(v["crop_x"].value), 3)
            b.crop_y = round(float(v["crop_y"].value), 3)
        if "fill" in v:
            inv = {lab: k for k, lab in FILL_LABELS.items()}
            b.fill_policy = inv.get(v["fill"].text, "none")
            if v["amb_on"].checkbox.active:
                db = float_or(v["amb_db"].text)
                if db is None:
                    errs.append("Ambience level must be a number in dB.")
                else:
                    b.ambience_db = db
            else:
                b.ambience_db = None
        if "gain" in v:
            g = float_or(v["gain"].text)
            if g is None:
                errs.append("Speech gain must be a number in dB.")
            else:
                b.gain_db = g
            b.silent = bool(v["silent"].checkbox.active)
            b.language = v["lang"].text.strip() or "el"
            b.card_background_asset_id = self.bg_map.get(v["card_bg"].text)
            lt = p.lower_third_for(label_)
            name, role = v["lt_name"].text, v["lt_role"].text
            if lt is None and (name or role):
                lt = LowerThird(label_)
                p.lower_thirds.append(lt)
            if lt is not None:
                lt.name, lt.role = name, role
                start, fade = float_or(v["lt_start"].text or "0"), float_or(v["lt_fade"].text or "0")
                end_txt = v["lt_end"].text.strip()
                end = float_or(end_txt) if end_txt else None
                if start is None or fade is None or (end_txt and end is None):
                    errs.append("Lower-third times must be numbers in seconds.")
                else:
                    lt.start_offset, lt.end_offset, lt.fade_seconds = start, end, fade
        self.err_lbl.text = "\n".join(errs)
        self.app.mark_dirty()
        self.app.schedule_refresh()
        self._refresh_values()

    def _refresh_values(self) -> None:
        if not self.current or "asset" not in getattr(self, "v", {}):
            return
        p = self.app.project
        b = p.bindings.get(self.current)
        a = p.asset(b.asset_id) if b else None
        if a is None:
            self.src_lbl.text = "(no media)"
            self.dur_lbl.text = "—"
            self.dur_lbl.color = WHITE
            return
        src = a.meta.get("duration")
        self.src_lbl.text = ("still image" if a.meta.get("is_image") else
                             f"{src:.3f} s  ({seconds_to_tc(src, p.output.fps)})" if src else "unknown duration")
        if a.meta.get("is_image"):
            self.dur_lbl.text = "still (follows narration)"
            self.dur_lbl.color = WHITE
            return
        tin, tout, srcd = binding_trim(p, b)
        err = trim_error(tin, tout, srcd)
        self.dur_lbl.text = err if err else f"{tout - tin:.3f} s  ({seconds_to_tc(tout - tin, p.output.fps)})"
        self.dur_lbl.color = RED if err else WHITE

    def _show(self, which: str) -> None:
        p = self.app.project
        b = p.bindings.get(self.current or "")
        a = p.asset(b.asset_id) if b else None
        if a is None:
            self.thumb.texture = None
            return
        t = b.in_seconds if which == "in" else (b.out_seconds if b.out_seconds is not None else
                                                (a.meta.get("duration") or 0) - 0.04)
        try:
            arr = thumbnail_array(a, max(0.0, t))
        except Exception as exc:
            self.app.status(f"Preview failed: {exc}")
            return
        self.thumb.texture = array_to_texture(arr) if arr is not None else None

    def _play_range(self) -> None:
        p = self.app.project
        b = p.bindings.get(self.current or "")
        a = p.asset(b.asset_id) if b else None
        if a is None:
            return
        tin, tout, _ = binding_trim(p, b)
        if tin is not None and tout is not None and tout > tin:
            self.app.play_range(a.path, tin, tout - tin)

    def unbind_marker(self) -> None:
        if not self.current:
            return
        label_ = self.current

        def go():
            p = self.app.project
            p.bindings.pop(label_, None)
            p.lower_thirds = [lt for lt in p.lower_thirds if lt.binding_label != label_]
            self.app.mark_dirty()
            self.app.refresh_all()
            self.build_form(label_)

        confirm("Unbind", f"Remove the media binding and settings of {label_}?", go, yes="Unbind")
