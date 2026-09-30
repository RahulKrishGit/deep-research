# Live briefs, Phase 1 — live progress and step briefs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish every research event as it happens (exactly once, with an `event_id`), put the plan's sub-topic titles on the stream, and turn the running stage's pipeline into live step briefs — the active row opens on a live brief (picks 1A, 2C, 3B) — while removing the "Now" header, the running-stage counters block, the pass counter and the page-1 extra-passes control.

**Architecture:** The engine gains one run-scoped live sink: `graph/live.py` holds a ContextVar that `_stream_graph_result` binds for the stream; `agent_node` and the five agents hand events to it the moment they build them, and the snapshot loop skips any `event_id` already published, so each event reaches the handler once (live ones early, the rest in state order). The web app keeps its burst-safe event core: `RunState` gains the topic checklist, outcome lines and reopen lines; a pure `lib/briefs.ts` derives each row's brief; a new `BriefSpine` renders the running stage's rows (head + `0fr→1fr` brief) with the pick-1A open/close, the pick-3B overlapped hand-off and the connector redrawn node-centre to node-centre; the Failed and Stopped stages keep the compact `Spine`. Docs record the new delivery model and the new running stage.

**Tech Stack:** Python 3.12 (`.venv`), LangGraph 1.2.10, pydantic 2, pytest + pytest-asyncio; Node v24.13.1, Next.js 16.3 (Turbopack), React 19, Vitest 5 + Testing Library + jsdom, Playwright (Chromium) against the API in replay mode. Written and dry-run on Windows, executed on Linux (the Conventions give both command forms); every check is pytest, Vitest, Playwright or a `node` one-liner.

**Spec:** `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md` — Phase 1 only: §4.1 (E1–E5), §4.2, §4.3, the Phase-1 rows of §4.9, AC1–AC9 and the Phase-1 parts of §6. Decisions D1–D3, D12–D15 and D17 are closed; nothing here reopens them. Section numbers below (§4.3, E2, AC6, D15 …) are the spec's unless prefixed `DESIGN.md`. Phase 2 (the one-time check) and Phase 3 (reader notes) are **not** planned here.

**Evidence.** Planning dry-ran this plan (2026-09-28) in scratch exports of `7afb21de`, applying every edit by its anchor with scripts that stop unless the anchor occurs exactly once. Tasks 1–6 ran step by step and each of their "Expected" lines was observed, ending at `4784 passed, 1 failed, 1 deselected` in the export — the failure is the `.env`-dependent test described under Conventions, so the main checkout reads `4785 passed, 1 deselected`. Tasks 7–10 ran step by step for `tsc` and Vitest (every count below observed at its step), with the Playwright `chromium` project run at the end of Tasks 7, 9 and 10 and the `visual` project at the end. The doc edits of Tasks 6 and 11 were applied by anchor and Task 11's check printed its expected JSON. Review round 1's changes were dry-run the same way on the web side (Vitest `155`, Chromium `47`, visual `8`) and on the two backend tests it touched. "Observed in planning" below refers to these runs. R1 held, so Contingency C was exercised only by a simulation: with its code applied and the stream-level binding replaced by a no-op, the five handler-level tests of `test_live.py` and the rest of the graph, runtime and API suites passed through the Tracker-keyed re-binding alone.

## Global Constraints

