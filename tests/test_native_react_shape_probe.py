"""Adversarial gate tests for the reviewed native ReAct shape probe (v2).

The probe is the evidence instrument for the live native-shape release gate, so
the gate has to fail closed against a batch that merely *looks* clean. These
tests drive the probe's own classification, record, and gate functions with
synthetic 30-record batches, including the exact exploit found in review: 29
valid calls plus one non-blank DSML final answer used to pass a probe whose
gate never inspected the final answer's text.

They also pin the privacy contract by value: provider text, tool arguments, and
exception messages carry a sentinel that must be absent from the serialized
record, and a record's keys must be exactly the enumerated field list.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from deep_research.observability.tracker import TokenUsage
from deep_research.providers import (
    NativeToolCall,
    NativeToolTurn,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
)
from deep_research.providers.native_output import native_text_violation

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPOSITORY_ROOT / "scripts" / "native_react_shape_probe.py"

SENTINEL = "PROBE_SENTINEL_MUST_NOT_BE_RETAINED_7C31"

MODEL = "deepseek-v4-flash"
ALLOWED_TOOLS = frozenset({"web_search", "query_memory"})

DSML_FINAL = (
    '<|DSML|tool_calls><|DSML|invoke name="web_search">'
    '{"query":"qec"}</|DSML|invoke></|DSML|tool_calls>'
)
TOOL_MARKUP_FINAL = '<tool_call>{"query":"qec"}</tool_call>'
FENCED_FINAL = 'Here is the call:\n```json\n{"query":"qec"}\n```'
LEGACY_ACTION_FINAL = json.dumps(
    {"action": "web_search", "tool_input_json": '{"query":"qec"}'}
)

# The plan's field list, in the plan's order. The probe may retain these fields
# and nothing else.
ENUMERATED_FIELDS = (
    "run",
    "outcome",
    "finish_category",
    "tool_name",
    "arguments_are_object",
    "ordinary_text_present",
    "ordinary_text_chars",
    "dsml_marker_present",
    "tool_markup_present",
    "fence_present",
    "legacy_action_json_present",
    "failure_category",
    "failure_origin",
    "retryable",
    "status_code",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)

PROHIBITED_FINAL_ANSWERS = {
    "dsml": DSML_FINAL,
    "tool_markup": TOOL_MARKUP_FINAL,
    "markdown_fence": FENCED_FINAL,
    "legacy_action_json": LEGACY_ACTION_FINAL,
}


@pytest.fixture(scope="module")
def probe() -> Any:
    """Load the checked-in probe by path; it is a script, not a package."""
    assert PROBE_PATH.is_file(), f"the reviewed v2 probe is missing at {PROBE_PATH}"
    spec = importlib.util.spec_from_file_location(
        "native_react_shape_probe_under_test", PROBE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _usage(seed: int) -> TokenUsage:
    return TokenUsage(input_tokens=seed, output_tokens=seed + 1)


def _tool_turn(
    *, name: str = "web_search", arguments: str = '{"query":"qec"}', seed: int = 1
) -> NativeToolTurn:
    return NativeToolTurn(
        model=MODEL,
        usage=_usage(seed),
        tool_call=NativeToolCall(tool_name=name, arguments_json=arguments),
    )


def _final_turn(answer: str, *, seed: int = 1) -> NativeToolTurn:
    return NativeToolTurn(model=MODEL, usage=_usage(seed), final_answer=answer)


def _telemetry(seed: int = 1) -> ProviderResponseTelemetry:
    return ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=32768,
        usage=_usage(seed),
        request_attempt=1,
    )


def _accepted_call(probe: Any, run: int) -> dict[str, Any]:
    return probe.build_turn_record(
        run=run, turn=_tool_turn(seed=run), allowed=ALLOWED_TOOLS
    )


def _twenty_nine_accepted_calls(probe: Any) -> list[dict[str, Any]]:
    return [_accepted_call(probe, run) for run in range(1, 30)]


FAILURE_TAILS: dict[str, BaseException] = {
    "local_response_failure": ProviderResponseError(
        "local refusal", failure_origin="local_response"
    ),
    "sdk_response_failure": ProviderResponseError("sdk refusal", failure_origin="sdk"),
    "timeout": ProviderTimeoutError("timed out"),
    "rate_limit": ProviderRateLimitError("rate limited"),
}

# Batch suffixes whose 30th record is one prohibited final answer.
ANSWER_TAILS = {
    "dsml_final": "dsml",
    "fenced_action_final": "markdown_fence",
    "legacy_action_json_final": "legacy_action_json",
}


def _adversarial_tail(probe: Any, suffix: str) -> dict[str, Any]:
    """The 30th record of each adversarial batch, built by the probe itself."""
    if suffix in ANSWER_TAILS:
        return probe.build_turn_record(
            run=30,
            turn=_final_turn(PROHIBITED_FINAL_ANSWERS[ANSWER_TAILS[suffix]], seed=30),
            allowed=ALLOWED_TOOLS,
        )
    if suffix == "unknown_tool":
        return probe.build_turn_record(
            run=30, turn=_tool_turn(name=SENTINEL, seed=30), allowed=ALLOWED_TOOLS
        )
    if suffix == "non_object_arguments":
        return probe.build_turn_record(
            run=30, turn=_tool_turn(arguments='["not","an","object"]', seed=30),
            allowed=ALLOWED_TOOLS,
        )
    if suffix == "output_limit":
        return probe.build_failure_record(
            run=30, error=ProviderOutputLimitError(_telemetry(seed=30))
        )
    return probe.build_failure_record(run=30, error=FAILURE_TAILS[suffix])


ADVERSARIAL_BATCH_NAMES = (
    "twenty_nine_calls_plus_dsml_final",
    "twenty_nine_calls_plus_fenced_action_final",
    "twenty_nine_calls_plus_legacy_action_json_final",
    "twenty_nine_calls_plus_unknown_tool",
    "twenty_nine_calls_plus_non_object_arguments",
    "twenty_nine_calls_plus_local_response_failure",
    "twenty_nine_calls_plus_sdk_response_failure",
    "twenty_nine_calls_plus_timeout",
    "twenty_nine_calls_plus_output_limit",
)


def _adversarial_batch(probe: Any, batch: str) -> list[dict[str, Any]]:
    return [
        *_twenty_nine_accepted_calls(probe),
        _adversarial_tail(probe, batch.removeprefix("twenty_nine_calls_plus_")),
    ]


@pytest.mark.parametrize("batch", ADVERSARIAL_BATCH_NAMES)
def test_the_gate_fails_every_adversarial_batch(probe: Any, batch: str) -> None:
    records = _adversarial_batch(probe, batch)
    assert len(records) == 30
    verdict = probe.evaluate_gate(records, 30)
    assert verdict["passed"] is False, f"{batch} passed the gate"


def test_thirty_accepted_records_pass_the_gate(probe: Any) -> None:
    """The gate must be falsifiable: a clean batch still has to pass."""
    records = [*_twenty_nine_accepted_calls(probe), _accepted_call(probe, 30)]
    verdict = probe.evaluate_gate(records, 30)
    assert verdict["passed"] is True
    assert verdict["shape_failures"] == 0
    assert verdict["provider_failures"] == 0


def test_a_short_batch_fails_the_gate(probe: Any) -> None:
    verdict = probe.evaluate_gate(_twenty_nine_accepted_calls(probe), 30)
    assert verdict["passed"] is False


def test_the_review_exploit_fails_with_exactly_one_shape_failure(probe: Any) -> None:
    """29 valid calls plus one non-blank DSML final answer must not pass."""
    records = _adversarial_batch(probe, "twenty_nine_calls_plus_dsml_final")
    verdict = probe.evaluate_gate(records, 30)
    assert verdict["passed"] is False
    assert verdict["shape_failures"] == 1
    assert verdict["provider_failures"] == 0
    assert verdict["shape_failed_runs"] == [30]


@pytest.mark.parametrize("shape", sorted(PROHIBITED_FINAL_ANSWERS))
def test_a_prohibited_final_answer_is_a_shape_failure(probe: Any, shape: str) -> None:
    record = probe.build_turn_record(
        run=1, turn=_final_turn(PROHIBITED_FINAL_ANSWERS[shape]), allowed=ALLOWED_TOOLS
    )
    assert record["outcome"] == "tool_protocol_text"
    assert record["outcome"] in probe.SHAPE_FAILURE_OUTCOMES


def test_the_probe_agrees_with_the_production_text_detector(probe: Any) -> None:
    """Task 4's detector is the single source of truth for text shape."""
    for text in PROHIBITED_FINAL_ANSWERS.values():
        violation = native_text_violation(text)
        assert violation is not None
        flags = probe.shape_flags(text)
        assert flags[probe.VIOLATION_FLAGS[violation]] is True
        assert flags["ordinary_text_present"] is False
    prose = "The evidence supports a moderate confidence assessment."
    assert native_text_violation(prose) is None
    prose_flags = probe.shape_flags(prose)
    assert prose_flags["ordinary_text_present"] is True
    assert prose_flags["ordinary_text_chars"] == len(prose)
    assert not any(
        prose_flags[flag] for flag in probe.VIOLATION_FLAGS.values()
    )


