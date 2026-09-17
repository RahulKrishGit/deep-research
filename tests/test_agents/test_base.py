"""Tests for the shared agent base class."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager

import pytest
from pydantic import ConfigDict, Field

from deep_research.agents.base import BaseAgent, call_configuration_fingerprint
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderTimeoutError
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
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

    first_call = completer.react_calls[0]
    assert first_call.agent_name == "summarizer"
    assert first_call.messages[0].content == "You are a deterministic test agent."
    assert "Why is the sky blue?" in first_call.messages[1].content
    assert "(no notes yet)" in first_call.messages[1].content
    assert "Iteration 1 of 3." in first_call.messages[1].content
    # The tool rides on the request, not in the text.
    assert [definition.name for definition in first_call.tools] == ["echo"]
    assert "- echo:" not in first_call.messages[1].content
    assert "echo" not in first_call.messages[0].content

    second_messages = completer.react_calls[1].messages
    assert "- [thought] Selected tool through provider-native calling." in (
        second_messages[1].content
    )
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
    assert contents[0] == "Selected tool through provider-native calling."
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

    body = completer.react_calls[2].messages[1].content
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


@pytest.mark.asyncio
async def test_every_provider_call_carries_its_own_configuration_fingerprint(
    tracker: Tracker,
) -> None:
    """A run says which configuration produced each of its requests.

    The ReAct decision and the structured output are different calls with
    different output budgets, so they cannot share one fingerprint — and both
    move when the model profile does, which is why the profile is part of the
    payload rather than only the agent's name.
    """
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to look up.", "Rayleigh.")],
        outputs=[Summary(headline="Rayleigh.")],
    )
    agent = SchemaAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(SchemaAgent.name),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(max_iterations=3),
        model_profile=EffectiveModelConfig(
            model="deepseek-v4-flash",
            thinking_mode="enabled",
            reasoning_effort="max",
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert set(outcome.call_fingerprints) == {"ReactDecision", "Summary"}
    assert len(set(outcome.call_fingerprints.values())) == 2

    other = SchemaAgent(
        provider=ScriptedCompleter(
            decisions=[finish("Nothing to look up.", "Rayleigh.")],
            outputs=[Summary(headline="Rayleigh.")],
        ),
        tracker=tracker,
        scratchpad=_pad(SchemaAgent.name),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(max_iterations=3),
        model_profile=EffectiveModelConfig(
            model="deepseek-v4-flash",
            thinking_mode="enabled",
            reasoning_effort="high",
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        cheaper = await other.run(_state())

    assert cheaper.call_fingerprints != outcome.call_fingerprints


@pytest.mark.parametrize(
    "field",
    [
        "agent_name",
        "model",
        "thinking_mode",
        "reasoning_effort",
        "output_limit",
        "context_limit",
        "schema_name",
        "prompt_version",
    ],
)
def test_a_call_fingerprint_covers_every_configured_input(field: str) -> None:
    """Changing any one input changes the fingerprint, and only that input."""
    baseline = {
        "agent_name": "planner",
        "model": "deepseek-v4-flash",
        "thinking_mode": "enabled",
        "reasoning_effort": "max",
        "output_limit": 32768,
        "context_limit": 8,
        "schema_name": "ResearchPlanDraft",
        "prompt_version": "planner-2",
    }
    changed = dict(baseline)
    changed[field] = "changed" if isinstance(baseline[field], str) else 4096

    assert call_configuration_fingerprint(**baseline) == (
        call_configuration_fingerprint(**baseline)
    )
    assert call_configuration_fingerprint(**changed) != (
        call_configuration_fingerprint(**baseline)
    )


def test_an_unresolved_model_profile_is_recorded_rather_than_invented(
    tracker: Tracker,
) -> None:
    """A hand-built agent fingerprints the field as unresolved."""
    agent = SummaryAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=_pad(),
        tools=[EchoTool(tracker)],
    )

    assert agent.model_profile is None
    fingerprint = agent.fingerprint_call("Summary")
    resolved = SummaryAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=_pad(),
        tools=[EchoTool(tracker)],
        model_profile=EffectiveModelConfig(
            model="deepseek-v4-flash",
            thinking_mode="enabled",
            reasoning_effort="high",
        ),
    ).fingerprint_call("Summary")

    assert fingerprint != resolved


def test_the_default_config_bounds_the_loop(tracker: Tracker) -> None:
    agent = SummaryAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=_pad(),
        tools=[EchoTool(tracker)],
    )

    assert agent.config == AgentRuntimeConfig()
    assert agent.toolset.names == ("echo",)


class BudgetAgent(SummaryAgent):
    """A synthetic agent declared under a canonical agent name.

    ``tool_budget_overrides`` is keyed by the six production agent names, so
    only an agent that *is* one of them can look up an override. Declaring a
    test double under ``synthesizer`` is what makes the base loop's budget
    lookup observable: with the override in place the loop must stop after
    one executed tool call even though the global budget is three.
    """

    name = "synthesizer"


@pytest.mark.asyncio
async def test_the_base_loop_uses_the_agent_specific_budget(
    tracker: Tracker,
) -> None:
    """The ReAct loop is bounded by this agent's budget, not the global one."""
    completer = ScriptedCompleter(
        [
            use_tool("Echo once.", "echo", '{"value": "one"}'),
            use_tool("Echo twice.", "echo", '{"value": "two"}'),
            finish("Enough.", "Rayleigh."),
        ]
    )
    agent = BudgetAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(BudgetAgent.name),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=3,
            tool_budget_overrides={"synthesizer": 1},
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.react.tool_calls == 1
    assert outcome.react.stop_reason == "tool_budget_exhausted"


@pytest.mark.asyncio
async def test_a_zero_budget_override_lets_an_agent_think_without_tools(
    tracker: Tracker,
) -> None:
    """``tool_budget_overrides={"synthesizer": 0}`` is a real bound, not a
    fallback to the global default."""
    completer = ScriptedCompleter([finish("Nothing to look up.", "Rayleigh.")])
    agent = BudgetAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(BudgetAgent.name),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(
            max_iterations=3,
            tool_budget=3,
            tool_budget_overrides={"synthesizer": 0},
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.react.tool_calls == 0
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_another_agents_budget_override_does_not_apply_here(
    tracker: Tracker,
) -> None:
    """A planner override must not narrow the synthesizer's own loop."""
    completer = ScriptedCompleter(
        [
            use_tool("Echo once.", "echo", '{"value": "one"}'),
            use_tool("Echo twice.", "echo", '{"value": "two"}'),
            finish("Enough.", "Rayleigh."),
        ]
    )
    agent = BudgetAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(BudgetAgent.name),
        tools=[EchoTool(tracker)],
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=2,
            tool_budget_overrides={"planner": 0},
        ),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        outcome = await agent.run(_state())

    assert outcome.react.tool_calls == 2


def test_the_agent_exposes_its_provider_and_tracker(tracker: Tracker) -> None:
    """Concrete agents that drive their own loops need both collaborators."""
    completer = ScriptedCompleter()
    agent = _agent(tracker, completer)

    assert agent.provider is completer
    assert agent.tracker is tracker
