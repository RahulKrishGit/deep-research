"""Tests for the Planner's plan contracts, validation, and prompts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.planner import (
    MAX_PLAN_REVIEW_CALLS,
    MAX_SUB_TOPICS,
    MIN_SUB_TOPICS,
    PLAN_INSTRUCTION,
    EvidenceTargetDraft,
    PlannerAgent,
    PlanReviewDraft,
    ResearchPlan,
    ResearchPlanDraft,
    SubTopicDraft,
    answer_kind_for,
    apply_answer_contract,
    derive_answer_contract,
    extend_plan,
    format_plan_problems,
    frozen_contract_for,
    geographic_scope_for,
    has_startup_guidance,
    invented_tolerances,
    inventory_target_ids,
    plan_messages,
    plan_review_messages,
    stale_year_anchors,
    support_policy_for,
    target_problems,
    targets_requiring_replanning,
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
from deep_research.utils.config import AgentRuntimeConfig, load_config
from deep_research.utils.types import (
    ORIGINAL_QUESTION_OMISSION_REFERENCE,
    EvidenceTarget,
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


def _target(
    question: str = "What benchmark result does Alpha report?",
    *,
    dimensions: list[str] | None = None,
    critical: bool = True,
) -> EvidenceTargetDraft:
    return EvidenceTargetDraft(
        question=question,
        required_dimensions=(
            ["measure: benchmark result", "period: most recent reported year"]
            if dimensions is None
            else dimensions
        ),
        critical=critical,
    )


def _draft(
    title: str = "Error correction",
    *,
    priority: int = 1,
    search_queries: list[str] | None = None,
    success_criteria: list[str] | None = None,
    evidence_targets: list[EvidenceTargetDraft] | None = None,
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
        evidence_targets=(
            [_target()] if evidence_targets is None else evidence_targets
        ),
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
    clock: Callable[[], datetime] | None = None,
) -> PlannerAgent:
    return PlannerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker, search=search, memory=memory),
        config=config or AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        clock=clock or _clock,
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
        outputs=[_scrambled_plan(), _review()],
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


# The two ``list[str]`` fields the provider is asked for. A plan request
# sampled 11 times returned ``success_criteria`` with exactly one element
# every time, and the live failure was that lone criterion arriving as a
# bare string instead of a one-element list.
_SCALAR_LIST_FIELDS = ("search_queries", "success_criteria")

_LONE_CRITERION = "A benchmark with a named source."


def _raw_draft(**overrides: object) -> dict[str, object]:
    """One sub-topic payload exactly as a provider would send it."""
    payload: dict[str, object] = {
        "title": "Error correction",
        "rationale": "Error correction is load-bearing for the answer.",
        "search_queries": ["qec benchmarks 2025"],
        "success_criteria": [_LONE_CRITERION],
        "priority": 1,
        "evidence_targets": [
            {
                "question": "What benchmark result does Alpha report?",
                "required_dimensions": ["measure: benchmark result"],
                "critical": True,
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("field", _SCALAR_LIST_FIELDS)
def test_a_lone_string_is_read_as_a_one_element_list(field: str) -> None:
    """The model's one-bracket wobble must not kill the whole session."""
    draft = ResearchPlanDraft.model_validate(
        {"sub_topics": [_raw_draft(**{field: _LONE_CRITERION})]}
    )

    assert getattr(draft.sub_topics[0], field) == [_LONE_CRITERION]


@pytest.mark.parametrize("field", _SCALAR_LIST_FIELDS)
def test_a_list_of_strings_is_read_unchanged(field: str) -> None:
    """The tolerance widens nothing that already worked."""
    values = ["qec benchmarks 2025", "surface code threshold 2026"]

    draft = ResearchPlanDraft.model_validate(
        {"sub_topics": [_raw_draft(**{field: values})]}
    )

    assert getattr(draft.sub_topics[0], field) == values


@pytest.mark.parametrize("field", _SCALAR_LIST_FIELDS)
@pytest.mark.parametrize(
    "wrong",
    [
        5,
        3.5,
        True,
        None,
        {"a": 1},
        [5],
        ["ok", 5],
        [["nested"]],
    ],
    ids=["int", "float", "bool", "null", "dict", "list-of-int", "mixed", "nested"],
)
def test_a_non_string_wrong_type_is_still_rejected(
    field: str, wrong: object
) -> None:
    """Only a bare ``str`` is tolerated; nothing else becomes valid."""
    with pytest.raises(ValidationError):
        ResearchPlanDraft.model_validate(
            {"sub_topics": [_raw_draft(**{field: wrong})]}
        )


@pytest.mark.parametrize("field", _SCALAR_LIST_FIELDS)
def test_the_field_schema_still_asks_the_model_for_an_array_of_strings(
    field: str,
) -> None:
    """The model is asked for a list, so the fix must add no schema keyword."""
    properties = SubTopicDraft.model_json_schema()["properties"]

    assert properties[field] == {
        "items": {"type": "string"},
        "title": field.replace("_", " ").title(),
        "type": "array",
    }


# ``SubTopicDraft.model_json_schema()`` as the provider is handed it, minus
# the ``description`` that the class docstring renders into. Task 2 added
# ``evidence_targets`` on purpose — the plan now has to say what each
# sub-topic owes — and added no constraint keyword with it, so the schema
# still carries only ``type``, ``title``, ``items``, ``additionalProperties``,
# ``required``, and ``properties``.
_PLAN_SCHEMA_BEFORE_THE_FIX: dict[str, object] = {
    "additionalProperties": False,
    "properties": {
        "evidence_targets": {
            "items": {"$ref": "#/$defs/EvidenceTargetDraft"},
            "title": "Evidence Targets",
            "type": "array",
        },
        "priority": {"title": "Priority", "type": "integer"},
        "rationale": {"title": "Rationale", "type": "string"},
        "search_queries": {
            "items": {"type": "string"},
            "title": "Search Queries",
            "type": "array",
        },
        "success_criteria": {
            "items": {"type": "string"},
            "title": "Success Criteria",
            "type": "array",
        },
        "title": {"title": "Title", "type": "string"},
    },
    "required": [
        "title",
        "rationale",
        "search_queries",
        "success_criteria",
        "priority",
        "evidence_targets",
    ],
    "title": "SubTopicDraft",
    "type": "object",
}


