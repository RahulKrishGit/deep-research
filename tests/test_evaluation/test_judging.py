"""The fixed, versioned LLM-as-judge."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deep_research.evaluation.judging import (
    _BLOCK_ORDER,
    COMMON_DIMENSION_WEIGHTS,
    JUDGE_PROMPT_ID,
    JUDGE_RATIONALE_GUIDANCE_MAX,
    JUDGE_RATIONALE_GUIDANCE_TARGET,
    JUDGE_SYSTEM_PROMPT,
    JudgeInput,
    build_judge_input,
    judge_prompt_fingerprint,
    judge_quality,
    render_judge_messages,
    run_judge,
)
from deep_research.evaluation.models import (
    JUDGE_RATIONALE_SCHEMA_MAX,
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

# The judge prompt fingerprint after the response contract was added. Judge
# scores recorded before this value are not comparable with scores after it.
# Superseded: 77a0898f4267 (pre-contract), 93edb1729cbb (pre-fallback).
_CONTRACT_FINGERPRINT = "74b9cddfbbee"


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
    # The judge carries its own operation-specific budget, not the planner's
    # and not the global cap. The verdict holds six common dimensions, the
    # agent-specific dimensions, and a rationale; at the global cap the
    # adapter returned output_limit with no score at all.
    assert provider.budgets == [runtime_config_for("planner").judge_max_tokens]


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
    # The first attempt fails by omission, not by an added key: JudgeVerdict now
    # tolerates additive noise, so an extra property would be accepted and this
    # test would no longer exercise the failure taxonomy at all.
    first_payload = {
        name: value
        for name, value in verdict.model_dump(mode="json").items()
        if name != "rationale"
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
    assert [item.category for item in feedback.diagnostics] == [
        "missing",
        "string_bounds",
    ]
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
            rationale="x" * (JUDGE_RATIONALE_SCHEMA_MAX + 1),
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


def test_the_judge_prompt_fingerprint_supersedes_the_pre_fallback_value() -> None:
    """The judge prompt identity changed deliberately.

    The asserted value is the fingerprint produced after the ``provider_fallback``
    block was added. The superseded value was ``93edb1729cbb``; judge scores taken
    before and after that change are not comparable, so this pin makes the next
    prompt edit a conscious act rather than a silent invalidation of recorded
    scores.

    Superseded again by the response contract below: the value before that edit
    was ``77a0898f4267``, which is the fingerprint recorded on the ``035d3c5``
    live artifacts. This is the prompt-identity change, not an incidental one.

    The fingerprint also covers ``JudgeVerdict.model_json_schema()``, so widening
    the enforced rationale bound moves it as well, even though that bound cannot
    affect a score: ``judge_quality`` reads ``scores`` alone and the rationale is
    a recorded comment. Marking the identity as changed is the safe direction.
    """
    assert judge_prompt_fingerprint(rubric_version=1) == _CONTRACT_FINGERPRINT
    assert judge_prompt_fingerprint(rubric_version=1) != "77a0898f4267"
    assert judge_prompt_fingerprint(rubric_version=1) != "93edb1729cbb"


def _critic_live_judge_body(critic_live_case) -> str:
    """The rendered judge request body for the registered live critic case."""
    judge_input = build_judge_input(
        critic_live_case_output(critic_live_case),
        critic_live_case,
        GateReport(),
        secrets=(),
    )
    return "\n".join(
        message.content for message in render_judge_messages(judge_input)
    )


def _example_instances(body: str) -> list[dict]:
    """Every example JSON instance the judge request carries, in order."""
    lines = [line for line in body.splitlines() if line.startswith('{"scores"')]
    return [json.loads(line) for line in lines]


def _prose(body: str) -> str:
    """The request with whitespace runs collapsed to single spaces.

    The template is wrapped to satisfy the line-length limit, so a phrase can
    straddle a newline. Asserting on raw text would make these tests break on a
    rewrap that changes no meaning; normalising removes that failure mode
    without weakening the assertion.
    """
    return " ".join(body.split())


def test_the_judge_prompt_states_the_response_contract(critic_live_case) -> None:
    """The prose must state the object's fields, not leave it to the schema.

    Measured: the judge schema bounds ``rationale`` at 2000 characters, but the
    prose named none of ``rationale``, ``json``, ``example``, ``field``,
    ``character``, ``object``, or ``keys``. In 30 production attempts at max
    effort, 5 first attempts exceeded the bound, with a median rationale of 1,735
    characters — 265 short of the cap. The constraint is unenforceable by the
    transport: adding ``strict`` to the request returns HTTP 400.
    """
    body = _critic_live_judge_body(critic_live_case)
    prose = _prose(body)

    assert "exactly one JSON object" in prose
    assert "no text before or after" in prose
    for field in ("scores", "agent_specific", "rationale"):
        assert field in prose, field
    # The bound lives in the schema; it has to be stated in prose as well.
    assert "2000" in prose
    assert "and no others" in prose
    assert "dimension id" in prose


def test_the_contract_forbids_the_extra_fields_the_model_actually_added(
    critic_live_case,
) -> None:
    """Naming the fields invited elaboration, so the ban has to be explicit.

    Measured after the contract first described the three fields: 4 of 5 first
    attempts added a fourth top-level key, ``agent_specific_note`` or
    ``final_note``, and one run failed terminally because the repair added it
    again. With no field named at all, the same probe recorded 0 extra keys in 30
    attempts. The prohibition therefore names the shapes actually observed.
    """
    prose = _prose(_critic_live_judge_body(critic_live_case))

    assert "Do not add any other field" in prose
    for temptation in ("note", "comment", "summary", "explanation"):
        assert temptation in prose, temptation
    assert "belongs in `rationale`" in prose


def test_the_request_uses_markdown_heading_levels_not_a_flat_list(
    critic_live_case,
) -> None:
    """This instruction owns H1; the judged run's blocks stay H2 beneath it.

    The blocks are rendered as ``## <name>`` by ``_render_blocks``, sixteen of
    them. With a flat heading list a block named ``## rubric`` reads as a
    section of this instruction rather than as part of the material being
    judged — the same collision the Critic request had with its report.
    """
    body = _critic_live_judge_body(critic_live_case)
    lines = body.splitlines()
    envelope = [line for line in lines if line.startswith("# ")]
    blocks = [line for line in lines if line.startswith("## ")]
    block_heads = {f"## {name}" for name in _BLOCK_ORDER}
    subsection_heads = {
        "## Common dimensions and their weights",
        "## Agent-specific dimensions and their anchors",
        "## How the final score is computed",
        "## How to choose each score",
        "## Reply format",
    }

    for section in (
        "# What you are scoring",
        "# How to read the run",
        "# The run to judge, block by block",
        "# Response contract",
    ):
        assert section in envelope, section
    # Every H2 is either a block of the judged run or a contract subsection, and
    # every block of the run appears exactly once.
    for line in blocks:
        assert line in block_heads or line in subsection_heads, line
    assert len([line for line in blocks if line in block_heads]) == len(_BLOCK_ORDER)
    # The run section must precede the blocks it introduces.
    assert body.index("# The run to judge, block by block") < body.index("## prompt_id")


def test_the_judge_prompt_gives_guidance_across_the_whole_scale(
    critic_live_case,
) -> None:
    """Not just the endpoints: a rubric anchors 1.0 and 0.0 and nothing between.

    Two judges can agree on the endpoints and still differ by 0.3 on a middling
    run, which is why the middle of the range needs instruction of its own.
    """
    body = _critic_live_judge_body(critic_live_case)
    prose = _prose(body)

    assert "## How to choose each score" in body
    for band in ("0.0-0.2:", "0.2-0.4:", "0.4-0.6:", "0.6-0.8:", "0.8-1.0:"):
        assert band in body, band
    # The rule must be anchored on evidence, not on prose quality.
    assert "not from the run's overall polish" in prose
    assert "A confident claim with no support behind it" in prose


def test_the_weighting_is_stated_as_explicit_arithmetic(critic_live_case) -> None:
    """Which dimension carries which weight must be readable, not implied.

    Groundedness at 0.25 outweighs any single agent dimension, and agent
    dimensions carry no weight at all. Stating that as a formula is what stops a
    run being rewarded for prose.
    """
    body = _critic_live_judge_body(critic_live_case)

    assert "## How the final score is computed" in body
    for name, weight in COMMON_DIMENSION_WEIGHTS.items():
        assert f"{weight:.2f} x {name}" in body, name
    assert f"sum to {sum(COMMON_DIMENSION_WEIGHTS.values()):.2f}" in body
    assert "carry no weight at all" in body


def test_the_stated_limit_is_harder_than_the_enforced_one(critic_live_case) -> None:
    """The prompt states a stricter rule than the system enforces, on purpose.

    The statement is the steering device: a credible hard limit is what keeps the
    model inside the range in most cases. Enforcement is local and wider, so the
    minority of runs which overshoot are still scored rather than becoming an
    unscorable ``string_too_long`` failure — measured at 5 of 30 production
    attempts when the limit was declared at 2000.

    Neither number may be reconciled to the other. Softening the prose loses the
    compliance pressure; tightening enforcement restores the failures this change
    exists to remove. Both are pinned here so that edit has to be deliberate.
    """
    body = _critic_live_judge_body(critic_live_case)
    prose = _prose(body)
    contract = _prose(body.partition("# Response contract")[2])
    schema_property = JudgeVerdict.model_json_schema()["properties"]["rationale"]

    assert JUDGE_RATIONALE_SCHEMA_MAX == 20000
    assert JUDGE_RATIONALE_GUIDANCE_MAX == 2000
    assert JUDGE_RATIONALE_GUIDANCE_TARGET == 1500
    assert JUDGE_RATIONALE_GUIDANCE_TARGET < JUDGE_RATIONALE_GUIDANCE_MAX
    assert JUDGE_RATIONALE_GUIDANCE_MAX < JUDGE_RATIONALE_SCHEMA_MAX

    # The steering statement is kept, including the claim of rejection.
    assert str(JUDGE_RATIONALE_GUIDANCE_MAX) in contract
    assert "The limit is hard" in prose
    assert "rejected outright" in prose
    # The model is given one number, not two: the enforcement bound is neither
    # declared in the schema nor stated in the prose.
    assert "maxLength" not in schema_property
    assert str(JUDGE_RATIONALE_SCHEMA_MAX) not in prose


def test_the_transmitted_schema_states_no_rationale_length(
    critic_live_case,
) -> None:
    """The request must carry exactly one length signal, not two.

    The prose states a hard 2000. If the schema appended by the provider also
    declared a length, it would present a second, different number for the same
    field -- measured at 20000 while the prose said 2000 -- and the model would
    have to guess which to obey. So the declared constraint is only ``minLength``
    and the maximum is enforced locally, after the response returns.
    """
    schema_property = JudgeVerdict.model_json_schema()["properties"]["rationale"]

    assert schema_property["minLength"] == 1
    assert "maxLength" not in schema_property
    # And the number the prose states is the only length the model is shown.
    body = _critic_live_judge_body(critic_live_case)
    assert str(JUDGE_RATIONALE_SCHEMA_MAX) not in _prose(body)


def test_the_rationale_bound_is_still_enforced_outside_the_schema() -> None:
    """Removing the declaration must not remove the check.

    The bound is a backstop against pathological output, enforced by a validator
    so that an over-long rationale stays a *validation* failure the attempt loop
    repairs, rather than a truncated response it never sees.
    """
    from pydantic import ValidationError

    lengths = (
        2263,
        2500,
        JUDGE_RATIONALE_GUIDANCE_MAX,
        JUDGE_RATIONALE_SCHEMA_MAX,
    )
    for length in lengths:
        verdict = JudgeVerdict.model_validate(
            {
                "scores": {name: 0.8 for name in JudgeScores.model_fields},
                "rationale": "x" * length,
            }
        )
        assert len(verdict.rationale) == length

    with pytest.raises(ValidationError) as raised:
        JudgeVerdict.model_validate(
            {
                "scores": {name: 0.8 for name in JudgeScores.model_fields},
                "rationale": "x" * (JUDGE_RATIONALE_SCHEMA_MAX + 1),
            }
        )
    # Classified as a string bound, so the shared repair guidance still applies.
    assert {item["type"] for item in raised.value.errors()} == {"string_too_long"}
    assert raised.value.errors()[0]["loc"] == ("rationale",)


def test_an_empty_rationale_is_still_rejected() -> None:
    """``minLength`` stays declared: an empty rationale is a useless verdict."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate(
            {
                "scores": {name: 0.8 for name in JudgeScores.model_fields},
                "rationale": "",
            }
        )


