"""Offline research-runner doubles for API session tests.

No provider, no graph, no tracker: these doubles implement the
``ResearchRunner`` call shape (``session_id``, ``config_overrides``,
``event_handler``) and return real ``ResearchOutcome`` objects built from
real ``ResearchState`` and ``TokenUsage`` records, so the session store is
tested against the same contracts the HTTP layer will use.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from deep_research.observability import TokenUsage
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import ResearchError, ResearchEvent, ResearchState


def make_outcome(
    *,
    session_id: str = "session-1",
    question: str = "Question",
    status: str = "completed",
    iteration: int = 0,
    report_path: str | None = None,
    trace_url: str | None = None,
    report: str | None = None,
    errors: Sequence[ResearchError] = (),
    events: Sequence[ResearchEvent] = (),
) -> ResearchOutcome:
    """Build one real outcome carrying the given session facts."""
    state = ResearchState(
        session_id=session_id,
        original_question=question,
        iteration=iteration,
        report=report,
        errors=list(errors),
        events=list(events),
    )
    return ResearchOutcome(
        session_id=session_id,
        question=question,
        status=status,
        state=state,
        trace_url=trace_url,
        report_path=report_path,
        token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        tool_calls=(),
    )


class ScriptedRunner:
    """Publish scripted events, then return a scripted outcome.

    ``error`` makes every call raise instead of returning, which is how
    configuration and unexpected failures are simulated. ``calls`` records
    every invocation so tests can assert what the store forwarded.
    """

    def __init__(
        self,
        *,
        events: Sequence[ResearchEvent] = (),
        status: str = "completed",
        iteration: int = 0,
        report_path: str | None = None,
        trace_url: str | None = None,
        report: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.status = status
        self.iteration = iteration
        self.report_path = report_path
        self.trace_url = trace_url
        self.report = report
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        question: str,
        session_id: str,
        config_overrides: Mapping[str, Any] | None = None,
        event_handler: Callable[[ResearchEvent], None] | None = None,
        **kwargs: Any,
    ) -> ResearchOutcome:
        self.calls.append(
            {
                "question": question,
                "session_id": session_id,
                "config_overrides": dict(config_overrides or {}),
                **kwargs,
            }
        )
        if event_handler is not None:
            for event in self.events:
                event_handler(event)
        if self.error is not None:
            raise self.error
        return make_outcome(
            session_id=session_id,
            question=question,
            status=self.status,
            iteration=self.iteration,
            report_path=self.report_path,
            trace_url=self.trace_url,
            report=self.report,
            events=self.events,
        )


class GateRunner:
    """Hold a run open until the test releases it.

    Publishes one ``graph.node.started`` event before ``started`` is set, so
    a test can observe the session's live progress fields, then blocks until
    ``release`` is set and returns a completed outcome.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        question: str,
        session_id: str,
        config_overrides: Mapping[str, Any] | None = None,
        event_handler: Callable[[ResearchEvent], None] | None = None,
        **kwargs: Any,
    ) -> ResearchOutcome:
        self.calls.append(
            {
                "question": question,
                "session_id": session_id,
                "config_overrides": dict(config_overrides or {}),
                **kwargs,
            }
        )
        event = ResearchEvent(
            event_type="graph.node.started",
            source="graph.planner",
            message="Node planner started.",
            metadata={"node": "planner", "iteration": 1},
        )
        if event_handler is not None:
            event_handler(event)
        self.started.set()
        await self.release.wait()
        return make_outcome(
            session_id=session_id,
            question=question,
            status="completed",
            iteration=1,
            report_path="report-session-1.md",
            events=[event],
        )
