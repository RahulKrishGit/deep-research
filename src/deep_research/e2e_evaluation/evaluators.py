"""Deterministic whole-report quality gates and a bounded judge adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import fsum
from typing import Any

from pydantic import JsonValue

from deep_research.agents.identity import claim_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    canonical_claims,
    canonical_sources,
    render_evidence_ledger,
    render_reader_report,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.e2e_evaluation.cases import ScriptedDependencies
from deep_research.e2e_evaluation.models import (
    ControlledCase,
    DeterministicEvaluation,
    EvidenceLedgerSummary,
    SnapshotPass,
    WholeReportJudgeInput,
    WholeReportJudgeScore,
    WholeReportRubric,
)
from deep_research.utils.types import ReportPoint, ResearchState

MAX_READER_REPORT_WORDS = 8_000
MAX_EVIDENCE_SUMMARY_SOURCES = 16
MAX_EVIDENCE_SUMMARY_CLAIMS = 16


def _urls(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = normalize_source_url(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _points(composition: Any) -> list[ReportPoint]:
    points: list[ReportPoint] = [*composition.summary, *composition.constraints]
    for section in composition.sections:
        points.extend(section.points)
    return points


def _snapshot_repeats(
    snapshots: Sequence[SnapshotPass],
) -> tuple[int, int]:
    """Count repeated complete source/claim snapshots after the first pass."""
    if not snapshots:
        return (0, 0)
    first = snapshots[0]
    source_repeats = 0
    claim_repeats = 0
    for snapshot in snapshots[1:]:
        if snapshot.sources == first.sources:
            source_repeats += 1
        if snapshot.claims == first.claims:
            claim_repeats += 1
    return source_repeats, claim_repeats


def _terminal_counts(state: ResearchState) -> tuple[int, int, int, int, int]:
    published = [
        event for event in state.events if event.event_type == "graph.report.published"
    ]
    quality = [
        event for event in state.events if event.event_type == "graph.quality.assessed"
    ]
    memory = [
        event for event in state.events if event.event_type == "graph.memory.saved"
    ]
    report_writes = sum(
        int(event.metadata.get("report_writes", 0)) for event in published
    )
    evidence_writes = sum(
        int(event.metadata.get("evidence_writes", 0)) for event in published
    )
    memory_writes = sum(int(event.metadata.get("memory_writes", 0)) for event in memory)
    # Return publication count and the event indexes needed for the timing
    # gate.  The latter are deliberately derived from typed events only.
    quality_index = next(
        (index for index, event in enumerate(state.events) if event in quality),
        -1,
    )
    publication_index = next(
        (index for index, event in enumerate(state.events) if event in published),
        -1,
    )
    memory_index = next(
        (index for index, event in enumerate(state.events) if event in memory),
        -1,
    )
    timing_failure = int(
        quality_index < 0
        or publication_index < quality_index
        or (memory_writes and memory_index < publication_index)
    )
    return (
        len(published),
        report_writes,
        evidence_writes,
        memory_writes,
        timing_failure,
    )


def _expected_cli_summary(
    state: ResearchState, metrics: Mapping[str, Any]
) -> dict[str, JsonValue]:
    quality = state.quality
    return {
        "quality_status": (quality is not None and not quality.hard_failures),
        "coverage_ratio": float(metrics["coverage_ratio"]),
        "cited_sources": int(metrics["cited_sources"]),
        "scored_cited_sources": int(metrics["scored_cited_sources"]),
        "duplicate_claims": int(metrics["duplicate_claims"]),
        "duplicate_source_rows": int(metrics["duplicate_source_rows"]),
        "uncited_settled_points": int(metrics["uncited_settled_points"]),
    }


def deterministic_evaluation(
    case: ControlledCase,
    state: ResearchState,
    *,
    passes: Sequence[SnapshotPass] | None = None,
    dependencies: ScriptedDependencies | None = None,
    cli_summary: Mapping[str, Any] | None = None,
) -> DeterministicEvaluation:
    """Evaluate a whole report using typed state and no report parsing."""
    composition = state.composition
    integrity: list[str] = []
    hard: list[str] = []
    if composition is None:
        integrity.append("missing_composition")
        # The report cannot be meaningfully evaluated without its typed
        # composition. Keep the failure shape deterministic for callers.
        raise ValueError("whole-report evaluation requires state.composition")

    quality = compute_report_quality(state, composition)
    hard.extend(quality.hard_failures)
    topics = list(case.sub_topics)
    topic_ids = {topic.coverage_id for topic in topics}
    claims = list(state.verified_claims or composition.claims)
    sources = list(state.evaluated_sources or composition.sources)
    source_urls = _urls([source.url for source in sources])
    source_url_set = set(source_urls)
    claim_ids: set[str] = set()
    claim_fingerprints: set[str] = set()
    duplicate_claims = 0
    for claim in claims:
        fingerprint = claim_fingerprint(claim.text)
        if claim.claim_id in claim_ids or fingerprint in claim_fingerprints:
            duplicate_claims += 1
        claim_ids.add(claim.claim_id)
        claim_fingerprints.add(fingerprint)
    duplicate_source_rows = len(sources) - len(source_url_set)

    points = _points(composition)
    cited_urls = _urls([url for point in points for url in point.source_urls])
    scored_by_url = {
        normalize_source_url(source.url): source.evaluation_status == "scored"
        for source in canonical_sources(sources)
    }
    scored_cited = sum(bool(scored_by_url.get(url, False)) for url in cited_urls)

    read_urls = (
        set(_urls(dependencies.read_urls)) if dependencies is not None else set()
    )
    read_cited = sum(url in read_urls for url in cited_urls)
    source_read_ratio = read_cited / len(cited_urls) if cited_urls else 0.0

    claims_by_id = {claim.claim_id: claim for claim in canonical_claims(claims)}
    claims_with_provenance = 0
    for claim in claims_by_id.values():
        passages = claim.verification_evidence
        if passages and all(
            normalize_source_url(passage.source_url) in read_urls
            for passage in passages
        ):
            claims_with_provenance += 1
    claim_count = len(claims_by_id)
    claim_provenance_ratio = (
        claims_with_provenance / claim_count if claim_count else 0.0
    )

    linked_points = 0
    for point in points:
        linked_claims = [
            claims_by_id[claim_id]
            for claim_id in point.claim_ids
            if claim_id in claims_by_id
        ]
        point_urls = _urls(point.source_urls)
        if (
            linked_claims
            and point_urls
            and all(
                url
                in _urls(
                    [
                        source_url
                        for claim in linked_claims
                        for source_url in claim.source_urls
                    ]
                )
                for url in point_urls
            )
            and all(url in source_url_set for url in point_urls)
        ):
            linked_points += 1
    citation_count = len(points)
    citation_linkage_ratio = linked_points / citation_count if citation_count else 0.0

    covered_ids = {
        coverage_id
        for claim in claims_by_id.values()
        for coverage_id in claim.consumed_coverage_ids
        if coverage_id in topic_ids
    }
    attempted_ids = {
        topic.coverage_id
        for topic in topics
        if any(
            finding.related_sub_topic == topic.title for finding in state.raw_findings
        )
        or topic.coverage_id in covered_ids
    }
    planned_topics = len(topics)
    covered_topics = len(covered_ids)
    attempted_topics = len(attempted_ids)
    coverage_ratio = covered_topics / planned_topics if planned_topics else 0.0

    contradicted = sum(
        claim.verdict == "contradicted" for claim in claims_by_id.values()
    )
    disclosed = sum(
        claim.verdict == "contradicted"
        and any(claim.claim_id in note for note in composition.uncertainty_notes)
        for claim in claims_by_id.values()
    )
    uncited = quality.uncited_settled_points

    snapshots = list(passes or case.passes)
    repeated_sources, repeated_claims = _snapshot_repeats(snapshots)
    critic_targets = len(
        {target for item in snapshots for target in item.critic_targets}
    )
    final_covered = {
        coverage_id
        for claim in claims_by_id.values()
        for coverage_id in claim.consumed_coverage_ids
    }
    closed_targets = len(
        {
            target
            for item in snapshots
            for target in item.critic_targets
            if target in final_covered
        }
    )
    new_evidence = len(
        {topic for item in snapshots[1:] for topic in item.new_evidence_topics}
    )
    forced_refinements = sum(item.force_refinement for item in snapshots)

    publication_events, report_writes, evidence_writes, memory_writes, timing = (
        _terminal_counts(state)
    )
    report = state.report or render_reader_report(composition)
    ledger = state.report_evidence or render_evidence_ledger(composition)
    report_words = len(report.split())
    ledger_words = len(ledger.split())

    raw_metrics: dict[str, Any] = {
        "coverage_ratio": coverage_ratio,
        "cited_sources": len(cited_urls),
        "scored_cited_sources": scored_cited,
        "duplicate_claims": duplicate_claims,
        "duplicate_source_rows": duplicate_source_rows,
        "uncited_settled_points": uncited,
    }
    expected_summary = _expected_cli_summary(state, raw_metrics)
    cli_matches = cli_summary is None or all(
        cli_summary.get(key) == value for key, value in expected_summary.items()
    )

    if source_read_ratio < 1.0:
        integrity.append("source_read_provenance")
    if scored_cited != len(cited_urls):
        integrity.append("unscored_cited_sources")
    if claim_provenance_ratio < 1.0:
        integrity.append("checked_claim_provenance")
    if citation_linkage_ratio < 1.0:
        integrity.append("citation_linkage")
    if duplicate_claims:
        integrity.append("duplicate_claims")
    if duplicate_source_rows:
        integrity.append("duplicate_source_rows")
    if contradicted != disclosed:
        integrity.append("contradiction_disclosure")
    if planned_topics and coverage_ratio < 0.80:
        integrity.append("coverage_below_0.80")
    if case.expected_refinement_topics and not set(
        case.expected_refinement_topics
    ).issubset(final_covered):
        integrity.append("refinement_targets_unresolved")
    if case.expected_refinement_topics and not set(
        case.expected_refinement_topics
    ).intersection(
        {topic for item in snapshots[1:] for topic in item.new_evidence_topics}
    ):
        integrity.append("refinement_added_no_evidence")
    if critic_targets and closed_targets < critic_targets:
        integrity.append("critic_targets_unresolved")
    if critic_targets and new_evidence < critic_targets:
        integrity.append("critic_refinement_no_new_evidence")
    if report_words > MAX_READER_REPORT_WORDS:
        integrity.append("reader_report_not_concise")
    if not ledger.strip() or ledger.strip() == report.strip():
        integrity.append("evidence_ledger_not_separate")
    if publication_events != 1 or report_writes != 1 or evidence_writes != 1:
        integrity.append("terminal_publication")
    if timing:
        integrity.append("publication_memory_timing")
    if not cli_matches:
        integrity.append("cli_summary_mismatch")
    hard.extend(integrity)

    return DeterministicEvaluation(
        planned_topics=planned_topics,
        attempted_topics=attempted_topics,
        covered_topics=covered_topics,
        coverage_ratio=coverage_ratio,
        read_sources=len(read_urls.intersection(source_url_set)),
        cited_sources=len(cited_urls),
        scored_cited_sources=scored_cited,
        source_read_provenance_ratio=source_read_ratio,
        checked_claims=claim_count,
        claims_with_provenance=claims_with_provenance,
        checked_claim_provenance_ratio=claim_provenance_ratio,
        resolved_citations=linked_points,
        citation_count=citation_count,
        citation_linkage_ratio=citation_linkage_ratio,
        duplicate_claims=duplicate_claims,
        duplicate_source_rows=duplicate_source_rows,
        contradicted_claims=contradicted,
        disclosed_contradictions=disclosed,
        uncited_settled_points=uncited,
        reader_report_words=report_words,
        evidence_ledger_words=ledger_words,
        reader_report_chars=len(report),
        evidence_ledger_chars=len(ledger),
        refinement_passes=len(snapshots),
        critic_targets=critic_targets,
        closed_critic_targets=closed_targets,
        new_evidence_in_refinement=new_evidence,
        publication_events=publication_events,
        report_writes=report_writes,
        evidence_writes=evidence_writes,
        memory_writes=memory_writes,
        cli_summary_matches=cli_matches,
        integrity_failures=integrity,
        hard_failures=hard,
        repeated_source_snapshot_passes=repeated_sources,
        repeated_claim_snapshot_passes=repeated_claims,
        gate_forced_refinement_passes=forced_refinements,
    )


def evidence_ledger_summary(
    composition: Any,
) -> EvidenceLedgerSummary:
    """Build the bounded summary permitted in the judge contract."""
    sources = canonical_sources(composition.sources)
    claims = canonical_claims(composition.claims)
    return EvidenceLedgerSummary(
        source_count=len(sources),
        scored_source_count=sum(
            source.evaluation_status == "scored" for source in sources
        ),
        claim_count=len(claims),
        verified_claim_count=sum(claim.verdict == "verified" for claim in claims),
        contradicted_claim_count=sum(
            claim.verdict == "contradicted" for claim in claims
        ),
        verification_passage_count=sum(
            len(claim.verification_evidence) for claim in claims
        ),
        duplicate_source_rows=0,
        duplicate_claims=0,
        source_titles=[
            source.title[:120] for source in sources[:MAX_EVIDENCE_SUMMARY_SOURCES]
        ],
        claim_summaries=[
            claim.text[:160] for claim in claims[:MAX_EVIDENCE_SUMMARY_CLAIMS]
        ],
    )


def build_judge_input(
    case: ControlledCase,
    state: ResearchState,
    metrics: DeterministicEvaluation,
) -> WholeReportJudgeInput:
    """Project only the five allowed, bounded whole-report judge fields."""
    composition = state.composition
    if composition is None:
        raise ValueError("whole-report judge requires state.composition")
    report = state.report or render_reader_report(composition)
    metrics_payload: dict[str, float | int | bool] = {
        "coverage_ratio": metrics.coverage_ratio,
        "source_read_provenance_ratio": metrics.source_read_provenance_ratio,
        "scored_cited_source_ratio": (
            metrics.scored_cited_sources / metrics.cited_sources
            if metrics.cited_sources
            else 0.0
        ),
        "checked_claim_provenance_ratio": metrics.checked_claim_provenance_ratio,
        "citation_linkage_ratio": metrics.citation_linkage_ratio,
        "duplicate_claims": metrics.duplicate_claims,
        "duplicate_source_rows": metrics.duplicate_source_rows,
        "disclosed_contradictions": metrics.disclosed_contradictions,
        "uncited_settled_points": metrics.uncited_settled_points,
        "reader_report_words": metrics.reader_report_words,
        "evidence_ledger_words": metrics.evidence_ledger_words,
        "cli_summary_matches": metrics.cli_summary_matches,
        "integrity_passed": metrics.integrity_passed,
    }
    return WholeReportJudgeInput(
        question=case.question,
        scoped_plan=list(case.sub_topics),
        reader_report=report[:16_000],
        deterministic_metrics=metrics_payload,
        evidence_ledger_summary=evidence_ledger_summary(composition),
    )


def judge_whole_report(
    payload: WholeReportJudgeInput,
    *,
    rubric: WholeReportRubric | None = None,
) -> WholeReportJudgeScore:
    """Score the bounded contract with a deterministic offline judge double."""
    dimensions = {
        dimension: 0.90 for dimension in (rubric or WholeReportRubric()).dimensions
    }
    metrics = payload.deterministic_metrics
    if not bool(metrics.get("integrity_passed", False)):
        score = 0.95
        rationale = (
            "Reader quality is strong, but deterministic integrity remains "
            "authoritative."
        )
    else:
        score = min(
            1.0,
            max(
                0.0,
                fsum(
                    float(metrics.get(key, 0.0))
                    for key in (
                        "coverage_ratio",
                        "source_read_provenance_ratio",
                        "checked_claim_provenance_ratio",
                        "citation_linkage_ratio",
                        "cli_summary_matches",
                    )
                )
                / 5.0
                + 0.4,
            ),
        )
        rationale = "Offline rubric score from bounded reader and metrics."
    return WholeReportJudgeScore(
        score=score,
        dimensions=dimensions,
        rationale=rationale,
        rubric=rubric or WholeReportRubric(),
    )


def repetition_accepted(
    metrics: DeterministicEvaluation,
    judge: WholeReportJudgeScore,
    *,
    judge_floor: float = 0.70,
) -> bool:
    """Hard integrity always wins over the judge score."""
    return metrics.integrity_passed and judge.score >= judge_floor


def evaluate_whole_report(*args: Any, **kwargs: Any) -> DeterministicEvaluation:
    """Compatibility alias for callers that use verb-oriented naming."""
    return deterministic_evaluation(*args, **kwargs)


def evaluate_report_quality(*args: Any, **kwargs: Any) -> DeterministicEvaluation:
    """Alias used by callers that treat the report as the quality subject."""
    return deterministic_evaluation(*args, **kwargs)


def build_whole_report_judge_input(*args: Any, **kwargs: Any) -> WholeReportJudgeInput:
    """Explicitly named alias for the bounded judge projection."""
    return build_judge_input(*args, **kwargs)


__all__ = [
    "MAX_READER_REPORT_WORDS",
    "build_judge_input",
    "deterministic_evaluation",
    "evaluate_report_quality",
    "evaluate_whole_report",
    "evidence_ledger_summary",
    "judge_whole_report",
    "build_whole_report_judge_input",
    "repetition_accepted",
]