- **Where.** Branch `feat/live-briefs-and-reader-notes`: in the Windows main checkout `C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research`, or wherever a Linux cloud session cloned it. Every path below is relative to the repository root (or to the worktree root if the controller executes in a `.worktrees/*` tree; then run `cd web && npm ci` once before Task 7 and keep `PY` pointing at the main checkout's venv). Tasks run **strictly in order 1 → 12**, one after another, never two at once — later tasks edit what earlier ones wrote (e.g. Task 11 Step 6 rewrites the api-gaps header sentence Task 6 Step 4 wrote). Commit after every task.
- **No live model.** Never run the live CLI (`python -m deep_research "…"`), never call a model provider, never read `.env`. Python runs are pytest or the API in `--mode replay` (scripted, network-denied).
- **No prompt text changes (spec §3.2).** No string that reaches a model changes: `agents/prompts.py` stays byte-identical and no message builder is edited. The replay harness asserts substrings and parses packet lines with anchored regexes (`e2e_evaluation/replay.py:529-571`, `:804-808`, `:1496`); the full replay suite must pass unchanged. The five agent-module fingerprint pins move (module code only) and are re-pinned in the task that moves them, with a comment in the file's existing style.
- **Events (E1–E3).** `ResearchEvent.event_id: str` defaults to a fresh `uuid4().hex` per event. Published live, exactly these: `graph.node.started` from `agent_node`; `planner.planning.started`, `planner.memory.recalled`, `planner.planning.completed`; `researcher.sub_topic.started`, `researcher.tool_call` (built when its step's observation is recorded), `researcher.sub_topic.completed`; `source_evaluator.evaluation.started` / `.completed`; `evidence_verifier.verification.completed`; `report_writer.report.written`. Every other event keeps snapshot publication. The object published live is the object returned in `state_update["events"]`. New metadata: `researcher.sub_topic.started` / `.completed` gain `coverage_id`; `planner.planning.completed` gains `sub_topics: [{coverage_id, title}]`, each title `summarize_text(title, limit=160)` (≤ 160 characters).
- **API.** No API code changes. `ResearchRequest.max_iterations` stays accepted (`api/models.py:46-72`); the web app stops sending it. The SSE `id:` line stays the per-subscriber position; the payload's `event_id` is the event's identity.
- **Web copy, verbatim (spec §4.2, §4.3; `·` is U+00B7 with a space each side, `…` is U+2026).** Chip while running: `Running · {step label}`, or `Running · starting` before any step is known. Subtitles while running: the row's static meta (`STAGES[].meta`), except Researching: `{n} topics · researching` until the first topic is done, then `{done} of {n} topics done · {pages} pages read · {findings} findings`. Bodies: Planning `Breaking your question into sub-topics…`; Evaluating `Rating sources for trustworthiness and relevance`; Verifying `Checking {findings} findings against their pages`; Writing `Writing the report from verified findings only`; Reviewing `Reviewing the draft on 7 dimensions`; Publishing `Saving the report and evidence log`. Outcomes: `{n} sub-topics`; `{n} topics · {pages} pages read · {findings} findings`; `{source_count} sources rated`; `{verified} verified · {corrected} corrected · {dropped} dropped`; `Report drafted · {statements} sentences · {citations} citations`; `Accepted · {score}` / `Not accepted · {score}` / `Sent back to fill {k} gaps`; `Published`. Topic facts: `not yet`, `reading`, `{findings} findings`. Reopen lines: `Going back to research {k} gaps the review found`, `Rewriting to fix {n} issues the review found`. Report pass fact: `One research round` / `Went back once to fill gaps` / `Went back twice to fill gaps` / `Went back {n} times to fill gaps`, plus ` · went back {once|twice|k times} for your notes` when `note_passes` > 0 (always 0 in Phase 1). Every count pluralises (singular at 1); in Researching's texts a measured 0 reads `no pages read` / `no findings` / `no topics`, never a bare `0` (AC5).
- **Motion (spec §4.3 table; D1, D3).** Only `transform`, `opacity`, `grid-template-rows` and the check's `stroke-dashoffset` animate. Open: height `0fr→1fr` over `--motion-fluid` (420 ms, `--ease-entrance`); lines opacity 0→1 and `translateY(4px)→0` over 240 ms at `280ms + i*60ms`. Close: lines →0 over 160 ms, no stagger; height over 420 ms at 160 ms. Hand-off from the render that marks row k done and row k+1 active: row k lines fade (180 ms, at 0), height closes (420 ms, at 100 ms), subtitle cross-fades to the outcome (200 ms, at 260 ms), connector k→k+1 fills (`--fill-line` 620 ms, at 180 ms); row k+1 node fills (420 ms, at 600 ms), height opens (420 ms, at 600 ms), lines rise (240 ms, at `900ms + i*60ms`). Topic done: ✓ `stroke-dashoffset 14→0` over 360 ms at 80 ms; the dot fades over 200 ms. Counts tween over 400 ms (rAF). The `halo` loop (active node, running topic dots) stays the only loop. Reduced motion: heights change at once; lines fade over 160 ms with no stagger and no translate; the ✓ appears without drawing; counts jump; halos pinned.
- **Theme (D17; `docs/design/running-stage-picks/BRIEF.md` "Theme rules", `:109-125`).** Tokens only. `web/app/globals.css` lines 1–1131 stay the prototype's CSS verbatim; every new rule goes at the end of the file, under `/* ═══ 2026-09-27: app-only additions ═══ */` (`:1133`), with no colour literal; `cd web && npm run -s check:css` prints `OK`. Colour means status (green = running/ok, amber `--status-warn` = the extra-pass reopen line). Purple only on the primary button. One surface per region: hairlines (`--border-soft`), no nested boxes.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone; captures are `fullPage: true`.
- **Out of Phase 1.** No `needs_input`, no check stage, no notes, no `note_passes` field, no new routes, no proxy header changes. `docs/design/prototype/` and `docs/design/reference/` are not changed.

### Conventions every task uses

- **Shell.** Every block runs in the agent's bash tool from the repository root (a POSIX shell with `node`, `npm`, `npx`, `git`, `grep`, `sed` — Git Bash on Windows, bash on Linux). The harness keeps no shell state between calls: every block that runs Python or Playwright sets `PY` first, every `web/` block starts with `cd web`. Paths contain spaces: quote them. No step runs `bash <file>`.
- **Python.** Every block that runs Python starts with the Windows line `PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"`. **On Linux**, the cloud session has already run `bash cloud-session/setup.sh`, which creates `.venv` (Python ≥ 3.11, `pyproject.toml:9`) with the dev extras; if `.venv/bin/python` is missing, create it the same way — `[ -x .venv/bin/python ] || "$(command -v python3.12 || command -v python3.11 || command -v python3)" -m venv .venv && .venv/bin/pip install -e ".[dev]"` — never over an existing venv, and use `PY="$PWD/.venv/bin/python"` in place of that line (blocks start at the repository root). The pytest line is the same everywhere: `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest <files> -q`. Baseline at `7afb21de`: `4726 passed, 1 deselected` (`-m 'not live'`, `pyproject.toml:45`) in about 80 s. One existing test, `tests/test_config.py::test_the_evidence_verifier_pipeline_config`, needs provider keys from an untracked `.env` that only the Windows main checkout has; wherever no `.env` exists — a `.worktrees/*` tree, the Linux cloud checkout — it fails with `MissingSecretsError: Missing required environment variables in strict mode: …` (observed in planning). That failure is environmental: there, add `--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config` to every full-suite run and expect one fewer passed and `2 deselected` (e.g. `4784 passed, 2 deselected` at the end of Task 6). Never read, copy or create `.env`.
- **Web unit tests.** `cd web && npx vitest run [files]`; `cd web && npm run -s typecheck` (tests are type-checked too: `tsconfig.json` includes `**/*.ts(x)`); `cd web && npm run -s check:css`. Baseline: `Test Files 19 passed (19)`, `Tests 117 passed (117)` — under full-suite load one pre-existing race (the C1 test in `session-screen.test.tsx`) can fail it; Task 7 fixes that race.
- **Playwright.** The API runs in replay mode as a Playwright `webServer` (`web/playwright.config.ts`, which sets `cwd` to the repository root and `PYTHONPATH=src`, so it serves the checkout's own backend). `DEEP_RESEARCH_PYTHON="$PY"` is required everywhere but the Windows main checkout (the config's fallback is the Windows venv path), and every block below sets it. On Linux run `cd web && npm ci && npx playwright install --with-deps chromium` once before Task 7. A scoped run builds first and puts the spec files **before** the project flag — `--project chromium` placed first swallows the file arguments as project names (observed in planning): `cd web && npm run -s build && DEEP_RESEARCH_PYTHON="$PY" npx playwright test e2e/a.spec.ts --project=chromium`. The whole project: `cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e` (build + `chromium`; baseline `39 passed`). Captures: `cd web && npm run -s build && VISUAL_CHECKPOINT=<name> DEEP_RESEARCH_PYTHON="$PY" npx playwright test --project=visual` → `8 passed`, images in `web/visual/<name>/` (gitignored).
- **Background servers across calls** start and stop only through `web/scripts/launch.mjs` (`start <name> <port> <ready-path> "<command>"`, `stop <name> <port>`; the API's ready path is `/research`). Its `stop` kills with `taskkill` (`launch.mjs:45`), which exists only on Windows; on Linux the step first ends the recorded process group itself (`start` spawns it detached, so its pid leads the group) and `stop` then only confirms the port is closed.
- **Line endings.** On Windows the files are CRLF on disk (`core.autocrlf=true`); on Linux they are LF. Keep whatever the checkout has — anchors are text and match either way. Edits are given by anchor: the exact existing text to replace (line numbers are `7afb21de`'s, and only locate the text).
- **TDD boundary.** pytest and Vitest tests are written first and shown failing; Playwright specs and replay-level proofs are verification written after the code they exercise ("Verification test").
- **Fingerprint pins.** `tests/test_evaluation/test_config.py` pins `agent_prompt_fingerprint(name)` (a hash of `deep_research.agents.<name>` plus `agents/prompts.py`, `evaluation/config.py:280-288`) for the five agents (`:983` planner, `:1128` researcher, `:1139` source_evaluator, `:1214` evidence_verifier, `:1309` report_writer). A task that edits an agent module re-pins it with the `repin` snippet in its own steps; nothing else in that file changes.
- **Commits.** `git add <paths> && git commit -m "<type>(<scope>): <what>"` — never `git add -A` (`web/visual/`, `web/.e2e-tmp/`, `web/test-results/`, `.launch-*.pid` are gitignored). Append whatever attribution trailer your own session requires.

## Review Focus

1. **The import cycle.** `deep_research.graph` imports every agent while its package initialises (`graph/__init__.py` → `graph/nodes.py` → `agents.*`), so a module-level `from deep_research.graph.live import publish_live` in `agents/planner.py` breaks `import deep_research.agents` and `import deep_research.api.app` with `ImportError: cannot import name 'answer_form_requirement' from partially initialized module 'deep_research.agents.planner'` (observed in planning; in some other agent modules the same line happens to work, so import order decides it). Agents publish through `agents/events.publish_live`, which imports `deep_research.graph.live` at call time. → pinned by the fresh-interpreter import test in Task 3.
2. **Exactly once, with two paths.** A live event is published by the sink and again present in its node's snapshot; the snapshot loop must skip its `event_id`. → Task 2 (`test_every_event_is_delivered_once_when_live_and_snapshot_paths_mix`) and Task 6 (the real graph in replay).
3. **A node that halts after publishing live** has delivered those events, but its halted state keeps none of them. → Task 2 (`test_a_node_that_halts_after_publishing_live_has_delivered_those_events_once`), recorded in api-gaps' delivery model (Task 6).
4. **R1, LangGraph context propagation**, is tested first (Task 2 Step 3) with the contingency written out (Task 2, "Contingency C").
5. **R3, tool-call order.** `researcher.tool_call` is now built in the step callback; it must carry exactly what the post-loop `tool_call_events` would have built, in step order, before the topic's completed event. → Task 3.
6. **Burst-safety of the new RunState fields.** The state after event k must equal a fresh replay of events 1..k for every k of both captures. → Task 8 (`(i) burst-safety`).
7. **The cross-fade needs a stable head.** Swapping the head between `div` and `button` when a row finishes remounts the subtitle and silently kills the 3B cross-fade (observed in planning: no `m-out` transition was recorded). → Task 9 keeps `div.ps-head` and lays a `button.ps-toggle` over it; Task 9's Vitest asserts the same subtitle node survives, Task 10's recorder asserts the transition.
8. **The connector includes the rows' borders.** `.spine-lg li` has `border:1px solid transparent` (`globals.css:470-475`); node centre to next node centre is `--space-2 + 2px + --node-c` below the padding box, or AC6's ±1 px fails by 2 px. → Task 9 geometry e2e at both widths.
9. **A route decision moves the active row before the row it leaves is done.** `graph.route.decided` (finalize) makes Publishing active one event before Reviewing's own `graph.node.completed` (`web/lib/run-state.ts:145-158`, `:100-108`); in replay the two are 150 ms apart. Keyed on the active change alone, the Reviewing → Publishing hand-off never got its `from` role, and Reviewing was painted pending for that event. → Task 9 awaits the row (its mark turning done gives it `from`, and it stays painted active and open until then); `brief-spine.test.tsx` pins the sequence and `motion.spec.ts` checks that nothing on Reviewing's row moves between its `to` and `from` transitions.

## Spec ambiguities resolved here

1. **E3's `graph.node.started` row cites `graph/nodes.py:209-212` (`agent_node`) only.** The reviewer, finalize and hop nodes keep snapshot publication: the web derives the active row from completions (`web/lib/run-state.ts:100-108`, and route decisions at `:145-158`), and the snapshot publishes each node's events in state order with its `graph.node.started` first, so AC1 holds for every node.
2. **Live events of a node that halts** are delivered once and are absent from the final state — inherent to live publication; E3's "same object later returned in `state_update`" cannot hold for a node that raises. Pinned by a test and documented (Tasks 2, 6).
3. **Connector formula (§4.3):** the spec's `bottom: calc(-1 * (var(--space-2) + var(--space-3) + 16.5px))` omits the two rows' 1 px borders; the plan uses `bottom: calc(-1 * (var(--space-2) + 2px + var(--node-c)))` with `--node-c: calc(var(--space-3) + 16.5px)`, which AC6's ±1 px requires. The node-centre formula itself is the spec's (no 4 px node offset from the canvas, `docs/design/running-stage-picks/theme.css:88`).
4. **"The head is a `button[aria-expanded]`" (§4.3):** implemented as `div.ps-head` (name + subtitle) with a `button.ps-toggle[aria-expanded][aria-controls]` laid over the whole head and named by it (`aria-labelledby`), so the head element never changes and the 3B subtitle cross-fade can run (Review Focus 7).
5. **Checklist rows are `div[role=listitem]` in `div.ps-topics[role=list]`**, not `li`: the verbatim descendant rules `.spine-lg li …` (`globals.css:470-496`) would style every topic as a pipeline row. The row itself carries the spec's `li.spine-row` class; no rule targets `.spine-row` today (`git grep spine-row` finds nothing in `web/`, `DESIGN.md` or the prototype), so it is a name, not a style hook.
6. **Extra-pass checklist (§4.3 "shows only the sub-topics that pass re-runs").** The stream names a re-run topic only by its `researcher.sub_topic.started`, so the extra pass's checklist starts empty and lists topics as they start (no waiting rows). It is emptied at `graph.route.decided` (`extra_pass`) — otherwise the previous pass's checklist shows for the hop's duration — and the reason line arrives with `graph.extra_pass.started`, as the spec says.
7. **Zero in words.** AC5 ("no count is ever rendered as `0`") is applied to every Researching text (subtitle, outcome, facts): `no pages read`, `no findings`, `no topics`. Other rows keep the spec's numeric templates (`4 verified · 0 corrected · 0 dropped`): a measured zero is not an unknown value (`docs/design/running-stage-picks/BRIEF.md:123`, theme rule 7), and AC5 is scoped to the checklist.
8. **Copy the spec does not give:** Verifying with 0 or 1 findings (`No findings to check`, `Checking 1 finding against its page`); Reviewing with no score (`Review unavailable`, the chip's own words) or an extra-pass route that lists no target (`Sent back for more research`); the chip before any step is known (`starting`, DESIGN.md §4 rule 6's stage name).
9. **The report head bar's `pass N of P` clause** (`ReportStage.tsx:70-72`) also becomes the plain-words pass fact: D15 makes the report speak in plain words, and DESIGN.md §4 requires the head bar and Session facts to agree. At 1252 px the meta line then wraps to three lines (observed in planning; `#reportMeta` is the one head-bar child allowed to shrink and wrap, so the toggle and both buttons keep their single row, `globals.css:1125-1131`).
10. **Dead helpers.** Removing the ceiling plumbing leaves `passText`, `passTotal`, `passNumber` and `SessionView.passes` unused; they are deleted. `SessionView` gains `step`.
11. **Doc rows the spec's §4.9 does not list** but that D13–D15 or E3 make false are amended too: DESIGN.md §2 (line 143), §3 inventory and the counters paragraph, §3.0, §3.2's composer table and request body, §3.5's arc paragraph ("the loop tag's amber"), §4 (chip copy and the pass section), §6's Running row, and api-gaps 3.1 (recorded obsolete).
12. **RunState** gains, beyond the spec's list, `plan` (the ordered titles Planning's reopened brief shows), `passFindings` (Verifying's count) and `reopen` (the reason lines); `open` lives on RunState as the spec says and a loop's re-armed rows drop out of it.
13. **Topic numbers use `--muted`**, not the canvas's `--meta` (`docs/design/running-stage-picks/theme.css:125`): DESIGN.md §3.5 records `--meta` as a stroke, never text.
14. **The chip at the very end.** After `graph.session.completed` and before `/status` turns terminal, `run.active` is `null`; the chip then names the row the run ended on (`chipStep`: Publishing, or the node that halted) rather than falling back to a stale `/status.current_agent`.
15. **A pre-existing test race.** `web/test/components/session-screen.test.tsx:80-81` asserts the unreachable banner without `waitFor`; under full-suite load it failed once at `7afb21de` itself and in 2 of 4 full Vitest runs once `BriefSpine` joined the module graph (planning). Task 7, the first task that expects the whole suite green, waits for it.
16. **The hand-off moment when a route decision leads (§4.3 motion table, "timed from the moment row k is marked done and row k+1 becomes active").** For Reviewing → Publishing those are two events. Publishing takes its `to` role when it becomes active; Reviewing keeps painting as the active row, open, until its own completion, and only then takes `from` and folds (review round 1, P2-1). Its lines and height fold only as far as they had opened: in replay Reviewing is active for about four paced events, less than its own opening delays as the `to` row.

## Open issues (for the human)

- **O1 — Reviewing's `Sent back to fill {k} gaps` outcome (§4.3 table) can never be seen.** The route decision that produces it also re-arms Reviewing to pending in the same event (DESIGN.md §3.5 "The pipeline is always exactly one pass"; `web/lib/run-state.ts:145-151`, `rearm` at `:79-87`), and a pending row never shows an outcome. The plan computes the string (so it is ready if Reviewing ever stays done through a loop) and relies on Researching's reopen line, which states the same fact. A decision is needed only if the human wants Reviewing to read done during a loop; nothing here blocks Phase 1.

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/utils/types.py` | `ResearchEvent.event_id` | 1 |
| `src/deep_research/graph/live.py` (new) | `_LIVE_SINK`, `publish_live`, `bind_live_sink`, `LiveSink` | 2 |
| `src/deep_research/graph/orchestrator.py` | `_stream_graph_result` binds the sink and skips published ids | 2 |
| `src/deep_research/graph/nodes.py` | `agent_node` publishes `graph.node.started` live | 2 |
| `src/deep_research/graph/__init__.py` | exports the live names | 2 |
| `src/deep_research/agents/events.py` | `publish_live` (call-time import of `graph.live`) | 3 |
| `src/deep_research/agents/researcher.py` | `tool_call_event`; live topic events; `coverage_id` | 3 |
| `src/deep_research/agents/__init__.py` | exports `publish_live`, `tool_call_event` | 3 |
| `src/deep_research/agents/planner.py` | live planner events; `sub_topics` titles | 4 |
| `src/deep_research/agents/source_evaluator.py`, `evidence_verifier.py`, `report_writer.py` | live events | 5 |
| `tests/test_types.py`, `tests/test_graph/test_state.py`, `tests/test_graph/test_session.py`, `tests/test_api/test_stream_and_artifacts.py` | E1 tests | 1 |
| `tests/test_graph/test_live.py` (new) | E2 tests, R1 first | 2 |
| `tests/test_imports.py`, `tests/test_agents/test_researcher.py`, `test_planner.py`, `test_source_evaluator.py`, `test_evidence_verifier.py`, `test_report_writer.py`, `tests/test_evaluation/test_config.py` | E3/AC2 tests and pins | 3–5 |
| `tests/test_api/test_replay.py` | E4 proof on the real graph; topic-findings sum over every replay case | 6 |
| `web/test/fixtures/events/*.json` | regenerated live captures | 6 |
| `docs/design/api-gaps.md`, `docs/design/DESIGN.md` | delivery docs (6); running-stage docs (11) | 6, 11 |
| `web/lib/format.ts`, `lib/api.ts`, `lib/session-store.ts` | chip step, pass fact, no `max_iterations`, no `extraPasses` | 7 |
| `web/components/RunningPipeline.tsx`, `SessionScreen.tsx`, `Composer.tsx`, `SettingsPopover.tsx`, `SettingsStrip.tsx`, `ReportRail.tsx`, `ReportStage.tsx` | removals, chip, pass fact | 7 (RunningPipeline, SessionScreen again in 9) |
| `web/lib/run-state.ts` | `stepLabel`, `chipStep` (7); briefs state and handlers (8) | 7, 8 |
| `web/lib/briefs.ts` (new) | `rowBrief` and the brief copy | 8 |
| `web/lib/tween.ts` (new), `web/components/loop-arc.ts` (new), `web/components/BriefSpine.tsx` (new), `web/components/Spine.tsx` | the running spine, arcs, tweens | 9 |
| `web/app/globals.css` | briefs anatomy (9); hand-off and reduced motion (10) | 9, 10 |
| `web/test/**`, `web/e2e/**` | Vitest and Playwright | 7–10 |

---

### Task 1: `ResearchEvent.event_id` (spec E1; R2)

**Files:**
- Modify: `src/deep_research/utils/types.py:11` (imports), `:34-35` (`def _utc_now_iso() -> str:`), `:1337-1342` (`class ResearchEvent(ContractModel):`)
- Modify (R2): `tests/test_types.py:421-447` (`def test_research_event_serializes_and_round_trips() -> None:`)
- Test: `tests/test_types.py`, `tests/test_graph/test_state.py`, `tests/test_graph/test_session.py`, `tests/test_api/test_stream_and_artifacts.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ResearchEvent.event_id: str` — 32 lowercase hex characters by default (`uuid4().hex`), kept by `model_copy`, `model_dump(mode="json")`/`model_validate`, `dump_state`/`load_state` and the SSE payload; a dumped event without the key loads with a fresh id. Every later task relies on it.

R2 was checked during planning by running the full suite with only this change: exactly one existing test fails (`tests/test_types.py::test_research_event_serializes_and_round_trips`, which compares a dump against a literal dict). Every other event comparison (`tests/test_graph/test_session.py:498,551,631`, `tests/test_runtime/test_run_research.py:458,493`, `tests/test_api/test_sessions.py:736`, `tests/test_api/test_stream_and_artifacts.py:42,93`) compares copies of the same event, whose ids agree.

- [ ] **Step 1: Write the failing tests**

In `tests/test_types.py`, replace the whole of `test_research_event_serializes_and_round_trips` (`:421-447`) with:

```python
def test_research_event_serializes_and_round_trips() -> None:
    event = ResearchEvent(
        event_type="agent.started",
        source="planner",
        message="Planner started.",
        timestamp="2026-07-25T12:00:00+00:00",
        metadata={
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
        event_id="0123456789abcdef0123456789abcdef",
    )
    payload = event.model_dump(mode="json")

    assert payload == {
        "event_type": "agent.started",
        "source": "planner",
        "message": "Planner started.",
        "timestamp": "2026-07-25T12:00:00+00:00",
        "metadata": {
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
        "event_id": "0123456789abcdef0123456789abcdef",
    }
    assert ResearchEvent.model_validate(payload) == event


def test_research_event_ids_default_to_distinct_uuid4_hex() -> None:
    """live-briefs spec E1: every event gets its own identity, so two events built
    alike are distinct, and a copy keeps the identity of what it copies."""
    first = ResearchEvent(event_type="graph.node.started", source="graph", message="Node started.")
    second = ResearchEvent(event_type="graph.node.started", source="graph", message="Node started.")

    assert len(first.event_id) == 32 and int(first.event_id, 16) >= 0
    assert first.event_id == first.event_id.lower()
    assert first.event_id != second.event_id
    assert first != second
    assert first.model_copy(deep=True).event_id == first.event_id
```

In `tests/test_graph/test_state.py`, add `ResearchEvent,` to the `from deep_research.utils.types import (` list — replace

```python
    ResearchError,
    ResearchState,
    ReviewDefect,
```

with

```python
    ResearchError,
    ResearchEvent,
    ResearchState,
    ReviewDefect,
```

and append:

```python
def test_a_checkpoint_written_before_event_ids_loads_with_fresh_distinct_ids() -> None:
    """live-briefs spec E1: an event recorded before ``event_id`` existed loads with a
    fresh id, and that id then survives every later dump and load, so the
    orchestrator's once-only rule holds across a resume."""
    channel = initial_graph_state(session_id="session-1", question="Why?")
    channel["state"]["events"] = [
        ResearchEvent(
            event_type="graph.node.started", source="graph.planner",
            message="Node planner started.", metadata={"node": "planner", "iteration": 0},
        ).model_dump(mode="json"),
        ResearchEvent(
            event_type="graph.node.completed", source="graph.planner",
            message="Node planner completed.", metadata={"node": "planner", "iteration": 0},
        ).model_dump(mode="json"),
    ]
    for event in channel["state"]["events"]:
        del event["event_id"]

    state = load_state(channel)
    ids = [event.event_id for event in state.events]

    assert all(len(event_id) == 32 for event_id in ids)
    assert len(set(ids)) == 2
    assert [event.event_id for event in load_state(dump_state(state)).events] == ids
```

In `tests/test_graph/test_session.py`, append (every name used is already imported there):

```python
@pytest.mark.asyncio
async def test_a_terminal_checkpoint_written_before_event_ids_publishes_each_event_once(
    tracker: Tracker,
) -> None:
    """live-briefs spec E1: an old checkpoint's events get fresh ids once, on load, and
    each is still published exactly once, in state order, with the completion last."""
    agents = fake_research_agents()
    graph = compile_research_graph(agents, checkpointer=build_checkpointer(enabled=True))
    first = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION
    )
    old = dump_state(first.state.model_copy(update={"events": first.state.events[:-1]}))
    for event in old["state"]["events"]:
        del event["event_id"]

    received = []
    resumed = await resume_research_graph(
        graph=EmptyValuesGraph(snapshot=SimpleNamespace(values=old, next=())),
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert resumed.status == "completed"
    assert received == resumed.state.events
    assert len({event.event_id for event in received}) == len(received)
    assert received[-1].event_type == "graph.session.completed"
```

In `tests/test_api/test_stream_and_artifacts.py`, directly after `test_stream_returns_typed_progress_as_sse`, add:

```python
def test_the_sse_payload_carries_the_event_id() -> None:
    """live-briefs spec E1: the payload is the event's own JSON, so its ``event_id``
    travels unchanged; the SSE ``id:`` line stays the per-subscriber position."""
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
        metadata={"node": "planner", "iteration": 0},
    )
    app = create_app(runner=ScriptedRunner(events=[event]), preflight=valid_preflight)

    with TestClient(app) as client:
        created = client.post("/research", json={"query": "Question"}).json()
        with client.stream("GET", f"/research/{created['session_id']}/stream") as response:
            body = "".join(response.iter_text())

    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["event_id"] == event.event_id
    assert "id: 1\n" in body
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_types.py tests/test_graph/test_state.py tests/test_graph/test_session.py tests/test_api/test_stream_and_artifacts.py -q -k "research_event_serializes or event_id"
```
Expected: `5 failed, 1 passed` — the round trip with `Extra inputs are not permitted`, the defaults test with `AttributeError: 'ResearchEvent' object has no attribute 'event_id'`, the other three with `KeyError: 'event_id'`; the existing `test_encode_sse_rejects_event_ids_below_one` (selected by `-k`) passes.

- [ ] **Step 3: Add the field**

In `src/deep_research/utils/types.py`, after `from urllib.parse import urlsplit, urlunsplit` add:

```python
from uuid import uuid4
```

Replace

```python
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

with

```python
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_event_id() -> str:
    """A fresh identity for one progress event: 32 lowercase hex characters."""
    return uuid4().hex
```

Replace

```python
class ResearchEvent(ContractModel):
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, _FiniteJsonValue] = Field(default_factory=dict)
```

with

```python
class ResearchEvent(ContractModel):
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, _FiniteJsonValue] = Field(default_factory=dict)
    event_id: str = Field(default_factory=_new_event_id, min_length=1)
    """This event's identity (live-briefs spec E1). One event object can reach the
    stream twice — published live, then again inside its node's snapshot — and the
    orchestrator publishes each id once. Fresh per construction and kept by every
    copy and dump; an event dumped before this field existed loads with a fresh id."""
```

- [ ] **Step 4: Run the touched suites**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_types.py tests/test_state.py tests/test_graph tests/test_api tests/test_runtime -q
```
Expected: `490 passed` (observed in planning), `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/utils/types.py tests/test_types.py tests/test_graph/test_state.py tests/test_graph/test_session.py tests/test_api/test_stream_and_artifacts.py && git commit -m "feat(events): every ResearchEvent carries an event_id"
```

---

### Task 2: The run-scoped live sink (spec E2; R1 tested first; AC1)

**Files:**
- Create: `src/deep_research/graph/live.py`, `tests/test_graph/test_live.py`
- Modify: `src/deep_research/graph/orchestrator.py:36-40` (imports), `:304-354` (`async def _stream_graph_result(`); `src/deep_research/graph/nodes.py:67-79` (imports), `:209-214` (inside `agent_node`); `src/deep_research/graph/__init__.py` (imports, `__all__`); `tests/test_imports.py:488` (the graph-submodule guard's list)

**Interfaces:**
- Consumes: `ResearchEvent.event_id` (Task 1).
- Produces: `deep_research.graph.live`: `LiveSink = Callable[[ResearchEvent], None]`; `publish_live(event: ResearchEvent) -> None` (no-op when unbound); `bind_live_sink(sink: LiveSink) -> ContextManager[None]` (restores the previous sink on exit); `_LIVE_SINK: ContextVar[LiveSink | None]`. `_stream_graph_result` binds a sink for the stream and publishes each `event_id` once. `agent_node` publishes `graph.node.started` live. Call `publish_live` on the event loop only (the sink calls `ResearchSession.publish`, which is not thread-safe, `api/sessions.py:61-72`).

- [ ] **Step 1: Write the R1 test and the sink's unit tests**

Create `tests/test_graph/test_live.py` (the `tracker` fixture comes from `tests/test_graph/conftest.py`):

```python
"""Live publication (live-briefs spec E2, AC1): events reach the handler as they
happen, exactly once."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import PlanningError
from deep_research.agents.events import agent_event
from deep_research.agents.steps import ReActRun
from deep_research.graph.live import _LIVE_SINK, bind_live_sink, publish_live
from deep_research.graph.orchestrator import (
    compile_research_graph,
    run_research_graph,
    session_config,
)
from deep_research.graph.state import initial_graph_state
from deep_research.observability import Tracker
from deep_research.utils.types import ResearchEvent, ResearchState
from tests.graph_fakes import fake_research_agents, fake_sub_topic, fake_target

QUESTION = "How mature is quantum error correction?"


def _event(event_type: str) -> ResearchEvent:
    return agent_event(agent_name="planner", event_type=event_type, message="Probe event.")


class LivePlanner:
    """A planner double that publishes live from its node's task and from a child task.

    ``seen_during_run`` is what the handler had received when ``run`` finished — the
    proof that live events arrived while the node was still running, not with its
    snapshot. ``sink_seen`` records whether the node's task could see a bound sink.
    """

    name = "planner"

    def __init__(self, received: list[ResearchEvent], *, fail: bool = False) -> None:
        self.received = received
        self.fail = fail
        self.published: dict[str, ResearchEvent] = {}
        self.seen_during_run: list[ResearchEvent] = []
        self.sink_seen: bool | None = None

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.sink_seen = _LIVE_SINK.get() is not None
        direct = _event("planner.planning.started")
        publish_live(direct)
        self.published["direct"] = direct

        async def child() -> None:
            event = _event("test.child_task")
            publish_live(event)
            self.published["child"] = event

        await asyncio.gather(child())
        self.seen_during_run = list(self.received)
        if self.fail:
            raise PlanningError("scripted planning failure")
        quiet = _event("test.snapshot_only")
        self.published["quiet"] = quiet
        return AgentRun(
            agent_name=self.name,
            result=None,
            react=ReActRun(agent_name=self.name, stop_reason="finished"),
            errors=[],
            state_update={
                "sub_topics": [fake_sub_topic(targets=[fake_target()])],
                "events": [direct, self.published["child"], quiet],
            },
        )


@pytest.mark.asyncio
async def test_a_sink_bound_around_the_graph_stream_reaches_node_tasks() -> None:
    """Risk R1, tested first: LangGraph runs a node in a task that inherits the
    ContextVar bound around ``astream``, and so does every task the node starts."""
    delivered: list[ResearchEvent] = []
    planner = LivePlanner(delivered)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    with bind_live_sink(delivered.append):
        async for _ in graph.astream(
            initial_graph_state(session_id="session-1", question=QUESTION),
            session_config("session-1", max_extra_passes=1),
            stream_mode="values",
        ):
            pass

    assert planner.sink_seen is True
    ids = {event.event_id for event in delivered}
    assert planner.published["direct"].event_id in ids
    assert planner.published["child"].event_id in ids


def test_publish_live_without_a_bound_sink_is_a_no_op() -> None:
    publish_live(_event("planner.planning.started"))  # nothing bound: no error


def test_bind_live_sink_restores_the_previous_sink() -> None:
    outer: list[ResearchEvent] = []
    inner: list[ResearchEvent] = []
    first, second, third = _event("a.b"), _event("a.c"), _event("a.d")
    with bind_live_sink(outer.append):
        with bind_live_sink(inner.append):
            publish_live(first)
        publish_live(second)
    publish_live(third)

    assert inner == [first]
    assert outer == [second]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_live.py -q
```
Expected: `1 error` — `ModuleNotFoundError: No module named 'deep_research.graph.live'`.

- [ ] **Step 3: Create the sink and settle R1**

Create `src/deep_research/graph/live.py`:

```python
"""Live publication: the run-scoped sink agents and nodes publish events to as they happen.

``_stream_graph_result`` binds a sink for the duration of one streamed run
(live-briefs spec E2); ``publish_live`` hands an event to it and is a no-op when
nothing is bound — a run without an event handler, or a unit test calling an agent
directly. The event published live is the same object the node later returns in its
state update, so the orchestrator's snapshot loop recognises it by ``event_id`` and
never publishes it twice.

A ContextVar rather than a parameter: LangGraph runs each node in a task that copies
the current context, and every task an agent starts copies it again, so a sink bound
around the stream reaches the researcher's concurrent sub-topic loops without being
threaded through each call. Call ``publish_live`` on the event loop only, never from a
worker thread: the sink calls the session's ``publish``, which is not thread-safe.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from deep_research.utils.types import ResearchEvent

LiveSink = Callable[[ResearchEvent], None]

_LIVE_SINK: ContextVar[LiveSink | None] = ContextVar(
    "deep_research_live_sink", default=None
)


def publish_live(event: ResearchEvent) -> None:
    """Hand one event to the run's live sink, if one is bound."""
    sink = _LIVE_SINK.get()
    if sink is not None:
        sink(event)


@contextmanager
def bind_live_sink(sink: LiveSink) -> Iterator[None]:
    """Bind ``sink`` for the body of the ``with`` block, then restore the previous one."""
    token = _LIVE_SINK.set(sink)
    try:
        yield
    finally:
        _LIVE_SINK.reset(token)
```

Run:

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_live.py -q
```
Expected: `3 passed` (observed in planning on LangGraph 1.2.10). **If `test_a_sink_bound_around_the_graph_stream_reaches_node_tasks` fails with `assert None is True` or `assert False is True` (the node's task did not see the sink), R1 has materialised: carry on with Steps 4–6 exactly as written, then apply "Contingency C" (at the end of this task) before Step 7.**

- [ ] **Step 4: Write the handler-level tests**

Append to `tests/test_graph/test_live.py`:

```python
@pytest.mark.asyncio
async def test_live_sink_reaches_nodes(tracker: Tracker) -> None:
    """The spec's named test (E2): a node's live events — its own, a child task's and
    its graph.node.started — reach the handler while the node runs, before the node's
    graph.node.completed."""
    received: list[ResearchEvent] = []
    planner = LivePlanner(received)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    seen = {event.event_id for event in planner.seen_during_run}
    assert planner.published["direct"].event_id in seen
    assert planner.published["child"].event_id in seen
    assert planner.published["quiet"].event_id not in seen
    started = next(
        e for e in received
        if e.event_type == "graph.node.started" and e.metadata["node"] == "planner"
    )
    assert started.event_id in seen
    types = [event.event_type for event in received]
    done = next(
        i for i, e in enumerate(received)
        if e.event_type == "graph.node.completed" and e.metadata["node"] == "planner"
    )
    assert max(
        types.index("planner.planning.started"),
        types.index("test.child_task"),
        types.index("test.snapshot_only"),
    ) < done
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_every_event_is_delivered_once_when_live_and_snapshot_paths_mix(
    tracker: Tracker,
) -> None:
    received: list[ResearchEvent] = []
    planner = LivePlanner(received)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    counts = Counter(event.event_id for event in received)
    assert set(counts.values()) == {1}
    assert set(counts) == {event.event_id for event in run.state.events}
    live = {event.event_id for event in planner.seen_during_run} | {
        e.event_id for e in received if e.event_type == "graph.node.started"
    }
    # Everything not published live keeps state order.
    assert [e.event_id for e in received if e.event_id not in live] == [
        e.event_id for e in run.state.events if e.event_id not in live
    ]
    assert received[0].event_type == "graph.session.started"
    assert received[-1].event_type == "graph.session.completed"


@pytest.mark.asyncio
async def test_a_node_that_halts_after_publishing_live_has_delivered_those_events_once(
    tracker: Tracker,
) -> None:
    """Delivered live, then the node halted: the handler has the events once, and the
    halted state — which keeps nothing the failed agent returned — does not."""
    received: list[ResearchEvent] = []
    graph = compile_research_graph(
        fake_research_agents(planner=LivePlanner(received, fail=True))
    )

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    assert run.status == "failed"
    assert [e.event_type for e in received].count("planner.planning.started") == 1
    assert "planner.planning.started" not in [e.event_type for e in run.state.events]
    assert {e.event_id for e in run.state.events} <= {e.event_id for e in received}
    assert len({e.event_id for e in received}) == len(received)


@pytest.mark.asyncio
async def test_two_runs_at_once_keep_their_own_sinks(tracker: Tracker) -> None:
    first: list[ResearchEvent] = []
    second: list[ResearchEvent] = []

    async def one(session_id: str, sink: list[ResearchEvent]) -> None:
        graph = compile_research_graph(fake_research_agents(planner=LivePlanner(sink)))
        await run_research_graph(
            graph=graph, tracker=tracker, session_id=session_id, question=QUESTION,
            event_handler=sink.append,
        )

    await asyncio.gather(one("session-a", first), one("session-b", second))

    for sink, session_id in ((first, "session-a"), (second, "session-b")):
        assert sink[0].metadata["session_id"] == session_id
        assert [e.event_type for e in sink].count("planner.planning.started") == 1
        assert [e.event_type for e in sink].count("test.child_task") == 1


@pytest.mark.asyncio
async def test_a_run_without_a_handler_binds_no_sink(tracker: Tracker) -> None:
    planner = LivePlanner([])
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION
    )

    assert run.status == "completed"
    assert planner.sink_seen is False
```

- [ ] **Step 5: Run them to verify the live path is missing**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_live.py -q
```
Expected: `2 failed, 6 passed` — `test_live_sink_reaches_nodes` (`AssertionError: assert '<32 hex>' in {'<32 hex>'}`: while the node ran, the handler had received only `graph.session.started`) and `test_a_node_that_halts…` (`assert 0 == 1`: without live publication the halted node's events are lost).

- [ ] **Step 6: Bind the sink in the orchestrator, skip published ids, publish `graph.node.started` live**

`src/deep_research/graph/orchestrator.py` — after the block

```python
from deep_research.graph.events import (
    session_completed_event,
    session_started_event,
)
```

add

```python
from deep_research.graph.live import bind_live_sink
```

In `_stream_graph_result`, replace the docstring's first paragraph

```python
    """Run the graph in values mode, publishing only newly appended events.

    Each ``stream_mode="values"`` snapshot is the cumulative channel, so the
    slice after the last published index is exactly the events this superstep
    appended — nothing is published twice, and order within a snapshot is the
    order the graph recorded it. On a resume the first snapshot carries the
    checkpointed events, so a fresh handler still sees the whole session.
```

with

```python
    """Run the graph in values mode, publishing every event exactly once.

    Events reach the handler by two paths (live-briefs spec E2). An agent or node
    publishes an event *live* the moment it builds it, through the sink bound here
    for the stream's duration (``graph/live.py``); the sink records its
    ``event_id``. Each ``stream_mode="values"`` snapshot is the cumulative channel,
    so the slice after the last published index is exactly the events this
    superstep appended; they are published in state order, skipping any id the live
    path already delivered. So nothing is published twice, a live event can arrive
    ahead of events its node recorded earlier in state order, and a node that halts
    after publishing live has delivered those events although its halted state keeps
    none of them. On a resume the first snapshot carries the checkpointed events, so
    a fresh handler still sees the whole session.
```

and replace

```python
    latest: ResearchGraphState | None = None
    published = 0

    if channel is not None:
        initial = load_state(channel)
        for event in initial.events:
            event_handler(event)
        published = len(initial.events)

    async for snapshot in graph.astream(
        channel,
        config,
        stream_mode="values",
    ):
        latest = snapshot
        state = load_state(snapshot)
        for event in state.events[published:]:
            event_handler(event)
        published = len(state.events)
```

with

```python
    latest: ResearchGraphState | None = None
    published = 0
    published_ids: set[str] = set()

    def live(event: ResearchEvent) -> None:
        event_handler(event)
        published_ids.add(event.event_id)

    if channel is not None:
        initial = load_state(channel)
        for event in initial.events:
            event_handler(event)
        published = len(initial.events)

    with bind_live_sink(live):
        async for snapshot in graph.astream(
            channel,
            config,
            stream_mode="values",
        ):
            latest = snapshot
            state = load_state(snapshot)
            for event in state.events[published:]:
                if event.event_id not in published_ids:
                    event_handler(event)
            published = len(state.events)
```

`src/deep_research/graph/nodes.py` — replace

```python
    route_decided_event,
)
from deep_research.graph.state import (
```

with

```python
    route_decided_event,
)
from deep_research.graph.live import publish_live
from deep_research.graph.state import (
```

and in `agent_node` replace

```python
        started = merge_research_state(
            state,
            {"events": [node_started_event(name, iteration=state.iteration)]},
        )
        try:
            outcome = await agent.run(started)
```

with

```python
        started_event = node_started_event(name, iteration=state.iteration)
        started = merge_research_state(state, {"events": [started_event]})
        # Published live (live-briefs spec E3): the object merged here is the one
        # this node's snapshot carries, so the orchestrator delivers it once.
        publish_live(started_event)
        try:
            outcome = await agent.run(started)
```

`src/deep_research/graph/__init__.py` — after the `from deep_research.graph.events import (…)` block add

```python
from deep_research.graph.live import LiveSink, bind_live_sink, publish_live
```

and in `__all__` add `"LiveSink",` after `"GraphRun",`, `"bind_live_sink",` after `"agent_node",`, and `"publish_live",` after `"publication_write_error",`.

`tests/test_imports.py` — `test_graph_submodule_public_names_all_reach_all` checks that every public module-level name of the graph submodules it lists is in `deep_research.graph.__all__`; add the new module to its list (`:488`), replacing

```python
    submodules = ["errors", "events", "nodes", "orchestrator", "state"]
```

with

```python
    submodules = ["errors", "events", "live", "nodes", "orchestrator", "state"]
```

(`live.py`'s public names are `LiveSink`, `publish_live` and `bind_live_sink`, all exported above; `_LIVE_SINK` is private.)

- [ ] **Step 7: Run the graph, runtime and API suites**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph tests/test_runtime tests/test_api tests/test_imports.py -q
```
Expected: `440 passed`, `0 failed`; `tests/test_graph/test_live.py` contributes `8 passed` (observed in planning).

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/graph/live.py src/deep_research/graph/orchestrator.py src/deep_research/graph/nodes.py src/deep_research/graph/__init__.py tests/test_graph/test_live.py tests/test_imports.py && git commit -m "feat(graph): publish events live through a run-scoped sink, each id once"
```

**Contingency C — only if Step 3's R1 test failed.** The ContextVar did not reach the node's task, so each node re-binds the run's sink inside its own task, finding it through the `Tracker` the agents already hold (spec R1: "pass the sink through the Tracker the agents already hold", `agents/base.py:325-328`). Sinks are keyed by session id, because one `Tracker` can serve concurrent runs — the tests' shared `tracker` fixture does in `test_two_runs_at_once_keep_their_own_sinks` — and every call site keeps calling `publish_live`. Planning never ran this path (R1 held on LangGraph 1.2.10).

C.1 `src/deep_research/observability/tracker.py`: in `Tracker.__init__`, directly after `self._metrics: list[MetricRecord] = []` (`:257`), add

```python
        # live-briefs spec R1 fallback: each streamed run's live sink, keyed by
        # session id (one Tracker can serve concurrent runs); the orchestrator
        # sets it for the stream's duration and each graph node re-binds it.
        self.live_sinks: dict[str, Callable[[ResearchEvent], None]] = {}
```

(`Callable` and `ResearchEvent` are already imported there, `tracker.py:6` and `:47`.)

C.2 `src/deep_research/graph/orchestrator.py`: replace Step 6's import line with `from deep_research.graph.live import LiveSink, bind_live_sink`; in `_stream_graph_result`'s keyword parameters, directly after `terminal_checkpoint: ResearchState | None = None,` add

```python
    tracker: Tracker | None = None,
    session_id: str | None = None,
```

replace Step 6's

```python
    with bind_live_sink(live):
        async for snapshot in graph.astream(
            channel,
            config,
            stream_mode="values",
        ):
            latest = snapshot
            state = load_state(snapshot)
            for event in state.events[published:]:
                if event.event_id not in published_ids:
                    event_handler(event)
            published = len(state.events)
```

with

```python
    sinks: dict[str, LiveSink] | None = None
    if tracker is not None and session_id is not None:
        sinks = tracker.live_sinks
        sinks[session_id] = live
    try:
        with bind_live_sink(live):
            async for snapshot in graph.astream(
                channel,
                config,
                stream_mode="values",
            ):
                latest = snapshot
                state = load_state(snapshot)
                for event in state.events[published:]:
                    if event.event_id not in published_ids:
                        event_handler(event)
                published = len(state.events)
    finally:
        if sinks is not None and session_id is not None:
            sinks.pop(session_id, None)
```

and in `_invoke`'s call `_stream_graph_result(` add the two arguments `tracker=tracker,` and `session_id=session_id,` after `terminal_checkpoint=terminal_checkpoint,`.

C.3 `src/deep_research/graph/nodes.py`: directly after `from collections.abc import Awaitable, Callable, Mapping, Sequence` (`:23`) add `from contextlib import AbstractContextManager, nullcontext`; replace Step 6's `from deep_research.graph.live import publish_live` with `from deep_research.graph.live import bind_live_sink, publish_live`; directly above `def agent_node(` add

```python
def _rebound_sink(agent: object, session_id: str) -> AbstractContextManager[None]:
    """Re-bind this run's live sink inside the node's own task (live-briefs R1 fallback)."""
    sinks = getattr(getattr(agent, "tracker", None), "live_sinks", None)
    sink = sinks.get(session_id) if isinstance(sinks, dict) else None
    return bind_live_sink(sink) if sink is not None else nullcontext()


async def _run_with_sink(agent: Any, started: ResearchState, session_id: str) -> AgentRun[Any]:
    """Run the agent with this run's live sink bound in the node's own task."""
    with _rebound_sink(agent, session_id):
        return await agent.run(started)
```

and in `agent_node` replace Step 6's

```python
        publish_live(started_event)
        try:
            outcome = await agent.run(started)
```

with

```python
        with _rebound_sink(agent, state.session_id):
            publish_live(started_event)
        try:
            outcome = await _run_with_sink(agent, started, state.session_id)
```

C.4 `tests/test_graph/test_live.py`: give `LivePlanner.__init__` the keyword parameter `tracker: Tracker | None = None` (after `fail: bool = False`) and the line `self.tracker = tracker`; in the five tests that call `run_research_graph`, construct the planner with `tracker=tracker` as well (e.g. `LivePlanner(received, tracker=tracker)`, `LivePlanner(received, fail=True, tracker=tracker)`, `LivePlanner(sink, tracker=tracker)`, `LivePlanner([], tracker=tracker)`); and in `test_a_sink_bound_around_the_graph_stream_reaches_node_tasks` replace the three assertions after the `with` block with

```python
    # R1 materialised (Contingency C): a sink bound around astream alone is not
    # seen inside LangGraph's node task; the Tracker-keyed re-binding in
    # graph/nodes.py (`_rebound_sink`) is what carries it there.
    assert planner.sink_seen is False
```

C.5 Run Step 7's command: expected `440 passed`, `0 failed`, as without the contingency. Record "R1 materialised; Contingency C applied" in the task summary.

---

### Task 3: The researcher publishes its topic events live (spec E3 researcher rows; R3; Review Focus 1)

**Files:**
- Modify: `src/deep_research/agents/events.py` (append `publish_live`); `src/deep_research/agents/researcher.py:38` (import), `:2573-2618` (`def sub_topic_started_event(` … `def tool_call_events(`), `:2672-2674` (`sub_topic_completed_event` metadata), `:2917-2927` (`class _LoopWithExtraction(NamedTuple):`), `:4378-4379` and `:4408-4413` (in `_research_sub_topic`), `:4415-4556` (`async def _research_one(`); `src/deep_research/agents/__init__.py:46-48`, `:397-398`, `:537`, `:864`
- Modify: `tests/test_evaluation/test_config.py:1128` (researcher pin)
- Test: `tests/test_agents/test_researcher.py` (append), `tests/test_imports.py` (append)

**Interfaces:**
- Consumes: `deep_research.graph.live.publish_live` (Task 2), through a call-time import.
- Produces: `deep_research.agents.events.publish_live(event: ResearchEvent) -> None` — the one function agents call (Tasks 4–5 use it); `deep_research.agents.researcher.tool_call_event(sub_topic: SubTopic, step: ReActStep) -> ResearchEvent | None`; `tool_call_events(sub_topic, run)` unchanged in signature and output; `_LoopWithExtraction.tool_calls: list[ResearchEvent]`; metadata `coverage_id: str` on `researcher.sub_topic.started` and `researcher.sub_topic.completed`. Both new names are exported from `deep_research.agents`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_researcher.py`: in the `from deep_research.agents.researcher import (` list replace

```python
    select_sub_topics,
)
from deep_research.agents.steps import (
```

with

```python
    select_sub_topics,
    tool_call_events,
)
from deep_research.agents.steps import (
```

replace `from deep_research.memory.scratchpad import ScratchpadMemory` with

```python
from deep_research.graph.live import bind_live_sink
from deep_research.memory.scratchpad import ScratchpadMemory
```

in the `from deep_research.utils.types import (` list replace

```python
    ResearchError,
    ResearchState,
    SubTopic,
    merge_research_state,
)
```

with

```python
    ResearchError,
    ResearchEvent,
    ResearchState,
    SubTopic,
    merge_research_state,
)
```

and append:

```python
@pytest.mark.asyncio
async def test_the_researcher_publishes_topic_and_tool_call_events_live_in_step_order(
    tracker: Tracker,
) -> None:
    """live-briefs spec E3 and R3: the started event as the loop begins, each tool call
    as its step is recorded, the completed event once extraction settles — live, in step
    order, as the very objects the run returns — and each tool call carries exactly what
    the post-loop rebuild (``tool_call_events``) builds."""
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(), outputs=[_findings_draft()]
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 1)])
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "q"):
        with bind_live_sink(received.append):
            outcome = await agent.run(state)

    returned = outcome.state_update["events"]
    assert [event.event_type for event in received] == [
        "researcher.sub_topic.started",
        "researcher.tool_call",
        "researcher.tool_call",
        "researcher.sub_topic.completed",
    ]
    assert [event.event_id for event in received] == [
        event.event_id for event in returned[:4]
    ]
    assert returned[4].event_type == "researcher.research.completed"
    assert returned[0].metadata["coverage_id"] == "topic-01"
    assert returned[3].metadata["coverage_id"] == "topic-01"

    def call(event: ResearchEvent) -> tuple[object, ...]:
        m = event.metadata
        return (m["sub_topic"], m["tool"], m["proposal_id"], m["iteration"], m["success"], m["error_type"])

    assert [call(e) for e in returned if e.event_type == "researcher.tool_call"] == [
        call(e) for e in tool_call_events(state.sub_topics[0], outcome.react)
    ]
```

`tests/test_imports.py`: replace the line `import deep_research` (`:3`) with

```python
import os
import subprocess
import sys
from pathlib import Path

import pytest

import deep_research
```

and append:

```python
@pytest.mark.parametrize(
    "module",
    [
        "deep_research.agents",
        "deep_research.agents.planner",
        "deep_research.agents.researcher",
        "deep_research.graph",
        "deep_research.api.app",
    ],
)
def test_either_side_of_the_live_import_cycle_can_be_imported_first(module: str) -> None:
    """live-briefs spec E2/E3: agents publish through ``deep_research.graph.live``, and
    ``deep_research.graph`` imports every agent while its package initialises, so a
    module-level import from an agent module would break whichever order a fresh
    process imports in. Each module here is imported first, in a fresh interpreter."""
    src = Path(deep_research.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
```

- [ ] **Step 2: Run them to verify the researcher test fails**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_researcher.py tests/test_imports.py -q -k "publishes_topic_and_tool_call or live_import_cycle"
```
Expected: `1 failed, 5 passed` — the researcher test with `assert [] == ['researcher.sub_topic.started', …]`; the five import guards pass today and stay the guard for Step 3. Why a guard (observed in planning): with a module-level `from deep_research.graph.live import publish_live` in `agents/planner.py` or `agents/report_writer.py`, `import deep_research.agents` and `import deep_research.api.app` fail with `ImportError: cannot import name 'answer_form_requirement' from partially initialized module 'deep_research.agents.planner' (most likely due to a circular import)` and this test file fails at collection; the same line in `researcher.py`, `source_evaluator.py` or `evidence_verifier.py` happens to import cleanly today. Agents therefore never import `graph.live` at module level — they call the helper added in Step 3 — so whether it works never depends on import order.

- [ ] **Step 3: The agents' publish helper**

Append to `src/deep_research/agents/events.py`:

```python


def publish_live(event: ResearchEvent) -> None:
    """Publish one agent event live, through the run's sink, if one is bound.

    live-briefs spec E3: the same object must also be returned in the agent's
    ``state_update["events"]`` -- the orchestrator recognises it there by its
    ``event_id`` and does not publish it twice. ``graph.live`` is imported at call
    time: ``deep_research.graph`` imports every agent while its package
    initialises, so a module-level import here would close an import cycle
    whenever ``deep_research.agents`` is imported first.
    """
    from deep_research.graph import live  # noqa: PLC0415

    live.publish_live(event)
```

In `src/deep_research/agents/__init__.py` replace

```python
from deep_research.agents.events import (
    agent_event,
)
```

with

```python
from deep_research.agents.events import (
    agent_event,
    publish_live,
)
```

replace `    sub_topic_started_event,\n    tool_call_events,` (the researcher import list, `:397-398`) with

```python
    sub_topic_started_event,
    tool_call_event,
    tool_call_events,
```

and in `__all__` add `    "publish_live",` after `    "agent_event",` and `    "tool_call_event",` before `    "tool_call_events",`.

- [ ] **Step 4: Publish the researcher's topic events live**

In `src/deep_research/agents/researcher.py`:

(a) Replace `from deep_research.agents.events import agent_event` with

```python
from deep_research.agents.events import agent_event, publish_live
```

(b) In `sub_topic_started_event` replace

```python
    """Announce that one sub-topic's loop is about to run."""
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.started",
        message=f"Researching sub-topic {index}.",
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "priority": sub_topic.priority,
```

with

```python
    """Announce that one sub-topic's loop is about to run.

    ``coverage_id`` lets a console match the topic to the plan's own list
    (live-briefs spec E3).
    """
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.started",
        message=f"Researching sub-topic {index}.",
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "coverage_id": sub_topic.coverage_id,
            "priority": sub_topic.priority,
```

(c) Replace the whole of `def tool_call_events(` … `return events` (`:2593-2618`) with

```python
def tool_call_event(sub_topic: SubTopic, step: ReActStep) -> ResearchEvent | None:
    """Report one tool call the sub-topic's loop made, or ``None`` for a step without one.

    Built when the step's observation is recorded (live-briefs spec E3), so the
    event is stamped at the call rather than when the sub-topic's loop ends.
    """
    observation = step.observation
    if observation is None:
        return None
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.tool_call",
        message=f"{observation.tool_name} call completed.",
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "tool": observation.tool_name,
            "proposal_id": step.proposal_id,
            "iteration": step.iteration,
            "success": observation.success,
            "error_type": observation.error_type,
        },
    )


def tool_call_events(
    sub_topic: SubTopic,
    run: ReActRun,
) -> list[ResearchEvent]:
    """Report one event per tool call the sub-topic's loop made."""
    return [
        event
        for step in run.steps
        if (event := tool_call_event(sub_topic, step)) is not None
    ]
```

(d) In `sub_topic_completed_event` replace

```python
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "index": index,
            "stop_reason": run.stop_reason,
```

with

```python
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "coverage_id": sub_topic.coverage_id,
            "index": index,
            "stop_reason": run.stop_reason,
```

(e) Replace

```python
    react: ReActRun
    extraction_tasks: dict[str, "asyncio.Task[_PageExtraction]"]
    admitted_read_order: list[str]
    extraction_gate: asyncio.Semaphore
```

with

```python
    react: ReActRun
    extraction_tasks: dict[str, "asyncio.Task[_PageExtraction]"]
    admitted_read_order: list[str]
    extraction_gate: asyncio.Semaphore
    tool_calls: list[ResearchEvent]
    """The loop's ``researcher.tool_call`` events, in step order, each built and
    published live as its step was recorded (live-briefs spec E3)."""
```

(f) In `_research_sub_topic` replace

```python
        async def record(step: ReActStep) -> None:
            await self._record_step(step, scratchpad=scratchpad)
```

with

```python
        tool_calls: list[ResearchEvent] = []

        async def record(step: ReActStep) -> None:
            await self._record_step(step, scratchpad=scratchpad)
            event = tool_call_event(task.sub_topic, step)
            if event is not None:
                publish_live(event)
                tool_calls.append(event)
```

and replace

```python
            admitted_read_order=admitted_read_order,
            extraction_gate=extraction_gate,
        )
```

with

```python
            admitted_read_order=admitted_read_order,
            extraction_gate=extraction_gate,
            tool_calls=tool_calls,
        )
```

(g) In `_research_one` replace

```python
        Nothing here writes to the agent: the loop's findings, errors, events
        and counters are returned for ``run`` to fold in plan order.
        """
        sub_topic = task.sub_topic
        events = [
            sub_topic_started_event(
                sub_topic,
                index=index,
                existing_sources=len(task.existing_sources),
            )
        ]
```

with

```python
        Nothing here writes to the agent: the loop's findings, errors, events
        and counters are returned for ``run`` to fold in plan order. The topic's
        events are also published live as they happen (live-briefs spec E3): the
        started event as the loop begins, each tool call as its step is
        recorded, the completed event once extraction settles -- the very
        objects this outcome returns.
        """
        sub_topic = task.sub_topic
        started = sub_topic_started_event(
            sub_topic,
            index=index,
            existing_sources=len(task.existing_sources),
        )
        publish_live(started)
        events = [started]
```

replace

```python
                react = loop_result.react
                elapsed_s = round(perf_counter() - started_at, 1)
```

with

```python
                react = loop_result.react
                tool_calls = loop_result.tool_calls
                elapsed_s = round(perf_counter() - started_at, 1)
```

replace

```python
        events.extend(tool_call_events(sub_topic, react))
        events.append(
            sub_topic_completed_event(
```

with

```python
        events.extend(tool_calls)
        completed = sub_topic_completed_event(
```

and replace

```python
                elapsed_s=elapsed_s,
            )
        )
        return _SubTopicOutcome(
```

with

```python
                elapsed_s=elapsed_s,
        )
        publish_live(completed)
        events.append(completed)
        return _SubTopicOutcome(
```

(The arguments between them keep their indentation; the call now closes one level out.)

- [ ] **Step 5: Run the researcher, planner-seam and import tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_imports.py tests/test_graph -q
```
Expected: `432 passed`, `0 failed` (observed in planning).

- [ ] **Step 6: Re-pin the researcher's fingerprint (module code only)**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - researcher a0691eedc3b2 "Live briefs (live-briefs spec E3, 2026-09-28): the topic events carry their coverage_id, each researcher.tool_call is built as its step is recorded (tool_call_event), and the three topic events are published live. Module code only; no prompt string changed and agents.prompts was untouched, so the other four target pins and the Judge pin are unchanged." <<'EOF'
import sys
import textwrap
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

agent, old, reason = sys.argv[1:4]
new = agent_prompt_fingerprint(agent)
path = Path("tests/test_evaluation/test_config.py")
text = path.read_text(encoding="utf-8")
anchor = f'    "{agent}": "{old}",\n'
assert text.count(anchor) == 1, f"pin {agent}={old} not found exactly once"
comment = textwrap.fill(
    f"{reason} Moved `{old}` -> `{new}`.", width=78,
    initial_indent="    # ", subsequent_indent="    # ",
)
path.write_text(text.replace(anchor, f'{comment}\n    "{agent}": "{new}",\n'), encoding="utf-8")
print(f"{agent}: {old} -> {new}")
EOF
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint
```
Expected: `researcher: a0691eedc3b2 -> e31f857727b3`, then `11 passed, 67 deselected`. That is the value the planning dry run produced from exactly this plan's text (the fingerprint hashes the module source through `inspect.getsource`, so line endings do not matter). A different value is acceptable when `git diff -- src/deep_research/agents/researcher.py` shows only this task's edits and every test this task runs passes — the snippet pins whatever the module now hashes to; do not hunt for single characters. The snippet also writes the comment above the pin, in the file's existing `Moved \`old\` -> \`new\`.` style.

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/agents/events.py src/deep_research/agents/researcher.py src/deep_research/agents/__init__.py tests/test_agents/test_researcher.py tests/test_imports.py tests/test_evaluation/test_config.py && git commit -m "feat(researcher): publish topic and tool-call events live, with coverage ids"
```

---

### Task 4: The planner publishes live and lists its sub-topics' titles (spec E3 planner rows; AC2)

**Files:**
- Modify: `src/deep_research/agents/planner.py:37` (import), `:2826-2840` (`def planning_completed_event(`), `:3111-3121` (in `run`)
- Modify: `tests/test_evaluation/test_config.py:983` (planner pin)
- Test: `tests/test_agents/test_planner.py` (append)

**Interfaces:**
- Consumes: `deep_research.agents.events.publish_live` (Task 3).
- Produces: `planner.planning.completed.metadata["sub_topics"]: list[{"coverage_id": str, "title": str}]` in plan order, each title `summarize_text(title, limit=160)` (≤ 160 characters, `…`-free: the helper ends a clamp with `...`); `[]` when the planner produced no plan. The three planner events are published live. Task 8's web handler reads `sub_topics`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_planner.py`: replace `from deep_research.agents.errors import AgentConfigurationError, PlanningError` with

```python
from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError, PlanningError
```

in the `from deep_research.agents.planner import (` list replace

```python
    plan_review_messages,
    stale_year_anchors,
```

with

```python
    plan_review_messages,
    planning_completed_event,
    stale_year_anchors,
```

replace `from deep_research.graph.state import HALTING_ERROR_TYPES, is_halted` with

```python
from deep_research.graph.live import bind_live_sink
from deep_research.graph.state import HALTING_ERROR_TYPES, is_halted
```

in the `from deep_research.utils.types import (` list replace

```python
    ResearchError,
    ResearchState,
    SubTopic,
    merge_research_state,
)
```

with

```python
    ResearchError,
    ResearchEvent,
    ResearchState,
    SubTopic,
    merge_research_state,
)
```

and append:

```python
@pytest.mark.asyncio
async def test_the_planner_publishes_its_events_live_and_lists_the_planned_titles(
    tracker: Tracker,
) -> None:
    """live-briefs spec E3 and AC2: the three planner events are published live as the
    objects the run returns, and planning.completed lists every planned sub-topic's
    coverage id and title, in plan order."""
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_plan("Cryptography", "Hardware timelines", "Mitigations"), _review()],
    )
    agent = _planner(tracker, completer)
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "q"):
        with bind_live_sink(received.append):
            outcome = await agent.run(_state())

    events = outcome.state_update["events"]
    assert [event.event_id for event in received] == [event.event_id for event in events]
    assert events[2].metadata["sub_topics"] == [
        {"coverage_id": s.coverage_id, "title": s.title} for s in outcome.result.sub_topics
    ]
    assert [s["title"] for s in events[2].metadata["sub_topics"]] == [
        "Cryptography", "Hardware timelines", "Mitigations",
    ]


def test_planning_completed_caps_each_title_at_160_characters() -> None:
    plan = ResearchPlan(
        sub_topics=[
            SubTopic(
                coverage_id="topic-01", title="Battery " * 40, rationale="r",
                search_queries=["q"], success_criteria=["c"], priority=1,
            )
        ]
    )
    outcome = AgentRun(
        agent_name="planner", result=plan,
        react=ReActRun(agent_name="planner", stop_reason="finished"),
        errors=[], state_update={},
    )

    [entry] = planning_completed_event(outcome).metadata["sub_topics"]

    assert entry["coverage_id"] == "topic-01"
    assert len(entry["title"]) <= 160
    assert entry["title"].endswith("...")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_planner.py -q -k "publishes_its_events_live or caps_each_title"
```
Expected: `2 failed` — `assert [] == [...]` and `KeyError: 'sub_topics'`.

- [ ] **Step 3: Implement**

In `src/deep_research/agents/planner.py` replace `from deep_research.agents.events import agent_event` with

```python
from deep_research.agents.events import agent_event, publish_live
```

Replace

```python
def planning_completed_event(outcome: AgentRun["ResearchPlan"]) -> ResearchEvent:
    """Report the finished plan's size and how the scoping loop stopped."""
    plan = outcome.result
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": 0 if plan is None else len(plan.sub_topics),
```

with

```python
def planning_completed_event(outcome: AgentRun["ResearchPlan"]) -> ResearchEvent:
    """Report the finished plan's size, its sub-topics and how the scoping loop stopped.

    ``sub_topics`` lists each planned sub-topic's ``coverage_id`` and title, the
    title capped at 160 characters (live-briefs spec AC2): plan content a console
    shows the reader, never provider error text (``agents/events.py``).
    """
    plan = outcome.result
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": 0 if plan is None else len(plan.sub_topics),
            "sub_topics": []
            if plan is None
            else [
                {
                    "coverage_id": sub_topic.coverage_id,
                    "title": summarize_text(sub_topic.title, limit=160),
                }
                for sub_topic in plan.sub_topics
            ],
```

In `run`, replace

```python
        events = [
            planning_started_event(state),
            memory_recalled_event(state.memory_context),
        ]
        try:
```

with

```python
        events = [
            planning_started_event(state),
            memory_recalled_event(state.memory_context),
        ]
        # Published live (live-briefs spec E3); the same objects are returned below.
        for event in events:
            publish_live(event)
        try:
```

and replace `        events.append(planning_completed_event(outcome))` with

```python
        completed = planning_completed_event(outcome)
        publish_live(completed)
        events.append(completed)
```

- [ ] **Step 4: Run the planner tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py -q
```
Expected: `291 passed`, `0 failed` (observed in planning).

- [ ] **Step 5: Re-pin the planner's fingerprint**

The same snippet as Task 3 Step 6, with this task's arguments:

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - planner 9c5745683d9b "Live briefs (live-briefs spec E3 and AC2, 2026-09-28): planner.planning.completed lists each sub-topic's coverage_id and title (at most 160 characters), and the three planner events are published live. Module code only; no prompt string changed and agents.prompts was untouched, so the other four target pins and the Judge pin are unchanged." <<'EOF'
import sys
import textwrap
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

agent, old, reason = sys.argv[1:4]
new = agent_prompt_fingerprint(agent)
path = Path("tests/test_evaluation/test_config.py")
text = path.read_text(encoding="utf-8")
anchor = f'    "{agent}": "{old}",\n'
assert text.count(anchor) == 1, f"pin {agent}={old} not found exactly once"
comment = textwrap.fill(
    f"{reason} Moved `{old}` -> `{new}`.", width=78,
    initial_indent="    # ", subsequent_indent="    # ",
)
path.write_text(text.replace(anchor, f'{comment}\n    "{agent}": "{new}",\n'), encoding="utf-8")
print(f"{agent}: {old} -> {new}")
EOF
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint
```

Expected: `planner: 9c5745683d9b -> d40ffac43845`, then `11 passed, 67 deselected` — the planning dry run's value. A different value is acceptable when `git diff -- src/deep_research/agents/planner.py` shows only this task's edits and every test this task runs passes; do not hunt for single characters.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/planner.py tests/test_agents/test_planner.py tests/test_evaluation/test_config.py && git commit -m "feat(planner): publish planning events live and list the planned titles"
```

---

### Task 5: The evaluator, verifier and writer publish live (spec E3 remaining rows)

**Files:**
- Modify: `src/deep_research/agents/source_evaluator.py:32`, `:1355-1383`; `src/deep_research/agents/evidence_verifier.py:46`, `:1006-1013`; `src/deep_research/agents/report_writer.py:37`, `:3337-3357`
- Modify: `tests/test_evaluation/test_config.py:1139`, `:1214`, `:1309` (three pins)
- Test: `tests/test_agents/test_source_evaluator.py`, `test_evidence_verifier.py`, `test_report_writer.py` (append)

**Interfaces:**
- Consumes: `deep_research.agents.events.publish_live` (Task 3).
- Produces: `source_evaluator.evaluation.started` / `.completed`, `evidence_verifier.verification.completed` and `report_writer.report.written` published live, each the object returned in `state_update["events"]`. No metadata changes.

- [ ] **Step 1: Write the failing tests**

In each of the three test files insert `from deep_research.graph.live import bind_live_sink` directly above its line `from deep_research.memory.scratchpad import ScratchpadMemory` (`test_evidence_verifier.py` has it at `:44`), and add `ResearchEvent,` to its `from deep_research.utils.types import (` list — directly above `    ResearchState,` in each (`test_source_evaluator.py:55`, `test_evidence_verifier.py:59`, `test_report_writer.py:57`). Then append:

`tests/test_agents/test_source_evaluator.py`:

```python
@pytest.mark.asyncio
async def test_the_evaluation_events_are_published_live(tracker: Tracker) -> None:
    """live-briefs spec E3: both evaluation events, live, as the objects returned."""
    agent = _evaluator(tracker, ScriptedCompleter())
    state = _eval_state([])
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", state.original_question):
        with bind_live_sink(received.append):
            outcome = await agent.run(state)

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "source_evaluator.evaluation.started",
        "source_evaluator.evaluation.completed",
    ]
    assert [event.event_id for event in received] == [event.event_id for event in events]
```

`tests/test_agents/test_evidence_verifier.py`:

```python
@pytest.mark.asyncio
async def test_the_verification_completed_event_is_published_live(tracker: Tracker) -> None:
    """live-briefs spec E3: verification.completed, live, as the object returned."""
    read = make_read()
    finding = make_finding(read, SNIPPET)
    agent = _evidence_verifier(tracker, ScriptedCompleter())
    state = _state(raw_findings=[finding], read_records={read.read_id: read})
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "question"):
        with bind_live_sink(received.append):
            outcome = await agent.run(state)

    [event] = outcome.state_update["events"]
    assert event.event_type == "evidence_verifier.verification.completed"
    assert [e.event_id for e in received] == [event.event_id]
```

`tests/test_agents/test_report_writer.py`:

```python
@pytest.mark.asyncio
async def test_the_report_written_event_is_published_live(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """live-briefs spec E3: report.written, live, as the object returned."""
    def route(messages, schema):
        del messages, schema
        raise ProviderResponseError(
            "provider returned an HTTP error", retryable=True,
            failure_category="http", http_status_code=503, failure_origin="sdk",
        )

    state = _one_part_state()
    agent = _writer(tracker, ScriptedCompleter(outputs=[route] * 10), report_writer_tools(tracker, output_root=tmp_path))
    received: list[ResearchEvent] = []

    async with tracker.session_span(state.session_id, state.original_question):
        with bind_live_sink(received.append):
            run = await agent.run(state)

    [written] = run.state_update["events"]
    assert written.event_type == "report_writer.report.written"
    assert [event.event_id for event in received] == [written.event_id]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_source_evaluator.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_report_writer.py -q -k "published_live"
```
Expected: `3 failed`, each `assert [] == [...]`.

- [ ] **Step 3: Implement**

In each of the three modules replace `from deep_research.agents.events import agent_event` with `from deep_research.agents.events import agent_event, publish_live`.

`source_evaluator.py` `run`: replace

```python
        events: list[ResearchEvent] = [
            evaluation_started_event(
                finding_count=len(state.raw_findings),
                source_count=len(task.groups),
            )
        ]
```

with

```python
        events: list[ResearchEvent] = [
            evaluation_started_event(
                finding_count=len(state.raw_findings),
                source_count=len(task.groups),
            )
        ]
        publish_live(events[0])  # live-briefs spec E3; returned below as well
```

and replace

```python
            snapshot = self._snapshot(sources)
            events.append(
                evaluation_completed_event(
                    snapshot,
                    reputation_hits=hits,
                    reputation_failures=failures,
                )
            )
```

with

```python
            snapshot = self._snapshot(sources)
            completed = evaluation_completed_event(
                snapshot,
                reputation_hits=hits,
                reputation_failures=failures,
            )
            publish_live(completed)
            events.append(completed)
```

`evidence_verifier.py` `run`: replace

```python
        react = ReActRun(agent_name=self.name, stop_reason="finished", errors=errors)
        return AgentRun(
            agent_name=self.name, result=VerifiedFindings(findings=judged), react=react,
            errors=errors,
            state_update={"verified_findings": snapshot, "errors": errors,
                          "events": [evidence_verified_event(judged)]},
```

with

```python
        react = ReActRun(agent_name=self.name, stop_reason="finished", errors=errors)
        completed = evidence_verified_event(judged)
        publish_live(completed)  # live-briefs spec E3; returned below as well
        return AgentRun(
            agent_name=self.name, result=VerifiedFindings(findings=judged), react=react,
            errors=errors,
            state_update={"verified_findings": snapshot, "errors": errors,
                          "events": [completed]},
```

`report_writer.py` `run`: replace

```python
        react = ReActRun(
            agent_name=self.name,
            stop_reason=stop_reason,
            errors=list(composition.errors),
        )
        return AgentRun(
```

with

```python
        react = ReActRun(
            agent_name=self.name,
            stop_reason=stop_reason,
            errors=list(composition.errors),
        )
        written = report_written_event(result)
        publish_live(written)  # live-briefs spec E3; returned below as well
        return AgentRun(
```

and replace

```python
                "errors": list(composition.errors),
                "events": [report_written_event(result)],
```

with

```python
                "errors": list(composition.errors),
                "events": [written],
```

(`state_update()` at `:3252-3266` — the `BaseAgent` hook `run` never calls — is left unchanged.)

- [ ] **Step 4: Run the three agents' tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_source_evaluator.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_report_writer.py tests/test_imports.py -q
```
Expected: `382 passed`, `0 failed` (observed in planning).

- [ ] **Step 5: Re-pin the three fingerprints**

The same snippet as Task 3 Step 6, run once per agent through a shell function, then the pin tests once:

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
repin() {
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - "$@" <<'EOF'
import sys
import textwrap
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

agent, old, reason = sys.argv[1:4]
new = agent_prompt_fingerprint(agent)
path = Path("tests/test_evaluation/test_config.py")
text = path.read_text(encoding="utf-8")
anchor = f'    "{agent}": "{old}",\n'
assert text.count(anchor) == 1, f"pin {agent}={old} not found exactly once"
comment = textwrap.fill(
    f"{reason} Moved `{old}` -> `{new}`.", width=78,
    initial_indent="    # ", subsequent_indent="    # ",
)
path.write_text(text.replace(anchor, f'{comment}\n    "{agent}": "{new}",\n'), encoding="utf-8")
print(f"{agent}: {old} -> {new}")
EOF
}
repin source_evaluator 0d96c48eae29 "Live briefs (live-briefs spec E3, 2026-09-28): both evaluation events are published live. Module code only; no prompt string changed and agents.prompts was untouched."
repin evidence_verifier 0dc169302934 "Live briefs (live-briefs spec E3, 2026-09-28): verification.completed is published live. Module code only; no prompt string changed and agents.prompts was untouched."
repin report_writer ee5199095402 "Live briefs (live-briefs spec E3, 2026-09-28): report.written is published live. Module code only; no prompt string changed and agents.prompts was untouched."
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint
```

Expected: `source_evaluator: 0d96c48eae29 -> fb7f60d73873`, `evidence_verifier: 0dc169302934 -> 4a3d56fab932`, `report_writer: ee5199095402 -> 0dcd4a41a378` (the planning dry run's values; a different value is acceptable when `git diff` of that module shows only this task's edits and every test this task runs passes — do not hunt for single characters), then `11 passed, 67 deselected`.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py src/deep_research/agents/evidence_verifier.py src/deep_research/agents/report_writer.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_report_writer.py tests/test_evaluation/test_config.py && git commit -m "feat(agents): evaluator, verifier and writer publish their events live"
```

---

### Task 6: The replay proof, the regenerated captures and the delivery docs (spec E4, E5; AC1; the findings-sum inference)

**Files:**
- Test: `tests/test_api/test_replay.py` (append)
- Regenerate: `web/test/fixtures/events/missing-target-triggers-one-extra-pass.json`, `web/test/fixtures/events/scoped-redraft-after-a-named-defect.json`
- Modify: `docs/design/api-gaps.md:13-14`, `:50-54`, `:73` (Closed table), `:106`; `docs/design/DESIGN.md:716-724`, `:747-750`, `:1531-1540`, `:1599`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: the two captures Tasks 8–10 test against — every event carries `event_id`; `researcher.sub_topic.*` carry `coverage_id`; `planner.planning.completed` carries `sub_topics`; topics interleave as they ran concurrently. Event counts are unchanged: 72 and 49.

- [ ] **Step 1: Verification tests on the real graph**

In `tests/test_api/test_replay.py` replace the import line

```python
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
```

with

```python
from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS, scenario_by_id
from deep_research.utils.types import ResearchEvent
```

replace

```python
from tests.test_api.replay_support import EXTRA_PASS_CASE, REVIEW_UNAVAILABLE_CASE, guarded
```

with

```python
from tests.test_api.replay_support import EXTRA_PASS_CASE, REVIEW_UNAVAILABLE_CASE, guarded, replay_outcome
```

(`ReplayRunner`, `production_config_path`, `scenario_by_id`, `EXTRA_PASS_CASE` and `guarded` are already imported there), and append:

```python
@pytest.mark.asyncio
async def test_the_replay_runner_delivers_every_event_once_inside_its_own_node(tmp_path: Path) -> None:
    """live-briefs spec E4 and AC1 on the real graph: the paced queue receives live
    events through the same handler; every event arrives once; each agent's events
    arrive between its node's graph.node.started and graph.node.completed; and each
    researcher.tool_call arrives after its topic's started event and before its
    completed event, although the topics run concurrently."""
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.0, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    ids = [event.event_id for event in received]
    assert len(ids) == len(set(ids))
    assert set(ids) == {event.event_id for event in outcome.state.events}
    open_node: str | None = None
    for event in received:
        if event.event_type == "graph.node.started":
            assert open_node is None, event.metadata
            open_node = event.metadata["node"]
        elif event.event_type == "graph.node.completed":
            assert open_node == event.metadata["node"]
            open_node = None
        elif event.source.startswith("agent."):
            assert open_node == event.source.removeprefix("agent."), event.event_type
    started_at: dict[str, int] = {}
    for index, event in enumerate(received):
        if event.event_type == "researcher.sub_topic.started":
            started_at[event.metadata["sub_topic"]] = index
        if event.event_type == "researcher.tool_call":
            title = event.metadata["sub_topic"]
            assert started_at[title] < index
            assert any(
                later.event_type == "researcher.sub_topic.completed"
                and later.metadata["sub_topic"] == title
                for later in received[index + 1:]
            )


@pytest.mark.parametrize("case_id", REPLAY_CASE_IDS)
def test_topic_findings_sum_matches_research_total(case_id: str, tmp_path: Path) -> None:
    """The spec's §4.3 inference: over every replay case, the findings_retained of a
    pass's completed topics sum to that pass's researcher.research.completed.findings,
    so the Researching subtitle's running total lands on the pass total."""
    outcome = replay_outcome(case_id, tmp_path)
    passes = 0
    retained = 0
    for event in outcome.state.events:
        if event.event_type == "graph.node.started" and event.metadata.get("node") == "researcher":
            retained = 0
        elif event.event_type == "researcher.sub_topic.completed":
            retained += event.metadata["findings_retained"]
        elif event.event_type == "researcher.research.completed":
            assert retained == event.metadata["findings"]
            passes += 1
    if passes == 0:
        pytest.skip(f"{case_id} completes no research pass, so it has no total to compare")
```

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_replay.py -q
```
Expected: `41 passed`, `0 failed`; the two new tests contribute `36` of them (1 + the 35 cases of `REPLAY_CASE_IDS`; observed in planning). If the findings-sum test fails for a case, stop and report the case: per spec §4.3 the Researching subtitle then must use the research-completed total only, which Task 8 would change.

- [ ] **Step 2: Regenerate the web captures**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 node web/scripts/launch.mjs start api 8010 /research "\"$PY\" -m deep_research.api --mode replay --port 8010 --replay-delay-ms 0"
cd web && DEEP_RESEARCH_API_URL=http://127.0.0.1:8010 node scripts/capture-replay-events.mjs missing-target-triggers-one-extra-pass scoped-redraft-after-a-named-defect; cd ..
if [ "$(uname -s)" = "Linux" ]; then kill -TERM -- "-$(cat .launch-api.pid)"; fi
node web/scripts/launch.mjs stop api 8010
```
Expected: `up: http://127.0.0.1:8010/research …`, then `test\fixtures\events\missing-target-triggers-one-extra-pass.json: 72 events, completed/1` and `test\fixtures\events\scoped-redraft-after-a-named-defect.json: 49 events, completed/0` (`test/fixtures/events/…` on Linux), then `stopped: port 8010 refuses connections …`. On Linux the `kill` line has already ended the server's process group, so `stop` first reports that `taskkill` failed and then `stopped: port 8010 refuses connections (pid … was already gone)`. A `FAILED:` line means a server survived: find its owner as the message says before going on.

- [ ] **Step 3: Check the captures carry the new fields and the old event core still reads them**

```bash
cd web && node -e "for (const c of ['missing-target-triggers-one-extra-pass','scoped-redraft-after-a-named-defect']) { const r = require('./test/fixtures/events/' + c + '.json'); const ids = new Set(r.events.map((e) => e.event_id)); const plan = r.events.find((e) => e.event_type === 'planner.planning.completed').metadata.sub_topics; const topics = r.events.filter((e) => e.event_type.startsWith('researcher.sub_topic.')); console.log(c, r.events.length, ids.size === r.events.length && !ids.has(undefined), plan.length, topics.every((e) => typeof e.metadata.coverage_id === 'string')); }" && npx vitest run test/run-state.test.ts
```
Expected: `missing-target-triggers-one-extra-pass 72 true 3 true`, `scoped-redraft-after-a-named-defect 49 true 2 true`, then `Test Files 1 passed (1)` / `Tests 11 passed (11)` — `test/run-state.test.ts` is the one Vitest file that reads the captures, and the unchanged event core is burst-safe under the new order (observed in planning). The whole Vitest suite runs first in Task 7, after Task 7 fixes a pre-existing race in `session-screen.test.tsx` that can fail it (see there).

- [ ] **Step 4: Docs — api-gaps.md**

In `docs/design/api-gaps.md` replace `E1, 1.1 and 1.2 are closed` with `E1, 1.1, 1.2 and 3.7 are closed`.

Replace the paragraph

```markdown
**Delivery model.** Events reach the stream once per node step: each
`stream_mode="values"` snapshot publishes the events that superstep appended
(`graph/orchestrator.py:312-346`), so a node's `graph.node.started`, everything
it emitted and its `graph.node.completed` arrive together when the node finishes.
Every running-stage rule in DESIGN.md §3.5 and §5.7 is written for that.
```

with

```markdown
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
```

Add this row as the last row of the "Closed since 2026-09-16" table (after the `/status.iteration` row):

```markdown
| 3.7 | live per-event delivery | closed 2026-09-28 (live-briefs spec E1–E3): every `ResearchEvent` carries an `event_id`; `agent_node` and the agents publish progress live through the run's sink (`graph/live.py`), and the snapshot loop skips ids already published, so each event is delivered once (`graph/orchestrator.py`) |
```

Delete the Stage 3 row that begins `| 3.7 | **Live per-event delivery** (new) |` (`:106`).

- [ ] **Step 5: Docs — DESIGN.md (delivery only; Task 11 does the running stage)**

Replace

```markdown
each step holds its highlight for as long as it actually runs; on the real stream
a node's events arrive together, once per node step, when the node finishes
(`graph/orchestrator.py:312-346`). The prototype cannot reproduce either, so it
```

with

```markdown
each step holds its highlight for as long as it actually runs; on the real stream
each event arrives as it happens (§5.7). The prototype cannot reproduce either, so it
```

Replace

```markdown
**The active row is derived, and it is derived from completions.** On the real
stream a node's `graph.node.started` arrives only when the node has already
finished, in the same burst as its own `graph.node.completed`, so the started
event cannot mark the running row. The "Now" row is the **successor of the last
```

with

```markdown
**The active row is derived, and it is derived from completions.** A node's
`graph.node.started` now arrives live when the node starts (§5.7), but a reconnect
replays the whole log as one burst and the reviewer's own start arrives with its
snapshot, so completions stay the rule and read the same either way. The "Now" row is the **successor of the last
```

Replace the paragraph that begins `**Delivery is once per node step.**` and ends `the API work.` with

```markdown
**Delivery is live.** Each event reaches the stream as it happens: `graph.node.started`
when a node starts, and each agent's progress events as the agent builds them — the
researcher's `researcher.sub_topic.started`, `researcher.tool_call` (built when its
step's observation is recorded) and `researcher.sub_topic.completed` while its topics
run, concurrently. Events that are not published live — the graph's route, review,
hop and completion events, and `researcher.research.completed` — arrive with their
node's snapshot, and an id already published live is never published twice
(`graph/live.py`, `graph/orchestrator.py`; api-gaps 3.7, closed). The screen therefore
moves within a node: the Researching checklist ticks topics off as they finish. Every
derivation above is still written so the state after event *k* depends only on events
1..*k*: a live run, a 100-event replay and a reconnect's burst paint the same screen.
```

In §6's table, in the `| Running |` row delete `events arrive once per node step, not live per event; `.

- [ ] **Step 6: Run the whole backend suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q
```
Expected: `4785 passed, 1 deselected` (the baseline's 4726 plus the 59 tests Tasks 1–6 add: 4 + 8 + 6 + 2 + 3 + 36), `0 failed`; without a `.env` (Linux, a worktree) run it with the `--deselect` of Conventions and expect `4784 passed, 2 deselected`. Also `git diff 196dd3f1 -- src/deep_research/agents/prompts.py` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add tests/test_api/test_replay.py web/test/fixtures/events docs/design/api-gaps.md docs/design/DESIGN.md && git commit -m "test(replay): live delivery on the real graph; regenerate the live captures; close api-gaps 3.7"
```

---

### Task 7: The removals, the chip and the pass fact (spec §4.2; D12–D15; AC3, AC8)

**Files:**
- Modify: `web/lib/format.ts:1-3`, `:6-20`, `:31-50`, `:70`; `web/lib/api.ts:5-10`, `:34-36`; `web/lib/session-store.ts:4`; `web/lib/run-state.ts:88` (after `plural`)
- Modify: `web/components/RunningPipeline.tsx` (whole file), `SessionScreen.tsx:7`, `:32`, `:85-86`, `:108-110`, `:140-145`, `:176`, `:180`, `:183`; `Composer.tsx:21-28`, `:124`; `SettingsPopover.tsx:6`, `:80-91`; `SettingsStrip.tsx:4-19`; `ReportRail.tsx:3`, `:16-17`, `:54`; `ReportStage.tsx:4`, `:13`, `:70-72`, `:101`
- Test: `web/test/format.test.ts`, `web/test/api.test.ts:7`, `web/test/run-state.test.ts`, `web/test/components/running-pipeline.test.tsx` (whole file), `status-chip.test.tsx`, `composer.test.tsx`, `report-rail.test.tsx`, `report-stage.test.tsx`, `session-screen.test.tsx:80-81` (a pre-existing race)
- E2E: `web/e2e/running.spec.ts:20`, `:29`; `arcs.spec.ts:4-11`, `:18-24`; `report.spec.ts:17`; `visual.spec.ts:31`; create `web/e2e/settings.spec.ts`

**Interfaces:**
- Consumes: nothing new from the backend (the API still accepts `max_iterations`).
- Produces: `SessionView { status, iteration, step: string | null, review, coverage }`; `toSessionView(s, step: string | null = null)`; `statusNote(view)` → `view.step ?? "starting"` while running; `passFact(iteration: number, notePasses = 0): string`; `stepLabel(node: string | null | undefined): string | null`; `chipStep(run: RunState): string | null`; `SubmittedSettings { model, thinking, outputDir }`; `ResearchRequest` without `max_iterations`; `ResearchEvent` with `event_id: string` (E1); `SettingsStrip({ settings, id })`; `ReportRail({ status, evidence })`; `ReportStage({ sessionId, status, strip })`; `RunningPipeline({ sessionId, run, question, strip, startedAt })` (Task 9 adds `onToggleRow`). Removed: `passNumber`, `passTotal`, `passText`, `EXTRA_MIN`, `EXTRA_MAX`, `#pillExtra`, `#stepExtra`, `.pipe-now` and `<Counters>` in the running stage.

- [ ] **Step 1: Write the failing unit tests**

`web/test/format.test.ts`: in the import, replace `passText` with `passFact`. Replace the first test of `describe("statusNote — one rule per API status", …)` with

```ts
  it("running names the active step and never a pass (live-briefs spec §4.2)", () => {
    expect(statusNote(toSessionView(base, "Researching"))).toBe("Researching");
    expect(statusNote(toSessionView(base))).toBe("starting");
    expect(statusNote(toSessionView({ ...base, iteration: 1 }, "Writing report"))).toBe("Writing report");
  });
```

replace the test `it("passText adds one to the zero-based iteration and drops the clause without a ceiling", …)` with

```ts
  it("passFact says the passes in plain words (live-briefs spec §4.2 table)", () => {
    expect(passFact(0)).toBe("One research round");
    expect(passFact(1)).toBe("Went back once to fill gaps");
    expect(passFact(2)).toBe("Went back twice to fill gaps");
    expect(passFact(3)).toBe("Went back 3 times to fill gaps");
    expect(passFact(0, 1)).toBe("One research round · went back once for your notes");
    expect(passFact(1, 2)).toBe("Went back once to fill gaps · went back twice for your notes");
    expect(passFact(2, 4)).toBe("Went back twice to fill gaps · went back 4 times for your notes");
  });
```

and in every other `toSessionView(<arg>, <number>)` call of the file (`:20`, `:24-26`, `:30`, `:34`, `:37`, `:56-58` today) delete the second argument, e.g. `toSessionView(s, 2)` → `toSessionView(s)` and `toSessionView({ ...s, coverage: coverage([]) }, 2)` → `toSessionView({ ...s, coverage: coverage([]) })`.

`web/test/api.test.ts:7`: replace `const request = { query: "q", max_iterations: 1, output_format: "markdown" as const, config_overrides: {} };` with `const request = { query: "q", output_format: "markdown" as const, config_overrides: {} };`.

`web/test/components/status-chip.test.tsx`: replace the `view` helper with

```ts
const view = (over: Partial<SessionView>): SessionView => ({ status: "running", iteration: 0, step: "Researching", review: null, coverage: null, ...over });
```

and replace

```ts
    expect(text(container)).toBe("Running · pass 1 of 2");
    expect(container.querySelector(".dot")!.className).toContain("dot-live");
```

with

```ts
    expect(text(container)).toBe("Running · Researching");
    expect(container.querySelector(".dot")!.className).toContain("dot-live");
    rerender(<StatusChip view={view({ step: null })} />);
    expect(text(container)).toBe("Running · starting");
```

`web/test/components/composer.test.tsx`: replace the test `it("builds the request the design specifies", …)` with

```tsx
  it("builds the request the design specifies, with no max_iterations (live-briefs spec §4.2, AC8)", () => {
    const body = buildRequest("  q  ", DEFAULT_SETTINGS);
    expect(body).toEqual({
      query: "q", output_format: "markdown",
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    });
    expect("max_iterations" in body).toBe(false);
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, outputDir: "" }).config_overrides).toEqual({ llm: { model: "deepseek-flash", thinking_mode: "enabled" } });
  });
  it("offers no extra-passes control: no pill and no stepper in the popover (AC8)", () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const { container } = render(<ConsoleProvider><Composer /></ConsoleProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Run settings" }));
    expect(container.querySelector("#pillExtra")).toBeNull();
    expect(document.querySelector("#stepExtra")).toBeNull();
    expect(document.querySelector("#settingsPop")!.textContent).not.toMatch(/extra pass/i);
  });
```

`web/test/components/report-rail.test.tsx`: replace `render(<ReportRail status={base} evidence={null} passes={2} />)` with `render(<ReportRail status={base} evidence={null} />)`; replace `expect(container.querySelector("#repFactPass")!.textContent).toBe("pass 1 of 2");` with `expect(container.querySelector("#repFactPass")!.textContent).toBe("One research round");`; replace `render(<ReportRail status={status} evidence={evidence} passes={2} />)` with `render(<ReportRail status={{ ...status, iteration: 1 }} evidence={evidence} />)`; and after `expect(container.querySelector("#repEvidenceCounts")!.textContent).toContain("4 network · 1 cache");` add `expect(container.querySelector("#repFactPass")!.textContent).toBe("Went back once to fill gaps");`.

`web/test/components/report-stage.test.tsx`: delete ` passes={2}` from all seven `render(<ReportStage … />)` calls, and directly above `describe("ReportStage — q-center (controller ruling 1)", () => {` add

```tsx
describe("ReportStage — the head bar states the passes in plain words (live-briefs spec §4.2, D15)", () => {
  it("ends the meta line with the pass fact, never a pass counter", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: { code: "session_not_complete", message: "not complete", reason: null, issues: [] } })));
    render(<ReportStage sessionId="s1" status={STATUS} strip={null} />);
    const meta = document.getElementById("reportMeta")!.textContent!;
    expect(meta.endsWith(" · Went back once to fill gaps")).toBe(true);
    expect(meta).not.toMatch(/\bpass\b/);
  });
});

```

`web/test/components/running-pipeline.test.tsx` — replace the whole file with

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { newRunState } from "../../lib/run-state";

describe("RunningPipeline — the removals (live-briefs spec §4.2, AC3)", () => {
  it("has no Now header, no counters block and no pass text; the card holds the spine", () => {
    const run = newRunState(2);
    run.active = "researcher";
    run.marks = { planner: "done" };
    const { container } = render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" />);
    for (const gone of [".pipe-now", "#runNow", "#runPasses", "#runLoopTag", "#runTrack", "#runCounters", ".counters"]) expect(container.querySelector(gone)).toBeNull();
    expect(container.querySelector("#stage-running")!.textContent).not.toMatch(/\bpass\b/i);
    expect(container.querySelectorAll("#spine li[data-stage]")).toHaveLength(7);
    expect(container.querySelector('#spine li[data-stage="researcher"]')!.getAttribute("data-state")).toBe("active");
  });
});
```

`web/test/components/session-screen.test.tsx` — a pre-existing race, fixed first because this task is the first to expect the whole Vitest suite green: the C1 test asserts the unreachable banner in the same tick as the loading stage (`:80-81`), and under full-suite load the banner can land a tick later (observed in planning: 1 of the 117 tests failed at `7afb21de` in a full run, and 2 of 4 full runs failed once `BriefSpine` joined the module graph; the file alone passes). Replace

```tsx
    await waitFor(() => expect(document.getElementById("stage-loading")).toBeTruthy());
    expect(document.querySelector('[role="alert"]')).toBeTruthy();
```

with

```tsx
    await waitFor(() => expect(document.getElementById("stage-loading")).toBeTruthy());
    // The banner follows the first 502 — waited for, not assumed to have landed in the same tick.
    await waitFor(() => expect(document.querySelector('[role="alert"]')).toBeTruthy());
```

`web/test/run-state.test.ts`: in the import from `../lib/run-state` add `chipStep, stepLabel`, and append:

```ts
describe("the chip's step (live-briefs spec §4.2)", () => {
  it("stepLabel names the row a node runs on; hops read as the row they lead back to", () => {
    expect(stepLabel("researcher")).toBe("Researching");
    expect(stepLabel("finalize_report")).toBe("Publishing");
    expect(stepLabel("extra_pass")).toBe("Researching");
    expect(stepLabel("writer_redraft")).toBe("Writing report");
    expect(stepLabel("graph")).toBeNull();
    expect(stepLabel(null)).toBeNull();
  });
  it("chipStep follows the active row, then the row the run ended on", () => {
    expect(chipStep(newRunState(2))).toBe("planner");
    const ended = replayRun(extraPass.events, P(extraPass));
    expect(ended.active).toBeNull();
    expect(chipStep(ended)).toBe("finalize_report");
    const halted = newRunState(2);
    applyEvent(halted, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
    applyEvent(halted, { type: "graph.node.skipped", metadata: { node: "researcher", iteration: 0, reason: "halted" } });
    applyEvent(halted, { type: "graph.session.completed", metadata: { status: "failed", iteration: 0, error_count: 1, has_report: false } });
    expect(chipStep(halted)).toBe("researcher");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npm run -s typecheck; npx vitest run
```
Expected: `typecheck` reports errors, among them `Module '"../lib/format"' has no exported member 'passFact'`, `Module '"../lib/run-state"' has no exported member 'stepLabel'` and `Property 'passes' is missing in type …`; Vitest reports `Test Files  7 failed | 12 passed (19)` / `Tests  11 failed | 109 passed (120)`, the failing files being `format.test.ts`, `run-state.test.ts`, `status-chip.test.tsx`, `composer.test.tsx`, `report-rail.test.tsx`, `report-stage.test.tsx`, `running-pipeline.test.tsx` (observed in planning).

- [ ] **Step 3: `lib/format.ts`, `lib/api.ts`, `lib/session-store.ts`, `lib/run-state.ts`**

`web/lib/format.ts`: in the header replace `API's flat fields to the prototype's session shape so statusNote/passText keep their bodies.` with `API's flat fields to the prototype's session shape so statusNote keeps its body.` Replace

```ts
export interface SessionView {
  status: SessionStatus;
  iteration: number;
  passes: number | null;
  review: { status: string | null; score: number | null } | null;
  coverage: CoverageProgress | null;
}

export function toSessionView(s: ResearchSessionResponse, passes: number | null): SessionView {
  const review =
    s.semantic_review_status === null && s.semantic_review_score === null
      ? null
      : { status: s.semantic_review_status, score: s.semantic_review_score };
  return { status: s.status, iteration: s.iteration, passes, review, coverage: s.coverage };
}
```

with

```ts
export interface SessionView {
  status: SessionStatus;
  iteration: number;
  /* The running row's label (live-briefs spec §4.2: "Running · {step}"); null when not known. */
  step: string | null;
  review: { status: string | null; score: number | null } | null;
  coverage: CoverageProgress | null;
}

export function toSessionView(s: ResearchSessionResponse, step: string | null = null): SessionView {
  const review =
    s.semantic_review_status === null && s.semantic_review_score === null
      ? null
      : { status: s.semantic_review_status, score: s.semantic_review_score };
  return { status: s.status, iteration: s.iteration, step, review, coverage: s.coverage };
}
```

Delete `passNumber` and `passTotal` with their comments (`:31-41`, from `/* \`iteration\` is the API's zero-based value;` through `passTotal`'s closing brace). Replace

```ts
export function passText(s: SessionView): string {
  const total = passTotal(s);
  return "pass " + passNumber(s.iteration) + (total === null ? "" : " of " + total);
}
```

with

```ts
/* The report's pass fact in plain words (live-briefs spec §4.2, D15): the extra research passes the
   run took (`iteration`, zero-based), then the passes it took for the reader's notes (`note_passes`,
   added in Phase 3 — 0 until then). */
const times = (n: number) => (n === 1 ? "once" : n === 2 ? "twice" : n + " times");
export function passFact(iteration: number, notePasses = 0): string {
  const research = iteration > 0 ? "Went back " + times(iteration) + " to fill gaps" : "One research round";
  return notePasses > 0 ? research + " · went back " + times(notePasses) + " for your notes" : research;
}
```

and replace `    default: return passText(s);` with `    default: return s.step ?? "starting";`.

`web/lib/api.ts`: replace

```ts
export interface ResearchRequest {
  query: string;
  max_iterations: number | null;
  output_format: "markdown";
```

with

```ts
/* No `max_iterations`: the console never sends one, so the API uses the configured extra-pass
   budget (live-briefs spec §4.2, D15). The API itself still accepts the field. */
export interface ResearchRequest {
  query: string;
  output_format: "markdown";
```

and replace

```ts
export interface ResearchEvent {
  event_type: string; source: string; message: string; timestamp: string; metadata: Record<string, unknown>;
}
```

with

```ts
/* `event_id` is the event's identity (live-briefs spec E1); the web app does not read it yet. */
export interface ResearchEvent {
  event_type: string; source: string; message: string; timestamp: string; metadata: Record<string, unknown>; event_id: string;
}
```

(the type is only ever cast from parsed JSON — `lib/stream.ts:63`, the captures in the tests — so no literal needs the new field).

`web/lib/session-store.ts:4`: replace `extraPasses: number; ` with nothing, so the line reads `export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; outputDir: string }`.

`web/lib/run-state.ts`: after the line `export function plural(n: number, one: string, many: string): string { return n + " " + (n === 1 ? one : many); }` add

```ts
/* The label of the row a node runs on, for the chip's "Running · {step}" (live-briefs spec §4.2).
   The two hops lead back into a row, so they read as that row; anything else is not a row. */
const HOP_ROW: Readonly<Record<string, NodeId>> = { extra_pass: "researcher", writer_redraft: "report_writer" };
export function stepLabel(node: string | null | undefined): string | null {
  if (!node) return null;
  const id = HOP_ROW[node] ?? node;
  return STAGES.find((s) => s.id === id)?.label ?? null;
}
/* The node the chip names while the stream is open: the active row; once graph.session.completed
   has arrived (and until /status turns terminal), the row the run ended on — Publishing, or the
   node that halted. */
export function chipStep(run: RunState): string | null {
  if (run.active) return run.active;
  if (run.finalStatus === null || run.finalStatus === "failed") return run.openNode;
  return "finalize_report";
}
```

- [ ] **Step 4: The components**

`web/components/RunningPipeline.tsx` — replace the whole file with

```tsx
"use client";
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { fmtElapsed, qFitClass } from "@/lib/format";
import { noteRunningLayout } from "@/lib/handoff";
import { marksFor, type RunState } from "@/lib/run-state";
import { Spine } from "./Spine";

/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone. The row's accessible name (" (in progress)") stays the
   non-colour state signal. */
export function RunningPipeline({ sessionId, run, question, strip, startedAt }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  // Noted on every render while this stage is mounted, so the rects are as fresh as the moment
  // status flips to a terminal one allows — see enterReport/runReportSlide (ReportStage.tsx),
  // ported from REPORT_HANDOFF (index.html:3195) / DESIGN.md:1453-1483.
  useLayoutEffect(() => {
    const q = document.getElementById("running-h");
    const o = document.getElementById("runningOpts");
    if (q && o) noteRunningLayout(sessionId, q, o);
  });
  return (
    // no-enter + is-arriving (index.html:2440, :3797, DESIGN.md:1438-1451 "submitted → running"):
    // both stages share the same header offset inside the same .run-wrap, so the seam is held
    // still — the generic slide is suppressed and only the card below the header rises in.
    <section className="stage is-on no-enter is-arriving" id="stage-running" aria-labelledby="running-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session running</p>
          <h1 className={"ask-q ask-locked" + qFitClass(question)} id="running-h" aria-describedby="runningOpts">{question}</h1>
          {strip}
          <div className="ask-meta"><span className="avail-mono" id="runElapsed">{fmtElapsed(elapsed)} elapsed</span></div>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <Spine marks={marksFor(run, run.active)} run={run} withArcs />
        </div>
      </div>
    </section>
  );
}
```

`web/components/SessionScreen.tsx`:
- `:7` — replace `import { applyEvent, marksFor, newRunState, toRunEvent, type RunState } from "@/lib/run-state";` with `import { applyEvent, chipStep, marksFor, newRunState, stepLabel, toRunEvent, type RunState } from "@/lib/run-state";`
- delete `:32` `  const ceilingFromStream = useRef<number | null>(null);`
- delete `:85` `  const ceiling = useCallback(() => ceilingFromStream.current ?? (submission ? submission.settings.extraPasses + 1 : null), [submission]);` and the blank line after it
- in `onOpen` (`:108`) replace `run.current = newRunState(ceiling());` with `run.current = newRunState(null);`
- delete `:110` (the `if (event.event_type === "graph.session.started" … ceilingFromStream.current = …` line)
- replace

```tsx
  const passes = ceiling();
  const stopped = status !== null && status.status === "running" && status.finished_at !== null;
  const view: SessionView | null = status ? toSessionView(status, passes) : null;
  // M4: the known ceiling, never run.current.maxPasses (which defaults to 1 before the ceiling is known).
  if (view && status?.status === "running" && streaming) { view.iteration = run.current.pass - 1; view.passes = passes; }
  useEffect(() => { setChip(stopped || notFound ? null : view); return () => setChip(null); }, [setChip, status, version, streaming, passes, stopped, notFound]); // eslint-disable-line react-hooks/exhaustive-deps
```

with

```tsx
  const stopped = status !== null && status.status === "running" && status.finished_at !== null;
  // live-briefs spec §4.2: "Running · {active step label}" — the stream's active row while it is
  // open (chipStep), the status snapshot's current_agent otherwise; no pass number anywhere.
  const step = status?.status === "running" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent) : null;
  const view: SessionView | null = status ? toSessionView(status, step) : null;
  useEffect(() => { setChip(stopped || notFound ? null : view); return () => setChip(null); }, [setChip, status, version, streaming, stopped, notFound]); // eslint-disable-line react-hooks/exhaustive-deps
```

- `:176` — replace `<SettingsStrip settings={submission?.settings ?? null} ceiling={passes} id="runningOpts" />` with `<SettingsStrip settings={submission?.settings ?? null} id="runningOpts" />`
- `:180` — delete ` ceiling={passes}` from the `<RunningPipeline … />` element
- `:183` — replace the line with `  return <ReportStage sessionId={sessionId} status={status} strip={<SettingsStrip settings={submission?.settings ?? null} id="reportOpts" />} />;`

`web/components/Composer.tsx`: replace

```tsx
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", extraPasses: 1, outputDir: "output/" };

/* Exactly the body the design specifies: max_iterations always present (0 included). */
export function buildRequest(question: string, s: SubmittedSettings): ResearchRequest {
  const config_overrides: Record<string, unknown> = { llm: { model: s.model, thinking_mode: s.thinking } };
  if (s.outputDir.trim()) config_overrides.output = { directory: s.outputDir.trim() };
  return { query: question.trim(), max_iterations: s.extraPasses, output_format: "markdown", config_overrides };
}
```

with

```tsx
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", outputDir: "output/" };

/* The body the design specifies, with no max_iterations: the API applies the configured
   extra-pass budget (live-briefs spec §4.2, D15). */
export function buildRequest(question: string, s: SubmittedSettings): ResearchRequest {
  const config_overrides: Record<string, unknown> = { llm: { model: s.model, thinking_mode: s.thinking } };
  if (s.outputDir.trim()) config_overrides.output = { directory: s.outputDir.trim() };
  return { query: question.trim(), output_format: "markdown", config_overrides };
}
```

and delete the line `            <span className="setting-pill" id="pillExtra">…</span>` (`:124`).

`web/components/SettingsPopover.tsx`: delete `export const EXTRA_MIN = 0, EXTRA_MAX = 2;` (`:6`) and the whole `<div className="pop-row">` that holds `id="lblExtra"` and `id="stepExtra"` (`:80-91`, through its closing `</div>`).

`web/components/SettingsStrip.tsx`: replace

```tsx
export function SettingsStrip({ settings, ceiling, id }: { settings: SubmittedSettings | null; ceiling: number | null; id?: string }) {
  if (!settings) {
    return (
      <div className="opts" id={id}>
        {ceiling ? <span className="opt"><span className="k">extra passes</span>{ceiling - 1}</span> : null}
        <span className="avail">settings as submitted: not recorded</span>
      </div>
    );
  }
```

with

```tsx
export function SettingsStrip({ settings, id }: { settings: SubmittedSettings | null; id?: string }) {
  if (!settings) {
    return (
      <div className="opts" id={id}>
        <span className="avail">settings as submitted: not recorded</span>
      </div>
    );
  }
```

and delete `      <span className="opt"><span className="k">extra passes</span>{settings.extraPasses}</span>`.

`web/components/ReportRail.tsx`: `:3` → `import { fmtScore, meterClass, passFact, statusNote, toSessionView } from "@/lib/format";`; `:16-17` → `export function ReportRail({ status, evidence }: { status: ResearchSessionResponse; evidence: EvidenceResponse | null }) {` / `  const view = toSessionView(status);`; `:54` → `<dt>pass</dt><dd id="repFactPass">{passFact(status.iteration)}</dd>`.

`web/components/ReportStage.tsx`: `:4` → `import { fmtClock, fmtSeconds, passFact, qFitClass } from "@/lib/format";`; `:13` → `export function ReportStage({ sessionId, status, strip }: { sessionId: string; status: ResearchSessionResponse; strip: ReactNode }) {`; replace `:70-72`

```tsx
  const sv = toSessionView(status, passes);
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passText(sv)}`;
```

with

```tsx
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passFact(status.iteration)}`;
```

and `:101` → `<ReportRail status={status} evidence={evidenceLoaded ? evidence.value : null} />`.

- [ ] **Step 5: Run the unit tests and the type check**

```bash
cd web && npm run -s typecheck && npx vitest run
```
Expected: no `typecheck` output; `Test Files 19 passed (19)`, `Tests 120 passed (120)`.

- [ ] **Step 6: Update the e2e specs (verification)**

`web/e2e/running.spec.ts` — replace `:20`

```ts
  await expect(page.locator("#topbarStatus .chip")).toContainText("Running · pass 1 of 2");
```

with

```ts
  // live-briefs spec §4.2 (AC3): the chip names the step; the running stage has no Now header,
  // no counters block and no pass text.
  await expect(page.locator("#topbarStatus .chip")).toHaveText(/^Running · (Planning|Researching|Evaluating sources|Verifying evidence|Writing report|Reviewing|Publishing)$/);
  for (const gone of [".pipe-now", "#runNow", "#runPasses", "#runLoopTag", "#runCounters"]) await expect(page.locator(`#stage-running ${gone}`)).toHaveCount(0);
  expect(await page.locator("#stage-running").textContent()).not.toMatch(/\bpass\b/i);
```

and replace `:29` `  await expect(page.locator("#runNow")).toHaveText("Researching", { timeout: 10_000 });` with

```ts
  await expect(page.locator('#spine li[data-stage="researcher"][data-state="active"]')).toBeVisible({ timeout: 10_000 });
```

`web/e2e/arcs.spec.ts` — rename the first test to `"the extra pass lights the amber arc: flowing then settled"` and replace its three lines `:8-10` (`#runLoopTag` twice, `#runPasses`) with

```ts
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
```

(keep `:11`, the Researching caption check); rename the second test to `"the redraft lights the grey arc"` and replace `:22-24` with

```ts
  await expect(page.locator('#spineWrap[data-arc="redraft"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
```

`web/e2e/report.spec.ts:17` → `  await expect(page.locator("#repFactPass")).toHaveText("Went back once to fill gaps"); // live-briefs spec §4.2 (AC8)`

`web/e2e/visual.spec.ts:31` → `      await expect(page.locator('#spine li[data-stage="researcher"][data-state="active"]')).toBeVisible({ timeout: 10_000 });`

Create `web/e2e/settings.spec.ts`:

```ts
// live-briefs spec §4.2 (D15, AC8): no extra-passes control anywhere, and the POST body carries no
// max_iterations, so the API applies the configured budget.
import { expect, test } from "@playwright/test";

test("the composer offers no extra-passes control and never sends max_iterations", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#pillExtra")).toHaveCount(0);
  await page.locator("#plusBtn").click();
  await expect(page.locator("#settingsPop")).toHaveAttribute("data-open", "true");
  await expect(page.locator("#stepExtra")).toHaveCount(0);
  await expect(page.locator("#settingsPop")).not.toContainText(/extra pass/i);
  await page.locator("#popClose").click();
  await page.getByLabel("Research question").fill("q");
  const posted = page.waitForRequest((r) => r.method() === "POST" && /\/api\/research$/.test(r.url()));
  await page.getByRole("button", { name: "Start research" }).click();
  const body = (await posted).postDataJSON() as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(["config_overrides", "output_format", "query"]);
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  await expect(page.locator("#runningOpts, #submittedOpts").first()).not.toContainText(/extra pass/i);
});
```

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e
```
Expected: `40 passed`.

- [ ] **Step 7: Checkpoint captures and full-height review**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && npm run -s build && VISUAL_CHECKPOINT=P1-T07-removals DEEP_RESEARCH_PYTHON="$PY" npx playwright test --project=visual
```
Expected: `8 passed`. Open every image in `web/visual/P1-T07-removals/` (Read tool), full height, beside `docs/design/reference/` of the same name (the references are the seven desktop captures and `07-idle-phone`; compare the other phone captures with their desktop reference and with the unchanged regions), and confirm: `01-idle`/`07-idle-phone` — the composer bar shows the model pill and `thinking enabled` only (no `extra passes` pill), nothing else moved; `02-submitted`, `03-running` (+ phone) — four strip chips (`model · thinking · effort · out`), no "Now" block, no counters block, the card holds the seven rows, chip `Running · Researching`; `09-running-extra-pass` (+ phone) — no loop tag, amber arc attached; `04-report` (+ phone) — the head bar ends `· Went back once to fill gaps` (three lines at 1252 px is expected), the Session facts row is not judged from the image — the rail scrolls on its own and a full-page capture clips it at the viewport (`globals.css:317-325`, unchanged) — `report.spec.ts` asserts it; `05-failed`, `08-evidence` (+ phone) — unchanged from the references; no page scrolls sideways at either width. Attach the images to the task summary.

- [ ] **Step 8: Commit**

```bash
git add web/lib/format.ts web/lib/api.ts web/lib/session-store.ts web/lib/run-state.ts web/components/RunningPipeline.tsx web/components/SessionScreen.tsx web/components/Composer.tsx web/components/SettingsPopover.tsx web/components/SettingsStrip.tsx web/components/ReportRail.tsx web/components/ReportStage.tsx web/test web/e2e && git commit -m "feat(web): remove the Now header, counters, pass counter and extra-passes control; name the step in the chip"
```

---

### Task 8: The briefs' state and the brief model (spec §4.3 "State (web)" and "Brief content"; AC5)

**Files:**
- Modify: `web/lib/run-state.ts` (header comment, imports, types, `newRunState`, `rearm`, helpers after `chipStep`, handlers)
- Create: `web/lib/briefs.ts`, `web/test/briefs.test.ts`
- Modify: `web/test/run-state.test.ts:8`, `:30-37` (exact-keys test), append

**Interfaces:**
- Consumes: `stepLabel`, `chipStep` (Task 7); the regenerated captures (Task 6).
- Produces, in `lib/run-state.ts`: `type TopicState = "waiting" | "running" | "done"`; `interface Topic { coverageId: string; title: string; state: TopicState; findings: number | null }`; `interface PlannedTopic { coverageId: string; title: string }`; `interface ReopenLine { kind: "extra_pass" | "redraft"; text: string }`; `RunState` gains `plan: PlannedTopic[]`, `topics: Topic[]`, `pagesRead: number | null`, `findingsSoFar: number | null`, `passFindings: number | null`, `reopen: Partial<Record<NodeId, ReopenLine>>`, `outcomes: Partial<Record<NodeId, string>>`, `open: Set<NodeId>`; `countPhrase(n, one, many): string`; `toggleOpen(run, id: NodeId): void`; a seventeenth handler `researcher.sub_topic.started`. In `lib/briefs.ts`: `type RowState = PaintedMark | "pending"`; `type Subtitle = { kind: "text"; text: string } | { kind: "research"; topics: number; done: number; pages: number; findings: number }`; `interface TopicLine { key; n; title; state; fact }`; `interface RowBrief { subtitle: Subtitle; outcome: string; why: ReopenLine | null; sentence: string | null; topics: TopicLine[] | null; titles: string[] | null }`; `STATIC_META`, `SENTENCES`, `topicFact(topic)`, `verifyingSentence(findings)`, `subtitleText(subtitle)`, `rowBrief(run, id, state)`. Task 9 renders them. Phase 3 will prepend acknowledgement lines to a brief; `RowBrief` keeps the reason line and the body separate so they can go after it.

- [ ] **Step 1: Write the failing tests**

`web/test/run-state.test.ts:8` — replace the import with

```ts
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, chipStep, countPhrase, failedMarks, newRunState, replayRun, stepLabel, toRunEvent, toggleOpen, type RunState } from "../lib/run-state";
```

In the first `describe`, rename `"has the seven rows and the sixteen handlers"` to `"has the seven rows and the seventeen handlers"` and replace the key list's last line pair

```ts
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.tool_call", "source_evaluator.evaluation.completed",
    ]);
```

with

```ts
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "source_evaluator.evaluation.completed",
    ]);
```

Append to `web/test/run-state.test.ts`:

```ts
/* live-briefs spec §4.3: the step briefs' state, proven on the regenerated live captures. */
type Planned = { coverage_id: string; title: string };
const md = <T,>(e: ResearchEvent, key: string) => e.metadata[key] as T;

describe("(f) the Researching checklist follows the live topic events", () => {
  for (const capture of [extraPass, redraft]) {
    const events = capture.events;
    const snaps = snapshots(events, P(capture));
    const planned = at(events, (e) => e.event_type === "planner.planning.completed");
    it(`${capture.case_id}: the plan lists every sub-topic title, each waiting`, () => {
      const titles = md<Planned[]>(events[planned], "sub_topics");
      expect(titles.length).toBeGreaterThan(0);
      const s = snaps[planned];
      expect(s.plan).toEqual(titles.map((t) => ({ coverageId: t.coverage_id, title: t.title })));
      expect(s.topics.map((t) => [t.coverageId, t.title, t.state, t.findings])).toEqual(titles.map((t) => [t.coverage_id, t.title, "waiting", null]));
      expect(s.outcomes.planner).toBe(countPhrase(titles.length, "sub-topic", "sub-topics"));
    });
    it(`${capture.case_id}: a topic runs from its started event and is done, with its findings, from its completed event`, () => {
      events.forEach((e, k) => {
        const topic = () => snaps[k].topics.find((t) => t.coverageId === md<string>(e, "coverage_id"));
        if (e.event_type === "researcher.sub_topic.started") expect(topic()?.state).toBe("running");
        if (e.event_type === "researcher.sub_topic.completed") {
          expect(topic()?.state).toBe("done");
          expect(topic()?.findings).toBe(md<number>(e, "findings_retained"));
        }
      });
    });
    it(`${capture.case_id}: pages read and findings sum the completed topics, then take the research total`, () => {
      let pages = 0, kept = 0;
      events.forEach((e, k) => {
        if (e.event_type === "graph.route.decided" && md<string>(e, "destination") === "extra_pass") { pages = 0; kept = 0; }
        if (e.event_type === "researcher.sub_topic.completed") {
          pages += md<number>(e, "successful_reads"); kept += md<number>(e, "findings_retained");
          expect(snaps[k].pagesRead).toBe(pages);
          expect(snaps[k].findingsSoFar).toBe(kept);
        }
        if (e.event_type === "researcher.research.completed") {
          expect(kept).toBe(md<number>(e, "findings")); // the spec's topic-findings-sum inference, on the stream
          expect(snaps[k].findingsSoFar).toBe(md<number>(e, "findings"));
          expect(snaps[k].passFindings).toBe(md<number>(e, "findings"));
          expect(snaps[k].outcomes.researcher).toBe([
            countPhrase(md<number>(e, "sub_topics_researched"), "topic", "topics"),
            countPhrase(pages, "page read", "pages read"),
            countPhrase(md<number>(e, "findings"), "finding", "findings"),
          ].join(" · "));
        }
      });
    });
  }
});

describe("(g) loops reopen rows with the reason", () => {
  it("the extra pass empties the checklist at the route decision, names the gaps, and lists only the topics it re-runs", () => {
    const events = extraPass.events;
    const snaps = snapshots(events, P(extraPass));
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
    const hop = at(events, (e) => e.event_type === "graph.extra_pass.started", decided);
    const rerun = at(events, (e) => e.event_type === "researcher.sub_topic.started", hop);
    expect(snaps[decided].topics).toEqual([]);
    expect(snaps[decided].outcomes.report_reviewer).toBe("Sent back to fill 1 gap");
    expect(snaps[hop].reopen.researcher).toEqual({ kind: "extra_pass", text: "Going back to research 1 gap the review found" });
    const title = md<Planned[]>(events[at(events, (e) => e.event_type === "planner.planning.completed")], "sub_topics")
      .find((t) => t.coverage_id === md<string>(events[rerun], "coverage_id"))!.title;
    expect(snaps[rerun].topics).toEqual([{ coverageId: md<string>(events[rerun], "coverage_id"), title, state: "running", findings: null }]);
  });
  it("the redraft reopens Writing with the number of issues", () => {
    const events = redraft.events;
    const snaps = snapshots(events, P(redraft));
    const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested");
    expect(snaps[requested].reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
    expect(snaps[requested].reopen.researcher).toBeUndefined();
  });
  it("re-armed rows lose a reader's reopen", () => {
    const run = newRunState(2);
    run.marks = { planner: "done", researcher: "done", source_evaluator: "done" };
    toggleOpen(run, "researcher");
    toggleOpen(run, "planner");
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] } });
    expect([...run.open]).toEqual(["planner"]);
  });
});

describe("(h) every row's outcome line", () => {
  it("reads the spec's templates at the end of each capture", () => {
    for (const capture of [extraPass, redraft]) {
      const events = capture.events;
      const run = replayRun(events, P(capture));
      const last = (type: string) => events.filter((e) => e.event_type === type).at(-1)!;
      const reviewed = last("graph.report.reviewed");
      expect(run.outcomes.source_evaluator).toBe(countPhrase(md<number>(last("source_evaluator.evaluation.completed"), "source_count"), "source rated", "sources rated"));
      const v = last("evidence_verifier.verification.completed");
      expect(run.outcomes.evidence_verifier).toBe(`${md<number>(v, "verified")} verified · ${md<number>(v, "verified_corrected")} corrected · ${md<number>(v, "dropped")} dropped`);
      const w = last("report_writer.report.written");
      expect(run.outcomes.report_writer).toBe(`Report drafted · ${md<number>(w, "statements")} sentences · ${md<number>(w, "citations")} citations`);
      expect(run.outcomes.report_reviewer).toBe(`Accepted · ${md<number>(reviewed, "mean_score").toFixed(2)}`);
      expect(run.outcomes.finalize_report).toBe("Published");
    }
  });
});

describe("(i) burst-safety: a late subscriber paints the same briefs", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const snaps = snapshots(capture.events, P(capture));
      capture.events.forEach((_, k) => expect(replayRun(capture.events.slice(0, k + 1), P(capture))).toEqual(snaps[k]));
    });
  }
});
```

Create `web/test/briefs.test.ts`:

```ts
// @vitest-environment node — pure derivations over the live captures (same reason as run-state.test.ts).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { SENTENCES, STATIC_META, rowBrief, subtitleText, topicFact, verifyingSentence } from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, toRunEvent, type RunState } from "../lib/run-state";

interface Capture { case_id: string; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const captures = [load("missing-target-triggers-one-extra-pass"), load("scoped-redraft-after-a-named-defect")];
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState((events[0].metadata.max_extra_passes as number) + 1);
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}

describe("the Researching brief (spec §4.3, AC5)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState(2);
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("search · scrape · read · memory");
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 2, sub_topics: [{ coverage_id: "topic-01", title: "Alpha" }, { coverage_id: "topic-02", title: "Beta" }] } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("2 topics · researching");
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "topic-02", sub_topic: "Beta", index: 2 } });
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-02", sub_topic: "Beta", index: 2, successful_reads: 0, findings_retained: 0 } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("1 of 2 topics done · no pages read · no findings");
    const topics = rowBrief(run, "researcher", "active").topics!;
    expect(topics.map((t) => [t.n, t.title, t.state, t.fact])).toEqual([[1, "Alpha", "waiting", "not yet"], [2, "Beta", "done", "no findings"]]);
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "topic-01", sub_topic: "Alpha", index: 1 } });
    expect(rowBrief(run, "researcher", "active").topics![0].fact).toBe("reading");
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-01", sub_topic: "Alpha", index: 1, successful_reads: 1, findings_retained: 1 } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("2 of 2 topics done · 1 page read · 1 finding");
  });
  it("never renders a bare 0 or a dash, at any point of either capture", () => {
    for (const capture of captures) {
      for (const run of snapshots(capture.events)) {
        const brief = rowBrief(run, "researcher", "active");
        const texts = [subtitleText(brief.subtitle), rowBrief(run, "researcher", "done").outcome, ...brief.topics!.map((t) => t.fact)];
        for (const text of texts) { expect(text).not.toMatch(/(^|\D)0(\D|$)/); expect(text).not.toContain("—"); }
      }
    }
  });
});

describe("every other row", () => {
  it("shows its static meta as the subtitle and one plain sentence", () => {
    const run = newRunState(2);
    for (const id of ["source_evaluator", "report_writer", "report_reviewer", "finalize_report"] as const) {
      const brief = rowBrief(run, id, "active");
      expect(brief.subtitle).toEqual({ kind: "text", text: STATIC_META[id] });
      expect(brief.sentence).toBe(SENTENCES[id]);
      expect(brief.topics).toBeNull();
    }
    expect(rowBrief(run, "planner", "active").sentence).toBe("Breaking your question into sub-topics…");
  });
  it("Verifying counts the findings of the pass it verifies", () => {
    expect(verifyingSentence(null)).toBe("Checking findings against their pages");
    expect(verifyingSentence(0)).toBe("No findings to check");
    expect(verifyingSentence(1)).toBe("Checking 1 finding against its page");
    expect(verifyingSentence(4)).toBe("Checking 4 findings against their pages");
    const run = newRunState(2);
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
    expect(rowBrief(run, "evidence_verifier", "active").sentence).toBe("Checking 4 findings against their pages");
  });
  it("Planning, once done, lists the sub-topic titles; every done row shows its outcome", () => {
    const [capture] = captures;
    const run = snapshots(capture.events).at(-1)!;
    const planning = rowBrief(run, "planner", "done");
    expect(planning.titles).toEqual(run.plan.map((p) => p.title));
    expect(planning.sentence).toBeNull();
    for (const id of AGENT_ORDER) expect(rowBrief(run, id, "done").outcome).toBe(run.outcomes[id]);
  });
  it("a looped row's first line is why it reopened", () => {
    const run = newRunState(2);
    applyEvent(run, { type: "graph.extra_pass.started", metadata: { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] } });
    expect(rowBrief(run, "researcher", "active").why).toEqual({ kind: "extra_pass", text: "Going back to research 2 gaps the review found" });
    applyEvent(run, { type: "graph.report.redraft_requested", metadata: { iteration: 1, redrafts: 1, material_defects: 3 } });
    expect(rowBrief(run, "report_writer", "active").why).toEqual({ kind: "redraft", text: "Rewriting to fix 3 issues the review found" });
  });
  it("topicFact follows the spec's table", () => {
    expect(topicFact({ coverageId: "a", title: "A", state: "waiting", findings: null })).toBe("not yet");
    expect(topicFact({ coverageId: "a", title: "A", state: "running", findings: null })).toBe("reading");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 1 })).toBe("1 finding");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 9 })).toBe("9 findings");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npx vitest run test/run-state.test.ts test/briefs.test.ts
```
Expected: `Test Files  2 failed (2)` and `Tests  11 failed | 14 passed (25)` — `test/briefs.test.ts` fails to load (`Error: Cannot find module '../lib/briefs'`); in `run-state.test.ts` the seventeen-handlers test and the ten new `(f)`–`(h)` tests fail on missing fields (`expected undefined to …`), while the two `(i)` burst-safety tests already pass, because the unchanged handlers are burst-safe (observed in planning).

- [ ] **Step 3: Extend `lib/run-state.ts`**

Append to the header comment (after `// depends only on events 1..k, so bursts, ticks and a replay from event 1 paint the same screen.`):

```ts
// live-briefs spec §4.3 (2026-09-28) extends it with the step briefs' state — the topic checklist,
// the outcome lines and the reopen lines — on the same rule; `open` is the one field the reader,
// not the stream, writes.
```

After `import type { ResearchEvent, SessionStatus } from "./api";` add `import { fmtScore } from "./format";`.

Replace

```ts
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string }
export interface RunState {
```

with

```ts
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string }
/* The step briefs' state (live-briefs spec §4.3). A topic is one planned sub-topic in this pass's
   Researching checklist; "waiting" until its researcher.sub_topic.started, "running" until its
   researcher.sub_topic.completed. */
export type TopicState = "waiting" | "running" | "done";
export interface Topic { coverageId: string; title: string; state: TopicState; findings: number | null }
export interface PlannedTopic { coverageId: string; title: string }
/* The first line of a row a loop reopened: why it reopened (the old loop tag's content). */
export interface ReopenLine { kind: "extra_pass" | "redraft"; text: string }
export interface RunState {
```

replace

```ts
  counters: Counters; countersPass: number;
  finalStatus: string | null;
}
```

with

```ts
  counters: Counters; countersPass: number;
  finalStatus: string | null;
  plan: PlannedTopic[];                   /* planner.planning.completed.metadata.sub_topics, in plan order */
  topics: Topic[];                        /* this pass's Researching checklist */
  pagesRead: number | null;               /* Σ successful_reads over this pass's completed topics */
  findingsSoFar: number | null;           /* Σ findings_retained over completed topics, then researcher.research.completed.findings */
  passFindings: number | null;            /* the latest researcher.research.completed.findings (Verifying's brief) */
  reopen: Partial<Record<NodeId, ReopenLine>>;
  outcomes: Partial<Record<NodeId, string>>;  /* each row's outcome line once it is done */
  open: Set<NodeId>;                      /* done rows the reader reopened — reader state, not derived from events */
}
```

replace

```ts
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
  };
}
```

with

```ts
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
    plan: [], topics: [], pagesRead: null, findingsSoFar: null, passFindings: null,
    reopen: {}, outcomes: {}, open: new Set(),
  };
}
```

in `rearm`, replace

```ts
    delete run.marks[AGENT_ORDER[i]];
    run.rearmed[AGENT_ORDER[i]] = true;
  }
```

with

```ts
    delete run.marks[AGENT_ORDER[i]];
    run.rearmed[AGENT_ORDER[i]] = true;
    run.open.delete(AGENT_ORDER[i]);
  }
```

and directly after the end of `chipStep` (added in Task 7; its last two lines are `  return "finalize_report";` and `}`) add

```ts
/* A measured count in words: 0 reads "no …", never a bare 0 (live-briefs spec AC5). */
export function countPhrase(n: number, one: string, many: string): string { return n === 0 ? "no " + many : plural(n, one, many); }
const count = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const topicId = (md: Md): string => (typeof md.coverage_id === "string" && md.coverage_id ? md.coverage_id : "index-" + String(md.index));
function topicFor(run: RunState, md: Md): Topic {
  const id = topicId(md);
  const known = run.topics.find((t) => t.coverageId === id);
  if (known) return known;
  const title = run.plan.find((p) => p.coverageId === id)?.title ?? (typeof md.sub_topic === "string" ? md.sub_topic : id);
  const topic: Topic = { coverageId: id, title, state: "waiting", findings: null };
  run.topics.push(topic);
  return topic;
}
/* Reviewing's outcome line, read at the route decision (the review's score arrived just before it). */
function reviewOutcome(run: RunState, md: Md): string {
  if (md.destination === "extra_pass") {
    const k = Array.isArray(md.missing_required_target_ids) ? md.missing_required_target_ids.length : 0;
    return k > 0 ? "Sent back to fill " + plural(k, "gap", "gaps") : "Sent back for more research";
  }
  const score = fmtScore(run.counters.reviewScore);
  if (md.reason === "report_accepted") return score === null ? "Accepted" : "Accepted · " + score;
  return score === null ? "Review unavailable" : "Not accepted · " + score;
}
export function toggleOpen(run: RunState, id: NodeId): void {
  if (run.open.has(id)) run.open.delete(id); else run.open.add(id);
}
```

Handlers — in `"graph.node.completed"`, directly above the line that begins `    if (node === "report_reviewer") return;` (`:106`; the line continues with a long run of spaces and the comment `/* the route decision already moved the active row */`, which stays as it is), insert this line:

```ts
    if (node === "finalize_report") run.outcomes.finalize_report = "Published";
```

Replace

```ts
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
  },
  "researcher.sub_topic.completed": (run) => {
    run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
  },
```

with

```ts
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
    run.outcomes.planner = run.captions.planner;
    const listed: unknown[] = Array.isArray(md.sub_topics) ? md.sub_topics : [];
    run.plan = listed.filter((t): t is { coverage_id: string; title: string } =>
      !!t && typeof (t as Md).coverage_id === "string" && typeof (t as Md).title === "string")
      .map((t) => ({ coverageId: t.coverage_id, title: t.title }));
    run.topics = run.plan.map((p) => ({ coverageId: p.coverageId, title: p.title, state: "waiting", findings: null }));
    run.pagesRead = null; run.findingsSoFar = null;
  },
  "researcher.sub_topic.started": (run, md) => {
    topicFor(run, md).state = "running";
  },
  "researcher.sub_topic.completed": (run, md) => {
    run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
    const topic = topicFor(run, md);
    const kept = count(md.findings_retained);
    topic.state = "done"; topic.findings = kept;
    run.pagesRead = (run.pagesRead ?? 0) + count(md.successful_reads);
    run.findingsSoFar = (run.findingsSoFar ?? 0) + kept;
  },
```

replace

```ts
    c.subTopicsTotal = md.sub_topics_researched + md.sub_topics_skipped;
    c.findings = md.findings;
  },
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
  },
  "evidence_verifier.verification.completed": (run, md) => {
    const c = run.counters;
    c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
  },
  "report_writer.report.written": (run, md) => {
    const c = run.counters;
    c.statements = md.statements; c.refused = md.refused;
  },
```

with

```ts
    c.subTopicsTotal = md.sub_topics_researched + md.sub_topics_skipped;
    c.findings = md.findings;
    run.findingsSoFar = count(md.findings);
    run.passFindings = count(md.findings);
    run.outcomes.researcher = [countPhrase(count(md.sub_topics_researched), "topic", "topics"),
      countPhrase(run.pagesRead ?? 0, "page read", "pages read"), countPhrase(count(md.findings), "finding", "findings")].join(" · ");
  },
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
    run.outcomes.source_evaluator = plural(count(md.source_count), "source rated", "sources rated");
  },
  "evidence_verifier.verification.completed": (run, md) => {
    const c = run.counters;
    c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
    run.outcomes.evidence_verifier = count(md.verified) + " verified · " + count(md.verified_corrected) + " corrected · " + count(md.dropped) + " dropped";
  },
  "report_writer.report.written": (run, md) => {
    const c = run.counters;
    c.statements = md.statements; c.refused = md.refused;
    run.outcomes.report_writer = "Report drafted · " + plural(count(md.statements), "sentence", "sentences") + " · " + plural(count(md.citations), "citation", "citations");
  },
```

replace

```ts
  "graph.route.decided": (run, md) => {
    run.tag = null;
    run.loopPending = false;
    if (md.destination === "extra_pass") {
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
```

with

```ts
  "graph.route.decided": (run, md) => {
    run.tag = null;
    run.loopPending = false;
    run.outcomes.report_reviewer = reviewOutcome(run, md);
    if (md.destination === "extra_pass") {
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
      /* the pass about to run starts its own checklist; the writer's next draft is not a redraft */
      run.topics = []; run.pagesRead = null; run.findingsSoFar = null; run.passFindings = null;
      delete run.reopen.report_writer;
```

after the line `    run.blurbs.researcher = "Researching the " + plural(n, "target", "targets") + " still missing a verified finding.";` add

```ts
    run.reopen.researcher = { kind: "extra_pass", text: "Going back to research " + plural(n, "gap", "gaps") + " the review found" };
```

and after the line `    run.tag = { kind: "redraft", label: "redraft", text: "Reviewer named " + plural(md.material_defects, "material defect", "material defects") };` add

```ts
    run.reopen.report_writer = { kind: "redraft", text: "Rewriting to fix " + plural(count(md.material_defects), "issue", "issues") + " the review found" };
```

- [ ] **Step 4: Create `lib/briefs.ts`**

```ts
// The live step briefs (live-briefs spec §4.3; picks 1A, 2C, 3B): what each spine row says while it
// runs, once it is done, and when a loop reopens it. Pure — a function of RunState only, so a burst,
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";

export type RowState = PaintedMark | "pending";
/* The subtitle a row shows until it is done: its static meta, or Researching's live facts line. */
export type Subtitle =
  | { kind: "text"; text: string }
  | { kind: "research"; topics: number; done: number; pages: number; findings: number };
export interface TopicLine { key: string; n: number; title: string; state: TopicState; fact: string }
export interface RowBrief {
  subtitle: Subtitle;              /* pending and active rows; green while active */
  outcome: string;                 /* done and loop rows */
  why: ReopenLine | null;          /* the first line of a row a loop reopened */
  sentence: string | null;         /* the one plain sentence of every step but Researching */
  topics: TopicLine[] | null;      /* Researching's checklist */
  titles: string[] | null;         /* Planning's final brief: the sub-topic titles */
}

export const STATIC_META: Readonly<Record<NodeId, string>> = Object.fromEntries(STAGES.map((s) => [s.id, s.meta])) as Record<NodeId, string>;
export const SENTENCES: Readonly<Record<Exclude<NodeId, "researcher" | "evidence_verifier">, string>> = {
  planner: "Breaking your question into sub-topics…",
  source_evaluator: "Rating sources for trustworthiness and relevance",
  report_writer: "Writing the report from verified findings only",
  report_reviewer: "Reviewing the draft on 7 dimensions",
  finalize_report: "Saving the report and evidence log",
};

export function topicFact(topic: Topic): string {
  if (topic.state === "waiting") return "not yet";
  if (topic.state === "running") return "reading";
  return countPhrase(topic.findings ?? 0, "finding", "findings");
}
/* Verifying's sentence, from researcher.research.completed.findings of the pass being verified. */
export function verifyingSentence(findings: number | null): string {
  if (findings === null) return "Checking findings against their pages";
  if (findings === 0) return "No findings to check";
  if (findings === 1) return "Checking 1 finding against its page";
  return "Checking " + findings + " findings against their pages";
}
export function subtitleText(subtitle: Subtitle): string {
  if (subtitle.kind === "text") return subtitle.text;
  const { topics, done, pages, findings } = subtitle;
  if (topics === 0) return STATIC_META.researcher;
  if (done === 0) return plural(topics, "topic", "topics") + " · researching";
  return done + " of " + topics + " " + (topics === 1 ? "topic" : "topics") + " done · "
    + countPhrase(pages, "page read", "pages read") + " · " + countPhrase(findings, "finding", "findings");
}
export function rowBrief(run: RunState, id: NodeId, state: RowState): RowBrief {
  const finished = state === "done" || state === "loop";
  const why = run.reopen[id] ?? null;
  const outcome = run.outcomes[id] ?? STATIC_META[id];
  const text: Subtitle = { kind: "text", text: STATIC_META[id] };
  if (id === "researcher") {
    return {
      subtitle: { kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
        pages: run.pagesRead ?? 0, findings: run.findingsSoFar ?? 0 },
      outcome, why, sentence: null, titles: null,
      topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })),
    };
  }
  if (id === "planner" && finished && run.plan.length > 0) {
    return { subtitle: text, outcome, why, sentence: null, topics: null, titles: run.plan.map((p) => p.title) };
  }
  const sentence = id === "evidence_verifier" ? verifyingSentence(run.passFindings) : SENTENCES[id];
  return { subtitle: text, outcome, why, sentence, topics: null, titles: null };
}
```

- [ ] **Step 5: Run the unit tests and the type check**

```bash
cd web && npm run -s typecheck && npx vitest run
```
Expected: no `typecheck` output; `Test Files 20 passed (20)`, `Tests 139 passed (139)`.

- [ ] **Step 6: Commit**

```bash
git add web/lib/run-state.ts web/lib/briefs.ts web/test/run-state.test.ts web/test/briefs.test.ts && git commit -m "feat(web): step-brief state and the brief model, burst-safe on the live captures"
```

---

### Task 9: The running spine — row anatomy, checklist, connector, arcs, open/close (spec §4.3; D1, D2, D17; AC4, AC5, AC6)

**Files:**
- Create: `web/lib/tween.ts`, `web/components/loop-arc.ts`, `web/components/BriefSpine.tsx`, `web/test/tween.test.tsx`, `web/test/components/brief-spine.test.tsx`, `web/test/components/loop-arc.test.tsx`, `web/e2e/briefs.spec.ts`
- Modify: `web/components/Spine.tsx:2-3`, `:28-55`; `web/components/RunningPipeline.tsx`; `web/components/SessionScreen.tsx`; `web/app/globals.css` (append); `web/test/components/running-pipeline.test.tsx`; `web/test/components/session-screen.test.tsx:117-130`, `:177`; `web/e2e/support.ts` (append); `web/e2e/arcs.spec.ts`; `web/e2e/visual.spec.ts:9-14`, `:31-32`

**Interfaces:**
- Consumes: `rowBrief`, `subtitleText`, `RowState`, `Subtitle` (Task 8); `toggleOpen`, `RunState.open` (Task 8); `reducedMotion()` (`web/lib/handoff.ts:22-24`).
- Produces: `BriefSpine({ marks, run, onToggle(id: NodeId) })` rendering `#spineWrap > svg.loop-layer + ol#spine.spine-lg.briefs > li.spine-row[data-stage][data-state][data-fed][data-open][data-handoff?][data-toggle?][aria-current?]`, each `li` = `span.bullet` + `div.ps-body` > (`div.ps-head` > `span.stage-name#name-{id}`, `span.stage-meta.xf#meta-{id}` > `span.m-live` + `span.m-out`, `span.stage-meta.loops`, `button.ps-toggle[aria-expanded][aria-controls][aria-labelledby]` on done/loop rows) + `div.ps-x#brief-{id}` > `div.ps-xi` > `div.ps-brief` > lines (`p.ln.b-why[data-kind]`, `p.ln.b-line`, `div.ps-topics[role=list] > div.ln[role=listitem][data-topic]`, `div.ps-titles[role=list] > div.ln`), each line carrying `--i`. `HANDOFF_HOLD_MS = 2000` and the `data-handoff="from"|"to"` roles (styled in Task 10), including the awaited hand-off: when the active row moves to the successor of a row not yet done (a route decision precedes the reviewer's own completion), that row stays painted active and open until its completion gives it the `from` role. `drawLoopArc(host, list, arc)`; `useLoopArc(enabled, wrap, list, arc, deps)` (window resize + `ResizeObserver` on the wrap + `transitionend` of `grid-template-rows`). `tweenValue(from, to, elapsedMs, durationMs = 400)`, `useTween(value)`, `TWEEN_MS = 400`. `RunningPipeline` gains `onToggleRow(id: NodeId): void`. Phase 3's note line goes after `BriefSpine` inside the same `.card`, and its acknowledgement lines go first in a brief's `lines`.

- [ ] **Step 1: Write the failing unit tests**

Create `web/test/tween.test.tsx`:

```tsx
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TWEEN_MS, tweenValue, useTween } from "../lib/tween";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function Shown({ value }: { value: number }) { return <span>{useTween(value)}</span>; }

describe("count tweens (live-briefs spec §4.3: numbers tween over 400 ms)", () => {
  it("eases from the old value to the new one and lands exactly at 400 ms", () => {
    expect(TWEEN_MS).toBe(400);
    expect(tweenValue(10, 20, 0)).toBe(10);
    expect(tweenValue(10, 20, 200)).toBe(19); // ease-out cubic: 1 - 0.5^3 = 0.875
    expect(tweenValue(10, 20, 399)).toBe(20);
    expect(tweenValue(10, 20, 400)).toBe(20);
    expect(tweenValue(10, 20, -5)).toBe(10);
  });
  it("counts through the values frame by frame", () => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 16) as unknown as number);
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    const { container, rerender } = render(<Shown value={2} />);
    rerender(<Shown value={40} />);
    expect(container.textContent).toBe("2");
    act(() => { vi.advanceTimersByTime(96); });
    const mid = Number(container.textContent);
    expect(mid).toBeGreaterThan(2);
    expect(mid).toBeLessThan(40);
    act(() => { vi.advanceTimersByTime(TWEEN_MS); });
    expect(container.textContent).toBe("40");
  });
  it("jumps from nothing, to zero and under reduced motion", () => {
    const { container, rerender } = render(<Shown value={0} />);
    rerender(<Shown value={3} />);
    expect(container.textContent).toBe("3");
    rerender(<Shown value={0} />);
    expect(container.textContent).toBe("0");
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
    rerender(<Shown value={5} />);
    rerender(<Shown value={9} />);
    expect(container.textContent).toBe("9");
  });
});
```

Create `web/test/components/loop-arc.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { drawLoopArc } from "../../components/loop-arc";

const rect = (left: number, top: number, width: number, height: number) =>
  ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() { return this; } }) as DOMRect;

describe("drawLoopArc", () => {
  it("runs from the reviewer's bullet centre to the destination's, with its arrowhead", () => {
    document.body.innerHTML = `<div id="w"><svg class="loop-layer"><path class="loop-base"></path><path class="loop-flow"></path><path class="loop-head"></path></svg>
      <ol id="l"><li data-stage="researcher"><span class="bullet"></span></li><li data-stage="report_reviewer"><span class="bullet"></span></li></ol></div>`;
    const host = document.getElementById("w")!, list = document.getElementById("l")!;
    host.getBoundingClientRect = () => rect(0, 0, 600, 500);
    list.querySelector<HTMLElement>('[data-stage="researcher"] .bullet')!.getBoundingClientRect = () => rect(12, 100, 33, 33);
    list.querySelector<HTMLElement>('[data-stage="report_reviewer"] .bullet')!.getBoundingClientRect = () => rect(12, 400, 33, 33);
    drawLoopArc(host, list, "extra_pass");
    expect(host.querySelector(".loop-base")!.getAttribute("d")).toBe("M 28.5 416.5 H 4 V 116.5 H 38.5");
    expect(host.querySelector(".loop-flow")!.getAttribute("d")).toBe("M 28.5 416.5 H 4 V 116.5 H 38.5");
    expect(host.querySelector(".loop-head")!.getAttribute("d")).toBe("M 31.5 112.5 L 39.5 116.5 L 31.5 120.5 Z");
    expect(host.querySelector("svg")!.getAttribute("width")).toBe("600");
  });
  it("draws nothing without an arc or a laid-out host", () => {
    document.body.innerHTML = `<div id="w"><svg class="loop-layer"><path class="loop-base"></path></svg><ol id="l"></ol></div>`;
    const host = document.getElementById("w")!, list = document.getElementById("l")!;
    drawLoopArc(host, list, null);
    drawLoopArc(host, list, "redraft");
    expect(host.querySelector(".loop-base")!.hasAttribute("d")).toBe(false);
  });
});
```

Create `web/test/components/brief-spine.test.tsx`:

```tsx
import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BriefSpine, HANDOFF_HOLD_MS } from "../../components/BriefSpine";
import { applyEvent, marksFor, newRunState, toggleOpen, type NodeId, type RunState } from "../../lib/run-state";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const TITLES = [{ coverage_id: "topic-01", title: "Adoption rate" }, { coverage_id: "topic-02", title: "Widget funding" }, { coverage_id: "topic-03", title: "Widget exports" }];
/* Planning done, Researching active with topic 2 done, topic 1 running, topic 3 waiting. */
function researching(): RunState {
  const run = newRunState(2);
  for (const [type, metadata] of [
    ["graph.node.started", { node: "planner", iteration: 0 }],
    ["planner.planning.completed", { sub_topic_count: 3, sub_topics: TITLES }],
    ["graph.node.completed", { node: "planner" }],
    ["graph.node.started", { node: "researcher", iteration: 0 }],
    ["researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Adoption rate", index: 1 }],
    ["researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Widget funding", index: 2 }],
    ["researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Widget funding", index: 2, successful_reads: 2, findings_retained: 2 }],
  ] as const) applyEvent(run, { type, metadata: { ...metadata } });
  return run;
}
const row = (c: HTMLElement, id: NodeId) => c.querySelector<HTMLElement>(`#spine > li[data-stage="${id}"]`)!;
const show = (run: RunState, onToggle = vi.fn()) => render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);

describe("BriefSpine — row anatomy (live-briefs spec §4.3, AC4, AC5)", () => {
  it("keeps the seven li.spine-row[data-stage] rows in ol#spine inside #spineWrap", () => {
    const { container } = show(researching());
    expect([...container.querySelectorAll("#spineWrap > ol#spine.spine-lg.briefs > li.spine-row[data-stage]")].map((li) => li.getAttribute("data-stage")))
      .toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
  });
  it("opens the active row, marks it aria-current=step, and lists every sub-topic with its mark and fact", () => {
    const { container } = show(researching());
    const li = row(container, "researcher");
    expect(li.getAttribute("data-state")).toBe("active");
    expect(li.getAttribute("data-open")).toBe("1");
    expect(li.getAttribute("aria-current")).toBe("step");
    expect(li.querySelector("button.ps-toggle")).toBeNull();
    expect(li.querySelector(".m-live")!.textContent).toBe("1 of 3 topics done · 2 pages read · 2 findings");
    const topics = [...li.querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((t) => [t.getAttribute("data-topic"), t.querySelector(".tn")!.textContent, t.querySelector(".tt")!.textContent, t.querySelector(".tf")!.textContent]))
      .toEqual([["running", "1", "1Adoption rate", "reading"], ["done", "2", "2Widget funding", "2 findings"], ["waiting", "3", "3Widget exports", "not yet"]]);
    expect(topics.map((t) => t.getAttribute("style"))).toEqual(["--i: 0;", "--i: 1;", "--i: 2;"]);
  });
  it("closes a done row on its outcome line behind a toggle button that reopens it", () => {
    const onToggle = vi.fn();
    const run = researching();
    const { container, rerender } = show(run, onToggle);
    const planning = row(container, "planner");
    expect(planning.getAttribute("data-open")).toBe("0");
    expect(planning.querySelector(".m-out")!.textContent).toBe("3 sub-topics");
    const head = planning.querySelector<HTMLButtonElement>("button.ps-toggle")!;
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(head.getAttribute("aria-controls")).toBe("brief-planner");
    expect(planning.querySelector("#brief-planner")!.getAttribute("aria-hidden")).toBe("true");
    fireEvent.click(head);
    expect(onToggle).toHaveBeenCalledWith("planner");
    toggleOpen(run, "planner");
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    expect(planning.getAttribute("data-open")).toBe("1");
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(planning.querySelector("#brief-planner")!.hasAttribute("aria-hidden")).toBe(false);
    expect([...planning.querySelectorAll(".ps-titles > .ln")].map((t) => t.textContent)).toEqual(["1Adoption rate", "2Widget funding", "3Widget exports"]);
  });
  it("never opens a pending row, even if asked", () => {
    const run = researching();
    toggleOpen(run, "source_evaluator");
    const { container } = show(run);
    const li = row(container, "source_evaluator");
    expect(li.getAttribute("data-state")).toBe("pending");
    expect(li.getAttribute("data-open")).toBe("0");
    expect(li.querySelector("button")).toBeNull();
    expect(li.querySelector(".m-live")!.textContent).toBe("authority · recency · relevance");
  });
  it("draws the connector from the upper row: data-fed stays on the lower row", () => {
    const { container } = show(researching());
    expect(row(container, "researcher").getAttribute("data-fed")).toBe("1");
    expect(row(container, "source_evaluator").getAttribute("data-fed")).toBe("0");
    expect(row(container, "planner").hasAttribute("data-fed")).toBe(false);
  });
  it("opens a looped Researching on why it reopened", () => {
    const run = researching();
    for (const [type, metadata] of [
      ["researcher.research.completed", { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 }],
      ["graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] }],
      ["graph.extra_pass.started", { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01"] }],
    ] as const) applyEvent(run, { type, metadata: { ...metadata } });
    const { container } = show(run);
    const why = row(container, "researcher").querySelector(".b-why")!;
    expect(why.textContent).toBe("Going back to research 1 gap the review found");
    expect(why.getAttribute("data-kind")).toBe("extra_pass");
    expect(why.getAttribute("style")).toBe("--i: 0;");
    expect(row(container, "researcher").querySelectorAll(".ps-topics > [data-topic]")).toHaveLength(0);
  });
});

describe("BriefSpine — the hand-off roles (spec §4.3 motion table, pick 3B)", () => {
  it("marks the row that finished 'from' and the next 'to' for HANDOFF_HOLD_MS, then clears both", () => {
    vi.useFakeTimers();
    const run = researching();
    const { container, rerender } = show(run);
    expect(row(container, "researcher").hasAttribute("data-handoff")).toBe(false);
    const subtitle = row(container, "researcher").querySelector(".stage-meta.xf");
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "researcher" } });
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    // The same subtitle element, now showing the outcome: a remounted one could not cross-fade.
    expect(row(container, "researcher").querySelector(".stage-meta.xf")).toBe(subtitle);
    expect(row(container, "researcher").querySelector(".m-out")!.textContent).toBe("3 topics · 2 pages read · 4 findings");
    expect(row(container, "researcher").querySelector("button.ps-toggle")!.getAttribute("aria-labelledby")).toBe("name-researcher meta-researcher");
    expect(row(container, "researcher").getAttribute("data-handoff")).toBe("from");
    expect(row(container, "source_evaluator").getAttribute("data-handoff")).toBe("to");
    expect(row(container, "source_evaluator").getAttribute("data-open")).toBe("1");
    expect(row(container, "researcher").getAttribute("data-open")).toBe("0");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
  it("awaits the row a route decision leaves until its own completion, then hands it off (Reviewing → Publishing)", () => {
    vi.useFakeTimers();
    const run = newRunState(2);
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const { container, rerender } = show(run);
    const again = () => rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    applyEvent(run, { type: "graph.report.reviewed", metadata: { mean_score: 0.9 } });
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "finalize", reason: "report_accepted" } });
    again();
    // The route decision moved the active row one event before Reviewing's own completion.
    expect(run.active).toBe("finalize_report");
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("active");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("1");
    expect(row(container, "report_reviewer").hasAttribute("aria-current")).toBe(false);
    expect(row(container, "report_reviewer").hasAttribute("data-handoff")).toBe(false);
    expect(row(container, "finalize_report").getAttribute("data-handoff")).toBe("to");
    expect(row(container, "finalize_report").getAttribute("aria-current")).toBe("step");
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "report_reviewer" } });
    again();
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("done");
    expect(row(container, "report_reviewer").getAttribute("data-handoff")).toBe("from");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("0");
    expect(row(container, "report_reviewer").querySelector(".m-out")!.textContent).toBe("Accepted · 0.90");
    expect(row(container, "finalize_report").getAttribute("data-handoff")).toBe("to");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
  it("never awaits a row a loop sends the run back from", () => {
    const run = newRunState(2);
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const { container, rerender } = show(run);
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] } });
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("pending");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("0");
    expect(row(container, "researcher").getAttribute("data-state")).toBe("active");
  });
  it("a first render is not a hand-off", () => {
    const { container } = show(researching());
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
});

describe("BriefSpine — the arcs stay attached while rows change height", () => {
  it("observes the wrap's size", () => {
    const observe = vi.fn();
    vi.stubGlobal("ResizeObserver", class { observe = observe; unobserve() {} disconnect() {} });
    const { container } = show(researching());
    expect(observe).toHaveBeenCalledWith(container.querySelector("#spineWrap"));
  });
});
```

In `web/test/components/running-pipeline.test.tsx` replace `startedAt="2026-09-27T00:00:00+00:00" />` with `startedAt="2026-09-27T00:00:00+00:00" onToggleRow={() => {}} />`.

In `web/test/components/session-screen.test.tsx`: in the C2 test (`describe("SessionScreen — C2: no false Planning state during an outage", …)`, `:91-128`) replace these two lines, which occur together once in the file (`:117-118`),

```tsx
    await waitFor(() => expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done"));
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
```

with

```tsx
    await waitFor(() => expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done"));
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
    // live-briefs spec §4.3: the active row is open on its brief; the done row is closed on its outcome.
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-open")).toBe("1");
    expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-open")).toBe("0");
```

and replace the end of the same test and the start of the next `describe` (`:126-130`)

```tsx
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
  });
});

describe("SessionScreen — final-wave item 2: refreshSessions on reaching a terminal status", () => {
```

with

```tsx
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-open")).toBe("1");
  });
});