def test_the_plan_schema_the_model_is_handed_is_unchanged() -> None:
    """A validator is not a schema keyword, so the request cannot shift.

    The draft model is converted to a strict JSON schema and sent to the
    provider, so anything that leaked into that schema would change what the
    model is asked for. This pins the whole schema but the human-readable
    docstring — including that ``evidence_targets`` brought no ``minItems``
    or ``minLength`` with it, which the strict subset rejects.
    """
    schema = dict(SubTopicDraft.model_json_schema())
    schema.pop("description", None)
    schema.pop("$defs", None)

    assert schema == _PLAN_SCHEMA_BEFORE_THE_FIX
    structural = json.dumps(
        {
            key: value
            for key, value in SubTopicDraft.model_json_schema().items()
            if key != "description"
        }
    )
    for keyword in ("minItems", "minLength", "maxItems"):
        assert keyword not in structural


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
    assert "1 finding(s) recalled from previous sessions" in task.guidance
    assert "leads, not evidence" in task.guidance
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state())

    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    decision_budget = AgentRuntimeConfig().react_decision_max_tokens
    assert completer.react_budgets == [decision_budget, decision_budget]
    # Both plan-side structured calls carry the planner's own budget: the
    # review is a tool-free call about the plan, not a ReAct decision.
    assert completer.budgets == [32768, 32768]


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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
    assert completer.budgets == [8192, 8192]


@pytest.mark.asyncio
async def test_repair_plan_requests_also_use_the_planner_final_budget(
    tracker: Tracker,
) -> None:
    """Every plan-side structured call carries the planner's own budget."""
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
            _review(),
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
    assert completer.budgets == [8192, 8192, 8192]


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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
            _review(),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.repair_attempted is True
    repair_body = completer.calls[-2][2][1].content
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
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


# --- Task 2: answer-shaped, scoped, feasible, production-configured plans ----

# The September 2026 session the baseline measured. Pinned so every assertion
# below is about the planner and not about the day this suite runs.
_CLOCK_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return _CLOCK_NOW


def _review(
    *,
    sound: bool = True,
    missing_dimensions: list[str] | None = None,
    atomicity_defects: list[str] | None = None,
    unsupported_premises: list[str] | None = None,
    repair_instruction: str = "",
) -> PlanReviewDraft:
    return PlanReviewDraft(
        sound=sound,
        missing_dimensions=missing_dimensions or [],
        atomicity_defects=atomicity_defects or [],
        unsupported_premises=unsupported_premises or [],
        repair_instruction=repair_instruction,
    )


def _sorting_plan() -> ResearchPlanDraft:
    return _plan("Cryptography", "Hardware timelines", "Mitigations")


def _contract(question: str = "What are the current constraints?", **kwargs):
    return derive_answer_contract(
        question=question, now=_CLOCK_NOW, **kwargs
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What limits grid-scale battery storage today?", "factual"),
        ("Which FERC rules govern battery storage interconnection?", "constraints"),
        ("What are the current EU permitting constraints?", "constraints"),
        ("How do pumped hydro and lithium-ion compare on cost?", "comparison"),
        ("What did the 2015 capacity market rules require?", "historical"),
        ("Why did interconnection queue times grow after 2019?", "explanation"),
    ],
)
def test_the_answer_form_matches_the_question(question: str, expected: str) -> None:
    assert answer_kind_for(question, clock_year=2026) == expected


def test_the_answer_contract_is_stamped_from_the_injected_run_clock() -> None:
    """The as-of date is the run clock's date, not the model's belief."""
    contract = _contract("What are the current interconnection constraints?")

    assert contract.as_of_date == "2026-09-16"
    assert contract.question == (
        "What are the current interconnection constraints?"
    )
    assert contract.answer_kind == "constraints"
    assert "latest available evidence as of 2026-09-16" in (
        contract.evidence_period_requirement
    )
    assert contract.geographic_scope == "unspecified"
    assert contract.assumptions


def test_a_question_that_names_a_year_keeps_that_year_as_its_as_of_date() -> None:
    """A user-supplied historical date is preserved, not re-anchored."""
    contract = _contract("What did the 2021 capacity market rules require?")

    assert contract.as_of_date == "2021-12-31"
    assert "2021" in contract.evidence_period_requirement
    assert "never substitute today's figures" in (
        contract.evidence_period_requirement
    )


def test_a_future_year_is_a_forecast_horizon_not_an_as_of_date() -> None:
    contract = _contract("What will 4-hour storage cost in 2035?")

    assert contract.as_of_date == "2026-09-16"
    assert "2035 is a forecast horizon" in contract.evidence_period_requirement
    assert "never as today's figure" in contract.evidence_period_requirement


def test_a_named_jurisdiction_is_recorded_and_an_unqualified_one_assumes() -> None:
    scoped = _contract("What are the current California interconnection rules?")
    unscoped = _contract("What are the current interconnection rules?")

    assert scoped.geographic_scope == "California"
    assert scoped.assumptions == []
    assert unscoped.geographic_scope == "unspecified"
    assert "no regional sample may support a global conclusion" in (
        unscoped.assumptions[0]
    )


def test_a_requested_word_limit_is_recorded() -> None:
    limited = _contract("Summarize the rules in under 500 words.")

    assert limited.requested_word_limit == 500
    assert _contract("What are the rules?").requested_word_limit is None


def test_a_naive_clock_is_rejected_at_construction(tracker: Tracker) -> None:
    with pytest.raises(AgentConfigurationError, match="timezone-aware"):
        PlannerAgent(
            provider=ScriptedCompleter(),
            tracker=tracker,
            scratchpad=ScratchpadMemory(
                session_id="session-1", agent_name="planner", max_entries=20
            ),
            tools=planner_tools(tracker),
            clock=lambda: datetime(2026, 9, 16, 12, 0),
        )


