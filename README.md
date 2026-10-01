# Deep Research

Multi-agent deep research system using LangGraph, DeepSeek, ChromaDB, and LangSmith.

## Project Status

Phase 4 complete — the package foundation, typed configuration/state, LangSmith observability, selectable DeepSeek/OpenAI chat providers, local and OpenAI embedding providers, core tools, the three-layer memory stack, the shared agent ReAct runtime, CLI, and FastAPI API are implemented.

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

   The default stack is DeepSeek chat with local embeddings, so
   `DEEPSEEK_API_KEY` and `TAVILY_API_KEY` are the only keys a research run
   needs. `OPENAI_API_KEY` is required only when `provider` or
   `embedding_provider` is set to `openai`.

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
model `deepseek-flash`, thinking mode `enabled`, and reasoning effort
`high`. Embedding defaults are also committed under `llm`: `embedding_provider`
`local`, backed by chromadb's default ONNX model at 384 dimensions, with no
API key and no per-call cost. `LLM_PROVIDER`, `LLM_MODEL`, `LLM_THINKING_MODE`,
`LLM_REASONING_EFFORT`, `LLM_EMBEDDING_PROVIDER`, `LLM_EMBEDDING_MODEL`,
`LLM_TIMEOUT`, and `LLM_RETRY_COUNT` override the corresponding YAML values.

Set `DEEPSEEK_API_KEY` for the default DeepSeek chat, in the process
environment or the repository-root `.env`. Embeddings default to
`LocalEmbeddingProvider`, which runs entirely offline and needs no API key.
Set `OPENAI_API_KEY` only when `provider` or `embedding_provider` is set to
`openai`.

A complete DeepSeek configuration with per-agent overrides:

```yaml
llm:
  provider: deepseek
  model: deepseek-flash
  thinking_mode: enabled
  reasoning_effort: high
  model_overrides:
    planner: deepseek-flash
    report_reviewer:
      model: deepseek-flash
      thinking_mode: enabled
      reasoning_effort: max
```

The `planner` override is the legacy string form — model only, inheriting the
global thinking mode and reasoning effort. The `report_reviewer` override is the
structured form with its own model, thinking mode, and reasoning effort. Both
forms remain valid. Keys are the five production agents (``planner``,
``researcher``, ``source_evaluator``, ``evidence_verifier``, ``report_writer``)
and the one service role (``report_reviewer``); an entry for a name outside
that set is accepted by the free-form mapping and simply never read.

To switch chat to OpenAI explicitly, set provider `openai` with a model,
thinking mode, and reasoning effort the OpenAI capability registry supports —
for example:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.6
LLM_THINKING_MODE=enabled
LLM_REASONING_EFFORT=high
```

`OPENAI_API_KEY` is then required for chat. Embeddings still default to the
local provider unless `embedding_provider` is also set to `openai`, in which
case the same key serves both.

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
provider performs one repair request if validation fails. `LocalEmbeddingProvider`
is the default synchronous embedding client memory uses; `OpenAIEmbeddingProvider`
is available when `embedding_provider: openai` is selected — see
`embed_query(...)` and `embed_documents(...)` below.

Provider selection fails fast and never falls back: an unknown provider or an
unsupported model/thinking/effort combination raises
`ProviderConfigurationError` before any request is made, and a selected
provider that fails stays failed — the other provider is never constructed in
its place.

### Secrets and migration

Strict mode (`load_config("config.yaml", strict=True)`, used by the CLI and
API) requires these secrets before any model is called:

| Selected providers | Required for a full research run |
| --- | --- |
| DeepSeek chat + local embeddings (default) | `DEEPSEEK_API_KEY`, `TAVILY_API_KEY` |
| DeepSeek chat + OpenAI embeddings | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + local embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + OpenAI embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |

LangSmith requirements remain conditional on tracing: `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` are required only when `LANGSMITH_TRACING=true`.

**Migration note.** The committed defaults changed from OpenAI to DeepSeek
chat and from OpenAI to local embeddings by design. Existing OpenAI users
must explicitly set `provider: openai` (and, for embeddings,
`embedding_provider: openai`), an OpenAI model, and a compatible
thinking/effort pair after this intentional default change. Legacy string
model overrides remain valid and inherit the global thinking mode and
reasoning effort.

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
from deep_research.providers import LocalEmbeddingProvider
from deep_research.utils.config import load_config
from deep_research.utils.types import merge_research_state

settings = load_config("config.yaml")

pad = ScratchpadMemory.from_config(
    settings.memory.short_term, session_id="session-123", agent_name="researcher"
)
pad.add("Tavily returned 5 results.", kind="observation")

long_term = LongTermMemory.from_config(
    settings.memory.long_term, embeddings=LocalEmbeddingProvider()
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
On an extra pass, sub-topics are ordered by the targets that pass was bought
for first, then by priority ascending, and capped at `max_sub_topics`. Extraction
is per page, not per loop (S6): a structured extraction call starts the moment
a read is admitted, in the background, rather than waiting for the whole loop
to finish, bounded by `agents.extraction_concurrency` (16) in flight at once —
a local resource bound, not the provider's own concurrency limit. A read that
yields nothing produces no `Finding`, so a source is never invented. A tool
failure is an observation and the loop continues; a provider failure stops
the remaining sub-topics with the findings so far kept. A high-priority
sub-topic that produced no findings records a recoverable
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

## Source Evaluator And Evidence Verifier

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

`EvidenceVerifierAgent` turns the researcher's findings into *verified* ones.
Two stages, and both are needed:

- **Figure Match** (code). For every finding, the snippet must appear on the
  page it cites: `figure_match` answers exactly two questions, `read_found` and
  `snippet_on_page`. A finding whose snippet is not on its read is dropped
  (`snippet_not_on_page`), and a finding with no read at all is dropped
  (`read_not_found`). It does *not* judge the figure against the snippet — a
  code pattern reading "is this number in that sentence" was the thing D8
  deleted — so every figure that survives goes to the Context Check.
- **Context Check** (one batched, tool-free AI call). Every figure that
  survived Figure Match goes to the model with the snippet it was copied from,
  the surrounding passage, and the fields the extractor recorded, and the model
  judges whether the snippet or passage actually states it — confirming its
  period, scope, kind (actual or forecast), attribution and the page's own
  organisation, or rejecting it. Batches hold
  `agents.verifier_batch_size` findings (5) and at most
  `agents.verifier_concurrency` batches are in flight (64). Code then applies
  the checks that cannot be a judgement: the corrected wording must be on the
  page, a relayed figure keeps its originator, and a page is credited with its
  own organisation only when code confirms the host is that organisation's own.

A finding's `verification.status` is one of `verified`, `verified_corrected`,
`quoted` or `dropped`, exactly as the code decides it: with **every** figure
dropped the finding is `dropped` with reason `all_figures_dropped`; with
**some** figure dropped, or one whose context was corrected, it is
`verified_corrected`; with every figure kept as written it is `verified`; a
finding with no figure at all — nothing for the Context Check to judge — is
`quoted` (its snippet is on the page, and that is all this status claims).
A figure the Context Check
rejected or that could not be confirmed is dropped with an enumerated reason —
`evidence_not_on_page`, `correction_not_on_page`, `context_rejected`,
`context_unavailable` — and a figure with no reply at all is kept as
"unchecked context" only when its own value appears in the snippet; otherwise
it is dropped as `context_unavailable`. A finding whose batch failed keeps its
Figure Match result, is marked `context_unchecked`, and is cited only as
"unchecked context" (PD-26); nothing is ever promoted by a Context Check it
never got.

Provenance built from the verified fields — organisation, kind, period,
scope, release — is no longer printed beside each reader sentence (spec
`2026-09-25-consumer-report-format.md` §3.1 rule 9): the reader states a
sentence in plain prose, and its full provenance lives in the evidence log's
per-finding record and in the quality JSON's fact rows. The one exception is
a sentence whose Statement Check verdict is `unchecked` (a batch failure)
and that carries a fact row whose unit the figure parser scales (D10): it
still ends with a deterministic
`(figure: {who reported it (and when)})` line, so the only sentence printed
without an independent check keeps a visible provenance line.

```python
from deep_research.agents import EvidenceVerifierAgent, SourceEvaluatorAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    evaluation = await evaluator.run(state)
    state = merge_research_state(state, evaluation.state_update)

    verification = await verifier.run(state)
    state = merge_research_state(state, verification.state_update)
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `source_evaluator.evaluation.started` | Source Evaluator | `finding_count`, `source_count` |
| `source_evaluator.evaluation.completed` | Source Evaluator | `source_count`, `average_score`, `low_confidence_count`, `reputation_hits`, `reputation_failures` |
| `evidence_verifier.verification.completed` | Evidence Verifier | `verified`, `verified_corrected`, `dropped`, `context_unchecked` |