describe("SessionScreen — final-wave item 2: refreshSessions on reaching a terminal status", () => {
```

At `:177` replace `— "Running · pass 1 of 2" —` with `— "Running · Planning" —` in the comment.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npx vitest run test/tween.test.tsx test/components/loop-arc.test.tsx test/components/brief-spine.test.tsx test/components/session-screen.test.tsx
```
Expected: the three new files fail to import (`Failed to resolve import "../lib/tween"`, `"../../components/loop-arc"`, `"../../components/BriefSpine"`); the session-screen C2 test fails with `expected null to be '1'`.

- [ ] **Step 3: `lib/tween.ts`, `components/loop-arc.ts`, and `Spine.tsx` on the shared arc hook**

Create `web/lib/tween.ts`:

```ts
// Counts tween from the old value to the new one over 400 ms (live-briefs spec §4.3 motion table);
// under reduced motion they jump. A value that appears from nothing, or reaches or leaves 0, jumps
// too: its words change ("no findings" ↔ "1 finding"), so there is nothing to count through.
import { useEffect, useRef, useState } from "react";
import { reducedMotion } from "./handoff";

export const TWEEN_MS = 400;

/* The integer to show `elapsedMs` into a tween from `from` to `to` (ease-out cubic). */
export function tweenValue(from: number, to: number, elapsedMs: number, durationMs: number = TWEEN_MS): number {
  if (elapsedMs >= durationMs) return to;
  const t = Math.max(0, elapsedMs) / durationMs;
  const eased = 1 - Math.pow(1 - t, 3);
  return Math.round(from + (to - from) * eased);
}

export function useTween(value: number): number {
  const [shown, setShown] = useState(value);
  const current = useRef(value);
  useEffect(() => {
    const from = current.current;
    if (from === value) return;
    if (from <= 0 || value <= 0 || reducedMotion()) { current.current = value; setShown(value); return; }
    const start = Date.now();
    let frame = 0;
    const step = () => {
      const next = tweenValue(from, value, Date.now() - start);
      current.current = next;
      setShown(next);
      if (next !== value) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return shown;
}
```

Create `web/components/loop-arc.ts`:

```ts
"use client";
// The return arc (DESIGN.md §3.5), measured from the two rows' bullets exactly as the prototype's
// drawLoop() measures it, and kept attached while rows change height (live-briefs spec §4.3).
import { useLayoutEffect, type DependencyList, type RefObject } from "react";
import { ARCS } from "@/lib/run-state";

export type ArcKind = keyof typeof ARCS;

export function drawLoopArc(host: HTMLElement | null, list: HTMLElement | null, arc: ArcKind | null): void {
  const svg = host?.querySelector<SVGSVGElement>("svg.loop-layer");
  if (!host || !list || !svg || !arc) return;
  const route = ARCS[arc];
  const from = list.querySelector<HTMLElement>(`li[data-stage="${route.from}"] .bullet`);
  const to = list.querySelector<HTMLElement>(`li[data-stage="${route.to}"] .bullet`);
  if (!from || !to) return;
  const hostRect = host.getBoundingClientRect();
  const w = Math.round(hostRect.width), h = Math.round(hostRect.height);
  if (!w || !h) return;
  svg.setAttribute("width", String(w)); svg.setAttribute("height", String(h));
  const leave = from.getBoundingClientRect(), enter = to.getBoundingClientRect();
  const x1 = enter.left - hostRect.left + enter.width / 2, y1 = enter.top - hostRect.top + enter.height / 2;
  const x2 = leave.left - hostRect.left + leave.width / 2, y2 = leave.top - hostRect.top + leave.height / 2;
  const r = Math.max(2, x1 - enter.width / 2 - 8);
  const d = `M ${x2} ${y2} H ${r} V ${y1} H ${x1 + 10}`;
  svg.querySelector(".loop-base")?.setAttribute("d", d);
  svg.querySelector(".loop-flow")?.setAttribute("d", d);
  svg.querySelector(".loop-head")?.setAttribute("d", `M ${x1 + 3} ${y1 - 4} L ${x1 + 11} ${y1} L ${x1 + 3} ${y1 + 4} Z`);
}

/* Re-measured on every layout pass, on a window resize, whenever the wrap itself changes size (a row
   opening or closing grows or shrinks it on every frame of the transition) and at the end of each
   row's height transition. */
export function useLoopArc(enabled: boolean, wrap: RefObject<HTMLElement | null>, list: RefObject<HTMLElement | null>, arc: ArcKind | null, deps: DependencyList): void {
  useLayoutEffect(() => {
    if (!enabled) return;
    const host = wrap.current;
    const draw = () => drawLoopArc(wrap.current, list.current, arc);
    draw();
    window.addEventListener("resize", draw);
    const observer = host && typeof ResizeObserver === "function" ? new ResizeObserver(draw) : null;
    if (observer && host) observer.observe(host);
    const onEnd = (event: TransitionEvent) => { if (event.propertyName === "grid-template-rows") draw(); };
    host?.addEventListener("transitionend", onEnd);
    return () => {
      window.removeEventListener("resize", draw);
      observer?.disconnect();
      host?.removeEventListener("transitionend", onEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
```

`web/components/Spine.tsx`: replace

```tsx
import { useLayoutEffect, useRef } from "react";
import { ARCS, STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
```

with

```tsx
import { useRef } from "react";
import { STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
import { useLoopArc } from "./loop-arc";
```

and replace the whole `useLayoutEffect(() => { if (!withArcs) return; const draw = () => { … }; … }, [withArcs, run.arc, run.loop, marks]);` block (`:28-55`) with

```tsx
  useLoopArc(withArcs, wrap, list, run.arc, [withArcs, run.arc, run.loop, marks]);
```

(The compact rows and their markup are otherwise unchanged; the Failed and Stopped stages keep them.)

- [ ] **Step 4: `BriefSpine.tsx`, and the running stage on it**

Create `web/components/BriefSpine.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { rowBrief, subtitleText, type RowState, type Subtitle } from "@/lib/briefs";
import { STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
import { useTween } from "@/lib/tween";
import { useLoopArc } from "./loop-arc";

/* How long the two hand-off roles stay on their rows (live-briefs spec §4.3 motion table): past the
   last line's rise with ten topics, 900 + 10 × 60 + 240 = 1740 ms. */
export const HANDOFF_HOLD_MS = 2000;
/* `awaiting` is the row the active row just left before that row was marked done. A route decision
   moves the active row one event before the row it leaves reports its own completion
   (graph.route.decided precedes the reviewer's graph.node.completed, web/lib/run-state.ts:145-158 and
   :100-108; 150 ms apart in replay). Until that completion arrives the awaited row keeps painting as
   the active row, open; then it takes the `from` role and folds with the 3B timings. */
interface Handoff { from: NodeId | null; to: NodeId | null; awaiting: NodeId | null }
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void }

const lineStyle = (i: number) => ({ ["--i" as string]: String(i) }) as CSSProperties;
const finishedState = (st: RowState | undefined) => st === "done" || st === "loop";

function ResearchSubtitle({ subtitle }: { subtitle: Extract<Subtitle, { kind: "research" }> }) {
  const done = useTween(subtitle.done), pages = useTween(subtitle.pages), findings = useTween(subtitle.findings);
  return <>{subtitleText({ ...subtitle, done, pages, findings })}</>;
}

/* The running stage's spine (picks 1A, 2C, 3B): each row is li > bullet + (head, brief). The active
   row is always open; a done or loop row shows its outcome and reopens from its head; a pending row
   never opens. The Failed and Stopped stages keep the compact <Spine>. */
export function BriefSpine({ marks, run, onToggle }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  // The hand-off is the render in which the active row changes: the row that was active (now done)
  // and the row that is active now carry data-handoff for HANDOFF_HOLD_MS, so the stylesheet can
  // time them as one choreography. Derived during render (React's "adjust state on prop change").
  // When the row that was active is not done yet and the new active row is its successor, that row is
  // awaited instead: its mark turning done or loop gives it the `from` role, restarting the hold.
  const [prevActive, setPrevActive] = useState<NodeId | null>(run.active);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  if (run.active !== prevActive) {
    setPrevActive(run.active);
    const from = prevActive && finishedState(marks[prevActive]) ? prevActive : null;
    const successor = prevActive === null ? null : STAGES[STAGES.findIndex((s) => s.id === prevActive) + 1]?.id ?? null;
    const awaiting = from === null && prevActive !== null && successor === run.active && run.finalStatus === null ? prevActive : null;
    setHandoff({ from, to: run.active, awaiting });
  } else if (handoff?.awaiting && handoff.from === null && handoff.to === run.active && finishedState(marks[handoff.awaiting])) {
    setHandoff({ from: handoff.awaiting, to: run.active, awaiting: null });
  }
  useEffect(() => {
    if (!handoff) return;
    // An awaited hand-off (a route decision ahead of the row's own completion) waits for that
    // completion however long it takes; only the roles themselves time out.
    if (handoff.awaiting && handoff.from === null) return;
    const timer = setTimeout(() => setHandoff(null), HANDOFF_HOLD_MS);
    return () => clearTimeout(timer);
  }, [handoff]);
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
    // The head never changes element, so its subtitle can cross-fade when the row finishes; a done
    // or loop row adds a button[aria-expanded] laid over the whole head, named by the head itself.
    const head = (
      <div className="ps-head">
        <span className="stage-name" id={`name-${s.id}`}>{s.label}<span className="sr">{st === "active" ? " (in progress)" : ""}</span></span>
        <span className="stage-meta xf" id={`meta-${s.id}`}>
          <span className="m-live" aria-hidden={finished ? "true" : undefined}>
            {brief.subtitle.kind === "research" ? <ResearchSubtitle subtitle={brief.subtitle} /> : brief.subtitle.text}
          </span>
          <span className="m-out" aria-hidden={finished ? undefined : "true"}>{brief.outcome}</span>
        </span>
        <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        {finished ? <button type="button" className="ps-toggle" aria-expanded={open} aria-controls={`brief-${s.id}`} aria-labelledby={`name-${s.id} meta-${s.id}`} onClick={() => onToggle(s.id)} /> : null}
      </div>
    );
    let n = 0;
    const lines: ReactNode[] = [];
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    if (brief.sentence) lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{brief.sentence}</p>);
    if (brief.topics) {
      lines.push(
        <div key="topics" className="ps-topics" role="list">
          {brief.topics.map((t) => (
            <div key={t.key} className="ln" role="listitem" data-topic={t.state} style={lineStyle(n++)}>
              <span className="mk" aria-hidden="true"><span className="ring" /><span className="dotc" /><svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg></span>
              <span className="tt"><span className="tn">{t.n}</span>{t.title}</span>
              <span className="tf">{t.fact}</span>
            </div>
          ))}
        </div>,
      );
    }
    if (brief.titles) {
      lines.push(
        <div key="titles" className="ps-titles" role="list">
          {brief.titles.map((title, k) => (
            <div key={k} className="ln" role="listitem" style={lineStyle(n++)}><span className="tn">{k + 1}</span><span>{title}</span></div>
          ))}
        </div>,
      );
    }
    return (
      <li key={s.id} className="spine-row" data-stage={s.id} data-state={st} data-fed={prev === null ? undefined : finishedState(prev) ? "1" : "0"}
        data-open={open ? "1" : "0"} data-handoff={role} data-toggle={finished ? "1" : undefined}
        aria-current={st === "active" && !awaited ? "step" : undefined} style={{ ["--delay" as string]: String(i * 60) }}>
        <span className="bullet" aria-hidden="true">{i + 1}</span>
        <div className="ps-body">
          {head}
          <div className="ps-x" id={`brief-${s.id}`} aria-hidden={open ? undefined : "true"}>
            <div className="ps-xi"><div className="ps-brief">{lines}</div></div>
          </div>
        </div>
      </li>
    );
  });
  return (
    <div className="spine-wrap" id="spineWrap" data-loop={run.loop} {...(run.arc ? { "data-arc": run.arc } : {})} ref={wrap}>
      <svg className="loop-layer" aria-hidden="true"><path className="loop-base" /><path className="loop-flow" /><path className="loop-head" /></svg>
      <ol className="spine-lg briefs" id="spine" ref={list}>{rows}</ol>
    </div>
  );
}
```

`web/components/RunningPipeline.tsx`: replace

```tsx
import { marksFor, type RunState } from "@/lib/run-state";
import { Spine } from "./Spine";
```

with

```tsx
import { marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { BriefSpine } from "./BriefSpine";
```

replace the comment and signature

```tsx
/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone. The row's accessible name (" (in progress)") stays the
   non-colour state signal. */
export function RunningPipeline({ sessionId, run, question, strip, startedAt }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string }) {
```

with

```tsx
/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone, whose active row is open on its live brief (§4.3). The
   row's accessible name (" (in progress)") and aria-current="step" are the non-colour state signals. */
export function RunningPipeline({ sessionId, run, question, strip, startedAt, onToggleRow }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; onToggleRow(id: NodeId): void }) {
```

and replace `          <Spine marks={marksFor(run, run.active)} run={run} withArcs />` with `          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} />`.

`web/components/SessionScreen.tsx`: replace the run-state import with

```tsx
import { applyEvent, chipStep, marksFor, newRunState, stepLabel, toRunEvent, toggleOpen, type NodeId, type RunState } from "@/lib/run-state";
```

after `  const [version, bump] = useReducer((n: number) => n + 1, 0);` add

```tsx
  // A done row reopened or closed by the reader (live-briefs spec §4.3): reader state on the run.
  const toggleRow = useCallback((id: NodeId) => { toggleOpen(run.current, id); bump(); }, []);
```

and in the `<RunningPipeline … />` element replace `startedAt={status.started_at} />` with `startedAt={status.started_at} onToggleRow={toggleRow} />`.

- [ ] **Step 5: The stylesheet**

Append to `web/app/globals.css` (end of file, inside the app-only section):

```css

/* ═══ 2026-09-28: live step briefs (live-briefs spec §4.3; picks 1A, 2C) ═══
   Scoped to the running stage's spine (`ol.spine-lg.briefs`, BriefSpine.tsx); the Failed and
   Stopped stages keep the compact rows above. Rows align to the top, so an open row keeps its node
   beside its name, and the connector is drawn by the upper row from its own node centre to the next
   one — it stretches with the row instead of assuming equal row heights. `--node-c` is a node's
   centre measured from its row's padding box: the row's padding plus half the 33 px node. */
.spine-lg.briefs{--node-c:calc(var(--space-3) + 16.5px)}
.spine-lg.briefs > li{align-items:start;transition:background var(--motion-fluid) var(--ease-entrance) var(--d-tint,0ms)}
.spine-lg.briefs > li + li::before,
.spine-lg.briefs > li + li::after{content:none}
/* To the next node's centre: the list gap, the two rows' 1px borders, then that row's `--node-c`. */
.spine-lg.briefs > li:not(:last-child)::before,
.spine-lg.briefs > li:not(:last-child)::after{
  content:"";position:absolute;left:calc(var(--space-3) + 16px);width:1px;height:auto;
  top:var(--node-c);bottom:calc(-1 * (var(--space-2) + 2px + var(--node-c)));background:var(--border);
}
.spine-lg.briefs > li:not(:last-child)::after{
  background:var(--status-ok);transform-origin:top;transform:scaleY(0);
  transition:transform var(--fill-line) var(--ease-entrance) var(--d-line,0ms);
}
.spine-lg.briefs > li[data-state="done"]:not(:last-child)::after,
.spine-lg.briefs > li[data-state="loop"]:not(:last-child)::after{transform:scaleY(1)}
.spine-lg.briefs .bullet{
  transition:background var(--motion-fluid) var(--ease-entrance) var(--d-node,0ms),color var(--motion-fluid) var(--ease-entrance) var(--d-node,0ms),
             border-color var(--motion-fluid) var(--ease-entrance) var(--d-node,0ms),box-shadow var(--motion-fluid) var(--ease-entrance) var(--d-node,0ms);
}
.spine-lg.briefs .stage-name{transition:color var(--motion-fluid) var(--ease-entrance) var(--d-node,0ms)}
.spine-lg.briefs .ps-body{min-width:0}
.spine-lg.briefs .ps-head{position:relative;min-height:33px}
.spine-lg.briefs .ps-toggle{position:absolute;inset:0;width:100%;padding:0;border-radius:var(--radius-sm)}
.spine-lg.briefs > li[data-toggle]:hover{background:var(--border-soft)}
/* The subtitle is two stacked lines that cross-fade: the live line until the row is done, then its outcome. */
.spine-lg.briefs .stage-meta.xf{display:grid;font-variant-numeric:tabular-nums}
.spine-lg.briefs .stage-meta.xf > span{grid-area:1/1;min-width:0;transition:opacity var(--motion-base) var(--ease-standard) var(--d-out,0ms)}
.spine-lg.briefs .m-out{opacity:0}
.spine-lg.briefs > li[data-state="done"] .m-live,.spine-lg.briefs > li[data-state="loop"] .m-live{opacity:0}
.spine-lg.briefs > li[data-state="done"] .m-out,.spine-lg.briefs > li[data-state="loop"] .m-out{opacity:1}
/* The brief (pick 1A): the height opens by the 0fr → 1fr technique, then its lines rise 4px and fade
   in 60ms apart; closing fades the lines out, then closes the height. */
.spine-lg.briefs .ps-x{display:grid;grid-template-rows:0fr;transition:grid-template-rows var(--motion-fluid) var(--ease-entrance) var(--d-h,0ms)}
.spine-lg.briefs > li[data-open="1"] .ps-x{grid-template-rows:1fr}
.spine-lg.briefs > li[data-open="0"]{--d-h:160ms}
.spine-lg.briefs .ps-xi{min-height:0;overflow:hidden}
.spine-lg.briefs .ps-brief{padding-top:var(--space-3);display:flex;flex-direction:column;gap:var(--space-2)}
.spine-lg.briefs .ln{transition:opacity var(--dur-c,240ms) var(--ease-entrance) var(--d-c,0ms),transform var(--dur-c,240ms) var(--ease-entrance) var(--d-c,0ms)}
.spine-lg.briefs > li[data-open="0"] .ln{opacity:0;transform:translateY(4px);--dur-c:160ms;--d-c:0ms}
.spine-lg.briefs > li[data-open="1"] .ln{--d-c:calc(280ms + var(--i,0) * 60ms)}
.spine-lg.briefs .b-line,.spine-lg.briefs .b-why{font-size:var(--text-sm);line-height:1.5}
.spine-lg.briefs .b-line{color:var(--fg)}
.spine-lg.briefs .b-why{color:var(--muted)}
.spine-lg.briefs .b-why[data-kind="extra_pass"]{color:var(--status-warn)}
/* Researching's checklist (pick 2C): mark · number · title · fact, hairlines between topics. Divs, not
   list items: the verbatim `.spine-lg li` rules would otherwise style every topic as a pipeline row. */
.ps-topics,.ps-titles{display:flex;flex-direction:column}
.ps-topics > .ln{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:var(--space-3);align-items:center;padding:6px 0;font-size:var(--text-sm);color:var(--muted)}
.ps-titles > .ln{display:grid;grid-template-columns:18px minmax(0,1fr);gap:var(--space-3);padding:6px 0;font-size:var(--text-sm);color:var(--fg)}
.ps-topics > .ln + .ln,.ps-titles > .ln + .ln{border-top:1px solid var(--border-soft)}
.ps-topics > [data-topic="running"],.ps-topics > [data-topic="done"]{color:var(--fg)}
.ps-topics .tn,.ps-titles .tn{font-family:var(--font-mono);color:var(--muted);margin-right:6px}
.ps-topics .tt{min-width:0;overflow-wrap:anywhere}
.ps-topics .tf{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.ps-topics > [data-topic="running"] .tf{color:var(--status-ok)}
/* ✓ drawn, ● running, ○ waiting — one 14px slot, so the column never shifts. */
.mk{width:14px;height:14px;position:relative;display:grid;place-items:center}
.mk .ring{position:absolute;inset:2px;border-radius:50%;border:1px solid var(--border);transition:opacity var(--motion-base) var(--ease-standard)}
.mk .dotc{position:absolute;inset:4px;border-radius:50%;background:var(--status-ok);opacity:0;transform:scale(.4);transition:opacity var(--motion-base) var(--ease-entrance),transform var(--motion-fluid) var(--ease-entrance)}
.mk svg{position:absolute;inset:0;width:14px;height:14px;overflow:visible}
.mk svg path{fill:none;stroke:var(--status-ok);stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:14;stroke-dashoffset:14;transition:stroke-dashoffset 360ms var(--ease-entrance) 80ms}
[data-topic="running"] > .mk .dotc{opacity:1;transform:none;animation:halo var(--motion-halo) var(--ease-entrance) infinite}
[data-topic="running"] > .mk .ring,[data-topic="done"] > .mk .ring{opacity:0}
[data-topic="done"] > .mk svg path{stroke-dashoffset:0}
@media (max-width:480px){
  .ps-topics > .ln{grid-template-columns:18px minmax(0,1fr);row-gap:0}
  .ps-topics .tf{grid-column:2}
}
```

- [ ] **Step 6: Run the unit tests, the type check and the CSS check**

```bash
cd web && npm run -s typecheck && npx vitest run && npm run -s check:css
```
Expected: no `typecheck` output; `Test Files 23 passed (23)`, `Tests 155 passed (155)`; `OK`.

- [ ] **Step 7: The e2e specs (verification)**

Append to `web/e2e/support.ts`:

```ts
/* live-briefs spec AC6: for each pair of adjacent rows, how far the upper row's connector (its
   ::before, measured from its padding box) starts from its own node's centre and ends from the next
   node's centre, in px. */
export const connectorOffsets = (page: Page) => page.evaluate(() => {
  const rows = [...document.querySelectorAll<HTMLElement>("#spine > li[data-stage]")];
  return rows.slice(0, -1).map((li, i) => {
    const r = li.getBoundingClientRect();
    const cs = getComputedStyle(li), line = getComputedStyle(li, "::before");
    const top = r.top + parseFloat(cs.borderTopWidth) + parseFloat(line.top);
    const bottom = r.bottom - parseFloat(cs.borderBottomWidth) - parseFloat(line.bottom);
    const a = li.querySelector(".bullet")!.getBoundingClientRect(), b = rows[i + 1].querySelector(".bullet")!.getBoundingClientRect();
    return { pair: li.dataset.stage + ">" + rows[i + 1].dataset.stage, top: top - (a.top + a.height / 2), bottom: bottom - (b.top + b.height / 2) };
  });
});
```

`web/e2e/arcs.spec.ts` — in the first test (Task 7 titled it `"the extra pass lights the amber arc: flowing then settled"`) replace `:11` (`await expect(page.locator('li[data-stage="researcher"] .stage-meta').first()).toHaveText("1 missing target only");`) with

```ts
  const why = page.locator('#spine li[data-stage="researcher"] .b-why');
  await expect(why).toHaveText("Going back to research 1 gap the review found");
  await expect(why).toHaveAttribute("data-kind", "extra_pass");
```

and rename it `"the extra pass lights the amber arc: flowing then settled, and Researching says why it reopened"`; in the second test (titled `"the redraft lights the grey arc"` since Task 7), directly after its line `  await expect(page.locator('#spineWrap[data-arc="redraft"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });` add

```ts
  const why = page.locator('#spine li[data-stage="report_writer"] .b-why');
  await expect(why).toHaveText("Rewriting to fix 1 issue the review found");
  await expect(why).toHaveAttribute("data-kind", "redraft");
```

and rename it `"the redraft lights the grey arc and Writing says why it reopened"`.

`web/e2e/visual.spec.ts`: directly after the line `const shoot = async (page: Page, name: string) => {` insert `  await page.mouse.move(0, 0); // no hover state in a capture: a finished row highlights under the pointer`, and replace the `03-running` wait Task 7 wrote, `      await expect(page.locator('#spine li[data-stage="researcher"][data-state="active"]')).toBeVisible({ timeout: 10_000 });`, with

```ts
      // live-briefs spec §6: 03-running is taken with the Researching brief open and a topic done.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"] .ps-topics [data-topic="done"]').first()).toBeVisible({ timeout: 10_000 });
```

Create `web/e2e/briefs.spec.ts`:

```ts
// live-briefs spec §4.3: the step briefs on the real (replay) stream — AC4 (open/close by click and
// keyboard), AC5 (every title, never a bare 0 or a dash), AC6 (connector and arc geometry).
import { expect, test, type Page } from "@playwright/test";
import { connectorOffsets, submit, waitTerminal } from "./support";

const ACME_TITLES = ["Adoption rate", "Widget funding", "Widget exports"];
const settledTransitions = (page: Page) => page.waitForFunction(() => document.getAnimations().filter((a) => a instanceof CSSTransition).length === 0);

test("the active row is open; a done row reopens and closes by click, Enter and Space (AC4)", async ({ page, request }) => {
  const id = await submit(page, "q");
  const researcher = page.locator('#spine li[data-stage="researcher"]');
  await expect(researcher).toHaveAttribute("data-state", "active", { timeout: 10_000 });
  await expect(researcher).toHaveAttribute("data-open", "1");
  await expect(researcher).toHaveAttribute("aria-current", "step");
  const planning = page.locator('#spine li[data-stage="planner"]');
  const head = planning.locator("button.ps-toggle");
  await expect(planning).toHaveAttribute("data-open", "0");
  await expect(planning.locator(".m-out")).toHaveText("3 sub-topics");
  await expect(head).toHaveAttribute("aria-expanded", "false");
  await head.click();
  await expect(planning).toHaveAttribute("data-open", "1");
  await expect(head).toHaveAttribute("aria-expanded", "true");
  await expect(planning.locator(".ps-titles > .ln")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
  await head.focus();
  await page.keyboard.press("Enter");
  await expect(planning).toHaveAttribute("data-open", "0");
  await expect(head).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Space");
  await expect(planning).toHaveAttribute("data-open", "1");
  await expect(head).toHaveAttribute("aria-expanded", "true");
  await waitTerminal(request, id);
});

test("the Researching checklist shows every title, and no Researching count ever reads 0 or a dash (AC5)", async ({ page, request }) => {
  const id = await submit(page, "q");
  const researcher = page.locator('#spine li[data-stage="researcher"]');
  await expect(researcher).toHaveAttribute("data-open", "1", { timeout: 10_000 });
  await expect(researcher.locator(".ps-topics .tt")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
  const seen = new Set<string>();
  for (let i = 0; i < 40; i++) {
    const texts = await researcher.evaluate((li) => [li.querySelector(".m-live")!.textContent!, li.querySelector(".m-out")!.textContent!, ...[...li.querySelectorAll(".ps-topics .tf")].map((f) => f.textContent!)]);
    texts.forEach((t) => seen.add(t));
    if ((await researcher.getAttribute("data-state")) === "done") break;
    await page.waitForTimeout(100);
  }
  for (const text of seen) { expect(text).not.toMatch(/(^|\D)0(\D|$)/); expect(text).not.toContain("—"); }
  await waitTerminal(request, id);
});

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    test.use({ viewport });
    test("the connector joins node centres while rows open, close and animate, within 1px (AC6)", async ({ page, request }) => {
      const id = await submit(page, "q");
      await expect(page.locator('#spine li[data-stage="researcher"]')).toHaveAttribute("data-open", "1", { timeout: 10_000 });
      const samples: { pair: string; top: number; bottom: number }[] = [];
      for (let i = 0; i < 60; i++) { samples.push(...(await connectorOffsets(page))); await page.waitForTimeout(50); }
      expect(samples.length).toBeGreaterThan(0);
      for (const s of samples) { expect(Math.abs(s.top), s.pair).toBeLessThanOrEqual(1); expect(Math.abs(s.bottom), s.pair).toBeLessThanOrEqual(1); }
      await waitTerminal(request, id);
    });
  });
}

test("the extra-pass arc stays attached to both nodes after Researching reopens, within 2px (AC6)", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  await settledTransitions(page);
  const gap = await page.evaluate(() => {
    const host = document.getElementById("spineWrap")!.getBoundingClientRect();
    const centre = (stage: string) => { const b = document.querySelector(`#spine li[data-stage="${stage}"] .bullet`)!.getBoundingClientRect(); return b.top - host.top + b.height / 2; };
    const [, , y2, , , , y1] = document.querySelector("#spineWrap .loop-base")!.getAttribute("d")!.split(" ");
    return { leave: Math.abs(Number(y2) - centre("report_reviewer")), enter: Math.abs(Number(y1) - centre("researcher")) };
  });
  expect(gap.leave).toBeLessThanOrEqual(2);
  expect(gap.enter).toBeLessThanOrEqual(2);
  await waitTerminal(request, id);
});
```

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e
```
Expected: `45 passed` (Task 7's 40 plus the five in `briefs.spec.ts`; `layout.spec.ts:38-39` still finds seven `#spine li[data-stage]` in order, and `cardsUnclipped` holds at both widths).

- [ ] **Step 8: Checkpoint captures and full-height review**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && npm run -s build && VISUAL_CHECKPOINT=P1-T09-briefs DEEP_RESEARCH_PYTHON="$PY" npx playwright test --project=visual
```
Expected: `8 passed`. Review `web/visual/P1-T09-briefs/03-running.png`, `03-running-phone.png`, `09-running-extra-pass.png`, `09-running-extra-pass-phone.png` full height against the canvas picks in `docs/design/running-stage-picks/` (`Main.dc.html` 1A, `Briefs.dc.html` column C, `PhoneBrief.dc.html`; values in `theme.css:79-136`, `:225-226`) and the spec's §4.3 tables, and confirm: Planning closed on `3 sub-topics`; Researching open with its subtitle in green (`1 of 3 topics done · … pages read · … findings`, or `3 topics · researching`), its checklist below with hairlines between topics, each topic marked by its state — ● green dot with `reading` in green, ✓ with `{n} findings` muted, ○ with `not yet` (at capture time the replay has topics 1 and 3 reading and topic 2 done: `1 of 3 topics done · 2 pages read · 2 findings`, observed in planning); the other rows closed on their static meta; the grey connector passes node centre to node centre through the open brief and the green fill stops at the last done node; no box inside the card, no purple; on phone each topic's fact sits under its title; in `09` the amber arc joins Reviewing's and Researching's nodes and Researching reads `1 topic · researching` and opens on the amber `Going back to research 1 gap the review found` above only the topic the extra pass re-runs (`1 Adoption rate · reading`), never the first pass's topics; no sideways scroll. Every other capture matches `P1-T07-removals`. Attach the four images to the task summary.

- [ ] **Step 9: Commit**

```bash
git add web/lib/tween.ts web/components/loop-arc.ts web/components/BriefSpine.tsx web/components/Spine.tsx web/components/RunningPipeline.tsx web/components/SessionScreen.tsx web/app/globals.css web/test web/e2e && git commit -m "feat(web): live step briefs — open rows, the topic checklist, node-to-node connector, arcs that follow"
```

---

### Task 10: The hand-off and reduced motion (spec §4.3 motion table; D1, D3; AC7)

**Files:**
- Modify: `web/app/globals.css` (append); `web/e2e/support.ts` (append)
- Create: `web/e2e/motion.spec.ts`

**Interfaces:**
- Consumes: `data-handoff="from"|"to"` and `HANDOFF_HOLD_MS` (Task 9), the `.ln`/`.ps-x`/`.m-out`/`::after`/`.bullet`/`.mk` elements and the custom properties `--d-h`, `--d-out`, `--d-tint`, `--d-line`, `--d-node`, `--dur-c`, `--d-c` (Task 9).
- Produces: the 3B timings and the reduced-motion rules; `installMotionRecorder(page)`, `motion(page): Promise<MotionRecord[]>`, `interface MotionRecord { stage; handoff; open; part; prop; delay; duration }` in `e2e/support.ts`.

- [ ] **Step 1: The stylesheet**

Append to `web/app/globals.css`:

```css

/* ═══ 2026-09-28: the hand-off between steps (pick 3B, overlapped; live-briefs spec §4.3) ═══
   Timed from the render in which row k is marked done and row k+1 becomes active; BriefSpine holds
   the two roles (data-handoff) for HANDOFF_HOLD_MS. Row k: its lines fade out at 0 (180ms), its
   height closes at 100ms, its subtitle cross-fades to the outcome at 260ms and the connector below it
   fills at 180ms (--fill-line). Row k+1: its node fills at 600ms, its height opens at 600ms and its
   lines rise at 900ms + i × 60ms — so the next row opens while the line is still filling. The
   running node's halo waits the same 600ms, so it starts once the node has filled. */
.spine-lg.briefs > li[data-handoff="from"]{--d-h:100ms;--d-out:260ms;--d-tint:260ms;--d-line:180ms}
.spine-lg.briefs > li[data-handoff="from"] .ln{transform:none;--dur-c:180ms;--d-c:0ms}
.spine-lg.briefs > li[data-handoff="to"]{--d-node:600ms;--d-tint:600ms;--d-h:600ms}
.spine-lg.briefs > li[data-handoff="to"] .ln{--d-c:calc(900ms + var(--i,0) * 60ms)}
.spine-lg.briefs > li[data-state="active"] .bullet{animation-delay:600ms}
@media (prefers-reduced-motion:reduce){
  /* Heights change at once; lines fade over 160ms with no stagger and no travel; the ✓ appears
     without drawing; the dot does not scale; halos rest at a pinned radius, as the node's does. */
  .spine-lg.briefs .ps-x{transition:none !important}
  .spine-lg.briefs .ln{transform:none !important;transition-property:opacity !important;transition-duration:160ms !important;transition-delay:0ms !important}
  .spine-lg.briefs > li::after{transition:none !important}
  .mk svg path{transition:none !important}
  .mk .dotc{transform:none !important}
  [data-topic="running"] > .mk .dotc{box-shadow:0 0 0 4px color-mix(in oklch,var(--status-ok) 13%,transparent) !important}
}
```

(The hand-off block must come after Task 9's block: `li[data-handoff]` and `li[data-open]` rules have equal specificity, so source order decides.)

```bash
cd web && npm run -s check:css
```
Expected: `OK`.

- [ ] **Step 2: The recorder and the motion spec (verification)**

Append to `web/e2e/support.ts`:

```ts
/* live-briefs spec AC7: every CSS transition the running spine starts, with the delay and duration it
   was started with — read from the element's computed transition lists at transitionrun, the moment
   a transition is created (its delay phase included). `part` names what moved; `handoff`/`open` are
   the row's roles at that moment. */
export interface MotionRecord { stage: string | null; handoff: string | null; open: string | null; part: string; prop: string; delay: number; duration: number }
export async function installMotionRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drMotion: MotionRecord[] };
    w.__drMotion = [];
    const ms = (v: string) => (v.trim().endsWith("ms") ? parseFloat(v) : parseFloat(v) * 1000);
    const partOf = (el: Element, pseudo: string): string => {
      if (pseudo) return "connector";
      if (el.classList.contains("ps-x")) return "ps-x";
      if (el.classList.contains("ln")) return "line";
      if (el.classList.contains("bullet")) return "bullet";
      if (el.classList.contains("m-live")) return "m-live";
      if (el.classList.contains("m-out")) return "m-out";
      if (el.classList.contains("dotc")) return "dot";
      if (el.tagName.toLowerCase() === "path" && el.closest(".mk")) return "check";
      if (el.matches("li[data-stage]")) return "row";
      return el.tagName.toLowerCase();
    };
    document.addEventListener("transitionrun", (event) => {
      const e = event as TransitionEvent;
      const el = e.target as Element;
      const row = el.closest("#spine > li[data-stage]");
      if (!row) return;
      const style = getComputedStyle(el, e.pseudoElement || null);
      const props = style.transitionProperty.split(",").map((p) => p.trim());
      const delays = style.transitionDelay.split(","), durations = style.transitionDuration.split(",");
      // A shorthand in the list starts its transitions on longhands: background → background-color,
      // border-color → border-top-color and its three siblings. A property the list does not name at
      // all records -1/-1, so no assertion can match it by borrowing another property's timing.
      const shorthand = e.propertyName === "background-color" ? "background"
        : /^border-(top|right|bottom|left)-color$/.test(e.propertyName) ? "border-color" : e.propertyName;
      let i = props.indexOf(e.propertyName);
      if (i < 0) i = props.indexOf(shorthand);
      if (i < 0) i = props.indexOf("all");
      w.__drMotion.push({ stage: row.getAttribute("data-stage"), handoff: row.getAttribute("data-handoff"), open: row.getAttribute("data-open"),
        part: partOf(el, e.pseudoElement), prop: e.propertyName,
        delay: i < 0 ? -1 : ms(delays[i % delays.length]), duration: i < 0 ? -1 : ms(durations[i % durations.length]) });
    }, true);
  });
}
export const motion = (page: Page) => page.evaluate(() => (window as unknown as { __drMotion: MotionRecord[] }).__drMotion);
```

Create `web/e2e/motion.spec.ts`:

```ts
// live-briefs spec §4.3 motion table (D1, D3; AC7): every transition the step briefs start, recorded
// with the delay and duration it started with, against the spec's timings — and none of them travel
// or change height under reduced motion.
import { expect, test } from "@playwright/test";
import { installMotionRecorder, motion, submit, waitTerminal, type MotionRecord } from "./support";

