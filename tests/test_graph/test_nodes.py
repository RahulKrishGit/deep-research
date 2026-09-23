"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.report import render_reader_report
from deep_research.agents.report_review import build_report_review_input
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
    report_review_node,
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
    graph_quality_status,
    graph_status,
    is_halted,
    load_state,
    progress_snapshot,
    repair_is_terminal,
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
    AcquisitionState,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    RefinementTarget,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchState,
    SubTopic,
    unanswered_required_targets,
)
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    FakeAgent,
    FakePublisher,
    FakeReviewer,
    fake_claim,
    fake_critique,
    fake_finding,
    fake_quality,
    fake_reader_composition,
    fake_rejected_report_review,
    fake_report_review,
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


# --- Task 10: the terminal semantic review node ------------------------------


def _reviewable_state(**overrides: object) -> ResearchState:
    """A pass whose report has a typed composition behind it.

    The review reads statements, targets, and evidence, so a state without a
    composition is one nothing can judge — which is its own test below.
    """
    base = fake_research_state(
        sub_topics=[fake_sub_topic()],
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
        quality=fake_quality(),
        critique=fake_critique(should_continue=False, score=9),
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )
    payload: dict[str, object] = {
        "composition": fake_reader_composition(base),
        "report": render_reader_report(fake_reader_composition(base)),
    }
    payload.update(overrides)
    return base.model_copy(update=payload)


@pytest.mark.asyncio
async def test_the_review_node_records_a_scored_review_beside_the_diagnostics() -> None:
    reviewer = FakeReviewer()
    state = _reviewable_state()

    result = await report_review_node(reviewer)(dump_state(state))
    loaded = load_state(result)

    assert reviewer.calls == 1
    assert loaded.report_review is not None
    assert loaded.report_review.status == "scored"
    assert loaded.report_review.input_fingerprint
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "scored"
    assert loaded.quality.semantic_review_score == 1.0
    # The judgement is recorded beside the structural diagnostics, never inside
    # them: a review that passed is not a hard failure and vice versa.
    assert loaded.quality.hard_failures == state.quality.hard_failures
    assert not loaded.errors
    assert not is_halted(loaded)
    reviewed = [
        event
        for event in loaded.events
        if event.event_type == "graph.report.reviewed"
    ]
    assert len(reviewed) == 1
    assert reviewed[0].metadata["review_status"] == "scored"
    assert reviewed[0].metadata["reused"] is False


@pytest.mark.asyncio
async def test_an_unreviewed_report_is_recorded_as_unreviewed_not_accepted() -> None:
    """A provider failure is a quality-assessment failure, not a graph failure.

    The report and its evidence are complete and still publish; what did not
    happen is the judgement, so the status says ``provider_failed``, the quality
    snapshot records it, and the error is recoverable — never a halt, and never
    an acceptance.
    """
    state = _reviewable_state()
    reviewer = FakeReviewer([fake_report_review(status="provider_failed")])

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    assert loaded.report_review is not None
    assert loaded.report_review.status == "provider_failed"
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "provider_failed"
    assert loaded.quality.semantic_review_score is None
    assert [error.error_type for error in loaded.errors] == [
        "graph_report_review_unavailable"
    ]
    assert loaded.errors[0].recoverable is True
    assert not is_halted(loaded)
    # The report itself is untouched: nothing about publishing it changed.
    assert loaded.report == (state.report or "").strip()
    assert graph_quality_status(loaded) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_the_published_state_keeps_the_record_that_nothing_judged_it() -> None:
    """A judgement is invalidated by *changed* content, not by a re-render.

    The terminal finalizer re-renders the composition with only the quality
    badge stamped on it, which is the same semantic material the review judged:
    that is the whole reason the fingerprint ignores the badge. A record of "no
    review was made" that carried no composition fingerprint could never match,
    so it was dropped by that merge — silently, at the node that publishes —
    and the state lost the honest record that the report went unreviewed while
    the quality snapshot still mentioned it.
    """
    state = _reviewable_state()
    reviewed = load_state(await report_review_node(None)(dump_state(state)))

    assert reviewed.report_review is not None
    assert reviewed.report_review.status == "incomplete"
    assert reviewed.report_review.composition_fingerprint

    published = load_state(
        await finalize_report_node(FakePublisher())(dump_state(reviewed))
    )

    assert published.report_review is not None
    assert published.report_review.status == "incomplete"
    assert published.report_review.input_fingerprint == (
        reviewed.report_review.input_fingerprint
    )
    assert published.quality is not None
    assert published.quality.semantic_review_status == "incomplete"


