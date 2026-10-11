# Automated News Package Editor

A Python/Tkinter desktop application for producing short political television news packages (normally 120 s). You supply the script and the media. The application binds script markers to clips, times the package, records or synthesises narration, mixes speech, music and SFX, places lower thirds, the watermark and Greek subtitles, previews the package, and renders verified deliverables with companion files.

It does not research, fact-check, write scripts, download footage or publish anything. The **AI Prompts** menu only stores editable text templates; it makes no API calls.

## Install

Requirements:

- Python 3.10 or later with Tkinter
- FFmpeg and ffprobe on `PATH`
- PortAudio, for the microphone and embedded audio preview

Tested with Python 3.12.3, Tk 8.6 and FFmpeg 6.1.1 on Linux.

**Linux (Debian/Ubuntu)**

```bash
sudo apt install python3-tk python3-venv ffmpeg libportaudio2
./install.sh                 # core
./install.sh --with-whisper  # also local Whisper transcription (faster-whisper)
./run.sh                     # or: ./run.sh path/to/project.newsproj.json
```

**macOS:** `brew install python-tk ffmpeg portaudio`, then `./install.sh` and `./run.sh`.

**Windows:** install Python from python.org (it includes Tkinter) and FFmpeg (`winget install Gyan.FFmpeg`). Then run `install.bat` (or `install.bat --with-whisper`) and `run.bat`.

Pinned, tested versions are listed in `requirements.txt` and `requirements-optional.txt`.

## Tablet (Android) version

A touch version for Android tablets shares this engine and the project format. It is built as a sideloadable APK with Buildozer, locally (`android/build_apk.sh`) or by the **Android APK** GitHub Actions workflow. See [docs/ANDROID.md](docs/ANDROID.md).

## Try the sample project

The `examples/demo/` folder contains a ready-to-open project made from synthetic media only: test patterns, tones and placeholder names. There is no real footage and there are no quotations.

```bash
./run.sh examples/demo/demo_project.newsproj.json
```

To regenerate the sample, run `python examples/build_demo.py examples/demo`. To regenerate and render it, add `--render web_horizontal`. The folder `examples/demo/exports/` holds a verified render of the sample with all its companion files.

## Workflow

1. **Settings → Library paths.** Set the folders for SFX, music, B-roll, images, soundbites and exports. These persist between launches in the per-user settings file.
2. **Script & timing** (left pane). Write or paste the script and insert markers with the toolbar. Counters update on every edit, including typing, paste, cut, undo/redo and project loading:
   - narration word count
   - WPM (120–160)
   - narration estimate
   - estimated total, and measured total once narration audio exists
   - word allowance
   - remaining budget

   When the package is over target, the script background turns `#4a2323` and the remaining-time label turns red. Both go back to normal when the overrun is fixed.
3. **Media & Properties → Assets.** Import files or add them from the libraries. Each asset gets a stable ID. Missing files can be relinked one at a time or automatically.
4. **Media & Properties → Markers.** Bind each marker to an asset. Settings per marker:
   - in/out points (`HH:MM:SS:FF` or seconds); trims are checked against the real media duration and the selected duration is shown immediately
   - resize override and crop framing
   - B-roll fill policy (loop or still hold) and ambience level
   - speech gain, a deliberately-silent flag and the language
   - lower-third name, role, timing and fade
   - card text
5. **Media & Properties → Overlays & Music.** Choose the watermark logo, the opening title, the music (explicit, or a seeded automatic choice that is saved), the base level, SFX placements and the export folder.
6. **Automation & Settings → Narration.**
   - *Manual:* record each block, import a file per block, or import one continuous take and map explicit in/out ranges.
   - *TTS:* gTTS or ElevenLabs, in Greek by default.
   - Changing a block's text marks its audio **stale**.