def test_an_ordinary_final_answer_is_accepted(probe: Any) -> None:
    record = probe.build_turn_record(
        run=1,
        turn=_final_turn("The evidence supports a moderate confidence assessment."),
        allowed=ALLOWED_TOOLS,
    )
    assert record["outcome"] == "final_answer"
    assert record["ordinary_text_present"] is True
    assert record["ordinary_text_chars"] == len(
        "The evidence supports a moderate confidence assessment."
    )


def test_character_counts_are_measured_not_hard_coded(probe: Any) -> None:
    short_answer = "short"
    long_answer = "a considerably longer final answer"
    short = probe.build_turn_record(
        run=1, turn=_final_turn(short_answer), allowed=ALLOWED_TOOLS
    )
    long = probe.build_turn_record(
        run=2, turn=_final_turn(long_answer), allowed=ALLOWED_TOOLS
    )
    assert short["ordinary_text_chars"] == len(short_answer)
    assert long["ordinary_text_chars"] == len(long_answer)
    assert short["ordinary_text_chars"] != long["ordinary_text_chars"]


def test_record_keys_are_exactly_the_enumerated_fields(probe: Any) -> None:
    assert tuple(probe.RECORD_FIELDS) == ENUMERATED_FIELDS
    records = _adversarial_batch(probe, "twenty_nine_calls_plus_dsml_final")
    records.append(
        probe.build_failure_record(run=31, error=ProviderOutputLimitError(_telemetry()))
    )
    records.append(
        probe.build_failure_record(run=32, error=ProviderTimeoutError("timed out"))
    )
    records.append(
        probe.build_turn_record(
            run=33, turn=_final_turn("plain prose answer"), allowed=ALLOWED_TOOLS
        )
    )
    for record in records:
        assert tuple(record) == ENUMERATED_FIELDS


