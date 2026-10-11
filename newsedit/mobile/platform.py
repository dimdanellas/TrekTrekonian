"""Android integration with desktop fallbacks.

Everything that differs between a tablet and a desktop lives here: where
FFmpeg is, which folders the app may use, storage and microphone
permissions, recording, and handing a file to another app.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ

# The FFmpeg executables are packaged as native "libraries" so Android extracts
# them to the app's native library folder, the only place it allows exec().
FFMPEG_LIB = "libffmpegbin.so"
FFPROBE_LIB = "libffprobebin.so"


def _activity():
    from jnius import autoclass  # type: ignore

    return autoclass("org.kivy.android.PythonActivity").mActivity


def native_lib_dir() -> Optional[str]:
    if not IS_ANDROID:
        return None
    try:
        return str(_activity().getApplicationInfo().nativeLibraryDir)
    except Exception:
        return None


def configure_environment() -> list[str]:
    """Point the engine at the bundled FFmpeg and at app-private folders.

    Must run before the engine or MoviePy is imported.  Returns problems found.
    """
    problems: list[str] = []
    if not IS_ANDROID:
        return problems
    private = Path(os.environ.get("ANDROID_PRIVATE") or Path.home())
    os.environ.setdefault("NEWSEDIT_CONFIG_DIR", str(private / "config"))
    os.environ.setdefault("NEWSEDIT_CACHE_DIR", str(private / "cache"))
    os.environ.setdefault("TMPDIR", str(private / "tmp"))
    Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    libdir = native_lib_dir()
    if not libdir:
        problems.append("The app's native library folder could not be found; FFmpeg is unavailable.")
        return problems
    ffmpeg = os.path.join(libdir, FFMPEG_LIB)
    ffprobe = os.path.join(libdir, FFPROBE_LIB)
    for exe in (ffmpeg, ffprobe):
        if not os.path.exists(exe):
            problems.append(f"{os.path.basename(exe)} is missing from the APK.")
    os.environ["NEWSEDIT_FFMPEG"] = ffmpeg
    os.environ["NEWSEDIT_FFPROBE"] = ffprobe
    os.environ["FFMPEG_BINARY"] = ffmpeg  # MoviePy uses it directly (no imageio-ffmpeg download)
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg
    # The executables link against libavcodec etc. in the same folder.
    os.environ["LD_LIBRARY_PATH"] = libdir + (":" + os.environ["LD_LIBRARY_PATH"]
                                              if os.environ.get("LD_LIBRARY_PATH") else "")
    return problems


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def storage_root() -> str:
    if IS_ANDROID:
        try:
            from android.storage import primary_external_storage_path  # type: ignore

            return primary_external_storage_path()
        except Exception:
            return "/storage/emulated/0"
    return str(Path.home())


def default_work_dir() -> str:
    """Suggested folder for projects and exports."""
    if IS_ANDROID:
        return os.path.join(storage_root(), "Documents", "NewsPackages")
    return str(Path.home())


def quick_places() -> list[tuple[str, str]]:
    root = storage_root()
    if IS_ANDROID:
        cands = [("Storage", root), ("Projects", default_work_dir()), ("Download", f"{root}/Download"),
                 ("Movies", f"{root}/Movies"), ("Music", f"{root}/Music"), ("DCIM", f"{root}/DCIM"),
                 ("Recordings", f"{root}/Recordings")]
    else:
        home = str(Path.home())
        cands = [("Home", home), ("Videos", f"{home}/Videos"), ("Music", f"{home}/Music"),
                 ("Downloads", f"{home}/Downloads")]
    return [(n, p) for n, p in cands if os.path.isdir(p) or n in ("Projects",)]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def has_all_files_access() -> bool:
    if not IS_ANDROID:
        return True
    try:
        from jnius import autoclass  # type: ignore

        version = autoclass("android.os.Build$VERSION")
        if version.SDK_INT >= 30:
            return bool(autoclass("android.os.Environment").isExternalStorageManager())
        from android.permissions import Permission, check_permission  # type: ignore

        return check_permission(Permission.WRITE_EXTERNAL_STORAGE)
    except Exception:
        return False


def request_all_files_access() -> None:
    """Shared-storage access so projects can reference media anywhere on the tablet.

    Android 11+ grants this on a system settings page ("All files access");
    older versions show the usual permission prompt.
    """
    if not IS_ANDROID:
        return
    from jnius import autoclass  # type: ignore

    version = autoclass("android.os.Build$VERSION")
    if version.SDK_INT >= 30:
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")
        act = _activity()
        intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + act.getPackageName()))
        act.startActivity(intent)
    else:
        from android.permissions import Permission, request_permissions  # type: ignore

        request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])


def ensure_microphone(callback: Callable[[bool], None]) -> None:
    if not IS_ANDROID:
        callback(True)
        return
    from android.permissions import Permission, check_permission, request_permissions  # type: ignore

    if check_permission(Permission.RECORD_AUDIO):
        callback(True)
        return

    def done(perms, grants):
        callback(bool(grants) and all(grants))

    request_permissions([Permission.RECORD_AUDIO], done)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class AndroidRecorder:
    """Records the microphone to AAC/M4A with Android's MediaRecorder."""

    suffix = ".m4a"

    def __init__(self, out_path: Path):
        self.out_path = Path(out_path)
        self._rec = None
        self._t0 = 0.0

    def start(self) -> None:
        from jnius import autoclass  # type: ignore

        MediaRecorder = autoclass("android.media.MediaRecorder")
        AudioSource = autoclass("android.media.MediaRecorder$AudioSource")
        OutputFormat = autoclass("android.media.MediaRecorder$OutputFormat")
        AudioEncoder = autoclass("android.media.MediaRecorder$AudioEncoder")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        r = MediaRecorder()
        r.setAudioSource(AudioSource.MIC)
        r.setOutputFormat(OutputFormat.MPEG_4)
        r.setAudioEncoder(AudioEncoder.AAC)
        r.setAudioChannels(1)
        r.setAudioSamplingRate(48000)
        r.setAudioEncodingBitRate(192000)
        r.setOutputFile(str(self.out_path))
        r.prepare()
        r.start()
        self._rec = r
        self._t0 = time.monotonic()

    def level(self) -> float:
        try:
            return min(1.0, self._rec.getMaxAmplitude() / 32767.0) if self._rec else 0.0
        except Exception:
            return 0.0

    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._rec else 0.0

    def stop(self) -> Optional[Path]:
        r, self._rec = self._rec, None
        if r is None:
            return None
        try:
            r.stop()
        finally:
            r.release()
        return self.out_path if self.out_path.exists() else None