@pytest.mark.asyncio
async def test_a_report_with_no_composition_is_never_sent_for_review() -> None:
    """Nothing reviewable: refused locally rather than scored over prose.

    A hand-built report with no statement records behind it cannot be tied to
    any evidence, so a reviewer that answered anyway would be scoring text
    nothing can check. The node does not ask.
    """
    reviewer = FakeReviewer()
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 0
    assert loaded.report_review is not None
    assert loaded.report_review.status == "incomplete"
    assert loaded.report_review.input_fingerprint
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "incomplete"
    assert graph_quality_status(loaded) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_a_review_of_the_identical_fingerprint_costs_no_call() -> None:
    state = _reviewable_state()
    packet = build_report_review_input(state)
    stored = fake_report_review(fingerprint=packet.fingerprint)
    state = state.model_copy(update={"report_review": stored})
    reviewer = FakeReviewer()

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 0
    assert loaded.report_review == stored
    reused = [
        event
        for event in loaded.events
        if event.event_type == "graph.report.reviewed"
    ]
    assert reused[0].metadata["reused"] is True


@pytest.mark.asyncio
async def test_a_review_of_other_content_is_made_again() -> None:
    state = _reviewable_state()
    stored = fake_report_review(fingerprint="a-different-packet")
    state = state.model_copy(update={"report_review": stored})
    reviewer = FakeReviewer()

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 1
    assert loaded.report_review is not None
    assert loaded.report_review.input_fingerprint != "a-different-packet"


@pytest.mark.asyncio
async def test_a_refusing_review_records_the_route_it_changed() -> None:
    """The Critic accepted; the review did not, so the edge moves."""
    reviewer = FakeReviewer([fake_rejected_report_review()])
    state = _reviewable_state(iteration=0, max_iterations=3)

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    reasons = [
        event.metadata["reason"]
        for event in loaded.events
        if event.event_type == "graph.route.decided"
    ]
    assert reasons == ["semantic_review_gap"]
    assert route_after_critic(dump_state(loaded)) == ROUTE_REFINE
    assert graph_status(loaded) == "incomplete"
    assert graph_quality_status(loaded) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_an_unchanged_route_is_recorded_once() -> None:
    """A review that agrees with the Critic does not re-announce the route."""
    reviewer = FakeReviewer()
    state = _reviewable_state()

    loaded = load_state(await report_review_node(reviewer)(dump_state(state)))

    assert [
        event.metadata["reason"]
        for event in loaded.events
        if event.event_type == "graph.route.decided"
    ] == []


def _two_topic_state(**overrides: object) -> ResearchState:
    """topic-01 satisfied (a finding names it), topic-02 still owed.

    The previous hop's snapshot is recorded too, so the stop decision is
    actually evaluated rather than skipped for a first refinement.
    """
    satisfied = fake_sub_topic()
    owed = fake_sub_topic(
        title="Cost curve", coverage_id="topic-02", priority=2
    ).model_copy(
        update={
            "evidence_targets": [
                _evidence_target("topic-02-target-01", coverage_id="topic-02")
            ]
        }
    )
    payload: dict[str, object] = {
        "sub_topics": [satisfied, owed],
        "raw_findings": [fake_finding()],
        "critique": fake_critique(should_continue=False, score=9),
        "max_iterations": 3,
        "iteration": 1,
    }
    payload.update(overrides)
    state = fake_research_state(**payload)
    return state.model_copy(
        update={"progress_history": [progress_snapshot(state)]}
    )


def _spent_topic_02() -> AcquisitionState:
    return AcquisitionState(
        target_id="topic-02",
        remaining_calls=0,
        empty_searches=2,
        denied_urls=["https://lab.example/denied"],
    )


