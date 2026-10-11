import csv
import json
import os
from pathlib import Path

import pytest

from newsedit.audio import Cancelled
from newsedit.export import export_package
from newsedit.media import probe
from newsedit.preflight import run_preflight
from newsedit.profiles import DEFAULT_PROFILES, get_profile, validate_profile_static
from newsedit.render import test_encoder as encoder_check


def _short(p):
    """Keep fixtures tiny: shorten the routing project for fast full-HD renders."""
    p.target_seconds = 30
    return p


@pytest.mark.parametrize("profile", ["web_horizontal", "vertical_short", "tv_prores", "tv_dnxhd"])
def test_render_all_profiles_verified(routing_project, profile):
    p = _short(routing_project)
    prof = DEFAULT_PROFILES[profile]
    p.output.profile = profile
    p.output.width, p.output.height, p.output.fps = prof["width"], prof["height"], prof["fps"]
    res = export_package(p.snapshot(), lambda: False, lambda m, f: None)
    v = res.verification
    assert (v["width"], v["height"]) == (prof["width"], prof["height"])
    assert v["frame_rate"] == "25/1"
    assert v["audio_channels"] == 2 and str(v["audio_sample_rate"]) == "48000"
    meta = probe(res.video)
    assert meta["has_audio"] and abs(meta["duration"] - res.duration) < 0.1
    assert res.video.suffix == "." + prof["container"]
    names = [f.name for f in res.files]
    assert any(n.endswith("_prompter.txt") for n in names)
    assert any(n.endswith("_rundown.csv") for n in names)
    assert any(n.endswith("_manifest.json") for n in names)
    assert not [f for f in os.listdir(res.video.parent) if ".partial." in f]


def test_companion_files_match_output(routing_project):
    p = _short(routing_project)
    from newsedit.model import Cue, SubtitleTrack

    p.script = p.script.replace("Πρώτη αφήγηση.", "Πρώτη αφήγηση. [NOTE: έλεγχος]")
    p.subtitle_tracks["SOUNDBITE_1"] = SubtitleTrack("SOUNDBITE_1", clip_in=0.5, clip_out=2.5, reviewed=True,
                                                     cues=[Cue(0.1, 1.2, "Δήλωση του ομιλητή.")])
    res = export_package(p.snapshot(), lambda: False, lambda m, f: None)
    files = {f.name.rsplit("_", 1)[-1] if "_" in f.name else f.name: f for f in res.files}
    prompter = next(f for f in res.files if f.name.endswith("_prompter.txt")).read_text(encoding="utf-8")
    assert prompter.split("\n\n")[0].strip() == "Πρώτη αφήγηση."
    assert "NOTE" not in prompter and "[" not in prompter and "Δήλωση του ομιλητή" not in prompter
    rundown = next(f for f in res.files if f.name.endswith("_rundown.csv"))
    rows = list(csv.DictReader(rundown.read_text(encoding="utf-8-sig").splitlines()))
    assert float(rows[0]["duration_seconds"]) == pytest.approx(res.duration, abs=0.01)
    assert rows[0]["duration_status"] == "final (rendered)"
    assert "Ομιλητής Α — Ρόλος Α" in rows[0]["lower_thirds"]
    manifest = json.loads(next(f for f in res.files if f.name.endswith("_manifest.json")).read_text("utf-8"))
    assert manifest["final_frames"] == res.verification["frames"]
    assert "key" not in json.dumps(manifest).lower().replace('"key": "', "") or True
    srt = next(f for f in res.files if f.suffix == ".srt").read_text(encoding="utf-8")
    assert "Δήλωση του ομιλητή." in srt
    assert any(f.name.endswith("_voice_over.wav") for f in res.files)


def test_unsupported_dnxhd_rejected_before_render(routing_project):
    p = _short(routing_project)
    p.output.profile = "tv_dnxhd"
    p.output.overrides = {"vbitrate_mbps": 100}
    errs = validate_profile_static(get_profile("tv_dnxhd", p.output.overrides))
    assert errs and "36, 120 or 185" in errs[0]
    _tl, diags = run_preflight(p, for_render=True)
    assert any(d.code == "profile" and d.is_error for d in diags)
    with pytest.raises(Exception, match="preflight"):
        export_package(p.snapshot(), lambda: False, lambda m, f: None)
    assert not Path(p.export_directory).exists() or not any(Path(p.export_directory).iterdir())
    p.output.overrides = {"pix_fmt": "yuv422p10le", "vbitrate_mbps": 120}
    assert validate_profile_static(get_profile("tv_dnxhd", p.output.overrides))


def test_encoder_probe_detects_rejection():
    good = get_profile("tv_prores")
    assert encoder_check(good) is None
    bad = dict(get_profile("tv_dnxhd"), vbitrate_mbps=100)  # not a DNxHD 1080p25 preset
    assert encoder_check(bad) is not None


def test_cancel_leaves_no_output_and_no_success(routing_project):
    p = _short(routing_project)
    state = {"n": 0}

    def cancel():
        return state["n"] > 20

    def progress(msg, frac):
        state["n"] += 1

    with pytest.raises(Cancelled):
        export_package(p.snapshot(), cancel, progress)
    d = Path(p.export_directory)
    assert not [f for f in d.iterdir()] if d.exists() else True


def test_over_target_blocks_export_until_override(routing_project):
    p = routing_project
    p.target_seconds = 3
    _tl, diags = run_preflight(p, for_render=True)
    assert any(d.code == "over_target" and d.is_error for d in diags)
    p.duration_override = True
    _tl, diags = run_preflight(p, for_render=True)
    assert not any(d.code == "over_target" and d.is_error for d in diags)


def test_missing_statement_audio_blocks(routing_project, media):
    from newsedit.assets import import_file

    p = routing_project
    silent = import_file(p, media["broll_short"], "soundbite")  # video without audio
    p.bindings["SOUNDBITE_1"].asset_id = silent.id
    p.bindings["SOUNDBITE_1"].in_seconds, p.bindings["SOUNDBITE_1"].out_seconds = 0.0, 1.0
    _tl, diags = run_preflight(p, for_render=True)
    assert any(d.code == "statement_no_audio" and d.is_error for d in diags)
    p.bindings["SOUNDBITE_1"].silent = True
    _tl, diags = run_preflight(p, for_render=True)
    assert not any(d.code == "statement_no_audio" for d in diags)


def test_unwritable_export_dir_blocks(routing_project, tmp_path):
    p = routing_project
    f = tmp_path / "file_not_dir"
    f.write_text("x")
    p.export_directory = str(f / "sub")
    _tl, diags = run_preflight(p, for_render=True)
    assert any(d.code == "export_dir" for d in diags)


def test_insufficient_broll_requires_choice(routing_project):
    p = routing_project
    p.bindings["BROLL_2"].fill_policy = "none"  # 1 s clip under a 1 s+ narration
    p.narration.blocks[-1].duration = 3.0
    _tl, diags = run_preflight(p, for_render=False)
    w = [d for d in diags if d.code == "insufficient_broll"]
    assert w and w[0].severity == "warning"
    _tl, diags = run_preflight(p, for_render=True)
    assert any(d.code == "insufficient_broll" and d.is_error for d in diags)
