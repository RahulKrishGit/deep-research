# LangGraph Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the six existing agents into a LangGraph state graph that runs `Planner → Researcher → Source Evaluator → Fact Checker → Synthesizer → Critic`, loops back to the Researcher on the Critic's recommendation while macro-iteration budget remains, forces an end when it does not, records graph-level events and route decisions, surfaces session metadata and final status on the existing LangSmith session span, and can resume a session from a checkpoint.

**Architecture:** The graph is a thin wiring layer over agents that already work. Its LangGraph channel carries the whole `ResearchState` as one JSON-safe mapping under a single key, and every node merges with the project's own `merge_research_state` rather than per-field LangGraph reducers — the append/replace rules and the "`iteration` moves only through `advance_research_iteration`" guard are already implemented and tested there, and a second copy expressed as channel annotations could drift from the first. Routing is a pure function of state (`graph_route`) called by the conditional edge, so the loop bound is decided by code that can be unit-tested without compiling a graph. The macro-iteration increment happens in its own `refine` node, because a LangGraph conditional edge routes but cannot write. Failure handling is a **halt discipline**: an enumerated set of graph error types marks a run dead, every downstream node sees the mark and skips its agent, and the router sends the run to `END` with status `failed` — so a non-recoverable failure ends the run *with its state intact* instead of raising out of `ainvoke` and losing everything collected.

**Tech Stack:** Python 3.11+, LangGraph 1.2+ (`StateGraph`, `START`/`END`, `add_conditional_edges`, `InMemorySaver`), Pydantic 2, LangSmith SDK 0.10+ (through the existing `Tracker`), pytest, pytest-asyncio (strict mode — every async test needs `@pytest.mark.asyncio`), Ruff.

## Global Constraints

- Preserve `requires-python = ">=3.11"`. This plan adds exactly two runtime dependencies to `pyproject.toml`: `langgraph>=1.2` and `langgraph-checkpoint>=4.1`. Nothing else.
- **No new agent behavior.** Spec 11's non-goals are explicit: no new agent behavior, no UI, no API, no production distributed execution. Nothing under `src/deep_research/agents/` changes in this plan. `src/deep_research/main.py` is *not* touched — `run_research()` is spec 12's (CLI) job.
- No FastAPI, Streamlit, or CLI import anywhere. No new tool, no new prompt, no new provider call.
- LangGraph is imported **only** in `src/deep_research/graph/orchestrator.py` and in `tests/test_graph/test_langgraph_contract.py`. `graph/state.py`, `graph/errors.py`, `graph/events.py`, and `graph/nodes.py` stay framework-free so the routing and halt rules are unit-testable without compiling a graph.
- LangSmith spans and session metadata go through the existing `Tracker` only. Do not add a second tracing path, do not call `langsmith` directly, and do not add a `node_span` to `Tracker` — node visibility comes from the agent's own `agent_span` plus LangGraph's native tracing.
- Recorded `ResearchError.details` and `ResearchEvent.metadata` carry counts, identifiers, and enumerated reasons only — never `str(exception)` and never raw provider text. Record `exception_type` instead.
- Every graph error type this plan can record is enumerated in `GRAPH_ERROR_REASONS`; `graph_error` refuses any type outside it. Every routing reason is enumerated in `GRAPH_ROUTES`; every final status is in `GRAPH_STATUSES`.
- **The iteration bound is checked by the graph itself, not read off `Critique`.** `Critique.should_continue` is the critic's recommendation; `state.iteration >= state.max_iterations` is the graph's law and is evaluated in `graph_route` independently of anything a model said.
- Every model that is a contract stays a `ContractModel` subclass (`extra="forbid"`, `str_strip_whitespace=True`, `validate_default=True`). The graph adds no new domain contract — it reuses `ResearchState`, `ResearchStateUpdate`, `ResearchEvent`, and `ResearchError` unchanged.
- Ruff `select = ["E", "F", "I"]`, line length 88. Imports must be isort-ordered.
- **No test may make a real OpenAI, Tavily, LangSmith, ChromaDB, or HTTP network call.** Graph tests use `FakeAgent` doubles from `tests/graph_fakes.py`, never a real agent, and every test constructs `Tracker(LangSmithRuntimeConfig(tracing_enabled=False, ...))` through the `tracker` fixture. There is no `.env`, no `OPENAI_API_KEY`, and no `TAVILY_API_KEY` in this environment; a task that needs one is a plan defect.
- `tests/test_imports.py` walks a hard-coded submodule list and asserts every public module-level name reaches the package `__all__`. Task 7 adds the same discipline for `deep_research.graph`. Every new public constant, function, class, and type alias must be in both the import list and `__all__`.

## Decisions And Assumptions

Recorded here because the spec does not settle them.

1. **The LangGraph channel holds one key.** `ResearchGraphState` is `TypedDict("ResearchGraphState", {"state": dict[str, JsonValue]})`, and each node returns a whole replacement dump. The alternative — annotating each `ResearchState` field with a LangGraph reducer — would require dragging `Annotated[..., operator.add]` into `utils/types.py`, which is the project's framework-free contract module, and would re-implement `merge_research_state` in a second place where the "iteration cannot be set directly" guard cannot be expressed. The graph is strictly sequential with one writer per superstep, so last-write-wins on a single channel is correct.
2. **The channel stores a JSON-safe dump, not the `ResearchState` object.** `dump_state` calls `model_dump(mode="json")` and `load_state` calls `model_validate`. LangGraph's checkpoint serializer *does* handle Pydantic v2 models, but it revives them by importing the class and warns that unregistered types "will be blocked in a future version" (and `LANGGRAPH_STRICT_MSGPACK=true` blocks them today). Storing primitives sidesteps the allowlist entirely, keeps checkpoints inspectable, and costs one validation per node boundary.
3. **The macro-iteration increment lives in a dedicated `refine` node.** A LangGraph conditional edge chooses a destination; it cannot update state. Putting the increment in a node makes it a visible, independently testable step and keeps the Researcher node identical on the first pass and on every refinement. The spec's logical route `Critic → Researcher` is preserved; `refine` is the hop that carries the increment.
4. **The graph reads `Critique.should_continue` and then re-checks the bound itself.** `graph_route` is: halted → `END`; no critique → `END`; `should_continue` false → `END`; `iteration >= max_iterations` → `END`; otherwise → `refine`. It deliberately does *not* re-run `critic.route_decision`, because that would duplicate the critic's quality thresholds (`ACCEPTANCE_SCORE`, gap precedence) in a second module. The one rule the graph owns outright is the bound.
5. **Failure is a halt mark in state, not an exception out of `ainvoke`.** `HALTING_ERROR_TYPES` enumerates the five graph error types that stop a run. `is_halted(state)` is true when any recorded error carries one of them. Downstream nodes see the mark, record `graph.node.skipped`, and return without invoking their agent. This is *not* "any error with `recoverable=False`": agents already record non-recoverable provider failures (`critic_review_provider_error`, `researcher_extraction_provider_error`) that a research pass is expected to survive, and treating those as halts would end a run over a single blip.
6. **Only three exception types are converted into halts:** `PlanningError`, `AgentConfigurationError`, and `ProviderConfigurationError`. Anything else raised by an agent propagates out of `ainvoke`. An unhandled exception is a defect, not a research outcome, and silently converting it into a recorded error would hide it.
7. **"An agent returns invalid state" means `merge_research_state` rejected the update.** That function already enforces the whole contract — unknown fields, a non-list append, a direct `iteration` write, and every Pydantic constraint. The node catches `TypeError` and `ValueError` (which `pydantic.ValidationError` subclasses) from the merge and records `graph_invalid_agent_state`, which halts.
8. **Node spans are the agents' own `agent_span`s.** `Tracker` has no generic node span and this plan does not add one; the constraint against a second tracing path is explicit. Node names equal agent names, so a LangSmith trace shows `agent.planner`, `agent.researcher`, … nested under `research.session`, plus LangGraph's own native node spans. `run_research_graph` opens the session span and attaches session metadata, the list of route decisions, and the final status through `span.set_outputs`.
9. **`session_started_event` is written into the initial state before `ainvoke` (so it is checkpointed); `session_completed_event` is appended to the returned state after `ainvoke` (so it is not).** The checkpoint is written by the graph, not by the runner; a completion event added after the graph finished belongs to the run, not to the durable state.
10. **Checkpointing uses `InMemorySaver`, and the checkpointer is injectable.** Spec 11's non-goals rule out production distributed execution and its testing section asks for "resume hook behavior with mocked checkpointing". A durable saver (`langgraph-checkpoint-sqlite`, Postgres) is a hardening concern for spec 15 and drops into `compile_research_graph(agents, checkpointer=...)` without touching a node.
11. **The graph performs no memory recall.** `initial_graph_state` accepts an optional `memory_context: MemorySnapshot` from its caller. Session-start recall touches ChromaDB and OpenAI embeddings, which no test in this plan may do, and spec 11's scope list does not include it. Specs 12–13 populate it when they own a real session.
12. **`recursion_limit` is always set explicitly**, derived as `(max_iterations + 1) * len(NODE_NAMES) + 10`. LangGraph 1.2 defaults it to 10007, but earlier releases defaulted to 25 — below what four macro passes over seven nodes need — and an explicit bound documents the graph's real shape.
13. **`GraphConfig` lands in `utils/config.py` beside `AgentRuntimeConfig`**, with `max_iterations` and `checkpointing_enabled`, plus `GRAPH_MAX_ITERATIONS` / `GRAPH_CHECKPOINTING_ENABLED` environment overrides and a `graph:` block in `config.yaml`. No `recursion_limit` setting: it is derived, and a second knob that can contradict `max_iterations` is a bug waiting to happen.
14. **Node wrappers depend on a `ResearchAgent` Protocol (`name: str`, `async run(state) -> AgentRun`), not on `BaseAgent`.** Every concrete agent already satisfies it, and it is what lets graph tests use two-line fakes with no provider, tracker, scratchpad, or toolset.

## Design Trade-Offs

- **One-key channel vs. LangGraph reducers.** Reducers are the idiomatic LangGraph answer and would let LangGraph itself express "findings append, report replaces". They lose on two counts here: they cannot express the `iteration` guard, and they would put a framework import inside `utils/types.py`, which every layer of this codebase depends on and which deliberately imports nothing but Pydantic. A single channel plus `merge_research_state` keeps exactly one implementation of the merge rules, already covered by `tests/test_state.py`.
- **`graph_route` is called twice per critic node — once by the node to record the decision, once by the conditional edge to act on it.** A LangGraph router is expected to be a pure read of state, so it cannot append the event itself. Calling one pure function twice on unchanged state is deterministic by construction and cheaper than threading a second channel key through the graph just to carry a string.
- **Halt-and-skip vs. `Command(goto=END)`.** LangGraph 1.x supports `Command` returns for dynamic routing, which would let a failing node jump straight to `END`. Halt-and-skip needs no extra framework surface, keeps every node's signature identical, and is testable by calling a node function directly with a halted state — no compiled graph required. The cost is a handful of no-op supersteps after a failure, which is nothing next to a research pass.
- **`graph/state.py`, `errors.py`, `events.py`, `nodes.py` are framework-free; only `orchestrator.py` imports LangGraph.** This mirrors the split the agents package already made between pure modules (`report.py`, `sources.py`, `steps.py`) and I/O modules. It means the two rules that matter most — routing and halting — are covered by fast tests that would still pass if the graph framework were replaced.
- **`ResearchAgents` is a frozen dataclass, not a dict.** `build_research_graph(agents)` gets a typed six-field signature instead of a stringly-typed mapping, so forgetting the Fact Checker is a `TypeError` at construction rather than a `KeyError` deep inside graph assembly.
- **Task 1 is a characterization test of LangGraph itself.** It tests no project code. It exists because the plan's entire shape rests on four framework behaviors — single-key dict channels, conditional edges with a `path_map`, resume by `thread_id`, and what `aget_state` does for an unknown thread — and discovering a difference in Task 6 costs far more than discovering it in Task 1.

## File Structure

- Modify `pyproject.toml` — add `langgraph>=1.2` and `langgraph-checkpoint>=4.1`.
- Create `tests/test_graph/__init__.py`, `tests/test_graph/conftest.py` — package marker and the `tracker` fixture.
- Create `tests/test_graph/test_langgraph_contract.py` — characterization tests pinning the framework surface.
- Create `src/deep_research/graph/state.py` — node names, route reasons, statuses, halting error types, the channel TypedDict, `load_state` / `dump_state` / `initial_graph_state`, `is_halted`, `graph_route`, `graph_status`, `graph_recursion_limit`. Pure.
- Create `tests/test_graph/test_state.py`.
- Create `src/deep_research/graph/errors.py` — `GraphError` / `GraphConfigurationError` / `GraphResumeError`, `GRAPH_ERROR_REASONS`, `graph_error`, and the five named error builders. Pure.
- Create `src/deep_research/graph/events.py` — `graph_event` and the seven graph event builders. Pure.
- Create `tests/test_graph/test_records.py` — covers both of the above.
- Create `src/deep_research/graph/nodes.py` — `ResearchAgent` Protocol, `GraphNode` alias, `agent_node`, `critic_node`, `refine_node`, `route_after_critic`. Framework-free.
- Create `tests/graph_fakes.py` — `FakeAgent` and domain builders (extended in Task 5 with `fake_research_agents`).
- Create `tests/test_graph/test_nodes.py`.
- Create `src/deep_research/graph/orchestrator.py` — `ResearchAgents`, `AGENT_NODE_ORDER`, `build_research_graph`, `compile_research_graph`, `build_checkpointer`, `session_config`, `GraphRun`, `run_research_graph`, `resume_research_graph`. The only LangGraph importer.
- Create `tests/test_graph/test_orchestrator.py` — happy path, loop-back, force end, agent failure, state merge.
- Modify `src/deep_research/utils/config.py` — `GraphConfig`, `ConfigSettings.graph`, two environment overrides.
- Modify `config.yaml` — a `graph:` block.
- Modify `tests/test_config.py` — graph settings and overrides.
- Create `tests/test_graph/test_session.py` — `run_research_graph`, trace metadata, span propagation, checkpointing, resume.
- Modify `src/deep_research/graph/__init__.py` — public exports for all five modules.
- Modify `tests/test_imports.py` — graph import list, graph `__all__` coverage, graph submodule walk.
- Modify `README.md` — orchestration section, event table, phase line.

