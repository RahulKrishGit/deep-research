"""Deterministic whole-report quality gates and a bounded judge adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from math import fsum
from typing import Any

from pydantic import JsonValue

from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    canonical_claims,
    canonical_sources,
    reader_citations,
    render_evidence_ledger,
    render_reader_report,
)
from deep_research.agents.report_review import semantic_review_passes
from deep_research.agents.sources import normalize_source_url
from deep_research.e2e_evaluation.cases import ScriptedDependencies
from deep_research.e2e_evaluation.models import (
    ControlledCase,
    DeterministicEvaluation,
    EvidenceLedgerSummary,
    SemanticReviewSummary,
    SnapshotPass,
    WholeReportJudgeInput,
    WholeReportJudgeScore,
    WholeReportRubric,
)
from deep_research.utils.types import ReportPoint, ReportReview, ResearchState

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


def _has_production_graph_events(state: ResearchState) -> bool:
    """Whether state came through the compiled graph's node wrappers."""
    return any(
        event.event_type == "graph.node.completed"
        and event.metadata.get("node") == "planner"
        for event in state.events
    )


def _snapshot_events(
    state: ResearchState, agent_name: str
) -> list[Any]:
    event_type = f"agent.{agent_name}.snapshot.completed"
    return [
        event
        for event in state.events
        if event.event_type == event_type
        and event.metadata.get("snapshot_kind") == "complete"
    ]


def _observed_snapshot_repeats(state: ResearchState) -> tuple[int, int]:
    """Count duplicate complete snapshots emitted by source/fact nodes."""
    source_events = _snapshot_events(state, "source_evaluator")
    claim_events = _snapshot_events(state, "fact_checker")

    def repeats(events: Sequence[Any]) -> int:
        if not events:
            return 0
        first = events[0].metadata.get("snapshot_fingerprint")
        return sum(
            event.metadata.get("snapshot_fingerprint") == first
            for event in events[1:]
        )

    return repeats(source_events), repeats(claim_events)


def _observed_coverage_ids(state: ResearchState, agent_name: str) -> list[set[str]]:
    """Return coverage identities from complete snapshot events, in order."""
    result: list[set[str]] = []
    for event in _snapshot_events(state, agent_name):
        values = event.metadata.get("coverage_ids", [])
        if isinstance(values, list):
            result.append({value for value in values if isinstance(value, str)})
    return result


def _observed_critic_targets(state: ResearchState) -> set[str]:
    targets: set[str] = set()
    for event in state.events:
        if event.event_type != "agent.critic.targets.recorded":
            continue
        values = event.metadata.get("coverage_ids", [])
        if isinstance(values, list):
            targets.update(value for value in values if isinstance(value, str))
    return targets


def _observed_attempted_topics(state: ResearchState) -> set[str]:
    attempted: set[str] = set()
    for event in state.events:
        if not event.event_type.endswith((".topic.attempted", ".topic.skipped")):
            continue
        coverage_id = event.metadata.get("coverage_id")
        if isinstance(coverage_id, str) and coverage_id:
            attempted.add(coverage_id)
    return attempted


def _rendered_citation_resolution(
    report: str, composition: Any
) -> bool:
    """Validate rendered markers against the rendered cited-only reference list."""
    if report.strip() != render_reader_report(composition).strip():
        return False
    citations = reader_citations(composition)
    expected_numbers = {citation.number for citation in citations}
    marker_numbers = {
        int(value) for value in re.findall(r"\[(\d+)\]", report)
    }
    if marker_numbers != expected_numbers:
        return False
    reference_section = report.split("## References", 1)
    if len(reference_section) != 2:
        return not expected_numbers
    rows = re.findall(
        r"(?m)^(\d+)\. .*? — (\S+)\s*$",
        reference_section[1],
    )
    rendered_numbers = {int(number) for number, _url in rows}
    rendered_urls = {url for _number, url in rows}
    return rendered_numbers == expected_numbers and rendered_urls == {
        citation.url for citation in citations
    }


