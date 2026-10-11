"""Preflight validation: errors block render, warnings explain what to change."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from .media import check_environment
from .model import Project
from .narration import validate_narration
from .parser import Diagnostic
from .profiles import get_profile, validate_profile_static
from .subtitles import track_cues_for_binding, validate_track
from .timeline import TimelineResult, compile_timeline


def used_asset_ids(project: Project, tl: TimelineResult) -> set[str]:
    ids: set[str] = set()
    for seg in tl.segments:
        if seg.visual_asset_id:
            ids.add(seg.visual_asset_id)
        b = project.bindings.get(seg.label)
        if b is not None:
            if b.asset_id and not seg.label.startswith("CARD_"):
                ids.add(b.asset_id)
            if b.card_background_asset_id:
                ids.add(b.card_background_asset_id)
    for x in (project.music.asset_id, project.watermark.asset_id):
        if x:
            ids.add(x)
    ids.update(p.asset_id for p in project.sfx)
    return ids


def check_export_dir(path: str) -> Optional[str]:
    if not path:
        return "No export folder is set. Choose one under Media → Overlays & Output or Settings → Paths."
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p), prefix=".write_test_")
        os.close(fd)
        os.unlink(tmp)
    except OSError as exc:
        return f"Export folder {path} is not writable: {exc.strerror or exc}"
    return None


def run_preflight(project: Project, for_render: bool = False, tl: Optional[TimelineResult] = None,
                  test_encoder: bool = False, custom_profiles: Optional[dict] = None
                  ) -> tuple[TimelineResult, list[Diagnostic]]:
    tl = tl or compile_timeline(project)
    diags: list[Diagnostic] = list(tl.parse.diagnostics) + list(tl.diagnostics)

    # Assets used by the timeline must exist and be readable
    for aid in sorted(used_asset_ids(project, tl)):
        a = project.asset(aid)
        if a is None:
            diags.append(Diagnostic("error", "asset_removed", f"A binding refers to removed asset {aid}.", segment=aid))
            continue
        if not os.path.exists(a.path):
            diags.append(Diagnostic("error", "asset_missing",
                                    f"{a.name}: file is missing ({a.path}). Use Relink in the Assets tab.",
                                    segment=a.name))
        elif not os.access(a.path, os.R_OK):
            diags.append(Diagnostic("error", "asset_unreadable", f"{a.name}: file is not readable.", segment=a.name))
        for w in a.meta.get("warnings", []):
            diags.append(Diagnostic("warning", "asset_meta", f"{a.name}: {w}", segment=a.name))

    # Statement speech audio
    seen = set()
    for seg in tl.segments:
        if seg.kind in ("soundbite", "standup") and seg.label not in seen:
            seen.add(seg.label)
            b = project.bindings.get(seg.label)
            a = project.asset(b.asset_id) if b else None
            if a is not None and not a.meta.get("has_audio") and not b.silent:
                diags.append(Diagnostic("error", "statement_no_audio",
                                        f"{seg.label}: the source has no audio. Replace it or mark it deliberately silent.",
                                        seg.char_start, seg.char_end, seg.label, seg.label))
            if a is not None and not a.meta.get("has_video") and not b.card_background_asset_id:
                diags.append(Diagnostic("warning", "statement_no_video",
                                        f"{seg.label}: audio-only statement; choose a still in its properties or "
                                        "it will show black.", seg.char_start, seg.char_end, seg.label, seg.label))

    # Narration audio (needed for render)
    narr = validate_narration(project, tl)
    if for_render:
        diags.extend(narr)
    else:
        diags.extend(d for d in narr if d.code != "narration_missing")

    # B-roll coverage requires an explicit choice before render
    if for_render:
        for d in diags:
            if d.code == "insufficient_broll":
                d.severity = "error"

    # Visual policy warnings
    if project.visual.resize_mode == "stretch":
        diags.append(Diagnostic("warning", "stretch", "Stretch resize distorts footage whose aspect ratio differs from "
                                                      "the output. Prefer Fit with Blur Background.", segment="Visual"))
    for label, b in project.bindings.items():
        if b.resize_mode == "stretch" and label in tl.parse.labels:
            diags.append(Diagnostic("warning", "stretch", f"{label} uses Stretch and may be distorted.",
                                    segment=label, label=label))

    # Lower thirds
    for lt in project.lower_thirds:
        if lt.end_offset is not None and lt.end_offset <= lt.start_offset:
            diags.append(Diagnostic("error", "lt_timing", f"Lower third for {lt.binding_label}: end must be after start.",
                                    segment=lt.binding_label, label=lt.binding_label))

    # Subtitles
    st = project.subtitles
    if not (37 <= st.max_chars <= 42):
        diags.append(Diagnostic("error", "sub_settings", "Subtitle line length must be 37–42 characters.",
                                segment="Subtitles"))
    checked = set()
    for seg in tl.segments:
        if seg.kind not in ("soundbite", "standup") or seg.label in checked:
            continue
        checked.add(seg.label)
        tr = project.subtitle_tracks.get(seg.label)
        b = project.bindings.get(seg.label)
        if tr is None:
            if b is not None and not b.silent and seg.src_out > seg.src_in:
                diags.append(Diagnostic("warning", "no_subtitles",
                                        f"{seg.label} has no subtitles yet. Transcribe or type them in the cue editor.",
                                        seg.char_start, seg.char_end, seg.label, seg.label))
            continue
        cues, cd = track_cues_for_binding(project, seg.label)
        diags.extend(cd)
        from .model import SubtitleTrack

        view = SubtitleTrack(seg.label, cues=cues, reviewed=tr.reviewed)
        diags.extend(validate_track(view, seg.src_out - seg.src_in, st, seg.label))

    # Output profile, encoder and font
    env = check_environment()
    try:
        prof = get_profile(project.output.profile, project.output.overrides, custom_profiles)
        for e in validate_profile_static(prof):
            diags.append(Diagnostic("error", "profile", f"Export profile: {e}", segment="Export"))
        if prof["fps"] != tl.fps:
            diags.append(Diagnostic("error", "profile", f"Timeline is {tl.fps} fps but the profile is {prof['fps']} fps; "
                                                        "reselect the profile.", segment="Export"))
        if not env.has_encoder(prof["vcodec"]):
            diags.append(Diagnostic("error", "encoder_missing", f"FFmpeg encoder {prof['vcodec']} is not available.",
                                    segment="Export"))
        if not env.has_encoder(prof["acodec"]):
            diags.append(Diagnostic("error", "encoder_missing", f"FFmpeg encoder {prof['acodec']} is not available.",
                                    segment="Export"))
        if test_encoder and not any(d.code in ("profile", "encoder_missing") for d in diags):
            from .render import test_encoder as _te

            err = _te(prof)
            if err:
                diags.append(Diagnostic("error", "encoder_rejected",
                                        f"FFmpeg rejected the profile settings: {err}", segment="Export"))
    except KeyError as exc:
        diags.append(Diagnostic("error", "profile", str(exc), segment="Export"))
    if env.font is None:
        diags.append(Diagnostic("error", "font", "No font with Greek coverage is installed.", segment="Visual"))
    for sev, msg in env.problems:
        if sev == "error" or for_render:
            diags.append(Diagnostic(sev if for_render else "warning", "environment", msg, segment="Environment"))
    if for_render:
        err = check_export_dir(project.export_directory)
        if err:
            diags.append(Diagnostic("error", "export_dir", err, segment="Export"))
    if not tl.segments:
        diags.append(Diagnostic("error" if for_render else "info", "empty", "The script has no segments yet.",
                                segment="Script"))
    # de-duplicate
    out, keys = [], set()
    for d in diags:
        k = d.key()
        if k not in keys:
            keys.add(k)
            out.append(d)
    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda d: order.get(d.severity, 3))
    return tl, out