## Report Writer And Report Reviewer

`ReportWriterAgent` turns `state.verified_findings` into the reader report —
the answer-first skeleton of `2026-09-25-consumer-report-format.md` §3: an
evidence line, the bottom line, an optional question-shaped table, one
section per plan part, What we couldn't confirm, and Sources — and its
evidence ledger. One call drafts each plan part's section (§6.3) from only
that part's own findings, every part running in parallel, and a last call
drafts the bottom line (§6.6) from the parts' checked statements once every
part has finished; a redraft re-asks only the parts a defect names (§6.9).
Code builds everything structural the model does not draft: the
question-shaped table (§4), the evidence line, the Sources list, the link to
the evidence log, and the evidence log's own full fact-row table
(`fact_rows()`, one row per fact, revisions folded and noted) — so the
required sections exist whether or not the model produced prose. The model
cites findings **by label only**; a point citing no known label is refused
with a recorded reason, and citations are derived from the cited findings,
so a URL no finding carries cannot be printed (PD-6).

Wording is judged once, by the Statement Check: one batched, tool-free call
over every candidate sentence with its cited findings' verified figures,
organisation and labels (the same `agents.verifier_batch_size` and
`agents.verifier_concurrency` bounds as the Context Check). Each verdict is
`consistent`, `corrected` (the sentence is kept in its corrected form) or
`inconsistent` (the sentence is refused, in full, in the ledger). A batch that
fails keeps its sentences and records the failure, so the sentence is cited
with an unchecked statement rather than silently dropped. Code applies no
wording pattern of its own: the numbers, dates, scopes, names and
forecast-versus-actual wording are the Statement Check's judgement, not a
regex match.

The writer also computes the run's quality snapshot (`compute_report_quality`)
over the composition it composed, which is what the terminal gates and the
extra-pass router read.

Each part's section request carries a point and word budget: "Write at most N
points for this part, about W words in total". A part that owns a required
target has weight 3 and every other part weight 1, and each part gets its
weighted share of the reader length. N is the largest of three numbers: the
required targets the part's findings answer, that share at about 45 words a
point, and 3. The reader length is the question's own word limit, else
`agents.report_target_words` (2000). The budget is an instruction only: code
drops no checked point, and the evidence log keeps every finding. A bound
finding is never context-only. The section rules tell the writer to cite the
stronger of two sources that state the same fact, and to name a weak page's
kind in the words its source line uses. `agents.writer_authority_floor` (0.4)
applies only to the bottom line. There, a checked statement whose findings all
come from sources at or below the floor (or marked low-confidence) is withheld
once any statement rests on a source above it.

