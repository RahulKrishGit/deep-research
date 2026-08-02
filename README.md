# Deep Research

Multi-agent deep research system using LangGraph, OpenAI, ChromaDB, and LangSmith.

## Project Status

Foundation phase — package skeleton, typed configuration/state, the LangSmith observability foundation, OpenAI chat/embedding providers, core tools, the three-layer memory stack, and the shared agent ReAct runtime.

## Setup

1. **Clone the repo**

   ```bash
   git clone <repo-url>
   cd deep-research
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**

   ```bash
   pip install -e ".[dev]"
   ```

4. **Configure environment**

   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

   `load_config("config.yaml")` automatically loads the `.env` file beside
   `config.yaml`. Values already set by the shell, CI, a container, or the
   deployment platform take precedence over `.env`, so the same loader is safe
   for local development and deployed environments. Keep personal keys only in
   `.env`; it is ignored by Git.

5. **Verify setup**

   ```bash
   python -c "import deep_research; print(deep_research.__version__)"
   pytest
   ```

## Project Layout

```
src/deep_research/     # Package root
|-- main.py            # run_research() entry point
|-- graph/             # LangGraph orchestration (Phase 3)
|-- agents/            # Research agents (Phase 3)
|-- memory/            # Short-term, long-term, procedural memory (Phase 2)
|-- tools/             # Web search, scraping, etc. (Phase 2)
|-- providers/         # LLM providers (Phase 1)
|-- observability/     # LangSmith tracing (Phase 2)
|-- utils/             # Config, types, shared utilities
tests/                 # Test suite
config.yaml            # Default runtime configuration
memory/                # Long-term persistence (gitignored)
output/                # Generated reports (gitignored)
```

## Configuration

See `config.yaml` for default non-secret settings. Copy `.env.example` to `.env`
and add personal API keys for local runs; `load_config("config.yaml")` loads that
sibling file automatically. Shell and CI environment variables take precedence.
Configuration overrides use uppercase full-path names, such as `LLM_MODEL` and
`MEMORY_LONG_TERM_PERSIST_DIRECTORY`. API keys remain environment-only and are
not included in typed settings or telemetry.

## Observability

Set `LANGSMITH_TRACING=false` to keep tracing fully local for tests and offline
development. Set it to `true` and provide non-empty `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` values to mirror the same spans to LangSmith.

Application code imports only the project tracker:

```python
from deep_research.observability import Tracker
from deep_research.utils.config import load_config

settings = load_config("config.yaml")
tracker = Tracker.from_config(settings.langsmith)

async with tracker.session_span("session-123", "Why is the sky blue?"):
    async with tracker.agent_span("planner"):
        async with tracker.tool_span(
            "web_search",
            {"query": "Rayleigh scattering"},
        ):
            results = await search_client.search("Rayleigh scattering")
```

The documentation snippet uses a named `search_client` placeholder only as an example dependency; the observability implementation does not create that client.

Each completed span appends a typed metric and structured event. A LangSmith
transport failure appends a recoverable `ResearchError` and research work
continues locally.

## OpenAI Providers

Set `OPENAI_API_KEY` in the process environment or the repository-root `.env`.
Chat and embedding defaults live under `llm` in `config.yaml`; `LLM_MODEL`,
`LLM_EMBEDDING_MODEL`, `LLM_TIMEOUT`, and `LLM_RETRY_COUNT` override the
corresponding YAML values.

Chat callers use project-owned messages and results, not OpenAI SDK types:

```python
from deep_research.providers import ChatMessage, OpenAIChatProvider

settings = load_config("config.yaml")
chat = OpenAIChatProvider(settings.llm, tracker)

async with tracker.session_span("session-123", "Why is the sky blue?"):
    result = await chat.complete(
        [ChatMessage(role="user", content="Why is the sky blue?")],
        agent_name="researcher",
    )
```

Use `complete_structured(messages, Schema)` for validated Pydantic output. The
provider performs one repair request if validation fails. `OpenAIEmbeddingProvider`
is the synchronous embedding client memory uses — see `embed_query(...)` and
`embed_documents(...)` below.

## Core Tools

Core tools run inside an active tracker session. They return failures as a
`ToolResult` instead of raising operational errors, so callers can retain
partial research progress. Network clients and memory backends are injectable,
which keeps tool tests deterministic without live services. The later memory
stack implements the exported `LongTermMemory` protocol.

```python
import os

