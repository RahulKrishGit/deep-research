"""Process-local research sessions: background tasks and safe snapshots.

The HTTP routes stay thin by owning nothing: ``SessionStore`` starts one
background task per session, records every ``ResearchEvent`` the runner
publishes into an append-only per-session list, and exposes replayable
iteration and safe terminal state. Nothing here touches the network, the
file system, or a provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeAlias

from pydantic import JsonValue

from deep_research.api.models import (
    CoverageProgressResponse,
    EvidenceCountsResponse,
    SessionStatus,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import ResearchError, ResearchEvent

TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed"}
)

ResearchRunner: TypeAlias = Callable[..., Awaitable[ResearchOutcome]]


@dataclass(slots=True)
class ResearchSession:
    """One running or finished session and everything it has published.

    ``events`` and ``errors`` are append-only snapshots: every event is
    stored as a deep copy so a caller mutating a published record can never
    rewrite session history, and responses read from these lists without
    ever seeing a live runner object.
    """

    session_id: str
    query: str
    status: SessionStatus
    started_at: datetime
    current_agent: str | None = None
    iteration: int = 0
    finished_at: datetime | None = None
    report_path: str | None = None
    trace_url: str | None = None
    errors: list[ResearchError] = field(default_factory=list)
    events: list[ResearchEvent] = field(default_factory=list)
    outcome: ResearchOutcome | None = None
    task: asyncio.Task[None] | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def publish(self, event: ResearchEvent) -> None:
        """Record one progress event and update the live status fields."""
        self.events.append(event.model_copy(deep=True))
        node = event.metadata.get("node")
        iteration = event.metadata.get("iteration")
        if event.event_type == "graph.node.started" and isinstance(node, str):
            self.current_agent = node
        if isinstance(iteration, int):
            self.iteration = iteration
        self.changed.set()


def outcome_response_fields(
    outcome: ResearchOutcome | None,
) -> dict[str, object]:
    """The additive API fields one finished outcome contributes, or ``{}``.

    Every value is the outcome's own typed property, which is built from the
    same records the summary, the reader report and the quality JSON render
    from — so the API cannot disagree with the CLI about one run, and nothing
    here re-derives a number from a report body.

    A session with no outcome contributes nothing at all: a running session
    has no artifact paths, no contract version, no review, no coverage and no
    span, and defaulting any of them would answer a question the run has not
    reached. An empty review status is ``None`` for the same reason — "no
    review was recorded" is not a status.
    """
    if outcome is None:
        return {}
    fields: dict[str, object] = {
        "evidence_path": outcome.evidence_path,
        "quality_path": outcome.quality_path,
        "quality_contract_version": outcome.quality_contract_version,
        "semantic_review_status": outcome.semantic_review_status or None,
        "semantic_review_score": outcome.semantic_review_score,
        "duration_seconds": outcome.duration_seconds,
    }
    coverage = outcome.coverage
    if coverage is not None:
        fields["coverage"] = CoverageProgressResponse(
            planned_topics=coverage.planned_topics,
            covered_topics=coverage.covered_topics,
            substantive_topic_ratio=coverage.substantive_topic_ratio,
            planned_targets=coverage.planned_targets,
            required_targets=coverage.required_targets,
            answered_targets=coverage.answered_targets,
            critical_targets=coverage.critical_targets,
            answered_critical_targets=coverage.answered_critical_targets,
            unanswered_critical_target_ids=list(
                coverage.unanswered_critical_target_ids
            ),
            unaccounted_target_ids=list(coverage.unaccounted_target_ids),
        )
    counts = outcome.evidence_counts
    if counts is not None:
        fields["evidence_counts"] = EvidenceCountsResponse(
            read_records=counts.read_records,
            network_reads=counts.network_reads,
            cache_reads=counts.cache_reads,
            unique_works=counts.unique_works,
            publishers=counts.publishers,
            source_urls=counts.source_urls,
            findings=counts.findings,
            assessed_sources=counts.assessed_sources,
            cited_assessed_sources=counts.cited_assessed_sources,
            checked_claims=counts.checked_claims,
            corroborated=counts.corroborated,
            primary_attributed=counts.primary_attributed,
            contested=counts.contested,
            not_established=counts.not_established,
        )
    return fields


class SessionStore:
    """Own one process's research sessions and their background tasks."""

    def __init__(self, *, runner: ResearchRunner) -> None:
        self._runner = runner
        self._sessions: dict[str, ResearchSession] = {}

    def start(
        self,
        *,
        session_id: str,
        query: str,
        max_iterations: int | None,
        output_format: str,
        config_overrides: dict[str, JsonValue],
        config_path: str,
    ) -> ResearchSession:
        """Register a running session synchronously and schedule its run.

        The record is visible (and its status is ``running``) before the
        background task gets its first chance to execute, so a caller can
        never observe a session that was started but not yet registered.
        """
        if session_id in self._sessions:
            raise ValueError(f"a session already exists for {session_id!r}")
        session = ResearchSession(
            session_id=session_id,
            query=query,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self._sessions[session_id] = session
        session.task = asyncio.create_task(
            self._run(
                session=session,
                query=query,
                max_iterations=max_iterations,
                output_format=output_format,
                config_overrides=config_overrides,
                config_path=config_path,
            )
        )
        return session

    def require(self, session_id: str) -> ResearchSession:
        """Return the session, or raise ``KeyError`` when it is unknown."""
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(
                f"no research session with id {session_id!r}"
            ) from None

    async def iter_events(
        self, session_id: str
    ) -> AsyncIterator[ResearchEvent]:
        """Replay one session's events from the first, then live progress.

        A subscriber always sees the whole history from index zero — the
        retained log, not a delta — and the iterator exits after every
        recorded event has been yielded and either the session has reached a
        terminal status or the session has been closed out by cancellation
        (``finished_at`` set, task done) while its public status still reads
        ``running``. Unknown sessions raise ``KeyError`` immediately.
        """
        session = self.require(session_id)
        yielded = 0
        while True:
            while yielded < len(session.events):
                event = session.events[yielded]
                yielded += 1
                yield event
            if session.status in TERMINAL_STATUSES:
                return
            task = session.task
            if session.finished_at is not None and (
                task is None or task.done()
            ):
                return
            await session.changed.wait()
            session.changed.clear()

    async def close(self) -> None:
        """Cancel every unfinished task; cancellation stays cancellation."""
        pending = [
            session.task
            for session in self._sessions.values()
            if session.task is not None and not session.task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for session in self._sessions.values():
            task = session.task
            if session.finished_at is None and task is not None and task.done():
                session.finished_at = datetime.now(timezone.utc)
                session.changed.set()

    async def _run(
        self,
        *,
        session: ResearchSession,
        query: str,
        max_iterations: int | None,
        output_format: str,
        config_overrides: dict[str, JsonValue],
        config_path: str,
    ) -> None:
        """Drive one runner call and fold its result into the session.

        Failures become status ``failed`` with safe enumerated records —
        never exception text, provider text, or request values. Cancellation
        is not a failure and always propagates; the ``finally`` still closes
        the session out so subscribers wake and readers see timestamps.
        """
        try:
            outcome = await self._runner(
                question=query,
                session_id=session.session_id,
                max_iterations=max_iterations,
                output_format=output_format,
                config_overrides=config_overrides,
                config_path=config_path,
                event_handler=session.publish,
            )
        except ResearchConfigurationError as error:
            _record_failure(
                session,
                error_type="api.research.configuration_error",
                message="Research service configuration is unavailable.",
                details={"reason": error.reason},
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _record_failure(
                session,
                error_type="api.research.failed",
                message="Research run failed unexpectedly.",
                details={"exception_type": type(error).__name__},
            )
        else:
            session.status = outcome.status
            session.iteration = outcome.state.iteration
            session.report_path = outcome.report_path
            session.trace_url = outcome.trace_url
            session.errors = [
                error.model_copy(deep=True) for error in outcome.errors
            ]
            session.outcome = outcome
        finally:
            session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()


def _record_failure(
    session: ResearchSession,
    *,
    error_type: str,
    message: str,
    details: dict[str, str],
) -> None:
    """Record one safe, non-recoverable failure on a session."""
    session.status = "failed"
    session.publish(
        ResearchEvent(
            event_type=error_type,
            source="api",
            message=message,
            metadata=dict(details),
        )
    )
    session.errors.append(
        ResearchError(
            error_type=error_type,
            source="api",
            message=message,
            recoverable=False,
            details=dict(details),
        )
    )
