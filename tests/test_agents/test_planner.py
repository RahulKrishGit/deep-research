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
    _PLAN_REPLY_EXAMPLES,
    EvidenceTargetDraft,
    PlannerAgent,
    PlanReviewDraft,
    ResearchPlan,
    ResearchPlanDraft,
    SubTopicDraft,
    answer_kind_for,
    apply_answer_contract,
    derive_answer_contract,
    _draft_targets,
    format_plan_problems,
    frozen_contract_for,
    geographic_scope_for,
    has_startup_guidance,
    invented_tolerances,
    inventory_target_ids,
    plan_messages,
    plan_review_messages,
    stale_year_anchors,
    target_problems,
    validate_plan_draft,
)
from deep_research.agents.prompts import AgentTask, STRUCTURED_REQUEST_END
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.cli import render_warnings
from deep_research.graph.state import HALTING_ERROR_TYPES, is_halted
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import AgentRuntimeConfig, load_config
from deep_research.utils.types import (
    ORIGINAL_QUESTION_OMISSION_REFERENCE,
    AnswerContract,
    EvidenceTarget,
    Finding,
    MemorySnapshot,
    ResearchError,
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
    required: bool = True,
    measure: str = "benchmark result",
    **fields: str,
) -> EvidenceTargetDraft:
    return EvidenceTargetDraft(
        question=question, required=required, measure=measure, **fields
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
    assert MIN_SUB_TOPICS == 1
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
        "the plan has 0 valid sub-topics; produce between 1 and 7"
    ]


def test_a_one_part_plan_is_valid() -> None:
    _, problems = validate_plan_draft(_plan("Alpha"))

    assert problems == []


def test_a_plan_with_too_many_sub_topics_is_rejected() -> None:
    _, problems = validate_plan_draft(
        _plan("A", "B", "C", "D", "E", "F", "G", "H")
    )

    assert problems == [
        "the plan has 8 valid sub-topics; produce between 1 and 7"
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
        "the plan has 8 valid sub-topics; produce between 1 and 7"
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

    assert rendered.startswith("The plan under repair is printed above.")
    assert "each names the target, the sub-topic or the plan it concerns" in rendered
    assert "- problem one" in rendered
    assert "- problem two" in rendered
    # A draft with no valid sub-topic prints no plan, and the wrapper must not
    # point at one.
    unprinted = format_plan_problems(["problem one"], plan_printed=False)
    assert "printed above" not in unprinted
    assert unprinted.startswith("The previous plan could not be validated")
    assert "- problem one" in unprinted


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
    assert "between 1 and 7" in body
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
    one_part, _ = validate_plan_draft(_plan("Alpha"))

    assert ResearchPlan(sub_topics=one_part).repair_attempted is False
    with pytest.raises(ValidationError):
        ResearchPlan(sub_topics=[])


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
                "required": True,
                "measure": "benchmark result",
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
    plan_budget = AgentRuntimeConfig().planner_final_max_tokens
    assert completer.react_budgets == [decision_budget, decision_budget]
    # Both plan-side structured calls carry the planner's own budget: the
    # review is a tool-free call about the plan, not a ReAct decision. The
    # numbers stay unpinned here -- the shipping values have their own tests
    # in tests/test_config.py -- so this test keeps asserting the separation
    # rather than repeating one value twice.
    assert completer.budgets == [plan_budget, plan_budget]


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
    retry_error = ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=32768,
            usage=TokenUsage(input_tokens=5, output_tokens=32768),
            request_attempt=1,
            structured_attempt=1,
        )
    )
    agent = _planner(
        tracker,
        ScriptedCompleter(
            decisions=[finish("No lookup needed.", "Three angles matter.")],
            # A truncated draft is re-asked once; the retry truncates too.
            outputs=[provider_error, retry_error],
        ),
    )

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    # The cause is the retry's own truncation (its telemetry, not the first
    # call's), as a redacted copy: the typed provider cause is kept, the
    # provider response it was read from is not.
    cause = caught.value.__cause__
    assert isinstance(cause, ProviderOutputLimitError)
    assert cause.telemetry == retry_error.telemetry
    assert cause.telemetry != provider_error.telemetry
    assert cause is not retry_error
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


@pytest.mark.asyncio
async def test_a_plan_that_stays_invalid_fails_the_session(
    tracker: Tracker,
) -> None:
    """A second structurally invalid plan is still fatal, and labelled.

    Nothing in either attempt could be handed to the researcher, so the run
    fails as it did before the gate policy changed. The two attempts' problems
    stay distinguishable: the merged, unlabelled list is what made the live run
    3 diagnosis read draft defects as the repaired plan's.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            ResearchPlanDraft(sub_topics=[]),
            _plan("A", "B", "C", "D", "E", "F", "G", "H"),
        ],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        "draft: the plan has 0 valid sub-topics; produce between 1 and 7",
        "repair: the plan has 8 valid sub-topics; produce between 1 and 7",
    )


def _targetless_plan() -> ResearchPlanDraft:
    """One sub-topic carries no evidence target at all."""
    return ResearchPlanDraft(
        sub_topics=[
            _draft("Queue totals", priority=1),
            _draft("Withdrawn capacity", priority=2),
            _draft("Reforms", priority=3, evidence_targets=[]),
        ]
    )


def _assertion_plan() -> ResearchPlanDraft:
    """Every target is written as an assertion rather than a question."""
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                title,
                priority=index,
                evidence_targets=[_target("Alpha reports the benchmark result.")],
            )
            for index, title in enumerate(
                ("Queue totals", "Withdrawn capacity", "Reforms"), start=1
            )
        ]
    )


def _stale_draft() -> ResearchPlanDraft:
    """A usable draft whose only defect is an advisory stale anchor.

    The anchor sits in the one target of the first sub-topic, so the shipped
    plan loses that target *and* the sub-topic left without any: a plan does not
    ship the defect its own repair was told about
    (test_a_surviving_advisory_target_is_dropped_not_recorded).
    """
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                "Interconnection",
                priority=1,
                evidence_targets=[
                    _target("What are the latest 2019 interconnection figures?")
                ],
            ),
            _draft("Queue totals", priority=2),
            _draft("Reforms", priority=3),
        ]
    )


@pytest.mark.asyncio
async def test_a_sub_topic_without_targets_is_structurally_fatal(
    tracker: Tracker,
) -> None:
    """A sub-topic carrying no target cannot be researched, so it still stops.

    The 1-4 target count is a structural rule, not an advisory one: a plan with
    an empty sub-topic is one no pass can execute. Filing the count line under
    the advisory list let exactly that plan reach the researcher with nothing
    but a recoverable record, where the parent commit raised on the same input.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_targetless_plan(), _targetless_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert failure.value.problems == (
        "draft: topic-03 proposes 0 evidence targets; every sub-topic carries "
        "between 1 and 4",
        "repair: topic-03 proposes 0 evidence targets; every sub-topic carries "
        "between 1 and 4",
    )
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
    ]


@pytest.mark.asyncio
async def test_an_assertion_target_is_structurally_fatal(tracker: Tracker) -> None:
    """A target written as an assertion is not an obligation to research.

    Like the target count, the question form is structural: a criterion phrased
    as a finding asks the researcher to confirm what the plan already assumes.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_assertion_plan(), _assertion_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as failure:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    assert list(failure.value.problems) == [
        f"{plan}: topic-0{index}-target-01 is written as an assertion; request "
        "the unknown as a question instead"
        for plan in ("draft", "repair")
        for index in (1, 2, 3)
    ]


@pytest.mark.asyncio
async def test_a_target_level_structural_defect_falls_back_to_a_valid_repair(
    tracker: Tracker,
) -> None:
    """Only the both-invalid case is fatal; a repair that fixes it is used."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_targetless_plan(), _sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Cryptography",
        "Hardware timelines",
        "Mitigations",
    ]
    assert outcome.result.repair_attempted is True
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    assert _plan_defects(outcome.state_update["errors"]) == []


