"""Tests for ReAct step contracts and their pure helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.steps import (
    DEFAULT_SUMMARY_LIMIT,
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    parse_tool_input,
    summarize_text,
)


def test_summary_limit_default_is_prompt_sized() -> None:
    assert DEFAULT_SUMMARY_LIMIT == 200


def test_summarize_text_collapses_whitespace() -> None:
    assert summarize_text("  a\n\n b\tc  ") == "a b c"


def test_summarize_text_truncates_with_an_ellipsis() -> None:
    summary = summarize_text("x" * 100, limit=10)

    assert summary == "xxxxxxx..."
    assert len(summary) == 10


def test_summarize_text_reports_empty_input_explicitly() -> None:
    assert summarize_text("   \n  ") == "(empty)"


def test_summarize_text_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be at least 1"):
        summarize_text("hello", limit=0)


def test_parse_tool_input_decodes_a_json_object() -> None:
    assert parse_tool_input('{"query": "qec", "max_results": 3}') == {
        "query": "qec",
        "max_results": 3,
    }


def test_parse_tool_input_treats_blank_input_as_no_arguments() -> None:
    assert parse_tool_input("") == {}
    assert parse_tool_input("{}") == {}


def test_parse_tool_input_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        parse_tool_input("{query: qec}")


def test_parse_tool_input_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_tool_input('["qec"]')


def test_parse_tool_input_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_tool_input('{"score": NaN}')


def test_use_tool_decision_carries_a_tool_and_encoded_arguments() -> None:
    decision = ReActDecision(
        thought="Search for benchmarks.",
        action="use_tool",
        tool_name="web_search",
        tool_input_json='{"query": "qec"}',
    )

    assert decision.tool_name == "web_search"
    assert parse_tool_input(decision.tool_input_json) == {"query": "qec"}


def test_use_tool_decision_defaults_to_no_arguments() -> None:
    decision = ReActDecision(
        thought="List everything.",
        action="use_tool",
        tool_name="echo",
    )

    assert decision.tool_input_json == "{}"


def test_finish_decision_carries_a_final_answer() -> None:
    decision = ReActDecision(
        thought="I have enough.",
        action="finish",
        final_answer="Error rates fell 30%.",
    )

    assert decision.final_answer == "Error rates fell 30%."
    assert decision.tool_name is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"thought": "t", "action": "use_tool"},
            "use_tool decisions require tool_name",
        ),
        (
            {
                "thought": "t",
                "action": "use_tool",
                "tool_name": "echo",
                "final_answer": "done",
            },
            "use_tool decisions must not carry final_answer",
        ),
        (
            {"thought": "t", "action": "finish"},
            "finish decisions require final_answer",
        ),
        (
            {
                "thought": "t",
                "action": "finish",
                "final_answer": "done",
                "tool_name": "echo",
            },
            "finish decisions must not name a tool",
        ),
        ({"thought": "", "action": "finish", "final_answer": "d"}, "thought"),
        ({"thought": "t", "action": "reflect"}, "action"),
    ],
)
def test_decision_rejects_inconsistent_action_shapes(
    payload: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        ReActDecision.model_validate(payload)


def test_decision_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReActDecision.model_validate(
            {
                "thought": "t",
                "action": "finish",
                "final_answer": "d",
                "confidence": 0.5,
            }
        )


def test_observation_records_a_failed_tool_call() -> None:
    observation = ReActObservation(
        tool_name="web_search",
        success=False,
        summary="web_search failed (TimeoutError): upstream timed out",
        latency_ms=12.5,
        error_type="TimeoutError",
    )

    assert observation.success is False
    assert observation.error_type == "TimeoutError"


def test_observation_rejects_a_negative_latency() -> None:
    with pytest.raises(ValidationError):
        ReActObservation(
            tool_name="echo",
            success=True,
            summary="ok",
            latency_ms=-1.0,
        )


def test_step_numbering_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        ReActStep(iteration=0, thought="t", action="finish", final_answer="d")


def test_run_reports_success_for_every_stop_reason_but_provider_error() -> None:
    def _run(stop_reason: str) -> ReActRun:
        return ReActRun.model_validate(
            {"agent_name": "researcher", "stop_reason": stop_reason}
        )

    assert _run("finished").succeeded is True
    assert _run("sufficient").succeeded is True
    assert _run("max_iterations").succeeded is True
    assert _run("tool_budget_exhausted").succeeded is True
    assert _run("provider_error").succeeded is False


def test_run_rejects_an_unknown_stop_reason() -> None:
    with pytest.raises(ValidationError):
        ReActRun(agent_name="researcher", stop_reason="gave_up")
