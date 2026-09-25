"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.report_reviewer import build_report_review_input
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report_writer import ReportWriterAgent
from deep_research.graph.errors import GRAPH_ERROR_REASONS, GraphConfigurationError
from deep_research.graph.nodes import (
    GraphNode,
    agent_node,
    extra_pass_node,
    finalize_report_node,
    report_reviewer_node,
    report_writer_node,
    route_after_review,
)
from deep_research.graph.state import (
    EXTRA_PASS_NODE,
    ROUTE_END,
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ResearchGraphState,
    dump_state,
    graph_quality_status,
    graph_status,
    is_halted,
    load_state,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import (
    LangSmithRuntimeConfig,
    RunTelemetryCollector,
    Tracker,
)
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
    REVIEW_DIMENSIONS,
    ResearchError,
    ResearchState,
)
from deep_research.agents.report_writer import REPORT_WRITER_NAME
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    SNIPPET,
    FakeAgent,
    FakePublisher,
    FakeReviewer,
    fake_quality,
    fake_report_review,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    fake_writer_update,
    halting_error,
    verified_pass,
)
from tests.research_fakes import FakeMemory, report_writer_tools


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


def _pass_state(**overrides: object) -> ResearchState:
    """A pass's evidence with nothing composed from it yet.

    The state the writer node is handed *before* it runs: the plan, the
    finding, and the page it was read from, with no composition and no
    quality snapshot. A fixture that arrived with either already stamped
    would hide the pass this node exists to run.
    """
    one = verified_pass()
    payload: dict[str, object] = {
        "sub_topics": [fake_sub_topic(targets=[fake_target()])],
        "raw_findings": [one.finding],
        "verified_findings": [one.finding],
        "read_records": {one.read.read_id: one.read},
        "evaluated_sources": [fake_scored_source()],
    }
    payload.update(overrides)
    return fake_research_state(**payload)


def _writer_state(**overrides: object) -> ResearchState:
    """A pass whose report has a typed composition behind it.

    The Report Writer's own output shape, so the writer and reviewer nodes are
    exercised against the artifacts their producers actually emit.
    """
    base = _pass_state()
    composition = fake_writer_composition(base)
    payload: dict[str, object] = {
        "composition": composition,
        "report": render_written_report(composition),
        "report_evidence": render_finding_log(composition),
    }
    payload.update(overrides)
    state = base.model_copy(update=payload)
    if "quality" not in overrides:
        # What the writer node would have stamped for this exact state, so a
        # fixture's snapshot cannot disagree with the composition beside it.
        state = state.model_copy(
            update={"quality": compute_report_quality(state, composition)}
        )
    return state


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
    one = verified_pass()
    agent = FakeAgent("researcher", [{"raw_findings": [one.finding]}])

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
    # Agents record a failed Context Check batch as non-recoverable. A
    # verification pass is expected to survive one; only enumerated graph
    # errors halt.
    outage = ResearchError(
        error_type="evidence_verifier_context_check_failed",
        source="agent.evidence_verifier",
        message="The Context Check failed for one batch.",
        recoverable=False,
    )
    agent = FakeAgent("evidence_verifier", [{"errors": [outage]}])

    state = load_state(
        await agent_node(agent)(dump_state(fake_research_state()))
    )

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
        agent_node(FakeAgent("evidence_verifier", [refusal])),
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
    agent = FakeAgent("report_writer", [refusal])

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
    assert [error.source for error in state.errors] == ["graph.report_writer"]
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
    agent = FakeAgent("report_writer", [{"report": "# never written"}])

    result = await agent_node(agent)(
        dump_state(fake_research_state(errors=[halting_error()]))
    )
    state = load_state(result)

    assert agent.calls == []
    assert state.report is None
    assert _event_types(state) == ["graph.node.skipped"]


# --- the Report Writer node --------------------------------------------------