---

### Task 1: Add LangGraph And Pin Its Contract

**Files:**
- Modify: `pyproject.toml:10-21` (`[project].dependencies`)
- Create: `tests/test_graph/__init__.py`
- Create: `tests/test_graph/conftest.py`
- Create: `tests/test_graph/test_langgraph_contract.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — this is the first.
- Produces: an installed `langgraph` and `langgraph.checkpoint`, plus a `tracker` fixture available to every test in `tests/test_graph/`. Later tasks rely on these framework names: `langgraph.graph.StateGraph`, `langgraph.graph.START`, `langgraph.graph.END`, `langgraph.checkpoint.memory.InMemorySaver`, `StateGraph.add_node/add_edge/add_conditional_edges/compile`, `CompiledStateGraph.ainvoke/aget_state`.

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, `[project].dependencies` — keep the list alphabetical:

```toml
dependencies = [
    "beautifulsoup4>=4.12",
    "chromadb>=0.5,<2",
    "httpx>=0.27",
    "langgraph>=1.2",
    "langgraph-checkpoint>=4.1",
    "langsmith>=0.10",
    "openai>=2",
    "pdfplumber>=0.11",
    "pydantic>=2",
    "python-dotenv>=1",
    "pyyaml",
    "tavily-python>=0.7",
]
```

`langgraph-checkpoint` is a hard dependency of `langgraph`, but this project imports `langgraph.checkpoint.memory.InMemorySaver` directly, so it is pinned directly — the same reason `langsmith` is pinned even though other packages pull it in.

- [ ] **Step 2: Install and confirm nothing already passing broke**

Run: `pip install -e ".[dev]"`
Expected: resolves and installs `langgraph`, `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk`, `langchain-core`, `xxhash`.

Then run: `pytest -q`
Expected: the whole existing suite still passes. `langgraph` pulls in `langchain-core`, which pins its own `langsmith` floor; if that upgrade breaks `tests/test_observability_*.py`, fix it here rather than carrying a red suite into Task 2.

- [ ] **Step 3: Write the failing characterization tests**

Create `tests/test_graph/__init__.py` (empty file — `tests/test_agents/` and `tests/test_tools/` both have one).

Create `tests/test_graph/conftest.py`:

```python
import pytest

from deep_research.observability import LangSmithRuntimeConfig, Tracker


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="graph-tests",
            api_key=None,
        )
    )
```

Create `tests/test_graph/test_langgraph_contract.py`:

```python
"""Characterization tests for the LangGraph surface this project builds on.

These test no project code. They pin the four framework behaviors the
orchestrator's design rests on — a single-key dict channel replaced by each
node, a conditional edge driven by a ``path_map``, resume by ``thread_id``,
and what state lookup does for an unknown thread or a graph with no
checkpointer — so a LangGraph upgrade that moves any of them fails here, in
one small file, instead of somewhere inside the research graph.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class _Channel(TypedDict):
    payload: dict[str, int]


async def _increment(channel: _Channel) -> _Channel:
    payload = dict(channel["payload"])
    payload["count"] = payload.get("count", 0) + 1
    return {"payload": payload}


async def _double(channel: _Channel) -> _Channel:
    payload = dict(channel["payload"])
    payload["count"] = payload["count"] * 2
    return {"payload": payload}


def _route(channel: _Channel) -> str:
    return "again" if channel["payload"]["count"] < 4 else "stop"


def _loop_builder() -> StateGraph:
    builder = StateGraph(_Channel)
    builder.add_node("increment", _increment)
    builder.add_node("double", _double)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", "double")
    builder.add_conditional_edges(
        "double", _route, {"again": "increment", "stop": END}
    )
    return builder


def _chain_builder() -> StateGraph:
    builder = StateGraph(_Channel)
    builder.add_node("increment", _increment)
    builder.add_node("double", _double)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", "double")
    builder.add_edge("double", END)
    return builder


def _config(thread_id: str) -> dict[str, object]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}


@pytest.mark.asyncio
async def test_a_conditional_edge_loops_until_its_path_map_says_stop() -> None:
    graph = _loop_builder().compile()

    result = await graph.ainvoke({"payload": {"count": 0}}, {"recursion_limit": 20})

    # 0 -> 1 -> 2 (loop, 2 < 4) -> 3 -> 6 (stop, 6 >= 4)
    assert result["payload"]["count"] == 6


@pytest.mark.asyncio
async def test_an_interrupted_run_resumes_from_its_thread_id() -> None:
    graph = _chain_builder().compile(
        checkpointer=InMemorySaver(), interrupt_before=["double"]
    )
    config = _config("session-1")

    paused = await graph.ainvoke({"payload": {"count": 0}}, config)
    snapshot = await graph.aget_state(config)
    resumed = await graph.ainvoke(None, config)

    assert paused["payload"]["count"] == 1
    assert snapshot.next == ("double",)
    assert resumed["payload"]["count"] == 2


@pytest.mark.asyncio
async def test_resuming_a_finished_thread_returns_its_final_values() -> None:
    graph = _chain_builder().compile(checkpointer=InMemorySaver())
    config = _config("session-1")

    await graph.ainvoke({"payload": {"count": 0}}, config)
    resumed = await graph.ainvoke(None, config)

    assert resumed["payload"]["count"] == 2


@pytest.mark.asyncio
async def test_an_unknown_thread_has_no_checkpointed_values() -> None:
    graph = _chain_builder().compile(checkpointer=InMemorySaver())

    snapshot = await graph.aget_state(_config("never-run"))

    assert snapshot.values == {}


@pytest.mark.asyncio
async def test_state_lookup_without_a_checkpointer_raises() -> None:
    graph = _chain_builder().compile()

    with pytest.raises(ValueError):
        await graph.aget_state(_config("session-1"))
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_graph/test_langgraph_contract.py -v && ruff check tests/test_graph/`
Expected: PASS, no lint findings.

If any of these five fail, stop and report before starting Task 2 — the plan's Tasks 5 and 6 assume exactly this behavior. `test_resuming_a_finished_thread_returns_its_final_values` and `test_an_unknown_thread_has_no_checkpointed_values` are the two `resume_research_graph` is built on.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_graph/
git commit -m "build: add langgraph and pin the framework contract it provides"
```

---

### Task 2: The Graph Channel, Halt Discipline, Routing, And Status

**Files:**
- Create: `src/deep_research/graph/state.py`
- Create: `tests/test_graph/test_state.py`

**Interfaces:**
- Consumes: `MemorySnapshot`, `ResearchState` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.graph.state`:
  - `GRAPH_SOURCE: str = "graph"`
  - `PLANNER_NODE`, `RESEARCHER_NODE`, `SOURCE_EVALUATOR_NODE`, `FACT_CHECKER_NODE`, `SYNTHESIZER_NODE`, `CRITIC_NODE`, `REFINE_NODE` — the seven node-name constants
  - `NODE_NAMES: tuple[str, ...]` — those seven, in execution order
  - `ROUTE_REFINE: str = "refine"`, `ROUTE_END: str = "end"`
  - `GRAPH_ROUTES: dict[str, str]` — the five enumerated routing reasons
  - `GRAPH_STATUSES: tuple[str, ...]` — `("completed", "max_iterations", "incomplete", "failed")`
  - `HALTING_ERROR_TYPES: frozenset[str]`
  - `DEFAULT_MAX_ITERATIONS: int = 3`
  - `ResearchGraphState(TypedDict)` — one key, `state: dict[str, JsonValue]`
  - `initial_graph_state(*, session_id: str, question: str, max_iterations: int = DEFAULT_MAX_ITERATIONS, memory_context: MemorySnapshot | None = None) -> ResearchGraphState`
  - `load_state(channel: ResearchGraphState) -> ResearchState`
  - `dump_state(state: ResearchState) -> ResearchGraphState`
  - `is_halted(state: ResearchState) -> bool`
  - `graph_route(state: ResearchState) -> tuple[str, str]` — `(destination, reason)`
  - `graph_status(state: ResearchState) -> str`
  - `graph_recursion_limit(max_iterations: int) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph/test_state.py`:

```python
"""Tests for the graph channel, the halt discipline, and routing."""

from __future__ import annotations

import pytest

from deep_research.graph.state import (
    DEFAULT_MAX_ITERATIONS,
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    ROUTE_END,
    ROUTE_REFINE,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.utils.types import (
    Critique,
    MemorySnapshot,
    ResearchError,
    ResearchState,
    SubTopic,
)


def _critique(*, should_continue: bool, score: int = 5) -> Critique:
    return Critique(
        score=score,
        gaps=["No cost data."] if should_continue else [],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=should_continue,
        rationale="Recorded for routing tests.",
    )


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _halting_error() -> ResearchError:
    return ResearchError(
        error_type="graph_invalid_agent_state",
        source="graph.researcher",
        message="An agent returned a state update the research state rejected.",
        recoverable=False,
    )


def test_the_initial_channel_carries_the_question_and_the_budget() -> None:
    channel = initial_graph_state(
        session_id="session-1",
        question="  How mature is quantum error correction?  ",
        max_iterations=2,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )
    state = load_state(channel)

    assert set(channel) == {"state"}
    assert state.session_id == "session-1"
    assert state.original_question == "How mature is quantum error correction?"
    assert state.max_iterations == 2
    assert state.iteration == 0
    assert state.memory_context.suggested_strategies == ["start broad"]


def test_the_initial_channel_defaults_to_an_empty_memory_snapshot() -> None:
    state = load_state(
        initial_graph_state(session_id="session-1", question="Why?")
    )

    assert state.memory_context == MemorySnapshot()
    assert state.max_iterations == DEFAULT_MAX_ITERATIONS


def test_the_channel_round_trips_a_populated_state_as_plain_json() -> None:
    state = _state(
        sub_topics=[
            SubTopic(
                title="Error correction",
                rationale="It is the bottleneck.",
                search_queries=["qec 2025"],
                success_criteria=["a logical error rate is quoted"],
                priority=1,
            )
        ],
        report="# Research report",
        critique=_critique(should_continue=False, score=8),
    )

    channel = dump_state(state)

    assert isinstance(channel["state"], dict)
    assert isinstance(channel["state"]["sub_topics"], list)
    assert isinstance(channel["state"]["sub_topics"][0], dict)
    assert load_state(channel) == state


def test_only_enumerated_error_types_halt_a_run() -> None:
    survivable = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )

    assert not is_halted(_state())
    assert not is_halted(_state(errors=[survivable]))
    assert is_halted(_state(errors=[_halting_error()]))


def test_a_halted_run_ends_whatever_the_critic_recommended() -> None:
    state = _state(
        errors=[_halting_error()],
        critique=_critique(should_continue=True),
    )

    assert graph_route(state) == (ROUTE_END, "halted")
    assert graph_status(state) == "failed"


def test_a_run_with_no_critique_ends_as_incomplete() -> None:
    assert graph_route(_state()) == (ROUTE_END, "missing_critique")
    assert graph_status(_state()) == "incomplete"


def test_a_satisfied_critic_ends_the_run() -> None:
    state = _state(critique=_critique(should_continue=False, score=9))

    assert graph_route(state) == (ROUTE_END, "critique_satisfied")
    assert graph_status(state) == "completed"


def test_an_unsatisfied_critic_buys_a_refinement_while_budget_remains() -> None:
    state = _state(
        critique=_critique(should_continue=True),
        iteration=1,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_REFINE, "refinement_requested")


def test_the_iteration_bound_beats_the_critics_recommendation() -> None:
    state = _state(
        critique=_critique(should_continue=True),
        iteration=2,
        max_iterations=2,
    )

    assert graph_route(state) == (ROUTE_END, "max_iterations_reached")
    assert graph_status(state) == "max_iterations"


def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    reasons = {
        graph_route(state)[1]
        for state in (
            _state(errors=[_halting_error()]),
            _state(),
            _state(critique=_critique(should_continue=False)),
            _state(critique=_critique(should_continue=True), max_iterations=2),
            _state(
                critique=_critique(should_continue=True),
                iteration=1,
                max_iterations=1,
            ),
        )
    }

    assert reasons == set(GRAPH_ROUTES)
    for reason in GRAPH_ROUTES:
        assert GRAPH_ROUTES[reason].strip()


def test_the_status_vocabulary_is_closed() -> None:
    assert set(GRAPH_STATUSES) == {
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    }


def test_the_recursion_limit_covers_every_planned_pass() -> None:
    assert graph_recursion_limit(1) == 2 * len(NODE_NAMES) + 10
    assert graph_recursion_limit(3) > graph_recursion_limit(1)
    with pytest.raises(ValueError, match="max_iterations"):
        graph_recursion_limit(0)


def test_the_node_names_are_unique_and_ordered() -> None:
    assert len(set(NODE_NAMES)) == len(NODE_NAMES)
    assert NODE_NAMES[0] == "planner"
    assert NODE_NAMES[-1] == "refine"


def test_the_halting_error_types_are_all_graph_owned() -> None:
    assert HALTING_ERROR_TYPES
    assert all(name.startswith("graph_") for name in HALTING_ERROR_TYPES)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.graph.state'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/graph/state.py`:

```python
"""The graph channel, the halt discipline, routing, and final status.

