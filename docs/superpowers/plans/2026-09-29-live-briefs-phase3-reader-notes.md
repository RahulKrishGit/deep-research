# Live briefs, Phase 3 — reader notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While a run is going, the reader can add up to ten notes from a quiet line at the foot of the pipeline card. The run reads each note with a fast model, acknowledges it in the running step, and uses it in every step but verification. Each note the review finds uncovered gets exactly one targeted research pass, each note the report ignores gets exactly one redraft, and the report says what became of every note.

**Architecture:**
- **API.** `POST /research/{id}/notes` → `SessionStore.add_note` → the session's `NoteBoard` (`runtime/notes.py`). `session.note.received` is published at once. An injected `NoteInterpreter` then reads the note within `hitl.note_interpret_timeout_s`: live mode asks the configured provider with thinking disabled; replay mode keeps the note as written. `session.note.interpreted` follows. The board is bound for the session's run through a ContextVar, exactly as `graph/live.py` binds the live sink.
- **Engine.** `ResearchState` gains `reader_notes` and `note_passes`. `agent_node` merges the board's new notes into the state before each agent runs. One renderer (`agents/reader_notes.py`) prints a `# Reader notes` block, only when there are notes, into the planner's plan and plan-review requests and its scoping turn, every researcher decision turn (read from the board) and its extraction, the source evaluator's `# Context` slot, the writer's section and bottom-line requests, and the review (with ids and a `note_dispositions` reply) — never into an evidence-verifier request. `graph_route` gains two note checks right after "halted": a new `note_pass` hop that appends `note-*` sub-topics and confines the researcher to them, and a note redraft through the existing `writer_redraft` hop that spends no `MAX_WRITER_REDRAFTS`. The report names a note it found no evidence for.
- **Web.** `RunState` gains `notes` and the note loops; `lib/notes.ts` words the acknowledgements; `NoteLine` is the card's last element; `BriefSpine` acknowledges notes in the running row; the report shows "Your notes" above the prose; the pass fact counts note passes.

**Tech Stack:** Python 3.12 (`.venv`), FastAPI, pydantic 2, LangGraph 1.2.10, pytest + pytest-asyncio; Node v22, Next.js 16 (Turbopack), React 19, Vitest + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode. Linux only: every command below is for the cloud VM.

**Spec:** `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md` — Phase 3 only: §4.6, §4.7, the Phase-3 rows of §4.8 and §4.9, AC15–AC20, the Phase-3 parts of §6, and §7 R4/R5. Decisions D8–D11a and D17 (§2) are closed; nothing here reopens them. Where this plan had to choose, the choice is listed under "Spec ambiguities resolved here"; where a spec statement could not be kept as written, it is an entry under "Open issues".

**Starting point: the end of Phase 2.** This plan runs after `docs/superpowers/plans/2026-09-29-live-briefs-phase2-one-time-check.md` is complete.
- For files Phase 2 edits, an anchor is the text Phase 2 writes, marked "(anchor as written by Phase 2 Task N)", or "(anchor as written by Phase 2's review fix `539d51f`)" for the one written by its fix wave. An anchor on text an earlier task of this plan writes is marked "(anchor as written by Task N)". Every other anchor is text at `fc2771e` (the end of Phase 1) that Phase 2 left as it was.
- The anchors were first written against the Phase 2 plan's own blocks (as committed at `ef4ee35`) applied to an export of `fc2771e`. By the time this plan was finished, Phase 2's Tasks 1–10 and its review fix wave were committed, ending at `539d51f` ("docs(design): sidebar mark and routes, the check's accent and refusal faces"), with only its verification task to run. Every anchor holds at `539d51f`, and every Expected value below was observed there.
- Phase 2's execution and fixes departed from its plan's text in eight files: `README.md` (one line rewrapped); `docs/design/DESIGN.md` (paragraphs reflowed, references corrected, §3.1's route list and running mark, stage 2a's accent and refusal faces); `src/deep_research/api/clarify.py` with `tests/test_api/test_clarify.py` (the checker's text collapsed to one line, one new test); `api/sessions.py` (a docstring); `api/replay.py` (formatting); and `web/components/ClarifyStage.tsx` with `web/test/components/clarify-stage.test.tsx` (the kept check held in state, `Other…` pressed only when it holds words, Just start keeps them; four new tests). This plan anchors on none of that text but one DESIGN.md sentence of `539d51f`'s (Task 12), and its README anchor is a sentence the rewrap leaves whole.
- Line numbers are those of each file when its task starts — `539d51f`, plus this plan's earlier tasks — and only locate the anchors.
- Task 1 checks every anchor again before anything is edited, in case Phase 2's verification or a later review fix moves one.
- Phase 2's final review left notes for this phase; the section "Phase 2's final-review notes" below says where each is met.

**Evidence.** Planning implemented every task on 2026-09-29, then executed this document itself, step by step, on a fresh export of `539d51f`:
- A script parsed this document's blocks as an implementer reads them. For each step it applied that step's blocks in order, stopping unless every anchor occurs exactly once, and then ran the step's command blocks. Every Expected line of Tasks 1–10 and 12–14 is the value that run printed. Only the `git` blocks were skipped; Task 14 Step 2's other lines were run by hand, and its two `git diff` lines checked by comparing the files with `539d51f`'s. Task 1 Step 2 printed `anchors: 280 exactly once; creates: 18 absent; appends: 3 onto files present`. Two command blocks were corrected after that run and re-run by hand: Task 13 Step 4's `grep` (its first version also searched the tests, and matched the new test's own words), and the re-pin snippet's pattern in Tasks 4 and 5 (now built without an f-string; it pins the same values).
- Playwright then ran on the tree that run left, with its ports moved to 8110, 3110 and 3111 so as not to disturb other agents' servers on the shared ports: Task 11 Step 2 printed `18 passed` (`e2e/notes.spec.ts`, each test three times), Step 3 `61 passed` and Step 4 `12 passed` (the `P3-T11` captures, 20 images). Task 14 Step 4's run was cut off by a container restart before it printed, so its Expected lines are Task 11's observed counts on the same tree, and the captures were produced but not yet read; Tasks 11 and 14 read them full height when the plan executes. Task 1's `55 passed` was observed on `539d51f` itself, the same way.
- Earlier runs of the same kind — on the Phase 2 plan's own end state (its blocks applied to `fc2771e`) and on `dfef7ee` — gave every increment below; their counts are lower by the tests Phase 2's execution and fixes added.

## Global Constraints

- **Where.** Branch `feat/live-briefs-and-reader-notes`, the Linux cloud checkout; every path is relative to the repository root. Tasks run **strictly in order 1 → 14**, one at a time. Commit after every task that changes files (Tasks 1 and 14 change none).
- **No live model, no secrets.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest or the API in `--mode replay`.
  - The live interpreter is exercised only through a recording completer (`tests/test_api/test_notes.py`, Task 7).
  - Every API test replaces it with a recording fake (`tests/test_api/conftest.py`, Task 8).
  - The engine's use of notes is proven on the real graph with replay's scripted completer and a bound board (`tests/test_graph/test_reader_notes_replay.py`).
- **Byte-identical without notes (spec §4.8 "Replay mode").**
  - `agents/prompts.py` and `agents/evidence_verifier.py` are not edited.
  - Every notes section is added only when there are notes, and the review asks its note verdicts through two schemas of their own, used only when its packet carries notes.
  - `tests/test_graph/test_reader_notes_replay.py::PINNED_RUN_DIGESTS` (Task 2) pins every request of two whole replay runs as they are at the end of Phase 2.
  - The full replay suite passes unchanged.
  - Four agent-fingerprint pins move because their modules' code changes, and each is re-pinned in the task that moves it: planner and researcher in Task 4, source evaluator and report writer in Task 5. The evidence verifier's pin stays `4a3d56fab932`; Task 14 checks it.
- **API contract (spec §4.6), exactly:**
  - `POST /research/{session_id}/notes` with body `{"text": "…"}`, 1–500 characters after trim. It returns `202` `{"note_id": "n1", "status": "received"}`; `404 session_not_found`; `409 notes_closed` while the session is `needs_input`, once it is terminal or stopped, and once `finalize_report` has started (read as ambiguity 5 says: from the run's published decision to publish or end); `409 note_limit_reached` past `MAX_NOTES_PER_RUN = 10` accepted notes; `422 validation_error` for empty or over-long text. A refused note is never counted.
  - Events, both through `session.publish`: `session.note.received` `{note_id, text}` at once; `session.note.interpreted` `{note_id, restatement, kinds, replaces, fallback}` when the reading ends.
  - A reading: `kinds` 1–3 of `emphasis | exclude | scope | new_angle | about_reader`; `restatement` ≤ 120 characters; `scope` `{geography?, period?}` or null; `new_questions` ≤ 3; `replaces` an earlier note id or null. A failed, slow or invalid reading keeps the note with `kinds: ["emphasis"]`, `restatement` = the text, `fallback: true`.
  - `ResearchSessionResponse` gains `notes: [{note_id, text, restatement, outcome}]`, `notes_remaining: int` (10 minus accepted), `note_passes: int` and `clarification: {questions, answers} | null`.
- **Engine contract (spec §4.6):**
  - `ReaderNote {note_id, text, received_at, received_during, kinds, restatement, scope, new_questions, replaces, reviewed, passed, redrafted}`; `ResearchState.reader_notes` (replace-on-write) and `note_passes`.
  - `graph_route` reads, right after "halted": first a note with disposition `no_evidence` and `passed == false` → `note_pass`; then a note `ignored_with_evidence` with `redrafted == false`, or one with `reviewed == false` → the redraft hop, spending no `MAX_WRITER_REDRAFTS`. Every rule after them is unchanged.
  - `graph_recursion_limit(p) = (p + 1) * len(NODE_NAMES) + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS) + 10`, with `NOTE_REDRAFT_STEPS = 3`.
  - D10: no evidence-verifier request ever carries a note. D11: one targeted pass per uncovered note, outside the extra-pass budget; the request-attempt budget still applies (`src/deep_research/request_budget.py:131-157`, untouched). D11a: at most 10 notes per run.
- **Web copy, verbatim (spec §4.7; `—` is U+2014, `…` is U+2026; (I) marks this plan's words where the spec has none):**

  | Where | Text |
  |---|---|
  | Note field | placeholder `Add a note — something to focus on, leave out or change`; label `Add a note for this research` (the pick's) |
  | Send | a neutral `.icon-btn`, an up-arrow, `span.sr` `Add note` |
  | Closed | `.cap` `Notes are closed — the report is being published` |
  | Send failed (I) | `.cap` `Couldn't send — try again`, under the field, until the next edit (any failure but the two 409s: a 5xx, a network error) |
  | Ack, not yet read | `Reading your note…` |
  | Ack, read | `Got it — {restatement}{where}` |
  | Ack, replacing | `Got it — {restatement}{where}, replacing your earlier note about {earlier restatement}` |
  | Ack, fallback | `Got it — passed on as you wrote it` |
  | `{where}` | Planning `, shaping the plan` · Researching `, from each topic's next search` · Evaluating `, in how sources are rated and in the report` · Verifying `, in the report` · Writing `, in this draft` · Reviewing `; the review will check it` · Publishing or no row: nothing |
  | Past two notes | the latest two, then `and {n} earlier notes` (`and 1 earlier note`) |
  | Note pass | Researching's first line `Researching your note: {restatement}` (`Researching your notes: {a}; {b}`); its checklist titles `Your note: {restatement}` |
  | Note redraft (I) | Writing's first line `Rewriting for your note: {restatement}` (`Rewriting for your notes: {a}; {b}`) |
  | Reviewing's outcome (I) | `Sent back to research your note` / `Sent back to research {k} of your notes`; `Sent back to the writer for your note` / `Sent back to the writer for {k} of your notes` |
  | Report | eyebrow `Your notes`; captions `covered`, `couldn't find evidence`, `not addressed in the report` (I, O8), `not checked` (I), `replaced by a later note` (I) |
  | Pass fact | `{research}` then ` · went back once / twice / {k} times for your notes` (`web/lib/format.ts:48-51`, unchanged) |
  | Report line | `Couldn't find evidence for your note: {restatement}` under "What we couldn't confirm"; (I) `We found sources on your note but could not state a checked answer: {restatement}` for a note target a finding answers but no statement states |
- **Theme (D17; `docs/design/running-stage-picks/BRIEF.md:109-125` "Theme rules").**
  - Tokens only.
  - `web/app/globals.css` lines 1–1131 stay the prototype's CSS verbatim: `cd web && npm run -s check:css` prints `OK`.
  - New rules go at the very end of the file, after Phase 2's, inside the app-only section (`/* ═══ 2026-09-27: app-only additions ═══ */`, `globals.css:1133`), with no colour literal.
  - No purple at rest: the note's send is the neutral `.icon-btn`, never `.btn-primary`; the field shows the theme's focus ring only while it has focus (Phase 2 ambiguity 26's reading of D17).
  - One surface per region: the note line is a hairline row of the pipeline card, and "Your notes" is a hairline block of the report card; neither is a box. Text entry looks like text: the field is the borderless `.tx`.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone; captures are `fullPage: true`.
- **Out of Phase 3.** No change to the one-time check's behaviour (Task 8 only caps its two timings, with the notes', at ten minutes: M6), the verifier, `agents/prompts.py`, the quality JSON, the request-attempt budget or the replay runner's pacing; no new session status; no pass counter.

### Conventions every task uses

- **Shell.** Every block runs in bash from the repository root. No shell state survives between blocks: every block that runs Python sets `PY` first, and every `web/` block starts with `cd web`. Line endings are LF.
- **Python.** `PY="$PWD/.venv/bin/python"`.
  - The pytest line is `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest <files> -q`.
  - Four tests cannot pass on this VM, and full-suite runs deselect them: `tests/test_config.py::test_the_evidence_verifier_pipeline_config` needs a `.env`, which does not exist here and must not be created, and three Windows-only path tests in `tests/test_evaluation/test_config.py`. Every full-suite block sets
    ```bash
    DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
    ```
    and runs `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT` (about 160 s). `12 deselected` is the `.env` test, the ten cases of the three Windows-only tests, and the one `live` test that `pyproject.toml` deselects.
- **Web unit tests.** `cd web && npm run -s typecheck` (Vitest's files are type-checked too), `cd web && npx vitest run [files]`, `cd web && npm run -s check:css`.
- **Playwright.** The API runs in replay mode as a Playwright `webServer` (`web/playwright.config.ts`) on port 8010, with the app on 3010 and an app pointed at a closed port on 3011.
  - Set `export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"` at the repository root before `cd web`.
  - Build first, and put spec files **before** the project flag, because `--project chromium` placed first swallows the file arguments: `npm run -s build && npx playwright test e2e/a.spec.ts --project=chromium`.
  - Captures: `VISUAL_CHECKPOINT=<name> npx playwright test --project=visual`, with the images in `web/visual/<name>/` (gitignored).
  - Ports 8010, 3010 and 3011 must be free before a run: `for p in 8010 3010 3011; do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "port $p is in use"; done` must print nothing. A leftover server makes the `webServer` start fail.
  - No step here keeps a server running across blocks. If one is ever started with `web/scripts/launch.mjs`, its `stop` uses Windows `taskkill`: on Linux first end the recorded process group, `kill -- -"$(cat .launch-<name>.pid)"`, and `stop` then only confirms that the port is closed.
- **Edits.** Every edit is "replace this exact text with that text"; "Create" writes a new file; "Append to" adds the block's text at the end of the file. Task 1 proves that each anchor occurs exactly once when its turn comes. If an anchor is not found when you reach it, stop and report it. Never improvise a nearby match.
- **TDD boundary.** pytest and Vitest tests are written first and shown failing. Playwright specs are verification, written after the code they exercise.
- **Commits.** `git add <paths> && git commit -m "<type>(<scope>): <what>"`, never `git add -A` (`web/visual/`, `web/.e2e-tmp/` and `web/test-results/` are gitignored). Append the attribution trailer your session requires, and push if your session's rules say to.

## Review Focus

1. **A note that lands while the review runs, or while its reading is still in flight** (spec §4.8 "A note arrives during Reviewing"). The review node must wait for every received note to be read, take in the ones its input did not carry as unreviewed, and route each to its one redraft; a note the review did read must be marked `reviewed`; and a reading that hangs must not hold the route past `_REVIEW_NOTES_WAIT_S`. → Task 3 `test_the_review_marks_what_it_read_and_waits_for_a_note_still_being_read`, `test_the_review_waits_for_a_reading_no_longer_than_its_own_ceiling`; the AC20 stub graph, where every note arrives during a review.
2. **The per-note guards and the route order.** Halted beats every note; a pass beats a redraft; a flag set by a hop is never bought twice; a note redraft never spends `MAX_WRITER_REDRAFTS`; a note's own targets never buy, spend or exhaust an extra pass; a replaced note routes nowhere. → Task 3's `graph_route` and hop tests, AC17, AC18 and AC20 on the compiled graph.
3. **D10 and byte identity.** No evidence-verifier request may carry a note, whichever agent sends it, and a run without notes must send exactly the requests it sent before this plan. → Task 2 `test_without_notes_every_request_of_a_replay_run_is_byte_identical` (two whole runs pinned), Task 6 `test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does`, and the reviewer's own no-notes schema test.
4. **The interpreter and the note window.** A reading that fails, hangs, answers nonsense or is missing keeps the note; the eleventh note and a note after publishing begins are refused and never counted; a service shutdown drops a note still being read without leaving anything waiting on it. → Task 7 `test_an_invalid_reading_is_refused_whole` (10 cases), Task 8 `test_a_failed_slow_or_missing_interpreter_keeps_the_note_as_written` (3 cases), `test_notes_close_while_the_session_waits_once_publishing_begins_and_once_it_ends`, `test_closing_the_store_drops_a_note_still_being_read`.
5. **The page reads notes from the stream alone.** A reload or a burst must paint the same acknowledgements; the line must disable after the tenth note with nothing added; the closed caption must replace the line; nothing on the card may be purple at rest. → Task 9 `is burst-safe`, Task 10 `NoteLine` tests, Task 11 `notes.spec.ts` (AC15 timing, AC20 limit, the closed caption through a recorder).

## Spec ambiguities resolved here

**API and engine**

1. **The interpreter's shape.** `NoteInterpreter = (text, question, earlier_notes, settings) -> NoteInterpretation`, with `settings` the request's own `ConfigSettings`, as Phase 2's checker receives them (its ambiguity 1). The live interpreter is Phase 2's `live_clarity_check` (`api/clarify.py:192-221`) over again: a fresh chat adapter per call from `clarity_llm_config(settings.llm)` (thinking disabled), a private `Tracker` with tracing disabled, `agent_name=None` and `max_tokens=2048`; it raises on an invalid reading so the store falls back, and the store, not the interpreter, owns the `asyncio.timeout`. Which interpreter runs is decided by the API mode, as the checker is: replay can never reach a provider.
2. **Ids.** `n1`…`n10`, assigned at receipt in order; a refused note gets no id and is not counted (D11a).
3. **A note is one line.** Its text is collapsed to single spaces before the 1–500 check, as Phase 2's answers are (its ambiguity 32), and every text field of a reading is collapsed too. A note kept as written becomes its own restatement, and a restatement is printed as one line that the renderer starts with `- ` and ends with ` ({kinds})`, so reader text never starts a line of its own (spec §4.6: "Its lines never begin with the packet patterns the replay harness parses"). The harness's whole-line readers (`e2e_evaluation/replay.py:531`, `:544`, `:576`) therefore never match a note line. Two of its readers instead search the whole packet for `- target_id=` and take the first match (`replay.py:909`, `:1032`); both first matches stay the packet's own: a researcher decision turn prints its notes after the acquisition context that carries that line, and the extraction request prints `# Reader notes` last, after `# Retrieved evidence`, whose first line is that line (Task 4; review round 1, P3-2). One replay-only, adversarial case is left: `_planned_target_ids` (`replay.py:1117-1123`) collects every line shaped like a planned target wherever it stands, so a restatement written as `topic-02-target-09 [topic-02]: …` would add that id to replay's scripted findings, and the researcher then drops an id outside the plan's inventory and keeps the finding (`agents/researcher.py:2075-2078`).
4. **An invalid reading is refused whole**: no kind, more than three, one outside the vocabulary or repeated; an empty restatement or one over 120 characters; more than three new questions, or one empty or over 200 characters; a scope field over 120 characters. A `replaces` that names no earlier note of this run is dropped instead, and the reading stands.
5. **When notes close.** "Once `finalize_report` has started" is read from the session's own stream: the first `graph.route.decided` whose destination is `finalize` or `end` closes notes — the event that makes Publishing the running row. A loop decision (`note_pass`, `extra_pass`, `redraft`) keeps them open. `needs_input`, a terminal status and `finished_at` (a stopped run) close them too.
6. **The board's lifetime.** One board per session, created with it and bound around the creation of the session's task, so the one-time check, the runner and every node see the same board. A note sent while the check waits is refused (`needs_input`); one sent during the live check call (status `running`) is taken, and the planner plans with it.
7. **How a node takes in the board.** `agent_node` merges only the board's notes the state does not hold yet, not `board.snapshot()` wholesale as §4.6 writes it: the board carries none of the graph's flags (`reviewed`, `passed`, `redrafted`), and a wholesale replace would erase them.
8. **The review node** is not an `agent_node`, so it merges the board itself. After the review it waits until no note is still being read (`NoteBoard.settled`), marks every active note its input carried `reviewed`, and adds the notes that arrived meanwhile unreviewed — which is what buys a note sent during Reviewing its redraft. The wait has its own ceiling, `_REVIEW_NOTES_WAIT_S = 30.0` seconds (`graph/nodes.py`, review round 1, P3-3): twice `note_interpret_timeout_s`'s default of 15 s, so with the default every reading has ended — read, or kept as written — before the review stops waiting; while an operator's raised timeout (M6 allows up to 600 s) can hold the route decision for 30 s at most instead of ten minutes. The cost of the bound: a note still being read when it passes is left out of this route decision, unreviewed. If the route loops, the next node takes the note in once it is read (`agent_node`, or the review node's own merge) and a later review judges it; if the route publishes, the note ends `pending` (`not checked`), as O4's note does.
9. **Which notes are rendered.** Only interpreted notes that no later note replaces. `new_angle` notes are kept out of a running research loop, as §4.6 says, and also out of the researcher's extraction, which reads the same set.
10. **The researcher's per-turn block.** The decision context is rebuilt before every provider turn (`agents/base.py:381-395`), so each turn renders every active note but the `new_angle` ones — from the state and the board — under `## Reader notes` after the acquisition context. That is §4.6's "notes newer than the loop's last-seen id are added": this codebase keeps no accumulating transcript to add them to.
11. **The planner's scoping turn.** `PlannerAgent.build_decision_context` returns the block, which the shared prompt module prints under `## Acquisition context` (`agents/prompts.py:259-260`). `prompts.py` is not edited (see O6).
12. **The review's verdicts.** Every schema field must appear in the request's reply-format example (`tests/test_agents/test_tool_free_prompts.py:861-862`), and a request without notes must stay byte-identical. So the review asks for `note_dispositions` through two schemas of its own, `ReportReviewNotesDraft` and `ScopedReportReviewNotesDraft`, used only when the packet carries notes. An unknown id, a status outside the vocabulary or a second verdict for a note is dropped, never a reason to refuse the review. A scoped re-review carries the previous verdicts for the notes it does not judge again.
13. **Dispositions are not a rubric dimension.** `ReportReview.note_dispositions` defaults to `[]` and never enters the acceptance rule; the quality record does not publish it (`agents/report.py:422-459` lists its fields explicitly).
14. **A note's sub-topic.** Coverage id `note-{id}`, title `Your note: {restatement}`, one required target per new question (`note-{id}-target-NN`) with the note's scope as geography and period; a note that raised no question gets one target whose question is its restatement. Its priority is the plan's largest plus one, so it sorts after every planned topic.
15. **Confining the note pass.** The note pass hands the researcher its targets through `extra_pass_target_ids`, exactly as the extra pass does (`agents/researcher.py:307-331`). `extra_pass_target_ids()` leaves `note-*` targets out, so a note's target never buys, spends or exhausts an extra pass (see O1).
16. **The note redraft.** Destination `redraft` with reason `note_redraft_requested`. The hop flags the due notes `redrafted` and emits its own `graph.note_redraft.requested`, instead of spending the review's writer re-run. The writer drafts every part afresh after either note loop (its redraft check reads the latest loop marker), and the reviewer then makes a full review, not a scoped one. A scoped redraft (only the parts a defect names, as the review's own re-run does) was not chosen because a note has no parts to scope to: it applies to the whole report — an emphasis reweights every section, an exclude or a scope note can remove material from any of them — and the review names no defect for it, only a disposition. The cost, stated (review round 1, P3-8): one note redraft re-drafts every section and buys a full review, at most once per note, so at most ten times a run (D11a), within the recursion limit (ambiguity 20).
17. **Status and events.** Both note reasons map to `incomplete`, like the other loop reasons, and are never a run's last decision. `graph.note_pass.started` carries `{iteration, note_passes, note_ids, targets}`, a superset of §4.6's `{note_ids, targets}`; `iteration` is unchanged by a note pass.
18. **The report's lines.** Each note sub-topic with an unanswered target is listed once, as `Couldn't find evidence for your note: {restatement}`, among "What we couldn't confirm"'s groups, instead of its questions under "We found no source we could check that answers:" or "This run did not research:". Each note sub-topic with a target that a verified finding answers but no printed statement states is listed once too, as `We found sources on your note but could not state a checked answer: {restatement}`, right after the group "We found sources on these but could not state a checked answer:", instead of its questions in that group (review round 1, P3-1). So no question the run derived from a note is ever printed: the reader wrote the note, not the questions. `answered_not_stated_targets` itself is unchanged, so the quality gate that exempts a disclosed target reads the same set as before (`agents/report.py:1461-1469`).
19. **Outcomes.** `covered` when the last review judged the note `honoured`; `not_found` for `no_evidence`; `not_addressed` for `ignored_with_evidence` — the findings bore on the note and the report still does not follow it, after its one redraft or because the run ended before it (O8); `replaced` when a later note replaced it (O3); `pending` otherwise — while the run goes on, and when no review judged it. A note the report ignores is never reported `covered`. `note_passes` is the finished state's count, or the stream's latest `graph.note_pass.started` while the run goes on. `clarification` is the one-time check's questions and the answers the run started with, `null` when it asked none.
20. **The recursion limit** is §4.6's formula with `NOTE_REDRAFT_STEPS = 3` (the redraft hop, the writer, the reviewer). `NODE_NAMES` now has ten entries, so `graph_recursion_limit(1)` is 160.

**Web**

21. **The acknowledgements' place.** A reopened row's reason stays its first line (§4.3), and the acknowledgements follow it, before the step's own content: the note pass's "first line" and the acks' "top of the brief" both hold.
22. **`{where}`** is read from the row that was running when `session.note.interpreted` arrived; with Publishing or no row running it is empty.
23. **Past two notes**: the two latest in receipt order, then one line `and {n} earlier notes`, `note` singular for one.
24. **Several notes in one loop**: `Researching your notes: {a}; {b}`. The note redraft's first line and Reviewing's outcome lines for the two note routes are this plan's words (the spec names none); see the copy table.
25. **The three unnamed outcomes** read `not checked`, `replaced by a later note` (O3) and `not addressed in the report` (O8).
26. **Notes left on the page**: the smaller of the last `/status`'s `notes_remaining` and ten less the notes the stream has received, so the line disables with no failed POST, even between status reads. The response fields are optional in `web/lib/api.ts`, because the replay captures under `web/test/fixtures/events/` predate them.
27. **The acknowledgement's motion.** A new line rises 4px and fades in over 200ms from its `@starting-style`: at once in a row that is already open, and in its place in the stagger in the row a hand-off opens. Under reduced motion it fades in place over 160ms, as every brief line does (`globals.css:1324`).
28. **Other POST failures** (a 5xx, a network error, any refusal but the two 409s) keep the text and show one `.cap` under the field, `Couldn't send — try again` (this plan's words; the spec names only the two 409s), until the reader edits the note or sends it again (review round 1, P3-6). It is a polite status (`role="status"`), and nothing else on the line changes.
29. **Class names.** The note line is `.note-line`: the prototype's `.note` is the status box (`globals.css:934-939`). The send is the plain `.icon-btn` (36px, 44px at phone width, `globals.css:173-178`, `:987`), not the pick's `.icon-btn.sm`, which the app does not have. The field is Phase 2's `.tx` (`globals.css:1352-1355`: transparent, a soft ground on hover, the focus ring on focus), which is global and sized for the check card (a 44px min-height, 8px padding); `.note-line .tx` gives the line its own 36px min-height and 6px padding, and Phase 2's rule stays as it is.
30. **The acknowledgements are announced.** Each note's ack line is its own polite live region (`aria-live="polite"`, `aria-atomic="true"`): nothing moves focus, as Phase 2's check card does, so a screen-reader user hears the line whole when it changes from `Reading your note…` to `Got it — …` (review round 1, P3-7). The `and {n} earlier notes` line is not a live region: it only counts.

## Phase 2's final-review notes, and where each is met

| Note | What it asked | Where |
|---|---|---|
| R1 | `SessionStore.start` takes the request's `settings` (typed `Any`) and passes it only to `_run`; the notes need the session's settings and `hitl.note_interpret_timeout_s` | Task 8: `ResearchSession.run_settings` and `ResearchSession.hitl`, set by `start`; `_interpret_note` reads both |
| R2 | `_clarify` drops the questions and the resolved answers after the wait; the status's `clarification: {questions, answers}` needs them | Task 8: `ResearchSession.check` (`CheckRecord`), set when the check asks and again with the answers the run starts with; `test_the_status_carries_the_one_time_check_the_session_asked` |
| R3 | `.tx` is global and sized for the check card | Task 10: `.note-line .tx` overrides the size (ambiguity 29) |
| R4 | the live clarity checker is the template for the interpreter | Task 7: `live_note_interpreter` (ambiguity 1) |
| M6 | the three `HitlConfig` timings accept `inf` and huge values (`1e12` overflows the deadline, so the session fails instead of the request) | Task 8: each is finite and at most `HITL_TIMING_MAX_S = 600`; `tests/test_config.py` and a `422` through the route |
| R6 | the dead `RunState` fields `tag`, `blurbs`, `maxPasses`, `BLURB` and `LoopTag` | Task 13, after every other web task; with them go the `graph.session.started` handler and the `passes` argument of `newRunState` and `replayRun`, which only fed `maxPasses` |
| O2 | live mode shows Planning for up to `check_timeout_s` before the check's card | Left to the human, as asked: nothing here changes it |

## Open issues (for the human)

- **O1 — A note's targets are `required = true` but are kept out of the extra-pass budget.** §4.6 makes them required. Every required target counts in the report's quality snapshot (`agents/quality.py:83-84`), so an unanswered note target is listed under "What we couldn't confirm" and counts among the snapshot's missing targets. But `extra_pass_target_ids()` feeds both the extra-pass route and `extra_passes_exhausted`, and a note target counted there would spend the extra-pass budget (against D11's "outside the extra-pass budget") or end an otherwise complete run as `max_iterations`. Resolution kept: required everywhere but `extra_pass_target_ids()` (ambiguity 15).
- **O2 — On the replay server a note is never applied.** `ReplayRunner` runs the graph at full speed and paces only the stream (`api/replay.py:64-141`): by the time the page shows Researching, the engine has finished. A note sent then is received, read and acknowledged, and ends `pending` (`not checked`). The engine's use of notes is proven offline instead, on the real graph with a bound board (Tasks 2–6), and the page's flow on the replay server (Task 11); DESIGN.md and `api-gaps.md` gap 3.9 say so. A replay runner that holds each node until the stream has published its start would close it; that changes the replay contract and is not planned.
- **O3 — The outcome enum gains `replaced`.** §4.6 lists `covered | not_found | pending`. A note a later note replaced is never judged, and `pending` would read as "still to come" forever. Resolution kept: a fourth value, `replaced`, rendered `replaced by a later note`.
- **O4 — A note in the last instant before the route is published is taken and never applied.** Between the review node's wait for readings and the orchestrator's publication of its route decision, a note can still be accepted: it misses the decision, and publishing then closes notes. It ends `pending` (`not checked`). The window is one node's return; closing it would need the route decided under a lock the notes route shares.
- **O5 — The interpreter's call is outside the run's request-attempt budget and its telemetry**, like Phase 2's check call (its O1): the budget belongs to the runner, and a note arrives from outside the run. One structured request of at most 2048 output tokens per note, at most ten per run, each within `hitl.note_interpret_timeout_s` (15 s). However high that timeout is set (M6 caps it at 600 s), the review node waits for a reading at most `_REVIEW_NOTES_WAIT_S` (30 s) before it reads its route (ambiguity 8).
- **O6 — The planner's scoping block is printed under `## Acquisition context`.** The shared prompt module prints a decision context under that heading (`agents/prompts.py:259-260`), and editing it would move every agent's fingerprint, the evidence verifier's included. The block's own lead sentence says what it is.
- **O7 — The live structured call is [INFERENCE].** `NoteInterpretationDraft` goes through the provider's structured-output path like Phase 2's `ClarityCheckDraft`. No test may call a provider; a rejected schema would fall back safely (every note kept as written, `fallback: true`). One live note by the owner settles it; that is outside this plan.
- **O8 — The outcome enum gains `not_addressed`: a note the report still ignores is never `covered`.** §4.6 lists `covered | not_found | pending`, and §4.7's captions are `covered` and `couldn't find evidence`; neither names a note the last review judged `ignored_with_evidence` — the findings bore on it and the report does not follow it, after its one redraft (D11, AC18) or because the run ended before that redraft. The first draft of this plan mapped it to `covered`, which tells the reader the opposite of what happened (review round 1, P2-1). Resolution (the controller's ruling on P2-1, 2026-09-29; the human may rename the value or its caption): a fifth value, `not_addressed`, captioned `not addressed in the report`, carried through `note_outcome` and `NoteOutcome` (Task 7), `ReaderNoteResponse.outcome` and the status response (Tasks 7–8), `ReaderNoteOutcome` and `OUTCOME_TEXT` (Task 9), "Your notes" (Task 10), the README (Task 8) and DESIGN.md (Task 12). No event carries an outcome: `session.note.interpreted` carries the reading only, and the two graph note events carry note ids (Tasks 3, 7), so no event changes.

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/utils/types.py` | `MAX_NOTES_PER_RUN`, `NOTE_COVERAGE_PREFIX`, `NOTE_TOPIC_TITLE_PREFIX`, `ReaderNoteKind`, `ReaderNoteScope`, `ReaderNote`, `NoteDisposition`, `active_reader_notes`, `with_board_notes`; `ReportReview.note_dispositions`; `ResearchState.reader_notes` / `note_passes` and their update keys | 2 |
| `src/deep_research/runtime/notes.py` (new) | `NoteBoard` (receive, add, drop, settle, snapshot), `NoteLimitReached`, `ReceivedNote`, the ContextVar binding | 2 |
| `src/deep_research/agents/reader_notes.py` (new), `agents/__init__.py` | each step's lead sentence, the one renderer, the board read at call time; the package's exports | 2, 3, 6 |
| `src/deep_research/graph/nodes.py` | every node starts from the board's notes (2); the review node's reviewed/late notes and its bounded wait (`_REVIEW_NOTES_WAIT_S`), the note redraft, `note_sub_topic`, `note_pass_node` (3) | 2, 3 |
| `src/deep_research/graph/state.py`, `graph/events.py`, `graph/orchestrator.py`, `graph/__init__.py` | the two note routes, `NOTE_PASS_NODE`, the recursion limit, the two note events, the `note_pass` node and edge | 3 |
| `src/deep_research/agents/planner.py`, `agents/researcher.py` | the notes in planning, scoping, each research turn and extraction | 4 |
| `src/deep_research/agents/source_evaluator.py`, `agents/report_writer.py`, `agents/report.py` | the notes in scoring and writing; a fresh draft after a note loop; the report's two note lines (uncovered, and answered but not stated) | 5 |
| `src/deep_research/agents/report_reviewer.py` | the notes in the review's packet and both its fingerprints (3); the review's notes block, `note_dispositions`, the two notes schemas (6) | 3, 6 |
| `src/deep_research/e2e_evaluation/replay.py` | replay's answer to the two notes schemas | 6 |
| `src/deep_research/api/notes.py` (new), `api/models.py` | the interpreter (live, scripted, fallback), the two events, outcomes; the note request and response shapes | 7 |
| `src/deep_research/api/sessions.py`, `api/app.py` | `add_note`, the reading task, the notes window, the board binding, the session's settings, timings and check record, the route, the status fields | 8 |
| `src/deep_research/utils/config.py` | `HITL_TIMING_MAX_S`; the three `HitlConfig` timings finite and at most ten minutes | 8 |
| `README.md`, `docs/design/api-gaps.md` | the notes route in the API's documentation; the response's 22 fields; gap 3.9 | 8 |
| `tests/graph_fakes.py`, `tests/test_runtime/test_note_board.py`, `tests/test_graph/test_reader_notes_state.py`, `tests/test_graph/test_reader_notes_replay.py` (new) | board, state and replay tests | 2 (replay: 2, 4, 5, 6) |
| `tests/test_graph/test_note_routing.py` (new), `tests/test_graph/test_state.py`, `tests/test_graph/test_orchestrator.py` | routing tests; the node list, reasons and limit | 3 |
| `tests/test_agents/test_reader_notes_planning.py`, `…_writing.py`, `…_review.py` (new), `tests/test_evaluation/test_config.py` (pins) | per-consumer request tests; the four re-pins | 4, 5, 6 |
| `tests/test_api/test_notes.py`, `tests/test_api/test_note_route.py` (new), `tests/test_api/conftest.py`, `tests/test_config.py` | service, lifecycle and route tests; the live-interpreter guard; the timings' caps | 7, 8 |
| `web/lib/api.ts`, `web/lib/notes.ts` (new), `web/lib/run-state.ts`, `web/lib/briefs.ts` | the note types and `addNote`; the notes' words; `RunState.notes` and the note loops (9), and no dead field (13); the running row's acknowledgements | 9, 13 |
| `web/components/NoteLine.tsx` (new), `BriefSpine.tsx`, `RunningPipeline.tsx`, `SessionScreen.tsx`, `ReportBody.tsx`, `ReportStage.tsx`, `ReportRail.tsx`, `web/app/globals.css` | the note line, the ack lines, "Your notes", the pass fact, the styles (10); `newRunState()` without its dead argument (13) | 10, 13 |
| `web/test/notes.test.ts`, `web/test/run-state.test.ts`, `web/test/components/note-line.test.tsx`, `web/test/components/reader-notes.test.tsx` | Vitest (9, 10); the run state's keys, and no `passes` argument (13) | 9, 10, 13 |
| `web/test/briefs.test.ts`, `web/test/clarify.test.ts`, `web/test/components/brief-spine.test.tsx`, `clarify-stage.test.tsx`, `failed-stage.test.tsx`, `running-pipeline.test.tsx`, `spine.test.tsx` | `newRunState()` without its dead argument | 13 |
| `web/e2e/support.ts`, `web/e2e/notes.spec.ts` (new), `web/e2e/visual.spec.ts` | Playwright and the `11-note-ack`, `12-report-notes` captures | 11 |
| `docs/design/DESIGN.md`, `web/README.md` | the design record; §3.1 and §6 brought up to `GET /research` and the notes route | 12 |

---

### Task 1: Re-anchor check and baselines

**Files:** none changed. This task only reads: the repository at the end of Phase 2, and this plan.

**Interfaces:**
- Consumes: Phase 2 complete, i.e. its Tasks 1–11 done and committed.
- Produces: the proof that every block of Tasks 2–13 applies in order — each anchor exactly once when its turn comes, each created file absent, each appended file present — and the baselines that later tasks add to.

- [ ] **Step 1: Confirm the starting point**

```bash
git log --oneline | grep -F "docs(design): the one-time check — stage 2a, needs_input, the settings row"
test -f src/deep_research/api/clarify.py && test -f web/components/ClarifyStage.tsx && test -f web/e2e/clarify.spec.ts && echo "Phase 2 files present"
test ! -e src/deep_research/runtime/notes.py && test ! -e web/components/NoteLine.tsx && echo "no Phase 3 file yet"
git status --short | wc -l
```

Expected: one commit line (Phase 2 Task 10's), then `Phase 2 files present`, then `no Phase 3 file yet`, then `0` (a clean tree). If the commit or the files are missing, stop: this plan starts where Phase 2 ends.

- [ ] **Step 2: Check every block of this plan against the tree, in order**

The check simulates the whole plan in memory — later anchors are checked against the text earlier blocks write — and changes nothing on disk.

```bash
PY="$PWD/.venv/bin/python"
"$PY" - <<'EOF'
import re
from pathlib import Path

plan = Path("docs/superpowers/plans/2026-09-29-live-briefs-phase3-reader-notes.md").read_text(encoding="utf-8")
edit = re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? — replace\n\n(?P<f1>`{3,4})[a-z]*\n(?P<old>.*?)\n(?P=f1)\n\nwith\n\n(?P<f2>`{3,4})[a-z]*\n(?P<new>.*?)\n(?P=f2)\n", re.S | re.M)
create = re.compile(r"^Create `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,4})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
append = re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n\n(?P<f>`{3,4})[a-z]*\n(?P<body>.*?)\n(?P=f)\n", re.S | re.M)
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

Expected: `anchors: 280 exactly once; creates: 18 absent; appends: 3 onto files present`.

Anything else is a line per problem (`-1` means that the file is missing). Then:
- Stop, and edit nothing.
- Report each line to the controller.

The usual cause is a Phase 2 review fix that changed text after this plan was written; the edits marked "(anchor as written by Phase 2 Task N)" are the ones to compare. To find it, compare `git log -p -- <file>` with the edit this plan gives for that file. The remedy is a plan amendment that the controller approves: re-anchor that edit on the text now in the file, and keep its replacement's meaning. Never guess an anchor during execution.

- [ ] **Step 3: Record the suite baselines**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT
```

Expected: `4834 passed, 6 skipped, 12 deselected` (observed in planning at `539d51f`). The Phase 2 plan's own final count is `4833`; its execution added `tests/test_api/test_clarify.py::test_check_text_from_the_model_is_collapsed_to_one_line_each`.

```bash
cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
```

Expected: no `typecheck` output; `Test Files  25 passed (25)`, `Tests  194 passed (194)`; `OK` (the Phase 2 plan's `190`, plus the four clarify-stage tests its execution and fixes added).

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npx playwright test --list --project=chromium | tail -1 && npx playwright test --list --project=visual | tail -1 && npm run -s test:e2e 2>&1 | tail -3
```

Expected: `Total: 55 tests in 18 files`, `Total: 10 tests in 1 file`, then `55 passed`.

- [ ] **Step 4: Record the whole-run request digests Task 2 pins**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - <<'EOF'
import asyncio, hashlib, tempfile
from pathlib import Path
from deep_research.e2e_evaluation.replay import build_replay_runtime, network_denied, offline_credentials, production_config_path, replay_settings
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research

async def sequence(case_id: str, root: Path):
    scenario, held = scenario_by_id(case_id), {}
    async def builder(current, *, session_id):
        replay = await build_replay_runtime(scenario, root=root, session_id=session_id, settings=replay_settings(scenario, root=root, base=current))
        held["completer"] = replay.completer
        return replay.runtime
    await run_research(question=scenario.question, session_id="s1", config_path=str(production_config_path()), max_extra_passes=scenario.max_extra_passes, runtime_builder=builder)
    return held["completer"].packet_sequence

for case_id in ("missing-target-triggers-one-extra-pass", "scoped-redraft-after-a-named-defect"):
    with tempfile.TemporaryDirectory() as tmp, offline_credentials(), network_denied():
        lines = sorted(f"{key} {hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}" for key, text in asyncio.run(sequence(case_id, Path(tmp))))
    print(case_id, (hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16], len(lines)))
EOF
```

Expected:
```text
missing-target-triggers-one-extra-pass ('03e113e5584707da', 46)
scoped-redraft-after-a-named-defect ('875313d15f3325f2', 29)
```

These are Task 2's `PINNED_RUN_DIGESTS`: every request the two runs send, as the end of Phase 2 sends them. If either value differs, Phase 2's execution changed a request after planning (a review fix, say). Then use the observed pair in Task 2's `PINNED_RUN_DIGESTS` instead, and record both values in the task summary: the pin is this plan's baseline, and Tasks 2–14 must keep whatever the start was.

- [ ] **Step 5: Record the per-file baselines that the scoped runs build on**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_state.py tests/test_graph/test_orchestrator.py -q | tail -1
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint | tail -1
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q | tail -1
cd web && npx vitest run test/run-state.test.ts test/briefs.test.ts 2>&1 | grep -E "Tests  " && npx vitest run test/components 2>&1 | grep -E "Tests  "
```

Expected, one line each (observed in planning at `539d51f`): `50 passed`; `11 passed, 67 deselected`; `180 passed`; `Tests  36 passed (36)`; `Tests  85 passed (85)`.

If a baseline differs from the Expected above — because Phase 2's verification or a later review fix added or removed tests after `539d51f` — record the observed value and use it: every later count is then shifted by the same amount. What must hold is that the named new tests pass (or, in a fail-first step, fail) and nothing else fails. The increments are:

| Suite | Increments by task |
|---|---|
| Backend | +17 (Task 2), +14 (Task 3), +5 (Task 4), +4 (Task 5), +7 (Task 6), +20 (Task 7), +28 (Task 8): 95 in all |
| Vitest | +10 (Task 9), +12 (Task 10), +1 (Task 13) |
| Chromium | +6 (Task 11) |
| Visual | +2 (Task 11) |

If a baseline run **fails**, stop and report it: Phase 3 starts from a green Phase 2.

- [ ] **Step 6: No commit**

Nothing changed. Task 1's summary lists the anchor line, the three suite baselines, the two digests and the five per-file baselines.

---

### Task 2: The note board, the notes in the run's state, and every node's starting notes (spec §4.6 "The note board", "Where each step uses notes")

**Files** (line numbers locate the anchors at the end of Phase 2):
- Create: `src/deep_research/runtime/notes.py`, `src/deep_research/agents/reader_notes.py`, `tests/test_runtime/test_note_board.py`, `tests/test_graph/test_reader_notes_state.py`, `tests/test_graph/test_reader_notes_replay.py`
- Modify: `src/deep_research/utils/types.py:1140` (the note types, before `REVIEW_RUBRIC_VERSION`), `:1249` (`ReportReview.note_dispositions`), `:2001`, `:2041` (the state and update fields, after Phase 2's `reader_answers`); `src/deep_research/agents/__init__.py:238`, `:718` (exports); `src/deep_research/graph/nodes.py:35`, `:110` (imports), `:190` (`_board_notes_update`), `:210` (`agent_node`)
- Test: `tests/graph_fakes.py:44`, `:367`, `:491`, `:512` (`fake_reader_note`; `fake_report_review(note_dispositions=…)`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - In `deep_research.utils.types`: `MAX_NOTES_PER_RUN = 10`, `NOTE_COVERAGE_PREFIX = "note-"`, `NOTE_TOPIC_TITLE_PREFIX = "Your note: "`; `ReaderNoteKind`, `NoteDispositionStatus`; `ReaderNoteScope(geography, period)`; `ReaderNote(ContractModel)` with `note_id` (`n1`–`n10`), `text` (1–500), `received_at`, `received_during`, `kinds` (1–3), `restatement` (1–500), `scope`, `new_questions` (≤ 3), `replaces`, and the flags `reviewed`, `passed`, `redrafted` (default `False`); `NoteDisposition(note_id, status)`; `active_reader_notes(notes)` (drops every note a later one replaces); `with_board_notes(existing, board)` (the state's notes, then the board's notes it does not hold); `ReportReview.note_dispositions: list[NoteDisposition] = []`; `ResearchState.reader_notes: list[ReaderNote] = []` (replace-on-write) and `note_passes: int = 0`, both keys in `ResearchStateUpdate`.
  - `deep_research.runtime.notes`: `NoteBoard` with `receive(text, *, received_at, received_during) -> ReceivedNote` (numbers `n1`…, raises `NoteLimitReached` past the tenth), `add(note)`, `drop(note_id)`, `interpreted(note_id)`, `snapshot()` (deep copies, receipt order), `received()`, `accepted`, `remaining`, `pending`, `async settled()`; `bind_note_board(board)` (a context manager) and `current_note_board()`.
  - `deep_research.agents.reader_notes`: `PLANNING_NOTES`, `RESEARCH_NOTES`, `EXTRACTION_NOTES`, `SOURCE_NOTES`, `WRITING_NOTES`, `REVIEW_NOTES` (each step's lead sentence); `render_reader_notes(notes, *, instruction, with_ids=False) -> str` (`""` without notes); `board_notes()`, `async notes_settled(*, timeout=None) -> bool` (`False` when `timeout` seconds passed with a note still being read), `live_reader_notes(state_notes)`, `research_reader_notes(notes)`; all exported from `deep_research.agents`.
  - `agent_node` starts every agent from the state plus the board's new notes (`_board_notes_update`, `{}` when there is none).
  - `tests/graph_fakes.py`: `fake_reader_note(note_id="n1", **overrides)` and `fake_report_review(..., note_dispositions={id: status})`.

The agents never import `runtime.notes` at module level: `deep_research.runtime` imports every agent while its package initialises, so `agents/reader_notes.py` reaches the board at call time, as `agents/events.publish_live` reaches the live sink.

- [ ] **Step 1: Write the failing tests**

The board tests drive `NoteBoard` directly. The state tests build notes with the new fake, run the stub graph with a bound board and read what each agent was handed. The replay file pins every request of two whole runs without notes (the digests Task 1 Step 4 recorded) and holds the helpers Tasks 4–6 append to.

Create `tests/test_runtime/test_note_board.py`:

```python
"""The run's note board (live-briefs spec §4.6, D11a): receive, interpret, settle, bind."""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.reader_notes import board_notes, notes_settled
from deep_research.runtime.notes import (
    NoteBoard,
    NoteLimitReached,
    bind_note_board,
    current_note_board,
)
from deep_research.utils.types import MAX_NOTES_PER_RUN
from tests.graph_fakes import fake_reader_note

AT = "2026-09-29T10:00:00.000+00:00"


def _receive(board: NoteBoard, text: str = "Focus on grid storage."):
    return board.receive(text, received_at=AT, received_during="researcher")


def test_notes_are_numbered_in_receipt_order_and_readable_only_once_interpreted() -> None:
    board = NoteBoard()
    first, second = _receive(board, "one"), _receive(board, "two")

    assert (first.note_id, second.note_id) == ("n1", "n2")
    assert (first.text, first.received_at, first.received_during) == ("one", AT, "researcher")
    assert board.accepted == 2 and board.remaining == MAX_NOTES_PER_RUN - 2
    assert board.pending == ("n1", "n2")
    assert board.snapshot() == [] and board.interpreted("n1") is None

    board.add(fake_reader_note("n2"))
    board.add(fake_reader_note("n1"))

    assert [note.note_id for note in board.snapshot()] == ["n1", "n2"]
    assert board.pending == ()
    assert [received.note_id for received in board.received()] == ["n1", "n2"]


def test_the_eleventh_note_is_refused_and_never_counted() -> None:
    board = NoteBoard()
    for _ in range(MAX_NOTES_PER_RUN):
        _receive(board)

    with pytest.raises(NoteLimitReached):
        _receive(board)
    assert board.accepted == MAX_NOTES_PER_RUN == 10
    assert board.remaining == 0
    assert board.received()[-1].note_id == "n10"


def test_a_note_is_added_once_and_only_after_it_was_received() -> None:
    board = NoteBoard()
    _receive(board)

    with pytest.raises(ValueError, match="never received"):
        board.add(fake_reader_note("n2"))
    board.add(fake_reader_note("n1"))
    with pytest.raises(ValueError, match="already settled"):
        board.add(fake_reader_note("n1"))


def test_the_snapshot_is_a_copy_the_caller_cannot_change() -> None:
    board = NoteBoard()
    _receive(board)
    board.add(fake_reader_note("n1"))

    board.snapshot()[0].kinds.append("exclude")

    assert board.snapshot()[0].kinds == ["emphasis"]
    assert board.interpreted("n1") == fake_reader_note("n1")


@pytest.mark.asyncio
async def test_settled_waits_for_every_received_note_to_be_added_or_dropped() -> None:
    board = NoteBoard()
    await asyncio.wait_for(board.settled(), timeout=1)  # nothing received yet
    _receive(board)
    _receive(board)

    waiter = asyncio.create_task(board.settled())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.add(fake_reader_note("n1"))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.drop("n2")
    await asyncio.wait_for(waiter, timeout=1)

    assert board.pending == ()
    assert [note.note_id for note in board.snapshot()] == ["n1"]
    assert board.interpreted("n2") is None


@pytest.mark.asyncio
async def test_the_board_is_bound_for_a_block_and_reaches_tasks_started_inside_it() -> None:
    board = NoteBoard()
    _receive(board)
    board.add(fake_reader_note("n1"))

    async def read() -> list[str]:
        await notes_settled()
        return [note.note_id for note in board_notes()]

    assert current_note_board() is None
    assert board_notes() == []
    await notes_settled()  # no board bound: returns at once
    with bind_note_board(board):
        assert current_note_board() is board
        task = asyncio.create_task(read())
    assert current_note_board() is None
    assert await task == ["n1"]
```

Create `tests/test_graph/test_reader_notes_state.py`:

```python
"""The reader's notes in the run's state, and how each node starts from them (live-briefs spec §4.6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    live_reader_notes,
    render_reader_notes,
    research_reader_notes,
)
from deep_research.graph.orchestrator import compile_research_graph, run_research_graph
from deep_research.graph.state import dump_state, initial_graph_state, load_state
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.types import (
    NoteDisposition,
    ReaderNote,
    ReportReview,
    ResearchState,
    active_reader_notes,
    merge_research_state,
    with_board_notes,
)
from tests.graph_fakes import (
    FakeAgent,
    fake_reader_note,
    fake_research_agents,
    verified_pass,
)

QUESTION = "How mature is quantum error correction?"
AT = "2026-09-29T10:00:00.000+00:00"


def test_a_reader_note_holds_its_reading_and_starts_unflagged() -> None:
    note = fake_reader_note("n1", kinds=["scope", "exclude"], scope={"geography": "United States"})

    assert note.model_dump(mode="json") == {
        "note_id": "n1",
        "text": "Focus on grid storage (n1).",
        "received_at": "2026-09-29T10:00:00+00:00",
        "received_during": "researcher",
        "kinds": ["scope", "exclude"],
        "restatement": "more weight on grid storage (n1)",
        "scope": {"geography": "United States", "period": None},
        "new_questions": [],
        "replaces": None,
        "reviewed": False,
        "passed": False,
        "redrafted": False,
    }
    for bad in (
        {"note_id": "n11"},
        {"note_id": "n0"},
        {"kinds": []},
        {"kinds": ["emphasis", "exclude", "scope", "new_angle"]},
        {"kinds": ["urgent"]},
        {"restatement": "   "},
        {"new_questions": ["a", "b", "c", "d"]},
        {"text": "x" * 501},
    ):
        with pytest.raises(ValidationError):
            ReaderNote.model_validate({**note.model_dump(), **bad})


def test_a_later_note_replaces_the_one_it_names_and_the_state_keeps_its_flags() -> None:
    first = fake_reader_note("n1")
    second = fake_reader_note("n2", replaces="n1")
    third = fake_reader_note("n3")

    assert [note.note_id for note in active_reader_notes([first, second, third])] == ["n2", "n3"]
    held = [first.model_copy(update={"reviewed": True, "passed": True})]
    merged = with_board_notes(held, [first, second])
    assert [(note.note_id, note.reviewed, note.passed) for note in merged] == [
        ("n1", True, True),
        ("n2", False, False),
    ]


def test_the_state_starts_without_notes_and_replaces_them_on_write() -> None:
    state = ResearchState(session_id="session-1", original_question=QUESTION)
    first = merge_research_state(state, {"reader_notes": [fake_reader_note("n1")], "note_passes": 1})
    second = merge_research_state(first, {"reader_notes": [fake_reader_note("n2")]})

    assert (state.reader_notes, state.note_passes) == ([], 0)
    assert [note.note_id for note in first.reader_notes] == ["n1"]
    assert [note.note_id for note in second.reader_notes] == ["n2"]
    assert second.note_passes == 1
    with pytest.raises(ValidationError):
        ResearchState(session_id="s", original_question=QUESTION, note_passes=-1)


def test_an_older_checkpoint_loads_with_no_notes_and_a_new_one_round_trips() -> None:
    channel = initial_graph_state(session_id="session-1", question=QUESTION)
    older = initial_graph_state(session_id="session-1", question=QUESTION)
    del older["state"]["reader_notes"]
    del older["state"]["note_passes"]
    noted = merge_research_state(load_state(channel), {"reader_notes": [fake_reader_note()]})

    assert load_state(older).reader_notes == []
    assert load_state(older).note_passes == 0
    assert load_state(dump_state(noted)).reader_notes == [fake_reader_note()]


def test_a_review_records_no_note_dispositions_unless_it_judged_notes() -> None:
    review = ReportReview(status="provider_failed")

    assert review.note_dispositions == []
    judged = review.model_copy(update={"note_dispositions": [NoteDisposition(note_id="n1", status="no_evidence")]})
    assert judged.note_dispositions[0].status == "no_evidence"
    with pytest.raises(ValidationError):
        NoteDisposition(note_id="n1", status="ignored")


def test_the_notes_block_is_one_line_per_note_and_empty_without_notes() -> None:
    notes = [
        fake_reader_note("n1", restatement="more weight on fire-safety standards"),
        fake_reader_note("n2", kinds=["scope", "exclude"], restatement="only the United States"),
    ]

    assert render_reader_notes([], instruction=PLANNING_NOTES) == ""
    assert render_reader_notes(notes, instruction="Lead.") == (
        "Lead.\n"
        "- more weight on fire-safety standards (emphasis)\n"
        "- only the United States (scope, exclude)"
    )
    assert render_reader_notes(notes, instruction="Lead.", with_ids=True).splitlines()[1:] == [
        "- n1: more weight on fire-safety standards (emphasis)",
        "- n2: only the United States (scope, exclude)",
    ]


def test_a_request_built_now_reads_the_board_and_a_running_loop_skips_new_angles() -> None:
    board = NoteBoard()
    for _ in range(3):
        board.receive("note", received_at=AT, received_during="researcher")
    board.add(fake_reader_note("n1"))
    board.add(fake_reader_note("n2", kinds=["new_angle"], new_questions=["How are cells recycled?"]))
    board.add(fake_reader_note("n3", replaces="n1"))
    held = [fake_reader_note("n1", reviewed=True)]

    assert [note.note_id for note in live_reader_notes(held)] == ["n1"]  # no board bound
    with bind_note_board(board):
        live = live_reader_notes(held)
    assert [note.note_id for note in live] == ["n2", "n3"]
    assert [note.note_id for note in research_reader_notes(live)] == ["n3"]


@pytest.mark.asyncio
async def test_every_node_starts_with_the_notes_received_so_far(tracker: Tracker) -> None:
    """spec §4.6: ``agent_node`` merges the board into the state before each agent runs,
    and a note added while one node runs reaches the next one."""
    board = NoteBoard()
    board.receive("first", received_at=AT, received_during="planner")
    board.add(fake_reader_note("n1", received_during="planner"))
    one = verified_pass()

    def researching(state: ResearchState) -> dict[str, object]:
        board.receive("second", received_at=AT, received_during="researcher")
        board.add(fake_reader_note("n2"))
        return one.update()

    agents = fake_research_agents(researcher=FakeAgent("researcher", update_factory=researching))
    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    def seen(agent: FakeAgent) -> list[str]:
        return [note.note_id for note in agent.calls[0].reader_notes]

    assert seen(agents.planner) == ["n1"]
    assert seen(agents.researcher) == ["n1"]
    assert seen(agents.source_evaluator) == ["n1", "n2"]
    assert seen(agents.evidence_verifier) == ["n1", "n2"]
    assert seen(agents.report_writer) == ["n1", "n2"]
    assert [note.note_id for note in run.state.reader_notes] == ["n1", "n2"]
    assert all(isinstance(note, ReaderNote) for note in run.state.reader_notes)


@pytest.mark.asyncio
async def test_a_run_without_a_board_or_notes_carries_no_notes(tracker: Tracker) -> None:
    agents = fake_research_agents()

    run = await run_research_graph(
        graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
    )

    assert run.state.reader_notes == [] and run.state.note_passes == 0
    assert all(call.reader_notes == [] for call in agents.planner.calls)
```

Create `tests/test_graph/test_reader_notes_replay.py`:

```python
"""Reader notes on the real graph, offline (live-briefs spec §4.6).

Every request of a replay run is recorded by the scripted completer as
``(agent:schema, text)``. Without notes the whole run's requests are pinned as
one digest per case — the value at the end of Phase 2 — so no Phase 3 change
can alter a single byte of a run that has no notes (spec §4.8 "Replay mode").
With notes on a bound board, each consumer's requests are read back.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from deep_research.e2e_evaluation.replay import (
    build_replay_runtime,
    production_config_path,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ReaderNote
from tests.graph_fakes import fake_reader_note
from tests.test_api.replay_support import EXTRA_PASS_CASE, REDRAFT_CASE, guarded

# sha256[:16] over the sorted "agent:schema sha256[:16]" lines of every request a
# run sent, and how many requests that was: observed at the end of Phase 2 and
# unchanged by Phase 3, because every notes section is added only when there are
# notes.
PINNED_RUN_DIGESTS = {
    EXTRA_PASS_CASE: ("03e113e5584707da", 46),
    REDRAFT_CASE: ("875313d15f3325f2", 29),
}
AT = "2026-09-29T10:00:00.000+00:00"


async def replay_packets(
    tmp_path: Path,
    case_id: str,
    *,
    board: NoteBoard | None = None,
    after_research_turn: Callable[[int], None] | None = None,
) -> tuple[str, list[tuple[str, str]], Any]:
    """Run one replay case through ``run_research``; return its status, every
    request it sent, and its final state.

    ``board`` is bound for the whole run, as the API binds a session's board.
    ``after_research_turn`` is called with the count of researcher decision turns
    answered so far, right after each one — the moment a note can land mid-loop.
    """
    scenario = scenario_by_id(case_id)
    held: dict[str, Any] = {}

    async def builder(current: ConfigSettings, *, session_id: str) -> Any:
        effective = replay_settings(scenario, root=tmp_path, base=current)
        replay = await build_replay_runtime(scenario, root=tmp_path, session_id=session_id, settings=effective)
        completer = replay.completer
        held["completer"] = completer
        if after_research_turn is not None:
            original = completer.complete_react
            turns = [0]

            async def complete_react(messages: Any, tools: Any, **kwargs: Any) -> Any:
                turn = await original(messages, tools, **kwargs)
                if kwargs.get("agent_name") == "researcher":
                    turns[0] += 1
                    after_research_turn(turns[0])
                return turn

            completer.complete_react = complete_react
        return replay.runtime

    bound = bind_note_board(board) if board is not None else contextlib.nullcontext()
    with bound:
        outcome = await run_research(
            question=scenario.question, session_id="s1", config_path=str(production_config_path()),
            max_extra_passes=scenario.max_extra_passes, runtime_builder=builder,
        )
    return outcome.status, list(held["completer"].packet_sequence), outcome.state


def run_digest(sequence: Sequence[tuple[str, str]]) -> tuple[str, int]:
    lines = sorted(f"{key} {hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}" for key, text in sequence)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16], len(lines)


def packets_for(sequence: Sequence[tuple[str, str]], key: str) -> list[str]:
    return [text for packet_key, text in sequence if packet_key == key]


EMPHASIS = fake_reader_note("n1", received_during="planner", restatement="more weight on lithium-ion safety standards")
ANGLE = fake_reader_note(
    "n2", kinds=["new_angle"], received_during="planner", restatement="how battery cells are recycled",
    new_questions=["How are battery cells recycled at end of life?"],
)
EMPHASIS_TEXT = "more weight on lithium-ion safety standards (emphasis)"
EMPHASIS_LINE = f"- {EMPHASIS_TEXT}"
ANGLE_LINE = "- how battery cells are recycled (new_angle)"


def noted_board(*notes: ReaderNote) -> NoteBoard:
    """A board holding ``notes``, each already interpreted, as a run that took them before planning."""
    board = NoteBoard()
    for note in notes:
        board.receive(note.text, received_at=AT, received_during=note.received_during)
        board.add(note)
    return board


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", sorted(PINNED_RUN_DIGESTS))
async def test_without_notes_every_request_of_a_replay_run_is_byte_identical(tmp_path: Path, case_id: str) -> None:
    with guarded():
        status, sequence, _ = await replay_packets(tmp_path, case_id)

    assert status == "completed"
    assert run_digest(sequence) == PINNED_RUN_DIGESTS[case_id]
    assert not any("Reader notes" in text or "reader added these notes" in text for _, text in sequence)
```

**`tests/graph_fakes.py`**: 4 edits, in file order.

`tests/graph_fakes.py` — replace

```python
    FindingVerification,
    ReadRecord,
```

with

```python
    FindingVerification,
    ReaderNote,
    ReadRecord,
```

`tests/graph_fakes.py` — replace

```python

def fake_quality(*, hard_failures: Sequence[str] = ()) -> ReportQualitySnapshot:
```

with

```python

def fake_reader_note(note_id: str = "n1", **overrides: object) -> ReaderNote:
    """One interpreted reader note (live-briefs spec §4.6): an emphasis, not yet
    reviewed, passed or redrafted, unless a test says otherwise."""
    payload: dict[str, object] = {
        "note_id": note_id,
        "text": f"Focus on grid storage ({note_id}).",
        "received_at": "2026-09-29T10:00:00+00:00",
        "received_during": "researcher",
        "kinds": ["emphasis"],
        "restatement": f"more weight on grid storage ({note_id})",
    }
    payload.update(overrides)
    return ReaderNote.model_validate(payload)


def fake_quality(*, hard_failures: Sequence[str] = ()) -> ReportQualitySnapshot:
```

`tests/graph_fakes.py` — replace

```python
    composition_fingerprint: str = "composition-1",
) -> ReportReview:
```

with

```python
    composition_fingerprint: str = "composition-1",
    note_dispositions: Mapping[str, str] | None = None,
) -> ReportReview:
```

`tests/graph_fakes.py` — replace

```python
        "rationale": "Recorded for graph tests.",
    }
```

with

```python
        "rationale": "Recorded for graph tests.",
        "note_dispositions": [
            {"note_id": note_id, "status": verdict}
            for note_id, verdict in (note_dispositions or {}).items()
        ],
    }
```

- [ ] **Step 2: Run the new tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_reader_notes_replay.py -q 2>&1 | tail -5
```

Expected: one `ERROR` line for each new file, then `Interrupted: 3 errors during collection` and `3 errors in …`. None of them imports yet: `ModuleNotFoundError: No module named 'deep_research.agents.reader_notes'` for the board and state files, and `… 'deep_research.runtime.notes'` for the replay file.

- [ ] **Step 3: Implement the types, the board, the renderer and the node merge**

**`src/deep_research/utils/types.py`**: 4 edits, in file order.

`src/deep_research/utils/types.py` — replace

```python

REVIEW_RUBRIC_VERSION = 3
```

with

```python

# Reader notes (live-briefs spec §4.6, D8-D11a): what the reader adds while a run
# is going, as the run's own agents and routing read it.
MAX_NOTES_PER_RUN = 10
"""D11a: a run accepts at most ten notes; the API refuses an eleventh."""
NOTE_COVERAGE_PREFIX = "note-"
"""A note's own sub-topic is ``note-{note_id}``, and its targets carry the same prefix."""
NOTE_TOPIC_TITLE_PREFIX = "Your note: "
"""A note's own sub-topic is titled ``Your note: {restatement}`` (live-briefs spec §4.6)."""
ReaderNoteKind: TypeAlias = Literal[
    "emphasis", "exclude", "scope", "new_angle", "about_reader"
]
NoteDispositionStatus: TypeAlias = Literal[
    "honoured", "ignored_with_evidence", "no_evidence"
]


class ReaderNoteScope(ContractModel):
    """The scope a ``scope`` note sets: a geography, a period, or both."""

    geography: str | None = Field(default=None, min_length=1, max_length=120)
    period: str | None = Field(default=None, min_length=1, max_length=120)


class ReaderNote(ContractModel):
    """One interpreted reader note (live-briefs spec §4.6).

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
    new_questions: list[str] = Field(default_factory=list, max_length=3)
    replaces: str | None = None
    reviewed: bool = False
    passed: bool = False
    redrafted: bool = False


class NoteDisposition(ContractModel):
    """What one review concluded about one reader note (live-briefs spec §4.6)."""

    note_id: str = Field(min_length=1)
    status: NoteDispositionStatus


def active_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes the run acts on: every note no later note replaces, in receipt order (D9)."""
    replaced = {note.replaces for note in notes if note.replaces}
    return [note for note in notes if note.note_id not in replaced]


def with_board_notes(
    existing: Sequence[ReaderNote],
    board: Sequence[ReaderNote],
) -> list[ReaderNote]:
    """The state's notes, then every board note the state does not hold yet.

    The board is append-only and an interpreted note never changes, so a note
    the state already holds keeps the state's own flags: the board carries
    none of the run's bookkeeping.
    """
    known = {note.note_id for note in existing}
    return [*existing, *(note for note in board if note.note_id not in known)]


REVIEW_RUBRIC_VERSION = 3
```

`src/deep_research/utils/types.py` — replace

```python
    rationale: str = ""
```

with

```python
    rationale: str = ""
    note_dispositions: list[NoteDisposition] = Field(default_factory=list)
    """One entry per reader note this review judged (live-briefs spec §4.6).

    Optional and empty by default, so a review without reader notes, and every
    review recorded before notes existed, is unchanged. It never enters the
    seven-dimension acceptance rule: ``graph_route`` reads it only to buy a
    note's one pass or one redraft.
    """
```

`src/deep_research/utils/types.py` (anchor as written by Phase 2 Task 2) — replace

```python
    """
    events: list[ResearchEvent] = Field(default_factory=list)
```

with

```python
    """
    reader_notes: list[ReaderNote] = Field(default_factory=list)
    """The reader's notes the run has taken in so far, in receipt order, or ``[]``.

    Replaced on every write (live-briefs spec §4.6). ``agent_node`` and the
    review node copy in every note the run's board holds that this list does
    not, so each node starts with the notes received so far; the flags on each
    note are the graph's own record of its one pass and one redraft.
    """
    note_passes: int = Field(default=0, ge=0)
    """How many targeted research passes the reader's notes bought (D11).

    Counted apart from ``iteration``: a note pass never spends the extra-pass
    budget, and the report's pass fact names the two separately.
    """
    events: list[ResearchEvent] = Field(default_factory=list)
```

`src/deep_research/utils/types.py` (anchor as written by Phase 2 Task 2) — replace

```python
    reader_answers: list[ReaderAnswer]
    events: list[ResearchEvent]
```

with

```python
    reader_answers: list[ReaderAnswer]
    reader_notes: list[ReaderNote]
    note_passes: int
    events: list[ResearchEvent]
```

Create `src/deep_research/runtime/notes.py`:

```python
"""The reader's notes for one run (live-briefs spec §4.6): the board the API
writes and the graph reads.

``SessionStore`` makes one ``NoteBoard`` per session and binds it for the run
through a ContextVar, exactly as the live sink is bound (``graph/live.py``): the
LangGraph node tasks and every task an agent starts copy the context, so a node
or a loop in flight reads the board without it being threaded through a call.

The board is append-only. The notes route *receives* a note (which numbers it
``n1``..``n10`` and counts it against ``MAX_NOTES_PER_RUN``), then *adds* the
interpreted note once its interpretation finishes. Only added notes are
readable: an agent never sees a note before it is interpreted. ``settled``
waits until every received note has been added or dropped, which is how the
review node takes in a note that was still being interpreted when the review
ended (§4.8 "A note arrives during Reviewing").

Nothing here is imported by an agent at module level: ``deep_research.runtime``
imports every agent while its package initialises, so agents and graph nodes
reach the board through ``agents.reader_notes`` at call time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from deep_research.utils.types import MAX_NOTES_PER_RUN, ReaderNote


class NoteLimitReached(Exception):
    """The run already holds ``MAX_NOTES_PER_RUN`` accepted notes (D11a)."""


@dataclass(frozen=True, slots=True)
class ReceivedNote:
    """A note the API accepted, before (or without) its interpretation."""

    note_id: str
    text: str
    received_at: str
    received_during: str


class NoteBoard:
    """One run's reader notes: received in order, readable once interpreted."""

    def __init__(self) -> None:
        self._received: list[ReceivedNote] = []
        self._notes: dict[str, ReaderNote] = {}
        self._dropped: set[str] = set()
        self._settled = asyncio.Event()
        self._settled.set()

    @property
    def accepted(self) -> int:
        """Notes received so far, interpreted or not; a refused note never counts."""
        return len(self._received)

    @property
    def remaining(self) -> int:
        """How many more notes this run accepts (D11a)."""
        return max(0, MAX_NOTES_PER_RUN - len(self._received))

    @property
    def pending(self) -> tuple[str, ...]:
        """Received notes whose interpretation has neither finished nor been dropped."""
        return tuple(
            note.note_id
            for note in self._received
            if note.note_id not in self._notes and note.note_id not in self._dropped
        )

    def received(self) -> list[ReceivedNote]:
        """Every accepted note, in receipt order."""
        return list(self._received)

    def receive(
        self, text: str, *, received_at: str, received_during: str
    ) -> ReceivedNote:
        """Accept one note and number it; raise ``NoteLimitReached`` past the tenth."""
        if len(self._received) >= MAX_NOTES_PER_RUN:
            raise NoteLimitReached
        note = ReceivedNote(
            note_id=f"n{len(self._received) + 1}",
            text=text,
            received_at=received_at,
            received_during=received_during,
        )
        self._received.append(note)
        self._settled.clear()
        return note

    def add(self, note: ReaderNote) -> None:
        """Make one received note readable, interpreted."""
        if note.note_id not in {received.note_id for received in self._received}:
            raise ValueError(f"note {note.note_id!r} was never received")
        if note.note_id in self._notes or note.note_id in self._dropped:
            raise ValueError(f"note {note.note_id!r} was already settled")
        self._notes[note.note_id] = note.model_copy(deep=True)
        self._settle()

    def drop(self, note_id: str) -> None:
        """Give up on one received note's interpretation (the session is closing)."""
        if note_id not in self._notes:
            self._dropped.add(note_id)
        self._settle()

    def interpreted(self, note_id: str) -> ReaderNote | None:
        """The interpreted note, or ``None`` while it is pending or when it was dropped."""
        note = self._notes.get(note_id)
        return None if note is None else note.model_copy(deep=True)

    def snapshot(self) -> list[ReaderNote]:
        """Every interpreted note, in receipt order; deep copies."""
        return [
            self._notes[note.note_id].model_copy(deep=True)
            for note in self._received
            if note.note_id in self._notes
        ]

    async def settled(self) -> None:
        """Return once no received note is still being interpreted."""
        await self._settled.wait()

    def _settle(self) -> None:
        if not self.pending:
            self._settled.set()


_NOTE_BOARD: ContextVar[NoteBoard | None] = ContextVar(
    "deep_research_note_board", default=None
)


def current_note_board() -> NoteBoard | None:
    """The board bound for this run, or ``None`` (the CLI, or a test with none)."""
    return _NOTE_BOARD.get()


@contextmanager
def bind_note_board(board: NoteBoard) -> Iterator[None]:
    """Bind ``board`` for the body of the ``with`` block, then restore the previous one."""
    token = _NOTE_BOARD.set(board)
    try:
        yield
    finally:
        _NOTE_BOARD.reset(token)


__all__ = [
    "NoteBoard",
    "NoteLimitReached",
    "ReceivedNote",
    "bind_note_board",
    "current_note_board",
]
```

Create `src/deep_research/agents/reader_notes.py`:

```python
"""Reader notes in the agents' requests (live-briefs spec §4.6, D9-D10).

One block, one line per note — ``- {restatement} ({kinds})`` — under a lead
sentence that says how *this* step uses the notes. Only interpreted notes that
no later note replaces are rendered, and every renderer returns ``""`` when
there is none, so a run without notes builds byte-identical requests.

The Evidence Verifier has no entry here, deliberately: verification is never
affected by a note (D10), and no verifier request carries this block.

The run's board is reached at call time: ``deep_research.runtime`` imports every
agent while its package initialises, so a module-level import of
``runtime.notes`` from an agent (or from ``graph.nodes``) would close an import
cycle whenever the graph is imported first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from deep_research.utils.types import (
    ReaderNote,
    active_reader_notes,
    with_board_notes,
)

PLANNING_NOTES = (
    "The reader added these notes while the run was going; each line is the "
    "note as the run understood it, and a later note replaces an earlier one it "
    "contradicts. Plan within them: an emphasis note gives its subject more "
    "weight, an exclude note leaves its subject out, a scope note narrows the "
    "plan to its scope, an about_reader note says who the report is for, and a "
    "new_angle note can become a sub-topic of its own."
)
RESEARCH_NOTES = (
    "The reader added these notes while the run was going. From your next "
    "search on, apply each one that bears on this sub-topic: an emphasis note "
    "gives its subject more weight, an exclude note means stop pursuing what "
    "it leaves out, and a scope note keeps later searches and reads inside its "
    "scope. Ignore a note that does not bear on this sub-topic."
)
EXTRACTION_NOTES = (
    "The reader added these notes while the run was going. A fact outside a "
    "scope note's scope, or about what an exclude note leaves out, is not a "
    "finding for this run; every other rule above still applies."
)
SOURCE_NOTES = (
    "The reader added these notes while the run was going. They bear on how "
    "relevant a source is to what the reader wants, and never on its authority "
    "or its recency."
)
WRITING_NOTES = (
    "The reader added these notes while the run was going. An emphasis note "
    "gives its subject more of the report, an exclude note leaves its subject "
    "out, a scope note leaves out-of-scope findings out and says so in one "
    "sentence, and an about_reader note sets the level of the writing. A note "
    "never changes what a finding says, and never licenses a sentence no "
    "verified finding carries."
)
REVIEW_NOTES = (
    "The reader added these notes while the run was going. For each note id "
    "below, return exactly one entry in note_dispositions: honoured when the "
    "report follows the note; ignored_with_evidence when the findings shown "
    "would let the report follow it and the report does not; no_evidence when "
    "no finding shown bears on it. A note never changes how a statement is "
    "judged against its findings."
)


class NoteLine(Protocol):
    """What one rendered line reads: a ``ReaderNote``, or the review's own view of one."""

    note_id: str
    restatement: str
    kinds: Sequence[str]


def board_notes() -> list[ReaderNote]:
    """The run's interpreted notes from its bound board, or ``[]`` with none bound."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return [] if board is None else board.snapshot()


async def notes_settled(*, timeout: float | None = None) -> bool:
    """Wait until the bound board has no note still being interpreted.

    ``timeout`` bounds the wait in seconds (``None``: no bound). ``True`` once
    nothing is being read, and at once with no board bound; ``False`` when the
    bound passed first, with the notes still being read left on the board.
    """
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    if board is None:
        return True
    try:
        async with asyncio.timeout(timeout):
            await board.settled()
    except TimeoutError:
        return False
    return True


def live_reader_notes(state_notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a request built *now* reads: the state's, then the board's newer ones, active only."""
    return active_reader_notes(with_board_notes(state_notes, board_notes()))


def research_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a running research loop applies: ``new_angle`` notes wait for the review's note pass."""
    return [note for note in notes if "new_angle" not in note.kinds]


def render_reader_notes(
    notes: Sequence[NoteLine],
    *,
    instruction: str,
    with_ids: bool = False,
) -> str:
    """``instruction``, then one line per note; ``""`` when there is none.

    ``with_ids`` prefixes each line with the note's id, for the review, whose
    reply names each note it judged.
    """
    if not notes:
        return ""
    lines = [instruction]
    for note in notes:
        prefix = f"{note.note_id}: " if with_ids else ""
        lines.append(f"- {prefix}{note.restatement} ({', '.join(note.kinds)})")
    return "\n".join(lines)


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

**`src/deep_research/agents/__init__.py`**: 2 edits, in file order.

`src/deep_research/agents/__init__.py` — replace

```python
    build_proposal_id,
    run_react_loop,
)
```

with

```python
    build_proposal_id,
    run_react_loop,
)
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

`src/deep_research/agents/__init__.py` — replace

```python
    "run_react_loop",
    "QUALITY_STATUS_NOT_GATED",
```

with

```python
    "run_react_loop",
    "EXTRACTION_NOTES",
    "PLANNING_NOTES",
    "RESEARCH_NOTES",
    "REVIEW_NOTES",
    "SOURCE_NOTES",
    "WRITING_NOTES",
    "NoteLine",
    "board_notes",
    "live_reader_notes",
    "notes_settled",
    "render_reader_notes",
    "research_reader_notes",
    "QUALITY_STATUS_NOT_GATED",
```

**`src/deep_research/graph/nodes.py`**: 4 edits, in file order.

`src/deep_research/graph/nodes.py` — replace

```python
)
from deep_research.agents.report import (
```

with

```python
)
from deep_research.agents.reader_notes import board_notes
from deep_research.agents.report import (
```

`src/deep_research/graph/nodes.py` — replace

```python
    merge_research_state,
)
```

with

```python
    merge_research_state,
    with_board_notes,
)
```

`src/deep_research/graph/nodes.py` — replace

```python


def agent_node(
    agent: ResearchAgent,
```

with

```python


def _board_notes_update(state: ResearchState) -> ResearchStateUpdate:
    """The run board's notes the state does not hold yet, as one update, or ``{}``.

    live-briefs spec §4.6: a node starts from every note received so far. A
    note the state already holds keeps the state's flags; ``{}`` when the
    board adds nothing, so a run without notes merges exactly what it did.
    """
    notes = with_board_notes(state.reader_notes, board_notes())
    if len(notes) == len(state.reader_notes):
        return {}
    return {"reader_notes": notes}


def agent_node(
    agent: ResearchAgent,
```

`src/deep_research/graph/nodes.py` — replace

```python
        started_event = node_started_event(name, iteration=state.iteration)
        started = merge_research_state(state, {"events": [started_event]})
        # Published live (live-briefs spec E3): the object merged here is the one
```

with

```python
        started_event = node_started_event(name, iteration=state.iteration)
        # live-briefs spec §4.6: every node begins with the notes received so far.
        started = merge_research_state(
            state, {"events": [started_event], **_board_notes_update(state)}
        )
        # Published live (live-briefs spec E3): the object merged here is the one
```

- [ ] **Step 4: Run the new tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_reader_notes_replay.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: `43 passed`: the new files' `17` (`6` board, `9` state, `2` replay) and `tests/test_imports.py`'s `26`, which prove that every new public name reaches `deep_research.agents.__all__`. The two replay cases passing are the proof that nothing so far changed a request.

- [ ] **Step 5: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4851 passed, 6 skipped, 12 deselected` (Task 1's `4834` + 17). No existing test changes result: a run without notes carries `reader_notes == []` everywhere.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/utils/types.py src/deep_research/runtime/notes.py src/deep_research/agents/reader_notes.py src/deep_research/agents/__init__.py src/deep_research/graph/nodes.py tests/graph_fakes.py tests/test_runtime/test_note_board.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_reader_notes_replay.py
git commit -m "feat(engine): reader notes on a per-run board, in the run's state, and in every node's starting state"
```

---

### Task 3: The note pass and the note redraft (spec §4.6 "Routing (D11)", "`note_pass` node"; AC17, AC18, AC20)

**Files:**
- Create: `tests/test_graph/test_note_routing.py`
- Modify: `src/deep_research/graph/state.py:29` (imports), `:45`, `:63`, `:75` (`NOTE_PASS_NODE`, `NODE_NAMES`, `NOTE_REDRAFT_STEPS`, `ROUTE_NOTE_PASS`), `:111`, `:135` (the two reasons and their status), `:247`, `:258` (`extra_pass_target_ids`, `note_dispositions`, `notes_due_a_pass`, `notes_due_a_redraft`), `:296` (`graph_route`), `:351` (`graph_recursion_limit`); `src/deep_research/graph/events.py:175` (two events); `src/deep_research/graph/nodes.py:35`, `:73`, `:84`, `:95`, `:109` (imports), `:369` (`_arrived_via_redraft_hop`), `:799`, `:812`, `:824` (the review node), `:1041` (`_reviewed_notes_update`), `:1197`, `:1205` (`writer_redraft_node`), `:1239` (`note_sub_topic`, `note_pass_node`); `src/deep_research/graph/orchestrator.py:11`, `:49`, `:58`, `:67`, `:176`, `:198`, `:208` (the node, its route and its edge); `src/deep_research/graph/__init__.py` (exports); `src/deep_research/agents/report_reviewer.py:105`, `:116` (imports), `:552` (`ReviewNoteView`), `:588` (`ReportReviewInput.reader_notes`), `:674` (`build_report_review_input`), `:999` (`report_review_input_fingerprint`), `:2128` (`scoped_report_review_input_fingerprint`), `:2835` (`__all__`); `src/deep_research/agents/__init__.py:322`, `:809` (`ReviewNoteView`'s export)
- Test: `tests/test_graph/test_state.py` (the node list, the nine reasons, the limit), `tests/test_graph/test_orchestrator.py` (the node order, the session config's bound)

**Interfaces:**
- Consumes: Task 2's types, board and `_board_notes_update`.
- Produces:
  - In `deep_research.graph.state`: `NOTE_PASS_NODE = ROUTE_NOTE_PASS = "note_pass"` (in `NODE_NAMES` before `extra_pass`), `NOTE_REDRAFT_STEPS = 3`; reasons `note_pass_requested` and `note_redraft_requested` (both status `incomplete`); `note_dispositions(state)`, `notes_due_a_pass(state)`, `notes_due_a_redraft(state)`; `graph_route` reads them right after "halted"; `extra_pass_target_ids` leaves `note-*` targets out; the new recursion limit.
  - In `deep_research.graph.events`: `note_pass_started_event(*, iteration, note_passes, note_ids, targets)` → `graph.note_pass.started`; `note_redraft_requested_event(*, iteration, note_ids)` → `graph.note_redraft.requested`.
  - In `deep_research.graph.nodes`: `note_sub_topic(note, *, priority) -> SubTopic`; `note_pass_node` (appends one sub-topic per note that owes a pass, sets `extra_pass_target_ids` to their targets, flags the notes `passed`, adds one to `note_passes`, emits the event; with no note due it halts on `graph_invalid_route`); `writer_redraft_node` serves a note redraft without spending a re-run; the review node merges the board, waits for readings for `_REVIEW_NOTES_WAIT_S = 30.0` seconds at most (ambiguity 8), marks its notes `reviewed` and adds late ones unreviewed.
  - The compiled graph routes `note_pass` → `researcher`.
  - In `deep_research.agents.report_reviewer`: `ReviewNoteView(note_id, restatement, kinds)`; `ReportReviewInput.reader_notes: list[ReviewNoteView]`, the state's active notes, filled by `build_report_review_input`. Both packet fingerprints leave it out while it is empty, so a packet without notes keeps its fingerprint. The review node needs this to hand the reviewer the notes it judges; the review request shows them from Task 6 on.

- [ ] **Step 1: Write the failing tests**

`graph_route` is tested on plain states; the hops and the review node on channels; AC17, AC18 and AC20 on the compiled stub graph. The review node's wait for a reading is tested twice: a reading that ends is waited for, and one that never ends is waited for only until `_REVIEW_NOTES_WAIT_S` (set to 50 ms in the test, whose `asyncio.wait_for(..., timeout=5)` fails the test if the node waits unbounded). `NoteJudge` is a reviewer double that judges each note its packet carries by how many reviews have read it, and can land a note on the board while a review runs. In AC20 each note arrives alone during a review, so each buys its own redraft and then its own pass: the most supersteps ten notes can take.

Create `tests/test_graph/test_note_routing.py`:

```python
"""The reader notes' routes: one targeted pass and one redraft per note (live-briefs spec §4.6,
D11, D11a; AC17, AC18, AC20)."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence

import pytest

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
from deep_research.graph.orchestrator import compile_research_graph, run_research_graph
from deep_research.graph.state import (
    MAX_WRITER_REDRAFTS,
    NOTE_PASS_NODE,
    ROUTE_END,
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_NOTE_PASS,
    ROUTE_REDRAFT,
    dump_state,
    extra_pass_target_ids,
    graph_recursion_limit,
    graph_route,
    graph_status,
    is_halted,
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
    fake_quality,
    fake_reader_note,
    fake_report_review,
    fake_research_agents,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    halting_error,
    verified_pass,
)

QUESTION = "How mature is quantum error correction?"
AT = "2026-09-29T10:00:00.000+00:00"


def _judged(*notes: ReaderNote, verdicts: dict[str, str], **overrides: object) -> ResearchState:
    return fake_research_state(
        reader_notes=list(notes),
        report_review=fake_report_review(note_dispositions=verdicts),
        **overrides,
    )


def _material() -> ReviewDefect:
    return ReviewDefect(
        defect_id="review-01", kind="contradiction", severity="major",
        statement_ids=["S001"], problem="The summary contradicts the findings.",
    )


# --- graph_route: order and per-note guards ------------------------------------


def test_a_note_without_evidence_buys_its_pass_before_anything_but_a_halt() -> None:
    due = fake_research_state(
        reader_notes=[fake_reader_note(reviewed=True)],
        report_review=fake_report_review(
            note_dispositions={"n1": "no_evidence"},
            missing_required_target_ids=["topic-01-target-02"],
            defects=[_material()],
        ),
    )

    assert graph_route(due) == (ROUTE_NOTE_PASS, "note_pass_requested")
    assert graph_status(due) == "incomplete"
    halted = due.model_copy(update={"errors": [halting_error()]})
    assert graph_route(halted) == (ROUTE_END, "halted")


def test_each_note_buys_one_pass_and_one_redraft_and_the_pass_comes_first() -> None:
    fresh = fake_reader_note("n1", reviewed=True)
    late = fake_reader_note("n2")  # arrived after the review input was built

    both = _judged(fresh, late, verdicts={"n1": "no_evidence"})
    assert graph_route(both) == (ROUTE_NOTE_PASS, "note_pass_requested")
    assert [note.note_id for note in notes_due_a_pass(both)] == ["n1"]
    assert [note.note_id for note in notes_due_a_redraft(both)] == ["n2"]

    passed = _judged(fresh.model_copy(update={"passed": True}), late, verdicts={"n1": "no_evidence"})
    assert graph_route(passed) == (ROUTE_REDRAFT, "note_redraft_requested")
    assert graph_status(passed) == "incomplete"

    done = _judged(
        fresh.model_copy(update={"passed": True}), late.model_copy(update={"redrafted": True, "reviewed": True}),
        verdicts={"n1": "no_evidence", "n2": "honoured"}, quality=fake_quality(),
    )
    assert graph_route(done) == (ROUTE_FINALIZE, "report_accepted")


def test_a_report_that_ignores_a_note_buys_its_redraft_even_with_the_reviews_own_rerun_spent() -> None:
    ignored = fake_reader_note(reviewed=True)
    spent = _judged(ignored, verdicts={"n1": "ignored_with_evidence"}, writer_redrafts=MAX_WRITER_REDRAFTS)

    assert graph_route(spent) == (ROUTE_REDRAFT, "note_redraft_requested")
    redrafted = _judged(
        ignored.model_copy(update={"redrafted": True}), verdicts={"n1": "ignored_with_evidence"},
    ).model_copy(update={"report_review": fake_report_review(
        note_dispositions={"n1": "ignored_with_evidence"}, defects=[_material()],
    )})
    # The note's redraft spent nothing: the review's own re-run is still there.
    assert redrafted.writer_redrafts == 0
    assert graph_route(redrafted) == (ROUTE_REDRAFT, "redraft_requested")


def test_a_replaced_or_honoured_note_routes_nowhere() -> None:
    first = fake_reader_note("n1", reviewed=True)
    second = fake_reader_note("n2", reviewed=True, replaces="n1")
    state = _judged(first, second, verdicts={"n1": "no_evidence", "n2": "honoured"}, quality=fake_quality())

    assert notes_due_a_pass(state) == [] and notes_due_a_redraft(state) == []
    assert graph_route(state) == (ROUTE_FINALIZE, "report_accepted")


def test_a_notes_own_targets_never_buy_or_exhaust_an_extra_pass() -> None:
    topic = note_sub_topic(fake_reader_note(), priority=2)
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), topic],
        report_review=fake_report_review(missing_required_target_ids=["note-n1-target-01"]),
    )

    assert extra_pass_target_ids(state) == []
    assert graph_route(state)[0] != ROUTE_EXTRA_PASS


# --- the note_pass node ---------------------------------------------------------


def test_a_notes_sub_topic_carries_its_questions_scope_and_required_targets() -> None:
    angled = fake_reader_note(
        "n3", kinds=["new_angle", "scope"], restatement="recycling at end of life",
        new_questions=["How are battery cells recycled?", "What does recycling cost?"],
        scope={"geography": "European Union", "period": "since 2023"},
    )

    topic = note_sub_topic(angled, priority=4)

    assert (topic.coverage_id, topic.title, topic.priority) == ("note-n3", "Your note: recycling at end of life", 4)
    assert topic.search_queries == ["How are battery cells recycled?", "What does recycling cost?"]
    assert [
        (t.target_id, t.coverage_id, t.question, t.required, t.geography, t.period) for t in topic.evidence_targets
    ] == [
        ("note-n3-target-01", "note-n3", "How are battery cells recycled?", True, "European Union", "since 2023"),
        ("note-n3-target-02", "note-n3", "What does recycling cost?", True, "European Union", "since 2023"),
    ]
    plain = note_sub_topic(fake_reader_note("n1"), priority=2)
    assert [t.question for t in plain.evidence_targets] == ["more weight on grid storage (n1)"]
    assert plain.evidence_targets[0].geography is None


@pytest.mark.asyncio
async def test_the_note_pass_opens_one_pass_for_every_note_that_owes_one() -> None:
    state = _judged(
        fake_reader_note("n1", reviewed=True), fake_reader_note("n2", reviewed=True),
        fake_reader_note("n3", reviewed=True, passed=True),
        verdicts={"n1": "no_evidence", "n2": "honoured", "n3": "no_evidence"},
        sub_topics=[fake_sub_topic(targets=[fake_target()])], iteration=1,
    )

    opened = load_state(await note_pass_node(dump_state(state)))

    assert [topic.coverage_id for topic in opened.sub_topics] == ["topic-01", "note-n1"]
    assert opened.sub_topics[-1].priority == state.sub_topics[0].priority + 1
    assert opened.extra_pass_target_ids == ["note-n1-target-01"]
    assert [topic.coverage_id for topic in select_sub_topics(opened)] == ["note-n1"]
    assert [(n.note_id, n.passed) for n in opened.reader_notes] == [("n1", True), ("n2", False), ("n3", True)]
    assert (opened.note_passes, opened.iteration) == (1, 1)
    started = [event for event in opened.events if event.event_type == "graph.note_pass.started"]
    assert [event.metadata for event in started] == [
        {"iteration": 1, "note_passes": 1, "note_ids": ["n1"], "targets": ["note-n1-target-01"]}
    ]
    assert [event.event_type for event in opened.events][-3:] == [
        "graph.node.started", "graph.note_pass.started", "graph.node.completed",
    ]


@pytest.mark.asyncio
async def test_the_note_pass_refuses_to_run_for_no_note_and_skips_a_halted_run() -> None:
    idle = load_state(await note_pass_node(dump_state(fake_research_state())))
    halted = load_state(await note_pass_node(dump_state(fake_research_state(errors=[halting_error()]))))

    assert [error.error_type for error in idle.errors] == ["graph_invalid_route"]
    assert is_halted(idle) and idle.note_passes == 0
    assert [event.event_type for event in halted.events][-1] == "graph.node.skipped"
    assert halted.note_passes == 0


# --- the redraft hop and the review node ------------------------------------------


@pytest.mark.asyncio
async def test_the_note_redraft_flags_its_notes_and_spends_no_rerun() -> None:
    state = _judged(
        fake_reader_note("n1", reviewed=True), fake_reader_note("n2"),
        verdicts={"n1": "ignored_with_evidence"}, writer_redrafts=MAX_WRITER_REDRAFTS,
    )

    hop = load_state(await writer_redraft_node(dump_state(state)))

    assert not is_halted(hop)
    assert hop.writer_redrafts == MAX_WRITER_REDRAFTS
    assert [(n.note_id, n.redrafted) for n in hop.reader_notes] == [("n1", True), ("n2", True)]
    assert [event.event_type for event in hop.events][-3:] == [
        "graph.node.started", "graph.note_redraft.requested", "graph.node.completed",
    ]
    assert hop.events[-2].metadata == {"iteration": 0, "note_ids": ["n1", "n2"]}
    assert _arrived_via_redraft_hop(hop.events) is False


def _drafted_state() -> ResearchState:
    """A run that has drafted its report and is ready for review."""
    one = verified_pass()
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()])],
        raw_findings=[one.finding], verified_findings=[one.finding],
        read_records={one.read.read_id: one.read}, evaluated_sources=[fake_scored_source()],
    )
    composition = fake_writer_composition(state)
    return state.model_copy(update={
        "composition": composition,
        "report": render_written_report(composition),
        "report_evidence": render_finding_log(composition),
    })


@pytest.mark.asyncio
async def test_the_review_marks_what_it_read_and_waits_for_a_note_still_being_read() -> None:
    """spec §4.8 "A note arrives during Reviewing": the node waits for the note, then
    takes it in unreviewed, which buys it a redraft."""
    board = NoteBoard()
    board.receive("first", received_at=AT, received_during="report_writer")
    board.add(fake_reader_note("n1"))
    board.receive("second", received_at=AT, received_during="report_reviewer")
    reviewer = FakeReviewer()

    async def interpret_later() -> None:
        await asyncio.sleep(0.05)
        board.add(fake_reader_note("n2", received_during="report_reviewer"))

    with bind_note_board(board):
        reading = asyncio.create_task(interpret_later())
        result = load_state(await report_reviewer_node(reviewer)(dump_state(_drafted_state())))
        await reading

    assert [note.note_id for note in reviewer.packets[0].reader_notes] == ["n1"]
    assert [(n.note_id, n.reviewed) for n in result.reader_notes] == [("n1", True), ("n2", False)]
    decided = [event for event in result.events if event.event_type == "graph.route.decided"]
    assert decided[-1].metadata["reason"] == "note_redraft_requested"
    assert decided[-1].metadata["destination"] == ROUTE_REDRAFT


@pytest.mark.asyncio
async def test_the_review_waits_for_a_reading_no_longer_than_its_own_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading that never ends holds the route for ``_REVIEW_NOTES_WAIT_S`` at most, however
    long the interpreter's own timeout is: the note stays on the board, out of this
    decision, and the route is read without it."""
    assert _REVIEW_NOTES_WAIT_S == 30.0
    assert HitlConfig().note_interpret_timeout_s < _REVIEW_NOTES_WAIT_S
    monkeypatch.setattr("deep_research.graph.nodes._REVIEW_NOTES_WAIT_S", 0.05)
    board = NoteBoard()
    board.receive("first", received_at=AT, received_during="report_writer")
    board.add(fake_reader_note("n1"))
    board.receive("never read", received_at=AT, received_during="report_reviewer")

    with bind_note_board(board):
        result = load_state(
            await asyncio.wait_for(report_reviewer_node(FakeReviewer())(dump_state(_drafted_state())), timeout=5)
        )

    assert board.pending == ("n2",)
    assert [(n.note_id, n.reviewed) for n in result.reader_notes] == [("n1", True)]
    # Read without the note: none is due, so the review's own verdict decides.
    decided = [event for event in result.events if event.event_type == "graph.route.decided"]
    assert (decided[-1].metadata["destination"], decided[-1].metadata["reason"]) == (
        ROUTE_FINALIZE, "report_not_accepted",
    )


# --- the compiled graph: AC17, AC18, AC20 ------------------------------------------


class NoteJudge(FakeReviewer):
    """A reviewer double that judges every note its packet carries (spec §4.6).

    A note is ``first`` the first time a review reads it and ``later`` after that.
    ``arrivals`` land on the board one per review, while that review runs, and
    only once every note already read is honoured — so each arrives alone.
    """

    def __init__(
        self,
        *,
        first: str,
        later: str,
        board: NoteBoard | None = None,
        arrivals: Sequence[ReaderNote] = (),
    ) -> None:
        super().__init__()
        self.first, self.later = first, later
        self.board = board
        self.arrivals = list(arrivals)
        self.seen: Counter[str] = Counter()

    async def review(self, packet: object, *, previous: ReportReview | None = None) -> ReportReview:
        review = await super().review(packet, previous=previous)
        verdicts: dict[str, str] = {}
        for note in getattr(packet, "reader_notes", []):
            self.seen[note.note_id] += 1
            verdicts[note.note_id] = self.first if self.seen[note.note_id] == 1 else self.later
        if self.arrivals and self.board is not None and all(v == "honoured" for v in verdicts.values()):
            note = self.arrivals.pop(0)
            self.board.receive(note.text, received_at=AT, received_during="report_reviewer")
            self.board.add(note)
        return review.model_copy(update={"note_dispositions": [
            NoteDisposition(note_id=note_id, status=verdict) for note_id, verdict in verdicts.items()  # type: ignore[arg-type]
        ]})


def _board(*notes: ReaderNote) -> NoteBoard:
    board = NoteBoard()
    for note in notes:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    return board


def _types(events: Sequence[ResearchEvent], prefix: str) -> list[str]:
    return [event.event_type for event in events if event.event_type.startswith(prefix)]


def _reasons(state: ResearchState) -> list[str]:
    return [e.metadata["reason"] for e in state.events if e.event_type == "graph.route.decided"]


@pytest.mark.asyncio
async def test_ac17_a_note_without_evidence_gets_exactly_one_targeted_pass(tracker: Tracker) -> None:
    board = _board(fake_reader_note("n1", received_during="planner"))
    agents = fake_research_agents(report_reviewer=NoteJudge(first="no_evidence", later="no_evidence"))

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert _reasons(state) == ["note_pass_requested", "report_accepted"]
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"]
    assert (state.iteration, state.note_passes) == (0, 1)
    second = agents.researcher.calls[1]
    assert second.extra_pass_target_ids == ["note-n1-target-01"]
    assert [topic.coverage_id for topic in select_sub_topics(second)] == ["note-n1"]
    assert len(agents.researcher.calls) == 2
    assert [(n.note_id, n.reviewed, n.passed, n.redrafted) for n in state.reader_notes] == [("n1", True, True, False)]
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_ac18_a_note_the_report_ignores_gets_exactly_one_redraft(tracker: Tracker) -> None:
    board = _board(fake_reader_note("n1", received_during="planner"))
    agents = fake_research_agents(report_reviewer=NoteJudge(first="ignored_with_evidence", later="honoured"))

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert _reasons(state) == ["note_redraft_requested", "report_accepted"]
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"]
    assert "graph.report.redraft_requested" not in [event.event_type for event in state.events]
    assert state.writer_redrafts == 0
    assert len(agents.report_writer.calls) == 2 and len(agents.researcher.calls) == 1
    assert [(n.note_id, n.redrafted) for n in state.reader_notes] == [("n1", True)]
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_ac20_ten_notes_each_buy_one_pass_and_one_redraft_within_the_recursion_limit(
    tracker: Tracker,
) -> None:
    """D11a's worst case: every note arrives alone, during a review, and is judged
    without evidence the first time — so each buys its own redraft, then its own pass."""
    board = NoteBoard()
    arrivals = [fake_reader_note(f"n{number}") for number in range(1, MAX_NOTES_PER_RUN + 1)]
    judge = NoteJudge(first="no_evidence", later="honoured", board=board, arrivals=arrivals)
    agents = fake_research_agents(report_reviewer=judge)

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert run.status == "completed"
    assert not is_halted(state)
    assert len(state.reader_notes) == MAX_NOTES_PER_RUN == 10
    assert all(n.reviewed and n.passed and n.redrafted for n in state.reader_notes)
    assert state.note_passes == 10 and state.iteration == 0 and state.writer_redrafts == 0
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"] * 10
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"] * 10
    assert _reasons(state) == ["note_redraft_requested", "note_pass_requested"] * 10 + ["report_accepted"]
    supersteps = sum(1 for e in state.events if e.event_type == "graph.node.started")
    assert supersteps < graph_recursion_limit(state.max_extra_passes)
    assert NOTE_PASS_NODE in {e.metadata["node"] for e in state.events if e.event_type == "graph.node.started"}
```

**`tests/test_graph/test_state.py`**: 10 edits, in file order.

`tests/test_graph/test_state.py` — replace

```python
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    PLANNER_NODE,
```

with

```python
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    NOTE_PASS_NODE,
    NOTE_REDRAFT_STEPS,
    PLANNER_NODE,
```

`tests/test_graph/test_state.py` — replace

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_REDRAFT,
```

with

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_NOTE_PASS,
    ROUTE_REDRAFT,
```

`tests/test_graph/test_state.py` — replace

```python
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
```

with

```python
    LEGACY_QUALITY_CONTRACT_VERSION,
    MAX_NOTES_PER_RUN,
    QUALITY_CONTRACT_VERSION,
```

`tests/test_graph/test_state.py` — replace

```python
    fake_quality,
    fake_report_review,
```

with

```python
    fake_quality,
    fake_reader_note,
    fake_report_review,
```

`tests/test_graph/test_state.py` — replace

```python
def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    """``graph_route`` has exactly the seven enumerated reasons (spec 6.3-6.5)."""
    missing = fake_report_review(missing_required_target_ids=["t2"])
```

with

```python
def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    """``graph_route`` has exactly the nine enumerated reasons (spec 6.3-6.5, and
    the reader notes' two of live-briefs spec §4.6)."""
    missing = fake_report_review(missing_required_target_ids=["t2"])
```

`tests/test_graph/test_state.py` — replace

```python
        fake_research_state(quality=fake_quality(), report_review=defective),
    )
```

with

```python
        fake_research_state(quality=fake_quality(), report_review=defective),
        fake_research_state(
            reader_notes=[fake_reader_note(reviewed=True)],
            report_review=fake_report_review(note_dispositions={"n1": "no_evidence"}),
        ),
        fake_research_state(
            reader_notes=[fake_reader_note()], report_review=fake_report_review()
        ),
    )
```

`tests/test_graph/test_state.py` — replace

```python
        "redraft_requested",
        "halted",
```

with

```python
        "redraft_requested",
        "note_pass_requested",
        "note_redraft_requested",
        "halted",
```

`tests/test_graph/test_state.py` — replace

```python
                    )
                ]
            ),
        ),
    }[reason]
    assert graph_route(state)[1] == reason
```

with

```python
                    )
                ]
            ),
        ),
        "note_pass_requested": fake_research_state(
            reader_notes=[fake_reader_note(reviewed=True)],
            report_review=fake_report_review(note_dispositions={"n1": "no_evidence"}),
        ),
        "note_redraft_requested": fake_research_state(
            reader_notes=[fake_reader_note()], report_review=fake_report_review()
        ),
    }[reason]
    assert graph_route(state)[1] == reason
```

`tests/test_graph/test_state.py` — replace

```python
def test_the_recursion_limit_covers_every_planned_pass() -> None:
    assert graph_recursion_limit(1) == 2 * len(NODE_NAMES) + 10
    assert graph_recursion_limit(3) > graph_recursion_limit(1)
```

with

```python
def test_the_recursion_limit_covers_every_planned_pass() -> None:
    assert graph_recursion_limit(1) == (
        2 * len(NODE_NAMES)
        + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS)
        + 10
    )
    assert graph_recursion_limit(3) > graph_recursion_limit(1)
```

`tests/test_graph/test_state.py` — replace

```python
        REPORT_REVIEWER_NODE,
        EXTRA_PASS_NODE,
        REDRAFT_NODE,
        FINALIZE_NODE,
    )
    # The two hops and the terminal publication are the last three: the
    # extra-pass hop is the only node the loop back into the researcher passes
    # through, the redraft hop is the only one that leads to the writer alone,
    # and the finalizer is the only writer of the artifacts.
    assert NODE_NAMES[-3] == EXTRA_PASS_NODE == ROUTE_EXTRA_PASS == "extra_pass"
```

with

```python
        REPORT_REVIEWER_NODE,
        NOTE_PASS_NODE,
        EXTRA_PASS_NODE,
        REDRAFT_NODE,
        FINALIZE_NODE,
    )
    # The three hops and the terminal publication are the last four: the
    # note-pass and extra-pass hops are the only nodes the loops back into the
    # researcher pass through, the redraft hop is the only one that leads to
    # the writer alone, and the finalizer is the only writer of the artifacts.
    assert NODE_NAMES[-4] == NOTE_PASS_NODE == ROUTE_NOTE_PASS == "note_pass"
    assert NODE_NAMES[-3] == EXTRA_PASS_NODE == ROUTE_EXTRA_PASS == "extra_pass"
```

**`tests/test_graph/test_orchestrator.py`**: 4 edits, in file order.

`tests/test_graph/test_orchestrator.py` — replace

```python
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    PLANNER_NODE,
    REDRAFT_NODE,
```

with

```python
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    NOTE_PASS_NODE,
    NOTE_REDRAFT_STEPS,
    PLANNER_NODE,
    REDRAFT_NODE,
```

`tests/test_graph/test_orchestrator.py` — replace

```python
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
```

with

```python
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    QUALITY_STATUS_ACCEPTED,
```

`tests/test_graph/test_orchestrator.py` — replace

```python
    # ``AGENT_NODE_ORDER``, so it must be exactly the head of ``NODE_NAMES``,
    # with the reviewer, the two hops and the finalizer after it.
    assert NODE_NAMES == (
        *AGENT_NODE_ORDER,
        REPORT_REVIEWER_NODE,
        EXTRA_PASS_NODE,
```

with

```python
    # ``AGENT_NODE_ORDER``, so it must be exactly the head of ``NODE_NAMES``,
    # with the reviewer, the three hops and the finalizer after it.
    assert NODE_NAMES == (
        *AGENT_NODE_ORDER,
        REPORT_REVIEWER_NODE,
        NOTE_PASS_NODE,
        EXTRA_PASS_NODE,
```

`tests/test_graph/test_orchestrator.py` — replace

```python
    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] == (2 + 1) * len(NODE_NAMES) + 10
```

with

```python
    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] == (
        (2 + 1) * len(NODE_NAMES)
        + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS)
        + 10
    )
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_note_routing.py tests/test_graph/test_state.py tests/test_graph/test_orchestrator.py -q 2>&1 | tail -5
```

Expected: one `ERROR` line each for `tests/test_graph/test_note_routing.py`, `tests/test_graph/test_state.py` and `tests/test_graph/test_orchestrator.py`, then `3 errors in …`: `cannot import name 'note_pass_node' from 'deep_research.graph.nodes'` for the new file, and `cannot import name 'NOTE_PASS_NODE' from 'deep_research.graph.state'` for the two updated ones.

- [ ] **Step 3: Implement the routes, the events, the hops, the review node's notes and the notes in the review's packet**

**`src/deep_research/graph/state.py`**: 10 edits, in file order.

`src/deep_research/graph/state.py` (anchor as written by Phase 2 Task 2) — replace

```python
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    MemorySnapshot,
    ReaderAnswer,
    ResearchState,
)
```

with

```python
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    NOTE_COVERAGE_PREFIX,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    MemorySnapshot,
    ReaderAnswer,
    ReaderNote,
    ResearchState,
    active_reader_notes,
)
```

`src/deep_research/graph/state.py` — replace

```python
REPORT_REVIEWER_NODE = "report_reviewer"
EXTRA_PASS_NODE = "extra_pass"
REDRAFT_NODE = "writer_redraft"
FINALIZE_NODE = "finalize_report"

# Execution order, with the terminal review, the two one-hop continuations,
# and the terminal publication step last. Node names deliberately equal agent
# names so a LangSmith trace reads the same as this tuple; ``report_reviewer``
# is the graph's own reviewer rather than one of the five agents,
# ``extra_pass`` is the hop that carries the iteration increment,
# ``writer_redraft`` is the hop that hands the reviewer's defects back to the
```

with

```python
REPORT_REVIEWER_NODE = "report_reviewer"
NOTE_PASS_NODE = "note_pass"
EXTRA_PASS_NODE = "extra_pass"
REDRAFT_NODE = "writer_redraft"
FINALIZE_NODE = "finalize_report"

# Execution order, with the terminal review, the three one-hop continuations,
# and the terminal publication step last. Node names deliberately equal agent
# names so a LangSmith trace reads the same as this tuple; ``report_reviewer``
# is the graph's own reviewer rather than one of the five agents,
# ``note_pass`` is the hop that opens the reader's notes' targeted research
# pass (live-briefs spec §4.6), ``extra_pass`` is the hop that carries the
# iteration increment,
# ``writer_redraft`` is the hop that hands the reviewer's defects back to the
```

`src/deep_research/graph/state.py` — replace

```python
    REPORT_REVIEWER_NODE,
    EXTRA_PASS_NODE,
```

with

```python
    REPORT_REVIEWER_NODE,
    NOTE_PASS_NODE,
    EXTRA_PASS_NODE,
```

`src/deep_research/graph/state.py` — replace

```python

ROUTE_EXTRA_PASS = "extra_pass"
```

with

```python

# The supersteps one writer re-run takes: the redraft hop, the writer, the
# reviewer. A reader note buys at most one such re-run and one targeted pass
# (live-briefs spec §4.6), which is what the recursion limit allows for.
NOTE_REDRAFT_STEPS = 3

ROUTE_NOTE_PASS = "note_pass"
ROUTE_EXTRA_PASS = "extra_pass"
```

`src/deep_research/graph/state.py` — replace

```python
    ),
    "halted": "The run stopped on a non-recoverable error.",
```

with

```python
    ),
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
    "halted": "The run stopped on a non-recoverable error.",
```

`src/deep_research/graph/state.py` — replace

```python
    "redraft_requested": "incomplete",
    "halted": "failed",
```

with

```python
    "redraft_requested": "incomplete",
    # Both note routes continue the run (live-briefs spec §4.6), so they read
    # as the loops above do; neither is ever a run's last decision.
    "note_pass_requested": "incomplete",
    "note_redraft_requested": "incomplete",
    "halted": "failed",
```

`src/deep_research/graph/state.py` — replace

```python
    both read the same targets.
    """
```

with

```python
    both read the same targets.

    A reader note's own targets (``note-…``, live-briefs spec §4.6) are never
    part of it: a note buys its one targeted pass through the note route,
    outside the extra-pass budget (D11), so a note target still missing after
    its pass neither buys an extra pass nor ends the run as exhausted.
    """
```

`src/deep_research/graph/state.py` — replace

```python
        if target_id in required
    ]
    return list(
        dict.fromkeys([*review.missing_required_target_ids, *coverage_target_ids])
    )
```

with

```python
        if target_id in required
    ]
    return [
        target_id
        for target_id in dict.fromkeys(
            [*review.missing_required_target_ids, *coverage_target_ids]
        )
        if not target_id.startswith(NOTE_COVERAGE_PREFIX)
    ]


def note_dispositions(state: ResearchState) -> dict[str, str]:
    """The latest review's verdict on each reader note it judged, by note id."""
    review = state.report_review
    if review is None:
        return {}
    return {entry.note_id: entry.status for entry in review.note_dispositions}


def notes_due_a_pass(state: ResearchState) -> list[ReaderNote]:
    """Active notes the review found no evidence for that have not had their one pass (D11)."""
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

`src/deep_research/graph/state.py` — replace

```python
    for ends as ``extra_passes_exhausted`` (status ``max_iterations``).
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    review = state.report_review
```

with

```python
    for ends as ``extra_passes_exhausted`` (status ``max_iterations``).

    The reader's notes are read right after a halt (live-briefs spec §4.6,
    D11): first a note the review found no evidence for buys its one targeted
    pass (``ROUTE_NOTE_PASS``), then a note the report ignores, or one no
    review has read yet, buys its one redraft (``ROUTE_REDRAFT`` with the
    reason ``note_redraft_requested``, which spends no writer re-run of the
    review's own). Each note is flagged when its route is taken, so neither
    check can loop; with no note due, every rule below reads as it always did.
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    if notes_due_a_pass(state):
        return ROUTE_NOTE_PASS, "note_pass_requested"
    if notes_due_a_redraft(state):
        return ROUTE_REDRAFT, "note_redraft_requested"
    review = state.report_review
```

`src/deep_research/graph/state.py` — replace

```python
    pass over eight nodes need — and an explicit value documents the shape.
    """
    if max_extra_passes < 0:
        raise ValueError("max_extra_passes must not be negative")
    return (max_extra_passes + 1) * len(NODE_NAMES) + _RECURSION_MARGIN
```

with

```python
    pass over eight nodes need — and an explicit value documents the shape.
    Every reader note may buy one targeted pass and one redraft (live-briefs
    spec §4.6, D11a), so the notes' own worst case is added on top: the limit
    is never what stops a run the notes lengthened.
    """
    if max_extra_passes < 0:
        raise ValueError("max_extra_passes must not be negative")
    return (
        (max_extra_passes + 1) * len(NODE_NAMES)
        + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS)
        + _RECURSION_MARGIN
    )
```

`src/deep_research/graph/events.py` — replace

```python

def quality_assessed_event(
```

with

```python

def note_pass_started_event(
    *,
    iteration: int,
    note_passes: int,
    note_ids: Sequence[str],
    targets: Sequence[str],
) -> ResearchEvent:
    """Announce the targeted pass the reader's notes just bought (live-briefs spec §4.6).

    Ids only: the notes it researches and the targets their own sub-topics
    carry. ``iteration`` is unchanged by a note pass, and ``note_passes`` is
    the run's count including this one.
    """
    return graph_event(
        event_type="graph.note_pass.started",
        message=f"Note pass {note_passes} started.",
        metadata={
            "iteration": iteration,
            "note_passes": note_passes,
            "note_ids": list(note_ids),
            "targets": list(targets),
        },
    )


def note_redraft_requested_event(
    *,
    iteration: int,
    note_ids: Sequence[str],
) -> ResearchEvent:
    """Announce the writer re-run the reader's notes just bought (live-briefs spec §4.6).

    Its own event, not ``graph.report.redraft_requested``: a note redraft
    spends none of the review's own writer re-runs, and the writer drafts
    afresh with the notes rather than patching the parts a defect named.
    """
    return graph_event(
        event_type="graph.note_redraft.requested",
        message="Writer re-run requested for the reader's notes.",
        metadata={"iteration": iteration, "note_ids": list(note_ids)},
    )


def quality_assessed_event(
```

**`src/deep_research/graph/nodes.py`**: 13 edits, in file order.

`src/deep_research/graph/nodes.py` (anchor as written by Task 2) — replace

```python
)
from deep_research.agents.reader_notes import board_notes
from deep_research.agents.report import (
```

with

```python
)
from deep_research.agents.reader_notes import board_notes, notes_settled
from deep_research.agents.report import (
```

`src/deep_research/graph/nodes.py` — replace

```python
    node_started_event,
    quality_assessed_event,
```

with

```python
    node_started_event,
    note_pass_started_event,
    note_redraft_requested_event,
    quality_assessed_event,
```

`src/deep_research/graph/nodes.py` — replace

```python
    MAX_WRITER_REDRAFTS,
    REDRAFT_NODE,
```

with

```python
    MAX_WRITER_REDRAFTS,
    NOTE_PASS_NODE,
    REDRAFT_NODE,
```

`src/deep_research/graph/nodes.py` — replace

```python
    load_state,
)
from deep_research.observability import RunTelemetryCollector
from deep_research.providers import ProviderConfigurationError, ProviderError
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Finding,
    ReportComposition,
```

with

```python
    load_state,
    notes_due_a_pass,
    notes_due_a_redraft,
)
from deep_research.observability import RunTelemetryCollector
from deep_research.providers import ProviderConfigurationError, ProviderError
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    EvidenceTarget,
    Finding,
    ReaderNote,
    ReportComposition,
```

`src/deep_research/graph/nodes.py` — replace

```python
    ResearchStateUpdate,
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
    for whichever marker comes first settles it without a new state field.
    """
    for event in reversed(events):
        if event.event_type == "graph.report.redraft_requested":
            return True
        if event.event_type == "graph.extra_pass.started":
            return False
    return False
```

with

```python
    for whichever marker comes first settles it without a new state field.

    A reader note's own loops (live-briefs spec §4.6) read like the extra
    pass: after ``graph.note_pass.started`` the writer drafts over new
    evidence, and after ``graph.note_redraft.requested`` it drafts afresh with
    the notes, so neither judgement may be carried over as a scoped one.
    """
    for event in reversed(events):
        if event.event_type == "graph.report.redraft_requested":
            return True
        if event.event_type in _FRESH_DRAFT_MARKERS:
            return False
    return False


# The loop markers after which the writer's next draft is not a redraft of the
# previous one: the reviewer's extra pass and both of a reader note's loops.
_FRESH_DRAFT_MARKERS = frozenset(
    {
        "graph.extra_pass.started",
        "graph.note_pass.started",
        "graph.note_redraft.requested",
    }
)
```

`src/deep_research/graph/nodes.py` — replace

```python
    trace says whether a model was asked this pass.
    """
```

with

```python
    trace says whether a model was asked this pass.

    The reader's notes (live-briefs spec §4.6): the node starts from every note
    received so far, and each active one is in the review input, so each is
    marked ``reviewed``. Before the route is read the node waits for any note
    still being interpreted — for ``_REVIEW_NOTES_WAIT_S`` at most — and takes
    in every note that arrived meanwhile, unreviewed, which is what buys a
    note sent during Reviewing its redraft.
    """
```

`src/deep_research/graph/nodes.py` — replace

```python
                        REPORT_REVIEWER_NODE, iteration=state.iteration
                    )
                ]
            },
```

with

```python
                        REPORT_REVIEWER_NODE, iteration=state.iteration
                    )
                ],
                **_board_notes_update(state),
            },
```

`src/deep_research/graph/nodes.py` — replace

```python
                else []
            }
        )
        merged = merge_research_state(
            started,
            {
                "report_review": review,
                "quality": _quality_with_review(started, review),
                "errors": list(errors),
            },
        )
        destination, reason = graph_route(merged)
```

with

```python
                else []
            }
        )
        await notes_settled(timeout=_REVIEW_NOTES_WAIT_S)
        merged = merge_research_state(
            started,
            {
                "report_review": review,
                "quality": _quality_with_review(started, review),
                "errors": list(errors),
                **_reviewed_notes_update(started),
            },
        )
        destination, reason = graph_route(merged)
```

`src/deep_research/graph/nodes.py` — replace

```python

def _unreviewed(packet: ReportReviewInput, reason: str, *, status: str) -> ReportReview:
```

with

```python

# The longest the review node waits for a note still being read before it reads
# its route (live-briefs spec §4.8 "A note arrives during Reviewing"): twice
# ``hitl.note_interpret_timeout_s``'s default of 15 s, so with the default every
# reading has ended first, and a raised timeout (up to ten minutes) can hold the
# route for this long at most. A note still being read then is left out of this
# decision, unreviewed; a loop's next node takes it in once it is read.
_REVIEW_NOTES_WAIT_S = 30.0


def _reviewed_notes_update(started: ResearchState) -> ResearchStateUpdate:
    """The review's notes marked reviewed, then every note that arrived since, or ``{}``.

    Every active note of ``started`` was in the review input
    (``build_report_review_input`` reads them), so each is marked
    ``reviewed``; a note on the board that ``started`` did not hold is added
    unreviewed (live-briefs spec §4.6). ``{}`` for a run with no notes, so its
    merge is exactly what it was.
    """
    reviewed = {note.note_id for note in active_reader_notes(started.reader_notes)}
    marked = [
        note.model_copy(update={"reviewed": True})
        if note.note_id in reviewed
        else note
        for note in started.reader_notes
    ]
    notes = with_board_notes(marked, board_notes())
    if not notes:
        return {}
    return {"reader_notes": notes}


def _unreviewed(packet: ReportReviewInput, reason: str, *, status: str) -> ReportReview:
```

`src/deep_research/graph/nodes.py` — replace

```python
    ``graph_invalid_route`` rather than paying for a draft its bound forbids.
    """
```

with

```python
    ``graph_invalid_route`` rather than paying for a draft its bound forbids.

    A reader note's redraft (live-briefs spec §4.6) comes through this same
    hop and spends none of that bound: the notes it is for are flagged
    ``redrafted`` — each note buys one — and ``graph.note_redraft.requested``
    tells the writer to draft afresh with the notes.
    """
```

`src/deep_research/graph/nodes.py` — replace

```python
    )
    if state.writer_redrafts >= MAX_WRITER_REDRAFTS:
```

with

```python
    )
    if graph_route(state)[1] == "note_redraft_requested":
        due = {note.note_id for note in notes_due_a_redraft(state)}
        return _with(
            started,
            {
                "reader_notes": [
                    note.model_copy(update={"redrafted": True})
                    if note.note_id in due
                    else note
                    for note in state.reader_notes
                ],
                "events": [
                    note_redraft_requested_event(
                        iteration=state.iteration,
                        note_ids=[
                            note.note_id
                            for note in state.reader_notes
                            if note.note_id in due
                        ],
                    ),
                    node_completed_event(
                        REDRAFT_NODE,
                        iteration=state.iteration,
                        event_count=1,
                        error_count=0,
                    ),
                ],
            },
        )
    if state.writer_redrafts >= MAX_WRITER_REDRAFTS:
```

`src/deep_research/graph/nodes.py` — replace

```python

def route_after_review(channel: ResearchGraphState) -> str:
```

with

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
        success_criteria=[f"A checked source answers: {question}" for question in questions],
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
    """Open the one targeted pass the reader's uncovered notes buy (live-briefs spec §4.6, D11).

    Its own hop, as ``extra_pass`` is, because the route cannot write: it
    appends one sub-topic per note the review found no evidence for, confines
    the researcher to those sub-topics' targets exactly as an extra pass is
    confined (``extra_pass_target_ids``), flags each note ``passed`` and counts
    the pass in ``note_passes`` — never in ``iteration``, so the extra-pass
    budget is untouched. A run that arrives with no note due records
    ``graph_invalid_route`` rather than researching nothing.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, NOTE_PASS_NODE)
    started = merge_research_state(
        state,
        {"events": [node_started_event(NOTE_PASS_NODE, iteration=state.iteration)]},
    )
    due = notes_due_a_pass(state)
    if not due:
        return _halt(
            started,
            invalid_route_error(
                node=NOTE_PASS_NODE,
                iteration=state.iteration,
                max_extra_passes=state.max_extra_passes,
            ),
        )
    priority = max((topic.priority for topic in state.sub_topics), default=0) + 1
    topics = [note_sub_topic(note, priority=priority) for note in due]
    targets = [target.target_id for topic in topics for target in topic.evidence_targets]
    passed = {note.note_id for note in due}
    note_passes = state.note_passes + 1
    return _with(
        started,
        {
            "sub_topics": topics,
            "extra_pass_target_ids": targets,
            "reader_notes": [
                note.model_copy(update={"passed": True})
                if note.note_id in passed
                else note
                for note in state.reader_notes
            ],
            "note_passes": note_passes,
            "events": [
                note_pass_started_event(
                    iteration=state.iteration,
                    note_passes=note_passes,
                    note_ids=[note.note_id for note in due],
                    targets=targets,
                ),
                node_completed_event(
                    NOTE_PASS_NODE,
                    iteration=state.iteration,
                    event_count=1,
                    error_count=0,
                ),
            ],
        },
    )


def route_after_review(channel: ResearchGraphState) -> str:
```

**`src/deep_research/graph/orchestrator.py`**: 7 edits, in file order.

`src/deep_research/graph/orchestrator.py` — replace

```python
          -> report_writer -> report_reviewer
          -> {extra_pass -> researcher | finalize_report -> END}
```

with

```python
          -> report_writer -> report_reviewer
          -> {note_pass -> researcher | extra_pass -> researcher
              | writer_redraft -> report_writer | finalize_report -> END}
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    finalize_report_node,
    report_reviewer_node,
```

with

```python
    finalize_report_node,
    note_pass_node,
    report_reviewer_node,
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    PLANNER_NODE,
    REDRAFT_NODE,
```

with

```python
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    NOTE_PASS_NODE,
    PLANNER_NODE,
    REDRAFT_NODE,
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_REDRAFT,
```

with

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_NOTE_PASS,
    ROUTE_REDRAFT,
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    )
    builder.add_node(EXTRA_PASS_NODE, extra_pass_node)
```

with

```python
    )
    builder.add_node(NOTE_PASS_NODE, note_pass_node)
    builder.add_node(EXTRA_PASS_NODE, extra_pass_node)
```

`src/deep_research/graph/orchestrator.py` — replace

```python
        {
            ROUTE_EXTRA_PASS: EXTRA_PASS_NODE,
```

with

```python
        {
            ROUTE_NOTE_PASS: NOTE_PASS_NODE,
            ROUTE_EXTRA_PASS: EXTRA_PASS_NODE,
```

`src/deep_research/graph/orchestrator.py` — replace

```python
    builder.add_edge(EXTRA_PASS_NODE, RESEARCHER_NODE)
    # The redraft hop loops back to the writer alone: its defects are about the
```

with

```python
    builder.add_edge(EXTRA_PASS_NODE, RESEARCHER_NODE)
    # A reader note's targeted pass (live-briefs spec §4.6) loops back the same
    # way, confined to the notes' own sub-topics; its redraft reuses the
    # writer-redraft hop below.
    builder.add_edge(NOTE_PASS_NODE, RESEARCHER_NODE)
    # The redraft hop loops back to the writer alone: its defects are about the
```

**`src/deep_research/graph/__init__.py`**: 8 edits, in file order.

`src/deep_research/graph/__init__.py` — replace

```python
    node_started_event,
    quality_assessed_event,
```

with

```python
    node_started_event,
    note_pass_started_event,
    note_redraft_requested_event,
    quality_assessed_event,
```

`src/deep_research/graph/__init__.py` — replace

```python
    finalize_report_node,
    report_reviewer_node,
```

with

```python
    finalize_report_node,
    note_pass_node,
    note_sub_topic,
    report_reviewer_node,
```

`src/deep_research/graph/__init__.py` — replace

```python
    MAX_WRITER_REDRAFTS,
    NODE_NAMES,
    PLANNER_NODE,
```

with

```python
    MAX_WRITER_REDRAFTS,
    NODE_NAMES,
    NOTE_PASS_NODE,
    NOTE_REDRAFT_STEPS,
    PLANNER_NODE,
```

`src/deep_research/graph/__init__.py` — replace

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_REDRAFT,
```

with

```python
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_NOTE_PASS,
    ROUTE_REDRAFT,
```

`src/deep_research/graph/__init__.py` — replace

```python
    initial_graph_state,
    is_halted,
    load_state,
)
```

with

```python
    initial_graph_state,
    is_halted,
    load_state,
    note_dispositions,
    notes_due_a_pass,
    notes_due_a_redraft,
)
```

`src/deep_research/graph/__init__.py` — replace

```python
    "MAX_WRITER_REDRAFTS",
    "NODE_NAMES",
    "PLANNER_NODE",
```

with

```python
    "MAX_WRITER_REDRAFTS",
    "NODE_NAMES",
    "NOTE_PASS_NODE",
    "NOTE_REDRAFT_STEPS",
    "PLANNER_NODE",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "ROUTE_FINALIZE",
    "ROUTE_REDRAFT",
```

with

```python
    "ROUTE_FINALIZE",
    "ROUTE_NOTE_PASS",
    "ROUTE_REDRAFT",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "node_started_event",
    "redraft_limit_error",
```

with

```python
    "node_started_event",
    "note_dispositions",
    "note_pass_node",
    "note_pass_started_event",
    "note_redraft_requested_event",
    "note_sub_topic",
    "notes_due_a_pass",
    "notes_due_a_redraft",
    "redraft_limit_error",
```

**`src/deep_research/agents/report_reviewer.py`**: 8 edits, in file order.

`src/deep_research/agents/report_reviewer.py` — replace

```python
    Finding,
    GapKind,
    GapSeverity,
    ReportPoint,
    ReportSection,
    ReportStatement,
```

with

```python
    Finding,
    GapKind,
    GapSeverity,
    ReaderNoteKind,
    ReportPoint,
    ReportSection,
    ReportStatement,
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    StatementReviewDisposition,
    UnitScore,
)
```

with

```python
    StatementReviewDisposition,
    UnitScore,
    active_reader_notes,
)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python

class ReportReviewInput(ContractModel):
```

with

```python

class ReviewNoteView(ContractModel):
    """One reader note as the review reads it (live-briefs spec §4.6): its id,
    so the reply can name it, and the run's own reading of it."""

    note_id: str = Field(min_length=1)
    restatement: str = Field(min_length=1)
    kinds: list[ReaderNoteKind] = Field(min_length=1)


class ReportReviewInput(ContractModel):
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)
    composition_fingerprint: str = ""
```

with

```python
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)
    reader_notes: list[ReviewNoteView] = Field(default_factory=list)
    """The reader's active notes (live-briefs spec §4.6), which the review
    judges one by one into ``note_dispositions``; ``[]`` for a run without
    notes, whose packet and fingerprint are then exactly what they were."""
    composition_fingerprint: str = ""
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        required_target_ids=list(quality.required_target_ids) if quality else [],
        composition_fingerprint=composition_semantic_fingerprint(composition),
```

with

```python
        required_target_ids=list(quality.required_target_ids) if quality else [],
        reader_notes=[
            ReviewNoteView(
                note_id=note.note_id,
                restatement=note.restatement,
                kinds=list(note.kinds),
            )
            for note in active_reader_notes(state.reader_notes)
        ],
        composition_fingerprint=composition_semantic_fingerprint(composition),
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    judgement of its content, while a content or reference change always does.
    """
    payload = packet.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
```

with

```python
    judgement of its content, while a content or reference change always does.
    ``reader_notes`` is left out while it is empty, so a packet without notes
    keeps the fingerprint it had before notes existed (live-briefs spec §4.6).
    """
    exclude = {"fingerprint"} | (set() if packet.reader_notes else {"reader_notes"})
    payload = packet.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    -- the changed/unchanged split and the carried previous defects -- is not
    the material a full review reads, even over an identical report."""
    payload = scoped.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
```

with

```python
    -- the changed/unchanged split and the carried previous defects -- is not
    the material a full review reads, even over an identical report. The
    base packet's reader notes are left out while empty, so a scoped packet
    without notes keeps the fingerprint it had before notes existed (spec §4.6)."""
    exclude: dict[str, object] = {"fingerprint": True}
    if not scoped.base.reader_notes:
        exclude["base"] = {"reader_notes": True}
    payload = scoped.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    "ReviewFindingView",
    "ReviewStatementView",
```

with

```python
    "ReviewFindingView",
    "ReviewNoteView",
    "ReviewStatementView",
```

**`src/deep_research/agents/__init__.py`**: 2 edits, in file order.

`src/deep_research/agents/__init__.py` — replace

```python
    ReviewDefectDraft,
)
```

with

```python
    ReviewDefectDraft,
    ReviewNoteView,
)
```

`src/deep_research/agents/__init__.py` — replace

```python
    "ReviewDefectDraft",
    "MAX_OPTION_ROWS",
```

with

```python
    "ReviewDefectDraft",
    "ReviewNoteView",
    "MAX_OPTION_ROWS",
```

- [ ] **Step 4: Run the tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_note_routing.py tests/test_graph/test_state.py tests/test_graph/test_orchestrator.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: `90 passed`: `test_note_routing.py`'s `14`, the two updated files' Task 1 count of `50`, and `tests/test_imports.py`'s `26` (which now reaches `ReviewNoteView`, and finds no public name of `graph/*.py` missing from `deep_research.graph.__all__`).

- [ ] **Step 5: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4865 passed, 6 skipped, 12 deselected` (+14). Every existing route test passes unchanged: with no note due, `graph_route` reads exactly as it did.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/graph/state.py src/deep_research/graph/events.py src/deep_research/graph/nodes.py src/deep_research/graph/orchestrator.py src/deep_research/graph/__init__.py src/deep_research/agents/report_reviewer.py src/deep_research/agents/__init__.py tests/test_graph/test_note_routing.py tests/test_graph/test_state.py tests/test_graph/test_orchestrator.py
git commit -m "feat(graph): one targeted pass and one redraft per reader note, outside the review's own budgets"
```

---

### Task 4: The planner and the researcher read the notes (spec §4.6 table: Planning, Researching; AC16's first half)

**Files:**
- Create: `tests/test_agents/test_reader_notes_planning.py`
- Modify: `src/deep_research/agents/planner.py:37`, `:63` (imports), `:2707`, `:2719`, `:2730` (`plan_messages`), `:2849`, `:2862` (`plan_review_messages`), `:3135`, `:3160` (`_reader_notes`, `reader_notes_block`, `build_decision_context`), `:3203` (`run`), `:3267`, `:3353` (the two requests); `src/deep_research/agents/researcher.py:56` (imports), `:1486`, `:1731` (`extraction_messages`: the signature, and the block after `# Retrieved evidence`), `:3173` (`_reader_notes_block`), `:3374`, `:3511`, `:3843` (the three extraction requests), `:4384` (the `decide` closure)
- Test: `tests/test_graph/test_reader_notes_replay.py` (one test appended), `tests/test_evaluation/test_config.py` (two pins, by the snippet in Step 6)

**Interfaces:**
- Consumes: Task 2's renderer, `live_reader_notes`, `research_reader_notes` and board.
- Produces:
  - `plan_messages(..., reader_notes: str = "")` and `plan_review_messages(..., reader_notes: str = "")` add `# Reader notes\n{block}` after `# Reader answers` (Phase 2's section), only when the block is not empty.
  - `PlannerAgent.reader_notes_block()` (the state's notes plus the board's, active, under `PLANNING_NOTES`) feeds both requests, and `PlannerAgent.build_decision_context` returns it for every scoping turn (`""` without notes).
  - `extraction_messages(..., reader_notes: str = "")` adds `# Reader notes` as the request's last section, after `# Retrieved evidence` and so after `# Planned targets`, only when the block is not empty: no reader text then comes before the evidence's own `- target_id=` line, which the replay harness takes as the first match (ambiguity 3).
  - Every researcher decision turn appends `\n\n## Reader notes\n{block}` to its acquisition context, read from the board at that turn; every extraction carries the same notes under `EXTRACTION_NOTES`. Both leave `new_angle` notes out.

- [ ] **Step 1: Write the failing tests**

The unit tests pin each builder's section order and its byte-identity without notes. The planner test lands a note on the board while the plan request is in flight: the plan review, built after it, must carry it. The appended replay test runs the real graph with a bound board and lands a note after the first research turn: the first turn does not carry it and the last one does.

Create `tests/test_agents/test_reader_notes_planning.py`:

```python
"""The reader's notes in the planner's and the researcher's requests (live-briefs spec §4.6 table).

Every consumer renders the same block — the step's own lead sentence, then one
``- {restatement} ({kinds})`` line per active note — and adds nothing at all
when there is no note, so a run without notes builds byte-identical requests.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.planner import (
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
)
from deep_research.agents.prompts import STRUCTURED_REQUEST_END, AgentTask
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    PLANNING_NOTES,
    render_reader_notes,
)
from deep_research.agents.researcher import SubTopicTask, extraction_messages
from deep_research.agents.steps import ReActRun
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.types import ResearchState
from tests.agent_fakes import ScriptedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_sub_topic, fake_target
from tests.test_agents.test_planner import (
    _CLOCK_NOW,
    _planner,
    _review,
    _run,
    _sorting_plan,
)

QUESTION = "What are the current constraints on grid-scale battery storage deployment?"
AT = "2026-09-29T10:00:00.000+00:00"
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]


# --- planning -------------------------------------------------------------------


def test_the_plan_requests_carry_the_notes_block_only_when_there_is_one() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW)
    task = AgentTask(instruction=QUESTION)
    block = render_reader_notes(NOTES, instruction=PLANNING_NOTES)
    planned = plan_messages(task, _run(), contract=contract, reader_notes=block)[1].content
    reviewed = plan_review_messages(contract, [], reader_notes=block)[1].content

    assert planned.index("# Answer contract\n") < planned.index("# Reader notes\n") < planned.index("# Scoping notes\n")
    assert f"# Reader notes\n{block}\n" in planned
    assert reviewed.index("# Answer contract\n") < reviewed.index("# Reader notes\n") < reviewed.index("# Plan under review\n")
    assert plan_messages(task, _run(), contract=contract, reader_notes="") == plan_messages(task, _run(), contract=contract)
    assert plan_review_messages(contract, [], reader_notes="") == plan_review_messages(contract, [])


@pytest.mark.asyncio
async def test_the_planner_reads_the_notes_into_its_scoping_turn_and_both_requests(tracker: Tracker) -> None:
    board = NoteBoard()
    for note in NOTES:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    board.receive("while planning", received_at=AT, received_during="planner")
    later = fake_reader_note("n3", kinds=["exclude"], restatement="leave out pumped hydro", received_during="planner")

    def plan_while_a_note_arrives(messages: list, schema: type) -> object:
        board.add(later)  # interpreted while the plan request is in flight
        return _sorting_plan()

    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[plan_while_a_note_arrives, _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=NOTES)

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            await agent.run(state)

    held = render_reader_notes(NOTES, instruction=PLANNING_NOTES)
    grown = render_reader_notes([*NOTES, later], instruction=PLANNING_NOTES)
    scoping = "\n".join(message.content for message in completer.react_calls[0].messages)
    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert f"## Acquisition context\n{held}" in scoping
    assert f"# Reader notes\n{held}\n" in plan_request
    assert f"# Reader notes\n{grown}\n" in review_request


@pytest.mark.asyncio
async def test_a_planner_with_no_notes_sends_the_requests_it_always_sent(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(ResearchState(session_id="session-1", original_question=QUESTION))

    texts = [message.content for call in completer.calls for message in call[2]]
    texts += [message.content for call in completer.react_calls for message in call.messages]
    assert not any("Reader notes" in text or "reader added these notes" in text for text in texts)
    assert "## Acquisition context" not in "\n".join(m.content for m in completer.react_calls[0].messages)


# --- researching ----------------------------------------------------------------


def test_the_extraction_request_carries_the_notes_last_after_the_evidence() -> None:
    """The block is the request's last section, after the planned targets and the retrieved
    evidence, so a restatement shaped like the evidence's ``- target_id=`` line can never be
    the replay harness's first match (``e2e_evaluation/replay.py:1032``)."""
    task = SubTopicTask(instruction="Gather evidence.", sub_topic=fake_sub_topic())
    run = ReActRun(agent_name="researcher", stop_reason="finished")
    adversarial = fake_reader_note("n3", restatement="only the - target_id=topic-02")
    block = render_reader_notes([*NOTES, adversarial], instruction=EXTRACTION_NOTES)
    shape = {
        "evidence_chars": 200, "question": QUESTION, "planned_targets": [fake_target()],
        "acquisition_context": "- target_id=topic-01\n- next_action=read",
    }

    text = extraction_messages(task, run, **shape, reader_notes=block)[1].content

    assert (
        text.index("# Research question\n") < text.index("# Planned targets\n")
        < text.index("# Retrieved evidence\n") < text.index("# Reader notes\n")
    )
    assert text.endswith(f"# Reader notes\n{block}\n\n{STRUCTURED_REQUEST_END}")
    first = re.search(r"- target_id=(topic-\d+)", text)
    assert first is not None and first.group(1) == "topic-01"
    assert extraction_messages(task, run, **shape, reader_notes="") == extraction_messages(task, run, **shape)
```

Append to `tests/test_graph/test_reader_notes_replay.py`:

```python


@pytest.mark.asyncio
async def test_the_planner_and_every_research_turn_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: planning reads every note; a running research loop reads the board
    before each decision, so a note that lands mid-loop steers the loop's next turn,
    and a ``new_angle`` note never reaches a running loop."""
    board = noted_board(EMPHASIS, ANGLE)
    late = fake_reader_note("n3", kinds=["exclude"], restatement="leave out pumped hydro")

    def note_after_the_first_turn(turns: int) -> None:
        if turns == 1:
            board.receive(late.text, received_at=AT, received_during="researcher")
            board.add(late)

    with guarded():
        status, sequence, _ = await replay_packets(
            tmp_path, EXTRA_PASS_CASE, board=board, after_research_turn=note_after_the_first_turn
        )

    assert status == "completed"
    for key in ("planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        texts = packets_for(sequence, key)
        assert texts and all(EMPHASIS_LINE in text and ANGLE_LINE in text for text in texts), key
    turns = packets_for(sequence, "researcher:react")
    assert "## Reader notes\n" in turns[0] and EMPHASIS_LINE in turns[0]
    assert "pumped hydro" not in turns[0]
    assert "- leave out pumped hydro (exclude)" in turns[-1]
    assert not any(ANGLE_LINE in text for text in turns)
    extractions = packets_for(sequence, "researcher:SubTopicFindingsDraft")
    assert extractions and all("# Reader notes\n" in text and EMPHASIS_LINE in text for text in extractions)
    assert not any(ANGLE_LINE in text for text in extractions)
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py -q 2>&1 | tail -8
```

Expected: four `FAILED` lines, then `4 failed, 3 passed`. `test_the_plan_requests_carry_the_notes_block_only_when_there_is_one` and `test_the_extraction_request_carries_the_notes_last_after_the_evidence` fail with `TypeError: … got an unexpected keyword argument 'reader_notes'`; `test_the_planner_reads_the_notes_into_its_scoping_turn_and_both_requests` finds no notes under `## Acquisition context`; `test_the_planner_and_every_research_turn_carry_the_notes` finds a `planner:react` turn without them. The three that pass are `test_a_planner_with_no_notes_sends_the_requests_it_always_sent` and Task 2's two digest cases.

- [ ] **Step 3: Put the notes into the planner's and the researcher's requests**

**`src/deep_research/agents/planner.py`**: 12 edits, in file order.

`src/deep_research/agents/planner.py` — replace

```python
from deep_research.agents.events import agent_event, publish_live
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.toolset import AgentToolset
```

with

```python
from deep_research.agents.events import agent_event, publish_live
from deep_research.agents.reader_notes import (
    PLANNING_NOTES,
    live_reader_notes,
    render_reader_notes,
)
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.steps import ReActRun, ReActStep, summarize_text
from deep_research.agents.toolset import AgentToolset
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
    EvidenceTarget,
    FigureKind,
    MemorySnapshot,
    ReaderAnswer,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
```

with

```python
    EvidenceTarget,
    FigureKind,
    MemorySnapshot,
    ReaderAnswer,
    ReaderNote,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
    plan_under_repair: Sequence[SubTopic] = (),
    reader_answers: Sequence[ReaderAnswer] = (),
) -> list[ChatMessage]:
```

with

```python
    plan_under_repair: Sequence[SubTopic] = (),
    reader_answers: Sequence[ReaderAnswer] = (),
    reader_notes: str = "",
) -> list[ChatMessage]:
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
    ``reader_answers`` add a ``# Reader answers`` section after the contract,
    only when there are any (live-briefs spec §4.4).
    """
```

with

```python
    ``reader_answers`` add a ``# Reader answers`` section after the contract,
    only when there are any (live-briefs spec §4.4). ``reader_notes`` is the
    rendered ``# Reader notes`` block (``agents.reader_notes``), added after
    them only when it is not empty (live-briefs spec §4.6).
    """
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
        material.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
    if task.guidance.strip():
```

with

```python
        material.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
    if reader_notes:
        material.append(f"# Reader notes\n{reader_notes}")
    if task.guidance.strip():
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
    reader_answers: Sequence[ReaderAnswer] = (),
) -> list[ChatMessage]:
    """Build the one tool-free request that reviews a plan's meaning.

    ``reader_answers`` add the same ``# Reader answers`` section the plan
    request carries, only when there are any, so the review does not flag a
    narrowing the reader asked for as a missing dimension (spec §4.4).
    """
```

with

```python
    reader_answers: Sequence[ReaderAnswer] = (),
    reader_notes: str = "",
) -> list[ChatMessage]:
    """Build the one tool-free request that reviews a plan's meaning.

    ``reader_answers`` add the same ``# Reader answers`` section the plan
    request carries, only when there are any, so the review does not flag a
    narrowing the reader asked for as a missing dimension (spec §4.4).
    ``reader_notes`` adds the plan request's ``# Reader notes`` block after
    them, for the same reason, only when it is not empty (spec §4.6).
    """
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
        sections.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
    sections += [
```

with

```python
        sections.append(f"# Reader answers\n{render_reader_answers(reader_answers)}")
    if reader_notes:
        sections.append(f"# Reader notes\n{reader_notes}")
    sections += [
```

`src/deep_research/agents/planner.py` — replace

```python
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the coverage ids this session already planned, so a later
```

with

```python
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the reader's notes received before planning started. Notes
        # that arrive while the planner runs are read from the run's board.
        self._reader_notes: tuple[ReaderNote, ...] = ()
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the coverage ids this session already planned, so a later
```

`src/deep_research/agents/planner.py` — replace

```python
            guidance=planner_guidance(state.memory_context),
        )
```

with

```python
            guidance=planner_guidance(state.memory_context),
        )

    def reader_notes_block(self) -> str:
        """The ``# Reader notes`` block as of now, or ``""`` (live-briefs spec §4.6).

        The notes the run was handed plus any that arrived since, from the
        run's board, so a note sent while the planner scopes the question
        reaches the plan it is about to draft.
        """
        return render_reader_notes(
            live_reader_notes(self._reader_notes), instruction=PLANNING_NOTES
        )

    def build_decision_context(
        self,
        task: AgentTask,
        *,
        iteration: int,
        steps: Sequence[ReActStep],
    ) -> str:
        """The scoping loop's per-turn context: the reader's notes, when there are any.

        Empty without notes, so the scoping turn's request is exactly what it
        was before notes existed (live-briefs spec §4.6).
        """
        del task, iteration, steps
        return self.reader_notes_block()
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
        self._reader_answers = tuple(state.reader_answers)
        self._planned_coverage_ids = frozenset(
```

with

```python
        self._reader_answers = tuple(state.reader_answers)
        self._reader_notes = tuple(state.reader_notes)
        self._planned_coverage_ids = frozenset(
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
                    plan_under_repair=plan_under_repair,
                    reader_answers=self._reader_answers,
                ),
```

with

```python
                    plan_under_repair=plan_under_repair,
                    reader_answers=self._reader_answers,
                    reader_notes=self.reader_notes_block(),
                ),
```

`src/deep_research/agents/planner.py` (anchor as written by Phase 2 Task 3) — replace

```python
                    repair=already_requested,
                    reader_answers=self._reader_answers,
                ),
```

with

```python
                    repair=already_requested,
                    reader_answers=self._reader_answers,
                    reader_notes=self.reader_notes_block(),
                ),
```

**`src/deep_research/agents/researcher.py`**: 8 edits, in file order.

`src/deep_research/agents/researcher.py` — replace

```python
from deep_research.agents.react import run_react_loop
from deep_research.agents.sources import normalize_source_url, publisher_identity
```

with

```python
from deep_research.agents.react import run_react_loop
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    RESEARCH_NOTES,
    live_reader_notes,
    render_reader_notes,
    research_reader_notes,
)
from deep_research.agents.sources import normalize_source_url, publisher_identity
```

`src/deep_research/agents/researcher.py` — replace

```python
    coverage_titles: Mapping[str, str] | None = None,
) -> list[ChatMessage]:
    """Build the messages that extract findings from one finished loop.
```

with

```python
    coverage_titles: Mapping[str, str] | None = None,
    reader_notes: str = "",
) -> list[ChatMessage]:
    """Build the messages that extract findings from one finished loop.

    ``reader_notes`` is the rendered reader-notes block (live-briefs spec
    §4.6), printed as ``# Reader notes`` so extraction respects a note's
    scope; an empty block adds nothing. It is the last section, after the
    retrieved evidence: no reader text then comes before the evidence's own
    ``- target_id=`` line, which the replay harness reads as the first match
    (``e2e_evaluation/replay.py``).
```

`src/deep_research/agents/researcher.py` — replace

```python
                else render_evidence(
                    run, limit=evidence_chars, discovery_payloads=False
                )
            )
        )
    )
    static = [
```

with

```python
                else render_evidence(
                    run, limit=evidence_chars, discovery_payloads=False
                )
            )
        )
    )
    if reader_notes:
        sections.append(f"# Reader notes\n{reader_notes}")
    static = [
```

`src/deep_research/agents/researcher.py` — replace

```python

    def _planned_targets(self) -> list[EvidenceTarget]:
```

with

```python

    def _reader_notes_block(self, instruction: str) -> str:
        """The reader's notes a request built now carries, or ``""`` (live-briefs spec §4.6).

        The notes this pass was handed plus any that arrived since, from the
        run's board, less ``new_angle`` notes: those wait for the review's
        note pass rather than steering a loop already running.
        """
        state_notes = (
            self._run_source_state.reader_notes
            if self._run_source_state is not None
            else ()
        )
        return render_reader_notes(
            research_reader_notes(live_reader_notes(state_notes)),
            instruction=instruction,
        )

    def _planned_targets(self) -> list[EvidenceTarget]:
```

`src/deep_research/agents/researcher.py` — replace

```python
                            read_ids=[read_id],
                        ),
                        planned_targets=planned_targets,
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
```

with

```python
                            read_ids=[read_id],
                        ),
                        planned_targets=planned_targets,
                        question=question,
                        coverage_titles=coverage_titles,
                        reader_notes=self._reader_notes_block(EXTRACTION_NOTES),
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
```

`src/deep_research/agents/researcher.py` — replace

```python
                        disputed_statements=dissent_statements or (),
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
```

with

```python
                        disputed_statements=dissent_statements or (),
                        question=question,
                        coverage_titles=coverage_titles,
                        reader_notes=self._reader_notes_block(EXTRACTION_NOTES),
                    ),
                    SubTopicFindingsDraft,
```

`src/deep_research/agents/researcher.py` — replace

```python
                            else None
                        ),
                        planned_targets=planned_targets,
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
```

with

```python
                            else None
                        ),
                        planned_targets=planned_targets,
                        question=question,
                        coverage_titles=coverage_titles,
                        reader_notes=self._reader_notes_block(EXTRACTION_NOTES),
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
```

`src/deep_research/agents/researcher.py` — replace

```python
        ) -> tuple[ReActDecision, ...]:
            return await self._complete_react_decision(
                task,
                iteration=iteration,
                steps=steps,
                decision_context=policy.context(
                    limit=self._decision_context_chars, for_decision=True
                ),
```

with

```python
        ) -> tuple[ReActDecision, ...]:
            # live-briefs spec §4.6: before each model call the loop reads the
            # run's board, so a note that arrived since its last turn steers
            # this one, under its own ``## Reader notes`` heading.
            context = policy.context(
                limit=self._decision_context_chars, for_decision=True
            )
            notes = self._reader_notes_block(RESEARCH_NOTES)
            return await self._complete_react_decision(
                task,
                iteration=iteration,
                steps=steps,
                decision_context=(
                    f"{context}\n\n## Reader notes\n{notes}" if notes else context
                ),
```

- [ ] **Step 4: Run the tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_planner.py tests/test_agents/test_planner_reader_answers.py tests/test_agents/test_researcher.py -q 2>&1 | tail -1
```

Expected: `540 passed`. The replay file's digest test still passes: without notes, no request changed.

- [ ] **Step 5: See the two pins move**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | grep -E "^E +\{|passed|failed"
```

Expected: `1 failed, 10 passed, 67 deselected`. `test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift` reports `{'planner': 'd1ba46ce147f'} != {'planner': 'e9b74316ad14'}` and `{'researcher': 'a8c9528f0c20'} != {'researcher': 'de7506bed63e'}` (the two lines come in either order): the modules' code changed, and `agents/prompts.py` did not.

- [ ] **Step 6: Re-pin the planner and the researcher (module code only)**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - \
  planner "Live briefs Phase 3 (live-briefs spec §4.6, 2026-09-29): the planner puts the reader's notes, only when there are any, in a # Reader notes section of the plan and plan-review requests and in the scoping turn's decision context. Without notes every request is byte-identical; agents.prompts was untouched, so the evidence_verifier pin and the Judge pin are unchanged." \
  researcher "Live briefs Phase 3 (live-briefs spec §4.6, 2026-09-29): the researcher reads the reader's notes from the run's board before every decision turn (## Reader notes) and into its extraction requests (# Reader notes), only when there are any. Without notes every request is byte-identical; agents.prompts was untouched." <<'EOF'
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
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | tail -1
```

Expected: `planner: e9b74316ad14 -> d1ba46ce147f`, `researcher: de7506bed63e -> a8c9528f0c20`, then `11 passed, 67 deselected`.

The snippet reads the current pin, so it works whatever value Phase 2 left, and it writes the comment above the pin in the file's existing ``Moved `old` -> `new`.`` style. The new values are the ones planning produced from exactly this task's text. A different value is acceptable when `git diff -- src/deep_research/agents/planner.py src/deep_research/agents/researcher.py` shows only this task's edits and every test in this task passes: the snippet pins whatever the modules now hash to, so do not hunt for single characters.

- [ ] **Step 7: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4870 passed, 6 skipped, 12 deselected` (+5). Every existing replay test passes unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/agents/planner.py src/deep_research/agents/researcher.py tests/test_agents/test_reader_notes_planning.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(agents): the planner and the researcher read the reader's notes, the researcher at every turn"
```

---

### Task 5: The source evaluator and the writer read the notes; the report names an uncovered note (spec §4.6 table: Evaluating sources, Writing; the note-pass paragraph's last sentence)

**Files:**
- Create: `tests/test_agents/test_reader_notes_writing.py`
- Modify: `src/deep_research/agents/source_evaluator.py:57`, `:76` (imports), `:1102` (`build_task`'s `guidance`); `src/deep_research/agents/report_writer.py:45`, `:108` (imports), `:516` (`ReportWriterTask.reader_notes`), `:1015` (`_is_redraft_hop`), `:1110` (`section_messages`), `:1194` (`bottom_line_messages`), `:3221` (`build_task`); `src/deep_research/agents/report.py:59` (imports), `:1481`, `:1500` (`_note_groups`, `_could_not_confirm_groups`)
- Test: `tests/test_graph/test_reader_notes_replay.py` (one test appended), `tests/test_evaluation/test_config.py` (two pins, by Task 4's snippet)

**Interfaces:**
- Consumes: Task 2's renderer; Task 3's `note_sub_topic` and loop markers.
- Produces:
  - The scoring request's `# Context` slot (`agents/source_evaluator.py:858-859`, unused until now) carries the active notes under `SOURCE_NOTES` — relevance only, never authority or recency — and is empty without notes.
  - `ReportWriterTask.reader_notes: str = ""`; `section_messages` and `bottom_line_messages` add `# Reader notes` right after `# Answer form`, only when it is not empty.
  - `_is_redraft_hop(state)` reads the latest loop marker at the same iteration: after `graph.report.redraft_requested` the draft is a redraft; after `graph.note_pass.started` or `graph.note_redraft.requested` it is a fresh draft.
  - `render_written_report` lists each note sub-topic with an unanswered target once, as `Couldn't find evidence for your note: {restatement}`, and each note sub-topic with a target a verified finding answers but no printed statement states once, as `We found sources on your note but could not state a checked answer: {restatement}` (right after the group `We found sources on these but could not state a checked answer:`, which no longer lists a note's target); so it never lists a note's derived questions in any group (ambiguity 18). `answered_not_stated_targets` is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_reader_notes_writing.py`:

```python
"""The reader's notes in the writer's drafts and in the report (live-briefs spec §4.6)."""

from __future__ import annotations

from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import _is_redraft_hop
from deep_research.graph.nodes import note_sub_topic
from deep_research.utils.types import ResearchEvent
from tests.graph_fakes import (
    fake_reader_note,
    fake_research_state,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    verified_pass,
)

# --- writing and the report ----------------------------------------------------------


def _event(event_type: str) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="A loop marker.")


def test_a_note_loop_makes_the_next_draft_a_fresh_one() -> None:
    """spec §4.6: after a note pass or a note redraft the writer drafts afresh, while the
    review's own redraft still patches only the parts its defects name."""
    base = fake_research_state()
    drafted = base.model_copy(update={"composition": fake_writer_composition(base)})

    def hop(*markers: str) -> bool:
        return _is_redraft_hop(drafted.model_copy(update={"events": [_event(m) for m in markers]}))

    assert hop() is True
    assert hop("graph.report.redraft_requested") is True
    assert hop("graph.report.redraft_requested", "graph.note_pass.started") is False
    assert hop("graph.note_redraft.requested") is False
    assert hop("graph.note_redraft.requested", "graph.report.redraft_requested") is True
    assert _is_redraft_hop(base) is False


def test_the_report_names_a_note_it_found_no_evidence_for() -> None:
    """spec §4.6: "A note that is still uncovered after its pass ends in the report as
    'Couldn't find evidence for your note: …'" — listed by the note, never by its questions."""
    angled = fake_reader_note(
        "n2", kinds=["new_angle"], restatement="how battery cells are recycled",
        new_questions=["How are battery cells recycled at end of life?"],
    )
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
    )

    report = render_written_report(fake_writer_composition(state))
    confirm = report[report.index("## What we couldn't confirm"):]

    assert confirm.startswith(
        "## What we couldn't confirm\n\n"
        "This run did not research:\n- What did battery storage additions reach?\n\n"
        "Couldn't find evidence for your note: how battery cells are recycled\n"
    )
    assert "How are battery cells recycled at end of life?" not in confirm


def test_a_note_answered_but_never_stated_is_named_by_the_note_too() -> None:
    """A note target a verified finding answers, but that no printed statement states, is
    disclosed by the note, never by the question the run derived from it."""
    angled = fake_reader_note(
        "n2", kinds=["new_angle"], restatement="how battery cells are recycled",
        new_questions=["How are battery cells recycled at end of life?"],
    )
    one = verified_pass(target_ids=["note-n2-target-01"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
        verified_findings=[one.finding],
    )
    composition = fake_writer_composition(state).model_copy(update={"summary": []})

    report = render_written_report(composition)
    confirm = report[report.index("## What we couldn't confirm"):]

    assert answered_not_stated_targets(composition) == ["note-n2-target-01"]
    assert "We found sources on your note but could not state a checked answer: how battery cells are recycled\n" in confirm
    assert "We found sources on these but could not state a checked answer:" not in confirm
    assert "How are battery cells recycled at end of life?" not in confirm
```

Append to `tests/test_graph/test_reader_notes_replay.py`:

```python


@pytest.mark.asyncio
async def test_the_source_evaluator_and_the_writer_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: the scoring request's ``# Context`` slot carries the notes, for
    relevance only; the writer's section and bottom-line requests carry
    ``# Reader notes`` right after the answer form."""
    with guarded():
        status, sequence, _ = await replay_packets(tmp_path, EXTRA_PASS_CASE, board=noted_board(EMPHASIS, ANGLE))

    assert status == "completed"
    scoring = packets_for(sequence, "source_evaluator:SourceScoresDraft")
    assert scoring and all(
        "# Context\nThe reader added these notes while the run was going. They bear on how relevant"
        in text and EMPHASIS_LINE in text and ANGLE_LINE in text
        for text in scoring
    )
    for key, after in (
        ("report_writer:SectionDraft", "# This part of the question\n"),
        ("report_writer:BottomLineDraft", "# Checked statements\n"),
    ):
        texts = packets_for(sequence, key)
        assert texts, key
        for text in texts:
            assert text.index("# Answer form\n") < text.index("# Reader notes\n") < text.index(after), key
            assert EMPHASIS_LINE in text and ANGLE_LINE in text, key
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_writing.py tests/test_graph/test_reader_notes_replay.py -q 2>&1 | tail -6
```

Expected: four `FAILED` lines, then `4 failed, 3 passed`: `test_a_note_loop_makes_the_next_draft_a_fresh_one` (after `graph.note_pass.started` the writer still takes its draft for a redraft), `test_the_report_names_a_note_it_found_no_evidence_for` (the note's question is listed under "This run did not research:", and no line names the note), `test_a_note_answered_but_never_stated_is_named_by_the_note_too` (the note's question is listed under "We found sources on these but could not state a checked answer:", and no line names the note) and `test_the_source_evaluator_and_the_writer_carry_the_notes`. The three that pass are Task 2's two digest cases and Task 4's replay test.

- [ ] **Step 3: Put the notes into scoring and writing, and name the uncovered note in the report**

**`src/deep_research/agents/source_evaluator.py`**: 3 edits, in file order.

`src/deep_research/agents/source_evaluator.py` — replace

```python
)
from deep_research.agents.sources import (
```

with

```python
)
from deep_research.agents.reader_notes import SOURCE_NOTES, render_reader_notes
from deep_research.agents.sources import (
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)
```

with

```python
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
    active_reader_notes,
)
```

`src/deep_research/agents/source_evaluator.py` — replace

```python
            instruction=state.original_question,
            groups=groups,
```

with

```python
            instruction=state.original_question,
            # live-briefs spec §4.6: the reader's notes fill the request's
            # ``# Context`` slot, for relevance only; ``""`` without notes.
            guidance=render_reader_notes(
                active_reader_notes(state.reader_notes), instruction=SOURCE_NOTES
            ),
            groups=groups,
```

**`src/deep_research/agents/report_writer.py`**: 7 edits, in file order.

`src/deep_research/agents/report_writer.py` — replace

```python
)
from deep_research.agents.report import (
```

with

```python
)
from deep_research.agents.reader_notes import WRITING_NOTES, render_reader_notes
from deep_research.agents.report import (
```

`src/deep_research/agents/report_writer.py` — replace

```python
    WriterPointDraft,
)
```

with

```python
    WriterPointDraft,
    active_reader_notes,
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
    one, else ``agents.report_target_words`` (spec §6.3)."""
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
```

with

```python
    one, else ``agents.report_target_words`` (spec §6.3)."""
    reader_notes: str = ""
    """The rendered reader-notes block (live-briefs spec §4.6), printed as
    ``# Reader notes`` in every section and bottom-line request; ``""`` for a
    run without notes, whose requests are then exactly what they were."""
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
```

`src/deep_research/agents/report_writer.py` — replace

```python
    new finding written from scratch.
    """
    return state.composition is not None and state.composition.iteration == state.iteration
```

with

```python
    new finding written from scratch.

    A reader note's loops keep the iteration (live-briefs spec §4.6) and read
    as a fresh draft too: after a note pass the draft is over new evidence,
    and a note redraft drafts every part afresh with the notes. The latest
    loop marker decides.
    """
    if state.composition is None or state.composition.iteration != state.iteration:
        return False
    for event in reversed(state.events):
        if event.event_type == "graph.report.redraft_requested":
            return True
        if event.event_type in ("graph.note_pass.started", "graph.note_redraft.requested"):
            return False
    return True
```

`src/deep_research/agents/report_writer.py` — replace

```python
        f"# Answer form\n{_answer_form_line(task)}",
        f"# This part of the question\n{job.sub_topic_title}\n{targets_block}",
    ]
    material.append(
```

with

```python
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(
        f"# This part of the question\n{job.sub_topic_title}\n{targets_block}"
    )
    material.append(
```

`src/deep_research/agents/report_writer.py` — replace

```python
        f"# Answer form\n{_answer_form_line(task)}",
        f"# Checked statements\n{statements_block}",
    ]
    if dispute_lines:
```

with

```python
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(f"# Checked statements\n{statements_block}")
    if dispute_lines:
```

`src/deep_research/agents/report_writer.py` — replace

```python
            target_words=budget_words,
            authority_floor=authority_floor,
```

with

```python
            target_words=budget_words,
            reader_notes=render_reader_notes(
                active_reader_notes(state.reader_notes), instruction=WRITING_NOTES
            ),
            authority_floor=authority_floor,
```

**`src/deep_research/agents/report.py`**: 3 edits, in file order.

`src/deep_research/agents/report.py` — replace

```python
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
```

with

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    QUALITY_STATUS_ACCEPTED,
```

`src/deep_research/agents/report.py` — replace

```python
    ]


def _could_not_confirm_groups(composition: ReportComposition) -> list[list[str]]:
    """§10: each present group, in order -- searched targets, unsearched
    targets, answered-but-unstated targets, failed parts, unreachable pages
    (capped at 5, Q5)."""
    groups: list[list[str]] = []
    searched = [target for target in composition.not_found if target.searched]
    unsearched = [target for target in composition.not_found if not target.searched]
    if searched:
        groups.append([
            "We found no source we could check that answers:",
```

with

```python
    ]


def _note_groups(
    composition: ReportComposition, target_ids: set[str], lead: str
) -> list[list[str]]:
    """live-briefs spec §4.6: one line, ``lead`` then the note as the run read
    it, per reader-note sub-topic with a target in ``target_ids``, in plan order.

    A note's targets are listed by the note, never by question: the reader
    wrote a note, not the questions the run derived from it.
    """
    return [
        [lead + topic.title.removeprefix(NOTE_TOPIC_TITLE_PREFIX)]
        for topic in composition.sub_topics
        if topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX)
        and any(target.target_id in target_ids for target in topic.evidence_targets)
    ]


def _could_not_confirm_groups(composition: ReportComposition) -> list[list[str]]:
    """§10: each present group, in order -- searched targets, unsearched
    targets, the reader's uncovered notes (live-briefs spec §4.6),
    answered-but-unstated targets, the reader's answered-but-unstated notes,
    failed parts, unreachable pages (capped at 5, Q5)."""
    groups: list[list[str]] = []
    rows = [
        target
        for target in composition.not_found
        if not target.target_id.startswith(NOTE_COVERAGE_PREFIX)
    ]
    searched = [target for target in rows if target.searched]
    unsearched = [target for target in rows if not target.searched]
    if searched:
        groups.append([
            "We found no source we could check that answers:",
```

`src/deep_research/agents/report.py` — replace

```python
        ])
    unstated_ids = answered_not_stated_targets(composition)
    if unstated_ids:
        targets_by_id = {
            target.target_id: target
            for topic in composition.sub_topics for target in topic.evidence_targets
        }
        groups.append([
            "We found sources on these but could not state a checked answer:",
            *[f"- {targets_by_id[t].question}" for t in unstated_ids if t in targets_by_id],
        ])
    failed = [part for part in composition.parts if part.status == "failed"]
```

with

```python
        ])
    groups.extend(_note_groups(
        composition,
        {row.target_id for row in composition.not_found},
        "Couldn't find evidence for your note: ",
    ))
    unstated = answered_not_stated_targets(composition)
    unstated_ids = [t for t in unstated if not t.startswith(NOTE_COVERAGE_PREFIX)]
    if unstated_ids:
        targets_by_id = {
            target.target_id: target
            for topic in composition.sub_topics for target in topic.evidence_targets
        }
        groups.append([
            "We found sources on these but could not state a checked answer:",
            *[f"- {targets_by_id[t].question}" for t in unstated_ids if t in targets_by_id],
        ])
    groups.extend(_note_groups(
        composition,
        set(unstated),
        "We found sources on your note but could not state a checked answer: ",
    ))
    failed = [part for part in composition.parts if part.status == "failed"]
```

- [ ] **Step 4: Run the tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_writing.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_report_writer.py tests/test_agents/test_report.py tests/test_agents/test_report_layout.py -q 2>&1 | tail -1
```

Expected: `350 passed`.

- [ ] **Step 5: See the two pins move**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | grep -E "^E +\{|passed|failed"
```

Expected: `2 failed, 9 passed, 67 deselected`. `test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift` reports `{'report_writer': '6e1aedc2888e'} != {'report_writer': '0dcd4a41a378'}` and `{'source_evaluator': '24809aa975a3'} != {'source_evaluator': 'fb7f60d73873'}`, in either order; `test_the_target_fingerprint_covers_the_shared_prompt_module` fails too, because it compares the writer's fingerprint with its pin.

- [ ] **Step 6: Re-pin the source evaluator and the writer (module code only)**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" - \
  source_evaluator "Live briefs Phase 3 (live-briefs spec §4.6, 2026-09-29): the source evaluator puts the reader's notes, only when there are any, in the scoring request's # Context slot, for relevance only. Without notes every request is byte-identical; agents.prompts was untouched." \
  report_writer "Live briefs Phase 3 (live-briefs spec §4.6, 2026-09-29): the writer puts the reader's notes, only when there are any, in a # Reader notes section of the section and bottom-line requests, and drafts afresh after a note pass or a note redraft. Without notes every request is byte-identical; agents.prompts was untouched." <<'EOF'
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
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_evaluation/test_config.py -q -k fingerprint 2>&1 | tail -1
```

Expected: `source_evaluator: fb7f60d73873 -> 24809aa975a3`, `report_writer: 0dcd4a41a378 -> 6e1aedc2888e`, then `11 passed, 67 deselected`. As in Task 4 Step 6, a different value is acceptable when `git diff -- src/deep_research/agents/source_evaluator.py src/deep_research/agents/report_writer.py` shows only this task's edits and every test in this task passes. `agents/report.py` is not part of any agent's fingerprint.

- [ ] **Step 7: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4874 passed, 6 skipped, 12 deselected` (+4).

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py src/deep_research/agents/report_writer.py src/deep_research/agents/report.py tests/test_agents/test_reader_notes_writing.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(agents): scoring and writing read the reader's notes; the report names a note it found no evidence for"
```

---

### Task 6: The review judges each note, replay answers it, and AC16 on the real graph (spec §4.6 table: Reviewing, Verifying evidence; AC16; D10)

**Files:**
- Create: `tests/test_agents/test_reader_notes_review.py`
- Modify: `src/deep_research/agents/report_reviewer.py` (line numbers as Task 3 leaves it) — imports (`:61`, `:104`); the notes example (`:403`); `NoteDispositionDraft`, `ReportReviewNotesDraft` (`:1108`, `:1124`); `_reader_notes_block` and `review_messages` (`:1311`, `:1336`, `:1346`); `_note_dispositions` (`:1488`); `_merge_review` (`:1556`, `:1580`); `_review_packet` (`:1952`, `:2010`); the scoped packet's carried verdicts and its fingerprint (`:2147`, `:2155`, `:2390`); the scoped example, messages, schema and merge (`:2464`–`:2828`); `__all__` (`:2861`). `src/deep_research/e2e_evaluation/replay.py:57` (imports), `:1532`, `:1594` (the two notes replies). `src/deep_research/agents/__init__.py:322`, `:810` (exports).
- Test: `tests/test_graph/test_reader_notes_replay.py` (the AC16 test appended)

**Interfaces:**
- Consumes: Task 2's renderer and types; Task 3's `ReportReviewInput.reader_notes` and the review node that marks notes reviewed; Tasks 4–5 (every other consumer already carries the block).
- Produces:
  - `ScopedReportReviewInput.carried_note_dispositions`, left out of the scoped packet's fingerprint while empty, as Task 3 leaves out its base's `reader_notes`.
  - `review_messages` and `scoped_review_messages` add `# Reader notes` — `REVIEW_NOTES`, then `- n1: {restatement} ({kinds})` per note — after `# Answer contract`, and show the notes example in the reply format, only when the packet carries notes.
  - `NoteDispositionDraft(note_id, status: str)`; `ReportReviewNotesDraft(ReportReviewDraft)` and `ScopedReportReviewNotesDraft(ScopedReportReviewDraft)` add `note_dispositions` and are requested only for a packet with notes; `ReportReviewDraft` and `ScopedReportReviewDraft` are unchanged.
  - `ReportReview.note_dispositions` holds one valid verdict per note the packet carried; a scoped re-review keeps the carried verdicts it does not judge again.
  - `ReplayCompleter` answers the two notes schemas with the base reply plus `honoured` for every note its request lists.

- [ ] **Step 1: Write the failing tests**

The reviewer tests reuse `tests/test_agents/test_report_reviewer.py`'s written-report fixtures. The conventions test holds the two notes requests to `test_tool_free_prompts.py`'s matrix: one reply format, static sections first, every field in it, every example valid. The AC16 test runs the real graph with two notes on a bound board and reads every request it sent.

Create `tests/test_agents/test_reader_notes_review.py`:

```python
"""The review's verdict on each reader note (live-briefs spec §4.6 "Reviewing")."""

from __future__ import annotations

import json

import pytest

from deep_research.agents.prompts import STRUCTURED_REQUEST_END
from deep_research.agents.reader_notes import REVIEW_NOTES, render_reader_notes
from deep_research.agents.report_reviewer import (
    REVIEW_DIMENSIONS,
    NoteDispositionDraft,
    PreviousDefectResolutionDraft,
    ReportReviewer,
    ReportReviewNotesDraft,
    ReviewDimensionScores,
    ScopedReportReviewNotesDraft,
    StatementDispositionDraft,
    build_report_review_input,
    build_scoped_report_review_input,
    remap_review_for_redraft,
    review_messages,
    scoped_review_messages,
)
from deep_research.utils.types import NoteDisposition
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import fake_reader_note
from tests.test_agents.test_report_reviewer import _draft as review_draft
from tests.test_agents.test_report_reviewer import _redraft_fixture as redraft_fixture
from tests.test_agents.test_report_reviewer import _redraft_state as redraft_state
from tests.test_agents.test_report_reviewer import state_with_written_report
from tests.test_agents.test_tool_free_prompts import (
    _labelled_examples as labelled_examples,
)
from tests.test_agents.test_tool_free_prompts import (
    _request_envelope as request_envelope,
)

NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]


# --- reviewing ------------------------------------------------------------------------


def _notes_draft(*verdicts: tuple[str, str]) -> ReportReviewNotesDraft:
    plain = review_draft()
    return ReportReviewNotesDraft(
        **plain.model_dump(),
        note_dispositions=[NoteDispositionDraft(note_id=n, status=s) for n, s in verdicts],
    )


def test_the_review_lists_each_note_with_its_id_and_asks_for_one_verdict_each() -> None:
    plain = build_report_review_input(state_with_written_report())
    noted = build_report_review_input(state_with_written_report(reader_notes=NOTES))
    body = review_messages(noted)[1].content
    block = render_reader_notes(noted.reader_notes, instruction=REVIEW_NOTES, with_ids=True)

    assert [(view.note_id, view.restatement, view.kinds) for view in noted.reader_notes] == [
        ("n1", "more weight on fire-safety standards", ["emphasis"]),
        ("n2", "only the United States", ["scope"]),
    ]
    assert body.index("# Answer contract\n") < body.index("# Reader notes\n") < body.index("# Reader content")
    assert f"# Reader notes\n{block}\n" in body
    assert "- n1: more weight on fire-safety standards (emphasis)" in block
    reply_format = body[body.index("# Reply format"):body.index("\n# ", body.index("# Reply format"))]
    assert '"note_dispositions"' in reply_format
    assert "Reader notes" not in review_messages(plain)[1].content
    assert '"note_dispositions"' not in review_messages(plain)[1].content
    assert plain.reader_notes == [] and plain.fingerprint != noted.fingerprint


def test_a_replaced_note_is_not_put_to_the_review() -> None:
    replacing = fake_reader_note("n3", restatement="only the European Union", replaces="n2", kinds=["scope"])
    noted = build_report_review_input(state_with_written_report(reader_notes=[*NOTES, replacing]))

    assert [view.note_id for view in noted.reader_notes] == ["n1", "n3"]


@pytest.mark.asyncio
async def test_each_notes_verdict_is_kept_once_and_an_unknown_one_is_dropped() -> None:
    completer = ScriptedCompleter(outputs=[_notes_draft(
        ("n1", "honoured"), ("n1", "no_evidence"), ("n2", "maybe"),
        ("n2", "ignored_with_evidence"), ("n9", "honoured"),
    )])
    noted = build_report_review_input(state_with_written_report(reader_notes=NOTES))

    review = await ReportReviewer(provider=completer).review(noted, previous=None)

    assert completer.calls[0][0] == "ReportReviewNotesDraft"
    assert review.status == "scored"
    assert [(d.note_id, d.status) for d in review.note_dispositions] == [
        ("n1", "honoured"), ("n2", "ignored_with_evidence"),
    ]


@pytest.mark.asyncio
async def test_a_review_without_notes_asks_the_schema_it_always_asked() -> None:
    completer = ScriptedCompleter(outputs=[review_draft()])

    review = await ReportReviewer(provider=completer).review(
        build_report_review_input(state_with_written_report()), previous=None
    )

    assert completer.calls[0][0] == "ReportReviewDraft"
    assert review.note_dispositions == []


@pytest.mark.asyncio
async def test_a_scoped_rereview_keeps_the_note_verdicts_it_did_not_judge_again() -> None:
    old, new, previous_review = redraft_fixture()
    judged = previous_review.model_copy(update={"note_dispositions": [
        NoteDisposition(note_id="n1", status="honoured"),
        NoteDisposition(note_id="n2", status="ignored_with_evidence"),
    ]})
    remapped = remap_review_for_redraft(judged, previous_composition=old, composition=new)
    assert remapped is not None
    scoped = build_scoped_report_review_input(
        redraft_state(new).model_copy(update={"reader_notes": NOTES}), previous_review=remapped
    )
    assert scoped is not None
    assert [d.note_id for d in scoped.carried_note_dispositions] == ["n1", "n2"]
    assert "# Reader notes\n" in scoped_review_messages(scoped)[1].content
    reply = ScopedReportReviewNotesDraft(
        dimensions=ReviewDimensionScores(**{name: 1.0 for name in REVIEW_DIMENSIONS}),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[PreviousDefectResolutionDraft(defect_id="review-01", resolved=True, note="Fixed.")],
        new_defects=[],
        rationale="Re-checked the report as it now stands.",
        note_dispositions=[NoteDispositionDraft(note_id="n2", status="honoured")],
    )
    completer = ScriptedCompleter(outputs=[reply])

    review = await ReportReviewer(provider=completer).review_scoped(scoped)

    assert completer.calls[0][0] == "ScopedReportReviewNotesDraft"
    assert [(d.note_id, d.status) for d in review.note_dispositions] == [("n1", "honoured"), ("n2", "honoured")]


def test_the_notes_review_requests_keep_the_shared_reply_conventions() -> None:
    """The matrix in ``test_tool_free_prompts`` holds for the notes schemas too: one
    reply format, static sections first, every field in it, every example valid."""
    old, new, previous_review = redraft_fixture()
    remapped = remap_review_for_redraft(previous_review, previous_composition=old, composition=new)
    assert remapped is not None
    state = redraft_state(new)
    requests = []
    for notes in ([], NOTES):
        full = build_report_review_input(state.model_copy(update={"reader_notes": notes}))
        scoped = build_scoped_report_review_input(
            state.model_copy(update={"reader_notes": notes}), previous_review=remapped
        )
        assert scoped is not None
        requests.append((review_messages(full)[1].content, scoped_review_messages(scoped)[1].content))
    (plain_full, plain_scoped), (noted_full, noted_scoped) = requests

    for plain, noted, schema in (
        (plain_full, noted_full, ReportReviewNotesDraft),
        (plain_scoped, noted_scoped, ScopedReportReviewNotesDraft),
    ):
        envelope = request_envelope(noted)
        assert envelope.count("# Reply format") == 1 and envelope.count("JSON object") == 1
        assert envelope.rstrip().splitlines()[-1] == STRUCTURED_REQUEST_END

        def static(body: str) -> list[str]:
            sections = [line for line in request_envelope(body).splitlines() if line.startswith("# ")]
            return sections[: sections.index("# Reply format")]

        assert static(noted) == static(plain)
        start = envelope.index("# Reply format")
        reply_format = envelope[start : envelope.index("\n# ", start)]
        for field in schema.model_fields:
            assert f'"{field}"' in reply_format, field
        examples = labelled_examples(noted)
        assert len(examples) == 1
        schema.model_validate(json.loads(examples[0][1]))
```

Append to `tests/test_graph/test_reader_notes_replay.py`:

```python


@pytest.mark.asyncio
async def test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does(tmp_path: Path) -> None:
    """AC16 / D10: the notes block reaches the planner, every research turn, the
    source evaluator, the writer and the review — and never an evidence-verifier
    request, whichever agent sends it."""
    with guarded():
        status, sequence, state = await replay_packets(tmp_path, EXTRA_PASS_CASE, board=noted_board(EMPHASIS, ANGLE))

    assert status == "completed"
    consumers = {
        "planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft",
        "researcher:react", "researcher:SubTopicFindingsDraft",
        "source_evaluator:SourceScoresDraft",
        "report_writer:SectionDraft", "report_writer:BottomLineDraft",
        "report_reviewer:ReportReviewNotesDraft",
    }
    keys = {key for key, _ in sequence}
    assert consumers <= keys
    assert "report_reviewer:ReportReviewDraft" not in keys
    for key in consumers:
        assert all(EMPHASIS_TEXT in text for text in packets_for(sequence, key)), key
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: more weight on lithium-ion safety standards (emphasis)" in review
    assert "- n2: how battery cells are recycled (new_angle)" in review
    checks = [text for key, text in sequence if key.split(":")[1] in {"ContextCheckDraft", "StatementCheckDraft"}]
    assert checks and {key for key, _ in sequence if key.startswith("evidence_verifier:")}
    for text in checks:
        assert "lithium-ion safety standards" not in text and "battery cells are recycled" not in text
        assert "Reader notes" not in text and "reader added these notes" not in text
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [
        ("n1", "honoured"), ("n2", "honoured"),
    ]
    assert [(note.note_id, note.reviewed) for note in state.reader_notes] == [("n1", True), ("n2", True)]
```

- [ ] **Step 2: Run the tests and see them fail**

The two files run separately, so the review file's import error cannot hide the AC16 test.

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_review.py -q 2>&1 | tail -3
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_reader_notes_replay.py -q 2>&1 | tail -2
```

Expected: first `ERROR tests/test_agents/test_reader_notes_review.py` and `1 error in …`: the file cannot import `NoteDispositionDraft` from `deep_research.agents.report_reviewer`. Then `FAILED tests/test_graph/test_reader_notes_replay.py::test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does` and `1 failed, 4 passed`: the review still asks for `ReportReviewDraft`, so no `report_reviewer:ReportReviewNotesDraft` request is sent.

- [ ] **Step 3: Judge each note in the review, and answer it in replay**

**`src/deep_research/agents/report_reviewer.py`**: 24 edits, in file order.

`src/deep_research/agents/report_reviewer.py` — replace

```python
)
from deep_research.agents.report import (
```

with

```python
)
from deep_research.agents.reader_notes import REVIEW_NOTES, render_reader_notes
from deep_research.agents.report import (
```

`src/deep_research/agents/report_reviewer.py` (anchor as written by Task 3) — replace

```python
    FactRow,
    Finding,
    GapKind,
    GapSeverity,
    ReaderNoteKind,
    ReportPoint,
    ReportSection,
    ReportStatement,
```

with

```python
    FactRow,
    Finding,
    GapKind,
    GapSeverity,
    NoteDisposition,
    ReaderNoteKind,
    ReportPoint,
    ReportSection,
    ReportStatement,
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        'F02\'s figure, a material defect."}',
    ),
)
```

with

```python
        'F02\'s figure, a material defect."}',
    ),
)


# The example a review of a packet with reader notes shows (live-briefs spec
# §4.6): the one above, plus the verdict on one note.
_REVIEW_NOTES_REPLY_EXAMPLES = (
    (
        "Example input: statements S001 and S002; S001 restates the actual that "
        "F01 reports, and S002 calls the actual that F02 reports a forecast; "
        "reader note n1, which the report follows.",
        '{"dimensions":{"completeness":0.7,"prioritization":0.8,'
        '"evidence_quality":0.5,"attribution":0.6,"uncertainty":0.7,'
        '"readability":0.9,"actionability":0.7},'
        '"statement_dispositions":[{"statement_id":"S001","disposition":"supported",'
        '"problem":""},{"statement_id":"S002","disposition":"unsupported",'
        '"problem":"F02 reports an actual; the sentence calls it a forecast."}],'
        '"defects":[{"kind":"contradiction","severity":"major",'
        '"statement_ids":["S002"],"target_ids":[],'
        '"problem":"S002 presents the actual F02 reports as a forecast."}],'
        '"rationale":"S001 is supported by F01; S002 misstates the kind of '
        'F02\'s figure, a material defect.",'
        '"note_dispositions":[{"note_id":"n1","status":"honoured"}]}',
    ),
)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python

class ReportReviewDraft(ContractModel):
```

with

```python

class NoteDispositionDraft(ContractModel):
    """One provider-reported verdict on one reader note (live-briefs spec §4.6).

    ``status`` is a plain string for the reason ``ReviewDefectDraft.kind`` is:
    one invented verdict drops that one entry (``_note_dispositions``) rather
    than refusing the whole review.
    """

    note_id: str = Field(min_length=1)
    status: str = Field(min_length=1)


class ReportReviewDraft(ContractModel):
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
```

with

```python
    defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ReportReviewNotesDraft(ReportReviewDraft):
    """A whole-report review of a packet that carries reader notes (live-briefs spec §4.6).

    The same reply plus one verdict per note. Its own schema, so a review
    without notes asks for exactly the reply, and shows exactly the examples,
    it always did.
    """

    note_dispositions: list[NoteDispositionDraft] = Field(default_factory=list)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python

def _deterministic_block(packet: ReportReviewInput) -> str:
```

with

```python

def _reader_notes_block(packet: ReportReviewInput) -> list[str]:
    """The ``# Reader notes`` section, with each note's id, or nothing at all.

    live-briefs spec §4.6: only a packet that carries notes gains the section
    (and the request to judge them), so a review without notes is byte-for-byte
    the request it was.
    """
    if not packet.reader_notes:
        return []
    return [
        "# Reader notes\n"
        + render_reader_notes(
            packet.reader_notes, instruction=REVIEW_NOTES, with_ids=True
        )
    ]


def _deterministic_block(packet: ReportReviewInput) -> str:
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        + _render_dimension_guidance(),
        "# Reply format\n" + render_structured_reply_format(_REVIEW_REPLY_EXAMPLES),
    ]
```

with

```python
        + _render_dimension_guidance(),
        "# Reply format\n"
        + render_structured_reply_format(
            _REVIEW_NOTES_REPLY_EXAMPLES if packet.reader_notes else _REVIEW_REPLY_EXAMPLES
        ),
    ]
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            "deterministic checks below are the whole of what is being judged."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        (
            "# Reader content — the complete candidate\n"
```

with

```python
            "deterministic checks below are the whole of what is being judged."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        *_reader_notes_block(packet),
        (
            "# Reader content — the complete candidate\n"
```

`src/deep_research/agents/report_reviewer.py` — replace

```python

def _derived_defects(
```

with

```python

_NOTE_DISPOSITION_STATUSES = frozenset(
    {"honoured", "ignored_with_evidence", "no_evidence"}
)


def _note_dispositions(
    drafts: Sequence[NoteDispositionDraft],
    *,
    packet: ReportReviewInput,
) -> list[NoteDisposition]:
    """One verdict per reader note the packet carried, in the reply's order.

    An id the packet did not carry, a status outside the vocabulary, or a
    second verdict on the same note is dropped: a note is never a reason to
    refuse the review of the report itself (live-briefs spec §4.6).
    """
    known = {note.note_id for note in packet.reader_notes}
    verdicts: dict[str, NoteDisposition] = {}
    for draft in drafts:
        note_id = draft.note_id.strip()
        status = draft.status.strip()
        if note_id in known and note_id not in verdicts and status in _NOTE_DISPOSITION_STATUSES:
            verdicts[note_id] = NoteDisposition(note_id=note_id, status=status)  # type: ignore[arg-type]
    return list(verdicts.values())


def _derived_defects(
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    status: str,
) -> ReportReview:
```

with

```python
    status: str,
    note_dispositions: Sequence[NoteDisposition] = (),
) -> ReportReview:
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        rationale=rationale,
    )
```

with

```python
        rationale=rationale,
        note_dispositions=list(note_dispositions),
    )
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        )

    try:
        reply = await reviewer._request(  # noqa: SLF001
            review_messages(packet), ReportReviewDraft
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, ReportReviewDraft), status="incomplete"
        )
    except ProviderError as error:
```

with

```python
        )

    schema = ReportReviewNotesDraft if packet.reader_notes else ReportReviewDraft
    try:
        reply = await reviewer._request(  # noqa: SLF001
            review_messages(packet), schema
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, schema), status="incomplete"
        )
    except ProviderError as error:
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
        )
    except ValidationError as error:
        # The last boundary: assembling the record is the one remaining step
```

with

```python
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
            note_dispositions=_note_dispositions(
                getattr(reply, "note_dispositions", ()), packet=packet
            ),
        )
    except ValidationError as error:
        # The last boundary: assembling the record is the one remaining step
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    )
    fingerprint: str = ""
```

with

```python
    )
    carried_note_dispositions: list[NoteDisposition] = Field(default_factory=list)
    """The previous review's verdicts on the reader's notes (live-briefs spec
    §4.6), kept for every note the re-review's reply does not judge again."""
    fingerprint: str = ""
```

`src/deep_research/agents/report_reviewer.py` (anchor as written by Task 3) — replace

```python
    the material a full review reads, even over an identical report. The
    base packet's reader notes are left out while empty, so a scoped packet
    without notes keeps the fingerprint it had before notes existed (spec §4.6)."""
    exclude: dict[str, object] = {"fingerprint": True}
    if not scoped.base.reader_notes:
```

with

```python
    the material a full review reads, even over an identical report. The
    reader-note fields are left out while empty, so a scoped packet without
    notes keeps the fingerprint it had before notes existed (spec §4.6)."""
    exclude: dict[str, object] = {"fingerprint": True}
    if not scoped.carried_note_dispositions:
        exclude["carried_note_dispositions"] = True
    if not scoped.base.reader_notes:
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            for statement_id in unchanged_ordered
        },
    )
```

with

```python
            for statement_id in unchanged_ordered
        },
        carried_note_dispositions=[
            entry
            for entry in previous_review.note_dispositions
            if entry.note_id in {note.note_id for note in base.reader_notes}
        ],
    )
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        '"rationale":"The redraft closed review-01; nothing else changed."}',
    ),
)
```

with

```python
        '"rationale":"The redraft closed review-01; nothing else changed."}',
    ),
)
# The scoped example a packet with reader notes shows (live-briefs spec §4.6).
_SCOPED_REVIEW_NOTES_REPLY_EXAMPLES = (
    (
        "Example input: changed statement S004; unchanged statement S001; "
        "one previous defect review-01 against S001, since resolved because "
        "S004 now supplies the qualifier S001 was missing; reader note n1, "
        "which the report follows.",
        '{"dimensions":{"completeness":0.85,"prioritization":0.8,'
        '"evidence_quality":0.8,"attribution":0.8,"uncertainty":0.8,'
        '"readability":0.85,"actionability":0.8},'
        '"statement_dispositions":[{"statement_id":"S004","disposition":"supported",'
        '"problem":""}],'
        '"previous_defect_resolutions":[{"defect_id":"review-01","resolved":true,'
        '"note":"S004 now supplies the missing qualifier."}],'
        '"new_defects":[],'
        '"rationale":"The redraft closed review-01; nothing else changed.",'
        '"note_dispositions":[{"note_id":"n1","status":"honoured"}]}',
    ),
)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
        "# Reply format\n"
        + render_structured_reply_format(_SCOPED_REVIEW_REPLY_EXAMPLES),
    ]
```

with

```python
        "# Reply format\n"
        + render_structured_reply_format(
            _SCOPED_REVIEW_NOTES_REPLY_EXAMPLES
            if packet.reader_notes
            else _SCOPED_REVIEW_REPLY_EXAMPLES
        ),
    ]
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            "and findings following them are shown for context."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        (
            "# Reader content — the complete candidate\n"
```

with

```python
            "and findings following them are shown for context."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        *_reader_notes_block(packet),
        (
            "# Reader content — the complete candidate\n"
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
    new_defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
```

with

```python
    new_defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ScopedReportReviewNotesDraft(ScopedReportReviewDraft):
    """A scoped re-review of a packet that carries reader notes (live-briefs spec §4.6):
    the same reply plus one verdict per note, for the reason ``ReportReviewNotesDraft`` exists."""

    note_dispositions: list[NoteDispositionDraft] = Field(default_factory=list)
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            status="incomplete",
        )
    try:
        reply = await reviewer._request(  # noqa: SLF001
            scoped_review_messages(scoped), ScopedReportReviewDraft
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, ScopedReportReviewDraft), status="incomplete"
        )
```

with

```python
            status="incomplete",
        )
    schema = (
        ScopedReportReviewNotesDraft if packet.reader_notes else ScopedReportReviewDraft
    )
    try:
        reply = await reviewer._request(  # noqa: SLF001
            scoped_review_messages(scoped), schema
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, schema), status="incomplete"
        )
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
                    if dispositions[statement_id] == UNREVIEWED_STATEMENT_DISPOSITION
                ],
            )
        )
        dimensions = None
    rationale = " ".join(part for part in rationale_parts if part).strip()
    try:
        return _merge_review(
            packet,
            dimension_scores=dimensions,
            dispositions=dispositions,
            defects=[*all_defects, *derived],
```

with

```python
                    if dispositions[statement_id] == UNREVIEWED_STATEMENT_DISPOSITION
                ],
            )
        )
        dimensions = None
    rationale = " ".join(part for part in rationale_parts if part).strip()
    judged = {
        entry.note_id: entry
        for entry in _note_dispositions(
            getattr(reply, "note_dispositions", ()), packet=packet
        )
    }
    note_dispositions = [
        judged.pop(entry.note_id, entry) for entry in scoped.carried_note_dispositions
    ] + list(judged.values())
    try:
        return _merge_review(
            packet,
            dimension_scores=dimensions,
            dispositions=dispositions,
            defects=[*all_defects, *derived],
```

`src/deep_research/agents/report_reviewer.py` — replace

```python
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
        )
    except ValidationError as error:
        return _failed_review(
```

with

```python
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
            note_dispositions=note_dispositions,
        )
    except ValidationError as error:
        return _failed_review(
```

`src/deep_research/agents/report_reviewer.py` (anchor as written by Task 3) — replace

```python
    "ReportReviewInput",
    "ReportReviewer",
    "ReviewDefectDraft",
    "ReviewDeterministic",
    "ReviewDimensionScores",
    "ReviewFindingView",
    "ReviewNoteView",
    "ReviewStatementView",
    "ScopedReportReviewDraft",
    "ScopedReportReviewInput",
    "StatementDispositionDraft",
```

with

```python
    "ReportReviewInput",
    "ReportReviewNotesDraft",
    "ReportReviewer",
    "ReviewDefectDraft",
    "ReviewDeterministic",
    "ReviewDimensionScores",
    "NoteDispositionDraft",
    "ReviewFindingView",
    "ReviewNoteView",
    "ReviewStatementView",
    "ScopedReportReviewDraft",
    "ScopedReportReviewInput",
    "ScopedReportReviewNotesDraft",
    "StatementDispositionDraft",
```

**`src/deep_research/e2e_evaluation/replay.py`**: 3 edits, in file order.

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
from deep_research.agents.report_reviewer import (
    PreviousDefectResolutionDraft,
    ReportReviewDraft,
    ReviewDefectDraft,
    ReviewDimensionScores,
    ScopedReportReviewDraft,
    StatementDispositionDraft,
```

with

```python
from deep_research.agents.report_reviewer import (
    NoteDispositionDraft,
    PreviousDefectResolutionDraft,
    ReportReviewDraft,
    ReportReviewNotesDraft,
    ReviewDefectDraft,
    ReviewDimensionScores,
    ScopedReportReviewDraft,
    ScopedReportReviewNotesDraft,
    StatementDispositionDraft,
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python

    def disposition_for(self, statement_id: str) -> str:
```

with

```python

    def _reply_ReportReviewNotesDraft(self, text: str) -> ReportReviewNotesDraft:
        """The whole-report reply above, plus a verdict for every reader note listed."""
        return ReportReviewNotesDraft(
            **self._reply_ReportReviewDraft(text).model_dump(),
            note_dispositions=self.note_dispositions_for(text),
        )

    def note_dispositions_for(self, text: str) -> list[NoteDispositionDraft]:
        """``honoured`` for every reader note the review packet lists (live-briefs spec §4.6).

        Read from the request's own ``# Reader notes`` section, so a packet
        without notes gets no entry and every existing case replies exactly
        as it did: a scripted reviewer has no way to judge a note against a
        report, and the one honest default is that the report followed it.
        """
        block = self._material_block(text, "Reader notes")
        return [
            NoteDispositionDraft(note_id=note_id, status="honoured")
            for note_id in re.findall(r"(?m)^- (n\d+): ", block)
        ]

    def disposition_for(self, statement_id: str) -> str:
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
            rationale="The redraft closed every previous defect; nothing else changed.",
        )
```

with

```python
            rationale="The redraft closed every previous defect; nothing else changed.",
        )

    def _reply_ScopedReportReviewNotesDraft(
        self, text: str
    ) -> ScopedReportReviewNotesDraft:
        """The scoped reply above, plus a verdict for every reader note listed."""
        return ScopedReportReviewNotesDraft(
            **self._reply_ScopedReportReviewDraft(text).model_dump(),
            note_dispositions=self.note_dispositions_for(text),
        )
```

**`src/deep_research/agents/__init__.py`**: 2 edits, in file order.

`src/deep_research/agents/__init__.py` (anchor as written by Task 3) — replace

```python
    ReviewDefectDraft,
    ReviewNoteView,
)
```

with

```python
    ReviewDefectDraft,
    NoteDispositionDraft,
    ReportReviewNotesDraft,
    ReviewNoteView,
    ScopedReportReviewNotesDraft,
)
```

`src/deep_research/agents/__init__.py` (anchor as written by Task 3) — replace

```python
    "ReviewDefectDraft",
    "ReviewNoteView",
    "MAX_OPTION_ROWS",
```

with

```python
    "ReviewDefectDraft",
    "NoteDispositionDraft",
    "ReportReviewNotesDraft",
    "ReviewNoteView",
    "ScopedReportReviewNotesDraft",
    "MAX_OPTION_ROWS",
```

- [ ] **Step 4: Run the tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_agents/test_reader_notes_review.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_report_reviewer.py tests/test_agents/test_tool_free_prompts.py tests/test_imports.py -q 2>&1 | tail -1
```

Expected: `222 passed`. `test_tool_free_prompts.py` passes unchanged: the review without notes asks the schema, and shows the example, it always did.

- [ ] **Step 5: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4881 passed, 6 skipped, 12 deselected` (+7). No pin moves in this task: `report_reviewer.py` and `e2e_evaluation/replay.py` are not part of any pinned fingerprint.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/report_reviewer.py src/deep_research/e2e_evaluation/replay.py src/deep_research/agents/__init__.py tests/test_agents/test_reader_notes_review.py tests/test_graph/test_reader_notes_replay.py
git commit -m "feat(review): a verdict on each reader note, asked only when there are notes; replay honours them"
```

---

### Task 7: The note interpreter and the API's note shapes (spec §4.6 "Interpretation", "Events", "Status response")

**Files:**
- Create: `src/deep_research/api/notes.py`, `tests/test_api/test_notes.py`
- Modify: `src/deep_research/api/models.py:19` (imports), `:128` (the note and check shapes, after `ClarificationAnswersRequest`), `:227` (`ResearchSessionResponse`'s four fields)

**Interfaces:**
- Consumes: Task 2's `ReaderNote`, `NoteBoard`, `ReceivedNote`; Phase 2's `clarity_llm_config` (`api/clarify.py`).
- Produces:
  - `deep_research.api.notes`: `NoteInterpretationDraft` (what the provider is asked for), `NoteInterpretation` (a valid reading), `validated_interpretation(draft, *, earlier) -> NoteInterpretation | None`, `fallback_interpretation(text)`, `note_messages(text, question, earlier)`, `live_note_interpreter(text, question, earlier, settings, *, completer=None)` (raises on a provider failure or an invalid reading), `scripted_note_interpreter(...)` (replay), `NoteInterpreter` (the callable type), `reader_note(received, reading)`, `note_received_event(received)`, `note_interpreted_event(note, *, fallback)`, `note_outcome(note_id, state)`, `note_records(board, state)`, `NoteOutcome`.
  - `deep_research.api.models`: `NoteRequest` (text collapsed to one line, 1–500), `NoteAcceptedResponse(note_id, status="received")`, `ReaderNoteResponse(note_id, text, restatement, outcome)`, `ClarificationQuestionResponse`, `ClarificationAnswerResponse`, `ClarificationRecordResponse`; `ResearchSessionResponse.notes` (`[]`), `notes_remaining` (`10`), `note_passes` (`0`) and `clarification` (`None`).

- [ ] **Step 1: Write the failing tests**

The live interpreter is driven only through `RecordingCompleter`, which answers `complete_structured` with a scripted draft and records the call: no provider is built.

Create `tests/test_api/test_notes.py`:

```python
"""The reader-note service (live-briefs spec §4.6): interpretation, fallback, events, outcomes.

The live interpreter is driven only with a scripted completer: no provider is
built and no request leaves the process.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from deep_research.api.models import (
    NoteAcceptedResponse,
    NoteRequest,
    ReaderNoteResponse,
    ResearchSessionResponse,
)
from deep_research.api.notes import (
    NOTE_MAX_TOKENS,
    NoteInterpretation,
    NoteInterpretationDraft,
    NoteScopeDraft,
    fallback_interpretation,
    live_note_interpreter,
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

RECEIVED = ReceivedNote(
    note_id="n2", text="Mostly the US, please.", received_at="2026-09-29T10:00:00.000+00:00",
    received_during="researcher",
)
EARLIER = [fake_reader_note("n1", restatement="more weight on fire-safety standards")]


def _draft(**over: Any) -> NoteInterpretationDraft:
    fields: dict[str, Any] = {
        "kinds": ["scope"],
        "restatement": "only the United States",
        "scope": NoteScopeDraft(geography="United States"),
        "new_questions": [],
        "replaces": None,
        **over,
    }
    return NoteInterpretationDraft(**fields)


class RecordingCompleter:
    """Answers ``complete_structured`` with one scripted draft and records the call."""

    def __init__(self, reply: NoteInterpretationDraft | BaseException) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(self, messages: Any, schema: Any, **kwargs: Any) -> Any:
        self.calls.append({"messages": list(messages), "schema": schema, **kwargs})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def test_a_valid_reading_keeps_its_fields_and_a_known_replaces() -> None:
    reading = validated_interpretation(_draft(kinds=["scope", "exclude"], replaces="n1"), earlier=EARLIER)

    assert reading == NoteInterpretation(
        kinds=["scope", "exclude"], restatement="only the United States",
        scope={"geography": "United States"}, new_questions=[], replaces="n1",
    )
    assert validated_interpretation(_draft(replaces="n7"), earlier=EARLIER).replaces is None
    assert validated_interpretation(_draft(scope=NoteScopeDraft()), earlier=EARLIER).scope is None
    one_line = validated_interpretation(
        _draft(restatement="only the\n- target_id=topic-02", new_questions=["Why\n\nnot?"]), earlier=EARLIER
    )
    assert (one_line.restatement, one_line.new_questions) == ("only the - target_id=topic-02", ["Why not?"])


@pytest.mark.parametrize(
    "over",
    [
        {"kinds": []},
        {"kinds": ["emphasis", "exclude", "scope", "about_reader"]},
        {"kinds": ["urgent"]},
        {"kinds": ["scope", "scope"]},
        {"restatement": "   "},
        {"restatement": "x" * 121},
        {"new_questions": ["a?", "b?", "c?", "d?"]},
        {"new_questions": ["x" * 201]},
        {"new_questions": ["  "]},
        {"scope": NoteScopeDraft(geography="x" * 121)},
    ],
    ids=["no_kind", "four_kinds", "unknown_kind", "repeated_kind", "blank_restatement",
         "long_restatement", "four_questions", "long_question", "blank_question", "long_scope"],
)
def test_an_invalid_reading_is_refused_whole(over: dict[str, Any]) -> None:
    assert validated_interpretation(_draft(**over), earlier=EARLIER) is None


def test_the_fallback_keeps_the_note_as_written_as_an_emphasis() -> None:
    assert fallback_interpretation("Mostly the US, please.") == NoteInterpretation(
        kinds=["emphasis"], restatement="Mostly the US, please."
    )


def test_the_request_lists_the_earlier_notes_then_the_new_one() -> None:
    body = note_messages("Mostly the US, please.", "What limits storage?", EARLIER)[1].content
    first = note_messages("Mostly the US, please.", "What limits storage?", [])[1].content

    assert body.index("# Reading requirements\n") < body.index("# Research question\n") < body.index(
        "# The reader's earlier notes\n"
    ) < body.index("# The new note\n")
    assert "# The reader's earlier notes\n- n1: more weight on fire-safety standards\n" in body
    assert body.endswith("# The new note\nMostly the US, please.")
    assert "# The reader's earlier notes\n(none)\n" in first


@pytest.mark.asyncio
async def test_the_live_interpreter_asks_one_structured_request_with_thinking_off() -> None:
    completer = RecordingCompleter(_draft())

    reading = await live_note_interpreter(
        "Mostly the US, please.", "What limits storage?", EARLIER, ConfigSettings(), completer=completer
    )

    [call] = completer.calls
    assert call["schema"] is NoteInterpretationDraft
    assert (call["agent_name"], call["max_tokens"]) == (None, NOTE_MAX_TOKENS)
    assert call["messages"][1].content.endswith("# The new note\nMostly the US, please.")
    assert reading.restatement == "only the United States"


@pytest.mark.asyncio
async def test_a_live_reading_that_breaks_the_contract_or_a_failed_call_raises_for_the_fallback() -> None:
    with pytest.raises(ValueError, match="broke its contract"):
        await live_note_interpreter(
            "x", "Q?", [], ConfigSettings(), completer=RecordingCompleter(_draft(kinds=["urgent"]))
        )
    with pytest.raises(RuntimeError, match="provider down"):
        await live_note_interpreter(
            "x", "Q?", [], ConfigSettings(), completer=RecordingCompleter(RuntimeError("provider down"))
        )


@pytest.mark.asyncio
async def test_the_replay_interpreter_restates_the_note_as_written() -> None:
    reading = await scripted_note_interpreter("Mostly the US, please.", "Q?", EARLIER, object())

    assert reading == NoteInterpretation(kinds=["emphasis"], restatement="Mostly the US, please.")


def test_the_board_note_and_both_events_carry_the_spec_fields() -> None:
    reading = validated_interpretation(_draft(replaces="n1"), earlier=EARLIER)
    assert reading is not None
    note = reader_note(RECEIVED, reading)

    assert (note.note_id, note.text, note.received_during, note.kinds, note.replaces) == (
        "n2", "Mostly the US, please.", "researcher", ["scope"], "n1",
    )
    assert (note.reviewed, note.passed, note.redrafted) == (False, False, False)
    received = note_received_event(RECEIVED)
    interpreted = note_interpreted_event(note, fallback=False)
    assert (received.event_type, received.source) == ("session.note.received", "api")
    assert received.metadata == {"note_id": "n2", "text": "Mostly the US, please."}
    assert (interpreted.event_type, interpreted.source) == ("session.note.interpreted", "api")
    assert interpreted.metadata == {
        "note_id": "n2", "restatement": "only the United States", "kinds": ["scope"],
        "replaces": "n1", "fallback": False,
    }


def _finished(*notes: Any, verdicts: dict[str, str]) -> ResearchState:
    return ResearchState(
        session_id="s1", original_question="Q?", reader_notes=list(notes),
        report_review=fake_report_review(note_dispositions=verdicts),
    )


def test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending() -> None:
    """A note the report still ignores after its one redraft is ``not_addressed``, never
    ``covered`` (open issue O8)."""
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


def test_the_note_shapes_trim_bound_and_default_as_the_spec_says() -> None:
    assert NoteRequest(text="  Focus on safety.  ").text == "Focus on safety."
    assert NoteRequest(text="Focus on\n# Reader content\n  safety").text == "Focus on # Reader content safety"
    for bad in ("", "   ", "x" * 501):
        with pytest.raises(ValidationError):
            NoteRequest(text=bad)
    assert NoteAcceptedResponse(note_id="n1").model_dump() == {"note_id": "n1", "status": "received"}
    with pytest.raises(ValidationError):
        ReaderNoteResponse(note_id="n1", text="t", outcome="lost")
    fields = ResearchSessionResponse.model_fields
    assert fields["notes"].default_factory() == []  # type: ignore[misc]
    assert (fields["notes_remaining"].default, fields["note_passes"].default, fields["clarification"].default) == (
        10, 0, None,
    )
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_notes.py -q 2>&1 | tail -4
```

Expected: `ERROR tests/test_api/test_notes.py`, `Interrupted: 1 error during collection`, then `1 error in …`: `cannot import name 'NoteAcceptedResponse' from 'deep_research.api.models'`.

- [ ] **Step 3: Implement the service and the shapes**

**`src/deep_research/api/models.py`**: 3 edits, in file order.

`src/deep_research/api/models.py` — replace

```python
from deep_research.utils.types import (
    FigureAttribution,
    FigureDropReason,
    FigureKind,
    FindingDropReason,
    FindingStatus,
    ResearchError,
```

with

```python
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    ClarityDimension,
    FigureAttribution,
    FigureDropReason,
    FigureKind,
    FindingDropReason,
    FindingStatus,
    ReaderAnswerSource,
    ResearchError,
```

`src/deep_research/api/models.py` — replace

```python

class CoverageProgressResponse(ApiModel):
```

with

```python

class NoteRequest(ApiModel):
    """``POST /research/{id}/notes``: one reader note, 1-500 characters after trim (spec §4.6).

    Collapsed to one single-spaced line first, as an answer's text is: a note
    kept as written becomes its own restatement, and a restatement is one line
    of an agent's request, so reader text can never start a line of its own.
    """

    text: str = Field(min_length=1, max_length=500)

    @field_validator("text", mode="before")
    @classmethod
    def one_line(cls, value: object) -> object:
        return collapse_whitespace(value) if isinstance(value, str) else value


class NoteAcceptedResponse(ApiModel):
    """The ``202`` a note earns: its id, and that it was received (not yet read)."""

    note_id: str = Field(min_length=1)
    status: Literal["received"] = "received"


class ReaderNoteResponse(ApiModel):
    """One accepted note in the session response (live-briefs spec §4.6).

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


class ClarificationQuestionResponse(ApiModel):
    """One question the one-time check asked (live-briefs spec §4.4)."""

    id: str = Field(min_length=1)
    dimension: ClarityDimension
    text: str = Field(min_length=1)
    short: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    best_guess: str = Field(min_length=1)


class ClarificationAnswerResponse(ApiModel):
    """The value one question's answer resolved to, and where it came from."""

    question_id: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source: ReaderAnswerSource


class ClarificationRecordResponse(ApiModel):
    """The one-time check a session asked: its questions, then the answers the run started with."""

    questions: list[ClarificationQuestionResponse] = Field(default_factory=list)
    answers: list[ClarificationAnswerResponse] = Field(default_factory=list)


class CoverageProgressResponse(ApiModel):
```

`src/deep_research/api/models.py` — replace

```python
    """The distinct counts, or ``None`` without a composition to count."""
```

with

```python
    """The distinct counts, or ``None`` without a composition to count."""

    notes: list[ReaderNoteResponse] = Field(default_factory=list)
    """Every note the reader added, in the order it was received (spec §4.6)."""

    notes_remaining: int = Field(default=MAX_NOTES_PER_RUN, ge=0, le=MAX_NOTES_PER_RUN)
    """How many more notes this session accepts (D11a): ten less the accepted ones."""

    note_passes: int = Field(default=0, ge=0)
    """The targeted research passes the reader's notes bought (D11)."""

    clarification: ClarificationRecordResponse | None = None
    """The one-time check the session asked, or ``None`` when it asked nothing."""
```

Create `src/deep_research/api/notes.py`:

```python
"""Reader notes at the API (live-briefs spec §4.6, D8-D11a): the interpreter,
its fallback, the two session events, and each note's outcome.

A reader may add up to ten notes while a run is going. The notes route accepts
one (``session.note.received``), and a ``NoteInterpreter`` reads it: live mode
asks the configured provider — the same path as the one-time check, thinking
disabled, structured output — and replay mode restates the note as written.
The interpreted note joins the run's board (``runtime/notes.py``), where every
step but verification reads it (D10), and ``session.note.interpreted`` carries
the run's reading back to the page, which acknowledges it (D9).

An interpretation never blocks a note. A call that fails, runs out of time or
answers with something invalid keeps the note as written, as an emphasis, and
its acknowledgement says so (``fallback``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from deep_research.api.clarify import clarity_llm_config
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ChatMessage, build_chat_provider
from deep_research.runtime.notes import NoteBoard, ReceivedNote
from deep_research.utils.config import ConfigSettings
from deep_research.utils.text import collapse_whitespace
from deep_research.utils.types import (
    ReaderNote,
    ReaderNoteKind,
    ReaderNoteScope,
    ResearchEvent,
    ResearchState,
    active_reader_notes,
)

NOTE_MAX_TOKENS = 2048
"""The interpretation's output cap: one short structured reading, thinking off."""
NOTE_TRACE_SESSION = "note-interpreter"
MAX_RESTATEMENT_CHARS = 120
MAX_NEW_QUESTIONS = 3
MAX_NEW_QUESTION_CHARS = 200
NoteOutcome: TypeAlias = Literal[
    "covered", "not_found", "not_addressed", "pending", "replaced"
]


class NoteScopeDraft(BaseModel):
    """The scope a note sets, as the provider proposes it."""

    model_config = ConfigDict(extra="forbid")

    geography: str | None = None
    period: str | None = None


class NoteInterpretationDraft(BaseModel):
    """What the provider is asked for; validated by ``validated_interpretation``.

    ``kinds`` holds plain strings so one invented kind is caught here and the
    note falls back, rather than the schema refusing the reply.
    """

    model_config = ConfigDict(extra="forbid")

    kinds: list[str] = Field(default_factory=list)
    restatement: str = ""
    scope: NoteScopeDraft | None = None
    new_questions: list[str] = Field(default_factory=list)
    replaces: str | None = None


class NoteInterpretation(BaseModel):
    """One note's reading (live-briefs spec §4.6): what kind of note, in plain words."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    scope: ReaderNoteScope | None = None
    new_questions: list[str] = Field(default_factory=list, max_length=MAX_NEW_QUESTIONS)
    replaces: str | None = None

    @model_validator(mode="after")
    def kinds_are_distinct_and_questions_short(self) -> NoteInterpretation:
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds repeat")
        if any(
            not question.strip() or len(question) > MAX_NEW_QUESTION_CHARS
            for question in self.new_questions
        ):
            raise ValueError("each new question is 1-200 characters")
        return self


NoteInterpreter: TypeAlias = Callable[
    [str, str, Sequence[ReaderNote], Any], Awaitable[NoteInterpretation]
]
"""``(note text, research question, the run's earlier interpreted notes, settings)``."""


def fallback_interpretation(text: str) -> NoteInterpretation:
    """The note as written, as an emphasis (spec §4.6: the interpreter failed)."""
    return NoteInterpretation(kinds=["emphasis"], restatement=text)


def validated_interpretation(
    draft: NoteInterpretationDraft,
    *,
    earlier: Sequence[ReaderNote],
) -> NoteInterpretation | None:
    """The draft as a reading of the note, or ``None`` when any field is invalid.

    Every text field is first collapsed to one single-spaced line, because each
    is printed as one line of an agent's request. Invalid is then: no kind, more
    than three, one outside the vocabulary or repeated; an empty restatement or
    one over 120 characters; more than three new questions, or one empty or
    over 200 characters; a scope field over 120 characters. A ``replaces`` that
    names no earlier note of this run is dropped rather than failing the
    reading: the note itself stands.
    """
    earlier_ids = {note.note_id for note in earlier}
    restatement = collapse_whitespace(draft.restatement)
    if not restatement or len(restatement) > MAX_RESTATEMENT_CHARS:
        return None
    scope = draft.scope
    geography = collapse_whitespace(scope.geography or "") if scope is not None else ""
    period = collapse_whitespace(scope.period or "") if scope is not None else ""
    try:
        reading = NoteInterpretation(
            kinds=[kind.strip() for kind in draft.kinds],  # type: ignore[misc]
            restatement=restatement,
            scope=(
                ReaderNoteScope(geography=geography or None, period=period or None)
                if geography or period
                else None
            ),
            new_questions=[collapse_whitespace(question) for question in draft.new_questions],
            replaces=(
                draft.replaces.strip()
                if draft.replaces and draft.replaces.strip() in earlier_ids
                else None
            ),
        )
    except ValidationError:
        return None
    return reading


NOTE_SYSTEM_PROMPT = (
    "You read one note a reader added to a research run that is already going. "
    "You have no tools and need none: the question, the note and the reader's "
    "earlier notes are printed in the request."
)

NOTE_INSTRUCTION = (
    "Say what the note asks the run to do.\n"
    "- kinds: one to three of emphasis (give a subject more weight), exclude "
    "(leave a subject out), scope (narrow to a place or a period), new_angle "
    "(research something the plan does not cover) and about_reader (who the "
    "report is for, or at what level to write).\n"
    "- restatement: what the note asks, in plain words, at most 120 "
    "characters, starting in lower case and never quoting the note back, for "
    "example \"more weight on fire-safety standards\".\n"
    "- scope: the geography or period a scope note sets, or null.\n"
    "- new_questions: for a new_angle note, or a scope that widens the "
    "question, up to 3 research questions it raises; otherwise an empty list.\n"
    "- replaces: the id of the one earlier note this note contradicts, or "
    "null."
)


def note_messages(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
) -> list[ChatMessage]:
    """The one tool-free request a live interpretation sends."""
    listed = "\n".join(
        f"- {note.note_id}: {note.restatement}" for note in active_reader_notes(earlier)
    ) or "(none)"
    return [
        ChatMessage(role="developer", content=NOTE_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"# Reading requirements\n{NOTE_INSTRUCTION}\n\n"
                f"# Research question\n{question}\n\n"
                f"# The reader's earlier notes\n{listed}\n\n"
                f"# The new note\n{text}"
            ),
        ),
    ]


async def live_note_interpreter(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
    settings: ConfigSettings,
    *,
    completer: Any | None = None,
) -> NoteInterpretation:
    """Ask the configured provider what the note asks, with thinking disabled.

    ``completer`` replaces the provider in tests; production builds the chat
    adapter from ``settings.llm`` exactly as the one-time check does. A
    provider failure propagates, and so does an invalid reading
    (``ValueError``): the session store falls back on either.
    """
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="deep-research-note-interpreter", api_key=None
        )
    )
    provider = (
        completer
        if completer is not None
        else build_chat_provider(clarity_llm_config(settings.llm), tracker)
    )
    async with tracker.session_span(NOTE_TRACE_SESSION, question):
        draft = await provider.complete_structured(
            note_messages(text, question, earlier),
            NoteInterpretationDraft,
            agent_name=None,
            max_tokens=NOTE_MAX_TOKENS,
        )
    reading = validated_interpretation(draft, earlier=earlier)
    if reading is None:
        raise ValueError("the note interpretation broke its contract")
    return reading


async def scripted_note_interpreter(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
    settings: object,
) -> NoteInterpretation:
    """Replay mode's interpreter (spec §4.8): the note restated as written, an emphasis."""
    del question, earlier, settings
    return NoteInterpretation(kinds=["emphasis"], restatement=text)


def reader_note(received: ReceivedNote, reading: NoteInterpretation) -> ReaderNote:
    """The board's record of one interpreted note."""
    return ReaderNote(
        note_id=received.note_id,
        text=received.text,
        received_at=received.received_at,
        received_during=received.received_during,
        kinds=list(reading.kinds),
        restatement=reading.restatement,
        scope=reading.scope,
        new_questions=list(reading.new_questions),
        replaces=reading.replaces,
    )


def note_received_event(received: ReceivedNote) -> ResearchEvent:
    """``session.note.received``: the note as the reader wrote it, published at once."""
    return ResearchEvent(
        event_type="session.note.received",
        source="api",
        message="The research received a note from the reader.",
        metadata={"note_id": received.note_id, "text": received.text},
    )


def note_interpreted_event(note: ReaderNote, *, fallback: bool) -> ResearchEvent:
    """``session.note.interpreted``: the run's reading of the note, for its acknowledgement."""
    return ResearchEvent(
        event_type="session.note.interpreted",
        source="api",
        message="The research read the reader's note.",
        metadata={
            "note_id": note.note_id,
            "restatement": note.restatement,
            "kinds": list(note.kinds),
            "replaces": note.replaces,
            "fallback": fallback,
        },
    )


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
    if note_id not in {note.note_id for note in active_reader_notes(state.reader_notes)}:
        return "replaced"
    review = state.report_review
    verdicts = (
        {entry.note_id: entry.status for entry in review.note_dispositions}
        if review is not None
        else {}
    )
    verdict = verdicts.get(note_id)
    if verdict == "no_evidence":
        return "not_found"
    if verdict == "ignored_with_evidence":
        return "not_addressed"
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


__all__ = [
    "MAX_NEW_QUESTIONS",
    "MAX_RESTATEMENT_CHARS",
    "NOTE_INSTRUCTION",
    "NOTE_MAX_TOKENS",
    "NOTE_SYSTEM_PROMPT",
    "NoteInterpretation",
    "NoteInterpretationDraft",
    "NoteInterpreter",
    "NoteOutcome",
    "NoteScopeDraft",
    "fallback_interpretation",
    "live_note_interpreter",
    "note_interpreted_event",
    "note_messages",
    "note_outcome",
    "note_received_event",
    "note_records",
    "reader_note",
    "scripted_note_interpreter",
    "validated_interpretation",
]
```

- [ ] **Step 4: Run the tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q 2>&1 | tail -1
```

Expected: `200 passed`: Task 1's `180` for the package, plus the new file's `20`.

- [ ] **Step 5: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4901 passed, 6 skipped, 12 deselected` (+20).

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/notes.py src/deep_research/api/models.py tests/test_api/test_notes.py
git commit -m "feat(api): the reader-note interpreter, its fallback and events, outcomes, and the note shapes"
```

---

### Task 8: The notes route, a session's notes, the timings' caps, and the API's documentation (spec §4.6 "Route", "The note board", "Status response"; §4.8 rows; §4.9 `api-gaps.md`, README; AC19's API half; AC20's 409; Phase 2 final review R1, R2, M6)

**Files:**
- Create: `tests/test_api/test_note_route.py`
- Modify: `src/deep_research/api/sessions.py:7`, `:29` (docstring, imports), `:49` (`NotesClosed`, `CheckRecord`), `:94`, `:106` (the session's note fields, settings, timings and check; `publish` closes notes and counts note passes), `:168` (`session_note_fields`), `:175`, `:214` (`SessionStore(note_interpreter=…)`, the board bound around the run's task), `:277` (`add_note`, `_interpret_note`), `:311` (`close` also cancels readings), `:430`, `:443` (`_clarify` records the check); `src/deep_research/api/app.py:33`, `:39`, `:55` (imports), `:67` (two safe messages), `:141` (the response fields), `:182`, `:189`, `:201` (`create_app(note_interpreter=…)`), `:293` (the route); `src/deep_research/utils/config.py:427`, `:435` (`HITL_TIMING_MAX_S` and the three caps); `README.md:758`, `:771`, `:818`, `:855`, `:892`; `docs/design/api-gaps.md:31` (the route, and the response's 22 fields), `:122` (gap 3.9)
- Test: `tests/test_api/conftest.py` (the live-interpreter guard); `tests/test_config.py:13`, `:1447` (the timings' caps)

**Interfaces:**
- Consumes: Task 7's service and shapes; Task 2's board; Phase 2's `SessionStore`, `HitlConfig.note_interpret_timeout_s` and `tests/test_api/conftest.py`.
- Produces:
  - `SessionStore(runner=…, clarity_checker=…, note_interpreter=…)`; `SessionStore.add_note(session_id, text) -> ReceivedNote`, raising `KeyError`, `NotesClosed` or `NoteLimitReached`.
  - Each session owns a `NoteBoard`, bound for its run's task; `ResearchSession.notes_closed` is set by the first published `graph.route.decided` to `finalize` or `end`; `ResearchSession.note_passes` follows `graph.note_pass.started`.
  - `ResearchSession.run_settings` and `ResearchSession.hitl` keep what `start` was given, for the note's reading and its timeout (R1); `ResearchSession.check` (`CheckRecord`) keeps the check's questions, then the answers the run starts with (R2).
  - `HITL_TIMING_MAX_S = 600.0` in `deep_research.utils.config`; `check_timeout_s`, `answer_wait_s` and `note_interpret_timeout_s` are each finite and in (0, 600] (M6), so `1e12` or `Infinity` in a request's `config_overrides` is a `422`, never a failed session.
  - `create_app(..., note_interpreter=None)` defaults to `live_note_interpreter` in live mode and `scripted_note_interpreter` in replay mode; `POST /research/{session_id}/notes`; the four status fields on every session response.

- [ ] **Step 1: Write the failing tests**

`HeldRunner` holds the run open, records the board it was bound to, and ends with every note on it judged. The guard in `conftest.py` replaces `live_note_interpreter` for the whole package, exactly as Phase 2's `live_check_calls` replaces the live checker, so no test builds a provider. The timing tests send `Infinity` as raw JSON (`TestClient`'s own encoder refuses it; FastAPI's parser takes it).

**`tests/test_api/conftest.py`**: 3 edits, in file order.

`tests/test_api/conftest.py` (anchor as written by Phase 2 Task 5) — replace

```python
"""API test guard: no API test reaches the live one-time check (live-briefs spec §4.4).

``create_app`` picks ``live_clarity_check`` for a live-mode app built without a
``clarity_checker``, and every live-mode test app here is built that way. The
live checker is replaced for every test in this package by one that asks
nothing and records the question it was given, so no test can build a provider,
and a test can still see whether the check ran (``live_check_calls``).
"""

from __future__ import annotations
```

with

```python
"""API test guard: no API test reaches the live one-time check (live-briefs spec §4.4)
or the live note interpreter (§4.6).

``create_app`` picks ``live_clarity_check`` for a live-mode app built without a
``clarity_checker``, and every live-mode test app here is built that way. The
live checker is replaced for every test in this package by one that asks
nothing and records the question it was given, so no test can build a provider,
and a test can still see whether the check ran (``live_check_calls``). The live
note interpreter is replaced the same way, by one that restates each note as
written and records its text (``live_note_calls``).
"""

from __future__ import annotations
```

`tests/test_api/conftest.py` (anchor as written by Phase 2 Task 5) — replace

```python
from deep_research.api.clarify import NO_QUESTIONS, ClarityCheck
```

with

```python
from deep_research.api.clarify import NO_QUESTIONS, ClarityCheck
from deep_research.api.notes import NoteInterpretation
```

`tests/test_api/conftest.py` (anchor as written by Phase 2 Task 5) — replace

```python
    monkeypatch.setattr(app_module, "live_clarity_check", asks_nothing)
    return calls
```

with

```python
    monkeypatch.setattr(app_module, "live_clarity_check", asks_nothing)
    return calls


@pytest.fixture(autouse=True)
def live_note_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def restates(text: str, question: str, earlier: object, settings: object) -> NoteInterpretation:
        del question, earlier, settings
        calls.append(text)
        return NoteInterpretation(kinds=["emphasis"], restatement=text)

    monkeypatch.setattr(app_module, "live_note_interpreter", restates)
    return calls
```

Create `tests/test_api/test_note_route.py`:

```python
"""The notes route and a session's notes (live-briefs spec §4.6, §4.8; AC19, AC20).

Store-level tests drive ``SessionStore`` with scripted interpreters and a runner
held open; the route tests go through ``TestClient``. No provider is ever reached:
the package's ``live_note_calls`` fixture replaces the live interpreter.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.notes import NoteInterpretation, scripted_note_interpreter
from deep_research.api.sessions import NotesClosed, SessionStore
from deep_research.runtime.notes import NoteBoard, NoteLimitReached, current_note_board
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings, HitlConfig
from deep_research.utils.types import ResearchEvent, ResearchState
from tests.graph_fakes import fake_report_review
from tests.test_api.fakes import GateRunner, make_outcome
from tests.test_api.test_app import valid_preflight
from tests.test_api.test_clarification import Checker
from tests.test_api.test_replay import replay_app

QUESTION = "What limits grid-scale battery storage?"
FAST = HitlConfig(note_interpret_timeout_s=0.2)


class HeldRunner:
    """A run held open until released; it records the board it was bound to and
    ends with every note the board holds judged by ``verdicts`` (default honoured)."""

    def __init__(self, verdicts: Mapping[str, str] | None = None, *, note_passes: int = 0) -> None:
        self.verdicts = dict(verdicts or {})
        self.note_passes = note_passes
        self.release = asyncio.Event()
        self.board: NoteBoard | None = None

    async def __call__(
        self, *, question: str, session_id: str,
        event_handler: Callable[[ResearchEvent], None] | None = None, **kwargs: Any,
    ) -> ResearchOutcome:
        self.board = current_note_board()
        if event_handler is not None:
            event_handler(ResearchEvent(
                event_type="graph.node.started", source="graph.researcher", message="Node researcher started.",
                metadata={"node": "researcher", "iteration": 0},
            ))
            for number in range(1, self.note_passes + 1):
                event_handler(ResearchEvent(
                    event_type="graph.note_pass.started", source="graph", message="Note pass started.",
                    metadata={"iteration": 0, "note_passes": number, "note_ids": [], "targets": []},
                ))
        await self.release.wait()
        notes = self.board.snapshot() if self.board is not None else []
        state = ResearchState(
            session_id=session_id, original_question=question, reader_notes=notes,
            note_passes=self.note_passes,
            report_review=fake_report_review(
                note_dispositions={note.note_id: self.verdicts.get(note.note_id, "honoured") for note in notes}
            ),
        )
        return make_outcome(session_id=session_id, question=question, state=state)


class Interpreter:
    """A scripted ``NoteInterpreter``: reads, raises, or hangs; records each call."""

    def __init__(self, reply: NoteInterpretation | BaseException | None = None, *, hang: bool = False) -> None:
        self.reply, self.hang = reply, hang
        self.calls: list[tuple[str, str, list[str]]] = []

    async def __call__(self, text: str, question: str, earlier: Any, settings: object) -> NoteInterpretation:
        self.calls.append((text, question, [note.note_id for note in earlier]))
        if self.hang:
            await asyncio.Event().wait()
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply or NoteInterpretation(kinds=["exclude"], restatement=f"leave out {text.lower()}")


def _start(store: SessionStore) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml", ask_clarifying_questions=False,
        settings=ConfigSettings(), hitl=FAST,
    )


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


# --- the store ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_note_is_published_at_once_then_read_onto_the_boards_the_run_holds() -> None:
    runner, interpreter = HeldRunner(), Interpreter()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    received = store.add_note("s1", "Pumped hydro")

    assert (received.note_id, received.text, received.received_during) == ("n1", "Pumped hydro", "researcher")
    assert _types(session.events)[-1] == "session.note.received"
    await _until(lambda: _types(session.events)[-1] == "session.note.interpreted")
    assert session.events[-1].metadata == {
        "note_id": "n1", "restatement": "leave out pumped hydro", "kinds": ["exclude"],
        "replaces": None, "fallback": False,
    }
    assert runner.board is session.note_board
    assert [note.note_id for note in session.note_board.snapshot()] == ["n1"]
    assert interpreter.calls == [("Pumped hydro", QUESTION, [])]
    store.add_note("s1", "Only 2024")
    await _until(lambda: len(session.note_board.snapshot()) == 2)
    assert interpreter.calls[-1] == ("Only 2024", QUESTION, ["n1"])
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interpreter",
    [Interpreter(RuntimeError("provider down")), Interpreter(hang=True), None],
    ids=["fails", "hangs", "missing"],
)
async def test_a_failed_slow_or_missing_interpreter_keeps_the_note_as_written(
    interpreter: Interpreter | None,
) -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    began = time.monotonic()

    store.add_note("s1", "Mostly the US, please.")
    await _until(lambda: _types(session.events)[-1] == "session.note.interpreted")

    assert time.monotonic() - began < FAST.note_interpret_timeout_s + 1.0
    assert session.events[-1].metadata == {
        "note_id": "n1", "restatement": "Mostly the US, please.", "kinds": ["emphasis"],
        "replaces": None, "fallback": True,
    }
    assert session.note_board.snapshot()[0].restatement == "Mostly the US, please."
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_notes_close_while_the_session_waits_once_publishing_begins_and_once_it_ends() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    session.status = "needs_input"
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too early")
    session.status = "running"
    session.publish(ResearchEvent(
        event_type="graph.route.decided", source="graph", message="Publish.",
        metadata={"destination": "finalize", "reason": "report_accepted", "iteration": 0},
    ))
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too late")
    assert session.note_board.accepted == 0
    with pytest.raises(KeyError):
        store.add_note("missing", "who?")
    runner.release.set()
    await _until(lambda: session.finished_at is not None)
    with pytest.raises(NotesClosed):
        store.add_note("s1", "after the end")


@pytest.mark.asyncio
async def test_a_route_decision_that_loops_back_keeps_the_notes_open() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    for destination in ("note_pass", "extra_pass", "redraft"):
        session.publish(ResearchEvent(
            event_type="graph.route.decided", source="graph", message="Loop.",
            metadata={"destination": destination, "reason": "note_pass_requested", "iteration": 0},
        ))

    assert store.add_note("s1", "still open").note_id == "n1"
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_the_eleventh_note_is_refused_and_a_refusal_is_never_counted() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    ids = [store.add_note("s1", f"note {number}").note_id for number in range(1, 11)]

    assert ids == [f"n{number}" for number in range(1, 11)]
    with pytest.raises(NoteLimitReached):
        store.add_note("s1", "one too many")
    assert session.note_board.accepted == 10 and session.note_board.remaining == 0
    await _until(lambda: session.note_board.pending == ())
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_closing_the_store_drops_a_note_still_being_read() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter(hang=True))
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    store.add_note("s1", "never read")
    await asyncio.sleep(0.02)

    await store.close()

    assert session.note_tasks == set()
    assert session.note_board.pending == ()
    assert session.note_board.snapshot() == []
    await asyncio.wait_for(session.note_board.settled(), timeout=1)


# --- the route ---------------------------------------------------------------------------


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


def test_the_notes_route_takes_a_note_and_the_status_lists_it_with_its_outcome() -> None:
    """AC19's status half: every note, its reading and its outcome; the pass count."""
    runner = HeldRunner({"n2": "no_evidence", "n3": "ignored_with_evidence"}, note_passes=1)
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        first = client.post(f"/research/{session_id}/notes", json={"text": "  Pumped hydro  "})
        second = client.post(f"/research/{session_id}/notes", json={"text": "Flow batteries"})
        third = client.post(f"/research/{session_id}/notes", json={"text": "Grid codes"})
        running = _wait(client, session_id, lambda body: all(n["restatement"] for n in body["notes"]))
        client.portal.call(runner.release.set)
        done = _wait(client, session_id, lambda body: body["status"] == "completed")
        closed = client.post(f"/research/{session_id}/notes", json={"text": "after the end"})

    assert (first.status_code, first.json()) == (202, {"note_id": "n1", "status": "received"})
    assert (second.json()["note_id"], third.json()["note_id"]) == ("n2", "n3")
    assert running["notes"] == [
        {"note_id": "n1", "text": "Pumped hydro", "restatement": "leave out pumped hydro", "outcome": "pending"},
        {"note_id": "n2", "text": "Flow batteries", "restatement": "leave out flow batteries", "outcome": "pending"},
        {"note_id": "n3", "text": "Grid codes", "restatement": "leave out grid codes", "outcome": "pending"},
    ]
    assert (running["notes_remaining"], running["note_passes"], running["clarification"]) == (7, 1, None)
    # A note the report still ignores is reported as such, never as covered (open issue O8).
    assert [(n["note_id"], n["outcome"]) for n in done["notes"]] == [
        ("n1", "covered"), ("n2", "not_found"), ("n3", "not_addressed"),
    ]
    assert (done["notes_remaining"], done["note_passes"]) == (7, 1)
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "notes_closed"


def test_the_notes_route_refuses_unknown_sessions_bad_text_and_the_eleventh_note() -> None:
    """AC20's API half: the eleventh note is a 409 ``note_limit_reached``."""
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        unknown = client.post("/research/missing/notes", json={"text": "hello"})
        empty = client.post(f"/research/{session_id}/notes", json={"text": "   "})
        long = client.post(f"/research/{session_id}/notes", json={"text": "x" * 501})
        accepted = [client.post(f"/research/{session_id}/notes", json={"text": f"note {n}"}) for n in range(10)]
        eleventh = client.post(f"/research/{session_id}/notes", json={"text": "one too many"})
        status = _status(client, session_id)
        client.portal.call(runner.release.set)

    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "session_not_found"
    assert (empty.status_code, long.status_code) == (422, 422)
    assert [response.status_code for response in accepted] == [202] * 10
    assert eleventh.status_code == 409
    assert eleventh.json()["error"] == {
        "code": "note_limit_reached", "message": "Research session takes no more notes.", "issues": [], "reason": None,
    }
    assert status["notes_remaining"] == 0 and len(status["notes"]) == 10


def test_a_session_without_notes_answers_the_new_fields_empty(live_note_calls: list[str]) -> None:
    app = create_app(runner=GateRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        body = _status(client, session_id)
        posted = client.post(f"/research/{session_id}/notes", json={"text": "read me"})
        _wait(client, session_id, lambda status: status["notes"][0]["restatement"] is not None)

    assert (body["notes"], body["notes_remaining"], body["note_passes"], body["clarification"]) == ([], 10, 0, None)
    assert posted.status_code == 202
    assert live_note_calls == ["read me"]


def test_a_replay_mode_app_reads_notes_with_the_scripted_interpreter(tmp_path: Path) -> None:
    app = replay_app(tmp_path)

    assert app.state.session_store._note_interpreter is scripted_note_interpreter


def test_the_status_carries_the_one_time_check_the_session_asked() -> None:
    """spec §4.6 Status response: ``clarification: {questions, answers}``."""
    app = create_app(runner=GateRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        waiting = _wait(client, session_id, lambda body: body["status"] == "needs_input")
        refused = client.post(f"/research/{session_id}/notes", json={"text": "too early"})
        client.post(f"/research/{session_id}/answers", json={"answers": [{"question_id": "q1", "choice": "European Union"}]})
        running = _wait(client, session_id, lambda body: body["status"] == "running")

    assert [q["id"] for q in waiting["clarification"]["questions"]] == ["q1", "q2", "q3"]
    assert waiting["clarification"]["answers"] == []
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "notes_closed"
    assert running["clarification"]["answers"] == [
        {"question_id": "q1", "value": "European Union", "source": "chosen"},
        {"question_id": "q2", "value": "Since 2023", "source": "best_guess"},
        {"question_id": "q3", "value": "General understanding", "source": "best_guess"},
    ]


@pytest.mark.parametrize("wait", [1e12, float("inf")])
def test_a_timing_too_long_or_not_finite_is_refused_with_the_request(wait: float) -> None:
    """Phase 2's final review (M6): ``1e12`` s passed validation, then overflowed the
    answer deadline inside the session. Now the request itself is a 422, and no
    session starts. The body is sent as raw JSON: ``Infinity`` is not standard JSON,
    so the test client's encoder refuses it, but FastAPI's parser takes it."""
    body = json.dumps({"query": QUESTION, "config_overrides": {"hitl": {"answer_wait_s": wait}}})
    app = create_app(runner=GateRunner(), preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        refused = client.post("/research", content=body, headers={"content-type": "application/json"})
        listed = client.get("/research").json()["sessions"]

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "validation_error"
    assert listed == []
```

**`tests/test_config.py`**: 2 edits, in file order.

`tests/test_config.py` — replace

```python
from deep_research.utils.config import (
    PRODUCTION_AGENT_NAMES,
```

with

```python
from deep_research.utils.config import (
    HITL_TIMING_MAX_S,
    PRODUCTION_AGENT_NAMES,
```

`tests/test_config.py` (anchor as written by Phase 2 Task 2) — replace

```python
            apply_config_overrides(ConfigSettings(), {"hitl": bad})
```

with

```python
            apply_config_overrides(ConfigSettings(), {"hitl": bad})


HITL_TIMINGS = ("check_timeout_s", "answer_wait_s", "note_interpret_timeout_s")


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 600.5, 1e12])
@pytest.mark.parametrize("field", HITL_TIMINGS)
def test_each_hitl_timing_refuses_infinite_nan_and_oversized_values(
    field: str, bad: float
) -> None:
    """A timing that is not a finite number of seconds up to ten minutes is refused
    where the request is validated: ``1e12`` s used to pass and then overflow the
    session's answer deadline, failing the run instead of the request."""
    with pytest.raises(ValidationError):
        HitlConfig(**{field: bad})
    with pytest.raises(ValueError):
        apply_config_overrides(ConfigSettings(), {"hitl": {field: bad}})


def test_each_hitl_timing_takes_up_to_ten_minutes() -> None:
    timings = HitlConfig(**{field: HITL_TIMING_MAX_S for field in HITL_TIMINGS})

    assert HITL_TIMING_MAX_S == 600.0
    assert [getattr(timings, field) for field in HITL_TIMINGS] == [600.0, 600.0, 600.0]
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_note_route.py -q 2>&1 | tail -3
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_config.py -q 2>&1 | tail -3
```

Expected: first `ERROR tests/test_api/test_note_route.py`, `Interrupted: 1 error during collection`, `1 error in …` (`cannot import name 'NotesClosed' from 'deep_research.api.sessions'`); then `ERROR tests/test_config.py`, `Interrupted: 1 error during collection`, `1 error in …` (`cannot import name 'HITL_TIMING_MAX_S' from 'deep_research.utils.config'`).

- [ ] **Step 3: Implement the session's notes and the route**

**`src/deep_research/utils/config.py`**: 2 edits, in file order.

`src/deep_research/utils/config.py` (anchor as written by Phase 2 Task 2) — replace

```python

class HitlConfig(BaseModel):
```

with

```python

HITL_TIMING_MAX_S = 600.0
"""The longest any reader-in-the-loop timing may be: ten minutes."""


class HitlConfig(BaseModel):
```

`src/deep_research/utils/config.py` (anchor as written by Phase 2 Task 2) — replace

```python
    ``config_overrides`` may set each; nothing reads them from the environment.
    """

    model_config = ConfigDict(extra="forbid")

    check_timeout_s: float = Field(default=20.0, gt=0)
    answer_wait_s: float = Field(default=60.0, gt=0)
    note_interpret_timeout_s: float = Field(default=15.0, gt=0)
```

with

```python
    ``config_overrides`` may set each; nothing reads them from the environment.

    Each is a finite number of seconds in (0, ``HITL_TIMING_MAX_S``]. A larger
    or non-finite value is refused where the request is validated (a ``422``):
    ``1e12`` seconds would otherwise pass here and then overflow the session's
    answer deadline, failing the run instead of the request.
    """

    model_config = ConfigDict(extra="forbid")

    check_timeout_s: float = Field(
        default=20.0, gt=0, le=HITL_TIMING_MAX_S, allow_inf_nan=False
    )
    answer_wait_s: float = Field(
        default=60.0, gt=0, le=HITL_TIMING_MAX_S, allow_inf_nan=False
    )
    note_interpret_timeout_s: float = Field(
        default=15.0, gt=0, le=HITL_TIMING_MAX_S, allow_inf_nan=False
    )
```

**`src/deep_research/api/sessions.py`**: 12 edits, in file order.

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
file system, or a provider: the one-time check reaches a provider only through
the injected ``ClarityChecker``.
"""
```

with

```python
file system, or a provider: the one-time check reaches a provider only through
the injected ``ClarityChecker``, and a reader note only through the injected
``NoteInterpreter``.
"""
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
from deep_research.api.models import (
    ClarificationAnswersRequest,
    CoverageProgressResponse,
    EvidenceCountsResponse,
    SessionStatus,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
```

with

```python
from deep_research.api.models import (
    ClarificationAnswerResponse,
    ClarificationAnswersRequest,
    ClarificationQuestionResponse,
    ClarificationRecordResponse,
    CoverageProgressResponse,
    EvidenceCountsResponse,
    ReaderNoteResponse,
    SessionStatus,
)
from deep_research.api.notes import (
    NoteInterpreter,
    fallback_interpretation,
    note_interpreted_event,
    note_received_event,
    note_records,
    reader_note,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.notes import NoteBoard, ReceivedNote, bind_note_board
from deep_research.runtime.outcome import ResearchOutcome
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
    """Answers arrived for a session that is not waiting for them (a 409)."""
```

with

```python
    """Answers arrived for a session that is not waiting for them (a 409)."""


class NotesClosed(Exception):
    """A note arrived for a session that no longer takes notes (a 409, spec §4.6)."""


@dataclass(frozen=True, slots=True)
class CheckRecord:
    """The one-time check a session asked, and the answers its run started with."""

    questions: tuple[ClarityQuestion, ...]
    answers: tuple[ReaderAnswer, ...] = ()
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
    """The check this session waits on while ``needs_input``, else ``None``."""
```

with

```python
    """The check this session waits on while ``needs_input``, else ``None``."""
    check: CheckRecord | None = None
    """The check this session asked, if any, kept for the status response."""
    note_board: NoteBoard = field(default_factory=NoteBoard)
    """The reader's notes (live-briefs spec §4.6), bound for the run's task."""
    note_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    notes_closed: bool = False
    """Set once the stream shows publication has begun: no note is taken after."""
    note_passes: int = 0
    run_settings: Any = None
    hitl: HitlConfig = field(default_factory=HitlConfig)
```

`src/deep_research/api/sessions.py` — replace

```python
            self.iteration = iteration
        self.changed.set()
```

with

```python
            self.iteration = iteration
        # live-briefs spec §4.6: a decision to publish (or to end) closes the
        # notes — this is the event that makes Publishing the active row, so
        # the page and the API agree on when notes stop.
        if event.event_type == "graph.route.decided" and event.metadata.get(
            "destination"
        ) in ("finalize", "end"):
            self.notes_closed = True
        note_passes = event.metadata.get("note_passes")
        if event.event_type == "graph.note_pass.started" and isinstance(note_passes, int):
            self.note_passes = note_passes
        self.changed.set()
```

`src/deep_research/api/sessions.py` — replace

```python

class SessionStore:
```

with

```python

def session_note_fields(session: ResearchSession) -> dict[str, object]:
    """The session response's reader-side fields (live-briefs spec §4.4, §4.6).

    Every note in receipt order with its reading and outcome, how many more
    notes the session takes, the note passes the run bought, and the one-time
    check it asked. A finished run's own state is the authority for outcomes
    and the pass count; while it runs, the stream's count stands in.
    """
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
        "notes_remaining": session.note_board.remaining,
        "note_passes": state.note_passes if state is not None else session.note_passes,
        "clarification": (
            None
            if check is None
            else ClarificationRecordResponse(
                questions=[
                    ClarificationQuestionResponse(**question.model_dump(mode="json"))
                    for question in check.questions
                ],
                answers=[
                    ClarificationAnswerResponse(
                        question_id=answer.question_id,
                        value=answer.value,
                        source=answer.source,
                    )
                    for answer in check.answers
                ],
            )
        ),
    }


class SessionStore:
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
        clarity_checker: ClarityChecker | None = None,
    ) -> None:
        self._runner = runner
        self._clarity_checker = clarity_checker
        self._sessions: dict[str, ResearchSession] = {}
```

with

```python
        clarity_checker: ClarityChecker | None = None,
        note_interpreter: NoteInterpreter | None = None,
    ) -> None:
        self._runner = runner
        self._clarity_checker = clarity_checker
        self._note_interpreter = note_interpreter
        self._sessions: dict[str, ResearchSession] = {}
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
            started_at=datetime.now(timezone.utc),
        )
        self._sessions[session_id] = session
        session.task = asyncio.create_task(
            self._run(
                session=session,
                query=query,
                max_extra_passes=max_extra_passes,
                output_format=output_format,
                config_overrides=config_overrides,
                config_path=config_path,
                ask_clarifying_questions=ask_clarifying_questions,
                settings=settings,
                hitl=hitl or HitlConfig(),
            )
        )
        return session
```

with

```python
            started_at=datetime.now(timezone.utc),
            run_settings=settings,
            hitl=hitl or HitlConfig(),
        )
        self._sessions[session_id] = session
        # live-briefs spec §4.6: the task copies this context, so the session's
        # note board is bound for its whole run — every node and every task an
        # agent starts inside it reads the same board.
        with bind_note_board(session.note_board):
            session.task = asyncio.create_task(
                self._run(
                    session=session,
                    query=query,
                    max_extra_passes=max_extra_passes,
                    output_format=output_format,
                    config_overrides=config_overrides,
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

    def add_note(self, session_id: str, text: str) -> ReceivedNote:
        """Accept one reader note for a running session (live-briefs spec §4.6).

        Raises ``KeyError`` for an unknown session; ``NotesClosed`` while the
        session waits for the one-time check's answers, once it has finished
        or stopped, and once its stream shows publication has begun; and
        ``NoteLimitReached`` past the tenth accepted note (D11a). A refused
        note is never counted. An accepted note is published at once, then
        interpreted in the background: the interpreted note joins the run's
        board and ``session.note.interpreted`` follows.
        """
        session = self.require(session_id)
        if (
            session.status != "running"
            or session.finished_at is not None
            or session.notes_closed
        ):
            raise NotesClosed(session_id)
        received = session.note_board.receive(
            text,
            received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            received_during=session.current_agent or "planner",
        )
        session.publish(note_received_event(received))
        task = asyncio.create_task(self._interpret_note(session, received))
        session.note_tasks.add(task)
        task.add_done_callback(session.note_tasks.discard)
        return received

    async def _interpret_note(
        self, session: ResearchSession, received: ReceivedNote
    ) -> None:
        """Read one note within ``hitl.note_interpret_timeout_s``, or keep it as written.

        Any failure — no interpreter, a provider error, a timeout, an invalid
        reading — keeps the note as an emphasis in the reader's own words, and
        its event says ``fallback`` (spec §4.6). Cancellation (the service
        closing) drops the note from the board's pending set, so nothing waits
        on it.
        """
        earlier = session.note_board.snapshot()
        fallback = False
        try:
            if self._note_interpreter is None:
                raise LookupError("no note interpreter")
            async with asyncio.timeout(session.hitl.note_interpret_timeout_s):
                reading = await self._note_interpreter(
                    received.text, session.query, earlier, session.run_settings
                )
        except asyncio.CancelledError:
            session.note_board.drop(received.note_id)
            raise
        except Exception as error:  # noqa: BLE001 - a note is never lost to its reading
            _log.warning("note interpretation fell back: %s", type(error).__name__)
            reading, fallback = fallback_interpretation(received.text), True
        note = reader_note(received, reading)
        session.note_board.add(note)
        session.publish(note_interpreted_event(note, fallback=fallback))

    async def iter_events(
```

`src/deep_research/api/sessions.py` — replace

```python
            if session.task is not None and not session.task.done()
        ]
```

with

```python
            if session.task is not None and not session.task.done()
        ] + [
            task
            for session in self._sessions.values()
            for task in session.note_tasks
            if not task.done()
        ]
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
        session.clarification = pending
        session.status = "needs_input"
```

with

```python
        session.clarification = pending
        session.check = CheckRecord(questions=questions)
        session.status = "needs_input"
```

`src/deep_research/api/sessions.py` (anchor as written by Phase 2 Task 5) — replace

```python
        session.status = "running"
        session.publish(clarification_answered_event(answers, reason))
```

with

```python
        session.status = "running"
        session.check = CheckRecord(questions=questions, answers=tuple(answers))
        session.publish(clarification_answered_event(answers, reason))
```

**`src/deep_research/api/app.py`**: 9 edits, in file order.

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
    ClarificationAnswersRequest,
    ResearchRequest,
```

with

```python
    ClarificationAnswersRequest,
    NoteAcceptedResponse,
    NoteRequest,
    ResearchRequest,
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
    ValidationIssue,
)
from deep_research.api.sessions import (
    NotWaitingForInput,
    ResearchRunner,
    ResearchSession,
    SessionStore,
    outcome_response_fields,
)
```

with

```python
    ValidationIssue,
)
from deep_research.api.notes import (
    NoteInterpreter,
    live_note_interpreter,
    scripted_note_interpreter,
)
from deep_research.api.sessions import (
    NotesClosed,
    NotWaitingForInput,
    ResearchRunner,
    ResearchSession,
    SessionStore,
    outcome_response_fields,
    session_note_fields,
)
```

`src/deep_research/api/app.py` — replace

```python
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.utils.config import ConfigSettings
```

with

```python
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.notes import NoteLimitReached
from deep_research.utils.config import ConfigSettings
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
    "not_waiting_for_input": "Research session is not waiting for answers.",
}
```

with

```python
    "not_waiting_for_input": "Research session is not waiting for answers.",
    "notes_closed": "Research session no longer takes notes.",
    "note_limit_reached": "Research session takes no more notes.",
}
```

`src/deep_research/api/app.py` — replace

```python
        **outcome_response_fields(session.outcome),
    )
```

with

```python
        **outcome_response_fields(session.outcome),
        **session_note_fields(session),
    )
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
    clarity_checker: ClarityChecker | None = None,
) -> FastAPI:
```

with

```python
    clarity_checker: ClarityChecker | None = None,
    note_interpreter: NoteInterpreter | None = None,
) -> FastAPI:
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
    replay server can never reach a provider for the check.
    """
    if clarity_checker is None:
        clarity_checker = (
            live_clarity_check if mode == "live" else scripted_clarity_check
        )
```

with

```python
    replay server can never reach a provider for the check.
    ``note_interpreter`` reads each reader note (spec §4.6) and follows
    ``mode`` the same way: replay restates a note as written.
    """
    if clarity_checker is None:
        clarity_checker = (
            live_clarity_check if mode == "live" else scripted_clarity_check
        )
    if note_interpreter is None:
        note_interpreter = (
            live_note_interpreter if mode == "live" else scripted_note_interpreter
        )
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
            )
        )
    store = SessionStore(runner=runner, clarity_checker=clarity_checker)

    @asynccontextmanager
```

with

```python
            )
        )
    store = SessionStore(
        runner=runner,
        clarity_checker=clarity_checker,
        note_interpreter=note_interpreter,
    )

    @asynccontextmanager
```

`src/deep_research/api/app.py` (anchor as written by Phase 2 Task 5) — replace

```python
            raise RequestValidationError(error.errors()) from None
        return _session_response(session)
```

with

```python
            raise RequestValidationError(error.errors()) from None
        return _session_response(session)

    @router.post(
        "/research/{session_id}/notes",
        status_code=202,
        response_model=NoteAcceptedResponse,
    )
    async def add_research_note(
        request: Request,
        payload: NoteRequest,
    ) -> NoteAcceptedResponse:
        """Take one reader note for a running session (live-briefs spec §4.6).

        ``404`` for an unknown session; ``409 notes_closed`` while the session
        waits for the one-time check's answers, once it has finished or
        stopped, and once publication has begun; ``409 note_limit_reached``
        past the tenth accepted note (D11a); ``422`` for an empty or overlong
        note. The note is interpreted after this answer, which is why its
        status is ``received``.
        """
        try:
            received = store.add_note(request.state.session_id, payload.text)
        except KeyError:
            raise ApiProblem(code="session_not_found", status_code=404) from None
        except NotesClosed:
            raise ApiProblem(code="notes_closed", status_code=409) from None
        except NoteLimitReached:
            raise ApiProblem(code="note_limit_reached", status_code=409) from None
        return NoteAcceptedResponse(note_id=received.note_id)
```

- [ ] **Step 4: Run the API package and the config tests**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q 2>&1 | tail -1
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_config.py -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config 2>&1 | tail -1
```

Expected: `215 passed` (Task 7's `200`, plus the new file's `15`), then `154 passed, 1 deselected` (the config tests, `13` of them new; the deselected one needs a `.env`). Every existing API test passes unchanged with the guard in place.

- [ ] **Step 5: Document the route**

**`README.md`**: 5 edits, in file order.

`README.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
endpoint, five session-scoped read endpoints (`status`, `stream`,
`report`, `evidence`, `trace`) and one session-scoped answers endpoint for the
one-time check, all served by a process-local `SessionStore`:
```

with

```markdown
endpoint, five session-scoped read endpoints (`status`, `stream`,
`report`, `evidence`, `trace`), one session-scoped answers endpoint for the
one-time check and one for the reader's notes, all served by a process-local
`SessionStore`:
```

`README.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
| `POST` | `/research/{session_id}/answers` | `202` `ResearchSessionResponse` (the one-time check's answers, below) |
```

with

```markdown
| `POST` | `/research/{session_id}/answers` | `202` `ResearchSessionResponse` (the one-time check's answers, below) |
| `POST` | `/research/{session_id}/notes` | `202` `{"note_id": "n1", "status": "received"}` (a reader note, below) |
```

`README.md` — replace

````markdown

A finished session's snapshot also carries the outcome's own readings, added
````

with

````markdown

**Reader notes** (live-briefs spec §4.6). While a session is `running`, the reader
may add up to ten notes — something to focus on, leave out or change:

```bash
curl -X POST http://localhost:8000/research/<session_id>/notes \
  -H "Content-Type: application/json" \
  -d '{"text": "More on fire-safety standards, please"}'
```

The note (1–500 characters, one line) is accepted at once as `session.note.received`
(`{note_id, text}`), then read by the configured model, thinking disabled, within
`hitl.note_interpret_timeout_s` (15 s): `session.note.interpreted` carries the run's
reading (`{note_id, restatement, kinds, replaces, fallback}`). A reading that fails or
times out keeps the note as written, as an emphasis, with `fallback: true`. Every step
but evidence verification reads the notes (a later note replaces an earlier one it
contradicts); a note the review finds no evidence for buys one targeted research pass,
and a note the report ignores buys one redraft, neither spending the extra-pass budget
or the writer's own re-run. Notes close once `finalize_report` has started: the run's
published decision to publish (or to end) closes them (live-briefs Phase 3 plan,
ambiguity 5). The status snapshot carries `notes` (each with its `text`, its
`restatement` once read, and its `outcome`: `covered`, `not_found`, `not_addressed`
when the report still does not follow the note after its one redraft, `replaced`, or
`pending` while the run goes on or when no review judged it), `notes_remaining`,
`note_passes`, and `clarification` (the one-time check's questions and the answers the
run started with, or `null`).

A finished session's snapshot also carries the outcome's own readings, added
````

`README.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
| `422` | Invalid request body or override shape, or answers that do not fit the session's questions (an unknown or repeated `question_id`, a `choice` that was not offered); the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace` and `/answers` |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) or without an evidence log (`evidence_unavailable`, on `/evidence`); answers sent to a session that is not waiting for them (`not_waiting_for_input`: never asked, already answered, or past its deadline) |
| `500` | Missing or invalid service configuration (`configuration_error`), without file contents, secret values, provider text, or tracebacks |
```

with

```markdown
| `422` | Invalid request body or override shape, or answers that do not fit the session's questions (an unknown or repeated `question_id`, a `choice` that was not offered); the error body lists field locations and types only, never rejected values |
| `404` | Unknown `session_id`, identical for `/status`, `/stream`, `/report`, `/evidence`, `/trace`, `/answers` and `/notes` |
| `409` | Report requested while no outcome exists yet (`session_not_complete`), or from a session that finished without a report (`report_unavailable`) or without an evidence log (`evidence_unavailable`, on `/evidence`); answers sent to a session that is not waiting for them (`not_waiting_for_input`: never asked, already answered, or past its deadline); a note sent while the session waits for answers, once `finalize_report` has started (from the run's published decision to publish, live-briefs Phase 3 plan ambiguity 5), or after it finished (`notes_closed`), or past its tenth note (`note_limit_reached`) |
| `500` | Missing or invalid service configuration (`configuration_error`), without file contents, secret values, provider text, or tracebacks |
```

`README.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
questions (Region, Period, For), so the check can be exercised offline.
```

with

```markdown
questions (Region, Period, For), so the check can be exercised offline. A reader note is
read in replay mode by a scripted interpreter that keeps it as written, as an emphasis;
replay runs the graph at full speed and paces only the stream, so a note added while the
running stage plays arrives after the engine has finished and ends `pending`.
```

**`docs/design/api-gaps.md`**: 2 edits, in file order.

`docs/design/api-gaps.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
| `POST` | `/research/{id}/answers` | `202` `ResearchSessionResponse`: the reader's answers to the one-time check, taken once; `404` unknown session, `409` `not_waiting_for_input`, `422` an answer that does not fit its question (`api/app.py:265-294`, `api/sessions.py` `submit_answers`) |

`ResearchSessionResponse` (`api/models.py:114-164`, assembled at
`api/sessions.py:73-128`), 18 fields: `session_id`, `query`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, `errors`, `evidence_path`, `quality_path`,
`quality_contract_version`, `semantic_review_status`,
`semantic_review_score`, `duration_seconds`, `coverage`
(`required_targets`, `answered_targets`, `missing_required_target_ids`,
`not_found_target_ids`) and `evidence_counts` (fifteen counts; `null`
unless the run left both a composition and a quality snapshot,
`runtime/outcome.py:525`). `status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
```

with

```markdown
| `POST` | `/research/{id}/answers` | `202` `ResearchSessionResponse`: the reader's answers to the one-time check, taken once; `404` unknown session, `409` `not_waiting_for_input`, `422` an answer that does not fit its question (`api/app.py:265-294`, `api/sessions.py` `submit_answers`) |
| `POST` | `/research/{id}/notes` | `202` `{note_id, status: "received"}`: one reader note, read in the background (`session.note.received`, then `session.note.interpreted`); `404` unknown session, `409` `notes_closed` (waiting for answers, once `finalize_report` has started — from the run's published decision to publish, live-briefs Phase 3 ambiguity 5 — or finished) or `note_limit_reached` (past the tenth note), `422` empty or over 500 characters (`api/app.py` `add_research_note`, `api/sessions.py` `add_note`) |

`ResearchSessionResponse` (`api/models.py:114-164`, assembled at
`api/sessions.py:73-128`), 22 fields: `session_id`, `query`, `status`,
`current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`,
`trace_url`, `errors`, `evidence_path`, `quality_path`,
`quality_contract_version`, `semantic_review_status`,
`semantic_review_score`, `duration_seconds`, `coverage`
(`required_targets`, `answered_targets`, `missing_required_target_ids`,
`not_found_target_ids`), `evidence_counts` (fifteen counts; `null`
unless the run left both a composition and a quality snapshot,
`runtime/outcome.py:525`), and the reader's side (live-briefs Phase 3):
`notes` (each note as written, the run's reading of it and its outcome),
`notes_remaining`, `note_passes` and `clarification` (the one-time check's
questions and the answers the run started with, or `null`). `status` is one of `running`, `needs_input` (the
one-time check waiting for the reader; not terminal), `completed`,
```

`docs/design/api-gaps.md` (anchor as written by Phase 2 Task 5) — replace

```markdown
| 3.8 | **No `checking` state for the one-time check** (live-briefs Phase 2, open issue O2) | While the live check call runs, for up to `hitl.check_timeout_s` (20 s), `status` reads `running` and the stream carries nothing, so the console shows stage 3 with Planning active; if questions come back, stage 2a replaces the pipeline card. Replay's checker answers at once, so replay never shows it. | `SessionStore._clarify` (`api/sessions.py`) knows the check is running but publishes nothing until the questions exist | Show stage 3 until `session.clarification.requested` arrives | A `session.clarification.started` event, or a `checking` status, pending the human's ruling on O2 |
```

with

```markdown
| 3.8 | **No `checking` state for the one-time check** (live-briefs Phase 2, open issue O2) | While the live check call runs, for up to `hitl.check_timeout_s` (20 s), `status` reads `running` and the stream carries nothing, so the console shows stage 3 with Planning active; if questions come back, stage 2a replaces the pipeline card. Replay's checker answers at once, so replay never shows it. | `SessionStore._clarify` (`api/sessions.py`) knows the check is running but publishes nothing until the questions exist | Show stage 3 until `session.clarification.requested` arrives | A `session.clarification.started` event, or a `checking` status, pending the human's ruling on O2 |
| 3.9 | **A replay run never applies a reader note** (live-briefs Phase 3, open issue O2) | Replay runs the graph at full speed and paces only the stream, so by the time the running stage shows a step the engine has finished; a note sent then is received, read and acknowledged, but no step reads it, and it ends `pending` (`not checked`). A live run applies every note that arrives before `finalize_report` starts (read from the run's decision to publish, ambiguity 5 of the same plan). | `ReplayRunner` (`api/replay.py`) drains its event queue after `run_research` returns | Prove the engine's use of notes offline (`tests/test_graph/test_reader_notes_replay.py`), and the page's flow on the replay server | A replay runner that holds each node until the stream has published the node's start, pending the human's ruling on O2 |
```

- [ ] **Step 6: Run the full suite**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4929 passed, 6 skipped, 12 deselected` (+28): the backend's final count.

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/api/sessions.py src/deep_research/api/app.py src/deep_research/utils/config.py tests/test_api/conftest.py tests/test_api/test_note_route.py tests/test_config.py README.md docs/design/api-gaps.md
git commit -m "feat(api): the notes route, the session's note board, the notes on the status response, and capped reader timings"
```

---

### Task 9: The web app's model of notes (spec §4.7 "Acknowledgements", "Note pass"; D9)

**Files:**
- Create: `web/lib/notes.ts`, `web/test/notes.test.ts`
- Modify: `web/lib/api.ts:35` (the response fields and note types), `:124` (`addNote`, after Phase 2's `submitAnswers`); `web/lib/run-state.ts:8` (import), `:24` (`ARCS.note_pass`), `:51` (`ReopenLine` kinds, `NoteState`), `:62`, `:78`, `:97` (`RunState.arc`, `RunState.notes`), `:119` (`HOP_ROW`), `:150` (`reviewOutcome`), `:187` (the hop check), `:262` (the note-pass route), `:293` (the two note-loop handlers), `:311` (the two note handlers); `web/lib/briefs.ts:3`, `:18`, `:54` (`RowBrief.acks`, `.earlier`)
- Test: `web/test/run-state.test.ts:28` (twenty-three handlers)

**Interfaces:**
- Consumes: Task 8's API (`POST /notes`, the status fields, the note events), and the `graph.note_pass.started` / `graph.note_redraft.requested` events of Task 3.
- Produces:
  - `web/lib/api.ts`: `ReaderNoteOutcome`, `ReaderNoteRecord`, `ClarificationRecord`, `NoteAcceptedResponse`; optional `notes`, `notes_remaining`, `note_passes`, `clarification` on `ResearchSessionResponse`; `addNote(sessionId, text)`.
  - `web/lib/run-state.ts`: `NoteState {id, text, interpreted, restatement, replaces, fallback, where}`; `RunState.notes`; handlers `session.note.received`, `session.note.interpreted` (records the running row in `where`), `graph.note_pass.started`, `graph.note_redraft.requested`; the `note_pass` route re-arms rows 2–6 and lights `arc: "note_pass"`; `stepLabel("note_pass")` is Researching.
  - `web/lib/notes.ts`: `NOTE_LIMIT`, `NOTE_MAX_CHARS`, `NOTE_PLACEHOLDER`, `NOTE_FIELD_LABEL`, `NOTE_SEND_LABEL`, `NOTES_CLOSED`, `NOTE_SEND_FAILED`, `WHERE`, `ackFor`, `visibleAcks`, `earlierNotesText`, `noteSubjects`, `notePassLine`, `noteRedraftLine`, `OUTCOME_TEXT`, `notesLeft`.
  - `rowBrief(...).acks` and `.earlier` — the running row only.

- [ ] **Step 1: Write the failing tests**

Create `web/test/notes.test.ts`:

```ts
// @vitest-environment node — a plain data/logic test (see run-state.test.ts).
import { describe, expect, it } from "vitest";
import { rowBrief } from "../lib/briefs";
import { NOTE_LIMIT, OUTCOME_TEXT, WHERE, ackFor, earlierNotesText, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
import { applyEvent, marksFor, newRunState, stepLabel, type NodeId, type RunEvent, type RunState } from "../lib/run-state";

const ev = (type: string, metadata: Record<string, unknown> = {}): RunEvent => ({ type, metadata });
const received = (id: string, text: string) => ev("session.note.received", { note_id: id, text });
const interpreted = (id: string, restatement: string, extra: Record<string, unknown> = {}) =>
  ev("session.note.interpreted", { note_id: id, restatement, kinds: ["emphasis"], replaces: null, fallback: false, ...extra });
const started = (node: string, iteration = 0) => ev("graph.node.started", { node, iteration });
const completed = (node: string, iteration = 0) => ev("graph.node.completed", { node, iteration, event_count: 1, error_count: 0 });
function play(events: RunEvent[]): RunState {
  const run = newRunState(2);
  for (const e of events) applyEvent(run, e);
  return run;
}
/* A run that reached Reviewing: planner → … → report_writer done, the reviewer running. */
const toReviewing: RunEvent[] = [
  ev("graph.session.started", { max_extra_passes: 1 }),
  started("planner"), ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [{ coverage_id: "topic-01", title: "Grid storage costs" }] }), completed("planner"),
  started("researcher"), completed("researcher"), started("source_evaluator"), completed("source_evaluator"),
  started("evidence_verifier"), completed("evidence_verifier"), started("report_writer"), completed("report_writer"), started("report_reviewer"),
];

describe("lib/notes — the note line's copy and each note's acknowledgement (live-briefs spec §4.7)", () => {
  it("names where the note takes effect, by the step that was running when it was read", () => {
    expect(WHERE).toEqual({
      planner: ", shaping the plan",
      researcher: ", from each topic's next search",
      source_evaluator: ", in how sources are rated and in the report",
      evidence_verifier: ", in the report",
      report_writer: ", in this draft",
      report_reviewer: "; the review will check it",
    });
    expect(NOTE_LIMIT).toBe(10);
  });

  it("acknowledges a note as received, then as read, as replacing an earlier one, or as passed on as written", () => {
    const run = play([
      started("planner"), completed("planner"), started("researcher"),
      received("n1", "More on fire safety please"), received("n2", "Actually, only the US"), received("n3", "skip costs"),
    ]);
    expect(ackFor(run.notes[0], run.notes)).toEqual({ key: "n1", lead: "Reading your note…", said: null, rest: "" });
    applyEvent(run, interpreted("n1", "more weight on fire-safety standards"));
    applyEvent(run, interpreted("n2", "only the United States", { replaces: "n1" }));
    applyEvent(run, interpreted("n3", "skip costs", { fallback: true }));
    expect(run.notes.map((n) => n.where)).toEqual(["researcher", "researcher", "researcher"]);
    expect(ackFor(run.notes[0], run.notes)).toEqual({ key: "n1", lead: "Got it — ", said: "more weight on fire-safety standards", rest: ", from each topic's next search" });
    expect(ackFor(run.notes[1], run.notes)).toEqual({
      key: "n2", lead: "Got it — ", said: "only the United States",
      rest: ", from each topic's next search, replacing your earlier note about more weight on fire-safety standards",
    });
    expect(ackFor(run.notes[2], run.notes)).toEqual({ key: "n3", lead: "Got it — passed on as you wrote it", said: null, rest: "" });
  });

  it("shows the latest two, oldest first, and counts the earlier ones", () => {
    const run = play([started("planner"), ...[1, 2, 3, 4].map((k) => received("n" + k, "note " + k))]);
    expect(visibleAcks(run.notes.slice(0, 2)).earlier).toBe(0);
    const shown = visibleAcks(run.notes);
    expect(shown.acks.map((a) => a.key)).toEqual(["n3", "n4"]);
    expect(shown.earlier).toBe(2);
    expect(earlierNotesText(1)).toBe("and 1 earlier note");
    expect(earlierNotesText(2)).toBe("and 2 earlier notes");
  });

  it("words the loops' first lines, the report's outcomes and the notes left", () => {
    const run = play([started("planner"), received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b"), interpreted("n2", "recycling")]);
    expect(notePassLine(["n1"], run.notes)).toBe("Researching your note: fire safety");
    expect(notePassLine(["n1", "n2"], run.notes)).toBe("Researching your notes: fire safety; recycling");
    expect(noteRedraftLine(["n2"], run.notes)).toBe("Rewriting for your note: recycling");
    expect(OUTCOME_TEXT).toEqual({
      covered: "covered", not_found: "couldn't find evidence", not_addressed: "not addressed in the report",
      pending: "not checked", replaced: "replaced by a later note",
    });
    expect([notesLeft(undefined, 0), notesLeft(10, 3), notesLeft(7, 1), notesLeft(4, 9), notesLeft(0, 0)]).toEqual([10, 7, 7, 1, 0]);
  });
});

describe("run-state — the note events and the note routes (live-briefs spec §4.6-§4.7)", () => {
  it("counts a replayed note once, drops one with no id, and moves no row", () => {
    const run = play([started("planner"), received("n1", "x"), received("n1", "x"), received("", "y"), ev("session.note.interpreted", { restatement: "z" })]);
    expect(run.notes.map((n) => n.id)).toEqual(["n1"]);
    expect(run.active).toBe("planner");
    expect(run.marks).toEqual({});
  });

  it("a note pass re-arms Researching onward, draws the note arc and lists only the notes' sub-topics", () => {
    const run = play([...toReviewing, received("n1", "Fire safety"), interpreted("n1", "more weight on fire-safety standards"),
      ev("graph.report.reviewed", { mean_score: 0.9 }),
      ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0, missing_required_target_ids: [] })]);
    expect(run.active).toBe("researcher");
    expect(run.arc).toBe("note_pass");
    expect(run.loop).toBe("flowing");
    expect(run.marks).toEqual({ planner: "done" });
    expect(run.topics).toEqual([]);
    applyEvent(run, completed("report_reviewer"));
    expect(run.marks).toEqual({ planner: "done" });
    applyEvent(run, started("note_pass"));
    applyEvent(run, ev("graph.note_pass.started", { iteration: 0, note_passes: 1, note_ids: ["n1"], targets: ["note-n1-target-01"] }));
    applyEvent(run, completed("note_pass"));
    expect(run.loop).toBe("settled");
    expect(run.pass).toBe(1);
    expect(run.reopen.researcher).toEqual({ kind: "note_pass", text: "Researching your note: more weight on fire-safety standards" });
    expect(run.topics).toEqual([{ coverageId: "note-n1", title: "Your note: more weight on fire-safety standards", state: "waiting", findings: null }]);
    expect(run.marks).toEqual({ planner: "done" });
    expect(stepLabel("note_pass")).toBe("Researching");
    applyEvent(run, started("researcher"));
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "note-n1" }));
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "note-n1", findings_retained: 2, successful_reads: 3 }));
    expect(run.topics.map((t) => [t.coverageId, t.state, t.findings])).toEqual([["note-n1", "done", 2]]);
    expect(marksFor(run, run.active).researcher).toBe("active");
  });

  it("a note redraft re-arms Writing onward with its own first line and the review's outcome", () => {
    const run = play([...toReviewing, received("n1", "US only"), interpreted("n1", "only the United States"),
      ev("graph.route.decided", { destination: "redraft", reason: "note_redraft_requested", iteration: 0, missing_required_target_ids: [] })]);
    expect(run.active).toBe("report_writer");
    expect(run.arc).toBe("redraft");
    expect(run.outcomes.report_reviewer).toBe("Sent back to the writer for your note");
    applyEvent(run, completed("report_reviewer"));
    applyEvent(run, started("writer_redraft"));
    applyEvent(run, ev("graph.note_redraft.requested", { iteration: 0, note_ids: ["n1"] }));
    applyEvent(run, completed("writer_redraft"));
    expect(run.reopen.report_writer).toEqual({ kind: "note_redraft", text: "Rewriting for your note: only the United States" });
    expect(run.loop).toBe("settled");
    expect(run.marks.report_writer).toBeUndefined();
  });

  it("the review's outcome names the note pass", () => {
    const run = play([...toReviewing, ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0 })]);
    expect(run.outcomes.report_reviewer).toBe("Sent back to research your note");
    applyEvent(run, ev("graph.note_pass.started", { note_ids: ["n1", "n2"], targets: [] }));
    expect(run.outcomes.report_reviewer).toBe("Sent back to research 2 of your notes");
  });

  it("is burst-safe: the same events paint the same notes in one go or one at a time", () => {
    const events = [...toReviewing, received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b")];
    const oneByOne = newRunState(2);
    const snaps = events.map((e) => { applyEvent(oneByOne, e); return structuredClone(oneByOne); });
    expect(snaps[snaps.length - 1].notes).toEqual(play(events).notes);
  });
});

describe("briefs — the active row acknowledges the notes (live-briefs spec §4.7)", () => {
  it("only the running row carries the acknowledgements, the latest two and the rest counted", () => {
    const run = play([started("planner"), completed("planner"), started("researcher"),
      ...[1, 2, 3].flatMap((k) => [received("n" + k, "note " + k), interpreted("n" + k, "reading " + k)])]);
    const active = rowBrief(run, "researcher", "active");
    expect(active.acks.map((a) => [a.said, a.rest])).toEqual([["reading 2", ", from each topic's next search"], ["reading 3", ", from each topic's next search"]]);
    expect(active.earlier).toBe("and 1 earlier note");
    for (const id of ["planner", "source_evaluator"] as NodeId[]) {
      const other = rowBrief(run, id, id === "planner" ? "done" : "pending");
      expect([other.acks, other.earlier]).toEqual([[], null]);
    }
  });
});
```

`web/test/run-state.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
describe("the port is the prototype's core", () => {
  it("has the seven rows and the nineteen handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.report.redraft_requested", "graph.report.reviewed", "graph.route.decided",
      "graph.session.completed", "graph.session.started", "planner.planning.completed", "report_writer.report.written",
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "source_evaluator.evaluation.completed",
    ]);
```

with

```ts
describe("the port is the prototype's core", () => {
  it("has the seven rows and the twenty-three handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed", "graph.session.started",
      "planner.planning.completed", "report_writer.report.written", "researcher.research.completed",
      "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "source_evaluator.evaluation.completed",
    ]);
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
cd web && npx vitest run test/notes.test.ts test/run-state.test.ts 2>&1 | grep -E "FAIL|Test Files|Tests  |Error:" | head -8
```

Expected: `FAIL  test/notes.test.ts` with `Error: Cannot find module '../lib/notes'`; `FAIL  test/run-state.test.ts > the port is the prototype's core > has the seven rows and the twenty-three handlers` with `AssertionError: expected [ …(19) ] to deeply equal [ …(23) ]`; then `Test Files  2 failed (2)` and `Tests  1 failed | 28 passed (29)` (`notes.test.ts` cannot load, so its 10 tests are not counted).

- [ ] **Step 3: Implement the model**

**`web/lib/api.ts`**: 2 edits, in file order.

`web/lib/api.ts` — replace

```ts
  coverage: CoverageProgress | null; evidence_counts: EvidenceCounts | null;
}
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
```

with

```ts
  coverage: CoverageProgress | null; evidence_counts: EvidenceCounts | null;
  /* live-briefs spec §4.6: the reader's notes, how many more the run takes (D11a), the note passes
     it bought and the one-time check it asked. Optional because a response recorded before notes
     existed (the replay captures under test/fixtures) carries none of them. */
  notes?: ReaderNoteRecord[]; notes_remaining?: number; note_passes?: number; clarification?: ClarificationRecord | null;
}
/* One accepted note: as the reader wrote it, the run's reading once interpreted, and what the
   finished run concluded ("pending" while it runs, or when no review judged it; "not_addressed"
   when the report still does not follow it after its one redraft). */
export type ReaderNoteOutcome = "covered" | "not_found" | "not_addressed" | "pending" | "replaced";
export interface ReaderNoteRecord { note_id: string; text: string; restatement: string | null; outcome: ReaderNoteOutcome }
export interface ClarificationRecord {
  questions: { id: string; dimension: string; text: string; short: string; options: string[]; best_guess: string }[];
  answers: { question_id: string; value: string; source: "chosen" | "typed" | "best_guess" }[];
}
/* POST /research/{id}/notes answers 202 with the note's id; its reading follows on the stream. */
export interface NoteAcceptedResponse { note_id: string; status: "received" }
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
```

`web/lib/api.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
  const r = await request(`/api/research/${id(sessionId)}/answers`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
```

with

```ts
  const r = await request(`/api/research/${id(sessionId)}/answers`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
/* One reader note, posted once (live-briefs spec §4.6-§4.7): 409 notes_closed once publishing has
   begun, 409 note_limit_reached past the tenth note. */
export async function addNote(sessionId: string, text: string): Promise<ApiResult<NoteAcceptedResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/notes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
  return { data: (await r.json()) as NoteAcceptedResponse, mode: modeOf(r) };
}
```

Create `web/lib/notes.ts`:

```ts
// Reader notes in the web app (live-briefs spec §4.7; D8, D9, D11a; pick 6A,
// docs/design/running-stage-picks/Hitl3.dc.html column A): the note line's copy, each note's
// acknowledgement, the first lines of the loops a note buys, and the report's outcome words.
// Pure — a function of RunState or of the status response only, so a burst and a replay from
// event 1 paint the same (DESIGN.md §5.7).
import type { ReaderNoteOutcome } from "./api";
import type { NodeId, NoteState } from "./run-state";

/* D11a: a run takes at most ten notes. The page never says so: the line only disables. */
export const NOTE_LIMIT = 10;
export const NOTE_MAX_CHARS = 500;
export const NOTE_PLACEHOLDER = "Add a note — something to focus on, leave out or change";
export const NOTE_FIELD_LABEL = "Add a note for this research";
export const NOTE_SEND_LABEL = "Add note";
export const NOTES_CLOSED = "Notes are closed — the report is being published";
/* Any failure but the two 409s (a 5xx, a network error): the text stays, and this caption says so
   until the next edit (this plan's words; the spec names only the two 409s). */
export const NOTE_SEND_FAILED = "Couldn't send — try again";

/* Where a note takes effect, by the step that was active when the run read it (§4.7 table).
   Publishing has none: notes are closed by then. */
export const WHERE: Readonly<Partial<Record<NodeId, string>>> = {
  planner: ", shaping the plan",
  researcher: ", from each topic's next search",
  source_evaluator: ", in how sources are rated and in the report",
  evidence_verifier: ", in the report",
  report_writer: ", in this draft",
  report_reviewer: "; the review will check it",
};

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

/* §4.7: with more than two notes, the brief shows the latest two (oldest first) and how many
   earlier notes it leaves out. */
export function visibleAcks(notes: readonly NoteState[]): { acks: Ack[]; earlier: number } {
  const shown = notes.slice(-2);
  return { acks: shown.map((note) => ackFor(note, notes)), earlier: notes.length - shown.length };
}
export function earlierNotesText(n: number): string {
  return "and " + n + (n === 1 ? " earlier note" : " earlier notes");
}

/* The words a loop's first line uses for the notes it serves: each one's reading, in receipt order. */
export function noteSubjects(ids: readonly string[], notes: readonly NoteState[]): string {
  return ids.map((id) => {
    const note = notes.find((n) => n.id === id);
    return note ? note.restatement ?? note.text : id;
  }).join("; ");
}
export function notePassLine(ids: readonly string[], notes: readonly NoteState[]): string {
  return (ids.length === 1 ? "Researching your note: " : "Researching your notes: ") + noteSubjects(ids, notes);
}
export function noteRedraftLine(ids: readonly string[], notes: readonly NoteState[]): string {
  return (ids.length === 1 ? "Rewriting for your note: " : "Rewriting for your notes: ") + noteSubjects(ids, notes);
}

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

/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
   seen since (a note is counted once it is received, whether or not it has been read yet). */
export function notesLeft(statusRemaining: number | undefined, received: number): number {
  return Math.max(0, Math.min(statusRemaining ?? NOTE_LIMIT, NOTE_LIMIT - received));
}
```

**`web/lib/run-state.ts`**: 12 edits, in file order.

`web/lib/run-state.ts` — replace

```ts
import { fmtScore } from "./format";
```

with

```ts
import { fmtScore } from "./format";
import { noteRedraftLine, notePassLine } from "./notes";
```

`web/lib/run-state.ts` — replace

```ts
export const AGENT_ORDER: readonly NodeId[] = STAGES.map((s) => s.id);
export const ARCS: Record<"extra_pass" | "redraft", { from: NodeId; to: NodeId }> = {
  extra_pass: { from: "report_reviewer", to: "researcher" },
  redraft: { from: "report_reviewer", to: "report_writer" },
};
```

with

```ts
export const AGENT_ORDER: readonly NodeId[] = STAGES.map((s) => s.id);
/* live-briefs spec §4.7: a note pass returns to Researching like the extra pass, drawn with the
   redraft's stroke (--meta), because a note pass is not a warning. */
export const ARCS: Record<"extra_pass" | "redraft" | "note_pass", { from: NodeId; to: NodeId }> = {
  extra_pass: { from: "report_reviewer", to: "researcher" },
  redraft: { from: "report_reviewer", to: "report_writer" },
  note_pass: { from: "report_reviewer", to: "researcher" },
};
```

`web/lib/run-state.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
/* The first line of a row a loop reopened: why it reopened (the old loop tag's content). */
export interface ReopenLine { kind: "extra_pass" | "redraft"; text: string }
/* The one-time check (live-briefs spec §4.4-§4.5): its questions and deadline from
```

with

```ts
/* The first line of a row a loop reopened: why it reopened (the old loop tag's content). */
export interface ReopenLine { kind: "extra_pass" | "redraft" | "note_pass" | "note_redraft"; text: string }
/* One reader note (live-briefs spec §4.6-§4.7): as received, then as the run read it. `where` is the
   row that was active when session.note.interpreted arrived — the step the acknowledgement names. */
export interface NoteState {
  id: string; text: string; interpreted: boolean; restatement: string | null;
  replaces: string | null; fallback: boolean; where: NodeId | null;
}
/* The one-time check (live-briefs spec §4.4-§4.5): its questions and deadline from
```

`web/lib/run-state.ts` — replace

```ts
  pass: number; maxPasses: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
```

with

```ts
  pass: number; maxPasses: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | "note_pass" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
```

`web/lib/run-state.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
  clarify: ClarifyState | null;           /* the one-time check; null while the stream has told none */
}
```

with

```ts
  clarify: ClarifyState | null;           /* the one-time check; null while the stream has told none */
  notes: NoteState[];                     /* the reader's notes, in receipt order */
}
```

`web/lib/run-state.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
    plan: [], topics: [], pagesRead: null, findingsSoFar: null, passFindings: null,
    reopen: {}, outcomes: {}, open: new Set(), clarify: null,
  };
```

with

```ts
    plan: [], topics: [], pagesRead: null, findingsSoFar: null, passFindings: null,
    reopen: {}, outcomes: {}, open: new Set(), clarify: null, notes: [],
  };
```

`web/lib/run-state.ts` — replace

```ts
   The two hops lead back into a row, so they read as that row; anything else is not a row. */
const HOP_ROW: Readonly<Record<string, NodeId>> = { extra_pass: "researcher", writer_redraft: "report_writer" };
export function stepLabel(node: string | null | undefined): string | null {
```

with

```ts
   The two hops lead back into a row, so they read as that row; anything else is not a row. */
const HOP_ROW: Readonly<Record<string, NodeId>> = { extra_pass: "researcher", note_pass: "researcher", writer_redraft: "report_writer" };
export function stepLabel(node: string | null | undefined): string | null {
```

`web/lib/run-state.ts` — replace

```ts
function reviewOutcome(run: RunState, md: Md): string {
  if (md.destination === "extra_pass") {
```

with

```ts
function reviewOutcome(run: RunState, md: Md): string {
  if (md.reason === "note_pass_requested") return "Sent back to research your note";
  if (md.reason === "note_redraft_requested") return "Sent back to the writer for your note";
  if (md.destination === "extra_pass") {
```

`web/lib/run-state.ts` — replace

```ts
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "writer_redraft") return;          /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
```

with

```ts
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "note_pass" || (node as string) === "writer_redraft") return; /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
```

`web/lib/run-state.ts` — replace

```ts
      delete run.reopen.report_writer;
    } else if (md.destination === "redraft") {
```

with

```ts
      delete run.reopen.report_writer;
    } else if (md.destination === "note_pass") {
      /* live-briefs spec §4.6: the note pass re-runs Researching onward, as the extra pass does */
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "note_pass"; run.loop = "flowing";
      run.topics = []; run.pagesRead = null; run.findingsSoFar = null; run.passFindings = null;
      delete run.reopen.report_writer;
    } else if (md.destination === "redraft") {
```

`web/lib/run-state.ts` — replace

```ts
  },
  "graph.session.completed": (run, md) => {
```

with

```ts
  },
  /* live-briefs spec §4.7: the note pass researches only the notes' own sub-topics — "Your note: …" —
     and its first line says which notes it is for. */
  "graph.note_pass.started": (run, md) => {
    const ids: string[] = Array.isArray(md.note_ids) ? md.note_ids.filter(isText) : [];
    run.loop = "settled";
    run.reopen.researcher = { kind: "note_pass", text: notePassLine(ids, run.notes) };
    run.outcomes.report_reviewer = ids.length === 1 ? "Sent back to research your note" : "Sent back to research " + ids.length + " of your notes";
    run.topics = ids.map((id) => {
      const note = run.notes.find((n) => n.id === id);
      return { coverageId: "note-" + id, title: "Your note: " + (note ? note.restatement ?? note.text : id), state: "waiting", findings: null };
    });
    run.pagesRead = null; run.findingsSoFar = null;
  },
  "graph.note_redraft.requested": (run, md) => {
    const ids: string[] = Array.isArray(md.note_ids) ? md.note_ids.filter(isText) : [];
    run.loop = "settled";
    run.reopen.report_writer = { kind: "note_redraft", text: noteRedraftLine(ids, run.notes) };
    run.outcomes.report_reviewer = ids.length === 1 ? "Sent back to the writer for your note" : "Sent back to the writer for " + ids.length + " of your notes";
    const c = run.counters;
    c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
  },
  "graph.session.completed": (run, md) => {
```

`web/lib/run-state.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
    run.clarify = { questions: run.clarify?.questions ?? [], deadlineAt: run.clarify?.deadlineAt ?? "", answered: { answers, reason: typeof md.reason === "string" ? md.reason : "" } };
  },
```

with

```ts
    run.clarify = { questions: run.clarify?.questions ?? [], deadlineAt: run.clarify?.deadlineAt ?? "", answered: { answers, reason: typeof md.reason === "string" ? md.reason : "" } };
  },
  /* live-briefs spec §4.6-§4.7: a note is acknowledged as received at once, then as the run read
     it; neither event moves a row. A replayed note is never counted twice. */
  "session.note.received": (run, md) => {
    if (!isText(md.note_id) || run.notes.some((n) => n.id === md.note_id)) return;
    run.notes.push({ id: md.note_id, text: typeof md.text === "string" ? md.text : "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null });
  },
  "session.note.interpreted": (run, md) => {
    if (!isText(md.note_id)) return;
    let note = run.notes.find((n) => n.id === md.note_id);
    if (!note) { note = { id: md.note_id, text: "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null }; run.notes.push(note); }
    note.interpreted = true;
    note.restatement = isText(md.restatement) ? md.restatement : null;
    note.replaces = isText(md.replaces) ? md.replaces : null;
    note.fallback = md.fallback === true;
    note.where = run.active;
  },
```

**`web/lib/briefs.ts`**: 3 edits, in file order.

`web/lib/briefs.ts` — replace

```ts
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";
```

with

```ts
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
import { earlierNotesText, visibleAcks, type Ack } from "./notes";
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";
```

`web/lib/briefs.ts` — replace

```ts
  titles: string[] | null;         /* Planning's final brief: the sub-topic titles */
}
```

with

```ts
  titles: string[] | null;         /* Planning's final brief: the sub-topic titles */
  acks: Ack[];                     /* the active row only: the latest two notes, acknowledged (§4.7) */
  earlier: string | null;          /* the active row only: "and {n} earlier notes" past two */
}
```

`web/lib/briefs.ts` — replace

```ts
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

with

```ts
  const text: Subtitle = { kind: "text", text: STATIC_META[id] };
  // live-briefs spec §4.7: the reader's notes are acknowledged in the row that is running now.
  const noted = id === run.active ? visibleAcks(run.notes) : { acks: [], earlier: 0 };
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

- [ ] **Step 4: Run the tests and the type check**

```bash
cd web && npm run -s typecheck && npx vitest run test/notes.test.ts test/run-state.test.ts test/briefs.test.ts 2>&1 | grep -E "Test Files|Tests  "
```

Expected: no `typecheck` output; `Test Files  3 passed (3)`, `Tests  46 passed (46)` — Task 1's `36` for the run-state and briefs files, plus the new file's `10`.

- [ ] **Step 5: Run every Vitest file**

```bash
cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  "
```

Expected: `Test Files  26 passed (26)`, `Tests  204 passed (204)` (Task 1's `194` + 10).

- [ ] **Step 6: Commit**

```bash
git add web/lib/api.ts web/lib/notes.ts web/lib/run-state.ts web/lib/briefs.ts web/test/notes.test.ts web/test/run-state.test.ts
git commit -m "feat(web): the reader's notes and the note loops in the run state; the running row's acknowledgements"
```

---

### Task 10: The note line, the acknowledgements and the report's "Your notes" (spec §4.7; D8, D9, D11a, D17; §4.8 rows)

**Files:**
- Create: `web/components/NoteLine.tsx`, `web/test/components/note-line.test.tsx`, `web/test/components/reader-notes.test.tsx`
- Modify: `web/components/BriefSpine.tsx:84` (the ack lines after the reopen line); `web/components/RunningPipeline.tsx:4`, `:40` (the note line, the card's last child); `web/components/SessionScreen.tsx:201` (`notesRemaining`); `web/components/ReportBody.tsx:7`, `:88`, `:117` ("Your notes"); `web/components/ReportStage.tsx:70`, `:96` (the notes and the pass fact); `web/components/ReportRail.tsx:53` (the pass fact); `web/app/globals.css:1366` (the styles, appended after Phase 2's last rule)

**Interfaces:**
- Consumes: Task 9's model, words and `addNote`.
- Produces:
  - `NoteLine({ sessionId, remaining })`: `div#noteLine.note-line` holding `input#noteInput.tx` and `button#noteSend.icon-btn`; Enter or the button sends the trimmed text once; read-only while in flight; cleared on 202; `div#noteLine[data-closed="1"] > p#noteClosed.cap` after a 409 `notes_closed`; the field and button `disabled` when `remaining` is 0 or after a 409 `note_limit_reached`, with nothing added; after any other failure the text stays and `div#noteLine[data-failed="1"]` adds `p#noteFailed.cap[role="status"]` `Couldn't send — try again` under the field until the next edit or send (ambiguity 28).
  - `RunningPipeline(..., notesRemaining?)` renders the line as the card's last child with `notesLeft(notesRemaining, run.notes.length)`.
  - `BriefSpine` renders `p.ln.ack[data-ack]` lines (a `span.d` dot, then the words, the reading in `span.said`), each a polite live region (`aria-live="polite"`, `aria-atomic="true"`; ambiguity 30), after the reopen line of the running row, and one `and {n} earlier notes` line past two, which is not.
  - `ReportBody(..., notes?)` renders `section#readerNotes.reader-notes` (an `h2.eyebrow` and one `li` per note: `span.rn-text`, then `span.cap` outcome) as the report card's first child, before `.prose`, only when there are notes.
  - `ReportStage` and `ReportRail` read `passFact(status.iteration, status.note_passes ?? 0)`.

- [ ] **Step 1: Write the failing tests**

Create `web/test/components/note-line.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NoteLine } from "../../components/NoteLine";
import { NOTES_CLOSED, NOTE_PLACEHOLDER, NOTE_SEND_FAILED } from "../../lib/notes";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const refusal = (code: string) => json(409, { error: { code, message: "Refused.", reason: null, issues: [] } });
const field = () => screen.getByRole<HTMLInputElement>("textbox", { name: "Add a note for this research" });
const send = () => screen.getByRole<HTMLButtonElement>("button", { name: "Add note" });
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
async function settle() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

afterEach(() => { vi.unstubAllGlobals(); });

describe("NoteLine — the quiet line at the foot of the pipeline card (live-briefs spec §4.7, D8, D11a)", () => {
  it("is a borderless field with the spec's placeholder and a neutral icon send, never the purple primary", () => {
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    expect(field().className).toBe("tx");
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(field().placeholder).toBe("Add a note — something to focus on, leave out or change");
    expect(send().className).toBe("icon-btn");
    expect(send().querySelector("svg path")!.getAttribute("d")).toBe("M8 13V3M3.5 7.5 8 3l4.5 4.5");
    expect(send().querySelector(".sr")!.textContent).toBe("Add note");
    expect(container.querySelector(".btn-primary")).toBeNull();
  });

  it("sends the trimmed note once on Enter, reads only while in flight, then clears", async () => {
    let answer: (r: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<NoteLine sessionId="s 1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "  More on fire safety  " } });
    fireEvent.keyDown(field(), { key: "Enter" });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(posts(fetchMock)).toHaveLength(1);
    const [url, init] = posts(fetchMock)[0] as [string, RequestInit];
    expect(url).toBe("/api/research/s%201/notes");
    expect(JSON.parse(init.body as string)).toEqual({ text: "More on fire safety" });
    expect(field().readOnly).toBe(true);
    await act(async () => { answer(json(202, { note_id: "n1", status: "received" })); });
    await settle();
    expect(field().readOnly).toBe(false);
    expect(field().value).toBe("");
  });

  it("sends from the button too, and never sends an empty note", async () => {
    const fetchMock = vi.fn(async () => json(202, { note_id: "n1", status: "received" }));
    vi.stubGlobal("fetch", fetchMock);
    render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "   " } });
    fireEvent.click(send());
    expect(posts(fetchMock)).toHaveLength(0);
    fireEvent.change(field(), { target: { value: "Only the US" } });
    fireEvent.click(send());
    await settle();
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it("gives way to one caption once publishing has begun (409 notes_closed)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => refusal("notes_closed")));
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "too late" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("#noteClosed")!.className).toBe("cap");
    expect(container.querySelector("#noteClosed")!.textContent).toBe(NOTES_CLOSED);
    expect(NOTES_CLOSED).toBe("Notes are closed — the report is being published");
  });

  it("disables the field and the send with no message and the same placeholder: none left, or a 409 note_limit_reached", async () => {
    const { container, unmount } = render(<NoteLine sessionId="s1" remaining={0} />);
    expect([field().disabled, send().disabled]).toEqual([true, true]);
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(container.textContent).toBe("Add note");
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => refusal("note_limit_reached")));
    const again = render(<NoteLine sessionId="s1" remaining={1} />);
    fireEvent.change(field(), { target: { value: "an eleventh" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect([field().disabled, send().disabled]).toEqual([true, true]);
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(again.container.querySelector(".cap")).toBeNull();
  });

  it("keeps the text after any other failure — a 5xx or a network error — and says it couldn't send until the next edit", async () => {
    const fetchMock = vi.fn(async () => json(500, { error: { code: "internal_error", message: "Oops.", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "keep me" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(field().value).toBe("keep me");
    expect([field().readOnly, field().disabled]).toEqual([false, false]);
    const failed = screen.getByRole("status");
    expect([failed.id, failed.className, failed.textContent]).toEqual(["noteFailed", "cap", NOTE_SEND_FAILED]);
    expect(NOTE_SEND_FAILED).toBe("Couldn't send — try again");
    fireEvent.change(field(), { target: { value: "keep me, edited" } });
    expect(container.querySelector("#noteFailed")).toBeNull();
    fetchMock.mockImplementation(async () => { throw new TypeError("Failed to fetch"); });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(field().value).toBe("keep me, edited");
    expect(screen.getByRole("status").textContent).toBe(NOTE_SEND_FAILED);
    expect(container.querySelector("#noteClosed")).toBeNull();
  });
});
```

Create `web/test/components/reader-notes.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BriefSpine } from "../../components/BriefSpine";
import { ReportBody } from "../../components/ReportBody";
import { ReportRail } from "../../components/ReportRail";
import { RunningPipeline } from "../../components/RunningPipeline";
import type { ResearchSessionResponse } from "../../lib/api";
import { applyEvent, marksFor, newRunState, type RunState } from "../../lib/run-state";

function researchingWithNotes(count: number): RunState {
  const run = newRunState(2);
  applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
  applyEvent(run, { type: "graph.node.completed", metadata: { node: "planner" } });
  applyEvent(run, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
  for (let k = 1; k <= count; k++) {
    applyEvent(run, { type: "session.note.received", metadata: { note_id: "n" + k, text: "note " + k } });
    if (k < count) applyEvent(run, { type: "session.note.interpreted", metadata: { note_id: "n" + k, restatement: "reading " + k, kinds: ["emphasis"], replaces: null, fallback: false } });
  }
  return run;
}
const pipeline = (run: RunState, notesRemaining?: number) =>
  render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" onToggleRow={() => {}} notesRemaining={notesRemaining} />);

describe("the running stage's notes (live-briefs spec §4.7)", () => {
  it("acknowledges the notes at the top of the running row's brief: a dot, the reading, where it applies", () => {
    const run = researchingWithNotes(2);
    const { container } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    const brief = container.querySelector('#spine > li[data-stage="researcher"] .ps-brief')!;
    const acks = [...brief.querySelectorAll(":scope > .ln.ack")];
    expect(acks.map((a) => a.textContent)).toEqual(["Got it — reading 1, from each topic's next search", "Reading your note…"]);
    expect(acks[0].querySelector(".said")!.textContent).toBe("reading 1");
    expect(acks[0].querySelector(".d")!.getAttribute("aria-hidden")).toBe("true");
    // Each acknowledgement is announced when it changes, without moving focus (ambiguity 30).
    expect(acks.map((a) => [a.getAttribute("aria-live"), a.getAttribute("aria-atomic")])).toEqual([["polite", "true"], ["polite", "true"]]);
    expect(brief.firstElementChild).toBe(acks[0]);
    expect(container.querySelectorAll(".ack")).toHaveLength(2);
  });

  it("past two notes, shows the latest two and counts the earlier ones", () => {
    const run = researchingWithNotes(4);
    const { container } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    expect([...container.querySelectorAll(".ack")].map((a) => a.textContent)).toEqual([
      "Got it — reading 3, from each topic's next search", "Reading your note…", "and 2 earlier notes",
    ]);
    expect(container.querySelector(".ack:not([data-ack])")!.hasAttribute("aria-live")).toBe(false);
  });

  it("puts the note line last in the pipeline card, and disables it once the stream has seen the tenth note", () => {
    // One pipeline at a time: jsdom resolves an id selector document-wide, so two mounted copies
    // of #noteInput would hide the second.
    const open = pipeline(researchingWithNotes(1), 10);
    expect(open.container.querySelector("#stage-running .card")!.lastElementChild!.id).toBe("noteLine");
    expect(open.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(false);
    open.unmount();
    const full = pipeline(researchingWithNotes(10), 9);
    expect(full.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(true);
    expect(full.container.querySelector<HTMLButtonElement>("#noteSend")!.disabled).toBe(true);
    full.unmount();
    const stale = pipeline(researchingWithNotes(0), 0);
    expect(stale.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(true);
  });
});

const MARKDOWN = "# Q\n\nEvidence as of 2026-09-16 · 1 source\n\n## Bottom line\n\nStorage grew [1].\n\n## Sources\n\n1. Source one\n";
const NOTES = [
  { note_id: "n1", text: "More on fire safety", restatement: "more weight on fire-safety standards", outcome: "covered" as const },
  { note_id: "n2", text: "Recycling too", restatement: "how cells are recycled", outcome: "not_found" as const },
  { note_id: "n3", text: "Actually only the US", restatement: "only the United States", outcome: "pending" as const },
  { note_id: "n4", text: "Leave out pumped hydro", restatement: "leave out pumped hydro", outcome: "not_addressed" as const },
];

describe("the report's Your notes (live-briefs spec §4.7, AC19)", () => {
  it("sits inside the report card above the prose, outside it, with each note and its outcome", () => {
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded={false} onOpenEvidence={() => {}} notes={NOTES} />);
    const card = container.querySelector("article.card.stack")!;
    const block = card.querySelector(":scope > section.reader-notes")!;
    expect(card.firstElementChild).toBe(block);
    expect(block.nextElementSibling!.className).toBe("prose");
    expect(block.closest(".prose")).toBeNull();
    expect(block.querySelector(".card")).toBeNull();
    expect(block.querySelector("h2.eyebrow")!.textContent).toBe("Your notes");
    expect([...block.querySelectorAll("li")].map((li) => [li.querySelector(".rn-text")!.textContent, li.querySelector(".cap")!.textContent])).toEqual([
      ["More on fire safety", "covered"], ["Recycling too", "couldn't find evidence"], ["Actually only the US", "not checked"],
      ["Leave out pumped hydro", "not addressed in the report"],
    ]);
  });

  it("is absent when the reader added no note", () => {
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded={false} onOpenEvidence={() => {}} />);
    expect(container.querySelector(".reader-notes")).toBeNull();
    expect(container.querySelector("article.card.stack")!.firstElementChild!.className).toBe("prose");
  });

  it("names the note passes in the pass fact", () => {
    const status: ResearchSessionResponse = {
      session_id: "s", query: "q", status: "completed", current_agent: null, iteration: 1, started_at: "2026-09-16T14:02:11Z",
      finished_at: "2026-09-16T14:12:00Z", report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null,
      quality_contract_version: null, semantic_review_status: "scored", semantic_review_score: 0.9, duration_seconds: null,
      coverage: null, evidence_counts: null, notes: NOTES, notes_remaining: 6, note_passes: 2, clarification: null,
    };
    const { container } = render(<ReportRail status={status} evidence={null} />);
    expect(container.querySelector("#repFactPass")!.textContent).toBe("Went back once to fill gaps · went back twice for your notes");
  });
});
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
cd web && npx vitest run test/components/note-line.test.tsx test/components/reader-notes.test.tsx 2>&1 | grep -E "FAIL|Test Files|Tests  " | head -8
```

Expected: `FAIL  test/components/note-line.test.tsx` (it cannot load `NoteLine`), then five `FAIL` lines from `reader-notes.test.tsx` — the two acknowledgement tests, the note line's place, the report block and the pass fact — then `Test Files  2 failed (2)` and `Tests  5 failed | 1 passed (6)`. The one that passes is `is absent when the reader added no note`; `note-line.test.tsx`'s 6 tests are not counted.

- [ ] **Step 3: Implement the components and the styles**

Create `web/components/NoteLine.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ApiError, addNote } from "@/lib/api";
import { NOTES_CLOSED, NOTE_FIELD_LABEL, NOTE_MAX_CHARS, NOTE_PLACEHOLDER, NOTE_SEND_FAILED, NOTE_SEND_LABEL } from "@/lib/notes";

/* The note line (live-briefs spec §4.7, D8; pick 6A, docs/design/running-stage-picks/Hitl3.dc.html
   column A): the last element in the pipeline card, under a hairline — a borderless field and a
   neutral icon send, never the purple primary. Enter sends. While the POST is in flight the field is
   read-only. With no note left to take (`remaining` is 0, or a 409 note_limit_reached) the field and
   the button are disabled, with no message and the placeholder unchanged (D11a). Once
   `finalize_report` has started (409 notes_closed) one caption takes the line's place. Any other
   failure (a 5xx, a network error) keeps the text and shows one caption under the field,
   "Couldn't send — try again", until the reader edits the note or sends it again. */
export function NoteLine({ sessionId, remaining }: { sessionId: string; remaining: number }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [closed, setClosed] = useState(false);
  const [full, setFull] = useState(false);
  const [failed, setFailed] = useState(false);
  const disabled = full || remaining <= 0;
  if (closed) return <div className="note-line" id="noteLine" data-closed="1"><p className="cap" id="noteClosed">{NOTES_CLOSED}</p></div>;
  const send = async () => {
    const body = text.trim();
    if (!body || sending || disabled) return;
    setSending(true);
    setFailed(false);
    try {
      await addNote(sessionId, body);
      setText("");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.body.code === "notes_closed") setClosed(true);
      else if (error instanceof ApiError && error.status === 409 && error.body.code === "note_limit_reached") setFull(true);
      else setFailed(true);
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="note-line" id="noteLine" data-failed={failed ? "1" : undefined}>
      <input className="tx" id="noteInput" type="text" aria-label={NOTE_FIELD_LABEL} placeholder={NOTE_PLACEHOLDER} maxLength={NOTE_MAX_CHARS}
        value={text} readOnly={sending} disabled={disabled} autoComplete="off"
        onChange={(event) => { setText(event.target.value); setFailed(false); }}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
      <button type="button" className="icon-btn" id="noteSend" disabled={disabled} onClick={() => void send()}>
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>
        <span className="sr">{NOTE_SEND_LABEL}</span>
      </button>
      {failed ? <p className="cap" id="noteFailed" role="status">{NOTE_SEND_FAILED}</p> : null}
    </div>
  );
}
```

`web/components/BriefSpine.tsx` — replace

```tsx
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    if (brief.sentence) lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{brief.sentence}</p>);
```

with

```tsx
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    // live-briefs spec §4.7 (D9): the reader's notes, acknowledged at the top of the running row's
    // brief — a muted dot, then the run's reading of the note, never the note echoed back. Each line
    // is a polite live region: nothing moves focus, so a screen reader hears the line whole when
    // "Reading your note…" becomes "Got it — …".
    for (const ack of brief.acks) {
      lines.push(
        <p key={`ack-${ack.key}`} className="ln ack" data-ack={ack.key} aria-live="polite" aria-atomic="true" style={lineStyle(n++)}>
          <span className="d" aria-hidden="true" />
          <span>{ack.lead}{ack.said !== null ? <span className="said">{ack.said}</span> : null}{ack.rest}</span>
        </p>,
      );
    }
    if (brief.earlier) lines.push(<p key="ack-earlier" className="ln ack" style={lineStyle(n++)}><span className="d" aria-hidden="true" /><span>{brief.earlier}</span></p>);
    if (brief.sentence) lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{brief.sentence}</p>);
```

**`web/components/RunningPipeline.tsx`**: 2 edits, in file order.

`web/components/RunningPipeline.tsx` — replace

```tsx
import { noteRunningLayout } from "@/lib/handoff";
import { marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { BriefSpine } from "./BriefSpine";

/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone, whose active row is open on its live brief (§4.3). The
   row's accessible name (" (in progress)") and aria-current="step" are the non-colour state signals. */
export function RunningPipeline({ sessionId, run, question, strip, startedAt, onToggleRow }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; onToggleRow(id: NodeId): void }) {
  const [elapsed, setElapsed] = useState(0);
```

with

```tsx
import { noteRunningLayout } from "@/lib/handoff";
import { notesLeft } from "@/lib/notes";
import { marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { BriefSpine } from "./BriefSpine";
import { NoteLine } from "./NoteLine";

/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone, whose active row is open on its live brief (§4.3). The
   row's accessible name (" (in progress)") and aria-current="step" are the non-colour state signals. */
/* live-briefs spec §4.7: the note line is the card's last element; `notesRemaining` is the last
   /status's count, lowered by every note the stream has received since (lib/notes.ts notesLeft). */
export function RunningPipeline({ sessionId, run, question, strip, startedAt, onToggleRow, notesRemaining }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; onToggleRow(id: NodeId): void; notesRemaining?: number }) {
  const [elapsed, setElapsed] = useState(0);
```

`web/components/RunningPipeline.tsx` — replace

```tsx
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} />
        </div>
```

with

```tsx
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} />
          <NoteLine sessionId={sessionId} remaining={notesLeft(notesRemaining, run.notes.length)} />
        </div>
```

`web/components/SessionScreen.tsx` (anchor as written by Phase 2 Task 9) — replace

```tsx
    if (phase !== null) return <ClarifyStage sessionId={sessionId} run={run.current} phase={phase} question={status.query} strip={strip} />;
    return <RunningPipeline sessionId={sessionId} run={run.current} question={status.query} strip={strip} startedAt={status.started_at} onToggleRow={toggleRow} />;
  }
```

with

```tsx
    if (phase !== null) return <ClarifyStage sessionId={sessionId} run={run.current} phase={phase} question={status.query} strip={strip} />;
    return <RunningPipeline sessionId={sessionId} run={run.current} question={status.query} strip={strip} startedAt={status.started_at} onToggleRow={toggleRow} notesRemaining={status.notes_remaining} />;
  }
```

**`web/components/ReportBody.tsx`**: 3 edits, in file order.

`web/components/ReportBody.tsx` — replace

```tsx
import type { Parent, PhrasingContent, Root, RootContent, Text } from "mdast";
```

with

```tsx
import type { Parent, PhrasingContent, Root, RootContent, Text } from "mdast";
import type { ReaderNoteRecord } from "@/lib/api";
import { OUTCOME_TEXT } from "@/lib/notes";
```

`web/components/ReportBody.tsx` — replace

```tsx

interface Props { markdown: string; evidenceLoaded: boolean; onOpenEvidence(): void }

export function ReportBody({ markdown, evidenceLoaded, onOpenEvidence }: Props) {
  const components: Components = {
```

with

```tsx

interface Props { markdown: string; evidenceLoaded: boolean; onOpenEvidence(): void; notes?: readonly ReaderNoteRecord[] }

export function ReportBody({ markdown, evidenceLoaded, onOpenEvidence, notes = [] }: Props) {
  const components: Components = {
```

`web/components/ReportBody.tsx` — replace

```tsx
    <article className="card stack" style={{ gap: "var(--space-5)" }}>
      <div className="prose">
```

with

```tsx
    <article className="card stack" style={{ gap: "var(--space-5)" }}>
      {/* live-briefs spec §4.7: "Your notes", inside the report card and above the prose — not a card
          of its own, and outside .prose — with each note's outcome as a caption. */}
      {notes.length > 0 ? (
        <section className="reader-notes" id="readerNotes" aria-labelledby="readerNotesH">
          <h2 className="eyebrow" id="readerNotesH">Your notes</h2>
          <ul className="rn-list">
            {notes.map((note) => (
              <li key={note.note_id} data-outcome={note.outcome}><span className="rn-text">{note.text}</span> <span className="cap">{OUTCOME_TEXT[note.outcome]}</span></li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="prose">
```

**`web/components/ReportStage.tsx`**: 2 edits, in file order.

`web/components/ReportStage.tsx` — replace

```tsx
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passFact(status.iteration)}`;
  const evidenceLoaded = evidence.kind === "ready";
```

with

```tsx
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passFact(status.iteration, status.note_passes ?? 0)}`;
  const evidenceLoaded = evidence.kind === "ready";
```

`web/components/ReportStage.tsx` — replace

```tsx
          <div className="stack" style={{ gap: "var(--space-6)" }}>
            {report.kind === "ready" ? <ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} />
              : <article className="card"><p className="avail">{report.kind === "unavailable" ? "Not published" : "loading report"}</p></article>}
```

with

```tsx
          <div className="stack" style={{ gap: "var(--space-6)" }}>
            {report.kind === "ready" ? <ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} notes={status.notes ?? []} />
              : <article className="card"><p className="avail">{report.kind === "unavailable" ? "Not published" : "loading report"}</p></article>}
```

`web/components/ReportRail.tsx` — replace

```tsx
          <dt>status</dt><dd id="repFactStatus">{status.status}</dd>
          <dt>pass</dt><dd id="repFactPass">{passFact(status.iteration)}</dd>
          <dt>started_at</dt><dd id="repFactStarted">{status.started_at}</dd>
```

with

```tsx
          <dt>status</dt><dd id="repFactStatus">{status.status}</dd>
          <dt>pass</dt><dd id="repFactPass">{passFact(status.iteration, status.note_passes ?? 0)}</dd>
          <dt>started_at</dt><dd id="repFactStarted">{status.started_at}</dd>
```

`web/app/globals.css` (anchor as written by Phase 2 Task 8) — replace

```css
  #clarifyCard .ck-q,#clarifyCard .ck-summary{animation-duration:160ms !important}
}
```

with

```css
  #clarifyCard .ck-q,#clarifyCard .ck-summary{animation-duration:160ms !important}
}

/* ═══ 2026-09-29: reader notes (live-briefs spec §4.7; pick 6A, docs/design/running-stage-picks/Hitl3.dc.html column A) ═══ */
/* The note line: the pipeline card's last element, under a hairline — a borderless field that reads
   as text (.tx, above) and a neutral icon send (D8). No purple at rest (D17): only the field's focus
   ring, while it has focus. Disabled after the tenth note with nothing added (D11a). */
.note-line{display:flex;align-items:center;gap:var(--space-2);padding-top:var(--space-3);border-top:1px solid var(--border-soft)}
.note-line .tx{min-height:36px;padding:6px var(--space-2);margin-left:calc(-1 * var(--space-2));text-overflow:ellipsis}
.note-line .tx[readonly]{color:var(--muted)}
.note-line .tx:disabled{cursor:not-allowed}
.note-line .tx:disabled:hover{background:transparent}
.note-line .icon-btn svg{width:14px;height:14px}
.note-line .icon-btn:disabled{color:var(--border);cursor:not-allowed}
.note-line .icon-btn:disabled:hover{background:transparent;border-color:transparent}
.note-line .cap{padding:6px 0}
/* A send that failed for any reason but the two 409s: the text stays, and one caption under the
   field says so until the next edit; the line wraps only then. */
.note-line[data-failed="1"]{flex-wrap:wrap}
.note-line #noteFailed{flex-basis:100%;padding:0}
/* Each acknowledgement (D9): a muted dot, then the run's reading of the note in --fg and the rest
   muted. A new one rises in from its starting style over 200ms: at once in a row that is already
   open, and in its place in the stagger in the row a hand-off is opening. Under reduced motion the
   brief's own rule keeps the fade and drops the travel. */
.spine-lg.briefs .ack{display:grid;grid-template-columns:14px minmax(0,1fr);gap:var(--space-3);align-items:baseline;font-size:var(--text-sm);line-height:1.5;color:var(--muted);overflow-wrap:anywhere}
.spine-lg.briefs .ack .d{width:6px;height:6px;border-radius:50%;background:var(--muted);justify-self:center;transform:translateY(-2px)}
.spine-lg.briefs .ack .said{color:var(--fg)}
.spine-lg.briefs > li[data-open="1"] .ack{--dur-c:200ms}
.spine-lg.briefs > li[data-open="1"]:not([data-handoff]) .ack{--d-c:0ms}
@starting-style{.spine-lg.briefs > li[data-open="1"] .ack{opacity:0;transform:translateY(4px)}}
/* A note pass returns to Researching drawn with the redraft's stroke: a note pass is not a warning
   (DESIGN.md §3.4, colour means status). */
.spine-wrap[data-arc="note_pass"] .loop-layer .loop-base,
.spine-wrap[data-arc="note_pass"] .loop-layer .loop-flow{stroke:var(--meta)}
.spine-wrap[data-arc="note_pass"] .loop-layer .loop-head{fill:var(--meta)}
/* The report's "Your notes": inside the report card and above the prose, no box of its own — an
   eyebrow, one line per note with its outcome as a caption, and a hairline below. */
.reader-notes{display:flex;flex-direction:column;gap:var(--space-2);padding-bottom:var(--space-4);border-bottom:1px solid var(--border-soft);max-width:var(--reading-max)}
.reader-notes h2{margin:0;font-weight:400}
.reader-notes .rn-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--space-1)}
.reader-notes li{font-size:var(--text-sm);line-height:1.5;color:var(--fg);overflow-wrap:anywhere}
.reader-notes li .cap{white-space:nowrap}
```

- [ ] **Step 4: Run the tests, the type check and the CSS check**

```bash
cd web && npm run -s typecheck && npx vitest run test/components 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
```

Expected: no `typecheck` output; `Test Files  18 passed (18)`, `Tests  97 passed (97)` — Task 1's `85`, plus the two new files' `12`; then `OK`.

- [ ] **Step 5: Run every Vitest file**

```bash
cd web && npx vitest run 2>&1 | grep -E "Test Files|Tests  "
```

Expected: `Test Files  28 passed (28)`, `Tests  216 passed (216)` (Task 1's `194` + 22).

- [ ] **Step 6: Commit**

```bash
git add web/components/NoteLine.tsx web/components/BriefSpine.tsx web/components/RunningPipeline.tsx web/components/SessionScreen.tsx web/components/ReportBody.tsx web/components/ReportStage.tsx web/components/ReportRail.tsx web/app/globals.css web/test/components/note-line.test.tsx web/test/components/reader-notes.test.tsx
git commit -m "feat(web): the note line at the pipeline card's foot, acknowledgements in the running row, and the report's Your notes"
```

---

### Task 11: The notes end to end on the replay server, and the captures (spec §6 Playwright and captures; AC15, AC19, AC20; §4.8 reduced motion and phone rows)

**Files:**
- Create: `web/e2e/notes.spec.ts`
- Modify: `web/e2e/support.ts:79` (the motion recorder names an ack line `ack`), `:128` (`installNoteRecorder`, `noteRecord`, after Phase 2's clarify recorder); `web/e2e/visual.spec.ts:3`, `:65` (the `11-note-ack` and `12-report-notes` captures, after Phase 2's `10-clarify`)

**Interfaces:**
- Consumes: Tasks 8–10 (the route, the page).
- Produces: six chromium specs and two visual tests; the captures `11-note-ack(-phone)` and `12-report-notes(-phone)`.

Replay's engine finishes before the paced stream shows Researching (O2), so these specs prove the note's flow through the API and the page — received, read, acknowledged, counted, refused, reported — and the report's outcomes read `not checked`. The engine's use of notes was proven in Tasks 2–6.

- [ ] **Step 1: Write the specs and the recorder**

`installNoteRecorder` wraps the page's `fetch` to stamp the moment each note POST is sent (AC15 is timed from it: see the coverage table) and polls every frame for the first fully shown acknowledgement and for the closed caption: the caption can be on screen for well under a second before the report replaces the running stage. The captures blur the field and scroll to the top first, so they show the line at rest.

**`web/e2e/support.ts`**: 2 edits, in file order.

`web/e2e/support.ts` — replace

```ts
      if (el.classList.contains("ps-x")) return "ps-x";
      if (el.classList.contains("ln")) return "line";
```

with

```ts
      if (el.classList.contains("ps-x")) return "ps-x";
      if (el.classList.contains("ack")) return "ack";
      if (el.classList.contains("ln")) return "line";
```

`web/e2e/support.ts` (anchor as written by Phase 2 Task 9) — replace

```ts
export const clarifyRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drClarify: ClarifyRecord }).__drClarify);
```

with

```ts
export const clarifyRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drClarify: ClarifyRecord }).__drClarify);

/* live-briefs spec §4.7 (AC15, AC19): when each note POST was sent, when each acknowledgement was
   first fully shown, and every caption the note line showed — recorded from before the page's
   scripts run, because the "notes are closed" caption can be on screen for well under a second
   before the report stage replaces the running one. Times are performance.now() in the page.
   AC15 is timed from the send: `session.note.interpreted` is published only after the server has
   the note, so it cannot reach the page before the POST left it, and a bound measured from the send
   holds for the event too (the POST's 202 would not do: it and the event's frame reach the page
   over separate connections, and nothing orders them). */
export interface NoteRecord { sent: number[]; acks: { text: string; at: number }[]; closed: string[] }
export async function installNoteRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drNotes: NoteRecord };
    w.__drNotes = { sent: [], acks: [], closed: [] };
    const original = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (init?.method === "POST" && /\/notes$/.test(url)) w.__drNotes.sent.push(performance.now());
      return original(input, init);
    };
    const tick = () => {
      for (const el of document.querySelectorAll<HTMLElement>(".ack")) {
        const text = el.textContent ?? "";
        if (text.startsWith("Got it") && Number(getComputedStyle(el).opacity) >= 0.99 && !w.__drNotes.acks.some((a) => a.text === text)) w.__drNotes.acks.push({ text, at: performance.now() });
      }
      const closed = document.getElementById("noteClosed")?.textContent ?? "";
      if (closed && !w.__drNotes.closed.includes(closed)) w.__drNotes.closed.push(closed);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
export const noteRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drNotes: NoteRecord }).__drNotes);
```

Create `web/e2e/notes.spec.ts`:

```ts
// live-briefs spec §4.6-§4.8 (D8, D9, D11a; AC15, AC19, AC20): reader notes on the replay server.
// Replay's interpreter restates a note as written, as an emphasis. Replay runs the graph at full
// speed and paces only the stream, so a note the page sends reaches the run's board after the
// engine has finished: these specs prove the note's flow through the API and the page; the engine's
// use of a note is proven offline by pytest (tests/test_graph/test_reader_notes_replay.py).
import { expect, test, type Page } from "@playwright/test";
import { API, installMotionRecorder, installNoteRecorder, motion, noteRecord, submit, waitTerminal } from "./support";

const QUESTION = "What is the current state of grid-scale battery storage?";
const PLACEHOLDER = "Add a note — something to focus on, leave out or change";
const field = (page: Page) => page.getByLabel("Add a note for this research");
/* Researching is the running row and its hand-off from Planning has settled. */
const researching = (page: Page) => page.locator('#spine li[data-stage="researcher"][data-state="active"]:not([data-handoff])').waitFor({ timeout: 15_000 });
async function note(page: Page, text: string) {
  await field(page).fill(text);
  await field(page).press("Enter");
  await expect(field(page)).toHaveValue("");
}

test.beforeEach(async ({ context }) => {
  // The longest case: its paced stream keeps the run going long enough to add notes to it.
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
});

test("a note sent while Researching is received, read and acknowledged in the running row within 1 s (AC15)", async ({ page, request }) => {
  await installNoteRecorder(page);
  await installMotionRecorder(page);
  const id = await submit(page, QUESTION);
  await researching(page);
  const card = page.locator("#stage-running .card");
  await expect(card.locator("> *").last()).toHaveId("noteLine");
  await expect(card.locator(".btn-primary")).toHaveCount(0);
  await note(page, "More on fire-safety standards");
  const ack = page.locator('#spine li[data-state="active"] .ack', { hasText: "Got it — More on fire-safety standards, from each topic's next search" });
  await expect(ack).toBeVisible();
  await expect(ack.locator(".said")).toHaveText("More on fire-safety standards");
  await expect.poll(async () => (await noteRecord(page)).acks.length).toBe(1);
  const record = await noteRecord(page);
  // Timed from the send, which the interpreted event can only follow: an upper bound on AC15's measure.
  expect(record.acks[0].at - record.sent[0]).toBeLessThan(1000);
  // It rises in over 200 ms, with no wait, in the row that is already open.
  const rise = (await motion(page)).filter((m) => m.part === "ack");
  expect(rise.map((m) => [m.prop, m.delay, m.duration]).sort()).toEqual([["opacity", 0, 200], ["transform", 0, 200]]);
  await waitTerminal(request, id);
  const stream = await (await request.get(`${API}/research/${id}/stream`)).text();
  const received = stream.indexOf("event: session.note.received"), read = stream.indexOf("event: session.note.interpreted");
  expect(received).toBeGreaterThan(-1);
  expect(read).toBeGreaterThan(received);
  expect(stream).toContain('"restatement":"More on fire-safety standards","kinds":["emphasis"],"replaces":null,"fallback":false');
});

test("under reduced motion an acknowledgement fades in place, with no travel (§4.8)", async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installMotionRecorder(page);
  const id = await submit(page, QUESTION);
  await researching(page);
  await note(page, "Leave out pumped hydro");
  await expect(page.locator(".ack", { hasText: "Got it — Leave out pumped hydro" })).toBeVisible();
  const fade = (await motion(page)).filter((m) => m.part === "ack");
  expect(fade.map((m) => [m.prop, m.delay, m.duration])).toEqual([["opacity", 0, 160]]);
  await waitTerminal(request, id);
});

test("after the tenth note the line is disabled with nothing added, and an eleventh is refused (AC20, D11a)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  for (let k = 1; k <= 9; k++) expect((await request.post(`${API}/research/${id}/notes`, { data: { text: `note ${k}` } })).status()).toBe(202);
  await note(page, "the tenth note");
  await expect(field(page)).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add note" })).toBeDisabled();
  await expect(field(page)).toHaveAttribute("placeholder", PLACEHOLDER);
  await expect(page.locator("#noteLine")).toHaveText("Add note");
  const eleventh = await request.post(`${API}/research/${id}/notes`, { data: { text: "one too many" } });
  expect(eleventh.status()).toBe(409);
  expect((await eleventh.json()).error.code).toBe("note_limit_reached");
  await expect(page.locator("#spine .ack")).toHaveText([/^Got it — note 9/, /^Got it — the tenth note/, "and 8 earlier notes"]);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).notes_remaining).toBe(0);
  await waitTerminal(request, id);
});

test("once publishing has begun a note is refused and one caption takes the line's place (AC19, §4.8)", async ({ page, request }) => {
  await installNoteRecorder(page);
  const id = await submit(page, QUESTION);
  await page.locator('#spine li[data-stage="finalize_report"][data-state="active"]').waitFor({ timeout: 60_000 });
  await field(page).fill("Is it too late?");
  await field(page).press("Enter");
  await expect.poll(async () => (await noteRecord(page)).closed).toEqual(["Notes are closed — the report is being published"]);
  await waitTerminal(request, id);
  const after = await request.post(`${API}/research/${id}/notes`, { data: { text: "after the end" } });
  expect(after.status()).toBe(409);
  expect((await after.json()).error.code).toBe("notes_closed");
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).notes).toEqual([]);
});

test("the report lists every note above the prose, each with its outcome (AC19)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await note(page, "More on fire-safety standards");
  expect((await request.post(`${API}/research/${id}/notes`, { data: { text: "Only the United States" } })).status()).toBe(202);
  await waitTerminal(request, id);
  const card = page.locator("#stage-report article.card.stack");
  await expect(card.locator("> section.reader-notes")).toBeVisible({ timeout: 20_000 });
  expect(await card.evaluate((el) => [...el.children].map((c) => c.className))).toEqual(["reader-notes", "prose"]);
  await expect(card.locator(".reader-notes h2")).toHaveText("Your notes");
  // Replay's engine finished before these notes arrived, so no review judged them.
  await expect(card.locator(".reader-notes li")).toHaveText(["More on fire-safety standards not checked", "Only the United States not checked"]);
  const status = await (await request.get(`${API}/research/${id}/status`)).json();
  expect(status.notes).toEqual([
    { note_id: "n1", text: "More on fire-safety standards", restatement: "More on fire-safety standards", outcome: "pending" },
    { note_id: "n2", text: "Only the United States", restatement: "Only the United States", outcome: "pending" },
  ]);
  expect([status.notes_remaining, status.note_passes]).toEqual([8, 0]);
});

test.describe("at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the note line spans the card, its send is a 44 px target, and nothing scrolls sideways (§4.8)", async ({ page, request }) => {
    const id = await submit(page, QUESTION);
    await researching(page);
    await note(page, "More on fire-safety standards");
    await expect(page.locator(".ack", { hasText: "Got it" })).toBeVisible();
    const geometry = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>("#stage-running .card")!;
      const line = document.getElementById("noteLine")!.getBoundingClientRect(), send = document.getElementById("noteSend")!.getBoundingClientRect();
      const box = card.getBoundingClientRect(), cs = getComputedStyle(card);
      const inner = box.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
      return { line: line.width, inner, send: [send.width, send.height], scroll: document.scrollingElement!.scrollWidth - window.innerWidth };
    });
    expect(Math.abs(geometry.line - geometry.inner)).toBeLessThanOrEqual(1);
    expect(geometry.send).toEqual([44, 44]);
    expect(geometry.scroll).toBeLessThanOrEqual(0);
    await waitTerminal(request, id);
  });
});
```

**`web/e2e/visual.spec.ts`**: 2 edits, in file order.

`web/e2e/visual.spec.ts` — replace

```ts
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";
```

with

```ts
import { expect, test, type Page } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";
```

`web/e2e/visual.spec.ts` (anchor as written by Phase 2 Task 9) — replace

```ts
      await waitTerminal(request, id);
    });
  });
```

with

```ts
      await waitTerminal(request, id);
    });

    // live-briefs spec §6: a note acknowledged at the top of the running Researching row, with the
    // note line at the card's foot (pick 6A); then the report's "Your notes" above the prose (§4.7).
    test(`11-note-ack${suffix}, 12-report-notes${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator('#spine li[data-stage="researcher"][data-state="active"]:not([data-handoff])').waitFor({ timeout: 15_000 });
      await page.getByLabel("Add a note for this research").fill("More on fire-safety standards");
      await page.getByLabel("Add a note for this research").press("Enter");
      await expect(page.locator("#spine .ack", { hasText: "Got it" })).toBeVisible();
      // At rest, from the top: typing scrolled the field into view, and its focus ring is not the resting look.
      await page.locator("#noteInput").blur();
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `11-note-ack${suffix}`);
      const second = await request.post(`${API}/research/${id}/notes`, { data: { text: "Only the United States" } });
      expect(second.status()).toBe(202);
      await waitTerminal(request, id);
      await expect(page.locator("#stage-report .reader-notes")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `12-report-notes${suffix}`);
    });
  });
```

- [ ] **Step 2: Run the notes specs, three times over**

```bash
for p in 8010 3010 3011; do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "port $p is in use"; done
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s build >/dev/null && npx playwright test e2e/notes.spec.ts --project=chromium --repeat-each=3 2>&1 | tail -3
```

Expected: no port line, then `18 passed` — each of the six specs three times; the AC15 timing and the closed-caption spec are the two that race the paced stream, and neither flakes.

- [ ] **Step 3: Run the whole chromium project**

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s test:e2e 2>&1 | tail -3
```

Expected: `61 passed` — Task 1's `55` plus the six notes specs. Every existing spec passes unchanged: the note line is one more child of the pipeline card, after the spine.

- [ ] **Step 4: Take the captures**

```bash
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && VISUAL_CHECKPOINT=P3-T11 npx playwright test --project=visual 2>&1 | tail -3 && ls visual/P3-T11 | wc -l
```

Expected: `12 passed`, then `20`.

- [ ] **Step 5: Review the captures, full height, with the Read tool**

**This is a first-time visual review, not a confirmation.** Planning produced these captures but never read them (see "Evidence" at the top; review round 1, P3-9), so no image of `11-note-ack` or `12-report-notes` has been checked by anyone yet: read every image and every slice as new, and hold each to every check below.

Read each of `web/visual/P3-T11/11-note-ack.png`, `11-note-ack-phone.png`, `12-report-notes.png` and `12-report-notes-phone.png` whole. The phone report capture is about 5,650 px tall; cut it into 1,400 px slices first and read every slice:

```bash
PY="$PWD/.venv/bin/python"
"$PY" - <<'EOF'
from PIL import Image
im = Image.open("web/visual/P3-T11/12-report-notes-phone.png")
for i, top in enumerate(range(0, im.size[1], 1400)):
    im.crop((0, top, im.size[0], min(top + 1400, im.size[1]))).save(f"web/visual/P3-T11/12-report-notes-phone-{i}.png")
print(im.size)
EOF
```

Expected: `(390, 5654)` or near it (the report's own length), and one slice per 1,400 px.

Check, against pick 6A (`docs/design/running-stage-picks/Hitl3.dc.html:18-49` column A, `theme.css:318-322`) and the spec, where the spec wins:
- `11-note-ack`: the topbar at the top, not mid-page. Researching open; its brief's first line is the ack — a muted dot, `Got it —` in `--muted`, `More on fire-safety standards` in `--fg`, `, from each topic's next search` in `--muted` — above the topic checklist. The note line is the card's last row under a `--border-soft` hairline: the placeholder `Add a note — something to focus on, leave out or change` in `--meta`, the up-arrow send in `--muted` at the right. **Nothing on the card is purple**, the field has no border and no ground, and nothing is nested.
- `11-note-ack-phone`: the same at 390 px; the line spans the card, the send is a 44 px target, and a placeholder longer than the field ends in `…` (one line, as every input). No horizontal scroll (the `shoot` helper asserts it).
- `12-report-notes`: inside the report card, above the evidence line: `YOUR NOTES`, then `More on fire-safety standards not checked` and `Only the United States not checked`, the captions in the mono `.cap`, a hairline below, then the report as `04-report` shows it. No second card, no box around the block.
- `12-report-notes-phone`: the same at 390 px, every slice read; the rest of the page matches `04-report-phone`.
- `03-running` and `09-running-extra-pass` (both viewports) now end with the note line and are otherwise unchanged from Phase 2's captures; `01`, `02`, `04`, `05`, `07`, `08` and `10` are unchanged.

Any finding is fixed in Task 10's files and the captures retaken under a new checkpoint name (`P3-T11b`), never overwritten.

- [ ] **Step 6: Commit**

```bash
git add web/e2e/support.ts web/e2e/notes.spec.ts web/e2e/visual.spec.ts
git commit -m "test(web): reader notes end to end on the replay server, and the note-ack and report-notes captures"
```

---

### Task 12: The design record for reader notes, and the sidebar's stale sentences (spec §4.9 Phase-3 rows: DESIGN.md §3.5, `web/README.md`)

**Files:**
- Modify: `docs/design/DESIGN.md:159` (stages 3 and 4 in the inventory), `:195` (the note line, the acknowledgements and "Your notes", after the composer paragraph), `:265` (§3.1: the notes route in the route list; the sidebar's source and its question row), `:795`, `:817`, `:839`, `:848`, `:882` (§3.5: the active-row table, the note-pass arc and its stroke, the loops' reopen lines, the honest-limitation paragraph), `:1040` (§4's pass-fact note), `:1377` (§5.6: the acknowledgement's motion), `:1627` (§6: the Idle row, a Notes row, the Sidebar row); `web/README.md:22`, `:36`

**Interfaces:**
- Consumes: Tasks 2–11 (the behaviour this records).
- Produces: DESIGN.md §3.5 with the note-pass route and its `--meta` stroke (the spec's §4.9 row), and the note line, its acknowledgements and the report block in §3 and §5.6; `web/README.md`'s capture count and a notes bullet.
- Also, as the controller asked after Phase 2's fix wave: §3.1 still called the sidebar a "client-assembled ledger" and §6 still said "no `GET /research`", though the sidebar lists `GET /research` (`web/components/ConsoleProvider.tsx:62`, memory only, newest first). Both are reworded minimally, with the two places that repeat the same stale fact: §3.1's question-text row (the client's own copy) and §6's Idle row ("`query` is never returned; no endpoint lists sessions").

- [ ] **Step 1: Edit the design record**

**`docs/design/DESIGN.md`**: 11 edits, in file order.

`docs/design/DESIGN.md` (anchor as written by Phase 2 Task 10) — replace

```markdown
| 2a | **Check** | `status == "needs_input"`, or the stream's `session.clarification.requested` until the planner's `graph.node.started` | One question at a time in the pipeline card's place, under the eyebrow `Before we start`, the locked question and the settings strip (pick 4B): the answers, the best guess marked, **Other…**, then Back · Skip this one · Just start and the countdown; after the answers, the one summary line `Starting research with: …` | The planner starts → stage 3 |
| 3 | **Running** | `status == "running"` | **The pipeline, centred**, with the question and its settings above it | Server status leaves `running` → stage 4 or 5 |
| 4 | **Report** | any terminal status with a report — `completed`, or the three partial outcomes: the extra-pass ceiling spent (`max_iterations`), a review that did not accept or a gate that blocked acceptance (`incomplete`, scored), no review score (`incomplete`, unavailable) | The question, the settings in force, actions, then the server's Markdown body and its rail — or the Evidence view | Opening another session, or New research |
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |
```

with

```markdown
| 2a | **Check** | `status == "needs_input"`, or the stream's `session.clarification.requested` until the planner's `graph.node.started` | One question at a time in the pipeline card's place, under the eyebrow `Before we start`, the locked question and the settings strip (pick 4B): the answers, the best guess marked, **Other…**, then Back · Skip this one · Just start and the countdown; after the answers, the one summary line `Starting research with: …` | The planner starts → stage 3 |
| 3 | **Running** | `status == "running"` | **The pipeline, centred**, with the question and its settings above it and the note line at the card's foot | Server status leaves `running` → stage 4 or 5 |
| 4 | **Report** | any terminal status with a report — `completed`, or the three partial outcomes: the extra-pass ceiling spent (`max_iterations`), a review that did not accept or a gate that blocked acceptance (`incomplete`, scored), no review score (`incomplete`, unavailable) | The question, the settings in force, actions, then — when the reader added notes — **Your notes**, the server's Markdown body and its rail — or the Evidence view | Opening another session, or New research |
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |
```

`docs/design/DESIGN.md` — replace

```markdown
gone for the rest of that session's life: stages 2, 3 and 4 show the locked-in
question and the settings it was run with, and nothing that accepts typing. A new
question is started from **New research** in the sidebar, which clears the active
session and returns to stage 1. This is deliberate — a text box beside a running
pipeline invites a second submission that the API would treat as an unrelated
session, and a text box above a finished report invites a question the operator
would expect to refine the report in place.
```

with

```markdown
gone for the rest of that session's life: stages 2, 3 and 4 show the locked-in
question and the settings it was run with, and nothing that accepts a question. A new
question is started from **New research** in the sidebar, which clears the active
session and returns to stage 1. This is deliberate — a text box beside a running
pipeline invites a second submission that the API would treat as an unrelated
session, and a text box above a finished report invites a question the operator
would expect to refine the report in place.

**The note line is the one text entry after stage 1, and it is not a second question**
(live-briefs D8, D9, D11a, 2026-09-29; pick 6A). The running stage's pipeline card ends
in a quiet line under a `--border-soft` hairline: a borderless field reading
`Add a note — something to focus on, leave out or change` and a neutral icon send, never
the purple primary. Enter sends, and the field is read-only while the note is in flight.
A note steers *this* run — the session reads it with a fast model and every step but
Verifying uses it (D10) — so it never reads as a new session. The running row
acknowledges each note at the top of its brief, with a muted dot: `Reading your note…`,
then `Got it — {the run's reading}{where it applies}`, with
`…, replacing your earlier note about {…}` when it contradicts an earlier one and
`Got it — passed on as you wrote it` when the reading failed; past two notes it shows the
latest two and `and {n} earlier notes`. The run's reading is shown, never the note echoed
back (D9). Each acknowledgement is a polite live region, so a screen reader hears it once
the note is read, and nothing moves focus. After the tenth note the field and the send are
disabled with no message and the same placeholder (D11a); once `finalize_report` has
started — from the run's decision to publish — a note is refused and one caption,
`Notes are closed — the report is being published`, takes the line's place. Any other
failed send keeps the text and says `Couldn't send — try again` under the field until the
next edit. The report then states what became of each note: inside the report card and
above the prose — not a card of its own, and outside `.prose` — `Your notes` lists each
note as written with its outcome as a caption: `covered`, `couldn't find evidence`,
`not addressed in the report` (the findings bore on it and the report still does not
follow it, after its one redraft — never `covered`), `not checked` (no review judged it)
or `replaced by a later note`.
```

`docs/design/DESIGN.md` (anchor as written by Phase 2's review fix `539d51f`) — replace

```markdown
only), the `GET`s keyed by an id the client must already hold (`/research/{id}/status`,
`/stream`, `/report`, `/evidence`, `/trace`) and `POST /research/{id}/answers` for the
one-time check. A session id is generated server-side (`new_session_id()`), so a
client cannot even guess one.

The sidebar therefore renders a **client-assembled ledger**, and says so in its
own footer rather than implying a server-side history:

| Sidebar row shows | Source | Honest when absent |
|---|---|---|
| Question text | The client's own copy of what it submitted, clamped to two lines | Never absent for rows the client created |
| A running mark | `isLive(status)` in the session snapshot: `running`, or `needs_input` while the session waits for the reader's answers | Absent on every settled row, by design — see below |
```

with

```markdown
only), the `GET`s keyed by an id the client must already hold (`/research/{id}/status`,
`/stream`, `/report`, `/evidence`, `/trace`), `POST /research/{id}/answers` for the
one-time check and `POST /research/{id}/notes` for the reader's notes. A session id is
generated server-side (`new_session_id()`), so a client cannot even guess one.

The sidebar therefore lists **only what this API process holds** (`GET /research`, newest
first, memory only), and says so in its own footer rather than implying a durable history:

| Sidebar row shows | Source | Honest when absent |
|---|---|---|
| Question text | The session's `query`, as `GET /research` returns it, clamped to two lines | Never absent: every session has one |
| A running mark | `isLive(status)` in the session snapshot: `running`, or `needs_input` while the session waits for the reader's answers | Absent on every settled row, by design — see below |
```

`docs/design/DESIGN.md` — replace

```markdown
| `graph.node.completed` for `planner` … `report_writer` | the next row |
| `graph.route.decided` | the destination's row, immediately: `extra_pass` → Researching, `redraft` → Writing, `finalize` → Publishing, `end` → none (the failed stage follows) |
| `graph.node.completed` for `report_reviewer` | **inert after a loop decision** — it neither marks Reviewing `done` nor moves the active row; after `finalize` or `end` it marks Reviewing `done` as any completion does |
| `graph.node.completed` for the hops `extra_pass` / `writer_redraft` | nothing: hops never map to a row |
| `graph.node.completed` for `finalize_report`, or `graph.session.completed` | none; the stage transition follows |

Three things make the loop legible, and **none of them is a sentence**:

1. **Two return arcs are drawn, in the spine's left gutter.** Each leaves the
   Reviewing node and returns to the row the graph re-runs: the **extra-pass
   arc** to Researching, stroked `--warn`, and the **redraft arc** to Writing,
   stroked `--meta`. A dashed overlay travels along the lit arc and an arrowhead
   points into the destination. The arc is measured from the live node positions
```

with

```markdown
| `graph.node.completed` for `planner` … `report_writer` | the next row |
| `graph.route.decided` | the destination's row, immediately: `extra_pass` or `note_pass` → Researching, `redraft` → Writing, `finalize` → Publishing, `end` → none (the failed stage follows) |
| `graph.node.completed` for `report_reviewer` | **inert after a loop decision** — it neither marks Reviewing `done` nor moves the active row; after `finalize` or `end` it marks Reviewing `done` as any completion does |
| `graph.node.completed` for the hops `extra_pass` / `note_pass` / `writer_redraft` | nothing: hops never map to a row |
| `graph.node.completed` for `finalize_report`, or `graph.session.completed` | none; the stage transition follows |

Three things make the loop legible, and **none of them is a sentence**:

1. **Return arcs are drawn in the spine's left gutter.** Each leaves the
   Reviewing node and returns to the row the graph re-runs: the **extra-pass
   arc** to Researching, stroked `--warn`; the **note-pass arc** to Researching
   too, stroked `--meta`, because a reader's note is not a warning (live-briefs
   §4.7); and the **redraft arc** to Writing, stroked `--meta`. A dashed overlay travels along the lit arc and an arrowhead
   points into the destination. The arc is measured from the live node positions
```

`docs/design/DESIGN.md` — replace

```markdown
   | `off` | run start; `graph.session.completed`; the next `graph.route.decided` | a run that has not looped, or whose loop is over |
   | `flowing` | `graph.route.decided` with `destination: extra_pass` or `redraft` | the handoff: dashes travel from Reviewing to the destination |
   | `settled` | `graph.extra_pass.started` / `graph.report.redraft_requested` | the re-armed rows are the ones running; the arc rests lit |

   At most one arc is lit; `#spineWrap` carries `data-arc="extra_pass"|"redraft"`
   beside `data-loop`. A one-pass run never shows either, which is correct —
   nothing looped. `--meta` is a stroke here and never text; the extra-pass reopen line's amber
   is the text-safe `--status-warn`. Under `prefers-reduced-motion` both arcs
   arrive already lit.

2. **The pipeline is always exactly one pass.** On `graph.route.decided` with
   `destination: extra_pass` rows 2–6 go hollow and Planning keeps `done`; with
   `destination: redraft` rows 5–6 go hollow and rows 1–4 keep `done`. The reset
```

with

```markdown
   | `off` | run start; `graph.session.completed`; the next `graph.route.decided` | a run that has not looped, or whose loop is over |
   | `flowing` | `graph.route.decided` with `destination: extra_pass`, `note_pass` or `redraft` | the handoff: dashes travel from Reviewing to the destination |
   | `settled` | `graph.extra_pass.started` / `graph.note_pass.started` / `graph.report.redraft_requested` / `graph.note_redraft.requested` | the re-armed rows are the ones running; the arc rests lit |

   At most one arc is lit; `#spineWrap` carries `data-arc="extra_pass"|"note_pass"|"redraft"`
   beside `data-loop`. A one-pass run never shows any, which is correct —
   nothing looped. `--meta` is a stroke here and never text; the extra-pass reopen line's amber
   is the text-safe `--status-warn`. Under `prefers-reduced-motion` every arc
   arrives already lit.

2. **The pipeline is always exactly one pass.** On `graph.route.decided` with
   `destination: extra_pass` or `note_pass` rows 2–6 go hollow and Planning keeps `done`; with
   `destination: redraft` rows 5–6 go hollow and rows 1–4 keep `done`. The reset
```

`docs/design/DESIGN.md` — replace

```markdown
3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass, on Writing for a redraft, once the row has completed again.
```

with

```markdown
3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass or a note pass, on Writing for a redraft, once the row has completed again.
```

`docs/design/DESIGN.md` — replace

```markdown
header's loop tag, which went with the "Now" header.
```

with

```markdown
header's loop tag, which went with the "Now" header.

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

`docs/design/DESIGN.md` — replace

```markdown
the route history, so the arcs and the reopen lines are driven by
`graph.route.decided`, `graph.extra_pass.started` and
`graph.report.redraft_requested` rather than by the session. A finished session
opens on its report, which states the passes in plain words (§4); its loops are
```

with

```markdown
the route history, so the arcs and the reopen lines are driven by
`graph.route.decided`, `graph.extra_pass.started`, `graph.note_pass.started`,
`graph.report.redraft_requested` and `graph.note_redraft.requested` rather than by the
session. A finished session
opens on its report, which states the passes in plain words (§4); its loops are
```

`docs/design/DESIGN.md` — replace

```markdown

`note_passes` arrives with reader notes (live-briefs Phase 3) and reads as 0 until
then. The one rule the old counter taught still holds: `researcher.tool_call` carries
an `iteration` that is the ReAct step index, never the pass, so nothing reads the
```

with

```markdown

`note_passes` is the status response's count of the targeted passes the reader's notes
bought (live-briefs §4.6): 0 for a run without notes, and for a status recorded before
notes existed. The one rule the old counter taught still holds: `researcher.tool_call` carries
an `iteration` that is the ReAct step index, never the pass, so nothing reads the
```

`docs/design/DESIGN.md` (anchor as written by Phase 2 Task 10) — replace

```markdown
  in place over 160ms, with no rise.
- **One decorative loop, and it is not load-bearing.** `halo` runs at
```

with

```markdown
  in place over 160ms, with no rise.
- **A note's acknowledgement rises in once** (live-briefs §4.7, 2026-09-29). A new
  `.ack` line starts from its `@starting-style` and rises 4px as it fades in over
  200ms: at once in a row that is already open, and in its place in the stagger in
  the row a hand-off is opening. **Under reduced motion** it fades in place over
  160ms, like every brief line.
- **One decorative loop, and it is not load-bearing.** `halo` runs at
```

`docs/design/DESIGN.md` (anchor as written by Phase 2 Task 10) — replace

```markdown
| Evidence (every stage) | **E1** — no `GET /research/{id}/evidence`: the Evidence view, the `Download evidence log` button and coverage's question text are prototype-only until it exists |
| Idle | the session's own `query` is never returned; no endpoint lists sessions; no effective-settings echo; no `/capabilities`; no `/health` |
| Submitted | nothing beyond Idle |
| Check | nothing: `needs_input`, the two `session.clarification.*` events and `POST /research/{id}/answers` serve it (live-briefs Phase 2) |
| Running | no token usage; no terminal frame; no `Last-Event-ID` resume (events carry an `event_id`, but a reconnect replays from event 1); the halting vocabulary is a client copy; shutdown leaves `running` |
| Report | Markdown only (a JSON projection is a nice-to-have now that the format is stable); no report hash on the response |
| Failed | what survived a halt comes only from the stream; the halted state still needs a seeded session |
| Sidebar | no `GET /research`; no result summary per row; no durable store |
```

with

```markdown
| Evidence (every stage) | **E1** — no `GET /research/{id}/evidence`: the Evidence view, the `Download evidence log` button and coverage's question text are prototype-only until it exists |
| Idle | no effective-settings echo; no `/capabilities`; no `/health` |
| Submitted | nothing beyond Idle |
| Check | nothing: `needs_input`, the two `session.clarification.*` events and `POST /research/{id}/answers` serve it (live-briefs Phase 2) |
| Notes | nothing: `POST /research/{id}/notes`, the two `session.note.*` events and the status's `notes`, `notes_remaining` and `note_passes` serve them (live-briefs Phase 3); on the replay server a note is acknowledged but never applied (api-gaps 3.9) |
| Running | no token usage; no terminal frame; no `Last-Event-ID` resume (events carry an `event_id`, but a reconnect replays from event 1); the halting vocabulary is a client copy; shutdown leaves `running` |
| Report | Markdown only (a JSON projection is a nice-to-have now that the format is stable); no report hash on the response |
| Failed | what survived a halt comes only from the stream; the halted state still needs a seeded session |
| Sidebar | no result summary per row; no durable store (`GET /research` lists only what the process holds) |
```

**`web/README.md`**: 2 edits, in file order.

`web/README.md` (anchor as written by Phase 2 Task 10) — replace

```markdown
  (`…/deep-research/.venv/Scripts/python.exe`); `npx playwright install chromium` once.
- `npm run capture:visual` — the sixteen full-page captures (8 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

with

```markdown
  (`…/deep-research/.venv/Scripts/python.exe`); `npx playwright install chromium` once.
- `npm run capture:visual` — the twenty full-page captures (10 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

`web/README.md` (anchor as written by Phase 2 Task 10) — replace

```markdown
  questions (Region, Period, For); `e2e/clarify.spec.ts` and the `10-clarify` capture use it.
- Replay mode's `duration_seconds` is the unpaced span (about 0.2 s), so the report head
```

with

```markdown
  questions (Region, Period, For); `e2e/clarify.spec.ts` and the `10-clarify` capture use it.
- Reader notes (live-briefs spec §4.6-§4.7): the note line at the foot of the running
  pipeline card posts `POST /research/{id}/notes` (the proxy forwards it); replay mode reads
  each note with a scripted interpreter that keeps it as written. Replay runs the engine
  ahead of its paced stream, so a note added on the replay server is acknowledged but never
  applied, and the report's "Your notes" reads it `not checked`; `e2e/notes.spec.ts` and the
  `11-note-ack` and `12-report-notes` captures use it.
- Replay mode's `duration_seconds` is the unpaced span (about 0.2 s), so the report head
```

- [ ] **Step 2: Check the record says what the code does**

```bash
grep -c "note_pass" docs/design/DESIGN.md
grep -F "Add a note — something to focus on, leave out or change" docs/design/DESIGN.md | wc -l
grep -F 'data-arc="extra_pass"|"note_pass"|"redraft"' docs/design/DESIGN.md | wc -l
grep -F "twenty full-page captures" web/README.md | wc -l
grep -cE "client-assembled ledger|no \`GET /research\`|no endpoint lists sessions" docs/design/DESIGN.md
grep -F "POST /research/{id}/notes" docs/design/DESIGN.md | wc -l
cd web && npm run -s check:css
```

Expected: `11`, `1`, `1`, `1`, `0`, `2`; then `OK`.

- [ ] **Step 3: Commit**

```bash
git add docs/design/DESIGN.md web/README.md
git commit -m "docs(design): reader notes — the note line, acknowledgements, the note-pass arc, Your notes; the sidebar reads GET /research"
```

---

### Task 13: Remove the run state's dead fields (Phase 2 final review R6)

Phase 1 removed the pass counter, the loop tag and the step blurbs from the page (D14, D15), and nothing has read `RunState.maxPasses`, `.tag` or `.blurbs`, or the `BLURB` table and the `LoopTag` type, since. Phase 2's final review asked for them to go after Phase 3's web work. This task runs after every other web task, so no earlier anchor moves, and it changes nothing the page shows.

**Files** (line numbers as Tasks 9–11 leave the files):
- Modify: `web/lib/run-state.ts:32` (`BLURB`, `LoopTag`), `:71` (`RunState.maxPasses`, `.tag`, `.blurbs`), `:100` (`newRunState()`), `:189` (the `graph.session.started` handler, whose only job was `maxPasses`), `:266`, `:294`, `:306`, `:335` (every write to `tag` and `blurbs`), `:395` (`replayRun(events)`); `web/components/SessionScreen.tsx:33`, `:118` (`newRunState()`)
- Test: `web/test/run-state.test.ts:8` (a new test), `:16`, `:25` (the handler list, now twenty-two) and every `tag`, `maxPasses` and `passes` use after it; the `passes` argument in `web/test/briefs.test.ts:13`, `:46`, `:60`, `:73`, `web/test/clarify.test.ts:14`, `:29`, `web/test/notes.test.ts:13`, `:135`, `web/test/components/brief-spine.test.tsx:10`, `:121`, `:151`, `clarify-stage.test.tsx:14`, `:201`, `:210`, `failed-stage.test.tsx:15`–`:95`, `reader-notes.test.tsx:10`, `running-pipeline.test.tsx:7`, `spine.test.tsx:7`, `:23`

**Interfaces:**
- Consumes: Tasks 9–11, the last web tasks.
- Produces: `RunState` without `maxPasses`, `tag` or `blurbs`; no `BLURB` or `LoopTag` export; no `graph.session.started` handler (`applyEvent` ignores an event it has no handler for, `web/lib/run-state.ts:370-373`); `newRunState()` and `replayRun(events)` without the `passes` argument, which only fed `maxPasses`. The reopen lines, which took over the loop tag's job in Phase 1, are what the tests now assert where they asserted the tag.

- [ ] **Step 1: Write the failing test, and drop the dead values from the tests**

The new test pins the run state's exact keys, that `graph.session.started` changes nothing, and that `BLURB` is gone. Every other edit here removes a use of what goes: the `passes` argument at each call, the `maxPasses` and `tag` assertions, and `graph.session.started` in the handler list.

**`web/test/run-state.test.ts`**: 20 edits, in file order.

`web/test/run-state.test.ts` — replace

```ts
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, chipStep, countPhrase, failedMarks, newRunState, plural, replayRun, stepLabel, toRunEvent, toggleOpen, type RunState } from "../lib/run-state";
```

with

```ts
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, chipStep, countPhrase, failedMarks, newRunState, plural, replayRun, stepLabel, toRunEvent, toggleOpen, type RunState } from "../lib/run-state";
import * as runStateModule from "../lib/run-state";
```

`web/test/run-state.test.ts` — replace

```ts
/* One snapshot per frame: the state after frames 1..k, as a late subscriber replaying k frames sees it. */
function snapshots(events: ResearchEvent[], passes: number): RunState[] {
  const run = newRunState(passes);
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
```

with

```ts
/* One snapshot per frame: the state after frames 1..k, as a late subscriber replaying k frames sees it. */
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
```

`web/test/run-state.test.ts` (anchor as written by Task 9) — replace

```ts
};
const P = (c: Capture) => (c.events[0].metadata.max_extra_passes as number) + 1;

describe("the port is the prototype's core", () => {
  it("has the seven rows and the twenty-three handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed", "graph.session.started",
      "planner.planning.completed", "report_writer.report.written", "researcher.research.completed",
```

with

```ts
};

describe("the port is the prototype's core", () => {
  it("has the seven rows and the twenty-two handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed",
      "planner.planning.completed", "report_writer.report.written", "researcher.research.completed",
```

`web/test/run-state.test.ts` — replace

```ts

describe("(a) terminal agreement with the server after the last frame", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const run = replayRun(capture.events, P(capture));
      expect(run.finalStatus).toBe(capture.status.status);
      expect(run.pass).toBe(capture.status.iteration + 1);
      expect(run.maxPasses).toBe(P(capture));
      expect(run.active).toBeNull();
      expect(run.loop).toBe("off");
      expect(run.arc).toBeNull();
      expect(run.tag).toBeNull();
    });
  }
});

describe("(b) the extra pass", () => {
  const snaps = snapshots(extraPass.events, P(extraPass));
  const events = extraPass.events;
```

with

```ts

describe("the run state holds only what the page reads (Phase 2 final review R6)", () => {
  it("has no pass cap, loop tag or blurbs, exports no BLURB, and graph.session.started changes nothing", () => {
    const run = newRunState();
    expect(Object.keys(run).sort()).toEqual([
      "active", "arc", "captions", "clarify", "counters", "countersPass", "finalStatus", "findingsSoFar",
      "loop", "loopPending", "marks", "notes", "open", "openNode", "outcomes", "pagesRead", "pass",
      "passFindings", "plan", "rearmed", "rearmedFirst", "reopen", "topics",
    ]);
    const before = structuredClone(run);
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
    expect(run).toEqual(before);
    expect(Object.keys(runStateModule)).not.toContain("BLURB");
  });
});

describe("(a) terminal agreement with the server after the last frame", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const run = replayRun(capture.events);
      expect(run.finalStatus).toBe(capture.status.status);
      expect(run.pass).toBe(capture.status.iteration + 1);
      expect(run.active).toBeNull();
      expect(run.loop).toBe("off");
      expect(run.arc).toBeNull();
    });
  }
});

describe("(b) the extra pass", () => {
  const snaps = snapshots(extraPass.events);
  const events = extraPass.events;
```

`web/test/run-state.test.ts` — replace

```ts
  });
  it("the hop settles the loop, advances the pass, sets the tag and resets this-pass counters", () => {
    const s = snaps[hop];
    expect(s.loop).toBe("settled");
    expect(s.pass).toBe(2);
    expect(s.tag?.kind).toBe("extra_pass");
    expect(s.tag?.text).toBe("1 required target had no verified finding");
    expect(s.captions.researcher).toBe("1 missing target only");
```

with

```ts
  });
  it("the hop settles the loop, advances the pass, sets the reopen line and resets this-pass counters", () => {
    const s = snaps[hop];
    expect(s.loop).toBe("settled");
    expect(s.pass).toBe(2);
    expect(s.reopen.researcher).toEqual({ kind: "extra_pass", text: "Going back to research 1 gap the review found" });
    expect(s.captions.researcher).toBe("1 missing target only");
```

`web/test/run-state.test.ts` — replace

```ts
describe("(c) the redraft", () => {
  const snaps = snapshots(redraft.events, P(redraft));
  const events = redraft.events;
```

with

```ts
describe("(c) the redraft", () => {
  const snaps = snapshots(redraft.events);
  const events = redraft.events;
```

`web/test/run-state.test.ts` — replace

```ts
    expect(s.pass).toBe(1);
    expect(s.tag).toEqual({ kind: "redraft", label: "redraft", text: "Reviewer named 1 material defect" });
  });
```

with

```ts
    expect(s.pass).toBe(1);
    expect(s.reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
  });
```

`web/test/run-state.test.ts` — replace

```ts
  it("marks the halting row active, the rest skipped, Publishing skipped, counters unreached", () => {
    const run = newRunState(2);
    for (const ev of halted) applyEvent(run, ev);
```

with

```ts
  it("marks the halting row active, the rest skipped, Publishing skipped, counters unreached", () => {
    const run = newRunState();
    for (const ev of halted) applyEvent(run, ev);
```

`web/test/run-state.test.ts` — replace

```ts
  it("an API-level failure (no graph.session.completed) still skips Publishing by the session status", () => {
    const run = newRunState(2);
    applyEvent(run, halted[0]);
```

with

```ts
  it("an API-level failure (no graph.session.completed) still skips Publishing by the session status", () => {
    const run = newRunState();
    applyEvent(run, halted[0]);
```

`web/test/run-state.test.ts` — replace

```ts
  it("chipStep follows the active row, then the row the run ended on", () => {
    expect(chipStep(newRunState(2))).toBe("planner");
    const ended = replayRun(extraPass.events, P(extraPass));
    expect(ended.active).toBeNull();
    expect(chipStep(ended)).toBe("finalize_report");
    const halted = newRunState(2);
    applyEvent(halted, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
```

with

```ts
  it("chipStep follows the active row, then the row the run ended on", () => {
    expect(chipStep(newRunState())).toBe("planner");
    const ended = replayRun(extraPass.events);
    expect(ended.active).toBeNull();
    expect(chipStep(ended)).toBe("finalize_report");
    const halted = newRunState();
    applyEvent(halted, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
```

`web/test/run-state.test.ts` — replace

```ts
    const published = at(events, (e) => e.event_type === "graph.node.completed" && e.metadata.node === "finalize_report");
    const run = newRunState(P(extraPass));
    for (let k = 0; k <= published; k++) applyEvent(run, toRunEvent(events[k]));
```

with

```ts
    const published = at(events, (e) => e.event_type === "graph.node.completed" && e.metadata.node === "finalize_report");
    const run = newRunState();
    for (let k = 0; k <= published; k++) applyEvent(run, toRunEvent(events[k]));
```

`web/test/run-state.test.ts` — replace

```ts
    // a run that ended without ever finishing Publishing (the reviewer routed to "end") has nothing to name
    const ended = newRunState(2);
    applyEvent(ended, { type: "graph.route.decided", metadata: { destination: "end", reason: "no_report" } });
```

with

```ts
    // a run that ended without ever finishing Publishing (the reviewer routed to "end") has nothing to name
    const ended = newRunState();
    applyEvent(ended, { type: "graph.route.decided", metadata: { destination: "end", reason: "no_report" } });
```

`web/test/run-state.test.ts` — replace

```ts
    const events = capture.events;
    const snaps = snapshots(events, P(capture));
    const planned = at(events, (e) => e.event_type === "planner.planning.completed");
```

with

```ts
    const events = capture.events;
    const snaps = snapshots(events);
    const planned = at(events, (e) => e.event_type === "planner.planning.completed");
```

`web/test/run-state.test.ts` — replace

```ts
    const events = extraPass.events;
    const snaps = snapshots(events, P(extraPass));
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
```

with

```ts
    const events = extraPass.events;
    const snaps = snapshots(events);
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
```

`web/test/run-state.test.ts` — replace

```ts
    const events = redraft.events;
    const snaps = snapshots(events, P(redraft));
    const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested");
    expect(snaps[requested].reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
    expect(snaps[requested].reopen.researcher).toBeUndefined();
  });
  it("re-armed rows lose a reader's reopen", () => {
    const run = newRunState(2);
    run.marks = { planner: "done", researcher: "done", source_evaluator: "done" };
```

with

```ts
    const events = redraft.events;
    const snaps = snapshots(events);
    const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested");
    expect(snaps[requested].reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
    expect(snaps[requested].reopen.researcher).toBeUndefined();
  });
  it("re-armed rows lose a reader's reopen", () => {
    const run = newRunState();
    run.marks = { planner: "done", researcher: "done", source_evaluator: "done" };
```

`web/test/run-state.test.ts` — replace

```ts
      const events = capture.events;
      const run = replayRun(events, P(capture));
      const last = (type: string) => events.filter((e) => e.event_type === type).at(-1)!;
```

with

```ts
      const events = capture.events;
      const run = replayRun(events);
      const last = (type: string) => events.filter((e) => e.event_type === type).at(-1)!;
```

`web/test/run-state.test.ts` — replace

```ts
    it(capture.case_id, () => {
      const snaps = snapshots(capture.events, P(capture));
      capture.events.forEach((_, k) => expect(replayRun(capture.events.slice(0, k + 1), P(capture))).toEqual(snaps[k]));
    });
```

with

```ts
    it(capture.case_id, () => {
      const snaps = snapshots(capture.events);
      capture.events.forEach((_, k) => expect(replayRun(capture.events.slice(0, k + 1))).toEqual(snaps[k]));
    });
```

`web/test/run-state.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
  it("holds the questions and the deadline, then the answers and why; no row moves", () => {
    const run = newRunState(null);
    expect(run.clarify).toBeNull();
```

with

```ts
  it("holds the questions and the deadline, then the answers and why; no row moves", () => {
    const run = newRunState();
    expect(run.clarify).toBeNull();
```

`web/test/run-state.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
  it("drops a malformed question or answer rather than inventing one", () => {
    const run = newRunState(null);
    applyEvent(run, { type: "session.clarification.requested", metadata: { questions: [questions[0], { id: "q2", text: "no options" }, null], deadline_at: 5 } });
```

with

```ts
  it("drops a malformed question or answer rather than inventing one", () => {
    const run = newRunState();
    applyEvent(run, { type: "session.clarification.requested", metadata: { questions: [questions[0], { id: "q2", text: "no options" }, null], deadline_at: 5 } });
```

`web/test/run-state.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
      .map((e, i) => ({ event_type: e.type, source: "api", message: "m", timestamp: "2026-09-29T10:00:00+00:00", metadata: e.metadata, event_id: `e${i}` }));
    const snaps = snapshots(events, 2);
    events.forEach((_, k) => expect(replayRun(events.slice(0, k + 1), 2)).toEqual(snaps[k]));
  });
```

with

```ts
      .map((e, i) => ({ event_type: e.type, source: "api", message: "m", timestamp: "2026-09-29T10:00:00+00:00", metadata: e.metadata, event_id: `e${i}` }));
    const snaps = snapshots(events);
    events.forEach((_, k) => expect(replayRun(events.slice(0, k + 1))).toEqual(snaps[k]));
  });
```

**`web/test/briefs.test.ts`**: 4 edits, in file order.

`web/test/briefs.test.ts` — replace

```ts
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState((events[0].metadata.max_extra_passes as number) + 1);
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}

describe("the Researching brief (spec §4.3, AC5)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState(2);
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("search · scrape · read · memory");
```

with

```ts
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}

describe("the Researching brief (spec §4.3, AC5)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState();
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("search · scrape · read · memory");
```

`web/test/briefs.test.ts` — replace

```ts
  it("shows its static meta as the subtitle and one plain sentence", () => {
    const run = newRunState(2);
    for (const id of ["source_evaluator", "report_writer", "report_reviewer", "finalize_report"] as const) {
```

with

```ts
  it("shows its static meta as the subtitle and one plain sentence", () => {
    const run = newRunState();
    for (const id of ["source_evaluator", "report_writer", "report_reviewer", "finalize_report"] as const) {
```

`web/test/briefs.test.ts` — replace

```ts
    expect(verifyingSentence(4)).toBe("Checking 4 findings against their pages");
    const run = newRunState(2);
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
```

with

```ts
    expect(verifyingSentence(4)).toBe("Checking 4 findings against their pages");
    const run = newRunState();
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
```

`web/test/briefs.test.ts` — replace

```ts
  it("a looped row's first line is why it reopened", () => {
    const run = newRunState(2);
    applyEvent(run, { type: "graph.extra_pass.started", metadata: { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] } });
```

with

```ts
  it("a looped row's first line is why it reopened", () => {
    const run = newRunState();
    applyEvent(run, { type: "graph.extra_pass.started", metadata: { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] } });
```

**`web/test/clarify.test.ts`**: 2 edits, in file order.

`web/test/clarify.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
function asked(): RunState {
  const run = newRunState(null);
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: WIRE, deadline_at: DEADLINE } });
```

with

```ts
function asked(): RunState {
  const run = newRunState();
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: WIRE, deadline_at: DEADLINE } });
```

`web/test/clarify.test.ts` (anchor as written by Phase 2 Task 6) — replace

```ts
  it("a needs_input status asks before the stream has delivered the questions; a running one shows no check", () => {
    expect(checkPhase(newRunState(null), "needs_input")).toBe("asking");
    expect(checkPhase(newRunState(null), "running")).toBeNull();
  });
```

with

```ts
  it("a needs_input status asks before the stream has delivered the questions; a running one shows no check", () => {
    expect(checkPhase(newRunState(), "needs_input")).toBe("asking");
    expect(checkPhase(newRunState(), "running")).toBeNull();
  });
```

**`web/test/notes.test.ts`**: 2 edits, in file order.

`web/test/notes.test.ts` (anchor as written by Task 9) — replace

```ts
function play(events: RunEvent[]): RunState {
  const run = newRunState(2);
  for (const e of events) applyEvent(run, e);
```

with

```ts
function play(events: RunEvent[]): RunState {
  const run = newRunState();
  for (const e of events) applyEvent(run, e);
```

`web/test/notes.test.ts` (anchor as written by Task 9) — replace

```ts
    const events = [...toReviewing, received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b")];
    const oneByOne = newRunState(2);
    const snaps = events.map((e) => { applyEvent(oneByOne, e); return structuredClone(oneByOne); });
```

with

```ts
    const events = [...toReviewing, received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b")];
    const oneByOne = newRunState();
    const snaps = events.map((e) => { applyEvent(oneByOne, e); return structuredClone(oneByOne); });
```

**`web/test/components/brief-spine.test.tsx`**: 3 edits, in file order.

`web/test/components/brief-spine.test.tsx` — replace

```tsx
function researching(): RunState {
  const run = newRunState(2);
  for (const [type, metadata] of [
```

with

```tsx
function researching(): RunState {
  const run = newRunState();
  for (const [type, metadata] of [
```

`web/test/components/brief-spine.test.tsx` — replace

```tsx
    vi.useFakeTimers();
    const run = newRunState(2);
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
```

with

```tsx
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
```

`web/test/components/brief-spine.test.tsx` — replace

```tsx
  it("never awaits a row a loop sends the run back from", () => {
    const run = newRunState(2);
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
```

with

```tsx
  it("never awaits a row a loop sends the run back from", () => {
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
```

**`web/test/components/clarify-stage.test.tsx`**: 3 edits, in file order.

`web/test/components/clarify-stage.test.tsx` (anchor as written by Phase 2 Task 8) — replace

```tsx
function asking(): RunState {
  const run = newRunState(null);
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: QUESTIONS, deadline_at: "2026-09-29T10:01:00.000Z" } });
```

with

```tsx
function asking(): RunState {
  const run = newRunState();
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: QUESTIONS, deadline_at: "2026-09-29T10:01:00.000Z" } });
```

`web/test/components/clarify-stage.test.tsx` (anchor as written by Phase 2 Task 8) — replace

```tsx
    const props = { sessionId: "s1", phase: "asking" as const, question: "What limits grid-scale battery storage?", strip: null };
    rerender(<ClarifyStage {...props} run={newRunState(null)} />);
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
```

with

```tsx
    const props = { sessionId: "s1", phase: "asking" as const, question: "What limits grid-scale battery storage?", strip: null };
    rerender(<ClarifyStage {...props} run={newRunState()} />);
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
```

`web/test/components/clarify-stage.test.tsx` (anchor as written by Phase 2 Task 8) — replace

```tsx
  it("holds the header alone until the stream delivers the questions", () => {
    show(newRunState(null));
    expect(document.getElementById("clarify-h")!.textContent).toBe("What limits grid-scale battery storage?");
    expect(document.getElementById("clarifyCard")).toBeNull();
  });

  it("renders no summary at all when the stream answered a check it never asked (no questions to summarise)", () => {
    const run = newRunState(null);
    applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "skipped", answers: [] } });
```

with

```tsx
  it("holds the header alone until the stream delivers the questions", () => {
    show(newRunState());
    expect(document.getElementById("clarify-h")!.textContent).toBe("What limits grid-scale battery storage?");
    expect(document.getElementById("clarifyCard")).toBeNull();
  });

  it("renders no summary at all when the stream answered a check it never asked (no questions to summarise)", () => {
    const run = newRunState();
    applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "skipped", answers: [] } });
```

**`web/test/components/failed-stage.test.tsx`**: 8 edits, in file order.

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "api.research.configuration_error", source: "api", message: "Research service configuration is unavailable.", recoverable: false, timestamp: "", details: { reason: "config_invalid" } });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed({ error_type: "api.research.configuration_error", source: "api", message: "Research service configuration is unavailable.", recoverable: false, timestamp: "", details: { reason: "config_invalid" } });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "The model provider is not configured, so the research run stopped.", recoverable: false, timestamp: "", details: { exception_type: "ValidationError" } });
    const run = newRunState(2);
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
```

with

```tsx
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "The model provider is not configured, so the research run stopped.", recoverable: false, timestamp: "", details: { exception_type: "ValidationError" } });
    const run = newRunState();
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "graph_invalid_route", source: "", message: "The graph reached an unregistered route.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed({ error_type: "graph_invalid_route", source: "", message: "The graph reached an unregistered route.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed(survived, halt);
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed(survived, halt);
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "some_unmapped_error_type", source: "agent.report_writer", message: "", recoverable: false, timestamp: "", details: {} });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed({ error_type: "some_unmapped_error_type", source: "agent.report_writer", message: "", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed();
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed();
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "api.research.failed", source: "api", message: "Research run failed unexpectedly.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed({ error_type: "api.research.failed", source: "api", message: "Research run failed unexpectedly.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/failed-stage.test.tsx` — replace

```tsx
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "m", recoverable: false, timestamp: "", details: {} });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

with

```tsx
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "m", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
```

`web/test/components/reader-notes.test.tsx` (anchor as written by Task 10) — replace

```tsx
function researchingWithNotes(count: number): RunState {
  const run = newRunState(2);
  applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
```

with

```tsx
function researchingWithNotes(count: number): RunState {
  const run = newRunState();
  applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
```

`web/test/components/running-pipeline.test.tsx` — replace

```tsx
  it("has no Now header, no counters block and no pass text; the card holds the spine", () => {
    const run = newRunState(2);
    run.active = "researcher";
```

with

```tsx
  it("has no Now header, no counters block and no pass text; the card holds the spine", () => {
    const run = newRunState();
    run.active = "researcher";
```

**`web/test/components/spine.test.tsx`**: 2 edits, in file order.

`web/test/components/spine.test.tsx` — replace

```tsx
  it("paints data-state per row, data-fed from the previous row, and the caption", () => {
    const run = newRunState(2);
    run.marks = { planner: "done", researcher: "loop" };
```

with

```tsx
  it("paints data-state per row, data-fed from the previous row, and the caption", () => {
    const run = newRunState();
    run.marks = { planner: "done", researcher: "loop" };
```

`web/test/components/spine.test.tsx` — replace

```tsx
  it("marks a halted run's Publishing row skipped", () => {
    const run = newRunState(2);
    run.openNode = "planner";
```

with

```tsx
  it("marks a halted run's Publishing row skipped", () => {
    const run = newRunState();
    run.openNode = "planner";
```

- [ ] **Step 2: Run the run-state tests and see the new one fail**

```bash
cd web && npx vitest run test/run-state.test.ts 2>&1 | grep -E "FAIL|Test Files|Tests  " | head -6
```

Expected: two `FAIL` lines, then `Test Files  1 failed (1)` and `Tests  2 failed | 28 passed (30)`: the new test (`the run state holds only what the page reads …`: its keys still include `blurbs`, `maxPasses` and `tag`), and `has the seven rows and the twenty-two handlers`, which no longer lists `graph.session.started`.

- [ ] **Step 3: Remove the fields, the handler and the argument**

**`web/lib/run-state.ts`**: 9 edits, in file order.

`web/lib/run-state.ts` — replace

```ts
};
export const BLURB: Record<NodeId, string> = {
  planner: "Turning the question into sub-topics and evidence targets.",
  researcher: "Searching and reading; every finding keeps a verbatim snippet.",
  source_evaluator: "Scoring every source behind the findings.",
  evidence_verifier: "Checking each snippet is on its page, then each figure's context.",
  report_writer: "Drafting from verified findings; every sentence is checked against what it cites.",
  report_reviewer: "Scoring the report; accepted at a mean of 0.80 with no material defect.",
  finalize_report: "Publishing the report, the evidence log and the quality record.",
};

export interface Counters {
  subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null;
  findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null;
  statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null;
}
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string }
/* The step briefs' state (live-briefs spec §4.3). A topic is one planned sub-topic in this pass's
```

with

```ts
};
export interface Counters {
  subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null;
  findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null;
  statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null;
}
/* The step briefs' state (live-briefs spec §4.3). A topic is one planned sub-topic in this pass's
```

`web/lib/run-state.ts` (anchor as written by Task 9) — replace

```ts
  openNode: NodeId | null;                /* the last graph.node.started with no graph.node.completed — the halting row */
  pass: number; maxPasses: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | "note_pass" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
  tag: LoopTag | null;
  rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>; blurbs: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number;
```

with

```ts
  openNode: NodeId | null;                /* the last graph.node.started with no graph.node.completed — the halting row */
  pass: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | "note_pass" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
  rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number;
```

`web/lib/run-state.ts` — replace

```ts
}
export function newRunState(passes: number | null | undefined): RunState {
  return {
    marks: {}, active: "planner", openNode: null,
    pass: 1, maxPasses: Math.max(1, Number(passes) || 1),
    loop: "off", arc: null, loopPending: false, tag: null,
    rearmed: {}, rearmedFirst: null, captions: {}, blurbs: {},
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
```

with

```ts
}
export function newRunState(): RunState {
  return {
    marks: {}, active: "planner", openNode: null,
    pass: 1,
    loop: "off", arc: null, loopPending: false,
    rearmed: {}, rearmedFirst: null, captions: {},
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
```

`web/lib/run-state.ts` — replace

```ts
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.session.started": (run, md) => {
    if (typeof md.max_extra_passes === "number") run.maxPasses = 1 + md.max_extra_passes;
  },
  "graph.node.started": (run, md) => {
```

with

```ts
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.node.started": (run, md) => {
```

`web/lib/run-state.ts` — replace

```ts
  "graph.route.decided": (run, md) => {
    run.tag = null;
    run.loopPending = false;
```

with

```ts
  "graph.route.decided": (run, md) => {
    run.loopPending = false;
```

`web/lib/run-state.ts` — replace

```ts
    run.loop = "settled";
    run.tag = { kind: "extra_pass", label: "extra pass", text: plural(n, "required target had no verified finding", "required targets had no verified finding") };
    run.captions.researcher = plural(n, "missing target only", "missing targets only");
    run.blurbs.researcher = "Researching the " + plural(n, "target", "targets") + " still missing a verified finding.";
    run.reopen.researcher = { kind: "extra_pass", text: "Going back to research " + plural(n, "gap", "gaps") + " the review found" };
```

with

```ts
    run.loop = "settled";
    run.captions.researcher = plural(n, "missing target only", "missing targets only");
    run.reopen.researcher = { kind: "extra_pass", text: "Going back to research " + plural(n, "gap", "gaps") + " the review found" };
```

`web/lib/run-state.ts` — replace

```ts
    run.loop = "settled";
    run.tag = { kind: "redraft", label: "redraft", text: "Reviewer named " + plural(md.material_defects, "material defect", "material defects") };
    run.reopen.report_writer = { kind: "redraft", text: "Rewriting to fix " + plural(count(md.material_defects), "issue", "issues") + " the review found" };
```

with

```ts
    run.loop = "settled";
    run.reopen.report_writer = { kind: "redraft", text: "Rewriting to fix " + plural(count(md.material_defects), "issue", "issues") + " the review found" };
```

`web/lib/run-state.ts` — replace

```ts
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null; run.tag = null;
    run.active = null;
```

with

```ts
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null;
    run.active = null;
```

`web/lib/run-state.ts` — replace

```ts
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata }; }
export function replayRun(events: readonly ResearchEvent[], passes: number | null | undefined): RunState {
  const run = newRunState(passes);
  for (const event of events) applyEvent(run, toRunEvent(event));
```

with

```ts
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata }; }
export function replayRun(events: readonly ResearchEvent[]): RunState {
  const run = newRunState();
  for (const event of events) applyEvent(run, toRunEvent(event));
```

**`web/components/SessionScreen.tsx`**: 2 edits, in file order.

`web/components/SessionScreen.tsx` — replace

```tsx
  const [streaming, setStreaming] = useState(false);
  const run = useRef<RunState>(newRunState(null));
  const wake = useRef<(() => void) | null>(null);
```

with

```tsx
  const [streaming, setStreaming] = useState(false);
  const run = useRef<RunState>(newRunState());
  const wake = useRef<(() => void) | null>(null);
```

`web/components/SessionScreen.tsx` — replace

```tsx
            streamed = true;
            run.current = newRunState(null);
            run.current.open = reopened;
```

with

```tsx
            streamed = true;
            run.current = newRunState();
            run.current.open = reopened;
```

- [ ] **Step 4: Type-check, run every Vitest file, and look for anything left**

```bash
cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && ! grep -rnwE "maxPasses|blurbs|BLURB|LoopTag" lib components app && ! grep -rnE "\brun\.tag\b" lib components && echo "no dead field left"
```

Expected: no `typecheck` output; `Test Files  28 passed (28)`, `Tests  217 passed (217)` (Task 10's `216` + 1); `no dead field left`. The type check covers the tests: a call that still passed `passes`, or a test that still read `tag` or `maxPasses`, would fail it. The two `grep`s cover the app's own code (the new test names `BLURB` to assert that it is gone).

- [ ] **Step 5: Commit**

```bash
git add web/lib/run-state.ts web/components/SessionScreen.tsx web/test/run-state.test.ts web/test/briefs.test.ts web/test/clarify.test.ts web/test/notes.test.ts web/test/components/brief-spine.test.tsx web/test/components/clarify-stage.test.tsx web/test/components/failed-stage.test.tsx web/test/components/reader-notes.test.tsx web/test/components/running-pipeline.test.tsx web/test/components/spine.test.tsx
git commit -m "refactor(web): drop the run state's dead pass cap, loop tag and blurbs"
```

---

### Task 14: Full verification and the final visual review (AC15–AC20)

**Files:** none changed, unless a check fails; a fix then goes to the task that owns the file, with its own commit.

**Interfaces:**
- Consumes: Tasks 1–13, committed.
- Produces: the evidence that Phase 3 is complete.

- [ ] **Step 1: The backend, whole**

```bash
PY="$PWD/.venv/bin/python"
DESELECT="--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config --deselect tests/test_evaluation/test_config.py::test_windows_output_root_has_a_pure_extended_path_contract --deselect tests/test_evaluation/test_config.py::test_windows_output_root_transformation_is_idempotent --deselect tests/test_evaluation/test_config.py::test_windows_runtime_config_preserves_all_evaluation_semantics"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q $DESELECT 2>&1 | tail -1
```

Expected: `4929 passed, 6 skipped, 12 deselected`: Task 1's baseline plus the 95 new tests of Task 1 Step 5's increments.

- [ ] **Step 2: What must not have moved**

```bash
PY="$PWD/.venv/bin/python"
PYTHONPATH=src "$PY" -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print(f('evidence_verifier'))"
FIRST=$(git log --format=%H -1 -F --grep="feat(engine): reader notes on a per-run board")
git diff --stat "$FIRST~1" HEAD -- src/deep_research/agents/prompts.py src/deep_research/agents/evidence_verifier.py src/deep_research/request_budget.py src/deep_research/api/replay.py config.yaml | tail -1
git diff --numstat "$FIRST~1" HEAD -- web/app/globals.css
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_graph/test_reader_notes_replay.py -q 2>&1 | tail -1
```

`FIRST` is Task 2's commit, so `$FIRST~1` is the end of Phase 2 as this plan found it.

Expected: `4a3d56fab932` (the verifier's pin, D10); nothing from the `--stat` (those five files are untouched); `40	0	web/app/globals.css` (the stylesheet only gained lines, all after line 1131); `5 passed` (the two pinned whole-run digests, and the notes on the real graph).

- [ ] **Step 3: The web app, whole**

```bash
cd web && npm run -s typecheck && npx vitest run 2>&1 | grep -E "Test Files|Tests  " && npm run -s check:css
```

Expected: no `typecheck` output; `Test Files  28 passed (28)`, `Tests  217 passed (217)` (Task 10's `216` + 1); `OK`.

- [ ] **Step 4: Playwright, whole, and the captures**

```bash
for p in 8010 3010 3011; do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "port $p is in use"; done
export DEEP_RESEARCH_PYTHON="$PWD/.venv/bin/python"
cd web && npm run -s test:e2e 2>&1 | tail -3 && VISUAL_CHECKPOINT=P3-final npx playwright test --project=visual 2>&1 | tail -3
```

Expected: no port line, `61 passed`, then `12 passed`.

- [ ] **Step 5: The final visual review**

**This is a first-time visual review too, not a confirmation.** Planning's own run of this step was cut off before it printed, and its captures were never read; Step 4's Expected lines are observed values from the same tree (see "Evidence"; review round 1, P3-9). Read every image as new, even where Task 11 Step 5 already passed the same view.

Read all twenty `web/visual/P3-final/*.png` whole (slice the tall phone report as in Task 11 Step 5). The checks of Task 11 Step 5 hold for `11-note-ack(-phone)` and `12-report-notes(-phone)`, and every other capture matches its Phase 2 counterpart except that the running captures end with the note line.

- [ ] **Step 6: No commit**

Nothing changed. The summary lists the five Expected lines of Steps 1–4 as observed, and the capture review.

## Acceptance-criteria coverage

| AC | Proven by |
|---|---|
| AC15 | Task 11 `a note sent while Researching is received, read and acknowledged in the running row within 1 s (AC15)` — the ack is fully shown less than 1 s after the page **sent** the POST, and the stream carries `session.note.received` before `session.note.interpreted`. The spec measures from the interpreted event; the test measures from the send, which is an upper bound on that measure: the event is published only after the server has the note, so it cannot reach the page before the POST left it. (The first draft measured from the POST's 202, which is not a bound: the 202 and the event's frame reach the page over separate connections, and nothing orders them — review round 1, P3-4.) Task 8 `test_a_note_is_published_at_once_then_read_onto_the_boards_the_run_holds`; Task 9/10 ack wording and placement |
| AC16 | Task 6 `test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does` (the real graph: planner's scoping turn, plan and plan review; every research turn and extraction; scoring; section and bottom line; review — and no `ContextCheckDraft` or `StatementCheckDraft` request); Tasks 4–6 per-agent tests |
| AC17 | Task 3 `test_ac17_a_note_without_evidence_gets_exactly_one_targeted_pass` (one `graph.note_pass.started`, the researcher's second call sees only `note-n1-target-01` and selects only `note-n1`, `iteration` 0, `note_passes` 1), plus the `graph_route` and `note_pass_node` tests |
| AC18 | Task 3 `test_ac18_a_note_the_report_ignores_gets_exactly_one_redraft` (`writer_redrafts` stays 0, no `graph.report.redraft_requested`), `test_a_report_that_ignores_a_note_buys_its_redraft_even_with_the_reviews_own_rerun_spent`, `test_the_note_redraft_flags_its_notes_and_spends_no_rerun` |
| AC19 | Task 8 `test_the_notes_route_takes_a_note_and_the_status_lists_it_with_its_outcome` (the outcomes `covered`, `not_found` and `not_addressed` through the status response, `notes_remaining`, `note_passes`, a 409 `notes_closed` after the end) and `test_notes_close_while_the_session_waits_once_publishing_begins_and_once_it_ends`; Task 7 `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending`; Task 10 `sits inside the report card above the prose…`; Task 11 `once publishing has begun a note is refused…` and `the report lists every note above the prose…` |
| AC20 | Task 3 `test_ac20_ten_notes_each_buy_one_pass_and_one_redraft_within_the_recursion_limit` (each note arrives alone during a review: ten redrafts and ten passes, completed, supersteps below the limit); Task 8 `test_the_notes_route_refuses_unknown_sessions_bad_text_and_the_eleventh_note`; Task 10 disabled-line tests; Task 11 `after the tenth note the line is disabled with nothing added, and an eleventh is refused (AC20, D11a)` |
| R4 | bounded by D11a and the per-note flags: Task 3 AC20 and `graph_recursion_limit`; the request-attempt budget is untouched |
| R5 | a wrongly reported `no_evidence` buys one pass per note and the report states the gap: Task 3 AC17 (the verdict repeats; no second pass) and Task 5 `test_the_report_names_a_note_it_found_no_evidence_for` |

## Self-review

- **Spec coverage.** §4.6: route, 202/404/409 (both)/422, `notes_remaining` (Tasks 7–8); interpretation, fallback and events (Task 7); the board, its binding, the state fields and the node merge (Task 2); every step's use of notes, and none for verification (Tasks 4–6); routing, the per-note guards, the `note_pass` node and the recursion limit (Task 3); the status response (Tasks 7–8). §4.7: the note line, the acknowledgements, the note pass's reopen line, checklist and arc, the report block (Tasks 9–10). §4.8: the interpreter's failure, a POST in flight, ten notes, conflicting notes, a note during Reviewing, a note after publishing begins, replay's interpreter, reduced motion, phone (Tasks 3, 7, 8, 10, 11). §4.9: DESIGN.md §3.5 (Task 12), `api-gaps.md` and the READMEs (Tasks 8, 12). AC15–AC20: the coverage table above. §6: pytest (API, engine), Vitest, Playwright and the captures. §7 R4, R5: the coverage table.
- **Placeholders.** None: every block is the code planning ran, and every Expected line is an observed value.
- **Type consistency.** `ReaderNote`, `NoteDisposition`, `NoteBoard`, `ReceivedNote`, `NoteInterpretation` and `NoteOutcome` are defined once and used by the names above; the web's `NoteState`, `ReaderNoteRecord` and `ReaderNoteOutcome` mirror them; `note_pass`, `note_pass_requested` and `note_redraft_requested` are spelled the same in the graph, the events, the tests and the web.
- **Phase 2's final-review notes.** R1, R2 and M6 (Task 8), R3 (Task 10), R4 (Task 7) and R6 (Task 13) are met as the table under "Phase 2's final-review notes" says; O2 is left to the human, as asked. The DESIGN.md sentences the controller named after the fix wave are Task 12's.
- **Decisions.** D8 (Task 10), D9 (Tasks 7, 9, 10), D10 (Tasks 4–6: AC16 proves no verifier request), D11 (Task 3), D11a (Tasks 2, 8, 10, 11), D17 (Task 10's styles; Task 11's review). Nothing here changes a decision; the open issues are where the spec's text could not be kept as written.

## Review round 1 (2026-09-29): findings and how each was resolved

The review (`.superpowers/sdd/phase3-plan-review-r1.md`, spec-plan-reviewer) approved the plan with changes: no P1, one P2, ten P3s. The controller ruled on every finding: P2-1 gets its own honest outcome, recorded as open issue O8; P3-1 to P3-10 are all applied. Each is resolved below, and the round's changes were dry-run as described under Evidence.

- **P2-1 — a note the report still ignores after its one redraft was reported `covered`.** Fixed as ruled, and recorded as open issue O8 (the human may rename the value or its caption).
  - `note_outcome` maps `ignored_with_evidence` to a fifth outcome, `not_addressed`, and only `honoured` to `covered` (Task 7). `NoteOutcome` and `ReaderNoteResponse.outcome` (Task 7), the status response (Task 8), `ReaderNoteOutcome` and `OUTCOME_TEXT` (`not addressed in the report`, Task 9) and "Your notes" (Task 10) carry it; the README (Task 8) and DESIGN.md (Task 12) list it. No event carries an outcome, so no event changes.
  - Tests: Task 7's `test_each_note_ends_covered_not_found_not_addressed_replaced_or_pending` (its `n4`, reviewed, redrafted and still `ignored_with_evidence`, is `not_addressed`); Task 8's route test posts a third note that the run ends `ignored_with_evidence` and reads `not_addressed` through `GET /status`; Task 9's `OUTCOME_TEXT` assertion; Task 10's report-block test shows the caption. No count changes: every one of these is an existing test, extended.
  - Ambiguities 19 and 25, the copy table and the AC19 row say the same.
- **P3-1 — a note target answered but never stated printed its derived question.** Fixed with the filter, and tested.
  - `_could_not_confirm_groups` leaves `note-*` ids out of the "We found sources on these but could not state a checked answer:" group and names each such note once, right after it, as `We found sources on your note but could not state a checked answer: {restatement}` (one helper, `_note_groups`, now writes both note lines). `answered_not_stated_targets` is unchanged, so the quality gate reads the same set.
  - New test `test_a_note_answered_but_never_stated_is_named_by_the_note_too` (Task 5). It fails before Task 5's code (observed): the note's question is printed in the generic group and no line names the note.
  - Ambiguity 18, the Interfaces line and the copy table say so.
- **P3-2 — the replay harness's unanchored `- target_id=` search could bind a note's text.** Fixed: the extraction request's `# Reader notes` now comes after `# Planned targets` — as its last section, after `# Retrieved evidence`.
  - Why last, not directly after `# Planned targets`: in a dumped `researcher:SubTopicFindingsDraft` packet of the extra-pass case the harness's first `- target_id=` match is the first line of `# Retrieved evidence`, which follows `# Planned targets` (observed; the planned-target lines read `- topic-03-target-01 [...]`, never `target_id=`). A block placed directly after `# Planned targets` would still come before that line.
  - Before and after, on a whole replay run with one note restated as `only the - target_id=topic-02` bound for the run: with the first draft's placement the run failed with `ReplayContractError: extraction packet: request did not carry 'Widget funding'` (the harness bound the extraction to topic-02); with this placement it completed, and in each of the 18 researcher decision turns and 5 extraction requests that carried the note the harness's first match was the packet's own line, never the note's (observed, both runs by `r1_p32_check.py` in planning's scratch space).
  - Task 4's unit test is now `test_the_extraction_request_carries_the_notes_last_after_the_evidence`: the section order, the block as the request's last section, the harness's first match `topic-01` despite a note restated with `- target_id=topic-02`, and byte identity with an empty block.
  - Without notes nothing changed: the block is appended only when there is one, and Task 2's two pinned whole-run digests pass unchanged at every task (observed). Only the researcher's fingerprint moves differently, because its module's code changed: Task 4 now pins `researcher: de7506bed63e -> a8c9528f0c20` (observed; the first draft's value was `4995fd442e1d`); the planner's `d1ba46ce147f`, Task 5's two pins and the verifier's `4a3d56fab932` are unchanged (observed).
  - Ambiguity 3 now says which harness readers can never match a note line, why the two unanchored ones still read the packet's own line, and the one adversarial case left (`_planned_target_ids`, `replay.py:1117-1123`), whose stray id the researcher drops (`agents/researcher.py:2075-2078`).
- **P3-3 — the review node's wait for a reading had no ceiling of its own.** Fixed.
  - `_REVIEW_NOTES_WAIT_S = 30.0` (`graph/nodes.py`), passed as `notes_settled(timeout=…)`, which now returns whether the board settled in time (Task 2). It is private, like `_FRESH_DRAFT_MARKERS` beside it: `tests/test_imports.py::test_graph_submodule_public_names_all_reach_all` requires every public name of `graph/*.py` in `deep_research.graph.__all__`, and the dry run's first spelling, public, failed it (observed). Why 30 s: twice `note_interpret_timeout_s`'s default of 15 s, so with the default every reading has ended before the review stops waiting; a raised timeout (M6 allows 600 s) holds the route for 30 s at most. The cost is stated in ambiguity 8 and O5: a note still being read then misses that route decision, unreviewed; a loop's next node takes it in, and a decision to publish leaves it `pending`, as O4's note.
  - New test `test_the_review_waits_for_a_reading_no_longer_than_its_own_ceiling` (Task 3): it pins the value and its relation to the default, sets the ceiling to 50 ms, lands a note that is never read, and requires the node to return inside `asyncio.wait_for(..., timeout=5)` with the note left on the board, `n1` reviewed and the route read without it (`finalize`, `report_not_accepted`: no note is due, so the review's own verdict decides). Without the bound the test fails on its 5 s `wait_for`.
- **P3-4 — AC15 was timed from the POST's 202.** Fixed with the smaller accurate change. Stating in the coverage table that the 202 is an upper bound would not have been accurate: the 202 and the `session.note.interpreted` frame reach the page over separate connections, and nothing orders them. The recorder now stamps the moment the page sends the POST, which the interpreted event can only follow — so `ack − sent < 1000 ms` bounds AC15's measure from above — and the coverage table says so. Observed: `notes.spec.ts` passed 18 of 18 under `--repeat-each=3` with the new stamp.
- **P3-5 — the notes example's edit read as an unclosed tuple.** Fixed: the anchor now ends `    ),\n)\n`, and the replacement closes both tuples itself. The file it writes is byte-identical to before, and the anchor count is still 280 (observed).
- **P3-6 — a non-409 failure of the note POST was silent.** Fixed as ruled.
  - `NoteLine` keeps the text and shows `p#noteFailed.cap[role="status"]` `Couldn't send — try again` under the field (the line wraps only then, `.note-line[data-failed="1"]`) until the reader edits the note or sends it again. The two 409s keep their own faces, and the note-limit case still shows nothing.
  - The existing Vitest `keeps the text after any other failure…` is extended to a 500 and a network error, the caption, and its clearing on the next edit (no count change). `NOTE_SEND_FAILED` joins `lib/notes.ts` (Task 9); ambiguity 28, the copy table and DESIGN.md say so. Task 14's stylesheet line count moves from `36` to `40`.
- **P3-7 — acknowledgements were not announced.** Fixed: each note's ack line is a polite live region (`aria-live="polite"`, `aria-atomic="true"`), so the line is read whole when `Reading your note…` becomes `Got it — …`; the `and {n} earlier notes` line is not one. Asserted in Task 10's two acknowledgement Vitests (no count change); ambiguity 30 and DESIGN.md.
- **P3-8 — the note redraft's full fresh draft was not argued.** Ambiguity 16 now says why a scoped redraft was not chosen: a note has no parts to scope to — it applies to the whole report — and the review names no defect for it, only a disposition. The cost is stated: at most ten full redrafts and full reviews a run.
- **P3-9 — the captures had never been read.** Task 11 Step 5 and Task 14 Step 5 now open with a bold line: each is a first-time visual review, not a confirmation, and every image and slice is to be read as new.
- **P3-10 — the docs said notes close "once publishing has begun".** The README (its `409` row and its notes paragraph) and `api-gaps.md` (the route row and gap 3.9) now use the spec's words, "once `finalize_report` has started", say how that is read — from the run's published decision to publish — and cite ambiguity 5 (Task 8). DESIGN.md's note paragraph (Task 12) and the Global Constraints' API line use the same words. Code docstrings and test names that describe the same moment in the implementation's terms are unchanged.
