# Per-stage API gaps

What each stage of the console needs that the current FastAPI surface does not
serve. Every entry names the ground truth — the object that already holds the
value inside the process — so the gap is a transport gap rather than missing data.

The rule this document exists to protect: **where a value is unavailable, the
interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder,
or a disabled control.**

The console is one page with six stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed, 6 Stopped by you), a one-time check that can come between 2 and 3
(2a) and a collapsible session sidebar, so gaps are
keyed `{stage}.{n}` plus `SB.{n}` for the sidebar; E1, 1.1, 1.2, 3.1 and 3.7 are closed
and recorded under Closed gaps below. An id in brackets is the gap's earlier numbering.

---

## Existing surface, for reference

| Method | Path | Returns |
|---|---|---|
| `POST` | `/research` | `202` `ResearchSessionResponse` (`api/app.py` `start_research`; `max_iterations` is passed to the graph as `max_extra_passes`) |
| `GET` | `/research` | `200` `{"sessions": [ResearchSessionResponse, …]}`, newest first, `?limit=` 1–200, default 20 (`list_research`) |
| `GET` | `/research/{id}/status` | `200` `ResearchSessionResponse` (`research_status`) |
| `GET` | `/research/{id}/stream` | `200` `text/event-stream`, replayed from id 1 then live; ids restart at 1 per subscriber (`research_stream`, `encode_sse` in `api/events.py`) |
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`report_unavailable` for a stopped session too) (`research_report`) |
| `GET` | `/research/{id}/evidence` | `200` JSON or `text/markdown` (`?format=`), or `409` `session_not_complete` / `evidence_unavailable` (`evidence_unavailable` for a stopped session too) (`research_evidence`) |
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`research_trace`) |
| `POST` | `/research/{id}/answers` | `202` `ResearchSessionResponse`: the reader's answers to the one-time check, taken once; `404` unknown session, `409` `not_waiting_for_input`, `422` an answer that does not fit its question (`api/app.py` `answer_research`, `api/sessions.py` `submit_answers`) |
| `POST` | `/research/{id}/notes` | `202` `{note_id, status: "received"}`: one reader note, read in the background (`session.note.received`, then `session.note.interpreted`); `404` unknown session, `409` `notes_closed` (waiting for answers, once `finalize_report` has started — from the run's published decision to publish — or finished) or `note_limit_reached` (past the tenth note), `422` empty or over 500 characters (`api/app.py` `add_research_note`, `api/sessions.py` `add_note`) |
| `POST` | `/research/{id}/stop` | `202` `ResearchSessionResponse` with `status: "stopped"`: the run is cancelled where it stands and writes nothing; `404` unknown session; `409` `not_stoppable` with `reason` `finished` (it has ended, a second stop included), `publishing` (from the run's published decision to publish or end) or `closing` (the service shutting down) (`api/app.py` `stop_research`, `api/sessions.py` `stop`) |

`ResearchSessionResponse` (`api/models.py`, assembled by `_session_response` in `api/app.py`
from `outcome_response_fields` and `session_note_fields` in `api/sessions.py`), 24 fields: `session_id`, `query`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, `errors`, `evidence_path`, `quality_path`,
`quality_contract_version`, `semantic_review_status`,
`semantic_review_score`, `duration_seconds`, `coverage`
(`required_targets`, `answered_targets`, `missing_required_target_ids`,
`not_found_target_ids`), `evidence_counts` (fifteen counts; `null`
unless the run left both a composition and a quality snapshot,
`runtime/outcome.py`), and the reader's side:
`notes` (each note as written, the run's reading of it and its outcome),
`notes_remaining`, `note_passes`, `clarification` (the one-time check's
questions and the answers the run started with, or `null`), `stopped_step` (the step a
stopped session was stopped at — `check` or a pipeline row — else `null`) and
`report_outline` (the published report's `##` headings in order, each with its kind, its
contents label and, for a topic, its number and the note it answers; `null` without a
published report). `status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
`max_iterations`, `incomplete`, `failed`, `stopped` (the reader stopped the run:
terminal, nothing published). Not on the response:
`quality_status`, `max_iterations`/`max_extra_passes`, token usage,
tool-call totals, any report structure beyond `report_outline`.

Every response carries `X-Deep-Research-Mode: live|replay`.

`ResearchRequest` accepts `query`, `max_iterations` (`int | None`, `ge=0`,
default → config), `output_format`, `config_overrides` and
`ask_clarifying_questions` (`bool`, default `true`; the one-time check, whose
timings are the `hitl` config section); overrides are
validated against `ConfigSettings()`, so an unknown path is a `422` before a
session exists.

**Delivery model.** Events reach the stream as they happen (3.7, closed).
Every `ResearchEvent` carries an `event_id`. `agent_node` publishes
`graph.node.started` when a node starts, and the agents publish their progress
events the moment they build them — the researcher's `researcher.sub_topic.started`,
each `researcher.tool_call` as its step is recorded, and
`researcher.sub_topic.completed`, while its topics run concurrently — through the
run's sink (`graph/live.py`). The per-superstep snapshot publishes everything else,
in state order, skipping any `event_id` already published live
(`graph/orchestrator.py`, `_stream_graph_result`). Every event is delivered exactly
once; a live event can arrive ahead of events its node recorded earlier; a node that
halts after publishing live has delivered those events although its halted state
keeps none of them. The reviewer node publishes its `graph.node.started` before its one
review call and its `graph.report.reviewed` the moment the review lands, before it waits
for notes still being read (`graph/nodes.py`); both also stay in the node's own events, and
the snapshot skips them by `event_id`. Four progress types are live-only:
`planner.progress`, `source_evaluator.progress`,
`evidence_verifier.progress` and `report_writer.progress`. Each is published through the
run's sink and never returned in a node's `state_update["events"]`, so it is never in the
run's state or any snapshot; `ResearchSession.publish` records it, so a late `/stream`
replays it. Every running-stage rule in DESIGN.md §3.5 and §5.7 stays burst-safe: the
state after event *k* depends only on events 1..*k*. The one-time check's
`session.clarification.requested` and `.answered` are published by the session itself
(`api/sessions.py`, `_clarify`), before the graph starts. A stopped session's last event
is `session.stopped` (`{step, stopped_at, elapsed_seconds}`), published by the session
itself (`api/sessions.py`, `stop`); nothing is published after it.

---

## Closed gaps

Kept as a record, one line each.

| Old # | Gap | How it closed |
|---|---|---|
| 2.4 | `quality_status` | `completed` ⇔ route `report_accepted` (`graph/state.py`); `semantic_review_status` splits the partial outcomes |
| 3.2 | quality snapshot | `coverage`, `evidence_counts`, `semantic_review_status`, `semantic_review_score` are on the response (`api/sessions.py`) |
| 3.3 | `evidence_path` | the path is served; the content moves to E1 |
| 3.5 | claim verdicts and confidence | obsolete: the pipeline has no claims; findings carry a verification status instead (E1) |
| 2.2 | tool-call counts | partly closed: the verifier, writer and reviewer make no tool calls; `researcher.tool_call` misses the planner's single budgeted call and `finalize_report`'s `write_document` and `save_to_memory` calls (`tool_budget_overrides` in `config.yaml`; `agents/planner.py`; `agents/report_writer.py`; `graph/nodes.py`); the planner's count is recoverable from `planner.planning.completed.tool_calls`, but `finalize_report`'s calls carry no event-stream count at all — only `ResearchOutcome.tool_calls` (`tools/base.py`) sees them |
| E1 | `GET /research/{id}/evidence` | served: JSON in the shape recorded here (built from `ReportComposition` by `api/evidence.py`, reusing the evidence log's own label pairing and figure text); `?format=markdown` returns `state.report_evidence` as `text/markdown`; `409 session_not_complete` while running, `409 evidence_unavailable` when nothing was composed |
| 1.1 | `query` echo | `query` is on every `ResearchSessionResponse` (`api/models.py`) |
| 1.2 | `GET /research` | the session list, newest first, `?limit=` 1–200 (default 20); process-local memory, as SB.2 records |
| — | `/status.iteration` store fix | `ResearchSession.publish` copies `iteration` from `graph.*` events only (`api/sessions.py`), so `researcher.tool_call`'s ReAct step index never moves the pass |
| 3.7 | live per-event delivery | closed: every `ResearchEvent` carries an `event_id`; `agent_node` and the agents publish progress live through the run's sink (`graph/live.py`), and the snapshot loop skips ids already published, so each event is delivered once (`graph/orchestrator.py`) |
| 3.1 | `max_iterations` echo | obsolete: the console no longer shows a pass ceiling or sends a budget; `max_extra_passes` stays on the stream at `graph.session.started` |
| — | cancel a running session | closed: `POST /research/{id}/stop`, the terminal `stopped` status with `stopped_step`, and `session.stopped`; no report, evidence log, quality record or memory entry is written |

---

## Stage 1 — Idle (composer)

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 1.3 | **The effective settings echo, now including per-role effort** [1.3] | The composer sends `llm.model` and `llm.thinking_mode` as overrides, then cannot show what was actually used; effort is fixed per role in `llm.model_overrides` (`config.yaml`) and the response has no config block, so the settings strip shows the submitted values and the effort line states the configured ones. | `ConfigSettings.llm` after `apply_config_overrides` | Show the submitted values, labelled as submitted; state effort per agent from the configuration | A non-secret `config` block: `provider`, `model`, `thinking_mode`, per-role `reasoning_effort`, `max_extra_passes` |
| 1.4 | **`GET /capabilities`** [1.4 + 1.5] | The provider override is now accepted by validation, but nothing lists the valid provider/model/thinking-mode/effort combinations, so the composer mirrors `capabilities.py` by hand (three DeepSeek models, `enabled`/`disabled`) and that copy drifts the moment the registry changes. | `_CAPABILITIES` in `providers/capabilities.py` | Hand-mirrored; the configured provider only | `GET /capabilities` returning `{provider, models[], thinking_modes[], enabled_efforts[]}[]` |
| 1.5 | **`GET /health`** [1.6] | A configuration failure is only discoverable by submitting, so the operator loses the question they just typed. | `prepare_research_settings` is already a side-effect-free callable | None; the compose surface cannot warn ahead of time | `GET /health` → `{ready: bool, reasons: [enumerated]}`, reusing the `configuration_error` reasons |

---

## Stage 2 — Submitted

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 2.— | Nothing beyond stage 1 | The submitted beat shows the question read back and the settings strip, both from the client's own copy (1.1, 1.3). It needs no field the idle stage does not. | — | — | — |

---

## Stage 3 — Running

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 3.2 | **Token usage, absent on every stage** [2.1] | "Cost and usage" wants token totals and the client cannot derive them; they are absent while running and after the run alike. | `ResearchOutcome.token_usage`, from `TokenUsageMetric`s accumulated in the tracker | `Not recorded`, with the reason stated | A `usage` block on the status response, or cumulative totals on `graph.node.completed` metadata |
| 3.3 | **A terminal frame on the stream** [2.3] | The stream ends when the session reaches a terminal state, but the final frame is an ordinary event. The console learns *that* the run ended and must then call `GET /status` to learn *how* — and the stage transition depends on knowing how. | `graph.session.completed` carries `status`, `iteration`, `error_count`, `has_report`, but the store returns without synthesising a frame | On stream close, re-read `/status` and transition from it | One terminal `api.session.closed` frame carrying the final snapshot |
| 3.4 | **Resume / `Last-Event-ID`** [2.6] | SSE `id` is per-subscriber and starts at 1, so it is a stream position, not a resume token. Every event carries an `event_id`, which gives identity but not order, so a reconnect still cannot ask for "everything after what I saw". | `ResearchSession.events` list index; `ResearchEvent.event_id` (an unordered uuid4 hex) | Re-derive the running stage from the full replay — every rule is idempotent over events 1..k | A monotonic `sequence` on `ResearchEvent`, plus `Last-Event-ID` support |
| 3.5 | **Halting-type vocabulary** [2.5] | The failed stage headlines the halting type in plain words and the rail groups recoverable errors, but the client keeps its own copy of `HALTING_ERROR_TYPES` to know which is which. | `HALTING_ERROR_TYPES` in `graph/state.py` | Client copy, small and stable | `halting: bool` on `ResearchError`, or publish the enumerated set |
| 3.6 | **Shutdown while running** [2.7] | On cancellation the store sets `finished_at` and leaves `status` as `running`, then the stream ends. The console sees a closed stream with a non-terminal status. | Deliberate: *"cancellation stays cancellation"* | On stream close, re-read `/status`; a closed stream with `finished_at` set and `status == "running"` means the service stopped | The terminal frame from 3.3, or an explicit status |
| 3.8 | **No `checking` state for the one-time check** | While the live check call runs, for up to `hitl.check_timeout_s` (20 s), `status` reads `running` and the stream carries nothing, so the console shows stage 3 with Planning active; if questions come back, stage 2a replaces the pipeline card. Replay's checker answers at once, so replay never shows it. | `SessionStore._clarify` (`api/sessions.py`) knows the check is running but publishes nothing until the questions exist | Show stage 3 until `session.clarification.requested` arrives | A `session.clarification.started` event, or a `checking` status |
| 3.9 | **A replay run never applies a reader note** | Replay runs the graph at full speed and paces only the stream, so by the time the running stage shows a step the engine has finished; a note sent then is received, read and acknowledged, but no step reads it, it ends `not_checked` (`not checked`), and the report's bottom line prints no line for it: a note gets its line only when the run has read it by the time it publishes, and a note that arrives after the run has ended gets none, which in replay is every note. A live run applies every note that arrives before `finalize_report` starts (read from the run's decision to publish). | `ReplayRunner` (`api/replay.py`) drains its event queue after `run_research` returns | Prove the engine's use of notes offline (`tests/test_graph/test_reader_notes_replay.py`), and the page's flow on the replay server | A replay runner that holds each node until the stream has published the node's start |

---

## Stage 4 — Report

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 4.1 | **Report body JSON** [3.1], **downgraded to nice-to-have** | `GET /report` returns Markdown only. The consumer format is stable and parseable — H2 sections in a fixed order, source lines matching `^(\d+)\. ` (`_REFERENCE_LINE` in `e2e_evaluation/runner.py`) — so the console renders it as-is with presentation-only adaptations (DESIGN.md §2 A) and a JSON projection would merely save the parse. | `ReportComposition` | Render the Markdown as-is; the server owns wording, order, numbering and the table | `GET /research/{id}/report?format=json` returning a safe projection, if ever wanted |
| 4.2 | **Report hash on the response** [3.7] | The console cannot show that the body it fetched is the body that was judged, rather than an earlier pass's. `quality.json` already hashes both Markdown documents (`render_quality_record` in `agents/report.py`); the response does not expose them. | `render_quality_record` writes `"artifacts": {name: sha256}` | The console fetches once and caches | `artifacts: {report: sha256, evidence: sha256}` on the response, copied from the quality record |

---

## Stage 5 — Failed

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 5.1 | **What survived the halt** [4.1] | The stage shows the running stage's counters frozen at the halt. None of those counts are on the response — `evidence_counts` is `null` after a halt before the writer — so a reload has nothing to show. | `ResearchState` (findings, sources, verifications) at the halt | The stream counters, re-derived from the replay; a counter whose node never ran reads `not reached` | A `collected` summary block on the response, or `evidence_counts` computed from state even without a composition |
| 5.2 | **Reachable states** [4.2/4.3] | Two of the three partial outcomes are now reproducible for free through the offline replay cases `review-unavailable` and `empty-but-clean`; the halted and configuration-error states still need a genuinely misconfigured service or a real failure. | The replay harness (`e2e_evaluation/replay.py`) for the partial states; nothing for a halt | Documented in `states.html` with the exact response body and the eight-frame event tail | A test-only seeded session, or the `GET /health` from 1.5 |

---

## Sidebar

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| SB.1 | **A result summary per row** [5.2] | Rows want the terminal outcome and its clause (`review accepted · 0.86`, `1 target not found`). A row currently carries the question and a running mark and nothing else, so **the list cannot report how anything ended** — completed, partial and failed rows are indistinguishable until opened. | `semantic_review_status`, `semantic_review_score`, `coverage`, the route reason | The row carries only `status === "running"`; every settled outcome is one click away on the session itself | Fold the review, coverage and route reason into the list projection (1.2). This is the one gap whose absence is visible as a design decision rather than as missing text |
| SB.2 | **Durability** [5.3] | Sessions are process-local; the list resets when the API restarts. | By design — *"durable queues, databases … remain out of scope"* | Stated on screen. The console never implies persistence it does not have | A JSON-lines session index beside `output/` would be a smaller change than a database |

---

## Gaps the front end should *not* close

Recorded so nobody adds them later:

- **Collaboration, sharing, orgs, billing.** Single local operator, no auth. There
  is no identity to attach any of them to.
- **Re-run endpoints.** The cancel half is closed: `POST /research/{id}/stop`
  cancels the session's task and writes no partial artifact, because `finalize_report`
  never runs. A re-run or resume route stays out: a
  stopped session's **Ask again** starts a new session with the same question.
- **An embedded trace viewer.** `trace_url` leaves the application. LangSmith owns
  that surface and duplicating it would be a second, worse implementation.
- **A rendered event log.** The stream is consumed as derived counters, not
  rendered. That is a product decision (DESIGN.md §5.7), and reintroducing a log
  view needs no new endpoint, so it is not a gap.
- **Any count rendered as `0` when the source is absent.** Every unavailable field
  is muted text. This is the rule the rest of the document exists to serve.
- **Per-agent effort editing.** An override of `llm.model_overrides.<role>`
  replaces that role's whole entry and drops its `timeout`
  (`apply_config_overrides` in `utils/config.py`); a global `llm.reasoning_effort` is shadowed by
  every role's own value (`LLMConfig.resolve_for`). The composer states the configured
  effort per agent and offers no control for it.
