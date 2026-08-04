"""Safe typed events the API records for requests and failures.

Everything a client-visible failure carries is an identifier, a route
template, an HTTP method, a status code, or an enumerated code and reason —
never a rejected input value, an exception message, provider text, or a
secret.
"""

from __future__ import annotations

from deep_research.utils.types import ResearchEvent


def encode_sse(event: ResearchEvent, *, event_id: int) -> str:
    """Render one typed progress record as one server-sent-event frame.

    The frame carries the record's id, its enumerated event type, and the
    full ``ResearchEvent`` JSON — nothing else. Event ids start at one per
    stream so a late subscriber can see where the replay began.
    """
    if event_id < 1:
        raise ValueError("event_id must be at least 1")
    return (
        f"id: {event_id}\n"
        f"event: {event.event_type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def api_error_event(
    *,
    session_id: str | None = None,
    route: str | None = None,
    method: str,
    status_code: int,
    code: str,
    reason: str | None = None,
) -> ResearchEvent:
    """Build one ``api.request.error`` event with only safe metadata.

    ``session_id`` and ``route`` are omitted when the request never produced
    them (validation can fail before any session exists); ``reason`` is an
    enumerated value or is omitted entirely.
    """
    metadata: dict[str, object] = {
        "method": method,
        "status_code": status_code,
        "code": code,
    }
    if session_id is not None:
        metadata["session_id"] = session_id
    if route is not None:
        metadata["route"] = route
    if reason is not None:
        metadata["reason"] = reason
    return ResearchEvent(
        event_type="api.request.error",
        source="api",
        message="API request failed.",
        metadata=metadata,
    )
