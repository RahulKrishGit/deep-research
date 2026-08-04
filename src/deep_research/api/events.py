"""Safe typed events the API records for requests and failures.

Everything a client-visible failure carries is an identifier, a route
template, an HTTP method, a status code, or an enumerated code and reason —
never a rejected input value, an exception message, provider text, or a
secret.
"""

from __future__ import annotations

from deep_research.utils.types import ResearchEvent


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
