"""Tests for the bounded, tracker-instrumented ReAct loop."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager

import pytest

from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import ReActDecision, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError, StructuredOutputError
from tests.agent_fakes import (
    BoomTool,
    EchoTool,
    StrictEchoTool,
    agent_scope,
    finish,
    use_tool,
)


def _capture_iteration_outputs(
    tracker: Tracker,
) -> list[dict[str, object] | None]:
    """Record ``span.outputs`` for every ``react_iteration_span`` opened.

    Mirrors the ``RecordingToolTracker`` pattern in ``test_tools/test_base.py``:
    wrap the real span so the assertion on ``span.set_outputs(...)`` fails if
    the call is ever removed from the loop.
    """
    captured: list[dict[str, object] | None] = []
    original = tracker.react_iteration_span

    @asynccontextmanager
    async def wrapped(iteration: int):
        async with original(iteration) as span:
            yield span
            captured.append(span.outputs)

    tracker.react_iteration_span = wrapped  # type: ignore[method-assign]
    return captured


def _decider(decisions: Sequence[ReActDecision]):
    queue = list(decisions)

    async def decide(
        iteration: int, steps: Sequence[ReActStep]
    ) -> ReActDecision:
        assert iteration == len(steps) + 1
        return queue.pop(0)

    return decide


def _raiser(error: BaseException):
    async def decide(
        iteration: int, steps: Sequence[ReActStep]
    ) -> ReActDecision:
        raise error

    return decide


def _toolset(tracker: Tracker, *names: str) -> AgentToolset:
    return AgentToolset(
        [EchoTool(tracker), BoomTool(tracker), StrictEchoTool(tracker)],
        allowed=list(names),
    )


@pytest.mark.asyncio
async def test_one_step_loop_finishes_immediately(tracker: Tracker) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider([finish("I already know this.", "The sky scatters blue.")]),
            max_iterations=3,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.succeeded is True
    assert run.iterations == 1
    assert run.tool_calls == 0
    assert run.final_answer == "The sky scatters blue."
    assert len(run.steps) == 1
    assert run.steps[0].observation is None
    assert run.errors == []


@pytest.mark.asyncio
async def test_multi_step_loop_calls_a_tool_then_finishes(tracker: Tracker) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Check the echo.", "echo", '{"value": "hello"}'),
                    finish("That is enough.", "It echoed hello."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.iterations == 2
    assert run.tool_calls == 1
    first, second = run.steps
    assert first.tool_name == "echo"
    assert first.tool_input == {"value": "hello"}
    assert first.observation is not None
    assert first.observation.success is True
    assert "echo succeeded" in first.observation.summary
    assert first.tool_result is not None
    assert first.tool_result.data == {"echo": "hello"}
    assert second.action == "finish"


@pytest.mark.asyncio
async def test_loop_stops_at_max_iterations(tracker: Tracker) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [use_tool("Keep going.", "echo", '{"value": "x"}')] * 2
            ),
            max_iterations=2,
            tool_budget=10,
        )

    assert run.stop_reason == "max_iterations"
    assert run.succeeded is True
    assert run.iterations == 2
    assert run.tool_calls == 2
    assert run.final_answer is None


@pytest.mark.asyncio
async def test_sufficiency_hook_stops_the_loop_early(tracker: Tracker) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [use_tool("Keep going.", "echo", '{"value": "x"}')] * 3
            ),
            max_iterations=3,
            tool_budget=10,
            is_sufficient=lambda steps: len(steps) >= 2,
        )

    assert run.stop_reason == "sufficient"
    assert run.iterations == 2


@pytest.mark.asyncio
async def test_tool_budget_stops_the_loop_before_the_extra_call(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [use_tool("Keep going.", "echo", '{"value": "x"}')] * 3
            ),
            max_iterations=5,
            tool_budget=1,
        )

    assert run.stop_reason == "tool_budget_exhausted"
    assert run.tool_calls == 1
    assert run.iterations == 2
    last = run.steps[-1]
    assert last.observation is not None
    assert last.observation.success is False
    assert last.observation.error_type == "agent_tool_budget_exhausted"
    assert [error.error_type for error in run.errors] == [
        "agent_tool_budget_exhausted"
    ]
    assert run.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_zero_budget_agent_can_still_think_and_finish(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker),
            decide=_decider([finish("No tool needed.", "Done.")]),
            max_iterations=3,
            tool_budget=0,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 0


@pytest.mark.asyncio
async def test_tool_failure_becomes_an_observation_and_the_loop_continues(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "boom", "echo"),
            decide=_decider(
                [
                    use_tool("Try the flaky tool.", "boom"),
                    finish("Fall back to what I know.", "Partial answer."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 1
    failed = run.steps[0]
    assert failed.observation is not None
    assert failed.observation.success is False
    assert failed.observation.error_type == "TimeoutError"
    assert "boom failed (TimeoutError): upstream timed out" in (
        failed.observation.summary
    )
    assert failed.tool_result is not None
    assert failed.tool_result.success is False
    error = run.errors[0]
    assert error.error_type == "agent_tool_failed"
    assert error.source == "agent.researcher"
    assert error.recoverable is True
    assert error.details == {
        "tool": "boom",
        "iteration": 1,
        "tool_error_type": "TimeoutError",
    }


@pytest.mark.asyncio
async def test_unknown_tool_summary_is_truncated_to_summary_limit(
    tracker: Tracker,
) -> None:
    """A pathological/hallucinated tool name must not blow past summary_limit."""
    long_tool_name = "x" * 500
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Reach for a made-up tool.", long_tool_name),
                    finish("Use what I have.", "Answered without it."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
            summary_limit=20,
        )

    observation = run.steps[0].observation
    assert observation is not None
    assert observation.error_type == "agent_unknown_tool"
    assert len(observation.summary) <= 20
    assert run.errors[0].error_type == "agent_unknown_tool"
    # The tool name portion is clamped to summary_limit; only the fixed
    # surrounding text ("... is not available to this agent.") adds to that.
    assert len(run.errors[0].message) <= 20 + len(" is not available to this agent.")
    assert long_tool_name not in run.errors[0].message


@pytest.mark.asyncio
async def test_tool_budget_exhausted_summary_is_truncated_to_summary_limit(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [use_tool("Keep going.", "echo", '{"value": "x"}')] * 2
            ),
            max_iterations=5,
            tool_budget=0,
            summary_limit=20,
        )

    assert run.stop_reason == "tool_budget_exhausted"
    observation = run.steps[-1].observation
    assert observation is not None
    assert observation.error_type == "agent_tool_budget_exhausted"
    assert len(observation.summary) <= 20


@pytest.mark.asyncio
async def test_finish_wins_over_sufficiency_on_the_same_step(
    tracker: Tracker,
) -> None:
    """`stop_reason` must not be overwritten once `finish` already set it."""
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider([finish("Done already.", "The answer.")]),
            max_iterations=3,
            tool_budget=5,
            is_sufficient=lambda steps: True,
        )

    assert run.stop_reason == "finished"


@pytest.mark.asyncio
async def test_unknown_tool_is_a_recoverable_observation(tracker: Tracker) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Reach for a tool I do not have.", "web_search"),
                    finish("Use what I have.", "Answered without search."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 0
    observation = run.steps[0].observation
    assert observation is not None
    assert observation.success is False
    assert observation.error_type == "agent_unknown_tool"
    assert "echo" in observation.summary
    assert run.errors[0].error_type == "agent_unknown_tool"


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_a_recoverable_observation(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Send bad arguments.", "echo", "{not json}"),
                    finish("Recovered.", "Done."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.tool_calls == 0
    observation = run.steps[0].observation
    assert observation is not None
    assert observation.error_type == "agent_invalid_tool_input"
    assert run.steps[0].tool_input == {}
    assert run.errors[0].error_type == "agent_invalid_tool_input"
    assert "tool_input_json" not in run.errors[0].details


@pytest.mark.asyncio
async def test_wrong_tool_arguments_surface_as_a_failed_tool_result(
    tracker: Tracker,
) -> None:
    """A tool that rejects its kwargs must not escape as an exception."""
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "strict_echo"),
            decide=_decider(
                [
                    use_tool("Call it wrong.", "strict_echo", '{"wrong": 1}'),
                    finish("Recovered.", "Done."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 1
    observation = run.steps[0].observation
    assert observation is not None
    assert observation.success is False
    assert observation.error_type == "TypeError"


@pytest.mark.asyncio
async def test_provider_failure_stops_the_loop_without_raising(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_raiser(ProviderTimeoutError("OpenAI request timed out")),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "provider_error"
    assert run.succeeded is False
    assert run.steps == []
    assert run.iterations == 1
    error = run.errors[0]
    assert error.error_type == "agent_provider_error"
    assert error.recoverable is False
    assert error.details == {
        "iteration": 1,
        "exception_type": "ProviderTimeoutError",
    }
    assert "timed out" not in error.model_dump_json()


@pytest.mark.asyncio
async def test_unrepairable_agent_output_stops_the_loop(tracker: Tracker) -> None:
    """`complete_structured` already made its one repair attempt."""
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_raiser(StructuredOutputError("still invalid")),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "provider_error"
    assert run.errors[0].details["exception_type"] == "StructuredOutputError"


@pytest.mark.asyncio
async def test_programming_errors_propagate(tracker: Tracker) -> None:
    with pytest.raises(AttributeError):
        async with agent_scope(tracker):
            await run_react_loop(
                agent_name="researcher",
                tracker=tracker,
                tools=_toolset(tracker, "echo"),
                decide=_raiser(AttributeError("bad hook")),
                max_iterations=2,
                tool_budget=5,
            )


@pytest.mark.asyncio
async def test_on_step_receives_every_completed_step(tracker: Tracker) -> None:
    seen: list[int] = []

    async def record(step: ReActStep) -> None:
        seen.append(step.iteration)

    async with agent_scope(tracker):
        await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Once.", "echo", '{"value": "a"}'),
                    finish("Done.", "Answer."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
            on_step=record,
        )

    assert seen == [1, 2]


@pytest.mark.asyncio
async def test_each_iteration_emits_a_metric_and_span_outputs(
    tracker: Tracker,
) -> None:
    captured = _capture_iteration_outputs(tracker)

    async with agent_scope(tracker):
        await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Check the echo.", "echo", '{"value": "hello"}'),
                    finish("Done.", "It echoed hello."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert len(captured) == 2
    tool_outputs, finish_outputs = captured
    assert tool_outputs is not None
    assert tool_outputs["agent_name"] == "researcher"
    assert tool_outputs["iteration"] == 1
    assert tool_outputs["action"] == "use_tool"
    assert tool_outputs["tool"] == "echo"
    assert tool_outputs["success"] is True
    assert "echo succeeded" in tool_outputs["observation"]
    assert finish_outputs is not None
    assert finish_outputs["action"] == "finish"
    assert finish_outputs["observation"] is None
    # No observation on a finish step: success must default to True, not
    # crash on `observation.success` against a None observation.
    assert finish_outputs["success"] is True

    iteration_metrics = [
        metric
        for metric in tracker.metrics
        if metric.metric_type == "agent" and metric.scope == "react_iteration"
    ]
    assert [metric.iteration for metric in iteration_metrics] == [1, 2]
    assert all(metric.agent_name == "researcher" for metric in iteration_metrics)
    assert all(metric.success for metric in iteration_metrics)
    assert all(metric.latency_ms >= 0.0 for metric in iteration_metrics)

    completed = [
        event
        for event in tracker.events
        if event.event_type == "observability.span.completed"
        and event.metadata.get("span_kind") == "react_iteration"
    ]
    first = completed[0].metadata
    assert first["span_name"] == "react.iteration.1"
    outputs = [
        event
        for event in tracker.events
        if event.metadata.get("span_kind") == "tool"
    ]
    assert outputs, "the tool call must open its own span inside the iteration"


@pytest.mark.asyncio
async def test_a_failed_iteration_span_records_the_provider_error_type(
    tracker: Tracker,
) -> None:
    async with agent_scope(tracker):
        await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_raiser(ProviderTimeoutError("boom")),
            max_iterations=2,
            tool_budget=5,
        )

    metric = next(
        metric
        for metric in tracker.metrics
        if metric.metric_type == "agent" and metric.scope == "react_iteration"
    )
    assert metric.success is False
    assert metric.error_type == "ProviderTimeoutError"


@pytest.mark.asyncio
async def test_the_loop_requires_an_active_agent_span(tracker: Tracker) -> None:
    with pytest.raises(RuntimeError, match="require an active"):
        async with tracker.session_span("session-1", "Why?"):
            await run_react_loop(
                agent_name="researcher",
                tracker=tracker,
                tools=_toolset(tracker, "echo"),
                decide=_decider([finish("Done.", "Answer.")]),
                max_iterations=2,
                tool_budget=5,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"agent_name": "  "}, "agent_name must not be blank"),
        ({"max_iterations": 0}, "max_iterations must be at least 1"),
        ({"tool_budget": -1}, "tool_budget must not be negative"),
    ],
)
async def test_the_loop_rejects_unbounded_arguments(
    tracker: Tracker, kwargs: dict[str, object], match: str
) -> None:
    payload: dict[str, object] = {
        "agent_name": "researcher",
        "tracker": tracker,
        "tools": _toolset(tracker, "echo"),
        "decide": _decider([finish("Done.", "Answer.")]),
        "max_iterations": 2,
        "tool_budget": 5,
    }
    payload.update(kwargs)

    with pytest.raises(ValueError, match=match):
        await run_react_loop(**payload)  # type: ignore[arg-type]