const only = (records: MotionRecord[], where: Partial<MotionRecord>) =>
  records.filter((r) => Object.entries(where).every(([k, v]) => r[k as keyof MotionRecord] === v));
const timings = (records: MotionRecord[]) => [...new Set(records.map((r) => `${r.delay}/${r.duration}`))].sort();

test("the hand-off (3B), a reader's open and close (1A) and the drawn check keep the spec's timings", async ({ page, request }) => {
  await installMotionRecorder(page);
  const id = await submit(page, "q");
  const planning = page.locator('#spine li[data-stage="planner"]');
  await expect(planning).toHaveAttribute("data-state", "done", { timeout: 10_000 });
  await page.waitForTimeout(2_100); // any hand-off roles from the first render's neighbours have cleared
  await planning.locator("button.ps-toggle").click();
  await expect(planning).toHaveAttribute("data-open", "1");
  await page.waitForTimeout(700);
  await planning.locator("button.ps-toggle").click();
  await expect(planning).toHaveAttribute("data-open", "0");
  await waitTerminal(request, id);
  const records = await motion(page);

  // Row k (Researching) as the hand-off's "from": lines fade 180ms at 0, height closes at 100ms,
  // the subtitle cross-fades at 260ms, the connector below it fills at 180ms.
  const from = only(records, { stage: "researcher", handoff: "from" });
  expect(timings(only(from, { part: "line", prop: "opacity" }))).toEqual(["0/180"]);
  expect(only(from, { part: "line", prop: "transform" })).toEqual([]);
  expect(timings(only(from, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["100/420"]);
  expect(timings(only(from, { part: "m-out", prop: "opacity" }))).toEqual(["260/200"]);
  expect(timings(only(from, { part: "connector", prop: "transform" }))).toEqual(["180/620"]);
  // Row k+1 (Evaluating sources) as the "to": node fills and height opens at 600ms, its one line rises at 900ms.
  const to = only(records, { stage: "source_evaluator", handoff: "to" });
  expect(timings(only(to, { part: "bullet", prop: "background-color" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "line", prop: "opacity" }))).toEqual(["900/240"]);
  expect(timings(only(to, { part: "line", prop: "transform" }))).toEqual(["900/240"]);

  // Reviewing → Publishing. graph.route.decided moves the active row one event before Reviewing's own
  // completion (150 ms apart in replay); Reviewing stays as it was — active, open — until then, so nothing
  // on its row moves between its last to-role transition and its first from-role one (it is never painted pending)…
  const onReviewing = records.filter((r) => r.stage === "report_reviewer");
  const firstFrom = onReviewing.findIndex((r) => r.handoff === "from");
  const lastTo = onReviewing.slice(0, Math.max(firstFrom, 0)).map((r) => r.handoff).lastIndexOf("to");
  expect(firstFrom).toBeGreaterThan(0);
  expect(onReviewing.slice(lastTo + 1, firstFrom)).toEqual([]);
  // …and its completion folds it with the from-role timings. The subtitle cross-fade and the connector fill
  // always run; its lines and height fold only as far as they had opened — Reviewing is active for about
  // four paced events, less than its own 600/900 ms opening delays — so those are held to their timing,
  // not their presence.
  const reviewing = onReviewing.filter((r) => r.handoff === "from");
  expect(timings(only(reviewing, { part: "m-out", prop: "opacity" }))).toEqual(["260/200"]);
  expect(timings(only(reviewing, { part: "connector", prop: "transform" }))).toEqual(["180/620"]);
  expect(timings(only(reviewing, { part: "line", prop: "opacity" })).filter((x) => x !== "0/180")).toEqual([]);
  expect(only(reviewing, { part: "line", prop: "transform" })).toEqual([]);
  expect(timings(only(reviewing, { part: "ps-x", prop: "grid-template-rows" })).filter((x) => x !== "100/420")).toEqual([]);

  // A reader's open (1A): height at once over 420ms, then the three titles rise 60ms apart from 280ms.
  const opened = only(records, { stage: "planner", handoff: null, open: "1" });
  expect(timings(only(opened, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["0/420"]);
  expect(timings(only(opened, { part: "line", prop: "opacity" }))).toEqual(["280/240", "340/240", "400/240"]);
  // …and close: the lines fade 160ms with no stagger, then the height closes at 160ms.
  const closed = only(records, { stage: "planner", handoff: null, open: "0" });
  expect(timings(only(closed, { part: "line", prop: "opacity" }))).toEqual(["0/160"]);
  expect(timings(only(closed, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["160/420"]);

  // A topic done: the ✓ draws over 360ms after 80ms; the dot fades over 200ms.
  expect(timings(only(records, { part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { part: "dot", prop: "opacity" }))).toEqual(["0/200"]);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing travels and no height animates; lines only fade, 160ms, together (AC7)", async ({ page, request }) => {
    await installMotionRecorder(page);
    const id = await submit(page, "q");
    const planning = page.locator('#spine li[data-stage="planner"]');
    await expect(planning).toHaveAttribute("data-state", "done", { timeout: 10_000 });
    await planning.locator("button.ps-toggle").click();
    await expect(planning).toHaveAttribute("data-open", "1");
    await waitTerminal(request, id);
    const records = await motion(page);
    expect(records.filter((r) => r.prop === "transform")).toEqual([]);
    expect(records.filter((r) => r.prop === "grid-template-rows")).toEqual([]);
    const lines = only(records, { part: "line" });
    expect(lines.length).toBeGreaterThan(0);
    expect(timings(lines)).toEqual(["0/160"]);
    expect([...new Set(lines.map((r) => r.prop))]).toEqual(["opacity"]);
  });
});
```

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e
```
Expected: `47 passed` (the existing `reduced-motion.spec.ts` included). Without Step 1's CSS both new tests fail (observed in planning): the hand-off test at its first assertion, because the finishing row's lines fade like a plain close (`"0/160"` received where `"0/180"` is expected), and the reduced-motion test because the lines still get `transform` transitions — which is how this spec proves the stylesheet rather than itself.

- [ ] **Step 3: Checkpoint captures and review**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && npm run -s build && VISUAL_CHECKPOINT=P1-T10-motion DEEP_RESEARCH_PYTHON="$PY" npx playwright test --project=visual
```
Expected: `8 passed`. Open the fourteen images in `web/visual/P1-T10-motion/` full height beside the same names in `web/visual/P1-T09-briefs/`: every rest state is unchanged (this task changes timing only, never a resting style). The hand-off itself — the canvas's `docs/design/running-stage-picks/Handoff.dc.html` column B, `docs/design/running-stage-picks/theme.css:166-174` — is verified by `motion.spec.ts` in Step 2, not by eye. Attach the images to the task summary.

- [ ] **Step 4: Commit**

```bash
git add web/app/globals.css web/e2e/support.ts web/e2e/motion.spec.ts && git commit -m "feat(web): the overlapped hand-off between steps, and briefs under reduced motion"
```

---

### Task 11: The design record for the running stage (spec §4.9 Phase-1 rows; E5)

**Files:**
- Modify: `docs/design/DESIGN.md` (§2 `:139-144`, §3 `:157`, `:189-195`, §3.0 `:227-230`, §3.2 `:470-486`, `:543-575`, §3.4 `:672-680`, `:698-700`, `:712`, §3.5 `:750`, `:784-789`, `:805-834`, `:846-848`, `:861-865`, §4 `:913`, `:995-1036`, §5.6 `:1338-1352`, §5.7 `:1514-1529`, `:1542-1546`, §5.8 `:1550-1587`, §6 `:1599`); `docs/design/api-gaps.md` (header, Closed table, 3.1)

**Interfaces:**
- Consumes: Tasks 6–10 (what the app now does).
- Produces: documentation only.

- [ ] **Step 1: DESIGN.md §2, §3, §3.0, §3.2**

Replace

```markdown
contribution is reduced to the stage spine and the counters block inside the
pipeline card (§5.8).
```

with

```markdown
contribution is reduced to the stage spine, whose running row opens on a live
brief (§3.4).
```

In the §3 inventory table replace `the five-chip settings strip (\`model · thinking · effort · extra passes · out\`)` with `the four-chip settings strip (\`model · thinking · effort · out\`)`.

Replace the paragraph that begins `The counters that used to sit in that column now live inside the pipeline card,` and ends `the whole spine off-screen on phone.` with

```markdown
The counters block that later sat below the spine has been removed from the running
stage too (live-briefs D13, 2026-09-28): each row counts for itself, in its live
subtitle while it runs and in its outcome line once it is done (§3.4). The block
remains on the Failed and Stopped stages, as what survived the halt (§5.8).
```

In §3.0 replace

```markdown
Every stage after submission carries the same five facts, in the same order, as
a mono chip row: **model, thinking, effort, extra passes, out** — `model
deepseek-flash` · `thinking enabled` · `effort per agent` · `extra passes 1` ·
`out output/`. They are rendered from the session's own copy, not from the live
```

with

```markdown
Every stage after submission carries the same four facts, in the same order, as
a mono chip row: **model, thinking, effort, out** — `model deepseek-flash` ·
`thinking enabled` · `effort per agent` · `out output/`. There is no extra-passes
chip (live-briefs D15; §3.2). They are rendered from the session's own copy, not from the live
```

In §3.2 replace `What the composer sends — the four knobs it exposes, and the one line it only` with `What the composer sends — the three knobs it exposes, and the one line it only`; delete the table row that begins `| Extra passes | top-level \`max_iterations\` |`; and in the request body replace `{"query": "…", "max_iterations": 1, "output_format": "markdown",` with `{"query": "…", "output_format": "markdown",`.

Replace the text from `` `max_iterations` is the operator's control over how long a run may take, so it is`` through `reviewer accepts first time takes none.` (`:543-575`) with

```markdown
**There is no extra-pass control** (live-briefs D15, 2026-09-28). The stepper, its
pill and its chip in the settings strip were removed: a reader had to guess the
budget before anything had been found. The reviewer's own gap-filling stays at the
configured `graph.max_extra_passes` (`config.yaml:211`, default 1); the composer
sends no `max_iterations`, and the API still accepts one (`api/models.py`), so a
scripted client keeps the old control.

The value is a ceiling, not a target, and the loop is not a retry: an extra pass
is bought only when a required evidence target still has no verified finding
after the review, and only while budget remains (`graph/state.py:295-297`). A run
whose targets are all answered publishes after one pass. Nothing on the running
stage counts passes (§3.5): a row that a loop reopens says why it reopened, and
the report states the passes in plain words (§4).
```

- [ ] **Step 2: DESIGN.md §3.4**

After the paragraph that ends `begins at the first node and stops at the last, so there is no overhang to clip.` add

```markdown
**Rows that expand keep the connector on the nodes** (live-briefs, 2026-09-28). The
running stage's rows open into a brief, so rows no longer share one height and the
midpoint rule above no longer lands on a node. In that spine (`ol.spine-lg.briefs`)
rows align to the top, each node sits at a fixed offset — its centre is
`var(--space-3) + 16.5px` below its row's padding box — and the connector is drawn
by the *upper* row, from its own node centre to the next node's centre
(`bottom: -(--space-2 + 2px + --space-3 + 16.5px)`: the list gap, the two rows' 1px
borders and the next node's offset). The fill scales from the top
(`transform: scaleY(0 → 1)`) once the upper row is `done` or `loop`; `data-fed`
stays on the lower row. The Failed and Stopped stages keep the compact rows and the
midpoint rule.

**What an open row says** (picks 1A, 2C). The active row is always open; a done or
loop row is closed on its outcome line and reopens from its head (a
`button[aria-expanded]` over the head); a pending row never opens. While a row runs
its subtitle is green: its static meta, except Researching's live facts line,
`{done} of {n} topics done · {pages} pages read · {findings} findings` (or
`{n} topics · researching` before the first topic is done). Its body is one plain
sentence — `Breaking your question into sub-topics…`, `Rating sources for
trustworthiness and relevance`, `Checking {findings} findings against their pages`,
`Writing the report from verified findings only`, `Reviewing the draft on 7
dimensions`, `Saving the report and evidence log` — except Researching's, a
checklist of the pass's topics: ○ `not yet`, ● `reading` (green, with the halo),
✓ `{n} findings`. Topics run concurrently, so several can be reading at once. Once
done, a row's subtitle is its outcome: `{n} sub-topics`; `{n} topics · {pages}
pages read · {findings} findings`; `{n} sources rated`; `{v} verified · {c}
corrected · {d} dropped`; `Report drafted · {s} sentences · {c} citations`;
`Accepted · {score}` or `Not accepted · {score}`; `Published`. A reopened Planning
row lists the sub-topic titles; a reopened Researching row, its final checklist. In
Researching's texts a measured zero reads in words (`no findings`).
```

Replace

```markdown
**One loop, and it is a ring.** The running node's halo eases between a 4px and a
7px radius at 13–20% over `2.2s`; the header status dot uses the same `halo`. That
is the whole animation budget for this screen.
```

with

```markdown
**One loop, and it is a ring.** The running node's halo eases between a 4px and a
7px radius at 13–20% over `2.2s`; the header status dot and each running topic's
dot in the Researching brief use the same `halo`. That is the whole looping budget
for this screen: a row opening or closing, the hand-off between steps, a topic's
drawn ✓ and a count's tween each run once (§5.6).
```

In the rules table's first row replace `and by the progress track's \`aria-valuenow\`.` with `and by \`aria-current="step"\` on the running row.`

- [ ] **Step 3: DESIGN.md §3.5**

Replace `event cannot mark the running row. The "Now" row is the` — after Task 6 the sentence reads `…read the same either way. The "Now" row is the **successor of the last` — so replace `The "Now" row is the **successor of the last` with `The active row is the **successor of the last`.

Replace `nothing looped. \`--meta\` is a stroke here and never text; the loop tag's amber` with `nothing looped. \`--meta\` is a stroke here and never text; the extra-pass reopen line's amber`.

Replace the paragraph from `**The loop's reason is the one sentence, and it lives in the header.**` through `departure from the spec's copy, which is written for n ≥ 2.` with

```markdown
**A reopened row says why it reopened** (live-briefs D14, 2026-09-28). On
`graph.extra_pass.started` Researching's brief opens on `Going back to research {k}
gaps the review found` (`k` = the event's `targets`; text `--status-warn`), and its
checklist lists only the topics that pass re-runs, as they start; on
`graph.report.redraft_requested` Writing's opens on `Rewriting to fix {n} issues the
review found` (`--muted`). Both pluralise (`1 gap`, `1 issue`). This replaces the
header's loop tag, which went with the "Now" header.
```

Replace the two paragraphs from `The **pass track was removed from the running stage.**` through `in flight opening its dot into a ring while an arc is \`flowing\`, so the two\nagree.` with

```markdown
**No pass counter** (live-briefs D14). The pass track, and later the pass number
in the header and the chip, were second representations of what the arc already
draws; both are gone. The internal `iteration` stays in the API and the trace, and
the report states the passes in plain words (§4). The prototype keeps its hidden
`#passTrack` host as a historical record.
```

Replace

```markdown
- **It defends a number that should not have needed defending.** A step count
  invites the question "out of how many"; `pass p of P` is the one counter kept,
  because both of its numbers are the run's own.
```

with

```markdown
- **It defends a number that should not have needed defending.** A step count
  invites the question "out of how many"; the running stage now keeps no counter
  at all, and a reopened row says why it reopened (live-briefs D14).
```

Replace

```markdown
the route history, so the arcs and the pass number are driven by
`graph.route.decided`, `graph.extra_pass.started` and
`graph.report.redraft_requested` rather than by the session. Reopening a finished
session can therefore show `pass p of P` but not replay its loops.
```

with

```markdown
the route history, so the arcs and the reopen lines are driven by
`graph.route.decided`, `graph.extra_pass.started` and
`graph.report.redraft_requested` rather than by the session. A finished session
opens on its report, which states the passes in plain words (§4); its loops are
not redrawn.
```

- [ ] **Step 4: DESIGN.md §4**

Replace the table row

```markdown
| **Running** | `running` | the pass from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · pass p of P` |
```

with

```markdown
| **Running** | `running` | the active row from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · {step}`, e.g. `Running · Researching` |
```

Replace the whole section from `### \`iteration\` on every surface, and it must agree on all of them` down to (not including) `### Error rendering` with

```markdown
### The passes, in plain words

The interface never shows a pass number (live-briefs D14, D15, 2026-09-28). While a
run is live the chip names the running row — `Running · {step}` — from the stream's
active row (§3.5), or from `/status.current_agent` before the stream opens (a hop
reads as the row it leads back to; with neither, `starting`); after
`graph.session.completed` and until `/status` turns terminal it names the row the run
ended on. After the run the report states how many times the run went back, from
`/status.iteration` (zero-based: an extra pass advances it, a redraft does not), in
the report head bar and as the Session facts' pass fact (`passFact`,
`web/lib/format.ts`):

| `iteration` | `note_passes` | Text |
|---|---|---|
| 0 | 0 | `One research round` |
| 1 | 0 | `Went back once to fill gaps` |
| 2 | 0 | `Went back twice to fill gaps` |
| n > 2 | 0 | `Went back n times to fill gaps` |
| any | k > 0 | the above, plus ` · went back once / twice / k times for your notes` |

`note_passes` arrives with reader notes (live-briefs Phase 3) and reads as 0 until
then. The one rule the old counter taught still holds: `researcher.tool_call` carries
an `iteration` that is the ReAct step index, never the pass, so nothing reads the
pass from agent events.

```

- [ ] **Step 5: DESIGN.md §5.6, §5.7, §5.8, §6**

In §5.6 replace `\`--motion-fluid: 420ms\` for the row surface and the progress track. The easing is` with `\`--motion-fluid: 420ms\` for the row surface. The easing is`; replace `\`--motion-halo: 2200ms\` on the running node and the header status dot. It stops` with `\`--motion-halo: 2200ms\` on the running node, on each running topic's dot and on the header status dot. It stops`; and directly before the bullet that begins `- **One decorative loop, and it is not load-bearing.**` add

```markdown
- **Step briefs open, hand off and tick, once each** (live-briefs picks 1A, 3B).
  Only `transform`, `opacity`, `grid-template-rows` and the check's
  `stroke-dashoffset` animate. A row opens by `grid-template-rows: 0fr → 1fr` over
  `--motion-fluid`, then its lines rise 4px and fade in over 240ms from 280ms, 60ms
  apart; it closes by fading its lines out over 160ms, then closing the height over
  420ms from 160ms. At a hand-off the finished row's lines fade out (180ms, at 0),
  its height closes (at 100ms), its subtitle cross-fades to its outcome (200ms, at
  260ms) and the connector below it fills (`--fill-line`, at 180ms); the next row's
  node fills and its height opens at 600ms and its lines rise from 900ms, so the
  next row opens while the line is still filling (about 1.3s in all). When a route
  decision arrives ahead of the finishing row's own completion (Reviewing), the next
  row starts its opening at the decision and the row it left stays open until its
  completion, then folds with the same finishing-row timings. A finished
  topic draws its ✓ (`stroke-dashoffset` 14 → 0 over 360ms after 80ms) as its dot
  fades (200ms); counts tween to their new value over 400ms. **Under reduced
  motion** heights change at once, lines fade over 160ms with no stagger and no
  rise, the ✓ appears without drawing, counts jump and the halos are pinned at rest.
```

In §5.7 replace

```markdown
The stream is consumed, not rendered. The running stage shows what it derives
from it — the active pipeline node (with its one-line explanation), the stage
position, `pass p of P`, the progress bar, the two arcs and the loop tag, and the
counters block — and nothing else. A raw log is deliberately absent from the main
region.
```

with

```markdown
The stream is consumed, not rendered. The running stage shows what it derives
from it — the spine's row states, the open row's live brief (§3.4), each finished
row's outcome line and the two arcs — and nothing else. A raw log is deliberately
absent from the main region.
```

replace the paragraph from `What each surface derives, and from which events` through `\`graph.node.skipped\` and its Publishing row from \`graph.session.completed\`.` with

```markdown
What each surface derives, and from which events (`graph/events.py`,
`agents/*.py`): the active row from `graph.node.completed` and
`graph.route.decided`; the arcs from `graph.route.decided`,
`graph.extra_pass.started` and `graph.report.redraft_requested`, which also give a
reopened row its first line; Researching's checklist from
`planner.planning.completed.sub_topics` (titles, in plan order) and
`researcher.sub_topic.started` / `.completed` (by `coverage_id`), and its live facts
line from the completed topics' `successful_reads` and `findings_retained` until
`researcher.research.completed.findings`; the outcome lines from
`planner.planning.completed`, `researcher.research.completed`,
`source_evaluator.evaluation.completed`, `evidence_verifier.verification.completed`,
`report_writer.report.written`, `graph.report.reviewed` with `graph.route.decided`,
and Publishing's own `graph.node.completed`; the chip's step from the active row;
the failed stage's skipped rows from `graph.node.skipped` and its Publishing row
from `graph.session.completed`.
```

and replace `and a tail answers neither better than a stage spine with counters does. The` with `and a tail answers neither better than a stage spine whose running row carries its own counts does. The`.

In §5.8 replace

```markdown
The running stage carries a compact counters block inside the pipeline card,
below its spine in a footer section, with the eyebrow `counted from the event
stream` — a departure from spec R1's "under its header" placement, made because
under the header the block pushed the spine roughly 250px down at 1252×853,
putting both arcs below the fold and the whole spine off-screen on phone (§3).
Its rows, each with its scope:
```

with

```markdown
The running stage no longer carries the counters block (live-briefs D13,
2026-09-28): each row counts for itself, in its live subtitle while it runs and its
outcome line once it is done (§3.4). The Failed and Stopped stages keep the block,
with the eyebrow `counted from the event stream`, as what survived the halt. Its
rows, each with its scope:
```

replace

```markdown
Rules: a counter whose event has not arrived reads muted `not yet`, never `0`;
counters update once per node step, because that is how the stream delivers
events (§5.7); on `graph.extra_pass.started` the this-pass rows reset to
`not yet` and the block's caption reads `pass p`; on
`graph.report.redraft_requested` the current-draft rows and the review score
reset. The failed stage freezes the same rows at the halt, and a row whose node
never ran reads `not reached`.

The block is honest now where the earlier cost card was not: real values arrive
during the run, one node at a time, and each row names the pass or draft it
counts. Token usage is still `Not recorded`: totals are **not** in
```

with

```markdown
Rules: the block is derived from the same stream and the same handlers as the
running stage, frozen where the run stopped; a row whose node never ran reads
`not reached`, never `0`; on `graph.extra_pass.started` the this-pass rows reset
and the block's caption reads `pass p`; on `graph.report.redraft_requested` the
current-draft rows and the review score reset.

The block is honest where the earlier cost card was not: each row names the pass
or draft it counts. Token usage is still `Not recorded`: totals are **not** in
```

In §6's `| Running |` row delete `` `max_extra_passes` is on the stream but not on `/status`; ``.

- [ ] **Step 6: api-gaps.md 3.1**

Replace `E1, 1.1, 1.2 and 3.7 are closed` with `E1, 1.1, 1.2, 3.1 and 3.7 are closed`; delete the Stage 3 row that begins `| 3.1 | **\`max_iterations\` echo, partially closed** [1.7] |`; and add, as the last row of the "Closed since 2026-09-16" table:

```markdown
| 3.1 | `max_iterations` echo | obsolete 2026-09-28: the console no longer shows a pass ceiling or sends a budget (live-briefs D14, D15); `max_extra_passes` stays on the stream at `graph.session.started` |
```

- [ ] **Step 7: Check the record**

```bash
node -e "const fs=require('fs'); const d=fs.readFileSync('docs/design/DESIGN.md','utf8'); const g=fs.readFileSync('docs/design/api-gaps.md','utf8'); const gone=['pass p of P','progress track','\`extra passes 1\`','Running · pass','the loop tag\'s amber','| Extra passes |','\"max_iterations\": 1','passNumber','**Delivery is once per node step.**','the stage spine and the counters block','five-chip settings strip']; const want=['Rows that expand keep the connector on the nodes','What an open row says','A reopened row says why it reopened','### The passes, in plain words','Step briefs open, hand off and tick','**Delivery is live.**','aria-current=\"step\"','No pass counter']; console.log(JSON.stringify({left: gone.filter((s)=>d.includes(s)), missing: want.filter((s)=>!d.includes(s)), gapsStillListed: ['| 3.7 | **Live per-event delivery**','| 3.1 | **\`max_iterations\` echo'].filter((s)=>g.includes(s))}))"
```
Expected: `{"left":[],"missing":[],"gapsStillListed":[]}`.

- [ ] **Step 8: Commit**

```bash
git add docs/design/DESIGN.md docs/design/api-gaps.md && git commit -m "docs(design): the running stage's live briefs, no pass counter, no extra-passes control"
```

---

### Task 12: Full verification and the final visual review (AC1–AC9)

**Files:** none changed unless a check fails.

- [ ] **Step 1: The backend suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q && git diff 196dd3f1 -- src/deep_research/agents/prompts.py src/deep_research/api | head -5
```
Expected: `4785 passed, 1 deselected`, `0 failed` (without a `.env`: the `--deselect` of Conventions, `4784 passed, 2 deselected`); the diff prints nothing (no prompt text, no API code changed).

- [ ] **Step 2: The web unit layer**

```bash
cd web && npm run -s typecheck && npx vitest run && npm run -s check:css
```
Expected: no `typecheck` output; `Test Files 23 passed (23)`, `Tests 155 passed (155)`; `OK`. (Planning saw `test/stream.test.ts` "delivers frames as they arrive…" fail once in six runs of the unmodified repository; if a run fails there, rerun that file alone once — a second failure is real and is investigated, not retried.)

- [ ] **Step 3: Playwright, both projects**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e && VISUAL_CHECKPOINT=P1-T12-final DEEP_RESEARCH_PYTHON="$PY" npx playwright test --project=visual
```
Expected: `47 passed`, then `8 passed`.

- [ ] **Step 4: The final full-height review**

Open all fourteen images in `web/visual/P1-T12-final/` (Read tool), full height. For every capture: no sideways scroll; tokens only (no colour outside the palette); purple only on `Download Report`/the send button; one surface per region. `01-idle`/`07-idle-phone`: two composer pills, six starters, unchanged otherwise from `docs/design/reference/01-idle.png`/`07-idle-phone.png`. `02-submitted` (+ phone): the question and four chips. `03-running` (+ phone): as Task 9 Step 8 — the Researching brief open with at least one ✓ topic, connector node to node, chip `Running · Researching`, no Now/counters/pass text. `09-running-extra-pass` (+ phone): amber arc attached at both nodes; the amber reopen line; only the re-run topic. `04-report` (+ phone): head bar ends `Went back once to fill gaps`; (the Session facts row is asserted by `report.spec.ts`; a full-page capture clips the self-scrolling rail, `globals.css:317-325`). `05-failed`, `08-evidence` (+ phone): identical to `P1-T07-removals`. Compare the running captures with the canvas picks in `docs/design/running-stage-picks/` (`Main.dc.html`, `Briefs.dc.html` column C, `Handoff.dc.html` column B, `PhoneBrief.dc.html`) and with spec §4.3; the spec wins where they differ (concurrent-topic wording; `not yet`/`reading`). Attach the images and a one-paragraph verdict per capture to the task summary.

- [ ] **Step 5: Record**

In the task summary: the counts from Steps 1–3, the checkpoint folder names (`P1-T07-removals`, `P1-T09-briefs`, `P1-T10-motion`, `P1-T12-final`), whether Contingency C was needed (Task 2), the five new fingerprint values (Tasks 3–5) and Open issue O1 for the human.

---

## Acceptance-criteria coverage

| AC | Satisfied by | Proven by |
|---|---|---|
| AC1 every event once; `graph.node.started` before its node's events and completion; each `researcher.tool_call` before its topic's completed | Tasks 1–5 | `tests/test_graph/test_live.py` (Task 2); researcher live test (Task 3); `test_the_replay_runner_delivers_every_event_once_inside_its_own_node` (Task 6) |
| AC2 `sub_topics` with every `coverage_id` and title ≤ 160 | Task 4 | `test_the_planner_publishes_its_events_live_and_lists_the_planned_titles`, `test_planning_completed_caps_each_title_at_160_characters` |
| AC3 no `.pipe-now`, no `#runCounters`, no "pass" text; chip `Running · {step}` | Task 7 | `running-pipeline.test.tsx`, `status-chip.test.tsx`, `format.test.ts`, `running.spec.ts` |
| AC4 active row open with its brief; done rows show outcomes, reopen/close by click, Enter, Space, `aria-expanded` in sync | Tasks 8–9 | `brief-spine.test.tsx`, `briefs.test.ts`, `briefs.spec.ts` (AC4 test) |
| AC5 every title; marks and facts per §4.3; never `0` or `—` | Tasks 8–9 | `run-state.test.ts` (f), `briefs.test.ts`, `brief-spine.test.tsx`, `briefs.spec.ts` (AC5 test) |
| AC6 connector node to node ±1 px open/closed/animating; arcs ±2 px after a row opens | Task 9 | `briefs.spec.ts` (geometry at 1252 and 390; arc test); `loop-arc.test.tsx`; `brief-spine.test.tsx` (ResizeObserver) |
| AC7 motion timings recorded; reduced motion: no transform transition, instant heights | Tasks 9–10 | `motion.spec.ts` (both tests); `tween.test.tsx`; existing `reduced-motion.spec.ts` |
| AC8 no extra-passes control; no `max_iterations` in the POST; pass fact per §4.2 | Task 7 | `composer.test.tsx`, `settings.spec.ts`, `format.test.ts` (`passFact`), `report-rail.test.tsx`, `report-stage.test.tsx`, `report.spec.ts` |
| AC9 no horizontal scroll at 1252 and 390; existing layout, handoff, report-slide and reduced-motion tests pass after the §6 updates | Tasks 7–12 | `npm run test:e2e` (`47 passed`), `layout.spec.ts`, `visual.spec.ts` (`shoot` asserts no sideways scroll) |

## Self-review

**1. Spec coverage (Phase 1).** E1 → Task 1. E2 → Task 2 (R1 first, contingency written). E3 → Tasks 2 (`graph.node.started`), 3 (researcher, `coverage_id`, tool calls from the step callback), 4 (planner, titles), 5 (evaluator, verifier, writer). E4 → Task 6 (the runner's paced queue receives live events through its handler — no replay code changes; proven on the real graph) and the fixture regeneration. E5 → Task 6 (api-gaps 3.7, DESIGN §5.7 delivery) and Task 11. §4.2: `.pipe-now` and `<Counters>` removed from `RunningPipeline`, `aria-current="step"` (Task 9), chip `Running · {step}`, extra passes removed everywhere named (`DEFAULT_SETTINGS`, `#pillExtra`, popover stepper, strip chip, `SubmittedSettings.extraPasses`, `ceiling()` and the `passes` plumbing), `buildRequest` without `max_iterations`, report pass fact with `note_passes` treated as 0 → Task 7. §4.3: row anatomy, pending/active/done rules, reopen by button and keyboard, brief content table, concurrent-topic checklist with waiting/running/done, pages and findings sums, reopened rows for extra pass and redraft, return arcs kept, motion table, reduced motion, connector geometry, arc `ResizeObserver` + `transitionend`, RunState changes, the seventeenth handler and the exact-keys update, burst-safety → Tasks 8–10. §4.9 Phase-1 rows → Tasks 6 and 11 (plus the rows listed in ambiguity 11). §6 Phase-1 tests: every "pytest: events" item is in Tasks 1–6; Vitest items in Tasks 7–9; Playwright items in Tasks 7, 9, 10; every "Existing tests to update" entry is updated in the task that removes its UI (`running.spec.ts:19,29`, `arcs.spec.ts`, `running-pipeline.test.tsx`, `status-chip.test.tsx`, `composer.test.tsx` in Task 7; `run-state.test.ts:29-38` in Task 8; `visual.spec.ts:31` in Tasks 7 and 9; `layout.spec.ts:38-39` kept green by `ol#spine > li[data-stage]`; `session-screen.test.tsx:117-126` in Task 9, and its pre-existing C1 race, `:80-81`, in Task 7). Visual captures after each major UI change with new checkpoint names → Tasks 7, 9, 10, 12. R2 → Task 1 (the one affected test, found by running the suite). R3 → Task 3. Phase 2/3: nothing planned; the brief keeps its reason line and body separate so acknowledgement lines can be prepended, and the card has room for a note line after `BriefSpine`.

**2. Placeholder scan.** No "TBD", "TODO", "similar to", "add validation". Every code step shows the code; every check shows its command and expected output. Each re-pinned fingerprint is computed and written by the `repin` snippet, and the value the planning dry run produced from this exact text is given as the expected output.

**3. Type consistency.** `Handoff { from, to, awaiting }` is local to `BriefSpine.tsx` (Task 9); `ResearchEvent.event_id: string` (Task 7) matches the payload of E1. `publish_live(event) -> None` in `graph/live.py` (Task 2) is what `agents/events.publish_live` calls (Task 3) and what Tasks 4–5 import; `bind_live_sink` is what every live test uses; `tool_call_event(sub_topic, step)` (Task 3) is what `tool_call_events` and the step callback call. On the web: `SessionView.step`, `toSessionView(s, step)`, `statusNote`, `passFact`, `stepLabel`, `chipStep` (Task 7) are what `SessionScreen`, `ReportRail`, `ReportStage` and the tests use; `Topic`, `TopicState`, `PlannedTopic`, `ReopenLine`, `RunState.plan/topics/pagesRead/findingsSoFar/passFindings/reopen/outcomes/open`, `countPhrase`, `toggleOpen` (Task 8) are what `lib/briefs.ts` imports and `BriefSpine`, `SessionScreen` and the tests use; `RowState`, `Subtitle`, `RowBrief`, `rowBrief`, `subtitleText`, `STATIC_META`, `SENTENCES`, `topicFact`, `verifyingSentence` (Task 8) are what Task 9 renders and tests; `useTween`, `TWEEN_MS`, `tweenValue`, `drawLoopArc`, `useLoopArc`, `HANDOFF_HOLD_MS`, `BriefSpine({ marks, run, onToggle })`, `RunningPipeline({ …, onToggleRow })` match across Tasks 9–10 and their tests; `connectorOffsets` (Task 9) and `installMotionRecorder`/`motion`/`MotionRecord` (Task 10) match their specs.

**4. Review Focus.** (1) Task 3 Step 1's import guard; (2) Task 2 Step 4 and Task 6 Step 1; (3) Task 2 Step 4; (4) Task 2 Step 3 with Contingency C; (5) Task 3 Step 1; (6) Task 8 `(i)`; (7) Task 9's hand-off test and Task 10's recorder; (8) Task 9's geometry e2e.

**5. The dry run (see Evidence) and what it changed.** Every correction below is already applied in the tasks above:
- Two anchors were partial lines an engineer could not match exactly — the `report_reviewer` line in `run-state.ts` (it carries a trailing comment) and the C2 block in `session-screen.test.tsx` (it ended mid-comment); both are now whole-line anchors.
- The `test_replay.py` imports now extend the two existing import lines instead of adding duplicate ones.
- The full-suite total is `4785` (59 new tests), not `4786`; the pass counts of every targeted run are now the observed ones.
- The C1 race fix moved from Task 9 to Task 7: it failed at `7afb21de` itself under full-suite load, so Task 7 is the first task that can expect the whole suite green, and Task 6 Step 3 runs only the capture reader.
- The import-cycle expectation now names the module that actually breaks (`planner.py`; in `researcher.py` the same import happens to work).
- The failure texts quoted in Task 2 Step 5, Task 7 Step 2 and Task 8 Step 2 are the ones the runs printed.
- DESIGN.md's new "No pass counter" paragraph no longer trips Task 11's own check.
- The spine rows carry the spec's `spine-row` class, asserted in `brief-spine.test.tsx`.
- The repin steps state the expected pins, and Tasks 4 and 5 carry the whole snippet, so each task stands alone.
- Contingency C now keys the sink by session id on the `Tracker` and is written as exact code: a single `live_sink` attribute would cross concurrent runs that share one `Tracker`, as the tests' `tracker` fixture does. It was checked by the simulation described under Evidence.
- A worktree's missing `.env` is handled explicitly (Conventions); a "watch it by eye" step was replaced by the recorder's check; the Task 9 edits that follow Task 7's now quote Task 7's lines; three citations were corrected (`run-state.ts` active-row and re-arm lines, `#reportMeta`'s place in the verbatim block).

## Review round 1 (2026-09-28): findings and how each was resolved

Verdict APPROVED WITH CHANGES, no P1. Every change below is applied in the tasks above and was dry-run as described under Evidence.

- **P2-1 — the Reviewing → Publishing hand-off never got `from`.** Confirmed: `graph.route.decided` (finalize) moves the active row before Reviewing's own completion, and in replay the two render 150 ms apart. Fixed in Task 9 as suggested — `from` is also derived from the mark transition of the row the active row left (an `awaiting` field beside `from`/`to`; the hold restarts) — plus one addition the suggestion alone does not reach: until its completion, the awaited row keeps painting as the active row, open (and without `aria-current`, which stays on the true active row). Evidence: with the mechanism alone, Reviewing was painted `pending` at the route decision (`brief-spine.test.tsx`: `expected 'pending' to be 'active'`) and the new `motion.spec.ts` check failed at "nothing on Reviewing's row moves between its `to` and `from` transitions"; with the addition both pass. The `motion.spec.ts` block cannot mirror the researcher's line-for-line: a recorder trace showed Reviewing active for about 600–750 ms in replay, less than its own 900 ms line-rise delay as the `to` row, so its lines never rose and have nothing to fold (its height folds only when its 600 ms opening had started). The block therefore requires the subtitle cross-fade (`260/200`) and the connector fill (`180/620`) under `from`, and holds any line or height fold to `0/180` and `100/420`. Counts: Vitest `153 → 155` (two tests in `brief-spine.test.tsx`); Playwright unchanged (`47`).
- **P3-1 — `tests/test_imports.py:488` lists the graph submodules.** Task 2 Step 6 adds `"live"`; `live.py`'s three public names are exported in the same step (`26 passed` in the dry run).
- **P3-2 — `web/lib/api.ts` `ResearchEvent` lacked `event_id`.** Task 7 Step 3 adds `event_id: string`; the type is only cast from parsed JSON, so no literal changes (typecheck clean).
- **P3-3 — `test_topic_findings_sum_matches_research_total` asserted `passes >= 1`.** Now `pytest.skip`s a case that completes no research pass. None does today, so the count stays `41 passed` with no skips.
- **P3-4 — the recorder fell back to index 0 for longhand properties.** Task 10 maps `background-color` to `background` and `border-*-color` to `border-color` before indexing the element's transition list, and records `-1/-1` for a property the list does not name, so nothing can borrow another property's timing.
- **P3-5 — fingerprint expectations.** Tasks 3–5 keep the dry run's pin as the expected output and accept a different value when `git diff` of the module shows only that task's edits and the task's tests pass.
- **P3-6 — the api-gaps header is edited by Tasks 6 and 11.** No text change needed; the strict 1 → 12 order is now stated in Global Constraints and in the Execution notes, naming this coupling.
- **Orchestrator note 7 (O1).** Left as resolved: the outcome is computed and unreachable; the human decides.
- **Orchestrator note 8.** Every canvas and brief reference now points at `docs/design/running-stage-picks/` (byte-identical to the old `.superpowers/` files, checked).
- **Orchestrator note 9.** Conventions give the Linux form of every command: the venv and `PY="$PWD/.venv/bin/python"`, the `--deselect` wherever no `.env` exists (never read or create one), `npm ci` plus the Playwright browser install, LF line endings. Found while doing this: `launch.mjs stop` kills with `taskkill`, which Linux lacks, so Task 6 Step 2 now ends the API's process group itself on Linux; otherwise the capture's API would keep port 8010 and every later Playwright run would fail to start.

## Execution notes for the controller

- Recommended: **subagent-driven**, one fresh implementer per task, a reviewer gate after each; tasks strictly in order 1 → 12 (Tasks 3–5 share `tests/test_evaluation/test_config.py`; Tasks 7–10 share `SessionScreen.tsx`, `run-state.ts`, `globals.css` and `e2e/support.ts`; Task 11 rewrites the api-gaps header sentence Task 6 wrote).
- Tasks 1–6 need only the venv; Tasks 7–12 need `web/node_modules` (`cd web && npm ci` once in a fresh worktree or checkout, plus `npx playwright install --with-deps chromium` on Linux) and free ports 8010, 3010, 3011 for Playwright. Windows only: OneDrive can lock `.next` during `next build` (`EPERM`/`EBUSY`); pause syncing for the command, as `web/README.md` says.
- On Linux (the cloud checkout), apply the Conventions throughout: `PY="$PWD/.venv/bin/python"`, the `--deselect` of the `.env`-dependent test in every full-suite run, and the process-group `kill` before `launch.mjs stop` (Task 6 Step 2 carries it). The canvas picks and the design brief are in `docs/design/running-stage-picks/`.
- If Task 2's R1 test fails, the implementer applies Contingency C inside Task 2 and says so; later tasks are unaffected (every call site still calls `publish_live`).
- Open issue O1 goes to the human with the Task 12 summary; it does not block any task.

## Review round 2: findings and how each was resolved

Fable 5.1 re-review of `03453dec`: **APPROVED**. Four P3s, applied by the orchestrator:

- **P3-A — Linux venv line.** The Conventions no longer hard-code `python3.11 -m venv`. They defer to `cloud-session/setup.sh` (which picks `python3.12 || python3.11 || python3`) and only create `.venv` when `.venv/bin/python` is missing.
- **P3-B — the awaited hand-off lapsing with the hold timer.** `BriefSpine`'s `[handoff]` effect now skips the timeout while `handoff.awaiting && handoff.from === null`. The awaited row therefore gets `from` however long its completion takes (for example with a large `--replay-delay-ms`). The Task 9 tests are unaffected: their awaited case completes within the hold, and the timeout still clears the roles once `from` is set.
- **P3-C — two rows `data-state="active"` for one paced event.** No change. Today's locators (`web/e2e/running.spec.ts:18`, `:35`) sample during Researching. New tests that need "the active row" should use `[aria-current="step"]`.
- **P3-D — DESIGN.md §5.6.** Task 11's hand-off bullet now records the two-moment case: a route decision leads, and the row it leaves stays open until its own completion, then folds with the `from` timings.