def production_cli_summary(lines: Sequence[str]) -> dict[str, JsonValue]:
    """Parse only the bounded summary lines emitted by ``cli.render_summary``."""
    summary: dict[str, JsonValue] = {}
    quality_line = next(
        (line for line in lines if line.startswith("Quality: ")), None
    )
    if quality_line is not None:
        summary["quality_status"] = quality_line.removeprefix("Quality: ").split(
            " ", 1
        )[0]
        coverage = re.search(r"(\d+)/(\d+) topics covered, (\d+)%", quality_line)
        if coverage is not None and int(coverage.group(2)):
            summary["coverage_ratio"] = int(coverage.group(3)) / 100
    evidence_line = next(
        (line for line in lines if line.startswith("Evidence: ")), None
    )
    if evidence_line is not None:
        match = re.search(
            r"Evidence: (\d+) cited sources; (\d+) scored; "
            r"(\d+) verified, (\d+) contradicted",
            evidence_line,
        )
        if match:
            summary.update(
                {
                    "cited_sources": int(match.group(1)),
                    "scored_cited_sources": int(match.group(2)),
                    "verified_claims": int(match.group(3)),
                    "contradicted_claims": int(match.group(4)),
                }
            )
    integrity_line = next(
        (line for line in lines if line.startswith("Integrity: ")), None
    )
    if integrity_line is not None:
        match = re.search(
            r"Integrity: (\d+) duplicate claims; (\d+) duplicate source rows; "
            r"(\d+) uncited settled points",
            integrity_line,
        )
        if match:
            summary.update(
                {
                    "duplicate_claims": int(match.group(1)),
                    "duplicate_source_rows": int(match.group(2)),
                    "uncited_settled_points": int(match.group(3)),
                }
            )
    return summary


def _terminal_counts(state: ResearchState) -> tuple[int, int, int, int, int]:
    published = [
        event for event in state.events if event.event_type == "graph.report.published"
    ]
    memory = [
        event for event in state.events if event.event_type == "graph.memory.saved"
    ]
    report_writes = 0
    evidence_writes = 0
    for event in published:
        if "report_writes" in event.metadata or "evidence_writes" in event.metadata:
            report_writes += int(event.metadata.get("report_writes", 0))
            evidence_writes += int(event.metadata.get("evidence_writes", 0))
            continue
        document_writes = int(event.metadata.get("document_writes", 0))
        report_writes += int(document_writes >= 1)
        evidence_writes += int(document_writes >= 2)
    memory_writes = sum(int(event.metadata.get("memory_writes", 0)) for event in memory)
    if not memory_writes:
        memory_writes = sum(
            int(event.metadata.get("memory_writes", 0)) for event in published
        )
    # Return publication count and the event indexes needed for the timing
    # gate.  The latter are deliberately derived from typed events only.
    quality_indexes = [
        index
        for index, event in enumerate(state.events)
        if event.event_type == "graph.quality.assessed"
    ]
    publication_indexes = [
        index
        for index, event in enumerate(state.events)
        if event.event_type == "graph.report.published"
    ]
    memory_indexes = [
        index
        for index, event in enumerate(state.events)
        if event.event_type == "graph.memory.saved"
    ]
    # Publication must follow the final quality assessment, not merely the
    # first assessment in a multi-pass run.
    quality_index = max(quality_indexes, default=-1)
    publication_index = min(publication_indexes, default=-1)
    memory_index = min(memory_indexes, default=-1)
    if memory_writes and memory_index < 0:
        # Production folds the memory-write count into its one terminal
        # publication event; the publisher performs it after both documents
        # inside that terminal operation.
        memory_index = publication_index
    timing_failure = int(
        quality_index < 0
        or publication_index <= quality_index
        or (memory_writes and memory_index < publication_index)
    )
    return (
        len(published),
        report_writes,
        evidence_writes,
        memory_writes,
        timing_failure,
    )


def _publication_operations_match(
    dependencies: ScriptedDependencies | None, memory_writes: int
) -> bool:
    """Check the scripted publisher's exact terminal operation sequence.

    The terminal node publishes a three-artifact set — reader Markdown, evidence
    Markdown, then the quality record — and only then writes memory, for an
    accepted report. The expectation names all three: the earlier two-artifact
    form predates the quality record, and while the double classified the third
    document as a second reader write, this compared a sequence that no longer
    described the run and reported an order failure that had not happened.
    """
    if dependencies is None:
        return True
    expected = [
        "reader_document",
        "evidence_document",
        "quality_document",
    ] + ["memory_claim"] * memory_writes
    return dependencies.publication_operations == expected


