# Base Agent ReAct Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared agent runtime — `BaseAgent`, its configuration and ReAct step contracts, a generic bounded ReAct loop, a pure prompt-rendering boundary, and a validated tool selection/execution path — so that later concrete agents (Planner, Researcher, Source Evaluator, Fact Checker, Synthesizer, Critic) are written by filling in four narrow hooks rather than by re-implementing loop control, tracing, or failure handling.

**Architecture:** Everything lives under `src/deep_research/agents/` and is layered bottom-up so each layer is testable alone. `steps.py` holds the pure ReAct data contracts (`ReActDecision`, `ReActObservation`, `ReActStep`, `ReActRun`) plus two pure helpers (`summarize_text`, `parse_tool_input`). `toolset.py` holds `AgentToolset`, the validated per-agent view over the injected `BaseTool` registry, and `ToolDescriptor`, the prompt-facing projection of a tool's `ClassVar` metadata. `prompts.py` is a pure function boundary: it turns an `AgentTask` plus toolset plus scratchpad window into a `list[ChatMessage]` with no I/O and no randomness. `react.py` holds `run_react_loop(...)`, a free async function that takes callbacks — not an agent — so the loop can be tested with lambdas and fakes. `base.py` binds them: `BaseAgent` owns the provider, tracker, scratchpad, toolset and config, opens the agent span, delegates iteration control to `run_react_loop`, and exposes four abstract hooks (`output_schema`, `system_prompt`, `build_task`, `finalize`) plus two overridable ones (`is_sufficient`, `state_update`). The runtime never mutates `ResearchState`: it reads through `build_task(state)` and returns a `ResearchStateUpdate` the caller merges, which keeps the loop state-shape-agnostic and LangGraph-free.

**Tech Stack:** Python 3.11+, Pydantic 2, OpenAI Python SDK (through the existing `OpenAIChatProvider`), LangSmith SDK 0.10+ (through the existing `Tracker`), pytest, pytest-asyncio (strict mode — every async test needs `@pytest.mark.asyncio`), Ruff

## Global Constraints

- Preserve `requires-python = ">=3.11"`. No new runtime dependencies: this plan adds zero entries to `pyproject.toml`.
- No LangGraph, FastAPI, or Streamlit import anywhere in `src/deep_research/agents/`. The runtime is a plain async library.
- No concrete Planner/Researcher/Source-Evaluator/Fact-Checker/Synthesizer/Critic agent. Specs 08-10 own those. Test doubles defined inside `tests/` are the only agent subclasses this plan creates.
- Do not re-implement provider retry or structured-output repair. `OpenAIChatProvider` already passes `max_retries=config.retry_count` to the SDK client and already performs exactly one repair request inside `complete_structured` before raising `StructuredOutputError`. The agent layer consumes that behavior and adds none of its own.
- Do not touch `BaseTool._execute`, `ToolCallContext`, or `ToolExecution`. The runtime uses tools only through the public `async execute(**kwargs) -> ToolResult` contract, which already never raises for operational failures.
- Every ReAct iteration runs inside `Tracker.react_iteration_span(iteration)`, which itself requires an active `agent_span` inside an active `session_span`. `run_react_loop` does **not** open the agent span; its caller does.
- Recorded `ResearchError.details` carry `exception_type` only — never `str(exception)` — matching the tracker and memory convention, so provider text cannot leak into `ResearchState`.
- Tool failures are observations. They are fed back to the model as text, are recorded as *recoverable* `ResearchError`s, and never stop the loop. Only provider/configuration failures are non-recoverable.
- Every model is a `ContractModel` subclass (`extra="forbid"`, `str_strip_whitespace=True`, `validate_default=True`). Tool-like class metadata uses `ClassVar`.
- Ruff `select = ["E", "F", "I"]`, line length 88. Imports must be isort-ordered.
- No test may make a real OpenAI, Tavily, or LangSmith network call. Every test constructs `Tracker(LangSmithRuntimeConfig(tracing_enabled=False, ...))`.
- Do not reintroduce an eager top-level `import openai`. Nothing in `agents/` imports the `openai` package at all; it imports `deep_research.providers`, which is already lazy.

## Known Risks And Unknowns

Flagged explicitly rather than guessed at.

1. **OpenAI strict structured outputs reject free-form objects.** `responses.parse(text_format=Schema)` converts the Pydantic model with `to_strict_json_schema`, which requires `additionalProperties: false` on every object. A field typed `dict[str, JsonValue]` produces an open object and would be rejected at request time. `ReActDecision` therefore carries **`tool_input_json: str`** — a JSON-encoded argument object the runtime decodes with `parse_tool_input`. It is uglier than a native mapping and is a deliberate trade: schema compatibility over prettiness. Decoding failures are handled as invalid actions, not crashes. **Open question for human review:** if a future SDK version supports open objects under strict mode, `tool_input_json` should collapse back to `tool_input: dict[str, JsonValue]`; the change is confined to `steps.py` and `react.py`.
2. **`ReActDecision` is never validated against a live OpenAI request in this plan.** All tests use a scripted completer. The first concrete agent (spec 08) is where the schema meets the real API. If `responses.parse` rejects the schema, the fix belongs in `steps.py`, and the nullable `tool_name`/`final_answer` fields are the most likely culprits — strict mode requires every property to be listed in `required`, which the SDK does automatically, but nullable unions have historically been the fragile part.
3. **`@dataclass(slots=True)` is deliberately not used for the generic `AgentRun`.** Combining `slots=True` with `typing.Generic` has been a recurring source of dataclass re-creation bugs. `AgentRun` uses a plain `@dataclass`. Do not "optimize" it by adding slots without running the suite.
4. **Tool budget accounting counts failed tool executions.** A tool that fails still consumed a network call and still costs money, so it decrements the budget. **Open question for human review:** if a concrete agent needs retries-without-budget-cost, the refund rule belongs in `run_react_loop`, not in the agent.
5. **`prompt_context_entries` interacts with `ScratchpadMemory.max_entries`.** The runtime writes up to two scratchpad entries per iteration (a thought and an observation), so a scratchpad configured with `max_turns: 20` holds roughly ten iterations of history before compaction. Both knobs are configurable and independent; the plan does not couple them.

## Design Trade-Offs

