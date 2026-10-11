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
  "$VENV/bin/pip" install -q "buildozer==$BUILDOZER_VERSION" setuptools build
fi

# Pin python-for-android to a released version instead of its moving master branch.
# (A git checkout, not the PyPI sdist: the sdist leaves out files some recipes need.)
P4A_DIR=".buildozer-p4a/python-for-android"
if [ ! -d "$P4A_DIR/.git" ]; then
  rm -rf "$P4A_DIR"
  git clone -q https://github.com/kivy/python-for-android.git "$P4A_DIR"
fi
TAG=$(git -C "$P4A_DIR" tag -l | grep -E "^v?${P4A_VERSION%%.*}\.0?$(echo $P4A_VERSION | cut -d. -f2)\.0?$(echo $P4A_VERSION | cut -d. -f3)$" | head -1)
if [ -z "$TAG" ]; then
  echo "python-for-android release $P4A_VERSION not found; tags are:"; git -C "$P4A_DIR" tag -l | tail -5; exit 1
fi
git -C "$P4A_DIR" checkout -q "$TAG"
"$VENV/bin/pip" install -q -e "$P4A_DIR"
export APP_P4A_SOURCE_DIR="$PWD/$P4A_DIR"
export VIRTUAL_ENV="$PWD/$VENV"  # Buildozer installs p4a's dependencies into this venv

PATH="$PWD/$VENV/bin:$PATH" buildozer android debug
ls -lh bin/*.apk
