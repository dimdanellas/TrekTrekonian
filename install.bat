@echo off
rem Install Automated News Package Editor into .venv (Windows). Requires Python 3.10+ from python.org (includes Tkinter)
rem and FFmpeg on PATH (e.g. winget install Gyan.FFmpeg).
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo FFmpeg not found on PATH. Install it, e.g.: winget install Gyan.FFmpeg & exit /b 1)
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if "%1"=="--with-whisper" .venv\Scripts\python -m pip install -r requirements-optional.txt
echo Installed. Start with run.bat
