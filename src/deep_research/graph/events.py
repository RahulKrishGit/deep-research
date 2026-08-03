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
    GRAPH_ROUTES,
    GRAPH_SOURCE,
    GRAPH_STATUSES,
)
from deep_research.utils.types import ResearchEvent


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
    source = GRAPH_SOURCE if node is None else f"{GRAPH_SOURCE}.{node.strip()}"
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
