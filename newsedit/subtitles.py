"""Local Whisper transcription, cue formatting/validation, collision-aware
placement and SRT/ASS sidecars.

Cue times are stored relative to the trimmed clip and offset by each
occurrence's own timeline start, so a later statement never inherits the
first one's offset.  Sidecars and burnt-in captions come from the same
`TimelineCue` list.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .media import MediaError, ffmpeg_bin, run
from .model import Cue, Project, SubtitleSettings, SubtitleTrack
from .parser import Diagnostic
from .timeline import TimelineResult
from .util import file_fingerprint, sha1_text

# Words that should not end a line (kept with the following word).
NO_BREAK_AFTER = {
    "ο", "η", "το", "οι", "τα", "του", "της", "των", "τον", "την", "τη", "τους", "τις", "στο", "στη", "στην",
    "στον", "στα", "στους", "στις", "στου", "και", "να", "με", "σε", "για", "από", "ως", "προς", "κατά", "μετά",
    "χωρίς", "ένα", "μια", "μία", "έναν", "θα", "δεν", "μην", "που", "κ.", "κ", "κα", "δρ.", "ότι", "αν",
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "mr.", "mrs.", "ms.", "dr.",
}


class TranscriptionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Transcription (local only)
# ---------------------------------------------------------------------------

def whisper_backend() -> Optional[str]:
    try:
        import faster_whisper  # noqa: F401
        return "faster_whisper"
    except Exception:
        pass
    try:
        import whisper  # noqa: F401
        return "whisper"
    except Exception:
        return None


WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")


def _extract_16k(path: str, tin: float, tout: float) -> str:
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    run([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-ss", f"{tin:.6f}", "-i", path, "-t", f"{tout - tin:.6f}",
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp], timeout=600)
    return tmp


def transcribe_clip(path: str, tin: float, tout: float, language: str = "el", model_name: str = "small",
                    device: str = "cpu", cache_dir: Optional[Path] = None,
                    cancel: Optional[Callable[[], bool]] = None) -> list[dict]:
    """Transcribe only the trimmed statement's own audio.

    Returns segments ``[{start, end, text, words:[[s,e,w],...]}]`` relative to the clip in-point.
    """
    key = sha1_text(os.path.abspath(path), file_fingerprint(path), round(tin, 3), round(tout, 3), language, model_name)
    cache_file = cache_dir / f"whisper_{key[:20]}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    backend = whisper_backend()
    if backend is None:
        raise TranscriptionError("No local Whisper backend is installed. Install faster-whisper "
                                 "(pip install faster-whisper) or type the cues manually.")
    wav = _extract_16k(path, tin, tout)
    out: list[dict] = []
    try:
        if backend == "faster_whisper":
            from faster_whisper import WhisperModel

            compute = "int8" if device == "cpu" else "float16"
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute)
            except Exception as exc:
                raise TranscriptionError(
                    f"Whisper model '{model_name}' could not be loaded ({exc.__class__.__name__}: {exc}). "
                    "The model is downloaded once from Hugging Face on first use; check the internet connection or "
                    "place the model in the local cache. Transcription never falls back to a cloud service.") from exc
            segments, _info = model.transcribe(wav, language=language or None, word_timestamps=True,
                                               vad_filter=False, beam_size=5)
            for s in segments:
                if cancel and cancel():
                    raise TranscriptionError("Transcription cancelled")
                words = [[float(w.start), float(w.end), w.word.strip()] for w in (s.words or [])]
                out.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip(), "words": words})
        else:
            import whisper

            try:
                model = whisper.load_model(model_name, device=device)
            except Exception as exc:
                raise TranscriptionError(f"Whisper model '{model_name}' could not be loaded: {exc}") from exc
            result = model.transcribe(wav, language=language or None, word_timestamps=True,
                                      fp16=(device != "cpu"))
            for s in result.get("segments", []):
                words = [[float(w["start"]), float(w["end"]), w["word"].strip()] for w in s.get("words", [])]
                out.append({"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
                            "words": words})
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass
    clip_len = tout - tin
    for s in out:
        s["start"] = max(0.0, min(clip_len, s["start"]))
        s["end"] = max(s["start"], min(clip_len, s["end"]))
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def wrap_cue_text(text: str, max_chars: int = 42, max_lines: int = 2) -> Optional[list[str]]:
    """Wrap at word boundaries into <= max_lines lines of <= max_chars.

    Prefers balanced lines and avoids leaving articles/prepositions at a line
    end.  Returns None when the text cannot fit (caller splits the cue).
    """
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    if max_lines < 2:
        return None
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        penalty = abs(len(a) - len(b))
        last = words[i - 1].lower().strip(",.;:!?«»\"'")
        if last in NO_BREAK_AFTER or words[i - 1].lower() in NO_BREAK_AFTER:
            penalty += 100
        if re.search(r"[.!?;,:]$", words[i - 1]):
            penalty -= 15  # phrase break
        if best is None or penalty < best[0]:
            best = (penalty, [a, b])
    return best[1] if best else None


def _split_text_units(words: list[str], max_chars: int, max_lines: int) -> list[list[str]]:
    """Group words into chunks that each wrap into max_lines lines."""
    def fits(ws: list[str]) -> bool:
        return wrap_cue_text(" ".join(ws), max_chars, max_lines) is not None

    chunks: list[list[str]] = []
    cur: list[str] = []
    for w in words:
        if not cur or fits(cur + [w]):
            cur.append(w)
            continue
        # Overflow: prefer a phrase break (punctuation) among the last few words.
        cut = next((k for k in range(len(cur) - 1, max(0, len(cur) - 6), -1)
                    if re.search(r"[.!?;,:»]$", cur[k - 1])), None)
        if cut:
            chunks.append(cur[:cut])
            cur = cur[cut:]
        else:
            chunks.append(cur)
            cur = []
        if cur and not fits(cur + [w]):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def format_segments(segments: list[dict], st: SubtitleSettings) -> list[Cue]:
    """Turn recognised segments into cues obeying line/lines/duration rules.

    Uses word timestamps where present; otherwise distributes time in
    proportion to characters (each split cue gets its own sub-interval).
    """
    cues: list[Cue] = []
    for seg in segments:
        text = re.sub(r"\s+", " ", seg.get("text", "")).strip()
        if not text:
            continue
        s0, s1 = float(seg["start"]), float(seg["end"])
        wts = seg.get("words") or []
        tokens = text.split(" ")
        if wts and len(wts) == len(tokens):
            timed = [(float(a), float(b), w) for (a, b, _), w in zip(wts, tokens)]
        elif wts:
            timed = [(float(a), float(b), str(w)) for a, b, w in wts if str(w).strip()]
        else:
            total = sum(len(t) + 1 for t in tokens) or 1
            timed, t = [], s0
            for tok in tokens:
                d = (s1 - s0) * (len(tok) + 1) / total
                timed.append((t, t + d, tok))
                t += d
        words = [w for _, _, w in timed]
        chunks = _split_text_units(words, st.max_chars, st.max_lines)
        idx = 0
        for ch in chunks:
            part = timed[idx: idx + len(ch)]
            idx += len(ch)
            # further split by max duration
            groups = [part]
            while any(g[-1][1] - g[0][0] > st.max_duration and len(g) > 1 for g in groups):
                ng = []
                for g in groups:
                    if g[-1][1] - g[0][0] > st.max_duration and len(g) > 1:
                        mid = len(g) // 2
                        ng += [g[:mid], g[mid:]]
                    else:
                        ng.append(g)
                groups = ng
            for g in groups:
                cues.append(Cue(start=round(g[0][0], 3), end=round(g[-1][1], 3), text=" ".join(w for _, _, w in g),
                                words=[[round(a, 3), round(b, 3), w] for a, b, w in g]))
    # ensure no overlaps between consecutive cues
    for a, b in zip(cues, cues[1:]):
        if b.start < a.end:
            a.end = b.start
    return cues


def apply_min_duration(cues: list[Cue], st: SubtitleSettings, clip_len: float) -> None:
    """Extend short cues up to min_duration where it collides with nothing."""
    for i, c in enumerate(cues):
        if c.end - c.start >= st.min_duration:
            continue
        limit = cues[i + 1].start if i + 1 < len(cues) else clip_len
        c.end = round(min(limit, c.start + st.min_duration), 3)


def validate_track(track: SubtitleTrack, clip_len: float, st: SubtitleSettings, label: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    prev_end = -1.0
    for i, c in enumerate(track.cues):
        name = f"{label} cue {i + 1}"
        if c.end <= c.start:
            diags.append(Diagnostic("error", "cue_negative", f"{name}: end must be after start.", segment=name, label=label))
            continue
        if c.start < -1e-3 or c.end > clip_len + 1e-3:
            diags.append(Diagnostic("error", "cue_range",
                                    f"{name}: {c.start:.2f}–{c.end:.2f}s is outside the clip (0–{clip_len:.2f}s).",
                                    segment=name, label=label))
        if c.start < prev_end - 1e-3:
            diags.append(Diagnostic("error", "cue_overlap", f"{name} overlaps the previous cue.", segment=name, label=label))
        prev_end = max(prev_end, c.end)
        lines = c.text.split("\n") if "\n" in c.text else (wrap_cue_text(c.text, st.max_chars, st.max_lines) or [])
        if not lines or len(lines) > st.max_lines or any(len(l) > st.max_chars for l in lines):
            diags.append(Diagnostic("error", "cue_too_long",
                                    f"{name}: text does not fit {st.max_lines} lines of {st.max_chars} characters; split it.",
                                    segment=name, label=label))
        dur = c.end - c.start
        if dur < st.min_duration - 1e-3:
            diags.append(Diagnostic("warning", "cue_short",
                                    f"{name}: {dur:.2f}s is under the {st.min_duration:g}s minimum and cannot be extended "
                                    "without colliding or leaving the clip; review it.", segment=name, label=label))
        if dur > 7.0 + 1e-3:
            diags.append(Diagnostic("error", "cue_long", f"{name}: {dur:.1f}s exceeds the 7 s maximum.", segment=name,
                                    label=label))
        elif dur > st.max_duration + 1e-3:
            diags.append(Diagnostic("warning", "cue_long", f"{name}: {dur:.1f}s exceeds the {st.max_duration:g}s default.",
                                    segment=name, label=label))
        cps = len(c.text.replace("\n", " ")) / dur if dur > 0 else 0
        if cps > st.max_cps:
            diags.append(Diagnostic("warning", "cue_fast",
                                    f"{name}: {cps:.0f} characters/second exceeds the {st.max_cps:g} cps reading-speed "
                                    "heuristic.", segment=name, label=label))
    if not track.reviewed and track.cues:
        diags.append(Diagnostic("warning", "cue_unreviewed",
                                f"{label}: machine-generated subtitles have not been marked reviewed.",
                                segment=label, label=label))
    return diags


def cue_lines(c: Cue, st: SubtitleSettings) -> list[str]:
    if "\n" in c.text:
        return [l.strip() for l in c.text.split("\n") if l.strip()]
    return wrap_cue_text(c.text, st.max_chars, st.max_lines) or [c.text]


# ---------------------------------------------------------------------------
# Timeline cues + placement
# ---------------------------------------------------------------------------

@dataclass
class TimelineCue:
    start: float  # timeline seconds
    end: float
    lines: list[str]
    source_label: str = ""
    bottom_y: int = 0  # pixel bottom edge of caption box in output frame
    raised: bool = False  # moved above a lower third
    estimated: bool = False
    occurrence: int = 0


def track_cues_for_binding(project: Project, label: str) -> tuple[list[Cue], list[Diagnostic]]:
    """Cues relative to the *current* trim; re-based if the trim changed since transcription."""
    tr = project.subtitle_tracks.get(label)
    b = project.bindings.get(label)
    if tr is None or b is None:
        return [], []
    a = project.asset(b.asset_id)
    out = b.out_seconds if b.out_seconds is not None else (a.meta.get("duration") if a else None)
    if out is None:
        return [], []
    diags: list[Diagnostic] = []
    shift = tr.clip_in - b.in_seconds  # source-based: subtract the selected in-point
    clip_len = out - b.in_seconds
    res = []
    if abs(shift) > 1e-6 or abs((tr.clip_out - tr.clip_in) - clip_len) > 1e-3:
        diags.append(Diagnostic("warning", "cue_trim_changed",
                                f"{label}: the trim changed after transcription; cues were re-based to the new in-point. "
                                "Review them or re-transcribe.", segment=label, label=label))
    for c in tr.cues:
        s, e = c.start + shift, c.end + shift
        if e <= 0 or s >= clip_len:
            continue
        res.append(Cue(max(0.0, s), min(clip_len, e), c.text, c.words))
    return res, diags


def narration_cues(seg, st: SubtitleSettings, fps: int) -> list[TimelineCue]:
    """Optional narration captions: text split across the block's duration (proportional)."""
    if not seg.text:
        return []
    fake = [{"start": 0.0, "end": seg.seconds, "text": seg.text.replace("\n", " "), "words": []}]
    cues = format_segments(fake, st)
    t0 = seg.start_frame / fps
    return [TimelineCue(t0 + c.start, t0 + c.end, cue_lines(c, st), f"Narration {seg.block_index + 1}",
                        estimated=seg.estimated) for c in cues]


