# Per-stage API gaps

What each stage of the console needs that the current FastAPI surface does not
serve. Every entry names the ground truth — the object that already holds the
value inside the process — so the gap is a transport gap rather than missing data.

The rule this document exists to protect: **where a value is unavailable, the
interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder,
or a disabled control.**

The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed) and a collapsible session sidebar, so gaps are
keyed `{stage}.{n}` plus `SB.{n}` for the sidebar, with `E1` — the one new
endpoint every stage would use — listed first. Re-keyed on 2026-09-26 to the
Evidence Verifier pipeline (`f27ac7e`); the 2026-09-16 ids are kept in brackets.

---

## Existing surface, for reference

| Method | Path | Returns |
|---|---|---|
| `POST` | `/research` | `202` `ResearchSessionResponse` (`api/app.py:159-188`; `max_iterations` is passed to the graph as `max_extra_passes`, `:183`) |
| `GET` | `/research/{id}/status` | `200` `ResearchSessionResponse` (`:190-202`) |
| `GET` | `/research/{id}/stream` | `200` `text/event-stream`, replayed from id 1 then live; ids restart at 1 per subscriber (`:204-236`, `api/events.py:14-27`) |
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`:238-263`) |
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`:265-269`) |

`ResearchSessionResponse` (`api/models.py:114-164`, assembled at
`api/sessions.py:73-128`), 17 fields: `session_id`, `status`, `current_agent`,
`iteration`, `started_at`, `finished_at`, `report_path`, `trace_url`, `errors`,
`evidence_path`, `quality_path`, `quality_contract_version`,
`semantic_review_status`, `semantic_review_score`, `duration_seconds`,
`coverage` (`required_targets`, `answered_targets`,
`missing_required_target_ids`, `not_found_target_ids`) and `evidence_counts`
(fifteen counts; `null` when the run left neither a composition nor a quality
snapshot, `runtime/outcome.py:525`). `status` is one of `running`, `completed`,
`max_iterations`, `incomplete`, `failed`. Not on the response: `quality_status`,
`query`, `max_iterations`/`max_extra_passes`, token usage, tool-call totals, any
report structure.

`ResearchRequest` accepts `query`, `max_iterations` (`int | None`, `ge=0`,
default → config), `output_format` and `config_overrides`; overrides are
validated against `ConfigSettings()`, so an unknown path is a `422` before a
session exists.

**Delivery model.** Events reach the stream once per node step: each
`stream_mode="values"` snapshot publishes the events that superstep appended
(`graph/orchestrator.py:312-346`), so a node's `graph.node.started`, everything
it emitted and its `graph.node.completed` arrive together when the node finishes.
Every running-stage rule in DESIGN.md §3.5 and §5.7 is written for that.

---

## Closed since 2026-09-16

Kept as a record, one line each.

| Old # | Gap | How it closed |
|---|---|---|
| 2.4 | `quality_status` | `completed` ⇔ route `report_accepted` (`graph/state.py:116`, `:322-337`); `semantic_review_status` splits the partial outcomes |
| 3.2 | quality snapshot | `coverage`, `evidence_counts`, `semantic_review_status`, `semantic_review_score` are on the response (`api/sessions.py:73-128`) |
| 3.3 | `evidence_path` | the path is served; the content moves to E1 |
| 3.5 | claim verdicts and confidence | obsolete: the pipeline has no claims; findings carry a verification status instead (E1) |
| 2.2 | tool-call counts | mostly closed: the verifier, writer and reviewer make no tool calls; `researcher.tool_call` misses only the planner's single budgeted call (`config.yaml:159`; `agents/planner.py:2999`), whose count is on `planner.planning.completed.tool_calls` |

---

## E1 — `GET /research/{id}/evidence`

The one endpoint every stage would use. JSON by default; `?format=markdown`
returns the published evidence log as `text/markdown`, which is what makes a
`Download evidence log` button appear on the report stage (today the button is
absent, never disabled). Subsumes the old 3.4 (citations with URLs), 3.6 (source
scores and bands) and the coverage id → question need. Ground truth:
`ReportComposition` and `ResearchState`.

| Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|
| **The findings, their verification and their sources** | The Evidence view (DESIGN.md §2 C) lists every finding with its status, target, snippet, source scores, Context Check and figures; coverage's not-found ids want their question text; the report's `How this was researched` link wants a destination | `composition.finding_labels`, `FindingVerification` (`utils/types.py:413-419`), `ScoredSource` (`:625-640`), `FigureResult` + `FigureContext` (`:368-400`), `NotFoundTarget` (`:1521-1532`), `RejectedDraftPoint` (`:1476-1486`), `composition.statement_passages` | The real app shows one muted line, `not served by the service yet`, where the list would be; the prototype renders the target state from a fixture in this shape | the JSON below |

```json
{"session_id": "…", "iteration": 0,
 "findings": [{"label": "F06", "status": "verified", "dropped_reason": null, "context_unchecked": false,
               "cited": true, "target_ids": ["T01"], "content": "…", "snippet": "…", "passage": null,
               "source": {"url": "…", "title": "…", "organisation": "…", "evaluation_status": "scored", "low_confidence": false,
                          "authority_score": 0.8, "recency_score": 0.7, "relevance_score": 0.9, "overall_score": 0.8},
               "figures": [{"value": "10.4 GW", "kept": true, "period": "2024", "scope": "…", "organisation": "…",
                            "attribution": "own", "kind": "actual", "release": "…", "evidence_words": "…",
                            "corrected": false, "dropped_reason": null, "reason": null}]}],
 "not_found": [{"target_id": "T02", "question": "…", "queries": ["…"], "pages_read": ["…"], "searched": true}],
 "refused": [{"where": "…", "text": "…", "reason": "…", "finding_labels": ["F03"]}]}
```

