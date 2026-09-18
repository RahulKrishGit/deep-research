"""What one finished research session produced, in one object.

``GraphRun`` already carries the session id, the state, the status, and the
trace URL. Three things every front-end needs are recorded somewhere less
convenient: where the two artifacts were published, whether the terminal
quality gates accepted the report, and token totals — which live only in the
tracker's metric records. Deriving them once here keeps the CLI, the API, and
the UI from re-implementing the same archaeology three times.

Every field is read from typed state or a typed terminal event. Nothing here
parses the report Markdown, and nothing here reads report prose: the quality
verdict is ``graph.state.graph_quality_status``, the same pure decision the
router used, and the artifact paths come from the finalizer's own record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.graph.orchestrator import GraphRun
from deep_research.graph.state import graph_quality_status
from deep_research.observability import (
    MetricRecord,
    TokenUsage,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.request_budget import RequestBudgetSnapshot
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
)

# The terminal event the finalizer emits, and the only record of where the
# session's final artifacts were written. It carries *both* paths, each
# ``None`` for a write that failed, so a reader is never pointed at an
# earlier refinement pass's file. Emitted by
# ``graph.events.report_published_event``.
REPORT_WRITTEN_EVENT = "graph.report.published"

REPORT_PATH_METADATA_KEY = "report_path"
EVIDENCE_PATH_METADATA_KEY = "evidence_path"


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """How often one tool was called during a session, and how often it failed."""

    tool_name: str
    calls: int
    failures: int


def _terminal_artifact_path(
    stamped: str | None,
    events: Sequence[ResearchEvent],
    *,
    metadata_key: str,
) -> str | None:
    """One terminal artifact's path, from the state stamp and its own event.

    ``stamped`` is the value the finalizer wrote into state from the write
    that actually succeeded, so a state with no events still names its file.
    The *last* terminal publication record wins over it: a resumed run can
    publish more than once, and a record that carries no path (a failed write)
    clears the previous one — a caller must never be pointed at an earlier
    refinement pass's artifact.
    """
    path = stamped if isinstance(stamped, str) and stamped else None
    for event in events:
        if event.event_type != REPORT_WRITTEN_EVENT:
            continue
        candidate = event.metadata.get(metadata_key)
        path = candidate if isinstance(candidate, str) and candidate else None
    return path


def report_path_from_state(state: ResearchState) -> str | None:
    """The path of the session's final reader report, if one was published.

    A refinement pass writes nothing: only the terminal finalizer publishes,
    so the newest ``graph.report.published`` record is the session's own. A
    None value means the Markdown in ``state.report`` was never written to
    disk, whatever an earlier pass managed to save.
    """
    return _terminal_artifact_path(
        state.report_path,
        state.events,
        metadata_key=REPORT_PATH_METADATA_KEY,
    )


def evidence_path_from_state(state: ResearchState) -> str | None:
    """The path of the session's final evidence ledger, if one was published.

    The ledger and the reader report are written by two independent terminal
    writes, so this is ``None`` when the ledger write failed — never the path
    of an earlier pass's ledger. The ledger Markdown in
    ``state.report_evidence`` is authoritative either way.
    """
    return _terminal_artifact_path(
        state.evidence_path,
        state.events,
        metadata_key=EVIDENCE_PATH_METADATA_KEY,
    )


def tool_call_summaries(
    metrics: Sequence[MetricRecord],
    *,
    session_id: str | None = None,
) -> list[ToolCallSummary]:
    """Group one session's tool spans by tool name, alphabetically.

    Pass ``session_id`` to exclude spans the tracker accumulated for other
    sessions — a resumed run shares its tracker with the run that made the
    checkpoint, and mixing the two would double-count.
    """
    counts: dict[str, list[int]] = {}
    for metric in metrics:
        if not isinstance(metric, ToolMetric):
            continue
        if session_id is not None and metric.session_id != session_id:
            continue
        entry = counts.setdefault(metric.tool_name, [0, 0])
        entry[0] += 1
        if not metric.success:
            entry[1] += 1
    return [
        ToolCallSummary(tool_name=name, calls=calls, failures=failures)
        for name, (calls, failures) in sorted(counts.items())
    ]


def total_token_usage(
    metrics: Sequence[MetricRecord],
    *,
    session_id: str | None = None,
) -> TokenUsage:
    """Sum one session's LLM spans' token usage.

    Zero totals mean "no provider reported usage", which is exactly what a
    fully mocked run produces — callers render that as "not available"
    rather than as "zero tokens". Pass ``session_id`` to exclude spans the
    tracker accumulated for other sessions.
    """
    input_tokens = 0
    output_tokens = 0
    for metric in metrics:
        if not isinstance(metric, TokenUsageMetric):
            continue
        if session_id is not None and metric.session_id != session_id:
            continue
        input_tokens += metric.input_tokens
        output_tokens += metric.output_tokens
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    """Everything one research session produced, ready to render."""

    session_id: str
    question: str
    status: str
    state: ResearchState
    trace_url: str | None
    report_path: str | None
    token_usage: TokenUsage
    tool_calls: tuple[ToolCallSummary, ...]
    evidence_path: str | None = None
    """The file the evidence ledger was published under, or ``None``.

    Derived with ``report_path`` from the terminal publication, so a failed
    ledger write is ``None`` rather than an earlier pass's file.
    """

    request_budget_snapshots: tuple[RequestBudgetSnapshot, ...] = ()
    """The run's terminal per-provider attempt and token counts.

    The budget's own immutable snapshots, in its fixed ``deepseek``,
    ``openai``, ``tavily`` order, and empty when no budget was observed at
    all — an injected outcome, or a runtime that carries none. Never
    ``None``: a front-end renders "not recorded" rather than inventing a
    limit, and an absent ceiling stays absent instead of becoming a zero.
    """

    @property
    def report(self) -> str | None:
        """The report Markdown, authoritative whether or not it was written."""
        return self.state.report

    @property
    def errors(self) -> tuple[ResearchError, ...]:
        """Recoverable errors recorded during the session."""
        return tuple(self.state.errors)

    @property
    def failed(self) -> bool:
        """True when the run ended without a judged report.

        Two ways reach it: the graph halted on a non-recoverable failure, or
        the Critic's review never validated, so the report was never accepted.
        Both are ``failed`` rather than ``completed``-with-limitations, and
        ``accepted`` is ``False`` for both.
        """
        return self.status == "failed"

    @property
    def quality(self) -> ReportQualitySnapshot | None:
        """The quality snapshot the terminal gates judged, if one was taken."""
        return self.state.quality

    @property
    def quality_status(self) -> str:
        """The terminal quality verdict, from the decision the router used."""
        return graph_quality_status(self.state)

    @property
    def accepted(self) -> bool:
        """True when the terminal quality status accepted the report."""
        return self.quality_status == QUALITY_STATUS_ACCEPTED


def build_outcome(
    run: GraphRun,
    *,
    metrics: Sequence[MetricRecord],
    request_budget_snapshots: Sequence[RequestBudgetSnapshot] = (),
) -> ResearchOutcome:
    """Fold one graph run and the tracker's metrics into an outcome.

    ``request_budget_snapshots`` defaults to the empty tuple so every existing
    injected and unit caller stays source-compatible: an outcome built without
    a budget simply records none.
    """
    return ResearchOutcome(
        session_id=run.session_id,
        question=run.state.original_question,
        status=run.status,
        state=run.state,
        trace_url=run.trace_url,
        report_path=report_path_from_state(run.state),
        token_usage=total_token_usage(metrics, session_id=run.session_id),
        tool_calls=tuple(
            tool_call_summaries(metrics, session_id=run.session_id)
        ),
        evidence_path=evidence_path_from_state(run.state),
        request_budget_snapshots=tuple(request_budget_snapshots),
    )
