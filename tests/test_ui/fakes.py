"""Offline synchronous runner fakes for local UI controller tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any

from deep_research.observability import TokenUsage
from deep_research.runtime.errors import CONFIGURATION_HINTS
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiClaimDetail,
    UiFactCheckSummary,
    UiRecentActivity,
    UiSessionSnapshot,
    UiSourceDetail,
    UiSourceSummary,
    UiSubTopicProgress,
    UiTokenUsage,
    UiToolCallSummary,
    history_entry_from_snapshot,
)
from deep_research.utils.types import (
    Claim,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
)

_DEMO_TIME = datetime(2026, 9, 9, 11, 20, tzinfo=timezone.utc)
_DEMO_QUESTION = "How will grid-scale batteries reshape energy markets by 2030?"
_DEMO_TOPICS = (
    "Current battery chemistry cost curves and manufacturing scale-up",
    "Grid operator adoption patterns and regulatory drivers",
    "Competing storage technologies and displacement risk",
    "Investment flows and project financing trends 2025–2030",
    "Market structure impacts on peaker plants and pricing",
)
_DEMO_MODES = (
    "New",
    "Running",
    "Completed",
    "History",
    "Max iterations",
    "Failed/partial",
)


def _empty_source_summary() -> UiSourceSummary:
    return UiSourceSummary(total=0, high=0, moderate=0, low=0, unrated=0)


def _empty_fact_summary() -> UiFactCheckSummary:
    return UiFactCheckSummary(
        verified=0,
        unverified=0,
        contradicted=0,
        insufficient_evidence=0,
    )


def _demo_topics() -> list[UiSubTopicProgress]:
    return [
        UiSubTopicProgress(index=1, title=_DEMO_TOPICS[0], status="completed"),
        UiSubTopicProgress(index=2, title=_DEMO_TOPICS[1], status="running"),
        UiSubTopicProgress(index=3, title=_DEMO_TOPICS[2], status="queued"),
        UiSubTopicProgress(index=4, title=_DEMO_TOPICS[3], status="queued"),
        UiSubTopicProgress(index=5, title=_DEMO_TOPICS[4], status="queued"),
    ]


def _demo_activities() -> list[UiRecentActivity]:
    return [
        UiRecentActivity(
            event_type="subtopic.completed",
            summary=(
                "Completed subtopic 1 — cost curves and manufacturing, "
                "6 sources reviewed"
            ),
        ),
        UiRecentActivity(
            event_type="subtopic.started",
            summary=(
                "Started subtopic 2 — grid operator adoption and regulatory "
                "drivers"
            ),
        ),
        UiRecentActivity(
            event_type="source.evaluated",
            summary="Evaluated 4 new sources for credibility and relevance",
        ),
    ]


def _demo_sources() -> UiSourceSummary:
    details = [
        UiSourceDetail(
            title="Grid Storage Outlook",
            url="https://example.com/grid-storage",
            tier="high",
            overall_score=0.92,
            rationale="Primary market dataset with transparent methodology.",
            corroboration_score=0.88,
            related_sub_topics=["Battery cost curves"],
        ),
        UiSourceDetail(
            title="Regional Grid Policy Review",
            url="https://example.com/grid-policy",
            tier="moderate",
            overall_score=0.73,
            rationale="Useful regulatory comparison with a limited forecast horizon.",
            corroboration_score=0.62,
            related_sub_topics=["Grid operator adoption"],
        ),
        UiSourceDetail(
            title="Storage Market Commentary",
            url="https://example.com/storage-commentary",
            tier="low",
            overall_score=0.48,
            rationale="Directional commentary with limited source transparency.",
            corroboration_score=0.35,
            related_sub_topics=["Market structure impacts"],
        ),
        UiSourceDetail(
            title="Unrated Pilot Note",
            url="https://example.com/pilot-note",
            tier="unrated",
            overall_score=0.0,
            rationale="The source could not be scored with the available evidence.",
            corroboration_score=0.1,
            related_sub_topics=["Competing storage technologies"],
        ),
    ]
    return UiSourceSummary(
        total=4,
        high=1,
        moderate=1,
        low=1,
        unrated=1,
        details=details,
    )


def _demo_claims() -> UiFactCheckSummary:
    details = [
        UiClaimDetail(
            text="Grid storage deployment expands substantially by 2030.",
            verdict="verified",
            confidence=0.92,
            source_urls=["https://example.com/grid-storage"],
            evidence=["Capacity data supports the estimate."],
        ),
        UiClaimDetail(
            text="All new storage projects use the same battery chemistry.",
            verdict="unverified",
            confidence=0.42,
            source_urls=["https://example.com/storage-commentary"],
            evidence=["The available sources do not cover all projects."],
        ),
        UiClaimDetail(
            text="Interconnection is never a constraint.",
            verdict="contradicted",
            confidence=0.89,
            source_urls=["https://example.com/grid-policy"],
            evidence=["Operator filings identify queue delays."],
            contradictions=["The claim conflicts with operator filings."],
        ),
        UiClaimDetail(
            text="Pilot economics will determine every regional outcome.",
            verdict="insufficient_evidence",
            confidence=0.28,
            source_urls=["https://example.com/pilot-note"],
            evidence=["Pilot-scale evidence is too limited for this conclusion."],
        ),
    ]
    return UiFactCheckSummary(
        verified=1,
        unverified=1,
        contradicted=1,
        insufficient_evidence=1,
        details=details,
    )


def _demo_report(*, partial: bool = False) -> str:
    ending = (
        "## Outlook to 2030\n\n"
        "Taken together, the evidence points to grid-scale storage becoming a default "
        "grid-planning asset rather than a supplementary one. The clearest uncertainty "
        "is how quickly interconnection and permitting reform can keep pace.\n\n"
        "### Sources cited\n\n"
        "[1] [Grid Storage Outlook](https://example.com/grid-storage) · "
        "[2] [Regional Grid Policy Review](https://example.com/grid-policy)"
    )
    if partial:
        ending = (
            "## Stopping point\n\n"
            "This partial report preserves the completed evidence and the next queued "
            "research question."
        )
    return f"""# Executive Summary

