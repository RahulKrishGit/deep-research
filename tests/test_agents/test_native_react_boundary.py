"""The one native tool boundary every model-directed agent crosses.

A regression guard, not a unit test of one agent. Each of the four agents that
ask a model to select a tool is run end to end through its own ``run`` — so an
agent that reintroduced a local ``decide`` closure calling
``complete_structured(..., ReActDecision, ...)`` fails here, because
``ScriptedCompleter.complete_structured`` raises for that schema.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from deep_research.agents.critic import CriticAgent, CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    FactCheckerAgent,
    PassageVerdictDraft,
)
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlannerAgent,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.researcher import (
    ResearcherAgent,
    SubTopicFindingsDraft,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers.deepseek_provider import DeepSeekChatProvider
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import Finding, ResearchState, SubTopic
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    critic_tools,
    fact_checker_tools,
    planner_tools,
    research_tools,
)
from tests.test_deepseek_provider import (
    FakeDeepSeekClient,
    RecordingCompletions,
    chat_response,
    deepseek_config,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
FINDING_URL = "https://example.test/qec"

_PLAN = ResearchPlanDraft(
    sub_topics=[
        SubTopicDraft(
            title=f"angle number {index}",
            rationale=f"Rationale for angle number {index}.",
            search_queries=[f"quantum computing angle {index}"],
            success_criteria=[f"evidence about angle number {index}"],
            priority=index,
            evidence_targets=[
                EvidenceTargetDraft(
                    question=(
                        f"What does angle number {index} report, and where?"
                    ),
                    required_dimensions=[f"measure: angle number {index}"],
                    critical=index == 1,
                )
            ],
        )
        for index in (1, 2, 3)
    ]
)


def _finding() -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=FINDING_URL,
        source_title="QEC results",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="angle number 1",
    )


def _state(**updates: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "how much capacity can quantum computing reach",
        "max_iterations": 2,
    }
    payload.update(updates)
    return ResearchState.model_validate(payload)


def _planner_case() -> tuple[ResearchState, list, list]:
    return (
        _state(),
        [finish("Enough context.", "Scoping is complete.")],
        [
            _PLAN,
            PlanReviewDraft(
                sound=True,
                missing_dimensions=[],
                atomicity_defects=[],
                unsupported_premises=[],
                repair_instruction="",
            ),
        ],
    )


def _researcher_case() -> tuple[ResearchState, list, list]:
    state = _state(
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="angle number 1",
                rationale="Rationale for angle number 1.",
                search_queries=["quantum computing angle 1"],
                success_criteria=["evidence about angle number 1"],
                priority=1,
            )
        ]
    )
    return (
        state,
        [finish("Nothing to add.", "The evidence is sufficient.")],
        [SubTopicFindingsDraft(findings=[])],
    )


def _fact_checker_case() -> tuple[ResearchState, list, list]:
    state = _state(raw_findings=[_finding()])
    return (
        state,
        [finish("Nothing independent.", "Nothing independent was retrieved.")],
        [
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Logical error rates fell below break-even.",
                        source_urls=[FINDING_URL],
                    )
                ]
            ),
            PassageVerdictDraft(
                verdict="insufficient_evidence",
                confidence=0.0,
                passages=[],
            ),
        ],
    )


def _critic_case() -> tuple[ResearchState, list, list]:
    state = _state(
        report="# Research report: the measured capacity is reported.",
        iteration=1,
    )
    return (
        state,
        [finish("The report is enough.", "No spot check needed.")],
        [
            CritiqueDraft(
                score=8,
                gaps=[],
                unsupported_claims=[],
                recommended_queries=[],
                rationale="The report answers the question from cited sources.",
            )
        ],
    )


TOOL_SELECTING_AGENTS: tuple[
    tuple[type, Callable[[Tracker], list[BaseTool]], Callable[[], tuple]], ...
] = (
    (PlannerAgent, planner_tools, _planner_case),
    (ResearcherAgent, research_tools, _researcher_case),
    (FactCheckerAgent, fact_checker_tools, _fact_checker_case),
    (CriticAgent, critic_tools, _critic_case),
)

AGENT_IDS = [agent_class.name for agent_class, _, _ in TOOL_SELECTING_AGENTS]


def _agent(
    agent_class: type,
    tracker: Tracker,
    completer: ScriptedCompleter,
    tools: list[BaseTool],
):
    return agent_class(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name=agent_class.name,
            max_entries=20,
        ),
        tools=tools,
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=2),
    )


@pytest.mark.parametrize(
    ("agent_class", "tool_factory", "case_factory"),
    TOOL_SELECTING_AGENTS,
    ids=AGENT_IDS,
)
@pytest.mark.asyncio
async def test_every_tool_selecting_agent_runs_its_loop_on_native_turns(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    case_factory: Callable[[], tuple],
    tracker: Tracker,
) -> None:
    state, decisions, outputs = case_factory()
    completer = ScriptedCompleter(decisions=decisions, outputs=outputs)
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", state.original_question):
        await agent.run(state)

    # The loop really ran, and it asked the provider for native turns.
    assert completer.react_calls
    assert completer.react_calls[0].agent_name == agent_class.name
    assert [definition.name for definition in completer.react_calls[0].tools] == list(
        agent_class.allowed_tools
    )
    # Nothing on this path ever requested the old structured decision schema.
    assert all(schema_name != "ReActDecision" for schema_name, _, _ in completer.calls)
    # And the tools were never advertised in the prompt text.
    body = completer.react_calls[0].messages[1].content
    assert "## Tools" not in body
    assert "tool_input_json" not in body


@pytest.mark.parametrize(
    ("agent_class", "tool_factory", "case_factory"),
    TOOL_SELECTING_AGENTS,
    ids=AGENT_IDS,
)
@pytest.mark.asyncio
async def test_a_native_tool_call_crosses_the_boundary_and_is_executed(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    case_factory: Callable[[], tuple],
    tracker: Tracker,
) -> None:
    """A native call reaches the loop as a real, allow-listed tool execution."""
    state, _, outputs = case_factory()
    tool_name = agent_class.allowed_tools[0]
    arguments = {
        "query_memory": '{"query": "quantum computing"}',
        "web_search": '{"query": "quantum computing"}',
    }.get(tool_name, "{}")
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Look it up.", tool_name, arguments),
            finish("Enough.", "The evidence is sufficient."),
        ],
        outputs=outputs,
    )
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.react.tool_calls == 1
    tool_steps = [step for step in outcome.react.steps if step.tool_name is not None]
    assert [step.tool_name for step in tool_steps] == [tool_name]
    assert tool_steps[0].thought == "Selected tool through provider-native calling."


# --- the malformed-text boundary, through the real provider parser -----------

BOUNDARY_SENTINEL = "NATIVE_BOUNDARY_SENTINEL_5EA1"

# Each template stays a valid instance of its own prohibited shape while
# carrying the sentinel, so "no content crossed the boundary" is a real check.
BOUNDARY_PROHIBITED_TEXT: tuple[tuple[str, str], ...] = (
    (
        "dsml-markup",
        '<|DSML|tool_calls><|DSML|invoke name="web_search">'
        '{"query":"<S>"}</|DSML|invoke></|DSML|tool_calls>',
    ),
    (
        "tool-call-tag",
        '<tool_call>{"name": "web_search", "note": "<S>"}</tool_call>',
    ),
    (
        "invoke-tag",
        '<invoke name="web_search">{"query": "<S>"}</invoke>',
    ),
    (
        "fenced-legacy-action",
        "```json\n"
        '{"action": "use_tool", "tool_name": "web_search", '
        '"tool_input_json": "{}", "note": "<S>"}\n'
        "```",
    ),
    (
        "bare-legacy-action-object",
        '{"action": "use_tool", "tool_name": "web_search", '
        '"tool_input_json": "{}", "note": "<S>"}',
    ),
    ("bare-tool-name-object", '{"tool_name": "web_search", "note": "<S>"}'),
    (
        "bare-tool-input-object",
        '{"tool_input_json": "{\\"query\\": \\"<S>\\"}"}',
    ),
)


def _researcher_over(
    tracker: Tracker,
    completions: RecordingCompletions,
) -> ResearcherAgent:
    """A real agent over the real native parser and a scripted provider body.

    The parser is the production one on purpose: a fake that raised the typed
    error itself would prove only that the loop handles an error it was handed,
    not that malformed provider text ever becomes one.
    """
    return ResearcherAgent(
        provider=DeepSeekChatProvider(
            deepseek_config(),
            tracker,
            client=FakeDeepSeekClient(completions),
        ),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name=ResearcherAgent.name,
            max_entries=20,
        ),
        tools=research_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=2),
    )


@pytest.mark.parametrize(
    ("shape", "template"),
    BOUNDARY_PROHIBITED_TEXT,
    ids=[shape for shape, _ in BOUNDARY_PROHIBITED_TEXT],
)
@pytest.mark.asyncio
async def test_malformed_final_text_never_becomes_a_finish_decision(
    shape: str,
    template: str,
    tracker: Tracker,
) -> None:
    """A prohibited final-text shape aborts the agent instead of finishing it.

    The regression this guards: DSML markup, a fenced or bare legacy action
    object, and tool markup were each accepted as a legitimate final answer, so
    an agent reported a normal ``finish`` over a rejected tool invocation.
    """
    text = template.replace("<S>", BOUNDARY_SENTINEL)
    completions = RecordingCompletions(
        chat_response(text=text, finish_reason="stop")
    )
    state, _, _ = _researcher_case()
    agent = _researcher_over(tracker, completions)

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    # No tool ran, no second turn was spent, and no repair was attempted.
    assert completions.calls
    assert len(completions.calls) == 1
    assert outcome.react.tool_calls == 0
    assert outcome.react.steps == []
    assert outcome.react.final_answer is None
    assert outcome.react.stop_reason == "provider_error"
    assert all(step.action != "finish" for step in outcome.react.steps)
    assert outcome.result is not None
    assert outcome.result.findings == []

    # The failure is the typed, content-free fallback, not a bare exception.
    assert [error.error_type for error in outcome.errors] == [
        "agent_provider_error"
    ]
    provider_error = outcome.errors[0]
    assert provider_error.recoverable is False
    assert provider_error.details["operation"] == "react_decision"
    failure = provider_error.details["provider_failure"]
    assert failure["kind"] == "provider_response"
    assert failure["failure_origin"] == "local_response"
    assert failure["exception_type"] == "ProviderResponseError"
    assert BOUNDARY_SENTINEL not in repr(outcome.errors)
    assert BOUNDARY_SENTINEL not in repr(outcome.state_update)


@pytest.mark.asyncio
async def test_legitimate_final_text_still_finishes_the_agent(
    tracker: Tracker,
) -> None:
    """The positive control for the shape rejection above.

    Without this, a boundary that rejected every final answer would satisfy
    the malformed-text test.
    """
    answer = "Nothing to add. The supplied evidence already answers the question."
    completions = RecordingCompletions(
        chat_response(text=answer, finish_reason="stop")
    )
    state, _, _ = _researcher_case()
    agent = _researcher_over(tracker, completions)

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert len(completions.calls) == 1
    assert outcome.react.stop_reason == "finished"
    assert outcome.react.tool_calls == 0
    assert outcome.react.final_answer == answer
    assert outcome.react.steps[-1].action == "finish"
