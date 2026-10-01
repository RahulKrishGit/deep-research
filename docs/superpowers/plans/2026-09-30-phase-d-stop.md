# Phase D — Stop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status** reviewed once: written 2026-09-30 by the spec-plan-author agent for Phase D of the approved spec at `73b4d7a6`, and dry-run as the Evidence paragraph below says; spec-plan-reviewer's review 1 (of `1cda4bdd`) approved it with changes, applied and re-run as "Review round 1" at the end records · **Date** 2026-09-30 · **Branch** `feat/notes-progress-report-stop` · **Phase order** D → A → C → B (spec §9): this plan is first.

**Goal:** The reader can stop one running session at once: `POST /research/{id}/stop` cancels the session's task — every provider, search and page request in flight with it — writes no report, and ends the session in a new terminal status `stopped`, which the web app shows with a Stop control beside the running chip, one confirmation, and a stopped stage that keeps what was done.

**Architecture:**
- **API.** `SessionStore.stop` runs synchronously inside the route: it reads the step the session was on from its own published events (`api/stop.py` `active_row`, the console's `run.active` rule), publishes `session.stopped`, sets `status = "stopped"`, `stopped_step` and `finished_at`, cancels a pending one-time check, then cancels the run's task and every note reading. `ResearchSession.publish` drops every later event, so `session.stopped` is the stream's last. `stopped` joins `TERMINAL_STATUSES`, so the stream closes, notes and answers are refused and every note outcome is terminal (`not_checked`, never `pending`). `/report` and `/evidence` answer a stopped session as a halted run (409).
- **Engine.** Nothing a run researches or writes changes. Cancellation already reaches LangGraph's node tasks, `gather` fan-outs, `AsyncOpenAI` and `httpx`; the one engine-side change is the search tool's transport: the default client becomes tavily's `AsyncTavilyClient`, awaited on the run's own loop, so a stop cancels a search in flight; injected synchronous clients keep running in a worker thread.
- **Web.** `ConsoleProvider` holds the session the topbar's `StopControl` acts on; `SessionScreen` offers it while the session is live and not publishing, and routes a `stopped` status to `UserStoppedStage`: a short note with "Ask again" over `BriefSpine` frozen at the stopped row. The chip reads `Stopped by you · at {step}` on a neutral dot; the sidebar names the session "stopped by you" in its accessible name only.

**Tech Stack:** Python 3.12 (`.venv`), FastAPI, pydantic 2, LangGraph 1.2.10, tavily-python 0.7.27 (`AsyncTavilyClient` over `httpx`), pytest + pytest-asyncio; Node 24, Next.js 16, React 19, Vitest 5 + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode. Windows 11, Git Bash: every command below is for this machine's main checkout.

**Spec:** `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` (commit `73b4d7a6`), Phase D only: §8 (8.1–8.6), §4 items 2 and 3 (what D implements before A), §3.10 (the ground truth §8 cites), the Phase-D rows of §9, §10 (AC28–AC33), §11.1–§11.3 and §12 R9. Decisions D17, D18, D19, D24, D25, D26, D29, D33 (and D38 where §8.3 names it) are closed; nothing here reopens them. Where this plan had to choose, the choice is listed under "Spec ambiguities resolved here"; where a spec statement cannot be kept as written in the order §9 sets (D first), it is an entry under "Open issues".

**Phase order (§9).** D is the first of four phases (D → A → C → B) and builds on `origin/main` as it is (`f4282818`) plus the spec. Three cross-phase contracts govern what D does and leaves alone:
- **§4 item 2, exactly as written for D-before-A.** D changes only today's `note_outcome` (`api/notes.py:343-376`): it gains `*, terminal: bool`, and each of its three `pending` exits (`:357`, `:360`, `:376`) returns `"not_checked" if terminal else "pending"`. `note_records` and `session_note_fields` pass `terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None` (§5.6). `ReaderNoteResponse.outcome` and `web/lib/api.ts` `ReaderNoteOutcome` gain `not_checked`; `OUTCOME_TEXT.not_checked` is "not checked" and `OUTCOME_TEXT.pending` becomes "not checked yet". Phase A later replaces the function body with the §5.6 table and adds `note_steering_outcome`; nothing else of §5.6 is done here.
- **§4 item 3.** A step is one of `check`, `planner`, `researcher`, `source_evaluator`, `evidence_verifier`, `report_writer`, `report_reviewer`, `finalize_report`; labels come from `STAGES`, and `check` reads "the questions".
- **§4 item 5.** "Phase D needs only item 2's `terminal` rule." Phase B's progress events, Phase A's note helpers and Phase C's report layout are untouched. Where §8.5 says "once Phase B has landed, the step's live subtitle from §6.3–§6.7", this plan gives the stopped row Researching's facts line only, in one helper (`stoppedSubtitle`, Task 7) that Phase B extends.

**Evidence.** Planning executed this document itself on 2026-09-30, on fresh exports of `73b4d7a6` (`git archive` into the session scratchpad, `web/node_modules` junctioned to the checkout's), with the checkout's own `.venv`:
- A script (Task 1 Step 2's, in its applying form) parsed this document's blocks as an implementer reads them and applied each step's blocks in order, stopping unless every anchor occurred exactly once. After each step it ran that step's pytest, Vitest, typecheck and CSS-check blocks. Every Expected line of Tasks 1–8, of Task 9 Step 4's type-check and test listing, and of Task 10 Steps 1–3 is the value that run printed; Task 1 Step 3's baselines were run on an untouched export; the reasons quoted under each fail-first step come from a second applying run with full tracebacks.
- No Playwright test, no capture and no server was run: the dispatch forbade running any research session, and the e2e suite starts replay sessions. Task 9's Playwright and capture lines and Task 10 Step 4's are therefore stated, not observed; each is marked **[not run in planning]** with the reasoning behind its value, and Task 1 Step 3's `--list` totals (which start no server) were observed.

## Global Constraints

- **Where.** The main checkout, branch `feat/notes-progress-report-stop`; every path is relative to the repository root. Tasks run **strictly in order 1 → 10**, one at a time. Commit after every task that changes files (Tasks 1 and 10 change none).
- **No live model, no secrets, no paid call.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider or Tavily. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest or the API in `--mode replay` (started only by Playwright's `webServer`, Task 9).
- **Stop, the decisions (spec §2, verbatim):**
  - D17: "`POST /research/{id}/stop` cancels the session's task at once (all in-flight provider and HTTP calls), writes no report, and ends the session in a new terminal status `stopped`, with the stream event `session.stopped` naming the step it was on. Report and evidence endpoints answer like a failed run (409 `report_unavailable`). … `409` when already terminal or once `finalize_report` has started (the notes cutoff); `404` for an unknown session. Offered from the one-time check (`needs_input`) through Reviewing."
  - D26: "A stopped session answers `/report` with 409 `report_unavailable` and `/evidence` with 409 `evidence_unavailable`, exactly as a graph-halted run does."
  - D18: "Web = `Stop.dc.html`: a Stop button (ghost, small square icon) beside the running status chip; one confirm popover "Stop this research?" (I) with "Keep going" (quiet) and "Stop research" (red text); the stopped state: chip "Stopped by you · at <step>" with a neutral grey dot, a short card "You stopped this research at HH:MM, N minutes in. No report was written…" (I) with an "Ask again" button (the same question, fresh), finished rows still openable, the stopped row with its partial facts, later rows "not run"; the note line hidden; the sidebar shows the session as stopped, not failed; works in replay mode."
  - D24: "The Stop popover keeps the canvas copy: "It stops right away and nothing more is spent. What's done so far stays here, but no report is written.""
  - D25: "Search switches to `AsyncTavilyClient`, including the retry side effect (§12 R9)."
  - D29: "A stopped session's sidebar row says "stopped by you" in its accessible name only; no visible status word (DESIGN §3.1)."
  - D33: "A stop during the one-time check shows no pipeline card."
  - D19: "DESIGN.md theme rules hold: tokens only; colour is status (green active/ok, amber warn, red danger, purple only on the one primary button); one surface per region; motion tokens; reduced motion turns movement into fades; unknown values read "not yet", never 0, —, or null."
- **API contract (spec §8.1, §8.2, §8.4), exactly:**
  - `POST /research/{session_id}/stop`, no body. **202** `ResearchSessionResponse` with `status: "stopped"` when the session is `running` or `needs_input`, not finished and not publishing. **409** `not_stoppable` with `reason` `finished` (terminal or `finished_at` set; a second stop included), `publishing` (`notes_closed`: the route decided `finalize` or `end`) or `closing` (the store closing). **404** `session_not_found` for an unknown id. `_SAFE_MESSAGES["not_stoppable"]` is "Research session can no longer be stopped."
  - `session.stopped`: source `api`, message "The reader stopped the research.", metadata `{step, stopped_at (ISO seconds), elapsed_seconds (int, now − started_at)}`; the last event of the session.
  - `SessionStatus` gains `stopped`; `ResearchSessionResponse` gains `stopped_step: str | None = None`; `TraceMetadata.status` follows. `TERMINAL_STATUSES` gains `stopped`.
  - `/report` → 409 `report_unavailable` and `/evidence` (both formats) → 409 `evidence_unavailable` when `status == "stopped"`, before the outcome checks. `/status`, `/trace` and `GET /research` answer normally.
  - The stored session keeps its events up to and including `session.stopped`, its errors (a stop adds none), notes (all `not_checked`), the check record, `stopped_step`, `iteration` and `finished_at`; no outcome, report path or trace URL.
- **Search (spec §8.3 items 1–6, D25):** a second protocol `AsyncSearchClient` (`async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]`); `client: SearchClient | AsyncSearchClient | None`; the default client `AsyncTavilyClient(api_key=api_key)`; the awaited call is the client's own coroutine when `inspect.iscoroutinefunction(self._client.search)`, else `asyncio.to_thread(search_once)`, both inside `asyncio.wait_for(..., timeout=self._timeout_s)`; the reservation stays before the call; the client is not closed at the end of a run.
- **Web copy, verbatim (`—` is U+2014, `…` is U+2026; (I) marks the spec's illustrative copy, used as written):**

  | Where | Text |
  |---|---|
  | Stop button | `Stop`, after a `span.stop-sq` |
  | Popover title (I) | `Stop this research?` |
  | Popover body (D24) | `It stops right away and nothing more is spent. What's done so far stays here, but no report is written.` |
  | Popover buttons | `Keep going` (`.btn.btn-quiet.btn-sm`), `Stop research` (`.btn.btn-sm.btn-danger`) |
  | 409 (I) | `Too late to stop — the research is finishing.`, with a `Close` button |
  | Any other failure (I) | `Couldn't stop — try again` |
  | Chip | `Stopped by you · at {step label}`; `{step label}` is the `STAGES` label, or `the questions` for `check` |
  | Stopped stage eyebrow (I) | `Stopped by you` |
  | Stopped note, first line (I) | `You stopped this research at {HH:MM}, {N} minutes in.` (`less than a minute in` under 60 s, `1 minute in`); at `check`: `You stopped this research at {HH:MM}, before it started.` |
  | Stopped note, second line (I) | `No report was written. The plan and what research found so far are kept below until the service restarts.` |
  | Button | `Ask again` |
  | Stopped row subtitle (I) | `Stopped · {its live facts}`, e.g. `Stopped · 3 of 5 topics done · 41 pages read · 212 findings`; `Stopped` for a row with no live facts |
  | Later rows (I) | `not run`, or `not run again` for rows a loop had re-armed |
  | Sidebar accessible name (I) | `{query} — stopped by you` |
  | Note outcomes (§4 item 2) | `OUTCOME_TEXT.not_checked` `not checked`; `OUTCOME_TEXT.pending` `not checked yet` (I) |
- **Theme (D19).** `web/app/globals.css` lines 1–1131 stay the prototype's CSS verbatim: `(cd web && npm run -s check:css)` prints `OK`. New rules go at the very end of the file, inside the app-only section (`/* ═══ 2026-09-27: app-only additions ═══ */`, `globals.css:1133`), with no colour literal. "Stop research" is the one red word: `--status-danger` text on a `--border` edge, never a filled button; the neutral dot is `--muted`; no purple anywhere in the control, the popover or the stopped stage.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone; captures are `fullPage: true`.
- **Out of Phase D.** No change to what a run researches or writes; no resume endpoint and no in-place re-run ("Ask again" starts a new session); no persistence across restarts; no `X-Replay-Hold-After` (Phase B, §6.10 — Open issue O1); no progress events, note helpers or report layout (Phases B, A, C); the service-stopped stage (`#stage-stopped`, `web/components/SessionScreen.tsx:208-233`) is unchanged.

### Conventions every task uses

- **Shell.** Every block runs in Git Bash from the repository root. No shell state survives between blocks: every `web/` command runs in a subshell `(cd web && …)`.
- **Line endings.** The checkout has `core.autocrlf=true`, so tracked files are CRLF on disk. Every anchor below is written with LF. Claude Code's Edit tool matches across the difference; a script must read with universal newlines (Task 1 Step 2's does). New files may be written with LF: git normalises them on commit.
- **Python.** The interpreter is `.venv/Scripts/python.exe`; `pyproject.toml` puts `src` on the path. The pytest line is `.venv/Scripts/python.exe -m pytest <files> -q`.
  - One test is deselected from every full-suite run: `tests/test_config.py::test_the_evidence_verifier_pipeline_config` loads the shipped configuration strictly (`load_settings("config.yaml")`, `tests/test_config.py:1387-1388`), which needs the real secrets. They must not be read, and without them (a worktree, an export) the test fails. Every full-suite block runs
    ```bash
    .venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
    ```
    (about 110 s). `2 deselected` is that test and the one `live` test `pyproject.toml` deselects.
- **Web unit tests.** `(cd web && npm run -s typecheck)` (Vitest's files and the e2e specs are type-checked too: `tsconfig.json` includes every `.ts`/`.tsx`), `(cd web && npx vitest run [files])`, `(cd web && npm run -s check:css)`.
- **Playwright.** The API runs in replay mode as a Playwright `webServer` (`web/playwright.config.ts:52-60`) on port 8010, with the app on 3010 and an app pointed at a closed port on 3011.
  - The config finds the venv at `../.venv/Scripts/python.exe` in the main checkout; inside a `.worktrees/*` tree set `DEEP_RESEARCH_PYTHON` to that interpreter first.
  - Build first, and put spec files **before** the project flag: `(cd web && npm run -s build && npx playwright test e2e/stop.spec.ts --project=chromium)`.
  - Captures: `(cd web && VISUAL_CHECKPOINT=<name> npx playwright test --project=visual)`, images in `web/visual/<name>/` (gitignored).
  - Ports 8010, 3010 and 3011 must be free before a run: `netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'` must print nothing. A leftover server makes the `webServer` start fail.
- **Edits.** Every edit is "replace this exact text with that text"; "Create" writes a new file; "Append to" adds the block's text at the end of the file. Task 1 proves that each anchor occurs exactly once when its turn comes. If an anchor is not found when you reach it, stop and report it. Never improvise a nearby match.
- **TDD boundary.** pytest and Vitest tests are written first and shown failing. Playwright specs are verification, written after the code they exercise (Task 9).
- **Commits.** `git add <paths> && git commit -m "<type>(<scope>): <what>"`, never `git add -A` (`web/visual/`, `web/.e2e-tmp/`, `web/test-results/` and the checkout's many untracked files must stay out). End every message with the attribution trailer your session requires, and push if your session's rules say to.

## Ground truth (read at `73b4d7a6`)

Every fact a task relies on, with where it is. Engine paths are under `src/deep_research/`.

| Fact | Where |
|---|---|
| `SessionStatus` is six values; `ResearchSessionResponse` has no `stopped_step`; `TraceMetadata.status` is a `SessionStatus`; `ReaderNoteResponse.outcome` is `covered`, `not_found`, `not_addressed`, `pending`, `replaced` | `api/models.py:35-42`, `:243-307`, `:398-403`, `:154-167` |
| `TERMINAL_STATUSES` is `completed`, `max_iterations`, `incomplete`, `failed` | `api/sessions.py:54-56` |
| `ResearchSession.publish` appends a deep copy, updates `current_agent`, `iteration`, `notes_closed` (from `graph.route.decided` to `finalize` or `end`) and `note_passes`, and sets `changed` | `api/sessions.py:132-153` |
| `add_note` refuses unless the session is `running`, not finished, not `notes_closed` and the store not closing; `submit_answers` refuses unless it is `needs_input` with a pending check before its deadline | `api/sessions.py:375-407`, `:349-373` |
| `iter_events` returns once the status is terminal, or the task is done with `finished_at` set | `api/sessions.py:445-472` |
| `close` raises the closing flag, cancels every session and note task, and sets `finished_at` on a session whose task ended without one | `api/sessions.py:474-501` |
| `_run` re-raises `CancelledError` and folds a returned outcome in its `else`; its `finally` turns `needs_input` back to `running`, clears the pending check and sets `finished_at` and `current_agent = None` unconditionally; `_record_failure` sets `failed` and publishes the failure | `api/sessions.py:553-554`, `:562-570`, `:571-577`, `:643-668` |
| `_clarify` sets `needs_input`, publishes `session.clarification.requested` and waits on the pending answers' future for at most `answer_wait_s` | `api/sessions.py:604-614`; `api/clarify.py:339-344` |
| `note_outcome` returns `pending` at three exits — no state, a note the state does not hold, no verdict; `note_records` calls it per received note | `api/notes.py:343-376` (`:357`, `:360`, `:376`), `:379-393` |
| `session_note_fields` builds `notes` from `note_records(session.note_board, state)` | `api/sessions.py:214-254` |
| The app's safe messages, `_session_response`, the notes route, `/report` (409 `session_not_complete` before an outcome, `report_unavailable` without a report) and `/evidence` (409 `session_not_complete`, `evidence_unavailable`) | `api/app.py:70-80`, `:141-155`, `:320-346`, `:396-421`, `:423-451` |
| `ReplayRunner` releases events through a queue drained by its own task, and on cancellation cancels the drain and re-raises; the start command's default delay is 150 ms | `api/replay.py:101-140`, `:123-128`; `api/__main__.py:26` |
| The search tool's client protocol is synchronous, its default is `TavilyClient(api_key=api_key)`, and each attempt reserves a unit, then runs `asyncio.to_thread(search_once)` inside `asyncio.wait_for`; timeouts and `httpx.HTTPStatusError` 429/5xx are retried | `tools/web_search.py:37-46`, `:97`, `:129-177`, `:194-202` |
| tavily-python 0.7.27 ships `AsyncTavilyClient` over `httpx.AsyncClient`; both clients map 400/401/403/429/432/433 to tavily's own errors and raise the transport's error for any other failing status | `.venv/Lib/site-packages/tavily/async_tavily.py:118-122`, `:162-171`, `:252`; `tavily/tavily.py:134-143`, `:225-232` |
| `BaseTool.execute` re-raises `RequestAttemptLimitError` and converts only `Exception`s, so a `CancelledError` passes through; the tracker's span re-raises every `BaseException` | `tools/base.py:107-165`; `observability/tracker.py:854-856` |
| LangGraph submits node tasks with `__cancel_on_exit__=True` and cancels and awaits them when its consumer exits | `.venv/Lib/site-packages/langgraph/pregel/_runner.py:471`; `_executor.py:186-205` |
| A halted node returns no completion; every later node records `graph.node.skipped` | `graph/nodes.py:189-200`, `:233-234`, `:255-274` |
| The page's active row: Planning in a fresh run state; a completion moves to the next row (not the reviewer's or a hop's); a route decision moves to its destination's row (`end` none); a skipped active row and the session's completion leave none | `web/lib/run-state.ts:91`, `:183-192`, `:193-196`, `:251-274`, `:321-325` |
| `stepLabel` maps a node (or a hop) to its `STAGES` label; `chipStep` names the chip's step | `web/lib/run-state.ts:16-24`, `:119-134` |
| `web/lib/run-state.ts` imports `fmtScore` from `web/lib/format.ts` | `web/lib/run-state.ts:8` |
| `SessionScreen`'s stage order: not found → loading → service-stopped (`running` with `finished_at`) → Submitted → check → Running → Failed → Report; it computes the chip's step for a live session only | `web/components/SessionScreen.tsx:154-165`, `:194-205`, `:208-233` |
| The topbar holds the status chip and the replay chip; the sidebar names only live rows | `web/components/Topbar.tsx:16-19`; `web/components/Sidebar.tsx:49-54` |
| `BriefSpine`'s props are `marks`, `run`, `onToggle`; a done or loop row has a toggle, the active row is open | `web/components/BriefSpine.tsx:17`, `:58-80` |
| `STATUS` maps each status to a label and a dot class; `.chip .dot` is `--muted` by default | `web/lib/format.ts:24-31`; `web/app/globals.css:187` |
| `p{margin:0}`; the app-only header; the reduced-motion block redefines `enter` as a fade and zeroes durations | `web/app/globals.css:63`, `:1133`, `:991-1028` |
| `waitTerminal` and the capture script list four terminal statuses | `web/e2e/support.ts:47-50`; `web/scripts/capture-replay-events.mjs:11-12` |
| Playwright runs the API in replay mode on 8010 with the app on 3010 and a dead app on 3011; the default interpreter is the checkout's venv | `web/playwright.config.ts:11`, `:52-60` |
| The full backend suite at `73b4d7a6`, `test_the_evidence_verifier_pipeline_config` deselected: `4960 passed, 2 deselected`; Vitest: 28 files, 219 tests; Playwright lists 61 Chromium and 12 visual tests | observed in planning (Task 1 Step 3) |

## Review Focus

1. **The step a stop records is the row the page shows** (AC32). `active_row` must be `run.active` on every prefix of every captured replay, and on the cases the captures do not reach (a hop, a halt's skipped rows, an `end` decision). → Task 3 `test_active_row_matches_web_rule`, `test_active_row_follows_the_consoles_rule` and `test_replay_stop_mid_stream`; Task 6 `web/test/active-row.test.ts`, which pins the page's rule to the same file.
2. **Nothing after the stop.** `session.stopped` is last whatever a task finishing its own cancellation hands over, no later node starts, nothing is published, and a runner that swallows its cancellation or fails in it cannot turn the stop into `completed` or `failed`. → Task 3 `test_publish_after_stop_dropped`, `test_a_run_that_swallows_its_cancellation_keeps_the_stop`; Task 5 `test_stop_cancels_inflight_calls`.
3. **Cancellation reaches what is in flight** (AC29, D25). A stub provider call and an async search inside the running node observe `CancelledError` within 1 s of the stop; a synchronous injected client still works off the loop; a 5xx is now retried. → Task 5's four search tests and `test_stop_cancels_inflight_calls`.
4. **The 409s and the halted answers** (AC30, AC31). Order of reasons, a second stop, the store closing, and `/report`, `/evidence`, notes and answers on a stopped session. → Task 3 store tests; Task 4 `test_stop_route_codes`.
5. **No note reads `pending` once a session has ended** (§4 item 2). → Task 2.
6. **The web flow.** Stop is offered from the check through Reviewing and gone while Publishing; the popover's focus, Escape, outside click, 409 and failure faces; the stopped stage's note, frozen rows and Ask again; the chip and the sidebar's accessible name. → Tasks 6–8 Vitest; Task 9 Playwright and captures.

## Spec ambiguities resolved here

**API**

1. **Where the new pieces live.** `api/stop.py` (new) holds `active_row`, `session_stopped_event`, `PIPELINE_ROWS` and `CHECK_STEP`; `NotStoppable` (with `StopRefusal`) sits in `api/sessions.py` beside `NotesClosed` and `NotWaitingForInput`, the store's other 409 exceptions.
2. **The 409 reasons' order.** `finished` (terminal, or `finished_at` set) is checked first, then `publishing` (`notes_closed`), then `closing`. So a second stop reads `finished`, and a session the store is closing while it publishes reads `publishing`. §8.1 lists the three conditions without an order.
3. **`active_row` is `run.active` exactly.** §8.2 step 2 summarises the rule; the page's handlers (`web/lib/run-state.ts:183-192`, `:193-196`, `:251-274`, `:321-325`) also leave no active row after a `graph.node.skipped` for the active row, after `graph.session.completed`, after Publishing's completion and after an `end` decision, and a completion for a node that is no row leaves none. `active_row` mirrors every one, so the two cannot differ (AC32). In a session that can still be stopped no such event has arrived: each one follows the route's `finalize` or `end` decision, which closes notes, or a halt, whose skipped rows are never the active one (`graph/nodes.py:233-234`, `:255-274`: the halting node published its start and returns no completion; every later node is skipped). `stop` therefore treats a `None` row as `publishing`, which it is in every reachable state; this is a restatement, not a new refusal.
4. **A stop while the one-time check's call runs** (status `running`, no event yet) records `planner`: that is the page's active row then (`newRunState().active`, `web/lib/run-state.ts:91`), and the page shows the running stage with Planning active (DESIGN.md §3, "There is no `checking` status").
5. **The cross-language pin for AC32.** `web/test/fixtures/active-rows.json` (new, Task 3) holds, for each captured replay, the page's active row after every prefix of its events, run-length encoded (`[[row, count], …]`). It is the page's own rule's output: planning computed it with `web/lib/run-state.ts` itself, and Task 6's Vitest test recomputes it from the fixtures on every run. Task 3's pytest compares `active_row` with the same file. When Phase B re-captures the fixtures (§6.10 item 3), the Vitest test fails until the file is rewritten with `WRITE_ACTIVE_ROWS=1` (Open issue O3).
6. **A run that ignores its cancellation cannot undo a stop.** `_run`'s `else` branch folds an outcome only when the session is not `stopped`, and `_record_failure` records nothing on a stopped session. A runner that catches the stop's `CancelledError` and returns, or raises in its handler, otherwise turns a stopped session into `completed` or `failed` with a report — against §8.4's "keeps no outcome". No production runner does this today; the guard costs one comparison each.

**Web**

7. **The chip's step label rides in `SessionView.step`.** §8.4 says `SessionView` gains `stoppedStep` and `statusNote` reads "at {step label}". `web/lib/format.ts` cannot turn a step id into its label without importing `web/lib/run-state.ts`, which already imports `format.ts` (`web/lib/run-state.ts:8`). So `SessionScreen` passes the stopped step's label as the `step` argument of `toSessionView` — the field that already carries the running row's label for the same chip — and `statusNote` reads `at {step}`, or `step not recorded` when the response names none (DESIGN §4 rule 4's vocabulary for a missing value). The chip's text is the spec's; no second field holds the same thing. This departs from §8.4's wording, so it is also Open issue O7, for the human to confirm or overrule.
8. **The step's label** is `stoppedStepLabel(step)` in `web/lib/stop.ts` (new): `the questions` for `check`, else `stepLabel(step)` from `STAGES`.
9. **The popover on a phone.** At ≤ 480 px the popover is fixed under the topbar between the phone gutters (`--container-gutter-phone`, 16 px) instead of hanging 320 px wide from the button's right edge: in replay mode the mode chip sits to the button's right, so a right-aligned 320 px box would start left of the viewport at 390 px. Above 480 px it is §8.5's anchored, right-aligned 320 px box.
10. **The popover's focus and faces.** "Keep going" takes focus on open (§8.5); after a 409, `Close` takes it; after any other failure, `Stop research` takes it back once re-enabled. While the POST is in flight Escape and an outside click do nothing, so the answer cannot land on a closed popover. The failure line is a `.cap` with `role="alert"`, as the note line's `Couldn't send — try again` is. The 409 face keeps the title and replaces the body and the two buttons ("replaces the body").
11. **Focus after a stop.** The Stop control unmounts with the running stage; the stopped stage moves focus to its first line (`p.b-now`, `tabIndex=-1`, no ring) when focus was left on the page body, as the clarify card moves focus to the line that replaces its buttons (DESIGN §3, stage 2a).
12. **The stopped row's live facts.** Before Phase B only Researching has live facts. Its stopped subtitle always uses the counted form — `Stopped · {done} of {n} topics done · {pages} · {findings}` — because the running form for no finished topic (`3 topics · researching`) would contradict "Stopped"; with no topic done it reads `none of 3 topics done`, never a bare 0 (live-briefs AC5's rule for a measured count). A Researching row with no topics yet, and every other row, reads `Stopped`.
13. **The frozen brief.** The stopped row is openable to its brief (§8.5); a topic that was running reads `stopped` with the ring (`data-topic="stopped"`, which no running or done rule styles, so the ring shows). The stopped row's screen-reader suffix is ` (stopped here)` (I), beside the active row's existing ` (in progress)`.
14. **Ask again.** One `POST /research` with `buildRequest(status.query, submission?.settings ?? DEFAULT_SETTINGS)` (`web/components/Composer.tsx:21-30`), never retried; the submission is recorded with the default Submitted beat (`recordSubmission`, `web/lib/session-store.ts:20`), so the new session opens on its usual beat without the composer's flight; then `refreshSessions()` and navigation. A failed POST shows `Couldn't ask again — try again` (I) and re-enables the button.
15. **Until the stream delivers `session.stopped`** — the page adopts the 202 response at once — the stopped note reads its time from the response's `finished_at` and its minutes from `finished_at − started_at`, the same instants `session.stopped` carries.
16. **The service-stopped stage keeps its name.** DESIGN.md's three mentions of "the Failed and Stopped stages" (`:240`, `:721`, `:1636`) mean the compact-spine stage of a service shutdown; Task 7 calls it "service-stopped" there, because the new stage is also "stopped" and uses the brief spine.

**Tests**

17. **The e2e 409 face is driven by a fulfilled route.** The page hides Stop the moment its stream shows Publishing, and the API refuses a stop from that same event, so a real 409 can reach the popover only inside a few milliseconds. `stop.spec.ts` proves the API's 409 after the run ends with a real request, and the popover's 409 face with `page.route` answering the POST with the API's own 409 body; `stop-control.test.tsx` covers the same face in Vitest.
18. **Test files.** The store, rule, replay and route tests are `tests/test_api/test_stop.py` (Tasks 3–4); the in-flight test, which needs the graph and the search tool, is `tests/test_api/test_stop_inflight.py` (Task 5).
19. **The end-to-end notes test follows §4 item 2 in Task 2.** `web/e2e/notes.spec.ts:109-110` expects a finished replay session's two notes to read `pending` on `/status`. §4 item 2 makes them `not_checked` (the session has ended and nothing judged them), so Task 2 changes those two values, although §11.2 lists that file only under Phase C, for its report block. The captions the test reads stay `not checked`.

## Open issues (for the human)

- **O1 — §8.6 asks D's README to mention `X-Replay-Hold-After`, which Phase B adds.** §6.10 item 2 (Phase B, AC21, `test_replay_restamps_and_holds`) defines the header; §9 lands D first. A README sentence about a header that does not exist would be false, so this plan documents Stop only (Task 9), and the header's README sentence belongs with Phase B's §6.10 work. Phase B's plan, written in parallel, should add it; please make sure it does.
- **O2 — A shared page fetch in flight (§8.3, D38).** "Whichever of the two lands second adds a shared fetch in flight to `test_stop_cancels_inflight_calls`." `perf/latency` is at `0b5f3515` (the audit only), so no shared fetch exists to test, and this plan cannot give that test's code. If the latency work merges before this plan runs, Task 1 Step 1 flags it (its tool-lock line prints `0`) and Phase D goes on: Task 5 Step 1 then extends `test_stop_cancels_inflight_calls` with a shared fetch in flight, written against that work's own helpers to the contract stated there (review round 1, M3).
- **O3 — Phase B must rewrite `web/test/fixtures/active-rows.json` when it re-captures the fixtures.** The file is the page's active row after every event of each capture; progress events move no row but shift every count. Phase B's re-capture step should run `(cd web && WRITE_ACTIVE_ROWS=1 npx vitest run test/active-row.test.ts)`, review the diff (only counts may change), then run both AC32 tests.
- **O4 — Playwright was not run in planning** (see Evidence). Task 9's specs and the captures are verification steps for the executor; their Expected lines are reasoned, not observed. The likeliest to differ is the phone topbar: at 390 px it now holds the status chip, Stop and `replay mode` on one row that does not wrap (`web/app/globals.css:1159-1160`), where only the chip's note shrinks (`:1083-1085`) and Stop does not (`flex:none`, Task 8). The longest label, `Waiting for you`, may leave Stop too little room [INFERENCE: `e2e/layout.spec.ts`'s phone run, `e2e/stop.spec.ts`'s phone test and Task 9 Step 6's review of `10-clarify-phone` prove or falsify it]. If it does, the executor reports it (Task 9 Step 6) rather than changing the layout unreviewed.
- **O5 — The live Tavily transport is [INFERENCE] until a live run.** Every test drives fakes; `AsyncTavilyClient` posts through `httpx.AsyncClient` (`.venv/Lib/site-packages/tavily/async_tavily.py:118-122`, `:252`), and R9's 5xx retry is the tool's own rule. The first live run after this phase (outside this plan, under the existing spend rules) settles it: watch for search failures.
- **O6 — Phases A, C and B must anchor on the text D leaves.** §9 lands D first, and D rewrites text the later phases also edit. A later plan anchored on `73b4d7a6` will miss its anchors in these places, and its own anchor check stops it there:
  - `src/deep_research/api/notes.py`: `NoteOutcome` gains `not_checked`; `note_outcome(note_id, state, *, terminal)`, with a new docstring and three `waiting` exits; `note_records(board, state, *, terminal)` (Task 2). Phase A replaces `note_outcome`'s body (§4 item 2), so it starts from Task 2's text.
  - `tests/test_api/test_notes.py`: `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending` (`:189`) keeps its name, so a later plan that selects it by name still finds it, but its body asserts both `terminal` values; `test_the_records_list_every_accepted_note_in_order_read_or_not` passes `terminal`; `test_the_note_shapes_trim_bound_and_default_as_the_spec_says` gains a `not_checked` assertion; `web/e2e/notes.spec.ts:109-110` expects `not_checked` (Task 2, ambiguity 19). No function is added between `_finished` and `test_the_note_shapes_trim_bound_and_default_as_the_spec_says`, between `note_outcome` and `__all__` in `api/notes.py`, or after `outcome` in `ReaderNoteResponse`; `session_note_fields` gains one name, `terminal`, between `state` and `check`.
  - `tests/test_api/test_note_route.py`: its import line (`:27`) becomes `from deep_research.api.sessions import NotesClosed, SessionStore, session_note_fields`, and two tests are appended after `:520`, `test_a_note_nothing_judged_reads_pending_while_the_session_runs_and_not_checked_once_it_ends` and `test_a_session_closed_out_by_a_shutdown_reads_its_notes_not_checked`. `web/test/notes.test.ts:72-75` expects `pending: "not checked yet", not_checked: "not checked"`, and `web/test/components/reader-notes.test.tsx:68`'s third note reads `outcome: "not_checked" as const` (all Task 2).
  - `src/deep_research/api/models.py` (`SessionStatus`, `ReaderNoteResponse`, `ResearchSessionResponse`), `src/deep_research/api/sessions.py` (`TERMINAL_STATUSES`, `ResearchSession`, `publish`, `session_note_fields`, `_run`, `_record_failure`) and `src/deep_research/api/app.py` (`_SAFE_MESSAGES`, `_session_response`, `/report`, `/evidence`), Tasks 2–4.
  - `web/lib/api.ts`, `web/lib/notes.ts`, `web/lib/format.ts`, `web/lib/run-state.ts`, `web/lib/briefs.ts`, `web/components/BriefSpine.tsx`, `web/components/SessionScreen.tsx`, `web/components/ConsoleProvider.tsx`, `web/components/Topbar.tsx`, the end of `web/app/globals.css`, `web/e2e/support.ts`, `web/scripts/capture-replay-events.mjs`, `web/e2e/visual.spec.ts`, `README.md`, `web/README.md`, `docs/design/DESIGN.md` and `docs/design/api-gaps.md` (Tasks 2 and 4–9); and `web/test/fixtures/active-rows.json` (O3).

  Please have each later plan re-run its anchor check on D's merged tree before it starts.
- **O7 — The chip's stopped step rides in `SessionView.step`, not in a new `SessionView.stoppedStep` (§8.4).** §8.4 has `toSessionView` copy `status.stopped_step` into a new field and `statusNote` print its label. The label comes from `STAGES` (§4 item 3) in `web/lib/run-state.ts`, which imports `fmtScore` from `web/lib/format.ts` (`web/lib/run-state.ts:8`), so `format.ts` reading it would make the two modules import each other. This plan has `SessionScreen` pass `stoppedStepLabel(status.stopped_step)` as `toSessionView`'s existing `step` argument instead (ambiguity 7). The chip reads `Stopped by you · at {step label}` either way; `format.test.ts`, `status-chip.test.tsx`, `user-stopped-stage.test.tsx` and `stop.spec.ts` pin it. If the field itself is wanted, the change stays inside Task 6: add `stoppedStep: string | null` to `SessionView`, set it from `s.stopped_step ?? null` in `toSessionView`, import `stoppedStepLabel` into `format.ts` for `statusNote`, and accept the import cycle `format.ts` → `stop.ts` → `run-state.ts` → `format.ts`. Each module uses the others' exports only inside functions, so no module needs another while it loads [INFERENCE: the full Vitest run and `npm run -s build` would prove or falsify it].

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/api/notes.py` | `NoteOutcome` gains `not_checked`; `note_outcome(..., *, terminal)` and `note_records(..., *, terminal)` | 2 |
| `src/deep_research/api/models.py` | `ReaderNoteResponse.outcome` gains `not_checked` (2); `SessionStatus` gains `stopped`, `ResearchSessionResponse.stopped_step` (3) | 2, 3 |
| `src/deep_research/api/stop.py` (new) | `PIPELINE_ROWS`, `CHECK_STEP`, `active_row`, `session_stopped_event` | 3 |
| `src/deep_research/api/sessions.py` | `terminal` for note outcomes (2); `TERMINAL_STATUSES` + `stopped`, `StopRefusal`, `NotStoppable`, `ResearchSession.stopped_step`, `publish` drops after a stop, `SessionStore.stop`, `_run`/`_record_failure` keep a stop (3) | 2, 3 |
| `src/deep_research/api/app.py` | `POST /research/{id}/stop`, `not_stoppable`, `stopped_step` on the response, `/report` and `/evidence` for a stopped session | 4 |
| `src/deep_research/tools/web_search.py` | `AsyncSearchClient`, the default `AsyncTavilyClient`, the awaited or threaded attempt | 5 |
| `tests/test_api/test_notes.py`, `tests/test_api/test_note_route.py`, `tests/test_api/test_sessions.py` | the `terminal` outcomes (2); `stopped` among the statuses (3) | 2, 3 |
| `tests/test_api/test_stop.py` (new), `tests/test_api/test_stop_inflight.py` (new) | store, rule and replay tests (3), route tests (4); the in-flight test (5) | 3, 4, 5 |
| `tests/test_tools/test_web_search.py`, `tests/test_evaluation/test_dependencies_controlled.py` | the async client, cancellation, the thread path, the 5xx retry; controlled mode never builds either Tavily client | 5 |
| `web/test/fixtures/active-rows.json` (new) | the page's active row after every prefix of each captured replay | 3 |
| `web/lib/api.ts` | `ReaderNoteOutcome` + `not_checked` (2); `SessionStatus` + `stopped`, `stopped_step`, `stopResearch` (6) | 2, 6 |
| `web/lib/notes.ts` | `OUTCOME_TEXT.not_checked`, `.pending` "not checked yet" | 2 |
| `web/lib/format.ts`, `web/lib/run-state.ts`, `web/lib/stop.ts` (new) | `STATUS.stopped`, `statusNote`; `RunState.stopped` and `session.stopped`; the stop copy, step label, clock and minutes | 6 |
| `web/components/Sidebar.tsx` | a stopped row's accessible name | 6 |
| `web/lib/briefs.ts`, `web/components/BriefSpine.tsx`, `web/components/UserStoppedStage.tsx` (new) | the frozen spine and the stopped stage | 7 |
| `web/components/SessionScreen.tsx` | the stage choice and the chip's label (7); offering Stop (8) | 7, 8 |
| `web/components/ConsoleProvider.tsx`, `web/components/Topbar.tsx`, `web/components/StopControl.tsx` (new) | the Stop target, the control and its confirmation | 8 |
| `web/app/globals.css` | the stopped stage (7); the control and popover (8) | 7, 8 |
| `web/test/*.test.ts`, `web/test/components/*.test.tsx` | Vitest for each of the above | 2, 6, 7, 8 |
| `web/e2e/notes.spec.ts` | a finished replay session's notes read `not_checked` on `/status` | 2 |
| `web/e2e/support.ts`, `web/scripts/capture-replay-events.mjs`, `web/e2e/stop.spec.ts` (new), `web/e2e/visual.spec.ts` | `stopped` is terminal; Stop end to end; the `19-stop-confirm` and `20-stopped` captures | 9 |
| `README.md`, `docs/design/api-gaps.md`, `docs/design/DESIGN.md`, `web/README.md` | note outcome wording (2); the route and the closed cancel gap (4); the stage, status and sidebar (7); the control (8); running the app (9) | 2, 4, 7, 8, 9 |

---

### Task 1: Starting point and baselines

**Files:** none changed. This task only reads the checkout and this plan.

**Interfaces:**
- Consumes: the branch `feat/notes-progress-report-stop` at `73b4d7a6` or later, with no code change after `f4282818` (spec header) other than, possibly, the latency work (Step 1).
- Produces: the proof that every block of Tasks 2–9 applies in order — each anchor exactly once when its turn comes, each created file absent, each appended file present — and the baselines later tasks add to.

- [ ] **Step 1: Confirm the starting point**

```bash
git log --oneline -1 73b4d7a6
git merge-base --is-ancestor 73b4d7a6 HEAD && echo "the spec commit is in this branch"
git diff --stat f4282818 HEAD -- src tests web | tail -1
git status --short --untracked-files=no | wc -l
test ! -e src/deep_research/api/stop.py && test ! -e web/components/StopControl.tsx && test ! -e web/components/UserStoppedStage.tsx && echo "no Phase D file yet"
grep -c "not_checked" src/deep_research/api/notes.py web/lib/api.ts
grep -c "One tool lock for the whole run" src/deep_research/agents/researcher.py
```

Expected, line by line:
- `73b4d7a6 docs(spec): apply Fable review 2 -- unstarted note topics owed their pass, hard-failure refusal wording, five-check meta`
- `the spec commit is in this branch`
- nothing (the diff stat is empty: no code, test or web change since `f4282818`), unless the latency work merged first (the last line)
- `0` (no modified tracked file; the checkout's many untracked files are not counted)
- `no Phase D file yet`
- `src/deep_research/api/notes.py:0` and `web/lib/api.ts:0` (no other phase has landed: Phase A would add `not_checked` too)
- `1` (the researcher's run-wide tool lock is still there: the latency work, D38, has not merged)

If the last line prints `0`, the latency work merged first. Do not stop: say so in the task summary, accept its change in the diff stat, and do the shared-fetch addition in Task 5 Step 1 (Open issue O2). If any other line differs, stop and report it: this plan starts on `main` plus the spec, and at most the latency work.

- [ ] **Step 2: Check every block of this plan against the tree, in order**

The check simulates the whole plan in memory — later anchors are checked against the text earlier blocks write — and changes nothing on disk. It reads every file with universal newlines, so the checkout's CRLF files match this plan's LF anchors.

```bash
.venv/Scripts/python.exe - <<'EOF'
import re
from pathlib import Path

plan = Path("docs/superpowers/plans/2026-09-30-phase-d-stop.md").read_text(encoding="utf-8")
edit = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? — replace\n\n(?P<f1>`{3,5})[a-z]*\n(?P<old>.*?)\n(?P=f1)\n\nwith\n\n(?P<f2>`{3,5})[a-z]*\n(?P<new>.*?)\n(?P=f2)\n", re.S | re.M)
create = re.compile(r"^Create `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,5})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
append = re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,5})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
blocks = sorted(((m.start(), kind, m) for kind, rx in (("edit", edit), ("create", create), ("append", append)) for m in rx.finditer(plan)), key=lambda b: b[0])
files, problems, counts = {}, [], {"edit": 0, "create": 0, "append": 0}
def text(path):
    if path not in files:
        files[path] = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else None
    return files[path]
for _, kind, m in blocks:
    path, current = m["path"], text(m["path"])
    counts[kind] += 1
    if kind == "edit":
        found = -1 if current is None else current.count(m["old"])
        if found != 1:
            problems.append(f"{path}: anchor found {found} times: {m['old'][:70]!r}")
            continue
        files[path] = current.replace(m["old"], m["new"])
    elif kind == "create":
        if current is not None:
            problems.append(f"{path}: already exists")
        files[path] = m["body"] + "\n"
    elif current is None:
        problems.append(f"{path}: missing for its append")
    else:
        files[path] = current + m["body"] + "\n"
print("\n".join(problems) or f"anchors: {counts['edit']} exactly once; creates: {counts['create']} absent; appends: {counts['append']} onto files present")
EOF
```

Expected: `anchors: 118 exactly once; creates: 12 absent; appends: 11 onto files present`.

Anything else is a line per problem (`-1` means that the file is missing). Then stop, edit nothing, and report each line to the controller. The remedy is a plan amendment the controller approves: re-anchor that edit on the text now in the file and keep its replacement's meaning. Never guess an anchor during execution.

- [ ] **Step 3: Record the suite baselines**

```bash
.venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config 2>&1 | tail -1
```

Expected: `4960 passed, 2 deselected, 2 warnings in 107.37s (0:01:47)` — the counts must match; the time varies.

```bash
(cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css)
```

Expected: no `typecheck` output; `Test Files  28 passed (28)`, `Tests  219 passed (219)`; `OK`.

```bash
(cd web && npx playwright test --list --project=chromium | tail -1 && npx playwright test --list --project=visual | tail -1)
```

Expected: `Total: 61 tests in 19 files`, then `Total: 12 tests in 1 file`. Listing starts no server.

- [ ] **Step 4: Record the per-file baselines the scoped runs build on**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api -q 2>&1 | tail -1
.venv/Scripts/python.exe -m pytest tests/test_tools/test_web_search.py tests/test_evaluation/test_dependencies_controlled.py -q 2>&1 | tail -1
```

Expected, one line each (the times vary): `227 passed, 2 warnings in 24.43s`; `53 passed, 1 warning in 0.66s`.

If a baseline differs from the Expected above — because a later commit added or removed tests — record the observed value and use it: every later count shifts by the same amount. What must hold is that the named new tests pass (or, in a fail-first step, fail) and nothing else fails. The increments are:

| Suite | Increments by task |
|---|---|
| Backend | +2 (Task 2), +9 (Task 3), +3 (Task 4), +5 (Task 5): +19 |
| Vitest | +0 (Task 2), +14 with 2 new files (Task 6), +9 with 1 new file (Task 7), +9 with 1 new file (Task 8): +32 tests, +4 files |
| Chromium | +7 (Task 9) |
| Visual | +2 (Task 9) |

If a baseline run **fails**, stop and report it: Phase D starts from a green `main`.

- [ ] **Step 5: No commit**

Nothing changed. Task 1's summary lists the anchor line and the baselines.

---

### Task 2: A note nothing judged reads `not_checked` once its session has ended (spec §4 item 2)

**Files** (line numbers locate the anchors at `73b4d7a6`):
- Modify: `src/deep_research/api/notes.py:53-55` (`NoteOutcome`), `:343-360` (`note_outcome`'s head), `:374-393` (its last exit, and `note_records`); `src/deep_research/api/models.py:157-167` (`ReaderNoteResponse`); `src/deep_research/api/sessions.py:222-233` (`session_note_fields`); `web/lib/api.ts:41-44` (`ReaderNoteOutcome`); `web/lib/notes.ts:67-76` (`OUTCOME_TEXT`); `README.md:839-844`, `:923`; `docs/design/api-gaps.md:127`
- Test: `tests/test_api/test_notes.py:189-215`, `:225-226`; `tests/test_api/test_note_route.py:27`, appended after `:520`; `web/test/notes.test.ts:72-75`; `web/test/components/reader-notes.test.tsx:68`; `web/e2e/notes.spec.ts:109-110`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `deep_research.api.notes`: `NoteOutcome = Literal["covered", "not_found", "not_addressed", "pending", "not_checked", "replaced"]`; `note_outcome(note_id: str, state: ResearchState | None, *, terminal: bool) -> NoteOutcome`; `note_records(board: NoteBoard, state: ResearchState | None, *, terminal: bool) -> list[tuple[ReceivedNote, str | None, NoteOutcome]]`. `terminal` has no default: every caller says whether the session has ended.
  - `ReaderNoteResponse.outcome` accepts `not_checked`.
  - `session_note_fields(session)` passes `terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None` (spec §5.6), so once `stopped` joins `TERMINAL_STATUSES` (Task 3) a stopped session's notes read `not_checked` with no further change.
  - Web: `ReaderNoteOutcome` gains `"not_checked"`; `OUTCOME_TEXT.not_checked === "not checked"`, `OUTCOME_TEXT.pending === "not checked yet"`. `web/e2e/notes.spec.ts` expects a finished replay session's two notes to read `not_checked` on `/status`.

- [ ] **Step 1: Write the failing API tests**

`tests/test_api/test_notes.py` — replace

```python
def test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending() -> None:
    """A note the report still ignores after its one redraft is ``not_addressed``, never
    ``covered`` (live-briefs Phase 3, open issue O8)."""
    state = _finished(
        fake_reader_note("n1"), fake_reader_note("n2"), fake_reader_note("n3"),
        fake_reader_note("n4", replaces="n3", reviewed=True, redrafted=True), fake_reader_note("n5"),
        verdicts={"n1": "honoured", "n2": "no_evidence", "n4": "ignored_with_evidence"},
    )

    assert [note_outcome(f"n{i}", state) for i in range(1, 7)] == [
        "covered", "not_found", "replaced", "not_addressed", "pending", "pending",
    ]
    assert note_outcome("n1", None) == "pending"


def test_the_records_list_every_accepted_note_in_order_read_or_not() -> None:
    board = NoteBoard()
    board.receive("first", received_at=RECEIVED.received_at, received_during="planner")
    board.receive("second", received_at=RECEIVED.received_at, received_during="researcher")
    board.add(fake_reader_note("n1", restatement="more weight on fire-safety standards"))

    records = note_records(board, None)

    assert [(r.note_id, r.text, restatement, outcome) for r, restatement, outcome in records] == [
        ("n1", "first", "more weight on fire-safety standards", "pending"),
        ("n2", "second", None, "pending"),
    ]
```

with

```python
def test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending() -> None:
    """A note the report still ignores after its one redraft is ``not_addressed``, never
    ``covered`` (live-briefs Phase 3, open issue O8). A note nothing judged waits — no state,
    a note the state does not hold, or no verdict: ``pending`` while its session goes on,
    ``not_checked`` once it has ended (notes-progress-report spec §4 item 2)."""
    state = _finished(
        fake_reader_note("n1"), fake_reader_note("n2"), fake_reader_note("n3"),
        fake_reader_note("n4", replaces="n3", reviewed=True, redrafted=True), fake_reader_note("n5"),
        verdicts={"n1": "honoured", "n2": "no_evidence", "n4": "ignored_with_evidence"},
    )

    assert [note_outcome(f"n{i}", state, terminal=False) for i in range(1, 7)] == [
        "covered", "not_found", "replaced", "not_addressed", "pending", "pending",
    ]
    assert [note_outcome(f"n{i}", state, terminal=True) for i in range(1, 7)] == [
        "covered", "not_found", "replaced", "not_addressed", "not_checked", "not_checked",
    ]
    assert (note_outcome("n1", None, terminal=False), note_outcome("n1", None, terminal=True)) == (
        "pending", "not_checked",
    )


def test_the_records_list_every_accepted_note_in_order_read_or_not() -> None:
    board = NoteBoard()
    board.receive("first", received_at=RECEIVED.received_at, received_during="planner")
    board.receive("second", received_at=RECEIVED.received_at, received_during="researcher")
    board.add(fake_reader_note("n1", restatement="more weight on fire-safety standards"))

    running = note_records(board, None, terminal=False)
    ended = note_records(board, None, terminal=True)

    assert [(r.note_id, r.text, restatement, outcome) for r, restatement, outcome in running] == [
        ("n1", "first", "more weight on fire-safety standards", "pending"),
        ("n2", "second", None, "pending"),
    ]
    assert [outcome for _received, _restatement, outcome in ended] == ["not_checked", "not_checked"]
```

`tests/test_api/test_notes.py` — replace

```python
    with pytest.raises(ValidationError):
        ReaderNoteResponse(note_id="n1", text="t", outcome="lost")
```

with

```python
    with pytest.raises(ValidationError):
        ReaderNoteResponse(note_id="n1", text="t", outcome="lost")
    assert ReaderNoteResponse(note_id="n1", text="t", outcome="not_checked").outcome == "not_checked"
```

`tests/test_api/test_note_route.py` — replace

```python
from deep_research.api.sessions import NotesClosed, SessionStore
```

with

```python
from deep_research.api.sessions import NotesClosed, SessionStore, session_note_fields
```

Append to `tests/test_api/test_note_route.py`:

```python


# --- notes-progress-report spec §4 item 2: no session that has ended reports ``pending`` ---------


def test_a_note_nothing_judged_reads_pending_while_the_session_runs_and_not_checked_once_it_ends() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        client.post(f"/research/{session_id}/notes", json={"text": "Pumped hydro"})
        running = _wait(client, session_id, lambda body: body["notes"][0]["restatement"] is not None)
        client.portal.call(runner.release.set)
        done = _wait(client, session_id, lambda body: body["status"] == "completed")

    assert [(note["note_id"], note["outcome"]) for note in running["notes"]] == [("n1", "pending")]
    assert [(note["note_id"], note["outcome"]) for note in done["notes"]] == [("n1", "not_checked")]


@pytest.mark.asyncio
async def test_a_session_closed_out_by_a_shutdown_reads_its_notes_not_checked() -> None:
    """A service shutdown leaves a session ``running`` with ``finished_at`` set: it has ended too."""
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    store.add_note("s1", "Pumped hydro")
    await _until(lambda: len(session.note_board.snapshot()) == 1)

    assert [note.outcome for note in session_note_fields(session)["notes"]] == ["pending"]
    await store.close()
    assert (session.status, session.finished_at is not None) == ("running", True)
    assert [note.outcome for note in session_note_fields(session)["notes"]] == ["not_checked"]
```

`GateRunner`'s finished state holds no note (`tests/test_api/fakes.py:143`, `:183-190`: `make_outcome` builds a state with no `reader_notes`, `:44-52`), so its session's note has nothing to be judged by; `HeldRunner` is held open until the store closes.

- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_notes.py tests/test_api/test_note_route.py -q 2>&1 | tail -8
```

Expected — five failures, then the count. Each `FAILED` line ends with its reason when the terminal is wide enough; a narrow one cuts it short.

```
FAILED tests/test_api/test_notes.py::test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending - TypeError: note_outcome() got an unexpected keyword argument 'terminal'
FAILED tests/test_api/test_notes.py::test_the_records_list_every_accepted_note_in_order_read_or_not - TypeError: note_records() got an unexpected keyword argument 'terminal'
FAILED tests/test_api/test_notes.py::test_the_note_shapes_trim_bound_and_default_as_the_spec_says - pydantic_core._pydantic_core.ValidationError: 1 validation error for ReaderNoteResponse
FAILED tests/test_api/test_note_route.py::test_a_note_nothing_judged_reads_pending_while_the_session_runs_and_not_checked_once_it_ends - AssertionError: assert [('n1', 'pending')] == [('n1', 'not_checked')]
FAILED tests/test_api/test_note_route.py::test_a_session_closed_out_by_a_shutdown_reads_its_notes_not_checked - AssertionError: assert ['pending'] == ['not_checked']
5 failed, 44 passed, 1 warning in 0.92s
```

- [ ] **Step 3: Implement the terminal rule in the API**

`src/deep_research/api/notes.py` — replace

```python
NoteOutcome: TypeAlias = Literal[
    "covered", "not_found", "not_addressed", "pending", "replaced"
]
```

with

```python
NoteOutcome: TypeAlias = Literal[
    "covered", "not_found", "not_addressed", "pending", "not_checked", "replaced"
]
"""``pending`` only while a session goes on; ``not_checked`` once it has ended with nothing
to judge the note by (notes-progress-report spec §4 item 2)."""
```

`src/deep_research/api/notes.py` — replace

```python
def note_outcome(note_id: str, state: ResearchState | None) -> NoteOutcome:
    """What the finished run concluded about one note.

    ``covered`` when the last review judged that the report follows it
    (``honoured``); ``not_found`` when it found no evidence bearing on it;
    ``not_addressed`` when it judged it ``ignored_with_evidence`` — the
    findings bore on the note and the report still does not follow it, after
    its one redraft or because the run ended before it — which is never
    ``covered`` (live-briefs Phase 3, open issue O8); ``replaced`` when a later
    note replaced it; and ``pending`` while the run is going or when no review
    judged it (a note that arrived too late, or a review that could not be
    made).
    """
    if state is None:
        return "pending"
    notes = [note for note in state.reader_notes if note.note_id == note_id]
    if not notes:
        return "pending"
```

with

```python
def note_outcome(
    note_id: str, state: ResearchState | None, *, terminal: bool
) -> NoteOutcome:
    """What the run concluded about one note.

    ``covered`` when the last review judged that the report follows it
    (``honoured``); ``not_found`` when it found no evidence bearing on it;
    ``not_addressed`` when it judged it ``ignored_with_evidence`` — the
    findings bore on the note and the report still does not follow it, after
    its one redraft or because the run ended before it — which is never
    ``covered`` (live-briefs Phase 3, open issue O8); ``replaced`` when a later
    note replaced it. With nothing to judge it by — no state yet, a note the
    state does not hold, or no verdict (a note that arrived too late, a review
    that could not be made) — it waits: ``pending`` while its session goes on,
    and ``not_checked`` once it has ended (``terminal``), so a finished, failed
    or stopped session never reports ``pending`` (notes-progress-report spec
    §4 item 2).
    """
    waiting: NoteOutcome = "not_checked" if terminal else "pending"
    if state is None:
        return waiting
    notes = [note for note in state.reader_notes if note.note_id == note_id]
    if not notes:
        return waiting
```

`src/deep_research/api/notes.py` — replace

```python
    if verdict == "honoured":
        return "covered"
    return "pending"


def note_records(
    board: NoteBoard, state: ResearchState | None
) -> list[tuple[ReceivedNote, str | None, NoteOutcome]]:
    """Every accepted note in receipt order: as received, its restatement once read, its outcome."""
    records: list[tuple[ReceivedNote, str | None, NoteOutcome]] = []
    for received in board.received():
        interpreted = board.interpreted(received.note_id)
        records.append(
            (
                received,
                interpreted.restatement if interpreted is not None else None,
                note_outcome(received.note_id, state),
            )
        )
    return records
```

with

```python
    if verdict == "honoured":
        return "covered"
    return waiting


def note_records(
    board: NoteBoard, state: ResearchState | None, *, terminal: bool
) -> list[tuple[ReceivedNote, str | None, NoteOutcome]]:
    """Every accepted note in receipt order: as received, its restatement once read, its outcome.

    ``terminal`` says the session has ended, so no outcome is ``pending``.
    """
    records: list[tuple[ReceivedNote, str | None, NoteOutcome]] = []
    for received in board.received():
        interpreted = board.interpreted(received.note_id)
        records.append(
            (
                received,
                interpreted.restatement if interpreted is not None else None,
                note_outcome(received.note_id, state, terminal=terminal),
            )
        )
    return records
```

`src/deep_research/api/models.py` — replace

```python
    ``restatement`` is the run's reading of it, ``None`` until the note is
    interpreted. ``outcome`` is the finished run's conclusion — ``covered``,
    ``not_found``, ``not_addressed`` (the report still does not follow the
    note, though the findings bore on it) or ``replaced`` — and ``pending``
    while the run is going or when no review judged the note.
    """

    note_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    restatement: str | None = None
    outcome: Literal["covered", "not_found", "not_addressed", "pending", "replaced"]
```

with

```python
    ``restatement`` is the run's reading of it, ``None`` until the note is
    interpreted. ``outcome`` is the run's conclusion — ``covered``,
    ``not_found``, ``not_addressed`` (the report still does not follow the
    note, though the findings bore on it) or ``replaced`` — or, with nothing
    to judge the note by, ``pending`` while the session goes on and
    ``not_checked`` once it has ended: no session that has ended reports
    ``pending`` (notes-progress-report spec §4 item 2).
    """

    note_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    restatement: str | None = None
    outcome: Literal[
        "covered", "not_found", "not_addressed", "pending", "not_checked", "replaced"
    ]
```

`src/deep_research/api/sessions.py` — replace

```python
    state = session.outcome.state if session.outcome is not None else None
    check = session.check
    return {
        "notes": [
            ReaderNoteResponse(
                note_id=received.note_id,
                text=received.text,
                restatement=restatement,
                outcome=outcome,
            )
            for received, restatement, outcome in note_records(session.note_board, state)
        ],
```

with

```python
    state = session.outcome.state if session.outcome is not None else None
    # notes-progress-report spec §4 item 2, §5.6: once the session has ended — a terminal
    # status, or closed out by a shutdown — a note nothing judged reads not_checked.
    terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None
    check = session.check
    return {
        "notes": [
            ReaderNoteResponse(
                note_id=received.note_id,
                text=received.text,
                restatement=restatement,
                outcome=outcome,
            )
            for received, restatement, outcome in note_records(
                session.note_board, state, terminal=terminal
            )
        ],
```

- [ ] **Step 4: Run them to make sure they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_notes.py tests/test_api/test_note_route.py -q 2>&1 | tail -1
.venv/Scripts/python.exe -m pytest tests/test_api -q 2>&1 | tail -1
```

Expected: `49 passed, 1 warning`; then `229 passed, 2 warnings` (Task 1 Step 4's `tests/test_api` count + 2).

- [ ] **Step 5: Write the failing web tests**

`web/test/notes.test.ts` — replace

```ts
    expect(OUTCOME_TEXT).toEqual({
      covered: "covered", not_found: "couldn't find evidence", not_addressed: "not addressed in the report",
      pending: "not checked", replaced: "replaced by a later note",
    });
```

with

```ts
    // notes-progress-report spec §4 item 2: "pending" occurs only while a run is going, and a session
    // that has ended reads "not_checked".
    expect(OUTCOME_TEXT).toEqual({
      covered: "covered", not_found: "couldn't find evidence", not_addressed: "not addressed in the report",
      pending: "not checked yet", not_checked: "not checked", replaced: "replaced by a later note",
    });
```

`web/test/components/reader-notes.test.tsx` — replace

```tsx
  { note_id: "n3", text: "Actually only the US", restatement: "only the United States", outcome: "pending" as const },
```

with

```tsx
  { note_id: "n3", text: "Actually only the US", restatement: "only the United States", outcome: "not_checked" as const },
```

The report's notes block shows only for a finished session (`web/components/ReportBody.tsx:120-131`), which no longer reports `pending`; its caption for this note stays `not checked`.

`web/e2e/notes.spec.ts` — replace

```ts
    { note_id: "n1", text: "More on fire-safety standards", restatement: "More on fire-safety standards", outcome: "pending" },
    { note_id: "n2", text: "Only the United States", restatement: "Only the United States", outcome: "pending" },
```

with

```ts
    { note_id: "n1", text: "More on fire-safety standards", restatement: "More on fire-safety standards", outcome: "not_checked" },
    { note_id: "n2", text: "Only the United States", restatement: "Only the United States", outcome: "not_checked" },
```

That end-to-end test reads a finished replay session's `/status` (`web/e2e/notes.spec.ts:95-113`): its two notes arrive after the engine has finished, nothing judges them, and the session has ended, so they now read `not_checked`; the report's captions it checks stay `not checked`. Playwright runs only in Task 9, whose whole Chromium suite includes this test; Step 8's `typecheck` covers the file now.

- [ ] **Step 6: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/notes.test.ts test/components/reader-notes.test.tsx 2>&1 | grep -E "✗|×|FAIL|Tests  ")
```

Expected — two failures (the two `×` lines come first, and the files may finish in either order), then the count:

```
 FAIL  test/notes.test.ts > lib/notes — the note line's copy and each note's acknowledgement (live-briefs spec §4.7) > words the loops' first lines, the report's outcomes and the notes left
 FAIL  test/components/reader-notes.test.tsx > the report's Your notes (live-briefs spec §4.7, AC19) > sits inside the report card above the prose, outside it, with each note and its outcome
      Tests  2 failed | 15 passed (17)
```

The first fails because `OUTCOME_TEXT` has no `not_checked` and its `pending` still reads `not checked`; the second because the `not_checked` note's caption is empty.

- [ ] **Step 7: Implement the outcome on the web**

`web/lib/api.ts` — replace

```ts
/* One accepted note: as the reader wrote it, the run's reading once interpreted, and what the
   finished run concluded ("pending" while it runs, or when no review judged it; "not_addressed"
   when the report still does not follow it after its one redraft). */
export type ReaderNoteOutcome = "covered" | "not_found" | "not_addressed" | "pending" | "replaced";
```

with

```ts
/* One accepted note: as the reader wrote it, the run's reading once interpreted, and what the run
   concluded — "not_addressed" when the report still does not follow it after its one redraft; with
   nothing to judge it by, "pending" while the session goes on and "not_checked" once it has ended
   (notes-progress-report spec §4 item 2). */
export type ReaderNoteOutcome = "covered" | "not_found" | "not_addressed" | "pending" | "not_checked" | "replaced";
```

`web/lib/notes.ts` — replace

```ts
/* The report's "Your notes" block: one caption per note (§4.7; "not addressed in the report",
   "not checked" and "replaced by a later note" are this plan's words for the three outcomes the
   spec's table leaves unnamed). A note the report still ignores is never "covered". */
export const OUTCOME_TEXT: Readonly<Record<ReaderNoteOutcome, string>> = {
  covered: "covered",
  not_found: "couldn't find evidence",
  not_addressed: "not addressed in the report",
  pending: "not checked",
  replaced: "replaced by a later note",
};
```

with

```ts
/* The report's "Your notes" block: one caption per note (§4.7; "not addressed in the report",
   "not checked" and "replaced by a later note" are this plan's words for the three outcomes the
   spec's table leaves unnamed). A note the report still ignores is never "covered". A session that
   has ended reads "not_checked" for a note nothing judged, and "pending" — "not checked yet" — occurs
   only while a run is going (notes-progress-report spec §4 item 2). */
export const OUTCOME_TEXT: Readonly<Record<ReaderNoteOutcome, string>> = {
  covered: "covered",
  not_found: "couldn't find evidence",
  not_addressed: "not addressed in the report",
  pending: "not checked yet",
  not_checked: "not checked",
  replaced: "replaced by a later note",
};
```

- [ ] **Step 8: Run them to make sure they pass, and type-check**

```bash
(cd web && npx vitest run test/notes.test.ts test/components/reader-notes.test.tsx 2>&1 | grep -E "Tests  " && npm run -s typecheck)
```

Expected: `Tests  17 passed (17)`, and no `typecheck` output.

- [ ] **Step 9: Say it in the documentation**

`README.md` — replace

```
ambiguity 5). The status snapshot carries `notes` (each with its `text`, its
`restatement` once read, and its `outcome`: `covered`, `not_found`, `not_addressed`
when the report still does not follow the note after its one redraft, `replaced`, or
`pending` while the run goes on or when no review judged it), `notes_remaining`,
`note_passes`, and `clarification` (the one-time check's questions and the answers the
run started with, or `null`).
```

with

```
ambiguity 5). The status snapshot carries `notes` (each with its `text`, its
`restatement` once read, and its `outcome`: `covered`, `not_found`, `not_addressed`
when the report still does not follow the note after its one redraft, `replaced`,
`pending` while the session goes on with nothing to judge the note by yet, or
`not_checked` once it has ended that way — no session that has ended reports `pending`),
`notes_remaining`, `note_passes`, and `clarification` (the one-time check's questions and
the answers the run started with, or `null`).
```

`README.md` — replace

```
running stage plays arrives after the engine has finished and ends `pending`. In replay mode the topbar
```

with

```
running stage plays arrives after the engine has finished and ends `not_checked`. In replay mode the topbar
```

`docs/design/api-gaps.md` — replace

```
read and acknowledged, but no step reads it, and it ends `pending` (`not checked`).
```

with

```
read and acknowledged, but no step reads it, and it ends `not_checked` (`not checked`).
```

- [ ] **Step 10: Commit**

```bash
git add src/deep_research/api/notes.py src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/test_notes.py tests/test_api/test_note_route.py web/lib/api.ts web/lib/notes.ts web/test/notes.test.ts web/test/components/reader-notes.test.tsx web/e2e/notes.spec.ts README.md docs/design/api-gaps.md
git commit -m "feat(api): a note nothing judged reads not_checked once its session has ended (notes-progress-report §4 item 2)"
```

---

### Task 3: The `stopped` status and `SessionStore.stop` (spec §8.2, §8.4's store rows, §4 item 3; AC28, AC30, AC31 store halves; AC32)

**Files** (line numbers locate the anchors at the end of Task 2):
- Create: `src/deep_research/api/stop.py`, `tests/test_api/test_stop.py`, `web/test/fixtures/active-rows.json`
- Modify: `src/deep_research/api/models.py:34-42` (`SessionStatus`), `:310-311` (`ResearchSessionResponse.stopped_step` after it); `src/deep_research/api/sessions.py:19` (imports), `:45-47`, `:54-56` (`TERMINAL_STATUSES`), `:66-67` (`StopRefusal` and `NotStoppable` after it), `:129-134` (`stopped_step`, `publish`), `:380` (`stop`, before `add_note`), `:526-528` (`_run`'s docstring), `:567-582` (`_run`'s `else` and `finally`), `:655-656` (`_record_failure`)
- Test: `tests/test_api/test_sessions.py:324-331`

**Interfaces:**
- Consumes: Task 2's `session_note_fields` `terminal` rule.
- Produces:
  - `deep_research.api.stop`: `PIPELINE_ROWS: tuple[str, ...]` (the seven rows in order), `CHECK_STEP = "check"`, `active_row(events: Iterable[ResearchEvent]) -> str | None` (exactly `run.active` of `web/lib/run-state.ts`), `session_stopped_event(step: str, stopped_at: datetime, elapsed_seconds: int) -> ResearchEvent`.
  - `deep_research.api.models`: `SessionStatus` includes `"stopped"`; `ResearchSessionResponse.stopped_step: str | None = None`.
  - `deep_research.api.sessions`: `TERMINAL_STATUSES` includes `"stopped"`; `StopRefusal = Literal["finished", "publishing", "closing"]`; `NotStoppable(session_id, reason)` with `.reason`; `ResearchSession.stopped_step: str | None`; `SessionStore.stop(session_id: str) -> ResearchSession` (raises `KeyError` or `NotStoppable`); `ResearchSession.publish` is a no-op once `status == "stopped"`.
  - `web/test/fixtures/active-rows.json`: `{case_id: [[row | null, count], …]}` — the page's active row after every prefix of each captured replay (prefix 0 first), run-length encoded. Task 6's Vitest test recomputes it from the page's own rule.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api/test_stop.py`:

```python
"""Stop (notes-progress-report spec §8; D17, D26, D33): one session ends at once.

The store tests drive ``SessionStore`` with scripted runners and checkers; the rule tests
read the captured replays the web app's own tests read; the replay test runs a scripted
case offline. No provider is ever reached.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from deep_research.api.models import ClarificationAnswersRequest
from deep_research.api.notes import NoteInterpretation
from deep_research.api.replay import ReplayRunner
from deep_research.api.sessions import (
    NotesClosed,
    NotStoppable,
    NotWaitingForInput,
    SessionStore,
    session_note_fields,
)
from deep_research.api.stop import active_row
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings, HitlConfig
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import GateRunner, ScriptedRunner, make_outcome
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_api.test_clarification import Checker

QUESTION = "What limits grid-scale battery storage?"
HITL = HitlConfig(check_timeout_s=0.5, answer_wait_s=5.0, note_interpret_timeout_s=0.2)
WEB_FIXTURES = Path(__file__).resolve().parents[2] / "web" / "test" / "fixtures"


def _start(store: SessionStore, *, ask: bool = False) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml", ask_clarifying_questions=ask,
        settings=ConfigSettings(), hitl=HITL,
    )


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _event(event_type: str, **metadata: Any) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="Event.", metadata=metadata)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


# --- the store (spec §8.2) -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_running_session_stops_at_once_and_says_where() -> None:
    """AC28's store half: the step read from what the session published, ``session.stopped``
    as its last event, the terminal status, ``finished_at`` and the cancelled task — all
    before ``stop`` returns control; a subscriber's stream closes after ``session.stopped``;
    a second stop is refused as ``finished``."""
    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    received: list[ResearchEvent] = []

    async def follow() -> None:
        async for event in store.iter_events("s1"):
            received.append(event)

    follower = asyncio.create_task(follow())
    await asyncio.sleep(0)

    assert store.stop("s1") is session

    assert (session.status, session.stopped_step, session.current_agent) == ("stopped", "planner", None)
    stopped_at = session.finished_at
    assert stopped_at is not None
    last = session.events[-1]
    assert (last.event_type, last.source, last.message) == (
        "session.stopped", "api", "The reader stopped the research.",
    )
    assert last.metadata == {
        "step": "planner",
        "stopped_at": stopped_at.isoformat(timespec="seconds"),
        "elapsed_seconds": int((stopped_at - session.started_at).total_seconds()),
    }
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)
    assert session.task.cancelled()
    assert (session.status, session.finished_at) == ("stopped", stopped_at)
    await asyncio.wait_for(follower, timeout=1)
    assert _types(received) == ["graph.node.started", "session.stopped"]
    assert (session.outcome, session.report_path, session.errors) == (None, None, [])
    with pytest.raises(NotStoppable) as again:
        store.stop("s1")
    assert again.value.reason == "finished"


@pytest.mark.asyncio
async def test_stop_during_needs_input() -> None:
    """A stop while the one-time check waits for the reader (D33): step ``check``, the check's
    pending answers cancelled, the runner never called, and the run's close-out keeps both the
    status and the time of the stop; answers and notes are refused afterwards (AC31's store
    half); the check it asked is kept for the status."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store, ask=True)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")
    pending = session.clarification
    assert pending is not None

    store.stop("s1")

    assert (session.status, session.stopped_step, session.clarification) == ("stopped", "check", None)
    assert pending.submitted.cancelled()
    stopped_at = session.finished_at
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)
    assert session.task.cancelled()
    assert (session.status, session.finished_at) == ("stopped", stopped_at)
    assert _types(session.events) == ["session.clarification.requested", "session.stopped"]
    assert session.events[-1].metadata["step"] == "check"
    assert runner.calls == []
    assert session.check is not None and session.check.answers == ()
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", ClarificationAnswersRequest(answers=[], skip=True))
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too late")


@pytest.mark.asyncio
async def test_publish_after_stop_dropped() -> None:
    """§8.2: a stopped session takes no more events, so ``session.stopped`` stays last whatever a
    task finishing its own cancellation still hands over — a replay's pacer, say — and nothing
    it drops can close the notes or wake a subscriber."""
    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    store.stop("s1")
    session.changed.clear()

    session.publish(_event("graph.node.completed", node="planner", iteration=0))
    session.publish(_event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0))

    assert _types(session.events) == ["graph.node.started", "session.stopped"]
    assert (session.notes_closed, session.changed.is_set()) == (False, False)
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)


@pytest.mark.asyncio
async def test_a_stop_is_refused_once_the_session_has_ended_is_publishing_or_the_service_is_closing() -> None:
    """§8.1's 409 reasons, in this plan's order: ``finished`` (completed, failed), ``publishing``
    (the route decided to publish), ``closing`` (the store is shutting down), and ``KeyError``
    for an unknown id (AC30's store half). A refused stop changes nothing."""
    done = SessionStore(runner=ScriptedRunner())
    _start(done)
    failed = SessionStore(runner=ScriptedRunner(error=RuntimeError("boom")))
    _start(failed)
    for store in (done, failed):
        task = store.require("s1").task
        assert task is not None
        await task
        with pytest.raises(NotStoppable) as finished:
            store.stop("s1")
        assert finished.value.reason == "finished"

    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    session.publish(_event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0))
    with pytest.raises(NotStoppable) as publishing:
        store.stop("s1")
    assert publishing.value.reason == "publishing"
    assert (session.status, _types(session.events)[-1]) == ("running", "graph.route.decided")
    with pytest.raises(KeyError):
        store.stop("missing")
    runner.release.set()
    await store.close()

    seen: list[str] = []
    stores: list[SessionStore] = []

    class Closing(GateRunner):
        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            try:
                return await super().__call__(**kwargs)
            except asyncio.CancelledError:
                try:
                    stores[0].stop("s1")
                except NotStoppable as refusal:
                    seen.append(refusal.reason)
                raise

    closing = Closing()
    shutting = SessionStore(runner=closing)
    stores.append(shutting)
    _start(shutting)
    await closing.started.wait()
    await shutting.close()
    assert seen == ["closing"]
    with pytest.raises(NotStoppable) as after:
        shutting.stop("s1")
    assert after.value.reason == "finished"


@pytest.mark.asyncio
async def test_a_stopped_sessions_notes_read_not_checked_and_a_reading_in_flight_is_dropped() -> None:
    """§8.4: a stopped session keeps the notes it took, each ``not_checked`` (§4 item 2); a note
    still being read is cancelled with the run, so nothing waits on the board; no note is taken
    afterwards."""

    class SecondNoteHangs:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, text: str, question: str, earlier: Any, settings: object) -> NoteInterpretation:
            self.calls += 1
            if self.calls == 2:
                await asyncio.Event().wait()
            return NoteInterpretation(kinds=["emphasis"], restatement=text)

    runner = GateRunner()
    store = SessionStore(runner=runner, note_interpreter=SecondNoteHangs())
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    store.add_note("s1", "first")
    await _until(lambda: len(session.note_board.snapshot()) == 1)
    store.add_note("s1", "second")
    await asyncio.sleep(0.02)
    readings = set(session.note_tasks)
    assert len(readings) == 1

    store.stop("s1")
    assert session.task is not None
    await asyncio.wait({session.task, *readings}, timeout=1)
    await asyncio.sleep(0)

    assert session.note_tasks == set()
    assert session.note_board.pending == ()
    assert [note.note_id for note in session.note_board.snapshot()] == ["n1"]
    fields = session_note_fields(session)
    assert [(note.note_id, note.restatement, note.outcome) for note in fields["notes"]] == [
        ("n1", "first", "not_checked"), ("n2", None, "not_checked"),
    ]
    assert fields["notes_remaining"] == 8
    with pytest.raises(NotesClosed):
        store.add_note("s1", "third")


@pytest.mark.asyncio
async def test_a_run_that_swallows_its_cancellation_keeps_the_stop() -> None:
    """A runner that catches the stop's cancellation and returns an outcome anyway, or fails while
    it unwinds, cannot turn a stopped session into a finished or failed one: no outcome, no error,
    ``session.stopped`` last (spec ambiguity 6)."""

    class Stubborn(GateRunner):
        def __init__(self, *, fail: bool) -> None:
            super().__init__()
            self.fail = fail

        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            try:
                return await super().__call__(**kwargs)
            except asyncio.CancelledError:
                if self.fail:
                    raise RuntimeError("cleanup failed") from None
                return make_outcome(status="completed", report="# A report written anyway")

    for fail in (False, True):
        runner = Stubborn(fail=fail)
        store = SessionStore(runner=runner)
        _start(store)
        session = store.require("s1")
        await runner.started.wait()
        store.stop("s1")
        assert session.task is not None
        await asyncio.wait({session.task}, timeout=1)

        assert (session.status, session.outcome, session.report_path, session.errors) == ("stopped", None, None, [])
        assert _types(session.events)[-1] == "session.stopped"


# --- the step a stop records (spec §8.2 step 2, §4 item 3; AC32) -------------------------


def test_active_row_follows_the_consoles_rule() -> None:
    """``active_row`` is ``run.active`` (web/lib/run-state.ts; DESIGN.md §3.5) on the cases the
    captured replays do not reach: a hop, the reviewer's own completion, every route destination,
    a halt's skipped rows, Publishing's completion, the session's completion, an unknown node."""

    def row(*events: ResearchEvent) -> str | None:
        return active_row(events)

    planned = _event("graph.node.completed", node="planner")
    assert row() == "planner"
    assert row(_event("graph.node.started", node="planner", iteration=0)) == "planner"
    assert row(planned) == "researcher"
    assert row(_event("graph.node.completed", node="report_writer")) == "report_reviewer"
    assert row(_event("graph.node.completed", node="report_writer"), _event("graph.node.completed", node="report_reviewer")) == "report_reviewer"
    for hop in ("extra_pass", "note_pass", "writer_redraft"):
        assert row(_event("graph.route.decided", destination="redraft"), _event("graph.node.completed", node=hop)) == "report_writer"
    assert [row(_event("graph.route.decided", destination=d)) for d in ("extra_pass", "note_pass", "redraft", "finalize", "end")] == [
        "researcher", "researcher", "report_writer", "finalize_report", None,
    ]
    assert row(planned, _event("graph.node.skipped", node="source_evaluator", iteration=0)) == "researcher"
    assert row(planned, _event("graph.node.skipped", node="researcher", iteration=0)) is None
    assert row(_event("graph.node.completed", node="finalize_report")) is None
    assert row(planned, _event("graph.session.completed", status="completed")) is None
    assert row(_event("graph.node.completed", node="unknown_node")) is None


def test_active_row_matches_web_rule() -> None:
    """AC32: after every prefix of every captured replay, ``active_row`` names the row the page
    shows as active. ``web/test/fixtures/active-rows.json`` is the page's own rule run over the
    same captures — ``web/test/active-row.test.ts`` recomputes it on every Vitest run — so the
    API and the page cannot disagree about the step a stop records."""
    golden = json.loads((WEB_FIXTURES / "active-rows.json").read_text(encoding="utf-8"))
    captured = sorted(path.stem for path in (WEB_FIXTURES / "events").glob("*.json"))
    assert sorted(golden) == captured
    for case_id in captured:
        record = json.loads((WEB_FIXTURES / "events" / f"{case_id}.json").read_text(encoding="utf-8"))
        events = [ResearchEvent.model_validate(raw) for raw in record["events"]]
        expected = [row for row, count in golden[case_id] for _ in range(count)]
        assert [active_row(events[:k]) for k in range(len(events) + 1)] == expected, case_id


@pytest.mark.asyncio
async def test_replay_stop_mid_stream(tmp_path: Path) -> None:
    """AC32 on the replay runner: replay runs the engine ahead of its paced stream, and a stop
    records the row the page shows at that moment — read from what the session has published,
    not from the engine — and the pacer publishes nothing after ``session.stopped``."""
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.05, root=tmp_path))
        store.start(
            session_id="s1", query=scenario.question, max_extra_passes=None, output_format="markdown",
            config_overrides={}, config_path=str(production_config_path()),
        )
        session = store.require("s1")
        # The first topic's start is event 8 of 72; Researching lasts until event 28, a second away.
        await _until(lambda: "researcher.sub_topic.started" in _types(session.events), timeout=15)
        before = list(session.events)
        store.stop("s1")
        assert session.task is not None
        await asyncio.wait({session.task}, timeout=2)
        await asyncio.sleep(0.3)  # six of the pacer's 50 ms beats: anything it still held would show

    assert session.task.cancelled()
    assert session.stopped_step == active_row(before) == "researcher"
    assert [event.event_id for event in session.events[:-1]] == [event.event_id for event in before]
    assert _types(session.events)[-1] == "session.stopped"
    assert (session.status, session.outcome) == ("stopped", None)
```

Create `web/test/fixtures/active-rows.json`:

```json
{
 "missing-target-triggers-one-extra-pass": [["planner",6],["researcher",22],["source_evaluator",4],["evidence_verifier",3],["report_writer",3],["report_reviewer",4],["researcher",11],["source_evaluator",4],["evidence_verifier",3],["report_writer",3],["report_reviewer",4],["finalize_report",4],[null,2]],
 "scoped-redraft-after-a-named-defect": [["planner",6],["researcher",13],["source_evaluator",4],["evidence_verifier",3],["report_writer",3],["report_reviewer",4],["report_writer",7],["report_reviewer",4],["finalize_report",4],[null,2]]
}
```

Planning computed it by running `web/lib/run-state.ts` itself over the two captures in `web/test/fixtures/events/` (73 and 50 prefixes); Task 6's `web/test/active-row.test.ts` recomputes it the same way and compares, and writes it again only when `WRITE_ACTIVE_ROWS=1` is set.

`tests/test_api/test_sessions.py` — replace

```python
def test_session_response_accepts_every_status() -> None:
    for status in (
        "running",
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    ):
```

with

```python
def test_session_response_accepts_every_status() -> None:
    for status in (
        "running",
        "needs_input",
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
        "stopped",
    ):
```

- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py -q 2>&1 | tail -4
.venv/Scripts/python.exe -m pytest tests/test_api/test_sessions.py -q 2>&1 | tail -1
```

Expected: the first run stops at collection, with `ImportError: cannot import name 'NotStoppable' from 'deep_research.api.sessions'` above its summary; its last lines are `ERROR tests/test_api/test_stop.py`, `Interrupted: 1 error during collection` (between rows of `!`) and `1 error in 0.46s` (the time varies). The second prints `1 failed, 34 passed in 0.40s`: the failure is `test_session_response_accepts_every_status`, a `ValidationError` because `ResearchSessionResponse` does not take `stopped` yet.

- [ ] **Step 3: Write the step rule and the event**

Create `src/deep_research/api/stop.py`:

```python
"""Stopping a session (notes-progress-report spec §8, D17): the step it was on, and its event.

``POST /research/{id}/stop`` records the step the reader stopped the run at, and the
console names the same step: ``active_row`` reads a session's published events with the
console's own rule for its active row (``run.active`` in ``web/lib/run-state.ts``;
DESIGN.md §3.5), so the row the page shows as stopped is the row the API recorded (AC32,
pinned for both by ``web/test/fixtures/active-rows.json``). ``session_stopped_event`` is
the last event a stopped session publishes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from deep_research.utils.types import ResearchEvent

PIPELINE_ROWS: tuple[str, ...] = (
    "planner",
    "researcher",
    "source_evaluator",
    "evidence_verifier",
    "report_writer",
    "report_reviewer",
    "finalize_report",
)
"""The console's seven rows, in order (``STAGES``, ``web/lib/run-state.ts``)."""

CHECK_STEP = "check"
"""The step of a session stopped while the one-time check waits for the reader (spec §4 item 3)."""

_HOPS = frozenset({"extra_pass", "note_pass", "writer_redraft"})
_DESTINATION_ROWS = {
    "extra_pass": "researcher",
    "note_pass": "researcher",
    "redraft": "report_writer",
    "finalize": "finalize_report",
}


def _next_row(node: object) -> str | None:
    """The row after ``node``'s, or ``None`` after Publishing and for anything that is no row."""
    if not isinstance(node, str) or node not in PIPELINE_ROWS:
        return None
    index = PIPELINE_ROWS.index(node) + 1
    return PIPELINE_ROWS[index] if index < len(PIPELINE_ROWS) else None


def active_row(events: Iterable[ResearchEvent]) -> str | None:
    """The console's active row after ``events``: the row a stop records.

    Planning until the planner completes. A ``graph.node.completed`` moves to the next
    row — the reviewer's own completion and the hops' move nothing, and Publishing's
    leaves none; ``graph.route.decided`` moves to its destination's row (``end`` leaves
    none); a skipped active row and the session's completion leave none. Exactly
    ``run.active`` in ``web/lib/run-state.ts``.
    """
    row: str | None = PIPELINE_ROWS[0]
    for event in events:
        kind, metadata = event.event_type, event.metadata
        if kind == "graph.node.completed":
            node = metadata.get("node")
            if node in _HOPS or node == "report_reviewer":
                continue
            row = _next_row(node)
        elif kind == "graph.route.decided":
            row = _DESTINATION_ROWS.get(str(metadata.get("destination")))
        elif kind == "graph.node.skipped":
            if metadata.get("node") == row:
                row = None
        elif kind == "graph.session.completed":
            row = None
    return row


def session_stopped_event(
    step: str, stopped_at: datetime, elapsed_seconds: int
) -> ResearchEvent:
    """``session.stopped``: the step the reader stopped the run at, when, and how long it ran."""
    return ResearchEvent(
        event_type="session.stopped",
        source="api",
        message="The reader stopped the research.",
        metadata={
            "step": step,
            "stopped_at": stopped_at.isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed_seconds,
        },
    )


__all__ = ["CHECK_STEP", "PIPELINE_ROWS", "active_row", "session_stopped_event"]
```

- [ ] **Step 4: Add the status, and stop a session in the store**

`src/deep_research/api/models.py` — replace

```python
# ``running`` when the answers arrive, the reader skips, or the wait times out.
SessionStatus = Literal[
    "running",
    "needs_input",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]
```

with

```python
# ``running`` when the answers arrive, the reader skips, or the wait times out.
# ``stopped`` is terminal: the reader stopped the run, which published nothing
# (notes-progress-report spec §8).
SessionStatus = Literal[
    "running",
    "needs_input",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
    "stopped",
]
```

`src/deep_research/api/models.py` — replace

```python
    clarification: ClarificationRecordResponse | None = None
    """The one-time check the session asked, or ``None`` when it asked nothing."""
```

with

```python
    clarification: ClarificationRecordResponse | None = None
    """The one-time check the session asked, or ``None`` when it asked nothing."""

    stopped_step: str | None = None
    """The step the reader stopped the run at — ``check`` (the one-time check) or a
    pipeline row, ``planner`` … ``finalize_report`` — when ``status`` is ``stopped``;
    ``None`` otherwise (notes-progress-report spec §4 item 3, §8.4)."""
```

`src/deep_research/api/sessions.py` — replace

```python
from typing import Any, TypeAlias
```

with

```python
from typing import Any, Literal, TypeAlias
```

`src/deep_research/api/sessions.py` — replace

```python
    note_records,
    reader_note,
)
```

with

```python
    note_records,
    reader_note,
)
from deep_research.api.stop import CHECK_STEP, active_row, session_stopped_event
```

`src/deep_research/api/sessions.py` — replace

```python
TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed"}
)
```

with

```python
TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed", "stopped"}
)
```

`src/deep_research/api/sessions.py` — replace

```python
class NotesClosed(Exception):
    """A note arrived for a session that no longer takes notes (a 409, spec §4.6)."""