def test_targets_carry_locally_stamped_ids_and_the_contract_dimensions() -> None:
    contract = _contract("What are the current interconnection constraints?")
    sub_topics, problems = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    title,
                    priority=index,
                    evidence_targets=[
                        _target(f"What does {title} report first?"),
                        _target(f"What does {title} report second?"),
                    ],
                )
                for index, title in enumerate(
                    ("Queue totals", "Withdrawn capacity", "Reforms"), start=1
                )
            ]
        )
    )
    stamped = apply_answer_contract(sub_topics, contract)

    assert problems == []
    assert [target.target_id for target in stamped[0].evidence_targets] == [
        "topic-01-target-01",
        "topic-01-target-02",
    ]
    first = stamped[0].evidence_targets[0]
    assert first.coverage_id == "topic-01"
    assert first.required is True
    assert first.critical is True
    assert any(
        dimension.startswith("evidence period:")
        for dimension in first.required_dimensions
    )
    assert any(
        dimension.startswith("geography:") for dimension in first.required_dimensions
    )
    assert any(
        dimension.startswith("answer form:") for dimension in first.required_dimensions
    )
    assert target_problems(stamped, contract) == []


def test_a_legacy_plan_without_targets_requires_replanning() -> None:
    legacy = SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="Alpha is load-bearing.",
        search_queries=["alpha 2025"],
        success_criteria=["A named source about Alpha."],
        priority=1,
    )

    assert legacy.evidence_targets == []
    assert targets_requiring_replanning([legacy]) == ["topic-01"]


def test_a_support_policy_is_assigned_before_any_verdict_exists() -> None:
    """Official rule dates are primary attribution, not a second model of it."""
    assert (
        support_policy_for(
            question="What is the effective date of the 2023 interconnection rule?"
        )
        == "primary_attribution"
    )
    assert (
        support_policy_for(question="What is the fee schedule for a permit?")
        == "primary_attribution"
    )
    assert (
        support_policy_for(
            question="What is the cost per megawatt of installed capacity?"
        )
        == "derivation"
    )
    assert (
        support_policy_for(
            question="Did queue times grow faster in the west than in the east?"
        )
        == "independent_pair"
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "How many projects waited more than 5 years?",
            "factual",
        ),
        (
            "What are the requirements for projects more than 5 MW?",
            "constraints",
        ),
        (
            "What share of the queue withdrew less than 15%?",
            "factual",
        ),
    ],
)
def test_a_threshold_question_is_not_a_comparison(
    question: str, expected: str
) -> None:
    """A bare inequality is a threshold, not a comparison.

    The comparison answer form ("the same measured dimension for every option
    compared, on one shared basis and unit") is stamped into every target's
    binding dimensions, and Section 2.3 judges a target answered only when
    those dimensions are satisfied. Stamping it onto "how many projects waited
    more than 5 years?" makes an ordinary quantity question unsatisfiable, so
    an inequality counts as a comparison only when it names a referent.
    """
    assert answer_kind_for(question, clock_year=2026) == expected


def test_a_threshold_rule_keeps_its_primary_attribution_policy() -> None:
    """"Tariffs of more than 10%" is an official threshold, not a comparison."""
    assert (
        support_policy_for(
            question="What are the rules for tariffs of more than 10%?"
        )
        == "primary_attribution"
    )
    # A comparison that *does* name a referent still outranks attribution.
    assert (
        support_policy_for(
            question="Is permitting slower in California than in Texas?"
        )
        == "independent_pair"
    )
    assert (
        support_policy_for(
            question="Did the 2023 regulation cost more than the 2019 one?"
        )
        == "independent_pair"
    )


def test_a_derived_marker_form_still_classifies() -> None:
    """Derived forms of a semantic marker match; jurisdiction names do not.

    "permit" must still reach "permitting" and "recent" must still reach
    "recently", while "indian" must never reach "Indiana" — one switch decides
    both.
    """
    assert (
        support_policy_for(
            question=(
                "What are the effective dates of the 2023 and 2024 "
                "interconnection rules?"
            )
        )
        == "primary_attribution"
    )
    assert (
        support_policy_for(question="What do the FERC fee schedules require?")
        == "primary_attribution"
    )
    assert (
        support_policy_for(
            question="What are the permitting requirements in California?"
        )
        == "primary_attribution"
    )
    assert stale_year_anchors(
        "the recently revised 2024 figures", as_of_year=2026
    ) == [2024]
    assert (
        answer_kind_for(
            "What are the recently revised 2024 figures?", clock_year=2026
        )
        == "factual"
    )


def test_a_method_based_tolerance_is_not_invented() -> None:
    """A plural method phrase is still a method."""
    assert invented_tolerances(
        "The two figures agree within 5 percentage points; the error bars "
        "are reported.",
        question="How much capacity was withheld?",
    ) == []


def test_a_standalone_uppercase_abbreviation_names_the_country() -> None:
    """"US federal permitting rules" is the country; "tell us" is a pronoun."""
    assert geographic_scope_for("What are US federal permitting rules?")[0] == (
        "United States"
    )
    assert geographic_scope_for("What are the US federal rules?")[0] == (
        "United States"
    )
    assert geographic_scope_for("Can you tell us about the rules?")[0] == (
        "unspecified"
    )
    assert geographic_scope_for("What are the uses of storage?")[0] == (
        "unspecified"
    )


def test_a_question_stated_tolerance_must_match_the_unit_too() -> None:
    """A relative band does not satisfy an absolute requirement.

    "Within 3%" and "within 3 percentage points" are different tolerances, so
    a criterion may not satisfy the question's precision by restating its
    number in another unit.
    """
    assert invented_tolerances(
        "Both agree within 3%.",
        question="Do the estimates agree within 3 percentage points?",
    ) == ["3%"]
    assert invented_tolerances(
        "Both agree within 10%.",
        question="Do the estimates agree within 10 percentage points?",
    ) == ["10%"]
    # The same number in the same unit is the question's own precision.
    assert invented_tolerances(
        "Both agree within 3 percentage points.",
        question="Do the estimates agree within 3 percentage points?",
    ) == []
    # "%" and "percent" are the same unit spelled two ways.
    assert invented_tolerances(
        "Both agree within 3 percent.",
        question="Do the estimates agree within 3%?",
    ) == []


def test_stale_year_anchors_are_reported_only_in_a_currency_frame() -> None:
    assert stale_year_anchors(
        "The current 2024 figures settle it.", as_of_year=2026
    ) == [2024]
    assert stale_year_anchors(
        "Latest available data as of 2023.", as_of_year=2026
    ) == [2023]
    # A question or criterion is allowed to be about an older year; that is
    # its subject, not a claim that the year is current.
    assert stale_year_anchors(
        "What did the 2021 rules require?", as_of_year=2026
    ) == []
    assert stale_year_anchors(
        "Current figures as of 2026.", as_of_year=2026
    ) == []