7. **Subtitles.** **Generate Subtitles** runs local Whisper on each trimmed statement. Review and correct the cues in **Edit Subtitles**, then tick *Reviewed*.
8. **Preview.** **Build preview** renders a low-resolution proxy through the final renderer, with play/pause, seek and frame stepping. **Inspect frame** renders one frame of the playhead at full resolution.
9. **Validate, then Render.** Validation runs the preflight, including a real encoder test. Rendering writes the deliverables listed under Export.

## Script grammar

| Marker | Meaning |
|---|---|
| `[BROLL_n]`, `[IMAGE_n]` | Visual for the following narration block. Adds no time on its own. |
| `[CARD_n]` | A title or data card you supply. It covers the following narration, or stands alone for its `card_seconds`. |
| `[SOUNDBITE_n]` | The trimmed statement, with its original audio. Narration stops before it and resumes after it. |
| `[STANDUP_n]` | An on-camera clip with its original audio and no narration. |
| `[PAUSE=1.5]` | An explicit pause, in seconds, that holds the current visual. |
| `[NOTE: …]` | An editorial note. It is never spoken, counted, sent to TTS or exported. |

**Legacy forms**

- `[INSERT_SOUNDBITE_n]`, `[INSERT_IMAGE_n]` and `[INSERT_BROLL_n]` are aliases of the canonical markers.
- `[START_SOUNDBITE_n] … [END_SOUNDBITE_n]` is also accepted. The text inside the pair is a reference transcript: it is flagged for review and never narrated.
- **Normalize legacy tags** rewrites all of these into canonical markers. The rewrite can be undone.

**Errors that block rendering:** a missing or mismatched END tag, a nested pair, a stray END tag and an unknown tag. When a pair is broken, the rest of the script is still read as narration rather than swallowed. Text in parentheses is ordinary narration.

**Word count:** a word is a run of Unicode letters or digits. A single apostrophe, hyphen or period between two runs keeps them as one word, so `Ε.Ε.` counts as 1. Markers and notes are excluded.

## Timing

```text
estimated_total = narration_words / wpm * 60 + Σ soundbite trims + Σ standalone cards + Σ pauses
available_words = floor(max(0, target − reserved) × wpm / 60)
```

- Recorded or TTS WAV durations replace the estimates. Changing the WPM changes only the estimates.
- Segments run in sequence and are rounded up to whole frames, so speech is never cut.
- An over-target package can still be edited and previewed. Export stays blocked until you shorten it or record a longer-duration override with a reason. The override is saved in the project and the manifest.
- Nothing is stretched, sped up, padded or trimmed automatically.

## Audio routing

- Narration is silent throughout statement and stand-up intervals. The renderer checks the actual audio intervals, not just whether tags are adjacent.
- B-roll audio is muted unless the marker sets an ambience level.
- Music has two policies:
  - **Duck** (the default): music drops `duck_db` (default 20 dB, ×0.1 amplitude) below the base level under *all* speech, with 100 ms attack and 300 ms release.
  - **Mute during soundbites**: music fades out over a configurable pre-roll before each statement and stays silent during it.
- Music loops with crossfades and fades out at the end.
- The peak target (default −1 dBFS) is enforced or reported.
- Loudness normalisation runs only if you enter a target LUFS. No station standard is assumed.

## Subtitles

- Whisper runs locally on each trimmed statement's own audio, never on the final mix. The default language is Greek. If the model is missing, the app says so; transcription never falls back to a cloud service.
- Cues have at most 2 lines of 37–42 characters and last 1–7 s (6 s by default). Fast cues, above 17 characters per second, are flagged.
- Line breaks fall at word boundaries and avoid leaving articles or prepositions at the end of a line.
- Long cues are split into separately timed cues. A cue too short to meet the minimum duration is flagged, never extended past the clip.
- Cue times are stored relative to the clip and offset by each statement's own timeline start.
- Captions sit just above the bottom of the 5 % safe area. They move above a lower third when the rendered boxes would collide, in both horizontal and vertical layouts.
- Burnt-in captions and the SRT/ASS sidecars come from the same reviewed cues.

## Export