```

with

```python
class NotesClosed(Exception):
    """A note arrived for a session that no longer takes notes (a 409, spec §4.6)."""


StopRefusal: TypeAlias = Literal["finished", "publishing", "closing"]


class NotStoppable(Exception):
    """A stop for a session that can no longer be stopped (a 409, notes-progress-report spec §8.1).

    ``reason`` says why: ``finished`` once the session has ended (a stopped one
    included), ``publishing`` once the run has decided to publish or to end, and
    ``closing`` while the service shuts down.
    """

    def __init__(self, session_id: str, reason: StopRefusal) -> None:
        super().__init__(session_id)
        self.reason: StopRefusal = reason
```

`src/deep_research/api/sessions.py` — replace

```python
    run_settings: Any = None
    hitl: HitlConfig = field(default_factory=HitlConfig)

    def publish(self, event: ResearchEvent) -> None:
        """Record one progress event and update the live status fields."""
        self.events.append(event.model_copy(deep=True))
```

with

```python
    run_settings: Any = None
    hitl: HitlConfig = field(default_factory=HitlConfig)
    stopped_step: str | None = None
    """The step the reader stopped the run at (notes-progress-report spec §8.2), else ``None``."""

    def publish(self, event: ResearchEvent) -> None:
        """Record one progress event and update the live status fields.

        A stopped session takes no more events (notes-progress-report spec §8.2), so
        ``session.stopped`` stays its last — whatever a task finishing its own
        cancellation still hands over, a replay's pacer for one.
        """
        if self.status == "stopped":
            return
        self.events.append(event.model_copy(deep=True))
