import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def _isolated_dirs(tmp_path_factory):
    base = tmp_path_factory.mktemp("appdirs")
    os.environ["NEWSEDIT_CONFIG_DIR"] = str(base / "config")
    os.environ["NEWSEDIT_CACHE_DIR"] = str(base / "cache")
    yield


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    """Small synthetic fixtures (Unicode file names on purpose)."""
    from newsedit import fixtures as F

    d = tmp_path_factory.mktemp("μέσα_media")
    m = {
        "sb_a": F.make_video(d / "δήλωση_Α.mp4", 3.0, 640, 360, tone_hz=440),
        "sb_b": F.make_video(d / "δήλωση_Β.mp4", 3.0, 360, 640, tone_hz=660),
        "broll": F.make_video(d / "broll_με_ήχο.mp4", 4.0, 640, 480, tone_hz=150),
        "broll_short": F.make_video(d / "broll_short.mp4", 1.0, 640, 360, tone_hz=None),
        "square": F.make_image(d / "τετράγωνο.png", 600, 600, "SQ"),
        "exif": F.make_image(d / "exif.jpg", 400, 200, "EXIF", exif_rotate=True),
        "rotated": F.make_video(d / "rotated.mp4", 1.0, 320, 180, tone_hz=None, rotate=90),
        "music": F.make_music_wav(d / "μουσική.wav", 6.0),
        "logo": F.make_logo(d / "logo.png", 200),
        "n1": F.make_tone_wav(d / "n1.wav", 1.6, hz=220),
        "n2": F.make_tone_wav(d / "n2.wav", 1.2, hz=260),
        "n3": F.make_tone_wav(d / "n3.wav", 1.0, hz=300),
        "dir": d,
    }
    return m


SCRIPT_ROUTING = """[BROLL_1]
Πρώτη αφήγηση.
[SOUNDBITE_1]
[IMAGE_1]
Δεύτερη αφήγηση.
[SOUNDBITE_2]
[BROLL_2]
Τελική αφήγηση.
"""


@pytest.fixture
def routing_project(media, tmp_path):
    """VO -> statement A -> VO -> statement B -> closing VO, all audio measured."""
    from newsedit.assets import import_file
    from newsedit.model import Binding, LowerThird, Project
    from newsedit.narration import register_recording
    from newsedit.parser import parse_script

    p = Project(title="Δοκιμή δρομολόγησης", script=SCRIPT_ROUTING, target_seconds=30)
    a = {k: import_file(p, media[k], kind) for k, kind in [
        ("sb_a", "soundbite"), ("sb_b", "soundbite"), ("broll", "broll"), ("square", "image"),
        ("broll_short", "broll"), ("music", "music"), ("logo", "logo")]}
    p.bindings = {
        "BROLL_1": Binding("BROLL_1", a["broll"].id, 0.0, 4.0),
        "SOUNDBITE_1": Binding("SOUNDBITE_1", a["sb_a"].id, 0.5, 2.5),
        "IMAGE_1": Binding("IMAGE_1", a["square"].id),
        "SOUNDBITE_2": Binding("SOUNDBITE_2", a["sb_b"].id, 0.0, 2.0),
        "BROLL_2": Binding("BROLL_2", a["broll_short"].id, 0.0, 1.0, fill_policy="hold"),
    }
    p.lower_thirds = [LowerThird("SOUNDBITE_1", "Ομιλητής Α", "Ρόλος Α", 0.2, 1.8),
                      LowerThird("SOUNDBITE_2", "Ομιλήτρια Β", "Ρόλος Β", 0.2, 1.8)]
    p.music.asset_id = a["music"].id
    p.watermark.asset_id = a["logo"].id
    p.export_directory = str(tmp_path / "εξαγωγές")
    for blk, key in zip(parse_script(p.script).narration_blocks, ("n1", "n2", "n3")):
        register_recording(p, blk.block_index, blk.text, media[key])
    return p
