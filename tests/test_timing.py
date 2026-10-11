import math

from newsedit.model import Asset, Binding, NarrationAudio, Project
from newsedit.textutil import count_words
from newsedit.timeline import block_hash, compile_timeline


def _project(words=200, wpm=140):
    p = Project(script="[BROLL_1]\n" + " ".join(["λέξη"] * words) + "\n[SOUNDBITE_1]\n[SOUNDBITE_2]\n", wpm=wpm)
    p.assets = [Asset("sb", "soundbite", "/nonexistent.mp4", meta={"duration": 60.0, "has_audio": True}),
                Asset("br", "broll", "/nonexistent2.mp4", meta={"duration": 300.0})]
    p.bindings = {"SOUNDBITE_1": Binding("SOUNDBITE_1", "sb", 0.0, 20.0),
                  "SOUNDBITE_2": Binding("SOUNDBITE_2", "sb", 30.0, 50.0),
                  "BROLL_1": Binding("BROLL_1", "br", 0.0, None)}
    return p


def test_acceptance_200_words_two_statements():
    tl = compile_timeline(_project())
    assert math.isclose(tl.estimated_total, 200 / 140 * 60 + 40, rel_tol=1e-9)
    assert round(tl.estimated_total, 1) == 125.7
    assert tl.available_words == 186
    codes = {d.code for d in tl.diagnostics}
    assert "over_target" in codes and "narration_allowance" in codes


def test_sequential_frames_and_quantisation():
    tl = compile_timeline(_project(words=7))
    segs = tl.segments
    assert [s.kind for s in segs] == ["narration", "soundbite", "soundbite"]
    assert segs[0].start_frame == 0
    assert segs[1].start_frame == segs[0].end_frame
    assert segs[0].duration_frames == math.ceil(7 / 140 * 60 * 25)  # 75 frames
    assert segs[1].duration_frames == 500


def test_measured_wav_supersedes_estimate_and_wpm_only_changes_estimates(tmp_path):
    from newsedit.fixtures import make_tone_wav

    p = _project(words=10)
    text = " ".join(["λέξη"] * 10)
    wav = make_tone_wav(tmp_path / "n.wav", 2.5)
    p.narration.blocks = [NarrationAudio(0, block_hash(text), text, "recorded", str(wav), duration=2.5)]
    tl = compile_timeline(p)
    assert tl.segments[0].seconds == 2.5 and not tl.segments[0].estimated
    p.wpm = 120
    tl2 = compile_timeline(p)
    assert tl2.segments[0].seconds == 2.5  # recording unchanged
    assert tl2.narration_estimate_seconds != tl.narration_estimate_seconds


def test_changed_text_marks_audio_stale(tmp_path):
    from newsedit.fixtures import make_tone_wav

    p = _project(words=10)
    text = " ".join(["λέξη"] * 10)
    wav = make_tone_wav(tmp_path / "n.wav", 2.5)
    p.narration.blocks = [NarrationAudio(0, block_hash(text), text, "recorded", str(wav), duration=2.5)]
    p.script = p.script.replace("λέξη", "άλλη", 1)
    tl = compile_timeline(p)
    assert tl.segments[0].stale and tl.segments[0].estimated
    assert "stale_narration" in {d.code for d in tl.diagnostics}


def test_override_downgrades_over_target_to_warning():
    p = _project()
    p.duration_override = True
    tl = compile_timeline(p)
    d = next(x for x in tl.diagnostics if x.code == "over_target")
    assert d.severity == "warning"


def test_word_counter_unicode():
    assert count_words("Η Ε.Ε. και ο κ. Παπαδόπουλος-Νικολάου, 2026!") == 7
    assert count_words("ΰ ά ώ") == 3
    assert count_words("— , . ;") == 0


def test_invalid_trim_detected():
    p = _project(words=5)
    p.bindings["SOUNDBITE_1"].out_seconds = 99.0
    tl = compile_timeline(p)
    assert any(d.code == "bad_trim" and d.label == "SOUNDBITE_1" for d in tl.diagnostics)
