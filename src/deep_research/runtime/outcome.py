"""What one finished research session produced, in one object.

``GraphRun`` already carries the session id, the state, the status, and the
trace URL. Two things every front-end needs are recorded somewhere less
convenient: the report's path lives only in the Synthesizer's completion
event, and token totals live only in the tracker's metric records. Deriving
both once here keeps the CLI, the API, and the UI from re-implementing the
same archaeology three times.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import (
    MetricRecord,
    TokenUsage,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.utils.types import ResearchError, ResearchState

# The only event that records where a report was written. Emitted by
# ``agents.synthesizer.synthesis_completed_event``.
REPORT_WRITTEN_EVENT = "synthesizer.synthesis.completed"


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """How often one tool was called during a session, and how often it failed."""

    tool_name: str
    calls: int
    failures: int


def report_path_from_state(state: ResearchState) -> str | None:
    """The path of the most recently written report, if one was written.

    The *last* synthesis event wins: a refinement pass rewrites the report
    under a new filename, and the newest file is the one that matches
    ``state.report``.
    """
    path: str | None = None
    for event in state.events:
        if event.event_type != REPORT_WRITTEN_EVENT:
            continue
        candidate = event.metadata.get("output_path")
        if isinstance(candidate, str) and candidate:
            path = candidate
    return path


def tool_call_summaries(
    metrics: Sequence[MetricRecord],
) -> list[ToolCallSummary]:
    """Group the run's tool spans by tool name, alphabetically."""
    counts: dict[str, list[int]] = {}
    for metric in metrics:
        if not isinstance(metric, ToolMetric):
            continue
        entry = counts.setdefault(metric.tool_name, [0, 0])
        entry[0] += 1
        if not metric.success:
            entry[1] += 1
    return [
        ToolCallSummary(tool_name=name, calls=calls, failures=failures)
        for name, (calls, failures) in sorted(counts.items())
    ]


def total_token_usage(metrics: Sequence[MetricRecord]) -> TokenUsage:
    """Sum every LLM span's token usage.

    Zero totals mean "no provider reported usage", which is exactly what a
    fully mocked run produces — callers render that as "not available"
    rather than as "zero tokens".
    """
    input_tokens = 0
    output_tokens = 0
    for metric in metrics:
        if isinstance(metric, TokenUsageMetric):
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
        """True when the graph halted on a non-recoverable failure."""
        return self.status == "failed"


def build_outcome(
    run: GraphRun,
    *,
    metrics: Sequence[MetricRecord],
) -> ResearchOutcome:
    """Fold one graph run and the tracker's metrics into an outcome."""
    return ResearchOutcome(
        session_id=run.session_id,
        question=run.state.original_question,
        status=run.status,
        state=run.state,
        trace_url=run.trace_url,
        report_path=report_path_from_state(run.state),
        token_usage=total_token_usage(metrics),
        tool_calls=tuple(tool_call_summaries(metrics)),
    )
