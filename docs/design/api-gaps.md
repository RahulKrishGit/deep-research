# Per-stage API gaps

What each stage of the console needs that the current FastAPI surface does not
serve. Every entry names the ground truth — the object that already holds the
value inside the process — so the gap is a transport gap rather than missing data.

The rule this document exists to protect: **where a value is unavailable, the
interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder,
or a disabled control.**

The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed) and a collapsible session sidebar, so gaps are
keyed `{stage}.{n}` plus `SB.{n}` for the sidebar; E1, 1.1, 1.2, 3.1 and 3.7 are closed
and recorded above. Re-keyed on 2026-09-26 to the
Evidence Verifier pipeline (`f27ac7e`); the 2026-09-16 ids are kept in brackets.

---

## Existing surface, for reference

| Method | Path | Returns |
|---|---|---|
| `POST` | `/research` | `202` `ResearchSessionResponse` (`api/app.py:159-188`; `max_iterations` is passed to the graph as `max_extra_passes`, `:183`) |
| `GET` | `/research` | `200` `{"sessions": [ResearchSessionResponse, …]}`, newest first, `?limit=` 1–200, default 20 (`:198-205`) |
| `GET` | `/research/{id}/status` | `200` `ResearchSessionResponse` (`:190-202`) |
| `GET` | `/research/{id}/stream` | `200` `text/event-stream`, replayed from id 1 then live; ids restart at 1 per subscriber (`:204-236`, `api/events.py:14-27`) |
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`:238-263`) |
| `GET` | `/research/{id}/evidence` | `200` JSON or `text/markdown` (`?format=`), or `409` `session_not_complete` / `evidence_unavailable` (`:313-338`) |
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`:265-269`) |

`ResearchSessionResponse` (`api/models.py:114-164`, assembled at
`api/sessions.py:73-128`), 18 fields: `session_id`, `query`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, `errors`, `evidence_path`, `quality_path`,
`quality_contract_version`, `semantic_review_status`,
`semantic_review_score`, `duration_seconds`, `coverage`
(`required_targets`, `answered_targets`, `missing_required_target_ids`,
`not_found_target_ids`) and `evidence_counts` (fifteen counts; `null`
unless the run left both a composition and a quality snapshot,
`runtime/outcome.py:525`). `status` is one of `running`, `completed`,
`max_iterations`, `incomplete`, `failed`. Not on the response:
`quality_status`, `max_iterations`/`max_extra_passes`, token usage,
tool-call totals, any report structure.

Every response carries `X-Deep-Research-Mode: live|replay`.

`ResearchRequest` accepts `query`, `max_iterations` (`int | None`, `ge=0`,
default → config), `output_format` and `config_overrides`; overrides are
validated against `ConfigSettings()`, so an unknown path is a `422` before a
session exists.

**Delivery model.** Events reach the stream as they happen (3.7, closed
2026-09-28). Every `ResearchEvent` carries an `event_id`. `agent_node` publishes
`graph.node.started` when a node starts, and the agents publish their progress
events the moment they build them — the researcher's `researcher.sub_topic.started`,
each `researcher.tool_call` as its step is recorded, and
`researcher.sub_topic.completed`, while its topics run concurrently — through the
run's sink (`graph/live.py`). The per-superstep snapshot publishes everything else,
in state order, skipping any `event_id` already published live
(`graph/orchestrator.py`, `_stream_graph_result`). Every event is delivered exactly
once; a live event can arrive ahead of events its node recorded earlier; a node that
halts after publishing live has delivered those events although its halted state
keeps none of them. Every running-stage rule in DESIGN.md §3.5 and §5.7 stays
burst-safe: the state after event *k* depends only on events 1..*k*.

---

## Closed since 2026-09-16

Kept as a record, one line each.

