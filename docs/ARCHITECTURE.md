# Architecture

Backend internals of Deep Research. For setup, configuration and the CLI, API
and evaluation commands, see the [README](../README.md); for the console's
design, see [`docs/design/`](design/README.md).

A research run turns one question into three published files (a reader report,
an evidence ledger and a quality record) by running six stages as a LangGraph
state graph. Every stage reads and writes one typed `ResearchState`; no stage
talks to another directly. The CLI and API both call `run_research()`
(`main.py`), which builds a runtime (`runtime/`), drives the compiled graph
(`graph/`) and returns a `ResearchOutcome` (`runtime/outcome.py`).

## Package map

| Package | Responsibility |
| --- | --- |
| `utils/` | `ConfigSettings` and `load_config`, the typed research contracts (`ResearchState`, `Finding`, `ReportComposition`, `ResearchEvent`, `ResearchError` ...), text and concurrency helpers. |
| `providers/` | Chat providers (DeepSeek, OpenAI), local and OpenAI embeddings, the model capability registry, the retry policy, structured-output validation. |
| `observability/` | The `Tracker` (spans, local metrics, optional LangSmith mirror), the per-run telemetry collector, stage capture for experiments. |
| `tools/` | The six tools an agent can call. |
| `memory/` | Scratchpad, long-term (ChromaDB) and procedural (JSON) memory. |
| `agents/` | `BaseAgent`, the ReAct loop, the five agents, the Report Reviewer, and the evidence, report and quality logic they share. |
| `graph/` | Graph state and routing (`state.py`), nodes (`nodes.py`), LangGraph wiring and the session runner (`orchestrator.py`), event constructors, live event delivery. |
| `runtime/` | Assembly of providers, tools, memory and agents (`assembly.py`), startup memory recall, the `ResearchOutcome` front ends read. |
| `api/` | The FastAPI app, the process-local `SessionStore`, the one-time check, reader notes, stop, SSE, the replay server. |
| `evaluation/`, `e2e_evaluation/`, `experiments/`, `request_budget.py` | Per-agent evaluation, the whole-report replay matrix, hand-run latency experiments, per-provider transport-attempt ceilings. |

`run_research()` is the one entry point the CLI and the API share. It validates
the inputs, loads configuration in strict mode (a missing secret or an invalid
config is a `ResearchConfigurationError` with an enumerated hint), builds the
runtime, recalls procedural memory for the planner, and drives the graph inside
one session span. Once the graph runs, failure is a status, not an exception;
any other exception is a defect and propagates.

## Core tools

`BaseTool` (`tools/base.py`) wraps every execution in a tracker `tool_span` and
returns a `ToolResult` (`success`, `data`, `error`, `latency_ms`, `metadata`).
A tool never raises an operational error to its caller: a failure becomes a
`ToolResult` with a `ToolError` (`type`, `message`, `recoverable`, `details`).
An exception the framework does not own is reported by its type with a static
message, never `str(error)`, because the text reaches public state. The one
exception is `RequestAttemptLimitError`, a spent request ceiling, which is
re-raised so the run stops instead of spending past a declared limit.

| Tool | Purpose |
| --- | --- |
| `web_search` | Tavily search (`tavily.search_depth`, `tavily.max_results`). |
| `web_scraper` | Fetch a web page (bounded attempts and retries). |
| `document_reader` | Extract the text of a PDF, spreadsheet or data file at a URL, in chunks. |
| `query_memory` | Semantic recall from long-term memory. |
| `save_to_memory` | Write a finding to long-term memory. |
| `write_document` | Write a file under `output.directory`. |

Clients and memory backends are injectable, so tool tests need no live service;
`runtime/memory_bridge.py` adapts typed long-term memory onto the tools' protocol.

## Memory

Three layers, each usable alone:

- **`ScratchpadMemory`**: synchronous, bounded (`memory.short_term.max_turns`),
  per agent and session, never persisted.
- **`LongTermMemory`**: async ChromaDB-backed semantic recall over verified
  findings, source reputations, report summaries and failed strategies. It
  persists under `<memory.long_term.persist_directory>/chroma/` and embeds with
  the configured embedding provider.
