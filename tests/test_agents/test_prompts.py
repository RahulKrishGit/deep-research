"""Tests for the pure prompt rendering boundary."""

from __future__ import annotations

import pytest

from deep_research.agents.prompts import (
    NATIVE_REACT_RESPONSE_CONTRACT,
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    STRUCTURED_EXAMPLE_NOTICE,
    STRUCTURED_REPLY_FORMAT,
    AgentTask,
    render_react_messages,
    render_scratchpad,
    render_source_dossier,
    render_structured_reply_format,
)
from deep_research.agents.sources import SourceGroup
from deep_research.memory.entries import ScratchpadEntry
from deep_research.utils.types import Finding


def _entry(content: str, kind: str = "thought") -> ScratchpadEntry:
    return ScratchpadEntry.model_validate(
        {"agent_name": "researcher", "kind": kind, "content": content}
    )


def test_scratchpad_renders_kind_prefixed_lines_oldest_first() -> None:
    rendered = render_scratchpad(
        [_entry("Search for benchmarks."), _entry("Found 5 results.", "observation")]
    )

    assert rendered == (
        "- [thought] Search for benchmarks.\n- [observation] Found 5 results."
    )


def test_scratchpad_says_so_when_empty() -> None:
    assert render_scratchpad([]) == "(no notes yet)"


def test_scratchpad_collapses_multiline_entry_content_onto_one_line() -> None:
    rendered = render_scratchpad(
        [
            _entry(
                "## Summary\nFirst I need X.\n\nThen Y.",
                kind="summary",
            )
        ]
    )

    assert rendered == "- [summary] ## Summary First I need X. Then Y."
    assert "\n" not in rendered.split("] ", 1)[1]


def test_the_native_contract_asks_for_provider_native_tool_calling() -> None:
    assert "provider-native tool calling" in NATIVE_REACT_RESPONSE_CONTRACT
    assert (
        "never write or imitate a tool call in text, JSON, XML, DSML, or a "
        "Markdown fence" in NATIVE_REACT_RESPONSE_CONTRACT
    )
    assert (
        "return the final answer directly" in NATIVE_REACT_RESPONSE_CONTRACT
    )


def test_the_native_contract_asks_for_one_or_more_tools() -> None:
    """The transport accepts several calls per turn, so the text must too.

    ``_native_response_outcome`` accepts every ``function_call`` item in one
    response, so a contract capping the model at one call throws away
    lookups the transport would have executed.
    """
    assert "one or more tools" in NATIVE_REACT_RESPONSE_CONTRACT
    assert "at most one" not in NATIVE_REACT_RESPONSE_CONTRACT
    assert (
        "never write or imitate a tool call in text, JSON, XML, DSML, or a "
        "Markdown fence" in NATIVE_REACT_RESPONSE_CONTRACT
    )


def test_the_native_contract_never_mentions_the_simulated_protocol() -> None:
    for forbidden in (
        "## Tools",
        "tool_input_json",
        "tool_name",
        "ReActDecision",
        "leave final_answer empty",
    ):
        assert forbidden not in NATIVE_REACT_RESPONSE_CONTRACT


def test_react_messages_open_with_the_agent_system_prompt() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )

    assert len(messages) == 2
    assert messages[0].role == "developer"
    assert messages[0].content == "You are a researcher."
    assert messages[1].role == "user"


def test_react_messages_carry_task_notes_and_the_iteration_budget() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )
    body = messages[1].content

    assert "Summarize QEC progress." in body
    assert "Prefer 2025 sources." in body
    assert "- [thought] Search for benchmarks." in body
    assert "Iteration 2 of 3." in body
    assert NATIVE_REACT_RESPONSE_CONTRACT in body


def test_react_messages_advertise_no_tool_catalogue_and_no_action_envelope() -> None:
    """The tools ride on the request itself, so the text must name none."""
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )
    body = messages[1].content

    for forbidden in (
        "## Tools",
        "tool_input_json",
        "tool_name",
        "ReActDecision",
        "Arguments:",
        "no tools available",
    ):
        assert forbidden not in body


def test_react_messages_omit_the_guidance_section_when_it_is_blank() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=1,
    )

    assert "## Guidance" not in messages[1].content