- **The loop is a free function taking callbacks, not a method or a Protocol.** `run_react_loop(*, decide=..., on_step=..., is_sufficient=...)` is testable with three lambdas and no agent at all, and `BaseAgent` binds its own methods into it. A `ReActPolicy` Protocol would have added a second abstract surface describing the same three functions.
- **`run_react_loop` opens iteration spans but not the agent span.** `Tracker.react_iteration_span` already raises `RuntimeError` without an active agent span, so the contract is enforced by the tracker rather than duplicated. `BaseAgent.run` owns the agent span so that `build_task`, the loop, and `finalize` all appear under one agent run and so the stop reason has exactly one place to be recorded.
- **Exceptions escape the iteration span, then get caught.** The `try:` sits *outside* `async with tracker.react_iteration_span(...)`, so a provider failure records `success=False` with its real error type on the span before the loop converts it into a `provider_error` stop. Catching inside the span would report a false success. This mirrors the pattern already used in `deep_research/memory/instrumentation.py`.
- **Only `OpenAIProviderError` is caught in the loop.** `BaseException`, `asyncio.CancelledError`, and ordinary programming errors (`TypeError`, `AttributeError`) propagate. A broad `except Exception` would turn a bug in a concrete agent's `decide` hook into a silent "provider error" stop.
- **An unknown tool is a recoverable observation, a bad decision schema is not.** If the model names a tool the agent may not use, the loop feeds "that tool is not available; you may use X, Y" back as an observation and spends one iteration — bounded, and the model gets a chance to correct. If the model returns output that cannot validate as `ReActDecision` at all, the provider has already made its one repair attempt and raised `StructuredOutputError`; retrying that in the agent layer would double the spend for no new information.
- **`BaseAgent.run` returns a result instead of raising on a stopped loop.** `AgentRun.result` is `ResultT | None` and `AgentRun.react.stop_reason` says why. A caller that wants an exception can check `stop_reason`; a graph node that wants to continue with partial state can merge `state_update` and move on. Only a `finalize` hook that itself raises (for example `StructuredOutputError` on the agent's own output schema) escapes `run`.
- **`output_schema` is an abstract property, not a `ClassVar`.** `ClassVar[type[ResultT]]` is illegal — a `ClassVar` may not contain a type variable — and the runtime needs the schema tied to the class's `ResultT` so `complete_output` returns the right type. The property is used by the base class (`complete_output`), so it is a real hook rather than decoration.
- **Scratchpad injection is required, not constructed from config.** `BaseAgent` takes a `ScratchpadMemory` instance. Building one internally would force a duplicate `max_entries` knob into `AgentRuntimeConfig` alongside the existing `memory.short_term.max_turns`, and would make it impossible to hand one agent a summarizer-backed pad and another a plain one.
- **Two scratchpad writes per iteration, not three.** The thought becomes a `"thought"` entry and the observation (or final answer) becomes an `"observation"`/`"decision"` entry. The chosen tool rides in the observation's metadata rather than in a third entry, so a 20-entry pad holds ~10 iterations rather than ~6.

## File Structure

- Modify `src/deep_research/utils/config.py` — add `AgentRuntimeConfig`, hang it off `ConfigSettings.agents`, register three environment overrides.
- Modify `config.yaml` — add the `agents:` section.
- Modify `tests/test_config.py` — extend the `config_path` fixture and the environment-override parametrization; add an `AgentRuntimeConfig` bounds test.
- Create `src/deep_research/agents/errors.py` — `AgentError`, `AgentConfigurationError`, `agent_error(...)`.
- Create `src/deep_research/agents/steps.py` — `StopReason`, `ReActActionType`, `ReActDecision`, `ReActObservation`, `ReActStep`, `ReActRun`, `summarize_text`, `parse_tool_input`.
- Create `src/deep_research/agents/toolset.py` — `ToolDescriptor`, `AgentToolset`.
- Create `src/deep_research/agents/prompts.py` — `AgentTask`, `REACT_RESPONSE_CONTRACT`, `render_tool_catalog`, `render_scratchpad`, `render_react_messages`.
- Create `src/deep_research/agents/react.py` — `run_react_loop` and its callback type aliases.
- Create `src/deep_research/agents/base.py` — `StructuredCompleter`, `AgentRun`, `BaseAgent`.
- Modify `src/deep_research/agents/__init__.py` — public exports (currently a stub docstring).
- Modify `README.md` and `tests/test_imports.py` — document and pin the public surface.
- Create `tests/agent_fakes.py` — shared `EchoTool`/`BoomTool`/`ScriptedCompleter`/`agent_scope` doubles (not collected: the filename does not match `python_files = ["test_*.py"]`).
- Create `tests/test_agents/__init__.py`, `tests/test_agents/conftest.py`.
- Create `tests/test_agents/test_steps.py`, `tests/test_agents/test_toolset.py`, `tests/test_agents/test_prompts.py`, `tests/test_agents/test_react.py`, `tests/test_agents/test_base.py`.

---

### Task 1: Agent Runtime Configuration And Error Contracts

**Files:**
- Modify: `src/deep_research/utils/config.py:74-113` (new model, `ConfigSettings` field, `_ENVIRONMENT_OVERRIDES`)
- Modify: `config.yaml` (append an `agents:` section)
- Modify: `tests/test_config.py:19-43` (fixture), `:199-236` (parametrize list), append one test
- Create: `src/deep_research/agents/errors.py`
- Create: `tests/test_agents/__init__.py`
- Create: `tests/test_agents/conftest.py`
- Create: `tests/test_agents/test_errors.py`

**Interfaces:**
- Consumes: `deep_research.utils.types.ResearchError`.
- Produces:
  - `AgentRuntimeConfig(max_iterations: int = 5, tool_budget: int = 10, prompt_context_entries: int = 8)` in `deep_research.utils.config`, reachable as `ConfigSettings.agents`
  - Environment overrides `AGENTS_MAX_ITERATIONS`, `AGENTS_TOOL_BUDGET`, `AGENTS_PROMPT_CONTEXT_ENTRIES`
  - `AgentError(Exception)`, `AgentConfigurationError(AgentError)`
  - `agent_error(*, agent_name: str, error_type: str, message: str, recoverable: bool = True, details: Mapping[str, JsonValue] | None = None) -> ResearchError`

- [ ] **Step 1: Write the failing configuration tests**

In `tests/test_config.py`, inside the `config_path` fixture, replace:

```python
                "output": {"directory": "output/", "default_format": "markdown"},
```

with:

```python
                "agents": {
                    "max_iterations": 5,
                    "tool_budget": 10,
                    "prompt_context_entries": 8,
                },
                "output": {"directory": "output/", "default_format": "markdown"},
```

In the `test_environment_overrides_every_yaml_leaf` parametrize list, add these three entries directly after the `MEMORY_PROCEDURAL_STRATEGIES_PATH` entry:

```python
        ("AGENTS_MAX_ITERATIONS", ("agents", "max_iterations"), "9", 9),
        ("AGENTS_TOOL_BUDGET", ("agents", "tool_budget"), "3", 3),
        (
            "AGENTS_PROMPT_CONTEXT_ENTRIES",
            ("agents", "prompt_context_entries"),
            "4",
            4,
        ),
```

Append to `tests/test_config.py`:

```python
def test_agent_runtime_defaults_bound_every_react_loop(config_path: Path) -> None:
    settings = load_config(str(config_path))

    assert settings.agents.max_iterations == 5
    assert settings.agents.tool_budget == 10
    assert settings.agents.prompt_context_entries == 8


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_iterations", 0),
        ("tool_budget", -1),
        ("prompt_context_entries", -1),
    ],
)
def test_agent_runtime_config_rejects_unbounded_values(
    field_name: str,
    invalid_value: int,
) -> None:
    """An agent loop with no upper bound is not a valid configuration."""
    from pydantic import ValidationError

    from deep_research.utils.config import AgentRuntimeConfig

    with pytest.raises(ValidationError):
        AgentRuntimeConfig(**{field_name: invalid_value})
```

- [ ] **Step 2: Run the config tests and verify they fail**

Run:

```bash
python -m pytest tests/test_config.py -v
```

Expected: `test_agent_runtime_defaults_bound_every_react_loop` fails with `AttributeError: 'ConfigSettings' object has no attribute 'agents'`, the three new override cases fail the same way, and `test_agent_runtime_config_rejects_unbounded_values` fails with `ImportError: cannot import name 'AgentRuntimeConfig'`.

- [ ] **Step 3: Add the agent runtime configuration section**

In `src/deep_research/utils/config.py`, add this class immediately after `MemoryConfig` and before `OutputConfig`:

```python
class AgentRuntimeConfig(BaseModel):
    """Bounds every ReAct agent runs under.

    ``tool_budget`` may be zero: an agent with no tools still gets to think
    and finish, it just may never call one.
    """

    max_iterations: int = Field(default=5, ge=1)
    tool_budget: int = Field(default=10, ge=0)
    prompt_context_entries: int = Field(default=8, ge=0)
```

Add the field to `ConfigSettings`, directly after `memory`:

```python
    agents: AgentRuntimeConfig = AgentRuntimeConfig()
```

In `_ENVIRONMENT_OVERRIDES`, add these three entries directly after `MEMORY_PROCEDURAL_STRATEGIES_PATH`:

```python
    "AGENTS_MAX_ITERATIONS": ("agents", "max_iterations"),
    "AGENTS_TOOL_BUDGET": ("agents", "tool_budget"),
    "AGENTS_PROMPT_CONTEXT_ENTRIES": ("agents", "prompt_context_entries"),
```

In `config.yaml`, add this section between the `memory:` block and the `output:` block:

```yaml
agents:
  max_iterations: 5
  tool_budget: 10
  prompt_context_entries: 8
```

- [ ] **Step 4: Run the config tests and verify they pass**

Run:

```bash
python -m pytest tests/test_config.py -v
```

Expected: every test passes, including the three new override cases.

- [ ] **Step 5: Write the failing agent error tests**

Create `tests/test_agents/__init__.py` as an empty file.

Create `tests/test_agents/conftest.py`:

```python
import pytest

from deep_research.observability import LangSmithRuntimeConfig, Tracker


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="agent-tests",
            api_key=None,
        )
    )
```

Create `tests/test_agents/test_errors.py`:

```python
"""Tests for agent exception contracts and structured error recording."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import (
    AgentConfigurationError,
    AgentError,
    agent_error,
)


def test_configuration_error_is_an_agent_error() -> None:
    assert issubclass(AgentConfigurationError, AgentError)
    assert issubclass(AgentError, Exception)


def test_agent_error_namespaces_the_source_by_agent_name() -> None:
    recorded = agent_error(
        agent_name="researcher",
        error_type="agent_unknown_tool",
        message="hallucinated_tool is not available to this agent.",
    )

    assert recorded.source == "agent.researcher"
    assert recorded.error_type == "agent_unknown_tool"
    assert recorded.recoverable is True
    assert recorded.details == {}


def test_agent_error_records_non_recoverable_failures_with_details() -> None:
    recorded = agent_error(
        agent_name="planner",
        error_type="agent_provider_error",
        message="The provider failed and the loop stopped.",
        recoverable=False,
        details={"iteration": 2, "exception_type": "ProviderTimeoutError"},
    )

    assert recorded.recoverable is False
    assert recorded.details == {
        "iteration": 2,
        "exception_type": "ProviderTimeoutError",
    }


def test_agent_error_rejects_a_blank_agent_name() -> None:
    with pytest.raises(ValueError, match="agent_name must not be blank"):
        agent_error(agent_name="   ", error_type="x", message="y")


def test_agent_error_copies_its_details_mapping() -> None:
    details: dict[str, int] = {"iteration": 1}

    recorded = agent_error(
        agent_name="researcher",
        error_type="agent_tool_failed",
        message="web_search failed.",
        details=details,
    )
    details["iteration"] = 99

    assert recorded.details == {"iteration": 1}
```

- [ ] **Step 6: Run the error tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_errors.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.agents.errors'`.

- [ ] **Step 7: Write the agent error contracts**

Create `src/deep_research/agents/errors.py`:

```python
"""Agent runtime exceptions and structured error recording."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.utils.types import ResearchError


class AgentError(Exception):
    """Base class for agent runtime failures."""


class AgentConfigurationError(AgentError):
    """An agent was assembled incorrectly. Not recoverable at runtime."""


def agent_error(
    *,
    agent_name: str,
    error_type: str,
    message: str,
    recoverable: bool = True,
    details: Mapping[str, JsonValue] | None = None,
) -> ResearchError:
    """Build one structured error attributed to a named agent.

    ``details`` must never contain ``str(exception)``: these records are
    copied into ``ResearchState.errors`` and provider text can carry keys,
    URLs, and paths. Record ``exception_type`` instead.
    """
    if not agent_name.strip():
        raise ValueError("agent_name must not be blank")
    return ResearchError(
        error_type=error_type,
        source=f"agent.{agent_name.strip()}",
        message=message,
        recoverable=recoverable,
        details=dict(details or {}),
    )
```

- [ ] **Step 8: Run the error tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents/test_errors.py tests/test_config.py -v
ruff check src/deep_research tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 9: Commit**

```bash
git add src/deep_research/agents/errors.py src/deep_research/utils/config.py config.yaml tests/test_agents tests/test_config.py
git commit -m "feat: add agent runtime config and error contracts"
```

---

### Task 2: ReAct Step Models And Pure Helpers

**Files:**
- Create: `src/deep_research/agents/steps.py`
- Create: `tests/test_agents/test_steps.py`

**Interfaces:**
- Consumes: `ContractModel`, `ResearchError` from `deep_research.utils.types`; `ToolResult` from `deep_research.tools.base`.
- Produces:
  - `StopReason = Literal["finished", "sufficient", "max_iterations", "tool_budget_exhausted", "provider_error"]`
  - `ReActActionType = Literal["use_tool", "finish"]`
  - `DEFAULT_SUMMARY_LIMIT = 200`
  - `summarize_text(text: str, *, limit: int = DEFAULT_SUMMARY_LIMIT) -> str`
  - `parse_tool_input(raw: str) -> dict[str, JsonValue]`
  - `ReActDecision(thought, action, tool_name=None, tool_input_json="{}", final_answer=None)` — the provider structured-output schema
  - `ReActObservation(tool_name, success, summary, latency_ms=0.0, error_type=None)`
  - `ReActStep(iteration, thought, action, tool_name=None, tool_input={}, observation=None, tool_result=None, final_answer=None)`
  - `ReActRun(agent_name, steps=[], stop_reason, iterations=0, tool_calls=0, final_answer=None, errors=[])` with a `succeeded: bool` property

- [ ] **Step 1: Write the failing step model tests**

Create `tests/test_agents/test_steps.py`:

```python
"""Tests for ReAct step contracts and their pure helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.steps import (
    DEFAULT_SUMMARY_LIMIT,
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    parse_tool_input,
    summarize_text,
)


def test_summary_limit_default_is_prompt_sized() -> None:
    assert DEFAULT_SUMMARY_LIMIT == 200


def test_summarize_text_collapses_whitespace() -> None:
    assert summarize_text("  a\n\n b\tc  ") == "a b c"


def test_summarize_text_truncates_with_an_ellipsis() -> None:
    summary = summarize_text("x" * 100, limit=10)

    assert summary == "xxxxxxx..."
    assert len(summary) == 10


def test_summarize_text_reports_empty_input_explicitly() -> None:
    assert summarize_text("   \n  ") == "(empty)"


def test_summarize_text_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be at least 1"):
        summarize_text("hello", limit=0)


def test_parse_tool_input_decodes_a_json_object() -> None:
    assert parse_tool_input('{"query": "qec", "max_results": 3}') == {
        "query": "qec",
        "max_results": 3,
    }


def test_parse_tool_input_treats_blank_input_as_no_arguments() -> None:
    assert parse_tool_input("") == {}
    assert parse_tool_input("{}") == {}


def test_parse_tool_input_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        parse_tool_input("{query: qec}")


def test_parse_tool_input_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_tool_input('["qec"]')


def test_parse_tool_input_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_tool_input('{"score": NaN}')


def test_use_tool_decision_carries_a_tool_and_encoded_arguments() -> None:
    decision = ReActDecision(
        thought="Search for benchmarks.",
        action="use_tool",
        tool_name="web_search",
        tool_input_json='{"query": "qec"}',
    )

    assert decision.tool_name == "web_search"
    assert parse_tool_input(decision.tool_input_json) == {"query": "qec"}


def test_use_tool_decision_defaults_to_no_arguments() -> None:
    decision = ReActDecision(
        thought="List everything.",
        action="use_tool",
        tool_name="echo",
    )

    assert decision.tool_input_json == "{}"


def test_finish_decision_carries_a_final_answer() -> None:
    decision = ReActDecision(
        thought="I have enough.",
        action="finish",
        final_answer="Error rates fell 30%.",
    )

    assert decision.final_answer == "Error rates fell 30%."
    assert decision.tool_name is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"thought": "t", "action": "use_tool"},
            "use_tool decisions require tool_name",
        ),
        (
            {
                "thought": "t",
                "action": "use_tool",
                "tool_name": "echo",
                "final_answer": "done",
            },
            "use_tool decisions must not carry final_answer",
        ),
        (
            {"thought": "t", "action": "finish"},
            "finish decisions require final_answer",
        ),
        (
            {
                "thought": "t",
                "action": "finish",
                "final_answer": "done",
                "tool_name": "echo",
            },
            "finish decisions must not name a tool",
        ),
        ({"thought": "", "action": "finish", "final_answer": "d"}, "thought"),
        ({"thought": "t", "action": "reflect"}, "action"),
    ],
)
def test_decision_rejects_inconsistent_action_shapes(
    payload: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        ReActDecision.model_validate(payload)


def test_decision_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReActDecision.model_validate(
            {
                "thought": "t",
                "action": "finish",
                "final_answer": "d",
                "confidence": 0.5,
            }
        )


def test_observation_records_a_failed_tool_call() -> None:
    observation = ReActObservation(
        tool_name="web_search",
        success=False,
        summary="web_search failed (TimeoutError): upstream timed out",
        latency_ms=12.5,
        error_type="TimeoutError",
    )

    assert observation.success is False
    assert observation.error_type == "TimeoutError"


def test_observation_rejects_a_negative_latency() -> None:
    with pytest.raises(ValidationError):
        ReActObservation(
            tool_name="echo",
            success=True,
            summary="ok",
            latency_ms=-1.0,
        )


def test_step_numbering_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        ReActStep(iteration=0, thought="t", action="finish", final_answer="d")


def test_run_reports_success_for_every_stop_reason_but_provider_error() -> None:
    def _run(stop_reason: str) -> ReActRun:
        return ReActRun.model_validate(
            {"agent_name": "researcher", "stop_reason": stop_reason}
        )

    assert _run("finished").succeeded is True
    assert _run("sufficient").succeeded is True
    assert _run("max_iterations").succeeded is True
    assert _run("tool_budget_exhausted").succeeded is True
    assert _run("provider_error").succeeded is False


def test_run_rejects_an_unknown_stop_reason() -> None:
    with pytest.raises(ValidationError):
        ReActRun(agent_name="researcher", stop_reason="gave_up")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_steps.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.agents.steps'`.

- [ ] **Step 3: Write the step models**

Create `src/deep_research/agents/steps.py`:

```python
"""Typed ReAct step records and the pure helpers that build them.

``ReActDecision`` doubles as the OpenAI structured-output schema for the
think/choose-action turn, so every field must survive strict JSON schema
conversion. That is why tool arguments travel as ``tool_input_json`` — a
JSON-encoded object — rather than as an open ``dict``, which strict mode
rejects.
"""

from __future__ import annotations

import json
from math import isfinite
from typing import Literal, TypeAlias

from pydantic import Field, JsonValue, model_validator

from deep_research.tools.base import ToolResult
from deep_research.utils.types import ContractModel, ResearchError

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


def _reject_json_constant(name: str) -> JsonValue:
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
    for key, value in parsed.items():
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"tool argument {key!r} must be a finite number")
    return parsed


class ReActDecision(ContractModel):
    """One think/choose-action turn, as returned by the provider."""

    thought: str = Field(min_length=1)
    action: ReActActionType
    tool_name: str | None = None
    tool_input_json: str = "{}"
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
    tool_input: dict[str, JsonValue] = Field(default_factory=dict)
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents/test_steps.py -v
ruff check src/deep_research/agents tests/test_agents
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/steps.py tests/test_agents/test_steps.py
git commit -m "feat: add ReAct step contracts"
```

---

### Task 3: Validated Tool Selection Path

**Files:**
- Create: `src/deep_research/agents/toolset.py`
- Create: `tests/agent_fakes.py`
- Create: `tests/test_agents/test_toolset.py`

**Interfaces:**
- Consumes: `BaseTool` from `deep_research.tools.base`; `AgentConfigurationError` from `deep_research.agents.errors`.
- Produces:
  - `ToolDescriptor(name, description, input_schema)` with `ToolDescriptor.from_tool(tool: BaseTool) -> ToolDescriptor`
  - `AgentToolset(tools: Sequence[BaseTool] = (), *, allowed: Sequence[str] = ())` with `names: tuple[str, ...]`, `get(name) -> BaseTool | None`, `descriptors() -> tuple[ToolDescriptor, ...]`, `__contains__`, `__len__`
- Also produces (test-only): `tests/agent_fakes.py` with `EchoTool`, `BoomTool`, `StrictEchoTool`, `ScriptedCompleter`, `use_tool`, `finish`, `agent_scope`.

- [ ] **Step 1: Write the shared test doubles**

Create `tests/agent_fakes.py`:

```python
"""Shared fakes for agent runtime tests.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel

from deep_research.agents.steps import ReActDecision
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)


class EchoTool(BaseTool):
    """Return the ``value`` keyword back to the agent."""

    name = "echo"
    description = "Echo one string back to the agent."
    input_schema = {"value": "string"}
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        return ToolExecution(
            data={"echo": kwargs["value"]},
            output_summary={"echoed": True},
        )


class BoomTool(BaseTool):
    """Always fail with a recoverable tool error."""

    name = "boom"
    description = "Always fail."
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        raise ToolExecutionError("upstream timed out", error_type="TimeoutError")


class StrictEchoTool(BaseTool):
    """Accept exactly one named argument, so bad arguments raise TypeError."""

    name = "strict_echo"
    description = "Echo one required string argument."
    input_schema = {"value": "string"}
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, *, value: str
    ) -> ToolExecution:
        return ToolExecution(data={"echo": value}, output_summary={"echoed": True})


class ScriptedCompleter:
    """Serve queued structured responses instead of calling OpenAI.

    ``ReActDecision`` requests pop from ``decisions``; every other schema pops
    from ``outputs``. A queued ``BaseException`` is raised instead of returned,
    which is how provider failures are simulated.
    """

    def __init__(
        self,
        decisions: Sequence[ReActDecision | BaseException] = (),
        outputs: Sequence[BaseModel | BaseException] = (),
    ) -> None:
        self._decisions: list[Any] = list(decisions)
        self._outputs: list[Any] = list(outputs)
        self.calls: list[tuple[str, str | None, list[ChatMessage]]] = []

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        agent_name: str | None = None,
    ) -> Any:
        self.calls.append((schema.__name__, agent_name, list(messages)))
        queue = self._decisions if schema is ReActDecision else self._outputs
        if not queue:
            raise AssertionError(f"no scripted response left for {schema.__name__}")
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def use_tool(
    thought: str,
    tool_name: str,
    tool_input_json: str = "{}",
) -> ReActDecision:
    return ReActDecision(
        thought=thought,
        action="use_tool",
        tool_name=tool_name,
        tool_input_json=tool_input_json,
    )


def finish(thought: str, final_answer: str) -> ReActDecision:
    return ReActDecision(thought=thought, action="finish", final_answer=final_answer)


@asynccontextmanager
async def agent_scope(
    tracker: Tracker,
    *,
    agent_name: str = "researcher",
    session_id: str = "session-1",
    question: str = "Why is the sky blue?",
) -> AsyncIterator[None]:
    """Open the session and agent spans a ReAct loop requires."""
    async with tracker.session_span(session_id, question):
        async with tracker.agent_span(agent_name):
            yield
```

- [ ] **Step 2: Write the failing toolset tests**

Create `tests/test_agents/test_toolset.py`:

```python
"""Tests for the per-agent tool selection path."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.toolset import AgentToolset, ToolDescriptor
from deep_research.observability import Tracker
from tests.agent_fakes import BoomTool, EchoTool


def test_descriptor_projects_tool_class_metadata(tracker: Tracker) -> None:
    descriptor = ToolDescriptor.from_tool(EchoTool(tracker))

    assert descriptor.name == "echo"
    assert descriptor.description == "Echo one string back to the agent."
    assert descriptor.input_schema == {"value": "string"}


def test_toolset_exposes_only_the_allowed_tools(tracker: Tracker) -> None:
    toolset = AgentToolset([EchoTool(tracker), BoomTool(tracker)], allowed=["echo"])

    assert toolset.names == ("echo",)
    assert len(toolset) == 1
    assert "echo" in toolset
    assert "boom" not in toolset
    assert toolset.get("boom") is None
    assert isinstance(toolset.get("echo"), EchoTool)


def test_toolset_preserves_the_declared_tool_order(tracker: Tracker) -> None:
    toolset = AgentToolset(
        [EchoTool(tracker), BoomTool(tracker)],
        allowed=["boom", "echo"],
    )

    assert toolset.names == ("boom", "echo")
    assert [descriptor.name for descriptor in toolset.descriptors()] == [
        "boom",
        "echo",
    ]


def test_toolset_rejects_an_allowed_tool_that_was_never_injected(
    tracker: Tracker,
) -> None:
    with pytest.raises(AgentConfigurationError, match="web_search"):
        AgentToolset([EchoTool(tracker)], allowed=["echo", "web_search"])


def test_toolset_rejects_duplicate_registry_names(tracker: Tracker) -> None:
    with pytest.raises(AgentConfigurationError, match="duplicate"):
        AgentToolset([EchoTool(tracker), EchoTool(tracker)], allowed=["echo"])


def test_toolset_rejects_duplicate_allowed_names(tracker: Tracker) -> None:
    with pytest.raises(AgentConfigurationError, match="duplicate"):
        AgentToolset([EchoTool(tracker)], allowed=["echo", "echo"])


def test_an_agent_with_no_allowed_tools_is_valid(tracker: Tracker) -> None:
    toolset = AgentToolset([EchoTool(tracker)], allowed=[])

    assert toolset.names == ()
    assert toolset.descriptors() == ()
    assert len(toolset) == 0
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_toolset.py -v
```

Expected: collection error â€” `ModuleNotFoundError: No module named 'deep_research.agents.toolset'`.

- [ ] **Step 4: Implement the toolset**

Create `src/deep_research/agents/toolset.py`:

```python
"""The validated, ordered view one agent has over the shared tool registry."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, JsonValue

from deep_research.agents.errors import AgentConfigurationError
from deep_research.tools.base import BaseTool
from deep_research.utils.types import ContractModel


class ToolDescriptor(ContractModel):
    """The prompt-facing projection of a tool's class metadata."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_tool(cls, tool: BaseTool) -> "ToolDescriptor":
        return cls(
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
        )


class AgentToolset:
    """The subset of injected tools one agent is permitted to call.

    Construction fails loudly: an agent that declares a tool nobody injected
    is a wiring mistake, not a runtime condition to be recovered from.
    """

    def __init__(
        self,
        tools: Sequence[BaseTool] = (),
        *,
        allowed: Sequence[str] = (),
    ) -> None:
        registry: dict[str, BaseTool] = {}
        for tool in tools:
            if tool.name in registry:
                raise AgentConfigurationError(
                    f"duplicate tool name in the registry: {tool.name}"
                )
            registry[tool.name] = tool

        selected: dict[str, BaseTool] = {}
        missing: list[str] = []
        for name in allowed:
            if name in selected or name in missing:
                raise AgentConfigurationError(f"duplicate allowed tool name: {name}")
            tool = registry.get(name)
            if tool is None:
                missing.append(name)
                continue
            selected[name] = tool
        if missing:
            names = ", ".join(missing)
            raise AgentConfigurationError(f"allowed tools were not injected: {names}")

        self._tools = selected

    @property
    def names(self) -> tuple[str, ...]:
        """Allowed tool names, in the order the agent declared them."""
        return tuple(self._tools)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(ToolDescriptor.from_tool(tool) for tool in self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
```

- [ ] **Step 5: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents/test_toolset.py -v
ruff check src/deep_research/agents tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/toolset.py tests/agent_fakes.py tests/test_agents/test_toolset.py
git commit -m "feat: add validated agent tool selection"
```

---

### Task 4: Prompt Rendering Boundary

**Files:**
- Create: `src/deep_research/agents/prompts.py`
- Create: `tests/test_agents/test_prompts.py`

**Interfaces:**
- Consumes: `ToolDescriptor` from `deep_research.agents.toolset`; `ScratchpadEntry` from `deep_research.memory.entries`; `ChatMessage` from `deep_research.providers`.
- Produces:
  - `AgentTask(instruction: str, guidance: str = "")`
  - `REACT_RESPONSE_CONTRACT: str`
  - `render_tool_catalog(descriptors: Sequence[ToolDescriptor]) -> str`
  - `render_scratchpad(entries: Sequence[ScratchpadEntry]) -> str`
  - `render_react_messages(*, system_prompt: str, task: AgentTask, descriptors: Sequence[ToolDescriptor], scratchpad: Sequence[ScratchpadEntry], iteration: int, max_iterations: int) -> list[ChatMessage]`

- [ ] **Step 1: Write the failing prompt tests**

Create `tests/test_agents/test_prompts.py`:

```python
"""Tests for the pure prompt rendering boundary."""

from __future__ import annotations

import pytest

from deep_research.agents.prompts import (
    AgentTask,
    render_react_messages,
    render_scratchpad,
    render_tool_catalog,
)
from deep_research.agents.toolset import ToolDescriptor
from deep_research.memory.entries import ScratchpadEntry


def _descriptor(name: str = "echo") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"Call {name}.",
        input_schema={"value": "string"},
    )


