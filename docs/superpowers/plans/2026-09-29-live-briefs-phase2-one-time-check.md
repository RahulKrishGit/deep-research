# Live briefs, Phase 2 — the one-time check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the research question leaves something material open, ask the reader at most three questions before planning — one at a time on page 2, in the pipeline card's place — and start the run on the answers, or on best guesses after 60 s; a settings row turns the check off.

**Architecture:** The API session gains one pre-run phase. `SessionStore._run` asks an injected `ClarityChecker` (live: the configured provider, thinking disabled, structured output; replay: a scripted checker that asks a fixed set only when the request carried `X-Replay-Clarify: on`). With questions, the session reads `needs_input`, publishes `session.clarification.requested`, waits up to `hitl.answer_wait_s` for `POST /research/{id}/answers`, publishes `session.clarification.answered` and calls the runner with `reader_answers`. The engine carries `reader_answers` into `ResearchState`; the planner reads them into its answer contract and, only when there are any, into a `# Reader answers` request section, so every existing request and replay packet stays byte-identical. The web app adds the `needs_input` status, a burst-safe `RunState.clarify`, a pure `lib/clarify.ts` and a `ClarifyStage` that sits between the Submitted beat and the running stage.

**Tech Stack:** Python 3.12 (`.venv`), FastAPI, pydantic 2, LangGraph 1.2.10, pytest + pytest-asyncio; Node v22, Next.js 16 (Turbopack), React 19, Vitest + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode. Linux only: every command below is for the cloud VM.

**Spec:** `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md` — Phase 2 only: §4.4, §4.5, the Phase-2 rows of §4.8 and §4.9, AC10–AC14 and the Phase-2 parts of §6. Decisions D4–D7, D16 and D17 are closed; nothing here reopens them. §7 lists no risk that concerns Phase 2 (R1–R3 and R6 are Phase 1's, R4–R5 Phase 3's); this plan's own risks are in Review Focus and Open issues. Phase 3 (reader notes) is **not** planned here: `hitl.note_interpret_timeout_s` is added only because §4.4 puts it in the same config block.

**Starting point: the end of Phase 1.** This plan runs after `docs/superpowers/plans/2026-09-28-live-briefs-phase1-live-progress-and-briefs.md` is complete. For files Phase 1 edits, the anchors below are the text Phase 1 writes, and each such edit is marked "(anchor as written by Phase 1 Task N)". By the time this plan was finished, Phase 1 Tasks 1–11 and a first review fix were committed, at `3565397`. That commit is the state every anchor, line number and baseline below was checked against; Phase 1 Task 12 is verification only. Task 1 checks every anchor again before anything is edited, in case a later Phase 1 fix moves one.

**Evidence.** Planning dry-ran every task on 2026-09-29, in scratch exports, applying each edit by its anchor with a script that stops unless the anchor occurs exactly once:
- **Backend, Tasks 2–5**, step by step, on an export of `eecc67a` (Phase 1 Tasks 1–8). Its backend is exactly `3565397`'s: `git diff --stat eecc67a 3565397 -- src tests config.yaml` is empty. Every fail-first and pass step was run and its Expected line observed, ending at `4831 passed, 6 skipped, 12 deselected` from a baseline of `4768 passed, 6 skipped, 12 deselected`.
- **Web, Tasks 6–9**, step by step, on that export plus Phase 1 Task 9's files, which were still under review then and later committed unchanged at the anchored lines.
  - `tsc`, Vitest (`155` → `186` tests) and the CSS check ran at every step.
  - Playwright `chromium` ran `settings.spec.ts` (2 passed), `clarify.spec.ts` (7 passed) and then the whole project (`53 passed`: that state's 45 plus these 8). `visual` ran as well (`10 passed`).
  - Both Playwright runs used ports moved off the shared ones, so the Phase 1 agents' own runs were not disturbed.
- **Docs, Tasks 5 and 10**, against HEAD, and against a copy of `DESIGN.md` carrying Phase 1 Task 11's edits.
- **This document, at the end of Phase 1.** Planning parsed this document's own edit blocks, exactly as an implementer reads them, and ran them on an export of `3565397`.
  - Task 1 Step 2's check printed `anchors: 141 exactly once; creates: 11 absent; appends: 8 present`.
  - Applying every block in order, with Task 3's re-pin snippet, gave `planner: d40ffac43845 -> e9b74316ad14`, `4831 passed, 6 skipped, 12 deselected`, a clean `tsc`, Vitest `25` files and `186` tests, and `check:css` `OK`.
  - It also gave Playwright `chromium` `55 passed` and `visual` `10 passed`. Planning reviewed `10-clarify` and `10-clarify-phone` full height, and saw that `01-idle` and `03-running` are unchanged from Phase 1.
  - Its eleven new files are byte-identical to the step-by-step dry run's.

## Global Constraints

- **Where.** Branch `feat/live-briefs-and-reader-notes`, the Linux cloud checkout; every path is relative to the repository root. Tasks run **strictly in order 1 → 11**, one at a time. Commit after every task that changes files (Task 1 changes none).
- **No live model, no secrets.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest or the API in `--mode replay`. The live checker is exercised only through a scripted completer (`RecordingCompleter`, Task 4). Every API test replaces it with a recording fake (`tests/test_api/conftest.py`, Task 5).
- **Byte-identical without answers (spec §4.4, Replay row).**
  - `agents/prompts.py` is not edited.
  - The planner adds `# Reader answers` only when there are answers.
  - `tests/test_agents/test_planner_reader_answers.py::PINNED_PACKETS` pins the planner's three request packets and the reviewer's packet on the extra-pass replay case, as they are at the start of Phase 2.
  - The full replay suite passes unchanged.
  - The planner's agent-fingerprint pin moves, because the module's code changes. It is re-pinned in Task 3, and no other pin moves.
- **API contract (spec §4.4), exactly:**
  - `ResearchRequest.ask_clarifying_questions: bool = True`.
  - `SessionStatus` gains `"needs_input"`. It is non-terminal.
  - Config block `hitl: {check_timeout_s: 20, answer_wait_s: 60, note_interpret_timeout_s: 15}`.
  - Event `session.clarification.requested` has metadata `{questions, deadline_at}`, where `deadline_at` is an ISO time, now + `answer_wait_s`.
  - Event `session.clarification.answered` has metadata `{answers: [{question_id, value, source}], reason}`, with `source` ∈ `chosen | typed | best_guess` and `reason` ∈ `answered | skipped | timed_out`.
  - `POST /research/{session_id}/answers` with body `{"answers": [{"question_id", "choice" | "text"}], "skip": bool}` returns `202` with the session response. It returns `404 session_not_found`, `409 not_waiting_for_input`, or `422 validation_error`.
  - Header `X-Replay-Clarify: on`.
  - Questions: 0–3 per check, ids `q1`–`q3`, each with 2–4 options and a `best_guess` that is one of them.
- **Web copy, verbatim (spec §4.5; `·` is U+00B7 with a space each side, `…` is U+2026):**

  | Where | Text |
  |---|---|
  | Chip | `Waiting for you · a few quick questions` on `.dot-warn` |
  | Eyebrow | `Before we start` |
  | Step line | `Question {i} of {n}` |
  | Best-guess cap | `best guess` |
  | Last option | `Other…` |
  | The field | label `Your own answer`; placeholder `Type your own answer`; button `Next` |
  | Footer | `Back` · `Skip this one` · `Just start` |
  | Countdown | `Starts with best guesses in {m}:{ss} if you don't answer` |
  | Summary | `Starting research with: {short}: {value} (you said)` or `… (best guess)`, joined by ` · ` |
  | 409 face | `Already started with best guesses` |
  | Failed POST face | `Your answers could not be sent; the run starts with best guesses.` |
  | Settings row | `Ask me when the question is unclear`, with `On` / `Off` |

  Replay's fixed set (pick 4B's copy):

  | Id | Short | Question | Options | Best guess |
  |---|---|---|---|---|
  | `q1` | `Region` | `Which region should this cover?` | `United States` · `European Union` · `Global` | `Global` |
  | `q2` | `Period` | `How recent should the sources be?` | `Last 12 months` · `Since 2023` · `Any time` | `Since 2023` |
  | `q3` | `For` | `What will you use it for?` | `General understanding` · `A project or investment decision` · `Policy or regulation work` | `General understanding` |