`ReportReviewer` makes the single quality judgement. The packet holds every
printed statement with its own label and its cited findings' snippets and
labels, the Verified figures (the fact-row table, unfiltered), the Table
(the question-shaped table's own backing statement and fact-row ids), What
the report could not confirm, each finding's `status:` line, and the gate
results, and the reviewer answers a disposition for every statement it read
— `supported`, `unsupported` or `not_reviewed` — plus a defect for anything
it could not establish. A defect is material when its severity is
`critical` or `major`, and a material defect blocks acceptance. A truncated
reply is asked once more at high effort; a provider failure gives
`provider_failed`, missing dispositions give `incomplete`, and either way
the report publishes as `partial` — an unscored review never fails the run
(PD-13). The *node*, not the model, stamps `missing_required_target_ids`
from the quality snapshot (PD-5).

After a redraft (spec §6.9), the reviewer's next call is a *scoped*
re-review (T5 addendum) rather than a second full one, whenever the
redrafted composition carries at least one part byte-identical to what the
previous review judged: it is fed only the changed statements to judge
fresh, the unchanged ones keep their carried-over dispositions, and it
resolves or carries forward the previous review's own defects. A scoped
reply that cannot be used (a provider failure, or one judging an id the
packet does not carry) falls back to exactly one fresh full review, never a
stale or partial judgement.

```python
from deep_research.agents import ReportReviewer, ReportWriterAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    written = await writer.run(state)
    state = merge_research_state(state, written.state_update)

review = await reviewer.review(build_report_review_input(state, state.composition))
state = merge_research_state(state, {"report_review": review})
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `report_writer.report.written` | Report Writer | `statements`, `citations`, `refused`, `fact_rows`, `not_found` |
| `report_reviewer.review.completed` | Report Reviewer | `review_status`, `mean_score`, `defect_count`, `missing_required_target_ids` |

The memory write is a separate, terminal step: only an accepted report's cited
findings are kept for future sessions through `save_to_memory`, and it is
outside the three-file artifact set.

## LangGraph Orchestration

`deep_research.graph` wires the five agents into one state graph:

```text
START -> planner -> researcher -> source_evaluator -> evidence_verifier
      -> report_writer -> report_reviewer
      -> { extra_pass -> researcher | finalize -> END | END }
```

The LangGraph channel carries the whole `ResearchState` as one JSON-safe
mapping under the key `state`, and every node merges its agent's
`ResearchStateUpdate` with `merge_research_state` — the same append/replace
rules the agents already run under, including "`iteration` moves only through
`advance_research_iteration`". There are no per-field LangGraph reducers, so
there is exactly one implementation of the merge rules.

`extra_pass` is the hop that carries the iteration increment: it sets
`extra_pass_target_ids` to the targets the reviewer's record still names and
advances the iteration, because a conditional edge routes but cannot write
state. The researcher then researches **only those targets**.

Routing is `graph_route`, a pure function of state, and it decides in this
order: a halted run ends; a missing required target with a pass left
(`iteration < max_extra_passes`) buys one targeted extra pass; an unscored
review publishes as `partial`; a report with no gate failure and a passing
review is accepted; and a missing target with no pass left ends as
`max_iterations`. `graph_status` reads the same decision and names the outcome
`completed`, `max_iterations`, `incomplete`, or `failed`. `max_iterations`
therefore means exactly this: **extra passes spent, required targets still
missing, and the report not accepted** — an accepted report with a target
listed under Not found finishes `completed` (PD-23).

Failure is a halt mark in state, not an exception out of `ainvoke`.
`PlanningError`, `AgentConfigurationError`, and `ProviderConfigurationError`
become enumerated `HALTING_ERROR_TYPES` entries; every later node records
`graph.node.skipped` and returns without invoking its agent; the router ends
the run with status `failed` and **everything collected before the failure
survives**. Recoverable agent and tool errors — including the non-recoverable
provider outages agents record for themselves — stay in `state.errors` and
never stop the graph. Any other exception propagates: an unhandled failure is a
defect, not a research outcome.

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
    evidence_verifier=evidence_verifier,
    report_writer=report_writer,
)
graph = compile_research_graph(
    agents, checkpointer=build_checkpointer(enabled=settings.graph.checkpointing_enabled)
)

run = await run_research_graph(
    graph=graph,
    tracker=tracker,
    session_id=session_id,
    question="How much battery storage capacity was added in 2024?",
    max_extra_passes=settings.graph.max_extra_passes,
)
print(run.status, run.state.report, run.trace_url)

# Later, same process, same session id:
resumed = await resume_research_graph(
    graph=graph, tracker=tracker, session_id=session_id
)
```

`run_research_graph` opens the existing `Tracker` session span, so every
agent inside a node produces its own `agent.<name>` span under
`research.session`; the session span's outputs carry the session id, the final
status, the full list of route decisions, the extra-pass iteration, the
per-collection counts and the error count. Graph configuration lives in
`config.yaml` under `graph:` (`max_extra_passes`, `checkpointing_enabled`),
overridable with `GRAPH_MAX_EXTRA_PASSES` and `GRAPH_CHECKPOINTING_ENABLED`.
Checkpointing uses an in-process `InMemorySaver`; a durable saver drops into
`compile_research_graph` without touching a node.

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `graph.session.started` | Runner | `session_id`, `max_extra_passes`, `checkpointing` |
| `graph.node.started` | Every node | `node`, `iteration` |
| `graph.node.completed` | Every node | `node`, `iteration`, `event_count`, `error_count` |
| `graph.node.skipped` | Every node after a halt | `node`, `iteration`, `reason` |
| `graph.route.decided` | Report Reviewer node | `destination`, `reason`, `iteration`, `max_extra_passes`, `missing_required_target_ids` |
| `graph.extra_pass.started` | Extra-pass node | `iteration`, `max_extra_passes`, `targets` |
| `graph.session.completed` | Runner | `status`, `iteration`, `error_count`, `has_report` |

The `graph.route.decided` fragment is always the router's own enumerated reason
(`report_accepted`, `report_not_accepted`, `review_unavailable`,
`extra_pass_requested`, `extra_passes_exhausted`, `halted`), never report prose.

## FastAPI Interface

The in-process API exposes one research start endpoint, a session list
endpoint, five session-scoped read endpoints (`status`, `stream`,
`report`, `evidence`, `trace`), one session-scoped answers endpoint for the
one-time check and one for the reader's notes, all served by a process-local
`SessionStore`:

| Method | Path | Response |
| --- | --- | --- |
| `POST` | `/research` | `202` `ResearchSessionResponse` |
| `GET` | `/research` | `200` `{"sessions": [ResearchSessionResponse, …]}`, newest first (`?limit=`, default 20, 1–200) |
| `GET` | `/research/{session_id}/status` | `200` `ResearchSessionResponse` |
| `GET` | `/research/{session_id}/stream` | `200` `text/event-stream` |
| `GET` | `/research/{session_id}/report` | `200` `text/markdown` |
| `GET` | `/research/{session_id}/evidence` | `200` JSON (the findings, their verification and sources, the not-found targets, the refused sentences); `?format=markdown` → the evidence log as `text/markdown` |
| `GET` | `/research/{session_id}/trace` | `200` `TraceResponse` |
| `POST` | `/research/{session_id}/answers` | `202` `ResearchSessionResponse` (the one-time check's answers, below) |
| `POST` | `/research/{session_id}/notes` | `202` `{"note_id": "n1", "status": "received"}` (a reader note, below) |

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
      "llm": {"model_overrides": {"report_reviewer": "deepseek-v4-pro"}}
    }
  }'
