"""Deterministic, query-aware selection over complete extracted passages."""

from __future__ import annotations

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
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)

# Small, deliberately transparent paraphrase groups. They make selection
# useful for common query/document morphology without pretending to be a
# semantic model or allowing a snippet to become evidence.
_VARIANTS: dict[str, frozenset[str]] = {
    "commission": frozenset(
        {"commission", "commissioned", "commissioning", "delivery"}
    ),
    "delay": frozenset({"delay", "delays", "delayed", "latency", "waiting"}),
    "queue": frozenset({"queue", "queues", "interconnection", "connection"}),
    "cost": frozenset({"cost", "costs", "price", "pricing", "expenditure"}),
    "emission": frozenset({"emission", "emissions", "co2", "carbon"}),
    "capacity": frozenset(
        {"capacity", "capacities", "mw", "gw", "megawatt", "gigawatt"}
    ),
    "increase": frozenset({"increase", "increased", "increasing", "growth", "grew"}),
    "decrease": frozenset({"decrease", "decreased", "decline", "fell", "reduction"}),
}


def _tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in _WORD.findall(text)
        if token.casefold() not in _STOP_WORDS
    ]


def _stem(token: str) -> str:
    """Apply only conservative morphology for query/document agreement."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def _expanded_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in _tokens(query):
        stem = _stem(token)
        terms.add(token)
        terms.add(stem)
        for key, variants in _VARIANTS.items():
            if token == key or token in variants or stem == key:
                terms.update(variants)
                terms.add(key)
    return terms


def _score(
    text: str, query_terms: set[str], query_tokens: list[str]
) -> tuple[int, int, int]:
    lowered = text.casefold()
    stripped = lowered.strip()
    if (
        "table of contents" in stripped
        or stripped.startswith(("contents", "appendix", "references", "bibliography"))
    ):
        return (0, 0, 0)
    words = _tokens(text)
    stems = {_stem(word) for word in words}
    exact = sum(1 for term in query_terms if term in words or term in stems)
    if not exact:
        return (0, 0, 0)
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
    return (exact, phrase_hit, context)


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
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query_tokens = _tokens(query)
    query_terms = _expanded_terms(query)
    scored: list[tuple[tuple[int, int, int], int, str]] = []
    for order, (locator, text) in enumerate(passages.items()):
        if not isinstance(locator, str) or not locator.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        score = _score(text, query_terms, query_tokens)
        if score[0]:
            scored.append((score, -order, locator))
    scored.sort(reverse=True)
    return [locator for _, _, locator in scored[:limit]]


__all__ = ["select_relevant_passages"]