Pure: nothing here imports LangGraph, opens a span, or touches I/O, so the
two rules that matter most — "the iteration bound beats any model
judgement" and "a non-recoverable failure ends the run with its state
intact" — are testable without compiling a graph or standing up an agent.

The channel carries the whole ``ResearchState`` as one JSON-safe mapping
under the key ``state``. Merging happens inside each node through the
project's own ``merge_research_state`` rather than through per-field
LangGraph reducers: the append/replace rules and the "``iteration`` moves
only through ``advance_research_iteration``" guard already live there, and
a second copy expressed as channel annotations could drift from the first.

The dump is plain JSON, not a Pydantic object. LangGraph's checkpoint
serializer does handle Pydantic v2 models, but it revives them by importing
the class and warns that unregistered types will be blocked in a future
release. Primitives sidestep that entirely and keep checkpoints readable.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import JsonValue

from deep_research.utils.types import MemorySnapshot, ResearchState

GRAPH_SOURCE = "graph"

PLANNER_NODE = "planner"
RESEARCHER_NODE = "researcher"
SOURCE_EVALUATOR_NODE = "source_evaluator"
FACT_CHECKER_NODE = "fact_checker"
SYNTHESIZER_NODE = "synthesizer"
CRITIC_NODE = "critic"
REFINE_NODE = "refine"

# Execution order, with the refinement hop last. Node names deliberately
# equal agent names so a LangSmith trace reads the same as this tuple.
NODE_NAMES = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    FACT_CHECKER_NODE,
    SYNTHESIZER_NODE,
    CRITIC_NODE,
    REFINE_NODE,
)

ROUTE_REFINE = "refine"
ROUTE_END = "end"

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the session span's outputs.
GRAPH_ROUTES = {
    "refinement_requested": (
        "The critic asked for another research pass and budget remains."
    ),
    "critique_satisfied": "The critic accepted the report.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "missing_critique": (
        "No critique was recorded, so no refinement can be justified."
    ),
    "halted": "The run stopped on a non-recoverable error.",
}

GRAPH_STATUSES = ("completed", "max_iterations", "incomplete", "failed")

_STATUS_BY_ROUTE_REASON = {
    "critique_satisfied": "completed",
    "max_iterations_reached": "max_iterations",
    "missing_critique": "incomplete",
    "refinement_requested": "incomplete",
    "halted": "failed",
}

# The error types that stop a run. Deliberately *not* "any error with
# recoverable=False": agents already record non-recoverable provider
# failures (critic_review_provider_error, for one) that a research pass is
# expected to survive, and halting on those would end a run over one blip.
HALTING_ERROR_TYPES = frozenset(
    {
        "graph_agent_configuration_error",
        "graph_planning_failed",
        "graph_provider_configuration_error",
        "graph_invalid_agent_state",
        "graph_invalid_route",
    }
)

DEFAULT_MAX_ITERATIONS = 3

# Head-room over the supersteps a full budget needs, for the START edge and
# LangGraph's own bookkeeping steps.
_RECURSION_MARGIN = 10


class ResearchGraphState(TypedDict):
    """The whole LangGraph channel: one JSON-safe ``ResearchState`` dump."""

    state: dict[str, JsonValue]


def dump_state(state: ResearchState) -> ResearchGraphState:
    """Render one research state as the channel a node returns."""
    return {"state": state.model_dump(mode="json")}


def load_state(channel: ResearchGraphState) -> ResearchState:
    """Validate the channel back into a research state.

    Validation is not ceremony: it is what makes a checkpoint written by an
    older build fail loudly here rather than silently half-populate a node.
    """
    return ResearchState.model_validate(channel["state"])


def initial_graph_state(
    *,
    session_id: str,
    question: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    memory_context: MemorySnapshot | None = None,
) -> ResearchGraphState:
    """Build the channel one research session starts from.

    ``memory_context`` is supplied by the caller. The graph performs no
    recall of its own: that touches ChromaDB and an embedding provider,
    which orchestration has no business owning.
    """
    return dump_state(
        ResearchState(
            session_id=session_id,
            original_question=question,
            max_iterations=max_iterations,
            memory_context=memory_context or MemorySnapshot(),
        )
    )


def is_halted(state: ResearchState) -> bool:
    """True when an enumerated graph failure has ended this run."""
    return any(
        error.error_type in HALTING_ERROR_TYPES for error in state.errors
    )


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Decide where the graph goes after the Critic, and why.

    Pure, so the conditional edge, the recorded route event, and the final
    status all read the same decision. ``Critique.should_continue`` is the
    critic's recommendation; the iteration bound is the graph's law and is
    checked here regardless of what the model said.
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    critique = state.critique
    if critique is None:
        return ROUTE_END, "missing_critique"
    if not critique.should_continue:
        return ROUTE_END, "critique_satisfied"
    if state.iteration >= state.max_iterations:
        return ROUTE_END, "max_iterations_reached"
    return ROUTE_REFINE, "refinement_requested"


def graph_status(state: ResearchState) -> str:
    """Name how this run ended, from the same decision the router used."""
    return _STATUS_BY_ROUTE_REASON[graph_route(state)[1]]


def graph_recursion_limit(max_iterations: int) -> int:
    """Bound LangGraph's supersteps from the graph's real shape.

    Always passed explicitly. LangGraph 1.2 defaults this generously, but
    earlier releases defaulted to 25 — under what four macro passes over
    seven nodes need — and an explicit value documents the shape.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    return (max_iterations + 1) * len(NODE_NAMES) + _RECURSION_MARGIN
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph/test_state.py -v && ruff check src/deep_research/graph/ tests/test_graph/`
Expected: PASS, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/graph/state.py tests/test_graph/test_state.py
git commit -m "feat: add the research graph channel, halt discipline, and routing"
```

---

### Task 3: Graph-Level Events And Errors

**Files:**
- Create: `src/deep_research/graph/errors.py`
- Create: `src/deep_research/graph/events.py`
- Create: `tests/test_graph/test_records.py`

**Interfaces:**
- Consumes: `GRAPH_ROUTES`, `GRAPH_SOURCE`, `HALTING_ERROR_TYPES` from `deep_research.graph.state` (Task 2); `ResearchError`, `ResearchEvent` from `deep_research.utils.types`.
- Produces, importable from `deep_research.graph.errors`:
  - `GraphError(Exception)`, `GraphConfigurationError(GraphError)`, `GraphResumeError(GraphError)`
  - `GRAPH_ERROR_REASONS: dict[str, str]`
  - `graph_error(*, error_type: str, node: str | None = None, details: Mapping[str, JsonValue] | None = None) -> ResearchError`
  - `agent_configuration_error(error: Exception, *, node: str) -> ResearchError`
  - `planning_failed_error(error: Exception, *, node: str) -> ResearchError`
  - `provider_configuration_error(error: Exception, *, node: str) -> ResearchError`
  - `invalid_agent_state_error(error: Exception, *, node: str) -> ResearchError`
  - `invalid_route_error(*, node: str, iteration: int, max_iterations: int) -> ResearchError`
- Produces, importable from `deep_research.graph.events`:
  - `graph_event(*, event_type: str, message: str, node: str | None = None, metadata: Mapping[str, JsonValue] | None = None) -> ResearchEvent`
  - `node_started_event(node: str, *, iteration: int) -> ResearchEvent`
  - `node_completed_event(node: str, *, iteration: int, event_count: int, error_count: int) -> ResearchEvent`
  - `node_skipped_event(node: str, *, iteration: int) -> ResearchEvent`
  - `route_decided_event(*, destination: str, reason: str, iteration: int, max_iterations: int, should_continue: bool) -> ResearchEvent`
  - `refinement_started_event(*, iteration: int, max_iterations: int) -> ResearchEvent`
  - `session_started_event(*, session_id: str, max_iterations: int, checkpointing: bool) -> ResearchEvent`
  - `session_completed_event(*, status: str, iteration: int, error_count: int, has_report: bool) -> ResearchEvent`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph/test_records.py`:

```python
"""Tests for graph-level event and error records."""

from __future__ import annotations

import pytest

from deep_research.graph.errors import (
    GRAPH_ERROR_REASONS,
    GraphConfigurationError,
    GraphError,
    GraphResumeError,
    agent_configuration_error,
    graph_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
)
from deep_research.graph.events import (
    graph_event,
    node_completed_event,
    node_skipped_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
    session_completed_event,
    session_started_event,
)
from deep_research.graph.state import (
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    ROUTE_REFINE,
)


def test_every_halting_error_type_has_an_enumerated_reason() -> None:
    assert HALTING_ERROR_TYPES <= set(GRAPH_ERROR_REASONS)
    for reason in GRAPH_ERROR_REASONS.values():
        assert reason.strip()


def test_an_unenumerated_error_type_cannot_be_recorded() -> None:
    with pytest.raises(ValueError, match="unknown graph error type"):
        graph_error(error_type="graph_something_new")


def test_a_graph_error_names_its_node_in_the_source() -> None:
    recorded = graph_error(
        error_type="graph_invalid_agent_state",
        node="researcher",
        details={"exception_type": "ValidationError"},
    )

    assert recorded.source == "graph.researcher"
    assert recorded.error_type == "graph_invalid_agent_state"
    assert recorded.message == GRAPH_ERROR_REASONS["graph_invalid_agent_state"]
    assert recorded.recoverable is False
    assert recorded.details == {"exception_type": "ValidationError"}


def test_a_node_free_graph_error_is_attributed_to_the_graph() -> None:
    assert graph_error(error_type="graph_planning_failed").source == "graph"


@pytest.mark.parametrize(
    ("builder", "expected_type"),
    [
        (agent_configuration_error, "graph_agent_configuration_error"),
        (planning_failed_error, "graph_planning_failed"),
        (provider_configuration_error, "graph_provider_configuration_error"),
        (invalid_agent_state_error, "graph_invalid_agent_state"),
    ],
)
def test_failure_builders_record_the_exception_type_only(
    builder: object, expected_type: str
) -> None:
    recorded = builder(ValueError("sk-secret-leaked-into-the-message"), node="planner")

    assert recorded.error_type == expected_type
    assert recorded.details == {"exception_type": "ValueError"}
    assert "secret" not in recorded.message
    assert recorded.recoverable is False


def test_an_invalid_route_records_the_budget_it_violated() -> None:
    recorded = invalid_route_error(node="refine", iteration=3, max_iterations=3)

    assert recorded.error_type == "graph_invalid_route"
    assert recorded.details == {"iteration": 3, "max_iterations": 3}
    assert recorded.recoverable is False


def test_graph_exceptions_share_one_base() -> None:
    assert issubclass(GraphConfigurationError, GraphError)
    assert issubclass(GraphResumeError, GraphError)


def test_a_graph_event_names_its_node_in_the_source() -> None:
    event = graph_event(
        event_type="graph.node.started",
        message="Node started.",
        node="planner",
        metadata={"iteration": 0},
    )

    assert event.source == "graph.planner"
    assert event.metadata == {"iteration": 0}


def test_a_blank_event_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="event_type"):
        graph_event(event_type="  ", message="Node started.")


def test_node_lifecycle_events_carry_their_counts() -> None:
    started = node_started_event("researcher", iteration=1)
    completed = node_completed_event(
        "researcher", iteration=1, event_count=4, error_count=1
    )
    skipped = node_skipped_event("synthesizer", iteration=1)

    assert started.event_type == "graph.node.started"
    assert started.metadata == {"node": "researcher", "iteration": 1}
    assert completed.event_type == "graph.node.completed"
    assert completed.metadata["event_count"] == 4
    assert completed.metadata["error_count"] == 1
    assert skipped.event_type == "graph.node.skipped"
    assert skipped.metadata["reason"] == "halted"


def test_a_route_decision_records_an_enumerated_reason() -> None:
    event = route_decided_event(
        destination=ROUTE_REFINE,
        reason="refinement_requested",
        iteration=0,
        max_iterations=3,
        should_continue=True,
    )

    assert event.event_type == "graph.route.decided"
    assert event.metadata["destination"] == ROUTE_REFINE
    assert event.metadata["reason"] == "refinement_requested"
    assert event.metadata["should_continue"] is True
    assert GRAPH_ROUTES["refinement_requested"] in event.message


def test_a_route_decision_refuses_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="unknown route reason"):
        route_decided_event(
            destination=ROUTE_REFINE,
            reason="because",
            iteration=0,
            max_iterations=3,
            should_continue=True,
        )


def test_a_refinement_announces_the_iteration_it_opened() -> None:
    event = refinement_started_event(iteration=1, max_iterations=3)

    assert event.event_type == "graph.refinement.started"
    assert event.metadata == {"iteration": 1, "max_iterations": 3}


def test_session_events_bracket_the_run() -> None:
    started = session_started_event(
        session_id="session-1", max_iterations=3, checkpointing=True
    )
    completed = session_completed_event(
        status="completed", iteration=1, error_count=0, has_report=True
    )

    assert started.event_type == "graph.session.started"
    assert started.metadata["checkpointing"] is True
    assert completed.event_type == "graph.session.completed"
    assert completed.metadata["status"] in GRAPH_STATUSES
    assert completed.metadata["has_report"] is True


def test_a_session_completion_refuses_an_unenumerated_status() -> None:
    with pytest.raises(ValueError, match="unknown graph status"):
        session_completed_event(
            status="finished", iteration=0, error_count=0, has_report=False
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph/test_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.graph.errors'`

- [ ] **Step 3: Write the error module**

Create `src/deep_research/graph/errors.py`:

```python
"""Graph runtime exceptions and enumerated graph-level error records.