from deep_research.observability import Tracker
from deep_research.tools import WebSearchTool, WriteDocumentTool
from deep_research.utils.config import load_config

settings = load_config("config.yaml", strict=True)
tracker = Tracker.from_config(settings.langsmith)

async with tracker.session_span("session-123", "research question"):
    search = WebSearchTool(
        tracker,
        api_key=os.environ["TAVILY_API_KEY"],
        search_depth=settings.tavily.search_depth,
        max_results=settings.tavily.max_results,
    )
    search_result = await search.execute(query="research question")

    writer = WriteDocumentTool(tracker, settings.output.directory)
    report_result = await writer.execute(
        filename="session-report.md",
        content="# Research report\n\n...",
    )
```

## Memory

Three layers, each independently usable:

- `ScratchpadMemory` — synchronous, bounded, per-agent and per-session. Never
  persisted. Optional summarization hook compacts the window instead of
  silently dropping the oldest notes.
- `LongTermMemory` — async, ChromaDB-backed semantic recall over verified
  findings, source reputations, report summaries, and notable failed
  strategies. Persists under `<memory.long_term.persist_directory>/chroma/`.
- `ProceduralMemory` — async, JSON-backed strategy registry at
  `memory/strategies.json`.

```python
from deep_research.memory import LongTermMemory, ProceduralMemory, ScratchpadMemory
from deep_research.providers import OpenAIEmbeddingProvider
from deep_research.utils.config import load_config
from deep_research.utils.types import merge_research_state

settings = load_config("config.yaml")

pad = ScratchpadMemory.from_config(
    settings.memory.short_term, session_id="session-123", agent_name="researcher"
)
pad.add("Tavily returned 5 results.", kind="observation")

long_term = LongTermMemory.from_config(
    settings.memory.long_term, embeddings=OpenAIEmbeddingProvider()
)
hits = await long_term.query("quantum error correction", top_k=5)

procedural = ProceduralMemory.from_config(settings.memory.procedural)
await procedural.load()
await procedural.record_session_outcome(
    topic_type="technology", succeeded=True, iterations=3
)