@pytest.mark.asyncio
async def test_the_stop_decision_reads_the_worklist_the_hop_routes() -> None:
    """A job this hop no longer routes must not hold the run open.

    The hop evaluated the stop decision against the *previous* hop's
    ``refinement_targets`` and only computed the list it dispatches on
    afterwards, so the accounting judged work no pass would run. A topic that
    was owed last hop and is satisfied now kept its queue counted, capacity
    looked unspent, and the run bought passes it could not use.
    """
    state = _two_topic_state(
        refinement_targets=[
            RefinementTarget(
                coverage_id="topic-01",
                target_ids=["target-01"],
                action="acquire",
                origin="unanswered_target",
                severity="major",
                problem="One obligation is unmet.",
            )
        ],
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                remaining_calls=3,
                candidate_urls=["https://lab.example/untouched"],
            ),
            "topic-02": _spent_topic_02(),
        },
    )

    recorded = load_state(await refine_node(dump_state(state)))

    # The routed worklist names the owed topic only, so topic-01's leftover
    # queue is not work this run can spend, and the leads that were all tried
    # are reported as the dead end they are.
    assert [
        job.coverage_id
        for job in recorded.refinement_targets
        if job.action == "acquire"
    ] == ["topic-02"]
    assert recorded.repair_stop_reason == "evidence_unavailable"
    assert recorded.progress_history[-1].pending_work_ids == []


@pytest.mark.asyncio
async def test_a_review_defect_the_hop_routes_opens_the_pass_it_was_routed_for() -> (
    None
):
    """The stop decision must see the jobs this hop just routed.

    A scored review that names a *satisfied* topic is a repair job the
    refinement hop routes to the Researcher. Judged against the previous hop's
    list — which said nothing about that topic, because the review had not run
    yet — the run called every lead spent, went terminal and published with
    the material defect it had just routed still open, dropping the queue the
    repair needed.
    """
    state = _two_topic_state(
        report_review=fake_report_review(
            defects=[
                CritiqueGap(
                    gap_id="review-01",
                    coverage_id="topic-01",
                    target_ids=["target-01"],
                    kind="missing_support",
                    severity="major",
                    repair_action="acquire",
                    problem="The cost figure rests on a single publisher.",
                )
            ]
        ),
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                remaining_calls=3,
                candidate_urls=["https://lab.example/queued"],
            ),
            "topic-02": _spent_topic_02(),
        },
    )

    recorded = load_state(await refine_node(dump_state(state)))

    assert "topic-01" in {
        job.coverage_id
        for job in recorded.refinement_targets
        if job.action == "acquire"
    }
    assert recorded.repair_stop_reason is None
    assert recorded.progress_history[-1].pending_work_ids == [
        "topic-01:candidate:https://lab.example/queued"
    ]
    assert not repair_is_terminal(recorded)


@pytest.mark.asyncio
async def test_a_routed_job_on_a_topic_never_acquired_keeps_the_pass_alive() -> None:
    """A routed repair is owed work even before any acquisition state exists.

    ``pending_repair_work`` iterates ``acquisition_state_by_target``, so a
    selectable key the run never opened an acquisition for contributed nothing
    to the count: the stop decision could call the run finished — every lead it
    *had* tried being spent — while the job this very hop had just routed for
    that topic was still open. The acquisition key is the record of an attempt;
    its absence says the work has not started, which is the opposite of done.
    """
    state = _two_topic_state(
        report_review=fake_report_review(
            defects=[
                CritiqueGap(
                    gap_id="review-01",
                    coverage_id="topic-01",
                    target_ids=["target-01"],
                    kind="missing_support",
                    severity="major",
                    repair_action="acquire",
                    problem="The cost figure rests on a single publisher.",
                )
            ]
        ),
        acquisition_state_by_target={"topic-02": _spent_topic_02()},
    )

    recorded = load_state(await refine_node(dump_state(state)))

    assert recorded.progress_history[-1].pending_work_ids == [
        "topic-01:unattempted"
    ]
    assert recorded.repair_stop_reason is None
    assert not repair_is_terminal(recorded)


@pytest.mark.asyncio
async def test_a_routed_job_whose_leads_are_spent_still_reaches_its_dead_end() -> (
    None
):
    """The dead-end reason stays reachable: every attempt made, nothing queued.

    Counting an unattempted target as owed work must not make
    ``evidence_unavailable`` unreachable. Once every selectable target has an
    acquisition state of its own, and none of them holds a queue or a call, the
    run has genuinely run out of leads and says so.
    """
    state = _two_topic_state(
        report_review=fake_report_review(
            defects=[
                CritiqueGap(
                    gap_id="review-01",
                    coverage_id="topic-01",
                    target_ids=["target-01"],
                    kind="missing_support",
                    severity="major",
                    repair_action="acquire",
                    problem="The cost figure rests on a single publisher.",
                )
            ]
        ),
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                remaining_calls=0,
                empty_searches=2,
                denied_urls=["https://lab.example/denied"],
            ),
            "topic-02": _spent_topic_02(),
        },
    )

    recorded = load_state(await refine_node(dump_state(state)))

    assert recorded.progress_history[-1].pending_work_ids == []
    assert recorded.repair_stop_reason == "evidence_unavailable"


