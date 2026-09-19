"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.graph.errors import GRAPH_ERROR_REASONS, GraphConfigurationError
from deep_research.graph.nodes import (
    REPAIR_NODES,
    GraphNode,
    agent_node,
    critic_node,
    finalize_report_node,
    invalidation_update,
    refine_node,
    refinement_targets_for,
    route_after_critic,
    route_after_refine,
    route_refinement,
    synthesizer_node,
)
from deep_research.graph.state import (
    ROUTE_END,
    ROUTE_FINALIZE,
    ROUTE_REFINE,
    ResearchGraphState,
    dump_state,
    is_halted,
    load_state,
    progress_snapshot,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ProviderConfigurationError
from deep_research.request_budget import (
    ProviderCategory,
    RequestAttemptLimitError,
    RequestBudgetSnapshot,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    REPAIR_ACTIONS,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    RefinementTarget,
    ReportQualitySnapshot,
    ResearchError,
    ResearchState,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    FakeAgent,
    FakePublisher,
    fake_claim,
    fake_critique,
    fake_finding,
    fake_quality,
    fake_reader_composition,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
    halting_error,
)
from tests.research_fakes import FakeMemory, synthesizer_tools


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


@pytest.mark.asyncio
async def test_a_node_merges_its_agents_update_and_brackets_it_with_events(
) -> None:
    agent = FakeAgent("planner", [{"sub_topics": [fake_sub_topic()]}])
    node = agent_node(agent)

    result = await node(dump_state(fake_research_state()))
    state = load_state(result)

    assert [topic.title for topic in state.sub_topics] == ["Error correction"]
    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
    ]
    assert state.events[-1].metadata["node"] == "planner"
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_an_agent_is_handed_state_that_already_records_its_start() -> None:
    agent = FakeAgent("researcher", [{"raw_findings": [fake_finding()]}])

    await agent_node(agent)(dump_state(fake_research_state()))

    # The agent is handed the state *after* graph.node.started is recorded,
    # so an agent that reads state.events sees its own node's start.
    assert _event_types(agent.calls[0]) == ["graph.node.started"]


@pytest.mark.asyncio
async def test_a_node_name_may_be_overridden_for_the_graph() -> None:
    agent = FakeAgent("planner")

    result = await agent_node(agent, node_name="scout")(
        dump_state(fake_research_state())
    )

    assert load_state(result).events[0].source == "graph.scout"


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_stays_in_state_and_does_not_halt(
) -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agent = FakeAgent("researcher", [{"errors": [recoverable]}])

    result = await agent_node(agent)(dump_state(fake_research_state()))
    state = load_state(result)

    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]
    assert not is_halted(state)
    assert state.events[-1].metadata["error_count"] == 1


@pytest.mark.asyncio
async def test_a_non_recoverable_agent_error_still_does_not_halt_the_graph(
) -> None:
    # Agents record provider outages as non-recoverable. A research pass is
    # expected to survive one; only enumerated graph errors halt.
    outage = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )
    agent = FakeAgent("critic", [{"errors": [outage]}])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert not is_halted(state)


@pytest.mark.parametrize(
    ("raised", "expected_type"),
    [
        (
            AgentConfigurationError("bad scratchpad"),
            "graph_agent_configuration_error",
        ),
        (
            ProviderConfigurationError("no api key"),
            "graph_provider_configuration_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_configuration_failure_halts_the_run_with_state_intact(
    raised: Exception, expected_type: str
) -> None:
    agent = FakeAgent("planner", [raised])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert [error.error_type for error in state.errors] == [expected_type]
    assert state.errors[0].details == {"exception_type": type(raised).__name__}
    assert is_halted(state)
    assert state.original_question == "How mature is quantum error correction?"


@pytest.mark.asyncio
async def test_planning_failure_preserves_safe_problems_without_hostile_text() -> None:
    hostile_exception_text = "hostile exception text from provider response"
    hostile_provider_sentinel = "hostile-provider-sentinel"
    raised = PlanningError(
        hostile_exception_text,
        problems=("the plan contains no sub-topics",),
        operation=hostile_provider_sentinel,
    )
    agent = FakeAgent("planner", [raised])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    recorded = state.errors[0]
    assert recorded.error_type == "graph_planning_failed"
    assert recorded.message == GRAPH_ERROR_REASONS["graph_planning_failed"]
    assert recorded.details == {
        "exception_type": "PlanningError",
        "problems": ["the plan contains no sub-topics"],
    }
    assert isinstance(recorded.details["problems"], list)
    assert hostile_exception_text not in recorded.message
    assert hostile_provider_sentinel not in recorded.message
    assert hostile_exception_text not in str(recorded.details)
    assert hostile_provider_sentinel not in str(recorded.details)
    assert is_halted(state)


def _request_attempt_refusal(
    *,
    provider: ProviderCategory = "tavily",
    attempts: int = 5,
    ceiling: int | None = 5,
    effective_limit: int | None = 5,
) -> RequestAttemptLimitError:
    """One refusal, as the budget raises it: static message plus snapshot."""
    return RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider=provider,
            attempts=attempts,
            ceiling=ceiling,
            effective_limit=effective_limit,
            input_tokens=0,
            output_tokens=0,
        )
    )