```

The `202` response carries the session snapshot: `session_id`, `query`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, and `errors`. Sessions start immediately in a background task;
`status` is `running` until the run reaches `completed`, `max_iterations`,
`incomplete`, or `failed`. Poll `status` or subscribe to the stream —
nothing blocks on research work.

**The one-time check** (live-briefs spec §4.4). Unless the request sets
`"ask_clarifying_questions": false`, the session first asks the configured model,
with thinking disabled and within `hitl.check_timeout_s` (20 s), whether the
question leaves something material open. With no questions — the usual answer,
and the answer to any failure or timeout — the run starts at once. With up to
three, `status` reads `needs_input` and the stream carries
`session.clarification.requested`: the questions, each with two to four options
and a best guess, and a `deadline_at`. The answers are sent once:

```bash
curl -X POST http://localhost:8000/research/<session_id>/answers \
  -H "Content-Type: application/json" \
  -d '{"answers": [{"question_id": "q1", "choice": "Global"},
                   {"question_id": "q2", "text": "since 2021"}], "skip": false}'
```

A question left out takes its best guess; with no answers at all the run starts on
best guesses after `hitl.answer_wait_s` (60 s). Either way
`session.clarification.answered` records the answers and why (`answered`,
`skipped` or `timed_out`), `status` returns to `running`, and the planner plans
within the answers.

**Reader notes** (live-briefs spec §4.6). While a session is `running`, the reader
may add up to ten notes — something to focus on, leave out or change:

```bash
curl -X POST http://localhost:8000/research/<session_id>/notes \
  -H "Content-Type: application/json" \
  -d '{"text": "More on fire-safety standards, please"}'
```

The note (1–500 characters, one line) is accepted at once as `session.note.received`
(`{note_id, text}`), then read by the configured model, thinking disabled, within
`hitl.note_interpret_timeout_s` (15 s): `session.note.interpreted` carries the run's
reading (`{note_id, restatement, kinds, replaces, fallback}`). A reading that fails or
times out keeps the note as written, as an emphasis, with `fallback: true`. Every step
but evidence verification reads the notes (a later note replaces an earlier one it
contradicts); a note the review finds no evidence for buys one targeted research pass,
and a note the report ignores buys one redraft, neither spending the extra-pass budget
or the writer's own re-run. Notes close once `finalize_report` has started: the run's
published decision to publish (or to end) closes them (live-briefs Phase 3 plan,
ambiguity 5). The status snapshot carries `notes` (each with its `text`, its
`restatement` once read, and its `outcome`: `covered`, `not_found`, `not_addressed`
when the report still does not follow the note after its one redraft, `replaced`, or
`pending` while the run goes on or when no review judged it), `notes_remaining`,
`note_passes`, and `clarification` (the one-time check's questions and the answers the
run started with, or `null`).

A finished session's snapshot also carries the outcome's own readings, added
to the response without changing any existing field: `evidence_path` and
`quality_path` (the other two files of the published set), the
`quality_contract_version`, the `semantic_review_status` and
`semantic_review_score`, the `duration_seconds` the recorded events cover, and
two nested blocks — `coverage` (the required and answered target counts, the
targets no verified finding answers, and the targets the report lists under
Not found) and `evidence_counts` (the distinct
read/work/source/finding counts, and the Evidence Verifier's own readings:
verified, corrected, dropped, unchecked-context and cited findings).
While a session is still running every one of those fields is `null` rather
than `0`: nothing has been measured yet, and a zero would be a claim the run
never made.

Streams are server-sent events: each frame is one typed `ResearchEvent` as
JSON, preceded by its id and event type:

```text
id: 1
event: graph.node.started
data: {"event_type":"graph.node.started","source":"graph.planner","message":"Node planner started.","timestamp":"...","metadata":{"node":"planner","iteration":0},"event_id":"bdb32a61e5d5408491933a6062d2e379"}

