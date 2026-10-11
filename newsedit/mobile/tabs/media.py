"""Asset library: import, inspect, relink and remove media."""
from __future__ import annotations

import os
from pathlib import Path

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KImage
from kivy.uix.slider import Slider

from ... import assets as A
from ...media import MediaError
from ...util import seconds_to_tc
from ..widgets import (DIM, RED, WHITE, FilePicker, SelectList, array_to_texture, button, confirm, hbox, label,
                       message, spinner, thumbnail_array, vstack, scroll)
from .base import Tab

KIND_LABELS = {"soundbite": "Soundbite", "standup": "Stand-up", "broll": "B-roll", "image": "Image",
               "music": "Music", "sfx": "SFX", "logo": "Logo/Watermark", "narration": "Narration"}
AUTO = "Auto-detect"


def asset_row_text(a) -> str:
    dur = a.meta.get("duration")
    info = "still" if a.meta.get("is_image") else (f"{dur:.1f} s" if dur else "?")
    missing = "" if os.path.exists(a.path) else "   MISSING"
    return f"{a.name}   ·   {KIND_LABELS.get(a.kind, a.kind)}   ·   {info}{missing}"


class MediaTab(Tab):
    def __init__(self, app):
        super().__init__(app)
        top = hbox()
        top.add_widget(label("Import as", size_hint_x=None, width=dp(100)))
        self.kind = spinner([AUTO] + list(KIND_LABELS.values()), AUTO)
        self.kind.size_hint_x = None
        self.kind.width = dp(200)
        top.add_widget(self.kind)
        top.add_widget(button("Import files…", self.import_files))
        top.add_widget(button("Relink…", self.relink))
        top.add_widget(button("Find missing", self.auto_relink))
        top.add_widget(button("Remove", self.remove))
        self.add_widget(top)

        body = BoxLayout(spacing=dp(8))
        self.list = SelectList(self.show_detail, size_hint_x=0.55)
        body.add_widget(self.list)
        detail = BoxLayout(orientation="vertical", size_hint_x=0.45, spacing=dp(4))
        self.thumb = KImage(size_hint_y=0.55, fit_mode="contain")
        detail.add_widget(self.thumb)
        self.scrub = Slider(min=0, max=1, value=0, size_hint_y=None, height=dp(40))
        self.scrub.bind(on_touch_up=lambda w, t: w.collide_point(*t.pos) and self._scrubbed())
        detail.add_widget(self.scrub)
        self.play_btn = button("Play audio", self.play)
        detail.add_widget(hbox(self.play_btn, button("Stop audio", self.app.stop_audio)))
        info_box = vstack()
        self.info = label("Select an asset.", color=DIM)
        info_box.add_widget(self.info)
        detail.add_widget(scroll(info_box))
        body.add_widget(detail)
        self.add_widget(body)

    def selected(self):
        return self.app.project.asset(self.list.selected)

    def load(self) -> None:
        self.list.selected = None
        self.thumb.texture = None
        self.info.text = "Select an asset."

    def refresh(self, full: bool) -> None:
        items = [(a.id, asset_row_text(a), None if os.path.exists(a.path) else RED) for a in self.app.project.assets]
        if full or len(items) != len(self.list._buttons):
            self.list.set_items(items)
        else:
            for key, text, color in items:
                self.list.update_item(key, text, color or WHITE)

    def show_detail(self, _key=None) -> None:
        a = self.selected()
        if a is None:
            return
        m = a.meta
        lines = [a.name, f"Kind: {KIND_LABELS.get(a.kind, a.kind)}   ID: {a.id}", a.path]
        if m.get("is_image"):
            lines.append(f"Still image {m.get('width')}×{m.get('height')}")
        else:
            dur = m.get("duration")
            if dur:
                lines.append(f"Duration {dur:.3f} s ({seconds_to_tc(dur, self.app.project.output.fps)})")
            if m.get("has_video"):
                lines.append(f"Video {m.get('width')}×{m.get('height')} @ {m.get('fps', 0):.3g} fps"
                             + (f", rotated {m.get('rotation')}°" if m.get("rotation") else ""))
            lines.append("Audio: yes" if m.get("has_audio") else "Audio: none")
        for w in m.get("warnings", []):
            lines.append(f"Warning: {w}")
        if not os.path.exists(a.path):
            lines.append("The file is missing. Use Relink or Find missing.")
        self.info.text = "\n".join(lines)
        self.scrub.max = max(0.01, float(m.get("duration") or 0.01))
        self.scrub.value = 0
        self.scrub.disabled = not m.get("has_video") or m.get("is_image")
        self.play_btn.disabled = not m.get("has_audio")
        self._thumb(0.0)

    def _scrubbed(self):
        self._thumb(self.scrub.value)

    def _thumb(self, t: float) -> None:
        try:
            arr = thumbnail_array(self.selected(), t)
        except Exception as exc:  # a broken file must not break the tab
            self.thumb.texture = None
            self.app.status(f"Preview failed: {exc}")
            return
        self.thumb.texture = array_to_texture(arr) if arr is not None else None

    def play(self) -> None:
        a = self.selected()
        if a is not None:
            self.app.play_range(a.path, self.scrub.value if a.meta.get("has_video") else 0.0, None)

    # ------------------------------------------------------------------ actions
    def _kind_key(self) -> str:
        inv = {v: k for k, v in KIND_LABELS.items()}
        return inv.get(self.kind.text, "")

    def import_files(self) -> None:
        kind = self._kind_key()
        exts = A.KIND_EXT.get(kind) if kind else (A.VIDEO_EXT | A.AUDIO_EXT | A.IMAGE_EXT)
        lib = getattr(self.app.settings_cfg.paths, A.LIBRARY_FOR_KIND.get(kind, ""), "") if kind else ""
        start = lib or self.app.last_folder()
        FilePicker(f"Import {self.kind.text}", lambda files: self._import(files, kind), exts=exts, start=start,
                   multiselect=True)

    def _import(self, files: list[str], kind: str) -> None:
        added, errors = [], []
        for f in files:
            try:
                added.append(A.import_file(self.app.project, f, kind or A.guess_kind(f)))
            except MediaError as exc:
                errors.append(str(exc))
        if added:
            self.app.mark_dirty()
            self.app.refresh_all()
            self.list.select(added[-1].id)
            self.app.status(f"Imported {len(added)} file(s).")
        if errors:
            message("Import", "\n".join(errors))

    def relink(self) -> None:
        a = self.selected()
        if a is None:
            message("Relink", "Select an asset first.")
            return
        parent = str(Path(a.path).parent)

        def chosen(files):
            try:
                A.relink(a, files[0])
            except MediaError as exc:
                message("Relink", str(exc))
                return
            self.app.mark_dirty()
            self.app.refresh_all()
            self.show_detail()
            if a.meta.get("warnings"):
                message("Relink", "\n".join(a.meta["warnings"]))

        FilePicker(f"Relink {a.name}", chosen, start=parent if os.path.isdir(parent) else self.app.last_folder())

    def auto_relink(self) -> None:
        p = self.app.project
        folders = [v for v in vars(self.app.settings_cfg.paths).values() if v]
        if self.app.project_path:
            folders.append(str(self.app.project_path.parent))
        found = A.auto_relink(p, folders)
        missing = A.missing_assets(p)
        if found:
            self.app.mark_dirty()
            self.app.refresh_all()
        message("Find missing", f"Relinked {len(found)} file(s). Still missing: {len(missing)}." +
                ("\n\nRelink the rest one at a time, or copy them into the project's folder." if missing else ""))

    def remove(self) -> None:
        a = self.selected()
        if a is None:
            return
        p = self.app.project
        used = [lab for lab, b in p.bindings.items() if b.asset_id == a.id or b.card_background_asset_id == a.id]
        if p.music.asset_id == a.id:
            used.append("music")
        if p.watermark.asset_id == a.id:
            used.append("watermark")
        used += [f"SFX @ {s.at_seconds:g}s" for s in p.sfx if s.asset_id == a.id]
        if used:
            message("Remove", f"{a.name} is used by: {', '.join(used)}. Unbind it first.")
            return

        def go():
            p.assets.remove(a)
            self.list.selected = None
            self.thumb.texture = None
            self.info.text = "Select an asset."
            self.app.mark_dirty()
            self.app.refresh_all()

        confirm("Remove", f"Remove {a.name} from the project? The file itself is not deleted.", go, yes="Remove")
