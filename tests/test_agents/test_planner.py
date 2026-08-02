"""Tests for the Planner's plan contracts, validation, and prompts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.errors import PlanningError
from deep_research.agents.planner import (
    MAX_SUB_TOPICS,
    MIN_SUB_TOPICS,
    PlannerAgent,
    ResearchPlan,
    ResearchPlanDraft,
    SubTopicDraft,
    format_plan_problems,
    plan_messages,
    validate_plan_draft,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    MemorySnapshot,
    ResearchState,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeMemory,
    FakeSearchClient,
    planner_tools,
    research_tools,
)


def _draft(
    title: str = "Error correction",
    *,
    priority: int = 1,
    search_queries: list[str] | None = None,
    success_criteria: list[str] | None = None,
) -> SubTopicDraft:
    return SubTopicDraft(
        title=title,
        rationale=f"{title} is load-bearing for the answer.",
        search_queries=(
            ["qec benchmarks 2025"] if search_queries is None else search_queries
        ),
        success_criteria=(
            ["A benchmark with a named source."]
            if success_criteria is None
            else success_criteria
        ),
        priority=priority,
    )


def _plan(*titles: str) -> ResearchPlanDraft:
    return ResearchPlanDraft(
        sub_topics=[
            _draft(title, priority=index)
            for index, title in enumerate(titles, start=1)
        ]
    )


def _run(*, observation: str | None = None, answer: str | None = None) -> ReActRun:
    steps: list[ReActStep] = []
    if observation is not None:
        steps.append(
            ReActStep(
                iteration=1,
                thought="Scope the question.",
                action="use_tool",
                tool_name="web_search",
                observation=ReActObservation(
                    tool_name="web_search",
                    success=True,
                    summary=observation,
                ),
            )
        )
    return ReActRun(
        agent_name="planner",
        steps=steps,
        stop_reason="finished",
        iterations=len(steps),
        final_answer=answer,
    )


def test_plan_size_bounds_match_the_spec() -> None:
    assert MIN_SUB_TOPICS == 3
    assert MAX_SUB_TOPICS == 7


def test_valid_plan_converts_every_draft_into_a_sub_topic() -> None:
    sub_topics, problems = validate_plan_draft(_plan("Alpha", "Beta", "Gamma"))

    assert problems == []
    assert [sub_topic.title for sub_topic in sub_topics] == [
        "Alpha",
        "Beta",
        "Gamma",
    ]
    assert sub_topics[0].priority == 1
    assert sub_topics[0].search_queries == ["qec benchmarks 2025"]


def test_an_empty_plan_is_rejected() -> None:
    sub_topics, problems = validate_plan_draft(ResearchPlanDraft(sub_topics=[]))

    assert sub_topics == []
    assert problems == [
        "the plan has 0 valid sub-topics; produce between 3 and 7"
    ]


def test_a_plan_with_too_few_sub_topics_is_rejected() -> None:
    _, problems = validate_plan_draft(_plan("Alpha", "Beta"))

    assert problems == [
        "the plan has 2 valid sub-topics; produce between 3 and 7"
    ]


def test_a_plan_with_too_many_sub_topics_is_rejected() -> None:
    _, problems = validate_plan_draft(
        _plan("A", "B", "C", "D", "E", "F", "G", "H")
    )

    assert problems == [
        "the plan has 8 valid sub-topics; produce between 3 and 7"
    ]


def test_redundant_sub_topics_are_rejected_ignoring_case_and_spacing() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Error correction", priority=1),
            _draft("  ERROR   CORRECTION ", priority=2),
            _draft("Hardware roadmaps", priority=3),
        ]
    )

    _, problems = validate_plan_draft(draft)

    assert problems == [
        "sub-topics 1 and 2 repeat the same title; every sub-topic must be "
        "distinct"
    ]


def test_a_sub_topic_missing_its_queries_is_reported_by_field() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Alpha", priority=1, search_queries=[]),
            _draft("Beta", priority=2),
            _draft("Gamma", priority=3),
        ]
    )

    sub_topics, problems = validate_plan_draft(draft)

    assert [sub_topic.title for sub_topic in sub_topics] == ["Beta", "Gamma"]
    assert problems == [
        "sub-topic 1 is invalid: check these fields: search_queries",
        "the plan has 2 valid sub-topics; produce between 3 and 7",
    ]


def test_duplicate_indices_refer_to_draft_positions_not_valid_ones() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Bad", priority=1, search_queries=[]),
            _draft("Alpha", priority=2),
            _draft("Alpha", priority=3),
            _draft("Beta", priority=4),
        ]
    )
    _, problems = validate_plan_draft(draft)
    assert problems == [
        "sub-topic 1 is invalid: check these fields: search_queries",
        "sub-topics 2 and 3 repeat the same title; every sub-topic must be "
        "distinct",
    ]


def test_a_sub_topic_with_a_zero_priority_is_reported_by_field() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Alpha", priority=0),
            _draft("Beta", priority=2),
            _draft("Gamma", priority=3),
        ]
    )

    _, problems = validate_plan_draft(draft)

    assert problems[0] == (
        "sub-topic 1 is invalid: check these fields: priority"
    )


def test_problems_render_as_one_corrective_instruction() -> None:
    rendered = format_plan_problems(["problem one", "problem two"])

    assert rendered.startswith("The previous plan was rejected.")
    assert "- problem one" in rendered
    assert "- problem two" in rendered


def test_plan_messages_carry_question_notes_and_requirements() -> None:
    messages = plan_messages(
        AgentTask(
            instruction="What are the security implications of quantum computing?",
            guidance="2 finding(s) recalled from previous sessions:",
        ),
        _run(observation="web_search succeeded: 3 results", answer="Enough."),
    )

    assert len(messages) == 2
    assert messages[0].role == "developer"
    body = messages[1].content
    assert "What are the security implications of quantum computing?" in body
    assert "2 finding(s) recalled from previous sessions:" in body
    assert "- web_search succeeded: 3 results" in body
    assert "- Enough." in body
    assert "between 3 and 7" in body
    assert "## Repair" not in body


def test_plan_messages_report_when_nothing_was_scoped() -> None:
    messages = plan_messages(AgentTask(instruction="Question?"), _run())

    assert "(no scoping notes)" in messages[1].content


def test_plan_messages_append_the_repair_section_only_when_given() -> None:
    messages = plan_messages(
        AgentTask(instruction="Question?"),
        _run(),
        repair="The previous plan was rejected.\n- problem one",
    )

    assert "## Repair" in messages[1].content
    assert "- problem one" in messages[1].content


def test_the_validated_plan_enforces_its_own_size_bounds() -> None:
    sub_topics, _ = validate_plan_draft(_plan("Alpha", "Beta", "Gamma"))

    assert ResearchPlan(sub_topics=sub_topics).repair_attempted is False
    with pytest.raises(ValidationError):
        ResearchPlan(sub_topics=sub_topics[:1])


def _state(
    question: str = "What are the security implications of quantum computing?",
    *,
    memory_context: MemorySnapshot | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=question,
        memory_context=memory_context or MemorySnapshot(),
    )


def _planner(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
) -> PlannerAgent:
    return PlannerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker, search=search, memory=memory),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )


def test_the_planner_declares_its_identity_and_tools() -> None:
    assert PlannerAgent.name == "planner"
    assert PlannerAgent.allowed_tools == ("query_memory", "web_search")


def test_build_task_carries_the_question_and_recalled_memory(
    tracker: Tracker,
) -> None:
    agent = _planner(tracker, ScriptedCompleter())
    state = _state(
        memory_context=MemorySnapshot(
            similar_findings=[
                Finding(
                    content="Shor's algorithm breaks RSA.",
                    source_url="https://example.test/shor",
                    source_title="Shor 1994",
                    extracted_at="2026-01-01T00:00:00+00:00",
                    confidence=0.9,
                    related_sub_topic="Cryptography",
                )
            ]
        )
    )

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert "1 finding(s) recalled from previous sessions:" in task.guidance
    assert "Shor's algorithm breaks RSA." in task.guidance


@pytest.mark.asyncio
async def test_the_planner_turns_a_question_into_a_validated_plan(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Recall prior work.", "query_memory", '{"query": "quantum"}'),
            finish("I understand the question.", "Three angles matter."),
        ],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.repair_attempted is False
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Cryptography",
        "Hardware timelines",
        "Mitigations",
    ]
    assert outcome.state_update["sub_topics"] == outcome.result.sub_topics
    assert outcome.state_update["errors"] == []


@pytest.mark.asyncio
async def test_the_plan_merges_into_research_state(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())
    state = merge_research_state(_state(), outcome.state_update)

    assert len(state.sub_topics) == 3
    assert len(state.events) == 3


@pytest.mark.asyncio
async def test_the_planner_emits_start_recall_and_completion_events(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)
    state = _state(
        memory_context=MemorySnapshot(suggested_strategies=["Prefer 2025 sources."])
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "planner.planning.started",
        "planner.memory.recalled",
        "planner.planning.completed",
    ]
    assert all(event.source == "agent.planner" for event in events)
    assert events[1].metadata["recalled_findings"] == 0
    assert events[1].metadata["suggested_strategies"] == 1
    assert events[2].metadata["sub_topic_count"] == 3
    assert events[2].metadata["repair_attempted"] is False
    assert events[2].metadata["stop_reason"] == "finished"


@pytest.mark.asyncio
async def test_a_redundant_plan_is_repaired_once_and_then_accepted(
    tracker: Tracker,
) -> None:
    redundant = ResearchPlanDraft(
        sub_topics=[
            _draft("Cryptography", priority=1),
            _draft("cryptography", priority=2),
        ]
    )
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            redundant,
            _plan("Cryptography", "Hardware timelines", "Mitigations"),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.repair_attempted is True
    repair_body = completer.calls[-1][2][1].content
    assert "## Repair" in repair_body
    assert "repeat the same title" in repair_body
    assert "produce between 3 and 7" in repair_body


@pytest.mark.asyncio
async def test_a_plan_that_stays_invalid_fails_the_session(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[ResearchPlanDraft(sub_topics=[]), _plan("Only one")],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        "the plan has 1 valid sub-topics; produce between 3 and 7",
    )


@pytest.mark.asyncio
async def test_a_provider_failure_fails_the_session_without_a_plan_request(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(decisions=[ProviderTimeoutError("timed out")])
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError, match="model provider"):
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert [call[0] for call in completer.calls] == ["ReActDecision"]


@pytest.mark.asyncio
async def test_a_provider_failure_during_the_initial_plan_draft_raises_planning_error(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[ProviderTimeoutError("timed out")],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError, match="model provider") as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert "timed out" not in str(failure.value)
    assert [call[0] for call in completer.calls] == [
        "ReActDecision",
        "ResearchPlanDraft",
    ]


@pytest.mark.asyncio
async def test_a_provider_failure_during_the_repair_call_raises_planning_error(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            ResearchPlanDraft(sub_topics=[]),
            ProviderTimeoutError("timed out"),
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError, match="model provider") as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert "timed out" not in str(failure.value)
    assert [call[0] for call in completer.calls] == [
        "ReActDecision",
        "ResearchPlanDraft",
        "ResearchPlanDraft",
    ]


@pytest.mark.asyncio
async def test_a_search_failure_does_not_stop_the_planner(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Scope the terms.", "web_search", '{"query": "quantum"}'),
            finish("Enough context.", "Three angles matter."),
        ],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(
        tracker,
        completer,
        search=FakeSearchClient([RuntimeError("tavily is down")]),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert [error.error_type for error in outcome.errors] == ["agent_tool_failed"]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_document_reader_succeeds_through_research_tools_defaults(
    tracker: Tracker,
) -> None:
    """A Task-4 author must be able to exercise document_reader offline.

    ``research_tools()``'s default client only served HTML, which
    ``DocumentReaderTool`` cannot parse. It now also serves content-type-
    aware bodies by request suffix, so a plain URL ending ``.json`` (or
    ``.csv``/``.md``) succeeds.
    """
    tools = {tool.name: tool for tool in research_tools(tracker)}
    document_reader = tools["document_reader"]

    async with tracker.session_span("session-1", "q"):
        result = await document_reader.execute(
            source="https://example.test/notes.json"
        )

    assert result.success is True
    assert result.data is not None
    assert result.data["format"] == "json"
    assert result.data["chunks"] != []
    assert result.data["failures"] == []
