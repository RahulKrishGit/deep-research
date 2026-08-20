"""Tests for the Critic's score clamping, routing maths, and prompts."""

from __future__ import annotations

import pytest

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    MAX_CRITIC_SCORE,
    MIN_CRITIC_SCORE,
    ROUTING_REASONS,
    CriticAgent,
    CritiqueDraft,
    CritiqueTask,
    build_critique,
    clamp_score,
    critique_messages,
    fallback_critique,
    normalize_notes,
    route_decision,
)
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    Critique,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import FakeSearchClient, critic_tools

CRITIC_SOURCE_URL = "https://example.org/a"


def _source(*, low_confidence: bool = False) -> ScoredSource:
    return ScoredSource(
        url=CRITIC_SOURCE_URL,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(*, verdict: str = "verified") -> Claim:
    return Claim(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=[CRITIC_SOURCE_URL],
        verdict=verdict,
        confidence=0.8,
        evidence=[],
        contradictions=[],
    )


def _draft(
    *,
    score: int = 8,
    gaps: list[str] | None = None,
    unsupported: list[str] | None = None,
    queries: list[str] | None = None,
    rationale: str = "Well sourced and complete.",
) -> CritiqueDraft:
    return CritiqueDraft(
        score=score,
        gaps=gaps or [],
        unsupported_claims=unsupported or [],
        recommended_queries=queries or [],
        rationale=rationale,
    )


def _task(**overrides: object) -> CritiqueTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "report": "# Research report: How mature is quantum error correction?",
        "iteration": 0,
        "max_iterations": 3,
        "claims": [_claim()],
        "sources": [_source()],
        "sub_topics": ["Alpha"],
        "error_count": 2,
    }
    payload.update(overrides)
    return CritiqueTask.model_validate(payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-4, 1), (0, 1), (1, 1), (7, 7), (10, 10), (99, 10)],
)
def test_scores_are_pinned_into_the_critic_score_range(
    raw: int, expected: int
) -> None:
    assert clamp_score(raw) == expected
    assert MIN_CRITIC_SCORE <= clamp_score(raw) <= MAX_CRITIC_SCORE


def test_notes_are_collapsed_deduplicated_and_capped() -> None:
    notes = normalize_notes(
        ["  No  cost data. ", "No cost data.", "", "   ", "No vendor audit."],
        limit=5,
    )

    assert notes == ["No cost data.", "No vendor audit."]
    assert normalize_notes(["a", "b", "c"], limit=2) == ["a", "b"]
    with pytest.raises(ValueError, match="limit"):
        normalize_notes(["a"], limit=0)


def test_an_acceptable_report_ends_the_run() -> None:
    assert route_decision(
        score=ACCEPTANCE_SCORE,
        gaps=[],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (False, "accepted_quality")


@pytest.mark.parametrize(
    ("score", "gaps", "unsupported", "reason"),
    [
        (6, [], [], "low_score"),
        (9, ["No cost data."], [], "critical_gaps"),
        (9, [], ["Costs fell tenfold."], "unsupported_claims"),
    ],
)
def test_a_weak_report_continues_with_the_reason_that_applies(
    score: int, gaps: list[str], unsupported: list[str], reason: str
) -> None:
    assert route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (True, reason)


def test_the_iteration_bound_beats_every_quality_signal() -> None:
    assert route_decision(
        score=1,
        gaps=["Everything is missing."],
        unsupported_claims=["All of it."],
        iteration=3,
        max_iterations=3,
        has_report=True,
    ) == (False, "max_iterations_reached")


def test_a_missing_report_continues_while_budget_remains() -> None:
    assert route_decision(
        score=10,
        gaps=[],
        unsupported_claims=[],
        iteration=1,
        max_iterations=3,
        has_report=False,
    ) == (True, "missing_report")


def test_a_critique_is_validated_clamped_and_routed() -> None:
    critique, reason = build_critique(
        _draft(
            score=99,
            gaps=["  No cost data. ", "No cost data."],
            queries=["qec cost 2025"],
        ),
        iteration=0,
        max_iterations=3,
    )

    assert isinstance(critique, Critique)
    assert critique.score == MAX_CRITIC_SCORE
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]
    assert critique.should_continue is True
    assert reason == "critical_gaps"
    assert critique.rationale.startswith("Well sourced and complete.")
    assert ROUTING_REASONS["critical_gaps"] in critique.rationale


def test_a_blank_model_rationale_still_yields_a_usable_one() -> None:
    critique, reason = build_critique(
        _draft(rationale="   "), iteration=0, max_iterations=3
    )

    assert critique.rationale == ROUTING_REASONS["accepted_quality"]
    assert reason == "accepted_quality"
    assert critique.should_continue is False


def test_the_last_iteration_stops_even_on_a_scathing_critique() -> None:
    critique, reason = build_critique(
        _draft(score=2, gaps=["No cost data."]),
        iteration=3,
        max_iterations=3,
    )

    assert critique.should_continue is False
    assert reason == "max_iterations_reached"
    assert critique.gaps == ["No cost data."]
    assert ROUTING_REASONS["max_iterations_reached"] in critique.rationale


def test_a_provider_outage_never_buys_another_research_cycle() -> None:
    critique, reason = fallback_critique(
        reason="provider_unavailable", iteration=0, max_iterations=3
    )

    assert critique.score == MIN_CRITIC_SCORE
    assert critique.should_continue is False
    assert reason == "provider_unavailable"
    assert critique.rationale == ROUTING_REASONS["provider_unavailable"]