```

`src/deep_research/api/sessions.py` — replace

```python
    def add_note(self, session_id: str, text: str) -> ReceivedNote:
```

with

```python
    def stop(self, session_id: str) -> ResearchSession:
        """Stop one session at once (notes-progress-report spec §8.2, D17).

        Raises ``KeyError`` for an unknown session and ``NotStoppable`` when it can no
        longer be stopped: ``finished`` once it has ended (a stopped one included),
        ``publishing`` once its stream shows the run's decision to publish or end — the
        notes cutoff — and ``closing`` while the store shuts down. Otherwise, with no
        ``await`` anywhere: the step it was on is read from what it has published
        (``check`` while the one-time check waits); ``session.stopped`` is published as
        its last event; the session ends ``stopped``; a pending check is cancelled; and
        the run's task and every note reading are cancelled, which cancels every call
        they have in flight. Nothing more is published, so nothing is written.
        """
        session = self.require(session_id)
        if session.status in TERMINAL_STATUSES or session.finished_at is not None:
            raise NotStoppable(session_id, "finished")
        if session.notes_closed:
            raise NotStoppable(session_id, "publishing")
        if self._closing:
            raise NotStoppable(session_id, "closing")
        if session.status == "needs_input":
            step = CHECK_STEP
        else:
            row = active_row(session.events)
            if row is None:
                # No row is active only once the run is ending: each event that leaves
                # none follows the decision that closes notes (spec ambiguity 3).
                raise NotStoppable(session_id, "publishing")
            step = row
        now = datetime.now(timezone.utc)
        session.publish(
            session_stopped_event(
                step, now, int((now - session.started_at).total_seconds())
            )
        )
        session.status = "stopped"
        session.stopped_step = step
        session.finished_at = now
        session.current_agent = None
        pending = session.clarification
        if pending is not None and not pending.submitted.done():
            pending.submitted.cancel()
        session.clarification = None
        session.changed.set()
        for task in (session.task, *session.note_tasks):
            if task is not None and not task.done():
                task.cancel()
        return session

    def add_note(self, session_id: str, text: str) -> ReceivedNote:
```

`src/deep_research/api/sessions.py` — replace

```python
        the session out so subscribers wake and readers see timestamps. A run
        cancelled while it waited for answers reads ``running`` with
        ``finished_at`` set, like any other interrupted run.
```

with

```python
        the session out so subscribers wake and readers see timestamps. A run
        cancelled while it waited for answers reads ``running`` with
        ``finished_at`` set, like any other interrupted run. A run the reader
        stopped was closed out by ``stop`` already: it keeps its ``stopped``
        status and the time of the stop, and nothing it returns or raises on
        the way out is folded in.
```

`src/deep_research/api/sessions.py` — replace

```python
        else:
            session.status = outcome.status
            session.iteration = outcome.state.iteration
            session.report_path = outcome.report_path
            session.trace_url = outcome.trace_url
            session.errors = [
                error.model_copy(deep=True) for error in outcome.errors
            ]
            session.outcome = outcome
        finally:
            if session.status == "needs_input":
                session.status = "running"
            session.clarification = None
            session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()
```

with

```python
        else:
            # A runner that caught the stop's cancellation and returned anyway does not
            # undo the stop: a stopped session keeps no outcome (spec §8.4).
            if session.status != "stopped":
                session.status = outcome.status
                session.iteration = outcome.state.iteration
                session.report_path = outcome.report_path
                session.trace_url = outcome.trace_url
                session.errors = [
                    error.model_copy(deep=True) for error in outcome.errors
                ]
                session.outcome = outcome
        finally:
            if session.status == "needs_input":
                session.status = "running"
            session.clarification = None
            # A stop closed the session out at the moment the reader asked for it.
            if session.finished_at is None:
                session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()
```

`src/deep_research/api/sessions.py` — replace

```python
    """Record one safe, non-recoverable failure on a session."""
    session.status = "failed"
```

with

```python
    """Record one safe, non-recoverable failure on a session.

    A stopped session records none: whatever its cancellation raised on the way
    out, the reader's stop is how it ended (notes-progress-report spec §8.4).
    """
    if session.status == "stopped":
        return
    session.status = "failed"
```

- [ ] **Step 5: Run them to make sure they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py tests/test_api/test_sessions.py -q 2>&1 | tail -1
.venv/Scripts/python.exe -m pytest tests/test_api -q 2>&1 | tail -1
```

Expected: `44 passed, 2 warnings`; then `238 passed, 2 warnings` (Task 2's 229 + 9).

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/stop.py src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/test_stop.py tests/test_api/test_sessions.py web/test/fixtures/active-rows.json
git commit -m "feat(api): a session can be stopped at once, ending stopped at the step it was on (notes-progress-report §8.2)"
```

---

### Task 4: `POST /research/{id}/stop`, a stopped session's answers, and the API's documentation (spec §8.1, §8.4's app row, §8.6's API rows; AC28, AC30, AC31)

**Files** (line numbers locate the anchors at the end of Task 3):
- Modify: `src/deep_research/api/app.py:48-50` (imports), `:79-80` (`_SAFE_MESSAGES`), `:152-153` (`_session_response`), `:346-349` (the route, after the notes route), `:402-411` (`/report`), `:439-442` (`/evidence`); `README.md:760`, `:773`, `:795`, `:844-845`, `:885-886` (FastAPI Interface); `docs/design/api-gaps.md:28-29`, `:32`, `:35`, `:46-48`, `:75`, `:95`, `:164-166`
- Test: `tests/test_api/test_stop.py:17-19`, `:36-37` (imports), appended after `:377`

**Interfaces:**
- Consumes: Task 3's `SessionStore.stop`, `NotStoppable.reason`, `ResearchSession.stopped_step`.
- Produces: `POST /research/{session_id}/stop` → 202 `ResearchSessionResponse` / 409 `not_stoppable` (`reason`) / 404 `session_not_found`; every `ResearchSessionResponse` carries `stopped_step`; `/report` and `/evidence` answer a stopped session 409 before looking for an outcome.

- [ ] **Step 1: Write the failing route tests**

`tests/test_api/test_stop.py` — replace

```python
import pytest

from deep_research.api.models import ClarificationAnswersRequest
```

with

```python
import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.models import ClarificationAnswersRequest
```

`tests/test_api/test_stop.py` — replace

```python
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_api.test_clarification import Checker
```

with

```python
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_api.test_app import valid_preflight, wait_until_terminal
from tests.test_api.test_clarification import Checker
from tests.test_api.test_replay import frames
```

Append to `tests/test_api/test_stop.py`:

```python


# --- the route (spec §8.1, §8.4) -------------------------------------------------------


def _status(client: TestClient, session_id: str) -> dict[str, Any]:
    return client.get(f"/research/{session_id}/status").json()


def _wait(client: TestClient, session_id: str, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = _status(client, session_id)
        if predicate(body):
            return body
        time.sleep(0.01)
    raise AssertionError("status never matched")


def _error(response: Any) -> tuple[int, str, str | None]:
    error = response.json()["error"]
    return response.status_code, error["code"], error["reason"]


def test_stop_route_codes() -> None:
    """AC28, AC30, AC31 through the route: a running session stops with 202 and the stopped
    session; its stream ends with ``session.stopped``; /status, /trace and the list name it;
    its note reads ``not_checked``; /report and /evidence (both formats) answer 409 as a halted
    run does, a note 409 ``notes_closed``, answers 409 ``not_waiting_for_input``; a second stop
    is 409 ``not_stoppable`` ``finished``; an unknown id is 404."""
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, lambda body: body["current_agent"] == "planner")
        client.post(f"/research/{session_id}/notes", json={"text": "Pumped hydro"})
        _wait(client, session_id, lambda body: body["notes"][0]["restatement"] is not None)
        stopped = client.post(f"/research/{session_id}/stop")
        # Checked before the stream is read: a session that did not stop never ends its stream.
        assert stopped.status_code == 202, stopped.text
        stream = client.get(f"/research/{session_id}/stream").text
        status = _status(client, session_id)
        trace = client.get(f"/research/{session_id}/trace").json()
        listed = client.get("/research").json()["sessions"]
        later = {
            "report": client.get(f"/research/{session_id}/report"),
            "evidence": client.get(f"/research/{session_id}/evidence"),
            "evidence_md": client.get(f"/research/{session_id}/evidence?format=markdown"),
            "note": client.post(f"/research/{session_id}/notes", json={"text": "too late"}),
            "answers": client.post(f"/research/{session_id}/answers", json={"answers": [], "skip": True}),
            "again": client.post(f"/research/{session_id}/stop"),
        }
        unknown = client.post("/research/missing/stop")

    body = stopped.json()
    assert (body["status"], body["stopped_step"], body["current_agent"]) == ("stopped", "planner", None)
    assert body["finished_at"] is not None
    assert frames(stream)[-1] == "session.stopped"
    assert (status["status"], status["stopped_step"], status["finished_at"]) == ("stopped", "planner", body["finished_at"])
    assert [(note["note_id"], note["outcome"]) for note in status["notes"]] == [("n1", "not_checked")]
    assert trace["metadata"]["status"] == "stopped"
    assert [(item["session_id"], item["status"], item["stopped_step"]) for item in listed] == [
        (session_id, "stopped", "planner"),
    ]
    assert {name: _error(response) for name, response in later.items()} == {
        "report": (409, "report_unavailable", None),
        "evidence": (409, "evidence_unavailable", None),
        "evidence_md": (409, "evidence_unavailable", None),
        "note": (409, "notes_closed", None),
        "answers": (409, "not_waiting_for_input", None),
        "again": (409, "not_stoppable", "finished"),
    }
    assert later["again"].json()["error"]["message"] == "Research session can no longer be stopped."
    assert _error(unknown) == (404, "session_not_found", None)


def test_the_stop_route_says_why_it_refuses() -> None:
    """AC30: 409 ``not_stoppable`` with ``finished`` once a run has completed or failed,
    ``publishing`` once the route decided to publish, ``closing`` while the store shuts down."""
    with TestClient(create_app(runner=ScriptedRunner(), preflight=valid_preflight)) as client:
        completed_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        wait_until_terminal(client, completed_id)
        completed = client.post(f"/research/{completed_id}/stop")
    failing = create_app(runner=ScriptedRunner(error=RuntimeError("boom")), preflight=valid_preflight)
    with TestClient(failing) as client:
        failed_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        wait_until_terminal(client, failed_id)
        failed = client.post(f"/research/{failed_id}/stop")
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    store = app.state.session_store
    with TestClient(app) as client:
        publishing_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        closing_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        for session_id in (publishing_id, closing_id):
            _wait(client, session_id, lambda body: body["current_agent"] == "planner")
        client.portal.call(
            store.require(publishing_id).publish,
            _event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0),
        )
        publishing = client.post(f"/research/{publishing_id}/stop")
        store._closing = True
        closing = client.post(f"/research/{closing_id}/stop")
        store._closing = False
        client.portal.call(runner.release.set)

    assert [_error(response) for response in (completed, failed, publishing, closing)] == [
        (409, "not_stoppable", "finished"),
        (409, "not_stoppable", "finished"),
        (409, "not_stoppable", "publishing"),
        (409, "not_stoppable", "closing"),
    ]


def test_a_stop_while_the_check_waits_answers_202_and_refuses_the_answers() -> None:
    """AC28 for a session in ``needs_input`` (D33's step ``check``) and AC31's answers half; the
    check's questions stay on the status, answered by nobody."""
    app = create_app(runner=GateRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, lambda body: body["status"] == "needs_input")
        stopped = client.post(f"/research/{session_id}/stop")
        answers = client.post(f"/research/{session_id}/answers", json={"answers": [], "skip": True})
        status = _status(client, session_id)

    assert stopped.status_code == 202
    assert (stopped.json()["status"], stopped.json()["stopped_step"]) == ("stopped", "check")
    assert _error(answers) == (409, "not_waiting_for_input", None)
    assert [question["id"] for question in status["clarification"]["questions"]] == ["q1", "q2", "q3"]
    assert status["clarification"]["answers"] == []
```

- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py -q 2>&1 | tail -5
```

Expected — the route does not exist yet, so FastAPI answers `404 {"detail":"Not Found"}`:

```
FAILED tests/test_api/test_stop.py::test_stop_route_codes - AssertionError: {"detail":"Not Found"}
FAILED tests/test_api/test_stop.py::test_the_stop_route_says_why_it_refuses - KeyError: 'error'
FAILED tests/test_api/test_stop.py::test_a_stop_while_the_check_waits_answers_202_and_refuses_the_answers - assert 404 == 202
3 failed, 9 passed, 2 warnings in 1.51s
```

- [ ] **Step 3: Add the route, and answer a stopped session as a halted run**

`src/deep_research/api/app.py` — replace

```python
from deep_research.api.sessions import (
    NotesClosed,
    NotWaitingForInput,
```

with

```python
from deep_research.api.sessions import (
    NotesClosed,
    NotStoppable,
    NotWaitingForInput,
```

`src/deep_research/api/app.py` — replace

```python
    "note_limit_reached": "Research session takes no more notes.",
}
```

with

```python
    "note_limit_reached": "Research session takes no more notes.",
    "not_stoppable": "Research session can no longer be stopped.",
}
```

`src/deep_research/api/app.py` — replace

```python
        errors=[error.model_copy(deep=True) for error in session.errors],
        **outcome_response_fields(session.outcome),
```

with

```python
        errors=[error.model_copy(deep=True) for error in session.errors],
        stopped_step=session.stopped_step,
        **outcome_response_fields(session.outcome),
```

`src/deep_research/api/app.py` — replace

```python
        return NoteAcceptedResponse(note_id=received.note_id)

    @router.get(
        "/research/{session_id}/status",
```

with

```python
        return NoteAcceptedResponse(note_id=received.note_id)

    @router.post(
        "/research/{session_id}/stop",
        status_code=202,
        response_model=ResearchSessionResponse,
    )
    async def stop_research(request: Request) -> ResearchSessionResponse:
        """Stop a session at once (notes-progress-report spec §8.1, D17).

        ``202`` with the stopped session: its run is cancelled where it stands — every
        provider, search and page request in flight with it — nothing is written, and
        ``session.stopped`` is its stream's last event. ``404`` for an unknown session;
        ``409 not_stoppable`` once it has ended (``reason: finished``, a second stop
        included), once the run has decided to publish or end (``publishing``), and
        while the service shuts down (``closing``).
        """
        try:
            session = store.stop(request.state.session_id)
        except KeyError:
            raise ApiProblem(code="session_not_found", status_code=404) from None
        except NotStoppable as refusal:
            raise ApiProblem(
                code="not_stoppable", status_code=409, reason=refusal.reason
            ) from None
        return _session_response(session)

    @router.get(
        "/research/{session_id}/status",
```

`src/deep_research/api/app.py` — replace

```python
        fabricated body and never a fake 404.
        """
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None
        if session.outcome is None:
```

with

```python
        fabricated body and never a fake 404. A stopped session wrote none and
        answers as a halted run does (notes-progress-report spec §8.4, D26).
        """
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None
        if session.status == "stopped":
            raise ApiProblem(code="report_unavailable", status_code=409)
        if session.outcome is None:
```

`src/deep_research/api/app.py` — replace

```python
            raise ApiProblem(code="session_not_found", status_code=404) from None
        if session.outcome is None:
            raise ApiProblem(code="session_not_complete", status_code=409)
        if format == "markdown":
```

with

```python
            raise ApiProblem(code="session_not_found", status_code=404) from None
        if session.status == "stopped":
            # notes-progress-report spec §8.4 (D26): a stopped run composed nothing.
            raise ApiProblem(code="evidence_unavailable", status_code=409)
        if session.outcome is None:
            raise ApiProblem(code="session_not_complete", status_code=409)
        if format == "markdown":
```

- [ ] **Step 4: Run them to make sure they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py -q 2>&1 | tail -1
.venv/Scripts/python.exe -m pytest tests/test_api -q 2>&1 | tail -1
```

Expected: `12 passed, 2 warnings`; then `241 passed, 2 warnings` (Task 3's 238 + 3).

- [ ] **Step 5: Document the route**

`README.md` — replace

```
one-time check and one for the reader's notes, all served by a process-local
```

with

```
one-time check, one for the reader's notes and one to stop a session, all served by a process-local
```

`README.md` — replace

```
| `POST` | `/research/{session_id}/notes` | `202` `{"note_id": "n1", "status": "received"}` (a reader note, below) |
```

with

```
| `POST` | `/research/{session_id}/notes` | `202` `{"note_id": "n1", "status": "received"}` (a reader note, below) |
| `POST` | `/research/{session_id}/stop` | `202` `ResearchSessionResponse` with `status: "stopped"` (Stop, below) |
```

`README.md` — replace

```
`incomplete`, or `failed`. Poll `status` or subscribe to the stream —
```

with

```
`incomplete`, or `failed` — or `stopped`, when the reader stops it (below). Poll `status` or subscribe to the stream —
```

`README.md` — replace

````
`notes_remaining`, `note_passes`, and `clarification` (the one-time check's questions and
the answers the run started with, or `null`).
````

with

````
`notes_remaining`, `note_passes`, and `clarification` (the one-time check's questions and
the answers the run started with, or `null`).

**Stop** (notes-progress-report spec §8). A session that is `running` or `needs_input`
can be stopped at once, from the one-time check until the run decides to publish:

```bash
curl -X POST http://localhost:8000/research/<session_id>/stop
```

The `202` carries the snapshot with `status: "stopped"` and `stopped_step`: the step it
was on — `check` while the one-time check waited, else the pipeline row (`planner` …
`report_reviewer`). The run is cancelled where it stands, with every provider, search and
page request it had in flight; nothing is published — no report, evidence log, quality
record or memory entry — and the stream's last event is `session.stopped`
(`{step, stopped_at, elapsed_seconds}`). A stopped session answers `/report` and
`/evidence` as a halted run does (`409 report_unavailable`, `409 evidence_unavailable`),
refuses notes and answers, and reads every note it took `not_checked`. A stop is refused
with `409 not_stoppable` once the session has ended (`reason: finished`, a second stop
included), once the run has decided to publish or end (`publishing`), and while the
service shuts down (`closing`).
````

`README.md` — replace

```
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace`, `/answers` and `/notes` |
```

with

```
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace`, `/answers`, `/notes` and `/stop` |
```

`README.md` — replace

```
or past its tenth note (`note_limit_reached`) |
```

with

```
or past its tenth note (`note_limit_reached`); a report or evidence log asked of a stopped session (`report_unavailable`, `evidence_unavailable`); a stop sent once the session has ended, once the run has decided to publish, or while the service shuts down (`not_stoppable`, with `reason` `finished`, `publishing` or `closing`) |
```

`docs/design/api-gaps.md` — replace

```
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`:238-263`) |
| `GET` | `/research/{id}/evidence` | `200` JSON or `text/markdown` (`?format=`), or `409` `session_not_complete` / `evidence_unavailable` (`:313-338`) |
```

with

```
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`report_unavailable` for a stopped session too) (`:238-263`) |
| `GET` | `/research/{id}/evidence` | `200` JSON or `text/markdown` (`?format=`), or `409` `session_not_complete` / `evidence_unavailable` (`evidence_unavailable` for a stopped session too) (`:313-338`) |
```

`docs/design/api-gaps.md` — replace

```
`api/sessions.py` `add_note`) |
```

with

```
`api/sessions.py` `add_note`) |
| `POST` | `/research/{id}/stop` | `202` `ResearchSessionResponse` with `status: "stopped"`: the run is cancelled where it stands and writes nothing; `404` unknown session; `409` `not_stoppable` with `reason` `finished` (it has ended, a second stop included), `publishing` (from the run's published decision to publish or end) or `closing` (the service shutting down) (`api/app.py` `stop_research`, `api/sessions.py` `stop`; notes-progress-report spec §8) |
```

`docs/design/api-gaps.md` — replace

```
`api/sessions.py:73-128`), 22 fields: `session_id`, `query`, `status`,
```

with

```
`api/sessions.py:73-128`), 23 fields: `session_id`, `query`, `status`,
```

`docs/design/api-gaps.md` — replace

```
questions and the answers the run started with, or `null`). `status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
`max_iterations`, `incomplete`, `failed`. Not on the response:
```

with

```
questions and the answers the run started with, or `null`), and `stopped_step` (the step a
stopped session was stopped at — `check` or a pipeline row — else `null`). `status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
`max_iterations`, `incomplete`, `failed`, `stopped` (the reader stopped the run:
terminal, nothing published). Not on the response:
```

`docs/design/api-gaps.md` — replace

```
session itself (`api/sessions.py`, `_clarify`), before the graph starts.
```

with

```
session itself (`api/sessions.py`, `_clarify`), before the graph starts. A stopped
session's last event is `session.stopped` (`{step, stopped_at, elapsed_seconds}`),
published by the session itself (`api/sessions.py`, `stop`); nothing is published after it.
```

`docs/design/api-gaps.md` — replace

```
| 3.1 | `max_iterations` echo | obsolete 2026-09-28: the console no longer shows a pass ceiling or sends a budget (live-briefs D14, D15); `max_extra_passes` stays on the stream at `graph.session.started` |
```

with

```
| 3.1 | `max_iterations` echo | obsolete 2026-09-28: the console no longer shows a pass ceiling or sends a budget (live-briefs D14, D15); `max_extra_passes` stays on the stream at `graph.session.started` |
| — | cancel a running session | closed 2026-09-30 (notes-progress-report spec §8): `POST /research/{id}/stop`, the terminal `stopped` status with `stopped_step`, and `session.stopped`; no report, evidence log, quality record or memory entry is written |
```

`docs/design/api-gaps.md` — replace

```
- **Re-run / cancel endpoints.** `SessionStore` cancels only on shutdown. A cancel
  route would need task ownership and a partial-artifact question the API has not
  answered; until it does, the console offers new research instead.
```

with

```
- **Re-run endpoints.** The cancel half closed on 2026-09-30: `POST /research/{id}/stop`
  cancels the session's task and writes no partial artifact, because `finalize_report`
  never runs (notes-progress-report spec §8). A re-run or resume route stays out: a
  stopped session's **Ask again** starts a new session with the same question.
```

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/app.py tests/test_api/test_stop.py README.md docs/design/api-gaps.md
git commit -m "feat(api): POST /research/{id}/stop, and a stopped session answers as a halted run (notes-progress-report §8.1, §8.4)"
```

---

### Task 5: A stop cancels every call in flight: the async search client (spec §8.3, D25, §12 R9; AC29)

**Files** (line numbers locate the anchors at the end of Task 4):
- Create: `tests/test_api/test_stop_inflight.py`
- Modify: `src/deep_research/tools/web_search.py:5-11` (imports), `:37-46` (the protocols), `:67`, `:97` (the client), `:132-166` (`_search_with_retries`)
- Test: `tests/test_tools/test_web_search.py:1-7` (imports), appended after `:692`; `tests/test_evaluation/test_dependencies_controlled.py:477-490`, `:509-510`, `:519-520`

**Interfaces:**
- Consumes: Task 3's `SessionStore.stop`.
- Produces: `deep_research.tools.web_search.AsyncSearchClient` (a `Protocol` with `async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]`); `WebSearchTool(..., client: SearchClient | AsyncSearchClient | None = None, ...)`, whose default client is `tavily.AsyncTavilyClient(api_key=api_key)`; an async client is awaited on the run's own loop, a synchronous one in `asyncio.to_thread`, both inside the same `asyncio.wait_for` and after the same reservation.

Every other cancellation path §8.3 lists already holds at `f4282818` and needs no code: LangGraph cancels and awaits its node tasks (`.venv/Lib/site-packages/langgraph/pregel/_runner.py:471`, `_executor.py:186-205`), `gather` fan-outs cancel their children, and `AsyncOpenAI` and `httpx` requests close with their tasks. `test_stop_cancels_inflight_calls` proves the stop reaches a stub provider call and an async search inside the running node on the real graph.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools/test_web_search.py` — replace

```python
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
```

with

```python
import asyncio
import json
import threading
import time
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from tavily import AsyncTavilyClient
```

Append to `tests/test_tools/test_web_search.py`:

```python


# ---------------------------------------------------------------------------
# The async default client (notes-progress-report spec §8.3, D25)
#
# A stop cancels the run's task. A search the tool awaits on the run's own loop
# is cancelled with it; one running in a worker thread cannot be stopped. So the
# default client is tavily's ``AsyncTavilyClient``, and an injected synchronous
# client (the replay double, the evaluation harness, the fakes above) keeps the
# thread path.
# ---------------------------------------------------------------------------


class _AsyncSearchClient:
    """An async client that answers from a queue, or waits until it is cancelled."""

    def __init__(self, responses: list[Mapping[str, Any] | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = False

    async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]:
        self.calls += 1
        self.started.set()
        if not self.responses:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_default_search_client_is_async(tracker) -> None:
    """D25: a tool built with a key and no client holds tavily's async client; nothing is sent."""
    tool = WebSearchTool(tracker, api_key="tvly-test-key")
    assert isinstance(tool._client, AsyncTavilyClient)
    await tool._client.close()


@pytest.mark.asyncio
async def test_search_cancelled_with_task(tracker) -> None:
    """§8.3: cancelling the task that awaits a search cancels the request in flight, after
    exactly one reserved unit."""
    budget = _tavily_budget()
    client = _AsyncSearchClient()
    tool = WebSearchTool(tracker, client=client, request_budget=budget)

    async def search() -> ToolResult:
        async with tracker.session_span("session-1", "question"):
            return await tool.execute(query="topic")

    task = asyncio.create_task(search())
    await asyncio.wait_for(client.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.cancelled is True
    assert budget.snapshot("tavily").attempts == 1


@pytest.mark.asyncio
async def test_sync_search_client_runs_in_thread(tracker) -> None:
    """An injected synchronous client still works, off the run's loop, in a worker thread."""
    loop_thread = threading.get_ident()
    threads: list[int] = []

    class ThreadRecordingClient(FakeSearchClient):
        def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]:
            threads.append(threading.get_ident())
            return super().search(query=query, search_depth=search_depth, max_results=max_results)

    client = ThreadRecordingClient([_search_response()])
    tool = WebSearchTool(tracker, client=client)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert len(threads) == 1 and threads[0] != loop_thread


@pytest.mark.asyncio
async def test_search_5xx_retried(tracker) -> None:
    """R9: the async client raises ``httpx.HTTPStatusError`` for a status tavily does not map, so a
    503 is retried under the tool's own rule, each retry reserving its own unit."""
    budget = _tavily_budget()
    client = _AsyncSearchClient([_status_error(503), _status_error(503), _search_response()])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(tracker, client=client, sleep=sleep, request_budget=budget)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert result.metadata["retry_count"] == 2
    assert client.calls == 3
    assert budget.snapshot("tavily").attempts == 3
    assert delays == [0.5, 1.0]
```

`test_sync_search_client_runs_in_thread` passes before and after this task: it pins the thread path the change must keep.

`tests/test_evaluation/test_dependencies_controlled.py` — replace

```python
    ``WebSearchTool`` folds the key into ``TavilyClient(api_key=...)`` at
    construction rather than storing it, so the proof is constructive: the
    patched constructor raises, and building a bundle must never call it.
    """
    from deep_research.tools import web_search as web_search_module

    def explode(api_key=None, **kwargs):
        raise AssertionError(
            f"TavilyClient must not be constructed in controlled mode "
            f"(received api_key={api_key!r}, {sorted(kwargs)})"
        )

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-should-never-be-used")
    monkeypatch.setattr(web_search_module, "TavilyClient", explode)
```

with

```python
    ``WebSearchTool`` folds the key into ``AsyncTavilyClient(api_key=...)``
    at construction rather than storing it (notes-progress-report spec §8.3,
    D25), so the proof is constructive: the patched constructor raises, and
    building a bundle must never call it.
    """
    from deep_research.tools import web_search as web_search_module

    def explode(api_key=None, **kwargs):
        raise AssertionError(
            f"AsyncTavilyClient must not be constructed in controlled mode "
            f"(received api_key={api_key!r}, {sorted(kwargs)})"
        )

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-should-never-be-used")
    monkeypatch.setattr(web_search_module, "AsyncTavilyClient", explode)
```

`tests/test_evaluation/test_dependencies_controlled.py` — replace

```python
    import httpx
    from tavily import TavilyClient
```

with

```python
    import httpx
    from tavily import AsyncTavilyClient, TavilyClient
```

`tests/test_evaluation/test_dependencies_controlled.py` — replace

```python
    assert search._client is not None
    assert not isinstance(search._client, TavilyClient)
```

with

```python
    assert search._client is not None
    assert not isinstance(search._client, (AsyncTavilyClient, TavilyClient))
```

Create `tests/test_api/test_stop_inflight.py`:

```python
"""Stop reaches every call in flight (notes-progress-report spec §8.3; D17, D25; AC29).

The real graph runs with stub agents: the researcher's turn holds a provider call and an
async search in flight until it is cancelled, and the publisher records what the finalizer
would write. No provider, no network.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.api.sessions import SessionStore
from deep_research.graph.orchestrator import ResearchAgents, compile_research_graph, run_research_graph
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.tools.web_search import WebSearchTool
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchState
from tests.graph_fakes import FakePublisher, fake_research_agents


class HangingSearch:
    """An async search client whose request never answers until it is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("a hanging search never answers")


class InFlightResearcher:
    """A researcher whose turn holds a provider call and a search in flight until cancelled."""

    name = "researcher"

    def __init__(self, tracker: Tracker) -> None:
        self.search = HangingSearch()
        self.tool = WebSearchTool(tracker, client=self.search)
        self.calling = asyncio.Event()
        self.cancelled_at: float | None = None

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async def provider_call() -> None:
            self.calling.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled_at = time.monotonic()
                raise

        await asyncio.gather(provider_call(), self.tool.execute(query="grid storage limits"))
        raise AssertionError("a turn held in flight never returns")


def _graph_runner(agents: ResearchAgents, tracker: Tracker) -> Callable[..., Awaitable[ResearchOutcome]]:
    """The ``ResearchRunner`` shape over the real graph, compiled from ``agents``."""

    async def runner(*, question: str, session_id: str, event_handler: Any = None, **_: Any) -> ResearchOutcome:
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id=session_id,
            question=question, event_handler=event_handler,
        )
        return build_outcome(run, metrics=())

    return runner


@pytest.mark.asyncio
async def test_stop_cancels_inflight_calls() -> None:
    """AC29 on the real graph: a stop cancels the running node's provider call and its async
    search within a second; no later node starts and no later agent is called; nothing is
    published, so no file and no memory entry is written (§8.3)."""
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False, project="stop-tests", api_key=None))
    researcher = InFlightResearcher(tracker)
    publisher = FakePublisher()
    agents = fake_research_agents(researcher=researcher, publisher=publisher)
    store = SessionStore(runner=_graph_runner(agents, tracker))
    store.start(
        session_id="s1", query="What limits grid-scale battery storage?", max_extra_passes=None,
        output_format="markdown", config_overrides={}, config_path="config.yaml", settings=ConfigSettings(),
    )
    session = store.require("s1")
    await asyncio.wait_for(researcher.calling.wait(), timeout=5)
    await asyncio.wait_for(researcher.search.started.wait(), timeout=5)

    asked = time.monotonic()
    store.stop("s1")
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)

    assert session.task.cancelled()
    assert researcher.cancelled_at is not None and researcher.cancelled_at - asked < 1.0
    assert researcher.search.cancelled is True
    await asyncio.sleep(0.2)
    started = [event.metadata["node"] for event in session.events if event.event_type == "graph.node.started"]
    assert started == ["planner", "researcher"]
    assert (session.stopped_step, session.events[-1].event_type) == ("researcher", "session.stopped")
    assert [agents.source_evaluator.calls, agents.evidence_verifier.calls, agents.report_writer.calls] == [[], [], []]
    assert (publisher.report_writes, publisher.memory_writes) == (0, 0)
```

`FakePublisher` records every document and memory write the finalizer attempts (`tests/graph_fakes.py:398-495`); publication and memory writes happen only in `finalize_report` (`graph/nodes.py:554-654`, `:683-790`), so zero attempts is "no file and no memory entry".

**Only if Task 1 Step 1's tool-lock line printed `0`** — the latency work (D38) merged first, so a page is fetched once per URL outside the tool lock and the download is shared between loops (`docs/superpowers/audits/2026-09-30-latency-audit.md:112`). Spec §8.3 then makes this phase add "a shared fetch in flight to `test_stop_cancels_inflight_calls`". This plan predates that work and cannot name its helpers, so write the addition against them, to this contract:
1. `InFlightResearcher.run` also starts two page fetches of one URL through the run's `web_scraper` tool, inside the same `asyncio.gather`, so the two share one download.
2. That download never answers. Where the latency work hands the scraper its shared `httpx.AsyncClient`, give it `httpx.AsyncClient(transport=httpx.MockTransport(handler))`, whose async `handler` counts its calls, awaits `asyncio.Event().wait()`, and records the time it receives `CancelledError` before re-raising it.
3. The test waits for the handler's first call as it waits for `researcher.search.started`. After `store.stop("s1")` it also asserts that the handler was called once (one shared download) and that it recorded its `CancelledError` less than 1 s after the stop.

If Step 4 then fails only on these assertions, the shared fetch is not cancelled with the run, which §8.3 requires ("a fetch task it shares must belong to the run and be cancelled with it"). Stop and report it with the failure; do not loosen the test.

- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tools/test_web_search.py tests/test_evaluation/test_dependencies_controlled.py tests/test_api/test_stop_inflight.py -q 2>&1 | grep -E "^(FAILED|ERROR) |^[0-9]+ (failed|passed)"
```

