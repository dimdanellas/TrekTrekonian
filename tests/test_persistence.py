import json
import os
import shutil

import pytest

from newsedit.config import AppConfig
from newsedit.model import Cue, ProjectLoadError, SubtitleTrack, load_project, save_project


def test_save_reopen_roundtrip(routing_project, tmp_path):
    p = routing_project
    p.subtitle_tracks["SOUNDBITE_1"] = SubtitleTrack("SOUNDBITE_1", clip_in=0.5, clip_out=2.5, reviewed=True,
                                                     cues=[Cue(0.1, 1.0, "Διορθωμένο όνομα Παπαδόπουλου.")])
    p.output.profile = "tv_prores"
    p.music.soundbite_policy = "mute"
    p.bindings["SOUNDBITE_1"].in_seconds = 0.75
    path = tmp_path / "έργο.newsproj.json"
    save_project(p, path)
    q = load_project(path)
    assert q.script == p.script
    assert [a.id for a in q.assets] == [a.id for a in p.assets]
    assert q.bindings["SOUNDBITE_1"].in_seconds == 0.75
    assert q.bindings["SOUNDBITE_1"].asset_id == p.bindings["SOUNDBITE_1"].asset_id
    assert q.subtitle_tracks["SOUNDBITE_1"].cues[0].text == "Διορθωμένο όνομα Παπαδόπουλου."
    assert q.output.profile == "tv_prores" and q.music.soundbite_policy == "mute"
    assert [(b.block_index, b.text_hash) for b in q.narration.blocks] == \
           [(b.block_index, b.text_hash) for b in p.narration.blocks]
    assert q.lower_thirds[0].name == "Ομιλητής Α"


def test_paths_relative_inside_project_dir(media, tmp_path):
    from newsedit.assets import import_file
    from newsedit.model import Project

    proj_dir = tmp_path / "proj"
    (proj_dir / "media").mkdir(parents=True)
    f = proj_dir / "media" / "clip.mp4"
    shutil.copy(media["sb_a"], f)
    p = Project()
    import_file(p, f, "soundbite")
    save_project(p, proj_dir / "x.newsproj.json")
    raw = json.loads((proj_dir / "x.newsproj.json").read_text(encoding="utf-8"))
    assert raw["assets"][0]["path"] == "media/clip.mp4"
    moved = tmp_path / "moved"
    shutil.move(str(proj_dir), str(moved))
    q = load_project(moved / "x.newsproj.json")
    assert os.path.exists(q.assets[0].path)


def test_malformed_files_give_clear_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="not valid JSON"):
        load_project(bad)
    bad.write_text(json.dumps({"title": "x"}), encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="schema_version"):
        load_project(bad)
    bad.write_text(json.dumps({"schema_version": 1, "wpm": "fast"}), encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="wpm"):
        load_project(bad)
    bad.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="newer"):
        load_project(bad)


def test_failed_save_preserves_previous(routing_project, tmp_path, monkeypatch):
    path = tmp_path / "p.newsproj.json"
    save_project(routing_project, path)
    before = path.read_text(encoding="utf-8")
    import newsedit.util as U

    def broken_fsync(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(U.os, "fsync", broken_fsync)
    routing_project.title = "Changed"
    with pytest.raises(OSError):
        save_project(routing_project, path)
    assert path.read_text(encoding="utf-8") == before
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_credentials_never_saved(routing_project, tmp_path):
    secret = "sk_live_SECRET_123"
    routing_project.narration.provider = "elevenlabs"
    routing_project.narration.voice_id = "voice123"
    # The application keeps keys only in memory; nothing on the model can hold one.
    path = tmp_path / "p.newsproj.json"
    save_project(routing_project, path)
    text = path.read_text(encoding="utf-8")
    assert secret not in text and "api_key" not in text.lower()


def test_settings_persist_and_malformed_settings_recover(tmp_path):
    cfg = AppConfig()
    cfg.paths.music = str(tmp_path)
    cfg.default_wpm = 150
    cfg.save()
    again, warn = AppConfig.load()
    assert warn is None and again.paths.music == str(tmp_path) and again.default_wpm == 150
    AppConfig.path().write_text("{broken", encoding="utf-8")
    cfg3, warn = AppConfig.load()
    assert warn and "Defaults" in warn and cfg3.default_wpm == 140
    assert AppConfig.path().with_suffix(".broken.json").exists()
