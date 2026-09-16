"""Pure report-quality metrics and hard-failure detection.

The quality pass consumes the typed research state and the typed composition
that the Synthesizer is about to render.  It deliberately never inspects
Markdown: reader points, claim links, source URLs, and the composition's
explicit metadata are the source of truth.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from deep_research.agents.identity import (
    claim_fingerprint,
    deduplicate_findings,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.report import ReportComposition, ReportPoint
from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import (
    Claim,
    ReportQualitySnapshot,
    ResearchState,
    ScoredSource,
)

# A plan with five or more topics is broad enough that an apparently good
# report must still account for most of the planned surface.
BROAD_PLAN_MIN_TOPICS = 5
BROAD_PLAN_COVERAGE_THRESHOLD = 0.80

_UNRESOLVED_MARKERS = frozenset(
    {
        "?",
        "[?]",
        "[citation needed]",
        "citation needed",
        "n/a",
        "tbd",
        "unknown",
        "unresolved",
    }
)


def _reader_points(composition: ReportComposition) -> Iterator[ReportPoint]:
    """Yield every typed point that appears in the reader report."""

    yield from composition.summary
    yield from composition.constraints
    for section in composition.sections:
        yield from section.points


def _source_rows(
    state: ResearchState,
    composition: ReportComposition,
) -> list[ScoredSource]:
    """Choose the state snapshot, falling back to the composition fixture."""

    return list(state.evaluated_sources or composition.sources)


def _claim_rows(state: ResearchState, composition: ReportComposition) -> list[Claim]:
    """Choose the state snapshot, falling back to the composition fixture."""

    return list(state.verified_claims or composition.claims)


def _canonical_sources(
    state: ResearchState,
    composition: ReportComposition,
) -> list[ScoredSource]:
    """Return one source assessment per canonical URL."""

    rows = _source_rows(state, composition)
    return merge_source_snapshot([], rows)


def _canonical_claims(
    state: ResearchState,
    composition: ReportComposition,
) -> list[Claim]:
    """Return one checked claim per canonical claim identity."""

    rows = _claim_rows(state, composition)
    return merge_claim_snapshot([], rows)


def _canonical_url_set(sources: Sequence[ScoredSource]) -> set[str]:
    return {
        normalized
        for source in sources
        if (normalized := normalize_source_url(source.url))
    }


def _point_claims(
    point: ReportPoint,
    claims_by_id: dict[str, Claim],
) -> list[Claim]:
    """Resolve a point's claim IDs while preserving point order."""

    resolved: list[Claim] = []
    seen: set[str] = set()
    for claim_id in point.claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None or claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        resolved.append(claim)
    return resolved


def _claim_aliases(claims: Sequence[Claim]) -> dict[str, Claim]:
    """Map canonical IDs and text fingerprints to checked claims."""

    aliases: dict[str, Claim] = {}
    for claim in claims:
        aliases[claim.claim_id] = claim
        aliases.setdefault(claim_fingerprint(claim.text), claim)
    return aliases


