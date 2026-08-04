# Deep Research

Multi-agent deep research system using LangGraph, selectable DeepSeek/OpenAI chat, OpenAI embeddings, ChromaDB, and LangSmith.

## Project Status

Foundation phase — package skeleton, typed configuration/state, the LangSmith observability foundation, chat and embedding providers, core tools, the three-layer memory stack, and the shared agent ReAct runtime.

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
|-- graph/             # LangGraph orchestration: state, nodes, routing, runner
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

## Chat and Embedding Providers

Chat defaults are committed under `llm` in `config.yaml`: provider `deepseek`,
model `deepseek-v4-flash`, thinking mode `enabled`, and reasoning effort
`high`. `LLM_PROVIDER`, `LLM_MODEL`, `LLM_THINKING_MODE`,
`LLM_REASONING_EFFORT`, `LLM_EMBEDDING_MODEL`, `LLM_TIMEOUT`, and
`LLM_RETRY_COUNT` override the corresponding YAML values.

Set `DEEPSEEK_API_KEY` for the default DeepSeek chat and `OPENAI_API_KEY` for
embeddings, in the process environment or the repository-root `.env`. OpenAI
embeddings remain active in DeepSeek mode — memory always embeds through
`OpenAIEmbeddingProvider`, whatever chat provider is selected.

A complete DeepSeek configuration with per-agent overrides:

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash
  thinking_mode: enabled
  reasoning_effort: high
  model_overrides:
    planner: deepseek-v4-flash
    critic:
      model: deepseek-v4-flash
      thinking_mode: enabled
      reasoning_effort: max
```

The `planner` override is the legacy string form — model only, inheriting the
global thinking mode and reasoning effort. The `critic` override is the
structured form with its own model, thinking mode, and reasoning effort. Both
forms remain valid.

To switch chat to OpenAI explicitly, set provider `openai` with a model,
thinking mode, and reasoning effort the OpenAI capability registry supports —
for example:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.6
LLM_THINKING_MODE=enabled
LLM_REASONING_EFFORT=high
```

The same `OPENAI_API_KEY` then serves both chat and embeddings.

Chat callers use project-owned messages and results, not provider SDK types.
Build the configured adapter through the factory rather than constructing a
default adapter directly:

```python
from deep_research.providers import ChatMessage, build_chat_provider

settings = load_config("config.yaml")
chat = build_chat_provider(settings.llm, tracker)

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

Provider selection fails fast and never falls back: an unknown provider or an
unsupported model/thinking/effort combination raises
`ProviderConfigurationError` before any request is made, and a selected
provider that fails stays failed — the other provider is never constructed in
its place.

### Secrets and migration

Strict mode (`load_config("config.yaml", strict=True)`, used by the CLI and
API) requires these secrets before any model is called:

| Selected chat provider | Required for a full research run |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` (embeddings), `TAVILY_API_KEY` |
| OpenAI | `OPENAI_API_KEY`, `TAVILY_API_KEY` |

LangSmith requirements remain conditional on tracing: `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` are required only when `LANGSMITH_TRACING=true`.

**Migration note.** The committed default changed from OpenAI to DeepSeek by
design. Existing OpenAI users must explicitly set `provider: openai`, an
OpenAI model, and a compatible thinking/effort pair after this intentional
default change. Legacy string model overrides remain valid and inherit the
global thinking mode and reasoning effort.

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
from deep_research.providers import build_chat_provider
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
    provider=build_chat_provider(settings.llm, tracker),
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

Neither agent sends a domain type to the chat provider. `SubTopic` and `Finding` declare
`Field(min_length=1)` constraints that render as `minLength`/`minItems`, which
strict structured outputs reject, so the provider is asked for
`ResearchPlanDraft` and `SubTopicFindingsDraft` — constraint-free mirrors —
and the drafts are validated into the domain types locally.

## Source Evaluator And Fact Checker

`SourceEvaluatorAgent` groups `state.raw_findings` by canonical source URL,
computes a corroboration score locally (the fraction of a source's
sub-topics that a *different* domain also covered), looks up any
reputation previous sessions recorded for each URL, and asks the model for
authority, recency, and relevance. `overall_score` is computed here, not by
the model: `0.35*authority + 0.15*recency + 0.30*relevance +
0.20*corroboration`, with a remembered reputation blended into authority at
weight 0.4. Every score is a `UnitScore` in `[0.0, 1.0]`. A source scoring
under `LOW_CONFIDENCE_THRESHOLD` (0.4) is flagged `low_confidence=True`, and
so is any source the model did not score, any source past `max_sources`, and
every source in a run where the scoring call failed — the guarantee is that
*every* source behind a finding gets a record. A failing reputation backend
records one recoverable `source_evaluator_reputation_unavailable` error and
scoring continues directly. This agent runs no ReAct loop and declares no
tools; reputation reaches it through an injected `ReputationSource`, which
`LongTermMemory` satisfies.