def _entry(content: str, kind: str = "thought") -> ScratchpadEntry:
    return ScratchpadEntry.model_validate(
        {"agent_name": "researcher", "kind": kind, "content": content}
    )


def test_tool_catalog_lists_name_description_and_arguments() -> None:
    catalog = render_tool_catalog([_descriptor("echo"), _descriptor("boom")])

    assert '- echo: Call echo. Arguments: {"value": "string"}' in catalog
    assert '- boom: Call boom. Arguments: {"value": "string"}' in catalog
    assert catalog.index("echo") < catalog.index("boom")


def test_tool_catalog_says_so_when_no_tool_is_allowed() -> None:
    assert render_tool_catalog([]) == "(no tools available)"


def test_scratchpad_renders_kind_prefixed_lines_oldest_first() -> None:
    rendered = render_scratchpad(
        [_entry("Search for benchmarks."), _entry("Found 5 results.", "observation")]
    )

    assert rendered == (
        "- [thought] Search for benchmarks.\n- [observation] Found 5 results."
    )


def test_scratchpad_says_so_when_empty() -> None:
    assert render_scratchpad([]) == "(no notes yet)"


def test_react_messages_open_with_the_agent_system_prompt() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        descriptors=[_descriptor()],
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )

    assert len(messages) == 2
    assert messages[0].role == "developer"
    assert messages[0].content == "You are a researcher."
    assert messages[1].role == "user"


