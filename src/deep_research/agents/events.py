"""Structured progress events attributed to a named agent.

The mirror of ``agents.errors.agent_error``: these records land in
``ResearchState.events`` and are read by the CLI, the API stream, and the UI.
``Tracker`` separately emits its own span lifecycle events; the two are
independent and both end up in state.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.utils.types import ResearchEvent


def agent_event(
    *,
    agent_name: str,
    event_type: str,
    message: str,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ResearchEvent:
    """Build one progress event attributed to a named agent.

    For a state-bound event -- one returned in an agent's state update, and so
    copied into ``ResearchState.events`` -- ``metadata`` must never contain
    ``str(exception)`` or raw provider text, which can carry keys, URLs, and
    paths: record counts, identifiers, and enumerated reasons instead. A
    live-only progress event (``*.progress``, notes-progress-report spec §4
    item 1) is never copied there, and may also carry exactly these
    reader-facing texts: plan and section titles (at most 160 characters), a
    finding's content (160) and a drafted sentence (200), hosts, and page-word
    correction values (60). Never an exception message, a URL path or query,
    or a model's reason text.
    """
    if not agent_name.strip():
        raise ValueError("agent_name must not be blank")
    if not event_type.strip():
        raise ValueError("event_type must not be blank")
    return ResearchEvent(
        event_type=event_type.strip(),
        source=f"agent.{agent_name.strip()}",
        message=message,
        metadata=dict(metadata or {}),
    )


def publish_live(event: ResearchEvent) -> None:
    """Publish one agent event live, through the run's sink, if one is bound.

    live-briefs spec E3: the same object must also be returned in the agent's
    ``state_update["events"]`` -- the orchestrator recognises it there by its
    ``event_id`` and does not publish it twice. The four live-only progress types
    (``planner.progress``, ``source_evaluator.progress``,
    ``evidence_verifier.progress`` and ``report_writer.progress``;
    notes-progress-report spec §4 item 1) are the exception: they are published
    here and never returned, so they are never in the run's state.
    ``graph.live`` is imported at call time: ``deep_research.graph`` imports
    every agent while its package initialises, so a module-level import here
    would close an import cycle whenever ``deep_research.agents`` is imported
    first.
    """
    from deep_research.graph import live  # noqa: PLC0415

    live.publish_live(event)
