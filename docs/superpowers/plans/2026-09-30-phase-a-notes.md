# Notes, progress, report and Stop — Phase A (reader notes that land) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status** revised after Fable review 1 (`.superpowers/reviews/2026-09-30-plan-a-review-1.md`, on `3638ce10`: approved with changes, no Critical); every finding is applied as "Review 1 resolutions" records. Written 2026-09-30 by the spec-plan-author agent from the approved spec at `73b4d7a6`. Nothing in it is open for the human (see "Open issues"). · **Branch** `feat/notes-progress-report-stop`, executed after Phase D is merged into it.

**Goal:** A reader's new_angle note becomes its own required sub-topic built by code — appended to the plan when it arrives during Planning, researched in its own thread at once when it arrives during Researching, and given exactly one note pass (after which only its part and the bottom line are redrafted) when it arrives later — its coverage decided by its own targets, a mixed note's steering half still steering and judged, and no note ever ending `pending` in a finished session.

**Architecture:**
- **Helpers** (`agents/reader_notes.py`): `is_research_note`, `has_steering_kind`, `steering_view`/`steering_notes` (which requests print which notes, §5.1), `note_sub_topic` (moved from `graph/nodes.py`, with a `reason`), `NOTES_WAIT_S`, and the researcher's reads of the board, whose `NoteBoard` gains a change count (`version`, `wait_for_change`).
- **Planning** (§5.2): `PlannerAgent.run` appends the research notes' topics after the plan's when the plan is published; `planner.planning.completed` lists them with their `note_id`. **Research** (§5.3): `ResearcherAgent.run` replaces its one `gather` with a dispatcher — every topic its own task, a research note read meanwhile its own ungated thread, a 30 s wait for a note still being read, every thread cancelled and awaited on any exit by exception. **Later** (§5.4): `notes_due_a_pass` owes a research note its pass while it has no researched topic, whatever the review said; only notes with a steering kind redraft; the note pass reuses a failed thread's topic; after a note pass the writer drafts only the notes' parts and the bottom line.
- **Outcomes** (§5.6): `note_outcome` decides a research note by its topic's targets and a steering note by the review, never `pending` once `terminal`; `note_steering_outcome` gives a mixed note's steering half; the session response gains `steering_outcome`. **Web** (§5.7): `NoteState.kinds`/`threadStarted`, a research note's acknowledgement table, a mixed note's two-part caption.

**Tech Stack:** Python 3.12 (`.venv`), pydantic 2, LangGraph 1.2.10, pytest 9 + pytest-asyncio, ruff; Node v24, Next.js 16, React 19, Vitest 5 + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode. Windows 11; every command below is for PowerShell in the main checkout.

**Spec:** `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` (commit `73b4d7a6`, reviewed clean by Fable in three rounds) — Phase A only: §5 (5.1–5.9), the Phase-A items of §4 (items 2 and 5), AC1–AC12, AC34, AC35, the Phase-A rows of §11.1 and §11.2, and R1–R4, R10, R11. Decisions D1–D5, D20, D22 and D31 (§2) are closed; nothing here reopens them. Where this plan had to choose, the choice is under "Spec ambiguities resolved here".

**Sources of truth.** The spec's §2 decisions, then its §4 cross-phase contracts, then its §5. Phase D is planned in parallel (`docs/superpowers/plans/2026-09-30-phase-d-stop.md`, a draft this revision was checked against; see "Evidence"). Everything this plan takes from D is what spec §4 item 2 and §8.4 say D delivers, and Task 1 Step 1 checks each piece before anything is edited. Every `path:line` below was read at `73b4d7a6` (whose code is `f4282818`'s); D changes none of `agents/`, `graph/`, `runtime/`, `utils/` (§9: its one engine-side change is `tools/web_search.py`), so those lines hold at the start of Task 2. Lines in `api/` and `web/` files D edits are approximate; their anchors are text. The five spans that cover text D writes are marked "(D-touched)": Task 1 Step 2 checks what Task 7's four hold, and Task 10's, in `web/e2e/notes.spec.ts`, holds only the status check it rewrites. No exact anchor rests on text D writes.

**Evidence.** Planning implemented every task, from this document's own blocks, on 2026-09-30:
- An export of `73b4d7a6` was made in a scratch directory, and a stand-in for Phase D was applied to it: exactly §4 item 2 (`NoteOutcome` and `ReaderNoteResponse.outcome` gain `not_checked`; `note_outcome(..., *, terminal)` with its three `pending` exits reading `"not_checked" if terminal else "pending"`; `note_records(..., *, terminal)`; `session_note_fields` computes `terminal`; `ReaderNoteOutcome` gains `not_checked`; `OUTCOME_TEXT.not_checked = "not checked"`, `OUTCOME_TEXT.pending = "not checked yet"`; the e2e notes status reads `not_checked`), plus §8.4's `stopped` in `SessionStatus`, `TERMINAL_STATUSES` and `STATUS`.
- A script then applied each step's blocks in order, refusing any anchor that did not occur exactly once, and the step's tests were run. Every `Expected:` line of Tasks 2–10 is what that run printed, with `.venv\Scripts\python.exe` replaced by the same interpreter run from the export. Task 1 Steps 1-3 printed the lines given there on the stand-in export.
- The full suite on the finished export: `4997 passed` and two failures outside this plan — `tests/test_config.py::test_the_evidence_verifier_pipeline_config`, which needs the `.env` the export does not have (this checkout has one, so it passes here), and `tests/test_agents/test_researcher.py::test_two_reads_extractions_overlap_in_time`, a wall-clock test (`elapsed < 0.2`) that failed once while other suites loaded the machine and then passed five times out of five alone; an earlier full run of the same code passed it (`4998 passed` with the `.env` test deselected). Baseline on the stand-in: `4960 passed` with the `.env` test failing, so this plan adds 38 Python tests. The web unit suite: `28 passed` files, `227 passed` tests (`219` before Task 10); `typecheck` clean; `check:css` `OK`.
- Revision 1 (Fable review 1) was checked on two fresh exports of `3638ce10`, and nothing else was re-run.
  - The stand-in export ran Task 1 Steps 1–3, Tasks 2–9's blocks, Task 7 Step 1's script and all of Task 10. The Task 10 counts above are from that run.
  - The second export had every block of Phase D's draft plan applied (all 141 applied). It ran Task 1 in full and Tasks 2–6 in full: every block applied, B-pins unmoved, `256 passed` with the researcher at `b9caf536e3f0` (the narrowed Task 6 edits give the same file), `368 passed`. It also ran Task 7 Step 1.
  - The test counts differ only where Phase D's own tests sit in the same files: `625` not `610` (Task 2), `72` not `70` (Task 3), `1 failed, 21 passed` not `19` (Task 7). Its baseline was `1 failed, 4979 passed` and 251 Vitest tests.
  - The anchor check also passed with a simulated latency-first landing (Task 1 Step 3).
- Not run by planning, because they start the replay API and the app (the dispatch said no research session): Task 11's Playwright run and visual captures. Their expected outcomes are [INFERENCE]: nothing a replay run shows changes, because replay's interpreter never returns `new_angle` (`api/notes.py:267-275`) and a replay run never applies a note (api-gaps 3.9); the e2e notes test's status check gains `steering_outcome: null` (Task 10 Step 6). Task 11 Steps 4–5 prove or refute it.

## Global Constraints

- **Where.** Branch `feat/notes-progress-report-stop`, the Windows main checkout, after Phase D is merged. Tasks run strictly in order 1 → 11, one at a time. Commit after every task that changes files (Tasks 1 and 11 change none). Phase order is "1. D — Stop … 2. A — Notes … 3. C — Report … 4. B — Progress" (spec §9).
- **No live model, no secrets.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider or Tavily. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest; the API runs only in `--mode replay`, and only under Playwright in Task 11. Out of scope: "A live, paid run (governed by the existing spend rules)" (spec §1.3).
- **Kept (D5), exactly:** "at most 10 notes (LB-D11a), one pass and one redraft per note, verification never sees notes (LB-D10), and the recursion-limit arithmetic stays exact." `graph_recursion_limit` and `NOTE_REDRAFT_STEPS` are not edited; `graph_recursion_limit(1)` stays 160.
- **Byte-identical without notes.** Every change prints, adds or decides something only when a note exists, with one exception: `planner.planning.completed` always carries `note_topic_count` (`0` in a run without notes), because spec §5.2 step 5 and §6.1 put it in every run's metadata. It is event metadata, never part of a model request, so no request digest moves. `tests/test_graph/test_reader_notes_replay.py::PINNED_RUN_DIGESTS` does not move in Phase A (Phase C re-pins it, spec §11.2); `agents/prompts.py` and `agents/evidence_verifier.py` are not edited, so the `evidence_verifier` fingerprint stays at its B-pin (Task 1 Step 4; `4a3d56fab932` at `f4282818`).
- **Names shared with Phase C and B (spec §4 item 5), exactly:** `is_research_note`, `has_steering_kind`, `steering_view`, `steering_notes`, `note_sub_topic(note, *, priority, reason)` in `agents/reader_notes.py`; `researched_note_topic_ids` in `graph/state.py`; `note_outcome(note_id, state, *, terminal)` and `note_steering_outcome(note_id, state, *, terminal)` in `api/notes.py`; `ReaderNote.short`; `NoteState.kinds`, `NoteState.threadStarted`.
- **Theme (D19).** "DESIGN.md theme rules hold: tokens only; colour is status (green active/ok, amber warn, red danger, purple only on the one primary button); one surface per region; motion tokens; reduced motion turns movement into fades; unknown values read "not yet", never 0, —, or null." Phase A adds no CSS: `web/app/globals.css` lines 1–1131 stay the prototype's verbatim and `npm run -s check:css` prints `OK`.
- **Copy.** Every (I) string of spec §5.7 is used as written: `, as its own topic`; `, researching it as its own topic now`; `, researched as its own topic after this draft is reviewed`; `, researched as its own topic next`; `{outcome words}; the rest of your note: {steering outcome words}`. `—` is U+2014.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone, captures `fullPage: true` (spec §11.3).

### Conventions every task uses

- **Shell.** PowerShell 5.1, from the repository root. Each block is self-contained: a `web` command block starts with `Push-Location web` and ends with `Pop-Location`. Scripts piped to Python are single-quoted here-strings whose text is ASCII only, because Windows PowerShell pipes text to a native program in ASCII.
- **Line endings.** This checkout has `core.autocrlf=true`, so its files have CRLF line endings; every anchor below is written with LF. Match the text, not the line ending (the Edit tool and `Path.read_text` both do).
- **Python.** `.venv\Scripts\python.exe -m pytest <files> -q`. The venv's editable install points at this checkout's `src`, so no `PYTHONPATH` is needed. A full run is `.venv\Scripts\python.exe -m pytest -q` (about 110 s; `pyproject.toml` deselects the one `live` test).
- **Web.** `Push-Location web; npx vitest run <files>; Pop-Location` for named files, `npm test` for all, `npm run -s typecheck`, `npm run -s check:css`; `npm run test:e2e` builds then runs Playwright's `chromium` project, and `npm run capture:visual` its `visual` project. Playwright starts the replay API on 8010 and the app on 3010 and 3011; nothing may be listening on them first.
- **Edits.** Every edit is "replace this exact text with that text"; "replace the lines from the one starting X up to, not including, the one starting Y" replaces whole lines between two anchors; "Create" writes a new file; "Append to" adds the block at the end of the file. Task 1 proves that every anchor occurs exactly once when its turn comes. If one is not found, stop and report it; never improvise a nearby match. An anchor marked "(D-touched)" is a span whose two boundary lines Phase D does not write, so it holds whatever D wrote between them.
- **TDD.** Each task's tests are written first and run failing; Playwright specs are verification only. A test that already passes before its implementation step is named as such in its Expected line.
- **Fingerprint pins (the B-pin rule).** `agent_prompt_fingerprint` hashes each agent module's whole source and `agents/prompts.py` (`evaluation/config.py:280-288`), read with universal newlines, so a CRLF checkout and an LF export give the same value. Any branch that edits an agent module or `agents/prompts.py` before this plan runs moves the fingerprints this plan starts from. The latency workstream (D38) is one: its draft plan edits `agents/researcher.py`, `agents/planner.py`, `agents/evidence_verifier.py` and `agents/source_evaluator.py`. So:
  1. Task 1 Step 4 records the five fingerprints it prints as **B-pin(planner)**, **B-pin(researcher)**, **B-pin(source_evaluator)**, **B-pin(evidence_verifier)** and **B-pin(report_writer)**, the same way it records B-py and B-web. At `f4282818` with Phase D merged and nothing else landed, they are `d1ba46ce147f`, `a8c9528f0c20`, `24809aa975a3`, `4a3d56fab932` and `6e1aedc2888e`.
  2. A task that edits an agent module re-pins it in `tests/test_evaluation/test_config.py`. The re-pin block's old text is that agent's one pin line, holding the value the pin holds when the task starts: the agent's B-pin, or for `report_writer` in Task 9 the value Task 4 wrote. The block shows the unmoved case's value: `f4282818`'s, or for Task 9 the value Task 4 writes in the unmoved case. That value is valid only while nothing else moved the module. When the B-pin differs, the old line holds the B-pin (in Task 9, what Task 4 actually wrote) instead. Task 1 Step 3 checks the anchors that way.
  3. The new value is whatever the task's own fingerprint command prints, provided every block of the task applied byte-exactly. The new pin line and the comment's "Moved `old` -> `new`" carry that printed value and the old one. Each re-pin block shows the value printed on the planning export (unmoved case). The printed value must equal the block's value whenever the agent's B-pin is the `f4282818` value.
  4. `evidence_verifier` must print its B-pin at every step, because Phase A never edits it or `agents/prompts.py`.
  5. In the unmoved case, a printed value that differs from the block's value means a block of the task was not applied byte-exactly. Find it with `git diff` against the task's blocks, apply it again exactly, and print again. Never pin a printed value without finding the cause.
- **Commits.** `git add <paths>` then `git commit -m "<type>(<scope>): <what>"`, never `git add -A` (this checkout holds untracked work that is not this plan's). End each message with the attribution trailer your session requires, and push if your session's rules say to.

## Decisions this plan implements (spec §2, verbatim; closed)

| # | Decision | Where |
|---|---|---|
| D1 | **(A1)** Every new_angle note received before research ends becomes its own sub-topic "Your note: {restatement}", with coverage_id `note-{note_id}`, evidence targets from the note's `new_questions`, required, built deterministically and not left to the planner. The planner still sees all notes (emphasis, exclude and scope keep shaping the plan). | Tasks 2, 5, 6 |
| D2 | **(A2)** Planning-time new_angle notes are held and appended when the plan is published. `planner.planning.completed` metadata includes them. The web shows them per Phase B's Planning design as "joins the plan", then "from your note". They are researched in the main round, with no extra pass. | Task 5 (the slots' display is Phase B's §6.3; until B, Planning's finished brief lists the topic, Task 10) |
| D3 | **(A3)** Research-time new_angle notes start their own research thread immediately inside the running researcher node, and the researcher does not finish until those threads finish. This replaces the Phase 3 rule that new_angle notes wait for the review's note pass. The spec decides the interaction with the semaphore, the sub-topic cap, the request budget, the events (`researcher.sub_topic.started` for the note thread), and a note that arrives after the researcher's last thread finished but before the node returns. | Task 6 |
| D4 | **(A4)** Notes arriving after research finished (Evaluating, Verifying, Writing, Reviewing) keep exactly one note pass (the existing `note_pass` node). The writer then writes only the new note part and the bottom line and carries every other part over unchanged through the existing carry-over (`PartJob`, LB spec §6.9). Exception: exclude and scope notes still redraft the parts they affect (the note redraft route). *Ruled by D22: the note redraft keeps drafting every part.* | Tasks 8, 9 |
| D5 | **(A5)** A new_angle note's coverage is decided by its own sub-topic's targets (answered or not found), not by the reviewer. The reviewer still gives dispositions for emphasis, exclude, scope and about_reader notes. If the review is unavailable (`provider_failed`), any owed note pass still runs. No note ends `pending` in a terminal session: `not checked` when nothing could judge it. Kept: at most 10 notes (LB-D11a), one pass and one redraft per note, verification never sees notes (LB-D10), and the recursion-limit arithmetic stays exact. | Tasks 4, 7, 8 |
| D20 | **(O18, review C1) Mixed-kind notes: both halves apply.** The new_angle half gets its own topic. The steering half (exclude, scope, emphasis, about_reader) steers the running loops, the evaluator, the writer and the review packet, printed with new_angle removed from its kinds; it keeps its reviewer disposition; it can trigger a note redraft on `ignored_with_evidence` or when no review input carried it; Reviewing and the bottom-line note line show both results. With an AC and a test. | Tasks 2, 4, 5, 7, 8, 10 (Reviewing's row is Phase B's §6.7; the bottom-line note line Phase C's §7.2) |
| D22 | **(O2)** Exclude and scope notes keep redrafting every part (today's note redraft); new_angle notes still draft only their own part and the bottom line. | Tasks 8, 9 |
| D31 | **(O8, default accepted)** A research note is "covered" when at least one of its targets is answered by a verified finding. | Tasks 7, 8 |
| D19, D38 | Theme rules (above); the latency workstream, on which this spec depends for nothing (D38) | Global Constraints; Open issues O-2 |

## Review Focus

1. **The research window** (§5.3, Task 6). A note read while any thread runs must start its own thread at once, ungated; a note still being read when every thread has ended must be waited for, at most `NOTES_WAIT_S`; the loop must exit only right after a scan that found no note due; and a cancellation must cancel and await every thread. → `test_research_note_thread_starts_ungated`, `test_late_note_waits_then_threads`, `test_late_note_wait_times_out_and_closes_window`, `test_dispatcher_cancels_threads_on_cancel`, and the existing tests of `tests/test_agents/test_researcher.py`, which pin the gated behaviour unchanged.
2. **The pass and redraft rules** (§5.4, Task 8). A research note owes its pass while it has no researched topic, whatever the review said; only a steering kind buys a redraft; a failed or unstarted note topic is reused, never duplicated; an unfunded reused pass is refused honestly; ten notes still fit the unchanged recursion limit in both worst cases. → `test_notes_due_a_pass_research_notes_any_review_status`, `test_research_notes_never_redraft`, `test_failed_note_thread_owes_one_pass`, the two `test_recursion_limit_*` stub graphs.
3. **Carry-over after a note pass** (§5.4, Task 9): only the notes' parts, parts with no previous section, and the bottom line are drafted; carried parts keep their statements and verdicts; a note redraft still drafts every part. → `test_writer_carries_parts_after_note_pass`, `test_note_redraft_unchanged`.
4. **Which requests print which notes** (§5.1, Task 4): one replay run checks every request kind. → `test_steering_views_per_request`, `test_mixed_kind_note_steers_and_researches`.
5. **The D-touched spans** (Tasks 7, 10): each replaces only text between two lines D does not write; Task 1 Step 1 checks every D piece A builds on, and Step 2 that the spans hold nothing else.

## Spec ambiguities resolved here

1. **`note_sub_topic`'s `reason` is required** (keyword-only, no default), so every caller says why the topic exists; the existing callers in tests gain it (Task 2).
2. **A research-time note topic's priority** is `max(priority of state.sub_topics, default 0) + 1`, read once per researcher run — the formula `note_pass_node` already uses (`graph/nodes.py:1409`) — so it sorts after every planned topic, as §5.3's "priority=max+1" says. The planning-time append uses §5.2 step 3's formula over the state's and the plan's topics.
3. **An ungated thread checks `stop` as it starts**, as a gated one does. A note thread created in the same scheduler turn as another loop's failure therefore returns without opening; its topic stays in the run's note topics with no completed event, and its note owes its pass, which reuses the topic — §5.3's rule for an unstarted planning-time topic.
4. **`note_steering_outcome` gains an optional keyword `note: ReaderNote | None = None`.** With no state (a running session) the function cannot tell from an id whether a note is mixed, yet §5.6 says a mixed note reads `waiting` with no state; `note_records` passes the board's reading. A call written as §5.6 and §7.2 write it, `note_steering_outcome(note_id, state, terminal=True)`, is unchanged.
5. **`note_records` returns `NoteRecord` named tuples** `(received, restatement, outcome, steering_outcome)`, so "both outcomes per note" (§5.6) has named fields.
6. **`note_id` on `researcher.sub_topic.started`** is added for every `note-…` topic — a research-time thread, a planning-time note topic, a note pass's topic — and omitted for a topic of the plan (`topic-NN`), which is how this plan reads §5.3's "the key is omitted for a planned topic". The web marks `threadStarted` from it whichever kind of topic it is.
7. **The 30 s wait** (§5.3 step 3). A wait that times out is not repeated in that researcher run: the loop scans once more (a note read during the wait still gets its thread), and then exits as soon as no thread is running. The loop only ever exits right after a scan that found no note due. With `stop` set, it does not wait for a note being read, because no thread could start for it.
8. **A note's short label** (§7.2, shared with C). A live reading's `short` outside 1–3 words or 24 characters is dropped, never the reading — losing a research note's kinds and questions over a label would be worse than deriving one — and `ReaderNote` then derives it. The fallback and replay readings carry no `short`; `ReaderNote` derives theirs from the restatement, which is §7.2's "The fallback and replay derive it from the restatement's first three words". Words are whitespace-separated and keep their punctuation; a first word over 24 characters is cut to its first 24.
9. **`ReaderNote.short`** sits after `restatement`, at most 24 characters, one line; a model validator derives it when empty, so a checkpoint written before the field loads with a derived label.
10. **The two note routes' explanations** in `GRAPH_ROUTES` are rewritten for the new rules (they are copied into every `graph.route.decided` message), though §5.4 does not name them.
11. **`select_sub_topics`** follows the same cap rule as `run` (note topics uncapped), so the helper and the run cannot disagree.
12. **§5.3's "frozen" list is not the only read before a loop ends.** `extract_findings` reads `_planned_targets()` again for a loop's owed-passage extraction (`agents/researcher.py:3752`, used at `:3881` and `:3909`), so a loop that started before a note thread can still offer that thread's targets there. This plan implements §5.3's rule as written — `_planned_targets()` includes the run's note topics — and states the consequence: such a finding binds to the note's target truthfully, which only helps the note's coverage (D31). No other behaviour depends on the frozen read.
13. **AC7's writer half** is tested where the decision is made, in `build_task`: after a note redraft no part is carried and no defect is fed back. The full-draft path that follows is today's, pinned by `test_a_new_iteration_drafts_every_part_fresh_not_as_a_redraft`.
14. **api-gaps 3.9** stays "unchanged in substance" (§5.9). If Phase D left its words reading `pending` (`not checked`) for a finished replay note, Task 10 Step 7 changes `pending` to `not_checked` there; otherwise it changes nothing.
15. **Tests live** in the existing notes test files, plus one new file for the dispatcher, `tests/test_agents/test_research_note_threads.py`; AC6 lives beside the writer's other carry-over tests in `tests/test_agents/test_report_writer.py`.
16. **§5.7's "When … is active" table** is read as today's `WHERE` is: a research note's `{where}` comes from the step that was active when the run read the note (`NoteState.where`). The one exception is "now". It shows whenever Researching is the active row (`run.active`) and the note's own thread has started, whichever step read the note. So a note read during Writing says "now" while its note pass researches it, and "now" stops once Researching ends (review P3-5: the code fix was chosen over recording the gap).

## Open issues

Nothing needs a decision. Two coordination notes:

- **O-1 — Phase D's deliverables.** This plan builds on spec §4 item 2 and §8.4 as D's deliverables (the list in "Evidence"). Task 1 Step 1 checks each; if Phase D leaves any of them to Phase A (§4: "Whichever phase lands first implements the item"), Step 1 prints `MISSING` and this plan must gain the piece before Task 2. Its D-touched anchors in `api/notes.py`, `api/models.py`, `api/sessions.py`, `tests/test_api/test_notes.py` and `web/e2e/notes.spec.ts` are spans whose boundary lines D does not write; Task 1 Steps 2 and 3 check them. Phase D's plan (`docs/superpowers/plans/2026-09-30-phase-d-stop.md`, committed at `1cda4bdd`) keeps `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending` under that name, names the session's flag `terminal` (Task 1 Steps 1–2 accept any name), and changes `web/e2e/notes.spec.ts:109-110` to `outcome: "not_checked"`. Task 10 Step 6 is a span that writes the final check whatever D left there.
- **O-2 — Order-independent with the latency workstream (D38) under the B-pin rule.** The latency branch edits agent modules (its draft plan: `agents/researcher.py`, `agents/planner.py`, `agents/evidence_verifier.py`, `agents/source_evaluator.py`), which moves fingerprints. Every re-pin anchors on the pin's value when the task starts (the B-pin rule, Conventions). Task 6 never touches the lines latency O4 rewrites: the tool lock's comment and construction (`agents/researcher.py:4741-4747`) and the loop call `self._research_one(index, task, tool_lock)` (`:4769`). It edits only the closure's header and gate line, two comments, and the gather block (review P2-1). The latency plan's own anchor there ends on the stop comment's first line (`# Set by the first loop whose work ends in a non-recoverable provider`), which Task 6 leaves as it is. So the plan applies whichever lands first.

Planning compared every source and test anchor of this plan with the latency workstream's draft plan (as it stood on 2026-09-30). Two of this plan's anchors share lines with latency anchors: import blocks in `agents/researcher.py` (`:57-58`) and `agents/report_writer.py` (`:80`). Neither side changes those shared lines, so both apply in either order. The pin lines in `tests/test_evaluation/test_config.py` also overlap. There the B-pin rule covers this plan, but the latency draft's re-pin anchors hold the old pin values, so if this plan lands first, the latency branch must re-anchor its re-pins. Anything else either branch rewrites first is reported by Task 1 Step 3.

## Review 1 resolutions (Fable, on `3638ce10`)

Review file: `.superpowers/reviews/2026-09-30-plan-a-review-1.md`, which approved the plan with changes and found nothing Critical. Every finding is fixed; none is rejected.

| Finding | Resolution |
|---|---|
| P2-1 Task 6 Step 7 rewrote the lines latency O4 rewrites | Step 7's one span is now four edits plus a narrower span. The edits are: the stop comment after its first line (`agents/researcher.py:4749-4753`), the closure's header and gate line (`:4756-4760`), and the closure's comment (`:4771-4779`). The span runs from `settled_results = await asyncio.gather(` up to `# Every sub-topic that was never attempted` (`:4786-4828`, the dispatcher). Nothing touches `:4741-4748` (the tool lock's comment and construction, the gate, and the stop comment's first line, where the latency draft's own anchor ends) or the call `self._research_one(index, task, tool_lock)` (`:4769`). The edited file is byte-identical to the old span's result, so the researcher's new pin is still `b9caf536e3f0` (re-checked, below). |
| P2-2 literal fingerprints assume no other branch moved a module | The B-pin rule is in Conventions. Task 1 Step 4 records five B-pins. Each re-pin block's old text is the one pin line, which Task 1 Step 3 checks with the B-pin in place. Each re-pin step and Task 11 Step 1 say what is pinned when a B-pin differs, and `evidence_verifier` must equal its B-pin (Global Constraints). O-2 is rewritten: the plan is order-independent under the rule. |
| P3-1 Task 1 Steps 1–2 stricter than §4 item 2 on D's naming | Step 1 matches `note_records\([^)]*terminal=`. Step 2 prints one verdict per span. An extra module-level name in the `api/notes.py` span stops the run only when `src/` or `tests/` uses it outside the span. The `api/sessions.py` span accepts the flag under any name (the one it passes as `terminal=`). The test span accepts either name of the outcome test. |
| P3-2 a test removed without saying so | Task 7's Files and Interfaces name the removed test and the test that supersedes it. |
| P3-3 the "only when a note exists" constraint vs `note_topic_count` | Global Constraints carve out `note_topic_count` (spec §5.2 step 5, §6.1) and say why no request digest moves (`PINNED_RUN_DIGESTS` hashes the request texts). |
| P3-4 the failure test relied on stop being set in the same scheduler turn | `test_note_thread_provider_failure_sets_stop` now sleeps 0.02 s after note n1's completed event and before n2 is read; a comment says why. |
| P3-5 a note read during Writing kept "after this draft is reviewed" while its pass ran | The code fix. `whereFor(note, active)` says "now" when `active === "researcher"` and the note's thread has started. `ackFor` and `visibleAcks` take `active` (default `null`), and `rowBrief` passes `run.active` (Task 10 Step 4). A new Vitest test covers it (Step 1), and ambiguity 16 records how the table is read. The web counts go to B-web + 8. |
| P3-6 re-pins did not say what a mismatch means | Conventions (the B-pin rule, point 5) and each re-pin step (Tasks 4, 5, 6, 9) say it. When the B-pin is the `f4282818` value, another printed value means a block was not applied byte-exactly: find it, apply it again exactly, and print again. Never pin without finding the cause. |
| P3-7 D may add whole-record note assertions | Task 7 Step 1 runs a script first. It extends every one-line `not_checked` note record in `tests/`, `web/test/` and `web/e2e/` with `steering_outcome` (`None` or `null`), stages the files and prints each record it changed. `web/e2e/notes.spec.ts` is left to Task 10 Step 6. |

Also changed in this revision (O-1): Task 10 Step 6 became a D-touched span that writes the final e2e status check whatever Phase D left between its boundary lines. This removes the one expected anchor miss the review's simulation found at that block.

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/agents/reader_notes.py` | the note classifications and steering views, `note_sub_topic`, `NOTES_WAIT_S`, the researcher's board reads, the planning lead | 2, 5 |
| `src/deep_research/runtime/notes.py` | `NoteBoard.version`, `wait_for_change` | 2 |
| `src/deep_research/utils/types.py` | `MAX_NOTE_SHORT_CHARS`, `note_short_label`, `ReaderNote.short` | 3 |
| `src/deep_research/api/notes.py` | the reading's `short`; `note_outcome`, `note_steering_outcome`, `NoteRecord`, `note_records` | 3, 7 |
| `src/deep_research/api/models.py`, `api/sessions.py` | `ReaderNoteResponse.steering_outcome`; `session_note_fields` | 7 |
| `src/deep_research/agents/source_evaluator.py`, `agents/report_reviewer.py` | the steering views in scoring and in the review packet | 4 |
| `src/deep_research/agents/report_writer.py` | the steering views in writing (4); `note_pass_coverage_ids` and the carry-over after a note pass (9) | 4, 9 |
| `src/deep_research/agents/planner.py` | the research notes' topics appended at publication; `planning.completed` lists them | 5 |
| `src/deep_research/agents/researcher.py` | the dispatcher, note threads, uncapped note topics, `note_id` on the started event | 6 |
| `src/deep_research/graph/state.py`, `graph/nodes.py`, `graph/__init__.py`, `agents/__init__.py` | `researched_note_topic_ids`, the pass and redraft rules, the note pass's reused topic, the moved builder and wait, exports | 2, 8 |
| `web/lib/run-state.ts`, `web/lib/notes.ts`, `web/lib/api.ts`, `web/components/ReportBody.tsx` | `NoteState.kinds`/`threadStarted`, the research acknowledgement table, `noteCaption`, `steering_outcome` | 10 |
| `docs/design/DESIGN.md`, `docs/design/api-gaps.md` | §3 note captions, §3.5 research notes; 3.9's word | 10 |
| `tests/test_runtime/test_note_board.py`, `tests/test_graph/test_reader_notes_state.py` | the board's count; the helpers; the label | 2, 3 |
| `tests/test_agents/test_reader_notes_review.py`, `tests/test_graph/test_reader_notes_replay.py`, `tests/test_evaluation/test_config.py` | the packet; the requests per kind on the real graph; the re-pins | 4, 5, 6, 9 |
| `tests/test_agents/test_reader_notes_planning.py` | the planning lead and the append | 5 |
| `tests/test_agents/test_research_note_threads.py` (new) | the dispatcher (AC2–AC4, AC35) | 6, 8 |
| `tests/test_api/test_notes.py`, `tests/test_api/test_note_route.py` | the short label; the outcome tables; terminal sessions | 3, 7 |
| `tests/test_graph/test_note_routing.py` | the routing rules, the reused topic, AC10's stub graphs | 2, 8 |
| `tests/test_agents/test_report_writer.py`, `tests/test_agents/test_reader_notes_writing.py` | carry-over after a note pass; the note redraft | 2, 9 |
| `web/test/notes.test.ts`, `web/test/run-state.test.ts`, `web/test/briefs.test.ts`, `web/test/components/reader-notes.test.tsx`, `web/e2e/notes.spec.ts` | Vitest; the e2e status shape | 10 |

---

### Task 1: Check the starting point, every anchor, and the baselines

**Files:** none changed.

**Interfaces:**
- Consumes: Phase D merged into `feat/notes-progress-report-stop`.
- Produces: proof that each block of Tasks 2–10 applies in order — every anchor exactly once when its turn comes, every created file absent, every appended file present — and the baseline counts Task 11 adds to.

- [ ] **Step 1: Confirm Phase D delivered what Phase A builds on (spec §4 item 2, §8.4), and that no Phase A change is present yet**

Run:

```powershell
git status --short --untracked-files=no
@'
import re
from pathlib import Path
checks = [
    ("SessionStatus has stopped", "src/deep_research/api/models.py", r'SessionStatus = Literal\[[^\]]*"stopped"'),
    ("TERMINAL_STATUSES has stopped", "src/deep_research/api/sessions.py", r'TERMINAL_STATUSES = frozenset\(\s*\{[^}]*"stopped"'),
    ("NoteOutcome has not_checked", "src/deep_research/api/notes.py", r'NoteOutcome: TypeAlias = Literal\[[^\]]*"not_checked"'),
    ("note_outcome takes terminal", "src/deep_research/api/notes.py", r'def note_outcome\([^)]*\*\s*,\s*terminal: bool'),
    ("note_records takes terminal", "src/deep_research/api/notes.py", r'def note_records\([^)]*\*\s*,\s*terminal: bool'),
    ("session_note_fields passes a terminal flag", "src/deep_research/api/sessions.py", r'note_records\([^)]*terminal='),
    ("ReaderNoteResponse.outcome has not_checked", "src/deep_research/api/models.py", r'outcome: Literal\[[^\]]*"not_checked"'),
    ("web ReaderNoteOutcome has not_checked", "web/lib/api.ts", r'export type ReaderNoteOutcome = [^;]*"not_checked"'),
    ("web OUTCOME_TEXT.not_checked", "web/lib/notes.ts", r'not_checked: "not checked"'),
    ("web OUTCOME_TEXT.pending", "web/lib/notes.ts", r'pending: "not checked yet"'),
]
for name, path, pattern in checks:
    text = Path(path).read_text(encoding="utf-8")
    print(("OK       " if re.search(pattern, text, re.S) else "MISSING  ") + name)
absent = [
    ("no steering_view yet", "src/deep_research/agents/reader_notes.py", "def steering_view("),
    ("no note threads test yet", "tests/test_agents/test_research_note_threads.py", None),
]
for name, path, needle in absent:
    p = Path(path)
    present = p.is_file() if needle is None else needle in p.read_text(encoding="utf-8")
    print(("PRESENT  " if present else "OK       ") + name)
'@ | .venv\Scripts\python.exe -
```

Expected: `git status` prints nothing, then twelve lines that all start with `OK`. A `MISSING` line means Phase D left that piece of §4 item 2 to Phase A (Open issue O-1): stop and report it. A `PRESENT` line means Phase A work is already on the branch: stop and report it.

- [ ] **Step 2: Confirm the four D-touched spans hold only what this plan replaces**

Task 7 replaces four spans of text Phase D wrote. Nothing Phase D added inside a span may be lost when Task 7 rewrites it. The checks below ignore any name Phase D chose that only the span itself uses:

- In `api/notes.py`, a module-level name other than `note_outcome` and `note_records` is fine when nothing outside the span uses it. A private helper of D's goes with the function it served.
- In `api/sessions.py`, the span assigns `state`, `check` and at most one other name, the flag it passes as `terminal=` (D's name for it does not matter). Its returned dict has only `notes` before `notes_remaining`.
- `ReaderNoteResponse` has only `outcome` in the span.
- The test span holds `_finished`, the records test, and the outcome test under either of its names.

```powershell
@'
import re
from pathlib import Path
def lines_of(path):
    return Path(path).read_text(encoding="utf-8").split("\n")
def span(path, start, end):
    lines = lines_of(path)
    i = next(k for k, line in enumerate(lines) if line.startswith(start))
    j = next(k for k, line in enumerate(lines) if k > i and line.startswith(end))
    return i, j, lines[i:j]
def used_outside(name, path, i, j):
    word = re.compile(r"\b" + re.escape(name) + r"\b")
    for file in sorted([*Path("src").rglob("*.py"), *Path("tests").rglob("*.py")]):
        for k, line in enumerate(lines_of(file)):
            if word.search(line) and not (file.as_posix() == path and i <= k < j):
                return file.as_posix() + ":" + str(k + 1)
    return None
def verdict(label, problems):
    print(label + ": " + ("OK" if not problems else "STOP " + "; ".join(problems)))
TOP = re.compile(r"(?:async def |def |class )(\w+)|(\w+)\s*(?::[^=]*)?=(?!=)")
path = "src/deep_research/api/notes.py"
i, j, body = span(path, "def note_outcome(", "__all__ = [")
names = [m.group(1) or m.group(2) for m in map(TOP.match, body) if m]
problems = [n + " missing" for n in ("note_outcome", "note_records") if n not in names]
for name in names:
    where = None if name in ("note_outcome", "note_records") else used_outside(name, path, i, j)
    if where:
        problems.append(name + " is used at " + where)
verdict("api/notes.py span", problems)
_, _, body = span("src/deep_research/api/models.py", "    outcome: Literal[", "class ClarificationQuestionResponse(ApiModel):")
fields = [line.split(":")[0].strip() for line in body if re.match(r"    [a-z_]+:", line)]
verdict("api/models.py span", [] if fields == ["outcome"] else ["fields " + repr(fields)])
_, _, body = span("src/deep_research/api/sessions.py", "    state = session.outcome.state if session.outcome is not None else None", '        "notes_remaining": session.note_board.remaining,')
flag = re.search(r"note_records\([^)]*terminal=(\w+)", "\n".join(body))
assigned = [line.split("=")[0].strip() for line in body if re.match(r"    [a-z_]+ = ", line)]
others = [n for n in assigned if n not in ("state", "check")]
keys = [m.group(1) for m in (re.match(r'        "(\w+)": ', line) for line in body) if m]
problems = [] if flag else ["no terminal= flag passed to note_records"]
problems += [n + " assigned" for n in others if not flag or n != flag.group(1)] + [n + " missing" for n in ("state", "check") if n not in assigned]
problems += [] if keys == ["notes"] else ["keys " + repr(keys)]
verdict("api/sessions.py span", problems)
_, _, body = span("tests/test_api/test_notes.py", "def _finished(", "def test_the_note_shapes_trim_bound_and_default_as_the_spec_says")
defs = [line.split("(")[0][4:] for line in body if line.startswith("def ")]
known = {"_finished", "test_the_records_list_every_accepted_note_in_order_read_or_not", "test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending", "test_each_note_ends_covered_not_found_not_addressed_replaced_or_waiting"}
problems = [d + " would be deleted" for d in defs if d not in known]
problems += [] if len(defs) == 3 and defs[0] == "_finished" else ["defs " + repr(defs)]
verdict("test_notes.py span", problems)
'@ | .venv\Scripts\python.exe -
```

Expected, exactly:

```text
api/notes.py span: OK
api/models.py span: OK
api/sessions.py span: OK
test_notes.py span: OK
```

A `STOP` line names something Phase D added inside a span that Task 7 would delete while something else needs it: stop and report it.

- [ ] **Step 3: Check every block of this plan against the tree, in order**

The check simulates the whole plan in memory — each block is applied to the text the earlier blocks left — and changes nothing on disk. A re-pin block's old pin line is checked with the agent's B-pin in place of the `f4282818` value it shows (the B-pin rule, Conventions).

```powershell
@'
import re
from pathlib import Path
from deep_research.evaluation.config import agent_prompt_fingerprint
PLAN = Path("docs/superpowers/plans/2026-09-30-phase-a-notes.md").read_text(encoding="utf-8")
AT_F4282818 = {"planner": "d1ba46ce147f", "researcher": "a8c9528f0c20", "source_evaluator": "24809aa975a3", "report_writer": "6e1aedc2888e"}
B_PIN_LINES = {'    "%s": "%s",' % (agent, value): '    "%s": "%s",' % (agent, agent_prompt_fingerprint(agent)) for agent, value in AT_F4282818.items()}
DASH = chr(0x2014)
EDIT = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? " + DASH + r" replace\n\n(?P<f1>`{3,4})[a-z]*\n(?P<old>.*?)\n(?P=f1)\n\nwith\n\n(?P<f2>`{3,4})[a-z]*\n(?P<new>.*?)\n(?P=f2)\n", re.S | re.M)
SPAN = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? " + DASH + r" replace the lines from the one starting `(?P<start>[^`\n]+)` up to, not including, the one starting `(?P<end>[^`\n]+)`, with\n\n(?P<f>`{3,4})[a-z]*\n(?P<new>.*?)\n(?P=f)\n", re.S | re.M)
CREATE = re.compile(r"^Create `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,4})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
APPEND = re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,4})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
kinds = (("edit", EDIT), ("span", SPAN), ("create", CREATE), ("append", APPEND))
blocks = sorted(((m.start(), kind, m) for kind, rx in kinds for m in rx.finditer(PLAN)), key=lambda b: b[0])
files, problems, counts = {}, [], {"edit": 0, "span": 0, "create": 0, "append": 0}
def text(path):
    if path not in files:
        p = Path(path)
        files[path] = p.read_text(encoding="utf-8") if p.is_file() else None
    return files[path]
