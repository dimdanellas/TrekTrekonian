#!/usr/bin/env bash
# Build the tablet APK on a Linux (x86_64) machine.
#
#   android/build_apk.sh            debug APK (installable by sideloading)
#
# First run downloads the Android SDK/NDK and compiles Python, Kivy, NumPy,
# Pillow and FFmpeg for ARM; expect it to take a long while.  Later runs reuse
# ~/.buildozer and the .buildozer folder and are much faster.
#
# Needs: Python 3.10+, JDK 17, git, zip/unzip, autoconf, automake, libtool,
# pkg-config, cmake, libffi-dev, libssl-dev, zlib1g-dev (Debian/Ubuntu names).
set -euo pipefail
cd "$(dirname "$0")/.."

P4A_VERSION=2026.5.9
BUILDOZER_VERSION=1.6.0
VENV=.buildozer-venv

if [ ! -x "$VENV/bin/buildozer" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q "buildozer==$BUILDOZER_VERSION" "cython==3.0.11" setuptools build
fi

# Pin python-for-android to a released version instead of its moving master branch.
P4A_DIR=".buildozer-p4a/python_for_android-$P4A_VERSION"
if [ ! -d "$P4A_DIR" ]; then
  mkdir -p .buildozer-p4a
  "$VENV/bin/pip" download -q --no-deps --no-binary :all: "python-for-android==$P4A_VERSION" -d .buildozer-p4a
  tar -xzf ".buildozer-p4a/python_for_android-$P4A_VERSION.tar.gz" -C .buildozer-p4a
  "$VENV/bin/pip" install -q -e "$P4A_DIR"
fi
export APP_P4A_SOURCE_DIR="$PWD/$P4A_DIR"

PATH="$PWD/$VENV/bin:$PATH" buildozer android debug
ls -lh bin/*.apk
