"""Unicode-aware spoken-word tokenizer.

Definition (documented in README): a *spoken word* is a maximal run of
Unicode letters/digits (categories L* and N*), optionally joined by a single
apostrophe, hyphen, or period between two such runs (e.g. ``Ε.Ε.``, ``co-op``,
``2,5`` is counted as two words because a comma is a pause).  Punctuation,
markers and editorial notes never count; callers strip markers before
counting.  Greek tonos/dialytika are letters, so ``ΰ`` or ``ά`` are handled.
"""
from __future__ import annotations

import re

WORD_RE = re.compile(r"[^\W_]+(?:['’\-.][^\W_]+)*", re.UNICODE)


def tokenize_words(text: str) -> list[str]:
    return WORD_RE.findall(text or "")


def count_words(text: str) -> int:
    return len(tokenize_words(text))


def estimate_seconds(words: int, wpm: float) -> float:
    """Estimated spoken duration; never rounded before limit evaluation."""
    if wpm <= 0:
        raise ValueError("WPM must be positive")
    return words / wpm * 60.0


def available_words(target_seconds: float, reserved_seconds: float, wpm: float) -> int:
    import math

    return int(math.floor(max(0.0, target_seconds - reserved_seconds) * wpm / 60.0))