| Field group | Source type |
|---|---|
| finding `label`, `status`, `dropped_reason`, `context_unchecked` | `composition.finding_labels` (`types.py:1658`); `FindingVerification` (`types.py:413-419`) |
| `passage` | `composition.statement_passages` (`agents/report.py:1940`) |
| `source` scores and statuses | `ScoredSource` (`types.py:625-640`); `organisation` = the Context Check's organisation or the page owner |
| `figures[]` | `FigureResult` + `FigureContext` (`types.py:368-400`): `attribution` ∈ own \| relayed \| unattributed, `kind` ∈ actual \| forecast; `release` as the evidence log prints it (`report.py:1961-1962`); a quoted or dropped finding carries none |
| `not_found[]` | `NotFoundTarget` (`types.py:1521-1532`) |
| `refused[]` | `RejectedDraftPoint` (`types.py:1476-1486`) |
| `cited` | what the response's `evidence_counts.cited_findings` sums |

---

## Stage 1 — Idle (composer)

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 1.1 | **The session's own `query`** [1.1] | Neither `POST /research` nor `GET /status` echoes the question back. The sidebar row, the running header and the report header all display it, so all three would be empty after a reload. | `ResearchSession.query`, `ResearchState.original_question` | The client keeps its own copy at submit time, and the sidebar states that it is client-assembled | Add `query: str` to `ResearchSessionResponse` |
| 1.2 | **The session list `GET /research`** [1.2, 5.1] | There is no collection route, and `new_session_id()` mints the id server-side, so a client cannot even enumerate what it cannot already name. The sidebar is this gap's whole surface. | `SessionStore._sessions` | Client ledger of ids from `202` responses, disclosed in the sidebar footer | `GET /research?limit=` |
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
| 3.1 | **`max_iterations` echo, partially closed** [1.7] | The ceiling `P` in `pass p of P` comes from `graph.session.started.max_extra_passes` while the stream is open, but `/status` never carries it, so a reload after the stream closes falls back to the client's submitted budget. | `ResearchState.max_extra_passes`; on the stream at `graph.session.started` | Read it from the stream; fall back to the submitted value, used consistently | Include `max_extra_passes` on the response |
| 3.2 | **Token usage, absent on every stage** [2.1] | "Cost and usage" wants token totals and the client cannot derive them; they are absent while running and after the run alike. | `ResearchOutcome.token_usage`, from `TokenUsageMetric`s accumulated in the tracker | `Not recorded`, with the reason stated | A `usage` block on the status response, or cumulative totals on `graph.node.completed` metadata |
| 3.3 | **A terminal frame on the stream** [2.3] | The stream ends when the session reaches a terminal state, but the final frame is an ordinary event. The console learns *that* the run ended and must then call `GET /status` to learn *how* — and the stage transition depends on knowing how. | `graph.session.completed` carries `status`, `iteration`, `error_count`, `has_report`, but the store returns without synthesising a frame | On stream close, re-read `/status` and transition from it | One terminal `api.session.closed` frame carrying the final snapshot |
| 3.4 | **Event identity / `Last-Event-ID`** [2.6] | SSE `id` is per-subscriber and starts at 1, so it is a stream position rather than an event identity. A reconnect cannot ask for "everything after what I saw". | `ResearchSession.events` list index | Re-derive the running stage from the full replay — every rule is idempotent over events 1..k | A monotonic `sequence` on `ResearchEvent`, plus `Last-Event-ID` support |
| 3.5 | **Halting-type vocabulary** [2.5] | The failed stage headlines the halting type in plain words and the rail groups recoverable errors, but the client keeps its own copy of `HALTING_ERROR_TYPES` to know which is which. | `HALTING_ERROR_TYPES` in `graph/state.py:141-150` | Client copy, small and stable | `halting: bool` on `ResearchError`, or publish the enumerated set |
| 3.6 | **Shutdown while running** [2.7] | On cancellation the store sets `finished_at` and leaves `status` as `running`, then the stream ends. The console sees a closed stream with a non-terminal status. | Deliberate: *"cancellation stays cancellation"* | On stream close, re-read `/status`; a closed stream with `finished_at` set and `status == "running"` means the service stopped | The terminal frame from 3.3, or an explicit status |
| 3.7 | **Live per-event delivery** (new) | Events are published once per node step (`graph/orchestrator.py:312-346`), not as they happen, so the running stage's counters and active row move once per node; during the researcher — the longest stage — the research counters read `not yet` for its whole duration. | The events exist as they are appended to `ResearchState.events`; only publication is batched per superstep | The design's burst-safe rules (DESIGN.md §3.5, §5.7): the state after event *k* depends only on events 1..*k*, so bursts, ticks and replays paint the same screen | Publish each event as it is appended (stream the node's events, not the superstep snapshot); listed for the API work |

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
| 5.2 | **Reachable states** [4.2/4.3] | The two partial outcomes are now reproducible for free through the offline replay cases `review-unavailable` and `empty-but-clean`; the halted and configuration-error states still need a genuinely misconfigured service or a real failure. | The replay harness (`e2e_evaluation/replay.py`) for the partial states; nothing for a halt | Documented in `states.html` with the exact response body and the eight-frame event tail | A test-only seeded session, or the `GET /health` from 1.5 |

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
