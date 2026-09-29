"""Process-local research sessions: background tasks and safe snapshots.

The HTTP routes stay thin by owning nothing: ``SessionStore`` starts one
background task per session, records every ``ResearchEvent`` the runner
publishes into an append-only per-session list, and exposes replayable
iteration and safe terminal state. Nothing here touches the network, the
file system, or a provider: the one-time check reaches a provider only through
the injected ``ClarityChecker``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeAlias

from pydantic import JsonValue

from deep_research.api.clarify import (
    ClarityChecker,
    ClarityQuestion,
    clarification_answered_event,
    clarification_requested_event,
    resolve_answers,
)
from deep_research.api.models import (
    ClarificationAnswersRequest,
    CoverageProgressResponse,
    EvidenceCountsResponse,
    SessionStatus,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import HitlConfig
from deep_research.utils.types import ReaderAnswer, ResearchError, ResearchEvent

TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed"}
)

ResearchRunner: TypeAlias = Callable[..., Awaitable[ResearchOutcome]]
_log = logging.getLogger(__name__)


class NotWaitingForInput(Exception):
    """Answers arrived for a session that is not waiting for them (a 409)."""


@dataclass(frozen=True, slots=True)
class ClarificationSubmission:
    """The reader's resolved answers, and whether they chose to start now."""

    answers: tuple[ReaderAnswer, ...]
    skipped: bool


@dataclass(slots=True)
class PendingClarification:
    """The one-time check a ``needs_input`` session is waiting on (spec §4.4)."""

    questions: tuple[ClarityQuestion, ...]
    deadline_at: datetime
    submitted: asyncio.Future[ClarificationSubmission]


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
    clarification: PendingClarification | None = None
    """The check this session waits on while ``needs_input``, else ``None``."""

    def publish(self, event: ResearchEvent) -> None:
        """Record one progress event and update the live status fields."""
        self.events.append(event.model_copy(deep=True))
        node = event.metadata.get("node")
        iteration = event.metadata.get("iteration")
        if event.event_type == "graph.node.started" and isinstance(node, str):
            self.current_agent = node
        # Only the graph's own events carry the pass: researcher.tool_call also
        # carries an ``iteration``, but that is the ReAct step index (A6).
        if event.event_type.startswith("graph.") and isinstance(iteration, int):
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
            required_targets=coverage.required_targets,
            answered_targets=coverage.answered_targets,
            missing_required_target_ids=list(
                coverage.missing_required_target_ids
            ),
            not_found_target_ids=list(coverage.not_found_target_ids),
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
            verified_findings=counts.verified_findings,
            corrected_findings=counts.corrected_findings,
            quoted_findings=counts.quoted_findings,
            dropped_findings=counts.dropped_findings,
            context_unchecked_findings=counts.context_unchecked_findings,
            cited_findings=counts.cited_findings,
        )
    return fields