def test_a_review_defect_becomes_a_typed_refinement_job() -> None:
    state = _reviewable_state(
        report_review=fake_rejected_report_review(repair_action="acquire"),
    )

    jobs = [
        job
        for job in refinement_targets_for(state)
        if job.origin == "review_defect"
    ]

    assert len(jobs) == 1
    assert jobs[0].action == "acquire"
    assert jobs[0].statement_ids == ["S001"]
    assert jobs[0].target_ids == ["t1"]


def test_an_incomplete_review_contributes_no_repair_jobs() -> None:
    """No judgement means no defects and no invented work."""
    state = _reviewable_state(
        report_review=fake_report_review(status="incomplete"),
    )

    assert [
        job
        for job in refinement_targets_for(state)
        if job.origin == "review_defect"
    ] == []


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
    report_review: ReportReview | None = None,
) -> ResearchState:
    """A run that has finished its Critic pass and is ready to be finalized.

    ``report_review`` defaults to a scored pass, because since Task 10 nothing
    is accepted without one: a state with no review is a state nothing judged,
    and a test that wants that outcome passes ``report_review=`` explicitly.
    """
    return fake_research_state(
        sub_topics=[fake_sub_topic()],
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
        report=report,
        report_evidence=report_evidence,
        quality=quality,
        report_review=(
            fake_report_review() if report_review is None else report_review
        ),
        critique=fake_critique(
            should_continue=should_continue,
            score=9 if not should_continue else 4,
        ),
        iteration=iteration,
        max_iterations=max_iterations,
        errors=errors or [],
    )


@pytest.mark.asyncio
async def test_the_finalizer_publishes_every_artifact_exactly_once() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=fake_quality()))
    )
    state = load_state(result)

    assert publisher.report_writes == 3
    assert state.report_path == "report-session-1-0.md"
    assert state.evidence_path == "report-session-1-0-evidence.md"
    assert state.quality_path == "report-session-1-0-quality.json"
    assert publisher.written_paths == [
        "report-session-1-0.md",
        "report-session-1-0-evidence.md",
        "report-session-1-0-quality.json",
    ]
    assert publisher.document_named("report-session-1-0.md")[1] == (
        "# Reader report"
    )
    assert publisher.document_named("-evidence.md")[1] == "# Evidence ledger"
    quality = json.loads(publisher.document_named("-quality.json")[1])
    assert quality["session_id"] == "session-1"
    assert quality["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert not state.errors
    published = state.events[-2]
    assert published.event_type == "graph.report.published"
    assert published.metadata["report_path"] == "report-session-1-0.md"
    assert published.metadata["evidence_path"] == "report-session-1-0-evidence.md"
    assert published.metadata["quality_path"] == (
        "report-session-1-0-quality.json"
    )
    assert published.metadata["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert published.metadata["document_writes"] == 3


@pytest.mark.asyncio
async def test_the_published_quality_record_hashes_the_two_documents_it_describes(
) -> None:
    """The set is internally checkable from the record it was published with."""
    publisher = FakePublisher()

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state(quality=fake_quality()))
        )
    )

    reader_text = publisher.document_named("report-session-1-0.md")[1]
    ledger_text = publisher.document_named("-evidence.md")[1]
    record = json.loads(publisher.document_named("-quality.json")[1])

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    assert record["artifacts"] == {
        "reader_markdown": digest(reader_text),
        "evidence_markdown": digest(ledger_text),
    }
    assert reader_text == state.report
    assert ledger_text == state.report_evidence
    assert record["quality_status"] == QUALITY_STATUS_ACCEPTED


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
    assert state.quality_path == "report-session-1-0-quality.json"
    reader = tmp_path / "report-session-1-0.md"
    ledger = tmp_path / "report-session-1-0-evidence.md"
    quality = tmp_path / "report-session-1-0-quality.json"
    assert reader.is_file() and ledger.is_file() and quality.is_file()
    assert reader.read_text(encoding="utf-8") == state.report
    assert ledger.read_text(encoding="utf-8") == state.report_evidence
    record = json.loads(quality.read_text(encoding="utf-8"))
    # The record the filesystem holds describes the files the filesystem holds.
    assert record["artifacts"]["reader_markdown"] == hashlib.sha256(
        reader.read_bytes()
    ).hexdigest()
    assert record["artifacts"]["evidence_markdown"] == hashlib.sha256(
        ledger.read_bytes()
    ).hexdigest()
    published = state.events[-2]
    assert published.metadata["document_writes"] == 3
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

    assert publisher.report_writes == 3
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

    assert publisher.report_writes == 3
    assert publisher.memory_writes == 0
    assert load_state(result).events[-2].metadata["quality_status"] == (
        QUALITY_STATUS_PARTIAL
    )


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_markdown_authoritative() -> None:
    """Step 7: state holds the artifacts whether or not a file exists.

    And an incomplete set advertises nothing: the reader Markdown is in state,
    the other two files may well be on disk, and no path is published, because
    a front-end pointed at two thirds of a set cannot tell which third is
    missing from the paths alone.
    """
    publisher = FakePublisher(fail_documents=("report-session-1-0.md",))

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state(quality=fake_quality()))
    )
    state = load_state(result)

    assert state.report == "# Reader report"
    assert state.report_evidence == "# Evidence ledger"
    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
    assert [error.error_type for error in state.errors] == [
        "graph_publication_failed"
    ]
    assert state.errors[0].details["artifact"] == "reader"
    published = state.events[-2]
    assert published.metadata["report_path"] is None
    assert published.metadata["evidence_path"] is None
    assert published.metadata["quality_path"] is None
    # The count is the truthful one: two writes succeeded.
    assert published.metadata["document_writes"] == 2