@pytest.mark.asyncio
async def test_a_failed_lint_repair_keeps_a_usable_draft(tracker: Tracker) -> None:
    """A repair that cannot be produced does not end a run whose draft is usable.

    Repairs are the heaviest plan request — live run 2 truncated on one — and a
    draft that is structurally researchable is worth more than no report at all.
    The failed repair is recorded against the repair, and the draft's own
    advisory defect is removed with the target it named rather than recorded: a
    plan does not ship the defect its own repair was told about. That is why the
    first sub-topic is absent here — its only target carried the defect, and a
    plan never ships a sub-topic with no obligations.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _stale_draft(),
            _output_limit_error(),
            _output_limit_error(),
            _review(),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What are the current interconnection constraints?")
        )

    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Queue totals",
        "Reforms",
    ]
    assert outcome.result.repair_attempted is True
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    records = _plan_defects(outcome.state_update["errors"])
    assert [record.details for record in records] == [
        {
            "stage": "plan_repair",
            "plan": "repair",
            "problems": [
                "repair: the plan repair raised ProviderOutputLimitError",
                "repair: the planner provider failed while requesting the "
                "final plan draft",
            ],
        },
    ]


@pytest.mark.asyncio
async def test_the_lint_repair_request_carries_unlabelled_problems(
    tracker: Tracker,
) -> None:
    """The labels belong to the records, not to the model's request.

    Every problem the planner reports is labelled with the plan it came from,
    but the corrective instruction the model reads is the one it has always
    read: a plan label in the prompt is text no model needs, and a silent
    change to model-visible input.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_stale_draft(), _sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state("What are the current interconnection constraints?"))

    repair_request = completer.calls[1][2][1].content
    # The plan the problems name is printed above the repair list, so an id in
    # that list names something the model can see and correct: the defect Fable
    # blocked on, pinned on the request the provider actually receives.
    assert "# Plan under repair (return this plan corrected, not unchanged)" in (
        repair_request
    )
    assert repair_request.index("# Plan under repair") < repair_request.index(
        "# Repair\n"
    )
    assert "topic-01-target-01" in repair_request.split("# Repair\n", 1)[0]
    assert repair_request.split("# Repair\n", 1)[1].split(
        f"\n\n{STRUCTURED_REQUEST_END}", 1
    )[0] == (
        "The plan under repair is printed above. Fix every problem listed "
        "below — each names the target, the sub-topic or the plan it concerns "
        "— and return that plan corrected.\n"
        "- topic-01-target-01 anchors currency to 2019 for a session as of "
        "2026-09-16; ask for the latest available evidence instead"
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


def test_planner_regression_plan_instruction_scopes_benefits_to_the_question() -> None:
    """Balanced coverage is asked for when the question asks for it, and only then.

    The instruction used to *require* a benefits-and-risks sub-topic for any
    technology question, which is the scope widening the plan's own review
    rules against ("Name any target that widens the scope"). The two prompts
    contradicted each other, and the plan-file reader has no way to tell which
    one won.
    """
    task = AgentTask(instruction="Some research question.")
    messages = plan_messages(task, _run())
    rendered = " ".join(message.content for message in messages)
    assert "benefits" in rendered
    assert "risks" in rendered
    assert "when the question asks about them" in rendered
    # The lexical ban is gone, so what replaces it has to keep the terms it
    # permits out of the plan's assertions.
    assert "Do not assert a search term as a fact" in rendered
    assert "is a lead" in rendered


def test_planner_regression_plan_instruction_names_no_support_policy() -> None:
    """PD-16: no part of the plan request asks the model for a policy.

    The draft field, the instruction paragraph and the reply example that
    taught the vocabulary are gone with the Fact Checker, so a plan can no
    longer propose a policy — while the guidance that keeps the plan inside
    the question (no second publisher where one issuer settles the fact, and
    no metadata dimension the question never asked for) stays.
    """
    task = AgentTask(instruction="Some research question.")
    messages = plan_messages(task, _run())
    rendered = " ".join(message.content for message in messages)

    assert "support_policy" not in rendered
    assert "primary_attribution" not in rendered
    assert "independent_pair" not in rendered
    assert "support_policy" not in " ".join(
        part for example in _PLAN_REPLY_EXAMPLES for part in example
    )
    assert "any further body's evidence where the question asks for several without naming them" in rendered
    assert (
        "A target never asks for a publication date, retrieval date or "
        "edition as its measure" in rendered
    )


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


def test_a_question_that_names_a_year_keeps_that_year_as_its_period() -> None:
    """The year the question names is its period, not an evidence cutoff.

    A question about 2021 is answered about 2021 — that is what the evidence
    period says — but the *cutoff* the user never stated is not inferred from
    it: the contract reads from the latest evidence the run can reach, so a
    later revision of the 2021 figure stays usable (user decision 2, review
    rank 4). Only an explicit "as of <date>" freezes the date.
    """
    contract = _contract("What did the 2021 capacity market rules require?")

    assert contract.as_of_date == "2026-09-16"
    assert "the period the question names (2021)" in (
        contract.evidence_period_requirement
    )
    assert "2021-12-31" not in contract.scope_statement
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


@pytest.mark.parametrize(
    "question",
    (
        "What did the 2 024 words of the act change in 2025?",
        "In 2024 words like \"carbon border\" entered the debate; what changed?",
        "What were the 2025 words of the treaty, and what do they require?",
    ),
)
def test_a_year_before_words_is_not_a_reader_length(question: str) -> None:
    """A length frame is what asks for a length, not a number near "words".

    The pattern read any two-to-seven digit number followed by "words", so a
    question whose own subject sits in that shape ("the 2025 words of the
    treaty", "in 2024 words like ... entered the debate") handed the writer's
    contract a four-digit "reader length" of 2024 or 2025 words — a limit
    nobody asked for, published as the reader's own request.
    """
    assert _contract(question).requested_word_limit is None


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


def test_targets_carry_locally_stamped_ids() -> None:
    contract = _contract("What are the current interconnection constraints?")
    sub_topics, problems = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    title,
                    priority=index,
                    evidence_targets=[
                        _target(
                            f"What does {title} report first?",
                            measure="interconnection constraints",
                        ),
                        _target(
                            f"What does {title} report second?",
                            measure="interconnection constraints",
                        ),
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
    # The draft's own fields stand: the contract no longer adds a dimension.
    assert first.measure == "interconnection constraints"
    assert target_problems(stamped, contract) == []


def test_an_unrequested_forecast_edition_requires_plan_repair() -> None:
    """'2025 forecast' does not authorize a planner-chosen January edition."""
    contract = _contract(
        "How much grid-scale battery storage capacity was added in the "
        "United States in 2024, and what do the latest forecasts project for 2025?"
    )
    topic = SubTopic(
        coverage_id="topic-02",
        title="EIA 2025 forecast vintage",
        rationale="Report the issuer's forecast and release vintage.",
        search_queries=["EIA 2025 battery storage additions forecast"],
        success_criteria=["Date the forecast the issuer publishes."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-02-target-01",
                coverage_id="topic-02",
                question=(
                    "What did the EIA Short-Term Energy Outlook's January 2025 "
                    "edition project for 2025 US utility-scale battery storage "
                    "capacity additions?"
                ),
                required=True,
                measure="projected annual utility-scale battery storage capacity additions",
                period="calendar year 2025 as projected in the January 2025 EIA Short-Term Energy Outlook",
            )
        ],
    )
    assert any(
        "January 2025" in issue and "vintage" in issue
        for issue in target_problems([topic], contract)
    )


def test_a_non_forecast_target_naming_a_project_noun_is_not_flagged() -> None:
    """"Counting projects larger than 1 MW" is a noun, not a forecast cue.

    The ungrouped cue alternation matched "project" as a bare substring of
    "projects", so a non-forecast target's own qualifier ("counting projects
    larger than 1 MW") was read as forecast wording, and the period
    requirement's own "December 2024" was flagged as an unrequested vintage
    even though the target asks about nothing but calendar year 2024.
    """
    contract = _contract(
        "How much battery storage capacity did EIA report was added in "
        "the United States by the end of 2024?"
    )
    topic = SubTopic(
        coverage_id="topic-01",
        title="EIA 2024 additions",
        rationale="Report the issuer's own 2024 figure.",
        search_queries=["EIA 2024 battery storage additions"],
        success_criteria=["Cite EIA's own count."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-01-target-01",
                coverage_id="topic-01",
                question=(
                    "What 2024 US utility-scale battery storage capacity "
                    "additions, counting projects larger than 1 MW, does "
                    "EIA report?"
                ),
                required=True,
                measure="annual utility-scale battery storage capacity additions",
                period="December 2024",
            )
        ],
    )
    assert not any(
        "vintage" in issue for issue in target_problems([topic], contract)
    )


def test_a_period_requirements_own_quarter_is_not_flagged_as_a_vintage() -> None:
    """A "period:" requirement's own spelling is not a forecast edition.

    The question asks for "the fourth quarter of 2025" and the period
    requirement spells the same quarter "Q4 2025"; neither is a publication
    edition the plan invented. Reading every date inside a period
    requirement as a candidate edition flagged it anyway, because the
    question's own spelling ("fourth quarter") never literally contains the
    requirement's abbreviated one ("Q4 2025").
    """
    contract = _contract(
        "What does the EIA forecast for battery storage additions in the "
        "fourth quarter of 2025?"
    )
    topic = SubTopic(
        coverage_id="topic-02",
        title="EIA Q4 2025 forecast",
        rationale="Report the issuer's own quarterly forecast.",
        search_queries=["EIA Q4 2025 battery storage forecast"],
        success_criteria=["Cite EIA's own forecast."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-02-target-01",
                coverage_id="topic-02",
                question=(
                    "What does EIA's outlook project for battery storage "
                    "additions in the fourth quarter of 2025?"
                ),
                required=True,
                measure="projected quarterly battery storage capacity additions",
                period="Q4 2025",
            )
        ],
    )
    assert not any(
        "vintage" in issue for issue in target_problems([topic], contract)
    )


def test_a_month_of_year_edition_the_question_already_names_is_not_flagged() -> (
    None
):
    """"January of 2025" and "January 2025" name the same edition.

    The question phrases the edition naturally ("released in January of
    2025"); the target's own dimensions spell the identical edition without
    "of" ("January 2025 edition"). A literal substring check never reads
    these as the same date, so the plan's own edition was flagged as
    unrequested even though the question named exactly it.
    """
    contract = _contract(
        "What did the EIA Short-Term Energy Outlook, released in January "
        "of 2025, project for battery storage additions in 2025?"
    )
    topic = SubTopic(
        coverage_id="topic-02",
        title="EIA January 2025 forecast vintage",
        rationale="Report the issuer's forecast and release vintage.",
        search_queries=["EIA January 2025 battery storage forecast"],
        success_criteria=["Date the forecast the issuer publishes."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-02-target-01",
                coverage_id="topic-02",
                question=(
                    "What did the EIA Short-Term Energy Outlook's January "
                    "2025 edition project for battery storage additions in "
                    "2025?"
                ),
                required=True,
                measure="projected annual battery storage capacity additions",
            )
        ],
    )
    assert not any(
        "vintage" in issue for issue in target_problems([topic], contract)
    )


def test_a_forecast_verb_naming_no_edition_is_not_flagged() -> None:
    """"EIA project[s]" the bare verb is not "projected": no cue, no flag.

    The ungrouped cue alternation read "project" inside "EIA project the
    grid will add" as a forecast cue, and then flagged the period
    requirement's own "December 2025" as an invented edition. Grouping the
    cue to whole words ("forecast", "forecasted", "projected", "projection",
    "outlook") leaves a bare present-tense "project" unmatched.
    """
    contract = _contract(
        "How much battery storage capacity will be added by the end of "
        "2025?"
    )
    topic = SubTopic(
        coverage_id="topic-01",
        title="EIA 2025 additions",
        rationale="Report the issuer's own count.",
        search_queries=["EIA battery storage additions 2025"],
        success_criteria=["Cite EIA's own count."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-01-target-01",
                coverage_id="topic-01",
                question=(
                    "What battery storage capacity does EIA project the "
                    "grid will add by the end of 2025?"
                ),
                required=True,
                measure="annual battery storage capacity additions",
                period="December 2025",
            )
        ],
    )
    assert not any(
        "vintage" in issue for issue in target_problems([topic], contract)
    )


