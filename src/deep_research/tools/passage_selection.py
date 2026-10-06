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

    Light and domain-neutral, by design: a token's own casefolded form
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


# ---------------------------------------------------------------------------
# lede detection: structure, not stop-word ratio
# ---------------------------------------------------------------------------
#
# A stop-word ratio cannot separate navigation from prose (measured on the
# first chunks of real review sites):
# a page's masthead and nav rail routinely quotes short connective phrases
# ("How we test", "What to look for", "Best on a budget"), so the *share* of
# connective words in real navigation (0.10-0.18) sits above real spec and
# pros/cons prose (0.083-0.12) -- the two overlap, and neither side of a
# single global ratio can be tuned to separate them.
#
# The structural signal that does hold across sites and languages: a page's
# nav rail is a run of link labels with essentially no sentence terminator
# anywhere in it, however many words it strings together, while genuine
# prose -- a pros/cons box, a verdict line, a feature list, a paragraph in
# German or Spanish -- breaks into ordinary clauses every ten to twenty
# words. The check is therefore the *longest run of words between sentence
# terminators*, measured after the chunk's own leading segment (a real
# opener's headline, which may carry no terminator of its own and would
# otherwise be counted as part of the first clause): if that longest run is
# very long, the passage strings labels together, not sentences.
_CJK_TERMINATORS = "\u3002\uff01\uff1f"  # 。！？
# A `.`/`!`/`?` (or CJK equivalent) ends a clause only away from a digit on
# either side (a decimal, "9.7", is not a sentence break) and only at a word
# boundary (followed by space or the end of the text).
_TERMINATOR = re.compile(
    r"(?<![0-9])(?P<mark>[.!?])(?!\d)(?=\s|$)"
    rf"|(?P<cjk>[{_CJK_TERMINATORS}])"
)
# A run of single letters each followed by a period ("U.S.", "a.m.", "Dr." is
# not one -- only a true run of bare single-letter-period pairs counts) right
# before the mark is an abbreviation, not the end of a sentence: checked by
# matching that run backward from the terminator, not by a fixed lookback.
_ABBREVIATION = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){1,}$")


def _clause_word_counts(text: str) -> list[int]:
    """The word count of each clause ``text`` splits into at a real terminator.

    A single clause (no terminator found at all) is the whole text: real
    navigation commonly carries no punctuation whatsoever.
    """
    clauses: list[str] = []
    start = 0
    for match in _TERMINATOR.finditer(text):
        if match.group("mark") == "." and _ABBREVIATION.search(
            text[: match.end()]
        ):
            continue
        clauses.append(text[start : match.end()])
        start = match.end()
    clauses.append(text[start:])
    return [len(_WORD.findall(clause)) for clause in clauses if clause.strip()]


# Below this many words, brevity alone keeps a passage out of the rule: a
# short PDF title page ("Annual Outlook 2025 - Issuer - April 2025") is still
# a lede, however label-shaped its handful of words are.
_LINK_DENSE_MIN_WORDS = 12
# The longest clause-after-the-title has to run this many words with no
# sentence terminator to read as a link rail: an ordinary sentence, clause,
# or a pros/cons/verdict line breaks well under this long before it, in any
# language that punctuates sentences at all (which a nav rail's own labels
# never do, however many of them a page strings together).
_LINK_DENSE_RUN_WORDS = 22
# A data row's own numbers exempt it: a page's link rail is never dense with
# figures the way a spec table or a CSV row is, whatever its clause shape.
_TABULAR_MIN_DIGIT_RATIO = 0.10


def is_link_dense(text: str) -> bool:
    """Whether ``text`` reads as navigation or another link rail, not prose.

    Structural only: no menu word, no site name, no domain vocabulary, and no
    language-specific connective list -- a sentence terminator means the same
    thing in English, German or Spanish, which a stop-word ratio never did.
    Used only to keep a read's own opening passage from being *forced in* as
    its lede when it is chrome, not a header (``acquisition.select_passages_with_lede``):
    ranking itself never calls this, so a navigation-shaped passage elsewhere
    on the page still competes for a slot on its own relevance.
    """
    raw = _WORD.findall(text.translate(_APOSTROPHE_TABLE))
    if len(raw) < _LINK_DENSE_MIN_WORDS:
        return False
    digit_count = sum(1 for word in raw if word.isdigit())
    if (digit_count / len(raw)) >= _TABULAR_MIN_DIGIT_RATIO:
        return False
    clauses = _clause_word_counts(text)
    # After the leading segment (a real opener's own headline): a chunk with
    # only one clause at all (no terminator anywhere) has no title to skip,
    # so the whole thing is what is measured.
    body = clauses[1:] or clauses
    return max(body, default=0) >= _LINK_DENSE_RUN_WORDS


# ---------------------------------------------------------------------------
# ranking: BM25-style coverage, never zeroed by shape
# ---------------------------------------------------------------------------
#
# A plain count of term occurrences let a chunk repeating one query word many
# times outrank a chunk that actually covers most of the query once each --
# on real pages a nav rail
# repeating "Headphones"/"Best" outscored the passage that named the
# microphone verdict. Distinct coverage has to dominate repetition, so each
# term's own contribution saturates (``_SATURATION_K``): a fifth repeat of
# one word adds far less than the first occurrence of a term nobody has
# matched yet. Terms that sit on nearly every passage of *this* read (a
# masthead's own topic word, a repeated menu label) also weigh less, from an
# inverse document frequency computed over the read's own passages -- never a
# global or cross-domain corpus, which would smuggle in exactly the kind of
# fixed vocabulary this module refuses to keep.
_SATURATION_K = 1.5
# Length normalisation divides by the square root of the passage's own word
# count, floored: without a floor a four-word chunk that happens to name
# every query term could outrank a full paragraph that names the same terms
# once each amid the sentences that actually answer the question.
_LENGTH_FLOOR_WORDS = 24


