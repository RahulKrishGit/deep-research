"""Tests for the Planner's plan contracts, validation, and prompts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from deep_research.agents.errors import PlanningError
from deep_research.agents.planner import (
    MAX_SUB_TOPICS,
    MIN_SUB_TOPICS,
    PLAN_INSTRUCTION,
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
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    MemorySnapshot,
    ResearchState,
    SubTopic,
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


# Three distinct mechanisms, drafted out of priority order. Ordering is only
# observable once a draft disagrees with it, which is exactly what the plan
# instruction asks the model for and the model does not always produce.
_SCRAMBLED_PLAN_TITLES = (
    ("Market rules", 3),
    ("Grid connection", 1),
    ("Siting and safety", 2),
)

_ORDERED_PLAN_TITLES = ("Grid connection", "Siting and safety", "Market rules")


def _scrambled_plan() -> ResearchPlanDraft:
    return ResearchPlanDraft(
        sub_topics=[
            _draft(title, priority=priority)
            for title, priority in _SCRAMBLED_PLAN_TITLES
        ]
    )


def test_validated_sub_topics_are_priority_ordered_and_carry_stable_ids() -> None:
    sub_topics, problems = validate_plan_draft(_scrambled_plan())

    assert problems == []
    assert [sub_topic.title for sub_topic in sub_topics] == list(
        _ORDERED_PLAN_TITLES
    )
    assert [sub_topic.coverage_id for sub_topic in sub_topics] == [
        "topic-01",
        "topic-02",
        "topic-03",
    ]


def test_equal_priorities_keep_the_models_order_and_the_same_ids() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft(title, priority=1) for title in _ORDERED_PLAN_TITLES
        ]
    )

    first, first_problems = validate_plan_draft(draft)
    again, again_problems = validate_plan_draft(draft)

    assert first_problems == again_problems == []
    assert [sub_topic.title for sub_topic in first] == list(_ORDERED_PLAN_TITLES)
    assert [sub_topic.coverage_id for sub_topic in first] == [
        f"topic-{index:02d}" for index in (1, 2, 3)
    ]
    assert [sub_topic.coverage_id for sub_topic in again] == [
        sub_topic.coverage_id for sub_topic in first
    ]


def test_a_repair_pass_produces_the_same_ids_for_the_same_ordered_titles() -> None:
    """A repair keeps the ids a surviving title already had.

    The first draft is rejected for carrying too many sub-topics, so the
    repair returns the fewest, most important ones. Their ids are a pure
    function of their position in priority order, so nothing is renumbered.
    """
    rejected = ResearchPlanDraft(
        sub_topics=[
            _draft(f"Mechanism {name}", priority=index)
            for index, name in enumerate("ABCDEFGH", start=1)
        ]
    )
    repaired = _plan("Mechanism A", "Mechanism B", "Mechanism C")

    rejected_sub_topics, rejected_problems = validate_plan_draft(rejected)
    repaired_sub_topics, repaired_problems = validate_plan_draft(repaired)

    assert rejected_problems == [
        "the plan has 8 valid sub-topics; produce between 3 and 7"
    ]
    assert repaired_problems == []
    kept = {
        sub_topic.title: sub_topic.coverage_id
        for sub_topic in rejected_sub_topics
        if sub_topic.title in {"Mechanism A", "Mechanism B", "Mechanism C"}
    }
    assert kept == {
        sub_topic.title: sub_topic.coverage_id
        for sub_topic in repaired_sub_topics
    }
    assert kept == {
        "Mechanism A": "topic-01",
        "Mechanism B": "topic-02",
        "Mechanism C": "topic-03",
    }


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
    assert "# Repair" not in body


def test_plan_messages_report_when_nothing_was_scoped() -> None:
    messages = plan_messages(AgentTask(instruction="Question?"), _run())

    assert "(no scoping notes)" in messages[1].content


def test_plan_messages_append_the_repair_section_only_when_given() -> None:
    messages = plan_messages(
        AgentTask(instruction="Question?"),
        _run(),
        repair="The previous plan was rejected.\n- problem one",
    )

    assert "# Repair" in messages[1].content
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
    config: AgentRuntimeConfig | None = None,
) -> PlannerAgent:
    return PlannerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker, search=search, memory=memory),
        config=config or AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )


def test_the_planner_declares_its_identity_and_tools() -> None:
    assert PlannerAgent.name == "planner"
    assert PlannerAgent.allowed_tools == ("query_memory", "web_search")


def test_the_plan_request_is_tool_free_while_the_loop_prompt_is_tool_aware(
    tracker: Tracker,
) -> None:
    """Transport and prompt must agree.

    ``plan_messages`` builds the separate structured plan call, which sends no
    tools. Reusing the ReAct system prompt there announced ``query_memory`` and
    ``web_search`` to a request that could not accept them — the measured cause
    of DeepSeek emitting tool markup into ordinary text. The loop prompt keeps
    naming them, because that request really does carry the tools.
    """
    task = AgentTask(instruction="How much capacity can QEC reach?")
    messages = plan_messages(
        task, ReActRun(agent_name="planner", stop_reason="finished")
    )

    developer = messages[0].content
    assert "query_memory" not in developer
    assert "web_search" not in developer

    loop_prompt = _planner(tracker, ScriptedCompleter()).system_prompt(task)
    assert "query_memory" in loop_prompt
    assert "web_search" in loop_prompt


@pytest.mark.asyncio
async def test_the_plan_reaching_state_carries_coverage_ids_in_priority_order(
    tracker: Tracker,
) -> None:
    """The ids a later stage reads are the ones the planner stamped."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_scrambled_plan()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert [
        (sub_topic.coverage_id, sub_topic.title)
        for sub_topic in outcome.result.sub_topics
    ] == [
        ("topic-01", "Grid connection"),
        ("topic-02", "Siting and safety"),
        ("topic-03", "Market rules"),
    ]
    assert outcome.state_update["sub_topics"] == outcome.result.sub_topics


