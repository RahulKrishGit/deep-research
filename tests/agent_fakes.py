"""Shared fakes for agent runtime tests.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from deep_research.agents.steps import ReActDecision
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ChatMessage,
    NativeToolCall,
    NativeToolTurn,
    ToolDefinition,
)
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
    required_arguments = ("value",)
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
    required_arguments = ("value",)
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, *, value: str
    ) -> ToolExecution:
        return ToolExecution(data={"echo": value}, output_summary={"echoed": True})


@dataclass(frozen=True, slots=True)
class ReactCall:
    """One recorded native ReAct request."""

    agent_name: str | None
    messages: list[ChatMessage]
    tools: tuple[ToolDefinition, ...]
    max_tokens: int | None


def native_turn_from_decision(decision: ReActDecision) -> NativeToolTurn:
    """The provider-native turn a real provider would return for a decision.

    Shared by every fake that scripts ReAct turns, so the conversion exists
    once and both the agent and evaluation doubles answer with the same shape.
    """
    if decision.action == "use_tool":
        return NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            tool_calls=(
                NativeToolCall(
                    tool_name=decision.tool_name,
                    arguments_json=decision.tool_input_json,
                ),
            ),
        )
    return NativeToolTurn(
        model="deepseek-v4-flash",
        usage=TokenUsage(),
        final_answer=decision.final_answer,
    )


class ScriptedCompleter:
    """Serve queued responses instead of calling a provider.

    ``complete_react`` pops one scripted ``ReActDecision`` and returns the
    provider-native turn a real provider would return for it.
    ``complete_structured`` serves every other schema from ``outputs`` and
    refuses ``ReActDecision`` outright, so a production agent that went back to
    the prompt-encoded tool protocol fails loudly here. A queued
    ``BaseException`` is raised instead of returned, which is how provider
    failures are simulated. A queued callable is called with the request's
    messages and the requested schema, for a reply that has to read the request
    it is answering.
    """

    def __init__(
        self,
        decisions: Sequence[ReActDecision | BaseException] = (),
        outputs: Sequence[BaseModel | BaseException] = (),
    ) -> None:
        self._decisions: list[Any] = list(decisions)
        self._outputs: list[Any] = list(outputs)
        self.calls: list[tuple[str, str | None, list[ChatMessage]]] = []
        self.budgets: list[int | None] = []
        self.react_calls: list[ReactCall] = []
        self.react_budgets: list[int | None] = []

    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        self.react_calls.append(
            ReactCall(
                agent_name=agent_name,
                messages=list(messages),
                tools=tuple(tools),
                max_tokens=max_tokens,
            )
        )
        self.react_budgets.append(max_tokens)
        if not self._decisions:
            raise AssertionError("no scripted decision left for a native ReAct turn")
        decision = self._decisions.pop(0)
        if isinstance(decision, BaseException):
            raise decision
        return native_turn_from_decision(decision)

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        # Recorded *before* the refusal on purpose: a guard test asserting
        # that no call ever names ``ReActDecision`` can only carry weight if a
        # refused attempt is visible in ``calls``.
        self.calls.append((schema.__name__, agent_name, list(messages)))
        self.budgets.append(max_tokens)
        if schema is ReActDecision:
            raise AssertionError(
                "ReAct decisions must be requested through complete_react"
            )
        if not self._outputs:
            raise AssertionError(f"no scripted response left for {schema.__name__}")
        response = self._outputs.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            # A factory, for a reply whose content depends on the request: a
            # model that selects the evidence ids it was actually shown cannot
            # be scripted as a fixed object, because the caller does not know
            # those ids before the request is built.
            return response(list(messages), schema)
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