- **Theme (D17; `docs/design/running-stage-picks/BRIEF.md` "Theme rules").**
  - Tokens only.
  - `web/app/globals.css` lines 1–1131 stay the prototype's CSS verbatim: `cd web && npm run -s check:css` prints `OK`.
  - New rules go at the very end of the file, after Phase 1's, inside the app-only section (`/* ═══ 2026-09-27: app-only additions ═══ */`), with no colour literal.
  - No purple on the check card: no `.btn-primary`, and no accent at rest. The one exception is Other…'s text field while it has focus, which shows the theme's focus ring (spec §4.5; the picks' theme rule 4; ambiguity 26).
  - One surface per region. The card is the only box. The answers are controls outlined by `--border`, the footer sits under a `--border-soft` hairline, and nothing is nested.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone; captures are `fullPage: true`.
- **Out of Phase 2.** No reader notes (no `/notes` route, no `note_passes`, no note line, no interpreter). No change to the running stage's briefs. `docs/design/prototype/` and `docs/design/reference/` are not changed.

### Conventions every task uses

- **Shell.** Every block runs in bash from the repository root. No shell state survives between blocks: every block that runs Python sets `PY` first, and every `web/` block starts with `cd web`. Line endings are LF.
- **Python.** `PY="$PWD/.venv/bin/python"` (the cloud setup created the venv).
  - The pytest line is `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest <files> -q`.
  - Four tests cannot pass on this VM, and full-suite runs deselect them:
    - `tests/test_config.py::test_the_evidence_verifier_pipeline_config` needs a `.env`, which does not exist here and must not be created.
    - Three Windows-only path tests in `tests/test_evaluation/test_config.py`.

    Every full-suite block sets:
    ```bash
    DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
    ```
    and runs `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT`. That takes about 150 s. `12 deselected` is the `.env` test, the ten cases of the three Windows-only tests (they are parametrized), and the one `live` test that `pyproject.toml` deselects (`addopts = "-m 'not live'"`).
- **Web unit tests.** `cd web && npm run -s typecheck` (Vitest's files are type-checked too), `cd web && npx vitest run [files]`, `cd web && npm run -s check:css`.
- **Playwright.** The API runs in replay mode as a Playwright `webServer` (`web/playwright.config.ts`), serving this checkout's backend on port 8010, with the app on 3010 and an app pointed at a closed port on 3011.
  - Set `export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"` at the repository root before `cd web`.
  - Build first, and put spec files **before** the project flag, because `--project chromium` placed first swallows the file arguments: `npm run -s build && npx playwright test e2e/a.spec.ts --project=chromium`.
  - The whole project: `npm run -s test:e2e`.
  - Captures: `VISUAL_CHECKPOINT=<name> npx playwright test --project=visual`, with the images in `web/visual/<name>/` (gitignored).
  - Chromium is installed under `$PLAYWRIGHT_BROWSERS_PATH` (`/opt/pw-browsers`). If a run reports a missing executable, run `npx playwright install chromium` once.
  - Ports 8010, 3010 and 3011 must be free before a run: `ss -ltn | grep -E ':(8010|3010|3011) '` prints nothing. A leftover server makes the `webServer` start fail.
  - No step here keeps a server running across blocks. If one is ever started with `web/scripts/launch.mjs`, note that its `stop` uses Windows `taskkill`. On Linux, first end the recorded process group: `kill -- -"$(cat .launch-<name>.pid)"`. `stop` then only confirms that the port is closed.
- **Edits.** Every edit is "replace this exact text with that text", and Task 1 has proved that each anchor occurs exactly once. If an anchor is not found when you reach it, stop and report it. Never improvise a nearby match.
- **TDD boundary.** pytest and Vitest tests are written first and shown failing. Playwright specs are verification, written after the code they exercise.
- **Commits.** Use `git add <paths> && git commit -m "<type>(<scope>): <what>"`, never `git add -A` (`web/visual/`, `web/.e2e-tmp/` and `web/test-results/` are gitignored). Append the attribution trailer your session requires, and push if your session's rules say to.

## Review Focus

1. **An answer that arrives late, twice, or from a second tab.** The API must take one set of answers, once, and only while the session waits; the run must never start twice or on a second set. A reader who taps the last answer as the minute ends sees `Already started with best guesses`. → Task 5 `test_answers_are_taken_once_and_only_while_the_session_waits`, `test_answers_after_the_deadline_are_refused`; Task 8 `an answer the API refuses as late reads 'Already started with best guesses'`.
2. **A checker that hangs, fails or answers nonsense** — provider down or slow, invalid JSON, a best guess that is not an option, four questions. The run must start with no questions and never wait longer than `check_timeout_s` (AC14). → Task 4 `test_an_invalid_draft_asks_nothing_at_all` (7 cases); Task 5 `test_a_check_that_hangs_delays_the_run_by_at_most_check_timeout_s`, `test_a_failed_or_empty_check_starts_the_run_exactly_as_before`.
3. **The stream ending while the session waits.** `needs_input` is not terminal. Code that reads "not `running`" as "finished" would stop reconnecting and never see the run start; the e2e helper `waitTerminal` had exactly that bug. → Task 9 `keeps the stream open while the session waits: an ended stream reconnects, as for a running one`, plus the `waitTerminal` fix and the AC11/AC12 e2e.
4. **A reload, or a late subscriber, during the check.** The card must rebuild from the stream alone, with no Submitted beat replayed and no stale summary. → Task 6 `(j) … is burst-safe`; Task 9 e2e `a reload while the check waits rebuilds the card from the stream (§4.8)`.
5. **Typed answers at the edges** — spaces only, over 200 characters, both a choice and text, an answer to a question that was never asked. → Task 4 `test_an_answer_carries_exactly_one_of_choice_or_text` (5 cases) and `test_answers_that_do_not_fit_the_questions_name_each_problem`; Task 5 `test_answers_that_do_not_fit_are_a_safe_422_and_the_session_keeps_waiting` (5 cases); Task 8 `opens Other… on a text field; Next waits for text; Enter moves on`.

## Spec ambiguities resolved here

**API and engine**

1. **`ResearchSettings` (§4.4's `ClarityChecker` type) is `ConfigSettings`** (`src/deep_research/utils/config.py`); the codebase has no `ResearchSettings`. The checker receives the request's own settings, with overrides applied, as returned by `preflight` (`api/app.py`).
2. **`ReaderAnswer` gains `short`**, beyond the spec's `{question_id, dimension, text, value, source}`. The spec's own assumption line, "Reader said: {short} = {value}", needs the label, and the planner has no other way to reach it.
3. **Which checker runs is decided by the API mode, not by configuration.** `create_app(mode="live")` defaults to `live_clarity_check` and `mode="replay"` to `scripted_clarity_check`. A replay server therefore cannot reach a provider, even with the header set.
4. **The test guard.** Every live-mode test app built without a `clarity_checker` would reach the live checker. `tests/test_api/conftest.py` replaces `live_clarity_check` with a recording fake for the whole package (autouse). `valid_preflight` returns `ConfigSettings()`, so the route can read `settings.hitl`.
5. **The live check call**:
   - thinking is disabled (`clarity_llm_config`);
   - `agent_name=None`, so no per-agent model override, effort or budget applies;
   - `max_tokens=4096`;
   - tracing is off (its own `Tracker`);
   - it sits outside the run's request-attempt budget, because the runner creates that budget later (see O1).
6. **An invalid provider output is dropped whole** (the spec's "the output is dropped"). Beyond the spec's three cases, "invalid" also covers:
   - fewer than 2 or more than 4 options;
   - a repeated option;
   - an option over 80 characters;
   - `text` over 200 characters or `short` over 40;
   - an empty field.

   The size caps are this plan's. The spec fixes only the counts, and the caps keep the card's layout bounded.
7. **The wait honours a boundary answer.** `SessionStore._clarify` waits with `asyncio.wait`, not `wait_for`, so an answer that lands at the last instant is used, not cancelled. The route refuses by wall clock (`now ≥ deadline_at` → 409), so no answer is accepted after the session stops waiting.
8. **The answers route's `202` body is the snapshot at acceptance.** `status` still reads `needs_input` until the session's own task applies the answers, an instant later. The web app does not read the body.
9. **A session cancelled during the wait** (service shutdown) ends as `running` with `finished_at` set: the existing "service stopped" reading. It never ends as `needs_input` with `finished_at`.
10. **The runner receives `reader_answers` only when questions were asked.** Otherwise it is called exactly as before, so every existing runner fake and every replay packet is untouched.
11. **The 422 issue types.**
    - Shape errors keep pydantic's own types: neither or both of `choice` and `text`, blank text, text over 200 characters, more than three answers.
    - Fit errors get three types of this plan's, each located at `body.answers.{i}.question_id` or `body.answers.{i}.choice`: `unknown_question`, `duplicate_question` and `choice_not_offered`.
    - The session keeps waiting after a 422.
12. **What the planner does with the answers** (§4.4 table):
    - The last `geography` answer sets `geographic_scope`. The "names no geography" assumption is dropped only when the scope had been `unspecified`.
    - The last `period` answer becomes `evidence_period_requirement`, verbatim.
    - `scope_statement` is recomposed from the new values.
    - `purpose` and `scope` answers become assumption lines only.
    - `# Reader answers` goes into the plan request and the plan-review request, which are the spec's `plan_messages` and `plan_review_messages`. It does not go into the scoping (react) turn.

**Web**

13. **Replay's fixed set uses pick 4B's copy** (`docs/design/running-stage-picks/Hitl1.dc.html`, column B): the table under Global Constraints. `Hitl3.dc.html` is the reader-notes pick (D8) and belongs to Phase 3, so no part of this plan follows it.
14. **How the three footer actions post.**
    - "Skip this one" clears that question's pick and moves on; on the last question it posts.
    - "Just start" posts the picks so far with `skip: true`, and the reason is `skipped`.
    - Finishing the last question posts with `skip: false`, and the reason is `answered`, even when every question was skipped one by one.
15. **The countdown reads `m:ss`.** The spec writes "0:{ss}", which cannot show the first second of a 60 s wait; `m:ss` reads `1:00` there and `0:42` later.
16. **The summary is the spec's single line.** The pick's "Start over" button and its "changeable any time" line are dropped (D7: no assumptions UI).
17. **A POST that fails for any reason other than 409** (network error, 5xx) shows `Your answers could not be sent; the run starts with best guesses.` It is never retried. The stream still moves the stage on when the wait ends.
18. **The stage's eyebrow is `Before we start`** (the pick), and the stage has no elapsed line, like the Submitted stage whose header it continues. The running stage adds its elapsed line when it takes over, as it already does after the Submitted beat. The locked question never moves across either hand-off: Task 9's e2e measures it.
19. **Step dots.** Only the current question's dot is `--fg`; the others are `--border`.
20. **Captions on the options.** The best-guess option always carries its `best guess` cap; "Other…" carries none. The placeholder is `--meta` (the pick's `theme.css:282`).
21. **The check's face comes from the stream first.** `checkPhase` (`web/lib/clarify.ts`) returns:
    - `asking` from `session.clarification.requested` until `.answered`;
    - `starting` from `.answered` until the pipeline begins, which is the first `graph.node.started`, row mark or `graph.session.completed`;
    - `asking` for a `needs_input` status alone, before the stream has said anything about the check;
    - `null` otherwise.

    §4.5 selects the stage on `status === "needs_input"`, but `/status` is read only at mount and after the stream ends, so the stream decides once it has spoken.
22. **`isLive(status)` answers "is this session still in progress?" everywhere.** It is true for `running` and `needs_input`. It is used by:
    - the stage switch;
    - the reconnect loop;
    - the Submitted-beat flight guard;
    - the sidebar's live mark (the spec's §4.5 Sidebar line);
    - `ConsoleProvider`'s 5 s list poll, without which a waiting session's mark would never clear.
23. **The chip's status follows the check's phase while the session is live.** `asking` reads `needs_input` and anything else reads `running`: `toSessionView` gets the phase's status, because the stream is newer than the last `/status`.
24. **`HitlConfig` values are floats** (`gt=0`): YAML's `20`, `60` and `15` load as `20.0`, `60.0` and `15.0`.
25. **The settings strip gets no chip for the new setting.** DESIGN.md §3.0 lists exactly four facts after Phase 1, and D16 names only the settings row.
26. **The focus ring on Other…'s field.** §4.5 asks for "the focus ring on focus", and the picks' theme rule 4 says the same: "The edit affordance appears only on hover (a `--border-soft` background) or on focus (the standard focus ring)" (`docs/design/running-stage-picks/BRIEF.md:109-125`). The theme's only ring is `--focus-ring`, which is accent (pick `theme.css:188`). D17's "purple only on the primary button" is therefore read as "at rest": the card carries no purple unless the field has focus.
27. **Hover and pressed colours change over `--motion-fast`.** The answers do this exactly as the prototype's `.btn` does (`web/app/globals.css:659`) and as the pick's own `.choice` does (`theme.css:270`). Theme rule 6 ("animate only transform, opacity and `grid-template-rows`") is read as governing movement. The only movement on the card is the next question's and the summary's fade-in (`enter`: opacity and a 6px rise, opacity only under reduced motion).

## Open issues (for the human)

- **O1 — The check call is outside the run's request-attempt budget and its telemetry.** The spec is silent. The run's budget (`request_budget.py`) and telemetry collector are created by the runner, after the check. The call is one structured request of at most 4096 output tokens with a 20 s timeout. Nothing here is blocked. Counting it would need the budget created before the check, which is a spec change.
- **O2 — A live session reads `running` until its questions arrive.** While the live check call runs (up to 20 s):
  - `/status` is `running` and the stream carries nothing, so the page shows the running stage with Planning active;
  - if questions come back, the card replaces the pipeline card.

  A "checking" status or event would avoid the flash, but §4.4 defines only the two events and `needs_input`. Replay's checker answers at once, so no test sees the flash. The least surprising reading of the spec is kept.
- **O3 — AC12's literal "60 s + 2 s" is proven in parts.**
  - The default is pinned: `ConfigSettings().hitl.answer_wait_s == 60.0` (Task 2).
  - The timeout path is tested with short waits in pytest (Task 5) and with `answer_wait_s: 4` end to end (Task 9).

  No test waits a real minute.
- **O4 — The live structured call is [INFERENCE].** `ClarityCheckDraft` goes through the configured provider's structured-output path, like every agent's draft. The DeepSeek adapter sends `schema.model_json_schema()` (`providers/deepseek_provider.py:1964`), and agent drafts already carry list defaults (`ReportReviewDraft.defects`, `agents/report_reviewer.py:1098`). No test may call a provider, though.

  If the provider rejected the schema, the failure would be safe: the run starts with no questions (AC14). But it would make the check silent in live mode. What settles it is one live `POST /research` by the owner with a question that names no region, which is outside this plan.

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `config.yaml`, `src/deep_research/utils/config.py` | the `hitl` block; `HitlConfig` on `ConfigSettings.hitl` | 2 |
| `src/deep_research/utils/types.py` | `ClarityDimension`, `ReaderAnswerSource`, `ReaderAnswer`; `ResearchState.reader_answers` and its update key | 2 |
| `src/deep_research/graph/state.py`, `graph/orchestrator.py`, `main.py` | carry `reader_answers` from `run_research` into the initial state | 2 |
| `src/deep_research/agents/planner.py`, `agents/__init__.py` | answers into the answer contract and a `# Reader answers` section; `render_reader_answers` | 3 |
| `src/deep_research/api/clarify.py` (new) | the check's contract and prompt, the live and scripted checkers, answer resolution, the two events | 4 |
| `src/deep_research/api/models.py` | `needs_input`, `ask_clarifying_questions`, `ClarificationAnswer`, `ClarificationAnswersRequest` | 4 |
| `src/deep_research/api/sessions.py` | the `needs_input` lifecycle (`_clarify`), `submit_answers` | 5 |
| `src/deep_research/api/app.py` | the `clarity_checker` parameter, the answers route, `not_waiting_for_input` | 5 |
| `src/deep_research/api/replay.py` | `X-Replay-Clarify`; `reader_answers` passed to `run_research` | 5 |
| `README.md`, `docs/design/api-gaps.md` | the check in the API's documentation | 5 |
| `tests/test_config.py`, `tests/test_graph/test_reader_answers.py` (new), `tests/test_runtime/test_run_research.py` | config and engine tests | 2 |
| `tests/test_agents/test_planner_reader_answers.py` (new), `tests/test_evaluation/test_config.py` (pin) | planner tests; the planner pin | 3 |
| `tests/test_api/test_clarify.py` (new) | the service's tests | 4 |
| `tests/test_api/conftest.py` (new), `tests/test_api/test_app.py`, `tests/test_api/test_clarification.py` (new) | the live-checker guard; lifecycle, route and replay tests | 5 |
| `web/lib/api.ts` | `needs_input`, `ClarificationAnswer`, `submitAnswers` (6); `ask_clarifying_questions` (7) | 6, 7 |
| `web/lib/format.ts` | `STATUS.needs_input`, the chip's note, `isLive` | 6 |
| `web/lib/run-state.ts` | `RunState.clarify` and the two `session.clarification.*` handlers | 6 |
| `web/lib/clarify.ts` (new) | `checkPhase`, `pipelineBegun`, the answers body, resolved answers, summary and countdown text | 6 |
| `web/app/api/[...path]/route.ts` | forwards `x-replay-clarify` | 6 |
| `web/lib/session-store.ts`, `web/components/Composer.tsx`, `web/components/SettingsPopover.tsx` | the settings row (D16) and the request flag | 7 |
| `web/components/ClarifyStage.tsx` (new), `web/app/globals.css` | the check stage and its styles | 8 |
| `web/components/SessionScreen.tsx`, `web/components/Sidebar.tsx`, `web/components/ConsoleProvider.tsx` | stage selection, chip, reconnect, sidebar mark and poll | 9 |
| `web/test/**` | Vitest | 6–9 |
| `web/e2e/settings.spec.ts`, `web/e2e/support.ts`, `web/e2e/clarify.spec.ts` (new), `web/e2e/visual.spec.ts` | Playwright and the `10-clarify` capture | 7, 9 |
| `docs/design/DESIGN.md`, `web/README.md` | the design record | 10 |

---

### Task 1: Re-anchor check and baselines

**Files:** none changed. This task only reads: the repository at the end of Phase 1, and this plan.

**Interfaces:**
- Consumes: Phase 1 complete, i.e. its Tasks 1–12 done and committed.
- Produces: the proof that every anchor in Tasks 2–10 occurs exactly once, and the baseline counts that later tasks add to.

- [ ] **Step 1: Confirm the starting point**

```bash
git log --oneline | grep -F "docs(design): the running stage's live briefs, no pass counter, no extra-passes control"
test -f web/components/BriefSpine.tsx && test -f web/e2e/briefs.spec.ts && test -f web/e2e/motion.spec.ts && echo "Phase 1 files present"
git status --short | wc -l
```

Expected: one commit line (Phase 1 Task 11's), then `Phase 1 files present`, then `0` (a clean tree). If the commit or the files are missing, stop: this plan starts where Phase 1 ends.

- [ ] **Step 2: Check every anchor in this plan against the tree**

```bash
PY="$PWD/.venv/bin/python"
"$PY" - <<'EOF'
import re
from pathlib import Path

plan = Path("docs/superpowers/plans/2026-09-29-live-briefs-phase2-one-time-check.md").read_text(encoding="utf-8")
edit = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? — replace\n\n(?P<f>`{3,4})[a-z]*\n(?P<old>.*?)\n(?P=f)\n", re.S | re.M)
create = re.compile(r"^Create `(?P<path>[^`\n]+)`:\n", re.M)
append = re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n", re.M)
problems, edits = [], list(edit.finditer(plan))
for m in edits:
    path = Path(m["path"])
    found = path.read_text(encoding="utf-8").count(m["old"]) if path.is_file() else -1
    if found != 1:
        problems.append(f"{m['path']}: anchor found {found} times: {m['old'][:70]!r}")
creates = [m["path"] for m in create.finditer(plan)]
problems += [f"{p}: already exists" for p in creates if Path(p).exists()]
appends = [m["path"] for m in append.finditer(plan)]
problems += [f"{p}: missing" for p in appends if not Path(p).is_file()]
print("\n".join(problems) or f"anchors: {len(edits)} exactly once; creates: {len(creates)} absent; appends: {len(appends)} present")
EOF
```

Expected: `anchors: 141 exactly once; creates: 11 absent; appends: 8 present`.

Anything else is a line per problem (`-1` means that the file is missing). Then:
- Stop, and edit nothing.
- Report each line to the controller.

The usual cause is a Phase 1 review fix that changed text after the Phase 1 plan was written. To find it, compare `git log -p -- <file>` with the edit this plan gives for that file. The remedy is a plan amendment that the controller approves: re-anchor that edit on the text now in the file, and keep its replacement's meaning. Never guess an anchor during execution.

- [ ] **Step 3: Record the baselines**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT
```

Expected: `4768 passed, 6 skipped, 12 deselected` (observed in planning at `eecc67a`; `git diff --stat eecc67a 3565397 -- src tests config.yaml` is empty, so Phase 1's later tasks add no Python test).

```bash
cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
```

Expected: no `typecheck` output; `Test Files  23 passed (23)`, `Tests  155 passed (155)`; `OK` (observed in planning at `3565397`).

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s test:e2e
```

Expected: `47 passed`. That is Phase 1 Task 12's count, and at `3565397` `npx playwright test --list --project=chromium` lists exactly 47 tests.

If a count differs because Phase 1 review fixes added or removed tests, record the observed count in the task summary and use it as the baseline. Every later Expected count is the baseline plus the increment this plan states:

| Suite | Increments by task |
|---|---|
| Backend | +8 (Task 2), +9 (Task 3), +23 (Task 4), +23 (Task 5) |
| Vitest | +16 (Task 6), +1 (Task 7), +10 (Task 8), +4 (Task 9) |
| Chromium | +1 (Task 7), +7 (Task 9) |
| Visual | +2 (Task 9) |

If a baseline run **fails**, stop and report it: Phase 2 starts from a green Phase 1.

- [ ] **Step 4: No commit**

Nothing changed. Task 1's summary lists the anchor line and the three baselines.

---

### Task 2: The `hitl` settings and the reader's answers in the engine's state (spec §4.4 Config, Engine)

**Files** (line numbers are those at `3565397`, the end of Phase 1, and only locate the anchors):
- Modify: `config.yaml:214` (a `hitl:` block before `output:`); `src/deep_research/utils/config.py:427` (`HitlConfig` before `OutputConfig`), `:549` (`ConfigSettings.hitl`); `src/deep_research/utils/types.py:1812` (the new types before `ResearchState`), `:1970`, `:2009-2010` (the state and update fields); `src/deep_research/graph/state.py:23`, `:32-34`, `:194-212` (`initial_graph_state`); `src/deep_research/graph/orchestrator.py:30`, `:81-82`, `:441-468` (`run_research_graph`); `src/deep_research/main.py:19`, `:45`, `:200-202`, `:347-349` (`run_research`)
- Create: `tests/test_graph/test_reader_answers.py`
- Test: `tests/test_config.py` (import at `:19-20`, three tests appended), `tests/test_runtime/test_run_research.py` (import at `:28`, two tests appended)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `deep_research.utils.config.HitlConfig` (`extra="forbid"`). Its fields are `check_timeout_s: float = 20.0`, `answer_wait_s: float = 60.0` and `note_interpret_timeout_s: float = 15.0`, each `gt=0`. It is exposed as `ConfigSettings.hitl` and can be overridden per request (`config_overrides={"hitl": {...}}`).
  - In `deep_research.utils.types`:
    - `ClarityDimension = Literal["geography", "period", "purpose", "scope"]`;
    - `ReaderAnswerSource = Literal["chosen", "typed", "best_guess"]`;
    - `ReaderAnswer(ContractModel)` with `question_id: str`, `dimension: ClarityDimension`, `text: str` (the question), `short: str` (its label), `value: str` and `source: ReaderAnswerSource`, all strings non-empty;
    - `ResearchState.reader_answers: list[ReaderAnswer]`, default `[]`, replace-on-write, with the key added to `ResearchStateUpdate`.
  - A `reader_answers: Sequence[ReaderAnswer] = ()` keyword on `run_research` (`main.py`), `run_research_graph` (`graph/orchestrator.py`) and `initial_graph_state` (`graph/state.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py` — replace

```python
    EvaluationConfig,
    LLMConfig,
```

with

```python
    EvaluationConfig,
    HitlConfig,
    LLMConfig,
```

Append to `tests/test_config.py`:

```python


def test_the_hitl_timings_default_to_the_spec_values() -> None:
    """live-briefs spec §4.4: the check call 20 s, the wait for answers 60 s (D6), a
    note's interpretation 15 s."""
    assert ConfigSettings().hitl == HitlConfig(
        check_timeout_s=20.0, answer_wait_s=60.0, note_interpret_timeout_s=15.0
    )


def test_the_shipped_config_file_carries_the_hitl_block() -> None:
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    assert raw["hitl"] == {
        "check_timeout_s": 20,
        "answer_wait_s": 60,
        "note_interpret_timeout_s": 15,
    }
    assert ConfigSettings.model_validate(raw).hitl.answer_wait_s == 60.0


def test_request_overrides_set_the_hitl_timings_and_reject_bad_ones() -> None:
    settings = apply_config_overrides(
        ConfigSettings(), {"hitl": {"answer_wait_s": 5, "check_timeout_s": 2.5}}
    )

    assert (settings.hitl.answer_wait_s, settings.hitl.check_timeout_s) == (5.0, 2.5)
    for bad in ({"answer_wait_s": 0}, {"check_timeout_s": -1}, {"question_limit": 3}):
        with pytest.raises(ValueError):
            apply_config_overrides(ConfigSettings(), {"hitl": bad})
```

Create `tests/test_graph/test_reader_answers.py`:

```python
"""The reader's answers to the one-time check in the run's state (live-briefs spec §4.4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.graph.state import dump_state, initial_graph_state, load_state
from deep_research.utils.types import ReaderAnswer, ResearchState, merge_research_state

ANSWER = ReaderAnswer(
    question_id="q1", dimension="geography", text="Which region should this cover?",
    short="Region", value="United States", source="chosen",
)


def test_a_reader_answer_names_its_question_dimension_value_and_source() -> None:
    assert ANSWER.model_dump() == {
        "question_id": "q1", "dimension": "geography", "text": "Which region should this cover?",
        "short": "Region", "value": "United States", "source": "chosen",
    }
    for bad in (
        {"dimension": "budget"},
        {"source": "guessed"},
        {"value": "   "},
    ):
        with pytest.raises(ValidationError):
            ReaderAnswer.model_validate({**ANSWER.model_dump(), **bad})


def test_the_state_starts_without_answers_and_replaces_them_on_write() -> None:
    state = ResearchState(session_id="session-1", original_question="Why?")
    first = merge_research_state(state, {"reader_answers": [ANSWER]})
    second = merge_research_state(
        first, {"reader_answers": [ANSWER.model_copy(update={"value": "Global"})]}
    )

    assert state.reader_answers == []
    assert first.reader_answers == [ANSWER]
    assert [answer.value for answer in second.reader_answers] == ["Global"]


def test_the_initial_channel_carries_the_answers_and_an_older_checkpoint_loads_none() -> None:
    channel = initial_graph_state(session_id="session-1", question="Why?", reader_answers=[ANSWER])
    older = initial_graph_state(session_id="session-1", question="Why?")
    del older["state"]["reader_answers"]

    assert load_state(channel).reader_answers == [ANSWER]
    assert load_state(dump_state(load_state(channel))).reader_answers == [ANSWER]
    assert load_state(older).reader_answers == []
    assert load_state(initial_graph_state(session_id="session-1", question="Why?")).reader_answers == []
```

`tests/test_runtime/test_run_research.py` — replace

```python
from deep_research.utils.types import ResearchEvent
```

with

```python
from deep_research.utils.types import ReaderAnswer, ResearchEvent
```

Append to `tests/test_runtime/test_run_research.py`:

```python


@pytest.mark.asyncio
async def test_reader_answers_start_in_the_state_the_planner_is_handed(
    config_file, tracker
) -> None:
    """live-briefs spec §4.4: the reader's answers start in the run's state, so
    the planner reads them, and the finished state still holds them."""
    answer = ReaderAnswer(
        question_id="q1", dimension="geography", text="Which region should this cover?",
        short="Region", value="Global", source="best_guess",
    )
    planner = FakeAgent("planner", [{"sub_topics": [fake_sub_topic(targets=[fake_target()])]}])

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker, agents=fake_research_agents(planner=planner)),
        reader_answers=[answer],
    )

    assert outcome.status == "completed"
    assert planner.calls[0].reader_answers == [answer]
    assert outcome.state.reader_answers == [answer]


@pytest.mark.asyncio
async def test_a_run_given_no_reader_answers_starts_with_none(config_file, tracker) -> None:
    outcome = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert outcome.state.reader_answers == []
```

- [ ] **Step 2: Run them to see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_config.py tests/test_graph/test_reader_answers.py tests/test_runtime/test_run_research.py -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
```

Expected: `3 errors`, one per file, at collection:
- `tests/test_config.py`: `ImportError: cannot import name 'HitlConfig' from 'deep_research.utils.config'`;
- `tests/test_graph/test_reader_answers.py` and `tests/test_runtime/test_run_research.py`: `ImportError: cannot import name 'ReaderAnswer' from 'deep_research.utils.types'`.

- [ ] **Step 3: Add the settings, the type and the state field, and carry the answers from `run_research` to the initial state**

The values are the spec's own: `20`, `60`, `15`. `reader_answers` is an ordinary replace-on-write field. `merge_research_state` (`utils/types.py:2176`) appends, unions or reduces only the fields it names (`_APPEND_STATE_FIELDS` at `:2021`, `_UNION_STATE_FIELDS` at `:2037`, and its reducers), and it replaces every other field; `reader_answers` is not named.

`src/deep_research/utils/config.py` — replace

```python
class OutputConfig(BaseModel):
```

with

```python
class HitlConfig(BaseModel):
    """The reader-in-the-loop timings (live-briefs spec §4.4), in seconds.

    ``check_timeout_s`` bounds the one-time check's provider call: a check that
    fails or runs out of time asks nothing and the run starts. ``answer_wait_s``
    is how long a session waits in ``needs_input`` before it starts on the
    check's best guesses (D6). ``note_interpret_timeout_s`` bounds one reader
    note's interpretation (reader notes, live-briefs Phase 3). Request-scoped
    ``config_overrides`` may set each; nothing reads them from the environment.
    """

    model_config = ConfigDict(extra="forbid")

    check_timeout_s: float = Field(default=20.0, gt=0)
    answer_wait_s: float = Field(default=60.0, gt=0)
    note_interpret_timeout_s: float = Field(default=15.0, gt=0)


class OutputConfig(BaseModel):
```

`src/deep_research/utils/config.py` — replace

```python
    graph: GraphConfig = GraphConfig()
```

with

```python
    graph: GraphConfig = GraphConfig()
    hitl: HitlConfig = HitlConfig()
```

`config.yaml` — replace

```yaml
output:
  directory: output/
  default_format: markdown
```

with

```yaml
# The reader in the loop (live-briefs spec §4.4), in seconds. Request-scoped
# config_overrides may set each value; no environment variable does.
hitl:
  check_timeout_s: 20            # the check call; failure or timeout = no questions
  answer_wait_s: 60              # D6
  note_interpret_timeout_s: 15   # Phase 3

output:
  directory: output/
  default_format: markdown
```

`src/deep_research/utils/types.py` — replace

```python
class ResearchState(ContractModel):
```

with

```python
# The one-time check (live-briefs spec §4.4): the dimensions a check question may
# ask about, and where each answer came from.
ClarityDimension: TypeAlias = Literal["geography", "period", "purpose", "scope"]
ReaderAnswerSource: TypeAlias = Literal["chosen", "typed", "best_guess"]


class ReaderAnswer(ContractModel):
    """One answer to the one-time check, as the planner reads it (live-briefs spec §4.4).

    ``text`` is the question the reader was asked and ``short`` its one- or
    two-word label ("Region"). ``source`` says where ``value`` came from: an
    option the reader chose, text the reader typed, or the check's own best
    guess for a question the reader skipped, left, or let time out on.
    """

    question_id: str = Field(min_length=1)
    dimension: ClarityDimension
    text: str = Field(min_length=1)
    short: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source: ReaderAnswerSource


class ResearchState(ContractModel):
```

`src/deep_research/utils/types.py` — replace

```python
    memory_context: MemorySnapshot = Field(default_factory=MemorySnapshot)
```

with

```python
    memory_context: MemorySnapshot = Field(default_factory=MemorySnapshot)
    reader_answers: list[ReaderAnswer] = Field(default_factory=list)
    """The reader's answers to the one-time check, best guesses included, or ``[]``.

    Set once, when the run starts, and replaced on every write (live-briefs
    spec §4.4). Empty when the check asked nothing, was turned off, or failed:
    every consumer renders its reader-answers section only when this is
    non-empty, so a run without answers builds the same requests as before.
    """
```

`src/deep_research/utils/types.py` — replace

```python
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
```

with

```python
    memory_context: MemorySnapshot
    reader_answers: list[ReaderAnswer]
    events: list[ResearchEvent]
```

`src/deep_research/graph/state.py` — replace

```python
from typing import TypedDict
```

with

```python
from collections.abc import Sequence
from typing import TypedDict
```

`src/deep_research/graph/state.py` — replace

```python
    MemorySnapshot,
    ResearchState,
)
```

with

```python
    MemorySnapshot,
    ReaderAnswer,
    ResearchState,
)
```

`src/deep_research/graph/state.py` — replace

```python
    memory_context: MemorySnapshot | None = None,
) -> ResearchGraphState:
```

with

```python
    memory_context: MemorySnapshot | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> ResearchGraphState:
```

`src/deep_research/graph/state.py` — replace

```python
    which orchestration has no business owning.
```

with

```python
    which orchestration has no business owning. ``reader_answers`` are the
    reader's answers to the one-time check (live-briefs spec §4.4), empty
    when nothing was asked.
```

`src/deep_research/graph/state.py` — replace

```python
            memory_context=memory_context or MemorySnapshot(),
```

with

```python
            memory_context=memory_context or MemorySnapshot(),
            reader_answers=list(reader_answers),
```

`src/deep_research/graph/orchestrator.py` — replace

```python
from collections.abc import Callable
```

with

```python
from collections.abc import Callable, Sequence
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    MemorySnapshot,
    ResearchEvent,
```

with

```python
    MemorySnapshot,
    ReaderAnswer,
    ResearchEvent,
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    event_handler: ProgressHandler | None = None,
) -> GraphRun:
    """Run one research session from the question to a final status.
```

with

```python
    event_handler: ProgressHandler | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> GraphRun:
    """Run one research session from the question to a final status.

    ``reader_answers`` are the reader's answers to the one-time check
    (live-briefs spec §4.4); they start in the initial state, so the planner
    reads them, and a run without them starts exactly as before.
```

`src/deep_research/graph/orchestrator.py` — replace

```python
            memory_context=memory_context,
        )
    )
```

with

```python
            memory_context=memory_context,
            reader_answers=reader_answers,
        )
    )
```

`src/deep_research/main.py` — replace

```python
from collections.abc import Awaitable, Callable, Mapping
```

with

```python
from collections.abc import Awaitable, Callable, Mapping, Sequence
```

`src/deep_research/main.py` — replace

```python
from deep_research.utils.types import ResearchEvent
```

with

```python
from deep_research.utils.types import ReaderAnswer, ResearchEvent
```

`src/deep_research/main.py` — replace

```python
    request_budget_handler: RequestBudgetObserver | None = None,
) -> ResearchOutcome:
    """Run one research session, or continue a checkpointed one.
```

with

```python
    request_budget_handler: RequestBudgetObserver | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> ResearchOutcome:
    """Run one research session, or continue a checkpointed one.

    ``reader_answers`` are the reader's answers to the API's one-time check
    (live-briefs spec §4.4). A fresh run starts with them in its state, where
    the planner reads them; a resumed run keeps the answers its checkpoint
    holds, so they are ignored there.
```

`src/deep_research/main.py` — replace

```python
                memory_context=memory_context,
                event_handler=event_handler,
            )
```

with

```python
                memory_context=memory_context,
                event_handler=event_handler,
                reader_answers=reader_answers,
            )
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_config.py tests/test_graph/test_reader_answers.py tests/test_runtime/test_run_research.py -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
```

Expected: `182 passed, 1 deselected` (observed in planning).

- [ ] **Step 5: Commit**

```bash
git add config.yaml src/deep_research/utils/config.py src/deep_research/utils/types.py src/deep_research/graph/state.py src/deep_research/graph/orchestrator.py src/deep_research/main.py tests/test_config.py tests/test_graph/test_reader_answers.py tests/test_runtime/test_run_research.py
git commit -m "feat(engine): hitl settings, and the reader's answers carried from run_research into the state"
```

---

### Task 3: The planner plans within the reader's answers (spec §4.4 Engine table; AC11's packet)

**Files** (line numbers at `3565397`):
- Modify: `src/deep_research/agents/planner.py`:
  - `:65-67`: the import;
  - `:1300-1391`: `derive_answer_contract` and the new `_with_reader_answers`;
  - `:2637-2661`: `render_reader_answers` and `plan_messages`;
  - `:2779-2787`: `plan_review_messages`;
  - `:3052`, `:3120`, `:3158`, `:3180-3182`, `:3262-3264`: `PlannerAgent`.
- Modify: `src/deep_research/agents/__init__.py:198-199`, `:682-683` (exports).
- Modify: `tests/test_evaluation/test_config.py`: the planner pin only, via the snippet in Step 6.
- Create: `tests/test_agents/test_planner_reader_answers.py`.

**Interfaces:**
- Consumes: Task 2's `ReaderAnswer` and `ResearchState.reader_answers`.
- Produces:
  - `derive_answer_contract(..., reader_answers: Sequence[ReaderAnswer] = ())`.
  - `render_reader_answers(reader_answers: Sequence[ReaderAnswer]) -> str`, exported from `deep_research.agents`.
  - `plan_messages(..., reader_answers=())` and `plan_review_messages(..., reader_answers=())`. Each adds `# Reader answers\n{render_reader_answers(...)}` right after `# Answer contract`, only when there are answers.
  - `PlannerAgent` reads `state.reader_answers` into all three.
  - With no answers, every request is byte-identical to today's (`PINNED_PACKETS`).

- [ ] **Step 1: Write the failing tests**

`PINNED_PACKETS` holds the sha256 prefixes of four packets: the planner's three requests and the reviewer's request, on the default replay case (`missing-target-triggers-one-extra-pass`) with session `"s1"`. Planning computed them with this harness at `8994d5a` (before Phase 1), at `a00ef0a` and at `eecc67a`, and got the same values each time. The test proves that Phase 2 leaves them unchanged when there are no answers.

Create `tests/test_agents/test_planner_reader_answers.py`:

```python
"""The reader's answers to the one-time check, as the planner reads them (live-briefs spec §4.4)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from deep_research.agents.planner import (
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
    render_reader_answers,
)
from deep_research.agents.prompts import AgentTask
from deep_research.e2e_evaluation.replay import (
    build_replay_runtime,
    production_config_path,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research
from deep_research.observability import Tracker
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ReaderAnswer, ResearchState
from tests.agent_fakes import ScriptedCompleter, finish
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_agents.test_planner import (
    _CLOCK_NOW,
    _planner,
    _review,
    _run,
    _sorting_plan,
)

QUESTION = "What are the current constraints on grid-scale battery storage deployment?"
REGION = ReaderAnswer(
    question_id="q1", dimension="geography", text="Which region should this cover?",
    short="Region", value="European Union", source="chosen",
)
PERIOD = ReaderAnswer(
    question_id="q2", dimension="period", text="How recent should the sources be?",
    short="Period", value="since 2021", source="typed",
)
PURPOSE = ReaderAnswer(
    question_id="q3", dimension="purpose", text="What will you use it for?",
    short="For", value="General understanding", source="best_guess",
)
ANSWERS = (REGION, PERIOD, PURPOSE)


def test_the_contract_takes_the_readers_scope_period_and_assumption_lines() -> None:
    plain = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW)
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)

    assert plain.geographic_scope == "unspecified"
    assert contract.geographic_scope == "European Union"
    assert contract.evidence_period_requirement == "since 2021"
    assert contract.assumptions == [
        "Reader said: Region = European Union",
        "Reader said: Period = since 2021",
        "Assumed (best guess): For = General understanding",
    ]
    assert "answered for European Union" in contract.scope_statement
    assert contract.scope_statement.endswith("evidence period: since 2021.")
    assert (contract.question, contract.as_of_date, contract.answer_kind) == (
        plain.question, plain.as_of_date, plain.answer_kind,
    )


def test_a_question_that_names_its_geography_keeps_its_assumptions_and_gains_the_lines() -> None:
    question = "What limits battery storage deployment in the United States?"
    purpose_only = derive_answer_contract(question=question, now=_CLOCK_NOW, reader_answers=(PURPOSE,))
    plain = derive_answer_contract(question=question, now=_CLOCK_NOW)

    assert purpose_only.geographic_scope == plain.geographic_scope == "United States"
    assert purpose_only.evidence_period_requirement == plain.evidence_period_requirement
    assert purpose_only.assumptions == [*plain.assumptions, "Assumed (best guess): For = General understanding"]


def test_no_answers_derive_exactly_the_contract_the_question_alone_yields() -> None:
    assert derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=()) == derive_answer_contract(
        question=QUESTION, now=_CLOCK_NOW
    )


def test_the_reader_answers_section_says_who_gave_each_answer() -> None:
    assert render_reader_answers(ANSWERS).splitlines() == [
        "Before planning, the reader answered a short check about what the question "
        "leaves open. Plan within these answers: a narrowing the reader asked for is "
        "not a missing part of the question.",
        "- Which region should this cover? European Union (the reader's answer)",
        "- How recent should the sources be? since 2021 (the reader's answer)",
        "- What will you use it for? General understanding (a best guess; the reader did not answer)",
    ]


def test_the_plan_request_carries_reader_answers_after_the_contract_only_when_there_are_any() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)
    task = AgentTask(instruction=QUESTION)
    with_answers = plan_messages(task, _run(), contract=contract, reader_answers=ANSWERS)[1].content
    without = plan_messages(task, _run(), contract=contract)[1].content

    assert with_answers.index("# Answer contract\n") < with_answers.index("# Reader answers\n") < with_answers.index("# Scoping notes\n")
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}\n" in with_answers
    assert "# Reader answers" not in without
    assert plan_messages(task, _run(), contract=contract, reader_answers=()) == plan_messages(task, _run(), contract=contract)


def test_the_plan_review_carries_the_same_section_only_when_there_are_answers() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)
    reviewed = plan_review_messages(contract, [], reader_answers=ANSWERS)[1].content

    assert reviewed.index("# Answer contract\n") < reviewed.index("# Reader answers\n") < reviewed.index("# Plan under review\n")
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}\n\n# Plan under review" in reviewed
    assert plan_review_messages(contract, [], reader_answers=()) == plan_review_messages(contract, [])
    assert "# Reader answers" not in plan_review_messages(contract, [])[1].content


@pytest.mark.asyncio
async def test_the_planner_reads_the_answers_from_its_state_into_both_requests_and_the_contract(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_answers=list(ANSWERS))

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert [call[0] for call in completer.calls] == ["ResearchPlanDraft", "PlanReviewDraft"]
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}" in plan_request
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}" in review_request
    assert "- Scope: European Union" in plan_request
    contract = outcome.state_update["answer_contract"]
    assert contract.geographic_scope == "European Union"
    assert "Reader said: Region = European Union" in contract.assumptions


# The two answer-contract consumers' packets for the default case, session "s1",
# with no answers, as sha256[:16] of the exact text: identical before this work
# (8994d5a, a00ef0a) and after it, because the check adds sections only when
# there are answers (spec §4.4 "Replay").
PINNED_PACKETS = {
    "planner:react": "a420821fa50ed937",
    "planner:ResearchPlanDraft": "1f7f8426a5be29de",
    "planner:PlanReviewDraft": "918178396a7380e5",
    "report_reviewer:ReportReviewDraft": "824e2b4aa04d2944",
}


async def _replay_packets(tmp_path: Path, reader_answers: tuple[ReaderAnswer, ...]) -> tuple[str, dict[str, str]]:
    scenario = scenario_by_id(EXTRA_PASS_CASE)
    held: dict[str, Any] = {}

    async def builder(current: ConfigSettings, *, session_id: str) -> Any:
        effective = replay_settings(scenario, root=tmp_path, base=current)
        replay = await build_replay_runtime(scenario, root=tmp_path, session_id=session_id, settings=effective)
        held["completer"] = replay.completer
        return replay.runtime

    outcome = await run_research(
        question=scenario.question, session_id="s1", config_path=str(production_config_path()),
        max_extra_passes=scenario.max_extra_passes, runtime_builder=builder, reader_answers=reader_answers,
    )
    return outcome.status, dict(held["completer"].packets)


@pytest.mark.asyncio
async def test_without_answers_the_replay_packets_are_byte_identical(tmp_path: Path) -> None:
    with guarded():
        status, packets = await _replay_packets(tmp_path, ())

    assert status == "completed"
    assert {key: hashlib.sha256(packets[key].encode("utf-8")).hexdigest()[:16] for key in PINNED_PACKETS} == PINNED_PACKETS
    assert not any("# Reader answers" in text for text in packets.values())


