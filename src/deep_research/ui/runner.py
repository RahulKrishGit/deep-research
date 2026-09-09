"""A non-blocking, process-local controller for the Streamlit UI."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from typing import TypeAlias

from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    prepare_research_settings,
    run_research_sync,
)
from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
)
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.ui.history import SessionHistoryStore
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiFactCheckSummary,
    UiSessionSnapshot,
    UiSourceSummary,
    history_entry_from_snapshot,
)
from deep_research.ui.progress import (
    display_tool_name,
    fact_check_summary,
    limitations_from_outcome,
    project_progress,
    source_summary,
    token_usage_from_outcome,
)
from deep_research.utils.config import load_config
from deep_research.utils.types import ResearchError, ResearchEvent

SyncRunner: TypeAlias = Callable[..., ResearchOutcome]
Preflight: TypeAlias = Callable[..., object]

_UNEXPECTED_FAILURE_MESSAGE = "Research run failed unexpectedly."
_CONFIGURATION_FAILURE_MESSAGE = "Research service configuration is unavailable."
_FALLBACK_CONFIGURATION_REASON = "configuration_error"
_FALLBACK_CONFIGURATION_HINT = (
    "Review the research configuration and try again."
)


@dataclass(slots=True)
class _ActiveSession:
    session_id: str
    question: str
    started_at: datetime
    max_iterations: int
    events: list[ResearchEvent] = field(default_factory=list)
    outcome: ResearchOutcome | None = None
    status: str = "running"
    finished_at: datetime | None = None
    failure: ResearchError | None = None


class LocalResearchController:
    """Own local worker threads and safe snapshots for research sessions."""

    def __init__(
        self,
        *,
        config_path: str = DEFAULT_CONFIG_PATH,
        runner: SyncRunner | None = None,
        preflight: Preflight | None = None,
        history_store: SessionHistoryStore | None = None,
    ) -> None:
        self._config_path = str(config_path)
        self._runner = runner or run_research_sync
        self._preflight = preflight or prepare_research_settings
        settings = load_config(self._config_path, strict=False)
        self._default_max_iterations = settings.graph.max_iterations
        self._history = history_store or SessionHistoryStore(
            output_directory=Path(settings.output.directory)
        )
        self._lock = RLock()
        self._sessions: dict[str, _ActiveSession] = {}

    @property
    def default_max_iterations(self) -> int:
        return self._default_max_iterations

    def start(
        self,
        *,
        question: str,
        max_iterations: int,
        output_format: str = "markdown",
    ) -> UiSessionSnapshot:
        """Validate configuration, register a session, and start one worker."""
        if type(max_iterations) is not int or max_iterations < 1:
            raise ValueError("max_iterations must be a positive integer")

        # This is deliberately before the active-session record is registered.
        self._preflight(
            config_path=self._config_path,
            output_format=output_format,
        )

        from deep_research.main import new_session_id

        session = _ActiveSession(
            session_id=new_session_id(),
            question=question,
            started_at=datetime.now(timezone.utc),
            max_iterations=max_iterations,
        )
        with self._lock:
            self._sessions[session.session_id] = session

        try:
            self._persist(self._snapshot(session.session_id))
        except Exception:
            with self._lock:
                self._sessions.pop(session.session_id, None)
            raise

        worker = Thread(
            target=self._run,
            kwargs={
                "session_id": session.session_id,
                "question": question,
                "max_iterations": max_iterations,
                "output_format": output_format,
            },
            name=f"deep-research-{session.session_id}",
            daemon=True,
        )
        worker.start()
        return self.snapshot(session.session_id)

    def snapshot(self, session_id: str) -> UiSessionSnapshot:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"unknown research session: {session_id!r}")
        return self._snapshot(session_id)

    def list_history(self, *, limit: int = 50) -> list[SessionHistoryEntry]:
        with self._lock:
            entries = self._history.list_entries(limit=limit)
        return [self._display_history_entry(entry) for entry in entries]

    def history_entry(self, session_id: str) -> SessionHistoryEntry | None:
        with self._lock:
            entry = self._history.get(session_id)
        if entry is None:
            return None
        return self._display_history_entry(entry)

    def read_history_report(self, entry: SessionHistoryEntry) -> str | None:
        with self._lock:
            return self._history.read_report(entry)

    def _run(
        self,
        *,
        session_id: str,
        question: str,
        max_iterations: int,
        output_format: str,
    ) -> None:
        try:
            outcome = self._runner(
                question=question,
                session_id=session_id,
                max_iterations=max_iterations,
                output_format=output_format,
                config_path=self._config_path,
                event_handler=lambda event: self._publish(session_id, event),
            )
        except ResearchConfigurationError as error:
            reason = _safe_configuration_reason(error.reason)
            self._record_failure(
                session_id,
                reason=reason,
                hint=CONFIGURATION_HINTS.get(reason, _FALLBACK_CONFIGURATION_HINT),
                message=_CONFIGURATION_FAILURE_MESSAGE,
            )
            return
        except Exception:
            self._record_failure(
                session_id,
                reason="unexpected_failure",
                hint=None,
                message=_UNEXPECTED_FAILURE_MESSAGE,
            )
            return

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            outcome_copy = deepcopy(outcome)
            finished_at = datetime.now(timezone.utc)

        terminal_snapshot = self._snapshot_from_values(
            session_id,
            outcome=outcome_copy,
            status=outcome_copy.status,
            finished_at=finished_at,
        )
        self._persist_terminal(terminal_snapshot)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.outcome = outcome_copy
                session.status = outcome_copy.status
                session.finished_at = finished_at

    def _publish(self, session_id: str, event: ResearchEvent) -> None:
        with self._lock:
            copied_event = event.model_copy(deep=True)
            session = self._sessions.get(session_id)
            if session is not None:
                session.events.append(copied_event)

    def _record_failure(
        self,
        session_id: str,
        *,
        reason: str,
        hint: str | None,
        message: str,
    ) -> None:
        details = {"reason": reason}
        if hint is not None:
            details["hint"] = hint
        error = ResearchError(
            error_type=(
                "ui.research.configuration_error"
                if reason != "unexpected_failure"
                else "ui.research.failed"
            ),
            source="ui",
            message=message,
            recoverable=False,
            details=details,
        )
        event = ResearchEvent(
            event_type=error.error_type,
            source="ui",
            message=message,
            metadata=details,
        )
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.events.append(event)
            finished_at = datetime.now(timezone.utc)

        terminal_snapshot = self._snapshot_from_values(
            session_id,
            status="failed",
            finished_at=finished_at,
            failure=error,
        )
        self._persist_terminal(terminal_snapshot)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.failure = error
                session.status = "failed"
                session.finished_at = finished_at

    def _snapshot(self, session_id: str) -> UiSessionSnapshot:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"unknown research session: {session_id!r}")
            values = {
                "events": [event.model_copy(deep=True) for event in session.events],
                "outcome": deepcopy(session.outcome),
                "failure": (
                    session.failure.model_copy(deep=True)
                    if session.failure
                    else None
                ),
                "status": session.status,
                "finished_at": session.finished_at,
                "question": session.question,
                "started_at": session.started_at,
                "requested_max_iterations": session.max_iterations,
            }
        return self._snapshot_from_values(session_id, **values)

    def _snapshot_from_values(
        self,
        session_id: str,
        *,
        events: list[ResearchEvent] | None = None,
        outcome: ResearchOutcome | None = None,
        failure: ResearchError | None = None,
        status: str = "running",
        finished_at: datetime | None = None,
        question: str | None = None,
        started_at: datetime | None = None,
        requested_max_iterations: int | None = None,
    ) -> UiSessionSnapshot:
        if question is None or started_at is None or requested_max_iterations is None:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is None:
                    raise KeyError(f"unknown research session: {session_id!r}")
                question = session.question
                started_at = session.started_at
                requested_max_iterations = session.max_iterations
                if events is None:
                    events = [
                        event.model_copy(deep=True) for event in session.events
                    ]
                if outcome is None:
                    outcome = deepcopy(session.outcome)
                if failure is None and session.failure is not None:
                    failure = session.failure.model_copy(deep=True)
                if status == "running":
                    status = session.status
                if finished_at is None:
                    finished_at = session.finished_at

        if outcome is not None:
            events = [event.model_copy(deep=True) for event in outcome.state.events]
            progress = project_progress(events)
            source_result = source_summary(
                outcome.state.evaluated_sources,
                outcome.state.raw_findings,
            )
            claims_result = fact_check_summary(outcome.state.verified_claims)
            token_usage = token_usage_from_outcome(outcome)
            tool_calls = [
                {
                    "tool_name": summary.tool_name,
                    "display_label": display_tool_name(summary.tool_name),
                    "calls": summary.calls,
                    "failures": summary.failures,
                }
                for summary in outcome.tool_calls
            ]
            return UiSessionSnapshot(
                session_id=session_id,
                question=question,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                current_agent=(
                    progress.current_agent if status == "failed" else None
                ),
                iteration=outcome.state.iteration,
                max_iterations=outcome.state.max_iterations,
                sub_topics=progress.sub_topics,
                recent_activity=progress.recent_activity,
                tool_calls=tool_calls,
                token_usage=token_usage,
                trace_url=outcome.trace_url,
                report_path=outcome.report_path,
                report=outcome.report,
                source_summary=source_result,
                fact_check_summary=claims_result,
                errors=[error.model_copy(deep=True) for error in outcome.errors],
                limitations=limitations_from_outcome(outcome),
                events_seen=len(events),
            ).model_copy(deep=True)

        assert events is not None
        progress = project_progress(events)
        errors = [] if failure is None else [failure]
        snapshot = UiSessionSnapshot(
            session_id=session_id,
            question=question,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            current_agent=(
                progress.current_agent
                if status in {"running", "failed"}
                else None
            ),
            iteration=progress.iteration,
            max_iterations=requested_max_iterations,
            sub_topics=progress.sub_topics,
            recent_activity=progress.recent_activity,
            tool_calls=progress.tool_calls,
            token_usage=None,
            trace_url=None,
            report_path=None,
            report=None,
            source_summary=UiSourceSummary(
                total=0,
                high=0,
                moderate=0,
                low=0,
                unrated=0,
            ),
            fact_check_summary=UiFactCheckSummary(
                verified=0,
                unverified=0,
                contradicted=0,
                insufficient_evidence=0,
            ),
            errors=errors,
            limitations=[],
            events_seen=len(events),
        )
        return snapshot.model_copy(deep=True)

    def _persist(self, snapshot: UiSessionSnapshot) -> None:
        entry = history_entry_from_snapshot(snapshot)
        with self._lock:
            self._history.upsert(entry)

    def _persist_terminal(self, snapshot: UiSessionSnapshot) -> None:
        """Best-effort terminal persistence; memory state remains authoritative."""
        try:
            self._persist(snapshot)
        except Exception:
            # A local history outage must never strand a finished worker as
            # running, and its exception text must not reach the UI.
            return

    def _display_history_entry(self, entry: SessionHistoryEntry) -> SessionHistoryEntry:
        with self._lock:
            active = entry.session_id in self._sessions
        if entry.status == "running" and not active:
            return entry.model_copy(update={"status": "incomplete"})
        return entry.model_copy(deep=True)


def _safe_configuration_reason(reason: object) -> str:
    if isinstance(reason, str) and reason in CONFIGURATION_HINTS:
        return reason
    return _FALLBACK_CONFIGURATION_REASON


__all__ = ["LocalResearchController"]