Expected — five failures, then the count (about 7 s: the two cancellation tests wait out their 1 s and 5 s bounds; `…` stands for the checkout's own path):

```
FAILED tests/test_tools/test_web_search.py::test_default_search_client_is_async - assert False
FAILED tests/test_tools/test_web_search.py::test_search_cancelled_with_task - TimeoutError
FAILED tests/test_tools/test_web_search.py::test_search_5xx_retried - AssertionError: assert False is True
FAILED tests/test_evaluation/test_dependencies_controlled.py::test_controlled_bundles_never_receive_a_tavily_key - AttributeError: <module 'deep_research.tools.web_search' from '…\src\deep_research\tools\web_search.py'> has no attribute 'AsyncTavilyClient'
FAILED tests/test_api/test_stop_inflight.py::test_stop_cancels_inflight_calls - TimeoutError
5 failed, 53 passed, 4 warnings in 7.01s
```

The default client is still the synchronous `TavilyClient`, and an async client's `search` is called from the worker thread and never awaited: the fake never starts (the two `TimeoutError`s), and the tool returns a failed result (`assert False is True`). `test_sync_search_client_runs_in_thread` passes.

- [ ] **Step 3: Await an async client on the run's loop**

`src/deep_research/tools/web_search.py` — replace

```python
import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from tavily import TavilyClient
```

with

```python
import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from tavily import AsyncTavilyClient
```

`src/deep_research/tools/web_search.py` — replace

```python
class SearchClient(Protocol):
    """The synchronous subset of the Tavily client used by this tool."""

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]: ...
```

with

```python
class SearchClient(Protocol):
    """The synchronous subset of a Tavily client: the tool runs it in a worker thread.

    Injected clients take this shape — the replay double, the evaluation harness and
    the test fakes — and return at once.
    """

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]: ...


class AsyncSearchClient(Protocol):
    """The asynchronous subset of a Tavily client, the default's (``AsyncTavilyClient``).

    The tool awaits it on the run's own loop, so cancelling the run — a stop —
    cancels a search in flight (notes-progress-report spec §8.3, D25).
    """

    async def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]: ...
```

`src/deep_research/tools/web_search.py` — replace

```python
        client: SearchClient | None = None,
```

with

```python
        client: SearchClient | AsyncSearchClient | None = None,
```

`src/deep_research/tools/web_search.py` — replace

```python
        self._client = client or TavilyClient(api_key=api_key)
```

with

```python
        # The client is never closed at the end of a run, as the provider's
        # ``AsyncOpenAI`` client is not (notes-progress-report spec §8.3 item 6).
        self._client: SearchClient | AsyncSearchClient = client or AsyncTavilyClient(
            api_key=api_key
        )
```

`src/deep_research/tools/web_search.py` — replace

```python
        budget = self._request_budget

        def search_once() -> Mapping[str, Any]:
            """The one transport attempt the loop already reserved a unit for."""
            return self._client.search(
                query=query,
                search_depth=self._search_depth,
                max_results=max_results,
            )

        attempts = self._max_retries + 1
        for attempt in range(attempts):
            # The reservation happens here rather than inside ``search_once``,
            # so it stays outside the cancellable window below. ``reserve`` is
            # synchronous and touches no network, but it is not instantaneous,
            # and a reservation made inside a work item handed to
            # ``asyncio.to_thread`` is cancelled the moment ``wait_for`` times
            # out: the refusal then lands on an already-cancelled future, is
            # dropped, and a spent ceiling is republished as an ordinary
            # timeout — a failed ``ToolResult`` recorded as ``agent_tool_failed``
            # instead of the run-ending refusal this ceiling exists to produce.
            # Reserving in the loop body keeps the ordering the ceiling relies
            # on: every real client call, the first and each retry, is preceded
            # by exactly one reservation, and a refused attempt is neither
            # charged nor sent. The refusal is not inside the caught tuple, so
            # it ends the request instead of being retried, and it never counts
            # as an attempt of its own, because the budget refuses before it
            # increments.
            if budget is not None:
                budget.reserve("tavily")
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(search_once),
                    timeout=self._timeout_s,
                )
```

with

```python
        budget = self._request_budget
        client = self._client
        # notes-progress-report spec §8.3 (D25): an async client — the default — is
        # awaited on the run's own loop, so cancelling the run cancels its request;
        # an injected synchronous client runs in a worker thread, as before.
        awaited = inspect.iscoroutinefunction(client.search)

        def search_once() -> Mapping[str, Any]:
            """One synchronous transport attempt the loop already reserved a unit for."""
            return client.search(  # type: ignore[return-value]
                query=query,
                search_depth=self._search_depth,
                max_results=max_results,
            )

        attempts = self._max_retries + 1
        for attempt in range(attempts):
            # The reservation happens here, before the attempt and outside the
            # cancellable window below, on both paths. ``reserve`` is
            # synchronous and touches no network, but it is not instantaneous.
            # On the thread path a reservation made inside the work item handed
            # to ``asyncio.to_thread`` is cancelled the moment ``wait_for`` times
            # out: the refusal then lands on an already-cancelled future, is
            # dropped, and a spent ceiling is republished as an ordinary
            # timeout — a failed ``ToolResult`` recorded as ``agent_tool_failed``
            # instead of the run-ending refusal this ceiling exists to produce.
            # On the async path the attempt is a coroutine on the run's own
            # loop, which a timeout or the run's cancellation (a stop) cancels
            # together with its request; reserving outside it keeps the two
            # paths alike. Reserving in the loop body keeps the ordering the
            # ceiling relies on: every real client call, the first and each
            # retry, is preceded by exactly one reservation, and a refused
            # attempt is neither charged nor sent. The refusal is not inside the
            # caught tuple, so it ends the request instead of being retried, and
            # it never counts as an attempt of its own, because the budget
            # refuses before it increments.
            if budget is not None:
                budget.reserve("tavily")
            try:
                return await asyncio.wait_for(
                    client.search(  # type: ignore[arg-type]
                        query=query,
                        search_depth=self._search_depth,
                        max_results=max_results,
                    )
                    if awaited
                    else asyncio.to_thread(search_once),
                    timeout=self._timeout_s,
                )
```

The caught tuple and `_is_retryable` are unchanged. tavily's async client maps 400, 401, 403, 429, 432 and 433 to its own exceptions and raises `httpx.HTTPStatusError` for any other failing status (`.venv/Lib/site-packages/tavily/async_tavily.py:162-171`), so a 5xx now reaches the caught tuple and is retried; the synchronous client raised `requests.HTTPError`, which was not (R9).

- [ ] **Step 4: Run them to make sure they pass, and the suites around the tool**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tools/test_web_search.py tests/test_evaluation/test_dependencies_controlled.py tests/test_api/test_stop_inflight.py -q 2>&1 | tail -1
.venv/Scripts/python.exe -m pytest tests/test_tools tests/test_runtime tests/test_api tests/test_evaluation/test_dependencies_controlled.py -q 2>&1 | tail -1
```

Expected: `58 passed, 1 warning` (Task 1 Step 4's second count + 5); then `631 passed, 2 warnings`.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/tools/web_search.py tests/test_tools/test_web_search.py tests/test_evaluation/test_dependencies_controlled.py tests/test_api/test_stop_inflight.py
git commit -m "feat(tools): search through AsyncTavilyClient so a stop cancels a search in flight (notes-progress-report §8.3, D25)"
```

---

### Task 6: The web app's model of a stopped session (spec §8.4's web rows, §4 item 3, §8.5 `stopResearch`; AC32's web half)

**Files** (line numbers locate the anchors at the end of Task 5):
- Create: `web/lib/stop.ts`, `web/test/stop.test.ts`, `web/test/active-row.test.ts`
- Modify: `web/lib/api.ts:2-3` (`SessionStatus`), `:39-40` (`stopped_step`), `:145-148` (`stopResearch` after `addNote`); `web/lib/format.ts:9-10`, `:23-31`, `:70-71`; `web/lib/run-state.ts:56-57`, `:77-78`, `:97`, `:353-356`; `web/components/Sidebar.tsx:50-54`
- Test: `web/test/api.test.ts:3`, `:76`; `web/test/format.test.ts:40-43`, `:48`; `web/test/components/status-chip.test.tsx`, appended after `:27`; `web/test/run-state.test.ts:29`, `:38-39`, `:50`, appended after `:344`; `web/test/components/sidebar.test.tsx`, appended after `:52`

**Interfaces:**
- Consumes: the API of Tasks 3–4 (`status: "stopped"`, `stopped_step`, `session.stopped {step, stopped_at, elapsed_seconds}`, `POST /research/{id}/stop`).
- Produces:
  - `web/lib/api.ts`: `SessionStatus` includes `"stopped"`; `ResearchSessionResponse.stopped_step?: string | null`; `stopResearch(sessionId: string): Promise<ApiResult<ResearchSessionResponse>>` (one POST, no body, never retried).
  - `web/lib/format.ts`: `STATUS.stopped = { label: "Stopped by you", dot: "dot-neutral" }` (the dot union gains `"dot-neutral"`); `statusNote` for `stopped` is `at {s.step}`, or `step not recorded` when `s.step` is null. `SessionView.step` carries the stopped step's label for a stopped session (spec ambiguity 7).
  - `web/lib/run-state.ts`: `interface StoppedRun { step: string | null; at: string | null; elapsedSeconds: number | null }`; `RunState.stopped: StoppedRun | null` (`null` in `newRunState()`); handler `session.stopped` sets it, and `active = null`, `loop = "off"`, `arc = null`, every mark kept.
  - `web/lib/stop.ts`: the copy constants `STOP_LABEL`, `STOP_TITLE`, `STOP_BODY`, `STOP_KEEP`, `STOP_CONFIRM`, `STOP_TOO_LATE`, `STOP_CLOSE`, `STOP_FAILED`, `STOPPED_EYEBROW`, `STOPPED_KEPT`, `ASK_AGAIN`, `ASK_AGAIN_FAILED`; `stoppedStepLabel(step: string | null | undefined): string | null`; `minutesIn(seconds: number): string`; `localClock(iso: string | null | undefined): string | null`; `secondsBetween(startedAt: string | null | undefined, finishedAt: string | null | undefined): number | null`; `stoppedLine(step: string | null, at: string | null | undefined, seconds: number | null): string`.
  - `Sidebar`: a stopped row's `aria-label` is `{query} — stopped by you`; `data-run="0"`; no visible word.

- [ ] **Step 1: Write the failing tests**

`web/test/api.test.ts` — replace

```ts
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, streamUrl, submitAnswers } from "../lib/api";
```

with

```ts
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, stopResearch, streamUrl, submitAnswers } from "../lib/api";
```

`web/test/api.test.ts` — replace

```ts
  it("builds same-origin URLs", () => {
```

with

```ts
  it("posts a stop once, with no body, to the session's stop route (notes-progress-report spec §8.1)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(202, { session_id: "s1", status: "stopped", stopped_step: "researcher" }, { "x-deep-research-mode": "replay" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await stopResearch("s1");
    expect([result.data.status, result.data.stopped_step, result.mode]).toEqual(["stopped", "researcher", "replay"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/research/s1/stop");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect([init.method, init.body]).toEqual(["POST", undefined]);
  });
  it("maps a refused stop's 409 to ApiError with its reason, and never retries it", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(409, { error: { code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "publishing", issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const error = await stopResearch("s1").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.body.code, error.body.reason]).toEqual([409, "not_stoppable", "publishing"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("builds same-origin URLs", () => {
```

`web/test/format.test.ts` — replace

```ts
  it("needs_input → Waiting for you · a few quick questions, on the warn dot (live-briefs spec §4.5)", () => {
    expect(statusNote(toSessionView({ ...base, status: "needs_input" }, "Planning"))).toBe("a few quick questions");
    expect(STATUS.needs_input).toEqual({ label: "Waiting for you", dot: "dot-warn" });
  });
```

with

```ts
  it("needs_input → Waiting for you · a few quick questions, on the warn dot (live-briefs spec §4.5)", () => {
    expect(statusNote(toSessionView({ ...base, status: "needs_input" }, "Planning"))).toBe("a few quick questions");
    expect(STATUS.needs_input).toEqual({ label: "Waiting for you", dot: "dot-warn" });
  });
  it("stopped → Stopped by you · at {step}, on the neutral dot (notes-progress-report spec §8.4)", () => {
    expect(statusNote(toSessionView({ ...base, status: "stopped", stopped_step: "researcher" }, "Researching"))).toBe("at Researching");
    expect(statusNote(toSessionView({ ...base, status: "stopped", stopped_step: "check" }, "the questions"))).toBe("at the questions");
    expect(statusNote(toSessionView({ ...base, status: "stopped" }))).toBe("step not recorded");
    expect(STATUS.stopped).toEqual({ label: "Stopped by you", dot: "dot-neutral" });
  });
```

`web/test/format.test.ts` — replace

```ts
    expect((["running", "needs_input", "completed", "max_iterations", "incomplete", "failed"] as const).map(isLive)).toEqual([true, true, false, false, false, false]);
```

with

```ts
    expect((["running", "needs_input", "completed", "max_iterations", "incomplete", "failed", "stopped"] as const).map(isLive)).toEqual([true, true, false, false, false, false, false]);
```

Append to `web/test/components/status-chip.test.tsx`:

```tsx

describe("StatusChip — a session the reader stopped (notes-progress-report spec §8.4)", () => {
  it("reads Stopped by you · at {step} on the neutral dot", () => {
    const { container } = render(<StatusChip view={view({ status: "stopped", step: "Researching" })} />);
    expect(text(container)).toBe("Stopped by you · at Researching");
    expect(container.querySelector(".dot")!.className).toBe("dot dot-neutral");
  });
});
```

`web/test/run-state.test.ts` — replace

```ts
  it("has the seven rows and the twenty-two handlers", () => {
```

with

```ts
  it("has the seven rows and the twenty-three handlers", () => {
```

`web/test/run-state.test.ts` — replace

```ts
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "source_evaluator.evaluation.completed",
```

with

```ts
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "session.stopped", "source_evaluator.evaluation.completed",
```

`web/test/run-state.test.ts` — replace

```ts
      "passFindings", "plan", "rearmed", "rearmedFirst", "reopen", "topics",
```

with

```ts
      "passFindings", "plan", "rearmed", "rearmedFirst", "reopen", "stopped", "topics",
```

Append to `web/test/run-state.test.ts`:

```ts

describe("session.stopped (notes-progress-report spec §8.4)", () => {
  it("records the step, the time and the seconds; leaves no row active and no loop lit; keeps every mark", () => {
    const events = extraPass.events;
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
    const run = replayRun(events.slice(0, decided + 1));
    const before = structuredClone(run);
    expect(newRunState().stopped).toBeNull();
    applyEvent(run, { type: "session.stopped", metadata: { step: "researcher", stopped_at: "2026-09-30T20:41:07+00:00", elapsed_seconds: 391 } });
    expect(run.stopped).toEqual({ step: "researcher", at: "2026-09-30T20:41:07+00:00", elapsedSeconds: 391 });
    expect([run.active, run.loop, run.arc]).toEqual([null, "off", null]);
    expect([run.marks, run.rearmed, run.topics]).toEqual([before.marks, before.rearmed, before.topics]);
    applyEvent(run, { type: "session.stopped", metadata: {} });
    expect(run.stopped).toEqual({ step: null, at: null, elapsedSeconds: null });
  });
});
```

Append to `web/test/components/sidebar.test.tsx`:

```tsx

describe("Sidebar — a session the reader stopped (notes-progress-report spec §8.4, D29)", () => {
  it("carries no mark and says it was stopped in its accessible name only", async () => {
    const now = new Date().toISOString();
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [{ ...session("a", now), status: "stopped", stopped_step: "researcher" }] })));
    render(<ConsoleProvider><Sidebar /></ConsoleProvider>);
    await waitFor(() => expect(document.querySelector('[data-session="a"]')).toBeTruthy());
    const row = document.querySelector('[data-session="a"]')!;
    expect([row.getAttribute("data-run"), row.getAttribute("aria-label"), row.textContent]).toEqual(["0", "q a — stopped by you", "q a"]);
  });
});
```

Create `web/test/stop.test.ts`:

```ts
// @vitest-environment node
// notes-progress-report spec §8.4-§8.5: the stopped session's words — the step's label, the time,
// how far in. Times are built in local time, so the clock reads the same in any time zone.
import { describe, expect, it } from "vitest";
import { localClock, minutesIn, secondsBetween, stoppedLine, stoppedStepLabel } from "../lib/stop";

describe("the stopped session's words (notes-progress-report spec §8.4-§8.5)", () => {
  it("labels the step a stop records: a row's label, or the questions for the one-time check", () => {
    expect(["check", "planner", "researcher", "report_reviewer", "finalize_report", null, "nonsense"].map((step) => stoppedStepLabel(step)))
      .toEqual(["the questions", "Planning", "Researching", "Reviewing", "Publishing", null, null]);
  });
  it("counts whole minutes in: less than a minute, 1 minute, then n minutes", () => {
    expect([0, 59, 60, 119, 120, 391].map(minutesIn)).toEqual([
      "less than a minute in", "less than a minute in", "1 minute in", "1 minute in", "2 minutes in", "6 minutes in",
    ]);
  });
  it("reads the time in the reader's own zone, 24-hour, and nothing for a missing or unreadable one", () => {
    expect(localClock(new Date(2026, 8, 30, 20, 41, 7).toISOString())).toBe("20:41");
    expect(localClock(new Date(2026, 8, 30, 7, 5, 0).toISOString())).toBe("07:05");
    expect([localClock(null), localClock("not a time")]).toEqual([null, null]);
  });
  it("says when the reader stopped and how far in — or that the run had not started", () => {
    const at = new Date(2026, 8, 30, 20, 41, 7).toISOString();
    expect(stoppedLine("researcher", at, 391)).toBe("You stopped this research at 20:41, 6 minutes in.");
    expect(stoppedLine("planner", at, 12)).toBe("You stopped this research at 20:41, less than a minute in.");
    expect(stoppedLine("check", at, 30)).toBe("You stopped this research at 20:41, before it started.");
    expect(stoppedLine("researcher", null, null)).toBe("You stopped this research.");
  });
  it("measures a run's whole seconds, and nothing for a missing or reversed pair", () => {
    expect(secondsBetween("2026-09-30T20:34:36+00:00", "2026-09-30T20:41:07+00:00")).toBe(391);
    expect([secondsBetween(null, "2026-09-30T20:41:07+00:00"), secondsBetween("2026-09-30T20:41:07+00:00", "2026-09-30T20:34:36+00:00")]).toEqual([null, null]);
  });
});
```

Create `web/test/active-row.test.ts`:

```ts
// @vitest-environment node — reads the replay captures (same reason as run-state.test.ts).
// notes-progress-report spec §8.2, AC32: the step a stop records is the row the page shows as active.
// This recomputes the page's own rule (`run.active`, lib/run-state.ts) after every prefix of every
// captured replay and compares it with test/fixtures/active-rows.json, which
// tests/test_api/test_stop.py::test_active_row_matches_web_rule holds the API's `active_row` to.
// After `npm run capture:events` re-records the captures, rewrite the file from the page's rule —
// `WRITE_ACTIVE_ROWS=1 npx vitest run test/active-row.test.ts` — review the diff, and run both tests.
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { applyEvent, newRunState, toRunEvent } from "../lib/run-state";

type Run = [string | null, number];
const fixtures = fileURLToPath(new URL("./fixtures/", import.meta.url));
const captures = path.join(fixtures, "events");
const goldenPath = path.join(fixtures, "active-rows.json");
const cases = readdirSync(captures).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -".json".length)).sort();

function activeRows(events: ResearchEvent[]): (string | null)[] {
  const run = newRunState();
  return [run.active, ...events.map((event) => { applyEvent(run, toRunEvent(event)); return run.active; })];
}
function runLength(rows: (string | null)[]): Run[] {
  const out: Run[] = [];
  for (const row of rows) {
    const last = out[out.length - 1];
    if (last && last[0] === row) last[1] += 1; else out.push([row, 1]);
  }
  return out;
}
const computed: Record<string, Run[]> = Object.fromEntries(cases.map((id) => {
  const capture = JSON.parse(readFileSync(path.join(captures, id + ".json"), "utf8")) as { events: ResearchEvent[] };
  return [id, runLength(activeRows(capture.events))];
}));
if (process.env.WRITE_ACTIVE_ROWS === "1") {
  writeFileSync(goldenPath, "{\n" + cases.map((id) => ` ${JSON.stringify(id)}: ${JSON.stringify(computed[id])}`).join(",\n") + "\n}\n");
}
const golden = JSON.parse(readFileSync(goldenPath, "utf8")) as Record<string, Run[]>;

describe("the page's active row after every prefix of every captured replay (AC32)", () => {
  it("covers every capture, and nothing else", () => {
    expect(Object.keys(golden).sort()).toEqual(cases);
  });
  for (const id of cases) {
    it(id, () => {
      expect(computed[id]).toEqual(golden[id]);
      expect(computed[id].reduce((sum, [, count]) => sum + count, 0)).toBe(JSON.parse(readFileSync(path.join(captures, id + ".json"), "utf8")).events.length + 1);
    });
  }
});
```

- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/api.test.ts test/format.test.ts test/components/status-chip.test.tsx test/run-state.test.ts test/components/sidebar.test.tsx test/stop.test.ts test/active-row.test.ts 2>&1 | grep -E "FAIL|Test Files|Tests  ")
```

Expected — nine `FAIL` lines (the files may finish in any order), then the counts:

```
 FAIL  test/stop.test.ts [ test/stop.test.ts ]
 FAIL  test/api.test.ts > the client > posts a stop once, with no body, to the session's stop route (notes-progress-report spec §8.1)
 FAIL  test/api.test.ts > the client > maps a refused stop's 409 to ApiError with its reason, and never retries it
 FAIL  test/format.test.ts > statusNote — one rule per API status > stopped → Stopped by you · at {step}, on the neutral dot (notes-progress-report spec §8.4)
 FAIL  test/run-state.test.ts > the port is the prototype's core > has the seven rows and the twenty-three handlers
 FAIL  test/run-state.test.ts > the run state holds only what the page reads (Phase 2 final review R6) > has no pass cap, loop tag or blurbs, exports no BLURB, and graph.session.started changes nothing
 FAIL  test/run-state.test.ts > session.stopped (notes-progress-report spec §8.4) > records the step, the time and the seconds; leaves no row active and no loop lit; keeps every mark
 FAIL  test/components/sidebar.test.tsx > Sidebar — a session the reader stopped (notes-progress-report spec §8.4, D29) > carries no mark and says it was stopped in its accessible name only
 FAIL  test/components/status-chip.test.tsx > StatusChip — a session the reader stopped (notes-progress-report spec §8.4) > reads Stopped by you · at {step} on the neutral dot
 Test Files  6 failed | 1 passed (7)
      Tests  8 failed | 59 passed (67)
```

`test/stop.test.ts` cannot load (`Cannot find module '../lib/stop'`), so its five tests are not counted; `stopResearch is not a function`; `statusNote` has no `stopped` case; the run state has no `session.stopped` handler and no `stopped` field; the sidebar names no stopped row; the chip still reads `Running · Researching`. `test/active-row.test.ts` passes already: it pins the page's existing rule against the file Task 3 wrote.

- [ ] **Step 3: Implement the model**

`web/lib/api.ts` — replace

```ts
/* "needs_input": the one-time check is waiting for the reader (live-briefs spec §4.4); not terminal. */
export type SessionStatus = "running" | "needs_input" | "completed" | "max_iterations" | "incomplete" | "failed";
```

with

```ts
/* "needs_input": the one-time check is waiting for the reader (live-briefs spec §4.4); not terminal.
   "stopped": the reader stopped the run (notes-progress-report spec §8); terminal, nothing published. */
export type SessionStatus = "running" | "needs_input" | "completed" | "max_iterations" | "incomplete" | "failed" | "stopped";
```

`web/lib/api.ts` — replace

```ts
  notes?: ReaderNoteRecord[]; notes_remaining?: number; note_passes?: number; clarification?: ClarificationRecord | null;
}
```

with

```ts
  notes?: ReaderNoteRecord[]; notes_remaining?: number; note_passes?: number; clarification?: ClarificationRecord | null;
  /* notes-progress-report spec §8.4: the step the reader stopped the run at — "check" or a pipeline
     row's node id — on a stopped session, null otherwise. Optional because a response recorded before
     Stop existed carries none. */
  stopped_step?: string | null;
}
```

`web/lib/api.ts` — replace

```ts
export async function addNote(sessionId: string, text: string): Promise<ApiResult<NoteAcceptedResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/notes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
  return { data: (await r.json()) as NoteAcceptedResponse, mode: modeOf(r) };
}
```

with

```ts
export async function addNote(sessionId: string, text: string): Promise<ApiResult<NoteAcceptedResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/notes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
  return { data: (await r.json()) as NoteAcceptedResponse, mode: modeOf(r) };
}
/* Stop a session, posted once with no body (notes-progress-report spec §8.1): 202 with the stopped
   session; 409 not_stoppable once it has ended, is publishing or the service is closing. */
export async function stopResearch(sessionId: string): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/stop`, { method: "POST" });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
```

`web/lib/format.ts` — replace

```ts
  /* The running row's label (live-briefs spec §4.2: "Running · {step}"); null when not known. */
  step: string | null;
```

with

```ts
  /* The running row's label (live-briefs spec §4.2: "Running · {step}"), or the label of the step a
     stopped session was stopped at (notes-progress-report spec §8.4: "Stopped by you · at {step}");
     null when not known. */
  step: string | null;
```

`web/lib/format.ts` — replace

```ts
/* Label and dot per API status (api/models.py:13-19). The second clause is built by statusNote(). */
export const STATUS: Record<SessionStatus, { label: string; dot: "dot-live" | "dot-ok" | "dot-warn" | "dot-danger" }> = {
  running: { label: "Running", dot: "dot-live" },
  needs_input: { label: "Waiting for you", dot: "dot-warn" },
  completed: { label: "Completed", dot: "dot-ok" },
  max_iterations: { label: "Partially completed", dot: "dot-warn" },
  incomplete: { label: "Partially completed", dot: "dot-warn" },
  failed: { label: "Failed", dot: "dot-danger" },
};
```

with

```ts
/* Label and dot per API status (api/models.py, SessionStatus). The second clause is built by
   statusNote(). A session the reader stopped sits on a neutral dot: stopping is neither a failure
   nor a warning (notes-progress-report spec D18). */
export const STATUS: Record<SessionStatus, { label: string; dot: "dot-live" | "dot-ok" | "dot-warn" | "dot-danger" | "dot-neutral" }> = {
  running: { label: "Running", dot: "dot-live" },
  needs_input: { label: "Waiting for you", dot: "dot-warn" },
  completed: { label: "Completed", dot: "dot-ok" },
  max_iterations: { label: "Partially completed", dot: "dot-warn" },
  incomplete: { label: "Partially completed", dot: "dot-warn" },
  failed: { label: "Failed", dot: "dot-danger" },
  stopped: { label: "Stopped by you", dot: "dot-neutral" },
};
```

`web/lib/format.ts` — replace

```ts
    case "failed": return "halted";
    default: return s.step ?? "starting";
```

with

```ts
    case "failed": return "halted";
    case "stopped": return s.step === null ? "step not recorded" : "at " + s.step;
    default: return s.step ?? "starting";
```

`web/lib/run-state.ts` — replace

```ts
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }
export interface RunState {
```

with

```ts
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }
/* notes-progress-report spec §8.2, §8.4: a stopped session's last event — the step it was on (a row's
   node id, or "check"), when (ISO) and how long it had run. A value the event does not carry is null,
   never invented. */
export interface StoppedRun { step: string | null; at: string | null; elapsedSeconds: number | null }
export interface RunState {
```

`web/lib/run-state.ts` — replace

```ts
  notes: NoteState[];                     /* the reader's notes, in receipt order */
}
```

with

```ts
  notes: NoteState[];                     /* the reader's notes, in receipt order */
  stopped: StoppedRun | null;             /* session.stopped: the reader stopped the run; null otherwise */
}
```

`web/lib/run-state.ts` — replace

```ts
    reopen: {}, outcomes: {}, open: new Set(), clarify: null, notes: [],
```

with

```ts
    reopen: {}, outcomes: {}, open: new Set(), clarify: null, notes: [], stopped: null,
```

`web/lib/run-state.ts` — replace

```ts
    note.fallback = md.fallback === true;
    note.where = run.active;
  },
};
```

with

```ts
    note.fallback = md.fallback === true;
    note.where = run.active;
  },
  /* notes-progress-report spec §8.4: the reader stopped the run. No row is active and no loop is lit;
     every mark stays as recorded, so the stopped stage can freeze the pipeline where it was. */
  "session.stopped": (run, md) => {
    run.stopped = {
      step: isText(md.step) ? md.step : null,
      at: isText(md.stopped_at) ? md.stopped_at : null,
      elapsedSeconds: typeof md.elapsed_seconds === "number" && Number.isFinite(md.elapsed_seconds) ? md.elapsed_seconds : null,
    };
    run.active = null; run.loop = "off"; run.arc = null;
  },
};
```

Create `web/lib/stop.ts`:

```ts
// Stop in the web app (notes-progress-report spec §8.4-§8.5; D18, D24, D33): the confirmation's copy,
// the stopped stage's sentences, and the label of the step a stop records. Pure — a function of the
// status response and the stream's RunState only.
import { stepLabel } from "./run-state";

export const STOP_LABEL = "Stop";
export const STOP_TITLE = "Stop this research?";
/* D24: the canvas copy, verbatim. */
export const STOP_BODY = "It stops right away and nothing more is spent. What's done so far stays here, but no report is written.";
export const STOP_KEEP = "Keep going";
export const STOP_CONFIRM = "Stop research";
export const STOP_TOO_LATE = "Too late to stop — the research is finishing.";
export const STOP_CLOSE = "Close";
export const STOP_FAILED = "Couldn't stop — try again";
export const STOPPED_EYEBROW = "Stopped by you";
export const STOPPED_KEPT = "No report was written. The plan and what research found so far are kept below until the service restarts.";
export const ASK_AGAIN = "Ask again";
/* This plan's words (the spec names none): a POST /research from "Ask again" that failed. */
export const ASK_AGAIN_FAILED = "Couldn't ask again — try again";

/* The label of the step a stop records (spec §4 item 3): a row's STAGES label, "the questions" for the
   one-time check, null for none or a step the page does not know. */
export function stoppedStepLabel(step: string | null | undefined): string | null {
  return step === "check" ? "the questions" : stepLabel(step);
}
/* Whole minutes the run had run: "less than a minute in", "1 minute in", "{n} minutes in". */
export function minutesIn(seconds: number): string {
  if (seconds < 60) return "less than a minute in";
  const minutes = Math.floor(seconds / 60);
  return minutes === 1 ? "1 minute in" : minutes + " minutes in";
}
/* HH:MM in the reader's own time zone, 24-hour; null for a missing or unreadable time. */
export function localClock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
/* Whole seconds from a run's start to its end; null when either is missing or the end comes first. */
export function secondsBetween(startedAt: string | null | undefined, finishedAt: string | null | undefined): number | null {
  if (!startedAt || !finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  return Number.isFinite(ms) && ms >= 0 ? Math.floor(ms / 1000) : null;
}
/* The stopped note's first line (spec §8.5): when, and how far in — or, for a stop during the one-time
   check, that the run had not started. A part the page cannot read is left out, never invented. */
export function stoppedLine(step: string | null, at: string | null | undefined, seconds: number | null): string {
  const clock = localClock(at);
  const said = "You stopped this research" + (clock === null ? "" : " at " + clock);
  if (step === "check") return said + ", before it started.";
  return seconds === null ? said + "." : said + ", " + minutesIn(seconds) + ".";
}
```

