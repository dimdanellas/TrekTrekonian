"""Tablet (Kivy) interface: helpers, and one end-to-end session in a subprocess."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cue_text_round_trip():
    os.chdir(ROOT)  # an earlier test may leave the process in a deleted folder
    pytest.importorskip("kivy")
    from newsedit.mobile.tabs.markers import cues_to_text, text_to_cues

    cues = text_to_cues("0 - 1.5\nΓεια σας\n\n1.5 – 3\nδεύτερη\nγραμμή\n")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 1.5, "Γεια σας"), (1.5, 3.0, "δεύτερη\nγραμμή")]
    assert text_to_cues(cues_to_text(cues))[1].text == "δεύτερη\nγραμμή"
    with pytest.raises(ValueError, match="Cue 1"):
        text_to_cues("no times here")
    with pytest.raises(ValueError, match="no text"):
        text_to_cues("0 - 1")


def test_desktop_environment_untouched(monkeypatch):
    from newsedit.mobile import platform as plat

    monkeypatch.setattr(plat, "IS_ANDROID", False)
    before = dict(os.environ)
    assert plat.configure_environment() == []
    assert dict(os.environ) == before


def test_android_environment_points_engine_at_bundled_ffmpeg(monkeypatch, tmp_path):
    from newsedit import media
    from newsedit.mobile import platform as plat

    libdir = tmp_path / "lib"
    libdir.mkdir()
    for name in (plat.FFMPEG_LIB, plat.FFPROBE_LIB):
        (libdir / name).write_bytes(b"")
    monkeypatch.setattr(plat, "IS_ANDROID", True)
    monkeypatch.setattr(plat, "native_lib_dir", lambda: str(libdir))
    for k in ("NEWSEDIT_FFMPEG", "NEWSEDIT_FFPROBE", "FFMPEG_BINARY", "IMAGEIO_FFMPEG_EXE", "LD_LIBRARY_PATH",
              "NEWSEDIT_CONFIG_DIR", "NEWSEDIT_CACHE_DIR", "TMPDIR"):
        monkeypatch.setenv(k, "x")  # recorded first, so every key is restored afterwards
        monkeypatch.delenv(k)
    monkeypatch.setenv("ANDROID_PRIVATE", str(tmp_path / "private"))
    assert plat.configure_environment() == []
    assert media.ffmpeg_bin() == str(libdir / plat.FFMPEG_LIB)
    assert media.ffprobe_bin() == str(libdir / plat.FFPROBE_LIB)
    assert os.environ["FFMPEG_BINARY"] == str(libdir / plat.FFMPEG_LIB)
    assert os.environ["LD_LIBRARY_PATH"].split(":")[0] == str(libdir)
    assert os.environ["NEWSEDIT_CONFIG_DIR"] == str(tmp_path / "private" / "config")


def test_android_font_lookup_includes_system_and_kivy_fonts(monkeypatch):
    from newsedit import fonts

    monkeypatch.setenv("ANDROID_ROOT", "/system")
    dirs = [str(d) for d in fonts._font_dirs()]
    pytest.importorskip("kivy")
    assert any(d.endswith(os.path.join("kivy", "data", "fonts")) for d in dirs)
    assert "Roboto-Regular.ttf" in fonts._CANDIDATES


@pytest.mark.skipif(not os.environ.get("DISPLAY") and os.name != "nt", reason="no display (run with xvfb-run)")
def test_tablet_session_end_to_end(tmp_path):
    pytest.importorskip("kivy")
    demo = tmp_path / "demo"
    shutil.copytree(ROOT / "examples" / "demo", demo, ignore=shutil.ignore_patterns("exports"))
    exports = tmp_path / "exports"
    report_path = tmp_path / "report.json"
    env = dict(os.environ, KIVY_HOME=str(tmp_path / "kivy_home"))
    cp = subprocess.run([sys.executable, str(ROOT / "tests" / "mobile_driver.py"),
                         str(demo / "demo_project.newsproj.json"), str(exports), str(report_path)],
                        capture_output=True, text=True, timeout=600, env=env, cwd=str(ROOT))
    assert report_path.exists(), f"exit {cp.returncode}: " + (cp.stdout + cp.stderr)[-3000:]
    r = json.loads(report_path.read_text(encoding="utf-8"))
    assert r["errors"] == []
    assert r["load_errors"] == []
    assert r["segments"] > 0 and r["assets"] > 0
    # live counters follow typing and undo
    assert r["words_after"] == r["words_before"] + 3
    assert r["words_after_undo"] == r["words_before"]
    assert r["dirty_after_edit"] is True
    # overrun colour sets and resets
    assert r["over_bg"][:3] != r["normal_bg"][:3]
    assert r["remaining_text"].startswith("OVER")
    # trims are validated against the real media duration
    assert r["bad_trim_error"] is True
    # manual subtitles
    assert r["cues"] == [[0.0, 2.0, "Πρώτη γραμμή"], [2.0, 4.5, "Δεύτερη γραμμή"]]
    assert r["reviewed"] is True
    # single frame from the final renderer
    assert r["frame_size"] == [960, 540]
    # full render with verified companion files
    outs = r["outputs"]
    assert any(o.endswith(".mp4") for o in outs), (outs, r["popups"])
    assert any(o.endswith("_manifest.json") for o in outs)
    assert any(o.endswith(".srt") for o in outs)
    assert r["last_render"].get("filename", "").endswith(".mp4")
