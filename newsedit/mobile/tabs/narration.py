"""Narration per block: record with the microphone, import a file, or text-to-speech."""
from __future__ import annotations

import json
import time
from typing import Optional

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.progressbar import ProgressBar

from ...config import AppConfig
from ...media import MediaError
from ...model import NarrationAudio
from ...narration import import_block_file, make_provider, remove_block_audio, upsert_block_audio
from ...timeline import block_hash
from .. import platform as plat
from ..widgets import (AMBER, DIM, GREEN, RED, FilePicker, Form, button, checkbox, confirm, heading, hbox, label,
                       message, scroll, spinner, text_input, vstack)
from .base import Tab

PROVIDERS = {"gtts": "gTTS (Google)", "elevenlabs": "ElevenLabs"}
MODES = {"manual": "Manual recording", "tts": "Text-to-speech"}


def _key_file():
    return AppConfig.path().with_name("elevenlabs_key.json")


class NarrationTab(Tab):
    def __init__(self, app):
        super().__init__(app, orientation="horizontal", spacing=dp(8))
        self._loading = False
        self.recorder = None
        self._rec_block: Optional[tuple[int, str]] = None
        self._rec_ev = None
        self._voices: dict[str, str] = {}

        settings = Form(label_width=dp(170))
        settings.full(heading("Narration settings"))
        self.mode = settings.row("Mode", spinner(MODES.values(), MODES["manual"], self._commit))
        self.provider = settings.row("TTS provider", spinner(PROVIDERS.values(), PROVIDERS["gtts"], self._commit))
        self.lang = settings.row("Language", spinner(["el", "en", "fr", "de", "it", "es", "tr"], "el", self._commit))
        self.tld = settings.row("gTTS accent (tld)", text_input("com", self._commit), "com, gr …")
        settings.full(heading("ElevenLabs"))
        self.key = settings.row("API key", text_input("", self._key_changed, password=True))
        self.remember = settings.row("", checkbox("Remember on this tablet", False, self._remember_changed))
        self.voice = settings.row("Voice", spinner([], "(load voices)", self._voice_picked))
        settings.full(hbox(button("Load voices", self.load_voices)))
        settings.full(label("Text sent for synthesis leaves the tablet. The key is only sent to ElevenLabs.",
                            color=DIM))
        left = scroll(settings)
        left.size_hint_x = 0.38
        self.add_widget(left)

        right = BoxLayout(orientation="vertical", size_hint_x=0.62, spacing=dp(4))
        right.add_widget(hbox(button("Generate TTS for missing/stale", lambda: self.generate_tts(None)),
                              button("Stop audio", self.app.stop_audio)))
        self.rec_lbl = label("", color=AMBER)
        self.level = ProgressBar(max=1.0, value=0, size_hint_y=None, height=dp(12))
        right.add_widget(self.rec_lbl)
        right.add_widget(self.level)
        self.blocks = vstack(spacing=dp(6))
        right.add_widget(scroll(self.blocks))
        self.add_widget(right)
        self._load_saved_key()

    # ------------------------------------------------------------------ settings
    def load(self) -> None:
        self._loading = True
        n = self.app.project.narration
        self.mode.text = MODES.get(n.mode, MODES["manual"])
        self.provider.text = PROVIDERS.get(n.provider, PROVIDERS["gtts"])
        self.lang.text = n.language
        self.tld.text = n.gtts_tld
        self.voice.text = n.voice_name or (n.voice_id or "(load voices)")
        self._loading = False

    def _commit(self, *_):
        if self._loading:
            return
        inv_m = {v: k for k, v in MODES.items()}
        inv_p = {v: k for k, v in PROVIDERS.items()}

        def apply(p):
            n = p.narration
            n.mode = inv_m.get(self.mode.text, "manual")
            n.provider = inv_p.get(self.provider.text, "gtts")
            n.language = self.lang.text
            n.gtts_tld = self.tld.text.strip() or "com"

        self.app.set_value(apply)

    def _voice_picked(self, name):
        if self._loading or name not in self._voices:
            return

        def apply(p):
            p.narration.voice_id = self._voices[name]
            p.narration.voice_name = name

        self.app.set_value(apply)

    def _key_changed(self, v):
        self.app.api_key = v.strip()
        if self.remember.checkbox.active:
            self._store_key()

    def _remember_changed(self, on):
        if self._loading:
            return
        if on:
            self._store_key()
        else:
            try:
                _key_file().unlink()
            except OSError:
                pass

    def _store_key(self):
        try:
            f = _key_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"api_key": self.app.api_key}), encoding="utf-8")
            f.chmod(0o600)
        except OSError as exc:
            self.app.status(f"Key not stored: {exc}")

    def _load_saved_key(self):
        try:
            key = json.loads(_key_file().read_text(encoding="utf-8")).get("api_key", "")
        except (OSError, ValueError):
            return
        self._loading = True
        self.app.api_key = key
        self.key.text = key
        self.remember.checkbox.active = True
        self._loading = False

    def load_voices(self):
        from ...narration import ElevenLabsProvider

        key = self.app.api_key
        if not key:
            message("ElevenLabs", "Enter your ElevenLabs API key first.")
            return

        def done(voices):
            self._voices = {name: vid for vid, name in voices}
            self.voice.values = list(self._voices)
            self.app.status(f"{len(voices)} voices loaded")

        self.app.start_job("voices", "Loading ElevenLabs voices",
                           lambda cancel, progress: ElevenLabsProvider.list_voices(key), on_done=done)

    # ------------------------------------------------------------------ blocks
    def refresh(self, full: bool) -> None:
        tl = self.app.timeline
        segs = [s for s in tl.segments if s.kind == "narration"]
        self.blocks.clear_widgets()
        if not segs:
            self.blocks.add_widget(label("No narration blocks: write narration text in the script.", color=DIM))
            return
        for s in segs:
            self.blocks.add_widget(self._block_card(s))

    def _block_card(self, s):
        na = s.narration
        if na is None:
            status, color = "no audio (estimated)", DIM
        elif s.stale:
            status, color = "STALE: text changed since this audio", RED
        else:
            status, color = f"{na.source}, {na.duration - na.trim_head - na.trim_tail:.1f} s", GREEN
        box = vstack(spacing=dp(2), padding=dp(6))
        preview = s.text if len(s.text) < 220 else s.text[:217] + "…"
        box.add_widget(label(f"[b]Block {s.block_index + 1}[/b]  ·  {s.words} words  ·  "
                             f"[color={'%02x%02x%02x' % tuple(int(c * 255) for c in color[:3])}]{status}[/color]",
                             markup=True))
        box.add_widget(label(preview, color=DIM))
        idx, text = s.block_index, s.text
        recording = self._rec_block is not None and self._rec_block[0] == idx
        rec = button("Stop recording" if recording else "Record",
                     self.record_stop if recording else (lambda: self.record_start(idx, text)))
        if self._rec_block is not None and not recording:
            rec.disabled = True
        row = hbox(rec, button("Import…", lambda: self.import_file(idx, text)),
                   button("TTS", lambda: self.generate_tts([idx])))
        play = button("Play", lambda: self.app.play_narration(na))
        play.disabled = na is None
        clear = button("Clear", lambda: self.clear(idx))
        clear.disabled = na is None
        row.add_widget(play)
        row.add_widget(clear)
        box.add_widget(row)
        return box

    def _narr_text(self, idx: int) -> Optional[str]:
        seg = next((s for s in self.app.timeline.segments if s.kind == "narration" and s.block_index == idx), None)
        return seg.text if seg else None

    def clear(self, idx: int) -> None:
        confirm("Clear narration", f"Remove the audio of block {idx + 1} from the project? (The file stays on disk.)",
                lambda: self.app.set_value(lambda p: remove_block_audio(p, idx)), yes="Clear")

    def import_file(self, idx: int, text: str) -> None:
        from ...media import AUDIO_EXT, VIDEO_EXT

        def chosen(files):
            try:
                import_block_file(self.app.project, idx, text, files[0], self.app.narration_dir())
            except MediaError as exc:
                message("Import narration", str(exc))
                return
            self.app.mark_dirty()
            self.app.schedule_refresh()

        FilePicker(f"Narration for block {idx + 1}", chosen, exts=AUDIO_EXT | VIDEO_EXT, start=self.app.last_folder())

    # -- recording ---------------------------------------------------------------
    def record_start(self, idx: int, text: str) -> None:
        if self.recorder is not None:
            return

        def granted(ok: bool):
            if not ok:
                message("Record", "Microphone permission was not granted.")
                return
            stem = self.app.narration_dir() / f"rec_{idx + 1:02d}_{int(time.time())}"
            try:
                rec = plat.make_recorder(stem)
                rec.start()
            except Exception as exc:  # MediaError, Java exceptions, missing PortAudio
                message("Record", f"Recording could not start: {exc}")
                return
            self.recorder = rec
            self._rec_block = (idx, text)
            self._rec_ev = Clock.schedule_interval(self._poll_rec, 0.1)
            self.refresh(False)

        plat.ensure_microphone(lambda ok: Clock.schedule_once(lambda *_: granted(ok), 0))

    def _poll_rec(self, *_):
        r = self.recorder
        if r is None:
            return False
        peak = r.level()
        self.level.value = peak
        self.rec_lbl.text = (f"Recording block {self._rec_block[0] + 1}: {r.elapsed():.1f} s"
                             + ("   CLIPPING!" if peak >= 0.99 else ""))
        return True

    def record_stop(self) -> None:
        r = self.recorder
        if r is None:
            return
        self.recorder = None
        if self._rec_ev is not None:
            self._rec_ev.cancel()
        idx, text = self._rec_block
        self._rec_block = None
        self.level.value = 0
        try:
            path = r.stop()
        except Exception as exc:
            message("Record", f"The recording failed: {exc}")
            self.refresh(False)
            return
        if path is None:
            message("Record", "Nothing was recorded.")
            self.refresh(False)
            return
        try:
            na = import_block_file(self.app.project, idx, text, path, self.app.narration_dir())
        except MediaError as exc:
            message("Record", f"The recording could not be read: {exc}")
            return
        na.source = "recorded"
        self.rec_lbl.text = f"Recorded block {idx + 1}: {na.duration:.1f} s"
        self.app.mark_dirty()
        self.app.schedule_refresh()

    # -- TTS -----------------------------------------------------------------------
    def generate_tts(self, blocks: Optional[list[int]], provider: Optional[str] = None) -> None:
        app = self.app
        app.recompute()
        segs = [s for s in app.timeline.segments if s.kind == "narration"]
        if blocks is None:
            segs = [s for s in segs if s.narration is None or s.stale]
        else:
            segs = [s for s in segs if s.block_index in blocks]
        if not segs:
            message("Narration", "Every narration block already has current audio.")
            return
        provider = provider or app.project.narration.provider
        if provider == "elevenlabs" and not (app.api_key and app.project.narration.voice_id):
            message("ElevenLabs", "Enter the API key and choose a voice first, or switch the provider to gTTS.")
            return
        prov = make_provider(app.project, provider, app.api_key)
        ndir = app.narration_dir()
        items = [(s.block_index, s.text) for s in segs]

        def work(cancel, progress):
            from ...media import media_duration

            out = []
            ndir.mkdir(parents=True, exist_ok=True)
            for k, (idx, text) in enumerate(items):
                if cancel():
                    break
                progress(f"TTS block {idx + 1} ({k + 1}/{len(items)})", k / len(items))
                wav = ndir / f"tts_{prov.name}_{prov.cache_key(text)[:16]}.wav"
                if not wav.exists():
                    prov.synthesize(text, wav)
                out.append((idx, text, str(wav), media_duration(wav)))
            return out

        def done(results):
            for idx, text, wav, dur in results:
                upsert_block_audio(app.project, NarrationAudio(
                    idx, block_hash(text), text, "tts", wav, provider=prov.name,
                    voice=getattr(prov, "voice_id", "") or getattr(prov, "language", ""), duration=dur))
            app.mark_dirty()
            app.schedule_refresh()
            app.status(f"Generated narration for {len(results)} block(s).")

        def fail(msg):
            if provider == "elevenlabs":
                confirm("ElevenLabs failed", f"{msg}\n\nUse gTTS instead? The narration text will be sent to "
                                             "Google's speech service.",
                        lambda: self.generate_tts(blocks, "gtts"), yes="Use gTTS")
                return True
            return False

        name = "ElevenLabs" if provider == "elevenlabs" else "Google (gTTS)"
        confirm("Online text-to-speech", f"{len(segs)} narration block(s) will be sent to {name} for synthesis. "
                                         "Continue?",
                lambda: app.start_job("tts", "Generating narration", work, done, fail), yes="Continue")