The graph mirror of ``agents.errors``. Every error type the graph can
record is enumerated in ``GRAPH_ERROR_REASONS``, and the subset that stops
a run is ``state.HALTING_ERROR_TYPES``. A node halts only for one of those,
never merely because an error carried ``recoverable=False`` — agents
already record non-recoverable provider failures a research pass is
expected to survive.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.graph.state import GRAPH_SOURCE, HALTING_ERROR_TYPES
from deep_research.utils.types import ResearchError

# Enumerated, project-generated failure messages. Never provider text and
# never str(exception): these reach ResearchState.errors, which the CLI,
# the API stream, and the UI all render.
GRAPH_ERROR_REASONS = {
    "graph_agent_configuration_error": (
        "An agent node was assembled incorrectly, so the research run "
        "stopped."
    ),
    "graph_planning_failed": (
        "The planner could not produce a research plan, so the research run "
        "stopped."
    ),
    "graph_provider_configuration_error": (
        "The model provider is not configured, so the research run stopped."
    ),
    "graph_invalid_agent_state": (
        "An agent returned a state update the research state rejected, so "
        "the research run stopped."
    ),
    "graph_invalid_route": (
        "The graph attempted a refinement pass with no budget left, so the "
        "research run stopped."
    ),
}


class GraphError(Exception):
    """Base class for research graph failures."""


class GraphConfigurationError(GraphError):
    """The graph was assembled incorrectly. Not recoverable at runtime."""


class GraphResumeError(GraphError):
    """A session could not be resumed from a checkpoint."""


def graph_error(
    *,
    error_type: str,
    node: str | None = None,
    details: Mapping[str, JsonValue] | None = None,
) -> ResearchError:
    """Build one enumerated graph-level error record.

    The message and the recoverability both come from the enumeration: an
    error the graph never named cannot be recorded, and a halting type is
    never recorded as recoverable. ``details`` must never contain
    ``str(exception)`` — record ``exception_type`` instead.
    """
    reason = GRAPH_ERROR_REASONS.get(error_type)
    if reason is None:
        raise ValueError(f"unknown graph error type: {error_type}")
    source = GRAPH_SOURCE if node is None else f"{GRAPH_SOURCE}.{node.strip()}"
    return ResearchError(
        error_type=error_type,
        source=source,
        message=reason,
        recoverable=error_type not in HALTING_ERROR_TYPES,
        details=dict(details or {}),
    )


def _from_exception(
    error_type: str,
    error: Exception,
    *,
    node: str,
) -> ResearchError:
    return graph_error(
        error_type=error_type,
        node=node,
        details={"exception_type": type(error).__name__},
    )


def agent_configuration_error(error: Exception, *, node: str) -> ResearchError:
    """Record that an agent was assembled incorrectly."""
    return _from_exception("graph_agent_configuration_error", error, node=node)


def planning_failed_error(error: Exception, *, node: str) -> ResearchError:
    """Record that the planner could not produce a plan."""
    return _from_exception("graph_planning_failed", error, node=node)


def provider_configuration_error(
    error: Exception,
    *,
    node: str,
) -> ResearchError:
    """Record that the model provider is not configured."""
    return _from_exception(
        "graph_provider_configuration_error", error, node=node
    )


def invalid_agent_state_error(error: Exception, *, node: str) -> ResearchError:
    """Record that an agent's state update was rejected by the state model."""
    return _from_exception("graph_invalid_agent_state", error, node=node)


def invalid_route_error(
    *,
    node: str,
    iteration: int,
    max_iterations: int,
) -> ResearchError:
    """Record that a refinement was attempted with no budget left.

    The router already forbids this. The guard exists because "iteration
    bounds prevent infinite loops" is the one property this graph must not
    lose to a future edit, and a second lock on that door costs three lines.
    """
    return graph_error(
        error_type="graph_invalid_route",
        node=node,
        details={"iteration": iteration, "max_iterations": max_iterations},
    )
```

- [ ] **Step 4: Write the event module**

Create `src/deep_research/graph/events.py`:

```python
"""Structured progress events emitted by the graph itself.

The graph mirror of ``agents.events``. These records land in
``ResearchState.events`` alongside the agents' own events and the
``Tracker``'s span lifecycle events, and are what makes a route decision
observable without reading a LangSmith trace.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.graph.state import (
    GRAPH_ROUTES,
    GRAPH_SOURCE,
    GRAPH_STATUSES,
)
from deep_research.utils.types import ResearchEvent


def graph_event(
    *,
    event_type: str,
    message: str,
    node: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ResearchEvent:
    """Build one progress event attributed to the graph or one of its nodes.

    ``metadata`` must never contain ``str(exception)`` or raw provider text:
    these records are copied into ``ResearchState.events``. Record counts,
    identifiers, and enumerated reasons instead.
    """
    if not event_type.strip():
        raise ValueError("event_type must not be blank")
    source = GRAPH_SOURCE if node is None else f"{GRAPH_SOURCE}.{node.strip()}"
    return ResearchEvent(
        event_type=event_type.strip(),
        source=source,
        message=message,
        metadata=dict(metadata or {}),
    )


def node_started_event(node: str, *, iteration: int) -> ResearchEvent:
    """Announce that a node began, before its agent is invoked."""
    return graph_event(
        event_type="graph.node.started",
        message=f"Node {node} started.",
        node=node,
        metadata={"node": node, "iteration": iteration},
    )


def node_completed_event(
    node: str,
    *,
    iteration: int,
    event_count: int,
    error_count: int,
) -> ResearchEvent:
    """Report what one node's agent contributed to state."""
    return graph_event(
        event_type="graph.node.completed",
        message=f"Node {node} completed.",
        node=node,
        metadata={
            "node": node,
            "iteration": iteration,
            "event_count": event_count,
            "error_count": error_count,
        },
    )


def node_skipped_event(node: str, *, iteration: int) -> ResearchEvent:
    """Report that a node ran no agent because the run had already halted."""
    return graph_event(
        event_type="graph.node.skipped",
        message=f"Node {node} was skipped because the run had halted.",
        node=node,
        metadata={"node": node, "iteration": iteration, "reason": "halted"},
    )


def route_decided_event(
    *,
    destination: str,
    reason: str,
    iteration: int,
    max_iterations: int,
    should_continue: bool,
) -> ResearchEvent:
    """Record where the graph went after the Critic, and why.

    ``reason`` is a ``GRAPH_ROUTES`` key, never provider text, so a consumer
    can group runs by *why* they continued or stopped rather than parsing a
    rationale. ``should_continue`` is recorded next to it so a reader can
    see when the iteration bound overrode the critic.
    """
    explanation = GRAPH_ROUTES.get(reason)
    if explanation is None:
        raise ValueError(f"unknown route reason: {reason}")
    return graph_event(
        event_type="graph.route.decided",
        message=explanation,
        metadata={
            "destination": destination,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "should_continue": should_continue,
        },
    )


