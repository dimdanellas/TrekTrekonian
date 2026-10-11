"""Build the sample project with synthetic media (test patterns and tones).

Usage:  python examples/build_demo.py [output_dir] [--render PROFILE]

All speakers, roles and statement texts are placeholders: no real people,
footage or quotations are used.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from newsedit import fixtures  # noqa: E402
from newsedit.assets import import_file  # noqa: E402
from newsedit.model import Binding, Cue, LowerThird, Project, SfxPlacement, SubtitleTrack, save_project  # noqa: E402
from newsedit.narration import register_recording  # noqa: E402
from newsedit.parser import parse_script  # noqa: E402

SCRIPT = """[NOTE: Δοκιμαστικό ρεπορτάζ με συνθετικό υλικό. Δεν περιέχει πραγματικές δηλώσεις.]
[BROLL_1]
Η κυβέρνηση παρουσίασε σήμερα τις νέες προτάσεις της για τη δοκιμαστική ενότητα του ρεπορτάζ.
[SOUNDBITE_1]
[IMAGE_1]
Η αντιπολίτευση απάντησε με διαφορετικές προτεραιότητες, σύμφωνα με το δοκιμαστικό σενάριο.
[SOUNDBITE_2]
[BROLL_2]
Οι επόμενες ημέρες θα δείξουν την πορεία της συζήτησης.
"""

NARRATION_SECONDS = [6.0, 5.5, 4.0]


def build(out: Path) -> tuple[Project, Path]:
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)
    f = fixtures
    sb1 = f.make_video(media / "δήλωση_Α.mp4", 6.0, 1280, 720, tone_hz=440, label="ΔΗΛΩΣΗ Α (δοκιμή)")
    sb2 = f.make_video(media / "δήλωση_Β_κάθετη.mp4", 5.0, 720, 1280, tone_hz=660, label="ΔΗΛΩΣΗ Β", pattern="smptebars")
    br1 = f.make_video(media / "βουλή_broll.mp4", 8.0, 1920, 1080, tone_hz=150, pattern="testsrc2", label="B-ROLL 1")
    br2 = f.make_video(media / "δρόμος_broll.mp4", 3.0, 1440, 1080, tone_hz=None, pattern="rgbtestsrc", label="B-ROLL 2")
    im1 = f.make_image(media / "χάρτης.jpg", 1600, 1600, "ΧΑΡΤΗΣ", exif_rotate=True)
    music = f.make_music_wav(media / "μουσική.wav", 20.0)
    logo = f.make_logo(media / "logo.png")
    whoosh = f.make_tone_wav(media / "whoosh.wav", 0.8, hz=900, amp=0.3, lead=0.0, tail=0.1, stereo=True)

    p = Project(title="Δοκιμαστικό ρεπορτάζ", script=SCRIPT, target_seconds=40.0)
    p.export_directory = str(out / "exports")
    a = {k: import_file(p, path, kind) for k, path, kind in [
        ("sb1", sb1, "soundbite"), ("sb2", sb2, "soundbite"), ("br1", br1, "broll"), ("br2", br2, "broll"),
        ("im1", im1, "image"), ("music", music, "music"), ("logo", logo, "logo"), ("fx", whoosh, "sfx")]}
    p.bindings = {
        "BROLL_1": Binding("BROLL_1", a["br1"].id, 0.0, 8.0),
        "SOUNDBITE_1": Binding("SOUNDBITE_1", a["sb1"].id, 0.5, 5.5),
        "IMAGE_1": Binding("IMAGE_1", a["im1"].id),
        "SOUNDBITE_2": Binding("SOUNDBITE_2", a["sb2"].id, 0.0, 4.5),
        "BROLL_2": Binding("BROLL_2", a["br2"].id, 0.0, 3.0, fill_policy="hold"),
    }
    p.lower_thirds = [
        LowerThird("SOUNDBITE_1", "Ομιλητής Α (δοκιμαστικό όνομα)", "Δοκιμαστικός ρόλος", 0.3, 4.0),
        LowerThird("SOUNDBITE_2", "Ομιλήτρια Β", "Εκπρόσωπος δοκιμής", 0.3, 4.0),
    ]
    p.subtitle_tracks = {
        "SOUNDBITE_1": SubtitleTrack("SOUNDBITE_1", source="manual", clip_in=0.5, clip_out=5.5, reviewed=True, cues=[
            Cue(0.2, 2.4, "Αυτή είναι μια δοκιμαστική δήλωση με συνθετικό ήχο."),
            Cue(2.5, 4.8, "Ο υπότιτλος ελέγχει το χρονισμό και τη θέση."),
        ]),
        "SOUNDBITE_2": SubtitleTrack("SOUNDBITE_2", source="manual", clip_in=0.0, clip_out=4.5, reviewed=True, cues=[
            Cue(0.3, 2.2, "Δεύτερη δοκιμαστική δήλωση."),
            Cue(2.3, 4.3, "Ξεκινά από τη δική της θέση στο χρονολόγιο."),
        ]),
    }
    p.music.asset_id = a["music"].id
    p.watermark.asset_id = a["logo"].id
    p.opening_title.enabled = True
    p.opening_title.text = "ΔΟΚΙΜΑΣΤΙΚΟ ΡΕΠΟΡΤΑΖ"
    p.opening_title.subtitle = "Συνθετικό υλικό"
    p.opening_title.duration = 3.0
    p.sfx = [SfxPlacement(a["fx"].id, 0.0, -14.0)]
    p.visual.transition = "fade"
    # Narration stand-ins (one PCM WAV per block)
    parse = parse_script(p.script)
    for blk, secs in zip(parse.narration_blocks, NARRATION_SECONDS):
        wav = fixtures.make_tone_wav(media / f"narration_{blk.block_index + 1:02d}.wav", secs, hz=200 + 40 * blk.block_index)
        register_recording(p, blk.block_index, blk.text, wav)
    path = out / "demo_project.newsproj.json"
    save_project(p, path)
    return p, path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(Path(__file__).parent / "demo"))
    ap.add_argument("--render", default="", help="profile key to render, e.g. web_horizontal")
    args = ap.parse_args()
    p, path = build(Path(args.out).resolve())
    print("Project:", path)
    if args.render:
        from newsedit.export import export_package

        p.output.profile = args.render
        if args.render == "vertical_short":
            p.output.width, p.output.height = 1080, 1920
        t0 = time.monotonic()
        res = export_package(p.snapshot(), lambda: False,
                             lambda m, v: print(f"\r{m[:60]:60s} {v * 100:5.1f}%", end="", flush=True))
        print(f"\nRendered in {time.monotonic() - t0:.1f}s")
        for f in res.files:
            print("  ", f.name)
        print(res.verification)
        for d in res.diagnostics:
            print(f"  [{d.severity}] {d.message}")


if __name__ == "__main__":
    main()
