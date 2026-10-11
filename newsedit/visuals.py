"""Resize policies, still frames, title/data cards, lower thirds, watermark
and subtitle bitmaps.  Every overlay records its real bounding box so that
caption placement can avoid it.
"""
from __future__ import annotations

import functools
import textwrap
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .fonts import bold_variant, find_font, load_font


@dataclass
class Overlay:
    rgba: np.ndarray  # h x w x 4 uint8
    x: int
    y: int

    @property
    def w(self) -> int:
        return self.rgba.shape[1]

    @property
    def h(self) -> int:
        return self.rgba.shape[0]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.w, self.y + self.h


def hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) != 6:
        return (16, 32, 64)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Resize policies
# ---------------------------------------------------------------------------

def _to_image(frame) -> Image.Image:
    if isinstance(frame, Image.Image):
        return frame.convert("RGB")
    return Image.fromarray(np.asarray(frame, dtype=np.uint8)[..., :3])


def fit_frame(frame, width: int, height: int, mode: str = "blur", crop_x: float = 0.0, crop_y: float = 0.0,
              fast: bool = False) -> np.ndarray:
    """Apply Center Crop / Fit with Blur Background / Stretch to one frame."""
    img = _to_image(frame)
    w, h = img.size
    resample = Image.BILINEAR if fast else Image.LANCZOS
    if (w, h) == (width, height):
        return np.asarray(img)
    if mode == "stretch":
        return np.asarray(img.resize((width, height), resample))
    if mode == "crop":
        scale = max(width / w, height / h)
        nw, nh = max(width, round(w * scale)), max(height, round(h * scale))
        big = img.resize((nw, nh), resample)
        ex, ey = nw - width, nh - height
        left = int(round(ex / 2 * (1 + max(-1.0, min(1.0, crop_x)))))
        top = int(round(ey / 2 * (1 + max(-1.0, min(1.0, crop_y)))))
        return np.asarray(big.crop((left, top, left + width, top + height)))
    # blur: full foreground over a scaled-to-fill Gaussian-blurred duplicate
    small_w, small_h = max(16, width // 8), max(16, height // 8)
    scale_bg = max(small_w / w, small_h / h)
    bg = img.resize((max(small_w, round(w * scale_bg)), max(small_h, round(h * scale_bg))), Image.BILINEAR)
    bw, bh = bg.size
    bg = bg.crop(((bw - small_w) // 2, (bh - small_h) // 2, (bw - small_w) // 2 + small_w, (bh - small_h) // 2 + small_h))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(2, small_w // 60)))
    bg = bg.resize((width, height), Image.BILINEAR)
    bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.25)
    scale = min(width / w, height / h)
    fw, fh = max(1, round(w * scale)), max(1, round(h * scale))
    fg = img.resize((fw, fh), resample)
    bg.paste(fg, ((width - fw) // 2, (height - fh) // 2))
    return np.asarray(bg)


@functools.lru_cache(maxsize=32)
def load_still(path: str, width: int, height: int, mode: str, crop_x: float = 0.0, crop_y: float = 0.0) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)  # respect EXIF orientation
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            base = Image.new("RGB", im.size, (0, 0, 0))
            base.paste(im, mask=im.split()[-1])
            im = base
        arr = fit_frame(im.convert("RGB"), width, height, mode, crop_x, crop_y)
    arr.setflags(write=False)
    return arr


def black(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Compositing
# ---------------------------------------------------------------------------

def composite(frame: np.ndarray, ov: Overlay, alpha: float = 1.0) -> None:
    """Alpha-blend overlay onto frame in place (clipped to frame bounds)."""
    H, W = frame.shape[:2]
    x0, y0 = max(0, ov.x), max(0, ov.y)
    x1, y1 = min(W, ov.x + ov.w), min(H, ov.y + ov.h)
    if x1 <= x0 or y1 <= y0 or alpha <= 0:
        return
    src = ov.rgba[y0 - ov.y:y1 - ov.y, x0 - ov.x:x1 - ov.x]
    a = src[..., 3:4].astype(np.float32) * (alpha / 255.0)
    dst = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (dst * (1 - a) + src[..., :3].astype(np.float32) * a + 0.5).astype(np.uint8)


def _font(size: int, bold: bool = False, font_path: Optional[str] = None):
    path = find_font(font_path)
    if path is None:
        raise RuntimeError("No font with Greek coverage found")
    if bold:
        path = bold_variant(path) or path
    return load_font(path, size)


def wrap_px(text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap by rendered pixel width (never truncates)."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        cur = ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if font.getlength(cand) <= max_width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return [l for l in lines if l] or [""]


def _fit_lines(text: str, size: int, max_width: int, max_lines: int, bold: bool, font_path: Optional[str],
               min_size: int = 12):
    """Shrink the font until the text wraps into max_lines within max_width."""
    s = size
    while True:
        font = _font(s, bold, font_path)
        lines = wrap_px(text, font, max_width)
        if (len(lines) <= max_lines and all(font.getlength(l) <= max_width for l in lines)) or s <= min_size:
            return font, lines
        s = max(min_size, int(s * 0.9))


def render_lower_third(name: str, role: str, width: int, height: int, inset: float = 0.05,
                       color: str = "#0b3d91", font_path: Optional[str] = None) -> Overlay:
    """Name/role strap at bottom-left inside the safe area; long names wrap/shrink."""
    scale = height / 1080 if width >= height else width / 1080
    pad = int(18 * scale)
    max_w = int(width * (1 - 2 * inset) * (0.62 if width >= height else 0.9)) - 2 * pad
    nfont, nlines = _fit_lines(name or " ", int(46 * scale), max_w, 2, True, font_path)
    rfont, rlines = _fit_lines(role or "", int(32 * scale), max_w, 2, False, font_path) if role else (None, [])
    n_lh = int(nfont.size * 1.22)
    r_lh = int(rfont.size * 1.25) if rfont else 0
    text_w = max([nfont.getlength(l) for l in nlines] + ([rfont.getlength(l) for l in rlines] if rfont else []))
    box_w = int(text_w) + 2 * pad + int(10 * scale)
    name_h = n_lh * len(nlines) + 2 * pad // 2
    role_h = (r_lh * len(rlines) + pad) if rlines else 0
    box_h = name_h + role_h
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, box_w, name_h], fill=hex_rgb(color) + (235,))
    d.rectangle([0, 0, int(8 * scale), box_h], fill=(230, 180, 30, 255))
    if rlines:
        d.rectangle([0, name_h, box_w, box_h], fill=(15, 15, 15, 215))
    y = pad // 2
    for l in nlines:
        d.text((pad + int(10 * scale), y), l, font=nfont, fill=(255, 255, 255, 255))
        y += n_lh
    y = name_h + pad // 2
    for l in rlines:
        d.text((pad + int(10 * scale), y), l, font=rfont, fill=(225, 225, 225, 255))
        y += r_lh
    x = int(width * inset)
    ybase = int(height * (1 - inset)) - box_h
    if width < height:  # vertical: sit in the lower third of the frame, above the caption zone
        ybase = int(height * 0.72) - box_h
    return Overlay(np.asarray(img), x, ybase)


def render_watermark(logo_path: str, width: int, height: int, width_ratio: float = 0.12, opacity: float = 0.85,
                     inset: float = 0.05) -> Overlay:
    with Image.open(logo_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
        target_w = max(8, int(width * width_ratio))
        target_h = max(1, int(im.height * target_w / im.width))
        im = im.resize((target_w, target_h), Image.LANCZOS)
    arr = np.asarray(im).copy()
    arr[..., 3] = (arr[..., 3].astype(np.float32) * max(0.0, min(1.0, opacity))).astype(np.uint8)
    x = width - int(width * inset) - target_w
    y = int(height * inset)
    return Overlay(arr, x, y)


def render_title(text: str, subtitle: str, width: int, height: int, inset: float = 0.05,
                 font_path: Optional[str] = None) -> Overlay:
    """Opening title: centred box in the lower half of the safe area."""
    scale = min(width, height) / 1080
    max_w = int(width * (1 - 2 * inset) * 0.9)
    tfont, tlines = _fit_lines(text or " ", int(72 * scale), max_w, 2, True, font_path)
    sfont, slines = _fit_lines(subtitle, int(40 * scale), max_w, 2, False, font_path) if subtitle else (None, [])
    pad = int(28 * scale)
    t_lh, s_lh = int(tfont.size * 1.2), int(sfont.size * 1.25) if sfont else 0
    w = int(max([tfont.getlength(l) for l in tlines] + ([sfont.getlength(l) for l in slines] if sfont else []))) + 2 * pad
    h = t_lh * len(tlines) + s_lh * len(slines) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 170))
    d = ImageDraw.Draw(img)
    y = pad
    for l in tlines:
        d.text(((w - tfont.getlength(l)) / 2, y), l, font=tfont, fill=(255, 255, 255, 255))
        y += t_lh
    for l in slines:
        d.text(((w - sfont.getlength(l)) / 2, y), l, font=sfont, fill=(235, 200, 60, 255))
        y += s_lh
    return Overlay(np.asarray(img), (width - w) // 2, int(height * 0.55))


def render_card(title: str, body: str, width: int, height: int, color: str = "#102040",
                background: Optional[np.ndarray] = None, inset: float = 0.05,
                font_path: Optional[str] = None) -> np.ndarray:
    """Full-frame manually supplied title/data card."""
    if background is not None:
        base = Image.fromarray(background).convert("RGB")
        base = Image.blend(base, Image.new("RGB", base.size, (0, 0, 0)), 0.45)
    else:
        base = Image.new("RGB", (width, height), hex_rgb(color))
    d = ImageDraw.Draw(base)
    scale = min(width, height) / 1080
    max_w = int(width * (1 - 4 * inset))
    tfont, tlines = _fit_lines(title or " ", int(80 * scale), max_w, 3, True, font_path)
    bfont, blines = _fit_lines(body, int(48 * scale), max_w, 8, False, font_path) if body else (None, [])
    t_lh, b_lh = int(tfont.size * 1.2), int(bfont.size * 1.3) if bfont else 0
    total = t_lh * len(tlines) + (int(30 * scale) + b_lh * len(blines) if blines else 0)
    y = (height - total) // 2
    for l in tlines:
        d.text(((width - tfont.getlength(l)) / 2, y), l, font=tfont, fill=(255, 255, 255))
        y += t_lh
    y += int(30 * scale)
    for l in blines:
        d.text(((width - bfont.getlength(l)) / 2, y), l, font=bfont, fill=(230, 230, 230))
        y += b_lh
    return np.asarray(base)


def render_subtitle(lines: list[str], width: int, height: int, bottom_y: int, style: str = "box",
                    box_opacity: float = 0.55, outline_px_1080: float = 2.0, font_size_1080: int = 44,
                    font_path: Optional[str] = None) -> Overlay:
    """White centred caption whose bottom edge sits at bottom_y."""
    scale = height / 1080 if width >= height else width / 1080
    font = _font(max(10, int(font_size_1080 * scale)), False, font_path)
    lh = int(font.size * 1.28)
    pad_x, pad_y = int(14 * scale), int(6 * scale)
    tw = int(max(font.getlength(l) for l in lines)) if lines else 0
    w, h = tw + 2 * pad_x, lh * len(lines) + 2 * pad_y
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stroke = max(1, int(round(outline_px_1080 * scale)))
    y = pad_y
    for l in lines:
        lw = font.getlength(l)
        x = (w - lw) / 2
        if style == "box":
            d.rectangle([x - pad_x, y, x + lw + pad_x, y + lh], fill=(0, 0, 0, int(255 * box_opacity)))
        y += lh
    y = pad_y
    for l in lines:
        lw = font.getlength(l)
        x = (w - lw) / 2
        if style == "box":
            d.text((x, y + (lh - font.size) / 2 - 1), l, font=font, fill=(255, 255, 255, 255))
        else:
            d.text((x, y + (lh - font.size) / 2 - 1), l, font=font, fill=(255, 255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += lh
    return Overlay(np.asarray(img), (width - w) // 2, bottom_y - h)


def subtitle_box_height(n_lines: int, width: int, height: int, font_size_1080: int = 44) -> int:
    scale = height / 1080 if width >= height else width / 1080
    size = max(10, int(font_size_1080 * scale))
    return int(size * 1.28) * n_lines + 2 * int(6 * scale)


def boxes_intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def thumbnail(frame: np.ndarray, max_w: int, max_h: int) -> Image.Image:
    img = Image.fromarray(frame)
    img.thumbnail((max_w, max_h))
    return img


def wrap_chars(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [""]