Battery storage is positioned to become a major source of flexibility on power
grids by 2030, driven by falling cell costs, renewable curtailment, and market
rules that reward multiple services.

- Deployment is expected to grow several-fold from 2024 levels.
- Manufacturing concentration creates both cost advantages and supply-chain risk.
- Interconnection and permitting remain material deployment constraints.

## Cost Trajectory and Manufacturing Scale

Pack costs have fallen sharply, while manufacturing scale-up and
balance-of-system learning continue to reduce project economics. The
[Grid Storage Outlook](https://example.com/grid-storage) provides a transparent
baseline.

## Grid Operator Adoption and Regulatory Drivers

Regulatory reform is unlocking storage for frequency response, capacity, and
arbitrage. Regional operator rules determine which projects can stack those
services.

## Market Structure Impacts

As storage penetration rises, wholesale price volatility may compress rather
than expand. Storage can flatten daily curves while changing the economics of
peaker plants.

| Scenario | 2030 capacity | Key driver |
| --- | ---: | --- |
| Conservative | ~680 GW | Slower interconnection reform |
| Base case | ~1,020 GW | Current policy trajectory holds |
| Accelerated | ~1,400 GW | Lower sodium-ion costs |

{ending}
"""


def _demo_snapshot(
    *,
    session_id: str,
    question: str = _DEMO_QUESTION,
    status: str = "running",
    iteration: int = 2,
    max_iterations: int = 4,
    token_usage: UiTokenUsage | None = UiTokenUsage(
        input_tokens=18_600,
        output_tokens=6_200,
    ),
    trace_url: str | None = "https://smith.langchain.com/o/demo/r/demo",
    report: str | None = None,
    report_path: str | None = None,
    errors: Sequence[ResearchError] = (),
    limitations: Sequence[str] = (),
    finished_at: datetime | None = None,
    quality: bool = False,
) -> UiSessionSnapshot:
    return UiSessionSnapshot(
        session_id=session_id,
        question=question,
        status=status,
        started_at=_DEMO_TIME - timedelta(minutes=12),
        finished_at=finished_at,
        current_agent="researcher" if status == "running" else None,
        iteration=iteration,
        max_iterations=max_iterations,
        planned_sub_topic_count=5 if status == "running" else 0,
        sub_topics=_demo_topics() if status == "running" else [],
        recent_activity=_demo_activities() if status == "running" else [],
        tool_calls=(
            [
                UiToolCallSummary(
                    tool_name="web_search",
                    display_label="Web search",
                    calls=8,
                    failures=0,
                )
            ]
            if status == "running"
            else []
        ),
        token_usage=token_usage,
        trace_url=trace_url,
        report_path=report_path,
        report=report,
        source_summary=_demo_sources() if quality else _empty_source_summary(),
        fact_check_summary=_demo_claims() if quality else _empty_fact_summary(),
        errors=list(errors),
        limitations=list(limitations),
        events_seen=8 if status == "running" else 24,
    )


class DemoController:
    """Deterministic, provider-free controller used by AppTests and visual review."""

    running_session_id = "1" * 32
    completed_session_id = "2" * 32
    max_iterations_session_id = "3" * 32
    incomplete_session_id = "4" * 32
    failed_partial_session_id = "5" * 32
    no_telemetry_session_id = "6" * 32
    configuration_error_session_id = "7" * 32
    started_session_id = "0" * 32

    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.history_reports: dict[str, str] = {}
        self._snapshots = self._build_snapshots()
        self._entries = self._build_history()

    @property
    def default_max_iterations(self) -> int:
        return 4

    @property
    def modes(self) -> tuple[str, ...]:
        return _DEMO_MODES

    def start(
        self,
        *,
        question: str,
        max_iterations: int,
        output_format: str = "markdown",
    ) -> UiSessionSnapshot:
        self.start_calls.append(
            {
                "question": question,
                "max_iterations": max_iterations,
                "output_format": output_format,
            }
        )
        snapshot = _demo_snapshot(
            session_id=self.started_session_id,
            question=question,
            max_iterations=max_iterations,
        )
        self._snapshots[self.started_session_id] = snapshot
        self._upsert_history(snapshot)
        return snapshot.model_copy(deep=True)

    def snapshot(self, session_id: str) -> UiSessionSnapshot:
        return self._snapshots[session_id].model_copy(deep=True)

    def list_history(self, *, limit: int | None = None) -> list[SessionHistoryEntry]:
        entries = self._entries if limit is None else self._entries[:limit]
        return [entry.model_copy(deep=True) for entry in entries]

    def history_entry(self, session_id: str) -> SessionHistoryEntry | None:
        for entry in self._entries:
            if entry.session_id == session_id:
                return entry.model_copy(deep=True)
        return None

    def read_history_report(self, entry: SessionHistoryEntry) -> str | None:
        return self.history_reports.get(entry.session_id)

    def complete_started_session(self) -> UiSessionSnapshot:
        snapshot = self._snapshots[self.started_session_id]
        completed = _demo_snapshot(
            session_id=self.started_session_id,
            question=snapshot.question,
            status="completed",
            iteration=3,
            max_iterations=snapshot.max_iterations,
            report=_demo_report(),
            report_path="reports/grid-scale-batteries-2030.md",
            finished_at=_DEMO_TIME,
            quality=True,
            limitations=[
                "Sodium-ion cost projections rely on early pilot-scale data.",
            ],
        )
        self._snapshots[self.started_session_id] = completed
        self._upsert_history(completed)
        self.history_reports[self.started_session_id] = completed.report or ""
        return completed.model_copy(deep=True)

    def session_id_for_mode(self, mode: str) -> str | None:
        return {
            "Running": self.running_session_id,
            "Completed": self.completed_session_id,
            "Max iterations": self.max_iterations_session_id,
            "Failed/partial": self.failed_partial_session_id,
        }.get(mode)

    def _build_snapshots(self) -> dict[str, UiSessionSnapshot]:
        failed_error = ResearchError(
            error_type="ui.research.failed",
            source="ui",
            message="Research run failed unexpectedly.",
            recoverable=False,
            details={"diagnostic": "DEMO_ONLY_PRIVATE_DETAIL"},
        )
        configuration_error = ResearchError(
            error_type="ui.research.configuration_error",
            source="ui",
            message="Research service configuration is unavailable.",
            recoverable=False,
            details={
                "reason": "missing_secrets",
                "hint": CONFIGURATION_HINTS["missing_secrets"],
            },
        )
        return {
            self.running_session_id: _demo_snapshot(
                session_id=self.running_session_id,
            ),
            self.completed_session_id: _demo_snapshot(
                session_id=self.completed_session_id,
                status="completed",
                iteration=3,
                report=_demo_report(),
                report_path="reports/grid-scale-batteries-2030.md",
                finished_at=_DEMO_TIME,
                quality=True,
                limitations=[
                    "Sodium-ion cost projections rely on early pilot-scale data.",
                    "Regional financing coverage is concentrated in US and EU sources.",
                ],
            ),
            self.max_iterations_session_id: _demo_snapshot(
                session_id=self.max_iterations_session_id,
                status="max_iterations",
                report=_demo_report(partial=True),
                report_path="reports/grid-scale-batteries-2030-partial.md",
                finished_at=_DEMO_TIME,
                quality=True,
                limitations=[
                    (
                        "The configured iteration limit was reached before all "
                        "subtopics completed."
                    ),
                ],
            ),
            self.incomplete_session_id: _demo_snapshot(
                session_id=self.incomplete_session_id,
                status="incomplete",
                iteration=2,
                report=_demo_report(partial=True),
                report_path="reports/grid-scale-batteries-2030-incomplete.md",
                finished_at=_DEMO_TIME,
            ),
            self.failed_partial_session_id: _demo_snapshot(
                session_id=self.failed_partial_session_id,
                status="failed",
                report=_demo_report(partial=True),
                report_path="reports/grid-scale-batteries-2030-failed.md",
                errors=[failed_error],
                finished_at=_DEMO_TIME,
            ),
            self.no_telemetry_session_id: _demo_snapshot(
                session_id=self.no_telemetry_session_id,
                token_usage=None,
                trace_url=None,
            ),
            self.configuration_error_session_id: _demo_snapshot(
                session_id=self.configuration_error_session_id,
                status="failed",
                iteration=0,
                token_usage=None,
                trace_url=None,
                errors=[configuration_error],
                finished_at=_DEMO_TIME,
            ),
        }

    def _build_history(self) -> list[SessionHistoryEntry]:
        history_snapshots = [
            self._snapshots[self.completed_session_id],
            self._snapshots[self.running_session_id],
            self._snapshots[self.max_iterations_session_id],
            self._snapshots[self.incomplete_session_id],
            self._snapshots[self.failed_partial_session_id],
        ]
        extra_questions = [
            (
                "8" * 32,
                "Impact of vertical farming on urban food supply chains",
                "completed",
            ),
            (
                "9" * 32,
                "Semiconductor export controls and supply chain resilience",
                "max_iterations",
            ),
            (
                "a" * 32,
                "Long-term effects of remote work on commercial real estate",
                "incomplete",
            ),
            (
                "b" * 32,
                "Desalination economics under water scarcity scenarios",
                "failed",
            ),
            (
                "c" * 32,
                "Effects of AI-assisted diagnostics on radiology workflow efficiency",
                "completed",
            ),
        ]
        entries = [
            history_entry_from_snapshot(snapshot)
            for snapshot in history_snapshots
        ]
        for offset, (session_id, question, status) in enumerate(
            extra_questions, start=1
        ):
            base = _demo_snapshot(
                session_id=session_id,
                question=question,
                status=status,
                iteration=1,
                report=_demo_report(partial=status != "completed")
                if status != "failed"
                else None,
                finished_at=_DEMO_TIME - timedelta(days=offset),
            )
            entries.append(history_entry_from_snapshot(base))
        entries.sort(key=lambda entry: entry.started_at, reverse=True)
        self.history_reports.update(
            {
                self.completed_session_id: self._snapshots[
                    self.completed_session_id
                ].report
                or "",
                self.max_iterations_session_id: self._snapshots[
                    self.max_iterations_session_id
                ].report
                or "",
                self.incomplete_session_id: self._snapshots[
                    self.incomplete_session_id
                ].report
                or "",
                self.failed_partial_session_id: self._snapshots[
                    self.failed_partial_session_id
                ].report
                or "",
            }
        )
        return entries

    def _upsert_history(self, snapshot: UiSessionSnapshot) -> None:
        entry = history_entry_from_snapshot(snapshot)
        self._entries = [
            existing
            for existing in self._entries
            if existing.session_id != entry.session_id
        ]
        self._entries.insert(0, entry)


def make_outcome(
    *,
    session_id: str = "a" * 32,
    question: str = "Question",
    status: str = "completed",
    iteration: int = 1,
    max_iterations: int = 3,
    report_path: str | None = None,
    trace_url: str | None = None,
    report: str | None = "# Research report",
    errors: Sequence[ResearchError] = (),
    events: Sequence[ResearchEvent] = (),
    raw_findings: Sequence[Finding] = (),
    evaluated_sources: Sequence[ScoredSource] = (),
    verified_claims: Sequence[Claim] = (),
    token_usage: TokenUsage | None = None,
    tool_calls: Sequence[ToolCallSummary] = (),
) -> ResearchOutcome:
    """Build a real outcome without providers, graph execution, or I/O."""
    state = ResearchState(
        session_id=session_id,
        original_question=question,
        iteration=iteration,
        max_iterations=max_iterations,
        report=report,
        errors=list(errors),
        events=list(events),
        raw_findings=list(raw_findings),
        evaluated_sources=list(evaluated_sources),
        verified_claims=list(verified_claims),
    )
    return ResearchOutcome(
        session_id=session_id,
        question=question,
        status=status,
        state=state,
        trace_url=trace_url,
        report_path=report_path,
        token_usage=token_usage or TokenUsage(input_tokens=0, output_tokens=0),
        tool_calls=tuple(tool_calls),
    )


class GatedSyncRunner:
    """Publish events, signal start, then wait for explicit release."""

    def __init__(
        self,
        *,
        events: Sequence[ResearchEvent] = (),
        outcome_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self.events = list(events)
        self.outcome_kwargs = dict(outcome_kwargs or {})
        self.started = Event()
        self.release = Event()
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        question: str,
        session_id: str,
        event_handler: Callable[[ResearchEvent], None] | None = None,
        **kwargs: Any,
    ) -> ResearchOutcome:
        self.calls.append(
            {
                "question": question,
                "session_id": session_id,
                "event_handler": event_handler,
                **kwargs,
            }
        )
        if event_handler is not None:
            for event in self.events:
                event_handler(event)
        self.started.set()
        self.release.wait()
        return make_outcome(
            question=question,
            session_id=session_id,
            events=self.events,
            **self.outcome_kwargs,
        )


class FailingSyncRunner:
    """Raise one supplied exception after recording the runner invocation."""

    def __init__(
        self,
        error: Exception,
        *,
        events: Sequence[ResearchEvent] = (),
    ) -> None:
        self.error = error
        self.events = list(events)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        event_handler: Callable[[ResearchEvent], None] | None = None,
        **kwargs: Any,
    ) -> ResearchOutcome:
        self.calls.append(dict(kwargs))
        if event_handler is not None:
            for event in self.events:
                event_handler(event)
        raise self.error