- **`ProceduralMemory`**: an async JSON strategy registry at
  `memory.procedural.strategies_path` (default `memory/strategies.json`). A
  corrupt file is renamed to `<file>.corrupt-<timestamp>.bak` and the registry
  restarts empty.

Memory failures are recoverable: a long-term write returns `False`, a query
returns `[]`, and each failure appends a `ResearchError` that `drain_errors()`
hands back for merging into `ResearchState.errors`. Only startup problems raise
`MemoryInitializationError`. At session start `runtime/recall.py` builds the
planner's `MemorySnapshot` from procedural guidance only, so remembered prose
never becomes a settled premise of the plan: the Researcher discovers prior leads
under its own tool budget and must re-admit anything it uses as evidence of this
run. Cited findings are written to long-term memory once, at publication, and
only for an accepted report.

## Agent runtime

`BaseAgent` (`agents/base.py`) runs a bounded ReAct loop (`agents/react.py`): build
the task, ask the model for a decision, execute the chosen tool, observe, write
the scratchpad, stop or continue. It has no LangGraph dependency; an agent is a
plain async object, `agent.run(state)` returns a `ResearchStateUpdate`, and the
caller merges it with `merge_research_state`. Decisions arrive as native
tool-call turns (`complete_react`); a malformed envelope fails closed and is not
repaired.

A concrete agent supplies `name`, `output_schema`, `system_prompt(task)`,
`build_task(state)` and `finalize(task, run)`, and may override
`allowed_tools` (default none), `is_sufficient(steps)` and `state_update`.
`finalize` is always called, whatever the stop reason, so a well-behaved agent
checks `run.succeeded` itself.

The loop stops with one of `finished`, `sufficient`, `max_iterations`,
`tool_budget_exhausted` or `provider_error`. It is bounded by
`agents.max_iterations` (model turns) and the per-agent tool budget
(`agents.tool_budget`, overridden per agent by `agents.tool_budget_overrides`).
A tool failure becomes an observation the model can react to plus a recoverable
`ResearchError`. A provider failure stops the loop with `provider_error` and
records a non-recoverable error; the provider has already applied its retry
policy, so the agent adds none.

Structured calls use `complete_structured(messages, Schema)`: the provider
validates the reply and makes exactly one repair request on failure, raising
`StructuredOutputError` if it still fails. Agents never send a domain type to
the provider. Domain types such as `SubTopic` declare `min_length` constraints
that strict structured outputs reject, so the provider is asked for a
constraint-free draft (`ResearchPlanDraft`, `SubTopicFindingsDraft`, ...) that
the agent validates into the domain type locally. A truncated structured reply
is re-asked once at `high` effort (`OUTPUT_LIMIT_RETRY_EFFORT`).

Each agent resolves its model, thinking mode, effort, timeout and retry count
from `llm.model_overrides[<agent>]`, else the global `llm` values; provider
selection fails fast and never falls back to the other provider.

## Agents

The Planner, Researcher, Source Evaluator, Evidence Verifier and Report Writer
are agents; the Report Reviewer is a service role. Only the Planner, Researcher
and Report Writer declare tools; the writer's `write_document` and
`save_to_memory` are its publishing methods, called by `finalize_report`.

### Planner

Turns `original_question` into 1 to 10 prioritized `SubTopic`s (`MIN_SUB_TOPICS`,
`MAX_SUB_TOPICS`), each carrying the evidence targets it must answer, and freezes
the `AnswerContract` (answer kind, scope, as-of date, optional requested word
limit) before any evidence is gathered. A small ReAct loop scopes the question
(`query_memory` when startup recall found no guidance, otherwise `web_search` for
an unfamiliar term; the shipped tool budget is 1). The plan itself then comes
from a structured call followed by a tool-free plan review. An unusable plan
gets exactly one repair with the problems listed back to the model, and a plan
that is still unusable raises `PlanningError`, which halts the run. There is no
partial plan. Reader answers from the one-time check and reader notes are read
into the plan. Priority is "lower is more important".

### Researcher

Runs one bounded ReAct loop per selected sub-topic (at most
`agents.max_sub_topics`, 10; `agents.sub_topic_concurrency` in flight), each with
its own tool budget (40 shipped) over `web_search`, `web_scraper`,
`document_reader` and `query_memory` (no memory-write tool). An extra or note
pass confines the researcher to the targets that pass was bought for.

