"""Stopping a session (notes-progress-report spec §8, D17): the step it was on, and its event.

``POST /research/{id}/stop`` records the step the reader stopped the run at, and the
console names the same step: ``active_row`` reads a session's published events with the
console's own rule for its active row (``run.active`` in ``web/lib/run-state.ts``;
DESIGN.md §3.5), so the row the page shows as stopped is the row the API recorded (AC32,
pinned for both by ``web/test/fixtures/active-rows.json``). ``session_stopped_event`` is
the last event a stopped session publishes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from deep_research.utils.types import ResearchEvent

PIPELINE_ROWS: tuple[str, ...] = (
    "planner",
    "researcher",
    "source_evaluator",
    "evidence_verifier",
    "report_writer",
    "report_reviewer",
    "finalize_report",
)
"""The console's seven rows, in order (``STAGES``, ``web/lib/run-state.ts``)."""

CHECK_STEP = "check"
"""The step of a session stopped while the one-time check waits for the reader (spec §4 item 3)."""

_HOPS = frozenset({"extra_pass", "note_pass", "writer_redraft"})
_DESTINATION_ROWS = {
    "extra_pass": "researcher",
    "note_pass": "researcher",
    "redraft": "report_writer",
    "finalize": "finalize_report",
}


def _next_row(node: object) -> str | None:
    """The row after ``node``'s, or ``None`` after Publishing and for anything that is no row."""
    if not isinstance(node, str) or node not in PIPELINE_ROWS:
        return None
    index = PIPELINE_ROWS.index(node) + 1
    return PIPELINE_ROWS[index] if index < len(PIPELINE_ROWS) else None


def active_row(events: Iterable[ResearchEvent]) -> str | None:
    """The console's active row after ``events``: the row a stop records.

    Planning until the planner completes. A ``graph.node.completed`` moves to the next
    row — the reviewer's own completion and the hops' move nothing, and Publishing's
    leaves none; ``graph.route.decided`` moves to its destination's row (``end`` leaves
    none); a skipped active row and the session's completion leave none. Exactly
    ``run.active`` in ``web/lib/run-state.ts``.
    """
    row: str | None = PIPELINE_ROWS[0]
    for event in events:
        kind, metadata = event.event_type, event.metadata
        if kind == "graph.node.completed":
            node = metadata.get("node")
            if node in _HOPS or node == "report_reviewer":
                continue
            row = _next_row(node)
        elif kind == "graph.route.decided":
            row = _DESTINATION_ROWS.get(str(metadata.get("destination")))
        elif kind == "graph.node.skipped":
            if metadata.get("node") == row:
                row = None
        elif kind == "graph.session.completed":
            row = None
    return row


def session_stopped_event(
    step: str, stopped_at: datetime, elapsed_seconds: int
) -> ResearchEvent:
    """``session.stopped``: the step the reader stopped the run at, when, and how long it ran."""
    return ResearchEvent(
        event_type="session.stopped",
        source="api",
        message="The reader stopped the research.",
        metadata={
            "step": step,
            "stopped_at": stopped_at.isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed_seconds,
        },
    )


__all__ = ["CHECK_STEP", "PIPELINE_ROWS", "active_row", "session_stopped_event"]