def test_the_plan_request_never_asks_the_model_for_a_coverage_id() -> None:
    """The id is stamped locally, so no provider request can propose one.

    ``coverage_id`` lives on ``SubTopic``, which is never sent to a provider,
    and not on the provider-facing ``ResearchPlanDraft``. This pins that
    boundary in both directions: the rendered request, and the strict JSON
    schema the request is constrained by.
    """
    messages = plan_messages(AgentTask(instruction="Question?"), _run())
    rendered = " ".join(message.content for message in messages)

    assert "coverage_id" not in rendered
    assert "topic-01" not in rendered
    assert "coverage_id" not in json.dumps(
        ResearchPlanDraft.model_json_schema(), sort_keys=True
    )


def _plan_text(sub_topic: SubTopic) -> str:
    """Everything about one planned sub-topic that a reader or search sees."""
    return " ".join(
        [
            sub_topic.title,
            *sub_topic.search_queries,
            *sub_topic.success_criteria,
        ]
    ).casefold()


# The recorded CLI run answered "What are the current constraints on
# grid-scale battery storage deployment?" with a report whose plan could not
# separate the constraint mechanisms. This fixture is a contract example of
# what a plan must be able to carry — the production planner holds no
# taxonomy of its own, so the five mechanisms live here and nowhere else.
BATTERY_CONSTRAINT_MECHANISMS = (
    "grid connection",
    "supply chain",
    "siting",
    "market rules",
    "project economics",
)