def _expected_cli_summary(
    state: ResearchState, metrics: Mapping[str, Any]
) -> dict[str, JsonValue]:
    quality = state.quality
    quality_status = (
        state.composition.quality_status
        if state.composition is not None
        else (
            "accepted"
            if quality is not None and not quality.hard_failures
            else "partial"
        )
    )
    return {
        "quality_status": quality_status,
        "coverage_ratio": round(
            float(
                quality.coverage_ratio
                if quality is not None
                else metrics["coverage_ratio"]
            ),
            2,
        ),
        "cited_sources": int(metrics["cited_sources"]),
        "scored_cited_sources": int(metrics["scored_cited_sources"]),
        # The formatter prints these two from the same quality snapshot, and
        # the plan requires the CLI to match state *exactly*: leaving them out
        # of the comparison meant a formatter that printed either count wrongly
        # still agreed with state. ``None`` when no quality pass ran, because
        # ``render_summary`` then prints no evidence line at all.
        "verified_claims": (
            int(quality.verified_claims) if quality is not None else None
        ),
        "contradicted_claims": (
            int(quality.contradicted_claims) if quality is not None else None
        ),
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
    cli_output: Sequence[str] | None = None,
) -> DeterministicEvaluation:
    """Evaluate a whole report from typed state and bounded formatter output."""
    composition = state.composition
    integrity: list[str] = []
    hard: list[str] = []
    if composition is None:
        integrity.append("missing_composition")
        # The report cannot be meaningfully evaluated without its typed
        # composition. Keep the failure shape deterministic for callers.
        raise ValueError("whole-report evaluation requires state.composition")

    # Which of the two metric branches below ran. Every *observed* leg is
    # gated on this, and when it is False the evaluator substitutes fixture
    # values that are true by construction — so a verdict that could not say
    # which branch ran reported a fixture-based "accepted".
    graph_observed = _has_production_graph_events(state)
    if not graph_observed:
        integrity.append("production_graph_unobserved")

    quality = compute_report_quality(state, composition)
    hard.extend(quality.hard_failures)
    topics = list(case.sub_topics)
    topic_ids = {topic.coverage_id for topic in topics}
    claims = list(state.verified_claims or composition.claims)
    sources = list(state.evaluated_sources or composition.sources)
    source_urls = _urls([source.url for source in sources])
    source_url_set = set(source_urls)
    finding_fingerprints = [
        finding_fingerprint(finding) for finding in state.raw_findings
    ]
    duplicate_finding_rows = len(finding_fingerprints) - len(
        set(finding_fingerprints)
    )
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
    # The cited set is what the reader report prints, read from the same index
    # the renderer numbers its markers with: Task 7 resolves each statement's
    # citations from its selected evidence, so a verified cluster cites the
    # passages that carried its verdict and not only the finding that first
    # raised it. The per-point model-supplied links are still what the linkage
    # check below judges.
    cited_urls = _urls([citation.url for citation in reader_citations(composition)])
    scored_by_url = {
        normalize_source_url(source.url): source.evaluation_status == "scored"
        for source in canonical_sources(sources)
    }
    scored_cited = sum(bool(scored_by_url.get(url, False)) for url in cited_urls)

    read_urls = (
        set(_urls(dependencies.read_urls)) if dependencies is not None else set()
    )
    read_source_count = sum(url in read_urls for url in source_url_set)
    source_read_ratio = (
        read_source_count / len(source_url_set) if source_url_set else 0.0
    )

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
    observed_attempts = _observed_attempted_topics(state)
    if graph_observed:
        attempted_ids = observed_attempts.intersection(topic_ids)
    else:
        attempted_ids = {
            topic.coverage_id
            for topic in topics
            if any(
                finding.related_sub_topic == topic.title
                for finding in state.raw_findings
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
    if graph_observed:
        repeated_sources, repeated_claims = _observed_snapshot_repeats(state)
        observed_targets = _observed_critic_targets(state)
        critic_target_ids = observed_targets
        source_coverage = _observed_coverage_ids(state, "source_evaluator")
        claim_coverage = _observed_coverage_ids(state, "fact_checker")
        repeated_passes = max(
            len(_snapshot_events(state, "fact_checker")),
            len(_snapshot_events(state, "source_evaluator")),
            1,
        )
        forced_refinements = sum(
            event.event_type == "graph.route.decided"
            and event.metadata.get("reason") == "quality_gate_failed"
            for event in state.events
        )
    else:
        repeated_sources, repeated_claims = _snapshot_repeats(snapshots)
        critic_target_ids = {
            target for item in snapshots for target in item.critic_targets
        }
        source_coverage = []
        claim_coverage = []
        repeated_passes = len(snapshots)
        forced_refinements = sum(item.force_refinement for item in snapshots)
    critic_targets = len(critic_target_ids)
    final_covered = {
        coverage_id
        for claim in claims_by_id.values()
        for coverage_id in claim.consumed_coverage_ids
    }
    closed_targets = len(critic_target_ids.intersection(final_covered))
    if source_coverage or claim_coverage:
        coverage_passes = claim_coverage or source_coverage
        prior_coverage = (
            set().union(*coverage_passes[:1]) if coverage_passes else set()
        )
        later_coverage = (
            set().union(*coverage_passes[1:])
            if len(coverage_passes) > 1
            else set()
        )
        new_evidence = len(later_coverage.difference(prior_coverage))
    else:
        new_evidence = len(
            {topic for item in snapshots[1:] for topic in item.new_evidence_topics}
        )

    publication_events, report_writes, evidence_writes, memory_writes, timing = (
        _terminal_counts(state)
    )
    publication_operations_match = _publication_operations_match(
        dependencies if publication_events else None,
        memory_writes,
    )
    report = state.report or render_reader_report(composition)
    ledger = state.report_evidence or render_evidence_ledger(composition)
    report_words = len(report.split())
    ledger_words = len(ledger.split())
    rendered_citations = _rendered_citation_resolution(report, composition)

    raw_metrics: dict[str, Any] = {
        "coverage_ratio": coverage_ratio,
        "cited_sources": len(cited_urls),
        "scored_cited_sources": scored_cited,
        "duplicate_claims": duplicate_claims,
        "duplicate_source_rows": duplicate_source_rows,
        "uncited_settled_points": uncited,
    }
    expected_summary = _expected_cli_summary(state, raw_metrics)
    if cli_output is not None:
        parsed_summary = production_cli_summary(cli_output)
        cli_matches = all(
            parsed_summary.get(key) == value for key, value in expected_summary.items()
        )
    elif cli_summary is not None:
        cli_matches = all(
            cli_summary.get(key) == value for key, value in expected_summary.items()
        )
    else:
        # Fails closed, like every other leg. The runner always supplies
        # ``cli_output``, so a caller that supplies neither has not shown that
        # the CLI agrees with state and must not be handed a passing gate.
        cli_matches = False

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
    if duplicate_finding_rows:
        integrity.append("duplicate_finding_rows")
    if contradicted != disclosed:
        integrity.append("contradiction_disclosure")
    if planned_topics and coverage_ratio < 0.80:
        integrity.append("coverage_below_0.80")
    if graph_observed and not topic_ids.issubset(observed_attempts):
        integrity.append("planned_topic_attempts")
    if case.expected_refinement_topics and not set(
        case.expected_refinement_topics
    ).issubset(final_covered):
        integrity.append("refinement_targets_unresolved")
    if case.expected_refinement_topics:
        if source_coverage or claim_coverage:
            prior = set().union(*(claim_coverage[:1] or source_coverage[:1]))
            later = set().union(*(claim_coverage[1:] or source_coverage[1:]))
            added = later.difference(prior)
        else:
            added = {
                topic
                for item in snapshots[1:]
                for topic in item.new_evidence_topics
            }
        if not set(case.expected_refinement_topics).intersection(added):
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
    if publication_events and not publication_operations_match:
        integrity.append("publication_operation_order")
    if not cli_matches:
        integrity.append("cli_summary_mismatch")
    if not rendered_citations:
        integrity.append("rendered_citation_resolution")
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
        duplicate_finding_rows=duplicate_finding_rows,
        contradicted_claims=contradicted,
        disclosed_contradictions=disclosed,
        uncited_settled_points=uncited,
        reader_report_words=report_words,
        evidence_ledger_words=ledger_words,
        reader_report_chars=len(report),
        evidence_ledger_chars=len(ledger),
        refinement_passes=repeated_passes,
        critic_targets=critic_targets,
        closed_critic_targets=closed_targets,
        new_evidence_in_refinement=new_evidence,
        publication_events=publication_events,
        report_writes=report_writes,
        evidence_writes=evidence_writes,
        memory_writes=memory_writes,
        cli_summary_matches=cli_matches,
        rendered_citation_resolution=rendered_citations,
        graph_observed=graph_observed,
        integrity_failures=integrity,
        hard_failures=hard,
        repeated_source_snapshot_passes=repeated_sources,
        repeated_claim_snapshot_passes=repeated_claims,
        gate_forced_refinement_passes=forced_refinements,
        targetless_gate_forced_refinements=(
            forced_refinements if not critic_targets else 0
        ),
    )


def evidence_ledger_summary(
    composition: Any,
    *,
    duplicate_source_rows: int = 0,
    duplicate_claims: int = 0,
) -> EvidenceLedgerSummary:
    """Build the bounded summary permitted in the judge contract.

    The two duplicate counts are passed in from the deterministic evaluation
    rather than hardcoded to zero: this is the only ledger surface the judge
    sees, and a repetition whose ``integrity_failures`` named
    ``duplicate_claims`` was still handing the judge a clean duplicate count.
    """
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
        duplicate_source_rows=duplicate_source_rows,
        duplicate_claims=duplicate_claims,
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
        # How much of the scoped plan the reader report actually presents as
        # ranked points. ``case.sub_topics`` cardinality is a property of the
        # fixture; this is a property of the report.
        "ranked_reader_points": len(composition.summary),
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
        "duplicate_finding_rows": metrics.duplicate_finding_rows,
        "disclosed_contradictions": metrics.disclosed_contradictions,
        "uncited_settled_points": metrics.uncited_settled_points,
        "reader_report_words": metrics.reader_report_words,
        "evidence_ledger_words": metrics.evidence_ledger_words,
        "cli_summary_matches": metrics.cli_summary_matches,
        "rendered_citation_resolution": metrics.rendered_citation_resolution,
        "integrity_passed": metrics.integrity_passed,
    }
    return WholeReportJudgeInput(
        question=case.question,
        scoped_plan=list(case.sub_topics),
        reader_report=report[:16_000],
        deterministic_metrics=metrics_payload,
        evidence_ledger_summary=evidence_ledger_summary(
            composition,
            duplicate_source_rows=metrics.duplicate_source_rows,
            duplicate_claims=metrics.duplicate_claims,
        ),
    )