@pytest.mark.asyncio
async def test_the_writer_node_stamps_the_snapshot_the_gates_computed() -> None:
    """The deterministic gates run here, over the state the pass *produced*.

    Judging the composition against the state handed in would report every
    pass as having no artifacts at all, so the writer node is the one place
    ``compute_report_quality`` is called.
    """
    agent = FakeAgent("report_writer", [], update_factory=fake_writer_update)

    result = await report_writer_node(agent)(dump_state(_pass_state()))
    state = load_state(result)

    assert state.composition is not None
    assert state.quality is not None
    assert state.quality.hard_failures == []
    assert state.quality.required_target_ids == ["topic-01-target-01"]
    assert state.quality.answered_target_ids == ["topic-01-target-01"]
    assert state.quality.missing_required_target_ids == []
    assert "graph.quality.assessed" in _event_types(state)
    assessed = [
        event for event in state.events if event.event_type == "graph.quality.assessed"
    ][0]
    assert assessed.metadata["hard_failures"] == []
    assert assessed.metadata["required_target_ids"] == ["topic-01-target-01"]


@pytest.mark.asyncio
async def test_the_writer_node_records_every_hard_failure_it_found() -> None:
    def unledgered(state: ResearchState) -> dict[str, object]:
        update = dict(fake_writer_update(state))
        update["report_evidence"] = ""
        update["composition"] = fake_writer_composition(state)
        return update

    agent = FakeAgent("report_writer", [], update_factory=unledgered)

    state = load_state(
        await report_writer_node(agent)(dump_state(_pass_state()))
    )

    assert state.quality is not None
    assert state.quality.hard_failures == ["missing_evidence_ledger"]


@pytest.mark.asyncio
async def test_a_compositionless_pass_records_no_quality_verdict() -> None:
    """The absence stays visible: no run is ever accepted without a snapshot."""
    agent = FakeAgent("report_writer", [{"report": "# prose with nothing behind it"}])

    state = load_state(
        await report_writer_node(agent)(dump_state(_pass_state()))
    )

    assert state.composition is None
    assert state.quality is None
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_a_halted_writer_pass_is_not_quality_graded() -> None:
    """A halted pass composed nothing; an earlier pass's composition is not re-judged."""
    agent = FakeAgent("report_writer", [{}])
    earlier = _writer_state(quality=None)

    result = await report_writer_node(agent)(
        dump_state(
            earlier.model_copy(update={"errors": [halting_error()]})
        )
    )
    state = load_state(result)

    assert agent.calls == []
    assert state.quality is None
    assert _event_types(state)[-1] == "graph.node.skipped"


# --- the Report Reviewer node ------------------------------------------------


@pytest.mark.asyncio
async def test_the_review_node_records_a_scored_review_beside_the_diagnostics() -> None:
    reviewer = FakeReviewer()
    state = _writer_state()

    result = await report_reviewer_node(reviewer)(dump_state(state))
    loaded = load_state(result)

    assert reviewer.calls == 1
    assert loaded.report_review is not None
    assert loaded.report_review.status == "scored"
    assert loaded.report_review.input_fingerprint
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "scored"
    assert loaded.quality.semantic_review_score == 0.9
    # The judgement is recorded beside the structural diagnostics, never inside
    # them: a review that passed is not a hard failure and vice versa.
    assert loaded.quality.hard_failures == state.quality.hard_failures  # type: ignore[union-attr]
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
async def test_the_review_node_stamps_the_missing_targets_the_gates_computed() -> None:
    """PD-5: code computes the missing targets; the reviewer never produces them.

    The stamp is what routing reads, and it is written whatever the review
    said — a review that named no missing target of its own cannot clear the
    obligation the deterministic pass measured.
    """
    reviewer = FakeReviewer()
    one = verified_pass()
    two_topics = fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )
    state = _writer_state(
        sub_topics=[two_topics],
        report_review=fake_report_review(missing_required_target_ids=["stale"]),
    )

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    assert loaded.quality is not None
    assert loaded.quality.missing_required_target_ids == ["topic-01-target-02"]
    assert loaded.report_review is not None
    assert loaded.report_review.missing_required_target_ids == [
        "topic-01-target-02"
    ]
    assert two_topics.evidence_targets[0].target_id == "topic-01-target-01"
    assert one.finding.verification is not None