`web/components/Sidebar.tsx` — replace

```tsx
              /* A running session is the only one that carries a mark: no chips, counts or durations here (index.html:1871-1876). */
              return (
                <li key={s.session_id}>
                  <button type="button" className="sb-item" data-session={s.session_id} data-run={running ? "1" : "0"} title={s.session_id}
                    aria-current={active === s.session_id ? "true" : "false"} aria-label={running ? `${s.query} — ${s.status === "needs_input" ? "waiting for you" : "running"}` : undefined}
```

with

```tsx
              /* A running session is the only one that carries a mark: no chips, counts or durations here (index.html:1871-1876).
                 A session the reader stopped says so in its accessible name only (notes-progress-report D29). */
              const label = running ? `${s.query} — ${s.status === "needs_input" ? "waiting for you" : "running"}`
                : s.status === "stopped" ? `${s.query} — stopped by you` : undefined;
              return (
                <li key={s.session_id}>
                  <button type="button" className="sb-item" data-session={s.session_id} data-run={running ? "1" : "0"} title={s.session_id}
                    aria-current={active === s.session_id ? "true" : "false"} aria-label={label}
```

- [ ] **Step 4: Run them to make sure they pass, and type-check**

```bash
(cd web && npx vitest run test/api.test.ts test/format.test.ts test/components/status-chip.test.tsx test/run-state.test.ts test/components/sidebar.test.tsx test/stop.test.ts test/active-row.test.ts 2>&1 | grep -E "Test Files|Tests  " && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  7 passed (7)`, `Tests  72 passed (72)`; no `typecheck` output; then `Test Files  30 passed (30)`, `Tests  233 passed (233)` (Task 1 Step 3's 28 files and 219 tests + 2 files and 14 tests).

- [ ] **Step 5: Commit**

```bash
git add web/lib/api.ts web/lib/format.ts web/lib/run-state.ts web/lib/stop.ts web/components/Sidebar.tsx web/test/api.test.ts web/test/format.test.ts web/test/components/status-chip.test.tsx web/test/run-state.test.ts web/test/components/sidebar.test.tsx web/test/stop.test.ts web/test/active-row.test.ts
git commit -m "feat(web): the stopped status, its chip, its event and the step it names (notes-progress-report §8.4)"
```

---

### Task 7: The stopped stage, and its design record (spec §8.5 "Stopped stage" and CSS, §8.4's `SessionScreen` and `Sidebar` rows, §8.6's DESIGN rows; D18, D29, D33; AC33's stage half)

**Files** (line numbers locate the anchors at the end of Task 6):
- Create: `web/components/UserStoppedStage.tsx`, `web/test/components/user-stopped-stage.test.tsx`
- Modify: `web/lib/briefs.ts:7` (`RowState`), appended after `:74`; `web/components/BriefSpine.tsx:3`, `:17`, `:29-30`, `:40`, `:57-66`, `:71-79`, `:102-108`, `:123`; `web/components/SessionScreen.tsx:9`, `:20`, `:25`, `:147-148`, `:163`, `:204`; `web/app/globals.css`, appended after `:1410`; `docs/design/DESIGN.md:150`, `:162`, `:240`, `:327-328`, `:721-722`, `:969-971`, `:982`, `:1030`, `:1637`, `:1681`; `docs/design/api-gaps.md:11-12`
- Test: `web/test/briefs.test.ts:6-7`, appended after `:86`

**Interfaces:**
- Consumes: Task 6's `RunState.stopped`, `stoppedStepLabel`, `stoppedLine`, `secondsBetween`, the copy constants, `STATUS.stopped`; Task 3's `status.stopped_step`.
- Produces:
  - `web/lib/briefs.ts`: `RowState` gains `"stopped" | "off"`; `stoppedSubtitle(run: RunState, id: NodeId): string`; `notRunText(run: RunState, id: NodeId): string`.
  - `BriefSpine` gains the optional prop `frozen?: NodeId` (today's props are `marks`, `run`, `onToggle`): rows before it as recorded, that row `data-state="stopped"` (openable, ` (stopped here)` for screen readers), later rows `data-state="off"`; no hand-off roles, no arc.
  - `UserStoppedStage({ status, run, strip, settings, onToggleRow })` renders `section#stage-user-stopped`; `SessionScreen` routes `status === "stopped"` to it before Failed and Report, and gives the chip the stopped step's label.
  - CSS: `.chip .dot-neutral`, `.stopped-note` (and its `.b-now`, `.b-sub`, `.stop-actions`), `.spine-lg.briefs > li[data-state="stopped"|"off"]`.

- [ ] **Step 1: Write the failing tests**

`web/test/briefs.test.ts` — replace

```ts
import { SENTENCES, STATIC_META, rowBrief, subtitleText, topicFact, verifyingSentence } from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, toRunEvent, type RunState } from "../lib/run-state";
```

with

```ts
import { SENTENCES, STATIC_META, notRunText, rowBrief, stoppedSubtitle, subtitleText, topicFact, verifyingSentence } from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, toRunEvent, type NodeId, type RunState } from "../lib/run-state";
```

Append to `web/test/briefs.test.ts`:

```ts

describe("the stopped row and the rows after it (notes-progress-report spec §8.5)", () => {
  it("Researching counts its topics after 'Stopped' — 'none of' before one is done, never a bare 0 — and a row with no live facts reads 'Stopped'", () => {
    const run = newRunState();
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped");
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 3, sub_topics: [{ coverage_id: "topic-01", title: "A" }, { coverage_id: "topic-02", title: "B" }, { coverage_id: "topic-03", title: "C" }] } });
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done · no pages read · no findings");
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-02", sub_topic: "B", index: 2, successful_reads: 41, findings_retained: 212 } });
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · 1 of 3 topics done · 41 pages read · 212 findings");
    expect((["planner", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"] as NodeId[]).map((id) => stoppedSubtitle(run, id))).toEqual(Array(5).fill("Stopped"));
  });
  it("a later row reads 'not run', or 'not run again' once a loop has re-armed it", () => {
    const run = newRunState();
    expect(notRunText(run, "source_evaluator")).toBe("not run");
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", iteration: 0 } });
    expect((["source_evaluator", "report_reviewer", "finalize_report"] as NodeId[]).map((id) => notRunText(run, id))).toEqual(["not run again", "not run again", "not run"]);
  });
});
```

Create `web/test/components/user-stopped-stage.test.tsx`:

```tsx
// notes-progress-report spec §8.5 (D18, D33; Stop.dc.html column 3): the stage of a session the reader
// stopped, rendered by SessionScreen from /status and the stream. Times are built in local time, so the
// clock reads the same in any time zone.
import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { SessionScreen } from "../../components/SessionScreen";
import { Topbar } from "../../components/Topbar";
import type { ResearchSessionResponse } from "../../lib/api";
import { readSubmission } from "../../lib/session-store";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useParams: () => ({}) }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const sse = (frames: string) => new Response(frames, { status: 200, headers: { "content-type": "text/event-stream", "x-deep-research-mode": "replay" } });
const frame = (n: number, type: string, metadata: Record<string, unknown> = {}) =>
  `id: ${n}\ndata: ${JSON.stringify({ event_type: type, source: "graph", message: "m", timestamp: "2026-09-30T00:00:00+00:00", metadata })}\n\n`;
const STARTED = new Date(2026, 8, 30, 20, 34, 36).toISOString();
const STOPPED_AT = new Date(2026, 8, 30, 20, 41, 7).toISOString();
const STOPPED: ResearchSessionResponse = {
  session_id: "s1", query: "Where can we get the best tasting lattes in San Jose?", status: "stopped", current_agent: null,
  iteration: 0, started_at: STARTED, finished_at: STOPPED_AT, report_path: null, trace_url: null, errors: [],
  evidence_path: null, quality_path: null, quality_contract_version: null, semantic_review_status: null,
  semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null, stopped_step: "researcher",
};
const TITLES = [{ coverage_id: "topic-01", title: "Published picks" }, { coverage_id: "topic-02", title: "Opening hours" }];
const RESEARCHING = [
  frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
  frame(2, "planner.planning.completed", { sub_topic_count: 2, sub_topics: TITLES }),
  frame(3, "graph.node.completed", { node: "planner" }),
  frame(4, "graph.node.started", { node: "researcher", iteration: 0 }),
  frame(5, "researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Published picks", index: 1 }),
  frame(6, "researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Opening hours", index: 2 }),
  frame(7, "researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Opening hours", index: 2, successful_reads: 3, findings_retained: 4 }),
  frame(8, "session.stopped", { step: "researcher", stopped_at: STOPPED_AT, elapsed_seconds: 391 }),
].join("");
const ROWS = ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"];

/* /status answers `status` and the stream `frames`; `post` answers a POST when it returns a response. */
function serve(status: ResearchSessionResponse, frames: string, post: (init: RequestInit) => Response | null = () => null) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") { const answer = post(init); if (answer) return answer; }
    if (url.includes("/stream")) return sse(frames);
    if (url.includes("/status")) return json(200, status);
    return json(200, { sessions: [] });
  });
}
const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const row = (id: string) => document.querySelector(`#stage-user-stopped #spine > li[data-stage="${id}"]`)!;
/* What a row says under its name: a finished row its outcome, any other row its live line. */
const said = (id: string) => {
  const state = row(id).getAttribute("data-state");
  return [state, text(row(id).querySelector(state === "done" || state === "loop" ? ".m-out" : ".m-live"))];
};

afterEach(() => { vi.unstubAllGlobals(); push.mockReset(); });

describe("the stopped stage (notes-progress-report spec §8.5; D18)", () => {
  it("says when the reader stopped and how far in, and that nothing was written, under the stopped chip", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    // The stage renders from /status at once; the stream's events, and the chip, land a moment later.
    await waitFor(() => expect(text(document.querySelector("#topbarStatus .chip"))).toBe("Stopped by you · at Researching"));
    await waitFor(() => expect(row("planner").getAttribute("data-state")).toBe("done"));
    const stage = document.getElementById("stage-user-stopped")!;
    expect(text(stage.querySelector(".ask-head .eyebrow"))).toBe("Stopped by you");
    expect(text(document.getElementById("user-stopped-h"))).toBe(STOPPED.query);
    const note = stage.querySelector(".stopped-note")!;
    expect(note.getAttribute("role")).toBe("status");
    expect([...note.querySelectorAll("p")].map((p) => text(p))).toEqual([
      "You stopped this research at 20:41, 6 minutes in.",
      "No report was written. The plan and what research found so far are kept below until the service restarts.",
    ]);
    expect([text(document.getElementById("askAgain")), document.getElementById("askAgain")!.className]).toEqual(["Ask again", "btn btn-ghost btn-sm"]);
    expect(["stage-failed", "stage-report", "stage-stopped", "stage-running"].map((id) => document.getElementById(id))).toEqual([null, null, null, null]);
    expect(document.querySelector("#topbarStatus .chip .dot")!.className).toBe("dot dot-neutral");
    expect(document.getElementById("stopBtn")).toBeNull();
  });

  it("freezes the pipeline at the stopped row: finished rows openable, the stopped row with its facts, later rows not run", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(ROWS.map(said)).toEqual([
      ["done", "2 sub-topics"],
      ["stopped", "Stopped · 1 of 2 topics done · 3 pages read · 4 findings"],
      ["off", "not run"], ["off", "not run"], ["off", "not run"], ["off", "not run"], ["off", "not run"],
    ]));
    expect(text(row("researcher").querySelector(".stage-name"))).toBe("Researching (stopped here)");
    expect(row("researcher").getAttribute("aria-current")).toBeNull();
    expect(ROWS.slice(0, 3).map((id) => row(id).querySelector("button.ps-toggle") !== null)).toEqual([true, true, false]);
    expect(row("researcher").getAttribute("data-open")).toBe("0");
    fireEvent.click(row("researcher").querySelector("button.ps-toggle")!);
    await waitFor(() => expect(row("researcher").getAttribute("data-open")).toBe("1"));
    const topics = [...row("researcher").querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((topic) => [topic.getAttribute("data-topic"), text(topic.querySelector(".tf"))])).toEqual([["stopped", "stopped"], ["done", "4 findings"]]);
    // No hand-off, no arc, no note line, no acknowledgement.
    expect(document.querySelector("#stage-user-stopped [data-handoff]")).toBeNull();
    expect(document.querySelector("#stage-user-stopped #spineWrap")!.getAttribute("data-loop")).toBe("off");
    expect(document.querySelector("#stage-user-stopped #spineWrap")!.hasAttribute("data-arc")).toBe(false);
    expect(document.getElementById("noteLine")).toBeNull();
    expect(document.querySelector("#stage-user-stopped .ack")).toBeNull();
  });

  it("reads 'not run again' on the rows a loop had re-armed, and 'Stopped' on a Researching row with no topics yet", async () => {
    const looped = [
      frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
      frame(2, "graph.node.completed", { node: "planner" }),
      ...["researcher", "source_evaluator", "evidence_verifier", "report_writer"].flatMap((node, i) => [
        frame(3 + 2 * i, "graph.node.started", { node, iteration: 0 }), frame(4 + 2 * i, "graph.node.completed", { node }),
      ]),
      frame(11, "graph.node.started", { node: "report_reviewer", iteration: 0 }),
      frame(12, "graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"], iteration: 0 }),
      frame(13, "graph.node.completed", { node: "report_reviewer" }),
      frame(14, "session.stopped", { step: "researcher", stopped_at: STOPPED_AT, elapsed_seconds: 391 }),
    ].join("");
    vi.stubGlobal("fetch", serve(STOPPED, looped));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(ROWS.map(said)).toEqual([
      ["done", "1–10 sub-topics"],
      ["stopped", "Stopped"],
      ["off", "not run again"], ["off", "not run again"], ["off", "not run again"], ["off", "not run again"], ["off", "not run"],
    ]));
  });

  it("shows no pipeline card after a stop during the one-time check, and says the run had not started (D33)", async () => {
    const check: ResearchSessionResponse = { ...STOPPED, stopped_step: "check" };
    const frames = frame(1, "session.clarification.requested", { questions: [], deadline_at: STOPPED_AT })
      + frame(2, "session.stopped", { step: "check", stopped_at: STOPPED_AT, elapsed_seconds: 30 });
    vi.stubGlobal("fetch", serve(check, frames));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(text(document.querySelector("#topbarStatus .chip"))).toBe("Stopped by you · at the questions"));
    expect(text(document.querySelector(".stopped-note .b-now"))).toBe("You stopped this research at 20:41, before it started.");
    expect(document.querySelector("#stage-user-stopped .card")).toBeNull();
  });

  it("reads the time and the minutes from the response until the stream has said them, moves focus to that line, and acknowledges no note in the stopped row", async () => {
    // The 202 (or /status) can land before the stream's session.stopped: Researching is still the
    // stream's active row then, and a note it acknowledged must not show in the frozen brief.
    const noted = [
      frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
      frame(2, "graph.node.completed", { node: "planner" }),
      frame(3, "graph.node.started", { node: "researcher", iteration: 0 }),
      frame(4, "session.note.received", { note_id: "n1", text: "Pastries too" }),
      frame(5, "session.note.interpreted", { note_id: "n1", restatement: "pastries too", kinds: ["emphasis"], replaces: null, fallback: false }),
    ].join("");
    vi.stubGlobal("fetch", serve(STOPPED, noted));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stoppedLine")).toBeTruthy());
    expect(text(document.getElementById("stoppedLine"))).toBe("You stopped this research at 20:41, 6 minutes in.");
    // Focus moves in an effect, which runs after the render that first shows the line.
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById("stoppedLine")));
    await waitFor(() => expect(row("planner").getAttribute("data-state")).toBe("done"));
    fireEvent.click(row("researcher").querySelector("button.ps-toggle")!);
    await waitFor(() => expect(row("researcher").getAttribute("data-open")).toBe("1"));
    expect(document.querySelector("#stage-user-stopped .ack")).toBeNull();
  });

  it("Ask again starts a new session with the same question and the default settings, records it and opens it", async () => {
    const posts: unknown[] = [];
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING, (init) => {
      posts.push(JSON.parse(String(init.body)));
      return json(202, { ...STOPPED, session_id: "s2", status: "running", finished_at: null, stopped_step: null });
    }));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("askAgain")).toBeTruthy());
    fireEvent.click(document.getElementById("askAgain")!);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/research/s2"));
    expect(posts).toEqual([{
      query: STOPPED.query, output_format: "markdown", ask_clarifying_questions: true,
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    }]);
    expect(readSubmission("s2")).not.toBeNull();
  });

  it("a failed Ask again says so and lets the reader try again", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING, () => json(500, { error: { code: "http_500", message: "x", reason: null, issues: [] } })));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("askAgain")).toBeTruthy());
    fireEvent.click(document.getElementById("askAgain")!);
    await waitFor(() => expect(text(document.getElementById("askAgainFailed"))).toBe("Couldn't ask again — try again"));
    expect(document.getElementById("askAgainFailed")!.getAttribute("role")).toBe("alert");
    expect((document.getElementById("askAgain") as HTMLButtonElement).disabled).toBe(false);
    expect(push).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/briefs.test.ts test/components/user-stopped-stage.test.tsx 2>&1 | grep -E "FAIL|Test Files|Tests  ")
```

Expected — nine `FAIL` lines, then the counts:

```
 FAIL  test/briefs.test.ts > the stopped row and the rows after it (notes-progress-report spec §8.5) > Researching counts its topics after 'Stopped' — 'none of' before one is done, never a bare 0 — and a row with no live facts reads 'Stopped'
 FAIL  test/briefs.test.ts > the stopped row and the rows after it (notes-progress-report spec §8.5) > a later row reads 'not run', or 'not run again' once a loop has re-armed it
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > says when the reader stopped and how far in, and that nothing was written, under the stopped chip
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > freezes the pipeline at the stopped row: finished rows openable, the stopped row with its facts, later rows not run
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > reads 'not run again' on the rows a loop had re-armed, and 'Stopped' on a Researching row with no topics yet
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > shows no pipeline card after a stop during the one-time check, and says the run had not started (D33)
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > reads the time and the minutes from the response until the stream has said them, moves focus to that line, and acknowledges no note in the stopped row
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > Ask again starts a new session with the same question and the default settings, records it and opens it
 FAIL  test/components/user-stopped-stage.test.tsx > the stopped stage (notes-progress-report spec §8.5; D18) > a failed Ask again says so and lets the reader try again
 Test Files  2 failed (2)
      Tests  9 failed | 7 passed (16)
```

`stoppedSubtitle is not a function` and `notRunText is not a function`; a `stopped` session still falls through to the report stage, so there is no `#stage-user-stopped` (`expected null to be truthy`, `Cannot read properties of null`), and its chip reads `Stopped by you · step not recorded`.

- [ ] **Step 3: The frozen spine**

`web/lib/briefs.ts` — replace

```ts
export type RowState = PaintedMark | "pending";
```

with

```ts
/* "stopped" and "off" occur only on the stopped stage's frozen spine (notes-progress-report spec §8.5). */
export type RowState = PaintedMark | "pending" | "stopped" | "off";
```

Append to `web/lib/briefs.ts`:

```ts
/* notes-progress-report spec §8.5: the stopped row's subtitle — "Stopped", then the live facts the row
   had when the reader stopped it. Before Phase B only Researching has live facts; its stopped line always
   counts the topics done ("none of 3", rather than its running "3 topics · researching", which would
   contradict "Stopped", and never a bare 0). A Researching row with no topics yet, and every other row,
   reads "Stopped"; Phase B gives each step its own facts here (§6.3–§6.7). */
export function stoppedSubtitle(run: RunState, id: NodeId): string {
  const topics = run.topics.length;
  if (id !== "researcher" || topics === 0) return "Stopped";
  const done = run.topics.filter((t) => t.state === "done").length;
  return "Stopped · " + (done === 0 ? "none" : String(done)) + " of " + plural(topics, "topic", "topics") + " done · "
    + countPhrase(run.pagesRead ?? 0, "page read", "pages read") + " · " + countPhrase(run.findingsSoFar ?? 0, "finding", "findings");
}
/* A row after the stopped one: "not run", or "not run again" when the loop the run was in had re-armed
   it — it ran in an earlier pass. */
export function notRunText(run: RunState, id: NodeId): string {
  return run.rearmed[id] ? "not run again" : "not run";
}
```

`web/components/BriefSpine.tsx` — replace

```tsx
import { rowBrief, subtitleText, type RowState, type Subtitle } from "@/lib/briefs";
```

with

```tsx
import { notRunText, rowBrief, stoppedSubtitle, subtitleText, type RowState, type Subtitle } from "@/lib/briefs";
```

`web/components/BriefSpine.tsx` — replace

```tsx
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void }
```

with

```tsx
/* `frozen`: the row the reader stopped the run at, on the stopped stage (notes-progress-report spec §8.5). */
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void; frozen?: NodeId }
```

`web/components/BriefSpine.tsx` — replace

```tsx
   never opens. The Failed and Stopped stages keep the compact <Spine>. */
export function BriefSpine({ marks, run, onToggle }: Props) {
```

with

```tsx
   never opens. The Failed and service-stopped stages keep the compact <Spine>.
   With `frozen` (notes-progress-report spec §8.5) it is the stopped stage's spine, frozen at the row the
   reader stopped: the rows before it as recorded, that row "stopped" (it opens to its frozen brief),
   every later row "off" — with no hand-off and no arc. */
export function BriefSpine({ marks, run, onToggle, frozen }: Props) {
```

`web/components/BriefSpine.tsx` — replace

```tsx
  if (run.active !== prevActive) {
```

with

```tsx
  if (frozen === undefined && run.active !== prevActive) {
```

`web/components/BriefSpine.tsx` — replace

```tsx
  useLoopArc(true, wrap, list, run.arc, [run.arc, run.loop, marks]);
  const rows = STAGES.map((s, i) => {
    const awaited = handoff?.awaiting === s.id;
    const st: RowState = awaited ? "active" : marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const finished = finishedState(st);
    const open = st === "active" || (finished && run.open.has(s.id));
    const brief = rowBrief(run, s.id, st);
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    const role = handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
```

with

```tsx
  useLoopArc(frozen === undefined, wrap, list, run.arc, [run.arc, run.loop, marks]);
  const frozenAt = frozen === undefined ? -1 : STAGES.findIndex((s) => s.id === frozen);
  const rows = STAGES.map((s, i) => {
    const awaited = handoff?.awaiting === s.id;
    const st: RowState = frozenAt >= 0
      ? (i < frozenAt ? marks[s.id] || "pending" : i === frozenAt ? "stopped" : "off")
      : awaited ? "active" : marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const finished = finishedState(st);
    const openable = finished || st === "stopped";
    const open = st === "active" || (openable && run.open.has(s.id));
    const brief = rowBrief(run, s.id, st);
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    const role = handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
    const fixed = st === "stopped" ? stoppedSubtitle(run, s.id) : st === "off" ? notRunText(run, s.id) : null;
```

`web/components/BriefSpine.tsx` — replace

```tsx
        <span className="stage-name" id={`name-${s.id}`}>{s.label}<span className="sr">{st === "active" ? " (in progress)" : ""}</span></span>
        <span className="stage-meta xf" id={`meta-${s.id}`}>
          <span className="m-live" aria-hidden={finished ? "true" : undefined}>
            {brief.subtitle.kind === "research" ? <ResearchSubtitle subtitle={brief.subtitle} /> : brief.subtitle.text}
          </span>
          <span className="m-out" aria-hidden={finished ? undefined : "true"}>{brief.outcome}</span>
        </span>
        <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        {finished ? <button type="button" className="ps-toggle" aria-expanded={open} aria-controls={`brief-${s.id}`} aria-labelledby={`name-${s.id} meta-${s.id}`} onClick={() => onToggle(s.id)} /> : null}
```

with

```tsx
        <span className="stage-name" id={`name-${s.id}`}>{s.label}<span className="sr">{st === "active" ? " (in progress)" : st === "stopped" ? " (stopped here)" : ""}</span></span>
        <span className="stage-meta xf" id={`meta-${s.id}`}>
          <span className="m-live" aria-hidden={finished ? "true" : undefined}>
            {fixed !== null ? fixed : brief.subtitle.kind === "research" ? <ResearchSubtitle subtitle={brief.subtitle} /> : brief.subtitle.text}
          </span>
          <span className="m-out" aria-hidden={finished ? undefined : "true"}>{brief.outcome}</span>
        </span>
        <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        {openable ? <button type="button" className="ps-toggle" aria-expanded={open} aria-controls={`brief-${s.id}`} aria-labelledby={`name-${s.id} meta-${s.id}`} onClick={() => onToggle(s.id)} /> : null}
```

`web/components/BriefSpine.tsx` — replace

```tsx
          {brief.topics.map((t) => (
            <div key={t.key} className="ln" role="listitem" data-topic={t.state} style={lineStyle(n++)}>
              <span className="mk" aria-hidden="true"><span className="ring" /><span className="dotc" /><svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg></span>
              <span className="tt"><span className="tn">{t.n}</span>{t.title}</span>
              <span className="tf">{t.fact}</span>
            </div>
          ))}
```

with

```tsx
          {brief.topics.map((t) => {
            // A topic still running when the reader stopped reads "stopped", with the ring (§8.5).
            const halted = st === "stopped" && t.state === "running";
            return (
              <div key={t.key} className="ln" role="listitem" data-topic={halted ? "stopped" : t.state} style={lineStyle(n++)}>
                <span className="mk" aria-hidden="true"><span className="ring" /><span className="dotc" /><svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg></span>
                <span className="tt"><span className="tn">{t.n}</span>{t.title}</span>
                <span className="tf">{halted ? "stopped" : t.fact}</span>
              </div>
            );
          })}
```

`web/components/BriefSpine.tsx` — replace

```tsx
        data-open={open ? "1" : "0"} data-handoff={role} data-toggle={finished ? "1" : undefined}
```

with

```tsx
        data-open={open ? "1" : "0"} data-handoff={role} data-toggle={openable ? "1" : undefined}
```

With `frozen` set the hand-off state is never derived (`handoff` stays `null`), so no row carries `data-handoff`.

- [ ] **Step 4: The stage**

Create `web/components/UserStoppedStage.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { startResearch, type ResearchSessionResponse } from "@/lib/api";
import { qFitClass } from "@/lib/format";
import { STAGES, marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { recordSubmission, type SubmittedSettings } from "@/lib/session-store";
import { ASK_AGAIN, ASK_AGAIN_FAILED, STOPPED_EYEBROW, STOPPED_KEPT, secondsBetween, stoppedLine } from "@/lib/stop";
import { BriefSpine } from "./BriefSpine";
import { DEFAULT_SETTINGS, buildRequest } from "./Composer";
import { useConsole } from "./ConsoleProvider";

/* notes-progress-report spec §8.5 (D18, D33; Stop.dc.html column 3): a session the reader stopped. The
   question and its settings; one short note — when, how far in, that no report was written — with "Ask
   again" (the same question, a new session); then the pipeline frozen at the stopped row. A stop during
   the one-time check shows no pipeline card (D33). The service-stopped stage (#stage-stopped,
   SessionScreen.tsx) is a different stage: the service shut down, the reader did not stop anything. */
export function UserStoppedStage({ status, run, strip, settings, onToggleRow }: {
  status: ResearchSessionResponse; run: RunState; strip: ReactNode; settings: SubmittedSettings | null; onToggleRow(id: NodeId): void;
}) {
  const router = useRouter();
  const { noteMode, refreshSessions } = useConsole();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const line = useRef<HTMLParagraphElement>(null);
  // The response says the step; the stream's session.stopped says when and how long, and until it has
  // arrived the response's own times stand in (spec ambiguity 15).
  const step = status.stopped_step ?? run.stopped?.step ?? null;
  const at = run.stopped?.at ?? status.finished_at;
  const seconds = run.stopped?.elapsedSeconds ?? secondsBetween(status.started_at, status.finished_at);
  const frozen = STAGES.find((s) => s.id === step)?.id ?? null;
  // The Stop control that had focus went with the running stage: the note's first line takes it, so a
  // keyboard or screen-reader user keeps their place. It is not a control, so it draws no ring.
  useEffect(() => { if (document.activeElement === document.body) line.current?.focus(); }, []);

  async function askAgain() {
    if (busy) return;
    setBusy(true); setFailed(false);
    const chosen = settings ?? DEFAULT_SETTINGS;
    try {
      const result = await startResearch(buildRequest(status.query, chosen)); // one POST; never retried
      noteMode(result.mode);
      recordSubmission(result.data.session_id, chosen);
      void refreshSessions();
      router.push(`/research/${result.data.session_id}`);
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <section className="stage is-on" id="stage-user-stopped" aria-labelledby="user-stopped-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>{STOPPED_EYEBROW}</p>
          <h1 className={"ask-q ask-locked" + qFitClass(status.query)} id="user-stopped-h">{status.query}</h1>
          {strip}
        </div>
        <div className="stopped-note" role="status">
          <p className="b-now" id="stoppedLine" tabIndex={-1} ref={line}>{stoppedLine(step, at, seconds)}</p>
          <p className="b-sub">{STOPPED_KEPT}</p>
          <div className="stop-actions">
            <button className="btn btn-ghost btn-sm" id="askAgain" type="button" disabled={busy} onClick={() => void askAgain()}>{ASK_AGAIN}</button>
            {failed ? <span className="cap" id="askAgainFailed" role="alert">{ASK_AGAIN_FAILED}</span> : null}
          </div>
        </div>
        {frozen !== null ? (
          <div className="card stack" style={{ gap: "var(--space-5)" }}>
            {/* No row is active on a stopped session. Until the stream's session.stopped clears it, the
                stream's last active row is the stopped one, and its brief would acknowledge notes. */}
            <BriefSpine marks={marksFor(run, null)} run={{ ...run, active: null }} onToggle={onToggleRow} frozen={frozen} />
          </div>
        ) : null}
      </div>
    </section>
  );
}
```

`web/components/SessionScreen.tsx` — replace

```tsx
import { readSubmission, submittedBeatRemaining, type Submission } from "@/lib/session-store";
```

with

```tsx
import { readSubmission, submittedBeatRemaining, type Submission } from "@/lib/session-store";
import { stoppedStepLabel } from "@/lib/stop";
```

`web/components/SessionScreen.tsx` — replace

```tsx
import { SubmittedStage } from "./SubmittedStage";
```

with

```tsx
import { SubmittedStage } from "./SubmittedStage";
import { UserStoppedStage } from "./UserStoppedStage";
```

`web/components/SessionScreen.tsx` — replace

```tsx
   failed → Failed (Task 18) · other terminal → Report (Task 17). */
```

with

```tsx
   stopped → the stopped stage (notes-progress-report spec §8.5) · failed → Failed (Task 18) ·
   other terminal → Report (Task 17). */
```

`web/components/SessionScreen.tsx` — replace