def judge_whole_report(
    payload: WholeReportJudgeInput,
    *,
    rubric: WholeReportRubric | None = None,
) -> WholeReportJudgeScore:
    """Score seven reader-facing dimensions from the bounded contract only.

    Every dimension is scored on its own 0..1 satisfaction scale, and no term
    is included that cannot vary with the input:

    * ``completeness`` is the evaluator's covered-topic ratio. The six
      ``REPORT_SECTIONS`` headings are printed by ``render_reader_report``
      unconditionally, so a "heading present" term scores 1.0 on every
      rendered report and could only inflate the mean.
    * ``prioritization`` is the share of the scoped plan the report presents
      as ranked points, not the plan's cardinality: a three-topic plan is not
      evidence that the report ranked anything.
    * ``uncertainty`` is the share of contradicted claims the report actually
      discloses, and 1.0 when there is nothing contradictory to disclose.
      Scoring ``contradicted / claims`` rewarded having a contradiction and
      scored an honest clean report at zero.
    * ``readability`` is the length band alone, and ``actionability`` the
      presence of decision language. Neither averages in an always-true term.
    * ``evidence_quality`` and ``attribution`` are **structural bounds**: they
      average ratios a hard integrity gate already requires to be 1.0
      (``source_read_provenance_ratio``, ``scored_cited_source_ratio``,
      ``checked_claim_provenance_ratio``, ``citation_linkage_ratio``,
      ``rendered_citation_resolution``). They stay in the score because the
      plan's Step 4 fixes the rubric at seven dimensions, and they are read
      here as what they are — a restatement of the gates, not independent
      reader-quality evidence. The positive control that the remaining terms
      can still fail a report the integrity gates pass is
      ``test_the_judge_can_score_an_integrity_clean_report_below_its_floor``.
    """
    rubric_value = rubric or WholeReportRubric()
    metrics = payload.deterministic_metrics
    ledger = payload.evidence_ledger_summary
    report = payload.reader_report.casefold()
    coverage = float(metrics.get("coverage_ratio", 0.0))
    read_ratio = float(metrics.get("source_read_provenance_ratio", 0.0))
    scored_ratio = float(metrics.get("scored_cited_source_ratio", 0.0))
    claim_ratio = float(metrics.get("checked_claim_provenance_ratio", 0.0))
    citation_ratio = float(metrics.get("citation_linkage_ratio", 0.0))
    citation_rendered = float(
        bool(metrics.get("rendered_citation_resolution", False))
    )
    words = len(payload.reader_report.split())
    planned = len(payload.scoped_plan)
    ranked = float(metrics.get("ranked_reader_points", 0))
    ranked_ratio = min(1.0, ranked / planned) if planned else 0.0
    contradicted = ledger.contradicted_claim_count
    disclosed = float(metrics.get("disclosed_contradictions", 0))
    disclosure_ratio = (
        min(1.0, disclosed / contradicted) if contradicted else 1.0
    )
    dimensions_by_name = {
        "completeness": coverage,
        "prioritization": ranked_ratio,
        "evidence_quality": fsum((read_ratio, scored_ratio, claim_ratio)) / 3,
        "attribution": (citation_ratio + citation_rendered) / 2,
        "uncertainty": disclosure_ratio,
        "readability": (
            1.0 if 40 <= words <= 8_000 else 0.35 if words else 0.0
        ),
        "actionability": (
            1.0
            if any(
                word in report
                for word in ("decision", "recommend", "should", "implication")
            )
            else 0.0
        ),
    }
    dimensions = {
        dimension: max(0.0, min(1.0, dimensions_by_name.get(dimension, 0.0)))
        for dimension in rubric_value.dimensions
    }
    score = max(0.0, min(1.0, fsum(dimensions.values()) / len(dimensions)))
    rationale = (
        "Structural-only seven-dimension score from bounded reader evidence. "
        "This is not an independent report judge: completeness, prioritization "
        "and uncertainty are ratios, readability is a length band, "
        "actionability is decision language, and evidence_quality and "
        "attribution restate hard integrity gates. The semantic review is "
        "semantic_review_summary."
    )
    return WholeReportJudgeScore(
        score=score,
        dimensions=dimensions,
        rationale=rationale,
        rubric=rubric_value,
        structural_only=True,
    )