@pytest.mark.asyncio
async def test_the_readers_answers_reach_the_planners_packets(tmp_path: Path) -> None:
    """AC11: the planner's packet contains ``# Reader answers``, and the run completes."""
    with guarded():
        status, packets = await _replay_packets(tmp_path, (REGION.model_copy(update={"value": "United States"}),))

    assert status == "completed"
    for key in ("planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        assert "# Reader answers\n" in packets[key]
        assert "- Which region should this cover? United States (the reader's answer)" in packets[key]
    assert "- Scope: United States" in packets["planner:ResearchPlanDraft"]
    assert "# Reader answers" not in packets["planner:react"]
```

- [ ] **Step 2: Run it to see it fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_planner_reader_answers.py -q
```

Expected: `1 error` at collection, `ImportError: cannot import name 'render_reader_answers' from 'deep_research.agents.planner'`.

- [ ] **Step 3: Read the answers into the contract and the two requests**

`_with_reader_answers` runs before `scope_statement` is composed, so the statement carries the reader's scope and period (ambiguity 12). `PlannerAgent.run` keeps the state's answers on `self._reader_answers`, next to `self._frozen_contract`, and the three consumers read them from there.

`src/deep_research/agents/planner.py` — replace

```python
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
```

with

```python
    MemorySnapshot,
    ReaderAnswer,
    ResearchError,
    ResearchEvent,
```

`src/deep_research/agents/planner.py` — replace

```python
    requested_word_limit: int | None = None,
) -> AnswerContract:
```

with

```python
    requested_word_limit: int | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> AnswerContract:
```

`src/deep_research/agents/planner.py` — replace

```python
    obligation — the very defect class this contract exists to remove.
    """
```

with

```python
    obligation — the very defect class this contract exists to remove.

    ``reader_answers`` are the reader's answers to the one-time check
    (live-briefs spec §4.4), applied after the question's own reading: a
    ``geography`` answer sets the scope, a ``period`` answer sets the evidence
    period, and every answer adds one assumption line. With none, the contract
    is exactly the one the question alone yields.
    """
```

`src/deep_research/agents/planner.py` — replace

```python
        asks_for_currency=asks_for_currency,
    )
    scope_statement = (
```

with

```python
        asks_for_currency=asks_for_currency,
    )
    scope, period, assumptions = _with_reader_answers(
        scope, period, assumptions, reader_answers
    )
    scope_statement = (
```

`src/deep_research/agents/planner.py` — replace

```python
def frozen_contract_for(
```

with

```python
def _with_reader_answers(
    scope: str,
    period: str,
    assumptions: list[str],
    reader_answers: Sequence[ReaderAnswer],
) -> tuple[str, str, list[str]]:
    """Apply the reader's answers to the question's own scope, period and assumptions.

    The last ``geography`` answer becomes the scope, and the "names no
    geography" assumption goes with the unspecified scope it explained; the
    last ``period`` answer becomes the evidence period. Every answer adds
    ``Reader said: {short} = {value}``, or ``Assumed (best guess): {short} =
    {value}`` for one the reader did not give (live-briefs spec §4.4).
    """
    geography = [a.value for a in reader_answers if a.dimension == "geography"]
    periods = [a.value for a in reader_answers if a.dimension == "period"]
    # ``geographic_scope_for`` returns an assumption only for an unspecified
    # scope, and it explains exactly that scope.
    lines = [] if geography and scope == "unspecified" else list(assumptions)
    lines += [
        f"{'Assumed (best guess)' if a.source == 'best_guess' else 'Reader said'}: "
        f"{a.short} = {a.value}"
        for a in reader_answers
    ]
    return (
        geography[-1] if geography else scope,
        periods[-1] if periods else period,
        lines,
    )


def frozen_contract_for(
```

`src/deep_research/agents/planner.py` — replace

```python
def plan_messages(
```

with

```python
def render_reader_answers(reader_answers: Sequence[ReaderAnswer]) -> str:
    """Print the reader's answers to the one-time check (live-briefs spec §4.4).

    One line per question, saying whether the reader gave the answer or the
    check assumed it. Rendered only when there are answers, so a request
    without them is byte-identical to one built before the check existed.
    """
    lines = [
        "Before planning, the reader answered a short check about what the "
        "question leaves open. Plan within these answers: a narrowing the "
        "reader asked for is not a missing part of the question."
    ]
    for answer in reader_answers:
        said = (
            "a best guess; the reader did not answer"
            if answer.source == "best_guess"
            else "the reader's answer"
        )
        lines.append(f"- {answer.text} {answer.value} ({said})")
    return "\n".join(lines)


def plan_messages(
```

`src/deep_research/agents/planner.py` — replace

```python
    plan_under_repair: Sequence[SubTopic] = (),
) -> list[ChatMessage]:
    """Build the messages that request one structured plan draft.
```

with

```python
    plan_under_repair: Sequence[SubTopic] = (),
    reader_answers: Sequence[ReaderAnswer] = (),
) -> list[ChatMessage]:
    """Build the messages that request one structured plan draft.
```

`src/deep_research/agents/planner.py` — replace

```python
    scope, which is what a replay of an older prompt looks like.
    """
```

with

```python
    scope, which is what a replay of an older prompt looks like.
    ``reader_answers`` add a ``# Reader answers`` section after the contract,
    only when there are any (live-briefs spec §4.4).
    """
```

`src/deep_research/agents/planner.py` — replace

```python
        material.append(f"# Answer contract\n{render_answer_contract(contract)}")
```

with

```python
        material.append(f"# Answer contract\n{render_answer_contract(contract)}")
    if reader_answers:
        material.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
```

`src/deep_research/agents/planner.py` — replace

```python
    repair: str | None = None,
) -> list[ChatMessage]:
    """Build the one tool-free request that reviews a plan's meaning."""
    sections = [
        f"# Original question (frozen)\n{contract.question}",
        f"# Answer contract\n{render_answer_contract(contract)}",
        f"# Plan under review\n{render_plan_for_review(sub_topics)}",
        f"# Review requirements\n{PLAN_REVIEW_INSTRUCTION}",
    ]
```

with

```python
    repair: str | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> list[ChatMessage]:
    """Build the one tool-free request that reviews a plan's meaning.

    ``reader_answers`` add the same ``# Reader answers`` section the plan
    request carries, only when there are any, so the review does not flag a
    narrowing the reader asked for as a missing dimension (spec §4.4).
    """
    sections = [
        f"# Original question (frozen)\n{contract.question}",
        f"# Answer contract\n{render_answer_contract(contract)}",
    ]
    if reader_answers:
        sections.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
    sections += [
        f"# Plan under review\n{render_plan_for_review(sub_topics)}",
        f"# Review requirements\n{PLAN_REVIEW_INSTRUCTION}",
    ]
```

`src/deep_research/agents/planner.py` — replace

```python
        self._frozen_contract: AnswerContract | None = None
```

with

```python
        self._frozen_contract: AnswerContract | None = None
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the reader's answers to the one-time check, if any.
        self._reader_answers: tuple[ReaderAnswer, ...] = ()
```

`src/deep_research/agents/planner.py` — replace

```python
        self._frozen_contract = state.answer_contract
```

with

```python
        self._frozen_contract = state.answer_contract
        self._reader_answers = tuple(state.reader_answers)
```

`src/deep_research/agents/planner.py` — replace

```python
        derived = derive_answer_contract(question=question, now=self._clock())
```

with

```python
        derived = derive_answer_contract(
            question=question,
            now=self._clock(),
            reader_answers=self._reader_answers,
        )
```

`src/deep_research/agents/planner.py` — replace

```python
                    plan_under_repair=plan_under_repair,
                ),
                ResearchPlanDraft,
```

with

```python
                    plan_under_repair=plan_under_repair,
                    reader_answers=self._reader_answers,
                ),
                ResearchPlanDraft,
```

`src/deep_research/agents/planner.py` — replace

```python
                plan_review_messages(
                    contract, sub_topics, repair=already_requested
                ),
```

with

```python
                plan_review_messages(
                    contract,
                    sub_topics,
                    repair=already_requested,
                    reader_answers=self._reader_answers,
                ),
```

`src/deep_research/agents/__init__.py` — replace

```python
    render_answer_contract,
    render_plan_for_review,
```

with

```python
    render_answer_contract,
    render_plan_for_review,
    render_reader_answers,
```

`src/deep_research/agents/__init__.py` — replace

```python
    "render_answer_contract",
    "render_plan_for_review",
```

with

```python
    "render_answer_contract",
    "render_plan_for_review",
    "render_reader_answers",
```

- [ ] **Step 4: Run the planner tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_planner_reader_answers.py tests/test_agents/test_planner.py -q
```

Expected: `298 passed` (observed in planning; the new file contributes `9`). The `langsmith` `DeprecationWarning` was there before this plan.

- [ ] **Step 5: See the planner's pin move**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint
```

Expected: `1 failed, 10 passed, 67 deselected`. `test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift` reports `{'planner': 'e9b74316ad14'} != {'planner': 'd40ffac43845'}`: the module's code changed, and `agents/prompts.py` did not.

- [ ] **Step 6: Re-pin the planner (module code only)**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - planner "Live briefs Phase 2 (live-briefs spec §4.4, 2026-09-29): the planner reads the reader's answers to the one-time check into the answer contract and, only when there are answers, into a # Reader answers section of the plan and plan-review requests. Without answers every request is byte-identical; agents.prompts was untouched, so the other four target pins and the Judge pin are unchanged." <<'EOF'
import re
import sys
import textwrap
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

agent, reason = sys.argv[1:3]
path = Path("tests/test_evaluation/test_config.py")
text = path.read_text(encoding="utf-8")
pins = re.findall(rf'(?m)^    "{agent}": "([0-9a-f]{{12}})",$', text)
assert len(pins) == 1, f"pin for {agent} found {len(pins)} times"
old, new = pins[0], agent_prompt_fingerprint(agent)
anchor = f'    "{agent}": "{old}",\n'
comment = textwrap.fill(
    f"{reason} Moved `{old}` -> `{new}`.", width=78,
    initial_indent="    # ", subsequent_indent="    # ",
)
path.write_text(text.replace(anchor, f'{comment}\n    "{agent}": "{new}",\n'), encoding="utf-8")
print(f"{agent}: {old} -> {new}")
EOF
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint
```

Expected: `planner: d40ffac43845 -> e9b74316ad14`, then `11 passed, 67 deselected`.

The snippet reads the current pin, so it works whatever value Phase 1 left. The new value is the one planning produced from exactly this text. A different value is acceptable when `git diff -- src/deep_research/agents/planner.py` shows only this task's edits and every test in this task passes: the snippet pins whatever the module now hashes to, so do not hunt for single characters. It also writes the comment above the pin, in the file's existing ``Moved `old` -> `new`.`` style.

- [ ] **Step 7: Run the full suite (the replay suite included)**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT
```

Expected: `4785 passed, 6 skipped, 12 deselected` (observed in planning: Task 1's baseline + 8 + 9). Every existing replay test passes unchanged, which is the spec's proof that the packets are byte-identical without answers.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/agents/planner.py src/deep_research/agents/__init__.py tests/test_agents/test_planner_reader_answers.py tests/test_evaluation/test_config.py
git commit -m "feat(planner): plan within the reader's answers to the one-time check"
```

---

### Task 4: The check's service and the API's new shapes (spec §4.4 Request, Service, `ClarityCheck` contract)

**Files:**
- Create: `src/deep_research/api/clarify.py`, `tests/test_api/test_clarify.py`
- Modify: `src/deep_research/api/models.py:8` (import), `:21-22` (`SessionStatus`), `:52-60` (`ResearchRequest`), `:75` (two new models before `CoverageProgressResponse`)

**Interfaces:**
- Consumes: Task 2's `ReaderAnswer`, `ClarityDimension`, `ConfigSettings`, `LLMConfig`.
- Produces, in `deep_research.api.clarify`:
  - Models:
    - `ClarityQuestionDraft` and `ClarityCheckDraft`, what the provider is asked for;
    - `ClarityQuestion(id, dimension, text, short, options, best_guess)`, frozen: `id` matches `^q[1-3]$`, `text` has at most 200 characters, `short` at most 40, there are 2–4 distinct options of 1–80 characters each, and `best_guess` is one of them;
    - `ClarityCheck(questions: list[ClarityQuestion])`, frozen: at most 3 questions, ids `q1..qn` in order.
  - Constants: `NO_QUESTIONS`, `MAX_CLARITY_QUESTIONS = 3`, `CLARITY_MAX_TOKENS = 4096`.
  - `ClarityChecker = Callable[[str, ConfigSettings], Awaitable[ClarityCheck]]`.
  - `validated_check(draft) -> ClarityCheck`: stamps `q1..qn`, and returns `NO_QUESTIONS` on any invalid field.
  - The prompt: `CLARITY_SYSTEM_PROMPT`, `CLARITY_INSTRUCTION`, `clarity_messages(question) -> list[ChatMessage]`.
  - The live checker:
    - `clarity_llm_config(llm) -> LLMConfig`, the same provider and model with `thinking_mode="disabled"`;
    - `async live_clarity_check(question, settings, *, completer=None) -> ClarityCheck`: one `complete_structured(..., ClarityCheckDraft, agent_name=None, max_tokens=4096)`. Provider errors propagate.
  - Replay:
    - `REPLAY_CLARIFY_HEADER = "x-replay-clarify"`;
    - `requested_clarify: ContextVar[bool]`;
    - `REPLAY_CLARITY_CHECK`, the fixed set in Global Constraints;
    - `async scripted_clarity_check(question, settings) -> ClarityCheck`.
  - Answers and events:
    - `AnswerValidationError(issues)`, whose `.errors()` returns `[{"loc", "type", "msg": "invalid answer"}]`;
    - `resolve_answers(questions, submitted=()) -> tuple[ReaderAnswer, ...]`;
    - `clarification_requested_event(questions, deadline_at) -> ResearchEvent`;
    - `clarification_answered_event(answers, reason) -> ResearchEvent`, both with `source="api"`.
- Produces, in `deep_research.api.models`:
  - `SessionStatus` with `"needs_input"`;
  - `ResearchRequest.ask_clarifying_questions: bool = True`;
  - `ClarificationAnswer(question_id, choice: str | None, text: str | None)`, which takes exactly one of `choice` and `text`; `text` has 1–200 characters and is whitespace-stripped, like every `ApiModel` string;
  - `ClarificationAnswersRequest(answers: list[ClarificationAnswer] (≤ 3), skip: bool = False)`.

- [ ] **Step 1: Write the failing tests**

The live checker is driven only by `RecordingCompleter`, a scripted stand-in for the provider: no provider is built and no request leaves the process.

Create `tests/test_api/test_clarify.py`:

```python
"""The one-time check's service (live-briefs spec §4.4): contract, checkers, answers, events.

The live checker is driven only with a scripted completer: no provider is built
and no request leaves the process.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from deep_research.api.clarify import (
    CLARITY_MAX_TOKENS,
    NO_QUESTIONS,
    REPLAY_CLARITY_CHECK,
    AnswerValidationError,
    ClarityCheck,
    ClarityCheckDraft,
    ClarityQuestionDraft,
    clarification_answered_event,
    clarification_requested_event,
    clarity_llm_config,
    live_clarity_check,
    requested_clarify,
    resolve_answers,
    scripted_clarity_check,
    validated_check,
)
from deep_research.api.models import ClarificationAnswer, ResearchRequest
from deep_research.utils.config import ConfigSettings, LLMConfig

QUESTIONS = REPLAY_CLARITY_CHECK.questions


def _draft(n: int = 1, **over: Any) -> ClarityCheckDraft:
    question = {
        "dimension": "geography",
        "text": "Which region should this cover?",
        "short": "Region",
        "options": ["United States", "European Union", "Global"],
        "best_guess": "Global",
        **over,
    }
    return ClarityCheckDraft(questions=[ClarityQuestionDraft(**question) for _ in range(n)])


class RecordingCompleter:
    """Answers ``complete_structured`` with one scripted draft and records the call."""

    def __init__(self, reply: ClarityCheckDraft | BaseException) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(self, messages, schema, **kwargs: Any) -> Any:
        self.calls.append({"messages": list(messages), "schema": schema, **kwargs})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def test_a_valid_draft_is_stamped_q1_to_qn_in_order() -> None:
    check = validated_check(_draft(3))

    assert [q.id for q in check.questions] == ["q1", "q2", "q3"]
    assert check.questions[0].best_guess == "Global"


@pytest.mark.parametrize(
    "draft",
    [
        _draft(4),
        _draft(1, best_guess="Asia"),
        _draft(1, options=["Global"]),
        _draft(1, options=["A", "B", "C", "D", "Global"]),
        _draft(1, options=["Global", "Global"], best_guess="Global"),
        _draft(1, text="x" * 201),
        _draft(1, short=""),
    ],
    ids=["four_questions", "best_guess_not_an_option", "one_option", "five_options", "repeated_option", "long_text", "empty_short"],
)
def test_an_invalid_draft_asks_nothing_at_all(draft: ClarityCheckDraft) -> None:
    assert validated_check(draft) == NO_QUESTIONS


def test_an_empty_draft_asks_nothing() -> None:
    assert validated_check(ClarityCheckDraft()) == NO_QUESTIONS
    assert NO_QUESTIONS.questions == []


def test_the_check_runs_on_the_configured_model_with_thinking_disabled() -> None:
    llm = LLMConfig(provider="deepseek", model="deepseek-v4-pro", thinking_mode="enabled")

    config = clarity_llm_config(llm)

    assert (config.provider, config.model, config.thinking_mode) == ("deepseek", "deepseek-v4-pro", "disabled")
    assert llm.thinking_mode == "enabled"


@pytest.mark.asyncio
async def test_the_live_check_asks_one_structured_tool_free_request() -> None:
    completer = RecordingCompleter(_draft(2))

    check = await live_clarity_check("What limits storage?", ConfigSettings(), completer=completer)

    [call] = completer.calls
    assert call["schema"] is ClarityCheckDraft
    assert (call["agent_name"], call["max_tokens"]) == (None, CLARITY_MAX_TOKENS)
    request = call["messages"][1].content
    assert request.endswith("# Research question\nWhat limits storage?")
    assert "Never ask about something the question already states." in request
    assert "Ask at most 3 questions." in request
    assert [q.id for q in check.questions] == ["q1", "q2"]


@pytest.mark.asyncio
async def test_a_live_check_whose_reply_breaks_the_contract_asks_nothing() -> None:
    check = await live_clarity_check("Q?", ConfigSettings(), completer=RecordingCompleter(_draft(4)))

    assert check == NO_QUESTIONS


@pytest.mark.asyncio
async def test_a_provider_failure_propagates_to_the_store_that_owns_the_fallback() -> None:
    with pytest.raises(RuntimeError, match="provider down"):
        await live_clarity_check("Q?", ConfigSettings(), completer=RecordingCompleter(RuntimeError("provider down")))


@pytest.mark.asyncio
async def test_the_replay_checker_asks_the_fixed_set_only_when_the_header_asked() -> None:
    assert await scripted_clarity_check("Q?", object()) == NO_QUESTIONS

    async def with_header() -> ClarityCheck:
        requested_clarify.set(True)
        return await scripted_clarity_check("Q?", object())

    assert await asyncio.create_task(with_header()) == REPLAY_CLARITY_CHECK
    assert requested_clarify.get() is False
    assert [(q.id, q.dimension, q.short, q.best_guess) for q in REPLAY_CLARITY_CHECK.questions] == [
        ("q1", "geography", "Region", "Global"),
        ("q2", "period", "Period", "Since 2023"),
        ("q3", "purpose", "For", "General understanding"),
    ]


def test_answers_resolve_in_question_order_and_skipped_ones_take_the_best_guess() -> None:
    answers = resolve_answers(
        QUESTIONS,
        [
            ClarificationAnswer(question_id="q2", text="since 2021"),
            ClarificationAnswer(question_id="q1", choice="United States"),
        ],
    )

    assert [(a.question_id, a.dimension, a.short, a.value, a.source) for a in answers] == [
        ("q1", "geography", "Region", "United States", "chosen"),
        ("q2", "period", "Period", "since 2021", "typed"),
        ("q3", "purpose", "For", "General understanding", "best_guess"),
    ]
    assert answers[0].text == "Which region should this cover?"
    assert [a.source for a in resolve_answers(QUESTIONS)] == ["best_guess"] * 3


def test_answers_that_do_not_fit_the_questions_name_each_problem() -> None:
    with pytest.raises(AnswerValidationError) as caught:
        resolve_answers(
            QUESTIONS,
            [
                ClarificationAnswer(question_id="q9", choice="Global"),
                ClarificationAnswer(question_id="q1", choice="Asia"),
                ClarificationAnswer(question_id="q2", text="recent"),
                ClarificationAnswer(question_id="q2", text="older"),
            ],
        )

    assert caught.value.errors() == [
        {"loc": ("body", "answers", 0, "question_id"), "type": "unknown_question", "msg": "invalid answer"},
        {"loc": ("body", "answers", 1, "choice"), "type": "choice_not_offered", "msg": "invalid answer"},
        {"loc": ("body", "answers", 3, "question_id"), "type": "duplicate_question", "msg": "invalid answer"},
    ]


def test_the_two_events_carry_the_spec_metadata() -> None:
    deadline = datetime(2026, 9, 29, 10, 0, 30, 123456, tzinfo=timezone.utc)
    requested = clarification_requested_event(QUESTIONS, deadline)
    answered = clarification_answered_event(resolve_answers(QUESTIONS), "timed_out")

    assert requested.event_type == "session.clarification.requested"
    assert requested.metadata["deadline_at"] == "2026-09-29T10:00:30.123+00:00"
    assert requested.metadata["questions"][0] == {
        "id": "q1", "dimension": "geography", "text": "Which region should this cover?", "short": "Region",
        "options": ["United States", "European Union", "Global"], "best_guess": "Global",
    }
    assert answered.event_type == "session.clarification.answered"
    assert answered.metadata == {
        "answers": [
            {"question_id": "q1", "value": "Global", "source": "best_guess"},
            {"question_id": "q2", "value": "Since 2023", "source": "best_guess"},
            {"question_id": "q3", "value": "General understanding", "source": "best_guess"},
        ],
        "reason": "timed_out",
    }


def test_the_request_asks_by_default_and_can_turn_the_check_off() -> None:
    """D16: the setting is on unless the request says otherwise."""
    assert ResearchRequest(query="Q?").ask_clarifying_questions is True
    assert ResearchRequest(query="Q?", ask_clarifying_questions=False).ask_clarifying_questions is False


@pytest.mark.parametrize(
    "body",
    [
        {"question_id": "q1"},
        {"question_id": "q1", "choice": "Global", "text": "anywhere"},
        {"question_id": "q1", "text": "   "},
        {"question_id": "q1", "text": "x" * 201},
        {"question_id": "", "choice": "Global"},
    ],
    ids=["neither", "both", "blank_text", "text_over_200", "blank_question_id"],
)
def test_an_answer_carries_exactly_one_of_choice_or_text(body: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ClarificationAnswer.model_validate(body)
    assert ClarificationAnswer(question_id="q1", text=" x" * 100).text == ("x " * 99) + "x"
```

- [ ] **Step 2: Run them to see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_clarify.py -q
```

Expected: `1 error` at collection, `ModuleNotFoundError: No module named 'deep_research.api.clarify'`.

- [ ] **Step 3: Write the service and the models**

`clarify.py` stays free of session state. The store (Task 5) owns the timeout, the waiting and the status. The prompt text is new and reaches only the check's own request; no agent's request changes.

Create `src/deep_research/api/clarify.py`:

```python
"""The one-time check (live-briefs spec §4.4): before planning, ask the reader.

When the question leaves something material open, the session asks the reader
up to three questions before the planner starts, each with a few options and
one best guess. A ``ClarityChecker`` decides what to ask: live mode asks the
configured provider (thinking disabled, structured output), and replay mode
asks nothing unless the request carried ``X-Replay-Clarify: on``, in which case
it asks a fixed set, so every existing scripted flow stays exactly as it was.

The check never blocks a run. A checker that fails, times out or answers with
something invalid asks nothing, and the run starts as it always did
(``SessionStore._clarify`` owns the timeout and the waiting).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ChatMessage, build_chat_provider
from deep_research.utils.config import ConfigSettings, LLMConfig
from deep_research.utils.types import (
    ClarityDimension,
    ReaderAnswer,
    ResearchEvent,
)

MAX_CLARITY_QUESTIONS = 3
CLARITY_MAX_TOKENS = 4096
"""The check's output cap: at most three short questions, with thinking off."""

CLARITY_TRACE_SESSION = "clarity-check"
MAX_TYPED_ANSWER_CHARS = 200


class ClarityQuestionDraft(BaseModel):
    """One question as the provider proposes it; validated by ``validated_check``."""

    model_config = ConfigDict(extra="forbid")

    dimension: ClarityDimension
    text: str
    short: str
    options: list[str]
    best_guess: str


class ClarityCheckDraft(BaseModel):
    """What the provider is asked for: the questions, possibly none."""

    model_config = ConfigDict(extra="forbid")

    questions: list[ClarityQuestionDraft] = Field(default_factory=list)


class ClarityQuestion(BaseModel):
    """One question the reader is asked (spec §4.4 ``ClarityCheck`` contract)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str = Field(pattern=r"^q[1-3]$")
    dimension: ClarityDimension
    text: str = Field(min_length=1, max_length=200)
    short: str = Field(min_length=1, max_length=40)
    options: list[str] = Field(min_length=2, max_length=4)
    best_guess: str = Field(min_length=1)

    @model_validator(mode="after")
    def options_are_distinct_and_hold_the_best_guess(self) -> ClarityQuestion:
        if any(not option.strip() or len(option) > 80 for option in self.options):
            raise ValueError("each option is 1-80 characters")
        if len(set(self.options)) != len(self.options):
            raise ValueError("options repeat")
        if self.best_guess not in self.options:
            raise ValueError("best_guess is not one of the options")
        return self


class ClarityCheck(BaseModel):
    """The check's result: 0-3 questions, ``q1``..``q3`` in order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    questions: list[ClarityQuestion] = Field(
        default_factory=list, max_length=MAX_CLARITY_QUESTIONS
    )

    @model_validator(mode="after")
    def ids_follow_the_order(self) -> ClarityCheck:
        if [q.id for q in self.questions] != [
            f"q{n}" for n in range(1, len(self.questions) + 1)
        ]:
            raise ValueError("question ids must be q1..qn in order")
        return self


NO_QUESTIONS = ClarityCheck()
ClarityChecker: TypeAlias = Callable[[str, ConfigSettings], Awaitable[ClarityCheck]]


def validated_check(draft: ClarityCheckDraft) -> ClarityCheck:
    """Stamp ``q1``..``qn`` on the drafted questions, or ask nothing at all.

    More than three questions, a best guess that is not one of its options, or
    any other invalid field drops the whole output (spec §4.4): a check that
    cannot be trusted asks nothing rather than asking something half-formed.
    """
    try:
        return ClarityCheck(
            questions=[
                ClarityQuestion(id=f"q{n}", **question.model_dump())
                for n, question in enumerate(draft.questions, start=1)
            ]
        )
    except ValidationError:
        return NO_QUESTIONS


CLARITY_SYSTEM_PROMPT = (
    "You check a research question before any research starts. You have no "
    "tools and need none: the question is printed in the request."
)

CLARITY_INSTRUCTION = (
    "Decide whether the question leaves open something whose answer would "
    "change the research plan, and ask the reader about it.\n"
    "- Ask only about these dimensions: geography (which place the answer "
    "covers), period (which time span it covers), purpose (what the reader "
    "needs it for) and scope (how broad it should be).\n"
    "- Ask only when the answer would change the plan. Never ask about "
    "something the question already states.\n"
    "- Ask at most 3 questions. When nothing material is open, return an empty "
    "questions list.\n"
    "- For each question give: dimension; text, the question in plain words "
    "(at most 200 characters); short, a one- or two-word label for it (at most "
    "40 characters); options, 2 to 4 short answers the reader can pick (each at "
    "most 80 characters); best_guess, the option you would assume if the reader "
    "does not answer, copied exactly from options."
)


def clarity_messages(question: str) -> list[ChatMessage]:
    """The one tool-free request the live check sends."""
    return [
        ChatMessage(role="developer", content=CLARITY_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"# Check requirements\n{CLARITY_INSTRUCTION}\n\n"
                f"# Research question\n{question}"
            ),
        ),
    ]


def clarity_llm_config(llm: LLMConfig) -> LLMConfig:
    """The configured provider and model, with thinking disabled (spec §4.4)."""
    return llm.model_copy(update={"thinking_mode": "disabled"})


async def live_clarity_check(
    question: str,
    settings: ConfigSettings,
    *,
    completer: Any | None = None,
) -> ClarityCheck:
    """Ask the configured provider what, if anything, to ask the reader.

    ``completer`` replaces the provider in tests; production builds the chat
    adapter from ``settings.llm`` with thinking disabled. Provider failures
    propagate: the session store treats any exception as "ask nothing".
    """
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="deep-research-clarity-check", api_key=None
        )
    )
    provider = (
        completer
        if completer is not None
        else build_chat_provider(clarity_llm_config(settings.llm), tracker)
    )
    async with tracker.session_span(CLARITY_TRACE_SESSION, question):
        draft = await provider.complete_structured(
            clarity_messages(question),
            ClarityCheckDraft,
            agent_name=None,
            max_tokens=CLARITY_MAX_TOKENS,
        )
    return validated_check(draft)


# --- replay mode --------------------------------------------------------------

REPLAY_CLARIFY_HEADER = "x-replay-clarify"
requested_clarify: ContextVar[bool] = ContextVar(
    "deep_research_replay_clarify", default=False
)
"""Set by ``ReplayCaseMiddleware`` from ``X-Replay-Clarify`` on ``POST /research``.

It travels into the session's task the way ``requested_case`` does: the task is
created inside the request's own context (``SessionStore.start``)."""

REPLAY_CLARITY_CHECK = ClarityCheck(
    questions=[
        ClarityQuestion(
            id="q1",
            dimension="geography",
            text="Which region should this cover?",
            short="Region",
            options=["United States", "European Union", "Global"],
            best_guess="Global",
        ),
        ClarityQuestion(
            id="q2",
            dimension="period",
            text="How recent should the sources be?",
            short="Period",
            options=["Last 12 months", "Since 2023", "Any time"],
            best_guess="Since 2023",
        ),
        ClarityQuestion(
            id="q3",
            dimension="purpose",
            text="What will you use it for?",
            short="For",
            options=[
                "General understanding",
                "A project or investment decision",
                "Policy or regulation work",
            ],
            best_guess="General understanding",
        ),
    ]
)
"""The fixed set the replay checker asks when ``X-Replay-Clarify: on`` (pick 4B's own copy)."""


