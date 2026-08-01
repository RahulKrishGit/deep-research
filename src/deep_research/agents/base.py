"""The shared agent base class: state in, bounded ReAct loop, typed result out.

Concrete agents implement four hooks (``output_schema``, ``system_prompt``,
``build_task``, ``finalize``) and may override two more (``is_sufficient``,
``state_update``). Everything else — tracing, iteration control, tool
selection, scratchpad writes, error collection — lives here.

The runtime never mutates ``ResearchState``. It reads through ``build_task``
and returns a ``ResearchStateUpdate`` the caller merges with
``merge_research_state``, which keeps this module free of any graph
framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask, render_react_messages
from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import ReActDecision, ReActRun, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
)

ResultT = TypeVar("ResultT", bound=BaseModel)
_SchemaT = TypeVar("_SchemaT", bound=BaseModel)


class StructuredCompleter(Protocol):
    """The one provider capability the agent runtime needs.

    ``OpenAIChatProvider`` satisfies it. Keeping the protocol to a single
    method keeps test doubles small; agents that also need free-text
    completion may type their own constructor against the concrete provider.
    """

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[_SchemaT],
        *,
        agent_name: str | None = None,
    ) -> _SchemaT:
        """Return validated structured output for ``schema``."""
        raise NotImplementedError


# Deliberately not slots=True: dataclass slot re-creation and Generic have a
# history of interacting badly, and this handle is never hot.
@dataclass
class AgentRun(Generic[ResultT]):
    """Everything one agent run produced."""

    agent_name: str
    result: ResultT | None
    react: ReActRun
    errors: list[ResearchError]
    state_update: ResearchStateUpdate


class BaseAgent(ABC, Generic[ResultT]):
    """Owns the provider, tracker, scratchpad, toolset, and loop bounds."""

    name: ClassVar[str]
    description: ClassVar[str]
    allowed_tools: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
    ) -> None:
        name = getattr(type(self), "name", "")
        if not isinstance(name, str) or not name.strip():
            raise AgentConfigurationError(
                "agent classes must define a non-blank name"
            )
        if scratchpad.agent_name != name.strip():
            raise AgentConfigurationError(
                "scratchpad agent_name must match the agent name"
            )
        self._name = name.strip()
        self._provider = provider
        self._tracker = tracker
        self._scratchpad = scratchpad
        self._config = config or AgentRuntimeConfig()
        # Built once at construction time so a declared-but-uninjected tool
        # fails loudly here rather than being deferred to first use.
        self._toolset = AgentToolset(tools, allowed=self.allowed_tools)

    @property
    def config(self) -> AgentRuntimeConfig:
        return self._config

    @property
    def scratchpad(self) -> ScratchpadMemory:
        return self._scratchpad

    @property
    def toolset(self) -> AgentToolset:
        return self._toolset

    # --- hooks concrete agents must implement -------------------------------

    @property
    @abstractmethod
    def output_schema(self) -> type[ResultT]:
        """The Pydantic model this agent produces."""
        raise NotImplementedError

    @abstractmethod
    def system_prompt(self, task: AgentTask) -> str:
        """The developer-role instructions for this agent."""
        raise NotImplementedError

    @abstractmethod
    def build_task(self, state: ResearchState) -> AgentTask:
        """Read research state and describe this run's job."""
        raise NotImplementedError

    @abstractmethod
    async def finalize(self, task: AgentTask, run: ReActRun) -> ResultT | None:
        """Turn a finished loop into the agent's typed output, or None."""
        raise NotImplementedError

    # --- hooks concrete agents may override ---------------------------------

    def is_sufficient(self, steps: Sequence[ReActStep]) -> bool:
        """Stop the loop early. Defaults to running until another bound hits."""
        del steps
        return False

    def state_update(
        self,
        result: ResultT | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Describe how this run changes research state.

        The default reports errors only; agents that write findings, plans,
        or reports override this. ``iteration`` is never returned — callers
        use ``advance_research_iteration``.
        """
        del result
        return {"errors": list(run.errors)}

    # --- runtime ------------------------------------------------------------

    async def complete_output(self, messages: Sequence[ChatMessage]) -> ResultT:
        """Request this agent's declared output schema from the provider.

        The provider already performs exactly one structured repair attempt
        and raises ``StructuredOutputError`` if the retry also fails; do not
        add another retry here.
        """
        return await self._provider.complete_structured(
            messages,
            self.output_schema,
            agent_name=self._name,
        )

    async def run(self, state: ResearchState) -> AgentRun[ResultT]:
        """Run one bounded ReAct loop and finalize its result."""
        task = self.build_task(state)
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            messages = render_react_messages(
                system_prompt=self.system_prompt(task),
                task=task,
                descriptors=toolset.descriptors(),
                scratchpad=self._scratchpad.recent(
                    self._config.prompt_context_entries
                ),
                iteration=iteration,
                max_iterations=self._config.max_iterations,
            )
            return await self._provider.complete_structured(
                messages,
                ReActDecision,
                agent_name=self._name,
            )

        async with self._tracker.agent_span(self._name) as span:
            react = await run_react_loop(
                agent_name=self._name,
                tracker=self._tracker,
                tools=toolset,
                decide=decide,
                max_iterations=self._config.max_iterations,
                tool_budget=self._config.tool_budget,
                on_step=self._record_step,
                is_sufficient=self.is_sufficient,
                summary_limit=self._config.observation_summary_chars,
            )
            react = react.model_copy(
                update={
                    "errors": [*react.errors, *self._scratchpad.drain_errors()]
                }
            )
            result = await self.finalize(task, react)
            span.set_outputs(
                {
                    "agent_name": self._name,
                    "stop_reason": react.stop_reason,
                    "iterations": react.iterations,
                    "tool_calls": react.tool_calls,
                    "produced_result": result is not None,
                }
            )

        return AgentRun(
            agent_name=self._name,
            result=result,
            react=react,
            errors=list(react.errors),
            state_update=self.state_update(result, react),
        )

    async def _record_step(self, step: ReActStep) -> None:
        """Write one iteration into the scratchpad the next prompt renders."""
        self._scratchpad.add(
            step.thought,
            kind="thought",
            metadata={"iteration": step.iteration},
        )
        if step.observation is not None:
            self._scratchpad.add(
                step.observation.summary,
                kind="observation",
                metadata={
                    "iteration": step.iteration,
                    "tool": step.observation.tool_name,
                    "success": step.observation.success,
                },
            )
        elif step.final_answer is not None:
            self._scratchpad.add(
                step.final_answer,
                kind="decision",
                metadata={"iteration": step.iteration},
            )
