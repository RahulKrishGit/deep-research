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
from deep_research.utils.types import (
    Finding,
    ScoredSource,
)

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


def _passage_key(finding: Finding) -> tuple[str, str, str, str, str] | None:
    """The identity of the evidence one finding carries, or ``None``.

    A finding the acquisition path produced names the read and locator it was
    mined from and quotes the page verbatim, so ``(url, sub-topic, read,
    locator, snippet)`` says exactly which sentence of which page this is. Two
    records with that key are one piece of evidence however each restated it:
    the restatement is the model's prose, and prose is not identity. A record
    that names no passage (a legacy or raw caller) has no such key, and nothing
    folds on it.
    """
    if not finding.read_id or not finding.locator or not finding.snippet:
        return None
    return (
        normalize_source_url(finding.source_url),
        _normalized_text(finding.related_sub_topic),
        finding.read_id,
        finding.locator,
        _normalized_text(finding.snippet),
    )


def deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Fold ``findings`` onto one record per identity, in first-seen order.

    Duplicates keep the higher ``confidence``; a tie keeps the earlier record,
    so the result does not depend on how a caller ordered equal-confidence
    restatements. Deliberately exact-match only — no embeddings and no fuzzy
    thresholds, because a near-miss merge would silently drop evidence.

    Identity is read twice, and the second reading is what makes a re-read
    free. ``finding_fingerprint`` keys the record on the page, the sub-topic
    and the prose, which is right when two records are the same *statement*.
    ``_passage_key`` then folds the records that are the same *evidence*: one
    passage of one page, quoted identically, restated twice — the shape a later
    pass produces when it re-reads a page the run already read and mines the
    sentence it already holds. Two different sentences of one passage stay two
    findings, because the fold is on the sentence and not on the passage alone.

    The fold keeps the record, and not its silences. ``raw_findings`` is
    append-only across research rounds, and a later extraction of the same
    passage may come back unbound — an id outside the plan is dropped rather
    than fatal — so a confidence-only fold would let a restatement delete the
    only record of which planned target that evidence answers. Target ids are
    unioned in the kept record's order, and its own dates and provenance win,
    with a field it lacks filled from the duplicate: the same argument covers
    whose figure the evidence is, because a restatement that recorded no
    attribution must not put an issuer's count back under the host that
    carried it.
    """
    kept: dict[str, Finding] = {}
    for finding in findings:
        fingerprint = finding_fingerprint(finding)
        existing = kept.get(fingerprint)
        if existing is None:
            kept[fingerprint] = finding
            continue
        if finding.confidence > existing.confidence:
            winner, loser = finding, existing
        else:
            winner, loser = existing, finding
        kept[fingerprint] = _merge_duplicate_findings(winner, loser)

    by_passage: dict[tuple[str, str, str, str, str], str] = {}
    for fingerprint, finding in list(kept.items()):
        key = _passage_key(finding)
        if key is None:
            continue
        held = by_passage.get(key)
        if held is None:
            by_passage[key] = fingerprint
            continue
        kept[held] = _merge_duplicate_findings(kept[held], finding)
        del kept[fingerprint]
    return list(kept.values())


def _merge_duplicate_findings(winner: Finding, loser: Finding) -> Finding:
    """The record to keep, carrying both records' bindings and provenance.

    Only the fields a fold could otherwise erase are merged. Content, URL,
    topic and confidence are the identity and the ranking, and the kept
    record's own values stand.

    Provenance is filled from the duplicate the way a date is, and in the same
    order — the kept record's own value wins and a field it lacks is filled —
    except for the attribution pair, which is one claim recorded in two
    halves: an attribution without its quote is not admitted, so a record that
    already carries a named body keeps both of its own halves rather than
    taking the loser's name beside its own phrase.
    """
    target_ids = list(dict.fromkeys([*winner.target_ids, *loser.target_ids]))
    dates = {
        name: getattr(winner, name) or getattr(loser, name)
        for name in (
            "vintage",
            "statement_date",
            "data_period",
            "measure_scope",
            "release_date",
        )
    }
    merged = winner.model_copy(update={"target_ids": target_ids, **dates})
    if not merged.attributed_issuer:
        merged = merged.model_copy(
            update={
                "attributed_issuer": loser.attributed_issuer,
                "attribution_quote": loser.attribution_quote,
            }
        )
    if not merged.snippet and loser.snippet:
        merged = merged.model_copy(
            update={
                "snippet": loser.snippet,
                "read_id": loser.read_id,
                "locator": loser.locator,
                "figures": list(loser.figures),
            }
        )
    if merged == winner:
        return winner
    return merged


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

    A provider failure, source cap, or missing model row is an operational
    status rather than a quality judgement, so a previously valid score is
    preserved through those transient states — but only while it is still an
    assessment of the same content. ``assessment_revision`` is the recorded
    evidence of that: once both records carry one and they differ, the
    document changed, the new record is about content the old score never saw,
    and the stale score is dropped rather than credited to it. Two records
    with no recorded revision cannot show a change, so they keep the historic
    behavior and the earlier score survives.
    """
    merged: dict[str, ScoredSource] = {}
    for source in (*previous, *current):
        url = normalize_source_url(source.url)
        existing = merged.get(url)
        if (
            existing is not None
            and existing.evaluation_status == "scored"
            and source.evaluation_status != "scored"
            and not _revision_changed(existing, source)
        ):
            continue
        merged[url] = source
    return list(merged.values())


def _revision_changed(existing: ScoredSource, incoming: ScoredSource) -> bool:
    """True when two assessments are demonstrably about different content.

    An empty revision is not evidence of a change: every record written before
    the revision contract existed carries none, and an unscored record built
    without a read says nothing about which content it was about.
    """
    return (
        bool(existing.assessment_revision)
        and bool(incoming.assessment_revision)
        and existing.assessment_revision != incoming.assessment_revision
    )