def build_timeline_cues(project: Project, tl: TimelineResult) -> tuple[list[TimelineCue], list[Diagnostic]]:
    st = project.subtitles
    cues: list[TimelineCue] = []
    diags: list[Diagnostic] = []
    occ: dict[str, int] = {}
    seen_diag = set()
    for seg in tl.segments:
        if seg.kind in ("soundbite", "standup"):
            occ[seg.label] = occ.get(seg.label, 0) + 1
            track_cues, d = track_cues_for_binding(project, seg.label)
            for x in d:
                if x.key() not in seen_diag:
                    seen_diag.add(x.key())
                    diags.append(x)
            t0 = seg.start_frame / tl.fps
            seg_end = seg.end_frame / tl.fps
            for c in track_cues:
                s, e = t0 + c.start, min(seg_end, t0 + c.end)
                if e > s:
                    cues.append(TimelineCue(s, e, cue_lines(c, st), seg.label, occurrence=occ[seg.label]))
        elif seg.kind == "narration" and st.narration_captions:
            cues.extend(narration_cues(seg, st, tl.fps))
    cues.sort(key=lambda c: c.start)
    return cues, diags


def lower_third_windows(project: Project, tl: TimelineResult) -> list[tuple[float, float, str]]:
    """Active lower-third intervals in timeline seconds."""
    out = []
    for seg in tl.segments:
        if seg.kind not in ("soundbite", "standup"):
            continue
        lt = project.lower_third_for(seg.label)
        if lt is None or not (lt.name or lt.role):
            continue
        t0 = seg.start_frame / tl.fps
        t1 = seg.end_frame / tl.fps
        s = t0 + max(0.0, lt.start_offset)
        e = t1 if lt.end_offset is None else min(t1, t0 + lt.end_offset)
        if e > s:
            out.append((s, e, seg.label))
    return out


