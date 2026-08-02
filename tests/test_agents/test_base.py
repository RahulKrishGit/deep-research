"""Tests for the shared agent base class."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager

import pytest
from pydantic import ConfigDict, Field

from deep_research.agents.base import BaseAgent
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderTimeoutError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ContractModel, ResearchState
from tests.agent_fakes import (
    BoomTool,
    EchoTool,
    ScriptedCompleter,
    finish,
    use_tool,
)


class Summary(ContractModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1)


class SummaryAgent(BaseAgent[Summary]):
    name = "summarizer"
    description = "Summarize whatever the loop observed."
    allowed_tools = ("echo",)

    @property
    def output_schema(self) -> type[Summary]:
        return Summary

    def system_prompt(self, task: AgentTask) -> str:
        return "You are a deterministic test agent."

    def build_task(self, state: ResearchState) -> AgentTask:
        return AgentTask(
            instruction=state.original_question,
            guidance=f"iteration {state.iteration}",
        )

    async def finalize(self, task: AgentTask, run: ReActRun) -> Summary | None:
        if run.final_answer is None:
            return None
        return Summary(headline=run.final_answer)


class SchemaAgent(SummaryAgent):
    """Produce the final result through the provider instead of locally."""

    name = "schema_summarizer"

    async def finalize(self, task: AgentTask, run: ReActRun) -> Summary | None:
        return await self.complete_output(
            [ChatMessage(role="user", content=task.instruction)]
        )


class SufficientAgent(SummaryAgent):
    name = "sufficient_summarizer"

    def is_sufficient(self, steps: Sequence[ReActStep]) -> bool:
        return any(
            step.observation is not None and step.observation.success
            for step in steps
        )


class FlakyAgent(SummaryAgent):
    name = "flaky_summarizer"
    allowed_tools = ("echo", "boom")


def _state(question: str = "Why is the sky blue?") -> ResearchState:
    return ResearchState(session_id="session-1", original_question=question)


def _pad(agent_name: str = "summarizer", max_entries: int = 20) -> ScratchpadMemory:
    return ScratchpadMemory(
        session_id="session-1",
        agent_name=agent_name,
        max_entries=max_entries,
    )


def _agent(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    agent_class: type[SummaryAgent] = SummaryAgent,
    tools: Sequence[object] | None = None,
    config: AgentRuntimeConfig | None = None,
) -> SummaryAgent:
    return agent_class(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(agent_class.name),
        tools=list(tools) if tools is not None else [EchoTool(tracker)],
        config=config or AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )


@pytest.mark.asyncio
async def test_run_returns_a_typed_result_from_a_one_step_loop(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter([finish("Nothing to look up.", "Rayleigh.")])
    agent = _agent(tracker, completer)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.agent_name == "summarizer"
    assert outcome.result == Summary(headline="Rayleigh.")
    assert outcome.react.stop_reason == "finished"
    assert outcome.errors == []
    assert outcome.state_update == {"errors": []}


@pytest.mark.asyncio
async def test_run_renders_the_task_tools_and_scratchpad_into_the_prompt(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        [
            use_tool("Check the echo.", "echo", '{"value": "hi"}'),
            finish("Enough.", "Rayleigh."),
        ]
    )
    agent = _agent(tracker, completer)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        await agent.run(_state())

    first_schema, first_agent_name, first_messages = completer.calls[0]
    assert first_schema == "ReActDecision"
    assert first_agent_name == "summarizer"
    assert first_messages[0].content == "You are a deterministic test agent."
    assert "Why is the sky blue?" in first_messages[1].content
    assert "- echo:" in first_messages[1].content
    assert "(no notes yet)" in first_messages[1].content
    assert "Iteration 1 of 3." in first_messages[1].content

    second_messages = completer.calls[1][2]
    assert "- [thought] Check the echo." in second_messages[1].content
    assert "- [observation] echo succeeded" in second_messages[1].content
    assert "Iteration 2 of 3." in second_messages[1].content


@pytest.mark.asyncio
async def test_run_writes_a_thought_and_an_observation_per_iteration(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        [
            use_tool("Check the echo.", "echo", '{"value": "hi"}'),
            finish("Enough.", "Rayleigh."),
        ]
    )
    agent = _agent(tracker, completer)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        await agent.run(_state())

    kinds = [entry.kind for entry in agent.scratchpad.entries]
    contents = [entry.content for entry in agent.scratchpad.entries]
    assert kinds == ["thought", "observation", "thought", "decision"]
    assert contents[0] == "Check the echo."
    assert contents[1].startswith("echo succeeded")
    assert contents[3] == "Rayleigh."
    assert agent.scratchpad.entries[1].metadata == {
        "iteration": 1,
        "tool": "echo",
        "success": True,
    }


@pytest.mark.asyncio
async def test_run_limits_the_rendered_scratchpad_window(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        [use_tool("Look again.", "echo", '{"value": "hi"}')] * 2
        + [finish("Enough.", "Rayleigh.")]
    )
    agent = _agent(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=3, tool_budget=3, prompt_context_entries=1
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        await agent.run(_state())

    body = completer.calls[2][2][1].content
    notes = [line for line in body.splitlines() if line.startswith("- [")]
    assert len(notes) == 1


@pytest.mark.asyncio
async def test_run_uses_the_configured_observation_summary_limit(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        [
            use_tool("Echo something long.", "echo", '{"value": "' + "x" * 50 + '"}'),
            finish("Enough.", "Rayleigh."),
        ]
    )
    agent = _agent(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=3, tool_budget=3, observation_summary_chars=20
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    observation = outcome.react.steps[0].observation
    assert observation is not None
    assert len(observation.summary) == 20


def _capture_agent_outputs(
    tracker: Tracker,
) -> list[dict[str, object] | None]:
    """Record ``span.outputs`` for every ``agent_span`` opened.

    Mirrors ``_capture_iteration_outputs`` in ``test_react.py``: wrap the
    real span so the assertion on ``span.set_outputs(...)`` fails if the
    call is ever removed from ``BaseAgent.run``.
    """
    captured: list[dict[str, object] | None] = []
    original = tracker.agent_span

    @asynccontextmanager
    async def wrapped(agent_name: str):
        async with original(agent_name) as span:
            yield span
            captured.append(span.outputs)

    tracker.agent_span = wrapped  # type: ignore[method-assign]
    return captured


@pytest.mark.asyncio
async def test_run_records_the_stop_reason_on_the_agent_span(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter([finish("Nothing to look up.", "Rayleigh.")])
    agent = _agent(tracker, completer)
    captured = _capture_agent_outputs(tracker)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        await agent.run(_state())

    assert len(captured) == 1
    outputs = captured[0]
    assert outputs is not None
    assert outputs["stop_reason"] == "finished"
    assert outputs["iterations"] == 1
    assert outputs["tool_calls"] == 0
    assert outputs["produced_result"] is True

    agent_metrics = [
        metric
        for metric in tracker.metrics
        if metric.metric_type == "agent" and metric.scope == "agent"
    ]
    assert len(agent_metrics) == 1
    assert agent_metrics[0].agent_name == "summarizer"

    completed = [
        event
        for event in tracker.events
        if event.event_type == "observability.span.completed"
        and event.metadata.get("span_name") == "agent.summarizer"
    ]
    assert len(completed) == 1
    assert completed[0].metadata["success"] is True


@pytest.mark.asyncio
async def test_tool_failures_reach_the_state_update_as_recoverable_errors(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        [use_tool("Try the flaky tool.", "boom"), finish("Enough.", "Partial.")]
    )
    agent = _agent(
        tracker,
        completer,
        agent_class=FlakyAgent,
        tools=[EchoTool(tracker), BoomTool(tracker)],
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.result == Summary(headline="Partial.")
    assert [error.error_type for error in outcome.errors] == ["agent_tool_failed"]
    assert outcome.errors[0].recoverable is True
    assert outcome.state_update == {"errors": outcome.errors}


@pytest.mark.asyncio
async def test_provider_failure_yields_no_result_and_a_stopped_run(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter([ProviderTimeoutError("timed out")])
    agent = _agent(tracker, completer)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.result is None
    assert outcome.react.stop_reason == "provider_error"
    assert outcome.react.succeeded is False
    assert outcome.errors[0].recoverable is False


@pytest.mark.asyncio
async def test_finalize_uses_the_declared_output_schema(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to look up.", "Rayleigh.")],
        outputs=[Summary(headline="From the provider.")],
    )
    agent = _agent(tracker, completer, agent_class=SchemaAgent)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.result == Summary(headline="From the provider.")
    assert completer.calls[-1][0] == "Summary"
    assert completer.calls[-1][1] == "schema_summarizer"


@pytest.mark.asyncio
async def test_a_finalize_failure_propagates(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to look up.", "Rayleigh.")],
        outputs=[ProviderTimeoutError("timed out")],
    )
    agent = _agent(tracker, completer, agent_class=SchemaAgent)

    with pytest.raises(ProviderTimeoutError):
        async with tracker.session_span("session-1", "Why is the sky blue?"):
            await agent.run(_state())


@pytest.mark.asyncio
async def test_the_sufficiency_hook_stops_the_loop(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        [use_tool("Check the echo.", "echo", '{"value": "hi"}')] * 3
    )
    agent = _agent(tracker, completer, agent_class=SufficientAgent)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.react.stop_reason == "sufficient"
    assert outcome.react.iterations == 1
    assert outcome.result is None


@pytest.mark.asyncio
async def test_scratchpad_errors_are_merged_into_the_run(tracker: Tracker) -> None:
    def explode(entries: object) -> str:
        raise RuntimeError("summarizer offline")

    completer = ScriptedCompleter(
        [use_tool("Look.", "echo", '{"value": "hi"}')] * 2
        + [finish("Enough.", "Rayleigh.")]
    )
    agent = SummaryAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="summarizer",
            max_entries=2,
            summarizer=explode,  # type: ignore[arg-type]
        ),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert any(
        error.error_type == "scratchpad_summarization_failed"
        for error in outcome.errors
    )
    assert agent.scratchpad.errors == ()


@pytest.mark.asyncio
async def test_run_requires_an_active_session_span(tracker: Tracker) -> None:
    completer = ScriptedCompleter([finish("Nothing to look up.", "Rayleigh.")])
    agent = _agent(tracker, completer)

    with pytest.raises(RuntimeError, match="require an active session"):
        await agent.run(_state())


def test_an_agent_may_not_declare_a_tool_that_was_not_injected(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()

    with pytest.raises(AgentConfigurationError, match="echo"):
        SummaryAgent(
            provider=completer,
            tracker=tracker,
            scratchpad=_pad(),
            tools=[],
        )


def test_the_scratchpad_must_belong_to_the_agent(tracker: Tracker) -> None:
    completer = ScriptedCompleter()

    with pytest.raises(AgentConfigurationError, match="scratchpad"):
        SummaryAgent(
            provider=completer,
            tracker=tracker,
            scratchpad=_pad("someone_else"),
            tools=[EchoTool(tracker)],
        )


def test_an_agent_class_without_a_name_is_rejected(tracker: Tracker) -> None:
    class NamelessAgent(SummaryAgent):
        name = "   "

    with pytest.raises(AgentConfigurationError, match="non-blank name"):
        NamelessAgent(
            provider=ScriptedCompleter(),
            tracker=tracker,
            scratchpad=_pad("nameless"),
            tools=[EchoTool(tracker)],
        )


def test_the_default_config_bounds_the_loop(tracker: Tracker) -> None:
    agent = SummaryAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=_pad(),
        tools=[EchoTool(tracker)],
    )

    assert agent.config == AgentRuntimeConfig()
    assert agent.toolset.names == ("echo",)


def test_the_agent_exposes_its_provider_and_tracker(tracker: Tracker) -> None:
    """Concrete agents that drive their own loops need both collaborators."""
    completer = ScriptedCompleter()
    agent = _agent(tracker, completer)

    assert agent.provider is completer
    assert agent.tracker is tracker
