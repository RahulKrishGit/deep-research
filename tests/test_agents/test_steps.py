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
    react_decision_from_native_turn,
    read_evidence_urls,
    summarize_text,
)
from deep_research.observability import TokenUsage
from deep_research.providers import NativeToolCall, NativeToolTurn
from deep_research.tools.base import ToolResult
from deep_research.utils.types import ResearchError


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


def test_parse_tool_input_rejects_the_self_key() -> None:
    """A ``self`` key collides with ``BaseTool.execute(self, **kwargs)``."""
    with pytest.raises(ValueError, match="self"):
        parse_tool_input('{"self": "x"}')


def test_parse_tool_input_rejects_a_non_identifier_key() -> None:
    with pytest.raises(ValueError, match="identifier"):
        parse_tool_input('{"bad key": 1}')


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
        tool_input_json="{}",
    )

    assert decision.tool_input_json == "{}"


def test_use_tool_decision_requires_tool_input_json() -> None:
    with pytest.raises(ValidationError, match="tool_input_json"):
        ReActDecision(
            thought="List everything.",
            action="use_tool",
            tool_name="echo",
        )


def test_finish_decision_carries_a_final_answer() -> None:
    decision = ReActDecision(
        thought="I have enough.",
        action="finish",
        tool_input_json="{}",
        final_answer="Error rates fell 30%.",
    )

    assert decision.final_answer == "Error rates fell 30%."
    assert decision.tool_name is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"thought": "t", "action": "use_tool", "tool_input_json": "{}"},
            "use_tool decisions require tool_name",
        ),
        (
            {
                "thought": "t",
                "action": "use_tool",
                "tool_name": "echo",
                "tool_input_json": "{}",
                "final_answer": "done",
            },
            "use_tool decisions must not carry final_answer",
        ),
        (
            {"thought": "t", "action": "finish", "tool_input_json": "{}"},
            "finish decisions require final_answer",
        ),
        (
            {
                "thought": "t",
                "action": "finish",
                "tool_input_json": "{}",
                "final_answer": "done",
                "tool_name": "echo",
            },
            "finish decisions must not name a tool",
        ),
        (
            {
                "thought": "",
                "action": "finish",
                "tool_input_json": "{}",
                "final_answer": "d",
            },
            "thought",
        ),
        (
            {"thought": "t", "action": "reflect", "tool_input_json": "{}"},
            "action",
        ),
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
                "tool_input_json": "{}",
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


def test_decision_strict_schema_has_no_default_on_tool_input_json() -> None:
    """OpenAI's strict-mode schema converter only strips ``None`` defaults.

    ``tool_input_json`` must therefore carry no default at all — a string
    default like ``"{}"`` would still show up as ``"default": "{}"`` on the
    wire while strict mode independently forces the field into ``required``,
    so the default would be dead weight that risks a 400 from the schema
    validator.
    """
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(ReActDecision)
    tool_input_schema = schema["properties"]["tool_input_json"]

    assert "default" not in tool_input_schema
    assert "tool_input_json" in schema["required"]


def test_parse_tool_input_rejects_a_non_finite_number_nested_in_a_list() -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_tool_input('{"scores": [1, 2, 1e400]}')


def test_parse_tool_input_rejects_a_non_finite_number_nested_in_a_dict() -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_tool_input('{"nested": {"deep": {"score": 1e400}}}')


def test_react_step_rejects_a_non_finite_number_nested_in_a_list() -> None:
    with pytest.raises(ValidationError, match="finite"):
        ReActStep(
            iteration=1,
            thought="t",
            action="finish",
            final_answer="d",
            tool_input={"scores": [1, 2, float("inf")]},
        )


def test_react_step_rejects_a_non_finite_number_nested_in_a_dict() -> None:
    with pytest.raises(ValidationError, match="finite"):
        ReActStep(
            iteration=1,
            thought="t",
            action="finish",
            final_answer="d",
            tool_input={"nested": {"deep": {"score": float("nan")}}},
        )


