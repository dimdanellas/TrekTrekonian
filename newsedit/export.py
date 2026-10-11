"""Delivery: full render job plus companion files (prompter, rundown,
segment rundown, subtitle sidecars, narration WAVs, manifest).

Rendering writes to a hidden temporary file in the export folder; only a
verified file is renamed to its final, never-overwriting name.  A failed or
cancelled job removes its temporary outputs and leaves user media untouched.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import APP_NAME, __version__
from .audio import Cancelled
from .media import SAMPLE_RATE, write_wav
from .model import Project
from .narration import load_block_audio
from .parser import Diagnostic
from .preflight import run_preflight
from .profiles import get_profile
from .render import RenderError, render_program
from .subtitles import build_timeline_cues, place_cues, write_ass, write_srt
from .timeline import TimelineResult
from .util import atomic_write_json, atomic_write_text, frames_to_tc, safe_filename, unique_path


def lower_thirds_text(project: Project, tl: TimelineResult) -> str:
    seen, parts = set(), []
    for seg in tl.segments:
        if seg.kind in ("soundbite", "standup"):
            lt = project.lower_third_for(seg.label)
            if lt and (lt.name or lt.role) and seg.label not in seen:
                seen.add(seg.label)
                parts.append(f"{lt.name} — {lt.role}" if lt.role else lt.name)
    return "; ".join(parts)


def prompter_text(tl: TimelineResult) -> str:
    """Narration only: no markers, notes or statement transcripts."""
    return tl.parse.prompter_text()


def write_prompter(tl: TimelineResult, path: Path) -> Path:
    atomic_write_text(path, prompter_text(tl))
    return path


def rundown_rows(project: Project, tl: TimelineResult, rendered: Optional[dict] = None) -> list[dict]:
    if rendered:
        dur = rendered["frames"] / rendered["fps"]
        status = "final (rendered)"
        filename = rendered.get("filename", "")
    else:
        dur = tl.total_frames / tl.fps
        status = "estimated" if not tl.all_measured else "measured (not rendered)"
        filename = ""
    prof = get_profile(project.output.profile, project.output.overrides)
    return [{
        "title": project.title,
        "duration_seconds": f"{dur:.2f}",
        "lower_thirds": lower_thirds_text(project, tl),
        "duration_status": status,
        "timecode_duration": frames_to_tc(int(round(dur * tl.fps)), tl.fps),
        "profile": prof.get("label", project.output.profile),
        "filename": filename,
    }]


def write_csv(rows: list[dict], path: Path) -> Path:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
    atomic_write_text(path, "﻿" + buf.getvalue())  # UTF-8 with BOM for spreadsheet compatibility
    return path


def segment_rows(project: Project, tl: TimelineResult) -> list[dict]:
    rows = []
    for seg in tl.segments:
        a = project.asset(seg.visual_asset_id)
        if seg.kind == "narration":
            audio = "narration" + (" (estimated)" if seg.estimated else "")
        elif seg.kind in ("soundbite", "standup"):
            audio = "original statement audio"
        else:
            audio = "music/ambience only"
        lt = project.lower_third_for(seg.label) if seg.kind in ("soundbite", "standup") else None
        rows.append({
            "index": seg.index + 1,
            "segment": seg.name(),
            "start_tc": frames_to_tc(seg.start_frame, tl.fps),
            "end_tc": frames_to_tc(seg.end_frame, tl.fps),
            "duration_seconds": f"{seg.duration_frames / tl.fps:.2f}",
            "visual_asset": (a.name if a else (seg.label if seg.label.startswith("CARD_") else "")),
            "source_in": f"{seg.src_in if seg.kind in ('soundbite', 'standup') else seg.visual_in:.3f}",
            "audio_type": audio,
            "lower_third": (f"{lt.name} — {lt.role}" if lt and lt.role else (lt.name if lt else "")),
        })
    return rows


def narration_exports(project: Project, tl: TimelineResult, voice_over, stem: Path, segment_wavs: bool,
                      voice_over_wav: bool) -> list[Path]:
    out = []
    if voice_over_wav and voice_over is not None:
        p = unique_path(stem.with_name(stem.name + "_voice_over.wav"))
        write_wav(p, voice_over)
        out.append(p)
    if segment_wavs:
        for seg in tl.segments:
            if seg.kind == "narration" and seg.narration is not None and not seg.stale:
                p = unique_path(stem.with_name(f"{stem.name}_narration_{seg.block_index + 1:02d}.wav"))
                write_wav(p, load_block_audio(project, seg.narration))
                out.append(p)
    return out


@dataclass
class ExportResult:
    video: Optional[Path] = None
    files: list[Path] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    duration: float = 0.0


def output_stem(project: Project, export_dir: Path, profile_key: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    return export_dir / f"{safe_filename(project.title)}_{profile_key}_{stamp}"


def export_package(project: Project, cancel: Callable[[], bool], progress: Callable[[str, float], None],
                   custom_profiles: Optional[dict] = None) -> ExportResult:
    """Full render job on an immutable snapshot of the project."""
    progress("Preflight validation", 0.0)
    tl, diags = run_preflight(project, for_render=True, test_encoder=True, custom_profiles=custom_profiles)
    errors = [d for d in diags if d.is_error]
    if errors:
        raise RenderError("Render blocked by preflight errors:\n" + "\n".join(f"• {d.message}" for d in errors[:12]))
    prof = get_profile(project.output.profile, project.output.overrides, custom_profiles)
    export_dir = Path(project.export_directory)
    stem = output_stem(project, export_dir, prof["key"])
    final = unique_path(stem.with_suffix("." + prof["container"]))
    stem = final.with_suffix("")
    tmp = final.with_name("." + final.stem + ".partial." + prof["container"])
    res = ExportResult(diagnostics=[d for d in diags if not d.is_error])
    try:
        outcome = render_program(project, tl, prof, tmp, cancel, progress)
        if cancel():
            raise Cancelled()
        os.replace(tmp, final)
    except BaseException:
        for p in (tmp, tmp.with_name(tmp.stem + ".mix.wav"), Path(str(tmp) + ".log")):
            try:
                p.unlink()
            except OSError:
                pass
        raise
    res.video = final
    res.verification = outcome.verification
    res.duration = outcome.duration
    res.files.append(final)
    progress("Writing companion files", 1.0)
    # Subtitle sidecars from the same placed cue data used for burn-in
    cues = outcome.cues
    st = project.subtitles
    if cues:
        if st.write_srt:
            p = stem.with_suffix(".srt")
            write_srt(cues, p)
            res.files.append(p)
        if st.write_ass:
            p = stem.with_suffix(".ass")
            write_ass(cues, p, prof["width"], prof["height"], st)
            res.files.append(p)
    res.files.append(write_prompter(tl, stem.with_name(stem.name + "_prompter.txt")))
    rendered = {"frames": outcome.frames, "fps": outcome.fps, "filename": final.name}
    res.files.append(write_csv(rundown_rows(project, tl, rendered), stem.with_name(stem.name + "_rundown.csv")))
    if project.export.segment_rundown:
        res.files.append(write_csv(segment_rows(project, tl), stem.with_name(stem.name + "_segments.csv")))
    res.files += narration_exports(project, tl, outcome.mix.voice_over if outcome.mix else None, stem,
                                   project.export.segment_wavs, project.export.voice_over_wav)
    manifest = build_manifest(project, tl, prof, outcome, res, diags)
    mp = stem.with_name(stem.name + "_manifest.json")
    atomic_write_json(mp, manifest)
    res.files.append(mp)
    return res


def build_manifest(project: Project, tl: TimelineResult, prof: dict, outcome, res: ExportResult,
                   diags: list[Diagnostic]) -> dict:
    """Machine-readable record; contains no credentials or provider responses."""
    media = []
    for seg in tl.segments:
        entry = {"segment": seg.name(), "kind": seg.kind, "start_frame": seg.start_frame,
                 "duration_frames": seg.duration_frames}
        if seg.kind in ("soundbite", "standup"):
            b = project.bindings.get(seg.label)
            a = project.asset(b.asset_id) if b else None
            entry.update(file=a.name if a else None, source_in=seg.src_in, source_out=seg.src_out)
        elif seg.visual_asset_id:
            a = project.asset(seg.visual_asset_id)
            entry.update(visual=a.name if a else None, visual_in=seg.visual_in, visual_out=seg.visual_out,
                         fill_policy=seg.fill_policy)
        if seg.kind == "narration":
            entry.update(narration_source=seg.narration.source if seg.narration else None,
                         provider=seg.narration.provider if seg.narration else None)
        media.append(entry)
    music = project.asset(project.music.asset_id)
    return {
        "application": f"{APP_NAME} {__version__}",
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": {"id": project.id, "title": project.title, "target_seconds": project.target_seconds,
                    "duration_override": project.duration_override,
                    "duration_override_reason": project.duration_override_reason},
        "profile": {k: v for k, v in prof.items() if k != "notes"},
        "final_duration_seconds": outcome.duration,
        "final_frames": outcome.frames,
        "fps": outcome.fps,
        "segments": media,
        "music": {"file": music.name if music else None, "base_db": project.music.base_db,
                  "duck_db": project.music.duck_db, "policy": project.music.soundbite_policy},
        "audio": {"peak_dbfs": round(outcome.mix.peak_db, 2) if outcome.mix else None,
                  "applied_gain_db": round(outcome.mix.applied_gain_db, 2) if outcome.mix else None,
                  "loudness_lufs": outcome.mix.loudness_lufs if outcome.mix else None},
        "verification": outcome.verification,
        "warnings_acknowledged": [d.message for d in diags if d.severity == "warning"],
        "outputs": [p.name for p in res.files],
    }


def export_companions_only(project: Project, which: str, export_dir: Path) -> Path:
    """Export prompter or rundown without rendering (rundown labelled estimated)."""
    from .timeline import compile_timeline

    tl = compile_timeline(project)
    prof = get_profile(project.output.profile, project.output.overrides)
    stem = output_stem(project, export_dir, prof["key"])
    export_dir.mkdir(parents=True, exist_ok=True)
    if which == "prompter":
        return write_prompter(tl, unique_path(stem.with_name(stem.name + "_prompter.txt")))
    if which == "rundown":
        rendered = None
        lr = project.last_render
        if lr.get("frames") and lr.get("timeline_hash") == timeline_hash(tl):
            rendered = {"frames": lr["frames"], "fps": lr["fps"], "filename": lr.get("filename", "")}
        return write_csv(rundown_rows(project, tl, rendered), unique_path(stem.with_name(stem.name + "_rundown.csv")))
    if which == "segments":
        return write_csv(segment_rows(project, tl), unique_path(stem.with_name(stem.name + "_segments.csv")))
    if which == "subtitles":
        cues, _ = build_timeline_cues(project, tl)
        place_cues(project, tl, cues, prof["width"], prof["height"])
        p = unique_path(stem.with_suffix(".srt"))
        write_srt(cues, p)
        write_ass(cues, unique_path(stem.with_suffix(".ass")), prof["width"], prof["height"], project.subtitles)
        return p
    raise ValueError(which)


def timeline_hash(tl: TimelineResult) -> str:
    from .util import sha1_text

    return sha1_text([(s.kind, s.label, s.start_frame, s.duration_frames) for s in tl.segments])[:16]