async def _run_node(
    node: GraphNode, channel: ResearchGraphState
) -> ResearchState:
    """Drive one node, reporting an escaped refusal as a plain failure.

    ``agent_node`` is required to *record* a spent request budget as an
    enumerated halt. Before that behaviour existed the refusal escaped the node
    entirely, so it is reported here as a readable failure rather than as a
    traceback from the awaited call.
    """
    try:
        result = await node(channel)
    except RequestAttemptLimitError as escaped:
        pytest.fail(
            "the request attempt limit refusal escaped the node instead of "
            f"halting the run: {escaped!r}"
        )
    return load_state(result)


@pytest.mark.asyncio
async def test_a_request_attempt_limit_refusal_halts_with_safe_snapshot_details(
) -> None:
    refusal = _request_attempt_refusal()
    agent = FakeAgent("researcher", [refusal])

    state = await _run_node(agent_node(agent), dump_state(fake_research_state()))

    assert [error.error_type for error in state.errors] == [
        "graph_request_attempt_limit_exceeded"
    ]
    recorded = state.errors[0]
    # Exact equality, so a sixth key cannot slip into a state record the CLI,
    # the API stream, and the UI all render.
    assert recorded.details == {
        "exception_type": "RequestAttemptLimitError",
        "provider": "tavily",
        "attempts": 5,
        "ceiling": 5,
        "effective_limit": 5,
    }
    assert recorded.message == GRAPH_ERROR_REASONS[
        "graph_request_attempt_limit_exceeded"
    ]
    assert recorded.source == "graph.researcher"
    assert recorded.recoverable is False
    assert is_halted(state)

    # The refusal's own text is never recorded, and neither are the token
    # totals the snapshot also carries.
    serialized = json.dumps(recorded.model_dump(mode="json"))
    assert str(refusal) not in serialized
    assert "Request attempt limit reached" not in serialized
    assert "input_tokens" not in serialized


@pytest.mark.asyncio
async def test_a_request_attempt_limit_refusal_with_a_zero_limit_records_the_zero(
) -> None:
    """A zero effective limit is a declared refusal, and is recorded as ``0``."""
    refusal = _request_attempt_refusal(
        provider="openai", attempts=0, ceiling=4, effective_limit=0
    )

    state = await _run_node(
        agent_node(FakeAgent("fact_checker", [refusal])),
        dump_state(fake_research_state()),
    )

    assert state.errors[0].details == {
        "exception_type": "RequestAttemptLimitError",
        "provider": "openai",
        "attempts": 0,
        "ceiling": 4,
        "effective_limit": 0,
    }
    assert is_halted(state)


@pytest.mark.asyncio
async def test_a_request_attempt_limit_refusal_is_never_retried_or_converted() -> None:
    """One pass, one graph-owned record: never a retry, provider, or tool error."""
    refusal = _request_attempt_refusal(
        provider="deepseek", attempts=7, ceiling=7, effective_limit=7
    )
    agent = FakeAgent("synthesizer", [refusal])

    state = await _run_node(agent_node(agent), dump_state(fake_research_state()))

    assert len(agent.calls) == 1
    assert [error.error_type for error in state.errors] == [
        "graph_request_attempt_limit_exceeded"
    ]
    # Not converted into any other enumerated failure, and never attributed to
    # a recoverable agent or tool taxonomy.
    assert {
        "graph_agent_configuration_error",
        "graph_provider_configuration_error",
        "graph_invalid_agent_state",
        "graph_planning_failed",
        "agent_tool_failed",
    }.isdisjoint({error.error_type for error in state.errors})
    assert [error.source for error in state.errors] == ["graph.synthesizer"]
    # The pass is left unfinished rather than completed.
    assert _event_types(state) == ["graph.node.started"]