def _battery_storage_plan() -> ResearchPlanDraft:
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                "Grid connection and interconnection queue position",
                priority=2,
                search_queries=[
                    "FERC interconnection queue storage wait times 2026"
                ],
                success_criteria=[
                    "A filing or queue dataset gives measured United States "
                    "interconnection wait times for storage in 2026."
                ],
            ),
            _draft(
                "Equipment supply chain and trade exposure",
                priority=3,
                search_queries=[
                    "battery cell supply chain tariffs 2026 United States"
                ],
                success_criteria=[
                    "A trade dataset or standards-body report measures "
                    "United States cell and inverter lead times in 2026."
                ],
            ),
            _draft(
                "Siting, permitting, and fire safety rules",
                priority=4,
                search_queries=[
                    "NFPA 855 UL 9540A local siting permit requirements 2026"
                ],
                success_criteria=[
                    "A standard or permit record names the United States "
                    "fire-safety thresholds a 2026 project must meet."
                ],
            ),
            _draft(
                "Wholesale market rules and storage compensation",
                priority=5,
                search_queries=[
                    "FERC Order 841 storage market participation 2026"
                ],
                success_criteria=[
                    "An ISO market filing documents the United States "
                    "compensation a 2026 storage project can earn."
                ],
            ),
            _draft(
                "Project economics and financing",
                priority=1,
                search_queries=[
                    "grid-scale battery storage levelized cost financing 2026"
                ],
                success_criteria=[
                    "A lender or utility filing reports the measured United "
                    "States cost and financing terms for 2026 projects."
                ],
            ),
        ]
    )


def test_a_battery_storage_plan_separates_every_constraint_mechanism() -> None:
    """Each mechanism gets its own planned sub-topic, not a bundled one."""
    sub_topics, problems = validate_plan_draft(_battery_storage_plan())

    assert problems == []
    assert len(sub_topics) == len(BATTERY_CONSTRAINT_MECHANISMS) == 5
    assert [sub_topic.coverage_id for sub_topic in sub_topics] == [
        f"topic-{index:02d}" for index in range(1, 6)
    ]

    carriers = {
        mechanism: [
            sub_topic.coverage_id
            for sub_topic in sub_topics
            if mechanism in _plan_text(sub_topic)
        ]
        for mechanism in BATTERY_CONSTRAINT_MECHANISMS
    }
    for mechanism, coverage_ids in carriers.items():
        assert len(coverage_ids) == 1, (mechanism, coverage_ids)
    assert sorted(
        coverage_id
        for coverage_ids in carriers.values()
        for coverage_id in coverage_ids
    ) == [f"topic-{index:02d}" for index in range(1, 6)]

    # The question is unqualified, so the plan states the scope it assumes:
    # a jurisdiction and an as-of year, in the text the plan already carries.
    plan_text = " ".join(_plan_text(sub_topic) for sub_topic in sub_topics)
    assert "united states" in plan_text
    assert "2026" in plan_text


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
async def test_react_decision_requests_carry_the_react_decision_budget(
    tracker: Tracker,
) -> None:
    """ReAct decisions carry their own budget, never the planner-final one.

    This replaces the earlier invariant that decisions carried no override at
    all. A live Critic repetition recorded ``{kind: output_limit,
    operation: react_decision}``, so decisions were being truncated at the
    global cap. The separation that still matters is which budget reaches
    which request: decisions get ``react_decision_max_tokens`` and only plan
    drafts get ``planner_final_max_tokens``.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Recall prior work.", "query_memory", '{"query": "quantum"}'),
            finish("I understand the question.", "Three angles matter."),
        ],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state())

    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
    ]
    decision_budget = AgentRuntimeConfig().react_decision_max_tokens
    assert completer.react_budgets == [decision_budget, decision_budget]
    assert completer.budgets == [32768]


@pytest.mark.asyncio
async def test_only_final_plan_requests_use_the_planner_final_budget(
    tracker: Tracker,
) -> None:
    """A raised planner-final budget reaches only ``ResearchPlanDraft``."""
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Recall prior work.", "query_memory", '{"query": "quantum"}'),
            finish("I understand the question.", "Three angles matter."),
        ],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=3,
            tool_budget=3,
            planner_final_max_tokens=8192,
        ),
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state())

    decision_budget = AgentRuntimeConfig().react_decision_max_tokens
    assert completer.react_budgets == [decision_budget, decision_budget]
    assert completer.budgets == [8192]


@pytest.mark.asyncio
async def test_repair_plan_requests_also_use_the_planner_final_budget(
    tracker: Tracker,
) -> None:
    """Both plan drafts — initial and repair — carry the final budget."""
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
    agent = _planner(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=3,
            tool_budget=3,
            planner_final_max_tokens=8192,
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.repair_attempted is True
    decision_budget = AgentRuntimeConfig().react_decision_max_tokens
    assert completer.react_budgets == [decision_budget]
    assert completer.budgets == [8192, 8192]


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=32768,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
            structured_attempt=1,
        )
    )


@pytest.mark.asyncio
async def test_planner_preserves_react_provider_cause_with_operation_context(
    tracker: Tracker,
) -> None:
    provider_error = _output_limit_error()
    agent = _planner(
        tracker,
        ScriptedCompleter(decisions=[provider_error]),
    )

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert caught.value.__cause__ is provider_error
    assert "reach" not in str(caught.value).casefold()
    assert "scop" in str(caught.value).casefold()


@pytest.mark.asyncio
async def test_planner_preserves_final_plan_provider_cause_without_reachability_wording(
    tracker: Tracker,
) -> None:
    provider_error = _output_limit_error()
    agent = _planner(
        tracker,
        ScriptedCompleter(
            decisions=[finish("No lookup needed.", "Three angles matter.")],
            outputs=[provider_error],
        ),
    )

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert caught.value.__cause__ is provider_error
    assert "reach" not in str(caught.value).casefold()
    assert "plan" in str(caught.value).casefold()


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
    assert "# Repair" in repair_body
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

    assert completer.calls == []
    assert len(completer.react_calls) == 1


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
        "ResearchPlanDraft",
    ]
    assert len(completer.react_calls) == 1


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
        "ResearchPlanDraft",
        "ResearchPlanDraft",
    ]
    assert len(completer.react_calls) == 1


def _schema_failure(
    *diagnostics: StructuredValidationDiagnostic,
) -> StructuredOutputError:
    """A structured failure whose message is hostile and must never surface."""
    return StructuredOutputError(
        "PROVIDER_SECRET_SENTINEL", diagnostics=diagnostics
    )


_PLAN_DRAFT_STATIC_PROBLEM = (
    "the planner provider failed while requesting the final plan draft"
)


@pytest.mark.asyncio
async def test_the_plan_draft_schema_diagnostic_reaches_the_planning_error(
    tracker: Tracker,
) -> None:
    """Both structured attempts stay diagnosable after the run has failed.

    The provider discards the validation diagnostic one frame above this
    catch, so the planner error is the last artifact that can carry it.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _schema_failure(
                StructuredValidationDiagnostic(
                    attempt=1,
                    field_paths=("sub_topics.0.title",),
                    category="missing",
                ),
                StructuredValidationDiagnostic(
                    attempt=2,
                    field_paths=("sub_topics.1.priority", "sub_topics.2.title"),
                    category="type_mismatch",
                ),
            )
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        _PLAN_DRAFT_STATIC_PROBLEM,
        "the plan draft failed schema validation on attempt 1 at "
        "sub_topics.0.title (missing)",
        "the plan draft failed schema validation on attempt 2 at "
        "sub_topics.1.priority, sub_topics.2.title (type_mismatch)",
    )
    assert "PROVIDER_SECRET_SENTINEL" not in str(failure.value)


