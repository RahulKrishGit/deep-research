"""Contracts for the evaluation harness."""

from __future__ import annotations

import json

import pytest

from deep_research.evaluation.models import (
    AGENT_NAMES,
    ARTIFACT_SCHEMA_VERSION,
    CLI_AGENT_NAMES,
    CaseExpectations,
    DeterministicMetric,
    EvaluationCase,
    ExperimentResult,
    JudgeFeedback,
    JudgeRubric,
    JudgeScores,
    RubricDimension,
    SuiteResult,
    TargetOutput,
    UnknownAgentError,
    UnknownTierError,
    cli_agent_name,
    parse_agent_name,
    parse_tier,
)
from deep_research.utils.types import ResearchState, SubTopic


def test_the_six_agent_names_are_fixed_and_ordered() -> None:
    assert AGENT_NAMES == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    )
    assert CLI_AGENT_NAMES == (
        "planner",
        "researcher",
        "source-evaluator",
        "fact-checker",
        "synthesizer",
        "critic",
    )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("planner", "planner"),
        ("source-evaluator", "source_evaluator"),
        ("source_evaluator", "source_evaluator"),
        ("  Fact-Checker  ", "fact_checker"),
    ],
)
def test_parse_agent_name_canonicalizes(given: str, expected: str) -> None:
    assert parse_agent_name(given) == expected


def test_parse_agent_name_lists_the_valid_values_when_unknown() -> None:
    with pytest.raises(UnknownAgentError) as caught:
        parse_agent_name("librarian")

    message = str(caught.value)
    assert "librarian" in message
    for name in CLI_AGENT_NAMES:
        assert name in message


def test_cli_agent_name_round_trips() -> None:
    for name in AGENT_NAMES:
        assert parse_agent_name(cli_agent_name(name)) == name


def test_parse_tier_rejects_an_unknown_tier() -> None:
    assert parse_tier("live") == "live"
    with pytest.raises(UnknownTierError) as caught:
        parse_tier("staging")
    assert "controlled" in str(caught.value)


def build_expectations() -> CaseExpectations:
    return CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={"minimum_sub_topics": 3},
        known_source_urls=["https://example.org/a"],
        max_iterations=5,
        max_tool_calls=10,
        deterministic_metrics=[
            DeterministicMetric(
                metric_id="coverage",
                weight=0.6,
                description="Every planned subtopic is distinct.",
            ),
            DeterministicMetric(
                metric_id="ordering",
                weight=0.4,
                description="Priorities are strictly increasing.",
            ),
        ],
    )


def test_deterministic_metric_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError) as caught:
        CaseExpectations(
            required_output_fields=["sub_topics"],
            reference={},
            known_source_urls=[],
            max_iterations=5,
            max_tool_calls=10,
            deterministic_metrics=[
                DeterministicMetric(
                    metric_id="coverage", weight=0.5, description="d"
                )
            ],
        )

    assert "sum to 1.0" in str(caught.value)


def test_deterministic_metric_ids_must_be_unique() -> None:
    with pytest.raises(ValueError):
        CaseExpectations(
            required_output_fields=["sub_topics"],
            reference={},
            known_source_urls=[],
            max_iterations=5,
            max_tool_calls=10,
            deterministic_metrics=[
                DeterministicMetric(
                    metric_id="coverage", weight=0.5, description="d"
                ),
                DeterministicMetric(
                    metric_id="coverage", weight=0.5, description="d"
                ),
            ],
        )


def build_case(**overrides) -> EvaluationCase:
    payload = dict(
        case_id="focused-decomposition",
        version=1,
        agent_name="planner",
        tier="controlled",
        title="Decompose a focused question",
        purpose="Check coverage, non-overlap, ordering, and search framing.",
        state=ResearchState(
            session_id="evaluation-planner-focused-decomposition",
            original_question="How do solid-state batteries fail?",
        ),
        dependency_scenario="planner-deterministic-memory",
        expectations=build_expectations(),
        judge_rubric=JudgeRubric(
            rubric_id="planner-decomposition",
            version=1,
            agent_dimensions=[
                RubricDimension(
                    dimension_id="decomposition_quality",
                    description="Subtopics partition the question.",
                    anchors={
                        "1.0": "Distinct, ordered, exhaustive subtopics.",
                        "0.0": "Overlapping or missing subtopics.",
                    },
                )
            ],
        ),
        metadata={"suite": "baseline"},
    )
    payload.update(overrides)
    return EvaluationCase(**payload)


