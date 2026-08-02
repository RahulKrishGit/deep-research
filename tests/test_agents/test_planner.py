"""Tests for the Planner's plan contracts, validation, and prompts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.planner import (
    MAX_SUB_TOPICS,
    MIN_SUB_TOPICS,
    ResearchPlan,
    ResearchPlanDraft,
    SubTopicDraft,
    format_plan_problems,
    plan_messages,
    validate_plan_draft,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep


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