def test_a_question_about_the_clock_year_is_answered_as_of_today() -> None:
    """Naming the clock's own year is a currency frame, not a closed period.

    A 2026 question asked on 2026-09-16 was being stamped
    ``as_of_date = 2026-12-31`` — a date three and a half months in the future,
    frozen into the contract and copied into every target's binding evidence
    period. That is the defect class this contract exists to remove, so the
    regression is pinned with the probe that found it.
    """
    contract = _contract("What are the 2026 interconnection rules?")

    assert contract.as_of_date == "2026-09-16"
    assert "the latest available evidence as of 2026-09-16" in (
        contract.evidence_period_requirement
    )
    assert "never substitute today's figures" not in (
        contract.evidence_period_requirement
    )
    assert "2026-12-31" not in contract.scope_statement
    assert contract.answer_kind == "constraints"


@pytest.mark.parametrize(
    "question",
    [
        "What are the 2026 interconnection rules?",
        "What are the current rules?",
        "What did the 2021 rules require?",
        "What will storage cost in 2035?",
        "How much capacity was withheld as of 2026-06-01?",
        "How much will be added between 2027 and 2030?",
    ],
)
def test_no_question_can_be_stamped_with_a_future_as_of_date(
    question: str,
) -> None:
    """The as-of date never moves past the run clock, on any branch."""
    contract = _contract(question)

    assert contract.as_of_date <= _CLOCK_NOW.date().isoformat()


def test_a_geography_alias_is_matched_on_token_boundaries() -> None:
    """The reviewer's probes: "tell us", "Indiana", and the "us" pronoun.

    A wrong jurisdiction is stamped into every target's geography dimension,
    where nothing downstream can see the error, so an unrecognized name must
    fall to the explicit-assumption path rather than to a guessed country.
    """
    unspecified = geographic_scope_for("Can you tell us about the permitting rules?")
    assert unspecified[0] == "unspecified"
    assert unspecified[1]

    indiana = geographic_scope_for("What are the siting rules in Indiana?")
    assert indiana[0] == "unspecified"
    assert geographic_scope_for("What are the uses of storage?")[0] == "unspecified"

    # Positive controls: the aliases that should still resolve.
    assert geographic_scope_for("What are the rules in India?")[0] == "India"
    assert geographic_scope_for("What are the rules in the US?")[0] == (
        "United States"
    )
    assert geographic_scope_for("What are the U.S. rules?")[0] == "United States"
    assert geographic_scope_for("What are the rules in the EU?")[0] == (
        "European Union"
    )


def test_a_marker_is_matched_on_token_boundaries() -> None:
    """"known" is not the currency word "now"; plurals still match."""
    assert answer_kind_for(
        "What is known about the 2015 capacity rules?", clock_year=2026
    ) == "historical"
    # A plural marker still matches its singular: "rules"/"policies" are
    # constraints questions, and "rule"/"policy" alone never covered them.
    assert answer_kind_for("What are the 2026 rules?", clock_year=2026) == (
        "constraints"
    )
    assert answer_kind_for("What are the current policies?", clock_year=2026) == (
        "constraints"
    )


def test_a_comparative_regulatory_question_is_an_independent_pair() -> None:
    """Comparison outranks attribution: a comparison needs two sources.

    "Is permitting slower in California than in Texas?" contains "permit", and
    the attribution rule captured it — a comparative conclusion downgraded to
    citing one authority, which is the opposite of Section 2.1.
    """
    assert (
        support_policy_for(
            question="Is permitting slower in California than in Texas?"
        )
        == "independent_pair"
    )
    assert (
        support_policy_for(
            question="Did the 2023 regulation cost more than the 2019 one?"
        )
        == "independent_pair"
    )
    # The attribution rule still owns the non-comparative cases.
    assert (
        support_policy_for(question="What is the fee schedule for a permit?")
        == "primary_attribution"
    )
    assert (
        support_policy_for(
            question="What is the effective date of the 2023 rule?"
        )
        == "primary_attribution"
    )


def test_a_bare_percentage_is_not_an_invented_tolerance() -> None:
    """A number is not a tolerance; an agreement frame is what makes one.

    Reporting a bare "15%" forced a repair on a perfectly ordinary question and
    then failed the planning pass when the model kept its correct number.
    """
    question = "How much capacity was withheld?"

    assert invented_tolerances(
        "Did withdrawals exceed 15% in 2025?", question=question
    ) == []
    assert invented_tolerances(
        "At least 15% of the queue withdrew.", question=question
    ) == []
    assert invented_tolerances(
        "Growth was 10% year over year.", question=question
    ) == []


def test_an_invented_tolerance_needs_no_basis_in_the_question() -> None:
    """The last measured plan invented 10%, 5 pp, 15%, and 3 pp."""
    question = "How much capacity was withheld in 2024?"

    assert invented_tolerances(
        "Two publishers agree within 10%.", question=question
    ) == ["10%"]
    assert invented_tolerances(
        "The two figures agree within 5 percentage points.", question=question
    ) == ["5 percentage points"]
    assert invented_tolerances(
        "Both sources agree to within 3 pp.", question=question
    ) == ["3 pp"]
    # The question itself sets the precision.
    assert invented_tolerances(
        "Both agree within 3 percentage points.",
        question="Do the estimates agree within 3 percentage points?",
    ) == []
    # A named measurement or method is a basis.
    assert invented_tolerances(
        "They agree within 2%, the instrument's stated measurement resolution.",
        question=question,
    ) == []