def refinement_started_event(
    *,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Announce the macro iteration a loop-back just opened."""
    return graph_event(
        event_type="graph.refinement.started",
        message=f"Refinement pass {iteration} started.",
        metadata={"iteration": iteration, "max_iterations": max_iterations},
    )


def session_started_event(
    *,
    session_id: str,
    max_iterations: int,
    checkpointing: bool,
) -> ResearchEvent:
    """Announce the research session, before the first node runs."""
    return graph_event(
        event_type="graph.session.started",
        message="Research session started.",
        metadata={
            "session_id": session_id,
            "max_iterations": max_iterations,
            "checkpointing": checkpointing,
        },
    )


def session_completed_event(
    *,
    status: str,
    iteration: int,
    error_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Report the final status of one research session."""
    if status not in GRAPH_STATUSES:
        raise ValueError(f"unknown graph status: {status}")
    return graph_event(
        event_type="graph.session.completed",
        message=f"Research session finished with status {status}.",
        metadata={
            "status": status,
            "iteration": iteration,
            "error_count": error_count,
            "has_report": has_report,
        },
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_graph/test_records.py -v && ruff check src/deep_research/graph/ tests/test_graph/`
Expected: PASS, no lint findings.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/errors.py src/deep_research/graph/events.py \
  tests/test_graph/test_records.py
git commit -m "feat: add graph-level events and enumerated graph errors"
```

---

### Task 4: Node Wrappers, The Halt Discipline, And The Refinement Hop

**Files:**
- Create: `src/deep_research/graph/nodes.py`
- Create: `tests/graph_fakes.py`
- Create: `tests/test_graph/test_nodes.py`

**Interfaces:**
- Consumes: `CRITIC_NODE`, `REFINE_NODE`, `ResearchGraphState`, `dump_state`, `graph_route`, `is_halted`, `load_state` from `deep_research.graph.state` (Task 2); `GraphConfigurationError`, `agent_configuration_error`, `invalid_agent_state_error`, `invalid_route_error`, `planning_failed_error`, `provider_configuration_error` from `deep_research.graph.errors` (Task 3); `node_completed_event`, `node_skipped_event`, `node_started_event`, `refinement_started_event`, `route_decided_event` from `deep_research.graph.events` (Task 3); `AgentRun` from `deep_research.agents.base`; `AgentConfigurationError`, `PlanningError` from `deep_research.agents.errors`; `ProviderConfigurationError` from `deep_research.providers`; `ResearchState`, `advance_research_iteration`, `merge_research_state` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.graph.nodes`:
  - `GraphNode: TypeAlias = Callable[[ResearchGraphState], Awaitable[ResearchGraphState]]`
  - `ResearchAgent(Protocol)` — `name: str`, `async run(state: ResearchState) -> AgentRun[Any]`
  - `agent_node(agent: ResearchAgent, *, node_name: str | None = None) -> GraphNode`
  - `critic_node(agent: ResearchAgent, *, node_name: str = CRITIC_NODE) -> GraphNode`
  - `refine_node(channel: ResearchGraphState) -> ResearchGraphState` (async)
  - `route_after_critic(channel: ResearchGraphState) -> str`
- Produces, importable from `tests.graph_fakes`:
  - `FakeAgent(name, updates=())` with `.name`, `.calls`, `async run(state)`
  - `fake_sub_topic()`, `fake_finding()`, `fake_scored_source()`, `fake_claim()`, `fake_critique(*, should_continue, score=5)`, `halting_error()`

- [ ] **Step 1: Write the fakes**

Create `tests/graph_fakes.py`:

```python
"""Offline agent doubles and domain builders for graph tests.

No provider, no tracker, no scratchpad, no toolset: the node wrappers
depend on the ``ResearchAgent`` protocol (``name`` plus ``run``), so a
graph test never has to assemble a real agent — which would need an API
key this repository deliberately does not have.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deep_research.agents.base import AgentRun
from deep_research.agents.steps import ReActRun
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


class FakeAgent:
    """Serve scripted state updates instead of running a real agent.

    ``updates`` is consumed one entry per call and the final entry repeats,
    which is how "a critic that asks for one refinement and then accepts"
    is expressed without scripting every pass. A queued ``BaseException``
    is raised instead of returned, which is how agent failures are
    simulated.
    """

    def __init__(
        self,
        name: str,
        updates: Sequence[ResearchStateUpdate | BaseException] = (),
    ) -> None:
        self.name = name
        self._updates: list[Any] = list(updates) or [{}]
        self.calls: list[ResearchState] = []

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.calls.append(state)
        position = min(len(self.calls) - 1, len(self._updates) - 1)
        update = self._updates[position]
        if isinstance(update, BaseException):
            raise update
        return AgentRun(
            agent_name=self.name,
            result=None,
            react=ReActRun(agent_name=self.name, stop_reason="finished"),
            errors=[],
            state_update=update,
        )


def fake_sub_topic(title: str = "Error correction") -> SubTopic:
    return SubTopic(
        title=title,
        rationale="It is the bottleneck.",
        search_queries=["qec 2025"],
        success_criteria=["a logical error rate is quoted"],
        priority=1,
    )


def fake_finding(content: str = "Break-even was reached.") -> Finding:
    return Finding(
        content=content,
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Error correction",
    )


def fake_scored_source(url: str = SOURCE_URL) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )


def fake_claim(text: str = "Break-even was reached in 2025.") -> Claim:
    return Claim(
        text=text,
        source_urls=[SOURCE_URL],
        verdict="verified",
        confidence=0.8,
        evidence=["An independent review states the same figure."],
        contradictions=[],
    )


def fake_critique(*, should_continue: bool, score: int = 5) -> Critique:
    return Critique(
        score=score,
        gaps=["No cost data."] if should_continue else [],
        unsupported_claims=[],
        recommended_queries=["qec cost 2025"] if should_continue else [],
        should_continue=should_continue,
        rationale="Recorded for graph tests.",
    )


def halting_error(node: str = "researcher") -> ResearchError:
    """One already-recorded graph halt, for tests that start from a dead run."""
    return ResearchError(
        error_type="graph_invalid_agent_state",
        source=f"graph.{node}",
        message="An agent returned a state update the research state rejected.",
        recoverable=False,
    )
```

- [ ] **Step 2: Write the failing node tests**

Create `tests/test_graph/test_nodes.py`:

```python
"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.graph.nodes import (
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    ROUTE_END,
    ROUTE_REFINE,
    dump_state,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.utils.types import ResearchError, ResearchState

from tests.graph_fakes import (
    FakeAgent,
    fake_critique,
    fake_finding,
    fake_sub_topic,
    halting_error,
)


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


@pytest.mark.asyncio
async def test_a_node_merges_its_agents_update_and_brackets_it_with_events(
) -> None:
    agent = FakeAgent("planner", [{"sub_topics": [fake_sub_topic()]}])
    node = agent_node(agent)

    result = await node(dump_state(_state()))
    state = load_state(result)

    assert [topic.title for topic in state.sub_topics] == ["Error correction"]
    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
    ]
    assert state.events[-1].metadata["node"] == "planner"
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_an_agent_is_handed_state_that_already_records_its_start() -> None:
    agent = FakeAgent("researcher", [{"raw_findings": [fake_finding()]}])

    await agent_node(agent)(dump_state(_state()))

    # The agent is handed the state *after* graph.node.started is recorded,
    # so an agent that reads state.events sees its own node's start.
    assert _event_types(agent.calls[0]) == ["graph.node.started"]


@pytest.mark.asyncio
async def test_a_node_name_may_be_overridden_for_the_graph() -> None:
    agent = FakeAgent("planner")

    result = await agent_node(agent, node_name="scout")(dump_state(_state()))

    assert load_state(result).events[0].source == "graph.scout"


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_stays_in_state_and_does_not_halt(
) -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agent = FakeAgent("researcher", [{"errors": [recoverable]}])

    result = await agent_node(agent)(dump_state(_state()))
    state = load_state(result)

    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]
    assert not is_halted(state)
    assert state.events[-1].metadata["error_count"] == 1


@pytest.mark.asyncio
async def test_a_non_recoverable_agent_error_still_does_not_halt_the_graph(
) -> None:
    # Agents record provider outages as non-recoverable. A research pass is
    # expected to survive one; only enumerated graph errors halt.
    outage = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )
    agent = FakeAgent("critic", [{"errors": [outage]}])

    state = load_state(await agent_node(agent)(dump_state(_state())))

    assert not is_halted(state)


@pytest.mark.parametrize(
    ("raised", "expected_type"),
    [
        (PlanningError("no plan"), "graph_planning_failed"),
        (
            AgentConfigurationError("bad scratchpad"),
            "graph_agent_configuration_error",
        ),
        (
            ProviderConfigurationError("no api key"),
            "graph_provider_configuration_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_configuration_failure_halts_the_run_with_state_intact(
    raised: Exception, expected_type: str
) -> None:
    agent = FakeAgent("planner", [raised])

    state = load_state(await agent_node(agent)(dump_state(_state())))

    assert [error.error_type for error in state.errors] == [expected_type]
    assert state.errors[0].details == {"exception_type": type(raised).__name__}
    assert is_halted(state)
    assert state.original_question == "How mature is quantum error correction?"


@pytest.mark.asyncio
async def test_an_unexpected_agent_exception_is_not_swallowed() -> None:
    agent = FakeAgent("planner", [RuntimeError("a defect, not an outcome")])

    with pytest.raises(RuntimeError, match="a defect"):
        await agent_node(agent)(dump_state(_state()))


@pytest.mark.asyncio
async def test_an_update_the_state_model_rejects_halts_the_run() -> None:
    agent = FakeAgent("researcher", [{"iteration": 2}])

    state = load_state(await agent_node(agent)(dump_state(_state())))

    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_a_halted_run_skips_every_later_node() -> None:
    agent = FakeAgent("synthesizer", [{"report": "# never written"}])

    result = await agent_node(agent)(
        dump_state(_state(errors=[halting_error()]))
    )
    state = load_state(result)

    assert agent.calls == []
    assert state.report is None
    assert _event_types(state) == ["graph.node.skipped"]


@pytest.mark.asyncio
async def test_the_critic_node_records_the_route_it_produced() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(_state(iteration=0, max_iterations=3))
    )
    state = load_state(result)

    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
        "graph.route.decided",
    ]
    assert state.events[-1].metadata == {
        "destination": ROUTE_REFINE,
        "reason": "refinement_requested",
        "iteration": 0,
        "max_iterations": 3,
        "should_continue": True,
    }
    assert route_after_critic(result) == ROUTE_REFINE


@pytest.mark.asyncio
async def test_the_critic_node_records_the_bound_overriding_the_critic() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(_state(iteration=2, max_iterations=2))
    )
    state = load_state(result)

    assert state.events[-1].metadata["reason"] == "max_iterations_reached"
    assert state.events[-1].metadata["should_continue"] is True
    assert route_after_critic(result) == ROUTE_END


@pytest.mark.asyncio
async def test_a_halted_critic_node_still_records_a_route() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(_state(errors=[halting_error()]))
    )

    assert agent.calls == []
    assert load_state(result).events[-1].metadata["reason"] == "halted"
    assert route_after_critic(result) == ROUTE_END


@pytest.mark.asyncio
async def test_the_refinement_hop_advances_the_macro_iteration() -> None:
    result = await refine_node(dump_state(_state(iteration=0, max_iterations=3)))
    state = load_state(result)

    assert state.iteration == 1
    assert _event_types(state) == ["graph.refinement.started"]
    assert state.events[0].metadata == {"iteration": 1, "max_iterations": 3}


@pytest.mark.asyncio
async def test_the_refinement_hop_refuses_to_spend_a_budget_it_lacks() -> None:
    state = load_state(
        await refine_node(dump_state(_state(iteration=2, max_iterations=2)))
    )

    assert state.iteration == 2
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_route"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_the_refinement_hop_skips_a_halted_run() -> None:
    state = load_state(
        await refine_node(dump_state(_state(errors=[halting_error()])))
    )

    assert state.iteration == 0
    assert _event_types(state) == ["graph.node.skipped"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_graph/test_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.graph.nodes'`

- [ ] **Step 4: Write minimal implementation**

Create `src/deep_research/graph/nodes.py`:

```python
"""LangGraph node wrappers over the agents that already work.

A node is thin on purpose: read the channel, invoke one agent, merge what
it returned with ``merge_research_state``, and bracket the whole thing with
graph-level events. Nothing here imports LangGraph — a node is an async
function from channel to channel, which is what makes every rule below
testable by calling it directly.

Failure handling is a halt discipline rather than an exception escaping
``ainvoke``. Three exception types become enumerated graph errors that mark
the run dead; every later node sees the mark, records ``graph.node.skipped``
and returns without invoking its agent; the router sends the run to ``END``
with status ``failed``. The state collected before the failure survives —
which is the whole point of not raising.

Anything other than those three exception types propagates. An unhandled
exception is a defect, not a research outcome, and converting it into a
recorded error would hide it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.graph.errors import (
    GraphConfigurationError,
    agent_configuration_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
)
from deep_research.graph.events import (
    node_completed_event,
    node_skipped_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    REFINE_NODE,
    ResearchGraphState,
    dump_state,
    graph_route,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.utils.types import (
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    advance_research_iteration,
    merge_research_state,
)

GraphNode: TypeAlias = Callable[
    [ResearchGraphState], Awaitable[ResearchGraphState]
]


class ResearchAgent(Protocol):
    """The one agent capability a graph node needs.

    Every concrete agent satisfies it. Keeping the protocol to a name and a
    ``run`` is what lets graph tests use two-line doubles with no provider,
    tracker, scratchpad, or toolset.
    """

    name: str

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        """Run one agent pass over research state."""
        raise NotImplementedError


def _with(
    state: ResearchState,
    update: ResearchStateUpdate,
) -> ResearchGraphState:
    return dump_state(merge_research_state(state, update))


def _skipped(state: ResearchState, node: str) -> ResearchGraphState:
    return _with(
        state,
        {"events": [node_skipped_event(node, iteration=state.iteration)]},
    )


def _halt(
    state: ResearchState,
    error: ResearchError,
) -> ResearchGraphState:
    return _with(state, {"errors": [error]})


def agent_node(
    agent: ResearchAgent,
    *,
    node_name: str | None = None,
) -> GraphNode:
    """Wrap one agent as a LangGraph node.

    ``node_name`` defaults to the agent's own name so a LangSmith trace and
    the graph read the same; it is overridable so the same agent class can
    fill two slots if a later spec ever needs that.
    """
    name = (node_name or agent.name).strip()
    if not name:
        raise GraphConfigurationError("graph nodes need a non-blank name")

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, name)

        started = merge_research_state(
            state,
            {"events": [node_started_event(name, iteration=state.iteration)]},
        )
        try:
            outcome = await agent.run(started)
        except PlanningError as error:
            return _halt(started, planning_failed_error(error, node=name))
        except AgentConfigurationError as error:
            return _halt(started, agent_configuration_error(error, node=name))
        except ProviderConfigurationError as error:
            return _halt(
                started, provider_configuration_error(error, node=name)
            )

        update = outcome.state_update
        try:
            merged = merge_research_state(started, update)
        except (TypeError, ValueError) as error:
            # merge_research_state already enforces the whole contract:
            # unknown fields, non-list appends, a direct iteration write, and
            # every Pydantic constraint. ValidationError subclasses ValueError.
            return _halt(started, invalid_agent_state_error(error, node=name))

        return _with(
            merged,
            {
                "events": [
                    node_completed_event(
                        name,
                        iteration=merged.iteration,
                        event_count=len(update.get("events", ())),
                        error_count=len(update.get("errors", ())),
                    )
                ]
            },
        )

    return node


def critic_node(
    agent: ResearchAgent,
    *,
    node_name: str = CRITIC_NODE,
) -> GraphNode:
    """Wrap the Critic and record the route its critique produced.

    The route event is recorded here rather than in the conditional edge
    because a LangGraph router reads state and cannot write it.
    ``graph_route`` is pure and the state does not change between this call
    and ``route_after_critic``'s, so the recorded decision and the taken
    edge agree by construction.
    """
    inner = agent_node(agent, node_name=node_name)

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        reviewed = await inner(channel)
        state = load_state(reviewed)
        destination, reason = graph_route(state)
        critique = state.critique
        return _with(
            state,
            {
                "events": [
                    route_decided_event(
                        destination=destination,
                        reason=reason,
                        iteration=state.iteration,
                        max_iterations=state.max_iterations,
                        should_continue=(
                            critique is not None and critique.should_continue
                        ),
                    )
                ]
            },
        )

    return node


async def refine_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Open the next macro iteration before research runs again.

    This exists as its own node because a LangGraph conditional edge routes
    but cannot write, and the macro-iteration increment has to happen
    somewhere the graph can see and a test can call.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, REFINE_NODE)
    if state.iteration >= state.max_iterations:
        return _halt(
            state,
            invalid_route_error(
                node=REFINE_NODE,
                iteration=state.iteration,
                max_iterations=state.max_iterations,
            ),
        )

    advanced = advance_research_iteration(state)
    return _with(
        advanced,
        {
            "events": [
                refinement_started_event(
                    iteration=advanced.iteration,
                    max_iterations=advanced.max_iterations,
                )
            ]
        },
    )


def route_after_critic(channel: ResearchGraphState) -> str:
    """The conditional edge out of the Critic. Pure read of state."""
    return graph_route(load_state(channel))[0]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_graph/test_nodes.py -v && ruff check src/deep_research/graph/ tests/graph_fakes.py tests/test_graph/`
Expected: PASS, no lint findings.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/nodes.py tests/graph_fakes.py \
  tests/test_graph/test_nodes.py
git commit -m "feat: wrap the agents as graph nodes with a halt discipline"
```

---

### Task 5: Graph Construction — The Full Sequence, The Loop, And The Bound

**Files:**
- Create: `src/deep_research/graph/orchestrator.py` (construction only — the session runner arrives in Task 6)
- Modify: `tests/graph_fakes.py` (append `fake_research_agents`)
- Create: `tests/test_graph/test_orchestrator.py`

**Interfaces:**
- Consumes: `agent_node`, `critic_node`, `refine_node`, `route_after_critic`, `ResearchAgent` from `deep_research.graph.nodes` (Task 4); every node-name constant plus `ROUTE_END`, `ROUTE_REFINE`, `graph_recursion_limit`, `initial_graph_state`, `load_state` from `deep_research.graph.state` (Task 2); `StateGraph`, `START`, `END` from `langgraph.graph`; `InMemorySaver` from `langgraph.checkpoint.memory` (Task 1).
- Produces, all importable from `deep_research.graph.orchestrator`:
  - `AGENT_NODE_ORDER: tuple[str, ...]` — the five nodes before the Critic, in order
  - `ResearchAgents` — a frozen dataclass with `planner`, `researcher`, `source_evaluator`, `fact_checker`, `synthesizer`, `critic`, each a `ResearchAgent`
  - `build_research_graph(agents: ResearchAgents) -> StateGraph`
  - `compile_research_graph(agents: ResearchAgents, *, checkpointer: Any | None = None) -> Any`
  - `build_checkpointer(*, enabled: bool) -> Any | None`
  - `session_config(session_id: str, *, max_iterations: int) -> dict[str, Any]`
- Produces, importable from `tests.graph_fakes`:
  - `fake_research_agents(**overrides: FakeAgent) -> ResearchAgents`

- [ ] **Step 1: Extend the fakes**

Append to `tests/graph_fakes.py`. Add `ResearchAgents` to the imports at the top of the file:

```python
from deep_research.graph.orchestrator import ResearchAgents
```

and append at the end:

```python
def fake_research_agents(**overrides: FakeAgent) -> ResearchAgents:
    """A full set of agents whose default pass answers the question once.

    Every slot can be replaced by keyword, which is how a test scripts a
    critic that asks for a refinement or a fact checker that fails.
    """
    defaults = {
        "planner": FakeAgent("planner", [{"sub_topics": [fake_sub_topic()]}]),
        "researcher": FakeAgent(
            "researcher", [{"raw_findings": [fake_finding()]}]
        ),
        "source_evaluator": FakeAgent(
            "source_evaluator", [{"evaluated_sources": [fake_scored_source()]}]
        ),
        "fact_checker": FakeAgent(
            "fact_checker", [{"verified_claims": [fake_claim()]}]
        ),
        "synthesizer": FakeAgent(
            "synthesizer", [{"report": "# Research report: pass 1"}]
        ),
        "critic": FakeAgent(
            "critic",
            [{"critique": fake_critique(should_continue=False, score=9)}],
        ),
    }
    defaults.update(overrides)
    return ResearchAgents(**defaults)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_graph/test_orchestrator.py`:

```python
"""Tests for the compiled research graph: sequence, loop, bound, failure."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    build_checkpointer,
    compile_research_graph,
    session_config,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    NODE_NAMES,
    REFINE_NODE,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.utils.types import ResearchError, ResearchState

from tests.graph_fakes import (
    FakeAgent,
    fake_critique,
    fake_finding,
    fake_research_agents,
)


async def _run(agents, *, max_iterations: int = 3) -> ResearchState:
    graph = compile_research_graph(agents)
    channel = initial_graph_state(
        session_id="session-1",
        question="How mature is quantum error correction?",
        max_iterations=max_iterations,
    )
    result = await graph.ainvoke(
        channel, session_config("session-1", max_iterations=max_iterations)
    )
    return load_state(result)


def _nodes_visited(state: ResearchState) -> list[str]:
    return [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.started"
    ]


def _route_reasons(state: ResearchState) -> list[str]:
    return [
        event.metadata["reason"]
        for event in state.events
        if event.event_type == "graph.route.decided"
    ]


def test_the_agent_node_order_matches_the_designed_sequence() -> None:
    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
    )
    assert set(AGENT_NODE_ORDER) | {CRITIC_NODE, REFINE_NODE} == set(NODE_NAMES)


@pytest.mark.asyncio
async def test_the_happy_path_runs_every_agent_once_in_order() -> None:
    agents = fake_research_agents()

    state = await _run(agents)

    assert _nodes_visited(state) == [*AGENT_NODE_ORDER, CRITIC_NODE]
    assert state.report == "# Research report: pass 1"
    assert state.iteration == 0
    assert _route_reasons(state) == ["critique_satisfied"]
    assert graph_status(state) == "completed"
    assert not state.errors


@pytest.mark.asyncio
async def test_the_critic_can_send_the_graph_back_to_the_researcher() -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("Break-even was reached.")]},
                {"raw_findings": [fake_finding("Costs fell tenfold.")]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        CRITIC_NODE,
    ]
    assert len(agents.planner.calls) == 1
    assert len(agents.researcher.calls) == 2
    assert _route_reasons(state) == [
        "refinement_requested",
        "critique_satisfied",
    ]
    assert graph_status(state) == "completed"


@pytest.mark.asyncio
async def test_a_refinement_pass_advances_the_macro_iteration() -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    state = await _run(agents)

    assert state.iteration == 1
    refinements = [
        event
        for event in state.events
        if event.event_type == "graph.refinement.started"
    ]
    assert [event.metadata["iteration"] for event in refinements] == [1]
    # The Researcher's second pass sees the critique that asked for it.
    assert agents.researcher.calls[1].critique is not None


@pytest.mark.asyncio
async def test_state_appends_accumulate_and_scalars_replace_across_passes(
) -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("first")]},
                {"raw_findings": [fake_finding("second")]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert [finding.content for finding in state.raw_findings] == [
        "first",
        "second",
    ]
    assert state.report == "# pass 2"
    assert state.critique is not None
    assert state.critique.score == 9
    assert len(state.evaluated_sources) == 2
    assert len(state.verified_claims) == 2


@pytest.mark.asyncio
async def test_the_iteration_bound_forces_an_end_the_critic_did_not_want(
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        )
    )

    state = await _run(agents, max_iterations=1)

    assert len(agents.researcher.calls) == 2
    assert state.iteration == 1
    assert _route_reasons(state) == [
        "refinement_requested",
        "max_iterations_reached",
    ]
    assert graph_status(state) == "max_iterations"
    assert state.critique is not None
    assert state.critique.should_continue is True


@pytest.mark.asyncio
async def test_an_agent_failure_stops_the_run_and_keeps_what_was_collected(
) -> None:
    agents = fake_research_agents(
        fact_checker=FakeAgent(
            "fact_checker", [AgentConfigurationError("bad scratchpad")]
        )
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
    ]
    assert agents.synthesizer.calls == []
    assert agents.critic.calls == []
    assert is_halted(state)
    assert graph_status(state) == "failed"
    assert _route_reasons(state) == ["halted"]
    # Everything gathered before the failure survives.
    assert len(state.raw_findings) == 1
    assert len(state.evaluated_sources) == 1
    skipped = [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.skipped"
    ]
    assert skipped == ["synthesizer", "critic"]


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_never_stops_the_graph() -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [{"raw_findings": [fake_finding()], "errors": [recoverable]}],
        )
    )

    state = await _run(agents)

    assert graph_status(state) == "completed"
    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]


@pytest.mark.asyncio
async def test_an_agent_that_returns_invalid_state_fails_the_run() -> None:
    agents = fake_research_agents(
        synthesizer=FakeAgent("synthesizer", [{"iteration": 5}])
    )

    state = await _run(agents)

    assert graph_status(state) == "failed"
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]


def test_the_session_config_pins_the_thread_and_the_superstep_bound() -> None:
    config = session_config("session-1", max_iterations=2)

    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] > 0


def test_a_checkpointer_is_built_only_when_it_is_asked_for() -> None:
    assert build_checkpointer(enabled=False) is None
    assert build_checkpointer(enabled=True) is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_graph/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.graph.orchestrator'`

- [ ] **Step 4: Write minimal implementation**

Create `src/deep_research/graph/orchestrator.py`:

```python
"""The research graph: assembly, compilation, and the session runner.

The only module in this package that imports LangGraph. Everything the
graph *decides* — routing, halting, the macro-iteration bound — lives in
``state.py`` and ``nodes.py``, framework-free, so this module is pure
wiring and the rules are testable without compiling anything.

Graph shape:

    START -> planner -> researcher -> source_evaluator -> fact_checker
          -> synthesizer -> critic -> {refine -> researcher | END}

``refine`` is the hop that carries the macro-iteration increment. It exists
because a LangGraph conditional edge chooses a destination but cannot write
state, and the increment has to happen somewhere both the graph and a test
can see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deep_research.graph.nodes import (
    ResearchAgent,
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    FACT_CHECKER_NODE,
    PLANNER_NODE,
    REFINE_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_REFINE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    graph_recursion_limit,
)

# The five agent nodes that run before the Critic, in order. The Critic and
# the refinement hop are named separately because they are wired by
# different calls: one conditional edge, one plain edge back.
AGENT_NODE_ORDER = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    FACT_CHECKER_NODE,
    SYNTHESIZER_NODE,
)


@dataclass(frozen=True)
class ResearchAgents:
    """The six agents one research graph runs.

    A dataclass rather than a mapping so ``build_research_graph`` has a
    typed six-field signature: forgetting the Fact Checker is a
    ``TypeError`` at construction, not a ``KeyError`` deep inside assembly.
    """

    planner: ResearchAgent
    researcher: ResearchAgent
    source_evaluator: ResearchAgent
    fact_checker: ResearchAgent
    synthesizer: ResearchAgent
    critic: ResearchAgent


def build_research_graph(agents: ResearchAgents) -> StateGraph:
    """Assemble the uncompiled research graph."""
    builder = StateGraph(ResearchGraphState)
    builder.add_node(PLANNER_NODE, agent_node(agents.planner, node_name=PLANNER_NODE))
    builder.add_node(
        RESEARCHER_NODE, agent_node(agents.researcher, node_name=RESEARCHER_NODE)
    )
    builder.add_node(
        SOURCE_EVALUATOR_NODE,
        agent_node(agents.source_evaluator, node_name=SOURCE_EVALUATOR_NODE),
    )
    builder.add_node(
        FACT_CHECKER_NODE,
        agent_node(agents.fact_checker, node_name=FACT_CHECKER_NODE),
    )
    builder.add_node(
        SYNTHESIZER_NODE,
        agent_node(agents.synthesizer, node_name=SYNTHESIZER_NODE),
    )
    builder.add_node(CRITIC_NODE, critic_node(agents.critic, node_name=CRITIC_NODE))
    builder.add_node(REFINE_NODE, refine_node)

    builder.add_edge(START, PLANNER_NODE)
    for source, destination in zip(
        AGENT_NODE_ORDER, (*AGENT_NODE_ORDER[1:], CRITIC_NODE), strict=True
    ):
        builder.add_edge(source, destination)
    builder.add_conditional_edges(
        CRITIC_NODE,
        route_after_critic,
        {ROUTE_REFINE: REFINE_NODE, ROUTE_END: END},
    )
    builder.add_edge(REFINE_NODE, RESEARCHER_NODE)
    return builder


def compile_research_graph(
    agents: ResearchAgents,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the research graph, optionally with a checkpointer.

    The checkpointer is injected rather than chosen here so a durable saver
    can replace the in-memory one without touching a node.
    """
    return build_research_graph(agents).compile(checkpointer=checkpointer)


def build_checkpointer(*, enabled: bool) -> Any | None:
    """Return the checkpointer this build supports, or ``None``.

    ``InMemorySaver`` survives a resume inside one process, which is what
    spec 11 asks for and what its tests exercise. Durable checkpointing is
    a hardening concern and drops in through ``compile_research_graph``.
    """
    return InMemorySaver() if enabled else None


def session_config(session_id: str, *, max_iterations: int) -> dict[str, Any]:
    """The LangGraph run config for one research session.

    ``thread_id`` is the session id, which is what makes resume-by-session
    work. ``recursion_limit`` is always set explicitly: it is derived from
    the graph's real shape rather than left to a framework default that has
    changed between releases.
    """
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": graph_recursion_limit(max_iterations),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_graph/ -v && ruff check src/deep_research/graph/ tests/graph_fakes.py tests/test_graph/`
Expected: PASS, no lint findings.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/orchestrator.py tests/graph_fakes.py \
  tests/test_graph/test_orchestrator.py
git commit -m "feat: assemble the research graph with critic loop-back routing"
```

---

### Task 6: The Session Runner — Trace Metadata, Checkpointing, And Resume

**Files:**
- Modify: `src/deep_research/graph/orchestrator.py` (append `GraphRun`, `run_research_graph`, `resume_research_graph`)
- Modify: `src/deep_research/utils/config.py` (`GraphConfig`, `ConfigSettings.graph`, two environment overrides)
- Modify: `config.yaml` (a `graph:` block)
- Modify: `tests/test_config.py` (graph settings)
- Create: `tests/test_graph/test_session.py`

**Interfaces:**
- Consumes: everything from Task 5; `GraphResumeError` from `deep_research.graph.errors` (Task 3); `session_completed_event`, `session_started_event` from `deep_research.graph.events` (Task 3); `DEFAULT_MAX_ITERATIONS`, `dump_state`, `graph_route`, `graph_status`, `initial_graph_state`, `load_state` from `deep_research.graph.state` (Task 2); `Tracker` from `deep_research.observability`; `MemorySnapshot`, `merge_research_state` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.graph.orchestrator`:
  - `GraphRun` — a frozen dataclass with `session_id: str`, `state: ResearchState`, `status: str`, `trace_url: str | None`
  - `run_research_graph(*, graph, tracker, session_id, question, max_iterations=DEFAULT_MAX_ITERATIONS, memory_context=None, checkpointing=False) -> GraphRun`
  - `resume_research_graph(*, graph, tracker, session_id, max_iterations=DEFAULT_MAX_ITERATIONS) -> GraphRun`
- Produces, importable from `deep_research.utils.config`:
  - `GraphConfig` — `max_iterations: int = 3` (`ge=1`), `checkpointing_enabled: bool = False`
  - `ConfigSettings.graph: GraphConfig`

- [ ] **Step 1: Write the failing config tests**

Append to `tests/test_config.py`. Add `GraphConfig` to the existing `deep_research.utils.config` import:

```python
def test_graph_settings_default_to_a_bounded_uncheckpointed_run() -> None:
    settings = ConfigSettings()

    assert settings.graph.max_iterations == 3
    assert settings.graph.checkpointing_enabled is False


def test_graph_settings_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"graph": {"max_iterations": 5, "checkpointing_enabled": True}}
        ),
        encoding="utf-8",
    )

    settings = load_config(str(path))

    assert settings.graph.max_iterations == 5
    assert settings.graph.checkpointing_enabled is True


def test_graph_settings_take_environment_overrides(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_MAX_ITERATIONS", "7")
    monkeypatch.setenv("GRAPH_CHECKPOINTING_ENABLED", "true")

    settings = load_config(str(config_path))

    assert settings.graph.max_iterations == 7
    assert settings.graph.checkpointing_enabled is True


def test_a_zero_iteration_budget_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"graph": {"max_iterations": 0}}), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        load_config(str(path))
```

- [ ] **Step 2: Write the failing session tests**

Create `tests/test_graph/test_session.py`:

```python
"""Tests for the session runner: trace metadata, checkpointing, resume."""

from __future__ import annotations

from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.errors import GraphResumeError
from deep_research.graph.orchestrator import (
    build_checkpointer,
    compile_research_graph,
    resume_research_graph,
    run_research_graph,
)
from deep_research.graph.state import DEFAULT_MAX_ITERATIONS
from deep_research.observability import AgentMetric, Tracker
from deep_research.utils.config import GraphConfig
from deep_research.utils.types import MemorySnapshot, ResearchState

from tests.graph_fakes import FakeAgent, fake_critique, fake_research_agents

QUESTION = "How mature is quantum error correction?"


class SpanningFakeAgent(FakeAgent):
    """A fake that opens the agent span a real agent would.

    Proves the session span's trace context reaches a LangGraph node — the
    contextvar has to survive whatever task LangGraph runs the node in, and
    ``Tracker.agent_span`` raises without an active session span.
    """

    def __init__(self, name: str, tracker: Tracker) -> None:
        super().__init__(name)
        self._tracker = tracker

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async with self._tracker.agent_span(self.name):
            return await super().run(state)


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


@pytest.mark.asyncio
async def test_a_run_returns_the_final_state_and_its_status(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        max_iterations=2,
    )

    assert run.session_id == "session-1"
    assert run.status == "completed"
    assert run.state.report == "# Research report: pass 1"
    assert run.state.original_question == QUESTION
    assert run.state.max_iterations == 2


@pytest.mark.asyncio
async def test_a_run_brackets_itself_with_session_events(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    types = _event_types(run.state)

    assert types[0] == "graph.session.started"
    assert types[-1] == "graph.session.completed"
    assert run.state.events[-1].metadata["status"] == "completed"
    assert run.state.events[-1].metadata["has_report"] is True


@pytest.mark.asyncio
async def test_a_run_carries_the_callers_memory_context_to_the_planner(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )

    assert agents.planner.calls[0].memory_context.suggested_strategies == [
        "start broad"
    ]


@pytest.mark.asyncio
async def test_a_run_attaches_session_metadata_and_routes_to_the_trace(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    completed = [
        event
        for event in tracker.events
        if event.event_type == "observability.span.completed"
        and event.metadata["span_name"] == "research.session"
    ]
    assert len(completed) == 1
    assert completed[0].metadata["session_id"] == "session-1"
    assert completed[0].metadata["success"] is True


@pytest.mark.asyncio
async def test_every_node_gets_its_own_agent_span(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=SpanningFakeAgent("planner", tracker),
        researcher=SpanningFakeAgent("researcher", tracker),
        source_evaluator=SpanningFakeAgent("source_evaluator", tracker),
        fact_checker=SpanningFakeAgent("fact_checker", tracker),
        synthesizer=SpanningFakeAgent("synthesizer", tracker),
        critic=SpanningFakeAgent("critic", tracker),
    )

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    spanned = {
        metric.agent_name
        for metric in tracker.metrics
        if isinstance(metric, AgentMetric)
    }
    assert spanned == {
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    }


@pytest.mark.asyncio
async def test_a_checkpointed_session_can_be_resumed_by_its_session_id(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        checkpointing=True,
    )
    call_counts = [len(agents.planner.calls), len(agents.critic.calls)]

    resumed = await resume_research_graph(
        graph=graph, tracker=tracker, session_id="session-1"
    )

    assert resumed.state.report == first.state.report
    assert resumed.state.original_question == QUESTION
    assert resumed.status == "completed"
    # A finished session replays its checkpoint; no agent runs again.
    assert [len(agents.planner.calls), len(agents.critic.calls)] == call_counts


@pytest.mark.asyncio
async def test_a_checkpointed_run_records_that_checkpointing_was_on(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        checkpointing=True,
    )

    assert run.state.events[0].metadata["checkpointing"] is True


@pytest.mark.asyncio
async def test_resuming_an_unknown_session_is_refused(tracker: Tracker) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    with pytest.raises(GraphResumeError, match="no checkpoint"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="never-run"
        )


@pytest.mark.asyncio
async def test_resuming_without_a_checkpointer_is_refused(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    with pytest.raises(GraphResumeError, match="checkpointer"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="session-1"
        )


@pytest.mark.asyncio
async def test_a_failed_run_still_returns_its_state(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=FakeAgent("planner", [AgentConfigurationError("bad wiring")])
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.status == "failed"
    assert run.state.original_question == QUESTION
    assert run.state.events[-1].metadata["status"] == "failed"


def test_the_graph_config_default_matches_the_graph_module_default() -> None:
    assert GraphConfig().max_iterations == DEFAULT_MAX_ITERATIONS
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_graph/test_session.py tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_research_graph' from 'deep_research.graph.orchestrator'` and `ImportError: cannot import name 'GraphConfig'`.

- [ ] **Step 4: Add the graph configuration**

In `src/deep_research/utils/config.py`, add this class immediately after `AgentRuntimeConfig`:

```python
class GraphConfig(BaseModel):
    """Bounds and durability for the macro research loop.

    ``max_iterations`` is the macro refinement budget the Critic spends;
    ``AgentRuntimeConfig.max_iterations`` is the *micro* ReAct bound inside
    one agent. They are deliberately separate numbers.

    There is no ``recursion_limit`` setting: LangGraph's superstep bound is
    derived from ``max_iterations`` and the graph's node count, and a second
    knob that could contradict the first is a bug waiting to happen.
    """

    max_iterations: int = Field(default=3, ge=1)
    checkpointing_enabled: bool = False
```

Add the field to `ConfigSettings`, between `agents` and `output`:

```python
    agents: AgentRuntimeConfig = AgentRuntimeConfig()
    graph: GraphConfig = GraphConfig()
    output: OutputConfig = OutputConfig()
```

Add the two overrides to `_ENVIRONMENT_OVERRIDES`, after the `AGENTS_*` block:

```python
    "GRAPH_MAX_ITERATIONS": ("graph", "max_iterations"),
    "GRAPH_CHECKPOINTING_ENABLED": ("graph", "checkpointing_enabled"),
```

In `config.yaml`, add a block between `agents:` and `output:`:

```yaml
graph:
  max_iterations: 3
  checkpointing_enabled: false
```

- [ ] **Step 5: Write the session runner**

Append to `src/deep_research/graph/orchestrator.py`. Replace the module's whole import
block with this final version — the `deep_research.graph.state` import grows, and four
new import blocks join it, all isort-ordered:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deep_research.graph.errors import GraphResumeError
from deep_research.graph.events import (
    session_completed_event,
    session_started_event,
)
from deep_research.graph.nodes import (
    ResearchAgent,
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    DEFAULT_MAX_ITERATIONS,
    FACT_CHECKER_NODE,
    PLANNER_NODE,
    REFINE_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_REFINE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    load_state,
)
from deep_research.observability import Tracker
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchState,
    merge_research_state,
)
```

Then append to the end of the module:

```python
@dataclass(frozen=True)
class GraphRun:
    """Everything one research session produced."""

    session_id: str
    state: ResearchState
    status: str
    trace_url: str | None


