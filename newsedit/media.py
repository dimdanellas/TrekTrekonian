"""FFmpeg/ffprobe wrappers: probing, audio decoding, environment checks."""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

SAMPLE_RATE = 48000
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".mpg", ".mpeg", ".wmv"}

_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


class MediaError(Exception):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("NEWSEDIT_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("NEWSEDIT_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def run(cmd: list[str], timeout: Optional[float] = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            creationflags=_NO_WINDOW)
    except FileNotFoundError as exc:
        raise MediaError(f"{cmd[0]} was not found. Install FFmpeg and make sure it is on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).name} timed out") from exc
    if check and cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = "\n".join(err[-6:])
        raise MediaError(f"{Path(cmd[0]).name} failed (exit {cp.returncode}): {tail}")
    return cp


def file_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return "unknown"


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    except (ValueError, AttributeError):
        return 0.0


def probe(path: str | Path) -> dict:
    """Return normalised metadata: duration, width, height, fps, has_audio, has_video,
    rotation, codec names, image flag, warnings."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"File not found: {path}")
    meta: dict = {"warnings": []}
    if file_kind(path) == "image":
        from PIL import Image, ImageOps

        try:
            with Image.open(path) as im:
                im2 = ImageOps.exif_transpose(im)
                meta.update(width=im2.width, height=im2.height, has_video=True, has_audio=False,
                            is_image=True, duration=None, fps=None, rotation=0,
                            exif_rotated=(im2.size != im.size))
        except Exception as exc:  # PIL raises many types
            raise MediaError(f"Cannot read image {path.name}: {exc}") from exc
        return meta
    cp = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
             timeout=60)
    try:
        data = json.loads(cp.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned unreadable data for {path.name}") from exc
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = None
    for src in (fmt, v or {}, a or {}):  # container duration first, then streams
        try:
            d = float(src.get("duration"))
        except (TypeError, ValueError):
            continue
        if d > 0:
            dur = d
            break
    if dur is None:
        meta["warnings"].append("Duration could not be determined (corrupt or streaming container?).")
    meta.update(duration=dur, has_video=v is not None, has_audio=a is not None, is_image=False,
                format_name=fmt.get("format_name", ""))
    if v is not None:
        rot = 0
        try:
            rot = int(float(v.get("tags", {}).get("rotate", 0)))
        except ValueError:
            pass
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                try:
                    rot = int(float(sd["rotation"]))
                except ValueError:
                    pass
        rot %= 360
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        if rot in (90, 270):
            w, h = h, w  # FFmpeg autorotates on decode; report display size
        fps = _fps(v.get("avg_frame_rate", "0/0")) or _fps(v.get("r_frame_rate", "0/0"))
        meta.update(width=w, height=h, fps=round(fps, 3), rotation=rot, vcodec=v.get("codec_name", ""),
                    field_order=v.get("field_order", ""), pix_fmt=v.get("pix_fmt", ""))
        if not w or not h:
            meta["warnings"].append("Video dimensions are missing.")
        if fps <= 0 or fps > 300:
            meta["warnings"].append("Frame rate metadata is missing or implausible.")
    if a is not None:
        meta.update(acodec=a.get("codec_name", ""), sample_rate=int(a.get("sample_rate", 0) or 0),
                    channels=int(a.get("channels", 0) or 0))
    return meta


def decode_audio(path: str | Path, start: float = 0.0, duration: Optional[float] = None,
                 sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode an audio range to float32 stereo array (n, 2) at sr."""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-vn", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    cp = run(cmd, timeout=600)
    arr = np.frombuffer(cp.stdout, dtype=np.float32)
    if arr.size % 2:
        arr = arr[:-1]
    return arr.reshape(-1, 2).copy()


def write_wav(path: str | Path, audio: np.ndarray, sr: int = SAMPLE_RATE, subtype: str = "PCM_24") -> None:
    """Write real PCM WAV (via soundfile when available, else the stdlib wave module)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]
    try:
        import soundfile as sf

        sf.write(str(path), audio, sr, subtype=subtype)
        return
    except ImportError:
        pass
    import wave

    clipped = np.clip(audio, -1.0, 1.0)
    data = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(audio.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def convert_to_wav(src: str | Path, dst: str | Path, sr: int = SAMPLE_RATE, channels: int = 1) -> Path:
    """Decode any provider/recording output to a genuine PCM WAV (never a renamed MP3)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    run([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-ac", str(channels),
         "-ar", str(sr), "-c:a", "pcm_s24le", "-f", "wav", str(tmp)], timeout=600)
    verify_pcm_wav(tmp)
    os.replace(tmp, dst)
    return dst


def verify_pcm_wav(path: str | Path) -> None:
    with open(path, "rb") as fh:
        head = fh.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise MediaError(f"{Path(path).name} is not a RIFF/WAVE file")
    info = probe(path)
    if not str(info.get("acodec", "")).startswith("pcm_"):
        raise MediaError(f"{Path(path).name} does not contain PCM audio")


def media_duration(path: str | Path) -> float:
    d = probe(path).get("duration")
    if d is None:
        raise MediaError(f"Unknown duration for {Path(path).name}")
    return float(d)


def detect_silence_edges(audio: np.ndarray, sr: int = SAMPLE_RATE, threshold_db: float = -45.0,
                         margin: float = 0.12) -> tuple[float, float]:
    """Return (head, tail) seconds of leading/trailing silence, keeping a safety margin.

    Conservative: uses 20 ms RMS windows; speech is never removed because the
    margin is kept on both sides.
    """
    if audio.size == 0:
        return 0.0, 0.0
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    win = max(1, int(sr * 0.02))
    n = len(mono) // win
    if n == 0:
        return 0.0, 0.0
    rms = np.sqrt(np.mean(mono[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    loud = np.nonzero(db > threshold_db)[0]
    if loud.size == 0:
        return 0.0, 0.0
    head = max(0.0, loud[0] * win / sr - margin)
    tail = max(0.0, (n - 1 - loud[-1]) * win / sr - margin)
    return round(head, 3), round(tail, 3)


@dataclass
class Environment:
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None
    ffmpeg_version: str = ""
    encoders: set = field(default_factory=set)
    filters: set = field(default_factory=set)
    modules: dict = field(default_factory=dict)  # name -> version or None
    font: Optional[str] = None
    problems: list = field(default_factory=list)  # (severity, message)

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders


@functools.lru_cache(maxsize=1)
def check_environment() -> Environment:
    env = Environment()
    env.ffmpeg = shutil.which(ffmpeg_bin()) or (ffmpeg_bin() if os.path.exists(ffmpeg_bin()) else None)
    env.ffprobe = shutil.which(ffprobe_bin()) or (ffprobe_bin() if os.path.exists(ffprobe_bin()) else None)
    if not env.ffmpeg or not env.ffprobe:
        env.problems.append(("error", "FFmpeg/ffprobe not found on PATH. Media probing, preview and rendering "
                                      "are unavailable until FFmpeg is installed."))
    else:
        try:
            cp = run([env.ffmpeg, "-hide_banner", "-version"], timeout=20)
            env.ffmpeg_version = cp.stdout.decode("utf-8", "replace").splitlines()[0]
            cp = run([env.ffmpeg, "-hide_banner", "-encoders"], timeout=20)
            for line in cp.stdout.decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) == 6:
                    env.encoders.add(parts[1])
            cp = run([env.ffmpeg, "-hide_banner", "-filters"], timeout=20)
            for line in cp.stdout.decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    env.filters.add(parts[1])
        except MediaError as exc:
            env.problems.append(("error", f"FFmpeg check failed: {exc}"))
        for enc in ("libx264", "aac", "prores_ks", "dnxhd", "pcm_s24le"):
            if enc not in env.encoders:
                env.problems.append(("warning", f"FFmpeg encoder {enc} is not available; profiles using it are disabled."))
    import importlib

    for mod in ("PIL", "numpy", "moviepy", "soundfile", "sounddevice", "gtts", "requests",
                "faster_whisper", "whisper", "keyring"):
        try:
            m = importlib.import_module(mod)
            env.modules[mod] = getattr(m, "__version__", "installed")
        except Exception as exc:  # ImportError, OSError (missing PortAudio) …
            env.modules[mod] = None
            if mod == "sounddevice":
                env.problems.append(("warning", f"Microphone/playback unavailable (sounddevice: {exc}). "
                                                "Import narration files or use TTS; preview opens an external player."))
    if env.modules.get("moviepy") is None:
        env.problems.append(("error", "MoviePy is not installed; rendering is unavailable."))
    if env.modules.get("faster_whisper") is None and env.modules.get("whisper") is None:
        env.problems.append(("warning", "No local Whisper backend installed (faster-whisper or openai-whisper); "
                                        "subtitles must be typed manually."))
    from .fonts import find_font

    env.font = find_font()
    if env.font is None:
        env.problems.append(("error", "No font with Greek coverage was found; titles and subtitles cannot render."))
    return env