def test_audit2_forecast_vintages_stay_flagged() -> None:
    """The real audit-2 topic-02 and topic-04 targets still name unrequested editions.

    Real targets from the audited run: topic-02 pins the January 2025 STEO
    edition, topic-04 pins the Q1 2025 US Energy Storage Monitor edition, and
    the original question names neither. Both are genuine, intended flags
    that must survive every false-positive fix above.
    """
    contract = _contract(
        "How much grid-scale battery storage capacity was added in the "
        "United States in 2024, and what do the latest forecasts project "
        "for 2025?"
    )
    topic_02 = SubTopic(
        coverage_id="topic-02",
        title="EIA 2025 forecast vintage",
        rationale="Report the issuer's forecast and release vintage.",
        search_queries=["EIA 2025 battery storage additions forecast"],
        success_criteria=["Date the forecast the issuer publishes."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-02-target-01",
                coverage_id="topic-02",
                question=(
                    "What did the EIA Short-Term Energy Outlook's January "
                    "2025 edition project for 2025 US utility-scale battery "
                    "storage capacity additions, in the unit that edition "
                    "reports?"
                ),
                required=True,
                measure="projected annual utility-scale battery storage capacity additions",
                period="calendar year 2025 as projected in the January 2025 EIA Short-Term Energy Outlook",
            )
        ],
    )
    topic_04 = SubTopic(
        coverage_id="topic-04",
        title="US Energy Storage Monitor 2025 forecast vintage",
        rationale="Report the Monitor's forecast and release vintage.",
        search_queries=["US Energy Storage Monitor Q1 2025 forecast"],
        success_criteria=["Date the forecast the Monitor publishes."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-04-target-01",
                coverage_id="topic-04",
                question=(
                    "What did the US Energy Storage Monitor's Q1 2025 "
                    "edition project for 2025 US grid-scale battery storage "
                    "capacity additions, in the unit that edition reports?"
                ),
                required=True,
                measure="projected annual grid-scale battery storage capacity additions",
                period="calendar year 2025 as projected in the Q1 2025 US Energy Storage Monitor",
            )
        ],
    )
    issues = target_problems([topic_02, topic_04], contract)
    assert any(
        "January 2025" in issue and "vintage" in issue for issue in issues
    )
    assert any("Q1 2025" in issue and "vintage" in issue for issue in issues)


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

    The comparison answer form — "the same dimension, measured or described,
    for every option compared, on one shared basis" — is printed in the
    answer contract every plan request carries, and Section 2.3 judges a
    target answered only when the obligations that contract states are
    satisfied. Applying it to "how many projects waited more than 5 years?"
    makes an ordinary quantity question unsatisfiable, so an inequality counts
    as a comparison only when it names a referent.
    """
    assert answer_kind_for(question, clock_year=2026) == expected


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [
        (
            "How many projects waited more than one year?",
            "factual",
        ),
        (
            "How many projects waited more than five years?",
            "factual",
        ),
        (
            "What are the requirements for tariffs greater than ten percent?",
            "constraints",
        ),
        (
            "What are the requirements for tariffs greater than half the baseline?",
            "constraints",
        ),
    ],
)
def test_spelled_out_quantity_thresholds_keep_the_answer_form(
    question: str, expected_kind: str
) -> None:
    """Spelled-out quantities after an inequality are thresholds, not referents."""
    assert answer_kind_for(question, clock_year=2026) == expected_kind


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [
        (
            "How Many Projects Waited More Than Five Years?",
            "factual",
        ),
        (
            "What Are the Requirements for Tariffs Greater Than Ten Percent?",
            "constraints",
        ),
    ],
)
def test_title_case_quantity_thresholds_keep_the_answer_form(
    question: str, expected_kind: str
) -> None:
    """Case must not turn title-cased quantities into comparison referents."""
    assert answer_kind_for(question, clock_year=2026) == expected_kind


@pytest.mark.parametrize(
    "question",
    [
        "Is permitting slower than texas?",
        "Is permitting slower than EU?",
        "Is interconnection slower than in PJM?",
        "Is interconnection slower than in pJm?",
        "Did the project cost more than other competitors?",
        "Did the project cost more than other alternatives?",
    ],
)
def test_case_insensitive_explicit_referents_select_comparison(
    question: str,
) -> None:
    """Named scopes and explicitly contrasted sets are comparisons."""
    assert answer_kind_for(question, clock_year=2026) == "comparison"


@pytest.mark.parametrize(
    "question",
    [
        "Is the score higher than nth?",
        "Is the rate greater than limits?",
    ],
)
def test_bare_underspecified_tokens_do_not_create_comparison_contracts(
    question: str,
) -> None:
    """Token shape alone cannot prove that a second referent was supplied."""
    assert answer_kind_for(question, clock_year=2026) == "factual"


@pytest.mark.parametrize(
    "question",
    [
        "Is the score higher than kth?",
        "Is the rate greater than thresholds?",
        "Is the rate greater than ceilings?",
        "Is the rate greater than bounds?",
        "Is interconnection slower than pJm?",
        "Did the project cost more than competitors?",
        "Did the project cost more than alternatives?",
    ],
)
def test_bare_acronym_and_plural_shapes_remain_ambiguous_without_a_signal(
    question: str,
) -> None:
    """Ambiguous bare tokens need a named scope or contrastive-set signal."""
    assert answer_kind_for(question, clock_year=2026) == "factual"


@pytest.mark.parametrize(
    "question",
    [
        "How many projects ranked higher than first percentile?",
        "How Many Projects Ranked Higher Than Second Percentile?",
        "How many projects ranked hIgHeR tHaN eLeVeNtH pErCeNtIlE?",
        "HOW MANY PROJECTS RANKED HIGHER THAN TWELFTH PERCENTILE?",
        "How many projects ranked higher than twenty-first percentile?",
        "How many projects ranked higher than 95th percentile?",
    ],
)
def test_ordinal_percentile_thresholds_are_not_comparisons(
    question: str,
) -> None:
    """An unknown ordinal is a threshold, not an inferred direct referent."""
    assert answer_kind_for(question, clock_year=2026) == "factual"


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [
        (
            "How many portfolios contain more than 2000 projects?",
            "factual",
        ),
        (
            "How Many Portfolios Contain More Than 2000 Projects?",
            "factual",
        ),
        (
            "What tariff requirements apply to rates greater than the statutory limit?",
            "constraints",
        ),
        (
            "What Legal Obligations Apply Above the Legal Maximum?",
            "constraints",
        ),
        (
            "What requirements apply bElOw tHe MiNiMuM rEqUiReMeNt?",
            "constraints",
        ),
    ],
)
def test_counts_and_named_limits_keep_noncomparison_contracts(
    question: str,
    expected_kind: str,
) -> None:
    """A count or named bound cannot become a comparison by lexical fallback."""
    assert answer_kind_for(question, clock_year=2026) == expected_kind


@pytest.mark.parametrize(
    "question",
    [
        "Is the rate greater than maximum?",
        "IS THE RATE GREATER THAN MINIMUM?",
        "Is the rate greater than LiMiT?",
        "Is the temperature lower than floor?",
        "IS THE SCORE HIGHER THAN CEILING?",
        "Is the score higher than first?",
        "Is the rate cheaper than minimum?",
        "Is the rate more expensive than ceiling?",
    ],
)
def test_bare_terminal_bounds_remain_ambiguous(question: str) -> None:
    """A singular bound is not positive evidence of a second referent."""
    assert answer_kind_for(question, clock_year=2026) == "factual"


def test_a_four_digit_count_does_not_reanchor_the_frozen_contract() -> None:
    """A count shaped like a year remains a current quantity obligation."""
    contract = _contract(
        "How many portfolios contain more than 2000 projects?"
    )

    assert contract.answer_kind == "factual"
    assert contract.as_of_date == "2026-09-16"
    assert "evidence available as of 2026-09-16" in (
        contract.evidence_period_requirement
    )
    assert "2000" not in contract.evidence_period_requirement


@pytest.mark.parametrize(
    "question",
    [
        "How many portfolios contain more than 2000 projects?",
        "Are there more than 2000 projects?",
    ],
)
def test_four_digit_count_syntax_keeps_a_current_contract(
    question: str,
) -> None:
    """Positive count syntax, rather than token width, identifies a count."""
    contract = _contract(question)

    assert contract.answer_kind == "factual"
    assert contract.as_of_date == "2026-09-16"
    assert contract.evidence_period_requirement == (
        "evidence available as of 2026-09-16"
    )


@pytest.mark.parametrize(
    ("question", "expected_years"),
    [
        (
            "Did the 2024 federal regulation cost more than 2019 federal regulation?",
            ("2019", "2024"),
        ),
        (
            "Was the 2022 regional capacity market rule more costly than the 2018 "
            "regional capacity market rule?",
            ("2018", "2022"),
        ),
        (
            "Did the 2023 state permit requirement cost more than the 2020 state "
            "permit requirement?",
            ("2020", "2023"),
        ),
    ],
)
def test_parallel_multiword_year_work_keeps_both_historical_periods(
    question: str,
    expected_years: tuple[str, str],
) -> None:
    """A repeated year/work pair is a comparison, not a count-shaped date loss.

    Both years are the periods the answer is *about*; the contract's as-of date
    is the session clock, because the observation years are not a cutoff the
    user stated (user decision 2).
    """
    contract = _contract(question)

    assert contract.answer_kind == "comparison"
    assert contract.as_of_date == "2026-09-16"
    assert all(
        year in contract.evidence_period_requirement for year in expected_years
    )


@pytest.mark.parametrize(
    ("question", "expected_years"),
    [
        (
            "How many 2024 projects cost more than 2019 projects?",
            ("2019", "2024"),
        ),
        (
            "How many 2024 federal regulation projects cost more than 2019 "
            "federal regulation projects?",
            ("2019", "2024"),
        ),
        (
            "How many 2024 regional capacity market projects cost more than the "
            "2019 regional capacity market projects?",
            ("2019", "2024"),
        ),
        (
            "What number of 2023 state permit projects cost more than the 2020 "
            "state permit projects?",
            ("2020", "2023"),
        ),
    ],
)
def test_count_framed_parallel_year_work_keeps_every_historical_period(
    question: str,
    expected_years: tuple[str, str],
) -> None:
    """Parallel year/work evidence outranks an overlapping count operand.

    The periods are the question's own; the as-of date is the session clock,
    which is not inferred from them (user decision 2).
    """
    contract = _contract(question)

    assert contract.answer_kind == "comparison"
    assert contract.as_of_date == "2026-09-16"
    assert all(
        year in contract.evidence_period_requirement for year in expected_years
    )


@pytest.mark.parametrize(
    "question",
    [
        "Did the 2024 regulation cost more than 2019 regulation?",
        "Is permitting slower than texas?",
        "IS PERMITTING SLOWER THAN TEXAS?",
        "Is interconnection slower than in pJm?",
        "Did the project cost more than other competitors?",
        "Is permitting slower in California than in Texas?",
        "Did the 2023 regulation cost more than the 2019 one?",
        "How do solar and wind compare on cost?",
        "Is solar cheaper relative to wind?",
    ],
)
def test_explicit_comparison_shapes_keep_pair_contracts(question: str) -> None:
    """Positive comparison syntax survives the conservative fallback."""
    assert answer_kind_for(question, clock_year=2026) == "comparison"


def test_a_causal_marker_without_than_keeps_explanation_contract() -> None:
    question = "Why did queue times grow after 2019?"

    assert answer_kind_for(question, clock_year=2026) == "explanation"


@pytest.mark.parametrize(
    "question",
    [
        "What legal obligations applied above the legal maximum in 2019?",
        "What minimum permit requirements applied in 2018?",
        "Which regulatory requirements were in force below the legal minimum in 2020?",
    ],
)
def test_dated_legal_constraints_keep_their_historical_form(
    question: str,
) -> None:
    """A dated constraint question is a historical one, and it still plans."""
    contract = _contract(question)
    sub_topics, problems = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    title,
                    priority=index,
                    evidence_targets=[_target(question, measure=question)],
                )
                for index, title in enumerate(
                    ("Legal text", "Administrative record", "Implementation"),
                    start=1,
                )
            ]
        )
    )

    assert problems == []
    assert contract.answer_kind == "historical"
    stamped = apply_answer_contract(sub_topics, contract)
    assert [
        target
        for topic in stamped
        for target in topic.evidence_targets
        if not target.required
    ] == []


def test_a_derived_marker_form_still_matches() -> None:
    """Derived forms of a marker match; jurisdiction names do not.

    "recent" must still reach "recently", while "indian" must never reach
    "Indiana" — one switch decides both.
    """
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


# The question the four live CLI runs used. It names 2024 as the period it is
# about and asks for 2025 forecasts. The observation years are the question's
# subject, not an evidence cutoff, so the contract reads from the run clock —
# which is why a target that mirrors the question's own wording names a year
# the as-of date has left behind, and why the co-occurrence rule reported it.
_AUDIT_QUESTION = (
    "How much grid-scale battery storage capacity was added in the United "
    "States in 2024, and what do the latest forecasts project for 2025?"
)

# The day the audit runs were made, so the contract below is the live one.
_AUDIT_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _audit_contract():
    return derive_answer_contract(question=_AUDIT_QUESTION, now=_AUDIT_NOW)


def _mirroring_audit_plan() -> ResearchPlanDraft:
    """Run 4's shape: the first topic mirrors the question's own framing."""
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                "Added capacity",
                priority=1,
                evidence_targets=[
                    _target(
                        "What do the latest EIA data show for grid-scale "
                        "battery storage capacity added in the United States "
                        "in 2024?"
                    )
                ],
                success_criteria=[
                    "The latest available figures state the 2024 additions "
                    "and the 2025 forecast."
                ],
            ),
            _draft("Queue totals", priority=2),
            _draft("Reforms", priority=3),
        ]
    )


def test_the_year_the_question_itself_names_is_never_a_stale_anchor() -> None:
    """The question's own period is its subject, not a currency claim.

    The audit question is *about* 2024 under a 2026-09-23 contract, so a target
    that mirrors the question's wording names a year below the as-of date. The
    co-occurrence rule reported exactly those targets — and criteria — in live
    runs 3 and 4, and because a reported anchor was fatal, both runs ended
    before any research started.
    """
    contract = _audit_contract()
    assert contract.as_of_date == "2026-09-23"
    sub_topics, problems = validate_plan_draft(_mirroring_audit_plan())
    assert problems == []

    stamped = apply_answer_contract(sub_topics, contract)

    assert target_problems(stamped, contract) == []


def test_the_questions_observation_years_are_not_an_evidence_cutoff() -> None:
    """User decision 2: "for 2025" is the period, not a knowledge cutoff.

    ``derive_answer_contract`` inferred 2025-12-31 from the observation years,
    so every target's binding evidence period said "as of 2025-12-31 and never
    substitute today's figures" — a cutoff the user never asked for, which
    forbids the later revisions and the published 2025 outturn the reader needs
    (review rank 4).
    """
    contract = _audit_contract()

    assert contract.as_of_date == "2026-09-23"
    assert "2025-12-31" not in contract.scope_statement
    period = contract.evidence_period_requirement
    assert "the period the question names (2024, 2025)" in period
    assert "as of 2026-09-23" in period
    # A later revision and a published outturn stay admissible — labelled for
    # what they are, beside the forecasts the question asks for.
    assert "as a forecast" in period
    assert "as an actual" in period


def test_an_explicit_as_of_date_still_freezes_the_contract() -> None:
    """The one thing that does freeze the date: a date the question states."""
    contract = _contract(
        "How much grid-scale battery storage capacity had been added in the "
        "United States as of 2025-12-31?"
    )

    assert contract.as_of_date == "2025-12-31"
    assert "as of 2025-12-31" in contract.evidence_period_requirement
    assert "2026-09-16" not in contract.evidence_period_requirement


@pytest.mark.parametrize(
    "question",
    [
        (
            "As of December 31, 2025, how much storage was operating in "
            "the US?"
        ),
        "As of the end of 2025, how much storage was operating in the US?",
        "As of 2025, how much storage was operating in the US?",
    ],
)
def test_an_explicit_as_of_phrase_freezes_the_contract(question: str) -> None:
    """A cutoff written in words freezes the date exactly like an ISO one.

    User decision 2: an explicit "as of" still freezes the date, whether it
    names a full date, "the end of" a year, or a bare year following "as
    of" — the last two meaning that year's end (review rank 2).
    """
    contract = _contract(question)

    assert contract.as_of_date == "2025-12-31"
    assert "as of 2025-12-31" in contract.evidence_period_requirement


def test_an_explicit_as_of_month_and_year_freezes_to_month_end() -> None:
    """A month and year with no day freezes to that month's last day."""
    contract = _contract(
        "As of June 2025, how much storage was operating in the US?"
    )

    assert contract.as_of_date == "2025-06-30"


