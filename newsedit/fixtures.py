"""Synthetic test/demo media generated with FFmpeg and Pillow.

These fixtures contain test patterns and tones only: no real political
footage, statements or attributed quotations.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .fonts import find_font, load_font
from .media import SAMPLE_RATE, ffmpeg_bin, run, write_wav


def make_video(path: Path, seconds: float, width: int, height: int, fps: int = 25, tone_hz: float | None = 440.0,
               pattern: str = "testsrc2", label: str = "", rotate: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    if label:
        font = find_font()
        if font:
            safe = label.replace(":", r"\:").replace("'", "")
            vf.append(f"drawtext=fontfile='{font}':text='{safe}':fontsize={height // 12}:fontcolor=white:"
                      f"box=1:boxcolor=black@0.6:x=(w-text_w)/2:y=h/8")
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
           f"{pattern}=s={width}x{height}:r={fps}:d={seconds}"]
    if tone_hz:
        # pulsed tone: speech-like on/off envelope so silence detection and ducking are testable
        cmd += ["-f", "lavfi", "-i",
                f"sine=f={tone_hz}:r={SAMPLE_RATE}:d={seconds},volume='3*(0.6+0.4*sin(2*PI*3*t))':eval=frame"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "30"]
    if tone_hz:
        cmd += ["-c:a", "aac", "-ac", "2", "-shortest"]
    if rotate:
        cmd += ["-metadata:s:v:0", f"rotate={rotate}"]
    cmd.append(str(path))
    run(cmd, timeout=300)
    if rotate:
        # attach display-matrix rotation without re-encoding
        tmp = path.with_suffix(".rot" + path.suffix)
        run([ffmpeg_bin(), "-v", "error", "-y", "-display_rotation", str(rotate), "-i", str(path), "-c", "copy",
             str(tmp)], timeout=120)
        tmp.replace(path)
    return path


def make_tone_wav(path: Path, seconds: float, hz: float = 220.0, amp: float = 0.4, lead: float = 0.3,
                  tail: float = 0.3, stereo: bool = False) -> Path:
    """Narration stand-in: syllable-like pulses with leading/trailing silence."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    env = (0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 4 * t))) * 0.5 + 0.25
    sig = amp * env * np.sin(2 * np.pi * hz * t) * (1 + 0.3 * np.sin(2 * np.pi * 2.0 * hz * t))
    sig[t < lead] = 0
    sig[t > seconds - tail] = 0
    sig = sig.astype(np.float32)
    arr = np.stack([sig, sig], 1) if stereo else sig[:, None]
    write_wav(path, arr)
    return path


def make_music_wav(path: Path, seconds: float = 30.0) -> Path:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    chords = [(220.0, 277.18, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    sig = np.zeros(n, np.float32)
    seg = int(2 * SAMPLE_RATE)
    for i in range(0, n, seg):
        c = chords[(i // seg) % len(chords)]
        tt = t[i:i + seg]
        sig[i:i + seg] = sum(np.sin(2 * np.pi * f * tt) for f in c) / 3 * 0.6
    write_wav(path, np.stack([sig, sig * 0.9], 1))
    return path


def make_image(path: Path, width: int, height: int, text: str, color=(30, 90, 160), exif_rotate: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color)
    d = ImageDraw.Draw(img)
    for i in range(0, width, max(1, width // 12)):
        d.line([(i, 0), (i, height)], fill=(color[0] // 2, color[1] // 2, color[2] // 2), width=2)
    font = find_font()
    if font:
        f = load_font(font, max(12, min(width, height) // 10))
        tw = f.getlength(text)
        d.text(((width - tw) / 2, height / 2 - f.size / 2), text, font=f, fill=(255, 255, 255))
    d.ellipse([10, 10, 60, 60], fill=(255, 220, 0))  # marker to verify orientation (top-left)
    if exif_rotate:
        # Store pixels rotated and tag Orientation=6 so a viewer must rotate 90° CW to display correctly.
        rot = img.rotate(90, expand=True)
        exif = Image.Exif()
        exif[0x0112] = 6
        rot.save(path, exif=exif.tobytes(), quality=92)
    else:
        img.save(path)
    return path


def make_logo(path: Path, size: int = 400) -> Path:
    img = Image.new("RGBA", (size, size // 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size // 2 - 1], radius=size // 10, fill=(200, 30, 40, 230))
    font = find_font()
    if font:
        f = load_font(font, size // 6)
        tw = f.getlength("ΕΚΠΟΜΠΗ")
        d.text(((size - tw) / 2, size // 4 - f.size / 2 - 4), "ΕΚΠΟΜΠΗ", font=f, fill=(255, 255, 255, 255))
    img.save(path)
    return path
