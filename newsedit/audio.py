"""Segment-based audio routing and mixing.

* Narration sits only inside narration segments; its gain is forced to zero
  throughout statement/stand-up intervals.
* Soundbites and stand-ups keep their original speech audio.
* B-roll source audio is muted unless the binding sets an ambience level.
* Music ducks by ``duck_db`` under *all* speech (narration and statements) with
  smooth attack/release envelopes, or fades out before statements with a
  configurable pre-roll ("mute" policy).
* Peaks are measured; optional loudness normalisation uses FFmpeg's EBU R128
  scanner.  No station-specific standard is assumed.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .media import SAMPLE_RATE, MediaError, decode_audio, ffmpeg_bin, run, write_wav
from .model import Project
from .narration import load_block_audio
from .parser import Diagnostic
from .timeline import TimelineResult
from .util import db_to_gain

CONTROL_RATE = 1000  # envelope control rate (Hz)


class Cancelled(Exception):
    pass


@dataclass
class MixResult:
    mix: np.ndarray
    voice_over: np.ndarray  # narration-only, timeline aligned
    statements: np.ndarray
    music_env_db: np.ndarray  # control-rate music gain (dB, -120 for silence)
    peak_db: float = -math.inf
    applied_gain_db: float = 0.0
    loudness_lufs: Optional[float] = None
    diagnostics: list = field(default_factory=list)
    narration_intervals: list = field(default_factory=list)  # (start_s, end_s)
    statement_intervals: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return len(self.mix) / SAMPLE_RATE


def _place(track: np.ndarray, audio: np.ndarray, start: int, max_len: Optional[int] = None) -> int:
    if audio.size == 0 or start >= len(track):
        return 0
    n = len(audio)
    if max_len is not None:
        n = min(n, max_len)
    n = min(n, len(track) - start)
    track[start:start + n] += audio[:n]
    return n


def loop_audio(audio: np.ndarray, length: int, crossfade: int) -> np.ndarray:
    """Loop with equal-power crossfades at the boundaries."""
    if len(audio) == 0:
        return np.zeros((length, 2), np.float32)
    if len(audio) >= length:
        return audio[:length].copy()
    crossfade = max(0, min(crossfade, len(audio) // 3))
    out = np.zeros((length, 2), np.float32)
    pos = 0
    step = len(audio) - crossfade
    t = np.linspace(0, math.pi / 2, crossfade, dtype=np.float32)[:, None] if crossfade else None
    first = True
    while pos < length:
        chunk = audio.copy()
        if crossfade and not first:
            chunk[:crossfade] *= np.sin(t)
        if crossfade:
            chunk[-crossfade:] *= np.cos(t)
        n = min(len(chunk), length - pos)
        out[pos:pos + n] += chunk[:n]
        pos += step if crossfade else len(audio)
        first = False
    return out


def _smooth(target: np.ndarray, attack_ms: float, release_ms: float, rate: int = CONTROL_RATE) -> np.ndarray:
    """Asymmetric one-pole smoothing of a gain curve (linear amplitude)."""
    a_att = math.exp(-1.0 / max(1e-6, attack_ms / 1000 * rate)) if attack_ms > 0 else 0.0
    a_rel = math.exp(-1.0 / max(1e-6, release_ms / 1000 * rate)) if release_ms > 0 else 0.0
    out = np.empty_like(target)
    cur = float(target[0]) if len(target) else 0.0
    for i, v in enumerate(target.tolist()):
        coef = a_att if v < cur else a_rel
        cur = v + (cur - v) * coef
        out[i] = cur
    return out


def music_envelope(project: Project, total_seconds: float, speech: list[tuple[float, float]],
                   statements: list[tuple[float, float]]) -> np.ndarray:
    """Control-rate linear amplitude for the music bed."""
    m = project.music
    n = max(1, int(math.ceil(total_seconds * CONTROL_RATE)))
    base = db_to_gain(m.base_db)
    ducked = db_to_gain(m.base_db - m.duck_db)  # amplitude multiplier 10**(-duck_db/20) below base
    target = np.full(n, base, dtype=np.float64)
    for s, e in speech:
        target[int(s * CONTROL_RATE):int(math.ceil(e * CONTROL_RATE))] = ducked
    if m.soundbite_policy == "mute":
        pre = max(0.0, m.mute_preroll)
        for s, e in statements:
            i0, i1 = int(s * CONTROL_RATE), int(math.ceil(e * CONTROL_RATE))
            p0 = max(0, int((s - pre) * CONTROL_RATE))
            if i0 > p0:
                ramp = np.linspace(1.0, 0.0, i0 - p0, endpoint=False)
                target[p0:i0] = np.minimum(target[p0:i0], target[p0:i0] * ramp)
            target[i0:i1] = 0.0
    env = _smooth(target, m.attack_ms, m.release_ms)
    if m.soundbite_policy == "mute":
        # Smoothing must never leak music into statements.
        for s, e in statements:
            env[int(s * CONTROL_RATE):int(math.ceil(e * CONTROL_RATE))] = 0.0
    fi, fo = int(m.fade_in * CONTROL_RATE), int(m.fade_out * CONTROL_RATE)
    if fi > 0:
        env[:fi] *= np.linspace(0, 1, min(fi, n))[: len(env[:fi])]
    if fo > 0:
        k = min(fo, n)
        env[n - k:] *= np.linspace(1, 0, k)
    return env


def _measure_lufs(audio: np.ndarray) -> Optional[float]:
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_wav(tmp, audio, subtype="FLOAT")
        cp = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", tmp, "-filter_complex", "ebur128", "-f", "null", "-"],
                 timeout=600, check=False)
        text = cp.stderr.decode("utf-8", "replace")
        m = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", text)
        if not m or m[-1] == "-inf":
            return None
        return float(m[-1])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def build_mix(project: Project, tl: TimelineResult, cancel: Optional[Callable[[], bool]] = None,
              progress: Optional[Callable[[str, float], None]] = None, frame_range: Optional[tuple[int, int]] = None
              ) -> MixResult:
    sr = SAMPLE_RATE
    fps = tl.fps
    spf = sr / fps
    total = int(round(tl.total_frames * spf))
    narr = np.zeros((total, 2), np.float32)
    stm = np.zeros((total, 2), np.float32)
    amb = np.zeros((total, 2), np.float32)
    diags: list[Diagnostic] = []
    narr_iv: list[tuple[float, float]] = []
    stm_iv: list[tuple[float, float]] = []

    def check():
        if cancel and cancel():
            raise Cancelled()

    segs = tl.segments
    for k, seg in enumerate(segs):
        check()
        if progress:
            progress(f"Audio: {seg.name()}", k / max(1, len(segs)))
        if frame_range and (seg.end_frame <= frame_range[0] or seg.start_frame >= frame_range[1]):
            continue
        start = int(round(seg.start_frame * spf))
        seg_len = int(round(seg.duration_frames * spf))
        if seg.kind == "narration" and seg.narration is not None and not seg.stale:
            audio = load_block_audio(project, seg.narration) * db_to_gain(project.audio.narration_gain_db)
            n = _place(narr, audio, start, seg_len)
            narr_iv.append((start / sr, (start + n) / sr))
        elif seg.kind in ("soundbite", "standup"):
            b = project.bindings.get(seg.label)
            a = project.asset(b.asset_id) if b else None
            if a is None or seg.src_out <= seg.src_in:
                continue
            stm_iv.append((start / sr, (start + seg_len) / sr))
            if b.silent:
                continue
            if not a.meta.get("has_audio"):
                diags.append(Diagnostic("error", "statement_no_audio",
                                        f"{seg.label}: source has no audio; mark it deliberately silent or replace it.",
                                        segment=seg.label, label=seg.label))
                continue
            audio = decode_audio(a.path, seg.src_in, seg.src_out - seg.src_in) * db_to_gain(b.gain_db)
            _place(stm, audio, start, seg_len)
        if seg.kind in ("narration", "pause", "card") and seg.label:
            b = project.bindings.get(seg.label)
            a = project.asset(b.asset_id) if b else None
            if b is not None and b.ambience_db is not None and a is not None and a.meta.get("has_audio") \
                    and not seg.visual_is_still:
                avail = max(0.0, (seg.visual_out or 0) - seg.visual_in)
                src = decode_audio(a.path, seg.visual_in, avail) if avail > 0 else np.zeros((0, 2), np.float32)
                if len(src) < seg_len and b.fill_policy == "loop":
                    src = loop_audio(src, seg_len, int(0.05 * sr))
                _place(amb, src * db_to_gain(b.ambience_db), start, seg_len)

    # Narration gain is zero throughout statement intervals (validated on real intervals).
    for s, e in stm_iv:
        i0, i1 = int(round(s * sr)), int(round(e * sr))
        if np.any(np.abs(narr[i0:i1]) > 1e-6):
            diags.append(Diagnostic("error", "vo_overlap",
                                    "ΠΡΟΣΟΧΗ: Εντοπίστηκε επικαλυπτόμενο Voice Over πάνω σε δήλωση πολιτικού!",
                                    segment="Audio"))
        narr[i0:i1] = 0.0

    # Music
    check()
    total_s = total / sr
    env = np.zeros(max(1, int(math.ceil(total_s * CONTROL_RATE))))
    music = np.zeros((total, 2), np.float32)
    ma = project.asset(project.music.asset_id)
    if ma is not None:
        if progress:
            progress("Audio: music", 0.9)
        src = decode_audio(ma.path)
        if project.music.loop:
            src = loop_audio(src, total, int(project.music.loop_crossfade * sr))
        else:
            if len(src) < total:
                diags.append(Diagnostic("warning", "music_short",
                                        f"Music ends at {len(src) / sr:.1f}s, before the package ends "
                                        f"({total_s:.1f}s). Enable looping or choose a longer track.", segment="Music"))
            src = np.pad(src, ((0, max(0, total - len(src))), (0, 0)))[:total]
        speech = narr_iv + stm_iv
        env = music_envelope(project, total_s, speech, stm_iv)
        t_ctrl = np.arange(len(env)) / CONTROL_RATE
        t_samp = np.arange(total) / sr
        gain = np.interp(t_samp, t_ctrl, env).astype(np.float32)
        music = src * gain[:, None]

    # SFX
    sfx = np.zeros((total, 2), np.float32)
    for p in project.sfx:
        check()
        a = project.asset(p.asset_id)
        if a is None:
            diags.append(Diagnostic("error", "sfx_missing", "An SFX placement refers to a removed asset.", segment="SFX"))
            continue
        audio = decode_audio(a.path) * db_to_gain(p.gain_db)
        start = int(round(p.at_seconds * sr))
        n = _place(sfx, audio, start)
        s0, s1 = start / sr, (start + n) / sr
        if any(s0 < e and s < s1 for s, e in narr_iv + stm_iv) and p.gain_db > -12:
            diags.append(Diagnostic("warning", "sfx_speech",
                                    f"SFX {a.name} at {p.at_seconds:.1f}s plays under speech at {p.gain_db:g} dB; "
                                    "consider lowering it below −12 dB or moving it.", segment="SFX"))

    mix = narr + stm + amb + music + sfx
    res = MixResult(mix=mix, voice_over=narr, statements=stm,
                    music_env_db=20 * np.log10(np.maximum(env, 1e-6)), diagnostics=diags,
                    narration_intervals=narr_iv, statement_intervals=stm_iv)

    # Loudness and peaks
    gain_db = 0.0
    if project.audio.loudness_target_lufs is not None and len(mix) > sr // 2:
        lufs = _measure_lufs(mix)
        if lufs is not None:
            gain_db = project.audio.loudness_target_lufs - lufs
            res.loudness_lufs = project.audio.loudness_target_lufs
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    peak_after = peak * db_to_gain(gain_db)
    target_peak = db_to_gain(project.audio.peak_target_db)
    if peak_after > target_peak:
        if project.audio.auto_fix_peaks:
            reduce = 20 * math.log10(target_peak / peak_after)
            gain_db += reduce
            diags.append(Diagnostic("warning", "peak_adjusted",
                                    f"Mix peak would reach {20 * math.log10(peak_after):.1f} dBFS; overall gain was "
                                    f"reduced by {-reduce:.1f} dB to meet the {project.audio.peak_target_db:g} dBFS peak "
                                    "target.", segment="Audio"))
        else:
            diags.append(Diagnostic("warning", "clipping",
                                    f"Mix peak {20 * math.log10(peak_after):.1f} dBFS exceeds the "
                                    f"{project.audio.peak_target_db:g} dBFS target and may clip. Enable automatic "
                                    "peak correction or lower music/SFX/speech gain.", segment="Audio"))
    if gain_db:
        res.mix = mix * db_to_gain(gain_db)
    res.applied_gain_db = gain_db
    final_peak = float(np.max(np.abs(res.mix))) if res.mix.size else 0.0
    res.peak_db = 20 * math.log10(final_peak) if final_peak > 0 else -math.inf
    if res.loudness_lufs is not None:
        res.loudness_lufs = _measure_lufs(res.mix)
    return res