@pytest.mark.asyncio
async def test_a_diagnostic_without_a_category_uses_the_documented_fallback(
    tracker: Tracker,
) -> None:
    """``category`` is optional on the contract, so the line needs a word."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _schema_failure(
                StructuredValidationDiagnostic(
                    attempt=2,
                    field_paths=("sub_topics",),
                    category=None,
                )
            )
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        _PLAN_DRAFT_STATIC_PROBLEM,
        "the plan draft failed schema validation on attempt 2 at "
        "sub_topics (unclassified)",
    )


@pytest.mark.asyncio
async def test_a_hostile_field_path_never_reaches_the_planning_error(
    tracker: Tracker,
) -> None:
    """Only the contract's normalized ``$`` placeholder may be published."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _schema_failure(
                StructuredValidationDiagnostic(
                    attempt=1,
                    field_paths=("<script>alert(1)</script>",),
                    category="other_schema",
                )
            )
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        _PLAN_DRAFT_STATIC_PROBLEM,
        "the plan draft failed schema validation on attempt 1 at "
        "$ (other_schema)",
    )
    assert "<script>" not in str(failure.value)


@pytest.mark.asyncio
async def test_a_third_diagnostic_is_not_carried_into_extra_lines(
    tracker: Tracker,
) -> None:
    """The provider bounds its diagnostics at two, so the lines stay bounded."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _schema_failure(
                *(
                    StructuredValidationDiagnostic(
                        attempt=index,
                        field_paths=(f"sub_topics.{index}",),
                        category="missing",
                    )
                    for index in range(1, 4)
                )
            )
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        _PLAN_DRAFT_STATIC_PROBLEM,
        "the plan draft failed schema validation on attempt 1 at "
        "sub_topics.1 (missing)",
        "the plan draft failed schema validation on attempt 2 at "
        "sub_topics.2 (missing)",
    )


@pytest.mark.asyncio
async def test_a_structured_failure_without_diagnostics_keeps_the_static_error(
    tracker: Tracker,
) -> None:
    """No diagnostics means no extra lines: the static pair is untouched."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_schema_failure()],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert str(failure.value) == (
        "The planner could not produce the requested plan draft because "
        "the model provider operation failed."
    )
    assert failure.value.problems == (_PLAN_DRAFT_STATIC_PROBLEM,)
    assert failure.value.operation == "plan_draft"


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


