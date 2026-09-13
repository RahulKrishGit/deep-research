"""Typed ReAct step records and the pure helpers that build them.

``ReActDecision`` is internal loop state, not a provider structured-output
schema: the provider boundary returns a ``NativeToolTurn`` and
``react_decision_from_native_turn`` adapts it here. A native turn may carry
several tool calls, so that adapter returns one decision per call. Tool
arguments still travel as ``tool_input_json`` — a JSON-encoded object — because
that is the decoding contract ``parse_tool_input`` enforces.
"""

from __future__ import annotations

import json
from typing import Literal, NoReturn, TypeAlias

from pydantic import Field, JsonValue, model_validator

from deep_research.providers import NativeToolTurn
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    ContractModel,
    ResearchError,
    _FiniteJsonValue,
    _validate_finite_json,
)

StopReason: TypeAlias = Literal[
    "finished",
    "sufficient",
    "max_iterations",
    "tool_budget_exhausted",
    "provider_error",
]
ReActActionType: TypeAlias = Literal["use_tool", "finish"]

DEFAULT_SUMMARY_LIMIT = 200
_ELLIPSIS = "..."


def summarize_text(text: str, *, limit: int = DEFAULT_SUMMARY_LIMIT) -> str:
    """Collapse whitespace and clamp ``text`` to ``limit`` characters.

    Summaries land in prompts and in span outputs, so they must be short,
    single-line, and never empty.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    collapsed = " ".join(text.split())
    if not collapsed:
        return "(empty)"
    if len(collapsed) <= limit:
        return collapsed
    if limit <= len(_ELLIPSIS):
        return _ELLIPSIS[:limit]
    return collapsed[: limit - len(_ELLIPSIS)].rstrip() + _ELLIPSIS


def _reject_json_constant(name: str) -> NoReturn:
    raise ValueError(f"tool arguments must be finite JSON numbers, got {name}")


def parse_tool_input(raw: str) -> dict[str, JsonValue]:
    """Decode a model-supplied JSON argument object.

    Raises ``ValueError`` for anything the runtime cannot forward to
    ``BaseTool.execute(**kwargs)``. Callers treat that as an invalid action,
    not as a crash.
    """
    candidate = raw.strip() or "{}"
    try:
        parsed = json.loads(candidate, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as error:
        raise ValueError("tool_input_json must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("tool_input_json must decode to a JSON object")
    try:
        _validate_finite_json(parsed)
    except ValueError as error:
        raise ValueError("tool arguments must be finite JSON numbers") from error
    for key in parsed:
        if key == "self":
            raise ValueError(
                "tool arguments must not use the reserved key 'self'"
            )
        if not key.isidentifier():
            raise ValueError(
                f"tool argument key {key!r} must be a valid Python identifier"
            )
    return parsed


class ReActDecision(ContractModel):
    """Internal loop state for one think/choose-action turn."""

    thought: str = Field(min_length=1)
    action: ReActActionType
    tool_name: str | None = None
    tool_input_json: str
    final_answer: str | None = None

    @model_validator(mode="after")
    def validate_action_shape(self) -> "ReActDecision":
        if self.action == "use_tool":
            if not self.tool_name:
                raise ValueError("use_tool decisions require tool_name")
            if self.final_answer:
                raise ValueError("use_tool decisions must not carry final_answer")
        else:
            if not self.final_answer:
                raise ValueError("finish decisions require final_answer")
            if self.tool_name:
                raise ValueError("finish decisions must not name a tool")
        return self


def react_decision_from_native_turn(
    turn: NativeToolTurn,
) -> tuple[ReActDecision, ...]:
    """Adapt one provider-native turn into the loop's internal decisions.

    A turn is plural by contract: the provider may select several tools at once
    and each call becomes its own ``use_tool`` decision, in the order the
    provider gave them. Nothing is sorted, deduped, or dropped here — deciding
    what to do with a batch belongs to ``run_react_loop``, which owns the tool
    budget and can report what it did not execute. A final answer is the
    degenerate one-decision case.

    ``thought`` is a short deterministic system summary, never provider
    reasoning: that keeps the step and scratchpad schemas intact without
    fabricating chain-of-thought, and it is the same text for every run of
    the same shape.
    """
    if turn.tool_calls:
        return tuple(
            ReActDecision(
                thought="Selected tool through provider-native calling.",
                action="use_tool",
                tool_name=call.tool_name,
                tool_input_json=call.arguments_json,
            )
            for call in turn.tool_calls
        )
    return (
        ReActDecision(
            thought="Finished without another tool call.",
            action="finish",
            final_answer=turn.final_answer,
            tool_input_json="{}",
        ),
    )


class ReActObservation(ContractModel):
    """What the agent learned from one tool call, as fed back to the model."""

    tool_name: str = Field(min_length=1)
    success: bool
    summary: str = Field(min_length=1)
    latency_ms: float = Field(default=0.0, ge=0.0)
    error_type: str | None = Field(default=None, min_length=1)


class ReActStep(ContractModel):
    """One completed think -> act -> observe cycle."""

    iteration: int = Field(ge=1)
    thought: str = Field(min_length=1)
    action: ReActActionType
    tool_name: str | None = Field(default=None, min_length=1)
    tool_input: dict[str, _FiniteJsonValue] = Field(default_factory=dict)
    observation: ReActObservation | None = None
    tool_result: ToolResult | None = None
    final_answer: str | None = Field(default=None, min_length=1)


class ReActRun(ContractModel):
    """The outcome of one bounded ReAct loop."""

    agent_name: str = Field(min_length=1)
    steps: list[ReActStep] = Field(default_factory=list)
    stop_reason: StopReason
    iterations: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    final_answer: str | None = Field(default=None, min_length=1)
    errors: list[ResearchError] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """True unless the loop stopped on a non-recoverable provider failure."""
        return self.stop_reason != "provider_error"
