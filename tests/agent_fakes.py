"""Shared fakes for agent runtime tests.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel

from deep_research.agents.steps import ReActDecision
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)


class EchoTool(BaseTool):
    """Return the ``value`` keyword back to the agent."""

    name = "echo"
    description = "Echo one string back to the agent."
    input_schema = {"value": "string"}
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        return ToolExecution(
            data={"echo": kwargs["value"]},
            output_summary={"echoed": True},
        )


class BoomTool(BaseTool):
    """Always fail with a recoverable tool error."""

    name = "boom"
    description = "Always fail."
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        raise ToolExecutionError("upstream timed out", error_type="TimeoutError")


class StrictEchoTool(BaseTool):
    """Accept exactly one named argument, so bad arguments raise TypeError."""

    name = "strict_echo"
    description = "Echo one required string argument."
    input_schema = {"value": "string"}
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, *, value: str
    ) -> ToolExecution:
        return ToolExecution(data={"echo": value}, output_summary={"echoed": True})


class ScriptedCompleter:
    """Serve queued structured responses instead of calling OpenAI.

    ``ReActDecision`` requests pop from ``decisions``; every other schema pops
    from ``outputs``. A queued ``BaseException`` is raised instead of returned,
    which is how provider failures are simulated.
    """

    def __init__(
        self,
        decisions: Sequence[ReActDecision | BaseException] = (),
        outputs: Sequence[BaseModel | BaseException] = (),
    ) -> None:
        self._decisions: list[Any] = list(decisions)
        self._outputs: list[Any] = list(outputs)
        self.calls: list[tuple[str, str | None, list[ChatMessage]]] = []

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        agent_name: str | None = None,
    ) -> Any:
        self.calls.append((schema.__name__, agent_name, list(messages)))
        queue = self._decisions if schema is ReActDecision else self._outputs
        if not queue:
            raise AssertionError(f"no scripted response left for {schema.__name__}")
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def use_tool(
    thought: str,
    tool_name: str,
    tool_input_json: str = "{}",
) -> ReActDecision:
    return ReActDecision(
        thought=thought,
        action="use_tool",
        tool_name=tool_name,
        tool_input_json=tool_input_json,
    )


def finish(thought: str, final_answer: str) -> ReActDecision:
    return ReActDecision(
        thought=thought,
        action="finish",
        final_answer=final_answer,
        tool_input_json="{}",
    )


@asynccontextmanager
async def agent_scope(
    tracker: Tracker,
    *,
    agent_name: str = "researcher",
    session_id: str = "session-1",
    question: str = "Why is the sky blue?",
) -> AsyncIterator[None]:
    """Open the session and agent spans a ReAct loop requires."""
    async with tracker.session_span(session_id, question):
        async with tracker.agent_span(agent_name):
            yield