@pytest.mark.asyncio
async def test_a_failed_quality_write_withholds_the_whole_advertised_set() -> None:
    """A required artifact that did not publish leaves no accepted output.

    The quality record is part of the set, not an optional extra: without it
    the two Markdown documents cannot be checked against the IDs and hashes
    that describe them, so the publication is incomplete and says so.
    """
    publisher = FakePublisher(fail_documents=("-quality.json",))

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state(quality=fake_quality()))
        )
    )

    assert state.report is not None and state.report_evidence is not None
    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
    assert [error.details["artifact"] for error in state.errors] == ["quality"]
    assert state.errors[0].details["failure_type"] == "ValidationError"
    assert state.events[-2].metadata["document_writes"] == 2


@pytest.mark.asyncio
async def test_each_artifact_write_fails_independently() -> None:
    """Each write records its own error; none of them hides another's."""
    publisher = FakePublisher(fail_documents=("-evidence.md", "-quality.json"))

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state(quality=fake_quality()))
        )
    )

    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
    assert [error.details["artifact"] for error in state.errors] == [
        "evidence",
        "quality",
    ]
    assert state.events[-2].metadata["document_writes"] == 1


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
    assert published.metadata["quality_path"] is None
    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
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
    assert state.quality_path is None
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


@pytest.mark.asyncio
async def test_an_extend_plan_job_dispatches_the_hop_to_the_planner() -> None:
    """The typed route is dispatched, not merely recorded.

    An original-question omission is expressed as ``extend_plan`` over the
    ``question`` sentinel. Computing and persisting that job while sending the
    next pass to the researcher is how the omission went unrepaired: the
    planner has to be the node the hop routes to.
    """
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        critique=_critique(
            _gap(
                action="extend_plan",
                kind="coverage",
                severity="critical",
                coverage_id=None,
                target_ids=["question"],
                problem="The question asks for a cost the plan never targeted.",
            )
        ),
        max_iterations=3,
        iteration=0,
    )

    result = await refine_node(dump_state(state))
    refined = load_state(result)

    assert refined.refinement_targets[0].action == "extend_plan"
    assert refined.refinement_targets[0].target_ids == ["question"]
    assert route_after_refine(result) == "planner"


@pytest.mark.asyncio
async def test_an_acquisition_repair_still_opens_a_research_pass() -> None:
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        critique=_critique(_gap(target_ids=["target-01"])),
        max_iterations=3,
        iteration=0,
    )

    result = await refine_node(dump_state(state))

    assert route_after_refine(result) == "researcher"


@pytest.mark.asyncio
async def test_the_refinement_hop_invalidates_only_the_claims_a_job_touches(
) -> None:
    """The hop applies the invalidation, and only for typed jobs.

    A mechanically unmet target gets an ``acquire`` job every pass, so
    invalidating on those would drop the claims of every still-open target on
    every pass. A Critic-typed job is a statement that its own scope changed,
    and that scope is what may be invalidated.
    """
    touched = fake_claim("The cost is 40 EUR.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    untouched = fake_claim("Safety improved.").model_copy(
        update={"target_ids": ["target-02"], "cluster_id": "cluster-02"}
    )
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        verified_claims=[touched, untouched],
        quality=fake_quality(),
        critique=_critique(
            _gap(
                action="adjudicate",
                kind="contradiction",
                coverage_id=None,
                target_ids=["target-01"],
            )
        ),
        max_iterations=3,
        iteration=0,
    )

    refined = load_state(await refine_node(dump_state(state)))

    assert [claim.claim_id for claim in refined.verified_claims] == [
        untouched.claim_id
    ]
    assert refined.quality is None


