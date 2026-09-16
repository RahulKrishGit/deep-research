"""The one shared provider-text shape detector.

The detector reads provider text and returns only a bounded literal. These
tests pin both halves of that contract: every prohibited shape is named, and
nothing the detector read can travel back out with the name.
"""

from __future__ import annotations

from typing import get_args

import pytest

from deep_research.providers.native_output import (
    NativeTextViolation,
    native_text_violation,
)

SENTINEL = "NATIVE_OUTPUT_SENTINEL_9F42"

VIOLATIONS: frozenset[str] = frozenset(
    {
        "dsml_markup",
        "tool_markup",
        "markdown_fence",
        "legacy_action_json",
    }
)


def test_the_violation_vocabulary_is_closed() -> None:
    """A consumer may switch on the result, so the set must be finite."""
    assert set(get_args(NativeTextViolation)) == VIOLATIONS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            '<|DSML|tool_calls><|DSML|invoke name="web_search">'
            '{"query":"qec"}</|DSML|invoke></|DSML|tool_calls>',
            "dsml_markup",
            id="deepseek-dsml-envelope",
        ),
        pytest.param(
            "  <|DsMl|tool_calls>  ",
            "dsml_markup",
            id="dsml-mixed-case",
        ),
        pytest.param("<|dsml|invoke>", "dsml_markup", id="dsml-lower-case"),
        pytest.param(
            '<tool_call>{"name": "web_search"}</tool_call>',
            "tool_markup",
            id="tool-call-tag",
        ),
        pytest.param("<TOOL_CALL>", "tool_markup", id="tool-call-upper-case"),
        pytest.param(
            '<invoke name="web_search">{"query": "qec"}</invoke>',
            "tool_markup",
            id="invoke-tag",
        ),
        pytest.param("<INVOKE>", "tool_markup", id="invoke-upper-case"),
        pytest.param("```json\n{}\n```", "markdown_fence", id="fenced-json"),
        pytest.param("the plan follows\n```\n", "markdown_fence", id="bare-fence"),
        pytest.param(
            '{"action": "use_tool"}',
            "legacy_action_json",
            id="legacy-action-key",
        ),
        pytest.param(
            '{"tool_name": "web_search"}',
            "legacy_action_json",
            id="legacy-tool-name-key",
        ),
        pytest.param(
            '{"tool_input_json": "{}"}',
            "legacy_action_json",
            id="legacy-tool-input-key",
        ),
        pytest.param(
            '  {"action": "finish", "final_answer": "done"}  ',
            "legacy_action_json",
            id="legacy-padded-object",
        ),
    ],
)
def test_every_prohibited_shape_is_named(text: str, expected: str) -> None:
    assert native_text_violation(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "The report answers the question from cited sources.",
            id="ordinary-prose",
        ),
        pytest.param("", id="empty"),
        pytest.param("   \n\t ", id="blank"),
        pytest.param(
            "The set {a, b} has two members.",
            id="prose-with-harmless-braces",
        ),
        pytest.param(
            "Prose that ends with an unbalanced brace {",
            id="prose-with-unbalanced-brace",
        ),
        pytest.param(
            '{"score": 8, "gaps": []}',
            id="json-without-a-legacy-key",
        ),
        pytest.param('["action"]', id="json-list"),
        pytest.param('"action"', id="json-string"),
        pytest.param("42", id="json-number"),
        pytest.param("null", id="json-null"),
        pytest.param(
            "{'action': 'use_tool'}",
            id="single-quoted-object-is-not-json",
        ),
        pytest.param(
            "See https://example.test/qec for the measured rate.",
            id="url",
        ),
    ],
)
def test_legitimate_final_text_is_not_a_violation(text: str) -> None:
    assert native_text_violation(text) is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            f'{{"action": "{SENTINEL}", "tool_name": "{SENTINEL}"}}',
            id="legacy-action-json",
        ),
        pytest.param(
            f"```json\n{SENTINEL}\n```",
            id="markdown-fence",
        ),
        pytest.param(
            f'<tool_call>{{"query": "{SENTINEL}"}}</tool_call>',
            id="tool-markup",
        ),
        pytest.param(
            f'<|DSML|tool_calls><|DSML|invoke name="{SENTINEL}">',
            id="dsml-markup",
        ),
    ],
)
def test_the_result_carries_no_provider_text(text: str) -> None:
    """A rejection may name the shape, never the content it read."""
    result = native_text_violation(text)
    assert result in VIOLATIONS
    assert SENTINEL not in result