@pytest.mark.asyncio
async def test_a_stale_anchor_in_a_plan_is_reported_and_repaired_not_accepted(
    tracker: Tracker,
) -> None:
    """A fixture proposing "2024 is current" becomes a latest-available
    obligation rather than a hard-coded old-year limit.

    The first plan is rejected for anchoring currency to 2024 in a September
    2026 session; the repair asks for the latest available evidence, and the
    obligation the reader ends up bound by names the run clock's date.
    """
    stale = ResearchPlanDraft(
        sub_topics=[
            _draft(
                title,
                priority=index,
                success_criteria=[
                    "Current as of 2024 figures are available for both "
                    "publishers."
                ],
            )
            for index, title in enumerate(
                ("Queue totals", "Withdrawn capacity", "Reforms"), start=1
            )
        ]
    )
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            stale,
            _sorting_plan(),
            _review(),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What are the current interconnection constraints?")
        )

    assert outcome.result is not None
    assert outcome.result.repair_attempted is True
    repair_request = completer.calls[1][2][1].content
    assert "anchors currency to 2024" in repair_request
    assert "ask for the latest available evidence instead" in repair_request

    for sub_topic in outcome.result.sub_topics:
        for target in sub_topic.evidence_targets:
            period = [
                dimension
                for dimension in target.required_dimensions
                if dimension.startswith("evidence period:")
            ]
            assert period == [
                "evidence period: the latest available evidence as of "
                "2026-09-16; a fixed earlier year is not a current answer"
            ]
            assert "2024" not in target.question


def test_an_asserted_target_is_reported_as_a_question_to_ask() -> None:
    contract = _contract("What are the current constraints?")
    sub_topics, _ = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    title,
                    priority=index,
                    evidence_targets=[_target("Queue times doubled in 2024.")],
                )
                for index, title in enumerate(
                    ("Queue totals", "Withdrawn capacity", "Reforms"), start=1
                )
            ]
        )
    )

    problems = target_problems(
        apply_answer_contract(sub_topics, contract), contract
    )

    assert any("written as an assertion" in problem for problem in problems)


def test_a_compound_target_is_found_by_the_review_not_by_a_regex() -> None:
    """Semantic atomicity is the review's job, and the review names it."""
    contract = _contract("How much storage capacity was added?")
    sub_topics = apply_answer_contract(
        validate_plan_draft(_sorting_plan())[0], contract
    )
    compound = (
        "How much capacity was added in California after the 2023 rule and in "
        "Texas after the 2021 rule?"
    )
    sub_topics = [
        sub_topics[0].model_copy(
            update={
                "evidence_targets": [
                    sub_topics[0].evidence_targets[0].model_copy(
                        update={"question": compound}
                    )
                ]
            }
        ),
        *sub_topics[1:],
    ]

    # Nothing structural rejects it: one sentence, one question mark.
    assert target_problems(sub_topics, contract) == []

    messages = plan_review_messages(contract, sub_topics)
    body = messages[1].content
    assert "one measure, one rule date, one jurisdiction" in body
    assert "compound even when it reads as one sentence" in body
    assert contract.question in body


@pytest.mark.asyncio
async def test_the_bounded_plan_review_repairs_once_and_can_fail_the_plan(
    tracker: Tracker,
) -> None:
    """The review/repair cycle is bounded, and a stuck plan fails the session.

    The bound is four plan-side calls: plan, review, repaired plan, confirming
    review. It is deliberately *two* reviews rather than one — a repair that is
    never re-reviewed is a plan accepted on hope — so the bound is asserted
    here rather than left to drift, and a plan that stays unsound raises with
    the reviewer's own defect list instead of being accepted.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(
                sound=False,
                missing_dimensions=["siting and permitting"],
                repair_instruction="Add a sub-topic for siting and permitting.",
            ),
            _sorting_plan(),
            _review(
                sound=False,
                atomicity_defects=["target-01-01 combines two measures"],
            ),
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(
                _state("What limits grid-scale battery storage deployment?")
            )

    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    review_calls = [
        call for call in completer.calls if call[0] == "PlanReviewDraft"
    ]
    assert MAX_PLAN_REVIEW_CALLS == 2
    assert len(review_calls) <= MAX_PLAN_REVIEW_CALLS
    assert any(
        "target-01-01 combines two measures" in problem
        for problem in caught.value.problems
    )
    # The review request is tool-free and carries the frozen question.
    review_request = completer.calls[1][2][1].content
    assert "What limits grid-scale battery storage deployment?" in review_request
    for tool_name in ("web_search", "query_memory", "web_scraper"):
        assert tool_name not in review_request


@pytest.mark.asyncio
async def test_a_biased_premise_is_named_by_the_review_and_fails_the_plan(
    tracker: Tracker,
) -> None:
    """A query or criterion that assumes the answer is repaired, then refused.

    "The plan is biased" is a meaning defect, so the review is what names it;
    the repair prompt must carry the reviewer's finding rather than a generic
    complaint, and a plan that keeps the premise must not be accepted.
    """
    premise = "the query assumes storage already caused the outage"
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(
                sound=False,
                unsupported_premises=[premise],
                repair_instruction="Ask which causes are established.",
            ),
            _sorting_plan(),
            _review(sound=False, unsupported_premises=[premise]),
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(
                _state("What limits grid-scale battery storage deployment?")
            )

    repair_request = completer.calls[2][2][1].content
    assert premise in repair_request
    assert any(
        f"plan review found an unsupported premise: {premise}" in problem
        for problem in caught.value.problems
    )
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]


@pytest.mark.asyncio
async def test_a_scope_widening_target_is_named_by_the_review_and_fails(
    tracker: Tracker,
) -> None:
    """A target that widens the frozen scope cannot be accepted.

    The review can only judge the scope if the request carries the frozen
    contract, so the request is inspected as well as the outcome: the reviewer
    must see the contract's geography and as-of date beside the plan.
    """
    widening = "What is the global effect of the United States rule?"
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(
                sound=False,
                unsupported_premises=[
                    f"target-01-01 widens the frozen scope: {widening}"
                ],
                repair_instruction="Keep every target inside the scope.",
            ),
            _sorting_plan(),
            _review(
                sound=False,
                unsupported_premises=[
                    f"target-01-01 widens the frozen scope: {widening}"
                ],
            ),
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(
                _state("What are the storage rules in the United States?")
            )

    review_request = completer.calls[1][2][1].content
    assert "- Scope: United States" in review_request
    assert "- As of: 2026-09-16" in review_request
    assert "widen" in review_request
    assert any(
        "widens the frozen scope" in problem
        for problem in caught.value.problems
    )


@pytest.mark.asyncio
async def test_an_infeasible_target_batch_is_repaired_once_then_refused(
    tracker: Tracker,
) -> None:
    """More than four obligations on one sub-topic is caught structurally.

    Feasibility has a structural half (a sub-topic carries at most four
    obligations; a pass that has to settle eight is not a pass) and a semantic
    half the review owns. This pins the structural half: the batch is rejected,
    the repair is asked for, and a model that keeps the oversized batch fails
    the session by name rather than producing a plan no pass could finish.
    """
    oversized = ResearchPlanDraft(
        sub_topics=[
            _draft(
                title,
                priority=index,
                evidence_targets=[
                    _target(f"What does {title} report as measure {measure}?")
                    for measure in range(1, 6)
                ],
            )
            for index, title in enumerate(
                ("Queue totals", "Withdrawn capacity", "Reforms"), start=1
            )
        ]
    )
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[oversized, oversized],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(
                _state("What limits grid-scale battery storage deployment?")
            )

    # Two plan requests and no review: the batch never became reviewable.
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
    ]
    repair_request = completer.calls[1][2][1].content
    assert "evidence_targets" in repair_request
    assert any("evidence_targets" in problem for problem in caught.value.problems)


@pytest.mark.asyncio
async def test_a_missing_dimension_is_named_in_the_repair_with_the_original_question(
    tracker: Tracker,
) -> None:
    """A seemingly diverse plan that omits a dimension is repaired by name."""
    repaired = _plan(
        "Queue totals", "Withdrawn capacity", "Siting and permitting"
    )
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(
                sound=False,
                missing_dimensions=["siting and permitting"],
                repair_instruction="Cover siting and permitting.",
            ),
            repaired,
            _review(),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    repair_request = completer.calls[2][2][1].content
    assert "siting and permitting" in repair_request
    assert "The original question is unchanged" in repair_request
    assert "What limits grid-scale battery storage deployment?" in repair_request
    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Queue totals",
        "Withdrawn capacity",
        "Siting and permitting",
    ]


@pytest.mark.asyncio
async def test_the_planning_request_carries_the_frozen_contract_and_its_obligations(
    tracker: Tracker,
) -> None:
    """The actual request is inspected, not just the planner's own fields."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state("What did the 2021 capacity rules require?"))

    plan_request = completer.calls[0][2][1].content
    assert "# Answer contract" in plan_request
    assert "- As of: 2021-12-31" in plan_request
    assert "never substitute today's figures" in plan_request
    assert "between 1 and 4 evidence_targets" in plan_request
    assert "2024" not in plan_request


