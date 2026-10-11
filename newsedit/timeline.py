"""Timing budget and the single authoritative compiled timeline.

The script is parsed into typed segments; this module turns them into
sequential timeline segments with integer frame positions.  Preview, audio
mixing, captions and every export use the same compiled timeline.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

from .model import Binding, NarrationAudio, Project
from .parser import Diagnostic, ParseResult, ScriptSegment, parse_script
from .textutil import available_words, estimate_seconds
from .util import sha1_text


def block_hash(text: str) -> str:
    return sha1_text(text.strip())[:16]


def narration_audio_for(project: Project, seg: ScriptSegment) -> tuple[Optional[NarrationAudio], bool]:
    """Return (audio, stale). Audio matches the block text hash; an entry for the
    same index with a different hash is stale (text changed since recording)."""
    h = block_hash(seg.text)
    by_hash = [b for b in project.narration.blocks if b.text_hash == h]
    if by_hash:
        exact = [b for b in by_hash if b.block_index == seg.block_index]
        return (exact[0] if exact else by_hash[0]), False
    for b in project.narration.blocks:
        if b.block_index == seg.block_index:
            return b, True
    return None, False


def narration_audio_seconds(project: Project, na: NarrationAudio) -> float:
    if na.source == "take":
        dur = max(0.0, na.take_out - na.take_in)
    else:
        dur = na.duration
    return max(0.0, dur - na.trim_head - na.trim_tail)


def binding_trim(project: Project, b: Optional[Binding]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (in, out, source_duration) with out resolved to source end when None."""
    if b is None:
        return None, None, None
    a = project.asset(b.asset_id)
    if a is None:
        return None, None, None
    src = a.meta.get("duration")
    out = b.out_seconds if b.out_seconds is not None else src
    return b.in_seconds, out, src


@dataclass
class TimelineSegment:
    index: int
    kind: str  # narration | soundbite | standup | card | pause
    label: str  # binding label (statement/card) or visual label (narration/pause)
    start_frame: int = 0
    duration_frames: int = 0
    seconds: float = 0.0  # exact (unquantised) duration
    estimated: bool = False
    block_index: int = -1
    text: str = ""
    words: int = 0
    narration: Optional[NarrationAudio] = None
    stale: bool = False
    src_in: float = 0.0  # statement source range
    src_out: float = 0.0
    visual_asset_id: Optional[str] = None
    visual_in: float = 0.0
    visual_out: Optional[float] = None
    visual_is_still: bool = False
    fill_policy: str = "none"
    char_start: int = 0
    char_end: int = 0
    standalone_card: bool = False

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames

    def name(self) -> str:
        if self.kind == "narration":
            return f"Narration {self.block_index + 1}"
        if self.kind == "pause":
            return "Pause"
        return self.label

    @property
    def has_speech(self) -> bool:
        return self.kind in ("narration", "soundbite", "standup")


@dataclass
class TimelineResult:
    fps: int
    segments: list[TimelineSegment] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    parse: Optional[ParseResult] = None
    narration_words: int = 0
    narration_estimate_seconds: float = 0.0  # words / wpm, all blocks
    narration_measured_seconds: float = 0.0  # for blocks that have audio
    narration_seconds_used: float = 0.0  # measured where available, estimate otherwise
    statement_seconds: float = 0.0
    other_seconds: float = 0.0  # standalone cards
    pause_seconds: float = 0.0
    transition_overlap_seconds: float = 0.0
    estimated_total: float = 0.0  # estimate-only formula (section 7)
    total_seconds: float = 0.0  # best-known total (measured where available), unquantised
    total_frames: int = 0
    available_words: int = 0
    all_measured: bool = False
    target_seconds: float = 120.0

    @property
    def reserved_seconds(self) -> float:
        return self.statement_seconds + self.other_seconds + self.pause_seconds - self.transition_overlap_seconds

    @property
    def frame_seconds(self) -> float:
        return self.total_frames / self.fps if self.fps else 0.0

    @property
    def over_target(self) -> bool:
        return self.total_seconds > self.target_seconds + 1e-9

    @property
    def remaining_seconds(self) -> float:
        return self.target_seconds - self.total_seconds

    def segment_at_frame(self, frame: int) -> Optional[TimelineSegment]:
        for s in self.segments:
            if s.start_frame <= frame < s.end_frame:
                return s
        return None

    def speech_intervals(self) -> list[tuple[float, float, str]]:
        return [(s.start_frame / self.fps, s.end_frame / self.fps, s.kind) for s in self.segments if s.has_speech]