| Old # | Gap | How it closed |
|---|---|---|
| 2.4 | `quality_status` | `completed` ⇔ route `report_accepted` (`graph/state.py:116`, `:322-337`); `semantic_review_status` splits the partial outcomes |
| 3.2 | quality snapshot | `coverage`, `evidence_counts`, `semantic_review_status`, `semantic_review_score` are on the response (`api/sessions.py:73-128`) |
| 3.3 | `evidence_path` | the path is served; the content moves to E1 |
| 3.5 | claim verdicts and confidence | obsolete: the pipeline has no claims; findings carry a verification status instead (E1) |
| 2.2 | tool-call counts | partly closed: the verifier, writer and reviewer make no tool calls; `researcher.tool_call` misses the planner's single budgeted call and `finalize_report`'s `write_document` and `save_to_memory` calls (`config.yaml:159`; `agents/planner.py:2999`; `agents/report_writer.py:3286-3293`; `graph/nodes.py:586`); the planner's count is recoverable from `planner.planning.completed.tool_calls`, but `finalize_report`'s calls carry no event-stream count at all — only `ResearchOutcome.tool_calls` (`tools/base.py:107-113`) sees them |
| E1 | `GET /research/{id}/evidence` | served since 2026-09-27: JSON in the shape recorded here (built from `ReportComposition` by `api/evidence.py`, reusing the evidence log's own label pairing and figure text); `?format=markdown` returns `state.report_evidence` as `text/markdown`; `409 session_not_complete` while running, `409 evidence_unavailable` when nothing was composed |
| 1.1 | `query` echo | `query` is on every `ResearchSessionResponse` (`api/models.py`) |
| 1.2 | `GET /research` | the session list, newest first, `?limit=` 1–200 (default 20); process-local memory, as SB.2 records |
| — | `/status.iteration` store fix | `ResearchSession.publish` copies `iteration` from `graph.*` events only (`api/sessions.py`), so `researcher.tool_call`'s ReAct step index never moves the pass |
| 3.7 | live per-event delivery | closed 2026-09-28 (live-briefs spec E1–E3): every `ResearchEvent` carries an `event_id`; `agent_node` and the agents publish progress live through the run's sink (`graph/live.py`), and the snapshot loop skips ids already published, so each event is delivered once (`graph/orchestrator.py`) |
| 3.1 | `max_iterations` echo | obsolete 2026-09-28: the console no longer shows a pass ceiling or sends a budget (live-briefs D14, D15); `max_extra_passes` stays on the stream at `graph.session.started` |

---

## Stage 1 — Idle (composer)

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 1.3 | **The effective settings echo, now including per-role effort** [1.3] | The composer sends `llm.model` and `llm.thinking_mode` as overrides and `max_iterations` at the top level, then cannot show what was actually used; effort is fixed per role in `llm.model_overrides` (`config.yaml:32-63`) and the response has no config block, so the settings strip shows the submitted values and the effort line states the configured ones. | `ConfigSettings.llm` after `apply_config_overrides` | Show the submitted values, labelled as submitted; state effort per agent from the configuration | A non-secret `config` block: `provider`, `model`, `thinking_mode`, per-role `reasoning_effort`, `max_extra_passes` |
| 1.4 | **`GET /capabilities`** [1.4 + 1.5] | The provider override is now accepted by validation, but nothing lists the valid provider/model/thinking-mode/effort combinations, so the composer mirrors `capabilities.py` by hand (three DeepSeek models, `enabled`/`disabled`) and that copy drifts the moment the registry changes. | `_CAPABILITIES` in `providers/capabilities.py:71-77` | Hand-mirrored; the configured provider only | `GET /capabilities` returning `{provider, models[], thinking_modes[], enabled_efforts[]}[]` |
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
| 3.4 | **Event identity / `Last-Event-ID`** [2.6] | SSE `id` is per-subscriber and starts at 1, so it is a stream position rather than an event identity. A reconnect cannot ask for "everything after what I saw". | `ResearchSession.events` list index | Re-derive the running stage from the full replay — every rule is idempotent over events 1..k | A monotonic `sequence` on `ResearchEvent`, plus `Last-Event-ID` support |
| 3.5 | **Halting-type vocabulary** [2.5] | The failed stage headlines the halting type in plain words and the rail groups recoverable errors, but the client keeps its own copy of `HALTING_ERROR_TYPES` to know which is which. | `HALTING_ERROR_TYPES` in `graph/state.py:141-150` | Client copy, small and stable | `halting: bool` on `ResearchError`, or publish the enumerated set |
| 3.6 | **Shutdown while running** [2.7] | On cancellation the store sets `finished_at` and leaves `status` as `running`, then the stream ends. The console sees a closed stream with a non-terminal status. | Deliberate: *"cancellation stays cancellation"* | On stream close, re-read `/status`; a closed stream with `finished_at` set and `status == "running"` means the service stopped | The terminal frame from 3.3, or an explicit status |

---

## Stage 4 — Report

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 4.1 | **Report body JSON** [3.1], **downgraded to nice-to-have** | `GET /report` returns Markdown only. The consumer format is stable and parseable — H2 sections in a fixed order, source lines matching `^(\d+)\. ` (`e2e_evaluation/runner.py:55`) — so the console renders it as-is with presentation-only adaptations (DESIGN.md §2 A) and a JSON projection would merely save the parse. | `ReportComposition` | Render the Markdown as-is; the server owns wording, order, numbering and the table | `GET /research/{id}/report?format=json` returning a safe projection, if ever wanted |
| 4.2 | **Report hash on the response** [3.7] | The console cannot show that the body it fetched is the body that was judged, rather than an earlier pass's. `quality.json` already hashes both Markdown documents (`agents/report.py:347-357`, `:634`); the response does not expose them. | `render_quality_record` writes `"artifacts": {name: sha256}` | The console fetches once and caches | `artifacts: {report: sha256, evidence: sha256}` on the response, copied from the quality record |

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
- **Re-run / cancel endpoints.** `SessionStore` cancels only on shutdown. A cancel
  route would need task ownership and a partial-artifact question the API has not
  answered; until it does, the console offers new research instead.
- **An embedded trace viewer.** `trace_url` leaves the application. LangSmith owns
  that surface and duplicating it would be a second, worse implementation.
- **A rendered event log.** The stream is consumed as derived counters, not
  rendered. That is a product decision (DESIGN.md §5.7), and reintroducing a log
  view needs no new endpoint, so it is not a gap.
- **Any count rendered as `0` when the source is absent.** Every unavailable field
  is muted text. This is the rule the rest of the document exists to serve.
- **Per-agent effort editing.** An override of `llm.model_overrides.<role>`
  replaces that role's whole entry and drops its `timeout`
  (`utils/config.py:664-686`); a global `llm.reasoning_effort` is shadowed by
  every role's own value (`config.py:85-107`). The composer states the configured
  effort per agent and offers no control for it.