@pytest.mark.asyncio
async def test_an_unexpected_agent_exception_is_not_swallowed() -> None:
    agent = FakeAgent("planner", [RuntimeError("a defect, not an outcome")])

    with pytest.raises(RuntimeError, match="a defect"):
        await agent_node(agent)(dump_state(fake_research_state()))


@pytest.mark.asyncio
async def test_an_update_the_state_model_rejects_halts_the_run() -> None:
    agent = FakeAgent("researcher", [{"iteration": 2}])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_a_halted_run_skips_every_later_node() -> None:
    agent = FakeAgent("synthesizer", [{"report": "# never written"}])

    result = await agent_node(agent)(
        dump_state(fake_research_state(errors=[halting_error()]))
    )
    state = load_state(result)

    assert agent.calls == []
    assert state.report is None
    assert _event_types(state) == ["graph.node.skipped"]


@pytest.mark.asyncio
async def test_the_critic_node_records_the_route_it_produced() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(iteration=0, max_iterations=3))
    )
    state = load_state(result)

    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
        "graph.route.decided",
    ]
    assert state.events[-1].metadata == {
        "destination": ROUTE_REFINE,
        "reason": "refinement_requested",
        "iteration": 0,
        "max_iterations": 3,
        "should_continue": True,
    }
    assert route_after_critic(result) == ROUTE_REFINE


@pytest.mark.asyncio
async def test_the_critic_node_records_the_bound_overriding_the_critic() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(iteration=2, max_iterations=2))
    )
    state = load_state(result)

    assert state.events[-1].metadata["reason"] == "max_iterations_reached"
    assert state.events[-1].metadata["should_continue"] is True
    assert route_after_critic(result) == ROUTE_FINALIZE


@pytest.mark.asyncio
async def test_a_halted_critic_node_still_records_a_route() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(errors=[halting_error()]))
    )

    assert agent.calls == []
    assert load_state(result).events[-1].metadata["reason"] == "halted"
    assert route_after_critic(result) == ROUTE_END


@pytest.mark.asyncio
async def test_the_refinement_hop_advances_the_macro_iteration() -> None:
    result = await refine_node(
        dump_state(fake_research_state(iteration=0, max_iterations=3))
    )
    state = load_state(result)

    assert state.iteration == 1
    assert _event_types(state) == ["graph.refinement.started"]
    assert state.events[0].metadata == {"iteration": 1, "max_iterations": 3}


@pytest.mark.asyncio
async def test_the_refinement_hop_refuses_to_spend_a_budget_it_lacks() -> None:
    state = load_state(
        await refine_node(
            dump_state(fake_research_state(iteration=2, max_iterations=2))
        )
    )

    assert state.iteration == 2
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_route"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_the_refinement_hop_skips_a_halted_run() -> None:
    state = load_state(
        await refine_node(dump_state(fake_research_state(errors=[halting_error()])))
    )

    assert state.iteration == 0
    assert _event_types(state) == ["graph.node.skipped"]


def _composed_state() -> ResearchState:
    """One pass's incoming state: a canonical snapshot to compose from."""
    return fake_research_state(
        sub_topics=[fake_sub_topic()],
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
    )


def _composing_update(state: ResearchState) -> dict[str, object]:
    """The update a real Synthesizer returns: both artifacts and the composition."""
    return {
        "report": "# Reader report",
        "report_evidence": "# Evidence ledger",
        "composition": fake_reader_composition(state),
    }


@pytest.mark.asyncio
async def test_the_synthesizer_node_scores_the_artifacts_it_just_composed() -> None:
    """The quality pass judges the state this pass produced, not the last one.

    ``compute_report_quality`` reads ``state.report`` and
    ``state.report_evidence`` for its missing-artifact gates, so it can only
    run once the agent's update has been merged. Judging the composition
    against the *incoming* state would report every first pass as having no
    artifacts at all.
    """
    agent = FakeAgent("synthesizer", [], update_factory=_composing_update)

    result = await synthesizer_node(agent)(dump_state(_composed_state()))
    state = load_state(result)

    assert state.quality is not None
    assert state.quality.hard_failures == []
    assert state.quality.duplicate_claims == 0
    assert state.quality.duplicate_source_rows == 0
    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
        "graph.quality.assessed",
    ]
    assert state.events[-1].metadata["hard_failures"] == []