def semantic_review_summary(
    review: ReportReview | None,
) -> SemanticReviewSummary:
    """Read one terminal semantic review into the harness's own record.

    Every input the review carries is read: its status, its mean, all seven
    dimensions, every defect with its materiality, the statements and evidence
    it covered, the batches it answered for, and the fingerprint it judged.
    ``accepted`` is the review's own rule, not the mean — a metric that scored
    a report with an unresolved critical defect as accepted would be reporting
    success while skipping the input that says otherwise, which is the failure
    mode this evaluator exists to avoid.

    ``None`` yields a summary with no status and no score, and
    ``SemanticReviewSummary.missing`` is then true: no judgement is recorded as
    no judgement, never as a clean review.
    """
    if review is None:
        return SemanticReviewSummary()
    return SemanticReviewSummary(
        rubric_version=review.rubric_version,
        status=review.status,
        score=review.mean_score,
        accepted=semantic_review_passes(review),
        dimensions=dict(review.dimensions),
        defect_count=len(review.defects),
        material_defect_count=len(review.material_defects),
        derived_defect_count=len(review.derived_defect_statement_ids),
        reviewed_statement_ids=list(review.reviewed_statement_ids),
        unreviewed_statement_ids=list(review.unreviewed_statement_ids),
        reviewed_evidence_ids=list(review.reviewed_evidence_ids),
        omitted_evidence_ids=list(review.omitted_evidence_ids),
        expected_batch_ids=list(review.expected_batch_ids),
        reviewed_batch_ids=list(review.reviewed_batch_ids),
        input_fingerprint=review.input_fingerprint,
        coverage_complete=review.coverage_complete,
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
    "production_cli_summary",
    "semantic_review_summary",
    "build_whole_report_judge_input",
    "repetition_accepted",
]
