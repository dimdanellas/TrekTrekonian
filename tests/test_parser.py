from newsedit.parser import parse_script


def codes(res):
    return {d.code for d in res.diagnostics}


def test_canonical_structure_and_soundbite_followed_by_text_is_valid():
    res = parse_script("[BROLL_1]\nΑ β γ.\n[SOUNDBITE_1]\nΣυνέχεια αφήγησης.\n")
    kinds = [s.kind for s in res.segments]
    assert kinds == ["narration", "soundbite", "narration"]
    assert not res.errors
    # text right after a standalone soundbite is the next narration block, not an overlap
    assert "vo_overlap" not in codes(res)


def test_aliases_normalize():
    res = parse_script("[INSERT_IMAGE_1]\nΚείμενο.\n[INSERT_SOUNDBITE_2]\n[insert_broll_3]\nΤέλος.")
    assert [s.label for s in res.segments] == ["IMAGE_1", "SOUNDBITE_2", "BROLL_3"]
    assert res.needs_normalization
    assert res.normalized_script == "[IMAGE_1]\nΚείμενο.\n[SOUNDBITE_2]\n[BROLL_3]\nΤέλος."
    assert not res.errors


def test_paired_tags_are_reference_text_never_narration():
    s = "[BROLL_1]\nΑφήγηση.\n[START_SOUNDBITE_1] Αυτά είπε ο ομιλητής [END_SOUNDBITE_1]\nΣυνέχεια."
    res = parse_script(s)
    assert [x.kind for x in res.segments] == ["narration", "soundbite", "narration"]
    assert res.segments[1].reference_text == "Αυτά είπε ο ομιλητής"
    assert "Αυτά" not in res.prompter_text()
    assert res.narration_words == 2
    assert "paired_reference_text" in codes(res)
    assert "[NOTE: Κείμενο αναφοράς δήλωσης: Αυτά είπε ο ομιλητής]" in res.normalized_script


def test_missing_end_blocks_and_does_not_consume_rest():
    res = parse_script("[BROLL_1]\nΑ.\n[START_SOUNDBITE_1]\nΑυτό μένει αφήγηση.\n[BROLL_2]\nΒ.")
    assert "missing_end" in {d.code for d in res.errors}
    # remainder of the script is still parsed as narration, not swallowed
    texts = [s.text for s in res.segments if s.kind == "narration"]
    assert "Αυτό μένει αφήγηση." in texts and "Β." in texts
    assert not res.needs_normalization


def test_mismatched_nested_and_stray_tags_are_errors():
    assert "mismatched_end" in codes(parse_script("[START_SOUNDBITE_1] x [END_SOUNDBITE_2]"))
    assert "nested_tag" in codes(parse_script("[START_SOUNDBITE_1] x [SOUNDBITE_2] y [END_SOUNDBITE_1]"))
    assert "stray_end" in codes(parse_script("[BROLL_1] x [END_SOUNDBITE_1]"))


def test_unknown_tags_notes_and_parentheses():
    res = parse_script("[BROLL_1]\nΑ (σε παρένθεση) β. [NOTE: σημείωση με πολλές λέξεις] γ.\n[FOO_1]")
    assert "unknown_tag" in {d.code for d in res.errors}
    assert res.narration_words == 5  # Α, σε, παρένθεση, β, γ
    assert "σημείωση" not in res.prompter_text()
    assert "(σε παρένθεση)" in res.prompter_text()


def test_markers_do_not_count_as_words():
    res = parse_script("[BROLL_1]\nένα δύο τρία\n[SOUNDBITE_1]\n[IMAGE_2]\nτέσσερα")
    assert res.narration_words == 4


def test_reuse_of_label_creates_two_instances():
    res = parse_script("[BROLL_1]\nα.\n[SOUNDBITE_1]\n[BROLL_1]\nβ.\n[SOUNDBITE_1]")
    assert [s.label for s in res.segments if s.kind == "soundbite"] == ["SOUNDBITE_1", "SOUNDBITE_1"]
    assert not res.errors


def test_narration_without_visual_is_error_and_card_standalone():
    res = parse_script("Χωρίς εικόνα.")
    assert "no_visual" in {d.code for d in res.errors}
    res = parse_script("[CARD_1]\n[SOUNDBITE_1]")
    assert [s.kind for s in res.segments] == ["card", "soundbite"]


def test_pause_marker():
    res = parse_script("[BROLL_1]\nα.\n[PAUSE=1,5]\n")
    assert res.segments[-1].kind == "pause" and res.segments[-1].pause_seconds == 1.5
    assert "bad_pause" in codes(parse_script("[BROLL_1] α [PAUSE 2]"))