@pytest.mark.asyncio
async def test_planner_regression_finish_decision_with_empty_tool_name_completes(
    tracker: Tracker,
) -> None:
    finish_with_empty_tool_name = finish(
        "I understand the question.", "Three angles matter."
    ).model_copy(update={"tool_name": ""})
    completer = ScriptedCompleter(
        decisions=[finish_with_empty_tool_name],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[-1].tool_name is None
    assert outcome.react.steps[-1].final_answer == "Three angles matter."


@pytest.mark.asyncio
async def test_planner_regression_tool_decision_with_empty_final_answer_completes(
    tracker: Tracker,
) -> None:
    tool_with_empty_final_answer = use_tool(
        "Recall prior work.", "query_memory", '{"query": "quantum"}'
    ).model_copy(update={"final_answer": ""})
    completer = ScriptedCompleter(
        decisions=[
            tool_with_empty_final_answer,
            finish("I understand the question.", "Three angles matter."),
        ],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations")],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[0].final_answer is None
    assert outcome.react.steps[0].tool_name == "query_memory"


def test_planner_regression_system_prompt_forbids_search_when_terms_are_familiar(
    tracker: Tracker,
) -> None:
    agent = _planner(tracker, ScriptedCompleter())
    prompt = agent.system_prompt(
        AgentTask(
            instruction=(
                "What evidence supports intermittent fasting for "
                "metabolic health?"
            )
        )
    )
    assert "every term in the research question is familiar" in prompt
    assert "finish without searching" in prompt


def test_planner_regression_plan_instruction_requires_priority_order() -> None:
    task = AgentTask(instruction="Some research question.")
    messages = plan_messages(task, _run())
    rendered = " ".join(message.content for message in messages)
    assert "priority order" in rendered
    assert "most important first" in rendered


def test_planner_regression_plan_instruction_permits_real_search_terms() -> None:
    """The plan instruction must permit the terms a real search needs.

    The removed sentence forbade any capitalized word or four-digit year the
    question did not itself contain, which blocked the identifiers, acronyms,
    jurisdictions, and years a query needs to reach primary or current
    evidence — exactly the planner defect the plan records for FERC, NFPA,
    UL 9540A, FEOC, and current-year material.
    """
    assert "Do not introduce any capitalized word" not in PLAN_INSTRUCTION
    for phrase in (
        "primary sources",
        "as-of date",
        "geographic scope",
        "measurable",
    ):
        assert phrase in PLAN_INSTRUCTION


def test_planner_regression_plan_instruction_requires_balanced_wording() -> None:
    task = AgentTask(instruction="Some research question.")
    messages = plan_messages(task, _run())
    rendered = " ".join(message.content for message in messages)
    assert "benefits" in rendered
    assert "risks" in rendered
    # The lexical ban is gone, so what replaces it has to keep the terms it
    # permits out of the plan's assertions.
    assert "Do not assert those terms as facts" in rendered
    assert "use them only as search targets" in rendered
