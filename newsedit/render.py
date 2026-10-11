"""Frame composition and rendering.

Composition uses MoviePy 2.x: sources are read with ``VideoFileClip`` and the
programme is a ``VideoClip`` whose frame function follows the compiled
timeline.  Frames are piped to FFmpeg with the profile's exact encoder
arguments, together with the pre-mixed programme audio.  (MoviePy 2.2's own
``write_videofile`` always appends ``-preset`` and forces ``yuva420p`` for
H.264, which conflicts with explicit broadcast profiles, so the encode step
drives FFmpeg directly.)

Preview proxies use the very same renderer and timeline, only smaller.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

os.environ.setdefault("FFMPEG_BINARY", "auto-detect")

from .audio import Cancelled, MixResult, build_mix  # noqa: E402
from .media import SAMPLE_RATE, MediaError, ffmpeg_bin, ffprobe_bin, run, write_wav  # noqa: E402
from .model import Project  # noqa: E402
from .profiles import ffmpeg_audio_args, ffmpeg_video_args  # noqa: E402
from .subtitles import TimelineCue, build_timeline_cues, place_cues  # noqa: E402
from .timeline import TimelineResult, TimelineSegment  # noqa: E402
from .visuals import (Overlay, black, composite, fit_frame, load_still, render_card,  # noqa: E402
                      render_lower_third, render_subtitle, render_title, render_watermark)

_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


class RenderError(Exception):
    pass


class _Source:
    """Lazy MoviePy reader for one video asset."""

    def __init__(self, path: str):
        from moviepy import VideoFileClip

        self.clip = VideoFileClip(path, audio=False)
        self.duration = float(self.clip.duration or 0)
        self.fps = float(self.clip.fps or 25)

    def frame(self, t: float) -> np.ndarray:
        t = max(0.0, min(t, self.duration - 1.0 / max(1.0, self.fps) - 1e-3))
        return self.clip.get_frame(t)

    def close(self) -> None:
        try:
            self.clip.close()
        except Exception:
            pass


class FrameRenderer:
    def __init__(self, project: Project, tl: TimelineResult, width: int, height: int,
                 cues: Optional[list[TimelineCue]] = None, fast: bool = False, burn_subtitles: bool = True):
        self.p = project
        self.tl = tl
        self.W, self.H = width, height
        self.fps = tl.fps
        self.fast = fast
        self._sources: dict[str, _Source] = {}
        self.inset = project.visual.safe_inset
        st = project.subtitles
        self.cues = cues if cues is not None else []
        self.burn = burn_subtitles and st.burn_in
        self._sub_cache: dict[int, Overlay] = {}
        self._lt_cache: dict[str, Overlay] = {}
        self._card_cache: dict[str, np.ndarray] = {}
        self.watermark: Optional[Overlay] = None
        wa = project.asset(project.watermark.asset_id)
        if wa is not None and os.path.exists(wa.path):
            self.watermark = render_watermark(wa.path, width, height, project.watermark.width_ratio,
                                              project.watermark.opacity, self.inset)
        self.title: Optional[Overlay] = None
        ot = project.opening_title
        if ot.enabled and (ot.text or ot.subtitle):
            self.title = render_title(ot.text, ot.subtitle, width, height, self.inset, st.font_path)
        self.overlay_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
        if self.watermark:
            self.overlay_boxes.append(("watermark", self.watermark.bbox))

    # ------------------------------------------------------------------
    def close(self) -> None:
        for s in self._sources.values():
            s.close()
        self._sources.clear()

    def _source(self, asset_id: str) -> _Source:
        if asset_id not in self._sources:
            a = self.p.asset(asset_id)
            self._sources[asset_id] = _Source(a.path)
        return self._sources[asset_id]

    def _mode(self, label: str) -> tuple[str, float, float]:
        b = self.p.bindings.get(label)
        mode = (b.resize_mode if b and b.resize_mode else self.p.visual.resize_mode)
        return mode, (b.crop_x if b else 0.0), (b.crop_y if b else 0.0)

    def lower_third(self, label: str) -> Optional[Overlay]:
        lt = self.p.lower_third_for(label)
        if lt is None or not (lt.name or lt.role):
            return None
        if label not in self._lt_cache:
            self._lt_cache[label] = render_lower_third(lt.name, lt.role, self.W, self.H, self.inset,
                                                       self.p.visual.lower_third_color, self.p.subtitles.font_path)
        return self._lt_cache[label]

    def _card(self, label: str) -> np.ndarray:
        if label not in self._card_cache:
            b = self.p.bindings.get(label)
            bg = None
            if b and b.card_background_asset_id:
                a = self.p.asset(b.card_background_asset_id)
                if a is not None and os.path.exists(a.path):
                    bg = load_still(a.path, self.W, self.H, "crop")
            self._card_cache[label] = render_card(b.card_title if b else label, b.card_body if b else "", self.W,
                                                  self.H, b.card_color if b else "#102040", bg, self.inset,
                                                  self.p.subtitles.font_path)
        return self._card_cache[label]

    def _visual(self, seg: TimelineSegment, t: float) -> np.ndarray:
        W, H = self.W, self.H
        if seg.kind in ("soundbite", "standup"):
            b = self.p.bindings.get(seg.label)
            a = self.p.asset(b.asset_id) if b else None
            if a is None:
                return black(W, H)
            mode, cx, cy = self._mode(seg.label)
            if a.meta.get("has_video") and not a.meta.get("is_image"):
                return fit_frame(self._source(a.id).frame(seg.src_in + t), W, H, mode, cx, cy, self.fast)
            bg = self.p.asset(b.card_background_asset_id) if b.card_background_asset_id else None
            if bg is not None:
                return load_still(bg.path, W, H, mode)
            return black(W, H)
        label = seg.label
        if not label:
            return black(W, H)
        if label.startswith("CARD_"):
            return self._card(label)
        a = self.p.asset(seg.visual_asset_id)
        if a is None:
            return black(W, H)
        mode, cx, cy = self._mode(label)
        if a.meta.get("is_image"):
            return load_still(a.path, W, H, mode, cx, cy)
        vin = seg.visual_in
        vout = seg.visual_out if seg.visual_out is not None else a.meta.get("duration") or 0.0
        avail = max(1e-3, vout - vin)
        if t >= avail:
            t = (t % avail) if seg.fill_policy == "loop" else avail - 1.0 / self.fps
        return fit_frame(self._source(a.id).frame(vin + t), W, H, mode, cx, cy, self.fast)

    def _subtitle(self, idx: int) -> Overlay:
        if idx not in self._sub_cache:
            st = self.p.subtitles
            c = self.cues[idx]
            self._sub_cache[idx] = render_subtitle(c.lines, self.W, self.H, c.bottom_y, st.style, st.box_opacity,
                                                   st.outline_px_1080, st.font_size_1080, st.font_path)
        return self._sub_cache[idx]

    def frame(self, f: int) -> np.ndarray:
        seg = self.tl.segment_at_frame(f)
        if seg is None:
            return black(self.W, self.H)
        t = (f - seg.start_frame) / self.fps
        img = np.array(self._visual(seg, t), dtype=np.uint8, copy=True)
        if img.shape[:2] != (self.H, self.W):
            img = fit_frame(img, self.W, self.H, "stretch", fast=True).copy()
        # Fade through black at boundaries (no timing overlap; speech unaffected)
        if self.p.visual.transition == "fade" and self.p.visual.fade_frames > 1:
            half = max(1, self.p.visual.fade_frames // 2)
            k_in = f - seg.start_frame
            k_out = seg.end_frame - 1 - f
            factor = 1.0
            if seg.index > 0 and k_in < half:
                factor = min(factor, (k_in + 0.5) / half)
            if seg.index < len(self.tl.segments) - 1 and k_out < half:
                factor = min(factor, (k_out + 0.5) / half)
            if factor < 1.0:
                img = (img.astype(np.float32) * factor).astype(np.uint8)
        tsec = f / self.fps
        # Opening title
        if self.title is not None:
            ot = self.p.opening_title
            if ot.start <= tsec < ot.start + ot.duration:
                fade = min(1.0, (tsec - ot.start) / 0.3, (ot.start + ot.duration - tsec) / 0.3)
                composite(img, self.title, max(0.0, fade))
        # Lower thirds
        if seg.kind in ("soundbite", "standup"):
            ov = self.lower_third(seg.label)
            lt = self.p.lower_third_for(seg.label)
            if ov is not None and lt is not None:
                s = lt.start_offset
                e = (seg.duration_frames / self.fps) if lt.end_offset is None else min(lt.end_offset,
                                                                                        seg.duration_frames / self.fps)
                if s <= t < e:
                    fd = max(1e-3, lt.fade_seconds)
                    alpha = min(1.0, (t - s) / fd, (e - t) / fd) if lt.fade_seconds > 0 else 1.0
                    composite(img, ov, max(0.0, alpha))
        # Watermark
        if self.watermark is not None:
            composite(img, self.watermark)
        # Subtitles
        if self.burn and self.cues:
            for i, c in enumerate(self.cues):
                if c.start <= tsec < c.end:
                    composite(img, self._subtitle(i))
        return img


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

@dataclass
class RenderOutcome:
    path: Path
    frames: int
    fps: int
    duration: float
    verification: dict = field(default_factory=dict)
    mix: Optional[MixResult] = None
    cues: list = field(default_factory=list)


def encode(renderer: FrameRenderer, frames: range, audio_wav: Optional[Path], out_path: Path, prof: dict,
           cancel: Callable[[], bool], progress: Callable[[str, float], None]) -> None:
    """Pipe RGB frames (via MoviePy's VideoClip) and the mixed audio into FFmpeg."""
    from moviepy import VideoClip

    fps = renderer.fps
    n = len(frames)
    first = frames.start
    clip = VideoClip(frame_function=lambda t: renderer.frame(first + int(round(t * fps))), duration=n / fps)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{renderer.W}x{renderer.H}", "-r", str(fps), "-i", "-"]
    if audio_wav is not None:
        cmd += ["-i", str(audio_wav), "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ffmpeg_video_args(prof)
    if audio_wav is not None:
        cmd += ffmpeg_audio_args(prof)
    cmd += ["-frames:v", str(n), "-f", prof["container"], str(out_path)]
    errlog = out_path.with_suffix(out_path.suffix + ".log")
    with open(errlog, "wb") as elog:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=elog,
                                creationflags=_NO_WINDOW)
        try:
            t0 = time.monotonic()
            for i in range(n):
                if cancel():
                    raise Cancelled()
                frame = clip.get_frame(i / fps)
                try:
                    proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
                except (BrokenPipeError, OSError):
                    break
                if i % 5 == 0 or i == n - 1:
                    el = time.monotonic() - t0
                    progress(f"Rendering frame {i + 1}/{n} ({(i + 1) / max(el, 1e-6):.1f} fps)", (i + 1) / n)
            proc.stdin.close()
            rc = proc.wait()
        except BaseException:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
            raise
        finally:
            clip.close()
    err = errlog.read_text(encoding="utf-8", errors="replace").strip()
    try:
        errlog.unlink()
    except OSError:
        pass
    if rc != 0:
        raise RenderError(f"FFmpeg encoding failed (exit {rc}): {err[-800:]}")


def verify_output(path: Path, prof: dict, expected_frames: int, expect_audio: bool = True,
                  full_decode: bool = True) -> dict:
    """Probe and decode the result. Raises RenderError on mismatch."""
    if not path.exists() or path.stat().st_size == 0:
        raise RenderError("Output file is missing or empty")
    cp = run([ffprobe_bin(), "-v", "error",
              "-print_format", "json", "-show_streams", "-show_format", "-count_packets", str(path)], timeout=300)
    data = json.loads(cp.stdout.decode("utf-8", "replace"))
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    problems = []
    if v is None:
        raise RenderError("Output has no video stream")
    expected_codec = {"prores_ks": "prores", "dnxhd": "dnxhd", "libx264": "h264"}[prof["vcodec"]]
    if v.get("codec_name") != expected_codec:
        problems.append(f"video codec {v.get('codec_name')} != {expected_codec}")
    if (int(v.get("width", 0)), int(v.get("height", 0))) != (prof["width"], prof["height"]):
        problems.append(f"size {v.get('width')}x{v.get('height')} != {prof['width']}x{prof['height']}")
    rate = v.get("r_frame_rate", "0/1")
    num, den = (int(x) for x in rate.split("/"))
    if den == 0 or abs(num / den - prof["fps"]) > 1e-3:
        problems.append(f"frame rate {rate} != {prof['fps']}")
    nb = int(v.get("nb_read_packets") or v.get("nb_frames") or 0)
    if nb and abs(nb - expected_frames) > 0:
        problems.append(f"frame count {nb} != {expected_frames}")
    if expect_audio:
        if a is None:
            problems.append("audio stream missing")
        else:
            if int(a.get("sample_rate", 0)) != prof.get("sample_rate", 48000):
                problems.append(f"audio sample rate {a.get('sample_rate')}")
            if int(a.get("channels", 0)) != prof.get("channels", 2):
                problems.append(f"audio channels {a.get('channels')}")
    dur = float(data.get("format", {}).get("duration", 0) or 0)
    exp_dur = expected_frames / prof["fps"]
    if abs(dur - exp_dur) > 0.1:
        problems.append(f"duration {dur:.3f}s != {exp_dur:.3f}s")
    if full_decode:
        cp = run([ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"], timeout=1800,
                 check=False)
        derr = cp.stderr.decode("utf-8", "replace").strip()
        if cp.returncode != 0 or derr:
            problems.append(f"decode check failed: {derr[:300]}")
    if problems:
        raise RenderError("Output verification failed: " + "; ".join(problems))
    return {"video_codec": v.get("codec_name"), "profile": v.get("profile"), "width": v.get("width"),
            "height": v.get("height"), "frame_rate": rate, "frames": nb, "pix_fmt": v.get("pix_fmt"),
            "field_order": v.get("field_order", ""), "duration": dur,
            "audio_codec": a.get("codec_name") if a else None, "audio_sample_rate": a.get("sample_rate") if a else None,
            "audio_channels": a.get("channels") if a else None, "decoded_ok": full_decode}


def check_speech_present(mix: MixResult, project: Project, tl: TimelineResult) -> list[str]:
    """A project with speech must never export mute speech."""
    problems = []
    sr = SAMPLE_RATE
    for seg in tl.segments:
        if seg.kind not in ("narration", "soundbite", "standup"):
            continue
        if seg.kind != "narration":
            b = project.bindings.get(seg.label)
            if b is None or b.silent:
                continue
        elif seg.narration is None:
            continue
        i0, i1 = int(seg.start_frame / tl.fps * sr), int(seg.end_frame / tl.fps * sr)
        src = mix.voice_over if seg.kind == "narration" else mix.statements
        if i1 > i0 and float(np.max(np.abs(src[i0:i1]))) < 1e-4:
            problems.append(f"{seg.name()} is silent in the mix")
    return problems


def render_program(project: Project, tl: TimelineResult, prof: dict, out_path: Path,
                   cancel: Callable[[], bool], progress: Callable[[str, float], None],
                   frame_range: Optional[tuple[int, int]] = None, verify: bool = True,
                   fast: bool = False) -> RenderOutcome:
    """Render the snapshot timeline to out_path (a temporary name chosen by the caller)."""
    f0, f1 = frame_range or (0, tl.total_frames)
    if f1 <= f0:
        raise RenderError("Nothing to render: the timeline is empty.")
    progress("Mixing audio", 0.0)
    mix = build_mix(project, tl, cancel=cancel, progress=lambda m, v: progress(m, v * 0.1))
    if not frame_range:
        silent = check_speech_present(mix, project, tl)
        if silent:
            raise RenderError("Speech would be silent: " + "; ".join(silent))
    sr = SAMPLE_RATE
    a0, a1 = int(round(f0 / tl.fps * sr)), int(round(f1 / tl.fps * sr))
    audio = mix.mix[a0:a1]
    wav = out_path.with_name(out_path.stem + ".mix.wav")
    write_wav(wav, audio, subtype="PCM_24")
    cues, _d = build_timeline_cues(project, tl)
    place_cues(project, tl, cues, prof["width"], prof["height"])
    renderer = FrameRenderer(project, tl, prof["width"], prof["height"], cues, fast=fast)
    try:
        encode(renderer, range(f0, f1), wav, out_path, prof, cancel, progress)
    finally:
        renderer.close()
        try:
            wav.unlink()
        except OSError:
            pass
    ver = {}
    if verify:
        progress("Verifying output", 1.0)
        ver = verify_output(out_path, prof, f1 - f0, expect_audio=True, full_decode=True)
    return RenderOutcome(out_path, f1 - f0, tl.fps, (f1 - f0) / tl.fps, ver, mix, cues)


def test_encoder(prof: dict) -> Optional[str]:
    """Encode two black frames with the exact profile arguments. Returns error text or None."""
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix="." + prof["container"])
    os.close(fd)
    try:
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               f"color=c=black:s={prof['width']}x{prof['height']}:r={prof['fps']}:d={2 / prof['fps']}",
               "-f", "lavfi", "-i", f"anullsrc=r={prof.get('sample_rate', 48000)}:cl=stereo", "-t",
               f"{2 / prof['fps']}", "-map", "0:v", "-map", "1:a"]
        cmd += ffmpeg_video_args(prof) + ffmpeg_audio_args(prof) + ["-f", prof["container"], tmp]
        try:
            cp = run(cmd, timeout=120, check=False)
        except MediaError as exc:
            return str(exc)
        if cp.returncode != 0:
            return cp.stderr.decode("utf-8", "replace").strip()[-400:] or f"exit {cp.returncode}"
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
