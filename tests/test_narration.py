import pytest

from newsedit.media import ffmpeg_bin, probe, run, verify_pcm_wav
from newsedit.model import Project
from newsedit.narration import (ElevenLabsProvider, GTTSProvider, TTSError, TTSProvider, auto_trim,
                                generate_tts_block, import_take, map_take_range, validate_narration)
from newsedit.parser import parse_script
from newsedit.timeline import compile_timeline


class FakeMp3Provider(TTSProvider):
    """Returns real MP3 bytes, like gTTS/ElevenLabs do."""
    name = "fake"

    def __init__(self, tmp):
        self.tmp = tmp
        self.calls = 0

    def cache_key(self, text):
        return "k" + str(abs(hash(text)))

    def synthesize(self, text, out_wav):
        from newsedit.narration import _decode_bytes_to_wav

        self.calls += 1
        mp3 = self.tmp / "x.mp3"
        run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1.2", str(mp3)])
        return _decode_bytes_to_wav(mp3.read_bytes(), ".mp3", out_wav)


def test_tts_output_is_real_pcm_wav_and_cached(tmp_path):
    p = Project(script="[BROLL_1]\nΚείμενο για σύνθεση.")
    blk = parse_script(p.script).narration_blocks[0]
    prov = FakeMp3Provider(tmp_path)
    na = generate_tts_block(p, 0, blk.text, prov, tmp_path / "narr")
    with open(na.wav_path, "rb") as fh:
        assert fh.read(4) == b"RIFF"
    verify_pcm_wav(na.wav_path)
    assert probe(na.wav_path)["sample_rate"] == 48000
    generate_tts_block(p, 0, blk.text, prov, tmp_path / "narr")
    assert prov.calls == 1  # cached by text/provider/settings


def test_fake_wav_is_rejected(tmp_path):
    from newsedit.media import MediaError

    mp3 = tmp_path / "speech.mp3"
    run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1", str(mp3)])
    fake = tmp_path / "voice_over.wav"
    fake.write_bytes(mp3.read_bytes())  # an MP3 renamed to .wav
    with pytest.raises(MediaError):
        verify_pcm_wav(fake)


def test_missing_credentials_are_recoverable_errors(tmp_path):
    with pytest.raises(TTSError) as e:
        ElevenLabsProvider("", "voice").synthesize("γεια", tmp_path / "a.wav")
    assert e.value.kind == "credentials"
    with pytest.raises(TTSError) as e:
        ElevenLabsProvider.list_voices("")
    assert e.value.kind == "credentials"


def test_gtts_network_failure_is_recoverable(tmp_path, monkeypatch):
    import gtts

    def boom(self, fp):
        raise gtts.tts.gTTSError("Failed to connect")

    monkeypatch.setattr(gtts.gTTS, "write_to_fp", boom)
    with pytest.raises(TTSError) as e:
        GTTSProvider().synthesize("γεια", tmp_path / "a.wav")
    assert e.value.kind in ("network", "limit")


def test_continuous_take_requires_explicit_ranges(media, tmp_path):
    p = Project(script="[BROLL_1]\nΠρώτο.\n[SOUNDBITE_1]\n[BROLL_1]\nΔεύτερο.")
    take = import_take(p, media["music"], tmp_path / "narr")
    tl = compile_timeline(p)
    diags = validate_narration(p, tl)
    assert sum(d.code == "narration_missing" for d in diags) == 2  # nothing split automatically
    blocks = parse_script(p.script).narration_blocks
    map_take_range(p, 0, blocks[0].text, take.id, 0.0, 2.0)
    map_take_range(p, 1, blocks[1].text, take.id, 1.5, 3.0)  # overlaps the first range
    tl = compile_timeline(p)
    diags = validate_narration(p, tl)
    assert any(d.code == "take_overlap" for d in diags)
    map_take_range(p, 1, blocks[1].text, take.id, 2.0, 3.5)
    tl = compile_timeline(p)
    assert not validate_narration(p, tl)
    assert tl.segments[0].seconds == 2.0 and tl.segments[2].seconds == 1.5
    with pytest.raises(ValueError):
        map_take_range(p, 1, blocks[1].text, take.id, 5.0, 99.0)


def test_silence_trim_is_reversible_and_keeps_speech(media, tmp_path):
    from newsedit.narration import register_recording

    p = Project(script="[BROLL_1]\nΚείμενο.")
    blk = parse_script(p.script).narration_blocks[0]
    na = register_recording(p, 0, blk.text, media["n1"])  # 0.3 s silence lead/tail
    head, tail = auto_trim(p, na)
    assert 0.1 < head < 0.3 and 0.1 < tail < 0.3
    tl = compile_timeline(p)
    assert tl.segments[0].seconds == pytest.approx(na.duration - head - tail)
    na.trim_head = na.trim_tail = 0
    assert compile_timeline(p).segments[0].seconds == pytest.approx(na.duration)
