#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python run_app.py "$@"; else exec python3 run_app.py "$@"; fi
