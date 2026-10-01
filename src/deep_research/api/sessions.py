"""Process-local research sessions: background tasks and safe snapshots.

The HTTP routes stay thin by owning nothing: ``SessionStore`` starts one
background task per session, records every ``ResearchEvent`` the runner
publishes into an append-only per-session list, and exposes replayable
iteration and safe terminal state. Nothing here touches the network, the
file system, or a provider: the one-time check reaches a provider only through
the injected ``ClarityChecker``, and a reader note only through the injected
``NoteInterpreter``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypeAlias

from pydantic import JsonValue

from deep_research.api.clarify import (
    ClarityChecker,
    ClarityQuestion,
    clarification_answered_event,
    clarification_requested_event,
    resolve_answers,
)
from deep_research.api.models import (
    ClarificationAnswerResponse,
    ClarificationAnswersRequest,
    ClarificationQuestionResponse,
    ClarificationRecordResponse,
    CoverageProgressResponse,
    EvidenceCountsResponse,
    ReaderNoteResponse,
    ReportOutlineEntryResponse,
    SessionStatus,
)
from deep_research.api.notes import (
    NoteInterpreter,
    fallback_interpretation,
    note_interpreted_event,
    note_received_event,
    note_records,
    reader_note,
)
from deep_research.api.stop import CHECK_STEP, active_row, session_stopped_event
from deep_research.agents.report import report_outline
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.notes import NoteBoard, ReceivedNote, bind_note_board
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import HitlConfig
from deep_research.utils.types import ReaderAnswer, ResearchError, ResearchEvent

TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed", "stopped"}
)

ResearchRunner: TypeAlias = Callable[..., Awaitable[ResearchOutcome]]
_log = logging.getLogger(__name__)


class NotWaitingForInput(Exception):
    """Answers arrived for a session that is not waiting for them (a 409)."""


class NotesClosed(Exception):
    """A note arrived for a session that no longer takes notes (a 409, spec §4.6)."""


StopRefusal: TypeAlias = Literal["finished", "publishing", "closing"]


class NotStoppable(Exception):
    """A stop for a session that can no longer be stopped (a 409, notes-progress-report spec §8.1).

    ``reason`` says why: ``finished`` once the session has ended (a stopped one
    included), ``publishing`` once the run has decided to publish or to end, and
    ``closing`` while the service shuts down.
    """

    def __init__(self, session_id: str, reason: StopRefusal) -> None:
        super().__init__(session_id)
        self.reason: StopRefusal = reason


@dataclass(frozen=True, slots=True)
class CheckRecord:
    """The one-time check a session asked, and the answers its run started with."""

    questions: tuple[ClarityQuestion, ...]
    answers: tuple[ReaderAnswer, ...] = ()


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
    check: CheckRecord | None = None
    """The check this session asked, if any, kept for the status response."""
    note_board: NoteBoard = field(default_factory=NoteBoard)
    """The reader's notes (live-briefs spec §4.6), bound for the run's task."""
    note_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    notes_closed: bool = False
    """Set once the stream shows publication has begun: no note is taken after."""
    note_passes: int = 0
    run_settings: Any = None
    hitl: HitlConfig = field(default_factory=HitlConfig)
    stopped_step: str | None = None
    """The step the reader stopped the run at (notes-progress-report spec §8.2), else ``None``."""

    def publish(self, event: ResearchEvent) -> None:
        """Record one progress event and update the live status fields.

        A stopped session takes no more events (notes-progress-report spec §8.2), so
        ``session.stopped`` stays its last — whatever a task finishing its own
        cancellation still hands over, a replay's pacer for one.
        """
        if self.status == "stopped":
            return
        self.events.append(event.model_copy(deep=True))
        node = event.metadata.get("node")
        iteration = event.metadata.get("iteration")
        if event.event_type == "graph.node.started" and isinstance(node, str):
            self.current_agent = node
        # Only the graph's own events carry the pass: researcher.tool_call also
        # carries an ``iteration``, but that is the ReAct step index (A6).
        if event.event_type.startswith("graph.") and isinstance(iteration, int):
            self.iteration = iteration
        # live-briefs spec §4.6: a decision to publish (or to end) closes the
        # notes — this is the event that makes Publishing the active row, so
        # the page and the API agree on when notes stop.
        if event.event_type == "graph.route.decided" and event.metadata.get(
            "destination"
        ) in ("finalize", "end"):
            self.notes_closed = True
        note_passes = event.metadata.get("note_passes")
        if event.event_type == "graph.note_pass.started" and isinstance(note_passes, int):
            self.note_passes = note_passes
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
    # Notes-progress-report spec §7.5: the headings of the report ``/report``
    # serves -- the Markdown and the outline come from one composition.
    composition = outcome.composition
    if composition is not None and outcome.report is not None:
        fields["report_outline"] = [
            ReportOutlineEntryResponse(**entry.model_dump()) for entry in report_outline(composition)
        ]
    return fields


def session_note_fields(session: ResearchSession) -> dict[str, object]:
    """The session response's reader-side fields (live-briefs spec §4.4, §4.6).

    Every note in receipt order with its reading and outcome, how many more
    notes the session takes, the note passes the run bought, and the one-time
    check it asked. A finished run's own state is the authority for outcomes
    and the pass count; while it runs, the stream's count stands in.
    """
    state = session.outcome.state if session.outcome is not None else None
    # notes-progress-report spec §5.6: a session that has ended never reports a
    # note ``pending``; a note nothing judged reads ``not_checked``.
    terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None
    check = session.check
    return {
        "notes": [
            ReaderNoteResponse(
                note_id=record.received.note_id,
                text=record.received.text,
                restatement=record.restatement,
                outcome=record.outcome,
                steering_outcome=record.steering_outcome,
            )
            for record in note_records(session.note_board, state, terminal=terminal)
        ],
        "notes_remaining": session.note_board.remaining,
        "note_passes": state.note_passes if state is not None else session.note_passes,
        "clarification": (
            None
            if check is None
            else ClarificationRecordResponse(
                questions=[
                    ClarificationQuestionResponse(**question.model_dump(mode="json"))
                    for question in check.questions
                ],
                answers=[
                    ClarificationAnswerResponse(
                        question_id=answer.question_id,
                        value=answer.value,
                        source=answer.source,
                    )
                    for answer in check.answers
                ],
            )
        ),
    }


class SessionStore:
    """Own one process's research sessions and their background tasks."""

    def __init__(
        self,
        *,
        runner: ResearchRunner,
        clarity_checker: ClarityChecker | None = None,
        note_interpreter: NoteInterpreter | None = None,
    ) -> None:
        self._runner = runner
        self._clarity_checker = clarity_checker
        self._note_interpreter = note_interpreter
        self._sessions: dict[str, ResearchSession] = {}
        self._closing = False

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
            run_settings=settings,
            hitl=hitl or HitlConfig(),
        )
        self._sessions[session_id] = session
        # live-briefs spec §4.6: the task copies this context, so the session's
        # note board is bound for its whole run — every node and every task an
        # agent starts inside it reads the same board.
        with bind_note_board(session.note_board):
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

    def stop(self, session_id: str) -> ResearchSession:
        """Stop one session at once (notes-progress-report spec §8.2, D17).

        Raises ``KeyError`` for an unknown session and ``NotStoppable`` when it can no
        longer be stopped: ``finished`` once it has ended (a stopped one included),
        ``publishing`` once its stream shows the run's decision to publish or end — the
        notes cutoff — and ``closing`` while the store shuts down. Otherwise, with no
        ``await`` anywhere: the step it was on is read from what it has published
        (``check`` while the one-time check waits); ``session.stopped`` is published as
        its last event; the session ends ``stopped``; a pending check is cancelled; and
        the run's task and every note reading are cancelled, which cancels every call
        they have in flight. Nothing more is published, so nothing is written.
        """
        session = self.require(session_id)
        if session.status in TERMINAL_STATUSES or session.finished_at is not None:
            raise NotStoppable(session_id, "finished")
        if session.notes_closed:
            raise NotStoppable(session_id, "publishing")
        if self._closing:
            raise NotStoppable(session_id, "closing")
        if session.status == "needs_input":
            step = CHECK_STEP
        else:
            row = active_row(session.events)
            if row is None:
                # No row is active only once the run is ending: each event that leaves
                # none follows the decision that closes notes (spec ambiguity 3).
                raise NotStoppable(session_id, "publishing")
            step = row
        now = datetime.now(timezone.utc)
        session.publish(
            session_stopped_event(
                step, now, int((now - session.started_at).total_seconds())
            )
        )
        session.status = "stopped"
        session.stopped_step = step
        session.finished_at = now
        session.current_agent = None
        pending = session.clarification
        if pending is not None and not pending.submitted.done():
            pending.submitted.cancel()
        session.clarification = None
        session.changed.set()
        for task in (session.task, *session.note_tasks):
            if task is not None and not task.done():
                task.cancel()
        return session

    def add_note(self, session_id: str, text: str) -> ReceivedNote:
        """Accept one reader note for a running session (live-briefs spec §4.6).

        Raises ``KeyError`` for an unknown session; ``NotesClosed`` while the
        session waits for the one-time check's answers, once it has finished
        or stopped, once the store is closing (its tasks are being cancelled
        while the session still reads ``running`` without a ``finished_at``),
        and once its stream shows publication has begun; and
        ``NoteLimitReached`` past the tenth accepted note (D11a). A refused
        note is never counted. An accepted note is published at once, then
        interpreted in the background: the interpreted note joins the run's
        board and ``session.note.interpreted`` follows.
        """
        session = self.require(session_id)
        if (
            session.status != "running"
            or session.finished_at is not None
            or session.notes_closed
            or self._closing
        ):
            raise NotesClosed(session_id)
        received = session.note_board.receive(
            text,
            received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            received_during=session.current_agent or "planner",
        )
        session.publish(note_received_event(received))
        task = asyncio.create_task(self._interpret_note(session, received))
        session.note_tasks.add(task)
        task.add_done_callback(
            lambda done: _note_task_done(session, received.note_id, done)
        )
        return received

    async def _interpret_note(
        self, session: ResearchSession, received: ReceivedNote
    ) -> None:
        """Read one note within ``hitl.note_interpret_timeout_s``, or keep it as written.

        Any failure — no interpreter, a provider error, a timeout, an invalid
        reading — keeps the note as an emphasis in the reader's own words, and
        its event says ``fallback`` (spec §4.6). Cancellation (the service
        closing) drops the note from the board's pending set, so nothing waits
        on it.
        """
        # Only notes numbered before this one, whatever else the board holds: a
        # note is read against what the reader had already said.
        received_ids = [note.note_id for note in session.note_board.received()]
        before = set(received_ids[: received_ids.index(received.note_id)])
        earlier = [
            note for note in session.note_board.snapshot() if note.note_id in before
        ]
        fallback = False
        try:
            if self._note_interpreter is None:
                raise LookupError("no note interpreter")
            async with asyncio.timeout(session.hitl.note_interpret_timeout_s):
                reading = await self._note_interpreter(
                    received.text, session.query, earlier, session.run_settings
                )
        except asyncio.CancelledError:
            session.note_board.drop(received.note_id)
            raise
        except Exception as error:  # noqa: BLE001 - a note is never lost to its reading
            _log.warning("note interpretation fell back: %s", type(error).__name__)
            reading, fallback = fallback_interpretation(received.text), True
        note = reader_note(received, reading)
        session.note_board.add(note)
        session.publish(note_interpreted_event(note, fallback=fallback))

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
        """Cancel every unfinished task; cancellation stays cancellation.

        The closing flag goes up first, so no note is taken from here on: a
        note accepted between ``task.cancel()`` and a task's own close-out
        would outlive this call. ``task.cancelling()`` cannot say this — a
        timeout firing inside the run's own task raises it too.
        """
        self._closing = True
        pending = [
            session.task
            for session in self._sessions.values()
            if session.task is not None and not session.task.done()
        ] + [
            task
            for session in self._sessions.values()
            for task in session.note_tasks
            if not task.done()
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
        ``finished_at`` set, like any other interrupted run. A run the reader
        stopped was closed out by ``stop`` already: it keeps its ``stopped``
        status and the time of the stop, and nothing it returns or raises on
        the way out is folded in.

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
            # A runner that caught the stop's cancellation and returned anyway does not
            # undo the stop: a stopped session keeps no outcome (spec §8.4).
            if session.status != "stopped":
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
            # A stop closed the session out at the moment the reader asked for it.
            if session.finished_at is None:
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
        session.check = CheckRecord(questions=questions)
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
        session.check = CheckRecord(questions=questions, answers=tuple(answers))
        session.publish(clarification_answered_event(answers, reason))
        return answers


def _note_task_done(
    session: ResearchSession, note_id: str, task: asyncio.Task[None]
) -> None:
    """Forget a finished reading; a reading cancelled before its first step is dropped.

    A task cancelled before it ever ran never enters ``_interpret_note``, so its
    own handler cannot drop the note: without this, the board would wait on it.
    """
    session.note_tasks.discard(task)
    if task.cancelled():
        session.note_board.drop(note_id)


def _record_failure(
    session: ResearchSession,
    *,
    error_type: str,
    message: str,
    details: dict[str, str],
) -> None:
    """Record one safe, non-recoverable failure on a session.

    A stopped session records none: whatever its cancellation raised on the way
    out, the reader's stop is how it ended (notes-progress-report spec §8.4).
    """
    if session.status == "stopped":
        return
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
