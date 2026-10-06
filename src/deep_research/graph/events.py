"""Structured progress events emitted by the graph itself.

The graph mirror of ``agents.events``. These records land in
``ResearchState.events`` alongside the agents' own events and the
``Tracker``'s span lifecycle events, and are what makes a route decision
observable without reading a LangSmith trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from deep_research.graph.state import (
    FINALIZE_NODE,
    GRAPH_ROUTES,
    GRAPH_SOURCE,
    GRAPH_STATUSES,
    REPORT_REVIEWER_NODE,
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

    For a state-bound event -- every graph event is returned in its node's
    update and copied into ``ResearchState.events`` -- ``metadata`` must never
    contain ``str(exception)`` or raw provider text. Record counts,
    identifiers, and enumerated reasons instead; only a live-only progress
    event may carry such texts.
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
    max_extra_passes: int,
    missing_required_target_ids: Sequence[str] = (),
) -> ResearchEvent:
    """Record where the graph went after the Report Reviewer, and why.

    ``reason`` is a ``GRAPH_ROUTES`` key, never provider text, so a consumer
    can group runs by *why* they continued or stopped rather than parsing a
    rationale. ``missing_required_target_ids`` is recorded next to it — the
    targets code measured, never ones a model proposed — so a reader can see
    which obligations bought a pass, or which ones the ceiling could no
    longer buy for.
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
            "max_extra_passes": max_extra_passes,
            "missing_required_target_ids": list(missing_required_target_ids),
        },
    )


def extra_pass_started_event(
    *,
    iteration: int,
    max_extra_passes: int,
    targets: Sequence[str],
) -> ResearchEvent:
    """Announce the one extra pass a loop-back just opened, and its job list.

    The targets are what the pass exists for: the required obligations the
    last pass measured as still missing a verified finding. They are ids the
    plan minted, never provider text.
    """
    return graph_event(
        event_type="graph.extra_pass.started",
        message=f"Extra pass {iteration} started.",
        metadata={
            "iteration": iteration,
            "max_extra_passes": max_extra_passes,
            "targets": list(targets),
        },
    )


def redraft_requested_event(
    *,
    iteration: int,
    redrafts: int,
    material_defects: int,
) -> ResearchEvent:
    """Announce the writer re-run a review's material defects just bought.

    Counts only: how many re-runs the run has now bought, and how many material
    defects the review that bought this one named. The defects themselves are
    provider text and stay in the review record, which is what the writer is
    handed; this event is what makes the re-run visible in the run's own
    history without copying a reviewer's prose into it.
    """
    return graph_event(
        event_type="graph.report.redraft_requested",
        message=(
            f"Writer re-run {redrafts} requested for one materially defective "
            "report."
        ),
        metadata={
            "iteration": iteration,
            "redrafts": redrafts,
            "material_defects": material_defects,
        },
    )


def note_pass_started_event(
    *,
    iteration: int,
    note_passes: int,
    note_ids: Sequence[str],
    targets: Sequence[str],
) -> ResearchEvent:
    """Announce the targeted pass the reader's notes just bought.

    Ids only: the notes it researches and the targets their own sub-topics
    carry. ``iteration`` is unchanged by a note pass, and ``note_passes`` is
    the run's count including this one.
    """
    return graph_event(
        event_type="graph.note_pass.started",
        message=f"Note pass {note_passes} started.",
        metadata={
            "iteration": iteration,
            "note_passes": note_passes,
            "note_ids": list(note_ids),
            "targets": list(targets),
        },
    )


def note_redraft_requested_event(
    *,
    iteration: int,
    note_ids: Sequence[str],
) -> ResearchEvent:
    """Announce the writer re-run the reader's notes just bought.

    Its own event, not ``graph.report.redraft_requested``: a note redraft
    spends none of the review's own writer re-runs, and the writer drafts
    afresh with the notes rather than patching the parts a defect named.
    """
    return graph_event(
        event_type="graph.note_redraft.requested",
        message="Writer re-run requested for the reader's notes.",
        metadata={"iteration": iteration, "note_ids": list(note_ids)},
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
            "required_target_ids": list(quality.required_target_ids),
            "answered_target_ids": list(quality.answered_target_ids),
            "missing_required_target_ids": list(
                quality.missing_required_target_ids
            ),
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
    fingerprint: str,
    reused: bool,
    criteria: Sequence[Mapping[str, JsonValue]] = (),
    notes: Sequence[Mapping[str, JsonValue]] = (),
) -> ResearchEvent:
    """Record the terminal semantic review's outcome for one pass.

    Counts, an enumerated status, and the packet fingerprint only — never the
    review's prose and never a defect's text, which are provider output. The
    status is one of ``scored``/``incomplete``/``provider_failed``: the first
    means a judgement exists, the other two are the honest record that none
    does. ``criteria`` and ``notes`` are what ``graph/review_brief.py`` reads
    for Reviewing's brief: the five criteria with ``met`` and the defect kinds
    behind a miss, and each note's enumerated result -- ids and enumerated
    values, never text.
    """
    return graph_event(
        event_type="graph.report.reviewed",
        message=(
            "The report was reviewed and scored."
            if review_status == "scored"
            else "The report review did not produce a score."
        ),
        node=REPORT_REVIEWER_NODE,
        metadata={
            "iteration": iteration,
            "review_status": review_status,
            "mean_score": mean_score,
            "material_defects": material_defects,
            "reviewed_statements": reviewed_statements,
            "input_fingerprint": fingerprint,
            "reused": reused,
            "criteria": [dict(criterion) for criterion in criteria],
            "notes": [dict(note) for note in notes],
        },
    )


def report_published_event(
    *,
    quality_status: str,
    report_path: str | None,
    evidence_path: str | None,
    quality_path: str | None = None,
    document_writes: int,
    memory_writes: int,
    error_count: int,
) -> ResearchEvent:
    """Record the one terminal publication of the composed artifact set.

    This is the only event that names where the session's final artifacts live,
    and it carries all three paths. A path is ``None`` when that write failed
    *or* when any other required write failed — the set is published whole or
    advertised not at all, so a front-end reading this event is never pointed
    at an earlier pass's file, and never at two thirds of a set.
    ``quality_status`` is an enumerated ``QUALITY_STATUS_*`` value; the counts
    are write outcomes, never content.
    """
    return graph_event(
        event_type="graph.report.published",
        message="The final report and its evidence ledger were published.",
        node=FINALIZE_NODE,
        metadata={
            "quality_status": quality_status,
            "report_path": report_path,
            "evidence_path": evidence_path,
            "quality_path": quality_path,
            "document_writes": document_writes,
            "memory_writes": memory_writes,
            "error_count": error_count,
        },
    )


def session_started_event(
    *,
    session_id: str,
    max_extra_passes: int,
    checkpointing: bool,
) -> ResearchEvent:
    """Announce the research session, before the first node runs."""
    return graph_event(
        event_type="graph.session.started",
        message="Research session started.",
        metadata={
            "session_id": session_id,
            "max_extra_passes": max_extra_passes,
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
