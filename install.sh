#!/usr/bin/env bash
# Install Automated News Package Editor into ./.venv (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "Python's Tkinter is missing. On Debian/Ubuntu: sudo apt install python3-tk python3-venv" >&2
  exit 1
fi
for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "$tool not found. On Debian/Ubuntu: sudo apt install ffmpeg" >&2; exit 1; }
done
if [ "$(uname)" = "Linux" ] && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "Note: PortAudio not found; microphone recording and embedded audio preview need it (sudo apt install libportaudio2)."
fi
"$PY" -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if [ "${1:-}" = "--with-whisper" ]; then
  .venv/bin/python -m pip install -r requirements-optional.txt
else
  echo "Tip: ./install.sh --with-whisper also installs local Whisper transcription."
fi
echo "Installed. Start with ./run.sh"
