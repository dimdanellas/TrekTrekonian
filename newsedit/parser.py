"""Deterministic script grammar.

Canonical markers (one per line is conventional but not required)::

    [BROLL_n]      visual for the following narration block (no own time)
    [IMAGE_n]      still image for the following narration block (no own time)
    [CARD_n]       manually supplied title/data card; covers following narration,
                   or stands alone for its configured card_seconds
    [SOUNDBITE_n]  inserts the trimmed statement with its original audio
    [STANDUP_n]    on-camera/stand-up clip with original audio, no narration
    [PAUSE=1.5]    explicit pause (seconds) holding the current visual
    [NOTE: ...]    editorial note: never spoken, counted or exported as prose

Legacy forms accepted and normalised visibly::

    [INSERT_SOUNDBITE_n] -> [SOUNDBITE_n]
    [INSERT_IMAGE_n]     -> [IMAGE_n]
    [INSERT_BROLL_n]     -> [BROLL_n]
    [START_SOUNDBITE_n] reference text [END_SOUNDBITE_n]
                         -> [SOUNDBITE_n] + [NOTE: reference text]

Everything else is narration.  Parenthesised prose is narration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .textutil import count_words

CANONICAL = ("BROLL", "IMAGE", "CARD", "SOUNDBITE", "STANDUP")
VISUAL_KINDS = ("BROLL", "IMAGE", "CARD")
STATEMENT_KINDS = ("SOUNDBITE", "STANDUP")

_NOTE_RE = re.compile(r"\[NOTE\s*:(.*?)\]", re.DOTALL | re.IGNORECASE)
_PAUSE_RE = re.compile(r"\[PAUSE\s*=\s*([0-9]+(?:[.,][0-9]+)?)\s*\]", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\[([^\[\]\n]*)\]")
_CANON_RE = re.compile(r"^(BROLL|IMAGE|CARD|SOUNDBITE|STANDUP)_(\d+)$")
_ALIAS_RE = re.compile(r"^INSERT_(SOUNDBITE|IMAGE|BROLL)_(\d+)$")
_START_RE = re.compile(r"^START_SOUNDBITE_(\d+)$")
_END_RE = re.compile(r"^END_SOUNDBITE_(\d+)$")
_TAGLIKE_RE = re.compile(r"^[A-Za-z]+(?:_[A-Za-z0-9]+)+$|^[A-Z0-9_]+$")


@dataclass
class Diagnostic:
    severity: str  # error | warning | info
    code: str
    message: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    segment: str = ""  # human-readable segment reference, e.g. "SOUNDBITE_2" or "Narration 3"
    label: str = ""  # binding label for navigation

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def key(self) -> str:
        return f"{self.code}|{self.segment}|{self.message}"


@dataclass
class Token:
    kind: str  # tag | text | note | pause | ref
    start: int
    end: int
    text: str = ""
    tag_kind: str = ""  # BROLL/IMAGE/CARD/SOUNDBITE/STANDUP
    number: int = 0
    legacy: str = ""  # original legacy form
    value: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.tag_kind}_{self.number}" if self.tag_kind else ""


@dataclass
class ScriptSegment:
    kind: str  # narration | soundbite | standup | card | pause
    char_start: int
    char_end: int
    label: str = ""  # binding label (statement/card) or visual label for narration
    text: str = ""  # narration text (markers/notes removed)
    block_index: int = -1  # narration block index (0-based)
    words: int = 0
    pause_seconds: float = 0.0
    reference_text: str = ""  # legacy paired interior text (never spoken)
    visual_inherited: bool = False

    @property
    def visual_label(self) -> str:
        return self.label if self.kind in ("narration", "pause") else ""

    def display_name(self) -> str:
        if self.kind == "narration":
            return f"Narration {self.block_index + 1}"
        if self.kind == "pause":
            return f"Pause {self.pause_seconds:g}s"
        return self.label


@dataclass
class ParseResult:
    segments: list[ScriptSegment] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    normalized_script: str = ""
    needs_normalization: bool = False
    labels: list[str] = field(default_factory=list)  # distinct binding labels in order of first use

    @property
    def narration_blocks(self) -> list[ScriptSegment]:
        return [s for s in self.segments if s.kind == "narration"]

    @property
    def narration_words(self) -> int:
        return sum(s.words for s in self.narration_blocks)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.is_error]

    def prompter_text(self) -> str:
        """Narration only, in order, paragraph breaks preserved."""
        return "\n\n".join(s.text for s in self.narration_blocks if s.text.strip()) + "\n"


def _tokenize(script: str, diags: list[Diagnostic]) -> list[Token]:
    """Split script into tag/note/pause/text tokens with char offsets."""
    spans: list[tuple[int, int, Token]] = []
    for m in _NOTE_RE.finditer(script):
        spans.append((m.start(), m.end(), Token("note", m.start(), m.end(), text=m.group(1).strip())))

    def overlaps(a: int, b: int) -> bool:
        return any(s < b and a < e for s, e, _ in spans)

    # Unterminated note: "[NOTE:" without closing bracket.
    for m in re.finditer(r"\[NOTE\s*:", script, re.IGNORECASE):
        if not overlaps(m.start(), m.end()):
            diags.append(Diagnostic("error", "note_unterminated",
                                    "Editorial note is missing its closing ']'.", m.start(), m.end(), "Note"))
    for m in _PAUSE_RE.finditer(script):
        if overlaps(m.start(), m.end()):
            continue
        val = float(m.group(1).replace(",", "."))
        spans.append((m.start(), m.end(), Token("pause", m.start(), m.end(), text=m.group(0), value=val)))
    for m in _BRACKET_RE.finditer(script):
        if overlaps(m.start(), m.end()):
            continue
        body = m.group(1).strip()
        up = body.upper()
        tok: Optional[Token] = None
        if (mm := _CANON_RE.match(up)):
            tok = Token("tag", m.start(), m.end(), text=m.group(0), tag_kind=mm.group(1), number=int(mm.group(2)))
            if body != up:
                tok.legacy = m.group(0)
        elif (mm := _ALIAS_RE.match(up)):
            tok = Token("tag", m.start(), m.end(), text=m.group(0), tag_kind=mm.group(1), number=int(mm.group(2)),
                        legacy=m.group(0))
        elif (mm := _START_RE.match(up)):
            tok = Token("start", m.start(), m.end(), text=m.group(0), tag_kind="SOUNDBITE", number=int(mm.group(1)))
        elif (mm := _END_RE.match(up)):
            tok = Token("end", m.start(), m.end(), text=m.group(0), tag_kind="SOUNDBITE", number=int(mm.group(1)))
        elif up.startswith("PAUSE"):
            diags.append(Diagnostic("error", "bad_pause", f"Invalid pause marker {m.group(0)}: use [PAUSE=1.5] (seconds).",
                                    m.start(), m.end(), "Pause"))
            tok = Token("bad", m.start(), m.end(), text=m.group(0))
        elif body and _TAGLIKE_RE.match(body):
            diags.append(Diagnostic("error", "unknown_tag", f"Unknown tag {m.group(0)}.", m.start(), m.end(), m.group(0)))
            tok = Token("bad", m.start(), m.end(), text=m.group(0))
        else:
            diags.append(Diagnostic("warning", "bracket_text",
                                    f"Bracketed text {m.group(0)!s} is treated as narration (it is not a known marker).",
                                    m.start(), m.end(), "Narration"))
            continue
        spans.append((m.start(), m.end(), tok))
    # Stray brackets not forming a token.
    spans.sort(key=lambda s: s[0])
    tokens: list[Token] = []
    pos = 0
    for s, e, tok in spans:
        if s > pos:
            tokens.append(Token("text", pos, s, text=script[pos:s]))
        tokens.append(tok)
        pos = e
    if pos < len(script):
        tokens.append(Token("text", pos, len(script), text=script[pos:]))
    return tokens


def parse_script(script: str) -> ParseResult:
    res = ParseResult()
    diags = res.diagnostics
    tokens = _tokenize(script, diags)
    edits: list[tuple[int, int, str]] = []

    # ---- resolve paired START/END into ref tokens ---------------------------
    resolved: list[Token] = []
    i = 0
    ref_texts: dict[int, str] = {}
    while i < len(tokens):
        t = tokens[i]
        if t.kind == "start":
            j = i + 1
            problem = None
            while j < len(tokens):
                u = tokens[j]
                if u.kind == "end":
                    if u.number != t.number:
                        problem = ("mismatched_end",
                                   f"{t.text} is closed by {u.text}: IDs do not match.")
                    break
                if u.kind in ("start", "tag", "pause"):
                    has_end = any(x.kind == "end" and x.number == t.number for x in tokens[j:])
                    if has_end:
                        problem = ("nested_tag", f"{t.text} contains another marker {u.text} before its END tag "
                                                 "(nesting is not allowed).")
                    else:
                        problem = ("missing_end", f"{t.text} has no matching [END_SOUNDBITE_{t.number}].")
                    break
                j += 1
            else:
                problem = ("missing_end", f"{t.text} has no matching [END_SOUNDBITE_{t.number}].")
            label = f"SOUNDBITE_{t.number}"
            if problem:
                diags.append(Diagnostic("error", problem[0], problem[1] + " Rendering is blocked until fixed.",
                                        t.start, t.end, label, label))
                # Treat START as a standalone soundbite; following text stays narration.
                resolved.append(Token("tag", t.start, t.end, text=t.text, tag_kind="SOUNDBITE",
                                      number=t.number, legacy=t.text))
                i += 1
                continue
            end_tok = tokens[j]
            inner = script[t.end:end_tok.start]
            inner_clean = _NOTE_RE.sub(" ", inner).strip()
            ref = Token("tag", t.start, end_tok.end, text=script[t.start:end_tok.end], tag_kind="SOUNDBITE",
                        number=t.number, legacy="paired")
            resolved.append(ref)
            if inner_clean:
                prev = ref_texts.get(t.number)
                if prev is not None and prev != inner_clean:
                    diags.append(Diagnostic("warning", "conflicting_reference",
                                            f"{label} has different reference texts in two paired blocks.",
                                            t.start, end_tok.end, label, label))
                ref_texts[t.number] = inner_clean
                diags.append(Diagnostic(
                    "warning", "paired_reference_text",
                    f"Text inside {t.text} … {end_tok.text} is treated as a reference transcript of the statement "
                    "and is excluded from narration, TTS and prompter. Review it or normalise the tags.",
                    t.start, end_tok.end, label, label))
                ref.value = 1.0
                ref.text = inner_clean
            repl = f"[SOUNDBITE_{t.number}]"
            if inner_clean:
                repl += f"\n[NOTE: Κείμενο αναφοράς δήλωσης: {inner_clean}]"
            edits.append((t.start, end_tok.end, repl))
            i = j + 1
            continue
        if t.kind == "end":
            diags.append(Diagnostic("error", "stray_end", f"{t.text} has no opening [START_SOUNDBITE_{t.number}].",
                                    t.start, t.end, f"SOUNDBITE_{t.number}", f"SOUNDBITE_{t.number}"))
            i += 1
            continue
        if t.kind == "tag" and t.legacy and t.legacy != "paired":
            edits.append((t.start, t.end, f"[{t.label}]"))
            diags.append(Diagnostic("info", "legacy_alias", f"Legacy marker {t.legacy} normalises to [{t.label}].",
                                    t.start, t.end, t.label, t.label))
        resolved.append(t)
        i += 1

    # ---- build segments -----------------------------------------------------
    current_visual: Optional[str] = None
    last_visual: Optional[str] = None
    visual_used = True
    visual_tok: Optional[Token] = None
    block_parts: list[tuple[int, int, str]] = []
    block_index = 0
    seen_labels: list[str] = []

    def note_label(label: str) -> None:
        if label not in seen_labels:
            seen_labels.append(label)

    def flush_block() -> None:
        nonlocal block_parts, block_index, visual_used
        if not block_parts:
            return
        text = "".join(p[2] for p in block_parts)
        start, end = block_parts[0][0], block_parts[-1][1]
        block_parts = []
        clean = re.sub(r"[ \t]+", " ", text)
        clean = re.sub(r"\n[ \t]*", "\n", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        if not clean:
            return
        words = count_words(clean)
        if words == 0:
            diags.append(Diagnostic("warning", "punct_only", "Narration block contains no spoken words.",
                                    start, end, "Narration"))
            return
        seg = ScriptSegment("narration", start, end, label=current_visual or "", text=clean,
                            block_index=block_index, words=words)
        if current_visual is None:
            if last_visual:
                seg.label = last_visual
                seg.visual_inherited = True
                diags.append(Diagnostic(
                    "warning", "visual_inherited",
                    f"Narration {block_index + 1} has no visual marker; it reuses {last_visual}. "
                    "Insert a [BROLL_n]/[IMAGE_n] before it to choose explicitly.",
                    start, end, f"Narration {block_index + 1}", last_visual))
            else:
                diags.append(Diagnostic(
                    "error", "no_visual",
                    f"Narration {block_index + 1} has no visual. Insert [BROLL_n], [IMAGE_n] or [CARD_n] before it.",
                    start, end, f"Narration {block_index + 1}"))
        res.segments.append(seg)
        block_index += 1
        visual_used = True

    def close_visual(standalone_ok: bool) -> None:
        """Called when a visual marker is followed by a non-narration boundary."""
        nonlocal visual_used
        if visual_tok is not None and not visual_used:
            if visual_tok.tag_kind == "CARD" and standalone_ok:
                res.segments.append(ScriptSegment("card", visual_tok.start, visual_tok.end, label=visual_tok.label))
            else:
                diags.append(Diagnostic(
                    "warning", "visual_unused",
                    f"[{visual_tok.label}] is not followed by narration, so it adds no time and is not shown.",
                    visual_tok.start, visual_tok.end, visual_tok.label, visual_tok.label))
        visual_used = True

    for t in resolved:
        if t.kind == "text":
            if t.text.strip():
                block_parts.append((t.start, t.end, t.text))
            elif block_parts:
                block_parts.append((t.start, t.end, t.text))
        elif t.kind == "note":
            continue  # notes do not break a block and are never spoken
        elif t.kind == "bad":
            continue
        elif t.kind == "pause":
            flush_block()
            close_visual(True)
            if t.value <= 0:
                diags.append(Diagnostic("error", "bad_pause", "Pause must be longer than zero seconds.",
                                        t.start, t.end, "Pause"))
                continue
            seg = ScriptSegment("pause", t.start, t.end, label=current_visual or last_visual or "",
                                pause_seconds=t.value)
            if not seg.label:
                diags.append(Diagnostic("error", "no_visual", "Pause has no visual to hold. Insert a visual marker first.",
                                        t.start, t.end, "Pause"))
            res.segments.append(seg)
        elif t.kind == "tag":
            label = t.label
            note_label(label)
            if t.number < 1:
                diags.append(Diagnostic("warning", "zero_index", f"[{label}] — marker numbers normally start at 1.",
                                        t.start, t.end, label, label))
            if t.tag_kind in VISUAL_KINDS:
                flush_block()
                close_visual(True)
                current_visual = label
                last_visual = label
                visual_tok = t
                visual_used = False
            else:
                flush_block()
                close_visual(True)
                seg = ScriptSegment(t.tag_kind.lower(), t.start, t.end, label=label)
                if t.legacy == "paired" and t.value:
                    seg.reference_text = t.text
                res.segments.append(seg)
                current_visual = None
                visual_tok = None
    flush_block()
    close_visual(True)

    res.labels = seen_labels
    if edits:
        res.needs_normalization = True
        out = []
        pos = 0
        for s, e, r in sorted(edits):
            out.append(script[pos:s])
            out.append(r)
            pos = e
        out.append(script[pos:])
        res.normalized_script = "".join(out)
    else:
        res.normalized_script = script
    # Paired-tag syntax errors make normalisation unsafe.
    if any(d.code in ("missing_end", "mismatched_end", "nested_tag", "stray_end") for d in diags):
        res.needs_normalization = False
        res.normalized_script = script
    return res


def next_free_number(script: str, kind: str) -> int:
    nums = [int(m.group(2)) for m in re.finditer(r"\[(?:INSERT_|START_)?(" + kind + r")_(\d+)\]", script.upper())]
    return (max(nums) + 1) if nums else 1
