"""Validate, inspect frames, preview and render."""
from __future__ import annotations

import os
from pathlib import Path

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KImage
from kivy.uix.slider import Slider

from ...preflight import run_preflight
from ...timeline import compile_timeline
from ...util import frames_to_tc
from .. import platform as plat
from ..widgets import (DIM, array_to_texture, button, confirm, heading, hbox, label, message, scroll,
                       vstack)
from .base import Tab, fill_diagnostics

PREVIEW_BLOCKING = ("unbound", "bad_trim", "asset_missing", "asset_removed", "missing_end", "mismatched_end",
                    "nested_tag", "stray_end", "unknown_tag", "no_visual", "statement_no_audio", "font", "environment")


class RenderTab(Tab):
    def __init__(self, app):
        super().__init__(app, orientation="horizontal", spacing=dp(8))
        self.proxy_path = ""
        self.last_files: list[Path] = []

        left = BoxLayout(orientation="vertical", size_hint_x=0.55, spacing=dp(4))
        left.add_widget(hbox(button("Validate", self.validate), button("Render package", self.render)))
        left.add_widget(hbox(button("Build preview", self.build_preview), button("Play preview", self.play_preview)))
        self.frame_img = KImage(fit_mode="contain")
        left.add_widget(self.frame_img)
        self.slider = Slider(min=0, max=1, step=1, value=0, size_hint_y=None, height=dp(46))
        self.slider.bind(value=lambda *_: self._update_tc())
        left.add_widget(self.slider)
        self.tc = label("", color=DIM)
        left.add_widget(self.tc)
        left.add_widget(hbox(button("Prev segment", lambda: self._jump(-1)), button("Show frame", self.inspect_frame),
                             button("Next segment", lambda: self._jump(1)), button("Full-res", lambda: self.inspect_frame(True))))
        self.add_widget(left)

        right = vstack(spacing=dp(4))
        right.add_widget(heading("Last render"))
        self.out_box = vstack(spacing=dp(2))
        self.out_box.add_widget(label("Nothing rendered in this session.", color=DIM))
        right.add_widget(self.out_box)
        right.add_widget(heading("Diagnostics"))
        self.diag_box = vstack(spacing=dp(2))
        right.add_widget(self.diag_box)
        sv = scroll(right)
        sv.size_hint_x = 0.45
        self.add_widget(sv)

    # ------------------------------------------------------------------
    def load(self) -> None:
        self.proxy_path = ""
        self.frame_img.texture = None

    def refresh(self, full: bool) -> None:
        tl = self.app.timeline
        self.slider.max = max(1, tl.total_frames - 1)
        if self.slider.value > self.slider.max:
            self.slider.value = 0
        self._update_tc()
        fill_diagnostics(self.diag_box, self.app.diagnostics, self.app.navigate_to)

    def _update_tc(self):
        tl = self.app.timeline
        if tl is None:
            return
        f = int(self.slider.value)
        seg = tl.segment_at_frame(f)
        self.tc.text = (f"Frame {f} · {frames_to_tc(f, tl.fps)} / {frames_to_tc(tl.total_frames, tl.fps)}"
                        + (f" · {seg.name()}" if seg else ""))

    def _jump(self, step: int) -> None:
        tl = self.app.timeline
        f = int(self.slider.value)
        starts = sorted({s.start_frame for s in tl.segments})
        if step > 0:
            nxt = [s for s in starts if s > f]
            self.slider.value = nxt[0] if nxt else self.slider.value
        else:
            prv = [s for s in starts if s < f]
            self.slider.value = prv[-1] if prv else 0

    def _blocking(self) -> list:
        return [d for d in self.app.diagnostics if d.is_error and d.code in PREVIEW_BLOCKING]

    # ------------------------------------------------------------------ frames
    def inspect_frame(self, full_res: bool = False) -> None:
        from ...profiles import get_profile, proxy_profile
        from ...render import FrameRenderer
        from ...subtitles import build_timeline_cues, place_cues

        app = self.app
        app.recompute()
        tl = app.timeline
        if not tl.segments or tl.total_frames == 0:
            message("Frame", "Nothing to show yet.")
            return
        blocking = self._blocking()
        if blocking:
            message("Frame", "Fix these first:\n" + "\n".join(f"• {d.message}" for d in blocking[:8]))
            return
        frame = min(tl.total_frames - 1, int(self.slider.value))
        snap = app.project.snapshot()
        prof = get_profile(snap.output.profile, snap.output.overrides)
        if not full_res:
            prof = proxy_profile(prof, 960)

        def work(cancel, progress):
            stl = compile_timeline(snap)
            cues, _ = build_timeline_cues(snap, stl)
            place_cues(snap, stl, cues, prof["width"], prof["height"])
            r = FrameRenderer(snap, stl, prof["width"], prof["height"], cues)
            try:
                return r.frame(frame)
            finally:
                r.close()

        def done(img):
            self.frame_img.texture = array_to_texture(img)
            app.status(f"Frame {frame} rendered at {img.shape[1]}×{img.shape[0]}.")

        app.start_job("inspect", "Rendering frame", work, done)

    # ------------------------------------------------------------------ preview
    def build_preview(self) -> None:
        from ...preview import build_proxy

        app = self.app
        app.recompute()
        tl = app.timeline
        if not tl.segments or tl.total_frames == 0:
            message("Preview", "Nothing to preview yet.")
            return
        blocking = self._blocking()
        if blocking:
            message("Preview", "Fix these first:\n" + "\n".join(f"• {d.message}" for d in blocking[:8]))
            return
        snap = app.project.snapshot()
        snap_tl = compile_timeline(snap)
        cache = app.cache_dir() / "preview"

        def done(info):
            self.proxy_path = info["path"]
            app.status("Preview ready. Tap Play preview.")
            self.play_preview()

        app.start_job("preview", "Building preview",
                      lambda cancel, progress: build_proxy(snap, snap_tl, cache, cancel, progress), on_done=done)

    def play_preview(self) -> None:
        if not self.proxy_path or not os.path.exists(self.proxy_path):
            message("Preview", "Build a preview first.")
            return
        self._open(self.proxy_path)

    def _open(self, path: str) -> None:
        try:
            plat.open_with_system(path)
        except Exception as exc:
            message("Open", f"No app could open {Path(path).name}: {exc}")

    # ------------------------------------------------------------------ delivery
    def validate(self) -> None:
        app = self.app
        app.recompute()
        snap = app.project.snapshot()

        def work(cancel, progress):
            progress("Validating (including encoder test)", None)
            return run_preflight(snap, for_render=True, test_encoder=True,
                                 custom_profiles=app.settings_cfg.custom_profiles)[1]

        def done(diags):
            fill_diagnostics(self.diag_box, diags, app.navigate_to)
            errs = [d for d in diags if d.is_error]
            if errs:
                message("Validate", f"{len(errs)} blocking error(s). Tap a diagnostic to go to it.")
            else:
                warns = sum(1 for d in diags if d.severity == "warning")
                message("Validate", f"Ready to render. {warns} warning(s) to review.")

        app.start_job("validate", "Validating", work, done)

    def render(self) -> None:
        from ...export import export_package, timeline_hash

        app = self.app
        app.recompute()
        if not app.project.export_directory:
            message("Render", "Choose an export folder in the Package tab first.")
            return
        tl, diags = run_preflight(app.project, for_render=True, custom_profiles=app.settings_cfg.custom_profiles)
        fill_diagnostics(self.diag_box, diags, app.navigate_to)
        errs = [d for d in diags if d.is_error]
        if errs:
            message("Render blocked", f"{len(errs)} blocking error(s):\n" +
                    "\n".join(f"• {d.message}" for d in errs[:10]))
            return
        warns = [d for d in diags if d.severity == "warning"]
        snap = app.project.snapshot()  # changes during rendering apply to the next job
        th = timeline_hash(tl)

        def done(res):
            app.project.last_render = {"frames": int(round(res.duration * tl.fps)), "fps": tl.fps,
                                       "filename": res.video.name, "timeline_hash": th,
                                       "profile": snap.output.profile}
            app.project.acknowledged_warnings = [d.message for d in res.diagnostics if d.severity == "warning"]
            app.mark_dirty()
            for f in res.files:
                plat.scan_media_file(str(f))
            self._show_outputs(res)
            v = res.verification
            message("Render complete",
                    f"Verified {res.video.name}\n{v.get('width')}×{v.get('height')} {v.get('video_codec')} "
                    f"@ {v.get('frame_rate')}, {v.get('frames')} frames ({res.duration:.2f} s), audio "
                    f"{v.get('audio_codec')} {v.get('audio_sample_rate')} Hz.\n\nSaved in {res.video.parent}")

        def go():
            app.start_job("render", "Rendering", lambda cancel, progress: export_package(
                snap, cancel, progress, app.settings_cfg.custom_profiles), done)

        if warns:
            confirm("Render", f"{len(warns)} warning(s) will be recorded as acknowledged in the export manifest:\n" +
                    "\n".join(f"• {d.message}" for d in warns[:8]) + "\n\nRender now? Keep the app open while it "
                                                                     "renders.", go, yes="Render")
        else:
            go()

    def _show_outputs(self, res) -> None:
        self.out_box.clear_widgets()
        for f in res.files:
            self.out_box.add_widget(button(f"Open {f.name}", lambda p=str(f): self._open(p)))