def test_a_future_explicit_as_of_is_capped_at_the_clock() -> None:
    """A stated cutoff nobody has lived through yet cannot be evidence."""
    contract = _contract(
        "As of 2026, how much storage is operating in the US?"
    )

    assert contract.as_of_date == "2026-09-16"


def test_a_bare_observation_year_still_earns_no_explicit_cutoff() -> None:
    """User decision 2, restated for the natural-language reader: a bare
    "for 2025" at a 2026 clock names the period, not a cutoff — only a
    cutoff phrase freezes anything."""
    contract = _contract("What were battery storage additions for 2025?")

    assert contract.as_of_date == "2026-09-16"
    assert "2025-12-31" not in contract.scope_statement


def test_a_year_the_question_does_not_name_is_still_a_stale_anchor() -> None:
    """The exemption is the question's own years, not every older year.

    The TR-04 defect is a plan that treats an earlier year as today. 2023 is
    not a year the audit question names, so a target that anchors currency to
    it is still reported under that contract.
    """
    contract = _audit_contract()
    sub_topics, _ = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    "Added capacity",
                    priority=1,
                    evidence_targets=[
                        _target(
                            "What are the current 2023 figures for added "
                            "battery storage capacity?"
                        )
                    ],
                ),
                _draft("Queue totals", priority=2),
                _draft("Reforms", priority=3),
            ]
        )
    )

    stamped = apply_answer_contract(sub_topics, contract)

    assert target_problems(stamped, contract) == [
        "topic-01-target-01 anchors currency to 2023 for a session as of "
        "2026-09-23; ask for the latest available evidence instead"
    ]


@pytest.mark.asyncio
async def test_a_plan_mirroring_the_questions_own_years_reaches_the_review(
    tracker: Tracker,
) -> None:
    """Run 4's replay: the mirroring draft is reviewed instead of fatal.

    Run 4 scripted two structured calls (20,509 and 12,549 output tokens) and
    ended at ``graph_planning_failed`` with ``sub_topics=[]``, because the
    draft was reported, the repair was reported the same way, and the second
    report raised. The scripted model below is that run's shape — every plan
    request answers with the same question-mirroring plan — and a draft the
    contract's own question sanctions must reach the semantic review instead.
    """

    def _run_4_reply(messages, schema):
        del messages
        return (
            _mirroring_audit_plan()
            if schema is ResearchPlanDraft
            else _review()
        )

    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_run_4_reply, _run_4_reply, _run_4_reply],
    )
    agent = _planner(tracker, completer, clock=lambda: _AUDIT_NOW)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(_AUDIT_QUESTION))

    assert outcome.result is not None
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    assert outcome.result.repair_attempted is False


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


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [
        (
            "Did the 2024 regulation cost more than 2019 regulation?",
            "comparison",
        ),
        ("Is permitting slower than Texas?", "comparison"),
    ],
)
def test_direct_comparison_referents_keep_the_comparison_answer(
    question: str, expected_kind: str
) -> None:
    """Direct noun referents after ``than`` are comparisons.

    The production classifier must recognize a named year or jurisdiction as
    the second item in a comparison even when it is not introduced by ``the``
    or ``in``.
    """
    assert answer_kind_for(question, clock_year=2026) == expected_kind


def test_a_definite_threshold_noun_stays_constraints() -> None:
    """A definite threshold noun is one rule, not a comparison.

    ``greater than the 10% threshold`` names the threshold that defines a
    tariff condition; it does not compare two options.  The production
    classifier must preserve the constraints answer form for this official
    rule question.
    """
    question = "What are the requirements for tariffs greater than the 10% threshold?"

    assert answer_kind_for(question, clock_year=2026) == "constraints"


def test_an_unrelated_derived_word_does_not_match_standard_constraint_marker() -> None:
    """``standardized`` must not be treated as the ``standard`` marker.

    The production marker matcher must bound derived forms to explicit semantic
    variants.  Otherwise an unrelated ``-ized`` word can silently stamp the
    constraints answer form onto a factual cost question.
    """
    question = "What did standardized testing cost?"

    assert answer_kind_for(question, clock_year=2026) == "factual"


