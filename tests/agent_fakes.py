"""Shared fakes for agent runtime tests.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Mapping, Sequence
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
        self.efforts: list[str | None] = []
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
        reasoning_effort: str | None = None,
    ) -> Any:
        # Recorded *before* the refusal on purpose: a guard test asserting
        # that no call ever names ``ReActDecision`` can only carry weight if a
        # refused attempt is visible in ``calls``.
        self.calls.append((schema.__name__, agent_name, list(messages)))
        self.budgets.append(max_tokens)
        self.efforts.append(reasoning_effort)
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


_TARGET_ID_LINE = re.compile(r"^- target_id=(\S+)$", re.MULTILINE)
_SUB_TOPIC_LINE = re.compile(r"^# Sub-topic\n(.+)$", re.MULTILINE)


def _request_text(messages: Sequence[ChatMessage]) -> str:
    return "\n".join(message.content for message in messages)


def request_target_id(messages: Sequence[ChatMessage]) -> str | None:
    """The target a ReAct packet is for, read out of its own ``target_id=`` line.

    The same field ``e2e_evaluation/replay.py``'s ``_researcher_turn`` reads to
    pick a topic's next action, so a double and the replay harness answer the
    same packet the same way.
    """
    match = _TARGET_ID_LINE.search(_request_text(messages))
    if match is None or match.group(1) == "-":
        return None
    return match.group(1)


def request_sub_topic(messages: Sequence[ChatMessage]) -> str | None:
    """The sub-topic an extraction request is about, from its title line."""
    match = _SUB_TOPIC_LINE.search(_request_text(messages))
    return None if match is None else match.group(1).strip()


@dataclass(slots=True)
class KeyedCall:
    """One request this double answered, with the key it was dispatched under."""

    key: str
    schema: str
    agent_name: str | None
    messages: list[ChatMessage]


class TargetKeyedCompleter:
    """Serve each scripted reply to the loop that asked for it, not in call order.

    ``ScriptedCompleter`` serves one queue in the order requests arrive, which
    is only right while exactly one loop runs: as soon as a loop's tool
    suspends, the next request comes from a different loop and the queued
    decision lands in the wrong one. This double keys every script by the
    sub-topic the request is about — the ``target_id=`` line of a ReAct
    packet, and the ``# Sub-topic`` title line of an extraction request. A
    request for a key nothing was scripted for fails the test outright,
    because silently handing it the next queued reply is the bug this double
    exists to catch.

    ``decisions`` maps a target id (``topic-01``) to the ReAct decisions that
    loop receives, in order; ``outputs`` maps a sub-topic title to the
    structured replies its extraction receives, in order. An entry may be a
    ``BaseException`` to raise it, or a callable called with the request's
    messages and schema — the same three forms ``ScriptedCompleter`` serves.
    ``delays`` maps a key to seconds this double sleeps before answering, so a
    test can decide which loop finishes last without touching the event loop's
    own scheduling.
    """

    def __init__(
        self,
        *,
        decisions: Mapping[str, Sequence[Any]] | None = None,
        outputs: Mapping[str, Sequence[Any]] | None = None,
        delays: Mapping[str, float] | None = None,
    ) -> None:
        self._decisions = {
            key: list(script) for key, script in (decisions or {}).items()
        }
        self._outputs = {
            key: list(script) for key, script in (outputs or {}).items()
        }
        self._delays = dict(delays or {})
        self.calls: list[KeyedCall] = []
        self.react_calls: list[KeyedCall] = []
        # The order each key's ReAct script ran out, which is the loop's own
        # completion order: a loop with no decision left asks for none.
        self.exhausted: list[str] = []
        self._in_flight = 0
        self.max_in_flight = 0

    def packets(self, key: str) -> list[str]:
        """Every ReAct request body ``key`` received, in order."""
        return [
            _request_text(call.messages)
            for call in self.react_calls
            if call.key == key
        ]

    def structured_requests(self, key: str) -> list[str]:
        """Every structured request body ``key`` received, in order."""
        return [
            _request_text(call.messages)
            for call in self.calls
            if call.key == key
        ]

    def remaining(self, key: str) -> int:
        """How many scripted ReAct decisions ``key`` never received."""
        return len(self._decisions.get(key, ()))

    async def _delay_for(self, key: str) -> None:
        delay = self._delays.get(key)
        if delay:
            await asyncio.sleep(delay)

    def _next(self, scripts: dict[str, list[Any]], key: str | None, *, kind: str) -> Any:
        if key is None:
            raise AssertionError(
                f"a {kind} request named no sub-topic this double could key on"
            )
        script = scripts.get(key)
        if script is None:
            raise AssertionError(
                f"no loop asked for {key!r} in a {kind} request"
            )
        if not script:
            raise AssertionError(
                f"{key!r} has no scripted {kind} reply left"
            )
        value = script.pop(0)
        if kind == "ReAct" and not script and key not in self.exhausted:
            self.exhausted.append(key)
        return value

    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        key = request_target_id(messages)
        self.react_calls.append(
            KeyedCall(
                key=key or "",
                schema="ReactDecision",
                agent_name=agent_name,
                messages=list(messages),
            )
        )
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            # A real model call suspends; a double that never yields would make
            # concurrent loops look serial whatever the loop does, so the
            # in-flight peak here could not observe a cap at all.
            await asyncio.sleep(0)
            if key is not None:
                await self._delay_for(key)
            decision = self._next(self._decisions, key, kind="ReAct")
            if isinstance(decision, BaseException):
                raise decision
            if isinstance(decision, NativeToolTurn):
                return decision
            return native_turn_from_decision(decision)
        finally:
            self._in_flight -= 1

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        del max_tokens, reasoning_effort
        key = request_sub_topic(messages)
        self.calls.append(
            KeyedCall(
                key=key or "",
                schema=schema.__name__,
                agent_name=agent_name,
                messages=list(messages),
            )
        )
        if schema is ReActDecision:
            raise AssertionError(
                "ReAct decisions must be requested through complete_react"
            )
        if key is not None:
            await self._delay_for(key)
        reply = self._next(self._outputs, key, kind="structured")
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(list(messages), schema)
        return reply


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
