"""Contracts for the evaluation harness."""

from __future__ import annotations

import json
from hashlib import sha256

import pytest

from deep_research.evaluation.models import (
    AGENT_NAMES,
    ARTIFACT_SCHEMA_VERSION,
    CLI_AGENT_NAMES,
    CaseExpectations,
    DependencyLedger,
    DeterministicMetric,
    EvaluationCase,
    EvaluationFailure,
    ExperimentResult,
    FallbackProviderDiagnostic,
    GateReport,
    JudgeFeedback,
    JudgeRubric,
    JudgeScores,
    ReActSummary,
    RepetitionResult,
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


def test_evaluation_failure_details_are_typed_and_allow_listed() -> None:
    import deep_research.evaluation.models as models_module

    details_type = getattr(models_module, "OutputLimitFailureDetails", None)
    assert details_type is not None
    output_limit = details_type(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage={"input_tokens": 8, "output_tokens": 4096, "total_tokens": 4104},
        request_attempt=1,
        structured_attempt=2,
    )
    failure = EvaluationFailure(
        stage="provider",
        reason="output_limit",
        message="The provider response reached its configured output limit.",
        exception_type="ProviderOutputLimitError",
        details=output_limit,
    )

    assert failure.details == output_limit
    assert failure.model_dump(mode="json")["details"] == {
        "kind": "output_limit",
        "finish_reason_category": "length",
        "configured_max_tokens": 4096,
        "usage": {
            "input_tokens": 8,
            "output_tokens": 4096,
            "total_tokens": 4104,
        },
        "request_attempt": 1,
        "structured_attempt": 2,
    }
    with pytest.raises(ValueError):
        details_type(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage={"input_tokens": 8, "output_tokens": 4096},
            request_attempt=1,
            raw_provider_output="secret",
        )


def test_schema_and_provider_failure_details_retain_only_safe_fields() -> None:
    import deep_research.evaluation.models as models_module

    diagnostic_type = getattr(models_module, "EvaluationDiagnostic", None)
    schema_type = getattr(models_module, "SchemaFailureDetails", None)
    transport_type = getattr(models_module, "ProviderFailureDetails", None)
    assert diagnostic_type is not None
    assert schema_type is not None
    assert transport_type is not None
    schema = schema_type(
        diagnostics=(
            diagnostic_type(
                kind="schema_output",
                attempt=1,
                field_paths=("sub_topics.0.title",),
            ),
        )
    )
    transport = transport_type(
        kind="provider_transport",
        type="ProviderResponseError",
        retryable=True,
        status_code=None,
    )

    assert schema.model_dump(mode="json") == {
        "kind": "schema_output",
        "diagnostics": [
            {
                "kind": "schema_output",
                "attempt": 1,
                "field_paths": ["sub_topics.0.title"],
            }
        ],
    }
    assert transport.model_dump(mode="json") == {
        "kind": "provider_transport",
        "type": "ProviderResponseError",
        "retryable": True,
        "status_code": None,
    }


def test_judge_feedback_round_trips_safe_evaluator_diagnostics() -> None:
    import deep_research.evaluation.models as models_module

    diagnostic_type = getattr(models_module, "EvaluationDiagnostic", None)
    assert diagnostic_type is not None
    feedback = JudgeFeedback(
        status="judge_not_run",
        not_run_reason="judge_schema_failure",
        prompt_id="individual-agent-judge",
        rubric_version=1,
        prompt_fingerprint="abc123abc123",
        judge_model="gpt-5.6-luna",
        judge_configuration_fingerprint="def456def456",
        diagnostics=(
            diagnostic_type(
                kind="schema_output",
                attempt=2,
                field_paths=("scores.completeness",),
            ),
        ),
    )

    restored = JudgeFeedback.model_validate_json(feedback.model_dump_json())
    assert restored == feedback
    assert "provider output" not in feedback.model_dump_json()


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


def test_target_output_records_thinking_mode_not_reasoning_mode() -> None:
    from deep_research.evaluation.models import TargetOutput

    assert "reasoning_mode" not in TargetOutput.model_fields
    assert TargetOutput.model_fields["thinking_mode"].default == "enabled"


def test_dependency_ledger_has_a_bounded_versioned_scenario_miss_contract() -> None:
    ledger = DependencyLedger(
        scenario_contract_version=2,
        scenario_misses=["web_search: unscripted query"],
    )

    payload = ledger.model_dump(mode="json")
    assert payload["scenario_contract_version"] == 2
    assert payload["scenario_misses"] == ["web_search: unscripted query"]
    assert DependencyLedger.model_validate(payload) == ledger
    assert DependencyLedger().scenario_contract_version == 1

    with pytest.raises(ValueError):
        DependencyLedger(scenario_misses=["x" * 257])
    with pytest.raises(ValueError):
        DependencyLedger(
            scenario_misses=[
                f"web_search: {index}" for index in range(17)
            ]
        )


def test_dependency_ledger_round_trips_bounded_source_url_fingerprints() -> None:
    fingerprint = sha256(
        "https://example.com/source".encode("utf-8")
    ).hexdigest()
    ledger = DependencyLedger(source_url_fingerprints=[fingerprint])

    payload = ledger.model_dump(mode="json")

    assert payload["source_url_fingerprints"] == [fingerprint]
    assert DependencyLedger.model_validate(payload) == ledger
    assert (
        DependencyLedger.model_validate({}).source_url_fingerprints == []
    )

    with pytest.raises(ValueError):
        DependencyLedger(source_url_fingerprints=["not-a-sha256"])
    with pytest.raises(ValueError):
        DependencyLedger(source_url_fingerprints=[fingerprint] * 129)


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


def test_repetition_result_accepts_bounded_typed_telemetry() -> None:
    result = RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=1,
        completed=True,
        gates=GateReport(),
        deterministic_quality=0.75,
        deterministic_metrics={"coverage": 1.0, "ordering": 0.0},
        prohibited_call_count=2,
        react_stop_reason="provider_error",
        fallback_provider_diagnostic=FallbackProviderDiagnostic(
            kind="output_limit", operation="react_decision"
        ),
    )

    payload = result.model_dump(mode="json")
    assert payload["deterministic_metrics"] == {
        "coverage": 1.0,
        "ordering": 0.0,
    }
    assert payload["fallback_provider_diagnostic"] == {
        "kind": "output_limit",
        "operation": "react_decision",
    }