Pages are not read whole into a prompt. A read is admitted as exact,
paragraph-bounded passages (`agents.read_admission_chars`), selected and ranked
by relevance into an extraction packet (`agents.evidence_packet_chars`), and
every ReAct decision sees only a small context packet
(`agents.decision_context_chars`). Extraction is per page: a structured call
starts as soon as a read is admitted, bounded by
`agents.extraction_concurrency`, and passages a required target is still owed
get a bounded re-ask. A read that yields nothing produces no `Finding`, so a
source is never invented. Each finding carries the snippet it was extracted from,
which the Evidence Verifier later checks against the page. The per-sub-topic
caps (`MAX_FINDINGS_PER_SUB_TOPIC` 120, `MAX_UNIQUE_SOURCES_PER_SUB_TOPIC` 48)
are runaway guards; up to `MAX_EXEMPT_PER_REQUIRED_TARGET` (2) findings per
required target sit outside them, so a cap cannot delete the answer a run was
sent to get.

A provider failure stops the remaining sub-topics with the findings so far kept.
A high-priority sub-topic (`HIGH_PRIORITY_THRESHOLD` 2) that produced no findings
records a recoverable `researcher_sub_topic_without_findings`; one never
attempted records `researcher_sub_topic_skipped` with `reason` `cap` or
`provider_failure_stopped_processing`. The two are mutually exclusive for a
sub-topic.

### Source Evaluator

Runs no ReAct loop and declares no tools. It groups findings by canonical source
URL, resolves each read's publisher, work and dating, and asks the model to
score authority, recency and relevance in batches
(`agents.source_evaluator.batch_size`, `max_total_sources`;
`agents.source_scoring_concurrency` in flight). Code stamps the rest:
`overall_score` is `0.45*authority + 0.15*recency + 0.40*relevance`; a reputation
remembered from earlier sessions (an injected `ReputationSource`, which
`LongTermMemory` satisfies) is blended into authority at weight 0.4; a score under
`LOW_CONFIDENCE_THRESHOLD` (0.4) sets `low_confidence`. Every source behind a
finding gets a record: one the model did not score, one past the cap, or one in a
failed batch carries no scores and an explicit `evaluation_status`
(`unscored_missing`, `unscored_cap`, `unscored_provider`). A failing reputation
backend records one recoverable `source_evaluator_reputation_unavailable` error.

### Evidence Verifier

Turns raw findings into verified ones in two stages, both required.

- **Figure Match** (code). The snippet must appear on the page the finding cites
  (`snippet_on_page`, `read_found`); otherwise the finding is dropped
  (`snippet_not_on_page`, or `read_not_found` when it has no read at all). Code
  judges no individual figure here.
- **Context Check** (model). Every figure of a surviving finding goes, with its
  snippet, surrounding passage and recorded fields, to batched tool-free calls
  (`agents.verifier_batch_size` items per call, `agents.verifier_concurrency`
  calls in flight). The model confirms or rejects the figure's period, scope,
  kind (actual or forecast), attribution and organisation. Code then enforces
  what cannot be a judgement: a correction must be supported by the quoted
  evidence words, the evidence words must be on the page, a relayed figure keeps
  its originator, and a page is credited with its own organisation only when code
  confirms the host is that organisation's.

`verification.status` is `verified` (every figure kept as written),
`verified_corrected` (a figure was corrected or some figure dropped), `quoted` (no
figure; the snippet is on the page and nothing more is claimed) or `dropped`
(`all_figures_dropped`, `snippet_not_on_page` or `read_not_found`). A figure is
dropped with `evidence_not_on_page`, `correction_not_on_page`, `context_rejected`
or `context_unavailable`. If a batch fails, the finding keeps its Figure Match
result, is marked `context_unchecked`, and keeps a figure only when its own value
appears in the snippet; nothing is promoted by a check it never got. The same
module hosts `check_statements`, the Statement Check the writer uses.

### Report Writer