def place_cues(project: Project, tl: TimelineResult, cues: list[TimelineCue], width: int, height: int) -> None:
    """Collision-aware placement from rendered bounds of captions and lower thirds."""
    from .visuals import boxes_intersect, render_lower_third, render_subtitle

    st = project.subtitles
    inset = project.visual.safe_inset
    default_bottom = int(round(height * (1 - inset)))
    windows = lower_third_windows(project, tl)
    lt_boxes: dict[str, tuple[int, int, int, int]] = {}
    for _s, _e, label in windows:
        if label not in lt_boxes:
            lt = project.lower_third_for(label)
            ov = render_lower_third(lt.name, lt.role, width, height, inset, project.visual.lower_third_color,
                                    st.font_path)
            lt_boxes[label] = ov.bbox
    margin = int(height * 0.012)
    for c in cues:
        ov = render_subtitle(c.lines, width, height, default_bottom, st.style, st.box_opacity, st.outline_px_1080,
                             st.font_size_1080, st.font_path)
        bottom = default_bottom
        box = ov.bbox
        for s, e, label in windows:
            if c.start < e and s < c.end:
                lb = lt_boxes[label]
                if boxes_intersect(box, lb):
                    bottom = min(bottom, lb[1] - margin)
                    box = (box[0], bottom - ov.h, box[2], bottom)
        c.bottom_y = bottom
        c.raised = bottom != default_bottom


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------