def test_lowercase_eu_and_uk_aliases_resolve_without_us_substring_matches() -> None:
    """Lowercase ``eu``/``uk`` aliases return jurisdictions safely.

    The production geography matcher must restore the ordinary lowercase
    aliases while keeping the case-sensitive ``US`` abbreviation: the pronoun
    ``us``, the word ``uses``, and the state name ``Indiana`` must remain
    unspecified rather than turning into country scopes.
    """
    assert geographic_scope_for("What are the rules in eu?")[0] == (
        "European Union"
    )
    assert geographic_scope_for("What are the rules in uk?")[0] == (
        "United Kingdom"
    )
    assert geographic_scope_for("Can you tell us about the rules?")[0] == (
        "unspecified"
    )
    assert geographic_scope_for("What are the uses of storage?")[0] == (
        "unspecified"
    )
    assert geographic_scope_for("What are the siting rules in Indiana?")[0] == (
        "unspecified"
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
    session's frozen contract — printed into the plan request — is what names
    the run clock's date.
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

    # What binds the reader is the contract the plan request prints, not a
    # dimension stamped onto a target (Task 5.2 stamps only the id and
    # ``required``): the session's own as-of date and evidence period.
    plan_request = completer.calls[0][2][1].content
    assert (
        "- Evidence period: the latest available evidence as of 2026-09-16"
        in plan_request
    )
    for sub_topic in outcome.result.sub_topics:
        for target in sub_topic.evidence_targets:
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


def _plan_defects(errors: list[ResearchError]) -> list[ResearchError]:
    """The non-halting plan-defect records in one state update.

    Read from the update rather than from the run object on purpose: the state
    update is what carries the records on to the ledger, the CLI and the
    quality record, so a record that never reached it would be invisible to an
    operator even though the planner had recorded it.
    """
    return [
        error
        for error in errors
        if error.error_type == "planner_plan_defects_unresolved"
    ]


@pytest.mark.asyncio
async def test_the_bounded_plan_review_records_an_unsound_confirming_verdict(
    tracker: Tracker,
) -> None:
    """The review/repair cycle stays bounded, and a stuck verdict is recorded.

    The bound is four plan-side calls: plan, review, repaired plan, confirming
    review. It is deliberately *two* reviews rather than one — a repair that is
    never re-reviewed is a plan accepted on hope — but the second verdict no
    longer ends the run: the repaired plan is structurally researchable, so it
    stands, and the reviewer's own defect list is recorded against the plan it
    judged.
    """
    question = "What limits grid-scale battery storage deployment?"
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

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(question))

    assert outcome.result is not None
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    assert len(completer.calls) <= 4
    review_calls = [
        call for call in completer.calls if call[0] == "PlanReviewDraft"
    ]
    assert MAX_PLAN_REVIEW_CALLS == 2
    assert len(review_calls) <= MAX_PLAN_REVIEW_CALLS
    records = _plan_defects(outcome.state_update["errors"])
    assert len(records) == 1
    assert records[0].recoverable is True
    assert records[0].details == {
        "stage": "confirming_review",
        "plan": "review_repair",
        "problems": [
            "review_repair: plan review found a compound obligation: "
            "target-01-01 combines two measures"
        ],
    }
    # The review request is tool-free and carries the frozen question.
    review_request = completer.calls[1][2][1].content
    assert question in review_request
    for tool_name in ("web_search", "query_memory", "web_scraper"):
        assert tool_name not in review_request


@pytest.mark.asyncio
async def test_a_biased_premise_is_named_by_the_review_and_is_recorded(
    tracker: Tracker,
) -> None:
    """A premise that assumes the answer is repaired, then recorded.

    "The plan is biased" is a meaning defect, so the review is what names it,
    and the repair prompt must carry the reviewer's finding rather than a
    generic complaint. A premise no repair removed is recorded against the plan
    that carries it instead of ending the run.
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

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    repair_request = completer.calls[2][2][1].content
    assert premise in repair_request
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    assert len(completer.calls) <= 4
    records = _plan_defects(outcome.state_update["errors"])
    assert [record.details["plan"] for record in records] == ["review_repair"]
    assert records[0].details == {
        "stage": "confirming_review",
        "plan": "review_repair",
        "problems": [
            f"review_repair: plan review found an unsupported premise: {premise}"
        ],
    }


@pytest.mark.asyncio
async def test_a_scope_widening_target_is_named_by_the_review_and_is_recorded(
    tracker: Tracker,
) -> None:
    """A target that widens the frozen scope is named, then recorded.

    The review can only judge the scope if the request carries the frozen
    contract, so the request is inspected as well as the outcome: the reviewer
    must see the contract's geography and as-of date beside the plan. The
    finding then labels the plan it was found on, which is what the merged,
    unlabelled list of run 3 could not say.
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

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What are the storage rules in the United States?")
        )

    assert outcome.result is not None
    review_request = completer.calls[1][2][1].content
    assert "- Scope: United States" in review_request
    assert "- As of: 2026-09-16" in review_request
    assert "widen" in review_request
    assert len(completer.calls) <= 4
    records = _plan_defects(outcome.state_update["errors"])
    assert records[0].details == {
        "stage": "confirming_review",
        "plan": "review_repair",
        "problems": [
            "review_repair: plan review found an unsupported premise: "
            f"target-01-01 widens the frozen scope: {widening}"
        ],
    }


def _tolerance_plan() -> ResearchPlanDraft:
    """One plan carrying exactly one advisory defect, and it names a target.

    The tolerance is the one the last measured plan invented ("agree within
    10%") and the question states none, so the plan's own repair is told about
    exactly that target.
    """
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                "Queue totals",
                priority=1,
                evidence_targets=[
                    _target(
                        "Do the two 2024 queue totals agree within 10%?",
                        measure="queue totals agreement",
                    ),
                    _target(
                        "What was the queue total in 2024?",
                        measure="queue total",
                    ),
                ],
            ),
            _draft("Withdrawn capacity", priority=2),
            _draft("Reforms", priority=3),
        ]
    )


@pytest.mark.asyncio
async def test_a_surviving_advisory_target_is_dropped_not_recorded(
    tracker: Tracker,
) -> None:
    """A plan does not ship the target its own repair was told about.

    The live nine-question probe's P3, P4 and P6 plans ended with
    planner_plan_defects_unresolved: an advisory naming a target the plan
    kept anyway, so the shipped plan carried a defect a reader has to read and
    the target it named stayed in the run's obligations. A plan that ignores the
    one correction it was given loses the target instead, and the pass records
    nothing about it — while a *structural* defect keeps today's behaviour
    (test_an_infeasible_target_batch_is_repaired_once_then_refused).
    """
    draft = _tolerance_plan()
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        # The repair returns the plan unchanged: the advisory survives it.
        outputs=[draft, draft, _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state("How much capacity was withheld in 2024?"))

    assert outcome.result is not None
    assert outcome.result.repair_attempted is True
    shipped = [
        (sub_topic.coverage_id, [target.question for target in sub_topic.evidence_targets])
        for sub_topic in outcome.result.sub_topics
    ]
    assert shipped[0] == (
        "topic-01",
        ["What was the queue total in 2024?"],
    )
    assert _plan_defects(outcome.state_update["errors"]) == []
    # The drop is a cleanup, not a defect to repair: no further plan request is
    # made for it, so the pass costs what it cost before the target left.
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]


def _all_optional_but_the_defective_target() -> ResearchPlanDraft:
    """One plan whose only required target is the one the advisory names."""
    return ResearchPlanDraft(
        sub_topics=[
            _draft(
                "Queue totals",
                priority=1,
                evidence_targets=[
                    _target(
                        "Do the two 2024 queue totals agree within 10%?",
                        measure="queue totals agreement",
                    ),
                    _target(
                        "What was the queue total in 2024?",
                        measure="queue total",
                        required=False,
                    ),
                ],
            ),
            _draft(
                "Withdrawn capacity",
                priority=2,
                evidence_targets=[_target(required=False)],
            ),
            _draft(
                "Reforms",
                priority=3,
                evidence_targets=[_target(required=False)],
            ),
        ]
    )


@pytest.mark.asyncio
async def test_a_plan_that_would_owe_nothing_keeps_a_required_target(
    tracker: Tracker,
) -> None:
    """Dropping the defective target must not leave a plan that owes nothing.

    The probe re-run measured the failure mode of a plan with no required
    target: a report could omit every part and still pass. So when the target an
    advisory names was the whole of what the plan owed, the first obligation of
    every sub-topic that survived is required again — the plan still ships
    without the target its repair was told about, and its dimensions are owed.
    """
    draft = _all_optional_but_the_defective_target()
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[draft, draft, _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state("How much capacity was withheld in 2024?"))

    assert outcome.result is not None
    questions = [
        (target.question, target.required)
        for sub_topic in outcome.result.sub_topics
        for target in sub_topic.evidence_targets
    ]
    assert ("Do the two 2024 queue totals agree within 10%?", True) not in questions
    assert all(required for _, required in questions)
    assert _plan_defects(outcome.state_update["errors"]) == []


@pytest.mark.asyncio
async def test_a_truncated_review_repair_falls_back_to_the_reviewed_plan(
    tracker: Tracker,
) -> None:
    """A repair request that cannot be completed keeps the plan it repairs.

    Live run 2 died exactly here: the third structured call hit the output cap,
    ``ProviderOutputLimitError`` is not repairable by design, and the run ended
    with no plan and nothing published. The plan the review judged is still
    structurally researchable, so it is the plan the run continues with — and
    the findings the review made against *that* plan are recorded too, because
    they are what the research about to run will carry unaddressed.
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
            _output_limit_error(),
            _output_limit_error(),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Cryptography",
        "Hardware timelines",
        "Mitigations",
    ]
    assert outcome.result.repair_attempted is True
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
        "ResearchPlanDraft",
    ]
    records = _plan_defects(outcome.state_update["errors"])
    assert [record.details for record in records] == [
        {
            "stage": "review",
            "plan": "draft",
            "problems": [
                "draft: plan review found a missing dimension: siting and "
                "permitting"
            ],
        },
        {
            "stage": "review_repair",
            "plan": "review_repair",
            "problems": [
                "review_repair: the plan review repair raised "
                "ProviderOutputLimitError",
                "review_repair: the planner provider failed while requesting "
                "the final plan draft",
            ],
        },
    ]


@pytest.mark.asyncio
async def test_a_first_plan_review_that_cannot_be_produced_keeps_the_plan(
    tracker: Tracker,
) -> None:
    """The one tool-free planning request that could still end a live run.

    A provider failure, a truncation or a schema failure on the *first* plan
    review raised out of ``finalize``: the run ended at ``graph_planning_failed``
    with nothing published, while the confirming review, the lint repair and the
    review repair were each already guarded. The review is a request like any
    other, so its failure is recorded as an ordinary plan defect and the plan it
    never judged — structurally valid, and researched nothing yet — stands.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _output_limit_error(), _output_limit_error()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Cryptography",
        "Hardware timelines",
        "Mitigations",
    ]
    assert outcome.result.repair_attempted is False
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "PlanReviewDraft",
    ]
    records = _plan_defects(outcome.state_update["errors"])
    assert [record.details for record in records] == [
        {
            "stage": "plan_review",
            "plan": "draft",
            "problems": [
                "draft: the plan review raised ProviderOutputLimitError",
                "draft: the planner provider failed while reviewing the plan",
            ],
        }
    ]