@pytest.mark.asyncio
async def test_an_unscored_review_still_carries_the_missing_targets() -> None:
    reviewer = FakeReviewer([fake_report_review(status="provider_failed")])
    state = _writer_state()

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    assert loaded.report_review is not None
    assert loaded.report_review.status == "provider_failed"
    assert loaded.report_review.missing_required_target_ids == []
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "provider_failed"
    assert loaded.quality.semantic_review_score is None
    assert [error.error_type for error in loaded.errors] == [
        "graph_report_review_unavailable"
    ]
    assert loaded.errors[0].recoverable is True
    assert not is_halted(loaded)
    assert graph_quality_status(loaded) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_the_review_node_records_one_route_decision_per_decision() -> None:
    reviewer = FakeReviewer()
    state = _writer_state()

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    decided = [
        event
        for event in loaded.events
        if event.event_type == "graph.route.decided"
    ]
    assert len(decided) == 1
    assert decided[0].metadata["reason"] == "report_accepted"
    assert decided[0].metadata["destination"] == ROUTE_FINALIZE
    assert decided[0].metadata["max_extra_passes"] == state.max_extra_passes
    assert route_after_review(dump_state(loaded)) == ROUTE_FINALIZE


@pytest.mark.asyncio
async def test_a_missing_target_moves_the_edge_and_is_recorded_once() -> None:
    reviewer = FakeReviewer()
    two_topics = fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )
    state = _writer_state(sub_topics=[two_topics])

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    decided = [
        event
        for event in loaded.events
        if event.event_type == "graph.route.decided"
    ]
    assert [event.metadata["reason"] for event in decided] == [
        "extra_pass_requested"
    ]
    assert route_after_review(dump_state(loaded)) == ROUTE_EXTRA_PASS


@pytest.mark.asyncio
async def test_a_halted_review_node_still_records_a_route() -> None:
    reviewer = FakeReviewer()

    loaded = load_state(
        await report_reviewer_node(reviewer)(
            dump_state(_writer_state(errors=[halting_error()]))
        )
    )

    assert reviewer.calls == 0
    assert _event_types(loaded) == ["graph.node.skipped"]
    assert route_after_review(dump_state(loaded)) == ROUTE_END
    assert graph_status(loaded) == "failed"


@pytest.mark.asyncio
async def test_a_report_with_no_composition_is_never_sent_for_review() -> None:
    """Nothing reviewable: refused locally rather than scored over prose."""
    reviewer = FakeReviewer()
    state = fake_research_state(
        quality=fake_quality(),
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 0
    assert loaded.report_review is not None
    assert loaded.report_review.status == "incomplete"
    assert loaded.report_review.input_fingerprint
    assert loaded.quality is not None
    assert loaded.quality.semantic_review_status == "incomplete"
    assert graph_quality_status(loaded) == QUALITY_STATUS_PARTIAL


@pytest.mark.asyncio
async def test_a_review_of_the_identical_fingerprint_costs_no_call() -> None:
    state = _writer_state()
    packet = build_report_review_input(state, state.composition)
    stored = fake_report_review(fingerprint=packet.fingerprint)
    state = state.model_copy(update={"report_review": stored})
    reviewer = FakeReviewer()

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 0
    assert loaded.report_review is not None
    assert loaded.report_review.input_fingerprint == packet.fingerprint
    reused = [
        event
        for event in loaded.events
        if event.event_type == "graph.report.reviewed"
    ]
    assert reused[0].metadata["reused"] is True


@pytest.mark.asyncio
async def test_a_review_of_other_content_is_made_again() -> None:
    state = _writer_state()
    stored = fake_report_review(fingerprint="a-different-packet")
    state = state.model_copy(update={"report_review": stored})
    reviewer = FakeReviewer()

    loaded = load_state(await report_reviewer_node(reviewer)(dump_state(state)))

    assert reviewer.calls == 1
    assert loaded.report_review is not None
    assert loaded.report_review.input_fingerprint != "a-different-packet"


# --- the extra-pass hop ------------------------------------------------------


def _owed_target_state(**overrides: object) -> ResearchState:
    two_topics = fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )
    base = _writer_state(sub_topics=[two_topics])
    payload: dict[str, object] = {
        "report_review": fake_report_review(
            missing_required_target_ids=["topic-01-target-02"]
        )
    }
    payload.update(overrides)
    return base.model_copy(update=payload)


