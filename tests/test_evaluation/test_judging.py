"""The fixed, versioned LLM-as-judge."""

from __future__ import annotations

import pytest

from deep_research.evaluation.judging import (
    COMMON_DIMENSION_WEIGHTS,
    JUDGE_PROMPT_ID,
    build_judge_input,
    judge_prompt_fingerprint,
    judge_quality,
    render_judge_messages,
    run_judge,
)
from deep_research.evaluation.models import JudgeScores, JudgeVerdict
from deep_research.providers import StructuredOutputError
from tests.evaluation_fakes import FakeStructuredProvider


def test_the_common_weights_match_the_approved_table() -> None:
    assert COMMON_DIMENSION_WEIGHTS == {
        "role_adherence": 0.15,
        "completeness": 0.20,
        "groundedness": 0.25,
        "reasoning_quality": 0.15,
        "usefulness": 0.15,
        "uncertainty_calibration": 0.10,
    }
    assert sum(COMMON_DIMENSION_WEIGHTS.values()) == pytest.approx(1.0)


def test_judge_quality_is_the_fixed_weighted_sum() -> None:
    scores = JudgeScores(
        role_adherence=1.0,
        completeness=0.0,
        groundedness=1.0,
        reasoning_quality=0.0,
        usefulness=1.0,
        uncertainty_calibration=0.0,
    )

    assert judge_quality(scores) == pytest.approx(0.15 + 0.25 + 0.15)


def test_a_perfect_score_is_one_and_a_zero_score_is_zero() -> None:
    high = JudgeScores(**{name: 1.0 for name in COMMON_DIMENSION_WEIGHTS})
    low = JudgeScores(**{name: 0.0 for name in COMMON_DIMENSION_WEIGHTS})

    assert judge_quality(high) == pytest.approx(1.0)
    assert judge_quality(low) == pytest.approx(0.0)


def test_agent_specific_dimensions_do_not_change_the_score(
    planner_case,
) -> None:
    """Agent rubrics add anchors, never weights."""
    scores = JudgeScores(**{name: 0.5 for name in COMMON_DIMENSION_WEIGHTS})
    with_extra = JudgeVerdict(
        scores=scores,
        agent_specific={"decomposition_quality": 0.0},
        rationale="r",
    )

    assert judge_quality(with_extra.scores) == pytest.approx(0.5)


def test_the_judge_input_carries_exactly_what_the_spec_permits(
    planner_case, clean_target_output, clean_gate_report
) -> None:
    judge_input = build_judge_input(
        clean_target_output, planner_case, clean_gate_report, secrets=()
    )
    payload = judge_input.model_dump(mode="json")

    for allowed in (
        "purpose",
        "rubric",
        "inputs",
        "reference_expectations",
        "agent_output",
        "state_update",
        "evidence",
        "trajectory",
        "gate_results",
    ):
        assert allowed in payload

    rendered = repr(payload).lower()
    assert "api_key" not in rendered
    assert "client" not in rendered
    assert "chain_of_thought" not in rendered
    assert "dependency_scenario" not in payload


def test_a_secret_in_the_output_is_redacted_before_the_judge_sees_it(
    planner_case, clean_target_output, clean_gate_report
) -> None:
    leaking = clean_target_output.model_copy(
        update={"state_update": {"note": "sk-abcdefghijklmnop"}}
    )

    judge_input = build_judge_input(
        leaking,
        planner_case,
        clean_gate_report,
        secrets=("sk-abcdefghijklmnop",),
    )

    assert "sk-abcdefghijklmnop" not in repr(
        judge_input.model_dump(mode="json")
    )
    assert "[REDACTED]" in repr(judge_input.model_dump(mode="json"))