def compile_timeline(project: Project, parse: Optional[ParseResult] = None) -> TimelineResult:
    fps = int(project.output.fps)
    parse = parse or parse_script(project.script)
    tl = TimelineResult(fps=fps, parse=parse, target_seconds=project.target_seconds)
    diags = tl.diagnostics
    cursor_frames = 0
    any_estimated = False

    for i, ps in enumerate(parse.segments):
        seg = TimelineSegment(index=i, kind=ps.kind, label=ps.label, char_start=ps.char_start, char_end=ps.char_end,
                              block_index=ps.block_index, text=ps.text, words=ps.words)
        seconds = 0.0
        if ps.kind == "narration":
            tl.narration_words += ps.words
            est = estimate_seconds(ps.words, project.wpm)
            tl.narration_estimate_seconds += est
            na, stale = narration_audio_for(project, ps)
            if na is not None and not stale and _audio_present(project, na):
                seconds = narration_audio_seconds(project, na)
                seg.narration = na
                tl.narration_measured_seconds += seconds
            else:
                seconds = est
                seg.estimated = True
                any_estimated = True
                if na is not None and stale:
                    seg.narration = na
                    seg.stale = True
            tl.narration_seconds_used += seconds
            _bind_visual(project, seg, ps.label)
        elif ps.kind in ("soundbite", "standup"):
            b = project.bindings.get(ps.label)
            tin, tout, src = binding_trim(project, b)
            if b is None or b.asset_id is None:
                diags.append(Diagnostic("error", "unbound", f"{ps.label} is not bound to a media file.",
                                        ps.char_start, ps.char_end, ps.label, ps.label))
            elif project.asset(b.asset_id) is None:
                diags.append(Diagnostic("error", "unbound", f"{ps.label} refers to a removed asset.",
                                        ps.char_start, ps.char_end, ps.label, ps.label))
            elif tout is None:
                diags.append(Diagnostic("error", "unknown_duration",
                                        f"{ps.label}: source duration unknown; set an explicit out-point.",
                                        ps.char_start, ps.char_end, ps.label, ps.label))
            else:
                err = trim_error(tin, tout, src)
                if err:
                    diags.append(Diagnostic("error", "bad_trim", f"{ps.label}: {err}",
                                            ps.char_start, ps.char_end, ps.label, ps.label))
                else:
                    seconds = tout - tin
                    seg.src_in, seg.src_out = tin, tout
            tl.statement_seconds += seconds
            # Statement video is its own visual
            if b is not None and b.asset_id:
                seg.visual_asset_id = b.asset_id
                seg.visual_in, seg.visual_out = seg.src_in, seg.src_out
        elif ps.kind == "card":
            b = project.bindings.get(ps.label)
            seconds = b.card_seconds if b else 4.0
            seg.standalone_card = True
            tl.other_seconds += seconds
            seg.label = ps.label
            _bind_visual(project, seg, ps.label)
        elif ps.kind == "pause":
            seconds = ps.pause_seconds
            tl.pause_seconds += seconds
            _bind_visual(project, seg, ps.label)
        seg.seconds = seconds
        seg.start_frame = cursor_frames
        seg.duration_frames = int(math.ceil(seconds * fps - 1e-6)) if seconds > 0 else 0
        cursor_frames += seg.duration_frames
        tl.segments.append(seg)

    tl.total_frames = cursor_frames
    tl.estimated_total = (tl.narration_estimate_seconds + tl.statement_seconds + tl.other_seconds
                          + tl.pause_seconds - tl.transition_overlap_seconds)
    tl.total_seconds = (tl.narration_seconds_used + tl.statement_seconds + tl.other_seconds + tl.pause_seconds
                        - tl.transition_overlap_seconds)
    tl.available_words = available_words(project.target_seconds, tl.reserved_seconds, project.wpm)
    tl.all_measured = not any_estimated

    # ---- timing diagnostics -------------------------------------------------
    if any_estimated and tl.narration_words:
        diags.append(Diagnostic("warning", "estimated_timing",
                                "Narration timing is estimated from word count until narration audio exists.",
                                segment="Timing"))
    if tl.narration_words > tl.available_words:
        diags.append(Diagnostic(
            "warning", "narration_allowance",
            f"Narration has {tl.narration_words} words; about {tl.available_words} fit at {project.wpm:g} WPM after "
            f"{tl.reserved_seconds:.1f} s of statements/pauses/cards. Shorten narration or statements.",
            segment="Timing"))
    if tl.over_target:
        sev = "warning" if project.duration_override else "error"
        msg = (f"Package is {tl.total_seconds:.1f} s, {tl.total_seconds - project.target_seconds:.1f} s over the "
               f"{project.target_seconds:g} s target.")
        msg += (" A longer-duration override is recorded." if project.duration_override
                else " Final export is blocked until corrected or an override is recorded.")
        diags.append(Diagnostic(sev, "over_target", msg, segment="Timing"))
    for seg in tl.segments:
        if seg.stale:
            diags.append(Diagnostic("error", "stale_narration",
                                    f"Narration {seg.block_index + 1} text changed after its audio was made; "
                                    "re-record or regenerate it.",
                                    seg.char_start, seg.char_end, seg.name()))
        _coverage_diag(project, seg, diags)
    _overlap_check(tl, diags)
    return tl


