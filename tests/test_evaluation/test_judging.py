"""The fixed, versioned LLM-as-judge."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deep_research.evaluation.judging import (
    _BLOCK_ORDER,
    COMMON_DIMENSION_WEIGHTS,
    JUDGE_PROMPT_ID,
    JUDGE_SYSTEM_PROMPT,
    JudgeInput,
    build_judge_input,
    judge_prompt_fingerprint,
    judge_quality,
    render_judge_messages,
    run_judge,
)
from deep_research.evaluation.models import (
    EvaluatorDiagnostic,
    GateReport,
    JudgeScores,
    JudgeVerdict,
    ReActSummary,
    TargetOutput,
    fallback_provider_diagnostic,
)
from deep_research.observability import LangSmithRuntimeConfig, TokenUsage, Tracker
from deep_research.providers import (
    DeepSeekJudgeProvider,
    OpenAIProviderError,
    ProviderOutputLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.utils.config import LLMConfig
from tests.evaluation_fakes import FakeStructuredProvider


class _RecordingResponses:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeDeepSeekClient:
    def __init__(self, responses: _RecordingResponses) -> None:
        self.responses = responses
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(calls=[]),
        )


def _responses_response(
    *,
    output_text: object,
    status: str = "completed",
    incomplete_reason: str | None = None,
    input_tokens: int = 8,
    output_tokens: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-response",
        status=status,
        incomplete_details=(
            None
            if incomplete_reason is None
            else SimpleNamespace(reason=incomplete_reason)
        ),
        output_text=output_text,
        model="deepseek-v4-flash",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def _deepseek_judge_config() -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
        }
    )


def _offline_deepseek_judge_provider(
    responses: _RecordingResponses,
) -> tuple[DeepSeekJudgeProvider, _FakeDeepSeekClient, Tracker]:
    client = _FakeDeepSeekClient(responses)
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    provider = DeepSeekJudgeProvider(
        _deepseek_judge_config(),
        tracker,
        client=client,
    )
    return provider, client, tracker


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
    assert feedback.judge_model == "deepseek-v4-flash"
    # The judge call never carries the planner-final budget: only the final
    # ResearchPlanDraft request may use the operation-specific value.
    assert provider.budgets == [None]


@pytest.mark.asyncio
async def test_deepseek_judge_adapter_produces_scored_feedback(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    verdict = JudgeVerdict(
        scores=JudgeScores(
            role_adherence=1.0,
            completeness=0.5,
            groundedness=0.75,
            reasoning_quality=0.25,
            usefulness=0.9,
            uncertainty_calibration=0.1,
        ),
        agent_specific={"decomposition_quality": 0.8},
        rationale="Grounded judge rationale.",
    )
    responses = _RecordingResponses(
        _responses_response(output_text=verdict.model_dump_json())
    )
    provider, client, tracker = _offline_deepseek_judge_provider(responses)

    async with tracker.session_span("session-1", "judge integration"):
        feedback = await run_judge(
            provider,
            clean_target_output,
            planner_case,
            clean_gate_report,
            runtime=runtime_config_for("planner"),
            secrets=(),
        )

    assert feedback.status == "scored"
    assert feedback.judge_quality == pytest.approx(judge_quality(verdict.scores))
    assert feedback.verdict == verdict
    assert feedback.diagnostics == ()
    assert len(responses.calls) == 1
    assert client.chat.completions.calls == []
    assert feedback.prompt_fingerprint == judge_prompt_fingerprint(
        rubric_version=1
    )


@pytest.mark.asyncio
async def test_deepseek_judge_adapter_schema_failures_stay_typed(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    verdict = JudgeVerdict(
        scores=JudgeScores(**{name: 0.6 for name in COMMON_DIMENSION_WEIGHTS}),
        agent_specific={},
        rationale="A valid baseline rationale.",
    )
    first_payload = {
        **verdict.model_dump(mode="json"),
        "unexpected": "first-invalid-response",
    }
    second_payload = {
        **verdict.model_dump(mode="json"),
        "rationale": "",
    }
    responses = _RecordingResponses(
        _responses_response(output_text=json.dumps(first_payload)),
        _responses_response(output_text=json.dumps(second_payload)),
    )
    provider, client, tracker = _offline_deepseek_judge_provider(responses)

    async with tracker.session_span("session-1", "judge integration"):
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
    assert feedback.verdict is None
    assert len(feedback.diagnostics) == 2
    assert [item.attempt for item in feedback.diagnostics] == [1, 2]
    assert all(item.kind == "schema_output" for item in feedback.diagnostics)
    assert all(
        len(path) <= 128
        for item in feedback.diagnostics
        for path in item.field_paths
    )
    assert len(responses.calls) == 2
    assert client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_deepseek_judge_adapter_output_limit_stays_typed(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    responses = _RecordingResponses(
        _responses_response(
            output_text="partial",
            status="incomplete",
            incomplete_reason="max_output_tokens",
            output_tokens=4096,
        )
    )
    provider, client, tracker = _offline_deepseek_judge_provider(responses)

    async with tracker.session_span("session-1", "judge integration"):
        feedback = await run_judge(
            provider,
            clean_target_output,
            planner_case,
            clean_gate_report,
            runtime=runtime_config_for("planner"),
            secrets=(),
        )

    assert feedback.status == "judge_not_run"
    assert feedback.not_run_reason == "judge_output_limit"
    assert feedback.judge_quality is None
    assert feedback.diagnostics == (
        EvaluatorDiagnostic(kind="output_limit", attempt=1),
    )
    assert all(item.kind != "schema_output" for item in feedback.diagnostics)
    assert len(responses.calls) == 1
    assert client.chat.completions.calls == []


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
    error = StructuredOutputError(
        "schema failed after one repair",
        diagnostics=[
            StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("$",),
                category="extra_forbidden",
            ),
            StructuredValidationDiagnostic(
                attempt=2,
                field_paths=("rationale",),
                category="string_bounds",
            ),
        ],
    )
    provider = FakeStructuredProvider(
        responses=[error]
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
    assert len(provider.calls) == 1
    assert feedback.diagnostics == (
        EvaluatorDiagnostic(
            kind="schema_output",
            attempt=1,
            category="extra_forbidden",
            field_paths=("$",),
        ),
        EvaluatorDiagnostic(
            kind="schema_output",
            attempt=2,
            category="string_bounds",
            field_paths=("rationale",),
        ),
    )


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


def test_judge_evaluator_metadata_records_thinking_mode(runtime_config_for) -> None:
    from deep_research.evaluation.judging import judge_evaluator_metadata

    metadata = judge_evaluator_metadata(runtime_config_for("planner"))

    assert metadata["thinking_mode"] == "enabled"
    assert "reasoning_mode" not in metadata


@pytest.mark.asyncio
async def test_a_judge_output_limit_failure_carries_a_diagnostic(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """A typed output-limit cause maps to ``judge_output_limit`` and its
    safe attempt telemetry survives as an evaluator diagnostic."""
    telemetry = ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=100, output_tokens=4096),
        request_attempt=1,
    )
    provider = FakeStructuredProvider(
        responses=[ProviderOutputLimitError(telemetry)]
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
    assert feedback.not_run_reason == "judge_output_limit"
    assert feedback.judge_quality is None
    assert feedback.evaluator_trace_url is None
    assert feedback.evaluator_source_url is None
    assert feedback.diagnostics == (
        EvaluatorDiagnostic(kind="output_limit", attempt=1),
    )


@pytest.mark.asyncio
async def test_a_schema_judge_failure_carries_bounded_field_paths(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """A schema cause keeps ``judge_schema_failure`` and projects only the
    allow-listed field paths, never the provider's own message text."""
    error = StructuredOutputError(
        "schema failed after one repair",
        diagnostics=[
            StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("scores.completeness",),
            )
        ],
    )
    provider = FakeStructuredProvider(responses=[error])

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
    assert feedback.diagnostics == (
        EvaluatorDiagnostic(
            kind="schema_output",
            attempt=1,
            field_paths=("scores.completeness",),
        ),
    )
    assert "schema failed after one repair" not in repr(feedback)


