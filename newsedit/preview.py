"""Preview: proxy rendering through the final renderer, and a synchronised
proxy player.

The proxy is rendered from the same compiled timeline (same frame numbers)
with speech routing, music, captions, lower thirds and watermark, at reduced
resolution.  Playback uses the audio device clock when ``sounddevice`` can
open an output stream; otherwise the player runs on the system clock with
*no sound* and says so, and the External Player button opens the proxy in
ffplay or the OS default player.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .media import SAMPLE_RATE, decode_audio, ffmpeg_bin, run
from .model import Project, to_dict
from .profiles import get_profile, proxy_profile
from .render import render_program
from .timeline import TimelineResult
from .util import sha1_text

_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def proxy_key(project: Project, tl: TimelineResult, frame_range: Optional[tuple[int, int]], size: tuple[int, int]) -> str:
    d = to_dict(project)
    for k in ("last_render", "acknowledged_warnings", "export_directory", "title"):
        d.pop(k, None)
    mt = [(a.id, os.path.getmtime(a.path) if os.path.exists(a.path) else 0) for a in project.assets]
    return sha1_text(json.dumps(d, sort_keys=True, ensure_ascii=False), mt, frame_range, size)[:20]


def build_proxy(project: Project, tl: TimelineResult, cache_dir: Path, cancel: Callable[[], bool],
                progress: Callable[[str, float], None], frame_range: Optional[tuple[int, int]] = None) -> dict:
    main = get_profile(project.output.profile, project.output.overrides)
    main["fps"] = tl.fps
    prof = proxy_profile(main)
    key = proxy_key(project, tl, frame_range, (prof["width"], prof["height"]))
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"proxy_{key}.mp4"
    f0 = frame_range[0] if frame_range else 0
    if not out.exists():
        tmp = cache_dir / f".proxy_{key}.partial.mp4"
        try:
            render_program(project, tl, prof, tmp, cancel, progress, frame_range=frame_range, verify=True, fast=True)
            os.replace(tmp, out)
        except BaseException:
            for p in (tmp, tmp.with_name(tmp.stem + ".mix.wav")):
                try:
                    p.unlink()
                except OSError:
                    pass
            raise
    # prune old proxies (keep 6 newest)
    proxies = sorted(cache_dir.glob("proxy_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in proxies[6:]:
        try:
            old.unlink()
        except OSError:
            pass
    return {"path": str(out), "first_frame": f0, "fps": tl.fps, "width": prof["width"], "height": prof["height"]}


def external_player_cmd(path: str, custom: str = "") -> list[str]:
    if custom:
        return [custom, path]
    ffplay = shutil.which("ffplay")
    if ffplay:
        return [ffplay, "-autoexit", "-window_title", "News Package Preview", path]
    if sys.platform.startswith("win"):
        return ["cmd", "/c", "start", "", path]
    if sys.platform == "darwin":
        return ["open", path]
    return ["xdg-open", path]


def open_external(path: str, custom: str = "") -> None:
    subprocess.Popen(external_player_cmd(path, custom), creationflags=_NO_WINDOW)


def grab_frame(path: str, t: float, width: int, height: int) -> np.ndarray:
    cp = run([ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.4f}", "-i", path, "-frames:v", "1",
              "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=60)
    data = np.frombuffer(cp.stdout, np.uint8)
    if data.size < width * height * 3:
        return np.zeros((height, width, 3), np.uint8)
    return data[: width * height * 3].reshape(height, width, 3)


class ProxyPlayer:
    """Plays a proxy file: frames via an FFmpeg pipe, audio via sounddevice.

    ``on_frame(frame_index, rgb_array)`` is invoked from a worker thread; the
    GUI must hand it to the main thread (it only stores the latest frame).
    """

    def __init__(self, path: str, fps: int, width: int, height: int):
        self.path, self.fps, self.W, self.H = path, fps, width, height
        self.audio = decode_audio(path)
        self.duration = len(self.audio) / SAMPLE_RATE
        self.total_frames = int(round(self.duration * fps))
        self.position = 0.0
        self.playing = False
        self.audio_ok = False
        self.audio_error = ""
        self._stream = None
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._t0 = 0.0
        self._pos0 = 0.0
        self._sample = 0
        self.latest: Optional[tuple[int, np.ndarray]] = None
        try:
            import sounddevice as sd

            sd.query_devices(kind="output")  # raises when no output device exists
            self.audio_ok = True
        except Exception as exc:
            self.audio_error = str(exc) or exc.__class__.__name__

    def clock(self) -> float:
        if not self.playing:
            return self.position
        if self._stream is not None:
            return self._pos0 + self._sample / SAMPLE_RATE
        return self._pos0 + (time.monotonic() - self._t0)

    def play(self) -> None:
        if self.playing:
            return
        if self.position >= self.duration - 1e-3:
            self.position = 0.0
        self._pos0 = self.position
        self._sample = 0
        self._stop.clear()
        self._start_audio()
        self._t0 = time.monotonic()
        self.playing = True
        self._thread = threading.Thread(target=self._video_loop, daemon=True)
        self._thread.start()

    def _start_audio(self) -> None:
        self._stream = None
        if not self.audio_ok:
            return
        try:
            import sounddevice as sd

            start = int(self._pos0 * SAMPLE_RATE)

            def cb(outdata, frames, time_info, status):
                i = start + self._sample
                chunk = self.audio[i:i + frames]
                outdata[: len(chunk)] = chunk
                if len(chunk) < frames:
                    outdata[len(chunk):] = 0
                self._sample += frames

            self._stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype="float32", callback=cb)
            self._stream.start()
        except Exception as exc:
            self.audio_ok = False
            self.audio_error = str(exc)
            self._stream = None

    def _video_loop(self) -> None:
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{self._pos0:.4f}", "-i", self.path, "-an",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      creationflags=_NO_WINDOW)
        fsize = self.W * self.H * 3
        idx = int(round(self._pos0 * self.fps))
        try:
            while not self._stop.is_set():
                data = self._proc.stdout.read(fsize)
                if len(data) < fsize:
                    break
                frame_t = idx / self.fps
                # wait until the clock reaches this frame; drop frames that are late
                while not self._stop.is_set():
                    now = self.clock()
                    if now >= frame_t - 0.002:
                        break
                    time.sleep(min(0.01, frame_t - now))
                if self.clock() - frame_t < 2.0 / self.fps:
                    self.latest = (idx, np.frombuffer(data, np.uint8).reshape(self.H, self.W, 3))
                idx += 1
        finally:
            self._kill_proc()
            if not self._stop.is_set():
                self.position = min(self.duration, self.clock())
                self.playing = False
                self._stop_audio()

    def _kill_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

    def _stop_audio(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def pause(self) -> None:
        if not self.playing:
            return
        self.position = min(self.duration, self.clock())
        self._stop.set()
        self.playing = False
        self._stop_audio()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._kill_proc()

    def seek(self, t: float) -> np.ndarray:
        was = self.playing
        self.pause()
        self.position = max(0.0, min(self.duration - 1.0 / self.fps, t))
        frame = grab_frame(self.path, self.position, self.W, self.H)
        self.latest = (int(round(self.position * self.fps)), frame)
        if was:
            self.play()
        return frame

    def step(self, frames: int) -> np.ndarray:
        idx = int(round(self.position * self.fps)) + frames
        return self.seek(idx / self.fps)

    def close(self) -> None:
        self.pause()
        self._stop.set()
        self._kill_proc()
        self._stop_audio()
