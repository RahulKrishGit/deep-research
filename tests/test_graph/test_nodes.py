"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.graph.nodes import (
    agent_node,
    critic_node,
    finalize_report_node,
    refine_node,
    route_after_critic,
    synthesizer_node,
)
from deep_research.graph.state import (
    ROUTE_END,
    ROUTE_FINALIZE,
    ROUTE_REFINE,
    dump_state,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    ReportQualitySnapshot,
    ResearchError,
    ResearchState,
)
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
        (PlanningError("no plan"), "graph_planning_failed"),
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
    agent = FakeAgent("synthesizer", [{"report": "# never written"}])

    state = load_state(
        await synthesizer_node(agent)(
            dump_state(fake_research_state(errors=[halting_error()]))
        )
    )

    assert agent.calls == []
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
