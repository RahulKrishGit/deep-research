"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.toolset import ToolDescriptor
from deep_research.memory.entries import ScratchpadEntry
from deep_research.providers import ChatMessage
from deep_research.utils.types import ContractModel

REACT_RESPONSE_CONTRACT = (
    "Respond with one decision.\n"
    'Set action to "use_tool" to call exactly one listed tool: put its name in '
    "tool_name and its arguments in tool_input_json as a JSON object string "
    '(for example {"value": "hello"}). Use "{}" when the tool takes no '
    "arguments.\n"
    'Set action to "finish" when you can answer without another tool call: put '
    "the answer in final_answer and leave tool_name empty.\n"
    "Always explain the choice in thought."
)


class AgentTask(ContractModel):
    """What one agent has been asked to do on this run."""

    instruction: str = Field(min_length=1)
    guidance: str = ""


def render_tool_catalog(descriptors: Sequence[ToolDescriptor]) -> str:
    """Render the allowed tools as one line each, in declaration order."""
    if not descriptors:
        return "(no tools available)"
    return "\n".join(
        f"- {descriptor.name}: {descriptor.description} "
        f"Arguments: {json.dumps(descriptor.input_schema, sort_keys=True)}"
        for descriptor in descriptors
    )


def render_scratchpad(entries: Sequence[ScratchpadEntry]) -> str:
    """Render scratchpad notes oldest first, one kind-prefixed line each."""
    if not entries:
        return "(no notes yet)"
    return "\n".join(f"- [{entry.kind}] {entry.content}" for entry in entries)


def render_react_messages(
    *,
    system_prompt: str,
    task: AgentTask,
    descriptors: Sequence[ToolDescriptor],
    scratchpad: Sequence[ScratchpadEntry],
    iteration: int,
    max_iterations: int,
) -> list[ChatMessage]:
    """Build the two messages one ReAct turn sends to the provider."""
    if not system_prompt.strip():
        raise ValueError("system_prompt must not be blank")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if iteration < 1:
        raise ValueError("iteration must be at least 1")
    if iteration > max_iterations:
        raise ValueError("iteration must not exceed max_iterations")

    sections = [f"## Task\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Guidance\n{task.guidance}")
    sections.append(f"## Tools\n{render_tool_catalog(descriptors)}")
    sections.append(f"## Notes so far\n{render_scratchpad(scratchpad)}")
    sections.append(f"## Budget\nIteration {iteration} of {max_iterations}.")
    sections.append(f"## Response contract\n{REACT_RESPONSE_CONTRACT}")

    return [
        ChatMessage(role="developer", content=system_prompt),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
