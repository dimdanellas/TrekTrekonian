#!/usr/bin/env bash
# Runs the test suite; GUI tests need a display (uses xvfb-run when available on headless Linux).
cd "$(dirname "$0")"
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null; then exec xvfb-run -a "$PY" -m pytest -q "$@"; fi
exec "$PY" -m pytest -q "$@"
