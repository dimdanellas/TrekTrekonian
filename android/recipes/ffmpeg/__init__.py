"""FFmpeg for the News Package Editor APK.

python-for-android's own recipe already builds the ``ffmpeg`` program (with
libx264 when ``av_codecs`` is in the requirements) and ships it as
``libffmpegbin.so``.  This override also ships ``ffprobe`` as
``libffprobebin.so``: the editor probes every imported clip with it.

Android only lets an app execute files from its native library folder, which
is why both programs are packaged under library names.
"""
from os.path import dirname, join

import sh
from pythonforandroid.logger import shprint
from pythonforandroid.recipes import ffmpeg as upstream
from pythonforandroid.util import current_directory

_UPSTREAM_DIR = dirname(upstream.__file__)


class FFMpegCLIRecipe(upstream.FFMpegRecipe):
    # Patches live next to the upstream recipe, not in this local folder.
    patches = [join(_UPSTREAM_DIR, p) for p in upstream.FFMpegRecipe.patches]
    _libs = upstream.FFMpegRecipe._libs + ["libffprobebin.so"]
    built_libraries = dict.fromkeys(_libs, "./lib")

    def should_build(self, arch):
        return super().should_build(arch) or not self._has_ffprobe(arch)

    def _has_ffprobe(self, arch):
        from os.path import exists

        return exists(join(self.get_build_dir(arch.arch), "lib", "libffprobebin.so"))

    def build_arch(self, arch):
        super().build_arch(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            shprint(sh.cp, "ffprobe", "./lib/libffprobebin.so")


recipe = FFMpegCLIRecipe()