Drafts the reader report from verified findings: one structured call per plan
part, all parts in parallel (`agents.writer_section_concurrency`), each seeing
only its own part's findings, then a last call for the bottom line from the
parts' checked statements. The model cites findings by label only; a point citing
no known label is refused with a recorded reason, and citations are derived from
the cited findings, so a URL no finding carries cannot be printed. Code builds
every structural element: the evidence line, the optional question-shaped table,
Sources, the link to the evidence log, and the evidence log's fact-row table.

Wording is judged once, by the **Statement Check**: batched tool-free calls over
every drafted sentence with its cited findings' verified figures. Each verdict is
`consistent`, `corrected` (kept in its corrected form) or `inconsistent`
(refused, in full, in the ledger); a failed batch keeps its sentences and records
the failure. Code applies no wording pattern of its own.

Each part's request carries a point and word budget: a part owning a required
target has weight 3, others 1, and each gets its weighted share of the reader
length (the question's own word limit, else `agents.report_target_words`, 2000);
points per part are at most the larger of the required targets answered, the
share at about 45 words per point, and 3. The budget is an instruction, not a
code filter. `agents.writer_authority_floor` (0.4) applies only to the bottom
line. A redraft re-asks only the parts a defect names and carries the rest over.
The writer computes the quality snapshot (`compute_report_quality`) the router and
the terminal gates read.

### Report Reviewer

A service role (`REPORT_REVIEWER_ROLE`, configured under
`llm.model_overrides.report_reviewer`), not an agent: one call judges the whole
report against the question and the findings behind each statement. The packet
holds the reader report verbatim, every statement with its label and cited
findings' snippets, the key facts, the obligations Not found could not answer and
the gate results, and carries no threshold. The reply scores seven dimensions (`completeness`,
`prioritization`, `evidence_quality`, `attribution`, `uncertainty`, `readability`,
`actionability`), gives a disposition for every statement it read (`supported`,
`unsupported`, `not_reviewed`) and lists typed defects with a severity of
`critical`, `major` or `minor`; `critical` and `major` are material and block
acceptance. Acceptance is a mean of at least 0.80 (`SEMANTIC_REVIEW_MEAN`) with
no material defect. A truncated reply is asked once more at `high` effort; a
provider failure gives `provider_failed` and missing dispositions give
`incomplete`, and either way the report publishes as partial: an unscored review
never fails the run. After a redraft the next review is scoped to the changed
statements when at least one part is byte-identical to what the previous review
judged, and an unusable scoped reply falls back to one fresh full review. Code,
not the model, stamps `missing_required_target_ids` from the quality snapshot.

## Orchestration

`graph/orchestrator.py` is the only module that imports LangGraph. Everything the
graph decides lives in `graph/state.py` and `graph/nodes.py`.

```text
START -> planner -> researcher -> source_evaluator -> evidence_verifier
      -> report_writer -> report_reviewer
      -> { note_pass -> researcher | extra_pass -> researcher
         | writer_redraft -> report_writer | finalize_report -> END | END }
```

The channel carries the whole `ResearchState` as one JSON-safe mapping under
`state`; every node merges its update with `merge_research_state`, so there is one
implementation of the merge rules and no per-field reducers.

- **`extra_pass`**, **`note_pass`** and **`writer_redraft`** are hops that exist
  because a conditional edge routes but cannot write state. `extra_pass`
  increments `iteration` and sets `extra_pass_target_ids`, replacing the previous
  list; the researcher then works only on those targets. `note_pass` does the same
  for reader notes without touching `iteration`. `writer_redraft` hands the
  review's material defects to the writer, with no research and no
  re-verification.
- **`finalize_report`** is the one node with no model call and the run's only
  writer. It stamps the terminal quality status, writes the three files from one
  frozen composition, advertises no path unless the whole set was written, saves
  cited findings to memory only when the status is `accepted`, and emits
  `graph.report.published`. A failed write is a recoverable error; the Markdown
  stays authoritative in state.

### Routing

`graph_route(state)` is a pure function that returns `(destination, reason)`; the
conditional edge, the route event, the final status and the quality status all read
it. In order:

1. A halted run ends (`end`, `halted`), publishing nothing.
2. A reader note owed its targeted pass buys a `note_pass`.
3. A reader note with a steering kind the report ignored buys one redraft.
4. Once a review exists, a required target with no verified finding (or one the
   review's own coverage defect names) buys an `extra_pass` while
   `iteration < max_extra_passes`.
5. A review that is not `scored` publishes as partial (`review_unavailable`).
6. A material defect buys one writer redraft (`MAX_WRITER_REDRAFTS = 1`).
7. No gate failure and a passing review publish as `report_accepted`.
8. Otherwise the run publishes as `extra_passes_exhausted` (targets still missing)
   or `report_not_accepted`.

`graph_status` maps the reason to `completed`, `max_iterations`, `incomplete` or
`failed`. `max_iterations` means exactly: extra passes spent, required targets
still missing, report not accepted. An accepted report that lists a target under
Not found is `completed`. LangGraph's recursion limit is derived from the graph's
real shape (`graph_recursion_limit`), not a separate setting.

### Bounds

| Bound | Where | Shipped value |
| --- | --- | --- |
| Extra research passes | `graph.max_extra_passes` | 1 |
| Writer redrafts | `MAX_WRITER_REDRAFTS` | 1 |
| Reader notes per run | `MAX_NOTES_PER_RUN` | 10 (each buys one pass and one redraft) |
| ReAct model turns | `agents.max_iterations` | 15 |
| Tool calls per loop | `agents.tool_budget` and overrides | 10; planner 1, researcher 40, others 0 |
| Transport attempts | `request_budget.*` | undeclared by default; request-scoped |

### Halting and errors

Failure is a halt mark in state, not an exception out of the graph. These error
types in `HALTING_ERROR_TYPES` end the run: `graph_planning_failed`,
`graph_agent_configuration_error`, `graph_provider_configuration_error`,
`graph_invalid_agent_state`, `graph_invalid_route` and
`graph_request_attempt_limit_exceeded`. After a halt every later node records
`graph.node.skipped` and returns without calling its agent, the router ends the
run as `failed`, and everything collected before the failure survives.
Recoverable agent and tool errors, including provider failures an agent records
for itself, stay in `state.errors` and never stop the graph. An unexpected
exception propagates: an unhandled failure is a defect, not a research outcome.

`ResearchError` carries `error_type`, `source`, `message`, `recoverable`,
`timestamp` and `details`; types are enumerated and messages are
project-authored, never provider text or a traceback.

### Session runner and checkpointing

`run_research_graph` opens the tracker's `research.session` span, so every agent
produces an `agent.<name>` span under it, and records route decisions and counts
on the span's outputs. `build_checkpointer` returns LangGraph's `InMemorySaver`
when `graph.checkpointing_enabled` is set, so a resume works only inside one
process; a durable saver drops into `compile_research_graph` without touching a
node. `resume_research_graph` raises `GraphResumeError` without a checkpoint.

## Events and streaming

Every progress record is a `ResearchEvent` (`event_type`, `source`, `message`,
`timestamp`, `metadata`, `event_id`) appended to `state.events`. The graph hands
events to an `event_handler` two ways so each is delivered once: agents and nodes
publish *live* through a sink bound for the run (`graph/live.py`), and each
`stream_mode="values"` snapshot publishes the events its superstep appended,
skipping ids already published live.

| Source | Event types |
| --- | --- |
| Graph | `graph.session.started`, `graph.node.started` / `completed` / `skipped`, `graph.route.decided`, `graph.extra_pass.started`, `graph.note_pass.started`, `graph.note_redraft.requested`, `graph.report.redraft_requested`, `graph.quality.assessed`, `graph.report.reviewed`, `graph.report.published`, `graph.session.completed` |
| Agents | `planner.planning.started` / `.completed`, `planner.memory.recalled`, `researcher.sub_topic.started` / `.completed`, `researcher.tool_call`, `researcher.research.completed`, `source_evaluator.evaluation.started` / `.completed`, `evidence_verifier.verification.completed`, `report_writer.report.written` |
| API session layer | `session.clarification.requested`, `session.clarification.answered`, `session.note.received`, `session.note.interpreted`, `session.stopped` |

`planner.progress`, `source_evaluator.progress`, `evidence_verifier.progress` and
`report_writer.progress` are live-only: each is a cumulative snapshot for the
running stage's brief, published through the sink and never stored in state.
`graph.route.decided` always carries the router's own enumerated reason, never
report prose.

The API stores every published event per session and serves it as server-sent
events; a late subscriber replays from id 1 and then follows live (see the README).

## Quality and artifacts

`compute_report_quality` runs nine deterministic gates over the composition:
`unresolved_citations`, `uncited_settled_points`, `duplicate_fact_rows`,
`missing_as_of`, `missing_scope`, `unjudged_sentences`,
`unaccounted_required_targets`, `missing_reader_report` and
`missing_evidence_ledger`. `duplicate_fact_rows` and `unjudged_sentences` are
invariants a correct pipeline cannot trip (`fact_rows()` merges same-fact rows,
and every kept sentence gets a Statement Check verdict or a recorded batch
failure). `unaccounted_required_targets` fires only for a required target that no
finding answers and that Not found does not list.

The terminal status is `accepted` only when `graph_route` returns
`report_accepted`; everything else is `partial`. The three files are
`report-<session>-<iteration>.md`, `...-evidence.md` and `...-quality.json`. The
reader report opens with an *Evidence as of* line whose date is the newest
timestamp the recorded evidence carries (`report_as_of`), never a clock read; the
run's own date is recorded separately as `generated_on`. The quality record also
carries the SHA-256 of both Markdown documents, the quality contract version
(`QUALITY_CONTRACT_VERSION`) and the run telemetry.

## Observability and request budget

The `Tracker` records every span locally and mirrors it to LangSmith when
`langsmith.tracing_enabled` is on. Span kinds are session, agent, ReAct
iteration, LLM, tool and memory, and completed spans append typed metrics
(`AgentMetric`, `ToolMetric`, `TokenUsageMetric`, `MemoryMetric`). A LangSmith
transport failure becomes a recoverable error and work continues locally. A
per-run `RunTelemetryCollector` reports rate-limit (429) errors and how many a
retry recovered, the peak provider calls in flight, each stage's calls and
seconds, and each operation's output tokens against its cap.

`RequestBudget` (`request_budget.py`) reserves one unit per transport attempt
before the call is made, shared by the provider transports and the search tool. A
declared ceiling `C` permits `floor(C * stop_fraction)` attempts and refuses the
next before any network I/O; a refused attempt never increments the counter.
Ceilings are request-scoped (`request_budget` in `config_overrides`, or the CLI's
`--request-*` flags) with no `config.yaml` key or environment variable.

## API session layer

`SessionStore` (`api/sessions.py`) starts one background task per session,
records its events and exposes safe snapshots; routes stay thin. Before the graph
starts, the one-time check (`api/clarify.py`) may hold a session in `needs_input`
while the reader answers. Reader notes go through a per-session `NoteBoard`
(`runtime/notes.py`) bound to the run: the API writes it, and nodes and agents
read it at call time, so a note reaches a node already in flight. Stop
(`api/stop.py`) cancels the run where it stands and publishes `session.stopped`;
`finalize_report` never runs, so nothing is published. Sessions, tasks and events
are process-local and disappear when the process exits.

## Replay harness

`e2e_evaluation/` is the offline release proof. Each repetition enters through the
production `deep_research.cli.main` (argument parsing, progress stream, summary,
exit codes) and runs the production agents, the real compiled graph, reviewer,
renderer and publisher against locally authored fixtures. Only the external
boundaries are substituted: the chat provider, the search client, the HTTP client
and long-term memory's vector store. A `ReplayScenario` declares the pages the
fixtures serve (a page refuses an excerpt that is not literally in its text), the
plan the planner is scripted to write, and the expected product result; every
scripted completion asserts the packet it was asked, so a reply can never populate
state the run did not produce.

The versioned manifest (`REPLAY_CASE_MANIFEST`, version 8, case semantics 2) holds
35 rows. The suite runs each row three times under `offline_credentials()` and
`network_denied()`, requires one identical outcome per row (exit code, terminal
quality, answered targets, published report) that matches its declared result, and
fails a suite whose runs attempted a connection. The API's replay mode
(`api/replay.py`) serves the same harness behind the HTTP interface with the same
two guards held for the whole process.