@pytest.mark.asyncio
async def test_the_synthesizer_node_records_every_hard_failure_it_found() -> None:
    agent = FakeAgent("synthesizer", [], update_factory=_composing_update)
    duplicated = _composed_state().model_copy(
        update={
            "evaluated_sources": [
                fake_scored_source("https://example.org/a"),
                fake_scored_source("https://example.org/a"),
            ]
        }
    )

    state = load_state(await synthesizer_node(agent)(dump_state(duplicated)))

    assert state.quality is not None
    assert state.quality.hard_failures == ["duplicate_source_rows"]
    assert state.events[-1].metadata["hard_failures"] == ["duplicate_source_rows"]


@pytest.mark.asyncio
async def test_a_compositionless_pass_records_no_quality_verdict() -> None:
    """Nothing typed to judge means no snapshot, never a clean one."""
    agent = FakeAgent("synthesizer", [{"report": "# Reader report"}])

    state = load_state(
        await synthesizer_node(agent)(dump_state(fake_research_state()))
    )

    assert state.quality is None
    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
    ]


@pytest.mark.asyncio
async def test_a_halted_synthesizer_pass_is_not_quality_graded() -> None:
    """A halted pass composed nothing, so nothing may be graded under it.

    The incoming state already carries an *earlier* pass's composition, which
    is exactly what a halt on iteration 1 or later looks like. Re-scoring it
    here would emit ``graph.quality.assessed`` labelled with the current
    iteration, asserting a verdict for a pass that never composed anything.
    """
    agent = FakeAgent("synthesizer", [{"report": "# never written"}])
    earlier = fake_research_state(
        sub_topics=[fake_sub_topic()],
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
        report="# earlier reader report",
        report_evidence="# earlier evidence ledger",
    )
    halted = earlier.model_copy(
        update={
            "composition": fake_reader_composition(earlier),
            "errors": [halting_error()],
            "iteration": 1,
        }
    )

    state = load_state(await synthesizer_node(agent)(dump_state(halted)))

    assert agent.calls == []
    assert state.composition is not None
    assert state.quality is None
    assert _event_types(state) == ["graph.node.skipped"]


# --- the terminal finalizer ---------------------------------------------------


def _finalized_state(
    *,
    quality: ReportQualitySnapshot | None,
    report: str = "# Reader report",
    report_evidence: str = "# Evidence ledger",
    iteration: int = 0,
    max_iterations: int = 3,
    should_continue: bool = False,
    errors: list[ResearchError] | None = None,
) -> ResearchState:
    """A run that has finished its Critic pass and is ready to be finalized."""
    return fake_research_state(
        sub_topics=[fake_sub_topic()],
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
        report=report,
        report_evidence=report_evidence,
        quality=quality,
        critique=fake_critique(
            should_continue=should_continue,
            score=9 if not should_continue else 4,
        ),
        iteration=iteration,
        max_iterations=max_iterations,
        errors=errors or [],
    )