```tsx
  // The topbar chip follows the run while streaming and /status afterwards. K7/M3: a stopped
  // session shows no chip at all — the sentence above the frozen pipeline already says what
```

with

```tsx
  // The topbar chip follows the run while streaming and /status afterwards. K7/M3: a service-stopped
  // session shows no chip at all — the sentence above the frozen pipeline already says what
```

`web/components/SessionScreen.tsx` — replace

```tsx
  const step = live && phase !== "asking" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent) : null;
```

with

```tsx
  // notes-progress-report spec §8.4: a session the reader stopped names the step it was stopped at.
  const step = live && phase !== "asking" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent)
    : status?.status === "stopped" ? stoppedStepLabel(status.stopped_step) : null;
```

`web/components/SessionScreen.tsx` — replace

```tsx
  if (status.status === "failed") return <FailedStage status={status} run={run.current} strip={strip} />;
```

with

```tsx
  if (status.status === "stopped") return <UserStoppedStage status={status} run={run.current} strip={strip} settings={submission?.settings ?? null} onToggleRow={toggleRow} />;
  if (status.status === "failed") return <FailedStage status={status} run={run.current} strip={strip} />;
```

Append to `web/app/globals.css`:

```css

/* ═══ 2026-09-30: Stop (notes-progress-report spec §8.4-§8.5; D18, D19, D29, D33; Stop.dc.html, progress.css:159-171) ═══ */
/* The stopped stage (#stage-user-stopped): one short note on the card surface, then the pipeline frozen
   at the row the reader stopped. The stopped row's node is quiet — a --muted edge, its digit in --fg, the
   surface fill, no halo — and later rows read in --meta. The chip's dot is neutral: stopping is neither a
   failure nor a warning. */
.chip .dot-neutral{background:var(--muted)}
.stopped-note{display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-4) var(--space-5);border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface)}
.stopped-note .b-now{font-size:var(--text-base);color:var(--fg);line-height:1.5}
.stopped-note .b-sub{font-size:var(--text-sm);color:var(--muted);line-height:1.5}
.stopped-note .stop-actions{display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap;padding-top:var(--space-1)}
/* Focus moves to the first line when this stage replaces the running one; it is not a control (D19). */
.stopped-note [tabindex="-1"]:focus{outline:none}
.spine-lg.briefs > li[data-state="stopped"] .bullet{border-color:var(--muted);color:var(--fg);background:var(--surface)}
.spine-lg.briefs > li[data-state="stopped"] .stage-name{color:var(--fg)}
.spine-lg.briefs > li[data-state="off"] .stage-name,.spine-lg.briefs > li[data-state="off"] .stage-meta{color:var(--meta)}
```

- [ ] **Step 5: Run them to make sure they pass, type-check, and check the stylesheet**

```bash
(cd web && npx vitest run test/briefs.test.ts test/components/user-stopped-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  " && npm run -s typecheck && npm run -s check:css && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  2 passed (2)`, `Tests  16 passed (16)`; no `typecheck` output; `OK`; then `Test Files  31 passed (31)`, `Tests  242 passed (242)` (Task 6's 233 + 9).

- [ ] **Step 6: Record the stage, the status and the sidebar row in the design**

`docs/design/DESIGN.md` — replace

```
One page, five stages, and a one-time check (2a) that can come between the second
```

with

```
One page, six stages, and a one-time check (2a) that can come between the second
```

`docs/design/DESIGN.md` — replace

```
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |
```

with

```
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |
| 6 | **Stopped by you** | `stopped` | The question, its settings and one short note — when the reader stopped and how far in, that no report was written — with **Ask again**; then the pipeline frozen at the stopped row (no pipeline card after a stop during the check) | Ask again (a new session, the same question), or New research |

**Stage 6 keeps what was done** (notes-progress-report D18, D29, D33, 2026-09-30). A session the
reader stopped (`stopped`, §4) opens on its own stage, `#stage-user-stopped` — not the
service-stopped stage a shutdown leaves (`running` with `finished_at`): the eyebrow
`Stopped by you`, the locked question and its settings strip, then one short note on the card
surface — `You stopped this research at {HH:MM}, {N} minutes in.` (the reader's own time;
`less than a minute in`, `1 minute in`), `No report was written. The plan and what research
found so far are kept below until the service restarts.` and **Ask again**, a ghost button that
starts a new session with the same question and settings. Below it the brief spine is frozen
at the row the reader stopped. Finished rows keep their outcomes and still open. The stopped
row has a quiet node — a `--muted` edge, its digit in `--fg`, the surface fill, no halo — the
subtitle `Stopped · {its live facts}` (Researching's facts line; `Stopped` for a row with
none), and it opens to its frozen brief, where a topic that was running reads `stopped` beside
its ring. Every later row reads `not run` in `--meta`, or `not run again` for a row the loop had
re-armed. There is no arc, no hand-off, no note line and no counters block. A stop during the
one-time check shows no pipeline card (D33), and the note reads `You stopped this research at
{HH:MM}, before it started.`
```

`docs/design/DESIGN.md` — replace

```
remains on the Failed and Stopped stages, as what survived the halt (§5.8).
```

with

```
remains on the Failed and service-stopped stages, as what survived the halt (§5.8).
```

`docs/design/DESIGN.md` — replace

```
session waits for the reader's answers). Under
`prefers-reduced-motion` the spin is suppressed, which leaves the hue and the text step.
```

with

```
session waits for the reader's answers). A session the reader stopped carries no ring; its
accessible name alone says so, ending in `— stopped by you` (notes-progress-report D29), with no
status word on screen. Under
`prefers-reduced-motion` the spin is suppressed, which leaves the hue and the text step.
```

`docs/design/DESIGN.md` — replace

```
stays on the lower row. The Failed and Stopped stages keep the compact rows and the
midpoint rule.
```

with

```
stays on the lower row. The Failed and service-stopped stages keep the compact rows and the
midpoint rule; stage 6 keeps this spine, frozen at the row the reader stopped.
```

`docs/design/DESIGN.md` — replace

```
The API's `SessionStatus` is a six-value literal (`api/models.py:32-39`;
`needs_input` joined it with the one-time check, live-briefs 2026-09-29). The
interface shows six statuses. This table is the contract between them, and it is
```

with

```
The API's `SessionStatus` is a seven-value literal (`api/models.py:37-45`;
`needs_input` joined it with the one-time check, live-briefs 2026-09-29, and `stopped`
with Stop, notes-progress-report 2026-09-30). The interface shows seven statuses. This
table is the contract between them, and it is
```

`docs/design/DESIGN.md` — replace

```
| **Failed** | `failed` | `errors` | `--fg` label, `--danger` dot | `Failed · halted`; the failed stage headlines the halting type |
```

with

```
| **Failed** | `failed` | `errors` | `--fg` label, `--danger` dot | `Failed · halted`; the failed stage headlines the halting type |
| **Stopped by you** | `stopped` | `stopped_step` | `--fg` label, neutral `--muted` dot | `Stopped by you · at {step}`, e.g. `Stopped by you · at Researching`; `at the questions` after a stop during the one-time check |
```

`docs/design/DESIGN.md` — replace

```
   stage's rail states it in full.
```

with

```
   stage's rail states it in full.
8. **`stopped` is the reader's own end, not a failure.** Its dot is neutral (`--muted`),
   never `--danger`, and a stop adds no error. Like a halt it publishes nothing:
   `GET /report` answers `409 report_unavailable` and `/evidence`
   `409 evidence_unavailable`, and every note the run took reads `not checked`.
```

`docs/design/DESIGN.md` — replace

```
outcome line once it is done (§3.4). The Failed and Stopped stages keep the block,
```

with

```
outcome line once it is done (§3.4). The Failed and service-stopped stages keep the block,
```

`docs/design/DESIGN.md` — replace

```
| Notes | nothing: `POST /research/{id}/notes`, the two `session.note.*` events and the status's `notes`, `notes_remaining` and `note_passes` serve them (live-briefs Phase 3); on the replay server a note is acknowledged but never applied (api-gaps 3.9) |
```

with

```
| Notes | nothing: `POST /research/{id}/notes`, the two `session.note.*` events and the status's `notes`, `notes_remaining` and `note_passes` serve them (live-briefs Phase 3); on the replay server a note is acknowledged but never applied (api-gaps 3.9) |
| Stopped | nothing: `POST /research/{id}/stop`, the `stopped` status with `stopped_step`, and `session.stopped` serve it (notes-progress-report §8) |
```

`docs/design/api-gaps.md` — replace

```
The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed), a one-time check that can come between 2 and 3
```

with

```
The console is one page with six stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed, 6 Stopped by you), a one-time check that can come between 2 and 3
```

- [ ] **Step 7: Commit**

```bash
git add web/lib/briefs.ts web/components/BriefSpine.tsx web/components/UserStoppedStage.tsx web/components/SessionScreen.tsx web/app/globals.css web/test/briefs.test.ts web/test/components/user-stopped-stage.test.tsx docs/design/DESIGN.md docs/design/api-gaps.md
git commit -m "feat(web): the stopped stage — when and how far in, Ask again, the pipeline frozen where it stopped (notes-progress-report §8.5)"
```

---

### Task 8: The Stop control and its confirmation (spec §8.5 "Stop control" and "Confirm popover", §8.6's DESIGN row; D17, D18, D24; AC33's control half)

**Files** (line numbers locate the anchors at the end of Task 7):
- Create: `web/components/StopControl.tsx`, `web/test/components/stop-control.test.tsx`
- Modify: `web/components/ConsoleProvider.tsx:6-7`, `:17-18`, `:27`, `:96-97`; `web/components/Topbar.tsx:5-7`, `:17-18`; `web/components/SessionScreen.tsx:31`, `:170`; `web/app/globals.css`, appended after `:1426`; `docs/design/DESIGN.md:178-180` (the end of stage 6's paragraph)
- Test: `web/test/components/session-screen.test.tsx`, appended after `:293`

**Interfaces:**
- Consumes: Task 6's `stopResearch`, `ApiError`, the copy constants; Task 7's stopped stage (a 202 hands the screen a `stopped` status, which it routes there).
- Produces:
  - `ConsoleProvider`: `export interface StopTarget { sessionId: string; onStopped(response: ResearchSessionResponse): void }`; `useConsole()` gains `stop: StopTarget | null` and `setStop(target: StopTarget | null): void`.
  - `SessionScreen` sets `{ sessionId, onStopped: setStatus }` while `isLive(status)`, `finished_at === null`, `run.finalStatus === null`, `chipStep(run) !== "finalize_report"` and `status.current_agent !== "finalize_report"`, and `null` otherwise.
  - `Topbar` renders `<StopControl key={stop.sessionId} target={stop} />` after the status chip and before the replay chip.
  - `StopControl({ target })`: `span.stop-anchor` > `button#stopBtn.btn.btn-ghost.btn-sm.btn-stop` (`aria-haspopup="dialog"`, `aria-expanded`) and, while open, `div#stopConfirm.stop-confirm[role=dialog][aria-labelledby=stopConfirmT]`.

- [ ] **Step 1: Write the failing tests**

Create `web/test/components/stop-control.test.tsx`:

```tsx
// notes-progress-report spec §8.5 (D17, D18, D24; Stop.dc.html columns 1-2): Stop and its one
// confirmation. The control is rendered on its own inside the provider; POST /stop is scripted per test.
import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { StopControl } from "../../components/StopControl";
import type { ResearchSessionResponse } from "../../lib/api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-deep-research-mode": "replay" } });
const STOPPED = { session_id: "s1", query: "q", status: "stopped", stopped_step: "researcher" } as unknown as ResearchSessionResponse;
const REFUSED = { error: { code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "publishing", issues: [] } };
const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
afterEach(() => vi.unstubAllGlobals());

/* `answer` scripts POST /stop; every other request is the sidebar's list read. */
function mount(answer: () => Promise<Response> | Response) {
  const stops: RequestInit[] = [];
  let lists = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/stop")) { stops.push(init ?? {}); return answer(); }
    lists++;
    return json(200, { sessions: [] });
  }));
  const onStopped = vi.fn();
  render(<ConsoleProvider><button type="button" id="elsewhere">elsewhere</button><StopControl target={{ sessionId: "s1", onStopped }} /></ConsoleProvider>);
  return { stops, onStopped, lists: () => lists };
}
const stopBtn = () => document.getElementById("stopBtn") as HTMLButtonElement;
const dialog = () => document.querySelector('[role="dialog"]');
const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>(".stop-confirm button")].find((b) => b.textContent === name)!;

describe("StopControl (notes-progress-report spec §8.5; D18, D24)", () => {
  it("is a small ghost button with a square, closed until pressed", () => {
    mount(() => json(202, STOPPED));
    expect(stopBtn().className).toBe("btn btn-ghost btn-sm btn-stop");
    expect(text(stopBtn())).toBe("Stop");
    expect(stopBtn().querySelector(".stop-sq")!.getAttribute("aria-hidden")).toBe("true");
    expect([stopBtn().getAttribute("aria-haspopup"), stopBtn().getAttribute("aria-expanded")]).toEqual(["dialog", "false"]);
    expect(dialog()).toBeNull();
  });

  it("asks once, in the canvas's words, with Keep going focused", () => {
    mount(() => json(202, STOPPED));
    fireEvent.click(stopBtn());
    const box = dialog()!;
    expect([box.id, box.className, box.getAttribute("aria-labelledby")]).toEqual(["stopConfirm", "stop-confirm", "stopConfirmT"]);
    expect(text(document.getElementById("stopConfirmT"))).toBe("Stop this research?");
    expect(text(box.querySelector(".b-sub"))).toBe("It stops right away and nothing more is spent. What's done so far stays here, but no report is written.");
    expect([...box.querySelectorAll("button")].map((b) => [b.textContent, b.className])).toEqual([
      ["Keep going", "btn btn-quiet btn-sm"], ["Stop research", "btn btn-sm btn-danger"],
    ]);
    expect(document.activeElement).toBe(button("Keep going"));
    expect([stopBtn().getAttribute("aria-expanded"), stopBtn().getAttribute("aria-controls")]).toEqual(["true", "stopConfirm"]);
  });

  it("Keep going, Escape and a click outside each close it and give focus back to Stop, sending nothing", () => {
    const { stops } = mount(() => json(202, STOPPED));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Keep going"));
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    fireEvent.click(stopBtn());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    fireEvent.click(stopBtn());
    // The press outside is cancelled (`false`): a browser would otherwise move focus to what was
    // pressed, or to the page itself, after the handler gave it back to Stop (review round 1, I1).
    expect(fireEvent.mouseDown(document.getElementById("elsewhere")!)).toBe(false);
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    expect(stops).toEqual([]);
  });

  it("Stop research posts once and disables both buttons while it waits; a 202 hands the stopped session over", async () => {
    let release!: (response: Response) => void;
    const { stops, onStopped, lists } = mount(() => new Promise<Response>((resolve) => { release = resolve; }));
    await waitFor(() => expect(lists()).toBe(1));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    expect([button("Keep going").disabled, button("Stop research").disabled]).toEqual([true, true]);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(document.getElementById("elsewhere")!);
    expect(dialog()).not.toBeNull();
    await act(async () => { release(json(202, STOPPED)); });
    await waitFor(() => expect(onStopped).toHaveBeenCalledWith(STOPPED));
    await waitFor(() => expect(dialog()).toBeNull());
    expect(stops.map((init) => [init.method, init.body])).toEqual([["POST", undefined]]);
    await waitFor(() => expect(lists()).toBe(2));
  });

  it("a 409 says it is too late, with Close focused, and hands nothing over", async () => {
    const { onStopped } = mount(() => json(409, REFUSED));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(text(document.getElementById("stopTooLate"))).toBe("Too late to stop — the research is finishing."));
    expect(text(document.getElementById("stopConfirmT"))).toBe("Stop this research?");
    expect([...dialog()!.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Close"]);
    // Focus follows the face in an effect, which runs after the render that shows it.
    await waitFor(() => expect(document.activeElement).toBe(button("Close")));
    fireEvent.click(button("Close"));
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    expect(onStopped).not.toHaveBeenCalled();
  });

  it("any other failure says so and lets the reader try again, one POST per press", async () => {
    let calls = 0;
    const { stops, onStopped } = mount(() => {
      calls++;
      return calls === 1 ? json(500, { error: { code: "http_500", message: "x", reason: null, issues: [] } }) : json(202, STOPPED);
    });
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(text(document.getElementById("stopFailed"))).toBe("Couldn't stop — try again"));
    expect(document.getElementById("stopFailed")!.getAttribute("role")).toBe("alert");
    expect([button("Keep going").disabled, button("Stop research").disabled]).toEqual([false, false]);
    await waitFor(() => expect(document.activeElement).toBe(button("Stop research")));
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(onStopped).toHaveBeenCalledTimes(1));
    expect(stops).toHaveLength(2);
  });
});
```

Append to `web/test/components/session-screen.test.tsx`:

```tsx

describe("SessionScreen — Stop (notes-progress-report spec §8.5, D17)", () => {
  const planning = frame(1, "graph.node.started", { node: "planner", iteration: 0 });
  const publishing = planning + frame(2, "graph.node.completed", { node: "planner" })
    + frame(3, "graph.route.decided", { destination: "finalize", reason: "report_accepted", iteration: 0 });
  const serve = (status: () => ResearchSessionResponse, frames: string, onStop?: () => Response) => vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/stop") && onStop) return onStop();
    if (url.includes("/stream")) return sse(frames);
    if (url.includes("/status")) return json(200, status());
    return json(200, { sessions: [] });
  });
  const topbar = () => [...document.querySelectorAll("#topbarStatus > *")].map((el) => el.id || el.className);

  it("is offered after the running chip and before the replay chip, and beside the check's waiting chip", async () => {
    vi.stubGlobal("fetch", serve(() => RUNNING, planning));
    const first = render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stopBtn")).toBeTruthy());
    await waitFor(() => expect(topbar()).toEqual(["chip", "stop-anchor", "modeChip"]));
    first.unmount();
    vi.stubGlobal("fetch", serve(() => ({ ...RUNNING, status: "needs_input" }), ""));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stopBtn")).toBeTruthy());
    expect(document.querySelector("#topbarStatus .chip")!.textContent!.replace(/\s+/g, " ").trim()).toBe("Waiting for you · a few quick questions");
  });

  it("is not offered once the stream shows Publishing, once /status does, or for a service-stopped session", async () => {
    vi.stubGlobal("fetch", serve(() => RUNNING, publishing));
    const streamed = render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.querySelector('#spine li[data-stage="finalize_report"]')?.getAttribute("data-state")).toBe("active"));
    // The screen withdraws Stop in an effect, after the render that shows Publishing.
    await waitFor(() => expect(document.getElementById("stopBtn")).toBeNull());
    streamed.unmount();
    for (const status of [{ ...RUNNING, current_agent: "finalize_report" }, STOPPED]) {
      vi.stubGlobal("fetch", serve(() => status, ""));
      const view = render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
      await waitFor(() => expect(document.getElementById("stage-loading")).toBeNull());
      expect(document.getElementById("stopBtn")).toBeNull();
      view.unmount();
    }
  });

  it("a stop the API takes moves the page to the stopped stage at once, and Stop is gone", async () => {
    let stopped = false;
    const after: ResearchSessionResponse = { ...RUNNING, status: "stopped", stopped_step: "planner", finished_at: "2026-09-27T00:05:00+00:00" };
    vi.stubGlobal("fetch", serve(() => (stopped ? after : RUNNING), planning, () => { stopped = true; return json(202, after); }));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stopBtn")).toBeTruthy());
    fireEvent.click(document.getElementById("stopBtn")!);
    fireEvent.click([...document.querySelectorAll<HTMLButtonElement>(".stop-confirm button")].find((b) => b.textContent === "Stop research")!);
    await waitFor(() => expect(document.getElementById("stage-user-stopped")).toBeTruthy());
    // The screen withdraws the target in an effect, one render after it adopts the stopped status.
    await waitFor(() => expect(document.getElementById("stopBtn")).toBeNull());
    await waitFor(() => expect(document.querySelector("#topbarStatus .chip")!.textContent!.replace(/\s+/g, " ").trim()).toBe("Stopped by you · at Planning"));
  });
});
```

- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/components/stop-control.test.tsx test/components/session-screen.test.tsx 2>&1 | grep -E "FAIL|Test Files|Tests  ")
```

Expected — three `FAIL` lines, then the counts:

```
 FAIL  test/components/stop-control.test.tsx [ test/components/stop-control.test.tsx ]
 FAIL  test/components/session-screen.test.tsx > SessionScreen — Stop (notes-progress-report spec §8.5, D17) > is offered after the running chip and before the replay chip, and beside the check's waiting chip
 FAIL  test/components/session-screen.test.tsx > SessionScreen — Stop (notes-progress-report spec §8.5, D17) > a stop the API takes moves the page to the stopped stage at once, and Stop is gone
 Test Files  2 failed (2)
      Tests  2 failed | 11 passed (13)
```

`Failed to resolve import "../../components/StopControl"`, so its six tests are not counted; the two `SessionScreen` tests find no `#stopBtn` (`expected null to be truthy`). "is not offered once the stream shows Publishing, once /status does, or for a service-stopped session" passes already: nothing offers Stop yet.

- [ ] **Step 3: The target, the control and where it is offered**

`web/components/ConsoleProvider.tsx` — replace

```tsx
type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
```

with

```tsx
type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
/* notes-progress-report spec §8.5: the session the topbar's Stop acts on, and how the screen takes the
   stopped session the API answers with. */
export interface StopTarget { sessionId: string; onStopped(response: ResearchSessionResponse): void }
```

`web/components/ConsoleProvider.tsx` — replace

```tsx
  unreachable: Unreachable | null; noteUnreachable(key: string, target: string, retry: () => void): void; clearUnreachable(key: string): void;
}
```

with

```tsx
  unreachable: Unreachable | null; noteUnreachable(key: string, target: string, retry: () => void): void; clearUnreachable(key: string): void;
  /* Set by SessionScreen while its session can be stopped; null otherwise (spec §8.5). */
  stop: StopTarget | null; setStop(target: StopTarget | null): void;
}
```

`web/components/ConsoleProvider.tsx` — replace

```tsx
  const [chip, setChip] = useState<SessionView | null>(null);
```

with

```tsx
  const [chip, setChip] = useState<SessionView | null>(null);
  const [stop, setStop] = useState<StopTarget | null>(null);
```

`web/components/ConsoleProvider.tsx` — replace

```tsx
    () => ({ mode, noteMode, chip, setChip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable }),
    [mode, noteMode, chip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable],
```

with

```tsx
    () => ({ mode, noteMode, chip, setChip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable, stop, setStop }),
    [mode, noteMode, chip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable, stop],
```

`web/components/Topbar.tsx` — replace

```tsx
import { StatusChip } from "./StatusChip";
export function Topbar() {
  const { sidebar, setSidebar, chip, mode } = useConsole();
```

with

```tsx
import { StatusChip } from "./StatusChip";
import { StopControl } from "./StopControl";
export function Topbar() {
  const { sidebar, setSidebar, chip, mode, stop } = useConsole();
```

`web/components/Topbar.tsx` — replace

```tsx
          {chip ? <StatusChip view={chip} /> : null}
          {mode === "replay" ? <ModeChip /> : null}
```

with

```tsx
          {chip ? <StatusChip view={chip} /> : null}
          {/* notes-progress-report spec §8.5: Stop sits after the status chip and before the replay chip. */}
          {stop ? <StopControl key={stop.sessionId} target={stop} /> : null}
          {mode === "replay" ? <ModeChip /> : null}
```

Create `web/components/StopControl.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError, stopResearch } from "@/lib/api";
import { STOP_BODY, STOP_CLOSE, STOP_CONFIRM, STOP_FAILED, STOP_KEEP, STOP_LABEL, STOP_TITLE, STOP_TOO_LATE } from "@/lib/stop";
import { useConsole, type StopTarget } from "./ConsoleProvider";

/* notes-progress-report spec §8.5 (D17, D18, D24; Stop.dc.html columns 1-2): Stop, after the running
   status chip — ghost, small, a square in the text colour — and its one confirmation. "Keep going" takes
   focus on open; Escape, a click outside or "Keep going" closes it and gives focus back to Stop. "Stop
   research" posts once: a 202 hands the stopped session to the screen, a 409 means the run is already
   finishing, and any other failure keeps the question open. Nothing stops until the reader says so. */
type Face = "ask" | "busy" | "late" | "failed";

export function StopControl({ target }: { target: StopTarget }) {
  const { noteMode, refreshSessions } = useConsole();
  const [open, setOpen] = useState(false);
  const [face, setFace] = useState<Face>("ask");
  const anchor = useRef<HTMLSpanElement>(null);
  const stopBtn = useRef<HTMLButtonElement>(null);
  const keepBtn = useRef<HTMLButtonElement>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const busy = face === "busy";

  function close() { setOpen(false); setFace("ask"); stopBtn.current?.focus(); }
  // Focus follows the face: Keep going on open, Close after a 409, Stop research after a failure.
  useEffect(() => {
    if (!open) return;
    if (face === "ask") keepBtn.current?.focus();
    else if (face === "late") closeBtn.current?.focus();
    else if (face === "failed") confirmBtn.current?.focus();
  }, [open, face]);
  // While the POST is in flight neither Escape nor a click outside closes it, so the answer always
  // lands on an open popover (spec ambiguity 10).
  useEffect(() => {
    if (!open || busy) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    // A press outside closes it. Its default action would move focus after this handler — to what was
    // pressed, or to the page when that cannot take focus — so it is cancelled, and focus stays on Stop.
    const onMouseDown = (event: MouseEvent) => {
      if (anchor.current && !anchor.current.contains(event.target as Node)) { event.preventDefault(); close(); }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onMouseDown);
    return () => { document.removeEventListener("keydown", onKeyDown); document.removeEventListener("mousedown", onMouseDown); };
  }, [open, busy]);

  async function confirm() {
    setFace("busy");
    try {
      const result = await stopResearch(target.sessionId); // one POST; never retried
      noteMode(result.mode);
      setOpen(false); setFace("ask");
      target.onStopped(result.data);
      void refreshSessions();
    } catch (error) {
      setFace(error instanceof ApiError && error.status === 409 ? "late" : "failed");
    }
  }

  return (
    <span className="stop-anchor" ref={anchor}>
      <button className="btn btn-ghost btn-sm btn-stop" id="stopBtn" type="button" ref={stopBtn}
        aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? "stopConfirm" : undefined}
        onClick={() => { if (!open) setOpen(true); else if (!busy) close(); }}>
        <span className="stop-sq" aria-hidden="true" />{STOP_LABEL}
      </button>
      {open ? (
        <div className="stop-confirm" id="stopConfirm" role="dialog" aria-labelledby="stopConfirmT">
          <p className="confirm-t" id="stopConfirmT">{STOP_TITLE}</p>
          {face === "late" ? (
            <>
              <p className="b-sub" id="stopTooLate">{STOP_TOO_LATE}</p>
              <div className="confirm-btns">
                <button className="btn btn-quiet btn-sm" type="button" ref={closeBtn} onClick={close}>{STOP_CLOSE}</button>
              </div>
            </>
          ) : (
            <>
              <p className="b-sub">{STOP_BODY}</p>
              {face === "failed" ? <p className="cap" id="stopFailed" role="alert">{STOP_FAILED}</p> : null}
              <div className="confirm-btns">
                <button className="btn btn-quiet btn-sm" type="button" ref={keepBtn} disabled={busy} onClick={close}>{STOP_KEEP}</button>
                <button className="btn btn-sm btn-danger" type="button" ref={confirmBtn} disabled={busy} onClick={() => void confirm()}>{STOP_CONFIRM}</button>
              </div>
            </>
          )}
        </div>
      ) : null}
    </span>
  );
}
```

`web/components/SessionScreen.tsx` — replace

```tsx
  const { noteMode, noteUnreachable, clearUnreachable, refreshSessions, setChip } = useConsole();
```

with

```tsx
  const { noteMode, noteUnreachable, clearUnreachable, refreshSessions, setChip, setStop } = useConsole();
```

`web/components/SessionScreen.tsx` — replace

```tsx
  useEffect(() => { setChip(stopped || notFound ? null : view); return () => setChip(null); }, [setChip, status, version, streaming, stopped, notFound]); // eslint-disable-line react-hooks/exhaustive-deps
```

with

```tsx
  useEffect(() => { setChip(stopped || notFound ? null : view); return () => setChip(null); }, [setChip, status, version, streaming, stopped, notFound]); // eslint-disable-line react-hooks/exhaustive-deps
  // notes-progress-report spec §8.5 (D17): Stop is offered from the one-time check through Reviewing —
  // while the session is live and not closed out, and neither the stream nor the last /status shows
  // Publishing or a finished graph: the API refuses a stop from the run's decision to publish.
  const stoppable = live && status.finished_at === null && run.current.finalStatus === null
    && chipStep(run.current) !== "finalize_report" && status.current_agent !== "finalize_report";
  useEffect(() => {
    setStop(stoppable ? { sessionId, onStopped: setStatus } : null);
    return () => setStop(null);
  }, [setStop, stoppable, sessionId]);
```

Append to `web/app/globals.css`:

```css
/* The Stop control (§8.5; D18, D24): a small ghost button after the status chip — a square in the text
   colour, then "Stop", kept at every width — and its one confirmation, hung below it, right-aligned, on
   one surface. "Stop research" is the one red word: --status-danger text on a --border edge, the danger
   edge on hover, never a filled button; nothing here is purple (D19). */
.stop-anchor{position:relative;display:inline-flex;flex:none}
.btn-stop{color:var(--fg)}
.stop-sq{width:8px;height:8px;background:currentColor;border-radius:1px;display:inline-block;flex:none}
.btn-danger{border-color:var(--border);color:var(--status-danger)}
.btn-danger:hover{background:var(--border-soft);border-color:var(--status-danger)}
.stop-confirm{position:absolute;top:calc(100% + var(--space-2));right:0;z-index:40;width:320px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:var(--space-4);display:flex;flex-direction:column;gap:var(--space-3);animation:enter var(--motion-base) var(--ease-standard) 1}
.stop-confirm .confirm-t{font-size:var(--text-base);font-weight:600;color:var(--fg);line-height:1.4}
.stop-confirm .b-sub{font-size:var(--text-sm);color:var(--muted);line-height:1.5}
.stop-confirm .confirm-btns{display:flex;gap:var(--space-2);justify-content:flex-end}
/* On a phone the replay chip sits to Stop's right, so a 320px box hung from its right edge would start
   left of the viewport: there it spans the width between the phone gutters, under the topbar
   (spec ambiguity 9). */
@media (max-width:480px){
  .stop-confirm{position:fixed;top:calc(var(--topbar) + var(--space-2));left:var(--container-gutter-phone);right:var(--container-gutter-phone);width:auto}
}
@media (prefers-reduced-motion:reduce){
  /* The popover fades in without rising: the reduced-motion block above redefines `enter` as a fade
     and zeroes every duration; this gives the popover its fade back, as the stages get theirs. */
  .stop-confirm{animation-duration:var(--motion-base) !important}
}
```

- [ ] **Step 4: Run them to make sure they pass, type-check, and check the stylesheet**