def test_react_step_carries_a_successful_tool_result() -> None:
    step = ReActStep(
        iteration=1,
        thought="Search for benchmarks.",
        action="use_tool",
        tool_name="web_search",
        tool_input={"query": "qec"},
        observation=ReActObservation(
            tool_name="web_search",
            success=True,
            summary="web_search found 3 results",
        ),
        tool_result=ToolResult(
            tool_name="web_search",
            success=True,
            data={"results": ["a", "b", "c"]},
            latency_ms=42.0,
        ),
    )

    assert step.tool_input == {"query": "qec"}
    assert step.tool_result is not None
    assert step.tool_result.success is True
    assert step.tool_result.data == {"results": ["a", "b", "c"]}
    assert step.observation is not None
    assert step.observation.success is True


def test_react_run_carries_a_non_empty_errors_list() -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="provider_error",
        errors=[
            ResearchError(
                error_type="TimeoutError",
                source="researcher",
                message="upstream timed out",
            )
        ],
    )

    assert len(run.errors) == 1
    assert run.errors[0].error_type == "TimeoutError"
    assert run.succeeded is False


def test_a_native_tool_call_becomes_an_internal_react_decision() -> None:
    decisions = react_decision_from_native_turn(
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            tool_calls=(
                NativeToolCall(
                    tool_name="web_search", arguments_json='{"query":"qec"}'
                ),
            ),
        )
    )
    assert decisions == (
        ReActDecision(
            thought="Selected tool through provider-native calling.",
            action="use_tool",
            tool_name="web_search",
            tool_input_json='{"query":"qec"}',
        ),
    )


def test_two_native_calls_become_two_decisions_in_the_provider_order() -> None:
    """One turn carrying several calls yields one decision per call, in order.

    The provider is the parallel-call authority: it chose the order, so the
    adapter must not sort, dedupe, or collapse the calls. Each decision keeps
    the deterministic system ``thought``, never provider reasoning text.
    """
    decisions = react_decision_from_native_turn(
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            tool_calls=(
                NativeToolCall(
                    tool_name="web_search", arguments_json='{"query":"first"}'
                ),
                NativeToolCall(
                    tool_name="query_memory", arguments_json='{"query":"second"}'
                ),
            ),
        )
    )
    assert decisions == (
        ReActDecision(
            thought="Selected tool through provider-native calling.",
            action="use_tool",
            tool_name="web_search",
            tool_input_json='{"query":"first"}',
        ),
        ReActDecision(
            thought="Selected tool through provider-native calling.",
            action="use_tool",
            tool_name="query_memory",
            tool_input_json='{"query":"second"}',
        ),
    )


