# Implementation notes

This file records how the brief's eight phases were implemented, the decisions made where the brief left room, and what could not be verified on the build machine.

## Phase checkpoints

| Phase | Delivered | Verified by |
|---|---|---|
| 1. Skeleton, model, persistence | `model.py`, `config.py`, `util.py`; atomic saves, typed loading, relative paths, autosave and recovery | `tests/test_persistence.py` |
| 2. Script parser and timing | `parser.py`, `textutil.py`, `timeline.py`; canonical and legacy markers, diagnostics with character offsets, live counters, overrun colour | `tests/test_parser.py`, `tests/test_timing.py`, GUI tests |
| 3. Media library and bindings | `media.py`, `assets.py`; ffprobe/Pillow probing (rotation, EXIF), trims checked against real duration, relinking | `tests/test_media.py`, GUI trim test |
| 4. Narration | `narration.py`; recording, per-block import, continuous takes with explicit ranges, gTTS and ElevenLabs, stale detection, silence trim | `tests/test_narration.py`, `tests/test_providers.py` (mocked) |
| 5. Audio mix | `audio.py`; routing, ducking or mute policy, music loop with crossfades, SFX, peak target, optional LUFS | `tests/test_audio.py` |
| 6. Visuals and subtitles | `visuals.py`, `subtitles.py`; resize modes, lower thirds, watermark, title, cards, Greek-aware wrapping, collision-free placement, SRT/ASS | `tests/test_subtitles.py`, delivery tests |
| 7. Preview | `preview.py`; proxy built by the final renderer, embedded player, frame inspection, external player | GUI preview test |
| 8. Export and delivery | `render.py`, `export.py`, `preflight.py`, `profiles.py`; four profiles, encoder test, `.partial` + verification + rename, companion files, manifest | `tests/test_delivery.py`, GUI render/cancel test |

## Decisions

- **Encoding.** MoviePy 2.x composes the frames, but frames are piped straight into FFmpeg. MoviePy's `write_videofile` forces options (`-preset`, an alpha pixel format) that are invalid for ProRes and DNxHD, so the app builds the FFmpeg command itself from the profile.
- **One timeline for everything.** The preview, the mix, captions, the rundown, segments CSV and the manifest all come from one compiled, frame-quantised timeline. Each segment is rounded up to whole frames so speech is never cut.
- **Voice-over WAV.** `…_voice_over.wav` is aligned to the programme timeline (silence during statements), so an editor can drop it on a track at zero. Per-block WAVs are optional.
- **Fade transition.** The optional fade is a fade through black, over `fade_frames`, so it never changes the timeline duration.
- **Extra markers.** `[CARD_n]`, `[STANDUP_n]` and `[PAUSE=x]` were added because the brief's package structure needs them; they follow the same grammar and diagnostics as the rest.
- **Narration staleness.** Each block's audio is keyed by a hash of its text. Edited text marks the audio stale rather than discarding it.
- **Background jobs.** Long work (TTS, transcription, preview, render) runs on worker threads that post events to a queue; the Tk main thread polls it. The UI never blocks, and renders work on an immutable snapshot.
- **Credentials.** The ElevenLabs key lives in memory or, if the user opts in, in the OS keychain via `keyring`. It is sent only in the request header.

## Limitations

- **Not tested live:** ElevenLabs and gTTS (network blocked here; tested with mocked HTTP), Whisper models (download blocked; tested with a stub model), microphone capture and audible playback (no audio devices; the player falls back to a silent clock and says so).
- **Interlaced output.** Interlaced TFF is offered as an override and passes the encoder test, but field order was checked only by ffprobe flags, not on a broadcast monitor.
- **Broadcast compliance.** No profile is claimed to meet a particular station's delivery specification. Check with the control room.
- **Speed.** Full-HD renders run at roughly 20 frames per second on the build machine (a 2-minute package takes about 2–3 minutes).