def test_the_prompt_carries_the_rubric_anchors_and_the_weights(
    planner_case, clean_target_output, clean_gate_report
) -> None:
    messages = render_judge_messages(
        build_judge_input(
            clean_target_output, planner_case, clean_gate_report, secrets=()
        )
    )
    body = "\n".join(message.content for message in messages)

    assert planner_case.judge_rubric.agent_dimensions[0].dimension_id in body
    assert "0.25" in body  # groundedness weight
    assert planner_case.purpose in body
    assert messages[0].role == "developer"


def test_the_prompt_fingerprint_changes_with_the_rubric_version() -> None:
    assert judge_prompt_fingerprint(rubric_version=1) != (
        judge_prompt_fingerprint(rubric_version=2)
    )
    assert judge_prompt_fingerprint(rubric_version=1) == (
        judge_prompt_fingerprint(rubric_version=1)
    )


@pytest.mark.asyncio
async def test_a_successful_judge_produces_scored_feedback(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    verdict = JudgeVerdict(
        scores=JudgeScores(**{n: 0.8 for n in COMMON_DIMENSION_WEIGHTS}),
        agent_specific={"decomposition_quality": 0.9},
        rationale="Distinct, prioritized subtopics with usable queries.",
    )
    provider = FakeStructuredProvider(responses=[verdict])

    feedback = await run_judge(
        provider,
        clean_target_output,
        planner_case,
        clean_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert feedback.status == "scored"
    assert feedback.judge_quality == pytest.approx(0.8)
    assert feedback.prompt_id == JUDGE_PROMPT_ID
    assert feedback.rubric_version == 1
    assert feedback.judge_model == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_the_judge_runs_even_when_a_hard_gate_failed(
    planner_case, clean_target_output, failing_gate_report, runtime_config_for
) -> None:
    verdict = JudgeVerdict(
        scores=JudgeScores(**{n: 0.4 for n in COMMON_DIMENSION_WEIGHTS}),
        agent_specific={},
        rationale="Output exists but is thin.",
    )
    provider = FakeStructuredProvider(responses=[verdict])

    feedback = await run_judge(
        provider,
        clean_target_output,
        planner_case,
        failing_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert feedback.status == "scored"


@pytest.mark.asyncio
async def test_no_evaluable_output_is_judge_not_run_with_a_typed_reason(
    planner_case, failed_target_output, failing_gate_report, runtime_config_for
) -> None:
    provider = FakeStructuredProvider(responses=[])

    feedback = await run_judge(
        provider,
        failed_target_output,
        planner_case,
        failing_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert feedback.status == "judge_not_run"
    assert feedback.not_run_reason == "no_evaluable_output"
    assert feedback.judge_quality is None
    assert provider.calls == []


@pytest.mark.asyncio
async def test_a_judge_provider_failure_is_typed_and_never_scored(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    provider = FakeStructuredProvider(
        responses=[StructuredOutputError("schema failed after one repair")]
    )

    feedback = await run_judge(
        provider,
        clean_target_output,
        planner_case,
        clean_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert feedback.status == "judge_not_run"
    assert feedback.not_run_reason == "judge_schema_failure"
    assert feedback.judge_quality is None


@pytest.mark.asyncio
async def test_the_judge_is_never_retried_beyond_the_provider_repair(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """One structured-output repair belongs to the provider; the harness
    must not add a second attempt on top of it."""
    provider = FakeStructuredProvider(
        responses=[
            StructuredOutputError("first"),
            JudgeVerdict(
                scores=JudgeScores(
                    **{n: 1.0 for n in COMMON_DIMENSION_WEIGHTS}
                ),
                agent_specific={},
                rationale="second attempt",
            ),
        ]
    )

    feedback = await run_judge(
        provider,
        clean_target_output,
        planner_case,
        clean_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert len(provider.calls) == 1
    assert feedback.status == "judge_not_run"


@pytest.mark.asyncio
async def test_an_overlong_rationale_is_a_schema_failure_not_a_truncation(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeVerdict(
            scores=JudgeScores(**{n: 0.5 for n in COMMON_DIMENSION_WEIGHTS}),
            agent_specific={},
            rationale="x" * 3000,
        )