async def scripted_clarity_check(question: str, settings: object) -> ClarityCheck:
    """Replay mode's checker: no questions unless the request asked for them."""
    del question, settings
    return REPLAY_CLARITY_CHECK if requested_clarify.get() else NO_QUESTIONS


# --- answers --------------------------------------------------------------------


class AnswerValidationError(ValueError):
    """Submitted answers that do not fit the questions asked (a 422)."""

    def __init__(self, issues: Sequence[tuple[tuple[str | int, ...], str]]) -> None:
        super().__init__("the answers do not fit the questions asked")
        self.issues = list(issues)

    def errors(self) -> list[dict[str, Any]]:
        """The issues in ``RequestValidationError``'s own shape: location and type only."""
        return [
            {"loc": location, "type": kind, "msg": "invalid answer"}
            for location, kind in self.issues
        ]


def resolve_answers(
    questions: Sequence[ClarityQuestion],
    submitted: Iterable[Any] = (),
) -> tuple[ReaderAnswer, ...]:
    """Every question's answer, in question order; a skipped one takes its best guess.

    ``submitted`` holds objects with ``question_id`` and exactly one of
    ``choice`` or ``text`` (``api.models.ClarificationAnswer``). An unknown
    question, a question answered twice, or a choice that is not one of its
    options raises ``AnswerValidationError`` naming each one.
    """
    by_id = {question.id: question for question in questions}
    given: dict[str, tuple[str, str]] = {}
    issues: list[tuple[tuple[str | int, ...], str]] = []
    for index, answer in enumerate(submitted):
        location: tuple[str | int, ...] = ("body", "answers", index)
        question = by_id.get(answer.question_id)
        if question is None:
            issues.append(((*location, "question_id"), "unknown_question"))
            continue
        if answer.question_id in given:
            issues.append(((*location, "question_id"), "duplicate_question"))
            continue
        if answer.choice is not None:
            if answer.choice not in question.options:
                issues.append(((*location, "choice"), "choice_not_offered"))
                continue
            given[answer.question_id] = (answer.choice, "chosen")
        else:
            given[answer.question_id] = (answer.text, "typed")
    if issues:
        raise AnswerValidationError(issues)
    return tuple(
        ReaderAnswer(
            question_id=question.id,
            dimension=question.dimension,
            text=question.text,
            short=question.short,
            value=given.get(question.id, (question.best_guess, "best_guess"))[0],
            source=given.get(question.id, (question.best_guess, "best_guess"))[1],
        )
        for question in questions
    )


def clarification_requested_event(
    questions: Sequence[ClarityQuestion], deadline_at: datetime
) -> ResearchEvent:
    """``session.clarification.requested``: the questions and when the wait ends."""
    return ResearchEvent(
        event_type="session.clarification.requested",
        source="api",
        message="The research is waiting for the reader's answers.",
        metadata={
            "questions": [question.model_dump(mode="json") for question in questions],
            "deadline_at": deadline_at.isoformat(timespec="milliseconds"),
        },
    )


def clarification_answered_event(
    answers: Sequence[ReaderAnswer], reason: str
) -> ResearchEvent:
    """``session.clarification.answered``: what the run starts with, and why now."""
    return ResearchEvent(
        event_type="session.clarification.answered",
        source="api",
        message="The research starts with the reader's answers.",
        metadata={
            "answers": [
                {
                    "question_id": answer.question_id,
                    "value": answer.value,
                    "source": answer.source,
                }
                for answer in answers
            ],
            "reason": reason,
        },
    )
```

`src/deep_research/api/models.py` — replace

```python
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
```

with

```python
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
```

`src/deep_research/api/models.py` — replace

```python
SessionStatus = Literal[
    "running",
```

with

```python
# ``needs_input`` is the one waiting status (live-briefs spec §4.4): the one-time
# check is waiting for the reader's answers. It is not terminal, and it returns to
# ``running`` when the answers arrive, the reader skips, or the wait times out.
SessionStatus = Literal[
    "running",
    "needs_input",
```

`src/deep_research/api/models.py` — replace

```python
    ``>= 0`` rather than ``>= 1``.
    """
```

with

```python
    ``>= 0`` rather than ``>= 1``.

    ``ask_clarifying_questions`` turns the one-time check on (live-briefs spec
    §4.4, D16): before planning, a question that leaves something material open
    gets up to three questions for the reader. Off, no check call is made.
    """
```

`src/deep_research/api/models.py` — replace

```python
    config_overrides: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("config_overrides")
```

with

```python
    config_overrides: dict[str, JsonValue] = Field(default_factory=dict)
    ask_clarifying_questions: bool = True

    @field_validator("config_overrides")
```

`src/deep_research/api/models.py` — replace

```python
class CoverageProgressResponse(ApiModel):
```

with

```python
class ClarificationAnswer(ApiModel):
    """One answer to the one-time check: an offered option, or the reader's own text."""

    question_id: str = Field(min_length=1)
    choice: str | None = Field(default=None, min_length=1)
    text: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def exactly_one_answer(self) -> ClarificationAnswer:
        if (self.choice is None) == (self.text is None):
            raise ValueError("an answer carries exactly one of choice or text")
        return self


class ClarificationAnswersRequest(ApiModel):
    """``POST /research/{id}/answers``: the reader's answers, once (spec §4.4).

    A question left out takes its best guess. ``skip`` records that the reader
    chose to start now ("Just start") with whatever they had answered.
    """

    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=3)
    skip: bool = False


class CoverageProgressResponse(ApiModel):
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_clarify.py -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```

Expected: `23 passed`, then `154 passed` (the API package: the existing 131 and these 23; observed in planning).

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/api/clarify.py src/deep_research/api/models.py tests/test_api/test_clarify.py
git commit -m "feat(api): the one-time check's contract, checkers, answers and events"
```

---

### Task 5: `needs_input`, the answers route and the replay header (spec §4.4 Lifecycle, Route; §4.8 rows; AC10, AC12–AC14; §4.9 API docs)

**Files** (line numbers at `3565397`):
- Modify: `src/deep_research/api/sessions.py`:
  - `:6-27`: docstring tail and imports;
  - `:33`: types;
  - `:59`: `ResearchSession.clarification`;
  - `:136-137`: `__init__`;
  - `:148-179`: `start`;
  - `:200`: `submit_answers`, new;
  - `:254-301`: `_run`, with the new `_clarify`.
- Modify: `src/deep_research/api/app.py`:
  - `:22-35`: imports;
  - `:58-59`: `_SAFE_MESSAGES`;
  - `:172-183`: `create_app`;
  - `:221-240`: `start_research` and the new route.
- Modify: `src/deep_research/api/replay.py`:
  - `:10`, `:21`, `:28`, `:40`: docstring and imports;
  - `:76-111`: `ReplayRunner.__call__`;
  - `:161`: `ReplayCaseMiddleware`.
- Modify: `README.md` ("FastAPI Interface" `:757-832`, "Run the app" `:864-865`) and `docs/design/api-gaps.md:11-12`, `:29`, `:40-41`, `:48`, `:64`.
- Create: `tests/test_api/conftest.py`, `tests/test_api/test_clarification.py`.
- Test: `tests/test_api/test_app.py:19-25` (`valid_preflight`).

**Interfaces:**
- Consumes: Task 4's `deep_research.api.clarify` and the new models; Task 2's `HitlConfig`, `ReaderAnswer` and `run_research(reader_answers=...)`.
- Produces:
  - `SessionStore(*, runner, clarity_checker: ClarityChecker | None = None)`.
  - `SessionStore.start(..., ask_clarifying_questions: bool = False, settings: Any = None, hitl: HitlConfig | None = None)`.
  - `SessionStore.submit_answers(session_id, request: ClarificationAnswersRequest) -> ResearchSession`. It raises `KeyError` for an unknown session, `NotWaitingForInput` when the session is not waiting (not `needs_input`, already answered, or past `deadline_at`), and `AnswerValidationError` when an answer does not fit.
  - `ResearchSession.clarification: PendingClarification | None`.
  - `create_app(..., clarity_checker: ClarityChecker | None = None)`; `None` means the mode's own checker (ambiguity 3).
  - The route `POST /research/{session_id}/answers`.
  - `ReplayRunner.__call__(..., reader_answers=())`.
  - `ReplayCaseMiddleware` sets `requested_clarify` from `X-Replay-Clarify: on`, for `POST /research` only.
  - The autouse fixture `live_check_calls: list[str]`, in `tests/test_api`.

- [ ] **Step 1: Write the failing tests**

`conftest.py` keeps every API test away from the live checker (ambiguity 4). `valid_preflight` now returns the default settings, because the route reads their `hitl` block. The lifecycle tests drive `SessionStore` with scripted checkers and `ScriptedRunner` (`tests/test_api/fakes.py`). The replay tests run the real graph offline.

Create `tests/test_api/conftest.py`:

```python
"""API test guard: no API test reaches the live one-time check (live-briefs spec §4.4).

``create_app`` picks ``live_clarity_check`` for a live-mode app built without a
``clarity_checker``, and every live-mode test app here is built that way. The
live checker is replaced for every test in this package by one that asks
nothing and records the question it was given, so no test can build a provider,
and a test can still see whether the check ran (``live_check_calls``).
"""

from __future__ import annotations

import importlib

import pytest

from deep_research.api.clarify import NO_QUESTIONS, ClarityCheck

# ``deep_research.api`` re-exports the module-level FastAPI ``app``, which shadows
# the ``app`` submodule as a package attribute, so the module is fetched by name.
app_module = importlib.import_module("deep_research.api.app")


@pytest.fixture(autouse=True)
def live_check_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def asks_nothing(question: str, settings: object) -> ClarityCheck:
        del settings
        calls.append(question)
        return NO_QUESTIONS

    monkeypatch.setattr(app_module, "live_clarity_check", asks_nothing)
    return calls
```

`tests/test_api/test_app.py` — replace

```python
from deep_research.runtime.errors import configuration_error
from tests.test_api.fakes import GateRunner, ScriptedRunner


def valid_preflight(**kwargs: Any) -> object:
    """Preflight double that never refuses a request."""
    return object()
```

with

```python
from deep_research.runtime.errors import configuration_error
from deep_research.utils.config import ConfigSettings
from tests.test_api.fakes import GateRunner, ScriptedRunner


def valid_preflight(**kwargs: Any) -> ConfigSettings:
    """Preflight double that never refuses a request: the default settings.

    The route reads the one-time check's timings from them (``settings.hitl``,
    live-briefs spec §4.4); the checker itself is replaced by the package's
    ``live_check_calls`` fixture (``tests/test_api/conftest.py``).
    """
    return ConfigSettings()
```

Create `tests/test_api/test_clarification.py`:

```python
"""The one-time check's lifecycle and route (live-briefs spec §4.4, §4.8; AC10-AC14).

Store-level tests drive ``SessionStore`` with scripted checkers and runners; the
route tests go through ``TestClient``; the replay tests run the real graph
offline with the replay-mode checker. No provider is ever reached.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.clarify import (
    NO_QUESTIONS,
    REPLAY_CLARITY_CHECK,
    AnswerValidationError,
    ClarityCheck,
    resolve_answers,
    scripted_clarity_check,
)
from deep_research.api.models import ClarificationAnswer, ClarificationAnswersRequest
from deep_research.api.sessions import NotWaitingForInput, SessionStore
from deep_research.utils.config import ConfigSettings, HitlConfig, apply_config_overrides
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import ScriptedRunner
from tests.test_api.replay_support import guarded
from tests.test_api.test_app import valid_preflight
from tests.test_api.test_replay import replay_app

TERMINAL = {"completed", "max_iterations", "incomplete", "failed"}
QUESTION = "What limits grid-scale battery storage?"
FAST = HitlConfig(check_timeout_s=0.5, answer_wait_s=5.0)


class Checker:
    """A scripted ``ClarityChecker``: returns, raises, or hangs; records each call."""

    def __init__(self, reply: ClarityCheck | BaseException | None = REPLAY_CLARITY_CHECK) -> None:
        self.reply = reply
        self.calls: list[tuple[str, object]] = []

    async def __call__(self, question: str, settings: object) -> ClarityCheck:
        self.calls.append((question, settings))
        if self.reply is None:
            await asyncio.Event().wait()  # hangs until cancelled by the check's timeout
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _start(store: SessionStore, *, hitl: HitlConfig = FAST, ask: bool = True) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml",
        ask_clarifying_questions=ask, settings=ConfigSettings(), hitl=hitl,
    )


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


def _request(*answers: dict[str, str], skip: bool = False) -> ClarificationAnswersRequest:
    return ClarificationAnswersRequest(answers=[ClarificationAnswer(**a) for a in answers], skip=skip)


# --- the lifecycle ------------------------------------------------------------


@pytest.mark.asyncio
async def test_answers_move_the_session_from_needs_input_to_the_run() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    requested = session.events[-1]
    assert requested.event_type == "session.clarification.requested"
    assert [q["id"] for q in requested.metadata["questions"]] == ["q1", "q2", "q3"]
    assert runner.calls == []

    store.submit_answers("s1", _request({"question_id": "q1", "choice": "United States"}, {"question_id": "q2", "text": "since 2021"}))
    await session.task

    expected = resolve_answers(
        REPLAY_CLARITY_CHECK.questions,
        [ClarificationAnswer(question_id="q1", choice="United States"), ClarificationAnswer(question_id="q2", text="since 2021")],
    )
    assert runner.calls[0]["reader_answers"] == expected
    assert _types(session.events) == ["session.clarification.requested", "session.clarification.answered"]
    assert session.events[-1].metadata["reason"] == "answered"
    assert session.status == "completed"
    assert session.clarification is None


@pytest.mark.asyncio
async def test_just_start_skips_with_what_was_answered_and_best_guesses_for_the_rest() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    store.submit_answers("s1", _request({"question_id": "q1", "choice": "Global"}, skip=True))
    await session.task

    answered = session.events[1]
    assert answered.metadata["reason"] == "skipped"
    assert [(a["value"], a["source"]) for a in answered.metadata["answers"]] == [
        ("Global", "chosen"), ("Since 2023", "best_guess"), ("General understanding", "best_guess"),
    ]
    assert [a.source for a in runner.calls[0]["reader_answers"]] == ["chosen", "best_guess", "best_guess"]


@pytest.mark.asyncio
async def test_no_answer_starts_the_run_on_best_guesses_when_the_wait_ends() -> None:
    """AC12: the run starts within answer_wait_s + 2 s, every answer a best guess."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    started = time.monotonic()
    _start(store, hitl=HitlConfig(answer_wait_s=0.3))
    session = store.require("s1")
    await session.task

    assert time.monotonic() - started < 0.3 + 2
    answered = session.events[1]
    assert answered.metadata["reason"] == "timed_out"
    assert {a["source"] for a in answered.metadata["answers"]} == {"best_guess"}
    assert [a.value for a in runner.calls[0]["reader_answers"]] == ["Global", "Since 2023", "General understanding"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [RuntimeError("provider down"), NO_QUESTIONS], ids=["check_failed", "no_questions"])
async def test_a_failed_or_empty_check_starts_the_run_exactly_as_before(reply: Any) -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker(reply))
    _start(store)
    session = store.require("s1")
    await session.task

    assert "reader_answers" not in runner.calls[0]
    assert session.events == []
    assert session.status == "completed"


@pytest.mark.asyncio
async def test_a_check_that_hangs_delays_the_run_by_at_most_check_timeout_s() -> None:
    """AC14."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker(None))
    started = time.monotonic()
    _start(store, hitl=HitlConfig(check_timeout_s=0.2))
    session = store.require("s1")
    await session.task

    assert time.monotonic() - started < 0.2 + 0.5
    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_with_the_setting_off_no_check_call_is_made() -> None:
    """AC13."""
    checker = Checker()
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=checker)
    _start(store, ask=False)
    await store.require("s1").task

    assert checker.calls == []
    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_a_store_without_a_checker_never_asks() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner)
    _start(store)
    await store.require("s1").task

    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_closing_during_the_wait_reads_as_an_interrupted_run() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    await store.close()

    assert session.status == "running"
    assert session.finished_at is not None
    assert session.clarification is None
    assert _types(session.events) == ["session.clarification.requested"]
    assert [e.event_type async for e in store.iter_events("s1")] == ["session.clarification.requested"]


@pytest.mark.asyncio
async def test_answers_are_taken_once_and_only_while_the_session_waits() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    with pytest.raises(KeyError):
        store.submit_answers("nope", _request())
    await _until(lambda: session.status == "needs_input")
    with pytest.raises(AnswerValidationError):
        store.submit_answers("s1", _request({"question_id": "q1", "choice": "Asia"}))
    assert session.status == "needs_input"

    store.submit_answers("s1", _request())
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())
    await session.task
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())


@pytest.mark.asyncio
async def test_answers_after_the_deadline_are_refused() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store, hitl=HitlConfig(answer_wait_s=5.0))
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")
    assert session.clarification is not None
    session.clarification.deadline_at = session.clarification.deadline_at.replace(year=2000)

    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())
    session.task.cancel()
    await asyncio.gather(session.task, return_exceptions=True)


# --- the route ------------------------------------------------------------------


def _wait(client: TestClient, session_id: str, statuses: set[str], timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/research/{session_id}/status").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.01)
    raise AssertionError(f"session never reached {statuses}")


def _overriding_preflight(**kwargs: Any) -> ConfigSettings:
    return apply_config_overrides(ConfigSettings(), kwargs["config_overrides"])


def test_the_answers_route_takes_answers_once_and_the_run_starts() -> None:
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        assert _wait(client, session_id, {"needs_input"})["status"] == "needs_input"

        posted = client.post(
            f"/research/{session_id}/answers",
            json={"answers": [{"question_id": "q1", "choice": "European Union"}, {"question_id": "q3", "text": "a grant proposal"}], "skip": False},
        )
        again = client.post(f"/research/{session_id}/answers", json={"answers": []})
        status = _wait(client, session_id, TERMINAL)

    assert posted.status_code == 202
    assert posted.json()["session_id"] == session_id
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "not_waiting_for_input"
    assert status["status"] == "completed"
    assert [(a.value, a.source) for a in runner.calls[0]["reader_answers"]] == [
        ("European Union", "chosen"), ("Since 2023", "best_guess"), ("a grant proposal", "typed"),
    ]


@pytest.mark.parametrize(
    ("body", "issue"),
    [
        ({"answers": [{"question_id": "q7", "choice": "Global"}]}, ("body.answers.0.question_id", "unknown_question")),
        ({"answers": [{"question_id": "q1", "choice": "Asia"}]}, ("body.answers.0.choice", "choice_not_offered")),
        ({"answers": [{"question_id": "q1", "choice": "Global"}, {"question_id": "q1", "choice": "Global"}]}, ("body.answers.1.question_id", "duplicate_question")),
        ({"answers": [{"question_id": "q2", "text": "x" * 201}]}, ("body.answers.0.text", "string_too_long")),
        ({"answers": [{"question_id": "q2", "text": "x", "choice": "Global"}]}, ("body.answers.0", "value_error")),
    ],
    ids=["unknown_question", "choice_not_offered", "duplicate", "text_over_200", "choice_and_text"],
)
def test_answers_that_do_not_fit_are_a_safe_422_and_the_session_keeps_waiting(body: dict[str, Any], issue: tuple[str, str]) -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, {"needs_input"})
        response = client.post(f"/research/{session_id}/answers", json=body)
        still = client.get(f"/research/{session_id}/status").json()["status"]

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert (issue[0], issue[1]) in {(i["location"], i["type"]) for i in error["issues"]}
    assert "Asia" not in response.text and "xxxx" not in response.text
    assert still == "needs_input"


def test_the_answers_route_is_404_for_an_unknown_session_and_409_when_nothing_was_asked() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight, clarity_checker=Checker(NO_QUESTIONS))
    with TestClient(app) as client:
        unknown = client.post("/research/nope/answers", json={"answers": []})
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, TERMINAL)
        never_asked = client.post(f"/research/{session_id}/answers", json={"answers": []})

    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "session_not_found")
    assert (never_asked.status_code, never_asked.json()["error"]["code"]) == (409, "not_waiting_for_input")


def test_the_request_flag_defaults_on_and_off_makes_no_check_call(live_check_calls: list[str]) -> None:
    """AC10 and AC13 at the route: the live-mode default checker (replaced by the
    package guard) runs for a request without the flag and never for one with it off."""
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    with TestClient(app) as client:
        first = client.post("/research", json={"query": "First question"}).json()["session_id"]
        second = client.post("/research", json={"query": "Second question", "ask_clarifying_questions": False}).json()["session_id"]
        statuses = [_wait(client, sid, TERMINAL)["status"] for sid in (first, second)]

    assert live_check_calls == ["First question"]
    assert statuses == ["completed", "completed"]
    assert all("reader_answers" not in call for call in runner.calls)


def test_the_wait_is_read_from_the_requests_own_settings() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=_overriding_preflight, clarity_checker=Checker())
    started = time.monotonic()
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION, "config_overrides": {"hitl": {"answer_wait_s": 0.4}}}).json()["session_id"]
        status = _wait(client, session_id, TERMINAL)
        frames = client.get(f"/research/{session_id}/stream").text

    assert status["status"] == "completed"
    assert time.monotonic() - started < 0.4 + 2
    assert '"reason":"timed_out"' in frames


def test_a_replay_mode_app_checks_with_the_scripted_checker(tmp_path: Path) -> None:
    app = replay_app(tmp_path)

    assert app.state.session_store._clarity_checker is scripted_clarity_check


# --- replay mode, the real graph ----------------------------------------------------


def test_replay_asks_nothing_without_the_header(tmp_path: Path) -> None:
    """AC10: the default flag is on and the question is clear, so nothing changes."""
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        session_id = client.post("/research", json={"query": "q"}).json()["session_id"]
        status = _wait(client, session_id, TERMINAL, timeout=30)
        names = [line[7:] for line in client.get(f"/research/{session_id}/stream").text.splitlines() if line.startswith("event: ")]

    assert status["status"] == "completed"
    assert not any(name.startswith("session.clarification") for name in names)
    assert names[0] == "graph.session.started"


def test_replay_with_the_header_asks_then_plans_with_the_readers_answers(tmp_path: Path) -> None:
    """AC11 on the replay server: needs_input, the answers, then the run completes."""
    app = replay_app(tmp_path)
    with guarded(), TestClient(app) as client:
        session_id = client.post("/research", json={"query": "q"}, headers={"X-Replay-Clarify": "on"}).json()["session_id"]
        waiting = _wait(client, session_id, {"needs_input"}, timeout=30)
        posted = client.post(
            f"/research/{session_id}/answers",
            json={"answers": [{"question_id": "q1", "choice": "United States"}, {"question_id": "q2", "text": "since 2021"}]},
        )
        status = _wait(client, session_id, TERMINAL, timeout=30)
        names = [line[7:] for line in client.get(f"/research/{session_id}/stream").text.splitlines() if line.startswith("event: ")]
        contract = app.state.session_store.require(session_id).outcome.state.answer_contract

    assert waiting["status"] == "needs_input"
    assert posted.status_code == 202
    assert status["status"] == "completed"
    assert names[:3] == ["session.clarification.requested", "session.clarification.answered", "graph.session.started"]
    assert contract.geographic_scope == "United States"
    assert contract.assumptions[-3:] == [
        "Reader said: Region = United States",
        "Reader said: Period = since 2021",
        "Assumed (best guess): For = General understanding",
    ]
```

- [ ] **Step 2: Run them to see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_clarification.py -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_app.py -q -x
```

Expected:
- `tests/test_api/test_clarification.py` fails with `1 error` at collection: `ImportError: cannot import name 'NotWaitingForInput' from 'deep_research.api.sessions'`.
- `tests/test_api/test_app.py` stops at its first test with ``AttributeError: <module 'deep_research.api.app' from '…/src/deep_research/api/app.py'> has no attribute 'live_clarity_check'``. The guard needs the name that Step 3 imports into `app.py`, so until then every API test errors the same way.

- [ ] **Step 3: Add the lifecycle, the route and the header**

The check runs inside the session's own task, before the runner.
- With no questions, the runner is called exactly as before (ambiguity 10).
- With questions, the session waits with `asyncio.wait`, which honours an answer landing at the boundary (ambiguity 7).
- The route refuses by wall clock. The `finally` block never leaves `needs_input` behind (ambiguity 9).

`src/deep_research/api/sessions.py` — replace

```python
iteration and safe terminal state. Nothing here touches the network, the
file system, or a provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeAlias

from pydantic import JsonValue

from deep_research.api.models import (
    CoverageProgressResponse,
    EvidenceCountsResponse,
    SessionStatus,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import ResearchError, ResearchEvent
```

with

```python
iteration and safe terminal state. Nothing here touches the network, the
file system, or a provider: the one-time check reaches a provider only through
the injected ``ClarityChecker``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeAlias

from pydantic import JsonValue

from deep_research.api.clarify import (
    ClarityChecker,
    ClarityQuestion,
    clarification_answered_event,
    clarification_requested_event,
    resolve_answers,
)
from deep_research.api.models import (
    ClarificationAnswersRequest,
    CoverageProgressResponse,
    EvidenceCountsResponse,
    SessionStatus,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import HitlConfig
from deep_research.utils.types import ReaderAnswer, ResearchError, ResearchEvent
```

`src/deep_research/api/sessions.py` — replace

```python
ResearchRunner: TypeAlias = Callable[..., Awaitable[ResearchOutcome]]
```

with

```python
ResearchRunner: TypeAlias = Callable[..., Awaitable[ResearchOutcome]]
_log = logging.getLogger(__name__)


class NotWaitingForInput(Exception):
    """Answers arrived for a session that is not waiting for them (a 409)."""


@dataclass(frozen=True, slots=True)
class ClarificationSubmission:
    """The reader's resolved answers, and whether they chose to start now."""

    answers: tuple[ReaderAnswer, ...]
    skipped: bool


@dataclass(slots=True)
class PendingClarification:
    """The one-time check a ``needs_input`` session is waiting on (spec §4.4)."""

    questions: tuple[ClarityQuestion, ...]
    deadline_at: datetime
    submitted: asyncio.Future[ClarificationSubmission]
```

`src/deep_research/api/sessions.py` — replace

```python
    changed: asyncio.Event = field(default_factory=asyncio.Event)
```

with

```python
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    clarification: PendingClarification | None = None
    """The check this session waits on while ``needs_input``, else ``None``."""
```

`src/deep_research/api/sessions.py` — replace

```python
    def __init__(self, *, runner: ResearchRunner) -> None:
        self._runner = runner
```

with

```python
    def __init__(
        self,
        *,
        runner: ResearchRunner,
        clarity_checker: ClarityChecker | None = None,
    ) -> None:
        self._runner = runner
        self._clarity_checker = clarity_checker
```

`src/deep_research/api/sessions.py` — replace

```python
        config_path: str,
    ) -> ResearchSession:
        """Register a running session synchronously and schedule its run.
```

with

```python
        config_path: str,
        ask_clarifying_questions: bool = False,
        settings: Any = None,
        hitl: HitlConfig | None = None,
    ) -> ResearchSession:
        """Register a running session synchronously and schedule its run.

        ``ask_clarifying_questions`` runs the one-time check before the runner
        (live-briefs spec §4.4) when the store has a ``clarity_checker``;
        ``settings`` is what the checker reads and ``hitl`` holds its timings.
```

`src/deep_research/api/sessions.py` — replace

```python
                config_path=config_path,
            )
        )
        return session
```

with

```python
                config_path=config_path,
                ask_clarifying_questions=ask_clarifying_questions,
                settings=settings,
                hitl=hitl or HitlConfig(),
            )
        )
        return session
