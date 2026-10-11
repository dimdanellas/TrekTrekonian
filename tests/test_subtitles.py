from newsedit.model import Cue, LowerThird, SubtitleSettings, SubtitleTrack
from newsedit.subtitles import (apply_min_duration, build_timeline_cues, format_segments, parse_srt, place_cues,
                                validate_track, wrap_cue_text, write_srt)
from newsedit.timeline import compile_timeline
from newsedit.visuals import boxes_intersect, render_lower_third, render_subtitle

LONG = ("Η κυβέρνηση παρουσίασε σήμερα ένα εκτεταμένο πακέτο μέτρων για την οικονομία, την υγεία και την "
        "παιδεία, ενώ η αντιπολίτευση ζήτησε περισσότερες λεπτομέρειες για τη χρηματοδότηση των προτάσεων.")


def test_wrap_within_two_lines_of_42():
    lines = wrap_cue_text("Ο υπουργός μίλησε για τη νέα πρόταση της κυβέρνησης.", 42, 2)
    assert lines and len(lines) <= 2 and all(len(l) <= 42 for l in lines)
    # articles/prepositions are not left at the end of a line
    assert not lines[0].split()[-1].lower() in {"τη", "της", "για", "ο"}
    assert wrap_cue_text(LONG, 42, 2) is None


def test_long_cue_is_split_into_timed_cues():
    st = SubtitleSettings()
    cues = format_segments([{"start": 0.0, "end": 12.0, "text": LONG, "words": []}], st)
    assert len(cues) >= 3
    for c in cues:
        lines = wrap_cue_text(c.text, 42, 2)
        assert lines and all(len(l) <= 42 for l in lines)
        assert c.end - c.start <= st.max_duration + 1e-6
    # each split cue has its own interval (no shared full-duration interval)
    assert len({(c.start, c.end) for c in cues}) == len(cues)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(cues, cues[1:]))
    assert cues[0].start == 0.0 and abs(cues[-1].end - 12.0) < 1e-6


def test_word_timestamps_drive_split_times():
    words = [[i * 0.5, i * 0.5 + 0.4, w] for i, w in enumerate(LONG.split())]
    st = SubtitleSettings()
    cues = format_segments([{"start": 0.0, "end": words[-1][1], "text": LONG, "words": words}], st)
    starts = {w[0] for w in words}
    assert all(c.start in starts for c in cues)


def test_unsatisfiable_short_cue_flagged_not_extended_beyond_clip():
    st = SubtitleSettings()
    cues = [Cue(0.0, 1.5, "Πρώτη φράση."), Cue(1.6, 2.0, "Ναι.")]
    apply_min_duration(cues, st, clip_len=2.0)
    assert cues[1].end <= 2.0
    tr = SubtitleTrack("SOUNDBITE_1", cues=cues, reviewed=True)
    d = validate_track(tr, 2.0, st, "SOUNDBITE_1")
    assert any(x.code == "cue_short" for x in d)
    bad = SubtitleTrack("SOUNDBITE_1", cues=[Cue(1.0, 0.5, "x"), Cue(0.0, 3.0, "y")], reviewed=True)
    codes = {x.code for x in validate_track(bad, 2.0, st, "SOUNDBITE_1")}
    assert {"cue_negative", "cue_range"} <= codes


def _with_tracks(p):
    p.subtitle_tracks = {
        "SOUNDBITE_1": SubtitleTrack("SOUNDBITE_1", clip_in=0.5, clip_out=2.5, reviewed=True,
                                     cues=[Cue(0.1, 1.0, "Πρώτη δήλωση."), Cue(1.1, 1.9, "Συνέχεια.")]),
        "SOUNDBITE_2": SubtitleTrack("SOUNDBITE_2", clip_in=0.0, clip_out=2.0, reviewed=True,
                                     cues=[Cue(0.1, 1.5, "Δεύτερη δήλωση.")]),
    }
    return p


def test_second_statement_uses_its_own_offset(routing_project):
    p = _with_tracks(routing_project)
    tl = compile_timeline(p)
    cues, _ = build_timeline_cues(p, tl)
    sb = [s for s in tl.segments if s.kind == "soundbite"]
    a0, b0 = sb[0].start_frame / tl.fps, sb[1].start_frame / tl.fps
    assert abs(cues[0].start - (a0 + 0.1)) < 1e-9
    b_cue = next(c for c in cues if c.source_label == "SOUNDBITE_2")
    assert abs(b_cue.start - (b0 + 0.1)) < 1e-9
    for c in cues:
        seg = sb[0] if c.source_label == "SOUNDBITE_1" else sb[1]
        assert seg.start_frame / tl.fps <= c.start < c.end <= seg.end_frame / tl.fps


def test_trim_change_rebases_cues(routing_project):
    p = _with_tracks(routing_project)
    p.bindings["SOUNDBITE_1"].in_seconds = 1.0  # was 0.5 at transcription
    tl = compile_timeline(p)
    cues, diags = build_timeline_cues(p, tl)
    a0 = tl.segments[1].start_frame / tl.fps
    first = next(c for c in cues if c.source_label == "SOUNDBITE_1")
    assert abs(first.start - a0) < 1e-9  # 0.1 + 0.5 - 1.0 < 0 -> clipped to clip start
    assert any(d.code == "cue_trim_changed" for d in diags)


def test_captions_avoid_lower_thirds_horizontal_and_vertical(routing_project):
    p = _with_tracks(routing_project)
    p.lower_thirds = [LowerThird("SOUNDBITE_1", "Ένα πολύ μακρύ όνομα ομιλητή που αναδιπλώνεται",
                                 "Ρόλος", 0.0, None),
                      LowerThird("SOUNDBITE_2", "Β", "Ρόλος", 0.0, None)]
    p.subtitle_tracks["SOUNDBITE_1"].cues[0].text = ("Μια αρκετά μεγάλη δήλωση που πιάνει όλο το πλάτος "
                                                     "της οθόνης για τον έλεγχο")
    tl = compile_timeline(p)
    for (w, h) in ((1920, 1080), (1080, 1920)):
        cues, _ = build_timeline_cues(p, tl)
        place_cues(p, tl, cues, w, h)
        for c in cues:
            lt = p.lower_third_for(c.source_label)
            lt_ov = render_lower_third(lt.name, lt.role, w, h, p.visual.safe_inset)
            sub = render_subtitle(c.lines, w, h, c.bottom_y)
            assert not boxes_intersect(sub.bbox, lt_ov.bbox), (w, h, c.lines)
            # inside the frame and the safe area
            assert sub.bbox[1] >= 0 and sub.bbox[3] <= int(h * (1 - p.visual.safe_inset)) + 1
            assert lt_ov.bbox[0] >= 0 and lt_ov.bbox[2] <= w


def test_lower_third_long_name_fits():
    ov = render_lower_third("Αλέξανδρος-Κωνσταντίνος Παπαδημητρόπουλος-Καραμανλής του Γεωργίου", "Βουλευτής",
                            1920, 1080)
    assert ov.bbox[2] <= 1920 * 0.95 + 1 and ov.bbox[0] >= 0


def test_srt_roundtrip(tmp_path, routing_project):
    p = _with_tracks(routing_project)
    tl = compile_timeline(p)
    cues, _ = build_timeline_cues(p, tl)
    write_srt(cues, tmp_path / "x.srt")
    rows = parse_srt((tmp_path / "x.srt").read_text(encoding="utf-8"))
    assert len(rows) == len(cues)
    assert abs(rows[0][0] - cues[0].start) < 0.002