def test_a_missing_report_is_worth_one_more_cycle() -> None:
    critique, reason = fallback_critique(
        reason="missing_report", iteration=0, max_iterations=3
    )

    assert critique.should_continue is True
    assert reason == "missing_report"
    assert critique.gaps == ["No report was available to review."]

    exhausted, exhausted_reason = fallback_critique(
        reason="missing_report", iteration=3, max_iterations=3
    )
    assert exhausted.should_continue is False
    assert exhausted_reason == "max_iterations_reached"


def test_fallback_critique_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_critique(reason="because", iteration=0, max_iterations=3)


def test_critique_messages_carry_the_report_and_every_quality_signal() -> None:
    messages = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "## Research question" in body
    assert "## Report under review" in body
    assert "# Research report:" in body
    assert "## Sub-topics planned" in body
    assert "- Alpha" in body
    assert "## Claim verdicts" in body
    assert "[verified 0.80]" in body
    assert "## Source quality" in body
    assert "## Recorded problems" in body
    assert "2 error(s)" in body
    assert "## Spot checks" in body
    assert "## Response contract" in body


def test_critique_messages_clamp_a_long_report_without_flattening_it() -> None:
    report = "# Title\n\n" + ("x" * 500)
    body = critique_messages(
        _task(report=report),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "# Title\n" in body
    assert "x" * 500 not in body
    assert "..." in body


def test_critique_messages_say_so_when_there_is_no_report() -> None:
    body = critique_messages(
        _task(report="   "),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "(no report)" in body


def _critic(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[BaseTool] | None = None,
    tool_budget: int = 0,
) -> CriticAgent:
    return CriticAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="critic", max_entries=20
        ),
        tools=tools if tools is not None else critic_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=tool_budget),
    )


def _critic_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "sub_topics": [
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
        "report": "# Research report: How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def test_build_task_carries_the_report_budget_and_quality_signals(
    tracker: Tracker,
) -> None:
    agent = _critic(tracker, ScriptedCompleter())
    state = _critic_state(iteration=2, max_iterations=3)

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert task.report == state.report
    assert task.iteration == 2
    assert task.max_iterations == 3
    assert task.sub_topics == ["Alpha"]
    assert len(task.claims) == 1
    assert len(task.sources) == 1
    assert task.error_count == 0


@pytest.mark.asyncio
async def test_an_acceptable_report_ends_the_graph(tracker: Tracker) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(decisions=[], outputs=[_draft(score=9)])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == 9
    assert outcome.state_update["critique"] == critique
    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_low_quality_report_asks_for_another_pass(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=4,
                    gaps=["No cost data."],
                    unsupported=["Costs fell tenfold."],
                    queries=["qec cost 2025"],
                    rationale="One source carries the whole argument.",
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]


@pytest.mark.asyncio
async def test_the_final_iteration_forces_a_stop(tracker: Tracker) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(outputs=[_draft(score=2, gaps=["No cost data."])]),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(iteration=3, max_iterations=3))

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.gaps == ["No cost data."]
    event = outcome.state_update["events"][-1]
    assert event.metadata["reason"] == "max_iterations_reached"
    assert event.metadata["should_continue"] is False


@pytest.mark.asyncio
async def test_a_run_emits_the_routing_counts_the_spec_requires(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=5,
                    gaps=["No cost data.", "No vendor audit."],
                    unsupported=["Costs fell tenfold."],
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "critic.critique.started",
        "critic.critique.completed",
    ]
    completed = events[-1].metadata
    assert completed["score"] == 5
    assert completed["gap_count"] == 2
    assert completed["unsupported_claim_count"] == 1
    assert completed["should_continue"] is True
    assert completed["reason"] == "low_score"
    assert completed["iteration"] == 0
    assert completed["max_iterations"] == 3


@pytest.mark.asyncio
async def test_a_missing_report_is_recorded_without_a_provider_call(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(report=None))

    assert completer.calls == []
    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.score == MIN_CRITIC_SCORE
    assert [error.error_type for error in outcome.errors] == [
        "critic_missing_report"
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_still_routes_and_stops(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(outputs=[ProviderError("down")])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == MIN_CRITIC_SCORE
    assert outcome.react.stop_reason == "provider_error"
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    assert error.recoverable is False
    assert error.details == {"exception_type": "ProviderError"}


@pytest.mark.asyncio
async def test_a_spot_check_reaches_the_review_prompt(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Check the cost figure.",
                "web_search",
                '{"query": "qec cost 2025"}',
            ),
            finish("Enough to judge.", "The cost figure checks out."),
        ],
        outputs=[_draft(score=9)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(tracker),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.tool_calls == 1
    review_call = next(
        call for call in completer.calls if call[0] == "CritiqueDraft"
    )
    assert "[web_search]" in review_call[2][1].content
    event = outcome.state_update["events"][-1]
    assert event.metadata["tool_calls"] == 1


@pytest.mark.asyncio
async def test_a_failing_spot_check_never_stops_the_review(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search first.", "web_search", '{"query": "qec"}'),
            finish("Judge without it.", "The search failed."),
        ],
        outputs=[_draft(score=8)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(
            tracker, search=FakeSearchClient([RuntimeError("tavily down")])
        ),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.score == 8
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_finalize_requires_a_critique_task(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(outputs=[_draft()]))

    with pytest.raises(AgentConfigurationError, match="CritiqueTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="critic", stop_reason="finished"),
        )


def test_the_critic_declares_its_spot_check_tools(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(), tools=critic_tools(tracker))

    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ("web_search", "query_memory")
    assert agent.output_schema is Critique
