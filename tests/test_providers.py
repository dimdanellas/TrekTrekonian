"""Provider and transcription adapters with the network/model mocked."""
import sys
import types

import pytest

from newsedit.narration import ElevenLabsProvider, TTSError


class FakeResp:
    def __init__(self, status, content=b"", js=None):
        self.status_code, self.content, self._js = status, content, js or {}

    def json(self):
        return self._js


@pytest.mark.parametrize("status,kind", [(401, "credentials"), (429, "limit"), (422, "voice"), (500, "other")])
def test_elevenlabs_errors_map_to_recoverable_kinds(monkeypatch, tmp_path, status, kind):
    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResp(status))
    with pytest.raises(TTSError) as e:
        ElevenLabsProvider("key", "voice").synthesize("γεια", tmp_path / "x.wav")
    assert e.value.kind == kind


def test_elevenlabs_network_error(monkeypatch, tmp_path):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(TTSError) as e:
        ElevenLabsProvider("key", "voice").synthesize("γεια", tmp_path / "x.wav")
    assert e.value.kind == "network"


def test_elevenlabs_success_decodes_mp3_and_sends_key_only_in_header(monkeypatch, tmp_path):
    import requests

    from newsedit.media import ffmpeg_bin, run, verify_pcm_wav

    mp3 = tmp_path / "a.mp3"
    run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1", str(mp3)])
    seen = {}

    def fake(method, url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return FakeResp(200, mp3.read_bytes())

    monkeypatch.setattr(requests, "request", fake)
    out = ElevenLabsProvider("SECRET", "v1").synthesize("Καλησπέρα", tmp_path / "o.wav")
    verify_pcm_wav(out)
    assert seen["headers"]["xi-api-key"] == "SECRET"
    assert "SECRET" not in seen["url"] and "SECRET" not in str(seen["json"])


def test_list_voices(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResp(
        200, js={"voices": [{"voice_id": "abc", "name": "Ελένη"}]}))
    assert ElevenLabsProvider.list_voices("k") == [("abc", "Ελένη")]


def test_transcription_is_local_clip_relative_and_cached(monkeypatch, media, tmp_path):
    """faster-whisper is replaced by a stub; we check what audio it receives and the timing."""
    from newsedit import subtitles as S
    from newsedit.media import probe

    received = {}

    class W:
        def __init__(self, start, end, word):
            self.start, self.end, self.word = start, end, word

    class Seg:
        start, end, text = 0.2, 1.4, " Γεια σας κόσμε"
        words = [W(0.2, 0.5, " Γεια"), W(0.5, 0.9, " σας"), W(0.9, 1.4, " κόσμε")]

    class FakeModel:
        def __init__(self, name, device="cpu", compute_type="int8"):
            received["model"] = (name, device)

        def transcribe(self, wav, **kw):
            received["duration"] = probe(wav)["duration"]
            received["kw"] = kw
            return iter([Seg()]), None

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    segs = S.transcribe_clip(str(media["sb_a"]), 0.5, 2.5, "el", "tiny", "cpu", tmp_path)
    assert abs(received["duration"] - 2.0) < 0.05  # only the trimmed clip was transcribed
    assert received["kw"]["language"] == "el" and received["model"] == ("tiny", "cpu")
    assert segs[0]["start"] == 0.2 and segs[0]["words"][0][2] == "Γεια"
    received.clear()
    again = S.transcribe_clip(str(media["sb_a"]), 0.5, 2.5, "el", "tiny", "cpu", tmp_path)
    assert again == segs and not received  # served from cache


def test_missing_whisper_backend_message(monkeypatch, media, tmp_path):
    from newsedit import subtitles as S

    monkeypatch.setattr(S, "whisper_backend", lambda: None)
    with pytest.raises(S.TranscriptionError, match="No local Whisper"):
        S.transcribe_clip(str(media["sb_a"]), 0.0, 1.0, "el", "tiny", "cpu", tmp_path / "c2")
