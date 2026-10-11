import numpy as np
import pytest
from PIL import Image

from newsedit.assets import import_file, relink, rescan_folder
from newsedit.media import MediaError, probe
from newsedit.model import Project
from newsedit.visuals import fit_frame, load_still


def _img(w, h):
    a = np.zeros((h, w, 3), np.uint8)
    a[:, :, 0] = 200
    a[h // 2 - 5:h // 2 + 5, :, 1] = 255  # horizontal green band across the full width
    return a


@pytest.mark.parametrize("w,h", [(360, 640), (500, 500)])
def test_resize_modes_vertical_and_square(w, h):
    src = _img(w, h)
    crop = fit_frame(src, 1920, 1080, "crop")
    assert crop.shape == (1080, 1920, 3)
    assert crop[:, :, 0].min() > 150  # fills the frame: no bars
    blur = fit_frame(src, 1920, 1080, "blur")
    assert blur.shape == (1080, 1920, 3)
    fw = round(w * 1080 / h)
    x0 = (1920 - fw) // 2
    # foreground preserved undistorted in the centre (full height, aspect kept)
    centre = blur[:, x0 + 5:x0 + fw - 5, 0]
    assert centre.mean() > 190
    # sides are the blurred, darkened fill (not black, not identical to foreground)
    assert 0 < blur[:, 5, 0].mean() < 190
    stretch = fit_frame(src, 1920, 1080, "stretch")
    assert stretch.shape == (1080, 1920, 3)
    band_rows = np.where(stretch[:, 960, 1] > 128)[0]
    assert band_rows.size > 0


def test_crop_framing_offset():
    src = np.zeros((1000, 500, 3), np.uint8)
    src[:100] = 255  # bright top
    top = fit_frame(src, 1920, 1080, "crop", crop_y=-1.0)
    bottom = fit_frame(src, 1920, 1080, "crop", crop_y=1.0)
    assert top[:50].mean() > 200 and bottom[:50].mean() < 10


def test_exif_orientation_respected(media):
    meta = probe(media["exif"])
    assert (meta["width"], meta["height"]) == (400, 200) and meta["exif_rotated"]
    arr = load_still(str(media["exif"]), 400, 200, "stretch")
    # the yellow orientation marker must be in the top-left after EXIF correction
    assert arr[30, 30, 0] > 200 and arr[30, 30, 1] > 180 and arr[30, 30, 2] < 80


def test_rotation_metadata_reported_as_display_size(media):
    meta = probe(media["rotated"])
    assert meta["rotation"] in (90, 270)
    assert (meta["width"], meta["height"]) == (180, 320)


def test_unicode_paths_and_stable_ids_survive_rescan(media, tmp_path):
    p = Project()
    first, errs = rescan_folder(p, media["dir"], "broll")
    ids = {a.path: a.id for a in p.assets}
    from newsedit.fixtures import make_video

    make_video(media["dir"] / "αα_νέο.mp4", 1.0, 320, 180, tone_hz=None)  # sorts first
    added, _ = rescan_folder(p, media["dir"], "broll")
    assert len(added) == 1
    for a in p.assets:
        if a.path in ids:
            assert ids[a.path] == a.id
    assert any("δήλωση" in a.name for a in p.assets)


def test_invalid_and_missing_media(media, tmp_path):
    p = Project()
    with pytest.raises(MediaError):
        import_file(p, tmp_path / "δεν_υπάρχει.mp4", "broll")
    with pytest.raises(MediaError):
        import_file(p, media["music"], "broll")  # wrong kind/extension
    a = import_file(p, media["sb_a"], "soundbite")
    a2 = import_file(p, media["sb_a"], "soundbite")
    assert a is a2  # same file, same ID


def test_relink_keeps_id(media, tmp_path):
    import shutil

    p = Project()
    copy = tmp_path / "copy.mp4"
    shutil.copy(media["sb_a"], copy)
    a = import_file(p, copy, "soundbite")
    aid = a.id
    copy.unlink()
    moved = tmp_path / "moved" / "copy.mp4"
    moved.parent.mkdir()
    shutil.copy(media["sb_a"], moved)
    from newsedit.assets import auto_relink, missing_assets

    assert missing_assets(p)
    found = auto_relink(p, [str(moved.parent)])
    assert found and a.id == aid and a.path == str(moved)
    relink(a, media["sb_a"])
    assert a.id == aid
