"""Pure text normalization shared by the planner and the memory recall path.

Nothing here performs I/O or reads a clock, so both callers get the same
answer for the same input. Two modules needed exactly this normalization —
the planner to deduplicate a target's required dimensions, the recall path to
deduplicate procedural query templates — and a second copy of it is a second
place for "is this the same phrase?" to be answered differently.
"""

from __future__ import annotations

from collections.abc import Iterable


def collapse_whitespace(text: str) -> str:
    """One single-spaced line: leading, trailing, and repeated spaces gone."""
    return " ".join(text.split())


def unique_phrases(values: Iterable[str]) -> list[str]:
    """First-seen order, blanks dropped, case-insensitive duplicates removed.

    Case-insensitive because the two producers are a language model and a
    stored strategy list: "Grid connection" and "grid connection" are one
    phrase for every purpose these callers have, and listing both spends a
    reader's attention on nothing. The first spelling seen is the one kept.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = collapse_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


__all__ = ["collapse_whitespace", "unique_phrases"]