@pytest.mark.asyncio
async def test_the_finalizer_publishes_both_artifacts_exactly_once() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=fake_quality()))
    )
    state = load_state(result)

    assert publisher.report_writes == 2
    assert state.report_path == "report-session-1-0.md"
    assert state.evidence_path == "report-session-1-0-evidence.md"
    assert publisher.written_paths == [
        "report-session-1-0.md",
        "report-session-1-0-evidence.md",
    ]
    assert publisher.document_named("report-session-1-0.md")[1] == (
        "# Reader report"
    )
    assert publisher.document_named("-evidence.md")[1] == "# Evidence ledger"
    assert not state.errors
    published = state.events[-2]
    assert published.event_type == "graph.report.published"
    assert published.metadata["report_path"] == "report-session-1-0.md"
    assert published.metadata["evidence_path"] == "report-session-1-0-evidence.md"
    assert published.metadata["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert published.metadata["document_writes"] == 2


@pytest.mark.asyncio
async def test_the_real_synthesizer_publishes_both_artifacts_into_a_real_root(
    tmp_path: Path,
) -> None:
    """Task 7's Important 1, closed end to end.

    The finalizer's write path was pinned link by link — node↔publisher
    signatures, ``publish_document``/``publish_claim`` called directly,
    ``terminal_publisher(agents) is synthesizer``, ``allowed_tools``,
    ``build_agents`` fail-fast — but nothing ever ran ``finalize_report_node``
    with the **real** ``SynthesizerAgent`` as its publisher into a real output
    root and asserted a file exists. ``WriteDocumentTool`` is the real tool,
    resolving against ``tmp_path`` exactly as production resolves against the
    configured output directory.
    """
    memory = FakeMemory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="nodes-finalizer-test", api_key=None
        )
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="synthesizer", max_entries=20
        ),
        tools=synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    # The real ``write_document`` tool opens a child span, which requires the
    # active session span the orchestrator always runs a graph inside.
    async with tracker.session_span("session-1", "How mature is QEC?"):
        result = await finalize_report_node(synthesizer)(
            dump_state(_finalized_state(quality=fake_quality()))
        )
    state = load_state(result)

    assert state.errors == []
    assert state.report_path == "report-session-1-0.md"
    assert state.evidence_path == "report-session-1-0-evidence.md"
    reader = tmp_path / "report-session-1-0.md"
    ledger = tmp_path / "report-session-1-0-evidence.md"
    assert reader.is_file() and ledger.is_file()
    assert reader.read_text(encoding="utf-8") == state.report
    assert ledger.read_text(encoding="utf-8") == state.report_evidence
    published = state.events[-2]
    assert published.metadata["document_writes"] == 2
    assert published.metadata["memory_writes"] == 1
    assert memory.saved


@pytest.mark.asyncio
async def test_an_accepted_report_publishes_only_its_high_confidence_claims() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=fake_quality()))
    )

    assert load_state(result).events[-2].metadata["memory_writes"] == 1
    assert publisher.memory_writes == 1
    assert publisher.saved_claims == ["Break-even was reached in 2025."]


@pytest.mark.asyncio
async def test_a_partial_report_publishes_artifacts_but_saves_no_claim() -> None:
    """Step 6: memory is written only for an accepted report."""
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(
            _finalized_state(
                quality=fake_quality(hard_failures=["duplicate_claims"]),
                iteration=2,
                max_iterations=2,
                should_continue=True,
            )
        )
    )

    assert publisher.report_writes == 2
    assert publisher.memory_writes == 0
    assert load_state(result).events[-2].metadata["quality_status"] == (
        QUALITY_STATUS_PARTIAL
    )


@pytest.mark.asyncio
async def test_an_ungated_report_is_partial_and_saves_no_claim() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=None))
    )

    assert publisher.report_writes == 2
    assert publisher.memory_writes == 0
    assert load_state(result).events[-2].metadata["quality_status"] == (
        QUALITY_STATUS_PARTIAL
    )


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_markdown_authoritative() -> None:
    """Step 7: state holds the artifacts whether or not a file exists."""
    publisher = FakePublisher(fail_documents=("report-session-1-0.md",))

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=fake_quality()))
    )
    state = load_state(result)

    assert state.report == "# Reader report"
    assert state.report_evidence == "# Evidence ledger"
    assert state.report_path is None
    assert state.evidence_path == "report-session-1-0-evidence.md"
    assert [error.error_type for error in state.errors] == [
        "graph_publication_failed"
    ]
    assert state.errors[0].details["artifact"] == "reader"
    assert state.events[-2].metadata["report_path"] is None


@pytest.mark.asyncio
async def test_the_two_artifact_writes_fail_independently() -> None:
    publisher = FakePublisher(fail_documents=("-evidence.md",))

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state(quality=fake_quality()))
        )
    )

    assert state.report_path == "report-session-1-0.md"
    assert state.evidence_path is None
    assert [error.details["artifact"] for error in state.errors] == ["evidence"]


@pytest.mark.asyncio
async def test_a_terminal_write_failure_never_advertises_an_earlier_artifact() -> None:
    """Step 7: the terminal event is the only path a front-end may read."""
    publisher = FakePublisher(fail_documents=("report-session-1-",))

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(
                _finalized_state(
                    quality=fake_quality(),
                    # A later pass, so an earlier pass's names exist to leak.
                    iteration=1,
                )
            )
        )
    )

    published = state.events[-2]
    assert published.metadata["report_path"] is None
    assert published.metadata["evidence_path"] is None
    assert state.report_path is None
    assert state.evidence_path is None
    assert "report-session-1-1-evidence.md" not in str(published.metadata)


