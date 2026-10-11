"""Package-wide settings: title, profile, look, overlays, music, subtitles, export folder."""
from __future__ import annotations

from kivy.metrics import dp

from ...model import RESIZE_LABELS
from ...profiles import DEFAULT_PROFILES, get_profile
from ..widgets import DIM, FilePicker, Form, button, checkbox, heading, hbox, label, scroll, spinner, text_input
from .base import Tab, float_or
from .markers import asset_choices, asset_text

TRANSITIONS = {"cut": "Cut", "fade": "Fade through black"}
SB_POLICY = {"duck": "Duck under statements", "mute": "Mute under statements"}


class PackageTab(Tab):
    def __init__(self, app):
        super().__init__(app)
        self._loading = False
        self.form_holder = None
        self.v: dict = {}

    def load(self) -> None:
        self.build()

    def refresh(self, full: bool) -> None:
        if full:
            self.build()

    def build(self) -> None:
        self._loading = True
        p = self.app.project
        f = Form(label_width=dp(230))
        v = self.v = {}
        c = self._commit
        f.full(heading("Package"))
        v["title"] = f.row("Title", text_input(p.title, c))
        labels = {k: prof["label"] for k, prof in DEFAULT_PROFILES.items()}
        v["profile"] = f.row("Export profile", spinner(labels.values(), labels.get(p.output.profile, p.output.profile),
                                                       c))
        self.profile_note = f.row("", label(get_profile(p.output.profile, p.output.overrides)["notes"], color=DIM))
        v["export_dir"] = f.row("Export folder", text_input(p.export_directory, c))
        f.full(hbox(button("Choose export folder…", self.pick_export)))
        v["vo_wav"] = f.row("", checkbox("Write the voice-over WAV", p.export.voice_over_wav, c))
        v["seg_wavs"] = f.row("", checkbox("Write one WAV per narration block", p.export.segment_wavs, c))

        f.full(heading("Look"))
        v["resize"] = f.row("Default resize", spinner(RESIZE_LABELS.values(), RESIZE_LABELS[p.visual.resize_mode], c))
        v["transition"] = f.row("Transition", spinner(TRANSITIONS.values(), TRANSITIONS[p.visual.transition], c))
        v["lt_color"] = f.row("Lower-third colour", text_input(p.visual.lower_third_color, c), "#RRGGBB")

        f.full(heading("Watermark"))
        names, self.logo_map = asset_choices(p, ("logo", "image"))
        v["wm"] = f.row("Logo", spinner(names, asset_text(p, p.watermark.asset_id), c))
        v["wm_w"] = f.row("Width (share of frame)", text_input(f"{p.watermark.width_ratio:g}", c,
                                                              input_filter="float"), "0.03 – 0.4")
        v["wm_o"] = f.row("Opacity", text_input(f"{p.watermark.opacity:g}", c, input_filter="float"), "0.1 – 1")

        f.full(heading("Opening title"))
        ot = p.opening_title
        v["ot_on"] = f.row("", checkbox("Show an opening title", ot.enabled, c))
        v["ot_text"] = f.row("Title text", text_input(ot.text, c))
        v["ot_sub"] = f.row("Subtitle", text_input(ot.subtitle, c))
        v["ot_start"] = f.row("Start (s)", text_input(f"{ot.start:g}", c, input_filter="float"))
        v["ot_dur"] = f.row("Duration (s)", text_input(f"{ot.duration:g}", c, input_filter="float"))

        f.full(heading("Music"))
        names, self.music_map = asset_choices(p, ("music",))
        v["music"] = f.row("Track", spinner(names, asset_text(p, p.music.asset_id), c), "import it in Media as Music")
        v["music_db"] = f.row("Level in pauses (dB)", text_input(f"{p.music.base_db:g}", c))
        v["music_policy"] = f.row("Under statements", spinner(SB_POLICY.values(), SB_POLICY[p.music.soundbite_policy],
                                                              c))
        v["music_loop"] = f.row("", checkbox("Loop with crossfade if too short", p.music.loop, c))

        f.full(heading("Subtitles"))
        st = p.subtitles
        v["burn"] = f.row("", checkbox("Burn subtitles into the picture", st.burn_in, c))
        v["narr_caps"] = f.row("", checkbox("Caption the narration as well", st.narration_captions, c))
        v["srt"] = f.row("", checkbox("Write .srt sidecar", st.write_srt, c))
        v["ass"] = f.row("", checkbox("Write .ass sidecar", st.write_ass, c))
        self.err = label("", color=(1, 0.42, 0.42, 1))
        f.full(self.err)
        self.clear_widgets()
        self.add_widget(scroll(f))
        self._loading = False

    def pick_export(self) -> None:
        def chosen(paths):
            self.v["export_dir"].text = paths[0]

        FilePicker("Export folder", chosen, mode="folder", start=self.v["export_dir"].text or self.app.last_folder())

    def _commit(self, *_):
        if self._loading:
            return
        v = self.v
        errs = []

        def num(key, lo=None, hi=None, name=""):
            x = float_or(v[key].text)
            if x is None:
                errs.append(f"{name} must be a number.")
                return None
            if lo is not None:
                x = max(lo, x)
            if hi is not None:
                x = min(hi, x)
            return x

        def apply(p):
            p.title = v["title"].text.strip() or p.title
            inv = {prof["label"]: k for k, prof in DEFAULT_PROFILES.items()}
            key = inv.get(v["profile"].text, p.output.profile)
            if key != p.output.profile:
                prof = get_profile(key)
                p.output.profile = key
                p.output.width, p.output.height, p.output.fps = prof["width"], prof["height"], prof["fps"]
                p.output.overrides = {}
                self.profile_note.text = prof["notes"]
            p.export_directory = v["export_dir"].text.strip()
            p.export.voice_over_wav = v["vo_wav"].checkbox.active
            p.export.segment_wavs = v["seg_wavs"].checkbox.active
            p.visual.resize_mode = {lab: k for k, lab in RESIZE_LABELS.items()}.get(v["resize"].text, "blur")
            p.visual.transition = {lab: k for k, lab in TRANSITIONS.items()}.get(v["transition"].text, "cut")
            p.visual.lower_third_color = v["lt_color"].text.strip() or p.visual.lower_third_color
            p.watermark.asset_id = self.logo_map.get(v["wm"].text)
            w = num("wm_w", 0.03, 0.4, "Watermark width")
            o = num("wm_o", 0.1, 1.0, "Watermark opacity")
            if w is not None:
                p.watermark.width_ratio = w
            if o is not None:
                p.watermark.opacity = o
            ot = p.opening_title
            ot.enabled = v["ot_on"].checkbox.active
            ot.text, ot.subtitle = v["ot_text"].text, v["ot_sub"].text
            s, d = num("ot_start", 0.0, None, "Title start"), num("ot_dur", 0.5, None, "Title duration")
            if s is not None:
                ot.start = s
            if d is not None:
                ot.duration = d
            new_music = self.music_map.get(v["music"].text)
            if new_music != p.music.asset_id:
                p.music.asset_id = new_music
                p.music.auto_seed = None
            db = num("music_db", None, None, "Music level")
            if db is not None:
                p.music.base_db = round(db, 1)
            p.music.soundbite_policy = {lab: k for k, lab in SB_POLICY.items()}.get(v["music_policy"].text, "duck")
            p.music.loop = v["music_loop"].checkbox.active
            st = p.subtitles
            st.burn_in = v["burn"].checkbox.active
            st.narration_captions = v["narr_caps"].checkbox.active
            st.write_srt = v["srt"].checkbox.active
            st.write_ass = v["ass"].checkbox.active

        self.app.set_value(apply)
        self.err.text = "\n".join(errs)