state = merge_research_state(state, {"errors": long_term.drain_errors()})
```

Memory failures are recoverable. A long-term write returns `False`, a query
returns `[]`, and each failure appends a recoverable `ResearchError` that
`drain_errors()` hands back for merging into `ResearchState.errors` — agents
continue with short-term state. Only startup problems raise
`MemoryInitializationError`. A corrupt `memory/strategies.json` is renamed to
`memory/strategies.json.corrupt-<timestamp>.bak` and the registry restarts
empty.

Long-term and procedural operations emit a `MemoryMetric` (operation, layer,
entry type, top-k, result count, latency, error type) whenever a tracker and an
active session span are available.

## Agent Runtime

`BaseAgent` runs a bounded ReAct loop — prepare context, think, choose an
action, execute a tool, observe, update the scratchpad, stop or continue. It
has no LangGraph dependency: a concrete agent is a plain async object.

A concrete agent implements four required hooks and one required `ClassVar`,
and may override up to three more:

| Hook | Required | Purpose |
| --- | --- | --- |
| `name` | yes (`ClassVar[str]`) | The agent's identity — used in spans, provider calls, and scratchpad matching |
| `output_schema` | yes | The Pydantic model the agent produces |
| `system_prompt(task)` | yes | Developer-role instructions |
| `build_task(state)` | yes | Read `ResearchState`, describe this run |
| `finalize(task, run)` | yes | Turn the finished loop into the typed output |
| `allowed_tools` | no (`ClassVar`) | Tool names this agent may call (default: none) |
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
`agents.prompt_context_entries` (`AGENTS_PROMPT_CONTEXT_ENTRIES`) controls how
many scratchpad entries are rendered into the prompt on each turn.
`agents.observation_summary_chars` (`AGENTS_OBSERVATION_SUMMARY_CHARS`) bounds
how long each tool observation summary can be before it is fed back to the
model and recorded in `ResearchState.errors`.
`outcome.react.stop_reason` is one of `finished`, `sufficient`,
`max_iterations`, `tool_budget_exhausted`, or `provider_error`.

Failures are predictable: a tool failure becomes an observation the model can
react to plus a recoverable `ResearchError`, and the loop continues. A model
provider failure stops the loop with `provider_error` and records a
non-recoverable `ResearchError` — the provider has already applied its
configured retries and its single structured repair attempt, so the agent
adds none of its own. `BaseAgent.run` still calls `finalize(task, run)`
unconditionally after every stop reason, including `provider_error`, so
whether `outcome.result` ends up `None` on failure is entirely up to the
concrete agent's own `finalize`. A well-behaved `finalize` should check
`run.stop_reason` (or the `ReActRun.succeeded` property) and return `None`
itself when the run didn't succeed, rather than assuming the runtime enforces
that for it.

Every iteration opens a `react_iteration_span` carrying agent name, iteration
number, thought summary, selected tool, and observation summary; the agent
span carries the stop reason and counts; token and latency metrics come from
the provider's own `llm_span`.

## Planner And Researcher

`PlannerAgent` turns `state.original_question` into 3–7 distinct, prioritized
`SubTopic` entries. Its ReAct loop is for scoping only — `query_memory` to
recall prior findings, `web_search` to resolve unfamiliar terminology — and
the plan itself comes from one structured-output call afterwards. If that plan
is empty, redundant, out of size bounds, or fails field validation, the agent
makes exactly one repair attempt with the problems listed back to the model,
and raises `PlanningError` if the repair also fails. There is no partial plan.

`ResearcherAgent` runs one bounded ReAct loop **per selected sub-topic**, each
in its own agent span with its own tool budget, using `web_search`,
`web_scraper`, `document_reader`, `query_memory`, and `save_to_memory`.
Sub-topics are ordered by Critic-flagged gaps first, then by priority
ascending, and capped at `max_sub_topics`. After each loop, a structured
extraction pass over the loop's actual tool payloads produces `Finding`
entries — skipped entirely when the loop retrieved nothing, so a source is
never invented. A tool failure is an observation and the loop continues; a
provider failure stops the remaining sub-topics with the findings so far kept.
A high-priority sub-topic that produced no findings records a recoverable
`researcher_sub_topic_without_findings` error in `state.errors`. A
high-priority sub-topic that was never attempted at all records a recoverable
`researcher_sub_topic_skipped` error instead — this can happen either because
`max_sub_topics` truncated the planned list before its turn came up
(`reason="cap"`), or because an earlier sub-topic's non-recoverable provider
failure stopped the pass before it could run
(`reason="provider_failure_stopped_processing"`). The two errors are mutually
exclusive for a given sub-topic: a run that died to a provider failure is
reported as that failure, never also as "no findings".

```python
from deep_research.agents import PlannerAgent, ResearcherAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    plan_run = await planner.run(state)
    state = merge_research_state(state, plan_run.state_update)

    research_run = await researcher.run(state)
    state = merge_research_state(state, research_run.state_update)
```

Priority convention: **lower is more important**. Priority 1 is the most
important sub-topic; `HIGH_PRIORITY_THRESHOLD` (2) is the largest value still
treated as high priority.

Both agents append progress events to `state.events`:

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `planner.planning.started` | Planner | `iteration`, `min_sub_topics`, `max_sub_topics` |
| `planner.memory.recalled` | Planner | `recalled_findings`, `suggested_strategies` |
| `planner.planning.completed` | Planner | `sub_topic_count`, `repair_attempted`, `stop_reason` |
| `researcher.sub_topic.started` | Researcher | `sub_topic`, `priority`, `existing_sources` |
| `researcher.tool_call` | Researcher | `tool`, `iteration`, `success`, `error_type` |
| `researcher.sub_topic.completed` | Researcher | `stop_reason`, `iterations`, `tool_calls`, `findings` |
| `researcher.research.completed` | Researcher | `sub_topics_planned`, `sub_topics_researched`, `sub_topics_skipped`, `findings` |

Neither agent sends a domain type to OpenAI. `SubTopic` and `Finding` declare
`Field(min_length=1)` constraints that render as `minLength`/`minItems`, which
strict structured outputs reject, so the provider is asked for
`ResearchPlanDraft` and `SubTopicFindingsDraft` — constraint-free mirrors —
and the drafts are validated into the domain types locally.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/
```

## Phases

- Phase 1: Core package foundation, config, types, providers
- Phase 2: Memory and tools
- Phase 3: Agents and LangGraph orchestration ← current (runtime, Planner, and Researcher complete; Source Evaluator, Fact Checker, Synthesizer, Critic, and the graph pending)
- Phase 4: CLI, API, and UI interfaces
- Phase 5: Tests and verification