def test_an_added_note_field_is_dropped_rather_than_fatal() -> None:
    """An additive note must not cost a whole evaluation repetition.

    Measured with the fields named in the prompt: 11 of 30 probe attempts added
    ``agent_specific_note``, ``agent_specific_notes``, ``rationale_note`` or
    ``final_note``, the repair sometimes invented another, and 3 of 30 runs were
    lost as unscorable. Nothing reads an added key, so it is dropped.
    """
    verdict = JudgeVerdict.model_validate(
        {
            "scores": {name: 0.8 for name in JudgeScores.model_fields},
            "rationale": "Grounded in the cited sources.",
            "agent_specific_note": "Explains the agent-specific scores.",
            "final_note": "One more remark.",
        }
    )

    assert set(verdict.model_dump()) == {"scores", "agent_specific", "rationale"}
    assert verdict.rationale == "Grounded in the cited sources."


def test_drift_in_a_required_field_is_still_fatal() -> None:
    """Tolerating additions must not tolerate a rename or an omission.

    ``scores`` and ``rationale`` stay required, so the drift that matters is
    still caught: relaxing ``extra`` only stops additions from failing.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as renamed:
        JudgeVerdict.model_validate(
            {"score": {name: 0.8 for name in JudgeScores.model_fields},
             "rationale": "text"}
        )
    assert {item["type"] for item in renamed.value.errors()} == {"missing"}

    with pytest.raises(ValidationError) as omitted:
        JudgeVerdict.model_validate(
            {"scores": {name: 0.8 for name in JudgeScores.model_fields}}
        )
    assert {item["type"] for item in omitted.value.errors()} == {"missing"}


def test_the_relaxation_is_scoped_to_the_verdict() -> None:
    """Other contracts still forbid extras; only this one model relaxes.

    The nested scores object keeps the project default, so an invented dimension
    inside it is still rejected.
    """
    from pydantic import ValidationError

    assert JudgeVerdict.model_config["extra"] == "ignore"
    assert JudgeScores.model_config.get("extra", "forbid") == "forbid"
    with pytest.raises(ValidationError):
        JudgeScores.model_validate(
            {**{name: 0.8 for name in JudgeScores.model_fields}, "notes": "extra"}
        )


def test_the_judge_prompt_shows_valid_json_examples(critic_live_case) -> None:
    """The examples must be real instances, not placeholder skeletons.

    Angle-bracket placeholders are not valid JSON, so they show the model
    something that is neither a schema nor an example. The provider supplies the
    schema in a trailing system message; these supply instances.
    """
    body = _critic_live_judge_body(critic_live_case)
    examples = _example_instances(body)

    assert len(examples) == 2
    for example in examples:
        assert sorted(example) == ["agent_specific", "rationale", "scores"]
        assert sorted(example["scores"]) == sorted(JudgeScores.model_fields)
        for value in example["scores"].values():
            assert isinstance(value, (int, float))
            assert 0.0 <= value <= 1.0
        for value in example["agent_specific"].values():
            assert 0.0 <= value <= 1.0
        assert 0 < len(example["rationale"]) <= JUDGE_RATIONALE_GUIDANCE_MAX
    for line in body.splitlines():
        if line.startswith('{"scores"'):
            assert "<" not in line


def test_the_two_examples_demonstrate_opposite_ends_of_the_scale(
    critic_live_case,
) -> None:
    """One weak and one strong, so no single value reads as the target.

    A single mid-scale example is the anchoring failure the Critic prompt hit
    first: the model aims at the illustrated number instead of judging. The pair
    must actually straddle the scale, and each must sit inside one named band.
    """
    weak, strong = _example_instances(_critic_live_judge_body(critic_live_case))

    assert max(weak["scores"].values()) <= 0.4
    assert min(strong["scores"].values()) >= 0.8
    assert "Weak run:" in _critic_live_judge_body(critic_live_case)
    assert "Strong run:" in _critic_live_judge_body(critic_live_case)
    # Values vary within each example, so no single number is the apparent answer.
    assert len(set(weak["scores"].values())) > 1
    assert len(set(strong["scores"].values())) > 1


def test_the_examples_score_every_rubric_dimension_by_its_real_id(
    critic_live_case,
) -> None:
    """Agent-specific keys must be the rubric's own ids, not invented ones.

    The dimensions differ per agent, so the examples are generated from the
    rubric in hand. A hard-coded id would be wrong for every other agent, and a
    wrong id in an example is a wrong id in the answer.
    """
    body = _critic_live_judge_body(critic_live_case)
    rubric_ids = [
        dimension.dimension_id
        for dimension in critic_live_case.judge_rubric.agent_dimensions
    ]

    assert rubric_ids, "the live critic rubric must carry agent dimensions"
    for example in _example_instances(body):
        assert sorted(example["agent_specific"]) == sorted(rubric_ids)


def test_the_examples_are_labelled_as_illustrative(critic_live_case) -> None:
    """Illustrative values must not read as a target score.

    The examples introduce numbers the prompt did not carry before, so the
    prompt has to say the values are placeholders; otherwise a judge could
    anchor on them the way the Critic's single example invited splitting the
    difference.
    """
    body = _critic_live_judge_body(critic_live_case)

    assert "placeholders" in _prose(body)
    assert "not a target to match" in _prose(body)
