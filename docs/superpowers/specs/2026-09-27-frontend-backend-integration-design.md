# Front end to back end: the Next.js console on the FastAPI service (sub-project 3 + a minimal API slice)

**Status** design approved by the human (brainstorm, 2026-09-27); spec authored 2026-09-27 from the approved brief; independent review round 1 returned BLOCKED (2 P1, 9 P2, 15 P3) and every finding is applied below under the controller's rulings of 2026-09-27 (§2.1); awaiting the reviewer's round 2 · **Date** 2026-09-27 · **Branch** `feat/frontend-backend-integration`, cut from `origin/main` `4e10823` (PR #25: the design package updated to the Evidence Verifier workflow; the worktree's `HEAD` is `4e10823b`, confirmed with `git rev-parse`) · **Tree** `.worktrees/frontend-backend` (the only tree this work touches) · **Sub-project** 3 of 3 (the React/Next app wired to the API) plus the minimal slice of sub-project 2 (API gaps) the app needs.

**Sources of truth.** The human decisions in §2 and the controller's rulings in §2.1 are closed; nothing below reopens them. The design the app implements is `docs/design/` at `4e10823` (`DESIGN.md`, `api-gaps.md`, `prototype/index.html`, `prototype/states.html`, `reference/*.png`); the previous spec, `docs/superpowers/specs/2026-09-26-frontend-design-workflow-update-design.md`, records the running-stage, status, report and Evidence rules the design carries and is cited here as "the design spec". Every `path:line` below was read in the worktree on 2026-09-27; paths are relative to the worktree root, and a path with no directory prefix such as `app.py`, `sessions.py`, `models.py`, `events.py` is under `src/deep_research/api/`, `replay.py` and `replay_matrix.py` under `src/deep_research/e2e_evaluation/`, `report.py`, `quality.py`, `sources.py`, `report_reviewer.py` under `src/deep_research/agents/`, `errors.py` under `src/deep_research/runtime/`, `types.py` under `src/deep_research/utils/`, `index.html` under `docs/design/prototype/`.

Legend: [INFERENCE] = not observed in code, in an artifact or in a command run for this spec; each one names the test that proves or falsifies it. (I) = illustrative copy written for this spec, to be used as-is unless the plan finds a shorter true sentence.

---

## 1. Problem and scope

The design package is complete and the API exists, but nothing connects them: `docs/design/prototype/index.html` is a self-contained vanilla-JS prototype driven by scripted events and fixtures (`index.html:2861-2869`, `:3410-3471`), and `src/deep_research/api/` serves five routes with no start command, no session list, no `query` echo and no evidence endpoint (`app.py:159-292`; the package is `__init__.py`, `app.py`, `events.py`, `models.py`, `sessions.py`). A run of the real stack costs provider money and 70–110 minutes, so nothing end to end can be exercised for free today.

This spec adds the real console — a Next.js 16 App Router app in `web/` that renders the approved design from the live API — and the smallest API slice it needs: a start command with a **replay mode** that runs the real graph offline from the e2e replay harness, the E1 evidence endpoint, the `query` echo, a session list, the `/status.iteration` fix and a mode header. Replay mode is what makes the whole path (browser → Next proxy → FastAPI → graph → stream → report → Evidence) run end to end in seconds at no cost; one live run, off-peak, is the final proof.

| In scope (this spec) | Out of scope (own specs or never) |
|---|---|
| `web/` — the Next.js app: pages, proxy route handler, the ported CSS, the ported event core, components, tests | Authentication, multi-user, sharing, deployment or hosting |
| `src/deep_research/api/` — `__main__.py` (start command), `replay.py` (replay runner and case middleware), `evidence.py` (E1 builder), additions to `app.py`, `models.py`, `sessions.py` | The other `api-gaps.md` items: token usage (3.2), effective-settings echo (1.3), `/capabilities` (1.4), `/health` (1.5), the terminal stream frame (3.3), `Last-Event-ID` (3.4), live per-event delivery (3.7), report-body JSON (4.1), report hash (4.2) |
| `api/evidence.py` imports two private helpers from `agents/report.py` (`_finding_registry_pairs`, `_figure_value_text`) — the repository's own convention (`report_reviewer.py:62-75` imports the same `_finding_registry_pairs`) — and computes the cited set locally with the `quality.py` expression | **Any engine change beyond A6**: no file under `src/deep_research/agents/`, `graph/`, `runtime/` or `utils/` changes; nothing in the published Markdown changes |
| `pyproject.toml`: declare `uvicorn` (§3.7) | A second app, a `rewrites`-based proxy, CORS |
| Contract-test updates the additive `query` field forces: five `ResearchSessionResponse(...)` constructions in `tests/test_api/test_sessions.py` gain `query=` (§4.2 A4) | Any other change to existing tests |
| Docs: `README.md` "Run the app"; `api-gaps.md` closes E1, 1.1, 1.2 and records the iteration fix; `DESIGN.md` one-line note | Any change to `docs/design/` beyond those lines; the `:root` tokens; `reference/*.png` |
| Tests: pytest for every API change; Vitest for the event core and components; Playwright end to end against the API in replay mode; full-page visual checkpoints; one live run | A rendered event log, cancel/re-run, an embedded trace viewer, per-agent effort editing (`api-gaps.md:168-189`) |

The deliverable of this spec is the set of decisions in §2–§4 and the acceptance criteria in §5. The implementation plan written after it does the work.

---

## 2. Decisions (human-approved 2026-09-27)

| # | Decision |
|---|---|
| Q1 | App **and** a minimal API: same-origin proxy (no CORS), the E1 evidence endpoint, the `query` echo, `GET /research` (session list). The rest of `api-gaps.md` waits. |
| Q2 | A Next.js app in `web/`, two processes (FastAPI + Next); Next proxies `/api/*` to FastAPI. |
| Q3 | A **replay mode** for the API: an offline replay runner so the whole path runs end to end for free in seconds; live runs only off-peak. |
| Q4 | Port the prototype into React keeping its core: the CSS moved verbatim (same `:root` tokens and class names); the event core (`EVENT_HANDLERS`, `applyEvent`, `marksFor`, counters, pass number) ported unchanged into one pure TypeScript module fed by the real SSE stream; one React component per surface. |

Approved detail, recorded in the brief and specified in §4 (section per item):

| Approved item | Where specified |
|---|---|
| S1 — architecture and running: `web/` Next.js 16 App Router + TypeScript; `python -m deep_research.api --mode replay\|live` (default live; `--host/--port` default `127.0.0.1:8000`); `npm run dev` → `http://localhost:3000`; proxy = a route handler `app/api/[...path]/route.ts` passing requests and responses through as streams (not `rewrites`); API origin from `DEEP_RESEARCH_API_URL` (default `http://127.0.0.1:8000`); pages `/` and `/research/[session_id]` | §4.1 |
| S2 — API additions A1 start command, A2 replay runner (case from `--replay-case`, default `missing-target-triggers-one-extra-pass`; `X-Replay-Case` honoured only in replay mode; the session records the case's own question; pacing `--replay-delay-ms`, default 150), A3 E1, A4 `query`, A5 `GET /research?limit=`, A6 iteration fix; response header `X-Deep-Research-Mode: replay\|live` | §4.2 |
| S3 — app files, data flow, Markdown rendering rules, sidebar polling (on load, after each submit, every 5 s while any session runs), the "replay mode" topbar chip, dependencies (runtime `next`, `react`, `react-dom`, `react-markdown`, `remark-gfm`; dev `typescript`, `vitest`, `@testing-library/react`, `@playwright/test`; no Tailwind, no component library) | §4.3 |
| S4 — errors and missing data, governing rule: unavailable → muted words, never `0`, `—`, `null` or a disabled control; the app never retries a `POST`; live runs start only from the composer | §4.4 |
| S5 — test layers (pytest, Vitest, Testing Library, Playwright vs the API in replay mode), full-page visual captures of every stage at 1252 and 390 px compared with `docs/design/reference/`, the four-checkpoint cadence, layout assertions, ONE live run off-peak after the controller confirms the time with the human, the docs updates, the definition of done (all layers green; checkpoints passed; the live run completes through the app; a whole-branch review passes; a PR merged with a merge commit, no squash) | §4.5, §5 |

### 2.1 Rulings from review round 1 (controller, 2026-09-27; final)

| # | Ruling |
|---|---|
| R1 | An unknown `X-Replay-Case` fails the session through the **existing** enumerated reason `config_invalid` (`errors.py:17-19`); `runtime/errors.py` does not change. `configuration_error` raises `ValueError` for any reason not in `CONFIGURATION_HINTS` (`errors.py:71-84`, the guard at `:82-83`), so a new reason was never an option without an engine change. |
| R2 | No rename or extraction in `src/deep_research/agents/`. `api/evidence.py` imports `_finding_registry_pairs` and `_figure_value_text` from `agents/report.py` (repository convention, `report_reviewer.py:62-75`) and computes the cited set locally with the `quality.py:95-97`, `:155` expression. |
| R3 | The reviewer's refinement rulings (a) process-lifetime guards, (b) `uvicorn>=0.30`, (c) "nothing published" = `composition is None` / `report_evidence is None`, (d) the extra `web/` files and components, (f) api-gaps id 3.6 [old 2.7] stand as ruled; `@testing-library/dom` joins the dev dependencies. |
| R4 | "The controller views images at the two largest" = the controller personally views the images at checkpoints C3 and C4 (the two largest sets); every checkpoint still gets a full-height visual review at both widths (§4.5). |
| R5 | Every P2 and P3 of round 1 is applied: the `query` field's five contract-test updates; T-A2b measured at the store level and through the real server, not through `TestClient` (which buffers the body); T-E1's first Running render; T-E2/T-E3 transition recording; a closed, non-blocklisted dead port; a quoted venv interpreter path; the engine's `topic-NN-target-NN` ids; the full phone capture set; the pacing numbers; the tautological T-W1(a) replaced; six chip outcomes; a Node CSS check; the Vite transformer option; hand-written tree walking in the remark plugin; the temp root owned by `main`; cancellation of the pacer; `timeout_graceful_shutdown`; the abort claim marked and tested; the mid-run API-death rule; T-E6 on `extra-pass-finds-nothing`; replay `duration_seconds`; the proxy's full error envelope; E1 precision (cited equality scoped, last-wins source match, a non-stripping model base); citation drift; the OneDrive risk; client/server component boundaries; the Sunday-night live-run constraint. |

---

## 3. Ground truth (read in the worktree, 2026-09-27)

### 3.1 The API as it stands

| Fact | Where |
|---|---|
| `create_app(*, runner=run_research, config_path=DEFAULT_CONFIG_PATH, preflight=prepare_research_settings, tracker=None) -> FastAPI`; module-level `app = create_app()` | `app.py:130-136`, `:351` |
| One `SessionStore(runner=runner)` per app, closed by the lifespan; `app.state.session_store`, `app.state.api_tracker` | `app.py:146-155` |
| Every route hangs off one `APIRouter(dependencies=[Depends(_trace_request)])`; the dependency binds one observability span per request and invents the session id for `POST /research` (`request.path_params.get("session_id") or new_session_id()`) | `app.py:78-92`, `:157` |
| `POST /research` → preflight (`ResearchConfigurationError` → `ApiProblem(configuration_error, 500, reason)`) → `store.start(session_id, query, max_extra_passes=payload.max_iterations, output_format, config_overrides, config_path)` → 202 `ResearchSessionResponse` | `app.py:159-188` (preflight `:169-178`) |
| `GET /research/{id}/status` → 200 or 404 `session_not_found` | `app.py:190-202` |
| `GET /research/{id}/stream` → `StreamingResponse(text/event-stream)` with `Cache-Control: no-cache`, `X-Accel-Buffering: no`; ids restart at 1 per subscriber; 404 before the stream starts for an unknown id | `app.py:204-236` |
| `GET /research/{id}/report` → `text/markdown` from `session.outcome.report`; 409 `session_not_complete` while `outcome is None`; 409 `report_unavailable` when `outcome.report is None` | `app.py:238-263` (`:253-257`, `:258-262`) |
| `GET /research/{id}/trace` → `TraceResponse` | `app.py:265-292` |
| Safe errors: `_SAFE_MESSAGES` (five codes), `ApiProblem(code, status_code, reason)`, the 422 handler emitting `ValidationIssue{location, type}` only, the `ApiProblem` handler emitting `ApiErrorResponse{error: {code, message, reason}}` | `app.py:48-76`, `:296-348` |
| No CORS middleware, no other middleware, no `__main__.py` | `app.py` (grep: none); the `api/` listing |
| SSE frame: `id: n\nevent: {event_type}\ndata: {ResearchEvent JSON}\n\n`; `event_id >= 1` | `events.py:14-27` |
| `SessionStatus` = `running \| completed \| max_iterations \| incomplete \| failed` | `models.py:13-19` |
| `ApiModel`: `extra="forbid"`, `str_strip_whitespace`, `validate_default` — every response model inherits the stripping | `models.py:22-35` |
| `ResearchRequest`: `query` (min 1), `max_iterations: int \| None` (`ge=0`, default None), `output_format: Literal["markdown"]`, `config_overrides` validated against `ConfigSettings()` | `models.py:38-63` |
| `ResearchSessionResponse`, 17 fields, no `query` | `models.py:114-164` |
| `ValidationIssue{location, type}`, `ApiErrorBody{code, message, reason, issues}`, `ApiErrorResponse{error}` | `models.py:183-206` |
| `ResearchEvent{event_type, source, message, timestamp, metadata}`; `ResearchError{error_type, source, message, recoverable, timestamp, details}` | `types.py:1337-1351` |

### 3.2 Sessions, the runner contract, the outcome

| Fact | Where |
|---|---|
| `ResearchRunner = Callable[..., Awaitable[ResearchOutcome]]`; `TERMINAL_STATUSES = {completed, max_iterations, incomplete, failed}` | `sessions.py:29-33` |
| `ResearchSession` (dataclass, `slots=True`): `session_id, query, status, started_at, current_agent, iteration, finished_at, report_path, trace_url, errors, events, outcome, task, changed` | `sessions.py:36-59` |
| `publish(event)` deep-copies the event into `events`, sets `current_agent` on `graph.node.started`, and **copies `metadata.iteration` from every event** — including `researcher.tool_call`, whose `iteration` is the ReAct step index (`agents/researcher.py:2612`, per the design spec §3.2). This is the A6 defect | `sessions.py:61-70` (`:68-69`) |
| `outcome_response_fields(outcome)` contributes the finished run's fields, or `{}` while running | `sessions.py:73-128` |
| `SessionStore.start(...)` registers the session as `running` synchronously (`query=query`, `:162`), then `asyncio.create_task(self._run(...))` (`:167-176`); a duplicate id raises `ValueError` | `sessions.py:138-178` |
| `iter_events(session_id)` yields the retained events from index 0, then live ones; returns once every recorded event is yielded and the status is terminal (`:207-208`), or `finished_at` is set with the task done (cancellation) | `sessions.py:188-215` |
| `close()` cancels unfinished tasks and stamps `finished_at` while `status` stays `running` ("cancellation stays cancellation") | `sessions.py:217-232` |
| `_run(...)` awaits `self._runner(question=query, session_id=..., max_extra_passes=..., output_format=..., config_overrides=..., config_path=..., event_handler=session.publish)`; `ResearchConfigurationError` → `_record_failure(api.research.configuration_error, details={"reason": error.reason})` (`:261-267`); any other exception → `api.research.failed` with `details={"exception_type": ...}` (`:270-275`); `asyncio.CancelledError` propagates; success folds `outcome.status`, `outcome.state.iteration` (`:279`), `report_path`, `trace_url`, `errors`, `outcome` (`:278-285`); `finally` sets `finished_at`, clears `current_agent`, wakes subscribers | `sessions.py:234-289` |
| `_record_failure` sets `status="failed"`, publishes one `ResearchEvent(event_type=error_type, source="api")` and appends a non-recoverable `ResearchError` | `sessions.py:292-317` |
| `ResearchOutcome`: `session_id, question, status, state, trace_url, report_path, token_usage, tool_calls, evidence_path, quality_path, duration_seconds, quality_contract_version, request_budget_snapshots`; properties `report` (= `state.report`), `errors`, `failed`, `composition` (= `state.composition`), `semantic_review_status/score`, `coverage`, `evidence_counts`; `duration_seconds` is the span the recorded events' own timestamps cover, never a clock | `runtime/outcome.py:324-416` (`:350-356`), `:467`, `:511` |
| `ResearchState.report_evidence` — the evidence ledger Markdown "authoritative in state whether or not any file exists"; `evidence_path` set only by the terminal finalizer; `composition: ReportComposition \| None` | `types.py:1846-1852`, `:1853-1858`, `:1868-1873` |

### 3.3 The engine entry point and the replay harness

| Fact | Where |
|---|---|
| `run_research(question, *, session_id, resume_session_id, config_path, max_extra_passes, output_format, config_overrides, runtime_builder=build_runtime, event_handler, request_budget_handler) -> ResearchOutcome` | `src/deep_research/main.py:189-201` |
| It **always** loads strict settings first — `prepare_research_settings(config_path=…, output_format=…, config_overrides=…)` — then `await runtime_builder(settings, session_id=effective_session_id)` | `main.py:271-280` |
| Strict loading requires the credential *names* of the configured stack (`TAVILY_API_KEY`; `DEEPSEEK_API_KEY` for the default chat provider) and reads `.env` beside the config with `override=False`, so a value already in the process environment wins over `.env` | `src/deep_research/utils/config.py:646-649`, `:728`, `:743-746` |
| `CONFIGURATION_HINTS` enumerates the only reasons a configuration error may carry (`config_file_missing`, `config_invalid`, `missing_secrets`, `provider_unconfigured`, `memory_unavailable`, `agents_misconfigured`, `blank_session_id`, `unsupported_output_format`, `no_question`, `no_checkpoint`, `question_and_resume`, `history_unavailable`); `configuration_error(*, reason, message)` raises `ValueError("unknown configuration reason: …")` for any other reason | `errors.py:13-58`, `:71-84` (the guard at `:82-83`; `config_invalid` at `:17-19`) |
| The orchestrator hands events to `event_handler` synchronously on the loop thread: the events one superstep appended, in a tight loop, when the superstep's snapshot arrives (one burst per node step), then `graph.session.completed` once more at the end | `src/deep_research/graph/orchestrator.py:333-346`, `:406-407` |
| `offline_credentials()` sets `DEEPSEEK_API_KEY` and `TAVILY_API_KEY` to placeholders and `LANGSMITH_TRACING=false`, restoring the previous values on exit | `replay.py:3527-3554` |
| `network_denied()` patches, process-wide, `socket.socket` (a subclass whose `connect`/`connect_ex` raise `AssertionError`), `socket.create_connection` and `socket.getaddrinfo` (raises "network name resolution attempted"), permits `socket.socketpair` for asyncio's self-pipe on Windows, records attempts, restores on exit. It is a `contextmanager`, so overlapping (non-LIFO) exits would restore the wrong objects | `replay.py:1755-1815` (`:1787-1790`, `:1792-1801`) |
| `run_replay_scenario(scenario, *, root, session_id, ...)` runs through the CLI and `asyncio.run(run_research(question=…, session_id=…, config_path=str(production_config_path()), max_extra_passes=scenario.max_extra_passes, output_format=…, config_overrides=…, runtime_builder=builder, event_handler=…))` — it cannot be called inside a running loop — inside `offline_credentials()` **only**; the harness's tests wrap it in `network_denied()` from outside | `replay.py:3557-3643` (`:3591-3628`, `:3634-3635`); `tests/test_e2e_evaluation/test_real_agents.py:89-93`, `tests/test_e2e_evaluation/test_runner.py:358-361` |
| Its `builder(current, *, session_id)` = `replay_settings(scenario, root=root, base=current)` then `await build_replay_runtime(scenario, root=root, session_id=session_id, settings=effective, long_term=…, packet_dump=…)`, returning `replay.runtime` | `replay.py:3591-3611` |
| `replay_settings` redirects `memory.long_term.persist_directory` → `root/memory`, `memory.procedural.strategies_path` → `root/procedural.json`, `output.directory` → `root/documents`, and applies the scenario's `agent_overrides` | `replay.py:1817-1861` |
| `build_replay_runtime` builds the production runtime with the scripted completer, search and HTTP doubles, tracing off, the frozen `replay_clock`, an in-memory long-term collection; it **patches `runtime.assembly.compile_research_graph` for the duration of `build_runtime`** and restores it in `finally` — a process-global mutation | `replay.py:1956-2066` (`:2031-2050`) |
| `production_config_path()` resolves the shipped `config.yaml` from the working directory or the repository root | `replay.py:3510-3525` |
| `ReplayScenario.case_id`, `question`, `max_extra_passes` (default 1) | `replay.py:440-451` |
| `REPLAY_CASE_IDS` (35 entries, confirmed by importing it with the venv interpreter); `scenario_by_id(case_id)` raises `KeyError("unknown replay case …")` for an unknown id | `replay_matrix.py:3571-3589` |
| Case questions (probed offline): `missing-target-triggers-one-extra-pass` → "What was the Acme widget adoption rate in the United States in 2024?"; `extra-pass-finds-nothing` → "What is the corroborated Acme widget adoption rate for 2024?"; `scoped-redraft-after-a-named-defect` → "What were the Acme widget adoption rate and export volume in the United States in 2024?"; `review-unavailable` → "What do the published measures say about the Acme widget in 2024?"; `empty-but-clean` → "What did the Acme widget measures show in 2024?"; all five carry `max_extra_passes=1` | `replay_matrix.py:1617-1620`, `:739-741`, `:2886-2889`, `:1122-1124`, `:1228-1230` |
| The five cases replayed offline for this spec (`run_replay_scenario` under `offline_credentials()` + `network_denied()`, zero connection attempts): `missing-target-triggers-one-extra-pass` → 72 events, `completed`, iteration 1, labels `F02 F03 F04 F05 F01`, no not-found, 0 dropped; `scoped-redraft-after-a-named-defect` → 49 events, `completed`, iteration 0; `review-unavailable` → 43 events, `incomplete`, iteration 0; `empty-but-clean` → 67 events, `max_iterations`, iteration 1, not-found ids `topic-01-target-01`, `topic-02-target-01`, `topic-03-target-01`; `extra-pass-finds-nothing` → 65 events, `completed`, iteration 1, one unlabelled (dropped) finding that the log labels `X01`, not-found `topic-01-target-01`, `dropped_findings == 1`. The graph itself runs in 0.1–0.35 s and `duration_seconds` is 0.09–0.22 s | measured 2026-09-27 with the venv interpreter |
| The repository imports private helpers across modules: `report_reviewer.py` imports `_figure_label_for`, `_finding_registry_pairs`, `_point_labels`, `_row_label` from `agents/report.py` and calls `_finding_registry_pairs` at `:701`, `:785`; ruff selects only `E`, `F`, `I` | `report_reviewer.py:62-75`; `pyproject.toml:37-38` |
| `latest_scored_sources(sources)` keeps the last append-ordered `ScoredSource` per normalized URL | `sources.py:60-63` |

### 3.4 The engine objects behind E1

| Field group of the E1 JSON | Source | Where |
|---|---|---|
| pairing of each finding with its printed label, in order; unlabelled findings print `X01`, `X02`… | `_finding_registry_pairs(composition)` (label queue per `finding_fingerprint`), used by `render_finding_log` | `report.py:1612-1635`, `:1921-1927`; `src/deep_research/agents/identity.py:79` |
| `status`, `dropped_reason`, `context_unchecked`; the log prints `not checked` when `verification is None` | `Finding.verification: FindingVerification \| None`; `FindingStatus`, `FindingDropReason` | `types.py:440-550` (`:549`), `:413-419`, `:356-365`; `report.py:1928-1935` |
| `passage` | `composition.statement_passages.get(finding_id)` | `types.py:1669`; `report.py:1939` |
| `cited` | `finding_fingerprint(finding)` ∈ `{i for p in points if p.statement for i in p.statement.finding_ids}` with `points = [*composition.summary, *(p for s in composition.sections for p in s.points)]` — the expression `evidence_counts.cited_findings` sums | `quality.py:95-97`, `:155` |
| `source` scores and statuses | `ScoredSource{url, title, authority_score, recency_score, relevance_score, overall_score, rationale, evaluation_status, low_confidence, ...}` from `composition.sources`; `SourceEvaluationStatus` = `scored \| unscored_cap \| unscored_provider \| unscored_missing`; URL canonicalisation `normalize_source_url`; the page owner `publisher_identity(url)`; last-wins per URL `latest_scored_sources` | `types.py:625-660`, `:57-62`, `:1652`; `sources.py:28`, `:60-63`, `:81` |
| `figures[]` | `FigureResult{figure, matched, context, evidence_words, corrected, dropped_reason, reason}` with `kept = dropped_reason is None` and `context` set on every kept figure; `FigureContext{period, scope, subject, attribution, organisation, kind, period_resolved_from}`; the value text with its unit `_figure_value_text(figure)`; `release` = `release_text(finding) or row_release.get(finding_id)` where `row_release` maps fact-row finding ids to `row.release` | `types.py:368-411`; `report.py:1697-1702`, `:1912-1913`, `:1962`; `src/deep_research/agents/verified_facts.py:1122` |
| `not_found[]` | `NotFoundTarget{target_id, question, queries, pages_read, searched}` from `composition.not_found`; target ids are the plan's own `topic-NN-target-NN` (measured above) | `types.py:1521-1532`, `:1656` |
| `refused[]` | `RejectedDraftPoint{where, text, reason, finding_labels}` from `composition.rejected_points` | `types.py:1472-1486`, `:1693` |
| `session_id`, `iteration` | `composition.session_id`, `composition.iteration` | `types.py:1637-1638` |
| The Markdown form | `render_finding_log(composition)`; the run keeps its output as `state.report_evidence` | `report.py:1902-2005`; `types.py:1846` |

The prototype's fixture `EVIDENCE.default` (`index.html:3410-3471`) carries exactly these keys — `label, status, dropped_reason, context_unchecked, cited, target_ids, content, snippet, passage, source{url, title, organisation, evaluation_status, low_confidence, authority_score, recency_score, relevance_score, overall_score}, figures[{value, kept, period, scope, organisation, attribution, kind, release, evidence_words, corrected, dropped_reason, reason}]`, `not_found[{target_id, question, queries, pages_read, searched}]`, `refused[{where, text, reason, finding_labels}]` — with `status: null` on the never-verified finding `F05`, `figures: []` on the quoted `F03` and the dropped `F04`. Two fixture conventions are the prototype's, not the engine's: its target ids read `T01`… where the engine's read `topic-01-target-01`, and its dropped finding is labelled `F04` where the engine labels a finding the report never registered `X01` (`report.py:1921-1927`).

### 3.5 The prototype

| Anchor | Where in `index.html` |
|---|---|
| `<style>` block; `:root{…}` tokens (the only literal colours); `</style>` | `:6`, `:11-55`, `:1138` |
| `<script>` IIFE | `:1572-3885` |
| Event core: `emptyCounters`, `newRunState(passes)`, `nextRow`, `rearm`, `plural`, `EVENT_HANDLERS` (keyed by event type, each reading only `md`), `applyEvent(run, ev)` (reads `ev.type`, `ev.metadata`), `marksFor(run, activeId)`, `COUNTER_ROWS` | `:2885-2889`, `:2890-2904`, `:2905-2908`, `:2911-2919`, `:2920`, `:2925-3019`, `:3020-3023`, `:3025-3030`, `:3033-3054` |
| `STAGES` (seven rows: id, label, meta), `AGENT_ORDER`, `ARCS` (`extra_pass`: reviewer → researcher; `redraft`: reviewer → writer), `BLURB` | `:2456-2464`, `:2465`, `:2501-2504`, `:3095-3103` |
| Format helpers: `STATUS` (label + dot class per API status), `passNumber`, `passTotal`, `passText`, `fmtScore`, `notFoundClause`, `statusNote`, `chipHTML`, `fmtDur`, `fmtSeconds`, `fmtClock`, `fmtElapsed` | `:1704-1710`, `:1715`, `:1721`, `:1725`, `:1730`, `:1734`, `:1747-1756`, `:1759`, `:1765`, `:1772`, `:1776`, `:1782` |
| Failed stage: `HALT_HEADLINES` (eight plain-words headlines, incl. the two API types), `populateFailed` (first non-recoverable error; halting row = `run.openNode`; Publishing `skipped` when status is `failed`; counters with `not reached`), `HALTED_EVENTS` (eight literal frames) | `:3708-3717`, `:3718-3751`, `:2829-2838` |
| Evidence view: `EVIDENCE.default`, `EV_FILTERS` (7 chips), `PILL_TEXT`, `VERIFICATION_TEXT`, `evidenceRows`, `renderChips`, `renderEvList`, `renderEvDetail`, `setView` | `:3410-3471`, `:3474-3486`, `:3483`, `:3485`, `:3488-3512`, `:3513`, `:3529`, `:3598`, `:3683-3691` |
| Meters: `meterClass` (`v > 0.8` ok, `>= 0.4` warn, else danger), `paintMeters`; report rail `renderEvidenceCounts`, `populateReport` | `:3267-3270`, `:3271`, `:3299`, `:3316` |
| DOM regions: sidebar (footer sentence "Sessions are held in the service process's memory, so this list is the client's own record of them."), topbar, stage-idle with the composer (`#prompt`, `#settingsPop`, `#composerError`), stage-submitted, stage-running (`#runNow`, `#runningBlurb`, `#runLoopTag`, `#runProgressLabel`, `#spineWrap[data-loop][data-arc]` + `#spine`, counters `#runCountersPass`, `#runCounters`), stage-report (head bar `#reportMeta`, `#segView` Report \| Evidence, `Download Report`, `#report-h`, `#reportOpts`; body card `.prose`; rail cards Review, Coverage, Evidence, Session facts, Cost and usage, Errors; Evidence view `#evChips`, `#evList`, `#evEmpty`, `#evDetail`), stage-failed (`#failedMeta`, `#failed-h`, `#failedType`, `#failedMessage`, facts, `#spineFailed`, `#failedCounters`) | `:1145-1168` (`:1166`), `:1175-1185`, `:1188-1263`, `:1277-1285`, `:1288-1329`, `:1347-1510`, `:1513-1565` |
| Spine rows carry `data-stage` (node id) and `data-state` (`pending \| active \| done \| loop \| skipped`); `#spineWrap` carries `data-loop` (`off \| flowing \| settled`) and `data-arc`; the arc SVG (`.loop-base`, `.loop-flow`, `.loop-head`) is drawn from the measured positions of the two rows | `:2565-2574`, `:2625-2634`, `:2553-2563`, `:2515-2545` |
| The prototype's session object is not the API's: it carries `q`, `passes` (= 1 + `max_extra_passes`), `review{status, score}`, `finished`, `durationSeconds`; `passNumber()` is the one place the zero-based offset lives | `:1644-1699`, `:1711-1718` |
| The working copy has CRLF line endings (`core.autocrlf=true`); a byte comparison with an LF file must normalise line endings first | `git config core.autocrlf`; reviewer's count of 3,887 CRs |

### 3.6 Design rules the app implements (pointers)

| Rule | Where |
|---|---|
| Five stages selected by server state (Submitted held ~2.2 s, `:156`); the composer exists on stage 1 only; the pipeline owns the running stage; `/` and `/research/[session_id]` are the same UI | `docs/design/DESIGN.md:147-198` |
| Status → chip label, dot, note; rules 1–7 (`failed` ≠ no report; `completed` ⇔ `report_accepted`; unavailable never becomes a value; no `waiting`) | `DESIGN.md:904-963` |
| Derived stage display: rows from the stream, not `current_agent`; Reviewing inert after a loop decision; Publishing `skipped` on `status == "failed"` | `DESIGN.md:965-992` |
| `iteration` on every surface: pass from `graph.node.started` / `graph.extra_pass.started` only; `/status.iteration` after the stream closes; `P = 1 + max_extra_passes` from `graph.session.started`, else the budget the session was created with, else no clause (`:1013-1018`) | `DESIGN.md:994-1036` |
| Running-stage rules (active row, header, loop tag, counters with `not yet`, spine states, arcs), the six chip outcomes and the failed stage, composer and settings strip, report rendering rules, rail cards, Evidence view | the design spec §4.1–§4.4 |
| Delivery model: events reach the stream once per node step | `api-gaps.md:41-46`; `orchestrator.py:333-346` |
| Gaps this spec closes: E1 (`api-gaps.md:69-104`), 1.1 `query` (`:111`), 1.2 `GET /research` (`:112`); gaps it leaves: 1.3–1.5, 3.1–3.7, 4.1–4.2, 5.1–5.2, SB.1–SB.2; shutdown while running = 3.6 [old 2.7] (`:136`); durability SB.2 (`:164`) | `api-gaps.md` |

### 3.7 Tooling and conventions

| Fact | Where / how |
|---|---|
| Node `v24.13.1`, npm `11.8.0`, `next` latest on npm `16.3.6` (re-run for this spec); the venv runs Python `3.12.14` with `uvicorn 0.52.1`, `fastapi 0.141.1`, `starlette 1.3.1` (imported for this spec) | `node --version`, `npm --version`, `npm view next version`; the venv interpreter |
| Next.js 16.3.6 route handlers: `GET/POST/...` exports taking `(request: NextRequest, { params })` where **`params` is a `Promise`**; `GET` handlers are dynamic by default since v15; a `Response` whose body is a `ReadableStream` streams. Pages receive `params` as a `Promise` too | `https://nextjs.org/docs/app/api-reference/file-conventions/route` (version 16.3.6, read 2026-09-27) |
| `uvicorn` is importable in the venv (a transitive dependency) but **not declared** in `pyproject.toml`; `fastapi>=0.115`, `httpx>=0.27`, `python-dotenv>=1` are | `pyproject.toml:10-25` |
| pytest: `pythonpath=["src"]`, `testpaths=["tests"]`, `addopts="-m 'not live'"`, marker `live`; `pytest-asyncio>=0.23` is a dev dependency | `pyproject.toml:29-31`, `:40-47` |
| API tests drive `create_app(runner=ScriptedRunner(), preflight=valid_preflight)` through `fastapi.testclient.TestClient` and poll `/status` to a terminal state; the only whole-body equality is on the trace response; five tests construct `ResearchSessionResponse(...)` directly without `query` | `tests/test_api/test_app.py:9-38`; `tests/test_api/test_stream_and_artifacts.py:164-170`; `tests/test_api/test_sessions.py:328`, `:339`, `:370`, `:397`, `:436` |
| Starlette 1.3.1's `TestClient` runs the app to completion and buffers the whole response body before returning it, so SSE frames read through it arrive in one block after the session ends — it cannot observe pacing | `.venv/Lib/site-packages/starlette/testclient.py:296`, `:342-353`, `:367` (read by the reviewer; the spec relies on it only to choose T-A2b's harness) |
| The package's `__main__` convention: `build_parser()` + `main(argv=None, *, runner=...) -> int`, `raise SystemExit(main())` | `src/deep_research/__main__.py`; `src/deep_research/cli.py:170`, `:1348` |
| Render script viewports `W = 1252, H = 853`, `PHONE_W = 390, PHONE_H = 844`; captures are viewport-sized except `06-states` and `09-running-extra-pass` (1252×1300) | `scripts/render_design_reference.mjs:27-28`, `:219`, `:237` |
| The Fetch standard's bad-port blocklist makes `fetch("http://127.0.0.1:1/…")` fail with `bad port` before any connection; a closed ordinary port fails with `ECONNREFUSED` | `node -e` probe by the reviewer, 2026-09-27 |
| `bash file.sh` on this workstation resolves to WSL without a distribution — never used; Windows commands run through `cmd`/PowerShell or Node; `where python` lists three interpreters and every path contains spaces | brief; reviewer's `where python` |
| The worktree lives under OneDrive (`…/OneDrive/Documents/Python Scripts/…`) | the path |
| The existing suite has 4,689 tests (brief-reported; not re-run for this spec) | brief |

---

## 4. Design

### 4.1 S1 — Architecture and running

**Two processes, one origin for the browser.**

| Process | Command | Listens on | Owns |
|---|---|---|---|
| API | `python -m deep_research.api --mode replay\|live [--host 127.0.0.1] [--port 8000] [--replay-case ID] [--replay-delay-ms 150]` (§4.2 A1) | `127.0.0.1:8000` | sessions, the graph, the stream, the artifacts |
| App | `npm run dev` in `web/` (`next dev`), or `npm run build && npm run start` | `http://localhost:3000` | pages, the proxy, rendering |

The browser talks only to `localhost:3000`. Every API call the app makes goes to `/api/...` on its own origin; the route handler `web/app/api/[...path]/route.ts` forwards it to `${DEEP_RESEARCH_API_URL}/...` (default `http://127.0.0.1:8000`, read from the Next server's environment at request time) and passes the response back **as a stream**. No CORS is configured anywhere, and `next.config.ts` has no `rewrites`: a rewrite would let Next's own response handling sit between the SSE body and the browser, and the stream must reach the browser frame by frame, uncompressed and unbuffered ([INFERENCE] that a rewrite would buffer or compress; the route handler makes the question moot and §4.5 T-W4 and T-E2 prove the frames arrive live).

**Proxy contract** (`web/app/api/[...path]/route.ts`, `export const dynamic = "force-dynamic"`, `runtime = "nodejs"`; exports `GET` and `POST` only — Next answers other methods itself):

| Aspect | Rule |
|---|---|
| Target URL | `${DEEP_RESEARCH_API_URL}/${(await params).path.join("/")}${request.nextUrl.search}` — path segments and the query string (`?limit=`, `?format=markdown`) pass through unchanged |
| Request headers forwarded | `accept`, `content-type`, `x-replay-case`; nothing else (no cookies, no `host`) |
| Request body | `GET`: none. `POST`: `await request.text()` forwarded as-is (bodies are small JSON; no request streaming, so no `duplex` option) |
| Upstream fetch | `fetch(target, { method, headers, body, cache: "no-store", signal: request.signal })`. The handler forwards its own `request.signal`, which T-W4 proves (a handler called with an aborted signal closes the upstream socket). Whether Next 16's server aborts `request.signal` when the browser disconnects is [INFERENCE] and stays one: its impact is low — a stale upstream read lasts only until that session's stream ends — and, if wanted, it is checked by hand against `next start` (disconnect a browser mid-stream and watch the API log) |
| Response | `new Response(upstream.body, { status: upstream.status, headers })` — the upstream body `ReadableStream` is handed through without reading it; status passes through (202, 200, 404, 409, 422, 500) |
| Response headers forwarded | `content-type`, `cache-control`, `x-accel-buffering`, `x-deep-research-mode`; nothing else (`content-length`, `transfer-encoding`, `connection`, `date`, `server` are dropped and recomputed by Next) |
| Upstream unreachable (`fetch` rejects: connection refused, DNS, reset before headers) | `502` JSON `{"error": {"code": "api_unreachable", "message": "Research service not reachable.", "reason": null, "issues": [], "target": "<DEEP_RESEARCH_API_URL>"}}` with `content-type: application/json` — the full `ApiErrorResponse` envelope (`models.py:194-206`) plus `target`, which the S4 banner prints |
| Compression | `next.config.ts` sets `compress: false`: the app is local and an SSE body must never wait in a gzip buffer ([INFERENCE] that Next's compression would affect `text/event-stream`; disabling it removes the question) |

**Pages.**

| Route | Stage(s) | Rule |
|---|---|---|
| `/` | Idle | the composer; nothing else (DESIGN.md §3) |
| `/research/[session_id]` | Submitted, Running, Report (with the Evidence view), Failed, and the S4 states "not in memory" and "service stopped" | the stage is derived from `GET /status` and the stream (§4.3 stage table); a reload or a shared link returns to the same session; the URL carries no other state |

**Environment.**

| Variable | Read by | Default |
|---|---|---|
| `DEEP_RESEARCH_API_URL` | the Next server (route handler) | `http://127.0.0.1:8000` |
| `PORT` (Next's own) / `--port` | `next dev` / `next start` | `3000` |

Nothing in the Python package imports or depends on `web/`; `web/` depends on the API only over HTTP.

### 4.2 S2 — API additions

#### A1 — Start command `python -m deep_research.api`

New module `src/deep_research/api/__main__.py`, following the package convention (`src/deep_research/__main__.py`; `cli.py:170`, `:1348`):

```python
def build_parser() -> argparse.ArgumentParser: ...
def build_app(args: argparse.Namespace, *, replay_root: Path | None = None) -> FastAPI: ...
def main(argv: Sequence[str] | None = None, *, serve: Callable[..., None] = uvicorn.run) -> int: ...

if __name__ == "__main__":
    raise SystemExit(main())
```

| Flag | Default | Rule |
|---|---|---|
| `--mode {live,replay}` | `live` | `live`: `create_app()` exactly as the module-level `app` today (`config_path=DEFAULT_CONFIG_PATH`, i.e. `config.yaml` in the working directory, `src/deep_research/main.py:47`). `replay`: the composition below |
| `--host HOST` | `127.0.0.1` | passed to uvicorn; in replay mode resolved to a numeric address first (below) |
| `--port PORT` | `8000` | integer 0–65535 |
| `--replay-case ID` | `missing-target-triggers-one-extra-pass` | must be in `REPLAY_CASE_IDS` (`replay_matrix.py:3571`); given with `--mode live` → parser error |
| `--replay-delay-ms N` | `150` | integer ≥ 0; `0` releases events as they arrive; given with `--mode live` → parser error |

Parser errors exit 2 with argparse's usage text (unknown mode or case, negative delay, out-of-range port, replay flags in live mode). Otherwise `main` serves until interrupted and returns 0. `serve` is injectable so the tests never bind a port. `main` in live mode: `serve(create_app(), host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=5)` — the same `serve(...)` call and the same 5 s bound as replay mode; only the guards, the temp root and the host resolution are replay-only.

`build_app` composes exactly one app — no second `FastAPI`. In replay mode it **receives** the root (`replay_root` is required in replay mode; a missing root is a `ValueError`) and never creates one:

```python
runner = ReplayRunner(default_case=args.replay_case, delay=args.replay_delay_ms / 1000, root=replay_root)
app = create_app(runner=runner, config_path=str(production_config_path()), mode="replay")
app.add_middleware(ReplayCaseMiddleware, default_case=args.replay_case)
```

`main` in replay mode, in this order: create `root = Path(tempfile.mkdtemp(prefix="deep-research-replay-"))` (so the process that deletes it is the one that made it); resolve `--host` to a numeric address with `socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]` **before** any guard is entered; build the app with `replay_root=root`; then

```python
with offline_credentials(), network_denied():
    serve(app, host=numeric_host, port=args.port, log_level="info", timeout_graceful_shutdown=5)
```

and `shutil.rmtree(root, ignore_errors=True)` in a `finally`. `timeout_graceful_shutdown=5` bounds uvicorn's wait for in-flight responses — without it, uvicorn 0.52.1 waits for every open SSE subscriber, so Ctrl+C with a stream open would block until the run ended (`uvicorn/server.py:272-310`, read by the reviewer). A forced kill (Playwright's `taskkill /T /F`, `Popen.terminate()`) skips the `finally`, so every test that spawns the server points `TEMP`/`TMP` at a pytest `tmp_path` (T-A1b) or a Playwright-owned directory (§4.5), and nothing leaks into the user's temp folder. One startup line names the mode, the case, the delay and the root. `uvicorn>=0.30` is added to `[project].dependencies` in `pyproject.toml` (it is present in the venv only transitively, §3.7).

**Why the two guards wrap the whole server process, not each run.** Three facts from §3.3 decide it:

1. `POST /research` runs the strict configuration load *before* the runner is called (`app.py:169-178` → `prepare_research_settings` → `load_config(strict=True)`), and the strict load refuses without the credential names (`config.py:743-746`). Per-run `offline_credentials()` inside the runner would leave every replay-mode `POST` answering `500 configuration_error / missing_secrets` on a machine without real keys — the opposite of what replay mode is for.
2. Both guards are plain `contextmanager`s that save and restore process globals (`replay.py:1804-1815`, `:3541-3554`). Two sessions running at once would enter and exit them in non-LIFO order and leave the process with the wrong `socket.socket` or a placeholder key stuck in the environment.
3. `load_dotenv(..., override=False)` (`config.py:728`) means the placeholders set once at startup win over any `.env` beside `config.yaml`; with the socket guard active for the process's lifetime, the replay-mode server cannot dial a provider even on a machine that has real keys — the guarantee that makes replay runs free.

Serving under `network_denied()`: the guard refuses `connect`, `connect_ex`, `create_connection` and `getaddrinfo` and permits `socketpair`; binding, listening and accepting call none of the four, and a numeric host needs no name resolution — which is why the host is resolved before the guard. The reviewer's round-1 probe served `create_app` under both guards with uvicorn 0.52.1 on a numeric host (POST 202, 72 SSE frames, `completed`, zero connection attempts); T-A1b (§4.5) makes that proof permanent with the real start command.

#### A2 — Replay runner (replay mode only)

New module `src/deep_research/api/replay.py`:

```python
REPLAY_CASE_HEADER = "x-replay-case"
requested_case: ContextVar[str | None] = ContextVar("deep_research_replay_case", default=None)

@dataclass
class ReplayRunner:
    default_case: str
    delay: float          # seconds between released events; 0.0 releases them as they arrive
    root: Path            # each session runs under root / session_id

    async def __call__(
        self, *, question: str, session_id: str, max_extra_passes: int | None,
        output_format: str, config_overrides: Mapping[str, JsonValue], config_path: str,
        event_handler: ProgressHandler | None,
    ) -> ResearchOutcome: ...

class ReplayCaseMiddleware:   # pure ASGI; no BaseHTTPMiddleware
    def __init__(self, app: ASGIApp, *, default_case: str) -> None: ...
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: ...
```

How the runner satisfies the store's contract (`sessions.py:234-289`):

| Keyword the store passes | What the runner does with it |
|---|---|
| `question` | **not used**: the runner passes `scenario.question` to `run_research` itself, because the scripted completer requires the case's own question in the planner's request (`replay.py:867`, `:973`, `:1018`, `:1028` `_require(text, self.scenario.question, …)`) and a store-level caller has no middleware in front of it. On the HTTP path the middleware also rewrites the request body (below), so `session.query` and the question that ran agree |
| `session_id` | passed through; also names the session's directory `root / session_id` |
| `max_extra_passes` | **not used**: the scenario's `max_extra_passes` is passed instead, as `run_replay_scenario` does (`replay.py:3618`), because each case's script is written for its declared ceiling |
| `output_format`, `config_overrides`, `config_path` | passed through; `config_path` is the shipped `config.yaml` (`production_config_path()`), so `run_research`'s strict load (`main.py:271-276`) sees the same file the harness uses |
| `event_handler` | wrapped by the pacer (below); `None` is passed through as `None` |
| return value | the `ResearchOutcome` `run_research` returns, **after** every queued event has been published — so `_run` folds the terminal status (`sessions.py:278-285`) only when the stream's tail is already in `session.events` |
| exceptions | `run_research`'s own propagate unchanged. An unknown case raises `configuration_error(reason="config_invalid", message=f"No replay case named {case_id!r}")` — `config_invalid` is an enumerated reason (`errors.py:17-19`) and `configuration_error` accepts only enumerated reasons (`errors.py:71-84`, guard `:82-83`, R1) — which `_run` records as `api.research.configuration_error` with `details.reason == "config_invalid"` (`sessions.py:261-267`). The runner also logs one warning naming the unknown case and `REPLAY_CASE_IDS` (server log only; never in a response) |

`__call__`, in order:

1. `case_id = requested_case.get() or self.default_case`; `scenario = scenario_by_id(case_id)`, converting `KeyError` to the configuration error above.
2. `session_root = self.root / session_id`, created with `mkdir(parents=True, exist_ok=True)`; every artifact of this session (`documents/`, `procedural.json`) lives there, so concurrent sessions share no file (`replay_settings` places all three redirected paths under `root`, `replay.py:1839-1854`).
3. `builder(current, *, session_id)`: `effective = replay_settings(scenario, root=session_root, base=current)`; `async with self._build_lock: replay = await build_replay_runtime(scenario, root=session_root, session_id=session_id, settings=effective)`; return `replay.runtime`. The lock exists because `build_replay_runtime` patches `runtime.assembly.compile_research_graph` for the duration of `build_runtime` (`replay.py:2031-2050`); two builds interleaving would let one capture the other's agents. The lock is one `asyncio.Lock` per runner (since Python 3.10 a lock binds to the running loop on first use; the venv runs 3.12.14) and guards the build only — runs otherwise proceed concurrently (the reviewer ran three at once; T-A2d keeps two).
4. The pacer: `queue: asyncio.Queue[ResearchEvent | None]`; the handler given to `run_research` is `lambda event: queue.put_nowait(event)`; a drain task started before the run does `while (event := await queue.get()) is not None: event_handler(event); await asyncio.sleep(self.delay)` (no sleep when `delay == 0`).
5. `outcome = await run_research(question=scenario.question, session_id=session_id, config_path=config_path, max_extra_passes=scenario.max_extra_passes, output_format=output_format, config_overrides=config_overrides, runtime_builder=builder, event_handler=paced_handler)`.
6. On return or on any exception other than cancellation: `queue.put_nowait(None)`; `await drain_task` — the tail is published whether the run returned or raised, then the outcome is returned (or the exception re-raised). On `asyncio.CancelledError` (the store's `close()` cancelling the session task at shutdown, `sessions.py:217-232`): `drain_task.cancel()`, await its cancellation, re-raise — shutdown does not wait `N × delay`, and nothing is published after the cancellation.

**Loop and thread safety.** Everything runs on the server's one event loop. The graph calls the handler synchronously on the loop thread (`orchestrator.py:333-346`, `:406-407`), so `put_nowait` from the handler is a same-loop call; the drain task is a task on that loop, so `session.publish` — which touches the session's lists and sets an `asyncio.Event` (`sessions.py:63-70`) — is only ever called from the loop thread, as it is in live mode. No thread, no `run_in_executor`, no second loop. The pacer changes *when* each event is published, never the order or the content: the store's replay-from-1 stream (`sessions.py:188-215`) and the design's burst-safe rules see the same event list a live run would produce, released one every `delay` seconds instead of one burst per node step — which is what makes the running stage watchable. The cases emit 43–72 events and the graph itself runs in 0.1–0.35 s (§3.3), so at 150 ms a replay session is visible for about 6.5–11 s (the default case: 72 events, ≈ 11 s).

**`X-Replay-Case` and the recorded question** — `ReplayCaseMiddleware`, installed only in replay mode (A1), outermost:

| Step | Rule |
|---|---|
| Scope | only `http` requests with method `POST` and path `/research`; everything else passes through untouched |
| Case | `case_id = headers.get(REPLAY_CASE_HEADER) or default_case`; `requested_case.set(case_id)` **before** calling downstream. Starlette's error and exception middleware, FastAPI's router and the endpoint run in the same task, and `SessionStore.start` creates the run's task from that context (`sessions.py:167-176`), so `asyncio.create_task`'s context copy carries the variable into `ReplayRunner.__call__` (the reviewer's probe confirmed it; T-A2c keeps it proven) |
| Body | when `case_id in REPLAY_CASE_IDS`: read the whole request body from `receive`, parse it as JSON; if it is an object, set `body["query"] = scenario_by_id(case_id).question` (scenarios cached per id), re-encode, replace the `content-length` header in `scope["headers"]`, and hand downstream a `receive` that yields the new body once. A body that is not JSON or not an object passes through untouched (FastAPI's own 422 follows). An unknown case rewrites nothing (the runner fails the session, above) |
| Effect | `ResearchRequest.query` is the case's question, so `session.query` (`sessions.py:162`), the `query` echo (A4), the list (A5) and the report's `# question` all show what actually ran — "Acme widget", never the typed text. The header is honoured only in replay mode because the middleware exists only there; in live mode the header is ignored (the proxy forwards it, the live app has no reader) |

The scenario's question replaces the operator's on purpose (approved: "the session records the case's own question, so the UI shows what ran"); the composer's typed text is discarded by the server, not by the app.

#### A3 — E1 `GET /research/{session_id}/evidence`

| Aspect | Rule |
|---|---|
| Query parameter | `format: Literal["json", "markdown"] = "json"`; any other value → the existing 422 `validation_error` (`app.py:296-322`) |
| 404 | unknown id → `session_not_found`, as every other route |
| 409 while running | `session.outcome is None` → `session_not_complete` (the same code `/report` uses, `app.py:253-257`) |
| 409 nothing published | JSON: `outcome.composition is None`; Markdown: `outcome.state.report_evidence is None` → new code `evidence_unavailable`, `_SAFE_MESSAGES["evidence_unavailable"] = "Research session finished without an evidence log."` (R3(c): `report`, `report_evidence` and `composition` are written in the same state update, so this mirrors `/report`'s `state.report is None` rule) |
| 200 `format=json` | `EvidenceResponse` built by `build_evidence_response(outcome)` in the new module `src/deep_research/api/evidence.py`, from the run's final `composition` |
| 200 `format=markdown` | `Response(outcome.state.report_evidence, media_type="text/markdown")` — the evidence log as `render_finding_log` composed it, the same source of truth `/report` uses for `state.report` |

The JSON is exactly the `api-gaps.md` E1 shape (`api-gaps.md:79-93`), which matches the prototype fixture key for key (§3.4):

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

(`T01`/`T02` are the api-gaps illustration; the engine's ids read `topic-01-target-01`, §3.4.)

Models added to `models.py`. They extend a new `EvidenceModel(BaseModel)` base with `extra="forbid"` and `validate_default=True` but **without** `str_strip_whitespace`, because `snippet`, `passage`, `content` and `evidence_words` are verbatim page text and must reach the client as recorded (`ApiModel` strips every string, `models.py:31-35`):

```python
class EvidenceSourceResponse(EvidenceModel):
    url: str; title: str; organisation: str
    evaluation_status: SourceEvaluationStatus | None = None   # None: no ScoredSource for this URL
    low_confidence: bool = False
    authority_score: float | None = None; recency_score: float | None = None
    relevance_score: float | None = None; overall_score: float | None = None

class EvidenceFigureResponse(EvidenceModel):
    value: str; kept: bool
    period: str | None; scope: str | None; organisation: str | None
    attribution: FigureAttribution | None; kind: FigureKind | None; release: str | None
    evidence_words: str | None; corrected: bool
    dropped_reason: FigureDropReason | None; reason: str | None

class EvidenceFindingResponse(EvidenceModel):
    label: str; status: FindingStatus | None; dropped_reason: FindingDropReason | None
    context_unchecked: bool; cited: bool; target_ids: list[str]
    content: str; snippet: str | None; passage: str | None
    source: EvidenceSourceResponse; figures: list[EvidenceFigureResponse]

class EvidenceNotFoundResponse(EvidenceModel):
    target_id: str; question: str; queries: list[str]; pages_read: list[str]; searched: bool

class EvidenceRefusedResponse(EvidenceModel):
    where: str; text: str; reason: str; finding_labels: list[str]

class EvidenceResponse(EvidenceModel):
    session_id: str; iteration: int
    findings: list[EvidenceFindingResponse]; not_found: list[EvidenceNotFoundResponse]; refused: list[EvidenceRefusedResponse]
```

Derivation, one row per field. `api/evidence.py` reuses the evidence log's own helpers by importing them — `from deep_research.agents.report import _finding_registry_pairs, _figure_value_text` — exactly as `agents/report_reviewer.py:62-75` does today (R2); it computes the cited set locally with the `quality.py` expression. No file under `agents/` changes.

| Field | Value | Where the log does the same |
|---|---|---|
| findings order and `label` | `for label, finding in _finding_registry_pairs(composition)`; a `None` label becomes `X01`, `X02`… in encounter order (engine truth: `extra-pass-finds-nothing`'s dropped finding is `X01`, §3.3) | `report.py:1612-1635`, `:1921-1927` |
| `status`, `dropped_reason`, `context_unchecked` | `v = finding.verification`: `v.status` / `v.dropped_reason` / `v.context_unchecked`; `None` / `None` / `False` when `v is None` (the log's `not checked`) | `report.py:1928-1935` |
| `cited` | `finding_fingerprint(finding) in cited`, where `cited = {i for p in points if p.statement for i in p.statement.finding_ids}` and `points = [*composition.summary, *(p for s in composition.sections for p in s.points)]` — the same two lines `compute_report_quality` sums | `quality.py:95-97`, `:155` |
| `target_ids`, `content`, `snippet` | the finding's own fields | `types.py:447`, `:441`, `:535` |
| `passage` | `composition.statement_passages.get(finding_id)` | `report.py:1939` |
| `source.url`, `source.title` | `finding.source_url`, `finding.source_title` | `report.py:1936` |
| `source` scores, `evaluation_status`, `low_confidence` | the `ScoredSource` for `normalize_source_url(finding.source_url)` in `{normalize_source_url(s.url): s for s in latest_scored_sources(composition.sources)}` — when several scored sources share a URL the last append-ordered one wins, as `latest_scored_sources` defines (`sources.py:60-63`); when none exists every score is `null`, `evaluation_status` is `null`, `low_confidence` is `false` (nothing was judged — "null is not a value", DESIGN.md §4 rule 4) | `types.py:625-640`; `sources.py:28`, `:60-63` |
| `source.organisation` | the first kept figure's `context.organisation`; otherwise `publisher_identity(finding.source_url)` — "the Context Check's organisation or the page owner" (`api-gaps.md:99`) | `types.py:376`; `sources.py:81` |
| `figures[]` | one per `FigureResult` in `v.figure_results` (none when `v is None`; a quoted finding or one dropped before the Context Check therefore carries none; an `all_figures_dropped` finding carries every figure with its `dropped_reason`, `types.py:421-437`) | `report.py:1954-1968` |
| `figures[].value` | `_figure_value_text(r.figure)` — the value with its unit unless the value already spells one | `report.py:1697-1702` |
| `figures[].kept` | `r.kept` (`dropped_reason is None`) | `types.py:403-404` |
| `figures[].period`, `scope`, `organisation`, `attribution`, `kind` | from `r.context` when kept; all `null` when dropped (a dropped figure has no confirmed context) | `types.py:368-380`; `report.py:1957-1964` |
| `figures[].release` | when kept: `release_text(finding) or row_release.get(finding_id)`, `row_release = {fid: row.release for row in composition.fact_rows for fid in (row.finding_id, *row.duplicate_finding_ids)}`; `null` when dropped | `report.py:1912-1913`, `:1962`; `verified_facts.py:1122` |
| `figures[].evidence_words`, `corrected`, `dropped_reason`, `reason` | `r.evidence_words`, `r.corrected`, `r.dropped_reason`, `r.reason` | `report.py:1965-1968` |
| `not_found[]` | `composition.not_found`, field for field | `types.py:1521-1532` |
| `refused[]` | `composition.rejected_points`, field for field | `types.py:1472-1486` |
| `session_id`, `iteration` | `composition.session_id`, `composition.iteration` | `types.py:1637-1638` |

`sum(f.cited for f in findings)` equals `evidence_counts.cited_findings` whenever every finding has its own fingerprint; two revision editions of one page share a fingerprint (`report.py:1613-1627`) and would both read `cited` for one counted id, so T-A3a asserts the equality on its one case and does not claim it in general.

#### A4 — `query` echo

`ResearchSessionResponse` gains `query: str = Field(min_length=1)` after `session_id` (`models.py:114-164`); `_session_response` passes `query=session.query` (`app.py:114-127`; the store already records it, `sessions.py:162`). Additive on the wire: no existing field changes and no client that reads the response breaks. It is a required field, so the five tests that construct `ResearchSessionResponse(...)` directly — `tests/test_api/test_sessions.py:328`, `:339`, `:370`, `:397`, `:436` — gain `query="…"` as a contract-test update (in scope, §1); nothing else in the existing suite constructs the model by hand (`tests/test_api/fakes.py` builds outcomes and events, not responses).

#### A5 — `GET /research?limit=`

| Aspect | Rule |
|---|---|
| Route | `@router.get("/research", response_model=SessionListResponse)` on the same router (so the observability span is bound as for every route) |
| Query parameter | `limit: int = Query(default=20, ge=1, le=200)`; out of range → 422 |
| Response | `{"sessions": [ResearchSessionResponse, …]}` — each item is exactly the `/status` response of that session, `query` included |
| Order | newest first: `started_at` descending; equal timestamps keep reverse insertion order (`SessionStore.list_sessions(limit)` sorts a stable reversed copy of `self._sessions.values()`) |
| Memory | process-local, as every session is (`sessions.py:1-8`); the list is empty after a restart, and the sidebar footer says so (§4.3) |

`SessionListResponse(ApiModel)` with `sessions: list[ResearchSessionResponse]` is added to `models.py`.

#### A6 — `/status.iteration` fix

`ResearchSession.publish` (`sessions.py:61-70`) updates `self.iteration` only for graph events:

```python
if event.event_type.startswith("graph.") and isinstance(iteration, int):
    self.iteration = iteration
```

`researcher.tool_call` (whose `iteration` is the ReAct step index; the reviewer's probe saw values 1–7 mid-run) no longer moves the session's pass; `graph.node.started`, `graph.node.completed`, `graph.extra_pass.started`, `graph.route.decided`, `graph.session.completed` still do, and `_run` still stamps `outcome.state.iteration` at the end (`sessions.py:279`). The existing tests publish `graph.node.started` with an iteration and keep passing (`tests/test_api/fakes.py:174-180`, `test_sessions.py:561`).

#### The mode header

`create_app` gains `mode: Literal["live", "replay"] = "live"`, records `app.state.mode = mode`, and installs one pure-ASGI `ModeHeaderMiddleware` (in `app.py`) that adds `X-Deep-Research-Mode: <mode>` to every `http.response.start` message — so it is on 202s, on the SSE stream's headers, on the Markdown responses and on every 4xx/5xx the exception handlers produce (they run inside Starlette's `ExceptionMiddleware`, inside this one). `ReplayCaseMiddleware` (A2) is added afterwards by `build_app`, so it is outermost; it never produces a response of its own, so the header is never missing. The module-level `app = create_app()` (`app.py:351`) stays `live`.

#### Files touched on the Python side

| File | Change |
|---|---|
| `src/deep_research/api/__main__.py` | new: `build_parser`, `build_app`, `main` (A1) |
| `src/deep_research/api/replay.py` | new: `REPLAY_CASE_HEADER`, `requested_case`, `ReplayRunner`, `ReplayCaseMiddleware` (A2) |
| `src/deep_research/api/evidence.py` | new: `build_evidence_response(outcome) -> EvidenceResponse` (A3); imports `_finding_registry_pairs`, `_figure_value_text` from `agents/report.py`, `release_text` from `agents/verified_facts.py`, `normalize_source_url`, `latest_scored_sources`, `publisher_identity` from `agents/sources.py`, `finding_fingerprint` from `agents/identity.py` |
| `src/deep_research/api/app.py` | `create_app(mode=…)`, `ModeHeaderMiddleware`, `GET /research`, `GET /research/{id}/evidence`, `_SAFE_MESSAGES["evidence_unavailable"]`, `_session_response(query=…)` |
| `src/deep_research/api/models.py` | `query`; `SessionListResponse`; `EvidenceModel` and the six `Evidence*Response` models |
| `src/deep_research/api/sessions.py` | `publish` (A6); `SessionStore.list_sessions(limit)` |
| `tests/test_api/test_sessions.py` | the five `ResearchSessionResponse(...)` constructions gain `query=` (A4) |
| `pyproject.toml` | `uvicorn>=0.30` in `dependencies` |

No file under `src/deep_research/agents/`, `graph/`, `runtime/` or `utils/` changes (R2).

### 4.3 S3 — App structure and data flow

#### Files

```
web/
  package.json  next.config.ts  tsconfig.json  vitest.config.ts  playwright.config.ts  README.md
  app/
    layout.tsx                 server component: the shell — .app grid, <Sidebar>, <main> with <Topbar> and the stage area (index.html:1141-1186)
    globals.css                index.html:7-1137 verbatim — same :root tokens (11-55), same class names, same media queries
    page.tsx                   "/"  — the idle stage: renders <Composer> only
    research/[id]/page.tsx     "/research/[session_id]" — `const { id } = await params;` then <SessionScreen sessionId={id} />
    api/[...path]/route.ts     the streaming proxy (§4.1)
  lib/
    api.ts                     typed client mirroring api/models.py (+ query, list, E1); ApiError, ApiUnreachableError; mode capture
    stream.ts                  the SSE reader (fetch + ReadableStream), frame parser, backoff schedule
    run-state.ts               the prototype's event core, ported unchanged (pure)
    format.ts                  statusNote, passText, fmtScore, notFoundClause and the other display helpers (pure)
    session-store.ts           client-only memory: submissions in flight (for the Submitted beat), submitted settings per session
  components/                  every file here is a client component ("use client")
    SessionScreen.tsx          owns one session: status, the stream, `run`, the stage derivation, the S4 states
    Sidebar.tsx  Topbar.tsx  StatusChip.tsx  ModeChip.tsx  Composer.tsx  SettingsPopover.tsx  SettingsStrip.tsx
    SubmittedStage.tsx  RunningPipeline.tsx  Spine.tsx  Counters.tsx  ReportStage.tsx  ReportBody.tsx  ReportRail.tsx
    EvidenceView.tsx  FailedStage.tsx  ServiceBanner.tsx  SessionNotFound.tsx
  test/                        Vitest: lib/*.test.ts, components/*.test.tsx, proxy.test.ts; fixtures/events/*.json
  e2e/                         Playwright: *.spec.ts; visual.spec.ts (the full-page captures)
  scripts/capture-replay-events.mjs   records a replay session's frames and final /status into test/fixtures/events/<case>.json
  scripts/check-css-verbatim.mjs      the AC10 check: index.html:7-1137 == globals.css lines 1-1131 after CRLF normalisation
```

**Server and client boundaries.** `app/layout.tsx` and the two pages are server components; the pages do nothing but unwrap `params` (a `Promise` in Next 15+, §3.7) and render a client component. Every file under `components/` is a client component (`"use client"`): they hold state, run effects (`fetch`, the stream, `sessionStorage`, `localStorage`, measuring the spine for the arc) and read the mode context. `lib/*` is plain TypeScript with no React import except `lib/session-store.ts`'s optional `sessionStorage` access, guarded by `typeof window`.

`lib/session-store.ts` is the one file beyond the brief's list: it holds the two facts only the submitting tab knows (that a session was just submitted, and what settings it was submitted with), so `page.tsx` and `SessionScreen` share them without a global. It is a module with a `Map` plus `sessionStorage` persistence under `dr.console.submission.<session_id>` (the prototype's own storage prefix, `index.html:1575`).

**Dependencies.** Runtime: `next` (16.x), `react`, `react-dom`, `react-markdown`, `remark-gfm`. Dev: `typescript`, `vitest`, `@testing-library/react`, `@testing-library/dom` (a required peer of `@testing-library/react` 16.x, R3), `@playwright/test`, plus what those need to run: `@types/react`, `@types/react-dom`, `@types/node`, `jsdom` (Testing Library's DOM under Vitest). No Tailwind, no component library, no CSS framework, no state library, no `unist-util-visit`: the CSS is the prototype's, the state is React's, and the one remark plugin walks the tree with its own recursion.

`next.config.ts`: `compress: false` (§4.1), `reactStrictMode: true`, nothing else. `vitest.config.ts`: `environment: "jsdom"`, `include: ["test/**/*.test.ts?(x)"]`, and the automatic JSX runtime set through the installed Vite major's transformer option — `esbuild: { jsx: "automatic" }` under Vite 6/7, the `oxc` transformer's JSX option under Vite 8, which no longer depends on esbuild [INFERENCE: the exact Vite 8 option name; the plan reads it from the installed Vite's documentation] — because Next's `tsconfig` keeps `jsx: "preserve"`; the proxy and lib tests set `// @vitest-environment node` where they need Node's `fetch`.

#### Data flow

1. **Submit** (`/`). The composer builds the request the design specifies (the design spec §4.3: `query`, `max_iterations` = the stepper, `output_format: "markdown"`, `config_overrides.llm.model`, `.llm.thinking_mode`, `.output.directory` when set) and calls `startResearch` once. On `202` it records `{ submittedAt, settings }` for the new `session_id` in `lib/session-store.ts`, refreshes the sidebar list, and `router.push("/research/{session_id}")`. On any error it stays on `/` and renders the S4 text under the composer; the question stays in the box. A `POST` is never retried.
2. **Open a session** (`SessionScreen`). `getStatus(id)` first; on 200, `readStream(streamUrl(id))`. The stream is opened for **every** known session, finished ones included: a finished session's stream replays all its events in one burst and closes (`sessions.py:200-208`), which is how the page learns `P` (`graph.session.started.max_extra_passes`), the pass it ended on, the halting row of a failed run and the counters — from the run's own events, never from constants.
3. **Each frame** → `applyEvent(run, toRunEvent(event))` on a `RunState` kept in a `useRef`, then a version bump (`useState`) so React re-renders; the handlers mutate in place exactly as the prototype's do. The Running stage renders from `run`; the topbar chip renders `Running · passText(view)` with `view.iteration = run.pass - 1` and `view.passes = run.maxPasses` while the stream is open.
4. **Stream end** → `getStatus(id)` again and act on it (stage table below). While the status is `running` and `finished_at` is `null`, or on a failed connection, reconnect on the backoff schedule; every reconnect starts from `newRunState(P)` and replays from event 1, so the screen after a reconnect is identical to an uninterrupted one (the burst-safe rules of the design spec §4.1).
5. **Finished** (any terminal status but `failed`): `getReport(id)` and `getEvidence(id)` once each, cached in component state; the Report stage renders the Markdown with the rules below and the rail from the status response; the Evidence view renders the E1 JSON. `Download Report` links `reportUrl(id)`; `Download evidence log` links `evidenceMarkdownUrl(id)` and is rendered only once `getEvidence` returned 200 (both documents are composed by the same writer pass, `types.py:1868-1873`; a 409 on one is a 409 on the other).
6. **Failed**: the Failed stage renders from the status response's first non-recoverable error and from `run` (`failedMarks`, counters with `not reached`).
7. **Sidebar**: `listSessions(50)` on layout mount, after every successful submit, and every 5 s while any listed session has `status === "running"` (the interval stops when none does; it restarts when a submit succeeds). Rows are grouped by the local date of `started_at` (Today, Yesterday, Earlier); a row shows the question and the running mark only (the prototype's rule, `index.html:1871-1880`); the active session is `aria-current`. Footer (I): *Sessions are held in the service process's memory; this list empties when the service restarts.* (`api-gaps.md` SB.2, `:164`).
8. **Mode chip**: every `lib/api.ts` call and the stream reader report the response's `X-Deep-Research-Mode`; a small React context (`ApiModeProvider`, a client component rendered by `app/layout.tsx`) keeps the last value; `ModeChip` renders a muted `replay mode` chip in the topbar when it is `replay`, nothing when `live` or unknown.

**When the API dies during a run** (the two rules that could collide, resolved): on `/research/[id]`, an `ApiUnreachableError` from `getStatus` or from the stream shows the S4 banner **and** the page keeps trying on the backoff ladder (`getStatus`, then the stream, at 1, 2, 4, 8, 16 s then every 30 s); **Retry** on the banner forces an attempt immediately and resets the ladder; the banner disappears on the first success. The ladder never issues a `POST`. On `/`, there is no ladder: only **Retry** repeats the failed read. So the stage table's "retries on Retry only" applies to the idle page; on a session page the ladder wins and Retry is a shortcut.

#### Stage derivation (`SessionScreen`)

| Observation | Stage / state |
|---|---|
| `getStatus` → 404 | **Not in memory** (S4): the sentence and a `New research` action; no stream is opened |
| `getStatus` → `ApiUnreachableError` | **Service unreachable** banner (S4) above whatever stage was last rendered; the ladder above keeps trying; **Retry** forces an attempt |
| `status == "running"`, `finished_at == null`, and `lib/session-store.ts` says this tab submitted it less than 2,200 ms ago | **Submitted** for the remainder of the 2,200 ms (the question and the settings strip, nothing else — `DESIGN.md:156`, `:161-168`), then **Running** |
| `status == "running"`, `finished_at == null` otherwise | **Running** (a reload lands here directly) |
| `status == "running"`, `finished_at != null` | **Service stopped** (S4, `api-gaps.md:136`): the sentence, the frozen pipeline from `run`, `New research`; no reconnect |
| `status == "failed"` | **Failed** |
| `status ∈ {completed, max_iterations, incomplete}` | **Report** (view `report` by default; `evidence` on the toggle or the evidence-log link) |

`P` for `pass p of P`, in this order: `graph.session.started.max_extra_passes + 1` from the stream; else the submitted `extraPasses + 1` from `lib/session-store.ts`; else `null`, and `passText` drops the clause (`DESIGN.md:1013-1018`). The pass number while running is `run.pass`; after the stream has closed it is `passNumber(status.iteration)`, which agrees with `run.pass` once A6 is in place.

**The settings strip** shows the five chips (`model · thinking · effort · extra passes · out`) from the submission recorded in `lib/session-store.ts`. When no record exists (another tab, a shared link, a restart of the app), it shows one muted line (I) *settings as submitted: not recorded* and, once the stream has delivered `graph.session.started`, a single `extra passes {P − 1}` chip — the one value the server confirms.

**Replay-mode durations.** `duration_seconds` is the span the recorded events' own timestamps cover (`outcome.py:350-356`), and the graph runs a replay in well under a second (§3.3: 0.09–0.22 s), so in replay mode the report head bar's `fmtSeconds(duration_seconds)` reads `0m 00s` even though the paced run was watchable for about 11 s. That is the truthful value, not a defect (C3 in §4.5 records it); the elapsed counter on the Running stage is wall time from `started_at` and does show the paced duration.

#### `lib/stream.ts` — the reader

```ts
export interface SseFrame { id: string | null; event: string | null; data: string; }
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string };
export interface StreamCallbacks { onOpen(mode: ApiMode | null): void; onEvent(event: ResearchEvent, id: number): void; }
export type StreamEnd = { kind: "ended" } | { kind: "failed"; status: number | null; error: unknown };
export function readStream(url: string, callbacks: StreamCallbacks, signal: AbortSignal): Promise<StreamEnd>;
export function* backoffDelaysMs(): Generator<number>;   // 1000, 2000, 4000, 8000, 16000, then 30000 forever
```

| Rule | Detail |
|---|---|
| Transport | `fetch(url, { headers: { accept: "text/event-stream" }, cache: "no-store", signal })`; the body is read with `TextDecoder` and fed to `parseSse`, which splits complete frames on a blank line (`\n\n` or `\r\n\r\n`), reads `id:`, `event:`, `data:` lines, joins multi-line `data`, ignores comment lines, and returns the unterminated remainder |
| Frame → event | `JSON.parse(frame.data)` as `ResearchEvent`; `id` parsed as an integer (the API's per-subscriber counter, `events.py:23-26`); `frame.event === event.event_type` by construction |
| Clean end | the body ends → `{ kind: "ended" }`; the page re-reads `/status` (flow step 4) |
| Failure | non-2xx (`status`), a network error, or the proxy's 502 → `{ kind: "failed", … }` |
| Reconnect (the page) | on `failed`, or on `ended` while `/status` still says `running` with `finished_at == null`: wait the next `backoffDelaysMs()` value, reset `run`, read again; stop once `/status` is terminal, or reads `running` with `finished_at` set, or the page unmounts (the `AbortController`) |
| Why not `EventSource` | it reconnects on its own schedule after *every* close, including the clean close every finished session's stream ends with (`sessions.py:207-208`) — it would reopen a finished stream forever; its timing cannot be the approved 1 → 30 s ladder; and it exposes neither the response headers (the mode chip) nor the difference between a clean end and a drop |

The proxy's upstream `fetch` (Node's `undici`) applies an idle body timeout of 300 s by default [INFERENCE: not measured here]; a live run's researcher node can be silent longer than that (events arrive once per node step, `api-gaps.md:41-46`). If that timeout fires, the browser sees a drop, reconnects on the ladder, and rebuilds the identical screen from the replay — the path S4 already requires, at a cost of one extra replay every five silent minutes at most. Replay mode never idles that long (§4.2 A2). Recorded in §6.

#### `lib/api.ts` — the client

```ts
export type SessionStatus = "running" | "completed" | "max_iterations" | "incomplete" | "failed";
export type ApiMode = "live" | "replay";
export interface ResearchRequest { query: string; max_iterations: number | null; output_format: "markdown"; config_overrides: Record<string, unknown>; }
export interface ResearchError { error_type: string; source: string; message: string; recoverable: boolean; timestamp: string; details: Record<string, unknown>; }
export interface CoverageProgress { required_targets: number; answered_targets: number; missing_required_target_ids: string[]; not_found_target_ids: string[]; }
export interface EvidenceCounts { read_records: number; network_reads: number; cache_reads: number; unique_works: number; publishers: number; source_urls: number; findings: number; assessed_sources: number; cited_assessed_sources: number; verified_findings: number; corrected_findings: number; quoted_findings: number; dropped_findings: number; context_unchecked_findings: number; cited_findings: number; }
export interface ResearchSessionResponse {
  session_id: string; query: string; status: SessionStatus; current_agent: string | null; iteration: number;
  started_at: string; finished_at: string | null; report_path: string | null; trace_url: string | null; errors: ResearchError[];
  evidence_path: string | null; quality_path: string | null; quality_contract_version: string | null;
  semantic_review_status: string | null; semantic_review_score: number | null; duration_seconds: number | null;
  coverage: CoverageProgress | null; evidence_counts: EvidenceCounts | null;
}
export interface SessionListResponse { sessions: ResearchSessionResponse[]; }
export interface ResearchEvent { event_type: string; source: string; message: string; timestamp: string; metadata: Record<string, unknown>; }
export interface ValidationIssue { location: string; type: string; }
export interface ApiErrorBody { code: string; message: string; reason: string | null; issues: ValidationIssue[]; target?: string; }   // the proxy's 502 carries reason: null, issues: [], target
export interface EvidenceSource { url: string; title: string; organisation: string; evaluation_status: string | null; low_confidence: boolean; authority_score: number | null; recency_score: number | null; relevance_score: number | null; overall_score: number | null; }
export interface EvidenceFigure { value: string; kept: boolean; period: string | null; scope: string | null; organisation: string | null; attribution: "own" | "relayed" | "unattributed" | null; kind: "actual" | "forecast" | null; release: string | null; evidence_words: string | null; corrected: boolean; dropped_reason: string | null; reason: string | null; }
export interface EvidenceFinding { label: string; status: "verified" | "verified_corrected" | "quoted" | "dropped" | null; dropped_reason: string | null; context_unchecked: boolean; cited: boolean; target_ids: string[]; content: string; snippet: string | null; passage: string | null; source: EvidenceSource; figures: EvidenceFigure[]; }
export interface EvidenceNotFound { target_id: string; question: string; queries: string[]; pages_read: string[]; searched: boolean; }
export interface EvidenceRefused { where: string; text: string; reason: string; finding_labels: string[]; }
export interface EvidenceResponse { session_id: string; iteration: number; findings: EvidenceFinding[]; not_found: EvidenceNotFound[]; refused: EvidenceRefused[]; }

export class ApiError extends Error { readonly status: number; readonly body: ApiErrorBody; }   // any non-2xx carrying the envelope
export class ApiUnreachableError extends Error { readonly target: string; }                       // the proxy's 502 api_unreachable
export interface ApiResult<T> { data: T; mode: ApiMode | null; }                                  // mode = X-Deep-Research-Mode

export function startResearch(body: ResearchRequest): Promise<ApiResult<ResearchSessionResponse>>;  // POST /api/research; one attempt, never retried
export function getStatus(sessionId: string): Promise<ApiResult<ResearchSessionResponse>>;
export function listSessions(limit?: number): Promise<ApiResult<SessionListResponse>>;              // GET /api/research?limit=50
export function getReport(sessionId: string): Promise<ApiResult<string>>;                           // text/markdown body
export function getEvidence(sessionId: string): Promise<ApiResult<EvidenceResponse>>;
export function reportUrl(sessionId: string): string;             // /api/research/{id}/report
export function evidenceMarkdownUrl(sessionId: string): string;   // /api/research/{id}/evidence?format=markdown
export function streamUrl(sessionId: string): string;             // /api/research/{id}/stream
```

Every function calls its own origin (`/api/...`), `cache: "no-store"`, and maps a non-2xx response to `ApiError` (or `ApiUnreachableError` when the body's `code` is `api_unreachable`). Nothing in the client retries.

#### `lib/run-state.ts` — the event core, ported unchanged

```ts
export type NodeId = "planner" | "researcher" | "source_evaluator" | "evidence_verifier" | "report_writer" | "report_reviewer" | "finalize_report";
export type Mark = "done" | "loop" | "skipped";
export type PaintedMark = Mark | "active";
export interface Stage { id: NodeId; label: string; meta: string; }
export const STAGES: readonly Stage[];                                   // index.html:2456-2464
export const AGENT_ORDER: readonly NodeId[];                             // :2465
export const ARCS: Record<"extra_pass" | "redraft", { from: NodeId; to: NodeId }>;   // :2501-2504
export const BLURB: Record<NodeId, string>;                              // :3095-3103
export interface Counters { subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null; findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null; statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null; }
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string; }
export interface RunState {
  marks: Partial<Record<NodeId, Mark>>; active: NodeId | null; openNode: NodeId | null;
  pass: number; maxPasses: number; loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | null;
  loopPending: boolean; tag: LoopTag | null; rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>; blurbs: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number; finalStatus: string | null;
}
export interface RunEvent { type: string; metadata: Record<string, unknown>; }
export type Handler = (run: RunState, md: Record<string, unknown>) => void;
export const EVENT_HANDLERS: Readonly<Record<string, Handler>>;         // the sixteen handlers of :2925-3019, bodies unchanged
export function emptyCounters(): Counters;                               // :2885
export function newRunState(passes: number | null | undefined): RunState; // :2890
export function plural(n: number, one: string, many: string): string;    // :2920
export function applyEvent(run: RunState, ev: RunEvent): void;           // :3020 — mutates `run`
export function marksFor(run: RunState, activeId: NodeId | null): Partial<Record<NodeId, PaintedMark>>;   // :3025
export interface CounterRow { key: string; label: string; scope: string; value(c: Counters): string | { muted: string } | null; }
export const COUNTER_ROWS: readonly CounterRow[];                        // :3033-3054
export function toRunEvent(event: ResearchEvent): RunEvent;              // { type: event.event_type, metadata: event.metadata }
export function replayRun(events: readonly ResearchEvent[], passes: number | null | undefined): RunState;   // newRunState + applyEvent over the list
export function failedMarks(run: RunState, sessionStatus: SessionStatus): Partial<Record<NodeId, PaintedMark>>;
   // marksFor(run, run.openNode), plus finalize_report = "skipped" when (run.finalStatus ?? sessionStatus) === "failed" — index.html:3745-3750
```

"Ported unchanged" is a checkable claim: the sixteen handler bodies, `newRunState`, `nextRow`, `rearm`, `marksFor`, `emptyCounters` and the seven `COUNTER_ROWS.value` functions are the prototype's statements with TypeScript types added, reading only `md.<key>` for the keys the design spec §3.3 lists. The one adaptation is `toRunEvent`, because the API's frame carries `event_type` (`types.py:1338`) where the prototype's scripts carried `type` (`index.html:2830`). Nothing in the module touches the DOM, timers or module state other than `AGENT_ORDER`.

#### `lib/format.ts` — the display helpers

```ts
export interface SessionView { status: SessionStatus; iteration: number; passes: number | null; review: { status: string | null; score: number | null } | null; coverage: CoverageProgress | null; }
export function toSessionView(s: ResearchSessionResponse, passes: number | null): SessionView;
   // review is null when both semantic_review_status and semantic_review_score are null; otherwise { status, score }
export const STATUS: Record<SessionStatus, { label: string; dot: "dot-live" | "dot-ok" | "dot-warn" | "dot-danger" }>;  // index.html:1704-1710
export function passNumber(iteration: unknown): number;           // :1715 — the one place the zero-based offset lives
export function passTotal(s: { passes: number | null }): number | null;   // :1721
export function passText(s: SessionView): string;                 // :1725 — "pass p" or "pass p of P"
export function fmtScore(v: unknown): string | null;              // :1730 — two decimals; null = no score, never 0
export function notFoundClause(s: SessionView): string;           // :1734
export function statusNote(s: SessionView): string;               // :1747 — the chip's second clause, one rule per status
export function fmtDur(a: string | null, b: string | null): string | null;   // :1765
export function fmtSeconds(s: number | null): string | null;      // :1772
export function fmtClock(iso: string | null): string | null;      // :1776 — "HH:MMZ"
export function fmtElapsed(sec: number): string;                  // :1782 — "MM:SS"
export function meterClass(v: number): "ok" | "warn" | "danger" | null;   // :3267 — v > 0.8 ok; 0.80 paints yellow on purpose (design spec §4.4)
export const HALT_HEADLINES: Readonly<Record<string, string>>;    // :3708-3717 — eight entries, incl. the two api.research.* types
export const PILL_TEXT: Readonly<Record<string, string>>;         // :3483
export const VERIFICATION_TEXT: Readonly<Record<string, string>>; // :3485
```

`toSessionView` is the adapter between the API's flat fields (`semantic_review_status`, `semantic_review_score`, `coverage`, `iteration`) and the prototype's session shape (`review{status, score}`, `coverage`, `iteration`, `passes`) so that `statusNote` and `passText` keep their bodies.

#### What each module ports

| Prototype (index.html) | Target | Note |
|---|---|---|
| `emptyCounters`, `newRunState`, `nextRow`, `rearm`, `plural`, `EVENT_HANDLERS`, `applyEvent`, `marksFor`, `COUNTER_ROWS`, `STAGES`, `AGENT_ORDER`, `ARCS`, `BLURB` (`:2885-3054`, `:2456-2465`, `:2501-2504`, `:3095-3103`) | `lib/run-state.ts` | unchanged; `toRunEvent`, `replayRun`, `failedMarks` added |
| `STATUS`, `passNumber`, `passTotal`, `passText`, `fmtScore`, `notFoundClause`, `statusNote`, `fmtDur`, `fmtSeconds`, `fmtClock`, `fmtElapsed`, `meterClass`, `HALT_HEADLINES`, `PILL_TEXT`, `VERIFICATION_TEXT` (`:1704-1784`, `:3267`, `:3708-3717`, `:3483-3485`) | `lib/format.ts` | unchanged; `toSessionView` added |
| `chipHTML` (`:1759`), `renderTopbar` (`:2119`) | `components/StatusChip.tsx`, `Topbar.tsx` | JSX instead of `innerHTML` |
| `renderList` (`:1855`), sidebar markup (`:1145-1168`), `setSidebar`/`toggleSidebar`/`isDrawer` (`:1891-1904`) | `components/Sidebar.tsx`, `app/layout.tsx` | data from `GET /research`; collapsed state kept in `localStorage` (`dr.console.sidebar`, `:1575`) |
| composer markup (`:1194-1263`), `STARTERS` (`:2285-2291`), the popover placement rules (`DESIGN.md` §3.2), `optionsHTML` (`:1836`) | `components/Composer.tsx`, `SettingsPopover.tsx`, `SettingsStrip.tsx` | the request body of the design spec §4.3 |
| `openSession` (`:3766`), `showStage` (`:1959`), the stage sections | `components/SessionScreen.tsx` | the stage table above, driven by `/status` and the stream |
| `updateRunningChrome` (`:3104`), `buildSpine`/`syncSpine` (`:2565-2650`), `drawLoop`/`setLoopState`/`loopRows` (`:2505-2563`), `renderCounters` (`:3057`), `renderLoopTag` (`:3074`), `renderPassTrack` (`:2653`) | `components/RunningPipeline.tsx`, `Spine.tsx`, `Counters.tsx` | declarative from `RunState`: rows `<li data-stage data-state>`, `.spine-wrap[data-loop][data-arc]`; the arc path is computed in a `useLayoutEffect` from the two rows' measured boxes, as `drawLoop` does |
| `populateReport` (`:3316`), `renderEvidenceCounts` (`:3299`), `paintMeters` (`:3271`), the report stage markup (`:1347-1497`) | `components/ReportStage.tsx`, `ReportBody.tsx`, `ReportRail.tsx` | the Markdown body comes from `GET /report`, not a fixture |
| `EV_FILTERS`, `evidenceRows`, `renderChips`, `renderEvList`, `renderEvDetail`, `setView` (`:3474-3691`), markup (`:1503-1508`) | `components/EvidenceView.tsx` (+ the view toggle in `ReportStage.tsx`) | data from E1; `evidenceRows` exported for tests |
| `populateFailed` (`:3718`), markup (`:1513-1565`) | `components/FailedStage.tsx` | `HALTED_EVENTS` is not ported: the real stream supplies the events |
| scripted playback: `SCRIPTS`, `buildEvents`, `weigh`, `PLAY_MS`, `play`, `startPlayback`, `HALTED_EVENTS`, `SESSIONS`, `EVIDENCE`, `window.drConsole` | nothing | prototype-only |

#### Components ↔ prototype regions

| Component | Region (index.html) | Reads | Renders |
|---|---|---|---|
| `app/layout.tsx` | `.app[data-sidebar]` grid, `<aside class="sidebar">`, `<header class="topbar">` (`:1141-1186`) | sidebar state, mode context | the shell in the prototype's positions: sidebar 296 px / 64 px collapsed, topbar 56 px (`--sidebar`, `--sidebar-collapsed`, `--topbar`, `:52-54`); the drawer at ≤ 1080 px (`:962-977`) |
| `Sidebar` | `:1145-1168` | `GET /research` | `New research`, grouped rows (question + running mark), the footer sentence, the count |
| `Topbar`, `StatusChip`, `ModeChip` | `:1175-1185`; `chipHTML` | `SessionView`, mode | sidebar toggle; `<span class="chip"><span class="dot {dot}"/>{label} <span class="kind">· {statusNote}</span></span>`; no chip on `/`; muted `replay mode` chip |
| `Composer`, `SettingsPopover` | `:1194-1263` | — | textarea, `+` popover (model ×3, thinking, read-only effort line, extra passes 0–2 default 1, output directory), submit; `#composerError` slot |
| `SettingsStrip` | `optionsHTML`, `#runningOpts`, `#reportOpts` | submission record, `P` | five mono chips |
| `SessionScreen` | the stage sections `#stage-submitted` … `#stage-failed` | status, stream, `lib/session-store.ts` | one of the stages or S4 states of the table above; owns the reconnect ladder and the mode report |
| `SubmittedStage` | `:1277-1285` | question, strip | eyebrow `Question locked in`, the question, the strip |
| `RunningPipeline` (+ `Spine`, `Counters`) | `:1288-1329` | `RunState`, question, strip, `started_at` | eyebrow, question, strip, elapsed; the pipeline card: `Now / {stage}`, blurb, loop tag, `stage n of 7`, `pass p of P`, the spine with both arcs, the counters block (`counted from the event stream`, `pass p`) |
| `ReportStage` → `ReportBody`, `ReportRail` | `:1347-1497` | status, Markdown, E1 | head bar (`#reportMeta` = `session {id} · finished {fmtClock} · {fmtSeconds} · {passText}`, Report \| Evidence toggle, `Download Report`, `Download evidence log`, `Open LangSmith trace`), question, strip; the body card; the rail: Review, Coverage, Evidence, Session facts, Cost and usage, Errors |
| `EvidenceView` | `:1503-1508` | E1 | filter chips with counts, the list, the detail pane (the design spec §4.4 Evidence side) |
| `FailedStage` | `:1513-1565` | status, `RunState` | meta, question, the halt panel (headline, message), `Why nothing was published` facts, `Where it stopped` spine, `What survived the halt` counters |
| `ServiceBanner`, `SessionNotFound` | — | S4 | the S4 sentences and actions |

#### Report rendering (`ReportBody.tsx`)

`react-markdown` with `remark-gfm` renders `GET /report`'s Markdown as-is — the server owns wording, order, numbering and the table — with these presentation-only adaptations from the design spec §4.4, implemented as a `components` map plus one remark plugin (`remarkCitationAnchors`, defined and exported in `ReportBody.tsx`; it walks the mdast tree with its own recursive function over `node.children`, since `unist-util-visit` is not a dependency):

| Markdown element | Rendering |
|---|---|
| `# {question}` (the first H1) | not rendered — the question is the stage's `<h1>` (`#report-h`) |
| the evidence line (`Evidence as of … · n source(s)` or `No source could be checked.`, `report.py:1066-1072` per the design spec §3.5) | `<p class="avail">` — one muted line under the head bar |
| `## Bottom line` + paragraph | the first block of the prose card, its own `<h2>` |
| table + `*caption*` | `<div class="tbl-frame"><table class="tbl">…</table></div>` then `<p class="tbl-foot"><em>caption</em></p>`; when the header's first cell is `Option`, the frame gets `data-pinned` and the first column is `position: sticky; left: 0` with the card surface (the options table scrolls sideways, the page never does) |
| `## {part}` + `- {point} [n].` | `<h2>` + `<ul>` in the same card |
| `[n]` in text (also runs like `[1][2]`) | `remarkCitationAnchors` turns each `[n]` into a link node → `<a href="#src-n">[n]</a>`; the text is unchanged |
| `## What we couldn't confirm` | as-is |
| `## Sources` `<ol>` | items get `id="src-{n}"` (from the list's start number and index); each `[Title](url)` link renders with `target="_blank" rel="noopener"` |
| `How this was researched: [evidence log](…)` (the last paragraph) | intercepted: `<p class="avail">How this was researched: <button type="button" class="link">evidence log</button></p>` switching the view to Evidence when E1 loaded; muted plain text with the same words while it has not |
| `(figure: …)` suffix | as-is |

Nothing is injected (no client-side limitations, no citations table); the design spec §4.4 lists what the prototype removed for the same reason.

#### The Evidence view

`EvidenceView` renders `evidenceRows(evidence)` (finding rows with `status ?? "not_checked"`, not-found rows, refused rows) behind the seven filter chips `All · Verified · Corrected · Quoted · Dropped · Not found · Refused` with counts, the list (`role="listbox"`, arrow keys, `aria-selected`), and the detail pane per the design spec §4.4 (status line from `VERIFICATION_TEXT`, snippet, passage, source link in a new tab, the Context Check line, four meters with `meterClass`, one line per figure, or the not-found / refused details). Labels are rendered as E1 sends them: a finding the report never registered carries `X01`, `X02`… (`report.py:1921-1927`), so a dropped finding reads `X01` where the prototype fixture showed `F04`. While E1 is loading it shows the muted line *loading evidence log*; on a 409 it shows *Not published*; it never renders a disabled control. The `Coverage` card's not-found ids become `{target_id} — {question}` from `not_found[]` once E1 loaded, with the engine's ids (`topic-01-target-01 — What …?`).

### 4.4 S4 — Errors and missing data

Governing rule (api-gaps.md, DESIGN.md §4 rule 4): where a value is unavailable the interface says so in muted words; it never renders `0`, `—`, `null`, a placeholder or a disabled control.

| Situation | Detected by | Behaviour |
|---|---|---|
| `POST /research` → 422 | `ApiError` with `status 422`, `body.issues[]` | under the composer, in the `#composerError` slot (`index.html:1260`), muted: (I) *The service rejected the request: {location (type), …}* — locations and types only, as the API sends them; the typed question stays in the box |
| `POST` → 500 `configuration_error` | `ApiError` 500, `body.code == "configuration_error"`, `body.reason` | same slot: *Service configuration error · {reason}* (the enumerated reason, e.g. `missing_secrets`); the question stays |
| API unreachable | `ApiUnreachableError` from any call or the stream (the proxy's 502 `api_unreachable`) | a banner at the top of the main region: *Research service not reachable at {target}* with a **Retry** action that repeats the failed read; on a session page the backoff ladder also keeps trying (§4.3); the composer stays usable and a submit repeats the same banner; nothing is disabled |
| `/research/[id]` → 404 | `getStatus` 404 `session_not_found` | *This session isn't in the service's memory — sessions are lost when the API restarts.* and a **New research** action; the sidebar stays |
| The stream drops mid-run | `readStream` → `failed`, or `ended` while `/status` is `running` with `finished_at == null` | reconnect after 1, 2, 4, 8, 16 s then every 30 s; the server replays from event 1 (`sessions.py:200-208`); the page rebuilds `run` from scratch, so the screen is identical; it stops once `/status` is terminal or reads `running` with `finished_at` set |
| The service stopped during a run | `/status` → `running` with `finished_at != null` (`sessions.py:225-230`; `api-gaps.md:136` [old 2.7]) | *The service stopped while this run was in progress. Nothing was published.* above the frozen pipeline; no reconnect; **New research**. With `timeout_graceful_shutdown=5` (A1) a subscriber may instead see the connection drop while the process exits; the next `/status` then fails as "API unreachable" — both readings are honest, and neither shows a value the run never had |
| 409 on `/report` or `/evidence` | never requested before a terminal status; then `ApiError` 409 (`session_not_complete`, `report_unavailable`, `evidence_unavailable`) | the report card, or the Evidence view, shows the muted line *Not published*; no download control is rendered |
| API-level failure | `/status` → `failed` with `errors[0].error_type ∈ {api.research.failed, api.research.configuration_error}` | the Failed stage: headline `HALT_HEADLINES[error_type]` (*Research run failed* / *Service configuration error*), the API's `message`, the facts list with `error_type`, `source`, `recoverable`, and `details.reason` when present (an unknown replay case records `config_invalid`, A2/R1) or `details.exception_type`; `Where it stopped` from `failedMarks(run, "failed")`; counters `not reached` |
| Graph halt | `/status` → `failed` with a `graph_*` halting type | the Failed stage with the plain-words headline of the design spec §4.2 |
| Values the API does not serve | `evidence_counts == null` → *not measured*; `semantic_review_score == null` → *not scored*; `trace_url == null` while running → *Not available while running*; `report_path == null` → *Not published*; token usage and tool-call totals → *Not recorded* (the Cost and usage card, `index.html:1477-1485`); settings with no submission record → *settings as submitted: not recorded* (§4.3) | muted text, in the place the value would occupy |
| A counter whose event has not arrived | `Counters` value `null` | *not yet* while running, *not reached* on the Failed stage (`index.html:3055-3073`, `:3750`) |

The app never retries a `POST` (no accidental second paid run): a submit is one `fetch`, and every error above leaves the operator to decide. Live runs start only from the composer — no URL, header or sidebar action starts one.

### 4.5 S5 — Tests, visual checks, the live run, docs, done

#### Test matrix

| Id | Layer / file | Proves |
|---|---|---|
| T-A1a | pytest `tests/test_api/test_main.py` | `build_parser` defaults (`live`, `127.0.0.1`, `8000`, the default case, 150); `--mode replay` builds one app with `app.state.mode == "replay"`, a `ReplayRunner` rooted at the given `replay_root` and `ReplayCaseMiddleware` installed, `config_path == str(production_config_path())`; `build_app` in replay mode without `replay_root` raises `ValueError`; `--replay-case` / `--replay-delay-ms` with `--mode live`, an unknown case, a negative delay → `SystemExit(2)`; `main([...], serve=fake)` hands `fake` the app, the numeric host, the port and `timeout_graceful_shutdown=5`, creates and removes the temp root, and returns 0; `--mode live` builds an app whose runner is `run_research` |
| T-A1b | pytest `test_main.py` (subprocess) | `python -m deep_research.api --mode replay --port <free> --replay-delay-ms 50` as a subprocess with `TEMP`/`TMP` pointed at `tmp_path`: `GET /research` answers 200 with `X-Deep-Research-Mode: replay` within 60 s; `POST /research` → 202 whose `query` is the default case's question; `/stream` is read with a **streaming** `httpx.Client` (`stream=True`, frame by frame): the first frame arrives while `GET /status` still reads `running`, the last frame is `graph.session.completed`; `/status` then reads `completed`; `/evidence` is 200; the process is terminated and `tmp_path` holds the only `deep-research-replay-*` directory it left. This is the permanent proof that the real start command binds and streams under `network_denied()` (§4.2 A1) |
| T-A2a | pytest `tests/test_api/test_replay.py` | inside `network_denied() as attempts` and `offline_credentials()`: `create_app(runner=ReplayRunner(default_case="missing-target-triggers-one-extra-pass", delay=0, root=tmp_path), config_path=str(production_config_path()), mode="replay")` + the middleware, through `TestClient`: `POST` → 202; polled to terminal: `status == "completed"`, `query == scenario.question`, `iteration == 1` (measured, §3.3); the stream's first frame is `graph.session.started` and its last `graph.session.completed`; `/report` 200 `text/markdown`; `attempts == []` |
| T-A2b | pytest `test_replay.py` (`pytest.mark.asyncio`, no `TestClient` — Starlette's `TestClient` buffers the whole body, §3.7) | at the store level, inside `offline_credentials()` and `network_denied()` exactly as T-A2a: `runner = ReplayRunner(default_case=case, delay=0.05, root=tmp_path)`, `store = SessionStore(runner=runner)`, `store.start(session_id=…, query=scenario.question, max_extra_passes=None, output_format="markdown", config_overrides={}, config_path=str(production_config_path()))` inside the running loop; a consumer of `store.iter_events(session_id)` stamps `time.perf_counter()` on every yield and reads `session.status` as it goes: the first event arrives while `status == "running"`, the events arrive in the recorded order with `graph.session.completed` last, `status` is terminal only after that last event has been yielded (the tail is published before `_run` folds the outcome), and the pacing holds in aggregate — `last - first >= 0.8 * (n - 1) * delay` for the `n` events received (no per-gap floor: receipt jitter and Windows' 15.6 ms `monotonic` resolution make single gaps unreliable). Then cancellation: `store.close()` while a run is in flight returns within 1 s and no event is published afterwards (the pacer is cancelled, not drained) |
| T-A2c | pytest `test_replay.py` | `X-Replay-Case: review-unavailable` → `query` is that case's question, terminal `incomplete`, `semantic_review_status == "provider_failed"`; `X-Replay-Case: no-such-case` → 202, then `failed` with `errors[0].error_type == "api.research.configuration_error"`, `details.reason == "config_invalid"`, and `query` equal to the posted text (no rewrite); on `create_app(mode="live")` (no middleware) the header leaves `query` unchanged; the ContextVar reaches the runner (the first assertion cannot pass otherwise) |
| T-A2d | pytest `test_replay.py` | two `POST`s without waiting → both reach a terminal status, neither `failed`; artifacts under `tmp_path / <session_id>` for each |
| T-A3a | pytest `tests/test_api/test_evidence.py` | `run_replay_scenario` offline (as `test_real_agents.py:89-93`) for `missing-target-triggers-one-extra-pass` and for `extra-pass-finds-nothing`, then `build_evidence_response(outcome)`: the `label`s equal, in order, the `### {label} — ` headings of `outcome.state.report_evidence` (the second case's first label is `X01`); `sum(f.cited)` equals `outcome.evidence_counts.cited_findings` on these two cases (every finding has its own fingerprint there; the equality is not claimed in general, A3); counts by `status` equal `verified_findings`, `corrected_findings`, `quoted_findings`, `dropped_findings`; every kept figure has non-null `organisation`, `attribution` and `kind` (`period` and `scope` are the Context Check's own and may be null, `types.py:371-372`) and every dropped one has all five `null` with a `dropped_reason`; `not_found` and `refused` lengths equal `composition.not_found` and `composition.rejected_points`; a `snippet` with leading or trailing whitespace survives `model_validate(model_dump())` unchanged (the non-stripping base) |
| T-A3b | pytest `test_evidence.py` (`TestClient`, scripted runner) | 404 unknown id; 409 `session_not_complete` while running; 409 `evidence_unavailable` for an outcome with no composition (JSON) and no `report_evidence` (Markdown); 200 JSON in the exact key set of the E1 shape; 200 `?format=markdown` with `content-type: text/markdown` and the exact `report_evidence` text; `?format=pdf` → 422 `validation_error` |
| T-A4 | pytest `test_app.py`, `test_sessions.py` | `query` on the 202, on `/status`, and on every list item, equal to the stripped request text; the five existing `ResearchSessionResponse(...)` constructions (`test_sessions.py:328`, `:339`, `:370`, `:397`, `:436`) pass with `query="…"` added, and a construction without `query` raises `ValidationError` |
| T-A5 | pytest `test_app.py` | `GET /research` → `{"sessions": []}`; three sessions → newest first; `?limit=2` → two; `?limit=0` and `?limit=201` → 422; each item equals that session's `/status` body |
| T-A6 | pytest `test_sessions.py` | `publish` of `researcher.tool_call {iteration: 7}` leaves `iteration` unchanged; `graph.node.started {iteration: 1}` sets 1; `graph.extra_pass.started {iteration: 1}` sets 1; a `graph.*` event without `iteration` leaves it |
| T-A7 | pytest `test_app.py` | `X-Deep-Research-Mode` equals the app's mode on 202, on `/status` 200, on 404, on 422, on the stream's response and on `/report` (a direct `Response`); `create_app()` is `live` |
| T-A8 | the existing suite | green after the change set, the five `query=` additions of T-A4 being the only edits to existing tests (run once by the controller after all subagents land) |
| T-W1 | Vitest `web/test/run-state.test.ts` | with `test/fixtures/events/{missing-target-triggers-one-extra-pass,scoped-redraft-after-a-named-defect}.json` (captured by `scripts/capture-replay-events.mjs` from the API in replay mode with `--replay-delay-ms 0`; each `{case_id, captured_at, status: <the final /status body>, events: ResearchEvent[]}`): (a) terminal agreement with the server — after the last frame, `finalStatus === status.status`, `pass === status.iteration + 1`, `maxPasses === graph.session.started.max_extra_passes + 1`, `active === null`, `loop === "off"`, `arc === null`, `tag === null`; (b) extra pass — at the first `graph.route.decided {destination: "extra_pass"}`: `marks.planner === "done"`, `active === "researcher"`, rows 3–6 absent from `marks`, `loop === "flowing"`, `arc === "extra_pass"`; unchanged after the reviewer's own `graph.node.completed`; after `graph.extra_pass.started`: `loop === "settled"`, `pass === 2`, `tag.kind === "extra_pass"`, this-pass counters `null`, `countersPass === 2`; (c) redraft — at `graph.route.decided {destination: "redraft"}`: rows 1–4 `done`, `active === "report_writer"`, row 6 absent, `arc === "redraft"`; `pass === 1` through `graph.report.redraft_requested`, whose tag reads `Reviewer named 1 material defect`; (d) `counters.toolCalls` equals the number of `researcher.tool_call` frames and `counters.sources` the last `source_evaluator.evaluation.completed.source_count`; (e) the prototype's `HALTED_EVENTS` (ported into the test from `index.html:2829-2838`) → `failedMarks` gives planner `active`, rows 2–6 `skipped`, `finalize_report` `skipped`, every counter `null` |
| T-W2 | Vitest `format.test.ts` | `statusNote` for the six outcomes of the design spec §4.2 table; `passText` with and without `passes`; `fmtScore(0.8) === "0.80"`, `fmtScore(null) === null`; `notFoundClause` at 0, 1, 2; `meterClass(0.8) === "warn"`; `toSessionView` maps the flat fields |
| T-W3 | Vitest `stream.test.ts` (node) | `parseSse` across chunk boundaries, CRLF, multi-line `data`, comment lines; `backoffDelaysMs` yields 1000, 2000, 4000, 8000, 16000, 30000, 30000; `readStream` against a local `http.createServer` writing two frames 100 ms apart then ending → `onEvent` twice with ids 1 and 2 before `ended`; a 404 upstream → `failed` with status 404 |
| T-W4 | Vitest `proxy.test.ts` (node) | the route handler with `DEEP_RESEARCH_API_URL` at a local server that writes one SSE frame, waits 300 ms, writes another and ends: the returned `Response.body` yields the first frame before the second is written (streamed, not buffered); forwarded and dropped headers per §4.1; `POST` forwards the body and `x-replay-case`; a closed ordinary port → 502 with `{"error": {"code": "api_unreachable", "message": …, "reason": null, "issues": [], "target"}}`; abort: the handler is called with a `Request` whose signal is aborted while the upstream stream is open, and the local server observes its socket close within 1 s — proves that the handler forwards its signal to the upstream `fetch`; whether Next's server aborts that signal on a browser disconnect stays the [INFERENCE] of §4.1 |
| T-W5 | Vitest + Testing Library `web/test/components/*.test.tsx` | `StatusChip` wording per status; `Counters` renders `not yet` for `null` and never `0`; `FailedStage` headlines `Service configuration error` and `Model provider misconfigured`; `ReportRail` renders `not measured` / `not scored` / `Not recorded` / `Not published`; `ReportBody` on a Markdown sample in the design spec §3.5 shape: no `h1`, muted evidence line, `[n]` → `a[href="#src-n"]`, `ol li#src-n`, source links `target="_blank"`, an `Option` table in a `.tbl-frame[data-pinned]`, the evidence-log line as a button when E1 is loaded; `EvidenceView` on the prototype's `EVIDENCE.default` fixture (ported into the test): `All` lists 8 rows, each chip's count matches, `not checked` appears only under `All`; `Composer` shows the 422 text with the question kept |
| T-E1…T-E13 | Playwright `web/e2e/*.spec.ts` | the table below |

**Playwright setup.** `playwright.config.ts` declares three `webServer` entries:

| Server | Command | Environment |
|---|---|---|
| the API in replay mode | `${JSON.stringify(python)} -m deep_research.api --mode replay --port 8010`, where `python = process.env.DEEP_RESEARCH_PYTHON ?? path.resolve(process.cwd(), "../.venv/Scripts/python.exe")` — the venv interpreter, **quoted** because every interpreter path on this workstation contains spaces and the command runs through `cmd.exe` (verified by the reviewer: the unquoted form fails). The default exists only in the main checkout; inside a `.worktrees/*` tree it does not (the venv lives at the main checkout root), so there `DEEP_RESEARCH_PYTHON` is **required**, and the config throws `DEEP_RESEARCH_PYTHON must point at the venv interpreter (…/.venv/Scripts/python.exe); <path> does not exist` at load when the resolved path is missing. No `__dirname`: the config resolves paths from `process.cwd()` (= `web/`) so it stays valid as an ES module | `cwd` = the repository root; `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`, `TEMP`/`TMP` = `web/.e2e-tmp/` (gitignored), created with `mkdirSync(…, { recursive: true })` when the config loads — outside Playwright's `outputDir` (`web/test-results/`), which Playwright clears at the start of a run, so the replay root can neither be swept away before the server starts nor vanish mid-run |
| the app | `npm run start -- --port 3010` | `DEEP_RESEARCH_API_URL=http://127.0.0.1:8010` |
| the app pointed at a dead port | `npm run start -- --port 3011` | `DEEP_RESEARCH_API_URL=http://127.0.0.1:${deadPort}`, where `deadPort` is reserved **once**: ``process.env.DEEP_RESEARCH_DEAD_PORT ??= execSync(`node -e "const s=require('net').createServer().listen(0,()=>{process.stdout.write(String(s.address().port));s.close()})"`).toString().trim()`` (single quotes inside the double-quoted `-e` argument; the reviewer ran this form and got an ordinary closed port) — synchronous, so the config needs no top-level `await`, and idempotent through `??=`, so the worker processes that re-evaluate the config inherit the same value instead of reserving their own. The result is an ordinary closed port that fails with `ECONNREFUSED`, never port 1 or another entry of the Fetch bad-port blocklist (§3.7); the tests read it from `process.env.DEEP_RESEARCH_DEAD_PORT` |

`npm run test:e2e` runs `next build` first, then `playwright test --project chromium` (the `visual` project runs only through `npm run capture:visual`); the production server, not `next dev`, so no dev overlay reaches a capture. Outcomes are picked with `context.setExtraHTTPHeaders({ "X-Replay-Case": … })`, which the proxy forwards. Timing facts the tests are written for: at the default 150 ms pacing the default case's six planner-phase frames are all out ≈ 0.9 s after the 202 while the Submitted beat holds 2.2 s, and the `flowing` window between `graph.route.decided` and `graph.extra_pass.started` is three frames ≈ 450 ms — shorter than a polling assertion's back-off. Transition-sensitive tests therefore record DOM changes with a `MutationObserver` installed through `page.addInitScript` (it appends every change of `#spineWrap`'s `data-loop`/`data-arc` and of each `li[data-stage]`'s `data-state` to `window.__drTransitions`) and assert on the recorded sequence, not on a sampled instant.

| Id | Test | Asserts |
|---|---|---|
| T-E1 | submit → running → report (default case) | the URL becomes `/research/{id}`; the Submitted beat shows the question and the strip; the **first Running render** has Planning `done` and the active row Researching or later (the planner finishes inside the Submitted beat); the chip reads `Running · pass 1 of 2`; the sidebar row shows the case's question ("Acme widget"), not the typed text; the Report stage shows the chip `Completed · review accepted · {score}`, the muted evidence line, `Bottom line`, `Sources`, and `Download Report` / `Download evidence log` links that answer 200 |
| T-E2 | running with an arc (`missing-target-triggers-one-extra-pass`) | the recorded `#spineWrap` transitions contain `data-loop=flowing` with `data-arc=extra_pass` followed by `data-loop=settled`; the loop tag `extra pass` with `1 required target had no verified finding`; `pass 2 of 2`; row 2's caption `1 missing target only`; the run ends Completed. This is also the proof that SSE frames cross the proxy live |
| T-E3 | redraft (`scoped-redraft-after-a-named-defect`) | the recorded transitions contain `data-arc=redraft` with `flowing` then `settled`; the tag `redraft` with `Reviewer named 1 material defect`; the chip stays `pass 1 of 2` while the tag shows |
| T-E4 | review unavailable (`review-unavailable`) | Report stage; chip `Partially completed · review unavailable`; the Review card's status text `review unavailable` and score `not scored` |
| T-E5 | empty but clean (`empty-but-clean`) | the Report stage, not the Failed one; the chip `Partially completed · extra passes used · 3 targets not found` (the case ends `max_iterations` at iteration 1 with three not-found targets, §3.3); the body is the server's Markdown as-is (no `Executive Summary`, no injected text); the Coverage card lists `topic-01-target-01 — {question}`, `topic-02-target-01 — {question}`, `topic-03-target-01 — {question}` from E1 |
| T-E6 | Evidence view (`extra-pass-finds-nothing` — the case with a dropped finding) | the toggle shows chips with counts, the first row selected, a detail pane with a source link; the `Dropped` chip filters to one row labelled `X01` (the engine's label for an unregistered finding — correct, not a defect); the report's evidence-log line switches the view |
| T-E7 | reload mid-run | submit; wait for `Now / Researching`; `page.reload()`: Running again (no Submitted beat), the active row is Researching or later, the elapsed counter continues from `started_at`; the run ends on the Report stage |
| T-E8 | API down (port 3011) | submit → the banner `Research service not reachable at http://127.0.0.1:${DEEP_RESEARCH_DEAD_PORT}` with Retry; the question stays; the composer accepts a second submit that shows the same banner |
| T-E9 | 404 session | `/research/does-not-exist` → the not-in-memory sentence and `New research` |
| T-E10 | failed stage (`X-Replay-Case: no-such-case`) | the Failed stage: headline `Service configuration error`, facts `api.research.configuration_error` and `config_invalid`, chip `Failed · halted`, `Where it stopped` with no row `done`, counters `not reached`, no download control |
| T-E11 | sidebar | two submits → rows newest first; a session started through `request.post` while the page is open appears within 5 s without a reload while one runs; the footer sentence is present |
| T-E12 | layout, every stage at 1252×853 and 390×844 | `document.scrollingElement.scrollWidth <= window.innerWidth`; at 1252 the sidebar is 296 px wide expanded and 64 px collapsed, the topbar 56 px tall, `.prose` ≤ 720 px, `.rail` 300 px; at 390 the sidebar is a closed drawer; each `.card` has `scrollHeight <= clientHeight + 1`; the spine has seven `li[data-stage]` in `AGENT_ORDER` |
| T-E13 | replay chip | the muted `replay mode` chip is in the topbar on every page served by the replay-mode API |

#### Visual checkpoints

`web/e2e/visual.spec.ts` (Playwright project `visual`, run with `npm run capture:visual`) drives the same flows against the replay-mode API and saves **full-page** PNGs — `page.screenshot({ fullPage: true })`, never viewport-height — into `web/visual/<checkpoint>/` (gitignored). Every stage and view at **both** widths, 1252 px and 390 px: fourteen captures — `01-idle`, `02-submitted`, `03-running`, `04-report`, `05-failed`, `08-evidence`, `09-running-extra-pass`, each with its `-phone` twin (`07-idle-phone` keeps the reference's name).

| Checkpoint | After | Captures | Compared with `docs/design/reference/` |
|---|---|---|---|
| C1 | the shell, `globals.css`, the composer (idle) | `01-idle`, `07-idle-phone` | `01-idle.png`, `07-idle-phone.png` |
| C2 | the running stage on the real stream | `02-submitted`, `02-submitted-phone`, `03-running`, `03-running-phone`, `09-running-extra-pass`, `09-running-extra-pass-phone` | `02-submitted.png`, `03-running.png`, `09-running-extra-pass.png`; the three phone captures have no reference and are judged against DESIGN.md's phone rules (the drawer, 16 px gutters, no horizontal scroll, the spine centred) |
| C3 | the report, the Evidence view, the failed stage | `04-report`, `04-report-phone`, `08-evidence`, `08-evidence-phone`, `05-failed`, `05-failed-phone` | `04-report.png`, `08-evidence.png`, `05-failed.png`; the chips and the failed panel against `06-states.png`; the three phone captures against the design rules (tables in scrolling frames, the rail stacked below the column) |
| C4 | all layers green, before the live run | all fourteen | all nine references, the five phone-only captures against the design rules |

At every checkpoint the implementer attaches the captures at both widths and the layout-assertion results, and reviews them full-height against the references and the design rules; the controller **personally views the images at C3 and C4**, the two largest sets (R4). Every difference in copy, position, card height or colour is fixed before the next checkpoint. The reference renders are viewport-height except `06-states` and `09-running-extra-pass` (§3.7), so the comparison is at equal width, top-aligned; the full-page capture is what shows anything the viewport would have hidden (the previous project's P0). Three things C3 must **not** treat as defects, because they are engine truth: a dropped or otherwise unregistered finding is labelled `X01` (not the fixture's `F04`); not-found targets read `topic-01-target-01`, not `T04`; and the report head bar reads `0m 00s` in replay mode (`duration_seconds` is the unpaced span, §4.3).

#### The live run

| Rule | Detail |
|---|---|
| When | only after every layer of the matrix is green and C4 has passed |
| How many | ONE run, through the app: `python -m deep_research.api --mode live`, `npm run start`, a question typed in the composer |
| Start | only after the controller has confirmed the time with the human; off-peak only (DeepSeek peak is 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday, excluding Chinese public holidays), and only when the run's 70–110 minutes fit before the next window — weekday starts at 04:00–04:30 UTC or 10:00–23:30 UTC; weekends unrestricted **except that a Sunday start must let a 110-minute run finish before Monday 01:00 UTC** (so no Sunday start after 23:10 UTC) — decided from `date -u` |
| Secrets (human decision, 2026-09-28) | the worktree has no `.env`. Immediately before the live API starts, the main checkout's `.env` is **copied as a file** into the worktree root (Node `fs.copyFileSync`; nobody reads, prints or greps its contents; `.env` is gitignored, `.gitignore:151`). The live API then starts **from the worktree root** with this branch's `config.yaml`, whose directory is where `load_dotenv` looks (`config.py:728`); the key variables (`DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `OPENAI_API_KEY`) are **removed from the API's environment** (`env -u …`) so the copied `.env` is the only source — `load_dotenv(override=False)` would otherwise let a key already in the agent harness's environment shadow the copy and bill or trace elsewhere; a pre-flight `load_settings("config.yaml")` under the same removal — a strict load, no provider call — must succeed first. After the run the copy is deleted and its absence verified. The main checkout's own `config.yaml` is never used: it belongs to an older `main` and this branch's strict loader rejects it |
| Never | stop a run that has started; start a second one; leave the `.env` copy in the worktree |
| Proves | submit → 202 → the stream through the proxy for the whole run (reconnects, if the idle timeout fires, rebuild the same screen) → the Report and the Evidence view from the real outcome; the topbar shows no replay chip; the head bar shows the real `duration_seconds` |
| Recorded | session id, start and end (UTC), the chip note, `duration_seconds`, the number of reconnects, the four full-page captures of the finished session (report and evidence at 1252 and 390 px) |

#### Docs

| File | Change |
|---|---|
| `README.md` | a **Run the app** section after *FastAPI Interface* (`README.md:755-835`): the two commands, the two modes (`--mode replay` dials nothing and needs no keys; `--mode live` needs the secrets of the existing matrix), `--host/--port`, `DEEP_RESEARCH_API_URL`, `X-Replay-Case`, `--replay-case`, `--replay-delay-ms`; the routes table gains `GET /research` and `GET /research/{session_id}/evidence`; the 202 paragraph names `query`; the `X-Deep-Research-Mode` header; the *UI* section (`README.md:837-846`) rewritten: the app exists in `web/` |
| `docs/design/api-gaps.md` | E1 (`:69-104`) moves to *Closed since 2026-09-16* as one line (served since 2026-09-27; JSON per the shape; `?format=markdown` returns `state.report_evidence`); 1.1 (`:111`) and 1.2 (`:112`) become closed lines; a closed line for the `/status.iteration` store fix (`sessions.py:68-69`); the *Existing surface* table lists seven routes, `query` (18 fields) and the mode header; every other gap stays as it is |
| `docs/design/DESIGN.md` | one line under the header note (`DESIGN.md:1-27`): *Implemented by the Next.js app in `web/` (2026-09-27); this package remains the reference.* |
| `web/README.md` | how to run, test, capture (the commands of this section), `DEEP_RESEARCH_PYTHON`, the OneDrive note (§6) |
| `.gitignore` | `web/node_modules/`, `web/.next/`, `web/test-results/`, `web/playwright-report/`, `web/visual/`, `web/.e2e-tmp/` |

#### Done

All test layers green (pytest including the existing suite; Vitest; Playwright); the four visual checkpoints passed; the live run completed through the app and is recorded; a final whole-branch review passes; a PR merged with a merge commit (no squash).

---

## 5. Acceptance criteria

| # | Criterion (checkable by the reviewer) |
|---|---|
| AC1 | `python -m deep_research.api --help` lists `--mode {live,replay}` (default `live`), `--host` (`127.0.0.1`), `--port` (`8000`), `--replay-case` (`missing-target-triggers-one-extra-pass`), `--replay-delay-ms` (`150`); `--mode live --replay-case x`, `--mode replay --replay-case no-such-case` and `--replay-delay-ms -1` exit 2; `main` passes `timeout_graceful_shutdown=5` to uvicorn in both modes and, in replay mode, removes the temp root it created (T-A1a). `pyproject.toml` declares `uvicorn`. |
| AC2 | With the API started as `--mode replay` in an environment holding neither `DEEPSEEK_API_KEY` nor `TAVILY_API_KEY`: `POST /research {"query": "anything"}` → 202 whose `query` is the default case's question and whose headers carry `X-Deep-Research-Mode: replay`; a streaming client receives the first `/stream` frame while `/status` still reads `running`, and the stream ends with `graph.session.completed`; `/status` reaches `completed` with `iteration == 1`; `/report`, `/evidence` and `/evidence?format=markdown` answer 200 (T-A1b). Under `network_denied()` the same run records zero connection attempts (T-A2a). |
| AC3 | `X-Replay-Case: review-unavailable` yields that case's question in `query` and a terminal `incomplete` with `semantic_review_status == "provider_failed"`; `X-Replay-Case: no-such-case` yields a `failed` session with `errors[0].error_type == "api.research.configuration_error"` and `details.reason == "config_invalid"`; on an app built with `create_app(mode="live")` the header changes nothing; no file under `src/deep_research/runtime/` changed (T-A2c, R1). |
| AC4 | At the store level with `delay=0.05`, under both guards and with the case's own question, an `iter_events` consumer receives the first event while `session.status == "running"`, receives the events in order with `graph.session.completed` last, sees the status turn terminal only after it, and measures `last - first >= 0.8 * (n - 1) * 0.05` s over the `n` events (`time.perf_counter()`); `store.close()` mid-run returns within 1 s and publishes nothing afterwards (T-A2b). Two sessions posted back to back both finish (T-A2d). |
| AC5 | `GET /research/{id}/evidence` returns JSON whose top-level keys are exactly `session_id, iteration, findings, not_found, refused`, whose finding keys are exactly the eleven of the E1 shape (`label … figures`), source keys the nine, figure keys the twelve, not-found keys the five, refused keys the four; the labels equal, in order, the `### {label} — ` headings of the run's evidence log; on `missing-target-triggers-one-extra-pass` and `extra-pass-finds-nothing`, `sum(cited)` equals `evidence_counts.cited_findings` and the second case's first label is `X01`; a snippet's leading and trailing whitespace survives the round trip; `?format=markdown` returns `state.report_evidence` verbatim as `text/markdown`; the codes are 404 unknown, 409 `session_not_complete` running, 409 `evidence_unavailable` without a composition or log, 422 for another `format` (T-A3a, T-A3b). `api/evidence.py` imports `_finding_registry_pairs` and `_figure_value_text` from `agents/report.py`; `git diff --stat 4e10823 -- src/deep_research/agents src/deep_research/runtime src/deep_research/graph src/deep_research/utils` is empty (R2). |
| AC6 | Every `ResearchSessionResponse` — the 202, `/status`, every list item — carries `query` equal to the stripped request text (T-A4). `GET /research` answers `{"sessions": [...]}` newest first, honours `limit` (default 20, 1–200, 422 outside) and each item equals that session's `/status` body (T-A5). |
| AC7 | `ResearchSession.publish` leaves `iteration` unchanged for `researcher.tool_call {iteration: 7}` and sets it for `graph.node.started` and `graph.extra_pass.started` (T-A6). |
| AC8 | `X-Deep-Research-Mode` is present on the 202, on 200s, on 404 and 422 responses, on the SSE response and on `/report`; `create_app()` reports `live` (T-A7). |
| AC9 | The whole pytest suite is green after the change set, run once after all implementation lands; the only edits to pre-existing tests are the five `query=` additions in `tests/test_api/test_sessions.py` (T-A4, T-A8). |
| AC10 | `web/app/globals.css` begins with `index.html:7-1137`, line for line after CRLF normalisation (the `:root` block `:11-55` included); app-only rules, if any, follow under one comment header `/* ═══ 2026-09-27: app-only additions ═══ */` and introduce no new colour literal. Checked by `npm run check:css` (`web/scripts/check-css-verbatim.mjs`: reads both files, normalises `\r\n` to `\n`, compares `index.html` lines 7–1137 with `globals.css` lines 1–1131, prints `OK` or the first differing line and exits non-zero) — a Node script, because `bash` on this workstation is WSL without a distribution and the working copy is CRLF (§3.5). |
| AC11 | `lib/run-state.ts` exports `STAGES` with the seven ids of `index.html:2456-2464` in order, the sixteen `EVENT_HANDLERS` keys of `:2925-3019`, `applyEvent`, `marksFor`, `newRunState`, `emptyCounters`, `COUNTER_ROWS` (seven rows, the labels and scopes of `:3033-3054`); T-W1 (a)–(e) pass on the two captured fixtures (each carrying its final `/status` body) and the ported `HALTED_EVENTS`. |
| AC12 | `lib/format.ts`'s `statusNote` returns, for the API responses of the six outcomes in the design spec §4.2 table: `pass 1 of 2`, `review accepted · 0.86 · 1 target not found`, `extra passes used · 1 target not found`, `not accepted · 0.71`, `review unavailable`, `halted`; `fmtScore(0.8) === "0.80"`; `meterClass(0.8) === "warn"` (T-W2). |
| AC13 | The proxy route handler streams: the first SSE frame is readable from its `Response.body` before the upstream writes the second; forwarded response headers are exactly `content-type`, `cache-control`, `x-accel-buffering`, `x-deep-research-mode`; a refused connection on a closed ordinary port yields 502 `{"error": {"code": "api_unreachable", "message": …, "reason": null, "issues": [], "target": "<DEEP_RESEARCH_API_URL>"}}`; aborting the handler's request closes the upstream socket within 1 s — the handler forwards its signal (T-W4). `next.config.ts` has `compress: false` and no `rewrites`. |
| AC14 | T-E1…T-E13 pass with `npm run test:e2e` against the replay-mode API on this workstation (Chromium), with the API server started through the quoted venv interpreter and the dead-port instance on a reserved, non-blocklisted port. |
| AC15 | On every stage at 1252×853 and 390×844: `document.scrollingElement.scrollWidth <= window.innerWidth`; at 1252 the sidebar measures 296 px expanded and 64 px collapsed, the topbar 56 px, `.prose` at most 720 px, `.rail` 300 px; at 390 the sidebar is a closed drawer; no `.card` is clipped; the spine has seven `li[data-stage]` (T-E12). |
| AC16 | The S4 texts appear verbatim where §4.4 says: the banner `Research service not reachable at http://127.0.0.1:<DEEP_RESEARCH_DEAD_PORT>` with Retry (T-E8), `This session isn't in the service's memory — sessions are lost when the API restarts.` (T-E9), `Service configuration error` with `config_invalid` in the facts on the failed stage for an unknown replay case (T-E10), `not yet` / `not reached` / `not measured` / `not scored` / `Not recorded` / `Not published` for the corresponding absences (T-W5); no `0`, `—`, `null` or disabled control stands in for a missing value anywhere in T-E1…T-E13. |
| AC17 | In replay mode the topbar shows a muted `replay mode` chip on every page (T-E13); in live mode it shows none. |
| AC18 | The fourteen full-page captures — seven stages/views (`01-idle`, `02-submitted`, `03-running`, `04-report`, `05-failed`, `08-evidence`, `09-running-extra-pass`) at 1252 px and their `-phone` twins at 390 px — exist for C4 under `web/visual/C4/`; the plan's summary records the implementer's full-height review at every checkpoint and the controller's own viewing at C3 and C4 (R4). |
| AC19 | `web/package.json` runtime dependencies are exactly `next`, `react`, `react-dom`, `react-markdown`, `remark-gfm`; dev dependencies are `typescript`, `vitest`, `@testing-library/react`, `@testing-library/dom`, `@playwright/test`, `@types/react`, `@types/react-dom`, `@types/node`, `jsdom` and nothing else (no Tailwind, no component or CSS library, no `unist-util-visit`). |
| AC20 | `README.md` has a `## Run the app` section naming the two commands and the two modes; its routes table lists `GET /research` and `GET /research/{session_id}/evidence`; `docs/design/api-gaps.md` has no `## E1` section and its closed table has lines for E1, 1.1, 1.2 and the iteration fix; `docs/design/DESIGN.md` carries the one-line implementation note; nothing else under `docs/design/` changed (`git diff --stat 4e10823 -- docs/design/` lists only those two files). |
| AC21 | ONE live run through the app is recorded per §4.5 (session id, UTC start and end, chip note, `duration_seconds`, reconnect count, the finished-session captures), started only after the human confirmed the time, outside the peak windows and early enough to finish before the next one. |
| AC22 | The branch's final whole-branch review passed and the PR was merged with a merge commit (no squash). |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Serving uvicorn inside `network_denied()` is exercised today only by the reviewer's round-1 probe (uvicorn 0.52.1, numeric host) — a later uvicorn or a non-numeric host could behave differently | the host is resolved to a numeric address before the guard; T-A1b runs the real start command as a subprocess and is the first test the plan writes. Fallback if T-A1b ever fails on bind or accept: `main` drives `uvicorn.Server(config)` directly — `await server.startup()` outside the guards, then `main_loop()` and `shutdown()` inside them — which keeps the process-lifetime scope for everything a request touches |
| Node's `fetch` (`undici`) drops an idle response body after 300 s [INFERENCE]; a live run's researcher node can be silent longer than that | the S4 reconnect ladder rebuilds the identical screen from the replay; replay mode never idles that long; the live run records the reconnect count (AC21) so the effect is measured, not guessed |
| Whether Next 16's server aborts a route handler's `request.signal` when the browser disconnects [INFERENCE]; the handler's own forwarding of the signal is what T-W4 proves | low impact: a stale upstream read lasts only until that session's stream ends (`iter_events` returns at the terminal status); checked by hand against `next start` if wanted (§4.1) |
| uvicorn's graceful shutdown waits for in-flight responses, so an open SSE subscriber would block Ctrl+C until the run ends, and a forced exit skips the lifespan (reviewer's reading of `uvicorn/server.py:272-310`) | `timeout_graceful_shutdown=5` (A1); the "service stopped" S4 state stays defined, and a dropped connection at exit surfaces as "API unreachable" instead — both are honest (§4.4) |
| `ReplayCaseMiddleware` rewrites the request body; a mistake there breaks every replay `POST` | T-A2a/T-A2c assert `query` on the 202 and on `/status`; the rewrite touches only `POST /research` with a JSON object body |
| The ContextVar must survive Starlette's middleware stack into the store's task | the reviewer's probe confirmed it; T-A2c cannot pass unless it does; both middlewares are pure ASGI (no `BaseHTTPMiddleware` task hop) |
| An unknown `X-Replay-Case` reports the generic reason `config_invalid` (R1), so the response does not name the case | the runner logs the case name and `REPLAY_CASE_IDS` server-side; the header is a test-only knob, and T-E10 relies on the reason, not the name |
| Two replay builds interleaving through the `compile_research_graph` patch (`replay.py:2031-2050`) | the build lock (§4.2 A2); the reviewer ran three sessions at once; T-A2d posts two |
| A long replay-mode session accumulates artifacts under the temp root; a forced kill skips `rmtree` | one root per process, created and removed by `main`, one directory per session; every spawner points `TEMP`/`TMP` at a directory it owns and that nothing clears while the server runs (T-A1b: `tmp_path`; Playwright: `web/.e2e-tmp/`, outside `outputDir`) |
| Starlette's `TestClient` buffers the whole SSE body, so a pacing test written against it passes vacuously or fails | T-A2b measures at the store level; T-A1b reads the real server with a streaming `httpx` client |
| The prototype's Submitted beat depends on knowing the tab submitted the session | recorded in `lib/session-store.ts`; a reload or a shared link goes straight to Running, as the design's stage table implies (a beat is for the submitting operator) |
| At the default pacing the planner finishes inside the 2.2 s Submitted beat, and the `flowing` arc state lasts ≈ 450 ms | T-E1 asserts the first Running render, not "Planning first"; T-E2/T-E3 assert recorded transitions from a `MutationObserver`, never a sampled instant |
| The worktree lives under OneDrive; `web/node_modules` and `web/.next` would be the first such trees here, and OneDrive sync is known to cause `EPERM`/`EBUSY` during `npm install` and `next build` [INFERENCE] | pause OneDrive sync for `npm install` and `next build`; if errors persist, keep `web/node_modules` and `web/.next` outside the synced folder through directory junctions (`mklink /J`) and record it in `web/README.md` |
| Vite 8 (which vitest 5 accepts) no longer depends on esbuild, so `esbuild.jsx` may not configure the JSX runtime [INFERENCE] | the plan sets the automatic JSX runtime through the installed Vite major's transformer option (§4.3) and T-W5 fails fast if JSX does not compile |
| `iteration` while a redraft runs stays at the pass (`nodes.py:1204` per the design spec) while the reviewer's tag shows | the chip reads `run.pass`; T-E3 asserts `pass 1 of 2` during the redraft |
| A figure of exactly 0.80 paints its meter yellow (`meterClass`, `index.html:3267-3270`) while acceptance is ≥ 0.80 | recorded on purpose by the design spec §4.4; the plan must not "fix" either side (T-W2 pins `meterClass(0.8) === "warn"`) |
| Engine truth differs from the prototype fixture in two visible places (`X01` labels, `topic-NN-target-NN` ids) and replay `duration_seconds` reads `0m 00s` | recorded in §3.4, §4.3 and the C3 note so nobody "fixes" the app to match the fixture |
| `bash file.sh` resolves to WSL on this workstation; interpreter paths contain spaces | every command runs through Node (`npm run …`), the quoted venv `python.exe`, or Playwright's `webServer` (spawned by Node with the path quoted) |
| Playwright needs a Chromium download and `next build` before `next start` | `npx playwright install chromium` once; `npm run test:e2e` builds first; the two `next start` instances share one build |
| The default 150 ms pacing makes each end-to-end session run 6.5–11 s | the Playwright timeouts allow 60 s per session; the fixture capture uses `--replay-delay-ms 0` |
| `react-markdown` 10.x's `components`/plugin API differs from older majors | the plan pins the installed major in `package.json` and T-W5 exercises every rendering rule against it |
| The count "4,689 tests" is brief-reported | AC9 is "the whole suite is green", whatever the count is on the day |

---

## 7. Out of scope

- Authentication, multi-user, sharing, deployment, hosting, HTTPS, a production build of the API.
- The other `api-gaps.md` items: token usage (3.2), the effective-settings echo (1.3), `/capabilities` (1.4), `/health` (1.5), the terminal stream frame (3.3), event identity / `Last-Event-ID` (3.4), the halting vocabulary on the response (3.5), live per-event delivery (3.7), report-body JSON (4.1), the report hash (4.2), what survived a halt (5.1), seeded failed states (5.2), a result summary per sidebar row (SB.1), durability (SB.2).
- Any engine change beyond A6: no file under `src/deep_research/agents/`, `graph/`, `runtime/` or `utils/` changes; no change to the published Markdown or to `config.yaml`; no new enumerated configuration reason.
- Any change to `docs/design/` beyond the `api-gaps.md` and `DESIGN.md` lines of §4.5; the `:root` tokens, the reference renders, the prototype.
- A rendered event log, cancel or re-run controls, an embedded trace viewer, provider selection, per-agent effort editing (`api-gaps.md:168-189`).
- An SSE heartbeat, a request-body stream through the proxy, HTTP methods other than `GET` and `POST` on `/api/*`.
- Persisting sessions across API restarts; the sidebar states the limitation instead.
- More than one live run.