def test_react_messages_carry_task_tools_notes_and_the_iteration_budget() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        descriptors=[_descriptor()],
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )
    body = messages[1].content

    assert "Summarize QEC progress." in body
    assert "Prefer 2025 sources." in body
    assert "- echo: Call echo." in body
    assert "- [thought] Search for benchmarks." in body
    assert "Iteration 2 of 3." in body
    assert "tool_input_json" in body


def test_react_messages_omit_the_guidance_section_when_it_is_blank() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        descriptors=[],
        scratchpad=[],
        iteration=1,
        max_iterations=1,
    )

    assert "## Guidance" not in messages[1].content


def test_react_messages_are_deterministic() -> None:
    def _render() -> list[str]:
        return [
            message.content
            for message in render_react_messages(
                system_prompt="You are a researcher.",
                task=AgentTask(instruction="Summarize QEC progress."),
                descriptors=[_descriptor()],
                scratchpad=[_entry("note")],
                iteration=1,
                max_iterations=3,
            )
        ]

    assert _render() == _render()


@pytest.mark.parametrize(
    ("iteration", "max_iterations", "match"),
    [
        (0, 3, "iteration must be at least 1"),
        (4, 3, "iteration must not exceed max_iterations"),
        (1, 0, "max_iterations must be at least 1"),
    ],
)
def test_react_messages_reject_an_impossible_iteration_budget(
    iteration: int, max_iterations: int, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        render_react_messages(
            system_prompt="You are a researcher.",
            task=AgentTask(instruction="Summarize QEC progress."),
            descriptors=[],
            scratchpad=[],
            iteration=iteration,
            max_iterations=max_iterations,
        )


def test_react_messages_reject_a_blank_system_prompt() -> None:
    with pytest.raises(ValueError, match="system_prompt must not be blank"):
        render_react_messages(
            system_prompt="   ",
            task=AgentTask(instruction="Summarize QEC progress."),
            descriptors=[],
            scratchpad=[],
            iteration=1,
            max_iterations=1,
        )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_prompts.py -v
```

Expected: collection error â€” `ModuleNotFoundError: No module named 'deep_research.agents.prompts'`.

- [ ] **Step 3: Implement the prompt boundary**

Create `src/deep_research/agents/prompts.py`:

```python
"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.toolset import ToolDescriptor
from deep_research.memory.entries import ScratchpadEntry
from deep_research.providers import ChatMessage
from deep_research.utils.types import ContractModel

REACT_RESPONSE_CONTRACT = (
    "Respond with one decision.\n"
    'Set action to "use_tool" to call exactly one listed tool: put its name in '
    "tool_name and its arguments in tool_input_json as a JSON object string "
    '(for example {"value": "hello"}). Use "{}" when the tool takes no '
    "arguments.\n"
    'Set action to "finish" when you can answer without another tool call: put '
    "the answer in final_answer and leave tool_name empty.\n"
    "Always explain the choice in thought."
)


class AgentTask(ContractModel):
    """What one agent has been asked to do on this run."""

    instruction: str = Field(min_length=1)
    guidance: str = ""


def render_tool_catalog(descriptors: Sequence[ToolDescriptor]) -> str:
    """Render the allowed tools as one line each, in declaration order."""
    if not descriptors:
        return "(no tools available)"
    return "\n".join(
        f"- {descriptor.name}: {descriptor.description} "
        f"Arguments: {json.dumps(descriptor.input_schema, sort_keys=True)}"
        for descriptor in descriptors
    )


def render_scratchpad(entries: Sequence[ScratchpadEntry]) -> str:
    """Render scratchpad notes oldest first, one kind-prefixed line each."""
    if not entries:
        return "(no notes yet)"
    return "\n".join(f"- [{entry.kind}] {entry.content}" for entry in entries)


def render_react_messages(
    *,
    system_prompt: str,
    task: AgentTask,
    descriptors: Sequence[ToolDescriptor],
    scratchpad: Sequence[ScratchpadEntry],
    iteration: int,
    max_iterations: int,
) -> list[ChatMessage]:
    """Build the two messages one ReAct turn sends to the provider."""
    if not system_prompt.strip():
        raise ValueError("system_prompt must not be blank")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if iteration < 1:
        raise ValueError("iteration must be at least 1")
    if iteration > max_iterations:
        raise ValueError("iteration must not exceed max_iterations")

    sections = [f"## Task\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Guidance\n{task.guidance}")
    sections.append(f"## Tools\n{render_tool_catalog(descriptors)}")
    sections.append(f"## Notes so far\n{render_scratchpad(scratchpad)}")
    sections.append(f"## Budget\nIteration {iteration} of {max_iterations}.")
    sections.append(f"## Response contract\n{REACT_RESPONSE_CONTRACT}")

    return [
        ChatMessage(role="developer", content=system_prompt),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents/test_prompts.py -v
ruff check src/deep_research/agents tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/prompts.py tests/test_agents/test_prompts.py
git commit -m "feat: add agent prompt rendering boundary"
```

---

### Task 5: Bounded ReAct Loop

**Files:**
- Create: `src/deep_research/agents/react.py`
- Create: `tests/test_agents/test_react.py`

**Interfaces:**
- Consumes: `Tracker` from `deep_research.observability`; `AgentToolset` from `deep_research.agents.toolset`; every model from `deep_research.agents.steps`; `agent_error` from `deep_research.agents.errors`; `OpenAIProviderError` from `deep_research.providers`; `ToolResult` from `deep_research.tools.base`.
- Produces:
  - `DecideCallback = Callable[[int, Sequence[ReActStep]], Awaitable[ReActDecision]]`
  - `StepCallback = Callable[[ReActStep], Awaitable[None]]`
  - `SufficiencyCallback = Callable[[Sequence[ReActStep]], bool]`
  - `run_react_loop(*, agent_name: str, tracker: Tracker, tools: AgentToolset, decide: DecideCallback, max_iterations: int, tool_budget: int, on_step: StepCallback | None = None, is_sufficient: SufficiencyCallback | None = None, summary_limit: int = DEFAULT_SUMMARY_LIMIT) -> ReActRun`

**Contract notes for the implementer:**
- `run_react_loop` must be called inside an active `agent_span`. It opens only `react_iteration_span`. `Tracker.react_iteration_span` already raises `RuntimeError` otherwise; do not duplicate that check.
- The `try:` that catches `OpenAIProviderError` sits **outside** `async with tracker.react_iteration_span(...)` so the span records the real failure before the loop converts it into a stop reason.
- Only `OpenAIProviderError` is caught. `TypeError`, `AttributeError`, `asyncio.CancelledError`, and every other exception propagate.

- [ ] **Step 1: Write the failing loop tests**

Create `tests/test_agents/test_react.py`:

```python
"""Tests for the bounded, tracker-instrumented ReAct loop."""

from __future__ import annotations

from collections.abc import Sequence

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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_react.py -v
```

Expected: collection error â€” `ModuleNotFoundError: No module named 'deep_research.agents.react'`.

- [ ] **Step 3: Implement the loop**

Create `src/deep_research/agents/react.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents/test_react.py -v
ruff check src/deep_research/agents tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Verify the loop is bounded under an adversarial decider**

Run:

```bash
python - <<'PY'
import asyncio

from deep_research.agents.react import run_react_loop
from deep_research.agents.toolset import AgentToolset
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from tests.agent_fakes import EchoTool, agent_scope, use_tool


async def main() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    tools = AgentToolset([EchoTool(tracker)], allowed=["echo"])

    async def never_finish(iteration, steps):
        return use_tool("Keep going forever.", "echo", '{"value": "x"}')

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=tools,
            decide=never_finish,
            max_iterations=6,
            tool_budget=3,
        )
    print(run.stop_reason, run.iterations, run.tool_calls)


asyncio.run(main())
PY
```

Expected: `tool_budget_exhausted 4 3`. A decider that never finishes still terminates, and the tool budget binds before the iteration cap.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/react.py tests/test_agents/test_react.py
git commit -m "feat: add bounded ReAct loop"
```

---

### Task 6: BaseAgent And Its Narrow Hooks

**Files:**
- Create: `src/deep_research/agents/base.py`
- Create: `tests/test_agents/test_base.py`

**Interfaces:**
- Consumes: everything built in Tasks 1-5, plus `ScratchpadMemory` from `deep_research.memory.scratchpad`, `ChatMessage` from `deep_research.providers`, `AgentRuntimeConfig` from `deep_research.utils.config`, `ResearchState`/`ResearchStateUpdate`/`ResearchError` from `deep_research.utils.types`.
- Produces:
  - `StructuredCompleter` â€” the `Protocol` the runtime needs from a chat provider: `async complete_structured(messages: Sequence[ChatMessage], schema: type[SchemaT], *, agent_name: str | None = None) -> SchemaT`
  - `AgentRun(agent_name: str, result: ResultT | None, react: ReActRun, errors: list[ResearchError], state_update: ResearchStateUpdate)` â€” a plain `@dataclass`, generic in `ResultT`
  - `BaseAgent(ABC, Generic[ResultT])` with:
    - ClassVars `name`, `description`, `allowed_tools: tuple[str, ...] = ()`
    - `__init__(*, provider: StructuredCompleter, tracker: Tracker, scratchpad: ScratchpadMemory, tools: Sequence[BaseTool] = (), config: AgentRuntimeConfig | None = None)`
    - properties `config`, `scratchpad`, `toolset`
    - abstract `output_schema: type[ResultT]` (property), `system_prompt(task) -> str`, `build_task(state) -> AgentTask`, `async finalize(task, run) -> ResultT | None`
    - overridable `is_sufficient(steps) -> bool` (default `False`), `state_update(result, run) -> ResearchStateUpdate` (default `{"errors": list(run.errors)}`)
    - `async run(state: ResearchState) -> AgentRun[ResultT]`
    - `async complete_output(messages: Sequence[ChatMessage]) -> ResultT`

- [ ] **Step 1: Write the failing BaseAgent tests**

Create `tests/test_agents/test_base.py`:

```python
"""Tests for the shared agent base class."""

from __future__ import annotations

from collections.abc import Sequence

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
async def test_run_records_the_stop_reason_on_the_agent_span(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter([finish("Nothing to look up.", "Rayleigh.")])
    agent = _agent(tracker, completer)

    async with tracker.session_span("session-1", "Why is the sky blue?"):
        await agent.run(_state())

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
    agent = SummaryAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=_pad(),
        tools=[EchoTool(tracker), BoomTool(tracker)],
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )
    agent.allowed_tools = ("echo", "boom")  # type: ignore[misc]

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
            scratchpad=_pad("   "),
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agents/test_base.py -v
```

Expected: collection error â€” `ModuleNotFoundError: No module named 'deep_research.agents.base'`.

- [ ] **Step 3: Implement BaseAgent**

Create `src/deep_research/agents/base.py`:

```python
"""The shared agent base class: state in, bounded ReAct loop, typed result out.

Concrete agents implement four hooks (``output_schema``, ``system_prompt``,
``build_task``, ``finalize``) and may override two more (``is_sufficient``,
``state_update``). Everything else â€” tracing, iteration control, tool
selection, scratchpad writes, error collection â€” lives here.

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
        self._provider = provider
        self._tracker = tracker
        self._scratchpad = scratchpad
        self._config = config or AgentRuntimeConfig()
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
        or reports override this. ``iteration`` is never returned â€” callers
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
            agent_name=self.name,
        )

    async def run(self, state: ResearchState) -> AgentRun[ResultT]:
        """Run one bounded ReAct loop and finalize its result."""
        task = self.build_task(state)

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            messages = render_react_messages(
                system_prompt=self.system_prompt(task),
                task=task,
                descriptors=self._toolset.descriptors(),
                scratchpad=self._scratchpad.recent(
                    self._config.prompt_context_entries
                ),
                iteration=iteration,
                max_iterations=self._config.max_iterations,
            )
            return await self._provider.complete_structured(
                messages,
                ReActDecision,
                agent_name=self.name,
            )

        async with self._tracker.agent_span(self.name) as span:
            react = await run_react_loop(
                agent_name=self.name,
                tracker=self._tracker,
                tools=self._toolset,
                decide=decide,
                max_iterations=self._config.max_iterations,
                tool_budget=self._config.tool_budget,
                on_step=self._record_step,
                is_sufficient=self.is_sufficient,
            )
            react = react.model_copy(
                update={
                    "errors": [*react.errors, *self._scratchpad.drain_errors()]
                }
            )
            result = await self.finalize(task, react)
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "stop_reason": react.stop_reason,
                    "iterations": react.iterations,
                    "tool_calls": react.tool_calls,
                    "produced_result": result is not None,
                }
            )

        return AgentRun(
            agent_name=self.name,
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agents -v
ruff check src/deep_research tests
```

Expected: every test in `tests/test_agents/` passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Verify the loop stays LangGraph-free**

Run:

```bash
grep -rn "langgraph\|fastapi\|streamlit" src/deep_research/agents/ || echo "no orchestration or UI dependency"
python -c "import deep_research.agents.base; import sys; print('openai' in sys.modules)"
```

Expected: `no orchestration or UI dependency`, then `False` â€” importing the agent runtime must not eagerly import the `openai` SDK.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/base.py tests/test_agents/test_base.py
git commit -m "feat: add BaseAgent ReAct runtime"
```

---

### Task 7: Public Exports, Documentation, And Full Verification

**Files:**
- Modify: `src/deep_research/agents/__init__.py:1`
- Modify: `tests/test_imports.py` (append)
- Modify: `README.md:8` (Project Status), add an Agent Runtime section after Memory, update the Phases list

**Interfaces:**
- Consumes: every public contract built in Tasks 1-6.
- Produces: the supported `deep_research.agents` import surface and its user-facing documentation.

- [ ] **Step 1: Write the failing public import test**

Append to `tests/test_imports.py`:

```python
def test_agent_runtime_contracts_import_from_package() -> None:
    from deep_research.agents import (  # noqa: F401
        AgentConfigurationError,
        AgentError,
        AgentRun,
        AgentTask,
        AgentToolset,
        BaseAgent,
        ReActActionType,
        ReActDecision,
        ReActObservation,
        ReActRun,
        ReActStep,
        StopReason,
        StructuredCompleter,
        ToolDescriptor,
        agent_error,
        parse_tool_input,
        render_react_messages,
        run_react_loop,
        summarize_text,
    )


def test_agent_runtime_config_imports_from_utils_config() -> None:
    from deep_research.utils.config import AgentRuntimeConfig

    assert AgentRuntimeConfig().max_iterations >= 1
```

- [ ] **Step 2: Run the import tests and verify they fail**

Run:

```bash
python -m pytest tests/test_imports.py -v
```

Expected: `test_agent_runtime_contracts_import_from_package` fails with `ImportError: cannot import name 'BaseAgent' from 'deep_research.agents'`.

- [ ] **Step 3: Export the agent runtime API**

Replace `src/deep_research/agents/__init__.py` with:

```python
"""The shared agent base class and its bounded ReAct runtime."""

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import (
    AgentConfigurationError,
    AgentError,
    agent_error,
)
from deep_research.agents.prompts import (
    REACT_RESPONSE_CONTRACT,
    AgentTask,
    render_react_messages,
    render_scratchpad,
    render_tool_catalog,
)
from deep_research.agents.react import (
    DecideCallback,
    StepCallback,
    SufficiencyCallback,
    run_react_loop,
)
from deep_research.agents.steps import (
    DEFAULT_SUMMARY_LIMIT,
    ReActActionType,
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    StopReason,
    parse_tool_input,
    summarize_text,
)
from deep_research.agents.toolset import AgentToolset, ToolDescriptor

__all__ = [
    "DEFAULT_SUMMARY_LIMIT",
    "REACT_RESPONSE_CONTRACT",
    "AgentConfigurationError",
    "AgentError",
    "AgentRun",
    "AgentTask",
    "AgentToolset",
    "BaseAgent",
    "DecideCallback",
    "ReActActionType",
    "ReActDecision",
    "ReActObservation",
    "ReActRun",
    "ReActStep",
    "StepCallback",
    "StopReason",
    "StructuredCompleter",
    "SufficiencyCallback",
    "ToolDescriptor",
    "agent_error",
    "parse_tool_input",
    "render_react_messages",
    "render_scratchpad",
    "render_tool_catalog",
    "run_react_loop",
    "summarize_text",
]
```

- [ ] **Step 4: Run the import tests and verify they pass**

Run:

```bash
python -m pytest tests/test_imports.py -v
```

Expected: all import tests pass.

- [ ] **Step 5: Document the agent runtime**

In `README.md`, replace the Project Status line:

```markdown
Foundation phase â€” package skeleton, typed configuration/state, the LangSmith observability foundation, OpenAI chat/embedding providers, core tools, and the three-layer memory stack.
```

with:

```markdown
Foundation phase â€” package skeleton, typed configuration/state, the LangSmith observability foundation, OpenAI chat/embedding providers, core tools, the three-layer memory stack, and the shared agent ReAct runtime.
```

Add this section after the Memory section and before `## Development`. The outer fence below is four backticks so the nested Python block survives copy-paste; write only the inner content into `README.md`:

````markdown
## Agent Runtime

`BaseAgent` runs a bounded ReAct loop â€” prepare context, think, choose an
action, execute a tool, observe, update the scratchpad, stop or continue. It
has no LangGraph dependency: a concrete agent is a plain async object.

A concrete agent implements four hooks and may override two more:

| Hook | Required | Purpose |
| --- | --- | --- |
| `output_schema` | yes | The Pydantic model the agent produces |
| `system_prompt(task)` | yes | Developer-role instructions |
| `build_task(state)` | yes | Read `ResearchState`, describe this run |
| `finalize(task, run)` | yes | Turn the finished loop into the typed output |
| `allowed_tools` | no | Tool names this agent may call (default: none) |
| `is_sufficient(steps)` | no | Stop early (default: never) |
| `state_update(result, run)` | no | Describe the state change (default: errors only) |

```python
from deep_research.agents import AgentTask, BaseAgent, ReActRun
from deep_research.memory import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import OpenAIChatProvider
from deep_research.tools import WebSearchTool
from deep_research.utils.config import load_config
from deep_research.utils.types import merge_research_state

settings = load_config("config.yaml")
tracker = Tracker.from_config(settings.langsmith)


class BriefAgent(BaseAgent[Brief]):
    name = "brief"
    description = "Answer one question from web search."
    allowed_tools = ("web_search",)

    @property
    def output_schema(self) -> type[Brief]:
        return Brief

    def system_prompt(self, task: AgentTask) -> str:
        return "You are a careful researcher."

    def build_task(self, state) -> AgentTask:
        return AgentTask(instruction=state.original_question)

    async def finalize(self, task: AgentTask, run: ReActRun) -> Brief | None:
        return None if run.final_answer is None else Brief(text=run.final_answer)


agent = BriefAgent(
    provider=OpenAIChatProvider(settings.llm, tracker),
    tracker=tracker,
    scratchpad=ScratchpadMemory.from_config(
        settings.memory.short_term, session_id="session-123", agent_name="brief"
    ),
    tools=[WebSearchTool(tracker, api_key=tavily_key)],
    config=settings.agents,
)

async with tracker.session_span("session-123", state.original_question):
    outcome = await agent.run(state)

state = merge_research_state(state, outcome.state_update)
```

Loops are bounded by `agents.max_iterations` and `agents.tool_budget` in
`config.yaml` (`AGENTS_MAX_ITERATIONS`, `AGENTS_TOOL_BUDGET` override them).
`outcome.react.stop_reason` is one of `finished`, `sufficient`,
`max_iterations`, `tool_budget_exhausted`, or `provider_error`.

Failures are predictable: a tool failure becomes an observation the model can
react to plus a recoverable `ResearchError`, and the loop continues. A model
provider failure stops the loop with `provider_error`, records a
non-recoverable `ResearchError`, and yields `outcome.result is None` â€” the
provider has already applied its configured retries and its single structured
repair attempt, so the agent adds none of its own.

Every iteration opens a `react_iteration_span` carrying agent name, iteration
number, thought summary, selected tool, and observation summary; the agent
span carries the stop reason and counts; token and latency metrics come from
the provider's own `llm_span`.
````

In the Phases list, replace:

```markdown
- Phase 2: Memory and tools â† current (complete)
- Phase 3: Agents and LangGraph orchestration
```

with:

```markdown
- Phase 2: Memory and tools
- Phase 3: Agents and LangGraph orchestration â† current (shared runtime complete, concrete agents pending)
```

- [ ] **Step 6: Run the full verification suite**

Run:

```bash
python -m pytest -v
ruff check src tests
git diff --check
```

Expected: the complete suite passes with no skips, Ruff prints `All checks passed!`, and `git diff --check` prints no output.

- [ ] **Step 7: Verify every acceptance criterion**

Run:

```bash
python -m pytest tests/test_agents/test_base.py -q
python -m pytest tests/test_agents/test_react.py -q
grep -rn "langgraph" src/deep_research/agents/ || echo "criterion 3: no LangGraph dependency"
git status --short
```

Expected:
- Criterion 1 (concrete agents built from narrow hooks): `tests/test_agents/test_base.py` defines `SummaryAgent`, `SchemaAgent`, and `SufficientAgent` using only the documented hooks, and they pass.
- Criterion 2 (bounded, testable loops): `tests/test_agents/test_react.py` passes, including the max-iteration, tool-budget, and sufficiency stops.
- Criterion 3 (no LangGraph): the grep prints the fallback message.
- Criterion 4 (predictable tool and provider failures): the tool-failure, unknown-tool, invalid-argument, and provider-error tests in both files pass.
- `git status --short` shows no stray files.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/agents/__init__.py tests/test_imports.py README.md
git commit -m "docs: publish agent ReAct runtime API"
```

- [ ] **Step 9: Verify the branch is clean**

Run:

```bash
git status --short
```

Expected: no output.

---

## Spec Coverage Map

| Spec requirement | Task |
| --- | --- |
| `BaseAgent` owning name, provider, tool registry, scratchpad, tracker, agent config | 6 |
| Agent configuration models | 1 |
| ReAct step models | 2 |
| Generic ReAct loop helper | 5 |
| Prompt rendering boundary | 4 |
| Tool selection and execution path | 3 (selection), 5 (execution) |
| Loop shape: context â†’ think â†’ act â†’ execute â†’ observe â†’ update â†’ stop/continue | 5, 6 |
| Stop: agent-specific sufficiency check | 5 (`is_sufficient` callback), 6 (`BaseAgent.is_sufficient` hook) |
| Stop: max ReAct iterations | 5 |
| Stop: tool budget exhausted | 5 |
| Stop: non-recoverable configuration or provider error | 1 (`AgentConfigurationError`), 3 (raised at wiring time), 5 (`provider_error`) |
| Concrete agents provide prompt templates | 6 (`system_prompt`) |
| Concrete agents provide allowed tools | 3 + 6 (`allowed_tools` ClassVar) |
| Concrete agents provide state read/write behavior | 6 (`build_task`, `state_update`) |
| Concrete agents provide sufficiency criteria | 6 (`is_sufficient`) |
| Concrete agents provide structured output schema | 6 (`output_schema`, `complete_output`) |
| Observability: agent name, iteration number, thought summary, tool selected, observation summary | 5 (iteration span outputs) |
| Observability: stop reason | 6 (agent span outputs) |
| Observability: token and latency metrics | 5 (iteration `AgentMetric` latency), inherited `TokenUsageMetric` from the provider's `llm_span` |
| Error handling: tool failures are observations | 5 |
| Error handling: provider failures retry once per provider config | Consumed, not re-implemented â€” documented in Global Constraints and asserted in 5 and 6 |
| Error handling: invalid agent output triggers one structured repair attempt | Consumed from `complete_structured`; the post-repair `StructuredOutputError` path is covered in 5 |
| Test: successful one-step ReAct loop | 5, 6 |
| Test: multi-step loop | 5, 6 |
| Test: max iteration stop | 5 |
| Test: tool failure as observation | 5, 6 |
| Test: invalid action handling | 5 (unknown tool, malformed arguments, rejected kwargs) |
| Test: scratchpad updates | 6 |
| Test: observability calls | 5, 6 |
| AC: concrete agents built by implementing narrow hooks | 6, 7 Step 7 |
| AC: ReAct loops are bounded and testable | 5 Step 5, 7 Step 7 |
| AC: agent runtime does not depend on LangGraph | 6 Step 5, 7 Step 7 |
| AC: tool and provider failures have predictable behavior | 5, 6, 7 Step 7 |

## Self-Review

**Spec coverage.** Every line of `docs/superpowers/specs/2026-07-25-07-base-agent-react-runtime-design.md` maps to a task in the table above, including all six scope bullets, all four stop conditions, all five concrete-agent hooks, all seven observability fields, all three error-handling rules, all seven required tests, and all four acceptance criteria. The three Non-Goals are enforced: no concrete Planner/Researcher/evaluator/fact-checker/synthesizer/critic exists (the only `BaseAgent` subclasses are the three test doubles inside `tests/test_agents/test_base.py`); no LangGraph import exists and Task 6 Step 5 plus Task 7 Step 7 grep for it; no UI or API code is added and `pyproject.toml` is untouched.

**No placeholders.** Every task ships complete, runnable code. There is no `pass`, no `...` body, no `TODO`, and no "implement this later" in any snippet. The two `raise NotImplementedError` occurrences are the intended bodies of `Protocol` and `@abstractmethod` declarations, matching the existing convention in `deep_research/tools/base.py` and `deep_research/observability/tracker.py`. Every `README.md` code block is either fully executable or explicitly framed as a subclass sketch. Each task's tests are written before its implementation, run to confirm failure with a named expected error, then run again to confirm success, and each ends in a commit.

**Type and signature consistency across tasks.** Cross-checked by hand:

- `AgentRuntimeConfig(max_iterations, tool_budget, prompt_context_entries)` is defined once in Task 1 and consumed unchanged in Tasks 6 and 7. `tool_budget` is `ge=0` in config and `tool_budget < 0` is the loop's rejection in Task 5 â€” the two bounds agree, and Task 5's zero-budget test proves the boundary is reachable.
- `agent_error(*, agent_name, error_type, message, recoverable=True, details=None)` (Task 1) is called with exactly those keywords in Task 5's five call sites.
- `summarize_text(text, *, limit)` and `parse_tool_input(raw) -> dict[str, JsonValue]` (Task 2) are called with matching signatures in Task 5. `parse_tool_input` raises `ValueError`, which is exactly what Task 5's `except ValueError` catches.
- `ReActDecision.tool_input_json` is a `str` in Task 2, produced as a `str` by `use_tool(...)` in Task 3's fakes, decoded by `parse_tool_input` in Task 5, and described as a JSON object string by `REACT_RESPONSE_CONTRACT` in Task 4. All four agree.
- `ReActStep.tool_input` is `dict[str, JsonValue]` and receives `parse_tool_input`'s return type. `ReActStep.tool_result` is `ToolResult | None`, matching `BaseTool.execute`'s return type exactly.
- `ReActObservation.error_type` is `str | None`; Task 5 sets it from `ToolError.type` (a non-empty `str`) or from one of three `agent_*` literals, never from an empty string.
- `AgentToolset(tools, *, allowed)` (Task 3) is constructed in Task 6 as `AgentToolset(tools, allowed=self.allowed_tools)` where `allowed_tools` is `ClassVar[tuple[str, ...]]` â€” a `Sequence[str]`, matching the parameter type. `toolset.get(name) -> BaseTool | None` and `toolset.names -> tuple[str, ...]` are used with those exact types in Task 5.
- `render_react_messages(...) -> list[ChatMessage]` (Task 4) is passed straight to `complete_structured(messages: Sequence[ChatMessage], ...)` (existing provider signature) in Task 6. `AgentTask` is constructed in Task 6's `build_task` and consumed by Task 4's renderer with the same field names.
- `run_react_loop(...)`'s `decide`, `on_step`, and `is_sufficient` parameter types (Task 5) match what Task 6 binds: `decide(iteration: int, steps: Sequence[ReActStep]) -> ReActDecision` async, `_record_step(step: ReActStep) -> None` async, `is_sufficient(steps: Sequence[ReActStep]) -> bool` sync.
- `ScratchpadMemory.add(content, *, kind, metadata)` is called in Task 6 with `kind` values `"thought"`, `"observation"`, `"decision"` â€” all members of the existing `ScratchpadEntryKind` literal in `deep_research/memory/entries.py`. `recent(count)` takes `int | None` and receives `prompt_context_entries: int` (`ge=0`), and `recent(0)` returns `()` rather than raising.
- `Tracker.react_iteration_span(iteration)` and `Tracker.agent_span(name)` are used with their existing signatures; `SpanHandle.set_outputs(Mapping[str, JsonValue])` receives only `str | int | bool | None` values in both Task 5 and Task 6.
- `ResearchStateUpdate` is a `TypedDict` whose `errors` key is `list[ResearchError]`; `state_update`'s default returns exactly that and never returns `"iteration"`, which `merge_research_state` rejects.
- `BaseAgent[ResultT]`'s `output_schema -> type[ResultT]`, `finalize(...) -> ResultT | None`, `complete_output(...) -> ResultT`, and `AgentRun.result: ResultT | None` are consistent, and Task 6's `SummaryAgent(BaseAgent[Summary])` binds `ResultT = Summary` at all four points.

**Known gaps deliberately left open.** Five items are flagged for human review rather than guessed: the `tool_input_json` encoding (Known Risks 1), the untested-against-live-OpenAI schema (Known Risks 2), the failed-tool-call budget accounting (Known Risks 4), whether `StructuredCompleter` should also expose plain `complete()` (Design Trade-Offs), and whether `AgentRuntimeConfig` should support per-agent overrides in the style of `LLMConfig.model_overrides` (Known Risks 5, deferred as YAGNI until a second agent needs different bounds).