@pytest.mark.asyncio
async def test_the_state_update_freezes_the_contract_and_the_initial_inventory(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state("What are the current constraints?"))

    contract = outcome.state_update["answer_contract"]
    assert contract.as_of_date == "2026-09-16"
    assert outcome.state_update["initial_target_ids"] == [
        "topic-01-target-01",
        "topic-02-target-01",
        "topic-03-target-01",
    ]
    assert "expanded_target_ids" not in outcome.state_update


def test_extend_plan_adds_topics_and_targets_without_touching_what_exists() -> None:
    """The siting/permitting case: an omission is added, never substituted.

    The initial plan covers queue totals, withdrawn capacity, and reforms. The
    extension adds siting and permitting, and nothing else changes: existing
    ids, priorities, critical flags, and the frozen as-of date all survive.
    """
    contract = _contract("What limits battery storage deployment?")
    existing = apply_answer_contract(
        validate_plan_draft(_sorting_plan())[0], contract
    )
    before = [sub_topic.model_dump() for sub_topic in existing]

    additions, problems = extend_plan(
        existing,
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    "Siting and permitting",
                    priority=4,
                    evidence_targets=[
                        _target("Which authority issues the siting permit?"),
                        _target("How long does permitting take?"),
                    ],
                )
            ]
        ),
        contract=contract,
    )

    assert problems == []
    assert [sub_topic.coverage_id for sub_topic in additions] == ["topic-04"]
    assert [target.target_id for target in additions[0].evidence_targets] == [
        "topic-04-target-01",
        "topic-04-target-02",
    ]
    assert [sub_topic.model_dump() for sub_topic in existing] == before
    assert [sub_topic.coverage_id for sub_topic in existing] == [
        "topic-01",
        "topic-02",
        "topic-03",
    ]


def test_an_extension_that_does_not_fit_is_an_explicit_capacity_conflict() -> None:
    """A full plan reports the conflict; it is not permission to delete."""
    contract = _contract("What limits battery storage deployment?")
    existing = apply_answer_contract(
        validate_plan_draft(
            ResearchPlanDraft(
                sub_topics=[
                    _draft(title, priority=index)
                    for index, title in enumerate(
                        (
                            "One",
                            "Two",
                            "Three",
                            "Four",
                            "Five",
                            "Six",
                            "Seven",
                        ),
                        start=1,
                    )
                ]
            )
        )[0],
        contract,
    )
    assert len(existing) == MAX_SUB_TOPICS

    additions, problems = extend_plan(
        existing,
        ResearchPlanDraft(
            sub_topics=[
                _draft("Siting and permitting", priority=8),
                _draft("Grid interconnection", priority=9),
            ]
        ),
        contract=contract,
    )

    assert additions == []
    assert any("capacity" in problem or "at most 7" in problem for problem in problems)
    assert any("never removed to make room" in problem for problem in problems)


def test_extending_a_legacy_plan_without_a_contract_is_refused(
    tracker: Tracker,
) -> None:
    """A legacy session has no frozen scope to extend within."""
    completer = ScriptedCompleter()
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        asyncio.run(
            agent.extend_plan(
                _state("What limits battery storage deployment?"),
                omission="siting and permitting",
            )
        )

    assert caught.value.problems == ("answer_contract is missing",)
    assert completer.calls == []