@pytest.mark.asyncio
async def test_an_unusable_review_repair_keeps_the_reviewed_plan(
    tracker: Tracker,
) -> None:
    """A review repair that is not researchable keeps the plan it was repairing.

    The other half of the same fallback: the repair came back, and nothing in
    it can be handed to the researcher. Its own structural defects are recorded
    against the repair, and the review's findings are recorded against the plan
    that stands.
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
            _targetless_plan(),
            _review(sound=False, missing_dimensions=["siting and permitting"]),
        ],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    assert [sub_topic.title for sub_topic in outcome.result.sub_topics] == [
        "Cryptography",
        "Hardware timelines",
        "Mitigations",
    ]
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "ResearchPlanDraft",
    ]
    records = _plan_defects(outcome.state_update["errors"])
    assert [record.details for record in records] == [
        {
            "stage": "review",
            "plan": "draft",
            "problems": [
                "draft: plan review found a missing dimension: siting and "
                "permitting"
            ],
        },
        {
            "stage": "review_repair",
            "plan": "review_repair",
            "problems": [
                "review_repair: topic-03 proposes 0 evidence targets; every "
                "sub-topic carries between 1 and 4"
            ],
        },
    ]


@pytest.mark.asyncio
async def test_a_recorded_plan_defect_reaches_the_warning_block(
    tracker: Tracker,
) -> None:
    """One non-halting record on the operator surface it still owns.

    The planner's record is an ordinary ``ResearchState.errors`` entry, so it
    reaches the CLI's warning block the way every recoverable error does. It is
    deliberately not a halt: the enumerated types that stop a run are
    unchanged, so the report-level gates judge the consequences.

    The evidence ledger and the quality record are not asserted here because
    both renderers are mid-cutover in this wave (Task 4.5 rewrites the quality
    record) and the claim-era reader path fails on a field Task 4.1 renamed —
    a failure owned by that file, not by the planner's record.
    """
    premise = "the query assumes storage already caused the outage"
    question = "What limits grid-scale battery storage deployment?"
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

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(question))

    state = merge_research_state(_state(question), outcome.state_update)
    record = _plan_defects(list(state.errors))[0]
    published = ResearchOutcome(
        session_id="session-1",
        question=question,
        status="completed",
        state=state,
        trace_url=None,
        report_path=None,
        token_usage=TokenUsage(),
        tool_calls=(),
    )

    assert render_warnings(published) == [
        "Warnings: 1 error (0 recovered, 1 non-fatal, 0 fatal)",
        "  agent.planner: 1 error",
        "    planner_plan_defects_unresolved (non-fatal)",
    ]
    # The plan label has to survive onto this surface, whose details are
    # withheld: the message is the only part of the record it publishes.
    assert record.message.endswith("(plan review_repair).")
    assert render_warnings(published, verbose=True)[-1] == (
        "    warning: [planner_plan_defects_unresolved] " + record.message
    )
    assert not is_halted(state)
    assert "planner_plan_defects_unresolved" not in HALTING_ERROR_TYPES


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
    assert "- As of: 2026-09-16" in plan_request
    assert "the period the question names (2021)" in plan_request
    assert "never substitute today's figures" in plan_request
    assert "between 1 and 4 evidence_targets" in plan_request
    # What the session plans from names the question's own year and the frozen
    # contract's date, and nothing else. (The static sections quote "added in
    # 2024" as an example of a bounded window and carry hypothetical reply
    # examples, so only the material that follows them is read here.)
    material = plan_request.split("# Research question\n", 1)[1]
    assert "2024" not in material


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
        required=True,
        measure="original question coverage",
    )
    real = EvidenceTarget(
        target_id="topic-03-target-02",
        coverage_id="topic-03",
        question="What does the third topic report?",
        required=True,
        measure="fact",
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
    # Every newly stamped obligation is planned inside the frozen contract,
    # which the request prints: the plan no longer restates its scope or date
    # on a target (Task 5.2 stamps only the id and ``required``).
    plan_request = completer.calls[0][2][1].content
    assert "- Scope: United States" in plan_request
    assert "- As of: 2026-09-16" in plan_request


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
    """The shipped budgets total one planner lookup and no tool path an agent
    that needs none still carries."""
    settings = load_config(str(Path("config.yaml")))

    assert settings.agents.tool_budget_for("planner") == 1
    assert settings.agents.tool_budget_for("researcher") == 20
    assert settings.agents.tool_budget_for("source_evaluator") == 0
    # The verifier and the writer reason over evidence they are handed, so
    # neither can spend a tool call.
    assert settings.agents.tool_budget_for("evidence_verifier") == 0
    assert settings.agents.tool_budget_for("report_writer") == 0


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
async def test_a_second_non_extension_pass_cannot_replace_the_live_topic_list(
    tracker: Tracker,
) -> None:
    """Carried from Task 2's review: the live topics and the inventory agree.

    ``initial_target_ids`` is union-protected, so a second non-extension pass
    that re-emitted a whole topic list would append a second ``topic-01``
    beside the first while the frozen denominator kept counting both. The
    plan already in the session stands: a re-plan contributes only the topics
    whose ids the session does not have, which here is none of them.
    """
    completer = ScriptedCompleter(
        decisions=[
            finish("No lookup needed.", "Three angles matter."),
            finish("No lookup needed.", "Three angles matter."),
        ],
        outputs=[_sorting_plan(), _review(), _sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    question = "What limits battery storage deployment?"

    async with tracker.session_span("session-1", "q"):
        first = await agent.run(_state(question))
        state = merge_research_state(_state(question), first.state_update)
        replanned = await agent.run(state)

    update = replanned.state_update

    # Nothing crosses the boundary: no topic replaces the frozen plan, and the
    # coverage denominator does not move.
    assert "sub_topics" not in update
    assert "initial_target_ids" not in update
    assert "expanded_target_ids" not in update
    merged = merge_research_state(state, update)
    assert [topic.coverage_id for topic in merged.sub_topics] == [
        "topic-01",
        "topic-02",
        "topic-03",
    ]
    assert merged.initial_target_ids == state.initial_target_ids


# --------------------------------------------------------------------------
# T2: the plan is answerable, in scope, and priced for the evidence
# --------------------------------------------------------------------------

# The live audit question, verbatim: the run that produced zero answered
# targets planned against this one.
_AUDIT_QUESTION = (
    "How much grid-scale battery storage capacity was added in the United "
    "States in 2024, and what do the latest forecasts project for 2025?"
)


def _stamped_plan(
    question: str, target: EvidenceTargetDraft
) -> list[SubTopic]:
    """One contract, one validated plan, stamped — the way planning stamps it.

    The plan carries three sub-topics because that is the contract's own bound
    and ``validate_plan_draft`` enforces it; the target under test is the first
    topic's, and the two fillers are ordinary obligations.
    """
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Topic 1", priority=1, evidence_targets=[target]),
            _draft("Topic 2", priority=2),
            _draft("Topic 3", priority=3),
        ]
    )
    sub_topics, problems = validate_plan_draft(draft)
    assert problems == []
    return apply_answer_contract(sub_topics, _contract(question))


def _stamped(question: str, target: EvidenceTargetDraft) -> EvidenceTarget:
    """The first topic's stamped obligation, from that same plan."""
    (first,) = _stamped_plan(question, target)[0].evidence_targets
    return first


# The target the run's plan gave the forecast's publication date. Its own
# measure is a metadata obligation the question never asks for, and
# ``dimension_is_answered`` refuses metadata the question did not ask about —
# so no claim could ever be bound to it (replay C10).
_FORECAST_DATE_TARGET = _target(
    "When was the latest forecast document published?",
    measure="publication date of the forecast document",
    period="latest vintage available on or before 2025-12-31",
)


def _one_topic_plan(
    question: str,
    *,
    title: str,
    criteria: list[str],
    target: EvidenceTargetDraft,
) -> list[SubTopic]:
    """A stamped three-topic plan whose first topic is the one under test."""
    sub_topics, problems = validate_plan_draft(
        ResearchPlanDraft(
            sub_topics=[
                _draft(
                    title,
                    priority=1,
                    success_criteria=criteria,
                    evidence_targets=[target],
                ),
                _draft("Topic 2", priority=2),
                _draft("Topic 3", priority=3),
            ]
        )
    )
    assert problems == []
    return apply_answer_contract(sub_topics, _contract(question))


_MEASURED_TARGET = _target(
    "How much capacity was added in 2024?",
    measure="battery storage capacity added, in MW",
)


def test_a_subtopic_the_question_never_asked_for_is_named() -> None:
    """The instruction used to *mandate* a benefits-and-risks sub-topic.

    That is the widening the plan review rules against, so the check names it
    instead of the planner asking for it.
    """
    stamped = _one_topic_plan(
        _AUDIT_QUESTION,
        title="Costs, benefits and risks",
        criteria=["Benefits and risks are quantified for both options."],
        target=_MEASURED_TARGET,
    )

    named = [
        problem
        for problem in target_problems(stamped, _contract(_AUDIT_QUESTION))
        if "never asks about" in problem
    ]

    assert [problem.split()[0] for problem in named] == ["topic-01"]


# The live P4 probe's EU AI Act plan: the statute's own term for the models it
# regulates is "systemic risk", so a sub-topic the question itself calls for
# carried that word, the widening lint fired, and a max-effort repair call was
# bought for a plan the model then kept — with the documented D14 risk that a
# repair deletes the very dimension the question asked for.
_AI_ACT_QUESTION = (
    "What obligations does the EU AI Act impose on providers of "
    "general-purpose AI models?"
)


def test_the_questions_own_domain_vocabulary_is_not_a_widening_demand() -> None:
    """A dimension is a pairing, not one word: a single marker is the domain's.

    "systemic risk" is what the question's own subject matter is called; the
    lint exists for the sub-topic an earlier instruction *mandated* (benefits
    and risks together), so one marker is left to the plan review, which judges
    scope widening with meaning.
    """
    stamped = _one_topic_plan(
        _AI_ACT_QUESTION,
        title="obligations for models posing systemic risk",
        criteria=[
            "The obligations that apply to models posing systemic risk are "
            "stated."
        ],
        target=_MEASURED_TARGET,
    )

    assert [
        problem
        for problem in target_problems(stamped, _contract(_AI_ACT_QUESTION))
        if "never asks about" in problem
    ] == []


def test_a_subtopic_the_question_does_ask_for_is_not_named() -> None:
    """The control: a value-judgement question is asking about benefits."""
    question = "Are grid-scale batteries good for the grid?"
    stamped = _one_topic_plan(
        question,
        title="Costs, benefits and risks",
        criteria=["Benefits and risks are quantified for both options."],
        target=_MEASURED_TARGET,
    )

    assert [
        problem
        for problem in target_problems(stamped, _contract(question))
        if "never asks about" in problem
    ] == []


def test_a_compound_target_question_is_named() -> None:
    """The example's own defect, as a check: two demands in one target."""
    question = "compare bus and rail options for a city"
    stamped = _one_topic_plan(
        question,
        title="Outcomes",
        criteria=["A figure is quoted."],
        target=_target(
            "Which documented risks does each option carry, and by which "
            "issuer?",
            measure="documented risk",
        ),
    )

    named = [
        problem
        for problem in target_problems(stamped, _contract(question))
        if "questions at once" in problem
    ]

    assert [problem.split()[0] for problem in named] == ["topic-01-target-01"]