def _normalized_unique(values: Sequence[str]) -> list[str]:
    """Return normalized non-empty values in first-seen order."""

    result: list[str] = []
    for value in values:
        normalized = normalize_source_url(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _is_unresolved_marker(value: str) -> bool:
    return value.strip().casefold() in _UNRESOLVED_MARKERS


def _mentions_coverage_id(note: str, coverage_id: str) -> bool:
    """Match a plan ID in a typed uncertainty/gap note.

    This is intentionally a token match over a typed note, not a parse of
    rendered Markdown.  It prevents ``topic-01`` from matching
    ``topic-010`` while allowing ordinary prose around the ID.
    """

    pattern = rf"(?<![\w-]){re.escape(coverage_id.casefold())}(?![\w-])"
    return re.search(pattern, note.casefold()) is not None


def compute_report_quality(
    state: ResearchState,
    composition: ReportComposition,
) -> ReportQualitySnapshot:
    """Compute deterministic report metrics and integrity hard failures.

    ``state`` supplies the cumulative evidence snapshots and artifact
    presence.  ``composition`` supplies the exact typed points that were
    selected for the reader report.  No report prose is parsed.
    """

    points = list(_reader_points(composition))
    claim_rows = _claim_rows(state, composition)
    canonical_claims = _canonical_claims(state, composition)
    source_rows = _source_rows(state, composition)
    canonical_sources = _canonical_sources(state, composition)
    findings = list(state.raw_findings or composition.findings)
    topics = list(state.sub_topics or composition.sub_topics)
    topic_ids = [topic.coverage_id for topic in topics]
    topic_id_set = set(topic_ids)

    claims_by_id = _claim_aliases(canonical_claims)
    covered_ids: set[str] = set()
    cited_urls: list[str] = []
    unresolved_citations = False
    uncited_settled_points = 0
    contradicted_settled_claim = False

    assessed_urls = _canonical_url_set(canonical_sources)
    for point in points:
        claims = _point_claims(point, claims_by_id)
        point_urls = _normalized_unique(point.source_urls)
        cited_urls.extend(point_urls)

        if not point.claim_ids or not point_urls or not claims:
            uncited_settled_points += 1
        if any(claim_id not in claims_by_id for claim_id in point.claim_ids):
            unresolved_citations = True
        if any(_is_unresolved_marker(url) for url in point.source_urls):
            unresolved_citations = True

        for claim in claims:
            if claim.verdict == "contradicted":
                contradicted_settled_claim = True
            covered_ids.update(
                coverage_id
                for coverage_id in claim.consumed_coverage_ids
                if coverage_id in topic_id_set
            )
            claim_urls = {
                normalize_source_url(url) for url in claim.source_urls
            }
            if any(url not in claim_urls for url in point_urls):
                unresolved_citations = True

        if any(url not in assessed_urls for url in point_urls):
            unresolved_citations = True

    cited_urls = _normalized_unique(cited_urls)
    source_by_url = {
        normalize_source_url(source.url): source for source in canonical_sources
    }
    scored_cited_sources = sum(
        source_by_url[url].evaluation_status == "scored"
        for url in cited_urls
        if url in source_by_url
    )
    unscored_cited_sources = any(
        url not in source_by_url
        or source_by_url[url].evaluation_status != "scored"
        for url in cited_urls
    )

    planned_topics = len(topics)
    covered_topics = len(covered_ids)
    coverage_ratio = covered_topics / planned_topics if planned_topics else 0.0
    unresolved_topic_ids = [
        coverage_id
        for coverage_id in topic_ids
        if coverage_id not in covered_ids
        and any(
            _mentions_coverage_id(note, coverage_id)
            for note in composition.uncertainty_notes
        )
    ]

    unique_source_urls = _canonical_url_set(canonical_sources)
    unique_findings = len(deduplicate_findings(findings))
    duplicate_source_rows = max(0, len(source_rows) - len(unique_source_urls))
    seen_claim_ids: set[str] = set()
    seen_claim_fingerprints: set[str] = set()
    duplicate_claims = 0
    for claim in claim_rows:
        fingerprint = claim_fingerprint(claim.text)
        if (
            claim.claim_id in seen_claim_ids
            or fingerprint in seen_claim_fingerprints
        ):
            duplicate_claims += 1
        seen_claim_ids.add(claim.claim_id)
        seen_claim_fingerprints.add(fingerprint)

    hard_failures: list[str] = []
    if duplicate_claims:
        hard_failures.append("duplicate_claims")
    if duplicate_source_rows:
        hard_failures.append("duplicate_source_rows")
    if unresolved_citations:
        hard_failures.append("unresolved_citations")
    if uncited_settled_points:
        hard_failures.append("uncited_settled_points")
    if unscored_cited_sources:
        hard_failures.append("unscored_cited_sources")
    if contradicted_settled_claim:
        hard_failures.append("contradicted_settled_claims")
    if (
        planned_topics >= BROAD_PLAN_MIN_TOPICS
        and coverage_ratio < BROAD_PLAN_COVERAGE_THRESHOLD
    ):
        hard_failures.append("broad_plan_coverage_below_0.80")
    if not composition.scope.strip():
        hard_failures.append("missing_scope")
    if not composition.as_of.strip():
        hard_failures.append("missing_as_of")
    if not state.report or not state.report.strip():
        hard_failures.append("missing_reader_report")
    if not state.report_evidence or not state.report_evidence.strip():
        hard_failures.append("missing_evidence_ledger")

    return ReportQualitySnapshot(
        coverage_ratio=coverage_ratio,
        planned_topics=planned_topics,
        covered_topics=covered_topics,
        unresolved_topic_ids=unresolved_topic_ids,
        unique_findings=unique_findings,
        unique_sources=len(unique_source_urls),
        cited_sources=len(cited_urls),
        scored_cited_source_ratio=(
            scored_cited_sources / len(cited_urls) if cited_urls else 0.0
        ),
        verified_claims=sum(
            claim.verdict == "verified" for claim in canonical_claims
        ),
        contradicted_claims=sum(
            claim.verdict == "contradicted" for claim in canonical_claims
        ),
        duplicate_claims=duplicate_claims,
        duplicate_source_rows=duplicate_source_rows,
        uncited_settled_points=uncited_settled_points,
        hard_failures=hard_failures,
    )


__all__ = [
    "BROAD_PLAN_COVERAGE_THRESHOLD",
    "BROAD_PLAN_MIN_TOPICS",
    "compute_report_quality",
]
