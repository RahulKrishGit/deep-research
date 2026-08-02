"""Tests for the pure prompt rendering boundary."""

from __future__ import annotations

import pytest

from deep_research.agents.prompts import (
    REACT_RESPONSE_CONTRACT,
    AgentTask,
    render_react_messages,
    render_scratchpad,
    render_tool_catalog,
)
from deep_research.agents.toolset import ToolDescriptor
from deep_research.memory.entries import ScratchpadEntry


def _descriptor(name: str = "echo") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"Call {name}.",
        input_schema={"value": "string"},
    )


def _entry(content: str, kind: str = "thought") -> ScratchpadEntry:
    return ScratchpadEntry.model_validate(
        {"agent_name": "researcher", "kind": kind, "content": content}
    )


def test_tool_catalog_lists_name_description_and_arguments() -> None:
    catalog = render_tool_catalog([_descriptor("echo"), _descriptor("boom")])

    assert '- echo: Call echo. Arguments: {"value": "string"}' in catalog
    assert '- boom: Call boom. Arguments: {"value": "string"}' in catalog
    assert catalog.index("echo") < catalog.index("boom")


def test_tool_catalog_says_so_when_no_tool_is_allowed() -> None:
    assert render_tool_catalog([]) == "(no tools available)"


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


def test_response_contract_tells_the_model_to_leave_final_answer_empty_for_tools() -> (
    None
):
    assert "leave final_answer empty" in REACT_RESPONSE_CONTRACT


def test_response_contract_gives_tool_input_json_guidance_for_finish() -> None:
    assert 'tool_input_json when finishing' in REACT_RESPONSE_CONTRACT


def test_react_messages_open_with_the_agent_system_prompt() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        descriptors=[_descriptor()],
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )

    assert len(messages) == 2
    assert messages[0].role == "developer"
    assert messages[0].content == "You are a researcher."
    assert messages[1].role == "user"


def test_react_messages_carry_task_tools_notes_and_the_iteration_budget() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        descriptors=[_descriptor()],
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )
    body = messages[1].content

    assert "Summarize QEC progress." in body
    assert "Prefer 2025 sources." in body
    assert "- echo: Call echo." in body
    assert "- [thought] Search for benchmarks." in body
    assert "Iteration 2 of 3." in body
    assert "tool_input_json" in body


def test_react_messages_omit_the_guidance_section_when_it_is_blank() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        descriptors=[],
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
                descriptors=[_descriptor()],
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
            descriptors=[],
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
        descriptors=[_descriptor()],
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )

    assert messages[1].content == (
        "## Task\n"
        "Summarize QEC progress.\n\n"
        "## Guidance\n"
        "Prefer 2025 sources.\n\n"
        "## Tools\n"
        '- echo: Call echo. Arguments: {"value": "string"}\n\n'
        "## Notes so far\n"
        "- [thought] Search for benchmarks.\n\n"
        "## Budget\n"
        "Iteration 2 of 3.\n\n"
        "## Response contract\n"
        f"{REACT_RESPONSE_CONTRACT}"
    )


def test_react_messages_reject_a_blank_system_prompt() -> None:
    with pytest.raises(ValueError, match="system_prompt must not be blank"):
        render_react_messages(
            system_prompt="   ",
            task=AgentTask(instruction="Summarize QEC progress."),
            descriptors=[],
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