class SessionStore:
    """Own one process's research sessions and their background tasks."""

    def __init__(
        self,
        *,
        runner: ResearchRunner,
        clarity_checker: ClarityChecker | None = None,
    ) -> None:
        self._runner = runner
        self._clarity_checker = clarity_checker
        self._sessions: dict[str, ResearchSession] = {}

    def start(
        self,
        *,
        session_id: str,
        query: str,
        max_extra_passes: int | None,
        output_format: str,
        config_overrides: dict[str, JsonValue],
        config_path: str,
        ask_clarifying_questions: bool = False,
        settings: Any = None,
        hitl: HitlConfig | None = None,
    ) -> ResearchSession:
        """Register a running session synchronously and schedule its run.

        ``ask_clarifying_questions`` runs the one-time check before the runner
        (live-briefs spec §4.4) when the store has a ``clarity_checker``;
        ``settings`` is what the checker reads and ``hitl`` holds its timings.

        The record is visible (and its status is ``running``) before the
        background task gets its first chance to execute, so a caller can
        never observe a session that was started but not yet registered.

        ``max_extra_passes`` is the extra-pass ceiling the request asked for,
        never ``max_iterations``: the request field kept its name for existing
        clients, and this is the graph's own vocabulary for what it sets.
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
                max_extra_passes=max_extra_passes,
                output_format=output_format,
                config_overrides=config_overrides,
                config_path=config_path,
                ask_clarifying_questions=ask_clarifying_questions,
                settings=settings,
                hitl=hitl or HitlConfig(),
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

    def list_sessions(self, limit: int) -> list[ResearchSession]:
        """The newest ``limit`` sessions: ``started_at`` descending, ties
        newest-registered first.
        """
        newest_registered_first = list(reversed(list(self._sessions.values())))
        ordered = sorted(
            newest_registered_first, key=lambda s: s.started_at, reverse=True
        )
        return ordered[:limit]

    def submit_answers(
        self, session_id: str, request: ClarificationAnswersRequest
    ) -> ResearchSession:
        """Hand the reader's answers to a session waiting in ``needs_input``.

        Raises ``KeyError`` for an unknown session, ``NotWaitingForInput`` when
        the session is not waiting (never asked, already answered, or past its
        deadline), and ``AnswerValidationError`` when an answer does not fit the
        questions asked. Answers are accepted once; the session's own task
        publishes them and starts the run.
        """
        session = self.require(session_id)
        pending = session.clarification
        if (
            session.status != "needs_input"
            or pending is None
            or pending.submitted.done()
            or datetime.now(timezone.utc) >= pending.deadline_at
        ):
            raise NotWaitingForInput(session_id)
        answers = resolve_answers(pending.questions, request.answers)
        pending.submitted.set_result(
            ClarificationSubmission(answers=answers, skipped=request.skip)
        )
        return session

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
        max_extra_passes: int | None,
        output_format: str,
        config_overrides: dict[str, JsonValue],
        config_path: str,
        ask_clarifying_questions: bool = False,
        settings: Any = None,
        hitl: HitlConfig | None = None,
    ) -> None:
        """Drive one runner call and fold its result into the session.

        Failures become status ``failed`` with safe enumerated records —
        never exception text, provider text, or request values. Cancellation
        is not a failure and always propagates; the ``finally`` still closes
        the session out so subscribers wake and readers see timestamps. A run
        cancelled while it waited for answers reads ``running`` with
        ``finished_at`` set, like any other interrupted run.

        With the one-time check on and questions asked, the runner is called
        with ``reader_answers``; otherwise it is called exactly as before.
        """
        try:
            extra: dict[str, Any] = {}
            if ask_clarifying_questions and self._clarity_checker is not None:
                answers = await self._clarify(
                    session, query=query, settings=settings, hitl=hitl or HitlConfig()
                )
                if answers is not None:
                    extra["reader_answers"] = answers
            outcome = await self._runner(
                question=query,
                session_id=session.session_id,
                max_extra_passes=max_extra_passes,
                output_format=output_format,
                config_overrides=config_overrides,
                config_path=config_path,
                event_handler=session.publish,
                **extra,
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
            if session.status == "needs_input":
                session.status = "running"
            session.clarification = None
            session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()

    async def _clarify(
        self,
        session: ResearchSession,
        *,
        query: str,
        settings: Any,
        hitl: HitlConfig,
    ) -> tuple[ReaderAnswer, ...] | None:
        """The one-time check (spec §4.4): the answers, or ``None`` if none were asked.

        The check call gets ``hitl.check_timeout_s``; any failure, a timeout
        included, asks nothing. With questions, the session waits in
        ``needs_input`` for at most ``hitl.answer_wait_s``; whatever the reader
        left unanswered takes its best guess.
        """
        assert self._clarity_checker is not None
        try:
            async with asyncio.timeout(hitl.check_timeout_s):
                check = await self._clarity_checker(query, settings)
            questions = tuple(check.questions)
        except Exception as error:  # noqa: BLE001 - the check never blocks a run
            _log.warning("one-time check skipped: %s", type(error).__name__)
            return None
        if not questions:
            return None
        deadline = datetime.now(timezone.utc) + timedelta(seconds=hitl.answer_wait_s)
        pending = PendingClarification(
            questions=questions,
            deadline_at=deadline,
            submitted=asyncio.get_running_loop().create_future(),
        )
        session.clarification = pending
        session.status = "needs_input"
        session.publish(clarification_requested_event(questions, deadline))
        await asyncio.wait({pending.submitted}, timeout=hitl.answer_wait_s)
        session.clarification = None
        if pending.submitted.done():
            submission = pending.submitted.result()
            answers = submission.answers
            reason = "skipped" if submission.skipped else "answered"
        else:
            pending.submitted.cancel()
            answers = resolve_answers(questions)
            reason = "timed_out"
        session.status = "running"
        session.publish(clarification_answered_event(answers, reason))
        return answers


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