@pytest.mark.asyncio
async def test_the_extra_pass_hop_advances_the_iteration_and_confines_the_pass() -> None:
    state = _owed_target_state(iteration=0, max_extra_passes=1)

    advanced = load_state(await extra_pass_node(dump_state(state)))

    assert advanced.iteration == 1
    assert advanced.extra_pass_target_ids == ["topic-01-target-02"]
    assert _event_types(advanced)[-3:] == [
        "graph.node.started",
        "graph.extra_pass.started",
        "graph.node.completed",
    ]
    started = advanced.events[-2]
    assert started.event_type == "graph.extra_pass.started"
    assert started.metadata == {
        "iteration": 1,
        "max_extra_passes": 1,
        "targets": ["topic-01-target-02"],
    }


@pytest.mark.asyncio
async def test_the_extra_pass_hop_replaces_the_worklist_it_inherits() -> None:
    """``extra_pass_target_ids`` is this pass's job list, never an accumulation."""
    state = _owed_target_state(
        iteration=0,
        max_extra_passes=1,
        extra_pass_target_ids=["topic-01-target-01"],
    )

    advanced = load_state(await extra_pass_node(dump_state(state)))

    assert advanced.extra_pass_target_ids == ["topic-01-target-02"]


@pytest.mark.asyncio
async def test_the_extra_pass_hop_refuses_to_spend_a_pass_it_lacks() -> None:
    """The router never sends the hop past the ceiling; the hop still guards it.

    ``iteration`` moves only through the graph's own advance, and the guard is
    the second lock on that door: a run that somehow reached the hop with no
    pass left records an enumerated halt rather than paying for a pass its
    declared ceiling forbids.
    """
    state = _owed_target_state(iteration=1, max_extra_passes=1)

    halted = load_state(await extra_pass_node(dump_state(state)))

    assert [error.error_type for error in halted.errors] == ["graph_invalid_route"]
    assert halted.errors[0].details == {"iteration": 1, "max_extra_passes": 1}
    assert is_halted(halted)
    assert halted.iteration == 1
    assert halted.extra_pass_target_ids == []


@pytest.mark.asyncio
async def test_the_extra_pass_hop_skips_a_halted_run() -> None:
    state = _owed_target_state(errors=[halting_error()])

    skipped = load_state(await extra_pass_node(dump_state(state)))

    assert skipped.iteration == 0
    assert skipped.extra_pass_target_ids == []
    assert _event_types(skipped) == ["graph.node.skipped"]
    assert skipped.events[-1].source == f"graph.{EXTRA_PASS_NODE}"


# --- the terminal publication ------------------------------------------------


_COMPUTED = object()
"""Sentinel: the fixture's own gate snapshot, as the writer node would stamp it."""


def _finalized_state(
    *,
    quality: object = _COMPUTED,
    report_review: object = None,
    errors: list[ResearchError] | None = None,
    iteration: int = 0,
) -> ResearchState:
    """A run that has finished its review pass and is ready to be finalized.

    ``report_review`` defaults to a scored pass, because nothing is accepted
    without one: a state with no review is a state nothing judged, and a test
    that wants that outcome passes ``report_review=`` explicitly.
    """
    overrides: dict[str, object] = {
        "report_review": (
            fake_report_review() if report_review is None else report_review
        ),
        "errors": errors or [],
        "iteration": iteration,
    }
    if quality is not _COMPUTED:
        overrides["quality"] = quality
    return _writer_state(**overrides)