`FactCheckerAgent` extracts the major factual claims from the findings in
one structured call (dropping any claim whose source URL never appeared in
`raw_findings`), then runs one bounded ReAct loop **per claim** using
`web_search`, `web_scraper`, `document_reader`, and `query_memory`. A
verdict is only requested from the model once the loop has actually
retrieved content from a domain other than the claim's own publisher.
Verdicts are normalized locally: no independent domain, a loop that died to
a provider failure, an unrecognized verdict string, or a failed verdict call
all become `insufficient_evidence` with confidence 0.0, and any verdict
arriving alongside reported contradictions becomes `contradicted`. There is
no path that invents confidence.

```python
from deep_research.agents import FactCheckerAgent, SourceEvaluatorAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    evaluation = await evaluator.run(state)
    state = merge_research_state(state, evaluation.state_update)

    fact_check = await checker.run(state)
    state = merge_research_state(state, fact_check.state_update)
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `source_evaluator.evaluation.started` | Source Evaluator | `finding_count`, `source_count` |
| `source_evaluator.evaluation.completed` | Source Evaluator | `source_count`, `average_score`, `low_confidence_count`, `reputation_hits`, `reputation_failures` |
| `fact_checker.claims.extracted` | Fact Checker | `claim_count`, `findings_considered`, `sources_considered` |
| `fact_checker.claim.checked` | Fact Checker | `claim`, `verdict`, `confidence`, `contradictions`, `independent_sources`, `tool_calls`, `reason` |
| `fact_checker.fact_check.completed` | Fact Checker | `claim_count`, `verified`, `unverified`, `contradicted`, `insufficient_evidence`, `contradiction_count`, `tool_calls` |

## Synthesizer And Critic

`SynthesizerAgent` turns `verified_claims`, `evaluated_sources`, and
`raw_findings` into the final Markdown report. The report's skeleton is
rendered locally by `agents.report` — all seven sections in
`REPORT_SECTIONS`, in order, whether or not they have content — so a report
always carries an executive summary, findings, verified claims, an
uncertainty section, limitations, numbered citations, and a source
appendix. Citations are numbered locally: evaluated sources first, then any
claim source not already numbered, and a URL the model attached that never
reached the evidence is dropped rather than cited. The model supplies only
prose. The composed Markdown is written through `write_document` and lands
in `state.report`; a failed write records a recoverable
`synthesizer_report_not_written` error and keeps the report in state
anyway. Verified claims at or above `DEFAULT_MEMORY_CONFIDENCE` (0.7) are
kept for future sessions through `save_to_memory`, capped at
`DEFAULT_MAX_MEMORY_FINDINGS` (10). This agent runs no ReAct loop: report
generation is one structured call, and both tool calls are deterministic
consequences of having produced a report.

Limitations are explicit and enumerated, never prose invented by a model:
recorded errors, an exhausted iteration budget, unscored or low-confidence
sources, no verified claim, a contradicted claim, and a failed report call
each add their own `LIMITATION_REASONS` line.

`CriticAgent` reviews that report. It may spot-check a suspected gap with
`web_search` or compare against prior sessions with `query_memory` in one
bounded ReAct loop, then asks for one structured review and computes the
routing decision itself. `route_decision` checks the iteration bound
first — `state.iteration >= state.max_iterations` always ends the run,
whatever the model said — then a missing report, then the score against
`ACCEPTANCE_SCORE` (7), then gaps, then unsupported claims. Every gap the
model lists counts as critical, because the prompt asks it to list a gap
only when closing it would materially change the answer. A provider failure
ends the run with the lowest score rather than buying another cycle; a
missing report buys one while budget remains. `Critique.should_continue` is
a recommendation record — nothing in the agent layer acts on it.

```python
from deep_research.agents import CriticAgent, SynthesizerAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    synthesis = await synthesizer.run(state)
    state = merge_research_state(state, synthesis.state_update)

    review = await critic.run(state)
    state = merge_research_state(state, review.state_update)
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `synthesizer.synthesis.started` | Synthesizer | `claim_count`, `source_count`, `finding_count`, `limitation_count` |
| `synthesizer.synthesis.completed` | Synthesizer | `section_count`, `citation_count`, `source_appendix_count`, `output_path`, `saved_findings`, `report_chars`, `limitations` |
| `critic.critique.started` | Critic | `iteration`, `max_iterations`, `claim_count`, `has_report` |
| `critic.critique.completed` | Critic | `score`, `gap_count`, `unsupported_claim_count`, `recommended_query_count`, `should_continue`, `reason`, `tool_calls` |

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

## FastAPI Interface

The in-process API exposes one research start endpoint and four read
endpoints, all served by a process-local `SessionStore`:

| Method | Path | Response |
| --- | --- | --- |
| `POST` | `/research` | `202` `ResearchSessionResponse` |
| `GET` | `/research/{session_id}/status` | `200` `ResearchSessionResponse` |
| `GET` | `/research/{session_id}/stream` | `200` `text/event-stream` |
| `GET` | `/research/{session_id}/report` | `200` `text/markdown` |
| `GET` | `/research/{session_id}/trace` | `200` `TraceResponse` |

Start a session:

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How mature is quantum error correction?",
    "max_iterations": 3,
    "output_format": "markdown",
    "config_overrides": {
      "output": {"directory": "api-output/"},
      "llm": {"model_overrides": {"critic": "deepseek-v4-pro"}}
    }
  }'
```

The `202` response carries the session snapshot: `session_id`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, and `errors`. Sessions start immediately in a background task;
`status` is `running` until the run reaches `completed`, `max_iterations`,
`incomplete`, or `failed`. Poll `status` or subscribe to the stream —
nothing blocks on research work.

Streams are server-sent events: each frame is one typed `ResearchEvent` as
JSON, preceded by its id and event type:

```text
id: 1
event: graph.node.started
data: {"event_type":"graph.node.started","source":"graph.planner","message":"Node planner started.","timestamp":"...","metadata":{"node":"planner","iteration":0}}

```

A subscriber that connects late replays the session's retained events from
id one, then follows live progress; the stream ends when the session
reaches a terminal state. The report endpoint returns the authoritative
Markdown body with `Content-Type: text/markdown` once the session is
finished. The trace endpoint returns `session_id`, `trace_url`, and
`metadata` carrying the `session_id`, the route template, and the current
`status`.

Errors are structured and safe:

| Status | Meaning |
| --- | --- |
| `422` | Invalid request body or override shape; the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for all four GET routes |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) |
| `500` | Missing or invalid service configuration (`configuration_error`), without file contents, secret values, provider text, or tracebacks |

Sessions, background tasks, and event history are process-local memory:
everything disappears when the process exits. Authentication, multi-tenant
authorization, durable queues, databases, and deployment setup remain out of
scope — running the app is left to the caller.

## Command Line Interface

```bash
python -m deep_research "What are the security implications of quantum computing?"
python -m deep_research "AI in healthcare" --max-iterations 5 --output-format markdown --verbose
python -m deep_research --interactive
python -m deep_research --resume <session_id>
```

| Option | Meaning |
| --- | --- |
| `question` | The research question. Mutually exclusive with `--interactive` and `--resume`. |
| `--interactive` | Prompt once for the question, run once, exit. |
| `--resume SESSION_ID` | Continue a checkpointed session. See the limitation below. |
| `--max-iterations N` | Macro refinement passes the critic may request. Defaults to `graph.max_iterations`. |
| `--output-format` | Report format. Only `markdown` is supported in this build. |
| `--config PATH` | YAML config file. Defaults to `config.yaml`. |
| `--verbose` | Print every progress event, tool call counts, and token totals. |

Every interface calls the same `deep_research.main.run_research()`, which loads
configuration in **strict** mode: the required secrets for the selected chat
provider (see the secret matrix under Chat and Embedding Providers) plus
`LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` when `langsmith.tracing_enabled` is
true must be present in the environment or in a `.env` file next to
`config.yaml`, or the command exits 1 before any model is called.

| Exit code | Meaning |
| --- | --- |
| 0 | The run produced a report (`completed`, `max_iterations`, or `incomplete`) |
| 1 | Configuration failure — bad config path, missing keys, unsupported format, no resumable checkpoint |
| 2 | Usage error |
| 3 | The graph failed (`failed`) |
| 130 | Interrupted with Ctrl-C |

Recoverable research errors never fail the command. They are printed as
`warning:` lines and disclosed inside the report's Limitations section.

**Progress is a post-run log, not a live stream.** `run_research_graph` invokes
the graph to completion and returns one result, so the CLI prints
`ResearchState.events` once the run is over. Live progress arrives with the
API's server-sent-events endpoint.

**`--resume` only works inside one process.** `build_checkpointer` returns
LangGraph's `InMemorySaver`, which does not survive the process that created
it, so resuming from a new command exits 1 with a clear message rather than
pretending a checkpoint exists. A durable saver drops into
`compile_research_graph` without touching a node.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/
```

## Live DeepSeek Smoke Test

The live smoke test is opt-in and excluded from a normal `python -m pytest`
run. Set `DEEPSEEK_API_KEY` in the environment first — the test makes one
bounded structured adapter call with at most one repair, and it does not use
embeddings or Tavily.

PowerShell:

```powershell
$env:RUN_DEEPSEEK_LIVE_TESTS="1"
python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -v
```

POSIX:

```bash
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -v
```

## Phases

- Phase 1: Core package foundation, config, types, providers
- Phase 2: Memory and tools
- Phase 3: Agents and LangGraph orchestration ← complete (all six agents and the graph)
- Phase 4: CLI ← complete; FastAPI API ← complete; Streamlit UI next
- Phase 5: Tests and verification