@pytest.mark.asyncio
async def test_startup_recall_is_the_planners_single_procedural_lookup(
    tracker: Tracker,
) -> None:
    """With guidance in hand the planner is not offered a second lookup.

    The startup recall IS the planner's procedural lookup, so the run must
    total at most one: the agent that already has guidance carries no
    ``query_memory`` at all, and one that has none may use its single call.
    The request itself is inspected, not just the counter.
    """
    budget = AgentRuntimeConfig(
        max_iterations=3,
        tool_budget=10,
        tool_budget_overrides={"planner": 1},
    )
    guided = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    guided_agent = _planner(tracker, guided, config=budget)
    state = _state(
        memory_context=MemorySnapshot(
            suggested_strategies=["Prefer primary filings."]
        )
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await guided_agent.run(state)

    assert guided_agent.config.tool_budget_for("planner") == 1
    offered = [definition.name for definition in guided.react_calls[0].tools]
    assert offered == ["web_search"]
    assert "Prefer primary filings." in guided.react_calls[0].messages[1].content
    # The plan request itself carries the guidance, not recalled findings.
    plan_request = guided.calls[0][2][1].content
    assert "Prefer primary filings." in plan_request
    assert outcome.result is not None

    unguided = ScriptedCompleter(
        decisions=[
            use_tool(
                "Recall procedural guidance.",
                "query_memory",
                '{"query": "quantum"}',
            ),
            finish("No further lookup.", "Three angles matter."),
        ],
        outputs=[_sorting_plan(), _review()],
    )
    unguided_agent = _planner(tracker, unguided, config=budget)

    async with tracker.session_span("session-1", "q"):
        await unguided_agent.run(_state())

    offered_without_guidance = [
        definition.name for definition in unguided.react_calls[0].tools
    ]
    assert offered_without_guidance == ["query_memory", "web_search"]
    # One lookup at most: the second decision made no tool call at all.
    assert unguided.react_calls[0].tools[0].name == "query_memory"
    assert unguided_agent.config.tool_budget_for("planner") == 1


def test_a_narrowed_toolset_can_only_remove_tools(tracker: Tracker) -> None:
    """``without`` narrows; an unknown name cannot widen anything."""
    agent = _planner(tracker, ScriptedCompleter())

    narrowed = agent.toolset.without("query_memory", "not_a_tool")

    assert narrowed.names == ("web_search",)
    assert agent.toolset.names == ("query_memory", "web_search")


@pytest.mark.asyncio
async def test_an_unguided_planner_is_refused_a_second_memory_lookup(
    tracker: Tracker,
) -> None:
    """The one-lookup cap is enforced on the production class, not by config.

    ``tool_budget_for("planner") == 1`` alone would allow one lookup per loop
    decision; what "a run totals no more than one procedural lookup" needs is
    the loop actually stopping on the second attempt, which only a run of the
    real ``PlannerAgent`` can show.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Recall procedural guidance.",
                "query_memory",
                '{"query": "quantum"}',
            ),
            use_tool(
                "Recall once more.",
                "query_memory",
                '{"query": "quantum again"}',
            ),
            finish("No further lookup.", "Three angles matter."),
        ],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=10,
            tool_budget_overrides={"planner": 1},
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.react.tool_calls == 1
    assert outcome.react.stop_reason == "tool_budget_exhausted"
    # The loop records the refusal it stopped on; what matters is that the
    # second lookup never executed, which the executed-call count above says.
    assert "agent_tool_budget_exhausted" in {
        error.error_type for error in outcome.errors
    }


def test_recalled_leads_alone_do_not_suppress_the_procedural_lookup() -> None:
    """A findings-only snapshot is not procedural guidance.

    ``has_startup_guidance`` keys on ``suggested_strategies`` alone. A resumed
    session whose snapshot carries recalled leads but no strategies has been
    given no procedural guidance, so it keeps its one lookup rather than being
    denied both the startup guidance and the tool that could replace it.
    """
    leads_only = MemorySnapshot(
        similar_findings=[
            Finding(
                content="Shor's algorithm breaks RSA.",
                source_url="https://example.test/shor",
                source_title="Shor 1994",
                extracted_at="2026-01-01T00:00:00+00:00",
                confidence=0.99,
                related_sub_topic="Cryptography",
            )
        ]
    )

    assert has_startup_guidance(leads_only) is False
    assert (
        has_startup_guidance(
            MemorySnapshot(suggested_strategies=["Prefer primary filings."])
        )
        is True
    )
    assert has_startup_guidance(MemorySnapshot()) is False


@pytest.mark.asyncio
async def test_a_leads_only_session_still_offers_the_procedural_lookup(
    tracker: Tracker,
) -> None:
    """The same rule as it reaches the provider request."""
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Recall procedural guidance.",
                "query_memory",
                '{"query": "quantum"}',
            ),
            finish("No further lookup.", "Three angles matter."),
        ],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    state = _state(
        memory_context=MemorySnapshot(
            similar_findings=[
                Finding(
                    content="Shor's algorithm breaks RSA.",
                    source_url="https://example.test/shor",
                    source_title="Shor 1994",
                    extracted_at="2026-01-01T00:00:00+00:00",
                    confidence=0.99,
                    related_sub_topic="Cryptography",
                )
            ]
        )
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(state)

    assert [
        definition.name for definition in completer.react_calls[0].tools
    ] == ["query_memory", "web_search"]


def test_the_inventory_excludes_the_reserved_omission_reference() -> None:
    """The frozen denominator counts evidence targets, not recorded gaps."""
    omission = EvidenceTarget(
        target_id="topic-03-target-01",
        coverage_id="topic-03",
        question=ORIGINAL_QUESTION_OMISSION_REFERENCE,
        required_dimensions=["original question coverage"],
        required=True,
        critical=True,
        support_policy="independent_pair",
    )
    real = EvidenceTarget(
        target_id="topic-03-target-02",
        coverage_id="topic-03",
        question="What does the third topic report?",
        required_dimensions=["fact"],
        required=True,
        critical=False,
        support_policy="independent_pair",
    )
    sub_topic = SubTopic(
        coverage_id="topic-03",
        title="Reforms",
        rationale="Reforms are load-bearing.",
        search_queries=["reforms 2026"],
        success_criteria=["A named source about reforms."],
        priority=1,
        evidence_targets=[omission, real],
    )

    assert inventory_target_ids([sub_topic]) == ["topic-03-target-02"]


@pytest.mark.asyncio
async def test_a_second_planning_pass_cannot_re_anchor_the_frozen_contract(
    tracker: Tracker,
) -> None:
    """Section 2.3: the question, scope, and as-of date are frozen.

    A later planning pass — a refinement, a resume, a replan after a failure —
    must not move a session's as-of date or widen its scope: every target
    already stamped carries the original obligation, and re-anchoring would
    silently change what "current" means for all of them.
    """
    frozen = _contract("What are the storage rules in the United States?")
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    state = merge_research_state(
        _state("What are the storage rules in the United States?"),
        {
            "answer_contract": frozen,
            "initial_target_ids": ["topic-01-target-01"],
        },
    )
    # The later pass is asked a *different* question with no geography and no
    # clock-relative anchor; neither may reach the contract.
    state = state.model_copy(
        update={"original_question": "What are the storage rules?"}
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert "answer_contract" not in outcome.state_update
    assert outcome.result is not None
    assert outcome.result.answer_contract == frozen
    assert outcome.result.answer_contract.geographic_scope == "United States"
    assert outcome.result.answer_contract.as_of_date == "2026-09-16"
    # Every newly stamped obligation carries the frozen period and geography.
    for sub_topic in outcome.result.sub_topics:
        for target in sub_topic.evidence_targets:
            assert "geography: United States" in target.required_dimensions
    plan_request = completer.calls[0][2][1].content
    assert "- Scope: United States" in plan_request


def test_a_frozen_contract_is_returned_unchanged_and_nothing_is_filled() -> None:
    """A field the frozen question never asked for is not a hole to fill.

    ``requested_word_limit = None`` records that the frozen question asked for
    no particular length. A word limit can only appear in the new derivation by
    coming from a *different* question, so adopting it would answer a request
    this session never received — and the only thing the round-one "fill absent
    fields" rule could ever fill was exactly that, which is why the fill is
    gone rather than made to persist.
    """
    frozen = _contract("What are the current constraints?")
    later = derive_answer_contract(
        question="What are the constraints in California?",
        now=_CLOCK_NOW,
    )
    with_limit = derive_answer_contract(
        question="What are the constraints?",
        now=_CLOCK_NOW,
        requested_word_limit=400,
    )

    assert frozen_contract_for(frozen, later) == frozen
    assert frozen_contract_for(frozen, with_limit) == frozen
    assert frozen_contract_for(frozen, with_limit).requested_word_limit is None
    assert frozen_contract_for(frozen, with_limit).geographic_scope == (
        frozen.geographic_scope
    )


@pytest.mark.asyncio
async def test_recalled_findings_are_leads_for_planning_not_premises(
    tracker: Tracker,
) -> None:
    """A recalled finding may suggest where to look; it settles nothing."""
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Recall prior work.", "query_memory", '{"query": "qec"}'),
            finish("No further lookup.", "Three angles matter."),
        ],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    state = _state(
        memory_context=MemorySnapshot(
            similar_findings=[
                Finding(
                    content="Shor's algorithm breaks RSA.",
                    source_url="https://example.test/shor",
                    source_title="Shor 1994",
                    extracted_at="2026-01-01T00:00:00+00:00",
                    confidence=0.99,
                    related_sub_topic="Cryptography",
                )
            ]
        )
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    plan_request = completer.calls[0][2][1].content
    assert "Shor's algorithm breaks RSA." in plan_request
    assert "leads, not evidence" in plan_request
    assert "never a settled premise" in plan_request
    for sub_topic in outcome.result.sub_topics:
        for target in sub_topic.evidence_targets:
            assert "Shor" not in target.question


def test_graph_budgets_let_the_planner_look_once_and_the_rest_work() -> None:
    """The shipped budgets total one planner lookup and no zero-tool agent
    that still needs a tool."""
    settings = load_config(str(Path("config.yaml")))

    assert settings.agents.tool_budget_for("planner") == 1
    assert settings.agents.tool_budget_for("researcher") == 10
    assert settings.agents.tool_budget_for("fact_checker") == 10
    assert settings.agents.tool_budget_for("source_evaluator") == 0
    assert settings.agents.tool_budget_for("synthesizer") == 0
    # Task 8 sets critic: 0; until then the critic keeps the global budget.
    assert settings.agents.tool_budget_for("critic") == 10


@pytest.mark.asyncio
async def test_the_plan_and_review_calls_carry_distinct_fingerprints(
    tracker: Tracker,
) -> None:
    """Each planner call records the configuration it was made under."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
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

    assert set(outcome.call_fingerprints) == {
        "ReactDecision",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    }
    assert len(set(outcome.call_fingerprints.values())) == 3
    assert agent.prompt_version == "planner-2"

    tighter = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    other = _planner(
        tracker,
        tighter,
        config=AgentRuntimeConfig(
            max_iterations=3,
            tool_budget=3,
            planner_final_max_tokens=4096,
        ),
    )

    async with tracker.session_span("session-1", "q"):
        changed = await other.run(_state())

    assert changed.call_fingerprints["ResearchPlanDraft"] != (
        outcome.call_fingerprints["ResearchPlanDraft"]
    )


@pytest.mark.asyncio
async def test_an_extension_through_the_agent_keeps_both_inventories(
    tracker: Tracker,
) -> None:
    """The extension appends; the initial inventory and contract are frozen."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(),
            ResearchPlanDraft(
                sub_topics=[_draft("Siting and permitting", priority=4)]
            ),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        planned = await agent.run(
            _state("What limits battery storage deployment?")
        )
        state = merge_research_state(
            _state("What limits battery storage deployment?"),
            planned.state_update,
        )
        extended = await agent.extend_plan(
            state, omission="siting and permitting"
        )

    assert extended.extension is True
    update = agent.state_update(
        extended, ReActRun(agent_name="planner", stop_reason="finished")
    )
    assert "answer_contract" not in update
    assert update["expanded_target_ids"] == ["topic-04-target-01"]
    # Only the addition crosses the state boundary; the plan already in state
    # supplies everything else.
    assert [sub_topic.coverage_id for sub_topic in update["sub_topics"]] == [
        "topic-04"
    ]
    merged = merge_research_state(state, update)
    assert merged.initial_target_ids == [
        "topic-01-target-01",
        "topic-02-target-01",
        "topic-03-target-01",
    ]
    assert merged.expanded_target_ids == ["topic-04-target-01"]
    assert [sub_topic.coverage_id for sub_topic in merged.sub_topics] == [
        "topic-01",
        "topic-02",
        "topic-03",
        "topic-04",
    ]
    assert merged.answer_contract == state.answer_contract
    # A later omission may add; it can never remove what the first plan owed.
    preserved = merge_research_state(
        merged, {"initial_target_ids": ["topic-01-target-01"]}
    )
    assert preserved.initial_target_ids == merged.initial_target_ids