@pytest.mark.asyncio
async def test_a_run_with_no_publisher_writes_nothing_and_says_so() -> None:
    result = await finalize_report_node(None)(
        dump_state(_finalized_state(quality=fake_quality()))
    )
    state = load_state(result)

    assert state.report == "# Reader report"
    assert state.report_path is None
    assert state.evidence_path is None
    assert [error.error_type for error in state.errors] == [
        "graph_publication_unavailable"
    ]
    assert state.errors[0].recoverable is True
    assert state.events[-2].metadata["document_writes"] == 0


@pytest.mark.asyncio
async def test_the_finalizer_stamps_the_verdict_into_a_rendered_report() -> None:
    """The published report states the gate's verdict, not the placeholder."""
    state = _finalized_state(quality=fake_quality())
    composition = fake_reader_composition(state)

    result = await finalize_report_node(FakePublisher())(
        dump_state(state.model_copy(update={"composition": composition}))
    )
    final = load_state(result)

    assert "**Quality status:** accepted" in (final.report or "")
    assert final.composition is not None
    assert final.composition.quality_status == QUALITY_STATUS_ACCEPTED
    assert "not yet quality-gated" not in (final.report or "")


@pytest.mark.asyncio
async def test_a_halted_run_is_never_finalized() -> None:
    publisher = FakePublisher()

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(
                _finalized_state(
                    quality=fake_quality(), errors=[halting_error()]
                )
            )
        )
    )

    assert publisher.documents == []
    assert state.report_path is None
    assert _event_types(state) == ["graph.node.skipped"]


# --- Task 9: typed repair routing -------------------------------------------


def _evidence_target(
    target_id: str = "target-01",
    *,
    coverage_id: str = "topic-01",
    required_dimensions: Sequence[str] = ("cost",),
    support_policy: str = "independent_pair",
) -> EvidenceTarget:
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=coverage_id,
        question="What does it cost?",
        required_dimensions=list(required_dimensions),
        required=True,
        critical=True,
        support_policy=support_policy,
    )


def _planned_topic(*targets: EvidenceTarget) -> SubTopic:
    return fake_sub_topic().model_copy(
        update={"evidence_targets": list(targets)}
    )


def _gap(
    *,
    gap_id: str = "gap-01",
    action: str = "acquire",
    kind: str = "coverage",
    severity: str = "major",
    coverage_id: str | None = "topic-01",
    target_ids: Sequence[str] = (),
    statement_ids: Sequence[str] = (),
    claim_cluster_ids: Sequence[str] = (),
    queries: Sequence[str] = (),
    problem: str = "One obligation is unmet.",
) -> CritiqueGap:
    return CritiqueGap(
        gap_id=gap_id,
        coverage_id=coverage_id,
        target_ids=list(target_ids),
        statement_ids=list(statement_ids),
        claim_cluster_ids=list(claim_cluster_ids),
        kind=kind,
        severity=severity,
        repair_action=action,
        problem=problem,
        recommended_queries=list(queries),
    )


def _critique(*gaps: CritiqueGap, should_continue: bool = True) -> Critique:
    return Critique(
        score=5,
        gaps=list(gaps),
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=should_continue,
        rationale="Recorded for routing tests.",
    )


def test_the_repair_route_table_is_keyed_by_the_typed_action_literals() -> None:
    assert REPAIR_ACTIONS == (
        "extend_plan",
        "acquire",
        "assess_source",
        "adjudicate",
        "consolidate",
        "synthesize",
    )
    assert tuple(REPAIR_NODES) == REPAIR_ACTIONS
    assert REPAIR_NODES == {
        "extend_plan": "planner",
        "acquire": "researcher",
        "assess_source": "source_evaluator",
        "adjudicate": "fact_checker",
        "consolidate": "fact_checker",
        "synthesize": "synthesizer",
    }