```

`src/deep_research/api/sessions.py` — replace

```python
    async def iter_events(
```

with

```python
    def submit_answers(
        self, session_id: str, request: ClarificationAnswersRequest
    ) -> ResearchSession:
        """Hand the reader's answers to a session waiting in ``needs_input``.

        Raises ``KeyError`` for an unknown session, ``NotWaitingForInput`` when
        the session is not waiting (never asked, already answered, or past its
        deadline), and ``AnswerValidationError`` when an answer does not fit the
        questions asked. Answers are accepted once; the session's own task
        publishes them and starts the run.
        """
        session = self.require(session_id)
        pending = session.clarification
        if (
            session.status != "needs_input"
            or pending is None
            or pending.submitted.done()
            or datetime.now(timezone.utc) >= pending.deadline_at
        ):
            raise NotWaitingForInput(session_id)
        answers = resolve_answers(pending.questions, request.answers)
        pending.submitted.set_result(
            ClarificationSubmission(answers=answers, skipped=request.skip)
        )
        return session

    async def iter_events(
```

`src/deep_research/api/sessions.py` — replace

```python
        config_path: str,
    ) -> None:
        """Drive one runner call and fold its result into the session.

        Failures become status ``failed`` with safe enumerated records —
        never exception text, provider text, or request values. Cancellation
        is not a failure and always propagates; the ``finally`` still closes
        the session out so subscribers wake and readers see timestamps.
        """
        try:
            outcome = await self._runner(
```

with

```python
        config_path: str,
        ask_clarifying_questions: bool = False,
        settings: Any = None,
        hitl: HitlConfig | None = None,
    ) -> None:
        """Drive one runner call and fold its result into the session.

        Failures become status ``failed`` with safe enumerated records —
        never exception text, provider text, or request values. Cancellation
        is not a failure and always propagates; the ``finally`` still closes
        the session out so subscribers wake and readers see timestamps. A run
        cancelled while it waited for answers reads ``running`` with
        ``finished_at`` set, like any other interrupted run.

        With the one-time check on and questions asked, the runner is called
        with ``reader_answers``; otherwise it is called exactly as before.
        """
        try:
            extra: dict[str, Any] = {}
            if ask_clarifying_questions and self._clarity_checker is not None:
                answers = await self._clarify(
                    session, query=query, settings=settings, hitl=hitl or HitlConfig()
                )
                if answers is not None:
                    extra["reader_answers"] = answers
            outcome = await self._runner(
```

`src/deep_research/api/sessions.py` — replace

```python
                event_handler=session.publish,
            )
```

with

```python
                event_handler=session.publish,
                **extra,
            )
```

`src/deep_research/api/sessions.py` — replace

```python
        finally:
            session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()
```

with

```python
        finally:
            if session.status == "needs_input":
                session.status = "running"
            session.clarification = None
            session.finished_at = datetime.now(timezone.utc)
            session.current_agent = None
            session.changed.set()

    async def _clarify(
        self,
        session: ResearchSession,
        *,
        query: str,
        settings: Any,
        hitl: HitlConfig,
    ) -> tuple[ReaderAnswer, ...] | None:
        """The one-time check (spec §4.4): the answers, or ``None`` when nothing was asked.

        The check call gets ``hitl.check_timeout_s``; any failure, a timeout
        included, asks nothing. With questions, the session waits in
        ``needs_input`` for at most ``hitl.answer_wait_s``; whatever the reader
        left unanswered takes its best guess.
        """
        assert self._clarity_checker is not None
        try:
            async with asyncio.timeout(hitl.check_timeout_s):
                check = await self._clarity_checker(query, settings)
            questions = tuple(check.questions)
        except Exception as error:  # noqa: BLE001 - the check never blocks a run
            _log.warning("one-time check skipped: %s", type(error).__name__)
            return None
        if not questions:
            return None
        deadline = datetime.now(timezone.utc) + timedelta(seconds=hitl.answer_wait_s)
        pending = PendingClarification(
            questions=questions,
            deadline_at=deadline,
            submitted=asyncio.get_running_loop().create_future(),
        )
        session.clarification = pending
        session.status = "needs_input"
        session.publish(clarification_requested_event(questions, deadline))
        await asyncio.wait({pending.submitted}, timeout=hitl.answer_wait_s)
        session.clarification = None
        if pending.submitted.done():
            submission = pending.submitted.result()
            answers = submission.answers
            reason = "skipped" if submission.skipped else "answered"
        else:
            pending.submitted.cancel()
            answers = resolve_answers(questions)
            reason = "timed_out"
        session.status = "running"
        session.publish(clarification_answered_event(answers, reason))
        return answers
```

`src/deep_research/api/app.py` — replace

```python
from deep_research.api.events import api_error_event, encode_sse
```

with

```python
from deep_research.api.clarify import (
    AnswerValidationError,
    ClarityChecker,
    live_clarity_check,
    scripted_clarity_check,
)
from deep_research.api.events import api_error_event, encode_sse
```

`src/deep_research/api/app.py` — replace

```python
    ApiErrorResponse,
    ResearchRequest,
```

with

```python
    ApiErrorResponse,
    ClarificationAnswersRequest,
    ResearchRequest,
```

`src/deep_research/api/app.py` — replace

```python
from deep_research.api.sessions import (
    ResearchRunner,
```

with

```python
from deep_research.api.sessions import (
    NotWaitingForInput,
    ResearchRunner,
```

`src/deep_research/api/app.py` — replace

```python
    "evidence_unavailable": "Research session finished without an evidence log.",
}
```

with

```python
    "evidence_unavailable": "Research session finished without an evidence log.",
    "not_waiting_for_input": "Research session is not waiting for answers.",
}
```

`src/deep_research/api/app.py` — replace

```python
    mode: Literal["live", "replay"] = "live",
) -> FastAPI:
    """Build the local FastAPI interface around one process's session store."""
```

with

```python
    mode: Literal["live", "replay"] = "live",
    clarity_checker: ClarityChecker | None = None,
) -> FastAPI:
    """Build the local FastAPI interface around one process's session store.

    ``clarity_checker`` decides the one-time check's questions (live-briefs
    spec §4.4). Left ``None`` it follows ``mode``: live mode asks the
    configured provider, and replay mode uses the scripted checker, so a
    replay server can never reach a provider for the check.
    """
    if clarity_checker is None:
        clarity_checker = (
            live_clarity_check if mode == "live" else scripted_clarity_check
        )
```

`src/deep_research/api/app.py` — replace

```python
    store = SessionStore(runner=runner)
```

with

```python
    store = SessionStore(runner=runner, clarity_checker=clarity_checker)
```

`src/deep_research/api/app.py` — replace

```python
            preflight(
                config_path=config_path,
```

with

```python
            settings = preflight(
                config_path=config_path,
```

`src/deep_research/api/app.py` — replace

```python
            config_path=config_path,
        )
        return _session_response(session)
```

with

```python
            config_path=config_path,
            ask_clarifying_questions=payload.ask_clarifying_questions,
            settings=settings,
            hitl=settings.hitl,
        )
        return _session_response(session)

    @router.post(
        "/research/{session_id}/answers",
        status_code=202,
        response_model=ResearchSessionResponse,
    )
    async def answer_research(
        request: Request,
        payload: ClarificationAnswersRequest,
    ) -> ResearchSessionResponse:
        """Take the reader's answers to the one-time check, once (spec §4.4).

        ``404`` for an unknown session, ``409 not_waiting_for_input`` when it is
        not in ``needs_input`` (never asked, already answered, or past its
        deadline), ``422`` when an answer names an unknown question, repeats
        one, or picks a choice that was not offered. Questions left out take
        their best guess. The response is taken as the answers are accepted:
        its ``status`` still reads ``needs_input`` until the session's own task
        applies them, an instant later.
        """
        try:
            session = store.submit_answers(request.state.session_id, payload)
        except KeyError:
            raise ApiProblem(code="session_not_found", status_code=404) from None
        except NotWaitingForInput:
            raise ApiProblem(
                code="not_waiting_for_input", status_code=409
            ) from None
        except AnswerValidationError as error:
            raise RequestValidationError(error.errors()) from None
        return _session_response(session)
```

`src/deep_research/api/replay.py` — replace

```python
``query`` to the case's own question, so the session records what ran.
```

with

```python
``query`` to the case's own question, so the session records what ran. It
also reads ``X-Replay-Clarify`` on the same request: ``on`` makes the scripted
one-time check ask its fixed questions (``api/clarify.py``), and anything else
leaves every flow exactly as it was (live-briefs spec §4.4).
```

`src/deep_research/api/replay.py` — replace

```python
from collections.abc import Mapping
```

with

```python
from collections.abc import Mapping, Sequence
```

`src/deep_research/api/replay.py` — replace

```python
from starlette.types import ASGIApp, Receive, Scope, Send
```

with

```python
from starlette.types import ASGIApp, Receive, Scope, Send

from deep_research.api.clarify import REPLAY_CLARIFY_HEADER, requested_clarify
```

`src/deep_research/api/replay.py` — replace

```python
from deep_research.utils.types import ResearchEvent
```

with

```python
from deep_research.utils.types import ReaderAnswer, ResearchEvent
```

`src/deep_research/api/replay.py` — replace

```python
        event_handler: ProgressHandler | None,
    ) -> ResearchOutcome:
        # The case decides the question and the ceiling: its scripted completer
        # requires its own question, and its script is written for its ceiling.
```

with

```python
        event_handler: ProgressHandler | None,
        reader_answers: Sequence[ReaderAnswer] = (),
    ) -> ResearchOutcome:
        # The case decides the question and the ceiling: its scripted completer
        # requires its own question, and its script is written for its ceiling.
        # The reader's answers are the run's own and go to the graph unchanged.
```

`src/deep_research/api/replay.py` — replace

```python
                event_handler=paced,
            )
```

with

```python
                event_handler=paced,
                reader_answers=reader_answers,
            )
```

`src/deep_research/api/replay.py` — replace

```python
        requested_case.set(case_id)
```

with

```python
        requested_case.set(case_id)
        requested_clarify.set(headers.get(REPLAY_CLARIFY_HEADER, "").strip().lower() == "on")
```

- [ ] **Step 4: Run the API tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```

Expected: `177 passed` (observed in planning; `test_clarification.py` contributes `23`).

- [ ] **Step 5: The API's documentation (spec §4.9: README "Run the app", api-gaps' route list)**

`README.md` — replace

```markdown
The in-process API exposes one research start endpoint, a session list
endpoint, and five session-scoped read endpoints (`status`, `stream`,
`report`, `evidence`, `trace`), all served by a process-local `SessionStore`:
```

with

```markdown
The in-process API exposes one research start endpoint, a session list
endpoint, five session-scoped read endpoints (`status`, `stream`,
`report`, `evidence`, `trace`) and one session-scoped answers endpoint for the
one-time check, all served by a process-local `SessionStore`:
```

`README.md` — replace

```markdown
| `GET` | `/research/{session_id}/trace` | `200` `TraceResponse` |
```

with

```markdown
| `GET` | `/research/{session_id}/trace` | `200` `TraceResponse` |
| `POST` | `/research/{session_id}/answers` | `202` `ResearchSessionResponse` (the one-time check's answers, below) |
```

`README.md` — replace

```markdown
`incomplete`, or `failed`. Poll `status` or subscribe to the stream —
nothing blocks on research work.
```

with

````markdown
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
````

`README.md` — replace

```markdown
| `422` | Invalid request body or override shape; the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence` and `/trace` |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) or without an evidence log (`evidence_unavailable`, on `/evidence`) |
```

with

```markdown
| `422` | Invalid request body or override shape, or answers that do not fit the session's questions (an unknown or repeated `question_id`, a `choice` that was not offered); the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace` and `/answers` |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) or without an evidence log (`evidence_unavailable`, on `/evidence`); answers sent to a session that is not waiting for them (`not_waiting_for_input`: never asked, already answered, or past its deadline) |
```

`README.md` — replace

```markdown
`X-Replay-Case: <case id>` (the ids of `e2e_evaluation/replay_matrix.py`); the session
records the case's own question.
```

with

```markdown
`X-Replay-Case: <case id>` (the ids of `e2e_evaluation/replay_matrix.py`); the session
records the case's own question. The one-time check asks nothing in replay mode unless the
`POST /research` also carries `X-Replay-Clarify: on`; then it asks a fixed set of three
questions (Region, Period, For), so the check can be exercised offline.
```

`docs/design/api-gaps.md` — replace

```markdown
The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed) and a collapsible session sidebar, so gaps are
```

with

```markdown
The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed), a one-time check that can come between 2 and 3
(2a, live-briefs Phase 2) and a collapsible session sidebar, so gaps are
```

`docs/design/api-gaps.md` — replace

```markdown
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`:265-269`) |
```

with

```markdown
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`:265-269`) |
| `POST` | `/research/{id}/answers` | `202` `ResearchSessionResponse`: the reader's answers to the one-time check, taken once; `404` unknown session, `409` `not_waiting_for_input`, `422` an answer that does not fit its question (`api/app.py:265-294`, `api/sessions.py` `submit_answers`) |
```

`docs/design/api-gaps.md` — replace

```markdown
`status` is one of `running`, `completed`,
`max_iterations`, `incomplete`, `failed`.
```

with

```markdown
`status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
`max_iterations`, `incomplete`, `failed`.
```

`docs/design/api-gaps.md` — replace

```markdown
default → config), `output_format` and `config_overrides`; overrides are
```

with

```markdown
default → config), `output_format`, `config_overrides` and
`ask_clarifying_questions` (`bool`, default `true`; the one-time check, whose
timings are the `hitl` config section); overrides are
```

`docs/design/api-gaps.md` — replace

```markdown
burst-safe: the state after event *k* depends only on events 1..*k*.
```

with

```markdown
burst-safe: the state after event *k* depends only on events 1..*k*. The one-time
check's `session.clarification.requested` and `.answered` are published by the
session itself (`api/sessions.py`, `_clarify`), before the graph starts.
```

- [ ] **Step 6: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT
```

Expected: `4831 passed, 6 skipped, 12 deselected` (observed in planning: the baseline + 63).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/api/sessions.py src/deep_research/api/app.py src/deep_research/api/replay.py tests/test_api/conftest.py tests/test_api/test_app.py tests/test_api/test_clarification.py README.md docs/design/api-gaps.md
git commit -m "feat(api): needs_input, the answers route and the replay header for the one-time check"
```

---

### Task 6: The web client's model of the check (spec §4.5 RunState, chip, proxy header; §4.8 reload row)

**Files** (line numbers at `3565397`):
- Modify: `web/lib/api.ts:2` (`SessionStatus`), `:34` (answer types), `:110-113` (`submitAnswers` after `getEvidence`); `web/lib/format.ts:25`, `:29-30` (`STATUS`, `isLive`), `:63` (`statusNote`); `web/lib/run-state.ts:52` (types), `:72`, `:92` (the field), `:155` (shape guards), `:274-279` (two handlers); ``web/app/api/[...path]/route.ts:8``
- Create: `web/lib/clarify.ts`, `web/test/clarify.test.ts`
- Test: `web/test/run-state.test.ts:29`, `:36-38` (exact keys) and `(j)` appended; `web/test/format.test.ts:3`, `:37-39`, `:42`; `web/test/api.test.ts:3`, `:56`; `web/test/proxy.test.ts:74`; `web/test/components/status-chip.test.tsx:21-22`

**Interfaces:**
- Consumes: the API of Tasks 4–5 (the two events' metadata, the route, the header).
- Produces:
  - In `web/lib/api.ts`:
    - `SessionStatus` with `"needs_input"`;
    - ``type ClarificationAnswer = { question_id: string; choice: string } | { question_id: string; text: string }``;
    - `interface ClarificationAnswersRequest { answers: ClarificationAnswer[]; skip: boolean }`;
    - `submitAnswers(sessionId, body): Promise<ApiResult<ResearchSessionResponse>>`, one POST that is never retried.
  - In `web/lib/format.ts`:
    - `STATUS.needs_input = { label: "Waiting for you", dot: "dot-warn" }`;
    - `statusNote` returns `a few quick questions` for it;
    - `isLive(status: SessionStatus): boolean`, true for `running` and `needs_input`.
  - In `web/lib/run-state.ts`:
    - `interface ClarifyQuestion { id; dimension; text; short; options: string[]; bestGuess }`;
    - `interface ClarifyAnswer { questionId; value; source: "chosen" | "typed" | "best_guess" }`;
    - `interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }`;
    - `RunState.clarify: ClarifyState | null`, starting at `null`;
    - handlers for `session.clarification.requested` and `session.clarification.answered`, making nineteen handlers in all.
  - In `web/lib/clarify.ts`:
    - `type CheckPhase = "asking" | "starting"`;
    - ``type Pick = { choice: string } | { text: string }``;
    - `pipelineBegun(run)`;
    - `checkPhase(run, status): CheckPhase | null` (ambiguity 21);
    - `answersBody(questions, picks): ClarificationAnswer[]`;
    - `resolvedAnswers(questions, picks): ClarifyAnswer[]`;
    - `summaryText(questions, answers): string`;
    - `secondsLeft(deadlineAt, now): number`;
    - `countdownText(seconds): string`.
  - The proxy forwards `x-replay-clarify`.

- [ ] **Step 1: Write the failing tests**

The `(j)` block's burst-safety test also passes before the handlers exist, because unknown events change nothing. It stays as the guard that the handlers keep the core's rule: the state after event *k* depends only on events 1..*k*.

Create `web/test/clarify.test.ts`:

```ts
// @vitest-environment node
// The one-time check's derivations (live-briefs spec §4.4-§4.5): which face it shows, the answers
// as posted and as the run starts with them, the summary line and the countdown.
import { describe, expect, it } from "vitest";
import { answersBody, checkPhase, countdownText, pipelineBegun, resolvedAnswers, secondsLeft, summaryText } from "../lib/clarify";
import { applyEvent, newRunState, type RunState } from "../lib/run-state";

const WIRE = [
  { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
  { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  { id: "q3", dimension: "purpose", text: "What will you use it for?", short: "For", options: ["General understanding", "A project or investment decision", "Policy or regulation work"], best_guess: "General understanding" },
];
const DEADLINE = "2026-09-29T10:01:00.000Z";
function asked(): RunState {
  const run = newRunState(null);
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: WIRE, deadline_at: DEADLINE } });
  return run;
}
function answered(run: RunState): RunState {
  applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "answered", answers: [
    { question_id: "q1", value: "United States", source: "chosen" },
    { question_id: "q2", value: "since 2021", source: "typed" },
    { question_id: "q3", value: "General understanding", source: "best_guess" },
  ] } });
  return run;
}

describe("checkPhase: which face the check shows (live-briefs spec §4.5)", () => {
  it("a needs_input status asks before the stream has delivered the questions; a running one shows no check", () => {
    expect(checkPhase(newRunState(null), "needs_input")).toBe("asking");
    expect(checkPhase(newRunState(null), "running")).toBeNull();
  });
  it("once the stream has told the check it decides: asking, then starting, then nothing once the planner starts", () => {
    const run = asked();
    expect(checkPhase(run, "running")).toBe("asking");
    answered(run);
    expect(checkPhase(run, "needs_input")).toBe("starting");
    expect(pipelineBegun(run)).toBe(false);
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
    expect(pipelineBegun(run)).toBe(true);
    expect(checkPhase(run, "running")).toBeNull();
  });
  it("a session that has ended shows no check", () => {
    const run = asked();
    applyEvent(run, { type: "graph.session.completed", metadata: { status: "failed" } });
    expect(checkPhase(run, "running")).toBeNull();
  });
});

describe("the answers", () => {
  const questions = asked().clarify!.questions;
  it("the POST body has one entry per answered question, in question order", () => {
    expect(answersBody(questions, { q3: { choice: "General understanding" }, q1: { text: "Canada" } })).toEqual([
      { question_id: "q1", text: "Canada" },
      { question_id: "q3", choice: "General understanding" },
    ]);
    expect(answersBody(questions, {})).toEqual([]);
  });
  it("a question the reader left takes its best guess, as the API resolves it", () => {
    expect(resolvedAnswers(questions, { q2: { choice: "Any time" } })).toEqual([
      { questionId: "q1", value: "Global", source: "best_guess" },
      { questionId: "q2", value: "Any time", source: "chosen" },
      { questionId: "q3", value: "General understanding", source: "best_guess" },
    ]);
  });
  it("the summary says what the run starts with and who chose each value", () => {
    const run = answered(asked());
    expect(summaryText(run.clarify!.questions, run.clarify!.answered!.answers)).toBe(
      "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
    );
  });
});

describe("the countdown", () => {
  const at = Date.parse(DEADLINE);
  it("counts whole seconds to the deadline and stops at 0", () => {
    expect(secondsLeft(DEADLINE, at - 60_000)).toBe(60);
    expect(secondsLeft(DEADLINE, at - 41_500)).toBe(42);
    expect(secondsLeft(DEADLINE, at + 5_000)).toBe(0);
    expect(secondsLeft("not a time", at)).toBe(0);
  });
  it("reads m:ss, so a full minute is 1:00", () => {
    expect(countdownText(60)).toBe("Starts with best guesses in 1:00 if you don't answer");
    expect(countdownText(42)).toBe("Starts with best guesses in 0:42 if you don't answer");
    expect(countdownText(0)).toBe("Starts with best guesses in 0:00 if you don't answer");
  });
});
```

`web/test/run-state.test.ts` (anchor as written by Phase 1 Task 8) — replace

```ts
  it("has the seven rows and the seventeen handlers", () => {
```

with

```ts
  it("has the seven rows and the nineteen handlers", () => {
```

`web/test/run-state.test.ts` (anchor as written by Phase 1 Task 8) — replace

```ts
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "source_evaluator.evaluation.completed",
    ]);
```

with

```ts
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "source_evaluator.evaluation.completed",
    ]);
```

Append to `web/test/run-state.test.ts`:

```ts

describe("(j) the one-time check (live-briefs spec §4.4-§4.5)", () => {
  const questions = [
    { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
    { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  ];
  const requested = { type: "session.clarification.requested", metadata: { questions, deadline_at: "2026-09-29T10:01:00.000Z" } };
  const answered = { type: "session.clarification.answered", metadata: { reason: "skipped", answers: [
    { question_id: "q1", value: "Global", source: "chosen" }, { question_id: "q2", value: "Since 2023", source: "best_guess" },
  ] } };
  it("holds the questions and the deadline, then the answers and why; no row moves", () => {
    const run = newRunState(null);
    expect(run.clarify).toBeNull();
    applyEvent(run, requested);
    expect(run.clarify).toEqual({
      questions: [
        { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], bestGuess: "Global" },
        { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], bestGuess: "Since 2023" },
      ],
      deadlineAt: "2026-09-29T10:01:00.000Z",
      answered: null,
    });
    applyEvent(run, answered);
    expect(run.clarify!.answered).toEqual({ reason: "skipped", answers: [
      { questionId: "q1", value: "Global", source: "chosen" }, { questionId: "q2", value: "Since 2023", source: "best_guess" },
    ] });
    expect(run.clarify!.questions).toHaveLength(2);
    expect(run.active).toBe("planner");
    expect(run.marks).toEqual({});
  });
  it("drops a malformed question or answer rather than inventing one", () => {
    const run = newRunState(null);
    applyEvent(run, { type: "session.clarification.requested", metadata: { questions: [questions[0], { id: "q2", text: "no options" }, null], deadline_at: 5 } });
    expect(run.clarify!.questions.map((q) => q.id)).toEqual(["q1"]);
    expect(run.clarify!.deadlineAt).toBe("");
    applyEvent(run, { type: "session.clarification.answered", metadata: { answers: [{ question_id: "q1", value: "Global", source: "guessed" }] } });
    expect(run.clarify!.answered).toEqual({ answers: [], reason: "" });
  });
  it("is burst-safe: a late subscriber paints the same check", () => {
    const events: ResearchEvent[] = [requested, answered, { type: "graph.session.started", metadata: { max_extra_passes: 1 } }, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } }]
      .map((e, i) => ({ event_type: e.type, source: "api", message: "m", timestamp: "2026-09-29T10:00:00+00:00", metadata: e.metadata, event_id: `e${i}` }));
    const snaps = snapshots(events, 2);
    events.forEach((_, k) => expect(replayRun(events.slice(0, k + 1), 2)).toEqual(snaps[k]));
  });
});
```

`web/test/format.test.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
import { fmtClock, fmtScore, fmtSeconds, meterClass, notFoundClause, passFact, qFitClass, statusNote, toSessionView } from "../lib/format";
```

with

```ts
import { STATUS, fmtClock, fmtScore, fmtSeconds, isLive, meterClass, notFoundClause, passFact, qFitClass, statusNote, toSessionView } from "../lib/format";
```

`web/test/format.test.ts` — replace

```ts
  it("failed → halted", () => {
    expect(statusNote(toSessionView({ ...base, status: "failed" }))).toBe("halted");
  });
```

with

```ts
  it("failed → halted", () => {
    expect(statusNote(toSessionView({ ...base, status: "failed" }))).toBe("halted");
  });
  it("needs_input → Waiting for you · a few quick questions, on the warn dot (live-briefs spec §4.5)", () => {
    expect(statusNote(toSessionView({ ...base, status: "needs_input" }, "Planning"))).toBe("a few quick questions");
    expect(STATUS.needs_input).toEqual({ label: "Waiting for you", dot: "dot-warn" });
  });
```

`web/test/format.test.ts` — replace

```ts
describe("helpers", () => {
```

with

```ts
describe("helpers", () => {
  it("isLive: a running session and one waiting for the reader are in progress; every other status is not", () => {
    expect((["running", "needs_input", "completed", "max_iterations", "incomplete", "failed"] as const).map(isLive)).toEqual([true, true, false, false, false, false]);
  });
```

`web/test/api.test.ts` — replace

```ts
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, streamUrl } from "../lib/api";
```

with

```ts
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, streamUrl, submitAnswers } from "../lib/api";
```

`web/test/api.test.ts` — replace

```ts
  it("builds same-origin URLs", () => {
```

with

```ts
  it("posts the check's answers once to the session's answers route (live-briefs spec §4.4)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(202, { session_id: "s1", status: "needs_input" }, { "x-deep-research-mode": "replay" }));
    vi.stubGlobal("fetch", fetchMock);
    const body = { answers: [{ question_id: "q1", choice: "Global" }, { question_id: "q2", text: "since 2021" }], skip: false };
    const result = await submitAnswers("s1", body);
    expect(result.data.status).toBe("needs_input");
    expect(result.mode).toBe("replay");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/research/s1/answers");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual(body);
  });
  it("maps a late answer's 409 to ApiError and never retries it", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(409, { error: { code: "not_waiting_for_input", message: "Research session is not waiting for answers.", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const error = await submitAnswers("s1", { answers: [], skip: true }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.body.code]).toEqual([409, "not_waiting_for_input"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("builds same-origin URLs", () => {
```

`web/test/proxy.test.ts` — replace

```ts
  it("answers 502 api_unreachable with the target when the connection is refused", async () => {
```

with

```ts
  it("forwards x-replay-clarify, so replay's scripted check can be asked for (live-briefs spec §4.4)", async () => {
    const seen: { headers?: IncomingMessage["headers"] } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      seen.headers = req.headers;
      res.writeHead(202, { "content-type": "application/json" });
      res.end("{}");
    });
    const request = new NextRequest("http://localhost:3000/api/research", {
      method: "POST", body: "{}", headers: { "content-type": "application/json", "x-replay-clarify": "on" },
    });
    expect((await POST(request, ctx("research"))).status).toBe(202);
    expect(seen.headers!["x-replay-clarify"]).toBe("on");
  });
  it("answers 502 api_unreachable with the target when the connection is refused", async () => {
```

`web/test/components/status-chip.test.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
    rerender(<StatusChip view={view({ status: "failed" })} />);
    expect(text(container)).toBe("Failed · halted");
```

with

```tsx
    rerender(<StatusChip view={view({ status: "failed" })} />);
    expect(text(container)).toBe("Failed · halted");
    rerender(<StatusChip view={view({ status: "needs_input", step: null })} />);
    expect(text(container)).toBe("Waiting for you · a few quick questions");
    expect(container.querySelector(".dot")!.className).toContain("dot-warn");
```

- [ ] **Step 2: Run them to see them fail**

```bash
cd web && npx vitest run test/clarify.test.ts test/run-state.test.ts test/format.test.ts test/api.test.ts test/proxy.test.ts test/components/status-chip.test.tsx 2>&1 | grep -E "Test Files|Tests  |Cannot find module"
```

Expected: `Error: Cannot find module '../lib/clarify'`, `Test Files  6 failed (6)`, `Tests  9 failed | 52 passed (61)` (observed in planning). `clarify.test.ts` cannot load, so its 8 tests are not counted yet.

- [ ] **Step 3: Add the status, the client call, the run state, the derivations and the header**

`web/lib/api.ts` — replace

```ts
export type SessionStatus = "running" | "completed" | "max_iterations" | "incomplete" | "failed";
```

with

```ts
/* "needs_input": the one-time check is waiting for the reader (live-briefs spec §4.4); not terminal. */
export type SessionStatus = "running" | "needs_input" | "completed" | "max_iterations" | "incomplete" | "failed";
```

`web/lib/api.ts` — replace

```ts
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
```

with

```ts
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
/* POST /research/{id}/answers (api/models.py ClarificationAnswersRequest): each answer carries
   exactly one of an offered choice or the reader's own text (at most 200 characters). */
export type ClarificationAnswer = { question_id: string; choice: string } | { question_id: string; text: string };
export interface ClarificationAnswersRequest { answers: ClarificationAnswer[]; skip: boolean }
```

`web/lib/api.ts` — replace

```ts
export async function getEvidence(sessionId: string): Promise<ApiResult<EvidenceResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/evidence`);
  return { data: (await r.json()) as EvidenceResponse, mode: modeOf(r) };
}
```

with

```ts
export async function getEvidence(sessionId: string): Promise<ApiResult<EvidenceResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/evidence`);
  return { data: (await r.json()) as EvidenceResponse, mode: modeOf(r) };
}
/* The one-time check's answers, posted once (live-briefs spec §4.5): a 409 not_waiting_for_input
   means the check already started on best guesses. */
export async function submitAnswers(sessionId: string, body: ClarificationAnswersRequest): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/answers`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
```

`web/lib/format.ts` — replace

```ts
  running: { label: "Running", dot: "dot-live" },