def test_no_final_answer_text_reaches_a_record(probe: Any) -> None:
    for shape, text in PROHIBITED_FINAL_ANSWERS.items():
        secret = f"{text} {SENTINEL}"
        record = probe.build_turn_record(
            run=1, turn=_final_turn(secret), allowed=ALLOWED_TOOLS
        )
        assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_ordinary_final_answer_text_does_not_reach_a_record(probe: Any) -> None:
    record = probe.build_turn_record(
        run=1, turn=_final_turn(f"an accepted answer {SENTINEL}"), allowed=ALLOWED_TOOLS
    )
    assert record["outcome"] == "final_answer"
    assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_tool_arguments_do_not_reach_a_record(probe: Any) -> None:
    record = probe.build_turn_record(
        run=1,
        turn=_tool_turn(arguments=json.dumps({"query": SENTINEL})),
        allowed=ALLOWED_TOOLS,
    )
    assert record["outcome"] == "tool_call"
    assert record["arguments_are_object"] is True
    assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_non_object_tool_arguments_do_not_reach_a_record(probe: Any) -> None:
    record = probe.build_turn_record(
        run=1, turn=_tool_turn(arguments=f'"{SENTINEL}"'), allowed=ALLOWED_TOOLS
    )
    assert record["outcome"] == "malformed_arguments"
    assert record["arguments_are_object"] is False
    assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_an_unavailable_tool_name_is_not_retained(probe: Any) -> None:
    """A provider-chosen name is provider text, so only allow-listed names stay."""
    record = probe.build_turn_record(
        run=1, turn=_tool_turn(name=SENTINEL), allowed=ALLOWED_TOOLS
    )
    assert record["outcome"] == "unknown_tool"
    assert record["tool_name"] is None
    assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_an_allow_listed_tool_name_is_retained(probe: Any) -> None:
    record = probe.build_turn_record(
        run=1, turn=_tool_turn(name="query_memory"), allowed=ALLOWED_TOOLS
    )
    assert record["outcome"] == "tool_call"
    assert record["tool_name"] == "query_memory"