@pytest.mark.parametrize(
    "metrics",
    [
        {"": 1.0},
        {"Not_snake_case": 1.0},
        {"has-dash": 1.0},
        {"too_large": 1.1},
        {"not_finite": float("nan")},
        {f"metric_{index}": 1.0 for index in range(17)},
    ],
)
def test_repetition_result_rejects_malformed_metric_maps(metrics) -> None:
    with pytest.raises(ValueError):
        RepetitionResult(
            case_id="focused-decomposition",
            case_version=1,
            repetition=1,
            completed=True,
            gates=GateReport(),
            deterministic_metrics=metrics,
        )


@pytest.mark.parametrize("bad_value", [True, False, "0.0", "1.0"])
def test_repetition_result_rejects_non_numeric_metric_values_before_coercion(
    bad_value,
) -> None:
    with pytest.raises(ValueError):
        RepetitionResult(
            case_id="focused-decomposition",
            case_version=1,
            repetition=1,
            completed=True,
            gates=GateReport(),
            deterministic_metrics={"coverage": bad_value, "ordering": 0.0},
        )


@pytest.mark.parametrize("count", [-1, 10_001, True])
def test_repetition_result_rejects_invalid_prohibited_call_counts(count) -> None:
    with pytest.raises(ValueError):
        RepetitionResult(
            case_id="focused-decomposition",
            case_version=1,
            repetition=1,
            completed=True,
            gates=GateReport(),
            prohibited_call_count=count,
        )


def test_repetition_result_rejects_unknown_stop_reasons_and_unsafe_fallbacks() -> None:
    with pytest.raises(ValueError):
        ReActSummary(
            iterations=0,
            tool_calls=0,
            stop_reason="unknown",
            max_iterations=1,
            tool_budget=0,
        )

    with pytest.raises(ValueError):
        ReActSummary(
            iterations=0,
            tool_calls=0,
            stop_reason="completed",
            max_iterations=1,
            tool_budget=0,
        )

    with pytest.raises(ValueError):
        FallbackProviderDiagnostic(
            kind="output_limit",
            operation="react_decision",
            raw_provider_output="must not persist",
        )