def test_react_messages_are_deterministic() -> None:
    def _render() -> list[str]:
        return [
            message.content
            for message in render_react_messages(
                system_prompt="You are a researcher.",
                task=AgentTask(instruction="Summarize QEC progress."),
                scratchpad=[_entry("note")],
                iteration=1,
                max_iterations=3,
            )
        ]

    assert _render() == _render()


@pytest.mark.parametrize(
    ("iteration", "max_iterations", "match"),
    [
        (0, 3, r"^iteration must be at least 1$"),
        (4, 3, "iteration must not exceed max_iterations"),
        (1, 0, "max_iterations must be at least 1"),
    ],
)
def test_react_messages_reject_an_impossible_iteration_budget(
    iteration: int, max_iterations: int, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        render_react_messages(
            system_prompt="You are a researcher.",
            task=AgentTask(instruction="Summarize QEC progress."),
            scratchpad=[],
            iteration=iteration,
            max_iterations=max_iterations,
        )


def test_react_messages_render_the_full_body_verbatim() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )

    assert messages[1].content == (
        "## Task\n"
        "Summarize QEC progress.\n\n"
        "## Guidance\n"
        "Prefer 2025 sources.\n\n"
        "## Notes so far\n"
        "- [thought] Search for benchmarks.\n\n"
        "## Budget\n"
        "Iteration 2 of 3.\n\n"
        "## How to respond\n"
        f"{NATIVE_REACT_RESPONSE_CONTRACT}"
    )


def test_react_messages_reject_a_blank_system_prompt() -> None:
    with pytest.raises(ValueError, match="system_prompt must not be blank"):
        render_react_messages(
            system_prompt="   ",
            task=AgentTask(instruction="Summarize QEC progress."),
            scratchpad=[],
            iteration=1,
            max_iterations=1,
        )


def test_memory_guidance_lists_recalled_findings_and_strategies() -> None:
    from deep_research.agents.prompts import render_memory_guidance
    from deep_research.utils.types import Finding, MemorySnapshot

    guidance = render_memory_guidance(
        MemorySnapshot(
            similar_findings=[
                Finding(
                    content="Logical error rates fell below break-even.",
                    source_url="https://example.test/qec",
                    source_title="QEC 2025",
                    extracted_at="2026-01-01T00:00:00+00:00",
                    confidence=0.8,
                    related_sub_topic="Error correction",
                )
            ],
            suggested_strategies=["Prefer peer-reviewed sources."],
        )
    )

    assert "1 finding(s) recalled from previous sessions:" in guidance
    assert "Logical error rates fell below break-even." in guidance
    assert "https://example.test/qec" in guidance
    assert "Strategies that worked before:" in guidance
    assert "- Prefer peer-reviewed sources." in guidance


def test_memory_guidance_is_empty_when_nothing_was_recalled() -> None:
    from deep_research.agents.prompts import render_memory_guidance
    from deep_research.utils.types import MemorySnapshot

    assert render_memory_guidance(MemorySnapshot()) == ""


PROMPT_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _prompt_finding(
    *,
    content: str = "Logical error rates fell below break-even.",
    url: str = "https://example.org/a",
    source_title: str = "QEC 2025",
    sub_topic: str = "Alpha",
    attributed_issuer: str | None = None,
    attribution_quote: str | None = None,
    measure_scope: str | None = None,
    vintage: str | None = None,
    release_date: str | None = None,
    data_period: str | None = None,
    statement_date: str | None = None,
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title=source_title,
        extracted_at=PROMPT_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
        attributed_issuer=attributed_issuer,
        attribution_quote=attribution_quote,
        measure_scope=measure_scope,
        vintage=vintage,
        release_date=release_date,
        data_period=data_period,
        statement_date=statement_date,
    )


def test_source_dossier_renders_every_scoring_input() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="QEC 2025",
        sub_topics=["Alpha"],
        findings=[_prompt_finding()],
    )

    rendered = render_source_dossier(group, index=2, reputation=0.9)

    assert "Source 2: https://example.org/a" in rendered
    assert "Title: QEC 2025" in rendered
    assert "Cited for: Alpha" in rendered
    assert "Corroboration" not in rendered
    assert "Known reputation: 0.90" in rendered
    assert "Logical error rates fell below break-even." in rendered