for _, kind, m in blocks:
    path, current = m["path"], text(m["path"])
    counts[kind] += 1
    if kind == "edit":
        old = B_PIN_LINES.get(m["old"], m["old"]) if path == "tests/test_evaluation/test_config.py" else m["old"]
        found = -1 if current is None else current.count(old)
        if found != 1:
            problems.append(path + ": anchor found " + str(found) + " times: " + repr(old[:70]))
            continue
        files[path] = current.replace(old, m["new"])
    elif kind == "span":
        lines = [] if current is None else current.split("\n")
        starts = [i for i, line in enumerate(lines) if line.startswith(m["start"])]
        ends = [i for i, line in enumerate(lines) if starts and i > starts[0] and line.startswith(m["end"])]
        if len(starts) != 1 or not ends:
            problems.append(path + ": span start found " + str(len(starts)) + " times: " + repr(m["start"][:60]))
            continue
        files[path] = "\n".join(lines[: starts[0]] + m["new"].split("\n") + lines[ends[0]:])
    elif kind == "create":
        if current is not None:
            problems.append(path + ": already exists")
            continue
        files[path] = m["body"] + "\n"
    else:
        if current is None:
            problems.append(path + ": append target missing")
            continue
        files[path] = current + m["body"] + "\n"
for problem in problems:
    print("PROBLEM", problem)
print("anchors: %d exactly once; spans: %d found; creates: %d absent; appends: %d onto files present; problems: %d" % (counts["edit"], counts["span"], counts["create"], counts["append"], len(problems)))
'@ | .venv\Scripts\python.exe -
```

Expected: `anchors: 144 exactly once; spans: 6 found; creates: 1 absent; appends: 10 onto files present; problems: 0`, and no `PROBLEM` line. Planning ran this on an export with every block of Phase D's draft plan applied, and printed that line. It printed the same line again after a simulated latency-first landing, in which `agents/researcher.py:4741-4746` was rewritten as the latency draft's Task 12 rewrites it and the researcher's pin moved to match.

- [ ] **Step 4: Record the baselines**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print([f(n) for n in ('planner', 'researcher', 'source_evaluator', 'evidence_verifier', 'report_writer')])"
Push-Location web; npm test; npm run -s typecheck; npm run -s check:css; Pop-Location
```

Expected: the pytest line ends `passed` with no `failed` or `error` (record its count as **B-py**; the planning export, which has no `.env`, printed `1 failed, 4960 passed` — the one failure is `tests/test_config.py::test_the_evidence_verifier_pipeline_config`, which reads this checkout's `.env` and passes here); then five fingerprints: record them, in order, as **B-pin(planner)**, **B-pin(researcher)**, **B-pin(source_evaluator)**, **B-pin(evidence_verifier)** and **B-pin(report_writer)** (the B-pin rule, Conventions). With Phase D merged and nothing else landed since `f4282818` they print `['d1ba46ce147f', 'a8c9528f0c20', '24809aa975a3', '4a3d56fab932', '6e1aedc2888e']`, the values the re-pin blocks of Tasks 4, 5, 6 and 9 show. A different value is not a stop: it means another branch moved that module first, and the B-pin rule says what each re-pin then anchors on and writes. Then Vitest ends `Tests  N passed (N)` with no failure (record N as **B-web**; `219` on the planning export), `typecheck` prints nothing, and `check:css` prints `OK`.


### Task 2: The note helpers, the moved `note_sub_topic`, and the board's change count

**Files:**
- Modify: `src/deep_research/runtime/notes.py` (`NoteBoard.__init__`, `add`, `drop`; new `version`, `wait_for_change`, `_bump`)
- Modify: `src/deep_research/agents/reader_notes.py` (imports, `STEERING_KINDS`, `NOTES_WAIT_S`, `NoteTopicReason`, the classification helpers, `research_reader_notes`, `note_sub_topic`, the board helpers, `__all__`)
- Modify: `src/deep_research/graph/nodes.py:36`, `:106-124`, `:831`, `:862`, `:1078-1086`, `:1343-1378`, `:1410`
- Modify: `src/deep_research/agents/__init__.py:241-254`, `:748`
- Test: `tests/test_runtime/test_note_board.py`, `tests/test_graph/test_reader_notes_state.py`, `tests/test_graph/test_note_routing.py`, `tests/test_agents/test_reader_notes_writing.py`

**Interfaces:**
- Consumes: Phase D merged (Task 1 Step 1). Nothing from earlier tasks of this plan.
- Produces (every later task and the Phase C plan use these names exactly, spec §4 item 5):
  - `agents/reader_notes.py`: `STEERING_KINDS: frozenset[str]`; `NOTES_WAIT_S: float = 30.0`; `NoteTopicReason = Literal["reader_note", "no_evidence"]`; `is_research_note(note: ReaderNote) -> bool`; `has_steering_kind(note: ReaderNote) -> bool`; `steering_view(note: ReaderNote) -> ReaderNote | None`; `steering_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]`; `note_sub_topic(note: ReaderNote, *, priority: int, reason: NoteTopicReason) -> SubTopic`; `board_version() -> int | None`; `async wait_for_board_change(seen: int) -> int`; `notes_being_read() -> bool`; `research_notes_without_a_topic(state_notes: Sequence[ReaderNote], known_coverage_ids: Collection[str]) -> list[ReaderNote]`; `research_reader_notes(notes)` now returns `steering_notes(notes)`.
  - `runtime/notes.py`: `NoteBoard.version: int` (property); `async NoteBoard.wait_for_change(seen: int) -> int`.
  - `graph/nodes.py` no longer defines `note_sub_topic` or `_REVIEW_NOTES_WAIT_S`; it imports `note_sub_topic` and `NOTES_WAIT_S` from `agents/reader_notes.py`, so `from deep_research.graph.nodes import note_sub_topic` keeps working.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime/test_note_board.py`:

```python


@pytest.mark.asyncio
async def test_the_board_counts_every_add_and_drop_and_wakes_a_waiter() -> None:
    """notes-progress-report spec §5.3: the researcher reads ``version`` before it scans the
    board and then waits for the next add or drop, so no change between the two is lost.
    Receiving a note is not a change: nothing can act on a note before it is read."""
    board = NoteBoard()
    _receive(board)
    _receive(board)
    assert board.version == 0
    assert await asyncio.wait_for(board.wait_for_change(-1), timeout=1) == 0

    waiter = asyncio.create_task(board.wait_for_change(0))
    _receive(board)
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.add(fake_reader_note("n1"))
    assert await asyncio.wait_for(waiter, timeout=1) == 1
    board.drop("n2")
    board.drop("n3")
    assert board.version == 3
    assert await asyncio.wait_for(board.wait_for_change(1), timeout=1) == 3
```

`tests/test_graph/test_reader_notes_state.py` — replace

```python
from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    live_reader_notes,
    render_reader_notes,
    research_reader_notes,
)
```

with

```python
from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    board_version,
    has_steering_kind,
    is_research_note,
    live_reader_notes,
    note_sub_topic,
    notes_being_read,
    render_reader_notes,
    research_notes_without_a_topic,
    research_reader_notes,
    steering_notes,
    steering_view,
    wait_for_board_change,
)
```

Append to `tests/test_graph/test_reader_notes_state.py`:

```python


# --- notes-progress-report spec §5.1, §5.3: research, steering and mixed notes ---------


def test_a_note_is_research_steering_or_both() -> None:
    """§5.1, D20: a research note's kinds include new_angle, a steering note's do not, and a
    mixed note is both. Its steering view is the note with new_angle left out of its kinds."""
    angle = fake_reader_note("n1", kinds=["new_angle"])
    mixed = fake_reader_note("n2", kinds=["new_angle", "exclude"])
    steer = fake_reader_note("n3", kinds=["scope", "emphasis"])

    assert [is_research_note(note) for note in (angle, mixed, steer)] == [True, True, False]
    assert [has_steering_kind(note) for note in (angle, mixed, steer)] == [False, True, True]
    assert steering_view(angle) is None
    assert steering_view(steer) is steer
    view = steering_view(mixed)
    assert view is not None and view.kinds == ["exclude"]
    assert view.model_dump(exclude={"kinds"}) == mixed.model_dump(exclude={"kinds"})
    assert mixed.kinds == ["new_angle", "exclude"]
    assert [(note.note_id, note.kinds) for note in steering_notes([angle, mixed, steer])] == [
        ("n2", ["exclude"]),
        ("n3", ["scope", "emphasis"]),
    ]
    assert research_reader_notes([angle, mixed, steer]) == steering_notes([angle, mixed, steer])


def test_a_notes_sub_topic_says_why_it_exists() -> None:
    """§5.1: one builder for both reasons; only the rationale differs."""
    note = fake_reader_note(
        "n4", kinds=["new_angle"], restatement="how cells are recycled",
        new_questions=["How are battery cells recycled?"],
    )

    asked = note_sub_topic(note, priority=3, reason="reader_note")
    owed = note_sub_topic(note, priority=3, reason="no_evidence")

    assert asked.rationale == "The reader asked for this in a note."
    assert owed.rationale == (
        "The reader asked for this in a note, and the review found no evidence for it yet."
    )
    assert asked.model_dump(exclude={"rationale"}) == owed.model_dump(exclude={"rationale"})
    assert (asked.coverage_id, asked.title, asked.priority) == ("note-n4", "Your note: how cells are recycled", 3)


@pytest.mark.asyncio
async def test_the_researcher_reads_the_boards_count_readings_and_untopiced_research_notes() -> None:
    """§5.3: the dispatcher's reads of the board, reached through ``agents.reader_notes`` at
    call time; with no board bound (the CLI) there is no count and nothing being read."""
    assert (board_version(), notes_being_read()) == (None, False)
    held = [fake_reader_note("n1", kinds=["new_angle"])]
    assert [note.note_id for note in research_notes_without_a_topic(held, set())] == ["n1"]
    assert research_notes_without_a_topic(held, {"note-n1"}) == []

    board = NoteBoard()
    for _ in range(4):
        board.receive("note", received_at=AT, received_during="researcher")
    board.add(fake_reader_note("n1", kinds=["new_angle"]))
    board.add(fake_reader_note("n2", kinds=["new_angle", "exclude"]))
    board.add(fake_reader_note("n3"))

    with bind_note_board(board):
        assert (board_version(), notes_being_read()) == (3, True)
        assert [note.note_id for note in research_notes_without_a_topic([], {"note-n1"})] == ["n2"]
        waiter = asyncio.create_task(wait_for_board_change(3))
        board.add(fake_reader_note("n4", kinds=["new_angle"], replaces="n2"))
        assert await asyncio.wait_for(waiter, timeout=1) == 4
        assert notes_being_read() is False
        assert [note.note_id for note in research_notes_without_a_topic([], {"note-n1"})] == ["n4"]
```

`tests/test_graph/test_reader_notes_state.py` — replace

```python
from __future__ import annotations

import pytest
```

with

```python
from __future__ import annotations

import asyncio

import pytest
```

`tests/test_graph/test_note_routing.py` — replace

```python
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.researcher import select_sub_topics
from deep_research.graph.nodes import (
    _REVIEW_NOTES_WAIT_S,
    _arrived_via_redraft_hop,
    note_pass_node,
    note_sub_topic,
    report_reviewer_node,
    writer_redraft_node,
)
```

with

```python
from deep_research.agents.reader_notes import NOTES_WAIT_S, note_sub_topic
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.researcher import select_sub_topics
from deep_research.graph.nodes import (
    _arrived_via_redraft_hop,
    note_pass_node,
    report_reviewer_node,
    writer_redraft_node,
)
```

`tests/test_graph/test_note_routing.py` — replace

```python
    topic = note_sub_topic(fake_reader_note(), priority=2)
```

with

```python
    topic = note_sub_topic(fake_reader_note(), priority=2, reason="no_evidence")
```

`tests/test_graph/test_note_routing.py` — replace

```python
    topic = note_sub_topic(angled, priority=4)
```

with

```python
    topic = note_sub_topic(angled, priority=4, reason="reader_note")
```

`tests/test_graph/test_note_routing.py` — replace

```python
    plain = note_sub_topic(fake_reader_note("n1"), priority=2)
```

with

```python
    plain = note_sub_topic(fake_reader_note("n1"), priority=2, reason="no_evidence")
```

`tests/test_graph/test_note_routing.py` — replace

```python
    """A reading that never ends holds the route for ``_REVIEW_NOTES_WAIT_S`` at most, however
    long the interpreter's own timeout is: the note stays on the board, out of this
    decision, and the route is read without it."""
    assert _REVIEW_NOTES_WAIT_S == 30.0
    assert HitlConfig().note_interpret_timeout_s < _REVIEW_NOTES_WAIT_S
    monkeypatch.setattr("deep_research.graph.nodes._REVIEW_NOTES_WAIT_S", 0.05)
```

with

```python
    """A reading that never ends holds the route for ``NOTES_WAIT_S`` at most, however
    long the interpreter's own timeout is: the note stays on the board, out of this
    decision, and the route is read without it."""
    assert NOTES_WAIT_S == 30.0
    assert HitlConfig().note_interpret_timeout_s < NOTES_WAIT_S
    monkeypatch.setattr("deep_research.graph.nodes.NOTES_WAIT_S", 0.05)
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import _is_redraft_hop
from deep_research.graph.nodes import note_sub_topic
```

with

```python
from deep_research.agents.reader_notes import note_sub_topic
from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import _is_redraft_hop
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
    )

    report = render_written_report(fake_writer_composition(state))