```

with

```ts
  running: { label: "Running", dot: "dot-live" },
  needs_input: { label: "Waiting for you", dot: "dot-warn" },
```

`web/lib/format.ts` — replace

```ts
  failed: { label: "Failed", dot: "dot-danger" },
};
```

with

```ts
  failed: { label: "Failed", dot: "dot-danger" },
};
/* A session still in progress: running, or waiting for the reader's answers to the one-time check
   (live-briefs spec §4.4: needs_input is not terminal). */
export function isLive(status: SessionStatus): boolean {
  return status === "running" || status === "needs_input";
}
```

`web/lib/format.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
    case "failed": return "halted";
```

with

```ts
    case "needs_input": return "a few quick questions";
    case "failed": return "halted";
```

`web/lib/run-state.ts` (anchor as written by Phase 1 Task 8) — replace

```ts
export interface ReopenLine { kind: "extra_pass" | "redraft"; text: string }
```

with

```ts
export interface ReopenLine { kind: "extra_pass" | "redraft"; text: string }
/* The one-time check (live-briefs spec §4.4-§4.5): its questions and deadline from
   session.clarification.requested, then the answers the run starts with from .answered. */
export interface ClarifyQuestion { id: string; dimension: string; text: string; short: string; options: string[]; bestGuess: string }
export interface ClarifyAnswer { questionId: string; value: string; source: "chosen" | "typed" | "best_guess" }
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }
```

`web/lib/run-state.ts` (anchor as written by Phase 1 Task 8) — replace

```ts
  open: Set<NodeId>;                      /* done rows the reader reopened — reader state, not derived from events */
```

with

```ts
  open: Set<NodeId>;                      /* done rows the reader reopened — reader state, not derived from events */
  clarify: ClarifyState | null;           /* the one-time check; null while the stream has told none */
```

`web/lib/run-state.ts` (anchor as written by Phase 1 Task 8) — replace

```ts
    reopen: {}, outcomes: {}, open: new Set(),
```

with

```ts
    reopen: {}, outcomes: {}, open: new Set(), clarify: null,
```

`web/lib/run-state.ts` — replace

```ts
/* Keyed by event type; each handler reads only `md` (the event's metadata). */
```

with

```ts
/* The check's wire shapes (api/clarify.py): an entry that does not fit is dropped, never invented. */
const isText = (v: unknown): v is string => typeof v === "string" && v.length > 0;
function isClarifyQuestion(q: unknown): q is { id: string; dimension: string; text: string; short: string; options: string[]; best_guess: string } {
  const m = q as Md | null;
  return !!m && isText(m.id) && isText(m.dimension) && isText(m.text) && isText(m.short)
    && Array.isArray(m.options) && m.options.every(isText) && isText(m.best_guess);
}
function isClarifyAnswer(a: unknown): a is { question_id: string; value: string; source: ClarifyAnswer["source"] } {
  const m = a as Md | null;
  return !!m && isText(m.question_id) && typeof m.value === "string" && ["chosen", "typed", "best_guess"].includes(m.source);
}

/* Keyed by event type; each handler reads only `md` (the event's metadata). */
```

`web/lib/run-state.ts` — replace

```ts
  "graph.session.completed": (run, md) => {
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null; run.tag = null;
    run.active = null;
  },
};
```

with

```ts
  "graph.session.completed": (run, md) => {
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null; run.tag = null;
    run.active = null;
  },
  /* live-briefs spec §4.4: the session asks before the graph starts; neither event moves a row. */
  "session.clarification.requested": (run, md) => {
    const listed: unknown[] = Array.isArray(md.questions) ? md.questions : [];
    run.clarify = {
      questions: listed.filter(isClarifyQuestion).map((q) => ({ id: q.id, dimension: q.dimension, text: q.text, short: q.short, options: [...q.options], bestGuess: q.best_guess })),
      deadlineAt: typeof md.deadline_at === "string" ? md.deadline_at : "",
      answered: null,
    };
  },
  "session.clarification.answered": (run, md) => {
    const listed: unknown[] = Array.isArray(md.answers) ? md.answers : [];
    const answers = listed.filter(isClarifyAnswer).map((a) => ({ questionId: a.question_id, value: a.value, source: a.source }));
    run.clarify = { questions: run.clarify?.questions ?? [], deadlineAt: run.clarify?.deadlineAt ?? "", answered: { answers, reason: typeof md.reason === "string" ? md.reason : "" } };
  },
};
```

Create `web/lib/clarify.ts`:

```ts
// The one-time check's derivations (live-briefs spec §4.4-§4.5): which face the check shows, the
// answers as the API takes them and as the run starts with them, the summary line and the
// countdown. Pure — a function of the stream's RunState and the reader's picks only.
import type { ClarificationAnswer, SessionStatus } from "./api";
import type { ClarifyAnswer, ClarifyQuestion, RunState } from "./run-state";

export type CheckPhase = "asking" | "starting";
/* What the reader picked for one question: an offered option, or their own words. */
export type Pick = { choice: string } | { text: string };

/* The pipeline has begun once any node has started or finished, or the session has ended. */
export function pipelineBegun(run: RunState): boolean {
  return run.openNode !== null || Object.keys(run.marks).length > 0 || run.finalStatus !== null;
}
/* "asking" while the check waits for the reader, "starting" from the answers until the planner
   starts, null otherwise. Once the stream has told the check, the stream decides; before that, a
   needs_input status alone reads as "asking" (the card then waits for the stream's questions). */
export function checkPhase(run: RunState, status: SessionStatus): CheckPhase | null {
  if (pipelineBegun(run)) return null;
  if (run.clarify) return run.clarify.answered ? "starting" : "asking";
  return status === "needs_input" ? "asking" : null;
}
/* The POST /answers entries: one per question the reader answered, in question order. */
export function answersBody(questions: readonly ClarifyQuestion[], picks: Readonly<Record<string, Pick>>): ClarificationAnswer[] {
  return questions.filter((q) => picks[q.id]).map((q) => ({ question_id: q.id, ...picks[q.id] }));
}
/* The answers the run starts with, resolved as the API resolves them: an unanswered question takes
   its best guess. */
export function resolvedAnswers(questions: readonly ClarifyQuestion[], picks: Readonly<Record<string, Pick>>): ClarifyAnswer[] {
  return questions.map((q): ClarifyAnswer => {
    const pick = picks[q.id];
    if (!pick) return { questionId: q.id, value: q.bestGuess, source: "best_guess" };
    return "choice" in pick ? { questionId: q.id, value: pick.choice, source: "chosen" } : { questionId: q.id, value: pick.text, source: "typed" };
  });
}
/* "Starting research with: Region: United States (you said) · Period: Since 2023 (best guess) · …" */
export function summaryText(questions: readonly ClarifyQuestion[], answers: readonly ClarifyAnswer[]): string {
  return "Starting research with: " + questions.map((q) => {
    const answer = answers.find((a) => a.questionId === q.id);
    const said = answer && answer.source !== "best_guess" ? "you said" : "best guess";
    return `${q.short}: ${answer ? answer.value : q.bestGuess} (${said})`;
  }).join(" · ");
}
/* Whole seconds until the check starts on best guesses; 0 once passed, or for an unreadable time. */
export function secondsLeft(deadlineAt: string, now: number): number {
  const at = Date.parse(deadlineAt);
  return Number.isFinite(at) ? Math.max(0, Math.ceil((at - now) / 1000)) : 0;
}
/* The footer cap, m:ss so that a full minute reads 1:00. */
export function countdownText(seconds: number): string {
  return `Starts with best guesses in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} if you don't answer`;
}
```

`web/app/api/[...path]/route.ts` — replace

```ts
const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case"] as const;
```

with

```ts
// x-replay-clarify: replay's scripted one-time check asks only when the POST carried it (live-briefs spec §4.4).
const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case", "x-replay-clarify"] as const;
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
cd web && npm run -s typecheck && npx vitest run test/clarify.test.ts test/run-state.test.ts test/format.test.ts test/api.test.ts test/proxy.test.ts test/components/status-chip.test.tsx 2>&1 | grep -E "Test Files|Tests  " && npx vitest run 2>&1 | grep -E "Test Files|Tests  "
```

Expected: no `typecheck` output; `Test Files  6 passed (6)`, `Tests  69 passed (69)`; then the whole suite, `Test Files  24 passed (24)`, `Tests  171 passed (171)` (observed in planning: the baseline + 16).

- [ ] **Step 5: Commit**

```bash
git add web/lib/api.ts web/lib/format.ts web/lib/run-state.ts web/lib/clarify.ts "web/app/api/[...path]/route.ts" web/test/clarify.test.ts web/test/run-state.test.ts web/test/format.test.ts web/test/api.test.ts web/test/proxy.test.ts web/test/components/status-chip.test.tsx
git commit -m "feat(web): needs_input, the check's run state and derivations, and the replay header through the proxy"
```

---

### Task 7: The settings row "Ask me when the question is unclear" (spec §4.5 Settings row; D16; AC13)

**Files:**
- Modify (every anchor as written by Phase 1 Task 7):
  - `web/lib/api.ts:5-11` (`ResearchRequest`);
  - `web/lib/session-store.ts:4` (`SubmittedSettings`);
  - `web/components/Composer.tsx:21-24`, `:28` (`DEFAULT_SETTINGS`, `buildRequest`);
  - `web/components/SettingsPopover.tsx:77-78` (the new row after Output directory, in the slot the extra-passes stepper left).
- Test:
  - `web/test/components/composer.test.tsx:13-17` (the body) and `:27-28` (a new test after the no-extra-passes test);
  - `web/test/api.test.ts:7` (the request literal).
- E2E:
  - `web/e2e/settings.spec.ts:3`, `:17`, and one test appended.

**Interfaces:**
- Consumes: Task 6's `web/lib/api.ts`. Task 5's API takes the flag and the header, and Task 6's proxy forwards the header.
- Produces:
  - `ResearchRequest.ask_clarifying_questions: boolean`, required on the client, since the composer always sends it;
  - `SubmittedSettings.askWhenUnclear: boolean`;
  - `DEFAULT_SETTINGS.askWhenUnclear === true`;
  - `buildRequest` returns `{ query, output_format, config_overrides, ask_clarifying_questions }`;
  - the popover row: `span.lbl#lblAsk` and `div.seg#segAsk[role=group]`, holding two `button[data-ask="on"|"off"][aria-pressed]` labelled `On` and `Off`.

- [ ] **Step 1: Write the failing tests**

The new composer test answers the POST with a 422, so it can read the posted body without starting the idle→running flight, which needs layout that jsdom does not have.

`web/test/components/composer.test.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
    const body = buildRequest("  q  ", DEFAULT_SETTINGS);
    expect(body).toEqual({
      query: "q", output_format: "markdown",
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    });
```

with

```tsx
    const body = buildRequest("  q  ", DEFAULT_SETTINGS);
    expect(body).toEqual({
      query: "q", output_format: "markdown",
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
      ask_clarifying_questions: true,
    });
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, askWhenUnclear: false }).ask_clarifying_questions).toBe(false);
```

`web/test/components/composer.test.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
    expect(document.querySelector("#settingsPop")!.textContent).not.toMatch(/extra pass/i);
  });
```

with

```tsx
    expect(document.querySelector("#settingsPop")!.textContent).not.toMatch(/extra pass/i);
  });
  it("offers 'Ask me when the question is unclear' as On/Off, on by default, and sends the choice (live-briefs spec D16)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? json(422, { error: { code: "validation_error", message: "Request validation failed.", reason: null, issues: [] } }) : json(200, { sessions: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Run settings" }));
    expect(document.getElementById("lblAsk")!.textContent).toBe("Ask me when the question is unclear");
    const seg = () => [...document.querySelectorAll("#segAsk button")];
    expect(seg().map((b) => [b.textContent, b.getAttribute("data-ask"), b.getAttribute("aria-pressed")])).toEqual([["On", "on", "true"], ["Off", "off", "false"]]);
    fireEvent.click(seg()[1]);
    expect(seg().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
    fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "q" } });
    fireEvent.submit(screen.getByLabelText("Research question").closest("form")!);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(init!.body as string).ask_clarifying_questions).toBe(false);
  });
```

`web/test/api.test.ts` — replace

```ts
const request = { query: "q", output_format: "markdown" as const, config_overrides: {} };
```

with

```ts
const request = { query: "q", output_format: "markdown" as const, config_overrides: {}, ask_clarifying_questions: true };
```

- [ ] **Step 2: Run them to see them fail**

```bash
cd web && npx vitest run test/components/composer.test.tsx test/api.test.ts 2>&1 | grep -E "Test Files|Tests  |×"
```

Expected: `Test Files  1 failed | 1 passed (2)`, `Tests  2 failed | 15 passed (17)`. The failures are `builds the request the design specifies, with no max_iterations (live-briefs spec §4.2, AC8)` (the body has no `ask_clarifying_questions`) and `offers 'Ask me when the question is unclear' as On/Off, on by default, and sends the choice (live-briefs spec D16)` (no `#lblAsk`).

- [ ] **Step 3: Add the setting, the request field and the row**

`web/lib/api.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
/* No `max_iterations`: the console never sends one, so the API uses the configured extra-pass
   budget (live-briefs spec §4.2, D15). The API itself still accepts the field. */
export interface ResearchRequest {
  query: string;
  output_format: "markdown";
  config_overrides: Record<string, unknown>;
}
```

with

```ts
/* No `max_iterations`: the console never sends one, so the API uses the configured extra-pass
   budget (live-briefs spec §4.2, D15). The API itself still accepts the field.
   `ask_clarifying_questions` is the settings row "Ask me when the question is unclear" (D16). */
export interface ResearchRequest {
  query: string;
  output_format: "markdown";
  config_overrides: Record<string, unknown>;
  ask_clarifying_questions: boolean;
}
```

`web/lib/session-store.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; outputDir: string }
```

with

```ts
// askWhenUnclear: the one-time check's setting (live-briefs spec D16). Only the request reads it; a
// Submission stored before it existed has none, and nothing downstream of the POST needs it.
export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; outputDir: string; askWhenUnclear: boolean }
```

`web/components/Composer.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", outputDir: "output/" };

/* The body the design specifies, with no max_iterations: the API applies the configured
   extra-pass budget (live-briefs spec §4.2, D15). */
```

with

```tsx
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", outputDir: "output/", askWhenUnclear: true };

/* The body the design specifies, with no max_iterations: the API applies the configured
   extra-pass budget (live-briefs spec §4.2, D15). The one-time check is asked for unless the
   reader turned it off (D16). */
```

`web/components/Composer.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
  return { query: question.trim(), output_format: "markdown", config_overrides };
```

with

```tsx
  return { query: question.trim(), output_format: "markdown", config_overrides, ask_clarifying_questions: s.askWhenUnclear };
```

`web/components/SettingsPopover.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
        <input className="input mono-in" id="outputDir" value={settings.outputDir} onChange={(e) => onChange({ ...settings, outputDir: e.target.value })} />
      </div>
```

with

```tsx
        <input className="input mono-in" id="outputDir" value={settings.outputDir} onChange={(e) => onChange({ ...settings, outputDir: e.target.value })} />
      </div>
      {/* live-briefs spec §4.5 (D16): in the slot the extra-passes stepper left; on by default. */}
      <div className="pop-row">
        <span className="lbl" id="lblAsk">Ask me when the question is unclear</span>
        <div className="seg" role="group" aria-labelledby="lblAsk" id="segAsk">
          {([["on", true], ["off", false]] as const).map(([key, on]) => <button key={key} type="button" data-ask={key} {...seg(settings.askWhenUnclear === on)} onClick={() => onChange({ ...settings, askWhenUnclear: on })}>{on ? "On" : "Off"}</button>)}
        </div>
      </div>
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
cd web && npm run -s typecheck && npx vitest run test/components/composer.test.tsx test/api.test.ts 2>&1 | grep -E "Test Files|Tests  " && npx vitest run 2>&1 | grep -E "Test Files|Tests  "
```

Expected: no `typecheck` output; `Test Files  2 passed (2)`, `Tests  17 passed (17)`; then `Test Files  24 passed (24)`, `Tests  172 passed (172)`.

- [ ] **Step 5: The e2e (verification)**

Check with the setting off and `X-Replay-Clarify: on`. Replay's checker would ask whenever it is called, so a missing card proves that no call was made (AC13; pytest `test_with_the_setting_off_no_check_call_is_made` proves the same at the store).

`web/e2e/settings.spec.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
import { expect, test } from "@playwright/test";
```

with

```ts
import { expect, test } from "@playwright/test";
import { API, waitTerminal } from "./support";
```

`web/e2e/settings.spec.ts` (anchor as written by Phase 1 Task 7) — replace

```ts
  expect(Object.keys(body).sort()).toEqual(["config_overrides", "output_format", "query"]);
```

with

```ts
  expect(Object.keys(body).sort()).toEqual(["ask_clarifying_questions", "config_overrides", "output_format", "query"]);
  expect(body.ask_clarifying_questions).toBe(true);
```

Append to `web/e2e/settings.spec.ts`:

```ts

// live-briefs spec D16, AC13: with the setting off the session makes no check at all — even with
// X-Replay-Clarify: on, which makes replay's scripted checker ask whenever it is called.
test("with 'Ask me when the question is unclear' off, no check is made (AC13)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  await page.goto("/");
  await page.locator("#plusBtn").click();
  await expect(page.locator('#segAsk button[data-ask="on"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator('#segAsk button[data-ask="off"]').click();
  await expect(page.locator('#segAsk button[data-ask="off"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator("#popClose").click();
  await page.getByLabel("Research question").fill("q");
  const posted = page.waitForRequest((r) => r.method() === "POST" && /\/api\/research$/.test(r.url()));
  await page.getByRole("button", { name: "Start research" }).click();
  expect(((await posted).postDataJSON() as Record<string, unknown>).ask_clarifying_questions).toBe(false);
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  const id = page.url().split("/").pop()!;
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#stage-clarify")).toHaveCount(0);
  await waitTerminal(request, id);
  expect(await (await request.get(`${API}/research/${id}/stream`)).text()).not.toContain("session.clarification");
});
```

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s build && npx playwright test e2e/settings.spec.ts --project=chromium
```

Expected: `2 passed` (observed in planning).

- [ ] **Step 6: Commit**

```bash
git add web/lib/api.ts web/lib/session-store.ts web/components/Composer.tsx web/components/SettingsPopover.tsx web/test/components/composer.test.tsx web/test/api.test.ts web/e2e/settings.spec.ts
git commit -m "feat(web): the 'Ask me when the question is unclear' setting, sent as ask_clarifying_questions"
```

---

### Task 8: The check stage (spec §4.5 Card, Submit; D5–D7, D17; §4.8 409 row)

**Files:**
- Create: `web/components/ClarifyStage.tsx`, `web/test/components/clarify-stage.test.tsx`
- Modify: `web/app/globals.css`. Append at the end of the file, after Phase 1's own additions; lines 1–1131 stay verbatim.

**Interfaces:**
- Consumes: Task 6's `submitAnswers`, `ApiError`, `RunState.clarify`, `ClarifyQuestion`, `ClarifyState`, `answersBody`, `resolvedAnswers`, `summaryText`, `secondsLeft`, `countdownText`, `CheckPhase` and `Pick`; `qFitClass` (`web/lib/format.ts`).
- Produces: ``ClarifyStage({ sessionId: string; run: RunState; phase: CheckPhase; question: string; strip: ReactNode })`` and `CHOICE_ADVANCE_MS = 240`. The DOM, which Task 9's e2e reads:
  - The stage: `section#stage-clarify.stage.is-on.no-enter.is-arriving`. Inside `.run-wrap`, an `.ask-head` holds the eyebrow, `h1#clarify-h.ask-q.ask-locked[aria-describedby=runningOpts]` and the strip.
  - The card: `div.card#clarifyCard[data-face="asking"|"summary"|"late"|"failed"]`. It is rendered only once the stream has delivered questions.
  - Asking face, top: `span.cap#clarifyStep`; `.step-dots > i[data-on]`; `h3.card-title#clarifyQ[tabindex=-1]` (focused after the reader moves).
  - Asking face, answers: `.choices[role=group] > button.choice[aria-pressed]`, where the best guess carries `span.cap` `best guess`, and `button.choice#clarifyOtherBtn` comes last.
  - Asking face, Other…: `.ck-other#clarifyOtherRow`, holding `input.tx#clarifyOther` and a `Next` button.
  - Asking face, footer: `.ic-foot`, holding `Back` · `Skip this one` · `Just start`, then `p.cap#clarifyCountdown`.
  - Summary face: `p.sm.ck-summary#clarifySummary`.

- [ ] **Step 1: Write the failing test**

Create `web/test/components/clarify-stage.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CHOICE_ADVANCE_MS, ClarifyStage } from "../../components/ClarifyStage";
import { applyEvent, newRunState, type RunState } from "../../lib/run-state";

const QUESTIONS = [
  { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
  { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  { id: "q3", dimension: "purpose", text: "What will you use it for?", short: "For", options: ["General understanding", "A project or investment decision", "Policy or regulation work"], best_guess: "General understanding" },
];
const NOW = Date.parse("2026-09-29T10:00:00.000Z");
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function asking(): RunState {
  const run = newRunState(null);
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: QUESTIONS, deadline_at: "2026-09-29T10:01:00.000Z" } });
  return run;
}
function show(run: RunState = asking(), phase: "asking" | "starting" = "asking") {
  return render(<ClarifyStage sessionId="s1" run={run} phase={phase} question="What limits grid-scale battery storage?" strip={null} />);
}
const card = () => document.getElementById("clarifyCard")!;
const option = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
const tap = (name: string) => { fireEvent.click(option(name)); act(() => { vi.advanceTimersByTime(CHOICE_ADVANCE_MS); }); };

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("ClarifyStage — the card (live-briefs spec §4.5, pick 4B)", () => {
  it("asks question 1 of 3 with its step dots, the best guess marked, Other… last, and no purple", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    expect(document.getElementById("stage-clarify")!.className).toBe("stage is-on no-enter is-arriving");
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    expect([...card().querySelectorAll(".step-dots i")].map((i) => i.getAttribute("data-on"))).toEqual(["1", "0", "0"]);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Which region should this cover?");
    const answers = [...card().querySelectorAll(".choices .choice")];
    expect(answers.map((b) => b.textContent)).toEqual(["United States", "European Union", "Globalbest guess", "Other…"]);
    expect(answers.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false", "false", "false"]);
    expect(card().querySelector(".choice .cap")!.textContent).toBe("best guess");
    expect(option("Back")).toHaveProperty("disabled", true);
    expect(document.getElementById("clarifyCountdown")!.textContent).toBe("Starts with best guesses in 1:00 if you don't answer");
    expect(card().querySelector(".btn-primary")).toBeNull();
  });

  it("counts down from the deadline, once a second", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    act(() => { vi.advanceTimersByTime(18_000); });
    expect(document.getElementById("clarifyCountdown")!.textContent).toBe("Starts with best guesses in 0:42 if you don't answer");
  });

  it("marks a tapped answer and moves on after 240 ms; Back returns with it pressed", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    fireEvent.click(option("European Union"));
    expect(option("European Union").getAttribute("aria-pressed")).toBe("true");
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    act(() => { vi.advanceTimersByTime(CHOICE_ADVANCE_MS); });
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
    expect([...card().querySelectorAll(".step-dots i")].map((i) => i.getAttribute("data-on"))).toEqual(["0", "1", "0"]);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 3 }));
    fireEvent.click(option("Back"));
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    expect(option("European Union").getAttribute("aria-pressed")).toBe("true");
  });

  it("opens Other… on a text field; Next waits for text; Enter moves on", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    fireEvent.click(option("Other…"));
    const field = screen.getByLabelText("Your own answer") as HTMLInputElement;
    expect(field.className).toBe("tx");
    expect(document.activeElement).toBe(field);
    expect(option("Other…").getAttribute("aria-expanded")).toBe("true");
    expect(option("Next")).toHaveProperty("disabled", true);
    fireEvent.change(field, { target: { value: "  Canada " } });
    expect(option("Next")).toHaveProperty("disabled", false);
    fireEvent.keyDown(field, { key: "Enter" });
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
  });

  it("posts the answers once after the last question and shows the summary", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    tap("United States");
    fireEvent.click(option("Other…"));
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "since 2021" } });
    fireEvent.click(option("Next"));
    await act(async () => { fireEvent.click(option("Skip this one")); });
    expect(posts(fetchMock)).toHaveLength(1);
    const [url, init] = posts(fetchMock)[0] as [string, RequestInit];
    expect(url).toBe("/api/research/s1/answers");
    expect(JSON.parse(init.body as string)).toEqual({ answers: [{ question_id: "q1", choice: "United States" }, { question_id: "q2", text: "since 2021" }], skip: false });
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
    );
    expect(card().getAttribute("data-face")).toBe("summary");
  });

  it("Just start posts what was answered so far with skip, once", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    tap("Global");
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(posts(fetchMock)).toHaveLength(1);
    expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({ answers: [{ question_id: "q1", choice: "Global" }], skip: true });
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: Global (you said) · Period: Since 2023 (best guess) · For: General understanding (best guess)",
    );
  });

  it("an answer the API refuses as late reads 'Already started with best guesses'", async () => {
    vi.useFakeTimers({ now: NOW });
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: { code: "not_waiting_for_input", message: "Research session is not waiting for answers.", reason: null, issues: [] } })));
    show();
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(card().textContent).toBe("Already started with best guesses");
  });

  it("any other failure says the answers were not sent, and never posts again", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(card().textContent).toBe("Your answers could not be sent; the run starts with best guesses.");
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it("once the stream says the check was answered, it shows what the run starts with", () => {
    const run = asking();
    applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "timed_out", answers: QUESTIONS.map((q) => ({ question_id: q.id, value: q.best_guess, source: "best_guess" })) } });
    show(run, "starting");
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: Global (best guess) · Period: Since 2023 (best guess) · For: General understanding (best guess)",
    );
  });

  it("holds the header alone until the stream delivers the questions", () => {
    show(newRunState(null));
    expect(document.getElementById("clarify-h")!.textContent).toBe("What limits grid-scale battery storage?");
    expect(document.getElementById("clarifyCard")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to see it fail**

```bash
cd web && npx vitest run test/components/clarify-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  |Failed to resolve"
```

Expected: `Error: Failed to resolve import "../../components/ClarifyStage" from "test/components/clarify-stage.test.tsx". Does the file exist?`, `Test Files  1 failed (1)`, `Tests  no tests`.

- [ ] **Step 3: Write the stage and its styles**

The stage behaves as follows:
- It holds the header still and changes only the card below it.
- A tapped answer shows as chosen for 240 ms, then the next question arrives.
- Answers are sent exactly once (`posted`), never retried. A 409 reads as late; anything else reads as not sent (ambiguities 14 and 17).
- The countdown re-reads the clock once a second, from the stream's `deadline_at`.
- Once the stream says the check was answered, the summary reads the stream's answers. Before that, it reads the reader's local picks.

The stylesheet ports the pick's `.choices`, `.choice`, `.step-dots`, `.ic-foot` and `.tx` (`docs/design/running-stage-picks/theme.css:186-188`, `:269-285`), using tokens only. Its answers stack at full width at every size, and its text field lines up with the answers.

Create `web/components/ClarifyStage.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, submitAnswers } from "@/lib/api";
import { answersBody, countdownText, resolvedAnswers, secondsLeft, summaryText, type CheckPhase, type Pick } from "@/lib/clarify";
import { qFitClass } from "@/lib/format";
import type { ClarifyQuestion, ClarifyState, RunState } from "@/lib/run-state";

