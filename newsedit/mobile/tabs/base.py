"""Shared tab base class and helpers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from kivy.uix.boxlayout import BoxLayout

from ...parser import Diagnostic
from ..widgets import AMBER, RED, WHITE, ROW, vstack

if TYPE_CHECKING:
    from ..app import NewsEditorApp


class Tab(BoxLayout):
    def __init__(self, app: "NewsEditorApp", **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("spacing", 4)
        kw.setdefault("padding", 4)
        super().__init__(**kw)
        self.app = app

    def load(self) -> None:
        """Project replaced: rebuild everything from it."""

    def refresh(self, full: bool) -> None:
        """Project or timeline changed."""


def diag_color(d: Diagnostic):
    return RED if d.is_error else (AMBER if d.severity == "warning" else WHITE)


def fill_diagnostics(box, diags: list[Diagnostic], on_tap: Callable[[Diagnostic], None],
                     limit: int = 60, empty: str = "No problems found.") -> None:
    from ..widgets import button, label

    box.clear_widgets()
    if not diags:
        box.add_widget(label(empty, color=(0.45, 0.85, 0.5, 1)))
        return
    order = {"error": 0, "warning": 1}
    for d in sorted(diags, key=lambda d: order.get(d.severity, 2))[:limit]:
        b = button(f"{d.severity.upper()}: {d.message}", lambda d=d: on_tap(d))
        b.color = diag_color(d)
        b.halign = "left"
        b.valign = "middle"
        b.background_color = (0.2, 0.2, 0.22, 1)
        b.bind(width=lambda w, *_: setattr(w, "text_size", (w.width - 16, None)),
               texture_size=lambda w, ts: setattr(w, "height", max(ROW, ts[1] + 12)))
        box.add_widget(b)


def float_or(text: str, default: Optional[float] = None) -> Optional[float]:
    try:
        return float((text or "").strip().replace(",", "."))
    except ValueError:
        return default


__all__ = ["Tab", "diag_color", "fill_diagnostics", "float_or", "vstack"]
