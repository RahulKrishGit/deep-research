"""The bounded ReAct loop every agent runs.

The loop takes callbacks rather than an agent, so it can be exercised with
plain functions and fakes. Its caller owns the ``agent_span``; the loop owns
one ``react_iteration_span`` per turn.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeAlias

from pydantic import JsonValue

from deep_research.agents.errors import agent_error, agent_provider_failure_details
from deep_research.agents.events import agent_event
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
from deep_research.providers import ProviderError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import ResearchError

DecideCallback: TypeAlias = Callable[
    [int, Sequence[ReActStep]], Awaitable[Sequence[ReActDecision]]
]
StepCallback: TypeAlias = Callable[[ReActStep], Awaitable[None]]
SufficiencyCallback: TypeAlias = Callable[[Sequence[ReActStep]], bool]

# The tool name the loop records for the calls a spent budget never reached.
# It is project-authored on purpose: the count of dropped calls is what the
# record must carry, and the provider's own names for them are not retained.
_UNEXECUTED_REMAINDER_TOOL_NAME = "(unexecuted)"

# The one tool whose failures publish a bounded, countable diagnosis. Those
# values are bounded by the producer too (``tools/web_scraper.py``), and they
# are revalidated here rather than trusted, because the record is public state
# and the values arrive from a tool: ``attempts`` is 1..3, ``retries`` is 0..2,
# ``status_code`` is an integer in 100..599, and ``content_type`` is either a
# lower-case ASCII media type of at most 64 characters or the producer's own
# static marker for a header it could not read. That marker is admitted
# deliberately, and it is the one place this projection is deliberately wider
# than the media-type grammar instead of narrower: the producer publishes the
# marker *in place of* a media type, and the branch that produces it publishes
# nothing else, so refusing the marker here would leave an unparseable content
# type with no diagnostic at all and uncountable by class. Nothing else in the
# failure is ever read, so the message, the request URL, the page content, and
# any unexpected key have no path into the record.
_SCRAPER_TOOL_NAME = "web_scraper"
_MIN_ATTEMPTS = 1
_MAX_ATTEMPTS = 3
_MIN_RETRIES = 0
_MAX_RETRIES = 2
_MIN_STATUS_CODE = 100
_MAX_STATUS_CODE = 599
_MAX_MEDIA_TYPE_LENGTH = 64
# The producer's static marker, project-authored text rather than remote
# input, so admitting it verbatim opens no channel.
_UNKNOWN_CONTENT_TYPE = "unknown"
_MEDIA_TYPE_PATTERN = re.compile(
    r"[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+"
)


def _bounded_int(value: JsonValue, *, minimum: int, maximum: int) -> int | None:
    """``value`` when it is an integer inside the bound, else ``None``.

    ``bool`` is rejected deliberately even though it is an ``int`` subclass: a
    published count of ``True`` is a type error, not a one, and the record
    exists to be counted by class.
    """
    if type(value) is not int:
        return None
    return value if minimum <= value <= maximum else None


def _bounded_media_type(value: JsonValue) -> str | None:
    """``value`` when it is a published content type, else ``None``.

    The producer publishes exactly two shapes, and both are kept: a lower-case
    ASCII media type, and its static marker for a header it could not read.
    The marker is admitted deliberately — it is project-authored text, the
    branch that publishes it publishes nothing else, and dropping it would make
    an unparseable content type indistinguishable from one that was never
    published. Everything else is dropped rather than normalised, ASCII first:
    folding and stripping are Unicode-aware, so a KELVIN SIGN would fold to
    ASCII ``k`` and a no-break space would be stripped, turning malformed input
    into a normalised copy of itself.
    """
    if not isinstance(value, str) or not value.isascii():
        return None
    if value == _UNKNOWN_CONTENT_TYPE:
        return value
    if len(value) > _MAX_MEDIA_TYPE_LENGTH or value != value.lower():
        return None
    return value if _MEDIA_TYPE_PATTERN.fullmatch(value) is not None else None


def _bounded_scraper_details(result: ToolResult) -> dict[str, JsonValue]:
    """The scraper's published failure values, revalidated one by one.

    ``status_code`` is optional by construction: ``raise_for_status()`` raises
    for any non-success response, including an out-of-range code a
    nonconforming peer can send, and the producer then omits the key entirely.
    An absent status therefore stays absent — never ``None``, never
    synthesized, never a bucket that no response produced.
    """
    error = result.error
    if error is None:
        return {}
    published = error.details
    projected: dict[str, JsonValue] = {}
    attempts = _bounded_int(
        published.get("attempts"), minimum=_MIN_ATTEMPTS, maximum=_MAX_ATTEMPTS
    )
    if attempts is not None:
        projected["attempts"] = attempts
    retries = _bounded_int(
        published.get("retries"), minimum=_MIN_RETRIES, maximum=_MAX_RETRIES
    )
    if retries is not None:
        projected["retries"] = retries
    status_code = _bounded_int(
        published.get("status_code"),
        minimum=_MIN_STATUS_CODE,
        maximum=_MAX_STATUS_CODE,
    )
    if status_code is not None:
        projected["status_code"] = status_code
    content_type = _bounded_media_type(published.get("content_type"))
    if content_type is not None:
        projected["content_type"] = content_type
    return projected


def _tool_failure_details(
    result: ToolResult,
    *,
    tool_name: str,
    iteration: int,
    tool_error_type: str,
) -> dict[str, JsonValue]:
    """Build the details for one ``agent_tool_failed`` record.

    Every failed call records the tool, the iteration, and the error type the
    observation already carries. ``tool_error_type`` is the one value here that
    is neither revalidated nor length-bounded, and it stays that way on
    purpose: its provenance is the tool that raised rather than this loop, and
    it is what the observation has always shown the model — a pre-existing
    property of the record, out of this projection's scope.

    Only ``web_scraper`` adds its own bounded diagnosis, and only for the tool
    the loop actually executed: the gate is the name the toolset resolved,
    never a name the result asserts about itself, so no other tool can reach
    into this projection.
    """
    details: dict[str, JsonValue] = {
        "tool": tool_name,
        "iteration": iteration,
        "tool_error_type": tool_error_type,
    }
    if tool_name == _SCRAPER_TOOL_NAME:
        details.update(_bounded_scraper_details(result))
    return details


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


def _unexecuted_remainder_step(
    *,
    iteration: int,
    unexecuted: int,
    tool_budget: int,
    summary_limit: int,
) -> ReActStep:
    """One step recording the calls a spent tool budget never reached.

    A model turn may ask for several tools at once, and the loop stops the
    moment the budget is spent rather than running the rest. Without this step
    the remainder would vanish from the record entirely: the run would report a
    budget stop while hiding how much of the provider's request it dropped. The
    number of dropped calls travels; their names and arguments never do.
    """
    summary = summarize_text(
        f"{unexecuted} further tool call(s) requested by the provider were not "
        f"executed because the tool budget of {tool_budget} calls is exhausted.",
        limit=summary_limit,
    )
    return ReActStep(
        iteration=iteration,
        thought=(
            "Stopped before every tool call the provider requested could run."
        ),
        action="use_tool",
        tool_name=_UNEXECUTED_REMAINDER_TOOL_NAME,
        tool_input={},
        observation=ReActObservation(
            tool_name=_UNEXECUTED_REMAINDER_TOOL_NAME,
            success=False,
            summary=summary,
            error_type="agent_tool_budget_exhausted",
        ),
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
    propagate_provider_errors: bool = True,
) -> ReActRun:
    """Run think -> act -> observe until a stop condition fires.

    Must be called inside an active agent span. Tool failures become
    observations; only a provider or configuration failure ends the run
    unsuccessfully.

    ``decide`` returns every decision the model made in one turn, because a
    native turn may select several tools at once. They execute in order, each
    producing its own step, and every step of that turn carries the turn's
    single iteration index: ``max_iterations`` bounds model turns, not tool
    calls, while ``tool_budget`` is charged once per *executed* call.

    A turn that spends the last of the budget records the calls it never
    reached in one extra step, so a budget stop never hides work the provider
    asked for and did not get.
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
        turn_steps: list[ReActStep] = []

        # The try sits outside the span so the span records the real failure
        # before the loop converts it into a stop reason. Steps are collected
        # first and appended after the span closes, so the span covers the
        # model turn and nothing else.
        try:
            async with tracker.react_iteration_span(iteration) as span:
                decisions = await decide(iteration, steps)
                for position, decision in enumerate(decisions):
                    observation: ReActObservation | None = None
                    tool_result: ToolResult | None = None
                    tool_input: dict[str, JsonValue] = {}
                    budget_spent = False

                    if decision.action == "finish":
                        stop_reason = "finished"
                    else:
                        tool_name = decision.tool_name or ""
                        # Unreachable-by-construction: ReActDecision requires a
                        # non-blank tool_name when action == "use_tool".
                        tool = tools.get(tool_name)
                        available = ", ".join(tools.names) or "none"
                        safe_tool_name = summarize_text(
                            tool_name, limit=summary_limit
                        )
                        if tool is None:
                            observation = ReActObservation(
                                tool_name=tool_name,
                                success=False,
                                summary=summarize_text(
                                    f"{tool_name} is not available to this "
                                    f"agent. Available tools: {available}.",
                                    limit=summary_limit,
                                ),
                                error_type="agent_unknown_tool",
                            )
                            errors.append(
                                agent_error(
                                    agent_name=agent_name,
                                    error_type="agent_unknown_tool",
                                    message=(
                                        f"{safe_tool_name} is not available to "
                                        "this agent."
                                    ),
                                    details={
                                        "tool": safe_tool_name,
                                        "iteration": iteration,
                                    },
                                )
                            )
                        elif tool_calls >= tool_budget:
                            observation = ReActObservation(
                                tool_name=tool_name,
                                success=False,
                                summary=summarize_text(
                                    f"The tool budget of {tool_budget} calls is "
                                    "exhausted; no further tool calls are "
                                    "possible.",
                                    limit=summary_limit,
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
                            budget_spent = True
                        else:
                            try:
                                tool_input = parse_tool_input(
                                    decision.tool_input_json
                                )
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
                                            f"{tool_name} arguments could not "
                                            "be decoded."
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
                                            details=_tool_failure_details(
                                                tool_result,
                                                tool_name=tool_name,
                                                iteration=iteration,
                                                tool_error_type=(
                                                    observation.error_type
                                                    or "unknown"
                                                ),
                                            ),
                                        )
                                    )

                    turn_steps.append(
                        ReActStep(
                            iteration=iteration,
                            thought=decision.thought,
                            action=decision.action,
                            tool_name=decision.tool_name or None,
                            tool_input=tool_input,
                            observation=observation,
                            tool_result=tool_result,
                            final_answer=decision.final_answer or None,
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
                            "tool": (
                                None
                                if decision.tool_name is None
                                else summarize_text(
                                    decision.tool_name, limit=summary_limit
                                )
                            ),
                            "observation": (
                                None
                                if observation is None
                                else observation.summary
                            ),
                            "success": (
                                True
                                if observation is None
                                else observation.success
                            ),
                        }
                    )
                    if budget_spent:
                        # Every call of this turn the provider asked for and did
                        # not get is counted here, including the one the budget
                        # refused a moment ago, so a budget stop can never
                        # silently drop work that was requested. The invariant
                        # the record supports is: executed + counted == requested.
                        unexecuted = len(decisions) - position
                        turn_steps.append(
                            _unexecuted_remainder_step(
                                iteration=iteration,
                                unexecuted=unexecuted,
                                tool_budget=tool_budget,
                                summary_limit=summary_limit,
                            )
                        )
                        errors.append(
                            agent_error(
                                agent_name=agent_name,
                                error_type="agent_tool_budget_exhausted",
                                message=(
                                    f"{unexecuted} further tool call(s) "
                                    "requested by the provider were not "
                                    "executed."
                                ),
                                details={
                                    "iteration": iteration,
                                    "tool_budget": tool_budget,
                                    "unexecuted_calls": unexecuted,
                                },
                            )
                        )
                        break
                    if decision.action == "finish":
                        break
        except ProviderError as error:
            tracker.record_event(
                agent_event(
                    agent_name=agent_name,
                    event_type="agent.provider_failure",
                    message="The ReAct provider decision failed.",
                    metadata={"iteration": iteration},
                )
            )
            if propagate_provider_errors:
                raise
            errors.append(
                agent_error(
                    agent_name=agent_name,
                    error_type="agent_provider_error",
                    message=(
                        "The model provider failed and the ReAct loop stopped."
                    ),
                    recoverable=False,
                    details=agent_provider_failure_details(
                        "react_decision", error, iteration=iteration
                    ),
                )
            )
            stop_reason = "provider_error"
            break

        for step in turn_steps:
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
        # One loop: its own totals are also its per-loop maximum.
        max_loop_iterations=iteration,
        max_loop_tool_calls=tool_calls,
        final_answer=steps[-1].final_answer if steps else None,
        errors=errors,
    )
