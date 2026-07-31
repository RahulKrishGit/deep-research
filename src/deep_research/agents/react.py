"""The bounded ReAct loop every agent runs.

The loop takes callbacks rather than an agent, so it can be exercised with
plain functions and fakes. Its caller owns the ``agent_span``; the loop owns
one ``react_iteration_span`` per turn.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeAlias

from pydantic import JsonValue

from deep_research.agents.errors import agent_error
from deep_research.agents.steps import (
    DEFAULT_SUMMARY_LIMIT,
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    StopReason,
    parse_tool_input,
    summarize_text,
)
from deep_research.agents.toolset import AgentToolset
from deep_research.observability import Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import ResearchError

DecideCallback: TypeAlias = Callable[
    [int, Sequence[ReActStep]], Awaitable[ReActDecision]
]
StepCallback: TypeAlias = Callable[[ReActStep], Awaitable[None]]
SufficiencyCallback: TypeAlias = Callable[[Sequence[ReActStep]], bool]


def _tool_observation(result: ToolResult, *, limit: int) -> ReActObservation:
    """Turn a tool outcome into text the model can act on."""
    error = result.error
    if error is None:
        payload = json.dumps(result.data, default=str, ensure_ascii=False)
        summary = f"{result.tool_name} succeeded: {payload}"
    else:
        summary = f"{result.tool_name} failed ({error.type}): {error.message}"
    return ReActObservation(
        tool_name=result.tool_name,
        success=result.success,
        summary=summarize_text(summary, limit=limit),
        latency_ms=result.latency_ms,
        error_type=None if error is None else error.type,
    )


async def run_react_loop(
    *,
    agent_name: str,
    tracker: Tracker,
    tools: AgentToolset,
    decide: DecideCallback,
    max_iterations: int,
    tool_budget: int,
    on_step: StepCallback | None = None,
    is_sufficient: SufficiencyCallback | None = None,
    summary_limit: int = DEFAULT_SUMMARY_LIMIT,
) -> ReActRun:
    """Run think -> act -> observe until a stop condition fires.

    Must be called inside an active agent span. Tool failures become
    observations; only a provider or configuration failure ends the run
    unsuccessfully.
    """
    if not agent_name.strip():
        raise ValueError("agent_name must not be blank")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if tool_budget < 0:
        raise ValueError("tool_budget must not be negative")
    agent_name = agent_name.strip()

    steps: list[ReActStep] = []
    errors: list[ResearchError] = []
    stop_reason: StopReason | None = None
    tool_calls = 0
    iteration = 0

    while iteration < max_iterations and stop_reason is None:
        iteration += 1
        observation: ReActObservation | None = None
        tool_result: ToolResult | None = None
        tool_input: dict[str, JsonValue] = {}

        # The try sits outside the span so the span records the real failure
        # before the loop converts it into a stop reason.
        try:
            async with tracker.react_iteration_span(iteration) as span:
                decision = await decide(iteration, steps)

                if decision.action == "finish":
                    stop_reason = "finished"
                else:
                    tool_name = decision.tool_name or ""
                    tool = tools.get(tool_name)
                    available = ", ".join(tools.names) or "none"
                    if tool is None:
                        observation = ReActObservation(
                            tool_name=tool_name,
                            success=False,
                            summary=(
                                f"{tool_name} is not available to this agent. "
                                f"Available tools: {available}."
                            ),
                            error_type="agent_unknown_tool",
                        )
                        errors.append(
                            agent_error(
                                agent_name=agent_name,
                                error_type="agent_unknown_tool",
                                message=(
                                    f"{tool_name} is not available to this agent."
                                ),
                                details={
                                    "tool": tool_name,
                                    "iteration": iteration,
                                },
                            )
                        )
                    elif tool_calls >= tool_budget:
                        observation = ReActObservation(
                            tool_name=tool_name,
                            success=False,
                            summary=(
                                f"The tool budget of {tool_budget} calls is "
                                "exhausted; no further tool calls are possible."
                            ),
                            error_type="agent_tool_budget_exhausted",
                        )
                        errors.append(
                            agent_error(
                                agent_name=agent_name,
                                error_type="agent_tool_budget_exhausted",
                                message=(
                                    "The agent stopped after exhausting its "
                                    "tool budget."
                                ),
                                details={
                                    "tool": tool_name,
                                    "iteration": iteration,
                                    "tool_budget": tool_budget,
                                },
                            )
                        )
                        stop_reason = "tool_budget_exhausted"
                    else:
                        try:
                            tool_input = parse_tool_input(decision.tool_input_json)
                        except ValueError as error:
                            observation = ReActObservation(
                                tool_name=tool_name,
                                success=False,
                                summary=summarize_text(
                                    f"{tool_name} arguments were rejected: "
                                    f"{error}",
                                    limit=summary_limit,
                                ),
                                error_type="agent_invalid_tool_input",
                            )
                            errors.append(
                                agent_error(
                                    agent_name=agent_name,
                                    error_type="agent_invalid_tool_input",
                                    message=(
                                        f"{tool_name} arguments could not be "
                                        "decoded."
                                    ),
                                    details={
                                        "tool": tool_name,
                                        "iteration": iteration,
                                    },
                                )
                            )
                        else:
                            tool_result = await tool.execute(**tool_input)
                            tool_calls += 1
                            observation = _tool_observation(
                                tool_result, limit=summary_limit
                            )
                            if not tool_result.success:
                                errors.append(
                                    agent_error(
                                        agent_name=agent_name,
                                        error_type="agent_tool_failed",
                                        message=(
                                            f"{tool_name} failed; the agent "
                                            "continued with an observation."
                                        ),
                                        details={
                                            "tool": tool_name,
                                            "iteration": iteration,
                                            "tool_error_type": (
                                                observation.error_type or "unknown"
                                            ),
                                        },
                                    )
                                )

                span.set_outputs(
                    {
                        "agent_name": agent_name,
                        "iteration": iteration,
                        "thought": summarize_text(
                            decision.thought, limit=summary_limit
                        ),
                        "action": decision.action,
                        "tool": decision.tool_name,
                        "observation": (
                            None if observation is None else observation.summary
                        ),
                        "success": True if observation is None else observation.success,
                    }
                )
        except OpenAIProviderError as error:
            errors.append(
                agent_error(
                    agent_name=agent_name,
                    error_type="agent_provider_error",
                    message=(
                        "The model provider failed and the ReAct loop stopped."
                    ),
                    recoverable=False,
                    details={
                        "iteration": iteration,
                        "exception_type": type(error).__name__,
                    },
                )
            )
            stop_reason = "provider_error"
            break

        step = ReActStep(
            iteration=iteration,
            thought=decision.thought,
            action=decision.action,
            tool_name=decision.tool_name,
            tool_input=tool_input,
            observation=observation,
            tool_result=tool_result,
            final_answer=decision.final_answer,
        )
        steps.append(step)
        if on_step is not None:
            await on_step(step)
        if stop_reason is None and is_sufficient is not None and is_sufficient(steps):
            stop_reason = "sufficient"

    if stop_reason is None:
        stop_reason = "max_iterations"

    return ReActRun(
        agent_name=agent_name,
        steps=steps,
        stop_reason=stop_reason,
        iterations=iteration,
        tool_calls=tool_calls,
        final_answer=steps[-1].final_answer if steps else None,
        errors=errors,
    )