def test_case_ids_must_be_kebab_case() -> None:
    with pytest.raises(ValueError) as caught:
        build_case(case_id="Focused_Decomposition")

    assert "kebab-case" in str(caught.value)


def test_a_case_round_trips_through_json() -> None:
    case = build_case()

    assert EvaluationCase.model_validate_json(case.model_dump_json()) == case


def test_case_state_is_deep_copied_per_repetition() -> None:
    case = build_case()
    first = case.fresh_state()
    second = case.fresh_state()

    first.sub_topics.append(
        SubTopic(
            title="t",
            rationale="r",
            search_queries=["q"],
            success_criteria=["c"],
            priority=1,
        )
    )

    assert second.sub_topics == []
    assert case.state.sub_topics == []
    assert first is not case.state
    assert second is not first


def test_judge_scores_reject_values_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError):
        JudgeScores(
            role_adherence=1.4,
            completeness=0.5,
            groundedness=0.5,
            reasoning_quality=0.5,
            usefulness=0.5,
            uncertainty_calibration=0.5,
        )


def test_judge_not_run_may_never_carry_a_quality_score() -> None:
    with pytest.raises(ValueError) as caught:
        JudgeFeedback(
            status="judge_not_run",
            not_run_reason="no_evaluable_output",
            judge_quality=0.0,
            prompt_id="individual-agent-judge",
            rubric_version=1,
            prompt_fingerprint="abc123abc123",
            judge_model="gpt-5.6-luna",
            judge_configuration_fingerprint="def456def456",
        )

    assert "fabricated" in str(caught.value)


def test_judge_not_run_requires_a_typed_reason() -> None:
    with pytest.raises(ValueError):
        JudgeFeedback(
            status="judge_not_run",
            prompt_id="individual-agent-judge",
            rubric_version=1,
            prompt_fingerprint="abc123abc123",
            judge_model="gpt-5.6-luna",
            judge_configuration_fingerprint="def456def456",
        )


def test_an_experiment_result_round_trips_through_json(
    experiment_result,
) -> None:
    payload = json.loads(experiment_result.model_dump_json())

    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert ExperimentResult.model_validate(payload) == experiment_result


# --- Supplemental contract tests (task self-review requirements) ---


def test_models_reject_unknown_fields() -> None:
    """Every contract model is extra="forbid", so artifacts stay strict."""
    with pytest.raises(ValueError):
        build_case(unexpected_field="nope")
    with pytest.raises(ValueError):
        TargetOutput(
            case_id="focused-decomposition",
            case_version=1,
            agent_name="planner",
            tier="controlled",
            repetition=1,
            session_id="evaluation-planner-focused-decomposition",
            experiment_name="planner-controlled-20260816T101500Z-abc1234",
            completed=True,
            target_model_requested="gpt-5.6-luna",
            target_reasoning_effort="high",
            unexpected_field="nope",
        )


def test_target_output_round_trips_through_json() -> None:
    output = TargetOutput(
        case_id="focused-decomposition",
        case_version=1,
        agent_name="planner",
        tier="controlled",
        repetition=1,
        session_id="evaluation-planner-focused-decomposition",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/1",
        completed=True,
        result={"sub_topics": []},
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="high",
    )

    payload = json.loads(output.model_dump_json())
    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert TargetOutput.model_validate(payload) == output
    assert TargetOutput.model_validate_json(output.model_dump_json()) == output


def test_suite_result_round_trips_through_json(experiment_result) -> None:
    suite = SuiteResult(
        suite_id="individual-agent-baseline",
        experiments=[experiment_result],
        status="REVIEW REQUIRED",
        metadata={"suite": "baseline"},
    )

    payload = json.loads(suite.model_dump_json())
    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert SuiteResult.model_validate(payload) == suite