def _saturating_tf(count: int) -> float:
    """A term's own weight for occurring ``count`` times, saturating quickly.

    ``count / (count + k)`` rises steeply from the first occurrence and
    flattens after: a term matched once already earns most of the weight a
    hundred repeats of it would, so covering five different terms once each
    outweighs one term repeated many times.
    """
    return count / (count + _SATURATION_K)


def _passage_term_counts(
    words: list[str], stems: list[str], query_terms: set[str]
) -> dict[str, int]:
    """How many times each query term is matched in one passage's own words."""
    counts: dict[str, int] = {}
    for word, stem in zip(words, stems):
        for term in (word, stem):
            if term in query_terms:
                counts[term] = counts.get(term, 0) + 1
                break
    return counts


def _score(
    text: str,
    term_counts: Mapping[str, int],
    idf: Mapping[str, float],
    word_count: int,
    query_tokens: list[str],
) -> tuple[float, int, int]:
    """A passage's BM25-style relevance: coverage first, never zero by shape.

    Structural chrome and shape (navigation, a foreign language, a feature
    list) are never a reason to score a passage at zero here -- only a term
    match, or its absence, decides that. The read's own opening passage being
    chrome instead of a header is a *lede* question (:func:`is_link_dense`
    guards that separately in ``acquisition.select_passages_with_lede``); a
    misfire here would cost the evidence itself, not just the forcing.
    """
    lowered = text.casefold()
    stripped = lowered.strip()
    if "table of contents" in stripped or stripped.startswith(
        ("contents", "appendix", "references", "bibliography")
    ):
        return (0.0, 0, 0)
    if not term_counts:
        return (0.0, 0, 0)
    weighted = sum(
        idf.get(term, 1.0) * _saturating_tf(count)
        for term, count in term_counts.items()
    )
    normaliser = math.sqrt(max(word_count, _LENGTH_FLOOR_WORDS))
    density = weighted / normaliser
    phrase = " ".join(query_tokens)
    phrase_hit = 1 if phrase and phrase in lowered else 0
    # A number or a percentage often carries the context a bare word lacks.
    # General and domain-neutral: no unit ("MW", "GW", ...) belongs here.
    context = sum(
        marker in lowered for marker in ("table", "figure", "note", "footnote", "%")
    )
    if any(char.isdigit() for char in text):
        context += 1
    return (density, phrase_hit, context)


def _ranked(
    passages: Mapping[str, str], query: str
) -> list[tuple[tuple[float, int, int], int, str]]:
    """Every locator that matches the query, scored and ordered best first.

    Inverse document frequency is computed over exactly the passages being
    ranked -- this read's own, never a global corpus -- so a word that sits on
    nearly every passage of the page (its own topic word, a repeated menu
    label) weighs little without needing to name it.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query_tokens = _tokens(query)
    query_terms = _expanded_terms(query)
    entries: list[tuple[str, str, dict[str, int], int]] = []
    for locator, text in passages.items():
        if not isinstance(locator, str) or not locator.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        words = _tokens(text)
        stems = [_stem(word) for word in words]
        counts = _passage_term_counts(words, stems, query_terms)
        entries.append((locator, text, counts, len(words)))
    documents = len(entries) or 1
    document_frequency = {term: 0 for term in query_terms}
    for _locator, _text, counts, _word_count in entries:
        for term in counts:
            document_frequency[term] += 1
    idf = {
        term: math.log((documents + 1) / (document_frequency[term] + 1)) + 1.0
        for term in query_terms
    }
    scored: list[tuple[tuple[float, int, int], int, str]] = []
    for order, (locator, text, counts, word_count) in enumerate(entries):
        score = _score(text, counts, idf, word_count, query_tokens)
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
    passages: Mapping[str, str], query: str, budget: int, *, admit_unmatched: bool = True
) -> list[str]:
    """Return every locator whole-page admission keeps within ``budget`` chars.

    A page's chunks vary sharply in length: a fixed passage *count*
    either starves a page of many short, on-topic chunks -- one real page
    admitted twelve of its 208 chunks and deferred several that answered
    the question -- or spends the whole allowance on a few long ones. Ranking
    is exactly :func:`select_relevant_passages`'s; only the cutover differs:
    passages are taken in rank order, each one kept only while it still fits,
    so a later, shorter passage may fill room an earlier, longer one left
    unusable, and a read contributes however many chunks its own relevance
    and length allow rather than a count decided in advance.

    Whole-page admission means every passage, not only the ones the query's
    own words touch: a semantic answer can share no word with the query that
    names it, and a passage sharing none is still part of the page the run
    read. Once every ranked match that fits is taken, the passages the query
    matched nothing in fill whatever budget remains, in reader order --
    ranking has nothing to order them by, so the page's own order is what is
    left, and each is kept only while it still fits, exactly like a match.

    ``admit_unmatched=False`` turns that fill off: a caller building a
    claim's own verification packet wants only the passages that bear on the
    claim, and an unrelated passage admitted there is noise that turns a free
    ``no_candidate`` outcome into a paid adjudication.
    """
    if budget < 1:
        raise ValueError("budget must be at least 1")
    selected: list[str] = []
    used = 0
    matched: set[str] = set()
    for _score, _order, locator in _ranked(passages, query):
        text_len = len(passages[locator])
        if used + text_len > budget:
            continue
        selected.append(locator)
        used += text_len
        matched.add(locator)
    if not admit_unmatched:
        return selected
    for locator, text in passages.items():
        if locator in matched:
            continue
        if not isinstance(locator, str) or not locator.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        text_len = len(text)
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