def _tool_step(
    tool_name: str,
    data: object,
    *,
    success: bool = True,
) -> ReActStep:
    """One step whose tool call carries exactly ``data``."""
    return ReActStep(
        iteration=1,
        thought=f"Call {tool_name}.",
        action="use_tool",
        tool_name=tool_name,
        observation=ReActObservation(
            tool_name=tool_name,
            success=success,
            summary=f"{tool_name} {'succeeded' if success else 'failed'}",
        ),
        tool_result=(
            ToolResult(tool_name=tool_name, success=True, data=data, latency_ms=1.0)
            if success
            else ToolResult(
                tool_name=tool_name,
                success=False,
                error={"type": "TimeoutError", "message": "upstream timed out"},
                latency_ms=1.0,
            )
        ),
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"url": "https://a.test/page", "text": "Read body."}, ("https://a.test/page",)),
        # A URL with nothing read from it is a candidate, not a read.
        ({"url": "https://a.test/page", "text": "   "}, ()),
        ({"url": "https://a.test/page"}, ()),
        ({"text": "Read body."}, ()),
        ({"url": 17, "text": "Read body."}, ()),
    ],
)
def test_only_a_scraped_page_with_text_is_read(
    data: object, expected: tuple[str, ...]
) -> None:
    assert read_evidence_urls(_tool_step("web_scraper", data)) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"source": "https://b.test/doc.pdf", "chunks": ["chunk"]},
            ("https://b.test/doc.pdf",),
        ),
        # An empty chunk list means the document was never actually read.
        ({"source": "https://b.test/doc.pdf", "chunks": []}, ()),
        ({"source": "https://b.test/doc.pdf"}, ()),
        ({"source": 17, "chunks": ["chunk"]}, ()),
    ],
)
def test_only_a_document_with_chunks_is_read(
    data: object, expected: tuple[str, ...]
) -> None:
    assert read_evidence_urls(_tool_step("document_reader", data)) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"matches": [{"content": "Recalled.", "source_url": "https://c.test/m"}]},
            ("https://c.test/m",),
        ),
        (
            {
                "matches": [
                    {
                        "content": "Recalled.",
                        "metadata": {"source_url": "https://c.test/m"},
                    }
                ]
            },
            ("https://c.test/m",),
        ),
        # A match with no readable content was never read.
        ({"matches": [{"source_url": "https://c.test/m"}]}, ()),
        ({"matches": [{"content": "   ", "source_url": "https://c.test/m"}]}, ()),
        # Content with no source is not a source.
        ({"matches": [{"content": "Recalled."}]}, ()),
        ({"matches": []}, ()),
        ({"matches": ["not-a-dict"]}, ()),
        ({"matches": [{"metadata": "not-a-dict"}]}, ()),
    ],
)
def test_only_a_memory_match_with_content_and_a_source_is_read(
    data: object, expected: tuple[str, ...]
) -> None:
    assert read_evidence_urls(_tool_step("query_memory", data)) == expected


def test_search_results_are_discovery_only() -> None:
    """Five search hits are five candidates, and not one byte of evidence."""
    step = _tool_step(
        "web_search",
        {
            "results": [
                {"title": f"Hit {index}", "url": f"https://s.test/{index}"}
                for index in range(5)
            ]
        },
    )

    assert read_evidence_urls(step) == ()


def test_a_write_is_never_read_evidence() -> None:
    step = _tool_step(
        "save_to_memory",
        {"entry_id": "1", "url": "https://d.test/stored"},
    )

    assert read_evidence_urls(step) == ()


def test_an_unknown_tool_is_never_read_evidence() -> None:
    step = _tool_step("echo", {"url": "https://e.test/page", "text": "body"})

    assert read_evidence_urls(step) == ()


@pytest.mark.parametrize(
    "data",
    [{"url": "https://f.test/page", "text": "Read body."}, {"text": None}],
)
def test_a_failed_call_reads_nothing(data: object) -> None:
    assert read_evidence_urls(_tool_step("web_scraper", data, success=False)) == ()


def test_a_step_without_a_tool_result_reads_nothing() -> None:
    step = ReActStep(
        iteration=1,
        thought="Think only.",
        action="finish",
        final_answer="No tool was called.",
    )

    assert read_evidence_urls(step) == ()


def test_read_evidence_urls_normalizes_in_order_and_deduplicates_within_the_step() -> (
    None
):
    step = _tool_step(
        "query_memory",
        {
            "matches": [
                {"content": "One.", "source_url": "HTTPS://A.test/one/"},
                {"content": "One again.", "source_url": "https://a.test/one"},
                {"content": "Two.", "source_url": "https://www.b.test/two?x=1#frag"},
            ]
        },
    )

    assert read_evidence_urls(step) == (
        "https://a.test/one",
        "https://b.test/two?x=1",
    )


def test_a_native_final_answer_becomes_an_internal_finish_decision() -> None:
    decisions = react_decision_from_native_turn(
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            final_answer="The available evidence is sufficient.",
        )
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.action == "finish"
    assert decision.thought == "Finished without another tool call."
    assert decision.tool_input_json == "{}"
    assert decision.final_answer == "The available evidence is sufficient."
    assert decision.tool_name is None
