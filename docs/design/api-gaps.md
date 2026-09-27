# Per-stage API gaps

What each stage of the console needs that the current FastAPI surface does not
serve. Every entry names the ground truth — the object that already holds the
value inside the process — so the gap is a transport gap rather than missing data.

The rule this document exists to protect: **where a value is unavailable, the
interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder,
or a disabled control.**

The console is one page with four stages (idle, running, report, failed) and a
collapsible session sidebar, so gaps are grouped by surface rather than by route.
Verified against the local checkout: five routes exist, `SessionStatus` is a
five-value literal, and `ResearchSessionResponse` carries nine fields.

---

## Existing surface, for reference

| Method | Path | Returns |
|---|---|---|
| `POST` | `/research` | `202` `ResearchSessionResponse` |
| `GET` | `/research/{id}/status` | `200` `ResearchSessionResponse` |
| `GET` | `/research/{id}/stream` | `200` `text/event-stream`, replayed from id 1 then live |
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` |
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` |

`ResearchSessionResponse`: `session_id`, `status`, `current_agent`, `iteration`,
`started_at`, `finished_at`, `report_path`, `trace_url`, `errors`.

`ResearchRequest` accepts `query`, `max_iterations`, `output_format` and
`config_overrides`. `config_overrides` is validated against `ConfigSettings()`, so
an unknown path is a `422` before a session exists.

---

## Stage 1 — Idle (composer)

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 1.1 | **The session's own `query`** | Neither `POST /research` nor `GET /status` echoes the question back. The sidebar row, the running header and the report header all display it, so all three would be empty after a reload. | `ResearchSession.query`, `ResearchState.original_question` | The client keeps its own copy at submit time, and the sidebar states that it is client-assembled | Add `query: str` to `ResearchSessionResponse` |
| 1.2 | **The session list** | There is no `GET /research`, and `new_session_id()` mints the id server-side, so a client cannot even enumerate what it cannot already name. | `SessionStore._sessions` | Client ledger of ids from `202` responses, disclosed in the sidebar footer | `GET /research?limit=` |
| 1.3 | **The effective model settings** | The composer sends `llm.model`, `llm.thinking_mode` and `llm.reasoning_effort` as overrides, then cannot show what was actually used — the snapshot has no config block, so the facts rail would be guessing. | `ConfigSettings.llm` after `apply_config_overrides` | Show the submitted values, labelled as submitted | A non-secret `config` block: `provider`, `model`, `thinking_mode`, `reasoning_effort` |
| 1.4 | **`llm.provider` override support** | The console offers DeepSeek models only, because there is no way to select a provider per request — even though `capabilities.py` registers a large OpenAI family. Presenting those models would produce a `422`. | `ProviderName` is a config value, not an override path | Stated in the composer popover: the configured provider is used as-is | Either add `llm.provider` to the override schema, or a `GET /providers` listing selectable providers and their models |
| 1.5 | **Model and effort vocabulary** | The composer mirrors provider capabilities by hand (two models, `high`/`max`, effort invalid when thinking is off). That copy drifts the moment `capabilities.py` changes. | `_CAPABILITIES` in `providers/capabilities.py` | Hand-mirrored, with the constraint enforced in the UI — the effort control is disabled when thinking is off | `GET /capabilities` returning `{provider, model, thinking_modes, enabled_efforts}[]` |
| 1.6 | **Readiness / preflight** | A configuration failure is only discoverable by submitting, so the operator loses the question they just typed. | `prepare_research_settings` is already a side-effect-free callable | None; the compose surface cannot warn ahead of time | `GET /health` → `{ready: bool, reasons: [enumerated]}`, reusing the `configuration_error` reasons |
| 1.7 | **`max_iterations` echo** | The `+` popover sends a refinement budget, and every surface that states a ceiling — the topbar chip, the pipeline's `pass n of m`, the report's pass count — reads it back from the client's own submitted value. The session response carries no `max_iterations`, so nothing confirms the server ran with the number the console is stating. A deployment that changed `graph.max_iterations` would leave every one of those surfaces quoting a ceiling the server never used. | `ResearchState.max_iterations` | The value the client submitted, used consistently everywhere a ceiling is shown | Include `max_iterations` on the response |

---