/* A tapped answer shows as chosen for this long before the next question (pick 4B). */
export const CHOICE_ADVANCE_MS = 240;
type Sent = "idle" | "sending" | "sent" | "late" | "failed";

/* live-briefs spec §4.5 (pick 4B; D5-D7, D17): the one-time check takes the pipeline card's place on
   page 2, one question at a time. The header is the running stage's own — the locked question and
   the settings strip — so when the planner starts only the card below it changes. The card arrives
   once the stream has delivered the questions (.is-arriving, like the pipeline card). */
export function ClarifyStage({ sessionId, run, phase, question, strip }: { sessionId: string; run: RunState; phase: CheckPhase; question: string; strip: ReactNode }) {
  const check = run.clarify;
  return (
    <section className="stage is-on no-enter is-arriving" id="stage-clarify" aria-labelledby="clarify-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Before we start</p>
          <h1 className={"ask-q ask-locked" + qFitClass(question)} id="clarify-h" aria-describedby="runningOpts">{question}</h1>
          {strip}
        </div>
        {check && check.questions.length > 0 ? <CheckCard sessionId={sessionId} check={check} phase={phase} /> : null}
      </div>
    </section>
  );
}

function CheckCard({ sessionId, check, phase }: { sessionId: string; check: ClarifyState; phase: CheckPhase }) {
  const { questions } = check;
  const [step, setStep] = useState(0);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [otherOpen, setOtherOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Sent>("idle");
  const [now, setNow] = useState(() => Date.now());
  const posted = useRef(false);
  const advancing = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(tick); if (advancing.current) clearTimeout(advancing.current); };
  }, []);
  // Focus follows the reader's own move to another question, never the first render.
  useEffect(() => { if (moved.current) heading.current?.focus(); }, [step]);

  const q: ClarifyQuestion = questions[Math.min(step, questions.length - 1)];
  const pick = picks[q.id];
  const busy = () => advancing.current !== null || posted.current;

  /* One POST, never retried (web/lib/api.ts convention). */
  async function send(final: Record<string, Pick>, skip: boolean) {
    if (posted.current) return;
    posted.current = true;
    setPicks(final);
    setSent("sending");
    try {
      await submitAnswers(sessionId, { answers: answersBody(questions, final), skip });
      setSent("sent");
    } catch (error) {
      setSent(error instanceof ApiError && error.status === 409 ? "late" : "failed");
    }
  }
  function advance(final: Record<string, Pick>) {
    setPicks(final);
    if (step >= questions.length - 1) { void send(final, false); return; }
    moved.current = true;
    const next = step + 1;
    setOtherOpen(Boolean(final[questions[next].id] && "text" in final[questions[next].id]));
    setStep(next);
  }
  function choose(option: string) {
    if (busy()) return;
    const final = { ...picks, [q.id]: { choice: option } };
    setPicks(final);
    setOtherOpen(false);
    advancing.current = setTimeout(() => { advancing.current = null; advance(final); }, CHOICE_ADVANCE_MS);
  }
  function submitOther() {
    const text = (drafts[q.id] ?? "").trim();
    if (!text || busy()) return;
    advance({ ...picks, [q.id]: { text } });
  }
  function skipOne() {
    if (busy()) return;
    const final = { ...picks };
    delete final[q.id];
    advance(final);
  }
  /* "Just start": whatever the reader has answered, best guesses for the rest (reason "skipped"). */
  function justStart() {
    if (posted.current) return;
    if (advancing.current) { clearTimeout(advancing.current); advancing.current = null; }
    void send(picks, true);
  }
  function back() {
    if (busy() || step === 0) return;
    const previous = questions[step - 1];
    moved.current = true;
    setOtherOpen(Boolean(picks[previous.id] && "text" in picks[previous.id]));
    setStep(step - 1);
  }

  if (sent === "late") {
    return <div className="card" id="clarifyCard" data-face="late"><p className="sm ck-summary">Already started with best guesses</p></div>;
  }
  if (sent === "failed" && phase === "asking") {
    return <div className="card" id="clarifyCard" data-face="failed"><p className="sm ck-summary">Your answers could not be sent; the run starts with best guesses.</p></div>;
  }
  if (phase === "starting" || sent === "sending" || sent === "sent") {
    const answers = check.answered ? check.answered.answers : resolvedAnswers(questions, picks);
    return <div className="card" id="clarifyCard" data-face="summary"><p className="sm ck-summary" id="clarifySummary">{summaryText(questions, answers)}</p></div>;
  }
  const chosen = pick && "choice" in pick ? pick.choice : null;
  const typed = pick && "text" in pick ? pick.text : null;
  const draft = drafts[q.id] ?? typed ?? "";
  return (
    <div className="card" id="clarifyCard" data-face="asking">
      <div className="ck-top">
        <span className="cap" id="clarifyStep">Question {step + 1} of {questions.length}</span>
        <span className="step-dots" aria-hidden="true">{questions.map((x, i) => <i key={x.id} data-on={i === step ? "1" : "0"} />)}</span>
      </div>
      <div className="ck-q" key={q.id}>
        <h3 className="card-title" id="clarifyQ" tabIndex={-1} ref={heading}>{q.text}</h3>
        <div className="choices" role="group" aria-labelledby="clarifyQ">
          {q.options.map((option) => (
            <button key={option} type="button" className="choice" aria-pressed={chosen === option} onClick={() => choose(option)}>
              <span>{option}</span>{option === q.bestGuess ? <span className="cap">best guess</span> : null}
            </button>
          ))}
          <button type="button" className="choice" id="clarifyOtherBtn" aria-pressed={otherOpen || typed !== null} aria-expanded={otherOpen} aria-controls="clarifyOtherRow"
            onClick={() => { if (!busy()) setOtherOpen((open) => !open); }}>
            <span>Other…</span>
          </button>
        </div>
        {otherOpen ? (
          <div className="ck-other" id="clarifyOtherRow">
            <input className="tx" id="clarifyOther" aria-label="Your own answer" placeholder="Type your own answer" maxLength={200} autoFocus value={draft}
              onChange={(e) => { const value = e.target.value; setDrafts((d) => ({ ...d, [q.id]: value })); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitOther(); } }} />
            <button type="button" className="btn btn-ghost btn-sm" disabled={!draft.trim()} onClick={submitOther}>Next</button>
          </div>
        ) : null}
      </div>
      <div className="ic-foot">
        <div className="ic-btns">
          <button type="button" className="btn btn-quiet btn-sm ck-back" disabled={step === 0} onClick={back}>Back</button>
          <button type="button" className="btn btn-quiet btn-sm" onClick={skipOne}>Skip this one</button>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={justStart}>Just start</button>
      </div>
      <p className="cap" id="clarifyCountdown">{countdownText(secondsLeft(check.deadlineAt, now))}</p>
    </div>
  );
}
```

Append to `web/app/globals.css`:

```css

/* ═══ 2026-09-29: the one-time check (live-briefs spec §4.5; pick 4B, docs/design/running-stage-picks/Hitl1.dc.html column B) ═══ */
/* One card in the pipeline card's place: a step line with dots, one question, its answers stacked
   full width, a hairline footer. No purple at rest (D17): the answers and the footer's buttons are
   neutral; the text field alone shows the app's focus ring while it has focus. */
#clarifyCard{display:flex;flex-direction:column;gap:var(--space-4)}
#clarifyCard .ck-top{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3)}
.step-dots{display:flex;gap:6px;align-items:center}
.step-dots i{width:6px;height:6px;border-radius:50%;background:var(--border);transition:background var(--motion-base) var(--ease-standard)}
.step-dots i[data-on="1"]{background:var(--fg)}
#clarifyCard .ck-q{display:flex;flex-direction:column;gap:var(--space-4);animation:enter var(--motion-base) var(--ease-entrance) both}
.choices{display:flex;flex-direction:column;gap:var(--space-2)}
.choice{display:flex;width:100%;min-height:44px;align-items:center;justify-content:space-between;gap:var(--space-2);padding:8px var(--space-4);border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--muted);font-size:var(--text-sm);text-align:left;transition:background var(--motion-fast) var(--ease-standard),border-color var(--motion-fast) var(--ease-standard),color var(--motion-fast) var(--ease-standard)}
.choice:hover{background:var(--border-soft);border-color:var(--muted);color:var(--fg)}
.choice[aria-pressed="true"]{background:var(--border-soft);border-color:var(--fg);color:var(--fg)}
.choice .cap{flex:none}
/* Text entry that reads as text (theme.css .tx): transparent, a soft ground on hover, the ring on focus.
   Its box and its text line up with the answers above it (same padding, an invisible 1px border). */
.tx{flex:1;min-width:0;display:block;min-height:44px;background:transparent;border:1px solid transparent;outline:none;color:var(--fg);font:inherit;font-size:var(--text-sm);padding:8px var(--space-4);border-radius:var(--radius-md);transition:background var(--motion-fast) var(--ease-standard)}
.tx:hover{background:var(--border-soft)}
.tx:focus{background:transparent;box-shadow:var(--focus-ring)}
.tx::placeholder{color:var(--meta)}
#clarifyCard .ck-other{display:flex;align-items:center;gap:var(--space-3)}
#clarifyCard .ic-foot{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap;padding-top:var(--space-4);border-top:1px solid var(--border-soft)}
#clarifyCard .ic-btns{display:flex;gap:var(--space-2)}
#clarifyCard .ck-back{margin-left:calc(-1 * var(--space-4))}
#clarifyCard .ck-summary{color:var(--fg);animation:enter var(--motion-base) var(--ease-entrance) both}
@media (prefers-reduced-motion:reduce){
  /* the next question and the summary fade in place, with no travel (spec §4.3, §4.8) */
  #clarifyCard .ck-q,#clarifyCard .ck-summary{animation-duration:160ms !important}
}
```

- [ ] **Step 4: Run the tests and the checks**

```bash
cd web && npm run -s typecheck && npx vitest run test/components/clarify-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  " && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
grep -nE "#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(" <(sed -n '/═══ 2026-09-29: the one-time check/,$p' web/app/globals.css) || echo "no colour literal"
```

Expected: no `typecheck` output; `Test Files  1 passed (1)`, `Tests  10 passed (10)`; `Test Files  25 passed (25)`, `Tests  182 passed (182)`; `OK`; `no colour literal`.

- [ ] **Step 5: Commit**

```bash
git add web/components/ClarifyStage.tsx web/test/components/clarify-stage.test.tsx web/app/globals.css
git commit -m "feat(web): the one-time check's stage — one question at a time, Other…, Back, Skip, Just start"
```

---

### Task 9: The check in the session page, the sidebar and the e2e (spec §4.5 Stage selection, Sidebar; §4.8; AC10–AC12; §6 Playwright and captures)

**Files:**
- Modify: `web/components/SessionScreen.tsx`:
  - `:4-5` and `:10`: imports;
  - `:20-22`: the header comment;
  - `:118`: the reconnect condition;
  - `:138-142` (anchor as written by Phase 1 Task 7): `live`, `stopped`, `phase`, the chip;
  - `:153`: the flight guard;
  - `:176-179` (anchor as written by Phase 1 Task 9): the stage switch.
- Modify: `web/components/Sidebar.tsx:4`, `:48`; `web/components/ConsoleProvider.tsx:4`, `:72`.
- Test: `web/test/components/session-screen.test.tsx` (a block appended); `web/test/components/sidebar.test.tsx:1`, `:24`, and a block appended.
- E2E: `web/e2e/support.ts:47-49` (`waitTerminal`) and a recorder appended; `web/e2e/clarify.spec.ts` (create); `web/e2e/visual.spec.ts:55-58` (the `10-clarify` capture, after `05-failed`).

**Interfaces:**
- Consumes: Task 6's `isLive`, `checkPhase` and `RunState.clarify`; Task 8's `ClarifyStage`; Task 7's setting; Task 5's API.
- Produces: the stage order `SubmittedStage` (the beat) → `ClarifyStage` (`checkPhase` not `null`) → `RunningPipeline`, for any live session. From `web/e2e/support.ts`:
  - `waitTerminal` now waits for a terminal status: `completed`, `max_iterations`, `incomplete` or `failed`;
  - `installClarifyRecorder(page)`;
  - `clarifyRecord(page): Promise<ClarifyRecord>`, where `interface ClarifyRecord { stage: boolean; summaries: string[] }`.

- [ ] **Step 1: Write the failing tests**

Append to `web/test/components/session-screen.test.tsx`:

```tsx

describe("SessionScreen — the one-time check (live-briefs spec §4.5)", () => {
  const QUESTIONS = [{ id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" }];
  const WAITING: ResearchSessionResponse = { ...RUNNING, status: "needs_input" };
  const requested = () => frame(1, "session.clarification.requested", { questions: QUESTIONS, deadline_at: new Date(Date.now() + 60_000).toISOString() });
  const answered = frame(2, "session.clarification.answered", { reason: "timed_out", answers: [{ question_id: "q1", value: "Global", source: "best_guess" }] });
  const serve = (status: ResearchSessionResponse, frames: () => string, onStream: () => void = () => {}) => vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/stream")) { onStream(); return sse(frames()); }
    if (url.includes("/status")) return json(200, status);
    return json(200, { sessions: [] });
  });

  it("a session waiting for the reader shows the check in the pipeline card's place, under the waiting chip", async () => {
    vi.stubGlobal("fetch", serve(WAITING, requested));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("clarifyStep")?.textContent).toBe("Question 1 of 1"));
    expect(document.getElementById("stage-clarify")!.className).toBe("stage is-on no-enter is-arriving");
    expect(document.getElementById("stage-running")).toBeNull();
    expect(document.getElementById("stage-report")).toBeNull();
    expect(document.querySelector("#topbarStatus .chip")!.textContent!.replace(/\s+/g, " ").trim()).toBe("Waiting for you · a few quick questions");
    expect(document.querySelector("#topbarStatus .chip .dot")!.className).toContain("dot-warn");
  });
  it("shows what the run starts with once answered, then gives way to the pipeline when the planner starts", async () => {
    let planner = false;
    vi.stubGlobal("fetch", serve(RUNNING, () => requested() + answered + (planner ? frame(3, "graph.node.started", { node: "planner", iteration: 0 }) : "")));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("clarifySummary")?.textContent).toBe("Starting research with: Region: Global (best guess)"));
    expect(document.querySelector("#topbarStatus .chip")!.textContent!.replace(/\s+/g, " ").trim()).toMatch(/^Running · /);
    expect(document.querySelector("#topbarStatus .chip .dot")!.className).toContain("dot-live");
    planner = true;
    await waitFor(() => expect(document.getElementById("stage-running")).toBeTruthy(), { timeout: 3_000 });
    expect(document.getElementById("stage-clarify")).toBeNull();
  });
  it("keeps the stream open while the session waits: an ended stream reconnects, as for a running one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let streamCalls = 0;
    vi.stubGlobal("fetch", serve(WAITING, requested, () => { streamCalls++; }));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("clarifyCard")).toBeTruthy());
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    await waitFor(() => expect(streamCalls).toBeGreaterThanOrEqual(2));
  });
});
```

`web/test/components/sidebar.test.tsx` — replace

```tsx
import { render, waitFor } from "@testing-library/react";
```

with

```tsx
import { act, render, waitFor } from "@testing-library/react";
```

`web/test/components/sidebar.test.tsx` — replace

```tsx
afterEach(() => vi.unstubAllGlobals());
```

with

```tsx
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
```

Append to `web/test/components/sidebar.test.tsx`:

```tsx

describe("Sidebar — a session waiting for the reader (live-briefs spec §4.5)", () => {
  it("carries the running mark, and the list keeps polling every 5 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let listCalls = 0;
    const now = new Date().toISOString();
    vi.stubGlobal("fetch", vi.fn(async () => { listCalls++; return json(200, { sessions: [{ ...session("a", now), status: "needs_input" }, session("b", now)] }); }));
    render(<ConsoleProvider><Sidebar /></ConsoleProvider>);
    await waitFor(() => expect(document.querySelector('[data-session="a"]')?.getAttribute("data-run")).toBe("1"));
    expect(document.querySelector('[data-session="a"]')!.getAttribute("aria-label")).toBe("q a — running");
    expect(document.querySelector('[data-session="b"]')!.getAttribute("data-run")).toBe("0");
    const before = listCalls;
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(listCalls).toBe(before + 1);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

```bash
cd web && npx vitest run test/components/session-screen.test.tsx test/components/sidebar.test.tsx 2>&1 | grep -E "Test Files|Tests  |×"
```

Expected: `Test Files  2 failed (2)`, `Tests  4 failed | 7 passed (11)`. The four failures are:
- `carries the running mark, and the list keeps polling every 5 s`;
- `a session waiting for the reader shows the check in the pipeline card's place, under the waiting chip`;
- `shows what the run starts with once answered, then gives way to the pipeline when the planner starts`;
- `keeps the stream open while the session waits: an ended stream reconnects, as for a running one`.

Before this task, a `needs_input` session renders the report stage and stops reconnecting.

- [ ] **Step 3: Select the stage, keep the stream open, and mark the sidebar**

Every `status === "running"` check in these three files becomes `isLive(status)` (ambiguity 22). The chip's status follows the check's phase (ambiguity 23).

`web/components/SessionScreen.tsx` — replace

```tsx
/* The stage is derived from /status and the stream (spec §4.3 stage table):
   404 → not in memory · running+finished_at → service stopped · running → Submitted (this tab, < 2.2 s) then Running ·
   failed → Failed (Task 18) · other terminal → Report (Task 17). */
```

with

```tsx
/* The stage is derived from /status and the stream (spec §4.3 stage table):
   404 → not in memory · running+finished_at → service stopped · running or needs_input → Submitted (this tab, < 2.2 s),
   then the one-time check while it asks and until the planner starts (live-briefs spec §4.5), then Running ·
   failed → Failed (Task 18) · other terminal → Report (Task 17). */
```

`web/components/SessionScreen.tsx` — replace

```tsx
import { ApiError, ApiUnreachableError, getStatus, streamUrl, type ResearchSessionResponse } from "@/lib/api";
import { qFitClass, toSessionView, type SessionView } from "@/lib/format";
```

with

```tsx
import { ApiError, ApiUnreachableError, getStatus, streamUrl, type ResearchSessionResponse } from "@/lib/api";
import { checkPhase } from "@/lib/clarify";
import { isLive, qFitClass, toSessionView, type SessionView } from "@/lib/format";
```

`web/components/SessionScreen.tsx` — replace

```tsx
import { useConsole } from "./ConsoleProvider";
```

with

```tsx
import { ClarifyStage } from "./ClarifyStage";
import { useConsole } from "./ConsoleProvider";
```

`web/components/SessionScreen.tsx` — replace

```tsx
        if (latest && (latest.status !== "running" || latest.finished_at !== null)) {
```

with

```tsx
        if (latest && (!isLive(latest.status) || latest.finished_at !== null)) {
```

`web/components/SessionScreen.tsx` (anchor as written by Phase 1 Task 7) — replace

```tsx
  const stopped = status !== null && status.status === "running" && status.finished_at !== null;
  // live-briefs spec §4.2: "Running · {active step label}" — the stream's active row while it is
  // open (chipStep), the status snapshot's current_agent otherwise; no pass number anywhere.
  const step = status?.status === "running" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent) : null;
  const view: SessionView | null = status ? toSessionView(status, step) : null;
```

with

```tsx
  // live-briefs spec §4.4: a session waiting for the reader (needs_input) is as live as a running one.
  const live = status !== null && isLive(status.status);
  const stopped = live && status.finished_at !== null;
  // live-briefs spec §4.5: "asking" while the check waits for the reader, "starting" from its answers
  // until the planner starts, null otherwise (lib/clarify.ts checkPhase).
  const phase = live ? checkPhase(run.current, status.status) : null;
  // live-briefs spec §4.2: "Running · {active step label}" — the stream's active row while it is
  // open (chipStep), the status snapshot's current_agent otherwise; no pass number anywhere. While
  // the check asks, the chip reads "Waiting for you · a few quick questions": the stream is newer
  // than the last /status, so the phase, not the snapshot, picks the chip's status.
  const step = live && phase !== "asking" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent) : null;
  const view: SessionView | null = status ? toSessionView(live ? { ...status, status: phase === "asking" ? "needs_input" : "running" } : status, step) : null;
```

`web/components/SessionScreen.tsx` — replace

```tsx
    if (status.status === "running" && beat) return; // this is the SubmittedStage branch
```

with

```tsx
    if (live && beat) return; // this is the SubmittedStage branch
```

`web/components/SessionScreen.tsx` (anchor as written by Phase 1 Task 9) — replace

```tsx
  if (status.status === "running") {
    if (beat) return <SubmittedStage sessionId={sessionId} question={status.query} strip={strip} />;
    return <RunningPipeline sessionId={sessionId} run={run.current} question={status.query} strip={strip} startedAt={status.started_at} onToggleRow={toggleRow} />;
  }
```

with

```tsx
  if (live) {
    if (beat) return <SubmittedStage sessionId={sessionId} question={status.query} strip={strip} />;
    // live-briefs spec §4.5: the check takes the pipeline card's place until the planner starts.
    if (phase !== null) return <ClarifyStage sessionId={sessionId} run={run.current} phase={phase} question={status.query} strip={strip} />;
    return <RunningPipeline sessionId={sessionId} run={run.current} question={status.query} strip={strip} startedAt={status.started_at} onToggleRow={toggleRow} />;
  }
```

`web/components/Sidebar.tsx` — replace

```tsx
import type { ResearchSessionResponse } from "@/lib/api";
```

with

```tsx
import type { ResearchSessionResponse } from "@/lib/api";
import { isLive } from "@/lib/format";
```

`web/components/Sidebar.tsx` — replace

```tsx
              const running = s.status === "running";
```

with

```tsx
              const running = isLive(s.status); // live-briefs spec §4.5: a session waiting for the reader counts as running
```

`web/components/ConsoleProvider.tsx` — replace

```tsx
import type { SessionView } from "@/lib/format";
```

with

```tsx
import { isLive, type SessionView } from "@/lib/format";
```

`web/components/ConsoleProvider.tsx` — replace

```tsx
  const anyRunning = sessions.some((s) => s.status === "running");
```

with

```tsx
  const anyRunning = sessions.some((s) => isLive(s.status)); // needs_input too (live-briefs spec §4.5): its mark must clear when the run ends
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
cd web && npm run -s typecheck && npx vitest run test/components/session-screen.test.tsx test/components/sidebar.test.tsx 2>&1 | grep -E "Test Files|Tests  " && npx vitest run 2>&1 | grep -E "Test Files|Tests  "
```

Expected: no `typecheck` output; `Test Files  2 passed (2)`, `Tests  11 passed (11)`; then `Test Files  25 passed (25)`, `Tests  186 passed (186)`.

- [ ] **Step 5: The e2e specs (verification)**

`waitTerminal` must not return on `needs_input` (Review Focus 3). The recorder catches the summary line, which can be on screen for less than a second before the planner starts. `clarify.spec.ts` covers:
- AC10–AC12;
- the Submitted beat followed by the check, with the question held still across both hand-offs;
- the reload row of §4.8;
- reduced motion;
- the phone layout.

The `10-clarify` capture shows question 1 of 3 at both widths.

`web/e2e/support.ts` — replace

```ts
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).not.toBe("running");
}
```

with

```ts
/* Terminal: neither running nor waiting for the reader's answers (needs_input, live-briefs spec §4.4). */
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).toMatch(/^(completed|max_iterations|incomplete|failed)$/);
}
```

Append to `web/e2e/support.ts`:

```ts

/* live-briefs spec §4.5: whether #stage-clarify was ever on the page, and every summary line the
   check showed, recorded from before the page's scripts run — the summary can be on screen for a
   fraction of a second before the planner starts and the running stage takes over. */
export interface ClarifyRecord { stage: boolean; summaries: string[] }
export async function installClarifyRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drClarify: ClarifyRecord };
    w.__drClarify = { stage: false, summaries: [] };
    new MutationObserver(() => {
      if (document.getElementById("stage-clarify")) w.__drClarify.stage = true;
      const line = document.getElementById("clarifySummary")?.textContent ?? "";
      if (line && w.__drClarify.summaries.at(-1) !== line) w.__drClarify.summaries.push(line);
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
}
export const clarifyRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drClarify: ClarifyRecord }).__drClarify);
```

Create `web/e2e/clarify.spec.ts`:

```ts
// live-briefs spec §4.5 and §4.8 (D5-D7; AC10-AC12): the one-time check on the replay server.
// Replay's scripted checker asks its three fixed questions only when POST /research carried
// X-Replay-Clarify: on (api/clarify.py), so every other spec sees no check at all.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API, clarifyRecord, installClarifyRecorder, submit, waitTerminal } from "./support";

const ALL_BEST_GUESSES = "Starting research with: Region: Global (best guess) · Period: Since 2023 (best guess) · For: General understanding (best guess)";
const streamText = async (request: APIRequestContext, id: string) => (await request.get(`${API}/research/${id}/stream`)).text();
const top = (page: Page, selector: string) => page.locator(selector).evaluate((el) => el.getBoundingClientRect().top);

test("a clear question skips the check: no card and no needs_input (AC10)", async ({ page, request }) => {
  await installClarifyRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await waitTerminal(request, id);
  expect((await clarifyRecord(page)).stage).toBe(false);
  expect(await streamText(request, id)).not.toContain("session.clarification");
});

test("the check asks one question at a time, posts the answers once and the run starts on them (AC11)", async ({ page, context, request }) => {
  await installClarifyRecorder(page);
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/api\/research\/[0-9a-f]+\/answers$/.test(r.url())) posts.push(r.postData() ?? ""); });
  const id = await submit(page, "q");
  const card = page.locator("#clarifyCard");
  await expect(card).toBeVisible({ timeout: 10_000 });
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("needs_input");
  await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3");
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Waiting for you · a few quick questions");
  await expect(page.locator("#topbarStatus .chip .dot")).toHaveClass(/dot-warn/);
  await expect(page.locator(`.sb-item[data-session="${id}"]`)).toHaveAttribute("data-run", "1");
  await expect(card.locator(".btn-primary")).toHaveCount(0);
  await card.getByRole("button", { name: "United States" }).click();
  await expect(page.locator("#clarifyStep")).toHaveText("Question 2 of 3");
  await card.getByRole("button", { name: "Other…" }).click();
  await page.getByLabel("Your own answer").fill("since 2021");
  await page.getByLabel("Your own answer").press("Enter");
  await expect(page.locator("#clarifyStep")).toHaveText("Question 3 of 3");
  await card.getByRole("button", { name: "Skip this one" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0])).toEqual({ answers: [{ question_id: "q1", choice: "United States" }, { question_id: "q2", text: "since 2021" }], skip: false });
  expect((await clarifyRecord(page)).summaries).toContain(
    "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
  );
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"answered"');
});

test("with no answer the check starts on best guesses when its wait ends (AC12)", async ({ page, request }) => {
  await installClarifyRecorder(page);
  const created = await request.post(`${API}/research`, { headers: { "X-Replay-Clarify": "on" }, data: { query: "q", config_overrides: { hitl: { answer_wait_s: 4 } } } });
  const id = ((await created.json()) as { session_id: string }).session_id;
  await page.goto(`/research/${id}`);
  await expect(page.locator("#clarifyCountdown")).toHaveText(/^Starts with best guesses in 0:0[0-4] if you don't answer$/, { timeout: 10_000 });
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 15_000 });
  expect((await clarifyRecord(page)).summaries.at(-1)).toBe(ALL_BEST_GUESSES);
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"timed_out"');
});

test("the check follows the Submitted beat and the question never moves across either hand-off (§4.5)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const id = await submit(page, "What limits grid-scale battery storage?");
  // Measured once the Submitted beat's header has settled (its reveal rises 6px, globals.css:220-224).
  await expect(page.locator("#stage-submitted")).toHaveClass(/is-revealing/);
  await page.locator("#submitted-h").evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)).then(() => null));
  const submitted = await top(page, "#submitted-h");
  await expect(page.locator("#stage-clarify")).toHaveClass(/is-arriving/, { timeout: 10_000 });
  await expect(page.locator("#clarifyCard")).toBeVisible();
  await expect(page.locator(".q-flight")).toHaveCount(0);
  expect(Math.abs((await top(page, "#clarify-h")) - submitted)).toBeLessThanOrEqual(1);
  await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  expect(Math.abs((await top(page, "#running-h")) - submitted)).toBeLessThanOrEqual(1);
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"skipped"');
});

test("a reload while the check waits rebuilds the card from the stream (§4.8)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const id = await submit(page, "q");
  await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
  await page.reload();
  await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3");
  await expect(page.locator("#stage-submitted")).toHaveCount(0);
  await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await waitTerminal(request, id);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("the next question fades in place, with no travel (§4.8)", async ({ page, context, request }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
    await page.locator("#clarifyCard").getByRole("button", { name: "United States" }).click();
    await expect(page.locator("#clarifyStep")).toHaveText("Question 2 of 3");
    const animations = await page.locator("#clarifyCard .ck-q").evaluate((el) => el.getAnimations().map((a) => ({
      name: (a as CSSAnimation).animationName,
      duration: a.effect!.getTiming().duration,
      props: (a.effect as KeyframeEffect).getKeyframes().flatMap((k) => Object.keys(k)),
    })));
    expect(animations).toHaveLength(1);
    expect(animations[0].name).toBe("enter");
    expect(animations[0].duration).toBe(160);
    expect(animations[0].props).toContain("opacity");
    expect(animations[0].props).not.toContain("transform");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
    await waitTerminal(request, id);
  });
});

test.describe("390×844", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("the card and its answers are full width, with no sideways scroll (§4.8)", async ({ page, context, request }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
    const layout = await page.evaluate(() => {
      const card = document.getElementById("clarifyCard")!.getBoundingClientRect();
      const wrap = document.querySelector("#stage-clarify .run-wrap")!.getBoundingClientRect();
      const list = document.querySelector("#clarifyCard .choices")!.getBoundingClientRect();
      const widths = [...document.querySelectorAll("#clarifyCard .choice")].map((b) => b.getBoundingClientRect().width);
      return { card: card.width, wrap: wrap.width, list: list.width, widths, scroll: document.scrollingElement!.scrollWidth, inner: window.innerWidth };
    });
    expect(Math.abs(layout.card - layout.wrap)).toBeLessThanOrEqual(1);
    expect(layout.widths).toHaveLength(4);
    for (const width of layout.widths) expect(Math.abs(width - layout.list)).toBeLessThanOrEqual(1);
    expect(layout.scroll).toBeLessThanOrEqual(layout.inner);
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
    await waitTerminal(request, id);
  });
});
```

`web/e2e/visual.spec.ts` — replace

```ts
      await shoot(page, `05-failed${suffix}`);
    });
  });
}
```

with

```ts
      await shoot(page, `05-failed${suffix}`);
    });

    // live-briefs spec §6: the one-time check's card, on question 1 of 3 (pick 4B).
    test(`10-clarify${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3", { timeout: 10_000 });
      await shoot(page, `10-clarify${suffix}`);
      await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
      await waitTerminal(request, id);
    });
  });
}
```

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s build && npx playwright test e2e/clarify.spec.ts --project=chromium && npm run -s test:e2e
```