| Profile | Container / video / audio |
|---|---|
| Television — ProRes | MOV, `prores_ks` profile 2 (ProRes 422), 10-bit 4:2:2, 1920×1080 25p, PCM 24-bit stereo 48 kHz |
| Television — DNxHD | MOV, `dnxhd` 120 Mb/s 4:2:2 1080p25 (36/120/185 accepted), PCM 24-bit stereo 48 kHz |
| Web/Social — Horizontal | MP4, `libx264` CRF 18, 4:2:0, 1920×1080 25p, AAC 192 kb/s stereo 48 kHz |
| Vertical Short | MP4, `libx264` CRF 20, 1080×1920 25p with blur padding, AAC 192 kb/s stereo 48 kHz |

You can override profile settings per project, including scan (progressive or interlaced TFF), bitrate and pixel format. Every combination is checked statically and then by a real two-frame FFmpeg encode before rendering. No profile is claimed to meet any particular control room's specification.

**How a render runs**

- It works on an immutable snapshot of the project, so edits made during the render apply to the next one.
- It writes to a hidden `.partial` file in the export folder.
- It verifies the result with ffprobe and a full decode, checking codec, size, frame rate, frame count, duration and audio.
- Only then does it rename the file. Existing files are never overwritten.
- Cancelling or a failure removes the temporary files and never shows a success message.

**Companion files**, named after the video file:

| File | Contents |
|---|---|
| `…_prompter.txt` | UTF-8 narration only, in order, with paragraph breaks kept. |
| `…_rundown.csv` | `title`, `duration_seconds`, `lower_thirds`, `duration_status`, `timecode_duration`, `profile`, `filename`. The duration is the rendered, frame-derived duration. Exported before rendering, it is labelled estimated. |
| `…_segments.csv` | Each segment's start/end timecode, visual, audio type and lower third. |
| `.srt`, `.ass` | Subtitle sidecars in timeline timing. |
| `…_voice_over.wav` | Narration only, aligned to the programme timeline (silence during statements), PCM 24-bit 48 kHz. Optional per-block WAVs are also available. |
| `…_manifest.json` | Profile, final duration and frames, media and trim ranges, acknowledged warnings, audio peak and gain, the verification results and the output list. It contains no credentials or provider responses. |

## Credentials

The ElevenLabs key is typed into a masked field. It stays in memory, or in the OS keychain if you tick *Remember* and `keyring` is installed. It is never written to project files, exports or logs.

If ElevenLabs fails, the app asks before using gTTS. Both providers receive the narration text, and the app says so before sending it.

## Files and folders

| What | Where |
|---|---|
| Settings | `~/.config/news_package_editor/settings.json` on Linux, `%APPDATA%\news_package_editor` on Windows, `~/Library/Application Support/news_package_editor` on macOS |
| Autosave | `autosave/` inside the settings folder. Saved every 60 s while there are unsaved changes; offered for recovery at the next start. |
| Cache | `~/.cache/news_package_editor/` holds preview proxies and the Whisper transcript cache. Source media are never modified. |
| Narration WAVs | `<project name>_narration/` next to the project file |
| Project files | `*.newsproj.json`. Paths inside the project folder are stored as relative paths. See `docs/PROJECT_SCHEMA.md`. |

## Tests

```bash
./run_tests.sh            # 79 tests; GUI tests use xvfb-run on headless Linux
```

The tests build small synthetic fixtures with FFmpeg. They cover these acceptance areas:

- timing
- parser
- routing
- manual mapping and TTS
- media
- music
- captions
- UI and jobs
- persistence
- delivery: real renders in all four profiles, probed and fully decoded

## Known limitations

See `docs/IMPLEMENTATION_NOTES.md`. In short:

- ElevenLabs and gTTS were tested only against mocked network responses, because this build machine had no access to those services.
- Whisper was tested with a stubbed model, because the model download was blocked here.
- Microphone capture and audible playback were not tested on hardware, because the build machine has no audio devices.