```

with

```python
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2, reason="reader_note")],
    )

    report = render_written_report(fake_writer_composition(state))
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
    one = verified_pass(target_ids=["note-n2-target-01"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
```

with

```python
    one = verified_pass(target_ids=["note-n2-target-01"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2, reason="reader_note")],
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
    one = verified_pass(target_ids=["note-n2-target-02"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
```

with

```python
    one = verified_pass(target_ids=["note-n2-target-02"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2, reason="reader_note")],
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_note_routing.py tests/test_agents/test_reader_notes_writing.py -q
```

Expected: `Interrupted: 3 errors during collection`, then `3 errors`. The three are `ImportError`s in `test_reader_notes_state.py`, `test_note_routing.py` and `test_reader_notes_writing.py`: `cannot import name 'board_version' from 'deep_research.agents.reader_notes'`, `cannot import name 'NOTES_WAIT_S'` and `cannot import name 'note_sub_topic'`, respectively. The first `ERROR` line names `tests/test_graph/test_reader_notes_state.py`. `test_note_board.py` collects, but the interrupted session runs no test, so its new test (which fails with `AttributeError: 'NoteBoard' object has no attribute 'version'` until Step 3) does not run here.

- [ ] **Step 3: Give the board its change count**

`src/deep_research/runtime/notes.py` — replace

```python
        self._settled = asyncio.Event()
        self._settled.set()

    @property
    def accepted(self) -> int:
```

with

```python
        self._settled = asyncio.Event()
        self._settled.set()
        self._version = 0
        self._changed = asyncio.Event()

    @property
    def version(self) -> int:
        """How many times a note was added or dropped (notes-progress-report spec §5.3).

        A reader that reads it before it looks at the board, and then waits with
        ``wait_for_change``, misses no add or drop in between.
        """
        return self._version

    @property
    def accepted(self) -> int:
```

`src/deep_research/runtime/notes.py` — replace

```python
        self._notes[note.note_id] = note.model_copy(deep=True)
        self._settle()

    def drop(self, note_id: str) -> None:
        """Give up on one received note's interpretation (the session is closing)."""
        if note_id not in self._notes:
            self._dropped.add(note_id)
        self._settle()
```

with

```python
        self._notes[note.note_id] = note.model_copy(deep=True)
        self._bump()
        self._settle()

    def drop(self, note_id: str) -> None:
        """Give up on one received note's interpretation (the session is closing)."""
        if note_id not in self._notes:
            self._dropped.add(note_id)
        self._bump()
        self._settle()

    async def wait_for_change(self, seen: int) -> int:
        """Return the board's ``version`` once it differs from ``seen``."""
        while self._version == seen:
            await self._changed.wait()
        return self._version
```

`src/deep_research/runtime/notes.py` — replace

```python
    def _settle(self) -> None:
        if not self.pending:
            self._settled.set()
```

with

```python
    def _settle(self) -> None:
        if not self.pending:
            self._settled.set()

    def _bump(self) -> None:
        """Count one add or drop and wake every waiter; later waiters wait on a fresh event."""
        self._version += 1
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()
```

- [ ] **Step 4: Add the helpers to `agents/reader_notes.py`**

`src/deep_research/agents/reader_notes.py` — replace

```python
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from deep_research.utils.types import (
    ReaderNote,
    active_reader_notes,
    with_board_notes,
)
```

with

```python
from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from typing import Literal, Protocol, TypeAlias

from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    EvidenceTarget,
    ReaderNote,
    SubTopic,
    active_reader_notes,
    with_board_notes,
)

STEERING_KINDS: frozenset[str] = frozenset({"emphasis", "exclude", "scope", "about_reader"})
"""The kinds that steer the run's own steps (notes-progress-report spec §5.1); ``new_angle``
asks for research of its own instead."""

NOTES_WAIT_S = 30.0
"""The longest a step waits for a note still being read before it moves on.

Shared by the review node, before it reads its route (live-briefs spec §4.8), and the
researcher, before its research window closes (notes-progress-report spec §5.3): twice
``hitl.note_interpret_timeout_s``'s default of 15 s, so with the default every reading in
flight when the wait begins has ended first, and a raised timeout (up to ten minutes) holds
either step for this long at most. A note still being read then is left out, and a later
step takes it in once it is read.
"""

NoteTopicReason: TypeAlias = Literal["reader_note", "no_evidence"]
"""Why a note has a sub-topic: the reader asked for research (a research note), or the
review found no evidence for a steering note (its one note pass)."""

_NOTE_TOPIC_RATIONALES: dict[str, str] = {
    "reader_note": "The reader asked for this in a note.",
    "no_evidence": (
        "The reader asked for this in a note, and the review found no evidence for it yet."
    ),
}
```

`src/deep_research/agents/reader_notes.py` — replace

```python
    note_id: str
    restatement: str
    kinds: Sequence[str]


def board_notes() -> list[ReaderNote]:
```

with

```python
    note_id: str
    restatement: str
    kinds: Sequence[str]


def is_research_note(note: ReaderNote) -> bool:
    """A research note asks the run to research something: its kinds include ``new_angle``
    (notes-progress-report spec §5.1)."""
    return "new_angle" in note.kinds


def has_steering_kind(note: ReaderNote) -> bool:
    """Whether the note steers the run's steps: emphasis, exclude, scope or about_reader.

    Every steering note has one; a research note that has one too is a mixed note (D20).
    """
    return any(kind in STEERING_KINDS for kind in note.kinds)


def steering_view(note: ReaderNote) -> ReaderNote | None:
    """The note as every steering request prints it (spec §5.1, D20).

    A note without ``new_angle`` is returned unchanged; a mixed note is a copy with
    ``new_angle`` left out of its kinds; a note whose only kind is ``new_angle`` has no
    steering half, so ``None``.
    """
    if not is_research_note(note):
        return note
    if not has_steering_kind(note):
        return None
    return note.model_copy(
        update={"kinds": [kind for kind in note.kinds if kind != "new_angle"]}
    )


def steering_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """``steering_view`` of each note, the ``None`` ones dropped, in the given order."""
    return [view for note in notes if (view := steering_view(note)) is not None]


def board_notes() -> list[ReaderNote]:
```

`src/deep_research/agents/reader_notes.py` — replace

```python
def research_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a running research loop applies: ``new_angle`` notes wait for the review's note pass."""
    return [note for note in notes if "new_angle" not in note.kinds]
```

with

```python
def research_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a research loop's turns and its extraction apply: their steering views.

    notes-progress-report spec §5.1: a note whose only kind is ``new_angle`` is researched
    as its own topic instead, and a mixed note steers with ``new_angle`` left out.
    """
    return steering_notes(notes)
```

`src/deep_research/agents/reader_notes.py` — replace

```python
        prefix = f"{note.note_id}: " if with_ids else ""
        lines.append(f"- {prefix}{note.restatement} ({', '.join(note.kinds)})")
    return "\n".join(lines)
```

with

```python
        prefix = f"{note.note_id}: " if with_ids else ""
        lines.append(f"- {prefix}{note.restatement} ({', '.join(note.kinds)})")
    return "\n".join(lines)


def note_sub_topic(
    note: ReaderNote, *, priority: int, reason: NoteTopicReason
) -> SubTopic:
    """The sub-topic that researches one reader note (notes-progress-report spec §5.1).

    Titled ``Your note: {restatement}``, with coverage id ``note-{note_id}`` and one
    required target per question the note raised — or, for a note that raised none, the
    note's own restatement — each carrying the note's scope. ``reason`` says why the
    topic exists, in its rationale: the reader asked for research (``reader_note``), or
    the review found no evidence for a steering note (``no_evidence``).
    """
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note.note_id}"
    questions = list(note.new_questions) or [note.restatement]
    geography = note.scope.geography if note.scope else None
    period = note.scope.period if note.scope else None
    return SubTopic(
        coverage_id=coverage_id,
        title=f"{NOTE_TOPIC_TITLE_PREFIX}{note.restatement}",
        rationale=_NOTE_TOPIC_RATIONALES[reason],
        search_queries=questions,
        success_criteria=[
            f"A checked source answers: {question}" for question in questions
        ],
        priority=priority,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"{coverage_id}-target-{number:02d}",
                coverage_id=coverage_id,
                question=question,
                required=True,
                measure=question,
                geography=geography,
                period=period,
            )
            for number, question in enumerate(questions, start=1)
        ],
    )


def board_version() -> int | None:
    """The bound board's change count, or ``None`` with no board bound (spec §5.3)."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return None if board is None else board.version


async def wait_for_board_change(seen: int) -> int:
    """Return the bound board's change count once it differs from ``seen`` (spec §5.3).

    Called only while a board is bound: ``board_version`` returned ``seen``.
    """
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    if board is None:
        raise RuntimeError("no note board is bound for this run")
    return await board.wait_for_change(seen)


def notes_being_read() -> bool:
    """Whether the bound board holds a received note whose reading has not ended."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return board is not None and bool(board.pending)


def research_notes_without_a_topic(
    state_notes: Sequence[ReaderNote], known_coverage_ids: Collection[str]
) -> list[ReaderNote]:
    """The active research notes, the state's then the board's, whose ``note-{id}`` is
    not in ``known_coverage_ids``, in receipt order (notes-progress-report spec §5.3)."""
    return [
        note
        for note in live_reader_notes(state_notes)
        if is_research_note(note)
        and f"{NOTE_COVERAGE_PREFIX}{note.note_id}" not in known_coverage_ids
    ]
```

`src/deep_research/agents/reader_notes.py` — replace

```python
__all__ = [
    "EXTRACTION_NOTES",
    "NoteLine",
    "PLANNING_NOTES",
    "RESEARCH_NOTES",
    "REVIEW_NOTES",
    "SOURCE_NOTES",
    "WRITING_NOTES",
    "board_notes",
    "live_reader_notes",
    "notes_settled",
    "render_reader_notes",
    "research_reader_notes",
]
```

with

```python
__all__ = [
    "EXTRACTION_NOTES",
    "NOTES_WAIT_S",
    "NoteLine",
    "NoteTopicReason",
    "PLANNING_NOTES",
    "RESEARCH_NOTES",
    "REVIEW_NOTES",
    "SOURCE_NOTES",
    "STEERING_KINDS",
    "WRITING_NOTES",
    "board_notes",
    "board_version",
    "has_steering_kind",
    "is_research_note",
    "live_reader_notes",
    "note_sub_topic",
    "notes_being_read",
    "notes_settled",
    "render_reader_notes",
    "research_notes_without_a_topic",
    "research_reader_notes",
    "steering_notes",
    "steering_view",
    "wait_for_board_change",
]
```

- [ ] **Step 5: Point `graph/nodes.py` at the moved builder and the shared wait**

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.agents.reader_notes import board_notes, notes_settled
```

with

```python
from deep_research.agents.reader_notes import (
    NOTES_WAIT_S,
    board_notes,
    note_sub_topic,
    notes_settled,
)
```

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    EvidenceTarget,
    Finding,
    ReaderNote,
    ReportComposition,
```

with

```python
from deep_research.utils.types import (
    Finding,
    ReportComposition,
```

`src/deep_research/graph/nodes.py` — replace

```python
    ResearchStateUpdate,
    SubTopic,
    active_reader_notes,
    advance_research_iteration,
```

with

```python
    ResearchStateUpdate,
    active_reader_notes,
    advance_research_iteration,
```

`src/deep_research/graph/nodes.py` — replace

```python
    still being interpreted — for ``_REVIEW_NOTES_WAIT_S`` at most — and takes
```

with

```python
    still being interpreted — for ``NOTES_WAIT_S`` at most — and takes
```

`src/deep_research/graph/nodes.py` — replace

```python
        await notes_settled(timeout=_REVIEW_NOTES_WAIT_S)
```

with

```python
        await notes_settled(timeout=NOTES_WAIT_S)
```

`src/deep_research/graph/nodes.py` — replace

```python
# The longest the review node waits for a note still being read before it reads
# its route (live-briefs spec §4.8 "A note arrives during Reviewing"): twice
# ``hitl.note_interpret_timeout_s``'s default of 15 s, so with the default every
# reading in flight when the wait begins has ended first (a note received
# mid-wait can still be pending when the wait ends), and a raised timeout (up to
# ten minutes) can hold the route for this long at most. A note still being read
# then is left out of this decision, unreviewed; a loop's next node takes it in
# once it is read.
_REVIEW_NOTES_WAIT_S = 30.0


def _reviewed_notes_update(started: ResearchState) -> ResearchStateUpdate:
```

with

```python
def _reviewed_notes_update(started: ResearchState) -> ResearchStateUpdate:
```

`src/deep_research/graph/nodes.py` — replace

```python
def note_sub_topic(note: ReaderNote, *, priority: int) -> SubTopic:
    """The sub-topic one reader note's targeted pass researches (live-briefs spec §4.6).

    Titled ``Your note: {restatement}``, with coverage id ``note-{note_id}``
    and one required target per question the note raised — or, for a note that
    raised none, the note's own restatement — each carrying the note's scope.
    """
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note.note_id}"
    questions = list(note.new_questions) or [note.restatement]
    geography = note.scope.geography if note.scope else None
    period = note.scope.period if note.scope else None
    return SubTopic(
        coverage_id=coverage_id,
        title=f"{NOTE_TOPIC_TITLE_PREFIX}{note.restatement}",
        rationale=(
            "The reader asked for this in a note, and the review found no "
            "evidence for it yet."
        ),
        search_queries=questions,
        success_criteria=[
            f"A checked source answers: {question}" for question in questions
        ],
        priority=priority,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"{coverage_id}-target-{number:02d}",
                coverage_id=coverage_id,
                question=question,
                required=True,
                measure=question,
                geography=geography,
                period=period,
            )
            for number, question in enumerate(questions, start=1)
        ],
    )


async def note_pass_node(channel: ResearchGraphState) -> ResearchGraphState:
```

with

```python
async def note_pass_node(channel: ResearchGraphState) -> ResearchGraphState:
```

`src/deep_research/graph/nodes.py` — replace

```python
    topics = [note_sub_topic(note, priority=priority) for note in due]
```

with

```python
    topics = [note_sub_topic(note, priority=priority, reason="no_evidence") for note in due]
```

- [ ] **Step 6: Export the new names from `deep_research.agents`**

`src/deep_research/agents/__init__.py` — replace

```python
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    PLANNING_NOTES,
    RESEARCH_NOTES,
    REVIEW_NOTES,
    SOURCE_NOTES,
    WRITING_NOTES,
    NoteLine,
    board_notes,
    live_reader_notes,
    notes_settled,
    render_reader_notes,
    research_reader_notes,
)
```

with

```python
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    NOTES_WAIT_S,
    PLANNING_NOTES,
    RESEARCH_NOTES,
    REVIEW_NOTES,
    SOURCE_NOTES,
    STEERING_KINDS,
    WRITING_NOTES,
    NoteLine,
    NoteTopicReason,
    board_notes,
    board_version,
    has_steering_kind,
    is_research_note,
    live_reader_notes,
    note_sub_topic,
    notes_being_read,
    notes_settled,
    render_reader_notes,
    research_notes_without_a_topic,
    research_reader_notes,
    steering_notes,
    steering_view,
    wait_for_board_change,
)
```

`src/deep_research/agents/__init__.py` — replace

```python
    "notes_settled",
    "render_reader_notes",
    "research_reader_notes",
```

with

```python
    "notes_settled",
    "render_reader_notes",
    "research_reader_notes",
    "NOTES_WAIT_S",
    "STEERING_KINDS",
    "NoteTopicReason",
    "board_version",
    "has_steering_kind",
    "is_research_note",
    "note_sub_topic",
    "notes_being_read",
    "research_notes_without_a_topic",
    "steering_notes",
    "steering_view",
    "wait_for_board_change",
```

- [ ] **Step 7: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_note_routing.py tests/test_agents/test_reader_notes_writing.py tests/test_imports.py -q
```

Expected: `65 passed` and no failure.

- [ ] **Step 8: Run the notes suites and the import guard**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph tests/test_runtime tests/test_api tests/test_agents/test_reader_notes_planning.py tests/test_agents/test_reader_notes_review.py tests/test_agents/test_reader_notes_writing.py -q
.venv\Scripts\python.exe -m ruff check --select F src/deep_research/agents/reader_notes.py src/deep_research/runtime/notes.py src/deep_research/graph/nodes.py src/deep_research/agents/__init__.py
```

Expected: `610 passed` (more if Phase D added tests under `tests/test_api`) and no failure, then `All checks passed!`.

- [ ] **Step 9: Commit**

```powershell
git add src/deep_research/runtime/notes.py src/deep_research/agents/reader_notes.py src/deep_research/graph/nodes.py src/deep_research/agents/__init__.py tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_note_routing.py tests/test_agents/test_reader_notes_writing.py
git commit -m "feat(notes): research, steering and mixed note helpers; the board's change count; note_sub_topic moves to agents"
```

### Task 3: A note's short label — `ReaderNote.short` and the reading's `short`

The spec gives this to whichever of A and C lands first (§4 item 5, §7.2 "Note short"); A lands first (§9), so A adds it and C only reads it.

**Files:**
- Modify: `src/deep_research/utils/types.py:1149-1150` (new `MAX_NOTE_SHORT_CHARS`, `note_short_label`), `:1173-1214` (`ReaderNote`)
- Modify: `src/deep_research/api/notes.py:38-45`, `:67-92`, `:134-174`, `:183-197`, `:291-314`
- Test: `tests/test_graph/test_reader_notes_state.py`, `tests/test_api/test_notes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (Phase C's note labels read these):
  - `utils/types.py`: `MAX_NOTE_SHORT_CHARS = 24`; `note_short_label(restatement: str) -> str`; `ReaderNote.short: str` (default `""`, at most 24 characters, one line; never empty after validation: derived from the restatement when the reading named none).
  - `api/notes.py`: `NoteInterpretationDraft.short: str = ""`; `NoteInterpretation.short: str = ""` (≤ 24 characters); `validated_interpretation` keeps a draft's `short` only when it has 1–3 words and ≤ 24 characters, and otherwise leaves it `""` without refusing the reading; `reader_note` copies it; `NOTE_INSTRUCTION` asks for it.

- [ ] **Step 1: Write the failing tests**

`tests/test_graph/test_reader_notes_state.py` — replace

```python
        "kinds": ["scope", "exclude"],
        "restatement": "more weight on grid storage (n1)",
        "scope": {"geography": "United States", "period": None},
```

with

```python
        "kinds": ["scope", "exclude"],
        "restatement": "more weight on grid storage (n1)",
        "short": "more weight on",
        "scope": {"geography": "United States", "period": None},
```

`tests/test_graph/test_reader_notes_state.py` — replace

```python
        {"new_questions": ["a", "b", "c", "d"]},
        {"text": "x" * 501},
    ):
```

with

```python
        {"new_questions": ["a", "b", "c", "d"]},
        {"text": "x" * 501},
        {"short": "x" * 25},
    ):
```

`tests/test_graph/test_reader_notes_state.py` — replace

```python
from deep_research.utils.types import (
    NoteDisposition,
    ReaderNote,
    ReportReview,
    ResearchState,
    active_reader_notes,
    merge_research_state,
    with_board_notes,
)
```

with

```python
from deep_research.utils.types import (
    NoteDisposition,
    ReaderNote,
    ReportReview,
    ResearchState,
    active_reader_notes,
    merge_research_state,
    note_short_label,
    with_board_notes,
)
```

Append to `tests/test_graph/test_reader_notes_state.py`:

```python


def test_a_notes_label_is_its_first_three_words_cut_on_a_word_boundary() -> None:
    """notes-progress-report spec §7.2 "Note short": the label a note carries when its reading
    named none — the restatement's first three words, cut at 24 characters on a word boundary."""
    assert note_short_label("more weight on grid storage") == "more weight on"
    assert note_short_label("internationalisation standards everywhere") == "internationalisation"
    assert note_short_label("extraordinarily-long-hyphenated-subject words") == "extraordinarily-long-hyp"
    assert note_short_label("only  the\tEU") == "only the EU"
    assert fake_reader_note("n1").short == "more weight on"
    assert fake_reader_note("n1", short=" grid  storage ").short == "grid storage"
```

`tests/test_api/test_notes.py` — replace

```python
from deep_research.api.notes import (
    NOTE_MAX_TOKENS,
    NoteInterpretation,
```

with

```python
from deep_research.api.notes import (
    NOTE_INSTRUCTION,
    NOTE_MAX_TOKENS,
    NoteInterpretation,
```

`tests/test_api/test_notes.py` — replace

```python
def test_the_fallback_keeps_the_note_as_written_as_an_emphasis() -> None:
```

with

```python
def test_a_reading_names_its_note_in_one_to_three_words_or_the_note_derives_it() -> None:
    """notes-progress-report spec §7.2 "Note short": the reading's label is kept at 1-3 words
    and at most 24 characters; a label outside those bounds is dropped — never the reading —
    and the board's note then derives one from its restatement, as the fallback's and
    replay's notes always do."""
    named = validated_interpretation(_draft(short=" United  States "), earlier=EARLIER)
    assert named is not None and named.short == "United States"
    assert reader_note(RECEIVED, named).short == "United States"
    for bad in ("", "   ", "the whole of the United States", "x" * 25):
        reading = validated_interpretation(_draft(short=bad), earlier=EARLIER)
        assert reading is not None and reading.short == "", bad
        assert reader_note(RECEIVED, reading).short == "only the United", bad
    assert reader_note(RECEIVED, fallback_interpretation("Mostly the US, please.")).short == "Mostly the US,"
    assert "- short: the note's subject in one to three words for a label, lower case.\n" in NOTE_INSTRUCTION
    assert NOTE_INSTRUCTION.index("- restatement:") < NOTE_INSTRUCTION.index("- short:") < NOTE_INSTRUCTION.index("- scope:")


def test_the_fallback_keeps_the_note_as_written_as_an_emphasis() -> None:
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_reader_notes_state.py -q
.venv\Scripts\python.exe -m pytest tests/test_api/test_notes.py -q
```

Expected: the first command stops at `Interrupted: 1 error during collection` — `ImportError: cannot import name 'note_short_label' from 'deep_research.utils.types'`; the second ends `1 failed` with the rest passed (`1 failed, 27 passed` on the stand-in export), the failure being `test_a_reading_names_its_note_in_one_to_three_words_or_the_note_derives_it` with `ValidationError: 1 validation error for NoteInterpretationDraft` (the draft has no `short` field yet).

- [ ] **Step 3: Give `ReaderNote` its label**

`src/deep_research/utils/types.py` — replace

```python
NOTE_TOPIC_TITLE_PREFIX = "Your note: "
"""A note's own sub-topic is titled ``Your note: {restatement}`` (live-briefs spec §4.6)."""
```

with

```python
NOTE_TOPIC_TITLE_PREFIX = "Your note: "
"""A note's own sub-topic is titled ``Your note: {restatement}`` (live-briefs spec §4.6)."""
MAX_NOTE_SHORT_CHARS = 24
"""A note's label, ``ReaderNote.short``, is at most 24 characters (notes-progress-report spec §7.2)."""


def note_short_label(restatement: str) -> str:
    """The restatement's first three words, cut at 24 characters on a word boundary.

    notes-progress-report spec §7.2: the label a note carries when its reading named
    none — the fallback and replay readings, a live reading whose ``short`` broke its
    bounds, and a note recorded before the field existed. A first word longer than 24
    characters is cut to its first 24.
    """
    words = restatement.split()[:3]
    label = ""
    for word in words:
        candidate = f"{label} {word}".strip()
        if len(candidate) > MAX_NOTE_SHORT_CHARS:
            break
        label = candidate
    if not label and words:
        label = words[0][:MAX_NOTE_SHORT_CHARS]
    return label
```

`src/deep_research/utils/types.py` — replace

```python
    The first nine fields are fixed once the note is interpreted; the three
    flags are the run's own bookkeeping, set by the graph: ``reviewed`` when a
    review input carried the note, ``passed`` when its one targeted research
    pass was bought, ``redrafted`` when its one redraft was (D11).
    ``restatement`` is the interpreter's plain-words reading, or the note's own
    text when the interpretation failed.
    """

    note_id: str = Field(pattern=r"^n([1-9]|10)$")
    text: str = Field(min_length=1, max_length=500)
    received_at: AwareISOString
    received_during: str = Field(min_length=1)
    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    scope: ReaderNoteScope | None = None
```

with

```python
    The first ten fields are fixed once the note is interpreted; the three
    flags are the run's own bookkeeping, set by the graph: ``reviewed`` when a
    review input carried the note, ``passed`` when its one targeted research
    pass was bought, ``redrafted`` when its one redraft was (D11).
    ``restatement`` is the interpreter's plain-words reading, or the note's own
    text when the interpretation failed. ``short`` names the note's subject in
    one to three words for a label (notes-progress-report spec §7.2): the
    reading's own, or ``note_short_label(restatement)`` when it named none.
    """

    note_id: str = Field(pattern=r"^n([1-9]|10)$")
    text: str = Field(min_length=1, max_length=500)
    received_at: AwareISOString
    received_during: str = Field(min_length=1)
    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    short: str = Field(default="", max_length=MAX_NOTE_SHORT_CHARS)
    scope: ReaderNoteScope | None = None
```

`src/deep_research/utils/types.py` — replace

```python
    def new_questions_are_one_line(cls, value: object) -> object:
        """The questions become a note sub-topic's queries and targets (``note_sub_topic``)."""
        if isinstance(value, list):
            return [
                collapse_whitespace(question) if isinstance(question, str) else question
                for question in value
            ]
        return value
```

with

```python
    def new_questions_are_one_line(cls, value: object) -> object:
        """The questions become a note sub-topic's queries and targets (``note_sub_topic``)."""
        if isinstance(value, list):
            return [
                collapse_whitespace(question) if isinstance(question, str) else question
                for question in value
            ]
        return value

    @field_validator("short", mode="before")
    @classmethod
    def short_is_one_line(cls, value: object) -> object:
        """The label is printed inside one line of the report (spec §7.2)."""
        return collapse_whitespace(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def short_label_or_derived(self) -> ReaderNote:
        """Every note carries a label: the reading's own, or its restatement's first words."""
        if not self.short:
            self.short = note_short_label(self.restatement)
        return self
```

- [ ] **Step 4: Ask the interpreter for the label, and keep it only within its bounds**

`src/deep_research/api/notes.py` — replace

```python
from deep_research.utils.types import (
    ReaderNote,
    ReaderNoteKind,
```

with

```python
from deep_research.utils.types import (
    MAX_NOTE_SHORT_CHARS,
    ReaderNote,
    ReaderNoteKind,
```

`src/deep_research/api/notes.py` — replace

```python
    kinds: list[str] = Field(default_factory=list)
    restatement: str = ""
    scope: NoteScopeDraft | None = None
```

with

```python
    kinds: list[str] = Field(default_factory=list)
    restatement: str = ""
    short: str = ""
    scope: NoteScopeDraft | None = None
```

`src/deep_research/api/notes.py` — replace

```python
    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    scope: ReaderNoteScope | None = None
    new_questions: list[str] = Field(default_factory=list, max_length=MAX_NEW_QUESTIONS)
```

with

```python
    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    short: str = Field(default="", max_length=MAX_NOTE_SHORT_CHARS)
    """The note's subject in 1-3 words for a label, or ``""`` when the reading named
    none (notes-progress-report spec §7.2); the board's note then derives it."""
    scope: ReaderNoteScope | None = None
    new_questions: list[str] = Field(default_factory=list, max_length=MAX_NEW_QUESTIONS)
```

`src/deep_research/api/notes.py` — replace

```python
    over 200 characters; a scope field over 120 characters. A ``replaces`` that
    names no earlier note of this run is dropped rather than failing the
    reading: the note itself stands.
    """
    earlier_ids = {note.note_id for note in earlier}
    restatement = collapse_whitespace(draft.restatement)
    if not restatement or len(restatement) > MAX_RESTATEMENT_CHARS:
        return None
```

with

```python
    over 200 characters; a scope field over 120 characters. A ``replaces`` that
    names no earlier note of this run is dropped rather than failing the
    reading: the note itself stands. So is a ``short`` that is not one to three
    words of at most 24 characters (notes-progress-report spec §7.2): a label
    never costs the reading, and the board's note derives one instead.
    """
    earlier_ids = {note.note_id for note in earlier}
    restatement = collapse_whitespace(draft.restatement)
    if not restatement or len(restatement) > MAX_RESTATEMENT_CHARS:
        return None
    short = collapse_whitespace(draft.short)
    if not 1 <= len(short.split()) <= 3 or len(short) > MAX_NOTE_SHORT_CHARS:
        short = ""
```

`src/deep_research/api/notes.py` — replace

```python
            kinds=[kind.strip() for kind in draft.kinds],  # type: ignore[misc]
            restatement=restatement,
            scope=(
```

with

```python
            kinds=[kind.strip() for kind in draft.kinds],  # type: ignore[misc]
            restatement=restatement,
            short=short,
            scope=(
```

`src/deep_research/api/notes.py` — replace

```python
    "example \"more weight on fire-safety standards\".\n"
    "- scope: the geography or period a scope note sets, or null.\n"
```

with

```python
    "example \"more weight on fire-safety standards\".\n"
    "- short: the note's subject in one to three words for a label, lower case.\n"
    "- scope: the geography or period a scope note sets, or null.\n"
```

`src/deep_research/api/notes.py` — replace

```python
        kinds=list(reading.kinds),
        restatement=reading.restatement,
        scope=reading.scope,
```

with

```python
        kinds=list(reading.kinds),
        restatement=reading.restatement,
        short=reading.short,
        scope=reading.scope,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_reader_notes_state.py tests/test_api/test_notes.py tests/test_api/test_note_route.py tests/test_runtime/test_note_board.py -q
```

Expected: `70 passed` on the stand-in export (more if Phase D added tests to these files) and no failure.

- [ ] **Step 6: Commit**

```powershell
git add src/deep_research/utils/types.py src/deep_research/api/notes.py tests/test_graph/test_reader_notes_state.py tests/test_api/test_notes.py
git commit -m "feat(notes): a note's short label, asked of the reading and derived when it names none"
```

### Task 4: Which requests print which notes — the evaluator, the writer and the review read steering views

**Files:**
- Modify: `src/deep_research/agents/source_evaluator.py:58`, `:1105-1109`
- Modify: `src/deep_research/agents/report_writer.py:46`, `:3246-3248`
- Modify: `src/deep_research/agents/report_reviewer.py:62`, `:625-628`, `:715-722`
- Modify: `tests/test_evaluation/test_config.py` (the `source_evaluator` and `report_writer` pins)
- Test: `tests/test_agents/test_reader_notes_review.py`, `tests/test_graph/test_reader_notes_replay.py`

**Interfaces:**
- Consumes: `steering_notes` (Task 2).
- Produces: the §5.1 table, exactly. The planner's requests print every active note with all its kinds (unchanged). Every research turn and extraction (Task 2's `research_reader_notes`), the source evaluator's `# Context`, the writer's section and bottom-line `# Reader notes`, and the review packet's `reader_notes` print `steering_notes(active_reader_notes(...))`. `ReportReviewInput.reader_notes` therefore never holds a note whose only kind is `new_angle`, and `_note_dispositions` drops a verdict that names one (its id is not in the packet, `agents/report_reviewer.py:1573`). No verifier request changes (LB-D10).

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_reader_notes_review.py` — replace

```python
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]
```

with

```python
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]
ANGLE = fake_reader_note("n3", kinds=["new_angle"], restatement="how battery cells are recycled")
MIXED = fake_reader_note("n4", kinds=["new_angle", "exclude"], restatement="recycling, leaving out exports")
```

Append to `tests/test_agents/test_reader_notes_review.py`:

```python


@pytest.mark.asyncio
async def test_review_packet_steering_notes_only() -> None:
    """notes-progress-report spec §5.5, AC8, D20: the packet lists the steering notes and each
    mixed note without new_angle in its kinds. A note whose only kind is new_angle is not put to
    the review, so a verdict naming it is dropped; a mixed note's verdict is kept, and judges
    its steering half."""
    noted = build_report_review_input(state_with_written_report(reader_notes=[*NOTES, ANGLE, MIXED]))
    body = review_messages(noted)[1].content

    assert [(view.note_id, view.kinds) for view in noted.reader_notes] == [
        ("n1", ["emphasis"]), ("n2", ["scope"]), ("n4", ["exclude"]),
    ]
    assert "- n4: recycling, leaving out exports (exclude)" in body
    assert "battery cells are recycled" not in body and "new_angle" not in body
    completer = ScriptedCompleter(outputs=[_notes_draft(
        ("n1", "honoured"), ("n3", "no_evidence"), ("n4", "ignored_with_evidence"),
    )])
    review = await ReportReviewer(provider=completer).review(noted, previous=None)
    assert [(d.note_id, d.status) for d in review.note_dispositions] == [
        ("n1", "honoured"), ("n4", "ignored_with_evidence"),
    ]
    only_angle = build_report_review_input(state_with_written_report(reader_notes=[ANGLE]))
    assert only_angle.reader_notes == []
    assert only_angle.fingerprint == build_report_review_input(state_with_written_report()).fingerprint
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
EMPHASIS_TEXT = "more weight on lithium-ion safety standards (emphasis)"
EMPHASIS_LINE = f"- {EMPHASIS_TEXT}"
ANGLE_LINE = "- how battery cells are recycled (new_angle)"
```

with

```python
MIXED = fake_reader_note(
    "n3", kinds=["new_angle", "exclude"], received_during="planner", restatement="recycling, leaving out exports",
    new_questions=["How are battery cells recycled without exporting them?"],
)
EMPHASIS_TEXT = "more weight on lithium-ion safety standards (emphasis)"
EMPHASIS_LINE = f"- {EMPHASIS_TEXT}"
ANGLE_LINE = "- how battery cells are recycled (new_angle)"
MIXED_FULL = "- recycling, leaving out exports (new_angle, exclude)"
MIXED_STEER = "- recycling, leaving out exports (exclude)"
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
@pytest.mark.asyncio
async def test_the_source_evaluator_and_the_writer_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: the scoring request's ``# Context`` slot carries the notes, for
    relevance only; the writer's section and bottom-line requests carry
    ``# Reader notes`` right after the answer form."""
```

with

```python
@pytest.mark.asyncio
async def test_the_source_evaluator_and_the_writer_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: the scoring request's ``# Context`` slot carries the notes, for
    relevance only; the writer's section and bottom-line requests carry
    ``# Reader notes`` right after the answer form. A note whose only kind is
    new_angle reaches neither: it is researched as its own topic instead
    (notes-progress-report spec §5.1)."""
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
        "# Context\nThe reader added these notes while the run was going. They bear on how relevant"
        in text and EMPHASIS_LINE in text and ANGLE_LINE in text
        for text in scoring
    )
```

with

```python
        "# Context\nThe reader added these notes while the run was going. They bear on how relevant"
        in text and EMPHASIS_LINE in text and ANGLE_LINE not in text
        for text in scoring
    )
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
            assert text.index("# Answer form\n") < text.index("# Reader notes\n") < text.index(after), key
            assert EMPHASIS_LINE in text and ANGLE_LINE in text, key
```

with

```python
            assert text.index("# Answer form\n") < text.index("# Reader notes\n") < text.index(after), key
            assert EMPHASIS_LINE in text and ANGLE_LINE not in text, key
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: more weight on lithium-ion safety standards (emphasis)" in review
    assert "- n2: how battery cells are recycled (new_angle)" in review
```

with

```python
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: more weight on lithium-ion safety standards (emphasis)" in review
    assert "- n2: how battery cells are recycled (new_angle)" not in review
```

`tests/test_graph/test_reader_notes_replay.py` — replace

```python
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [
        ("n1", "honoured"), ("n2", "honoured"),
    ]
```

with

```python
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [("n1", "honoured")]
```

Append to `tests/test_graph/test_reader_notes_replay.py`:

```python


@pytest.mark.asyncio
async def test_steering_views_per_request(tmp_path: Path) -> None:
    """notes-progress-report spec §5.1 table (D20): the planner's requests print every note with
    all its kinds. Every research turn, extraction, scoring, writing and review request prints
    the steering notes, and each mixed note as a steering note with new_angle left out; a note
    whose only kind is new_angle reaches none of them, and no verifier request carries a note."""
    with guarded():
        status, sequence, state = await replay_packets(
            tmp_path, EXTRA_PASS_CASE, board=noted_board(EMPHASIS, ANGLE, MIXED)
        )

    assert status == "completed"
    for key in ("planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        texts = packets_for(sequence, key)
        assert texts and all(
            EMPHASIS_LINE in text and ANGLE_LINE in text and MIXED_FULL in text for text in texts
        ), key
    for key in (
        "researcher:react", "researcher:SubTopicFindingsDraft", "source_evaluator:SourceScoresDraft",
        "report_writer:SectionDraft", "report_writer:BottomLineDraft",
    ):
        texts = packets_for(sequence, key)
        assert texts, key
        for text in texts:
            assert EMPHASIS_LINE in text and MIXED_STEER in text, key
            assert ANGLE_LINE not in text and MIXED_FULL not in text, key
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: more weight on lithium-ion safety standards (emphasis)" in review
    assert "- n3: recycling, leaving out exports (exclude)" in review
    assert "- n2:" not in review and ANGLE_LINE not in review and MIXED_FULL not in review
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [
        ("n1", "honoured"), ("n3", "honoured"),
    ]
    checks = [text for key, text in sequence if key.split(":")[1] in {"ContextCheckDraft", "StatementCheckDraft"}]
    assert checks
    for text in checks:
        assert "leaving out exports" not in text and "battery cells are recycled" not in text
        assert "Reader notes" not in text and "reader added these notes" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_reader_notes_review.py tests/test_graph/test_reader_notes_replay.py -q
```

Expected: `4 failed, 9 passed` — `test_review_packet_steering_notes_only`, `test_the_source_evaluator_and_the_writer_carry_the_notes`, `test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does` and `test_steering_views_per_request`, each because a note whose only kind is new_angle still reaches the evaluator, the writer and the review, and a mixed note reaches them with `new_angle` in its kinds.

- [ ] **Step 3: Print steering views in the three requests**

`src/deep_research/agents/source_evaluator.py` — replace

```python
from deep_research.agents.reader_notes import SOURCE_NOTES, render_reader_notes
```

with

```python
from deep_research.agents.reader_notes import (
    SOURCE_NOTES,
    render_reader_notes,
    steering_notes,
)
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
            # live-briefs spec §4.6: the reader's notes fill the request's
            # ``# Context`` slot, for relevance only; ``""`` without notes.
            guidance=render_reader_notes(
                active_reader_notes(state.reader_notes), instruction=SOURCE_NOTES
            ),
```

with

```python
            # live-briefs spec §4.6: the reader's notes fill the request's
            # ``# Context`` slot, for relevance only; ``""`` without notes. Their
            # steering views only (notes-progress-report spec §5.1): a note whose
            # only kind is new_angle is its own topic, and that topic is among the
            # sub-topics a source cited for it is judged with.
            guidance=render_reader_notes(
                steering_notes(active_reader_notes(state.reader_notes)),
                instruction=SOURCE_NOTES,
            ),
```

`src/deep_research/agents/report_writer.py` — replace

```python
from deep_research.agents.reader_notes import WRITING_NOTES, render_reader_notes
```

with

```python
from deep_research.agents.reader_notes import (
    WRITING_NOTES,
    render_reader_notes,
    steering_notes,
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
            reader_notes=render_reader_notes(
                active_reader_notes(state.reader_notes), instruction=WRITING_NOTES
            ),
```

with

```python
            # notes-progress-report spec §5.1: the steering views only; a note whose
            # only kind is new_angle is its own part of the report instead.
            reader_notes=render_reader_notes(
                steering_notes(active_reader_notes(state.reader_notes)),
                instruction=WRITING_NOTES,
            ),
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
from deep_research.agents.reader_notes import REVIEW_NOTES, render_reader_notes
```

with

```python
from deep_research.agents.reader_notes import (
    REVIEW_NOTES,
    render_reader_notes,
    steering_notes,
)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    """The reader's active notes (live-briefs spec §4.6), which the review
    judges one by one into ``note_dispositions``; ``[]`` for a run without
    notes, whose packet and fingerprint are then exactly what they were."""
```

with

```python
    """The reader's active notes as steering notes (live-briefs spec §4.6;
    notes-progress-report spec §5.5), which the review judges one by one into
    ``note_dispositions``: every steering note, and each mixed note with
    ``new_angle`` left out of its kinds. A note whose only kind is ``new_angle``
    is never listed — its own topic's targets decide it. ``[]`` for a run
    without such notes, whose packet and fingerprint are then exactly what
    they were."""
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            for note in active_reader_notes(state.reader_notes)
        ],
        composition_fingerprint=composition_semantic_fingerprint(composition),
```

with

```python
            # notes-progress-report spec §5.5 (D5, D20): the steering notes, and
            # each mixed note's steering half.
            for note in steering_notes(active_reader_notes(state.reader_notes))
        ],
        composition_fingerprint=composition_semantic_fingerprint(composition),
```

- [ ] **Step 4: Run the tests to verify they pass, and read the two moved fingerprints**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_reader_notes_review.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_report_reviewer.py tests/test_agents/test_source_evaluator.py -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print(f('source_evaluator'), f('report_writer'))"
```

Expected: `208 passed`, then `cc5a310b0aa0 cafa5ba3d613` when B-pin(source_evaluator) and B-pin(report_writer) are the `f4282818` values (`24809aa975a3`, `6e1aedc2888e`); with another B-pin, that agent prints another value, which Step 5 pins under the B-pin rule.

- [ ] **Step 5: Re-pin the two fingerprints**

`agent_prompt_fingerprint` hashes each agent module's source (`evaluation/config.py:280-288`), so the source evaluator's and the writer's pins move; no prompt string changed and `agents.prompts` was untouched. The blocks show the unmoved case (the B-pin rule, Conventions). When Task 1 Step 4 recorded another B-pin for an agent, that block's old line holds the B-pin, and the new pin line and the comment's "Moved `…` -> `…`" carry the B-pin and the value Step 4 printed. When the B-pin is the `f4282818` value and Step 4 printed anything other than the block's value, a block of this task was not applied byte-exactly. Find it, apply it again exactly, and run Step 4 again; never pin a printed value without finding the cause.

`tests/test_evaluation/test_config.py` — replace

```python
    "source_evaluator": "24809aa975a3",
```

with

```python
    # notes-progress-report Phase A (spec §5.1, 2026-09-30): the scoring
    # request's # Context slot carries the notes' steering views only (a note
    # whose only kind is new_angle is its own topic instead). Without notes
    # every request is byte-identical; agents.prompts was untouched. Moved
    # `24809aa975a3` -> `cc5a310b0aa0`.
    "source_evaluator": "cc5a310b0aa0",
```

`tests/test_evaluation/test_config.py` — replace

```python
    "report_writer": "6e1aedc2888e",
```

with

```python
    # notes-progress-report Phase A (spec §5.1, 2026-09-30): the section and
    # bottom-line requests carry the notes' steering views only. Without notes
    # every request is byte-identical; agents.prompts was untouched. Moved
    # `6e1aedc2888e` -> `cafa5ba3d613`.
    "report_writer": "cafa5ba3d613",
```

- [ ] **Step 6: Run the pins and the no-note byte-identity pin**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_evaluation/test_config.py tests/test_graph/test_reader_notes_replay.py -q
```

Expected: `84 passed`, including `test_without_notes_every_request_of_a_replay_run_is_byte_identical[...]` for both cases (`PINNED_RUN_DIGESTS` does not move: every change prints only when there are notes).

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/agents/source_evaluator.py src/deep_research/agents/report_writer.py src/deep_research/agents/report_reviewer.py tests/test_agents/test_reader_notes_review.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(notes): the evaluator, the writer and the review read the notes' steering views"
```

### Task 5: Planning-time research notes join the plan

**Files:**
- Modify: `src/deep_research/agents/reader_notes.py:29-36` (`PLANNING_NOTES`)
- Modify: `src/deep_research/agents/planner.py:38-42`, `:63-81`, `:2921-2949`, `:3262-3278`
- Modify: `tests/test_evaluation/test_config.py` (the `planner` pin)
- Test: `tests/test_agents/test_reader_notes_planning.py`, `tests/test_graph/test_reader_notes_replay.py`

**Interfaces:**
- Consumes: `is_research_note`, `note_sub_topic(..., reason="reader_note")`, `live_reader_notes` (Task 2).
- Produces:
  - `PLANNING_NOTES` is spec §5.2's text exactly.
  - `planning_completed_event(outcome: AgentRun[ResearchPlan], *, note_topics: Sequence[SubTopic] = ()) -> ResearchEvent`: `sub_topics` lists the plan's topics as `{coverage_id, title}`, then each note topic as `{coverage_id, title, note_id}`; `sub_topic_count` counts both; `note_topic_count` counts the note topics. Phase B adds a `states` keyword beside `note_topics` (§5.2 step 5).
  - `PlannerAgent.run`'s `state_update`: `sub_topics` is the plan's new topics, then the note topics in receipt order; `initial_target_ids` is the plan's ids, then the note topics' ids. A run with no plan (`outcome.result is None`) appends nothing and reports `note_topic_count: 0`.
  - `PlannerAgent._note_topics_for(state: ResearchState, plan: ResearchPlan | None) -> list[SubTopic]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_reader_notes_planning.py` — replace

```python
from deep_research.agents.planner import (
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
)
```

with

```python
from deep_research.agents.planner import (
    PlannerAgent,
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
)
```

`tests/test_agents/test_reader_notes_planning.py` — replace

```python
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]
```

with

```python
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]
ANGLE = fake_reader_note(
    "n3", kinds=["new_angle"], restatement="how battery cells are recycled", received_during="planner",
    new_questions=["How are battery cells recycled?", "What does recycling cost?"],
)
PLANNING_NOTES_TEXT = (
    "The reader added these notes while the run was going; each line is the note as the run "
    "understood it, and a later note replaces an earlier one it contradicts. Plan within them: an "
    "emphasis note gives its subject more weight, an exclude note leaves its subject out, a scope "
    "note narrows the plan to its scope, and an about_reader note says who the report is for. A "
    "new_angle note is researched as a sub-topic of its own that the run adds to this plan once it "
    "is final: do not plan a sub-topic for it, and a subject only a new_angle note asks for is not "
    "a missing part of the question."
)
```

`tests/test_agents/test_reader_notes_planning.py` — replace

```python
# --- researching ----------------------------------------------------------------
```

with

```python
def test_planning_notes_text() -> None:
    """notes-progress-report spec §5.2: the planner is told a new_angle note becomes a sub-topic
    the run adds itself, so it plans none for it and its review reports no missing dimension (E4)."""
    assert PLANNING_NOTES == PLANNING_NOTES_TEXT


@pytest.mark.asyncio
async def test_planner_appends_research_notes(tracker: Tracker) -> None:
    """notes-progress-report spec §5.2, AC1 (D1, D2): every research note read before the
    planner's run returns joins the plan it is published with — one read before planning, and
    one (a mixed note) read while the plan request was in flight — as its own required sub-topic,
    after the plan's own, in receipt order; planning.completed lists both with their note ids;
    and both plan requests carry the new lead."""
    board = NoteBoard()
    for note in [*NOTES, ANGLE]:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    board.receive("while planning", received_at=AT, received_during="planner")
    late = fake_reader_note(
        "n4", kinds=["new_angle", "exclude"], restatement="recycling, leaving out exports",
        received_during="planner",
    )

    def plan_while_a_note_arrives(messages: list, schema: type) -> object:
        board.add(late)  # read while the plan request is in flight
        return _sorting_plan()

    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[plan_while_a_note_arrives, _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=[*NOTES, ANGLE])

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            outcome = await agent.run(state)

    topics = outcome.state_update["sub_topics"]
    assert [topic.coverage_id for topic in topics] == ["topic-01", "topic-02", "topic-03", "note-n3", "note-n4"]
    angle, mixed = topics[3], topics[4]
    assert (angle.title, angle.priority, angle.rationale) == (
        "Your note: how battery cells are recycled", 4, "The reader asked for this in a note.",
    )
    assert [(t.target_id, t.question, t.required) for t in angle.evidence_targets] == [
        ("note-n3-target-01", "How are battery cells recycled?", True),
        ("note-n3-target-02", "What does recycling cost?", True),
    ]
    assert [(t.target_id, t.question) for t in mixed.evidence_targets] == [
        ("note-n4-target-01", "recycling, leaving out exports"),
    ]
    assert outcome.state_update["initial_target_ids"][-3:] == [
        "note-n3-target-01", "note-n3-target-02", "note-n4-target-01",
    ]
    completed = outcome.state_update["events"][-1]
    assert completed.event_type == "planner.planning.completed"
    assert completed.metadata["sub_topics"][3:] == [
        {"coverage_id": "note-n3", "title": "Your note: how battery cells are recycled", "note_id": "n3"},
        {"coverage_id": "note-n4", "title": "Your note: recycling, leaving out exports", "note_id": "n4"},
    ]
    assert (completed.metadata["sub_topic_count"], completed.metadata["note_topic_count"]) == (5, 2)
    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert PLANNING_NOTES_TEXT in plan_request and PLANNING_NOTES_TEXT in review_request
    assert "- how battery cells are recycled (new_angle)" in plan_request
    assert "- recycling, leaving out exports (new_angle, exclude)" in review_request


@pytest.mark.asyncio
async def test_planner_appends_nothing_without_a_plan(
    tracker: Tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notes-progress-report spec §5.2 (review M10): a run that produced no plan appends no note
    topic; its update holds only its errors and events, and the note is the researcher's, or
    owes a note pass."""

    async def no_plan(self: PlannerAgent, task: object, run: object) -> None:
        return None

    monkeypatch.setattr(PlannerAgent, "finalize", no_plan)
    completer = ScriptedCompleter(decisions=[finish("No lookup needed.", "Scoped.")])
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=[ANGLE])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.result is None
    assert set(outcome.state_update) == {"errors", "events"}
    completed = outcome.state_update["events"][-1]
    assert (
        completed.metadata["sub_topic_count"], completed.metadata["note_topic_count"], completed.metadata["sub_topics"]
    ) == (0, 0, [])


# --- researching ----------------------------------------------------------------
```

Append to `tests/test_graph/test_reader_notes_replay.py`:

```python


@pytest.mark.asyncio
async def test_mixed_kind_note_steers_and_researches(tmp_path: Path) -> None:
    """notes-progress-report spec AC34 (D20), on the real graph: a mixed note read before
    planning joins the plan as its own topic, which the run researches; the planner's requests
    print both its kinds; every loop, extraction, scoring, writing and review request carries it
    as an exclude note; and the review's verdict on it is kept."""
    alone = fake_reader_note(
        "n1", kinds=["new_angle", "exclude"], received_during="planner", restatement="recycling, leaving out exports",
        new_questions=["How are battery cells recycled without exporting them?"],
    )
    with guarded():
        status, sequence, state = await replay_packets(tmp_path, EXTRA_PASS_CASE, board=noted_board(alone))

    assert status == "completed"
    topics = {topic.coverage_id: topic for topic in state.sub_topics}
    assert "note-n1" in topics
    assert (topics["note-n1"].title, [target.question for target in topics["note-n1"].evidence_targets]) == (
        "Your note: recycling, leaving out exports", ["How are battery cells recycled without exporting them?"],
    )
    [completed] = [event for event in state.events if event.event_type == "planner.planning.completed"]
    assert completed.metadata["sub_topics"][-1] == {
        "coverage_id": "note-n1", "title": "Your note: recycling, leaving out exports", "note_id": "n1",
    }
    assert completed.metadata["note_topic_count"] == 1
    assert any(
        event.event_type == "researcher.sub_topic.started" and event.metadata["coverage_id"] == "note-n1"
        for event in state.events
    )
    for key in ("planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        texts = packets_for(sequence, key)
        assert texts and all("- recycling, leaving out exports (new_angle, exclude)" in text for text in texts), key
    for key in (
        "researcher:react", "researcher:SubTopicFindingsDraft", "source_evaluator:SourceScoresDraft",
        "report_writer:SectionDraft", "report_writer:BottomLineDraft",
    ):
        texts = packets_for(sequence, key)
        assert texts, key
        for text in texts:
            assert "- recycling, leaving out exports (exclude)" in text, key
            assert "(new_angle, exclude)" not in text, key
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: recycling, leaving out exports (exclude)" in review
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [("n1", "honoured")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py -q
```

Expected: `4 failed, 10 passed` — `test_planning_notes_text` (the old lead), `test_planner_appends_research_notes` (no `note-n3` topic), `test_planner_appends_nothing_without_a_plan` (no `note_topic_count` key yet) and `test_mixed_kind_note_steers_and_researches` (no `note-n1` topic in the plan).

- [ ] **Step 3: Tell the planner the run adds a new_angle note's sub-topic itself**

`src/deep_research/agents/reader_notes.py` — replace

```python
PLANNING_NOTES = (
    "The reader added these notes while the run was going; each line is the "
    "note as the run understood it, and a later note replaces an earlier one it "
    "contradicts. Plan within them: an emphasis note gives its subject more "
    "weight, an exclude note leaves its subject out, a scope note narrows the "
    "plan to its scope, an about_reader note says who the report is for, and a "
    "new_angle note can become a sub-topic of its own."
)
```

with

```python
PLANNING_NOTES = (
    "The reader added these notes while the run was going; each line is the "
    "note as the run understood it, and a later note replaces an earlier one it "
    "contradicts. Plan within them: an emphasis note gives its subject more "
    "weight, an exclude note leaves its subject out, a scope note narrows the "
    "plan to its scope, and an about_reader note says who the report is for. A "
    "new_angle note is researched as a sub-topic of its own that the run adds to "
    "this plan once it is final: do not plan a sub-topic for it, and a subject "
    "only a new_angle note asks for is not a missing part of the question."
)
```

- [ ] **Step 4: Append the research notes' topics when the plan is published**

`src/deep_research/agents/planner.py` — replace

```python
from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    live_reader_notes,
    render_reader_notes,
)
```

with

```python
from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    is_research_note,
    live_reader_notes,
    note_sub_topic,
    render_reader_notes,
)
```

`src/deep_research/agents/planner.py` — replace

```python
from deep_research.utils.types import (
    MAX_TARGETS_PER_TOPIC,
    AnswerContract,
```

with

```python
from deep_research.utils.types import (
    MAX_TARGETS_PER_TOPIC,
    NOTE_COVERAGE_PREFIX,
    AnswerContract,
```

`src/deep_research/agents/planner.py` — replace

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
            "repair_attempted": False if plan is None else plan.repair_attempted,
```

with

```python
def planning_completed_event(
    outcome: AgentRun["ResearchPlan"],
    *,
    note_topics: Sequence[SubTopic] = (),
) -> ResearchEvent:
    """Report the finished plan's size, its sub-topics and how the scoping loop stopped.

    ``sub_topics`` lists each planned sub-topic's ``coverage_id`` and title, the
    title capped at 160 characters (live-briefs spec AC2): plan content a console
    shows the reader, never provider error text (``agents/events.py``). The
    reader's research notes the plan is published with follow, each with its
    ``note_id`` (notes-progress-report spec §5.2); ``sub_topic_count`` counts
    both, and ``note_topic_count`` the notes' alone.
    """
    plan = outcome.result
    planned = [] if plan is None else list(plan.sub_topics)
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": len(planned) + len(note_topics),
            "sub_topics": [
                *(
                    {
                        "coverage_id": sub_topic.coverage_id,
                        "title": summarize_text(sub_topic.title, limit=160),
                    }
                    for sub_topic in planned
                ),
                *(
                    {
                        "coverage_id": sub_topic.coverage_id,
                        "title": summarize_text(sub_topic.title, limit=160),
                        "note_id": sub_topic.coverage_id.removeprefix(
                            NOTE_COVERAGE_PREFIX
                        ),
                    }
                    for sub_topic in note_topics
                ),
            ],
            "note_topic_count": len(note_topics),
            "repair_attempted": False if plan is None else plan.repair_attempted,
```

`src/deep_research/agents/planner.py` — replace

```python
        finally:
            self._restricted_toolset = None
        completed = planning_completed_event(outcome)
        publish_live(completed)
        events.append(completed)
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update={**outcome.state_update, "events": events},
            call_fingerprints=dict(outcome.call_fingerprints),
        )
```

with

```python
        finally:
            self._restricted_toolset = None
        # notes-progress-report spec §5.2 (D1, D2): every research note read so
        # far joins the plan it is published with, as its own sub-topic. Nothing
        # from here to the live publication below awaits, so a note read after
        # this line is the researcher's to pick up (§5.3).
        note_topics = self._note_topics_for(state, outcome.result)
        completed = planning_completed_event(outcome, note_topics=note_topics)
        publish_live(completed)
        events.append(completed)
        update: ResearchStateUpdate = {**outcome.state_update, "events": events}
        if note_topics:
            update["sub_topics"] = [*update.get("sub_topics", []), *note_topics]
            update["initial_target_ids"] = [
                *update.get("initial_target_ids", []),
                *inventory_target_ids(note_topics),
            ]
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update=update,
            call_fingerprints=dict(outcome.call_fingerprints),
        )

    def _note_topics_for(
        self, state: ResearchState, plan: ResearchPlan | None
    ) -> list[SubTopic]:
        """The sub-topics of the research notes this plan is published with (spec §5.2).

        Every active note read so far — the state's, then the board's newer ones —
        whose kinds include ``new_angle`` and whose ``note-{id}`` neither the
        session nor the plan holds yet, in receipt order, one priority after the
        plan's last. None for a run that produced no plan: such a note is the
        researcher's, or owes a note pass.
        """
        if plan is None:
            return []
        known = [*state.sub_topics, *plan.sub_topics]
        known_ids = {topic.coverage_id for topic in known}
        held = [
            note
            for note in live_reader_notes(self._reader_notes)
            if is_research_note(note)
            and f"{NOTE_COVERAGE_PREFIX}{note.note_id}" not in known_ids
        ]
        if not held:
            return []
        priority = max(topic.priority for topic in known) + 1
        return [
            note_sub_topic(note, priority=priority, reason="reader_note")
            for note in held
        ]
```

- [ ] **Step 5: Run the tests to verify they pass, and read the moved fingerprint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_planner.py -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print(f('planner'))"
```

Expected: `303 passed`, then `3934bea57f61` when B-pin(planner) is `d1ba46ce147f`; with another B-pin, another value, which Step 6 pins under the B-pin rule.

- [ ] **Step 6: Re-pin the planner**

The block shows the unmoved case (the B-pin rule, Conventions). When Task 1 Step 4 recorded another B-pin(planner), the old line holds it, and the new pin line and the comment's "Moved `…` -> `…`" carry the B-pin and the value Step 5 printed. When B-pin(planner) is `d1ba46ce147f` and Step 5 printed anything but `3934bea57f61`, a block of this task was not applied byte-exactly. Find it, apply it again exactly, and run Step 5 again; never pin a printed value without finding the cause.

`tests/test_evaluation/test_config.py` — replace

```python
    "planner": "d1ba46ce147f",
```

with

```python
    # notes-progress-report Phase A (spec §5.2, 2026-09-30): the reader's
    # research notes join the plan as their own sub-topics when it is
    # published, planning.completed lists them, and the planning lead says the
    # run adds them. Without notes every request is byte-identical;
    # agents.prompts was untouched. Moved `d1ba46ce147f` -> `3934bea57f61`.
    "planner": "3934bea57f61",
```

- [ ] **Step 7: Run the pins**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_evaluation/test_config.py -q
```

Expected: `78 passed`.

- [ ] **Step 8: Commit**

```powershell
git add src/deep_research/agents/reader_notes.py src/deep_research/agents/planner.py tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(planner): research notes read before the plan is published join it as their own sub-topics"
```

### Task 6: Research-time research notes get their own thread — the researcher's dispatcher

**Files:**
- Modify: `src/deep_research/agents/researcher.py` — imports (`:15-19`, `:57-63`, `:85-104`), `select_sub_topics` (`:341-349`), `sub_topic_started_event` (`:2590-2612`), `research_completed_event`'s docstring (`:2731-2739`), two module helpers after `_cancel_and_gather_pages` (`:3011-3032`), `__init__` (`:3165-3166`), `_reader_notes_block` (`:3190-3205`), `_planned_targets` (`:3207-3238`), `extract_findings` (`:3752-3760`), `state_update` (`:4314-4322`), `_research_sub_topic` (`:4378-4386`), `run` (`:4639-4652`, `:4653`, `:4692-4695`, the stop comment `:4749-4753`, the closure's header and gate line `:4756-4760`, its comment `:4771-4779`, the gather and the fold `:4786-4828`, `:4859-4866`; never the tool lock `:4741-4747` or the loop call `:4769`, review P2-1)
- Modify: `tests/test_evaluation/test_config.py` (the `researcher` pin)
- Create: `tests/test_agents/test_research_note_threads.py`

**Interfaces:**
- Consumes: `NOTES_WAIT_S`, `board_version`, `wait_for_board_change`, `notes_being_read`, `notes_settled`, `research_notes_without_a_topic`, `note_sub_topic` (Task 2).
- Produces:
  - `researcher.select_sub_topics(state, max_sub_topics)` caps the planned topics only and adds every eligible `note-…` topic after them; `_selected_and_capped(state, max_sub_topics) -> tuple[list[SubTopic], list[SubTopic]]`.
  - `researcher.sub_topic.started` metadata gains `note_id` for a topic whose coverage id starts with `note-` (absent for a planned topic). Phase A's web (Task 10) reads it.
  - `researcher.research.completed.sub_topics_planned` counts the state's topics plus the note threads this run started.
  - `ResearcherAgent._run_note_topics: list[SubTopic]` (reset per run); `ResearcherAgent.state_update` appends them as `sub_topics` when there are any; `_planned_targets()` includes their targets on any pass; `_coverage_titles() -> dict[str, str]`.
  - `ResearcherAgent.run`: no longer one `gather`; every thread is its own task, cancelled and awaited in a `finally` on any exit by exception, `CancelledError` included — Phase D's stop and Phase C and B rely on nothing else from here.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_research_note_threads.py`:

```python
"""A reader's research note gets its own research thread inside the running researcher
(notes-progress-report spec §5.3, D3; AC2, AC3, AC4, AC35)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from deep_research.agents.reader_notes import note_sub_topic
from deep_research.agents.researcher import ResearcherAgent
from deep_research.graph.live import bind_live_sink
from deep_research.graph.nodes import agent_node
from deep_research.graph.state import dump_state, load_state
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ResearchEvent, ResearchState, SubTopic
from tests.agent_fakes import TargetKeyedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_sub_topic, fake_target
from tests.research_fakes import research_tools

AT = "2026-09-30T10:00:00.000+00:00"
QUESTION = "Where can we get the best tasting lattes in San Jose?"


class GatedCompleter(TargetKeyedCompleter):
    """``TargetKeyedCompleter`` whose named keys wait on an ``asyncio.Event`` before each
    reply, recording each key whose wait was cancelled."""

    def __init__(self, *, gates: Mapping[str, asyncio.Event] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gates = dict(gates or {})
        self.cancelled: list[str] = []

    async def _delay_for(self, key: str) -> None:
        gate = self.gates.get(key)
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(key)
                raise
        await super()._delay_for(key)


def _researcher(
    tracker: Tracker, completer: TargetKeyedCompleter, *, concurrency: int = 1, max_sub_topics: int = 10
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(session_id="session-1", agent_name="researcher", max_entries=20),
        tools=research_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
        max_sub_topics=max_sub_topics,
        sub_topic_concurrency=concurrency,
    )


def _topic(number: int) -> SubTopic:
    coverage_id = f"topic-{number:02d}"
    return fake_sub_topic(
        f"Topic {number}", coverage_id=coverage_id, priority=number,
        targets=[fake_target(f"{coverage_id}-target-01", coverage_id=coverage_id)],
    )


def _state(*topics: SubTopic) -> ResearchState:
    return ResearchState(session_id="session-1", original_question=QUESTION, sub_topics=list(topics))


def _done(key: str) -> object:
    return finish(f"{key} is covered.", f"{key} answer.")


def _seen(events: Sequence[ResearchEvent], event_type: str, coverage_id: str) -> list[ResearchEvent]:
    return [e for e in events if e.event_type == event_type and e.metadata.get("coverage_id") == coverage_id]


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _read(board: NoteBoard, note_id: str, **overrides: Any) -> None:
    """Receive one note on the board and read it at once."""
    board.receive("a note", received_at=AT, received_during="researcher")
    board.add(fake_reader_note(note_id, **overrides))


@pytest.mark.asyncio
async def test_researcher_note_topics_uncapped(tracker: Tracker) -> None:
    """AC2: ten planned topics at max_sub_topics = 10 and one note topic: all eleven are
    researched, and the cap skips none of them."""
    planned = [_topic(number) for number in range(1, 11)]
    noted = note_sub_topic(fake_reader_note("n1", kinds=["new_angle"]), priority=11, reason="reader_note")
    completer = TargetKeyedCompleter(decisions={t.coverage_id: [_done(t.coverage_id)] for t in [*planned, noted]})
    agent = _researcher(tracker, completer, concurrency=10, max_sub_topics=10)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(*planned, noted))

    events = outcome.state_update["events"]
    started = [e.metadata["coverage_id"] for e in events if e.event_type == "researcher.sub_topic.started"]
    assert sorted(started) == sorted([*(t.coverage_id for t in planned), "note-n1"])
    assert not [e for e in outcome.errors if e.error_type == "researcher_sub_topic_skipped"]
    research = events[-1]
    assert research.event_type == "researcher.research.completed"
    assert (research.metadata["sub_topics_researched"], research.metadata["sub_topics_skipped"]) == (11, 0)


@pytest.mark.asyncio
async def test_research_note_thread_starts_ungated(tracker: Tracker) -> None:
    """AC3 (D3): with the only loop slot held by a blocked planned loop, a research note read
    meanwhile starts its own thread at once — its started event names the note and is published
    before the blocked loop completes — and the researcher's node completes only after the note's
    own loop has."""
    planned_gate, note_gate = asyncio.Event(), asyncio.Event()
    completer = GatedCompleter(
        gates={"topic-01": planned_gate, "note-n1": note_gate},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]},
    )
    node = agent_node(_researcher(tracker, completer, concurrency=1))
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(node(dump_state(_state(_topic(1)))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"], restatement="pastries in the cafe")
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "note-n1")))
            assert not _seen(published, "researcher.sub_topic.completed", "topic-01")
            planned_gate.set()
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "topic-01")))
            await asyncio.sleep(0.02)
            assert not running.done()
            note_gate.set()
            final = load_state(await asyncio.wait_for(running, timeout=5))

    [started] = _seen(published, "researcher.sub_topic.started", "note-n1")
    assert (started.metadata["note_id"], started.metadata["index"], started.metadata["sub_topic"]) == (
        "n1", 2, "Your note: pastries in the cafe",
    )
    assert "note_id" not in _seen(published, "researcher.sub_topic.started", "topic-01")[0].metadata
    kinds = [(e.event_type, e.metadata.get("coverage_id") or e.metadata.get("node")) for e in final.events]
    assert kinds.index(("researcher.sub_topic.completed", "note-n1")) < kinds.index(("graph.node.completed", "researcher"))
    assert [topic.coverage_id for topic in final.sub_topics] == ["topic-01", "note-n1"]
    [research] = [e for e in final.events if e.event_type == "researcher.research.completed"]
    assert (research.metadata["sub_topics_planned"], research.metadata["sub_topics_researched"]) == (2, 2)


@pytest.mark.asyncio
async def test_late_note_waits_then_threads(tracker: Tracker) -> None:
    """AC4 (§5.3): a note received while the last loop runs, and read only once every loop has
    ended, is waited for and still gets its thread before the researcher returns."""
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]})
    agent = _researcher(tracker, completer)
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "topic-01")))
            await asyncio.sleep(0.05)
            assert not running.done()
            board.add(fake_reader_note("n1", kinds=["new_angle"]))
            outcome = await asyncio.wait_for(running, timeout=5)

    assert [topic.coverage_id for topic in outcome.state_update["sub_topics"]] == ["note-n1"]
    assert [
        e.metadata["coverage_id"] for e in outcome.state_update["events"] if e.event_type == "researcher.sub_topic.completed"
    ] == ["topic-01", "note-n1"]


@pytest.mark.asyncio
async def test_late_note_wait_times_out_and_closes_window(tracker: Tracker, monkeypatch: pytest.MonkeyPatch) -> None:
    """§5.3, §5.8: a reading that outlasts the wait closes the window when the wait times out;
    the researcher returns with no thread for it, and the note stays on the board to be read."""
    monkeypatch.setattr("deep_research.agents.researcher.NOTES_WAIT_S", 0.05)
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")]})
    agent = _researcher(tracker, completer)
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            outcome = await asyncio.wait_for(agent.run(_state(_topic(1))), timeout=5)

    assert board.pending == ("n1",)
    assert "sub_topics" not in outcome.state_update
    assert [
        e.metadata["coverage_id"] for e in outcome.state_update["events"] if e.event_type == "researcher.sub_topic.started"
    ] == ["topic-01"]


@pytest.mark.asyncio
async def test_note_thread_provider_failure_sets_stop(tracker: Tracker) -> None:
    """AC35 (review I5): a note thread that ends in a provider failure stops the pass like any
    loop: the planned loop already running finishes, no later note gets a thread in this run,
    and the failed thread keeps its topic, its completed event recording the failure."""
    planned_gate = asyncio.Event()
    completer = GatedCompleter(
        gates={"topic-01": planned_gate},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [ProviderTimeoutError("timed out")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"])
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "note-n1")))
            # The failed thread sets stop only after its completed event is published; give it
            # the turns it needs first, so the next note is read once stop is set however many
            # awaits lie between that event and the thread's return.
            await asyncio.sleep(0.02)
            _read(board, "n2", kinds=["new_angle"])
            planned_gate.set()
            outcome = await asyncio.wait_for(running, timeout=5)

    [failed] = _seen(published, "researcher.sub_topic.completed", "note-n1")
    assert failed.metadata["stop_reason"] == "provider_error"
    assert not _seen(published, "researcher.sub_topic.started", "note-n2")
    [planned] = _seen(outcome.state_update["events"], "researcher.sub_topic.completed", "topic-01")
    assert planned.metadata["stop_reason"] == "finished"
    assert [topic.coverage_id for topic in outcome.state_update["sub_topics"]] == ["note-n1"]


@pytest.mark.asyncio
async def test_a_planning_time_note_topic_waits_its_turn_and_stop_leaves_it_unstarted(tracker: Tracker) -> None:
    """§5.3 (review 2, I-1): a planning-time note topic is gated and sorts last; when another
    loop's provider failure sets stop before it starts, it never opens, records only
    ``provider_failure_stopped_processing`` and has no completed event."""
    noted = note_sub_topic(fake_reader_note("n1", kinds=["new_angle"]), priority=2, reason="reader_note")
    completer = TargetKeyedCompleter(
        decisions={"topic-01": [ProviderTimeoutError("timed out")], "note-n1": [_done("note-n1")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(_topic(1), noted))

    events = outcome.state_update["events"]
    assert not _seen(events, "researcher.sub_topic.started", "note-n1")
    assert not _seen(events, "researcher.sub_topic.completed", "note-n1")
    skipped = [e.details for e in outcome.errors if e.error_type == "researcher_sub_topic_skipped"]
    assert [(d["coverage_id"], d["reason"]) for d in skipped] == [("note-n1", "provider_failure_stopped_processing")]
    assert completer.remaining("note-n1") == 1
    assert "sub_topics" not in outcome.state_update


@pytest.mark.asyncio
async def test_dispatcher_cancels_threads_on_cancel(tracker: Tracker) -> None:
    """§5.3: the dispatcher's tasks are not a gather's children, so a cancelled run (Phase D's
    stop) cancels every thread still running — a planned loop and a note's — and awaits each
    before the cancellation leaves the researcher."""
    completer = GatedCompleter(
        gates={"topic-01": asyncio.Event(), "note-n1": asyncio.Event()},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"])
            await _until(lambda: len(completer.react_calls) == 2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

    assert sorted(completer.cancelled) == ["note-n1", "topic-01"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_research_note_threads.py -q
```

Expected: `6 failed, 1 passed`, in about 16 s: no note thread ever starts and the researcher never waits, so the ungated, waiting and cancelling tests fail (most with `TimeoutError` from `_until`), `test_researcher_note_topics_uncapped` finds the note topic capped, and `test_late_note_wait_times_out_and_closes_window` cannot patch the missing `NOTES_WAIT_S`. `test_a_planning_time_note_topic_waits_its_turn_and_stop_leaves_it_unstarted` passes already: it pins today's gated behaviour, which the dispatcher keeps.

- [ ] **Step 3: Import what the dispatcher needs, and cap only the planned topics**

`src/deep_research/agents/researcher.py` — replace

```python
import asyncio
import json
import logging
```

with

```python
import asyncio
import contextlib
import json
import logging
```

`src/deep_research/agents/researcher.py` — replace

```python
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    RESEARCH_NOTES,
    live_reader_notes,
    render_reader_notes,
    research_reader_notes,
)
```

with

```python
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    NOTES_WAIT_S,
    RESEARCH_NOTES,
    board_version,
    live_reader_notes,
    note_sub_topic,
    notes_being_read,
    notes_settled,
    render_reader_notes,
    research_notes_without_a_topic,
    research_reader_notes,
    wait_for_board_change,
)
```

`src/deep_research/agents/researcher.py` — replace

```python
    MAX_SNIPPET_CHARS,
    QUALITY_CONTRACT_VERSION,
```

with

```python
    MAX_SNIPPET_CHARS,
    NOTE_COVERAGE_PREFIX,
    QUALITY_CONTRACT_VERSION,
```

`src/deep_research/agents/researcher.py` — replace

```python
def select_sub_topics(
    state: ResearchState,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """The first pass researches every planned sub-topic; an extra pass only the
    sub-topics that own a missing required target (spec §6.5, §7.2)."""
    if max_sub_topics < 1:
        raise ValueError("max_sub_topics must be at least 1")
    return _eligible_sub_topics(state)[:max_sub_topics]
```

with

```python
def _selected_and_capped(
    state: ResearchState, max_sub_topics: int
) -> tuple[list[SubTopic], list[SubTopic]]:
    """This pass's topics, and the planned ones the cap leaves out.

    notes-progress-report spec §5.3: the eligible planned topics, at most
    ``max_sub_topics`` of them, then every eligible reader-note topic
    (``note-…``), which the cap never touches: a run holds at most ten notes
    (LB-D11a).
    """
    eligible = _eligible_sub_topics(state)
    planned = [
        topic
        for topic in eligible
        if not topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX)
    ]
    noted = [
        topic
        for topic in eligible
        if topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX)
    ]
    return [*planned[:max_sub_topics], *noted], planned[max_sub_topics:]


def select_sub_topics(
    state: ResearchState,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """The first pass researches every planned sub-topic, up to ``max_sub_topics``,
    and every reader-note sub-topic; an extra or note pass only the sub-topics
    that own one of its targets (spec §6.5, §7.2; notes-progress-report §5.3)."""
    if max_sub_topics < 1:
        raise ValueError("max_sub_topics must be at least 1")
    return _selected_and_capped(state, max_sub_topics)[0]
```

- [ ] **Step 4: Name the note in its thread's started event, and count the threads as planned**

`src/deep_research/agents/researcher.py` — replace

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
            "index": index,
            "existing_sources": existing_sources,
        },
    )
```

with

```python
    """Announce that one sub-topic's loop is about to run.

    ``coverage_id`` lets a console match the topic to the plan's own list
    (live-briefs spec E3). A reader note's own topic (``note-{id}``) also names
    its ``note_id`` (notes-progress-report spec §5.3), so the page can say the
    note is being researched now; a planned topic carries no such key.
    """
    metadata: dict[str, JsonValue] = {
        "sub_topic": summarize_text(sub_topic.title),
        "coverage_id": sub_topic.coverage_id,
        "priority": sub_topic.priority,
        "index": index,
        "existing_sources": existing_sources,
    }
    if sub_topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX):
        metadata["note_id"] = sub_topic.coverage_id.removeprefix(NOTE_COVERAGE_PREFIX)
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.started",
        message=f"Researching sub-topic {index}.",
        metadata=metadata,
    )
```

`src/deep_research/agents/researcher.py` — replace

```python
    ``sub_topics_planned`` is every sub-topic the Planner produced;
```

with

```python
    ``sub_topics_planned`` is every sub-topic the Planner produced, plus the
    reader notes' own topics this run started threads for (notes-progress-report
    spec §5.3);
```

- [ ] **Step 5: Add the dispatcher's two waits**

`src/deep_research/agents/researcher.py` — replace

```python
    for page_task in page_extractions.values():
        if not page_task.done():
            page_task.cancel()
    if page_extractions:
        await asyncio.gather(*page_extractions.values(), return_exceptions=True)
```

with

```python
    for page_task in page_extractions.values():
        if not page_task.done():
            page_task.cancel()
    if page_extractions:
        await asyncio.gather(*page_extractions.values(), return_exceptions=True)


async def _until_a_thread_ends_or_the_board_changes(
    threads: Sequence["asyncio.Task[_SubTopicOutcome | None]"],
    seen: int | None,
) -> None:
    """Return once any of ``threads`` ends or, with a board bound, the board changes.

    notes-progress-report spec §5.3: ``seen`` is the board's change count read
    before the dispatcher's scan, so a note added after that scan wakes this
    wait at once. With no board bound (``None``: the CLI) only the threads are
    waited on. The board's waiter never outlives the call.
    """
    change = (
        None if seen is None else asyncio.ensure_future(wait_for_board_change(seen))
    )
    try:
        await asyncio.wait(
            [*threads, *([] if change is None else [change])],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if change is not None:
            change.cancel()
            await asyncio.gather(change, return_exceptions=True)


def _thread_result(
    thread: "asyncio.Task[_SubTopicOutcome | None]",
) -> "_SubTopicOutcome | None | BaseException":
    """One settled thread's outcome, or the exception it ended with."""
    if thread.cancelled():
        return asyncio.CancelledError()
    error = thread.exception()
    return thread.result() if error is None else error
```

- [ ] **Step 6: Keep the run's note topics, offer their targets, and append them to the plan**

`src/deep_research/agents/researcher.py` — replace

```python
        self._sub_topic_concurrency = resolved_concurrency
        self._run_source_state: ResearchState | None = None
```

with

```python
        self._sub_topic_concurrency = resolved_concurrency
        self._run_source_state: ResearchState | None = None
        # The reader notes' own topics this run started threads for, in start
        # order (notes-progress-report spec §5.3): their targets join the
        # extraction list, and the run's update appends them to the plan.
        self._run_note_topics: list[SubTopic] = []
```

`src/deep_research/agents/researcher.py` — replace

```python
        The notes this pass was handed plus any that arrived since, from the
        run's board, less ``new_angle`` notes: those wait for the review's
        note pass rather than steering a loop already running.
        """
```

with

```python
        The notes this pass was handed plus any that arrived since, from the
        run's board, as steering notes (notes-progress-report spec §5.1): a
        note whose only kind is ``new_angle`` gets a thread of its own instead,
        and a mixed note steers with ``new_angle`` left out of its kinds.
        """
```

`src/deep_research/agents/researcher.py` — replace

```python
        A run with no plan in hand — a direct ``extract_findings`` call, or a
        snapshot predating the target inventory — yields none, and extraction
        then binds nothing, because there is no inventory to bind against.
        """
        state = self._run_source_state
        if state is None:
            return []
        planned = [
            target
            for sub_topic in state.sub_topics
            for target in counted_evidence_targets(sub_topic.evidence_targets)
        ]
        wanted = set(state.extra_pass_target_ids)
        if not wanted:
            return planned
        return [target for target in planned if target.target_id in wanted]
```

with

```python
        A run with no plan in hand — a direct ``extract_findings`` call, or a
        snapshot predating the target inventory — yields none, and extraction
        then binds nothing, because there is no inventory to bind against.

        A reader note's own thread started in this run (notes-progress-report
        spec §5.3) adds its topic's targets, on any pass. The list is built
        again at each call, so a read made after a thread started sees its
        targets.
        """
        state = self._run_source_state
        if state is None:
            return []
        planned = [
            target
            for sub_topic in state.sub_topics
            for target in counted_evidence_targets(sub_topic.evidence_targets)
        ]
        wanted = set(state.extra_pass_target_ids)
        if wanted:
            planned = [target for target in planned if target.target_id in wanted]
        return [
            *planned,
            *(
                target
                for topic in self._run_note_topics
                for target in counted_evidence_targets(topic.evidence_targets)
            ),
        ]

    def _coverage_titles(self) -> dict[str, str]:
        """Each topic's title by coverage id: the plan's, then this run's note threads'."""
        topics = [
            *(
                self._run_source_state.sub_topics
                if self._run_source_state is not None
                else ()
            ),
            *self._run_note_topics,
        ]
        return {topic.coverage_id: topic.title for topic in topics}
```

`src/deep_research/agents/researcher.py` — replace

```python
        planned_targets = self._planned_targets() if policy is not None else []
        coverage_titles = {
            sub_topic.coverage_id: sub_topic.title
            for sub_topic in (
                self._run_source_state.sub_topics
                if self._run_source_state is not None
                else ()
            )
        }
```

with

```python
        planned_targets = self._planned_targets() if policy is not None else []
        coverage_titles = self._coverage_titles()
```

`src/deep_research/agents/researcher.py` — replace

```python
        planned_targets = self._planned_targets()
        coverage_titles = {
            sub_topic.coverage_id: sub_topic.title
            for sub_topic in (
                self._run_source_state.sub_topics
                if self._run_source_state is not None
                else ()
            )
        }
```

with

```python
        planned_targets = self._planned_targets()
        coverage_titles = self._coverage_titles()
```

`src/deep_research/agents/researcher.py` — replace

```python
        """Findings and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["raw_findings"] = list(result.findings)
```

with

```python
        """Findings, errors and this run's note topics. ``run`` adds the progress events.

        The reader notes' own topics this run started threads for join the
        plan's (notes-progress-report spec §5.3); the target inventories are
        left as they are, as a note pass leaves them.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["raw_findings"] = list(result.findings)
        if self._run_note_topics:
            update["sub_topics"] = list(self._run_note_topics)
```

- [ ] **Step 7: Replace the gather with the dispatcher**

`src/deep_research/agents/researcher.py` — replace

```python
        finish; a run-wide attempt ceiling is re-raised after every loop has
        settled, so the node still halts the run.
        """
        self._run_source_state = state
        self._run_reads = dict(state.read_records)
```

with

```python
        finish; a run-wide attempt ceiling is re-raised after every loop has
        settled, so the node still halts the run.

        A reader's research note read while the loops run gets its own thread
        at once, outside the ``sub_topic_concurrency`` slots, and the run does
        not return until every thread has ended (notes-progress-report spec
        §5.3, D3); a note still being read when they have is waited for, for
        ``NOTES_WAIT_S`` at most.
        """
        self._run_source_state = state
        self._run_note_topics = []
        self._run_reads = dict(state.read_records)
```

`src/deep_research/agents/researcher.py` — replace

```python
        base_task = self.build_task(state)
        eligible = _eligible_sub_topics(state)
        selected = eligible[: self._max_sub_topics]
        capped = eligible[self._max_sub_topics :]
```

with

```python
        base_task = self.build_task(state)
        # notes-progress-report spec §5.3: the cap holds the planned topics only.
        selected, capped = _selected_and_capped(state, self._max_sub_topics)
```

The next four edits leave alone what the latency workstream rewrites (review P2-1): the tool lock's comment and construction (`researcher.py:4741-4747`, including the gate's line), the stop comment's first line (`:4748`), and the loop's call `self._research_one(index, task, tool_lock)` (`:4769`). Whatever those lines hold when this task runs, they stay.

`src/deep_research/agents/researcher.py` — replace

```python
        # failure. A loop that has not started yet checks it as it acquires
        # the gate and stops there, which is what keeps
        # ``provider_failure_stopped_processing`` for the topics that never
        # got a turn, while the loops already running finish. It is set before
        # the gate is released, so a waiter cannot slip past it.
```

with

```python
        # failure. A loop that has not started yet checks it as it starts and
        # stops there, which is what keeps ``provider_failure_stopped_processing``
        # for the topics that never got a turn, while the loops already running
        # finish; and once it is set no reader note gets a thread of its own
        # (notes-progress-report spec §5.3). A gated loop sets it before the
        # gate is released, so a waiter cannot slip past it.
```

`src/deep_research/agents/researcher.py` — replace

```python
        async def research(
            index: int, sub_topic: SubTopic
        ) -> _SubTopicOutcome | None:
            """Run one sub-topic, or skip it when the pass has stopped."""
            async with gate:
```

with

```python
        async def research(
            index: int, sub_topic: SubTopic, *, gated: bool
        ) -> _SubTopicOutcome | None:
            """Run one sub-topic, or skip it when the pass has stopped.

            A gated topic waits for one of ``sub_topic_concurrency`` slots; a
            reader note's own thread is not gated (spec §5.3), so it starts at
            once, beside however many loops are running.
            """
            async with (gate if gated else contextlib.nullcontext()):
```

`src/deep_research/agents/researcher.py` — replace

```python
                    # A loop that RAISES stops the pass exactly as one that
                    # returns a provider failure does. The error is re-raised
                    # to the gather below, so every topic still queued behind
                    # this gate must not start: its model turns would be spent
                    # on work the re-raise throws away. (The plan's ceiling is
                    # seven sub-topics and the default cap is five, so queued
                    # work is the ordinary case, not a corner.) The flag is set
                    # before the gate is released, so a waiter cannot slip past
                    # it.
```

with

```python
                    # A loop that RAISES stops the pass exactly as one that
                    # returns a provider failure does. The error is re-raised
                    # once every thread has settled, so every topic still
                    # queued behind this gate, and every note not yet given a
                    # thread, must not start: its model turns would be spent on
                    # work the re-raise throws away.
```

`src/deep_research/agents/researcher.py` — replace the lines from the one starting `        settled_results = await asyncio.gather(` up to, not including, the one starting `        # Every sub-topic that was never attempted -- either truncated by the`, with

```python
        # notes-progress-report spec §5.3 (D3): the dispatcher. Every selected
        # topic runs as its own task; a research note read while any of them
        # runs gets its own thread at once. A note still being read when every
        # thread has ended is waited for, ``NOTES_WAIT_S`` at most, so it still
        # gets its thread. The window closes on a scan that found no note due,
        # with no await between that scan and the loop's exit, so a note read
        # after it owes a note pass instead (§5.4).
        order: list[SubTopic] = list(selected)
        threads = [
            asyncio.create_task(research(index, sub_topic, gated=True))
            for index, sub_topic in enumerate(selected, start=1)
        ]
        note_priority = (
            max((topic.priority for topic in state.sub_topics), default=0) + 1
        )
        topic_ids = {topic.coverage_id for topic in state.sub_topics}
        closing = False
        try:
            while True:
                seen = board_version()
                if not stop.is_set():
                    for note in research_notes_without_a_topic(
                        state.reader_notes, topic_ids
                    ):
                        topic = note_sub_topic(
                            note, priority=note_priority, reason="reader_note"
                        )
                        topic_ids.add(topic.coverage_id)
                        self._run_note_topics.append(topic)
                        order.append(topic)
                        threads.append(
                            asyncio.create_task(
                                research(len(order), topic, gated=False)
                            )
                        )
                unfinished = [thread for thread in threads if not thread.done()]
                if unfinished:
                    await _until_a_thread_ends_or_the_board_changes(unfinished, seen)
                    continue
                if closing or stop.is_set() or not notes_being_read():
                    break
                closing = not await notes_settled(timeout=NOTES_WAIT_S)
        finally:
            # Unlike a gather's children, these tasks are not cancelled with the
            # run: whatever ends it early -- a cancellation (Phase D's stop)
            # included -- cancels every thread still running and awaits it.
            running = [thread for thread in threads if not thread.done()]
            for thread in running:
                thread.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
        settled_results = [_thread_result(thread) for thread in threads]
        # Every thread has settled by now, so a halt cancels no work: a
        # run-wide attempt ceiling is re-raised and still stops the run
        # (``graph/nodes.py`` halts on it), and any other unexpected failure is
        # re-raised rather than swallowed, in thread order.
        refusals = [
            item
            for item in settled_results
            if isinstance(item, RequestAttemptLimitError)
        ]
        if refusals:
            raise refusals[0]
        settled: list[_SubTopicOutcome | None] = []
        for item in settled_results:
            if isinstance(item, BaseException):
                raise item
            settled.append(item)

        # The folds below iterate the run's own task list, never the order the
        # loops finished in: the plan's topics in plan order, then the note
        # threads in the order they started, so a report cannot depend on which
        # loop happened to be scheduled first.
        unstarted: list[SubTopic] = []
        for sub_topic, outcome in zip(order, settled, strict=True):
            if outcome is None:
                unstarted.append(sub_topic)
                continue
            if outcome.target_id is not None:
                if outcome.target_state is not None:
                    self._run_acquisition_states[outcome.target_id] = (
                        outcome.target_state
                    )
                self._run_seen_target_ids.add(outcome.target_id)
            runs.append(outcome.react)
            findings.extend(outcome.findings)
            errors.extend(outcome.errors)
            events.extend(outcome.events)

```

`src/deep_research/agents/researcher.py` — replace

```python
            research_completed_event(
                sub_topics_planned=len(state.sub_topics),
                sub_topics_researched=len(runs),
```

with

```python
            research_completed_event(
                sub_topics_planned=len(state.sub_topics) + len(self._run_note_topics),
                sub_topics_researched=len(runs),
```

- [ ] **Step 8: Run the tests to verify they pass, and read the moved fingerprint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_research_note_threads.py tests/test_agents/test_researcher.py tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print(f('researcher'))"
```

Expected: `256 passed`, then `b9caf536e3f0` when B-pin(researcher) is `a8c9528f0c20`; with another B-pin, another value, which Step 9 pins under the B-pin rule.

- [ ] **Step 9: Re-pin the researcher**

The block shows the unmoved case (the B-pin rule, Conventions). When Task 1 Step 4 recorded another B-pin(researcher) — for instance after the latency workstream landed — the old line holds it, and the new pin line and the comment's "Moved `…` -> `…`" carry the B-pin and the value Step 8 printed. When B-pin(researcher) is `a8c9528f0c20` and Step 8 printed anything but `b9caf536e3f0`, a block of this task was not applied byte-exactly. Find it, apply it again exactly, and run Step 8 again; never pin a printed value without finding the cause.

`tests/test_evaluation/test_config.py` — replace

```python
    "researcher": "a8c9528f0c20",
```

with

```python
    # notes-progress-report Phase A (spec §5.3, 2026-09-30): a dispatcher
    # replaces the one gather, so a research note read while the loops run gets
    # its own ungated thread; note topics are never capped; the turns and the
    # extraction read the notes' steering views. Without notes every request is
    # byte-identical; agents.prompts was untouched. Moved `a8c9528f0c20` ->
    # `b9caf536e3f0`.
    "researcher": "b9caf536e3f0",
```

- [ ] **Step 10: Run the pins, the graph and the replay suites**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_evaluation/test_config.py tests/test_graph tests/test_e2e_evaluation -q
.venv\Scripts\python.exe -m ruff check --select F src/deep_research/agents/researcher.py tests/test_agents/test_research_note_threads.py
```

Expected: `368 passed` and no failure, then `All checks passed!`.

- [ ] **Step 11: Commit**

```powershell
git add src/deep_research/agents/researcher.py tests/test_agents/test_research_note_threads.py tests/test_evaluation/test_config.py
git commit -m "feat(researcher): a research note read while research runs gets its own thread at once"
```

### Task 7: Note outcomes — the §5.6 table, the steering half, and the API field

Phase D left `note_outcome` with a `terminal` keyword and its three `return "pending"` exits reading `"not_checked" if terminal else "pending"` (§4 item 2). This task replaces that function with §5.6's table, as §4 item 2 says A does, and adds `note_steering_outcome`. Because its anchors cover text Phase D wrote, this task's span edits are marked "(D-touched)": each replaces whatever text sits between two lines that Phase D did not change.

**Files:**
- Modify: `src/deep_research/api/notes.py` (imports `:19-45`; the span from `def note_outcome(` to `__all__ = [`; `__all__`)
- Modify: `src/deep_research/api/models.py` (`ReaderNoteResponse`, the span from its `outcome:` line to `class ClarificationQuestionResponse(ApiModel):`)
- Modify: `src/deep_research/api/sessions.py` (`session_note_fields`, the span from `state = session.outcome.state …` to `"notes_remaining": …`)
- Test: `tests/test_api/test_notes.py`, `tests/test_api/test_note_route.py`, and any other test file whose note record Step 1's script extends (P3-7)
- Removes one test (review P3-2): the span from `def _finished(` replaces `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending` (`tests/test_api/test_notes.py:189-201` at `f4282818`; Phase D keeps its name and adds its `terminal` assertions). `test_note_outcome_table` supersedes it, row by row against §5.6. The span keeps `_finished` and rewrites `test_the_records_list_every_accepted_note_in_order_read_or_not` for `NoteRecord`. The plan's `+38` Python tests is net of this removal.

**Interfaces:**
- Consumes: `is_research_note`, `has_steering_kind`, `note_sub_topic` (Task 2); Phase D's `NoteOutcome` (with `not_checked`), `TERMINAL_STATUSES` (with `stopped`).
- Produces (Phase C's `_terminal_artifacts` calls the first two with `terminal=True`, §7.2):
  - `note_outcome(note_id: str, state: ResearchState | None, *, terminal: bool) -> NoteOutcome` — §5.6's table.
  - `note_steering_outcome(note_id: str, state: ResearchState | None, *, terminal: bool, note: ReaderNote | None = None) -> NoteOutcome | None` — `None` unless the note is mixed; `note` is the board's reading, used only when no state holds the note (spec ambiguity A-4 below).
  - `class NoteRecord(NamedTuple)`: `received: ReceivedNote`, `restatement: str | None`, `outcome: NoteOutcome`, `steering_outcome: NoteOutcome | None`; `note_records(board, state, *, terminal) -> list[NoteRecord]`.
  - `ReaderNoteResponse.steering_outcome: Literal[covered, not_found, not_addressed, pending, replaced, not_checked] | None = None`.
  - Removed: `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending`, superseded by `test_note_outcome_table`.

- [ ] **Step 1: Write the failing tests**

Every note in a session response gains `steering_outcome` (`None` unless the note is mixed), so a test that compares a whole note record to the response must carry it. Today such records are `tests/test_api/test_note_route.py:429-431` and `web/e2e/notes.spec.ts:108-111`, which this step and Task 10 Step 6 edit. Phase D may add more, for example for a stopped session's `not_checked` notes (review P3-7). This script extends every one-line note record that holds `"note_id"` and `"outcome": "not_checked"` in `tests/` (Python), or `note_id:` and `outcome: "not_checked"` in `web/test/` and `web/e2e/` (TypeScript), and does not yet carry the field. It adds `"steering_outcome": None` or `steering_outcome: null` right after the outcome. It skips `web/e2e/notes.spec.ts`, which Task 10 Step 6 rewrites, and stages each file it changes, so Step 6's commit takes it:

```powershell
@'
import re
import subprocess
from pathlib import Path
RULES = [
    ("tests", "*.py", re.compile(r'("outcome": "not_checked")(?=[,}])'), ', "steering_outcome": None', '"note_id"'),
    ("web/test", "*.ts*", re.compile(r'(outcome: "not_checked"(?: as const)?)(?=[,} ])'), ", steering_outcome: null", "note_id:"),
    ("web/e2e", "*.ts", re.compile(r'(outcome: "not_checked"(?: as const)?)(?=[,} ])'), ", steering_outcome: null", "note_id:"),
]
changed = []
for root, pattern, rx, extra, key in RULES:
    for path in sorted(Path(root).rglob(pattern)):
        if path.as_posix() == "web/e2e/notes.spec.ts" or "node_modules" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        hits = 0
        for k, line in enumerate(lines):
            if key in line and "steering_outcome" not in line and rx.search(line):
                lines[k] = rx.sub(lambda m: m.group(1) + extra, line, count=1)
                hits += 1
                print("extended " + path.as_posix() + ":" + str(k + 1))
        if hits:
            path.write_text("\n".join(lines), encoding="utf-8")
            changed.append(path.as_posix())
print("records extended in " + str(len(changed)) + " files")
if changed:
    subprocess.run(["git", "add", "--", *changed], check=True)
'@ | .venv\Scripts\python.exe -
```

Expected: the last line reads `records extended in N files`, each `extended` line above it naming one record it changed. On the export with Phase D's draft plan applied it extended one record and printed `extended web/test/components/reader-notes.test.tsx:68`: Phase D turns that fixture's note `n3` into a `not_checked` one. On the stand-in export it printed `records extended in 0 files`. Extending an input fixture changes nothing it renders, because a note whose `steering_outcome` is `null` keeps its one caption.

Then the tests of this task:

`tests/test_api/test_notes.py` — replace

```python
from typing import Any

import pytest
from pydantic import ValidationError
```

with

```python
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError
```

`tests/test_api/test_notes.py` — replace

```python
from deep_research.api.models import (
    NoteAcceptedResponse,
```

with

```python
from deep_research.agents.reader_notes import note_sub_topic
from deep_research.api.models import (
    NoteAcceptedResponse,
```

`tests/test_api/test_notes.py` — replace

```python
    note_interpreted_event,
    note_messages,
    note_outcome,
    note_received_event,
    note_records,
    reader_note,
    scripted_note_interpreter,
    validated_interpretation,
)
from deep_research.runtime.notes import NoteBoard, ReceivedNote
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchState
from tests.graph_fakes import fake_reader_note, fake_report_review
```

with

```python
    note_interpreted_event,
    note_messages,
    note_outcome,
    note_received_event,
    note_records,
    note_steering_outcome,
    reader_note,
    scripted_note_interpreter,
    validated_interpretation,
)
from deep_research.api.sessions import ResearchSession, session_note_fields
from deep_research.runtime.notes import NoteBoard, ReceivedNote
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import (
    NotFoundTarget,
    ReaderNote,
    ReportComposition,
    ReportQualitySnapshot,
    ResearchState,
    SubTopic,
)
from tests.graph_fakes import fake_reader_note, fake_report_review
from tests.test_api.fakes import make_outcome
```

`tests/test_api/test_notes.py` (D-touched) — replace the lines from the one starting `def _finished(` up to, not including, the one starting `def test_the_note_shapes_trim_bound_and_default_as_the_spec_says`, with

```python
def _finished(*notes: Any, verdicts: dict[str, str]) -> ResearchState:
    return ResearchState(
        session_id="s1", original_question="Q?", reader_notes=list(notes),
        report_review=fake_report_review(note_dispositions=verdicts),
    )


ANGLE_NOTE = fake_reader_note("n1", kinds=["new_angle"], new_questions=["How are cells recycled?", "At what cost?"])
MIXED_NOTE = fake_reader_note("n2", kinds=["new_angle", "exclude"])


def _topics(*notes: ReaderNote) -> list[SubTopic]:
    return [note_sub_topic(note, priority=2, reason="reader_note") for note in notes]


def test_note_outcome_table() -> None:
    """notes-progress-report spec §5.6, row by row (D5, D31): a research note — a mixed note's
    new_angle half included — is decided by its own topic's targets, never by the review; a
    steering note by the review's verdict; and what would read ``pending`` while the run goes on
    reads ``not_checked`` once it has ended."""
    steering = [fake_reader_note(f"n{number}") for number in range(3, 7)]
    base = _finished(ANGLE_NOTE, MIXED_NOTE, *steering, verdicts={
        "n2": "ignored_with_evidence", "n3": "honoured", "n4": "ignored_with_evidence", "n5": "no_evidence",
    })
    for terminal, waiting in ((False, "pending"), (True, "not_checked")):
        assert note_outcome("n1", None, terminal=terminal) == waiting
        assert note_outcome("n9", base, terminal=terminal) == waiting
        assert note_outcome("n1", base, terminal=terminal) == waiting
        assert note_outcome("n2", base, terminal=terminal) == waiting
        assert [note_outcome(f"n{number}", base, terminal=terminal) for number in (3, 4, 5, 6)] == [
            "covered", "not_addressed", "not_found", waiting,
        ]
    topics = base.model_copy(update={"sub_topics": _topics(ANGLE_NOTE, MIXED_NOTE)})
    assert note_outcome("n1", topics, terminal=True) == "not_checked"
    answered = topics.model_copy(update={
        "quality": ReportQualitySnapshot(answered_target_ids=["note-n1-target-02", "note-n2-target-01"]),
    })
    assert [note_outcome(note_id, answered, terminal=True) for note_id in ("n1", "n2")] == ["covered", "covered"]
    searched = topics.model_copy(update={"composition": ReportComposition(
        question="Q?", session_id="s1", not_found=[
            NotFoundTarget(target_id="note-n1-target-01", question="How are cells recycled?", searched=True),
            NotFoundTarget(target_id="note-n2-target-01", question="more weight on grid storage (n2)", searched=False),
        ],
    )})
    assert note_outcome("n1", searched, terminal=False) == "not_found"
    assert note_outcome("n2", searched, terminal=False) == "pending"
    replaced = _finished(ANGLE_NOTE, fake_reader_note("n2", replaces="n1"), verdicts={})
    assert note_outcome("n1", replaced, terminal=True) == "replaced"


def test_note_steering_outcome_table() -> None:
    """§5.6 (D20): only a mixed note has a steering outcome — the review's verdict on its
    steering half, mapped as a steering note's — and it too never reads ``pending`` once the
    run has ended. With no state yet, the board's reading of the note says whether it is mixed."""
    steer, angle = fake_reader_note("n3"), fake_reader_note("n4", kinds=["new_angle"])
    state = _finished(MIXED_NOTE, steer, angle, verdicts={"n2": "ignored_with_evidence", "n3": "honoured"})
    assert [note_steering_outcome(note_id, state, terminal=True) for note_id in ("n3", "n4")] == [None, None]
    for verdict, outcome in (
        ("honoured", "covered"), ("ignored_with_evidence", "not_addressed"), ("no_evidence", "not_found"),
    ):
        assert note_steering_outcome("n2", _finished(MIXED_NOTE, verdicts={"n2": verdict}), terminal=False) == outcome
    unjudged = _finished(MIXED_NOTE, verdicts={})
    assert note_steering_outcome("n2", unjudged, terminal=False) == "pending"
    assert note_steering_outcome("n2", unjudged, terminal=True) == "not_checked"
    assert note_steering_outcome("n2", None, terminal=False, note=MIXED_NOTE) == "pending"
    assert note_steering_outcome("n2", None, terminal=True, note=MIXED_NOTE) == "not_checked"
    assert note_steering_outcome("n3", None, terminal=True, note=steer) is None
    assert note_steering_outcome("n2", None, terminal=True) is None
    replaced = _finished(MIXED_NOTE, fake_reader_note("n3", replaces="n2"), verdicts={"n2": "honoured"})
    assert note_steering_outcome("n2", replaced, terminal=True) == "replaced"
    halves = unjudged.model_copy(update={
        "sub_topics": _topics(MIXED_NOTE),
        "quality": ReportQualitySnapshot(answered_target_ids=["note-n2-target-01"]),
        "report_review": fake_report_review(note_dispositions={"n2": "ignored_with_evidence"}),
    })
    assert (note_outcome("n2", halves, terminal=True), note_steering_outcome("n2", halves, terminal=True)) == (
        "covered", "not_addressed",
    )


def test_replaced_note_after_topic_reads_replaced() -> None:
    """§5.8: a research note replaced after its topic was researched keeps its topic, and the
    topic's part, in the run, and reads ``replaced``; the note that replaced it is judged on
    its own kinds."""
    later = fake_reader_note("n2", kinds=["scope"], replaces="n1")
    state = _finished(ANGLE_NOTE, later, verdicts={"n2": "honoured"}).model_copy(update={
        "sub_topics": _topics(ANGLE_NOTE),
        "quality": ReportQualitySnapshot(answered_target_ids=["note-n1-target-01"]),
    })

    assert [topic.coverage_id for topic in state.sub_topics] == ["note-n1"]
    assert note_outcome("n1", state, terminal=True) == "replaced"
    assert note_outcome("n2", state, terminal=True) == "covered"


@pytest.mark.parametrize("status", ["completed", "max_iterations", "incomplete", "failed", "stopped"])
def test_terminal_sessions_never_pending(status: str) -> None:
    """AC9: no session in a terminal status — ``stopped`` included — reports a note ``pending``
    in either field: not a research note with no topic, not a steering note no review judged,
    not a mixed note, and not a note still being read when the run ended."""
    now = datetime.now(timezone.utc)
    session = ResearchSession(session_id="s1", query="Q?", status=status, started_at=now, finished_at=now)  # type: ignore[arg-type]
    notes = [ANGLE_NOTE, fake_reader_note("n2"), fake_reader_note("n3", kinds=["new_angle", "exclude"])]
    for note in notes:
        session.note_board.receive(note.text, received_at=RECEIVED.received_at, received_during="researcher")
        session.note_board.add(note)
    session.note_board.receive("still being read", received_at=RECEIVED.received_at, received_during="report_reviewer")
    if status != "stopped":
        session.outcome = make_outcome(
            session_id="s1", question="Q?",
            state=ResearchState(session_id="s1", original_question="Q?", reader_notes=notes),
        )

    fields = session_note_fields(session)

    assert [(note.note_id, note.outcome, note.steering_outcome) for note in fields["notes"]] == [
        ("n1", "not_checked", None), ("n2", "not_checked", None),
        ("n3", "not_checked", "not_checked"), ("n4", "not_checked", None),
    ]


def test_the_records_list_every_accepted_note_in_order_read_or_not() -> None:
    board = NoteBoard()
    for text in ("first", "second", "third"):
        board.receive(text, received_at=RECEIVED.received_at, received_during="researcher")
    board.add(fake_reader_note("n1", restatement="more weight on fire-safety standards"))
    board.add(fake_reader_note("n3", kinds=["new_angle", "exclude"], restatement="recycling, leaving out exports"))

    records = note_records(board, None, terminal=False)

    assert [
        (r.received.note_id, r.received.text, r.restatement, r.outcome, r.steering_outcome) for r in records
    ] == [
        ("n1", "first", "more weight on fire-safety standards", "pending", None),
        ("n2", "second", None, "pending", None),
        ("n3", "third", "recycling, leaving out exports", "pending", "pending"),
    ]
    assert [r.outcome for r in note_records(board, None, terminal=True)] == ["not_checked"] * 3


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
    with pytest.raises(ValidationError):
        ReaderNoteResponse(note_id="n1", text="t", outcome="covered", steering_outcome="lost")
    assert ReaderNoteResponse(note_id="n1", text="t", outcome="not_checked").steering_outcome is None
```

`tests/test_api/test_note_route.py` — replace

```python
    assert running["notes"] == [
        {"note_id": "n1", "text": "Pumped hydro", "restatement": "leave out pumped hydro", "outcome": "pending"},
        {"note_id": "n2", "text": "Flow batteries", "restatement": "leave out flow batteries", "outcome": "pending"},
        {"note_id": "n3", "text": "Grid codes", "restatement": "leave out grid codes", "outcome": "pending"},
    ]
```

with

```python
    assert running["notes"] == [
        {"note_id": "n1", "text": "Pumped hydro", "restatement": "leave out pumped hydro", "outcome": "pending",
         "steering_outcome": None},
        {"note_id": "n2", "text": "Flow batteries", "restatement": "leave out flow batteries", "outcome": "pending",
         "steering_outcome": None},
        {"note_id": "n3", "text": "Grid codes", "restatement": "leave out grid codes", "outcome": "pending",
         "steering_outcome": None},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api/test_notes.py -q
.venv\Scripts\python.exe -m pytest tests/test_api/test_note_route.py -q
```

Expected: the first command stops at `Interrupted: 1 error during collection` — `ImportError: cannot import name 'note_steering_outcome' from 'deep_research.api.notes'`; the second ends `1 failed` with the rest passed (`1 failed, 19 passed` on the stand-in export), the failure being `test_the_notes_route_takes_a_note_and_the_status_lists_it_with_its_outcome` (the status has no `steering_outcome` yet).

- [ ] **Step 3: Replace `note_outcome` and `note_records` with §5.6's two functions**

`src/deep_research/api/notes.py` — replace

```python
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, TypeAlias
```

with

```python
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, NamedTuple, TypeAlias
```

`src/deep_research/api/notes.py` — replace

```python
from deep_research.api.clarify import clarity_llm_config
```

with

```python
from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.api.clarify import clarity_llm_config
```

`src/deep_research/api/notes.py` — replace

```python
from deep_research.utils.types import (
    MAX_NOTE_SHORT_CHARS,
    ReaderNote,
```

with

```python
from deep_research.utils.types import (
    MAX_NOTE_SHORT_CHARS,
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
```

`src/deep_research/api/notes.py` (D-touched) — replace the lines from the one starting `def note_outcome(` up to, not including, the one starting `__all__ = [`, with

```python
class NoteRecord(NamedTuple):
    """One accepted note as the session response reports it (notes-progress-report spec §5.6)."""

    received: ReceivedNote
    restatement: str | None
    """The run's reading of the note, ``None`` until it is read."""
    outcome: NoteOutcome
    steering_outcome: NoteOutcome | None
    """A mixed note's steering half (D20); ``None`` for every other note."""


_VERDICT_OUTCOMES: dict[str, NoteOutcome] = {
    "honoured": "covered",
    "ignored_with_evidence": "not_addressed",
    "no_evidence": "not_found",
}


def note_outcome(
    note_id: str, state: ResearchState | None, *, terminal: bool
) -> NoteOutcome:
    """What the run concluded about one note (notes-progress-report spec §5.6, D5, D31).

    A research note — its kinds include ``new_angle``, a mixed note's new_angle
    half included — is decided by its own ``note-{id}`` topic's targets, never
    by the review: ``covered`` once a verified finding answers one of them,
    ``not_found`` once the composition lists one as searched and not found. A
    steering note is decided by the latest review's verdict: ``honoured`` is
    ``covered``, ``ignored_with_evidence`` is ``not_addressed`` (never
    ``covered``: live-briefs Phase 3, O8), ``no_evidence`` is ``not_found``. A
    note a later note replaced is ``replaced``. Anything else — no state, a
    note the state does not hold, no topic, no verdict — is ``pending`` while
    the run goes on, and ``not_checked`` once it has ended (``terminal``): a
    finished session never reports a note ``pending``.
    """
    waiting: NoteOutcome = "not_checked" if terminal else "pending"
    if state is None:
        return waiting
    note = next((held for held in state.reader_notes if held.note_id == note_id), None)
    if note is None:
        return waiting
    if note_id not in {held.note_id for held in active_reader_notes(state.reader_notes)}:
        return "replaced"
    if is_research_note(note):
        return _research_outcome(note_id, state, waiting=waiting)
    return _verdict_outcome(note_id, state, waiting=waiting)


def note_steering_outcome(
    note_id: str,
    state: ResearchState | None,
    *,
    terminal: bool,
    note: ReaderNote | None = None,
) -> NoteOutcome | None:
    """A mixed note's steering half (notes-progress-report spec §5.6, D20), or ``None``.

    ``None`` for every note that is not mixed. For a mixed note: ``pending``
    (``not_checked`` once the run has ended) with no state, ``replaced`` when a
    later note replaced it, and otherwise the latest review's verdict mapped as
    ``note_outcome`` maps a steering note's. ``note`` is the board's reading of
    the note, which says whether it is mixed while no state holds it.
    """
    waiting: NoteOutcome = "not_checked" if terminal else "pending"
    held = (
        None
        if state is None
        else next((item for item in state.reader_notes if item.note_id == note_id), None)
    )
    reading = held if held is not None else note
    if reading is None or not (is_research_note(reading) and has_steering_kind(reading)):
        return None
    if state is None or held is None:
        return waiting
    if note_id not in {item.note_id for item in active_reader_notes(state.reader_notes)}:
        return "replaced"
    return _verdict_outcome(note_id, state, waiting=waiting)


def _research_outcome(
    note_id: str, state: ResearchState, *, waiting: NoteOutcome
) -> NoteOutcome:
    """A research note's result, from its own topic's targets (spec §5.6, D31)."""
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note_id}"
    topic = next((item for item in state.sub_topics if item.coverage_id == coverage_id), None)
    if topic is None:
        return waiting
    targets = {target.target_id for target in topic.evidence_targets}
    answered = set(state.quality.answered_target_ids) if state.quality is not None else set()
    if targets & answered:
        return "covered"
    not_found = state.composition.not_found if state.composition is not None else []
    if any(row.target_id in targets and row.searched for row in not_found):
        return "not_found"
    return waiting


def _verdict_outcome(
    note_id: str, state: ResearchState, *, waiting: NoteOutcome
) -> NoteOutcome:
    """A steering note's result, or a mixed note's steering half's: the latest review's verdict."""
    review = state.report_review
    verdicts = (
        {entry.note_id: entry.status for entry in review.note_dispositions}
        if review is not None
        else {}
    )
    verdict = verdicts.get(note_id)
    return waiting if verdict is None else _VERDICT_OUTCOMES[verdict]


def note_records(
    board: NoteBoard, state: ResearchState | None, *, terminal: bool
) -> list[NoteRecord]:
    """Every accepted note in receipt order: as received, its restatement once read, and both outcomes."""
    records: list[NoteRecord] = []
    for received in board.received():
        interpreted = board.interpreted(received.note_id)
        records.append(
            NoteRecord(
                received=received,
                restatement=interpreted.restatement if interpreted is not None else None,
                outcome=note_outcome(received.note_id, state, terminal=terminal),
                steering_outcome=note_steering_outcome(
                    received.note_id, state, terminal=terminal, note=interpreted
                ),
            )
        )
    return records


```

`src/deep_research/api/notes.py` — replace

```python
    "NoteOutcome",
    "NoteScopeDraft",
```

with

```python
    "NoteOutcome",
    "NoteRecord",
    "NoteScopeDraft",
```

`src/deep_research/api/notes.py` — replace

```python
    "note_received_event",
    "note_records",
    "reader_note",
```

with

```python
    "note_received_event",
    "note_records",
    "note_steering_outcome",
    "reader_note",
```

- [ ] **Step 4: Carry the steering outcome in the session response**

`src/deep_research/api/models.py` (D-touched) — replace the lines from the one starting `    outcome: Literal[` up to, not including, the one starting `class ClarificationQuestionResponse(ApiModel):`, with

```python
    outcome: Literal[
        "covered", "not_found", "not_addressed", "pending", "replaced", "not_checked"
    ]
    steering_outcome: (
        Literal[
            "covered", "not_found", "not_addressed", "pending", "replaced", "not_checked"
        ]
        | None
    ) = None
    """A mixed note's steering half (notes-progress-report spec §5.6, D20): the
    review's verdict on what the note asked besides research, in ``outcome``'s
    words; ``None`` for every other note."""


```

`src/deep_research/api/sessions.py` (D-touched) — replace the lines from the one starting `    state = session.outcome.state if session.outcome is not None else None` up to, not including, the one starting `        "notes_remaining": session.note_board.remaining,`, with

```python
    state = session.outcome.state if session.outcome is not None else None
    # notes-progress-report spec §5.6: a session that has ended never reports a
    # note ``pending``; a note nothing judged reads ``not_checked``.
    terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None
    check = session.check
    return {
        "notes": [
            ReaderNoteResponse(
                note_id=record.received.note_id,
                text=record.received.text,
                restatement=record.restatement,
                outcome=record.outcome,
                steering_outcome=record.steering_outcome,
            )
            for record in note_records(session.note_board, state, terminal=terminal)
        ],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api -q
.venv\Scripts\python.exe -m ruff check --select F src/deep_research/api/notes.py src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/test_notes.py
```

Expected: `235 passed` on the stand-in export (more with Phase D's own API tests) and no failure, then `All checks passed!`.

- [ ] **Step 6: Commit**

The files Step 1's script extended are already staged, so this commit takes them too.

```powershell
git add src/deep_research/api/notes.py src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/test_notes.py tests/test_api/test_note_route.py
git commit -m "feat(api): a research note's outcome from its own topic, a mixed note's steering outcome, never pending once a run ends"
```

### Task 8: Notes that arrive after research — the pass and redraft rules, the note pass's reused topic, and the recursion-limit worst cases

**Files:**
- Modify: `src/deep_research/graph/state.py:28-40` (imports), `:126-136` (`GRAPH_ROUTES` note reasons), `:303-336` (`researched_note_topic_ids`, `notes_due_a_pass`, `notes_due_a_redraft`), `:372-378` (`graph_route`'s docstring)
- Modify: `src/deep_research/graph/nodes.py` — imports, `note_pass_node` (`:1381-1443`)
- Modify: `src/deep_research/graph/__init__.py:98-106`, `:110-160` (export `researched_note_topic_ids`)
- Test: `tests/test_graph/test_note_routing.py`, `tests/test_agents/test_research_note_threads.py`

**Interfaces:**
- Consumes: `is_research_note`, `has_steering_kind`, `note_sub_topic`, `research_notes_without_a_topic` (Task 2); the researcher's dispatcher (Task 6); `note_outcome` (Task 7).
- Produces:
  - `graph/state.py`: `researched_note_topic_ids(state: ResearchState) -> set[str]` (spec §5.1); `notes_due_a_pass(state) -> list[ReaderNote]` and `notes_due_a_redraft(state) -> list[ReaderNote]` exactly as §5.4's code; the route order unchanged; `graph_recursion_limit` unchanged (`graph_recursion_limit(1) == 160`).
  - `graph/nodes.py`: `note_pass_node` appends a `note_sub_topic` only for a due note whose `note-{id}` the state does not hold (reason `reader_note` for a research note, `no_evidence` for a steering note), and sets `extra_pass_target_ids` to the targets of every due note's topic, reused or new.

- [ ] **Step 1: Write the failing tests**

`tests/test_graph/test_note_routing.py` — replace

```python
from deep_research.agents.reader_notes import NOTES_WAIT_S, note_sub_topic
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.researcher import select_sub_topics
```

with

```python
from deep_research.agents.planner import ResearchPlan
from deep_research.agents.reader_notes import (
    NOTES_WAIT_S,
    note_sub_topic,
    research_notes_without_a_topic,
)
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.researcher import ResearcherAgent, select_sub_topics
from deep_research.api.notes import note_outcome
```

`tests/test_graph/test_note_routing.py` — replace

```python
    load_state,
    notes_due_a_pass,
    notes_due_a_redraft,
)
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import HitlConfig
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    NoteDisposition,
    ReaderNote,
    ReportReview,
    ResearchEvent,
    ResearchState,
    ReviewDefect,
)
from tests.graph_fakes import (
    FakeReviewer,
```

with

```python
    load_state,
    notes_due_a_pass,
    notes_due_a_redraft,
    researched_note_topic_ids,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import AgentRuntimeConfig, HitlConfig
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    AcquisitionState,
    NoteDisposition,
    NotFoundTarget,
    ReaderNote,
    ReportComposition,
    ReportQualitySnapshot,
    ReportReview,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ReviewDefect,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, TargetKeyedCompleter
from tests.graph_fakes import (
    FakeAgent,
    FakeReviewer,
```

`tests/test_graph/test_note_routing.py` — replace

```python
    fake_target,
    fake_writer_composition,
    halting_error,
    verified_pass,
)
```

with

```python
    fake_target,
    fake_writer_composition,
    fake_writer_update,
    halting_error,
    verified_pass,
)
from tests.research_fakes import research_tools
from tests.test_agents.test_planner import _planner
```

Append to `tests/test_graph/test_note_routing.py`:

```python


# --- notes-progress-report spec §5.4: the pass and redraft rules (D4, D5, D20, D22) -------


def _completed(coverage_id: str, stop_reason: str) -> ResearchEvent:
    return ResearchEvent(
        event_type="researcher.sub_topic.completed", source="researcher", message="Sub-topic complete.",
        metadata={"coverage_id": coverage_id, "stop_reason": stop_reason},
    )


@pytest.mark.parametrize("review_status", [None, "scored", "incomplete", "provider_failed"])
def test_notes_due_a_pass_research_notes_any_review_status(review_status: str | None) -> None:
    """§5.4, AC5 (D5): a research note with no researched topic owes its one pass whatever the
    review's status — none at all, or a provider failure — and the route takes it before
    review_unavailable; once its topic has done its research it owes none."""
    angle = fake_reader_note("n1", kinds=["new_angle"], reviewed=True)
    review = None if review_status is None else fake_report_review(status=review_status)
    owed = fake_research_state(
        reader_notes=[angle], report_review=review, sub_topics=[fake_sub_topic(targets=[fake_target()])],
    )

    assert notes_due_a_pass(owed) == [angle]
    assert graph_route(owed) == (ROUTE_NOTE_PASS, "note_pass_requested")
    topic = note_sub_topic(angle, priority=2, reason="reader_note")
    researched = owed.model_copy(update={
        "sub_topics": [*owed.sub_topics, topic], "events": [_completed("note-n1", "finished")],
    })
    assert researched_note_topic_ids(researched) == {"note-n1"}
    assert notes_due_a_pass(researched) == []


@pytest.mark.asyncio
async def test_research_notes_never_redraft() -> None:
    """§5.4, AC5, AC7 (D20, D22): only a note with a steering kind buys a redraft. A note whose
    only kind is new_angle never does, whatever the review said of it or whether it read it; a
    mixed note's steering half does — on ignored_with_evidence, or when no review input carried
    it — after the note's pass, and that redraft spends none of the review's own re-run. A mixed
    note whose steering half was judged no_evidence buys no pass: its topic researched it."""
    angle_unread = fake_reader_note("n1", kinds=["new_angle"])
    angle_ignored = fake_reader_note("n2", kinds=["new_angle"], reviewed=True)
    mixed_ignored = fake_reader_note("n3", kinds=["new_angle", "exclude"], reviewed=True)
    mixed_unread = fake_reader_note("n4", kinds=["new_angle", "exclude"])
    mixed_honoured = fake_reader_note("n5", kinds=["new_angle", "scope"], reviewed=True)
    state = _judged(
        angle_unread, angle_ignored, mixed_ignored, mixed_unread, mixed_honoured,
        verdicts={"n2": "ignored_with_evidence", "n3": "ignored_with_evidence", "n5": "honoured"},
        writer_redrafts=MAX_WRITER_REDRAFTS,
    )

    assert [note.note_id for note in notes_due_a_redraft(state)] == ["n3", "n4"]
    assert [note.note_id for note in notes_due_a_pass(state)] == ["n1", "n2", "n3", "n4", "n5"]
    assert graph_route(state) == (ROUTE_NOTE_PASS, "note_pass_requested")
    passed = state.model_copy(update={
        "reader_notes": [note.model_copy(update={"passed": True}) for note in state.reader_notes],
    })
    assert graph_route(passed) == (ROUTE_REDRAFT, "note_redraft_requested")
    hop = load_state(await writer_redraft_node(dump_state(passed)))
    assert [(note.note_id, note.redrafted) for note in hop.reader_notes] == [
        ("n1", False), ("n2", False), ("n3", True), ("n4", True), ("n5", False),
    ]
    assert hop.writer_redrafts == MAX_WRITER_REDRAFTS
    researched = _judged(
        mixed_honoured, verdicts={"n5": "no_evidence"},
        sub_topics=[note_sub_topic(mixed_honoured, priority=2, reason="reader_note")],
        events=[_completed("note-n5", "finished")],
    )
    assert notes_due_a_pass(researched) == []


@pytest.mark.asyncio
async def test_failed_note_thread_owes_one_pass(tracker: Tracker) -> None:
    """AC35 (review I5; review 2, I-1 and M-2): a research note whose thread ended in a
    provider failure, or whose planning-time topic stop left unstarted, owes its one note pass
    unless a finding answered one of its targets; the pass reuses its topic and confines the
    researcher to it; and when that topic has spent its acquisition budget, the pass is refused
    as unfunded and opens no loop, and the note stays passed and reads not_found."""
    angle = fake_reader_note("n1", kinds=["new_angle"], reviewed=True)
    topic = note_sub_topic(angle, priority=2, reason="reader_note")
    failed = fake_research_state(
        reader_notes=[angle], sub_topics=[fake_sub_topic(targets=[fake_target()]), topic],
        report_review=fake_report_review(),
        events=[_completed("topic-01", "finished"), _completed("note-n1", "provider_error")],
    )
    unstarted = failed.model_copy(update={"events": [_completed("topic-01", "finished")]})
    answered = failed.model_copy(update={
        "quality": ReportQualitySnapshot(answered_target_ids=["note-n1-target-01"]),
    })

    for owed in (failed, unstarted):
        assert researched_note_topic_ids(owed) == set()
        assert notes_due_a_pass(owed) == [angle]
        opened = load_state(await note_pass_node(dump_state(owed)))
        assert [sub_topic.coverage_id for sub_topic in opened.sub_topics] == ["topic-01", "note-n1"]
        assert opened.extra_pass_target_ids == ["note-n1-target-01"]
        assert [(note.note_id, note.passed) for note in opened.reader_notes] == [("n1", True)]
        assert notes_due_a_pass(opened) == []
    assert researched_note_topic_ids(answered) == {"note-n1"}
    assert notes_due_a_pass(answered) == []
    assert note_outcome("n1", answered, terminal=True) == "covered"

    opened = load_state(await note_pass_node(dump_state(failed)))
    spent = opened.model_copy(update={
        "acquisition_state_by_target": {"note-n1": AcquisitionState(target_id="note-n1", remaining_calls=0)},
    })
    completer = TargetKeyedCompleter()
    agent = ResearcherAgent(
        provider=completer, tracker=tracker,
        scratchpad=ScratchpadMemory(session_id="session-1", agent_name="researcher", max_entries=20),
        tools=research_tools(tracker), config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )
    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(spent)

    assert [error.error_type for error in outcome.errors] == ["researcher_extra_pass_unfunded"]
    assert completer.react_calls == []
    after = merge_research_state(spent, outcome.state_update).model_copy(update={
        "composition": ReportComposition(question=QUESTION, session_id="session-1", not_found=[
            NotFoundTarget(target_id="note-n1-target-01", question=angle.restatement, searched=True),
        ]),
    })
    assert notes_due_a_pass(after) == []
    assert note_outcome("n1", after, terminal=True) == "not_found"


@pytest.mark.asyncio
async def test_replaced_note_before_topic_gets_none(tracker: Tracker) -> None:
    """§5.8: a research note a later note replaced before it had a topic never gets one — not
    from the planner, not from the researcher's dispatcher, not from a note pass — and the later
    note is judged on its own kinds."""
    angle = fake_reader_note("n1", kinds=["new_angle"])
    later = fake_reader_note("n2", kinds=["exclude"], replaces="n1")
    planner = _planner(tracker, ScriptedCompleter())
    planner._reader_notes = (angle, later)
    plan = ResearchPlan(sub_topics=[fake_sub_topic(targets=[fake_target()])])

    assert planner._note_topics_for(fake_research_state(), plan) == []
    assert research_notes_without_a_topic([angle, later], set()) == []
    state = _judged(
        angle.model_copy(update={"reviewed": True}), later.model_copy(update={"reviewed": True}),
        verdicts={"n2": "no_evidence"},
    )
    assert [note.note_id for note in notes_due_a_pass(state)] == ["n2"]
    opened = load_state(await note_pass_node(dump_state(state)))
    assert [topic.coverage_id for topic in opened.sub_topics] == ["note-n2"]
    assert opened.sub_topics[0].rationale.endswith("and the review found no evidence for it yet.")


# --- AC10: the two worst cases ten notes allow, on the compiled graph ---------------------


class StagedNoteJudge(FakeReviewer):
    """A reviewer double that judges every note its packet carries by how many reviews have
    read it: ``verdicts[k]`` the (k+1)-th time, ``final`` after that."""

    def __init__(self, *, verdicts: Sequence[str], final: str) -> None:
        super().__init__()
        self.verdicts, self.final = list(verdicts), final
        self.seen: Counter[str] = Counter()

    async def review(self, packet: object, *, previous: ReportReview | None = None) -> ReportReview:
        review = await super().review(packet, previous=previous)
        judged: dict[str, str] = {}
        for note in getattr(packet, "reader_notes", []):
            count = self.seen[note.note_id]
            self.seen[note.note_id] += 1
            judged[note.note_id] = self.verdicts[count] if count < len(self.verdicts) else self.final
        return review.model_copy(update={"note_dispositions": [
            NoteDisposition(note_id=note_id, status=verdict) for note_id, verdict in judged.items()  # type: ignore[arg-type]
        ]})


def _writer_with_arrivals(board: NoteBoard, arrivals: list[ReaderNote]) -> FakeAgent:
    """A writer double that, each time it runs with every note so far passed and redrafted,
    lets the next note arrive — received and read while this Writing step runs."""

    def write(state: ResearchState) -> ResearchStateUpdate:
        if arrivals and all(note.passed and note.redrafted for note in state.reader_notes):
            note = arrivals.pop(0)
            board.receive(note.text, received_at=AT, received_during="report_writer")
            board.add(note)
        return fake_writer_update(state)

    return FakeAgent("report_writer", [], update_factory=write)


async def _ten_notes_during_writing(
    tracker: Tracker, kinds: list[str], verdicts: Sequence[str],
) -> ResearchState:
    board = NoteBoard()
    arrivals = [
        fake_reader_note(f"n{number}", kinds=kinds, received_during="report_writer")
        for number in range(1, MAX_NOTES_PER_RUN + 1)
    ]
    agents = fake_research_agents(
        report_writer=_writer_with_arrivals(board, arrivals),
        report_reviewer=StagedNoteJudge(verdicts=verdicts, final="honoured"),
    )
    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )
    assert run.status == "completed"
    return run.state


def _ten_passes_and_ten_redrafts(state: ResearchState) -> None:
    assert not is_halted(state)
    assert len(state.reader_notes) == MAX_NOTES_PER_RUN == 10
    assert all(note.passed and note.redrafted for note in state.reader_notes)
    assert (state.note_passes, state.iteration, state.writer_redrafts) == (10, 0, 0)
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"] * 10
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"] * 10
    assert _reasons(state) == ["note_pass_requested", "note_redraft_requested"] * 10 + ["report_accepted"]
    assert graph_recursion_limit(state.max_extra_passes) == graph_recursion_limit(1) == 160
    supersteps = sum(1 for event in state.events if event.event_type == "graph.node.started")
    assert supersteps < graph_recursion_limit(state.max_extra_passes)


@pytest.mark.asyncio
async def test_recursion_limit_steering_notes_pass_then_redraft(tracker: Tracker) -> None:
    """AC10, case 1 (review I1): ten steering notes, each arriving during a different Writing
    step, each judged no_evidence (it buys its pass) and then ignored_with_evidence (it buys its
    redraft): ten passes and ten redrafts, under the unchanged recursion limit."""
    state = await _ten_notes_during_writing(tracker, ["emphasis"], ["no_evidence", "ignored_with_evidence"])

    _ten_passes_and_ten_redrafts(state)
    assert all(topic.rationale.endswith("no evidence for it yet.") for topic in state.sub_topics[1:])


@pytest.mark.asyncio
async def test_recursion_limit_mixed_notes_during_writing(tracker: Tracker) -> None:
    """AC10, case 2: ten mixed notes, each arriving during a different Writing step, each
    buying its pass (its new_angle half has no topic) and then its redraft (its steering half
    judged ignored_with_evidence): ten passes and ten redrafts, under the unchanged limit."""
    state = await _ten_notes_during_writing(
        tracker, ["new_angle", "exclude"], ["ignored_with_evidence", "ignored_with_evidence"],
    )

    _ten_passes_and_ten_redrafts(state)
    assert [topic.coverage_id for topic in state.sub_topics[1:]] == [f"note-n{number}" for number in range(1, 11)]
    assert all(topic.rationale == "The reader asked for this in a note." for topic in state.sub_topics[1:])
```

Append to `tests/test_agents/test_research_note_threads.py`:

```python


@pytest.mark.asyncio
async def test_note_after_window_owes_pass(tracker: Tracker, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: a note read only after the research window closed gets no thread in that run; once
    it is read, the next node takes it in, and after the review notes_due_a_pass returns it."""
    monkeypatch.setattr("deep_research.agents.researcher.NOTES_WAIT_S", 0.05)
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")]})
    node = agent_node(_researcher(tracker, completer))
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            researched = load_state(await node(dump_state(_state(_topic(1)))))
        board.add(fake_reader_note("n1", kinds=["new_angle"]))
        reviewed = merge_research_state(researched, {
            "reader_notes": with_board_notes(researched.reader_notes, board.snapshot()),
            "report_review": fake_report_review(),
        })

    assert [topic.coverage_id for topic in researched.sub_topics] == ["topic-01"]
    assert [note.note_id for note in notes_due_a_pass(reviewed)] == ["n1"]
    assert graph_route(reviewed) == (ROUTE_NOTE_PASS, "note_pass_requested")
```

`tests/test_agents/test_research_note_threads.py` — replace

```python
from deep_research.graph.state import dump_state, load_state
```

with

```python
from deep_research.graph.state import ROUTE_NOTE_PASS, dump_state, graph_route, load_state, notes_due_a_pass
```

`tests/test_agents/test_research_note_threads.py` — replace

```python
from deep_research.utils.types import ResearchEvent, ResearchState, SubTopic
from tests.agent_fakes import TargetKeyedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_sub_topic, fake_target
```

with

```python
from deep_research.utils.types import ResearchEvent, ResearchState, SubTopic, merge_research_state, with_board_notes
from tests.agent_fakes import TargetKeyedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_report_review, fake_sub_topic, fake_target
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_note_routing.py -q
.venv\Scripts\python.exe -m pytest tests/test_agents/test_research_note_threads.py -q
```

Expected: the first command stops at `Interrupted: 1 error during collection` — `ImportError: cannot import name 'researched_note_topic_ids' from 'deep_research.graph.state'`; the second ends `1 failed, 7 passed`, the failure being `test_note_after_window_owes_pass` (a research note with no verdict is not yet due a pass).

- [ ] **Step 3: The pass and redraft rules**

`src/deep_research/graph/state.py` — replace

```python
from deep_research.agents.report_reviewer import semantic_review_passes
```

with

```python
from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.agents.report_reviewer import semantic_review_passes
```

`src/deep_research/graph/state.py` — replace

```python
    "note_pass_requested": (
        "The review found no evidence for a reader note that has not had its "
        "one targeted research pass; the researcher runs for the notes that "
        "owe one, outside the extra-pass budget."
    ),
    "note_redraft_requested": (
        "The review found a reader note the report ignores although its "
        "findings bear on it, or a note arrived after the review input was "
        "built, and that note has not had its one redraft; the writer drafts "
        "again with the reader's notes."
    ),
```

with

```python
    "note_pass_requested": (
        "A reader note owes its one targeted research pass: a research note "
        "with no researched topic of its own, whatever the review said, or a "
        "steering note the review found no evidence for; the researcher runs "
        "for the notes that owe one, outside the extra-pass budget."
    ),
    "note_redraft_requested": (
        "The review found a reader note's steering ignored although its "
        "findings bear on it, or a note with a steering kind arrived after the "
        "review input was built, and that note has not had its one redraft; "
        "the writer drafts again with the reader's notes."
    ),
```

`src/deep_research/graph/state.py` — replace

```python
def notes_due_a_pass(state: ResearchState) -> list[ReaderNote]:
    """Active notes the review found no evidence for, not yet passed (D11)."""
    verdicts = note_dispositions(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if verdicts.get(note.note_id) == "no_evidence" and not note.passed
    ]


def notes_due_a_redraft(state: ResearchState) -> list[ReaderNote]:
    """Active notes owed their one redraft (live-briefs spec §4.6).

    A note the report ignores although its findings bear on it, or a note no
    review input carried because it arrived after the input was built.
    """
    verdicts = note_dispositions(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if not note.redrafted
        and (
            verdicts.get(note.note_id) == "ignored_with_evidence"
            or not note.reviewed
        )
    ]
```

with

```python
def researched_note_topic_ids(state: ResearchState) -> set[str]:
    """The reader-note topics (``note-…``) that have done their research (spec §5.1).

    A topic is researched when its latest ``researcher.sub_topic.completed``
    event ended with any stop reason but ``provider_error``, or when a
    verified finding answers one of its targets (``state.quality``; no quality
    snapshot counts as none answered). A topic with no completed event — one
    ``stop`` left unstarted — is not researched. So a note whose thread failed,
    or never started, still owes its one note pass, unless a finding already
    answered it (D31; review I5; review 2, I-1).
    """
    latest: dict[str, str] = {}
    for event in state.events:
        if event.event_type != "researcher.sub_topic.completed":
            continue
        coverage_id = event.metadata.get("coverage_id")
        stop_reason = event.metadata.get("stop_reason")
        if isinstance(coverage_id, str) and isinstance(stop_reason, str):
            latest[coverage_id] = stop_reason
    answered = (
        set(state.quality.answered_target_ids) if state.quality is not None else set()
    )
    return {
        topic.coverage_id
        for topic in state.sub_topics
        if topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX)
        and (
            latest.get(topic.coverage_id, "provider_error") != "provider_error"
            or any(target.target_id in answered for target in topic.evidence_targets)
        )
    }


def notes_due_a_pass(state: ResearchState) -> list[ReaderNote]:
    """Active notes owed their one targeted pass, not yet passed (D11; spec §5.4, D5).

    A research note — its kinds include ``new_angle`` — owes it while it has
    no researched topic of its own, whatever the review said or whether one
    was made; a steering note owes it when the review found no evidence for
    it. A mixed note's steering half judged ``no_evidence`` buys no pass: its
    topic already researched the note.
    """
    verdicts = note_dispositions(state)
    researched = researched_note_topic_ids(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if not note.passed
        and (
            (
                is_research_note(note)
                and f"{NOTE_COVERAGE_PREFIX}{note.note_id}" not in researched
            )
            or (
                not is_research_note(note)
                and verdicts.get(note.note_id) == "no_evidence"
            )
        )
    ]


def notes_due_a_redraft(state: ResearchState) -> list[ReaderNote]:
    """Active notes owed their one redraft (live-briefs spec §4.6; spec §5.4, D20).

    Only a note with a steering kind buys one — a steering note, or a mixed
    note's steering half: one the report ignores although its findings bear
    on it, or one no review input carried because it arrived after the input
    was built. A note whose only kind is ``new_angle`` never does.
    """
    verdicts = note_dispositions(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if has_steering_kind(note)
        and not note.redrafted
        and (
            verdicts.get(note.note_id) == "ignored_with_evidence"
            or not note.reviewed
        )
    ]
```

`src/deep_research/graph/state.py` — replace

```python
    The reader's notes are read right after a halt (live-briefs spec §4.6,
    D11): first a note the review found no evidence for buys its one targeted
    pass (``ROUTE_NOTE_PASS``), then a note the report ignores, or one no
    review has read yet, buys its one redraft (``ROUTE_REDRAFT`` with the
    reason ``note_redraft_requested``, which spends no writer re-run of the
    review's own). Each note is flagged when its route is taken, so neither
    check can loop; with no note due, every rule below reads as it always did.
    """
```

with

```python
    The reader's notes are read right after a halt (live-briefs spec §4.6,
    D11; notes-progress-report spec §5.4): first a note owed its one targeted
    pass buys it (``ROUTE_NOTE_PASS``) — a research note with no researched
    topic, whatever the review's status, or a steering note the review found
    no evidence for — then a note with a steering kind the report ignores, or
    one no review has read yet, buys its one redraft (``ROUTE_REDRAFT`` with
    the reason ``note_redraft_requested``, which spends no writer re-run of
    the review's own). Each note is flagged when its route is taken, so
    neither check can loop; with no note due, every rule below reads as it
    always did.
    """
```

- [ ] **Step 4: The note pass reuses a note's topic**

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.agents.reader_notes import (
    NOTES_WAIT_S,
    board_notes,
    note_sub_topic,
    notes_settled,
)
```

with

```python
from deep_research.agents.reader_notes import (
    NOTES_WAIT_S,
    board_notes,
    is_research_note,
    note_sub_topic,
    notes_settled,
)
```

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.utils.types import (
    Finding,
    ReportComposition,
```

with

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    Finding,
    ReportComposition,
```

`src/deep_research/graph/nodes.py` — replace

```python
    ResearchStateUpdate,
    active_reader_notes,
    advance_research_iteration,
```

with

```python
    ResearchStateUpdate,
    SubTopic,
    active_reader_notes,
    advance_research_iteration,
```

`src/deep_research/graph/nodes.py` — replace

```python
    """Open the one targeted pass the uncovered reader notes buy (spec §4.6, D11).

    Its own hop, as ``extra_pass`` is, because the route cannot write: it
    appends one sub-topic per note the review found no evidence for, confines
    the researcher to those sub-topics' targets exactly as an extra pass is
    confined (``extra_pass_target_ids``), flags each note ``passed`` and counts
    the pass in ``note_passes`` — never in ``iteration``, so the extra-pass
    budget is untouched. A run that arrives with no note due records
    ``graph_invalid_route`` rather than researching nothing.
    """
```

with

```python
    """Open the one targeted pass the reader notes that owe one buy (spec §4.6, D11).

    Its own hop, as ``extra_pass`` is, because the route cannot write: it
    confines the researcher to the due notes' topics' targets exactly as an
    extra pass is confined (``extra_pass_target_ids``), flags each note
    ``passed`` and counts the pass in ``note_passes`` — never in
    ``iteration``, so the extra-pass budget is untouched. A note whose
    ``note-{id}`` topic the run already holds — a research note whose thread
    failed or never started (notes-progress-report spec §5.3, §5.4) — reuses
    it; every other due note gets its topic appended. A run that arrives with
    no note due records ``graph_invalid_route`` rather than researching
    nothing.
    """
```

`src/deep_research/graph/nodes.py` — replace

```python
    priority = max((topic.priority for topic in state.sub_topics), default=0) + 1
    topics = [note_sub_topic(note, priority=priority, reason="no_evidence") for note in due]
    targets = [
        target.target_id for topic in topics for target in topic.evidence_targets
    ]
```

with

```python
    priority = max((topic.priority for topic in state.sub_topics), default=0) + 1
    held = {topic.coverage_id: topic for topic in state.sub_topics}
    topics: list[SubTopic] = []
    added: list[SubTopic] = []
    for note in due:
        topic = held.get(f"{NOTE_COVERAGE_PREFIX}{note.note_id}")
        if topic is None:
            topic = note_sub_topic(
                note,
                priority=priority,
                reason="reader_note" if is_research_note(note) else "no_evidence",
            )
            added.append(topic)
        topics.append(topic)
    targets = [
        target.target_id for topic in topics for target in topic.evidence_targets
    ]
```

`src/deep_research/graph/nodes.py` — replace

```python
        {
            "sub_topics": topics,
            "extra_pass_target_ids": targets,
```

with

```python
        {
            "sub_topics": added,
            "extra_pass_target_ids": targets,
```

- [ ] **Step 5: Export the new reader**

`src/deep_research/graph/__init__.py` — replace

```python
    notes_due_a_pass,
    notes_due_a_redraft,
```

with

```python
    notes_due_a_pass,
    notes_due_a_redraft,
    researched_note_topic_ids,
```

`src/deep_research/graph/__init__.py` — replace

```python
    "notes_due_a_redraft",
```

with

```python
    "notes_due_a_redraft",
    "researched_note_topic_ids",
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph tests/test_agents/test_research_note_threads.py -q
.venv\Scripts\python.exe -m ruff check --select F src/deep_research/graph tests/test_graph/test_note_routing.py tests/test_agents/test_research_note_threads.py
```

Expected: `225 passed` and no failure, then `All checks passed!`.

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/graph/state.py src/deep_research/graph/nodes.py src/deep_research/graph/__init__.py tests/test_graph/test_note_routing.py tests/test_agents/test_research_note_threads.py
git commit -m "feat(graph): research notes owe their pass whatever the review said, only steering notes redraft, a failed thread's topic is reused"
```

### Task 9: After a note pass the writer drafts only the notes' parts and the bottom line

**Files:**
- Modify: `src/deep_research/agents/report_writer.py` — imports (`:84-86`), `ReportWriterTask` (`:507-509`), after `_is_redraft_hop` (`:1013-1035`), `compose_written_report` (`:2942-2949`, `:3003-3005`), `build_task` (`:3216-3243`)
- Modify: `tests/test_evaluation/test_config.py` (the `report_writer` pin)
- Test: `tests/test_agents/test_report_writer.py`, `tests/test_agents/test_reader_notes_writing.py`

**Interfaces:**
- Consumes: the `graph.note_pass.started` marker (`note_ids` metadata, `graph/events.py`); `writer_redraft_node`'s `graph.note_redraft.requested` marker (unchanged).
- Produces:
  - `ReportWriterTask.note_pass_coverage_ids: list[str]` (default `[]`).
  - `report_writer._note_pass_coverage_ids(state: ResearchState) -> list[str]`: `[f"note-{id}" for id in marker.metadata["note_ids"]]` when the latest of the four loop markers is `graph.note_pass.started` and `state.composition.iteration == state.iteration`; otherwise `[]`.
  - `build_task` passes `previous=state.composition` when it is a redraft hop or `note_pass_coverage_ids` is non-empty; `defects` stays the redraft hop's only.
  - `compose_written_report`: with `note_pass_coverage_ids` non-empty, `redraft_this = placement.coverage_id in note_pass_coverage_ids`, `defects_here = []`; `is_redraft` stays `bool(task.defects)`, so the bottom line is drafted fresh. `_is_redraft_hop` is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_report_writer.py`:

```python


@pytest.mark.asyncio
async def test_writer_carries_parts_after_note_pass(checker, tracker: Tracker, tmp_path: Path) -> None:
    """notes-progress-report spec §5.4, D4, AC6: after a note pass the writer drafts the notes'
    own parts, any part with no previous section (P2-1) and the bottom line — fresh, with no
    defect fed back — and carries every other part over unchanged, with its verdicts."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    tn = make_target("note-n1-target-01", coverage_id="note-n1", required=True,
                     question="How much battery capacity was recycled in 2024?")
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    fn = _checked("https://a.test/3", "3 GW were recycled in 2024.", "3", "GW", organisation=EIA,
                 target_ids=["note-n1-target-01"])
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2]),
              _topic("note-n1", "Your note: how much was recycled", [tn], priority=2)]
    previous_statement = ReportStatement(statement_id="S001", text="10.4 GW in 2024.",
                                         finding_ids=[finding_fingerprint(f1)], target_ids=["topic-01-target-01"])
    previous_section = ReportSection(title="First", coverage_id="topic-01",
                                     points=[ReportPointFor("10.4 GW in 2024.", previous_statement)])
    from deep_research.utils.types import ReportComposition
    previous = ReportComposition(question="Q?", session_id="s1", sections=[previous_section], summary=[],
                                 sub_topics=topics[:2], statement_verdicts={"S001": "corrected"})
    marker = ResearchEvent(event_type="graph.note_pass.started", source="graph", message="Note pass started.",
                           metadata={"iteration": 0, "note_passes": 1, "note_ids": ["n1"],
                                     "targets": ["note-n1-target-01"]})
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2, fn], composition=previous,
                          report_review=_scored_review([]), events=[marker])
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)
    label_by_url = {finding.source_url: label for label, finding in task.registry}
    calls: list[str] = []

    def route(messages, schema):
        body = messages[-1].content
        if schema.__name__ == "BottomLineDraft":
            calls.append("bottom_line")
            # A kept sentence, so no fallback moves a carried point out of its section.
            return BottomLineDraft(sentences=[WriterPointDraft(
                text="According to the source, 3 GW were recycled in 2024.",
                finding_labels=[label_by_url["https://a.test/3"]])])
        if "3 GW were recycled in 2024." in body:
            calls.append("note-n1")
            return SectionDraft(title="Your note: how much was recycled", points=[WriterPointDraft(
                text="According to the source, 3 GW were recycled in 2024.",
                finding_labels=[label_by_url["https://a.test/3"]])])
        if "5 GW in 2025." in body:
            calls.append("topic-02")
            return SectionDraft(title="Second", points=[WriterPointDraft(
                text="According to the source, 5 GW in 2025.", finding_labels=[label_by_url["https://a.test/2"]])])
        calls.append("topic-01")
        return SectionDraft(title="First", points=[])

    completer = ScriptedCompleter(outputs=[route, route, route])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert (task.note_pass_coverage_ids, task.defects, task.previous) == (["note-n1"], [], previous)
    assert sorted(calls) == ["bottom_line", "note-n1", "topic-02"]
    statuses = {part.coverage_id: part.status for part in composition.parts}
    assert statuses == {"topic-01": "carried_over", "topic-02": "written", "note-n1": "written"}
    [carried] = [section for section in composition.sections if section.coverage_id == "topic-01"]
    assert (carried.title, [point.text for point in carried.points]) == ("First", ["10.4 GW in 2024."])
    assert carried.points[0].statement.finding_ids == [finding_fingerprint(f1)]
    assert composition.statement_verdicts[carried.points[0].statement.statement_id] == "corrected"
    from deep_research.graph.nodes import _arrived_via_redraft_hop
    assert _arrived_via_redraft_hop([marker]) is False  # the review after a note pass is a full one
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
from deep_research.agents.reader_notes import note_sub_topic
from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import _is_redraft_hop
from deep_research.utils.types import ResearchEvent
from tests.graph_fakes import (
    fake_reader_note,
    fake_research_state,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    verified_pass,
)
```

with

```python
from pathlib import Path

from deep_research.agents.reader_notes import note_sub_topic
from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import ReportWriterTask, _is_redraft_hop
from deep_research.observability import Tracker
from deep_research.utils.types import ResearchEvent
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    fake_reader_note,
    fake_research_state,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    verified_pass,
)
from tests.research_fakes import report_writer_tools
from tests.test_agents.test_report_writer import _writer
```

`tests/test_agents/test_reader_notes_writing.py` — replace

```python
def _event(event_type: str) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="A loop marker.")
```

with

```python
def _event(event_type: str, metadata: dict[str, object] | None = None) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="A loop marker.", metadata=metadata or {})


def test_note_redraft_unchanged(tracker: Tracker, tmp_path: Path) -> None:
    """notes-progress-report spec §5.4, AC7 (D22): a note redraft — a steering note, or a mixed
    note's steering half, judged ignored_with_evidence — still drafts every part afresh: the
    writer carries no part over and is fed no defect. Only a note pass, when it is the latest
    loop and the composition is this iteration's, carries parts (AC6)."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    base = fake_research_state(sub_topics=[fake_sub_topic(targets=[fake_target()])])
    drafted = base.model_copy(update={"composition": fake_writer_composition(base)})
    note_pass = ("graph.note_pass.started", {"note_ids": ["n1"]})
    note_redraft = ("graph.note_redraft.requested", {"note_ids": ["n2"]})

    def task_after(*markers: tuple[str, dict[str, object]]) -> ReportWriterTask:
        return agent.build_task(drafted.model_copy(update={"events": [_event(*marker) for marker in markers]}))

    after_redraft = task_after(note_pass, note_redraft)
    assert (after_redraft.note_pass_coverage_ids, after_redraft.previous, after_redraft.defects) == ([], None, [])
    after_pass = task_after(note_redraft, note_pass)
    assert (after_pass.note_pass_coverage_ids, after_pass.previous) == (["note-n1"], drafted.composition)
    reviewers_own = task_after(note_pass, ("graph.report.redraft_requested", {}))
    assert (reviewers_own.note_pass_coverage_ids, reviewers_own.previous) == ([], drafted.composition)
    stale = drafted.model_copy(update={"iteration": 1, "events": [_event(*note_pass)]})
    assert (agent.build_task(stale).note_pass_coverage_ids, agent.build_task(stale).previous) == ([], None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_writer.py::test_writer_carries_parts_after_note_pass tests/test_agents/test_reader_notes_writing.py -q
```

Expected: `2 failed, 4 passed` — `test_writer_carries_parts_after_note_pass` with `AssertionError: no scripted response left for BottomLineDraft` (every part is still drafted, so the scripted replies run out before the bottom line) and `test_note_redraft_unchanged` with `AttributeError: 'ReportWriterTask' object has no attribute 'note_pass_coverage_ids'`.

- [ ] **Step 3: Carry every part but the notes' own after a note pass**

`src/deep_research/agents/report_writer.py` — replace

```python
from deep_research.utils.types import (
    AcquisitionState,
    AnswerKind,
```

with

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    AcquisitionState,
    AnswerKind,
```

`src/deep_research/agents/report_writer.py` — replace

```python
    previous: ReportComposition | None = None
    """The prior pass's composition, read only on a redraft: a part with no
    routed defect is carried over from here unchanged (spec §6.9)."""
```

with

```python
    previous: ReportComposition | None = None
    """The prior pass's composition, read only on a redraft — a part with no
    routed defect is carried over from here unchanged (spec §6.9) — and after
    a note pass, when every part but the notes' own is (notes-progress-report
    spec §5.4)."""
    note_pass_coverage_ids: list[str] = Field(default_factory=list)
    """The reader notes' own parts a note pass researched (notes-progress-report
    spec §5.4, D4): after a note pass only these parts — and a part with no
    previous section (P2-1) — and the bottom line are drafted, and every other
    part is carried over from ``previous`` unchanged; ``[]`` otherwise."""
```

`src/deep_research/agents/report_writer.py` — replace

```python
        if event.event_type in ("graph.note_pass.started", "graph.note_redraft.requested"):
            return False
    return True
```

with

```python
        if event.event_type in ("graph.note_pass.started", "graph.note_redraft.requested"):
            return False
    return True


# The loop markers a writer call can follow: the review's own redraft, an extra
# pass, and a reader note's two loops (spec §6.9; live-briefs spec §4.6).
_LOOP_MARKERS = frozenset(
    {
        "graph.report.redraft_requested",
        "graph.extra_pass.started",
        "graph.note_pass.started",
        "graph.note_redraft.requested",
    }
)


def _note_pass_coverage_ids(state: ResearchState) -> list[str]:
    """The notes' own parts a note pass researched, when one is what this draft follows.

    notes-progress-report spec §5.4 (D4): read from the latest loop marker. Only
    when it is ``graph.note_pass.started`` and the composition on hand is this
    iteration's does this draft carry every other part over; after any other
    loop, or with no composition to carry, ``[]``.
    """
    if state.composition is None or state.composition.iteration != state.iteration:
        return []
    marker = next(
        (event for event in reversed(state.events) if event.event_type in _LOOP_MARKERS),
        None,
    )
    if marker is None or marker.event_type != "graph.note_pass.started":
        return []
    note_ids = marker.metadata.get("note_ids")
    if not isinstance(note_ids, list):
        return []
    return [
        f"{NOTE_COVERAGE_PREFIX}{note_id}"
        for note_id in note_ids
        if isinstance(note_id, str)
    ]
```

`src/deep_research/agents/report_writer.py` — replace

```python
    the checked section statements (spec §6). A redraft re-asks only the
    parts a material defect names (§6.9); every other part is carried over
    unchanged. ``batch_size``/``concurrency`` are the Statement Check's
```

with

```python
    the checked section statements (spec §6). A redraft re-asks only the
    parts a material defect names (§6.9), and a draft after a note pass only
    the notes' own parts (notes-progress-report spec §5.4); every other part
    is carried over unchanged. ``batch_size``/``concurrency`` are the Statement Check's
```

`src/deep_research/agents/report_writer.py` — replace

```python
        previous_section = previous_sections_by_coverage.get(placement.coverage_id)
        if not is_redraft:
            redraft_this, defects_here = True, []
```

with

```python
        previous_section = previous_sections_by_coverage.get(placement.coverage_id)
        if task.note_pass_coverage_ids:
            # notes-progress-report spec §5.4 (D4): after a note pass only the
            # notes' own parts are drafted; every other part is carried over
            # unchanged, and one with no previous section is still drafted
            # (P2-1, ``_run_part``).
            redraft_this = placement.coverage_id in task.note_pass_coverage_ids
            defects_here = []
        elif not is_redraft:
            redraft_this, defects_here = True, []
```

`src/deep_research/agents/report_writer.py` — replace

```python
            else self.config.report_target_words
        )
        return ReportWriterTask(
```

with

```python
            else self.config.report_target_words
        )
        # notes-progress-report spec §5.4 (D4): after a note pass only the
        # notes' own parts and the bottom line are drafted; the rest is carried.
        note_pass_coverage_ids = _note_pass_coverage_ids(state)
        return ReportWriterTask(
```

`src/deep_research/agents/report_writer.py` — replace

```python
            defects=material_defects(state.report_review) if _is_redraft_hop(state) else [],
            previous=state.composition if _is_redraft_hop(state) else None,
```

with

```python
            defects=material_defects(state.report_review) if _is_redraft_hop(state) else [],
            previous=(
                state.composition
                if _is_redraft_hop(state) or note_pass_coverage_ids
                else None
            ),
            note_pass_coverage_ids=note_pass_coverage_ids,
```

- [ ] **Step 4: Run the tests to verify they pass, and read the moved fingerprint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_writer.py tests/test_agents/test_reader_notes_writing.py -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print(f('report_writer'))"
```

Expected: `158 passed`, then `a27544eee344` when B-pin(report_writer) is `6e1aedc2888e` (so Task 4 wrote `cafa5ba3d613`); with another B-pin, another value, which Step 5 pins under the B-pin rule.

- [ ] **Step 5: Re-pin the writer**

The block shows the unmoved case (the B-pin rule, Conventions). Its old line holds the value Task 4 wrote, `cafa5ba3d613` in the unmoved case. The new pin line and the comment's "Moved `…` -> `…`" carry that value and the value Step 4 printed. When B-pin(report_writer) is `6e1aedc2888e` and Step 4 printed anything but `a27544eee344`, a block of this task was not applied byte-exactly. Find it, apply it again exactly, and run Step 4 again; never pin a printed value without finding the cause.

`tests/test_evaluation/test_config.py` — replace

```python
    "report_writer": "cafa5ba3d613",
```

with

```python
    # notes-progress-report Phase A (spec §5.4, 2026-09-30): after a note
    # pass the writer drafts only the notes' own parts and the bottom line,
    # and carries every other part over. Without notes every request is
    # byte-identical; agents.prompts was untouched. Moved `cafa5ba3d613` ->
    # `a27544eee344`.
    "report_writer": "a27544eee344",
```

- [ ] **Step 6: Run the pins and the replay suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_evaluation/test_config.py tests/test_graph/test_reader_notes_replay.py tests/test_e2e_evaluation -q
```

Expected: `167 passed` and no failure.

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/agents/report_writer.py tests/test_agents/test_report_writer.py tests/test_agents/test_reader_notes_writing.py tests/test_evaluation/test_config.py
git commit -m "feat(writer): after a note pass only the notes' parts and the bottom line are drafted"
```

### Task 10: The page — a research note's acknowledgement, its thread, a mixed note's caption; the design record

**Files:**
- Modify: `web/lib/run-state.ts:48-51` (`NoteState`), `:207-209` (`researcher.sub_topic.started`), `:342-355` (the two note handlers)
- Modify: `web/lib/notes.ts:20-48` (`RESEARCH_WHERE`, `RESEARCH_NOW`, `isResearchNote`, `whereFor`, `ackFor`, `visibleAcks`), before `notesLeft` (`noteCaption`)
- Modify: `web/lib/briefs.ts:59` (the active row's acknowledgements are worded for the active row, review P3-5)
- Modify: `web/lib/api.ts:45` (`ReaderNoteRecord`)
- Modify: `web/components/ReportBody.tsx:8-9`, `:127`
- Modify: `web/e2e/notes.spec.ts:108-111` (the status the API now answers)
- Modify: `docs/design/DESIGN.md:218-224` (§3, the report's note captions), `:878-888` (§3.5); `docs/design/api-gaps.md:127` (3.9's outcome word, only if Phase D left it)
- Test: `web/test/notes.test.ts`, `web/test/run-state.test.ts`, `web/test/briefs.test.ts`, `web/test/components/reader-notes.test.tsx`

**Interfaces:**
- Consumes: `researcher.sub_topic.started.metadata.note_id` (Task 6); `planner.planning.completed`'s note entries (Task 5); `session.note.interpreted.metadata.kinds` (unchanged since live-briefs Phase 3); `ReaderNoteResponse.steering_outcome` (Task 7); Phase D's `OUTCOME_TEXT.not_checked = "not checked"` and `OUTCOME_TEXT.pending = "not checked yet"`.
- Produces (Phase B's Planning note slots and Reviewing note rows read these, §4 item 5):
  - `NoteState.kinds: string[]` (from `session.note.interpreted.metadata.kinds`, strings only; `[]` until read) and `NoteState.threadStarted: boolean` (set by a `researcher.sub_topic.started` whose `note_id` names the note).
  - `notes.ts`: `RESEARCH_WHERE: Partial<Record<NodeId, string>>`, `RESEARCH_NOW`, `isResearchNote(note: NoteState): boolean`, `noteCaption(note: Pick<ReaderNoteRecord, "outcome" | "steering_outcome">): string`. `ackFor(note, notes, active: NodeId | null = null)` picks the research table for any note whose kinds include `new_angle`, keyed like `WHERE` on the step that read the note. It says `RESEARCH_NOW` whenever `active` is `"researcher"` and the note's own thread has started, which includes a note pass researching a note read after Researching (review P3-5). `visibleAcks(notes, active: NodeId | null = null)` passes `active` on, and `rowBrief` passes `run.active`.
  - `ReaderNoteRecord.steering_outcome?: ReaderNoteOutcome | null`.

- [ ] **Step 1: Write the failing tests**

`web/test/notes.test.ts` — replace

```ts
import { NOTE_LIMIT, OUTCOME_TEXT, WHERE, ackFor, earlierNotesText, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
```

with

```ts
import { NOTE_LIMIT, OUTCOME_TEXT, RESEARCH_NOW, RESEARCH_WHERE, WHERE, ackFor, earlierNotesText, noteCaption, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
```

Append to `web/test/notes.test.ts`:

```ts

describe("a research note's acknowledgement and thread (notes-progress-report spec §5.7, AC12)", () => {
  const angle = (id: string, restatement: string, kinds: string[] = ["new_angle"]) => interpreted(id, restatement, { kinds });
  const ownThread = (id: string) => ev("researcher.sub_topic.started", { coverage_id: "note-" + id, note_id: id, sub_topic: "Your note: pastries in the cafe", index: 2 });
  const restOf = (run: RunState) => ackFor(run.notes[0], run.notes, run.active).rest;
  const upTo = (node: NodeId): RunEvent[] => {
    const order: NodeId[] = ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"];
    return order.slice(0, order.indexOf(node)).flatMap((done) => [started(done), completed(done)]).concat(started(node));
  };

  it("names the research table for each step the note can be read in", () => {
    expect(RESEARCH_WHERE).toEqual({
      planner: ", as its own topic",
      researcher: ", as its own topic",
      source_evaluator: ", researched as its own topic after this draft is reviewed",
      evidence_verifier: ", researched as its own topic after this draft is reviewed",
      report_writer: ", researched as its own topic after this draft is reviewed",
      report_reviewer: ", researched as its own topic next",
    });
    expect(RESEARCH_NOW).toBe(", researching it as its own topic now");
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as NodeId[]) {
      expect(restOf(play([...upTo(node), received("n1", "Pastries too"), angle("n1", "pastries in the cafe")]))).toBe(RESEARCH_WHERE[node]);
    }
    expect(restOf(play([...toReviewing, received("n1", "Pastries too"), angle("n1", "pastries in the cafe")]))).toBe(", researched as its own topic next");
  });

  it("says 'now' only once the note's own thread has started, and lists that thread in the checklist", () => {
    const run = play([started("planner"), ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [{ coverage_id: "topic-01", title: "Grid storage costs" }] }),
      completed("planner"), started("researcher"), received("n1", "Pastries too"), angle("n1", "pastries in the cafe")]);
    expect(run.notes[0]).toMatchObject({ kinds: ["new_angle"], threadStarted: false });
    expect(restOf(run)).toBe(", as its own topic");
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Grid storage costs", index: 1 }));
    expect(restOf(run)).toBe(", as its own topic");
    applyEvent(run, ownThread("n1"));
    expect(run.notes[0].threadStarted).toBe(true);
    expect(restOf(run)).toBe(", researching it as its own topic now");
    expect(run.topics.map((t) => [t.coverageId, t.title, t.state])).toEqual([
      ["topic-01", "Grid storage costs", "running"], ["note-n1", "Your note: pastries in the cafe", "running"],
    ]);
  });

  it("says 'now' while Researching runs the note's own topic, whichever step read the note (review P3-5)", () => {
    const run = play([...upTo("report_writer"), received("n1", "Pastries too"), angle("n1", "pastries in the cafe")]);
    expect(restOf(run)).toBe(", researched as its own topic after this draft is reviewed");
    for (const e of [completed("report_writer"), started("report_reviewer"),
      ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0 }), started("note_pass"),
      ev("graph.note_pass.started", { iteration: 0, note_passes: 1, note_ids: ["n1"], targets: ["note-n1-target-01"] }),
      completed("note_pass"), started("researcher")]) applyEvent(run, e);
    expect(run.active).toBe("researcher");
    expect(restOf(run)).toBe(", researched as its own topic after this draft is reviewed");
    applyEvent(run, ownThread("n1"));
    expect(restOf(run)).toBe(RESEARCH_NOW);
    expect(visibleAcks(run.notes, run.active).acks[0].rest).toBe(RESEARCH_NOW);
    expect(rowBrief(run, "researcher", "active").acks.map((a) => a.rest)).toEqual([RESEARCH_NOW]);
    applyEvent(run, completed("researcher"));
    expect(restOf(run)).toBe(", researched as its own topic after this draft is reviewed");
  });

  it("acknowledges a mixed note as a research note, and keeps a steering note's words", () => {
    const run = play([started("planner"), completed("planner"), started("researcher"),
      received("n1", "a"), angle("n1", "pastries, nothing closed", ["new_angle", "exclude"]),
      received("n2", "b"), interpreted("n2", "leave out closed cafes", { kinds: ["exclude"] }), ownThread("n1")]);
    expect(run.notes.map((n) => [n.kinds, n.threadStarted])).toEqual([[["new_angle", "exclude"], true], [["exclude"], false]]);
    expect(ackFor(run.notes[0], run.notes, run.active).rest).toBe(", researching it as its own topic now");
    expect(ackFor(run.notes[1], run.notes, run.active).rest).toBe(", from each topic's next search");
  });

  it("captions a mixed note with both of its results, and every other note with its one", () => {
    expect(noteCaption({ outcome: "covered", steering_outcome: "not_addressed" })).toBe("covered; the rest of your note: not addressed in the report");
    expect(noteCaption({ outcome: "not_checked", steering_outcome: "not_checked" })).toBe("not checked; the rest of your note: not checked");
    expect(noteCaption({ outcome: "not_checked", steering_outcome: null })).toBe(OUTCOME_TEXT.not_checked);
    expect(noteCaption({ outcome: "pending" })).toBe("not checked yet");
  });
});
```

`web/test/run-state.test.ts` — replace

```ts
describe("(a) terminal agreement with the server after the last frame", () => {
```

with

```ts
describe("a reader note's kinds and its own thread (notes-progress-report spec §5.7)", () => {
  it("keeps the reading's kinds, strings only, and marks the thread started from its own started event only", () => {
    const run = newRunState();
    applyEvent(run, { type: "session.note.received", metadata: { note_id: "n1", text: "Pastries too" } });
    expect(run.notes[0]).toMatchObject({ kinds: [], threadStarted: false });
    applyEvent(run, { type: "session.note.interpreted", metadata: { note_id: "n1", restatement: "pastries", kinds: ["new_angle", "exclude", 7], replaces: null, fallback: false } });
    expect(run.notes[0].kinds).toEqual(["new_angle", "exclude"]);
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "note-n2", note_id: "n2", sub_topic: "Your note: other", index: 3 } });
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "topic-01", sub_topic: "Alpha", index: 1 } });
    expect(run.notes[0].threadStarted).toBe(false);
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "note-n1", note_id: "n1", sub_topic: "Your note: pastries", index: 2 } });
    expect(run.notes[0].threadStarted).toBe(true);
    applyEvent(run, { type: "session.note.interpreted", metadata: { note_id: "n9", restatement: "late", kinds: ["new_angle"], replaces: null, fallback: false } });
    expect(run.notes[1]).toMatchObject({ id: "n9", kinds: ["new_angle"], threadStarted: false });
  });
});

describe("(a) terminal agreement with the server after the last frame", () => {
```

`web/test/briefs.test.ts` — replace

```ts
  it("a looped row's first line is why it reopened", () => {
```

with

```ts
  it("Planning, once done, lists a research note's own topic with the plan's (notes-progress-report spec §5.7)", () => {
    const run = newRunState();
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 2, note_topic_count: 1, sub_topics: [
      { coverage_id: "topic-01", title: "Published picks" }, { coverage_id: "note-n1", title: "Your note: pastries in the cafe", note_id: "n1" },
    ] } });
    expect(rowBrief(run, "planner", "done").titles).toEqual(["Published picks", "Your note: pastries in the cafe"]);
    expect(run.topics.map((t) => t.coverageId)).toEqual(["topic-01", "note-n1"]);
  });
  it("a looped row's first line is why it reopened", () => {
```

`web/test/components/reader-notes.test.tsx` — replace

```tsx
  it("is absent when the reader added no note", () => {
```

with

```tsx
  it("captions a mixed note with both of its results (notes-progress-report spec §5.7, AC12)", () => {
    const notes = [
      { note_id: "n5", text: "Pastries, nothing closed", restatement: "pastries, nothing closed", outcome: "covered" as const, steering_outcome: "not_addressed" as const },
      { note_id: "n6", text: "Opening hours", restatement: "opening hours", outcome: "not_checked" as const, steering_outcome: null },
    ];
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded={false} onOpenEvidence={() => {}} notes={notes} />);
    expect([...container.querySelectorAll(".reader-notes li .cap")].map((cap) => cap.textContent)).toEqual([
      "covered; the rest of your note: not addressed in the report", "not checked",
    ]);
  });

  it("is absent when the reader added no note", () => {
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
Push-Location web; npx vitest run test/notes.test.ts test/run-state.test.ts test/briefs.test.ts test/components/reader-notes.test.tsx; Pop-Location
```

Expected: `7 failed` with the rest passed (`Test Files  3 failed | 1 passed (4)`, `Tests  7 failed | 55 passed (62)` on the stand-in export): the five new tests of `notes.test.ts` (no `RESEARCH_WHERE`, `RESEARCH_NOW` or `noteCaption` yet), the new `run-state.test.ts` test and the new `reader-notes.test.tsx` test. The new `briefs.test.ts` test passes already: Planning's finished brief lists every entry `planning.completed` lists (`web/lib/briefs.ts:69-71`), which is §5.7's "until B lands" behaviour, now pinned.

- [ ] **Step 3: Keep each note's kinds and its thread in the run state**

`web/lib/run-state.ts` — replace

```ts
export interface NoteState {
  id: string; text: string; interpreted: boolean; restatement: string | null;
  replaces: string | null; fallback: boolean; where: NodeId | null;
}
```

with

```ts
export interface NoteState {
  id: string; text: string; interpreted: boolean; restatement: string | null;
  replaces: string | null; fallback: boolean; where: NodeId | null;
  /* notes-progress-report spec §5.7: the run's reading of the note's kinds ([] until it is read), and
     whether its own research thread has started — a researcher.sub_topic.started naming it. */
  kinds: string[]; threadStarted: boolean;
}
```

`web/lib/run-state.ts` — replace

```ts
  "researcher.sub_topic.started": (run, md) => {
    topicFor(run, md).state = "running";
  },
```

with

```ts
  "researcher.sub_topic.started": (run, md) => {
    topicFor(run, md).state = "running";
    /* notes-progress-report spec §5.7: a reader note's own thread names its note */
    const note = isText(md.note_id) ? run.notes.find((n) => n.id === md.note_id) : undefined;
    if (note) note.threadStarted = true;
  },
```

`web/lib/run-state.ts` — replace

```ts
    run.notes.push({ id: md.note_id, text: typeof md.text === "string" ? md.text : "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null });
```

with

```ts
    run.notes.push({ id: md.note_id, text: typeof md.text === "string" ? md.text : "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null, kinds: [], threadStarted: false });
```

`web/lib/run-state.ts` — replace

```ts
    if (!note) { note = { id: md.note_id, text: "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null }; run.notes.push(note); }
    note.interpreted = true;
    note.restatement = isText(md.restatement) ? md.restatement : null;
```

with

```ts
    if (!note) { note = { id: md.note_id, text: "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null, kinds: [], threadStarted: false }; run.notes.push(note); }
    note.interpreted = true;
    note.restatement = isText(md.restatement) ? md.restatement : null;
    note.kinds = Array.isArray(md.kinds) ? md.kinds.filter(isText) : [];
```

- [ ] **Step 4: Word a research note's acknowledgement and a mixed note's caption**

`web/lib/notes.ts` — replace

```ts
/* One acknowledgement line: `lead`, then the run's reading in `said` (the pick's .said), then `rest`. */
export interface Ack { key: string; lead: string; said: string | null; rest: string }

export function ackFor(note: NoteState, notes: readonly NoteState[]): Ack {
  if (!note.interpreted) return { key: note.id, lead: "Reading your note…", said: null, rest: "" };
  if (note.fallback) return { key: note.id, lead: "Got it — passed on as you wrote it", said: null, rest: "" };
  const earlier = note.replaces ? notes.find((n) => n.id === note.replaces) : undefined;
  const replacing = earlier ? ", replacing your earlier note about " + (earlier.restatement ?? earlier.text) : "";
  const where = note.where ? WHERE[note.where] ?? "" : "";
  return { key: note.id, lead: "Got it — ", said: note.restatement ?? note.text, rest: where + replacing };
}
```

with

```ts
/* notes-progress-report spec §5.7 (D1-D4): a research note — its kinds include new_angle, a mixed note
   included — is researched as its own topic, so its acknowledgement says when, by the step that was
   running when the run read it. "Now" while Researching is the active row and the note's own thread
   has started — a planning-time topic, a research-time thread or a note pass — and only then: a note
   read after the researcher's window closed gets no thread in that run. */
export const RESEARCH_WHERE: Readonly<Partial<Record<NodeId, string>>> = {
  planner: ", as its own topic",
  researcher: ", as its own topic",
  source_evaluator: ", researched as its own topic after this draft is reviewed",
  evidence_verifier: ", researched as its own topic after this draft is reviewed",
  report_writer: ", researched as its own topic after this draft is reviewed",
  report_reviewer: ", researched as its own topic next",
};
export const RESEARCH_NOW = ", researching it as its own topic now";
export const isResearchNote = (note: NoteState): boolean => note.kinds.includes("new_angle");
function whereFor(note: NoteState, active: NodeId | null): string {
  if (!note.where) return "";
  if (!isResearchNote(note)) return WHERE[note.where] ?? "";
  if (active === "researcher" && note.threadStarted) return RESEARCH_NOW;
  return RESEARCH_WHERE[note.where] ?? "";
}

/* One acknowledgement line: `lead`, then the run's reading in `said` (the pick's .said), then `rest`.
   `active` is the run's active row (RunState.active); a research note's "now" needs it. */
export interface Ack { key: string; lead: string; said: string | null; rest: string }

export function ackFor(note: NoteState, notes: readonly NoteState[], active: NodeId | null = null): Ack {
  if (!note.interpreted) return { key: note.id, lead: "Reading your note…", said: null, rest: "" };
  if (note.fallback) return { key: note.id, lead: "Got it — passed on as you wrote it", said: null, rest: "" };
  const earlier = note.replaces ? notes.find((n) => n.id === note.replaces) : undefined;
  const replacing = earlier ? ", replacing your earlier note about " + (earlier.restatement ?? earlier.text) : "";
  return { key: note.id, lead: "Got it — ", said: note.restatement ?? note.text, rest: whereFor(note, active) + replacing };
}
```

`web/lib/notes.ts` — replace

```ts
export function visibleAcks(notes: readonly NoteState[]): { acks: Ack[]; earlier: number } {
  const shown = notes.slice(-2);
  return { acks: shown.map((note) => ackFor(note, notes)), earlier: notes.length - shown.length };
}
```

with

```ts
export function visibleAcks(notes: readonly NoteState[], active: NodeId | null = null): { acks: Ack[]; earlier: number } {
  const shown = notes.slice(-2);
  return { acks: shown.map((note) => ackFor(note, notes, active)), earlier: notes.length - shown.length };
}
```

`web/lib/briefs.ts` — replace

```ts
  const noted = id === run.active ? visibleAcks(run.notes) : { acks: [], earlier: 0 };
```

with

```ts
  const noted = id === run.active ? visibleAcks(run.notes, run.active) : { acks: [], earlier: 0 };
```

`web/lib/notes.ts` — replace

```ts
import type { ReaderNoteOutcome } from "./api";
```

with

```ts
import type { ReaderNoteOutcome, ReaderNoteRecord } from "./api";
```

`web/lib/notes.ts` — replace

```ts
/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
```

with

```ts
/* notes-progress-report spec §5.7 (D20): a mixed note's caption reads both of its results, its own topic's
   first; every other note's caption is its one outcome. */
export function noteCaption(note: Pick<ReaderNoteRecord, "outcome" | "steering_outcome">): string {
  const words = OUTCOME_TEXT[note.outcome];
  return note.steering_outcome ? words + "; the rest of your note: " + OUTCOME_TEXT[note.steering_outcome] : words;
}

/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
```

`web/lib/api.ts` — replace

```ts
export interface ReaderNoteRecord { note_id: string; text: string; restatement: string | null; outcome: ReaderNoteOutcome }
```

with

```ts
/* steering_outcome (notes-progress-report spec §5.6, D20): a mixed note's steering half; null for every other
   note, and absent from a response recorded before the field existed. */
export interface ReaderNoteRecord { note_id: string; text: string; restatement: string | null; outcome: ReaderNoteOutcome; steering_outcome?: ReaderNoteOutcome | null }
```

`web/components/ReportBody.tsx` — replace

```tsx
import type { ReaderNoteRecord } from "@/lib/api";
import { OUTCOME_TEXT } from "@/lib/notes";
```

with

```tsx
import type { ReaderNoteRecord } from "@/lib/api";
import { noteCaption } from "@/lib/notes";
```

`web/components/ReportBody.tsx` — replace

```tsx
              <li key={note.note_id} data-outcome={note.outcome}><span className="rn-text">{note.text}</span> <span className="cap">{OUTCOME_TEXT[note.outcome]}</span></li>
```

with

```tsx
              <li key={note.note_id} data-outcome={note.outcome}><span className="rn-text">{note.text}</span> <span className="cap">{noteCaption(note)}</span></li>
```

- [ ] **Step 5: Run the tests to verify they pass, and type-check**

Run:

```powershell
Push-Location web; npx vitest run test/notes.test.ts test/run-state.test.ts test/briefs.test.ts test/components/reader-notes.test.tsx; npm run -s typecheck; Pop-Location
```

Expected: no failure (`Tests  62 passed (62)` on the stand-in export; more with Phase D's own tests in these files), then `typecheck` prints nothing.

- [ ] **Step 6: The status the e2e notes test reads gains the new field**

The replay session these notes reach has finished, so after Phase D's terminal rule they read `not_checked`, and after Task 7 each carries `steering_outcome: null` (a steering note). Phase D's plan changes these lines to `outcome: "not_checked"` (Open issue O-1). This edit is a span that writes the final check whatever D left between its two boundary lines:

`web/e2e/notes.spec.ts` (D-touched) — replace the lines from the one starting `  expect(status.notes).toEqual([` up to, not including, the one starting `  expect([status.notes_remaining, status.note_passes]).toEqual([8, 0]);`, with

```ts
  expect(status.notes).toEqual([
    { note_id: "n1", text: "More on fire-safety standards", restatement: "More on fire-safety standards", outcome: "not_checked", steering_outcome: null },
    { note_id: "n2", text: "Only the United States", restatement: "Only the United States", outcome: "not_checked", steering_outcome: null },
  ]);
```

- [ ] **Step 7: Bring the design record up to Phase A**

`docs/design/DESIGN.md` — replace

```markdown
`not addressed in the report` (the findings bore on it and the report still does not
follow it, after its one redraft — never `covered`), `not checked` (no review judged it)
or `replaced by a later note`.
```

with

```markdown
`not addressed in the report` (the findings bore on it and the report still does not
follow it, after its one redraft — never `covered`), `not checked` (nothing in the
finished run could judge it: no review did, or its own topic never researched it) or
`replaced by a later note`. A research note's caption comes from its own topic's targets,
never from the review: `covered` once a verified finding answers one of them. A mixed
note's caption reads both of its results — `{its topic's result}; the rest of your note:
{its steering result}` (notes-progress-report §5.6–§5.7, D20, D31).
```

`docs/design/DESIGN.md` — replace

```markdown
**A reader's note buys its own reopenings** (live-briefs §4.6–§4.7, D11, 2026-09-29).
A note the review found no evidence for buys one targeted research pass: on
`graph.note_pass.started` Researching opens on
`Researching your note: {the run's reading}` (`Researching your notes: {a}; {b}` for
several; `--muted`), and its checklist lists only the notes' own sub-topics,
`Your note: …`. A note the report
ignores, or one that arrived while the review ran, buys one redraft: on
`graph.note_redraft.requested` Writing opens on `Rewriting for your note: {…}`. Neither
spends the review's own budget — a note pass is not an extra pass and does not advance
`iteration`, and a note redraft is not the one writer re-run — and each note buys at most
one of each, so ten notes (D11a) bound the run.
```

with

```markdown
**A research note never waits for a review** (notes-progress-report §5, D1–D5, D20,
2026-09-30). A note whose reading includes `new_angle` is researched as its own topic,
`Your note: …`. Read during Planning, it joins the plan when the plan is published, and
Planning's finished brief lists it with the plan's titles; read during Researching, it
starts its own topic at once, beside the topics already running, as a row of the
checklist, and the step does not finish until that topic does. Its acknowledgement says
which: `, as its own topic`; `, researching it as its own topic now` once its own topic
has started; `, researched as its own topic after this draft is reviewed` from Evaluating
to Writing; `, researched as its own topic next` during Reviewing. A note read after
Researching — or one whose topic failed or never started — buys one note pass, after
which only that note's part and the bottom line are rewritten. A mixed note does both:
its topic, and its steering half judged and enforced like a steering note.

**A reader's note buys its own reopenings** (live-briefs §4.6–§4.7, D11, 2026-09-29). A
research note owed its pass, or a steering note the review found no evidence for, buys
one targeted research pass: on `graph.note_pass.started` Researching opens on
`Researching your note: {the run's reading}` (`Researching your notes: {a}; {b}` for
several; `--muted`), and its checklist lists only the notes' own sub-topics,
`Your note: …`. A note with a steering kind the report ignores, or one that arrived while
the review ran, buys one redraft, which rewrites every part: on
`graph.note_redraft.requested` Writing opens on `Rewriting for your note: {…}`. Neither
spends the review's own budget — a note pass is not an extra pass and does not advance
`iteration`, and a note redraft is not the one writer re-run — and each note buys at most
one of each, so ten notes (D11a) bound the run.
```

Then bring api-gaps 3.9's outcome word in line with Phase D's `not_checked` (spec §5.9: "unchanged in substance"; this changes nothing when Phase D already did it):

```powershell
@'
from pathlib import Path
p = Path("docs/design/api-gaps.md")
t = p.read_text(encoding="utf-8")
old = "and it ends `pending` (`not checked`)"
print("changed" if old in t else "already current")
p.write_text(t.replace(old, "and it ends `not_checked` (`not checked`)"), encoding="utf-8")
'@ | .venv\Scripts\python.exe -
```

Expected: `changed`, or `already current` when Phase D changed the row itself.

- [ ] **Step 8: Run the whole web unit suite and the CSS guard**

Run:

```powershell
Push-Location web; npm test; npm run -s check:css; Pop-Location
```

Expected: Vitest ends `Tests  B-web + 8 passed` with no failure (`227 passed` on the stand-in export), then `OK`.

- [ ] **Step 9: Commit**

```powershell
git add web/lib/run-state.ts web/lib/notes.ts web/lib/briefs.ts web/lib/api.ts web/components/ReportBody.tsx web/e2e/notes.spec.ts web/test/notes.test.ts web/test/run-state.test.ts web/test/briefs.test.ts web/test/components/reader-notes.test.tsx docs/design/DESIGN.md docs/design/api-gaps.md
git commit -m "feat(web): a research note's acknowledgement and thread, a mixed note's two-part caption; DESIGN.md for Phase A"
```

### Task 11: Final verification

**Files:** none changed (a capture writes images under `web/visual/A-final/`, which git ignores).

**Interfaces:**
- Consumes: Tasks 1–10 committed; Task 1's baselines **B-py**, **B-web** and the five B-pins.
- Produces: the evidence that Phase A is complete — every suite green, the four moved pins and the one unmoved, the shared names in place, the e2e run and the captures.

- [ ] **Step 1: The whole Python suite, the pins, and the lint guard**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print([f(n) for n in ('planner', 'researcher', 'source_evaluator', 'evidence_verifier', 'report_writer')])"
.venv\Scripts\python.exe -m ruff check --select F src/deep_research/agents/reader_notes.py src/deep_research/agents/planner.py src/deep_research/agents/researcher.py src/deep_research/agents/source_evaluator.py src/deep_research/agents/report_writer.py src/deep_research/agents/report_reviewer.py src/deep_research/agents/__init__.py src/deep_research/graph src/deep_research/runtime/notes.py src/deep_research/utils/types.py src/deep_research/api/notes.py src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_agents/test_research_note_threads.py tests/test_graph/test_note_routing.py tests/test_api/test_notes.py
```

Expected: pytest ends `B-py + 38 passed` with no `failed` or `error` (the planning export printed `4997 passed` with the `.env` test and one timing failure, below). Then the five fingerprints: planner, researcher, source_evaluator and report_writer equal the values their last re-pins wrote (Task 5, Task 6, Task 4, Task 9), and evidence_verifier equals B-pin(evidence_verifier); `tests/test_evaluation/test_config.py` in the pytest run above checks the same. When the five B-pins were the `f4282818` values, they print `['3934bea57f61', 'b9caf536e3f0', 'cc5a310b0aa0', '4a3d56fab932', 'a27544eee344']`. Then `All checks passed!`.

`tests/test_agents/test_researcher.py::test_two_reads_extractions_overlap_in_time` asserts a wall time under 0.2 s for two overlapping 0.1 s calls, and it predates this plan. On the planning export it failed once in a full run while other test suites loaded the machine, and passed five times out of five alone, as it did in an earlier full run of the same code. If it fails here, run it alone — `.venv\Scripts\python.exe -m pytest tests/test_agents/test_researcher.py::test_two_reads_extractions_overlap_in_time -q` — and it must print `1 passed`; any other failure is a defect: stop and report it.

- [ ] **Step 2: The shared names are where the spec says, and nothing old remains**

Run:

```powershell
@'
import inspect
from deep_research.agents import reader_notes
from deep_research.api import notes
from deep_research.graph import nodes, state
from deep_research.utils.types import ReaderNote
for name in ("is_research_note", "has_steering_kind", "steering_view", "steering_notes", "note_sub_topic", "NOTES_WAIT_S"):
    print(name, hasattr(reader_notes, name))
print("researched_note_topic_ids", hasattr(state, "researched_note_topic_ids"))
print("note_outcome", str(inspect.signature(notes.note_outcome)))
print("note_steering_outcome", str(inspect.signature(notes.note_steering_outcome)))
print("ReaderNote.short", "short" in ReaderNote.model_fields)
print("graph.nodes keeps no wait of its own", not hasattr(nodes, "_REVIEW_NOTES_WAIT_S"))
print("note_sub_topic lives in agents", nodes.note_sub_topic.__module__)
'@ | .venv\Scripts\python.exe -
```

Expected, exactly:

```text
is_research_note True
has_steering_kind True
steering_view True
steering_notes True
note_sub_topic True
NOTES_WAIT_S True
researched_note_topic_ids True
note_outcome (note_id: 'str', state: 'ResearchState | None', *, terminal: 'bool') -> 'NoteOutcome'
note_steering_outcome (note_id: 'str', state: 'ResearchState | None', *, terminal: 'bool', note: 'ReaderNote | None' = None) -> 'NoteOutcome | None'
ReaderNote.short True
graph.nodes keeps no wait of its own True
note_sub_topic lives in agents deep_research.agents.reader_notes
```

- [ ] **Step 3: The web unit suite, types and CSS**

Run:

```powershell
Push-Location web; npm test; npm run -s typecheck; npm run -s check:css; Pop-Location
```

Expected: Vitest ends `Tests  B-web + 8 passed` (on the planning export with Phase D's stand-in: `28 passed` files, `227 passed` tests); `typecheck` prints nothing; `check:css` prints `OK`.

- [ ] **Step 4: Playwright against the replay API**

First make sure nothing listens on 8010, 3010 or 3011:

```powershell
foreach ($p in 8010, 3010, 3011) { if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) { "port $p is in use" } }
```

Expected: no output. Then:

```powershell
Push-Location web; npm run test:e2e; Pop-Location
```

Expected [INFERENCE, not run by planning — this step proves or refutes it]: every test passes, the same count as after Phase D, with no new failure. A replay run never applies a note and its interpreter never returns `new_angle` (`api/notes.py:267-275`; api-gaps 3.9), so the only change an e2e test can see is the status's `steering_outcome: null`, which `e2e/notes.spec.ts` now expects (Task 10 Step 6). A failure here is a defect of this plan: stop and report it.

- [ ] **Step 5: Visual captures, checkpoint `A-final`**

Run:

```powershell
Push-Location web; $env:VISUAL_CHECKPOINT = "A-final"; npm run capture:visual; Remove-Item Env:VISUAL_CHECKPOINT; Pop-Location
Get-ChildItem web/visual/A-final -Filter *.png | Measure-Object | Select-Object -ExpandProperty Count
```

Expected [INFERENCE, not run by planning]: every capture test passes (each also asserts no page-wide horizontal scroll), and the image count equals the count of the checkpoint Phase D took. Open `11-note-ack.png`, `11-note-ack-phone.png`, `12-report-notes.png` and `12-report-notes-phone.png` at full height beside Phase D's: they must look the same — a replay note is an emphasis, so its acknowledgement keeps the steering words, and its report caption is `not checked`. Any visible difference is a defect of this plan: stop and report it.

- [ ] **Step 6: Record the result**

Nothing to commit. Report: B-py and the Step 1 count; B-web and the Step 3 count; the five B-pins and the Step 1 fingerprints; the Step 4 and Step 5 summaries; and the capture folder `web/visual/A-final/`.

---

## Self-review against the spec

### Acceptance criteria → tasks and tests

| AC | What it asks | Task | Test(s) |
|---|---|---|---|
| AC1 | A research note read before the planner returns is in `planning.completed.sub_topics` as `{coverage_id: "note-{id}", title: "Your note: {restatement}", note_id}` and in `state.sub_topics` with one required target per new question; both plan requests carry the new `PLANNING_NOTES` | 5 | `test_planner_appends_research_notes`, `test_planning_notes_text` |
| AC2 | 10 planned topics + 1 note topic at `max_sub_topics = 10`: 11 researched, no `cap` skip | 6 | `test_researcher_note_topics_uncapped` |
| AC3 | With `sub_topic_concurrency = 1` and a blocked planned loop, a note's thread starts at once, its started event (with `note_id`) before the blocked loop completes; the researcher's `graph.node.completed` follows the note loop's completed event | 6 | `test_research_note_thread_starts_ungated` |
| AC4 | A note read within the wait after every loop finished gets a thread; one read after the window closed is returned by `notes_due_a_pass` after the review | 6, 8 | `test_late_note_waits_then_threads`; `test_note_after_window_owes_pass` (and `test_late_note_wait_times_out_and_closes_window`) |
| AC5 | `notes_due_a_pass` returns a research note with no researched topic whatever the review's status, never one whose topic was researched; `notes_due_a_redraft` never returns a new_angle-only note and returns a mixed note on `ignored_with_evidence` or when unreviewed | 8 | `test_notes_due_a_pass_research_notes_any_review_status` (4 cases), `test_research_notes_never_redraft` |
| AC6 | After a note pass: one section call per note part, one per part with no previous section, one bottom-line call; every other section unchanged with its verdicts; the reviewer runs a full review | 9 | `test_writer_carries_parts_after_note_pass` (its last assertion: after `graph.note_pass.started`, `_arrived_via_redraft_hop` is `False`, so the review is a full one; `_FRESH_DRAFT_MARKERS` is unchanged) |
| AC7 | A steering note's (or a mixed note's steering half's) `ignored_with_evidence` buys exactly one note redraft that drafts every part and leaves `writer_redrafts` unchanged | 8, 9 | `test_research_notes_never_redraft` (one redraft, `writer_redrafts` unchanged); `test_note_redraft_unchanged` (every part drafted: nothing carried, no defect) |
| AC8 | The packet holds steering notes and mixed notes without `new_angle`; a new_angle-only note's disposition is dropped, a mixed note's kept | 4 | `test_review_packet_steering_notes_only`; `test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does` (flipped) |
| AC9 | `note_outcome` and `note_steering_outcome` follow §5.6 row by row; no terminal session (incl. `stopped`) reports `pending` in either field | 7 | `test_note_outcome_table`, `test_note_steering_outcome_table`, `test_terminal_sessions_never_pending` (5 statuses) |
| AC10 | Both worst cases (ten steering notes: pass then redraft; ten mixed notes: pass then redraft), each note arriving in a different Writing step, complete under the unchanged recursion limit with `max_extra_passes` at its configured value | 8 | `test_recursion_limit_steering_notes_pass_then_redraft`, `test_recursion_limit_mixed_notes_during_writing` |
| AC11 | No evidence-verifier request contains a note | 4 | `test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does` (existing), `test_steering_views_per_request` |
| AC12 | Web: the research-note ack per §5.7, "now" only after the thread's started event; the thread in the Researching checklist; "not checked" for `not_checked`; a mixed note's caption shows both outcomes | 10 | `notes.test.ts` "a research note's acknowledgement and thread" (5 tests, one of them "says 'now' while Researching runs the note's own topic, whichever step read the note", review P3-5); `run-state.test.ts` "a reader note's kinds and its own thread"; `reader-notes.test.tsx` "captions a mixed note with both of its results"; `briefs.test.ts` (Planning's finished brief) |
| AC34 | A mixed note (new_angle + exclude): own topic (at planning or during research); every loop, extraction, evaluator, writer and review request prints it with `(exclude)`, the planner's with both kinds; its disposition kept, `ignored_with_evidence` buys its redraft after its pass when it arrived late; `note_outcome` follows its targets and `note_steering_outcome` its disposition; Reviewing's row and the bottom-line note line show both results | 4, 5, 6, 7, 8, 10 | `test_mixed_kind_note_steers_and_researches` (planning-time topic, requests, disposition), `test_steering_views_per_request`, `test_planner_appends_research_notes` (a mixed note read mid-plan), `test_research_notes_never_redraft` and `test_recursion_limit_mixed_notes_during_writing` (pass, then redraft), `test_note_steering_outcome_table` (both halves apart), the web caption tests. Reviewing's row is Phase B's (§6.7) and the bottom-line note line Phase C's (§7.2); both read `note_steering_outcome` and `NoteState.kinds`, which this plan supplies. |
| AC35 | A failed note thread sets `stop` and no later note thread starts; unanswered, it owes one pass that reuses its topic, confines the researcher and marks it `passed`; answered, it reads `covered`; an unstarted planning-time topic is owed its pass the same way; an unfunded reused pass records `researcher_extra_pass_unfunded`, opens no loop, and the note stays `passed` and reads `not_found` | 6, 8 | `test_note_thread_provider_failure_sets_stop`, `test_a_planning_time_note_topic_waits_its_turn_and_stop_leaves_it_unstarted`, `test_failed_note_thread_owes_one_pass` |

### The rest of the spec's Phase A → tasks

| Spec | Task |
|---|---|
| §4 item 2 (A replaces `note_outcome` with §5.6's table and adds `note_steering_outcome`; `ReaderNoteResponse` field) | 7 (D's half checked in Task 1) |
| §4 item 5 (the shared names) | 2, 3, 7, 8, 10 — Global Constraints lists them |
| §5.1 helpers, `note_sub_topic` moved with `reason`, `researched_note_topic_ids`, the request table | 2, 4, 8 |
| §5.2 `PLANNING_NOTES`, appending at publication, `note_topic_count`, no plan → no append | 5 |
| §5.3 dispatcher, ungated threads, board change count, the 30 s window, what a thread sees, failures and cancellation, budget (unchanged: threads reserve from the run's one budget), events and state | 2, 6 |
| §5.4 routing code, mixed notes, `note_pass_node`'s reuse and targets, the unfunded reused pass, the writer after a note pass, the D22 exception | 8, 9 |
| §5.5 the review's view of notes (`_reviewed_notes_update` unchanged) | 4 |
| §5.6 outcomes, `note_records`, `session_note_fields`, `steering_outcome` (API and `web/lib/api.ts`) | 7, 10 |
| §5.7 `NoteState.kinds`/`threadStarted`, the `where` table, the checklist row, `OUTCOME_TEXT` (Phase D's), the mixed caption, Planning's finished brief | 10 |
| §5.8 edge cases: replaced before a topic; replaced after; planned twice (R3: no merge, nothing to build); ten notes in one run (bounded by LB-D11a, ungated); a note during an extra or note pass (the dispatcher runs in every researcher node); Stop during research (the `finally`); a failed thread; an unstarted planning-time topic; an unfunded pass; a reading that outlasts the wait; a mixed note | `test_replaced_note_before_topic_gets_none`, `test_replaced_note_after_topic_reads_replaced`, `test_dispatcher_cancels_threads_on_cancel`, the AC35 tests, `test_late_note_wait_times_out_and_closes_window`, `test_mixed_kind_note_steers_and_researches` |
| §5.9 DESIGN.md §3.5 and §3 inventory; api-gaps 3.9 | 10 |
| §11.1 Phase A pytest names | every one exists under the spec's name: Tasks 4–9 (plus helpers' and label tests in 2–3) |
| §11.1 Phase A Vitest | 10 |
| §11.2 Phase A updates (`test_note_routing.py`, `test_reader_notes_state.py`, `test_reader_notes_replay.py` flips at `:172`, `:183`, `:209` with `:149`, `:154`, `:157` kept, `test_reader_notes_planning.py`, `test_reader_notes_review.py`, `test_reader_notes_writing.py`, `tests/test_api/test_notes.py`, `test_note_route.py`) | 2–9 |
| §11.3 visual captures after the phase | 11 |
| R1–R4, R10, R11 | accepted by the spec; R1 rests on LB-D11a and the one budget (no tool-lock assumption in Task 6); R2's 30 s bound is `NOTES_WAIT_S`; R3 no merge; R4 carry-over as today (Task 9); R10 harmless; R11 one pass per note |

### Placeholder scan

Searched this document for "TBD", "TODO", "implement later", "fill in", "similar to Task", "appropriate", "as needed", "etc.": none in any instruction. Every code step shows the code; every command shows its expected output, observed on the planning export except the two [INFERENCE] lines of Task 11, which name the step that proves them.

### Type and name consistency

- `note_sub_topic(note: ReaderNote, *, priority: int, reason: NoteTopicReason) -> SubTopic` — defined in Task 2; called with `reason=` in Tasks 2 (graph/nodes, tests), 5 (planner), 6 (researcher), 7 and 8 (tests, `note_pass_node`).
- `NOTES_WAIT_S` — defined in Task 2; read by `graph/nodes.py` (Task 2) and `agents/researcher.py` (Task 6) as a module-level name, so tests patch `deep_research.graph.nodes.NOTES_WAIT_S` and `deep_research.agents.researcher.NOTES_WAIT_S`.
- `board_version() -> int | None`, `wait_for_board_change(seen: int) -> int`, `notes_being_read() -> bool`, `research_notes_without_a_topic(state_notes, known_coverage_ids) -> list[ReaderNote]` — Task 2; used by Task 6 and Task 8's tests.
- `researched_note_topic_ids(state) -> set[str]` — Task 8, in `graph/state.py`, exported from `deep_research.graph`.
- `note_outcome(note_id, state, *, terminal) -> NoteOutcome`, `note_steering_outcome(note_id, state, *, terminal, note=None) -> NoteOutcome | None`, `NoteRecord`, `note_records(board, state, *, terminal) -> list[NoteRecord]` — Task 7; `test_failed_note_thread_owes_one_pass` (Task 8) calls `note_outcome(..., terminal=True)`.
- `ReportWriterTask.note_pass_coverage_ids: list[str]`, `_note_pass_coverage_ids(state) -> list[str]` — Task 9.
- `planning_completed_event(outcome, *, note_topics=())`, `PlannerAgent._note_topics_for(state, plan)` — Task 5; Task 8's §5.8 test calls `_note_topics_for`.
- `NoteState.kinds: string[]`, `NoteState.threadStarted: boolean`, `RESEARCH_WHERE`, `RESEARCH_NOW`, `isResearchNote`, `noteCaption`, `ackFor(note, notes, active = null)`, `visibleAcks(notes, active = null)`, `ReaderNoteRecord.steering_outcome?` — Task 10; `rowBrief` passes `run.active`.
- The fingerprints, under the B-pin rule (Conventions): each re-pin anchors on the value its agent's pin holds when the task starts, and the next re-pin of the same agent anchors on what it wrote. In the unmoved case: `report_writer` `6e1aedc2888e` → `cafa5ba3d613` (Task 4) → `a27544eee344` (Task 9); `source_evaluator` `24809aa975a3` → `cc5a310b0aa0` (4); `planner` `d1ba46ce147f` → `3934bea57f61` (5); `researcher` `a8c9528f0c20` → `b9caf536e3f0` (6); `evidence_verifier` stays at its B-pin, `4a3d56fab932`.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-30-phase-a-notes.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, a review between tasks, fast iteration (superpowers:subagent-driven-development).
2. **Inline Execution** — the tasks in one session with checkpoints (superpowers:executing-plans).
