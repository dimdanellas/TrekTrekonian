[app]

# Tablet (Android) build of the News Package Editor. Build with android/build_apk.sh
# or the "Android APK" GitHub Actions workflow; see docs/ANDROID.md.

title = News Package Editor
package.name = newspackageeditor
package.domain = org.redshirtlabs

source.dir = .
source.include_exts = py,png,jpg,ttf,json,txt
source.exclude_dirs = tests,examples,docs,android,bin,.buildozer,.git,.github,.venv,__pycache__,newsedit/ui
source.exclude_patterns = run_app.py,install.*,run.*,run_tests.sh,*.zip,pytest.ini

version.regex = __version__ = ['"](.*)['"]
version.filename = %(source.dir)s/newsedit/__init__.py

# Recipes (compiled for Android): python3, kivy, numpy, pillow, openssl, ffmpeg (+ libx264 via av_codecs).
# The rest are pure-Python packages installed as they are. Versions match requirements.txt where the
# recipes allow it. imageio-ffmpeg is left out on purpose: MoviePy is pointed at the bundled FFmpeg.
requirements = python3,kivy==2.3.1,pyjnius,android,numpy,pillow,openssl,ffmpeg,av_codecs,moviepy==2.2.1,imageio==2.38.1,decorator==5.3.1,proglog==0.1.12,tqdm,python-dotenv==1.2.4,gTTS==2.5.4,click<8.2,requests==2.34.2,urllib3,idna,certifi,chardet

orientation = landscape, landscape-reverse
fullscreen = 0

# Microphone for narration, internet for text-to-speech, shared storage for media and projects.
android.permissions = INTERNET, RECORD_AUDIO, MANAGE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE

android.api = 35
android.minapi = 26
android.ndk = 28c
android.ndk_api = 26
android.accept_sdk_license = True
# Most current tablets are 64-bit ARM. Add armeabi-v7a for older 32-bit devices (doubles the build time).
android.archs = arm64-v8a
android.allow_backup = False
android.debug_artifact = apk
android.release_artifact = apk

# FFmpeg recipe override that also ships ffprobe (see android/recipes/ffmpeg).
p4a.local_recipes = ./android/recipes
# Set by android/build_apk.sh to a pinned python-for-android release (empty = clone master).
p4a.source_dir =
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 0