def test_an_atomic_target_question_is_not_named() -> None:
    """The control: one demand is not compound, however it is worded."""
    question = "compare bus and rail options for a city"
    stamped = _one_topic_plan(
        question,
        title="Outcomes",
        criteria=["A figure is quoted."],
        target=_target(
            "Which documented risks does each option carry?",
            measure="documented risk",
        ),
    )

    assert [
        problem
        for problem in target_problems(stamped, _contract(question))
        if "questions at once" in problem
    ] == []


def test_an_energy_measure_the_capacity_question_never_named_is_optional() -> (
    None
):
    """MWh for a "how much capacity" question is planned optional.

    ``_AUDIT_QUESTION`` asks "how much ... capacity was added"; a target that
    asks for MWh answers a measure the question never named, and no claim
    about capacity can discharge it. Demanding it as required would leave a
    required, unanswerable obligation on the frozen inventory, so it is
    planned optional here, with an advisory naming why (change 6).
    """
    stamped = _one_topic_plan(
        _AUDIT_QUESTION,
        title="EIA additions",
        criteria=["The energy added in 2024 is stated with its issuer."],
        target=_target(
            "How much energy did EIA report added to battery storage in "
            "2024, in megawatt-hours?",
            required=False,
            measure="annual battery storage energy additions, in MWh",
            period="calendar year 2024",
        ),
    )

    (target,) = stamped[0].evidence_targets
    assert target.required is False

    named = [
        problem
        for problem in target_problems(stamped, _contract(_AUDIT_QUESTION))
        if "energy figure" in problem
    ]
    assert [problem.split()[0] for problem in named] == ["topic-01-target-01"]


def test_a_capacity_measure_the_question_asked_for_is_not_named() -> None:
    """The control: a target asking for the same power unit the question does."""
    stamped = _one_topic_plan(
        _AUDIT_QUESTION,
        title="EIA additions",
        criteria=["The capacity added in 2024 is stated with its issuer."],
        target=_target(
            "How much capacity did EIA report added to battery storage in "
            "2024, in megawatts?",
            measure="annual battery storage capacity additions, in MW",
            period="calendar year 2024",
        ),
    )

    (target,) = stamped[0].evidence_targets
    assert target.required is True
    assert [
        problem
        for problem in target_problems(stamped, _contract(_AUDIT_QUESTION))
        if "energy figure" in problem
    ] == []


def test_an_energy_measure_an_energy_question_names_is_not_downgraded() -> (
    None
):
    """The control: a question that itself asks about energy keeps the target."""
    question = (
        "How much energy did battery storage systems add to the grid in "
        "2024, and what do the latest forecasts project for 2025?"
    )
    stamped = _one_topic_plan(
        question,
        title="EIA additions",
        criteria=["The energy added in 2024 is stated with its issuer."],
        target=_target(
            "How much energy did EIA report added to battery storage in "
            "2024, in megawatt-hours?",
            measure="annual battery storage energy additions, in MWh",
            period="calendar year 2024",
        ),
    )

    (target,) = stamped[0].evidence_targets
    assert target.required is True


@pytest.mark.parametrize(
    "energy_word",
    ["energy", "duration", "hours"],
)
def test_a_question_naming_energy_duration_or_hours_in_words_is_not_downgraded(
    energy_word: str,
) -> None:
    """P1-c: a question may name the energy family in words, not only units.

    "How much ... capacity was added" still reads as a power question, but a
    question that also asks about the storage's energy, duration, or hours of
    discharge really did ask for an energy figure in words, even with no
    literal "MWh" token in its own prose — the same way ``_ENERGY_UNIT``
    exempts a question that spells the unit out.
    """
    question = (
        "How much battery storage capacity and "
        f"{energy_word} was added in the United States in 2024, and what "
        "do the latest forecasts project for 2025?"
    )
    stamped = _one_topic_plan(
        question,
        title="EIA additions",
        criteria=["The energy added in 2024 is stated with its issuer."],
        target=_target(
            "How much energy did EIA report added to battery storage in "
            "2024, in megawatt-hours?",
            measure="annual battery storage energy additions, in MWh",
            period="calendar year 2024",
        ),
    )

    (target,) = stamped[0].evidence_targets
    assert target.required is True
    assert [
        problem
        for problem in target_problems(stamped, _contract(question))
        if "energy figure" in problem
    ] == []


@pytest.mark.parametrize(("label", "payload"), _PLAN_REPLY_EXAMPLES)
def test_every_plan_example_the_model_is_shown_passes_every_plan_check(
    label: str, payload: str
) -> None:
    """Each example is a plan the planner would accept (D10: two shapes, neither the benchmark's)."""
    question = label.split("Example input:", 1)[1].strip().rstrip(".")
    sub_topics, problems = validate_plan_draft(ResearchPlanDraft.model_validate_json(payload))
    assert problems == []
    assert target_problems(sub_topics, _contract(question)) == []


# The live ``topic-02-target-01`` question from the 1-iteration run whose report
# failed all five criteria. Its figure is one agency's own outlook number, and
# the two bodies the plan named publish differently scoped ones (review rank 2).
_LIVE_FORECAST_TARGET_QUESTION = (
    "What 2025 addition of utility-scale battery storage capacity in the "
    "United States, in megawatts, does the federal energy statistical "
    "agency's most recently published outlook project?"
)


def _structured_draft(**overrides: object) -> SubTopicDraft:
    fields: dict[str, object] = dict(
        question="How much battery storage capacity did EIA report added in the U.S. in 2024?",
        required=True,
        measure="battery storage power capacity added",
        unit_dimension="power",
        period="2024",
        kind="actual",
        geography="United States",
        organisation="U.S. Energy Information Administration",
    )
    fields.update(overrides)
    return SubTopicDraft(
        title="EIA 2024 additions", rationale="r", search_queries=["q"],
        success_criteria=["c"], priority=1,
        evidence_targets=[EvidenceTargetDraft(**fields)],
    )


def test_draft_targets_carry_the_structured_fields() -> None:
    [target] = _draft_targets(_structured_draft(), "topic-01")
    assert (target.measure, target.unit_dimension, target.period, target.kind) == (
        "battery storage power capacity added", "power", "2024", "actual"
    )
    assert target.organisation == "U.S. Energy Information Administration"


def test_an_unknown_kind_or_a_blank_organisation_is_stamped_empty() -> None:
    [target] = _draft_targets(
        _structured_draft(unit_dimension="volts", kind="estimate", organisation=" "),
        "topic-01",
    )
    assert (target.unit_dimension, target.kind, target.organisation) == ("volts", None, None)


def test_a_mixed_case_dimension_or_kind_is_folded_to_the_canonical_spelling() -> None:
    [target] = _draft_targets(
        _structured_draft(unit_dimension="Power", kind="Actual"), "topic-01"
    )
    assert (target.unit_dimension, target.kind) == ("power", "actual")


def test_the_answer_contract_keeps_the_structured_fields() -> None:
    targets = _draft_targets(_structured_draft(), "topic-01")
    topic = SubTopic(
        coverage_id="topic-01", title="EIA 2024 additions", rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
        evidence_targets=targets,
    )
    contract = AnswerContract(
        question="How much battery storage was added in the U.S. in 2024?",
        scope_statement="United States, as of 2026-09-24, a factual answer.",
        geographic_scope="United States", as_of_date="2026-09-24",
        evidence_period_requirement="the period the question names (2024)",
        assumptions=[], answer_kind="factual",
    )
    [stamped] = apply_answer_contract([topic], contract)
    [target] = stamped.evidence_targets
    assert (
        target.measure,
        target.unit_dimension,
        target.period,
        target.kind,
        target.geography,
        target.organisation,
    ) == (
        "battery storage power capacity added",
        "power",
        "2024",
        "actual",
        "United States",
        "U.S. Energy Information Administration",
    )


def test_a_draft_target_without_a_measure_is_a_plan_problem() -> None:
    draft = ResearchPlanDraft(
        sub_topics=[
            _draft("Topic 1", priority=1, evidence_targets=[_target(measure="")]),
            _draft("Topic 2", priority=2),
            _draft("Topic 3", priority=3),
        ]
    )
    _, problems = validate_plan_draft(draft)
    assert any("check these fields: measure" in problem for problem in problems)


# The live nine-question planning probe's P5 and P8 plans marked planner-added
# aims as required. The fix for that is the instruction's own sentence, not a
# code test: the probe *re-run* measured what a word test costs — a
# majority-of-distinctive-words rule removed ``required`` from all eleven
# targets of the headphones question, whose measures paraphrase it, so the plan
# owed nothing. The rule this pair pins is therefore the model's flag being
# stamped as the draft gives it, whichever wording its measure uses.
_BATTERY_QUESTION = (
    "How much grid-scale battery storage capacity was added in the United "
    "States in 2024, and what do the latest forecasts project for 2025?"
)
_HEADPHONES_QUESTION = (
    "which is the best headphones to buy 2026 for best audio quality and best mic"
)


@pytest.mark.parametrize(
    "measure",
    (
        "sound quality rating (highest-ranked model)",
        "microphone recording quality rating",
        "number of weeks in the fiscal year",
        "TIOBE index rating increase",
    ),
)
def test_a_required_flag_the_draft_set_is_stamped_however_the_measure_is_worded(
    measure: str,
) -> None:
    """A paraphrase is still the question's part, and an aid is the model's call.

    Both directions of the probe re-run's regression: a legitimate part whose
    measure only paraphrases the question keeps ``required``, and an aid the
    model marked required keeps it too — the sentence in the plan instruction is
    what makes an added aid optional, and a word test cannot tell the two apart.
    """
    assert _stamped(_HEADPHONES_QUESTION, _target(measure=measure)).required is True


def test_an_optional_target_the_question_names_is_not_promoted() -> None:
    """Required is the model's to grant: the rule only ever removes it."""
    assert (
        _stamped(
            _BATTERY_QUESTION,
            _target(
                "What did grid-scale battery storage add in 2024?",
                required=False,
                measure="grid-scale battery storage capacity added",
            ),
        ).required
        is False
    )


def test_the_answer_contract_adds_no_boilerplate_to_targets() -> None:
    targets = _draft_targets(_structured_draft(), "topic-01")
    topic = SubTopic(
        coverage_id="topic-01", title="EIA 2024 additions", rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
        evidence_targets=targets,
    )
    # The contract's question names the measure, so the rule under test here
    # (nothing is added to a target's fields) is the only thing that acts.
    [stamped] = apply_answer_contract(
        [topic], _contract("How much battery storage power capacity was added?")
    )
    assert stamped.evidence_targets[0].model_dump() == targets[0].model_dump()


def test_the_plan_instruction_states_the_floor() -> None:
    for phrase in ("one target per organisation, measure, period and kind",
                   "A target is required only when the question itself asks "
                   "for that thing",
                   "optional", "behind a paywall",
                   "a target you add to make another target checkable",
                   "a derived total"):
        assert phrase in PLAN_INSTRUCTION


