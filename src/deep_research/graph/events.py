"""Structured progress events emitted by the graph itself.

The graph mirror of ``agents.events``. These records land in
``ResearchState.events`` alongside the agents' own events and the
``Tracker``'s span lifecycle events, and are what makes a route decision
observable without reading a LangSmith trace.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.graph.state import (
    FINALIZE_NODE,
    GRAPH_ROUTES,
    GRAPH_SOURCE,
    GRAPH_STATUSES,
    REPORT_REVIEW_NODE,
)
from deep_research.utils.types import ReportQualitySnapshot, ResearchEvent


def graph_event(
    *,
    event_type: str,
    message: str,
    node: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ResearchEvent:
    """Build one progress event attributed to the graph or one of its nodes.

    ``metadata`` must never contain ``str(exception)`` or raw provider text:
    these records are copied into ``ResearchState.events``. Record counts,
    identifiers, and enumerated reasons instead.
    """
    if not event_type.strip():
        raise ValueError("event_type must not be blank")
    node = node.strip() if node else None
    source = GRAPH_SOURCE if not node else f"{GRAPH_SOURCE}.{node}"
    return ResearchEvent(
        event_type=event_type.strip(),
        source=source,
        message=message,
        metadata=dict(metadata or {}),
    )


def node_started_event(node: str, *, iteration: int) -> ResearchEvent:
    """Announce that a node began, before its agent is invoked."""
    return graph_event(
        event_type="graph.node.started",
        message=f"Node {node} started.",
        node=node,
        metadata={"node": node, "iteration": iteration},
    )


def node_completed_event(
    node: str,
    *,
    iteration: int,
    event_count: int,
    error_count: int,
) -> ResearchEvent:
    """Report what one node's agent contributed to state."""
    return graph_event(
        event_type="graph.node.completed",
        message=f"Node {node} completed.",
        node=node,
        metadata={
            "node": node,
            "iteration": iteration,
            "event_count": event_count,
            "error_count": error_count,
        },
    )


def node_skipped_event(node: str, *, iteration: int) -> ResearchEvent:
    """Report that a node ran no agent because the run had already halted."""
    return graph_event(
        event_type="graph.node.skipped",
        message=f"Node {node} was skipped because the run had halted.",
        node=node,
        metadata={"node": node, "iteration": iteration, "reason": "halted"},
    )


def route_decided_event(
    *,
    destination: str,
    reason: str,
    iteration: int,
    max_iterations: int,
    should_continue: bool,
) -> ResearchEvent:
    """Record where the graph went after the Critic, and why.

    ``reason`` is a ``GRAPH_ROUTES`` key, never provider text, so a consumer
    can group runs by *why* they continued or stopped rather than parsing a
    rationale. ``should_continue`` is recorded next to it so a reader can
    see when the iteration bound overrode the critic.
    """
    explanation = GRAPH_ROUTES.get(reason)
    if explanation is None:
        raise ValueError(f"unknown route reason: {reason}")
    return graph_event(
        event_type="graph.route.decided",
        message=explanation,
        metadata={
            "destination": destination,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "should_continue": should_continue,
        },
    )


def refinement_started_event(
    *,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Announce the macro iteration a loop-back just opened."""
    return graph_event(
        event_type="graph.refinement.started",
        message=f"Refinement pass {iteration} started.",
        metadata={"iteration": iteration, "max_iterations": max_iterations},
    )


def quality_assessed_event(
    *,
    iteration: int,
    quality: ReportQualitySnapshot,
) -> ResearchEvent:
    """Record the deterministic quality verdict for one composed report.

    Counts and enumerated hard-failure names only — never report prose. The
    names come from ``agents.quality``'s closed set, which is what makes this
    record groupable without reading a report.
    """
    failures = list(quality.hard_failures)
    return graph_event(
        event_type="graph.quality.assessed",
        message=(
            "The report quality gates found no hard failure."
            if not failures
            else "The report quality gates found a hard failure."
        ),
        metadata={
            "iteration": iteration,
            "hard_failures": failures,
            "coverage_ratio": quality.coverage_ratio,
            "duplicate_claims": quality.duplicate_claims,
            "duplicate_source_rows": quality.duplicate_source_rows,
            "uncited_settled_points": quality.uncited_settled_points,
        },
    )


def report_review_completed_event(
    *,
    iteration: int,
    review_status: str,
    mean_score: float | None,
    material_defects: int,
    reviewed_statements: int,
    omitted_evidence: int,
    fingerprint: str,
    reused: bool,
) -> ResearchEvent:
    """Record the terminal semantic review's outcome for one pass.

    Counts, an enumerated status, and the packet fingerprint only — never the
    review's prose and never a defect's text, which are provider output. The
    status is one of ``scored``/``incomplete``/``provider_failed``, kept apart
    from the Critic's own ``review_status`` so a reader of the event stream can
    tell which reviewer the record is about.
    """
    return graph_event(
        event_type="graph.report.reviewed",
        message=(
            "The report was reviewed and scored."
            if review_status == "scored"
            else "The report review did not produce a score."
        ),
        node=REPORT_REVIEW_NODE,
        metadata={
            "iteration": iteration,
            "review_status": review_status,
            "mean_score": mean_score,
            "material_defects": material_defects,
            "reviewed_statements": reviewed_statements,
            "omitted_evidence": omitted_evidence,
            "input_fingerprint": fingerprint,
            "reused": reused,
        },
    )


def report_published_event(
    *,
    quality_status: str,
    report_path: str | None,
    evidence_path: str | None,
    document_writes: int,
    memory_writes: int,
    error_count: int,
) -> ResearchEvent:
    """Record the one terminal publication of both composed artifacts.

    This is the only event that names where the session's final report lives,
    and it carries *both* paths. A path is ``None`` when that write failed, so
    a front-end reading this event is never pointed at an earlier refinement
    pass's file. ``quality_status`` is an enumerated ``QUALITY_STATUS_*``
    value; the counts are write outcomes, never content.
    """
    return graph_event(
        event_type="graph.report.published",
        message="The final report and its evidence ledger were published.",
        node=FINALIZE_NODE,
        metadata={
            "quality_status": quality_status,
            "report_path": report_path,
            "evidence_path": evidence_path,
            "document_writes": document_writes,
            "memory_writes": memory_writes,
            "error_count": error_count,
        },
    )


def session_started_event(
    *,
    session_id: str,
    max_iterations: int,
    checkpointing: bool,
) -> ResearchEvent:
    """Announce the research session, before the first node runs."""
    return graph_event(
        event_type="graph.session.started",
        message="Research session started.",
        metadata={
            "session_id": session_id,
            "max_iterations": max_iterations,
            "checkpointing": checkpointing,
        },
    )


def session_completed_event(
    *,
    status: str,
    iteration: int,
    error_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Report the final status of one research session."""
    if status not in GRAPH_STATUSES:
        raise ValueError(f"unknown graph status: {status}")
    return graph_event(
        event_type="graph.session.completed",
        message=f"Research session finished with status {status}.",
        metadata={
            "status": status,
            "iteration": iteration,
            "error_count": error_count,
            "has_report": has_report,
        },
    )
