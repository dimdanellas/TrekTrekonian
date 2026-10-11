"""Touch-sized building blocks: rows, fields, popups and file pickers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, Optional

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

from . import platform as plat

ROW = dp(46)
FONT = sp(16)
SMALL = sp(14)
RED = (1, 0.42, 0.42, 1)
GREEN = (0.45, 0.85, 0.5, 1)
AMBER = (1, 0.78, 0.35, 1)
DIM = (0.7, 0.7, 0.75, 1)
WHITE = (1, 1, 1, 1)
SCRIPT_BG = (0.11, 0.12, 0.14, 1)
FIELD_BG = (0.2, 0.21, 0.24, 1)
OVERRUN_BG = (0x4a / 255, 0x23 / 255, 0x23 / 255, 1)  # same colour as the desktop app


def button(text: str, on_press: Optional[Callable] = None, width: Optional[float] = None, **kw) -> Button:
    b = Button(text=text, size_hint_y=None, height=ROW, font_size=FONT, **kw)
    if width is not None:
        b.size_hint_x = None
        b.width = width
    if on_press is not None:
        b.bind(on_release=lambda *_: on_press())
    return b


class WrapLabel(Label):
    """Label that wraps to its width and grows to fit its text."""

    def __init__(self, **kw):
        kw.setdefault("font_size", FONT)
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "middle")
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        self.bind(width=self._wrap, texture_size=self._fit)
        self._wrap()

    def _wrap(self, *_):
        self.text_size = (max(1, self.width - dp(8)), None)

    def _fit(self, *_):
        self.height = max(dp(28), self.texture_size[1] + dp(8))


def label(text: str = "", color=WHITE, bold: bool = False, size: float = FONT, **kw) -> WrapLabel:
    return WrapLabel(text=text, color=color, bold=bold, font_size=size, **kw)


def heading(text: str) -> WrapLabel:
    return label(text, color=AMBER, bold=True, size=sp(17))


def text_input(text: str = "", on_change: Optional[Callable[[str], None]] = None, multiline: bool = False,
               hint: str = "", input_filter=None, password: bool = False, height: Optional[float] = None) -> TextInput:
    ti = TextInput(text=text, multiline=multiline, hint_text=hint, input_filter=input_filter, password=password,
                   font_size=FONT, size_hint_y=None, height=height or (ROW if not multiline else dp(120)),
                   write_tab=False, background_color=FIELD_BG, foreground_color=WHITE, cursor_color=WHITE,
                   hint_text_color=DIM, padding=(dp(8), dp(12)))
    if on_change is not None:
        ti.bind(text=lambda _w, v: on_change(v))
    return ti


def spinner(values: Iterable[str], text: str, on_select: Optional[Callable[[str], None]] = None) -> Spinner:
    sp_ = Spinner(text=text, values=list(values), size_hint_y=None, height=ROW, font_size=FONT)
    if on_select is not None:
        sp_.bind(text=lambda _w, v: on_select(v))
    return sp_


def checkbox(text: str, active: bool, on_change: Optional[Callable[[bool], None]] = None) -> BoxLayout:
    row = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(4))
    cb = CheckBox(active=active, size_hint_x=None, width=dp(44))
    row.add_widget(cb)
    lab = label(text)
    lab.size_hint_y = 1
    row.add_widget(lab)
    row.checkbox = cb  # type: ignore[attr-defined]
    if on_change is not None:
        cb.bind(active=lambda _w, v: on_change(bool(v)))
    return row


def hbox(*widgets, height: float = ROW, spacing: float = dp(6)) -> BoxLayout:
    row = BoxLayout(size_hint_y=None, height=height, spacing=spacing)
    for w in widgets:
        row.add_widget(w)
    return row


class Form(GridLayout):
    """Two-column label/field form that grows with its content."""

    def __init__(self, label_width: float = dp(190), **kw):
        super().__init__(cols=2, spacing=(dp(8), dp(6)), padding=dp(8), size_hint_y=None, **kw)
        self.label_width = label_width
        self.bind(minimum_height=self.setter("height"))

    def row(self, text: str, widget, hint: str = ""):
        lab = label(text + (f"\n[size={int(SMALL)}][color=b3b3bf]{hint}[/color][/size]" if hint else ""),
                    markup=True)
        lab.size_hint_x = None
        lab.width = self.label_width
        self.add_widget(lab)
        widget.size_hint_x = 1
        self.add_widget(widget)
        return widget

    def full(self, widget):
        """A widget spanning both columns (heading, note, button row)."""
        spacer = Label(size_hint_x=None, width=self.label_width, size_hint_y=None, height=widget.height)
        widget.bind(height=lambda _w, h: setattr(spacer, "height", h))
        self.add_widget(spacer)
        self.add_widget(widget)
        return widget


def scroll(child) -> ScrollView:
    sv = ScrollView(do_scroll_x=False, bar_width=dp(8), scroll_type=["bars", "content"])
    sv.add_widget(child)
    return sv


def vstack(spacing: float = dp(4), padding: float = dp(4)) -> GridLayout:
    g = GridLayout(cols=1, spacing=spacing, padding=padding, size_hint_y=None)
    g.bind(minimum_height=g.setter("height"))
    return g


class SelectList(BoxLayout):
    """Scrollable list of toggle rows with single selection."""

    def __init__(self, on_select: Callable[[str], None], **kw):
        super().__init__(orientation="vertical", **kw)
        self.on_select = on_select
        self.box = vstack(spacing=dp(2))
        self.add_widget(scroll(self.box))
        self.selected: Optional[str] = None
        self._buttons: dict[str, ToggleButton] = {}

    def set_items(self, items: list[tuple[str, str, Optional[tuple]]], keep: bool = True) -> None:
        """items: (key, text, colour or None)."""
        prev = self.selected if keep else None
        self.box.clear_widgets()
        self._buttons = {}
        group = f"sel{id(self)}"
        for key, text, color in items:
            b = ToggleButton(text=text, group=group, size_hint_y=None, height=ROW, font_size=FONT,
                             halign="left", valign="middle", shorten=True, shorten_from="right",
                             allow_no_selection=False)
            b.bind(width=lambda w, *_: setattr(w, "text_size", (w.width - dp(16), None)))
            if color:
                b.color = color
            b.bind(on_release=lambda w, k=key: self._pick(k))
            self._buttons[key] = b
            self.box.add_widget(b)
        if prev in self._buttons:
            self._buttons[prev].state = "down"
            self.selected = prev
        else:
            self.selected = None

    def update_item(self, key: str, text: str, color=None) -> None:
        b = self._buttons.get(key)
        if b is not None:
            b.text = text
            b.color = color or WHITE

    def select(self, key: str) -> None:
        if key in self._buttons:
            for k, b in self._buttons.items():
                b.state = "down" if k == key else "normal"
            self._pick(key)

    def _pick(self, key: str) -> None:
        self.selected = key
        self.on_select(key)


# ---------------------------------------------------------------------------
# Popups
# ---------------------------------------------------------------------------

def _popup(title: str, content, size=(0.8, 0.6), dismissable: bool = True) -> Popup:
    p = Popup(title=title, content=content, size_hint=size, auto_dismiss=dismissable, title_size=sp(18))
    p.open()
    return p


def message(title: str, text: str, size=(0.75, 0.55)) -> Popup:
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
    body = vstack()
    body.add_widget(label(text))
    box.add_widget(scroll(body))
    p = None
    box.add_widget(button("OK", lambda: p.dismiss()))
    p = _popup(title, box, size)
    return p


def confirm(title: str, text: str, on_yes: Callable[[], None], on_no: Optional[Callable[[], None]] = None,
            yes: str = "OK", no: str = "Cancel", size=(0.75, 0.5)) -> Popup:
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
    body = vstack()
    body.add_widget(label(text))
    box.add_widget(scroll(body))
    p: Optional[Popup] = None

    def act(fn):
        p.dismiss()
        if fn:
            fn()

    box.add_widget(hbox(button(no, lambda: act(on_no)), button(yes, lambda: act(on_yes))))
    p = _popup(title, box, size, dismissable=False)
    return p


def choice(title: str, text: str, options: list[tuple[str, Callable[[], None]]], size=(0.75, 0.5)) -> Popup:
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
    body = vstack()
    body.add_widget(label(text))
    box.add_widget(scroll(body))
    p: Optional[Popup] = None

    def act(fn):
        p.dismiss()
        fn()

    box.add_widget(hbox(*[button(t, (lambda f=f: act(f))) for t, f in options]))
    p = _popup(title, box, size, dismissable=False)
    return p


def ask_text(title: str, prompt: str, initial: str, on_ok: Callable[[str], None], multiline: bool = False,
             input_filter=None) -> Popup:
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
    box.add_widget(label(prompt))
    ti = text_input(initial, multiline=multiline, input_filter=input_filter,
                    height=dp(160) if multiline else ROW)
    box.add_widget(ti)
    box.add_widget(BoxLayout())
    p: Optional[Popup] = None

    def ok():
        p.dismiss()
        on_ok(ti.text)

    box.add_widget(hbox(button("Cancel", lambda: p.dismiss()), button("OK", ok)))
    p = _popup(title, box, (0.7, 0.55 if multiline else 0.4))
    ti.focus = True
    return p


class FilePicker:
    """Browse the tablet's storage for files, a folder, or a save location.

    mode: "open" (one or more files), "folder", or "save" (folder + file name).
    """

    def __init__(self, title: str, on_done: Callable[[list[str]], None], mode: str = "open",
                 exts: Optional[Iterable[str]] = None, start: str = "", multiselect: bool = False,
                 default_name: str = ""):
        self.on_done, self.mode = on_done, mode
        self.exts = {e.lower() for e in exts} if exts else None
        start = start if start and os.path.isdir(start) else plat.storage_root()
        if not os.path.isdir(start):
            start = str(Path.home())
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        places = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(4))
        places.add_widget(button("Up", self._up, width=dp(70)))
        for name, path in plat.quick_places():
            places.add_widget(button(name, lambda p=path: self._go(p)))
        root.add_widget(places)
        self.path_lbl = label("", color=DIM, size=SMALL)
        root.add_widget(self.path_lbl)
        self.fc = FileChooserListView(path=start, multiselect=multiselect, dirselect=(mode == "folder"),
                                      filters=[self._filter], show_hidden=False)
        self.fc.bind(path=lambda *_: self._show_path(), selection=lambda *_: self._sel_changed())
        root.add_widget(self.fc)
        self.name_in = None
        if mode == "save":
            self.name_in = text_input(default_name, hint="File name")
            root.add_widget(hbox(label("File name", size_hint_x=None, width=dp(110)), self.name_in))
        if mode == "folder":
            root.add_widget(hbox(button("New folder…", self._new_folder)))
        ok_text = {"open": "Open", "folder": "Use this folder", "save": "Save here"}[mode]
        root.add_widget(hbox(button("Cancel", lambda: self.popup.dismiss()), button(ok_text, self._ok)))
        self.popup = Popup(title=title, content=root, size_hint=(0.95, 0.95), title_size=sp(18))
        self._show_path()
        self.popup.open()

    def _filter(self, folder: str, name: str) -> bool:
        if os.path.isdir(os.path.join(folder, name)):
            return True
        if self.mode == "folder":
            return False
        return self.exts is None or Path(name).suffix.lower() in self.exts

    def _show_path(self):
        self.path_lbl.text = self.fc.path

    def _sel_changed(self):
        if self.mode == "save" and self.fc.selection:
            sel = self.fc.selection[0]
            if os.path.isfile(sel):
                self.name_in.text = os.path.basename(sel)

    def _go(self, path: str):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        if os.path.isdir(path):
            self.fc.path = path

    def _up(self):
        parent = str(Path(self.fc.path).parent)
        if os.access(parent, os.R_OK):
            self.fc.path = parent

    def _new_folder(self):
        def make(name: str):
            name = name.strip().replace("/", "_")
            if not name:
                return
            p = os.path.join(self.fc.path, name)
            try:
                os.makedirs(p, exist_ok=True)
                self.fc.path = p
            except OSError as exc:
                message("New folder", f"Could not create {name}: {exc.strerror or exc}")

        ask_text("New folder", "Folder name:", "", make)

    def _ok(self):
        if self.mode == "open":
            files = [f for f in self.fc.selection if os.path.isfile(f)]
            if not files:
                return
            self.popup.dismiss()
            self.on_done(files)
        elif self.mode == "folder":
            sel = [f for f in self.fc.selection if os.path.isdir(f)]
            self.popup.dismiss()
            self.on_done([sel[0] if sel else self.fc.path])
        else:
            name = (self.name_in.text or "").strip()
            if not name:
                return
            self.popup.dismiss()
            self.on_done([os.path.join(self.fc.path, name)])


def array_to_texture(arr):
    """RGB uint8 numpy array (top row first) to a Kivy texture."""
    from kivy.graphics.texture import Texture

    h, w = arr.shape[:2]
    tex = Texture.create(size=(w, h), colorfmt="rgb")
    tex.blit_buffer(arr[::-1].copy().tobytes(), colorfmt="rgb", bufferfmt="ubyte")
    return tex


def thumbnail_array(asset, t: float, max_w: int = 480, max_h: int = 270):
    """A small RGB frame of an asset at time t, or None for audio-only media."""
    import os

    import numpy as np
    from PIL import Image, ImageOps

    if asset is None or not os.path.exists(asset.path):
        return None
    if asset.meta.get("is_image"):
        with Image.open(asset.path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_w, max_h))
            return np.asarray(im.copy())
    if asset.meta.get("has_video"):
        from ..preview import grab_frame

        w, h = asset.meta.get("width") or 16, asset.meta.get("height") or 9
        s = min(max_w / w, max_h / h)
        tw, th = max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)
        return grab_frame(asset.path, t, tw, th)
    return None
