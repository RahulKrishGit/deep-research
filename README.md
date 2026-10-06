# Deep Research

Deep Research turns a research question into a cited Markdown report. A
[LangGraph](https://langchain-ai.github.io/langgraph/) pipeline of six agents does the work: a **Planner** splits
the question into sub-topics, a **Researcher** searches and reads the web, a
**Source Evaluator** scores every source, an **Evidence Verifier** checks each
finding against the page it came from, a **Report Writer** drafts a report that
cites only verified findings, and a **Report Reviewer** judges the draft and
decides whether to publish it, buy one more research pass for required targets
nobody answered, or ask for a redraft. Every run publishes three files: the
reader report, an evidence ledger and a machine-readable quality record.

It runs from a command line, a FastAPI service, or a Next.js console that
renders the live pipeline. The default stack is DeepSeek chat, local embeddings,
Tavily web search, ChromaDB memory and optional LangSmith tracing.

For how the pieces work internally, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The console's design is in [`docs/design/`](docs/design/README.md).

## Pipeline

```text
question -> planner -> researcher -> source_evaluator -> evidence_verifier
         -> report_writer -> report_reviewer
         -> publish (finalize_report)
            | extra_pass -> researcher        (required targets still unanswered)
            | writer_redraft -> report_writer (the review named a material defect)
            | note_pass -> researcher         (a reader note owes a research pass)
```

| Stage | What it does |
| --- | --- |
| Planner | Breaks the question into prioritized sub-topics with evidence targets. |
| Researcher | Runs a bounded search-and-read loop per sub-topic and extracts findings with the snippet each came from. |
| Source Evaluator | Scores every source behind a finding for authority, recency and relevance. |
| Evidence Verifier | Confirms each snippet is on its page, then checks each figure's context with the model; drops what it cannot confirm. |
| Report Writer | Drafts one section per plan part and the bottom line, cites findings by label, and builds the evidence ledger. |
| Report Reviewer | Scores the report once and decides: accept, one targeted extra pass, one redraft, or publish as partial. |

## Setup

Prerequisites: Python 3.11+, a DeepSeek API key and a Tavily API key. The web
console additionally needs Node.js and npm.

```bash
git clone <repo-url>
cd deep-research
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows
pip install -e ".[dev]"
cp .env.example .env           # then add your API keys
python -c "import deep_research; print(deep_research.__version__)"
cd web && npm install          # console only; run the two commands separately in Windows PowerShell
cd ..
```

## Configuration

Non-secret settings live in [`config.yaml`](config.yaml); secrets live in the
environment. `load_config` reads a `.env` file beside `config.yaml`
automatically, and a variable already set by the shell, CI or the deployment
platform wins over `.env`. Keep personal keys only in `.env`, which Git ignores.

**Overrides.** A setting can be overridden by an uppercase variable named after
its full path: `llm.model` is `LLM_MODEL`, `memory.long_term.persist_directory`
is `MEMORY_LONG_TERM_PERSIST_DIRECTORY`, `agents.verifier_concurrency` is
`AGENTS_VERIFIER_CONCURRENCY`, `graph.max_extra_passes` is
`GRAPH_MAX_EXTRA_PASSES`. Only the names listed in `_ENVIRONMENT_OVERRIDES` in
[`src/deep_research/utils/config.py`](src/deep_research/utils/config.py) are
read. Per-agent model profiles (`llm.model_overrides`), tool budgets
(`agents.tool_budget_overrides`), the reader-interaction timings (`hitl.*`) and
the request budget (`request_budget.*`) are not environment-driven: they come
from the YAML file or, per API request, from `config_overrides`.

**Secrets** are environment-only and never appear in typed settings or
telemetry. The CLI and API load configuration in strict mode and refuse to start
when a required secret is missing:

| Selected providers | Required for a research run |
| --- | --- |
| DeepSeek chat + local embeddings (default) | `DEEPSEEK_API_KEY`, `TAVILY_API_KEY` |
| DeepSeek chat + OpenAI embeddings | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + local embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + OpenAI embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |

**Tracing.** `config.yaml` turns LangSmith tracing on. `.env.example` sets
`LANGSMITH_TRACING=false`, which keeps spans and metrics local. With tracing on,
strict mode also requires `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT`. A
LangSmith transport failure is recorded as a recoverable error and never stops
a run.

## Providers

Chat defaults are under `llm` in `config.yaml`: provider `deepseek`, model
`deepseek-flash`, thinking mode `enabled`, reasoning effort `high`, with
per-agent effort and timeout profiles in `llm.model_overrides`. Embeddings
default to `local` (ChromaDB's bundled ONNX model, 384 dimensions): offline, no
key, no per-call cost. Override with `LLM_PROVIDER`, `LLM_MODEL`,
`LLM_THINKING_MODE`, `LLM_REASONING_EFFORT`, `LLM_EMBEDDING_PROVIDER`,
`LLM_EMBEDDING_MODEL`, `LLM_TIMEOUT` and `LLM_RETRY_COUNT`.

To use OpenAI for chat, set `LLM_PROVIDER=openai` with a model and a
thinking/effort pair its capability registry
([`providers/capabilities.py`](src/deep_research/providers/capabilities.py))
supports, for example `LLM_MODEL=gpt-5.6`, `LLM_THINKING_MODE=enabled`,
`LLM_REASONING_EFFORT=high`. Set `LLM_EMBEDDING_PROVIDER=openai` to embed with
OpenAI as well. Selection fails fast and never falls back: an unknown provider or
an unsupported model/thinking/effort combination raises
`ProviderConfigurationError` before any request, and a selected provider that
fails stays failed.

## Run the app

The console in `web/` talks to the API through a same-origin proxy
(`/api/*` to `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8000`). Run two
processes.

```bash
# 1. The API, from the repository root. Live: the real graph and real providers;
#    needs the secrets above and spends provider credit on every session.
python -m deep_research.api --mode live --host 127.0.0.1 --port 8000

#    Or replay: the real graph on scripted offline cases. No network, no keys,
#    no credit spent.
python -m deep_research.api --mode replay --replay-case missing-target-triggers-one-extra-pass --replay-delay-ms 150

# 2. The console, in web/
npm run dev        # http://localhost:3000, bound to 127.0.0.1
```

`--host` defaults to `127.0.0.1` and `--port` to `8000`. `--replay-case` and
`--replay-delay-ms` are valid only with `--mode replay`; the case defaults to
`missing-target-triggers-one-extra-pass` and the delay to 150 ms between
released events (`0` releases events as they arrive). Replay mode runs the whole
server with placeholder credentials and every socket connection refused, so a
session costs nothing and finishes in seconds. A `POST /research` may pick the
case with `X-Replay-Case: <case id>`, make the one-time check ask its fixed
questions with `X-Replay-Clarify: on`, or hold the stream after an event with
`X-Replay-Hold-After: <event type>[#<n>]`. Sessions live in the API process's
memory and disappear when it exits.

## Command Line Interface

```bash
python -m deep_research "What are the security implications of quantum computing?"
python -m deep_research "AI in healthcare" --max-iterations 1 --output-format markdown --verbose
python -m deep_research --interactive
python -m deep_research --resume <session_id>
```

| Option | Meaning |
| --- | --- |
| `question` | The research question. Mutually exclusive with `--interactive` and `--resume`. |
| `--interactive` | Prompt once for the question, run once, exit. |
| `--resume SESSION_ID` | Continue a checkpointed session. Works only inside the process that started it (see below). |
| `--max-iterations N` | Ceiling on extra research passes for missing required targets. `0` is accepted and buys no extra pass. Defaults to `graph.max_extra_passes` (1). |
| `--output-format` | Report format. Only `markdown` is supported. |
| `--config PATH` | YAML config file. Defaults to `config.yaml`. |
| `--verbose` | Also print tool-call totals, the proposals the researcher dropped, the request budget, token totals and the typed error messages behind the warnings. |
| `--debug-events` | Print every recorded event with its type and source and no metadata (span lifecycle events stay out). |
| `--require-quality` | Exit 4 unless the terminal quality gates accepted the report. Without it, a finished partial report exits 0. |
| `--request-deepseek-attempt-ceiling N`, `--request-openai-attempt-ceiling N`, `--request-tavily-attempt-ceiling N` | Transport attempts this run may reserve per provider. The attempt past the limit is refused before any network call; an undeclared ceiling only counts attempts. |
| `--request-stop-fraction F` | Fraction of each ceiling the run may spend, `0 < F <= 1`; the limit is `floor(ceiling * F)`. |

| Exit code | Meaning |
| --- | --- |
| 0 | The run finished (`completed`, `max_iterations` or `incomplete`). A partial report is still 0 unless `--require-quality` is set. |
| 1 | Configuration failure: bad config path, missing keys, unsupported format, no question, no resumable checkpoint. |
| 2 | Usage error. |
| 3 | The graph failed (`failed`). |
| 4 | `--require-quality` was set and the report was not accepted. |
| 130 | Interrupted with Ctrl-C. |

Recoverable research errors never fail the command. They print as `warning:`
lines, grouped by agent, type and cause, and are disclosed in the report.

Progress streams while the graph runs. The summary then prints, in order: the
session id and status, the quality verdict with the review's status and score,
the reasons for a partial verdict, required targets answered and Not found,
sources assessed versus cited, the review's fingerprint, findings checked and
dropped, integrity counts, a telemetry line (with advice when a provider
throttled), anything unresolved, the three artifact paths, the trace URL and the
elapsed time. Every line is read from the same typed records the artifacts
render from, so the console, the report, the ledger and the quality record
cannot disagree about a run.

**`--resume` works only inside one process.** Checkpointing uses LangGraph's
in-memory saver (`graph.checkpointing_enabled`), which does not survive the
process that created it, so resuming from a new command exits 1 with a clear
message instead of pretending a checkpoint exists.

### Lowering parallelism when a provider throttles

Six bounds decide how many provider calls one run makes at once. Lowering one is
a config or environment change, not a code change:

| Setting | Shipped value | What it bounds | Environment override |
| --- | --- | --- | --- |
| `agents.sub_topic_concurrency` | 10 | Researcher sub-topics in flight | `AGENTS_SUB_TOPIC_CONCURRENCY` |
| `agents.source_scoring_concurrency` | 6 | Source-evaluator scoring batches in flight | `AGENTS_SOURCE_SCORING_CONCURRENCY` |
| `agents.verifier_batch_size` | 5 | Context Check and Statement Check items per call | `AGENTS_VERIFIER_BATCH_SIZE` |
| `agents.verifier_concurrency` | 64 | Verification calls in flight | `AGENTS_VERIFIER_CONCURRENCY` |
| `agents.extraction_concurrency` | 16 | One sub-topic's per-page extraction calls in flight | `AGENTS_EXTRACTION_CONCURRENCY` |
| `agents.writer_section_concurrency` | 10 | The writer's section drafts in flight | `AGENTS_WRITER_SECTION_CONCURRENCY` |

When the telemetry line reports rate limits, lower the knob its advice names,
usually `agents.verifier_concurrency` first and then
`agents.sub_topic_concurrency`. Lower `agents.verifier_batch_size` only if calls
are being truncated.

### Artifacts

A finished run publishes three files under `output.directory` (default
`output/`). The set is advertised whole or not at all: if a write fails, no path
is printed.

| Artifact | Contents | Name |
| --- | --- | --- |
| Reader report | The bottom line first, an optional question-shaped table, one section per plan part, what could not be confirmed, and **Sources** listing only what the statements cite. | `report-<session>-<iteration>.md` |
| Evidence ledger | Every finding with its snippet and read locator, every figure kept or dropped with its reason, the verification record, and every drafted sentence the Statement Check refused. | `report-<session>-<iteration>-evidence.md` |
| Quality record | Counts and ids, the SHA-256 of each published document, the quality contract version, findings with their verification, fact rows, Not found, and the review's status and packet fingerprint. | `report-<session>-<iteration>-quality.json` |

Writing cited findings to long-term memory is a separate step that happens only
for an accepted report; a failed memory write is counted on its own and leaves
the three paths advertised.

### Quality semantics

The terminal status is printed on every summary and stamped on all three
artifacts. Nine deterministic gates judge the structure of the report:
`unresolved_citations`, `uncited_settled_points`, `duplicate_fact_rows`,
`missing_as_of`, `missing_scope`, `unjudged_sentences`,
`unaccounted_required_targets`, `missing_reader_report` and
`missing_evidence_ledger`.

A run is **accepted** only when all three hold: no gate failed; the Report
Reviewer scored the report with a mean of at least 0.80 over its seven
dimensions, with no material defect and a disposition recorded for every
statement it read; and the router's route was `report_accepted`. Anything else is
**partial**, including a report no review judged and one whose review was lost
to a provider failure. The run status is then one of `completed` (accepted),
`max_iterations` (extra passes spent, required targets still missing, report not
accepted), `incomplete` (published without an accepted judgement) or `failed`
(the graph halted).

## FastAPI interface

`python -m deep_research.api` serves an in-process API: one start endpoint, a
session list, session-scoped reads (`status`, `stream`, `report`, `evidence`,
`trace`) and three session-scoped writes (`answers`, `notes`, `stop`). Sessions,
background tasks and event history are process-local memory. There is no
authentication, database or durable queue. Every response carries
`X-Deep-Research-Mode: live` or `replay`.

| Method | Path | Response |
| --- | --- | --- |
| `POST` | `/research` | `202` session snapshot |
| `GET` | `/research` | `200` `{"sessions": [...]}`, newest first (`?limit=`, default 20, 1 to 200) |
| `GET` | `/research/{session_id}/status` | `200` session snapshot |
| `GET` | `/research/{session_id}/stream` | `200` `text/event-stream` |
| `GET` | `/research/{session_id}/report` | `200` `text/markdown` |
| `GET` | `/research/{session_id}/evidence` | `200` JSON of findings, verification, sources, Not found and refused sentences; `?format=markdown` returns the evidence ledger as `text/markdown` |
| `GET` | `/research/{session_id}/trace` | `200` `{session_id, trace_url, metadata}` |
| `POST` | `/research/{session_id}/answers` | `202` snapshot; answers the one-time check |
| `POST` | `/research/{session_id}/notes` | `202` `{"note_id": ..., "status": "received"}`; adds a reader note |
| `POST` | `/research/{session_id}/stop` | `202` snapshot with `status: "stopped"` |

The start body accepts `query` (required), `max_iterations` (extra passes,
`>= 0`), `output_format` (`markdown`), `config_overrides` and
`ask_clarifying_questions` (default `true`). Unknown keys and unknown override
paths are rejected with `422` before a session exists.

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"query": "How mature is quantum error correction?",
       "max_iterations": 1,
       "config_overrides": {"output": {"directory": "api-output/"}}}'

curl -N http://localhost:8000/research/<session_id>/stream     # live events
curl http://localhost:8000/research/<session_id>/report        # once finished
```

`status` is `running`, `needs_input`, then one of `completed`,
`max_iterations`, `incomplete`, `failed` or `stopped`. The snapshot carries
`session_id`, `query`, `status`, `current_agent`, `iteration`, `started_at`,
`finished_at`, `report_path`, `evidence_path`, `quality_path`, `trace_url`,
`errors`, and, once finished, the review status and score, `coverage`,
`evidence_counts` and `report_outline`; fields not yet measured are `null`, never
`0`.

**One-time check.** Unless `ask_clarifying_questions` is `false`, the session
first asks the model whether the question leaves something material open. Usually
it does not and the run starts at once. With up to three questions, `status`
reads `needs_input` and the stream carries `session.clarification.requested`;
post the answers once to `/research/{session_id}/answers` as
`{"answers": [{"question_id": "q1", "choice": "Global"}], "skip": false}`. A
question left out takes its best guess; with no answers the run starts on best
guesses after `hitl.answer_wait_s` (60 s).

**Notes and stop.** While a session runs the reader may add up to ten notes
(1 to 500 characters) to focus, leave out or change something, or stop the run.
A stopped run is cancelled where it stands and publishes nothing:

```bash
curl -X POST http://localhost:8000/research/<session_id>/notes \
  -H "Content-Type: application/json" -d '{"text": "More on fire-safety standards"}'
curl -X POST http://localhost:8000/research/<session_id>/stop
```

The stream is server-sent events, one typed `ResearchEvent` per frame (`id:`,
`event:` and a JSON `data:` line). A late subscriber replays the session's events
from id 1, then follows live progress; the stream ends when the session reaches a
terminal status.

Errors have the shape `{"error": {"code", "message", "reason", "issues"}}`:

| Status | Codes |
| --- | --- |
| `404` | `session_not_found` (identical for every session-scoped route) |
| `409` | `session_not_complete`, `report_unavailable`, `evidence_unavailable`, `not_waiting_for_input`, `notes_closed`, `note_limit_reached`, `not_stoppable` (`reason`: `finished`, `publishing`, `closing`) |
| `422` | `validation_error`: field locations and types only, never the rejected values |
| `500` | `configuration_error`: missing or invalid service configuration, without secrets or tracebacks |

## Evaluation

Two separate harnesses measure quality. Neither is part of the default test run.

### Per-agent evaluation

`python -m deep_research.evaluation` runs controlled and live experiments for one
agent at a time (`planner`, `researcher`, `source-evaluator`,
`evidence-verifier`, `report-writer`) against LangSmith, scored by deterministic
gates and a judge model. It never imports `deep_research.graph`, so it does not
exercise routing, redrafts or publication.

```bash
python -m deep_research.evaluation list                                    # agents, tiers, cases, dataset names
python -m deep_research.evaluation agent researcher                        # all controlled cases, 3 repetitions
python -m deep_research.evaluation agent researcher --case conflicting-evidence
python -m deep_research.evaluation agent researcher --tier live            # the agent's one live case
python -m deep_research.evaluation agent researcher --reasoning-effort medium
python -m deep_research.evaluation agent researcher --target-thinking-mode disabled
python -m deep_research.evaluation suite                                   # controlled experiments, all five agents
```

Shared options: `--config`, `--tier`, `--output-directory`,
`--experiment-prefix`, `--judge-reasoning-effort`, `--verbose` and
`--production-parity` / `--no-production-parity`. `agent` adds `--case`,
`--reasoning-effort` and `--target-thinking-mode`.

| Exit code | Meaning |
| --- | --- |
| 0 | Completed; the automated gates and scores passed |
| 1 | Completed but failed gates or scores |
| 2 | Invalid usage, unknown agent, tier or case, or an invalid local case registry |
| 3 | Configuration, credential, dataset-sync or LangSmith infrastructure failure |
| 130 | Interrupted with Ctrl-C |

**Controlled and live evaluation make real DeepSeek and LangSmith calls and cost
real money.** There is no dry-run mode for `agent` or `suite`; live-tier runs
also use real tools such as Tavily search. Run `list` first. Required
environment: the chat provider's key (`DEEPSEEK_API_KEY` by default),
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and `TAVILY_API_KEY` for live cases
whose agent declares `web_search`.
`LANGSMITH_ENDPOINT` and `LANGSMITH_WORKSPACE_ID` are optional and read by the
LangSmith SDK. Each `agent` run writes
`output/evaluations/<agent>/<experiment-name>/results.json`, and each `suite`
run writes `output/evaluations/suite/<suite-id>/summary.json`;
`--output-directory` moves the root.

### Whole-report evaluation

`python -m deep_research.e2e_evaluation` checks the five-agent handoff, citations,
the evidence ledger, the Statement Check, the targeted extra pass and publication
on a versioned matrix of offline cases. It exposes two commands:

```bash
python -m deep_research.e2e_evaluation list
python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
```

`suite` runs all 35 rows of the replay manifest (manifest v8, case semantics v2).
Each row enters through the production `deep_research.cli` with the five
production agents, the real graph, reviewer, renderer and publisher, and only the
external boundaries scripted; the socket layer is denied, and a suite whose runs
attempted network access is not accepted. A row passes when all three repetitions
meet its declared product result (accepted or partial, and the exit code) and
produce one identical outcome; disagreeing repetitions fail as `NON-deterministic`.
No judge score is computed. The controlled tier requires exactly three
repetitions, and `--tier live` is declared only and refused before anything runs.
Output goes to `output/evaluations/e2e/replay-suite.json`, with each repetition's
documents under `output/evaluations/e2e/replay/<case-id>/repetition-<n>/`. Exit
code 0 means the suite was accepted, 1 that it was not, and 2 an error such as an
invalid tier or repetition count.

## Development

```bash
pytest                                  # Python tests (the live marker is excluded by default)
ruff check src tests                    # lint

cd web
npm test                                # Vitest
npm run typecheck                       # tsc --noEmit
npm run test:e2e                        # next build, then Playwright against the API in replay mode
```

`npm run test:e2e` starts the API with `.venv/Scripts/python.exe`; set
`DEEP_RESEARCH_PYTHON` to the interpreter if your virtual environment lives
elsewhere. Run `npx playwright install chromium` once beforehand.

The live DeepSeek smoke test is opt-in and makes one bounded structured call
(no embeddings, no Tavily). It needs `DEEPSEEK_API_KEY`; on PowerShell set
`$env:RUN_DEEPSEEK_LIVE_TESTS="1"` instead of the inline variable:

```bash
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -v
```

## Project layout

```text
src/deep_research/
  agents/            The five agents, Report Reviewer, ReAct runtime, report and quality logic
  api/               FastAPI app, session store, SSE, one-time check, notes, stop, replay server
  e2e_evaluation/    Whole-report replay matrix and its CLI
  evaluation/        Per-agent LangSmith evaluation harness and its CLI
  experiments/       Hand-run latency experiments (stage replay, paired live runs)
  graph/             LangGraph state, nodes, routing, events, session runner
  memory/            Scratchpad, ChromaDB long-term and JSON procedural memory
  observability/     Tracker, metrics, run telemetry, LangSmith integration
  providers/         DeepSeek and OpenAI chat, local and OpenAI embeddings, retry, capabilities
  runtime/           Assembles a runnable session; the outcome the CLI and API report
  tools/             web_search, web_scraper, document_reader, memory tools, write_document
  utils/             Typed configuration, research types, text and concurrency helpers
  cli.py             python -m deep_research
  main.py            run_research(): the one entry point the CLI and API share
  request_budget.py  Per-provider transport-attempt ceilings
web/                 Next.js console (see web/README.md)
tests/               Python test suite
docs/                ARCHITECTURE.md (backend internals), design/ (console design, API gaps, prototype)
cloud-session/       Tooling for Claude Code cloud sessions (not part of the application)
config.yaml          Default runtime configuration (.env.example is the secrets template)
```

`memory/` (long-term and procedural memory) and `output/` (published reports and
evaluation artifacts) are created at run time and ignored by Git.