@pytest.mark.asyncio
async def test_the_finalizer_publishes_every_artifact_exactly_once() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state())
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
    assert publisher.document_named("-evidence.md")[1].startswith(
        "# Evidence log:"
    )
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
            dump_state(_finalized_state())
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
    assert record["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert state.report is not None
    assert state.report_evidence is not None


@pytest.mark.asyncio
async def test_the_finalizer_stamps_the_verdict_into_the_published_composition(
) -> None:
    """The published composition states the terminal verdict, not the placeholder."""
    state = _finalized_state()
    publisher = FakePublisher()

    final = load_state(
        await finalize_report_node(publisher)(dump_state(state))
    )

    assert final.composition is not None
    assert final.composition.quality_status == QUALITY_STATUS_ACCEPTED
    record = json.loads(publisher.document_named("-quality.json")[1])
    assert record["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert final.report == publisher.document_named("report-session-1-0.md")[1]


@pytest.mark.asyncio
async def test_the_finalizer_stamps_the_run_telemetry_the_record_renders(
) -> None:
    """The run's collector is read at publication and stamped into state first.

    The quality record describes a run, and the collector is that run's: the
    node that renders the record is the one place the reading is taken, so the
    published JSON and the state cannot disagree about it. A stamp taken after
    the render would publish one reading and store another.
    """
    collector = RunTelemetryCollector()
    collector.record_call(
        agent="report_writer",
        operation="structured_output",
        seconds=3.0,
        output_tokens=900,
        configured_cap=1_000,
        truncated=True,
    )
    publisher = FakePublisher()

    final = load_state(
        await finalize_report_node(publisher, run_telemetry=collector)(
            dump_state(_finalized_state())
        )
    )

    assert final.run_telemetry == collector.snapshot()
    record = json.loads(publisher.document_named("-quality.json")[1])
    assert record["telemetry"] == collector.snapshot().model_dump(mode="json")


@pytest.mark.asyncio
async def test_a_finalizer_without_a_collector_stamps_no_telemetry() -> None:
    """No collector is no measurement: the state and the record both carry
    ``None`` rather than an empty snapshot, which would read as a run whose
    providers never called anything."""
    publisher = FakePublisher()

    final = load_state(
        await finalize_report_node(publisher)(dump_state(_finalized_state()))
    )

    assert final.run_telemetry is None
    assert json.loads(publisher.document_named("-quality.json")[1])["telemetry"] is None


@pytest.mark.asyncio
async def test_the_published_ledger_is_the_finding_log_of_the_published_composition(
) -> None:
    """The evidence artifact is the finding log, rendered from one frozen composition."""
    state = _finalized_state()
    publisher = FakePublisher()

    final = load_state(
        await finalize_report_node(publisher)(dump_state(state))
    )

    assert final.composition is not None
    assert final.report_evidence == render_finding_log(final.composition).strip()
    ledger = publisher.document_named("-evidence.md")[1]
    assert "## Findings" in ledger
    assert SNIPPET in ledger


@pytest.mark.asyncio
async def test_the_published_ledger_carries_the_records_made_after_writing(
) -> None:
    """The ledger's run records are the run's, not the writer's snapshot.

    A composition is built by the Report Writer, so its own error list stops
    there — and every record made after it (the review's "nothing judged this
    report", for one) would be invisible in the published artifacts. The
    finalizer publishes from one frozen composition, so the composition it
    publishes carries the run's final records.
    """
    publisher = FakePublisher()
    state = _finalized_state()
    later = ResearchError(
        error_type="graph_report_review_unavailable",
        source="graph.report_reviewer",
        message="No judgement of the report exists.",
        recoverable=True,
    )

    result = await finalize_report_node(publisher)(
        dump_state(state.model_copy(update={"errors": [later]}))
    )
    final = load_state(result)

    assert final.composition is not None
    assert [error.error_type for error in final.composition.errors] == [
        "graph_report_review_unavailable"
    ]
    record = json.loads(publisher.document_named("-quality.json")[1])
    assert record["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_the_real_writer_publishes_three_artifacts_into_a_real_root(
    tmp_path: Path,
) -> None:
    """The publication path, driven by the real writer's own tools.

    ``WriteDocumentTool`` is the real tool, resolving against ``tmp_path``
    exactly as production resolves against the configured output directory,
    so a file really exists after the run rather than a double claiming one
    does.
    """
    memory = FakeMemory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="nodes-finalizer-test", api_key=None
        )
    )
    writer = ReportWriterAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name=REPORT_WRITER_NAME, max_entries=20
        ),
        tools=report_writer_tools(tracker, output_root=tmp_path, memory=memory),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    # The real ``write_document`` tool opens a child span, which requires the
    # active session span the orchestrator always runs a graph inside.
    async with tracker.session_span("session-1", "How mature is QEC?"):
        result = await finalize_report_node(writer)(
            dump_state(_finalized_state())
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
async def test_an_accepted_report_saves_only_its_cited_findings() -> None:
    publisher = FakePublisher()

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state())
    )

    assert load_state(result).events[-2].metadata["memory_writes"] == 1
    assert publisher.memory_writes == 1
    assert publisher.saved_findings == [SNIPPET]