def test_exception_messages_do_not_reach_a_record(probe: Any) -> None:
    for error in (
        ProviderResponseError(f"refused {SENTINEL}", failure_origin="local_response"),
        ProviderResponseError(f"refused {SENTINEL}", failure_origin="sdk"),
        ProviderTimeoutError(f"timed out {SENTINEL}"),
        ProviderRateLimitError(f"rate limited {SENTINEL}"),
    ):
        record = probe.build_failure_record(run=1, error=error)
        assert SENTINEL not in json.dumps(record, sort_keys=True)


def test_a_local_response_failure_is_a_shape_failure(probe: Any) -> None:
    error = ProviderResponseError("refused", failure_origin="local_response")
    record = probe.build_failure_record(run=1, error=error)
    assert record["outcome"] == "local_rejection"
    assert record["outcome"] in probe.SHAPE_FAILURE_OUTCOMES


def test_an_sdk_response_failure_is_never_defaulted_to_malformed_envelope(
    probe: Any,
) -> None:
    """A generic response error must not inherit the shape verdict."""
    record = probe.build_failure_record(
        run=1, error=ProviderResponseError("refused", failure_origin="sdk")
    )
    assert record["outcome"] == "response_sdk"
    assert record.get("failure_origin") == "sdk"
    assert "malformed_envelope" not in probe.OUTCOMES


def test_an_unknown_category_origin_combination_is_an_instrument_error(
    probe: Any,
) -> None:
    """No pair the repository cannot produce may be silently classified."""
    unknown = ProviderResponseError(
        "refused", failure_origin="local_response", failure_category="transport"
    )
    assert probe.classify_failure(unknown) == "instrument_error"
    record = probe.build_failure_record(run=1, error=unknown)
    assert record["outcome"] == "instrument_error"
    verdict = probe.evaluate_gate([*_twenty_nine_accepted_calls(probe), record], 30)
    assert verdict["passed"] is False


def test_every_known_category_origin_combination_is_classified(probe: Any) -> None:
    known = {
        ("response", "local_response"): "local_rejection",
        ("response", "sdk"): "response_sdk",
        ("transport", "sdk"): "transport_error",
        ("http", "sdk"): "http_error",
    }
    for (category, origin), expected in known.items():
        error = ProviderResponseError(
            "refused", failure_origin=origin, failure_category=category
        )
        assert probe.classify_failure(error) == expected
        assert expected in probe.OUTCOMES


