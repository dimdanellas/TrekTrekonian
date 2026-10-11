"""Narration: microphone recording, file import, continuous-take mapping and
TTS provider adapters (ElevenLabs, gTTS).

Every narration block gets its own PCM WAV (or an explicit range of a
continuous take).  Provider output is decoded with FFmpeg into a real PCM WAV;
renaming an MP3 to .wav never happens.  API keys are passed in per call and
never stored in the project.
"""
from __future__ import annotations

import io
import os
import queue
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .media import SAMPLE_RATE, MediaError, convert_to_wav, decode_audio, detect_silence_edges, media_duration
from .model import NarrationAudio, NarrationTake, Project
from .parser import Diagnostic
from .timeline import TimelineResult, block_hash, narration_audio_for
from .util import sha1_text

GTTS_NOTE = ("gTTS uses Google Translate's public speech endpoint: one voice per language, with an optional "
             "regional accent (top-level domain) and slow mode. It does not offer named voices.")
ONLINE_NOTE = "Online providers transmit the narration text to the provider's servers."


class TTSError(Exception):
    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind  # credentials | network | limit | voice | dependency | other


# ---------------------------------------------------------------------------
# TTS providers
# ---------------------------------------------------------------------------

class TTSProvider:
    name = "base"
    online = True

    def synthesize(self, text: str, out_wav: Path) -> Path:  # pragma: no cover - interface
        raise NotImplementedError

    def cache_key(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class GTTSProvider(TTSProvider):
    name = "gtts"

    def __init__(self, language: str = "el", tld: str = "com", slow: bool = False):
        self.language, self.tld, self.slow = language, tld, slow

    def cache_key(self, text: str) -> str:
        return sha1_text("gtts", text.strip(), self.language, self.tld, self.slow)

    def synthesize(self, text: str, out_wav: Path) -> Path:
        try:
            from gtts import gTTS
            from gtts.tts import gTTSError
        except ImportError as exc:
            raise TTSError("gTTS is not installed (pip install gTTS).", "dependency") from exc
        buf = io.BytesIO()
        try:
            gTTS(text=text, lang=self.language, tld=self.tld, slow=self.slow).write_to_fp(buf)
        except gTTSError as exc:
            msg = str(exc)
            kind = "limit" if "429" in msg else "network"
            raise TTSError(f"gTTS failed: {msg}", kind) from exc
        except ValueError as exc:  # unsupported language
            raise TTSError(f"gTTS: {exc}", "voice") from exc
        except Exception as exc:  # requests/urllib errors
            raise TTSError(f"gTTS connection error: {exc}", "network") from exc
        return _decode_bytes_to_wav(buf.getvalue(), ".mp3", out_wav)


class ElevenLabsProvider(TTSProvider):
    name = "elevenlabs"
    API = "https://api.elevenlabs.io/v1"

    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_multilingual_v2",
                 stability: float = 0.5, similarity_boost: float = 0.75, timeout: float = 60.0):
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self.stability, self.similarity_boost, self.timeout = stability, similarity_boost, timeout

    def cache_key(self, text: str) -> str:
        return sha1_text("elevenlabs", text.strip(), self.voice_id, self.model_id, self.stability, self.similarity_boost)

    def _check(self) -> None:
        if not self.api_key:
            raise TTSError("ElevenLabs API key is missing. Enter it in Automation → Narration.", "credentials")
        if not self.voice_id:
            raise TTSError("No ElevenLabs voice selected. Load voices and choose one.", "voice")

    @classmethod
    def _request(cls, method: str, url: str, api_key: str, timeout: float, **kw):
        try:
            import requests
        except ImportError as exc:
            raise TTSError("The 'requests' package is required for ElevenLabs.", "dependency") from exc
        headers = kw.pop("headers", {})
        headers["xi-api-key"] = api_key
        try:
            r = requests.request(method, url, headers=headers, timeout=timeout, **kw)
        except requests.RequestException as exc:
            raise TTSError(f"Cannot reach ElevenLabs: {exc.__class__.__name__}", "network") from exc
        if r.status_code == 401:
            raise TTSError("ElevenLabs rejected the API key (401).", "credentials")
        if r.status_code == 429:
            raise TTSError("ElevenLabs rate or quota limit reached (429).", "limit")
        if r.status_code in (400, 404, 422):
            detail = ""
            try:
                detail = str(r.json().get("detail", ""))[:200]
            except Exception:
                pass
            raise TTSError(f"ElevenLabs refused the request ({r.status_code}). {detail}".strip(), "voice")
        if r.status_code >= 400:
            raise TTSError(f"ElevenLabs error {r.status_code}.", "other")
        return r

    @classmethod
    def list_voices(cls, api_key: str, timeout: float = 30.0) -> list[tuple[str, str]]:
        if not api_key:
            raise TTSError("ElevenLabs API key is missing.", "credentials")
        r = cls._request("GET", f"{cls.API}/voices", api_key, timeout)
        voices = r.json().get("voices", [])
        return [(v.get("voice_id", ""), v.get("name", "")) for v in voices if v.get("voice_id")]

    def synthesize(self, text: str, out_wav: Path) -> Path:
        self._check()
        r = self._request(
            "POST", f"{self.API}/text-to-speech/{self.voice_id}?output_format=mp3_44100_128", self.api_key,
            self.timeout, headers={"Content-Type": "application/json", "Accept": "audio/mpeg"},
            json={"text": text, "model_id": self.model_id,
                  "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity_boost}})
        if not r.content:
            raise TTSError("ElevenLabs returned empty audio.", "other")
        return _decode_bytes_to_wav(r.content, ".mp3", out_wav)


def _decode_bytes_to_wav(data: bytes, suffix: str, out_wav: Path) -> Path:
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            return convert_to_wav(tmp, out_wav, SAMPLE_RATE, channels=1)
        except MediaError as exc:
            raise TTSError(f"Provider audio could not be decoded: {exc}", "other") from exc
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def make_provider(project: Project, provider: str, api_key: str = "") -> TTSProvider:
    n = project.narration
    if provider == "elevenlabs":
        return ElevenLabsProvider(api_key, n.voice_id, n.model_id, n.stability, n.similarity_boost)
    if provider == "gtts":
        return GTTSProvider(n.language, n.gtts_tld, n.gtts_slow)
    raise TTSError(f"Unknown provider {provider!r}")


def upsert_block_audio(project: Project, na: NarrationAudio) -> None:
    project.narration.blocks = [b for b in project.narration.blocks if b.block_index != na.block_index]
    project.narration.blocks.append(na)
    project.narration.blocks.sort(key=lambda b: b.block_index)


def remove_block_audio(project: Project, block_index: int) -> None:
    project.narration.blocks = [b for b in project.narration.blocks if b.block_index != block_index]


def generate_tts_block(project: Project, block_index: int, text: str, provider: TTSProvider,
                       narration_dir: Path) -> NarrationAudio:
    """Synthesize one narration block (cached by text/provider/voice/settings)."""
    narration_dir.mkdir(parents=True, exist_ok=True)
    key = provider.cache_key(text)
    out = narration_dir / f"tts_{provider.name}_{key[:16]}.wav"
    if not out.exists():
        provider.synthesize(text, out)
    dur = media_duration(out)
    na = NarrationAudio(block_index=block_index, text_hash=block_hash(text), text=text, source="tts",
                        wav_path=str(out), provider=provider.name,
                        voice=getattr(provider, "voice_id", "") or getattr(provider, "language", ""), duration=dur)
    upsert_block_audio(project, na)
    return na


def import_block_file(project: Project, block_index: int, text: str, src: str | Path,
                      narration_dir: Path) -> NarrationAudio:
    """Import an existing narration recording for one block (decoded to PCM WAV)."""
    narration_dir.mkdir(parents=True, exist_ok=True)
    out = narration_dir / f"imported_{block_index + 1:02d}_{uuid.uuid4().hex[:6]}.wav"
    convert_to_wav(src, out, SAMPLE_RATE, channels=1)
    na = NarrationAudio(block_index=block_index, text_hash=block_hash(text), text=text, source="imported",
                        wav_path=str(out), duration=media_duration(out))
    upsert_block_audio(project, na)
    return na


def register_recording(project: Project, block_index: int, text: str, wav: Path) -> NarrationAudio:
    na = NarrationAudio(block_index=block_index, text_hash=block_hash(text), text=text, source="recorded",
                        wav_path=str(wav), duration=media_duration(wav))
    upsert_block_audio(project, na)
    return na


def import_take(project: Project, src: str | Path, narration_dir: Path) -> NarrationTake:
    narration_dir.mkdir(parents=True, exist_ok=True)
    tid = f"take_{uuid.uuid4().hex[:8]}"
    out = narration_dir / f"{tid}.wav"
    convert_to_wav(src, out, SAMPLE_RATE, channels=1)
    take = NarrationTake(id=tid, path=str(out), duration=media_duration(out))
    project.narration.takes.append(take)
    return take


def map_take_range(project: Project, block_index: int, text: str, take_id: str, tin: float, tout: float) -> NarrationAudio:
    take = next((t for t in project.narration.takes if t.id == take_id), None)
    if take is None:
        raise ValueError("Unknown take")
    if not (0 <= tin < tout <= take.duration + 1e-3):
        raise ValueError(f"Range {tin:.2f}–{tout:.2f}s is outside the take (0–{take.duration:.2f}s)")
    na = NarrationAudio(block_index=block_index, text_hash=block_hash(text), text=text, source="take",
                        take_id=take_id, take_in=tin, take_out=tout, duration=tout - tin)
    upsert_block_audio(project, na)
    return na


def validate_narration(project: Project, tl: TimelineResult) -> list[Diagnostic]:
    """Manual-mode assignment checks: unassigned blocks and overlapping take ranges."""
    diags: list[Diagnostic] = []
    blocks = [s for s in tl.segments if s.kind == "narration"]
    for s in blocks:
        if s.narration is None:
            diags.append(Diagnostic(
                "error", "narration_missing",
                f"Narration {s.block_index + 1} has no recording/TTS audio assigned.",
                s.char_start, s.char_end, s.name()))
    by_take: dict[str, list[NarrationAudio]] = {}
    current = {id(s.narration) for s in blocks if s.narration is not None}
    for na in project.narration.blocks:
        if na.source == "take" and id(na) in current:
            by_take.setdefault(na.take_id or "", []).append(na)
    for take_id, items in by_take.items():
        items.sort(key=lambda b: b.take_in)
        for a, b in zip(items, items[1:]):
            if b.take_in < a.take_out - 1e-3:
                diags.append(Diagnostic(
                    "error", "take_overlap",
                    f"Narration {a.block_index + 1} and {b.block_index + 1} use overlapping ranges of the same take.",
                    segment=f"Narration {b.block_index + 1}"))
    return diags


def load_block_audio(project: Project, na: NarrationAudio) -> np.ndarray:
    """Decode the referenced audio range (silence trims applied) as stereo float32 @48k."""
    if na.source == "take":
        take = next((t for t in project.narration.takes if t.id == na.take_id), None)
        if take is None:
            raise MediaError("Narration take is missing")
        start = na.take_in + na.trim_head
        dur = max(0.0, na.take_out - na.take_in - na.trim_head - na.trim_tail)
        return decode_audio(take.path, start, dur)
    if not na.wav_path:
        raise MediaError("Narration audio path is empty")
    dur = max(0.0, na.duration - na.trim_head - na.trim_tail)
    return decode_audio(na.wav_path, na.trim_head, dur)


def auto_trim(project: Project, na: NarrationAudio) -> tuple[float, float]:
    """Detect leading/trailing silence (reversible: stored as trims)."""
    saved = na.trim_head, na.trim_tail
    na.trim_head = na.trim_tail = 0.0
    try:
        audio = load_block_audio(project, na)
    finally:
        na.trim_head, na.trim_tail = saved
    head, tail = detect_silence_edges(audio)
    na.trim_head, na.trim_tail = head, tail
    return head, tail


def stale_blocks(project: Project, tl: TimelineResult) -> list[int]:
    return [s.block_index for s in tl.segments if s.kind == "narration" and s.stale]


# ---------------------------------------------------------------------------
# Microphone recording (sounddevice + soundfile)
# ---------------------------------------------------------------------------

def list_input_devices() -> list[tuple[int, str]]:
    try:
        import sounddevice as sd
    except Exception:
        return []
    try:
        devs = sd.query_devices()
    except Exception:
        return []
    return [(i, d["name"]) for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]


@dataclass
class RecorderEvent:
    kind: str  # level | stopped | error
    value: float = 0.0
    message: str = ""
    path: Optional[str] = None


class Recorder:
    """Records mono 48 kHz PCM WAV. Levels and completion arrive via ``events``.

    The input stream and file are always closed on stop, error or exit.
    """

    def __init__(self, out_path: Path, device: Optional[int] = None, samplerate: int = SAMPLE_RATE):
        self.out_path = Path(out_path)
        self.device = device
        self.samplerate = samplerate
        self.events: "queue.Queue[RecorderEvent]" = queue.Queue()
        self._stream = None
        self._file = None
        self._lock = threading.Lock()
        self.started_at = 0.0
        self.running = False

    def start(self) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
        except Exception as exc:
            raise MediaError(f"Recording needs sounddevice/soundfile with PortAudio: {exc}") from exc
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = sf.SoundFile(str(self.out_path), mode="w", samplerate=self.samplerate, channels=1,
                                  subtype="PCM_24", format="WAV")

        def callback(indata, frames, time_info, status):  # runs on the audio thread
            with self._lock:
                if self._file is not None:
                    self._file.write(indata.copy())
            peak = float(np.max(np.abs(indata))) if len(indata) else 0.0
            self.events.put(RecorderEvent("level", peak))

        try:
            self._stream = sd.InputStream(samplerate=self.samplerate, channels=1, dtype="float32",
                                          device=self.device, callback=callback)
            self._stream.start()
        except Exception as exc:
            self._close()
            try:
                os.unlink(self.out_path)
            except OSError:
                pass
            raise MediaError(f"Cannot open the microphone: {exc}") from exc
        self.started_at = time.monotonic()
        self.running = True

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.running else 0.0

    def _close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None

    def stop(self) -> Optional[Path]:
        was = self.running
        self.running = False
        self._close()
        if was:
            self.events.put(RecorderEvent("stopped", path=str(self.out_path)))
            return self.out_path
        return None


def play_audio_file(path: str | Path, start: float = 0.0, duration: Optional[float] = None) -> Callable[[], None]:
    """Play a file through the default output device; returns a stop function."""
    try:
        import sounddevice as sd
    except Exception as exc:
        raise MediaError(f"Audio playback unavailable: {exc}") from exc
    audio = decode_audio(path, start, duration)
    try:
        sd.play(audio, SAMPLE_RATE)
    except Exception as exc:
        raise MediaError(f"Audio playback failed: {exc}") from exc
    return sd.stop


def play_array(audio: np.ndarray) -> Callable[[], None]:
    try:
        import sounddevice as sd
    except Exception as exc:
        raise MediaError(f"Audio playback unavailable: {exc}") from exc
    try:
        sd.play(audio, SAMPLE_RATE)
    except Exception as exc:
        raise MediaError(f"Audio playback failed: {exc}") from exc
    return sd.stop