def _session_outputs(state: ResearchState, *, status: str) -> dict[str, Any]:
    """The session metadata this run attaches to its LangSmith span.

    Counts, identifiers, and enumerated reasons only — never provider text
    and never a report body. ``route_decisions`` is the whole macro history
    of the run, which is what makes "graph route decisions are observable"
    true from the trace alone.
    """
    critique = state.critique
    return {
        "session_id": state.session_id,
        "status": status,
        "route_reason": graph_route(state)[1],
        "route_decisions": [
            event.metadata["reason"]
            for event in state.events
            if event.event_type == "graph.route.decided"
        ],
        "iteration": state.iteration,
        "max_iterations": state.max_iterations,
        "sub_topic_count": len(state.sub_topics),
        "finding_count": len(state.raw_findings),
        "source_count": len(state.evaluated_sources),
        "claim_count": len(state.verified_claims),
        "critic_score": None if critique is None else critique.score,
        "has_report": state.report is not None,
        "error_count": len(state.errors),
    }


async def _invoke(
    *,
    graph: Any,
    tracker: Tracker,
    channel: ResearchGraphState | None,
    session_id: str,
    question: str,
    max_iterations: int,
) -> GraphRun:
    """Run or resume the compiled graph inside one session span.

    ``channel`` is ``None`` when resuming: LangGraph reads the thread's
    checkpoint instead of an input. The session span is what gives every
    agent inside a node an active trace context — ``Tracker.agent_span``
    raises without one.
    """
    async with tracker.session_span(session_id, question) as span:
        result = await graph.ainvoke(
            channel, session_config(session_id, max_iterations=max_iterations)
        )
        final = load_state(result)
        status = graph_status(final)
        final = merge_research_state(
            final,
            {
                "events": [
                    session_completed_event(
                        status=status,
                        iteration=final.iteration,
                        error_count=len(final.errors),
                        has_report=final.report is not None,
                    )
                ]
            },
        )
        span.set_outputs(_session_outputs(final, status=status))
        trace_url = span.trace_url

    return GraphRun(
        session_id=session_id,
        state=final,
        status=status,
        trace_url=trace_url,
    )


