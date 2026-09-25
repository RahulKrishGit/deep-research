"""Deterministic, query-aware selection over complete extracted passages."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", re.IGNORECASE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "each",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "no",
        "of",
        "on",
        "or",
        "own",
        "so",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)


_APOSTROPHE_TABLE = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'"}
)


def _tokens(text: str) -> list[str]:
    """Reduce text to the comparable words a locator is scored on.

    A hyphen, a possessive, and the typographic apostrophe a page prints
    instead of "'" mark the same words a page may space out or name bare
    ("sound-quality" against "sound quality", "Orion’s" against "Orion"), so
    both sides of a comparison are reduced the same way. Function words are
    dropped: they appear in any broad-vocabulary chunk and would let page
    chrome outrank a verdict.
    """
    tokens: list[str] = []
    for match in _WORD.findall(text.translate(_APOSTROPHE_TABLE)):
        token = match.casefold().removesuffix("'s").removesuffix("'")
        tokens.extend(
            part for part in token.split("-") if part and part not in _STOP_WORDS
        )
    return tokens


def _stem(token: str) -> str:
    """Apply only conservative morphology for query/document agreement."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def _expanded_terms(query: str) -> set[str]:
    """Every comparable form one of the query's own words may take.

    Light and domain-neutral, by design (D1): a token's own casefolded form
    and its conservative stem cover plural/singular and -ing/-ed agreement,
    and hyphen/space agreement is already folded in by :func:`_tokens`.
    Nothing here maps one word to a *different* one -- no domain word list,
    however small or "transparent" -- because a selector that decides
    "interconnection" means "queue" is guessing at a relation the query never
    stated, in energy or any other domain.
    """
    terms: set[str] = set()
    for token in _tokens(query):
        terms.add(token)
        terms.add(_stem(token))
    return terms


# A passage reads as navigation, a link rail, or another run of site chrome
# when it strings a long run of bare words together with almost none of the
# connective words a written sentence needs -- the same connectives
# ``_STOP_WORDS`` already lists for query matching. Genuine prose, however
# terse, keeps a real share of them ("the", "of", "in", "and", ...); a menu
# ("Reviews Deals News Forum Search Sign In") does not, however many items it
# strings together or how many of them happen to repeat a query's own words.
# The word-count floor keeps a short caption or a one-line verdict out of the
# rule: chrome is what a *long* run of bare nouns looks like, not brevity. A
# tabular row shares the same low connective-word ratio -- a CSV row, a data
# table -- so a share of purely numeric tokens above ``_TABULAR_MIN_DIGIT_RATIO``
# exempts it: a page's own link rail is never dense with figures the way a
# measurement row is.
_LINK_DENSE_MIN_WORDS = 12
_LINK_DENSE_MAX_STOP_RATIO = 0.08
_TABULAR_MIN_DIGIT_RATIO = 0.10


def is_link_dense(text: str) -> bool:
    """Whether ``text`` reads as navigation or another link rail, not prose.

    Structural only: no menu word, no site name, no domain vocabulary --
    just the ratio of connective words a passage carries, with a data row's
    own numbers exempting it. Used both to keep such a passage from ever
    scoring (:func:`_score`) and to keep it from being forced in as a read's
    lede (``acquisition.select_passages_with_lede``).
    """
    raw = _WORD.findall(text.translate(_APOSTROPHE_TABLE))
    if len(raw) < _LINK_DENSE_MIN_WORDS:
        return False
    stop_count = sum(1 for word in raw if word.casefold() in _STOP_WORDS)
    if (stop_count / len(raw)) > _LINK_DENSE_MAX_STOP_RATIO:
        return False
    digit_count = sum(1 for word in raw if word.isdigit())
    return (digit_count / len(raw)) < _TABULAR_MIN_DIGIT_RATIO


def _score(
    text: str, query_terms: set[str], query_tokens: list[str]
) -> tuple[float, int, int]:
    """Rank ``text`` against the query: weighted, and normalised by length.

    The primary component is a term-frequency density: every occurrence of a
    query term counts (a passage that repeats "battery storage" five times is
    more clearly about it than one that mentions it once), divided by the
    square root of the passage's own word count so a long page that only
    happens to brush against the query cannot outrank a short one that is
    actually about it. ``phrase_hit`` and ``context`` remain modest
    tie-breakers after that, exactly as before.
    """
    lowered = text.casefold()
    stripped = lowered.strip()
    if (
        "table of contents" in stripped
        or stripped.startswith(("contents", "appendix", "references", "bibliography"))
        or is_link_dense(text)
    ):
        return (0.0, 0, 0)
    words = _tokens(text)
    if not words:
        return (0.0, 0, 0)
    stems = [_stem(word) for word in words]
    matched = sum(
        1
        for word, stem in zip(words, stems)
        if word in query_terms or stem in query_terms
    )
    if not matched:
        return (0.0, 0, 0)
    density = matched / math.sqrt(len(words))
    phrase = " ".join(query_tokens)
    phrase_hit = 1 if phrase and phrase in lowered else 0
    # Headers, units, and qualifiers often carry the context a bare number
    # lacks. Keep this as a modest tie-breaker after lexical coverage.
    context = sum(
        marker in lowered
        for marker in (
            "table",
            "figure",
            "note",
            "footnote",
            "unit",
            "%",
            "mw",
            "gw",
            "not",
            "without",
        )
    )
    return (density, phrase_hit, context)


def _ranked(
    passages: Mapping[str, str], query: str
) -> list[tuple[tuple[float, int, int], int, str]]:
    """Every locator that matches the query, scored and ordered best first."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query_tokens = _tokens(query)
    query_terms = _expanded_terms(query)
    scored: list[tuple[tuple[float, int, int], int, str]] = []
    for order, (locator, text) in enumerate(passages.items()):
        if not isinstance(locator, str) or not locator.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        score = _score(text, query_terms, query_tokens)
        if score[0]:
            scored.append((score, -order, locator))
    scored.sort(reverse=True)
    return scored


def select_relevant_passages(
    passages: Mapping[str, str], query: str, limit: int
) -> list[str]:
    """Return the best locator IDs after inspecting the complete mapping.

    Selection is intentionally local and deterministic: all extracted chunks
    are scored, no response prefix is serialized, and ties retain reader
    order. A caller may run another bounded batch when this result is empty or
    when omitted IDs remain pending.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return [locator for _, _, locator in _ranked(passages, query)[:limit]]


def select_passages_by_budget(
    passages: Mapping[str, str], query: str, budget: int
) -> list[str]:
    """Return the best-ranked locators whose combined text fits ``budget`` chars.

    A page's chunks vary sharply in length (D1): a fixed passage *count*
    either starves a page of many short, on-topic chunks -- the audited run
    admitted twelve of a page's 208 chunks and deferred several that answered
    the question -- or spends the whole allowance on a few long ones. Ranking
    is exactly :func:`select_relevant_passages`'s; only the cutover differs:
    passages are taken in rank order, each one kept only while it still fits,
    so a later, shorter passage may fill room an earlier, longer one left
    unusable, and a read contributes however many chunks its own relevance
    and length allow rather than a count decided in advance.
    """
    if budget < 1:
        raise ValueError("budget must be at least 1")
    selected: list[str] = []
    used = 0
    for _score, _order, locator in _ranked(passages, query):
        text_len = len(passages[locator])
        if used + text_len > budget:
            continue
        selected.append(locator)
        used += text_len
    return selected


__all__ = [
    "is_link_dense",
    "select_passages_by_budget",
    "select_relevant_passages",
]
