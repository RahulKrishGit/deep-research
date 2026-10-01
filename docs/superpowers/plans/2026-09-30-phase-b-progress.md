# Notes, progress, report and Stop — Phase B (live progress per step) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status** reviewed once: written 2026-09-30 by the spec-plan-author agent from the approved spec at `73b4d7a6` (reviewed clean by Fable in three rounds); spec-plan-reviewer's review 1 (of `e6a9c10b`, `.superpowers/reviews/2026-09-30-plan-b-review-1.md`) blocked it on two P1s, with P2s and P3s. Every finding is applied, with the human's ruling on O2 recorded as decision D39 and Phase C's plan (`bd48725c`) folded in; "Review round 1" at the end records each resolution and the re-run. Items for the human are under "Open issues". · **Branch** `feat/notes-progress-report-stop`, executed last, after Phases D, A and C are merged into it (spec §9).

**Goal:** Every step of the running stage shows real progress while it works — Planning's slots and status line, Evaluating's bar and Strong / Fair / Weak split, Verifying's and Writing's tickers of real findings and sentences, and Reviewing's five checks and the reader's notes — from live-only progress events the agents publish as each unit of work starts or settles.

**Architecture:**
- **Engine.** Four new live-only event types, each built by a pure helper and published through `publish_live`, never returned in a state update (spec §4 item 1): `planner.progress` before each plan-side request (`_PlanProgress` in `agents/planner.py`), `source_evaluator.progress` from `score_sources(..., on_progress=)`, `evidence_verifier.progress` from `verify(..., on_progress=)`, and `report_writer.progress` from a `_WritingProgress` the composition binds in a ContextVar, fed by `check_statements(..., on_batch=)`. Counts are sets of keys, so they are cumulative and idempotent. `planner.planning.completed` stamps each slot's final state; `source_evaluator.evaluation.completed` gains the split; the reviewer node publishes its `graph.node.started` and `graph.report.reviewed` live, the latter with five criteria and each note's result from `graph/review_brief.py`.
- **Replay and proxy.** `ReplayRunner._drain` re-stamps each event with its release time and holds after the event `X-Replay-Hold-After` names, until Phase D's `POST /stop` ends the session; the web proxy forwards the header. The replay fixtures are re-captured once.
- **Web.** `RunState` keeps each step's latest progress snapshot, the rows' start times and durations, and the latest hard failures; `lib/briefs.ts` derives a discriminated `body` per row; `components/StepBodies.tsx` renders the bodies inside `BriefSpine`'s existing `.ln` rhythm; `lib/ticker.ts` paces the tickers at one sample per 1,200 ms; `RunningPipeline`'s one-second clock ticks the elapsed times. On a loop route `BriefSpine` holds Reviewing open on its checks and its verdict for `HANDOFF_HOLD_MS` before the hand-off, and the hollow row stays reopenable (decision D39). CSS ports the picked canvas rules with two loops (the sheen, the drift), each only while its step runs.

**Tech Stack:** Python 3.12 (`.venv`), pydantic 2 (`JsonValue`), LangGraph 1.2.10, pytest + pytest-asyncio; Node 24, Next.js 16, React 19, Vitest 5 + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode. Windows 11, Git Bash, the main checkout.

**Spec:** `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` (commit `73b4d7a6`), Phase B only: §4 items 1 and 4, §6 (6.1–6.11), the Phase-B rows of §9, §10 (AC13–AC21), §11.1–§11.3 and §12 R7. Decisions D6–D12, D19, D21, D23, D30, D34 and D35 (§2) are closed; nothing here reopens them. The human's ruling of 2026-09-30 on this plan's O2 is decision D39 (below), closed the same way; it amends §6.7's last bullet. Where this plan had to choose, the choice is under "Spec ambiguities resolved here"; where a spec statement could not be kept as written, it is an entry under "Open issues".

**Starting point: D, A and C merged.** §9 orders the phases D → A → C → B, so B starts from the branch with the other three merged. Every anchor below is text the tree holds then:
- Text Phase D's plan writes (`docs/superpowers/plans/2026-09-30-phase-d-stop.md`): `web/lib/briefs.ts` (`RowState`, `stoppedSubtitle`, `notRunText`), `web/components/BriefSpine.tsx` (`frozen`), `web/lib/run-state.ts` (`StoppedRun`, `session.stopped`), `web/test/run-state.test.ts` (the handler and key lists), `web/test/components/user-stopped-stage.test.tsx`, `web/README.md` (the Stop note) and `docs/design/DESIGN.md`.
- Text Phase A's plan writes (`docs/superpowers/plans/2026-09-30-phase-a-notes.md`, revised 2026-09-30 19:01): `agents/planner.py` (`planning_completed_event(..., note_topics=)` and its call), `web/lib/briefs.ts` (`visibleAcks(run.notes, run.active)`), `web/lib/run-state.ts` (`NoteState.kinds`), `web/test/briefs.test.ts`, `tests/test_agents/test_reader_notes_planning.py` and `tests/test_graph/test_reader_notes_replay.py`.
- Text Phase C's plan writes (`docs/superpowers/plans/2026-09-30-phase-c-report.md` at `bd48725c`, still in review): `tests/test_imports.py`'s `submodules` line, which gains `note_outcomes` (Task 6 anchors on that line), and `graph/nodes.py`'s `from deep_research.graph.note_outcomes import report_note_lines` (Task 6 inserts its import before `graph.state`'s, so it sorts after C's). B's writer hooks stay off the functions §7.1–§7.3 rewrite (`_run_bottom_line`, `_check_and_finalize_bottom_line`, `_consider_bottom_line_point`, `_bottom_line_fallback`): see ambiguity 7; C's bottom-line `_check` call stays valid because `part=None` is the default. Every other B anchor is untouched by C's blocks. Task 1 Step 2 checks every anchor on the merged tree before anything is edited, and stops on any that moved; a later revision of C's plan may move one (O4).

**Evidence.** Planning executed this plan, block by block, on 2026-09-30:
- **Engine, task by task.** On an export of `73b4d7a6` with Phase D's and Phase A's plans applied by their own blocks (D: `anchors: 118 exactly once; creates: 12 absent; appends: 11 onto files present`; A: `anchors: 144 exactly once; spans: 6 found; creates: 1 absent; appends: 10 onto files present`), each of Tasks 2–7 was applied in its order, its tests run first (failing), then its code, then its tests and its regression files. Every pytest Expected line of Tasks 2–7 is that run's output.
- **Web, task by task.** On the same tree, Tasks 7 (proxy), 8–14 were applied the same way; every Vitest, typecheck, check:css and `playwright --list` Expected line was that run's output, and review round 1 re-ran them on D + A + C (the lines now give both, or that run's). Task 8's capture was replaced by the fixtures planning had captured earlier the same day (below).
- **One pass of the whole plan, before review 1's revision,** on a fresh export with D and A (revised) applied: every block applied with each anchor exactly once; then `726 passed` (Phase B's pytest files), `36 passed (36)` / `316 passed (316)` (Vitest), typecheck exit 0, `OK` (check:css), and the full Python suite `5057 passed, 2 deselected` (the baseline was `5017 passed, 2 deselected`: B adds 40).
- **Replay.** pytest's replay tests run the scripted offline cases in-process (network denied). Earlier the same day planning also ran `npm run capture:events` once against a replay server in a scratch export (offline, no provider, no key, no cost) to count the progress events (R7): `98` and `67` events. After that, following the dispatch's "do not run any research session", planning started no server and ran no Playwright test: every Playwright and capture line below is marked **[not run in planning]** and reasoned from that capture; `playwright test --list` (which starts no server) was run.
- **Review round 1, on D + A + C.** The revised plan's edited steps were re-run task by task on an export of `73b4d7a6` with Phase D's, Phase A's (revised) and Phase C's (`bd48725c`) plans applied by their own blocks.
  - Task 8's capture ran in-process (TestClient, network denied, offline credentials), as pytest's replay tests do.
  - Every Expected line that names D + A + C is that run's output. "Review round 1" at the end lists the results and two export artifacts (O4).

## Global Constraints

- **Where.** The main checkout, branch `feat/notes-progress-report-stop`; every path is relative to the repository root. Tasks run **strictly in order 1 → 15**, one at a time. Commit after every task that changes files (Tasks 1 and 15 change none).
- **No live model, no secrets, no paid call.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider or Tavily. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest, or the API in `--mode replay` (Task 8's capture and Playwright's `webServer`).
- **Progress events are live-only (spec §4 item 1, verbatim).** "A progress event (§6.1) is published through `publish_live` and is **not** returned in any `state_update["events"]`. So it is never in `state.events`, a checkpoint, the quality record or `node.completed.event_count`, and the CLI's plain and verbose output are unchanged (no progress type ends in `.completed` or is in `PROGRESS_EVENT_TYPES`). `ResearchSession.publish` records it, so a reconnect replays it."
- **What a progress event may carry (spec §4 item 1, verbatim).** "plan and section titles (≤ 160 chars), a finding's content (≤ 160) and a drafted sentence (≤ 200), both of which the published report and evidence log print anyway, hosts, and page-word correction values (≤ 60). Everything else is a count, an id or an enumerated value. Never an exception message, a URL path or query, or a model's reason text (a Context Check `reason`, a review defect's `problem`, a plan review's `repair_instruction`)."
- **Counts are cumulative and idempotent (§6.1, review M13).** "Every progress event carries running totals, and each total counts a source (evaluator), a finding (verifier) or a sentence label (writer) once, when an event first reports it."
- **Event metadata (§6.1), exactly:**

  | Event | Metadata |
  |---|---|
  | `planner.progress` | `step` (`drafting` \| `fixing` \| `checking`), `check_round` (0, 1, 2), `sub_topics: [{coverage_id, title ≤ 160, state}]`, `[]` until a draft has returned |
  | `planner.planning.completed` (extended) | each `sub_topics` entry gains `state` (final); a note topic (Phase A's `note_id` entry) reads `planned` |
  | `source_evaluator.progress` | `to_rate`, `reused`, `capped`, `rated`, `strong`, `fair`, `weak`, `unrated`, `batches`, `batches_done` |
  | `source_evaluator.evaluation.completed` (extended) | `strong_count`, `fair_count`, `weak_count` |
  | `evidence_verifier.progress` | `total`, `checked`, `verified`, `corrected`, `quoted`, `dropped`, `batches`, `batches_done`, `sample` (`{text ≤ 160, verdict, correction: null \| {field, value ≤ 60}, drop_reason, source: {role, host}}`) or `null` |
  | `report_writer.progress` | `phase` (`sections` \| `bottom_line`), `parts_total`, `parts_returned`, `sentences_drafted`, `sentences_checked`, `backed`, `removed`, `unchecked`, `fraction` (0–1, 3 decimals), `sample` (`{text ≤ 200, verdict: backed \| removed, findings, section}`) or `null` |
  | `graph.report.reviewed` (extended, now live) | `criteria`: 5 × `{dimension, met: true \| false \| null, kinds}`; `notes`: `[{note_id, result, reason, steering?: {result, reason}}]` |
- **Evaluator split (§6.2).** Strong `overall_score ≥ 0.70` (`STRONG_SOURCE_THRESHOLD = 0.70`); Fair `≥ 0.40` and `< 0.70`; Weak `< 0.40` (today's `low_confidence`); not rated: the `unscored_*` statuses.
- **Criteria (§6.2, D23, D35).** completeness ← coverage, mechanism; evidence_quality ← missing_support, acquisition, source_quality, freshness; attribution ← identity; uncertainty ← contradiction; readability ← semantic_duplicate, presentation. `met` is `null` unless the review is `scored`; prioritization and actionability are left out.
- **Ticker pace (§6.5).** `TICKER_HOLD_MS = 1200`: a dwell, not an animation; the newest waiting sample shows when the hold ends.
- **Web copy, verbatim (spec §6.3–§6.7; `—` is U+2014, `…` is U+2026, `·` is U+00B7; (I) is the spec's illustrative copy, used as written):**

  | Where | Text |
  |---|---|
  | Planning status (I) | `Reading your question and your answers…` / `Reading your question…`; `Drafting a plan for your question…`; `Checking the plan covers everything you asked…`; `Checking the fixed plan…`; `Fixing {k} topic(s) the check flagged…` / `Fixing what the check found…`; `Plan ready · research starts now` |
  | Planning slot facts (I) | `drafting`, `checking`, `being fixed`, `fixed`, `still flagged`, `not checked`, `joins the plan`, `from your note` |
  | Planning subtitle / outcome | `Xm SSs`; `{n} sub-topics · {k} from your note(s) · {duration}` |
  | Evaluating (I) | `Rating {n} sources for trustworthiness and relevance`; `Rating {n} new sources · {r} already rated`; `No new sources to rate`; stats `Rated` `{rated} of {n}`, `Strong`, `Fair`, `Weak`, each `not yet` before the first batch; subtitle `starting · not yet rated`, `{rated} of {n} rated`; outcome `{scored} sources rated · {s} strong · {f} fair · {w} weak` (+ ` · {u} not rated`) |
  | Verifying (I) | eyebrow `Just checked`; placeholder `The first findings are being checked…`; verdicts `verified`, `quoted as written`, `corrected — the page dates it {v}` / `— the page states no period for it` / `— the page says it covers {v}` / `— the page says it is about {v}` / `— the page states it as an actual` / `— the page states it as a forecast` / `— one of its figures was not on the page`, `dropped — {reason}` (`the page could not be read again`, `the page does not say this`, `the page does not show this figure`, `the page does not back its date or scope`, `the page's context does not support it`, `its figures could not be checked`); sources `an original report`, `independent research`, `a round-up of other sources`, `the business's own words`, else the host; tally `{checked} of {total} checked · {v} verified · {c} corrected · {d} dropped`; subtitle `starting · not yet checked`, `{checked} of {total} checked` |
  | Writing (I) | eyebrow `Just written`; placeholder `The first section is being drafted…`, then, once a section has returned and until the first checked sentence, `The first sentences are being checked…` (this plan's, O3); `✓ backed by {k} finding(s)`, `✗ removed — no verified finding says this`; tally `{checked} of {drafted} sentences checked · ✓ {backed} backed · ✗ {removed} removed · section {k} of {n}` (+ ` · {u} not checked`); subtitle `{k} of {n} sections written`, `writing the bottom line` |
  | Subtitles this plan adds (I, not in the spec; `liveSubtitle`, Task 10) | Evaluating with nothing to rate `nothing new to rate`; Verifying with nothing to check `nothing to check`; Writing before its first event `starting · not yet written`; Writing with no section to draft this pass `no section to rewrite` |
  | Reviewing criteria (I, D35) | `Covers your whole question`, `Rests on strong evidence`, `Every claim is credited correctly`, `Honest about what is uncertain`, `Easy to read` |
  | Reviewing lines (I) | `Reading the draft as a critical reader would · usually 1–3 min`; `Review done · deciding what happens next…`; eyebrow `Your notes`; facts `reading`, `not checked`, `covered`, `not found`, `researched next`, `honoured`, `not followed`, `no evidence found`; a mixed note `{research words} · {steering words}`; issue words per §6.7, more than one `{n} issues · {first}` |
  | Reviewing verdicts and outcomes (§6.7 table) | `Accepted · all 5 criteria met` + notes clause / `Accepted · all 5 met`; `{d} thing(s) to fix · sending the draft back to the writer` / `… · back to the writer`; `{k} gap(s) to fill · going back to research` / `Sent back to fill {k} gaps`; `Going back to research your note(s)`; `Sending the draft back to the writer for your note(s)`; `The review could not be completed · publishing as partial` / `Review unavailable`; `Not accepted · {m} of 5 met`, `Not accepted · a check the run makes itself failed`, `Not accepted · the reviewer's overall judgement fell short`; `Not accepted · {k} gaps still open` |
  | Reviewing meta | `5 checks`; `5 checks · your notes` while a note is held |
- **Theme (D19, D21, D30).** Tokens only: `(cd web && npm run -s check:css)` prints `OK`; new rules go at the very end of `web/app/globals.css`, with no colour literal. Green (`--status-ok`) is the active step's own progress and a kept verdict; amber (`--status-warn`) a check not met, a flagged slot, a dropped finding or a removed sentence. Two loops besides the halo, each only while its step is the active row: the sheen on Planning's skeleton slots, the drift on Reviewing's bar while its call runs.
- **Reduced motion (§6.9).** No sheen (a still bar); Reviewing's bar a static full-width bar at opacity .35; every cross-fade opacity only over 160 ms; counts jump.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone; captures are `fullPage: true`.
- **Out of Phase B.** No change to what a run researches, writes or decides; no new session status; no prompt text change (`agents/prompts.py` untouched since Task 1, and every request byte-identical to what the tree sent at Task 1); the status chip's own score (`DESIGN.md` §4) is unchanged.

### Conventions every task uses

- **Shell.** Every block runs in Git Bash from the repository root. No shell state survives between blocks: every `web/` command runs in a subshell `(cd web && …)`.
- **Line endings.** The checkout has `core.autocrlf=true`, so tracked files are CRLF on disk. Every anchor below is written with LF. Claude Code's Edit tool matches across the difference; a script must read with universal newlines (Task 1 Step 2's does). New files may be written with LF: git normalises them on commit.
- **Python.** The interpreter is `.venv/Scripts/python.exe`; `pyproject.toml` puts `src` on pytest's path. The pytest line is `.venv/Scripts/python.exe -m pytest <files> -q`. A script that imports `deep_research` outside pytest runs with `PYTHONPATH=src`, so it reads this checkout's code (the re-pin snippet, Tasks 2–5).
  - Full-suite runs deselect `tests/test_config.py::test_the_evidence_verifier_pipeline_config`, which loads the shipped configuration strictly and needs the secrets (they must not be read):
    ```bash
    .venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
    ```
    `2 deselected` is that test and the one `live` test `pyproject.toml` deselects.
- **Web unit tests.** `(cd web && npm run -s typecheck)` (Vitest's files and the e2e specs are type-checked too), `(cd web && npx vitest run [files])`, `(cd web && npm run -s check:css)`.
- **Playwright.** The API runs in replay mode as a Playwright `webServer` on port 8010, with the app on 3010 and an app pointed at a closed port on 3011 (`web/playwright.config.ts`). Build first, and put spec files **before** the project flag: `(cd web && npm run -s build && npx playwright test e2e/progress.spec.ts --project=chromium)`. Captures: `(cd web && VISUAL_CHECKPOINT=<name> npx playwright test --project=visual)`, images in `web/visual/<name>/` (gitignored). Ports 8010, 3010 and 3011 must be free first: `netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'` prints nothing.
- **Edits.** Every edit is "replace this exact text with that text"; "Create" writes a new file; "Append to" adds the block's text at the end of the file. Task 1 proves that each anchor occurs exactly once when its turn comes. If an anchor is not found when you reach it, stop and report it. Never improvise a nearby match.
- **Fingerprint pins.** Four agent modules change, so four pins in `tests/test_evaluation/test_config.py` move: planner (Task 2), source evaluator (3), evidence verifier (4), report writer (5). The re-pin snippet reads the current pin, so it works whatever Phases D, A and C left; Task 1 records the four values it starts from.
- **TDD boundary.** pytest and Vitest tests are written first and shown failing. Playwright specs are verification, written after the code they exercise (Tasks 12–13).
- **Commits.** `git add <paths> && git commit -m "<type>(<scope>): <what>"`, never `git add -A` (`web/visual/`, `web/.e2e-tmp/` and `web/test-results/` are gitignored). Append the attribution trailer your session requires, and push if your session's rules say to.

## Review Focus

1. **Live-only means live-only** (AC13). No progress event in any state update, `state.events`, a node's `event_count` or the CLI; no provider or exception text in any metadata. → `test_progress_events_live_only` (the real graph), each agent's live test, `test_cli_unchanged_for_progress_types`, the `SECRET` checks in the planner, verifier and writer tests.
2. **Counts that cannot drift** (review M13). A source, a finding or a sentence label counts once, whatever the batch size and however often a batch reports. → `test_evaluation_progress_counts_a_source_once`, `test_progress_counts_idempotent`, `test_verifier_tally_ends_on_the_completed_counts[2]` and `[5]`, `test_writing_progress_counts_a_sentence_once`, `test_writer_progress_counts_what_a_substituted_checker_returns`.
3. **The planner's slot states** (AC14). Which slot reads what, for a sound plan, a local repair, a review repair, a failed review and a failed review repair; final states that reflect the slot as it stands. → `test_planner_progress_states` (five flows).
4. **Reviewing never shows a score; its notes follow §6.7** (AC18). Five criteria, the kind map, a mixed note's two halves, an unnamed research note `researched next`, a spent pass `not found`. → `test_reviewed_event_five_criteria_mapping`, `test_reviewed_event_mixed_note_steering`, `step-briefs.test.ts` (Reviewing), the burst-safety and no-score tests in `briefs.test.ts`.
5. **Burst safety** (AC19, DESIGN §5.7). The state after event *k* equals a replay of events 1..*k*, on synthesized runs and on both re-captured fixtures. → `progress-state.test.ts` (burst safety), `briefs.test.ts` (burst safety over the captures).
6. **Motion budget** (AC20, D21). Only the two new loops, only while their step runs; reduced motion stops both and drops every transform. → `progress.spec.ts` ("the two loops"), `motion.spec.ts` (its reduced-motion test now records the new elements too).
7. **The loop route's hold** (D39). Reviewing holds its checks and verdict for `HANDOFF_HOLD_MS` on a loop route, only then hands over, never marks the row, and stays reopenable; a Stop ends the hold. → `brief-spine.test.tsx` (the D39 describe and the rewritten "never awaits…" case), `progress.spec.ts` (D39's case), the "Decision D39" section's trigger.

## Decision D39 — a loop route holds Reviewing on its verdict (the human's ruling, 2026-09-30)

| # | Phase | Decision |
|---|---|---|
| D39 | B | **(This plan's O2, ruled by the human on 2026-09-30; closed)** When the review sends the run back — a redraft, an extra pass, a note pass or a note redraft — the Reviewing row holds open about 2 s on its ✓/✗ list and its verdict line (the canvas's A2 artboard), then hands over to the row the run returns to, and stays reopenable afterwards. This amends spec §6.7's last bullet ("What follows the verdict keeps today's loop presentation"): today's loop presentation follows the hold. |

How Task 11 builds it in `web/components/BriefSpine.tsx` (review 1's P1-1):
- **The hold rides the hand-off's own clock.** `Handoff` gains `held: boolean`. In the render where the active row changes, when the row left is Reviewing and the row now active is the lit arc's own destination (`run.arc !== null && run.active === ARCS[run.arc].to`, `ARCS` from `web/lib/run-state.ts`), the hand-off is `{ from: null, to: <the destination>, awaiting: "report_reviewer", held: true }`. While held, Reviewing paints as today's awaited row: `data-state="active"`, open, its subtitle `reading the draft · {elapsed}` frozen at `reviewedAt`, its body `reviewingBody` with the verdict shown (`status.on = 2`) above the five checks and the notes, but no `aria-current`. The destination row paints `pending`, closed, with no role; no row carries `data-handoff`. An effect ends the hold after `HANDOFF_HOLD_MS` (2,000 ms) with `{ from: "report_reviewer", to, awaiting: null, held: false }`. Then the ordinary hand-off runs: Reviewing folds with the from-role timings (`globals.css`'s `li[data-handoff="from"]` rules), the destination opens with the to-role's, and the roles clear after another `HANDOFF_HOLD_MS`. The awaited branch that waits for a completion is guarded with `!handoff.held`. The arc flows during the hold.
- **The trigger is the lit arc's destination, not review 1's suggested `run.loop === "flowing"`.** This is a deliberate deviation from the review's wording, with the same intent.
  - **Why not `loop`.** `graph.route.decided` sets `loop = "flowing"` only for a loop destination. But the loop's own start event (`graph.extra_pass.started`, `graph.report.redraft_requested`, `graph.note_pass.started`, `graph.note_redraft.requested`) sets `loop = "settled"`. When that event lands in the same React render as the route decision (a reconnect's burst, or two SSE messages batched into one render), a `loop` test misses the hold.
  - **Why the arc's destination.** `run.arc` is set by the same four loop routes. It is cleared only by a `finalize` or `end` route, `graph.session.completed` and `session.stopped` (their handlers in `web/lib/run-state.ts`). No other event moves the active row off Reviewing to another row: the reviewer's own `graph.node.completed` returns before moving it. So "the row left is Reviewing, and the row now active is the lit arc's destination" holds exactly on a loop route, burst or not. A burst that has already carried the run past the destination (Researching done, Evaluating running) gets the ordinary hand-off, not a hold on a row the run has left behind.
  - **Observed.** The case "holds Reviewing even when the loop's own start event lands in the same render as the route decision" fails under the review's condition (`1 failed | 13 passed (14)`) and passes under this one.
- **The hollow row reopens.** After the hold Reviewing has no mark: the loop's `rearm` deleted it, and DESIGN §3.5 item 2 forbids marking it `done` or `loop` above the hollow rows. A pending Reviewing row is `reopenable` when `run.rearmed.report_reviewer` and `run.reviewing.landed` are both true. It then gets the head's toggle and `data-toggle`, and opens on `run.open` like a done row, on `reviewingBody(run, false)` unchanged. Its head keeps the pending treatment: hollow node, static meta `5 checks`. `rearm()` already deletes `run.open.report_reviewer`, and Task 9's reset of `run.reviewing` on Reviewing's `graph.node.started` removes the toggle when Reviewing runs again. Nothing changes in `lib/briefs.ts` or `lib/run-state.ts`.
- **Reduced motion.** The hold is a dwell on a timer, like the hand-off's own `HANDOFF_HOLD_MS`, so it is kept under `prefers-reduced-motion`. The fold and the open that follow are the hand-off's existing opacity fades. DESIGN §5.6 says so (Task 14).
- **Stop during the hold.** `session.stopped` sets `run.active = null`. The next render replaces the held hand-off with `{ from: null, to: null, awaiting: null, held: false }`, and the effect's cleanup clears the hold's timer. The stopped stage's `BriefSpine` is `frozen` and derives no hand-off. No API change.
- **What else moves with it.**
  - The existing Vitest case "never awaits a row a loop sends the run back from" asserted the old drop (Reviewing pending at once). It now asserts the hold and the timer's hand-over (Task 11).
  - `motion.spec.ts`'s Reviewing window is re-derived. The default replay case (`missing-target-triggers-one-extra-pass`, `src/deep_research/api/__main__.py:25`) takes an extra pass, so Reviewing's first from-role is the hold's end, 2 s after `graph.route.decided`. Only the verdict's cross-fade (an `xf` record) falls between its last to-role transition and that one. Its assertions are unchanged (Task 12).
  - `briefs.spec.ts`'s arc test, titled "after Researching reopens", now waits for Researching to reopen before it measures (Task 12).
  - The `09-running-extra-pass` capture waits for Researching to reopen too, so it keeps the design reference's subject (Task 13).
- **Tests.**
  - `brief-spine.test.tsx`, Task 11: "BriefSpine — a loop route holds Reviewing on its checks and verdict (decision D39)", with three cases: the hold, the hand-over, the reopen and the reset; the hold when the loop's start event lands in the same render; a Stop 500 ms into the hold. Also the rewritten "never awaits…" case.
  - `progress.spec.ts`, Task 12: "a loop route holds Reviewing open on its checks and verdict, then hands over; Reviewing reopens (D39, AC18)", on the redraft case held at `graph.route.decided`.

## Spec ambiguities resolved here

**Engine**

1. **`planning_completed_event`'s `states` (§5.2 step 5).** Phase A's builder gains `states: Mapping[str, str] | None = None`; with it, `with_slot_states` stamps each entry: a planned topic reads the state `PlannerAgent.finalize` left in `self._plan_states` (`not_checked` when none was recorded), a note topic (an entry with `note_id`) reads `planned`. Without `states` the event is exactly Phase A's.
2. **Which slots a repair flags (§6.2).** `flagged_topic_ids` returns the plan's own coverage ids that match `\btopic-\d{2}\b` in the text acted on (a target id `topic-02-target-01` names its topic; an id not in the plan is dropped). Only ids leave the helper.
3. **`repaired` and the final states.** A slot is repaired when its title or evidence targets differ (`model_dump_json(include={"title", "evidence_targets"})`) between the attempt before a repair and the one after, or it is new. The final state is `not_checked` unless the latest review that produced a verdict saw the slot as it now stands; then `flagged` when that verdict named it, else `fixed` when a repair changed or added it, else `passed`.
4. **The evaluator's numbers.** `to_rate` is the sources `score_sources` scores this pass (after the cap), `reused` the stored assessments it keeps, `capped` the sources the cap left out; `rated` counts `scored`, `unrated` the `unscored_provider` and `unscored_missing` ones of the settled batches. With nothing to rate one event reads `to_rate: 0`, `batches: 0`.
5. **The verifier's first event and its sample.** `total` is the findings this pass judges (those Figure Match decided and those it sends to a Context Check); the first event follows the Figure Match loop and already counts its decisions. The sample is the first finding of the batch (or of the Figure Match set) by priority `verified_corrected` > `dropped` > `verified` > `quoted`; `correction.field` is `period`, `period_cleared`, `scope`, `subject`, `kind` or `figure` (the first corrected figure's first changed field); `drop_reason` is the finding's own, else its first dropped figure's.
6. **The verifier's batch hook.** `one(batch)` computes `verify_finding(item, replies[key])` (pure) for its own items to count them; the merge after the gather is unchanged. `check_statements(..., on_batch=)` reports each batch's items and verdicts after `_check_statement_batch` returns, outside the gate.
7. **The writer's counts ride in a ContextVar.** `compose_written_report` builds `_WritingProgress(P)` once the part jobs exist and binds it in `_WRITING_PROGRESS`; the part tasks it creates copy that context. `_check` reports through it under `part=(coverage_id, section title)` or, with `part=None`, under the bottom line, and `_attempt_bottom_line_draft` marks each bottom-line call's start. So B adds no parameter to `_run_bottom_line` or `_check_and_finalize_bottom_line`, which Phase C rewrites (§7.1–§7.3); the ContextVar stays set for the rest of the composing task, which calls neither function again. `P` counts the jobs `_run_part` drafts this pass (§6.2: `redraft` or no previous section, with findings). A part whose draft fails counts as returned, with no sentence (`_run_part` reports `part_returned(job.coverage_id, ())` before returning its failed outcome), so `parts_returned` reaches `P` once every part has come back, and the bar and `{k} of {n} sections written` end full. There, "written" means "came back from the writer", the spec's (I) wording kept (review 1, P3). *(Owner-delegated decision O2, 2026-10-01: a failed part is now counted apart. `report_writer.progress` carries `parts_failed`, the parts that ended failed; the subtitle reads `{parts_returned − parts_failed} of {n} sections written`, then ` · {f} couldn't be written`; the tally's `section {k} of {n}` stays the settle count. Spec §6.1, §6.6.)*
8. **The writer's sample and its text.** The first newly counted sentence with a verdict, in batch order; its text is the corrected text for a `corrected` verdict; `findings` is the count of its cited labels. A key no batch reported (a substituted checker returns verdicts without calling `on_batch`) is counted once the check returns, so the last event still reads every sentence checked.
9. **Reviewing's builders live in `graph/review_brief.py`.** They read Phase A's `is_research_note`, `has_steering_kind` and `researched_note_topic_ids`; `agents/` cannot import `graph/`. A research note's half: `covered` when a verified finding answers one of its targets (D31); `not_found` when its topic was researched **or its note is already `passed`** (Fable's final note on §6.7: the unfunded refusal of §5.4 leaves a passed note with no researched topic, and it reads `not found`, not `researched next`); else `pending` / `to_research`.
10. **The reviewer node's two live events.** The started event is the object in the node's update, published live once merged; `graph.report.reviewed` is built after the review's `missing_required_target_ids` is stamped, published live before the notes wait, and returned in the snapshot as the same object (the orchestrator skips its id).
11. **Replay's hold.** `X-Replay-Hold-After: <event_type>[#<n>]` (`n ≥ 1`, default 1; anything else holds nothing) is read only on `POST /research`, into a ContextVar like `requested_clarify`. The drain publishes through the n-th event of that type, then waits on an `asyncio.Event` that is never set, so only a cancellation ends it: Phase D's stop, or shutdown.

**Web**

12. **`RunEvent.timestamp` is optional** (`timestamp?: string`, §4 item 4 says `timestamp: string`): `toRunEvent` always copies the frame's, and the many synthesized events in the existing tests carry none. A handler receives `string | null`. O1 records it as a spec amendment line (accepted by review 1).
13. **Where each row's time comes from.** `RunState.startedAt[node]` is the node's latest `graph.node.started` timestamp; `durations[node]` the seconds from it to the node's `graph.node.completed` (kept for a loop's reviewer too). Planning's and Reviewing's outcomes add `· {duration}`; Reviewing's elapsed time stops when its review lands (`reviewedAt`).
14. **Each block resets on its node's `graph.node.started`** (§6.9). A research note's slot read before the planner starts is kept; a re-armed row starts clean.
15. **The stopped row's facts (Phase D §8.5).** B extends Phase D's `stoppedSubtitle`: every row but Researching reads `Stopped · {its live subtitle}` frozen at `run.stopped.at` (`Stopped` when it has none); Researching keeps D's counted form. A slot still running at the stop reads `stopped` with the ring (`data-topic="stopped"`), as D's topics do.
16. **`SENTENCES` keeps only Publishing's** (§6.9): `{ finalize_report: "Saving the report and evidence log" }`; `verifyingSentence` goes.
17. **The briefs switch in two tasks.** Task 10 adds the bodies' types and derivations to `lib/briefs.ts` without changing `rowBrief` (the old API still compiles), with the components that render them; Task 11 switches `rowBrief` to `body` and `BriefSpine` to the components. Each task ends green.
18. **The e2e hold points.** §11.1's five (`planner.progress`, `source_evaluator.progress#2`, `evidence_verifier.progress#2`, `report_writer.progress#3`, `graph.report.reviewed`), plus `planner.progress#2` (titles), `report_writer.progress#4` (the first checked sentence: at `#3` the replay case has returned both sections and checked none) and `graph.node.started#6` (Reviewing's call running, for the drift), and `graph.route.decided` on the redraft case (D39's hold). `reduced-motion.spec.ts` needs no change: AC20's reduced-motion checks are in `progress.spec.ts`, and `motion.spec.ts`'s reduced-motion test records the new elements' transitions too.
19. **Captures.** §11.3 names `13-planning-brief(-phone)` and `14`–`17` at desktop: Planning at both widths, the other four at 1252 px, held at `planner.progress#2`, `source_evaluator.progress#2`, `evidence_verifier.progress#2`, `report_writer.progress#4` and `graph.report.reviewed`.
20. **Lint.** The new Python lines add no `F`, `E741` or `I001` finding to `ruff check --ignore E501` (the repository is not E501-clean, and no test runs ruff). With Phase C's `from deep_research.graph.note_outcomes import report_note_lines` in `graph/nodes.py`, Task 6 inserts `review_brief`'s import right before `graph.state`'s, so the block stays sorted (review 1, P2-4).
21. **How AC13's allow-list is tested: by exclusion, not by enumeration** (review 1, P3). AC13 says it "tests this allow-list". The tests do this:
    - `test_progress_events_live_only` (Task 7) asserts that no string in any progress event's metadata, over the real graph's replay, holds `://`, so no URL path or query.
    - Three planted secrets never appear in a progress event: a plan review's `repair_instruction` (`SECRET-REVIEW-WORDS`, Task 2), a Context Check `reason` (`SECRET-CONTEXT-REASON`, Task 4) and a Statement Check verdict `reason` (`SECRET-VERDICT-REASON`, Task 5).
    - `test_a_slot_title_is_capped_at_160_characters` holds the plan-title cap.

    Two things are not tested: the other caps (a finding ≤ 160, a sentence ≤ 200, a correction value ≤ 60) are the builders' own truncation and are not asserted, and no test checks every metadata field against the list. `graph.report.reviewed` is not a progress event; `test_review_brief.py` checks that a defect's `problem` never reaches its `criteria`.

## Open issues (for the human)

- **O1 — closed (review 1): a spec amendment line.** `RunEvent.timestamp` is optional, not `string` (ambiguity 12): making it required would change every synthesized `{ type, metadata }` event in the existing Vitest files for no behaviour, and the frames always carry it. Review 1 accepted it. The amendment line for spec §4 item 4: "`RunEvent` is `{ type, metadata, timestamp?: string }`; `toRunEvent` always copies the frame's `timestamp`, and a handler receives `string | null`."
- **O2 — closed: decision D39.** The human ruled on 2026-09-30 that a loop route holds Reviewing open about 2 s on its checks and its verdict, then hands over, and that the row stays reopenable. Built in Task 11, as the "Decision D39" section above sets out.
- **O3 — closed (review 1): `The first sentences are being checked…`.** §6.6's `The first section is being drafted…` would stand under `2 of 2 sections written` once every section had returned and none was checked yet (one 150 ms beat in replay; until the first Statement Check batch returns, live). Writing's placeholder now reads `The first sentences are being checked…` (I) once `partsReturned > 0` and no sample has arrived (`writingBody`'s `placeholder`, Task 10). No human ruling was needed (review 1, P3).
- **O4 — Phase C's plan is in review; B anchors on its current text (`bd48725c`).**
  - **Anchors.** Simulated D → A → C → B on an export of `73b4d7a6`, every B anchor holds exactly once (Task 1 Step 2's Expected line). That is after two re-anchors in Task 6: `tests/test_imports.py`'s line and the `graph/nodes.py` import (review 1, P1-2 and P2-4).
  - **C's pending revisions.** C's review asks for three: the request-count wording of its Expected lines, `noteCaption`'s deletion (`web/lib/notes.ts`, `web/test/notes.test.ts`) and `web/README.md`'s `capture:visual` line. None touches a B anchor: B edits neither notes file, and B no longer edits the `capture:visual` line (Task 7; its own bullet names the captures). A later C revision that edits any other file B anchors on may need Task 1 Step 2 again; it stops on any anchor moved.
  - **Expected lines C shifts (review 1, P2-3).** These were observed on D + A + C with C's plan at `bd48725c`; where C's revision `c0951e35` changes a value, the line gives that revision's value and says so:
    - Task 1 Step 3's pins: `planner` `55c1f86bac40` and `report_writer` `f11d61b869d9` (C's), `source_evaluator` `cc5a310b0aa0` and `evidence_verifier` `4a3d56fab932` (unchanged). Also F0/V0 `33` / `276` (`277` on `bd48725c`; `c0951e35` deletes `noteCaption`'s `notes.test.ts` case) and L0/L1 `76` / `17`; `P0` is `5069` after `c0951e35` (C's own count).
    - Task 2 Step 5's planner move, `55c1f86bac40 -> 75d84b30f360`, and Task 5 Step 5's, `f11d61b869d9 -> c4082e94159e`.
    - Task 2 Step 6, `405 passed`, unchanged. Task 5 Step 6, `288 passed`, now with C's `test_report_bottom_line.py` too: `312 passed`. Task 6 Step 5, `256 passed`. Task 7 Step 6, `257 passed`.
    - Task 8 Step 2's counts are unchanged: C changes the sentence counts inside a pass's last, bottom-line event (7 rather than 8 in either case's first pass, 5 rather than 6 in the redraft), not how many events arrive. No e2e hold point or capture reads that event.
    - Task 5's `test_the_report_written_event_is_published_live` asserts the sequence `[(1, 0, 0.0), (1, 1, 0.5)]`, which depends on `_run_bottom_line`'s early return that C rewrites. It holds on D + A + C (Task 5 Step 4's `4 passed`). On any other sequence, stop and report it.
  - **Two failures that come from the export, not from C or B.**
    - The export applies C's blocks but not C's re-pin steps, so its pins and its `PINNED_RUN_DIGESTS` were still D + A's. C's revision `c0951e35` pins the digests `d92891c23a2cfe2e`/`46` and `9ac8c34e25224206`/`29`. On `bd48725c`, whose digests were `593379964423addd` and `372fa419f195289b`, both byte-identical runs read the same with and without B, so B keeps every request byte-identical on C.
    - `test_note_outcomes.py`'s subprocess imported the main checkout's package. With `PYTHONPATH=src` it passes with B applied.
- **O5 — Playwright was not run in planning** (dispatch: no research session). Tasks 12–13's Playwright and capture Expected lines are reasoned from the planning capture and the Vitest runs; each is marked **[not run in planning]**. Planning did run one offline `capture:events` against a replay server before applying that rule (Evidence), and review round 1 ran Task 8's capture in-process (TestClient, network denied, offline credentials), as pytest's replay tests do.
- **O6 — The latency workstream (D38).**
  - **How Task 1 sees it.** If `perf/latency` (plan `docs/superpowers/plans/2026-09-30-latency.md`) merges before this plan runs, Task 1 Step 1's tool-lock line prints `0`. Do not stop; say so in the task summary.
  - **Anchors that may have moved.**
    - The import blocks of `agents/source_evaluator.py` (Task 3), `agents/evidence_verifier.py` (Task 4) and `agents/report_writer.py` (Task 5).
    - `verify`'s signature, and its `one(batch)` wrapper under `gate = asyncio.Semaphore(self.config.verifier_concurrency)` (Task 4).
    - `check_statements`' `one(batch)` wrapper (Task 4).

    The latency plan edits those regions (its call labels, the halves asked together, the Statement Check capture, figure-bounded batches, the batched reputation lookups). Task 1 Step 2 prints each anchor that moved, and the standing remedy applies: stop, and the controller approves a plan amendment that re-anchors each edit on the text now in the file, keeping its replacement's meaning.
  - **Counts and the prompt check.** The latency work's smaller batch size changes only how many events arrive, never the counts (review M13). `test_statement_check_reports_each_settled_batch` passes `batch_size=5` itself, so it does not depend on `CONTEXT_CHECK_BATCH_SIZE`. Task 15 Step 3 diffs `agents/prompts.py` against the commit Task 1 recorded (`BASE`), so the latency work's own prompt change (its O9) is not read as B's.
- **O7 — R7's estimate (accepted by review 1).** The re-captured fixtures carry 26 and 18 progress events (about 13 per pass, not 20), so paced replay grows by about 4 s and 3 s; the e2e timeout (90 s) is unaffected.

## File map

| File | Responsibility after this plan | Task |
|---|---|---|
| `src/deep_research/agents/events.py`, `src/deep_research/graph/events.py` | the no-provider-text rule binds state-bound events; `report_review_completed_event(..., criteria, notes)` | 2, 6 |
| `src/deep_research/agents/planner.py` | `PLAN_SLOT_STATES`, `flagged_topic_ids`, `plan_progress`, `planner_progress_event`, `with_slot_states`, `_PlanProgress`; `planning_completed_event(..., states=)`; `finalize` publishes, `run` stamps | 2 |
| `src/deep_research/agents/source_evaluator.py` | `STRONG_SOURCE_THRESHOLD`, `source_strength`, `strength_counts`, `_EvaluationProgress`, `evaluation_progress_event`; `score_sources(..., on_progress=)`; the split on `evaluation.completed` | 3 |
| `src/deep_research/agents/evidence_verifier.py` | `verification_sample`, `_VerifyProgress`, `verification_progress_event`; `verify(..., on_progress=)`; `check_statements(..., on_batch=)` | 4 |
| `src/deep_research/agents/report_writer.py` | `writing_progress_event`, `_WritingProgress`, `_WRITING_PROGRESS`; `_check(..., part=)`; the hooks in `_run_part`, `_attempt_bottom_line_draft`, `compose_written_report` | 5 |
| `src/deep_research/graph/review_brief.py` (new), `src/deep_research/graph/nodes.py` | `CRITERIA`, `CRITERION_FOR_KIND`, `review_criteria`, `review_note_results`; the reviewer node's two live events | 6 |
| `src/deep_research/agents/__init__.py`, `src/deep_research/graph/__init__.py` | the new public names | 2–6 |
| `src/deep_research/api/replay.py` | re-stamped publication; `REPLAY_HOLD_HEADER`, `requested_hold`, `parse_hold`; the hold | 7 |
| `web/app/api/[...path]/route.ts` | forwards `x-replay-hold-after` | 7 |
| `web/test/fixtures/events/*.json`, `web/test/fixtures/active-rows.json` | re-captured with the progress events; Phase D's active-row pin rewritten | 8 |
| `web/lib/run-state.ts` | `RunEvent.timestamp`; `startedAt`, `durations`, `planning`, `evaluating`, `verifying`, `writing`, `reviewing`, `hardFailures`; five new handlers; Reviewing's meta and outcomes | 9 |
| `web/lib/briefs.ts` | the bodies' types and derivations (10); `rowBrief`'s `body`, subtitles, outcomes, stopped rows (11) | 10, 11 |
| `web/lib/ticker.ts` (new), `web/components/StepBodies.tsx` (new) | `TICKER_HOLD_MS`, `useTicker`; the five bodies' markup | 10 |
| `web/components/BriefSpine.tsx`, `web/components/RunningPipeline.tsx` | renders each body; the clock; the loop route's hold and the reopenable hollow Reviewing row (D39) | 11 |
| `web/app/globals.css` | the briefs' rules, the two loops, reduced motion | 12 |
| `web/e2e/progress.spec.ts` (new), `briefs.spec.ts`, `motion.spec.ts`, `support.ts`, `visual.spec.ts` | the briefs on the replay stream, D39's case; the arc test and the 09 capture after the hold; the new captures | 12, 13 |
| `README.md`, `web/README.md`, `docs/design/DESIGN.md` | the hold header and replay timestamps (the web README's own bullet names the step briefs' captures; its `capture:visual` line is Phase C's); §3.4, §3.5 (with D39), §5.6 (with D39), §5.7 | 7, 14 |
| Tests | per task, listed in each task's Files | 2–13 |

---

### Task 1: Starting point, every anchor, and the baselines

**Files:** none changed. This task only reads the checkout and this plan.

**Interfaces:**
- Consumes: the branch with Phases D, A and C merged.
- Produces: the proof that every block of Tasks 2–14 applies in order, and the baselines (`BASE` the starting commit, `P0` pytest passed, `V0` Vitest tests, `F0` Vitest files, `L0`/`L1` Playwright tests, the four pins) later tasks add to or compare with.

- [ ] **Step 1: Confirm the starting point**

```bash
git merge-base --is-ancestor 73b4d7a6 HEAD && echo "the spec commit is in this branch"
test -e src/deep_research/api/stop.py && test -e web/components/UserStoppedStage.tsx && echo "Phase D is in"
grep -c "^def is_research_note\|^def has_steering_kind" src/deep_research/agents/reader_notes.py
grep -c "^def researched_note_topic_ids" src/deep_research/graph/state.py
grep -c "^MAX_ANSWER_SENTENCES" src/deep_research/agents/report_writer.py
grep -c "^def report_outline" src/deep_research/agents/report.py
test -e src/deep_research/graph/note_outcomes.py && echo "Phase C's note_outcomes is in"
test ! -e src/deep_research/graph/review_brief.py && test ! -e web/components/StepBodies.tsx && echo "no Phase B file yet"
git status --short --untracked-files=no | wc -l
grep -c "One tool lock for the whole run" src/deep_research/agents/researcher.py
```

Expected, line by line: `the spec commit is in this branch`; `Phase D is in`; `2`; `1` (Phase A is in); `1`; `1` (Phase C is in, §7.1, §7.5); `Phase C's note_outcomes is in` (Task 6 anchors on its import and its `tests/test_imports.py` line); `no Phase B file yet`; `0`; `1` (the researcher's run-wide tool lock is still there: the latency work, D38, has not merged). If any line but the last differs, stop and report it. If the last prints `0`, the latency work merged first: do not stop, say so in the task summary, and read O6 before Step 2.

- [ ] **Step 2: Check every block of this plan against the tree, in order**

The check simulates the whole plan in memory — each block is applied to the text the earlier blocks left — and changes nothing on disk. It reads every file with universal newlines.

```bash
.venv/Scripts/python.exe - <<'EOF'
import re
from pathlib import Path

PLAN = Path("docs/superpowers/plans/2026-09-30-phase-b-progress.md").read_text(encoding="utf-8")
DASH = chr(0x2014)
EDIT = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? " + DASH + r" replace\n\n(?P<f1>`{3,5})[a-z]*\n(?P<old>.*?)\n(?P=f1)\n\nwith\n\n(?P<f2>`{3,5})[a-z]*\n(?P<new>.*?)\n(?P=f2)\n", re.S | re.M)
CREATE = re.compile(r"^Create `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,5})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
APPEND = re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,5})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
blocks = sorted(((m.start(), kind, m) for kind, rx in (("edit", EDIT), ("create", CREATE), ("append", APPEND)) for m in rx.finditer(PLAN)), key=lambda b: b[0])
files, problems, counts = {}, [], {"edit": 0, "create": 0, "append": 0}
def text(path):
    if path not in files:
        files[path] = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n") if Path(path).is_file() else None
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

Expected: `anchors: 165 exactly once; creates: 10 absent; appends: 13 onto files present`.

Anything else is a line per problem (`-1` means that the file is missing). Then stop, edit nothing, and report each line to the controller. The remedy is a plan amendment the controller approves: re-anchor that edit on the text now in the file and keep its replacement's meaning. Never guess an anchor during execution. (Planning observed exactly this line on `73b4d7a6` with Phases D, A and C — C's plan at `bd48725c` — applied by their own blocks. A later revision of C's plan, O4, or the latency work merged first, O6, is what can move an anchor.)

- [ ] **Step 3: Record the baselines**

```bash
git rev-parse HEAD
grep -E '^    "(planner|source_evaluator|evidence_verifier|report_writer)": "[0-9a-f]{12}",$' tests/test_evaluation/test_config.py
.venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config 2>&1 | tail -1
(cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npm run -s typecheck; echo "typecheck exit $?")
(cd web && npm run -s check:css)
(cd web && npx playwright test --project=chromium --list 2>&1 | tail -1; npx playwright test --project=visual --list 2>&1 | tail -1)
```

Expected:
- A 40-character commit id. Write it down as `BASE`: Task 15 Step 3 diffs `agents/prompts.py` against it, so "no prompt text change" means since this task (review 1, P2-1).
- Four pin lines. Write the four 12-character values down: Tasks 2–5's re-pins start from them.
- `P0 passed, 2 deselected`, with no `failed` or `error`. Write `P0` down.
- `Test Files  F0 passed (F0)` and `Tests  V0 passed (V0)`. Write both down.
- `typecheck exit 0`; `OK`.
- `Total: L0 tests in N files` and `Total: L1 tests in 1 file`. Write `L0` and `L1` down.

What planning observed on `73b4d7a6` with D, A and C applied:
- Pins: `planner` `55c1f86bac40` and `report_writer` `f11d61b869d9` (the values C's re-pins leave), `source_evaluator` `cc5a310b0aa0`, `evidence_verifier` `4a3d56fab932`.
- `33` / `276` with C's revision `c0951e35`, which deletes `noteCaption`'s `notes.test.ts` case (planning observed `277` with C at `bd48725c`); `76` and `17`.
- `P0`: `5069` after C (`c0951e35`'s own count); planning observed `5017 passed` on D + A.
- The absolute D + A + C Vitest totals in Tasks 9–11 are for `c0951e35`; planning observed each one higher with C at `bd48725c`.

Use what this step prints.

No commit: nothing changed.

---

### Task 2: Planning's progress (spec §6.1–§6.3; AC13, AC14)

**Files:**
- Modify: `src/deep_research/agents/events.py` (`agent_event`'s docstring), `src/deep_research/graph/events.py` (`graph_event`'s docstring), `src/deep_research/agents/planner.py` (imports, the new block before `_UNCATEGORIZED`, `planning_completed_event`, `PlannerAgent.__init__`, `run`, `finalize`), `src/deep_research/agents/__init__.py`
- Create: `tests/test_agents/test_planner_progress.py`
- Test: `tests/test_agents/test_planner.py` (the live test), `tests/test_agents/test_events.py`, `tests/test_cli/test_render.py`; Phase A's `tests/test_agents/test_reader_notes_planning.py` and `tests/test_graph/test_reader_notes_replay.py` (a note entry now carries `state: "planned"`); `tests/test_evaluation/test_config.py` (the planner pin)

**Interfaces:**
- Consumes: Phase A's `planning_completed_event(outcome, *, note_topics=())` and its call in `PlannerAgent.run`; today's `requested_problems(review)`, `PlanReviewDraft`, `summarize_text`, `publish_live`.
- Produces (`deep_research.agents.planner`, exported from `deep_research.agents`):
  - `PLAN_SLOT_STATES: tuple[str, ...]`
  - `flagged_topic_ids(texts: Sequence[str], sub_topics: Sequence[SubTopic]) -> set[str]`
  - `plan_progress(sub_topics, *, flagged, repaired, step: str, check_round: int) -> dict[str, JsonValue]`
  - `planner_progress_event(metadata: dict[str, JsonValue]) -> ResearchEvent` (type `planner.progress`, agent `planner`)
  - `with_slot_states(event: ResearchEvent, states: Mapping[str, str]) -> ResearchEvent`
  - `planning_completed_event(outcome, *, note_topics=(), states: Mapping[str, str] | None = None)`
  - private: `_PlanProgress` (`publish`, `mark_repaired`, `verdict`, `final`), `PlannerAgent._plan_states: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_planner.py` — replace

```python
    events = outcome.state_update["events"]
    assert [event.event_id for event in received] == [event.event_id for event in events]
    assert events[2].metadata["sub_topics"] == [
        {"coverage_id": s.coverage_id, "title": s.title} for s in outcome.result.sub_topics
    ]

```

with

```python
    events = outcome.state_update["events"]
    # notes-progress-report spec §4 item 1: planner.progress is published live
    # and never returned; every other event is published live as the object returned.
    progress = [event for event in received if event.event_type == "planner.progress"]
    assert [event.metadata["step"] for event in progress] == ["drafting", "checking"]
    assert [event.event_id for event in received if event not in progress] == [
        event.event_id for event in events
    ]
    assert events[2].metadata["sub_topics"] == [
        {"coverage_id": s.coverage_id, "title": s.title, "state": "passed"}
        for s in outcome.result.sub_topics
    ]

```

`tests/test_agents/test_events.py` — replace

```python
from deep_research.agents.events import agent_event

```

with

```python
from deep_research.agents.events import agent_event
from deep_research.graph.events import graph_event

```

Append to `tests/test_agents/test_events.py`:

```python


def test_the_no_provider_text_rule_binds_state_bound_events() -> None:
    """notes-progress-report spec §4 item 1 (AC13): a live-only progress event is never
    copied into ``ResearchState.events``, so both builders scope the rule to the
    events that are."""
    for builder in (agent_event, graph_event):
        assert "state-bound" in (builder.__doc__ or "")
```

Append to `tests/test_cli/test_render.py`:

```python


def test_cli_unchanged_for_progress_types() -> None:
    """notes-progress-report spec §4 item 1 (AC13): plain and verbose output never
    stream a live-only progress event; none ends in ``.completed`` or is a
    progress type."""
    from deep_research.cli import is_streamed_event

    for event_type in (
        "planner.progress",
        "source_evaluator.progress",
        "evidence_verifier.progress",
        "report_writer.progress",
    ):
        assert is_streamed_event(event_type, verbose=False) is False
        assert is_streamed_event(event_type, verbose=True) is False
```


`tests/test_agents/test_reader_notes_planning.py` — replace

```python
    assert completed.metadata["sub_topics"][3:] == [
        {"coverage_id": "note-n3", "title": "Your note: how battery cells are recycled", "note_id": "n3"},
        {"coverage_id": "note-n4", "title": "Your note: recycling, leaving out exports", "note_id": "n4"},
    ]
```

with

```python
    assert completed.metadata["sub_topics"][3:] == [
        {"coverage_id": "note-n3", "title": "Your note: how battery cells are recycled", "note_id": "n3", "state": "planned"},
        {"coverage_id": "note-n4", "title": "Your note: recycling, leaving out exports", "note_id": "n4", "state": "planned"},
    ]
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
    assert completed.metadata["sub_topics"][-1] == {
        "coverage_id": "note-n1", "title": "Your note: recycling, leaving out exports", "note_id": "n1",
    }
```

with

```python
    assert completed.metadata["sub_topics"][-1] == {
        "coverage_id": "note-n1", "title": "Your note: recycling, leaving out exports", "note_id": "n1",
        "state": "planned",
    }
```


Create `tests/test_agents/test_planner_progress.py`:

```python
"""Planning's live progress (notes-progress-report spec §6.1-§6.3, AC13, AC14).

The planner publishes one live ``planner.progress`` right before each plan-side
request -- the draft, a repair, each review -- and stamps every slot's final
state on ``planner.planning.completed``. Progress events are live-only: they
never reach the planner's state update.
"""

from __future__ import annotations

import json

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.planner import (
    ResearchPlan,
    ResearchPlanDraft,
    flagged_topic_ids,
    plan_progress,
    planning_completed_event,
)
from deep_research.agents.steps import ReActRun
from deep_research.graph.live import bind_live_sink
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.utils.types import ResearchEvent, SubTopic
from tests.agent_fakes import ScriptedCompleter, finish
from tests.test_agents.test_planner import (
    _draft,
    _plan,
    _planner,
    _review,
    _stale_draft,
    _state,
    _target,
)

SECRET = "SECRET-REVIEW-WORDS"


def _sub_topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id, title=title, rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
    )


def _topics(*titles: str) -> list[SubTopic]:
    return [_sub_topic(f"topic-{n:02d}", title) for n, title in enumerate(titles, start=1)]


def _states(metadata: dict) -> list[tuple[str, str]]:
    return [(entry["coverage_id"], entry["state"]) for entry in metadata["sub_topics"]]


def test_plan_progress_reads_each_slot_as_the_request_about_to_start_leaves_it() -> None:
    """§6.3: drafted before any review, checking while one runs, being_fixed while a
    flagged slot's repair runs, then passed -- or fixed when a repair changed it."""
    topics = _topics("Alpha", "Beta", "Gamma")

    assert plan_progress((), flagged=set(), repaired=set(), step="drafting", check_round=0) == {
        "step": "drafting", "check_round": 0, "sub_topics": [],
    }
    assert _states(plan_progress(topics, flagged={"topic-02"}, repaired=set(), step="fixing", check_round=0)) == [
        ("topic-01", "drafted"), ("topic-02", "being_fixed"), ("topic-03", "drafted"),
    ]
    assert _states(plan_progress(topics, flagged=set(), repaired={"topic-01"}, step="checking", check_round=1)) == [
        ("topic-01", "checking"), ("topic-02", "checking"), ("topic-03", "checking"),
    ]
    assert _states(plan_progress(topics, flagged={"topic-03"}, repaired={"topic-01"}, step="fixing", check_round=1)) == [
        ("topic-01", "fixed"), ("topic-02", "passed"), ("topic-03", "being_fixed"),
    ]
    entry = plan_progress(topics, flagged=set(), repaired=set(), step="checking", check_round=2)["sub_topics"][0]
    assert entry == {"coverage_id": "topic-01", "title": "Alpha", "state": "checking"}


def test_a_slot_title_is_capped_at_160_characters() -> None:
    [entry] = plan_progress(
        [_sub_topic("topic-01", "Battery " * 40)], flagged=set(), repaired=set(),
        step="checking", check_round=1,
    )["sub_topics"]
    assert len(entry["title"]) <= 160


def test_flagged_ids_are_the_plans_own_coverage_ids_named_in_the_text() -> None:
    """§6.2: only ids leave the helper; a target id names its topic, an unknown id is dropped."""
    topics = _topics("Alpha", "Beta")
    texts = [
        "topic-02-target-01 anchors currency to 2019",
        f"Split topic-01 into two; {SECRET}",
        "topic-09 is not in this plan; nor is subtopic-01",
    ]
    assert flagged_topic_ids(texts, topics) == {"topic-01", "topic-02"}


def test_planning_completed_carries_each_slots_final_state() -> None:
    """§6.1: every entry gains its final state; a note topic reads ``planned``; without
    states the event is exactly what it was."""
    plan = ResearchPlan(sub_topics=_topics("Alpha", "Beta"))
    note = _sub_topic("note-n1", "Your note: pastries at the cafés")
    outcome = AgentRun(
        agent_name="planner", result=plan,
        react=ReActRun(agent_name="planner", stop_reason="finished"),
        errors=[], state_update={},
    )

    plain = planning_completed_event(outcome, note_topics=[note])
    stamped = planning_completed_event(
        outcome, note_topics=[note], states={"topic-01": "fixed"},
    )

    assert all("state" not in entry for entry in plain.metadata["sub_topics"])
    assert [(e["coverage_id"], e["state"]) for e in stamped.metadata["sub_topics"]] == [
        ("topic-01", "fixed"), ("topic-02", "not_checked"), ("note-n1", "planned"),
    ]
    assert stamped.metadata["sub_topics"][2]["note_id"] == "n1"


def _flow(name: str) -> tuple[list[object], list[tuple[str, int, list[tuple[str, str]]]], list[tuple[str, str]]]:
    """(scripted outputs, expected progress events, expected final states) for one AC14 flow."""
    three = _plan("Cryptography", "Hardware timelines", "Mitigations")
    flagged_review = _review(
        sound=False,
        atomicity_defects=["topic-02 asks for two measures at once"],
        repair_instruction=f"Split topic-02-target-01 into two targets. {SECRET}",
    )
    repaired_three = ResearchPlanDraft(sub_topics=[
        _draft("Cryptography", priority=1),
        _draft("Hardware timelines", priority=2,
               evidence_targets=[_target("What qubit count does Alpha report for 2025?")]),
        _draft("Mitigations", priority=3),
    ])
    ids = ["topic-01", "topic-02", "topic-03"]
    if name == "sound on round 1":
        return (
            [three, _review()],
            [("drafting", 0, []), ("checking", 1, [(i, "checking") for i in ids])],
            [(i, "passed") for i in ids],
        )
    if name == "a local repair":
        stale_fixed = ResearchPlanDraft(sub_topics=[
            _draft("Interconnection", priority=1,
                   evidence_targets=[_target("What are the latest interconnection figures?")]),
            _draft("Queue totals", priority=2),
            _draft("Reforms", priority=3),
        ])
        return (
            [_stale_draft(), stale_fixed, _review()],
            [
                ("drafting", 0, []),
                ("fixing", 0, [("topic-01", "being_fixed"), ("topic-02", "drafted"), ("topic-03", "drafted")]),
                ("checking", 1, [(i, "checking") for i in ids]),
            ],
            [("topic-01", "fixed"), ("topic-02", "passed"), ("topic-03", "passed")],
        )
    if name == "a review repair, then sound":
        return (
            [three, flagged_review, repaired_three, _review()],
            [
                ("drafting", 0, []),
                ("checking", 1, [(i, "checking") for i in ids]),
                ("fixing", 1, [("topic-01", "passed"), ("topic-02", "being_fixed"), ("topic-03", "passed")]),
                ("checking", 2, [(i, "checking") for i in ids]),
            ],
            [("topic-01", "passed"), ("topic-02", "fixed"), ("topic-03", "passed")],
        )
    if name == "a failed review":
        return (
            [three, ProviderTimeoutError("timed out")],
            [("drafting", 0, []), ("checking", 1, [(i, "checking") for i in ids])],
            [(i, "not_checked") for i in ids],
        )
    if name == "a failed review repair":
        return (
            [three, flagged_review, ProviderTimeoutError("timed out")],
            [
                ("drafting", 0, []),
                ("checking", 1, [(i, "checking") for i in ids]),
                ("fixing", 1, [("topic-01", "passed"), ("topic-02", "being_fixed"), ("topic-03", "passed")]),
            ],
            [("topic-01", "passed"), ("topic-02", "flagged"), ("topic-03", "passed")],
        )
    raise AssertionError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow",
    [
        "sound on round 1",
        "a local repair",
        "a review repair, then sound",
        "a failed review",
        "a failed review repair",
    ],
)
async def test_planner_progress_states(tracker: Tracker, flow: str) -> None:
    """AC14 on the planner: one live planner.progress before each plan-side request,
    with the §6.3 slot states, and each slot's final state on planning.completed."""
    outputs, expected_progress, expected_final = _flow(flow)
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")], outputs=outputs,
    )
    agent = _planner(tracker, completer)
    question = (
        "What are the current interconnection constraints?"
        if flow == "a local repair" else "What are the security implications of quantum computing?"
    )
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "q"):
        with bind_live_sink(received.append):
            outcome = await agent.run(_state(question))

    progress = [e for e in received if e.event_type == "planner.progress"]
    assert [(e.metadata["step"], e.metadata["check_round"], _states(e.metadata)) for e in progress] == expected_progress
    completed = outcome.state_update["events"][-1]
    assert completed.event_type == "planner.planning.completed"
    assert _states(completed.metadata) == expected_final
    # AC13: live-only, and never the review's own words.
    returned = {e.event_id for e in outcome.state_update["events"]}
    assert all(e.event_id not in returned for e in progress)
    assert all(SECRET not in json.dumps(e.metadata) for e in received)
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_agents/test_planner_progress.py "tests/test_agents/test_planner.py::test_the_planner_publishes_its_events_live_and_lists_the_planned_titles" "tests/test_agents/test_events.py::test_the_no_provider_text_rule_binds_state_bound_events" "tests/test_cli/test_render.py::test_cli_unchanged_for_progress_types" "tests/test_agents/test_reader_notes_planning.py::test_planner_appends_research_notes" "tests/test_graph/test_reader_notes_replay.py::test_mixed_kind_note_steers_and_researches" -q 2>&1 | tail -3
```

Expected: `ERROR tests/test_agents/test_planner_progress.py` (`ImportError: cannot import name 'flagged_topic_ids' from 'deep_research.agents.planner'`), and the run is interrupted at collection (`1 error`).

- [ ] **Step 3: Write the implementation**

`src/deep_research/agents/events.py` — replace

```python
    """Build one progress event attributed to a named agent.

    ``metadata`` must never contain ``str(exception)`` or raw provider text:
    these records are copied into ``ResearchState.events`` and provider text
    can carry keys, URLs, and paths. Record counts, identifiers, and
    enumerated reasons instead.
    """
```

with

```python
    """Build one progress event attributed to a named agent.

    For a state-bound event -- one returned in an agent's state update, and so
    copied into ``ResearchState.events`` -- ``metadata`` must never contain
    ``str(exception)`` or raw provider text, which can carry keys, URLs, and
    paths: record counts, identifiers, and enumerated reasons instead. A
    live-only progress event (``*.progress``, notes-progress-report spec §4
    item 1) is never copied there, and may also carry exactly these
    reader-facing texts: plan and section titles (at most 160 characters), a
    finding's content (160) and a drafted sentence (200), hosts, and page-word
    correction values (60). Never an exception message, a URL path or query,
    or a model's reason text.
    """
```

`src/deep_research/graph/events.py` — replace

```python
    """Build one progress event attributed to the graph or one of its nodes.

    ``metadata`` must never contain ``str(exception)`` or raw provider text:
    these records are copied into ``ResearchState.events``. Record counts,
    identifiers, and enumerated reasons instead.
    """
```

with

```python
    """Build one progress event attributed to the graph or one of its nodes.

    For a state-bound event -- every graph event is returned in its node's
    update and copied into ``ResearchState.events`` -- ``metadata`` must never
    contain ``str(exception)`` or raw provider text. Record counts,
    identifiers, and enumerated reasons instead (notes-progress-report spec §4
    item 1 lists the texts only a live-only progress event may carry).
    """
```


`src/deep_research/agents/planner.py` — replace

```python
from pydantic import Field, ValidationError, field_validator, model_validator

```

with

```python
from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

```

`src/deep_research/agents/planner.py` — replace

```python


_UNCATEGORIZED = "unclassified"

```

with

```python


# --- notes-progress-report spec §6.1-§6.3: Planning's live progress ----------

#: What a plan slot reads (spec §6.3). ``planner.progress`` carries ``drafted``,
#: ``checking``, ``being_fixed``, ``passed`` and ``fixed`` while the plan is
#: drafted, checked and fixed; ``planner.planning.completed`` carries each
#: slot's final state: ``passed``, ``fixed``, ``flagged`` or ``not_checked``.
PLAN_SLOT_STATES: tuple[str, ...] = (
    "drafted", "checking", "being_fixed", "passed", "fixed", "flagged", "not_checked",
)
_PLAN_TOPIC_ID = re.compile(r"\btopic-\d{2}\b")


def flagged_topic_ids(texts: Sequence[str], sub_topics: Sequence[SubTopic]) -> set[str]:
    """The plan's coverage ids that ``texts`` name (notes-progress-report spec §6.2).

    Only ids leave this function, never the text it read: a problem line or a
    plan review's ``repair_instruction`` is the model's own words.
    """
    known = {sub_topic.coverage_id for sub_topic in sub_topics}
    return {
        coverage_id
        for text in texts
        for coverage_id in _PLAN_TOPIC_ID.findall(text)
        if coverage_id in known
    }


def plan_progress(
    sub_topics: Sequence[SubTopic],
    *,
    flagged: set[str] | frozenset[str],
    repaired: set[str] | frozenset[str],
    step: str,
    check_round: int,
) -> dict[str, JsonValue]:
    """``planner.progress`` metadata for the plan-side request about to start (spec §6.1).

    ``step`` names the request: ``drafting`` (the draft), ``fixing`` (a repair)
    or ``checking`` (a review), and ``check_round`` is 0 before the first
    review, 1 for the review and its repair, 2 for the confirming review. Each
    slot's ``state`` follows §6.3: every slot reads ``checking`` while a review
    runs; a slot the text being acted on flagged reads ``being_fixed`` while its
    repair runs; before any review a slot reads ``drafted``, and after one it
    reads ``passed``, or ``fixed`` when a repair changed or added it.
    """

    def state(coverage_id: str) -> str:
        if step == "checking":
            return "checking"
        if step == "fixing" and coverage_id in flagged:
            return "being_fixed"
        if check_round == 0:
            return "drafted"
        return "fixed" if coverage_id in repaired else "passed"

    return {
        "step": step,
        "check_round": check_round,
        "sub_topics": [
            {
                "coverage_id": sub_topic.coverage_id,
                "title": summarize_text(sub_topic.title, limit=160),
                "state": state(sub_topic.coverage_id),
            }
            for sub_topic in sub_topics
        ],
    }


def planner_progress_event(metadata: dict[str, JsonValue]) -> ResearchEvent:
    """One ``planner.progress`` event (notes-progress-report spec §4 item 1, §6.1).

    Live-only: published through the run's sink and never returned in the
    planner's state update, so it is never in ``ResearchState.events``.
    """
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.progress",
        message="Planning progress.",
        metadata=metadata,
    )


def with_slot_states(event: ResearchEvent, states: Mapping[str, str]) -> ResearchEvent:
    """``planner.planning.completed`` with each sub-topic's final slot state (spec §6.1).

    A planned topic reads the state the plan checks left it in (``not_checked``
    when none is recorded); a note topic the run appended (§5.2) reads
    ``planned``.
    """
    entries = event.metadata.get("sub_topics")
    if not isinstance(entries, list):
        return event
    stamped: list[JsonValue] = [
        {
            **entry,
            "state": "planned"
            if "note_id" in entry
            else states.get(str(entry.get("coverage_id")), "not_checked"),
        }
        for entry in entries
        if isinstance(entry, dict)
    ]
    return event.model_copy(
        update={"metadata": {**event.metadata, "sub_topics": stamped}}
    )


def _plan_signature(sub_topic: SubTopic) -> str:
    """What a repair can change about one slot: its title and its targets."""
    return sub_topic.model_dump_json(include={"title", "evidence_targets"})


class _PlanProgress:
    """One planning run's slot states, published live as ``planner.progress`` (spec §6.2).

    ``repaired`` collects the ids a repair changed or added. ``judged`` holds
    each id's signature as the latest review that produced a verdict saw it,
    and ``flagged`` the ids that verdict named; ``judged`` stays ``None`` until
    a review produced a verdict.
    """

    def __init__(self) -> None:
        self.repaired: set[str] = set()
        self.judged: dict[str, str] | None = None
        self.flagged: set[str] = set()

    def publish(
        self,
        step: str,
        check_round: int,
        sub_topics: Sequence[SubTopic],
        *,
        flagged: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        publish_live(
            planner_progress_event(
                plan_progress(
                    sub_topics,
                    flagged=flagged,
                    repaired=self.repaired,
                    step=step,
                    check_round=check_round,
                )
            )
        )

    def mark_repaired(
        self, before: Sequence[SubTopic], after: Sequence[SubTopic]
    ) -> None:
        earlier = {sub_topic.coverage_id: _plan_signature(sub_topic) for sub_topic in before}
        self.repaired |= {
            sub_topic.coverage_id
            for sub_topic in after
            if earlier.get(sub_topic.coverage_id) != _plan_signature(sub_topic)
        }

    def verdict(self, sub_topics: Sequence[SubTopic], review: PlanReviewDraft) -> None:
        self.judged = {
            sub_topic.coverage_id: _plan_signature(sub_topic) for sub_topic in sub_topics
        }
        self.flagged = (
            set()
            if review.sound
            else flagged_topic_ids(
                [*requested_problems(review), review.repair_instruction], sub_topics
            )
        )

    def final(self, sub_topics: Sequence[SubTopic]) -> dict[str, str]:
        """Each slot's final state (spec §6.3), for ``planner.planning.completed``.

        ``not_checked`` when no review produced a verdict on the slot as it now
        stands; otherwise ``flagged`` when that verdict named it (no repair is
        left), ``fixed`` when a repair changed or added it, else ``passed``.
        """
        states: dict[str, str] = {}
        for sub_topic in sub_topics:
            coverage_id = sub_topic.coverage_id
            if (
                self.judged is None
                or self.judged.get(coverage_id) != _plan_signature(sub_topic)
            ):
                states[coverage_id] = "not_checked"
            elif coverage_id in self.flagged:
                states[coverage_id] = "flagged"
            elif coverage_id in self.repaired:
                states[coverage_id] = "fixed"
            else:
                states[coverage_id] = "passed"
        return states


_UNCATEGORIZED = "unclassified"

```

`src/deep_research/agents/planner.py` — replace

```python
from collections.abc import Callable, Sequence

```

with

```python
from collections.abc import Callable, Mapping, Sequence

```

`src/deep_research/agents/planner.py` — replace

```python
def planning_completed_event(
    outcome: AgentRun["ResearchPlan"],
    *,
    note_topics: Sequence[SubTopic] = (),
) -> ResearchEvent:
```

with

```python
def planning_completed_event(
    outcome: AgentRun["ResearchPlan"],
    *,
    note_topics: Sequence[SubTopic] = (),
    states: Mapping[str, str] | None = None,
) -> ResearchEvent:
```

`src/deep_research/agents/planner.py` — replace

```python
    plan = outcome.result
    planned = [] if plan is None else list(plan.sub_topics)
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
```

with

```python
    plan = outcome.result
    planned = [] if plan is None else list(plan.sub_topics)
    event = agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
```

`src/deep_research/agents/planner.py` — replace

```python
            "stop_reason": outcome.react.stop_reason,
            "iterations": outcome.react.iterations,
            "tool_calls": outcome.react.tool_calls,
        },
    )



```

with

```python
            "stop_reason": outcome.react.stop_reason,
            "iterations": outcome.react.iterations,
            "tool_calls": outcome.react.tool_calls,
        },
    )
    # notes-progress-report spec §6.1: with ``states`` each entry also carries
    # its slot's final state (§6.3); without them the event is as Phase A built it.
    return event if states is None else with_slot_states(event, states)



```

`src/deep_research/agents/planner.py` — replace

```python
        # Counted per run so the review/repair cycle stays bounded.
        self._review_calls = 0


```

with

```python
        # Counted per run so the review/repair cycle stays bounded.
        self._review_calls = 0
        # Set by ``finalize`` (notes-progress-report spec §6.3): each slot's final
        # state, which ``run`` stamps on ``planner.planning.completed``.
        self._plan_states: dict[str, str] = {}


```

`src/deep_research/agents/planner.py` — replace

```python
        self._review_calls = 0
        events = [
            planning_started_event(state),
```

with

```python
        self._review_calls = 0
        self._plan_states = {}
        events = [
            planning_started_event(state),
```

`src/deep_research/agents/planner.py` — replace

```python
        completed = planning_completed_event(outcome, note_topics=note_topics)

```

with

```python
        completed = planning_completed_event(
            outcome, note_topics=note_topics, states=self._plan_states
        )

```

`src/deep_research/agents/planner.py` — replace

```python
        contract = self.answer_contract_for(task.instruction)
        attempt = await self._request_plan(
            task, run, contract=contract, plan=_PLAN_DRAFT_LABEL
        )
        repaired = False
        if attempt.structural or attempt.advisory:
            repaired = True
            try:
```

with

```python
        contract = self.answer_contract_for(task.instruction)
        # notes-progress-report spec §6.2: one live ``planner.progress`` right
        # before each plan-side request; nothing runs between one request's
        # return and the next one's start, so each event also marks the return.
        progress = _PlanProgress()
        progress.publish("drafting", 0, ())
        attempt = await self._request_plan(
            task, run, contract=contract, plan=_PLAN_DRAFT_LABEL
        )
        draft = attempt
        repaired = False
        if attempt.structural or attempt.advisory:
            repaired = True
            progress.publish(
                "fixing", 0, attempt.sub_topics,
                flagged=flagged_topic_ids(attempt.problems, attempt.sub_topics),
            )
            try:
```

`src/deep_research/agents/planner.py` — replace

```python
        attempt = self._without_defective_targets(attempt, contract=contract)
        self._record_defects(
            run,
            stage="plan_checks",
            plan=attempt.plan,
            problems=attempt.labelled_advisory,
        )

        try:
            review = await self._review_plan(
                contract, attempt.sub_topics, run=run
            )
```

with

```python
        attempt = self._without_defective_targets(attempt, contract=contract)
        if repaired:
            progress.mark_repaired(draft.sub_topics, attempt.sub_topics)
        self._record_defects(
            run,
            stage="plan_checks",
            plan=attempt.plan,
            problems=attempt.labelled_advisory,
        )

        progress.publish("checking", 1, attempt.sub_topics)
        try:
            review = await self._review_plan(
                contract, attempt.sub_topics, run=run
            )
```

`src/deep_research/agents/planner.py` — replace

```python
                problems=_raised_problems(
                    error, label=attempt.plan, what="the plan review"
                ),
            )
            return self._plan_from(attempt, contract=contract, repaired=repaired)
        if review.sound:
            return self._plan_from(attempt, contract=contract, repaired=repaired)

```

with

```python
                problems=_raised_problems(
                    error, label=attempt.plan, what="the plan review"
                ),
            )
            self._plan_states = progress.final(attempt.sub_topics)
            return self._plan_from(attempt, contract=contract, repaired=repaired)
        progress.verdict(attempt.sub_topics, review)
        if review.sound:
            self._plan_states = progress.final(attempt.sub_topics)
            return self._plan_from(attempt, contract=contract, repaired=repaired)

```

`src/deep_research/agents/planner.py` — replace

```python
        rejected = [
            f"{attempt.plan}: {problem}"
            for problem in requested_problems(review)
        ]
        try:
            reattempt = await self._request_plan(
```

with

```python
        rejected = [
            f"{attempt.plan}: {problem}"
            for problem in requested_problems(review)
        ]
        progress.publish("fixing", 1, attempt.sub_topics, flagged=progress.flagged)
        try:
            reattempt = await self._request_plan(
```

`src/deep_research/agents/planner.py` — replace

```python
                    what="the plan review repair",
                ),
            )
            return self._plan_from(attempt, contract=contract, repaired=True)
        if not reattempt.usable:
```

with

```python
                    what="the plan review repair",
                ),
            )
            self._plan_states = progress.final(attempt.sub_topics)
            return self._plan_from(attempt, contract=contract, repaired=True)
        if not reattempt.usable:
```

`src/deep_research/agents/planner.py` — replace

```python
                plan=reattempt.plan,
                problems=reattempt.labelled,
            )
            return self._plan_from(attempt, contract=contract, repaired=True)
        attempt = reattempt

```

with

```python
                plan=reattempt.plan,
                problems=reattempt.labelled,
            )
            self._plan_states = progress.final(attempt.sub_topics)
            return self._plan_from(attempt, contract=contract, repaired=True)
        progress.mark_repaired(attempt.sub_topics, reattempt.sub_topics)
        attempt = reattempt

```

`src/deep_research/agents/planner.py` — replace

```python
        confirming: PlanReviewDraft | None = None
        try:
            confirming = await self._review_plan(
```

with

```python
        confirming: PlanReviewDraft | None = None
        progress.publish("checking", 2, attempt.sub_topics)
        try:
            confirming = await self._review_plan(
```

`src/deep_research/agents/planner.py` — replace

```python
        if confirming is not None and not confirming.sound:
            self._record_defects(
                run,
                stage="confirming_review",
                plan=attempt.plan,
                problems=[
                    f"{attempt.plan}: {problem}"
                    for problem in requested_problems(confirming)
                ],
            )
        return self._plan_from(attempt, contract=contract, repaired=True)

```

with

```python
        if confirming is not None:
            progress.verdict(attempt.sub_topics, confirming)
        if confirming is not None and not confirming.sound:
            self._record_defects(
                run,
                stage="confirming_review",
                plan=attempt.plan,
                problems=[
                    f"{attempt.plan}: {problem}"
                    for problem in requested_problems(confirming)
                ],
            )
        self._plan_states = progress.final(attempt.sub_topics)
        return self._plan_from(attempt, contract=contract, repaired=True)

```


`src/deep_research/agents/__init__.py` — replace

```python
    planning_completed_event,

```

with

```python
    planning_completed_event,
    PLAN_SLOT_STATES,
    flagged_topic_ids,
    plan_progress,
    planner_progress_event,
    with_slot_states,

```

`src/deep_research/agents/__init__.py` — replace

```python
    "planning_completed_event",

```

with

```python
    "planning_completed_event",
    "PLAN_SLOT_STATES",
    "flagged_topic_ids",
    "plan_progress",
    "planner_progress_event",
    "with_slot_states",

```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's command again. Expected: `14 passed` (`test_planner_progress.py`'s 9, with the five flows of `test_planner_progress_states`, and the five others; `test_cli_unchanged_for_progress_types` already passed: it pins behaviour this task must keep).

- [ ] **Step 5: Re-pin the planner (module code only)**

```bash
.venv/Scripts/python.exe -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | tail -1
PYTHONPATH=src .venv/Scripts/python.exe - planner "Phase B (notes-progress-report spec §6.1-§6.3, 2026-09-30): the planner publishes a live planner.progress before each plan-side request and stamps each slot's final state on planner.planning.completed. No request text changed; agents.prompts was untouched." <<'EOF'
import re
import sys
import textwrap
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

path = Path("tests/test_evaluation/test_config.py")
args = sys.argv[1:]
for agent, reason in zip(args[::2], args[1::2]):
    text = path.read_text(encoding="utf-8")
    pins = re.findall('(?m)^    "' + agent + '": "([0-9a-f]{12})",$', text)
    assert len(pins) == 1, "pin for " + agent + " found " + str(len(pins)) + " times"
    old, new = pins[0], agent_prompt_fingerprint(agent)
    anchor = '    "' + agent + '": "' + old + '",\n'
    comment = textwrap.fill(reason + " Moved `" + old + "` -> `" + new + "`.", width=78, initial_indent="    # ", subsequent_indent="    # ")
    path.write_text(text.replace(anchor, comment + '\n    "' + agent + '": "' + new + '",\n'), encoding="utf-8")
    print(agent + ": " + old + " -> " + new)
EOF
.venv/Scripts/python.exe -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | tail -1
```

Expected: `1 failed, 10 passed, 67 deselected` (`test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift`: the planner's module changed); then `planner: <the Task 1 value> -> <a new value>`; then `11 passed, 67 deselected`. Planning observed `planner: 55c1f86bac40 -> 75d84b30f360` on D + A + C (`3934bea57f61 -> def9ba2de30c` on D + A): the snippet pins whatever the module now hashes to.

- [ ] **Step 6: Run the regression files**

```bash
.venv/Scripts/python.exe -m pytest tests/test_agents/test_planner.py tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_events.py tests/test_cli/test_render.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: no `failed` or `error` (planning: `405 passed`, on D + A and on D + A + C).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/agents/events.py src/deep_research/graph/events.py src/deep_research/agents/planner.py src/deep_research/agents/__init__.py tests/test_agents/test_planner_progress.py tests/test_agents/test_planner.py tests/test_agents/test_events.py tests/test_cli/test_render.py tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(progress): Planning's live progress -- planner.progress before each plan-side request, final slot states"
```

---

### Task 3: Evaluating's progress and split (spec §6.1, §6.2, §6.4; AC13, AC15)

**Files:**
- Modify: `src/deep_research/agents/source_evaluator.py` (imports, `STRONG_SOURCE_THRESHOLD` and the strength helpers, `_EvaluationProgress`, `evaluation_progress_event`, `evaluation_completed_event`, `score_sources`, `run`), `src/deep_research/agents/__init__.py`
- Test: `tests/test_agents/test_source_evaluator.py`; `tests/test_evaluation/test_config.py` (the pin)

**Interfaces:**
- Consumes: today's `score_sources(task)`, `ScoredSource.overall_score`/`.status`, `LOW_CONFIDENCE_THRESHOLD`, `publish_live`.
- Produces (exported from `deep_research.agents`): `STRONG_SOURCE_THRESHOLD = 0.70`; `SourceStrength = Literal["strong", "fair", "weak"]`; `source_strength(source: ScoredSource) -> SourceStrength | None` (`None` for an unscored source); `strength_counts(sources) -> dict[str, int]` (`strong_count`, `fair_count`, `weak_count`); `evaluation_progress_event(metadata: Mapping[str, JsonValue]) -> ResearchEvent`; `score_sources(task, *, on_progress: Callable[[dict[str, JsonValue]], None] | None = None)`. `evaluation_completed_event` adds the three counts.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_source_evaluator.py` — replace

```python
    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "source_evaluator.evaluation.started",
        "source_evaluator.evaluation.completed",
    ]
    assert [event.event_id for event in received] == [event.event_id for event in events]

```

with

```python
    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "source_evaluator.evaluation.started",
        "source_evaluator.evaluation.completed",
    ]
    # notes-progress-report spec §4 item 1: the progress event is live-only.
    assert [event.event_type for event in received] == [
        "source_evaluator.evaluation.started",
        "source_evaluator.progress",
        "source_evaluator.evaluation.completed",
    ]
    assert [
        event.event_id for event in received if event.event_type != "source_evaluator.progress"
    ] == [event.event_id for event in events]

```

`tests/test_agents/test_source_evaluator.py` — replace

```python
from deep_research.agents.source_evaluator import (
    AUTHORITY_WEIGHT,
```

with

```python
from deep_research.agents.source_evaluator import (
    AUTHORITY_WEIGHT,
```

`tests/test_agents/test_source_evaluator.py` — replace

```python
    REPUTATION_BLEND,
    EvaluatedSources,
```

with

```python
    REPUTATION_BLEND,
    STRONG_SOURCE_THRESHOLD,
    EvaluatedSources,
```

`tests/test_agents/test_source_evaluator.py` — replace

```python
    overall_score,
)
from deep_research.agents.sources import SourceGroup, normalize_source_url
```

with

```python
    overall_score,
    source_strength,
)
from deep_research.agents.sources import SourceGroup, normalize_source_url
```

Append to `tests/test_agents/test_source_evaluator.py`:

```python


# --- notes-progress-report spec §6.1, §6.2, §6.4: Evaluating's live progress --------


def _strength_source(url: str, overall: float | None, *, status: str = "scored") -> ScoredSource:
    if status != "scored":
        return fallback_scored_source(_group(url=url), reason=status)
    return ScoredSource(
        url=url, title="t", authority_score=overall, recency_score=overall,
        relevance_score=overall, overall_score=overall, rationale="r",
        low_confidence=overall < LOW_CONFIDENCE_THRESHOLD,
    )


def test_source_strength_split() -> None:
    """§6.2: strong at 0.70 and above, weak below 0.40 (today's low_confidence),
    fair between, and no strength for an unscored source."""
    assert STRONG_SOURCE_THRESHOLD == 0.70
    assert source_strength(_strength_source("https://a.test/1", 0.70)) == "strong"
    assert source_strength(_strength_source("https://a.test/2", 0.69)) == "fair"
    assert source_strength(_strength_source("https://a.test/3", 0.40)) == "fair"
    assert source_strength(_strength_source("https://a.test/4", 0.39)) == "weak"
    assert source_strength(_strength_source("https://a.test/5", None, status="unscored_provider")) is None


@pytest.mark.asyncio
async def test_evaluator_progress_split(tracker: Tracker) -> None:
    """AC15: one event once the batches are planned, then one per settled batch in
    completion order, scored or failed; strong + fair + weak is always ``rated``,
    and the bar's (rated + unrated) / to_rate reaches 1 exactly at the last batch."""
    findings = [_eval_finding(f"https://source-{index}.test/page") for index in range(5)]
    first = SourceScoresDraft(sources=[
        _draft(url="https://source-0.test/page", authority=0.8, recency=0.6, relevance=0.9),
        _draft(url="https://source-1.test/page", authority=0.5, recency=0.5, relevance=0.5),
    ])
    last = SourceScoresDraft(sources=[
        _draft(url="https://source-4.test/page", authority=0.2, recency=0.2, relevance=0.2),
    ])
    completer = ScriptedCompleter(outputs=[first, _output_limit_error(), last])
    agent = _evaluator(
        tracker, completer, batch_size=2, max_total_sources=5,
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0, source_scoring_concurrency=1),
    )
    task, _, _ = await agent.lookup_reputations(agent.build_task(_eval_state(findings)))
    seen: list[dict] = []

    await agent.score_sources(task, on_progress=seen.append)

    base = {"to_rate": 5, "reused": 0, "capped": 0, "batches": 3}
    assert seen == [
        {**base, "rated": 0, "strong": 0, "fair": 0, "weak": 0, "unrated": 0, "batches_done": 0},
        {**base, "rated": 2, "strong": 1, "fair": 1, "weak": 0, "unrated": 0, "batches_done": 1},
        {**base, "rated": 2, "strong": 1, "fair": 1, "weak": 0, "unrated": 2, "batches_done": 2},
        {**base, "rated": 3, "strong": 1, "fair": 1, "weak": 1, "unrated": 2, "batches_done": 3},
    ]
    assert [(m["rated"] + m["unrated"]) / m["to_rate"] for m in seen] == [0.0, 0.4, 0.8, 1.0]
    assert all(m["strong"] + m["fair"] + m["weak"] == m["rated"] for m in seen)


@pytest.mark.asyncio
async def test_evaluator_progress_counts_reused_and_capped_sources_apart(tracker: Tracker) -> None:
    """§6.1: a reused assessment and a capped source are never rated in this pass."""
    findings = [_eval_finding(f"https://source-{index}.test/page") for index in range(3)]
    prior = _strength_source("https://source-0.test/page", 0.9)
    completer = ScriptedCompleter(outputs=[SourceScoresDraft(sources=[
        _draft(url="https://source-1.test/page"),
    ])])
    agent = _evaluator(tracker, completer, batch_size=2, max_total_sources=1)
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "q"):
        with bind_live_sink(received.append):
            await agent.run(_eval_state(findings, evaluated_sources=[prior]))

    progress = [e.metadata for e in received if e.event_type == "source_evaluator.progress"]
    assert progress[0] == {
        "to_rate": 1, "reused": 1, "capped": 1, "rated": 0, "strong": 0, "fair": 0,
        "weak": 0, "unrated": 0, "batches": 1, "batches_done": 0,
    }
    assert progress[-1]["rated"] == 1 and progress[-1]["batches_done"] == 1


def test_evaluation_progress_counts_a_source_once() -> None:
    """Review M13: a source reported twice -- a batch's halves reported on their own,
    or one report repeated -- is counted once."""
    from deep_research.agents.source_evaluator import _EvaluationProgress

    progress = _EvaluationProgress(to_rate=2, reused=0, capped=0, batches=1)
    strong = _strength_source("https://a.test/1", 0.9)
    failed = _strength_source("https://a.test/2", None, status="unscored_provider")
    progress.record([strong])
    progress.record([strong, failed])
    progress.record([failed])

    assert progress.metadata()["rated"] == 1
    assert progress.metadata()["strong"] == 1
    assert progress.metadata()["unrated"] == 1


def test_evaluation_completed_carries_the_split() -> None:
    """§6.1: the completed event counts strong, fair and weak over the snapshot's
    scored sources; an unscored source is in none of them."""
    sources = [
        _strength_source("https://a.test/1", 0.9),
        _strength_source("https://a.test/2", 0.5),
        _strength_source("https://a.test/3", 0.1),
        _strength_source("https://a.test/4", None, status="unscored_cap"),
    ]
    metadata = evaluation_completed_event(sources, reputation_hits=0, reputation_failures=0).metadata

    assert (metadata["strong_count"], metadata["fair_count"], metadata["weak_count"]) == (1, 1, 1)
    assert metadata["low_confidence_count"] == metadata["weak_count"]
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest "tests/test_agents/test_source_evaluator.py::test_the_evaluation_events_are_published_live" "tests/test_agents/test_source_evaluator.py::test_source_strength_split" "tests/test_agents/test_source_evaluator.py::test_evaluator_progress_split" "tests/test_agents/test_source_evaluator.py::test_evaluator_progress_counts_reused_and_capped_sources_apart" "tests/test_agents/test_source_evaluator.py::test_evaluation_progress_counts_a_source_once" "tests/test_agents/test_source_evaluator.py::test_evaluation_completed_carries_the_split" -q 2>&1 | tail -3
```

Expected: `ERROR tests/test_agents/test_source_evaluator.py` (`ImportError: cannot import name 'STRONG_SOURCE_THRESHOLD'`), `1 error`.

- [ ] **Step 3: Write the implementation**

`src/deep_research/agents/source_evaluator.py` — replace

```python
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple, Protocol

from pydantic import Field

```

with

```python
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal, NamedTuple, Protocol, TypeAlias

from pydantic import Field, JsonValue

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
LOW_CONFIDENCE_THRESHOLD = 0.4
DEFAULT_BATCH_SIZE = 12

```

with

```python
LOW_CONFIDENCE_THRESHOLD = 0.4
# notes-progress-report spec §6.2: Evaluating's split on ``overall_score``. Strong
# at 0.70 and above, weak below ``LOW_CONFIDENCE_THRESHOLD`` (exactly today's
# ``low_confidence``), fair in between.
STRONG_SOURCE_THRESHOLD = 0.70
DEFAULT_BATCH_SIZE = 12

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
def evaluation_status_counts(
    sources: Sequence[ScoredSource],
) -> dict[str, int]:
```

with

```python
SourceStrength: TypeAlias = Literal["strong", "fair", "weak"]


def source_strength(source: ScoredSource) -> SourceStrength | None:
    """Strong, fair or weak (notes-progress-report spec §6.2); ``None`` unscored."""
    if source.evaluation_status != "scored" or source.overall_score is None:
        return None
    if source.overall_score >= STRONG_SOURCE_THRESHOLD:
        return "strong"
    if source.overall_score >= LOW_CONFIDENCE_THRESHOLD:
        return "fair"
    return "weak"


def strength_counts(sources: Sequence[ScoredSource]) -> dict[str, int]:
    """How many scored sources are strong, fair and weak (spec §6.1)."""
    strengths = [source_strength(source) for source in sources]
    return {
        "strong_count": strengths.count("strong"),
        "fair_count": strengths.count("fair"),
        "weak_count": strengths.count("weak"),
    }


class _EvaluationProgress:
    """One scoring pass's running counts (notes-progress-report spec §6.1, §6.2).

    Each source is counted once, the first time a settled batch reports it, so
    a total never depends on how many events arrive or how sources were
    batched (review M13). ``rated``/``unrated`` hold urls; ``rated`` maps each
    scored url to its strength.
    """

    def __init__(self, *, to_rate: int, reused: int, capped: int, batches: int) -> None:
        self.to_rate = to_rate
        self.reused = reused
        self.capped = capped
        self.batches = batches
        self.batches_done = 0
        self.rated: dict[str, SourceStrength] = {}
        self.unrated: set[str] = set()

    def record(self, sources: Iterable[ScoredSource]) -> None:
        for source in sources:
            if source.url in self.rated or source.url in self.unrated:
                continue
            strength = source_strength(source)
            if strength is None:
                self.unrated.add(source.url)
            else:
                self.rated[source.url] = strength

    def metadata(self) -> dict[str, JsonValue]:
        strengths = list(self.rated.values())
        return {
            "to_rate": self.to_rate,
            "reused": self.reused,
            "capped": self.capped,
            "rated": len(self.rated),
            "strong": strengths.count("strong"),
            "fair": strengths.count("fair"),
            "weak": strengths.count("weak"),
            "unrated": len(self.unrated),
            "batches": self.batches,
            "batches_done": self.batches_done,
        }


def evaluation_progress_event(metadata: Mapping[str, JsonValue]) -> ResearchEvent:
    """One ``source_evaluator.progress`` event (spec §4 item 1, §6.1): live-only."""
    return agent_event(
        agent_name=SOURCE_EVALUATOR_NAME,
        event_type="source_evaluator.progress",
        message="Source evaluation progress.",
        metadata=metadata,
    )


def evaluation_status_counts(
    sources: Sequence[ScoredSource],
) -> dict[str, int]:
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
            "low_confidence_count": low_confidence_count(sources),
            "unique_source_count": len(sources),
            **status_counts,
            "reputation_hits": reputation_hits,
            "reputation_failures": reputation_failures,
        },
    )

```

with

```python
            "low_confidence_count": low_confidence_count(sources),
            "unique_source_count": len(sources),
            **status_counts,
            **strength_counts(sources),
            "reputation_hits": reputation_hits,
            "reputation_failures": reputation_failures,
        },
    )

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
    async def score_sources(
        self,
        task: SourceEvaluationTask,
    ) -> tuple[list[ScoredSource], list[ResearchError], bool]:
```

with

```python
    async def score_sources(
        self,
        task: SourceEvaluationTask,
        *,
        on_progress: Callable[[dict[str, JsonValue]], None] | None = None,
    ) -> tuple[list[ScoredSource], list[ResearchError], bool]:
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
        strength of its failure. The returned snapshot is assembled in
        ``task.groups`` order whatever order the batches finished in.
        """
        if not task.groups:
            return [], [], False

```

with

```python
        strength of its failure. The returned snapshot is assembled in
        ``task.groups`` order whatever order the batches finished in.

        ``on_progress`` (notes-progress-report spec §6.2) is called once the
        batches are planned, before the first scoring call -- with nothing to
        score, once with every count 0 -- then once each batch settles, scored
        or failed, with cumulative counts in completion order.
        """
        if not task.groups:
            if on_progress is not None:
                on_progress(
                    _EvaluationProgress(to_rate=0, reused=0, capped=0, batches=0).metadata()
                )
            return [], [], False

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
        batches = [
            groups_to_score[start : start + self._batch_size]
            for start in range(0, len(groups_to_score), self._batch_size)
        ]

        errors: list[ResearchError] = []
```

with

```python
        batches = [
            groups_to_score[start : start + self._batch_size]
            for start in range(0, len(groups_to_score), self._batch_size)
        ]
        progress = _EvaluationProgress(
            to_rate=len(groups_to_score),
            reused=len(task.groups) - len(eligible_groups),
            capped=len(capped),
            batches=len(batches),
        )
        if on_progress is not None:
            on_progress(progress.metadata())

        errors: list[ResearchError] = []
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
        async def score_one(position: int, batch: list[SourceGroup]) -> None:
            """Score one batch, marking its own sources when it fails."""
            nonlocal provider_failed

```

with

```python
        def settled_batch(batch: list[SourceGroup]) -> None:
            """Count one settled batch's sources and report the running totals."""
            progress.batches_done += 1
            progress.record(assessed[group.url] for group in batch if group.url in assessed)
            if on_progress is not None:
                on_progress(progress.metadata())

        async def score_one(position: int, batch: list[SourceGroup]) -> None:
            """Score one batch, marking its own sources when it fails."""
            nonlocal provider_failed

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
                for group in batch:
                    assessed[group.url] = fallback_scored_source(
                        group,
                        reason="unscored_provider",
                        dossier=task.dossiers.get(group.url),
                    )
                return

```

with

```python
                for group in batch:
                    assessed[group.url] = fallback_scored_source(
                        group,
                        reason="unscored_provider",
                        dossier=task.dossiers.get(group.url),
                    )
                settled_batch(batch)
                return

```

`src/deep_research/agents/source_evaluator.py` — replace

```python
                else:
                    assessed[group.url] = build_scored_source(
                        group,
                        draft,
                        reputation=task.reputations.get(group.url),
                        dossier=task.dossiers.get(group.url),
                    )

        settled = await asyncio.gather(
```

with

```python
                else:
                    assessed[group.url] = build_scored_source(
                        group,
                        draft,
                        reputation=task.reputations.get(group.url),
                        dossier=task.dossiers.get(group.url),
                    )
            settled_batch(batch)

        settled = await asyncio.gather(
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
            sources, scoring_errors, provider_failed = await self.score_sources(
                task
            )
```

with

```python
            sources, scoring_errors, provider_failed = await self.score_sources(
                task,
                # notes-progress-report spec §6.2: each count, live-only.
                on_progress=lambda metadata: publish_live(
                    evaluation_progress_event(metadata)
                ),
            )
```


`src/deep_research/agents/__init__.py` — replace

```python
    evaluation_completed_event,

```

with

```python
    evaluation_completed_event,
    evaluation_progress_event,
    STRONG_SOURCE_THRESHOLD,
    SourceStrength,
    source_strength,
    strength_counts,

```

`src/deep_research/agents/__init__.py` — replace

```python
    "evaluation_completed_event",

```

with

```python
    "evaluation_completed_event",
    "evaluation_progress_event",
    "STRONG_SOURCE_THRESHOLD",
    "SourceStrength",
    "source_strength",
    "strength_counts",

```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's command again. Expected: `6 passed`.

- [ ] **Step 5: Re-pin the source evaluator**

Run Task 2 Step 5's snippet with the arguments `source_evaluator "Phase B (notes-progress-report spec §6.2, §6.4, 2026-09-30): live source_evaluator.progress once the batches are planned and as each settles, and the strong/fair/weak split on evaluation.completed. No request text changed."` in place of the planner's, between the same two fingerprint runs.

Expected: `1 failed, 10 passed, 67 deselected`; `source_evaluator: <the Task 1 value> -> <a new value>` (planning, on D + A + C as on D + A: `cc5a310b0aa0 -> 356d1486f0e3`); `11 passed, 67 deselected`.

- [ ] **Step 6: Run the regression files**

```bash
.venv/Scripts/python.exe -m pytest tests/test_agents/test_source_evaluator.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: no failure (planning: `120 passed`).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py src/deep_research/agents/__init__.py tests/test_agents/test_source_evaluator.py tests/test_evaluation/test_config.py
git commit -m "feat(progress): Evaluating's live progress and the strong/fair/weak split"
```

---

### Task 4: Verifying's progress, samples and the Statement Check's batch hook (spec §6.1, §6.2, §6.5; AC13, AC16)

**Files:**
- Modify: `src/deep_research/agents/evidence_verifier.py` (imports, the sample helpers after `verify_finding`, `_VerifyProgress`, `verification_progress_event`, `verify`, `run`, `check_statements`), `src/deep_research/agents/__init__.py`
- Test: `tests/test_agents/test_evidence_verifier.py`; `tests/test_evaluation/test_config.py` (the pin)

**Interfaces:**
- Consumes: today's `verify_finding(item, reply)` (pure), `normalize_source_url`, `summarize_text`, `FindingStatus`, `publish_live`.
- Produces (exported): `verification_sample(judged: Sequence[tuple[Finding, FindingVerification]], sources) -> dict[str, JsonValue] | None`; `verification_progress_event(metadata) -> ResearchEvent`; `verify(..., *, on_progress: Callable[[dict[str, JsonValue]], None] | None = None)`; `check_statements(..., on_batch: Callable[[Sequence[StatementCheckItem], Mapping[str, StatementVerdictDraft | None]], None] | None = None)`. Task 5's writer passes `on_batch`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_evidence_verifier.py` — replace

```python
    [event] = outcome.state_update["events"]
    assert event.event_type == "evidence_verifier.verification.completed"
    assert [e.event_id for e in received] == [event.event_id]

```

with

```python
    [event] = outcome.state_update["events"]
    assert event.event_type == "evidence_verifier.verification.completed"
    # notes-progress-report spec §4 item 1: the progress events are live-only.
    assert [e.event_id for e in received if e.event_type != "evidence_verifier.progress"] == [event.event_id]
    assert [e.event_type for e in received].count("evidence_verifier.progress") == 1

```

`tests/test_agents/test_evidence_verifier.py` — replace

```python
    statement_check_messages,
    unchecked_context,
    verify_finding,
)
```

with

```python
    statement_check_messages,
    unchecked_context,
    verification_sample,
    verify_finding,
)
```

Append to `tests/test_agents/test_evidence_verifier.py`:

```python


# --- notes-progress-report spec §6.1, §6.2, §6.5: Verifying's live progress --------

SECRET_REASON = "SECRET-CONTEXT-REASON"


def _secret_confirm_reply(messages: list, schema: type) -> ContextCheckDraft:
    """``_confirm_reply``, with a reason no progress event may ever carry."""
    draft = _confirm_reply(messages, schema)
    return ContextCheckDraft(figures=[
        figure_reply.model_copy(update={"reason": SECRET_REASON}) for figure_reply in draft.figures
    ])


def _four_findings():
    """A: figures, on the page (Context Check); B: snippet not on the page; C: no
    figures, on the page (quoted); D: its read is missing."""
    read = make_read()
    a = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")], content="Finding A")
    b = make_finding(read, "A sentence the page never prints.", figures=[figure("3", "GW")], content="Finding B")
    c = make_finding(read, "U.S. battery capacity increased 66% in 2024.", content="Finding C")
    elsewhere = make_read("Another page entirely.", url="https://other.test/x", title="Other")
    d = make_finding(elsewhere, "Another page entirely.", figures=[figure("1", "GW")], content="Finding D")
    return read, [a, b, c, d]


@pytest.mark.asyncio
async def test_verifier_first_event_counts_figure_match(tracker: Tracker) -> None:
    """AC16 (review M6): the first event arrives before any Context Check call, and its
    ``checked`` already counts every finding Figure Match decided."""
    read, findings = _four_findings()
    completer = ScriptedCompleter(outputs=[_secret_confirm_reply])
    agent = _evidence_verifier(tracker, completer)
    calls_at_event: list[int] = []
    seen: list[dict] = []

    def on_progress(metadata: dict) -> None:
        calls_at_event.append(len(completer.calls))
        seen.append(metadata)

    await agent.verify(findings, {read.read_id: read}, [], on_progress=on_progress)

    assert calls_at_event == [0, 1]
    first, last = seen
    assert {k: v for k, v in first.items() if k != "sample"} == {
        "total": 4, "checked": 3, "verified": 0, "corrected": 0, "quoted": 1,
        "dropped": 2, "batches": 1, "batches_done": 0,
    }
    assert first["sample"] == {
        "text": "Finding B", "verdict": "dropped", "correction": None,
        "drop_reason": "snippet_not_on_page", "source": {"role": None, "host": "eia.gov"},
    }
    assert {k: v for k, v in last.items() if k != "sample"} == {
        "total": 4, "checked": 4, "verified": 1, "corrected": 0, "quoted": 1,
        "dropped": 2, "batches": 1, "batches_done": 1,
    }
    assert last["sample"]["text"] == "Finding A" and last["sample"]["verdict"] == "verified"
    assert all(SECRET_REASON not in json.dumps(metadata) for metadata in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [2, 5])
async def test_verifier_tally_ends_on_the_completed_counts(tracker: Tracker, batch_size: int) -> None:
    """AC16: whatever the batch size, the last tally equals the completed event's
    counts, and the sample is never the Context Check's reason text."""
    read = make_read(_metrics_page(6), url="https://example.test/batch", title="Batch metrics")
    findings = [_metric_finding(read, i) for i in range(6)]
    completer = ScriptedCompleter(outputs=[_secret_confirm_reply] * 3)
    agent = _evidence_verifier(
        tracker, completer, config=AgentRuntimeConfig(verifier_batch_size=batch_size),
    )
    state = _state(raw_findings=findings, read_records={read.read_id: read})
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "question"):
        with bind_live_sink(received.append):
            outcome = await agent.run(state)

    progress = [e.metadata for e in received if e.event_type == "evidence_verifier.progress"]
    [completed] = outcome.state_update["events"]
    assert len(progress) == 1 + -(-6 // batch_size)
    assert progress[-1]["checked"] == progress[-1]["total"] == 6
    assert (progress[-1]["verified"], progress[-1]["corrected"], progress[-1]["quoted"], progress[-1]["dropped"]) == (
        completed.metadata["verified"], completed.metadata["verified_corrected"],
        completed.metadata["quoted"], completed.metadata["dropped"],
    )
    assert [m["checked"] for m in progress] == sorted(m["checked"] for m in progress)
    assert all(SECRET_REASON not in json.dumps(m) for m in progress)


def test_progress_counts_idempotent() -> None:
    """AC16 (review M13): a batch reported as two halves, or twice, counts each finding once."""
    from deep_research.agents.evidence_verifier import _VerifyProgress

    read, findings = _four_findings()
    verified = FindingVerification(status="verified", figure_results=[])
    progress = _VerifyProgress(findings, batches=1, sources=[])
    progress.record([(findings[0], verified), (findings[2], verified)])
    progress.record([(findings[0], verified)])
    progress.record([(findings[2], verified), (findings[0], verified)])

    assert progress.metadata(None)["checked"] == 2
    assert progress.metadata(None)["verified"] == 2


def _figure_result(figure_, *, period=None, scope=None, subject=None, kind="actual", corrected=True, dropped=None):
    if dropped is not None:
        return FigureResult(figure=figure_, matched=True, dropped_reason=dropped)
    return FigureResult(
        figure=figure_, matched=True, corrected=corrected,
        context=FigureContext(period=period, scope=scope, subject=subject, attribution="own",
                              organisation="EIA", kind=kind),
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_figure_result(figure("10.4", "GW", "2024", "actual"), period="2025"), {"field": "period", "value": "2025"}),
        (_figure_result(figure("10.4", "GW", "2024", "actual"), period=None), {"field": "period_cleared", "value": None}),
        (_figure_result(figure("10.4", "GW", "2024", "actual"), period="2024", scope="all segments"),
         {"field": "scope", "value": "all segments"}),
        (_figure_result(figure("10.4", "GW", "2024", "actual"), period="2024", subject="Model A"),
         {"field": "subject", "value": "Model A"}),
        (_figure_result(figure("10.4", "GW", "2024", "forecast"), period="2024", kind="actual"),
         {"field": "kind", "value": "actual"}),
        (_figure_result(figure("10.4", "GW", "2024", "actual"), dropped="evidence_not_on_page"),
         {"field": "figure", "value": None}),
    ],
)
def test_verifier_progress_samples(result: FigureResult, expected: dict) -> None:
    """§6.1: a corrected sample names what the page changed, in the kept context's own
    words; the host is the page's, without ``www.``; the role is the evaluated one."""
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[result.figure], content="Finding A")
    kept = _figure_result(figure("19.6", "GW", "2025", "forecast"), period="2025", kind="forecast", corrected=False)
    verification = FindingVerification(status="verified_corrected", figure_results=[result, kept])
    source = ScoredSource(
        url=finding.source_url, title="EIA", rationale="r", evaluation_status="unscored_missing",
        source_role="original_report",
    )

    sample = verification_sample([(finding, verification)], {"https://eia.gov/todayinenergy/detail.php?id=64705": source})

    assert sample == {
        "text": "Finding A", "verdict": "verified_corrected", "correction": expected,
        "drop_reason": None, "source": {"role": "original_report", "host": "eia.gov"},
    }


def test_a_sample_prefers_a_correction_then_a_drop_and_names_the_first_figures_reason() -> None:
    """§6.2: verified_corrected > dropped > verified > quoted, first in report order; a
    finding whose every figure dropped names its first figure's reason."""
    read = make_read()
    quoted = make_finding(read, SNIPPET, content="Quoted")
    dropped = make_finding(read, SNIPPET, figures=[figure("1", "GW")], content="Dropped")
    all_dropped = FindingVerification(
        status="dropped", dropped_reason="all_figures_dropped",
        figure_results=[_figure_result(figure("1", "GW"), dropped="context_rejected")],
    )
    judged = [(quoted, FindingVerification(status="quoted")), (dropped, all_dropped)]

    sample = verification_sample(judged, {})

    assert sample["text"] == "Dropped"
    assert sample["drop_reason"] == "context_rejected"
    assert verification_sample([], {}) is None


@pytest.mark.asyncio
async def test_statement_check_reports_each_settled_batch() -> None:
    """§6.2: ``on_batch`` is called once per settled batch with its items and verdicts."""
    finding = _statement_finding("18.9", "GW")
    items = [_statement_item(f"S{i:02d}", f"Wood Mackenzie states {i} GW.", finding) for i in range(12)]
    completer = ScriptedCompleter(outputs=[_confirm_statement_reply] * 3)
    reports: list[tuple[list[str], list[str]]] = []

    results, errors = await check_statements(
        completer, items, question="How much storage?", batch_size=5,
        on_batch=lambda batch, verdicts: reports.append(
            ([item.label for item in batch], sorted(verdicts))
        ),
    )

    assert errors == []
    assert sorted(len(labels) for labels, _ in reports) == [2, 5, 5]
    assert all(labels == keys for labels, keys in ((sorted(batch_labels), keys_) for batch_labels, keys_ in reports))
    assert sorted(label for labels, _ in reports for label in labels) == sorted(results)
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest "tests/test_agents/test_evidence_verifier.py::test_the_verification_completed_event_is_published_live" "tests/test_agents/test_evidence_verifier.py::test_verifier_first_event_counts_figure_match" "tests/test_agents/test_evidence_verifier.py::test_verifier_tally_ends_on_the_completed_counts" "tests/test_agents/test_evidence_verifier.py::test_progress_counts_idempotent" "tests/test_agents/test_evidence_verifier.py::test_verifier_progress_samples" "tests/test_agents/test_evidence_verifier.py::test_a_sample_prefers_a_correction_then_a_drop_and_names_the_first_figures_reason" "tests/test_agents/test_evidence_verifier.py::test_statement_check_reports_each_settled_batch" -q 2>&1 | tail -3
```

Expected: `ERROR tests/test_agents/test_evidence_verifier.py` (`ImportError: cannot import name 'verification_sample'`), `1 error`.

- [ ] **Step 3: Write the implementation**

`src/deep_research/agents/evidence_verifier.py` — replace

```python
import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import Field, ValidationError

```

with

```python
import asyncio
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, ValidationError

```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
from deep_research.agents.sources import publisher_identity
from deep_research.agents.steps import ReActRun

```

with

```python
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import ReActRun, summarize_text

```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
    FindingFigure,
    FindingVerification,
    ReadRecord,
```

with

```python
    FindingFigure,
    FindingStatus,
    FindingVerification,
    ReadRecord,
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
        figure_results=results, context_unchecked=unchecked,
    )


def context_check_messages(items: Sequence[ContextItem]) -> list[ChatMessage]:
```

with

```python
        figure_results=results, context_unchecked=unchecked,
    )


# --- notes-progress-report spec §6.1, §6.2, §6.5: Verifying's live progress ----

#: The sample a report shows (spec §6.2): the first finding, in report order, of
#: the first of these statuses the report holds.
_SAMPLE_ORDER: tuple[FindingStatus, ...] = ("verified_corrected", "dropped", "verified", "quoted")


def _correction(finding: Finding, verification: FindingVerification) -> dict[str, JsonValue] | None:
    """What the check changed, from the first figure it corrected or dropped (spec §6.1).

    ``field`` is ``period``, ``period_cleared``, ``scope``, ``subject``, ``kind``
    or ``figure`` (a figure the page did not carry); ``value`` is the kept
    context's own words, at most 60 characters, or ``None``. A figure that was
    only filled in (a period the extraction left empty) is not a correction.
    """
    for result in verification.figure_results:
        if not result.kept:
            return {"field": "figure", "value": None}
        context = result.context
        if not result.corrected or context is None:
            continue
        recorded_period = result.figure.period or finding.data_period
        if recorded_period is not None and context.period != recorded_period:
            if context.period is None:
                return {"field": "period_cleared", "value": None}
            return {"field": "period", "value": summarize_text(context.period, limit=60)}
        if context.scope is not None and context.scope != finding.measure_scope:
            return {"field": "scope", "value": summarize_text(context.scope, limit=60)}
        if context.subject is not None and context.subject != result.figure.subject:
            return {"field": "subject", "value": summarize_text(context.subject, limit=60)}
        if result.figure.kind is not None and context.kind != result.figure.kind:
            return {"field": "kind", "value": context.kind}
    return None


def _drop_reason(verification: FindingVerification) -> str | None:
    """The finding's own drop reason, or its first figure's when all figures dropped."""
    if verification.dropped_reason not in (None, "all_figures_dropped"):
        return verification.dropped_reason
    return next(
        (result.dropped_reason for result in verification.figure_results if result.dropped_reason),
        None,
    )


def _sample_host(url: str) -> str | None:
    """The page's host, without ``www.``; never a path or a query (spec §4 item 1)."""
    try:
        return urlsplit(normalize_source_url(url)).hostname or None
    except ValueError:
        return None


def verification_sample(
    judged: Sequence[tuple[Finding, FindingVerification]],
    sources: Mapping[str, ScoredSource],
) -> dict[str, JsonValue] | None:
    """Verifying's ticker sample for one report (spec §6.1, §6.2), or ``None``.

    ``sources`` maps a normalized source url to its assessment. The text is the
    finding's content, at most 160 characters; the verdict is the finding's
    status; never the Context Check's reason text.
    """
    for status in _SAMPLE_ORDER:
        for finding, verification in judged:
            if verification.status != status:
                continue
            source = sources.get(normalize_source_url(finding.source_url))
            return {
                "text": summarize_text(finding.content, limit=160),
                "verdict": status,
                "correction": _correction(finding, verification)
                if status == "verified_corrected" else None,
                "drop_reason": _drop_reason(verification) if status == "dropped" else None,
                "source": {
                    "role": source.source_role if source is not None else None,
                    "host": _sample_host(finding.source_url),
                },
            }
    return None


class _VerifyProgress:
    """One verification pass's running tally (spec §6.1).

    ``total`` is the findings this pass judges; a finding is counted once, the
    first time a report names it -- the Figure Match pass or a settled batch --
    so the tally never depends on how many events arrive or how findings were
    batched (review M13).
    """

    def __init__(
        self, findings: Sequence[Finding], *, batches: int, sources: Sequence[ScoredSource]
    ) -> None:
        self.total = len({finding_fingerprint(finding) for finding in findings})
        self.batches = batches
        self.batches_done = 0
        self.sources = {normalize_source_url(source.url): source for source in sources}
        self.statuses: dict[str, FindingStatus] = {}

    def record(
        self, judged: Sequence[tuple[Finding, FindingVerification]]
    ) -> dict[str, JsonValue] | None:
        """Count the findings this report names for the first time; return its sample."""
        new: list[tuple[Finding, FindingVerification]] = []
        for finding, verification in judged:
            key = finding_fingerprint(finding)
            if key in self.statuses:
                continue
            self.statuses[key] = verification.status
            new.append((finding, verification))
        return verification_sample(new, self.sources)

    def metadata(self, sample: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
        counts = Counter(self.statuses.values())
        return {
            "total": self.total,
            "checked": len(self.statuses),
            "verified": counts["verified"],
            "corrected": counts["verified_corrected"],
            "quoted": counts["quoted"],
            "dropped": counts["dropped"],
            "batches": self.batches,
            "batches_done": self.batches_done,
            "sample": sample,
        }


def verification_progress_event(metadata: Mapping[str, JsonValue]) -> ResearchEvent:
    """One ``evidence_verifier.progress`` event (spec §4 item 1, §6.1): live-only."""
    return agent_event(
        agent_name=EVIDENCE_VERIFIER_NAME,
        event_type="evidence_verifier.progress",
        message="Verification progress.",
        metadata=metadata,
    )


def context_check_messages(items: Sequence[ContextItem]) -> list[ChatMessage]:
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
            judged = await self.verify(pending, state.read_records, errors, state.evaluated_sources)
```

with

```python
            judged = await self.verify(
                pending, state.read_records, errors, state.evaluated_sources,
                # notes-progress-report spec §6.2: each tally, live-only.
                on_progress=lambda metadata: publish_live(verification_progress_event(metadata)),
            )
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
    async def verify(self, findings: Sequence[Finding], reads: Mapping[str, ReadRecord],
                     errors: list[ResearchError], sources: Sequence[ScoredSource] = ()) -> list[Finding]:
        results: dict[str, FindingVerification] = {}
```

with

```python
    async def verify(self, findings: Sequence[Finding], reads: Mapping[str, ReadRecord],
                     errors: list[ResearchError], sources: Sequence[ScoredSource] = (), *,
                     on_progress: Callable[[dict[str, JsonValue]], None] | None = None) -> list[Finding]:
        """Figure Match every finding, then Context Check the figure-bearing ones.

        ``on_progress`` (notes-progress-report spec §6.2) is called once after
        the Figure Match pass, before the first Context Check -- its count
        already holds the findings Figure Match decided -- then once each batch
        settles, with the running tally and that report's sample.
        """
        results: dict[str, FindingVerification] = {}
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
        gate = asyncio.Semaphore(self.config.verifier_concurrency)

        async def one(batch: list[ContextItem]) -> dict[str, dict[int, FigureCheckDraft] | None]:
            async with gate:
                return await self._check(batch, errors, split=True)

```

with

```python
        gate = asyncio.Semaphore(self.config.verifier_concurrency)
        progress = _VerifyProgress(findings, batches=len(batches), sources=sources)
        if on_progress is not None:
            decided = [
                (finding, results[finding_fingerprint(finding)])
                for finding in findings
                if finding_fingerprint(finding) in results
            ]
            on_progress(progress.metadata(progress.record(decided)))

        async def one(batch: list[ContextItem]) -> dict[str, dict[int, FigureCheckDraft] | None]:
            async with gate:
                replies = await self._check(batch, errors, split=True)
            if on_progress is not None:
                progress.batches_done += 1
                judged = [
                    (item.finding, verify_finding(item, replies[finding_fingerprint(item.finding)]))
                    for item in batch
                    if finding_fingerprint(item.finding) in replies
                ]
                on_progress(progress.metadata(progress.record(judged)))
            return replies

```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
    concurrency: int = CONTEXT_CHECK_CONCURRENCY,
    gate: asyncio.Semaphore | None = None,
) -> tuple[dict[str, StatementVerdictDraft | None], list[ResearchError]]:
```

with

```python
    concurrency: int = CONTEXT_CHECK_CONCURRENCY,
    gate: asyncio.Semaphore | None = None,
    on_batch: Callable[
        [Sequence[StatementCheckItem], Mapping[str, StatementVerdictDraft | None]], None
    ] | None = None,
) -> tuple[dict[str, StatementVerdictDraft | None], list[ResearchError]]:
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
    so the whole pass never runs more than ``verifier_concurrency`` checks at
    once. ``None`` (every other caller) keeps today's behaviour: a private
    semaphore scoped to this one call, sized from ``concurrency``.
    """
```

with

```python
    so the whole pass never runs more than ``verifier_concurrency`` checks at
    once. ``None`` (every other caller) keeps today's behaviour: a private
    semaphore scoped to this one call, sized from ``concurrency``.

    ``on_batch`` (notes-progress-report spec §6.2) is called once each batch
    settles -- its re-asked halves included -- with that batch's items and
    verdicts, so the Report Writer can count its sentences as they are judged.
    """
```

`src/deep_research/agents/evidence_verifier.py` — replace

```python
    async def one(batch: Sequence[StatementCheckItem]) -> dict[str, StatementVerdictDraft | None]:
        async with gate:
            return await _check_statement_batch(provider, batch, question, errors, fingerprint, split=True)

```

with

```python
    async def one(batch: Sequence[StatementCheckItem]) -> dict[str, StatementVerdictDraft | None]:
        async with gate:
            verdicts = await _check_statement_batch(
                provider, batch, question, errors, fingerprint, split=True
            )
        if on_batch is not None:
            on_batch(batch, verdicts)
        return verdicts

```


`src/deep_research/agents/__init__.py` — replace

```python
    evidence_verified_event,
    STATEMENT_CHECK_SYSTEM_PROMPT,

```

with

```python
    evidence_verified_event,
    verification_progress_event,
    verification_sample,
    STATEMENT_CHECK_SYSTEM_PROMPT,

```

`src/deep_research/agents/__init__.py` — replace

```python
    "evidence_verified_event",

```

with

```python
    "evidence_verified_event",
    "verification_progress_event",
    "verification_sample",

```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's command again. Expected: `13 passed` (the tally test at batch sizes 2 and 5, six samples).

- [ ] **Step 5: Re-pin the evidence verifier**

Run Task 2 Step 5's snippet with `evidence_verifier "Phase B (notes-progress-report spec §6.2, §6.5, 2026-09-30): live evidence_verifier.progress after Figure Match and as each Context Check batch settles, and check_statements' on_batch. No request text changed; LB-D10 holds."`.

Expected: `1 failed, 10 passed, 67 deselected`; `evidence_verifier: <the Task 1 value> -> <a new value>` (planning, on D + A + C as on D + A: `4a3d56fab932 -> 2c360ecb7315`); `11 passed, 67 deselected`.

- [ ] **Step 6: Run the regression files**

```bash
.venv/Scripts/python.exe -m pytest tests/test_agents/test_evidence_verifier.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: no failure (planning: `153 passed`).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/agents/evidence_verifier.py src/deep_research/agents/__init__.py tests/test_agents/test_evidence_verifier.py tests/test_evaluation/test_config.py
git commit -m "feat(progress): Verifying's live progress, its samples and the Statement Check's batch hook"
```

---

### Task 5: Writing's progress (spec §6.1, §6.2, §6.6; AC13, AC17)

**Files:**
- Modify: `src/deep_research/agents/report_writer.py` (imports, the progress block before `_answer_form_line`, `_check`, `_attempt_bottom_line_draft`, `_run_part`, `compose_written_report`), `src/deep_research/agents/__init__.py`
- Test: `tests/test_agents/test_report_writer.py` (three substitutes, the live test, three new tests), `tests/test_agents/test_report_reviewer.py` (two substitutes); `tests/test_evaluation/test_config.py` (the pin); Phase C's `tests/test_agents/test_report_bottom_line.py` (regression only: its `checker` fixture installs `test_report_writer.py`'s `_FakeChecker`)

**Interfaces:**
- Consumes: Task 4's `check_statements(..., on_batch=)`; today's `PartJob`, `_section_title`, `summarize_text`, `publish_live`.
- Produces: `writing_progress_event(metadata) -> ResearchEvent` (exported); private `_WritingProgress(parts_total)` (`publish`, `part_returned`, `bottom_line_started`, `bottom_line_drafted`, `count`, `reporter`, `settle`), `_WRITING_PROGRESS: ContextVar[_WritingProgress | None]`, `_check(..., part: tuple[str, str] | None = None)`. The five `check_statements` substitutes spec §3.6 names (three in `test_report_writer.py`, two in `test_report_reviewer.py`) accept `on_batch=None` (§11.2, review I3; R8).

- [ ] **Step 1: Write the failing tests**

First list every substitute of `check_statements` the tests install (spec §12 R8; review 1, P2-2):

```bash
git grep -n -E "async def (fake|flaky)_check|def __call__\(self, provider|evidence_verifier\.check_statements[^_]|ev\.check_statements = " -- tests
```

Expected: these twelve lines. Planning saw the line numbers on D + A + C; a different line number is no problem.
- `tests/test_agents/test_report_bottom_line.py:71`: Phase C's `checker` fixture, which installs `_FakeChecker`.
- `tests/test_agents/test_report_reviewer.py:948` and `:2778`: the two `consistent` substitutes.
- `tests/test_agents/test_report_writer.py:1117`, `:1132` and `:1156`: `_FakeChecker`'s docstring, its `__call__` and its fixture.
- `tests/test_agents/test_report_writer.py:2685`, `:2689` and `:2696`: `fake_check`.
- `tests/test_agents/test_report_writer.py:3276`, `:3288` and `:3294`: `flaky_check`.

They are the five substitutes this step changes. If a line names any other substitute, stop and report it: it needs `on_batch=None` too, as a plan amendment.

`tests/test_agents/test_report_writer.py` — replace

```python
    async def __call__(self, provider, items, *, question, fingerprint=None,
                       batch_size=None, concurrency=None, gate=None):
        del provider, question, fingerprint, batch_size, concurrency
```

with

```python
    async def __call__(self, provider, items, *, question, fingerprint=None,
                       batch_size=None, concurrency=None, gate=None, on_batch=None):
        del provider, question, fingerprint, batch_size, concurrency, on_batch
```

`tests/test_agents/test_report_writer.py` — replace

```python
    async def fake_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None):
```

with

```python
    async def fake_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None, on_batch=None):
```

`tests/test_agents/test_report_writer.py` — replace

```python
    async def flaky_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None):
```

with

```python
    async def flaky_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None, on_batch=None):
```

`tests/test_agents/test_report_reviewer.py` — replace

```python
        batch_size=None, concurrency=None, gate=None,
    ):
        # The bounds and the shared gate are part of the call the real
        # checker accepts (PD-12; spec §6.5's shared semaphore).
        del provider, question, fingerprint, batch_size, concurrency, gate

```

with

```python
        batch_size=None, concurrency=None, gate=None, on_batch=None,
    ):
        # The bounds and the shared gate are part of the call the real
        # checker accepts (PD-12; spec §6.5's shared semaphore).
        del provider, question, fingerprint, batch_size, concurrency, gate, on_batch

```

`tests/test_agents/test_report_reviewer.py` — replace

```python
        batch_size=None, concurrency=None, gate=None,
    ):
        del provider, question, fingerprint, batch_size, concurrency, gate
        return {item.label: _Verdict(item.label) for item in items}, []

    monkeypatch.setattr(

```

with

```python
        batch_size=None, concurrency=None, gate=None, on_batch=None,
    ):
        del provider, question, fingerprint, batch_size, concurrency, gate, on_batch
        return {item.label: _Verdict(item.label) for item in items}, []

    monkeypatch.setattr(

```

`tests/test_agents/test_report_writer.py` — replace

```python
    [written] = run.state_update["events"]
    assert written.event_type == "report_writer.report.written"
    assert [event.event_id for event in received] == [written.event_id]

```

with

```python
    [written] = run.state_update["events"]
    assert written.event_type == "report_writer.report.written"
    # notes-progress-report spec §4 item 1: the progress events are live-only.
    progress = [event.metadata for event in received if event.event_type == "report_writer.progress"]
    assert [event.event_id for event in received if event.event_type != "report_writer.progress"] == [written.event_id]
    assert [(m["parts_total"], m["parts_returned"], m["fraction"]) for m in progress] == [(1, 0, 0.0), (1, 1, 0.5)]

```

Append to `tests/test_agents/test_report_writer.py`:

```python


# --- notes-progress-report spec §6.1, §6.2, §6.6: Writing's live progress ----------

SECRET_VERDICT = "SECRET-VERDICT-REASON"


def _two_part_state() -> ResearchState:
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                  target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                  target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]
    return ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                         verified_findings=[f1, f2])


def _two_part_route(messages, schema):
    """Sections, the bottom line, and the real Statement Check's replies: a sentence
    about growth is refused, the 2025 one corrected, every other one consistent."""
    import re

    from deep_research.agents.evidence_verifier import (
        StatementCheckDraft,
        StatementVerdictDraft,
    )

    body = messages[-1].content
    if schema.__name__ == "StatementCheckDraft":
        verdicts = []
        for label, text in re.findall(r"^## (\S+)\nsentence: (.*)$", body, re.M):
            if "grew" in text:
                verdicts.append(StatementVerdictDraft(label=label, verdict="inconsistent", reason=SECRET_VERDICT))
            elif "2025" in text:
                verdicts.append(StatementVerdictDraft(
                    label=label, verdict="corrected", reason=SECRET_VERDICT,
                    corrected_text="According to the source, 5 GW is forecast for 2025."))
            else:
                verdicts.append(StatementVerdictDraft(label=label, verdict="consistent", reason=SECRET_VERDICT))
        return StatementCheckDraft(statements=verdicts)
    if schema.__name__ == "BottomLineDraft":
        return BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])])
    if "First" in body.split("# This part of the question")[1][:40]:
        return SectionDraft(title="First", points=[
            WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, storage grew in 2024.", finding_labels=["F01"]),
        ])
    return SectionDraft(title="Second", points=[
        WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"]),
    ])


@pytest.mark.asyncio
async def test_writer_progress_fraction_monotonic(tracker: Tracker, tmp_path: Path) -> None:
    """AC17: one event when the jobs are built, one per returned part, one per Statement
    Check batch and one when the bottom line starts; samples are real drafted sentences
    with their check's verdict (the corrected text when corrected); ``fraction`` never
    decreases and reaches 1; never the check's reason text."""
    import json

    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    received: list[ResearchEvent] = []

    with bind_live_sink(received.append):
        await compose_written_report(
            task, provider=ScriptedCompleter(outputs=[_two_part_route] * 8),
            batch_size=1, section_concurrency=7,
        )

    progress = [e.metadata for e in received if e.event_type == "report_writer.progress"]
    assert progress[0] == {
        "phase": "sections", "parts_total": 2, "parts_returned": 0, "sentences_drafted": 0,
        "sentences_checked": 0, "backed": 0, "removed": 0, "unchecked": 0, "fraction": 0.0,
        "sample": None,
    }
    fractions = [m["fraction"] for m in progress]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    phases = [m["phase"] for m in progress]
    assert phases.index("bottom_line") == len(phases) - 2
    assert {k: progress[-1][k] for k in ("parts_returned", "sentences_drafted", "sentences_checked",
                                         "backed", "removed", "unchecked")} == {
        "parts_returned": 2, "sentences_drafted": 4, "sentences_checked": 4,
        "backed": 3, "removed": 1, "unchecked": 0,
    }
    samples = [m["sample"] for m in progress if m["sample"] is not None]
    assert {(s["text"], s["verdict"], s["findings"], s["section"]) for s in samples} == {
        ("According to the source, 10.4 GW in 2024.", "backed", 1, "First"),
        ("According to the source, storage grew in 2024.", "removed", 1, "First"),
        ("According to the source, 5 GW is forecast for 2025.", "backed", 1, "Second"),
        ("According to the source, 10.4 GW in 2024.", "backed", 1, "Bottom line"),
    }
    assert all(SECRET_VERDICT not in json.dumps(m) for m in progress)


@pytest.mark.asyncio
async def test_writer_progress_counts_what_a_substituted_checker_returns(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """A checker that never reports a batch (the tests' own substitutes) is counted once
    it returns: the last event still reads every sentence checked."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    received: list[ResearchEvent] = []

    with bind_live_sink(received.append):
        await compose_written_report(
            task, provider=ScriptedCompleter(outputs=[_two_part_route] * 4), section_concurrency=7,
        )

    last = [e.metadata for e in received if e.event_type == "report_writer.progress"][-1]
    assert (last["sentences_drafted"], last["sentences_checked"], last["backed"], last["fraction"]) == (4, 4, 4, 1.0)


def test_writing_progress_counts_a_sentence_once() -> None:
    """Review M13: a batch reported as two halves, then whole, counts each label once."""
    from deep_research.agents.report_writer import _WritingProgress

    progress = _WritingProgress(parts_total=1)
    progress.part_returned("topic-01", ["P01.01", "P01.02"])
    a = _FakeStatementCheckItem(label="P01.01", text="A.", labels=["F01"])
    b = _FakeStatementCheckItem(label="P01.02", text="B.", labels=["F01", "F02"])
    verdicts = {"P01.01": _verdict("consistent"), "P01.02": _verdict("inconsistent")}

    first = progress.count("First", [a], verdicts)
    second = progress.count("First", [b], verdicts)
    again = progress.count("First", [a, b], verdicts)

    assert first == {"text": "A.", "verdict": "backed", "findings": 1, "section": "First"}
    assert second == {"text": "B.", "verdict": "removed", "findings": 2, "section": "First"}
    assert again is None
    assert (len(progress.backed), len(progress.removed), len(progress.unchecked)) == (1, 1, 0)
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest "tests/test_agents/test_report_writer.py::test_the_report_written_event_is_published_live" "tests/test_agents/test_report_writer.py::test_writer_progress_fraction_monotonic" "tests/test_agents/test_report_writer.py::test_writer_progress_counts_what_a_substituted_checker_returns" "tests/test_agents/test_report_writer.py::test_writing_progress_counts_a_sentence_once" -q 2>&1 | tail -1
```

Expected: `4 failed` (no `report_writer.progress` is published yet; the last test cannot import `_WritingProgress`).

- [ ] **Step 3: Write the implementation**

`src/deep_research/agents/report_writer.py` — replace

```python
import asyncio
import re
from collections.abc import Callable, Iterator, Mapping, Sequence

```

with

```python
import asyncio
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar

```

`src/deep_research/agents/report_writer.py` — replace

```python
from pydantic import Field, ValidationError

```

with

```python
from pydantic import Field, JsonValue, ValidationError

```

`src/deep_research/agents/report_writer.py` — replace

```python


def _answer_form_line(task: ReportWriterTask) -> str:
```

with

```python


# --- notes-progress-report spec §6.1, §6.2, §6.6: Writing's live progress -----

#: The scope the bottom line's sentences are counted under; a part's is its coverage id.
_BOTTOM_LINE_SCOPE = "bottom_line"
_BOTTOM_LINE_SECTION = "Bottom line"


def _drafts_this_pass(job: PartJob) -> bool:
    """Whether ``_run_part`` makes a section call for this job (spec §6.2's ``P``).

    A job with no findings is empty, and one with ``redraft`` false and a
    previous section is carried over with no call (§6.9); every other job,
    one with no previous section included (P2-1), is drafted.
    """
    return bool(job.findings or job.context_findings) and (
        job.redraft or job.previous is None
    )


def writing_progress_event(metadata: Mapping[str, JsonValue]) -> ResearchEvent:
    """One ``report_writer.progress`` event (spec §4 item 1, §6.1): live-only."""
    return agent_event(
        agent_name=REPORT_WRITER_NAME,
        event_type="report_writer.progress",
        message="Writing progress.",
        metadata=metadata,
    )


class _WritingProgress:
    """One composition's running counts (spec §6.1, §6.2), published live.

    ``drafted`` maps each drafted part's coverage id -- and the bottom line's
    scope -- to the candidate keys handed to its Statement Check. A key is
    counted once, the first time a batch reports it (``backed``, ``removed``,
    or ``unchecked`` when its batch failed), so no total depends on how many
    events arrive or how sentences were batched (review M13). ``fraction``
    weighs each drafted part and the bottom line ``1/(P+1)``; a scope
    contributes its settled share once drafted, a part that returned nothing
    to check contributes in full, and the value never decreases.
    """

    def __init__(self, parts_total: int) -> None:
        self.parts_total = parts_total
        self.phase = "sections"
        self.returned: set[str] = set()
        self.drafted: dict[str, frozenset[str]] = {}
        self.backed: set[str] = set()
        self.removed: set[str] = set()
        self.unchecked: set[str] = set()
        self.fraction = 0.0

    def publish(self, sample: dict[str, JsonValue] | None = None) -> None:
        settled = self.backed | self.removed | self.unchecked
        shares = [
            len(keys & settled) / len(keys) if keys else 1.0
            for keys in self.drafted.values()
        ]
        self.fraction = max(
            self.fraction, min(1.0, round(sum(shares) / (self.parts_total + 1), 3))
        )
        publish_live(writing_progress_event({
            "phase": self.phase,
            "parts_total": self.parts_total,
            "parts_returned": len(self.returned),
            "sentences_drafted": sum(len(keys) for keys in self.drafted.values()),
            "sentences_checked": len(self.backed) + len(self.removed),
            "backed": len(self.backed),
            "removed": len(self.removed),
            "unchecked": len(self.unchecked),
            "fraction": self.fraction,
            "sample": sample,
        }))

    def part_returned(self, coverage_id: str, keys: Sequence[str]) -> None:
        """A part's draft returned with these candidate keys (none when it failed)."""
        self.returned.add(coverage_id)
        self.drafted[coverage_id] = frozenset(keys)
        self.publish()

    def bottom_line_started(self) -> None:
        """A bottom-line call is about to start: the first attempt or the re-ask."""
        self.phase = "bottom_line"
        self.publish()

    def bottom_line_drafted(self, keys: Sequence[str]) -> None:
        """The bottom line's candidates about to be checked; a re-ask adds its own."""
        self.drafted[_BOTTOM_LINE_SCOPE] = (
            self.drafted.get(_BOTTOM_LINE_SCOPE, frozenset()) | frozenset(keys)
        )

    def count(
        self, section: str, items: Sequence[object], verdicts: Mapping[str, object]
    ) -> dict[str, JsonValue] | None:
        """Count the keys these items report for the first time; return the sample.

        The sample is the first newly counted sentence with a verdict, in batch
        order: its text (the corrected text for a ``corrected`` verdict, at most
        200 characters), ``backed`` or ``removed``, its cited findings' count and
        ``section``; ``None`` when no item had a verdict.
        """
        sample: dict[str, JsonValue] | None = None
        for item in items:
            label: str = getattr(item, "label")
            if label in self.backed or label in self.removed or label in self.unchecked:
                continue
            verdict = verdicts.get(label)
            if verdict is None:
                self.unchecked.add(label)
                continue
            kept = getattr(verdict, "verdict") in ("consistent", "corrected")
            (self.backed if kept else self.removed).add(label)
            if sample is None:
                corrected = getattr(verdict, "corrected_text", "") or ""
                text = (
                    corrected
                    if getattr(verdict, "verdict") == "corrected" and corrected.strip()
                    else getattr(item, "text")
                )
                sample = {
                    "text": summarize_text(text, limit=200),
                    "verdict": "backed" if kept else "removed",
                    "findings": len(getattr(item, "labels")),
                    "section": summarize_text(section, limit=160),
                }
        return sample

    def reporter(
        self, section: str
    ) -> Callable[[Sequence[object], Mapping[str, object]], None]:
        """The Statement Check's ``on_batch`` for one part's (or the bottom line's) check."""

        def on_batch(items: Sequence[object], verdicts: Mapping[str, object]) -> None:
            self.publish(self.count(section, items, verdicts))

        return on_batch

    def settle(
        self, section: str, items: Sequence[object], verdicts: Mapping[str, object]
    ) -> None:
        """Count, once the check returned, any key no batch reported; publish if one was."""
        before = len(self.backed) + len(self.removed) + len(self.unchecked)
        sample = self.count(section, items, verdicts)
        if len(self.backed) + len(self.removed) + len(self.unchecked) != before:
            self.publish(sample)


#: The composition in progress (spec §6.2). ``compose_written_report`` sets it
#: before its part tasks start, so every part task, ``_check`` and each
#: bottom-line call of that composition read the same counts without a
#: parameter through the bottom-line helpers. Its value stays set for the rest
#: of the task that composed: nothing else in that task calls ``_check`` or the
#: bottom-line call, and the next composition sets its own.
_WRITING_PROGRESS: ContextVar[_WritingProgress | None] = ContextVar(
    "deep_research_writing_progress", default=None
)


def _answer_form_line(task: ReportWriterTask) -> str:
```

`src/deep_research/agents/report_writer.py` — replace

```python
    passages: Mapping[str, str], source_lines: Mapping[str, str],
) -> tuple[Mapping[str, _Verdict | None], list[ResearchError]]:
    """Run the Statement Check over one part's (or the bottom line's)
    candidates, through the shared gate (spec §6.5, D8, PD-12)."""
    if not candidates:
        return {}, []
```

with

```python
    passages: Mapping[str, str], source_lines: Mapping[str, str],
    part: tuple[str, str] | None = None,
) -> tuple[Mapping[str, _Verdict | None], list[ResearchError]]:
    """Run the Statement Check over one part's (or the bottom line's)
    candidates, through the shared gate (spec §6.5, D8, PD-12).

    Inside a composition (notes-progress-report spec §6.2), each settled batch
    is counted and published live, under ``part`` -- ``(coverage_id, section
    title)`` -- or, when ``part`` is ``None``, under the bottom line. Once the
    check returns, a key no batch reported is counted from the verdicts
    returned, so a substituted checker is counted too.
    """
    if not candidates:
        return {}, []
    progress = _WRITING_PROGRESS.get()
    section = _BOTTOM_LINE_SECTION if part is None else part[1]
    if progress is not None and part is None:
        progress.bottom_line_drafted([candidate.key for candidate in candidates])
```

`src/deep_research/agents/report_writer.py` — replace

```python
    try:
        return await check_statements(
            provider, items, question=question, fingerprint=fingerprint,
            batch_size=batch_size, gate=gate,
        )
    except ProviderConfigurationError:
        raise
    except (ProviderError, StructuredOutputError, ValidationError) as error:
        return {}, [agent_error(
            agent_name=REPORT_WRITER_NAME,
            error_type="report_writer_statement_check_failed",
            message="The report writer's statement check failed; every drafted point was kept unchanged.",
            details={"exception_type": type(error).__name__},
        )]

```

with

```python
    try:
        verdicts, errors = await check_statements(
            provider, items, question=question, fingerprint=fingerprint,
            batch_size=batch_size, gate=gate,
            on_batch=None if progress is None else progress.reporter(section),
        )
    except ProviderConfigurationError:
        raise
    except (ProviderError, StructuredOutputError, ValidationError) as error:
        verdicts, errors = {}, [agent_error(
            agent_name=REPORT_WRITER_NAME,
            error_type="report_writer_statement_check_failed",
            message="The report writer's statement check failed; every drafted point was kept unchanged.",
            details={"exception_type": type(error).__name__},
        )]
    if progress is not None:
        progress.settle(section, items, verdicts)
    return verdicts, errors

```

`src/deep_research/agents/report_writer.py` — replace

```python
    fingerprint: Callable[[str], object] | None,
) -> tuple[BottomLineDraft | None, list[ResearchError]]:
    errors: list[ResearchError] = []

```

with

```python
    fingerprint: Callable[[str], object] | None,
) -> tuple[BottomLineDraft | None, list[ResearchError]]:
    progress = _WRITING_PROGRESS.get()
    if progress is not None:
        progress.bottom_line_started()
    errors: list[ResearchError] = []

```

`src/deep_research/agents/report_writer.py` — replace

```python
    if draft is None:
        return _PartOutcome(job=job, section=None, status="failed", errors=draft_errors, verdicts={})

```

with

```python
    if draft is None:
        progress = _WRITING_PROGRESS.get()
        if progress is not None:
            progress.part_returned(job.coverage_id, ())
        return _PartOutcome(job=job, section=None, status="failed", errors=draft_errors, verdicts={})

```

`src/deep_research/agents/report_writer.py` — replace

```python
    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
        source_lines=finding_source_lines(task.findings, sources_by_url(task.sources)),
    )

    stated_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    disputed_labels: set[str] = set()
```

with

```python
    progress = _WRITING_PROGRESS.get()
    if progress is not None:
        progress.part_returned(job.coverage_id, [candidate.key for candidate in candidates])
    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
        source_lines=finding_source_lines(task.findings, sources_by_url(task.sources)),
        part=(job.coverage_id, _section_title(draft.title, job.sub_topic_title)),
    )

    stated_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    disputed_labels: set[str] = set()
```

`src/deep_research/agents/report_writer.py` — replace

```python
    section_gate = asyncio.Semaphore(max(1, section_concurrency))
    check_gate = asyncio.Semaphore(max(1, resolved_concurrency))
```

with

```python
    # notes-progress-report spec §6.2: Writing's counts, published live from here
    # on; the part tasks below copy this context, so each of them reads them too.
    progress = _WritingProgress(sum(1 for job in jobs if _drafts_this_pass(job)))
    _WRITING_PROGRESS.set(progress)
    progress.publish()

    section_gate = asyncio.Semaphore(max(1, section_concurrency))
    check_gate = asyncio.Semaphore(max(1, resolved_concurrency))
```


`src/deep_research/agents/__init__.py` — replace

```python
    report_written_event,

```

with

```python
    report_written_event,
    writing_progress_event,

```

`src/deep_research/agents/__init__.py` — replace

```python
    "report_written_event",

```

with

```python
    "report_written_event",
    "writing_progress_event",

```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's command again. Expected: `4 passed`. `test_the_report_written_event_is_published_live` asserts the sequence `[(1, 0, 0.0), (1, 1, 0.5)]`. It depends on `_run_bottom_line`'s early return, which Phase C rewrites; planning observed it passing on D + A + C. On any other sequence, stop and report it (review 1, P2-3).

Then check that every substitute accepts `on_batch`. Each one mirrors the real signature's `gate=None`, and this task puts `on_batch=None` on that same line:

```bash
git grep -n "gate=None" -- tests | grep -v on_batch
```

Expected: nothing.

- [ ] **Step 5: Re-pin the report writer**

Run Task 2 Step 5's snippet with `report_writer "Phase B (notes-progress-report spec §6.2, §6.6, 2026-09-30): live report_writer.progress as the parts and the bottom line are drafted and checked, through a per-composition ContextVar. No request text changed."`.

Expected: `2 failed, 9 passed, 67 deselected`: `test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift`, and `test_the_target_fingerprint_covers_the_shared_prompt_module`, which compares the report writer's own fingerprint with its pin. Then `report_writer: <the Task 1 value> -> <a new value>` (planning, on D + A + C: `f11d61b869d9 -> c4082e94159e`); then `11 passed, 67 deselected`.

- [ ] **Step 6: Run the regression files**

```bash
.venv/Scripts/python.exe -m pytest tests/test_agents/test_report_writer.py tests/test_agents/test_report_bottom_line.py tests/test_agents/test_report_reviewer.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: no failure (planning, on D + A + C: `312 passed`, of which Phase C's `test_report_bottom_line.py` is 24 — its `checker` fixture installs `test_report_writer.py`'s `_FakeChecker`, and its compose tests run under this task's hooks).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/agents/report_writer.py src/deep_research/agents/__init__.py tests/test_agents/test_report_writer.py tests/test_agents/test_report_reviewer.py tests/test_evaluation/test_config.py
git commit -m "feat(progress): Writing's live progress -- parts, sentences checked, the bottom line"
```

---

### Task 6: Reviewing's live start and result, with five criteria and the notes (spec §6.1, §6.2, §6.7; AC18)

**Files:**
- Create: `src/deep_research/graph/review_brief.py`, `tests/test_graph/test_review_brief.py`
- Modify: `src/deep_research/graph/events.py` (`report_review_completed_event`), `src/deep_research/graph/nodes.py` (an import placed right before `graph.state`'s, so it sorts after Phase C's `note_outcomes` import; the reviewer node), `src/deep_research/graph/__init__.py`, `tests/test_imports.py` (Phase C's `submodules` line, which already lists `note_outcomes`)
- Test: `tests/test_graph/test_nodes.py`

**Interfaces:**
- Consumes: Phase A's `is_research_note`, `has_steering_kind` (`agents/reader_notes.py`) and `researched_note_topic_ids` (`graph/state.py`); today's `DIMENSION_GUIDANCE`, `ReportReview.material_defects`/`.note_dispositions`, `active_reader_notes`. Not Phase C's `graph/note_outcomes.py`: its outcomes are a note's terminal outcome for the published report, while Reviewing's results are the middle of the run (§6.7's `researched next`, and a spent pass reading `not found`); `review_brief.py`'s docstring says so (review 1, P3).
- Produces (`deep_research.graph`, exported): `CRITERIA: tuple[str, ...]` (the five, in `DIMENSION_GUIDANCE` order); `CRITERION_FOR_KIND: dict[GapKind, str]`; `review_criteria(review) -> list[dict[str, JsonValue]]`; `review_note_results(state, review) -> list[dict[str, JsonValue]]`; `report_review_completed_event(..., criteria=(), notes=())`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph/test_review_brief.py`:

```python
"""What Reviewing's brief reads from one review (notes-progress-report spec §6.1, §6.2, §6.7; AC18)."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.agents.reader_notes import note_sub_topic
from deep_research.graph.review_brief import (
    CRITERIA,
    CRITERION_FOR_KIND,
    review_criteria,
    review_note_results,
)
from deep_research.utils.types import (
    GAP_KINDS,
    ReportQualitySnapshot,
    ReviewDefect,
)
from tests.graph_fakes import fake_reader_note, fake_report_review, fake_research_state


def _defect(n: int, kind: str, *, severity: str = "major") -> ReviewDefect:
    return ReviewDefect(defect_id=f"review-{n:02d}", kind=kind, severity=severity, problem=f"SECRET problem {n}")


def test_reviewed_event_five_criteria_mapping() -> None:
    """D23 and §6.2: five criteria in DIMENSION_GUIDANCE order; every defect kind maps to
    one; a material defect marks its criterion not met with its kind, a minor one does
    not; an unscored review marks none."""
    assert CRITERIA == ("completeness", "evidence_quality", "attribution", "uncertainty", "readability")
    assert set(CRITERION_FOR_KIND) == set(GAP_KINDS)
    assert set(CRITERION_FOR_KIND.values()) == set(CRITERIA)
    review = fake_report_review(defects=[
        _defect(1, "coverage"), _defect(2, "missing_support"), _defect(3, "freshness"),
        _defect(4, "identity"), _defect(5, "presentation", severity="minor"),
    ])

    criteria = review_criteria(review)

    assert criteria == [
        {"dimension": "completeness", "met": False, "kinds": ["coverage"]},
        {"dimension": "evidence_quality", "met": False, "kinds": ["missing_support", "freshness"]},
        {"dimension": "attribution", "met": False, "kinds": ["identity"]},
        {"dimension": "uncertainty", "met": True, "kinds": []},
        {"dimension": "readability", "met": True, "kinds": []},
    ]
    assert "SECRET" not in str(criteria)
    assert [c["met"] for c in review_criteria(fake_report_review(status="provider_failed"))] == [None] * 5


def _completed(coverage_id: str, stop_reason: str):
    return agent_event(
        agent_name="researcher", event_type="researcher.sub_topic.completed", message="m",
        metadata={"coverage_id": coverage_id, "stop_reason": stop_reason},
    )


def test_reviewed_event_mixed_note_steering() -> None:
    """§6.7 and D20: a research note's result from its own targets, a steering note's from
    the review, a mixed note both; a research note with no researched topic is owed its
    pass, unless that pass is spent (``passed``, the unfunded refusal of §5.4); a
    replaced note is not listed."""
    covered = fake_reader_note("n1", kinds=["new_angle"], restatement="pastries at the cafés")
    mixed = fake_reader_note("n2", kinds=["new_angle", "exclude"], restatement="pastries, not closed cafés")
    honoured = fake_reader_note("n3", kinds=["emphasis"])
    unjudged = fake_reader_note("n4", kinds=["scope"])
    spent = fake_reader_note("n5", kinds=["new_angle"], restatement="opening hours", passed=True)
    searched = fake_reader_note("n6", kinds=["new_angle"], restatement="seating")
    replaced = fake_reader_note("n7", kinds=["emphasis"])
    replacing = fake_reader_note("n8", kinds=["emphasis"], replaces="n7")
    notes = [covered, mixed, honoured, unjudged, spent, searched, replaced, replacing]
    topics = [note_sub_topic(note, priority=2, reason="reader_note") for note in (covered, mixed, spent, searched)]
    state = fake_research_state(
        reader_notes=notes,
        sub_topics=topics,
        quality=ReportQualitySnapshot(answered_target_ids=["note-n1-target-01"]),
        events=[_completed("note-n1", "finished"), _completed("note-n5", "provider_error"),
                _completed("note-n6", "finished")],
    )
    review = fake_report_review(note_dispositions={
        "n2": "ignored_with_evidence", "n3": "honoured", "n8": "no_evidence",
    })

    assert review_note_results(state, review) == [
        {"note_id": "n1", "result": "met", "reason": "covered"},
        {"note_id": "n2", "result": "pending", "reason": "to_research",
         "steering": {"result": "not_met", "reason": "ignored_with_evidence"}},
        {"note_id": "n3", "result": "met", "reason": "honoured"},
        {"note_id": "n4", "result": "not_checked", "reason": "not_judged"},
        {"note_id": "n5", "result": "not_met", "reason": "not_found"},
        {"note_id": "n6", "result": "not_met", "reason": "not_found"},
        {"note_id": "n8", "result": "not_met", "reason": "no_evidence"},
    ]
```


Append to `tests/test_graph/test_nodes.py`:

```python


# --- notes-progress-report spec §6.1: the reviewer's start and result, live ---------


@pytest.mark.asyncio
async def test_reviewer_started_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.1: the reviewer's graph.node.started is published live when the node starts,
    and graph.report.reviewed -- with its five criteria and the notes' results -- before
    the notes wait; both stay in the node's snapshot as the objects published."""
    from deep_research.graph import nodes as nodes_module
    from deep_research.graph.live import bind_live_sink

    received: list = []
    seen_at_wait: list[list[str]] = []

    async def settled(*, timeout: float | None = None) -> bool:
        seen_at_wait.append([event.event_type for event in received])
        return True

    monkeypatch.setattr(nodes_module, "notes_settled", settled)
    with bind_live_sink(received.append):
        loaded = load_state(await report_reviewer_node(FakeReviewer())(dump_state(_writer_state())))

    assert [event.event_type for event in received] == ["graph.node.started", "graph.report.reviewed"]
    assert received[0].metadata["node"] == "report_reviewer"
    assert seen_at_wait == [["graph.node.started", "graph.report.reviewed"]]
    ids = [event.event_id for event in loaded.events]
    assert received[0].event_id in ids and received[1].event_id in ids
    reviewed = received[1].metadata
    assert [c["dimension"] for c in reviewed["criteria"]] == [
        "completeness", "evidence_quality", "attribution", "uncertainty", "readability",
    ]
    assert all(c["met"] is True for c in reviewed["criteria"])
    assert reviewed["notes"] == []


@pytest.mark.asyncio
async def test_an_unscored_review_marks_no_criterion() -> None:
    """§6.2: without a scored review every criterion is ``met: null``."""
    loaded = load_state(await report_reviewer_node(
        FakeReviewer([fake_report_review(status="provider_failed")])
    )(dump_state(_writer_state())))

    [reviewed] = [e for e in loaded.events if e.event_type == "graph.report.reviewed"]
    assert [c["met"] for c in reviewed.metadata["criteria"]] == [None] * 5
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_graph/test_review_brief.py "tests/test_graph/test_nodes.py::test_reviewer_started_live" "tests/test_graph/test_nodes.py::test_an_unscored_review_marks_no_criterion" -q 2>&1 | tail -3
```

Expected: `ERROR tests/test_graph/test_review_brief.py` (`ModuleNotFoundError: No module named 'deep_research.graph.review_brief'`), `1 error`.

- [ ] **Step 3: Write the implementation**

Create `src/deep_research/graph/review_brief.py`:

```python
"""What Reviewing's brief reads from one review (notes-progress-report spec §6.1, §6.2, §6.7).

``graph.report.reviewed`` carries the five criteria a review can mark not met,
and what became of each of the reader's notes. Both are counts, ids and
enumerated values only: never the review's prose, a defect's ``problem`` text
or a score (spec §4 item 1, D11, D23).

The notes' results are not Phase C's ``graph/note_outcomes.py`` outcomes, on
purpose. Those are a note's terminal outcome for the published report
(``covered`` / ``not_found`` / ``not_addressed`` / ``not_checked``). These are
Reviewing's view in the middle of a run (spec §6.7): a research note with no
researched topic reads ``researched next``, because the route still owes it
its pass, and one whose pass is already spent (``passed``, the unfunded
refusal of §5.4) reads ``not found``.
"""

from __future__ import annotations

from pydantic import JsonValue

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.agents.report_reviewer import DIMENSION_GUIDANCE
from deep_research.graph.state import researched_note_topic_ids
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    GapKind,
    ReaderNote,
    ReportReview,
    ResearchState,
    active_reader_notes,
)

#: D23: prioritization and actionability have no defect kind, so no review can
#: mark them not met; Reviewing shows the other five, in ``DIMENSION_GUIDANCE`` order.
CRITERIA: tuple[str, ...] = tuple(
    name
    for name, _ in DIMENSION_GUIDANCE
    if name not in {"prioritization", "actionability"}
)

#: §6.2: the criterion each defect kind counts against.
CRITERION_FOR_KIND: dict[GapKind, str] = {
    "coverage": "completeness",
    "mechanism": "completeness",
    "missing_support": "evidence_quality",
    "acquisition": "evidence_quality",
    "source_quality": "evidence_quality",
    "freshness": "evidence_quality",
    "identity": "attribution",
    "contradiction": "uncertainty",
    "semantic_duplicate": "readability",
    "presentation": "readability",
}


def review_criteria(review: ReportReview) -> list[dict[str, JsonValue]]:
    """The five criteria (spec §6.2): ``met`` is ``None`` without a scored review,
    else ``False`` when a material defect maps to it; ``kinds`` lists the mapped
    kinds, one per material defect, in defect order."""
    scored = review.status == "scored"
    kinds: dict[str, list[JsonValue]] = {criterion: [] for criterion in CRITERIA}
    if scored:
        for defect in review.material_defects:
            kinds[CRITERION_FOR_KIND[defect.kind]].append(defect.kind)
    return [
        {
            "dimension": criterion,
            "met": (not kinds[criterion]) if scored else None,
            "kinds": kinds[criterion],
        }
        for criterion in CRITERIA
    ]


def _research_half(
    note: ReaderNote, state: ResearchState, researched: set[str], answered: set[str]
) -> dict[str, JsonValue]:
    """A research note's result from its own topic's targets (spec §5.6, §6.7).

    Covered when a verified finding answers one of its targets (D31); not found
    when its topic was researched, or when its one note pass is already spent
    (``passed``: the unfunded-refusal corner of §5.4); otherwise it is owed its
    pass and is researched next.
    """
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note.note_id}"
    targets = {
        target.target_id
        for topic in state.sub_topics
        if topic.coverage_id == coverage_id
        for target in topic.evidence_targets
    }
    if targets & answered:
        return {"result": "met", "reason": "covered"}
    if coverage_id in researched or note.passed:
        return {"result": "not_met", "reason": "not_found"}
    return {"result": "pending", "reason": "to_research"}


_STEERING_RESULT: dict[str, dict[str, JsonValue]] = {
    "honoured": {"result": "met", "reason": "honoured"},
    "ignored_with_evidence": {"result": "not_met", "reason": "ignored_with_evidence"},
    "no_evidence": {"result": "not_met", "reason": "no_evidence"},
}


def review_note_results(state: ResearchState, review: ReportReview) -> list[dict[str, JsonValue]]:
    """Each active note's result (spec §6.7), in receipt order.

    A research note's result comes from its topic's targets, a steering note's
    from this review's disposition; a mixed note carries its research half in
    ``result``/``reason`` and its steering half in ``steering`` (D20).
    """
    answered = set(state.quality.answered_target_ids) if state.quality else set()
    researched = researched_note_topic_ids(state)
    verdicts = {entry.note_id: entry.status for entry in review.note_dispositions}
    results: list[dict[str, JsonValue]] = []
    for note in active_reader_notes(state.reader_notes):
        steering = _STEERING_RESULT.get(
            verdicts.get(note.note_id, ""), {"result": "not_checked", "reason": "not_judged"}
        )
        if not is_research_note(note):
            results.append({"note_id": note.note_id, **steering})
            continue
        entry: dict[str, JsonValue] = {
            "note_id": note.note_id,
            **_research_half(note, state, researched, answered),
        }
        if has_steering_kind(note):
            entry["steering"] = dict(steering)
        results.append(entry)
    return results
```


`src/deep_research/graph/events.py` — replace

```python
def report_review_completed_event(
    *,
    iteration: int,
    review_status: str,
    mean_score: float | None,
    material_defects: int,
    reviewed_statements: int,
    fingerprint: str,
    reused: bool,
) -> ResearchEvent:
    """Record the terminal semantic review's outcome for one pass.

    Counts, an enumerated status, and the packet fingerprint only — never the
    review's prose and never a defect's text, which are provider output. The
    status is one of ``scored``/``incomplete``/``provider_failed``: the first
    means a judgement exists, the other two are the honest record that none
    does.
    """
```

with

```python
def report_review_completed_event(
    *,
    iteration: int,
    review_status: str,
    mean_score: float | None,
    material_defects: int,
    reviewed_statements: int,
    fingerprint: str,
    reused: bool,
    criteria: Sequence[Mapping[str, JsonValue]] = (),
    notes: Sequence[Mapping[str, JsonValue]] = (),
) -> ResearchEvent:
    """Record the terminal semantic review's outcome for one pass.

    Counts, an enumerated status, and the packet fingerprint only — never the
    review's prose and never a defect's text, which are provider output. The
    status is one of ``scored``/``incomplete``/``provider_failed``: the first
    means a judgement exists, the other two are the honest record that none
    does. ``criteria`` and ``notes`` are what Reviewing's brief reads
    (notes-progress-report spec §6.1, ``graph/review_brief.py``): the five
    criteria with ``met`` and the defect kinds behind a miss, and each note's
    enumerated result -- ids and enumerated values, never text.
    """
```

`src/deep_research/graph/events.py` — replace

```python
            "input_fingerprint": fingerprint,
            "reused": reused,
        },
    )

```

with

```python
            "input_fingerprint": fingerprint,
            "reused": reused,
            "criteria": [dict(criterion) for criterion in criteria],
            "notes": [dict(note) for note in notes],
        },
    )

```

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.graph.state import (
    EXTRA_PASS_NODE,
```

with

```python
from deep_research.graph.review_brief import review_criteria, review_note_results
from deep_research.graph.state import (
    EXTRA_PASS_NODE,
```

`src/deep_research/graph/nodes.py` — replace

```python
        started = merge_research_state(
            state,
            {
                "events": [
                    node_started_event(
                        REPORT_REVIEWER_NODE, iteration=state.iteration
                    )
                ],
                **_board_notes_update(state),
            },
        )
        review, errors, reused = await _review_report(started, reviewer)
```

with

```python
        started_event = node_started_event(
            REPORT_REVIEWER_NODE, iteration=state.iteration
        )
        started = merge_research_state(
            state,
            {
                "events": [started_event],
                **_board_notes_update(state),
            },
        )
        # notes-progress-report spec §6.1: published live, so Reviewing's elapsed
        # time starts on time; the same object stays in this node's snapshot,
        # which the orchestrator then skips by its event_id.
        publish_live(started_event)
        review, errors, reused = await _review_report(started, reviewer)
```

`src/deep_research/graph/nodes.py` — replace

```python
        await notes_settled(timeout=
```

with

```python
        reviewed_event = report_review_completed_event(
            iteration=started.iteration,
            review_status=review.status,
            mean_score=review.mean_score,
            material_defects=len(review.material_defects),
            reviewed_statements=len(review.reviewed_statement_ids),
            fingerprint=review.input_fingerprint,
            reused=reused,
            criteria=review_criteria(review),
            notes=review_note_results(started, review),
        )
        # Published live before the notes wait (spec §6.1): Reviewing's checks land
        # the moment the review does, not after a note still being read.
        publish_live(reviewed_event)
        await notes_settled(timeout=
```

`src/deep_research/graph/nodes.py` — replace

```python
                "events": [
                    report_review_completed_event(
                        iteration=started.iteration,
                        review_status=review.status,
                        mean_score=review.mean_score,
                        material_defects=len(review.material_defects),
                        reviewed_statements=len(review.reviewed_statement_ids),
                        fingerprint=review.input_fingerprint,
                        reused=reused,
                    ),
                    route_decided_event(
```

with

```python
                "events": [
                    reviewed_event,
                    route_decided_event(
```


`src/deep_research/graph/__init__.py` — replace

```python
from deep_research.graph.state import (

```

with

```python
from deep_research.graph.review_brief import (
    CRITERIA,
    CRITERION_FOR_KIND,
    review_criteria,
    review_note_results,
)
from deep_research.graph.state import (

```

`src/deep_research/graph/__init__.py` — replace

```python
    "report_review_completed_event",

```

with

```python
    "report_review_completed_event",
    "CRITERIA",
    "CRITERION_FOR_KIND",
    "review_criteria",
    "review_note_results",

```

`tests/test_imports.py` — replace

```python
    submodules = ["errors", "events", "live", "note_outcomes", "nodes", "orchestrator", "state"]

```

with

```python
    submodules = ["errors", "events", "live", "note_outcomes", "nodes", "orchestrator", "review_brief", "state"]

```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's command again. Expected: `4 passed`.

- [ ] **Step 5: Run the regression files**

```bash
.venv/Scripts/python.exe -m pytest tests/test_graph tests/test_imports.py -q 2>&1 | tail -1
```

Expected: no failure (planning: `256 passed` on D + A + C, `247` on D + A).

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/review_brief.py src/deep_research/graph/events.py src/deep_research/graph/nodes.py src/deep_research/graph/__init__.py tests/test_graph/test_review_brief.py tests/test_graph/test_nodes.py tests/test_imports.py
git commit -m "feat(progress): Reviewing's start and result published live, with five criteria and the notes' results"
```

---

### Task 7: Replay's release times and hold, the proxy, and the proof that progress stays live-only (spec §6.10 items 1–2; AC13, AC21)

**Files:**
- Modify: `src/deep_research/api/replay.py`, `web/app/api/[...path]/route.ts`, `README.md` ("Run the app"), `web/README.md`
- Test: `tests/test_api/test_replay.py`, `web/test/proxy.test.ts`

**Interfaces:**
- Consumes: Phase D's `POST /research/{id}/stop` (202 and `stopped_step`) and `ResearchSession.publish` dropping events after a stop; `_utc_now_iso` (`utils/types.py`).
- Produces: `REPLAY_HOLD_HEADER = "x-replay-hold-after"`, `requested_hold: ContextVar[tuple[str, int] | None]`, `parse_hold(value: str) -> tuple[str, int] | None` (`deep_research.api.replay`); `ReplayRunner._drain(queue, publish, hold=None)` publishes `event.model_copy(update={"timestamp": <release time>})`. The proxy forwards `x-replay-hold-after`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api/test_replay.py` — replace

```python
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner

```

with

```python
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner, parse_hold

```

`tests/test_api/test_replay.py` — replace

```python
    ids = [event.event_id for event in received]
    assert len(ids) == len(set(ids))
    assert set(ids) == {event.event_id for event in outcome.state.events}

```

with

```python
    ids = [event.event_id for event in received]
    assert len(ids) == len(set(ids))
    # notes-progress-report spec §4 item 1: the four progress types are live-only, so
    # they are exactly the received events the state does not hold.
    progress = {event.event_id for event in received if event.event_type in PROGRESS_TYPES}
    assert set(ids) - progress == {event.event_id for event in outcome.state.events}

```

`tests/test_api/test_replay.py` — replace

```python
def replay_app(root: Path, *, delay: float = 0.0):
```

with

```python
PROGRESS_TYPES = frozenset({
    "planner.progress",
    "source_evaluator.progress",
    "evidence_verifier.progress",
    "report_writer.progress",
})


def replay_app(root: Path, *, delay: float = 0.0):
```

Append to `tests/test_api/test_replay.py`:

```python



# --- notes-progress-report spec §4 item 1, §6.1, §6.10 ------------------------------


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.asyncio
async def test_progress_events_live_only(tmp_path: Path) -> None:
    """AC13 on the real graph: each of the four progress types is published, none is in
    the run's state, no node's ``event_count`` counts one, and no string they carry is a
    URL."""
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.0, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    assert {event.event_type for event in received} >= PROGRESS_TYPES
    assert not {event.event_type for event in outcome.state.events} & PROGRESS_TYPES
    node: str | None = None
    own = 0
    for event in received:
        if event.event_type == "graph.node.started":
            node, own = event.metadata["node"], 0
        elif event.source == f"agent.{node}" and event.event_type not in PROGRESS_TYPES:
            own += 1
        elif event.event_type == "graph.node.completed" and event.source.startswith("graph.") and node in {
            "planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer",
        }:
            assert event.metadata["event_count"] == own, node
    for event in received:
        if event.event_type in PROGRESS_TYPES:
            assert all("://" not in text for text in _strings(event.metadata)), event.metadata


def test_parse_hold() -> None:
    """§6.10: ``<event_type>[#<n>]``, n at least 1; anything else holds nothing."""
    assert parse_hold("planner.progress") == ("planner.progress", 1)
    assert parse_hold(" evidence_verifier.progress#2 ") == ("evidence_verifier.progress", 2)
    for bad in ("", "#2", "planner.progress#0", "planner.progress#x", "planner progress", "a#-1"):
        assert parse_hold(bad) is None, bad


def _event(event_type: str, n: int) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type, source="graph", message=f"m{n}",
        timestamp="2026-01-01T00:00:00+00:00", event_id=f"e{n}",
    )


@pytest.mark.asyncio
async def test_replay_restamps_and_holds(tmp_path: Path) -> None:
    """AC21: each event is published with its release time (its id unchanged), and a
    hold releases events through the n-th of its type, then waits until cancelled."""
    runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.01, root=tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    for n, event_type in enumerate(["a", "b", "c", "b", "d"]):
        queue.put_nowait(_event(event_type, n))
    queue.put_nowait(None)
    published: list[ResearchEvent] = []

    drain = asyncio.create_task(runner._drain(queue, published.append, ("b", 2)))
    await asyncio.sleep(0.3)

    assert [event.event_id for event in published] == ["e0", "e1", "e2", "e3"]
    assert all(event.timestamp != "2026-01-01T00:00:00+00:00" for event in published)
    assert [event.timestamp for event in published] == sorted(event.timestamp for event in published)
    assert not drain.done()
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert len(published) == 4


@pytest.mark.asyncio
async def test_published_timestamps_are_the_release_times(tmp_path: Path) -> None:
    """AC21 through the runner: the paced copies span the pacing, while the engine's own
    state keeps the times it ran at."""
    delay = 0.02
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    from datetime import datetime

    span = lambda events: (  # noqa: E731
        datetime.fromisoformat(events[-1].timestamp) - datetime.fromisoformat(events[0].timestamp)
    ).total_seconds()
    assert span(received) >= 0.8 * (len(received) - 1) * delay
    assert span(received) > span(outcome.state.events)
```


Append to `tests/test_api/test_replay.py`:

```python


def test_hold_after_holds_the_stream_until_the_session_is_stopped(tmp_path: Path) -> None:
    """AC21 through the API (notes-progress-report spec §6.10 item 2): with
    ``X-Replay-Hold-After: graph.report.reviewed`` the stream releases events through the
    first review and nothing after it; the session stays running, on Reviewing, while held;
    ``POST /stop`` (Phase D) ends it there, and ``session.stopped`` is the stream's last frame."""
    with guarded(), TestClient(replay_app(tmp_path, delay=0.01)) as client:
        posted = client.post(
            "/research", json={"query": "q"}, headers={"X-Replay-Hold-After": "graph.report.reviewed"},
        )
        assert posted.status_code == 202
        session_id = posted.json()["session_id"]
        deadline = time.monotonic() + 30
        while client.get(f"/research/{session_id}/status").json()["current_agent"] != "report_reviewer":
            assert time.monotonic() < deadline, "the run never reached Reviewing"
            time.sleep(0.02)
        time.sleep(0.5)  # fifty of the pacer's 10 ms beats: an event released past the hold would show
        held = client.get(f"/research/{session_id}/status").json()
        assert (held["status"], held["current_agent"]) == ("running", "report_reviewer")

        stopped = client.post(f"/research/{session_id}/stop")
        assert stopped.status_code == 202
        assert (stopped.json()["status"], stopped.json()["stopped_step"]) == ("stopped", "report_reviewer")
        names = frames(client.get(f"/research/{session_id}/stream").text)

    assert names.count("graph.report.reviewed") == 1
    assert names[-2:] == ["graph.report.reviewed", "session.stopped"]
```


`web/test/proxy.test.ts` — replace

```ts
    expect(seen.headers!["x-replay-clarify"]).toBe("on");
  });

```

with

```ts
    expect(seen.headers!["x-replay-clarify"]).toBe("on");
  });
  it("forwards x-replay-hold-after, so a capture can hold replay's stream at one step (notes-progress-report spec §6.10)", async () => {
    const seen: { headers?: IncomingMessage["headers"] } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      seen.headers = req.headers;
      res.writeHead(202, { "content-type": "application/json" });
      res.end("{}");
    });
    const request = new NextRequest("http://localhost:3000/api/research", {
      method: "POST", body: "{}", headers: { "content-type": "application/json", "x-replay-hold-after": "evidence_verifier.progress#2" },
    });
    expect((await POST(request, ctx("research"))).status).toBe(202);
    expect(seen.headers!["x-replay-hold-after"]).toBe("evidence_verifier.progress#2");
  });

```


- [ ] **Step 2: Run them to make sure they fail**

```bash
.venv/Scripts/python.exe -m pytest "tests/test_api/test_replay.py::test_the_replay_runner_delivers_every_event_once_inside_its_own_node" "tests/test_api/test_replay.py::test_progress_events_live_only" "tests/test_api/test_replay.py::test_parse_hold" "tests/test_api/test_replay.py::test_replay_restamps_and_holds" "tests/test_api/test_replay.py::test_published_timestamps_are_the_release_times" "tests/test_api/test_replay.py::test_hold_after_holds_the_stream_until_the_session_is_stopped" -q 2>&1 | tail -3
(cd web && npx vitest run test/proxy.test.ts 2>&1 | grep -E "Tests  ")
```

Expected: `ERROR tests/test_api/test_replay.py` (`ImportError: cannot import name 'parse_hold'`), `1 error`; then `Tests  1 failed | 8 passed (9)` (the header is not forwarded yet).

- [ ] **Step 3: Write the implementation**

`src/deep_research/api/replay.py` — replace

```python
one-time check ask its fixed questions (``api/clarify.py``), and anything else
leaves every flow exactly as it was (live-briefs spec §4.4).

```

with

```python
one-time check ask its fixed questions (``api/clarify.py``), and anything else
leaves every flow exactly as it was (live-briefs spec §4.4).

Two pacing aids (notes-progress-report spec §6.10): each event is published
with its release time as its timestamp, so the console's elapsed times read as
they would live; and ``X-Replay-Hold-After: <event_type>[#<n>]`` on the same
request makes the stream stop after the n-th event of that type (default the
first) and hold until the session is stopped or the server shuts down -- what
the visual captures use to photograph one step's brief.

```

`src/deep_research/api/replay.py` — replace

```python
from deep_research.utils.types import ReaderAnswer, ResearchEvent

```

with

```python
from deep_research.utils.types import ReaderAnswer, ResearchEvent, _utc_now_iso

```

`src/deep_research/api/replay.py` — replace

```python
REPLAY_CASE_HEADER = "x-replay-case"
requested_case: ContextVar[str | None] = ContextVar("deep_research_replay_case", default=None)
_log = logging.getLogger(__name__)

```

with

```python
REPLAY_CASE_HEADER = "x-replay-case"
requested_case: ContextVar[str | None] = ContextVar("deep_research_replay_case", default=None)
REPLAY_HOLD_HEADER = "x-replay-hold-after"
requested_hold: ContextVar[tuple[str, int] | None] = ContextVar(
    "deep_research_replay_hold", default=None
)
_log = logging.getLogger(__name__)


def parse_hold(value: str) -> tuple[str, int] | None:
    """``<event_type>[#<n>]`` as ``(event_type, n)``; ``None`` for anything else.

    ``n`` defaults to 1 and must be a whole number of at least 1; an event type
    is one token with no whitespace. A malformed value holds nothing.
    """
    event_type, _, count = value.strip().partition("#")
    if not event_type or any(character.isspace() for character in event_type):
        return None
    if not count:
        return event_type, 1
    if not count.isdigit() or int(count) < 1:
        return None
    return event_type, int(count)

```

`src/deep_research/api/replay.py` — replace

```python
        del question, max_extra_passes
        scenario = resolve_scenario(requested_case.get() or self.default_case)
```

with

```python
        del question, max_extra_passes
        scenario = resolve_scenario(requested_case.get() or self.default_case)
        hold = requested_hold.get()
```

`src/deep_research/api/replay.py` — replace

```python
            drain_task = asyncio.create_task(self._drain(queue, event_handler))
```

with

```python
            drain_task = asyncio.create_task(self._drain(queue, event_handler, hold))
```

`src/deep_research/api/replay.py` — replace

```python
    async def _drain(self, queue: asyncio.Queue[ResearchEvent | None], publish: ProgressHandler) -> None:
        while (event := await queue.get()) is not None:
            publish(event)
            if self.delay > 0:
                await asyncio.sleep(self.delay)

```

with

```python
    async def _drain(
        self,
        queue: asyncio.Queue[ResearchEvent | None],
        publish: ProgressHandler,
        hold: tuple[str, int] | None = None,
    ) -> None:
        """Release each event ``delay`` seconds apart, stamped with its release time.

        The engine ran unpaced, so the events' own timestamps are a fraction of a
        second apart; the copy published carries the moment it is released (spec
        §6.10), while the engine's state keeps its own. With ``hold``, the drain
        stops after the n-th event of that type and waits until it is cancelled.
        """
        seen = 0
        while (event := await queue.get()) is not None:
            publish(event.model_copy(update={"timestamp": _utc_now_iso()}))
            if hold is not None and event.event_type == hold[0]:
                seen += 1
                if seen == hold[1]:
                    await asyncio.Event().wait()
            if self.delay > 0:
                await asyncio.sleep(self.delay)

```

`src/deep_research/api/replay.py` — replace

```python
        requested_clarify.set(
            headers.get(REPLAY_CLARIFY_HEADER, "").strip().lower() == "on"
        )
```

with

```python
        requested_clarify.set(
            headers.get(REPLAY_CLARIFY_HEADER, "").strip().lower() == "on"
        )
        requested_hold.set(parse_hold(headers.get(REPLAY_HOLD_HEADER, "")))
```


`web/app/api/[...path]/route.ts` — replace

```ts
// x-replay-clarify: replay's scripted one-time check asks only when the POST carried it (live-briefs spec §4.4).
const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case", "x-replay-clarify"] as const;
```

with

```ts
// x-replay-clarify: replay's scripted one-time check asks only when the POST carried it (live-briefs spec §4.4).
// x-replay-hold-after: replay holds its stream after the named event, for captures (notes-progress-report spec §6.10).
const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case", "x-replay-clarify", "x-replay-hold-after"] as const;
```


- [ ] **Step 4: Run the tests to make sure they pass**

Run Step 2's commands again. Expected: `6 passed`; `Tests  9 passed (9)`.

- [ ] **Step 5: The README and the web README** (Phase D's plan, Open issue O1, hands this sentence to B)

B leaves `web/README.md`'s `npm run capture:visual` line alone: Phase C's revision rewrites it (C's review, M5). B's own bullet names the five step briefs' captures instead.

`README.md` — replace

```markdown
questions (Region, Period, For), so the check can be exercised offline.
```

with

```markdown
questions (Region, Period, For), so the check can be exercised offline. A `POST /research` may
also carry `X-Replay-Hold-After: <event type>[#<n>]`: the stream then stops after the n-th event
of that type (the first when `#<n>` is left out) and holds until the session is stopped or the
server exits; the step briefs' captures use it (notes-progress-report spec §6.10). Replay
publishes each event stamped with the moment it releases it, so the steps' elapsed times read
as they would live.
```

`web/README.md` — replace

```markdown
- `npm run capture:events -- <case-id>` — records a replay session's frames into
  `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at
  `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`).
```

with

```markdown
- `npm run capture:events -- <case-id>` — records a replay session's frames into
  `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at
  `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`). After a re-capture, rewrite the
  page's active-row pin from the new frames: `WRITE_ACTIVE_ROWS=1 npx vitest run
  test/active-row.test.ts`.
```

`web/README.md` — replace

```markdown
- Stop (notes-progress-report spec §8): the topbar's Stop, beside the running chip, asks once and
```

with

```markdown
- Live progress per step (notes-progress-report spec §6): each running step's brief reads its
  own live-only progress event — `planner.progress`, `source_evaluator.progress`,
  `evidence_verifier.progress`, `report_writer.progress` — and Reviewing reads
  `graph.report.reviewed`, which is now published as the review lands. In replay mode
  `POST /research` may carry `X-Replay-Hold-After: <event type>[#<n>]` (the proxy forwards it):
  the stream holds after that event until the session is stopped. `e2e/progress.spec.ts` and the
  step briefs' captures use it, then `POST /research/{id}/stop`: `npm run capture:visual` adds
  `13-planning-brief` (with its `-phone` twin), `14-evaluating-brief`, `15-verifying-brief`,
  `16-writing-brief` and `17-reviewing-brief`.
- Stop (notes-progress-report spec §8): the topbar's Stop, beside the running chip, asks once and
```


- [ ] **Step 6: Run the API regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api -q 2>&1 | tail -1
```

Expected: no failure (planning: `257 passed` on D + A + C, `255` on D + A).

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/api/replay.py tests/test_api/test_replay.py "web/app/api/[...path]/route.ts" web/test/proxy.test.ts README.md web/README.md
git commit -m "feat(replay): release-time timestamps and X-Replay-Hold-After, forwarded by the proxy"
```

---

### Task 8: Re-capture the replay fixtures, and rewrite Phase D's active-row pin (spec §6.10 item 3; D's Open issue O3)

**Files:**
- Modify (generated): `web/test/fixtures/events/missing-target-triggers-one-extra-pass.json`, `web/test/fixtures/events/scoped-redraft-after-a-named-defect.json`, `web/test/fixtures/active-rows.json`

**Interfaces:**
- Consumes: Tasks 2–7 (the engine's progress events), Phase D's `web/test/active-row.test.ts` (`WRITE_ACTIVE_ROWS=1`) and `tests/test_api/test_stop.py::test_active_row_matches_web_rule`.
- Produces: captures holding the four progress types (Tasks 9–11 read them) and an active-row pin that matches them.

- [ ] **Step 1: Capture both cases from a replay server with no pacing**

```bash
PYW="$(cygpath -m "$PWD")/.venv/Scripts/python.exe"
MSYS_NO_PATHCONV=1 PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 node web/scripts/launch.mjs start api 8010 /research "\"$PYW\" -m deep_research.api --mode replay --port 8010 --replay-delay-ms 0"
(cd web && npm run -s capture:events -- missing-target-triggers-one-extra-pass scoped-redraft-after-a-named-defect)
node web/scripts/launch.mjs stop api 8010
```

`MSYS_NO_PATHCONV=1` keeps Git Bash from rewriting `/research` into a Windows path, and `cygpath -m` gives `launch.mjs`'s `cmd.exe` child a drive-letter interpreter path. Expected: `up: http://127.0.0.1:8010/research (pid …)`; two lines `test\fixtures\events\<case>.json: N events, completed/<iteration>`; `stopped: port 8010 refuses connections (pid … and its tree)`. Planning's capture (HEAD + B, before D, A and C) read `98 events` and `67 events`, and review round 1's in-process capture of the same two cases on D + A + C + B read the same: `98 events, completed/1` and `67 events, completed/0`.

- [ ] **Step 2: Check that the captures carry the progress events**

```bash
.venv/Scripts/python.exe - <<'EOF'
import json
from collections import Counter
for case in ("missing-target-triggers-one-extra-pass", "scoped-redraft-after-a-named-defect"):
    events = json.load(open(f"web/test/fixtures/events/{case}.json", encoding="utf-8"))["events"]
    counts = Counter(e["event_type"] for e in events if e["event_type"].endswith(".progress"))
    print(case, dict(sorted(counts.items())))
EOF
```

Expected: each line names all four types. Planning: `{'evidence_verifier.progress': 4, 'planner.progress': 2, 'report_writer.progress': 16, 'source_evaluator.progress': 4}` and `{'evidence_verifier.progress': 2, 'planner.progress': 2, 'report_writer.progress': 12, 'source_evaluator.progress': 2}` (the same on D + A + C: C changes the sentence counts inside a pass's last, bottom-line event — 7 rather than 8 in either case's first pass, 5 rather than 6 in the redraft — not how many events arrive).

- [ ] **Step 3: Rewrite the active-row pin and run both AC32 tests**

```bash
(cd web && npx vitest run test/active-row.test.ts 2>&1 | grep -E "Tests  ")
(cd web && WRITE_ACTIVE_ROWS=1 npx vitest run test/active-row.test.ts 2>&1 | grep -E "Tests  ")
git diff --stat -- web/test/fixtures/active-rows.json
(cd web && npx vitest run test/active-row.test.ts 2>&1 | grep -E "Tests  ")
.venv/Scripts/python.exe -m pytest tests/test_api/test_stop.py -k active_row -q 2>&1 | tail -1
```

Expected: `Tests  2 failed | 1 passed (3)` (the new captures shifted every count); then `Tests  3 passed (3)`; a two-line diff stat for `active-rows.json` (review `git diff`: only run lengths change, never a row name — progress events move no row); `Tests  3 passed (3)`; `2 passed, 10 deselected`.

- [ ] **Step 4: Commit**

```bash
git add web/test/fixtures/events/missing-target-triggers-one-extra-pass.json web/test/fixtures/events/scoped-redraft-after-a-named-defect.json web/test/fixtures/active-rows.json
git commit -m "test(fixtures): re-capture the replay events with the progress events; rewrite the active-row pin"
```

---

### Task 9: The web's run state (spec §4 item 4, §6.3–§6.7, §6.9; AC19)

**Files:**
- Modify: `web/lib/run-state.ts`
- Create: `web/test/progress-state.test.ts`
- Test: `web/test/run-state.test.ts` (the handler and key lists, the evaluator and reviewer outcomes), `web/test/components/brief-spine.test.tsx` (Reviewing's outcome line)

**Interfaces:**
- Consumes: Task 8's captures; Phase A's `NoteState.kinds`; Phase D's `RunState.stopped`.
- Produces (`web/lib/run-state.ts`):
  - `RunEvent { type; metadata; timestamp?: string }`; `Handler = (run, md, timestamp: string | null) => void`; `toRunEvent` copies `timestamp`.
  - Types `SlotState`, `PlanSlot`, `NoteSlot`, `PlanningState`, `EvaluatingState`, `VerifierSample`, `VerifyingState`, `WriterSample`, `WritingState`, `CriterionState`, `ReviewHalf`, `ReviewNoteResult`, `ReviewingState`; `emptyPlanning()`, `emptyReviewing()`; `notAcceptedLine(run): string`.
  - `RunState` gains `startedAt`, `durations`, `planning`, `evaluating`, `verifying`, `writing`, `reviewing`, `hardFailures`.
  - Handlers `planner.progress`, `source_evaluator.progress`, `evidence_verifier.progress`, `report_writer.progress`, `graph.quality.assessed`; extended `graph.node.started`/`.completed`, `planner.planning.completed`, `source_evaluator.evaluation.completed`, `graph.report.reviewed`, `graph.route.decided`, `session.note.interpreted` (a research note's slot).
  - `STAGES`: Reviewing's meta `5 checks`.

- [ ] **Step 1: Write the failing tests**

`web/test/run-state.test.ts` — replace

```ts
  it("has the seven rows and the twenty-three handlers", () => {
```

with

```ts
  it("has the seven rows and the twenty-eight handlers", () => {
```

`web/test/run-state.test.ts` — replace

```ts
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed",
      "planner.planning.completed", "report_writer.report.written", "researcher.research.completed",
      "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "session.stopped", "source_evaluator.evaluation.completed",
    ]);
```

with

```ts
      "evidence_verifier.progress", "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.quality.assessed", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed",
      "planner.planning.completed", "planner.progress", "report_writer.progress", "report_writer.report.written", "researcher.research.completed",
      "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "session.stopped", "source_evaluator.evaluation.completed", "source_evaluator.progress",
    ]);
```

`web/test/run-state.test.ts` — replace

```ts
      "active", "arc", "captions", "clarify", "counters", "countersPass", "finalStatus", "findingsSoFar",
      "loop", "loopPending", "marks", "notes", "open", "openNode", "outcomes", "pagesRead", "pass",
      "passFindings", "plan", "rearmed", "rearmedFirst", "reopen", "stopped", "topics",
    ]);
```

with

```ts
      "active", "arc", "captions", "clarify", "counters", "countersPass", "durations", "evaluating", "finalStatus", "findingsSoFar",
      "hardFailures", "loop", "loopPending", "marks", "notes", "open", "openNode", "outcomes", "pagesRead", "pass",
      "passFindings", "plan", "planning", "rearmed", "rearmedFirst", "reopen", "reviewing", "startedAt", "stopped", "topics",
      "verifying", "writing",
    ]);
```

`web/test/run-state.test.ts` — replace

```ts
      const reviewed = last("graph.report.reviewed");
      expect(run.outcomes.source_evaluator).toBe(plural(md<number>(last("source_evaluator.evaluation.completed"), "source_count"), "source rated", "sources rated"));
```

with

```ts
      const e = last("source_evaluator.evaluation.completed");
      const unrated = md<number>(e, "unscored_cap_count") + md<number>(e, "unscored_provider_count") + md<number>(e, "unscored_missing_count");
      // notes-progress-report spec §6.4: the scored sources and their split, never a score.
      expect(run.outcomes.source_evaluator).toBe(`${plural(md<number>(e, "scored_count"), "source rated", "sources rated")} · ${md<number>(e, "strong_count")} strong · `
        + `${md<number>(e, "fair_count")} fair · ${md<number>(e, "weak_count")} weak` + (unrated > 0 ? ` · ${unrated} not rated` : ""));
```

`web/test/run-state.test.ts` — replace

```ts
      expect(run.outcomes.report_reviewer).toBe(`Accepted · ${md<number>(reviewed, "mean_score").toFixed(2)}`);
```

with

```ts
      // §6.7: the accepted review's outcome names its criteria, never its score.
      expect(run.outcomes.report_reviewer).toBe("Accepted · all 5 met");
```


`web/test/components/brief-spine.test.tsx` — replace

```tsx
    expect(row(container, "report_reviewer").querySelector(".m-out")!.textContent).toBe("Accepted · 0.90");
```

with

```tsx
    expect(row(container, "report_reviewer").querySelector(".m-out")!.textContent).toBe("Accepted · all 5 met");
```


Create `web/test/progress-state.test.ts`:

```ts
// @vitest-environment node — pure state over synthesized events (notes-progress-report spec §6.9).
import { describe, expect, it } from "vitest";
import { applyEvent, newRunState, notAcceptedLine, type RunEvent, type RunState } from "../lib/run-state";

const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState {
  const run = newRunState();
  for (const e of events) applyEvent(run, e);
  return run;
}
/* The state after each event equals a fresh replay of events 1..k (DESIGN.md §5.7). */
function burstSafe(events: RunEvent[]): void {
  const run = newRunState();
  events.forEach((e, k) => { applyEvent(run, e); expect(play(events.slice(0, k + 1))).toEqual(structuredClone(run)); });
}
const slot = (coverage_id: string, title: string, state: string) => ({ coverage_id, title, state });

describe("timestamps (spec §4 item 4)", () => {
  it("keeps each row's start and, at its completion, its duration; a restart clears the duration", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("graph.node.completed", { node: "planner" }, at(83)),
    ]);
    expect(run.startedAt.planner).toBe(at(0));
    expect(run.durations.planner).toBe(83);
    applyEvent(run, ev("graph.node.started", { node: "planner", iteration: 0 }, at(90)));
    expect(run.durations.planner).toBeUndefined();
    applyEvent(run, ev("graph.node.started", { node: "extra_pass", iteration: 1 }, at(91)));
    expect(run.startedAt).not.toHaveProperty("extra_pass");
  });
  it("records no time for an event that carries none", () => {
    const run = play([ev("graph.node.started", { node: "researcher", iteration: 0 }), ev("graph.node.completed", { node: "researcher" })]);
    expect(run.startedAt).toEqual({});
    expect(run.durations).toEqual({});
  });
  it("keeps a looped reviewer's duration although its completion moves no row", () => {
    const run = play([
      ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)),
      ev("graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["t"] }, at(40)),
      ev("graph.node.completed", { node: "report_reviewer" }, at(41)),
    ]);
    expect(run.durations.report_reviewer).toBe(41);
    expect(run.active).toBe("researcher");
  });
});

describe("Planning (spec §6.3)", () => {
  const plan = [slot("topic-01", "Alpha", "checking"), slot("topic-02", "Beta", "checking")];
  it("follows each planner.progress, then stamps the final states and the planned notes", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.progress", { step: "drafting", check_round: 0, sub_topics: [] }),
    ]);
    expect(run.planning).toEqual({ step: "drafting", round: 0, slots: [], noteSlots: [] });
    applyEvent(run, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: plan }));
    expect(run.planning.slots).toEqual([
      { coverageId: "topic-01", title: "Alpha", state: "checking" }, { coverageId: "topic-02", title: "Beta", state: "checking" },
    ]);
    applyEvent(run, ev("planner.planning.completed", {
      sub_topic_count: 3, note_topic_count: 1,
      sub_topics: [slot("topic-01", "Alpha", "fixed"), slot("topic-02", "Beta", "flagged"),
        { coverage_id: "note-n1", title: "Your note: pastries", note_id: "n1", state: "planned" }],
    }));
    expect(run.planning.step).toBe("ready");
    expect(run.planning.slots.map((s) => s.state)).toEqual(["fixed", "flagged"]);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries", state: "planned" }]);
    expect(run.outcomes.planner).toBe("3 sub-topics · 1 from your note");
    expect(run.plan.map((p) => p.coverageId)).toEqual(["topic-01", "topic-02", "note-n1"]);
  });
  it("reads an unknown or missing slot state as drafted, and ignores a progress event with no known step", () => {
    const run = play([ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "pondering"), { coverage_id: "topic-02", title: "Beta" }] })]);
    expect(run.planning.slots.map((s) => s.state)).toEqual(["drafted", "drafted"]);
    applyEvent(run, ev("planner.progress", { step: "musing", sub_topics: [] }));
    expect(run.planning.step).toBe("checking");
  });
  it("gives a research note read while Planning runs its own slot, until the plan leaves it out", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.received", { note_id: "n1", text: "pastries too" }),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries at the cafés", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("session.note.received", { note_id: "n2", text: "skip closed ones" }),
      ev("session.note.interpreted", { note_id: "n2", restatement: "leave out closed cafés", kinds: ["exclude"], replaces: null, fallback: false }),
    ]);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries at the cafés", state: "pending" }]);
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed")] }));
    expect(run.planning.noteSlots).toEqual([]);
  });
  it("gives no slot once Planning is not the running row, and drops a slot whose note is replaced", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("session.note.interpreted", { note_id: "n2", restatement: "only pastries made in-house", kinds: ["new_angle"], replaces: "n1", fallback: false }),
    ]);
    expect(run.planning.noteSlots.map((s) => s.noteId)).toEqual(["n2"]);
    applyEvent(run, ev("graph.node.completed", { node: "planner" }, at(5)));
    applyEvent(run, ev("session.note.interpreted", { note_id: "n3", restatement: "seating", kinds: ["new_angle"], replaces: null, fallback: false }));
    expect(run.planning.noteSlots.map((s) => s.noteId)).toEqual(["n2"]);
  });
});

describe("Evaluating, Verifying and Writing (spec §6.4-§6.6)", () => {
  it("keeps the latest evaluator counts and the split outcome", () => {
    const run = play([
      ev("graph.node.started", { node: "source_evaluator", iteration: 0 }, at(0)),
      ev("source_evaluator.progress", { to_rate: 5, reused: 1, capped: 0, rated: 2, strong: 1, fair: 1, weak: 0, unrated: 1, batches: 3, batches_done: 2 }),
      ev("source_evaluator.evaluation.completed", { source_count: 6, scored_count: 5, strong_count: 2, fair_count: 2, weak_count: 1, unscored_cap_count: 0, unscored_provider_count: 1, unscored_missing_count: 0 }),
    ]);
    expect(run.evaluating).toEqual({ toRate: 5, reused: 1, capped: 0, rated: 2, strong: 1, fair: 1, weak: 0, unrated: 1, batches: 3, batchesDone: 2 });
    expect(run.outcomes.source_evaluator).toBe("5 sources rated · 2 strong · 2 fair · 1 weak · 1 not rated");
    applyEvent(run, ev("graph.node.started", { node: "source_evaluator", iteration: 1 }, at(9)));
    expect(run.evaluating).toBeNull();
  });
  it("keeps the verifier's latest tally and its last two samples, numbered; a report with no sample keeps them", () => {
    const sample = (text: string) => ({ text, verdict: "dropped", correction: null, drop_reason: "snippet_not_on_page", source: { role: null, host: "eia.gov" } });
    const tally = { total: 4, checked: 3, verified: 0, corrected: 0, quoted: 1, dropped: 2, batches: 1, batches_done: 0 };
    const run = play([
      ev("evidence_verifier.progress", { ...tally, sample: sample("A") }),
      ev("evidence_verifier.progress", { ...tally, sample: sample("B") }),
      ev("evidence_verifier.progress", { ...tally, sample: sample("C") }),
      ev("evidence_verifier.progress", { ...tally, checked: 4, batches_done: 1, sample: null }),
    ]);
    expect(run.verifying!.checked).toBe(4);
    expect(run.verifying!.samples.map((s) => [s.seq, s.text])).toEqual([[2, "B"], [3, "C"]]);
    expect(run.verifying!.samples[1]).toEqual({ seq: 3, text: "C", verdict: "dropped", correction: null, dropReason: "snippet_not_on_page", role: null, host: "eia.gov" });
  });
  it("keeps the writer's latest counts, its phase and a fraction that never falls", () => {
    const counts = { parts_total: 2, parts_returned: 1, sentences_drafted: 2, sentences_checked: 1, backed: 1, removed: 0, unchecked: 0 };
    const run = play([
      ev("report_writer.progress", { ...counts, phase: "sections", fraction: 0.5, sample: { text: "S.", verdict: "backed", findings: 2, section: "Alpha" } }),
      ev("report_writer.progress", { ...counts, phase: "bottom_line", fraction: 0.4, sample: null }),
    ]);
    expect(run.writing!.phase).toBe("bottom_line");
    expect(run.writing!.fraction).toBe(0.5);
    expect(run.writing!.samples).toEqual([{ seq: 1, text: "S.", verdict: "backed", findings: 2, section: "Alpha" }]);
  });
});

describe("Reviewing (spec §6.7)", () => {
  const criteria = (failing: Record<string, string[]> = {}) => ["completeness", "evidence_quality", "attribution", "uncertainty", "readability"]
    .map((dimension) => ({ dimension, met: !failing[dimension], kinds: failing[dimension] ?? [] }));
  const review = (md: Record<string, unknown>) => [
    ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)),
    ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.9, material_defects: 0, criteria: criteria(), notes: [], ...md }, at(70)),
  ];
  it("keeps the criteria, the notes' results and when the review landed -- never the score", () => {
    const run = play(review({
      material_defects: 1, criteria: criteria({ uncertainty: ["contradiction"] }),
      notes: [{ note_id: "n1", result: "pending", reason: "to_research", steering: { result: "not_met", reason: "ignored_with_evidence" } }],
    }));
    expect(run.reviewing.landed).toBe(true);
    expect(run.reviewing.reviewedAt).toBe(at(70));
    expect(run.reviewing.defects).toBe(1);
    expect(run.reviewing.criteria![3]).toEqual({ dimension: "uncertainty", met: false, kinds: ["contradiction"] });
    expect(run.reviewing.notes).toEqual([{ noteId: "n1", result: "pending", reason: "to_research", steering: { result: "not_met", reason: "ignored_with_evidence" } }]);
  });
  it("words each route's outcome, with no score", () => {
    const decide = (reason: string, md: Record<string, unknown> = {}, extra: RunEvent[] = []) =>
      play([...extra, ...review(md), ev("graph.route.decided", { destination: "finalize", reason, missing_required_target_ids: ["a", "b"] })]).outcomes.report_reviewer;
    expect(decide("report_accepted")).toBe("Accepted · all 5 met");
    expect(decide("redraft_requested", { material_defects: 1 })).toBe("1 thing to fix · back to the writer");
    expect(decide("extra_pass_requested")).toBe("Sent back to fill 2 gaps");
    expect(decide("review_unavailable")).toBe("Review unavailable");
    expect(decide("extra_passes_exhausted")).toBe("Not accepted · 2 gaps still open");
    expect(decide("report_not_accepted", { criteria: criteria({ completeness: ["coverage"], readability: ["presentation"] }) })).toBe("Not accepted · 3 of 5 met");
    expect(decide("report_not_accepted", {}, [ev("graph.quality.assessed", { hard_failures: ["missing_evidence_ledger"] })])).toBe("Not accepted · a check the run makes itself failed");
    expect(decide("report_not_accepted", {}, [ev("graph.quality.assessed", { hard_failures: [] })])).toBe("Not accepted · the reviewer's overall judgement fell short");
  });
  it("keeps the latest quality verdict's hard failures for the refusal line", () => {
    const run = play([ev("graph.quality.assessed", { hard_failures: ["a"] }), ev("graph.quality.assessed", { hard_failures: [] })]);
    expect(run.hardFailures).toEqual([]);
    expect(notAcceptedLine({ ...run, reviewing: { ...run.reviewing, criteria: [] } })).toBe("Not accepted · 0 of 5 met");
  });
  it("starts every review clean", () => {
    const run = play([...review({}), ev("graph.route.decided", { destination: "redraft", reason: "redraft_requested" }), ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(99))]);
    expect(run.reviewing).toEqual({ landed: false, reviewedAt: null, criteria: null, notes: [], defects: null, reason: null, missing: 0 });
  });
});

describe("burst safety (AC19)", () => {
  it("paints the same state from any prefix of a synthesized run", () => {
    burstSafe([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking")] }),
      ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), { coverage_id: "note-n1", title: "Your note: pastries", note_id: "n1", state: "planned" }] }),
      ev("graph.node.completed", { node: "planner" }, at(60)),
      ev("evidence_verifier.progress", { total: 1, checked: 1, verified: 1, corrected: 0, quoted: 0, dropped: 0, batches: 1, batches_done: 1, sample: { text: "A", verdict: "verified", correction: null, drop_reason: null, source: { role: "derivative", host: "x.org" } } }),
      ev("graph.quality.assessed", { hard_failures: [] }),
      ev("graph.report.reviewed", { review_status: "scored", material_defects: 0, criteria: [], notes: [] }, at(90)),
    ]);
  });
});
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/run-state.test.ts test/progress-state.test.ts test/components/brief-spine.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  3 failed (3)` — the five handlers, the new keys, the split and score-free outcomes and every `progress-state.test.ts` test are missing.

- [ ] **Step 3: Write the implementation**

`web/lib/run-state.ts` — replace

```ts
import type { ResearchEvent, SessionStatus } from "./api";
import { fmtScore } from "./format";
import { noteRedraftLine, notePassLine } from "./notes";

```

with

```ts
import type { ResearchEvent, SessionStatus } from "./api";
import { noteRedraftLine, notePassLine } from "./notes";

```

`web/lib/run-state.ts` — replace

```ts
  { id: "report_reviewer", label: "Reviewing", meta: "7 dimensions · accept at mean 0.80" },
```

with

```ts
  { id: "report_reviewer", label: "Reviewing", meta: "5 checks" },
```

`web/lib/run-state.ts` — replace

```ts
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }

```

with

```ts
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }
/* notes-progress-report spec §6.3: one planned topic's slot in Planning's brief, in the state the
   latest planner.progress (then planner.planning.completed) gives it. */
export type SlotState = "skeleton" | "drafted" | "checking" | "being_fixed" | "passed" | "fixed" | "flagged" | "not_checked";
export interface PlanSlot { coverageId: string; title: string; state: SlotState }
/* A research note's own slot: "pending" while Planning runs, "planned" once planning.completed lists it. */
export interface NoteSlot { noteId: string; title: string; state: "pending" | "planned" }
export interface PlanningState { step: "reading" | "drafting" | "checking" | "fixing" | "ready"; round: number; slots: PlanSlot[]; noteSlots: NoteSlot[] }
/* §6.1: the latest source_evaluator.progress, as sent. */
export interface EvaluatingState { toRate: number; reused: number; capped: number; rated: number; strong: number; fair: number; weak: number; unrated: number; batches: number; batchesDone: number }
/* §6.1: one ticker sample; `seq` numbers this pass's samples, so each keeps its own key. */
export interface VerifierSample { seq: number; text: string; verdict: string; correction: { field: string; value: string | null } | null; dropReason: string | null; role: string | null; host: string | null }
export interface VerifyingState { total: number; checked: number; verified: number; corrected: number; quoted: number; dropped: number; batches: number; batchesDone: number; samples: VerifierSample[]; sampleCount: number }
export interface WriterSample { seq: number; text: string; verdict: "backed" | "removed"; findings: number; section: string }
export interface WritingState { phase: "sections" | "bottom_line"; partsTotal: number; partsReturned: number; drafted: number; checked: number; backed: number; removed: number; unchecked: number; fraction: number; samples: WriterSample[]; sampleCount: number }
/* §6.1, §6.7: graph.report.reviewed's five criteria and each note's result, then the route decided after it. */
export interface CriterionState { dimension: string; met: boolean | null; kinds: string[] }
export interface ReviewHalf { result: string; reason: string }
export interface ReviewNoteResult extends ReviewHalf { noteId: string; steering: ReviewHalf | null }
export interface ReviewingState { landed: boolean; reviewedAt: string | null; criteria: CriterionState[] | null; notes: ReviewNoteResult[]; defects: number | null; reason: string | null; missing: number }

```

`web/lib/run-state.ts` — replace

```ts
  notes: NoteState[];                     /* the reader's notes, in receipt order */

```

with

```ts
  notes: NoteState[];                     /* the reader's notes, in receipt order */
  startedAt: Partial<Record<NodeId, string>>;  /* each row's latest graph.node.started timestamp (notes-progress-report spec §4 item 4) */
  durations: Partial<Record<NodeId, number>>;  /* seconds from that start to the row's graph.node.completed */
  planning: PlanningState;                /* Planning's status and slots (§6.3) */
  evaluating: EvaluatingState | null;     /* this pass's latest source_evaluator.progress (§6.4) */
  verifying: VerifyingState | null;       /* this pass's latest evidence_verifier.progress, the last two samples kept (§6.5) */
  writing: WritingState | null;           /* this draft's latest report_writer.progress, the last two samples kept (§6.6) */
  reviewing: ReviewingState;              /* this review's graph.report.reviewed and route decision (§6.7) */
  hardFailures: string[] | null;          /* the latest graph.quality.assessed.hard_failures (§6.7's refusal line) */

```

`web/lib/run-state.ts` — replace

```ts
export interface RunEvent { type: string; metadata: Record<string, unknown> }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Md = Record<string, any>; // the handlers read metadata keys exactly as the prototype does
export type Handler = (run: RunState, md: Md) => void;

```

with

```ts
/* `timestamp` is the event's own (notes-progress-report spec §4 item 4): optional, because the
   events tests and fixtures synthesize carry none; every event from the stream has one. */
export interface RunEvent { type: string; metadata: Record<string, unknown>; timestamp?: string }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Md = Record<string, any>; // the handlers read metadata keys exactly as the prototype does
export type Handler = (run: RunState, md: Md, timestamp: string | null) => void;

```

`web/lib/run-state.ts` — replace

```ts
clarify: null, notes: [],
```

with

```ts
clarify: null, notes: [],
    startedAt: {}, durations: {}, planning: emptyPlanning(), evaluating: null, verifying: null, writing: null,
    reviewing: emptyReviewing(), hardFailures: null,
```

`web/lib/run-state.ts` — replace

```ts
export function emptyCounters(): Counters {
```

with

```ts
export function emptyPlanning(): PlanningState { return { step: "reading", round: 0, slots: [], noteSlots: [] }; }
export function emptyReviewing(): ReviewingState {
  return { landed: false, reviewedAt: null, criteria: null, notes: [], defects: null, reason: null, missing: 0 };
}
export function emptyCounters(): Counters {
```

`web/lib/run-state.ts` — replace

```ts
/* Reviewing's outcome line, read at the route decision (the review's score arrived just before it). */
function reviewOutcome(run: RunState, md: Md): string {
  if (md.reason === "note_pass_requested") return "Sent back to research your note";
  if (md.reason === "note_redraft_requested") return "Sent back to the writer for your note";
  if (md.destination === "extra_pass") {
    const k = Array.isArray(md.missing_required_target_ids) ? md.missing_required_target_ids.length : 0;
    return k > 0 ? "Sent back to fill " + plural(k, "gap", "gaps") : "Sent back for more research";
  }
  const score = fmtScore(run.counters.reviewScore);
  if (md.reason === "report_accepted") return score === null ? "Accepted" : "Accepted · " + score;
  return score === null ? "Review unavailable" : "Not accepted · " + score;
}
```

with

```ts
const missingCount = (md: Md): number => (Array.isArray(md.missing_required_target_ids) ? md.missing_required_target_ids.length : 0);
/* notes-progress-report spec §6.7: why a scored report was not accepted, without a score. */
export function notAcceptedLine(run: RunState): string {
  const met = run.reviewing.criteria?.filter((c) => c.met === true).length ?? 0;
  if (met < 5) return "Not accepted · " + met + " of 5 met";
  return (run.hardFailures?.length ?? 0) > 0
    ? "Not accepted · a check the run makes itself failed"
    : "Not accepted · the reviewer's overall judgement fell short";
}
/* Reviewing's outcome line, read at the route decision (notes-progress-report spec §6.7 route
   table): never a score; the brief adds the row's duration (lib/briefs.ts). */
function reviewOutcome(run: RunState, md: Md): string {
  switch (md.reason) {
    case "note_pass_requested": return "Sent back to research your note";
    case "note_redraft_requested": return "Sent back to the writer for your note";
    case "report_accepted": return "Accepted · all 5 met";
    case "redraft_requested": return plural(run.reviewing.defects ?? 0, "thing", "things") + " to fix · back to the writer";
    case "extra_pass_requested": {
      const k = missingCount(md);
      return k > 0 ? "Sent back to fill " + plural(k, "gap", "gaps") : "Sent back for more research";
    }
    case "report_not_accepted": return notAcceptedLine(run);
    case "extra_passes_exhausted": return "Not accepted · " + plural(missingCount(md), "gap", "gaps") + " still open";
    default: return "Review unavailable";
  }
}
```

`web/lib/run-state.ts` — replace

```ts
/* Keyed by event type; each handler reads only `md` (the event's metadata). */
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.node.started": (run, md) => {
    run.openNode = md.node;
    /* the pass number is read here and from graph.extra_pass.started only */
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
  },
  "graph.node.completed": (run, md) => {
    const node: NodeId = md.node;
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "note_pass" || (node as string) === "writer_redraft") return; /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
```

with

```ts
/* notes-progress-report spec §6.1-§6.7: the progress events' wire shapes. An entry that does not fit
   is dropped, never invented; an unknown slot state reads as drafted (a ring, no fact). */
const SLOT_STATES: readonly string[] = ["skeleton", "drafted", "checking", "being_fixed", "passed", "fixed", "flagged", "not_checked"];
function planSlots(listed: unknown): PlanSlot[] {
  return (Array.isArray(listed) ? listed : [])
    .filter((t): t is Md => !!t && isText((t as Md).coverage_id) && typeof (t as Md).title === "string" && !isText((t as Md).note_id))
    .map((t) => ({ coverageId: t.coverage_id, title: t.title, state: (SLOT_STATES.includes(t.state) ? t.state : "drafted") as SlotState }));
}
function stepSeconds(from: string | undefined, to: string | null): number | null {
  if (!from || !to) return null;
  const s = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : null;
}
const textOrNull = (v: unknown): string | null => (isText(v) ? v : null);
function verifierSample(v: unknown, seq: number): VerifierSample | null {
  const m = v as Md | null;
  if (!m || typeof m.text !== "string" || !isText(m.verdict)) return null;
  const correction = m.correction && isText(m.correction.field) ? { field: m.correction.field as string, value: textOrNull(m.correction.value) } : null;
  const source = (m.source ?? {}) as Md;
  return { seq, text: m.text, verdict: m.verdict, correction, dropReason: textOrNull(m.drop_reason), role: textOrNull(source.role), host: textOrNull(source.host) };
}
function writerSample(v: unknown, seq: number): WriterSample | null {
  const m = v as Md | null;
  if (!m || typeof m.text !== "string" || (m.verdict !== "backed" && m.verdict !== "removed")) return null;
  return { seq, text: m.text, verdict: m.verdict, findings: count(m.findings), section: typeof m.section === "string" ? m.section : "" };
}
function reviewCriteria(listed: unknown): CriterionState[] | null {
  if (!Array.isArray(listed)) return null;
  return listed.filter((c): c is Md => !!c && isText((c as Md).dimension))
    .map((c) => ({ dimension: c.dimension, met: typeof c.met === "boolean" ? c.met : null, kinds: Array.isArray(c.kinds) ? c.kinds.filter(isText) : [] }));
}
const reviewHalf = (m: Md): ReviewHalf => ({ result: isText(m.result) ? m.result : "not_checked", reason: isText(m.reason) ? m.reason : "not_judged" });
function reviewNotes(listed: unknown): ReviewNoteResult[] {
  return (Array.isArray(listed) ? listed : []).filter((n): n is Md => !!n && isText((n as Md).note_id))
    .map((n) => ({ noteId: n.note_id, ...reviewHalf(n), steering: n.steering && typeof n.steering === "object" ? reviewHalf(n.steering) : null }));
}
/* §6.3: a note read as a research note while Planning is the running row joins the plan as its own
   slot; a later note that replaces it takes its slot away. */
function noteSlot(run: RunState, md: Md): void {
  if (isText(md.replaces)) run.planning.noteSlots = run.planning.noteSlots.filter((s) => s.noteId !== md.replaces);
  const kinds: unknown[] = Array.isArray(md.kinds) ? md.kinds : [];
  if (run.active !== "planner" || run.planning.step === "ready" || !kinds.includes("new_angle") || !isText(md.restatement)) return;
  if (!isText(md.note_id) || run.planning.noteSlots.some((s) => s.noteId === md.note_id)) return;
  run.planning.noteSlots.push({ noteId: md.note_id, title: "Your note: " + md.restatement, state: "pending" });
}

/* Keyed by event type; each handler reads only `md` (the event's metadata) and, for the rows' times,
   the event's own timestamp. */
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.node.started": (run, md, timestamp) => {
    run.openNode = md.node;
    /* the pass number is read here and from graph.extra_pass.started only */
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
    const node = md.node as NodeId;
    if (!AGENT_ORDER.includes(node)) return; /* the hops never map to a row */
    if (timestamp) run.startedAt[node] = timestamp; else delete run.startedAt[node];
    delete run.durations[node];
    /* notes-progress-report spec §6.9: each step's block starts clean, so a re-armed row does too;
       a research note's slot read before the planner started stays. */
    if (node === "planner") run.planning = { ...emptyPlanning(), noteSlots: run.planning.noteSlots };
    if (node === "source_evaluator") run.evaluating = null;
    if (node === "evidence_verifier") run.verifying = null;
    if (node === "report_writer") run.writing = null;
    if (node === "report_reviewer") run.reviewing = emptyReviewing();
  },
  "graph.node.completed": (run, md, timestamp) => {
    const node: NodeId = md.node;
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "note_pass" || (node as string) === "writer_redraft") return; /* hops never map to a row */
    const seconds = stepSeconds(run.startedAt[node], timestamp);
    if (seconds !== null) run.durations[node] = seconds;
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
```

`web/lib/run-state.ts` — replace

```ts
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
    run.outcomes.planner = run.captions.planner;
    const listed: unknown[] = Array.isArray(md.sub_topics) ? md.sub_topics : [];
```

with

```ts
  "planner.planning.completed": (run, md) => {
    const fromNotes = count(md.note_topic_count);
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
    run.outcomes.planner = run.captions.planner + (fromNotes > 0 ? " · " + plural(fromNotes, "from your note", "from your notes") : "");
    const listed: unknown[] = Array.isArray(md.sub_topics) ? md.sub_topics : [];
```

`web/lib/run-state.ts` — replace

```ts
    run.topics = run.plan.map((p) => ({ coverageId: p.coverageId, title: p.title, state: "waiting", findings: null }));
    run.pagesRead = null; run.findingsSoFar = null;
  },
```

with

```ts
    run.topics = run.plan.map((p) => ({ coverageId: p.coverageId, title: p.title, state: "waiting", findings: null }));
    run.pagesRead = null; run.findingsSoFar = null;
    /* notes-progress-report spec §6.3: each slot's final state, and the research notes the plan took in. */
    run.planning.step = "ready";
    run.planning.slots = planSlots(listed);
    run.planning.noteSlots = listed.filter((t): t is Md => !!t && isText((t as Md).note_id) && typeof (t as Md).title === "string")
      .map((t) => ({ noteId: t.note_id, title: t.title, state: "planned" as const }));
  },
```

`web/lib/run-state.ts` — replace

```ts
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
    run.outcomes.source_evaluator = plural(count(md.source_count), "source rated", "sources rated");
  },
```

with

```ts
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
    /* notes-progress-report spec §6.4: "{scored} sources rated · {s} strong · {f} fair · {w} weak". */
    if (typeof md.strong_count !== "number") { run.outcomes.source_evaluator = plural(count(md.source_count), "source rated", "sources rated"); return; }
    const unrated = count(md.unscored_cap_count) + count(md.unscored_provider_count) + count(md.unscored_missing_count);
    run.outcomes.source_evaluator = plural(count(md.scored_count), "source rated", "sources rated") + " · " + count(md.strong_count) + " strong · "
      + count(md.fair_count) + " fair · " + count(md.weak_count) + " weak" + (unrated > 0 ? " · " + unrated + " not rated" : "");
  },
```

`web/lib/run-state.ts` — replace

```ts
  "graph.report.reviewed": (run, md) => {
    const c = run.counters;
    c.reviewSeen = true;
    c.reviewScore = typeof md.mean_score === "number" ? md.mean_score : null;
  },
  "graph.route.decided": (run, md) => {
    run.loopPending = false;
    run.outcomes.report_reviewer = reviewOutcome(run, md);
```

with

```ts
  "graph.report.reviewed": (run, md, timestamp) => {
    const c = run.counters;
    c.reviewSeen = true;
    c.reviewScore = typeof md.mean_score === "number" ? md.mean_score : null;
    /* notes-progress-report spec §6.7: what Reviewing's checks and notes read -- never the score. */
    run.reviewing.landed = true;
    run.reviewing.reviewedAt = timestamp;
    run.reviewing.criteria = reviewCriteria(md.criteria);
    run.reviewing.notes = reviewNotes(md.notes);
    run.reviewing.defects = typeof md.material_defects === "number" ? md.material_defects : null;
  },
  "graph.route.decided": (run, md) => {
    run.loopPending = false;
    run.reviewing.reason = isText(md.reason) ? md.reason : null;
    run.reviewing.missing = missingCount(md);
    run.outcomes.report_reviewer = reviewOutcome(run, md);
```

`web/lib/run-state.ts` — replace

```ts
    note.where = run.active;
  },

```

with

```ts
    note.where = run.active;
    noteSlot(run, md);
  },

```

`web/lib/run-state.ts` — replace

```ts
  },
};
export function applyEvent(run: RunState, ev: RunEvent): void {
  const h = EVENT_HANDLERS[ev.type];
  if (h) h(run, ev.metadata || {});
}
```

with

```ts
  },
  /* notes-progress-report spec §6.1-§6.7: the four live-only progress events, each a cumulative
     snapshot (the latest wins), and the latest quality verdict's hard failures (§6.7's refusal line). */
  "planner.progress": (run, md) => {
    if (md.step !== "drafting" && md.step !== "checking" && md.step !== "fixing") return;
    run.planning.step = md.step;
    run.planning.round = count(md.check_round);
    run.planning.slots = planSlots(md.sub_topics);
  },
  "source_evaluator.progress": (run, md) => {
    run.evaluating = {
      toRate: count(md.to_rate), reused: count(md.reused), capped: count(md.capped), rated: count(md.rated),
      strong: count(md.strong), fair: count(md.fair), weak: count(md.weak), unrated: count(md.unrated),
      batches: count(md.batches), batchesDone: count(md.batches_done),
    };
  },
  "evidence_verifier.progress": (run, md) => {
    const before = run.verifying;
    const next = verifierSample(md.sample, (before?.sampleCount ?? 0) + 1);
    run.verifying = {
      total: count(md.total), checked: count(md.checked), verified: count(md.verified), corrected: count(md.corrected),
      quoted: count(md.quoted), dropped: count(md.dropped), batches: count(md.batches), batchesDone: count(md.batches_done),
      samples: next ? [...(before?.samples ?? []), next].slice(-2) : before?.samples ?? [],
      sampleCount: (before?.sampleCount ?? 0) + (next ? 1 : 0),
    };
  },
  "report_writer.progress": (run, md) => {
    const before = run.writing;
    const next = writerSample(md.sample, (before?.sampleCount ?? 0) + 1);
    run.writing = {
      phase: md.phase === "bottom_line" ? "bottom_line" : "sections",
      partsTotal: count(md.parts_total), partsReturned: count(md.parts_returned), drafted: count(md.sentences_drafted),
      checked: count(md.sentences_checked), backed: count(md.backed), removed: count(md.removed), unchecked: count(md.unchecked),
      fraction: Math.min(1, Math.max(before?.fraction ?? 0, typeof md.fraction === "number" ? md.fraction : 0)),
      samples: next ? [...(before?.samples ?? []), next].slice(-2) : before?.samples ?? [],
      sampleCount: (before?.sampleCount ?? 0) + (next ? 1 : 0),
    };
  },
  "graph.quality.assessed": (run, md) => {
    run.hardFailures = Array.isArray(md.hard_failures) ? md.hard_failures.filter(isText) : [];
  },
};
export function applyEvent(run: RunState, ev: RunEvent): void {
  const h = EVENT_HANDLERS[ev.type];
  if (h) h(run, ev.metadata || {}, typeof ev.timestamp === "string" && ev.timestamp ? ev.timestamp : null);
}
```

`web/lib/run-state.ts` — replace

```ts
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata }; }
```

with

```ts
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata, timestamp: event.timestamp }; }
```


- [ ] **Step 4: Run the tests to make sure they pass, then the whole web suite**

```bash
(cd web && npx vitest run test/run-state.test.ts test/progress-state.test.ts test/components/brief-spine.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npm run -s typecheck; echo "typecheck exit $?")
```

Expected: `Test Files  3 passed (3)`, `Tests  58 passed (58)`; `F0 + 1` files and `V0 + 16` tests passed (this task's 15 and Task 7's proxy test; on D + A + C: `34` / `292`); `typecheck exit 0`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/run-state.ts web/test/run-state.test.ts web/test/progress-state.test.ts web/test/components/brief-spine.test.tsx
git commit -m "feat(web): the run state keeps each step's progress, the rows' times and the latest hard failures"
```

---

### Task 10: Each step's own brief — the derivations, the ticker and the bodies (spec §6.3–§6.7; AC14–AC18)

**Files:**
- Modify: `web/lib/briefs.ts` (its imports; the bodies' block appended)
- Create: `web/lib/ticker.ts`, `web/components/StepBodies.tsx`, `web/test/step-briefs.test.ts`, `web/test/ticker.test.tsx`, `web/test/components/step-bodies.test.tsx`

**Interfaces:**
- Consumes: Task 9's state and types; `fmtSeconds` (`lib/format.ts`); `useTween` (`lib/tween.ts`).
- Produces:
  - `web/lib/briefs.ts` (additive): `CheckMark`, `SlotLine`, `CheckLine`, `TickerLine`, `StatusStack`, `EvaluatingStats`, `VerifyTally`, `WritingTally`, `BriefBody` (Writing's body carries its `placeholder`); `VERIFY_PLACEHOLDER`, `WRITING_PLACEHOLDER`, `WRITING_CHECKING_PLACEHOLDER` (O3), `SKELETON_WIDTHS`, `REVIEW_CRITERIA`, `ISSUE_WORDS`, `NOTE_RESULT_WORDS`, `SOURCE_WORDS`, `DROP_WORDS`; `elapsedText`, `planningStatus`, `planningSlots`, `evaluatingBody`, `verifierVerdict`, `sourceWords`, `verifyingBody`, `verifyTallyText`, `writerVerdict`, `writingBody`, `writingTallyText`, `issueText`, `listedNotes`, `noteMet`, `notesClause`, `criterionLines`, `reviewNoteLines`, `reviewVerdict`, `reviewingBody`, `liveSubtitle(run, id, clock: number | null)`.
  - `web/lib/ticker.ts`: `TICKER_HOLD_MS = 1200`, `useTicker<T extends { key: string }>(latest: T | null): { current: T | null; previous: T | null }`.
  - `web/components/StepBodies.tsx`: `Mark`, `StatusLine`, `PlanningSlots`, `EvaluatingLines`, `VerifyingLines`, `WritingLines`, `ReviewingLines` (each takes its body and `first`, the `--i` of its first line).

- [ ] **Step 1: Write the failing tests**

Create `web/test/step-briefs.test.ts`:

```ts
// @vitest-environment node — each step's own brief (notes-progress-report spec §6.3-§6.7), derived from
// synthesized events: pure functions of RunState and a clock.
import { describe, expect, it } from "vitest";
import {
  REVIEW_CRITERIA, elapsedText, evaluatingBody, issueText, liveSubtitle, notesClause, planningSlots, planningStatus,
  reviewVerdict, reviewingBody, sourceWords, verifierVerdict, verifyTallyText, verifyingBody, writerVerdict, writingBody,
  writingTallyText,
} from "../lib/briefs";
import { applyEvent, newRunState, type RunEvent, type RunState, type VerifierSample } from "../lib/run-state";

/* A fixed clock, ten minutes after every synthesized event's base time, so elapsed times are exact. */
const NOW = Date.UTC(2026, 8, 30, 12, 10, 0);
const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState { const run = newRunState(); for (const e of events) applyEvent(run, e); return run; }
const shown = (stack: { texts: string[]; on: number }) => stack.texts[stack.on];
const note = (id: string, restatement: string, kinds: string[], replaces: string | null = null) => [
  ev("session.note.received", { note_id: id, text: restatement }),
  ev("session.note.interpreted", { note_id: id, restatement, kinds, replaces, fallback: false }),
];

describe("elapsed times", () => {
  it("reads Xm SSs from a start to a clock, and nothing without either", () => {
    expect(elapsedText(at(0), Date.parse(at(442)))).toBe("7m 22s");
    expect(elapsedText(undefined, NOW)).toBeNull();
    expect(elapsedText(at(0), null)).toBeNull();
  });
});

describe("Planning (spec §6.3)", () => {
  const started = ev("graph.node.started", { node: "planner", iteration: 0 }, at(0));
  const slot = (coverage_id: string, title: string, state: string) => ({ coverage_id, title, state });
  it("reads the question, then shows four skeleton slots that say 'drafting' while the plan is drafted", () => {
    const run = play([started]);
    expect(shown(planningStatus(run))).toBe("Reading your question…");
    expect(liveSubtitle(run, "planner", NOW)).toBe("10m 00s");
    applyEvent(run, ev("planner.progress", { step: "drafting", check_round: 0, sub_topics: [] }));
    expect(shown(planningStatus(run))).toBe("Drafting a plan for your question…");
    expect(planningSlots(run, false).map((s) => [s.title, s.width, s.mark, s.fact, s.gone])).toEqual([
      [null, "78%", "waiting", "drafting", false], [null, "64%", "waiting", "drafting", false],
      [null, "72%", "waiting", "drafting", false], [null, "52%", "waiting", "drafting", false],
    ]);
  });
  it("says 'and your answers' when the one-time check gave answers", () => {
    const run = play([
      ev("session.clarification.answered", { reason: "answered", answers: [{ question_id: "q1", value: "Global", source: "chosen" }] }),
      started,
    ]);
    expect(shown(planningStatus(run))).toBe("Reading your question and your answers…");
  });
  it("fills slots with titles (a surplus skeleton fades out), checks, fixes, and reads the final states", () => {
    const run = play([started, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking"), slot("topic-02", "Beta", "checking"), slot("topic-03", "Gamma", "checking")] })]);
    expect(shown(planningStatus(run))).toBe("Checking the plan covers everything you asked…");
    expect(planningSlots(run, false).map((s) => [s.n, s.title, s.mark, s.fact, s.gone])).toEqual([
      [1, "Alpha", "running", "checking", false], [2, "Beta", "running", "checking", false],
      [3, "Gamma", "running", "checking", false], [4, null, "waiting", "", true],
    ]);
    applyEvent(run, ev("planner.progress", { step: "fixing", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), slot("topic-02", "Beta", "being_fixed"), slot("topic-03", "Gamma", "passed")] }));
    expect(shown(planningStatus(run))).toBe("Fixing 1 topic the check flagged…");
    expect(planningSlots(run, false).slice(0, 3).map((s) => [s.mark, s.fact])).toEqual([["done", ""], ["running", "being fixed"], ["done", ""]]);
    applyEvent(run, ev("planner.progress", { step: "checking", check_round: 2, sub_topics: [slot("topic-01", "Alpha", "checking")] }));
    expect(shown(planningStatus(run))).toBe("Checking the fixed plan…");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 3, sub_topics: [slot("topic-01", "Alpha", "passed"), slot("topic-02", "Beta", "fixed"), slot("topic-03", "Gamma", "flagged")] }));
    expect(shown(planningStatus(run))).toBe("Plan ready · research starts now");
    expect(planningSlots(run, false).slice(0, 3).map((s) => [s.mark, s.fact])).toEqual([["done", ""], ["done", "fixed"], ["fail", "still flagged"]]);
  });
  it("says 'Fixing what the check found…' when a repair names no topic, and 'not checked' with no verdict", () => {
    const run = play([started, ev("planner.progress", { step: "fixing", check_round: 0, sub_topics: [slot("topic-01", "Alpha", "drafted")] })]);
    expect(shown(planningStatus(run))).toBe("Fixing what the check found…");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "not_checked")] }));
    expect(planningSlots(run, false)[0]).toMatchObject({ mark: "waiting", fact: "not checked" });
  });
  it("gives a research note its own slot: 'joins the plan', then 'from your note' (D2)", () => {
    const run = play([started, ...note("n1", "pastries at the cafés", ["new_angle"])]);
    expect(planningSlots(run, false).at(-1)).toMatchObject({ key: "note-n1", n: 5, title: "Your note: pastries at the cafés", mark: "waiting", fact: "joins the plan", rise: true });
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), { coverage_id: "note-n1", title: "Your note: pastries at the cafés", note_id: "n1", state: "planned" }] }));
    expect(planningSlots(run, false).at(-1)).toMatchObject({ n: 2, mark: "done", fact: "from your note" });
  });
  it("reads a slot still running at the stop 'stopped', with the ring (§8.5)", () => {
    const run = play([started, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking")] })]);
    expect(planningSlots(run, true)[0]).toMatchObject({ mark: "stopped", fact: "stopped" });
  });
});

describe("Evaluating sources (spec §6.4)", () => {
  const progress = (md: Record<string, number>) => ev("source_evaluator.progress", { to_rate: 44, reused: 0, capped: 0, rated: 0, strong: 0, fair: 0, weak: 0, unrated: 0, batches: 4, batches_done: 0, ...md });
  it("says 'not yet' until the first batch lands, then the split, with a bar of (rated + unrated) / to_rate", () => {
    const run = play([progress({})]);
    expect(evaluatingBody(run)).toMatchObject({ lead: "Rating 44 sources for trustworthiness and relevance", stats: { rated: null, toRate: 44, strong: null, fair: null, weak: null } });
    expect(liveSubtitle(run, "source_evaluator", NOW)).toBe("starting · not yet rated");
    applyEvent(run, progress({ rated: 10, strong: 5, fair: 4, weak: 1, unrated: 2, batches_done: 1 }));
    const after = evaluatingBody(run);
    expect(after.stats).toEqual({ rated: 10, toRate: 44, strong: 5, fair: 4, weak: 1 });
    expect(after.bar).toBeCloseTo(12 / 44);
    expect(liveSubtitle(run, "source_evaluator", NOW)).toBe("10 of 44 rated");
  });
  it("names reused sources, and has no bar or split with nothing new to rate", () => {
    expect(evaluatingBody(play([progress({ to_rate: 1, reused: 3 })])).lead).toBe("Rating 1 new source · 3 already rated");
    const none = play([progress({ to_rate: 0, reused: 3, batches: 0 })]);
    expect(evaluatingBody(none)).toEqual({ kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null });
    expect(liveSubtitle(none, "source_evaluator", NOW)).toBe("nothing new to rate");
  });
});

describe("Verifying evidence (spec §6.5)", () => {
  const s = (over: Partial<VerifierSample>): VerifierSample => ({ seq: 1, text: "T", verdict: "verified", correction: null, dropReason: null, role: null, host: "eia.gov", ...over });
  it("words every verdict, the corrected ones by what the page changed and the dropped ones by why", () => {
    expect(verifierVerdict(s({}))).toBe("verified");
    expect(verifierVerdict(s({ verdict: "quoted" }))).toBe("quoted as written");
    const corrected = (field: string, value: string | null) => verifierVerdict(s({ verdict: "verified_corrected", correction: { field, value } }));
    expect(corrected("period", "2025")).toBe("corrected — the page dates it 2025");
    expect(corrected("period_cleared", null)).toBe("corrected — the page states no period for it");
    expect(corrected("scope", "all segments")).toBe("corrected — the page says it covers all segments");
    expect(corrected("subject", "Model A")).toBe("corrected — the page says it is about Model A");
    expect(corrected("kind", "forecast")).toBe("corrected — the page states it as a forecast");
    expect(corrected("kind", "actual")).toBe("corrected — the page states it as an actual");
    expect(corrected("figure", null)).toBe("corrected — one of its figures was not on the page");
    expect(verifierVerdict(s({ verdict: "verified_corrected" }))).toBe("corrected");
    const dropped = (reason: string) => verifierVerdict(s({ verdict: "dropped", dropReason: reason }));
    expect([dropped("read_not_found"), dropped("snippet_not_on_page"), dropped("evidence_not_on_page"), dropped("correction_not_on_page"), dropped("context_rejected"), dropped("context_unavailable")]).toEqual([
      "dropped — the page could not be read again", "dropped — the page does not say this", "dropped — the page does not show this figure",
      "dropped — the page does not back its date or scope", "dropped — the page's context does not support it", "dropped — its figures could not be checked",
    ]);
  });
  it("names the source by its role, else its host", () => {
    expect([sourceWords(s({ role: "original_report" })), sourceWords(s({ role: "independent_research" })), sourceWords(s({ role: "derivative" })),
      sourceWords(s({ role: "company_statement" })), sourceWords(s({ role: "mixed" })), sourceWords(s({ role: null, host: null }))])
      .toEqual(["an original report", "independent research", "a round-up of other sources", "the business's own words", "eia.gov", null]);
  });
  it("shows the last two samples, quotes a quoted one, marks a dropped one, and tallies", () => {
    const sample = (t: string, verdict: string) => ({ text: t, verdict, correction: null, drop_reason: verdict === "dropped" ? "snippet_not_on_page" : null, source: { role: "derivative", host: "x.org" } });
    const tally = { total: 10, checked: 4, verified: 2, corrected: 1, quoted: 0, dropped: 1, batches: 2, batches_done: 1 };
    const run = play([ev("evidence_verifier.progress", { ...tally, sample: sample("A", "quoted") }), ev("evidence_verifier.progress", { ...tally, sample: sample("B", "dropped") })]);
    const v = verifyingBody(run);
    expect(v.samples.map((l) => [l.key, l.text, l.quoted, l.kept, l.where])).toEqual([["v1", "A", true, true, "a round-up of other sources"], ["v2", "B", false, false, "a round-up of other sources"]]);
    expect(v.bar).toBe(0.4);
    expect(verifyTallyText(v.tally!)).toBe("4 of 10 checked · 2 verified · 1 corrected · 1 dropped");
    expect(liveSubtitle(run, "evidence_verifier", NOW)).toBe("4 of 10 checked");
  });
  it("waits on its placeholder before the first event, and says 'No findings to check' with none", () => {
    expect(verifyingBody(newRunState())).toEqual({ kind: "verifying", empty: null, samples: [], bar: 0, tally: null });
    expect(liveSubtitle(newRunState(), "evidence_verifier", NOW)).toBe("starting · not yet checked");
    const none = play([ev("evidence_verifier.progress", { total: 0, checked: 0, verified: 0, corrected: 0, quoted: 0, dropped: 0, batches: 0, batches_done: 0, sample: null })]);
    expect(verifyingBody(none).empty).toBe("No findings to check");
  });
});

describe("Writing report (spec §6.6)", () => {
  it("words a backed and a removed sentence", () => {
    expect(writerVerdict({ seq: 1, text: "T", verdict: "backed", findings: 1, section: "S" })).toBe("✓ backed by 1 finding");
    expect(writerVerdict({ seq: 1, text: "T", verdict: "backed", findings: 3, section: "S" })).toBe("✓ backed by 3 findings");
    expect(writerVerdict({ seq: 1, text: "T", verdict: "removed", findings: 2, section: "S" })).toBe("✗ removed — no verified finding says this");
  });
  it("tallies sentences as sections return, then says it is writing the bottom line", () => {
    const counts = { parts_total: 5, parts_returned: 2, sentences_drafted: 12, sentences_checked: 9, backed: 8, removed: 1, unchecked: 0, fraction: 0.3 };
    const run = play([ev("report_writer.progress", { phase: "sections", ...counts, sample: { text: "S.", verdict: "backed", findings: 2, section: "Where" } })]);
    const w = writingBody(run);
    expect(writingTallyText(w.tally!)).toBe("9 of 12 sentences checked · ✓ 8 backed · ✗ 1 removed · section 2 of 5");
    expect(w.samples[0]).toMatchObject({ verdict: "✓ backed by 2 findings", kept: true, where: "Where" });
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("2 of 5 sections written");
    applyEvent(run, ev("report_writer.progress", { phase: "bottom_line", ...counts, unchecked: 2, sample: null }));
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("writing the bottom line");
    expect(writingTallyText(writingBody(run).tally!)).toBe("9 of 12 sentences checked · ✓ 8 backed · ✗ 1 removed · section 2 of 5 · 2 not checked");
  });
  it("waits on 'The first section is being drafted…', then, once a section has returned, on 'The first sentences are being checked…'", () => {
    expect(writingBody(newRunState()).placeholder).toBe("The first section is being drafted…");
    const counts = { parts_total: 2, sentences_checked: 0, backed: 0, removed: 0, unchecked: 0, fraction: 0, sample: null };
    const none = play([ev("report_writer.progress", { phase: "sections", parts_returned: 0, sentences_drafted: 0, ...counts })]);
    expect(writingBody(none).placeholder).toBe("The first section is being drafted…");
    const returned = play([ev("report_writer.progress", { phase: "sections", parts_returned: 2, sentences_drafted: 4, ...counts })]);
    expect(writingBody(returned).placeholder).toBe("The first sentences are being checked…");
  });
  it("shows no tally before a sentence is drafted, and no section clause with no section to rewrite", () => {
    const run = play([ev("report_writer.progress", { phase: "sections", parts_total: 0, parts_returned: 0, sentences_drafted: 0, sentences_checked: 0, backed: 0, removed: 0, unchecked: 0, fraction: 0, sample: null })]);
    expect(writingBody(run).tally).toBeNull();
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("no section to rewrite");
    expect(writingTallyText({ checked: 1, drafted: 1, backed: 1, removed: 0, unchecked: 0, partsReturned: 0, partsTotal: 0 })).toBe("1 of 1 sentences checked · ✓ 1 backed · ✗ 0 removed");
  });
});

describe("Reviewing (spec §6.7)", () => {
  const crit = (failing: Record<string, string[]> = {}) => REVIEW_CRITERIA.map(({ dimension }) => ({ dimension, met: !failing[dimension], kinds: failing[dimension] ?? [] }));
  const start = ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0));
  const reviewed = (md: Record<string, unknown> = {}) => ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.87, material_defects: 0, criteria: crit(), notes: [], ...md }, at(95));
  const decided = (reason: string, missing: string[] = []) => ev("graph.route.decided", { destination: "finalize", reason, missing_required_target_ids: missing });
  it("reads while the single call runs: five rings, the indeterminate bar, the elapsed time", () => {
    const run = play([start]);
    const r = reviewingBody(run, false);
    expect(shown(r.status)).toBe("Reading the draft as a critical reader would · usually 1–3 min");
    expect(r.waiting).toBe(true);
    expect(r.criteria.map((c) => [c.text, c.mark, c.before, c.landed])).toEqual(REVIEW_CRITERIA.map((c) => [c.label, "waiting", "reading", false]));
    expect(liveSubtitle(run, "report_reviewer", NOW)).toBe("reading the draft · 10m 00s");
  });
  it("lands the checks with the issue in plain words -- no score anywhere -- and freezes the elapsed time", () => {
    const run = play([start, reviewed({ material_defects: 3, criteria: crit({ completeness: ["coverage"], uncertainty: ["contradiction", "contradiction"] }) })]);
    const r = reviewingBody(run, false);
    expect(r.waiting).toBe(false);
    expect(shown(r.status)).toBe("Review done · deciding what happens next…");
    expect(r.criteria.map((c) => [c.mark, c.fact])).toEqual([
      ["fail", "a part of your question has no answer"], ["done", ""], ["done", ""],
      ["fail", "2 issues · sources disagree and the draft does not say so"], ["done", ""],
    ]);
    expect(JSON.stringify(r)).not.toMatch(/0\.87/);
    expect(liveSubtitle(run, "report_reviewer", NOW)).toBe("reading the draft · 1m 35s");
  });
  it("reads 'not checked' for every criterion of a review that was not scored", () => {
    const run = play([start, reviewed({ review_status: "provider_failed", criteria: REVIEW_CRITERIA.map(({ dimension }) => ({ dimension, met: null, kinds: [] })) })]);
    expect(reviewingBody(run, false).criteria.every((c) => c.mark === "waiting" && c.fact === "not checked")).toBe(true);
  });
  it("lists the notes with their results: a mixed note with both, an unnamed research note 'researched next'", () => {
    const run = play([
      ...note("n1", "pastries at the cafés", ["new_angle"]),
      ...note("n2", "pastries, but skip closed cafés", ["new_angle", "exclude"]),
      ...note("n3", "more on safety", ["emphasis"]),
      ...note("n4", "a later research note", ["new_angle"]),
      ...note("n5", "a later steering note", ["scope"]),
      ...note("n6", "replaced", ["emphasis"]), ...note("n7", "the replacement", ["emphasis"], "n6"),
      start,
      reviewed({ notes: [
        { note_id: "n1", result: "met", reason: "covered" },
        { note_id: "n2", result: "met", reason: "covered", steering: { result: "not_met", reason: "ignored_with_evidence" } },
        { note_id: "n3", result: "met", reason: "honoured" },
        { note_id: "n7", result: "not_met", reason: "no_evidence" },
      ] }),
    ]);
    expect(reviewingBody(run, false).notes.map((n) => [n.text, n.mark, n.fact])).toEqual([
      ["Pastries at the cafés", "done", "covered"],
      ["Pastries, but skip closed cafés", "fail", "covered · not followed"],
      ["More on safety", "done", "honoured"],
      ["A later research note", "waiting", "researched next"],
      ["A later steering note", "waiting", "not checked"],
      ["The replacement", "fail", "no evidence found"],
    ]);
  });
  it("words the verdict line per route, with the notes clause on acceptance", () => {
    const verdict = (events: RunEvent[]) => reviewVerdict(play(events));
    expect(verdict([start, reviewed()])).toBeNull();
    expect(verdict([start, reviewed(), decided("report_accepted")])).toBe("Accepted · all 5 criteria met");
    const n1 = note("n1", "a", ["emphasis"]);
    expect(verdict([...n1, start, reviewed({ notes: [{ note_id: "n1", result: "met", reason: "honoured" }] }), decided("report_accepted")])).toBe("Accepted · all 5 criteria met · your note met");
    expect(verdict([...n1, start, reviewed({ notes: [{ note_id: "n1", result: "not_met", reason: "no_evidence" }] }), decided("report_accepted")])).toBe("Accepted · all 5 criteria met · your note not met");
    expect(verdict([start, reviewed({ material_defects: 1 }), decided("redraft_requested")])).toBe("1 thing to fix · sending the draft back to the writer");
    expect(verdict([start, reviewed(), decided("extra_pass_requested", ["a", "b"])])).toBe("2 gaps to fill · going back to research");
    expect(verdict([...n1, start, reviewed(), decided("note_pass_requested")])).toBe("Going back to research your note");
    expect(verdict([...n1, start, reviewed(), decided("note_redraft_requested")])).toBe("Sending the draft back to the writer for your note");
    expect(verdict([start, reviewed({ review_status: "provider_failed" }), decided("review_unavailable")])).toBe("The review could not be completed · publishing as partial");
    expect(verdict([start, reviewed({ criteria: crit({ readability: ["presentation"] }) }), decided("report_not_accepted")])).toBe("Not accepted · 4 of 5 met");
    expect(verdict([ev("graph.quality.assessed", { hard_failures: ["missing_evidence_ledger"] }), start, reviewed(), decided("report_not_accepted")])).toBe("Not accepted · a check the run makes itself failed");
    expect(verdict([start, reviewed(), decided("report_not_accepted")])).toBe("Not accepted · the reviewer's overall judgement fell short");
    expect(verdict([start, reviewed(), decided("extra_passes_exhausted", ["a"])])).toBe("Not accepted · 1 gap still open");
    expect(shown(reviewingBody(play([start, reviewed(), decided("report_accepted")]), false).status)).toBe("Accepted · all 5 criteria met");
  });
  it("counts notes for the clause: both, all, some", () => {
    expect([notesClause(0, 0), notesClause(2, 2), notesClause(3, 3), notesClause(1, 3), notesClause(0, 2)])
      .toEqual(["", " · both your notes met", " · all 3 of your notes met", " · 1 of your 3 notes met", " · 0 of your 2 notes met"]);
    expect(issueText(["identity"])).toBe("a source is credited to the wrong publisher");
  });
  it("freezes on 'stopped' when the reader stopped the run before the review landed", () => {
    const r = reviewingBody(play([start]), true);
    expect(r.waiting).toBe(false);
    expect(r.criteria[0]).toMatchObject({ mark: "waiting", before: "stopped", landed: false });
  });
});
```


Create `web/test/ticker.test.tsx`:

```tsx
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TICKER_HOLD_MS, useTicker } from "../lib/ticker";

afterEach(() => { vi.useRealTimers(); });

type Sample = { key: string };
const s = (key: string): Sample => ({ key });
const mount = (latest: Sample | null) =>
  renderHook(({ latest: l }: { latest: Sample | null }) => useTicker(l), { initialProps: { latest } });

describe("useTicker (notes-progress-report spec §6.5: at most one sample per 1,200 ms)", () => {
  it("holds for 1,200 ms", () => { expect(TICKER_HOLD_MS).toBe(1200); });
  it("shows the first sample at once and holds the next until 1,200 ms after it", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(null);
    expect(result.current).toEqual({ current: null, previous: null });
    rerender({ latest: s("a") });
    expect(result.current).toEqual({ current: s("a"), previous: null });
    act(() => { vi.advanceTimersByTime(300); });
    rerender({ latest: s("b") });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS - 301); });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current).toEqual({ current: s("b"), previous: s("a") });
  });
  it("ends a burst on its newest sample, as a replay would", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("a"));
    rerender({ latest: s("b") });
    rerender({ latest: s("c") });
    rerender({ latest: s("d") });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS); });
    expect(result.current).toEqual({ current: s("d"), previous: s("a") });
  });
  it("shows a sample at once when the hold has already passed", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS + 500); });
    rerender({ latest: s("b") });
    expect(result.current).toEqual({ current: s("b"), previous: s("a") });
  });
});
```


Create `web/test/components/step-bodies.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvaluatingLines, PlanningSlots, ReviewingLines, StatusLine, VerifyingLines, WritingLines } from "../../components/StepBodies";
import { VERIFY_PLACEHOLDER, verifyTallyText, writingTallyText, type CheckLine, type SlotLine, type TickerLine } from "../../lib/briefs";

const slot = (over: Partial<SlotLine>): SlotLine => ({ key: "slot-1", n: 1, title: null, width: "78%", mark: "waiting", fact: "", gone: false, rise: false, ...over });
const check = (over: Partial<CheckLine>): CheckLine => ({ key: "completeness", text: "Covers your whole question", mark: "waiting", before: "reading", fact: "", landed: false, ...over });
const tick = (over: Partial<TickerLine>): TickerLine => ({ key: "v1", text: "Revenue rose 4%", quoted: false, verdict: "verified", kept: true, where: "an original report", ...over });

describe("StepBodies (notes-progress-report spec §6.3-§6.7)", () => {
  it("StatusLine stacks every text in one cell and shows the `on`th", () => {
    const { container } = render(<StatusLine stack={{ texts: ["A", "B", "C"], on: 1 }} i={2} />);
    const line = container.querySelector(".ln.b-now.xf")!;
    expect([...line.children].map((p) => [p.textContent, p.getAttribute("data-on"), p.getAttribute("aria-hidden")]))
      .toEqual([["A", "0", "true"], ["B", "1", null], ["C", "0", "true"]]);
    expect(line.getAttribute("style")).toBe("--i: 2;");
  });
  it("PlanningSlots: a skeleton bar until the title, a gone row marked for its fade, a late row rising", () => {
    const { container } = render(<PlanningSlots first={1} slots={[
      slot({}),
      slot({ key: "slot-2", n: 2, title: "Beta", mark: "running", fact: "checking" }),
      slot({ key: "slot-3", n: 3, gone: true }),
      slot({ key: "note-n1", n: 3, title: "Your note: x", width: null, fact: "joins the plan", rise: true }),
    ]} />);
    const rows = [...container.querySelectorAll<HTMLElement>(".ps-topics.ps-slots > .ln")];
    expect(rows.map((r) => [r.getAttribute("data-topic"), r.getAttribute("data-gone"), r.getAttribute("data-rise"), r.querySelector(".tf")!.textContent, r.getAttribute("style")])).toEqual([
      ["waiting", null, null, "", "--i: 1;"], ["running", null, null, "checking", "--i: 2;"],
      ["waiting", "1", null, "", "--i: 3;"], ["waiting", null, "1", "joins the plan", "--i: 4;"],
    ]);
    expect(rows[0].querySelector(".sk")!.getAttribute("data-on")).toBe("1");
    expect(rows[0].querySelector<HTMLElement>(".sk")!.style.width).toBe("78%");
    expect(rows[1].querySelector(".sk")!.getAttribute("data-on")).toBe("0");
    expect(rows[1].querySelector(".tt")!.textContent).toBe("2Beta");
    expect(rows[2].getAttribute("aria-hidden")).toBe("true");
    expect(rows[0].querySelectorAll(".mk svg").length).toBe(2);
  });
  it("EvaluatingLines: 'not yet' until a batch lands, the bar as a scaleX, no bar or split with nothing to rate", () => {
    const { container, rerender } = render(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "Rating 44 sources for trustworthiness and relevance", bar: 0, stats: { rated: null, toRate: 44, strong: null, fair: null, weak: null } }} />);
    expect([...container.querySelectorAll(".stats > .stat")].map((s) => s.textContent)).toEqual(["Ratednot yet", "Strongnot yet", "Fairnot yet", "Weaknot yet"]);
    expect(container.querySelector<HTMLElement>(".pb > i")!.style.transform).toBe("scaleX(0)");
    rerender(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "Rating 44 sources for trustworthiness and relevance", bar: 0.25, stats: { rated: 10, toRate: 44, strong: 5, fair: 4, weak: 1 } }} />);
    expect([...container.querySelectorAll(".stats > .stat .v")].map((s) => s.textContent)).toEqual(["10 of 44", "5", "4", "1"]);
    expect(container.querySelector<HTMLElement>(".pb > i")!.style.transform).toBe("scaleX(0.25)");
    rerender(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null }} />);
    expect(container.querySelector(".pb")).toBeNull();
    expect(container.querySelector(".stats")).toBeNull();
    expect(container.textContent).toBe("No new sources to rate");
  });
  it("VerifyingLines: the placeholder until a sample, then the sample and its verdict; the tally reads as verifyTallyText", () => {
    const tally = { checked: 4, total: 10, verified: 2, corrected: 1, dropped: 1 };
    const { container, rerender } = render(<VerifyingLines first={0} body={{ kind: "verifying", empty: null, samples: [], bar: 0, tally: null }} />);
    const placeholder = container.querySelector(".tickbox .b-sub")!;
    expect([placeholder.textContent, placeholder.getAttribute("data-on")]).toEqual([VERIFY_PLACEHOLDER, "1"]);
    expect(container.querySelector(".b-facts")).toBeNull();
    rerender(<VerifyingLines first={0} body={{ kind: "verifying", empty: null, samples: [tick({ quoted: true, verdict: "quoted as written" })], bar: 0.4, tally }} />);
    expect(container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")).toBe("0");
    expect(container.querySelector(".tickbox [data-on='1'] .qt")!.textContent).toBe("“Revenue rose 4%”");
    expect(container.querySelector(".tickbox [data-on='1'] .vd")!.textContent).toBe("quoted as written · an original report");
    expect(container.querySelector(".b-facts")!.textContent).toBe(verifyTallyText(tally));
    rerender(<VerifyingLines first={0} body={{ kind: "verifying", empty: "No findings to check", samples: [], bar: 0, tally: null }} />);
    expect(container.textContent).toBe("No findings to check");
  });
  it("WritingLines: the body's placeholder until a sample, a removed sentence not kept, the tally as writingTallyText", () => {
    const tally = { checked: 9, drafted: 12, backed: 8, removed: 1, unchecked: 2, partsReturned: 2, partsTotal: 5 };
    const placeholder = "The first sentences are being checked…";
    const { container, rerender } = render(<WritingLines first={0} body={{ kind: "writing", placeholder, samples: [], bar: 0.1, tally }} />);
    expect([container.querySelector(".tickbox .b-sub")!.textContent, container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")]).toEqual([placeholder, "1"]);
    rerender(<WritingLines first={0} body={{ kind: "writing", placeholder, samples: [tick({ key: "w1", verdict: "✗ removed — no verified finding says this", kept: false, where: "Where" })], bar: 0.3, tally }} />);
    expect(container.querySelector(".b-facts")!.textContent).toBe(writingTallyText(tally));
    expect(container.querySelector(".tickbox .vd")!.getAttribute("data-kept")).toBe("0");
  });
  it("ReviewingLines: the indeterminate bar while waiting, five checks revealed in order, the notes under their own heading", () => {
    const criteria = ["a", "b", "c", "d", "e"].map((key) => check({ key }));
    const status = (on: number) => ({ texts: ["Reading", "Done", ""], on });
    const { container, rerender } = render(<ReviewingLines first={0} body={{ kind: "reviewing", status: status(0), waiting: true, criteria, notes: [] }} />);
    expect(container.querySelector(".pb.ind")!.getAttribute("data-on")).toBe("1");
    expect([...container.querySelectorAll(".rv-list > .ln")].map((r) => r.getAttribute("style")))
      .toEqual(["--i: 2; --r: 0;", "--i: 3; --r: 1;", "--i: 4; --r: 2;", "--i: 5; --r: 3;", "--i: 6; --r: 4;"]);
    expect(container.querySelector(".rv-notes-h")).toBeNull();
    rerender(<ReviewingLines first={0} body={{
      kind: "reviewing", status: status(1), waiting: false,
      criteria: [check({ key: "a", mark: "fail", fact: "a part of your question has no answer", landed: true }), ...criteria.slice(1).map((c) => ({ ...c, mark: "done" as const, landed: true }))],
      notes: [check({ key: "n1", text: "More on safety", mark: "done", fact: "honoured", landed: true })],
    }} />);
    expect(container.querySelector(".pb.ind")!.getAttribute("data-on")).toBe("0");
    const first = container.querySelector(".rv-list > .ln")!;
    expect(first.getAttribute("data-topic")).toBe("fail");
    expect([...first.querySelectorAll(".tf > span")].map((s) => [s.textContent, s.getAttribute("data-on")])).toEqual([["reading", "0"], ["a part of your question has no answer", "1"]]);
    expect(container.querySelector(".rv-notes-h")!.textContent).toBe("Your notes");
    expect(container.querySelector("[aria-label='Your notes'] > .ln")!.getAttribute("style")).toBe("--i: 8; --r: 5;");
  });
});
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/step-briefs.test.ts test/ticker.test.tsx test/components/step-bodies.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  3 failed (3)` (none of the derivations, `lib/ticker.ts` or `components/StepBodies.tsx` exists yet).

- [ ] **Step 3: Write the implementation**

`web/lib/briefs.ts` — replace

```ts
import { earlierNotesText, visibleAcks, type Ack } from "./notes";
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";
```

with

```ts
import { fmtSeconds } from "./format";
import { earlierNotesText, visibleAcks, type Ack } from "./notes";
import {
  STAGES, countPhrase, notAcceptedLine, plural,
  type NodeId, type NoteState, type PaintedMark, type ReopenLine, type ReviewHalf, type ReviewNoteResult,
  type RunState, type SlotState, type Topic, type TopicState, type VerifierSample, type WriterSample,
} from "./run-state";
```

Append to `web/lib/briefs.ts`:

```ts

/* ═══ notes-progress-report spec §6.3-§6.7: each step's own brief ═══ */
/* What a checklist row's `.mk` draws (its data-topic): ○ waiting, ● running, ✓ done, amber ✗ fail;
   "stopped" is a slot that was running when the reader stopped the run — the ring, as Researching's. */
export type CheckMark = "waiting" | "running" | "done" | "fail" | "stopped";
/* One Planning slot: a skeleton bar until its title is known (`title` null); `gone` for a surplus
   skeleton the plan's titles leave over, which fades out; `rise` for a row that arrives later. */
export interface SlotLine { key: string; n: number; title: string | null; width: string | null; mark: CheckMark; fact: string; gone: boolean; rise: boolean }
/* One Reviewing row: a criterion or a note, `before` while the review runs, `fact` once it landed. */
export interface CheckLine { key: string; text: string; mark: CheckMark; before: string; fact: string; landed: boolean }
/* One ticker sample: the finding or the drafted sentence, its verdict's words, where it came from. */
export interface TickerLine { key: string; text: string; quoted: boolean; verdict: string; kept: boolean; where: string | null }
/* A cross-fading status line: every text in one grid cell, the `on`th one shown. */
export interface StatusStack { texts: string[]; on: number }
export interface EvaluatingStats { rated: number | null; toRate: number; strong: number | null; fair: number | null; weak: number | null }
export interface VerifyTally { checked: number; total: number; verified: number; corrected: number; dropped: number }
export interface WritingTally { checked: number; drafted: number; backed: number; removed: number; unchecked: number; partsReturned: number; partsTotal: number }
export type BriefBody =
  | { kind: "planning"; status: StatusStack; slots: SlotLine[] }
  | { kind: "research"; topics: TopicLine[] }
  | { kind: "evaluating"; lead: string; bar: number | null; stats: EvaluatingStats | null }
  | { kind: "verifying"; empty: string | null; samples: TickerLine[]; bar: number; tally: VerifyTally | null }
  | { kind: "writing"; placeholder: string; samples: TickerLine[]; bar: number; tally: WritingTally | null }
  | { kind: "reviewing"; status: StatusStack; waiting: boolean; criteria: CheckLine[]; notes: CheckLine[] }
  | { kind: "sentence"; text: string };
export const VERIFY_PLACEHOLDER = "The first findings are being checked…";
export const WRITING_PLACEHOLDER = "The first section is being drafted…";
/* §6.6: once a section has returned, until the first sentence comes back checked. */
export const WRITING_CHECKING_PLACEHOLDER = "The first sentences are being checked…";
/* §6.3: four skeleton slots before the plan's titles; the count is a placeholder, not a claim. */
export const SKELETON_WIDTHS: readonly string[] = ["78%", "64%", "72%", "52%"];
/* §6.7, D23, D35: the five criteria a review can mark not met, in DIMENSION_GUIDANCE order. */
export const REVIEW_CRITERIA: readonly { dimension: string; label: string }[] = [
  { dimension: "completeness", label: "Covers your whole question" },
  { dimension: "evidence_quality", label: "Rests on strong evidence" },
  { dimension: "attribution", label: "Every claim is credited correctly" },
  { dimension: "uncertainty", label: "Honest about what is uncertain" },
  { dimension: "readability", label: "Easy to read" },
];
export const ISSUE_WORDS: Readonly<Record<string, string>> = {
  coverage: "a part of your question has no answer",
  mechanism: "a step in the explanation is missing",
  missing_support: "a sentence says more than its sources",
  acquisition: "a needed source could not be read",
  source_quality: "a claim rests on a weak source",
  freshness: "a figure is out of date",
  identity: "a source is credited to the wrong publisher",
  contradiction: "sources disagree and the draft does not say so",
  semantic_duplicate: "the same point is made twice",
  presentation: "a sentence is hard to follow",
};
export const NOTE_RESULT_WORDS: Readonly<Record<string, string>> = {
  covered: "covered", not_found: "not found", to_research: "researched next",
  honoured: "honoured", ignored_with_evidence: "not followed", no_evidence: "no evidence found", not_judged: "not checked",
};
export const SOURCE_WORDS: Readonly<Record<string, string>> = {
  original_report: "an original report", independent_research: "independent research",
  derivative: "a round-up of other sources", company_statement: "the business's own words",
};
export const DROP_WORDS: Readonly<Record<string, string>> = {
  read_not_found: "the page could not be read again",
  snippet_not_on_page: "the page does not say this",
  evidence_not_on_page: "the page does not show this figure",
  correction_not_on_page: "the page does not back its date or scope",
  context_rejected: "the page's context does not support it",
  context_unavailable: "its figures could not be checked",
};
const CORRECTION_WORDS: Readonly<Record<string, (value: string | null) => string>> = {
  period: (v) => "the page dates it " + (v ?? ""),
  period_cleared: () => "the page states no period for it",
  scope: (v) => "the page says it covers " + (v ?? ""),
  subject: (v) => "the page says it is about " + (v ?? ""),
  kind: (v) => (v === "forecast" ? "the page states it as a forecast" : "the page states it as an actual"),
  figure: () => "one of its figures was not on the page",
};
const SLOT_MARK: Readonly<Record<SlotState, CheckMark>> = {
  skeleton: "waiting", drafted: "waiting", checking: "running", being_fixed: "running",
  passed: "done", fixed: "done", flagged: "fail", not_checked: "waiting",
};
const SLOT_FACT: Readonly<Record<SlotState, string>> = {
  skeleton: "", drafted: "", checking: "checking", being_fixed: "being fixed",
  passed: "", fixed: "fixed", flagged: "still flagged", not_checked: "not checked",
};

const capitalise = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);
/* `Xm SSs` from an ISO start to `toMs`; null with no start or no clock. */
export function elapsedText(fromIso: string | undefined, toMs: number | null): string | null {
  if (!fromIso || toMs === null) return null;
  const from = Date.parse(fromIso);
  return Number.isNaN(from) ? null : fmtSeconds(Math.max(0, (toMs - from) / 1000));
}
const researchSubtitle = (run: RunState): Subtitle => ({
  kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
  pages: run.pagesRead ?? 0, findings: run.findingsSoFar ?? 0,
});

/* §6.3: the status line. */
export function planningStatus(run: RunState): StatusStack {
  const p = run.planning;
  const answered = (run.clarify?.answered?.answers.length ?? 0) > 0;
  const fixing = p.slots.filter((s) => s.state === "being_fixed").length;
  return {
    texts: [
      answered ? "Reading your question and your answers…" : "Reading your question…",
      "Drafting a plan for your question…",
      p.round === 2 ? "Checking the fixed plan…" : "Checking the plan covers everything you asked…",
      fixing > 0 ? "Fixing " + plural(fixing, "topic", "topics") + " the check flagged…" : "Fixing what the check found…",
      "Plan ready · research starts now",
    ],
    on: ({ reading: 0, drafting: 1, checking: 2, fixing: 3, ready: 4 } as const)[p.step],
  };
}
/* §6.3: the slots — skeletons before the titles, then one per topic, then one per research note. A slot
   still running when the reader stopped the run reads "stopped", with the ring (§8.5). */
export function planningSlots(run: RunState, stopped: boolean): SlotLine[] {
  const p = run.planning;
  const lines: SlotLine[] = [];
  if (p.slots.length === 0) {
    SKELETON_WIDTHS.forEach((width, i) => lines.push({
      key: "slot-" + (i + 1), n: i + 1, title: null, width, mark: "waiting",
      fact: p.step === "drafting" && !stopped ? "drafting" : "", gone: false, rise: false,
    }));
  } else {
    p.slots.forEach((slot, i) => {
      const halted = stopped && SLOT_MARK[slot.state] === "running";
      lines.push({
        key: "slot-" + (i + 1), n: i + 1, title: slot.title, width: SKELETON_WIDTHS[i] ?? null,
        mark: halted ? "stopped" : SLOT_MARK[slot.state], fact: halted ? "stopped" : SLOT_FACT[slot.state],
        gone: false, rise: i >= SKELETON_WIDTHS.length,
      });
    });
    for (let i = p.slots.length; i < SKELETON_WIDTHS.length; i++) {
      lines.push({ key: "slot-" + (i + 1), n: i + 1, title: null, width: SKELETON_WIDTHS[i], mark: "waiting", fact: "", gone: true, rise: false });
    }
  }
  const shown = lines.filter((line) => !line.gone).length;
  p.noteSlots.forEach((slot, i) => lines.push({
    key: "note-" + slot.noteId, n: shown + i + 1, title: slot.title, width: null,
    mark: slot.state === "planned" ? "done" : "waiting", fact: slot.state === "planned" ? "from your note" : "joins the plan",
    gone: false, rise: true,
  }));
  return lines;
}

/* §6.4 */
export function evaluatingBody(run: RunState): Extract<BriefBody, { kind: "evaluating" }> {
  const e = run.evaluating;
  if (e === null) {
    return { kind: "evaluating", lead: "Rating sources for trustworthiness and relevance", bar: 0,
      stats: { rated: null, toRate: 0, strong: null, fair: null, weak: null } };
  }
  if (e.toRate === 0) return { kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null };
  const lead = e.reused > 0
    ? "Rating " + plural(e.toRate, "new source", "new sources") + " · " + e.reused + " already rated"
    : "Rating " + plural(e.toRate, "source", "sources") + " for trustworthiness and relevance";
  const landed = e.batchesDone > 0;
  return {
    kind: "evaluating", lead, bar: Math.min(1, (e.rated + e.unrated) / e.toRate),
    stats: { rated: landed ? e.rated : null, toRate: e.toRate, strong: landed ? e.strong : null, fair: landed ? e.fair : null, weak: landed ? e.weak : null },
  };
}

/* §6.5 */
export function verifierVerdict(sample: VerifierSample): string {
  if (sample.verdict === "verified") return "verified";
  if (sample.verdict === "quoted") return "quoted as written";
  if (sample.verdict === "verified_corrected") {
    const words = sample.correction ? CORRECTION_WORDS[sample.correction.field] : undefined;
    return words ? "corrected — " + words(sample.correction!.value) : "corrected";
  }
  const reason = sample.dropReason ? DROP_WORDS[sample.dropReason] : undefined;
  return reason ? "dropped — " + reason : "dropped";
}
export function sourceWords(sample: VerifierSample): string | null {
  return (sample.role ? SOURCE_WORDS[sample.role] : undefined) ?? sample.host;
}
const verifierLine = (s: VerifierSample): TickerLine => ({
  key: "v" + s.seq, text: s.text, quoted: s.verdict === "quoted", verdict: verifierVerdict(s), kept: s.verdict !== "dropped", where: sourceWords(s),
});
export function verifyingBody(run: RunState): Extract<BriefBody, { kind: "verifying" }> {
  const v = run.verifying;
  if (v === null) return { kind: "verifying", empty: null, samples: [], bar: 0, tally: null };
  if (v.total === 0) return { kind: "verifying", empty: "No findings to check", samples: [], bar: 0, tally: null };
  return {
    kind: "verifying", empty: null, samples: v.samples.map(verifierLine), bar: Math.min(1, v.checked / v.total),
    tally: { checked: v.checked, total: v.total, verified: v.verified, corrected: v.corrected, dropped: v.dropped },
  };
}
export function verifyTallyText(t: VerifyTally): string {
  return t.checked + " of " + t.total + " checked · " + t.verified + " verified · " + t.corrected + " corrected · " + t.dropped + " dropped";
}

/* §6.6 */
export function writerVerdict(sample: WriterSample): string {
  return sample.verdict === "backed" ? "✓ backed by " + plural(sample.findings, "finding", "findings") : "✗ removed — no verified finding says this";
}
const writerLine = (s: WriterSample): TickerLine => ({
  key: "w" + s.seq, text: s.text, quoted: false, verdict: writerVerdict(s), kept: s.verdict === "backed", where: s.section,
});
export function writingBody(run: RunState): Extract<BriefBody, { kind: "writing" }> {
  const w = run.writing;
  if (w === null) return { kind: "writing", placeholder: WRITING_PLACEHOLDER, samples: [], bar: 0, tally: null };
  return {
    kind: "writing", placeholder: w.partsReturned > 0 ? WRITING_CHECKING_PLACEHOLDER : WRITING_PLACEHOLDER,
    samples: w.samples.map(writerLine), bar: w.fraction,
    tally: w.drafted > 0
      ? { checked: w.checked, drafted: w.drafted, backed: w.backed, removed: w.removed, unchecked: w.unchecked, partsReturned: w.partsReturned, partsTotal: w.partsTotal }
      : null,
  };
}
export function writingTallyText(t: WritingTally): string {
  return t.checked + " of " + t.drafted + " sentences checked · ✓ " + t.backed + " backed · ✗ " + t.removed + " removed"
    + (t.partsTotal > 0 ? " · section " + t.partsReturned + " of " + t.partsTotal : "")
    + (t.unchecked > 0 ? " · " + t.unchecked + " not checked" : "");
}

/* §6.7 */
export function issueText(kinds: readonly string[]): string {
  const first = ISSUE_WORDS[kinds[0] ?? ""] ?? "an issue was raised";
  return kinds.length > 1 ? kinds.length + " issues · " + first : first;
}
/* Every interpreted note no later note replaces, in receipt order: the notes Reviewing lists. */
export function listedNotes(notes: readonly NoteState[]): NoteState[] {
  const replaced = new Set(notes.map((n) => n.replaces).filter((id): id is string => !!id));
  return notes.filter((n) => n.interpreted && !replaced.has(n.id));
}
export function noteMet(result: ReviewNoteResult | undefined): boolean {
  return !!result && result.result === "met" && (!result.steering || result.steering.result === "met");
}
export function notesClause(met: number, n: number): string {
  if (n === 0) return "";
  if (n === 1) return met === 1 ? " · your note met" : " · your note not met";
  if (n === 2 && met === 2) return " · both your notes met";
  if (met === n) return " · all " + n + " of your notes met";
  return " · " + met + " of your " + n + " notes met";
}
export function criterionLines(run: RunState, stopped: boolean): CheckLine[] {
  const r = run.reviewing;
  const before = stopped && !r.landed ? "stopped" : "reading";
  return REVIEW_CRITERIA.map(({ dimension, label }) => {
    if (!r.landed) return { key: dimension, text: label, mark: "waiting", before, fact: "", landed: false };
    const found = r.criteria?.find((c) => c.dimension === dimension);
    if (!found || found.met === null) return { key: dimension, text: label, mark: "waiting", before, fact: "not checked", landed: true };
    return found.met
      ? { key: dimension, text: label, mark: "done", before, fact: "", landed: true }
      : { key: dimension, text: label, mark: "fail", before, fact: issueText(found.kinds), landed: true };
  });
}
const halfMark = (h: ReviewHalf): CheckMark => (h.result === "met" ? "done" : h.result === "not_met" ? "fail" : "waiting");
const halfWords = (h: ReviewHalf): string => NOTE_RESULT_WORDS[h.reason] ?? h.reason;
export function reviewNoteLines(run: RunState, stopped: boolean): CheckLine[] {
  const r = run.reviewing;
  const before = stopped && !r.landed ? "stopped" : "reading";
  return listedNotes(run.notes).map((note) => {
    const text = capitalise(note.restatement ?? note.text);
    if (!r.landed) return { key: note.id, text, mark: "waiting", before, fact: "", landed: false };
    const result = r.notes.find((x) => x.noteId === note.id);
    if (!result) {
      /* §6.7: a note the review input never carried; a research note is then owed its pass. */
      return { key: note.id, text, mark: "waiting", before, fact: note.kinds.includes("new_angle") ? "researched next" : "not checked", landed: true };
    }
    if (!result.steering) return { key: note.id, text, mark: halfMark(result), before, fact: halfWords(result), landed: true };
    const marks = [halfMark(result), halfMark(result.steering)];
    const mark: CheckMark = marks.includes("fail") ? "fail" : marks.every((m) => m === "done") ? "done" : "waiting";
    return { key: note.id, text, mark, before, fact: halfWords(result) + " · " + halfWords(result.steering), landed: true };
  });
}
/* §6.7's route table: the verdict line, once the route is decided. */
export function reviewVerdict(run: RunState): string | null {
  const r = run.reviewing;
  const listed = listedNotes(run.notes);
  const met = listed.filter((n) => noteMet(r.notes.find((x) => x.noteId === n.id))).length;
  const notes = listed.length === 1 ? "note" : "notes";
  switch (r.reason) {
    case null: return null;
    case "report_accepted": return "Accepted · all 5 criteria met" + notesClause(met, listed.length);
    case "redraft_requested": return plural(r.defects ?? 0, "thing", "things") + " to fix · sending the draft back to the writer";
    case "extra_pass_requested": return plural(r.missing, "gap", "gaps") + " to fill · going back to research";
    case "note_pass_requested": return "Going back to research your " + notes;
    case "note_redraft_requested": return "Sending the draft back to the writer for your " + notes;
    case "review_unavailable": return "The review could not be completed · publishing as partial";
    case "report_not_accepted": return notAcceptedLine(run);
    case "extra_passes_exhausted": return "Not accepted · " + plural(r.missing, "gap", "gaps") + " still open";
    default: return null;
  }
}
export function reviewingBody(run: RunState, stopped: boolean): Extract<BriefBody, { kind: "reviewing" }> {
  const r = run.reviewing;
  const verdict = reviewVerdict(run);
  return {
    kind: "reviewing",
    status: {
      texts: ["Reading the draft as a critical reader would · usually 1–3 min", "Review done · deciding what happens next…", verdict ?? ""],
      on: verdict !== null ? 2 : r.landed ? 1 : 0,
    },
    waiting: !r.landed && !stopped,
    criteria: criterionLines(run, stopped),
    notes: reviewNoteLines(run, stopped),
  };
}

/* A row's live facts line (§6.3-§6.7) as text, or null when it has none. `clock` is now, or the
   moment the reader stopped the run (null when the stop's time is not known). */
export function liveSubtitle(run: RunState, id: NodeId, clock: number | null): string | null {
  switch (id) {
    case "planner": return elapsedText(run.startedAt.planner, clock);
    case "researcher": return run.topics.length > 0 ? subtitleText(researchSubtitle(run)) : null;
    case "source_evaluator": {
      const e = run.evaluating;
      if (e === null) return "starting · not yet rated";
      if (e.toRate === 0) return "nothing new to rate";
      return e.batchesDone === 0 ? "starting · not yet rated" : e.rated + " of " + e.toRate + " rated";
    }
    case "evidence_verifier": {
      const v = run.verifying;
      if (v === null) return "starting · not yet checked";
      return v.total === 0 ? "nothing to check" : v.checked + " of " + v.total + " checked";
    }
    case "report_writer": {
      const w = run.writing;
      if (w === null) return "starting · not yet written";
      if (w.phase === "bottom_line") return "writing the bottom line";
      return w.partsTotal === 0 ? "no section to rewrite" : w.partsReturned + " of " + w.partsTotal + " sections written";
    }
    case "report_reviewer": {
      // The elapsed time stops when the review lands: what follows is the route decision, not the call.
      const landed = run.reviewing.landed && run.reviewing.reviewedAt ? Date.parse(run.reviewing.reviewedAt) : NaN;
      const elapsed = elapsedText(run.startedAt.report_reviewer, Number.isNaN(landed) ? clock : landed);
      return elapsed === null ? "reading the draft" : "reading the draft · " + elapsed;
    }
    default: return null;
  }
}
```


Create `web/lib/ticker.ts`:

```ts
// notes-progress-report spec §6.5: a ticker's visible sample changes at most once every
// TICKER_HOLD_MS. A dwell, like HANDOFF_HOLD_MS (components/BriefSpine.tsx), not an animation
// duration, so it holds under reduced motion too. A sample that arrives sooner waits; when the hold
// ends the newest waiting sample shows, so a burst and a replay end on the same sample.
import { useEffect, useRef, useState } from "react";

export const TICKER_HOLD_MS = 1200;

export interface Ticked<T> { current: T | null; previous: T | null }

/* The sample to show now and the one it replaced (which fades out under it). The first sample
   shows at once; each later one shows TICKER_HOLD_MS after the one before it, or at once when that
   long has already passed. */
export function useTicker<T extends { key: string }>(latest: T | null): Ticked<T> {
  const [shown, setShown] = useState<Ticked<T>>({ current: latest, previous: null });
  const since = useRef<number>(Date.now());
  useEffect(() => {
    if (latest === null || latest.key === shown.current?.key) return;
    const show = () => {
      since.current = Date.now();
      setShown((s) => ({ current: latest, previous: s.current }));
    };
    const wait = shown.current === null ? 0 : Math.max(0, TICKER_HOLD_MS - (Date.now() - since.current));
    if (wait === 0) { show(); return; }
    const timer = setTimeout(show, wait);
    return () => clearTimeout(timer);
  }, [latest, shown]);
  return shown;
}
```


Create `web/components/StepBodies.tsx`:

```tsx
"use client";
// The step bodies of the running spine's briefs (notes-progress-report spec §6.3-§6.7). Each renders
// one step's `.ln` lines, numbered from `first` so a brief keeps one stagger (live-briefs pick 1A).
import type { CSSProperties } from "react";
import {
  VERIFY_PLACEHOLDER,
  type BriefBody, type CheckLine, type SlotLine, type StatusStack, type TickerLine,
} from "@/lib/briefs";
import { useTicker } from "@/lib/ticker";
import { useTween } from "@/lib/tween";

const lineStyle = (i: number, r?: number) =>
  ({ ["--i" as string]: String(i), ...(r === undefined ? {} : { ["--r" as string]: String(r) }) }) as CSSProperties;

/* ✓ drawn, ● running, ○ waiting, amber ✗: one 14px slot, so the column never shifts. */
export function Mark() {
  return (
    <span className="mk" aria-hidden="true">
      <span className="ring" /><span className="dotc" />
      <svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg>
      <svg className="x" viewBox="0 0 14 14"><path d="M4 4 L10 10" /><path d="M10 4 L4 10" /></svg>
    </span>
  );
}

/* A status line: every text stacked in one grid cell, the `on`th one shown (the others cross-fade out). */
export function StatusLine({ stack, i }: { stack: StatusStack; i: number }) {
  return (
    <div className="ln b-now xf rise" style={lineStyle(i)}>
      {stack.texts.map((text, k) => (
        <p key={k} data-on={k === stack.on ? "1" : "0"} aria-hidden={k === stack.on ? undefined : "true"}>{text}</p>
      ))}
    </div>
  );
}

/* §6.3: Planning's slots — a skeleton bar that cross-fades to its title, a mark and a fact. */
export function PlanningSlots({ slots, first }: { slots: SlotLine[]; first: number }) {
  return (
    <div className="ps-topics ps-slots" role="list">
      {slots.map((slot, k) => (
        <div key={slot.key} className="ln" role="listitem" data-topic={slot.mark} data-gone={slot.gone ? "1" : undefined}
          data-rise={slot.rise ? "1" : undefined} aria-hidden={slot.gone ? "true" : undefined} style={lineStyle(first + k)}>
          <Mark />
          <span className="tt xf rise">
            <span className="sk" data-on={slot.title === null ? "1" : "0"} style={slot.width ? { width: slot.width } : undefined} />
            <span data-on={slot.title === null ? "0" : "1"}>{slot.title === null ? null : <><span className="tn">{slot.n}</span>{slot.title}</>}</span>
          </span>
          <span className="tf">{slot.fact}</span>
        </div>
      ))}
    </div>
  );
}

function Count({ value }: { value: number }) { return <>{useTween(value)}</>; }

/* §6.4: the lead, a determinate bar and the Rated / Strong / Fair / Weak split. */
export function EvaluatingLines({ body, first }: { body: Extract<BriefBody, { kind: "evaluating" }>; first: number }) {
  const s = body.stats;
  const stat = (label: string, value: number | null, of?: number) => (
    <div className="stat"><span className="eyebrow">{label}</span><span className="v">{value === null ? "not yet" : <><Count value={value} />{of === undefined ? "" : " of " + of}</>}</span></div>
  );
  return (
    <>
      <p className="ln b-now" style={lineStyle(first)}>{body.lead}</p>
      {body.bar === null ? null : <span className="ln pb" style={lineStyle(first + 1)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>}
      {s === null ? null : (
        <div className="ln stats" style={lineStyle(first + 2)}>
          {stat("Rated", s.rated, s.toRate)}{stat("Strong", s.strong)}{stat("Fair", s.fair)}{stat("Weak", s.weak)}
        </div>
      )}
    </>
  );
}

/* §6.5, §6.6: the ticker box — the last two samples stacked, the newest showing, paced by useTicker. */
function TickerBox({ samples, placeholder, i }: { samples: TickerLine[]; placeholder: string; i: number }) {
  const { current, previous } = useTicker(samples.at(-1) ?? null);
  return (
    <div className="ln tickbox" style={lineStyle(i)}>
      <div className="xf rise">
        <p className="b-sub" data-on={current ? "0" : "1"} aria-hidden={current ? "true" : undefined}>{placeholder}</p>
        {[previous, current].map((line) => line === null ? null : (
          <div key={line.key} data-on={line === current ? "1" : "0"} aria-hidden={line === current ? undefined : "true"}>
            <p className="qt">{line.quoted ? "“" + line.text + "”" : line.text}</p>
            <p className="vd" data-kept={line.kept ? "1" : "0"}><b>{line.verdict}</b>{line.where ? " · " + line.where : ""}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function VerifyingLines({ body, first }: { body: Extract<BriefBody, { kind: "verifying" }>; first: number }) {
  if (body.empty !== null) return <p className="ln b-sub" style={lineStyle(first)}>{body.empty}</p>;
  const t = body.tally;
  return (
    <>
      <p className="ln eyebrow" style={lineStyle(first)}>Just checked</p>
      <TickerBox samples={body.samples} placeholder={VERIFY_PLACEHOLDER} i={first + 1} />
      <span className="ln pb" style={lineStyle(first + 2)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>
      {t === null ? null : (
        <p className="ln b-facts" style={lineStyle(first + 3)}>
          <b>{t.checked}</b> of {t.total} checked · <b>{t.verified}</b> verified · <b>{t.corrected}</b> corrected · <b>{t.dropped}</b> dropped
        </p>
      )}
    </>
  );
}

export function WritingLines({ body, first }: { body: Extract<BriefBody, { kind: "writing" }>; first: number }) {
  const t = body.tally;
  return (
    <>
      <p className="ln eyebrow" style={lineStyle(first)}>Just written</p>
      <TickerBox samples={body.samples} placeholder={body.placeholder} i={first + 1} />
      <span className="ln pb" style={lineStyle(first + 2)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>
      {t === null ? null : (
        <p className="ln b-facts" style={lineStyle(first + 3)}>
          <b>{t.checked}</b> of {t.drafted} sentences checked · <span className="ok">✓ {t.backed}</span> backed · <span className="no">✗ {t.removed}</span> removed
          {t.partsTotal > 0 ? ` · section ${t.partsReturned} of ${t.partsTotal}` : ""}{t.unchecked > 0 ? ` · ${t.unchecked} not checked` : ""}
        </p>
      )}
    </>
  );
}

/* §6.7: one criterion or note — its mark, its text, and its fact cross-fading from "reading". `r` is
   its place in the reveal, 60 ms apart once the review lands. */
function CheckRow({ line, i, r }: { line: CheckLine; i: number; r: number }) {
  return (
    <div className="ln" role="listitem" data-topic={line.mark} style={lineStyle(i, r)}>
      <Mark />
      <span className="tt">{line.text}</span>
      <span className="tf xf">
        <span data-on={line.landed ? "0" : "1"} aria-hidden={line.landed ? "true" : undefined}>{line.before}</span>
        <span data-on={line.landed ? "1" : "0"} aria-hidden={line.landed ? undefined : "true"}>{line.fact}</span>
      </span>
    </div>
  );
}

export function ReviewingLines({ body, first }: { body: Extract<BriefBody, { kind: "reviewing" }>; first: number }) {
  let i = first;
  const status = i++;
  const bar = i++;
  const criteria = body.criteria.map((line, r) => <CheckRow key={line.key} line={line} i={i++} r={r} />);
  const head = body.notes.length > 0 ? i++ : -1;
  const notes = body.notes.map((line, r) => <CheckRow key={line.key} line={line} i={i++} r={body.criteria.length + r} />);
  return (
    <>
      <StatusLine stack={body.status} i={status} />
      <span className="ln pb ind" data-on={body.waiting ? "1" : "0"} aria-hidden="true" style={lineStyle(bar)}><i /></span>
      <div className="ps-topics rv-list" role="list" aria-label="Checks">{criteria}</div>
      {head < 0 ? null : (
        <>
          <div className="ln rv-notes-h" style={lineStyle(head)}><div className="b-rule" /><span className="eyebrow">Your notes</span></div>
          <div className="ps-topics rv-list" role="list" aria-label="Your notes">{notes}</div>
        </>
      )}
    </>
  );
}
```


- [ ] **Step 4: Run the tests to make sure they pass, then the whole web suite**

```bash
(cd web && npx vitest run test/step-briefs.test.ts test/ticker.test.tsx test/components/step-bodies.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npm run -s typecheck; echo "typecheck exit $?")
```

Expected: `Test Files  3 passed (3)`, `Tests  34 passed (34)`; `F0 + 4` files and `V0 + 50` tests (on D + A + C: `37` / `326`); `typecheck exit 0`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/briefs.ts web/lib/ticker.ts web/components/StepBodies.tsx web/test/step-briefs.test.ts web/test/ticker.test.tsx web/test/components/step-bodies.test.tsx
git commit -m "feat(web): each step's own brief -- the derivations, the ticker and the bodies"
```

---

### Task 11: The rows show their bodies, and the steps' clock (spec §6.9, §8.5; AC14–AC19)

**Files:**
- Modify: `web/lib/briefs.ts` (header, `Subtitle`'s comment, `RowBrief`, `SENTENCES`, `verifyingSentence` removed, `rowBrief`, Phase D's `stoppedSubtitle`), `web/components/BriefSpine.tsx` (the bodies, the clock, and D39's hold: `Handoff`, the hand-off derivation and effect, the row state, `openable`, the role), `web/components/RunningPipeline.tsx`
- Test: `web/test/briefs.test.ts` (rewritten), `web/test/components/brief-spine.test.tsx` (Planning's slots; D39's hold: the rewritten "never awaits…" case and a new describe with three cases), `web/test/components/running-pipeline.test.tsx` (the clock), Phase D's `web/test/components/user-stopped-stage.test.tsx` (Planning's outcome now ends with its duration)

**Interfaces:**
- Consumes: Task 10's derivations and components; Phase D's `RowState` (`"stopped" | "off"`), `notRunText` and `BriefSpine`'s `frozen`; Phase A's `visibleAcks(notes, active)`.
- Produces: `RowBrief { subtitle; outcome; why; body: BriefBody; acks; earlier }`; `rowBrief(run, id, state, nowMs = Date.now())`; `SENTENCES = { finalize_report }`; `stoppedSubtitle` with each step's live facts; `BriefSpine`'s optional `now?: number` prop; `RunningPipeline` passes its one-second clock. Decision D39 in `BriefSpine` (which now imports `ARCS`): `Handoff.held`, the held derivation and its `HANDOFF_HOLD_MS` effect, the destination row pending while held, no role while held, and a `reopenable` hollow Reviewing row (the "Decision D39" section).

- [ ] **Step 1: Write the failing tests**

`web/test/briefs.test.ts` — replace

```ts
// @vitest-environment node — pure derivations over the live captures (same reason as run-state.test.ts).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { SENTENCES, STATIC_META, notRunText, rowBrief, stoppedSubtitle, subtitleText, topicFact, verifyingSentence } from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, toRunEvent, type NodeId, type RunState } from "../lib/run-state";

interface Capture { case_id: string; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const captures = [load("missing-target-triggers-one-extra-pass"), load("scoped-redraft-after-a-named-defect")];
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}

describe("the Researching brief (spec §4.3, AC5)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState();
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
    const run = newRunState();
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
    const run = newRunState();
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
  it("Planning, once done, lists a research note's own topic with the plan's (notes-progress-report spec §5.7)", () => {
    const run = newRunState();
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 2, note_topic_count: 1, sub_topics: [
      { coverage_id: "topic-01", title: "Published picks" }, { coverage_id: "note-n1", title: "Your note: pastries in the cafe", note_id: "n1" },
    ] } });
    expect(rowBrief(run, "planner", "done").titles).toEqual(["Published picks", "Your note: pastries in the cafe"]);
    expect(run.topics.map((t) => t.coverageId)).toEqual(["topic-01", "note-n1"]);
  });
  it("a looped row's first line is why it reopened", () => {
    const run = newRunState();
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

with

```ts
// @vitest-environment node — rowBrief over synthesized events and the live captures. Each step's own
// body is tested in step-briefs.test.ts; this file tests what a row shows and when.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import {
  SENTENCES, STATIC_META, notRunText, rowBrief, stoppedSubtitle, subtitleText, topicFact, type BriefBody, type RowBrief,
} from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, replayRun, toRunEvent, type NodeId, type RunEvent, type RunState } from "../lib/run-state";

interface Capture { case_id: string; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const captures = [load("missing-target-triggers-one-extra-pass"), load("scoped-redraft-after-a-named-defect")];
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}
/* A fixed clock, ten minutes after every synthesized event's base time, so elapsed times are exact. */
const NOW = Date.UTC(2026, 8, 30, 12, 10, 0);
const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState { const run = newRunState(); for (const e of events) applyEvent(run, e); return run; }
function body<K extends BriefBody["kind"]>(brief: RowBrief, kind: K): Extract<BriefBody, { kind: K }> {
  expect(brief.body.kind).toBe(kind);
  return brief.body as Extract<BriefBody, { kind: K }>;
}
const text = (brief: RowBrief) => subtitleText(brief.subtitle);

describe("the Researching brief (live-briefs spec §4.3, AC5; kept by notes-progress-report D12)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState();
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("search · scrape · read · memory");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, sub_topics: [{ coverage_id: "topic-01", title: "Alpha" }, { coverage_id: "topic-02", title: "Beta" }] }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("2 topics · researching");
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Beta", index: 2 }));
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Beta", index: 2, successful_reads: 0, findings_retained: 0 }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("1 of 2 topics done · no pages read · no findings");
    expect(body(rowBrief(run, "researcher", "active", NOW), "research").topics.map((t) => [t.n, t.title, t.state, t.fact]))
      .toEqual([[1, "Alpha", "waiting", "not yet"], [2, "Beta", "done", "no findings"]]);
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Alpha", index: 1 }));
    expect(body(rowBrief(run, "researcher", "active", NOW), "research").topics[0].fact).toBe("reading");
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-01", sub_topic: "Alpha", index: 1, successful_reads: 1, findings_retained: 1 }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("2 of 2 topics done · 1 page read · 1 finding");
  });
  it("never renders a bare 0 or a dash, at any point of either capture", () => {
    for (const capture of captures) {
      for (const run of snapshots(capture.events)) {
        const brief = rowBrief(run, "researcher", "active", NOW);
        const texts = [text(brief), rowBrief(run, "researcher", "done", NOW).outcome, ...body(brief, "research").topics.map((t) => t.fact)];
        for (const t of texts) { expect(t).not.toMatch(/(^|\D)0(\D|$)/); expect(t).not.toContain("—"); }
      }
    }
  });
  it("topicFact follows the spec's table", () => {
    expect(topicFact({ coverageId: "a", title: "A", state: "waiting", findings: null })).toBe("not yet");
    expect(topicFact({ coverageId: "a", title: "A", state: "running", findings: null })).toBe("reading");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 1 })).toBe("1 finding");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 9 })).toBe("9 findings");
  });
});

describe("every other row (notes-progress-report spec §6.3-§6.9)", () => {
  it("shows its static meta while pending and its live facts while active; Reviewing's meta is '5 checks', '· your notes' with a note held", () => {
    const run = newRunState();
    for (const id of AGENT_ORDER.filter((id) => id !== "researcher")) expect(text(rowBrief(run, id, "pending", NOW))).toBe(STATIC_META[id]);
    expect(STATIC_META.report_reviewer).toBe("5 checks");
    applyEvent(run, ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)));
    expect(text(rowBrief(run, "planner", "active", NOW))).toBe("10m 00s");
    applyEvent(run, ev("session.note.received", { note_id: "n1", text: "more on safety" }));
    applyEvent(run, ev("session.note.interpreted", { note_id: "n1", restatement: "more on safety", kinds: ["emphasis"], replaces: null, fallback: false }));
    expect(text(rowBrief(run, "report_reviewer", "pending", NOW))).toBe("5 checks · your notes");
  });
  it("gives each step its own body, and Publishing its one sentence", () => {
    const run = newRunState();
    expect(AGENT_ORDER.map((id) => rowBrief(run, id, "active", NOW).body.kind))
      .toEqual(["planning", "research", "evaluating", "verifying", "writing", "reviewing", "sentence"]);
    expect(body(rowBrief(run, "finalize_report", "active", NOW), "sentence").text).toBe("Saving the report and evidence log");
    expect(SENTENCES).toEqual({ finalize_report: "Saving the report and evidence log" });
  });
  it("Planning, once done, keeps its slots: the plan's titles and a research note's own topic (spec §5.7, §6.3)", () => {
    const run = newRunState();
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [
      { coverage_id: "topic-01", title: "Published picks", state: "passed" },
      { coverage_id: "note-n1", title: "Your note: pastries in the cafe", note_id: "n1", state: "planned" },
    ] }));
    expect(body(rowBrief(run, "planner", "done", NOW), "planning").slots.filter((s) => !s.gone).map((s) => [s.n, s.title, s.fact]))
      .toEqual([[1, "Published picks", ""], [2, "Your note: pastries in the cafe", "from your note"]]);
    expect(run.topics.map((t) => t.coverageId)).toEqual(["topic-01", "note-n1"]);
  });
  it("ends Planning's and Reviewing's outcomes with their durations", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.planning.completed", { sub_topic_count: 5, note_topic_count: 1, sub_topics: [] }),
      ev("graph.node.completed", { node: "planner" }, at(442)),
      ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(500)),
      ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.9, material_defects: 0, criteria: [], notes: [] }, at(595)),
      ev("graph.route.decided", { destination: "finalize", reason: "report_accepted", missing_required_target_ids: [] }),
      ev("graph.node.completed", { node: "report_reviewer" }, at(601)),
    ]);
    expect(rowBrief(run, "planner", "done", NOW).outcome).toBe("5 sub-topics · 1 from your note · 7m 22s");
    expect(rowBrief(run, "report_reviewer", "done", NOW).outcome).toBe("Accepted · all 5 met · 1m 41s");
  });
  it("every done row of a capture shows its outcome, with a duration on Planning's and Reviewing's", () => {
    const run = snapshots(captures[0].events).at(-1)!;
    for (const id of AGENT_ORDER) {
      const outcome = rowBrief(run, id, "done", NOW).outcome;
      if (id === "planner" || id === "report_reviewer") expect(outcome.startsWith(run.outcomes[id]! + " · ") && /\d+m \d\ds$/.test(outcome)).toBe(true);
      else expect(outcome).toBe(run.outcomes[id]);
    }
  });
  it("a looped row's first line is why it reopened", () => {
    const run = play([ev("graph.extra_pass.started", { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] })]);
    expect(rowBrief(run, "researcher", "active", NOW).why).toEqual({ kind: "extra_pass", text: "Going back to research 2 gaps the review found" });
    applyEvent(run, ev("graph.report.redraft_requested", { iteration: 1, redrafts: 1, material_defects: 3 }));
    expect(rowBrief(run, "report_writer", "active", NOW).why).toEqual({ kind: "redraft", text: "Rewriting to fix 3 issues the review found" });
  });
});

describe("the stopped row and the rows after it (notes-progress-report spec §8.5, with §6's live facts)", () => {
  it("Researching counts its topics after 'Stopped' — 'none of' before one is done, never a bare 0", () => {
    const run = newRunState();
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 3, sub_topics: [{ coverage_id: "topic-01", title: "A" }, { coverage_id: "topic-02", title: "B" }, { coverage_id: "topic-03", title: "C" }] }));
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done · no pages read · no findings");
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "B", index: 2, successful_reads: 41, findings_retained: 212 }));
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · 1 of 3 topics done · 41 pages read · 212 findings");
  });
  it("every other row reads 'Stopped · {its live facts}', frozen at the stop; with none, 'Stopped'", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [{ coverage_id: "topic-01", title: "Alpha", state: "checking" }] }),
      ev("session.stopped", { step: "planner", stopped_at: at(125), elapsed_seconds: 125 }),
    ]);
    expect(stoppedSubtitle(run, "planner")).toBe("Stopped · 2m 05s");
    expect(text(rowBrief(run, "planner", "stopped", NOW))).toBe("Stopped · 2m 05s");
    expect(body(rowBrief(run, "planner", "stopped", NOW), "planning").slots[0]).toMatchObject({ mark: "stopped", fact: "stopped" });
    expect(stoppedSubtitle(newRunState(), "planner")).toBe("Stopped");
    expect((["source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"] as NodeId[]).map((id) => stoppedSubtitle(newRunState(), id))).toEqual([
      "Stopped · starting · not yet rated", "Stopped · starting · not yet checked", "Stopped · starting · not yet written", "Stopped · reading the draft",
    ]);
  });
  it("freezes Reviewing's checks on 'stopped' when the stop came first", () => {
    const run = play([ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)), ev("session.stopped", { step: "report_reviewer", stopped_at: at(30), elapsed_seconds: 30 })]);
    const r = body(rowBrief(run, "report_reviewer", "stopped", NOW), "reviewing");
    expect(r.waiting).toBe(false);
    expect(r.criteria[0]).toMatchObject({ mark: "waiting", before: "stopped", landed: false });
    expect(text(rowBrief(run, "report_reviewer", "stopped", NOW))).toBe("Stopped · reading the draft · 0m 30s");
  });
  it("a later row reads 'not run', or 'not run again' once a loop has re-armed it", () => {
    const run = newRunState();
    expect(notRunText(run, "source_evaluator")).toBe("not run");
    expect(text(rowBrief(run, "source_evaluator", "off", NOW))).toBe("not run");
    applyEvent(run, ev("graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", iteration: 0 }));
    expect((["source_evaluator", "report_reviewer", "finalize_report"] as NodeId[]).map((id) => notRunText(run, id))).toEqual(["not run again", "not run again", "not run"]);
  });
});

describe("burst safety over the live captures (notes-progress-report AC19)", () => {
  for (const capture of captures) {
    it(`${capture.case_id}: every row's brief from a replay of events 1..k equals the live stream's`, () => {
      const snaps = snapshots(capture.events);
      capture.events.forEach((_, k) => {
        const replayed = replayRun(capture.events.slice(0, k + 1));
        for (const id of AGENT_ORDER) for (const state of ["active", "done"] as const) expect(rowBrief(replayed, id, state, NOW)).toEqual(rowBrief(snaps[k], id, state, NOW));
      });
    });
    it(`${capture.case_id}: Reviewing's brief never shows a score`, () => {
      for (const run of snapshots(capture.events)) expect(JSON.stringify(rowBrief(run, "report_reviewer", "active", NOW).body)).not.toMatch(/\d\.\d\d/);
    });
  }
});
```

`web/test/components/brief-spine.test.tsx` — replace

```tsx
    expect([...planning.querySelectorAll(".ps-titles > .ln")].map((t) => t.textContent)).toEqual(["1Adoption rate", "2Widget funding", "3Widget exports"]);
```

with

```tsx
    expect([...planning.querySelectorAll(".ps-slots > .ln:not([data-gone]) .tt")].map((t) => t.textContent)).toEqual(["1Adoption rate", "2Widget funding", "3Widget exports"]);
```

`web/test/components/user-stopped-stage.test.tsx` — replace

```tsx
      ["done", "2 sub-topics"],
```

with

```tsx
      ["done", "2 sub-topics · 0m 00s"],
```

`web/test/components/running-pipeline.test.tsx` — replace

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { newRunState } from "../../lib/run-state";

```

with

```tsx
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { applyEvent, newRunState } from "../../lib/run-state";

afterEach(() => { vi.useRealTimers(); });

```

Append to `web/test/components/running-pipeline.test.tsx`:

```tsx

describe("RunningPipeline — the steps' clock (notes-progress-report spec §6.9)", () => {
  it("ticks the active step's elapsed time with the page's one-second clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-30T12:00:05Z"));
    const run = newRunState();
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 }, timestamp: "2026-09-30T12:00:00+00:00" });
    const { container } = render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-30T12:00:00+00:00" onToggleRow={() => {}} />);
    const live = () => container.querySelector('#spine li[data-stage="planner"] .m-live')!.textContent;
    expect(live()).toBe("0m 05s");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(live()).toBe("0m 06s");
  });
});
```

`web/test/components/brief-spine.test.tsx` — replace

```tsx
  it("never awaits a row a loop sends the run back from", () => {
    const run = newRunState();
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
```

with

```tsx
  it("never awaits a row a loop sends the run back from: the hold's own timer hands it over (D39)", () => {
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const { container, rerender } = show(run);
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] } });
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    expect([row(container, "report_reviewer").getAttribute("data-state"), row(container, "report_reviewer").getAttribute("data-open")]).toEqual(["active", "1"]);
    expect(row(container, "researcher").getAttribute("data-state")).toBe("pending");
    // A loop's completion of the reviewer is inert, so none ever releases the row: the timer does.
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("pending");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("0");
    expect(row(container, "researcher").getAttribute("data-state")).toBe("active");
  });
```

Append to `web/test/components/brief-spine.test.tsx`:

```tsx

describe("BriefSpine — a loop route holds Reviewing on its checks and verdict (decision D39)", () => {
  /* Writing done and Reviewing's call running; then the review lands with one criterion not met and the
     route sends the draft back to the writer, with `also` applied in the same render. */
  function sentBack(also: Parameters<typeof applyEvent>[1][] = []) {
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const onToggle = (id: NodeId) => toggleOpen(run, id);
    const { container, rerender } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    const again = () => rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    const criteria = ["completeness", "evidence_quality", "attribution", "uncertainty", "readability"]
      .map((dimension) => ({ dimension, met: dimension !== "completeness", kinds: dimension === "completeness" ? ["coverage"] : [] }));
    applyEvent(run, { type: "graph.report.reviewed", metadata: { review_status: "scored", mean_score: 0.6, material_defects: 1, criteria, notes: [] } });
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "redraft", reason: "redraft_requested", missing_required_target_ids: [] } });
    for (const event of also) applyEvent(run, event);
    again();
    return { run, container, again };
  }
  const VERDICT = "1 thing to fix · sending the draft back to the writer";

  it("keeps Reviewing open on its five checks and its verdict while Writing waits closed; then hands over, and Reviewing reopens", () => {
    const { run, container, again } = sentBack();
    // The reviewer's own completion after a loop route is inert; the redraft's start says why Writing reopens.
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "report_reviewer" } });
    applyEvent(run, { type: "graph.report.redraft_requested", metadata: { iteration: 0, redrafts: 1, material_defects: 1 } });
    again();
    const reviewing = row(container, "report_reviewer"), writing = row(container, "report_writer");
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open"), reviewing.hasAttribute("aria-current"), reviewing.hasAttribute("data-handoff")])
      .toEqual(["active", "1", false, false]);
    expect(reviewing.querySelectorAll(".rv-list > .ln")).toHaveLength(5);
    expect([...reviewing.querySelectorAll(".rv-list > .ln[data-topic='fail'] .tt")].map((t) => t.textContent)).toEqual(["Covers your whole question"]);
    expect(reviewing.querySelector(".b-now.xf > [data-on='1']")!.textContent).toBe(VERDICT);
    expect([writing.getAttribute("data-state"), writing.getAttribute("data-open"), writing.hasAttribute("data-handoff"), writing.hasAttribute("aria-current")])
      .toEqual(["pending", "0", false, false]);

    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open"), reviewing.getAttribute("data-handoff")]).toEqual(["pending", "0", "from"]);
    expect([writing.getAttribute("data-state"), writing.getAttribute("data-open"), writing.getAttribute("data-handoff"), writing.getAttribute("aria-current")])
      .toEqual(["active", "1", "to", "step"]);
    expect(writing.querySelector(".b-why")!.textContent).toBe("Rewriting to fix 1 issue the review found");

    // The hollow row reopens from its head, on the same checks and verdict.
    const toggle = reviewing.querySelector<HTMLButtonElement>("button.ps-toggle")!;
    expect([reviewing.getAttribute("data-toggle"), toggle.getAttribute("aria-expanded")]).toEqual(["1", "false"]);
    fireEvent.click(toggle);
    again();
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open")]).toEqual(["pending", "1"]);
    expect(reviewing.querySelector(".b-now.xf > [data-on='1']")!.textContent).toBe(VERDICT);
    expect(reviewing.querySelectorAll(".rv-list > .ln[data-topic='fail']")).toHaveLength(1);

    // Reviewing running again starts its block clean: nothing to reopen.
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 1 } });
    again();
    expect(reviewing.querySelector("button.ps-toggle")).toBeNull();
  });

  it("holds Reviewing even when the loop's own start event lands in the same render as the route decision", () => {
    // The redraft's start settles the loop (run.loop "settled"); the lit arc still names Writing.
    const { container } = sentBack([
      { type: "graph.node.completed", metadata: { node: "report_reviewer" } },
      { type: "graph.report.redraft_requested", metadata: { iteration: 0, redrafts: 1, material_defects: 1 } },
    ]);
    expect([row(container, "report_reviewer").getAttribute("data-state"), row(container, "report_reviewer").getAttribute("data-open")]).toEqual(["active", "1"]);
    expect(row(container, "report_writer").getAttribute("data-state")).toBe("pending");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(row(container, "report_writer").getAttribute("data-handoff")).toBe("to");
  });

  it("a Stop during the hold ends it: Reviewing paints pending and no row takes a hand-off role", () => {
    const { run, container, again } = sentBack();
    act(() => { vi.advanceTimersByTime(500); });
    applyEvent(run, { type: "session.stopped", metadata: { step: "report_writer", stopped_at: "2026-09-30T12:00:00+00:00", elapsed_seconds: 60 } });
    again();
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("pending");
    expect(container.querySelector("[data-handoff]")).toBeNull();
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
});
```


- [ ] **Step 2: Run them to make sure they fail**

```bash
(cd web && npx vitest run test/briefs.test.ts test/components/brief-spine.test.tsx test/components/running-pipeline.test.tsx test/components/user-stopped-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
```

Expected: `Test Files  4 failed (4)` — `rowBrief` has no `body`, Planning's brief still lists `.ps-titles`, a loop route still drops Reviewing at once (D39), the clock does not reach the rows, and the outcome has no duration.

- [ ] **Step 3: Write the implementation**

`web/lib/briefs.ts` — replace

```ts
// The live step briefs (live-briefs spec §4.3; picks 1A, 2C, 3B): what each spine row says while it
// runs, once it is done, and when a loop reopens it. Pure — a function of RunState only, so a burst,
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
```

with

```ts
// The live step briefs: what each spine row says while it runs, once it is done, when a loop reopens
// it and when the reader stopped the run on it. live-briefs spec §4.3 (picks 1A, 2C, 3B) built the
// frame; notes-progress-report spec §6 (2026-09-30) gives every step its own body: Planning's slots
// (Main.dc.html B), Evaluating's bar and split (Evaluating.dc.html A), Verifying's and Writing's
// tickers (Verifying.dc.html C, Writing.dc.html E) and Reviewing's checks (Reviewing.dc.html A
// revised). Pure — a function of RunState and the clock only, so a burst, a tick and a replay from
// event 1 paint the same brief (DESIGN.md §5.7).
```

`web/lib/briefs.ts` — replace

```ts
/* The subtitle a row shows until it is done: its static meta, or Researching's live facts line. */
```

with

```ts
/* The subtitle a row shows until it is done: a line of text, or Researching's live facts line. */
```

`web/lib/briefs.ts` — replace

```ts
export interface RowBrief {
  subtitle: Subtitle;              /* pending and active rows; green while active */
  outcome: string;                 /* done and loop rows */
  why: ReopenLine | null;          /* the first line of a row a loop reopened */
  sentence: string | null;         /* the one plain sentence of every step but Researching */
  topics: TopicLine[] | null;      /* Researching's checklist */
  titles: string[] | null;         /* Planning's final brief: the sub-topic titles */
  acks: Ack[];                     /* the active row only: the latest two notes, acknowledged (§4.7) */
  earlier: string | null;          /* the active row only: "and {n} earlier notes" past two */
}
```

with

```ts
export interface RowBrief {
  subtitle: Subtitle;              /* pending, active, stopped and off rows; green while active */
  outcome: string;                 /* done and loop rows */
  why: ReopenLine | null;          /* the first line of a row a loop reopened */
  body: BriefBody;                 /* the step's own lines (§6.3-§6.7) */
  acks: Ack[];                     /* the active row only: the latest two notes, acknowledged */
  earlier: string | null;          /* the active row only: "and {n} earlier notes" past two */
}
```

`web/lib/briefs.ts` — replace

```ts
export const SENTENCES: Readonly<Record<Exclude<NodeId, "researcher" | "evidence_verifier">, string>> = {
  planner: "Breaking your question into sub-topics…",
  source_evaluator: "Rating sources for trustworthiness and relevance",
  report_writer: "Writing the report from verified findings only",
  report_reviewer: "Reviewing the draft on 7 dimensions",
  finalize_report: "Saving the report and evidence log",
};
```

with

```ts
/* §6.9: Publishing keeps its one plain sentence; every other step has its own body. */
export const SENTENCES: Readonly<Pick<Record<NodeId, string>, "finalize_report">> = {
  finalize_report: "Saving the report and evidence log",
};
```

`web/lib/briefs.ts` — replace

```ts
/* Verifying's sentence, from researcher.research.completed.findings of the pass being verified. */
export function verifyingSentence(findings: number | null): string {
  if (findings === null) return "Checking findings against their pages";
  if (findings === 0) return "No findings to check";
  if (findings === 1) return "Checking 1 finding against its page";
  return "Checking " + findings + " findings against their pages";
}

```

with

```ts

```

`web/lib/briefs.ts` — replace

```ts
export function rowBrief(run: RunState, id: NodeId, state: RowState): RowBrief {
  const finished = state === "done" || state === "loop";
  const why = run.reopen[id] ?? null;
  const outcome = run.outcomes[id] ?? STATIC_META[id];
  const text: Subtitle = { kind: "text", text: STATIC_META[id] };
  // live-briefs spec §4.7: the reader's notes are acknowledged in the row that is running now.
  const noted = id === run.active ? visibleAcks(run.notes, run.active) : { acks: [], earlier: 0 };
  const notes = { acks: noted.acks, earlier: noted.earlier > 0 ? earlierNotesText(noted.earlier) : null };
  if (id === "researcher") {
    return {
      subtitle: { kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
        pages: run.pagesRead ?? 0, findings: run.findingsSoFar ?? 0 },
      outcome, why, sentence: null, titles: null, ...notes,
      topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })),
    };
  }
  if (id === "planner" && finished && run.plan.length > 0) {
    return { subtitle: text, outcome, why, sentence: null, topics: null, titles: run.plan.map((p) => p.title), ...notes };
  }
  const sentence = id === "evidence_verifier" ? verifyingSentence(run.passFindings) : SENTENCES[id];
  return { subtitle: text, outcome, why, sentence, topics: null, titles: null, ...notes };
}
```

with

```ts
function subtitleFor(run: RunState, id: NodeId, state: RowState, nowMs: number): Subtitle {
  if (state === "stopped") return { kind: "text", text: stoppedSubtitle(run, id) };
  if (state === "off") return { kind: "text", text: notRunText(run, id) };
  if (id === "researcher") return researchSubtitle(run);
  if (state === "active") {
    const live = liveSubtitle(run, id, nowMs);
    if (live !== null) return { kind: "text", text: live };
  }
  /* §6.7 (review 2, M-4): Reviewing's static meta names the notes while the run holds one. */
  if (id === "report_reviewer" && listedNotes(run.notes).length > 0) return { kind: "text", text: STATIC_META.report_reviewer + " · your notes" };
  return { kind: "text", text: STATIC_META[id] };
}
function outcomeFor(run: RunState, id: NodeId): string {
  const outcome = run.outcomes[id];
  if (outcome === undefined) return STATIC_META[id];
  const seconds = run.durations[id];
  /* §6.3, §6.7: Planning's and Reviewing's outcomes end with the row's duration. */
  return (id === "planner" || id === "report_reviewer") && typeof seconds === "number" ? outcome + " · " + fmtSeconds(seconds) : outcome;
}
function bodyFor(run: RunState, id: NodeId, stopped: boolean): BriefBody {
  switch (id) {
    case "planner": return { kind: "planning", status: planningStatus(run), slots: planningSlots(run, stopped) };
    case "researcher":
      return { kind: "research", topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })) };
    case "source_evaluator": return evaluatingBody(run);
    case "evidence_verifier": return verifyingBody(run);
    case "report_writer": return writingBody(run);
    case "report_reviewer": return reviewingBody(run, stopped);
    default: return { kind: "sentence", text: SENTENCES.finalize_report };
  }
}
/* `nowMs` is the page's one-second clock (RunningPipeline), so the elapsed times tick; a burst, a tick
   and a replay given the same clock paint the same brief. */
export function rowBrief(run: RunState, id: NodeId, state: RowState, nowMs: number = Date.now()): RowBrief {
  // live-briefs spec §4.7: the reader's notes are acknowledged in the row that is running now.
  const noted = id === run.active ? visibleAcks(run.notes, run.active) : { acks: [], earlier: 0 };
  return {
    subtitle: subtitleFor(run, id, state, nowMs),
    outcome: outcomeFor(run, id),
    why: run.reopen[id] ?? null,
    body: bodyFor(run, id, state === "stopped"),
    acks: noted.acks,
    earlier: noted.earlier > 0 ? earlierNotesText(noted.earlier) : null,
  };
}
```

`web/lib/briefs.ts` — replace

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
```

with

```ts
/* notes-progress-report spec §8.5: the stopped row's subtitle — "Stopped", then the live facts the row
   had when the reader stopped it, frozen at the stop (§6.3-§6.7). Researching's always counts the topics
   done ("none of 3", rather than its running "3 topics · researching", which would contradict
   "Stopped", and never a bare 0); a row with no live facts reads "Stopped". */
export function stoppedSubtitle(run: RunState, id: NodeId): string {
  if (id === "researcher") {
    const topics = run.topics.length;
    if (topics === 0) return "Stopped";
    const done = run.topics.filter((t) => t.state === "done").length;
    return "Stopped · " + (done === 0 ? "none" : String(done)) + " of " + plural(topics, "topic", "topics") + " done · "
      + countPhrase(run.pagesRead ?? 0, "page read", "pages read") + " · " + countPhrase(run.findingsSoFar ?? 0, "finding", "findings");
  }
  const at = run.stopped?.at ? Date.parse(run.stopped.at) : NaN;
  const live = liveSubtitle(run, id, Number.isNaN(at) ? null : at);
  return live === null ? "Stopped" : "Stopped · " + live;
}
```


`web/components/BriefSpine.tsx` — replace

```tsx
import { useLoopArc } from "./loop-arc";

```

with

```tsx
import { useLoopArc } from "./loop-arc";
import { EvaluatingLines, PlanningSlots, ReviewingLines, StatusLine, VerifyingLines, WritingLines } from "./StepBodies";

```

`web/components/BriefSpine.tsx` — replace

```tsx
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void; frozen?: NodeId }
```

with

```tsx
/* `now`: RunningPipeline's one-second clock (notes-progress-report spec §6.9), which the elapsed times tick with. */
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void; frozen?: NodeId; now?: number }
```

`web/components/BriefSpine.tsx` — replace

```tsx
export function BriefSpine({ marks, run, onToggle, frozen }: Props) {
```

with

```tsx
export function BriefSpine({ marks, run, onToggle, frozen, now }: Props) {
```

`web/components/BriefSpine.tsx` — replace

```tsx
    const brief = rowBrief(run, s.id, st);
```

with

```tsx
    const brief = rowBrief(run, s.id, st, now);
```

`web/components/BriefSpine.tsx` — replace

```tsx
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);

```

with

```tsx
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    const body = brief.body;
    // notes-progress-report spec §6.3: Planning's status line leads its brief; the acknowledgements follow it.
    if (body.kind === "planning") lines.push(<StatusLine key="status" stack={body.status} i={n++} />);

```

`web/components/BriefSpine.tsx` — replace

```tsx
    if (brief.sentence) lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{brief.sentence}</p>);
    if (brief.topics) {
      lines.push(
        <div key="topics" className="ps-topics" role="list">
          {brief.topics.map(
```

with

```tsx
    if (body.kind === "sentence") lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{body.text}</p>);
    if (body.kind === "research") {
      lines.push(
        <div key="topics" className="ps-topics" role="list">
          {body.topics.map(
```

`web/components/BriefSpine.tsx` — replace

```tsx
    if (brief.titles) {
      lines.push(
        <div key="titles" className="ps-titles" role="list">
          {brief.titles.map((title, k) => (
            <div key={k} className="ln" role="listitem" style={lineStyle(n++)}><span className="tn">{k + 1}</span><span>{title}</span></div>
          ))}
        </div>,
      );
    }

```

with

```tsx
    // notes-progress-report spec §6.3-§6.7: each step's own body, numbered on from the lines above it.
    if (body.kind === "planning") lines.push(<PlanningSlots key="body" slots={body.slots} first={n} />);
    if (body.kind === "evaluating") lines.push(<EvaluatingLines key="body" body={body} first={n} />);
    if (body.kind === "verifying") lines.push(<VerifyingLines key="body" body={body} first={n} />);
    if (body.kind === "writing") lines.push(<WritingLines key="body" body={body} first={n} />);
    if (body.kind === "reviewing") lines.push(<ReviewingLines key="body" body={body} first={n} />);

```

`web/components/BriefSpine.tsx` — replace

```tsx
import { STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
```

with

```tsx
import { ARCS, STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
```

`web/components/BriefSpine.tsx` — replace

```tsx
   the active row, open; then it takes the `from` role and folds with the 3B timings. */
interface Handoff { from: NodeId | null; to: NodeId | null; awaiting: NodeId | null }
```

with

```tsx
   the active row, open; then it takes the `from` role and folds with the 3B timings.
   `held` (decision D39, notes-progress-report spec §6.7 as amended): a loop route — the extra pass, a
   redraft, a note pass, a note redraft — holds Reviewing as the awaited row for HANDOFF_HOLD_MS, open on
   its checks and its verdict, while the row the run goes back to waits, closed and pending, with no role;
   then Reviewing takes the `from` role and the ordinary hand-off runs. The hold paints; it never marks. */
interface Handoff { from: NodeId | null; to: NodeId | null; awaiting: NodeId | null; held: boolean }
```

`web/components/BriefSpine.tsx` — replace

```tsx
  if (frozen === undefined && run.active !== prevActive) {
    setPrevActive(run.active);
    const from = prevActive && finishedState(marks[prevActive]) ? prevActive : null;
    const successor = prevActive === null ? null : STAGES[STAGES.findIndex((s) => s.id === prevActive) + 1]?.id ?? null;
    const awaiting = from === null && prevActive !== null && successor === run.active && run.finalStatus === null ? prevActive : null;
    setHandoff({ from, to: run.active, awaiting });
  } else if (handoff?.awaiting && handoff.from === null && handoff.to === run.active && finishedState(marks[handoff.awaiting])) {
    setHandoff({ from: handoff.awaiting, to: run.active, awaiting: null });
  }
```

with

```tsx
  if (frozen === undefined && run.active !== prevActive) {
    setPrevActive(run.active);
    if (prevActive === "report_reviewer" && run.arc !== null && run.active === ARCS[run.arc].to) {
      // D39: a loop route — the run left Reviewing for the lit arc's own destination. A finalize or end
      // route, graph.session.completed and session.stopped all clear the arc; it stays lit when the
      // loop's own start event lands in the same render; a burst already past the destination hands
      // over as usual.
      setHandoff({ from: null, to: run.active, awaiting: "report_reviewer", held: true });
    } else {
      const from = prevActive && finishedState(marks[prevActive]) ? prevActive : null;
      const successor = prevActive === null ? null : STAGES[STAGES.findIndex((s) => s.id === prevActive) + 1]?.id ?? null;
      const awaiting = from === null && prevActive !== null && successor === run.active && run.finalStatus === null ? prevActive : null;
      setHandoff({ from, to: run.active, awaiting, held: false });
    }
  } else if (handoff?.awaiting && !handoff.held && handoff.from === null && handoff.to === run.active && finishedState(marks[handoff.awaiting])) {
    setHandoff({ from: handoff.awaiting, to: run.active, awaiting: null, held: false });
  }
```

`web/components/BriefSpine.tsx` — replace

```tsx
  useEffect(() => {
    if (!handoff) return;
    // An awaited hand-off
```

with

```tsx
  useEffect(() => {
    if (!handoff) return;
    // D39: the hold is a dwell on a timer, kept under reduced motion; when it ends Reviewing takes the
    // `from` role (a loop's own completion of the reviewer is inert, so no mark would ever release it).
    if (handoff.held) {
      const to = handoff.to;
      const held = setTimeout(() => setHandoff({ from: "report_reviewer", to, awaiting: null, held: false }), HANDOFF_HOLD_MS);
      return () => clearTimeout(held);
    }
    // An awaited hand-off
```

`web/components/BriefSpine.tsx` — replace

```tsx
      : awaited ? "active" : marks[s.id] || "pending";
```

with

```tsx
      : awaited ? "active" : handoff?.held && handoff.to === s.id ? "pending" : marks[s.id] || "pending";
```

`web/components/BriefSpine.tsx` — replace

```tsx
    const openable = finished || st === "stopped";
```

with

```tsx
    // D39: once the hold has ended, the hollow Reviewing row (the loop's rearm took its mark) reopens to
    // its checks and its verdict, until Reviewing runs again (graph.node.started resets run.reviewing).
    const reopenable = s.id === "report_reviewer" && st === "pending" && run.rearmed.report_reviewer === true && run.reviewing.landed;
    const openable = finished || st === "stopped" || reopenable;
```

`web/components/BriefSpine.tsx` — replace

```tsx
    const role = handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
```

with

```tsx
    const role = handoff?.held ? undefined : handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
```

`web/components/RunningPipeline.tsx` — replace

```tsx
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)));
```

with

```tsx
  const [elapsed, setElapsed] = useState(0);
  // notes-progress-report spec §6.9: the same one-second clock ticks the steps' elapsed times.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => {
      const at = Date.now();
      setNow(at);
      setElapsed(Math.max(0, Math.floor((at - new Date(startedAt).getTime()) / 1000)));
    };
```

`web/components/RunningPipeline.tsx` — replace

```tsx
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} />
```

with

```tsx
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} now={now} />
```


- [ ] **Step 4: Run the tests to make sure they pass, then the whole web suite**

```bash
(cd web && npx vitest run test/briefs.test.ts test/components/brief-spine.test.tsx test/components/running-pipeline.test.tsx test/components/user-stopped-stage.test.tsx 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npm run -s typecheck; echo "typecheck exit $?")
```

Expected: `Test Files  4 passed (4)`, `Tests  40 passed (40)` (with Phase D's stopped-stage tests and D39's three); `F0 + 4` files and `V0 + 61` tests (on D + A + C: `37` / `337`); `typecheck exit 0`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/briefs.ts web/components/BriefSpine.tsx web/components/RunningPipeline.tsx web/test/briefs.test.ts web/test/components/brief-spine.test.tsx web/test/components/running-pipeline.test.tsx web/test/components/user-stopped-stage.test.tsx
git commit -m "feat(web): the rows show each step's body; the elapsed times tick"
```

---

### Task 12: The briefs' CSS, and the briefs on the replay stream (spec §6.9, §11.1 Playwright; AC14–AC18, AC20, AC21)

**Files:**
- Modify: `web/app/globals.css` (the live-briefs reduced-motion block; a new section at the end), `web/e2e/briefs.spec.ts` (Planning's outcome and slots; the arc test waits for Researching to reopen after D39's hold), `web/e2e/support.ts` (`installMotionRecorder`'s parts), `web/e2e/motion.spec.ts` (the timings, and the Reviewing window re-derived for D39)
- Create: `web/e2e/progress.spec.ts`

**Interfaces:**
- Consumes: Tasks 7–11; Phase D's `POST /research/{id}/stop`.
- Produces: the rules for `.b-now`, `.b-sub`, `.b-facts`, `.b-rule`, `.ps-brief .xf`, `.ps-slots .sk` (+ `@keyframes sheen`), `[data-gone]`, `[data-rise]`, the amber ✗, `.pb` (+ `.pb.ind`, `@keyframes drift`), `.stats`/`.stat`, `.tickbox`/`.qt`/`.vd`, `.rv-list`, `.rv-notes-h`; the motion recorder's `xf` part.

- [ ] **Step 1: The CSS**

`web/app/globals.css` — replace

```css
  [data-topic="running"] > .mk .dotc{box-shadow:0 0 0 4px color-mix(in oklch,var(--status-ok) 13%,transparent) !important}
}

```

with

```css
  [data-topic="running"] > .mk .dotc{box-shadow:0 0 0 4px color-mix(in oklch,var(--status-ok) 13%,transparent) !important}
  /* notes-progress-report spec §6.9 (D21): no sheen — the skeleton bar stays, still; Reviewing's bar is
     a static full-width bar at .35; every cross-fade is opacity only, over 160ms; counts jump (useTween). */
  .spine-lg.briefs .sk::after{content:none !important;animation:none !important}
  .spine-lg.briefs .pb.ind[data-on="1"] i{animation:none !important;width:100% !important;transform:none !important;opacity:.35 !important}
  .spine-lg.briefs .ps-brief .xf > *{transform:none !important;transition-property:opacity !important;transition-duration:160ms !important}
}

```

Append to `web/app/globals.css`:

```css

/* ═══ 2026-09-30: live progress per step (notes-progress-report spec §6.3-§6.9; Main.dc.html B, Evaluating.dc.html A, Verifying.dc.html C, Writing.dc.html E, Reviewing.dc.html A revised; progress.css) ═══ */
/* Each step's own brief, scoped to the running spine's briefs. Colour is status only: green for the
   active step's own progress, amber for a check that did not pass, a dropped finding or a removed
   sentence (D30). Two loops besides the halo (D21), each only while its step is the active row: the
   sheen on Planning's skeleton slots and the drift on Reviewing's bar while its call runs. */
.spine-lg.briefs .b-now{font-size:var(--text-base);color:var(--fg);line-height:1.5}
.spine-lg.briefs .b-sub{font-size:var(--text-sm);color:var(--muted);line-height:1.5}
.spine-lg.briefs .b-facts{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--muted);font-variant-numeric:tabular-nums}
.spine-lg.briefs .b-facts b{color:var(--fg);font-weight:500}
.spine-lg.briefs .b-facts .ok{color:var(--status-ok)}
.spine-lg.briefs .b-facts .no{color:var(--status-warn)}
.spine-lg.briefs .b-rule{height:1px;background:var(--border-soft);margin:var(--space-1) 0}
/* Cross-fades (progress.css:23-24, :34-35; theme.css .xf): a line's texts stacked in one grid cell, the
   data-on="1" one shown; a .rise stack also lifts 5px as a text fades out, and settles as one fades in. */
.spine-lg.briefs .ps-brief .xf{display:grid}
.spine-lg.briefs .ps-brief .xf > *{grid-area:1/1;min-width:0;transition:opacity var(--motion-base) var(--ease-standard)}
.spine-lg.briefs .ps-brief .xf > [data-on="0"]{opacity:0}
.spine-lg.briefs .ps-brief .xf.rise > *{transition:opacity var(--motion-base) var(--ease-standard),transform var(--motion-fluid) var(--ease-entrance)}
.spine-lg.briefs .ps-brief .xf.rise > [data-on="0"]{transform:translateY(5px)}
/* Planning's slots (§6.3, progress.css:30-33): a quiet bar until a title lands. Its sheen runs only on a
   skeleton slot of the active row, so it ends when the titles arrive, when Planning finishes and when
   the run stops. */
.spine-lg.briefs .ps-slots .sk{display:block;height:9px;border-radius:var(--radius-sm);background:var(--border-soft);position:relative;overflow:hidden;align-self:center}
.spine-lg.briefs > li[data-state="active"] .ps-slots .sk[data-on="1"]::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,color-mix(in oklch,var(--fg) 7%,transparent),transparent);transform:translateX(-100%);animation:sheen var(--motion-halo) var(--ease-standard) infinite}
@keyframes sheen{to{transform:translateX(100%)}}
/* A skeleton the plan's titles leave over fades out, then leaves the layout; a slot that arrives later
   (a fifth topic, a note's slot) rises in at once, as a note's acknowledgement does. */
.spine-lg.briefs .ps-slots > [data-gone="1"]{opacity:0;display:none;transition-property:opacity,display;transition-duration:var(--motion-base);transition-timing-function:var(--ease-standard);transition-behavior:allow-discrete}
.spine-lg.briefs > li[data-open="1"]:not([data-handoff]) .ps-slots > [data-rise="1"]{--d-c:0ms}
@starting-style{.spine-lg.briefs > li[data-open="1"] .ps-slots > [data-rise="1"]{opacity:0;transform:translateY(4px)}}
/* The amber ✗ (progress.css:82-88): a slot still flagged, a check that did not pass, a note not met. */
.ps-topics .mk svg.x path{stroke:var(--status-warn)}
[data-topic="done"] > .mk svg.x path{stroke-dashoffset:14}
[data-topic="fail"] > .mk svg.x path{stroke-dashoffset:0}
[data-topic="fail"] > .mk .ring{opacity:0}
.ps-topics > [data-topic="fail"]{color:var(--fg)}
.ps-topics > [data-topic="fail"] .tf{color:var(--status-warn)}
/* The determinate bar (§6.4-§6.6, progress.css:8-10): green, because it is the active step's own
   progress, filling over --fill-line. */
.spine-lg.briefs .pb{display:block;height:2px;background:var(--border-soft);border-radius:var(--radius-pill);overflow:hidden}
.spine-lg.briefs .pb i{display:block;height:100%;background:var(--status-ok);transform-origin:left;transition:transform var(--fill-line) var(--ease-entrance)}
/* Reviewing's indeterminate bar (§6.7, progress.css:12-14): a 28% segment drifting while the call runs;
   it stops, and the segment hides, once the review lands. */
.spine-lg.briefs .pb.ind i{width:28%}
.spine-lg.briefs > li[data-state="active"] .pb.ind[data-on="1"] i{animation:drift var(--motion-halo) var(--ease-standard) infinite}
.spine-lg.briefs .pb.ind[data-on="0"] i{animation:none;opacity:0}
@keyframes drift{0%{transform:translateX(-110%)}100%{transform:translateX(380%)}}
/* Evaluating's split (§6.4; theme.css:137-139), wrapping on a phone. */
.spine-lg.briefs .stats{display:flex;flex-wrap:wrap;gap:var(--space-2) var(--space-6);padding:var(--space-1) 0}
.spine-lg.briefs .stat{display:flex;flex-direction:column;gap:2px}
.spine-lg.briefs .stat .v{font-family:var(--font-mono);font-size:var(--text-lg);color:var(--fg);font-variant-numeric:tabular-nums;line-height:1.3}
/* Verifying's and Writing's ticker (§6.5-§6.6, progress.css:61-64): the newest sample shows and the one
   before it fades out under it; a kept verdict is green, a dropped finding or removed sentence amber. */
.spine-lg.briefs .tickbox{padding:var(--space-3) 0;border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft);min-height:112px}
.spine-lg.briefs .qt{font-size:var(--text-base);color:var(--fg);line-height:1.5;margin:0;overflow-wrap:anywhere}
.spine-lg.briefs .vd{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--muted);margin-top:4px}
.spine-lg.briefs .vd b{font-weight:500;color:var(--status-ok)}
.spine-lg.briefs .vd[data-kept="0"] b{color:var(--status-warn)}
@starting-style{.spine-lg.briefs .tickbox .xf > [data-on="1"]{opacity:0;transform:translateY(5px)}}
/* Reviewing's checks (§6.7): rings that read "reading" while the call runs; when the review lands each
   row's mark and fact change 60ms after the row above it (--r, its place in the reveal). */
.spine-lg.briefs .rv-list .tf{white-space:normal;text-align:right}
.spine-lg.briefs .rv-list .mk .ring,.spine-lg.briefs .rv-list .mk .dotc,.spine-lg.briefs .rv-list .tf > *{transition-delay:calc(var(--r,0) * 60ms)}
.spine-lg.briefs .rv-list .mk svg path{transition-delay:calc(80ms + var(--r,0) * 60ms)}
.spine-lg.briefs .rv-notes-h{display:flex;flex-direction:column;gap:var(--space-2)}
@media (max-width:480px){
  .spine-lg.briefs .rv-list .tf{text-align:left}
}
```


```bash
(cd web && npm run -s check:css)
```

Expected: `OK`.

- [ ] **Step 2: The e2e specs**

Create `web/e2e/progress.spec.ts`:

```ts
// notes-progress-report spec §6.3-§6.10 (AC14-AC18, AC20, AC21): each step's brief on the replay
// stream, held after a named event with X-Replay-Hold-After (§6.10 item 2), then stopped with Phase
// D's POST /stop so the next test starts on an idle server. The default case is
// missing-target-triggers-one-extra-pass: three topics, four sources, four findings, two parts.
import { expect, test, type APIRequestContext, type BrowserContext, type Page } from "@playwright/test";
import { API, submit } from "./support";

/* The browser's POST /research carries the header through the proxy's allowlist. */
const holdAt = (context: BrowserContext, hold: string) => context.setExtraHTTPHeaders({ "X-Replay-Hold-After": hold });
const stop = async (request: APIRequestContext, id: string) => expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
const row = (page: Page, id: string) => page.locator(`#spine li[data-stage="${id}"]`);
const animations = (page: Page) => page.evaluate(() => document.getAnimations().map((a) => (a as CSSAnimation).animationName).filter(Boolean));

test("Planning: the status line, four skeleton slots that say 'drafting', the elapsed time (AC14)", async ({ page, context, request }) => {
  await holdAt(context, "planner.progress");
  const id = await submit(page, "q");
  const planning = row(page, "planner");
  await expect(planning.locator(".b-now.xf > [data-on='1']")).toHaveText("Drafting a plan for your question…", { timeout: 10_000 });
  await expect(planning.locator(".ps-slots > .ln")).toHaveCount(4);
  await expect(planning.locator(".ps-slots > .ln .sk[data-on='1']")).toHaveCount(4);
  await expect(planning.locator(".ps-slots > .ln .tf")).toHaveText(["drafting", "drafting", "drafting", "drafting"]);
  await expect(planning.locator(".m-live")).toHaveText(/^\dm \d\ds$/);
  await stop(request, id);
});

test("Planning: titles fill the slots and the check runs; the surplus skeleton leaves (AC14)", async ({ page, context, request }) => {
  await holdAt(context, "planner.progress#2");
  const id = await submit(page, "q");
  const planning = row(page, "planner");
  await expect(planning.locator(".b-now.xf > [data-on='1']")).toHaveText("Checking the plan covers everything you asked…", { timeout: 10_000 });
  await expect(planning.locator(".ps-slots > .ln:not([data-gone]) .tt")).toHaveText(["1Adoption rate", "2Widget funding", "3Widget exports"]);
  await expect(planning.locator(".ps-slots > .ln[data-topic='running']")).toHaveCount(3);
  await expect(planning.locator(".ps-slots > [data-gone='1']")).toBeHidden();
  await stop(request, id);
});

test("Evaluating: the lead, the full bar and the Strong / Fair / Weak split once the batch lands (AC15)", async ({ page, context, request }) => {
  await holdAt(context, "source_evaluator.progress#2");
  const id = await submit(page, "q");
  const evaluating = row(page, "source_evaluator");
  await expect(evaluating).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(evaluating.locator(".b-now")).toHaveText("Rating 4 sources for trustworthiness and relevance");
  await expect(evaluating.locator(".stat .eyebrow")).toHaveText(["Rated", "Strong", "Fair", "Weak"]);
  await expect(evaluating.locator(".stat .v")).toHaveText(["4 of 4", "4", "0", "0"]);
  await expect(evaluating.locator(".pb > i")).toHaveAttribute("style", /scaleX\(1\)/);
  await expect(evaluating.locator(".m-live")).toHaveText("4 of 4 rated");
  await stop(request, id);
});

test("Verifying: a real finding with its verdict and its source, the bar and the tally (AC16)", async ({ page, context, request }) => {
  await holdAt(context, "evidence_verifier.progress#2");
  const id = await submit(page, "q");
  const verifying = row(page, "evidence_verifier");
  await expect(verifying).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(verifying.locator(".eyebrow")).toHaveText("Just checked");
  await expect(verifying.locator(".tickbox .xf > div[data-on='1'] .qt")).toHaveText("the Acme widget funding round in the United States was 12 million dollars in 2024");
  await expect(verifying.locator(".tickbox .xf > div[data-on='1'] .vd")).toHaveText("verified · an original report");
  await expect(verifying.locator(".tickbox .vd[data-kept='1']")).toHaveCount(1);
  await expect(verifying.locator(".b-facts")).toHaveText("4 of 4 checked · 4 verified · 0 corrected · 0 dropped");
  await expect(verifying.locator(".m-live")).toHaveText("4 of 4 checked");
  await stop(request, id);
});

test("Writing: the tally grows as the sections return; the placeholder waits for the first checked sentence (AC17)", async ({ page, context, request }) => {
  await holdAt(context, "report_writer.progress#3");
  const id = await submit(page, "q");
  const writing = row(page, "report_writer");
  await expect(writing).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(writing.locator(".eyebrow")).toHaveText("Just written");
  await expect(writing.locator(".tickbox .b-sub")).toHaveText("The first sentences are being checked…");
  await expect(writing.locator(".b-facts")).toHaveText("0 of 4 sentences checked · ✓ 0 backed · ✗ 0 removed · section 2 of 2");
  await expect(writing.locator(".m-live")).toHaveText("2 of 2 sections written");
  await stop(request, id);
});

test("Writing: a drafted sentence with its Statement Check verdict and its section (AC17)", async ({ page, context, request }) => {
  await holdAt(context, "report_writer.progress#4");
  const id = await submit(page, "q");
  const writing = row(page, "report_writer");
  await expect(writing).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(writing.locator(".tickbox .xf > div[data-on='1'] .qt")).toHaveText("Acme Institute 2 reports 12 million dollars for 2024.");
  await expect(writing.locator(".tickbox .xf > div[data-on='1'] .vd")).toHaveText("✓ backed by 1 finding · Widget funding");
  await expect(writing.locator(".b-facts")).toHaveText("2 of 4 sentences checked · ✓ 2 backed · ✗ 0 removed · section 2 of 2");
  await stop(request, id);
});

test("Reviewing: five checks land with no score; the status line waits for the route (AC18)", async ({ page, context, request }) => {
  await holdAt(context, "graph.report.reviewed");
  const id = await submit(page, "q");
  const reviewing = row(page, "report_reviewer");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("Review done · deciding what happens next…", { timeout: 30_000 });
  await expect(reviewing.locator(".rv-list > .ln .tt")).toHaveText([
    "Covers your whole question", "Rests on strong evidence", "Every claim is credited correctly", "Honest about what is uncertain", "Easy to read",
  ]);
  await expect(reviewing.locator(".rv-list > .ln[data-topic='done']")).toHaveCount(5);
  await expect(reviewing.locator(".pb.ind")).toHaveAttribute("data-on", "0");
  await expect(reviewing.locator(".rv-notes-h")).toHaveCount(0);
  await expect(reviewing).not.toContainText(/\d\.\d\d/);
  await expect(reviewing.locator(".m-live")).toHaveText(/^reading the draft · \dm \d\ds$/);
  await stop(request, id);
});

test("a loop route holds Reviewing open on its checks and verdict, then hands over; Reviewing reopens (D39, AC18)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "scoped-redraft-after-a-named-defect", "X-Replay-Hold-After": "graph.route.decided" });
  const id = await submit(page, "q");
  const reviewing = row(page, "report_reviewer"), writing = row(page, "report_writer");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("1 thing to fix · sending the draft back to the writer", { timeout: 30_000 });
  await expect(reviewing.locator(".rv-list > .ln[data-topic='fail'] .tt")).toHaveText(["Easy to read"]);
  await expect(writing).toHaveAttribute("data-open", "0");
  await page.waitForTimeout(2_300); // HANDOFF_HOLD_MS (2000 ms, components/BriefSpine.tsx) and a margin
  await expect(writing).toHaveAttribute("data-open", "1");
  await expect(reviewing).toHaveAttribute("data-open", "0");
  await reviewing.locator("button.ps-toggle").click();
  await expect(reviewing).toHaveAttribute("data-open", "1");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("1 thing to fix · sending the draft back to the writer");
  await stop(request, id);
});

test.describe("the two loops (D21, AC20)", () => {
  test("the sheen runs only on Planning's skeleton slots, and the drift only while Reviewing's call runs", async ({ page, context, request }) => {
    await holdAt(context, "planner.progress");
    let id = await submit(page, "q");
    await expect(row(page, "planner").locator(".ps-slots .sk[data-on='1']")).toHaveCount(4, { timeout: 10_000 });
    await expect.poll(() => animations(page)).toContain("sheen");
    expect(await animations(page)).not.toContain("drift");
    await stop(request, id);

    await holdAt(context, "graph.node.started#6"); // the sixth start is Reviewing's (planner … report_reviewer)
    id = await submit(page, "q");
    await expect(row(page, "report_reviewer").locator(".pb.ind")).toHaveAttribute("data-on", "1", { timeout: 30_000 });
    await expect.poll(() => animations(page)).toContain("drift");
    expect(await animations(page)).not.toContain("sheen");
    await stop(request, id);

    await holdAt(context, "graph.report.reviewed");
    id = await submit(page, "q");
    await expect(row(page, "report_reviewer").locator(".pb.ind")).toHaveAttribute("data-on", "0", { timeout: 30_000 });
    await expect.poll(() => animations(page)).not.toContain("drift");
    expect(await animations(page)).not.toContain("sheen");
    await stop(request, id);
  });

  test.describe("under reduced motion", () => {
    test.use({ reducedMotion: "reduce" });
    test("neither loop runs: the skeleton bar is still and Reviewing's bar is a static full-width line", async ({ page, context, request }) => {
      await holdAt(context, "planner.progress");
      let id = await submit(page, "q");
      await expect(row(page, "planner").locator(".ps-slots .sk[data-on='1']")).toHaveCount(4, { timeout: 10_000 });
      await page.waitForTimeout(300);
      expect(await animations(page)).not.toContain("sheen");
      await stop(request, id);

      await holdAt(context, "graph.node.started#6");
      id = await submit(page, "q");
      const bar = row(page, "report_reviewer").locator(".pb.ind[data-on='1'] > i");
      await expect(bar).toHaveCount(1, { timeout: 30_000 });
      await page.waitForTimeout(300);
      expect(await animations(page)).not.toContain("drift");
      expect(await bar.evaluate((i) => [getComputedStyle(i).opacity, Math.round(i.getBoundingClientRect().width) === Math.round(i.parentElement!.getBoundingClientRect().width)]))
        .toEqual(["0.35", true]);
      await stop(request, id);
    });
  });
});

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    test.use({ viewport });
    test("no step's brief scrolls the page sideways (AC21)", async ({ page, context, request }) => {
      for (const [hold, stage] of [["planner.progress#2", "planner"], ["source_evaluator.progress#2", "source_evaluator"], ["evidence_verifier.progress#2", "evidence_verifier"], ["report_writer.progress#4", "report_writer"], ["graph.report.reviewed", "report_reviewer"]] as const) {
        await holdAt(context, hold);
        const id = await submit(page, "q");
        await expect(row(page, stage)).toHaveAttribute("data-open", "1", { timeout: 30_000 });
        await page.waitForTimeout(1_500); // the opening row's lines have risen
        const [scroll, inner] = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
        expect(scroll, `${hold}: no page-wide horizontal scroll`).toBeLessThanOrEqual(inner);
        await stop(request, id);
      }
    });
  });
}
```


`web/e2e/briefs.spec.ts` — replace

```ts
  await expect(planning.locator(".m-out")).toHaveText("3 sub-topics");
```

with

```ts
  // notes-progress-report spec §6.3: the outcome ends with Planning's duration.
  await expect(planning.locator(".m-out")).toHaveText(/^3 sub-topics · \dm \d\ds$/);
```

`web/e2e/briefs.spec.ts` — replace

```ts
  await expect(planning.locator(".ps-titles > .ln")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
```

with

```ts
  await expect(planning.locator(".ps-slots > .ln:not([data-gone]) .tt")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
```

`web/e2e/briefs.spec.ts` — replace

```ts
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  await settledTransitions(page);
```

with

```ts
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  // Decision D39: Reviewing holds its verdict for HANDOFF_HOLD_MS before Researching reopens.
  await expect(page.locator('#spine li[data-stage="researcher"]')).toHaveAttribute("data-open", "1");
  await settledTransitions(page);
```

`web/e2e/support.ts` — replace

```ts
      if (el.classList.contains("m-out")) return "m-out";

```

with

```ts
      if (el.classList.contains("m-out")) return "m-out";
      // notes-progress-report spec §6.3-§6.7: one text of a cross-fading stack (a status line, a fact).
      if (el.parentElement?.classList.contains("xf")) return "xf";

```

`web/e2e/motion.spec.ts` — replace

```ts
  // Row k+1 (Evaluating sources) as the "to": node fills and height opens at 600ms, its one line rises at 900ms.
  const to = only(records, { stage: "source_evaluator", handoff: "to" });
  expect(timings(only(to, { part: "bullet", prop: "background-color" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "line", prop: "opacity" }))).toEqual(["900/240"]);
  expect(timings(only(to, { part: "line", prop: "transform" }))).toEqual(["900/240"]);
```

with

```ts
  // Row k+1 (Evaluating sources) as the "to": node fills and height opens at 600ms, its three lines (the
  // lead, the bar, the split; notes-progress-report spec §6.4) rise from 900ms, 60ms apart.
  const to = only(records, { stage: "source_evaluator", handoff: "to" });
  expect(timings(only(to, { part: "bullet", prop: "background-color" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "line", prop: "opacity" }))).toEqual(["1020/240", "900/240", "960/240"]);
  expect(timings(only(to, { part: "line", prop: "transform" }))).toEqual(["1020/240", "900/240", "960/240"]);
```

`web/e2e/motion.spec.ts` — replace

```ts
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
```

with

```ts
  // Reviewing's first hand-off is the default case's extra pass (decision D39): graph.route.decided holds
  // Reviewing as it was — active, open, on its checks and its verdict — for HANDOFF_HOLD_MS, then it takes
  // the from role. So nothing on its row moves between its last to-role transition (as the review lands) and
  // its first from-role one, 2 s later (it is never painted pending)…
  const onReviewing = records.filter((r) => r.stage === "report_reviewer");
  const firstFrom = onReviewing.findIndex((r) => r.handoff === "from");
  const lastTo = onReviewing.slice(0, Math.max(firstFrom, 0)).map((r) => r.handoff).lastIndexOf("to");
  expect(firstFrom).toBeGreaterThan(0);
  // …but the route decision's verdict, which cross-fades into the status line as the hold begins
  // (notes-progress-report spec §6.7), an opacity change in place.
  expect(onReviewing.slice(lastTo + 1, firstFrom).filter((r) => r.part !== "xf")).toEqual([]);
  // Both of its from-role folds keep the from-role timings: after the hold, as a pending row (its lines and
  // height only), and at its completion before Publishing, when the subtitle cross-fade and the connector
  // fill run too. Its lines and height fold only as far as they had opened — before Publishing, Reviewing
  // is active for about four paced events, less than its own 600/900 ms opening delays — so those are
  // held to their timing, not their presence.
```

`web/e2e/motion.spec.ts` — replace

```ts
  // A reader's open (1A): height at once over 420ms, then the three titles rise 60ms apart from 280ms.
  const opened = only(records, { stage: "planner", handoff: null, open: "1" });
  expect(timings(only(opened, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["0/420"]);
  expect(timings(only(opened, { part: "line", prop: "opacity" }))).toEqual(["280/240", "340/240", "400/240"]);
```

with

```ts
  // A reader's open (1A): height at once over 420ms, then the status line and the three slots rise 60ms
  // apart from 280ms (notes-progress-report spec §6.3; the surplus fourth slot has left the layout).
  const opened = only(records, { stage: "planner", handoff: null, open: "1" });
  expect(timings(only(opened, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["0/420"]);
  expect(timings(only(opened, { part: "line", prop: "opacity" }))).toEqual(["280/240", "340/240", "400/240", "460/240"]);
```

`web/e2e/motion.spec.ts` — replace

```ts
  // A topic done: the ✓ draws over 360ms after 80ms; the dot fades over 200ms.
  expect(timings(only(records, { part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { part: "dot", prop: "opacity" }))).toEqual(["0/200"]);
```

with

```ts
  // A topic done: the ✓ draws over 360ms after 80ms; the dot fades over 200ms.
  expect(timings(only(records, { stage: "researcher", part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { stage: "planner", part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { part: "dot", prop: "opacity" }))).toEqual(["0/200"]);
  // Reviewing's five checks land 60ms apart (notes-progress-report spec §6.7).
  expect(timings(only(records, { stage: "report_reviewer", part: "check", prop: "stroke-dashoffset" })))
    .toEqual(["140/360", "200/360", "260/360", "320/360", "80/360"]);
```


```bash
(cd web && npm run -s typecheck; echo "typecheck exit $?")
(cd web && npx playwright test e2e/progress.spec.ts --project=chromium --list 2>&1 | tail -1; npx playwright test --project=chromium --list 2>&1 | tail -1)
```

Expected: `typecheck exit 0`; `Total: 12 tests in 1 file`; `Total: L0 + 12 tests in` one more file than at Task 1 (planning, on D + A + C: `88 tests in 22 files`).

- [ ] **Step 3: Run the briefs, motion and progress specs** **[not run in planning]**

```bash
netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'
(cd web && npm run -s build && npx playwright test e2e/progress.spec.ts e2e/briefs.spec.ts e2e/motion.spec.ts e2e/reduced-motion.spec.ts --project=chromium 2>&1 | tail -3)
```

Expected: the `netstat` line prints nothing; then `21 passed` (progress 12, briefs 5, motion 2, reduced motion 2). Reasoning: each hold point names an event the planning capture holds (Task 8 Step 2), every asserted text is one the Vitest tests derive from the same events, and each test ends with a `202` stop. D39's case holds the redraft case after `graph.route.decided`: the hold is the page's own timer, so it ends while the stream is held. If a `motion.spec.ts` timing differs, compare it with the CSS above before changing either. The Reviewing window there (`onReviewing.slice(lastTo + 1, firstFrom)`) is re-derived for D39 [not run in planning]:
- Reviewing's first from-role is now the extra pass's, at the hold's end.
- Its last to-role transitions are the review landing (`graph.report.reviewed`, 150 ms after Reviewing starts, inside the to-role's 2 s).
- The hold begins one event later and drops the role, so the verdict's cross-fade is recorded with no role. That cross-fade is the one `xf` record between the two.
- After the hold Reviewing folds as a pending row: lines `0/180` and height `100/420`, with no `m-out` or connector transition, because those run only when a row turns done or loop. Every existing assertion on `reviewing` therefore still holds.

If the window holds anything else, report it.

- [ ] **Step 4: Run the whole chromium project** **[not run in planning]**

```bash
(cd web && npx playwright test --project=chromium 2>&1 | tail -2)
```

Expected: `L0 + 12 passed`.

- [ ] **Step 5: Commit**

```bash
git add web/app/globals.css web/e2e/progress.spec.ts web/e2e/briefs.spec.ts web/e2e/support.ts web/e2e/motion.spec.ts
git commit -m "feat(web): the briefs' styles and loops, and the briefs on the replay stream"
```

---

### Task 13: The step briefs' captures (spec §11.3)

**Files:**
- Modify: `web/e2e/visual.spec.ts` (the `09-running-extra-pass` capture waits for Researching to reopen after D39's hold, keeping the design reference's subject; the step briefs' captures appended)

**Interfaces:**
- Consumes: Tasks 7–12.
- Produces: `13-planning-brief`, `13-planning-brief-phone`, `14-evaluating-brief`, `15-verifying-brief`, `16-writing-brief`, `17-reviewing-brief`.

- [ ] **Step 1: The captures**

`web/e2e/visual.spec.ts` — replace

```ts
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      await shoot(page, `09-running-extra-pass${suffix}`);
```

with

```ts
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      // Decision D39: Reviewing holds its verdict for HANDOFF_HOLD_MS first; 09 shows Researching reopened.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"]')).toBeVisible();
      await shoot(page, `09-running-extra-pass${suffix}`);
```

Append to `web/e2e/visual.spec.ts`:

```ts

// notes-progress-report spec §11.3: each step's brief, held with X-Replay-Hold-After (§6.10) at a moment
// that shows its body, then stopped (Phase D's POST /stop). Planning also at phone width.
const BRIEFS = [
  { name: "13-planning-brief", hold: "planner.progress#2", stage: "planner", ready: ".ps-slots > .ln[data-topic='running']" },
  { name: "14-evaluating-brief", hold: "source_evaluator.progress#2", stage: "source_evaluator", ready: ".stats .v" },
  { name: "15-verifying-brief", hold: "evidence_verifier.progress#2", stage: "evidence_verifier", ready: ".tickbox .qt" },
  { name: "16-writing-brief", hold: "report_writer.progress#4", stage: "report_writer", ready: ".tickbox .qt" },
  { name: "17-reviewing-brief", hold: "graph.report.reviewed", stage: "report_reviewer", ready: ".rv-list > .ln[data-topic='done']" },
] as const;
for (const [suffix, viewport, briefs] of [["", null, BRIEFS], ["-phone", PHONE, BRIEFS.slice(0, 1)]] as const) {
  test.describe(`step briefs${suffix}`, () => {
    if (viewport) test.use({ viewport });
    for (const brief of briefs) {
      test(`${brief.name}${suffix}`, async ({ page, request, context }) => {
        await context.setExtraHTTPHeaders({ "X-Replay-Hold-After": brief.hold });
        const id = await submit(page, "What is the current state of grid-scale battery storage?");
        const row = page.locator(`#spine li[data-stage="${brief.stage}"]`);
        await expect(row).toHaveAttribute("data-open", "1", { timeout: 30_000 });
        await expect(row.locator(brief.ready).first()).toBeVisible();
        await page.waitForTimeout(1_500); // the opening row's lines have risen and the ticker has settled
        await shoot(page, `${brief.name}${suffix}`);
        expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
      });
    }
  });
}
```


```bash
(cd web && npm run -s typecheck; echo "typecheck exit $?")
(cd web && npx playwright test --project=visual --list 2>&1 | tail -1)
```

Expected: `typecheck exit 0`; `Total: L1 + 6 tests in 1 file` (planning, on D + A + C: `23`).

- [ ] **Step 2: Take every capture** **[not run in planning]**

```bash
(cd web && npm run -s build && VISUAL_CHECKPOINT=phase-b-progress npx playwright test --project=visual 2>&1 | tail -2)
ls web/visual/phase-b-progress | grep -E "^1[3-7]-"
```

Expected: `L1 + 6 passed`; six files, `13-planning-brief.png`, `13-planning-brief-phone.png`, `14-evaluating-brief.png`, `15-verifying-brief.png`, `16-writing-brief.png`, `17-reviewing-brief.png` (every capture also asserts no page-wide horizontal scroll).

- [ ] **Step 3: Review each capture at full height against its artboard**

Open each image beside its pick in `.superpowers/progress-canvas/project/` (`Main.dc.html` option B, `Evaluating.dc.html` A, `Verifying.dc.html` C, `Writing.dc.html` E, `Reviewing.dc.html` A revised) and check, writing a one-line verdict per capture into the task's report:
- the step's row is open, its subtitle green, and its body's lines follow the order of §6.3–§6.7;
- only status colour: green for the running step's own progress and kept verdicts, amber for ✗, a dropped finding or a removed sentence; nothing purple; no score anywhere in Reviewing;
- Planning: titles in the slots, the running slots' dots, no fourth skeleton; at 390 px the slot facts sit under the titles;
- Evaluating: the bar full, `4 of 4`, the split; Verifying and Writing: the ticker's text, its verdict line, the tally; Reviewing: five ✓ with `Review done · deciding what happens next…`.
A difference from the artboard that §6 prescribes (five criteria, no score, the app's 60 ms rhythm) is expected; any other difference is reported, not fixed in this task.

- [ ] **Step 4: Commit**

```bash
git add web/e2e/visual.spec.ts
git commit -m "test(visual): the step briefs' captures, held with X-Replay-Hold-After"
```

---

### Task 14: The design record (spec §6.11)

**Files:**
- Modify: `docs/design/DESIGN.md` (§3.4 "What an open row says", with D39's exception and Reviewing's hold, and the loops; §3.5's active-row rule and a new paragraph after item 3 on D39's hold, which paints and never marks; §5.6's bullets, D39's hold among them, kept under reduced motion; §5.7 "What each surface derives" and "Delivery is live")

**Interfaces:**
- Consumes: Tasks 2–13 (what the document now describes).
- Produces: the amended DESIGN.md.

- [ ] **Step 1: The amendments**

`docs/design/DESIGN.md` — replace

```markdown
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

with

```markdown
**What an open row says** (picks 1A, 2C; notes-progress-report §6, 2026-09-30). The
active row is always open; a done or loop row is closed on its outcome line and
reopens from its head (a `button[aria-expanded]` over the head); a pending row never
opens, save Reviewing's after a loop route (D39, §3.5). While a row runs its subtitle is green and live: Planning's elapsed time
(`Xm SSs`); Researching's `{done} of {n} topics done · {pages} pages read ·
{findings} findings` (or `{n} topics · researching` before the first topic is done);
`{rated} of {n} rated`; `{checked} of {n} checked`; `{k} of {n} sections written`,
then `writing the bottom line`; `reading the draft · {elapsed}`. Each row's body is its
own (picks `Main.dc.html` B, `Evaluating.dc.html` A, `Verifying.dc.html` C,
`Writing.dc.html` E, `Reviewing.dc.html` A revised):

- **Planning** — a status line that cross-fades `Reading your question…` → `Drafting
  a plan for your question…` → `Checking the plan covers everything you asked…` →
  `Fixing {k} topics the check flagged…` → `Plan ready · research starts now`, over
  four skeleton slots that fill with the plan's titles and tick as the check passes
  (`being fixed`, `fixed`, an amber ✗ `still flagged`); a research note read during
  Planning has its own slot, `joins the plan`, then `from your note`.
- **Researching** — the pass's topics: ○ `not yet`, ● `reading` (green, with the
  halo), ✓ `{n} findings`. Topics run concurrently, so several can be reading at once.
- **Evaluating sources** — `Rating {n} sources for trustworthiness and relevance`, a
  determinate bar, and Rated / Strong / Fair / Weak, each `not yet` until the first
  batch lands.
- **Verifying evidence** and **Writing report** — a ticker (`Just checked`, `Just
  written`) showing one real finding or drafted sentence at a time with its verdict
  in words — a kept one green, a dropped finding or a removed sentence amber — over a
  determinate bar and a tally.
- **Reviewing** — five criteria (`Covers your whole question`, `Rests on strong
  evidence`, `Every claim is credited correctly`, `Honest about what is uncertain`,
  `Easy to read`) and the reader's notes: rings that read `reading` under an
  indeterminate bar while the one call runs, then ✓, or an amber ✗ with the issue in
  plain words. Never a score. When the route sends the run back, Reviewing stays open
  on its checks and its verdict line for 2s before the run goes back (D39, §3.5).
- **Publishing** — `Saving the report and evidence log`.

Once done, a row's subtitle is its outcome: `{n} sub-topics · {k} from your note ·
{duration}`; `{n} topics · {pages} pages read · {findings} findings`; `{n} sources
rated · {s} strong · {f} fair · {w} weak`; `{v} verified · {c} corrected · {d}
dropped`; `Report drafted · {s} sentences · {c} citations`; Reviewing's route in
words with its duration (`Accepted · all 5 met · {duration}`, `{d} things to fix ·
back to the writer`, `Not accepted · {m} of 5 met`); `Published`. Reviewing's static
meta is `5 checks`, and `5 checks · your notes` while the run holds a note. A reopened
row shows its final body. A measured zero reads in words (`no findings`), never a
bare 0.
```

`docs/design/DESIGN.md` — replace

```markdown
**One loop, and it is a ring.** The running node's halo eases between a 4px and a
7px radius at 13–20% over `2.2s`; the header status dot and each running topic's
dot in the Researching brief use the same `halo`. That is the whole looping budget
for this screen: a row opening or closing, the hand-off between steps, a topic's
drawn ✓ and a count's tween each run once (§5.6).

A second loop — a soft radial blob that travelled down the running row — was built
and then removed. It failed on craft rather than on principle: a blurred
`radial-gradient` moving across the row has no clean edge, so it read as a smear
drifting over the hairline connectors rather than as progress, and it drew the eye
to the space *between* steps instead of to the step that was running. The lesson
recorded here is that on this surface a shape has to be a ring or a line to sit
cleanly next to a 1px connector; anything without an edge reads as dirt.
```

with

```markdown
**Three loops, each a ring or a line, each only while its step runs** (D21,
2026-09-30). The running node's halo eases between a 4px and a 7px radius at 13–20%
over `2.2s`; the header status dot and each running topic's or slot's dot use the same
`halo`. Planning's skeleton slots carry a sheen — a `--fg` gradient at 7% sweeping
the bar over `--motion-halo` — only while Planning is the running row and its titles
have not arrived. Reviewing's bar carries a drift — a 28% segment crossing a hairline
over `--motion-halo` — only while its one call runs; it stops when the review lands.
Everything else runs once: a row opening or closing, the hand-off between steps, a
drawn ✓ or ✗, a status line's cross-fade, a ticker's sample, a count's tween (§5.6).
Under reduced motion neither the sheen nor the drift runs: the skeleton bar is still,
and Reviewing's bar is a static full-width line at 35%.

An earlier second loop — a soft radial blob that travelled down the running row — was
built and then removed. It failed on craft rather than on principle: a blurred
`radial-gradient` moving across the row has no clean edge, so it read as a smear
drifting over the hairline connectors rather than as progress, and it drew the eye
to the space *between* steps instead of to the step that was running. The lesson
recorded here is that on this surface a shape has to be a ring or a line to sit
cleanly next to a 1px connector; anything without an edge reads as dirt — which is
why the two later loops are a line's sheen and a line's drift.
```

`docs/design/DESIGN.md` — replace

```markdown
3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass or a note pass, on Writing for a redraft, once the row has completed again.
```

with

```markdown
3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass or a note pass, on Writing for a redraft, once the row has completed again.

**A loop route holds its verdict for 2s** (D39, the human's ruling of 2026-09-30 on
notes-progress-report §6.7). The active row moves on the route decision as the table
says, but the painting waits: Reviewing stays painted as the row that ran — open on its
checks, its notes and the verdict line (`1 thing to fix · sending the draft back to the
writer`, `1 gap to fill · going back to research`, a note route's line) — for
`HANDOFF_HOLD_MS` (2,000ms), while the row the run returns to waits, closed and pending.
Then the ordinary hand-off runs: Reviewing folds with the from-role timings and that row
opens with the to-role's (§5.6). The hold paints and never marks: Reviewing stays
hollow, as item 2's reset left it, and its own completion stays inert. Afterwards the
hollow Reviewing row keeps a toggle on its head that reopens it on that review — the
hollow node and `5 checks` stay — until Reviewing runs again. A Stop during the hold
ends it.
```

`docs/design/DESIGN.md` — replace

```markdown
replays the whole log as one burst and the reviewer's own start arrives with its
snapshot, so completions stay the rule and read the same either way.
```

with

```markdown
replays the whole log as one burst (the reviewer's own start is live too, notes-progress-report
§6.1), so completions stay the rule and read the same either way.
```

`docs/design/DESIGN.md` — replace

```markdown
- **One decorative loop, and it is not load-bearing.** `halo` runs at
  `--motion-halo: 2200ms` on the running node, on each running topic's dot and on the header status dot. It stops
  under reduced motion, and §3.4's table is identical either way — no state on this
  screen depends on an animation being mid-cycle. A second loop was tried and
  removed; §3.4 records why.
```

with

```markdown
- **Each step's own brief changes in place** (notes-progress-report §6, 2026-09-30).
  A status line, a slot's title and a check's fact cross-fade: opacity over
  `--motion-base` and a 5px settle over `--motion-fluid`. A slot or a note's slot that
  arrives later rises in as an acknowledgement does; a surplus skeleton fades out,
  then leaves the layout. A determinate bar fills over `--fill-line`. A ticker's sample
  changes at most once every 1,200ms (`TICKER_HOLD_MS`, a dwell like the hand-off's
  hold, not an animation), and a burst ends on its newest sample. When a review lands
  its checks change 60ms apart. **Under reduced motion** every cross-fade is opacity
  only over 160ms with no settle, a skeleton leaves at once, the bars jump, the ✓ and
  ✗ appear without drawing and counts jump.
- **A loop route holds the verdict** (D39, 2026-09-30). When the route sends the run
  back, Reviewing stays open on its checks and its verdict for `HANDOFF_HOLD_MS`
  (2,000ms) before the hand-off runs (§3.5). The hold is a dwell on a timer, like the
  hand-off's own hold, not an animation, so it is kept under reduced motion; the fold
  and the open that follow are the hand-off's, opacity only under reduced motion.
- **Three decorative loops, none load-bearing** (D21). `halo` runs at
  `--motion-halo: 2200ms` on the running node, on each running topic's and slot's dot
  and on the header status dot; Planning's skeleton sheen and Reviewing's drift run
  at the same duration, each only while its step runs (§3.4). All three stop under
  reduced motion, and §3.4's table is identical either way — no state on this screen
  depends on an animation being mid-cycle. A blob that travelled down the running row
  was tried and removed; §3.4 records why.
```

`docs/design/DESIGN.md` — replace

```markdown
the failed stage's skipped rows from `graph.node.skipped` and its Publishing row
from `graph.session.completed`.
```

with

```markdown
the failed stage's skipped rows from `graph.node.skipped` and its Publishing row
from `graph.session.completed`. Each step's own brief (§3.4) reads its progress event —
`planner.progress` (Planning's status line and slots, then `planner.planning.completed`'s
final slot states and note slots), `source_evaluator.progress` (the bar and the split),
`evidence_verifier.progress` and `report_writer.progress` (the ticker, the bar and the
tally) — each a cumulative snapshot whose latest wins; Reviewing reads
`graph.report.reviewed`'s `criteria` and `notes`, its verdict from `graph.route.decided`
(with the latest `graph.quality.assessed` for a refusal), and every row's elapsed time
and duration from its `graph.node.started` and `graph.node.completed` timestamps.
```

`docs/design/DESIGN.md` — replace

```markdown
**Delivery is live.** Each event reaches the stream as it happens: `graph.node.started`
is published live when an agent node starts (the reviewer, Publishing and the two hop
nodes keep snapshot publication); each agent's progress events are published as the
agent builds them — the researcher's `researcher.sub_topic.started`,
`researcher.tool_call` (built when its step's observation is recorded) and
`researcher.sub_topic.completed` while its topics run, concurrently. Events that are not
published live — the graph's route, review, hop and completion events, and
`researcher.research.completed` — arrive with their node's snapshot, and an id already
published live is never published twice (`graph/live.py`, `graph/orchestrator.py`;
api-gaps 3.7, closed). The screen therefore moves within a node: the Researching
checklist ticks topics off as they finish. Every derivation above is still written so
the state after event *k* depends only on events 1..*k*: a live run, a 100-event replay
and a reconnect's burst paint the same screen.
```

with

```markdown
**Delivery is live.** Each event reaches the stream as it happens: `graph.node.started`
is published live when an agent node or the reviewer starts (Publishing and the hop
nodes keep snapshot publication); each agent's progress events are published as the
agent builds them — the researcher's `researcher.sub_topic.started`,
`researcher.tool_call` (built when its step's observation is recorded) and
`researcher.sub_topic.completed` while its topics run, concurrently; the four step
progress events (`planner.progress`, `source_evaluator.progress`,
`evidence_verifier.progress`, `report_writer.progress`) as each unit of work starts or
settles; and the reviewer's `graph.report.reviewed` the moment its review returns. The
step progress events are live-only: they are never in the run's state, so a checkpoint,
the quality record and `graph.node.completed.event_count` never hold them, and a
reconnect replays them from the session's own log. Events that are not published
live — the graph's route, hop and completion events, and
`researcher.research.completed` — arrive with their node's snapshot, and an id already
published live is never published twice (`graph/live.py`, `graph/orchestrator.py`;
api-gaps 3.7, closed). The screen therefore moves within a node: the Researching
checklist ticks topics off as they finish. Every derivation above is still written so
the state after event *k* depends only on events 1..*k*: a live run, a 100-event replay
and a reconnect's burst paint the same screen.
```


- [ ] **Step 2: Check that no stale statement remains**

```bash
grep -nE "Reviewing the draft on 7|One loop, and it is a ring|One decorative loop|the reviewer's own start arrives with its|Breaking your question into sub-topics" docs/design/DESIGN.md web/lib web/components | wc -l
```

Expected: `0`.

- [ ] **Step 3: Commit**

```bash
git add docs/design/DESIGN.md
git commit -m "docs(design): each step's brief, the two loops, the loop route's hold, live progress events"
```

---

### Task 15: Final verification (AC13–AC21)

**Files:** none changed.

- [ ] **Step 1: The full Python suite**

```bash
.venv/Scripts/python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config 2>&1 | tail -1
```

Expected: `P0 + 40 passed, 2 deselected` and nothing failed (planning: `5057 passed`, from `5017`, on D + A; after C's `c0951e35`, `P0` is `5069`, so `5109`).

- [ ] **Step 2: The web**

```bash
(cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  ")
(cd web && npm run -s typecheck; echo "typecheck exit $?")
(cd web && npm run -s check:css)
```

Expected: `F0 + 4` files and `V0 + 61` tests, all passed; `typecheck exit 0`; `OK`.

- [ ] **Step 3: Live-only, with no provider text, on the real graph (AC13)**

```bash
.venv/Scripts/python.exe -m pytest "tests/test_api/test_replay.py::test_progress_events_live_only" "tests/test_cli/test_render.py::test_cli_unchanged_for_progress_types" "tests/test_agents/test_events.py::test_the_no_provider_text_rule_binds_state_bound_events" -q 2>&1 | tail -1
git diff --stat <BASE> HEAD -- src/deep_research/agents/prompts.py | tail -1
```

Type the commit id Task 1 Step 3 wrote down in place of `<BASE>`. Expected: `3 passed`; nothing (no prompt text changed since Task 1; a change the latency work made before this plan ran is not B's, O6).

- [ ] **Step 4: Playwright, both projects** **[not run in planning]**

```bash
netstat -ano | grep -E ':(8010|3010|3011) .*LISTENING'
(cd web && npm run -s test:e2e 2>&1 | tail -2)
(cd web && VISUAL_CHECKPOINT=phase-b-final npx playwright test --project=visual 2>&1 | tail -2)
```

Expected: nothing; `L0 + 12 passed`; `L1 + 6 passed`. The report captures are Phase C's: with C at `c0951e35` the default case's Key figures prints five rows labelled by source, with no "Showing…" caption (C's D40). That is C's, not a B change.

No commit: nothing changed.

---

## Acceptance-criteria coverage

| AC | Where it is proven |
|---|---|
| AC13 | Task 2 (`test_the_no_provider_text_rule_binds_state_bound_events`, `test_cli_unchanged_for_progress_types`, the planner tests' `SECRET` check), Tasks 3–5 (each agent's live test: progress published, never returned), Task 4 (`SECRET_REASON`), Task 5 (`SECRET_VERDICT`), Task 7 (`test_progress_events_live_only`: on the real graph, absent from `state.events` and every `event_count`, no `://` in any string). The allow-list is tested by exclusion, not field by field: ambiguity 21 |
| AC14 | Task 2 (`test_planner_progress_states`: sound, local repair, review repair then sound, failed review, failed review repair), Tasks 9–11 (state, slots, status line, note slots, subtitle, outcome with note clause and duration), Task 12 (`progress.spec.ts` Planning) |
| AC15 | Task 3 (`test_evaluator_progress_split`, reused and capped, idempotent, the completed split), Tasks 9–10 (`not yet`, the bar, the outcome), Task 12 |
| AC16 | Task 4 (`test_verifier_first_event_counts_figure_match`, `test_verifier_tally_ends_on_the_completed_counts[2]`/`[5]`, `test_progress_counts_idempotent`, `test_verifier_progress_samples`), Task 10 (verdict words, amber `kept: false`, `ticker.test.tsx` pace), Task 12 |
| AC17 | Task 5 (`test_writer_progress_fraction_monotonic`: real sentences, Statement Check verdicts, monotonic fraction, `bottom_line` phase), Task 10 (`writing the bottom line`; the two placeholders, O3), Task 12 (`progress.spec.ts` Writing, with `The first sentences are being checked…` at `report_writer.progress#3`) |
| AC18 | Task 6 (`test_reviewed_event_five_criteria_mapping`, `test_reviewed_event_mixed_note_steering`, `test_reviewer_started_live`, `test_an_unscored_review_marks_no_criterion`), Task 10 (`step-briefs.test.ts` Reviewing: five rows, issue words, notes, every route's verdict incl. both `m = 5` refusals), Task 11 (no score in any brief of either capture; D39's hold, hand-over, reopen and Stop in `brief-spine.test.tsx`), Task 12 (`progress.spec.ts` Reviewing, and D39's case on the redraft case held at `graph.route.decided`) |
| AC19 | Task 9 (`progress-state.test.ts` burst safety), Task 11 (`briefs.test.ts` over both re-captured fixtures) |
| AC20 | Task 12 (CSS: token durations only; `progress.spec.ts` "the two loops", with and without reduced motion; `motion.spec.ts`'s reduced-motion test), Task 10 (`TICKER_HOLD_MS` is a dwell) |
| AC21 | Task 7 (`test_replay_restamps_and_holds`, `test_published_timestamps_are_the_release_times`, `test_hold_after_holds_the_stream_until_the_session_is_stopped`, the proxy test), Task 12 (`progress.spec.ts`: no horizontal scroll at 1252 and 390 px), Task 13 (captures) |

## Spec Phase B items, and where each lands

| Spec | Task |
|---|---|
| §4 item 1 (live-only; what metadata may carry; the two docstrings) | 2 (docstrings), 2–7 |
| §4 item 4 (`RunEvent.timestamp`, `Handler`'s third argument) | 9 (O1) |
| §6.1 event contract, all seven rows | 2, 3, 4, 5, 6 |
| §6.2 hooks, sample choice, writing fraction, evaluator split, criteria map | 2, 3, 4, 5, 6 |
| §6.3 Planning | 2, 9, 10, 11, 12 |
| §6.4 Evaluating | 3, 9, 10, 12 |
| §6.5 Verifying (incl. D30, `TICKER_HOLD_MS`) | 4, 9, 10, 12 |
| §6.6 Writing (incl. D34) | 5, 9, 10, 12 |
| §6.7 Reviewing (D23, D35; the route table; Fable's `passed` → `not found`; "5 checks"); its last bullet as D39 amends it (the loop route's hold) | 6, 9, 10, 11, 12, 13, 14 |
| §6.8 Researching unchanged | 11 (kept, tested) |
| §6.9 web state, bodies, clock, CSS, the two loops, reduced motion | 9–12 |
| §6.10 replay items 1–3 | 7, 8 |
| §6.11 documentation | 7 (README), 14 |
| §11.1 tests; §11.2 updates (run-state lists, five substitutes, briefs, brief-spine, running-pipeline, briefs.spec, visual) | 2–13 |
| §11.3 captures | 13 |
| §12 R7 (counted), R8 (five substitutes found and updated) | 8, 5 |
| Phase D's O1 (README sentence) and O3 (active-row pin) | 7, 8 |

## Self-review

- **Spec coverage.** Every §6 subsection, §4 items 1 and 4, AC13–AC21, the Phase B rows of §9 and §11, R7 and R8 map to a task above, and D39 amends §6.7's last bullet as the human ruled. One statement is kept differently and recorded as a spec amendment line: `RunEvent.timestamp`'s optionality (O1). The e2e and capture hold points beyond §11.1's five are ambiguity 18, recorded, not an issue. O2 is closed by D39, O3 by review 1.
- **Placeholder scan.** No step says "TBD", "similar to" or "add tests"; every code step carries its full code. `BASE`, `P0`, `V0`, `F0`, `L0`, `L1` and the four pins are values Task 1 Step 3 records on the merged tree, because the merge decides them; each later Expected line states the delta, and the values planning observed on D + A + C (C's plan at `bd48725c`). The one parameter in a command, Task 15 Step 3's `<BASE>`, is that recorded commit id.
- **Type consistency.** `planning_completed_event(..., states=)` (Task 2) is what `PlannerAgent.run` passes; `check_statements(..., on_batch=)` (Task 4) is what `_check` passes (Task 5) and what the five substitutes accept; `report_review_completed_event(..., criteria, notes)` (Task 6) takes `review_criteria`/`review_note_results`; `RunEvent.timestamp` and `Handler`'s third argument (Task 9) feed `startedAt`/`durations`, which `liveSubtitle` and `rowBrief` read (Tasks 10–11); `BriefBody`'s kinds (Task 10) are what `rowBrief` returns and `BriefSpine` switches on (Task 11); `StepBodies`' props take exactly the `Extract<BriefBody, …>` members; `CheckMark`'s `"stopped"` is the `data-topic` Phase D's frozen topics use; Writing's body's `placeholder` (Task 10) is what `WritingLines` shows; `Handoff.held` (Task 11) is set and cleared only inside `BriefSpine`.

## Review round 1 (2026-09-30): findings and how each was resolved

spec-plan-reviewer reviewed `e6a9c10b` (`.superpowers/reviews/2026-09-30-plan-b-review-1.md`). It blocked the plan on two P1s, with four P2s and six P3s. Phase C's review (`.superpowers/reviews/2026-09-30-plan-c-review-1.md`, "Impact on Phase B's plan") listed what C's plan moves for B. The human ruled on O2: decision D39. Each item is resolved below.

The edited steps were re-run once, task by task, on an export of `73b4d7a6` with Phase D's, Phase A's (revised) and Phase C's (`bd48725c`) plans applied by their own blocks. That export carries C's code but not C's re-pin steps (O4). Results:
- Task 1 Step 2: `anchors: 165 exactly once; creates: 10 absent; appends: 13 onto files present`.
- Tasks 2–7: every pytest Expected line as now written. The fingerprint and byte-identical lines read as O4 explains.
- Task 8: the two cases captured in-process, `98` and `67` events with the same progress counts. The active-row pin read `2 failed | 1 passed (3)`, then `3 passed (3)` once rewritten; the API pin read `2 passed`.
- Tasks 9–11, whole suite: `34` / `293`, `37` / `327`, `37` / `338`; typecheck exit 0 after each.
- Task 12: `OK`; `Total: 12 tests in 1 file`; `Total: 88 tests in 22 files`. Task 13: `Total: 23 tests in 1 file`.
- Task 6: `ruff check --select I001,F` printed `All checks passed!` on its three files.

- **P1-1 — the human's O2 ruling (D39).** Applied as the review specified, with one deviation in the trigger.
  - `BriefSpine.tsx` (Task 11) gains:
    - `Handoff.held`, set by the held derivation;
    - an effect that ends the hold after `HANDOFF_HOLD_MS`;
    - the destination row painted pending while held, and no role while held;
    - `!handoff.held` on the awaited branch;
    - `reopenable` for the hollow Reviewing row.

    Nothing changes in `lib/briefs.ts` or `lib/run-state.ts`.
  - **The deviation.** The trigger is the lit arc's own destination (`run.arc !== null && run.active === ARCS[run.arc].to`), not `run.loop === "flowing"`. A loop's own start event in the same render would make the `loop` test miss the hold ("Decision D39"). A third Vitest case pins this: it fails under the review's condition and passes under this one.
  - **Reduced motion and Stop.** As the review said; DESIGN §5.6 records the first (Task 14).
  - **Tests.**
    - (a) `brief-spine.test.tsx` has the review's two cases and the same-render case. The fixture's not-met criterion is completeness (`coverage`), so its ✗ reads `Covers your whole question`. Observed: with the D39 edits undone, the hold case and the rewritten "never awaits…" case fail (`2 failed | 11 passed (13)`, before the same-render case was added); with them, all pass (`14 passed`). The Stop case passes either way: it guards that the hold's timer does not outlive a stop.
    - (b) `progress.spec.ts`'s D39 case runs on the redraft case. Its ✗ is `Easy to read` (`presentation`), read from the capture.
    - (c) `motion.spec.ts`'s window is re-derived (Task 12 Step 3). Its assertions hold unchanged; its comments now say why.
  - **Also moved by the hold**, found while applying it:
    - the existing "never awaits a row a loop sends the run back from" case, which asserted the drop and now asserts the hold and the timer's hand-over;
    - `briefs.spec.ts`'s arc test, whose title says "after Researching reopens" and which now waits for that;
    - the `09-running-extra-pass` capture, which now waits for Researching to reopen and so keeps the design reference's subject.
  - **Bookkeeping.**
    - D39 is recorded in its own section and amends §6.7's last bullet; O2 is closed.
    - DESIGN, Task 14: §3.4 (the pending row's exception, Reviewing's hold); §3.5 (a paragraph after item 3: the hold paints and never marks); §5.6 (the hold is a dwell, kept under reduced motion).
    - AC18's row names the new tests.
- **P1-2 — Phase C moves one anchor.** Task 6 now anchors `tests/test_imports.py` on C's line, which lists `note_outcomes`, and adds `review_brief` to it. A later revision of C's plan may move it again; Task 1 Step 2 stops if it does (O4).
- **P2-1 — the latency work merged first.**
  - (a) Task 1 Step 3 writes down `BASE` (`git rev-parse HEAD`). Task 15 Step 3 diffs `prompts.py` against it, and Global Constraints say "since Task 1".
  - (b) Task 1 Step 1 adds the tool-lock line: "do not stop; say so in the summary".
  - (c) O6 lists the anchors that may move and the standing remedy.
  - (d) `test_statement_check_reports_each_settled_batch` passes `batch_size=5`.
- **P2-2 — R8's substitute grep.**
  - Task 5 Step 1 lists the substitutes: twelve lines on D + A + C, C's `checker` fixture among them. Its pattern also catches `flaky_check` and the `ev.check_statements = …` installs, which the review's pattern missed.
  - Task 5 Step 4 checks that every `gate=None` line carries `on_batch`. For that check to mean something, `fake_check`'s and `flaky_check`'s new `on_batch=None` now sits on their `gate=None` line. On a continuation line, the check would have printed both.
  - Observed: the inventory as listed, and the check printed nothing.
- **P2-3 — Expected lines Phase C shifts.** O4 states them, with the values observed on D + A + C, and so does each step:
  - Task 1 Step 3's pins and baselines;
  - Task 2 Steps 5–6;
  - Task 5 Steps 4–6: the live test's sequence holds, and on any other, stop and report;
  - Task 6 Step 5 and Task 7 Step 6;
  - Task 8 Steps 1–2, unchanged;
  - Tasks 9–13's counts.

  Found while re-running: Task 5 Step 5's "before" line was wrong, on D + A as on D + A + C. Changing the report writer's module also fails `test_the_target_fingerprint_covers_the_shared_prompt_module` (`tests/test_evaluation/test_config.py:1771` at `73b4d7a6`), which compares the report writer's fingerprint with its pin. The line now reads `2 failed, 9 passed, 67 deselected`. Observed with C's pin in place before the re-pin.
- **P2-4 — `graph/nodes.py`'s import order.** Task 6 inserts `from deep_research.graph.review_brief import …` right before `from deep_research.graph.state import (`, so it lands after C's `note_outcomes` import. Observed: ruff's `I001` passes.
- **P3.**
  - **O3 closed.** Writing's placeholder reads `The first sentences are being checked…` once `partsReturned > 0` with no sample: `writingBody` has a new `placeholder`, and `WritingLines` shows it. `step-briefs.test.ts` gains one case, `step-bodies.test.tsx`'s Writing case checks the placeholder, and `progress.spec.ts` expects it at `report_writer.progress#3`.
  - **The four invented subtitles** are listed as (I) in Global Constraints' copy table.
  - **`parts_returned` counting a failed draft** is documented (ambiguity 7), not counted apart. *(Superseded 2026-10-01 by owner decision O2: counted apart as `parts_failed`; spec §6.1, §6.6.)*
  - **AC13.** Ambiguity 21 sets what the tests check against AC13's wording, and the AC13 row points to it.
  - **`review_brief.py`'s docstring** says why its results are not Phase C's `note_outcomes` (Task 6).
  - **O1** is recorded as a spec amendment line. **O7** is accepted and kept.
- **Phase C's review, "Impact on Phase B's plan".**
  - Items 1, 3, 5, 6 and 10: B's anchors are untouched (Task 1 Step 2).
  - Item 2, the bottom line's counts: the number of progress events is unchanged (Task 8 Step 2). The counts inside a pass's last event change, and nothing B asserts reads them.
  - Item 4: as P2-4 above.
  - Item 7, the pins after C: Task 1 Step 3 and O4.
  - Item 8, the web: B has no anchor in `ReportBody`. `visual.spec.ts`'s B edit (the 09 capture) and B's append hold with C's additions.
  - Item 9, the docs: B's DESIGN anchors hold with C's new §5.6 bullet. B no longer edits `web/README.md`'s `capture:visual` line, which C's M5 rewrites; B's own bullet names its five captures (Task 7).