def _audio_present(project: Project, na: NarrationAudio) -> bool:
    if na.source == "take":
        take = next((t for t in project.narration.takes if t.id == na.take_id), None)
        return take is not None and os.path.exists(take.path)
    return bool(na.wav_path) and os.path.exists(na.wav_path)


def trim_error(tin: Optional[float], tout: Optional[float], src: Optional[float]) -> str:
    if tin is None or tout is None:
        return "trim is not set"
    if tin < 0:
        return "in-point is negative"
    if tout <= tin:
        return f"out-point {tout:.2f}s must be after in-point {tin:.2f}s"
    if src is not None and tout > src + 1e-3:
        return f"out-point {tout:.2f}s is beyond the source duration {src:.2f}s"
    return ""


def _bind_visual(project: Project, seg: TimelineSegment, label: str) -> None:
    if not label:
        return
    b = project.bindings.get(label)
    if b is None:
        return
    seg.fill_policy = b.fill_policy
    kind = label.split("_")[0]
    if kind == "CARD":
        seg.visual_asset_id = b.card_background_asset_id
        seg.visual_is_still = True
        return
    a = project.asset(b.asset_id)
    if a is None:
        return
    seg.visual_asset_id = a.id
    seg.visual_is_still = bool(a.meta.get("is_image"))
    seg.visual_in = b.in_seconds
    seg.visual_out = b.out_seconds if b.out_seconds is not None else a.meta.get("duration")


def _coverage_diag(project: Project, seg: TimelineSegment, diags: list[Diagnostic]) -> None:
    if seg.kind not in ("narration", "pause"):
        return
    label = seg.label
    if not label:
        return
    kind = label.split("_")[0]
    b = project.bindings.get(label)
    if kind == "CARD":
        return
    if b is None or project.asset(b.asset_id) is None:
        diags.append(Diagnostic("error", "unbound", f"{label} (visual for {seg.name()}) is not bound to a media file.",
                                seg.char_start, seg.char_end, label, label))
        return
    if seg.visual_is_still:
        return
    vin, vout = seg.visual_in, seg.visual_out
    a = project.asset(b.asset_id)
    err = trim_error(vin, vout, a.meta.get("duration") if a else None)
    if err:
        diags.append(Diagnostic("error", "bad_trim", f"{label}: {err}", seg.char_start, seg.char_end, label, label))
        return
    avail = (vout or 0) - vin
    if seg.seconds > avail + 1e-3:
        if b.fill_policy == "none":
            diags.append(Diagnostic(
                "warning", "insufficient_broll",
                f"{seg.name()} lasts {seg.seconds:.1f} s but {label} covers only {avail:.1f} s. Choose more footage, "
                "an approved loop, a still hold, or shorten the narration (required before render).",
                seg.char_start, seg.char_end, seg.name(), label))
    pref = project.visual.broll_shot_pref_seconds
    if pref > 0 and seg.kind == "narration" and min(seg.seconds, avail) > pref * 3:
        diags.append(Diagnostic(
            "info", "long_shot",
            f"{label} is held for {min(seg.seconds, avail):.1f} s in {seg.name()} (editorial preference is cuts every "
            f"~{pref:g} s; fine for maps and cards).",
            seg.char_start, seg.char_end, seg.name(), label))


OVERLAP_WARNING = "ΠΡΟΣΟΧΗ: Εντοπίστηκε επικαλυπτόμενο Voice Over πάνω σε δήλωση πολιτικού!"


def _overlap_check(tl: TimelineResult, diags: list[Diagnostic]) -> None:
    """Validate the actual interval intersection of narration and statement audio."""
    narr = [(s.start_frame, s.start_frame + int(math.ceil(s.seconds * tl.fps - 1e-6)), s)
            for s in tl.segments if s.kind == "narration"]
    stm = [(s.start_frame, s.end_frame, s) for s in tl.segments if s.kind in ("soundbite", "standup")]
    for a0, a1, a in narr:
        for b0, b1, b in stm:
            if a0 < b1 and b0 < a1:
                diags.append(Diagnostic("error", "vo_overlap", f"{OVERLAP_WARNING} ({a.name()} / {b.label})",
                                        a.char_start, a.char_end, b.label, b.label))