def test_a_missing_obligation_routes_only_its_affected_target_to_acquisition() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                target_ids=["target-02"],
                kind="missing_support",
                queries=["low-carbon cement cost premium"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert [target.target_ids for target in targets] == [["target-02"]]
    assert [target.claim_cluster_ids for target in targets] == [[]]
    assert targets[0].queries == ["low-carbon cement cost premium"]
    assert route_refinement(targets[0]) == "researcher"


def test_a_source_assessment_failure_routes_to_the_source_evaluator() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="assess_source",
                kind="source_quality",
                target_ids=["target-01"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert route_refinement(targets[0]) == "source_evaluator"


def test_an_omitted_mechanism_already_in_evidence_routes_to_synthesis() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="synthesize",
                kind="presentation",
                coverage_id=None,
                statement_ids=["S003"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert targets[0].statement_ids == ["S003"]
    assert route_refinement(targets[0]) == "synthesizer"


def test_absent_mechanism_evidence_routes_to_acquisition() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="acquire",
                kind="mechanism",
                target_ids=["target-01"],
                queries=["how does direct air capture work"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert route_refinement(targets[0]) == "researcher"
    assert targets[0].queries == ["how does direct air capture work"]


def test_a_contradiction_routes_to_adjudication() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="adjudicate",
                kind="contradiction",
                coverage_id=None,
                claim_cluster_ids=["cluster-01"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert route_refinement(targets[0]) == "fact_checker"


def test_a_semantic_duplicate_routes_to_consolidation() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="consolidate",
                kind="semantic_duplicate",
                coverage_id=None,
                claim_cluster_ids=["cluster-01"],
            )
        )
    )

    targets = refinement_targets_for(state)

    assert route_refinement(targets[0]) == "fact_checker"


def test_an_original_question_omission_routes_to_planner_extension() -> None:
    state = fake_research_state(
        critique=_critique(
            _gap(
                action="extend_plan",
                kind="coverage",
                severity="critical",
                coverage_id=None,
                target_ids=["question"],
                problem="The question asks for a cost the plan never targeted.",
            )
        )
    )

    targets = refinement_targets_for(state)

    assert targets[0].target_ids == ["question"]
    assert route_refinement(targets[0]) == "planner"


def test_an_unknown_repair_action_is_refused_rather_than_routed() -> None:
    """A value this contract does not know must not fall back to a node."""
    target = RefinementTarget(
        target_ids=["target-01"], action="acquire", problem="No cost data."
    ).model_copy(update={"action": "search_more"})

    with pytest.raises(GraphConfigurationError):
        route_refinement(target)


def test_critic_silence_does_not_suppress_an_unanswered_required_target() -> None:
    """A topic with no Critic gap is still repairable while it owes an answer."""
    unmet = _evidence_target()
    state = fake_research_state(
        sub_topics=[_planned_topic(unmet)],
        critique=_critique(),
    )

    targets = refinement_targets_for(state)

    assert [target.target_ids for target in targets] == [["target-01"]]
    assert targets[0].origin == "unanswered_target"
    assert targets[0].coverage_id == "topic-01"
    assert route_refinement(targets[0]) == "researcher"


def test_one_raw_metadata_finding_does_not_complete_a_required_target() -> None:
    """The reviewed baseline's defect: a finding is not a satisfied target."""
    unmet = _evidence_target()
    state = fake_research_state(
        sub_topics=[_planned_topic(unmet)],
        raw_findings=[fake_finding("The source was published in 2024.")],
        critique=_critique(),
    )

    targets = refinement_targets_for(state)

    assert [target.target_ids for target in targets] == [["target-01"]]


def test_an_answered_target_with_a_critic_gap_is_repaired_by_its_gap() -> None:
    """A gap on a target the report already answers is still the Critic's job."""
    answered = _evidence_target()
    topic = _planned_topic(answered)
    composition = fake_reader_composition(
        fake_research_state(sub_topics=[topic])
    )
    state = fake_research_state(
        sub_topics=[topic],
        composition=composition,
        critique=_critique(
            _gap(target_ids=["target-01"], problem="The cost figure is stale.")
        ),
    )

    targets = refinement_targets_for(state)

    assert [target.origin for target in targets] == ["critic_gap"]


def test_gaps_differing_only_in_action_are_two_repair_jobs() -> None:
    """Routing identity includes the action: two nodes, two jobs."""
    state = fake_research_state(
        critique=_critique(
            _gap(gap_id="gap-01", action="acquire", target_ids=["target-01"]),
            _gap(gap_id="gap-02", action="adjudicate", target_ids=["target-01"]),
        )
    )

    targets = refinement_targets_for(state)

    assert [route_refinement(target) for target in targets] == [
        "researcher",
        "fact_checker",
    ]


def test_a_critic_gap_and_a_mechanical_defect_on_one_scope_are_one_job() -> None:
    unmet = _evidence_target()
    state = fake_research_state(
        sub_topics=[_planned_topic(unmet)],
        critique=_critique(
            _gap(
                gap_id="gap-03",
                action="acquire",
                target_ids=["target-01"],
                queries=["cost 2026"],
            )
        ),
    )

    targets = refinement_targets_for(state)

    assert len(targets) == 1
    assert targets[0].gap_id == "gap-03"
    assert targets[0].origin == "critic_gap"
    assert targets[0].queries == ["cost 2026"]


def test_new_factual_assertions_route_back_to_the_fact_checker() -> None:
    composition = fake_reader_composition(fake_research_state()).model_copy(
        update={"returned_to_fact_checker": ["It costs 12 EUR per tonne."]}
    )
    state = fake_research_state(composition=composition)

    targets = refinement_targets_for(state)

    assert [target.origin for target in targets] == ["returned_assertion"]
    assert route_refinement(targets[0]) == "fact_checker"


def test_a_presentation_repair_invalidates_no_verified_claim() -> None:
    state = fake_research_state(
        verified_claims=[fake_claim()],
        quality=fake_quality(),
    )
    presentation = RefinementTarget(
        statement_ids=["S001"],
        action="synthesize",
        problem="The paragraph is duplicated.",
    )

    assert invalidation_update(state, [presentation]) == {}


def test_invalidation_removes_only_the_claims_a_repair_touches() -> None:
    touched = fake_claim("The cost is 40 EUR.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    untouched = fake_claim("Safety improved.").model_copy(
        update={"target_ids": ["target-02"], "cluster_id": "cluster-02"}
    )
    state = fake_research_state(
        verified_claims=[touched, untouched],
        quality=fake_quality(),
    )
    repair = RefinementTarget(
        target_ids=["target-01"], action="acquire", problem="No cost data."
    )

    update = invalidation_update(state, [repair])

    assert [row.claim_id for row in update["verified_claims"]] == [
        untouched.claim_id
    ]
    assert update["quality"] is None


@pytest.mark.asyncio
async def test_the_refinement_hop_persists_the_typed_repair_jobs() -> None:
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        critique=_critique(
            _gap(
                action="adjudicate",
                kind="contradiction",
                coverage_id=None,
                claim_cluster_ids=["cluster-01"],
            )
        ),
        max_iterations=3,
    )

    refined = load_state(await refine_node(dump_state(state)))

    assert refined.iteration == 1
    assert [job.action for job in refined.refinement_targets] == [
        "adjudicate",
        "acquire",
    ]
    assert refined.progress_history
    assert refined.progress_history[-1].pending_work_ids == []
    assert refined.repair_stop_reason is None


@pytest.mark.asyncio
async def test_a_second_unchanged_refinement_hop_records_no_progress() -> None:
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        max_iterations=3,
    )

    first = await refine_node(dump_state(state))
    refined = load_state(await refine_node(first))

    assert refined.iteration == 2
    assert len(refined.progress_history) == 2
    assert refined.repair_stop_reason == "no_progress"


@pytest.mark.asyncio
async def test_a_stalled_repair_hop_opens_no_another_research_pass() -> None:
    """The stall is acted on here, not after another pass has been spent."""
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        max_iterations=3,
        iteration=1,
        critique=fake_critique(should_continue=True, score=4),
    )
    unchanged = state.model_copy(
        update={"progress_history": [progress_snapshot(state)]}
    )

    result = await refine_node(dump_state(unchanged))
    refined = load_state(result)

    assert refined.iteration == 1
    assert refined.repair_stop_reason == "no_progress"
    assert route_after_refine(result) == "finalize"
    assert refined.events[-1].metadata["reason"] == "no_progress"


@pytest.mark.asyncio
async def test_a_refinement_that_is_still_working_opens_the_next_pass() -> None:
    state = fake_research_state(max_iterations=3, iteration=0)

    result = await refine_node(dump_state(state))

    assert load_state(result).iteration == 1
    assert route_after_refine(result) == "researcher"