Expected: `7 passed` (each about 14 s), then the whole project `55 passed` (about 9 min): Phase 1's `47`, plus `settings.spec.ts`'s new test, plus these `7`. Both were observed in planning at `3565397`.

- [ ] **Step 6: Captures and the visual review**

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && VISUAL_CHECKPOINT=P2-T09-clarify npx playwright test --project=visual
```

Expected: `10 passed` (observed in planning); sixteen images in `web/visual/P2-T09-clarify/`. Open `10-clarify.png` and `10-clarify-phone.png` with the Read tool, full height, and compare them with column B of `docs/design/running-stage-picks/Hitl1.dc.html` and with spec §4.5. Confirm:
- **Chip.** `Waiting for you · a few quick questions` on the amber dot. On phone the note ends in an ellipsis, as DESIGN.md §4 rule 7 allows.
- **Header.** The eyebrow `BEFORE WE START`, the locked question, and the four-chip strip, with no chip for the new setting.
- **Card top.** One card: `Question 1 of 3` with three dots on the right, only the first bright. Then the title `Which region should this cover?`.
- **Answers.** Four full-width answers: `United States`, `European Union`, `Global` (with a muted `best guess` at its right end) and `Other…`.
- **Footer.** Below a hairline: `Back` dimmed, `Skip this one` quiet, `Just start` as a ghost button on the right. Then `Starts with best guesses in 0:5x if you don't answer`, which wraps to two lines on phone.
- **Page.** The sidebar's current session carries the live mark. No purple anywhere on the card, no box inside a box, no sideways scroll.

Where the pick differs, the spec wins, and these differences are expected:
- the text field is borderless (the pick's is boxed);
- the footer carries the countdown (column B has none);
- the summary is one line, with no Start over.

Every other capture must match `web/visual/P1-T12-final/` if this checkout has it. If it does not, confirm that none of those captures shows a check card or the waiting chip. Attach the two images and a one-paragraph verdict to the task summary.

- [ ] **Step 7: Commit**

```bash
git add web/components/SessionScreen.tsx web/components/Sidebar.tsx web/components/ConsoleProvider.tsx web/test/components/session-screen.test.tsx web/test/components/sidebar.test.tsx web/e2e/support.ts web/e2e/clarify.spec.ts web/e2e/visual.spec.ts
git commit -m "feat(web): the check between the Submitted beat and the running stage; needs_input is live everywhere"
```

---

### Task 10: The design record for the check (spec §4.9 Phase-2 rows; DESIGN.md's status contract)

**Files:**
- Modify: `docs/design/DESIGN.md` (line numbers at `3565397`):
  - §3: the opening sentence (`:150`), the stage table (rows 2, 2a and 3, `:157-158`), and a paragraph before "The composer exists on stage 1 only" (`:171`);
  - §3.0: the sentence Phase 1 Task 11 wrote about the strip (`:227`);
  - §3.2: the composer table and the request body, as Phase 1 Task 11 left them (`:467`, `:475`, `:480-482`);
  - §4: the literal's sentence (`:899-900`), a `needs_input` row after the Running row as Phase 1 Task 11 wrote it (`:905`), and rule 6 (`:948-950`);
  - §5.6: a bullet before "One decorative loop" (`:1339`);
  - §6: a Check row (`:1588`).
- Modify: `web/README.md`: the capture count (`:23-24`), and a Notes bullet (`:30-32`).

**Interfaces:**
- Consumes: Tasks 2–9, i.e. what the app now does.
- Produces: documentation only.

- [ ] **Step 1: Record the check in DESIGN.md and web/README.md**

The line numbers are those at `3565397`. If a later fix moves DESIGN.md's lines again, the section names still locate each anchor.

`docs/design/DESIGN.md` — replace

```markdown
One page, five stages. The session is the page: `/` and `/research/[session_id]`
```

with

```markdown
One page, five stages, and a one-time check (2a) that can come between the second
and the third. The session is the page: `/` and `/research/[session_id]`
```

`docs/design/DESIGN.md` — replace

```markdown
| 2 | **Submitted** | `status == "running"`, first beat |
```

with

```markdown
| 2 | **Submitted** | `status == "running"` or `"needs_input"`, first beat |
```

`docs/design/DESIGN.md` — replace

```markdown
| Held ~2.2s → stage 3 |
```

with

```markdown
| Held ~2.2s → stage 2a or 3 |
```

`docs/design/DESIGN.md` — replace

```markdown
| 3 | **Running** |
```

with

```markdown
| 2a | **Check** | `status == "needs_input"`, or the stream's `session.clarification.requested` until the planner's `graph.node.started` | One question at a time in the pipeline card's place, under the eyebrow `Before we start`, the locked question and the settings strip (pick 4B): the answers, the best guess marked, **Other…**, then Back · Skip this one · Just start and the countdown; after the answers, the one summary line `Starting research with: …` | The planner starts → stage 3 |
| 3 | **Running** |
```

`docs/design/DESIGN.md` — replace

```markdown
**The composer exists on stage 1 only.**
```

with

```markdown
**Stage 2a, the one-time check, asks once and only when it matters** (live-briefs D4–D7,
D16, 2026-09-29). With "Ask me when the question is unclear" on (§3.2), the session
first asks the configured model whether the question leaves something material open —
geography, period, purpose or scope. Usually it does not, and the run starts as
before. Otherwise the session waits (`needs_input`) and the check takes the pipeline
card's place below the locked question and its settings strip: one question at a time, at most three, two to four
answers each with one marked `best guess`, and **Other…** for the reader's own words.
A tapped answer moves on after 240ms. The footer offers Back, Skip this one and Just
start, and its cap counts down to the start on best guesses (60s, D6). The answers are
shown once, in the summary line, and never again: there is no assumptions UI (D7). No
purple on the card: nothing on it is the page's primary action.

**The composer exists on stage 1 only.**
```

`docs/design/DESIGN.md` (anchor as written by Phase 1 Task 11) — replace

```markdown
chip (live-briefs D15; §3.2).
```

with

```markdown
chip (live-briefs D15; §3.2), and none for the one-time check's setting (D16): whether
the check ran is visible as stage 2a itself.
```

`docs/design/DESIGN.md` (anchor as written by Phase 1 Task 11) — replace

```markdown
What the composer sends — the three knobs it exposes, and the one line it only
```

with

```markdown
What the composer sends — the four knobs it exposes, and the one line it only
```

`docs/design/DESIGN.md` — replace

```markdown
| Output directory | `config_overrides.output.directory` | Free text |
```

with

```markdown
| Output directory | `config_overrides.output.directory` | Free text |
| Ask me when the question is unclear | top-level `ask_clarifying_questions` | **On** or **Off**, default **On** (live-briefs D16), in the slot the extra-passes stepper left; Off makes no check (stage 2a) |
```

`docs/design/DESIGN.md` (anchor as written by Phase 1 Task 11) — replace

```markdown
{"query": "…", "output_format": "markdown",
 "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"},
                      "output": {"directory": "output/"}}}
```

with

```markdown
{"query": "…", "output_format": "markdown",
 "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"},
                      "output": {"directory": "output/"}},
 "ask_clarifying_questions": true}
```

`docs/design/DESIGN.md` — replace

```markdown
The API's `SessionStatus` is a five-value literal (`api/models.py:13-19`). The
interface shows five statuses. This table is the contract between them, and it is
```

with

```markdown
The API's `SessionStatus` is a six-value literal (`api/models.py:31-38`;
`needs_input` joined it with the one-time check, live-briefs 2026-09-29). The
interface shows six statuses. This table is the contract between them, and it is
```

`docs/design/DESIGN.md` (anchor as written by Phase 1 Task 11) — replace

```markdown
| **Running** | `running` | the active row from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · {step}`, e.g. `Running · Researching` |
```

with

```markdown
| **Running** | `running` | the active row from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · {step}`, e.g. `Running · Researching` |
| **Waiting for you** | `needs_input` | the check's phase from the stream (`session.clarification.*`, stage 2a), which outranks a `/status` read taken just before it | `--fg` label, `--warn` dot | `Waiting for you · a few quick questions` |
```

`docs/design/DESIGN.md` — replace

```markdown
6. **`waiting` does not exist.** Before the first frame, or between
   `POST /research` and the first event, the screen is *Running* with stage
   `starting`. There is no sixth status.
```

with

```markdown
6. **The only waiting status is the reader's.** Before the first frame, or
   between `POST /research` and the first event, the screen is *Running* with
   stage `starting`. `needs_input` means the one-time check is waiting for the
   reader's answers (live-briefs D5, D6), shown as *Waiting for you*; nothing
   else waits, and a slow service never reads as waiting.
```

`docs/design/DESIGN.md` — replace

```markdown
- **One decorative loop, and it is not load-bearing.**
```

with

```markdown
- **The one-time check moves once per question** (live-briefs pick 4B, 2026-09-29).
  Its card arrives as the pipeline card does (`.is-arriving`); a tapped answer shows
  as chosen for 240ms, then the next question fades and rises in (`enter`,
  `--motion-base`), and the summary line arrives the same way. The step dots change
  colour, never size. **Under reduced motion** the next question and the summary fade
  in place over 160ms, with no rise.
- **One decorative loop, and it is not load-bearing.**
```

`docs/design/DESIGN.md` — replace

```markdown
| Submitted | nothing beyond Idle |
```

with

```markdown
| Submitted | nothing beyond Idle |
| Check | nothing: `needs_input`, the two `session.clarification.*` events and `POST /research/{id}/answers` serve it (live-briefs Phase 2) |
```

`web/README.md` — replace

```markdown
- `npm run capture:visual` — the fourteen full-page captures (7 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

with

```markdown
- `npm run capture:visual` — the sixteen full-page captures (8 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

`web/README.md` — replace

```markdown
## Notes

- Replay mode's
```

with

```markdown
## Notes

- The one-time check (live-briefs spec §4.4-§4.5): the composer sends
  `ask_clarifying_questions` (the settings row "Ask me when the question is unclear", on by
  default). In replay mode the check asks nothing unless `POST /research` carries
  `X-Replay-Clarify: on` — the proxy forwards it — and then asks a fixed set of three
  questions (Region, Period, For); `e2e/clarify.spec.ts` and the `10-clarify` capture use it.
- Replay mode's
```

- [ ] **Step 2: Check the record**

```bash
PY="$PWD/.venv/bin/python"
"$PY" - <<'EOF'
from pathlib import Path
d = Path("docs/design/DESIGN.md").read_text(encoding="utf-8")
w = Path("web/README.md").read_text(encoding="utf-8")
gone = ["There is no sixth status.", "a five-value literal", "the three knobs it exposes"]
want = ["| 2a | **Check** |", "**Stage 2a, the one-time check, asks once and only when it matters**",
        "| Ask me when the question is unclear | top-level `ask_clarifying_questions` |",
        '"ask_clarifying_questions": true}', "| **Waiting for you** | `needs_input` |",
        "**The only waiting status is the reader's.**", "**The one-time check moves once per question**",
        "| Check | nothing:"]
print({"left": [s for s in gone if s in d], "missing": [s for s in want if s not in d],
       "readme": [s for s in ("sixteen full-page captures", "X-Replay-Clarify: on") if s not in w]})
EOF
```

Expected: `{'left': [], 'missing': [], 'readme': []}` (observed in planning).

- [ ] **Step 3: Commit**

```bash
git add docs/design/DESIGN.md web/README.md
git commit -m "docs(design): the one-time check — stage 2a, needs_input, the settings row"
```

---

### Task 11: Full verification and the final visual review (AC10–AC14)

**Files:** none, unless a check fails. Then fix the failure in the task that owns the code, re-run this task from Step 1, and commit the fix with that task's scope.

**Interfaces:**
- Consumes: Tasks 1–10.
- Produces: the evidence for every acceptance criterion (table below) and the checkpoint `P2-T11-final`.

- [ ] **Step 1: The backend**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT
```

Expected: `4831 passed, 6 skipped, 12 deselected` (Task 1's baseline + 63).

- [ ] **Step 2: The web unit layer**

```bash
cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
```

Expected: no `typecheck` output; `Test Files  25 passed (25)`, `Tests  186 passed (186)`; `OK`.

- [ ] **Step 3: Playwright and the captures**

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s test:e2e && VISUAL_CHECKPOINT=P2-T11-final npx playwright test --project=visual
```

Expected: `55 passed`, then `10 passed` (both observed in planning at `3565397`).

- [ ] **Step 4: The final visual review**

Open all sixteen images in `web/visual/P2-T11-final/` with the Read tool, full height. For every capture, check:
- no sideways scroll;
- tokens only, with no colour outside the palette;
- purple only on the page's one primary control (`Download Report`, or the send button), and never on the check card;
- one surface per region.

Per capture:
- **`01-idle`, `07-idle-phone`.** The composer is unchanged: two pills, six starters, and no pill for the new setting.
- **`02-submitted`, `03-running`, `09-running-extra-pass`, `04-report`, `05-failed`, `08-evidence`** (and their phone captures). Identical to `web/visual/P2-T09-clarify/`: Task 10 changed documentation only. None shows the check card or the waiting chip.
- **`10-clarify`, `10-clarify-phone`.** Everything Task 9 Step 6 lists, compared once more with `docs/design/running-stage-picks/Hitl1.dc.html` column B, `docs/design/running-stage-picks/theme.css:186-188`, `:269-285`, and spec §4.5. The spec wins where they differ.

Attach the images and a one-paragraph verdict per capture to the task summary.

- [ ] **Step 5: Nothing left behind**

```bash
git status --short
git log --oneline -12
```

Expected: `git status --short` prints nothing. The newest commits are the nine of Tasks 2–10, plus any review-fix commits, all above Phase 1's last commit.

---

## Acceptance-criteria coverage

| Criterion (spec §5, §4.8, §6) | Tasks | Proof |
|---|---|---|
| **AC10** With the setting on and a clear question (the replay default), the flow is Phase 1's: no `needs_input`, no card | 5, 9 | pytest `test_replay_asks_nothing_without_the_header`, `test_a_failed_or_empty_check_starts_the_run_exactly_as_before[no_questions]`; e2e `a clear question skips the check: no card and no needs_input (AC10)`; every existing chromium spec still passes (`55 passed`) |
| **AC11** The status goes `needs_input`, the card shows "Question 1 of n", the chip reads "Waiting for you · a few quick questions" | 5, 6, 9 | e2e `the check asks one question at a time, …` (the status read, `#clarifyStep`, chip text and `.dot-warn`); Vitest `needs_input → Waiting for you · …`, the status-chip test |
| **AC11** Answers, including an "Other…" typed answer, POST once, and the summary shows "(you said)" and "(best guess)" | 8, 9 | Vitest `posts the answers once after the last question and shows the summary`; e2e AC11 (one POST, its exact body, the recorded summary, `reason: answered`) |
| **AC11** The planner's packet contains `# Reader answers` | 3, 5 | pytest `test_the_readers_answers_reach_the_planners_packets`, `test_replay_with_the_header_asks_then_plans_with_the_readers_answers` |
| **AC12** With no interaction the run starts within 60 s + 2 s, every answer `best_guess`, `reason == "timed_out"` | 2, 5, 9 | pytest `test_the_hitl_timings_default_to_the_spec_values` (60 s), `test_no_answer_starts_the_run_on_best_guesses_when_the_wait_ends`; e2e `with no answer the check starts on best guesses when its wait ends (AC12)` (a 4 s wait); see O3 |
| **AC13** With the setting off, no check call is made | 5, 7 | pytest `test_with_the_setting_off_no_check_call_is_made`, `test_the_request_flag_defaults_on_and_off_makes_no_check_call`; e2e `with 'Ask me when the question is unclear' off, no check is made (AC13)` |
| **AC14** A check failure never delays the run by more than `check_timeout_s` | 4, 5 | pytest `test_a_check_that_hangs_delays_the_run_by_at_most_check_timeout_s`, `test_a_failed_or_empty_check_starts_the_run_exactly_as_before[check_failed]`, `test_an_invalid_draft_asks_nothing_at_all`, `test_a_live_check_whose_reply_breaks_the_contract_asks_nothing` |
| §4.8 The check call fails or times out → no questions | 4, 5 | as AC14 |
| §4.8 Answers after the deadline → 409; the card reads "Already started with best guesses" and the stage moves on with the stream | 5, 8 | pytest `test_answers_after_the_deadline_are_refused`, `test_the_answers_route_is_404_for_an_unknown_session_and_409_when_nothing_was_asked`; Vitest `an answer the API refuses as late …` |
| §4.8 Reload during `needs_input` → the card rebuilds from the stream | 6, 9 | Vitest `(j) … is burst-safe`; e2e `a reload while the check waits rebuilds the card from the stream (§4.8)` |
| §4.8 Replay mode: the scripted checker, header-gated | 4, 5, 6 | pytest `test_the_replay_checker_asks_the_fixed_set_only_when_the_header_asked`, `test_a_replay_mode_app_checks_with_the_scripted_checker`; Vitest `forwards x-replay-clarify, …` |
| §4.8 Reduced motion | 8, 9 | e2e `the next question fades in place, with no travel (§4.8)` |
| §4.8 Phone: the card and its answers full width, no horizontal scroll | 8, 9 | e2e `the card and its answers are full width, with no sideways scroll (§4.8)`; `visual.spec.ts`'s `shoot` asserts no sideways scroll |
| §6 pytest API: lifecycle (answer, skip, timeout, check failure); the route's 202/404/409/422; `X-Replay-Clarify` gating | 4, 5 | `tests/test_api/test_clarify.py` (23), `tests/test_api/test_clarification.py` (23) |
| §6 The proxy forwards the new header | 6 | Vitest `forwards x-replay-clarify, so replay's scripted check can be asked for (live-briefs spec §4.4)` |
| §6 pytest engine: `derive_answer_contract` with answers; `# Reader answers` in plan and plan-review messages; the full replay suite unchanged without answers | 2, 3 | `tests/test_agents/test_planner_reader_answers.py` (9, `PINNED_PACKETS` included); `tests/test_graph/test_reader_answers.py`; the full suite |
| §6 Vitest: run-state handlers with the exact-keys assertion; `ClarifyStage` (Other…, Back, Skip, Just start, countdown text); chip texts | 6, 8 | `run-state.test.ts` (nineteen handlers, `(j)`), `clarify.test.ts`, `clarify-stage.test.tsx`, `format.test.ts`, `status-chip.test.tsx` |
| §6 Playwright: the check flow with `X-Replay-Clarify: on`; reduced motion; phone layout | 9 | `web/e2e/clarify.spec.ts` (7) |
| §6 Captures `10-clarify(-phone)`, a new checkpoint each time, reviewed full height against the picks | 9, 11 | `P2-T09-clarify`, `P2-T11-final` |
| §4.9 Phase-2 rows: api-gaps' route list; README / web README "Run the app" mention the check and `X-Replay-Clarify` | 5, 10 | the doc edits; Task 10 Step 2's check |
| §4.5 Settings row "Ask me when the question is unclear", On/Off, sent as `ask_clarifying_questions` (D16) | 7 | Vitest `offers 'Ask me when the question is unclear' as On/Off, …`; e2e `settings.spec.ts` (both tests) |
| §4.5 Sidebar: a `needs_input` session counts as running for the live mark | 9 | Vitest `carries the running mark, and the list keeps polling every 5 s`; e2e AC11 (`data-run="1"`) |

## Self-review

**1. Spec coverage (Phase 2).**

§4.4:
- Request → Task 4.
- Config (the `hitl` block, overridable per request) → Task 2.
- "Configured provider and model with thinking disabled and structured output" → Task 4 (`clarity_llm_config`, `complete_structured`).
- Service: `ClarityChecker`, the live checker, the scripted checker, the header, `create_app(clarity_checker)` → Tasks 4 and 5. The proxy allowlist → Task 6.
- The `ClarityCheck` contract and its drop rules → Task 4.
- Lifecycle steps 1–5 → Task 5.
- The route, with its 202/404/409/422 and best guesses → Tasks 4 and 5.
- Engine (`run_research`, `ResearchState`, `ReaderAnswer`) → Task 2.
- The consumer table → Task 3.
- The Replay row → Task 3 (`PINNED_PACKETS`) and the full suite in Tasks 3, 5 and 11.

§4.5:
- Stage selection → Task 9.
- Card, Other…, the footer, no purple, the countdown → Task 8.
- Submit once, the summary, the hand-over on the planner's `graph.node.started` → Tasks 6, 8 and 9.
- Chip → Task 6.
- Settings row → Task 7.
- RunState → Task 6.
- Sidebar → Task 9.

§4.8's Phase-2 rows, AC10–AC14, the Phase-2 parts of §6, and §4.9's Phase-2 rows → the table above.

§7 has no Phase-2 risk. D4 (no plan review, no mid-run pause) holds: the only wait is the one-time check before planning. D5–D7, D16 and D17 → Tasks 7 and 8. Phase 3 is not planned; the config key it needs is present.

**2. Placeholder scan.** There is no "TBD", "TODO", "similar to" or "handle edge cases".
- Every code step shows the code, and every check shows its command and expected output.
- Every expected count was observed in the planning dry run. The whole-project Playwright counts (`55`, `10`) were observed at the end of Phase 1, `3565397`.
- The only [INFERENCE] is O4, which no permitted test can settle.

**3. Type consistency.**
- `ReaderAnswer` has the same six fields in `types.py`, `clarify.resolve_answers`, the planner and every test.
- `ClarityCheck` and `ClarityQuestion` are the same in `clarify.py`, the store and the tests.
- `ClarificationAnswer` exists on both sides: pydantic in `models.py`, and a TS union in `api.ts`, with the same `question_id`/`choice`/`text` keys.
- The wire metadata keys (`questions`, `deadline_at`, `answers`, `question_id`, `value`, `source`, `reason`, `best_guess`) are written by Task 4 and read by Task 6's guards.
- `CheckPhase`, `Pick`, `ClarifyQuestion.bestGuess`, `ClarifyAnswer.questionId` and `RunState.clarify` are the same names in Tasks 6, 8 and 9.
- `isLive` is defined in Task 6 and used in Task 9.
- `CHOICE_ADVANCE_MS` is defined in Task 8 and used in its test.
- The DOM ids (`#stage-clarify`, `#clarifyCard`, `#clarifyStep`, `#clarifySummary`, `#clarifyCountdown`, `#clarifyOther`, `#segAsk`, `#lblAsk`) are the same in the components, Vitest and e2e.

**4. Review Focus.** Each of the five lines names its tests, and each test lives in the task that owns the code.

**5. Proportion.** The plan is long because the dispatch asks for exact code, as the Phase 1 plan gives it. The new files (`clarify.py`, the stage, the tests) appear once, in full, and nothing is repeated between tasks: later tasks refer to earlier ones through their Interfaces blocks.

## Execution notes for the controller

- **Recommended: subagent-driven.** Use one fresh implementer per task and a reviewer gate after each. Run the tasks strictly in order 1 → 11:
  - Tasks 2–5 build on one another's interfaces.
  - Tasks 6–9 share `web/lib/api.ts` and `web/test/api.test.ts`, and Task 9 wires Tasks 6–8 together.
  - Task 10 documents the finished behaviour.
- **What the tasks need.**
  - Tasks 1–5 need only the venv.
  - Tasks 6–11 need `web/node_modules` (`cd web && npm ci` if absent) and Chromium (`/opt/pw-browsers`).
  - Playwright needs ports 8010, 3010 and 3011 free. No other session may run Playwright in this checkout at the same time.
- **Time.** The full backend suite takes about 150 s. A scoped Playwright file takes about 2 min with its build. The whole chromium project takes about 12 min, and the visual project about 2 min.
- **If Task 1 reports an anchor problem,** amend this plan before Task 2. Every anchor is checked up front on purpose, so that no task discovers a stale anchor halfway through its edits.
