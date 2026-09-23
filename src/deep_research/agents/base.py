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

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import (
    PROMPT_VERSION,
    AgentTask,
    render_react_messages,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import (
    ReActDecision,
    ReActRun,
    ReActStep,
    react_decision_from_native_turn,
)
from deep_research.agents.toolset import AgentToolset
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, NativeToolTurn, ToolDefinition
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
)

ResultT = TypeVar("ResultT", bound=BaseModel)
_SchemaT = TypeVar("_SchemaT", bound=BaseModel)


def call_configuration_fingerprint(
    *,
    agent_name: str,
    model: str,
    thinking_mode: str,
    reasoning_effort: str,
    output_limit: int | None,
    context_limit: int,
    schema_name: str,
    prompt_version: str,
) -> str:
    """A stable fingerprint of everything one provider call is configured by.

    Twelve hex characters over a sorted JSON payload, the same shape the
    evaluation harness uses for its configuration fingerprints. Every input
    is part of it on purpose: two artifacts whose calls differ in model,
    thinking, reasoning effort, output budget, context budget, response
    schema, or prompt version must not compare equal, and a field that is
    missing from this payload is a field whose change nobody can see.

    ``output_limit`` is the operation's own output budget (``None`` means the
    provider's global cap), and ``context_limit`` is how many scratchpad
    entries the request carries.
    """

    payload = {
        "agent_name": agent_name,
        "model": model,
        "thinking_mode": thinking_mode,
        "reasoning_effort": reasoning_effort,
        "output_limit": output_limit,
        "context_limit": context_limit,
        "schema_name": schema_name,
        "prompt_version": prompt_version,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


OUTPUT_LIMIT_RETRY_EFFORT = "high"
"""The effort a truncated structured call is re-asked at.

A reasoning token is a completion token, so the same output budget buys more
answer at a lower effort — and every agent whose calls are large enough to hit
a cap reasons at ``max``, so the retry is where the effort comes *down*. Lower
rather than higher on purpose: this is a second attempt at getting the reply
the run could not get, not a request for a better one, and the budget is
deliberately unchanged. The rule it belongs to is branch-level — one retry
under the same cap, then the caller's own failure path — and it lives here
because base is the module that owns the structured-call contract every one of
those callers goes through. One value, never a copy per agent.
"""

OUTPUT_LIMIT_RETRY_OUTCOMES = ("answered", "truncated", "failed")
"""What one retry can come back with.

``answered`` is a reply — whether the caller used it or had to re-ask it, which
is the caller's own record to make; ``truncated`` is the same truncation a
second time; ``failed`` is a provider failure that is neither, where no reply
arrived at all and the caller's own failure path takes over. Three outcomes of
the *retry call*, never a verdict about the artifact it feeds, so the record
cannot disagree with what follows it.
"""

OUTPUT_LIMIT_RETRY_READINGS: dict[str, str] = {
    "answered": "the retry returned a reply.",
    "truncated": "the retry was truncated as well.",
    "failed": "the retry failed at the provider.",
}
"""The sentence one outcome contributes to its record's message.

Shared by every call that records a retry, so the same outcome cannot read two
ways in two artifacts, and so a record whose outcome is not in
``OUTPUT_LIMIT_RETRY_OUTCOMES`` cannot be built at all.
"""

OUTPUT_LIMIT_ATTEMPT_EFFORTS: tuple[str | None, ...] = (
    None,
    OUTPUT_LIMIT_RETRY_EFFORT,
)
"""The efforts one call is attempted at, in order, and there are never more.

``None`` is the agent's own configured effort, which the first attempt always
uses, so an ordinary call is one request whose shape is byte-identical to the
one this project has always sent. The second entry is reached only by an
output-limit truncation of the first.
"""


class StructuredCompleter(Protocol):
    """The structured-output capability the agent runtime needs.

    ``OpenAIChatProvider`` and ``DeepSeekChatProvider`` satisfy it. Keeping
    the protocol to a single method keeps test doubles small, and it is the
    only capability the evaluation judge needs — the judge is typed against
    this protocol and never against ``AgentCompleter``, so judge wiring cannot
    require or invoke native tool calling.
    """

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[_SchemaT],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> _SchemaT:
        """Return validated structured output for ``schema``.

        ``max_tokens`` is a per-call output-budget override for this request
        only; ``None`` means the provider's configured global cap.
        ``reasoning_effort`` is the same kind of override for the effort this
        request reasons at: ``None`` means the effort the agent's own profile
        resolves to, which is what every ordinary call sends, and a value is
        validated against the same capability registry the configured effort
        goes through. It exists so a truncated call can be re-asked at another
        effort without editing the agent's configuration.
        """
        raise NotImplementedError


@runtime_checkable
class AgentCompleter(StructuredCompleter, Protocol):
    """Every capability a production agent's provider must offer.

    Both halves are required: tool-free finalization/extraction/review calls
    use ``complete_structured``, and every model-directed ReAct iteration uses
    ``complete_react``. Declaring them together is what lets construction fail
    loudly when a provider implements only one.
    """

    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        """Return one native tool call or one final answer."""
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
    call_fingerprints: dict[str, str] = field(default_factory=dict)
    """One configuration fingerprint per kind of provider call this run made.

    Keyed by the call label — the response schema's name for a structured
    call, ``"ReactDecision"`` for a model-directed turn — so a run says which
    configuration produced each kind of request rather than only which agent
    ran. Repeating a call kind within one run does not add an entry: the
    agent's configuration does not change between its own calls, so the label
    is the right key and a loop of five decisions stays one line.
    """


class BaseAgent(ABC, Generic[ResultT]):
    """Owns the provider, tracker, scratchpad, toolset, and loop bounds."""

    name: ClassVar[str]
    description: ClassVar[str]
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    preserve_provider_errors: ClassVar[bool] = False
    prompt_version: ClassVar[str] = PROMPT_VERSION
    """Which prompt instructions this agent's calls use.

    The shared library version by default; an agent whose own prompt contract
    changed sets its own, so an artifact records the instructions it was
    produced under rather than a value that moves for every agent at once.
    """

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
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
        # A provider missing either capability is a wiring mistake that would
        # otherwise surface as an AttributeError mid-loop, after paid calls.
        if not isinstance(provider, AgentCompleter):
            raise AgentConfigurationError(
                "agent provider must implement structured and native ReAct "
                "completion"
            )
        self._name = name.strip()
        self._provider = provider
        self._tracker = tracker
        self._scratchpad = scratchpad
        self._config = config or AgentRuntimeConfig()
        self._model_profile = model_profile
        self._call_fingerprints: dict[str, str] = {}
        # Built once at construction time so a declared-but-uninjected tool
        # fails loudly here rather than being deferred to first use.
        self._toolset = AgentToolset(tools, allowed=self.allowed_tools)

    @property
    def config(self) -> AgentRuntimeConfig:
        return self._config

    @property
    def model_profile(self) -> EffectiveModelConfig | None:
        """The resolved model and effort this agent's calls run under.

        ``None`` until the assembly hands one over: production
        (``runtime.assembly``) and evaluation (``evaluation.factory``) both
        pass ``settings.llm.resolve_for(name)``, so a per-agent model or
        effort override reaches the fingerprint of every call the agent
        makes. A hand-built agent that omits it fingerprints the field as
        unresolved rather than inventing a model.
        """
        return self._model_profile

    def fingerprint_call(
        self,
        label: str,
        *,
        output_limit: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Fingerprint one provider call and record it for this run.

        Called at the call site, so the label names the request actually being
        made (a schema name, or ``"ReactDecision"``) and the output limit is
        that request's own budget rather than a single run-wide number.

        A per-call ``reasoning_effort`` override is part of what a request is
        configured by, so it stands in for the profile's value here when one is
        passed; ``None`` fingerprints the profile's own effort, which is what
        every ordinary call sends.
        """
        profile = self._model_profile
        effort = (
            reasoning_effort
            if reasoning_effort is not None
            else ("unresolved" if profile is None else profile.reasoning_effort)
        )
        value = call_configuration_fingerprint(
            agent_name=self._name,
            model="unresolved" if profile is None else profile.model,
            thinking_mode=(
                "unresolved" if profile is None else profile.thinking_mode
            ),
            reasoning_effort=effort,
            output_limit=output_limit,
            context_limit=self._config.prompt_context_entries,
            schema_name=label,
            prompt_version=self.prompt_version,
        )
        self._call_fingerprints[label] = value
        return value

    @property
    def provider(self) -> AgentCompleter:
        """The provider, for agents that drive their own loop."""
        return self._provider

    @property
    def tracker(self) -> Tracker:
        """The tracker, for agents that open their own spans."""
        return self._tracker

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

    def build_decision_context(
        self,
        task: AgentTask,
        *,
        iteration: int,
        steps: Sequence[ReActStep],
    ) -> str:
        """Return complete structured context for the next ReAct decision.

        The default is empty so non-acquisition agents keep their existing
        prompt shape. Agents that carry bounded candidate/read state override
        this hook; it is called immediately before every provider turn.
        """
        del task, iteration, steps
        return ""

    # --- runtime ------------------------------------------------------------

    async def complete_output(self, messages: Sequence[ChatMessage]) -> ResultT:
        """Request this agent's declared output schema from the provider.

        The provider already performs exactly one structured repair attempt
        and raises ``StructuredOutputError`` if the retry also fails; do not
        add another retry here.
        """
        self.fingerprint_call(self.output_schema.__name__)
        return await self._provider.complete_structured(
            messages,
            self.output_schema,
            agent_name=self._name,
        )

    async def _complete_react_decision(
        self,
        task: AgentTask,
        *,
        iteration: int,
        steps: Sequence[ReActStep] = (),
        decision_context: str | None = None,
    ) -> tuple[ReActDecision, ...]:
        """Ask the provider for one native ReAct turn, adapted to loop state.

        The sole bridge between the provider-native tool boundary and
        ``run_react_loop``. Every model-directed agent inherits this path, so
        no agent can reintroduce a prompt-encoded tool protocol of its own.
        One turn may select several tools at once, so a tuple of decisions
        comes back rather than a single one.

        The tools travel through ``self.toolset`` rather than the internal
        attribute, so an agent that narrows its own toolset for one run — the
        planner withholding ``query_memory`` once startup recall has supplied
        the guidance — offers the provider exactly the tools it will execute.
        """
        self.fingerprint_call(
            "ReactDecision",
            output_limit=self._config.react_decision_max_tokens,
        )
        context = (
            self.build_decision_context(
                task,
                iteration=iteration,
                steps=steps,
            )
            if decision_context is None
            else decision_context
        )
        turn = await self._provider.complete_react(
            render_react_messages(
                system_prompt=self.system_prompt(task),
                task=task,
                scratchpad=self._scratchpad.recent(
                    self._config.prompt_context_entries
                ),
                iteration=iteration,
                max_iterations=self._config.max_iterations,
                decision_context=context,
            ),
            self.toolset.provider_definitions(),
            agent_name=self._name,
            max_tokens=self._config.react_decision_max_tokens,
        )
        return react_decision_from_native_turn(turn)
    async def run(self, state: ResearchState) -> AgentRun[ResultT]:
        """Run one bounded ReAct loop and finalize its result."""
        task = self.build_task(state)
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> tuple[ReActDecision, ...]:
            return await self._complete_react_decision(
                task,
                iteration=iteration,
                steps=steps,
            )

        async with self._tracker.agent_span(self._name) as span:
            react = await run_react_loop(
                agent_name=self._name,
                tracker=self._tracker,
                tools=toolset,
                decide=decide,
                max_iterations=self._config.max_iterations,
                tool_budget=self._config.tool_budget_for(self._name),
                on_step=self._record_step,
                is_sufficient=self.is_sufficient,
                summary_limit=self._config.observation_summary_chars,
                propagate_provider_errors=self.preserve_provider_errors,
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
                    "call_fingerprints": dict(self._call_fingerprints),
                }
            )

        return AgentRun(
            agent_name=self._name,
            result=result,
            react=react,
            errors=list(react.errors),
            state_update=self.state_update(result, react),
            call_fingerprints=dict(self._call_fingerprints),
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
