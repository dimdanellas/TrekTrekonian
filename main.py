"""Entry point of the tablet (Kivy/Android) build.

Buildozer packages this file as the APK's main script.  It also runs on a
desktop with Kivy installed:  python main.py [project.newsproj.json]
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("KIVY_NO_ARGS", "1")

# FFmpeg and app folders must be configured before the engine is imported.
from newsedit.mobile.platform import configure_environment  # noqa: E402

_problems = configure_environment()

from newsedit.mobile.app import run_app  # noqa: E402

if __name__ == "__main__":
    run_app(sys.argv[1] if len(sys.argv) > 1 else None, _problems)