async def run_research_graph(
    *,
    graph: Any,
    tracker: Tracker,
    session_id: str,
    question: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    memory_context: MemorySnapshot | None = None,
    checkpointing: bool = False,
) -> GraphRun:
    """Run one research session from the question to a final status.

    ``session_started_event`` is written into the initial state *before* the
    graph runs, so it is checkpointed with everything else.
    ``session_completed_event`` is appended after, so it is not: the
    checkpoint is written by the graph, and a completion recorded by the
    runner belongs to the run rather than to the durable state.
    """
    state = load_state(
        initial_graph_state(
            session_id=session_id,
            question=question,
            max_iterations=max_iterations,
            memory_context=memory_context,
        )
    )
    state = merge_research_state(
        state,
        {
            "events": [
                session_started_event(
                    session_id=session_id,
                    max_iterations=max_iterations,
                    checkpointing=checkpointing,
                )
            ]
        },
    )
    return await _invoke(
        graph=graph,
        tracker=tracker,
        channel=dump_state(state),
        session_id=session_id,
        question=state.original_question,
        max_iterations=max_iterations,
    )


async def resume_research_graph(
    *,
    graph: Any,
    tracker: Tracker,
    session_id: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> GraphRun:
    """Continue a checkpointed session from where it stopped.

    The question is read back out of the checkpoint rather than re-supplied,
    so a resume cannot silently research something else under a session id
    that already means something.
    """
    config = session_config(session_id, max_iterations=max_iterations)
    try:
        snapshot = await graph.aget_state(config)
    except ValueError as error:
        raise GraphResumeError(
            "resuming a session requires a graph compiled with a checkpointer"
        ) from error

    values = snapshot.values
    if not isinstance(values, dict) or "state" not in values:
        raise GraphResumeError(
            f"no checkpoint was recorded for session {session_id}"
        )

    return await _invoke(
        graph=graph,
        tracker=tracker,
        channel=None,
        session_id=session_id,
        question=load_state(values).original_question,
        max_iterations=max_iterations,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_graph/ tests/test_config.py -v && ruff check src/deep_research/ tests/`
Expected: PASS, no lint findings.

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/graph/orchestrator.py src/deep_research/utils/config.py \
  config.yaml tests/test_config.py tests/test_graph/test_session.py
git commit -m "feat: run and resume a research session through the graph"
```

---

### Task 7: Publish The Graph Surface

**Files:**
- Modify: `src/deep_research/graph/__init__.py` (replace the stub)
- Modify: `tests/test_imports.py` (append three tests)
- Modify: `README.md`

**Interfaces:**
- Consumes: every public name produced by Tasks 2–6.
- Produces: `deep_research.graph.__all__`, the single import surface every later spec (12 CLI, 13 API, 14 UI) builds against.

- [ ] **Step 1: Write the failing import tests**

Append to `tests/test_imports.py`:

```python
def test_graph_contracts_import_from_package() -> None:
    from deep_research.graph import (  # noqa: F401
        AGENT_NODE_ORDER,
        CRITIC_NODE,
        DEFAULT_MAX_ITERATIONS,
        FACT_CHECKER_NODE,
        GRAPH_ERROR_REASONS,
        GRAPH_ROUTES,
        GRAPH_SOURCE,
        GRAPH_STATUSES,
        HALTING_ERROR_TYPES,
        NODE_NAMES,
        PLANNER_NODE,
        REFINE_NODE,
        RESEARCHER_NODE,
        ROUTE_END,
        ROUTE_REFINE,
        SOURCE_EVALUATOR_NODE,
        SYNTHESIZER_NODE,
        GraphConfigurationError,
        GraphError,
        GraphNode,
        GraphResumeError,
        GraphRun,
        ResearchAgent,
        ResearchAgents,
        ResearchGraphState,
        agent_configuration_error,
        agent_node,
        build_checkpointer,
        build_research_graph,
        compile_research_graph,
        critic_node,
        dump_state,
        graph_error,
        graph_event,
        graph_recursion_limit,
        graph_route,
        graph_status,
        initial_graph_state,
        invalid_agent_state_error,
        invalid_route_error,
        is_halted,
        load_state,
        node_completed_event,
        node_skipped_event,
        node_started_event,
        planning_failed_error,
        provider_configuration_error,
        refine_node,
        refinement_started_event,
        resume_research_graph,
        route_after_critic,
        route_decided_event,
        run_research_graph,
        session_completed_event,
        session_config,
        session_started_event,
    )


def test_graph_all_surface_is_fully_covered() -> None:
    """Every name in ``deep_research.graph.__all__`` must actually resolve."""
    import deep_research.graph as graph_pkg

    missing = [name for name in graph_pkg.__all__ if not hasattr(graph_pkg, name)]
    assert not missing, f"__all__ entries missing from package: {missing}"


def test_graph_submodule_public_names_all_reach_all() -> None:
    """Every public module-level name in ``graph/*.py`` must be exported.

    The graph twin of ``test_agent_submodule_public_names_all_reach_all``:
    it catches a public name that exists in a submodule but was never wired
    into ``__all__``, which the import list above cannot.
    """
    import ast
    from pathlib import Path

    import deep_research.graph as graph_pkg

    graph_dir = Path(graph_pkg.__file__).parent
    submodules = ["errors", "events", "nodes", "orchestrator", "state"]

    missing: list[str] = []
    for module_name in submodules:
        path = graph_dir / f"{module_name}.py"
        assert path.is_file(), f"expected submodule file missing: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            name: str | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        break
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                name = node.target.id

            if name is None or name.startswith("_"):
                continue
            if name not in graph_pkg.__all__:
                missing.append(f"{module_name}.{name}")

    assert not missing, (
        "public submodule names missing from `deep_research.graph.__all__`: "
        f"{missing}"
    )


def test_the_graph_nodes_cover_the_designed_sequence() -> None:
    from deep_research.graph import AGENT_NODE_ORDER, CRITIC_NODE, NODE_NAMES

    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
    )
    assert CRITIC_NODE == "critic"
    assert NODE_NAMES[-1] == "refine"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imports.py -v`
Expected: FAIL with `ImportError: cannot import name 'AGENT_NODE_ORDER' from 'deep_research.graph'`

- [ ] **Step 3: Publish the package surface**

Replace the whole contents of `src/deep_research/graph/__init__.py`:

```python
"""LangGraph orchestration: the research graph and its session runner."""

from deep_research.graph.errors import (
    GRAPH_ERROR_REASONS,
    GraphConfigurationError,
    GraphError,
    GraphResumeError,
    agent_configuration_error,
    graph_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
)
from deep_research.graph.events import (
    graph_event,
    node_completed_event,
    node_skipped_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
    session_completed_event,
    session_started_event,
)
from deep_research.graph.nodes import (
    GraphNode,
    ResearchAgent,
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    GraphRun,
    ResearchAgents,
    build_checkpointer,
    build_research_graph,
    compile_research_graph,
    resume_research_graph,
    run_research_graph,
    session_config,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    DEFAULT_MAX_ITERATIONS,
    FACT_CHECKER_NODE,
    GRAPH_ROUTES,
    GRAPH_SOURCE,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    PLANNER_NODE,
    REFINE_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_REFINE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)

__all__ = [
    "AGENT_NODE_ORDER",
    "CRITIC_NODE",
    "DEFAULT_MAX_ITERATIONS",
    "FACT_CHECKER_NODE",
    "GRAPH_ERROR_REASONS",
    "GRAPH_ROUTES",
    "GRAPH_SOURCE",
    "GRAPH_STATUSES",
    "HALTING_ERROR_TYPES",
    "NODE_NAMES",
    "PLANNER_NODE",
    "REFINE_NODE",
    "RESEARCHER_NODE",
    "ROUTE_END",
    "ROUTE_REFINE",
    "SOURCE_EVALUATOR_NODE",
    "SYNTHESIZER_NODE",
    "GraphConfigurationError",
    "GraphError",
    "GraphNode",
    "GraphResumeError",
    "GraphRun",
    "ResearchAgent",
    "ResearchAgents",
    "ResearchGraphState",
    "agent_configuration_error",
    "agent_node",
    "build_checkpointer",
    "build_research_graph",
    "compile_research_graph",
    "critic_node",
    "dump_state",
    "graph_error",
    "graph_event",
    "graph_recursion_limit",
    "graph_route",
    "graph_status",
    "initial_graph_state",
    "invalid_agent_state_error",
    "invalid_route_error",
    "is_halted",
    "load_state",
    "node_completed_event",
    "node_skipped_event",
    "node_started_event",
    "planning_failed_error",
    "provider_configuration_error",
    "refine_node",
    "refinement_started_event",
    "resume_research_graph",
    "route_after_critic",
    "route_decided_event",
    "run_research_graph",
    "session_completed_event",
    "session_config",
    "session_started_event",
]
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest -q && ruff check src/ tests/`
Expected: PASS, no lint findings. If `test_graph_submodule_public_names_all_reach_all` fails, it names the exact missing symbol — add it to both the import block and `__all__`.

- [ ] **Step 5: Document the orchestration layer**

Append this section to `README.md`, immediately after the `## Synthesizer And Critic` section's event table and before `## Development`:

````markdown
## LangGraph Orchestration

`deep_research.graph` wires the six agents into one state graph:

```text
START -> planner -> researcher -> source_evaluator -> fact_checker
      -> synthesizer -> critic -> {refine -> researcher | END}
```

The LangGraph channel carries the whole `ResearchState` as one JSON-safe
mapping under the key `state`, and every node merges its agent's
`ResearchStateUpdate` with `merge_research_state` — the same append/replace
rules and the same "`iteration` moves only through
`advance_research_iteration`" guard the agents already run under. There are
no per-field LangGraph reducers, so there is exactly one implementation of
the merge rules.

`refine` is the hop that carries the macro-iteration increment. It exists
because a conditional edge routes but cannot write state.

Routing is `graph_route`, a pure function of state: a halted run ends, a run
with no critique ends, `Critique.should_continue` being false ends the run,
`state.iteration >= state.max_iterations` ends the run **whatever the critic
recommended**, and only then does the graph loop back. The bound is checked
by the graph itself rather than trusted to the critic, so no model judgement
can make the loop run forever. `graph_status` reads the same decision and
names the outcome `completed`, `max_iterations`, `incomplete`, or `failed`.

Failure is a halt mark in state, not an exception out of `ainvoke`.
`PlanningError`, `AgentConfigurationError`, and `ProviderConfigurationError`
become enumerated `HALTING_ERROR_TYPES` entries; every later node records
`graph.node.skipped` and returns without invoking its agent; the router ends
the run with status `failed` and **everything collected before the failure
survives**. Recoverable agent and tool errors — including the
non-recoverable provider outages agents record for themselves — stay in
`state.errors` and never stop the graph. Any other exception propagates: an
unhandled failure is a defect, not a research outcome.

```python
from deep_research.graph import (
    ResearchAgents,
    build_checkpointer,
    compile_research_graph,
    resume_research_graph,
    run_research_graph,
)

agents = ResearchAgents(
    planner=planner,
    researcher=researcher,
    source_evaluator=source_evaluator,
    fact_checker=fact_checker,
    synthesizer=synthesizer,
    critic=critic,
)
graph = compile_research_graph(
    agents, checkpointer=build_checkpointer(enabled=settings.graph.checkpointing_enabled)
)

run = await run_research_graph(
    graph=graph,
    tracker=tracker,
    session_id=session_id,
    question="How mature is quantum error correction?",
    max_iterations=settings.graph.max_iterations,
    checkpointing=settings.graph.checkpointing_enabled,
)
print(run.status, run.state.report, run.trace_url)

# Later, same process, same session id:
resumed = await resume_research_graph(
    graph=graph, tracker=tracker, session_id=session_id
)
```

`run_research_graph` opens the existing `Tracker` session span, so every
agent inside a node produces its own `agent.<name>` span under
`research.session`; the session span's outputs carry the session id, the
final status, the full list of route decisions, the macro iteration, the
per-collection counts, the critic score, and the error count. Graph
configuration lives in `config.yaml` under `graph:` (`max_iterations`,
`checkpointing_enabled`), overridable with `GRAPH_MAX_ITERATIONS` and
`GRAPH_CHECKPOINTING_ENABLED`. Checkpointing uses an in-process
`InMemorySaver`; a durable saver drops into `compile_research_graph`
without touching a node.

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `graph.session.started` | Runner | `session_id`, `max_iterations`, `checkpointing` |
| `graph.node.started` | Every node | `node`, `iteration` |
| `graph.node.completed` | Every node | `node`, `iteration`, `event_count`, `error_count` |
| `graph.node.skipped` | Every node after a halt | `node`, `iteration`, `reason` |
| `graph.route.decided` | Critic node | `destination`, `reason`, `iteration`, `max_iterations`, `should_continue` |
| `graph.refinement.started` | Refine node | `iteration`, `max_iterations` |
| `graph.session.completed` | Runner | `status`, `iteration`, `error_count`, `has_report` |
````

Update the `## Project Layout` block's graph line:

```
|-- graph/             # LangGraph orchestration: state, nodes, routing, runner
```

Update the phase line at the bottom of `README.md`:

```markdown
- Phase 3: Agents and LangGraph orchestration ← complete (all six agents and the graph)
```

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/__init__.py tests/test_imports.py README.md
git commit -m "docs: publish the langgraph orchestration surface"
```

---

## Open Risks

Named rather than papered over. None blocks the plan; each has a check inside a task.

1. **Installing LangGraph pulls in `langchain-core`, which pins its own `langsmith` floor.** This repository's `Tracker` is built directly on `langsmith`. Task 1 Step 2 runs the whole existing suite immediately after install for exactly this reason. If `tests/test_observability_*.py` breaks, fix it there before Task 2.
2. **`graph.ainvoke(None, config)` on a *finished* thread.** `resume_research_graph` relies on it returning the thread's final values rather than re-running from `START`. `test_resuming_a_finished_thread_returns_its_final_values` in Task 1 pins it. If LangGraph turns out to re-run, resume becomes "resume an interrupted run only" and `resume_research_graph` should return early from the snapshot when `snapshot.next` is empty.
3. **Trace-context propagation into LangGraph nodes.** `Tracker.agent_span` raises without an active session span, and the session span binds through a `ContextVar` set in the caller's coroutine. Async tasks copy the ambient context at creation, so this should hold — `test_every_node_gets_its_own_agent_span` in Task 6 is the assertion that proves it rather than assuming it.
4. **`InMemorySaver` does not survive a process restart.** Spec 11's non-goals rule out production distributed execution and its testing section asks only for mocked checkpointing, so this is in scope as written; a durable saver is spec 15's call and needs no graph change.
5. **Node-level observability leans on LangGraph's own LangSmith integration plus the agents' spans**, because the constraint against a second tracing path forbids adding a `node_span` to `Tracker`. If a later spec wants node timings independent of the agent inside, that is a `Tracker` change, not a graph change.

---

## Self-Review

Run after the last task, against `docs/superpowers/specs/2026-07-25-11-langgraph-orchestration-design.md`.

**Spec coverage**

| Spec requirement | Where |
| --- | --- |
| `orchestrator.py` | Tasks 5 and 6 |
| Graph construction function | Task 5 `build_research_graph` / `compile_research_graph` |
| Node wrappers for each agent | Task 4 `agent_node`, `critic_node`, `refine_node` |
| Conditional routing after Critic | Task 4 `route_after_critic`; Task 2 `graph_route` |
| Checkpoint and resume hooks | Task 6 `build_checkpointer`, `session_config`, `resume_research_graph` |
| Graph-level tests with mocked agents | Every task; `tests/graph_fakes.py`, no network anywhere |
| Full sequence Planner → … → Critic | Task 5 `test_the_happy_path_runs_every_agent_once_in_order` |
| `END` when `should_continue` is false | Task 2 `test_a_satisfied_critic_ends_the_run` |
| Back to Researcher when true and iteration < max | Task 5 `test_the_critic_can_send_the_graph_back_to_the_researcher` |
| Initialize `ResearchState` | Task 2 `initial_graph_state` |
| Invoke each agent node | Task 4 `agent_node` |
| Apply state merge rules | Task 4 (merge inside every node); Task 5 `test_state_appends_accumulate_and_scalars_replace_across_passes` |
| Increment macro iteration on loop-back | Task 4 `refine_node`; Task 5 `test_a_refinement_pass_advances_the_macro_iteration` |
| Record graph-level events | Task 3 (seven builders); asserted in Tasks 4, 5, 6 |
| Surface LangSmith trace metadata | Task 6 `_session_outputs` + the existing session span |
| Nodes visible as separate spans | Task 6 `test_every_node_gets_its_own_agent_span` |
| Resume by `session_id` when checkpointing is enabled | Task 6 `resume_research_graph` + three resume tests |
| Stop on non-recoverable configuration errors | Task 4 `test_a_configuration_failure_halts_the_run_with_state_intact` |
| Recoverable agent and tool errors remain in state | Task 4 and Task 5 `test_a_recoverable_agent_error_never_stops_the_graph` |
| Invalid agent state → record error, stop with failure status | Task 4 and Task 5 `test_an_agent_that_returns_invalid_state_fails_the_run` |
| Happy path test | Task 5 |
| Critic loop-back test | Task 5 |
| Max iteration force end test | Task 5 `test_the_iteration_bound_forces_an_end_the_critic_did_not_want` |
| Agent failure test | Task 5 `test_an_agent_failure_stops_the_run_and_keeps_what_was_collected` |
| State merge test | Task 5 |
| Resume hook test with mocked checkpointing | Task 6 |
| Iteration bounds prevent infinite loops | Task 2 `graph_route` (bound checked in the graph, not read off the critique); Task 4 `refine_node`'s second guard |
| Graph route decisions are observable | Task 3 `route_decided_event`; Task 6 `_session_outputs["route_decisions"]` |

**Non-goals held:** no agent behavior changed (nothing under `src/deep_research/agents/` is touched); no UI, no API, no CLI (`main.py` untouched — spec 12 owns it); no distributed execution (in-process `InMemorySaver` only).

**Type consistency:** `ResearchGraphState` is defined once in `state.py` and is the parameter and return type of every node in `nodes.py` and the schema passed to `StateGraph` in `orchestrator.py`. `load_state` / `dump_state` are the only two functions that touch the channel dict. `graph_route` returns `(destination, reason)` where `destination` is always `ROUTE_REFINE` or `ROUTE_END` and `reason` is always a `GRAPH_ROUTES` key — the same pair the conditional edge's `path_map` maps and the same `reason` `route_decided_event` validates. Every `error_type` `graph_error` can build is in `GRAPH_ERROR_REASONS`, and every one that halts is in `HALTING_ERROR_TYPES` (pinned by `test_every_halting_error_type_has_an_enumerated_reason`). Every `status` in `session_completed_event`, `graph_status`, and `GraphRun.status` comes from `GRAPH_STATUSES`. `ResearchAgent` is the one protocol `agent_node`, `critic_node`, `ResearchAgents`, and `FakeAgent` all satisfy. `GraphConfig.max_iterations` and `DEFAULT_MAX_ITERATIONS` are pinned equal by `test_the_graph_config_default_matches_the_graph_module_default`.