```bash
(cd web && npx vitest run test/components/stop-control.test.tsx test/components/session-screen.test.tsx 2>&1 | grep -E "Test Files|Tests  " && npm run -s typecheck && npm run -s check:css && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  2 passed (2)`, `Tests  19 passed (19)`; no `typecheck` output; `OK`; then `Test Files  32 passed (32)`, `Tests  251 passed (251)` (Task 7's 242 + 9).

- [ ] **Step 5: Record the control in the design**

`docs/design/DESIGN.md` — replace

```
re-armed. There is no arc, no hand-off, no note line and no counters block. A stop during the
one-time check shows no pipeline card (D33), and the note reads `You stopped this research at
{HH:MM}, before it started.`
```

with

```
re-armed. There is no arc, no hand-off, no note line and no counters block. A stop during the
one-time check shows no pipeline card (D33), and the note reads `You stopped this research at
{HH:MM}, before it started.`

**Stop asks once** (notes-progress-report D17, D18, D24, 2026-09-30). From the one-time check
through Reviewing, a small ghost **Stop** — an 8px square in the text colour, then the word,
kept at every width — sits after the status chip in the topbar, before the replay chip. It asks
once: a popover hung below it, right-aligned, 320px wide on one surface, `Stop this research?`
and `It stops right away and nothing more is spent. What's done so far stays here, but no report
is written.`, with `Keep going` (quiet, focused on open) and `Stop research`, the one red word:
`--status-danger` text on a `--border` edge, never a filled button. Escape, a click outside or
Keep going closes it and gives focus back to Stop; nothing stops until the reader says so.
Stopping cancels the run where it stands, with every call it has in flight, writes nothing and
opens stage 6. Once the run decides to publish the control is gone, because the API refuses a
stop from that decision (`409 not_stoppable`); a refusal that still reaches an open popover says
`Too late to stop — the research is finishing.` with a Close button, and any other failure says
`Couldn't stop — try again` and keeps both buttons. On a phone the popover spans the width
between the gutters, under the topbar. Under reduced motion it fades in without rising.
```

- [ ] **Step 6: Commit**

```bash
git add web/components/ConsoleProvider.tsx web/components/Topbar.tsx web/components/StopControl.tsx web/components/SessionScreen.tsx web/app/globals.css web/test/components/stop-control.test.tsx web/test/components/session-screen.test.tsx docs/design/DESIGN.md
git commit -m "feat(web): Stop beside the running chip, with one confirmation (notes-progress-report §8.5, D24)"
```

---

### Task 9: Stop end to end on the replay server, the captures, and running the app (spec §11.1 Playwright, §11.3, §8.4's e2e rows, §8.6's README rows; AC28, AC30–AC33)

**Files** (line numbers locate the anchors at the end of Task 8):
- Create: `web/e2e/stop.spec.ts`
- Modify: `web/e2e/support.ts:47-50` (`waitTerminal`); `web/scripts/capture-replay-events.mjs:11-12`; `web/e2e/visual.spec.ts:87-88` (two captures after `12-report-notes`); `README.md:944-945` ("Run the app"); `web/README.md:23-24`, `:41-42`

**Interfaces:**
- Consumes: everything of Tasks 2–8, through the replay server (`python -m deep_research.api --mode replay --port 8010`, 150 ms between released events).
- Produces: `waitTerminal` and the capture script treat `stopped` as terminal; `e2e/stop.spec.ts` (7 tests); the `19-stop-confirm(-phone)` and `20-stopped(-phone)` captures.

The replay case `missing-target-triggers-one-extra-pass` releases 72 events, 150 ms apart (about 11 s). Researching is the page's active row from event 6 to event 28 (about 3.3 s) and again from 42 to 53, and Publishing from 67 (`web/test/fixtures/active-rows.json`). A spec that waits for Researching to become active and then presses Stop and Stop research lands inside that window with about 3 s to spare; every timing below rests on that.

- [ ] **Step 1: Treat `stopped` as terminal in the e2e helper and the capture script**

`web/e2e/support.ts` — replace

```ts
/* Terminal: neither running nor waiting for the reader's answers (needs_input, live-briefs spec §4.4). */
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).toMatch(/^(completed|max_iterations|incomplete|failed)$/);
}
```

with

```ts
/* Terminal: neither running nor waiting for the reader's answers (needs_input, live-briefs spec §4.4);
   a session the reader stopped is terminal too (notes-progress-report spec §8.4). */
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).toMatch(/^(completed|max_iterations|incomplete|failed|stopped)$/);
}
```

`web/scripts/capture-replay-events.mjs` — replace

```js
// A session waiting for the reader (needs_input, live-briefs spec §4.4) has not finished either.
const TERMINAL = /^(completed|max_iterations|incomplete|failed)$/;
```

with

```js
// A session waiting for the reader (needs_input, live-briefs spec §4.4) has not finished either; one the
// reader stopped has (notes-progress-report spec §8.4).
const TERMINAL = /^(completed|max_iterations|incomplete|failed|stopped)$/;
```

- [ ] **Step 2: Write the end-to-end spec**

Create `web/e2e/stop.spec.ts`:

```ts
// notes-progress-report spec §8 (D17, D18, D24, D29, D33; AC28, AC30, AC31, AC33): Stop on the replay
// server. Replay paces the stream (150 ms an event) while the engine runs ahead unpaced, so the page's
// active row is the stream's — and the step a stop records is read from that same stream (AC32).
import { expect, test, type Page } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

const QUESTION = "What is the current state of grid-scale battery storage?";
const chip = (page: Page) => page.locator("#topbarStatus .chip");
const stop = (page: Page) => page.locator("#stopBtn");
const popover = (page: Page) => page.getByRole("dialog", { name: "Stop this research?" });
const row = (page: Page, stage: string) => page.locator(`#stage-user-stopped #spine > li[data-stage="${stage}"]`);
/* Researching has just become the active row: about 3 s of its first pass remain (see Task 9's note). */
const researching = (page: Page) => page.locator('#spine li[data-stage="researcher"][data-state="active"]').waitFor({ timeout: 15_000 });
async function stopNow(page: Page) {
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stage-user-stopped")).toBeVisible();
}

test.beforeEach(async ({ context }) => {
  // The longest case: its paced stream keeps each step on screen long enough to stop it there.
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
});

test("Stop is offered through the run, gone once Publishing starts, and a stop after the end is refused (AC30, AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await expect(stop(page)).toHaveText("Stop");
  await page.locator('#spine li[data-stage="finalize_report"][data-state="active"]').waitFor({ timeout: 30_000 });
  await expect(stop(page)).toHaveCount(0);
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await expect(stop(page)).toHaveCount(0);
  const refused = await request.post(`${API}/research/${id}/stop`);
  expect(refused.status()).toBe(409);
  expect((await refused.json()).error).toEqual({ code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "finished", issues: [] });
});

test("confirm and stop mid-run: the stopped stage, its chip, the frozen pipeline, the sidebar and the API's answers (AC28, AC31-AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stop(page).click();
  await expect(popover(page).locator(".b-sub")).toHaveText("It stops right away and nothing more is spent. What's done so far stays here, but no report is written.");
  await expect(popover(page).getByRole("button", { name: "Keep going" })).toBeFocused();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stage-user-stopped")).toBeVisible();
  const status = await (await request.get(`${API}/research/${id}/status`)).json();
  expect([status.status, status.stopped_step]).toEqual(["stopped", "researcher"]);
  expect(status.finished_at).not.toBeNull();
  await expect(chip(page)).toHaveText("Stopped by you · at Researching");
  await expect(chip(page).locator(".dot")).toHaveClass("dot dot-neutral");
  await expect(page.locator(".stopped-note .b-now")).toHaveText(/^You stopped this research at \d\d:\d\d, (less than a minute|1 minute|\d+ minutes) in\.$/);
  await expect(page.locator(".stopped-note .b-sub")).toHaveText("No report was written. The plan and what research found so far are kept below until the service restarts.");
  await expect(row(page, "planner")).toHaveAttribute("data-state", "done");
  await expect(row(page, "researcher")).toHaveAttribute("data-state", "stopped");
  await expect(row(page, "researcher").locator(".m-live")).toHaveText(/^Stopped( · .+)?$/);
  for (const stage of ["source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) {
    await expect(row(page, stage)).toHaveAttribute("data-state", "off");
    await expect(row(page, stage).locator(".m-live")).toHaveText("not run");
  }
  await row(page, "planner").locator("button.ps-toggle").click();
  await expect(row(page, "planner")).toHaveAttribute("data-open", "1");
  await expect(page.locator("#noteLine")).toHaveCount(0);
  await expect(stop(page)).toHaveCount(0);
  const stream = await (await request.get(`${API}/research/${id}/stream`)).text();
  expect(stream.trimEnd().split(/\r?\n\r?\n/).at(-1)).toContain("event: session.stopped");
  for (const [path, code] of [["report", "report_unavailable"], ["evidence", "evidence_unavailable"], ["evidence?format=markdown", "evidence_unavailable"]] as const) {
    const answer = await request.get(`${API}/research/${id}/${path}`);
    expect([answer.status(), (await answer.json()).error.code]).toEqual([409, code]);
  }
  const note = await request.post(`${API}/research/${id}/notes`, { data: { text: "too late" } });
  expect([note.status(), (await note.json()).error.code]).toEqual([409, "notes_closed"]);
  // D29: the sidebar names the session as stopped in its accessible name only.
  const item = page.locator(`#sessionList [data-session="${id}"]`);
  await expect(item).toHaveAttribute("aria-label", /— stopped by you$/);
  await expect(item).toHaveAttribute("data-run", "0");
});

test("Keep going, Escape and a click outside close the confirmation and give focus back to Stop (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Keep going" }).click();
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  await stop(page).click();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  await stop(page).click();
  await page.locator("#running-h").click();
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("running");
  await waitTerminal(request, id);
});

test("a refusal that reaches the open confirmation says it is too late (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  // The page hides Stop as its stream shows Publishing, and the API refuses from that same event, so a
  // real 409 reaches an open popover only inside a few milliseconds (spec ambiguity 17): this is the
  // API's own refusal body, answered for the page's POST.
  await page.route("**/api/research/*/stop", (route) => route.fulfill({
    status: 409, contentType: "application/json",
    body: JSON.stringify({ error: { code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "publishing", issues: [] } }),
  }));
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stopTooLate")).toHaveText("Too late to stop — the research is finishing.");
  await expect(popover(page).getByRole("button", { name: "Close" })).toBeFocused();
  await popover(page).getByRole("button", { name: "Close" }).click();
  await expect(popover(page)).toHaveCount(0);
  await waitTerminal(request, id);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("completed");
});

test("Ask again starts a new session with the same question (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stopNow(page);
  const question = await page.locator("#user-stopped-h").textContent();
  await page.locator("#askAgain").click();
  await page.waitForURL((url) => /\/research\/[0-9a-f]+$/.test(url.pathname) && !url.pathname.endsWith(id));
  const fresh = page.url().split("/").pop()!;
  expect((await (await request.get(`${API}/research/${fresh}/status`)).json()).query).toBe(question);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("stopped");
  await waitTerminal(request, fresh);
});

test("a stop while the one-time check asks shows no pipeline card (D33)", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Clarify": "on" });
  const id = await submit(page, QUESTION);
  await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
  await stopNow(page);
  await expect(page.locator(".stopped-note .b-now")).toHaveText(/^You stopped this research at \d\d:\d\d, before it started\.$/);
  await expect(chip(page)).toHaveText("Stopped by you · at the questions");
  await expect(page.locator("#stage-user-stopped .card")).toHaveCount(0);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).stopped_step).toBe("check");
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("Stop keeps its label, the confirmation fits the screen, and the stopped stage has no sideways scroll (AC33)", async ({ page }) => {
    await submit(page, QUESTION);
    await researching(page);
    await expect(stop(page)).toHaveText("Stop");
    await stop(page).click();
    const box = (await popover(page).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    await popover(page).getByRole("button", { name: "Stop research" }).click();
    await expect(page.locator("#stage-user-stopped")).toBeVisible();
    const [scroll, inner] = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
    expect(scroll).toBeLessThanOrEqual(inner);
  });
});
```

- [ ] **Step 3: Add the two captures**

`web/e2e/visual.spec.ts` — replace

```ts
      await shoot(page, `12-report-notes${suffix}`);
    });
```

with

```ts
      await shoot(page, `12-report-notes${suffix}`);
    });

    // notes-progress-report spec §11.3: the confirmation over the running stage, then the stopped stage.
    // The popover opens as Researching starts; the stop is confirmed the moment a topic is done, so the
    // stopped row carries Researching's facts (about 1.2 s of Researching remain then).
    test(`19-stop-confirm${suffix}, 20-stopped${suffix}`, async ({ page, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator('#spine li[data-stage="researcher"][data-state="active"]').waitFor({ timeout: 15_000 });
      await page.locator("#stopBtn").click();
      await expect(page.getByRole("dialog", { name: "Stop this research?" })).toBeVisible();
      await shoot(page, `19-stop-confirm${suffix}`);
      await page.locator('#spine li[data-stage="researcher"] .ps-topics [data-topic="done"]').first().waitFor({ state: "attached", timeout: 10_000 });
      await page.getByRole("dialog", { name: "Stop this research?" }).getByRole("button", { name: "Stop research" }).click();
      await expect(page.locator("#stage-user-stopped")).toBeVisible();
      await shoot(page, `20-stopped${suffix}`);
    });
```

- [ ] **Step 4: Type-check, build, and run the spec**

```bash
(cd web && npm run -s typecheck)
(cd web && npx playwright test --list --project=chromium | tail -1 && npx playwright test --list --project=visual | tail -1)
netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'
(cd web && npm run -s build && npx playwright test e2e/stop.spec.ts --project=chromium 2>&1 | tail -3)
```

Expected: no `typecheck` output; `Total: 68 tests in 20 files`, then `Total: 14 tests in 1 file` (Task 1 Step 3's 61 and 12, + 7 and + 2; listing starts no server); no `netstat` line; then **[not run in planning]** `7 passed`.

Any failure: read its trace (`web/test-results/`) before changing anything. A stop that lands after Researching ended (the mid-run test then reads another `stopped_step`) means the machine is slower than the ~3 s margin above; report it rather than loosening the assertion.

- [ ] **Step 5: Run the whole Chromium suite and the captures**

```bash
(cd web && npx playwright test --project=chromium 2>&1 | tail -3)
(cd web && VISUAL_CHECKPOINT=D-T9 npx playwright test --project=visual 2>&1 | tail -3 && ls visual/D-T9 | wc -l)
```

Expected: **[not run in planning]** `68 passed` (Task 1's 61 + 7); then `14 passed` and `24` images (Task 1's 12 tests, + 2; the 20 images `web/e2e/visual.spec.ts:18-88` takes today, 10 per viewport, + 4).

- [ ] **Step 6: Review the captures at full height**

Open each new capture in `web/visual/D-T9/` beside `Stop.dc.html` (`.superpowers/progress-canvas/project/`, columns 2 and 3), and check, at 1252 and at 390 px:
- `19-stop-confirm`: Stop sits after the running chip and before `replay mode`, a small square then `Stop`; the popover hangs below it (at 390 px it spans the width between the gutters, under the topbar), one surface, the title, the D24 body copy, `Keep going` quiet and `Stop research` in red text on a grey edge; nothing purple.
- `20-stopped`: the eyebrow `Stopped by you`; the chip `Stopped by you · at Researching` on a grey dot; the note card with the time line, the second line and a ghost `Ask again`; Planning done, Researching with a quiet grey-edged node and `Stopped · {k} of {n} topics done · …`, the five later rows `not run` in the dimmer grey; no note line, no arc, no halo anywhere.
- `10-clarify-phone` (re-taken by the same run): at 390 px the topbar now holds the waiting chip, Stop and `replay mode` on one row; the chip's label must not overlap Stop.

Record what you checked in the task summary. Anything that does not match: stop and report it with the capture's name.

- [ ] **Step 7: Say how to stop a run, in both READMEs**

`README.md` — replace

```
running stage plays arrives after the engine has finished and ends `not_checked`. In replay mode the topbar
shows a muted `replay mode` chip.
```

with

```
running stage plays arrives after the engine has finished and ends `not_checked`. In replay mode the topbar
shows a muted `replay mode` chip.
While a session runs — from the one-time check through Reviewing — the topbar's **Stop**, beside
the status chip, asks once and then sends `POST /research/{id}/stop`: the run is cancelled where it
stands, nothing is published, and the page keeps the pipeline frozen where it stopped, with
**Ask again**. Replay mode stops a session the same way.
```

`web/README.md` — replace

```
- `npm run capture:visual` — the twenty full-page captures (10 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

with

```
- `npm run capture:visual` — the twenty-four full-page captures (12 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

`web/README.md` — replace

```
  applied, and the report's "Your notes" reads it `not checked`; `e2e/notes.spec.ts` and the
  `11-note-ack` and `12-report-notes` captures use it.
```

with

```
  applied, and the report's "Your notes" reads it `not checked`; `e2e/notes.spec.ts` and the
  `11-note-ack` and `12-report-notes` captures use it.
- Stop (notes-progress-report spec §8): the topbar's Stop, beside the running chip, asks once and
  posts `POST /research/{id}/stop` (the proxy forwards it); the session ends `stopped` and its page
  keeps the pipeline frozen where it stopped, with "Ask again". On the replay server the engine runs
  ahead of its paced stream, so by the time a step can be stopped it has usually finished — its
  files land in the replay's temporary directory, which the server deletes on exit — but the session
  itself still ends `stopped`, with no report on the API. `e2e/stop.spec.ts` and the
  `19-stop-confirm` and `20-stopped` captures use it.
```

- [ ] **Step 8: Commit**

```bash
git add web/e2e/support.ts web/scripts/capture-replay-events.mjs web/e2e/stop.spec.ts web/e2e/visual.spec.ts README.md web/README.md
git commit -m "test(web): Stop end to end on the replay server, and the stop captures (notes-progress-report §11.1, §11.3)"
```

---

### Task 10: Full verification (AC28–AC33)

**Files:** none changed.

**Interfaces:**
- Consumes: Tasks 1–9.
- Produces: the evidence that Phase D is complete — every suite green, the anchors of this plan all consumed, the acceptance criteria each pinned by a named test.

- [ ] **Step 1: The whole backend suite**

```bash
.venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config 2>&1 | tail -1
```

Expected: `4979 passed, 2 deselected, 2 warnings in 106.14s (0:01:46)` (Task 1 Step 3's 4960 + 19; the time varies).

- [ ] **Step 2: The whole web unit suite, the types and the stylesheet**

```bash
(cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css)
```

Expected: no `typecheck` output; `Test Files  32 passed (32)`, `Tests  251 passed (251)` (Task 1 Step 3's 28 files and 219 tests + 4 files and 32 tests); `OK`.

- [ ] **Step 3: What this phase promised, checked by name**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py tests/test_api/test_stop_inflight.py tests/test_tools/test_web_search.py tests/test_evaluation/test_dependencies_controlled.py tests/test_api/test_notes.py tests/test_api/test_note_route.py -q 2>&1 | tail -1
(cd web && npx vitest run test/stop.test.ts test/active-row.test.ts test/components/stop-control.test.tsx test/components/user-stopped-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
grep -rn --include=*.py "TavilyClient(" src | grep -v AsyncTavilyClient
```

Expected: `119 passed, 2 warnings`; then `Test Files  4 passed (4)`, `Tests  21 passed (21)`; then nothing from the `grep`: no synchronous `TavilyClient(` is built anywhere under `src`.

- [ ] **Step 4: The Chromium suite and the captures once more, on the finished tree**

```bash
netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'
(cd web && npm run -s build && npx playwright test --project=chromium 2>&1 | tail -3)
(cd web && VISUAL_CHECKPOINT=D-final npx playwright test --project=visual 2>&1 | tail -3)
```

Expected: no `netstat` line; **[not run in planning]** `68 passed`; `14 passed`. Review `19-stop-confirm`, `20-stopped` and `10-clarify-phone` in `web/visual/D-final/` as in Task 9 Step 6.

- [ ] **Step 5: No commit**

Nothing changed. The task summary lists the four results and the capture review.

---

## Acceptance-criteria coverage

| AC | What it asks | Where it is met | Pinned by |
|---|---|---|---|
| AC28 | `POST /stop` on a `running` and a `needs_input` session → 202 `stopped`; `session.stopped` last; the stream closes; `/status` shows `stopped_step` and `finished_at` | Tasks 3, 4 | `test_a_running_session_stops_at_once_and_says_where`, `test_stop_during_needs_input`, `test_stop_route_codes`, `test_a_stop_while_the_check_waits_answers_202_and_refuses_the_answers`; e2e "confirm and stop mid-run" |
| AC29 | an in-flight stub call cancelled within 1 s; no later node; no file; no memory write; an async search in flight observes `CancelledError`; a tool built with no client holds `AsyncTavilyClient` | Task 5 | `test_stop_cancels_inflight_calls`, `test_search_cancelled_with_task`, `test_default_search_client_is_async` (and `test_sync_search_client_runs_in_thread`, `test_search_5xx_retried`) |
| AC30 | 409 `not_stoppable` for completed, failed, a second stop, after the decision to finalize, during store close; 404 unknown | Tasks 3, 4 | `test_a_stop_is_refused_once_the_session_has_ended_is_publishing_or_the_service_is_closing`, `test_stop_route_codes`, `test_the_stop_route_says_why_it_refuses`; e2e "Stop is offered through the run…" |
| AC31 | `/report` 409 `report_unavailable`; `/evidence` (JSON and Markdown) 409 `evidence_unavailable`; notes 409 `notes_closed`; answers 409 `not_waiting_for_input` | Tasks 3, 4 | `test_stop_route_codes`, `test_stop_during_needs_input`, `test_a_stop_while_the_check_waits_answers_202_and_refuses_the_answers`; e2e "confirm and stop mid-run" |
| AC32 | replay: a stop mid-stream records the web's active row at that moment | Tasks 3, 6 | `test_active_row_matches_web_rule` + `web/test/active-row.test.ts` (one shared file), `test_active_row_follows_the_consoles_rule`, `test_replay_stop_mid_stream`; e2e "confirm and stop mid-run" |
| AC33 | Stop from the check through Reviewing, not while Publishing; the popover (focus, Escape, 409, failure; D24 copy); the stopped stage (chip, card, openable rows, stopped facts, "not run"); the sidebar label; Ask again | Tasks 6–9 | `stop-control.test.tsx`, `session-screen.test.tsx` (Stop), `user-stopped-stage.test.tsx`, `briefs.test.ts`, `sidebar.test.tsx`, `status-chip.test.tsx`, `format.test.ts`; `e2e/stop.spec.ts` (7); captures 19 and 20 |
| §4 item 2 (AC9's D half) | no terminal session reports `pending` | Task 2 (+ `stopped` in Task 3) | `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending`, `test_the_records_list_every_accepted_note_in_order_read_or_not`, `test_a_note_nothing_judged_reads_pending_while_the_session_runs_and_not_checked_once_it_ends`, `test_a_session_closed_out_by_a_shutdown_reads_its_notes_not_checked`, `test_a_stopped_sessions_notes_read_not_checked_and_a_reading_in_flight_is_dropped`, `test_stop_route_codes`; e2e `notes.spec.ts` "the report lists every note above the prose, each with its outcome (AC19)" |

Spec §11.1's Phase D tests and where each is: `test_stop_route_codes` (Task 4); `test_stop_cancels_inflight_calls` (Task 5); `test_default_search_client_is_async`, `test_search_cancelled_with_task`, `test_sync_search_client_runs_in_thread`, `test_search_5xx_retried` (Task 5); `test_stop_during_needs_input`, `test_publish_after_stop_dropped`, `test_active_row_matches_web_rule`, `test_replay_stop_mid_stream` (Task 3). Vitest: `format.test.ts`, `status-chip.test.tsx`, `sidebar.test.tsx`, `run-state.test.ts` (Task 6), `session-screen.test.tsx` (Task 8), `stop-control.test.tsx` (Task 8), `user-stopped-stage.test.tsx` (Task 7). Playwright: `stop.spec.ts` (Task 9). §11.2's Phase D rows: `web/e2e/support.ts`, `web/scripts/capture-replay-events.mjs` (Task 9), `web/test/components/session-screen.test.tsx` (Task 8; the service-stopped tests unchanged), `tests/test_evaluation/test_dependencies_controlled.py` (Task 5); `tests/test_tools/test_web_search.py` keeps its synchronous fakes, which now exercise the thread path. Beyond §11.2, §4 item 2 also changes `web/e2e/notes.spec.ts:109-110` (Task 2, ambiguity 19). §11.3: `19-stop-confirm(-phone)`, `20-stopped(-phone)` (Task 9).

## Spec Phase D items, and where each lands

| Spec | Item | Task |
|---|---|---|
| §8.1 | the route, its three answers, `_SAFE_MESSAGES["not_stoppable"]`, a second stop `finished` | 4 |
| §8.2 | `SessionStore.stop` steps 1–5; `publish` drops after a stop; `_run`'s `finally` keeps `finished_at` | 3 |
| §8.3 | what stops; `test_stop_cancels_inflight_calls`; the async search, items 1–6 and its tests | 5 (shared fetch: Open issue O2) |
| §8.4 | `SessionStatus`, `stopped_step`, `TraceMetadata`; `TERMINAL_STATUSES`; `/report` and `/evidence`; `web/lib/api.ts`; `web/lib/format.ts`; `ReportStage`/`ReportRail` unchanged; `Sidebar`; `RunState.stopped`; `SessionScreen`; `waitTerminal`, the capture script; what the stored session keeps | 3, 4, 6, 7, 9 |
| §8.5 | `StopControl`, `UserStoppedStage`, `stopResearch`; `ConsoleProvider.stop`; the offer condition; `Topbar`; the button, the popover (D24), its faces; the stopped stage, `BriefSpine.frozen`, the rows; the CSS | 6, 7, 8 |
| §8.6 | DESIGN §4, §3 inventory, §3.1; api-gaps surface and the closed cancel gap; README and `web/README.md` | 4, 7, 8, 9 (`X-Replay-Hold-After`: Open issue O1) |
| §4 item 2 | `not_checked`, `terminal`, `OUTCOME_TEXT` | 2 |
| §4 item 3 | the step ids and labels | 3, 6 |
| §11.3 | the two captures, reviewed against the canvas | 9, 10 |
| §12 R9 | the 5xx retry | 5 (`test_search_5xx_retried`); the live check is Open issue O5 |

## Self-review

- **Spec coverage.** Every §8 subsection, §4 items 2 and 3, AC28–AC33, the Phase D rows of §11.1–§11.3 and R9 map to a task in the two tables above. Two §8 statements cannot be met by D as written in the order §9 sets, and are Open issues rather than silent changes: the README's `X-Replay-Hold-After` (O1) and the shared fetch in `test_stop_cancels_inflight_calls` (O2). One detail is built differently from §8.4's wording, with the same visible result, and is Open issue O7 for the human: the chip's stopped label rides in `SessionView.step` rather than a new `stoppedStep` field (ambiguity 7). One existing test outside §11.2's Phase D rows breaks with §4 item 2 and is updated in Task 2: `web/e2e/notes.spec.ts:109-110` (ambiguity 19). O6 asks the later phases' plans to anchor on the text D leaves.
- **Placeholders.** None: every code step carries its code, every command its expected output; the Playwright and capture Expected lines are reasoned and marked as not run in planning (O4).
- **Type consistency.** `NotStoppable.reason: StopRefusal` (Task 3) is what the route maps (Task 4); `ResearchSession.stopped_step` (Task 3) feeds `_session_response` (Task 4); `stoppedStepLabel` (Task 6) is what `SessionScreen` passes as the chip's `step` (Task 7); `RunState.stopped: StoppedRun | null` (Task 6) is what `UserStoppedStage` reads (Task 7); `StopTarget` (Task 8) is what `SessionScreen` sets and `StopControl` takes; `stoppedSubtitle`, `notRunText` and `RowState`'s `"stopped" | "off"` (Task 7) are what `BriefSpine` uses; `web/test/fixtures/active-rows.json`'s shape (Task 3) is what both AC32 tests read.

## Review round 1 (2026-09-30): findings and how each was resolved

spec-plan-reviewer reviewed `1cda4bdd` and approved the plan with changes: one Important finding and four Minor ones. The coordinator relayed the ruling: apply I1, M1, M3 and M4; keep M2 as it stands. Each is resolved below. The edited steps were re-run on a fresh export of `73b4d7a6` with this document applied block by block. Results:
- Task 1 Step 2: `anchors: 118 exactly once; creates: 12 absent; appends: 11 onto files present`.
- Tasks 7 and 8, Steps 2 and the pass steps: the counts given there, unchanged.
- Task 10 Steps 2 and 3, the web half: `32 passed` files, `251 passed` tests; then `4 passed` files, `21 passed` tests.
- Eight parallel runs of the five Stop test files: every one passed.

- **I1 (Important) — in a real browser, a click outside left focus on the page, not on Stop.** Fixed in Task 8.
  - The document `mousedown` handler in `StopControl` now calls `event.preventDefault()` before `close()` when the press lands outside the anchor. A press's default action runs after the handler and moves focus to what was pressed, or to the page body when that cannot take focus. That undid `close()`'s focus on Stop, against §8.5's "returns focus to Stop" and Task 9's `toBeFocused()` after a click on `#running-h`.
  - `stop-control.test.tsx` now asserts that `fireEvent.mouseDown(...)` on an element outside returns `false`. jsdom never moves focus on a press, so the cancelled event is what a unit test can see; the browser's behaviour is Task 9's to check.
  - Observed: without the `preventDefault()` call the test fails (`expected true to be false`); with it, `6 passed`.
- **M1 — O6's inventory missed three test files D changes.** Added to O6:
  - `tests/test_api/test_note_route.py`: its import line, and the two tests appended after `:520`.
  - `web/test/notes.test.ts:72-75`.
  - `web/test/components/reader-notes.test.tsx:68`.
- **M2 — `SessionView.step` in place of `stoppedStep`.** Kept as it stands, as ruled (Open issue O7).
- **M3 — Task 1 Step 1 stopped Phase D when the latency work had merged first.** Softened.
  - A `0` on the tool-lock line is now flagged in the task summary rather than stopping the phase, and the diff-stat line accepts that work's change.
  - Task 5 Step 1 then adds the shared fetch in flight to `test_stop_cancels_inflight_calls`, written against that work's helpers to a stated contract: two fetches of one URL share one download that never answers, the handler sees one call, and it is cancelled less than 1 s after the stop. If only that fails, it is reported, not loosened.
  - O2 and Task 1's Interfaces say the same. The step stays conditional because the helpers do not exist yet (`perf/latency` is still the audit only).
- **M4 — a stopped row could show note acknowledgements before `session.stopped`, and the DESIGN cite was off.** Fixed.
  - `UserStoppedStage` passes `{ ...run, active: null }` to `BriefSpine` (Task 7 Step 4). The cause: `rowBrief` acknowledges notes only on `run.active` (`web/lib/briefs.ts:59`), which stays the stopped row until the stream's `session.stopped` clears it, and the 202 or `/status` can arrive first.
  - The existing test, now "reads the time and the minutes from the response until the stream has said them, moves focus to that line, and acknowledges no note in the stopped row", streams a note acknowledged in Researching with no `session.stopped`, opens the stopped row and finds no `.ack`. The file still holds seven tests, so every count stands.
  - Observed: with `run={run}` the test fails (`expected <p class="ln ack" data-ack="n1" …> to be null`); with the change, `7 passed`.
  - The DESIGN §4 cite (Task 7 Step 6) now reads `api/models.py:37-45`. Those are the literal's lines once Task 3 has run, observed on the export; its comment is `:32-36`. The review's "~35-46" was an estimate; the observed span is used.