class DesktopRecorder:
    """Wraps the desktop recorder (sounddevice/PortAudio) for testing on a PC."""

    suffix = ".wav"

    def __init__(self, out_path: Path):
        from ..narration import Recorder

        self._r = Recorder(Path(out_path))
        self._peak = 0.0

    def start(self) -> None:
        self._r.start()

    def level(self) -> float:
        while not self._r.events.empty():
            ev = self._r.events.get_nowait()
            if ev.kind == "level":
                self._peak = ev.value
        return min(1.0, self._peak or 0.0)

    def elapsed(self) -> float:
        return self._r.elapsed()

    def stop(self) -> Optional[Path]:
        return self._r.stop()


def make_recorder(out_stem: Path):
    cls = AndroidRecorder if IS_ANDROID else DesktopRecorder
    return cls(out_stem.with_suffix(cls.suffix))


# ---------------------------------------------------------------------------
# Opening files in other apps
# ---------------------------------------------------------------------------

_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".wav": "audio/wav", ".m4a": "audio/mp4",
         ".srt": "text/plain", ".txt": "text/plain", ".csv": "text/csv", ".json": "application/json"}


def open_with_system(path: str) -> None:
    """Open a file in the tablet's default app (video player, text viewer...)."""
    if not IS_ANDROID:
        from ..preview import open_external

        open_external(path)
        return
    from jnius import autoclass, cast  # type: ignore

    StrictMode = autoclass("android.os.StrictMode")
    VmPolicyBuilder = autoclass("android.os.StrictMode$VmPolicy$Builder")
    # A file:// URI is fine for a sideloaded app; relax the check that forbids it.
    StrictMode.setVmPolicy(VmPolicyBuilder().build())
    Intent = autoclass("android.content.Intent")
    Uri = autoclass("android.net.Uri")
    File = autoclass("java.io.File")
    intent = Intent(Intent.ACTION_VIEW)
    mime = _MIME.get(Path(path).suffix.lower(), "*/*")
    intent.setDataAndType(Uri.fromFile(File(path)), mime)
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK)
    act = _activity()
    chooser = Intent.createChooser(intent, cast("java.lang.CharSequence", autoclass("java.lang.String")("Open with")))
    act.startActivity(chooser)


def scan_media_file(path: str) -> None:
    """Make a new export visible to the gallery and to USB file transfer."""
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass  # type: ignore

        MediaScannerConnection = autoclass("android.media.MediaScannerConnection")
        MediaScannerConnection.scanFile(_activity(), [path], None, None)
    except Exception:
        pass


def keep_screen_on(on: bool) -> None:
    """Long renders must not be interrupted by the screen timing out."""
    if not IS_ANDROID:
        return
    try:
        from android.runnable import run_on_ui_thread  # type: ignore
        from jnius import autoclass  # type: ignore

        LayoutParams = autoclass("android.view.WindowManager$LayoutParams")

        @run_on_ui_thread
        def apply():
            w = _activity().getWindow()
            if on:
                w.addFlags(LayoutParams.FLAG_KEEP_SCREEN_ON)
            else:
                w.clearFlags(LayoutParams.FLAG_KEEP_SCREEN_ON)

        apply()
    except Exception:
        pass


def platform_name() -> str:
    return "Android" if IS_ANDROID else sys.platform

