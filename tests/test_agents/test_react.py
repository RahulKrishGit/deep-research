"""Tests for the bounded, tracker-instrumented ReAct loop."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import asynccontextmanager

import pytest

from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import ReActDecision, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
)
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
    ) -> tuple[ReActDecision, ...]:
        assert iteration == len(steps) + 1
        return (queue.pop(0),)

    return decide


def _batch_decider(turns: Sequence[Sequence[ReActDecision]]):
    """Serve one whole model turn — a sequence of decisions — per call.

    ``decide`` returns every decision the provider made in a single turn, so a
    turn that carried several parallel tool calls is replayed as one turn.
    """
    queue = [tuple(turn) for turn in turns]

    async def decide(
        iteration: int, steps: Sequence[ReActStep]
    ) -> tuple[ReActDecision, ...]:
        del iteration, steps
        return queue.pop(0)

    return decide


def _raiser(error: BaseException):
    async def decide(
        iteration: int, steps: Sequence[ReActStep]
    ) -> tuple[ReActDecision, ...]:
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
async def test_finish_decision_normalizes_empty_unused_tool_name(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Enough evidence.",
        action="finish",
        tool_name="",
        tool_input_json="{}",
        final_answer="Done.",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker),
            decide=_decider([decision]),
            max_iterations=2,
            tool_budget=0,
        )

    assert run.stop_reason == "finished"
    assert run.steps[0].tool_name is None
    assert run.steps[0].final_answer == "Done."


@pytest.mark.asyncio
async def test_tool_decision_normalizes_empty_unused_final_answer(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Check one source.",
        action="use_tool",
        tool_name="echo",
        tool_input_json='{"value": "x"}',
        final_answer="",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [decision, finish("Enough.", "Done.")]
            ),
            max_iterations=2,
            tool_budget=1,
        )

    assert run.steps[0].tool_name == "echo"
    assert run.steps[0].final_answer is None


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
    """The budget is spent on the first call, so the second one never runs.

    The record gained one entry when a native turn learned to carry several
    calls: the call the budget refused and the calls behind it are now counted
    explicitly, so a budget stop states how much of what the provider asked for
    did not execute instead of dropping it silently. The loop itself still
    stops on the same call it always did.
    """
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
    assert all(
        error.error_type == "agent_tool_budget_exhausted" for error in run.errors
    )
    assert len(run.errors) == 2
    assert run.errors[0].recoverable is True
    assert run.errors[0].details["tool"] == "echo"
    # The refused call is the whole of this turn's unexecuted work: one call was
    # requested, none of it ran, and the count says so.
    assert run.errors[1].details["unexecuted_calls"] == 1


@pytest.mark.asyncio
async def test_one_turn_with_two_calls_records_two_steps_in_one_iteration(
    tracker: Tracker,
) -> None:
    """A parallel-call turn is one model turn, so it is one iteration.

    ``max_iterations`` still counts model turns: the two calls the provider
    made together produce two ``ReActStep`` records that share iteration 1, and
    the whole turn stays inside the single ``react_iteration_span`` opened for
    that turn.
    """
    captured = _capture_iteration_outputs(tracker)
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_batch_decider(
                [
                    (
                        use_tool("Look up A.", "echo", '{"value": "a"}'),
                        use_tool("Look up B.", "echo", '{"value": "b"}'),
                    ),
                    (finish("Enough.", "Both answers came back."),),
                ]
            ),
            max_iterations=5,
            tool_budget=10,
        )

    assert run.stop_reason == "finished"
    assert run.iterations == 2
    assert run.tool_calls == 2
    assert len(run.steps) == 3
    first, second, third = run.steps
    assert first.iteration == 1
    assert second.iteration == 1
    assert third.iteration == 2
    assert [first.tool_input, second.tool_input] == [
        {"value": "a"},
        {"value": "b"},
    ]
    assert [step.observation.success for step in (first, second)] == [True, True]
    assert third.action == "finish"
    # One model turn is one traced turn.
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_every_call_in_a_batch_keeps_its_own_outcome(tracker: Tracker) -> None:
    """One bad call must not cancel the rest of the provider's batch.

    An unavailable tool, undecodable arguments, and a failing tool each record
    their own failed observation and ``agent_error``, and the loop continues to
    the next call in the same turn. Only executed calls count against the
    budget.
    """
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo", "boom"),
            decide=_batch_decider(
                [
                    (
                        use_tool("Reach for a tool I do not have.", "web_search"),
                        use_tool("Send bad arguments.", "echo", "{not json}"),
                        use_tool("Try the flaky tool.", "boom"),
                        use_tool("Now a good one.", "echo", '{"value": "ok"}'),
                    ),
                    (finish("Enough.", "Three of four failed."),),
                ]
            ),
            max_iterations=3,
            tool_budget=10,
        )

    assert run.stop_reason == "finished"
    assert run.iterations == 2
    assert run.tool_calls == 2
    assert [step.iteration for step in run.steps] == [1, 1, 1, 1, 2]
    assert [step.observation.error_type for step in run.steps[:4]] == [
        "agent_unknown_tool",
        "agent_invalid_tool_input",
        "TimeoutError",
        None,
    ]
    assert [step.observation.success for step in run.steps[:4]] == [
        False,
        False,
        False,
        True,
    ]
    assert [error.error_type for error in run.errors] == [
        "agent_unknown_tool",
        "agent_invalid_tool_input",
        "agent_tool_failed",
    ]


@pytest.mark.asyncio
async def test_the_tool_budget_records_the_unexecuted_remainder_of_a_batch(
    tracker: Tracker,
) -> None:
    """Nothing the provider asked for may vanish when the budget stops a batch.

    With a budget of one, the first call of a two-call turn executes, the
    second call records the budget-exhausted observation, and the calls that
    were never reached are recorded as one step naming *how many* were dropped
    — a count, never provider text.
    """
    sentinel = "TOOL_BUDGET_REMAINDER_SENTINEL_9F2B"
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_batch_decider(
                [
                    (
                        use_tool("First.", "echo", '{"value": "a"}'),
                        use_tool(
                            "Second.",
                            "echo",
                            json.dumps({"value": sentinel}),
                        ),
                    ),
                ]
            ),
            max_iterations=3,
            tool_budget=1,
        )

    assert run.stop_reason == "tool_budget_exhausted"
    assert run.tool_calls == 1
    assert run.iterations == 1
    assert len(run.steps) == 3
    assert [step.iteration for step in run.steps] == [1, 1, 1]

    executed, exhausted, remainder = run.steps
    assert executed.observation.success is True
    assert exhausted.observation.error_type == "agent_tool_budget_exhausted"
    assert exhausted.observation.success is False

    remainder_observation = remainder.observation
    assert remainder_observation is not None
    assert remainder_observation.success is False
    assert remainder_observation.error_type == "agent_tool_budget_exhausted"
    assert remainder_observation.summary == (
        "1 further tool call(s) requested by the provider were not executed "
        "because the tool budget of 1 calls is exhausted."
    )
    assert remainder.final_answer is None
    assert remainder.tool_result is None

    budget_errors = [
        error
        for error in run.errors
        if "unexecuted_calls" in error.details
    ]
    assert len(budget_errors) == 1
    assert budget_errors[0].error_type == "agent_tool_budget_exhausted"
    assert budget_errors[0].details["unexecuted_calls"] == 1
    assert isinstance(budget_errors[0].details["unexecuted_calls"], int)
    assert sentinel not in repr(run.steps)
    assert sentinel not in repr(run.errors)


@pytest.mark.asyncio
async def test_a_batch_that_exhausts_the_budget_exactly_records_no_remainder(
    tracker: Tracker,
) -> None:
    """A batch the budget covers exactly has nothing left to report."""
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_batch_decider(
                [
                    (
                        use_tool("First.", "echo", '{"value": "a"}'),
                        use_tool("Second.", "echo", '{"value": "b"}'),
                    ),
                    (finish("Enough.", "Both calls ran."),),
                ]
            ),
            max_iterations=3,
            tool_budget=2,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 2
    assert len(run.steps) == 3
    assert run.errors == []


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
async def test_unknown_tool_error_details_clamp_an_oversized_tool_name(
    tracker: Tracker,
) -> None:
    """``errors[].details["tool"]`` must not carry an unbounded model string."""
    oversized_tool_name = "x" * (400)
    captured = _capture_iteration_outputs(tracker)
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Reach for a tool I do not have.", oversized_tool_name),
                    finish("Use what I have.", "Answered without search."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
            summary_limit=50,
        )

    assert len(run.errors[0].details["tool"]) <= 50
    assert captured[0] is not None
    assert len(captured[0]["tool"]) <= 50


@pytest.mark.asyncio
async def test_self_keyed_tool_arguments_are_a_recoverable_observation(
    tracker: Tracker,
) -> None:
    """A ``self`` key must not crash the loop via ``tool.execute(**kwargs)``."""
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [
                    use_tool("Send a self key.", "echo", '{"self": "x"}'),
                    finish("Recovered.", "Done."),
                ]
            ),
            max_iterations=4,
            tool_budget=5,
        )

    assert run.stop_reason == "finished"
    assert run.tool_calls == 0
    observation = run.steps[0].observation
    assert observation is not None
    assert observation.error_type == "agent_invalid_tool_input"
    assert run.steps[0].tool_input == {}
    assert run.errors[0].error_type == "agent_invalid_tool_input"


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
async def test_provider_failure_is_reraised_after_recording_a_safe_event(
    tracker: Tracker,
) -> None:
    provider_error = ProviderTimeoutError("OpenAI request timed out")
    async with agent_scope(tracker):
        with pytest.raises(ProviderTimeoutError) as caught:
            await run_react_loop(
                agent_name="researcher",
                tracker=tracker,
                tools=_toolset(tracker, "echo"),
                decide=_raiser(provider_error),
                max_iterations=4,
                tool_budget=5,
            )

    assert caught.value is provider_error


@pytest.mark.asyncio
async def test_unrepairable_agent_output_is_reraised(tracker: Tracker) -> None:
    """`complete_structured` already made its one repair attempt."""
    async with agent_scope(tracker):
        with pytest.raises(StructuredOutputError):
            await run_react_loop(
                agent_name="researcher",
                tracker=tracker,
                tools=_toolset(tracker, "echo"),
                decide=_raiser(StructuredOutputError("still invalid")),
                max_iterations=4,
                tool_budget=5,
            )


@pytest.mark.asyncio
async def test_provider_decision_records_safe_event_and_reraises_original_error(
    tracker: Tracker,
) -> None:
    provider_error = ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=4, output_tokens=4096),
            request_attempt=1,
        )
    )

    async with agent_scope(tracker):
        with pytest.raises(ProviderOutputLimitError) as caught:
            await run_react_loop(
                agent_name="planner",
                tracker=tracker,
                tools=_toolset(tracker, "echo"),
                decide=_raiser(provider_error),
                max_iterations=4,
                tool_budget=5,
            )

    assert caught.value is provider_error
    provider_events = [
        event
        for event in tracker.events
        if event.event_type == "agent.provider_failure"
    ]
    assert len(provider_events) == 1
    assert provider_events[0].metadata == {"iteration": 1}
    assert "4096" not in provider_events[0].model_dump_json()


@pytest.mark.asyncio
async def test_compatibility_provider_failure_records_safe_details_without_raising(
    tracker: Tracker,
) -> None:
    provider_error = ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=4, output_tokens=4096),
            request_attempt=1,
        )
    )

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_raiser(provider_error),
            max_iterations=4,
            tool_budget=5,
            propagate_provider_errors=False,
        )

    assert run.stop_reason == "provider_error"
    assert run.succeeded is False
    assert len(run.errors) == 1
    error = run.errors[0]
    assert error.error_type == "agent_provider_error"
    assert error.recoverable is False
    assert error.details["operation"] == "react_decision"
    provider = error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1
    assert "Provider response reached" not in str(error.details)


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
        with pytest.raises(ProviderTimeoutError):
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
