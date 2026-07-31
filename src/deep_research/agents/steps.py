"""Typed ReAct step records and the pure helpers that build them.

``ReActDecision`` doubles as the OpenAI structured-output schema for the
think/choose-action turn, so every field must survive strict JSON schema
conversion. That is why tool arguments travel as ``tool_input_json`` — a
JSON-encoded object — rather than as an open ``dict``, which strict mode
rejects.
"""

from __future__ import annotations

import json
from typing import Literal, NoReturn, TypeAlias

from pydantic import Field, JsonValue, model_validator

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
    return parsed


class ReActDecision(ContractModel):
    """One think/choose-action turn, as returned by the provider."""

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
