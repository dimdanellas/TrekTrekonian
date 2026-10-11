"""Small shared helpers: atomic writes, timecodes, hashing, safe names."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from . import APP_SLUG


def user_config_dir() -> Path:
    """Per-user configuration directory (never the shell working directory)."""
    override = os.environ.get("NEWSEDIT_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_SLUG


def user_cache_dir() -> Path:
    override = os.environ.get("NEWSEDIT_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / APP_SLUG


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Write text to a temp file in the same directory, fsync, then replace.

    On failure the previous file is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path | str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha1_text(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def file_fingerprint(path: Path | str) -> str:
    """Cheap fingerprint (size + mtime) used to invalidate caches."""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    return f"{st.st_size}-{int(st.st_mtime)}"


def seconds_to_tc(seconds: float, fps: int = 25) -> str:
    """HH:MM:SS:FF timecode (non-drop frame)."""
    if seconds is None or math.isnan(seconds):
        return "--:--:--:--"
    neg = seconds < 0
    frames = int(round(abs(seconds) * fps))
    ff = frames % fps
    total = frames // fps
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{'-' if neg else ''}{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def frames_to_tc(frames: int, fps: int = 25) -> str:
    return seconds_to_tc(frames / fps, fps)


def parse_time(text: str, fps: int = 25) -> float:
    """Parse seconds ("12.5"), MM:SS(.ms), HH:MM:SS(.ms) or HH:MM:SS:FF."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        raise ValueError("empty time value")
    parts = text.split(":")
    try:
        if len(parts) == 1:
            value = float(parts[0])
        elif len(parts) == 2:
            value = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            value = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 4:
            ff = int(parts[3])
            if ff >= fps:
                raise ValueError(f"frame field {ff} must be below {fps}")
            value = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + ff / fps
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"invalid time value: {text!r}") from exc
    if value < 0:
        raise ValueError("time cannot be negative")
    return value


def fmt_seconds(seconds: float | None, decimals: int = 1) -> str:
    if seconds is None:
        return "—"
    return f"{seconds:.{decimals}f} s"


_SAFE_RE = re.compile(r"[^\w\-]+", re.UNICODE)


def safe_filename(text: str, fallback: str = "package", max_len: int = 60) -> str:
    """Unicode-safe filename stem: keeps Greek letters, drops path/control chars."""
    text = unicodedata.normalize("NFC", text or "").strip()
    text = _SAFE_RE.sub("_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return (text[:max_len].rstrip("_")) or fallback


def unique_path(path: Path) -> Path:
    """Return path, or path with _2, _3 … suffix so nothing is silently overwritten."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def gain_to_db(gain: float) -> float:
    if gain <= 0:
        return -math.inf
    return 20.0 * math.log10(gain)


def open_in_file_manager(path: Path | str) -> None:
    import subprocess

    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
