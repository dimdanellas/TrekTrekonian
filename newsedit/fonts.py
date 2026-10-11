"""Locate a sans-serif font with Greek coverage (Arial preferred)."""
from __future__ import annotations

import functools
import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

from PIL import ImageFont

_CANDIDATES = [
    # Arial first, per the subtitle specification
    "arial.ttf", "Arial.ttf", "Arial Unicode.ttf",
    # Metric-compatible / broad-coverage substitutes
    "LiberationSans-Regular.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf", "FreeSans.ttf",
    "Helvetica.ttc", "segoeui.ttf", "tahoma.ttf",
    # Android system fonts
    "Roboto-Regular.ttf", "NotoSans-Regular.ttc",
]
_BOLD = {
    "arial.ttf": "arialbd.ttf", "Arial.ttf": "Arial Bold.ttf",
    "LiberationSans-Regular.ttf": "LiberationSans-Bold.ttf", "DejaVuSans.ttf": "DejaVuSans-Bold.ttf",
    "NotoSans-Regular.ttf": "NotoSans-Bold.ttf", "FreeSans.ttf": "FreeSansBold.ttf",
    "segoeui.ttf": "segoeuib.ttf", "tahoma.ttf": "tahomabd.ttf",
    "Roboto-Regular.ttf": "Roboto-Bold.ttf",
}


def _font_dirs() -> list[Path]:
    dirs = [Path(__file__).parent / "assets" / "fonts"]
    if sys.platform.startswith("win"):
        dirs.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                 Path.home() / "Library/Fonts"]
    elif "ANDROID_ARGUMENT" in os.environ or "ANDROID_ROOT" in os.environ:
        dirs.append(Path("/system/fonts"))
        # Kivy bundles Roboto (Greek coverage, with a bold weight) inside the APK
        spec = importlib.util.find_spec("kivy")
        if spec and spec.origin:
            dirs.append(Path(spec.origin).parent / "data" / "fonts")
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.exists()]


@functools.lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for d in _font_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                idx.setdefault(f, os.path.join(root, f))
    return idx


def has_greek(path: str) -> bool:
    try:
        font = ImageFont.truetype(path, 40)
    except OSError:
        return False
    try:
        # A font without Greek renders .notdef boxes that are identical for different letters.
        a = font.getmask("Ω").getbbox()
        b = font.getmask("λ").getbbox()
        c = font.getmask("\uffff").getbbox()
        return bool(a) and bool(b) and (a != c or b != c)
    except Exception:
        return False


@functools.lru_cache(maxsize=4)
def find_font(preferred: Optional[str] = None) -> Optional[str]:
    if preferred and os.path.exists(preferred) and has_greek(preferred):
        return preferred
    idx = _index()
    for name in _CANDIDATES:
        p = idx.get(name)
        if p and has_greek(p):
            return p
    return None


def bold_variant(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    name = os.path.basename(path)
    b = _BOLD.get(name)
    if b:
        cand = os.path.join(os.path.dirname(path), b)
        if os.path.exists(cand):
            return cand
        p = _index().get(b)
        if p:
            return p
    return path


@functools.lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(6, int(size)))