def test_source_dossier_says_so_when_no_reputation_is_known() -> None:
    group = SourceGroup(
        url="https://example.org/a", domain="example.org", title="A"
    )

    rendered = render_source_dossier(group, index=1, reputation=None)

    assert "Known reputation: none on record" in rendered
    assert "(no findings)" in rendered


def test_source_dossier_clamps_long_finding_text() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="A",
        sub_topics=["Alpha"],
        findings=[_prompt_finding(content="x" * 500)],
    )

    rendered = render_source_dossier(
        group, index=1, reputation=None, excerpt_chars=50
    )

    assert "x" * 500 not in rendered
    assert "..." in rendered


def test_new_prompt_constants_state_their_contracts() -> None:
    # The scoring call must never be asked for a combined score: this
    # project computes overall_score from the three source dimensions.
    assert "overall" not in SOURCE_SCORING_INSTRUCTION
    assert "authority" in SOURCE_SCORING_INSTRUCTION
    assert "between 0 and 1" in SOURCE_SCORING_INSTRUCTION
    assert "exact url" in SOURCE_EVALUATOR_SYSTEM_PROMPT


def test_the_source_evaluator_keeps_a_written_date_at_its_own_precision() -> None:
    """A dateline written in words dates the document by that day.

    The instruction used to ask for "the year, for 'January 15, 2026'",
    because the value had to appear in the quote. Every EIA page in the
    audited run dates itself that way, so two releases of one series were
    recorded as the same "2026" and could not be ranked — the reduction is
    what the instruction must not ask for, and the reader now accepts the
    full date a spelled quote states.
    """
    instruction = SOURCE_SCORING_INSTRUCTION

    assert "return the full date the words name" in instruction
    assert "the year, for" not in instruction
    # The value still has to be one the quote states: precision is the
    # document's, never the model's.
    assert "the date the words name and no finer one" in instruction


# --- the shared structured reply format --------------------------------------

def test_the_shared_reply_format_names_json_and_forbids_a_fence() -> None:
    assert "JSON object" in STRUCTURED_REPLY_FORMAT
    assert "no Markdown fence" in STRUCTURED_REPLY_FORMAT


def test_render_structured_reply_format_normalizes_each_example() -> None:
    rendered = render_structured_reply_format(
        (
            (
                "Example input: an isolated case.",
                '{ "b": 2,\n "a": 1 }',
            ),
        )
    )

    assert STRUCTURED_REPLY_FORMAT in rendered
    assert STRUCTURED_EXAMPLE_NOTICE in rendered
    assert "Example input: an isolated case.\nExample JSON output:\n" in rendered
    # Compact, sorted, single-line, and never fenced.
    assert '{"a":1,"b":2}' in rendered
    assert "```" not in rendered


def test_render_structured_reply_format_accepts_two_examples() -> None:
    rendered = render_structured_reply_format(
        (
            ("Weak example input: a.", '{"score":1}'),
            ("Strong example input: b.", '{"score":9}'),
        )
    )

    assert rendered.count("Example JSON output:") == 2


@pytest.mark.parametrize(
    "examples",
    [
        pytest.param((), id="zero-examples"),
        pytest.param(
            (
                ("One.", '{"a":1}'),
                ("Two.", '{"a":2}'),
                ("Three.", '{"a":3}'),
            ),
            id="three-examples",
        ),
        pytest.param((("Example input: x.", "{not json}"),), id="malformed-json"),
        pytest.param((("Example input: x.", "[1, 2]"),), id="array-payload"),
        pytest.param((("Example input: x.", '"scalar"'),), id="scalar-payload"),
        pytest.param((("   ", '{"a":1}'),), id="blank-label"),
        pytest.param((("Line one\nLine two", '{"a":1}'),), id="multi-line-label"),
    ],
)
def test_render_structured_reply_format_fails_closed(examples) -> None:
    with pytest.raises(ValueError):
        render_structured_reply_format(examples)
