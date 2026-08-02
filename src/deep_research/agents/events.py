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

    ``metadata`` must never contain ``str(exception)`` or raw provider text:
    these records are copied into ``ResearchState.events`` and provider text
    can carry keys, URLs, and paths. Record counts, identifiers, and
    enumerated reasons instead.
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