def test_the_plan_instruction_orders_optional_sub_topics_after_required_ones() -> None:
    """D17: a sub-topic beyond the question's own parts must never outrank a
    sub-topic that answers a part, so research always reaches the required
    parts before anything the plan added.
    """
    for phrase in (
        "A sub-topic whose targets are all optional answers no part of the "
        "question",
        "give it a lower priority than every sub-topic that carries a "
        "required target",
        "research reaches the question's own parts before anything the plan "
        "added",
    ):
        assert phrase in PLAN_INSTRUCTION


# The live P3/P4 plan probe stamped bodies the question never named by
# *describing* them where a body's name belongs — "the manufacturer of
# semaglutide" for the maker of a drug, and a join of two institutions for the
# bodies that enacted a rule. §6.6 binds a target's answer to a finding through
# the strict ``same_organisation`` match, and neither phrase is any page's own
# organisation label, so the target was reported Not found for the whole run
# while the report held its answer, and an extra pass was bought for it.
_QUESTION_NAMING_NO_BODY = (
    "How much did semaglutide sales grow in 2024, and what is projected for 2025?"
)

# A body the plan may only have *described*: lower-case prose naming a role,
# with or without a clause. The re-review's probe found the shapes a role-word
# list missed — every one of them was still being stamped, so the target was
# still pre-failed for the whole run.
_DESCRIBED_BODIES = (
    "the manufacturer of semaglutide",
    "the drug's maker",
    "the manufacturer",
    "the body that publishes the primary record",
    "the company responsible for the trial",
    "the regional health authority",
    "the national public health agency",
    "the national statistical agency",
    "the regulator",
    "the market monitor",
)


@pytest.mark.parametrize("organisation", _DESCRIBED_BODIES)
def test_a_body_the_plan_can_only_describe_is_left_empty(organisation: str) -> None:
    assert (
        _stamped(_QUESTION_NAMING_NO_BODY, _target(organisation=organisation)).organisation
        is None
    )


@pytest.mark.parametrize(
    "organisation",
    (
        "U.S. Energy Information Administration",
        "EIA",
        "the European Chemicals Agency",
        "Novo Nordisk",
        # The names a role-word list emptied, which loosened a quantity
        # target's provenance rather than tightening it: `_figure_answers`
        # drops the organisation conjunct when a target carries none.
        "Centers for Disease Control and Prevention",
        "National Institute of Standards and Technology",
        "Department of Health and Human Services",
        "Department for Energy Security and Net Zero",
        "WHO",
        "WHO Europe",
        # Two bodies joined by a capitalised conjunction are a name-shaped
        # value, and the join alone therefore stays stamped: "Centers for
        # Disease Control and Prevention" carries that conjunction inside one
        # body's name, so refusing the shape emptied real names. Whether a join
        # names one body or two is the plan review's judgement.
        "European Parliament and Council of the European Union",
    ),
)
def test_a_body_the_plan_names_is_stamped_by_name(organisation: str) -> None:
    """The counterpart the guard must not break: a name is not a description.

    The question names no body in either case, so every one of these is the
    plan's own inference of the body that publishes the primary record — which
    the plan instruction asks for and §6.6 is then able to match against a
    page's own label. A guard that emptied these would refuse that inference
    and loosen every figure target's provenance: an emptied organisation drops
    that conjunct from ``verified_facts._figure_answers``, so another body's
    figure could answer the target.
    """
    assert (
        _stamped(_QUESTION_NAMING_NO_BODY, _target(organisation=organisation)).organisation
        == organisation
    )


def test_the_models_required_flag_is_what_the_plan_stamps() -> None:
    [optional] = _draft_targets(_structured_draft(required=False), "topic-01")
    [required] = _draft_targets(_structured_draft(), "topic-01")
    assert (optional.required, required.required) == (False, True)


def test_an_open_dimension_word_survives_stamping() -> None:
    [target] = _draft_targets(_structured_draft(unit_dimension=" Currency "), "topic-01")
    assert target.unit_dimension == "currency"


def test_the_plan_request_puts_its_requirements_before_the_question() -> None:
    body = plan_messages(AgentTask(instruction="Why did ferry fares rise?"), _run())[1].content
    assert body.startswith("# Plan requirements\n")
    assert body.index("# Reply format") < body.index("# Research question")
    assert body.rstrip().endswith(STRUCTURED_REQUEST_END)


def test_a_reasons_question_drops_a_quantity_target_of_its_own() -> None:
    """A the-planner-added "how many" on a why-question is optional and formless.

    The final probe's P6 plan added "the number of senators reported as
    proscribed under Sulla" to a why-question. The question asks for no
    quantity, so the target carries no figure (rule 4's first half) and no
    obligation: a figure the evidence holds belongs inside the reason's finding.
    """
    stamped = _stamped(
        "Why did the Roman Republic fall?",
        _target(
            "How many senators were proscribed under Sulla?",
            measure="number of senators proscribed under Sulla",
            unit_dimension="count",
            kind="actual",
        ),
    )

    assert (stamped.unit_dimension, stamped.kind, stamped.required) == (
        None,
        None,
        False,
    )


def test_a_magnitude_word_inside_a_measure_is_not_a_quantity_ask() -> None:
    """The measure's own wording alone never demotes: only an ask does.

    This is the shape the probe re-run caught — a word list reading "totals"
    inside a sub-topic's own words removed ``required`` from a legitimate target.
    """
    stamped = _stamped(
        "What are the current interconnection constraints?",
        _target(
            "What does the queue report say?",
            measure="queue total reported",
        ),
    )

    assert stamped.required is True


def test_a_figure_target_carries_the_window_the_question_bounds() -> None:
    """The P7 defect: the window the question states, on every figure target.

    Two of that plan's three sea-level targets carried "since 1993" and the third
    was left empty, which the reviewer then had to repair.
    """
    stamped = _stamped(
        "How much has global mean sea level risen since 1993, and what are the "
        "main contributors?",
        _target(
            "How much has sea level risen?",
            measure="sea level rise",
            unit_dimension="distance",
            kind="actual",
        ),
    )

    assert stamped.period == "1993"


def test_a_year_the_question_does_not_bound_is_not_a_period() -> None:
    """The counterpart: a year that names when the reader buys stays no period."""
    stamped = _stamped(
        "which is the best headphones to buy 2026 for best audio quality and best mic",
        _target(
            "What is the highest-rated model's audio score?",
            measure="sound quality rating",
            unit_dimension="rating",
            kind="actual",
        ),
    )

    assert stamped.period is None


def test_the_instruction_says_which_targets_are_optional_and_which_body_is_the_authority() -> None:
    """The final probe's grade, pinned where code can pin it.

    The three sentences are instruction text: the required flag and the body a
    measure's authority is are the model's call, and the controller's ruling
    removed code judgement of a target's wording. What code can pin is that each
    sentence is in the instruction *and* in the request the model reads.
    """
    for phrase in (
        "A target is required only when the question itself asks for that thing",
        "a sub-category, aspect, example, event, item or attribute the "
        "question does not name",
        "a description of a role, or an author no page credits",
        "stamp an adopting or enacting body",
        "every figure target for that window",
    ):
        assert phrase in PLAN_INSTRUCTION
    body = plan_messages(
        AgentTask(instruction="Why did the Roman Republic fall?"), _run()
    )[1].content
    for phrase in (
    ):
        assert phrase in body


# Run 2's plan call ended at 59,064 of 65,536 output tokens (90% of its cap) and
# smoke 2 at 77%: a plan-side request that reasons past the cap raises
# ``ProviderOutputLimitError``, which is not retryable, so a truncated first
# draft ended the run before any research. The reviewer and the writer already
# re-ask a truncated request once at the shared retry effort under the same
# budget; the planner's structured calls now do the same.
@pytest.mark.asyncio
async def test_a_truncated_plan_draft_is_re_asked_once_at_the_retry_effort(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_output_limit_error(), _sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "ResearchPlanDraft",
        "PlanReviewDraft",
    ]
    # The same request, the same output budget, the lower effort.
    assert completer.calls[0][2] == completer.calls[1][2]
    assert completer.budgets[0] == completer.budgets[1] is not None
    assert completer.efforts[:2] == [None, "high"]
    retries = [
        error
        for error in outcome.state_update["errors"]
        if error.error_type == "planner_output_limit_retry"
    ]
    assert [error.recoverable for error in retries] == [True]
    assert "reasoning_effort high" in retries[0].message
    assert "the retry returned a reply" in retries[0].message


@pytest.mark.asyncio
async def test_a_plan_draft_truncated_twice_fails_as_it_did_before(
    tracker: Tracker,
) -> None:
    """One retry, never a loop: a second truncation keeps today's failure."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_output_limit_error(), _output_limit_error()],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError, match="model provider"):
        async with tracker.session_span("session-1", "q"):
            await agent.run(
                _state("What limits grid-scale battery storage deployment?")
            )

    assert completer.efforts == [None, "high"]


@pytest.mark.asyncio
async def test_a_truncated_plan_review_is_re_asked_once_at_the_retry_effort(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _output_limit_error(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state("What limits grid-scale battery storage deployment?")
        )

    assert outcome.result is not None
    assert [call[0] for call in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "PlanReviewDraft",
    ]
    assert completer.efforts == [None, None, "high"]
    assert completer.budgets[1] == completer.budgets[2]
    assert _plan_defects(outcome.state_update["errors"]) == []


@pytest.mark.asyncio
async def test_a_retry_that_fails_validation_keeps_its_diagnostics_as_a_redacted_copy(
    tracker: Tracker,
) -> None:
    """A non-truncation failure of the retry is redacted like a second truncation.

    The reviewer's rule (``_re_ask_truncated``): the retry's failure keeps its
    type and its provider-free diagnostics, so ``structured_output_problems``
    still names what failed validation, but the caller receives a fresh copy
    with the provider exception chain cut, not the object the provider raised.
    """
    schema_failure = StructuredOutputError(
        "the draft failed validation",
        diagnostics=[{"attempt": 2, "field_paths": ["sub_topics"]}],
    )
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_output_limit_error(), schema_failure],
    )
    agent = _planner(tracker, completer)

    with pytest.raises(PlanningError) as caught:
        async with tracker.session_span("session-1", "q"):
            await agent.run(_state())

    cause = caught.value.__cause__
    assert isinstance(cause, StructuredOutputError)
    assert cause is not schema_failure
    assert cause.__cause__ is None and cause.__suppress_context__
    assert cause.diagnostics == schema_failure.diagnostics
    assert any(
        "failed schema validation on attempt 2 at sub_topics" in problem
        for problem in caught.value.problems
    )
    assert completer.efforts == [None, "high"]
