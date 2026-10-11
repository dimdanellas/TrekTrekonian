# Tablet (Android) build

The tablet version is a touch interface (Kivy) over the same engine as the desktop app. Projects are the same `.newsproj.json` files, so a package can move between a PC and the tablet.

## What it does

| Tab | Contents |
|---|---|
| Script | Script editor, marker buttons, live counters (words, estimate, totals, word allowance, remaining), red background when over target, diagnostics you can tap |
| Media | Import clips, stills, music, SFX and logos from the tablet's storage; scrub video, play audio, relink, find missing files |
| Markers | Bind each marker: media, in/out points, resize and crop framing, B-roll fill and ambience, speech gain, lower thirds, cards, and typed subtitles for statements |
| Narration | Record each block with the microphone, import a file, or generate gTTS / ElevenLabs speech |
| Package | Title, export profile and folder, look, watermark, opening title, music, subtitle options |
| Render | Validate, render single frames, build and play a low-resolution preview, render the package and open the results |

Not on the tablet: local Whisper transcription (type statement subtitles in Markers), continuous-take mapping, SFX placement and the AI prompt templates. Do those on the PC; the project keeps them.

## Building the APK

The APK is built on Linux (x86_64). Two ways:

**GitHub Actions.** Push the repository to GitHub and run the **Android APK** workflow (Actions tab → Android APK → Run workflow). It also runs on pushes to `main`/`master` that touch the app. Each successful build is published as the **tablet-apk** release (`NewsPackageEditor.apk`), so the tablet can download it straight from the repository's Releases page; the run also keeps it as the `news-package-editor-apk` artifact. The first run compiles Python, Kivy, NumPy, Pillow and FFmpeg for ARM and can take over an hour; the cache makes later runs much faster.

**Your own Linux machine.**

```bash
sudo apt install git zip unzip openjdk-17-jdk python3-venv autoconf automake libtool libltdl-dev \
  pkg-config cmake libffi-dev libssl-dev zlib1g-dev libncurses-dev lld patch
android/build_apk.sh
```

The APK appears in `bin/`. The script pins Buildozer 1.6.0 and python-for-android 2026.5.9.

The build targets 64-bit ARM (`arm64-v8a`), which covers current tablets. For an older 32-bit tablet, add `armeabi-v7a` to `android.archs` in `buildozer.spec`.

## Installing on the tablet

1. Copy the `.apk` to the tablet (USB, cloud drive, or download it on the tablet).
2. Open it. Android asks to allow installing apps from that source (the Files app or browser); allow it once.
3. On first start, allow **All files access** when asked, so projects can use media anywhere on the tablet. Allow the microphone when you first record.

Projects and exports default to `Documents/NewsPackages` on the tablet's storage.

## How the pieces fit

- `main.py` is the APK entry point. `newsedit/mobile/` holds the Kivy interface; the engine modules are shared with the desktop app.
- FFmpeg and ffprobe are compiled by python-for-android and packaged as `libffmpegbin.so` and `libffprobebin.so`, because Android only lets apps run programs from their native-library folder. `android/recipes/ffmpeg` adds ffprobe to the upstream recipe. `newsedit/mobile/platform.py` points the engine and MoviePy at them.
- Encoders available on the tablet: libx264 and AAC (web profiles), ProRes and DNxHD (television profiles), PCM.
- Titles and subtitles use a system font with Greek coverage (Roboto), with the copy bundled inside Kivy as a fallback.

## Testing on a PC

`pip install kivy==2.3.1` and run `python main.py examples/demo/demo_project.newsproj.json`. `tests/test_mobile.py` drives a full tablet session, including a verified render, under `xvfb-run`.