@pytest.mark.asyncio
async def test_a_generic_judge_provider_failure_in_a_cause_chain_stays_typed(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """A generic provider cause anywhere in the chain maps to
    ``judge_provider_failure`` and carries no fabricated diagnostics."""
    try:
        try:
            raise OpenAIProviderError("the model provider is unavailable")
        except Exception as cause:
            raise RuntimeError("wrapped by the harness") from cause
    except Exception as error:
        provider = FakeStructuredProvider(responses=[error])

    feedback = await run_judge(
        provider,
        clean_target_output,
        planner_case,
        clean_gate_report,
        runtime=runtime_config_for("planner"),
        secrets=(),
    )

    assert feedback.status == "judge_not_run"
    assert feedback.not_run_reason == "judge_provider_failure"
    assert feedback.judge_quality is None
    assert feedback.diagnostics == ()
    assert feedback.evaluator_trace_url is None
    assert feedback.evaluator_source_url is None


@pytest.mark.asyncio
async def test_judge_provider_failure_and_schema_reasons_are_distinct(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """Transport/timeout causes map to ``judge_transport``, HTTP status
    causes to ``judge_http``, and unresolved response causes stay
    ``judge_provider_failure``."""
    runtime = runtime_config_for("planner")
    scenarios = [
        (ProviderTimeoutError("judge timed out"), "judge_transport"),
        (
            ProviderResponseError(
                "connection reset", failure_category="transport"
            ),
            "judge_transport",
        ),
        (
            ProviderResponseError(
                "status 503",
                failure_category="http",
                http_status_code=503,
            ),
            "judge_http",
        ),
        (
            ProviderResponseError(
                "unusable response", failure_category="response"
            ),
            "judge_provider_failure",
        ),
    ]
    for error, expected in scenarios:
        provider = FakeStructuredProvider(responses=[error])
        feedback = await run_judge(
            provider,
            clean_target_output,
            planner_case,
            clean_gate_report,
            runtime=runtime,
            secrets=(),
        )
        assert feedback.status == "judge_not_run"
        assert feedback.not_run_reason == expected
        assert feedback.judge_quality is None
        assert feedback.diagnostics == ()


def _fallback_error() -> dict[str, object]:
    return {
        "error_type": "critic_review_provider_error",
        "source": "agent.critic",
        "message": "provider review fallback used",
        "recoverable": False,
        "details": {
            "operation": "critic_report_review",
            "provider_failure": {
                "kind": "schema_output",
                "exception_type": "StructuredOutputError",
                "diagnostics": [
                    {
                        "attempt": 1,
                        "field_paths": ["$"],
                        "category": "json_invalid",
                    }
                ],
            },
        },
    }


def critic_live_case_output(case) -> TargetOutput:
    """A completed, healthy critic repetition for the live case."""
    critique = {
        "score": 8,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": False,
        "rationale": "The report covers commercial-scale deployment.",
    }
    return TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=1,
        session_id="evaluation-critic-live-review",
        experiment_name="critic-live-review-control",
        completed=True,
        result={"critique": critique},
        state_update={"critique": critique},
        errors=[],
        react=ReActSummary(
            iterations=1,
            tool_calls=0,
            stop_reason="finished",
            max_iterations=case.expectations.max_iterations,
            tool_budget=case.expectations.max_tool_calls,
        ),
        target_model_requested="deepseek-v4-flash",
        target_model_returned="deepseek-v4-flash",
        target_reasoning_effort="max",
    )


def _judge_input_for(output, case, *, fallback=None):
    return build_judge_input(
        output, case, GateReport(), secrets=(), fallback=fallback
    )


def test_the_judge_input_names_a_provider_fallback(critic_live_case) -> None:
    output = critic_live_case_output(critic_live_case).model_copy(
        update={"errors": [_fallback_error()]}
    )

    judge_input = _judge_input_for(
        output,
        critic_live_case,
        fallback=fallback_provider_diagnostic(output),
    )

    assert judge_input.provider_fallback is not None
    assert judge_input.provider_fallback["kind"] == "schema_output"
    assert judge_input.provider_fallback["operation"] == "critic_report_review"
    assert judge_input.provider_fallback["diagnostics"][0]["category"] == (
        "json_invalid"
    )


def test_the_judge_input_omits_the_fallback_block_for_a_healthy_run(
    critic_live_case,
) -> None:
    output = critic_live_case_output(critic_live_case)

    judge_input = _judge_input_for(
        output,
        critic_live_case,
        fallback=fallback_provider_diagnostic(output),
    )

    assert judge_input.provider_fallback is None


def test_every_judge_input_field_is_rendered_as_a_block() -> None:
    """A field absent from _BLOCK_ORDER would be invisible to the judge."""
    assert set(JudgeInput.model_fields) == set(_BLOCK_ORDER)


def test_the_fallback_block_is_rendered_in_the_judge_prompt(
    critic_live_case,
) -> None:
    output = critic_live_case_output(critic_live_case).model_copy(
        update={"errors": [_fallback_error()]}
    )

    messages = render_judge_messages(
        _judge_input_for(
            output,
            critic_live_case,
            fallback=fallback_provider_diagnostic(output),
        )
    )

    assert "## provider_fallback" in messages[1].content
    assert "critic_report_review" in messages[1].content


def test_the_judge_is_told_how_to_read_a_fallback() -> None:
    assert "provider_fallback" in JUDGE_SYSTEM_PROMPT
    assert "no model review" in JUDGE_SYSTEM_PROMPT
