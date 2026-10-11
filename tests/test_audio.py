import math

import numpy as np

from newsedit.audio import CONTROL_RATE, build_mix, music_envelope
from newsedit.media import SAMPLE_RATE
from newsedit.model import Project
from newsedit.timeline import compile_timeline
from newsedit.util import db_to_gain


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def _span(tl, seg):
    return int(seg.start_frame / tl.fps * SAMPLE_RATE), int(seg.end_frame / tl.fps * SAMPLE_RATE)


def test_routing_narrator_silent_during_statements(routing_project):
    p = routing_project
    tl = compile_timeline(p)
    assert [s.kind for s in tl.segments] == ["narration", "soundbite", "narration", "soundbite", "narration"]
    mix = build_mix(p, tl)
    for seg in tl.segments:
        a, b = _span(tl, seg)
        if seg.kind == "soundbite":
            assert np.max(np.abs(mix.voice_over[a:b])) == 0.0  # no narrator during either statement
            assert _rms(mix.statements[a:b]) > 0.05  # both statements preserved
        else:
            assert np.max(np.abs(mix.statements[a:b])) == 0.0
    assert len(mix.statement_intervals) == 2
    assert not [d for d in mix.diagnostics if d.code == "vo_overlap"]


def test_broll_audio_muted_by_default_and_ambience_override(routing_project):
    p = routing_project
    p.music.asset_id = None
    tl = compile_timeline(p)
    seg = tl.segments[0]  # narration over BROLL_1 whose source has a 150 Hz tone
    a, b = _span(tl, seg)
    mix = build_mix(p, tl)
    residual = mix.mix[a:b] - mix.voice_over[a:b]
    assert np.max(np.abs(residual)) < 1e-6
    p.bindings["BROLL_1"].ambience_db = -12.0
    mix2 = build_mix(p, tl)
    residual2 = mix2.mix[a:b] - mix2.voice_over[a:b] * (10 ** (mix2.applied_gain_db / 20))
    assert _rms(residual2) > 0.01


def test_ducking_db_math_and_restore():
    p = Project()
    p.music.base_db = -18
    p.music.duck_db = 20
    p.music.fade_in = p.music.fade_out = 0
    env = music_envelope(p, 10.0, [(2.0, 5.0)], [])
    base = db_to_gain(-18)
    ducked = db_to_gain(-38)
    assert math.isclose(ducked / base, 10 ** (-20 / 20), rel_tol=1e-9)
    assert math.isclose(env[int(1.0 * CONTROL_RATE)], base, rel_tol=1e-3)  # pause: base level
    assert math.isclose(env[int(4.5 * CONTROL_RATE)], ducked, rel_tol=1e-2)  # under speech
    assert math.isclose(env[int(8.0 * CONTROL_RATE)], base, rel_tol=1e-3)  # restored
    # smooth: no step larger than a small fraction per control sample
    assert np.max(np.abs(np.diff(env))) < base * 0.05


def test_ducking_acts_on_both_narration_and_statements(routing_project):
    p = routing_project
    p.music.fade_in = p.music.fade_out = 0
    tl = compile_timeline(p)
    mix = build_mix(p, tl)
    env = 10 ** (mix.music_env_db / 20)
    base = db_to_gain(p.music.base_db)
    for seg in tl.segments:
        mid = (seg.start_frame + seg.end_frame) / 2 / tl.fps
        # narration mid-block may be in a silent lead/tail of the narration file; statements are speech throughout
        if seg.kind == "soundbite":
            assert env[int(mid * CONTROL_RATE)] < base * 0.2


def test_mute_policy_preroll_and_silence_during_statements(routing_project):
    p = routing_project
    p.music.soundbite_policy = "mute"
    p.music.mute_preroll = 0.4
    tl = compile_timeline(p)
    mix = build_mix(p, tl)
    env = 10 ** (mix.music_env_db / 20)
    for s, e in mix.statement_intervals:
        seg_env = env[int(s * CONTROL_RATE) + 1:int(e * CONTROL_RATE) - 1]
        assert np.max(seg_env) <= 1e-5
        # the pre-roll fades towards silence before the statement
        assert env[int((s - 0.05) * CONTROL_RATE)] < env[int((s - 0.38) * CONTROL_RATE)] + 1e-9


def test_peak_correction(routing_project):
    p = routing_project
    p.music.base_db = 0
    p.audio.narration_gain_db = 12
    tl = compile_timeline(p)
    mix = build_mix(p, tl)
    assert mix.peak_db <= p.audio.peak_target_db + 1e-6
    assert any(d.code == "peak_adjusted" for d in mix.diagnostics)
    p.audio.auto_fix_peaks = False
    mix2 = build_mix(p, tl)
    assert any(d.code == "clipping" for d in mix2.diagnostics)