@pytest.mark.asyncio
async def test_a_partial_report_publishes_artifacts_but_saves_no_finding() -> None:
    """Memory is written only for an accepted report."""
    publisher = FakePublisher()

    state = _finalized_state(
        quality=fake_quality(hard_failures=["unjudged_sentences"]),
        report_review=fake_report_review(
            dimensions={name: 0.5 for name in REVIEW_DIMENSIONS}
        ),
    )

    result = await finalize_report_node(publisher)(dump_state(state))

    assert publisher.report_writes == 3
    assert publisher.memory_writes == 0
    assert load_state(result).events[-2].metadata["quality_status"] == (
        QUALITY_STATUS_PARTIAL
    )


@pytest.mark.asyncio
async def test_an_ungated_report_is_partial_and_saves_no_finding() -> None:
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
    """State holds the artifacts whether or not a file exists.

    And an incomplete set advertises nothing: the reader Markdown is in state,
    the other two files may well be on disk, and no path is published, because
    a front-end pointed at two thirds of a set cannot tell which third is
    missing from the paths alone.
    """
    publisher = FakePublisher(fail_documents=("report-session-1-0.md",))

    result = await finalize_report_node(publisher)(
        dump_state(_finalized_state())
    )
    state = load_state(result)

    assert state.report
    assert state.report_evidence
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
            dump_state(_finalized_state())
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
            dump_state(_finalized_state())
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
    """The terminal event is the only path a front-end may read."""
    publisher = FakePublisher(fail_documents=("report-session-1-",))

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state(iteration=1))
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
async def test_a_failed_memory_write_is_recorded_and_never_hides_the_artifacts(
) -> None:
    publisher = FakePublisher(fail_findings=True)

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(_finalized_state())
        )
    )

    assert state.report_path == "report-session-1-0.md"
    assert state.evidence_path == "report-session-1-0-evidence.md"
    assert state.quality_path == "report-session-1-0-quality.json"
    assert [error.details["artifact"] for error in state.errors] == ["memory"]
    assert state.events[-2].metadata["memory_writes"] == 0


@pytest.mark.asyncio
async def test_a_run_with_no_publisher_writes_nothing_and_says_so() -> None:
    result = await finalize_report_node(None)(
        dump_state(_finalized_state())
    )
    state = load_state(result)

    assert state.report
    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
    assert [error.error_type for error in state.errors] == [
        "graph_publication_unavailable"
    ]
    assert state.errors[0].recoverable is True
    assert state.events[-2].metadata["document_writes"] == 0


@pytest.mark.asyncio
async def test_a_halted_run_is_never_finalized() -> None:
    publisher = FakePublisher()

    state = load_state(
        await finalize_report_node(publisher)(
            dump_state(
                _finalized_state(errors=[halting_error()])
            )
        )
    )

    assert publisher.documents == []
    assert state.report_path is None
    assert _event_types(state) == ["graph.node.skipped"]


def test_a_graph_node_factory_refuses_a_blank_name() -> None:
    with pytest.raises(GraphConfigurationError):
        agent_node(FakeAgent("planner"), node_name="   ")