def test_an_output_limit_record_is_classified_by_its_own_kind(probe: Any) -> None:
    """An output-limit record legitimately carries no origin."""
    error = ProviderOutputLimitError(_telemetry())
    assert probe.classify_failure(error) == "output_limit"
    error.failure_origin = None  # type: ignore[assignment]
    assert probe.classify_failure(error) == "output_limit"
    assert probe.classify_failure(error) != "instrument_error"
    record = probe.build_failure_record(run=1, error=error)
    assert record["outcome"] == "output_limit"
    assert record["outcome"] in probe.PROVIDER_FAILURE_OUTCOMES
    assert record["outcome"] not in probe.SHAPE_FAILURE_OUTCOMES


def test_timeouts_and_rate_limits_are_provider_failures(probe: Any) -> None:
    for error, expected in (
        (ProviderTimeoutError("timed out"), "timeout"),
        (ProviderRateLimitError("rate limited"), "rate_limit"),
    ):
        assert probe.classify_failure(error) == expected
        record = probe.build_failure_record(run=1, error=error)
        assert record["outcome"] == expected
        assert record["outcome"] in probe.PROVIDER_FAILURE_OUTCOMES


def test_the_outcome_vocabulary_is_closed(probe: Any) -> None:
    assert set(probe.SHAPE_FAILURE_OUTCOMES).isdisjoint(probe.PROVIDER_FAILURE_OUTCOMES)
    assert probe.ACCEPTED_OUTCOMES == frozenset({"tool_call", "final_answer"})
    assert set(probe.OUTCOMES) == (
        set(probe.ACCEPTED_OUTCOMES)
        | set(probe.SHAPE_FAILURE_OUTCOMES)
        | set(probe.PROVIDER_FAILURE_OUTCOMES)
    )


def test_usage_totals_are_measured_not_hard_coded(probe: Any) -> None:
    first = probe.build_turn_record(
        run=1, turn=_tool_turn(seed=11), allowed=ALLOWED_TOOLS
    )
    second = probe.build_turn_record(
        run=2, turn=_tool_turn(seed=97), allowed=ALLOWED_TOOLS
    )
    assert (first["input_tokens"], first["output_tokens"]) == (11, 12)
    assert (second["input_tokens"], second["output_tokens"]) == (97, 98)
    assert first["input_tokens"] != second["input_tokens"]


def test_the_dry_run_inventory_proves_request_construction(probe: Any) -> None:
    inventory = asyncio.run(probe.run_dry_run(30))
    assert inventory["problems"] == []
    assert inventory["passed"] is True
    assert inventory["logical_requests"] == 30
    assert inventory["sdk_request_ceiling"] == 30
    assert inventory["model"] == MODEL
    assert inventory["reasoning_effort"] == "max"
    assert inventory["thinking"] == "enabled"
    assert inventory["max_tokens"] == 32768
    assert inventory["tool_choice"] == "auto"
    assert inventory["response_format_present"] is False
    assert inventory["native_tool_names"] == ["web_search", "query_memory"]
    assert inventory["repository_retry_count"] == 0
    assert inventory["sdk_retry_count"] == 0
    assert inventory["sdk_create_calls"] == 1
    assert inventory["tool_executions"] == 0
    assert inventory["langsmith_requests"] == 0
    assert inventory["proves"] == "request construction only"


def test_the_dry_run_records_exactly_one_sdk_create(probe: Any) -> None:
    """The call count comes from wrapping the injected client's create method."""
    recorder = probe.SdkCallRecorder()
    client = probe.RecordingSDKClient(recorder)
    asyncio.run(
        probe.measure_one_request(client=client, recorder=recorder)
    )
    assert recorder.create_calls == 1
    assert len(recorder.requests) == 1
