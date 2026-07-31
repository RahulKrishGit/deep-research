"""Tests for the pure prompt rendering boundary."""

from __future__ import annotations

import pytest

from deep_research.agents.prompts import (
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
        (0, 3, "iteration must be at least 1"),
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