def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(cues: list[TimelineCue], path: Path) -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n" + "\n".join(c.lines) + "\n")
    path.write_text("\n".join(out), encoding="utf-8")


def write_ass(cues: list[TimelineCue], path: Path, width: int, height: int, st: SubtitleSettings,
              font_name: str = "Arial") -> None:
    scale = height / 1080 if width >= height else width / 1080
    size = int(st.font_size_1080 * scale)
    if st.style == "box":
        border_style, outline, back = 3, 0, f"&H{int(255 * (1 - st.box_opacity)):02X}000000"
        outline_col = back
    else:
        border_style, outline, back = 1, max(1, round(st.outline_px_1080 * scale)), "&H00000000"
        outline_col = "&H00000000"
    margin_v_default = height - int(round(height * 0.95))
    hdr = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{size},&H00FFFFFF,&H00FFFFFF,{outline_col},{back},0,0,0,0,100,100,0,0,"
        f"{border_style},{outline},0,2,20,20,{margin_v_default},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for c in cues:
        mv = height - c.bottom_y if c.bottom_y else margin_v_default
        text = "\\N".join(l.replace("{", "(").replace("}", ")") for l in c.lines)
        lines.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,{mv},,{text}")
    path.write_text(hdr + "\n".join(lines) + "\n", encoding="utf-8")


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Minimal SRT reader (used by tests and cue import)."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = block.strip().splitlines()
        if len(rows) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", rows[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        out.append((s, e, "\n".join(rows[2:])))
    return out
