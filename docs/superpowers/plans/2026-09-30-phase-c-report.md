# Notes, progress, report and Stop — Phase C (the report) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status** revised 2026-09-30 for Fable's review 1 (`.superpowers/reviews/2026-09-30-plan-c-review-1.md`: approved with changes; every finding resolved under "Review 1: how each finding was resolved") and the human's ruling D40 on its I2; written the same day by the spec-plan-author agent from the approved spec at `73b4d7a6` (draft `bd48725c`). One item needs the human's eye (Open issues, O-1). · **Branch** `feat/notes-progress-report-stop`, executed after Phases D and A are merged into it (spec §9: D → A → C → B).

**Goal:** The published report answers first — a direct answer of at most two sentences, then one checked line per topic and one per reader note with its result — puts a merged, labelled Key figures (or Options compared) table after the topics, and reads in the console as one card per section with a contents list, so a reader sees the answer, the evidence per topic and what became of their notes at a glance.

**Architecture:**
- **Writer** (`agents/report_writer.py`, spec §7.1–§7.3). The bottom-line request prints the reader's one-time-check answers and heads each checked section `## {coverage_id} · {title}`; the reply is `sentences` (the direct answer, at most `MAX_ANSWER_SENTENCES = 2`) plus `topics` (one `TopicLineDraft` per listed topic). Answer sentences and topic lines are checked by the same Statement Check and keep every existing honesty rule; a `BottomLineLayout` on the composition records which kept statements are the answer and which are whose topic line. The fallback builds the same shape from the sections (one checked point per topic, `assembled = true`). Each section gets a `short_title`, the topic line's label.
- **Renderer** (`agents/report.py`, `agents/report_table.py`, spec §7.4–§7.5). `report_outline(composition)` lists the `##` headings in their new order — bottom line, topics, Key figures or Options compared, what we couldn't confirm, sources — and `render_written_report` prints from it; the evidence line carries the reader's answers. `key_figures_table` replaces `findings_table`: labels from the sub-topic a row mostly answers — a row with no named item labelled by the source that reported it (D40) — values from one passage merged, one row per label, at most ten. At publication the finalizer stamps `reader_note_lines` from each note's terminal outcome (`graph/note_outcomes.py`, moved below the API so the graph can read it), and the bottom line prints them with ✓/✗.
- **Console** (`web/`, spec §7.6). `/status` carries `report_outline`; `ReportBody` splits the Markdown at its `##` headings into cards paired with the outline, with a contents list that is a sticky rail from a 1310 px report stage and a sticky chip row below it, the current entry marked as the reader scrolls; the "Your notes" block is gone.

**Tech Stack:** Python 3.12 (`.venv`), pydantic 2, LangGraph 1.2.10, pytest 9 + pytest-asyncio, ruff; Node v24, Next.js 16, React 19, react-markdown 10 + remark-gfm (mdast types only), Vitest 5 + Testing Library + jsdom, Playwright 1.63 (Chromium) against the API in replay mode, TypeScript 7. Windows 11; every command below is for PowerShell in the main checkout.

**Spec:** `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` (commit `73b4d7a6`, reviewed clean by Fable in three rounds) — Phase C only: §7 (7.1–7.8), §4 item 5 (what C takes from A), §3.7–§3.8 (the ground truth §7 cites), AC22–AC27, AC36 and AC34's bottom-line part, the Phase C rows of §11.1–§11.3, and R5. Decisions D13–D16, D20 (its bottom-line part), D27, D28, D32, D36, D37, D19 and D40 (the human's ruling of 2026-09-30 on this plan's review, recorded in the spec's §2 and §7.4) are closed; nothing here reopens them. Where this plan had to choose, the choice is under "Spec ambiguities resolved here"; the one statement that cannot be kept exactly as worded is under "Open issues".

**Sources of truth.** The spec's §2 decisions, then its §4 contracts, then its §7. Phases D and A are merged before this plan starts; this plan anchors on the text their plans leave — `docs/superpowers/plans/2026-09-30-phase-d-stop.md` (as revised in `996a22a1`) and `docs/superpowers/plans/2026-09-30-phase-a-notes.md` (as revised in `796e88b8` and `e07a8f9c`) — and Task 1 checks every piece it builds on before anything is edited. Names taken from A, exactly as spec §4 item 5 gives them: `is_research_note`, `has_steering_kind`, `steering_view`, `steering_notes` (`agents/reader_notes.py`), `note_outcome(note_id, state, *, terminal)` and `note_steering_outcome(note_id, state, *, terminal, note=None)` (`api/notes.py`, moved here to `graph/note_outcomes.py` with a re-export), `ReaderNote.short` (with A's `MAX_NOTE_SHORT_CHARS = 24` and `note_short_label`). Every `path:line` below was read at `73b4d7a6` (whose code is `f4282818`'s) unless it says "on the D+A tree", which means the tree the two plans leave.

**Evidence.** Planning executed this document's blocks on 2026-09-30:
- A scratch tree was built from `git archive` of the branch (code `73b4d7a6`'s), then every edit block of the Phase D plan as revised in `996a22a1` (Tasks 2–9) and of the Phase A plan as revised in `796e88b8`/`e07a8f9c` (Tasks 2–10) was applied with an anchor-exact applier; all applied with no problem. On that D+A tree the full suite printed `5017 passed, 2 deselected`, Vitest `32` files and `259` tests, and the five agent fingerprints `['3934bea57f61', 'b9caf536e3f0', 'cc5a310b0aa0', '4a3d56fab932', 'a27544eee344']` (planner, researcher, source evaluator, evidence verifier, report writer): A re-pinned the planner, researcher, source evaluator and writer (at `73b4d7a6` they read `d1ba46ce147f`, `a8c9528f0c20`, `24809aa975a3` and `6e1aedc2888e`, `tests/test_evaluation/test_config.py`), and only the verifier's is unchanged since `73b4d7a6`.
- Every block of Tasks 2–12 was then applied in order to that tree, each step's tests run as the step says, and every `Expected:` line of Tasks 1–10 and 12 is what that run printed (pytest with `PYTHONPATH=src` on the export, so the export's code was the code under test). The finished tree: `5069 passed, 2 deselected`; Vitest `33` files, `276` tests; `typecheck` clean; `check:css` `OK`; pyflakes findings one fewer than before and none new. Task 1 Step 3's check of every anchor printed `problems: 0` on that D+A tree.
- Not run in planning, because they start replay sessions and the dispatch forbade running any research session: Task 11's Playwright runs and captures. Their specs were type-checked (`npm run -s typecheck` covers `e2e/`) and listed (`npx playwright test --list`: chromium `76 tests in 21 files`, visual `17 tests in 1 file`, from `68`/`20` and `14`/`1`); their pass/fail outcomes are marked **[not run in planning]** with the reasoning behind each.

## Global Constraints

- **Where.** Branch `feat/notes-progress-report-stop`, the Windows main checkout, after Phases D and A are merged. Tasks run strictly in order 1 → 13, one at a time. Commit after every task that changes tracked files (Tasks 1 and 13 change none). Phase order is "1. D — Stop … 2. A — Notes … 3. C — Report … 4. B — Progress" (spec §9).
- **No live model, no secrets.** Never run the live CLI, the API in `--mode live`, or anything that calls a model provider or Tavily. Never read, create, print or commit `.env` or any `.env.*` file. Python runs are pytest; the API runs only in `--mode replay`, and only under Playwright in Task 11. Out of scope: "A live, paid run (governed by the existing spend rules)" (spec §1.3).
- **The decisions this plan builds (spec §2, verbatim):**
  - D13: "**(C1)** The bottom line is a direct answer first (1–2 sentences, shaped by the reader's one-time-check answers), then one cited line per planned topic in plan order (labelled with a short topic name), then one line per note with ✓/✗ and how it was handled. Every existing honesty rule of `BOTTOM_LINE_INSTRUCTION` stays."
  - D14: "**(C2)** When the bottom-line call fails, or every sentence is refused, the fallback builds the same shape from the sections — one kept, checked sentence per topic plus each note's status, labelled as assembled from the sections — replacing the "first point of required parts" rule and keeping its floor and dispute protections."
  - D15: "**(C3)** The figures table moves out of the bottom line into its own "Key figures" section after the topics. Each row is labelled item · measure (never a raw snippet); rows about the same item merge ("4.7 of 5 · 20 reviews"); about 10 rows at most; the full list stays in the evidence log."
  - D16: "**(C4)** Layout = `Report.dc.html` B+C: each section its own card on the page ground (bottom line card first, then topic cards with eyebrow "Topic n of N" / "· from your note", Key figures, What we couldn't confirm); a contents list to the left that jumps to headings with the current one marked; on a phone the list becomes a horizontally scrolling chip row under the header. The Review / Coverage / Evidence rail stays. The Markdown report file gets the same structure (heading order)."
  - D20 (its bottom-line part): "Reviewing and the bottom-line note line show both results."
  - D27: "**(O3, default accepted)** The options table (comparison questions) also leaves the bottom line, as "Options compared" (I) in the same slot after the topics."
  - D28: "**(O4 and review M15, default accepted)** The contents rail shows only when the report stage is at least 1310 px wide; below that, the chip row. At the 1252 px capture width, and at 1568 px with the sidebar expanded, the report shows chips (§7.6 gives the viewport arithmetic)."
  - D32: "**(O10, default accepted)** On a phone, Key figures shows the source under each row's label."
  - D36: "**(O16, default accepted)** Key figures merges values from one passage (one primary finding) and shows one row per label."
  - D40 (the human's ruling of 2026-09-30 on this plan's review, I2; amends D36 and §7.4 items 2–3): "When a fact row has no named item (subject is None), the Key figures label falls back to the source that reported it, e.g. "Tripadvisor · Rating, 2024", so figures from different findings keep separate rows; merging still only happens for the same item from the same passage; one row per label and the 10-row cap stay."
  - D37: "**(O17, default accepted)** A bottom-line note line whose outcome is not checked carries no mark."
  - D19: "DESIGN.md theme rules hold: tokens only; colour is status (green active/ok, amber warn, red danger, purple only on the one primary button); one surface per region; motion tokens; reduced motion turns movement into fades; unknown values read "not yet", never 0, —, or null."
- **Exact values (spec §7.1–§7.6).** `MAX_ANSWER_SENTENCES = 2`; no name `MAX_BOTTOM_LINE_SENTENCES` remains in `src/` or `tests/` (AC36); a short title or a note's short is 1–3 words and at most 24 characters; `MAX_KEY_FIGURE_ROWS = 10`; Key figures columns "What" | "Figure" | "Source"; the contents rail from a report-stage width of `1310` px (176 + 32 + 770 + 32 + 300); the current-section line and `scroll-margin-top` `var(--topbar) + 56px + var(--space-4)`; the bottom line's key column `132px`; `--report-card-w` `calc(var(--reading-max) + 2 * var(--space-6) + 2px)`.
- **Copy (spec §7.1–§7.5, used as written; (I) marks the spec's illustrative copy).** The bottom-line prompts and reply examples are the spec's §7.1 text byte for byte (Task 3's blocks). Refusal reasons: "over the direct answer's two sentences", "a line for a topic the request did not list", "a second line for one topic", "a topic line cites a finding its topic does not". Fallback errors (I): "The bottom-line draft failed twice; one checked section point per topic stands in for it." and "Every drafted bottom-line sentence was refused; one checked section point per topic stands in for it.". Assembled line (I): "*Assembled from the sections below; the summary could not be written this time.*". Note label: "Your note · {short}". Note-line texts (I) as §7.2 lists them (Task 7). Headings: "Bottom line", "Key figures", "Options compared" (I), "What we couldn't confirm", "Sources"; contents labels "Not confirmed", "{short} (your note)"; eyebrows "Topic {i} of {N}" and "Topic {i} of {N} · from your note". Key figures caption: "Showing {k} of {n} verified figures; all are in the evidence log.".
- **CSS.** `web/app/globals.css` lines 1–1131 stay the prototype's CSS verbatim; new rules go at the end of the file, inside the app-only section, with no colour literal: `Push-Location web; npm run -s check:css; Pop-Location` prints `OK`. The contents list never uses the class `.rail`, which is the Review rail's (`web/e2e/layout.spec.ts:47` measures `.rail` at 300 px).
- **Fingerprints and digests are re-pinned, never typed.** Task 1 records the five agent fingerprints as baselines (B-planner, B-writer, …). A task that changes an agent's prompt text or a replay run's requests runs the re-pin helper, which recomputes each value exactly as its test does and rewrites only those that moved; its Expected line names which keys move and which stay, with the value the planning run printed for reference. A replay run's request count is the baseline's own and no task of this plan changes it: Task 1 records both counts (46 and 29 on the D+A tree; different if the latency branch's verifier batch size landed first). The latency workstream (D38) may land first and move a baseline; the moves this plan makes are the same keys either way.
- **Viewports.** `1252 × 853` desktop, `390 × 844` phone, `1920 × 1080` for the rail capture; captures `fullPage: true` (spec §11.3).

### Conventions every task uses

- **Shell.** PowerShell 5.1, from the repository root. A `web` command block starts with `Push-Location web` and ends with `Pop-Location`. Helper scripts live in `.superpowers\sdd\2026-09-30-phase-c\` (git-ignored, `.gitignore:227`); Task 1 creates them.
- **Line endings.** The checkout has `core.autocrlf=true`, so tracked files are CRLF on disk; every anchor below is written with LF. Match the text, not the line ending: the Edit tool and Python's `read_text` both do, and the helpers read with universal newlines.
- **Python.** `.venv\Scripts\python.exe -m pytest <files> -q`. A full run is `.venv\Scripts\python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config` (about 110 s): that test loads the shipped configuration strictly and needs the real secrets, which must not be read; `2 deselected` is it and the one `live` test `pyproject.toml` deselects.
- **Group test files by directory.** List every `tests/test_agents/` file of a pytest command together. On the D+A tree, `tests/test_agents/test_reader_notes_writing.py` listed after a file from another directory that itself follows a `tests/test_agents/` file errors with `fixture 'tracker' not found` (pytest loses `tests/test_agents/conftest.py` for it); this is independent of Phase C and every command below avoids it.
- **Web.** `npx vitest run <files>` for named files, `npm test` for all, `npm run -s typecheck` (it covers `test/` and `e2e/`), `npm run -s check:css`; `npm run test:e2e` builds then runs Playwright's `chromium` project, `npm run capture:visual` its `visual` project. Playwright starts the replay API on 8010 and the app on 3010 and 3011; nothing may be listening on them first.
- **Edits.** "`path` — replace" then two blocks replaces the first block's exact text, which must occur exactly once, with the second's. "replace the lines from the one starting X up to, not including, the one starting Y" replaces whole lines between two anchors. "Replace the whole of `path` with:" rewrites a file. "Create" writes a new file; "Append to" adds the block at the end. Task 1 proves, in memory, that every anchor occurs exactly once when its turn comes; if one is not found during execution, stop and report it, never improvise a nearby match.
- **TDD.** Each task's tests are written first and run failing; Playwright specs and captures are verification (Task 11).
- **Commits.** `git add <paths>` then `git commit -m "<type>(<scope>): <what>"`, never `git add -A` (this checkout holds untracked work that is not this plan's). End each message with the attribution trailer your session requires, and push if your session's rules say to.

## Decisions this plan implements

| # | Decision (spec §2; quoted under Global Constraints) | Where |
|---|---|---|
| D13 | The bottom line: a direct answer, one line per topic, one line per note | Tasks 3, 4, 5, 7 |
| D14 | The fallback in the same shape, floor and dispute protections kept | Task 4 |
| D15 | Key figures after the topics, labelled, merged, about 10 rows | Tasks 5, 6 |
| D16 | Cards, a contents list that jumps and marks the current section, chips on a phone; the Markdown in the same order | Tasks 5, 8, 9, 10, 11 |
| D20 | A mixed note's bottom-line line shows both results | Task 7 |
| D27 | Options compared in the same slot | Task 5 |
| D28 | The rail from a 1310 px report stage, chips below | Tasks 9, 10, 11 |
| D32 | On a phone, Key figures shows the source under each label | Tasks 10, 11 |
| D36 | Merge within one passage, one row per label | Task 6 |
| D40 | A row with no named item is labelled by the source that reported it | Task 6 (and the spec's §2 and §7.4, amended with this revision) |
| D37 | No mark on a not-checked note line | Task 7 |
| D19 | Theme rules | Tasks 10, 12 |

## Review Focus

1. **The reply contract** (Task 3): the request's `# Reader answers` and `## {coverage_id} · {title}` headers, the two reply examples (each validates, names only its own headers' topics, marks are spans of their point), the third-answer refusal and the re-ask text. → `test_bottom_line_request_reader_answers_and_ids`, `test_bottom_line_examples_valid_and_name_topics`, `test_answer_overflow_refused`, `test_missing_outcome_reask_text`.
2. **Topic lines and the fallback** (Task 4): a topic line cites only its own topic's labels, one per topic, never split; answer and topic lines keep separate restatement guards; the fallback moves one checked point per topic (a section it empties loses its card). → `test_bottom_line_topic_line_rules`, `test_a_topic_line_may_restate_the_answer_fact`, `test_fallback_one_line_per_topic`, `test_fallback_move_drops_emptied_section`.
3. **One outline, one Markdown** (Task 5): `render_written_report` prints its headings from `report_outline`, so the web can pair them by position. → `test_report_outline_matches_headings`, `test_markdown_heading_order`.
4. **Key figures** (Task 6): the label rule — with D40's source label for a row with no named item — the passage-level merge, one row per label, the cap and the caption, on the latte run's real rows and on the replay default case. → `test_key_figures_labels_and_merge_latte`, `test_key_figures_measure_rule`, `test_a_row_with_no_named_item_is_labelled_by_its_source`, `test_the_replay_default_case_labels_its_figures_by_their_sources`.
5. **Note lines at publication** (Task 7): stamped from `note_outcome(..., terminal=True)` outside the review's fingerprint; marks per D20/D37. → `test_note_lines_stamped_at_publication`, `test_mixed_note_line_both_results`, `test_fingerprint_ignores_note_lines`.
6. **The cards** (Task 10): the plugin that splits each bottom-line row into key and line, the Key figures phone copy, the contents' rail/chips decision and the jump's focus (a memoised card, so the focused heading survives the current-entry update).

## Spec ambiguities resolved here

1. **The outcomes move below the API** (spec §4 item 5, §7.2). §7.2 has the finalizer call `note_outcome(..., terminal=True)` and `note_steering_outcome`, which A defines in `api/notes.py`; `graph` cannot import `api` (`api/__init__.py:3` imports `api.app`, which builds the app at import, `api/app.py:539`, and imports `deep_research.main`, `api/app.py:57`, which imports `deep_research.graph`, `main.py:26-27`). Task 2 moves the two functions, their private helpers and `NoteOutcome` unchanged to `graph/note_outcomes.py` with a script that moves whatever text A left, and `api/notes.py` imports the three names back, so A's callers and tests are unchanged.
2. **`ReaderNote.short` is A's.** §4 item 5 gives it to whichever of A and C lands first; A's plan (its Task 3) adds it with `MAX_NOTE_SHORT_CHARS` and `note_short_label`, so this plan only reads it, and Task 1 stops if it is missing.
3. **Each section's reply example carries `short_title`.** §7.1 adds the field to `SectionDraft` and asks the rules for it; `tests/test_agents/test_tool_free_prompts.py` requires every static example to match its schema's fields, so the two `_SECTION_REPLY_EXAMPLES` gain `"short_title":"Closure"` and `"short_title":"Value for money"`.
4. **Topic lines are never split.** §7.1 says a topic's line is one line; a line over `MAX_POINT_CHARS` is refused rather than split (`split=False`), and its topic keeps no line.
5. **Answer and topic lines keep separate restatement guards.** Example 1 of §7.1 has topic-02's line restate the answer's fact; one shared guard would refuse it, so each keeps its own row set.
6. **Topic-line flight keys are `BT01`… and `RT01`…** (answer keys stay `B`/`R`); the replay Statement Check double answers `(?:S|P\d+\.|BT|RT|B|R)` labels so replay checks them.
7. **The order the bottom line prints** (§7.2 "summary"): plan topics in plan order, then note topics by note number (`n1` first); `_topic_line_order` reads a note topic's number from its `note-n{k}` coverage id.
8. **A note topic's contents label** is the section's short title plus " (your note)", as §7.5 says; the bottom line's label for the same topic is "Your note · {short}" (the note's own short), as §7.2 says.
9. **Key figures' label capitalises only a first lower-case letter**, so a brand such as "iJava" keeps its case; a row whose label would be "stated figure" with no item is not eligible (§7.4 item 2, which D40 leaves as it is); merged rows that end with one label keep the first by the cap's priority (D36).
10. **D40's source label** (the human's ruling on this plan's review, I2). "No named item" is read as the label rule's own: `subject` is None, or it starts with a pronoun, which §7.4 item 2 already treats as no item; both used to print a measure-only label, and both now take the source. "The source that reported it" is the name the Source column prints with its date left off — the row's organisation, or the page's credited publisher (else its site) when the organisation is the page's own site or the figure is unattributed — except that a relayed figure is labelled by the organisation it is credited to ("EIA", not "EIA, reported by energi.media"). The label reads `{Source} · {Measure}` with the measure capitalised as the measure-only label was, matching the ruling's example "Tripadvisor · Rating, 2024"; `, {period}` follows as before. Merging is unchanged — (label, primary finding, kind) — so one source's values from one passage still merge, and one row per label still keeps the first of one source's rows from different passages. On the replay default case the five subject-less figures come from five findings at five sources: they print as five rows ("Acme Institute 17 · Rate, 2024", "Acme Institute 2 · Value, 2024", "Independent Bureau 2 · Value, 2024", "Acme Institute 3 · Value, 2024", "Independent Bureau 3 · Value, 2024"), where the measure-only labels printed two; `test_the_replay_default_case_labels_its_figures_by_their_sources` pins them.
11. **`findings_table` is deleted, not kept beside `key_figures_table`.** Its private helpers go with it (`_select_rows`'s priority survives as `_row_priority`). The five tests that pinned its quoted-snippet labels and its "What was measured" column go with it: `test_rival_rows_both_get_quoted_form`, `test_row_with_no_subject_is_quoted_even_without_a_rival`, and the three `test_period_resolved_from_*` tests, which pinned P3-3's "(counted from the release date, …)" qualifier and `_what_was_measured`'s "({scope})" suffix — the Key figures label carries neither, so the period basis and scope leave the reader table, and the evidence log keeps `period_resolved_from` ("period resolved from the page date …", `agents/report.py:845-846`). The others now read `key_figures_table`.
12. **`noteCaption` is deleted** (review 1, M3). Once the report's "Your notes" block is gone (Task 10) no component calls A's `noteCaption` (`web/lib/notes.ts`); Phase B's Reviewing rows use their own words table, so Task 10 deletes the function and its `web/test/notes.test.ts` case. `OUTCOME_TEXT` stays: its own test pins it.
13. **A composition with no layout** (written before this change) prints its bottom line as one paragraph and then any note lines; the evidence line's answers are printed only on the "Evidence as of …" form, never on "No source could be checked.".
14. **The web's fallback.** With no outline, or one whose headings do not match the chunks one to one, every card is kind `section`, id `rep-sec-{n}`, its heading as the eyebrow and no topic number (§7.6). Only the Bottom line card runs the bottom-line plugin; every other card runs the Key figures plugin, which acts only on a table whose header reads What / Figure / Source.
15. **The contents mode is decided twice, on one width.** `ReportBody` sets `data-contents` from `#stage-report`'s `clientWidth` (a `ResizeObserver`), and the rail rules sit under `@container report (min-width:1310px)` on the same element; in rail mode the report group, its head and the Evidence view widen together to `176px + 2 × --space-8 + --report-card-w + --rail` so `layout.spec.ts`'s alignment checks hold.
16. **A click holds the current entry** until the jump's `scrollend`, the reader's own wheel, touch or key, or 1.5 s — whichever is first — so a jump to a card the page cannot scroll to the top (Sources, at the end) still marks it current.
17. **Topic titles keep the app's `.prose h2` size** (`web/app/globals.css:828`): §7.6's port table does not list the canvas's `.rp h2`, and the app's report already sets heading sizes.
18. **The reader-notes e2e and capture.** Replay never applies a note (api-gaps 3.9; `web/README.md:38-40`), so `e2e/notes.spec.ts` and `12-report-notes` now prove the absence of the old block and of any note line; the note lines' look is proved by Vitest and by one Playwright test that serves a noted report through a route (precedent: `web/e2e/layout.spec.ts:204`).
19. **Docs beyond §7.8.** The API reference (`README.md`, `docs/design/api-gaps.md`) gains `report_outline`, `DESIGN.md` §2.A's order sentence is updated, and `web/README.md` names the new spec and captures, and its `capture:visual` line counts the new captures (review 1, M5); §7.8's three rows are done as written.

## Open issues

- **O-1 (needs the human's eye, not a decision): the latte fixture's source exists only in this checkout.** AC25's fixture is generated from `output/report-a02a75fd75d44d8481f34953a4ff52e1-0-quality.json`, which git ignores (`.gitignore:224`). Task 6 generates and commits `tests/fixtures/latte-key-figures.json` from it in this checkout (the spec's plan); a worktree or another machine cannot regenerate it, and the committed fixture is what the tests read. The fixture names its own provenance: its `source` key holds the record's path, which carries the session id (`a02a75fd75d44d8481f34953a4ff52e1`), and its `question` key the session's question. If the output file is gone when Task 6 runs, stop and report it.
- **O-2 (coordination).** Phase B lands after C and changes `check_statements`' signature (`on_batch`). This plan adds no new substitute: its new tests reuse `_FakeChecker` (`tests/test_agents/test_report_writer.py:1131-1132`, one of the five §3.6 lists) through a fixture, so B's change to `_FakeChecker.__call__` covers them; B's grep will list the new fixture's `monkeypatch.setattr(... check_statements, fake ...)` line in `tests/test_agents/test_report_bottom_line.py`.

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/graph/note_outcomes.py` (new) | `NoteOutcome`, `note_outcome`, `note_steering_outcome` (moved from `api/notes.py`); `report_note_lines` and its texts | 2, 7 |
| `src/deep_research/api/notes.py` | re-exports the moved names | 2 |
| `src/deep_research/graph/__init__.py`, `graph/nodes.py` | exports; `_terminal_artifacts` stamps `reader_note_lines` | 2, 7 |
| `src/deep_research/utils/types.py` | `ReportSection.short_title`, `SectionDraft.short_title`, `TopicLineDraft`, `BottomLineDraft.topics`; `NOTE_LABEL_PREFIX`, `note_label`, `BottomLineTopic`, `BottomLineLayout`, `ReportComposition.bottom_line`/`.reader_answers`; `ReportOutlineKind`, `ReportOutlineEntry`; `NoteLineOutcome`, `ReportNoteLine`, `ReportComposition.reader_note_lines` | 3, 4, 5, 7 |
| `src/deep_research/agents/planner.py`, `agents/__init__.py` | `reader_answer_lines`; exports | 3, 5, 6 |
| `src/deep_research/agents/report_writer.py` | the request, the reply contract, topic lines, the layout, the fallback, short titles | 3, 4 |
| `src/deep_research/agents/report.py` | `report_outline`, the heading order, the evidence line's answers, the bottom line's list and note lines, the Key figures Source cell | 5, 6, 7 |
| `src/deep_research/agents/report_table.py` | `key_figures_table`, `merge_key_figures`, `KeyFigureGroup`, `MAX_KEY_FIGURE_ROWS`; D40's source label (`_who_name`, `_label_source`) | 6 |
| `src/deep_research/e2e_evaluation/replay.py` | the section and bottom-line doubles; the Statement Check double's topic-line keys | 3, 4 |
| `src/deep_research/api/models.py`, `api/sessions.py` | `ReportOutlineEntryResponse`, `ResearchSessionResponse.report_outline` | 8 |
| `tests/test_graph/test_note_outcomes.py`, `tests/test_agents/test_report_bottom_line.py`, `test_report_markdown.py`, `test_key_figures.py`, `tests/test_graph/test_note_lines.py` (new); `tests/fixtures/latte-key-figures.json` (new) | the tests of §11.1's Phase C row | 2–7 |
| `tests/test_agents/test_report_writer.py`, `test_tool_free_prompts.py`, `test_planner_reader_answers.py`, `test_report_layout.py`, `test_report.py`, `test_report_table.py`, `tests/test_e2e_evaluation/test_replay_doubles.py`, `tests/test_imports.py`, `tests/test_api/test_sessions.py`; re-pins in `tests/test_evaluation/test_config.py`, `tests/test_graph/test_reader_notes_replay.py` | §11.2's Phase C rows | 2–8 |
| `web/lib/api.ts`, `web/lib/report.ts` (new) | `ReportOutlineEntry`; splitting, pairing, the evidence line's parts, the contents' helpers | 9 |
| `web/components/ReportBody.tsx`, `ReportStage.tsx`, `web/app/globals.css` | the cards, the contents list, the plugins; the props; the CSS | 10 |
| `web/lib/notes.ts` | `noteCaption` deleted (no caller once "Your notes" is gone) | 10 |
| `web/test/report.test.ts` (new), `web/test/components/report-body.test.tsx`, `reader-notes.test.tsx`, `report-stage.test.tsx`, `web/test/notes.test.ts` | Vitest | 9, 10 |
| `web/e2e/report-layout.spec.ts` (new), `report.spec.ts`, `layout.spec.ts`, `notes.spec.ts`, `visual.spec.ts` | Playwright and captures | 11 |
| `README.md`, `docs/design/api-gaps.md`, `docs/design/DESIGN.md`, `docs/superpowers/specs/2026-09-25-consumer-report-format.md`, `web/README.md` | the API reference; the design record; the superseding note; running the app | 8, 12 |

## Task Right-Sizing

Thirteen tasks, each with its own test cycle and a deliverable a reviewer could accept or reject alone: the pre-flight (1); the outcome move, a pure refactor with its own import test (2); the writer's request and reply contract (3) apart from what it does with the reply (4), because each moves the writer's fingerprint and each has its own AC; the Markdown order (5), Key figures (6) and note lines (7), three renderer changes with separate ACs; the API field (8); the web's pure helpers (9) apart from the component and CSS (10), so the helpers are reviewed without the DOM; the end-to-end checks and captures (11), which only verify; the design record (12); and the final verification (13). Documentation that a code task makes true travels with it (the API reference in Task 8); the design record is its own task because it describes Tasks 5–11 together.

---

### Task 1: Starting point, helpers and baselines

**Files:** none tracked. Creates the git-ignored helpers in `.superpowers\sdd\2026-09-30-phase-c\`: `preflight.py`, `move_outcomes.py`, `check_blocks.py`, `repin.py`, `lint_compare.py`.

**Interfaces:**
- Consumes: Phases D and A merged into `feat/notes-progress-report-stop`.
- Produces: the proof that every block of Tasks 2–12 applies in order; the baselines B-py (full suite), B-web (Vitest), B-e2e and B-visual (Playwright listings), the five fingerprints (B-planner, B-researcher, B-evaluator, B-verifier, B-writer) and the two pinned replay runs' request counts (B-count-extra, B-count-redraft); `move_outcomes.moved(api_text) -> (api_text, graph_text, names)` (Task 2); `repin.py digests` and `repin.py fingerprints "<why>"` (Tasks 3–6); `lint_compare.py save|compare` (Task 13).

- [ ] **Step 1: Create the helpers**

Create `.superpowers/sdd/2026-09-30-phase-c/preflight.py`:

```python
"""Phase C, Task 1 Step 2: confirm that Phases D and A are on the branch and no Phase C work is.

Run from the repository root:
    .venv\\Scripts\\python.exe .superpowers\\sdd\\2026-09-30-phase-c\\preflight.py
"""
import re
from pathlib import Path

PRESENT = [
    ("D: SessionStatus has stopped", "src/deep_research/api/models.py", r'SessionStatus = Literal\[[^\]]*"stopped"'),
    ("D: waitTerminal accepts stopped", "web/e2e/support.ts", r"failed\|stopped\)"),
    ("A: is_research_note, has_steering_kind, steering_view, steering_notes", "src/deep_research/agents/reader_notes.py",
     r"def is_research_note\(.*def has_steering_kind\(|def has_steering_kind\(.*def is_research_note\("),
    ("A: steering_view and steering_notes", "src/deep_research/agents/reader_notes.py", r"def steering_view\(.*def steering_notes\("),
    ("A: note_outcome(..., *, terminal)", "src/deep_research/api/notes.py", r"def note_outcome\(\s*note_id: str, state: ResearchState \| None, \*, terminal: bool"),
    ("A: note_steering_outcome", "src/deep_research/api/notes.py", r"def note_steering_outcome\("),
    ("A: the outcomes sit between _VERDICT_OUTCOMES and note_records", "src/deep_research/api/notes.py",
     r"^_VERDICT_OUTCOMES: dict\[str, NoteOutcome\] = \{\n.*^def note_records\(\n"),
    ("A: ReaderNote.short", "src/deep_research/utils/types.py", r"\n    short: str = Field\(default=\"\", max_length=MAX_NOTE_SHORT_CHARS\)"),
    ("A: the writer imports NOTE_COVERAGE_PREFIX", "src/deep_research/agents/report_writer.py",
     r"from deep_research\.utils\.types import \(\n    NOTE_COVERAGE_PREFIX,\n"),
    ("A: the writer prints the steering views", "src/deep_research/agents/report_writer.py", r"steering_notes\(active_reader_notes\(state\.reader_notes\)\)"),
    ("A: noteCaption", "web/lib/notes.ts", r"export function noteCaption\("),
]
ABSENT = [
    ("no graph/note_outcomes.py yet", "src/deep_research/graph/note_outcomes.py", None),
    ("no web/lib/report.ts yet", "web/lib/report.ts", None),
    ("no MAX_ANSWER_SENTENCES yet", "src/deep_research/agents/report_writer.py", "MAX_ANSWER_SENTENCES"),
    ("no report_outline yet", "src/deep_research/agents/report.py", "def report_outline("),
]
for name, path, pattern in PRESENT:
    text = Path(path).read_text(encoding="utf-8")
    print(("OK       " if re.search(pattern, text, re.S | re.M) else "MISSING  ") + name)
for name, path, needle in ABSENT:
    p = Path(path)
    present = p.is_file() if needle is None else needle in p.read_text(encoding="utf-8")
    print(("PRESENT  " if present else "OK       ") + name)
```

Create `.superpowers/sdd/2026-09-30-phase-c/move_outcomes.py`:

```python
"""Phase C Task 2: move the note outcomes from api/notes.py to graph/note_outcomes.py.

notes-progress-report spec §7.2: the finalizer stamps the bottom line's note lines from
``note_outcome`` and ``note_steering_outcome``, and ``graph`` cannot import ``api``. This
moves, unchanged, whatever Phase A left between ``_VERDICT_OUTCOMES`` and ``note_records``
(and the ``NoteOutcome`` alias); ``api/notes.py`` then imports the three public names back.
It refuses a span that holds anything but the four functions below, or ``class NoteRecord``.
Run from the repository root: ``.venv\\Scripts\\python.exe .superpowers\\sdd\\2026-09-30-phase-c\\move_outcomes.py``.
"""
from pathlib import Path

API = Path("src/deep_research/api/notes.py")
GRAPH = Path("src/deep_research/graph/note_outcomes.py")
MOVED = ["note_outcome", "note_steering_outcome", "_research_outcome", "_verdict_outcome"]
HEADER = '''"""What a run concluded about each reader note (notes-progress-report spec §5.6).

Moved here unchanged from ``api/notes.py`` by Phase C, so the finalizer can read
each note's outcome when it stamps the report's note lines (spec §7.2): ``graph``
never imports ``api``, because importing ``deep_research.api`` builds the app
(``api/app.py``), which imports ``deep_research.main``, which imports this package.
``api/notes.py`` re-exports every name here, so each caller keeps its import.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
    ResearchState,
    active_reader_notes,
)

'''
FOOTER = '''

__all__ = [
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
]
'''
IMPORT_ANCHOR = "from deep_research.observability import "


def moved(api_text: str) -> tuple[str, str, list[str]]:
    """Return api/notes.py without the outcomes, the new module's text, and the moved functions."""
    alias_start = api_text.index("NoteOutcome: TypeAlias = Literal[\n")
    alias_end = api_text.index("\n]\n", alias_start) + len("\n]\n")
    alias = api_text[alias_start:alias_end]
    text = api_text[:alias_start] + api_text[alias_end:]
    start = text.index("_VERDICT_OUTCOMES: dict[str, NoteOutcome] = {\n")
    end = text.index("def note_records(\n", start)
    body = text[start:end].rstrip("\n") + "\n"
    if "\nclass NoteRecord(" in "\n" + body:
        raise SystemExit(f"{API}: class NoteRecord lies inside the span to move; it must stay in api/notes.py")
    names = [line.split("(")[0][4:] for line in body.splitlines() if line.startswith("def ")]
    if names != MOVED:
        raise SystemExit(f"{API}: the span holds {names}, not {MOVED}")
    text = text[:start] + text[end:]
    if text.count(IMPORT_ANCHOR) != 1:
        raise SystemExit(f"{API}: the import anchor {IMPORT_ANCHOR!r} is not there exactly once")
    text = text.replace(
        IMPORT_ANCHOR,
        "from deep_research.graph.note_outcomes import (\n"
        "    NoteOutcome,\n"
        "    note_outcome,\n"
        "    note_steering_outcome,\n"
        ")\n" + IMPORT_ANCHOR,
    )
    return text, HEADER + alias + "\n\n" + body + FOOTER, names


if __name__ == "__main__":
    if GRAPH.exists():
        raise SystemExit(f"{GRAPH} already exists")
    api_text, graph_text, names = moved(API.read_text(encoding="utf-8"))
    API.write_text(api_text, encoding="utf-8")
    GRAPH.write_text(graph_text, encoding="utf-8")
    print("moved:", names)
```

Create `.superpowers/sdd/2026-09-30-phase-c/check_blocks.py`:

```python
"""Check every edit block of the Phase C plan against the tree, in order, in memory.

Run from the repository root:
    .venv\\Scripts\\python.exe .superpowers\\sdd\\2026-09-30-phase-c\\check_blocks.py

Each block of Tasks 2-13 is applied to the text the earlier blocks left: an exact
"replace" must find its anchor exactly once; "replace the lines from the one starting X up
to, not including, the one starting Y" must find one X and a later Y; "Replace the whole
of" needs the file; "Create" needs it absent; "Append to" needs it present. Task 2's move
script is simulated where the plan marks it. Files are read with universal newlines, so the
checkout's CRLF files match the plan's LF anchors. Nothing is written.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from move_outcomes import API, GRAPH, moved  # noqa: E402

PLAN = Path("docs/superpowers/plans/2026-09-30-phase-c-report.md")
DASH = "—"
FENCE = r"(?P<f>`{3,5})[a-z]*\n(?P<body>.*?)\n(?P=f)\n"
FORMS = {
    "edit": re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? " + DASH + r" replace\n\n(?P<f1>`{3,5})[a-z]*\n(?P<old>.*?)\n(?P=f1)\n\nwith\n\n" + FENCE, re.S | re.M),
    "span": re.compile(r"^`(?P<path>[^`\n]+)`(?: \([^\n]*\))? " + DASH + r" replace the lines from the one starting `(?P<start>[^`\n]+)` up to, not including, the one starting `(?P<end>[^`\n]+)`, with\n\n" + FENCE, re.S | re.M),
    "whole": re.compile(r"^Replace the whole of `(?P<path>[^`\n]+)` with:\n\n" + FENCE, re.S | re.M),
    "create": re.compile(r"^Create `(?P<path>[^`\n]+)`:\n\n" + FENCE, re.S | re.M),
    "append": re.compile(r"^Append to `(?P<path>[^`\n]+)`:\n\n" + FENCE, re.S | re.M),
    "move": re.compile(r"^<!-- simulate: move_outcomes -->$", re.M),
}


def main() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    plan = plan[re.search(r"^### Task 2:", plan, re.M).start():]
    found = sorted(((m.start(), kind, m) for kind, rx in FORMS.items() for m in rx.finditer(plan)), key=lambda b: b[0])
    files: dict[str, str | None] = {}
    counts = dict.fromkeys(FORMS, 0)
    problems: list[str] = []

    def text(path: str) -> str | None:
        if path not in files:
            p = Path(path)
            files[path] = p.read_text(encoding="utf-8") if p.is_file() else None
        return files[path]

    for _, kind, m in found:
        counts[kind] += 1
        if kind == "move":
            api_text, graph_text, _ = moved(text(API.as_posix()) or "")
            files[API.as_posix()], files[GRAPH.as_posix()] = api_text, graph_text
            continue
        path, current = m["path"], text(m["path"])
        if kind == "edit":
            n = -1 if current is None else current.count(m["old"])
            if n != 1:
                problems.append(f"{path}: anchor found {n} times: {m['old'][:80]!r}")
                continue
            files[path] = current.replace(m["old"], m["body"])
        elif kind == "span":
            lines = [] if current is None else current.split("\n")
            starts = [i for i, line in enumerate(lines) if line.startswith(m["start"])]
            ends = [i for i, line in enumerate(lines) if starts and i > starts[0] and line.startswith(m["end"])]
            if len(starts) != 1 or not ends:
                problems.append(f"{path}: span start found {len(starts)} times, end {'found' if ends else 'missing'}: {m['start']!r}")
                continue
            files[path] = "\n".join(lines[: starts[0]] + m["body"].split("\n") + lines[ends[0]:])
        elif kind == "whole":
            if current is None:
                problems.append(f"{path}: missing for its replacement")
                continue
            files[path] = m["body"] + "\n"
        elif kind == "create":
            if current is not None:
                problems.append(f"{path}: already exists")
                continue
            files[path] = m["body"] + "\n"
        else:
            if current is None:
                problems.append(f"{path}: missing for its append")
                continue
            files[path] = current + m["body"] + "\n"
    for problem in problems:
        print("PROBLEM", problem)
    print(
        f"anchors: {counts['edit']} exactly once; spans: {counts['span']}; whole files: {counts['whole']}; "
        f"creates: {counts['create']} absent; appends: {counts['append']}; moves: {counts['move']}; problems: {len(problems)}"
    )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

The re-pin helper recomputes the two whole-run digests of `tests/test_graph/test_reader_notes_replay.py::PINNED_RUN_DIGESTS` and the four packet digests of `tests/test_agents/test_planner_reader_answers.py::PINNED_PACKETS` exactly as those tests compute them, and every agent fingerprint pinned in `tests/test_evaluation/test_config.py::PINNED_TARGET_PROMPT_FINGERPRINTS`; it rewrites only the values that moved and prints one line per value.

Create `.superpowers/sdd/2026-09-30-phase-c/repin.py`:

```python
"""Re-pin what a notes-progress-report Phase C task moves; run from the repository root.

    PYTHONPATH=src "$PY" .superpowers/sdd/2026-09-30-phase-c/repin.py digests
    PYTHONPATH=src "$PY" .superpowers/sdd/2026-09-30-phase-c/repin.py fingerprints "<why>"

``digests`` recomputes the whole-run request digests of
tests/test_graph/test_reader_notes_replay.py::PINNED_RUN_DIGESTS and the packet
digests of tests/test_agents/test_planner_reader_answers.py::PINNED_PACKETS exactly as
those tests compute them, rewrites every value that moved, and prints one line per value.
``fingerprints`` recomputes every agent's prompt fingerprint and rewrites each pin in
tests/test_evaluation/test_config.py::PINNED_TARGET_PROMPT_FINGERPRINTS that moved, with
a comment above it naming why and the move.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

RUN_FILE = ROOT / "tests/test_graph/test_reader_notes_replay.py"
PACKET_FILE = ROOT / "tests/test_agents/test_planner_reader_answers.py"
PIN_FILE = ROOT / "tests/test_evaluation/test_config.py"
DIGEST_NOTE = (
    "    # notes-progress-report Phase C re-pinned these values: its writer requests "
    "and the report\n    # the review reads changed (spec §7.1, §7.4, §7.5).\n"
)


def _note_once(text: str, opening: str) -> str:
    if DIGEST_NOTE in text:
        return text
    return text.replace(opening, opening + DIGEST_NOTE, 1)


def digests() -> None:
    import tests.test_agents.test_planner_reader_answers as answers
    import tests.test_graph.test_reader_notes_replay as runs
    from tests.test_api.replay_support import guarded

    text = RUN_FILE.read_text(encoding="utf-8")
    for constant, old_hex, old_count in re.findall(r'(?m)^    (\w+): \("([0-9a-f]{16})", (\d+)\),$', text):
        case_id = getattr(runs, constant)
        with tempfile.TemporaryDirectory() as tmp, guarded():
            _, sequence, _ = asyncio.run(runs.replay_packets(Path(tmp), case_id))
        new_hex, new_count = runs.run_digest(sequence)
        old_line = f'    {constant}: ("{old_hex}", {old_count}),'
        text = text.replace(old_line, f'    {constant}: ("{new_hex}", {new_count}),', 1)
        print(f"{case_id}: ({old_hex}, {old_count}) -> ({new_hex}, {new_count})")
    text = _note_once(text, "PINNED_RUN_DIGESTS = {\n")
    RUN_FILE.write_text(text, encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp, guarded():
        _, packets = asyncio.run(answers._replay_packets(Path(tmp), ()))
    text = PACKET_FILE.read_text(encoding="utf-8")
    for key, old_hex in re.findall(r'(?m)^    "([^"]+)": "([0-9a-f]{16})",$', text):
        new_hex = hashlib.sha256(packets[key].encode("utf-8")).hexdigest()[:16]
        text = text.replace(f'    "{key}": "{old_hex}",', f'    "{key}": "{new_hex}",', 1)
        print(f"{key}: {old_hex} -> {new_hex}")
    text = _note_once(text, "PINNED_PACKETS = {\n")
    PACKET_FILE.write_text(text, encoding="utf-8")


def fingerprints(why: str) -> None:
    from deep_research.evaluation.config import agent_prompt_fingerprint
    from deep_research.evaluation.models import AGENT_NAMES

    text = PIN_FILE.read_text(encoding="utf-8")
    for name in AGENT_NAMES:
        match = re.search(rf'(?m)^    "{name}": "([0-9a-f]{{12}})",$', text)
        if match is None:
            raise SystemExit(f"no pin line for {name}")
        old, new = match.group(1), agent_prompt_fingerprint(name)
        if old == new:
            print(f"{name}: {old} (unchanged)")
            continue
        comment = textwrap.fill(
            f"{why} Moved `{old}` -> `{new}`.", width=78,
            initial_indent="    # ", subsequent_indent="    # ",
        )
        text = text.replace(match.group(0), f"{comment}\n    \"{name}\": \"{new}\",", 1)
        print(f"{name}: {old} -> {new}")
    PIN_FILE.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:2] == ["digests"]:
        digests()
    elif sys.argv[1:2] == ["fingerprints"] and len(sys.argv) == 3:
        fingerprints(sys.argv[2])
    else:
        raise SystemExit(__doc__)
```

Create `.superpowers/sdd/2026-09-30-phase-c/lint_compare.py`:

```python
"""Record (Task 1), then compare (Task 13), the pyflakes findings of src and tests.

    .venv\\Scripts\\python.exe .superpowers\\sdd\\2026-09-30-phase-c\\lint_compare.py save
    .venv\\Scripts\\python.exe .superpowers\\sdd\\2026-09-30-phase-c\\lint_compare.py compare

Line numbers are dropped, so a finding that only moved is the same finding.
"""
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent / "ruff-before.txt"


def findings() -> list[str]:
    out = subprocess.run(
        [str(Path(".venv/Scripts/ruff.exe")), "check", "--select", "F", "src", "tests", "--output-format", "concise"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return sorted({re.sub(r":\d+:\d+:", ":", line) for line in out.splitlines() if re.search(r":\d+:\d+: F\d", line)})


if sys.argv[1:] == ["save"]:
    found = findings()
    BASE.write_text("\n".join(found) + "\n", encoding="utf-8")
    print(f"saved {len(found)} findings")
elif sys.argv[1:] == ["compare"]:
    before = {line for line in BASE.read_text(encoding="utf-8").splitlines() if line}
    after = set(findings())
    print("new:", sorted(after - before) or "none")
    print("gone:", sorted(before - after) or "none")
else:
    raise SystemExit(__doc__)
```

- [ ] **Step 2: Confirm that D and A are on the branch and that no Phase C work is**

Run:

```powershell
git status --short --untracked-files=no
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\preflight.py
```

Expected: `git status` prints nothing, then fifteen lines that all start with `OK`. A `MISSING` line means a piece of D or A this plan builds on is not there (for `ReaderNote.short`, spec §4 item 5 would give it to C): stop and report it. A `PRESENT` line means Phase C work is already on the branch: stop and report it.

- [ ] **Step 3: Check every block of this plan against the tree, in order**

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\check_blocks.py
```

Expected: `anchors: 192 exactly once; spans: 2; whole files: 2; creates: 9 absent; appends: 5; moves: 1; problems: 0`, and no `PROBLEM` line. A `PROBLEM` line names an anchor that is not in the tree D and A left: stop and report it; the remedy is a plan amendment, never a guessed anchor.

- [ ] **Step 4: Record the baselines**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
.venv\Scripts\python.exe -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print([f(n) for n in ('planner', 'researcher', 'source_evaluator', 'evidence_verifier', 'report_writer')])"
.venv\Scripts\python.exe -c "from tests.test_graph.test_reader_notes_replay import PINNED_RUN_DIGESTS as p; print(p)"
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\lint_compare.py save
Push-Location web; npm test; npm run -s typecheck; npm run -s check:css; npx playwright test --list --project=chromium | Select-Object -Last 1; npx playwright test --list --project=visual | Select-Object -Last 1; Pop-Location
```

Expected:
- the pytest line ends `passed, 2 deselected` with no `failed` or `error`: record its count as **B-py** (the planning D+A tree printed `5017 passed, 2 deselected`);
- five fingerprints: record them as B-planner, B-researcher, B-evaluator, B-verifier, B-writer (planning: `['3934bea57f61', 'b9caf536e3f0', 'cc5a310b0aa0', '4a3d56fab932', 'a27544eee344']`);
- the two pinned replay runs, each `(digest, request count)`: record the counts as **B-count-extra** and **B-count-redraft** (planning: `{'missing-target-triggers-one-extra-pass': ('03e113e5584707da', 46), 'scoped-redraft-after-a-named-defect': ('875313d15f3325f2', 29)}`; the counts differ if the latency branch's verifier batch size landed first). No task of this plan changes either count;
- `saved N findings` (planning: `26`);
- Vitest `Test Files  N passed`, `Tests  M passed` with no failure: record M as **B-web** (planning: `32` files, `259` tests); `typecheck` prints nothing; `check:css` prints `OK`;
- `Total: 68 tests in 20 files` and `Total: 14 tests in 1 file` (record them as B-e2e and B-visual if they differ). Listing starts no server.

If a baseline differs from the planning value because D or A landed differently, record the observed value: every later count shifts by the same amount. The increments this plan adds are B-py + 52, B-web + 17 tests (+1 file), B-e2e + 8 tests (+1 file), B-visual + 3.

- [ ] **Step 5: The five `check_statements` substitutes stay as they are**

Run:

```powershell
git grep -n -E "async def fake_check|def __call__\(self, provider|evidence_verifier\.check_statements[^_]" -- tests
```

Expected: six lines and nothing else — two in `tests/test_agents/test_report_reviewer.py` (each `"deep_research.agents.evidence_verifier.check_statements", consistent`, planning lines `948` and `2778`) and four in `tests/test_agents/test_report_writer.py`: `_FakeChecker`'s docstring (`1116`), its `__call__` (`1131`), its fixture's `monkeypatch.setattr("deep_research.agents.evidence_verifier.check_statements", fake, raising=False)` (`1155`) and the in-test `fake_check` (`2684`). These are the substitutes spec §3.6 lists, found by this pattern. Phase C does not change `check_statements`' signature; its new tests reuse `_FakeChecker` (Open issue O-2).

- [ ] **Step 6: No commit**

Nothing tracked changed.

---

### Task 2: The note outcomes move below the API

The finalizer must read each note's terminal outcome when it stamps the bottom line's note lines (spec §7.2, Task 7), and `graph` cannot import `api` (spec ambiguity 1). This task moves A's outcome functions, unchanged, to `graph/note_outcomes.py`.

**Files:**
- Create: `src/deep_research/graph/note_outcomes.py` (written by the move helper)
- Modify: `src/deep_research/api/notes.py` (the `NoteOutcome` alias, `api/notes.py:56-58` on the D+A tree; the span from `_VERDICT_OUTCOMES` up to `def note_records(`, `:371-470` on the D+A tree; its imports), `src/deep_research/graph/__init__.py` (imports, `__all__`), `tests/test_imports.py:495` (the graph submodule list)
- Test: `tests/test_graph/test_note_outcomes.py` (new)

**Interfaces:**
- Consumes: A's `NoteOutcome`, `note_outcome(note_id: str, state: ResearchState | None, *, terminal: bool) -> NoteOutcome`, `note_steering_outcome(note_id: str, state: ResearchState | None, *, terminal: bool, note: ReaderNote | None = None) -> NoteOutcome | None` and their private helpers `_VERDICT_OUTCOMES`, `_research_outcome`, `_verdict_outcome`; `move_outcomes.moved` (Task 1).
- Produces: `deep_research.graph.note_outcomes` with `NoteOutcome`, `note_outcome`, `note_steering_outcome` (the same objects `deep_research.api.notes` and `deep_research.graph` export). Task 7 adds `report_note_lines` to this module and anchors on the `__all__` block the helper writes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph/test_note_outcomes.py`:

```python
"""The note outcomes live below the API (notes-progress-report spec §5.6, §7.2).

The finalizer stamps the bottom line's note lines from ``note_outcome`` and
``note_steering_outcome``; ``graph`` cannot import ``api``, so the two live in
``graph/note_outcomes.py`` and ``api/notes.py`` re-exports them.
"""

from __future__ import annotations

import subprocess
import sys

from deep_research.api import notes as api_notes
from deep_research.graph import note_outcomes


def test_the_graph_reads_note_outcomes_without_importing_the_api() -> None:
    probe = (
        "import sys; import deep_research.graph.note_outcomes; "
        "print(any(name == 'deep_research.api' or name.startswith('deep_research.api.') "
        "for name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_the_api_re_exports_the_moved_outcomes() -> None:
    assert api_notes.note_outcome is note_outcomes.note_outcome
    assert api_notes.note_steering_outcome is note_outcomes.note_steering_outcome
    assert api_notes.NoteOutcome is note_outcomes.NoteOutcome
```

`tests/test_imports.py` — replace

```python
    submodules = ["errors", "events", "live", "nodes", "orchestrator", "state"]
```

with

```python
    submodules = ["errors", "events", "live", "note_outcomes", "nodes", "orchestrator", "state"]
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_note_outcomes.py tests/test_imports.py -q
```

Expected: `1 error` — `ImportError: cannot import name 'note_outcomes' from 'deep_research.graph'` while collecting `tests/test_graph/test_note_outcomes.py` (`Interrupted: 1 error during collection`).

- [ ] **Step 3: Move the outcomes**

<!-- simulate: move_outcomes -->

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\move_outcomes.py
.venv\Scripts\ruff.exe check --select F401 --fix src/deep_research/api/notes.py
```

Expected: `moved: ['note_outcome', 'note_steering_outcome', '_research_outcome', '_verdict_outcome']` (the helper stops instead, naming what it found, if the span between `_VERDICT_OUTCOMES` and `def note_records(` holds any other function or `class NoteRecord`), then `Found 4 errors (4 fixed, 0 remaining).` — the imports only the moved code used: `typing.Literal`, `has_steering_kind`, `is_research_note` and `NOTE_COVERAGE_PREFIX`. The helper writes `graph/note_outcomes.py` as its module docstring and imports, A's `NoteOutcome` alias, the moved block exactly as A left it, and

```python
__all__ = [
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
]
```

and inserts into `api/notes.py`, just above `from deep_research.observability import …`:

```python
from deep_research.graph.note_outcomes import (
    NoteOutcome,
    note_outcome,
    note_steering_outcome,
)
```

`api/notes.py`'s `__all__` keeps its three names, so `from deep_research.api.notes import note_outcome` still works everywhere.

- [ ] **Step 4: Export them from the graph package**

`src/deep_research/graph/__init__.py` — replace

```python
from deep_research.graph.live import LiveSink, bind_live_sink, publish_live
```

with

```python
from deep_research.graph.live import LiveSink, bind_live_sink, publish_live
from deep_research.graph.note_outcomes import (
    NoteOutcome,
    note_outcome,
    note_steering_outcome,
)
```

`src/deep_research/graph/__init__.py` — replace

```python
    "LiveSink",
    "ProgressHandler",
```

with

```python
    "LiveSink",
    "NoteOutcome",
    "ProgressHandler",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "note_dispositions",
    "note_pass_node",
```

with

```python
    "note_dispositions",
    "note_outcome",
    "note_pass_node",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "note_redraft_requested_event",
    "note_sub_topic",
```

with

```python
    "note_redraft_requested_event",
    "note_steering_outcome",
    "note_sub_topic",
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_note_outcomes.py tests/test_imports.py tests/test_api/test_notes.py tests/test_api/test_note_route.py -q
.venv\Scripts\ruff.exe check --select F src/deep_research/api/notes.py src/deep_research/graph/note_outcomes.py src/deep_research/graph/__init__.py
```

Expected: `85 passed` (A's outcome tables in `tests/test_api/test_notes.py` pass through the re-export unchanged), then `All checks passed!`.

- [ ] **Step 6: Commit**

```powershell
git add src/deep_research/graph/note_outcomes.py src/deep_research/api/notes.py src/deep_research/graph/__init__.py tests/test_graph/test_note_outcomes.py tests/test_imports.py
git commit -m "refactor(graph): move the note outcomes below the API so the finalizer can read them"
```

---

### Task 3: The bottom line's request and reply contract (AC22, AC36)

Spec §7.1 and §7.7: the request carries the reader's answers and `## {coverage_id} · {title}` headers; the reply is a direct answer of at most two sentences plus one topic line per listed topic; the prompts and reply examples are the spec's, byte for byte; sections gain a short title; the replay doubles reply in the new shape.

**Files:**
- Modify: `src/deep_research/utils/types.py` (`ReportSection` `:1588`, `SectionDraft`/`BottomLineDraft` `:1949-1960` on the D+A tree)
- Modify: `src/deep_research/agents/planner.py` (`render_reader_answers`, `:2685-2704` at `73b4d7a6`), `src/deep_research/agents/__init__.py` (imports and `__all__`)
- Modify: `src/deep_research/agents/report_writer.py` (imports; the ruling comment `:115-122`; `MAX_BOTTOM_LINE_SENTENCES` `:136`; `_SECTION_TITLE_CHARS` `:138`; the section rules and examples; `BOTTOM_LINE_SYSTEM_PROMPT` `:379`; `BOTTOM_LINE_INSTRUCTION` `:385`; `_BOTTOM_LINE_REPLY_EXAMPLES` `:442-469`; `ReportWriterTask` `:472`; `bottom_line_messages` `:1204`; `_section_title` `:1704`; `_run_part`; the fallback and finalize caps; the re-ask; `build_task` `:3248` — all on the D+A tree)
- Modify: `src/deep_research/e2e_evaluation/replay.py` (`_reply_SectionDraft` `:1378-1437`, `_reply_BottomLineDraft` `:1441-1466`)
- Test: `tests/test_agents/test_report_bottom_line.py` (new), `tests/test_agents/test_report_writer.py:21`, `:1051`, `tests/test_agents/test_tool_free_prompts.py:530-537`, `tests/test_agents/test_planner_reader_answers.py`, `tests/test_e2e_evaluation/test_replay_doubles.py`; re-pins in `tests/test_evaluation/test_config.py`, `tests/test_graph/test_reader_notes_replay.py:37-40`, `tests/test_agents/test_planner_reader_answers.py::PINNED_PACKETS`

**Interfaces:**
- Consumes: `ReaderAnswer` (`utils/types.py`), `state.reader_answers`; A's `build_task` (its `reader_notes=render_reader_notes(steering_notes(…))` argument stays).
- Produces:
  - `utils/types.py`: `ReportSection.short_title: str = ""`; `SectionDraft.short_title: str = ""`; `class TopicLineDraft(WriterPointDraft)` with `topic: str = ""`; `BottomLineDraft.topics: list[TopicLineDraft] = []`.
  - `agents/planner.py`: `reader_answer_lines(reader_answers: Sequence[ReaderAnswer]) -> list[str]` (exported from `deep_research.agents`).
  - `agents/report_writer.py`: `MAX_ANSWER_SENTENCES = 2` (exported, replacing `MAX_BOTTOM_LINE_SENTENCES`); `ReportWriterTask.reader_answers: list[ReaderAnswer]`; `bottom_line_messages(task, sections, *, previous=(), previous_topics: Mapping[str, str] = {}, defects=(), …)`; `_section_short_title(drafted_short_title: str, title: str) -> str`; `_FALLBACK_POINTS = 4` (interim; Task 4 replaces it).
  - Replay: `_reply_SectionDraft` sets `short_title` to the title's first two words; `_reply_BottomLineDraft` returns one answer sentence and one `TopicLineDraft` per `## {id} · ` block.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_report_writer.py` — replace

```python
    MAX_BOTTOM_LINE_SENTENCES,
```

with

```python
    MAX_ANSWER_SENTENCES,
```

`tests/test_agents/test_report_writer.py` — replace

```python
def test_bottom_line_system_prompt_states_the_sentence_bound():
    assert "two to four sentences" in BOTTOM_LINE_SYSTEM_PROMPT
```

with

```python
def test_bottom_line_system_prompt_states_the_sentence_bound():
    assert "one or two sentences" in BOTTOM_LINE_SYSTEM_PROMPT
    assert MAX_ANSWER_SENTENCES == 2
```

`tests/test_agents/test_tool_free_prompts.py` — replace

```python
from deep_research.agents.report_writer import (
    PartJob,
    ReportWriterTask,
    bottom_line_messages,
    section_messages,
)
```

with

```python
from deep_research.agents.report_writer import (
    _BOTTOM_LINE_REPLY_EXAMPLES,
    PartJob,
    ReportWriterTask,
    bottom_line_messages,
    section_messages,
)
```

`tests/test_agents/test_tool_free_prompts.py` — replace

```python
        (_SOURCE_SCORE_REPLY_EXAMPLES, SourceScoresDraft),
        (_judge_examples(), JudgeVerdict),
    )
```

with

```python
        (_SOURCE_SCORE_REPLY_EXAMPLES, SourceScoresDraft),
        (_judge_examples(), JudgeVerdict),
        (_BOTTOM_LINE_REPLY_EXAMPLES, BottomLineDraft),
    )
```

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
    assert composition.rejected_points == []
    # Both section points are kept and checked, so the bottom line -- drafted
    # only from checked section statements (spec §6.6) -- restates them both.
    assert [point.text for point in composition.summary] == [
        point.text for point in draft.points
    ]
```

with

```python
    assert composition.rejected_points == []
    assert draft.short_title == "Battery storage"  # the title's first two words (§7.7)
    # The bottom line answers with the first checked section statement
    # (notes-progress-report spec §7.7), in the section's own words.
    assert composition.summary[0].text == draft.points[0].text
```

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
@pytest.mark.asyncio
async def test_the_writer_double_caps_the_bottom_line_at_four_checked_statements() -> None:
    """Up to 4 of the checked section statements reach the bottom line, with
    their labels (spec §11.3), however many the section itself kept."""
    sources = tuple(
        page(f"finding{n}", value=str(n), unit="GW", period="2024") for n in range(1, 6)
    )
    completer = ReplayCompleter(scenario(*sources))
    task = writer_task(
        [(f"F{n:02d}", verified(source)) for n, source in enumerate(sources, start=1)]
    )

    composition = await compose_written_report(
        task, provider=completer, fingerprint=None,
    )

    kept_section_texts = [point.text for point in composition.sections[0].points]
    assert len(kept_section_texts) == 5
    assert len(composition.summary) == 4
    assert [point.text for point in composition.summary] == kept_section_texts[:4]
    assert all(
        point.statement is not None and len(point.statement.finding_ids) == 1
        for point in composition.summary
    )
```

with

```python
def test_the_writer_double_answers_once_and_drafts_one_line_per_topic() -> None:
    """notes-progress-report spec §7.7: the first statement of the first
    ``## {coverage_id} · {title}`` block is the one answer sentence, and the
    first statement of each block is that topic's line, with its labels."""
    sources = tuple(
        page(f"finding{n}", value=str(n), unit="GW", period="2024") for n in range(1, 5)
    )
    completer = ReplayCompleter(scenario(*sources))
    task = writer_task(
        [(f"F{n:02d}", verified(source)) for n, source in enumerate(sources, start=1)]
    )
    registry = dict(task.registry)

    def section(coverage_id: str, title: str, labels: list[str]) -> ReportSection:
        points = [
            ReportPoint(
                text=f"{label} statement.",
                statement=ReportStatement(
                    statement_id=f"S-{label}", text=f"{label} statement.",
                    finding_ids=[finding_fingerprint(registry[label])],
                ),
            )
            for label in labels
        ]
        return ReportSection(title=title, coverage_id=coverage_id, points=points)

    request = "\n".join(
        message.content
        for message in bottom_line_messages(
            task,
            [section("topic-01", "First part", ["F01", "F02"]),
             section("topic-02", "Second part", ["F03", "F04"])],
        )
    )

    draft = completer._reply_BottomLineDraft(request)

    assert [(point.text, point.finding_labels) for point in draft.sentences] == [
        ("F01 statement.", ["F01"]),
    ]
    assert [(line.topic, line.text, line.finding_labels) for line in draft.topics] == [
        ("topic-01", "F01 statement.", ["F01"]),
        ("topic-02", "F03 statement.", ["F03"]),
    ]
```

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
from deep_research.agents.report_writer import (
    PartJob,
    ReportWriterTask,
    compose_written_report,
    section_messages,
)
```

with

```python
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report_writer import (
    PartJob,
    ReportWriterTask,
    bottom_line_messages,
    compose_written_report,
    section_messages,
)
```

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
    ReadRecord,
    ResearchState,
    SectionDraft,
```

with

```python
    ReadRecord,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchState,
    SectionDraft,
```

Create `tests/test_agents/test_report_bottom_line.py`:

```python
"""The bottom line that answers first (notes-progress-report spec §7.1-§7.3; AC22-AC24, AC36).

The bottom-line request carries the reader's answers and one ``## {coverage_id} ·
{title}`` block per checked section; its reply is a direct answer of one or two
sentences, then one line per topic; the fallback builds the same shape from the
sections.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.prompts import render_structured_reply_format
from deep_research.agents.report_writer import (
    _BOTTOM_LINE_REPLY_EXAMPLES,
    _MARK_SPAN_CHARS,
    BOTTOM_LINE_INSTRUCTION,
    SECTION_INSTRUCTION,
    _section_short_title,
    bottom_line_messages,
    compose_written_report,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.types import (
    AnswerContract,
    BottomLineDraft,
    ReaderAnswer,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchState,
    SectionDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import make_target
from tests.research_fakes import report_writer_tools
from tests.test_agents.test_report_writer import (
    EIA,
    _checked,
    _FakeChecker,
    _FakeStatementCheckItem,
    _statement_finding,
    _topic,
    _writer,
)

ANSWERS = [
    ReaderAnswer(question_id="q1", dimension="scope", text="How many picks do you want?",
                 short="Picks", value="Just one", source="chosen"),
    ReaderAnswer(question_id="q2", dimension="geography", text="Which area?",
                 short="Area", value="San Jose", source="best_guess"),
]


@pytest.fixture
def checker(monkeypatch) -> _FakeChecker:
    fake = _FakeChecker()
    monkeypatch.setattr("deep_research.agents.evidence_verifier.StatementCheckItem",
                        _FakeStatementCheckItem, raising=False)
    monkeypatch.setattr("deep_research.agents.evidence_verifier.check_statements", fake, raising=False)
    return fake


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def _state(*, reader_answers: list[ReaderAnswer] | None = None) -> ResearchState:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    return ResearchState(session_id="s1", original_question="How much capacity was added?",
                         sub_topics=[_topic("topic-01", "Capacity added", [target])],
                         verified_findings=[finding], reader_answers=reader_answers or [])


def _section(task) -> ReportSection:
    finding = task.findings[0]
    statement = ReportStatement(statement_id="S001", text="The EIA reported 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(finding)])
    return ReportSection(title="Capacity added", coverage_id="topic-01",
                         points=[ReportPoint(text=statement.text, statement=statement)])


# --- the request (AC22) ----------------------------------------------------------------


def test_bottom_line_request_reader_answers_and_ids(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state(reader_answers=ANSWERS))
    assert task.reader_answers == ANSWERS

    body = bottom_line_messages(task, [_section(task)])[-1].content

    assert (
        "# Reader answers\n"
        "- How many picks do you want? Just one (the reader's answer)\n"
        "- Which area? San Jose (a best guess; the reader did not answer)\n\n"
        "# Checked statements\n"
    ) in body
    assert "Plan within these answers" not in body
    assert body.index("# Answer form\n") < body.index("# Reader answers\n") < body.index("# Checked statements\n")
    assert "## topic-01 \u00b7 Capacity added\n- The EIA reported 10.4 GW in 2024. (cites F01)" in body
    without = writer.build_task(_state())
    assert "\n# Reader answers\n" not in bottom_line_messages(without, [_section(without)])[-1].content


def test_a_bottom_line_redraft_prefixes_its_answer_and_topic_lines(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    answer = ReportPoint(text="Old answer.", statement=ReportStatement(statement_id="S001", text="Old answer."))
    line = ReportPoint(text="Old line.", statement=ReportStatement(statement_id="S002", text="Old line."))

    body = bottom_line_messages(task, [], previous=[answer, line], previous_topics={"S002": "topic-01"})[-1].content

    assert "# Your previous bottom line\n- answer: Old answer.\n- topic-01: Old line." in body


@pytest.mark.asyncio
async def test_answer_overflow_refused(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        # Three answer sentences that state no figure, so no restatement guard refuses
        # one: only the two-sentence cap can.
        BottomLineDraft(sentences=[
            WriterPointDraft(text="The EIA reports that capacity grew.", finding_labels=["F01"]),
            WriterPointDraft(text="The EIA counts the additions by year.", finding_labels=["F01"]),
            WriterPointDraft(text="The EIA tracks storage additions.", finding_labels=["F01"]),
        ]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 2
    [refused] = [r for r in composition.rejected_points if r.where.startswith("bottom_line")]
    assert (refused.where, refused.reason) == ("bottom_line[2]", "over the direct answer's two sentences")
    assert len(checker.calls[-1]) == 2  # the overflow never spends a check


@pytest.mark.asyncio
async def test_missing_outcome_reask_text(checker, tracker, tmp_path: Path) -> None:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://a.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://a.test/2", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    contract = AnswerContract(
        question="Why did the programme close?", scope_statement="Answered as of 2026-09-24.",
        geographic_scope="worldwide", as_of_date="2026-09-24",
        evidence_period_requirement="the period the question names", assumptions=[],
        answer_kind="explanation", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Why did the programme close?",
                          sub_topics=[_topic("topic-01", "Programme closure", [target])],
                          verified_findings=[cause, outcome], answer_contract=contract)
    seen: list[str] = []

    def bottom_line(messages, schema):
        seen.append(messages[-1].content)
        return BottomLineDraft(sentences=[WriterPointDraft(
            text="A funding cut reduced the budget in 2018, restated.", finding_labels=["F01"])])

    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        bottom_line, bottom_line,
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))

    await compose_written_report(agent.build_task(state), provider=completer, section_concurrency=7)

    assert len(seen) == 2
    assert (
        'The bottom line names no outcome. State the outcome the statements under "Outcome" '
        "state, credited and dated as they state it, within the answer's two sentences: fold "
        "it into the last answer sentence or replace one, never add a third."
    ) in seen[1]


# --- the prompts and the reply examples (AC36) -------------------------------------------


def test_bottom_line_examples_valid_and_name_topics() -> None:
    assert len(_BOTTOM_LINE_REPLY_EXAMPLES) == 2
    render_structured_reply_format(_BOTTOM_LINE_REPLY_EXAMPLES)  # one-line labels, JSON objects
    for label, payload in _BOTTOM_LINE_REPLY_EXAMPLES:
        draft = BottomLineDraft.model_validate_json(payload)
        assert 1 <= len(draft.sentences) <= 2
        assert draft.topics
        headed = set(re.findall(r"## (\S+) \u00b7 ", label))
        assert {line.topic for line in draft.topics} <= headed, label
        for point in [*draft.sentences, *draft.topics]:
            for mark in point.items:
                for span in (mark.name, mark.verdict):
                    if span:
                        assert len(span) <= _MARK_SPAN_CHARS
                        assert cosmetic_text(span) in cosmetic_text(point.text), (span, point.text)


def test_the_request_shows_both_examples_under_its_reply_format(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    body = bottom_line_messages(task, [_section(task)])[-1].content
    reply_format = body.split("# Reply format\n", 1)[1].split("\n# ", 1)[0]
    for label, payload in _BOTTOM_LINE_REPLY_EXAMPLES:
        compact = json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert f"{label}\nExample JSON output:\n{compact}" in reply_format


def test_no_old_sentence_cap_name_remains() -> None:
    root = Path(__file__).resolve().parents[2]
    needle = re.compile(r"\bMAX_BOTTOM_LINE" + r"_SENTENCES\b")
    hits = [
        str(path.relative_to(root))
        for folder in ("src", "tests")
        for path in (root / folder).rglob("*.py")
        if needle.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_the_rules_ask_for_the_answer_then_one_line_per_topic() -> None:
    assert BOTTOM_LINE_INSTRUCTION.splitlines()[1].startswith(
        "- sentences: one or two sentences that answer the question directly"
    )
    assert BOTTOM_LINE_INSTRUCTION.splitlines()[2].startswith("- topics: one line per topic listed")
    assert "within the two to four sentences" not in BOTTOM_LINE_INSTRUCTION
    assert "- short_title names the same part in one to three words for a contents list" in SECTION_INSTRUCTION


# --- the section's short title (§7.2) ----------------------------------------------------


@pytest.mark.parametrize(
    ("drafted", "expected"),
    [
        ("Capacity", "Capacity"),
        ("  Opening\nhours ", "Opening hours"),
        ("Value for money", "Value for money"),
        ("Four words are many", "Capacity added in 2024"),
        ("Added in 2024", "Capacity added in 2024"),
        ("Best picks", "Capacity added in 2024"),
        ("Supercalifragilisticexpia", "Capacity added in 2024"),
        ("", "Capacity added in 2024"),
    ],
)
def test_a_short_title_keeps_one_to_three_plain_words(drafted: str, expected: str) -> None:
    assert _section_short_title(drafted, "Capacity added in 2024") == expected


@pytest.mark.asyncio
async def test_a_written_section_carries_its_short_title(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", short_title="Capacity",
                     points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [(s.title, s.short_title) for s in composition.sections] == [("Capacity added", "Capacity")]
```

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
    kept_texts = [point.text for point in composition.summary]
    assert "Acme Institute reports 10.4 GW for 2024." in kept_texts
```

with

```python
    # The sections print every kept sentence; the bottom line answers with the
    # first only (notes-progress-report spec §7.7).
    kept_texts = [point.text for section in composition.sections for point in section.points]
    assert "Acme Institute reports 10.4 GW for 2024." in kept_texts
```

`tests/test_agents/test_planner_reader_answers.py` — replace

```python
    assert not any("# Reader answers" in text for text in packets.values())
```

with

```python
    # The section, not the bottom line's rule that names it (notes-progress-report spec §7.1).
    assert not any("\n# Reader answers\n" in text for text in packets.values())
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_bottom_line.py tests/test_agents/test_report_writer.py -q
```

Expected: `2 errors` during collection — `ImportError: cannot import name '_section_short_title' from 'deep_research.agents.report_writer'` and `ImportError: cannot import name 'MAX_ANSWER_SENTENCES' from 'deep_research.agents.report_writer'`.

- [ ] **Step 3: Write the contract**

`src/deep_research/utils/types.py` — replace

```python
class ReportSection(ContractModel):
    """One validated theme of the findings, as printed points."""

    title: str = Field(min_length=1)
    points: list[ReportPoint] = Field(default_factory=list)
    coverage_id: str = ""
    """The plan sub-topic this section renders; "" for a legacy composition."""
```

with

```python
class ReportSection(ContractModel):
    """One validated theme of the findings, as printed points."""

    title: str = Field(min_length=1)
    points: list[ReportPoint] = Field(default_factory=list)
    coverage_id: str = ""
    """The plan sub-topic this section renders; "" for a legacy composition."""
    short_title: str = ""
    """One to three words naming the part for the bottom line's label and the
    contents list (notes-progress-report spec §7.2); "" for a composition
    written before it, whose readers then use ``title``."""
```

`src/deep_research/utils/types.py` — replace

```python
class SectionDraft(ContractModel):
    """One part's drafted reply: a title and its kept points (spec §6.3)."""

    title: str
    points: list[WriterPointDraft] = Field(default_factory=list)


class BottomLineDraft(ContractModel):
    """The bottom-line call's drafted reply: 2-4 sentences (spec §6.6)."""

    sentences: list[WriterPointDraft] = Field(default_factory=list)
```

with

```python
class SectionDraft(ContractModel):
    """One part's drafted reply: a title and its kept points (spec §6.3)."""

    title: str
    points: list[WriterPointDraft] = Field(default_factory=list)
    short_title: str = ""
    """The part named in one to three words for a contents list (notes-progress-report
    spec §7.1); a reply without one keeps the section title in its place."""


class TopicLineDraft(WriterPointDraft):
    """One drafted bottom-line line for one listed topic (notes-progress-report spec §7.1)."""

    topic: str = ""
    """The coverage id at the start of the topic's ``## {id} · {title}`` heading."""


class BottomLineDraft(ContractModel):
    """The bottom-line call's drafted reply: a direct answer of one or two sentences, then one line per topic (notes-progress-report spec §7.1)."""

    sentences: list[WriterPointDraft] = Field(default_factory=list)
    topics: list[TopicLineDraft] = Field(default_factory=list)
```

`src/deep_research/agents/planner.py` — replace

```python
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
```

with

```python
    lines = [
        "Before planning, the reader answered a short check about what the "
        "question leaves open. Plan within these answers: a narrowing the "
        "reader asked for is not a missing part of the question."
    ]
    lines.extend(reader_answer_lines(reader_answers))
    return "\n".join(lines)


def reader_answer_lines(reader_answers: Sequence[ReaderAnswer]) -> list[str]:
    """One line per answer to the one-time check: the question, the answer, and
    whether the reader gave it or the check assumed it -- the lines the planner
    prints under its lead sentence and the bottom line prints alone
    (notes-progress-report spec §7.1)."""
    lines: list[str] = []
    for answer in reader_answers:
        said = (
            "a best guess; the reader did not answer"
            if answer.source == "best_guess"
            else "the reader's answer"
        )
        lines.append(f"- {answer.text} {answer.value} ({said})")
    return lines
```

`src/deep_research/agents/__init__.py` — replace

```python
    render_plan_for_review,
    render_reader_answers,
```

with

```python
    reader_answer_lines,
    render_plan_for_review,
    render_reader_answers,
```

`src/deep_research/agents/__init__.py` — replace

```python
    "render_plan_for_review",
    "render_reader_answers",
```

with

```python
    "reader_answer_lines",
    "render_plan_for_review",
    "render_reader_answers",
```

`src/deep_research/agents/__init__.py` — replace

```python
    MAX_BOTTOM_LINE_SENTENCES,
    MAX_BOTTOM_LINE_SENTENCE_WORDS,
```

with

```python
    MAX_ANSWER_SENTENCES,
    MAX_BOTTOM_LINE_SENTENCE_WORDS,
```

`src/deep_research/agents/__init__.py` — replace

```python
    "MAX_BOTTOM_LINE_SENTENCES",
    "MAX_BOTTOM_LINE_SENTENCE_WORDS",
```

with

```python
    "MAX_ANSWER_SENTENCES",
    "MAX_BOTTOM_LINE_SENTENCE_WORDS",
```

`src/deep_research/agents/report_writer.py` — replace

```python
from deep_research.agents.planner import Clock, answer_form_requirement, utc_now
```

with

```python
from deep_research.agents.planner import (
    Clock,
    answer_form_requirement,
    reader_answer_lines,
    utc_now,
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
    PageCredit,
    ReadRecord,
    RejectedDraftPoint,
```

with

```python
    PageCredit,
    ReadRecord,
    ReaderAnswer,
    RejectedDraftPoint,
```

`src/deep_research/agents/report_writer.py` — replace

```python
# findings per part. MAX_BOTTOM_LINE_SENTENCES (4) stays: it is the top of
# the bottom line's own 2-4 sentence shape (WRI-4), not a truncation of
# content.
```

with

```python
# findings per part. MAX_ANSWER_SENTENCES (2) is the top of the bottom
# line's direct answer, one or two sentences (notes-progress-report spec
# §7.1), not a truncation of content: the bottom line's topic lines have no
# count cap.
```

`src/deep_research/agents/report_writer.py` — replace

```python
MAX_BOTTOM_LINE_SENTENCES = 4
```

with

```python
MAX_ANSWER_SENTENCES = 2
```

`src/deep_research/agents/report_writer.py` — replace

```python
_SECTION_TITLE_CHARS = 80
```

with

```python
_SECTION_TITLE_CHARS = 80
#: A section's short title (notes-progress-report spec §7.2): 1-3 words, at
#: most 24 characters, no digit and no verdict word, else the title stands in.
_SHORT_TITLE_WORDS = 3
_SHORT_TITLE_CHARS = 24
#: The §6.8 fallback's point cap, kept apart from the direct answer's own cap.
_FALLBACK_POINTS = 4
```

`src/deep_research/agents/report_writer.py` — replace

```python
    "- A section title names the part of the question this section answers, in the "
    "question's own words where it has them: at most eight words, never a judgement or "
    "a status.\n"
```

with

```python
    "- A section title names the part of the question this section answers, in the "
    "question's own words where it has them: at most eight words, never a judgement or "
    "a status.\n"
    "- short_title names the same part in one to three words for a contents list "
    "(\"Published picks\", \"Opening hours\"): no number, no judgement, at most 24 "
    "characters.\n"
```

`src/deep_research/agents/report_writer.py` — replace

```python
        '{"title":"Programme closure","points":[{"text":"Example Register records '
        'that the outreach program closed in 2020.","finding_labels":["F01"],'
        '"disputes":false,"outcome":true,"items":[]}]}',
```

with

```python
        '{"title":"Programme closure","short_title":"Closure","points":[{"text":'
        '"Example Register records that the outreach program closed in 2020.",'
        '"finding_labels":["F01"],"disputes":false,"outcome":true,"items":[]}]}',
```

`src/deep_research/agents/report_writer.py` — replace

```python
        '{"title":"Value for money","points":[{"text":"example-register.test says '
```

with

```python
        '{"title":"Value for money","short_title":"Value for money","points":'
        '[{"text":"example-register.test says '
```

`src/deep_research/agents/report_writer.py` — replace

```python
BOTTOM_LINE_SYSTEM_PROMPT = (
    "Write the bottom line: two to four sentences answering the question directly, "
    "from the checked statements listed; each was checked against the findings it "
    "cites; state nothing they do not."
)
```

with

```python
BOTTOM_LINE_SYSTEM_PROMPT = (
    "Write the bottom line from the checked statements listed: first a direct answer "
    "to the question in one or two sentences, then one line for each topic listed, in "
    "the listed order. Each statement was checked against the findings it cites; "
    "state nothing they do not."
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
    "Rules:\n"
    "- The most direct answer first, then the question's parts in order.\n"
    "- For a question whose answer form is a causal mechanism (one asking why or how "
    "something happened or works), give the mechanism as ordered steps within the two "
    "to four sentences: each step states a cause, its effect and the sources that "
```

with

```python
    "Rules:\n"
    "- sentences: one or two sentences that answer the question directly, the most "
    "direct answer first. When # Reader answers is listed, give the answer the form "
    "those answers ask for \u2014 how many options, which area, for what purpose \u2014 "
    "naming only options, figures and picks the listed statements carry, each credited "
    "as its statement credits it; the reader's answers narrow what is answered, never "
    "what a statement says.\n"
    "- topics: one line per topic listed under # Checked statements, in the listed "
    "order, with topic set to the id at the start of that topic's heading. The line "
    "states the fact from that topic's own statements that best answers the question; "
    "when the answer sentences already state that fact and the topic has another that "
    "bears on the question, it states that one instead. It cites only labels that "
    "topic's own statements cite. Leave a topic out only when none of its statements "
    "bears on the question.\n"
    "- For a question whose answer form is a causal mechanism (one asking why or how "
    "something happened or works), give the mechanism as ordered steps within the "
    "answer's sentences: each step states a cause, its effect and the sources that "
```

`src/deep_research/agents/report_writer.py` — replace

```python
_BOTTOM_LINE_REPLY_EXAMPLES = (
    (
        "Example input: # Checked statements ## Noise ratings - Example Tester gives "
        "Model A a noise rating of 4.5 out of 5. (cites F01; options: Model A) - "
        "Example Register says Model B is the one to beat for the price. (cites F02; "
        "options: Model B [picked])",
        '{"sentences":[{"text":"Example Tester rates Model A 4.5 out of 5 for noise, '
        'while Example Register names Model B the one to beat for the price.",'
        '"finding_labels":["F01","F02"],"items":[{"name":"Model A","verdict":"4.5 out '
        'of 5 for noise","picked":false,"by":"F01"},{"name":"Model B","verdict":"the '
        'one to beat for the price","picked":true,"by":"F02"}]}]}',
    ),
    (
        "Example input: # Checked statements ## Why the program closed - Example "
        "Institute reports that a 2018 funding cut reduced the outreach budget. "
        "(cites F04) - Example Register states that the reduced budget forced staff "
        "reductions through 2019. (cites F05) - Example Register records that the "
        "outreach program closed in 2020 after its funding ended. (cites F06) "
        "# Outcome - Example Register records that the outreach program closed in "
        "2020 after its funding ended. (cites F06)",
        '{"sentences":[{"text":"According to Example Institute, a 2018 funding cut '
        'reduced the outreach budget, and Example Register says the reduced budget '
        'forced staff reductions through 2019.","finding_labels":["F04","F05"],'
        '"items":[]},{"text":"The outreach program then closed in 2020 after the '
        'funding ended, Example Register records.","finding_labels":["F06"],'
        '"items":[]}]}',
    ),
)
```

with

```python
_BOTTOM_LINE_REPLY_EXAMPLES = (
    (
        "Example input: # Reader answers - How many picks do you want? Just one (the "
        "reader's answer) # Checked statements ## topic-01 \u00b7 Noise ratings - Example "
        "Tester gives Model A a noise rating of 4.5 out of 5. (cites F01; options: Model "
        "A) ## topic-02 \u00b7 Value for money - Example Register says Model B is the one "
        "to beat for the price. (cites F02; options: Model B [picked])",
        '{"sentences":[{"text":"Example Register picks Model B as the one to beat for '
        'the price.","finding_labels":["F02"],"items":[{"name":"Model B","verdict":"the '
        'one to beat for the price","picked":true,"by":"F02"}]}],"topics":[{"topic":'
        '"topic-01","text":"Example Tester rates Model A 4.5 out of 5 for noise.",'
        '"finding_labels":["F01"],"items":[{"name":"Model A","verdict":"4.5 out of 5 '
        'for noise","picked":false,"by":"F01"}]},{"topic":"topic-02","text":"Example '
        'Register says Model B is the one to beat for the price.","finding_labels":'
        '["F02"],"items":[{"name":"Model B","verdict":"the one to beat for the price",'
        '"picked":true,"by":"F02"}]}]}',
    ),
    (
        "Example input: # Checked statements ## topic-01 \u00b7 Why the program closed - "
        "Example Institute reports that a 2018 funding cut reduced the outreach budget. "
        "(cites F04) - Example Register states that the reduced budget forced staff "
        "reductions through 2019. (cites F05) ## topic-02 \u00b7 When it closed - Example "
        "Register records that the outreach program closed in 2020 after its funding "
        "ended. (cites F06) # Outcome - Example Register records that the outreach "
        "program closed in 2020 after its funding ended. (cites F06)",
        '{"sentences":[{"text":"According to Example Institute, a 2018 funding cut '
        'reduced the outreach budget, and the program closed in 2020 after its funding '
        'ended, Example Register records.","finding_labels":["F04","F06"],"items":[]}],'
        '"topics":[{"topic":"topic-01","text":"Example Register states that the reduced '
        'budget forced staff reductions through 2019.","finding_labels":["F05"],'
        '"items":[]},{"topic":"topic-02","text":"The outreach program closed in 2020 '
        'after its funding ended, Example Register records.","finding_labels":["F06"],'
        '"items":[]}]}',
    ),
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
    """D6/D7: ``agents.writer_authority_floor`` -- the bottom line's own
```

with

```python
    reader_answers: list[ReaderAnswer] = Field(default_factory=list)
    """The reader's answers to the one-time check (``state.reader_answers``),
    printed as ``# Reader answers`` in the bottom-line request so its direct
    answer takes the form they ask for (notes-progress-report spec §7.1); ``[]``
    when the check asked nothing."""
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
    """D6/D7: ``agents.writer_authority_floor`` -- the bottom line's own
```

`src/deep_research/agents/report_writer.py` — replace

```python
def _rendered_previous_bottom_line(points: Sequence[ReportPoint]) -> str:
    return "\n".join(f"- {point.text}" for point in points) or "(none)"
```

with

```python
def _rendered_previous_bottom_line(
    points: Sequence[ReportPoint], topics: Mapping[str, str] = _EMPTY_MAPPING,
) -> str:
    """The previous bottom line, each point prefixed with ``answer:`` or, for a
    topic line, its own ``{coverage_id}:`` (notes-progress-report spec §7.1).
    ``topics`` maps a topic line's statement id to its coverage id."""
    return "\n".join(
        f"- {topics.get(point.statement_id, 'answer')}: {point.text}" for point in points
    ) or "(none)"
```

`src/deep_research/agents/report_writer.py` — replace

```python
def bottom_line_messages(
    task: ReportWriterTask, sections: Sequence[ReportSection], *,
    previous: Sequence[ReportPoint] = (), defects: Sequence[ReviewDefect] = (),
```

with

```python
def bottom_line_messages(
    task: ReportWriterTask, sections: Sequence[ReportSection], *,
    previous: Sequence[ReportPoint] = (), previous_topics: Mapping[str, str] = _EMPTY_MAPPING,
    defects: Sequence[ReviewDefect] = (),
```

`src/deep_research/agents/report_writer.py` — replace

```python
        if lines:
            blocks.append(f"## {section.title}\n" + "\n".join(lines))
```

with

```python
        if lines:
            blocks.append(f"## {section.coverage_id} \u00b7 {section.title}\n" + "\n".join(lines))
```

`src/deep_research/agents/report_writer.py` — replace

```python
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(f"# Checked statements\n{statements_block}")
```

with

```python
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_answers:
        material.append(
            "# Reader answers\n" + "\n".join(reader_answer_lines(task.reader_answers))
        )
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(f"# Checked statements\n{statements_block}")
```

`src/deep_research/agents/report_writer.py` — replace

```python
            "it: on a mechanism answer the last step ends on one of these, credited "
            "and dated as it states it, within the two to four sentences.\n"
```

with

```python
            "it: on a mechanism answer the last step ends on one of these, credited "
            "and dated as it states it, within the answer's sentences.\n"
```

`src/deep_research/agents/report_writer.py` — replace

```python
    if previous:
        material.append(f"# Your previous bottom line\n{_rendered_previous_bottom_line(previous)}")
```

with

```python
    if previous:
        material.append(
            f"# Your previous bottom line\n{_rendered_previous_bottom_line(previous, previous_topics)}"
        )
```

`src/deep_research/agents/report_writer.py` — replace

```python
def _section_title(drafted_title: str, sub_topic_title: str) -> str:
```

with

```python
def _section_short_title(drafted_short_title: str, title: str) -> str:
    """Notes-progress-report spec §7.2: the drafted short title when it has one to
    three words, at most 24 characters, no digit and no verdict word; otherwise
    the section's own title stands in."""
    short = " ".join(drafted_short_title.split())
    if (
        not short
        or len(short.split()) > _SHORT_TITLE_WORDS
        or len(short) > _SHORT_TITLE_CHARS
        or _TITLE_DIGIT.search(short)
        or _TITLE_VERDICT_WORD.search(short)
    ):
        return title
    return short


def _section_title(drafted_title: str, sub_topic_title: str) -> str:
```

`src/deep_research/agents/report_writer.py` — replace

```python
    title = _section_title(draft.title, job.sub_topic_title)
    section = ReportSection(title=title, points=points, coverage_id=job.coverage_id) if points else None
```

with

```python
    title = _section_title(draft.title, job.sub_topic_title)
    section = ReportSection(
        title=title, short_title=_section_short_title(draft.short_title, title),
        points=points, coverage_id=job.coverage_id,
    ) if points else None
```

`src/deep_research/agents/report_writer.py` — replace

```python
    """§6.8: up to ``MAX_BOTTOM_LINE_SENTENCES`` kept checked points, the
```

with

```python
    """§6.8: up to ``_FALLBACK_POINTS`` kept checked points, the
```

`src/deep_research/agents/report_writer.py` — replace

```python
        if len(points) >= MAX_BOTTOM_LINE_SENTENCES:
            break
```

with

```python
        if len(points) >= _FALLBACK_POINTS:
            break
```

`src/deep_research/agents/report_writer.py` — replace

```python
    candidates, overflow_candidates = (
        candidates[:MAX_BOTTOM_LINE_SENTENCES], candidates[MAX_BOTTOM_LINE_SENTENCES:],
    )
    for extra in overflow_candidates:
        rejected.append(RejectedDraftPoint(
            where=extra.where, text=extra.text, finding_labels=list(extra.finding_labels),
            reason="over the bottom line's four sentences",
        ))
```

with

```python
    candidates, overflow_candidates = (
        candidates[:MAX_ANSWER_SENTENCES], candidates[MAX_ANSWER_SENTENCES:],
    )
    for extra in overflow_candidates:
        rejected.append(RejectedDraftPoint(
            where=extra.where, text=extra.text, finding_labels=list(extra.finding_labels),
            reason="over the direct answer's two sentences",
        ))
```

`src/deep_research/agents/report_writer.py` — replace

```python
                    problem=(
                        'The bottom line names no outcome. End it with the outcome '
                        'the statements under "Outcome" state, credited and dated as '
                        'they state it, within four sentences: fold the outcome into '
                        'the last sentence or replace one, never add a fifth.'
                    ),
```

with

```python
                    problem=(
                        'The bottom line names no outcome. State the outcome the '
                        'statements under "Outcome" state, credited and dated as they '
                        "state it, within the answer's two sentences: fold it into the "
                        'last answer sentence or replace one, never add a third.'
                    ),
```

`src/deep_research/agents/report_writer.py` — replace

```python
            target_words=budget_words,
```

with

```python
            target_words=budget_words,
            reader_answers=list(state.reader_answers),
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
        if not points:
            raise ReplayContractError(
                "the section packet listed no finding to draft from"
            )
        return SectionDraft(title=title, points=points)
```

with

```python
        if not points:
            raise ReplayContractError(
                "the section packet listed no finding to draft from"
            )
        # notes-progress-report spec §7.7: the title's first two words.
        return SectionDraft(title=title, points=points, short_title=" ".join(title.split()[:2]))
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
    _BOTTOM_LINE_STATEMENT = re.compile(r"^(.*) \(cites ([^;()]*)(?:; options: .*)?\)$")

    def _reply_BottomLineDraft(self, text: str) -> BottomLineDraft:
        """Up to 4 of the checked section statements' own texts, with their
        labels (spec §11.3): a bottom line built only from what a part's own
        draft already had verified, never inventing new prose. ``cites
        nothing`` (a statement with no finding label) carries no label."""
        block = self._material_block(text, "Checked statements")
        sentences: list[WriterPointDraft] = []
        for line in block.splitlines():
            if not line.startswith("- "):
                continue
            match = self._BOTTOM_LINE_STATEMENT.match(line[2:])
            if match is None:
                continue
            point_text, cites = match.group(1), match.group(2)
            labels = (
                [] if cites.strip() == "nothing"
                else [label.strip() for label in cites.split(",")]
            )
            sentences.append(WriterPointDraft(text=point_text, finding_labels=labels))
            if len(sentences) == 4:
                break
        if not sentences:
            raise ReplayContractError(
                "the bottom-line packet listed no checked statement"
            )
        return BottomLineDraft(sentences=sentences)
```

with

```python
    _BOTTOM_LINE_STATEMENT = re.compile(r"^(.*) \(cites ([^;()]*)(?:; options: .*)?\)$")
    _BOTTOM_LINE_TOPIC = re.compile(r"^## (\S+) \u00b7 ")

    def _reply_BottomLineDraft(self, text: str) -> BottomLineDraft:
        """The first statement of the first ``## {coverage_id} · {title}`` block
        as the one answer sentence, and the first statement of each block as that
        topic's line (notes-progress-report spec §7.7): a bottom line built only
        from what a part's own draft already had verified, never inventing new
        prose. ``cites nothing`` (a statement with no finding label) carries no
        label."""
        block = self._material_block(text, "Checked statements")
        topics: list[TopicLineDraft] = []
        topic: str | None = None
        for line in block.splitlines():
            heading = self._BOTTOM_LINE_TOPIC.match(line)
            if heading is not None:
                topic = heading.group(1)
                continue
            if topic is None or not line.startswith("- "):
                continue
            match = self._BOTTOM_LINE_STATEMENT.match(line[2:])
            if match is None:
                continue
            point_text, cites = match.group(1), match.group(2)
            labels = (
                [] if cites.strip() == "nothing"
                else [label.strip() for label in cites.split(",")]
            )
            topics.append(TopicLineDraft(topic=topic, text=point_text, finding_labels=labels))
            topic = None  # the first statement of each block only
        if not topics:
            raise ReplayContractError(
                "the bottom-line packet listed no checked statement"
            )
        first = topics[0]
        answer = WriterPointDraft(text=first.text, finding_labels=list(first.finding_labels))
        return BottomLineDraft(sentences=[answer], topics=topics)
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
    BottomLineDraft,
```

with

```python
    BottomLineDraft,
    TopicLineDraft,
```

- [ ] **Step 4: Run the tests; only the pinned digests and fingerprints fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_bottom_line.py tests/test_agents/test_report_writer.py tests/test_agents/test_tool_free_prompts.py tests/test_agents/test_planner_reader_answers.py tests/test_e2e_evaluation/test_replay_doubles.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py tests/test_imports.py -q
```

Expected: `5 failed, 391 passed` — exactly `test_without_answers_the_replay_packets_are_byte_identical` (the review packet reads the new bottom line), both `test_without_notes_every_request_of_a_replay_run_is_byte_identical` cases (the writer's requests changed), `test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift` and `test_the_target_fingerprint_covers_the_shared_prompt_module` (the planner's and the writer's prompt text changed).

- [ ] **Step 5: Re-pin what the contract moved**

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py digests
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py fingerprints "notes-progress-report Phase C (spec §7.1): the bottom line's answer-then-topics request and reply contract."
```

Expected, one line per value (planning values in brackets):
- `missing-target-triggers-one-extra-pass: (<old>, <B-count-extra>) -> (<new>, <B-count-extra>)` [`03e113e5584707da` → `e7aa9a4cc1ddbe55`] and `scoped-redraft-after-a-named-defect: (<old>, <B-count-redraft>) -> (<new>, <B-count-redraft>)` [`875313d15f3325f2` → `e9a1b5b09e61171a`]: new digests, each count the baseline's own (B-count-extra, B-count-redraft), unchanged by this task: 46 and 29 on the D+A tree, different if the latency branch's batch size landed first;
- `planner:react`, `planner:ResearchPlanDraft` and `planner:PlanReviewDraft` each `X -> X` (unchanged: the planner's requests carry no answers in this case), and `report_reviewer:ReportReviewDraft: <old> -> <new>` [`824e2b4aa04d2944` → `92cb55eb182d36cf`];
- `planner: <B-planner> -> <new>` [`3934bea57f61` → `55c1f86bac40`], `researcher: … (unchanged)`, `source_evaluator: … (unchanged)`, `evidence_verifier: … (unchanged)`, `report_writer: <B-writer> -> <new>` [`a27544eee344` → `7ed80d440f0a`].

The helper adds, once, a comment above `PINNED_RUN_DIGESTS` and `PINNED_PACKETS` saying Phase C re-pinned them (spec §7.1, §7.4, §7.5), and a comment naming this reason and the move above each moved fingerprint.

- [ ] **Step 6: Run the tests to verify they pass**

Run the Step 4 command again.

Expected: `396 passed`.

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/utils/types.py src/deep_research/agents/planner.py src/deep_research/agents/__init__.py src/deep_research/agents/report_writer.py src/deep_research/e2e_evaluation/replay.py tests/test_agents/test_report_bottom_line.py tests/test_agents/test_report_writer.py tests/test_agents/test_tool_free_prompts.py tests/test_agents/test_planner_reader_answers.py tests/test_e2e_evaluation/test_replay_doubles.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(writer): the bottom line answers in one or two sentences, then one line per topic"
```

---

### Task 4: One line per topic, the bottom line's layout and the fallback (AC24)

Spec §7.1–§7.3: each topic line passes the topic rules and the Statement Check; the kept bottom line records its layout (which statements are the answer, which are whose topic line, with labels); the fallback assembles one checked point per topic in plan order; the composition carries the reader's answers.

**Files:**
- Modify: `src/deep_research/utils/types.py` (`active_reader_notes` `:1262`, `ReportComposition` `:1774` on the D+A tree)
- Modify: `src/deep_research/agents/report_writer.py` (imports; `_FALLBACK_POINTS`; `ReportWriterTask`; `build_task`; `_consider_bottom_line_point` `:1907`; `_bottom_line_fallback` `:2313`; `_check_and_finalize_bottom_line` `:2435`; `_run_bottom_line` `:2569`; `_renumber` `:2841`; `compose_written_report` `:2983` — on the D+A tree, after Task 3)
- Modify: `src/deep_research/e2e_evaluation/replay.py` (`_reply_StatementCheckDraft`'s label pattern `:1260`)
- Test: `tests/test_agents/test_report_bottom_line.py`, `tests/test_e2e_evaluation/test_replay_doubles.py`; re-pins as Task 3

**Interfaces:**
- Consumes: Task 3's `TopicLineDraft`, `BottomLineDraft.topics`, `ReportSection.short_title`, `ReportWriterTask.reader_answers`; A's `ReaderNote.short`; `NOTE_COVERAGE_PREFIX` (already imported by the writer since A).
- Produces:
  - `utils/types.py`: `NOTE_LABEL_PREFIX = "Your note · "`; `note_label(note: ReaderNote) -> str`; `class BottomLineTopic(ContractModel)`: `coverage_id: str`, `label: str`, `statement_id: str`; `class BottomLineLayout(ContractModel)`: `answer_ids: list[str]`, `topic_lines: list[BottomLineTopic]`, `assembled: bool = False`; `ReportComposition.bottom_line: BottomLineLayout | None = None`, `ReportComposition.reader_answers: list[str] = []`.
  - `agents/report_writer.py`: `ReportWriterTask.note_labels: dict[str, str]` (`note-{id}` → `Your note · {short}`); `_bottom_line_fallback(outcomes, …) -> (points, verdicts, moved_ids, coverage_ids)`; `_check_and_finalize_bottom_line(…, listed_topics=…)` returns an 8-tuple ending `topic_of`; `_run_bottom_line` returns a 7-tuple ending `_LineLayout | None`; topic-line flight keys `BT01…`/`RT01…`.

- [ ] **Step 1: Write the failing tests**

`tests/test_e2e_evaluation/test_replay_doubles.py` — replace

```python
    # One finding, checked twice (its own section point, and the bottom line
    # that restates it): the parallel writer statement-checks both, so the
    # packet manifests both ids rather than the single-call writer's one.
    assert len(packet.expected_statement_ids) == 2
```

with

```python
    # One finding, checked three times -- its own section point, the bottom
    # line's answer that restates it, and its topic's line (notes-progress-report
    # spec §7.7): the parallel writer statement-checks all three, so the packet
    # manifests all three ids rather than the single-call writer's one.
    assert len(packet.expected_statement_ids) == 3
```

Append to `tests/test_e2e_evaluation/test_replay_doubles.py`:

```python


@pytest.mark.asyncio
async def test_a_replayed_bottom_line_keeps_its_answer_and_its_topic_line() -> None:
    """notes-progress-report spec §7.7, end to end through the real writer: the
    double's answer and its topic line are both checked and kept, the topic line
    labelled with the section's short title."""
    source = page("answered", value="10.4")
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])

    composition = await compose_written_report(task, provider=completer, fingerprint=None)

    assert composition.rejected_points == []
    layout = composition.bottom_line
    assert layout is not None and not layout.assembled
    assert layout.answer_ids == ["S001"]
    assert [(line.coverage_id, line.label, line.statement_id) for line in layout.topic_lines] == [
        ("topic-01", "Battery storage", "S002"),
    ]
    assert [point.statement_id for point in composition.summary] == ["S001", "S002"]


def test_the_statement_double_answers_the_topic_line_keys() -> None:
    """``BT``/``RT`` are the bottom line's topic-line flight keys (spec §7.1)."""
    source = page("topic", value="10.4")
    completer = ReplayCompleter(scenario(source))
    items = [
        StatementCheckItem(label=label, text="Acme Institute reports 10.4 GW for 2024.",
                           findings=[verified(source)], labels=["Acme Institute's own figure"])
        for label in ("B01", "BT01", "R01", "RT01")
    ]

    reply = completer._reply_StatementCheckDraft(statement_request(items))

    assert [draft.label for draft in reply.statements] == ["B01", "BT01", "R01", "RT01"]
```

`tests/test_agents/test_report_bottom_line.py` — replace

```python
    SectionDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import make_target
```

with

```python
    SectionDraft,
    TopicLineDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import make_target
from tests.graph_fakes import fake_reader_note
```

`tests/test_agents/test_report_bottom_line.py` — replace

```python
    _FakeStatementCheckItem,
    _statement_finding,
    _topic,
    _writer,
)
```

with

```python
    _FakeStatementCheckItem,
    _output_limit_error,
    _statement_finding,
    _topic,
    _verdict,
    _writer,
)
```

Append to `tests/test_agents/test_report_bottom_line.py`:

```python


# --- topic lines and the layout (§7.1, §7.2) -----------------------------------------------


def _two_part_state(*, notes=(), note_topic=None):
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True, unit_dimension=None)
    f1 = _statement_finding("https://one.test/1", "According to the source, part one holds.",
                            target_ids=["topic-01-target-01"])
    f2 = _statement_finding("https://two.test/1", "According to the source, part two holds.",
                            target_ids=["topic-02-target-01"])
    topics = [_topic("topic-01", "Part one", [t1]), _topic("topic-02", "Part two", [t2])]
    findings = [f1, f2]
    if note_topic is not None:
        topic, finding = note_topic
        topics.append(topic)
        findings.append(finding)
    return ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                         verified_findings=findings, reader_notes=list(notes),
                         reader_answers=ANSWERS)


def _labels(task) -> dict[str, str]:
    return {finding.source_url: label for label, finding in task.registry}


def _sections_route(task, extra=None):
    labels = _labels(task)

    def route(messages, schema):
        body = messages[-1].content
        part = re.search(r"# This part of the question\n(.+)", body).group(1)
        url = {"Part one": "https://one.test/1", "Part two": "https://two.test/1"}.get(part, "https://note.test/1")
        text = {"Part one": "According to the source, part one holds.",
                "Part two": "According to the source, part two holds."}.get(part, "According to the source, pastries are sold.")
        short = {"Part one": "One", "Part two": "Two"}.get(part, "Pastries")
        return SectionDraft(title=part, short_title=short,
                            points=[WriterPointDraft(text=text, finding_labels=[labels[url]])])

    return route


@pytest.mark.asyncio
async def test_bottom_line_topic_line_rules(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one, two = labels["https://one.test/1"], labels["https://two.test/1"]
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[
            TopicLineDraft(topic="topic-09", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds again.", finding_labels=[one]),
            TopicLineDraft(topic="topic-02", text="According to the source, part one holds here.", finding_labels=[one]),
        ],
    )
    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, draft])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    refused = {r.where: r.reason for r in composition.rejected_points}
    assert refused == {
        "bottom_line.topics[0]": "a line for a topic the request did not list",
        "bottom_line.topics[2]": "a second line for one topic",
        "bottom_line.topics[3]": "a topic line cites a finding its topic does not",
    }
    layout = composition.bottom_line
    assert layout is not None and layout.answer_ids == ["S001"]
    assert [(line.coverage_id, line.label) for line in layout.topic_lines] == [("topic-01", "One")]
    assert two  # the second part's own label exists; its topic simply has no kept line


@pytest.mark.asyncio
async def test_bottom_line_layout_and_order(checker, tracker, tmp_path: Path) -> None:
    """Spec §7.2: summary holds the answer, the plan's topic lines in plan order,
    then the notes' topic lines in receipt order -- whatever order the reply
    gave them -- and the layout labels a note's topic ``Your note · {short}``."""

    note = fake_reader_note("n2", kinds=["new_angle"], restatement="pastries at the cafés",
                            short="pastries")
    note_target = make_target("note-n2-target-01", coverage_id="note-n2", required=True, unit_dimension=None)
    note_finding = _statement_finding("https://note.test/1", "According to the source, pastries are sold.",
                                      target_ids=["note-n2-target-01"])
    note_topic = _topic("note-n2", "Your note: pastries at the cafés", [note_target], priority=3)
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state(notes=[note], note_topic=(note_topic, note_finding)))
    assert task.note_labels == {"note-n2": "Your note \u00b7 pastries"}
    labels = _labels(task)
    one, two, three = (labels[u] for u in ("https://one.test/1", "https://two.test/1", "https://note.test/1"))
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[
            TopicLineDraft(topic="note-n2", text="According to the source, pastries are sold.", finding_labels=[three]),
            TopicLineDraft(topic="topic-02", text="According to the source, part two holds.", finding_labels=[two]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
        ],
    )
    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, route, draft])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert [p.text for p in composition.summary] == [
        "According to the source, part one holds.",
        "According to the source, part one holds.",
        "According to the source, part two holds.",
        "According to the source, pastries are sold.",
    ]
    layout = composition.bottom_line
    assert layout.answer_ids == ["S001"]
    assert [(t.coverage_id, t.label, t.statement_id) for t in layout.topic_lines] == [
        ("topic-01", "One", "S002"), ("topic-02", "Two", "S003"),
        ("note-n2", "Your note \u00b7 pastries", "S004"),
    ]
    assert composition.reader_answers == ["Just one", "San Jose"]


@pytest.mark.asyncio
async def test_a_topic_line_may_restate_the_answer_fact(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text=sentence, finding_labels=["F01"])],
                        topics=[TopicLineDraft(topic="topic-01", text=sentence, finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == [sentence, sentence]
    assert not [r for r in composition.rejected_points if r.reason.startswith("restates")]


# --- the fallback (§7.3, AC24) -------------------------------------------------------------


def _five_part_state() -> ResearchState:
    topics, findings = [], []
    for n in range(1, 6):
        cid = f"topic-{n:02d}"
        target = make_target(f"{cid}-target-01", coverage_id=cid, required=(n != 2), unit_dimension=None)
        topics.append(_topic(cid, f"Part {n}", [target], priority=n))
        findings.append(_statement_finding(f"https://p{n}.test/1", f"According to the source, part {n} holds.",
                                           target_ids=[target.target_id]))
        findings.append(_statement_finding(f"https://p{n}.test/2", f"According to the source, part {n} also holds.",
                                           target_ids=[target.target_id]))
    return ResearchState(session_id="s1", original_question="Q?", sub_topics=topics, verified_findings=findings)


def _five_part_route(task):
    labels = _labels(task)

    def route(messages, schema):
        part = re.search(r"# This part of the question\n(.+)", messages[-1].content).group(1)
        n = part.split()[-1]
        points = [WriterPointDraft(text=f"According to the source, part {n} holds.",
                                   finding_labels=[labels[f"https://p{n}.test/1"]])]
        if n != "5":  # part 5 keeps one point only, so the fallback's move empties it
            points.append(WriterPointDraft(text=f"According to the source, part {n} also holds.",
                                           finding_labels=[labels[f"https://p{n}.test/2"]]))
        return SectionDraft(title=part, short_title=f"P{'abcde'[int(n) - 1]}", points=points)

    return route


@pytest.mark.asyncio
async def test_fallback_one_line_per_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_five_part_state())
    route = _five_part_route(task)
    completer = ScriptedCompleter(outputs=[route] * 5 + [_output_limit_error(), _output_limit_error()])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    # Plan order, the optional part included, and no cap of four.
    assert [p.text for p in composition.summary] == [
        f"According to the source, part {n} holds." for n in range(1, 6)
    ]
    layout = composition.bottom_line
    assert layout.assembled and layout.answer_ids == []
    assert [(t.coverage_id, t.label) for t in layout.topic_lines] == [
        ("topic-01", "Pa"), ("topic-02", "Pb"), ("topic-03", "Pc"), ("topic-04", "Pd"), ("topic-05", "Pe"),
    ]
    assert "The bottom-line draft failed twice; one checked section point per topic stands in for it." in [
        e.message for e in composition.errors
    ]


@pytest.mark.asyncio
async def test_fallback_move_drops_emptied_section(checker, tracker, tmp_path: Path) -> None:
    """Review M9: a picked point leaves its section, so a part whose only kept
    point is picked loses its section; its line stays in the bottom line."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_five_part_state())
    route = _five_part_route(task)
    completer = ScriptedCompleter(outputs=[route] * 5 + [_output_limit_error(), _output_limit_error()])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert [s.coverage_id for s in composition.sections] == ["topic-01", "topic-02", "topic-03", "topic-04"]
    assert [p.text for s in composition.sections for p in s.points] == [
        f"According to the source, part {n} also holds." for n in range(1, 5)
    ]
    assert composition.bottom_line.topic_lines[-1].label == "Pe"


@pytest.mark.asyncio
async def test_every_sentence_refused_falls_back_to_one_line_per_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    route = _sections_route(task)
    refused = BottomLineDraft(sentences=[WriterPointDraft(text="An unlabelled claim.", finding_labels=["F99"])])
    completer = ScriptedCompleter(outputs=[route, route, refused])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert composition.bottom_line.assembled
    assert [t.coverage_id for t in composition.bottom_line.topic_lines] == ["topic-01", "topic-02"]
    assert (
        "Every drafted bottom-line sentence was refused; one checked section point per topic stands in for it."
        in [e.message for e in composition.errors]
    )


@pytest.mark.asyncio
async def test_a_reask_shows_its_previous_lines_by_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one = labels["https://one.test/1"]
    checker.verdicts["BT02"] = _verdict_inconsistent()
    seen: list[str] = []
    first = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
                TopicLineDraft(topic="topic-02", text="According to the source, part two is wrong.",
                               finding_labels=[labels["https://two.test/1"]])],
    )

    def reask(messages, schema):
        seen.append(messages[-1].content)
        return first

    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, first, reask])

    await compose_written_report(task, provider=completer, section_concurrency=1)

    assert (
        "# Your previous bottom line\n- answer: According to the source, part one holds.\n"
        "- topic-01: According to the source, part one holds."
    ) in seen[0]


def _verdict_inconsistent():
    return _verdict("inconsistent", reason="No cited finding supports this claim.")
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_bottom_line.py tests/test_e2e_evaluation/test_replay_doubles.py -q
```

Expected: `10 failed, 41 passed` — the seven new bottom-line tests (`AttributeError: 'ReportComposition' object has no attribute 'bottom_line'`, or a topic line never asked for), `test_the_reviewer_double_scores_every_dimension_and_disposes_every_statement` (`assert 2 == 3`), `test_a_replayed_bottom_line_keeps_its_answer_and_its_topic_line` and `test_the_statement_double_answers_the_topic_line_keys` (`['B01'] == ['B01', 'BT01', 'R01', 'RT01']`).

- [ ] **Step 3: Topic lines, the layout and the fallback**

`src/deep_research/utils/types.py` — replace

```python
def active_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
```

with

```python
NOTE_LABEL_PREFIX = "Your note \u00b7 "
"""A note's label in the bottom line (notes-progress-report spec §7.2): ``Your note · {short}``."""


def note_label(note: ReaderNote) -> str:
    """``Your note · {short}``: how the bottom line names a note and its topic (spec §7.2)."""
    return f"{NOTE_LABEL_PREFIX}{note.short}"


def active_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
```

`src/deep_research/utils/types.py` — replace

```python
class ReportComposition(ContractModel):
    """Everything one written pass composed, and the evidence it renders.
```

with

```python
class BottomLineTopic(ContractModel):
    """One topic line of the bottom line (notes-progress-report spec §7.2)."""

    coverage_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    """The section's short title, or ``Your note · {short}`` for a note's topic."""
    statement_id: str = Field(min_length=1)


class BottomLineLayout(ContractModel):
    """Which of a composition's ``summary`` statements are the direct answer and
    which are topic lines, in the order the report prints them (spec §7.2)."""

    answer_ids: list[str] = Field(default_factory=list)
    topic_lines: list[BottomLineTopic] = Field(default_factory=list)
    assembled: bool = False
    """True when the fallback assembled the lines from the sections (spec §7.3)."""


class ReportComposition(ContractModel):
    """Everything one written pass composed, and the evidence it renders.
```

`src/deep_research/utils/types.py` — replace

```python
    Empty for a composition nobody finalized — a fixture, or a draft no
    finalizer published — and a renderer states nothing about checks it was
    not told about.
    """

    @model_validator(mode="after")
    def canonicalize_evidence(self) -> ReportComposition:
```

with

```python
    Empty for a composition nobody finalized — a fixture, or a draft no
    finalizer published — and a renderer states nothing about checks it was
    not told about.
    """
    bottom_line: BottomLineLayout | None = None
    """The bottom line's shape: its answer, then one line per topic
    (notes-progress-report spec §7.2). ``None`` when the bottom line is empty, and
    for a composition written before the shape existed: both render ``summary``
    as one paragraph."""
    reader_answers: list[str] = Field(default_factory=list)
    """The values of the reader's answers to the one-time check, in question
    order, printed on the evidence line (spec §7.5); ``[]`` when it asked nothing."""

    @model_validator(mode="after")
    def canonicalize_evidence(self) -> ReportComposition:
```

`src/deep_research/agents/report_writer.py` — replace

```python
    AnswerKind,
    BottomLineDraft,
```

with

```python
    AnswerKind,
    BottomLineDraft,
    BottomLineLayout,
    BottomLineTopic,
```

`src/deep_research/agents/report_writer.py` — replace

```python
    SubTopic,
    UnreachablePage,
    WriterPointDraft,
    active_reader_notes,
)
```

with

```python
    SubTopic,
    TopicLineDraft,
    UnreachablePage,
    WriterPointDraft,
    active_reader_notes,
    note_label,
)
```

`src/deep_research/agents/report_writer.py` — replace

```python
#: The §6.8 fallback's point cap, kept apart from the direct answer's own cap.
_FALLBACK_POINTS = 4
```

with

```python
#: A bottom-line request that lists no topic (spec §7.1): no topic line is kept.
_NO_TOPICS: Mapping[str, frozenset[str]] = {}
```

`src/deep_research/agents/report_writer.py` — replace

```python
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
    """D6/D7: ``agents.writer_authority_floor`` -- the bottom line's own
```

with

```python
    note_labels: dict[str, str] = Field(default_factory=dict)
    """``note-{note_id}`` -> ``Your note · {short}`` for every active reader note
    (notes-progress-report spec §7.2): the label of a note's topic line, and what
    orders note topics after the plan's, by receipt."""
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
    """D6/D7: ``agents.writer_authority_floor`` -- the bottom line's own
```

`src/deep_research/agents/report_writer.py` — replace

```python
            reader_answers=list(state.reader_answers),
```

with

```python
            reader_answers=list(state.reader_answers),
            note_labels={
                f"{NOTE_COVERAGE_PREFIX}{note.note_id}": note_label(note)
                for note in active_reader_notes(state.reader_notes)
            },
```

`src/deep_research/agents/report_writer.py` — replace

```python
def _consider_bottom_line_point(
    point: WriterPointDraft, where: str, *, cited_by_sections: Mapping[str, Finding],
    disputed_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
```

with

```python
def _consider_bottom_line_point(
    point: WriterPointDraft, where: str, *, cited_by_sections: Mapping[str, Finding],
    disputed_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
    split: bool = True,
) -> list[_Candidate]:
```

`src/deep_research/agents/report_writer.py` — replace

```python
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    pieces = [drafted] if len(drafted) <= MAX_POINT_CHARS else _split_oversize_point(drafted)
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):02d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=cited_findings, items=list(point.items),
                  had_label_group=had_label_group, disputes=point.disputes,
                  outcome=point.outcome)
        for n, piece in enumerate(pieces, start=1)
    ]


def _apply_marks(
```

with

```python
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    if len(drafted) <= MAX_POINT_CHARS:
        pieces: list[str] | None = [drafted]
    else:
        pieces = _split_oversize_point(drafted) if split else None
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):02d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=cited_findings, items=list(point.items),
                  had_label_group=had_label_group, disputes=point.disputes,
                  outcome=point.outcome)
        for n, piece in enumerate(pieces, start=1)
    ]


def _consider_topic_line(
    line: TopicLineDraft, where: str, *, listed_topics: Mapping[str, frozenset[str]],
    seen_topics: set[str], cited_by_sections: Mapping[str, Finding], disputed_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
    """Notes-progress-report spec §7.1: one topic line. Refused when its topic is
    not one the request listed, when its topic already has a line, or when it
    cites a label its own topic's kept statements do not; then every rule a
    bottom-line sentence follows. A topic line is one line, so it is never split:
    one over ``MAX_POINT_CHARS`` is refused.

    ``listed_topics`` maps each listed topic's coverage id to the labels its
    kept statements cite.
    """
    topic = line.topic.strip()
    drafted, _ = _strip_label_groups(" ".join(line.text.split()))

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(line.finding_labels), reason=reason,
        ))

    if topic not in listed_topics:
        refuse("a line for a topic the request did not list")
        return []
    if topic in seen_topics:
        refuse("a second line for one topic")
        return []
    seen_topics.add(topic)
    if any(label.strip() not in listed_topics[topic] for label in line.finding_labels):
        refuse("a topic line cites a finding its topic does not")
        return []
    return _consider_bottom_line_point(
        line, where, cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
        numbers=numbers, rejected=rejected, key_prefix=key_prefix, split=False,
    )


@dataclass(frozen=True)
class _LineLayout:
    """The kept bottom line's shape by temporary statement key (spec §7.2):
    the answer's keys, then ``(coverage_id, key)`` for each topic line."""

    answer_keys: tuple[str, ...]
    topic_keys: tuple[tuple[str, str], ...]
    assembled: bool


def _drafted_layout(points: Sequence[ReportPoint], topic_of: Mapping[str, str]) -> _LineLayout | None:
    if not points:
        return None
    return _LineLayout(
        answer_keys=tuple(p.statement_id for p in points if p.statement_id not in topic_of),
        topic_keys=tuple((topic_of[p.statement_id], p.statement_id) for p in points if p.statement_id in topic_of),
        assembled=False,
    )


def _assembled_layout(points: Sequence[ReportPoint], coverage_ids: Sequence[str]) -> _LineLayout | None:
    if not points:
        return None
    return _LineLayout(
        answer_keys=(),
        topic_keys=tuple(zip(coverage_ids, (point.statement_id for point in points))),
        assembled=True,
    )


def _apply_marks(
```

`src/deep_research/agents/report_writer.py` — replace

```python
def _bottom_line_fallback(
    outcomes: Sequence[_PartOutcome], required_coverage_ids: set[str],
    disputed_labels: frozenset[str] = frozenset(),
    label_by_finding_id: Mapping[str, str] = _EMPTY_MAPPING,
    findings_by_id: Mapping[str, Finding] | None = None,
    sources: Mapping[str, ScoredSource] | None = None,
    authority_floor: float = 0.0,
    self_descriptions: Mapping[str, str] | None = None,
    any_above_floor: bool = False,
) -> tuple[list[ReportPoint], dict[str, str], set[str]]:
    """§6.8: up to ``_FALLBACK_POINTS`` kept checked points, the
    first of each part with a required target then the first of the other
    parts -- moved into the bottom line as new statements with their own
    flight keys and the source statement's real verdict, never a section's
    own id and never a hard-coded "consistent" (P1-a). Returns the fallback
    points, their verdicts, and the source statement ids to remove from
    their sections (spec's "move": a sentence is printed once).
```

with

```python
def _bottom_line_fallback(
    outcomes: Sequence[_PartOutcome],
    disputed_labels: frozenset[str] = frozenset(),
    label_by_finding_id: Mapping[str, str] = _EMPTY_MAPPING,
    findings_by_id: Mapping[str, Finding] | None = None,
    sources: Mapping[str, ScoredSource] | None = None,
    authority_floor: float = 0.0,
    self_descriptions: Mapping[str, str] | None = None,
    any_above_floor: bool = False,
) -> tuple[list[ReportPoint], dict[str, str], set[str], list[str]]:
    """D14 (notes-progress-report spec §7.3): one kept checked point per topic, in
    plan order, note topics included -- each part's first ``consistent`` or
    ``corrected`` point, moved into the bottom line as that topic's line, as a
    new statement with its own flight key and the source statement's real
    verdict, never a section's own id and never a hard-coded "consistent"
    (P1-a). No answer sentence and no cap: a part with no eligible point has no
    line. Returns the fallback points, their verdicts, the source statement ids
    to remove from their sections (spec's "move": a sentence is printed once),
    and each point's coverage id.
```

`src/deep_research/agents/report_writer.py` — replace

```python
    findings_by_id = findings_by_id or {}
    sources = sources or {}
    self_descriptions = self_descriptions or {}
    with_required: list[_PartOutcome] = []
    others: list[_PartOutcome] = []
    for outcome in outcomes:
        if outcome.section is None:
            continue
        (with_required if outcome.job.coverage_id in required_coverage_ids else others).append(outcome)

    points: list[ReportPoint] = []
    verdicts: dict[str, str] = {}
    moved: set[str] = set()
    numbers = iter(range(1, 100))
    for outcome in [*with_required, *others]:
        if len(points) >= _FALLBACK_POINTS:
            break
        section = outcome.section
```

with

```python
    findings_by_id = findings_by_id or {}
    sources = sources or {}
    self_descriptions = self_descriptions or {}
    points: list[ReportPoint] = []
    verdicts: dict[str, str] = {}
    moved: set[str] = set()
    coverage_ids: list[str] = []
    numbers = iter(range(1, 100))
    for outcome in outcomes:  # one outcome per part, in plan order
        section = outcome.section
```

`src/deep_research/agents/report_writer.py` — replace

```python
        new_id = f"B{next(numbers):02d}"
        new_statement = chosen_point.statement.model_copy(update={"statement_id": new_id})
        points.append(chosen_point.model_copy(update={"statement": new_statement}))
        verdicts[new_id] = chosen_verdict
        moved.add(chosen_id)
    return points, verdicts, moved
```

with

```python
        new_id = f"B{next(numbers):02d}"
        new_statement = chosen_point.statement.model_copy(update={"statement_id": new_id})
        points.append(chosen_point.model_copy(update={"statement": new_statement}))
        verdicts[new_id] = chosen_verdict
        moved.add(chosen_id)
        coverage_ids.append(outcome.job.coverage_id)
    return points, verdicts, moved, coverage_ids
```

`src/deep_research/agents/report_writer.py` — replace

```python
    label_finding_ids: Mapping[str, str], key_prefix: str, disputed_labels: frozenset[str] = frozenset(),
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], list[tuple[str, str]], bool]:
    """One bottom-line draft's candidates, checked and finalized (spec §6.6).

    Returns (points, verdict_map, check_errors, rejected, dropped_marks,
    statement_check_refusals, fully_checked). ``statement_check_refusals``
```

with

```python
    label_finding_ids: Mapping[str, str], key_prefix: str, disputed_labels: frozenset[str] = frozenset(),
    listed_topics: Mapping[str, frozenset[str]] = _NO_TOPICS,
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], list[tuple[str, str]], bool, dict[str, str]]:
    """One bottom-line draft's candidates, checked and finalized (spec §6.6):
    at most ``MAX_ANSWER_SENTENCES`` answer sentences, then one line per listed
    topic (notes-progress-report spec §7.1).

    Returns (points, verdict_map, check_errors, rejected, dropped_marks,
    statement_check_refusals, fully_checked, topic_of), ``topic_of`` mapping a
    topic line's flight key to its coverage id. ``statement_check_refusals``
```

`src/deep_research/agents/report_writer.py` — replace

```python
    for extra in overflow_candidates:
        rejected.append(RejectedDraftPoint(
            where=extra.where, text=extra.text, finding_labels=list(extra.finding_labels),
            reason="over the direct answer's two sentences",
        ))

    verdicts, check_errors = await _check(
```

with

```python
    for extra in overflow_candidates:
        rejected.append(RejectedDraftPoint(
            where=extra.where, text=extra.text, finding_labels=list(extra.finding_labels),
            reason="over the direct answer's two sentences",
        ))
    topic_numbers = iter(range(1, 100))
    seen_topics: set[str] = set()
    topic_of: dict[str, str] = {}
    for n, line in enumerate(draft.topics):
        for candidate in _consider_topic_line(
            line, f"bottom_line.topics[{n}]", listed_topics=listed_topics, seen_topics=seen_topics,
            cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
            numbers=topic_numbers, rejected=rejected, key_prefix=f"{key_prefix}T",
        ):
            topic_of[candidate.key] = line.topic.strip()
            candidates.append(candidate)

    verdicts, check_errors = await _check(
```

`src/deep_research/agents/report_writer.py` — replace

```python
    stated_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    refusals: list[tuple[str, str]] = []
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=stated_rows, task_facts=task.facts,
```

with

```python
    # A topic line may restate the answer's fact when its topic has no other
    # (spec §7.1, example 1), so the answer and the topic lines each keep their
    # own restatement guard.
    answer_rows: set[str] = set()
    topic_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    refusals: list[tuple[str, str]] = []
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=topic_rows if candidate.key in topic_of else answer_rows,
            task_facts=task.facts,
```

`src/deep_research/agents/report_writer.py` — replace

```python
    return points, verdict_map, check_errors, rejected, dropped_marks, refusals, fully_checked


def _bottom_line_disputed_labels(
```

with

```python
    return points, verdict_map, check_errors, rejected, dropped_marks, refusals, fully_checked, topic_of


def _bottom_line_disputed_labels(
```

`src/deep_research/agents/report_writer.py` — replace

```python
    batch_size: int, label_urls: Mapping[str, str], label_finding_ids: Mapping[str, str],
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], set[str]]:
    """The bottom-line call (spec §6.6), started once every part task has
    finished. Returns (points, temp verdicts, errors, rejected, dropped_marks,
    moved_statement_ids -- source section statement ids a §6.8 fallback moved
    into the bottom line, so the caller removes them from their sections and
    a sentence is never printed twice, P1-a)."""
```

with

```python
    batch_size: int, label_urls: Mapping[str, str], label_finding_ids: Mapping[str, str],
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], set[str], _LineLayout | None]:
    """The bottom-line call (spec §6.6), started once every part task has
    finished. Returns (points, temp verdicts, errors, rejected, dropped_marks,
    moved_statement_ids -- source section statement ids a fallback moved into
    the bottom line, so the caller removes them from their sections and a
    sentence is never printed twice, P1-a -- and the kept points' layout,
    ``None`` when nothing is kept)."""
```

`src/deep_research/agents/report_writer.py` — replace

```python
    required_coverage = {t.coverage_id for t in task.targets if t.required}
    # RevZ2 P2-c: scoped to the writer's own kept ``disputes: true`` marks
```

with

```python
    # Notes-progress-report spec §7.1: each listed topic and the labels its
    # kept statements cite -- a topic line may cite only those.
    listed_topics = {
        section.coverage_id: frozenset(
            label_by_finding_id[finding_id]
            for point in section.points if point.statement is not None
            for finding_id in point.statement.finding_ids if finding_id in label_by_finding_id
        )
        for section in checked_sections if section.coverage_id
    }
    # RevZ2 P2-c: scoped to the writer's own kept ``disputes: true`` marks
```

`src/deep_research/agents/report_writer.py` — replace

```python
            if provider_failed:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_provider_error",
                    message="Every part failed; the report has no bottom line.", recoverable=False,
                )
            else:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_all_parts_refused",
                    message="Every part's drafted points were refused; the report has no bottom line.",
                )
            return [], {}, [error], [], [], set()
```

with

```python
            if provider_failed:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_provider_error",
                    message="Every part failed; the report has no bottom line.", recoverable=False,
                )
            else:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_all_parts_refused",
                    message="Every part's drafted points were refused; the report has no bottom line.",
                )
            return [], {}, [error], [], [], set(), None
```

`src/deep_research/agents/report_writer.py` — replace

```python
                message="No section statement was checked and kept; the bottom line is left empty.",
            )
            return [], {}, [error], [], [], set()
        return [], {}, [], [], [], set()

    is_redraft = bool(task.defects)
    previous_points = task.previous.summary if (is_redraft and task.previous) else []
    bottom_line_defects: list[ReviewDefect] = []
    if is_redraft:
        _, _, bottom_line_defects = _route_defects(task.defects, task.previous, task.targets)
    messages = bottom_line_messages(task, checked_sections, previous=previous_points,
                                    defects=bottom_line_defects, disputed_statement_ids=marked_statement_ids,
                                    outcome_statement_ids=outcome_statement_ids)
```

with

```python
                message="No section statement was checked and kept; the bottom line is left empty.",
            )
            return [], {}, [error], [], [], set(), None
        return [], {}, [], [], [], set(), None

    is_redraft = bool(task.defects)
    previous_points = task.previous.summary if (is_redraft and task.previous) else []
    previous_topics = (
        {line.statement_id: line.coverage_id for line in task.previous.bottom_line.topic_lines}
        if is_redraft and task.previous is not None and task.previous.bottom_line is not None
        else {}
    )
    bottom_line_defects: list[ReviewDefect] = []
    if is_redraft:
        _, _, bottom_line_defects = _route_defects(task.defects, task.previous, task.targets)
    messages = bottom_line_messages(task, checked_sections, previous=previous_points,
                                    previous_topics=previous_topics,
                                    defects=bottom_line_defects, disputed_statement_ids=marked_statement_ids,
                                    outcome_statement_ids=outcome_statement_ids)
```

`src/deep_research/agents/report_writer.py` — replace

```python
    if draft is None:
        fallback_points, fallback_verdicts, moved = _bottom_line_fallback(
            outcomes, required_coverage, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        error = agent_error(
            agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
            message="The bottom-line draft failed twice; kept section points stand in for it.",
        )
        return fallback_points, fallback_verdicts, [*draft_errors, error], [], [], moved

    points, verdict_map, check_errors, rejected, dropped_marks, refusals, _ = (
        await _check_and_finalize_bottom_line(
            task, draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
            batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
            label_finding_ids=label_finding_ids, key_prefix="B", disputed_labels=disputed_labels,
        )
    )
```

with

```python
    if draft is None:
        fallback_points, fallback_verdicts, moved, fallback_topics = _bottom_line_fallback(
            outcomes, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        error = agent_error(
            agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
            message="The bottom-line draft failed twice; one checked section point per topic stands in for it.",
        )
        return (fallback_points, fallback_verdicts, [*draft_errors, error], [], [], moved,
                _assembled_layout(fallback_points, fallback_topics))

    points, verdict_map, check_errors, rejected, dropped_marks, refusals, _, topic_of = (
        await _check_and_finalize_bottom_line(
            task, draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
            batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
            label_finding_ids=label_finding_ids, key_prefix="B", disputed_labels=disputed_labels,
            listed_topics=listed_topics,
        )
    )
```

`src/deep_research/agents/report_writer.py` — replace

```python
        reask_messages = bottom_line_messages(task, checked_sections, previous=points,
                                              defects=reask_defects, disputed_statement_ids=marked_statement_ids,
                                              outcome_statement_ids=outcome_statement_ids)
```

with

```python
        reask_messages = bottom_line_messages(task, checked_sections, previous=points,
                                              previous_topics=topic_of,
                                              defects=reask_defects, disputed_statement_ids=marked_statement_ids,
                                              outcome_statement_ids=outcome_statement_ids)
```

`src/deep_research/agents/report_writer.py` — replace

```python
        if reask_draft is not None:
            (reask_points, reask_verdict_map, reask_check_errors, reask_rejected,
             reask_dropped_marks, _, reask_fully_checked) = await _check_and_finalize_bottom_line(
                task, reask_draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
                batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
                label_finding_ids=label_finding_ids, key_prefix="R", disputed_labels=disputed_labels,
            )
            if reask_fully_checked:
                points, verdict_map, dropped_marks = (
                    reask_points, reask_verdict_map, reask_dropped_marks,
                )
```

with

```python
        if reask_draft is not None:
            (reask_points, reask_verdict_map, reask_check_errors, reask_rejected,
             reask_dropped_marks, _, reask_fully_checked, reask_topic_of) = await _check_and_finalize_bottom_line(
                task, reask_draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
                batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
                label_finding_ids=label_finding_ids, key_prefix="R", disputed_labels=disputed_labels,
                listed_topics=listed_topics,
            )
            if reask_fully_checked:
                points, verdict_map, dropped_marks, topic_of = (
                    reask_points, reask_verdict_map, reask_dropped_marks, reask_topic_of,
                )
```

`src/deep_research/agents/report_writer.py` — replace

```python
        fallback_points, fallback_verdicts, moved = _bottom_line_fallback(
            outcomes, required_coverage, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        if fallback_points:
            error = agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
                message="Every drafted bottom-line sentence was refused; kept section points stand in for it.",
            )
            return (fallback_points, fallback_verdicts,
                    [*draft_errors, *check_errors, error], rejected, dropped_marks, moved)

    return points, verdict_map, [*draft_errors, *check_errors], rejected, dropped_marks, set()
```

with

```python
        fallback_points, fallback_verdicts, moved, fallback_topics = _bottom_line_fallback(
            outcomes, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        if fallback_points:
            error = agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
                message=(
                    "Every drafted bottom-line sentence was refused; one checked section point "
                    "per topic stands in for it."
                ),
            )
            return (fallback_points, fallback_verdicts,
                    [*draft_errors, *check_errors, error], rejected, dropped_marks, moved,
                    _assembled_layout(fallback_points, fallback_topics))

    return (points, verdict_map, [*draft_errors, *check_errors], rejected, dropped_marks, set(),
            _drafted_layout(points, topic_of))
```

`src/deep_research/agents/report_writer.py` — replace

```python
def _renumber(
    bottom_line_points: list[ReportPoint], sections: list[ReportSection],
) -> tuple[list[ReportPoint], list[ReportSection], dict[str, str]]:
```

with

```python
def _topic_line_order(coverage_id: str, task: ReportWriterTask) -> tuple[int, int]:
    """Spec §7.2: the plan's topics in plan order, then the active notes' topics
    in receipt order (``n1`` first)."""
    if coverage_id in task.note_labels:
        return (1, int(coverage_id.removeprefix(f"{NOTE_COVERAGE_PREFIX}n")))
    plan = [topic.coverage_id for topic in task.sub_topics]
    return (0, plan.index(coverage_id) if coverage_id in plan else len(plan))


def _ordered_bottom_line(
    points: Sequence[ReportPoint], layout: _LineLayout | None, task: ReportWriterTask,
) -> tuple[list[ReportPoint], list[tuple[str, str]]]:
    """The kept bottom line in the order the report prints it (spec §7.2): the
    answer, then the topic lines; and the topic lines' ``(coverage_id, key)``."""
    if layout is None:
        return list(points), []
    by_key = {point.statement_id: point for point in points}
    topic_keys = sorted(layout.topic_keys, key=lambda pair: _topic_line_order(pair[0], task))
    ordered = [by_key[key] for key in layout.answer_keys] + [by_key[key] for _, key in topic_keys]
    return ordered, topic_keys


def _renumber(
    bottom_line_points: list[ReportPoint], sections: list[ReportSection],
) -> tuple[list[ReportPoint], list[ReportSection], dict[str, str]]:
```

`src/deep_research/agents/report_writer.py` — replace

```python
            errors=[agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_no_verified_findings",
                message="No verified finding could be cited; the report is the recorded facts alone.",
            )],
            answer_kind=task.answer_kind,
        )
```

with

```python
            errors=[agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_no_verified_findings",
                message="No verified finding could be cited; the report is the recorded facts alone.",
            )],
            answer_kind=task.answer_kind,
            reader_answers=[answer.value for answer in task.reader_answers],
        )
```

`src/deep_research/agents/report_writer.py` — replace

```python
    (bottom_line_points, bottom_line_verdicts, bottom_line_errors, bottom_line_rejected,
     bottom_line_dropped_marks, bottom_line_moved_ids) = await _run_bottom_line(
```

with

```python
    (bottom_line_points, bottom_line_verdicts, bottom_line_errors, bottom_line_rejected,
     bottom_line_dropped_marks, bottom_line_moved_ids, line_layout) = await _run_bottom_line(
```

`src/deep_research/agents/report_writer.py` — replace

```python
        if remaining:
            sections.append(outcome.section.model_copy(update={"points": remaining}))
    new_summary, new_sections, remap = _renumber(bottom_line_points, sections)
```

with

```python
        if remaining:
            sections.append(outcome.section.model_copy(update={"points": remaining}))
    ordered_points, topic_keys = _ordered_bottom_line(bottom_line_points, line_layout, task)
    new_summary, new_sections, remap = _renumber(ordered_points, sections)
    # A topic line's label is its section's short title -- taken before the
    # fallback's move, which can empty a section -- or its note's label.
    short_titles = {
        outcome.job.coverage_id: outcome.section.short_title or outcome.section.title
        for outcome in resolved_outcomes if outcome.section is not None
    }
    bottom_line = None
    if line_layout is not None:
        bottom_line = BottomLineLayout(
            answer_ids=[remap[key] for key in line_layout.answer_keys],
            topic_lines=[
                BottomLineTopic(
                    coverage_id=coverage_id,
                    label=task.note_labels.get(coverage_id) or short_titles.get(coverage_id, coverage_id),
                    statement_id=remap[key],
                )
                for coverage_id, key in topic_keys
            ],
            assembled=line_layout.assembled,
        )
```

`src/deep_research/agents/report_writer.py` — replace

```python
        generated_on=task.generated_on, errors=all_errors, answer_kind=task.answer_kind,
        dropped_marks=dropped_marks,
    )
    return _assemble_composition(composed, task)
```

with

```python
        generated_on=task.generated_on, errors=all_errors, answer_kind=task.answer_kind,
        dropped_marks=dropped_marks, bottom_line=bottom_line,
        reader_answers=[answer.value for answer in task.reader_answers],
    )
    return _assemble_composition(composed, task)
```

`src/deep_research/e2e_evaluation/replay.py` — replace

```python
        # The production writer's own flight keys (spec §6.7): ``P{part:02d}.``
        # for a section's own batch, ``B`` for the bottom line's, both
        # renumbered to the reader's ``S001…`` only after every check
        # finishes, so the check itself never sees an ``S`` label from that
        # path -- but a test that calls this double directly still builds its
        # own items with plain ``S00n`` labels, which stay accepted too.
        for label, body in _packet_blocks(text, r"(?:S|P\d+\.|B)"):
```

with

```python
        # The production writer's own flight keys (spec §6.7): ``P{part:02d}.``
        # for a section's own batch, ``B`` and ``R`` for the bottom line's
        # answer and its re-ask, ``BT`` and ``RT`` for their topic lines
        # (notes-progress-report spec §7.1), all renumbered to the reader's
        # ``S001…`` only after every check finishes, so the check itself never
        # sees an ``S`` label from that path -- but a test that calls this double
        # directly still builds its own items with plain ``S00n`` labels, which
        # stay accepted too.
        for label, body in _packet_blocks(text, r"(?:S|P\d+\.|BT|RT|B|R)"):
```

- [ ] **Step 4: Run the tests; only the pinned values fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_bottom_line.py tests/test_agents/test_report_writer.py tests/test_agents/test_reader_notes_writing.py tests/test_agents/test_tool_free_prompts.py tests/test_agents/test_planner_reader_answers.py tests/test_e2e_evaluation/test_replay_doubles.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py tests/test_imports.py -q
```

Expected: `5 failed, 405 passed` — the same five pinned tests as Task 3 Step 4.

- [ ] **Step 5: Re-pin**

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py digests
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py fingerprints "notes-progress-report Phase C (spec §7.1-§7.3): one line per topic, the bottom line's layout and the fallback in the same shape."
```

Expected: both run digests move, each count the baseline's own (B-count-extra, B-count-redraft), unchanged by this task: 46 and 29 on the D+A tree, different if the latency branch's batch size landed first [planning `1baf10d33103cdf4`, `a2139e2c14155b31`]; the three planner packets unchanged; `report_reviewer:ReportReviewDraft` moves [`2026d6a3ba612fff`]; only `report_writer` moves [`f11d61b869d9`], every other agent `(unchanged)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run the Step 4 command again.

Expected: `410 passed`.

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/utils/types.py src/deep_research/agents/report_writer.py src/deep_research/e2e_evaluation/replay.py tests/test_agents/test_report_bottom_line.py tests/test_e2e_evaluation/test_replay_doubles.py tests/test_agents/test_planner_reader_answers.py tests/test_graph/test_reader_notes_replay.py tests/test_evaluation/test_config.py
git commit -m "feat(writer): one checked line per topic, the bottom line's layout, and the fallback in the same shape"
```

---

### Task 5: The Markdown's order, the outline, and the bottom line's list (AC26)

Spec §7.5: `render_written_report` prints title, evidence line (with the reader's answers), `## Bottom line` (the answer paragraph, or the assembled line, then one `- **{label}:** {line}` per topic line), the topic sections, `## Key figures` or `## Options compared`, `## What we couldn't confirm`, `## Sources` and the evidence-log link — every heading taken from `report_outline`; citations are numbered bottom line → sections → table.

**Files:**
- Modify: `src/deep_research/agents/report.py` (module docstring; imports; `__all__` `:89`; `written_citations` `:975-1013`; `_evidence_line` `:1068-1074`; `_bottom_line_block` `:1226-1273`; `render_written_report` `:1612-1650`)
- Modify: `src/deep_research/utils/types.py` (before `class ReportComposition`), `src/deep_research/agents/__init__.py` (imports, `__all__`)
- Test: `tests/test_agents/test_report_markdown.py` (new), `tests/test_agents/test_report_layout.py` (the skeleton's headings, the citation-order test, the two goldens' table blocks moved under `## Key figures`), `tests/test_agents/test_report.py` (one docstring); re-pins in `tests/test_graph/test_reader_notes_replay.py` and `tests/test_agents/test_planner_reader_answers.py`

**Interfaces:**
- Consumes: Task 4's `BottomLineLayout`, `ReportComposition.bottom_line`/`.reader_answers`, `ReportSection.short_title`; `NOTE_COVERAGE_PREFIX`.
- Produces:
  - `utils/types.py`: `ReportOutlineKind = Literal["bottom_line", "topic", "key_figures", "options", "not_confirmed", "sources"]`; `class ReportOutlineEntry(ContractModel)`: `heading: str`, `kind: ReportOutlineKind`, `label: str`, `topic_index: int | None`, `topic_count: int | None`, `note_id: str | None`.
  - `agents/report.py`: `report_outline(composition: ReportComposition) -> list[ReportOutlineEntry]` (exported from `deep_research.agents`); the private `_ASSEMBLED_BOTTOM_LINE`, `_bottom_line_block` (layout-aware) and `_plain_bottom_line` (the old paragraph). Task 7 extends `_bottom_line_block`; Task 8 serves `report_outline`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents/test_report.py` — replace

```python

def test_citation_numbers_follow_bottom_line_then_sections() -> None:
    """§5: ``written_citations`` order is bottom line, then the table, then
    sections; with no table here, the bottom line's page takes reference 1.
    """
    composition = _written_composition(
```

with

```python

def test_citation_numbers_follow_bottom_line_then_sections() -> None:
    """Notes-progress-report spec §7.5: ``written_citations`` order is bottom
    line, then the sections, then the table; with no table here, the bottom
    line's page takes reference 1.
    """
    composition = _written_composition(
```

`tests/test_agents/test_report_layout.py` — replace

```python
        "## Bottom line",
        "## Capacity",
        "## What we couldn't confirm",
        "## Sources",
```

with

```python
        "## Bottom line",
        "## Capacity",
        "## Key figures",
        "## What we couldn't confirm",
        "## Sources",
```

`tests/test_agents/test_report_layout.py` — replace

```python


def test_citation_order_is_bottom_line_then_table_then_sections() -> None:
    a = _finding("https://a.example.test/1", "A reports X.", "1", "unit", organisation="A")
    b = _finding("https://b.example.test/1", "B reports Y.", "2", "unit", organisation="B")
```

with

```python


def test_citation_order_is_bottom_line_then_sections_then_table() -> None:
    """Notes-progress-report spec §7.5: the table prints after the topics, so its
    pages are numbered after the sections'."""
    a = _finding("https://a.example.test/1", "A reports X.", "1", "unit", organisation="A")
    b = _finding("https://b.example.test/1", "B reports Y.", "2", "unit", organisation="B")
```

`tests/test_agents/test_report_layout.py` — replace

```python
    index = written_citations(composition)

    assert [citation.url for citation in index] == [a.source_url, b.source_url, c.source_url]


```

with

```python
    index = written_citations(composition)

    assert [citation.url for citation in index] == [a.source_url, c.source_url, b.source_url]


```

`tests/test_agents/test_report_layout.py` — replace

```python
For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar [1]. The Energy Information Administration's forecast, released 2025-06-10 and reported by Utility Dive, projects domestic storage capacity rising from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026 [2]. The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| "Generators added 10.4 GW of new battery storage capacity in 2024, the second-largest generating capacity addition after solar." | 10.4 GW, actual | house.gov (stated 2025-03-12) | [1] |
| "cumulative utility-scale battery storage capacity exceeded 26 gigawatts (GW) in 2024, according to our January 2025 Preliminary Monthly…" | 26 gigawatts (GW), actual | house.gov (stated 2025-03-12) | [1] |
| Battery storage capacity, 2025 | 43.6 gigawatts (GW), actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| Battery storage (first six months of 2026) | 8.3 GW, actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| "Utility-scale battery storage in the United States is poised to more than double over the next two years and will close out 2026 at nearly 65 GW…" | 65 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| Battery storage (utility-scale), Q1 2024 | 17 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "Counting projects larger than 1 MW in the electric power sector, EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25…" | 28 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "…EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25 to 64.9 GW at the end of 2026." | 64.9 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| ERCOT, 2025 | 15 GW, actual | EIA, reported by energi.media (stated 2026-01-21) | [4] |
| ERCOT, 2027 | 37 GW, forecast | EIA, reported by energi.media (stated 2026-01-21) | [4] |

## Capacity added in 2024

```

with

```python
For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar [1]. The Energy Information Administration's forecast, released 2025-06-10 and reported by Utility Dive, projects domestic storage capacity rising from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026 [2]. The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].

## Capacity added in 2024

```

`tests/test_agents/test_report_layout.py` — replace

```python

- The EIA expects battery capacity in ERCOT to rise from about 15 GW in 2025 to 37 GW by the end of 2027, according to EIA, as reported by energi.media [4].

## What we couldn't confirm
```

with

```python

- The EIA expects battery capacity in ERCOT to rise from about 15 GW in 2025 to 37 GW by the end of 2027, according to EIA, as reported by energi.media [4].

## Key figures

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| "Generators added 10.4 GW of new battery storage capacity in 2024, the second-largest generating capacity addition after solar." | 10.4 GW, actual | house.gov (stated 2025-03-12) | [1] |
| "cumulative utility-scale battery storage capacity exceeded 26 gigawatts (GW) in 2024, according to our January 2025 Preliminary Monthly…" | 26 gigawatts (GW), actual | house.gov (stated 2025-03-12) | [1] |
| Battery storage capacity, 2025 | 43.6 gigawatts (GW), actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| Battery storage (first six months of 2026) | 8.3 GW, actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| "Utility-scale battery storage in the United States is poised to more than double over the next two years and will close out 2026 at nearly 65 GW…" | 65 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| Battery storage (utility-scale), Q1 2024 | 17 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "Counting projects larger than 1 MW in the electric power sector, EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25…" | 28 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "…EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25 to 64.9 GW at the end of 2026." | 64.9 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| ERCOT, 2025 | 15 GW, actual | EIA, reported by energi.media (stated 2026-01-21) | [4] |
| ERCOT, 2027 | 37 GW, forecast | EIA, reported by energi.media (stated 2026-01-21) | [4] |

## What we couldn't confirm
```

`tests/test_agents/test_report_layout.py` — replace

```python
Under the Constitution, each State appoints, in the manner its Legislature directs, a number of electors equal to its Senators and Representatives in Congress [1][2]; archives.gov reports 538 electoral votes in all, with 270 needed to elect, for the 2024 and 2028 presidential elections [3]. The electors meet in their respective states and vote by ballot for two persons [1], and if two or more candidates remain with equal votes, the Senate chooses the Vice President from them by ballot [2].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| The District of Columbia | three electors | National Archives | [3] |
| "…Senators and Representatives in its U.S. Congressional delegation—two votes for its Senators in the U.S. Senate…" | two votes | National Archives | [3] |
| Total Electoral Votes, 2024 and 2028 presidential elections | 538 electoral votes | National Archives | [3] |
| Majority Needed to Elect, 2024 and 2028 presidential elections | 270 votes | National Archives | [3] |

*No figure in this table is a forecast.*

## How many electors there are

```

with

```python
Under the Constitution, each State appoints, in the manner its Legislature directs, a number of electors equal to its Senators and Representatives in Congress [1][2]; archives.gov reports 538 electoral votes in all, with 270 needed to elect, for the 2024 and 2028 presidential elections [3]. The electors meet in their respective states and vote by ballot for two persons [1], and if two or more candidates remain with equal votes, the Senate chooses the Vice President from them by ballot [2].

## How many electors there are

```

`tests/test_agents/test_report_layout.py` — replace

```python

- According to justia.com, if two or more candidates should remain with equal votes, the Senate shall choose from them by ballot the Vice President [2].

## What we couldn't confirm
```

with

```python

- According to justia.com, if two or more candidates should remain with equal votes, the Senate shall choose from them by ballot the Vice President [2].

## Key figures

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| The District of Columbia | three electors | National Archives | [3] |
| "…Senators and Representatives in its U.S. Congressional delegation—two votes for its Senators in the U.S. Senate…" | two votes | National Archives | [3] |
| Total Electoral Votes, 2024 and 2028 presidential elections | 538 electoral votes | National Archives | [3] |
| Majority Needed to Elect, 2024 and 2028 presidential elections | 270 votes | National Archives | [3] |

*No figure in this table is a forecast.*

## What we couldn't confirm
```

Create `tests/test_agents/test_report_markdown.py`:

```python
"""The reader report's structure (notes-progress-report spec §7.5; AC23, AC26).

The Markdown prints ``# question``, the evidence line, ``## Bottom line`` (the
answer, then one line per topic), one ``##`` per topic section, ``## Key figures``
or ``## Options compared``, ``## What we couldn't confirm``, ``## Sources`` and the
evidence-log link; ``report_outline`` names the same ``##`` headings, in order.
"""

from __future__ import annotations

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report, report_outline
from deep_research.utils.types import (
    BottomLineLayout,
    BottomLineTopic,
    FindingVerification,
    NotFoundTarget,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ReportTable,
    TableCell,
)
from tests.evidence_fakes import make_finding, make_read

ASSEMBLED = "*Assembled from the sections below; the summary could not be written this time.*"


def _finding(n: int):
    read = make_read(f"Body {n}.", url=f"https://s{n}.example.test/page", title=f"Page {n}")
    finding = make_finding(read, f"Body {n}.")
    return finding.model_copy(update={"verification": FindingVerification(status="verified")})


def _point(statement_id: str, text: str, finding) -> ReportPoint:
    return ReportPoint(text=text, source_urls=[finding.source_url], statement=ReportStatement(
        statement_id=statement_id, text=text, finding_ids=[finding_fingerprint(finding)]))


def _composition(*, table_shape: str | None = "findings", layout: bool = True, assembled: bool = False,
                 note_topic: bool = False, emptied: bool = False, answers: list[str] | None = None) -> ReportComposition:
    f = [_finding(n) for n in range(1, 6)]
    answer = _point("S001", "Agency One reports the answer.", f[0])
    line_one = _point("S002", "Agency One reports part one.", f[0])
    line_two = _point("S003", "Agency Two reports part two.", f[1])
    sections = [
        ReportSection(title="Part one in full", short_title="Part one", coverage_id="topic-01",
                      points=[_point("S004", "Agency Three reports more on part one.", f[2])]),
        ReportSection(title="Part two in full", short_title="Part two", coverage_id="topic-02",
                      points=[] if emptied else [_point("S005", "Agency Four reports more on part two.", f[3])]),
    ]
    if note_topic:
        sections.append(ReportSection(title="Your note: pastries at the cafés", short_title="Pastries",
                                      coverage_id="note-n2",
                                      points=[_point("S006", "Agency Four reports pastries.", f[3])]))
    table = None
    if table_shape is not None:
        table = ReportTable(shape=table_shape, columns=["What", "Figure", "Source"],
                            rows=[[TableCell(text="A · measure"), TableCell(text="1"),
                                   TableCell(text="Agency Five", finding_ids=[finding_fingerprint(f[4])])]])
    summary = [answer, line_one, line_two]
    bottom_line = None
    if layout:
        bottom_line = BottomLineLayout(
            answer_ids=[] if assembled else ["S001"],
            topic_lines=[BottomLineTopic(coverage_id="topic-01", label="Part one", statement_id="S002"),
                         BottomLineTopic(coverage_id="topic-02", label="Part two", statement_id="S003")],
            assembled=assembled,
        )
        if assembled:
            summary = [line_one, line_two]
    return ReportComposition(
        question="What is the answer?", session_id="s1", as_of="2026-09-30T00:00:00+00:00",
        findings=f, summary=summary, sections=sections, table=table, bottom_line=bottom_line,
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What about part three?", searched=True)],
        reader_answers=answers or [],
    )


def _headings(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def _bottom_line(markdown: str) -> str:
    return markdown.split("## Bottom line\n\n", 1)[1].split("\n\n## ", 1)[0]


def test_markdown_heading_order() -> None:
    markdown = render_written_report(_composition())
    assert markdown.splitlines()[0] == "# What is the answer?"
    assert _headings(markdown) == [
        "## Bottom line", "## Part one in full", "## Part two in full", "## Key figures",
        "## What we couldn't confirm", "## Sources",
    ]
    assert markdown.rstrip().splitlines()[-1].startswith("How this was researched: [evidence log](")
    options = render_written_report(_composition(table_shape="options"))
    assert "## Options compared" in _headings(options) and "## Key figures" not in _headings(options)
    assert "## Key figures" not in render_written_report(_composition(table_shape=None))


def test_report_outline_matches_headings() -> None:
    for composition in (
        _composition(), _composition(table_shape="options"), _composition(table_shape=None),
        _composition(layout=False), _composition(note_topic=True), _composition(emptied=True),
        ReportComposition(question="q", session_id="s"),
    ):
        outline = report_outline(composition)
        assert [f"## {entry.heading}" for entry in outline] == _headings(render_written_report(composition))


def test_report_outline_labels_and_numbers_the_topics() -> None:
    outline = report_outline(_composition(note_topic=True))
    assert [(e.kind, e.label, e.topic_index, e.topic_count, e.note_id) for e in outline] == [
        ("bottom_line", "Bottom line", None, None, None),
        ("topic", "Part one", 1, 3, None),
        ("topic", "Part two", 2, 3, None),
        ("topic", "Pastries (your note)", 3, 3, "n2"),
        ("key_figures", "Key figures", None, None, None),
        ("not_confirmed", "Not confirmed", None, None, None),
        ("sources", "Sources", None, None, None),
    ]


def test_the_outline_numbers_only_the_topic_sections_that_remain() -> None:
    """Review M9 (AC24): a section the fallback emptied prints no heading and takes no number."""
    topics = [e for e in report_outline(_composition(emptied=True)) if e.kind == "topic"]
    assert [(e.heading, e.topic_index, e.topic_count) for e in topics] == [("Part one in full", 1, 1)]


def test_bottom_line_layout_and_markdown() -> None:
    body = _bottom_line(render_written_report(_composition()))
    assert body == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Part two:** Agency Two reports part two [2]."
    )


def test_an_assembled_bottom_line_says_so_above_its_topic_lines() -> None:
    body = _bottom_line(render_written_report(_composition(assembled=True)))
    assert body.splitlines()[0] == ASSEMBLED
    assert body.splitlines()[2:] == [
        "- **Part one:** Agency One reports part one [1].",
        "- **Part two:** Agency Two reports part two [2].",
    ]


def test_a_composition_without_a_layout_renders_its_bottom_line_as_one_paragraph() -> None:
    body = _bottom_line(render_written_report(_composition(layout=False)))
    assert body == (
        "Agency One reports the answer [1]. Agency One reports part one [1]. "
        "Agency Two reports part two [2]."
    )


def test_the_evidence_line_carries_the_readers_answers() -> None:
    markdown = render_written_report(_composition(answers=["San Jose plus nearby South Bay cities", "Top 3-5 standout spots"]))
    assert markdown.splitlines()[2] == (
        "Evidence as of 2026-09-30 \u00b7 5 sources \u00b7 San Jose plus nearby South Bay cities "
        "\u00b7 Top 3-5 standout spots"
    )
    undated = _composition(answers=["San Jose"]).model_copy(update={"as_of": ""})
    assert render_written_report(undated).splitlines()[2] == "No source could be checked."


def test_citations_are_numbered_bottom_line_then_sections_then_table() -> None:
    markdown = render_written_report(_composition())
    sources = markdown.split("## Sources\n\n", 1)[1].splitlines()
    assert [line.split(" \u2014 ")[0] for line in sources[:5]] == [
        "1. s1.example.test", "2. s2.example.test", "3. s3.example.test", "4. s4.example.test",
        "5. s5.example.test",
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_markdown.py -q
```

Expected: `1 error` — `ImportError: cannot import name 'report_outline' from 'deep_research.agents.report'`.

- [ ] **Step 3: Print the report from its outline**

`src/deep_research/agents/__init__.py` — replace

```python
    render_written_report,
    report_as_of,
    report_filename,
    report_scope,
```

with

```python
    render_written_report,
    report_as_of,
    report_outline,
    report_filename,
    report_scope,
```

`src/deep_research/agents/__init__.py` — replace

```python
    "render_written_report",
    "report_as_of",
    "report_filename",
    "report_scope",
```

with

```python
    "render_written_report",
    "report_as_of",
    "report_outline",
    "report_filename",
    "report_scope",
```

`src/deep_research/agents/report.py` — replace

```python
the quality record, the state and the review):

* :func:`render_written_report` — spec §3: the answer-first skeleton --
  title, evidence line, bottom line, the question-shaped table, part
  sections, what could not be confirmed, and sources -- every citation
  numbered in the order a reader meets it (bottom line, then the table, then
  the sections);
* :func:`render_finding_log` — spec §9: the audit trail -- an "About this
  report" block (counts, scope, exact as-of, the parts, the table's shape),
```

with

```python
the quality record, the state and the review):

* :func:`render_written_report` — spec §3, as notes-progress-report spec §7.5
  orders it: title, evidence line, bottom line, part sections, Key figures or
  Options compared, what could not be confirmed, and sources -- every citation
  numbered in the order a reader meets it (bottom line, then the sections, then
  the table); :func:`report_outline` names those headings for the web;
* :func:`render_finding_log` — spec §9: the audit trail -- an "About this
  report" block (counts, scope, exact as-of, the parts, the table's shape),
```

`src/deep_research/agents/report.py` — replace

```python
    RejectedDraftPoint,
    ReportComposition,
    ReportPart,
    ReportPoint,
```

with

```python
    RejectedDraftPoint,
    ReportComposition,
    ReportOutlineEntry,
    ReportPart,
    ReportPoint,
```

`src/deep_research/agents/report.py` — replace

```python
    "render_written_report",
    "report_as_of",
    "report_filename",
    "report_scope",
```

with

```python
    "render_written_report",
    "report_as_of",
    "report_outline",
    "report_filename",
    "report_scope",
```

`src/deep_research/agents/report.py` — replace

```python
    """Only the pages the report cites, numbered as a reader meets them.

    Spec §5: bottom line, then the table, then the sections. Nothing here
    walks ``fact_rows``: the Key Facts table that used to draw citations from
    every fact row is gone from the reader report (moved, unfiltered, to the
```

with

```python
    """Only the pages the report cites, numbered as a reader meets them.

    Notes-progress-report spec §7.5: bottom line, then the sections, then the
    table, which now prints after the topics. Nothing here
    walks ``fact_rows``: the Key Facts table that used to draw citations from
    every fact row is gone from the reader report (moved, unfiltered, to the
```

`src/deep_research/agents/report.py` — replace

```python
        for url in point.source_urls:
            add(url)
    table = composition.table
    if table is not None:
```

with

```python
        for url in point.source_urls:
            add(url)
    for section in composition.sections:
        for point in section.points:
            for url in point.source_urls:
                add(url)
    table = composition.table
    if table is not None:
```

`src/deep_research/agents/report.py` — replace

```python
                    if finding is not None:
                        add(finding.source_url)
    for section in composition.sections:
        for point in section.points:
            for url in point.source_urls:
                add(url)
    titles = {normalize_source_url(s.url): s.title for s in composition.sources}
    for finding in composition.findings:
```

with

```python
                    if finding is not None:
                        add(finding.source_url)
    titles = {normalize_source_url(s.url): s.title for s in composition.sources}
    for finding in composition.findings:
```

`src/deep_research/agents/report.py` — replace

```python

def _evidence_line(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 2: ``Evidence as of {date} · {n} source(s)``, or the honest
    fallback when no evidence date can be read."""
    date = _evidence_date(composition.as_of)
    if date is None:
        return "No source could be checked."
    return f"Evidence as of {date} · {_counted(len(index), 'source', 'sources')}"


```

with

```python

def _evidence_line(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 2: ``Evidence as of {date} · {n} source(s)``, then `` · {answer}``
    for each of the reader's answers to the one-time check (notes-progress-report
    spec §7.5); or the honest fallback when no evidence date can be read."""
    date = _evidence_date(composition.as_of)
    if date is None:
        return "No source could be checked."
    answers = "".join(f" \u00b7 {value}" for value in composition.reader_answers)
    return f"Evidence as of {date} \u00b7 {_counted(len(index), 'source', 'sources')}{answers}"


```

`src/deep_research/agents/report.py` — replace

```python


def _bottom_line_block(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 3 and §10: one paragraph, or a fallback when nothing was
    written.

    The "nothing answered" sentence is reserved for a pass that cites
```

with

```python


#: Notes-progress-report spec §7.5 item 3: the paragraph an assembled bottom line prints.
_ASSEMBLED_BOTTOM_LINE = (
    "*Assembled from the sections below; the summary could not be written this time.*"
)


def _bottom_line_block(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """Notes-progress-report spec §7.5 items 3-4: the ``## Bottom line`` body -- the
    answer paragraph (or, for an assembled bottom line, the line that says so),
    then one ``- **{label}:** {line}`` per topic line. A composition without a
    layout prints one paragraph (``_plain_bottom_line``)."""
    layout = composition.bottom_line
    if layout is None:
        return _plain_bottom_line(composition, index)
    by_id = {point.statement_id: point for point in composition.summary if point.statement is not None}
    if layout.assembled:
        paragraph = _ASSEMBLED_BOTTOM_LINE
    else:
        paragraph = " ".join(
            _rendered_point(by_id[statement_id], composition, index)
            for statement_id in layout.answer_ids if statement_id in by_id
        )
    items = [
        f"- **{line.label}:** {_rendered_point(by_id[line.statement_id], composition, index)}"
        for line in layout.topic_lines if line.statement_id in by_id
    ]
    return "\n\n".join(block for block in (paragraph, "\n".join(items)) if block)


def _plain_bottom_line(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 3 and §10: one paragraph, or a fallback when nothing was
    written -- the bottom line of a composition without a layout
    (notes-progress-report spec §7.5).

    The "nothing answered" sentence is reserved for a pass that cites
```

`src/deep_research/agents/report.py` — replace

```python


def render_written_report(composition: ReportComposition) -> str:
    """Spec §3: the answer-first skeleton.

    Title, evidence line, bottom line, the question-shaped table, one section
    per non-empty part, what could not be confirmed, and sources -- every
    citation numbered in the order a reader meets it. Cut entirely: the old
    Executive summary, Key facts, Not found, header counts and scope (spec
    §3.1 rule 9); those move to the evidence log (§9).
    """
    index = written_citations(composition)
    lines = [f"# {composition.question}", "", _evidence_line(composition, index)]
    lines += ["", "## Bottom line", "", _bottom_line_block(composition, index)]

    table = composition.table
    if table is not None:
        lines.append("")
        lines.extend(_table_lines(table, composition, index))

    for section in composition.sections:
        if not section.points:
            continue
        lines += ["", f"## {section.title}", ""]
        lines += [_written_bullet(point, composition, index) for point in section.points]

    groups = _could_not_confirm_groups(composition)
    if groups:
        lines += ["", "## What we couldn't confirm", ""]
        lines.append("\n\n".join("\n".join(group) for group in groups))

    if index:
        lines += ["", "## Sources", ""]
        lines.extend(_source_line(citation, composition) for citation in index)

    lines += [
```

with

```python


def report_outline(composition: ReportComposition) -> list[ReportOutlineEntry]:
    """Notes-progress-report spec §7.5: one entry per ``##`` heading of the
    reader report, in order. ``render_written_report`` prints its headings from
    this list, so the web's contents list and cards pair with them exactly.

    Topics are the sections with points, numbered among themselves -- a section
    the bottom-line fallback emptied prints no heading and takes no number (§7.3).
    A note's topic is labelled ``{short} (your note)``.
    """
    entries = [ReportOutlineEntry(heading="Bottom line", kind="bottom_line", label="Bottom line")]
    printed = [section for section in composition.sections if section.points]
    for number, section in enumerate(printed, start=1):
        note_id = (
            section.coverage_id.removeprefix(NOTE_COVERAGE_PREFIX)
            if section.coverage_id.startswith(NOTE_COVERAGE_PREFIX) else None
        )
        short = section.short_title or section.title
        entries.append(ReportOutlineEntry(
            heading=section.title, kind="topic",
            label=f"{short} (your note)" if note_id else short,
            topic_index=number, topic_count=len(printed), note_id=note_id,
        ))
    if composition.table is not None:
        if composition.table.shape == "options":
            entries.append(ReportOutlineEntry(heading="Options compared", kind="options", label="Options compared"))
        else:
            entries.append(ReportOutlineEntry(heading="Key figures", kind="key_figures", label="Key figures"))
    if _could_not_confirm_groups(composition):
        entries.append(ReportOutlineEntry(
            heading="What we couldn't confirm", kind="not_confirmed", label="Not confirmed",
        ))
    if written_citations(composition):
        entries.append(ReportOutlineEntry(heading="Sources", kind="sources", label="Sources"))
    return entries


def render_written_report(composition: ReportComposition) -> str:
    """Spec §3's answer-first skeleton, in notes-progress-report spec §7.5's order.

    Title, evidence line, bottom line, one section per part with points, Key
    figures or Options compared, what could not be confirmed, and sources --
    each ``##`` heading taken from :func:`report_outline`, and every citation
    numbered in the order a reader meets it. Cut entirely: the old Executive
    summary, Key facts, Not found, header counts and scope (spec §3.1 rule 9);
    those move to the evidence log (§9).
    """
    index = written_citations(composition)
    groups = _could_not_confirm_groups(composition)
    printed = iter([section for section in composition.sections if section.points])
    lines = [f"# {composition.question}", "", _evidence_line(composition, index)]
    for entry in report_outline(composition):
        lines += ["", f"## {entry.heading}", ""]
        if entry.kind == "bottom_line":
            lines.append(_bottom_line_block(composition, index))
        elif entry.kind == "topic":
            section = next(printed)
            lines += [_written_bullet(point, composition, index) for point in section.points]
        elif entry.kind in ("key_figures", "options") and composition.table is not None:
            lines.extend(_table_lines(composition.table, composition, index))
        elif entry.kind == "not_confirmed":
            lines.append("\n\n".join("\n".join(group) for group in groups))
        elif entry.kind == "sources":
            lines.extend(_source_line(citation, composition) for citation in index)

    lines += [
```

`src/deep_research/utils/types.py` — replace

```python


class ReportComposition(ContractModel):
    """Everything one written pass composed, and the evidence it renders.
```

with

```python


ReportOutlineKind: TypeAlias = Literal[
    "bottom_line", "topic", "key_figures", "options", "not_confirmed", "sources"
]


class ReportOutlineEntry(ContractModel):
    """One ``##`` heading of the reader report, in order (notes-progress-report spec §7.5).

    ``heading`` is the heading's text exactly as printed; ``label`` is its short
    name in the web's contents list. ``topic_index``/``topic_count`` number a
    topic among the printed topic sections; ``note_id`` names the reader note a
    note's topic answers.
    """

    heading: str = Field(min_length=1)
    kind: ReportOutlineKind
    label: str = Field(min_length=1)
    topic_index: int | None = Field(default=None, ge=1)
    topic_count: int | None = Field(default=None, ge=1)
    note_id: str | None = None


class ReportComposition(ContractModel):
    """Everything one written pass composed, and the evidence it renders.
```

- [ ] **Step 4: Run the tests; only the pinned digests fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_report_markdown.py tests/test_agents/test_report.py tests/test_agents/test_report_layout.py tests/test_agents/test_report_writer.py tests/test_agents/test_planner_reader_answers.py tests/test_graph/test_reader_notes_replay.py tests/test_imports.py -q
```

Expected: `3 failed, 303 passed` — `test_without_answers_the_replay_packets_are_byte_identical` and both `test_without_notes_every_request_of_a_replay_run_is_byte_identical` cases: the review reads the re-ordered report.

- [ ] **Step 5: Re-pin**

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py digests
```

Expected: both run digests move, each count the baseline's own (B-count-extra, B-count-redraft), unchanged by this task: 46 and 29 on the D+A tree, different if the latency branch's batch size landed first [planning `b642638db6939e43`, `21f775d0b1ce5183`]; the planner packets unchanged; `report_reviewer:ReportReviewDraft` moves [`34668604c0fbb5bb`]. No fingerprint moves (`agents/report.py` is not an agent's prompt module), so the fingerprints are not re-run.

- [ ] **Step 6: Run the tests to verify they pass**

Run the Step 4 command again.

Expected: `306 passed`.

- [ ] **Step 7: Commit**

```powershell
git add src/deep_research/agents/report.py src/deep_research/utils/types.py src/deep_research/agents/__init__.py tests/test_agents/test_report_markdown.py tests/test_agents/test_report_layout.py tests/test_agents/test_report.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_planner_reader_answers.py
git commit -m "feat(report): answer-first heading order with the table after the topics, printed from report_outline"
```

---

### Task 6: Key figures (AC25, D40)

Spec §7.4 (D15, D27, D36, as D40 amends them): `key_figures_table` replaces `findings_table` inside `build_table` — eligible rows as today, labelled item · measure from the sub-topic the row mostly answers, a row with no named item labelled by the source that reported it (D40, spec ambiguity 10), values from one passage merged, one row per label, at most ten, columns What / Figure / Source, the Source cell printed as "{text} {markers}"; the options branch is unchanged.

**Files:**
- Modify: `src/deep_research/agents/report_table.py` (`__all__` `:48`; `MAX_FINDING_ROWS` `:54`; `build_table` `:316-338`; `_who_text` `:829-844`, split into `_who_name` and `_who_text`; the findings-table helpers through `findings_table` `:847-926`, deleted)
- Modify: `src/deep_research/agents/report.py` (the table cell's last column, `:1348-1351` at `73b4d7a6`), `src/deep_research/agents/__init__.py`
- Create: `tests/fixtures/latte-key-figures.json` (generated), `.superpowers/sdd/2026-09-30-phase-c/make_latte_fixture.py` (git-ignored)
- Test: `tests/test_agents/test_key_figures.py` (new), `tests/test_agents/test_report_table.py`, `tests/test_agents/test_report_layout.py`, `tests/test_agents/test_report_writer.py`; re-pins as Task 5

**Interfaces:**
- Consumes: `fact_rows` rows (`FactRow`: `subject`, `measure`, `target_ids`, `finding_id`, `duplicate_finding_ids`, `kind`, `period`, …); `composition.sub_topics` and their `evidence_targets` (`unit_dimension`); today's `_row_eligible`, `_result_text`, `_who_text`, `cosmetic_text`.
- Produces: `report_table.MAX_KEY_FIGURE_ROWS = 10`; `KeyFigureGroup` (frozen dataclass: `label`, `rows`, `shown`, property `values`); `merge_key_figures(rows, composition) -> list[KeyFigureGroup]` (each row labelled by the private `_key_figure_label(row, composition, finding_by_id)`; D40's source by `_label_source(row, finding, page_credits)`, built on `_who_name`, the Source column's name without its date); `key_figures_table(composition: ReportComposition) -> ReportTable | None`; `__all__ = ["KeyFigureGroup", "build_table", "key_figures_table", "merge_key_figures", "options_table"]` (`findings_table` is gone; all four exported from `deep_research.agents`).

- [ ] **Step 1: Generate the latte fixture**

Create `.superpowers/sdd/2026-09-30-phase-c/make_latte_fixture.py`:

```python
"""Copy the latte run's fact rows, and the findings they name, into tests/fixtures/latte-key-figures.json.

notes-progress-report spec AC25: ``output/`` is git-ignored (``.gitignore:224``), so the run's
quality record exists only in the checkout that ran the session. Run once, from the repository
root; the fixture it writes is committed. The plan is in no artifact, so the fixture declares the
nine targets the rows cite, each with ``unit_dimension`` null (no artifact records it): a target's
measure is the one the record prints for the rows whose first sorted target it is
(agents/verified_facts.py:1378), and the two targets that are first for no row read "not recorded".
"""
import hashlib
import json
import sys
from pathlib import Path

SOURCE = Path("output/report-a02a75fd75d44d8481f34953a4ff52e1-0-quality.json")
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/latte-key-figures.json")
record = json.loads(SOURCE.read_text(encoding="utf-8"))
rows = record["fact_rows"]
named = set()
for row in rows:
    named.add(row["finding_id"])
    named.update(row["duplicate_finding_ids"])
    named.update(edition["finding_id"] for edition in row["earlier"])
findings = [
    {key: finding[key] for key in ("id", "label", "source_url", "verification")}
    for finding in record["findings"] if finding["id"] in named
]
measures: dict[str, str] = {}
for row in rows:
    first = sorted(row["target_ids"])[:1]
    if first:
        measures.setdefault(first[0], row["measure"])
plan = {
    "topic-01": ["topic-01-target-01", "topic-01-target-02"],
    "topic-02": ["topic-02-target-01", "topic-02-target-02", "topic-02-target-03"],
    "topic-03": ["topic-03-target-01", "topic-03-target-02"],
    "topic-04": ["topic-04-target-01", "topic-04-target-02"],
}
titles = {part["coverage_id"]: part["sub_topic_title"] for part in record["parts"]}
fixture = {
    "source": SOURCE.as_posix(),
    "question": record["question"],
    "sub_topics": [
        {
            "coverage_id": coverage_id,
            "title": titles[coverage_id],
            "targets": [
                {"target_id": target_id, "measure": measures.get(target_id, "not recorded"), "unit_dimension": None}
                for target_id in target_ids
            ],
        }
        for coverage_id, target_ids in plan.items()
    ],
    "fact_rows": rows,
    "findings": findings,
}
cited = {target_id for row in rows for target_id in row["target_ids"]}
assert cited == {t for ids in plan.values() for t in ids}, sorted(cited)
assert len(rows) == 140 and len(findings) == 96, (len(rows), len(findings))
TARGET.parent.mkdir(parents=True, exist_ok=True)
TARGET.write_text(json.dumps(fixture, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()[:16]
print(f"{TARGET.as_posix()}: {len(rows)} fact rows, {len(findings)} findings, 9 targets, sha256 {digest}")
```

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\make_latte_fixture.py
```

Expected: `tests/fixtures/latte-key-figures.json: 140 fact rows, 96 findings, 9 targets, sha256 ec72d82581618e7f`. A `FileNotFoundError` for `output/report-a02a75fd75d44d8481f34953a4ff52e1-0-quality.json` means this is not the checkout that ran the latte session: stop and report it (Open issue O-1).

- [ ] **Step 2: Write the failing tests**

`test_report_table.py` loses the five tests that pinned `findings_table`'s output (spec ambiguity 11): its two quoted-snippet tests, and the three `test_period_resolved_from_*` tests, which pinned P3-3's "(counted from the release date, …)" qualifier and `_what_was_measured`'s "({scope})" suffix. The Key figures label carries neither: the period basis and scope leave the reader table, and the evidence log keeps `period_resolved_from` (`agents/report.py:845-846`). `test_key_figures.py` pins D40 twice: on hand-built rows (`test_a_row_with_no_named_item_is_labelled_by_its_source`) and on the replay default case, whose five figures now print as five rows (`test_the_replay_default_case_labels_its_figures_by_their_sources`).

Create `tests/test_agents/test_key_figures.py`:

```python
"""Key figures (notes-progress-report spec §7.4; AC25, D15, D36, D40).

The verified figures leave the bottom line for their own section after the topics:
each row labelled ``item · measure`` (never a quoted snippet) -- a row with no named
item by the source that reported it (D40) -- the values one passage states about one
item merged, one row per label, at most ten rows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deep_research.agents import report_table
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report
from deep_research.agents.report_table import (
    MAX_KEY_FIGURE_ROWS,
    build_table,
    key_figures_table,
    merge_key_figures,
)
from deep_research.utils.types import (
    EvidenceTarget,
    FactRow,
    FindingVerification,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    SubTopic,
)
from tests.evidence_fakes import make_finding, make_read
from tests.test_api.replay_support import EXTRA_PASS_CASE, replay_outcome

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "latte-key-figures.json"


def _latte() -> ReportComposition:
    """The latte run's 140 fact rows and the findings they name, with each recorded
    finding rebuilt under a fingerprint of its own (the record keeps no finding
    body), and its nine planned targets in plan order."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sub_topics = [
        SubTopic(
            coverage_id=topic["coverage_id"], title=topic["title"], rationale="not recorded",
            search_queries=["not recorded"], success_criteria=["not recorded"], priority=number,
            evidence_targets=[
                EvidenceTarget(target_id=target["target_id"], coverage_id=topic["coverage_id"],
                               question="not recorded", required=True, measure=target["measure"],
                               unit_dimension=target["unit_dimension"])
                for target in topic["targets"]
            ],
        )
        for number, topic in enumerate(data["sub_topics"], start=1)
    ]
    findings, renamed = [], {}
    for recorded in data["findings"]:
        read = make_read(f"Recorded finding {recorded['label']}.", url=recorded["source_url"],
                         title=recorded["label"])
        finding = make_finding(read, f"Recorded finding {recorded['label']}.",
                               content=f"Recorded finding {recorded['id']}.")
        finding = finding.model_copy(
            update={"verification": FindingVerification.model_validate(recorded["verification"])})
        renamed[recorded["id"]] = finding_fingerprint(finding)
        findings.append(finding)
    rows = [
        FactRow.model_validate({
            **row,
            "finding_id": renamed[row["finding_id"]],
            "duplicate_finding_ids": [renamed[i] for i in row["duplicate_finding_ids"]],
            "earlier": [{**e, "finding_id": renamed[e["finding_id"]]} for e in row["earlier"]],
        })
        for row in data["fact_rows"]
    ]
    return ReportComposition(question=data["question"], session_id="latte",
                             as_of="2026-09-30T20:16:05+00:00", sub_topics=sub_topics,
                             findings=findings, fact_rows=rows)


def test_key_figures_labels_and_merge_latte(monkeypatch: pytest.MonkeyPatch) -> None:
    composition = _latte()
    assert len(composition.fact_rows) == 140

    groups = merge_key_figures(composition.fact_rows, composition)

    bijan = [g for g in groups if g.label == "Bijan Bakery \u00b7 aggregate customer rating"]
    assert [(g.values, [row.row_id for row in g.rows]) for g in bijan] == [
        ("4.2 of 5 bubbles \u00b7 87 reviews", ["K003", "K004"]),
    ]
    assert all(len({row.finding_id for row in g.rows}) == 1 for g in groups)
    assert not any(g.label[:1] in {'"', "\u201c", "\u2026"} for g in groups)

    # With every row treated as eligible (eligibility reads finding bindings the
    # record does not keep), the printed section.
    monkeypatch.setattr(report_table, "_row_eligible", lambda *args, **kwargs: True)
    table = build_table(composition)
    assert table is not None
    assert table.columns == ["What", "Figure", "Source"]
    assert len(table.rows) <= MAX_KEY_FIGURE_ROWS
    labels = [row[0].text for row in table.rows]
    assert len(labels) == len(set(labels))
    bijan_finding = next(f for f in composition.findings if finding_fingerprint(f) == bijan[0].rows[0].finding_id)
    point = ReportPoint(
        text="Tripadvisor lists Bijan Bakery among San Jose's coffee shops.",
        source_urls=[bijan_finding.source_url],
        statement=ReportStatement(statement_id="S001", text="Tripadvisor lists Bijan Bakery among San Jose's coffee shops.",
                                  finding_ids=[finding_fingerprint(bijan_finding)]),
    )
    markdown = render_written_report(composition.model_copy(update={
        "table": table,
        "sections": [ReportSection(title="Published picks", coverage_id="topic-01", points=[point])],
    }))
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings.index("## Published picks") < headings.index("## Key figures")
    figures = markdown.split("## Key figures\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert figures.splitlines()[0] == "| What | Figure | Source |"
    assert "| Bijan Bakery \u00b7 aggregate customer rating | 4.2 of 5 bubbles \u00b7 87 reviews | tripadvisor.com [1] |" in figures


def _target(target_id: str, coverage_id: str, measure: str, unit_dimension: str | None = None) -> EvidenceTarget:
    return EvidenceTarget(target_id=target_id, coverage_id=coverage_id, question="q", required=True,
                          measure=measure, unit_dimension=unit_dimension)


def _topic(coverage_id: str, *targets: EvidenceTarget) -> SubTopic:
    return SubTopic(coverage_id=coverage_id, title=coverage_id, rationale="r", search_queries=["q"],
                    success_criteria=["c"], priority=1, evidence_targets=list(targets))


def _fact(row_id: str, *, finding_id: str = "f1", subject: str | None = "Example Cafe", value: str = "4.2 of 5",
          target_ids: tuple[str, ...] = (), measure: str = "row measure", period: str | None = None,
          kind: str = "actual") -> FactRow:
    return FactRow(row_id=row_id, organisation="Example Org", attribution="own", subject=subject, measure=measure,
                   period=period, value=value, kind=kind, finding_id=finding_id, target_ids=list(target_ids))


def _labels(composition: ReportComposition, *rows: FactRow) -> list[str]:
    return [g.label for g in merge_key_figures(list(rows), composition)]


def test_key_figures_measure_rule() -> None:
    plan = ReportComposition(question="q", session_id="s", sub_topics=[
        _topic("topic-01", _target("topic-01-target-01", "topic-01", "guide mentions")),
        _topic("topic-02", _target("topic-02-target-01", "topic-02", "review words"),
               _target("topic-02-target-02", "topic-02", "aggregate rating", unit_dimension="score"),
               _target("topic-02-target-03", "topic-02", "review count")),
        _topic("topic-03", _target("topic-03-target-01", "topic-03", "opening hours")),
    ])
    # The sub-topic owning most of the row's targets, and its target with a unit_dimension.
    majority = _fact("K001", target_ids=("topic-01-target-01", "topic-02-target-01", "topic-02-target-02"))
    # A tie goes to the earlier sub-topic in plan order.
    tie = _fact("K002", finding_id="f2", target_ids=("topic-03-target-01", "topic-01-target-01"))
    # No target with a unit_dimension: the sub-topic's first such target in plan order.
    plain = _fact("K003", finding_id="f3", target_ids=("topic-02-target-03", "topic-02-target-01"))
    # No planned target: the row's own measure.
    unplanned = _fact("K004", finding_id="f4", target_ids=("other-target",))
    assert _labels(plan, majority, tie, plain, unplanned) == [
        "Example Cafe \u00b7 aggregate rating", "Example Cafe \u00b7 guide mentions",
        "Example Cafe \u00b7 review words", "Example Cafe \u00b7 row measure",
    ]


def test_key_figure_labels_name_the_item_never_a_snippet() -> None:
    plan = ReportComposition(question="q", session_id="s")
    # D40: with no named item -- no subject, or one that starts with a pronoun -- the
    # label names the source that reported the row.
    assert _labels(plan, _fact("K001", subject=None, measure="aggregate rating")) == [
        "Example Org \u00b7 Aggregate rating",
    ]
    assert _labels(plan, _fact("K001", subject="its new location", measure="opening hours")) == [
        "Example Org \u00b7 Opening hours",
    ]
    assert _labels(plan, _fact("K001", subject=None, measure="stated figure")) == []
    assert _labels(plan, _fact("K001", subject="Example Cafe", measure="stated figure")) == [
        "Example Cafe \u00b7 stated figure",
    ]
    assert _labels(plan, _fact("K001", subject="iJava Cafe", measure="rating")) == ["iJava Cafe \u00b7 rating"]
    assert _labels(plan, _fact("K001", subject="bijan bakery", measure="rating")) == ["Bijan bakery \u00b7 rating"]
    assert _labels(plan, _fact("K001", measure="rating", period="2025")) == ["Example Cafe \u00b7 rating, 2025"]
    assert _labels(plan, _fact("K001", measure="rating in 2025", period="2025")) == ["Example Cafe \u00b7 rating in 2025"]


def test_key_figures_merge_one_passage_and_never_two() -> None:
    plan = ReportComposition(question="q", session_id="s")
    groups = merge_key_figures([
        _fact("K001", value="4.7 of 5 bubbles"),
        _fact("K002", value="20 reviews"),
        _fact("K003", value="4.6 of 5 bubbles"),
        _fact("K004", value="4.7 of 5 bubbles"),
        _fact("K005", finding_id="f2", value="30 reviews"),
        _fact("K006", value="4.7 of 5 bubbles", kind="forecast"),
    ], plan)
    assert [(g.values, [r.row_id for r in g.rows]) for g in groups] == [
        ("4.7 of 5 bubbles \u00b7 20 reviews", ["K001", "K002", "K004"]),
        ("4.6 of 5 bubbles", ["K003"]),
        ("30 reviews", ["K005"]),
        ("4.7 of 5 bubbles", ["K006"]),
    ]


def _eligible_composition(rows: list[FactRow], **fields) -> ReportComposition:
    findings, renamed = [], {}
    for row in rows:
        if row.finding_id in renamed:
            continue
        read = make_read(f"Page {row.finding_id}.", url=f"https://{row.finding_id}.example.test/p", title=row.finding_id)
        finding = make_finding(read, f"Page {row.finding_id}.", target_ids=["topic-01-target-01"])
        findings.append(finding.model_copy(update={"verification": FindingVerification(status="verified")}))
        renamed[row.finding_id] = finding_fingerprint(findings[-1])
    plan = [_topic("topic-01", _target("topic-01-target-01", "topic-01", "rating"))]
    return ReportComposition(question="q", session_id="s", sub_topics=plan, findings=findings,
                             fact_rows=[r.model_copy(update={"finding_id": renamed[r.finding_id],
                                                             "target_ids": ["topic-01-target-01"]}) for r in rows],
                             **fields)


def test_key_figures_print_one_row_per_label() -> None:
    """D36: two passages' rows under one label keep only the first; the rest stay in the evidence log."""
    composition = _eligible_composition([
        _fact("K001", finding_id="a", subject="Starbucks", value="3.5 of 5"),
        _fact("K002", finding_id="b", subject="Starbucks", value="4.4 of 5"),
        _fact("K003", finding_id="c", subject="Philz Coffee", value="4.5 of 5"),
    ])
    table = key_figures_table(composition)
    assert [(row[0].text, row[1].text, row[0].row_ids) for row in table.rows] == [
        ("Starbucks \u00b7 rating", "3.5 of 5", ["K001"]),
        ("Philz Coffee \u00b7 rating", "4.5 of 5", ["K003"]),
    ]
    assert table.caption == (
        "Showing 2 of 3 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_a_row_with_no_named_item_is_labelled_by_its_source() -> None:
    """D40: a row with no named item is labelled by the source that reported it, so
    figures from different findings keep separate rows. One passage's values about it
    still merge; under one label only the first row prints (D36); a relayed figure is
    labelled by the organisation it is credited to."""
    def unnamed(row_id: str, finding_id: str, organisation: str, value: str, **fields) -> FactRow:
        return _fact(row_id, finding_id=finding_id, subject=None, value=value, period="2024").model_copy(
            update={"organisation": organisation, **fields})

    composition = _eligible_composition([
        unnamed("K001", "a", "Tripadvisor", "4.7 of 5"),
        unnamed("K002", "a", "Tripadvisor", "20 reviews"),
        unnamed("K003", "b", "Yelp", "4.4 of 5"),
        unnamed("K004", "c", "Tripadvisor", "4.1 of 5"),
        unnamed("K005", "d", "EIA", "38 GW", attribution="relayed", relay_host="energi.media"),
    ])
    table = key_figures_table(composition)
    assert [(row[0].text, row[1].text, row[2].text, row[0].row_ids) for row in table.rows] == [
        ("Tripadvisor \u00b7 Rating, 2024", "4.7 of 5 \u00b7 20 reviews", "Tripadvisor", ["K001", "K002"]),
        ("Yelp \u00b7 Rating, 2024", "4.4 of 5", "Yelp", ["K003"]),
        ("EIA \u00b7 Rating, 2024", "38 GW", "EIA, reported by energi.media", ["K005"]),
    ]
    assert table.caption == (
        "Showing 4 of 5 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_key_figures_cap_at_ten_rows_and_count_fact_rows() -> None:
    rows = []
    for n in range(12):
        rows.append(_fact(f"K{2 * n + 1:03d}", finding_id=f"p{n}", subject=f"Cafe {n:02d}", value=f"4.{n} of 5"))
        rows.append(_fact(f"K{2 * n + 2:03d}", finding_id=f"p{n}", subject=f"Cafe {n:02d}", value=f"{n + 10} reviews"))
    table = key_figures_table(_eligible_composition(rows))
    assert len(table.rows) == MAX_KEY_FIGURE_ROWS == 10
    assert table.rows[0][1].text == "4.0 of 5 \u00b7 10 reviews"
    assert table.caption == (
        "Showing 20 of 24 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_a_key_figures_source_prints_its_text_then_its_markers() -> None:
    composition = _eligible_composition([
        _fact("K001", finding_id="a", subject="Starbucks", value="3.5 of 5"),
        _fact("K002", finding_id="b", subject="Philz Coffee", value="4.5 of 5"),
    ])
    markdown = render_written_report(composition.model_copy(update={"table": key_figures_table(composition)}))
    assert "| Starbucks \u00b7 rating | 3.5 of 5 | Example Org [1] |" in markdown
    assert re.search(r"\| Philz Coffee \u00b7 rating \| 4\.5 of 5 \| Example Org \[2\] \|", markdown)


def test_the_replay_default_case_labels_its_figures_by_their_sources(tmp_path: Path) -> None:
    """D40 on the replay server's default case: its five figures name no item and come
    from five findings, so each keeps its own row, labelled by its source -- where a
    measure-only label printed two rows of five."""
    table = replay_outcome(EXTRA_PASS_CASE, tmp_path).composition.table
    assert table is not None and table.columns == ["What", "Figure", "Source"]
    assert [(row[0].text, row[1].text) for row in table.rows] == [
        ("Acme Institute 17 \u00b7 Rate, 2024", "40 percent"),
        ("Acme Institute 2 \u00b7 Value, 2024", "12 million dollars"),
        ("Independent Bureau 2 \u00b7 Value, 2024", "12 million dollars"),
        ("Acme Institute 3 \u00b7 Value, 2024", "3.4 million units"),
        ("Independent Bureau 3 \u00b7 Value, 2024", "3.4 million units"),
    ]
    assert table.caption == "No figure in this table is a forecast."
```

`tests/test_agents/test_report_layout.py` — replace

```python
    written_citations,
)
from deep_research.agents.report_table import findings_table
from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import (
```

with

```python
    written_citations,
)
from deep_research.agents.report_table import key_figures_table
from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import (
```

`tests/test_agents/test_report_layout.py` — replace

```python
        "sub_topics": [topic_one, topic_two],
    })
    table = findings_table(composition)
    assert table is not None
    composition = composition.model_copy(update={"table": table})
```

with

```python
        "sub_topics": [topic_one, topic_two],
    })
    table = key_figures_table(composition)
    assert table is not None
    composition = composition.model_copy(update={"table": table})
```

`tests/test_agents/test_report_table.py` — replace

```python

Pure functions, no provider: ``build_table`` picks the shape (§4.1),
``options_table`` and ``findings_table`` build it from checked statements and
verified figures only (§4.2, §4.3). Fixtures follow the §4.2 Fable
acceptance paragraph and the §13.2/§13.3 examples.
"""
```

with

```python

Pure functions, no provider: ``build_table`` picks the shape (§4.1),
``options_table`` and ``key_figures_table`` build it from checked statements and
verified figures only (§4.2, §4.3; notes-progress-report spec §7.4). Fixtures follow the §4.2 Fable
acceptance paragraph and the §13.2/§13.3 examples.
"""
```

`tests/test_agents/test_report_table.py` — replace

```python

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report_table import build_table, findings_table, options_table
from deep_research.utils.types import (
    EarlierEdition,
```

with

```python

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report_table import build_table, key_figures_table, options_table
from deep_research.utils.types import (
    EarlierEdition,
```

`tests/test_agents/test_report_table.py` — replace

```python


def test_choice_rule_two_eligible_rows_give_findings_table() -> None:
    row_a, finding_a = _row(
        "K001",
```

with

```python


def test_choice_rule_two_eligible_rows_give_key_figures() -> None:
    row_a, finding_a = _row(
        "K001",
```

`tests/test_agents/test_report_table.py` — replace

```python
    table = build_table(composition)
    assert table is not None
    assert table.shape == "findings"


def test_choice_rule_one_row_gives_no_table() -> None:
```

with

```python
    table = build_table(composition)
    assert table is not None
    assert table.shape == "findings"
    assert table.columns == ["What", "Figure", "Source"]


def test_choice_rule_one_row_gives_no_table() -> None:
```

`tests/test_agents/test_report_table.py` — replace

```python
    vintage: str | None = None,
) -> tuple[FactRow, Finding]:
    snippet = evidence_words or f"{organisation} reports {value} {unit} for {row_id}."
    read = make_read(snippet, url=url, title=f"{organisation} page")
```

with

```python
    vintage: str | None = None,
) -> tuple[FactRow, Finding]:
    # Key figures label a row by its item (notes-progress-report spec §7.4): each
    # fixture row is its own item unless a test names one.
    subject = subject if subject is not None else f"Item {row_id}"
    snippet = evidence_words or f"{organisation} reports {value} {unit} for {row_id}."
    read = make_read(snippet, url=url, title=f"{organisation} page")
```

`tests/test_agents/test_report_table.py` — replace

```python
# (4) The Example 13.2 rows: rivals, who strings, mixed-kind suffixes.
# =============================================================================


def test_rival_rows_both_get_quoted_form() -> None:
    row1, finding1 = _row(
        "K001",
        url="https://house.gov/a",
        value="10.4",
        unit="GW",
        period="2024",
        kind="actual",
        subject="Battery storage capacity",
        organisation="house.gov",
        attribution="unattributed",
        target_ids=["req-01"],
        finding_target_ids=["req-01"],
        evidence_words="Generators added 10.4 GW of new battery storage capacity in 2024.",
    )
    row2, finding2 = _row(
        "K002",
        url="https://house.gov/a",
        value="26",
        unit="GW",
        period="2024",
        kind="actual",
        subject="Battery storage capacity",
        organisation="house.gov",
        attribution="unattributed",
        target_ids=["req-01"],
        finding_target_ids=["req-01"],
        evidence_words="Cumulative utility-scale battery storage capacity exceeded 26 GW in 2024.",
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-01",
                        coverage_id="topic-01",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[finding1, finding2],
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    texts = [row[0].text for row in table.rows]
    assert all(text.startswith('"') for text in texts)


def test_row_with_no_subject_is_quoted_even_without_a_rival() -> None:
    row, finding = _row(
        "K006",
        url="https://eia.gov/a",
        value="8.3",
        unit="GW",
        period="2026",
        kind="actual",
        subject=None,
        organisation="EIA",
        attribution="own",
        target_ids=["req-06"],
        finding_target_ids=["req-06"],
        evidence_words="Battery storage rose 8.3 GW in the first half of 2026.",
    )
    other_row, other_finding = _row(
        "K099",
        url="https://eia.gov/b",
        value="1",
        unit="GW",
        period="2019",
        kind="actual",
        subject="Unrelated widget output",
        organisation="EIA",
        attribution="own",
        target_ids=["req-99"],
        finding_target_ids=["req-99"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-06",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-06",
                        coverage_id="topic-06",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-99",
                        coverage_id="topic-06",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    quoted_texts = [row[0].text for row in table.rows if row[0].text.startswith('"')]
    assert any("8.3 GW" in text for text in quoted_texts)


```

with

```python
# (4) The Example 13.2 rows: rivals, who strings, mixed-kind suffixes.
# =============================================================================


```

`tests/test_agents/test_report_table.py` — replace

```python
        },
    )
    table = findings_table(composition)
    assert table is not None
    who_by_row_id = {row[0].row_ids[0]: row[2].text for row in table.rows}
```

with

```python
        },
    )
    table = key_figures_table(composition)
    assert table is not None
    who_by_row_id = {row[0].row_ids[0]: row[2].text for row in table.rows}
```

`tests/test_agents/test_report_table.py` — replace

```python
        },
    )
    table = findings_table(composition)
    assert table is not None
    who_texts = {row[0].row_ids[0]: row[2].text for row in table.rows}
```

with

```python
        },
    )
    table = key_figures_table(composition)
    assert table is not None
    who_texts = {row[0].row_ids[0]: row[2].text for row in table.rows}
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    who = {r[0].row_ids[0]: r[2].text for r in table.rows}
```

with

```python
        fact_rows=[row, other_row],
    )
    table = key_figures_table(composition)
    assert table is not None
    who = {r[0].row_ids[0]: r[2].text for r in table.rows}
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[actual_row, forecast_row],
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == ""
```

with

```python
        fact_rows=[actual_row, forecast_row],
    )
    table = key_figures_table(composition)
    assert table is not None
    assert table.caption == ""
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == "No figure in this table is a forecast."
```

with

```python
        fact_rows=[row1, row2],
    )
    table = key_figures_table(composition)
    assert table is not None
    assert table.caption == "No figure in this table is a forecast."
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[row1, row2, row3],
    )
    table = findings_table(composition)
    assert table is not None
    row_ids = {r[0].row_ids[0] for r in table.rows}
```

with

```python
        fact_rows=[row1, row2, row3],
    )
    table = key_figures_table(composition)
    assert table is not None
    row_ids = {r[0].row_ids[0] for r in table.rows}
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    for row in table.rows:
```

with

```python
        fact_rows=[row1, row2],
    )
    table = key_figures_table(composition)
    assert table is not None
    for row in table.rows:
```

`tests/test_agents/test_report_table.py` — replace

```python


def test_cap_12_selection_priority() -> None:
    required_row, required_finding = _row(
        "K001",
```

with

```python


def test_cap_10_selection_priority() -> None:
    required_row, required_finding = _row(
        "K001",
```

`tests/test_agents/test_report_table.py` — replace

```python
        ),
    )
    table = findings_table(composition)
    assert table is not None
    assert len(table.rows) == 12
    row_ids = {r[0].row_ids[0] for r in table.rows}
    assert "K001" in row_ids  # answers a required target: always kept
    assert "K002" in row_ids  # cited by the bottom line: kept ahead of "the rest"
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )
```

with

```python
        ),
    )
    table = key_figures_table(composition)
    assert table is not None
    assert len(table.rows) == 10
    row_ids = {r[0].row_ids[0] for r in table.rows}
    assert "K001" in row_ids  # answers a required target: always kept
    assert "K002" in row_ids  # cited by the bottom line: kept ahead of "the rest"
    assert table.caption == (
        "Showing 10 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )
```

`tests/test_agents/test_report_table.py` — replace

```python
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "Every figure in this table is a forecast."
    )
```

with

```python
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = key_figures_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 10 of 13 verified figures; all are in the evidence log. "
        "Every figure in this table is a forecast."
    )
```

`tests/test_agents/test_report_table.py` — replace

```python
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )
```

with

```python
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = key_figures_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 10 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )
```

`tests/test_agents/test_report_table.py` — replace

```python
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-01-01)"
```

with

```python
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = key_figures_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-01-01)"
```

`tests/test_agents/test_report_table.py` — replace

```python
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-02-02)"
```

with

```python
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = key_figures_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-02-02)"
```

`tests/test_agents/test_report_table.py` — replace

```python
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
```

with

```python
        fact_rows=[row, other_row],
    )
    table = key_figures_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
```

`tests/test_agents/test_report_table.py` — replace

```python
    ]
    assert len(matching) == 1


def test_period_resolved_from_names_the_release_date_as_basis() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2025",
        period_resolved_from="2025-06-10",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        release_date="2025-06-10",
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the release date, 2025-06-10" in what
    assert "counted from the page's date" not in what


def test_period_resolved_from_names_the_statement_date_as_basis() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2025",
        period_resolved_from="2025-03-12",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        statement_date="2025-03-12",
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the statement date, 2025-03-12" in what


def test_period_resolved_from_falls_back_to_the_pages_date() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2026",
        period_resolved_from="2026-02-20",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the page's date, 2026-02-20" in what


```

with

```python
    ]
    assert len(matching) == 1


```

`tests/test_agents/test_report_writer.py` — replace

```python
    markdown = render_written_report(composition)
    evidence = render_finding_log(composition)
    assert "| What was measured | Result |" in markdown
    assert "(2026-01-05)" in markdown
    assert "(updated 2026-02-10)" in markdown
```

with

```python
    markdown = render_written_report(composition)
    evidence = render_finding_log(composition)
    assert "| What | Figure | Source |" in markdown
    assert "(2026-01-05)" in markdown
    assert "(updated 2026-02-10)" in markdown
```

- [ ] **Step 3: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_key_figures.py -q
```

Expected: `1 error` — `ImportError: cannot import name 'MAX_KEY_FIGURE_ROWS' from 'deep_research.agents.report_table'`.

- [ ] **Step 4: Build Key figures**

`src/deep_research/agents/__init__.py` — replace

```python
)
from deep_research.agents.report_table import (
    MAX_FINDING_ROWS,
    MAX_FULL_PAGES_PER_CELL,
    MAX_OPTION_PART_COLUMNS,
    MAX_OPTION_ROWS,
    build_table,
    findings_table,
    options_table,
)
```

with

```python
)
from deep_research.agents.report_table import (
    MAX_FULL_PAGES_PER_CELL,
    MAX_KEY_FIGURE_ROWS,
    MAX_OPTION_PART_COLUMNS,
    MAX_OPTION_ROWS,
    KeyFigureGroup,
    build_table,
    key_figures_table,
    merge_key_figures,
    options_table,
)
```

`src/deep_research/agents/__init__.py` — replace

```python
    "MAX_OPTION_PART_COLUMNS",
    "MAX_FULL_PAGES_PER_CELL",
    "MAX_FINDING_ROWS",
    "build_table",
    "options_table",
    "findings_table",
    "BottomLineDraft",
    "CONTEXT_ONLY_RELEVANCE",
```

with

```python
    "MAX_OPTION_PART_COLUMNS",
    "MAX_FULL_PAGES_PER_CELL",
    "MAX_KEY_FIGURE_ROWS",
    "KeyFigureGroup",
    "build_table",
    "options_table",
    "key_figures_table",
    "merge_key_figures",
    "BottomLineDraft",
    "CONTEXT_ONLY_RELEVANCE",
```

`src/deep_research/agents/report.py` — replace

```python
    by_id: Mapping[str, Finding],
) -> str:
    """One printed cell, by column position and table shape (§4.2, §4.3)."""
    if shape == "findings":
        if position == last:
            return _finding_ids_markers(cell.finding_ids, by_id, index) or _CELL_EMPTY
        return _table_cell(cell.text) if cell.text else _CELL_EMPTY
    # options
```

with

```python
    by_id: Mapping[str, Finding],
) -> str:
    """One printed cell, by column position and table shape (§4.2, §4.3). A
    figures table's last column prints its text, then its markers
    (notes-progress-report spec §7.4 item 5: Key figures' Source)."""
    if shape == "findings":
        if position == last:
            text = _table_cell(cell.text) if cell.text else ""
            markers = _finding_ids_markers(cell.finding_ids, by_id, index)
            return " ".join(part for part in (text, markers) if part) or _CELL_EMPTY
        return _table_cell(cell.text) if cell.text else _CELL_EMPTY
    # options
```

`src/deep_research/agents/report_table.py` — replace

```python
statements are checked: an **options table** assembled from option marks on
kept (``consistent``/``corrected``) statements, when a required part on its
own names >= 2 options; else a **findings table** of verified figures, when
>= 2 qualify; else no table. No model call ever writes table text — every
word in a cell is either a verbatim span of a checked sentence (options) or a
page-verified field (findings).

:func:`build_table` is the one entry point the Report Writer calls
(spec §6.7); :func:`options_table` and :func:`findings_table` are exposed
separately because each is independently testable against its own fixture
(spec §14 T2) and each may be asked to build a table the caller then decides
```

with

```python
statements are checked: an **options table** assembled from option marks on
kept (``consistent``/``corrected``) statements, when a required part on its
own names >= 2 options; else **Key figures** (notes-progress-report spec
§7.4), the verified figures labelled ``item · measure`` and merged per
passage, when >= 2 rows qualify; else no table. No model call ever writes
table text — every word in a cell is either a verbatim span of a checked
sentence (options) or a page-verified field (Key figures).

:func:`build_table` is the one entry point the Report Writer calls
(spec §6.7); :func:`options_table` and :func:`key_figures_table` are exposed
separately because each is independently testable against its own fixture
(spec §14 T2) and each may be asked to build a table the caller then decides
```

`src/deep_research/agents/report_table.py` — replace

```python
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from deep_research.agents.evidence import cosmetic_text, excerpt_matches
from deep_research.agents.figures import parse_figure, quantities_in, same_quantity
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.verified_facts import (
    same_organisation,
    same_period,
    same_subject,
)
from deep_research.utils.types import (
    EarlierEdition,
    FactRow,
    FigureResult,
    Finding,
    ItemMark,
```

with

```python
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from deep_research.agents.evidence import cosmetic_text, excerpt_matches
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.verified_facts import same_organisation
from deep_research.utils.types import (
    EarlierEdition,
    FactRow,
    Finding,
    ItemMark,
```

`src/deep_research/agents/report_table.py` — replace

```python
)

__all__ = ["build_table", "options_table", "findings_table"]

# Row/column bounds (§4.2, §4.3): a table is a summary, never the whole log.
MAX_OPTION_ROWS = 8
MAX_OPTION_PART_COLUMNS = 4
MAX_FULL_PAGES_PER_CELL = 2
MAX_FINDING_ROWS = 12

_KEPT_VERDICTS = frozenset({"consistent", "corrected"})
```

with

```python
)

__all__ = ["KeyFigureGroup", "build_table", "key_figures_table", "merge_key_figures", "options_table"]

# Row/column bounds (§4.2; notes-progress-report spec §7.4): a table is a
# summary, never the whole log.
MAX_OPTION_ROWS = 8
MAX_OPTION_PART_COLUMNS = 4
MAX_FULL_PAGES_PER_CELL = 2
MAX_KEY_FIGURE_ROWS = 10

_KEPT_VERDICTS = frozenset({"consistent", "corrected"})
```

`src/deep_research/agents/report_table.py` — replace

```python
_OPTIONS_COLUMNS_HEAD = "Option"
_RECOMMENDED_BY_COLUMN = "Recommended by"
_FINDINGS_COLUMNS = [
    "What was measured",
    "Result",
    "Who reported it (and when)",
    "Source",
]
_OPTIONS_CAPTION = (
    "Each cell quotes the report's own sentence about the option in that "
```

with

```python
_OPTIONS_COLUMNS_HEAD = "Option"
_RECOMMENDED_BY_COLUMN = "Recommended by"
_KEY_FIGURE_COLUMNS = ["What", "Figure", "Source"]
#: ``verified_facts.fact_rows``' measure for a figure no planned target or unit names.
_STATED_FIGURE = "stated figure"
#: A value's shape (spec §7.4 item 3): each run of digits, dots and commas reads ``#``.
_VALUE_NUMBERS = re.compile(r"[\d.,]+")
_OPTIONS_CAPTION = (
    "Each cell quotes the report's own sentence about the option in that "
```

`src/deep_research/agents/report_table.py` — replace

```python
)
_POSSESSIVE_PRONOUNS = frozenset({"it", "its", "this", "these", "their", "they"})
_QUOTE_CLAMP_CHARS = 140

_DASH_CLASS = re.compile(r"\s*[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212]\s*")
```

with

```python
)
_POSSESSIVE_PRONOUNS = frozenset({"it", "its", "this", "these", "their", "they"})

_DASH_CLASS = re.compile(r"\s*[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212]\s*")
```

`src/deep_research/agents/report_table.py` — replace

```python

def build_table(composition: ReportComposition) -> ReportTable | None:
    """§4.1: options when one required part alone marks >= 2 options; else findings when >= 2 qualify; else none.

    The >= 2 test applies per required part (consistent with the column
```

with

```python

def build_table(composition: ReportComposition) -> ReportTable | None:
    """§4.1: options when one required part alone marks >= 2 options; else Key figures when >= 2 rows qualify; else none.

    The >= 2 test applies per required part (consistent with the column
```

`src/deep_research/agents/report_table.py` — replace

```python
    the gate passes but the resulting options table still has fewer than 2
    rows (every marked option's only cell lay outside the parts that
    ultimately qualified as columns), this falls through to the findings
    table instead of publishing a near-empty options table.
    """
    resolved_marks, dropped = _resolve_marks(composition)
```

with

```python
    the gate passes but the resulting options table still has fewer than 2
    rows (every marked option's only cell lay outside the parts that
    ultimately qualified as columns), this falls through to Key figures
    instead of publishing a near-empty options table.
    """
    resolved_marks, dropped = _resolve_marks(composition)
```

`src/deep_research/agents/report_table.py` — replace

```python
        if table is not None and len(table.rows) >= 2:
            return table
    table = findings_table(composition)
    if table is not None and len(table.rows) >= 2:
        return table
```

with

```python
        if table is not None and len(table.rows) >= 2:
            return table
    table = key_figures_table(composition)
    if table is not None and len(table.rows) >= 2:
        return table
```

`src/deep_research/agents/report_table.py` — replace

```python

# =============================================================================
# §4.3 findings table
# =============================================================================

```

with

```python

# =============================================================================
# §4.3's eligible figures, as notes-progress-report spec §7.4's Key figures
# =============================================================================

```

`src/deep_research/agents/report_table.py` — replace

```python


def _select_rows(
    eligible: Sequence[FactRow],
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    bottom_line_cited: set[str],
) -> list[FactRow]:
    if len(eligible) <= MAX_FINDING_ROWS:
        return list(eligible)

    def priority(row: FactRow) -> int:
        if _explicitly_answers_required(row, finding_by_id, required_target_ids):
            return 0
        if _row_and_duplicate_ids(row) & bottom_line_cited:
            return 1
        return 2

    ordered = sorted(
        eligible, key=priority
    )  # stable: keeps original order within a priority group
    return ordered[:MAX_FINDING_ROWS]


def _display_order(
    rows: Sequence[FactRow], composition: ReportComposition
) -> list[FactRow]:
    part_by_finding = _part_by_finding(composition)
    plan_order = [topic.coverage_id for topic in composition.sub_topics]

    def key(row: FactRow) -> tuple[int, str]:
        part = part_by_finding.get(row.finding_id)
        index = plan_order.index(part) if part in plan_order else len(plan_order)
        return (index, row.row_id)

    return sorted(rows, key=key)


```

with

```python


def _row_priority(
    row: FactRow,
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    bottom_line_cited: set[str],
) -> int:
    """§4.3's cap priority: a row that explicitly answers a required target
    first, then one the bottom line cites, then the rest."""
    if _explicitly_answers_required(row, finding_by_id, required_target_ids):
        return 0
    if _row_and_duplicate_ids(row) & bottom_line_cited:
        return 1
    return 2


```

`src/deep_research/agents/report_table.py` — replace

```python
        return True
    return wanted <= _words(text)


def _kept_figure_result(finding: Finding, row: FactRow) -> FigureResult | None:
    """The finding's own kept figure that states ``row``'s value (mirrors report.py's context lookup)."""
    if finding.verification is None:
        return None
    stated = quantities_in(row.value)
    for result in finding.verification.figure_results:
        if not result.kept:
            continue
        quantity = parse_figure(result.figure.value, result.figure.unit)
        if quantity is not None and any(
            same_quantity(quantity, other) for other in stated
        ):
            return result
        if cosmetic_text(
            f"{result.figure.value} {result.figure.unit}"
        ) == cosmetic_text(row.value):
            return result
    return None


def _clamp_around_value(words: str, value: str) -> str:
    """§4.3: clamped to 140 characters at word boundaries around the value."""
    if len(words) <= _QUOTE_CLAMP_CHARS:
        return words
    anchor = 0
    lead = (value or "").strip().split()
    if lead:
        found = cosmetic_text(words).find(cosmetic_text(lead[0]))
        if found >= 0:
            anchor = found
    half = _QUOTE_CLAMP_CHARS // 2
    start = max(0, anchor - half)
    end = min(len(words), anchor + half)
    if start > 0:
        next_space = words.find(" ", start)
        if 0 <= next_space < anchor:
            start = next_space + 1
    if end < len(words):
        prev_space = words.rfind(" ", 0, end)
        if prev_space > anchor:
            end = prev_space
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(words) else ""
    return f"{prefix}{words[start:end].strip()}{suffix}"


def _quoted_form(finding: Finding | None, row: FactRow) -> str:
    result = _kept_figure_result(finding, row) if finding is not None else None
    words = (result.evidence_words if result is not None else None) or row.value
    return f'"{_clamp_around_value(words, row.value)}"'


def _has_rival(row: FactRow, table_rows: Sequence[FactRow]) -> bool:
    """§4.3: same kind, matching periods (or both empty), compatible subjects, a different value."""
    for other in table_rows:
        if other is row:
            continue
        if row.kind != other.kind:
            continue
        periods_match = same_period(row.period, other.period) or (
            not row.period and not other.period
        )
        if not periods_match:
            continue
        if not same_subject(row.subject, other.subject):
            continue
        if row.value == other.value:
            continue
        return True
    return False


def _period_resolved_from_basis(row: FactRow, finding: Finding | None) -> str:
    """P3-3: name the actual basis a relative period was resolved from, rather
    than always saying "the page's date" — a resolved period may specifically
    be counted from the finding's own admitted release or statement date."""
    if finding is not None and row.period_resolved_from:
        if finding.release_date and row.period_resolved_from == finding.release_date:
            return "the release date"
        if (
            finding.statement_date
            and row.period_resolved_from == finding.statement_date
        ):
            return "the statement date"
    return "the page's date"


def _what_was_measured(row: FactRow, finding: Finding | None, rival: bool) -> str:
    subject = (row.subject or "").strip()
    starts_with_pronoun = (
        bool(subject) and cosmetic_text(subject).split()[0] in _POSSESSIVE_PRONOUNS
    )
    if not subject or starts_with_pronoun or rival:
        return _quoted_form(finding, row)
    text = subject[0].upper() + subject[1:]
    if row.scope and not _words_present(row.scope, text):
        text = f"{text} ({row.scope})"
    if row.period:
        if row.period_resolved_from:
            basis = _period_resolved_from_basis(row, finding)
            text = f"{text}, {row.period} (counted from {basis}, {row.period_resolved_from})"
        elif not _words_present(row.period, text):
            text = f"{text}, {row.period}"
    return text


```

with

```python
        return True
    return wanted <= _words(text)


```

`src/deep_research/agents/report_table.py` — replace

```python
def _who_text(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    source_url = finding.source_url if finding is not None else ""
    host = publisher_identity(source_url) if source_url else ""
    credited = _page_publisher(page_credits, source_url) if source_url else None
    if row.attribution == "own":
        org = row.organisation
        if not org or same_organisation(org, host):
            org = credited or host
        who = org
    elif row.attribution == "relayed":
        who = f"{row.organisation}, reported by {credited or (row.relay_host or '')}"
    else:  # unattributed
        who = credited or host
    return f"{who}{_when_text(finding, row.kind)}"
```

with

```python
def _who_name(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    """Who a row's figure is credited to, as the Source column names it, its date left off."""
    source_url = finding.source_url if finding is not None else ""
    host = publisher_identity(source_url) if source_url else ""
    credited = _page_publisher(page_credits, source_url) if source_url else None
    if row.attribution == "own":
        org = row.organisation
        if not org or same_organisation(org, host):
            org = credited or host
        return org
    if row.attribution == "relayed":
        return f"{row.organisation}, reported by {credited or (row.relay_host or '')}"
    return credited or host  # unattributed


def _who_text(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    return f"{_who_name(row, finding, page_credits)}{_when_text(finding, row.kind)}"


def _label_source(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    """D40: the source that reported a row, for the label of a row with no named
    item -- the organisation a relayed figure is credited to, else the name the
    Source column prints (the publisher, or the page's own site)."""
    if row.attribution == "relayed" and row.organisation:
        return row.organisation
    return _who_name(row, finding, page_credits)
```

`src/deep_research/agents/report_table.py` — replace

```python


def findings_table(composition: ReportComposition) -> ReportTable | None:
    """§4.3: eligible verified figures, capped and ordered, or ``None`` when fewer than 2 qualify."""
    required_target_ids = {
        target.target_id
```

with

```python


@dataclass(frozen=True)
class KeyFigureGroup:
    """One merged Key figures row (notes-progress-report spec §7.4 item 3): the
    values one passage states about one item, under one label. ``rows`` holds
    every fact row merged here, in row order; ``shown`` the ones whose values
    print -- a row whose value repeats a shown one only adds its finding ids."""

    label: str
    rows: tuple[FactRow, ...]
    shown: tuple[FactRow, ...]

    @property
    def values(self) -> str:
        return " \u00b7 ".join(row.value for row in self.shown)


def _capitalised(text: str) -> str:
    """``text`` with its first letter upper-cased -- unless its second letter
    already is, as in a name written ``iJava`` or ``eBay``, whose case stands."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].upper() + text[1:]


def _key_figure_measure(row: FactRow, composition: ReportComposition) -> str:
    """Spec §7.4 item 2: read the row by the sub-topic owning the most of its
    planned targets (the earlier in plan order on a tie), and take that
    sub-topic's first such target in plan order whose ``unit_dimension`` is
    set, else its first such target; a row answering no planned target keeps
    ``row.measure``. ``row.measure`` itself is the first answered target in
    sorted id order (``verified_facts.fact_rows``), which can name another
    sub-topic's measure."""
    wanted = set(row.target_ids)
    owning = [
        (index, [target for target in topic.evidence_targets if target.target_id in wanted])
        for index, topic in enumerate(composition.sub_topics)
    ]
    owning = [(index, targets) for index, targets in owning if targets]
    if not owning:
        return row.measure
    _, targets = max(owning, key=lambda pair: (len(pair[1]), -pair[0]))
    quantity = next((target for target in targets if target.unit_dimension is not None), None)
    return (quantity or targets[0]).measure


def _key_figure_label(
    row: FactRow, composition: ReportComposition, finding_by_id: Mapping[str, Finding]
) -> str | None:
    """Spec §7.4 item 2, as D40 amends it: ``{Item} \u00b7 {measure}``, the item
    being the row's subject unless it starts with a pronoun; a row with no named
    item is labelled by the source that reported it, ``{Source} \u00b7 {Measure}``
    (``_label_source``), so figures from different findings keep separate rows --
    ``{Measure}`` alone only when no source can be named. Then ``, {period}``
    when the label does not already say it. ``None`` for a row with no item whose
    measure is only "stated figure": such a row is not eligible. Never a quoted
    snippet."""
    subject = " ".join((row.subject or "").split())
    item = "" if subject and cosmetic_text(subject).split()[0] in _POSSESSIVE_PRONOUNS else subject
    measure = " ".join(_key_figure_measure(row, composition).split())
    if not item and cosmetic_text(measure) == _STATED_FIGURE:
        return None
    if item:
        label = f"{_capitalised(item)} \u00b7 {measure}"
    else:
        finding = finding_by_id.get(row.finding_id)
        source = " ".join(_label_source(row, finding, composition.page_credits).split())
        label = f"{source} \u00b7 {_capitalised(measure)}" if source else _capitalised(measure)
    if row.period and not _words_present(row.period, label):
        label = f"{label}, {row.period}"
    return label


def _value_shape(value: str) -> str:
    return cosmetic_text(_VALUE_NUMBERS.sub("#", value))


def merge_key_figures(rows: Sequence[FactRow], composition: ReportComposition) -> list[KeyFigureGroup]:
    """Spec §7.4 items 2-3, before eligibility and the cap: label each row (D40:
    a row with no named item by the source that reported it), and merge the
    values one passage states about one item.

    Rows sharing (label, primary finding, kind) form a group. Within a group, a
    row whose value equals a shown value (``cosmetic_text``) only joins that
    merged row; any other joins the first merged row holding no value of its
    shape, or starts a new one -- so "4.2 of 5 bubbles" and "87 reviews" from
    one passage merge, while two ratings never share a row. Rows from two
    passages never merge. Merged rows come back in the order their first rows
    appear; a row ``_key_figure_label`` refuses is left out.
    """
    finding_by_id = _finding_by_id(composition)
    merged: list[tuple[str, list[FactRow], list[FactRow]]] = []
    positions_by_group: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        label = _key_figure_label(row, composition, finding_by_id)
        if label is None:
            continue
        positions = positions_by_group.setdefault((label, row.finding_id, row.kind), [])
        same = next(
            (p for p in positions
             if any(cosmetic_text(shown.value) == cosmetic_text(row.value) for shown in merged[p][2])),
            None,
        )
        if same is not None:
            merged[same][1].append(row)
            continue
        shape = _value_shape(row.value)
        slot = next(
            (p for p in positions if all(_value_shape(shown.value) != shape for shown in merged[p][2])),
            None,
        )
        if slot is None:
            merged.append((label, [], []))
            slot = len(merged) - 1
            positions.append(slot)
        merged[slot][1].append(row)
        merged[slot][2].append(row)
    return [KeyFigureGroup(label=label, rows=tuple(all_rows), shown=tuple(shown)) for label, all_rows, shown in merged]


def _group_display_order(
    groups: Sequence[KeyFigureGroup], composition: ReportComposition
) -> list[KeyFigureGroup]:
    """Each merged row in the plan order of its first row's part, then by row id."""
    part_by_finding = _part_by_finding(composition)
    plan_order = [topic.coverage_id for topic in composition.sub_topics]

    def key(group: KeyFigureGroup) -> tuple[int, str]:
        part = part_by_finding.get(group.rows[0].finding_id)
        index = plan_order.index(part) if part in plan_order else len(plan_order)
        return (index, group.rows[0].row_id)

    return sorted(groups, key=key)


def key_figures_table(composition: ReportComposition) -> ReportTable | None:
    """Notes-progress-report spec §7.4: the eligible verified figures (§4.3's
    rule, ``_row_eligible``) labelled and merged, one row per label, at most
    ``MAX_KEY_FIGURE_ROWS``, with the What / Figure / Source columns; ``None``
    when fewer than 2 fact rows qualify.

    The cap keeps the merged rows whose best fact row ranks first by §4.3's
    priority (``_row_priority``; stable within a rank); of merged rows sharing
    a label only the first by that priority prints -- the table cannot tell
    them apart, and a repeated label with different figures reads as a
    contradiction (D36) -- and every other stays in the evidence log.
    """
    required_target_ids = {
        target.target_id
```

`src/deep_research/agents/report_table.py` — replace

```python
        else None
    )

    eligible = [
        row
        for row in composition.fact_rows
        if _row_eligible(
            row, finding_by_id, required_target_ids, cited, quantity_target_ids
        )
    ]
    if len(eligible) < 2:
        return None

    total = len(eligible)
    selected = _select_rows(
        eligible, finding_by_id, required_target_ids, bottom_line_cited
    )
    displayed = _display_order(selected, composition)

    kinds = {row.kind for row in displayed}
    mixed = len(kinds) > 1
    kind_caption = ""
```

with

```python
        else None
    )
    eligible = [
        row
        for row in composition.fact_rows
        if _row_eligible(row, finding_by_id, required_target_ids, cited, quantity_target_ids)
    ]
    groups = merge_key_figures(eligible, composition)
    eligible_count = sum(len(group.rows) for group in groups)
    if eligible_count < 2:
        return None

    def priority(group: KeyFigureGroup) -> int:
        return min(
            _row_priority(row, finding_by_id, required_target_ids, bottom_line_cited)
            for row in group.rows
        )

    labels: set[str] = set()
    distinct: list[KeyFigureGroup] = []
    for group in sorted(groups, key=priority):  # stable: appearance order within a rank
        if group.label not in labels:
            labels.add(group.label)
            distinct.append(group)
    displayed = _group_display_order(distinct[:MAX_KEY_FIGURE_ROWS], composition)

    kinds = {group.rows[0].kind for group in displayed}
    mixed = len(kinds) > 1
    kind_caption = ""
```

`src/deep_research/agents/report_table.py` — replace

```python

    rows: list[list[TableCell]] = []
    for row in displayed:
        finding = finding_by_id.get(row.finding_id)
        rival = _has_rival(row, displayed)
        row_ids = [row.row_id]
        finding_ids = _row_finding_ids(row)
        rows.append(
            [
                TableCell(
                    text=_what_was_measured(row, finding, rival),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(
                    text=_result_text(row, mixed, finding_by_id),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(
                    text=_who_text(row, finding, composition.page_credits),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(text="", row_ids=row_ids, finding_ids=finding_ids),
            ]
        )

    if total > MAX_FINDING_ROWS:
        caption = f"Showing {MAX_FINDING_ROWS} of {total} verified figures; all are in the evidence log."
        if kind_caption:
            caption = f"{caption} {kind_caption}"
    else:
        caption = kind_caption

    return ReportTable(
        shape="findings", columns=list(_FINDINGS_COLUMNS), rows=rows, caption=caption
    )

```

with

```python

    rows: list[list[TableCell]] = []
    for group in displayed:
        first = group.rows[0]
        row_ids = [row.row_id for row in group.rows]
        finding_ids = list(dict.fromkeys(fid for row in group.rows for fid in _row_finding_ids(row)))
        figure = " \u00b7 ".join(_result_text(row, mixed, finding_by_id) for row in group.shown)
        who = _who_text(first, finding_by_id.get(first.finding_id), composition.page_credits)
        rows.append([
            TableCell(text=group.label, row_ids=row_ids, finding_ids=finding_ids),
            TableCell(text=figure, row_ids=row_ids, finding_ids=finding_ids),
            TableCell(text=who, row_ids=row_ids, finding_ids=finding_ids),
        ])

    shown_count = sum(len(group.rows) for group in displayed)
    caption = kind_caption
    if shown_count < eligible_count:
        caption = f"Showing {shown_count} of {eligible_count} verified figures; all are in the evidence log."
        if kind_caption:
            caption = f"{caption} {kind_caption}"
    return ReportTable(
        shape="findings", columns=list(_KEY_FIGURE_COLUMNS), rows=rows, caption=caption
    )

```

- [ ] **Step 5: Run the tests; only the pinned digests fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agents/test_key_figures.py tests/test_agents/test_report_table.py tests/test_agents/test_report_layout.py tests/test_agents/test_report_writer.py tests/test_agents/test_planner_reader_answers.py tests/test_graph/test_reader_notes_replay.py tests/test_imports.py -q
.venv\Scripts\ruff.exe check --select F src/deep_research/agents/report_table.py tests/test_agents/test_key_figures.py tests/test_agents/test_report_table.py
```

Expected: `3 failed, 312 passed` — the same three pinned digest tests as Task 5 Step 4 (the replay case's table is now Key figures, its five rows labelled by source); then `All checks passed!`.

- [ ] **Step 6: Re-pin**

Run:

```powershell
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\repin.py digests
```

Expected: both run digests move, each count the baseline's own (B-count-extra, B-count-redraft), unchanged by this task: 46 and 29 on the D+A tree, different if the latency branch's batch size landed first [planning `d92891c23a2cfe2e`, `9ac8c34e25224206`]; the planner packets unchanged; `report_reviewer:ReportReviewDraft` moves [`ba466f328de6eb67`].

- [ ] **Step 7: Run the tests to verify they pass**

Run the first Step 5 command again.

Expected: `315 passed`.

- [ ] **Step 8: Commit**

```powershell
git add src/deep_research/agents/report_table.py src/deep_research/agents/report.py src/deep_research/agents/__init__.py tests/fixtures/latte-key-figures.json tests/test_agents/test_key_figures.py tests/test_agents/test_report_table.py tests/test_agents/test_report_layout.py tests/test_agents/test_report_writer.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_planner_reader_answers.py
git commit -m "feat(report): Key figures - labelled (by source when no item is named), merged within one passage, one row per label, after the topics"
```

---

### Task 7: The note lines, stamped at publication (AC23, AC34's bottom-line part)

Spec §7.2 and §7.5 item 4: at publication `_terminal_artifacts` stamps one `ReportNoteLine` per active note, in receipt order, from `note_outcome(..., terminal=True)` and `note_steering_outcome(..., terminal=True)`; the bottom line prints them after the topic lines with ✓/✗; a note's own topic line prints only as its note's line. Neither field enters the review's fingerprint.

**Files:**
- Modify: `src/deep_research/utils/types.py` (before `class BottomLineLayout`; `ReportComposition.reader_answers`' neighbour)
- Modify: `src/deep_research/graph/note_outcomes.py` (imports; the `__all__` block Task 2's helper wrote), `src/deep_research/graph/__init__.py`, `src/deep_research/graph/nodes.py` (imports `:88`; `_terminal_artifacts` `:451-515`, its `finalized` update `:500-505` on the D+A tree)
- Modify: `src/deep_research/agents/report.py` (`_bottom_line_block`, Task 5's)
- Test: `tests/test_graph/test_note_lines.py` (new)

**Interfaces:**
- Consumes: Task 2's `note_outcome`, `note_steering_outcome`; Task 4's `note_label`, `BottomLineLayout`; A's `is_research_note`, `ReaderNote.short`, `active_reader_notes`; `composition_semantic_fingerprint` (`agents/report_reviewer.py:1001-1026`), unchanged.
- Produces: `utils/types.py`: `NoteLineOutcome = Literal["covered", "not_found", "not_addressed", "not_checked"]`; `class ReportNoteLine(ContractModel)`: `note_id`, `label`, `outcome: NoteLineOutcome`, `steering_outcome: NoteLineOutcome | None`, `statement_id: str | None`, `text: str`; `ReportComposition.reader_note_lines: list[ReportNoteLine] = []`. `graph/note_outcomes.py`: `STEERING_NOTE_TEXT`, `RESEARCH_NOTE_TEXT`, `STEERING_HALF_TEXT` (dicts by outcome), `report_note_lines(state: ResearchState, composition: ReportComposition) -> list[ReportNoteLine]` (all exported from `deep_research.graph`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph/test_note_lines.py`:

```python
"""The reader's notes in the bottom line (notes-progress-report spec §7.2, §7.5; AC23, AC34).

At publication each active note gets one line, stamped from its terminal outcome:
a research note points at its topic's kept line or names its outcome; a steering
note says how the report treated it; a mixed note shows both halves. Marks are
✓ (covered), ✗ (not found or not followed) and none (not checked).
"""

from __future__ import annotations

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report
from deep_research.agents.report_reviewer import composition_semantic_fingerprint
from deep_research.graph.nodes import _terminal_artifacts
from deep_research.graph.note_outcomes import report_note_lines
from deep_research.utils.types import (
    BottomLineLayout,
    BottomLineTopic,
    EvidenceTarget,
    FindingVerification,
    NotFoundTarget,
    ReportComposition,
    ReportNoteLine,
    ReportPoint,
    ReportQualitySnapshot,
    ReportSection,
    ReportStatement,
    ResearchState,
    SubTopic,
)
from tests.evidence_fakes import make_finding, make_read
from tests.graph_fakes import fake_reader_note, fake_report_review


def _finding(n: int):
    read = make_read(f"Body {n}.", url=f"https://s{n}.example.test/page", title=f"Page {n}")
    return make_finding(read, f"Body {n}.").model_copy(
        update={"verification": FindingVerification(status="verified")})


def _point(statement_id: str, text: str, finding) -> ReportPoint:
    return ReportPoint(text=text, source_urls=[finding.source_url], statement=ReportStatement(
        statement_id=statement_id, text=text, finding_ids=[finding_fingerprint(finding)]))


def _topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id, title=title, rationale="r", search_queries=["q"],
        success_criteria=["c"], priority=1,
        evidence_targets=[EvidenceTarget(target_id=f"{coverage_id}-target-01", coverage_id=coverage_id,
                                         question="q", required=True, measure="m")],
    )


PASTRIES = fake_reader_note("n1", kinds=["new_angle"], restatement="pastries at the cafés", short="pastries")
CLOSED = fake_reader_note("n2", kinds=["exclude"], restatement="leave out cafés that might be closed",
                          short="open now")
SAFETY = fake_reader_note("n3", kinds=["emphasis"], restatement="more weight on fire-safety standards",
                          short="fire safety")
OLD = fake_reader_note("n4", kinds=["emphasis"], restatement="only downtown", short="downtown")
NEWER = fake_reader_note("n5", kinds=["scope"], restatement="only San Jose", short="San Jose", replaces="n4")
MIXED = fake_reader_note("n6", kinds=["new_angle", "exclude"],
                         restatement="pastries, but skip anything that might be closed", short="pastries")


def _state(*notes, answered=("note-n1-target-01",), verdicts=None, note_line=True,
           not_found=()) -> ResearchState:
    f = [_finding(n) for n in range(1, 4)]
    answer = _point("S001", "Agency One reports the answer.", f[0])
    line_one = _point("S002", "Agency One reports part one.", f[0])
    note_one = _point("S003", "Agency Three reports pastries.", f[2])
    note_topic = next((n for n in notes if "new_angle" in n.kinds), None)
    sub_topics = [_topic("topic-01", "Part one")]
    if note_topic is not None:
        sub_topics.append(_topic(f"note-{note_topic.note_id}", f"Your note: {note_topic.restatement}"))
    topic_lines = [BottomLineTopic(coverage_id="topic-01", label="Part one", statement_id="S002")]
    summary = [answer, line_one]
    if note_topic is not None and note_line:
        topic_lines.append(BottomLineTopic(coverage_id=f"note-{note_topic.note_id}",
                                           label=f"Your note \u00b7 {note_topic.short}", statement_id="S003"))
        summary.append(note_one)
    composition = ReportComposition(
        question="Where are the best lattes?", session_id="s1", as_of="2026-09-30T00:00:00+00:00",
        findings=f, sub_topics=sub_topics, summary=summary,
        sections=[ReportSection(title="Part one in full", short_title="Part one", coverage_id="topic-01",
                                points=[_point("S004", "Agency Two reports more on part one.", f[1])])],
        bottom_line=BottomLineLayout(answer_ids=["S001"], topic_lines=topic_lines),
        not_found=[NotFoundTarget(target_id=t, question="q", searched=True) for t in not_found],
    )
    return ResearchState(
        session_id="s1", original_question="Where are the best lattes?", sub_topics=sub_topics,
        reader_notes=list(notes), composition=composition,
        quality=ReportQualitySnapshot(answered_target_ids=list(answered)),
        report_review=fake_report_review(note_dispositions=verdicts or {}),
    )


def _bottom_line(markdown: str) -> str:
    return markdown.split("## Bottom line\n\n", 1)[1].split("\n\n## ", 1)[0]


def test_note_lines_stamped_at_publication() -> None:
    state = _state(PASTRIES, CLOSED, SAFETY, OLD, NEWER, verdicts={"n2": "honoured"})

    reader, _, _, finalized = _terminal_artifacts(state, "accepted")

    assert [(line.note_id, line.outcome, line.statement_id, line.text) for line in finalized.reader_note_lines] == [
        ("n1", "covered", "S003", ""),
        ("n2", "covered", None, "Followed: leave out cafés that might be closed"),
        ("n3", "not_checked", None, "Not checked: more weight on fire-safety standards"),
        ("n5", "not_checked", None, "Not checked: only San Jose"),
    ]
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note \u00b7 pastries:** \u2713 Agency Three reports pastries [2].\n"
        "- **Your note \u00b7 open now:** \u2713 Followed: leave out cafés that might be closed\n"
        "- **Your note \u00b7 fire safety:** Not checked: more weight on fire-safety standards\n"
        "- **Your note \u00b7 San Jose:** Not checked: only San Jose"
    )


def test_the_writer_render_prints_a_note_topic_line_as_a_plain_topic_line() -> None:
    """Spec §7.5 item 4: before publication stamps the note lines -- the render the
    reviewer reads -- a note's topic line prints with its label and no mark."""
    state = _state(PASTRIES, CLOSED)
    assert _bottom_line(render_written_report(state.composition)) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note \u00b7 pastries:** Agency Three reports pastries [2]."
    )


def test_mixed_note_line_both_results() -> None:
    """D20 (AC23, AC34): a mixed note's line is its topic line, then a sentence for
    its steering half; ✗ when either half is not found or not followed."""
    state = _state(MIXED, verdicts={"n6": "ignored_with_evidence"}, answered=("note-n6-target-01",))

    [line] = report_note_lines(state, state.composition)

    assert (line.outcome, line.steering_outcome, line.statement_id) == ("covered", "not_addressed", "S003")
    assert line.text == "The rest of your note was not followed in this report."
    reader, _, _, _ = _terminal_artifacts(state, "accepted")
    assert _bottom_line(reader).splitlines()[-1] == (
        "- **Your note \u00b7 pastries:** \u2717 Agency Three reports pastries [2]. "
        "The rest of your note was not followed in this report."
    )
    honoured = _state(MIXED, verdicts={"n6": "honoured"}, answered=("note-n6-target-01",))
    assert _bottom_line(_terminal_artifacts(honoured, "accepted")[0]).splitlines()[-1].startswith(
        "- **Your note \u00b7 pastries:** \u2713 Agency Three reports pastries [2]."
    )


def test_a_research_note_without_a_kept_line_names_its_outcome() -> None:
    not_found = _state(PASTRIES, answered=(), note_line=False, not_found=("note-n1-target-01",))
    [line] = report_note_lines(not_found, not_found.composition)
    assert (line.outcome, line.statement_id, line.text) == ("not_found", None, "No source we could check covers this.")
    assert _bottom_line(_terminal_artifacts(not_found, "partial")[0]).splitlines()[-1] == (
        "- **Your note \u00b7 pastries:** \u2717 No source we could check covers this."
    )
    unresearched = _state(PASTRIES, answered=(), note_line=False)
    unresearched = unresearched.model_copy(update={"sub_topics": unresearched.sub_topics[:1]})
    [line] = report_note_lines(unresearched, unresearched.composition)
    assert (line.outcome, line.text) == ("not_checked", "Not researched.")
    assert _bottom_line(_terminal_artifacts(unresearched, "partial")[0]).splitlines()[-1] == (
        "- **Your note \u00b7 pastries:** Not researched."
    )


def test_steering_note_lines_follow_the_reviews_verdicts() -> None:
    texts = {}
    for verdict in ("honoured", "ignored_with_evidence", "no_evidence", None):
        state = _state(CLOSED, verdicts={"n2": verdict} if verdict else {})
        [line] = report_note_lines(state, state.composition)
        texts[verdict] = (line.outcome, line.text)
    assert texts == {
        "honoured": ("covered", "Followed: leave out cafés that might be closed"),
        "ignored_with_evidence": ("not_addressed", "Not followed in this report: leave out cafés that might be closed"),
        "no_evidence": ("not_found", "No source we could check covers this: leave out cafés that might be closed"),
        None: ("not_checked", "Not checked: leave out cafés that might be closed"),
    }


def test_a_composition_without_a_layout_still_prints_its_note_lines() -> None:
    state = _state(CLOSED, verdicts={"n2": "no_evidence"})
    legacy = state.composition.model_copy(update={"bottom_line": None})
    reader, _, _, _ = _terminal_artifacts(state.model_copy(update={"composition": legacy}), "partial")
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1]. Agency One reports part one [1].\n\n"
        "- **Your note \u00b7 open now:** \u2717 No source we could check covers this: "
        "leave out cafés that might be closed"
    )


def test_fingerprint_ignores_note_lines() -> None:
    """Spec §7.2: the bottom line's layout, the note lines and the reader's answers
    never enter the review's fingerprint -- stamping them at publication cannot
    invalidate the judgement made of the same content."""
    composition = _state(PASTRIES).composition
    stamped = composition.model_copy(update={
        "reader_note_lines": [ReportNoteLine(note_id="n1", label="Your note \u00b7 pastries", outcome="covered",
                                             statement_id="S003")],
        "bottom_line": None,
        "reader_answers": ["San Jose"],
    })
    assert composition_semantic_fingerprint(stamped) == composition_semantic_fingerprint(composition)
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_note_lines.py -q
```

Expected: `1 error` — `ImportError: cannot import name 'report_note_lines' from 'deep_research.graph.note_outcomes'`.

- [ ] **Step 3: Stamp the note lines and print them**

`src/deep_research/utils/types.py` — replace

```python
class BottomLineLayout(ContractModel):
```

with

```python
NoteLineOutcome: TypeAlias = Literal["covered", "not_found", "not_addressed", "not_checked"]


class ReportNoteLine(ContractModel):
    """One reader note's line in the bottom line, stamped when the report is
    published (notes-progress-report spec §7.2): the note's terminal outcome, a
    mixed note's steering half, and either the note's kept topic line
    (``statement_id``) or code-written ``text``, or both."""

    note_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    outcome: NoteLineOutcome
    steering_outcome: NoteLineOutcome | None = None
    statement_id: str | None = None
    text: str = ""


class BottomLineLayout(ContractModel):
```

`src/deep_research/utils/types.py` — replace

```python
    reader_answers: list[str] = Field(default_factory=list)
    """The values of the reader's answers to the one-time check, in question
    order, printed on the evidence line (spec §7.5); ``[]`` when it asked nothing."""
```

with

```python
    reader_answers: list[str] = Field(default_factory=list)
    """The values of the reader's answers to the one-time check, in question
    order, printed on the evidence line (spec §7.5); ``[]`` when it asked nothing."""
    reader_note_lines: list[ReportNoteLine] = Field(default_factory=list)
    """One line per active reader note, in receipt order, stamped at publication
    (spec §7.2); ``[]`` in the writer's own composition, which prints a note's
    topic line as a plain topic line."""
```

`src/deep_research/graph/note_outcomes.py` — replace

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
    ResearchState,
    active_reader_notes,
)
```

with

```python
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
    ReportComposition,
    ReportNoteLine,
    ResearchState,
    active_reader_notes,
    note_label,
)
```

`src/deep_research/graph/note_outcomes.py` — replace

```python
__all__ = [
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
]
```

with

```python
#: A steering note's line (spec §7.2, (I)), by its terminal outcome.
STEERING_NOTE_TEXT: dict[str, str] = {
    "covered": "Followed: {restatement}",
    "not_addressed": "Not followed in this report: {restatement}",
    "not_found": "No source we could check covers this: {restatement}",
    "not_checked": "Not checked: {restatement}",
}
#: A research note's line when its topic kept no line (spec §7.2, (I)). A research
#: note's terminal outcome is covered, not_found or not_checked (spec §5.6).
RESEARCH_NOTE_TEXT: dict[str, str] = {
    "covered": "See the section below.",
    "not_found": "No source we could check covers this.",
    "not_checked": "Not researched.",
}
#: The sentence a mixed note's steering half adds (spec §7.2, D20, (I)).
STEERING_HALF_TEXT: dict[str, str] = {
    "covered": "The rest of your note was followed.",
    "not_addressed": "The rest of your note was not followed in this report.",
    "not_found": "No source we could check bears on the rest of your note.",
    "not_checked": "The rest of your note was not checked.",
}


def report_note_lines(state: ResearchState, composition: ReportComposition) -> list[ReportNoteLine]:
    """Notes-progress-report spec §7.2: one line per active reader note, in
    receipt order, with its terminal outcome (and a mixed note's steering
    half's). A research note whose topic kept a bottom-line line points at it;
    one without names its outcome in words; a steering note's line is its
    outcome and its restatement; a mixed note adds one sentence for its
    steering half. A replaced note has no line."""
    kept = (
        {line.coverage_id: line.statement_id for line in composition.bottom_line.topic_lines}
        if composition.bottom_line is not None else {}
    )
    lines: list[ReportNoteLine] = []
    for note in active_reader_notes(state.reader_notes):
        outcome = note_outcome(note.note_id, state, terminal=True)
        steering = note_steering_outcome(note.note_id, state, terminal=True)
        if is_research_note(note):
            statement_id = kept.get(f"{NOTE_COVERAGE_PREFIX}{note.note_id}")
            parts = [] if statement_id else [RESEARCH_NOTE_TEXT.get(outcome, RESEARCH_NOTE_TEXT["not_checked"])]
            if steering is not None:
                parts.append(STEERING_HALF_TEXT[steering])
            text = " ".join(parts)
        else:
            statement_id = None
            text = STEERING_NOTE_TEXT[outcome].format(restatement=note.restatement)
        lines.append(ReportNoteLine(
            note_id=note.note_id, label=note_label(note), outcome=outcome,
            steering_outcome=steering, statement_id=statement_id, text=text,
        ))
    return lines


__all__ = [
    "RESEARCH_NOTE_TEXT",
    "STEERING_HALF_TEXT",
    "STEERING_NOTE_TEXT",
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
    "report_note_lines",
]
```

`src/deep_research/graph/__init__.py` — replace

```python
from deep_research.graph.note_outcomes import (
    NoteOutcome,
    note_outcome,
    note_steering_outcome,
)
```

with

```python
from deep_research.graph.note_outcomes import (
    RESEARCH_NOTE_TEXT,
    STEERING_HALF_TEXT,
    STEERING_NOTE_TEXT,
    NoteOutcome,
    note_outcome,
    note_steering_outcome,
    report_note_lines,
)
```

`src/deep_research/graph/__init__.py` — replace

```python
    "RESEARCHER_NODE",
    "ROUTE_END",
```

with

```python
    "RESEARCHER_NODE",
    "RESEARCH_NOTE_TEXT",
    "ROUTE_END",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "SOURCE_EVALUATOR_NODE",
    "GraphConfigurationError",
```

with

```python
    "SOURCE_EVALUATOR_NODE",
    "STEERING_HALF_TEXT",
    "STEERING_NOTE_TEXT",
    "GraphConfigurationError",
```

`src/deep_research/graph/__init__.py` — replace

```python
    "quality_assessed_event",
    "report_published_event",
```

with

```python
    "quality_assessed_event",
    "report_note_lines",
    "report_published_event",
```

`src/deep_research/graph/nodes.py` — replace

```python
from deep_research.graph.live import publish_live
```

with

```python
from deep_research.graph.live import publish_live
from deep_research.graph.note_outcomes import report_note_lines
```

`src/deep_research/graph/nodes.py` — replace

```python
    finalized = composition.model_copy(
        update={
            "quality_status": status,
            "errors": list(state.errors),
        }
    )
```

with

```python
    finalized = composition.model_copy(
        update={
            "quality_status": status,
            "errors": list(state.errors),
            # Notes-progress-report spec §7.2: each reader note's line, from its
            # terminal outcome -- like ``errors``, outside the review's fingerprint.
            "reader_note_lines": report_note_lines(state, composition),
        }
    )
```

`src/deep_research/agents/report.py` — replace

```python
    ReportComposition,
    ReportOutlineEntry,
    ReportPart,
    ReportPoint,
```

with

```python
    ReportComposition,
    ReportNoteLine,
    ReportOutlineEntry,
    ReportPart,
    ReportPoint,
```

`src/deep_research/agents/report.py` — replace

```python
    then one ``- **{label}:** {line}`` per topic line. A composition without a
    layout prints one paragraph (``_plain_bottom_line``)."""
```

with

```python
    then one ``- **{label}:** {line}`` per topic line, then one line per reader
    note once publication has stamped them (spec §7.2); a note's own topic line
    prints only as its note's line. A composition without a layout prints one
    paragraph (``_plain_bottom_line``), then the note lines."""
```

`src/deep_research/agents/report.py` — replace

```python
    layout = composition.bottom_line
    if layout is None:
        return _plain_bottom_line(composition, index)
    by_id = {point.statement_id: point for point in composition.summary if point.statement is not None}
    if layout.assembled:
        paragraph = _ASSEMBLED_BOTTOM_LINE
    else:
        paragraph = " ".join(
            _rendered_point(by_id[statement_id], composition, index)
            for statement_id in layout.answer_ids if statement_id in by_id
        )
    items = [
        f"- **{line.label}:** {_rendered_point(by_id[line.statement_id], composition, index)}"
        for line in layout.topic_lines if line.statement_id in by_id
    ]
    return "\n\n".join(block for block in (paragraph, "\n".join(items)) if block)
```

with

```python
    by_id = {point.statement_id: point for point in composition.summary if point.statement is not None}
    layout = composition.bottom_line
    if layout is None:
        paragraph, items = _plain_bottom_line(composition, index), []
    else:
        printed_by_notes = {line.statement_id for line in composition.reader_note_lines if line.statement_id}
        if layout.assembled:
            paragraph = _ASSEMBLED_BOTTOM_LINE
        else:
            paragraph = " ".join(
                _rendered_point(by_id[statement_id], composition, index)
                for statement_id in layout.answer_ids if statement_id in by_id
            )
        items = [
            f"- **{line.label}:** {_rendered_point(by_id[line.statement_id], composition, index)}"
            for line in layout.topic_lines
            if line.statement_id in by_id and line.statement_id not in printed_by_notes
        ]
    items += [_note_line_item(line, by_id, composition, index) for line in composition.reader_note_lines]
    return "\n\n".join(block for block in (paragraph, "\n".join(items)) if block)


#: Spec §7.2's marks: a note half not found or not followed.
_NOTE_MISSED = frozenset({"not_found", "not_addressed"})


def _note_mark(line: ReportNoteLine) -> str:
    """Spec §7.2 (D20, D37): ``✗`` when any half of the note was not found or not
    followed, ``✓`` when every half is covered, no mark otherwise."""
    outcomes = [line.outcome] + ([line.steering_outcome] if line.steering_outcome else [])
    if any(outcome in _NOTE_MISSED for outcome in outcomes):
        return "✗"
    if all(outcome == "covered" for outcome in outcomes):
        return "✓"
    return ""


def _note_line_item(
    line: ReportNoteLine, by_id: Mapping[str, ReportPoint], composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """Spec §7.5 item 4: ``- **{note label}:** {✓ |✗ }{line}``, the line being the
    note's kept topic line, its code-written text, or the one then the other."""
    stated = (
        _rendered_point(by_id[line.statement_id], composition, index)
        if line.statement_id and line.statement_id in by_id else ""
    )
    mark = _note_mark(line)
    body = " ".join(part for part in (stated, line.text) if part)
    return f"- **{line.label}:** {mark + ' ' if mark else ''}{body}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_graph/test_note_lines.py tests/test_graph/test_note_outcomes.py tests/test_graph/test_note_routing.py tests/test_graph/test_reader_notes_state.py tests/test_graph/test_reader_notes_replay.py tests/test_agents/test_report_markdown.py tests/test_agents/test_report_bottom_line.py tests/test_imports.py -q
.venv\Scripts\ruff.exe check --select F src/deep_research/graph src/deep_research/utils/types.py tests/test_graph/test_note_lines.py
```

Expected: `113 passed` (no digest moves: a replay run without notes stamps no line, and the review reads the composition before publication), then `All checks passed!`.

- [ ] **Step 5: Commit**

```powershell
git add src/deep_research/utils/types.py src/deep_research/graph/note_outcomes.py src/deep_research/graph/__init__.py src/deep_research/graph/nodes.py src/deep_research/agents/report.py tests/test_graph/test_note_lines.py
git commit -m "feat(report): one bottom-line line per reader note, stamped at publication with its result"
```

---

### Task 8: `report_outline` on the session response

Spec §7.5 "Outline for the web": `ResearchSessionResponse` gains `report_outline: list[ReportOutlineEntryResponse] | None` via `outcome_response_fields`, from the same composition the report was rendered from; the API reference says so.

**Files:**
- Modify: `src/deep_research/api/models.py` (imports; before `class ResearchSessionResponse` `:259`; after its `clarification` field `:322` on the D+A tree)
- Modify: `src/deep_research/api/sessions.py` (imports; `outcome_response_fields` `:182` on the D+A tree)
- Modify: `README.md` (the status snapshot's fields), `docs/design/api-gaps.md` (the response's fields)
- Test: `tests/test_api/test_sessions.py`

**Interfaces:**
- Consumes: Task 5's `report_outline`, `ReportOutlineKind`.
- Produces: `api/models.py`: `class ReportOutlineEntryResponse(ApiModel)` with `heading`, `kind`, `label`, `topic_index`, `topic_count`, `note_id`; `ResearchSessionResponse.report_outline: list[ReportOutlineEntryResponse] | None = None`, set only when the outcome has both a composition and a report. Task 9 mirrors it in `web/lib/api.ts`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api/test_sessions.py`:

```python


def test_session_response_carries_the_report_outline() -> None:
    """notes-progress-report spec §7.5: the published report's headings, in order,
    from the composition the report was rendered from."""
    from deep_research.agents.report import render_written_report, report_outline

    state = judged_state()
    state = state.model_copy(update={"report": render_written_report(state.composition)})
    response = ResearchSessionResponse(
        session_id="session-1", query="Question", status="completed", iteration=1,
        started_at=datetime.now(timezone.utc), **outcome_response_fields(outcome_of(state)),
    )

    assert response.report_outline is not None
    assert [entry.model_dump() for entry in response.report_outline] == [
        entry.model_dump() for entry in report_outline(state.composition)
    ]
    assert [f"## {entry.heading}" for entry in response.report_outline] == [
        line for line in state.report.splitlines() if line.startswith("## ")
    ]


def test_a_session_without_a_published_report_has_no_outline() -> None:
    assert "report_outline" not in outcome_response_fields(outcome_of(judged_state()))
    running = ResearchSessionResponse(
        session_id="session-1", query="Question", status="running", iteration=0,
        started_at=datetime.now(timezone.utc),
    )
    assert running.report_outline is None
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api/test_sessions.py -q
```

Expected: `2 failed, 35 passed` — `test_session_response_carries_the_report_outline` and `test_a_session_without_a_published_report_has_no_outline`, both with `AttributeError: 'ResearchSessionResponse' object has no attribute 'report_outline'`.

- [ ] **Step 3: Serve the outline, and document it**

`src/deep_research/api/models.py` — replace

```python
    ReaderAnswerSource,
    ResearchError,
```

with

```python
    ReaderAnswerSource,
    ReportOutlineKind,
    ResearchError,
```

`src/deep_research/api/models.py` — replace

```python
class ResearchSessionResponse(ApiModel):
```

with

```python
class ReportOutlineEntryResponse(ApiModel):
    """One ``##`` heading of the published report, in order (notes-progress-report spec §7.5).

    ``heading`` is the heading exactly as the Markdown prints it and ``label`` its
    short name in the console's contents list; ``topic_index`` and ``topic_count``
    number a topic among the printed topics; ``note_id`` names the reader note a
    note's own topic answers.
    """

    heading: str = Field(min_length=1)
    kind: ReportOutlineKind
    label: str = Field(min_length=1)
    topic_index: int | None = Field(default=None, ge=1)
    topic_count: int | None = Field(default=None, ge=1)
    note_id: str | None = None


class ResearchSessionResponse(ApiModel):
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

    report_outline: list[ReportOutlineEntryResponse] | None = None
    """The published report's ``##`` headings, in order (notes-progress-report spec
    §7.5), so the console lays the report out as cards with a contents list;
    ``None`` while the run goes on and for a session with no report."""
```

`src/deep_research/api/sessions.py` — replace

```python
    EvidenceCountsResponse,
    ReaderNoteResponse,
```

with

```python
    EvidenceCountsResponse,
    ReaderNoteResponse,
    ReportOutlineEntryResponse,
```

`src/deep_research/api/sessions.py` — replace

```python
from deep_research.runtime.errors import ResearchConfigurationError
```

with

```python
from deep_research.agents.report import report_outline
from deep_research.runtime.errors import ResearchConfigurationError
```

`src/deep_research/api/sessions.py` — replace

```python
            context_unchecked_findings=counts.context_unchecked_findings,
            cited_findings=counts.cited_findings,
        )
    return fields
```

with

```python
            context_unchecked_findings=counts.context_unchecked_findings,
            cited_findings=counts.cited_findings,
        )
    # Notes-progress-report spec §7.5: the headings of the report ``/report``
    # serves -- the Markdown and the outline come from one composition.
    composition = outcome.composition
    if composition is not None and outcome.report is not None:
        fields["report_outline"] = [
            ReportOutlineEntryResponse(**entry.model_dump()) for entry in report_outline(composition)
        ]
    return fields
```

`README.md` — replace

```markdown
`notes_remaining`, `note_passes`, and `clarification` (the one-time check's questions and
the answers the run started with, or `null`).
```

with

```markdown
`notes_remaining`, `note_passes`, and `clarification` (the one-time check's questions and
the answers the run started with, or `null`). Once a report is published the snapshot also
carries `report_outline`: the report's `##` headings in order, each
`{heading, kind, label, topic_index, topic_count, note_id}` with `kind` one of
`bottom_line`, `topic`, `key_figures`, `options`, `not_confirmed` or `sources`
(notes-progress-report spec §7.5); it is `null` while the run goes on and for a session
with no report.
```

`docs/design/api-gaps.md` — replace

```markdown
stopped session was stopped at — `check` or a pipeline row — else `null`).
```

with

```markdown
stopped session was stopped at — `check` or a pipeline row — else `null`), and
`report_outline` (the published report's `##` headings in order, each with its kind, its
contents label and, for a topic, its number and the note it answers; `null` without a
published report — notes-progress-report spec §7.5).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api -q
```

Expected: `252 passed` (B-api + 2: planning's D+A tree printed `250`).

- [ ] **Step 5: Commit**

```powershell
git add src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/test_sessions.py README.md docs/design/api-gaps.md
git commit -m "feat(api): the session response carries the published report's outline"
```

---

### Task 9: The web's outline type and the report's pure helpers

Spec §7.6 "Types", "Chunks", "Contents", "Current section", "Evidence line on a phone": everything the cards need that does not touch the DOM, in `web/lib/report.ts`, so it is tested without rendering.

**Files:**
- Modify: `web/lib/api.ts` (`ResearchSessionResponse`, `:30` on the D+A tree)
- Create: `web/lib/report.ts`
- Test: `web/test/report.test.ts` (new)

**Interfaces:**
- Consumes: Task 8's `report_outline` on `/status`.
- Produces (`web/lib/api.ts`): `type ReportOutlineKind`; `interface ReportOutlineEntry { heading; kind; label; topic_index: number | null; topic_count: number | null; note_id: string | null }`; `ResearchSessionResponse.report_outline?: ReportOutlineEntry[] | null`.
- Produces (`web/lib/report.ts`): `CONTENTS_RAIL_MIN = 1310`; `type ContentsMode = "rail" | "chips"`; `contentsModeFor(stageWidth: number): ContentsMode`; `CURRENT_LINE_PX = 128`; `splitReport(markdown): { evidenceLine: string | null; chunks: { heading; markdown }[] }`; `type CardKind = ReportOutlineKind | "section"`; `interface ReportCard { id; kind; heading; markdown; label; eyebrow; number: number | null }`; `reportCards(chunks, outline): ReportCard[]` (ids `rep-bottom-line`, `rep-topic-{i}`, `rep-key-figures`, `rep-options`, `rep-not-confirmed`, `rep-sources`; `rep-sec-{n}` without a matching outline); `parseEvidenceLine(line): { date; count; answers: string[] } | null`; `sourceIdsOf(chunks): Set<string>`; `currentCard(tops, line): string | null`; `revealChip(nav, chip): void`. Task 10 uses all of them.

- [ ] **Step 1: Write the failing tests**

Create `web/test/report.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ReportOutlineEntry } from "../lib/api";
import {
  CONTENTS_RAIL_MIN, CURRENT_LINE_PX, contentsModeFor, currentCard, parseEvidenceLine, reportCards, revealChip,
  sourceIdsOf, splitReport,
} from "../lib/report";

const MARKDOWN = [
  "# Where are the best lattes?", "", "Evidence as of 2026-09-30 · 3 sources · San Jose · Top 3", "",
  "## Bottom line", "", "Voyager stands out [1].", "", "- **Ratings:** Voltaire rates 4.7 [2].", "",
  "## Ratings and reviews", "", "- Voltaire rates 4.7 [2].", "",
  "## Your note: pastries at the cafés", "", "- Chromatic sells pastries [3].", "",
  "## Key figures", "", "| What | Figure | Source |", "|---|---|---|", "| Voltaire · rating | 4.7 of 5 | Tripadvisor [2] |", "",
  "## What we couldn't confirm", "", "Nothing else.", "",
  "## Sources", "", "1. a.test — [A](https://a.test/)", "2. b.test — [B](https://b.test/)", "3. c.test — [C](https://c.test/)", "",
  "How this was researched: [evidence log](report-s-evidence.md)", "",
].join("\n");

const entry = (heading: string, kind: ReportOutlineEntry["kind"], label: string, topic: [number, number] | null = null, noteId: string | null = null): ReportOutlineEntry =>
  ({ heading, kind, label, topic_index: topic?.[0] ?? null, topic_count: topic?.[1] ?? null, note_id: noteId });
const OUTLINE: ReportOutlineEntry[] = [
  entry("Bottom line", "bottom_line", "Bottom line"),
  entry("Ratings and reviews", "topic", "Ratings", [1, 2]),
  entry("Your note: pastries at the cafés", "topic", "Pastries (your note)", [2, 2], "n1"),
  entry("Key figures", "key_figures", "Key figures"),
  entry("What we couldn't confirm", "not_confirmed", "Not confirmed"),
  entry("Sources", "sources", "Sources"),
];

describe("splitReport (notes-progress-report spec §7.6 Chunks)", () => {
  it("splits at every '## ' line and lifts the evidence line out of the lead", () => {
    const { evidenceLine, chunks } = splitReport(MARKDOWN.replace(/\n/g, "\r\n"));
    expect(evidenceLine).toBe("Evidence as of 2026-09-30 · 3 sources · San Jose · Top 3");
    expect(chunks.map((chunk) => chunk.heading)).toEqual([
      "Bottom line", "Ratings and reviews", "Your note: pastries at the cafés", "Key figures", "What we couldn't confirm", "Sources",
    ]);
    expect(chunks[0].markdown).toBe("## Bottom line\n\nVoyager stands out [1].\n\n- **Ratings:** Voltaire rates 4.7 [2].\n");
    expect(chunks.at(-1)!.markdown.trimEnd().endsWith("How this was researched: [evidence log](report-s-evidence.md)")).toBe(true);
  });

  it("has no evidence line when the lead holds only the question", () => {
    expect(splitReport("# Q\n\n## Bottom line\n\nA.\n").evidenceLine).toBeNull();
  });
});

describe("reportCards pairs chunks with report_outline by position", () => {
  it("numbers topics 'Topic i of N', marks a note's topic, and gives every card its contents label", () => {
    const cards = reportCards(splitReport(MARKDOWN).chunks, OUTLINE);
    expect(cards.map((card) => [card.id, card.kind, card.label, card.eyebrow, card.number])).toEqual([
      ["rep-bottom-line", "bottom_line", "Bottom line", "Bottom line", null],
      ["rep-topic-1", "topic", "Ratings", "Topic 1 of 2", 1],
      ["rep-topic-2", "topic", "Pastries (your note)", "Topic 2 of 2 · from your note", 2],
      ["rep-key-figures", "key_figures", "Key figures", "Key figures", null],
      ["rep-not-confirmed", "not_confirmed", "Not confirmed", "What we couldn't confirm", null],
      ["rep-sources", "sources", "Sources", "Sources", null],
    ]);
  });

  it("falls back to unnumbered cards named by their headings without an outline or when a heading differs", () => {
    const chunks = splitReport(MARKDOWN).chunks;
    const renamed = OUTLINE.map((item, i) => (i === 1 ? { ...item, heading: "Ratings" } : item));
    for (const outline of [null, undefined, OUTLINE.slice(1), renamed]) {
      const cards = reportCards(chunks, outline);
      expect(cards.map((card) => [card.id, card.kind, card.label, card.eyebrow, card.number])).toEqual(
        chunks.map((chunk, i) => [`rep-sec-${i + 1}`, "section", chunk.heading, chunk.heading, null]),
      );
    }
  });
});

describe("the evidence line's parts (spec §7.5, §7.6)", () => {
  it("splits the date, the count and one part per reader answer", () => {
    expect(parseEvidenceLine("Evidence as of 2026-09-30 · 29 sources · San Jose plus nearby South Bay · a place to go now")).toEqual({
      date: "2026-09-30", count: "29 sources", answers: ["San Jose plus nearby South Bay", "a place to go now"],
    });
    expect(parseEvidenceLine("Evidence as of 2026-09-30 · 1 source")).toEqual({ date: "2026-09-30", count: "1 source", answers: [] });
  });

  it("does not split any other line", () => {
    expect(parseEvidenceLine("No source could be checked.")).toBeNull();
  });
});

describe("sourceIdsOf reads the Sources card's ids before any card renders", () => {
  it("returns one src-n per numbered source", () => {
    expect([...sourceIdsOf(splitReport(MARKDOWN).chunks)]).toEqual(["src-1", "src-2", "src-3"]);
    expect(sourceIdsOf(splitReport("# Q\n\n## Bottom line\n\nA.\n").chunks).size).toBe(0);
  });
});

describe("the contents list (D28)", () => {
  it("is a rail from a 1310 px report stage, chips below it", () => {
    expect(CONTENTS_RAIL_MIN).toBe(176 + 32 + 770 + 32 + 300);
    expect([contentsModeFor(1576), contentsModeFor(1310), contentsModeFor(1309), contentsModeFor(1224), contentsModeFor(358)]).toEqual([
      "rail", "rail", "chips", "chips", "chips",
    ]);
  });

  it("marks the last card whose top has passed the line, else the first", () => {
    expect(CURRENT_LINE_PX).toBe(56 + 56 + 16);
    const tops = (...values: number[]) => values.map((top, i) => ({ id: `c${i + 1}`, top }));
    expect(currentCard(tops(300, 900, 1500), CURRENT_LINE_PX)).toBe("c1");
    expect(currentCard(tops(-400, 128.6, 700), CURRENT_LINE_PX)).toBe("c2");
    expect(currentCard(tops(-900, -300, 40), CURRENT_LINE_PX)).toBe("c3");
    expect(currentCard([], CURRENT_LINE_PX)).toBeNull();
  });

  it("scrolls the chip row just enough to show the current chip", () => {
    const nav = { scrollLeft: 0, clientWidth: 300 } as HTMLElement;
    const chip = (offsetLeft: number, offsetWidth: number) => ({ offsetLeft, offsetWidth }) as HTMLElement;
    revealChip(nav, chip(500, 120));
    expect(nav.scrollLeft).toBe(320);
    revealChip(nav, chip(400, 100));
    expect(nav.scrollLeft).toBe(320);
    revealChip(nav, chip(40, 100));
    expect(nav.scrollLeft).toBe(40);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
Push-Location web; npx vitest run test/report.test.ts; Pop-Location
```

Expected: `Test Files  1 failed (1)`, `Tests  no tests` — `Failed to resolve import "../lib/report" from "test/report.test.ts". Does the file exist?`.

- [ ] **Step 3: Write the type and the helpers**

`web/lib/api.ts` — replace

```ts
export interface ResearchSessionResponse {
```

with

```ts
/* notes-progress-report spec §7.5: one "## " heading of the published report, in order. */
export type ReportOutlineKind = "bottom_line" | "topic" | "key_figures" | "options" | "not_confirmed" | "sources";
export interface ReportOutlineEntry {
  heading: string; kind: ReportOutlineKind; label: string;
  topic_index: number | null; topic_count: number | null; note_id: string | null;
}
export interface ResearchSessionResponse {
  /* The published report's headings (spec §7.5, §7.6). Optional because a response recorded before
     the report became cards (the replay captures under test/fixtures) carries none. */
  report_outline?: ReportOutlineEntry[] | null;
```

Create `web/lib/report.ts`:

```ts
// The report as cards (notes-progress-report spec §7.6): the server's Markdown split at its "## "
// headings and paired, by position, with /status's report_outline. Pure: no DOM but the two
// measuring helpers at the foot, which take the elements they measure.
import type { ReportOutlineEntry, ReportOutlineKind } from "./api";

/* The report stage's width at which the contents list becomes a rail (D28): 176 px of rail, the
   32 px gap, the 770 px cards, the 32 px gap and the 300 px Review rail. */
export const CONTENTS_RAIL_MIN = 1310;
export type ContentsMode = "rail" | "chips";
export const contentsModeFor = (stageWidth: number): ContentsMode => (stageWidth >= CONTENTS_RAIL_MIN ? "rail" : "chips");
/* var(--topbar) + 56px (the chip row) + var(--space-4): a card whose top has passed this line is
   the current one; the cards' scroll-margin-top is the same length (globals.css). */
export const CURRENT_LINE_PX = 128;

export interface ReportChunk { heading: string; markdown: string }
export interface ReportParts { evidenceLine: string | null; chunks: ReportChunk[] }

/* Split at every line that starts with "## ". The lines before the first heading hold "# question"
   (the stage prints the question itself) and the evidence line. */
export function splitReport(markdown: string): ReportParts {
  const lead: string[] = [];
  const chunks: ReportChunk[] = [];
  for (const line of markdown.replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("## ")) chunks.push({ heading: line.slice(3), markdown: line });
    else if (chunks.length === 0) lead.push(line);
    else chunks[chunks.length - 1].markdown += "\n" + line;
  }
  const evidenceLine = lead.find((line) => line.trim() !== "" && !line.startsWith("# ")) ?? null;
  return { evidenceLine, chunks };
}

/* "section" is a card the outline could not name: no outline, or one that does not match. */
export type CardKind = ReportOutlineKind | "section";
export interface ReportCard {
  id: string; kind: CardKind; heading: string; markdown: string;
  label: string; eyebrow: string; number: number | null;
}

const FIXED_IDS: Record<Exclude<ReportOutlineKind, "topic">, string> = {
  bottom_line: "rep-bottom-line", key_figures: "rep-key-figures", options: "rep-options",
  not_confirmed: "rep-not-confirmed", sources: "rep-sources",
};

/* One card per chunk. With an outline whose headings match the chunks one to one, a topic card is
   numbered "Topic i of N" (" · from your note" for a note's topic) and every entry gives its
   contents label; otherwise every card shows its heading as its eyebrow, unnumbered. */
export function reportCards(chunks: readonly ReportChunk[], outline: readonly ReportOutlineEntry[] | null | undefined): ReportCard[] {
  if (!outline || outline.length !== chunks.length || outline.some((entry, i) => entry.heading !== chunks[i].heading)) {
    return chunks.map((chunk, i): ReportCard => ({
      heading: chunk.heading, markdown: chunk.markdown, id: `rep-sec-${i + 1}`, kind: "section",
      label: chunk.heading, eyebrow: chunk.heading, number: null,
    }));
  }
  const entries = outline;
  return chunks.map((chunk, i): ReportCard => {
    const base = { heading: chunk.heading, markdown: chunk.markdown };
    const entry = entries[i];
    if (entry.kind === "topic") {
      const fromNote = entry.note_id !== null;
      return {
        ...base, id: `rep-topic-${entry.topic_index}`, kind: "topic", label: entry.label,
        eyebrow: `Topic ${entry.topic_index} of ${entry.topic_count}${fromNote ? " · from your note" : ""}`, number: entry.topic_index,
      };
    }
    return { ...base, id: FIXED_IDS[entry.kind], kind: entry.kind, label: entry.label, eyebrow: chunk.heading, number: null };
  });
}

/* "Evidence as of {date} · {n} source(s)" then " · {answer}" per reader answer (spec §7.5); any
   other line ("No source could be checked.") is not split. */
export interface EvidenceParts { date: string; count: string; answers: string[] }
export function parseEvidenceLine(line: string): EvidenceParts | null {
  const prefix = "Evidence as of ";
  if (!line.startsWith(prefix)) return null;
  const [date, count, ...answers] = line.slice(prefix.length).split(" · ");
  return date && count ? { date, count, answers } : null;
}

/* The "src-n" ids the Sources card's list will carry, read before any card renders, so a citation
   in any card can link to its source. */
export function sourceIdsOf(chunks: readonly ReportChunk[]): Set<string> {
  const ids = new Set<string>();
  const sources = chunks.find((chunk) => chunk.heading === "Sources");
  for (const line of sources?.markdown.split("\n") ?? []) {
    const match = /^(\d+)\.\s/.exec(line);
    if (match) ids.add(`src-${match[1]}`);
  }
  return ids;
}

/* The current card: the last whose top has passed `line` px below the viewport's top (1 px allowed
   for sub-pixel rounding, so a card a jump left exactly on the line counts), else the first. */
export function currentCard(tops: readonly { id: string; top: number }[], line: number): string | null {
  let current = tops[0]?.id ?? null;
  for (const { id, top } of tops) if (top <= line + 1) current = id;
  return current;
}

/* Scroll a chip row sideways just enough to show `chip` (its offsetParent is the sticky row). */
export function revealChip(nav: HTMLElement, chip: HTMLElement): void {
  const start = chip.offsetLeft;
  const end = start + chip.offsetWidth;
  if (start < nav.scrollLeft) nav.scrollLeft = start;
  else if (end > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = end - nav.clientWidth;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
Push-Location web; npx vitest run test/report.test.ts; npm run -s typecheck; Pop-Location
```

Expected: `Tests  10 passed (10)`; `typecheck` prints nothing.

- [ ] **Step 5: Commit**

```powershell
git add web/lib/api.ts web/lib/report.ts web/test/report.test.ts
git commit -m "feat(web): the report outline type and the helpers that split and pair the report"
```

---

### Task 10: The report as cards with a contents list (AC27's unit half)

Spec §7.6: `ReportBody` is rewritten — the evidence line lifted out as `p.cap#reportEvidence` (with `.ev-pre`, `.ev-count`, `.ev-ans` spans), one `section.card.rsec` per chunk, a `nav.rep-contents` that is a sticky rail from a 1310 px report stage and a sticky chip row below it, the current entry marked as the reader scrolls and set by a click that scrolls its card to the top and focuses its heading; the bottom line card's answer as `p.lead`, an emphasis-only paragraph as `p.b-sub`, and its list as `ul.bl-list` rows of `span.k` (mark + label) and `span.bl-line`; Key figures' Source copied under each What cell for phones; the "Your notes" block and its CSS removed. `ReportStage` passes the outline instead of the notes.

**Files:**
- Modify: `web/components/ReportBody.tsx` (the whole file; the "Your notes" block was `:120-131` at `73b4d7a6`), `web/components/ReportStage.tsx:97`
- Modify: `web/app/globals.css` (`:1207` and `:1217`, `--report-card-w`; the "Your notes" rules `:1404-1410`; a new section at the end)
- Test: `web/test/components/report-body.test.tsx` (the whole file), `web/test/components/reader-notes.test.tsx` (its "Your notes" tests, A's mixed-note caption test among them, go: the block they test is gone), `web/test/components/report-stage.test.tsx` (two report fixtures gain a `## Bottom line` heading: a report with no `##` heading renders no card), `web/test/notes.test.ts` (A's `noteCaption` case goes with the function)
- Modify: `web/lib/notes.ts` (`noteCaption`, `:103-108` on the D+A tree, deleted with its `ReaderNoteRecord` import: no component calls it once the "Your notes" block is gone; spec ambiguity 12)

**Interfaces:**
- Consumes: Task 9's helpers and `ReportOutlineEntry`; `reducedMotion()` (`web/lib/handoff.ts:21`); the remark plugin API (`react-markdown`'s `Options["remarkPlugins"]`; mdast types only).
- Produces: `ReportBody({ markdown, outline, evidenceLoaded, onOpenEvidence })`; the exported plugins `remarkCitationAnchors(options?: { sourceIds?: ReadonlySet<string> })`, `remarkBottomLine()`, `remarkKeyFigures()`; the DOM Task 11's specs read: `#reportEvidence`, `.rep-layout[data-contents="rail"|"chips"]`, `nav.rep-contents` links `a[href="#rep-…"]` with `.tn` and `.rc-l` and `aria-current="true"`, `.rc-h` "Contents" in rail mode, `.rep-cards > section.card.rsec[data-kind]#rep-…`, headings `#rep-…-h`, `.rsec-eb`, `.bl-list > li > .k` (`.ok`/`.no`) and `.bl-line`, `.kf-source`, `.kf-src`.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `web/test/components/report-body.test.tsx` with:

```tsx
import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportBody } from "../../components/ReportBody";
import type { ReportOutlineEntry } from "../../lib/api";

// notes-progress-report spec §7.5-§7.6: the published report, rendered as one card per "## " section.
const MARKDOWN = `# Where are the best lattes in San Jose?

Evidence as of 2026-09-30 · 4 sources · San Jose plus nearby South Bay · a place to go now

## Bottom line

Voyager Craft Coffee stands out [1][2].

- **Ratings and reviews:** Tripadvisor ranks Voltaire Coffee House highest [2].
- **Your note · open now:** ✓ Followed: only cafés open now
- **Your note · pastries:** ✗ Chromatic sells pastries [3]. The rest of your note was not followed in this report.
- **Your note · fire safety:** Not checked: more weight on fire safety

## Ratings and reviews

- Tripadvisor gives Voltaire Coffee House 4.7 of 5 [2], and see note [9].

## Your note: pastries at the cafés

- Chromatic Coffee serves locally sourced pastries [3].

## Key figures

| What | Figure | Source |
|---|---|---|
| Voltaire Coffee House · aggregate customer rating | 4.7 of 5 · 20 reviews | Tripadvisor [2] |
| Voyager Craft Coffee · locations | 6 | Sprudge, 2025 [4] |

*Showing 2 of 9 verified figures; all are in the evidence log.*

## What we couldn't confirm

Two pages could not be opened.

## Sources

1. a.test — [Guide](https://a.test/)
2. b.test — [Ratings](https://b.test/)
3. c.test — [Pastries](https://c.test/)
4. d.test — [Locations](https://d.test/)

How this was researched: [evidence log](report-abc-0-evidence.md)
`;

const fixed = (heading: string, kind: ReportOutlineEntry["kind"], label: string): ReportOutlineEntry =>
  ({ heading, kind, label, topic_index: null, topic_count: null, note_id: null });
const OUTLINE: ReportOutlineEntry[] = [
  fixed("Bottom line", "bottom_line", "Bottom line"),
  { heading: "Ratings and reviews", kind: "topic", label: "Ratings and reviews", topic_index: 1, topic_count: 2, note_id: null },
  { heading: "Your note: pastries at the cafés", kind: "topic", label: "Pastries (your note)", topic_index: 2, topic_count: 2, note_id: "n2" },
  fixed("Key figures", "key_figures", "Key figures"),
  fixed("What we couldn't confirm", "not_confirmed", "Not confirmed"),
  fixed("Sources", "sources", "Sources"),
];

/* jsdom lays nothing out: every card's top is 0. Give each card the top it would have in a tall page. */
function stackCards() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const i = [...document.querySelectorAll(".rep-cards > .rsec")].indexOf(this);
    return { top: i < 0 ? 0 : 400 + i * 600, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  });
}
const show = (outline: ReportOutlineEntry[] | null = OUTLINE, markdown = MARKDOWN, onOpen = () => {}) =>
  render(<section id="stage-report"><ReportBody markdown={markdown} outline={outline} evidenceLoaded onOpenEvidence={onOpen} /></section>);

afterEach(() => {
  vi.restoreAllMocks();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("ReportBody: one card per section (spec §7.6 Structure, Cards)", () => {
  it("pairs each chunk with its outline entry, keeps one h2 per card inside its .prose, and shows no Your notes block", () => {
    stackCards();
    const { container } = show();
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector(".reader-notes")).toBeNull();
    const cards = [...container.querySelectorAll(".report-col > .rep-layout > .rep-cards > section.card.rsec")];
    expect(cards.map((card) => [card.id, card.getAttribute("data-kind"), card.getAttribute("aria-labelledby")])).toEqual([
      ["rep-bottom-line", "bottom_line", "rep-bottom-line-h"], ["rep-topic-1", "topic", "rep-topic-1-h"],
      ["rep-topic-2", "topic", "rep-topic-2-h"], ["rep-key-figures", "key_figures", "rep-key-figures-h"],
      ["rep-not-confirmed", "not_confirmed", "rep-not-confirmed-h"], ["rep-sources", "sources", "rep-sources-h"],
    ]);
    expect(cards.map((card) => card.querySelectorAll("h2").length)).toEqual([1, 1, 1, 1, 1, 1]);
    expect(cards.map((card) => {
      const h2 = card.querySelector(":scope > .prose > h2")!;
      return [h2.textContent, h2.className, h2.id, h2.getAttribute("tabindex")];
    })).toEqual([
      ["Bottom line", "eyebrow", "rep-bottom-line-h", "-1"],
      ["Ratings and reviews", "", "rep-topic-1-h", "-1"],
      ["Your note: pastries at the cafés", "", "rep-topic-2-h", "-1"],
      ["Key figures", "eyebrow", "rep-key-figures-h", "-1"],
      ["What we couldn't confirm", "eyebrow", "rep-not-confirmed-h", "-1"],
      ["Sources", "eyebrow", "rep-sources-h", "-1"],
    ]);
    expect(cards.map((card) => card.querySelector(":scope > p.eyebrow.rsec-eb")?.textContent ?? null)).toEqual([
      null, "Topic 1 of 2", "Topic 2 of 2 · from your note", null, null, null,
    ]);
  });

  it("renders a report with no outline, or one that does not match, as unnumbered cards named by their headings", () => {
    stackCards();
    for (const outline of [null, OUTLINE.slice(0, 5)]) {
      const { container, unmount } = show(outline);
      const cards = [...container.querySelectorAll(".rep-cards > .rsec")];
      expect(cards.map((card) => [card.id, card.getAttribute("data-kind")])).toEqual(
        [1, 2, 3, 4, 5, 6].map((n) => [`rep-sec-${n}`, "section"]),
      );
      expect(cards.every((card) => card.querySelector("h2")!.className === "eyebrow")).toBe(true);
      expect(container.querySelector(".rsec-eb")).toBeNull();
      expect([...container.querySelectorAll(".rep-contents a .rc-l")].map((label) => label.textContent)).toEqual([
        "Bottom line", "Ratings and reviews", "Your note: pastries at the cafés", "Key figures", "What we couldn't confirm", "Sources",
      ]);
      unmount();
    }
  });
});

describe("the contents list (spec §7.6 Contents, Current section; D28)", () => {
  it("lists every card, Sources included, as chips below a 1310 px stage, the first card current", () => {
    stackCards();
    const { container } = show();
    expect(container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("chips");
    const nav = container.querySelector('nav.rep-contents[aria-label="Report contents"]')!;
    expect(nav.querySelector(".rc-h")).toBeNull();
    expect([...nav.querySelectorAll("a")].map((a) => [a.getAttribute("href"), a.querySelector(".tn")!.textContent, a.querySelector(".rc-l")!.textContent])).toEqual([
      ["#rep-bottom-line", "", "Bottom line"], ["#rep-topic-1", "1", "Ratings and reviews"], ["#rep-topic-2", "2", "Pastries (your note)"],
      ["#rep-key-figures", "", "Key figures"], ["#rep-not-confirmed", "", "Not confirmed"], ["#rep-sources", "", "Sources"],
    ]);
    expect([...nav.querySelectorAll('a[aria-current="true"]')].map((a) => a.getAttribute("href"))).toEqual(["#rep-bottom-line"]);
  });

  it("is a rail headed Contents from a 1310 px report stage", () => {
    stackCards();
    let width = 1576;
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.id === "stage-report" ? width : 0;
    });
    const wide = show();
    expect(wide.container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("rail");
    expect(wide.container.querySelector(".rep-contents > .eyebrow.rc-h")!.textContent).toBe("Contents");
    wide.unmount();
    width = 1309;
    const narrow = show();
    expect(narrow.container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("chips");
  });

  it("jumps to a card on click: it becomes current, scrolls to the top and its heading takes focus", () => {
    stackCards();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const { container } = show();
    fireEvent.click(container.querySelector('.rep-contents a[href="#rep-key-figures"]')!);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(scrollIntoView.mock.contexts[0]).toBe(container.querySelector("#rep-key-figures"));
    expect(document.activeElement).toBe(container.querySelector("#rep-key-figures-h"));
    expect([...container.querySelectorAll('.rep-contents a[aria-current="true"]')].map((a) => a.getAttribute("href"))).toEqual(["#rep-key-figures"]);
  });

  it("jumps without smooth scrolling under reduced motion", () => {
    stackCards();
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion"), media: query, onchange: null,
      addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
    }));
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const { container } = show();
    fireEvent.click(container.querySelector('.rep-contents a[href="#rep-sources"]')!);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
  });
});

describe("the bottom line card (spec §7.5 items 3-4, §7.6 Cards)", () => {
  it("prints the answer as a lead, then one row per topic line and note line with its mark in the key", () => {
    stackCards();
    const { container } = show();
    const card = container.querySelector("#rep-bottom-line")!;
    expect(card.querySelector(".prose > p.lead")!.textContent).toBe("Voyager Craft Coffee stands out [1][2].");
    const rows = [...card.querySelectorAll(".prose > ul.bl-list > li")];
    expect(rows.map((li) => [
      li.querySelector(":scope > .k")!.textContent, li.querySelector(".k > .ok, .k > .no")?.className ?? null,
      li.querySelector(":scope > .bl-line")!.textContent,
    ])).toEqual([
      ["Ratings and reviews", null, "Tripadvisor ranks Voltaire Coffee House highest [2]."],
      ["✓Your note · open now", "ok", "Followed: only cafés open now"],
      ["✗Your note · pastries", "no", "Chromatic sells pastries [3]. The rest of your note was not followed in this report."],
      ["Your note · fire safety", null, "Not checked: more weight on fire safety"],
    ]);
    expect(rows[2].querySelector('.bl-line a.cite[href="#src-3"]')).not.toBeNull();
  });

  it("prints an assembled bottom line as a muted line above its topic lines, with no lead", () => {
    stackCards();
    const markdown = "# Q\n\nEvidence as of 2026-09-30 · 1 source\n\n## Bottom line\n\n"
      + "*Assembled from the sections below; the summary could not be written this time.*\n\n"
      + "- **Part one:** Agency One reports part one [1].\n\n## Sources\n\n1. a.test — [A](https://a.test/)\n";
    const { container } = show([fixed("Bottom line", "bottom_line", "Bottom line"), fixed("Sources", "sources", "Sources")], markdown);
    const card = container.querySelector("#rep-bottom-line")!;
    expect(card.querySelector("p.lead")).toBeNull();
    expect(card.querySelector("p.b-sub")!.textContent).toBe("Assembled from the sections below; the summary could not be written this time.");
    expect([...card.querySelectorAll(".bl-list > li")].map((li) => [li.querySelector(".k")!.textContent, li.querySelector(".bl-line")!.textContent])).toEqual([
      ["Part one", "Agency One reports part one [1]."],
    ]);
  });
});

describe("citations, tables and the evidence line across cards", () => {
  it("links [n] in every card to its source in the Sources card; a number with no source stays plain text", () => {
    stackCards();
    const { container } = show();
    expect([...container.querySelectorAll("#rep-sources ol.sources > li")].map((li) => li.id)).toEqual(["src-1", "src-2", "src-3", "src-4"]);
    expect(container.querySelector('#rep-topic-1 a.cite[href="#src-2"]')!.textContent).toBe("[2]");
    expect(container.querySelector('#rep-topic-2 a.cite[href="#src-3"]')).not.toBeNull();
    expect(container.querySelector('a[href="#src-9"]')).toBeNull();
    expect(container.querySelector("#rep-topic-1 li")!.textContent).toContain("[9]");
    expect(container.querySelector("#rep-bottom-line p.lead")!.textContent).toContain("[1][2]"); // a run stays a run
    for (const a of container.querySelectorAll("#rep-sources ol li a")) {
      expect([a.getAttribute("target"), a.getAttribute("rel")]).toEqual(["_blank", "noopener"]);
    }
  });

  it("gives the Key figures table's Source column a phone copy under each What cell", () => {
    stackCards();
    const { container } = show();
    const frame = container.querySelector("#rep-key-figures .tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(false);
    const table = frame.querySelector("table.tbl")!;
    expect([...table.querySelectorAll("th, td")].filter((cell) => cell.classList.contains("kf-source")).map((cell) => cell.textContent)).toEqual([
      "Source", "Tripadvisor [2]", "Sprudge, 2025 [4]",
    ]);
    const rows = [...table.querySelectorAll("tbody tr")];
    expect(rows.map((row) => row.children[0].querySelector(".kf-src")!.textContent)).toEqual(["Tripadvisor [2]", "Sprudge, 2025 [4]"]);
    expect(rows[0].children[0].querySelector('.kf-src a.cite[href="#src-2"]')).not.toBeNull();
    expect(table.querySelector("thead .kf-src")).toBeNull();
    expect(frame.nextElementSibling!.className).toContain("tbl-foot");
  });

  it("frames an options table pinned, with its citation-only Source cells marked", () => {
    stackCards();
    const markdown = "# Q\n\nEvidence as of 2026-09-30 · 2 sources\n\n## Bottom line\n\nLFP leads [1].\n\n"
      + "## Options compared\n\n| Option | Energy density | Recommended by |\n|---|---|---|\n| LFP | lower | [1] |\n| NMC | higher | [2] |\n\n"
      + "*Options compared on the parts the plan named; an empty cell reads not stated.*\n\n"
      + "## Sources\n\n1. a.test — [A](https://a.test/)\n2. b.test — [B](https://b.test/)\n";
    const outline = [fixed("Bottom line", "bottom_line", "Bottom line"), fixed("Options compared", "options", "Options compared"), fixed("Sources", "sources", "Sources")];
    const { container } = show(outline, markdown);
    expect(container.querySelector("#rep-options h2.eyebrow")!.textContent).toBe("Options compared");
    const frame = container.querySelector("#rep-options .tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(true);
    expect([...frame.querySelectorAll("tbody tr")].map((tr) => tr.lastElementChild!.classList.contains("n"))).toEqual([true, true]);
    expect(frame.querySelector(".kf-src, .kf-source")).toBeNull();
  });

  it("lifts the evidence line out of the cards as spans the phone can shorten", () => {
    stackCards();
    const { container } = show();
    const line = container.querySelector(".report-col > p.cap#reportEvidence")!;
    expect(line.textContent).toBe("Evidence as of 2026-09-30 · 4 sources · San Jose plus nearby South Bay · a place to go now");
    expect([line.querySelector(".ev-pre")!.textContent, line.querySelector(".ev-count")!.textContent]).toEqual(["Evidence as of ", " · 4 sources"]);
    expect([...line.querySelectorAll(".ev-ans")].map((part) => part.textContent)).toEqual([" · San Jose plus nearby South Bay", " · a place to go now"]);
    expect(container.querySelector(".rep-cards")!.textContent).not.toContain("Evidence as of");
  });

  it("renders a line that is not 'Evidence as of …' whole", () => {
    stackCards();
    const { container } = show(null, "# Q\n\nNo source could be checked.\n\n## Bottom line\n\nNothing could be checked.\n");
    const line = container.querySelector("#reportEvidence")!;
    expect(line.textContent).toBe("No source could be checked.");
    expect(line.querySelector("span")).toBeNull();
  });

  it("keeps the evidence-log link in the Sources card: a button once the log has loaded, muted words before", () => {
    stackCards();
    const onOpen = vi.fn();
    const { container, unmount } = show(OUTLINE, MARKDOWN, onOpen);
    const link = container.querySelector("#rep-sources p.avail#repEvidenceLink button.link")!;
    expect(link.textContent).toBe("evidence log");
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledTimes(1);
    unmount();
    const loading = render(<ReportBody markdown={MARKDOWN} outline={OUTLINE} evidenceLoaded={false} onOpenEvidence={() => {}} />);
    expect(loading.container.querySelector("#repEvidenceLink button")).toBeNull();
    expect(loading.container.querySelector("#repEvidenceLink")!.textContent).toBe("How this was researched: evidence log");
  });
});
```

`web/test/components/reader-notes.test.tsx` — replace

```tsx
import { BriefSpine } from "../../components/BriefSpine";
import { ReportBody } from "../../components/ReportBody";
import { ReportRail } from "../../components/ReportRail";
```

with

```tsx
import { BriefSpine } from "../../components/BriefSpine";
import { ReportRail } from "../../components/ReportRail";
```

`web/test/components/reader-notes.test.tsx` — replace the lines from the one starting `const MARKDOWN = ` up to, not including, the one starting `  it("names the note passes in the pass fact"`, with

```tsx
const NOTES = [
  { note_id: "n1", text: "More on fire safety", restatement: "more weight on fire-safety standards", outcome: "covered" as const },
];

/* notes-progress-report spec §7.6: the report no longer lists the reader's notes above its prose; each
   note's line is in the bottom line (test/components/report-body.test.tsx). The rail still counts the
   note passes. */
describe("the report rail's pass fact (live-briefs spec §4.2, D11)", () => {
```

`web/test/components/report-stage.test.tsx` — replace

```tsx
        return md("# Q\n\nBody text.\n");
```

with

```tsx
        return md("# Q\n\n## Bottom line\n\nBody text.\n");
```

`web/test/components/report-stage.test.tsx` — replace

```tsx
        return reportCalls === 1 ? unreachable : md("# Q\n\nBody text.\n");
```

with

```tsx
        return reportCalls === 1 ? unreachable : md("# Q\n\n## Bottom line\n\nBody text.\n");
```

`web/test/notes.test.ts` — replace

```ts
import { NOTE_LIMIT, OUTCOME_TEXT, RESEARCH_NOW, RESEARCH_WHERE, WHERE, ackFor, earlierNotesText, noteCaption, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
```

with

```ts
import { NOTE_LIMIT, OUTCOME_TEXT, RESEARCH_NOW, RESEARCH_WHERE, WHERE, ackFor, earlierNotesText, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
```

`web/test/notes.test.ts` — replace

```ts
  });

  it("captions a mixed note with both of its results, and every other note with its one", () => {
    expect(noteCaption({ outcome: "covered", steering_outcome: "not_addressed" })).toBe("covered; the rest of your note: not addressed in the report");
    expect(noteCaption({ outcome: "not_checked", steering_outcome: "not_checked" })).toBe("not checked; the rest of your note: not checked");
    expect(noteCaption({ outcome: "not_checked", steering_outcome: null })).toBe(OUTCOME_TEXT.not_checked);
    expect(noteCaption({ outcome: "pending" })).toBe("not checked yet");
  });
```

with

```ts
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```powershell
Push-Location web; npx vitest run test/components/report-body.test.tsx test/components/reader-notes.test.tsx test/components/report-stage.test.tsx test/notes.test.ts; Pop-Location
```

Expected: `Test Files  1 failed | 3 passed (4)`, `Tests  14 failed | 27 passed (41)` — every test of `report-body.test.tsx` (the old body renders one article, no cards).

- [ ] **Step 3: Rewrite `ReportBody`, pass the outline, and add the CSS**

Replace the whole of `web/components/ReportBody.tsx` with:

```tsx
"use client";
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import Markdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
// M9: `mdast` types only, never a runtime import — they arrive transitively through
// react-markdown/remark-gfm's own `@types/mdast` dependency, so AC19's "no new dependency" holds
// without declaring `@types/mdast` in package.json.
import type { Emphasis, Paragraph, Parent, PhrasingContent, Root, RootContent, Text } from "mdast";
import type { ReportOutlineEntry } from "@/lib/api";
import { reducedMotion } from "@/lib/handoff";
import {
  CURRENT_LINE_PX, contentsModeFor, currentCard, parseEvidenceLine, reportCards, revealChip, sourceIdsOf, splitReport,
  type CardKind, type ContentsMode, type ReportCard,
} from "@/lib/report";

/* remarkCitationAnchors (spec §4.3 Report rendering): marks what the design adapts with
   hProperties the components below read — the caption after a table, the Sources list ids, the
   evidence-log link line — and turns every "[n]" whose source n exists into a link to #src-n
   (prototype index.html:1378, :1398-1415: class "cite"). A bracketed number with no matching
   source (minor 3, fix round 1) stays plain text. Hand-written recursion over node.children:
   unist-util-visit is not a dependency.
   The report renders one card per "## " section (notes-progress-report spec §7.6), so
   `sourceIds` — the "src-n" ids the Sources card's list will carry, read from the whole report
   before any card renders (lib/report.ts sourceIdsOf) — lets a citation in any card link to its
   source; a Sources list in this tree adds its own ids too. */
const textOf = (node: RootContent | PhrasingContent): string =>
  node.type === "text" ? node.value : "children" in node ? (node.children as PhrasingContent[]).map(textOf).join("") : "";
const setProps = (node: { data?: { hProperties?: Record<string, unknown> } }, props: Record<string, unknown>) => {
  node.data = node.data ?? {};
  node.data.hProperties = { ...(node.data.hProperties ?? {}), ...props };
};
const isCitationLink = (node: PhrasingContent): boolean =>
  node.type === "link" && (node.data?.hProperties as Record<string, unknown> | undefined)?.["data-cite"] !== undefined;
function citationAnchors(node: Parent, sourceIds: ReadonlySet<string>): void {
  const out: PhrasingContent[] = [];
  for (const child of node.children as PhrasingContent[]) {
    if (child.type === "text") {
      const re = /\[(\d+)\]/g;
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(child.value)) !== null) {
        const id = `src-${m[1]}`;
        if (!sourceIds.has(id)) continue; // minor 3: no matching source — leave the bracket as plain text
        if (m.index > last) out.push({ type: "text", value: child.value.slice(last, m.index) });
        const anchor: PhrasingContent = { type: "link", url: `#${id}`, data: { hProperties: { "data-cite": m[1] } }, children: [{ type: "text", value: m[0] }] };
        setProps(anchor, { className: "cite" }); // through setProps, not the literal: hast's Properties types className as string[]
        out.push(anchor);
        last = m.index + m[0].length;
      }
      if (last < child.value.length) out.push({ type: "text", value: child.value.slice(last) } as Text);
    } else {
      if (child.type !== "link" && child.type !== "inlineCode" && "children" in child) citationAnchors(child as Parent, sourceIds);
      out.push(child);
    }
  }
  node.children = out;
}
/* I2 (fix round 1): a table cell whose rendered content is only citation link(s) — the Source
   column — gets class "n" (index.html:1384-1390: <td class="n"><a class="cite" …>). Runs after
   citationAnchors has already replaced the cell's "[n]" text with link nodes. */
function markCitationOnlyCells(table: Parent): void {
  for (const row of table.children as Parent[]) {
    if (!("children" in row)) continue;
    for (const cell of (row as Parent).children as Parent[]) {
      if (!("children" in cell) || cell.children.length === 0) continue;
      if ((cell.children as PhrasingContent[]).every(isCitationLink)) setProps(cell, { className: "n" });
    }
  }
}
export interface CitationOptions { sourceIds?: ReadonlySet<string> }
export function remarkCitationAnchors(options: CitationOptions = {}) {
  return (tree: Root) => {
    let inSources = false;
    const sourceIds = new Set<string>(options.sourceIds ?? []);
    tree.children.forEach((node, i) => {
      if (node.type === "heading" && node.depth === 2) { inSources = textOf(node) === "Sources"; return; }
      if (node.type === "list" && inSources) {
        inSources = false;
        const start = node.start ?? 1;
        setProps(node, { className: "sources" });
        node.children.forEach((li, k) => { const id = `src-${start + k}`; setProps(li, { id }); sourceIds.add(id); });
        return;
      }
      if (node.type === "paragraph") {
        const t = textOf(node);
        if (t.startsWith("How this was researched:")) setProps(node, { className: "avail", "data-role": "evidence-log-link" });
        else if (node.children.length === 1 && node.children[0].type === "emphasis" && tree.children[i - 1]?.type === "table") setProps(node, { className: "tbl-foot" });
      }
    });
    for (const node of tree.children) {
      if (node.type === "paragraph" || node.type === "list" || node.type === "table") citationAnchors(node as Parent, sourceIds);
      if (node.type === "table") markCitationOnlyCells(node as Parent);
    }
  };
}

/* An inline wrapper that renders as <span class=…>: an mdast emphasis node renamed through
   data.hName, so its children still pass through every later plugin (the citation anchors). */
const span = (className: string, children: PhrasingContent[]): Emphasis =>
  ({ type: "emphasis", data: { hName: "span", hProperties: { className: [className] } }, children });

/* remarkBottomLine (notes-progress-report spec §7.5 items 3-4, §7.6 Cards): in the Bottom line
   card the answer paragraph becomes p.lead, an emphasis-only paragraph (the assembled line) a
   muted p.b-sub, and the list ul.bl-list, each "- **{label}:** {✓ |✗ }{line}" item split into
   span.k — the mark (span.ok ✓ or span.no ✗), then the label without its colon — and span.bl-line. */
const MARK = /^\s*([✓✗])\s*/;
function bottomLineItem(paragraph: Paragraph): void {
  const [label, ...rest] = paragraph.children;
  if (label?.type !== "strong") return;
  const key: PhrasingContent[] = [];
  const first = rest[0];
  if (first?.type === "text") {
    const mark = MARK.exec(first.value);
    if (mark) key.push(span(mark[1] === "✓" ? "ok" : "no", [{ type: "text", value: mark[1] }]));
    rest[0] = { type: "text", value: first.value.replace(mark ? MARK : /^\s+/, "") };
  }
  key.push({ type: "text", value: textOf(label).replace(/:\s*$/, "") });
  paragraph.children = [span("k", key), span("bl-line", rest)];
}
export function remarkBottomLine() {
  return (tree: Root) => {
    let lead = false;
    for (const node of tree.children) {
      if (node.type === "paragraph") {
        if (node.children.length === 1 && node.children[0].type === "emphasis") setProps(node, { className: "b-sub" });
        else if (!lead) { setProps(node, { className: "lead" }); lead = true; }
      } else if (node.type === "list" && !node.ordered) {
        setProps(node, { className: "bl-list" });
        for (const item of node.children) if (item.children[0]?.type === "paragraph") bottomLineItem(item.children[0]);
      }
    }
  };
}

/* remarkKeyFigures (spec §7.4, §7.6 "Key figures on a phone", D32): in the What / Figure / Source
   table every third cell gets class kf-source, and each What cell also carries a copy of its row's
   Source cell as span.kf-src. At ≤ 480 px the CSS hides the column and shows the copy, so the
   forecast issuer and release stay visible; above that the copy is hidden. Runs before the
   citation anchors, so the copy's "[n]" links like the original's. */
export function remarkKeyFigures() {
  return (tree: Root) => {
    for (const node of tree.children) {
      if (node.type !== "table") continue;
      const [head] = node.children;
      if (!head || head.children.map((cell) => textOf(cell).trim()).join("|") !== "What|Figure|Source") continue;
      for (const row of node.children) {
        const [what, , source] = row.children;
        if (!what || !source) continue;
        setProps(source, { className: "kf-source" });
        if (row !== head) what.children.push(span("kf-src", structuredClone(source.children)));
      }
    }
  };
}

type Plugins = NonNullable<Options["remarkPlugins"]>;
function pluginsFor(kind: CardKind, sourceIds: ReadonlySet<string>): Plugins {
  const citations: Plugins[number] = [remarkCitationAnchors, { sourceIds }];
  return kind === "bottom_line" ? [remarkGfm, remarkBottomLine, citations] : [remarkGfm, remarkKeyFigures, citations];
}

/* "Evidence as of {date} · {n} sources" (+ " · {answer}" per reader answer) as spans: at ≤ 480 px
   .ev-pre and .ev-ans are hidden, leaving "{date} · {n} sources" (spec §7.6). Any other line
   ("No source could be checked.") renders whole. */
function EvidenceLine({ line }: { line: string }) {
  const parts = parseEvidenceLine(line);
  if (!parts) return <p className="cap" id="reportEvidence">{line}</p>;
  return (
    <p className="cap" id="reportEvidence">
      <span className="ev-pre">Evidence as of </span>{parts.date}<span className="ev-count"> · {parts.count}</span>
      {parts.answers.map((answer, i) => <span className="ev-ans" key={i}> · {answer}</span>)}
    </p>
  );
}

interface CardProps { card: ReportCard; sourceIds: ReadonlySet<string>; evidenceLoaded: boolean; onOpenEvidence(): void }
/* One section card (spec §7.6 Cards): a topic card opens with its "Topic i of N" eyebrow above
   its own h2; every other card prints its heading as h2.eyebrow. Each heading is the focus target
   of a contents jump (tabIndex -1). Memoised, with stable Markdown components: a new component
   function would remount the heading a jump has just focused whenever the current entry changes. */
const SectionCard = memo(function SectionCard({ card, sourceIds, evidenceLoaded, onOpenEvidence }: CardProps) {
  const headingId = `${card.id}-h`;
  const plugins = useMemo(() => pluginsFor(card.kind, sourceIds), [card.kind, sourceIds]);
  const components = useMemo((): Components => ({
    h1: () => null, // the question is the stage's own <h1>
    h2: ({ children }) => <h2 id={headingId} tabIndex={-1} className={card.kind === "topic" ? undefined : "eyebrow"}>{children}</h2>,
    p: ({ node, children, ...rest }) => {
      const role = (node?.properties as Record<string, unknown> | undefined)?.["dataRole"] ?? (node?.properties as Record<string, unknown> | undefined)?.["data-role"];
      if (role === "evidence-log-link") {
        return (
          <p className="avail" id="repEvidenceLink">How this was researched: {evidenceLoaded
            ? <button type="button" className="link" onClick={onOpenEvidence}>evidence log</button>
            : <span className="mono">evidence log</span>}</p>
        );
      }
      return <p {...rest}>{children}</p>;
    },
    table: ({ node, children }) => {
      const firstHeader = (() => {
        const thead = node?.children.find((c) => c.type === "element" && c.tagName === "thead");
        const tr = thead && "children" in thead ? thead.children.find((c) => c.type === "element") : undefined;
        const th = tr && "children" in tr ? tr.children.find((c) => c.type === "element") : undefined;
        const text = th && "children" in th ? th.children.map((c) => (c.type === "text" ? c.value : "")).join("") : "";
        return text.trim();
      })();
      return <div className="tbl-frame" {...(firstHeader === "Option" ? { "data-pinned": "" } : {})}><table className="tbl">{children}</table></div>;
    },
    a: ({ href, className, children }) => (href?.startsWith("#src-") ? <a href={href} className={className}>{children}</a> : <a href={href} className="tlink" target="_blank" rel="noopener">{children}</a>),
  }), [card.kind, headingId, evidenceLoaded, onOpenEvidence]);
  return (
    <section className="card rsec" data-kind={card.kind} id={card.id} aria-labelledby={headingId}>
      {card.kind === "topic" ? <p className="eyebrow rsec-eb">{card.eyebrow}</p> : null}
      <div className="prose">
        <Markdown remarkPlugins={plugins} components={components}>{card.markdown}</Markdown>
      </div>
    </section>
  );
});

/* What releases a contents click's hold on the current entry: the jump's scroll ending, or the
   reader scrolling on their own; PIN_MS covers a jump that never scrolls (already in place). */
const USER_SCROLL = ["wheel", "touchstart", "keydown"] as const;
const PIN_MS = 1500;

interface Props { markdown: string; outline: readonly ReportOutlineEntry[] | null; evidenceLoaded: boolean; onOpenEvidence(): void }

/* The report as section cards with a contents list (notes-progress-report spec §7.6, D16, D28):
   the server's Markdown split at its "## " headings, paired with /status's report_outline. The
   contents list is a sticky rail left of the cards when the report stage is at least
   CONTENTS_RAIL_MIN wide, otherwise a sticky row of chips above them. */
export function ReportBody({ markdown, outline, evidenceLoaded, onOpenEvidence }: Props) {
  const { evidenceLine, chunks } = useMemo(() => splitReport(markdown), [markdown]);
  const cards = useMemo(() => reportCards(chunks, outline), [chunks, outline]);
  const sourceIds = useMemo(() => sourceIdsOf(chunks), [chunks]);
  const rootRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const [contents, setContents] = useState<ContentsMode>("chips");
  const [current, setCurrent] = useState<string | null>(cards[0]?.id ?? null);
  const pinned = useRef(false);
  const unpin = useRef<(() => void) | null>(null);

  // D28: measured on the report stage (the viewport less the sidebar and gutters); the CSS rail
  // rules sit under the matching @container query, so both read the same width.
  useLayoutEffect(() => {
    const stage = rootRef.current?.closest<HTMLElement>("#stage-report");
    if (!stage) return;
    const measure = () => setContents(contentsModeFor(stage.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // The current entry follows the scroll, at most once a frame, unless a click has just chosen it.
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      if (pinned.current) return;
      const tops = cards.map((card) => ({ id: card.id, top: document.getElementById(card.id)?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY }));
      setCurrent(currentCard(tops, CURRENT_LINE_PX));
    };
    const onScroll = () => { if (frame === 0) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); if (frame !== 0) cancelAnimationFrame(frame); };
  }, [cards]);
  useEffect(() => () => unpin.current?.(), []);

  // In the chip row the current chip is scrolled into view.
  useEffect(() => {
    const nav = navRef.current;
    const chip = current && nav ? nav.querySelector<HTMLElement>(`a[href="#${current}"]`) : null;
    if (contents === "chips" && nav && chip) revealChip(nav, chip);
  }, [contents, current]);

  const jump = (event: MouseEvent<HTMLAnchorElement>, id: string) => {
    event.preventDefault();
    const card = document.getElementById(id);
    if (!card) return;
    unpin.current?.();
    pinned.current = true;
    const release = () => {
      pinned.current = false;
      clearTimeout(timer);
      window.removeEventListener("scrollend", release);
      for (const type of USER_SCROLL) window.removeEventListener(type, release);
      unpin.current = null;
    };
    const timer = setTimeout(release, PIN_MS);
    window.addEventListener("scrollend", release);
    for (const type of USER_SCROLL) window.addEventListener(type, release, { passive: true });
    unpin.current = release;
    setCurrent(id);
    card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    document.getElementById(`${id}-h`)?.focus({ preventScroll: true });
  };

  return (
    <div className="report-col" ref={rootRef}>
      {evidenceLine !== null ? <EvidenceLine line={evidenceLine} /> : null}
      <div className="rep-layout" data-contents={contents}>
        <nav className="rep-contents" aria-label="Report contents" ref={navRef}>
          {contents === "rail" ? <span className="eyebrow rc-h">Contents</span> : null}
          {cards.map((card) => (
            <a key={card.id} href={`#${card.id}`} aria-current={current === card.id ? "true" : undefined} onClick={(event) => jump(event, card.id)}>
              <span className="tn">{card.number ?? ""}</span><span className="rc-l">{card.label}</span>
            </a>
          ))}
        </nav>
        <div className="rep-cards">
          {cards.map((card) => <SectionCard key={card.id} card={card} sourceIds={sourceIds} evidenceLoaded={evidenceLoaded} onOpenEvidence={onOpenEvidence} />)}
        </div>
      </div>
    </div>
  );
}
```

`web/components/ReportStage.tsx` — replace

```tsx
<ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} notes={status.notes ?? []} />
```

with

```tsx
<ReportBody markdown={report.value} outline={status.report_outline ?? null} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} />
```

`web/app/globals.css` — replace

```css
   padding (--space-5 each side) and border (1px each side); the card and the 300px rail are
```

with

```css
   padding (--space-6 each side, notes-progress-report spec §7.6) and border (1px each side); the card and the 300px rail are
```

`web/app/globals.css` — replace

```css
#stage-report{--report-card-w:calc(var(--reading-max) + 2 * var(--space-5) + 2px)}
```

with

```css
#stage-report{--report-card-w:calc(var(--reading-max) + 2 * var(--space-6) + 2px)}
```

`web/app/globals.css` — replace

```css
.spine-wrap[data-arc="note_pass"] .loop-layer .loop-head{fill:var(--meta)}
/* The report's "Your notes": inside the report card and above the prose, no box of its own — an
   eyebrow, one line per note with its outcome as a caption, and a hairline below. */
.reader-notes{display:flex;flex-direction:column;gap:var(--space-2);padding-bottom:var(--space-4);border-bottom:1px solid var(--border-soft);max-width:var(--reading-max)}
.reader-notes h2{margin:0;font-weight:400}
.reader-notes .rn-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--space-1)}
.reader-notes li{font-size:var(--text-sm);line-height:1.5;color:var(--fg);overflow-wrap:anywhere}
.reader-notes li .cap{white-space:nowrap}
```

with

```css
.spine-wrap[data-arc="note_pass"] .loop-layer .loop-head{fill:var(--meta)}
```

Append to `web/app/globals.css`:

```css

/* ═══ 2026-09-30: the report as section cards with a contents list (notes-progress-report spec §7.6;
   docs/design canvas Report.dc.html B + C, progress.css:118-155) ═══
   ReportBody splits the report at its "## " headings into one card per section. The contents list is
   a sticky 176px rail left of the cards when the report stage is at least 1310px wide (176 + 32 + 770
   + 32 + 300: the rail, a gap, a card, a gap, the Review rail), and a sticky row of chips above them
   otherwise (D28). ReportBody sets data-contents from the same stage width the @container query below
   reads. Never the class `.rail`: that is the Review rail's (layout.spec.ts measures it at 300px). */
#stage-report{container:report / inline-size}
.report-col,.rep-layout,.rep-cards{display:flex;flex-direction:column;gap:var(--space-4);min-width:0}
.rep-cards > .rsec.card{display:flex;flex-direction:column;gap:var(--space-4);padding:var(--space-6);scroll-margin-top:calc(var(--topbar) + 56px + var(--space-4))}
.rsec .prose > :first-child{margin-top:0}
.rsec .prose > :last-child{margin-bottom:0}
.rsec .rsec-eb{margin:0}
/* Every card keeps an h2; a fixed section's is its eyebrow. The heading a contents jump focuses
   (tabindex -1) shows no ring. */
.rsec .prose h2.eyebrow{font-family:var(--font-mono);font-size:var(--text-xs);font-weight:400;line-height:1.5;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 var(--space-4)}
.rsec h2[tabindex="-1"]:focus{outline:none}
/* The bottom line: the answer as a lead (the prototype's own .lead, :637, is muted and 62ch wide),
   then one row per topic line and note line, the key column 132px, a note's mark ✓ ok or ✗ warn. */
.rsec[data-kind="bottom_line"] .prose p.lead{font-size:var(--text-lg);line-height:1.6;color:var(--fg);max-width:none}
.rsec[data-kind="bottom_line"] .prose p.b-sub{font-size:var(--text-sm);line-height:1.5;color:var(--muted)}
.rsec[data-kind="bottom_line"] .prose ul.bl-list{list-style:none;margin:0;padding:0}
.rsec[data-kind="bottom_line"] .bl-list li{display:grid;grid-template-columns:132px minmax(0,1fr);gap:var(--space-4);margin:0;padding:10px 0;border-top:1px solid var(--border-soft)}
.rsec[data-kind="bottom_line"] .bl-list .k{font-size:var(--text-sm);line-height:1.6;color:var(--muted)}
.rsec[data-kind="bottom_line"] .bl-list .k .ok{margin-right:4px;color:var(--status-ok)}
.rsec[data-kind="bottom_line"] .bl-list .k .no{margin-right:4px;color:var(--status-warn)}
/* Key figures: the copy of a row's Source under its What cell shows only on a phone (D32). */
.rsec .tbl .kf-src{display:none;margin-top:2px;font-size:var(--text-xs);line-height:1.5;color:var(--muted)}
/* The contents list as chips: one horizontally scrolling row, sticky under the topbar. */
.rep-contents{display:flex;gap:6px;min-width:0}
.rep-contents a{display:inline-flex;align-items:center;font-size:var(--text-sm);color:var(--muted);text-decoration:none}
.rep-layout[data-contents="chips"] .rep-contents{position:sticky;top:var(--topbar);z-index:2;overflow-x:auto;scrollbar-width:none;padding:var(--space-3) 0;background:var(--bg);border-bottom:1px solid var(--border)}
.rep-layout[data-contents="chips"] .rep-contents::-webkit-scrollbar{display:none}
.rep-layout[data-contents="chips"] .rep-contents a{flex:none;min-height:32px;padding:0 var(--space-3);border:1px solid var(--border);border-radius:var(--radius-md);white-space:nowrap}
.rep-layout[data-contents="chips"] .rep-contents a[aria-current="true"]{color:var(--fg);border-color:var(--muted);background:var(--border-soft)}
.rep-layout[data-contents="chips"] .rep-contents .tn:empty{display:none}
.rep-layout[data-contents="chips"] .rep-contents .tn::after{content:" · ";white-space:pre}
/* The contents list as a rail, once the report stage holds the rail, a card and the Review rail side by
   side. The report group, its head and the Evidence view widen together, so they stay aligned. */
@container report (min-width:1310px){
  .rep-layout[data-contents="rail"]{display:grid;grid-template-columns:176px minmax(0,1fr);gap:var(--space-8);align-items:start}
  .rep-layout[data-contents="rail"] .rep-contents{position:sticky;top:calc(var(--topbar) + var(--space-4));flex-direction:column;gap:2px;padding-top:var(--space-1)}
  .rep-layout[data-contents="rail"] .rep-contents .rc-h{padding:0 8px 6px}
  .rep-layout[data-contents="rail"] .rep-contents a{gap:6px;padding:5px 8px;border-radius:var(--radius-sm);line-height:1.35}
  .rep-layout[data-contents="rail"] .rep-contents a:hover,
  .rep-layout[data-contents="rail"] .rep-contents a[aria-current="true"]{color:var(--fg);background:var(--border-soft)}
  .rep-layout[data-contents="rail"] .rep-contents .tn{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--meta);min-width:14px}
  .report-main{grid-template-columns:minmax(0,calc(176px + var(--space-8) + var(--report-card-w))) var(--rail);max-width:calc(176px + 2 * var(--space-8) + var(--report-card-w) + var(--rail))}
  #stage-report .report-head,.evidence-view{max-width:calc(176px + 2 * var(--space-8) + var(--report-card-w) + var(--rail))}
  #reportEvidence{padding-left:calc(176px + var(--space-8))}
}
/* A phone: tighter cards, the bottom line's rows stacked, the evidence line as "{date} · {n} sources"
   (Report.dc.html:93), and the Key figures' Source shown under each What cell. */
@media (max-width:480px){
  .rep-cards > .rsec.card{padding:var(--space-4)}
  .rsec[data-kind="bottom_line"] .bl-list li{grid-template-columns:minmax(0,1fr);gap:2px}
  #reportEvidence .ev-pre,#reportEvidence .ev-ans{display:none}
  .rsec .tbl .kf-source{display:none}
  .rsec .tbl .kf-src{display:block}
}
```

`web/lib/notes.ts` — replace

```ts
import type { ReaderNoteOutcome, ReaderNoteRecord } from "./api";
```

with

```ts
import type { ReaderNoteOutcome } from "./api";
```

`web/lib/notes.ts` — replace

```ts
/* notes-progress-report spec §5.7 (D20): a mixed note's caption reads both of its results, its own topic's
   first; every other note's caption is its one outcome. */
export function noteCaption(note: Pick<ReaderNoteRecord, "outcome" | "steering_outcome">): string {
  const words = OUTCOME_TEXT[note.outcome];
  return note.steering_outcome ? words + "; the rest of your note: " + OUTCOME_TEXT[note.steering_outcome] : words;
}

/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
```

with

```ts
/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
Push-Location web; npm test; npm run -s typecheck; npm run -s check:css; Pop-Location
```

Expected: `Test Files  33 passed (33)`, `Tests  276 passed (276)` (B-web + 17: report.test.ts's 10, report-body's 14 in place of 3, reader-notes' 3 report tests and notes.test.ts's `noteCaption` case gone); `typecheck` prints nothing; `check:css` prints `OK`.

- [ ] **Step 5: Commit**

```powershell
git add web/components/ReportBody.tsx web/components/ReportStage.tsx web/app/globals.css web/lib/notes.ts web/test/components/report-body.test.tsx web/test/components/reader-notes.test.tsx web/test/components/report-stage.test.tsx web/test/notes.test.ts
git commit -m "feat(web): the report as section cards with a contents rail or chip row"
```

---

### Task 11: The report end to end on the replay server, and the captures (AC27)

Spec §11.1 (Playwright, C), §11.2 (`report.spec.ts:9-11`, `layout.spec.ts:128-163`, `notes.spec.ts`, `12-report-notes`) and §11.3 (`18-report-cards(-phone)`, `18b-report-cards-1920`; `04-report` and `12-report-notes` re-taken).

**Files:**
- Create: `web/e2e/report-layout.spec.ts`
- Modify: `web/e2e/report.spec.ts:9-11`, `web/e2e/layout.spec.ts:152-153` (the group's left edge), `web/e2e/notes.spec.ts` (the report test, located by its title), `web/e2e/visual.spec.ts` (test 12's wait; a new test 18 before `05-failed`; a 1920 describe at the end)

**Interfaces:**
- Consumes: Task 10's DOM; the replay default case `missing-target-triggers-one-extra-pass` (its report: seven sections — "Bottom line", "Adoption rate", "Widget funding", "Widget exports", "Key figures", "What we couldn't confirm", "Sources"; short titles are the replay double's first two title words); `X-Replay-Clarify: on` with "Just start" (best guesses "Global", "Since 2023", "General understanding"); `submit`, `waitTerminal` (`web/e2e/support.ts`, `stopped` included since D).
- Produces: the AC27 checks; captures `18-report-cards`, `18-report-cards-phone`, `18b-report-cards-1920`, and re-taken `04-report(-phone)`, `12-report-notes(-phone)`.

- [ ] **Step 1: Write the specs**

Create `web/e2e/report-layout.spec.ts`:

```ts
// notes-progress-report spec §7.5-§7.6 (D16, D28, D32; AC27): the report as section cards with a
// contents list, on the replay server. The default case (missing-target-triggers-one-extra-pass)
// publishes seven sections: the bottom line, three topics, Key figures, What we couldn't confirm and
// Sources. Replay never applies a reader note (api-gaps 3.9), so the note lines' look is checked on a
// report served through a route.
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const CARD_IDS = ["rep-bottom-line", "rep-topic-1", "rep-topic-2", "rep-topic-3", "rep-key-figures", "rep-not-confirmed", "rep-sources"];
const LABELS = ["Bottom line", "Adoption rate", "Widget funding", "Widget exports", "Key figures", "Not confirmed", "Sources"];
const noSideScroll = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth)).toBe(true);
const contents = (page: Page) => page.locator('nav.rep-contents[aria-label="Report contents"]');
const current = (page: Page) => contents(page).locator('a[aria-current="true"] .rc-l');
async function openReport(page: Page, request: Parameters<typeof waitTerminal>[0]) {
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
  return id;
}

test.describe("at 1252 px", () => {
  test.use({ viewport: { width: 1252, height: 853 }, reducedMotion: "reduce" });

  test("each section is its own card beside the Review rail, with a contents row of chips and no Your notes block", async ({ page, request }) => {
    await openReport(page, request);
    await expect(page.locator(".rep-cards > section.card.rsec")).toHaveCount(7);
    expect(await page.locator(".rep-cards > section.card.rsec").evaluateAll((cards) => cards.map((card) => card.id))).toEqual(CARD_IDS);
    await expect(page.locator(".rsec .rsec-eb")).toHaveText(["Topic 1 of 3", "Topic 2 of 3", "Topic 3 of 3"]);
    await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
    await expect(page.locator(".report-main .rail")).toBeVisible();
    await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", "chips");
    await expect(contents(page).locator("a .rc-l")).toHaveText(LABELS);
    await expect(contents(page).locator("a .tn")).toHaveText(["", "1", "2", "3", "", "", ""]);
    await expect(current(page)).toHaveText("Bottom line");
    expect(await contents(page).evaluate((nav) => [getComputedStyle(nav).position, getComputedStyle(nav).overflowX])).toEqual(["sticky", "auto"]);
    const nav = (await contents(page).boundingBox())!, first = (await page.locator("#rep-bottom-line").boundingBox())!;
    expect(nav.y + nav.height).toBeLessThanOrEqual(first.y);
    await noSideScroll(page);
  });

  test("a click jumps to its card and marks it; the current entry then follows the reader's scroll", async ({ page, request }) => {
    await openReport(page, request);
    await contents(page).locator('a[href="#rep-topic-2"]').click();
    await expect(current(page)).toHaveText("Widget funding");
    await expect(page.locator("#rep-topic-2-h")).toBeFocused();
    // The card's top lands on the line the current card is read from: var(--topbar) + 56px + var(--space-4).
    await expect.poll(() => page.locator("#rep-topic-2").evaluate((card) => Math.round(card.getBoundingClientRect().top))).toBe(128);
    // The reader's own scrolling (a wheel) releases the click's hold, and the line rule takes over.
    await page.mouse.move(700, 500);
    await page.mouse.wheel(0, -20_000);
    await expect(current(page)).toHaveText("Bottom line");
    const top = await page.locator("#rep-topic-3").evaluate((card) => card.getBoundingClientRect().top);
    await page.mouse.wheel(0, top - 100);
    await expect(current(page)).toHaveText("Widget exports");
    await noSideScroll(page);
  });

  test("a run that answered the one-time check shows its answers on the evidence line; Key figures keep their Source column", async ({ page, request, context }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
    await waitTerminal(request, id);
    await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("#reportEvidence")).toHaveText(/^Evidence as of \d{4}-\d{2}-\d{2} · \d+ sources? · Global · Since 2023 · General understanding$/);
    const figures = page.locator("#rep-key-figures table.tbl");
    await expect(figures.locator("th.kf-source")).toBeVisible();
    await expect(figures.locator(".kf-src").first()).toBeHidden();
  });
});

for (const [width, mode] of [[1920, "rail"], [1568, "chips"], [1252, "chips"]] as const) {
  test.describe(`at ${width} px with the sidebar expanded`, () => {
    test.use({ viewport: { width, height: 853 }, reducedMotion: "reduce" });

    test(`the contents list is ${mode === "rail" ? "a rail left of the cards" : "a row of chips above the cards"} (D28)`, async ({ page, request }) => {
      await openReport(page, request);
      await expect(page.locator("#app")).toHaveAttribute("data-sidebar", "expanded");
      await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", mode);
      const nav = (await contents(page).boundingBox())!, first = (await page.locator("#rep-bottom-line").boundingBox())!;
      if (mode === "rail") {
        await expect(contents(page).locator(".rc-h")).toHaveText("Contents");
        expect(Math.round(nav.width)).toBe(176);
        expect(Math.abs(nav.x + nav.width + 32 - first.x)).toBeLessThanOrEqual(1);
        expect(Math.round(first.width)).toBe(770);
      } else {
        await expect(contents(page).locator(".rc-h")).toHaveCount(0);
        expect(nav.y + nav.height).toBeLessThanOrEqual(first.y);
      }
      await noSideScroll(page);
    });
  });
}

test.describe("at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });

  test("chips scroll sideways, the evidence line reads '{date} · {n} sources' and Key figures show their Source under What", async ({ page, request, context }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
    await waitTerminal(request, id);
    await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", "chips");
    await expect(page.locator("#reportEvidence .ev-ans")).toHaveText([" · Global", " · Since 2023", " · General understanding"]);
    expect(await page.locator("#reportEvidence").evaluate((line) => (line as HTMLElement).innerText)).toMatch(/^\d{4}-\d{2}-\d{2} · \d+ sources?$/);
    const figures = page.locator("#rep-key-figures table.tbl");
    await expect(figures.locator("th.kf-source")).toBeHidden();
    await expect(figures.locator("tbody tr").first().locator("td").first().locator(".kf-src")).toBeVisible();
    expect(await contents(page).evaluate((nav) => nav.scrollWidth > nav.clientWidth)).toBe(true);
    await contents(page).getByRole("link", { name: "Sources" }).click();
    await expect(current(page)).toHaveText("Sources");
    expect(await contents(page).evaluate((nav) => {
      const chip = nav.querySelector<HTMLElement>('a[aria-current="true"]')!;
      return chip.offsetLeft >= nav.scrollLeft && chip.offsetLeft + chip.offsetWidth <= nav.scrollLeft + nav.clientWidth;
    })).toBe(true);
    await noSideScroll(page);
  });
});

// Replay finishes its run before a note the page sends can reach it (api-gaps 3.9), so the bottom
// line's note lines are served here: the report and its outline, exactly as the API would publish
// them for a run that took three notes.
const NOTED_REPORT = `# q

Evidence as of 2026-09-16 · 2 sources

## Bottom line

Acme Institute 17 reports 40 percent for 2024 [1].

- **Adoption rate:** Acme Institute 17 reports 40 percent for 2024 [1].
- **Your note · recycling:** ✓ Independent Bureau 2 reports 12 million dollars for 2024 [2].
- **Your note · safety:** ✗ Not followed in this report: more weight on safety standards
- **Your note · exports:** Not checked: only exports

## Adoption rate

- Acme Institute 17 reports 40 percent for 2024 [1].

## Sources

1. agency17.example.test — [Adoption survey](https://agency17.example.test/adoption-2024)
2. bureau2.example.test — [Widget funding (independent panel)](https://bureau2.example.test/widget-funding-2024-panel)

How this was researched: [evidence log](report-noted-evidence.md)
`;
const NOTED_OUTLINE = [
  { heading: "Bottom line", kind: "bottom_line", label: "Bottom line", topic_index: null, topic_count: null, note_id: null },
  { heading: "Adoption rate", kind: "topic", label: "Adoption rate", topic_index: 1, topic_count: 1, note_id: null },
  { heading: "Sources", kind: "sources", label: "Sources", topic_index: null, topic_count: null, note_id: null },
];

test.describe("the bottom line's note lines", () => {
  test.use({ viewport: { width: 1252, height: 853 }, reducedMotion: "reduce" });

  test("print one row per note, the mark in its key: ✓ ok, ✗ warn, none when not checked (AC23)", async ({ page, request, context }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    await context.route(new RegExp(`/api/research/${id}/report$`), (route) => route.fulfill({ body: NOTED_REPORT, contentType: "text/markdown; charset=utf-8" }));
    await context.route(new RegExp(`/api/research/${id}/status$`), async (route) => {
      const response = await route.fetch();
      route.fulfill({ response, json: { ...(await response.json()), report_outline: NOTED_OUTLINE } });
    });
    await page.goto(`/research/${id}`);
    const rows = page.locator("#rep-bottom-line ul.bl-list > li");
    await expect(rows).toHaveCount(4);
    await expect(rows.locator(":scope > .k")).toHaveText(["Adoption rate", "✓Your note · recycling", "✗Your note · safety", "Your note · exports"]);
    await expect(rows.nth(1).locator(".k .ok")).toHaveText("✓");
    await expect(rows.nth(2).locator(".k .no")).toHaveText("✗");
    await expect(rows.nth(3).locator(".k .ok, .k .no")).toHaveCount(0);
    const colours = await page.evaluate(() => {
      const probe = (token: string) => {
        const span = document.createElement("span");
        span.style.color = `var(${token})`;
        document.body.append(span);
        const colour = getComputedStyle(span).color;
        span.remove();
        return colour;
      };
      const mark = (selector: string) => getComputedStyle(document.querySelector(selector)!).color;
      return { ok: mark("#rep-bottom-line .k .ok"), no: mark("#rep-bottom-line .k .no"), statusOk: probe("--status-ok"), statusWarn: probe("--status-warn") };
    });
    expect([colours.ok, colours.no]).toEqual([colours.statusOk, colours.statusWarn]);
    expect(await rows.first().evaluate((li) => getComputedStyle(li).gridTemplateColumns.split(" ")[0])).toBe("132px");
  });
});
```

`web/e2e/report.spec.ts` — replace

```ts
  await expect(page.locator("#stage-report .prose p.avail").first()).toContainText(/^Evidence as of|^No source could be checked/);
  await expect(page.locator("#stage-report .prose h2", { hasText: "Bottom line" })).toBeVisible();
  await expect(page.locator("#stage-report .prose h2", { hasText: "Sources" })).toBeVisible();
```

with

```ts
  // notes-progress-report spec §7.6: the evidence line is lifted out of the cards; each card keeps its h2.
  await expect(page.locator("#reportEvidence")).toContainText(/^Evidence as of|^No source could be checked/);
  await expect(page.locator("#stage-report .rsec .prose h2", { hasText: "Bottom line" })).toBeVisible();
  await expect(page.locator("#stage-report .rsec .prose h2", { hasText: "Sources" })).toBeVisible();
```

`web/e2e/layout.spec.ts` — replace

```ts
      const groupLeft = card.x;
      const groupRight = rail.x + rail.width;
```

with

```ts
      // notes-progress-report spec §7.6 (D28): from a 1310px report stage the contents rail leads the
      // group (1920px with the sidebar expanded); below it the contents are chips above the cards.
      const contentsMode = await page.locator(".rep-layout").getAttribute("data-contents");
      expect(contentsMode).toBe(width === 1920 ? "rail" : "chips");
      const groupLeft = contentsMode === "rail" ? (await page.locator(".rep-contents").boundingBox())!.x : card.x;
      const groupRight = rail.x + rail.width;
```

`web/e2e/notes.spec.ts` — replace

```ts
test("the report lists every note above the prose, each with its outcome (AC19)", async ({ page, request }) => {
```

with

```ts
test("the report shows no Your notes block, and a note the run never read adds no line to its bottom line (AC19, AC27)", async ({ page, request }) => {
```

`web/e2e/notes.spec.ts` — replace the lines from the one starting `  const card = page.locator("#stage-report article.card.stack");` up to, not including, the one starting `  const status = await (await request.get(`, with

```ts
  // notes-progress-report spec §7.6: a note's line lives in the bottom line, stamped at publication from
  // the run's own notes. Replay's engine finished before these notes arrived (api-gaps 3.9), so the run
  // holds none: the bottom line prints no note line, and /status still lists both.
  await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
  await expect(page.locator("#rep-bottom-line .bl-list .k", { hasText: "Your note" })).toHaveCount(0);
```

`web/e2e/visual.spec.ts` — replace

```ts
    // note line at the card's foot (pick 6A); then the report's "Your notes" above the prose (§4.7).
```

with

```ts
    // note line at the card's foot (pick 6A); then the report as cards, with no "Your notes" block
    // (notes-progress-report spec §7.6): replay never applies a note (api-gaps 3.9).
```

`web/e2e/visual.spec.ts` — replace

```ts
      await expect(page.locator("#stage-report .reader-notes")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible();
```

with

```ts
      await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
```

`web/e2e/visual.spec.ts` — replace

```ts
    test(`05-failed${suffix}`, async ({ page, request, context }) => {
```

with

```ts
    // notes-progress-report spec §11.3: the report as section cards, reached through the one-time check
    // so its evidence line carries the reader's answers (the phone shortens it to "{date} · {n} sources").
    test(`18-report-cards${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
      await waitTerminal(request, id);
      await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `18-report-cards${suffix}`);
    });

    test(`05-failed${suffix}`, async ({ page, request, context }) => {
```

Append to `web/e2e/visual.spec.ts`:

```ts

// notes-progress-report spec §11.3 (D28): at 1920px the report stage holds the contents rail, the
// cards and the Review rail side by side.
test.describe("captures at 1920", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test("18b-report-cards-1920", async ({ page, request }) => {
    const id = await submit(page, "What is the current state of grid-scale battery storage?");
    await waitTerminal(request, id);
    await expect(page.locator('.rep-layout[data-contents="rail"]')).toBeVisible({ timeout: 20_000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await shoot(page, "18b-report-cards-1920");
  });
});
```

- [ ] **Step 2: Type-check and list them**

Run:

```powershell
Push-Location web; npm run -s typecheck; npx playwright test --list --project=chromium | Select-Object -Last 1; npx playwright test --list --project=visual | Select-Object -Last 1; Pop-Location
```

Expected: `typecheck` prints nothing; `Total: 76 tests in 21 files` (B-e2e + 8, + 1 file); `Total: 17 tests in 1 file` (B-visual + 3).

- [ ] **Step 3: Run the end-to-end suite** [not run in planning]

Run:

```powershell
Push-Location web; npm run test:e2e; Pop-Location
```

Expected: every test passes, `76 passed`. Reasoning, since planning could not run it: the selectors are the ones Task 10's Vitest pins; the default case's sections, labels and topic eyebrows are those planning printed from the same replay case offline (`tests/test_api/replay_support.py::replay_outcome`), and its stage widths are 1252 − 296 − 48 = 908 px (chips), 1568 − 296 − 48 = 1224 px (chips) and 1920 − 296 − 48 = 1576 px (rail); a jump lands a card's top at `scroll-margin-top` 128 px when the page has at least 853 − 128 px below it, which the topic-2 card has; the phone's evidence line hides `.ev-pre` and `.ev-ans`, leaving `{date} · {n} sources`. If a test fails, fix the spec or the code to the spec's §7.6, never the expectation's meaning, and record the change.

- [ ] **Step 4: Take the captures** [not run in planning]

Run:

```powershell
Push-Location web; $env:VISUAL_CHECKPOINT = "phase-c"; npm run capture:visual; Remove-Item Env:VISUAL_CHECKPOINT; Pop-Location
```

Expected: `17 passed`, and `web/visual/phase-c/` holds, among the others, `04-report.png`, `04-report-phone.png`, `12-report-notes.png`, `12-report-notes-phone.png`, `18-report-cards.png`, `18-report-cards-phone.png` and `18b-report-cards-1920.png` (`web/visual/` is git-ignored).

- [ ] **Step 5: Review the captures at full height against the canvas**

Open `.superpowers/progress-canvas/project/Report.dc.html` (styled by `progress.css` over `theme.css`) beside each capture and check, writing one line per capture into the task summary:
- `18b-report-cards-1920`: the 176 px contents rail left of the cards, headed "Contents", Bottom line current, topic numbers in the mono `.tn` column; cards on the page ground; the Review rail at right; the head and the evidence line aligned to the group (canvas desktop artboard).
- `18-report-cards` and `04-report`: the chip row above the cards with Bottom line current, under the evidence line; the bottom line card's lead, then its rows with a 132 px key column and hairlines; topic cards with "Topic i of 3" eyebrows; Key figures with five rows, each labelled by its source (D40), and the caption "No figure in this table is a forecast."; no "Your notes" block.
- `18-report-cards-phone`: the evidence line reads `{date} · {n} sources`; chips scroll sideways; cards at 16 px padding; bottom-line rows stacked; Key figures with no Source column and the source under each label (canvas phone artboard).
- `12-report-notes(-phone)`: the report as cards with no note line and no "Your notes" block (replay never applies a note).

Theme (D19): no colour outside the tokens; ✓ green and ✗ amber appear only as note marks (proved on a served report by `report-layout.spec.ts`); no purple other than the one primary button.

- [ ] **Step 6: Commit**

```powershell
git add web/e2e/report-layout.spec.ts web/e2e/report.spec.ts web/e2e/layout.spec.ts web/e2e/notes.spec.ts web/e2e/visual.spec.ts
git commit -m "test(web): the report's cards, contents and note lines end to end, and their captures"
```

---

### Task 12: The design record

Spec §7.8, plus spec ambiguity 19: `DESIGN.md` §3's inventory row 4 and the note paragraph, §2.A's order sentence, a §5.6 bullet for the contents jump under reduced motion; the superseding note on the consumer-format spec; `web/README.md`'s running notes.

**Files:**
- Modify: `docs/design/DESIGN.md` (§2.A `:66-68`, row 4 `:161`, the note paragraph `:254-263` on the D+A tree, §5.6's bullets), `docs/superpowers/specs/2026-09-25-consumer-report-format.md:1`, `web/README.md` (`:23-24`, the `capture:visual` line D wrote, and `:38-40`, on the D+A tree)

**Interfaces:**
- Consumes: Tasks 5–11 as built.
- Produces: the design record the next phase (B) reads.

- [ ] **Step 1: Update the documents**

`docs/design/DESIGN.md` — replace

```markdown
The centre column is the report as a document, in the server's own order: the
bottom line, the findings or options table, the parts with their cited points,
what could not be confirmed, the sources. A details rail beside it carries the
```

with

```markdown
The centre column is the report as a document, in the server's own order: the
bottom line (a direct answer, then one line per topic and per reader note), the
parts with their cited points, the Key figures or Options compared table, what
could not be confirmed, the sources — each section its own card, with a contents
list beside or above them (notes-progress-report §7.5–§7.6). A details rail beside it carries the
```

`docs/design/DESIGN.md` — replace

```markdown
| The question, the settings in force, actions, then — when the reader added notes — **Your notes**, the server's Markdown body and its rail — or the Evidence view |
```

with

```markdown
| The question, the settings in force, actions, then the server's Markdown as one card per section — the bottom line with a line for each topic and each reader note first — with a contents list (a sticky rail left of the cards from a 1310px report stage, a sticky row of chips above them below that; notes-progress-report §7.6, D28), and its rail — or the Evidence view |
```

`docs/design/DESIGN.md` — replace

```markdown
next edit. The report then states what became of each note: inside the report card and
above the prose — not a card of its own, and outside `.prose` — `Your notes` lists each
note as written with its outcome as a caption: `covered`, `couldn't find evidence`,
`not addressed in the report` (the findings bore on it and the report still does not
follow it, after its one redraft — never `covered`), `not checked` (nothing in the
finished run could judge it: no review did, or its own topic never researched it) or
`replaced by a later note`. A research note's caption comes from its own topic's targets,
never from the review: `covered` once a verified finding answers one of them. A mixed
note's caption reads both of its results — `{its topic's result}; the rest of your note:
{its steering result}` (notes-progress-report §5.6–§5.7, D20, D31).
```

with

```markdown
next edit. The report then states what became of each note in its bottom line, after the
topic lines: one line per note, labelled `Your note · {short}` (the note's subject in one to
three words). A research note's line is its own topic's line, or says `No source we could
check covers this.` or `Not researched.`; a steering note's line says how the report treated
it — `Followed:`, `Not followed in this report:`, `No source we could check covers this:` or
`Not checked:`, then the run's reading of the note. A mark leads the line: ✓ when the note
was covered, ✗ when it was not found or not followed, none when nothing in the finished run
could judge it. A mixed note's line is its topic's, then one sentence for the rest of the
note, with ✗ when either half missed (notes-progress-report §7.2, D20, D37). There is no
separate notes block. Each note's outcome also stays on the session's `/status`: `covered`,
`not_found`, `not_addressed` (the findings bore on it and the report still does not follow it,
after its one redraft — never `covered`), `not_checked` (nothing in the finished run could
judge it: no review did, or its own topic never researched it) or `replaced`; a research
note's comes from its own topic's targets, never from the review (§5.6, D31).
```

`docs/design/DESIGN.md` — replace

```markdown
  the row a hand-off is opening. **Under reduced motion** it fades in place over
  160ms, like every brief line.
```

with

```markdown
  the row a hand-off is opening. **Under reduced motion** it fades in place over
  160ms, like every brief line.
- **A contents jump scrolls, then lands on the heading** (notes-progress-report §7.6,
  2026-09-30). Choosing an entry in the report's contents marks it current, scrolls its
  card to the top — `scroll-margin-top` is the topbar, the chip row's 56px and
  `--space-4` — smoothly, and moves focus to the card's heading, which shows no ring.
  **Under reduced motion** the scroll is instant (`behavior: "auto"`); the current entry
  and the focus move the same way, and the chip row scrolls its current chip into view
  without animation either way.
```

`docs/superpowers/specs/2026-09-25-consumer-report-format.md` — replace

```markdown
# Consumer report format: answer-first skeleton, question-shaped table, parallel writer

```

with

```markdown
# Consumer report format: answer-first skeleton, question-shaped table, parallel writer

> **Superseded in part (2026-09-30)** by `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` §7: the bottom line is a direct answer of at most two sentences, then one line per topic and one per reader note; the findings table becomes Key figures (What · Figure · Source, at most 10 rows, one per label) and prints after the topic sections, as `## Key figures` or `## Options compared`; and the console shows the report as one card per section with a contents list. Everything else below stands.

```

`web/README.md` — replace

```markdown
  applied, and the report's "Your notes" reads it `not checked`; `e2e/notes.spec.ts` and the
  `11-note-ack` and `12-report-notes` captures use it.
```

with

```markdown
  applied: `/status` reads it `not_checked`, and the report's bottom line has no line for it
  (notes-progress-report spec §7.2); `e2e/notes.spec.ts` and the `11-note-ack` and
  `12-report-notes` captures use it.
- The report (notes-progress-report spec §7.6): one card per section, with a contents list
  that is a sticky rail left of the cards from a 1310px report stage and a sticky row of
  chips above them below that; `e2e/report-layout.spec.ts` and the `18-report-cards` and
  `18b-report-cards-1920` captures use it.
```

The captures line counts what Task 11 adds: thirteen stages and views at each of 1252 and 390 px (D's twelve and `18-report-cards`), and `18b-report-cards-1920` (review 1, M5).

`web/README.md` — replace

```markdown
- `npm run capture:visual` — the twenty-four full-page captures (12 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

with

```markdown
- `npm run capture:visual` — the twenty-seven full-page captures (13 stages/views × 1252 and
  390 px, and the report's cards at 1920 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
```

- [ ] **Step 2: Check that nothing still describes the old report**

Run:

```powershell
git grep -n "Your notes" -- docs/design/DESIGN.md docs/design/api-gaps.md web/README.md README.md
git grep -c "notes-progress-report §7" -- docs/design/DESIGN.md
```

Expected: the first prints nothing; the second prints `docs/design/DESIGN.md:4`.

- [ ] **Step 3: Commit**

```powershell
git add docs/design/DESIGN.md docs/superpowers/specs/2026-09-25-consumer-report-format.md web/README.md
git commit -m "docs(design): the report as cards with contents, and the reader's notes in the bottom line"
```

---

### Task 13: Final verification

**Files:** none changed.

- [ ] **Step 1: The whole suite, the lint and the old name**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q --deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config
.venv\Scripts\python.exe .superpowers\sdd\2026-09-30-phase-c\lint_compare.py compare
git grep -n "MAX_BOTTOM_LINE_SENTENCES" -- src tests
git grep -n -w "findings_table" -- src tests web
```

Expected: `B-py + 52 passed, 2 deselected` (planning: `5069 passed, 2 deselected`); `new: none` and `gone:` exactly the one `tests\test_agents\test_report_writer.py: F401 … MAX_BOTTOM_LINE_SENTENCES imported but unused` finding; the two greps print nothing (`-w` matches the name alone, not the two kept tests named `test_capped_findings_table_keeps_the_*_caption`, which now exercise Key figures' captions).

- [ ] **Step 2: The web**

Run:

```powershell
Push-Location web; npm test; npm run -s typecheck; npm run -s check:css; Pop-Location
```

Expected: `Tests  B-web + 17 passed` (planning `276`), no `typecheck` output, `OK`.

- [ ] **Step 3: The acceptance criteria, each by its test**

Run:

```powershell
.venv\Scripts\python.exe -m pytest "tests/test_agents/test_report_bottom_line.py::test_bottom_line_request_reader_answers_and_ids" "tests/test_agents/test_report_bottom_line.py::test_answer_overflow_refused" "tests/test_agents/test_report_bottom_line.py::test_missing_outcome_reask_text" "tests/test_agents/test_report_bottom_line.py::test_bottom_line_examples_valid_and_name_topics" "tests/test_agents/test_report_bottom_line.py::test_no_old_sentence_cap_name_remains" "tests/test_agents/test_report_bottom_line.py::test_bottom_line_topic_line_rules" "tests/test_agents/test_report_bottom_line.py::test_fallback_one_line_per_topic" "tests/test_agents/test_report_bottom_line.py::test_fallback_move_drops_emptied_section" "tests/test_agents/test_report_markdown.py::test_bottom_line_layout_and_markdown" "tests/test_agents/test_report_markdown.py::test_markdown_heading_order" "tests/test_agents/test_report_markdown.py::test_report_outline_matches_headings" "tests/test_agents/test_report_markdown.py::test_the_outline_numbers_only_the_topic_sections_that_remain" "tests/test_agents/test_key_figures.py::test_key_figures_labels_and_merge_latte" "tests/test_agents/test_key_figures.py::test_key_figures_measure_rule" "tests/test_agents/test_key_figures.py::test_a_row_with_no_named_item_is_labelled_by_its_source" "tests/test_agents/test_key_figures.py::test_the_replay_default_case_labels_its_figures_by_their_sources" "tests/test_graph/test_note_lines.py::test_note_lines_stamped_at_publication" "tests/test_graph/test_note_lines.py::test_mixed_note_line_both_results" "tests/test_graph/test_note_lines.py::test_fingerprint_ignores_note_lines" -q
```

Expected: `19 passed`. AC27's checks are Task 11 Step 3's `e2e/report-layout.spec.ts` run.

- [ ] **Step 4: Nothing left uncommitted**

Run:

```powershell
git status --short --untracked-files=no
git log --oneline -11
```

Expected: `git status` prints nothing; the log's top eleven commits are this plan's (Tasks 2–12), newest first.

---

## Acceptance-criteria coverage

| AC | What it asks | Where it is built | Where it is proved |
|---|---|---|---|
| AC22 | The request's `# Reader answers` (answer lines only) and `## {coverage_id} · {title}` headers; at most two answer sentences, a third refused as "over the direct answer's two sentences"; topic lines pass §7.1's rules and the Statement Check; the re-ask's exact text | Tasks 3, 4 | `test_bottom_line_request_reader_answers_and_ids`, `test_answer_overflow_refused`, `test_missing_outcome_reask_text`, `test_bottom_line_topic_line_rules`, `test_a_replayed_bottom_line_keeps_its_answer_and_its_topic_line` |
| AC36 | The two reply examples under `# Reply format`; each validates, has ≤ 2 sentences, ≥ 1 topic, only its own headers' topic ids, marks that are spans of their point; no `MAX_BOTTOM_LINE_SENTENCES` | Task 3 | `test_bottom_line_examples_valid_and_name_topics`, `test_the_request_shows_both_examples_under_its_reply_format`, `test_no_old_sentence_cap_name_remains`, `test_tool_free_prompts.py`'s example table |
| AC23 | `## Bottom line`: the answer paragraph and a list — one line per kept topic line (its short title) and one per active note ("Your note · {short}", mark per outcome; a mixed note's both results) | Tasks 4, 5, 7 | `test_bottom_line_layout_and_markdown`, `test_note_lines_stamped_at_publication`, `test_mixed_note_line_both_results`, `test_a_research_note_without_a_kept_line_names_its_outcome`, `test_steering_note_lines_follow_the_reviews_verdicts`; the look: `report-body.test.tsx`, `report-layout.spec.ts` (served report) |
| AC34 (bottom-line part) | A mixed note's bottom-line line shows both results with the combined mark | Task 7 | `test_mixed_note_line_both_results` |
| AC24 | The fallback: the assembled label, one checked line per topic, floor and dispute protections; a topic whose only point moved has no section and no card; `report_outline` numbers the sections that remain | Tasks 4, 5 | `test_fallback_one_line_per_topic`, `test_fallback_move_drops_emptied_section`, `test_every_sentence_refused_falls_back_to_one_line_per_topic`, `test_the_outline_numbers_only_the_topic_sections_that_remain`, `test_an_assembled_bottom_line_says_so_above_its_topic_lines` |
| AC25 | The latte rows: the merged row "Bijan Bakery · aggregate customer rating" / "4.2 of 5 bubbles · 87 reviews"; no merged row from two primary findings; no quoted-snippet label; Key figures after the topics, ≤ 10 rows, distinct labels, What / Figure / Source; with D40, a row with no named item labelled by its source | Tasks 5, 6 | `test_key_figures_labels_and_merge_latte`, `test_key_figures_measure_rule`, `test_a_row_with_no_named_item_is_labelled_by_its_source`, `test_the_replay_default_case_labels_its_figures_by_their_sources`, `test_markdown_heading_order` |
| AC26 | The heading order of §7.5, and `report_outline` one to one with the headings | Task 5 (served by Task 8) | `test_markdown_heading_order`, `test_report_outline_matches_headings`, `test_session_response_carries_the_report_outline` |
| AC27 | Each section its own card; the rail at ≥ 1310 px container width, chips below (1920 rail; 1252 and 1568-with-sidebar chips); the current entry marked while scrolling; no "Your notes" block; the Review rail present; at 390 px the evidence line reads "{date} · {n} sources" only; no horizontal scroll at 1252 or 390 px | Tasks 9, 10 | `report-body.test.tsx`, `report.test.ts`; `report-layout.spec.ts`, `layout.spec.ts`, `notes.spec.ts`, captures `18-report-cards(-phone)`, `18b-report-cards-1920` (Task 11) |

## Spec Phase C items, and where each lands

| Spec | Item | Task |
|---|---|---|
| §7.1 | Reply schema (`SectionDraft.short_title`, `TopicLineDraft`, `BottomLineDraft.topics`), prompts and rules verbatim, the two reply examples, `MAX_ANSWER_SENTENCES`, the request's `# Reader answers` and headers, the topic-line refusals, the Statement Check over both, the re-ask text | 3, 4 |
| §7.2 | `bottom_line` layout, `reader_note_lines` stamped by the finalizer, `reader_answers`, `ReportSection.short_title`, `summary` order and `_renumber`, labels, note short (A's), the note-line texts and marks, outside the fingerprint | 4, 7 (A: the short) |
| §7.3 | The fallback in the same shape, its two messages, the move that can empty a section | 4 |
| §7.4 | `key_figures_table`, label (with D40's source label), merge, cap, columns, caption; placement after the topics; Options compared | 5, 6 |
| §7.5 | The heading order, the evidence line's answers, the assembled line, the list, citation order, `report_outline` on the response | 5, 8 |
| §7.6 | Structure, chunks, rendering with `sourceIds`, cards, card box, contents (rail/chips, D28), current section, the phone's evidence line and Key figures, removal of "Your notes", types, CSS | 9, 10, 11 |
| §7.7 | The section and bottom-line replay doubles | 3, 4 |
| §7.8 | `DESIGN.md` row 4 and the note paragraph, §5.6, the consumer-format superseding note | 12 |
| §11.1 C | pytest, Vitest and Playwright tests named there | 3–11 |
| §11.2 C | `report.spec.ts:9-11`, `layout.spec.ts:128-163`; the table, report, layout and writer tests and the replay doubles; `test_report_writer.py:21`, `:1051`; `test_tool_free_prompts.py:530-537`; `test_reader_notes_replay.py:37-40`; `report-body.test.tsx`, `reader-notes.test.tsx`, `notes.spec.ts`, `12-report-notes` | 3–6, 10, 11 |
| §11.3 | `18-report-cards(-phone)`, `18b-report-cards-1920`; `04-report`, `12-report-notes` re-taken | 11 |
| §12 R5 | The bottom line's larger shape makes "fully checked" rarer; the re-ask rule is kept as is | 3, 4 (unchanged rule) |

## Self-review

1. **Spec coverage.** Every §7 subsection, every Phase C AC (AC22–AC27, AC36, AC34's bottom-line part) and every Phase C row of §11.1–§11.3 maps to a task above. §7.2's note short is A's (spec ambiguity 2), checked in Task 1.
2. **Placeholders.** None: every code step carries its code; the two values a step cannot know in advance (re-pinned digests and fingerprints) are computed by the helper, with the keys that move named and the planning values given; Task 11's run outcomes are marked [not run in planning] with their reasoning.
3. **Type consistency.** The names Tasks 2–10 produce are the ones later tasks consume: `note_outcome`/`note_steering_outcome` (2 → 7), `TopicLineDraft`, `BottomLineDraft.topics`, `ReportSection.short_title`, `reader_answer_lines`, `MAX_ANSWER_SENTENCES` (3 → 4, 5), `BottomLineLayout`, `BottomLineTopic`, `note_label`, `ReportComposition.bottom_line`/`.reader_answers` (4 → 5, 7), `ReportOutlineEntry`, `report_outline` (5 → 8, 9), `key_figures_table` (6), `ReportNoteLine`, `reader_note_lines`, `report_note_lines` (7), `ReportOutlineEntryResponse`, `report_outline` on `/status` (8 → 9), `splitReport`, `reportCards`, `parseEvidenceLine`, `sourceIdsOf`, `currentCard`, `revealChip`, `contentsModeFor` (9 → 10), and Task 10's DOM ids and classes (→ 11).
4. **Anchors.** Every block was applied in order to the tree Phases D and A leave (see Evidence), and Task 1 Step 3 re-checks them all before the first edit.


## Review 1: how each finding was resolved

Fable's review 1 (`.superpowers/reviews/2026-09-30-plan-c-review-1.md`, of `bd48725c`): approved with changes. Every changed step was re-run on a D+A tree rebuilt from the revised D and A plans; Task 1 Step 3's anchor check prints `problems: 0` there.

| Finding | Resolution |
|---|---|
| I1 — the replay digests' request counts were hard-coded | Every re-pin Expected line now names each count as the baseline's own, unchanged by the task (46/29 on the D+A tree; different if the latency branch's batch size landed first): Task 3 Step 5, Task 4 Step 5, Task 5 Step 5, Task 6 Step 6. Task 1 Step 4 prints `PINNED_RUN_DIGESTS` and records the two counts as B-count-extra and B-count-redraft; Global Constraints says no task changes them. |
| I2 — a measure-only label collapsed figures from different findings (the human's ruling, D40) | D40 is built in Task 6: a row with no named item is labelled `{Source} · {Measure}` by the source that reported it (`_label_source`, on `_who_name`, the Source column's name without its date); merging and one row per label are unchanged; spec ambiguity 10 records the reading of "no named item" and of "the source". New tests: `test_a_row_with_no_named_item_is_labelled_by_its_source` and `test_the_replay_default_case_labels_its_figures_by_their_sources`, which pins the replay default case's five rows and caption; `test_key_figure_labels_name_the_item_never_a_snippet`'s two measure-only expectations now read "Example Org · …". The spec's §2 gains D40 (and D36 its amendment note) and §7.4 item 2 its one-line amendment, in the same revision. Task 6's re-pinned digests move accordingly (planning `d92891c23a2cfe2e`, `9ac8c34e25224206`, reviewer packet `ba466f328de6eb67`); the suite grows by 2 (B-py + 52). |
| M1 — the Evidence bullet misdescribed the D+A fingerprints | Corrected: the five values are listed, with A's four re-pins against `73b4d7a6`'s values and the verifier's the only one unchanged. |
| M2 — the deleted `test_period_resolved_from_*` tests were not explained | Task 6 Step 2 and spec ambiguity 11 now say what they pinned (P3-3's "(counted from the release date, …)" and the "({scope})" suffix), that the period basis and scope leave the reader table, and that the evidence log keeps `period_resolved_from` (`agents/report.py:845-846`). |
| M3 — `noteCaption` has no caller after Task 10 | Deleted in Task 10 with its `web/test/notes.test.ts` case and the `ReaderNoteRecord` import it alone used (spec ambiguity 12); B-web + 17. |
| M4 — the move helper trusted its span | `move_outcomes.py` now stops unless the span holds exactly `note_outcome`, `note_steering_outcome`, `_research_outcome` and `_verdict_outcome`, and stops if `class NoteRecord` lies inside it (Task 2 Step 3). |
| M5 — `web/README.md`'s `capture:visual` line went stale | Task 12 rewrites it: twenty-seven captures (thirteen stages and views at each of 1252 and 390 px, and the report's cards at 1920 px). |
| M6 (optional) — the fixture's provenance | Kept as is: the fixture's `source` key already names the quality record, whose name is the session id, and its `question` key the question (Open issue O-1); the generator stays in the git-ignored helper folder because it reads a git-ignored file only this checkout has. |