@pytest.mark.asyncio
async def test_a_terminal_hop_does_not_invalidate_the_ledger_it_publishes(
) -> None:
    """No pass follows a stall, so nothing may be dropped on the way out.

    Invalidating here would leave finalization reading a ledger no review
    judged: the dropped claim's ``claim_id`` would vanish from state while the
    report rendered from the composition still cites it, and the quality
    snapshot would be cleared although nothing replaced it.
    """
    touched = fake_claim("The cost is 40 EUR.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    untouched = fake_claim("Safety improved.").model_copy(
        update={"target_ids": ["target-02"], "cluster_id": "cluster-02"}
    )
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        verified_claims=[touched, untouched],
        quality=fake_quality(),
        critique=_critique(
            _gap(
                action="adjudicate",
                kind="contradiction",
                coverage_id=None,
                target_ids=["target-01"],
            )
        ),
        max_iterations=3,
        iteration=1,
    )
    unchanged = state.model_copy(
        update={"progress_history": [progress_snapshot(state)]}
    )

    result = await refine_node(dump_state(unchanged))
    refined = load_state(result)

    assert refined.repair_stop_reason == "no_progress"
    assert route_after_refine(result) == "finalize"
    assert [claim.claim_id for claim in refined.verified_claims] == [
        touched.claim_id,
        untouched.claim_id,
    ]
    assert refined.quality is not None


@pytest.mark.asyncio
async def test_an_unanswered_target_job_invalidates_no_claim() -> None:
    """A pending obligation is not a changed input: its claims stand.

    The claim here carries the very target the mechanical job names, and it
    does not satisfy that target's policy — which is why the target is open —
    so invalidating on the mechanical job would delete it on every pass.
    """
    open_obligation = fake_claim(
        "Later work bears on the cost.", confidence=0.4
    ).model_copy(update={"target_ids": ["target-01"]})
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        verified_claims=[open_obligation],
        critique=_critique(),
        max_iterations=3,
        iteration=0,
    )
    assert unanswered_required_targets(state)

    refined = load_state(await refine_node(dump_state(state)))

    assert [job.origin for job in refined.refinement_targets] == [
        "unanswered_target"
    ]
    assert [claim.claim_id for claim in refined.verified_claims] == [
        open_obligation.claim_id
    ]


@pytest.mark.asyncio
async def test_an_assess_source_job_drops_the_sources_its_claims_cite() -> None:
    """The requirement names source reviews: a re-assessment invalidates them."""
    url = "https://example.org/a"
    stale = fake_claim("The cost is 40 EUR.").model_copy(
        update={"target_ids": ["target-01"], "source_urls": [url]}
    )
    state = fake_research_state(
        sub_topics=[_planned_topic(_evidence_target())],
        verified_claims=[stale],
        evaluated_sources=[fake_scored_source(url)],
        critique=_critique(
            _gap(
                action="assess_source",
                kind="source_quality",
                target_ids=["target-01"],
            )
        ),
        max_iterations=3,
        iteration=0,
    )

    refined = load_state(await refine_node(dump_state(state)))

    assert refined.verified_claims == []
    assert refined.evaluated_sources == []


@pytest.mark.asyncio
async def test_a_synthesize_job_invalidates_nothing_at_the_hop() -> None:
    claim = fake_claim("The cost is 40 EUR.").model_copy(
        update={"target_ids": ["target-01"]}
    )
    state = fake_research_state(
        verified_claims=[claim],
        evaluated_sources=[fake_scored_source()],
        quality=fake_quality(),
        critique=_critique(
            _gap(
                action="synthesize",
                kind="presentation",
                coverage_id=None,
                statement_ids=["S001"],
            )
        ),
        max_iterations=3,
        iteration=0,
    )

    refined = load_state(await refine_node(dump_state(state)))

    assert [row.claim_id for row in refined.verified_claims] == [claim.claim_id]
    assert refined.evaluated_sources
    assert refined.quality is not None
