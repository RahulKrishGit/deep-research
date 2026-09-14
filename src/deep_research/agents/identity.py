"""Canonical identities for evidence that outlives a single research pass.

A research pass re-derives its sources and claims from scratch, but the state
it updates carries every pass's evidence. Without a stable identity per
record, a later pass cannot tell "the same source, re-scored" from "a new
source", and every re-scoring piles up another copy. This module gives each
record the identity later passes merge on:

* a source is its canonical URL — ``normalize_source_url``, reused rather
  than reimplemented, so one page reported three ways is one source;
* a claim is the SHA-256 of its normalized text;
* a finding is the SHA-256 of its canonical URL, its normalized
  ``related_sub_topic``, and its normalized content.

Nothing here performs I/O, reads a clock, or calls a provider, so an identity
or a merged snapshot is a deterministic function of its inputs. Normalization
is deliberately conservative: it collapses formatting only — Unicode form,
case, whitespace, and prose punctuation — and never digits, units,
comparisons, negation, geography, or dates. Two records that differ in what
they assert therefore never share an identity, while two that differ only in
how they are written always do.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence

from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import Claim, Finding, ScoredSource

# Punctuation and symbols that survive normalization because they can change
# what a claim asserts: units and ranges ("km/h", "40%", "3-5 kg"), comparisons
# ("<400 ppm"), and currency ("$5"). Every other punctuation or symbol is
# typography and is dropped.
_SEMANTIC_SYMBOLS = frozenset("%+-<=>/~^|$\u20ac\u00a3\u00a5")

# Separates the fields of a composite identity. Normalized text cannot contain
# a control character, so no field can forge a boundary.
_FIELD_SEPARATOR = "\x1f"


def _is_decimal_point(text: str, index: int) -> bool:
    """True when ``text[index]`` is a ``.`` sitting between two digits.

    A decimal point is part of a number ("3.5"), unlike the sentence-final
    period that must collapse, so it is the one punctuation mark kept by
    position rather than by kind.
    """
    if index == 0 or index + 1 >= len(text):
        return False
    return text[index - 1].isdecimal() and text[index + 1].isdecimal()


def _normalized_text(text: str) -> str:
    """Collapse the formatting in ``text`` without losing what it asserts."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept: list[str] = []
    for index, char in enumerate(folded):
        if char.isalnum() or unicodedata.category(char).startswith("M"):
            kept.append(char)
        elif char.isspace():
            kept.append(" ")
        elif char in _SEMANTIC_SYMBOLS:
            kept.append(char)
        elif char == "." and _is_decimal_point(folded, index):
            kept.append(char)
    return " ".join("".join(kept).split())


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_fingerprint(text: str) -> str:
    """Return the stable identity of one claim's text.

    Two claims share a fingerprint exactly when they assert the same thing and
    differ only in how it was written; materially different years, numbers,
    units, comparisons, negation, or geography always differ.
    """
    return _digest(_normalized_text(text))


def finding_fingerprint(finding: Finding) -> str:
    """Return the stable identity of one finding.

    The key is its canonical URL, normalized ``related_sub_topic``, and
    normalized ``content``. ``source_title``, ``confidence``, and
    ``extracted_at`` are deliberately excluded: the same finding re-reported
    by a later pass is the same finding, however it was timestamped.
    """
    return _digest(
        _FIELD_SEPARATOR.join(
            (
                normalize_source_url(finding.source_url),
                _normalized_text(finding.related_sub_topic),
                _normalized_text(finding.content),
            )
        )
    )


def deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Fold ``findings`` onto one record per identity, in first-seen order.

    Duplicates keep the higher ``confidence``; a tie keeps the earlier record,
    so the result does not depend on how a caller ordered equal-confidence
    restatements. Deliberately exact-match only — no embeddings and no fuzzy
    thresholds, because a near-miss merge would silently drop evidence.
    """
    kept: dict[str, Finding] = {}
    for finding in findings:
        fingerprint = finding_fingerprint(finding)
        existing = kept.get(fingerprint)
        if existing is None or finding.confidence > existing.confidence:
            kept[fingerprint] = finding
    return list(kept.values())


def merge_source_snapshot(
    previous: Sequence[ScoredSource],
    current: Sequence[ScoredSource],
) -> list[ScoredSource]:
    """Return the canonical source snapshot after this pass re-scored.

    ``evaluated_sources`` replaces rather than appends, so the producer must
    emit every source it has ever assessed. This is how it does that without
    losing sources this pass never revisited: the latest assessment for a
    canonical URL wins, a source first seen earlier keeps its position, and
    each canonical URL appears at most once.
    """
    merged: dict[str, ScoredSource] = {}
    for source in (*previous, *current):
        url = normalize_source_url(source.url)
        existing = merged.get(url)
        # A provider failure, source cap, or missing model row is an
        # operational status rather than a quality judgement. Preserve a
        # previously valid score through those transient states; a new scored
        # record still replaces any older unscored record.
        if (
            existing is not None
            and existing.evaluation_status == "scored"
            and source.evaluation_status != "scored"
        ):
            continue
        merged[url] = source
    return list(merged.values())


def merge_claim_snapshot(
    previous: Sequence[Claim],
    current: Sequence[Claim],
) -> list[Claim]:
    """Return the canonical claim snapshot after this pass re-verified.

    Keyed by ``claim_fingerprint``, so the latest verdict for a claim wins: a
    claim the latest pass contradicted no longer reads as verified, and a
    stale positive judgement cannot outlive the evidence against it. Claims
    this pass never revisited keep their first-seen position, and each
    fingerprint appears at most once.
    """
    merged: dict[str, Claim] = {}
    for claim in (*previous, *current):
        merged[claim_fingerprint(claim.text)] = claim
    return list(merged.values())
