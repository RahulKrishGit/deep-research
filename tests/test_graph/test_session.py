"""Tests for the session runner: trace metadata, checkpointing, resume."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.errors import GraphResumeError
from deep_research.graph.orchestrator import (
    build_checkpointer,
    compile_research_graph,
    resume_research_graph,
    run_research_graph,
)
from deep_research.graph.state import (
    DEFAULT_MAX_EXTRA_PASSES,
    PLANNER_NODE,
    ResearchGraphState,
    dump_state,
    is_halted,
)
from deep_research.observability import AgentMetric, Tracker
from deep_research.observability.context import LangSmithRuntimeConfig
from deep_research.providers import ProviderConfigurationError
from deep_research.request_budget import (
    RequestAttemptLimitError,
    RequestBudgetSnapshot,
)
from deep_research.utils.config import GraphConfig
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchError,
    ResearchState,
    ReviewDefect,
)
from tests.graph_fakes import (
    FakeAgent,
    FakePublisher,
    FakeReviewer,
    fake_report_review,
    fake_research_agents,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
    fake_target,
    fake_writer_update,
    verified_pass,
)
from tests.test_observability_tracker import RecordingTraceFactory

QUESTION = "How mature is quantum error correction?"


class SpanningFakeAgent(FakeAgent):
    """A fake that opens the agent span a real agent would.

    Proves the session span's trace context reaches a LangGraph node — the
    contextvar has to survive whatever task LangGraph runs the node in, and
    ``Tracker.agent_span`` raises without an active session span.
    """

    def __init__(self, name: str, tracker: Tracker) -> None:
        super().__init__(name)
        self._tracker = tracker

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async with self._tracker.agent_span(self.name):
            return await super().run(state)


class RefusingReviewer:
    """A reviewer whose provider refused an attempt past the declared ceiling.

    The refusal is the one failure the production ``ReportReviewer`` cannot
    translate into a review status: the request budget raises it before the
    transport is asked, and the provider and tool layers deliberately re-raise
    it rather than converting it. The terminal review node therefore has to
    record it, because the report it was handed is already composed — which is
    why the ceiling is tripped here on purpose.
    """

    def __init__(self, error: RequestAttemptLimitError) -> None:
        self._error = error
        self.packets: list[object] = []
        self.review_records: tuple[ResearchError, ...] = ()

    @property
    def calls(self) -> int:
        return len(self.packets)

    async def review(
        self,
        packet: object,
        *,
        previous: Any | None = None,
    ) -> Any:
        self.packets.append(packet)
        raise self._error


class EmptyValuesGraph:
    """A compiled-graph stand-in whose values stream is always empty.

    LangGraph can legally yield no snapshots when a resumed thread has no
    pending nodes, so the empty-stream boundary has to be pinned by tests:
    a terminal checkpoint is the only empty stream a resume may fall back
    to. ``aget_state`` serves the scripted snapshot each test supplies.
    """

    def __init__(self, *, snapshot: Any | None = None) -> None:
        self._snapshot = snapshot

    async def astream(
        self,
        channel: ResearchGraphState | None,
        config: dict[str, Any],
        *,
        stream_mode: str | None = None,
    ) -> AsyncIterator[ResearchGraphState]:
        """Yield nothing, ever: a stream with zero snapshots."""
        return
        yield  # pragma: no cover

    async def ainvoke(
        self,
        channel: ResearchGraphState | None,
        config: dict[str, Any],
    ) -> ResearchGraphState:
        raise AssertionError("the empty-stream graph must never be invoked")

    async def aget_state(self, config: dict[str, Any]) -> Any:
        if self._snapshot is None:
            raise ValueError("a resumable graph needs a checkpointer")
        return self._snapshot


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


def _two_target_topic():
    """One planned topic with one answerable target and one still owed."""
    return fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )


def _owed_agents(**overrides: object):
    """A run that owes one target per pass, so the loop really turns.

    The default pass answers topic-01-target-01 only, so every pass ends with
    a missing required target and buys the next one until the ceiling is spent
    — which is what makes a multi-pass run reachable at all.
    """
    defaults: dict[str, object] = {
        "planner": FakeAgent("planner", [{"sub_topics": [_two_target_topic()]}]),
        "report_writer": FakeAgent(
            "report_writer", [], update_factory=fake_writer_update
        ),
        "report_reviewer": FakeReviewer(),
    }
    defaults.update(overrides)
    return fake_research_agents(**defaults)


@pytest.mark.asyncio
async def test_a_run_returns_the_final_state_and_its_status(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        max_extra_passes=2,
    )

    assert run.session_id == "session-1"
    assert run.status == "completed"
    assert run.state.report
    assert run.state.original_question == QUESTION
    assert run.state.max_extra_passes == 2


@pytest.mark.asyncio
async def test_a_run_brackets_itself_with_session_events(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    types = _event_types(run.state)

    assert types[0] == "graph.session.started"
    assert types[-1] == "graph.session.completed"
    assert run.state.events[-1].metadata["status"] == "completed"
    assert run.state.events[-1].metadata["has_report"] is True


@pytest.mark.asyncio
async def test_a_run_carries_the_callers_memory_context_to_the_planner(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )

    assert agents.planner.calls[0].memory_context.suggested_strategies == [
        "start broad"
    ]


@pytest.mark.asyncio
async def test_a_run_attaches_session_metadata_and_routes_to_the_trace() -> None:
    trace_factory = RecordingTraceFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=trace_factory,
    )
    first_source = fake_scored_source("https://example.org/a")
    second_source = fake_scored_source("https://example.org/b")
    one = verified_pass()
    agents = fake_research_agents(
        # ``evaluated_sources`` and ``verified_findings`` are canonical
        # snapshots, so each pass emits the whole list. The trace must then
        # report the two sources and the one verified finding the second pass
        # carried — not three and two, which is what appending pass 1 to pass 2
        # would produce.
        source_evaluator=FakeAgent(
            "source_evaluator",
            [
                {"evaluated_sources": [first_source]},
                {"evaluated_sources": [first_source, second_source]},
            ],
        ),
        evidence_verifier=FakeAgent(
            "evidence_verifier",
            [
                {"verified_findings": [one.finding]},
                {"verified_findings": [one.finding]},
            ],
        ),
        planner=FakeAgent("planner", [{"sub_topics": [_two_target_topic()]}]),
        report_writer=FakeAgent(
            "report_writer", [], update_factory=fake_writer_update
        ),
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        max_extra_passes=1,
    )

    completed = [
        event
        for event in tracker.events
        if event.event_type == "observability.span.completed"
        and event.metadata["span_name"] == "research.session"
    ]
    assert len(completed) == 1
    assert completed[0].metadata["session_id"] == "session-1"
    assert completed[0].metadata["success"] is True

    # The whole trace surface the spec asks for lands on the session run:
    # identity, final status, the route decisions, and every count. A wrong
    # key or a dropped field fails here rather than shipping silently.
    session_run = trace_factory.managers[0].run
    assert run.trace_url == session_run.trace_url
    assert session_run.end_calls[-1]["outputs"] == {
        "session_id": "session-1",
        "status": "completed",
        "route_reason": "report_accepted",
        "route_decisions": ["extra_pass_requested", "report_accepted"],
        "iteration": 1,
        "max_extra_passes": 1,
        "extra_pass_target_count": 1,
        "sub_topic_count": 1,
        "finding_count": 2,
        "verified_finding_count": 1,
        "source_count": 2,
        "has_report": True,
        "error_count": 0,
    }


@pytest.mark.asyncio
async def test_every_node_gets_its_own_agent_span(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=SpanningFakeAgent("planner", tracker),
        researcher=SpanningFakeAgent("researcher", tracker),
        source_evaluator=SpanningFakeAgent("source_evaluator", tracker),
        evidence_verifier=SpanningFakeAgent("evidence_verifier", tracker),
        report_writer=SpanningFakeAgent("report_writer", tracker),
    )

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    spanned = {
        metric.agent_name
        for metric in tracker.metrics
        if isinstance(metric, AgentMetric)
    }
    assert spanned == {
        "planner",
        "researcher",
        "source_evaluator",
        "evidence_verifier",
        "report_writer",
    }


@pytest.mark.asyncio
async def test_a_checkpointed_session_can_be_resumed_by_its_session_id(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.researcher.calls)]

    resumed = await resume_research_graph(
        graph=graph, tracker=tracker, session_id="session-1"
    )

    assert resumed.state.report == first.state.report
    assert resumed.state.original_question == QUESTION
    assert resumed.status == "completed"
    # A finished session replays its checkpoint; no agent runs again.
    assert [len(agents.planner.calls), len(agents.researcher.calls)] == call_counts


@pytest.mark.asyncio
async def test_a_resume_uses_the_checkpointed_extra_pass_budget(
    tracker: Tracker,
) -> None:
    agents = _owed_agents(
        researcher=FakeAgent(
            "researcher",
            [RuntimeError("crash"), verified_pass().update()],
        )
    )
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )

    # A mid-run crash leaves the thread checkpointed but unfinished. The
    # budget the session started with must survive in the checkpoint, or a
    # resume runs under the runner's own default instead of the session's.
    with pytest.raises(RuntimeError, match="crash"):
        await run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            max_extra_passes=2,
        )

    resumed = await resume_research_graph(
        graph=graph, tracker=tracker, session_id="session-1"
    )

    assert resumed.state.max_extra_passes == 2
    assert resumed.state.iteration == 2
    assert resumed.status == "completed"
    # The crashed pass re-runs on resume, and every pass the budget allows
    # runs: four researcher calls in all.
    assert len(agents.researcher.calls) == 4


@pytest.mark.asyncio
async def test_the_started_event_reads_checkpointing_off_the_compiled_graph(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.state.events[0].metadata["checkpointing"] is True


@pytest.mark.asyncio
async def test_a_graph_without_a_checkpointer_never_claims_resumability(
    tracker: Tracker,
) -> None:
    run = await run_research_graph(
        graph=compile_research_graph(fake_research_agents()),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.state.events[0].metadata["checkpointing"] is False


@pytest.mark.asyncio
async def test_resuming_an_unknown_session_is_refused(tracker: Tracker) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    with pytest.raises(GraphResumeError, match="no checkpoint"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="never-run"
        )


@pytest.mark.asyncio
async def test_resuming_without_a_checkpointer_is_refused(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    with pytest.raises(GraphResumeError, match="checkpointer"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="session-1"
        )


@pytest.mark.asyncio
async def test_a_completed_checkpoint_resumes_with_a_handler_without_rerunning_nodes(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.researcher.calls)]

    received = []
    resumed = await resume_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert resumed.status == "completed"
    assert resumed.state.report == first.state.report
    # A finished session replays its checkpoint; no agent runs again.
    assert [len(agents.planner.calls), len(agents.researcher.calls)] == call_counts
    # Every checkpointed event is delivered once, in state order, and the
    # runner's single completion event closes the stream.
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"
    assert (
        sum(
            event.event_type == "graph.session.completed"
            for event in received
        )
        == 1
    )


@pytest.mark.asyncio
async def test_a_zero_snapshot_terminal_resume_publishes_the_checkpoint(
    tracker: Tracker,
) -> None:
    """The acceptance case: an empty values stream on a terminal checkpoint.

    A resumed thread with no pending nodes can yield zero snapshots. The
    checkpoint itself is then the run's only result: it must be returned
    and its events published exactly once, in state order, with one
    completion event last — never a RuntimeError and never a node rerun.
    """
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.researcher.calls)]
    # The durable checkpoint holds everything except the runner-appended
    # completion event, and has no pending nodes.
    checkpointed = first.state.model_copy(
        update={"events": first.state.events[:-1]}
    )
    empty = EmptyValuesGraph(
        snapshot=SimpleNamespace(values=dump_state(checkpointed), next=())
    )

    received = []
    resumed = await resume_research_graph(
        graph=empty,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert [len(agents.planner.calls), len(agents.researcher.calls)] == call_counts
    assert resumed.status == "completed"
    assert resumed.state.report == first.state.report
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"
    assert (
        sum(
            event.event_type == "graph.session.completed"
            for event in received
        )
        == 1
    )


@pytest.mark.asyncio
async def test_an_empty_values_stream_on_a_fresh_run_is_still_an_error(
    tracker: Tracker,
) -> None:
    with pytest.raises(
        RuntimeError, match="research graph produced no state"
    ):
        await run_research_graph(
            graph=EmptyValuesGraph(),
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            event_handler=lambda event: None,
        )


@pytest.mark.asyncio
async def test_an_empty_values_stream_on_a_nonterminal_resume_is_still_an_error(
    tracker: Tracker,
) -> None:
    checkpointed = SimpleNamespace(
        values=dump_state(fake_research_state(max_extra_passes=2)),
        next=(PLANNER_NODE,),
    )

    with pytest.raises(
        RuntimeError, match="research graph produced no state"
    ):
        await resume_research_graph(
            graph=EmptyValuesGraph(snapshot=checkpointed),
            tracker=tracker,
            session_id="session-1",
            event_handler=lambda event: None,
        )


@pytest.mark.asyncio
async def test_an_unfinished_resume_streams_events_in_order_without_duplicates(
    tracker: Tracker,
) -> None:
    agents = _owed_agents(
        researcher=FakeAgent(
            "researcher",
            [RuntimeError("crash"), verified_pass().update()],
        )
    )
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    with pytest.raises(RuntimeError, match="crash"):
        await run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            max_extra_passes=2,
        )

    received = []
    resumed = await resume_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert resumed.status == "completed"
    # The crashed pass re-runs on resume, exactly as without a handler.
    assert len(agents.researcher.calls) == 4
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"


@pytest.mark.asyncio
async def test_a_failed_run_still_returns_its_state(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=FakeAgent("planner", [AgentConfigurationError("bad wiring")])
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.status == "failed"
    assert run.state.original_question == QUESTION
    assert run.state.events[-1].metadata["status"] == "failed"


def _deepseek_ceiling_refusal() -> RequestAttemptLimitError:
    """One refusal, as the budget raises it: static message plus snapshot."""
    return RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="deepseek",
            attempts=4,
            ceiling=4,
            effective_limit=4,
            input_tokens=0,
            output_tokens=0,
        )
    )


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (
            _deepseek_ceiling_refusal(),
            "report_review_request_attempt_limit",
        ),
        (
            ProviderConfigurationError(
                "the deepseek provider has no api key configured"
            ),
            "report_review_provider_unconfigured",
        ),
    ],
)
@pytest.mark.asyncio
async def test_an_unaskable_reviewer_still_publishes_the_report(
    tracker: Tracker,
    refusal: Exception,
    reason: str,
) -> None:
    """A refusal at the reviewer is an unmade judgement, never a lost report.

    Two failures reach this node instead of a judgement: the run's declared
    request ceiling, spent, and a provider this run cannot reach at all. Both
    are raised before any review exists — the provider and tool layers
    deliberately re-raise them rather than translating them — and the report is
    already composed by the writer node before this one runs, so publishing it
    spends nothing.

    Left unhandled, the refusal escaped ``run_research_graph`` as an uncaught
    error: the run published nothing although its report was finished, the CLI
    died on a traceback with the exit code its own contract reserves for a
    configuration error, and the API recorded the same event as a failed
    session.
    """
    publisher = FakePublisher()
    reviewer = RefusingReviewer(refusal)
    agents = fake_research_agents(
        publisher=publisher, report_reviewer=reviewer
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert reviewer.calls == 1
    # The finished report is published, and nothing about it is claimed to be
    # judged: the run is incomplete, not failed, and not accepted.
    assert len(publisher.written_paths) == 3
    assert run.state.report
    assert run.status == "incomplete"
    assert run.state.report_review is not None
    assert run.state.report_review.status == "incomplete"
    assert run.state.report_review.mean_score is None
    assert not is_halted(run.state)
    assert [
        error.error_type for error in run.state.errors
    ] == ["graph_report_review_unavailable"]
    assert run.state.errors[0].recoverable is True
    assert run.state.errors[0].details == {
        "review_status": "incomplete",
        "reason": reason,
    }
    # Neither the refusal's own text nor its numbers reach the record, exactly
    # as the enumerated halt paths keep them out.
    serialized = json.dumps(run.state.model_dump(mode="json"))
    assert str(refusal) not in serialized
    assert "input_tokens" not in serialized
    assert run.state.quality is not None
    assert run.state.quality.semantic_review_status == "incomplete"


@pytest.mark.asyncio
async def test_a_material_defect_re_drafts_the_report_once(tracker: Tracker) -> None:
    """A judged report its own reviewer refused is re-drafted, once, then published.

    The second draft is the whole point: the reviewer's defects name what is
    wrong with a report the evidence already supports, so the run cannot fix
    them with research and must not publish them unanswered. The bound is the
    other half — exactly one re-run, so a reviewer that keeps naming defects
    cannot loop the writer — and the writer composing the second draft is
    handed the review that asked for it, which is where the defects it must
    address come from.
    """
    defect = ReviewDefect(
        defect_id="review-01",
        kind="contradiction",
        severity="major",
        statement_ids=["S001"],
        problem="The summary contradicts the findings below it.",
    )
    reviewer = FakeReviewer(
        [
            fake_report_review(
                defects=[defect],
                reviewed_statement_ids=("S001",),
            )
        ]
    )
    writer = FakeAgent(
        "report_writer", [], update_factory=fake_writer_update
    )
    publisher = FakePublisher()
    agents = fake_research_agents(
        publisher=publisher, report_reviewer=reviewer, report_writer=writer
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    # One re-run: the writer ran twice, and never three times.
    assert len(writer.calls) == 2
    first, second = writer.calls
    assert first.writer_redrafts == 0
    assert second.writer_redrafts == 1
    assert second.report_review is not None
    assert [
        defect.defect_id for defect in second.report_review.material_defects
    ] == ["review-01"]
    # The re-run is recorded, and the run still publishes its artifacts.
    assert [
        event.event_type
        for event in run.state.events
        if event.event_type == "graph.report.redraft_requested"
    ] == ["graph.report.redraft_requested"]
    assert len(publisher.written_paths) == 3
    assert run.state.writer_redrafts == 1


def test_the_graph_config_default_matches_the_graph_module_default() -> None:
    assert GraphConfig().max_extra_passes == DEFAULT_MAX_EXTRA_PASSES