```

Every event carries its own `event_id`; the SSE `id:` line is the frame's
position in this subscriber's replay. A subscriber that connects late replays
the session's retained events from id one, then follows live progress; the
stream ends when the session reaches a terminal state. The report endpoint
returns the authoritative Markdown body with `Content-Type: text/markdown`
once the session is finished. The trace endpoint returns `session_id`,
`trace_url`, and `metadata` carrying the `session_id`, the route template, and
the current `status`.

Errors are structured and safe:

| Status | Meaning |
| --- | --- |
| `422` | Invalid request body or override shape, or answers that do not fit the session's questions (an unknown or repeated `question_id`, a `choice` that was not offered); the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace`, `/answers` and `/notes` |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) or without an evidence log (`evidence_unavailable`, on `/evidence`); answers sent to a session that is not waiting for them (`not_waiting_for_input`: never asked, already answered, or past its deadline); a note sent while the session waits for answers, once `finalize_report` has started (from the run's published decision to publish, live-briefs Phase 3 plan ambiguity 5), or after it finished (`notes_closed`), or past its tenth note (`note_limit_reached`) |
| `500` | Missing or invalid service configuration (`configuration_error`), without file contents, secret values, provider text, or tracebacks |

Every response carries `X-Deep-Research-Mode: live` or `replay` (see *Run the app*).

Sessions, background tasks, and event history are process-local memory:
everything disappears when the process exits. Authentication, multi-tenant
authorization, durable queues, databases, and deployment setup remain out of
scope; see *Run the app* below for running the API together with the console.

## Run the app

The console is a Next.js app in `web/` that talks to the API through a same-origin
proxy (`/api/*` → `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8000`). Two
processes:

```bash
# 1. the API — live: the real graph calling the real provider, needs the
#    secrets of the matrix above, and spends their credit on every session …
python -m deep_research.api --mode live --host 127.0.0.1 --port 8000
#    … or replay: the real graph on scripted offline cases — offline and
#    free, no network, no keys, no provider credit spent
python -m deep_research.api --mode replay --replay-case missing-target-triggers-one-extra-pass --replay-delay-ms 150

# 2. the app, in web/ (once: npm install)
npm run dev            # http://localhost:3000, bound to 127.0.0.1 only (the API binds loopback on purpose and the proxy would otherwise undo that); DEEP_RESEARCH_API_URL overrides the API origin
```

Replay mode wraps the whole server in the e2e harness's `offline_credentials()` and
`network_denied()`: every provider call is scripted and every socket connect is refused, so
a session costs nothing and finishes in seconds (`--replay-delay-ms` paces the stream so the
running stage can be watched). A `POST /research` may name the case with the header
`X-Replay-Case: <case id>` (the ids of `e2e_evaluation/replay_matrix.py`); the session
records the case's own question. The one-time check asks nothing in replay mode unless the
`POST /research` also carries `X-Replay-Clarify: on`; then it asks a fixed set of three
questions (Region, Period, For), so the check can be exercised offline. A reader note is
read in replay mode by a scripted interpreter that keeps it as written, as an emphasis;
replay runs the graph at full speed and paces only the stream, so a note added while the
running stage plays arrives after the engine has finished and ends `pending`. In replay mode the topbar
shows a muted `replay mode` chip.
Sessions are held in the API process's memory: the sidebar's list empties when the API
restarts.

Tests: `pytest` for the API; in `web/`, `npm test` (Vitest), `npm run test:e2e` (Playwright
against the API in replay mode; set `DEEP_RESEARCH_PYTHON` to the venv interpreter inside a
worktree), `npm run capture:visual` (full-page captures at 1252 and 390 px into `web/visual/`).

Live-provider smoke tests are opt-in and require separate authorization at
execution time. They are not part of the default repository test runs.

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
| `--max-iterations N` | Extra research passes for missing required targets. Zero is accepted — a run that buys no extra pass. Defaults to `graph.max_extra_passes` (1). |
| `--output-format` | Report format. Only `markdown` is supported in this build. |
| `--config PATH` | YAML config file. Defaults to `config.yaml`. |
| `--verbose` | Print every progress event and the run's totals: per-tool calls with their failures and retries, the proposals the researcher dropped, per-provider attempts and tokens. |
| `--debug-events` | Print the complete bounded event record: every recorded event, named by its enumerated type and source, with no event metadata rendered. |
| `--require-quality` | Exit 4 unless the terminal quality gates accepted the report; without it, a finished partial report exits 0. |

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

The summary's evidence lines are the same typed records the artifacts render
from, so the console, the reader report, the ledger and the quality record
cannot disagree about one run:

```text
Quality: partial (review scored 0.86)
Quality reasons: 1 gate failure (unjudged_sentences)
Required targets: 6/7 answered
Not found: topic-02-target-01
Sources: 10 assessed, 8 cited; reads 14 (network 11, cache reuse 3), works 10, publishers 6, findings 10
Review: scored (fingerprint 8f2c1d…)
Findings: 7 checked (1 with corrected context, 2 unchecked context), 3 dropped; 6 cited
Integrity: 0 duplicate fact rows; 0 uncited statements; 1 unjudged sentences; 2 forecasts without release
Unresolved: 1 missing required target (topic-02-target-01)
```

`Findings` counts the Evidence Verifier's own readings: the total is every
finding it kept (7), one of which it kept with a corrected context and two with
an unchecked context, and three more it dropped; six of the kept findings are
cited by the report. `Integrity` counts the structural invariants the quality
gates judge; `forecasts without release` is counted and printed but is not a
gate (PD-24).

### Lowering parallelism when a provider throttles (spec 7.3)

Six concurrency bounds decide how many provider calls one run makes at once,
and they are the *only* caps. Lowering one is a config or environment change,
with no code edit and no re-run of anything:

| Setting | Default | What it bounds | Environment override |
| --- | --- | --- | --- |
| `agents.sub_topic_concurrency` | 10 | researcher sub-topics in flight | `AGENTS_SUB_TOPIC_CONCURRENCY` |
| `agents.source_scoring_concurrency` | 6 | source-evaluator scoring batches in flight | `AGENTS_SOURCE_SCORING_CONCURRENCY` |
| `agents.verifier_batch_size` | 5 | Context Check and Statement Check items per call | `AGENTS_VERIFIER_BATCH_SIZE` |
| `agents.verifier_concurrency` | 64 | verification calls in flight | `AGENTS_VERIFIER_CONCURRENCY` |
| `agents.extraction_concurrency` | 16 | one sub-topic's per-page extraction calls in flight (S6) | `AGENTS_EXTRACTION_CONCURRENCY` |
| `agents.writer_section_concurrency` | 10 | the parallel writer's section drafts in flight (spec §6.10) | `AGENTS_WRITER_SECTION_CONCURRENCY` |

On a run that meets recurring `429` responses, lower the knob of the agent the
telemetry names at the peak — `agents.verifier_concurrency` first, then
`agents.sub_topic_concurrency` — and lower `agents.verifier_batch_size` only if
the calls themselves are being truncated.

**Progress is a post-run log, not a live stream.** `run_research_graph` invokes
the graph to completion and returns one result, so the CLI prints
`ResearchState.events` once the run is over. Live progress arrives with the
API's server-sent-events endpoint.

**`--resume` only works inside one process.** `build_checkpointer` returns
LangGraph's `InMemorySaver`, which does not survive the process that created
it, so resuming from a new command exits 1 with a clear message rather than
pretending a checkpoint exists. A durable saver drops into
`compile_research_graph` without touching a node.

`--require-quality` is the automation-friendly mode: it preserves the report
and evidence paths and the normal summary, but returns exit code 4 when the
terminal status is not `accepted`. The default exit code remains 0 for a
finished partial report so an operator can inspect its limitations.

### Artifacts

A finished run publishes three files under `output.directory`, and it
advertises all three or none of them: a publication that failed a write prints
no path at all rather than pointing at an earlier refinement pass's file. The
three names are one family, derived from the session id and the pass:

| Artifact | Answers | Name |
| --- | --- | --- |
| Reader report | The bottom line first, an optional question-shaped table, one section per plan part, What we couldn't confirm, and **Sources** listing only the sources its statements cite (publisher, title, date). A figure's own organisation, kind, period, scope and release live in the evidence log and the quality record, not beside the reader sentence. | `report-<session>-<iteration>.md` |
| Evidence ledger | What was checked and what the check found. It carries every finding with its snippet and read locator, every figure kept or dropped with its reason, the verification record, and every drafted sentence the Statement Check refused, in full. | `report-<session>-<iteration>-evidence.md` |
| Quality record | The replay surface: counts, ids, the SHA-256 of each published document, the quality contract version, the findings with their verification, the fact rows, Not found, and the review's status and packet fingerprint. | `report-<session>-<iteration>-quality.json` |

Finding-to-memory writes are a *separate* write, attempted only for an accepted
report, and they are outside that artifact set: a failed memory write is
reported as its own count and leaves the three paths advertised.

The reader report prints an **Evidence as of** line — the evidence date and
the count of sources it cites, read from the newest timestamp
the *recorded evidence* carries — the reads' retrieval times and the findings'
extraction times, never a graph event and never a clock read — so the same
session always renders the same date and a session with no dated evidence says
so instead of printing when it happened to be printed. The ledger states the
session and the pass instead, and neither document prints the run's own clock
date: that date is carried as `generated_on` in the quality record. Sources are
attributed where the report cites them: its **Sources** list carries only the
pages its own statements cite, while the ledger keeps every finding the pass
recorded — cited by a statement or not — with the snippet it was read from, its
read locator and its verification. How many sources were assessed, and how many
of those the report cited, is published as the counts `assessed_sources` and
`cited_assessed_sources` in the run's evidence counts, not as rows in either
document.

### Quality Semantics

The terminal status is printed on every summary and stamped on all three
artifacts. Nine deterministic gates decide the structural half of acceptance,
and each fires on its own defect:

`unresolved_citations`, `uncited_settled_points`, `duplicate_fact_rows`,
`missing_as_of`, `missing_scope`, `unjudged_sentences`,
`unaccounted_required_targets`, `missing_reader_report`,
`missing_evidence_ledger`.

Three of them deserve a sentence. `duplicate_fact_rows` is an *invariant*:
`fact_rows()` merges same-fact rows, and the gate exists to catch a hand-built
or future composition that did not go through it. `unjudged_sentences` is the
same kind of invariant: `compose_written_report` records a Statement Check
verdict for every kept sentence, so a kept sentence with neither a verdict nor
a recorded batch failure is the only way to fail it. `unaccounted_required_targets`
fires only for a required target that no finding answers **and** that Not found
does not list: a missing target the report discloses is accounted for.

A run is **accepted** only when all three of these hold:

- no gate above failed;
- the Report Reviewer scored the report with a mean of 0.80 or above over its
  seven dimensions, with no material defect and complete coverage — every
  statement it read carrying a recorded disposition;
- the router's own route was `report_accepted` (PD-23), which is also what
  decides `completed` when passes are spent and a target is listed under Not
  found.

Anything else is **partial** — including a report no quality pass ever judged,
and one whose review was missing, incomplete, or lost to a provider failure.
Only an accepted report's cited findings are written to memory.

### Upgrading

The Evidence Verifier pipeline replaced the fact checker, the critic and the
synthesizer. Existing invocations keep working — the CLI flag
`--max-iterations` and the API field `max_iterations` kept their names and now
set the extra-pass ceiling — but a custom configuration and an existing
checkpoint need attention:

| Old | Now | What happens if you leave it |
| --- | --- | --- |
| `GRAPH_MAX_ITERATIONS` (env) | `GRAPH_MAX_EXTRA_PASSES` | The old export is **ignored without a warning**: the environment key is no longer read, so the config file's `graph.max_extra_passes` stays in force. |
| `graph.max_iterations` (config) | `graph.max_extra_passes` | **Fails validation**, naming `graph.max_iterations`: the config models forbid unknown keys. |
| `agents.claim_batch_size`, `agents.claim_batches_per_pass`, `agents.critic_review_max_tokens`, `agents.claim_verification_max_tokens` | removed (`agents.verifier_batch_size` and `agents.verifier_concurrency` bound the Context and Statement Checks) | Each **fails validation**, naming the key. |
| `agents.tool_budget_overrides.fact_checker` (and any other removed agent's entry) | remove the entry | **Fails validation**, naming the offending key: the table rejects a name outside the five production agents. |
| `llm.model_overrides.fact_checker`, `.synthesizer`, `.critic`, `.report_judge` | `.report_writer`, `.report_reviewer` | The old entries are **ignored**: `model_overrides` is a free-form mapping, so an unused key is accepted and has no effect. |
| `AGENTS_MAX_ITERATIONS` | unchanged | This one is the researcher's per-turn cap, not the macro budget; it still applies. |

Checkpoints written before the cutover **cannot be resumed**: `ResearchState`
rejects the removed fields, so a checkpoint that carries them fails to load
rather than being read with them silently ignored. Start a fresh session.

## Individual Agent Evaluation

A separate, dedicated CLI at `python -m deep_research.evaluation` runs
per-agent controlled and live experiments against LangSmith, scored by
deterministic gates and a judge model. It is independent of the graph-level
CLI above: it never imports `deep_research.graph`, and evaluates each of the
five agents (`planner`, `researcher`, `source-evaluator`, `evidence-verifier`,
`report-writer`) in isolation against code-backed cases — four controlled and
one live per agent.

The individual-agent controlled tier exercises one agent contract at a time
with the configured provider and LangSmith experiment. Its live tier adds the
real external tools for an explicitly authorized case. These are not
whole-report acceptance results: they do not exercise graph routing, snapshot
replacement, terminal publication, or the reader/evidence pair.

```powershell
# List agents, tiers, cases, repetitions, and dataset names.
python -m deep_research.evaluation list

# Run all four controlled cases for one agent, three times each.
python -m deep_research.evaluation agent researcher

# Run one controlled case, still with three repetitions.
python -m deep_research.evaluation agent researcher --case conflicting-evidence

# Run the selected agent's single live case once.
python -m deep_research.evaluation agent researcher --tier live

# Compare one agent at a different effort without editing the baseline config.
python -m deep_research.evaluation agent researcher --reasoning-effort medium

# Compare one agent with its thinking disabled (the judge still thinks).
# Experiment-only: such a run is never release evidence.
python -m deep_research.evaluation agent researcher --target-thinking-mode disabled

# Run controlled experiments for all five agents.
python -m deep_research.evaluation suite
```

| Exit code | Meaning |
| --- | --- |
| 0 | Automated pass |
| 1 | Completed but failed gates or scores |
| 2 | Invalid usage / unknown agent, tier, or case / invalid local case registry |
| 3 | Configuration / credential / dataset-sync / LangSmith infrastructure failure |
| 130 | Interrupted with Ctrl-C |

**Controlled evaluation makes real DeepSeek and LangSmith calls and costs real
money.** There is no dry-run or mock mode for `agent` or `suite` — every
invocation creates a real LangSmith experiment and calls the real DeepSeek API
with the configured target and judge models. Live-tier runs additionally
exercise real tools (e.g. Tavily web search) for agents that declare them.
Run `list` first, and read the live-verification runbook linked below before
running anything that spends money.

### Environment Variables

Runtime secrets stay environment-only, exactly like the graph CLI above:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | Always | Target and judge model calls. |
| `LANGSMITH_API_KEY` | Always | Experiment creation, dataset sync, tracing. |
| `LANGSMITH_PROJECT` | Always | The LangSmith project experiments are recorded under. |
| `LANGSMITH_ENDPOINT` | Optional | Override the LangSmith API region, e.g. the EU endpoint `https://eu.api.smith.langchain.com`. |
| `LANGSMITH_WORKSPACE_ID` | Optional | Disambiguate a workspace when the API key has access to more than one. |
| `TAVILY_API_KEY` | Only for live cases whose agent declares `web_search` | Real web search during live-tier evaluation. |

`OPENAI_API_KEY` is not required: the evaluation baseline runs chat on
DeepSeek and embeddings locally. It is needed only if `config.yaml` selects
an OpenAI chat provider or an OpenAI embedding model.

### Artifacts

Every `agent` invocation writes a durable, round-trippable JSON artifact in
addition to the LangSmith experiment:

- `output/evaluations/<agent>/<experiment-name>/results.json`
- `output/evaluations/suite/<suite-id>/summary.json` for `suite` runs

`--output-directory` overrides the `output/evaluations/` root.

## Whole-Report Quality Evaluation

The graph-level campaign is a separate CLI and package:
`python -m deep_research.e2e_evaluation`. Individual-agent evaluation checks
one agent's contract in isolation; whole-report evaluation checks the
five-agent handoff, the verified-finding snapshot, citations and the evidence
log's provenance, what the report could not confirm, the Statement Check's
verdicts, the targeted extra pass, terminal publication, memory timing, and
each row's declared result against the run that produced it. The package
ships one controlled
harness, and its CLI exposes exactly two commands: `list` and `suite`.

### Real-agent harness

`suite` runs the real agents: the 35 rows of the versioned replay manifest
(manifest v8, case semantics v2), each started through the production
`deep_research.cli` entrypoint with the five production agent classes, the real
graph, reviewer, renderer, and publisher, and only the external boundaries
scripted. The socket layer is denied for every repetition and the attempts it
records are carried into the result, so a suite that reached the network is not
accepted however clean every row looked. A row passes when all three of its
repetitions met its declared expected product result *and* produced one
identical outcome — exit code, terminal quality, answered targets, and published
report. The repetitions exist to check that order, identity resolution and
state isolation are deterministic, so a row whose repetitions disagree fails as
`NON-deterministic` rather than passing with a note beside it, and a suite
holding such a row is not accepted. A repetition's dates come from the
harness's own declared instant rather than from the wall clock, so the `As of`
stamp in the reader report is the same on every run and a suite that straddles
midnight UTC cannot fail a row on the clock instead of on the agents. This
harness computes no judge score at all.

The rows cover the pipeline's own failure and recovery paths, including
`broad-constraints`, `comparative-conflict`, `same-work-mirror`,
`report-relay-labelled-as-relay`, `forecast-versus-actual-kept-apart`,
`missing-target-triggers-one-extra-pass`, `extra-pass-recovers-missing-target`,
`extra-pass-finds-nothing`, `blocked-html-pdf-fallback`, `unsupported-mechanism`,
`review-unavailable`, `non-constraint-answer`, `empty-but-clean`,
`memory-is-not-read`, `validated-cache-reuse`,
`decision-context-late-candidate`, `figure-not-on-page-dropped`,
`evidence-words-not-on-page-rejected`, `report-scope-corrected-to-all-segments`,
`revision-noted` and `statement-check-failure-keeps-sentences`. Each row states
its own sources, its Context Check and Statement Check overrides, its declared
result (accepted or partial, and the exit code) and its invariants over
production state — for example `report-scope-corrected-to-all-segments`
requires the kept figure's scope to be "all segments", the finding to be
`verified_corrected`, and no reader sentence to say "grid-scale".

```
Mode: real-agent (21 cases from replay manifest v3, case semantics v2)
Agents: production classes through the real graph
```

The live tier is *declared only*: `suite --tier live` parses and is then refused
before anything runs (`run_replay_suite` raises "live tier is declared only and
has no runner; running it requires a separately authorized canary"), and no live
runner exists in this package. Live provider, search, judge, and LangSmith calls
belong to a separately authorized canary.

Whole-report gates are independent of the individual-agent gates: the quality
pass's own hard failures (`unresolved_citations`, `uncited_settled_points`,
`duplicate_fact_rows`, `missing_as_of`, `missing_scope`, `unjudged_sentences`,
`unaccounted_required_targets`, `missing_reader_report`,
`missing_evidence_ledger`) reach `suite` as `hard:<name>` gap kinds, and a row
fails on any kind its case does not allow. No row's allow-list tolerates a hard
failure, so an integrity failure is never a note beside a passing row. The
production CLI `--require-quality` flag is a graph-run exit policy (exit 4 for a
non-accepted terminal quality status); it does not replace these gates.

```powershell
# List the 21 row ids, with the manifest and case-semantics versions.
python -m deep_research.e2e_evaluation list

# Run the campaign: 21 rows, three repetitions each, network-zero.
# The controlled tier runs exactly three repetitions; any other count is refused.
python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
```

Each suite writes its own JSON artifact under `output/evaluations/e2e/`:
`replay-suite.json`. It carries the campaign identity, the tier and mode, the
manifest and case-semantics versions, the graph revision, the suite's `accepted`
and `rows_accepted` verdicts, and, for every row, every repetition's exit code,
terminal quality, expectation failures, answered targets, recorded network
attempts, published report fingerprint and the seven deterministic counts read
from that run's own final state — `verified_findings`, `dropped_findings`,
`context_unchecked_findings`, `duplicate_fact_rows`, `unjudged_sentences`,
`missing_required_targets` and `extra_passes` (the extra passes the graph
spent). The counts the quality snapshot holds are `null` only where the run
stamped no snapshot, because a pass that composed no report judged nothing;
`extra_passes` is `state.iteration` and is always recorded. Each repetition's
own published documents sit under
`output/evaluations/e2e/replay/<case-id>/repetition-<n>/`. Controlled stdout
prints the mode header, one line per row, the suite verdict, the artifact path
and the network line — never report bodies or raw tool/model payloads.

### Manual Live Verification

Because this CLI spends real money and makes real network calls, it is never
exercised by the automated test suite beyond scope-guarding unit tests. See
[`docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md`](docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md)
for the fixed manual verification order, the exact commands, the confirmation
checklists, and the open questions a human must resolve in the LangSmith UI
before trusting this harness's output.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/ tests/
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
- Phase 3: Agents and LangGraph orchestration ← complete (the five agents and the graph)
- Phase 4: CLI ← complete; FastAPI API ← complete; Streamlit UI ← complete
- Phase 5: Tests and verification