## Stage 2 — Running

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 2.1 | **Token totals during a run** | "Cost and usage" is a running-stage card, and token totals are the half of it the client cannot derive. They are currently absent until the run ends. | `ResearchOutcome.token_usage`, from `TokenUsageMetric`s accumulated in the tracker | `Not returned while running`, with the reason stated | Either a `usage` block on the status response, or cumulative totals on `graph.node.completed` metadata |
| 2.2 | **Tool-call counts** | Same card. The client counts `researcher.tool_call` events, which covers the researcher only — not the critic's spot-checks or the fact checker's per-claim loops. | `ResearchOutcome.tool_calls`, from `ToolMetric`s | Counted from events and labelled as such | `tool_calls: [{tool_name, calls, failures}]` on the response |
| 2.3 | **A terminal frame on the stream** | The stream ends when the session reaches a terminal state, but the final frame is an ordinary event. The console learns *that* the run ended and must then call `GET /status` to learn *how* — and the stage transition depends on knowing how. | `graph.session.completed` carries `status`, `iteration`, `error_count`, `has_report`, but the store returns without synthesising a frame | On stream close, re-read `/status` and transition from it | One terminal `api.session.closed` frame carrying the final snapshot |
| 2.4 | **`quality_status`** | The report stage's distinction between *Completed* and *Partially completed* for a session whose `status` is `completed` is invisible without it, and the running → report transition has the same problem one step earlier. | `ResearchOutcome.quality_status` → `accepted` \| `partial` \| `not yet quality-gated` | The console treats `completed` as partial unless the trace shows acceptance, and states the rule | `quality_status: str` on the response |
| 2.5 | **Error-type vocabulary** | The rail groups errors and the failed stage headlines the halting type, but the client keeps its own copy of `HALTING_ERROR_TYPES` to know which is which. | `HALTING_ERROR_TYPES` in `graph/state.py` | Client copy, small and stable | `halting: bool` on `ResearchError`, or publish the enumerated set |
| 2.6 | **Event identity past replay** | SSE `id` is per-subscriber and starts at 1, so it is a stream position rather than an event identity. A reconnect cannot ask for "everything after what I saw". | `ResearchSession.events` list index | Re-render the derived counters from the full replay, which is idempotent | A monotonic `sequence` on `ResearchEvent`, plus `Last-Event-ID` support |
| 2.7 | **Shutdown while running** | On cancellation the store sets `finished_at` and leaves `status` as `running`, then the stream ends. The console sees a closed stream with a non-terminal status. | Deliberate: *"cancellation stays cancellation"* | On stream close, re-read `/status`; a closed stream with `finished_at` set and `status == "running"` means the service stopped | The terminal frame from 2.3, or an explicit status |

---

## Stage 3 — Report

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 3.1 | **Report structure** | `GET /report` returns Markdown only. A table of contents, a verdict breakdown and per-claim source rows all require parsing text the server already holds as typed objects. | `ReportComposition` — `summary`, `sections`, `claims`, `sources`, `constraints`, `limitations`, `uncertainty_notes`, `rejected` | The body is rendered as prose; the quality panel is transcribed and labelled as transcribed | `GET /research/{id}/report?format=json` returning a safe projection |
| 3.2 | **Quality snapshot** | Coverage ratio, planned vs covered topics, unresolved topic ids, unique sources and findings, cited-source ratio, hard failures. The single most valuable missing field for a partial run. | `ReportQualitySnapshot` on `ResearchState.quality` | Transcribed from the report's own appendix, labelled as transcribed | `quality: ReportQualitySnapshot` on the response |
| 3.3 | **`evidence_path`** | The run publishes two artifacts per pass; only one is surfaced. | `ResearchOutcome.evidence_path` | Stated as a gap in the artifacts card — no link, and no disabled button standing in for one | Add `evidence_path`, plus `GET /research/{id}/evidence` |
| 3.4 | **Citations with URLs** | The report body carries numbered references; the URL and title mapping lives in the composition. | `Citation`, `ReportComposition.sources` | The citation table is transcribed from the report's own Markdown list | Included in 3.1 |
| 3.5 | **Claim verdicts and confidence** | A ledger view needs `verdict`, `confidence`, `contradictions`, `verification_evidence`, `insufficient_reason`. | `Claim` records in `verified_claims` | Read from the Markdown's verified-claims section, which carries confidence but not `insufficient_reason` | Included in 3.1 |
| 3.6 | **Source scores and bands** | The quality panel and any ledger need `authority`, `recency`, `relevance`, `overall`, `low_confidence`, `evaluation_status`. | `ScoredSource` records | Read from the evidence-ledger table — which is not reachable (3.3) | Included in 3.1 |
| 3.7 | **Body identity** | The console cannot show that the body it fetched is the body that was judged, rather than an earlier pass's. | `state.report` and `state.composition` are the same pass by construction | The console fetches once and caches | `report_sha256` and `composition_id` on the response |

---

## Stage 4 — Failed

| # | Needed | Why | Honest workaround | Suggested shape |
|---|---|---|---|---|
| 4.1 | **What survived the halt** | The stage reports findings, sources and claims collected before the halt. None of those counts are in the response; the console shows zeroes that are correct only for the halt-in-the-planner case it models. | Shows real zeroes for that case and labels them as counts read from state | A `collected` summary block, or the quality snapshot from 3.2 |
| 4.2 | **Reachable configuration failure** | Reproducing the configuration-error state requires a genuinely misconfigured service. | Documented in `states.html` with the exact response body | The `GET /health` from 1.6 |
| 4.3 | **Reachable failed and partial states** | A halt needs a real failure; `max_iterations` needs a real run exhausting its budget. Both are slow or expensive to produce on demand. | Documented from the real enumerated types and limitation strings | A test-only seeded session |

---

## Sidebar

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 5.1 | **Collection endpoint** | The entire surface. Without it the sidebar can only show what this browser started. | `SessionStore._sessions` | Client ledger, disclosed in the sidebar footer | `GET /research` |
| 5.2 | **A result summary per row** | Rows want `sources evaluated`, `claims checked`, and the terminal reason. A row currently carries the question and a running mark and nothing else, so **the list cannot report how anything ended** — completed, partial and failed rows are indistinguishable until opened. | `ReportQualitySnapshot`, `graph_route` reason | The row carries only `status === "running"`; every settled outcome is one click away on the session itself | Fold the quality snapshot and route reason into the list projection. This is the one gap whose absence is visible as a design decision rather than as missing text |
| 5.3 | **Durability** | Sessions are process-local; the list resets when the API restarts. | By design — *"durable queues, databases … remain out of scope"* | Stated on screen. The console never implies persistence it does not have | A JSON-lines session index beside `output/` would be a smaller change than a database |

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
