# Notes that land, live progress in every step, a report that answers first, and Stop

**Status** designs picked by the human on a Claude Design canvas on 2026-09-30 (the picked options are listed in §2); spec written 2026-09-30 by the spec-plan-author agent (draft committed as `1f186af0`); revised the same day for Fable's reviews 1 and 2 (`.superpowers/reviews/2026-09-30-spec-review-1.md`, `-2.md`; every finding resolved in §14) and the human's rulings on every open issue (D20–D38); no decision is open, and the one implementation deviation, D19 (an unmeasured count is left out), was decided under the owner's delegation on 2026-10-01: kept (§13). Four phases, A–D, each independently plannable; each gets its own implementation plan, authored by `spec-plan-author` (Opus 5.5, max effort) and reviewed by `spec-plan-reviewer` (Fable 5.1, max effort) until clean · **Date** 2026-09-30 · **Branch** `feat/notes-progress-report-stop`, cut from `origin/main` `f4282818` (the merge of PR #28, "live step briefs, the one-time check, and reader notes (Phases 1–3)") · **Canvas** https://claude.ai/artifact/FmPfLZmGCk5wuAamVu8suT (private to the human; source files in `.superpowers/progress-canvas/project/`, which git ignores).

**Sources of truth.** The human's decisions in §2 are closed, and nothing below reopens them; where one cannot be built exactly as worded, §13 says so rather than changing it. D1–D19 are the decisions handed to this spec; D20–D38 are the human's rulings of 2026-09-30 on the draft's open issues, relayed by the coordinator, and where a ruling amends an earlier decision the earlier row says so. The approved latency workstream on branch `perf/latency` (`docs/superpowers/audits/2026-09-30-latency-audit.md`) runs beside this spec and changes the researcher's tool lock and the verifier's batching; §3.13 lists what this spec relies on from it. The picked designs are the canvas artboards named in §2, styled by `progress.css` over `theme.css` (a copy of `docs/design/running-stage-picks/theme.css`); only the picked options matter. The design the app implements is `docs/design/DESIGN.md` and `docs/design/api-gaps.md`. The previous spec is `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md`, cited as "LB spec", with its decisions as LB-D1 … LB-D17. `web/app/globals.css` lines 1–1131 are the prototype's CSS verbatim; new rules go under the app-only header at `web/app/globals.css:1133` with no colour literal (`web/scripts/check-css-verbatim.mjs`).

Every `path:line` below was read at `f4282818` on 2026-09-30, except the latency audit, read at `554ba7a7`, the branch commit that adds it; the branch changes no code or test after `f4282818`. Engine paths are under `src/deep_research/` and written `agents/…`, `graph/…`, `api/…`, `runtime/…`, `utils/…`; web paths are under `web/`.

**Legend.**
- **[INFERENCE]** marks something not observed in code or in a command run for this spec. Each one names the test that proves or falsifies it.
- **(I)** marks illustrative copy written for this spec. Use it as written unless the plan finds a shorter true sentence.
- **Spec choice** marks a detail the human's decisions leave open, decided here with its reason.
- A **research note** is an interpreted reader note, not replaced by a later note, whose `kinds` include `new_angle`. A **steering note** is any other such note (emphasis, exclude, scope, about_reader). A **mixed note** is a research note whose kinds also include a steering kind: both halves apply (D20). Its new_angle half gets its own topic like any research note, and its steering half steers, is judged and is shown like a steering note (§5.1).

---

## 1. Problem and scope

### 1.1 What the first live run of PR #28 showed

Question "Where can we get the best tasting Lattes in san Jose", 2026-09-30. Published artifacts: `output/report-a02a75fd75d44d8481f34953a4ff52e1-0.md`, `-evidence.md`, `-quality.json`. The session's own event log is not in `output/`; rows marked *reported* come from the dispatch.

| # | Observation | Evidence |
|---|---|---|
| E1 | The one-time check asked three questions (Area, Purpose, Scope), answered "San Jose plus nearby South Bay cities", "A place to go right now", "Top 3-5 standout spots". | *reported* |
| E2 | Planning ran 20:02:59–20:10:25. The planner runs at `reasoning_effort: max`. | times *reported*; effort `config.yaml:32-34` |
| E3 | Note n1 "dont include the ones that might closed right now" (exclude) arrived at 20:06:50 and n2 "I also want pastries in the cafe" (new_angle) at 20:08:01, both during planning. | *reported* |
| E4 | The plan had four topics and no pastries topic. The confirming plan review flagged it, and the plan stood. | the quality record's `parts` are `topic-01`…`topic-04`; it holds two `planner_plan_defects_unresolved` records for plan `review_repair` (the plan-checks and confirming-review messages); the defect wording ("missing dimension: Pastries … the reader's new_angle note…") is *reported* |
| E5 | The planner reads notes only when it builds a request, so a note that arrives during the long draft call misses the draft. | `reader_notes_block()` renders the board when called (`agents/planner.py:3183-3192`), from `_request_plan` (`:3316`), `_review_plan` (`:3403`) and the scoping turn (`:3194-3207`) |
| E6 | Running research loops never see a new_angle note; it waits for the review's `no_evidence` and a `note_pass`. | `research_reader_notes` drops new_angle notes (`agents/reader_notes.py:113-115`); `notes_due_a_pass` requires `no_evidence` (`graph/state.py:311-318`) |
| E7 | The provider ran out of credit: Statement Check batches failed, the bottom line failed twice, and the terminal review was `provider_failed`. | quality record: 8 × `evidence_verifier_statement_check_failed`, 2 × `report_writer_bottom_line_failed`, `review.status == "provider_failed"`, 1 × `graph_report_review_unavailable`; timings *reported* |
| E8 | The route was `review_unavailable` → finalize. No note pass ran and both notes ended `pending` in a finished session. | `session_status: incomplete`; `_reviewed_notes_update` marks every active note `reviewed` whatever the review's status (`graph/nodes.py:1089-1108`), so no note is due a redraft, and `note_outcome` returns `pending` when no verdict exists (`api/notes.py:343-376`) |
| E9 | The fallback bottom line is one sentence about three cafés' roasting. It answers nothing. | the report's `## Bottom line` holds that one sentence; `_bottom_line_fallback` takes the first kept, checked point of each part with a required target, then of the others, at most four (`agents/report_writer.py:2263-2350`), and most points were unchecked (E7) |
| E10 | The figures table sits inside the bottom line, labels rows with raw Tripadvisor snippets, and prints two rows under one label. | the table follows the bottom-line paragraph with no heading (`agents/report.py:1623-1628`). Fact rows K003/K004 both have subject "Bijan Bakery", with values "4.2 of 5 bubbles" and "87 reviews". `_has_rival` compares kind, period, subject and value but never the unit (`agents/report_table.py:734-751`), so each row is the other's rival, and a rival is labelled with its quoted evidence words (`:769-785`, `:728-731`). Their `measure` is another target's ("cafés named in published best-latte or best-coffee guides"), picked as the first answered target that carries a measure (`agents/verified_facts.py:1378`). |
| E11 | The running stage looked static: only Planning's start, recall and completion, the researcher's topics and tool calls, and each other agent's completion are live. | live sites: `graph/nodes.py:243`, `agents/planner.py:3261`, `:3269`, `agents/researcher.py:4445`, `:4504`, `:4628`, `agents/source_evaluator.py:1368`, `:1390`, `agents/evidence_verifier.py:1008`, `agents/report_writer.py:3371` |
| E12 | One session cannot be stopped: only store shutdown cancels tasks. | `SessionStore.close` (`api/sessions.py:474-501`); no route cancels (`api/app.py:242-480`); api-gaps lists a cancel route as a gap not to close (`docs/design/api-gaps.md:164-166`) |

### 1.2 Phases

| Phase | Delivers |
|---|---|
| **A. Reader notes that land** | A new_angle note becomes its own required sub-topic, built by code. At planning time it is appended to the plan; during research it starts its own loop at once; after research it gets exactly one note pass, after which the writer drafts only the new part and the bottom line. Its coverage comes from its own targets. A mixed note also steers, is judged and is shown as a steering note (D20). No note ends `pending` in a finished session. |
| **B. Live progress per step** | Real progress events from each unit of work, and the picked brief for Planning, Evaluating sources, Verifying evidence, Writing report and Reviewing. |
| **C. The report** | A bottom line that answers first, then one cited line per topic and one line per note; a fallback with the same shape; a "Key figures" section with labelled, merged rows; the report as cards with a contents list, in the web app and in the Markdown file's heading order. |
| **D. Stop** | `POST /research/{id}/stop`, a terminal `stopped` status, and the Stop control, confirm popover and stopped state. |

### 1.3 In and out of scope

| In scope | Out of scope |
|---|---|
| `agents/`: planner, researcher, source evaluator, evidence verifier, report writer, report table and renderer, reviewer packet and progress events | Surfacing the DeepSeek out-of-credit error (a separate task already queued) |
| `graph/`: note routing, note pass, live publication of the reviewer's start and result | The planner's `max` reasoning effort (`config.yaml:32-34`) |
| `api/`: stop route, `stopped` status, note outcomes, report outline, replay pacing aids; `tools/web_search.py`: the async Tavily client, so a stop cancels a search in flight (§8.3) | The idle page; the page 1→2 and 2→3 journeys (kept exactly as PR #27 built them) |
| `web/`: step briefs, notes copy, report layout, Stop control and stopped stage, chip, sidebar | A resume endpoint; re-running a stopped session in place ("Ask again" starts a new session) |
| `docs/design/DESIGN.md`, `api-gaps.md`, README amendments listed per phase | The Evidence view; the Failed stage; the service-stopped stage (`web/components/SessionScreen.tsx:208-233`) |
| Tests at every layer; visual captures at 1252 and 390 px | A live, paid run (governed by the existing spend rules); persistence across restarts (sessions stay in memory, `docs/design/api-gaps.md:154`) |

---

## 2. Decisions (human-approved 2026-09-30; closed)

D1–D19 were handed to this spec; D20–D38 are the human's rulings on the draft's open issues (O1–O17 of `1f186af0`), on the review's O18, and the latency approval. An *italic* note on an earlier row names the ruling that amends it.

| # | Phase | Decision |
|---|---|---|
| D1 | A | **(A1)** Every new_angle note received before research ends becomes its own sub-topic "Your note: {restatement}", with coverage_id `note-{note_id}`, evidence targets from the note's `new_questions`, required, built deterministically and not left to the planner. The planner still sees all notes (emphasis, exclude and scope keep shaping the plan). |
| D2 | A | **(A2)** Planning-time new_angle notes are held and appended when the plan is published. `planner.planning.completed` metadata includes them. The web shows them per Phase B's Planning design as "joins the plan", then "from your note". They are researched in the main round, with no extra pass. |
| D3 | A | **(A3)** Research-time new_angle notes start their own research thread immediately inside the running researcher node, and the researcher does not finish until those threads finish. This replaces the Phase 3 rule that new_angle notes wait for the review's note pass. The spec decides the interaction with the semaphore, the sub-topic cap, the request budget, the events (`researcher.sub_topic.started` for the note thread), and a note that arrives after the researcher's last thread finished but before the node returns. |
| D4 | A | **(A4)** Notes arriving after research finished (Evaluating, Verifying, Writing, Reviewing) keep exactly one note pass (the existing `note_pass` node). The writer then writes only the new note part and the bottom line and carries every other part over unchanged through the existing carry-over (`PartJob`, LB spec §6.9). Exception: exclude and scope notes still redraft the parts they affect (the note redraft route). *Ruled by D22: the note redraft keeps drafting every part.* |
| D5 | A | **(A5)** A new_angle note's coverage is decided by its own sub-topic's targets (answered or not found), not by the reviewer. The reviewer still gives dispositions for emphasis, exclude, scope and about_reader notes. If the review is unavailable (`provider_failed`), any owed note pass still runs. No note ends `pending` in a terminal session: `not checked` when nothing could judge it. Kept: at most 10 notes (LB-D11a), one pass and one redraft per note, verification never sees notes (LB-D10), and the recursion-limit arithmetic stays exact. |
| D6 | B | Progress is **real**: backend events published as each unit of work finishes. |
| D7 | B | **Planning = `Main.dc.html` option B**: topic slots wait as quiet skeleton bars with a sheen, fill with titles when the plan draft returns, then tick as the plan check passes; the topic under repair shows "being fixed"; a status line cross-fades Reading → Drafting → Checking → Fixing → Plan ready; the row subtitle is the elapsed time; a planning-time note shows an ack line and its own slot "joins the plan" → "from your note". |
| D8 | B | **Evaluating sources = `Evaluating.dc.html` option A**: "Rating 44 sources for trustworthiness and relevance", a determinate bar, stats Rated "n of N" / Strong / Fair / Weak ("not yet" before the first batch). |
| D9 | B | **Verifying evidence = `Verifying.dc.html` option C**: a "Just checked" ticker showing one real finding at a time with its verdict (verified / corrected, with the corrected value / quoted as written / dropped, with the reason in plain words) and its source kind; a determinate bar; a tally line. |
| D10 | B | **Writing report = `Writing.dc.html` option E**: a "Just written" ticker of real drafted sentences with ✓ "backed by k findings" or ✗ "removed — no verified finding says this", the section title, a determinate bar of sentences checked, "section k of 5". |
| D11 | B | **Reviewing = `Reviewing.dc.html` (A revised; both A1 all-met and A2 one-not-met)**: seven criteria in plain words from `REVIEW_DIMENSIONS`, no scores; ✓ when the reviewer raised no material issue under a criterion, amber ✗ with the issue in plain words when it did; a "Your notes" list with ✓/✗ per note; an indeterminate bar, the elapsed time and "usually 1–3 min" while the single call runs; the outcome "Accepted · all 7 met" or "1 thing to fix · back to the writer". *Amended by D23: five criteria, "Accepted · all 5 met".* |
| D12 | B | Researching keeps its current checklist (LB spec §4.3). |
| D13 | C | **(C1)** The bottom line is a direct answer first (1–2 sentences, shaped by the reader's one-time-check answers), then one cited line per planned topic in plan order (labelled with a short topic name), then one line per note with ✓/✗ and how it was handled. Every existing honesty rule of `BOTTOM_LINE_INSTRUCTION` stays. |
| D14 | C | **(C2)** When the bottom-line call fails, or every sentence is refused, the fallback builds the same shape from the sections — one kept, checked sentence per topic plus each note's status, labelled as assembled from the sections — replacing the "first point of required parts" rule and keeping its floor and dispute protections. |
| D15 | C | **(C3)** The figures table moves out of the bottom line into its own "Key figures" section after the topics. Each row is labelled item · measure (never a raw snippet); rows about the same item merge ("4.7 of 5 · 20 reviews"); about 10 rows at most; the full list stays in the evidence log. |
| D16 | C | **(C4)** Layout = `Report.dc.html` B+C: each section its own card on the page ground (bottom line card first, then topic cards with eyebrow "Topic n of N" / "· from your note", Key figures, What we couldn't confirm); a contents list to the left that jumps to headings with the current one marked; on a phone the list becomes a horizontally scrolling chip row under the header. The Review / Coverage / Evidence rail stays. The Markdown report file gets the same structure (heading order). |
| D17 | D | `POST /research/{id}/stop` cancels the session's task at once (all in-flight provider and HTTP calls), writes no report, and ends the session in a new terminal status `stopped`, with the stream event `session.stopped` naming the step it was on. Report and evidence endpoints answer like a failed run (409 `report_unavailable`). *Amended by D26: `/evidence` answers 409 `evidence_unavailable`.* `409` when already terminal or once `finalize_report` has started (the notes cutoff); `404` for an unknown session. Offered from the one-time check (`needs_input`) through Reviewing. |
| D18 | D | Web = `Stop.dc.html`: a Stop button (ghost, small square icon) beside the running status chip; one confirm popover "Stop this research?" (I) with "Keep going" (quiet) and "Stop research" (red text); the stopped state: chip "Stopped by you · at <step>" with a neutral grey dot, a short card "You stopped this research at HH:MM, N minutes in. No report was written…" (I) with an "Ask again" button (the same question, fresh), finished rows still openable, the stopped row with its partial facts, later rows "not run"; the note line hidden; the sidebar shows the session as stopped, not failed; works in replay mode. *The popover's body copy is D24; the sidebar treatment is D29.* |
| D19 | all | DESIGN.md theme rules hold: tokens only; colour is status (green active/ok, amber warn, red danger, purple only on the one primary button); one surface per region; motion tokens; reduced motion turns movement into fades; unknown values read "not yet", never 0, —, or null. |
| D20 | A, B, C | **(O18, review C1) Mixed-kind notes: both halves apply.** The new_angle half gets its own topic. The steering half (exclude, scope, emphasis, about_reader) steers the running loops, the evaluator, the writer and the review packet, printed with new_angle removed from its kinds; it keeps its reviewer disposition; it can trigger a note redraft on `ignored_with_evidence` or when no review input carried it; Reviewing and the bottom-line note line show both results. With an AC and a test. |
| D21 | B | **(O1)** Both new animation loops are allowed: the Planning skeleton sheen and the Reviewing indeterminate drift. DESIGN §3.4 and §5.6 are amended. Each runs only while its step is active and is static under reduced motion. |
| D22 | A | **(O2)** Exclude and scope notes keep redrafting every part (today's note redraft); new_angle notes still draft only their own part and the bottom line. |
| D23 | B | **(O7)** Reviewing shows only the criteria that can fail: prioritization ("Puts the most important first") and actionability are dropped, because the kind → criterion map can never mark them ✗. Five criteria; the design text, ACs and copy say five. Amends D11. |
| D24 | D | **(O9)** The Stop popover keeps the canvas copy: "It stops right away and nothing more is spent. What's done so far stays here, but no report is written." |
| D25 | D | **(O15)** Search switches to `AsyncTavilyClient`, including the retry side effect (§12 R9). |
| D26 | D | **(O13, default accepted)** A stopped session answers `/report` with 409 `report_unavailable` and `/evidence` with 409 `evidence_unavailable`, exactly as a graph-halted run does. Amends D17's parenthetical. |
| D27 | C | **(O3, default accepted)** The options table (comparison questions) also leaves the bottom line, as "Options compared" (I) in the same slot after the topics. |
| D28 | C | **(O4 and review M15, default accepted)** The contents rail shows only when the report stage is at least 1310 px wide; below that, the chip row. At the 1252 px capture width, and at 1568 px with the sidebar expanded, the report shows chips (§7.6 gives the viewport arithmetic). |
| D29 | D | **(O5, default accepted)** A stopped session's sidebar row says "stopped by you" in its accessible name only; no visible status word (DESIGN §3.1). |
| D30 | B | **(O6, default accepted)** A dropped verdict in Verifying's ticker is amber; kept verdicts are green. |
| D31 | A | **(O8, default accepted)** A research note is "covered" when at least one of its targets is answered by a verified finding. |
| D32 | C | **(O10, default accepted)** On a phone, Key figures shows the source under each row's label. |
| D33 | D | **(O11, default accepted)** A stop during the one-time check shows no pipeline card. |
| D34 | B | **(O12, default accepted)** Writing's tally counts sentences drafted so far, so its total grows as sections return. |
| D35 | B | **(O14, default accepted)** The attribution criterion reads "Every claim is credited correctly", not the canvas's "Every claim is cited". (The canvas's actionability wording is moot under D23.) |
| D36 | C | **(O16, default accepted)** Key figures merges values from one passage (one primary finding) and shows one row per label. *Amended by D40: a row with no named item is labelled by its source.* |
| D37 | C | **(O17, default accepted)** A bottom-line note line whose outcome is not checked carries no mark. |
| D38 | all | **(Latency workstream, approved by the human)** Branch `perf/latency` narrows the researcher's run-wide tool lock to per-URL single-flight with one shared client and a per-run robots cache (amending EV-D9, `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md:47`), raises `agents.verifier_concurrency` to 64, and may drop `agents.verifier_batch_size` to 2. This spec depends on none of these changes and works with each (§3.13, review M13). |
| D39 | B | **(The human's ruling of 2026-09-30 on Phase B's plan review 1, O2)** When the review sends the run back (a redraft, an extra pass, a note pass or a note redraft), the Reviewing row holds open about 2 s on its ✓/✗ list and its verdict line (the canvas's A2 artboard), then hands over to the row the run returns to, and stays reopenable afterwards. The hold paints and never marks, and a Stop ends it. *Amends §6.7's last bullet.* |
| D40 | C | **(The human's ruling of 2026-09-30 on Phase C's plan review 1, I2)** When a fact row has no named item (no subject, or one that starts with a pronoun: §7.4 item 2's "no item"), the Key figures label falls back to the source that reported it, e.g. "Tripadvisor · Rating, 2024", so figures from different findings keep separate rows; merging still only happens for the same item from the same passage; one row per label and the 10-row cap stay. *Amends D36 and §7.4 items 2–3.* |

**Where the draft's open issues went.** The draft's O1–O17 and the review's O18 (`1f186af0`) are all decided; the review's alternative for O7 (derive ✗ for prioritization and actionability from a low dimension score) is superseded by D23, which drops the two criteria.

| Draft issue | Ruling | Where the spec applies it |
|---|---|---|
| O1 two new loops | D21 | §6.3, §6.9, §6.11, AC20 |
| O2 "the parts they affect" | D22 | §5.4, AC7 |
| O3 options table | D27 | §7.4 |
| O4 contents rail width (and review M15) | D28 | §7.6, AC27 |
| O5 sidebar | D29 | §8.4 |
| O6 dropped verdict colour | D30 | §6.5 |
| O7 criteria that cannot fail | D23 | §6.1, §6.2, §6.7, AC18, R6 |
| O8 "covered" | D31 | §5.6 |
| O9 popover copy | D24 | §8.5, AC33 |
| O10 phone Key figures | D32 | §7.6 |
| O11 stop during the check | D33 | §8.5 |
| O12 Writing tally | D34 | §6.6 |
| O13 `/evidence` code | D26 | §8.4, AC31 |
| O14 criterion wording | D35 | §6.7 |
| O15 async search | D25 | §8.3, R9 |
| O16 Key figures merge | D36 | §7.4, AC25 |
| O17 unmarked note line | D37 | §7.2 |
| O18 mixed-kind notes (review C1) | D20 | §5.1, §5.4–§5.8, §6.7, §7.2, AC34 |

---

## 3. Ground truth

### 3.1 Events and live publication

| Fact | Where |
|---|---|
| `ResearchEvent` has `event_type`, `source`, `message`, `timestamp` (stamped at construction), `metadata` (finite JSON) and `event_id` (a fresh uuid hex per event). | `utils/types.py:1455-1465` |
| `publish_live` hands an event to the run's sink and is a no-op when none is bound; agents call the wrapper in `agents/events.py`. | `graph/live.py:32-46`, `agents/events.py:44-56` |
| The orchestrator binds the sink only when an event handler is supplied, publishes live events at once, and publishes snapshot events whose id it has not already published. | `graph/orchestrator.py:316-379` (sink `:350-352`, dedupe `:366-371`) |
| `graph.node.completed.event_count` counts the events in the agent's state update. | `graph/nodes.py:276-288` |
| Event metadata must never carry provider text or exception text; `graph.report.reviewed` carries "never the review's prose and never a defect's text". | `agents/events.py:27-31`, `graph/events.py:34-37`, `:262-266` |
| `agent_node` publishes its `graph.node.started` live; the reviewer node merges its started event without publishing it live. | `graph/nodes.py:236-243`, `:841-851` |
| `ResearchSession.publish` stores a deep copy in `session.events` and updates `current_agent`, `iteration`, `notes_closed` and `note_passes`. | `api/sessions.py:132-153` |
| The CLI streams through the same handler; plain and verbose output show only `PROGRESS_EVENT_TYPES`, `.completed` events and three verbose types; `--debug` shows every type. | `cli.py:369-375`, `:381-392`, `:410-442` |
| Replay runs the engine unpaced and releases each event through a queue, `delay` seconds apart (default 150 ms). | `api/replay.py:101-106`, `:136-140`; `api/__main__.py:26` |
| Replay reads `X-Replay-Case` and `X-Replay-Clarify` on `POST /research` into ContextVars; the web proxy forwards only allowlisted request headers. | `api/replay.py:143-203`; `web/app/api/[...path]/route.ts:9` |

### 3.2 Reader notes: API, board, routing, outcomes

| Fact | Where |
|---|---|
| `NoteBoard` receives (numbers `n1`…`n10`), adds interpreted notes, drops, snapshots, and `settled()` waits for pending readings; it has no change notification. | `runtime/notes.py:47-131` |
| The board is bound for the session's task through a ContextVar. | `api/sessions.py:311-327`, `runtime/notes.py:133-150` |
| `add_note` refuses unless the session is `running`, not finished, not `notes_closed` and the store not closing; `notes_closed` is set by `graph.route.decided` with destination `finalize` or `end`. | `api/sessions.py:375-407`, `:146-149` |
| A failed reading keeps the note as an emphasis in the reader's words. | `api/sessions.py:409-443`, `api/notes.py:129-131` |
| The interpretation schema is `kinds`, `restatement`, `scope`, `new_questions`, `replaces`; restatements ≤ 120 chars; ≤ 3 new questions of ≤ 200 chars. | `api/notes.py:47-120`, `:134-174`, `:183-197` |
| Replay's interpreter restates the note as written, as an emphasis; a replay run never applies a note (api-gaps 3.9). | `api/notes.py:267-275`; `docs/design/api-gaps.md:127` |
| `ReaderNote` keeps `reviewed`, `passed`, `redrafted` flags; `MAX_NOTES_PER_RUN = 10`; prefixes `note-` and `Your note: `. | `utils/types.py:1145-1150`, `:1173-1214` |
| The board refuses an eleventh note (`NoteLimitReached`), and a note id matches `^n([1-9]\|10)$`, so a run holds at most ten notes of every kind together. | `runtime/notes.py:80-85`; `utils/types.py:1184` |
| Outcomes are `covered`, `not_found`, `not_addressed`, `pending`, `replaced`; `pending` when no state or no verdict. | `api/notes.py:53-55`, `:343-376`; `api/models.py:154-167`; `web/lib/api.ts:44`; `web/lib/notes.ts:70-76` |
| Requests that carry notes: planning (`PLANNING_NOTES`), research loops without new_angle notes, extraction, source evaluation (relevance only), writing, review (with ids); verification carries none (LB-D10). | `agents/reader_notes.py:29-69`, `:113-115`; `agents/researcher.py:3190-3205`, `:4418-4437`; `agents/source_evaluator.py:1105-1109`; `agents/report_writer.py:3246-3248`; `agents/report_reviewer.py:715-722` |
| `PLANNING_NOTES` tells the planner "a new_angle note can become a sub-topic of its own". | `agents/reader_notes.py:29-36` |
| Every notes block prints one line per note, `- {restatement} ({kinds})`; `WRITING_NOTES` gives rules for emphasis, exclude, scope and about_reader and none for new_angle. | `agents/reader_notes.py:118-135`, `:54-61` |
| The source evaluator judges a source with the sub-topics it was cited for: its passages are chosen per obligation from each such sub-topic's title, searches, criteria and target questions. | `agents/prompts.py:105-114`; `agents/source_evaluator.py:787-812`, `:1094-1098` |
| The review packet lists every active note; dispositions for unknown ids, repeats and unknown statuses are dropped. | `agents/report_reviewer.py:579-585`, `:625-628`, `:715-722`, `:1557-1580` |
| The review node waits at most `_REVIEW_NOTES_WAIT_S = 30.0` for notes still being read, then marks every active note of its input `reviewed`. | `graph/nodes.py:862`, `:1086`, `:1089-1108` |
| Route order: halted → `notes_due_a_pass` → `notes_due_a_redraft` → extra pass → `review_unavailable` → redraft → accepted → exhausted → not accepted. | `graph/state.py:339-406` |
| A note is due a pass on a `no_evidence` verdict when not `passed`; due a redraft when not `redrafted` and `ignored_with_evidence` or not `reviewed`. | `graph/state.py:303-336` |
| The extra-pass target list leaves out every `note-` target, so a note target still missing never buys an extra pass. | `graph/state.py:283-300` |
| `note_pass_node` appends `note_sub_topic` topics, confines the researcher through `extra_pass_target_ids`, flags `passed`, counts `note_passes`. `note_sub_topic` builds one required target per new question (or the restatement) and a rationale that says "the review found no evidence for it yet". | `graph/nodes.py:1343-1378`, `:1381-1443` |
| A note redraft goes through `writer_redraft_node` and spends no `MAX_WRITER_REDRAFTS`. | `graph/nodes.py:1281-1309` |
| The recursion limit is `(max_extra_passes + 1) × len(NODE_NAMES) + MAX_NOTES_PER_RUN × (len(NODE_NAMES) + NOTE_REDRAFT_STEPS) + 10`. | `graph/state.py:64-86`, `:432-448` |
| After a note pass or a note redraft the writer drafts every part afresh: `_is_redraft_hop` returns False after `graph.note_pass.started` or `graph.note_redraft.requested`, so `defects` and `previous` are empty. | `agents/report_writer.py:1013-1035`, `:3242-3243` |
| The report's "What we couldn't confirm" names note targets by their note. | `agents/report.py:1486-1568` |
| The report reviewer after a note loop runs a full review: note markers are fresh-draft markers. | `graph/nodes.py:365-403` |

### 3.3 Planner

| Fact | Where |
|---|---|
| 1–10 sub-topics; `ResearchPlan.sub_topics` is capped at 10 by validation. | `agents/planner.py:89-90`, `:847-866` |
| Plan-side flow: draft; one repair of local problems; one review; at most one repair of the review's findings and one confirming review (`MAX_PLAN_REVIEW_CALLS = 2`). Any review or repair failure keeps the plan that stands. | `agents/planner.py:100`, `:3626-3842` |
| Plan labels `draft`, `repair`, `review_repair`. | `agents/planner.py:105-107` |
| `PlanReviewDraft` is `sound`, `missing_dimensions`, `atomicity_defects`, `unsupported_premises`, `repair_instruction` (provider text). | `agents/planner.py:804-818`, `:2607-2631` |
| Coverage ids are positional `topic-NN`; target ids `{coverage_id}-target-NN`; every local problem starts with a coverage or target id; the review request prints ids. | `agents/planner.py:1467-1475`, `:1565-1572`, `:1924-2075`, `:2815-2852` |
| `planning.started` and `memory.recalled` are published live before the loop; `planning.completed` carries `sub_topics: [{coverage_id, title ≤ 160}]` and is published live. | `agents/planner.py:2891-2949`, `:3255-3269` |
| `state_update` appends the plan's new topics and stamps their target ids into `initial_target_ids`; a run that produced no plan (`result is None`) returns only its errors. | `agents/planner.py:3844-3882`, `:3868-3870` |
| `render_reader_answers` prints the check's answers; `report_writer` already imports from `agents.planner`. | `agents/planner.py:2685-2704`; `agents/report_writer.py:40` |

### 3.4 Researcher

| Fact | Where |
|---|---|
| Eligible topics are sorted by priority; on an extra or note pass only topics owning an `extra_pass_target_ids` target run. The cap is `eligible[:max_sub_topics]`. | `agents/researcher.py:314-349`, `:4692-4695` |
| Topics run under `asyncio.Semaphore(sub_topic_concurrency)` via `asyncio.gather(..., return_exceptions=True)`; a loop's provider failure sets `stop`, and topics not yet started are recorded `provider_failure_stopped_processing`; attempt-limit refusals are re-raised after every loop settles. | `agents/researcher.py:4746-4808`, `:4841-4849` |
| Each loop's `decide` closure reads the board before every model turn. | `agents/researcher.py:4418-4437` |
| Planned targets are read from the state the node started with (`_run_source_state`), confined to `extra_pass_target_ids` on a pass. A loop reads the extraction target list once, when it starts; `bound_sub_topic_findings` reads `_planned_targets()` again for its required ids when the loop ends. | `agents/researcher.py:3207-3238`, `:4378-4396`, `:4577-4585` |
| Every loop that returns, a failed one included, emits `researcher.sub_topic.completed` with its `coverage_id` and `stop_reason`; a loop succeeded unless `stop_reason == "provider_error"`. | `agents/researcher.py:2693-2719`, `:4610`; `agents/steps.py:193`, `:211-213` |
| One run-wide tool lock serialises every loop's tool section (EV-D9); the approved latency work narrows it (D38). | `agents/researcher.py:4741-4746`; `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md:47` |
| `researcher.sub_topic.started` metadata: `sub_topic`, `coverage_id`, `priority`, `index`, `existing_sources`; published live. | `agents/researcher.py:2590-2612`, `:4499-4504` |
| `research_completed_event` counts planned, researched, skipped, findings. | `agents/researcher.py:2722-2751`, `:4859-4866` |
| Config: `sub_topic_concurrency: 10`, `max_sub_topics: 10`, researcher tool budget 40, `max_iterations: 15`. | `config.yaml:120`, `:169`, `:158-160`, `:149` |
| One `RequestBudget` per run; each attempt reserves a unit before any I/O and a refused attempt raises `RequestAttemptLimitError`. | `request_budget.py:111-157` |

### 3.5 Source evaluator

| Fact | Where |
|---|---|
| `evaluation.started` carries `finding_count` and `source_count = len(task.groups)` and is published live before scoring. | `agents/source_evaluator.py:922-936`, `:1362-1368` |
| A source whose stored assessment still matches is reused; the cap `max_total_sources` applies only to sources needing an assessment; batches of `batch_size` (12, `DEFAULT_BATCH_SIZE`) run concurrently under `source_scoring_concurrency` (6). | `agents/source_evaluator.py:99`, `:1175-1201`, `:1257-1263`; `config.yaml:121`, `:144-145` |
| A batch either scores its sources (`scored`, or `unscored_missing` for a row the reply omitted) or marks them all `unscored_provider`. | `agents/source_evaluator.py:1207-1255` |
| `overall_score = 0.45·authority + 0.15·recency + 0.40·relevance`; `low_confidence = overall < 0.4` (`LOW_CONFIDENCE_THRESHOLD`). | `agents/source_evaluator.py:88-98`, `:240-251`, `:415-457` |
| `evaluation.completed` carries `source_count`, `average_score`, `low_confidence_count` and status counts over the whole snapshot. | `agents/source_evaluator.py:939-965`, `:548-562` |
| A scored source records `source_role` (`original_report`, `independent_research`, `derivative`, `company_statement`, `mixed`, `unknown`) and `serving_host`. | `utils/types.py:84-91`, `:634-705` |

### 3.6 Evidence verifier

| Fact | Where |
|---|---|
| It judges only findings not yet verified, or re-bound to a new target; the snapshot merges new verdicts over old. | `agents/evidence_verifier.py:990-1005`, `:1115-1134` |
| Figure Match decides `read_not_found`, `snippet_not_on_page` and `quoted` before any model call; figure-bearing findings go to batched Context Checks (`verifier_batch_size` 5, `verifier_concurrency` 16 at `f4282818`; see D38). | `agents/evidence_verifier.py:1017-1049`; `config.yaml:122-123` |
| `verify` gathers `one(batch)` under one semaphore and maps replies to findings only after every batch has returned. A batch whose reply fails is re-asked once in two halves, one after the other, inside `_check`; `check_statements` does the same inside `_check_statement_batch`. | `agents/evidence_verifier.py:1041-1053`, `:1057-1073`, `:1430-1461`, `:1517-1536` |
| A verdict is `verified`, `verified_corrected`, `quoted` or `dropped` (`read_not_found`, `snippet_not_on_page`, `all_figures_dropped`); a figure drops for `evidence_not_on_page`, `correction_not_on_page`, `context_rejected`, `context_unavailable`. | `utils/types.py:365-374`; `agents/evidence_verifier.py:770-918` |
| A correction can set a period (or clear one), a scope, a subject or a kind; `FigureResult.reason` is the Context Check's own text (provider text). | `agents/evidence_verifier.py:786-875`; `utils/types.py:392-419` |
| `check_statements` batches drafted sentences and returns `consistent`, `corrected`, `inconsistent` or `None` (unjudged) per label. Five test substitutes replace it with fixed signatures that end at `gate=None`: two in-test fakes and `_FakeChecker.__call__` in the writer tests, and two in the reviewer tests. | `agents/evidence_verifier.py:1485-1536`; `tests/test_agents/test_report_writer.py:2684-2688`, `:3275-3287`, `:1115-1147` (`__call__` at `:1131-1132`); `tests/test_agents/test_report_reviewer.py:938-945`, `:2770-2775` |

### 3.7 Report writer

| Fact | Where |
|---|---|
| `BOTTOM_LINE_SYSTEM_PROMPT` asks for "two to four sentences"; the first rule of `BOTTOM_LINE_INSTRUCTION` is "The most direct answer first, then the question's parts in order."; `MAX_BOTTOM_LINE_SENTENCES = 4`. | `agents/report_writer.py:374-435`, `:131` |
| `MAX_BOTTOM_LINE_SENTENCES` caps the kept candidates before the check (refusal reason "over the bottom line's four sentences") and the fallback's points; the missing-outcome re-ask says "within four sentences … never add a fifth". The constant is exported from `agents/__init__.py` and imported by the writer tests, and one writer test asserts the system prompt says "two to four sentences". | `agents/report_writer.py:120-122`, `:2273`, `:2314`, `:2421-2429`, `:2740-2743`; `agents/__init__.py:344`, `:829`; `tests/test_agents/test_report_writer.py:21`, `:1051` |
| `_BOTTOM_LINE_REPLY_EXAMPLES` holds two examples whose replies fill `sentences` only, printed under `# Reply format`. A reply format takes one or two examples, each a single-line label and one JSON object; the example-table test validates the planner's, extraction's, evaluator's and judge's tables against their schemas (`:530-537`), not the writer's. | `agents/report_writer.py:437-464`, `:1212`; `agents/prompts.py:76-104`; `tests/test_agents/test_tool_free_prompts.py:530-557` |
| The bottom-line request lists checked statements under `## {section.title}` blocks, plus `# Reader notes`, "Disputed" and "Outcome" blocks; it carries no reader answers. | `agents/report_writer.py:1154-1247` |
| `BottomLineDraft` is `sentences: list[WriterPointDraft]`; `SectionDraft` is `title`, `points`. | `utils/types.py:1899-1921` |
| Parts are drafted concurrently under `section_gate`; each part's Statement Check runs off its own draft through a shared `check_gate`; the bottom line runs after every part. | `agents/report_writer.py:3017-3042`, `:2171-2260` |
| Carry-over: a job with `redraft=False` and a previous section returns that section and its verdicts with no call; a part with no previous section is drafted even when not routed a redraft (P2-1). | `agents/report_writer.py:907-934`, `:2180-2193`, `:3000-3015` |
| The bottom line keeps only consistent/corrected statements, applies the authority floor when any statement meets it, guards disputed labels, re-asks once on refusals, and falls back to `_bottom_line_fallback` when the draft is `None` or nothing is kept; the fallback moves the chosen statements out of their sections, and a section left with no points is dropped. | `agents/report_writer.py:2519-2788`, `:2263-2350`, `:3044-3057` |
| Statement ids are renumbered `S001…` in render order (bottom line, then sections). | `agents/report_writer.py:2791-2818` |
| `report_written_event` carries statements, citations, refused, fact_rows, not_found, table, parts, failed_parts. | `agents/report_writer.py:3117-3137` |

### 3.8 Report rendering and the figures table

| Fact | Where |
|---|---|
| Markdown order today: title, evidence line, `## Bottom line`, the bottom-line paragraph, the table (no heading), one `##` per section, `## What we couldn't confirm`, `## Sources`, the evidence-log link. | `agents/report.py:1612-1650` |
| `_bottom_line_block` joins the summary points into one paragraph. | `agents/report.py:1226-1273` |
| `build_table`: an options table when one required part marks ≥ 2 options, else the findings table, else none. The findings table has columns What was measured / Result / Who reported it (and when) / Source, at most 12 rows, prioritised by explicit required answers, then bottom-line citation. | `agents/report_table.py:316-338`, `:51-70`, `:635-668`, `:847-926` |
| `fact_rows` groups one fact across pages (primary + `duplicate_finding_ids`); `measure` is the first answered target's measure in target-id order, else a dimension label, else "stated figure". | `agents/verified_facts.py:1334-1405` |
| `EvidenceTarget.measure` is required; `unit_dimension` is `None` for a qualitative target. | `utils/types.py:868-892` |
| The review fingerprint projects statements, finding ids, fact rows, not-found, table, parts and unreachable pages only. | `agents/report_reviewer.py:979-1034` |
| The evidence log keeps every verified figure. | `agents/report.py:1772-1832`, `:1943-1961` |

### 3.9 Reviewer

| Fact | Where |
|---|---|
| Seven dimensions: completeness, prioritization, evidence_quality, attribution, uncertainty, readability, actionability, with definitions. | `utils/types.py:1127-1137`; `agents/report_reviewer.py:244-288` |
| `ReviewDefect` carries `kind` (10 `GapKind`s), `severity`, `target_ids`, `statement_ids`, `problem`, `coverage_ids`, `resolution`; **no dimension**. `material` = critical/major and not resolved. | `utils/types.py:1031-1117` |
| `graph.report.reviewed` carries status, mean score, material defect count, reviewed statements, fingerprint, reused. | `graph/events.py:250-285`; `graph/nodes.py:873-884` |
| The review reads `state.report` verbatim as its reader content. | `agents/report_reviewer.py:692-695` |

### 3.10 Sessions, API, cancellation, replay

| Fact | Where |
|---|---|
| `SessionStatus` is `running`, `needs_input`, `completed`, `max_iterations`, `incomplete`, `failed`; `TERMINAL_STATUSES` excludes `needs_input`. | `api/models.py:35-42`; `api/sessions.py:54-56` |
| `_run` re-raises `CancelledError`; its `finally` turns `needs_input` back to `running` and sets `finished_at` and `current_agent = None`. | `api/sessions.py:503-577` |
| `iter_events` returns once the status is terminal, or the task is done with `finished_at` set. | `api/sessions.py:445-472` |
| `/report` answers 409 `session_not_complete` without an outcome and 409 `report_unavailable` with no report; `/evidence` answers 409 `evidence_unavailable` without a composition or ledger. | `api/app.py:396-451` |
| Provider calls use `AsyncOpenAI`; page reads use `httpx.AsyncClient`; Tavily search runs in `asyncio.to_thread`. | `providers/deepseek_provider.py:96-125`; `tools/web_scraper.py:176`, `tools/document_reader.py:233`; `tools/web_search.py:162-166` |
| The search tool's client protocol is synchronous, and its default client is tavily's `TavilyClient`, which posts with `requests`; cancelling the awaiting task cannot stop a request already running in the worker thread. Each attempt reserves its budget unit before the call. | `tools/web_search.py:37-46`, `:97`, `:134-140`, `:160-166`; installed `tavily/tavily.py:1`, `:225-228` |
| The installed `tavily-python` 0.7.27 (`pyproject.toml:23` requires `>=0.7`) also ships `AsyncTavilyClient`, which posts with `httpx.AsyncClient`. Both clients map 400, 401, 403, 429, 432 and 433 to tavily's own exceptions; any other failing status raises the transport's error, `requests.HTTPError` for the sync client and `httpx.HTTPStatusError` for the async one. The tool retries timeouts and `httpx.HTTPStatusError` 429/5xx only. | installed `tavily/async_tavily.py:47-122`, `:162-171`, `:251-259`; `tavily/tavily.py:134-143`, `:225-232`; `tools/web_search.py:163-176`, `:194-202` |
| Eight synchronous search clients are injected: the replay double, the evaluation harness, and six test fakes. One test asserts that controlled mode never constructs `TavilyClient`. | `e2e_evaluation/replay.py:1656`; `evaluation/dependencies.py:574-583`; `tests/research_fakes.py:31-41`; `tests/test_agents/test_researcher.py:6038-6050`; `tests/test_evaluation/conftest.py:1760-1761`, `:2023-2030`; `tests/test_tools/test_web_search.py:21-26`, `:447-457`; `tests/test_evaluation/test_dependencies_controlled.py:472-520` |
| Long-term memory calls `asyncio.to_thread` for Chroma and embeddings, which are local by default (`local` builds `LocalEmbeddingProvider`, and the model name applies to the OpenAI adapter only). | `memory/long_term.py:239-314`; `config.yaml:8-9`; `providers/factory.py:133-145` |
| LangGraph 1.2.10 submits node tasks with `__cancel_on_exit__=True`; its executor cancels and awaits them when the consumer exits. | installed `langgraph/pregel/_runner.py:471`, `:528`, `:925`; `langgraph/pregel/_executor.py:186-205` |
| `ReplayRunner` cancels its drain task on cancellation and re-raises. | `api/replay.py:123-128` |
| `run_research` cancels its loop-lag monitor in `finally`; publication and memory writes happen only in `finalize_report`. | `main.py:358-364`; `graph/nodes.py:554-654`, `:683-790` |

### 3.11 Web app

| Fact | Where |
|---|---|
| `STAGES` (seven rows, static metas), `RunState`, 22 handlers; `run-state.test.ts` asserts the exact handler keys and RunState keys. | `web/lib/run-state.ts:16-24`, `:57-78`, `:177-356`; `web/test/run-state.test.ts:29-41`, `:44-50` |
| `RunEvent` is `{type, metadata}`; the timestamp is dropped. | `web/lib/run-state.ts:79`, `:382` |
| `NoteState` keeps no `kinds`; ack "where" text depends only on the active step. `OUTCOME_TEXT.pending` reads "not checked" today. | `web/lib/run-state.ts:48-51`, `:346-355`; `web/lib/notes.ts:22-41`, `:70-76` |
| `rowBrief` gives every step but Researching one sentence; Planning's finished brief lists the titles. | `web/lib/briefs.ts:25-31`, `:53-74` |
| `BriefSpine` takes the props `marks`, `run` and `onToggle`, and renders `li.spine-row` rows with `.ps-head` and `.ps-x > .ps-xi > .ps-brief` lines (`.ln`, `--i` stagger); the checklist is `.ps-topics > .ln[data-topic]` with `.mk` (ring, dot, drawn ✓). The Failed and service-stopped stages use the compact `<Spine>`. | `web/components/BriefSpine.tsx:17`, `:27-141`; `web/app/globals.css:1231-1333` |
| Reviewing's outcome line is "Accepted · {score}" / "Not accepted · {score}". | `web/lib/run-state.ts:149-159` |
| `SessionScreen` stage order: not found → loading → service-stopped (`running` + `finished_at`) → Submitted → check → Running → Failed → Report. | `web/components/SessionScreen.tsx:194-205`, `:208-233` |
| Chip: `STATUS` labels and dots; `isLive` is `running`/`needs_input`; the sidebar marks only live rows. | `web/lib/format.ts:24-36`, `:63-73`; `web/components/Sidebar.tsx:48-58`; `web/components/StatusChip.tsx:4-9` |
| The topbar holds the status chip and the replay chip. | `web/components/Topbar.tsx:16-19` |
| The report renders the whole Markdown in one `article.card` with a "Your notes" block above `.prose`; the evidence line is marked by a remark plugin. | `web/components/ReportBody.tsx:63-137`; `web/app/globals.css:826-832`, `:1406-1410` |
| The report column and rail are centred as one group at `--report-card-w` (human decision "card hugs the text"). | `web/app/globals.css:1217-1229`; `web/e2e/layout.spec.ts:128-163` |
| e2e tests assert the first `.prose p.avail` is the evidence line and `h2` "Bottom line"/"Sources" inside `.prose`; `waitTerminal` and the capture script list the terminal statuses. | `web/e2e/report.spec.ts:9-11`; `web/e2e/support.ts` (`waitTerminal`); `web/scripts/capture-replay-events.mjs:12` |

### 3.12 Theme and motion

| Fact | Where |
|---|---|
| Colour tokens (`--status-ok/warn/danger`, `--muted`, `--meta`, `--border(-soft)`, `--surface`, `--bg`, `--accent` only on `.btn-primary`); motion tokens `--motion-fast` 120, `--motion-base` 200, `--motion-fluid` 420, `--fill-line` 620, `--fill-solid` 700, `--motion-halo` 2200, eases. Layout tokens `--rail` 300, `--sidebar` 296, `--sidebar-collapsed` 64; `.viewport` gutters 24 px. | `web/app/globals.css:5-49` (motion `:22-37`, layout `:45-47`), `:38`, `:205` |
| Reduced motion zeroes every duration and iteration, then restores opacity-only `enter`/`leave`/`arrive`. | `web/app/globals.css:991-1028` |
| DESIGN §3.4 and §5.6: the halo is "the whole looping budget for this screen"; a second, edge-less loop was removed. | `docs/design/DESIGN.md:759-771`, `:1426-1430` |
| The picked canvas CSS adds a skeleton sheen loop (`.sk::after`, 1800 ms), an indeterminate drift loop (`.pb.ind i`, 2400 ms), and a 600 ms bar fill, with reduced-motion rules that stop both loops. | `.superpowers/progress-canvas/project/progress.css:8-14`, `:30-35`, `:173-179` |

### 3.13 The latency workstream (D38)

| Fact | Where |
|---|---|
| `agents.verifier_concurrency` goes from 16 to 64; it also bounds the writer's Statement Check. | `docs/superpowers/audits/2026-09-30-latency-audit.md:18`, `:109`, `:134` |
| An experiment may drop `agents.verifier_batch_size` from 5 to 2, which sizes the Statement Check too: a Tamil-sized pass of 170 figure-bearing findings becomes 85 Context Check calls. | `docs/superpowers/audits/2026-09-30-latency-audit.md:22`, `:113`, `:137-140` |
| A failed batch's two re-ask halves run together instead of one after the other. | `docs/superpowers/audits/2026-09-30-latency-audit.md:20`, `:118` |
| The run-wide tool lock is narrowed to admission and ledger commits, with a per-URL single-flight fetch, one shared HTTP client and robots.txt cached per host per run; this amends EV-D9. The audit notes that Stop must then cancel more in-flight fetches. | `docs/superpowers/audits/2026-09-30-latency-audit.md:21`, `:112`, `:227`, `:291` |

---

## 4. Contracts shared by more than one phase

Whichever phase lands first implements the item; later phases reuse it.

1. **Progress events are live-only.** A progress event (§6.1) is published through `publish_live` and is **not** returned in any `state_update["events"]`. So it is never in `state.events`, a checkpoint, the quality record or `node.completed.event_count`, and the CLI's plain and verbose output are unchanged (no progress type ends in `.completed` or is in `PROGRESS_EVENT_TYPES`). `ResearchSession.publish` records it, so a reconnect replays it.
   - **What its metadata may carry.** The builders' rule that metadata never holds raw provider text (`agents/events.py:27-31`, `graph/events.py:34-37`) exists because those events are copied into `ResearchState.events`. A progress event never is, so the two docstrings are amended to say the rule binds state-bound events, and a progress event may carry exactly these reader-facing texts: plan and section titles (≤ 160 chars), a finding's content (≤ 160) and a drafted sentence (≤ 200), both of which the published report and evidence log print anyway, hosts, and page-word correction values (≤ 60). Everything else is a count, an id or an enumerated value. Never an exception message, a URL path or query, or a model's reason text (a Context Check `reason`, a review defect's `problem`, a plan review's `repair_instruction`). AC13 tests this allow-list.
2. **Terminal note outcomes.** `NoteOutcome` gains `not_checked`, and `note_outcome` gains a keyword `terminal`; with `terminal` true it never returns `pending`.
   - Phase A replaces `note_outcome` with the §5.6 table and adds `note_steering_outcome`.
   - If Phase D lands before A, D changes only today's function (`api/notes.py:343-376`): it gains `*, terminal: bool`, and each of its three `return "pending"` exits (`:357`, `:360`, `:376`) returns `"not_checked" if terminal else "pending"`. `note_records` and `session_note_fields` pass `terminal` as §5.6 states. A then replaces the function.
   - `ReaderNoteResponse.outcome` and `web/lib/api.ts` `ReaderNoteOutcome` gain `not_checked`. `OUTCOME_TEXT.not_checked` is "not checked", and `OUTCOME_TEXT.pending` becomes "not checked yet" (I). `pending` now occurs only while a run is going, and the one place that prints an outcome, the report's notes block, shows only for a finished session (`web/components/ReportBody.tsx:120-131`).
3. **Step ids.** A session's step is one of `check`, `planner`, `researcher`, `source_evaluator`, `evidence_verifier`, `report_writer`, `report_reviewer`, `finalize_report`. Labels come from `STAGES` (`web/lib/run-state.ts:16-24`); `check` reads "the questions" (I).
4. **Event timestamps on the web.** `RunEvent` gains `timestamp: string` (`toRunEvent` copies `event.timestamp`), and `Handler` gains a third argument `timestamp`. Phase B implements it. *Amended (Phase B plan, O1; accepted by plan review 1): `RunEvent` is `{ type, metadata, timestamp?: string }`; `toRunEvent` always copies the frame's `timestamp`, and a handler receives `string | null`.*
5. **What one phase takes from another.**
   - Phase C uses Phase A's `is_research_note`, `has_steering_kind` and `steering_view` (§5.1), and `note_outcome(..., terminal=True)` with `note_steering_outcome` (§5.6). C's plan starts from A merged, as §9 orders them. If C has to land first, it adds those helpers exactly as §5.1 and §5.6 state them.
   - `ReaderNote.short` and the interpreter's `short` (§7.2) are shared: whichever of A and C lands first adds them.
   - Phase B's Planning note slots and Reviewing note rows use A's `NoteState.kinds`, research-note results and mixed-note results. Its Writing progress counts C's bottom-line phase. B lands last (§9).
   - Phase D needs only item 2's `terminal` rule.

---

## 5. Phase A — reader notes that land

### 5.1 Definitions and helpers

All helpers live in `agents/reader_notes.py` unless another file is named.
- `is_research_note(note: ReaderNote) -> bool` is `"new_angle" in note.kinds`.
- `has_steering_kind(note) -> bool` is true when `note.kinds` holds any of `emphasis`, `exclude`, `scope`, `about_reader`. Every steering note has one; a mixed note is a research note that has one.
- `steering_view(note) -> ReaderNote | None` returns the note unchanged when it has no `new_angle`; `note.model_copy(update={"kinds": [k for k in note.kinds if k != "new_angle"]})` for a mixed note; and `None` for a note whose only kind is `new_angle`. `steering_notes(notes)` maps `steering_view` over `notes`, drops the `None`s and keeps the order.
- `note_sub_topic` moves from `graph/nodes.py:1343-1378` to `agents/reader_notes.py` (agents cannot import `graph/`), with a keyword `reason: Literal["reader_note", "no_evidence"]`. Its rationale is "The reader asked for this in a note." for `reader_note`, and today's sentence for `no_evidence`. `graph/nodes.py` imports it from there. Everything else about the topic is unchanged: title `Your note: {restatement}`, coverage id `note-{note_id}`, one required target per new question (or the restatement when there is none), with the note's scope.
- `researched_note_topic_ids(state) -> set[str]`, in `graph/state.py`: a coverage id in `state.sub_topics` that starts with `note-` is researched when its latest `researcher.sub_topic.completed` event in `state.events` has a `stop_reason` other than `"provider_error"`, or when at least one of its targets is in `state.quality.answered_target_ids` (no `state.quality` counts as none answered). A topic with no completed event is not researched. So a note is still owed its pass when its thread failed, or when `stop` left its planning-time topic unstarted (§5.3), unless a finding answered one of its targets: then it has covered its note (D31) and owes none (review I5; review 2, I-1).

**Which requests print which notes (D20).** `render_reader_notes` is unchanged; what changes is the list each caller passes.

| Request | Notes it prints |
|---|---|
| Plan and plan review | every active note with all its kinds (`PLANNING_NOTES`, §5.2) |
| Research loop turns and extraction | `research_reader_notes` (`agents/reader_notes.py:113-115`) returns `steering_notes(notes)` |
| Source evaluation | `steering_notes(active_reader_notes(state.reader_notes))`, in place of the active list (`agents/source_evaluator.py:1107-1109`) |
| Writing: sections and bottom line | the same, in place of the active list (`agents/report_writer.py:3246-3248`) |
| Review packet | the same, with ids (`agents/report_reviewer.py:715-722`) |
| Verification | none (LB-D10) |

- A mixed note reaches every steering request as a steering note, with `new_angle` removed from its printed kinds, as D20 rules.
- A note whose only kind is `new_angle` reaches none of them. Loops never printed one (`agents/reader_notes.py:113-115`), and the review packet leaves it out (§5.5). The evaluator and the writer stop printing it too (spec choice, so one rule holds everywhere): its own topic is among the sub-topics the evaluator judges that topic's sources with (§3.2), `WRITING_NOTES` has no rule for new_angle (`agents/reader_notes.py:54-61`), and the writer drafts the topic's own part.

### 5.2 Planning-time research notes (D1, D2)

**Planner prompt.** `PLANNING_NOTES` (`agents/reader_notes.py:29-36`) becomes, exactly:

> The reader added these notes while the run was going; each line is the note as the run understood it, and a later note replaces an earlier one it contradicts. Plan within them: an emphasis note gives its subject more weight, an exclude note leaves its subject out, a scope note narrows the plan to its scope, and an about_reader note says who the report is for. A new_angle note is researched as a sub-topic of its own that the run adds to this plan once it is final: do not plan a sub-topic for it, and a subject only a new_angle note asks for is not a missing part of the question.

The plan request and the plan-review request both print it (`agents/planner.py:2740-2741`, `:2877-2878`), so the review stops reporting a note's subject as a missing dimension (E4).

**Appending at publication.** In `PlannerAgent.run` (`agents/planner.py:3233-3278`), after `super().run(state)` returns and before `planning_completed_event` is built, with no `await` in between:
1. `notes = live_reader_notes(self._reader_notes)` (state notes plus the board's newer ones, active only).
2. `held = [n for n in notes if is_research_note(n) and f"note-{n.note_id}" not in known]`, where `known` is the coverage ids of `state.sub_topics` plus the plan's.
3. `priority = max(t.priority for t in [*state.sub_topics, *plan.sub_topics]) + 1`, and `note_topics = [note_sub_topic(n, priority=priority, reason="reader_note") for n in held]`, in receipt order.
4. The returned update appends `note_topics` after the plan's topics in `sub_topics` and their target ids after the plan's in `initial_target_ids`.
5. `planning_completed_event(outcome, note_topics=note_topics, states=…)`: `sub_topics` lists the plan's topics, then each note topic as `{coverage_id, title, note_id}`; `sub_topic_count` counts both; a new `note_topic_count` counts the note topics. `states` is Phase B's (§6.3); before Phase B, no `state` key is emitted.

`plan` is `outcome.result`. When the run produced no plan (`outcome.result is None`), steps 1–4 are skipped: nothing is appended, `note_topic_count` is 0, and `state_update` returns only the errors, as it does today (`agents/planner.py:3868-3870`, review M10). Such a note is then picked up by the researcher, or owes a note pass.

A note interpreted after step 1 is picked up by the researcher (§5.3); the synchronous tail of the planner run leaves no gap.

### 5.3 Research-time research notes (D3)

**The dispatcher.** `ResearcherAgent.run` (`agents/researcher.py:4639-4882`) replaces its single `asyncio.gather` with a dispatcher:

1. `selected` = the eligible non-note topics, capped at `max_sub_topics`, followed by every eligible note topic (never capped: at most 10 by LB-D11a). Today's cap line `:4694-4695` applies to the non-note list only.
2. Each selected topic is started as its own task (`asyncio.create_task(research(index, topic, gated=True))`, where `gated` means it waits on the existing `Semaphore(sub_topic_concurrency)`).
3. Loop:
   - For every active research note on the board that is interpreted and whose `note-{id}` is neither in the node's state nor already started in this run, and while `stop` is not set: build `note_sub_topic(note, priority=max+1, reason="reader_note")`, add it to `self._run_note_topics`, and start `research(next_index, topic, gated=False)`. A note thread **does not wait on the semaphore**, so it starts at once even when `sub_topic_concurrency` loops are running.
   - If any task is unfinished: `await asyncio.wait({*unfinished, board_change}, return_when=FIRST_COMPLETED)`, where `board_change` completes on the board's next add or drop (below). Continue the loop.
   - Every task has finished. If the board has a note still being read (`board.pending`), `await notes_settled(timeout=30.0)` (the review node's own bound, `graph/nodes.py:1086`, shared as one constant `NOTES_WAIT_S` in `agents/reader_notes.py`) and continue the loop, so a note that arrives after the last thread finished but before the node returns still gets its thread. If the wait timed out, or nothing is pending, the research window **closes here**: the check that found no due note and the loop's exit run with no `await` between them.
4. A research note interpreted after the window closed is due a note pass (§5.4). The boundary is exact because both the dispatcher and the notes route run on one event loop.

**Board change notification.** `NoteBoard` gains `version: int`, incremented on every `add` and `drop`, and `async def wait_for_change(self, seen: int) -> int`, which returns the current version once it differs from `seen`. No wake-up is lost, because the dispatcher reads `version` before scanning. The researcher reaches the board through helpers in `agents/reader_notes.py`, imported at call time as today (`agents/reader_notes.py:80-105`). With no board bound (the CLI), the dispatcher waits on its tasks only.

**What the thread sees.** `_planned_targets()` (`agents/researcher.py:3207-3238`) returns the planned targets as today (confined on a pass) plus every target of `self._run_note_topics`. The coverage titles map built at loop start (`:4378-4386`) includes the run's note topics too. Two reads of the list differ in time (review M8):
- **Frozen:** a loop reads its extraction target list once, when it starts (`:4378`). A loop that started before a note thread never offers the note's targets to extraction, so none of its findings can bind to them.
- **Live:** `bound_sub_topic_findings` reads `_planned_targets()` again for its required ids when the loop ends (`:4577-4585`), so the list includes note targets added since. That changes nothing for the earlier loop: required ids only protect findings bound to them, and it has none.

**Failures and cancellation.**
- A thread that raises or ends in a provider failure sets `stop`, exactly as a planned loop does (`:4768-4783`). Once `stop` is set, no new note thread starts, so a research-time note that got no thread has no topic and is due a note pass.
- A planning-time note topic is gated (step 2) and sorts last, because its priority is one above the plan's (§5.2; `agents/researcher.py:314-340`). If `stop` is set while it still waits on the semaphore, `research` returns before its loop opens (`:4760-4762`), and the topic gets only a `provider_failure_stopped_processing` record (`:4841-4849`), no completed event. It keeps the topic it got at planning, `researched_note_topic_ids` leaves it out, and its note is owed its pass, which reuses the topic (review 2, I-1).
- A note thread that started and then ended in a provider failure keeps its topic in `sub_topics`, with whatever findings it returned, and its `researcher.sub_topic.completed` records `stop_reason: "provider_error"` (§3.4). Unless those findings answered one of its targets, `researched_note_topic_ids` leaves the topic out, so the note is still owed its one note pass, and the pass reuses the topic rather than adding a second one (§5.4). A failed planned topic is retried through its missing required targets and an extra pass; a note target never buys an extra pass (`graph/state.py:283-300`), so this is the note's equivalent (review I5).
- Attempt-limit refusals are re-raised after every task has settled, and any other exception is re-raised after the refusals, in index order (today's `agents/researcher.py:4797-4808`).
- The dispatcher wraps the loop in `try/finally`. On any exit by exception, `CancelledError` included, it cancels every unfinished thread task and awaits them (`return_exceptions=True`). Manual tasks, unlike `gather`, are not cancelled with their parent; this is what makes Phase D's stop reach every loop.

**Budget.** Note threads reserve from the run's one `RequestBudget` like any loop (`request_budget.py:131-157`). There is no separate allowance; a spent ceiling halts the run as it does today.

**Events and state.**
- `sub_topic_started_event` (`agents/researcher.py:2590-2612`) gains `note_id` (the note's id; the key is omitted for a planned topic). Its `index` is the next index after the planned ones.
- `research_completed_event.sub_topics_planned` counts `len(state.sub_topics) + len(self._run_note_topics)`.
- The researcher's update appends `self._run_note_topics` to `sub_topics`. Inventories are untouched, as `note_pass_node` leaves them today.
- The fold that builds findings, runs and events keeps index order (planned, then note threads by start order).

### 5.4 Notes that arrive after research (D4)

**Routing.** In `graph/state.py`:
```python
def notes_due_a_pass(state):
    verdicts = note_dispositions(state)
    researched = researched_note_topic_ids(state)
    return [
        note for note in active_reader_notes(state.reader_notes)
        if not note.passed and (
            (is_research_note(note) and f"note-{note.note_id}" not in researched)
            or (not is_research_note(note) and verdicts.get(note.note_id) == "no_evidence")
        )
    ]

def notes_due_a_redraft(state):   # only a note with a steering kind buys a redraft
    verdicts = note_dispositions(state)
    return [
        note for note in active_reader_notes(state.reader_notes)
        if has_steering_kind(note) and not note.redrafted
        and (verdicts.get(note.note_id) == "ignored_with_evidence" or not note.reviewed)
    ]
```
- The route order is unchanged (`graph/state.py:380-385`). Because both note checks come before `review_unavailable`, an owed pass runs whatever the review's status, `provider_failed` included (D5).
- A mixed note can buy both its pass (its new_angle half, while it has no researched topic) and its redraft (its steering half, on `ignored_with_evidence`, or when no review input carried it). The pass comes first by route order, and the review after the pass carries the note, so a mixed note that arrived late is judged before any redraft (D20). One pass and one redraft per note is exactly today's per-note allowance in the recursion limit (`graph/state.py:64-86`), which therefore stays unchanged.
- A mixed note's steering half judged `no_evidence` buys no pass: its topic already researched the note (the second clause admits steering notes only).

**`note_pass_node`** (`graph/nodes.py:1381-1443`) changes in two places:
1. It builds `note_sub_topic(note, priority=…, reason="reader_note" if is_research_note(note) else "no_evidence")` only for a due note whose `note-{id}` is not already in `state.sub_topics`, and appends only those.
2. `extra_pass_target_ids` holds the targets of every due note's topic, whether reused (a failed thread's topic, §5.3) or new.

Everything else is unchanged: the `passed` flag, `note_passes`, and the `graph.note_pass.started` event, whose `note_ids` name every due note.

**A reused topic's pass can be refused (review 2, M-2).** `note_pass_node` sets `extra_pass_target_ids`, so the researcher's unfunded-pass guard applies to a note pass (`agents/researcher.py:2348-2377`, `:4701-4739`). When every topic of the pass has a recorded acquisition state with no calls left and no extraction owed, which a failed thread can leave behind by spending its budget before the provider failed, the researcher opens no loop and records `researcher_extra_pass_unfunded` (`:2320-2345`). The note is already `passed`, so it gets no second pass; it reads `not_found`, because its thread searched (`searched = true`, `utils/types.py:1655`). This is the honest outcome, and it is stated rather than changed. An unstarted topic has no recorded acquisition state, so the guard never refuses its pass.

**Writer after a note pass (D4).**
1. `ReportWriterTask` gains `note_pass_coverage_ids: list[str] = []`.
2. In `build_task` (`agents/report_writer.py:3190-3250`), when the latest loop marker in `state.events` — the last of `graph.report.redraft_requested`, `graph.extra_pass.started`, `graph.note_pass.started` and `graph.note_redraft.requested` — is `graph.note_pass.started`, and `state.composition` is not `None` with `composition.iteration == state.iteration`:
   - `note_pass_coverage_ids = [f"note-{i}" for i in marker.metadata["note_ids"]]`;
   - `previous = state.composition`;
   - `defects = []`.
3. In `compose_written_report` (`:3000-3015`), when `note_pass_coverage_ids` is non-empty, `redraft_this = placement.coverage_id in note_pass_coverage_ids` and `defects_here = []`. `_run_part` then carries every other part over unchanged through `:2180-2188` (a part with no previous section is still drafted, per P2-1 at `:2190-2193`).
4. `is_redraft` stays `bool(task.defects)`, so it is False and the bottom line is drafted fresh (`:2648-2649`).
5. `_is_redraft_hop` (`:1013-1035`) is unchanged: `defects` stay empty after any note marker.
6. The reviewer still runs a full review after a note pass (the note markers stay in `_FRESH_DRAFT_MARKERS`, `graph/nodes.py:397-403`).

**The exception (D4, D22).** A note with a steering kind, a steering note or the steering half of a mixed note, that the review finds `ignored_with_evidence`, or that no review input carried, takes the existing note redraft route (`graph/nodes.py:1281-1309`). That route keeps drafting every part afresh (`agents/report_writer.py:1033-1034`), as D22 rules: no record says which parts a note affects.

### 5.5 The review's view of notes (D5)

- `build_report_review_input` (`agents/report_reviewer.py:715-722`) lists `steering_notes(active_reader_notes(state.reader_notes))` (§5.1): every steering note, and every mixed note with `new_angle` removed from its kinds, each with its id. `REVIEW_NOTES` is unchanged.
- A disposition naming a note whose only kind is `new_angle` is dropped, because its id is not in the packet (`:1573`). A mixed note's disposition is kept and judges its steering half (D20).
- `_reviewed_notes_update` (`graph/nodes.py:1089-1108`) is unchanged: it marks every active note of the review's input `reviewed`, which is what `notes_due_a_redraft` reads for a mixed note.
- LB-D10 holds: no verifier request changes.

### 5.6 Outcomes (D5)

Two functions in `api/notes.py`, each with `waiting = "not_checked" if terminal else "pending"`.

`note_outcome(note_id: str, state: ResearchState | None, *, terminal: bool) -> NoteOutcome` gives a research note's result from its topic's targets (a mixed note's new_angle half included) and a steering note's result from the review:

| Case, checked top to bottom | Outcome |
|---|---|
| no state, or the note is not in `state.reader_notes` | `waiting` |
| a later active note replaces it | `replaced` |
| research note, no `note-{id}` topic in `state.sub_topics` | `waiting` |
| research note, at least one of its topic's targets is in `state.quality.answered_target_ids` (no `state.quality` counts as none answered) | `covered` |
| research note, `state.composition` lists at least one of its targets in `not_found` with `searched = true` | `not_found` |
| research note, otherwise | `waiting` |
| steering note, latest review verdict `honoured` | `covered` |
| steering note, `ignored_with_evidence` | `not_addressed` |
| steering note, `no_evidence` | `not_found` |
| steering note, no verdict | `waiting` |

`note_steering_outcome(note_id, state, *, terminal) -> NoteOutcome | None` gives a mixed note's steering half (D20). It is `None` for every note that is not mixed. For a mixed note it is `waiting` with no state, `replaced` when a later note replaces it, and otherwise the review's verdict mapped exactly as the steering rows above (`honoured` → `covered`, `ignored_with_evidence` → `not_addressed`, `no_evidence` → `not_found`, no verdict → `waiting`).

- **D31.** "Covered" means at least one of the note's targets is answered by a verified finding. The unanswered targets are still listed under "What we couldn't confirm" (`agents/report.py:1533-1551`).
- `note_records(board, state, *, terminal)` (`api/notes.py:378-392`) returns both outcomes per note, and `session_note_fields` (`api/sessions.py:214-254`) passes `terminal = session.status in TERMINAL_STATUSES or session.finished_at is not None`.
- `ReaderNoteResponse.outcome` gains `not_checked`, and `ReaderNoteResponse` gains `steering_outcome: ReaderNoteOutcome | None = None`; `web/lib/api.ts` mirrors it as `steering_outcome?: ReaderNoteOutcome | null`.

### 5.7 Web changes (Phase A)

- `NoteState` gains `kinds: string[]`, from `session.note.interpreted.metadata.kinds`, and `threadStarted: boolean`, set when a `researcher.sub_topic.started` event's `note_id` names the note.
- `ackFor` (`web/lib/notes.ts:34-41`) uses this `where` table for a research note, a mixed note included:

| When | `{where}` (I) |
|---|---|
| Planning is active | ", as its own topic" |
| Researching is active and `threadStarted` | ", researching it as its own topic now" |
| Researching is active and no thread has started for it | ", as its own topic" |
| Evaluating, Verifying or Writing is active | ", researched as its own topic after this draft is reviewed" |
| Reviewing is active | ", researched as its own topic next" |

  "Now" is keyed on the thread's own started event, because a note interpreted after the researcher's window closed (§5.3) gets no thread in that run (review M4). Steering notes keep today's `WHERE` table (`web/lib/notes.ts:22-29`).
- Researching's checklist gains the note thread's row from `researcher.sub_topic.started` through the existing `topicFor` (`web/lib/run-state.ts:139-147`).
- `OUTCOME_TEXT.not_checked` is "not checked", and `OUTCOME_TEXT.pending` becomes "not checked yet" (I; §4 item 2).
- Until Phase C removes the report's notes block, a mixed note's caption there reads "{outcome words}; the rest of your note: {steering outcome words}" (I), from `OUTCOME_TEXT`.
- The Planning slots are Phase B's (§6.3). Until B lands, the finished Planning brief lists the note topic's title with the plan's (`web/lib/briefs.ts:69-71`).

### 5.8 Edge cases (Phase A)

| Case | Behaviour |
|---|---|
| A research note is replaced by a later note before its topic exists | It never gets a topic; the later note is judged on its own kinds. |
| A research note is replaced after its topic was researched | The topic's part stays in the report; the outcome is `replaced`. |
| The same subject is also planned by the model despite the prompt | Two topics cover it; nothing is merged (§12 R3). |
| Ten research notes during one researcher run | Ten threads start ungated; LB-D11a bounds the count. |
| A research note arrives while an extra pass or a note pass runs | It gets a thread in that run (the dispatcher runs in every researcher node). |
| Stop (Phase D) during research | The dispatcher's `finally` cancels every thread. |
| A note thread ends in a provider failure | `stop` is set and no later note thread starts. The note keeps its topic and, unless the thread answered one of its targets, is still owed one note pass, which reuses the topic (§5.3, §5.4). |
| Another loop's provider failure sets `stop` while a planning-time note topic still waits on the semaphore | The topic never starts and has no completed event. The note keeps its topic and is owed one note pass, which reuses the topic (§5.3). |
| Every topic of a note pass has already spent its acquisition budget | The researcher refuses the pass as unfunded, no loop opens, the note stays `passed` and reads `not_found` (§5.4). |
| A note is still being read when every thread has finished, and its reading outlasts the 30 s wait | The window closes when the wait times out; once read, the note is owed a note pass. |
| A mixed note, for example "I also want pastries, but skip anything that might be closed" (new_angle, exclude) | Its new_angle half gets its topic like any research note. Its exclude half is printed to every loop, extraction, the evaluator, the writer and the review as an exclude note, is judged by the review, and can buy the note's one redraft. Both results reach Reviewing and the bottom line (D20). |

### 5.9 Documentation (Phase A)

| Doc | Change |
|---|---|
| `DESIGN.md` §3.5 (`:878-888`) | A research note never waits for a review: planning-time notes join the plan; research-time notes start their own topic at once; later notes buy one note pass, after which only the note's part and the bottom line are rewritten. A note thread that fails is owed that pass. A mixed note does both: its topic, and its steering half judged and enforced like a steering note. |
| `DESIGN.md` §3 inventory (`:219-224`) | Note outcomes gain `not checked` for a finished run. |
| `api-gaps.md` 3.9 (`:127`) | Unchanged in substance; replay still never applies a note. |

---

## 6. Phase B — live progress per step

### 6.1 Event contract

All new types are progress events (§4.1). Extended types keep every existing key.

| Event | Emitted | Metadata |
|---|---|---|
| `planner.progress` (new) | at the start of each plan-side request: the draft, the local repair, each review, the review repair | `step`: `drafting` \| `fixing` \| `checking`; `check_round`: 0, 1 or 2; `sub_topics`: `[{coverage_id, title (≤ 160), state}]`, `[]` until a draft has returned. `state` is in the §6.3 table. |
| `planner.planning.completed` (extended, still in state) | unchanged | each `sub_topics` entry gains `state` (final, §6.3) and, for a note topic, `note_id`; plus `note_topic_count` (§5.2) |
| `source_evaluator.progress` (new) | once when the batches are planned, before the first scoring call; then after each batch settles | `to_rate`, `reused`, `capped`, `rated`, `strong`, `fair`, `weak`, `unrated`, `batches`, `batches_done` |
| `source_evaluator.evaluation.completed` (extended) | unchanged | `strong_count`, `fair_count`, `weak_count` over the snapshot's scored sources |
| `evidence_verifier.progress` (new) | once after the Figure Match pass, before the first Context Check; then after each batch settles | `total` (the findings this pass judges), `checked` (those with a verdict so far, the ones Figure Match decided included, so the first event already counts them and the tally never jumps), `verified`, `corrected`, `quoted`, `dropped`, `batches`, `batches_done`, `sample` (below) or `null` |
| `report_writer.progress` (new) | when the part jobs are built; when each part's draft returns; after each Statement Check batch of a part or of the bottom line; when the bottom-line call starts | `phase`: `sections` \| `bottom_line`; `parts_total`, `parts_returned`, `sentences_drafted`, `sentences_checked`, `backed`, `removed`, `unchecked`, `fraction` (0–1, 3 decimals); `sample` (below) or `null` |
| `graph.report.reviewed` (extended; **now published live**, with the reviewer's `graph.node.started`) | right after `_review_report` returns, before the notes wait (`graph/nodes.py:852-862`) | `criteria`: 5 × `{dimension, met: true \| false \| null, kinds: [GapKind…]}` for completeness, evidence_quality, attribution, uncertainty and readability, in `DIMENSION_GUIDANCE` order (D23); `notes`: `[{note_id, result: met \| not_met \| pending \| not_checked, reason, steering?: {result, reason}}]`, with `steering` only for a mixed note (§6.7, D20) |

*Owner-delegated decision (2026-10-01; O1, O2 item 2).* `report_writer.progress` also carries `parts_failed`: how many of the parts in `parts_returned` ended `failed`, so nothing of them is written. It is a count only (never a provider's or an exception's text, §4 item 1). `parts_returned` keeps its meaning, every part that has settled, written or failed, so `fraction` and the bar are unchanged and `parts_failed` never exceeds `parts_returned`. The page reads written parts as `parts_returned - parts_failed` (§6.6).

Samples, bounded, and holding only §4 item 1's allowed texts (never a model's reason text):
- **Verifier** `{text, verdict, correction, drop_reason, source}`: `text` is `summarize_text(finding.content, limit=160)`; `verdict` is the finding status; `correction` is `null` or `{field: period | period_cleared | scope | subject | kind | figure, value}` (value ≤ 60 chars; page words from the kept context); `drop_reason` is the enumerated finding or first figure drop reason; `source` is `{role: SourceRole | null, host}`.
- **Writer** `{text, verdict: backed | removed, findings, section}`: `text` ≤ 200 chars (the corrected text for a `corrected` verdict); `findings` is the count of the sentence's cited labels; `section` is the section title, or "Bottom line".

The `graph.node.started` of `report_reviewer` is published live (the same object stays in its snapshot) so Reviewing's elapsed time starts on time.

**Counts are cumulative and idempotent (review M13).** Every progress event carries running totals, and each total counts a source (evaluator), a finding (verifier) or a sentence label (writer) once, when an event first reports it. So nothing depends on how many progress events arrive or how items are batched:
- if the latency work drops the batch size to 2 (§3.13), a Tamil-sized verifier pass emits about 86 `evidence_verifier.progress` events (one after Figure Match, then one per batch) instead of about 35, and the brief is the same;
- if a failed batch's two re-ask halves run together and each reports on its own, their labels are counted once.

### 6.2 Hooks (backend)

| Agent | Hook |
|---|---|
| Planner | In `finalize` (`agents/planner.py:3626-3842`), publish `planner.progress` immediately before each of the five `_request_plan`/`_review_plan` calls, with the states in §6.3. A pure helper `plan_progress(sub_topics, *, flagged, repaired, step, check_round)` builds the metadata. `flagged` is the plan ids that match `topic-\d{2}` in the text being acted on: the attempt's labelled problems for a local repair; `requested_problems(review)` plus `repair_instruction` for a review repair. Only ids, never the text, leave the helper. `repaired` is the ids whose title or targets changed, or that are new, between the attempt before a repair and the one after. The planner does no provider or tool work between one plan-side call's return and the next call's start (`agents/planner.py:3664-3842`), so each start event also marks the previous call's return: the draft's titles, and the check's result. After the last call, `planning.completed` marks it (`:3268-3269`). |
| Evaluator | `score_sources` (`agents/source_evaluator.py:1154-1301`) takes `on_progress: Callable[[dict], None] \| None`. It is called once after `batches` is built, then in `score_one`'s completion (success or provider failure), with cumulative counts in completion order. `run` passes a callback that builds and live-publishes the event. |
| Verifier | `verify` (`agents/evidence_verifier.py:1017-1055`) takes `on_progress`. It is called once after the Figure Match loop (`:1021-1040`), then inside `one(batch)` (`:1045-1047`) once `self._check` returns: `one` computes `verify_finding(item, replies[key])` (a pure function, `:878`) for its own items to build the running counts and the sample, then returns its replies. The merge after the gather (`:1049-1053`) is unchanged. |
| Writer | `check_statements` (`agents/evidence_verifier.py:1485-1536`) gains `on_batch: Callable[[Sequence[StatementCheckItem], Mapping[str, StatementVerdictDraft \| None]], None] \| None = None`, called inside `one(batch)` once `_check_statement_batch` returns, with that batch's items and verdicts. A `_WritingProgress` object (one per `compose_written_report`) holds the counters as sets of labels, so a label is counted once; `_run_part`, `_check` and `_run_bottom_line` update it and live-publish `report_writer.progress`. The five substitutes of `check_statements` (§3.6) gain `on_batch=None` (§11.2, review I3). |
| Reviewer node | `report_reviewer_node` publishes its started event live, then builds `report_review_completed_event(..., criteria=…, notes=…)`, publishes it live, and returns it in its events as today. |

**Sample choice.** Deterministic and tested:
- Verifier: among the batch (or the Figure Match set, for the first event), the first finding in batch order by priority `verified_corrected` > `dropped` > `verified` > `quoted`.
- Writer: the first sentence in batch order with a non-`None` verdict; `null` when the whole batch failed. The ticker keeps its last sample.

**Writing fraction.** `P` = the parts this run drafts (jobs with `redraft` true and findings). Each drafted part and the bottom line weigh `1/(P+1)`. A part contributes `checked_i / drafted_i` once drafted (0 before); the bottom line contributes its own `checked / drafted` once drafted. The value never decreases. With `P = 0`, the bottom line alone fills the bar.

**Evaluator split (spec choice).** On `overall_score`:

| Word | Rule |
|---|---|
| Strong | ≥ 0.70 (new `STRONG_SOURCE_THRESHOLD = 0.70`) |
| Fair | ≥ 0.40 and < 0.70 |
| Weak | < 0.40 (exactly today's `low_confidence`) |
| not rated | `unscored_*` statuses |

These counts cover the sources scored in this pass; reused sources are counted in `reused`.

**Criteria from defects (spec choice).** A criterion is `met: null` when the review is not `scored`; otherwise `met: false` when a material defect maps to it, and `met: true` otherwise. `kinds` lists the mapped kinds, one per material defect, in defect order. Mapping:

| GapKind | Criterion |
|---|---|
| coverage, mechanism | completeness |
| missing_support, acquisition, source_quality, freshness | evidence_quality |
| identity | attribution |
| contradiction | uncertainty |
| semantic_duplicate, presentation | readability |

prioritization and actionability have no kind, so no review can mark them ✗; the event and the brief leave them out (D23).

### 6.3 Planning (D7, `Main.dc.html` option B)

**Structure of the open row's brief**, in order:
1. The status line: a stack of five `p` in one grid cell (`.xf`), one visible.
2. Ack lines (existing).
3. The slot list (`.ps-topics` markup): one row per topic, then one row per research note known to the page.

**Slot states** (from `planner.progress` and the final `planning.completed` states):

| State | When | Mark | Fact (I) |
|---|---|---|---|
| skeleton | before the first titles | ring; a skeleton bar in the title cell | "drafting" while `step = drafting`, else none |
| drafted | titles known, not yet checked | ring | none |
| checking | `step = checking` | green dot with halo | "checking" |
| being_fixed | `step = fixing` and the slot is flagged | green dot with halo | "being fixed" |
| passed | the last check was sound, or did not flag this slot | ✓ | none |
| fixed | as passed, and a repair changed or added it | ✓ | "fixed" |
| flagged | the last check flagged it and no repair is left | amber ✗ | "still flagged" |
| not_checked | no check produced a verdict | ring | "not checked" |
| note, pending | a research note interpreted while Planning is active | ring | "joins the plan" |
| note, planned | `planning.completed` lists `note-{id}` | ✓ | "from your note" |

- Before titles, the list shows **four** skeleton slots at widths 78 %, 64 %, 72 %, 52 % (canvas `widths`). This is a spec choice: the count is a placeholder, not a claim.
- When titles arrive, the first `min(4, n)` slots cross-fade from bar to title, extra titles rise in, and surplus bars fade out.
- A note slot appears when its note is interpreted as a research note while Planning is the active row. If `planning.completed` does not list it, the slot is removed, and the note appears as a thread in Researching (§5.3).

**Status line** (I): "Reading your question and your answers…" (or "Reading your question…" when the check gave no answers) until the first `planner.progress`; then "Drafting a plan for your question…" (drafting), "Checking the plan covers everything you asked…" (round 1), "Checking the fixed plan…" (round 2), "Fixing {k} topic(s) the check flagged…" (`k` flagged, `k > 0`) or "Fixing what the check found…" (`k = 0`); "Plan ready · research starts now" on `planning.completed`.

**Row subtitle.** The elapsed time `Xm SSs` (green while active), from the planner's `graph.node.started` timestamp. **Outcome:** "{n} sub-topics · {k} from your note(s) · {duration}" (the clause is left out when `k = 0`).

**Motion.**
- The skeleton sheen is `.sk::after`, a `color-mix(in oklch,var(--fg) 7%,transparent)` gradient translating over `--motion-halo`, repeating (D21). It exists only on a slot in the skeleton state while Planning is the active row: it ends when titles arrive, when Planning finishes, and when the run stops.
- Title and fact cross-fades: opacity over `--motion-base`, `translateY(5px)` over `--motion-fluid`, `--ease-entrance`.
- Slots stagger 60 ms, the brief's own rhythm (the canvas's 260 ms is replaced to match LB-D1).
- Reduced motion: no sheen (static bar), fades only.

### 6.4 Evaluating sources (D8, `Evaluating.dc.html` option A)

- **Brief.** `b-now` "Rating {to_rate} sources for trustworthiness and relevance" (I); with `reused > 0`, "Rating {to_rate} new sources · {reused} already rated" (I); with `to_rate = 0`, "No new sources to rate" (I) and no bar or stats.
- **Bar.** A determinate bar (`.pb`), `fraction = (rated + unrated) / to_rate`.
- **Stats row.** Rated "{rated} of {to_rate}", Strong, Fair, Weak; each reads "not yet" before the first batch lands.
- **Subtitle** (green): "starting · not yet rated" before the first batch, then "{rated} of {to_rate} rated".
- **Outcome.** "{scored} sources rated · {strong} strong · {fair} fair · {weak} weak", plus " · {u} not rated" when some are unscored (I).
- **Motion.** Numbers tween over 400 ms (`useTween`); the bar fills over `--fill-line`.

### 6.5 Verifying evidence (D9, `Verifying.dc.html` option C)

- **Brief.** Eyebrow "Just checked"; a `.tickbox` (hairlines above and below, `min-height:112px`) with the last two samples stacked, the newest showing. Before the first sample, one `b-sub` line "The first findings are being checked…" (I), which pairs with Writing E's placeholder (spec choice).
- **Sample text.** `p.qt` shows the finding text, in curly quotes when the verdict is `quoted`.
- **Verdict line.** `p.vd` shows the verdict words, then " · {source}". Verdicts (I):

| Verdict | Words |
|---|---|
| verified | "verified" |
| verified_corrected | "corrected — the page dates it {v}", "— the page states no period for it", "— the page says it covers {v}", "— the page says it is about {v}", "— the page states it as an actual/a forecast", or "— one of its figures was not on the page" |
| quoted | "quoted as written" |
| dropped | "dropped — {reason}": `read_not_found` "the page could not be read again"; `snippet_not_on_page` "the page does not say this"; `evidence_not_on_page` "the page does not show this figure"; `correction_not_on_page` "the page does not back its date or scope"; `context_rejected` "the page's context does not support it"; `context_unavailable` "its figures could not be checked" |

- **Verdict colour.** `--status-ok` for kept verdicts, `--status-warn` for dropped (D30; the canvas coloured every verdict green).
- **Ticker pace.** The visible sample changes at most once every `TICKER_HOLD_MS = 1200` (a dwell, like `HANDOFF_HOLD_MS`, `web/components/BriefSpine.tsx:10`, not an animation duration). A sample that arrives sooner waits, and when the hold ends the newest waiting sample shows. `RunState` still keeps the latest two, so a burst and a replay end on the same sample. Writing's ticker (§6.6) uses the same pace. This keeps the ticker readable whatever the verifier's batch size (§3.13).
- **The ticker and WCAG 2.2.2** (*owner-delegated decision, 2026-10-01; O2 item 3, reworded in O2 fix round 2*). A ticker changes its sample on its own, at most once every `TICKER_HOLD_MS`, and offers no way to pause it. Success criterion 2.2.2 (Pause, Stop, Hide) asks for a way to pause information that updates automatically, unless the updating is essential. The ticker does not meet that exception on its own: what it shows is also available another way, since the row's subtitle, bar and tally carry every count a sample illustrates. Having no pause control is therefore an accepted risk, recorded as an owner-delegated decision of 2026-10-01, and not a claim of conformance. Three things limit it, and `web/e2e/progress.spec.ts` and `web/test/components/brief-spine.test.tsx` pin the first two: the ticker is not an `aria-live` region (the only live region in the running spine is a note's acknowledgement), so a screen reader is not interrupted at each sample and reads one only when the reader reaches it; its change is a cross-fade, and under reduced motion the 5 px settle is dropped, so it is opacity only over 160 ms; and every count is also in the static subtitle, bar and tally. If the risk is ever to be closed, the remedy is to hold the samples while the pointer is over the ticker or focus is inside it, with a keyboard "Pause samples" toggle. `DESIGN.md` §5.6 records the same.
- **Source words** (I): `original_report` "an original report"; `independent_research` "independent research"; `derivative` "a round-up of other sources"; `company_statement` "the business's own words"; otherwise the host.
- **Bar and tally.** `.pb` at `checked / total`; tally `b-facts` "{checked} of {total} checked · {v} verified · {c} corrected · {d} dropped".
- **Subtitle** "starting · not yet checked", then "{checked} of {total} checked". **Outcome** unchanged (`web/lib/run-state.ts:236-240`).

### 6.6 Writing report (D10, `Writing.dc.html` option E)

- **Brief.** Eyebrow "Just written"; `.tickbox` with the placeholder "The first section is being drafted…" until the first sample, then samples: `p.qt` sentence; `p.vd` "✓ backed by {k} finding(s)" (`--status-ok`) or "✗ removed — no verified finding says this" (`--status-warn`), then " · {section}".
- **Bar.** `.pb` at `fraction`.
- **Tally** "{checked} of {drafted} sentences checked · ✓ {backed} backed · ✗ {removed} removed · section {parts_returned} of {parts_total}", plus " · {u} not checked" when `unchecked > 0`. `drafted` grows as sections return; the canvas's fixed 39 assumed a known total (D34).
- **Subtitle** "{parts_returned} of {parts_total} sections written"; in `phase = bottom_line`, "writing the bottom line". **Outcome** unchanged.
- **The ticker's placeholder** (*implementation ruling, final fix wave, 2026-10-01; E7*). Until a section has returned it is "The first section is being drafted…". Once one has, it is "The first sentences are being checked…" for as long as some drafted sentence is unsettled (`sentences_checked + unchecked < sentences_drafted`). When every drafted sentence is settled, none was checked (`sentences_checked = 0`, `unchecked > 0`) and no sample has shown, it is "None of the drafted sentences could be checked", so a run whose every Statement Check failed does not wait on "being checked" for good.
- **The ticker's placeholder when no sentence was drafted** (*implementation ruling, final fix wave, 2026-10-01; P3-4*). A part can return with every point refused (`report_writer_all_parts_refused`), which leaves `sentences_drafted = 0` with `parts_returned > 0`, and no sentence is or will be checked for it. While some part is still out (`parts_returned < parts_total`) the ticker keeps the pre-parts line, "The first section is being drafted…", since sentences may still come. Once every part has returned with `sentences_drafted = 0` it reads "No sentences were drafted to check". It never reads "The first sentences are being checked…" when nothing was drafted.
- **The subtitle names the sections that could not be written** (*owner-delegated decision, 2026-10-01; O2 item 2*). With `written = parts_returned - parts_failed` (§6.1), the live subtitle reads "{written} of {parts_total} sections written", then " · {f} couldn't be written" when `f = parts_failed > 0` ("2 of 5 sections written · 1 couldn't be written", "0 of 2 sections written · 2 couldn't be written"). A count of failed parts above the settled ones reads as every settled part failed, never a negative number, and an event from before the key reads as none failed. In `phase = bottom_line` the subtitle stays "writing the bottom line", and a draft with no part reads "no section to rewrite". The tally's "section {parts_returned} of {parts_total}" stays the settle count: it says how far the sections have got, the subtitle how many of them were written. A failed part still counts as returned, so the bar fills. The written count can fall by one: a part's draft returns and counts as written, then its Statement Check refuses every point and the part ends `failed`, so "{written} of {parts_total} sections written" can go down as well as up, while `parts_returned` and the bar never do.
- **The ticker's placeholder while the bottom line is written** (*owner-delegated decision, 2026-10-01; O2 item 7, corrected in O2 fix round 1*). The two lines above that describe a part that is still out or that returned with nothing drafted belong to the sections' phase. `sentences_drafted` counts the bottom line's own candidates as well as the sections' sentences, so the rule keys on it. In `phase = bottom_line`, with no sample yet, while `sentences_drafted = 0` the ticker reads "Writing the bottom line…", the words the subtitle uses, on both edges alike: when every part returned with nothing drafted (a note pass whose own part was fully refused while the carried parts let the bottom line run: it read "No sentences were drafted to check" until the bottom line drafted) and when there is no part at all (a pass with nothing to draft: it read "The first section is being drafted…"). Once the bottom line has drafted sentences (`sentences_drafted > 0`) both edges read as the sections' path does: "The first sentences are being checked…" for as long as a drafted sentence is unsettled, and "None of the drafted sentences could be checked" once every one is settled with none checked. In the sections' phase both edges keep their earlier words. "Writing the bottom line…" is the in-flight line: a Writing row that has finished (done, or hollow after a loop) and is reopened shows how the step ended, so with nothing ever drafted it reads "No sentences were drafted to check" in either phase (*O2 fix round 2*). A running row, and a row frozen by a stop, keep the in-flight line. `rowBrief` passes the row's state to `writingBody`, as the other bodies learn they are frozen.

### 6.7 Reviewing (D11, `Reviewing.dc.html` A revised)

**Criteria rows** (I), in this order, with no score anywhere; only the five criteria a review can mark ✗ (D23):
1. completeness "Covers your whole question"
2. evidence_quality "Rests on strong evidence"
3. attribution "Every claim is credited correctly"
4. uncertainty "Honest about what is uncertain"
5. readability "Easy to read"

The canvas's "Puts the most important first" (prioritization) and "Useful for deciding where to go" (actionability) are dropped: no issue kind maps to either (§6.2), so they could only ever show ✓ (D23). One name differs from the canvas (D35): its "Every claim is cited" becomes "Every claim is credited correctly", because attribution judges whether a claim's provenance reads as what it is, the publisher's own figure or a relay credited to its originator (`agents/report_reviewer.py:266-270`), not whether a claim has a citation.

**While the call runs:**
- the status line "Reading the draft as a critical reader would · usually 1–3 min" (I);
- the indeterminate bar (`.pb.ind`), a 28 % segment drifting over `--motion-halo`, repeating while the call runs (D21);
- every criterion row a ring with fact "reading";
- "Your notes" (eyebrow, after a `.b-rule`; left out when there are none) lists every interpreted, not-replaced note the page knows, in receipt order, named by its restatement with the first letter capitalised, each "reading";
- subtitle "reading the draft · {elapsed}".

**On `graph.report.reviewed`:**
- the bar hides;
- criteria reveal 60 ms apart as ✓ (met, no fact), amber ✗ (issue text: one kind → its words, more → "{n} issues · {first kind's words}"), or ring "not checked" (`met: null`);
- each listed note takes its result from the event's `notes`. A listed note the event does not name arrived after the review input was built: a research note then reads ring "researched next", because it has no topic yet and the route owes it a note pass (review M4); any other reads ring "not checked";
- the status line reads "Review done · deciding what happens next…" (I) until the route decision.

Issue words by kind (I): coverage "a part of your question has no answer"; mechanism "a step in the explanation is missing"; missing_support "a sentence says more than its sources"; acquisition "a needed source could not be read"; source_quality "a claim rests on a weak source"; freshness "a figure is out of date"; identity "a source is credited to the wrong publisher"; contradiction "sources disagree and the draft does not say so"; semantic_duplicate "the same point is made twice"; presentation "a sentence is hard to follow".

Note results in the event (§6.1):

| Note | `result` / `reason` | Mark, fact (I) |
|---|---|---|
| research note, a target answered | met / covered | ✓ "covered" |
| research note whose topic is researched (`researched_note_topic_ids`), none answered | not_met / not_found | ✗ "not found" |
| research note with no researched topic (none yet, its thread failed before answering a target, or `stop` left it unstarted) | pending / to_research | ring "researched next" |
| steering note `honoured` | met / honoured | ✓ "honoured" |
| steering note `ignored_with_evidence` | not_met / ignored_with_evidence | ✗ "not followed" |
| steering note `no_evidence` | not_met / no_evidence | ✗ "no evidence found" |
| steering note, no verdict | not_checked / not_judged | ring "not checked" |

A mixed note's entry carries its research half in `result` and `reason`, exactly as a research note's, and its steering half in `steering: {result, reason}`, exactly as a steering note's (D20). Its row shows both: the fact is "{research words} · {steering words}" (for example "covered · not followed"), and the mark is amber ✗ when either half is `not_met`, ✓ when both are `met`, and a ring otherwise. The notes clause below counts a mixed note as met only when both halves are.

Verdict line and outcome, set on `graph.route.decided` (I). `d` is `material_defects`; `k` is the missing targets; `m` is the criteria met; `n` is the notes listed.

| Route reason | Verdict line | Outcome |
|---|---|---|
| report_accepted | "Accepted · all 5 criteria met" + notes clause | "Accepted · all 5 met" |
| redraft_requested | "{d} thing(s) to fix · sending the draft back to the writer" | "{d} thing(s) to fix · back to the writer" |
| extra_pass_requested | "{k} gap(s) to fill · going back to research" | "Sent back to fill {k} gaps" |
| note_pass_requested | "Going back to research your note(s)" | today's (`web/lib/run-state.ts:150`) |
| note_redraft_requested | "Sending the draft back to the writer for your note(s)" | today's (`:151`) |
| review_unavailable | "The review could not be completed · publishing as partial" | "Review unavailable" |
| report_not_accepted | "Not accepted · {m} of 5 met" when `m < 5`; when `m = 5`, "Not accepted · a check the run makes itself failed" if the latest `graph.quality.assessed` lists any `hard_failures`, else "Not accepted · the reviewer's overall judgement fell short" | same |
| extra_passes_exhausted | "Not accepted · {k} gaps still open" | same |

- The notes clause: " · your note met" / " · your note not met" (`n = 1`); " · both your notes met" (`n = 2`, both met); " · all {n} of your notes met"; otherwise " · {met} of your {n} notes met". It is left out with no notes.
- The outcome gains " · {duration}".
- **Reviewing's subtitle once the review has landed** (*owner-delegated decision, 2026-10-01; O2 item 5*). The subtitle "reading the draft · {elapsed}" above runs while the call runs. Once `graph.report.reviewed` has landed it reads, in the past tense, "read the draft in {elapsed}", frozen at the moment it landed, which no clock moves; with no landing time it reads "read the draft" rather than a time that keeps running. The same words follow the row onto a stopped stage ("Stopped · read the draft in 1m 35s" when the review had landed before the stop; "Stopped · reading the draft · 0m 30s" when it had not). A loop that starts Reviewing again goes back to "reading the draft · {elapsed}".
- **A halted route** (*owner-delegated decision, 2026-10-01; O2 item 6*). `halted` is a route reason (`graph/state.py`, `GRAPH_ROUTES`), and its outcome reads "Halted", never "Review unavailable". The backend does not publish `graph.route.decided` with that reason today: `report_reviewer_node` returns without a decision when the run has already halted, and the errors its own review adds are never halting types (`graph/nodes.py`, `_review_report`). So no screen shows it now (the Failed stage's compact `Spine` shows no outcome at all); the case is explicit so that a stream that ever carries it is worded truthfully. A reason the table does not know keeps the "Review unavailable" fall-back, as `review_unavailable` does.
- **The Reviewing row's static meta (review 2, M-4).** Today's "7 dimensions · accept at mean 0.80" (`web/lib/run-state.ts:22`) names seven criteria and a score, both of which D23 and D11 rule out. It becomes "5 checks" (I), and `rowBrief` (`web/lib/briefs.ts:53-74`) adds " · your notes" (I) while the run holds an interpreted, not-replaced note, so the line is true with and without notes. The compact `Spine` of the Failed and service-stopped stages shows "5 checks" (`web/components/Spine.tsx:24`). `web/test/briefs.test.ts:50` and `web/test/run-state.test.ts` follow.
- What follows the verdict keeps today's loop presentation, apart from the hold D39 adds (spec choice): the spine's seven rows stay fixed, a redraft re-arms the Writing row with its reopen line "Rewriting to fix {n} issue(s) the review found" (`web/lib/run-state.ts:287-289`), and the arc lights as today. The canvas's seventh row, "Writing report · fixing 1 thing" / "only the affected section is rewritten", stands for that hand-off; it is not a new row. As D39 amends it: when the route sends the run back (a redraft, an extra pass, a note pass or a note redraft), Reviewing holds open on its checks, its notes and its verdict line for about 2 s (`HANDOFF_HOLD_MS`) before the hand-over, while the row the run returns to waits closed and pending; the hold paints and never marks, so Reviewing stays unmarked as the loop's reset left it; afterwards the hollow Reviewing row stays reopenable on that review until Reviewing runs again; and a Stop ends the hold.

### 6.8 Researching (D12)

Unchanged; note threads appear as rows (§5.7).

### 6.9 Web state and rendering

- `RunState` gains:
  - `startedAt` and `durations` per row;
  - `planning {step, round, slots, noteSlots}`;
  - `evaluating`, `verifying` (with `samples`, the last two), `writing` (with `samples`), `reviewing {criteria, notes, reason, defects}`.
- Each block resets on its node's `graph.node.started`, so a re-armed row starts clean.
- New handlers: `planner.progress`, `source_evaluator.progress`, `evidence_verifier.progress`, `report_writer.progress`, and `graph.quality.assessed`, which keeps the latest `hard_failures` in `RunState.hardFailures` for Reviewing's refusal line (§6.7). Extended: `graph.node.started` and `graph.node.completed` (timestamps), `graph.report.reviewed`, `graph.route.decided` (Reviewing outcome), `planner.planning.completed` (slot states, note slots).
- Every derivation stays burst-safe (DESIGN §5.7): cumulative snapshots, the latest wins.
- `rowBrief` returns a discriminated `body` per row: `planning`, `research` (today's), `evaluating`, `verifying`, `writing`, `reviewing`, `sentence` (Publishing). `BriefSpine` renders each as `.ln` lines, so row open, close and hand-off (LB-D1, LB-D3) apply unchanged. `SENTENCES` (`web/lib/briefs.ts:25-31`) keeps only Publishing's; the others, "Reviewing the draft on 7 dimensions" included, give way to the new bodies.
- Elapsed values tick from `RunningPipeline`'s existing one-second timer (`web/components/RunningPipeline.tsx:16-22`), passed down.

**CSS** (app-only section, ported from `progress.css` with app selectors; no colour literal):

| Canvas rule | Port |
|---|---|
| `.pb`, `.pb i` (`:9-10`) | as is, fill `transition: transform var(--fill-line) var(--ease-entrance)` |
| `.pb.ind i`, `@keyframes drift` (`:12-14`) | duration `var(--motion-halo)` |
| `.sk`, `.sk::after`, `@keyframes sheen` (`:31-33`) | duration `var(--motion-halo)` |
| `.xf`, `.fi` (`:23-24`, `:34-35`) | as is |
| `.stats`, `.stat` (theme.css `:137-139`) | as is |
| `.tickbox`, `.qt`, `.vd` (`:61-64`) | `.vd b` colour set per verdict (§6.5) |
| `.mk svg.x`, `data-s="fail"` (`:82-88`) | as `[data-topic="fail"]` on `.ps-topics` rows |

**The two loops (D21).** The sheen runs only on skeleton slots while Planning is active (§6.3); the drift runs only while Reviewing's call runs, and the bar is hidden once `graph.report.reviewed` lands (§6.7). Neither exists on any other row or stage.

**Reduced motion** (extends the block at `web/app/globals.css:1320-1333`):
- no sheen (static bar);
- the indeterminate bar becomes a static full-width bar at opacity .35;
- cross-fades are opacity only over 160 ms;
- numbers jump.

### 6.10 Replay

1. **Timestamps.** `ReplayRunner._drain` publishes `event.model_copy(update={"timestamp": <now, ISO>})`, so paced replay shows realistic elapsed times. The engine's own state keeps its times, so `duration_seconds` is unchanged. Nothing else reads a published event's timestamp: no code under `api/`, nor `graph/orchestrator.py`, `graph/live.py` or `cli.py`, reads `timestamp` (a search finds only two comments, `api/sessions.py:521` and `cli.py:1078`), and the web drops it today (`web/lib/run-state.ts:79`, `:382`).
2. **Hold for captures.** `X-Replay-Hold-After: <event_type>[#<n>]` on `POST /research` (replay only; read by `ReplayCaseMiddleware` into a ContextVar like `requested_clarify`; added to the proxy allowlist `web/app/api/[...path]/route.ts:9`). The drain releases events through the n-th event of that type (default 1), then holds until the session is stopped (Phase D) or the server shuts down. Visual captures use it, then `POST /stop`.
3. Fixtures in `web/test/fixtures/events/` are re-captured with `npm run capture:events`.

### 6.11 Documentation (Phase B)

| Doc | Change |
|---|---|
| `DESIGN.md` §3.4 (`:724-741`, `:759-771`) | Each step's brief as §6.3–§6.7; the open-row paragraph's sentences and outcomes (`:724-741`, including "Reviewing the draft on 7 dimensions" and "Accepted · {score}") are rewritten to them, and Reviewing's static meta is "5 checks" (D23). The looping budget becomes: the halo; the skeleton sheen while the plan draft is in flight; the drift on Reviewing's bar while its call runs. Each runs only while its step is active, and both are static under reduced motion (D21). |
| `DESIGN.md` §5.6 (`:1398-1431`) | Motion and reduced motion for the new briefs. The "one decorative loop" rule (`:1426-1430`) gains D21's two exceptions: the sheen on skeleton slots while Planning is active and the drift on Reviewing's bar while its call runs, each static under reduced motion. |
| `DESIGN.md` §5.7 (`:1597-1625`) | The progress events, live-only, and which brief each feeds. |
| `DESIGN.md` §3.5 (`:811-826`) | The reviewer's start is live. |
| `DESIGN.md` §5.7 "Delivery is live" (`:1613-1621`) | The reviewer's `graph.node.started` and `graph.report.reviewed` are published live, so they leave both of the paragraph's snapshot lists ("the reviewer, Publishing and the two hop nodes keep snapshot publication"; "the graph's route, review, hop and completion events"), and the four progress events join the live list (review M12). |

---

## 7. Phase C — the report

### 7.1 The bottom line's reply and request (D13)

**Reply schema** (`utils/types.py:1911-1921`):
- `SectionDraft` gains `short_title: str = ""`.
- `BottomLineDraft` keeps `sentences` (now the direct answer) and gains `topics: list[TopicLineDraft] = []`, where `TopicLineDraft(WriterPointDraft)` adds `topic: str` (a coverage id). Its docstring becomes "The bottom-line call's drafted reply: a direct answer of one or two sentences, then one line per topic (notes-progress-report spec §7.1)."
- The 51 existing `BottomLineDraft(...)` constructions in `src/` and `tests/` stay valid because `topics` has a default. Provider-facing drafts already carry defaulted fields (`WriterPointDraft.disputes`, `utils/types.py:1905`; `SourceScoreDraft.methods_score`, `agents/source_evaluator.py:147`). The new fields travel the same way: the configured DeepSeek provider (`config.yaml:5-6`) prints `model_json_schema()` into its system message and validates the reply with `model_validate_json`, which fills a missing defaulted field (`providers/deepseek_provider.py:150-160`, `:1447-1453`, `:1549`); its Responses path sends the schema without `strict` (`:1955-1966`) and validates the same way (`:2103`); the OpenAI provider's `responses.parse` (`providers/openai_provider.py:617`) already carries `WriterPointDraft.disputes`.

**Prompts** (exact).

`BOTTOM_LINE_SYSTEM_PROMPT` becomes:
> Write the bottom line from the checked statements listed: first a direct answer to the question in one or two sentences, then one line for each topic listed, in the listed order. Each statement was checked against the findings it cites; state nothing they do not.

In `BOTTOM_LINE_INSTRUCTION`, the first rule (`agents/report_writer.py:382`) is replaced by:
> - sentences: one or two sentences that answer the question directly, the most direct answer first. When # Reader answers is listed, give the answer the form those answers ask for — how many options, which area, for what purpose — naming only options, figures and picks the listed statements carry, each credited as its statement credits it; the reader's answers narrow what is answered, never what a statement says.
> - topics: one line per topic listed under # Checked statements, in the listed order, with topic set to the id at the start of that topic's heading. The line states the fact from that topic's own statements that best answers the question; when the answer sentences already state that fact and the topic has another that bears on the question, it states that one instead. It cites only labels that topic's own statements cite. Leave a topic out only when none of its statements bears on the question.

The mechanism rule's "within the two to four sentences" (`:384-385`) and the Outcome block's "within the two to four sentences" (`:1239`) become "within the answer's sentences". Every other rule is unchanged.

`SECTION_INSTRUCTION` gains:
> - short_title names the same part in one to three words for a contents list ("Published picks", "Opening hours"): no number, no judgement, at most 24 characters.

**Reply examples (review I2).** `_BOTTOM_LINE_REPLY_EXAMPLES` (`agents/report_writer.py:437-464`) is replaced by these two, so the `# Reply format` the model reads (`:1212`) shows `topics` beside `sentences`; each label is one line, as `render_structured_reply_format` requires (`agents/prompts.py:76-104`).

Example 1, label:
> Example input: # Reader answers - How many picks do you want? Just one (the reader's answer) # Checked statements ## topic-01 · Noise ratings - Example Tester gives Model A a noise rating of 4.5 out of 5. (cites F01; options: Model A) ## topic-02 · Value for money - Example Register says Model B is the one to beat for the price. (cites F02; options: Model B [picked])

Example 1, payload:
```json
{"sentences":[{"text":"Example Register picks Model B as the one to beat for the price.","finding_labels":["F02"],"items":[{"name":"Model B","verdict":"the one to beat for the price","picked":true,"by":"F02"}]}],"topics":[{"topic":"topic-01","text":"Example Tester rates Model A 4.5 out of 5 for noise.","finding_labels":["F01"],"items":[{"name":"Model A","verdict":"4.5 out of 5 for noise","picked":false,"by":"F01"}]},{"topic":"topic-02","text":"Example Register says Model B is the one to beat for the price.","finding_labels":["F02"],"items":[{"name":"Model B","verdict":"the one to beat for the price","picked":true,"by":"F02"}]}]}
```

Example 2, label:
> Example input: # Checked statements ## topic-01 · Why the program closed - Example Institute reports that a 2018 funding cut reduced the outreach budget. (cites F04) - Example Register states that the reduced budget forced staff reductions through 2019. (cites F05) ## topic-02 · When it closed - Example Register records that the outreach program closed in 2020 after its funding ended. (cites F06) # Outcome - Example Register records that the outreach program closed in 2020 after its funding ended. (cites F06)

Example 2, payload:
```json
{"sentences":[{"text":"According to Example Institute, a 2018 funding cut reduced the outreach budget, and the program closed in 2020 after its funding ended, Example Register records.","finding_labels":["F04","F06"],"items":[]}],"topics":[{"topic":"topic-01","text":"Example Register states that the reduced budget forced staff reductions through 2019.","finding_labels":["F05"],"items":[]},{"topic":"topic-02","text":"The outreach program closed in 2020 after its funding ended, Example Register records.","finding_labels":["F06"],"items":[]}]}
```

Example 1 shows the reader's answer shaping the answer (one pick) and a topic line that repeats the answer because its topic has nothing else; example 2 shows the outcome in the answer and topic-01 moving to its next-best fact because the answer already states its best one. Both examples are added to the example-table test (`tests/test_agents/test_tool_free_prompts.py:530-537`) as `(_BOTTOM_LINE_REPLY_EXAMPLES, BottomLineDraft)`, and a new test checks each payload has at most two `sentences`, at least one `topics` entry, only topic ids that appear as `## {id} · ` headers in its own label, and marks whose `name` and `verdict` are verbatim spans of their own point's text, as the writer's rule 8 requires (`agents/report_writer.py:1903-1929`; review 2, M-1).

**The four-sentence cap (review I4).** `MAX_BOTTOM_LINE_SENTENCES` (4) is replaced by `MAX_ANSWER_SENTENCES = 2` (`agents/report_writer.py:131`):
- the cap before the check (`:2421-2429`) keeps at most two answer candidates, with the refusal reason "over the direct answer's two sentences"; topic lines have no count cap, only the per-topic rules below;
- the fallback's cap (`:2314`) and its docstring's "up to `MAX_BOTTOM_LINE_SENTENCES`" (`:2273`) are removed (§7.3);
- the ruling comment (`:120-122`) says the answer's two sentences are the top of its one-to-two sentence shape;
- the exports (`agents/__init__.py:344`, `:829`) and the writer tests' import (`tests/test_agents/test_report_writer.py:21`) take the new name;
- the missing-outcome re-ask's problem text (`agents/report_writer.py:2740-2743`) becomes, exactly: "The bottom line names no outcome. State the outcome the statements under "Outcome" state, credited and dated as they state it, within the answer's two sentences: fold it into the last answer sentence or replace one, never add a third."
- the writer test that asserts "two to four sentences" (`tests/test_agents/test_report_writer.py:1051`) asserts "one or two sentences".

**Request.**
- `ReportWriterTask` gains `reader_answers: list[ReaderAnswer]` (from `state.reader_answers`).
- `bottom_line_messages` adds `# Reader answers`, then one line per answer, after `# Answer form` when there are any. The lines are exactly the answer lines `render_reader_answers` prints (`agents/planner.py:2697-2703`), without its planning lead sentence ("Plan within these answers…", `:2692-2696`), which would misdirect the writer: the answer-line loop moves into a shared `reader_answer_lines(reader_answers) -> list[str]` in `agents/planner.py`, which both use.
- Each block header becomes `## {coverage_id} · {section.title}` (`agents/report_writer.py:1207`).
- On a redraft, `_rendered_previous_bottom_line` prefixes each point with `answer:` or `{coverage_id}:`.

**Checks** (`_consider_bottom_line_point` and `_check_and_finalize_bottom_line`):

| Draft item | Rule | Refusal reason (project text) |
|---|---|---|
| answer sentence | at most 2 kept candidates; extras refused before the check | "over the direct answer's two sentences" |
| topic line with an unknown `topic` | refused | "a line for a topic the request did not list" |
| second line for one topic | refused | "a second line for one topic" |
| topic line citing a label its own section's kept statements do not cite | refused | "a topic line cites a finding its topic does not" |
| any item | today's rules: labels cited by sections, disputed-label guard, 60 words, 1200 chars | unchanged |

Every kept answer sentence and topic line goes through the Statement Check. The floor filter, the dispute block, the outcome guard and the one re-ask (`agents/report_writer.py:2682-2766`) apply to the whole bottom line; the re-ask's result replaces attempt 1 only when fully checked, as today.

### 7.2 The composition and note lines (D13)

- **`ReportComposition` gains:**
  - `bottom_line: BottomLineLayout | None` = `{answer_ids: [statement id], topic_lines: [{coverage_id, label, statement_id}], assembled: bool}`;
  - `reader_note_lines: list[ReportNoteLine]` = `[{note_id, label, outcome, steering_outcome | None, statement_id | None, text}]`, stamped by the finalizer;
  - `reader_answers: list[str]`: the values of `task.reader_answers` in question order, set by `compose_written_report` (both composition paths, `agents/report_writer.py:2956-2968` and `:3085-3096`).
- **`ReportSection` gains `short_title`.** A drafted short title is kept when it has 1–3 words, ≤ 24 chars, no digit and no verdict word (`agents/report_writer.py:1648-1651`); otherwise the section's title stands in.
- **`summary`** holds the answer points, then the non-note topic lines in plan order, then the note-topic lines in their notes' receipt order (`n1` first) — the order the Markdown prints them — so every consumer of `statements` (reviewer, gates, citations) is unchanged. `_renumber` remaps the layout's ids. Neither new field enters `composition_semantic_fingerprint` (`agents/report_reviewer.py:1001-1026`).
- **No bottom line.** When `_run_bottom_line` returns no point on its early paths (`agents/report_writer.py:2608-2646`), `bottom_line` is `None` and `_bottom_line_block`'s existing sentences apply (`agents/report.py:1244-1273`).
- **Labels.** A topic line's label is the section's `short_title`. A note topic's label is "Your note · {note short}": `ReportWriterTask` gains `note_labels: dict[str, str]` (`note-{id}` → label), built in `build_task` from the active notes.
- **Note short.** The note interpreter gains `short` ("the note's subject in one to three words for a label, lower case", added to `NOTE_INSTRUCTION`; validated at 1–3 words and ≤ 24 chars). The fallback and replay derive it from the restatement's first three words, cut at 24 chars on a word boundary. `ReaderNote` gains `short: str = ""` and derives it when empty.
- **Note lines at publication.** `_terminal_artifacts` (`graph/nodes.py:448-515`) stamps `reader_note_lines`: one per active note, in receipt order, with `outcome = note_outcome(..., terminal=True)` and `steering_outcome = note_steering_outcome(..., terminal=True)` (§5.6).
  - A research note whose `note-{id}` has a kept topic line references it (`statement_id`).
  - A steering note has code text (I): covered "Followed: {restatement}"; not_addressed "Not followed in this report: {restatement}"; not_found "No source we could check covers this: {restatement}"; not_checked "Not checked: {restatement}".
  - A research note without a kept line has code text (I): covered "See the section below."; not_found "No source we could check covers this."; not_checked "Not researched."
  - A mixed note's line is its research half's line (the kept topic line or the code text above), then one sentence for its steering half (I; D20): covered "The rest of your note was followed."; not_addressed "The rest of your note was not followed in this report."; not_found "No source we could check bears on the rest of your note."; not_checked "The rest of your note was not checked."
  - *Implementation ruling (final fix wave, 2026-10-01; the final review's P2-2).* Every active note's line references the kept topic line of its own `note-{id}` topic when there is one, not only a research note's: a steering note keeps its code text after it, and a mixed note its steering-half sentence. A steering note that bought a note pass (judged `no_evidence`) owns such a topic, so its row prints that line and then the steering text in one row, "- **Your note · open now:** ✗ Agency Three reports pastries [2]. No source we could check covers this: leave out cafés that might be closed", and the topic's own row is dropped, never a second row under the same label. Each active note therefore has exactly one bottom-line row. The two sentences are printed as produced; the research's line and the steering outcome are not reconciled (D13: one line per note).
  - *Owner-delegated decision (2026-10-01; O1 and O2 item 1).* Every note the run has read by the time it publishes gets its bottom-line line. A note read after the reviewer's last merge into the state is on the board but not in the state, so `finalize_report` takes the board's notes in at its first merge, before `reader_note_lines` is stamped: such a note is taken in as already settled (`reviewed`, `passed` and `redrafted` set), so it changes no route, no run status and no quality verdict, and its line reads as a note nothing judged (`not_checked`: "Not checked: …" for a steering note, "Not researched." for a research note, §7.2's words). A note that arrives after the run has ended gets no line, and neither does one whose reading has not ended when `finalize_report` takes the notes in. In replay mode the engine finishes before the stream is paced out, so a note added there arrives after the run has ended: the replay report prints no note line and `/status` reads the note `not_checked` (api-gaps 3.9). The rule above (§3.2, §5.9) that replay never applies a note stands.
  - *Implementation ruling, owner-delegated (2026-10-01; O1 fix round 2, P3-1).* A note the run reads only after it has decided to publish gets its line like any other note (its terminal outcome in the existing wording, so "Not checked: …" or "Not researched." when nothing took it up), but it replaces no earlier note whatever it says it replaces: the report already followed that note, so that note keeps its line and its outcome (`finalize_report` takes such a note in with `replaces` cleared, `graph/nodes.py`, `_closed_notes_update`). `/status` agrees: the earlier note reads as it did, and the late one reads `not_checked`.
- **Marks.** ✓ for covered, ✗ for not_found and not_addressed, none for not_checked (D37). A mixed note takes ✗ when either half is not_found or not_addressed, ✓ when both are covered, and no mark otherwise (D20). Replaced notes have no line.

### 7.3 The fallback (D14)

`_bottom_line_fallback` (`agents/report_writer.py:2263-2350`) becomes: for each part outcome with a section, in plan order (note topics included), pick exactly as today — the first kept `consistent`/`corrected` point that meets the floor when any statement does, preferring the part's marked dispute point when the pick cites a disputed label — and move it into the bottom line as that topic's line. There is no cap of four, no answer sentences, and the layout has `assembled = true`. A part with no eligible point has no line. The error messages become "The bottom-line draft failed twice; one checked section point per topic stands in for it." and "Every drafted bottom-line sentence was refused; one checked section point per topic stands in for it." (I).

The move stays a move (review M9): a picked point leaves its section (`agents/report_writer.py:3044-3057`), so a topic whose only kept point is picked loses its section and its card; its line stays in the bottom line under its label. This is accepted: printing the sentence twice is what the move prevents (P1-a). `report_outline` (§7.5) is built from the sections that remain, so "Topic {i} of {N}" counts printed topic sections.

*Implementation ruling (final fix wave, 2026-10-01; the final review's P2-1).* "Its line stays in the bottom line" holds only until a later pass, because a pass that carries parts over (D4) carries them from the previous composition, where the move had already taken each picked point out of its section. So when a note pass or a redraft carries parts over, `compose_written_report` first restores the points an assembled bottom line moved: each topic line's point, looked up in `previous.summary` by statement id and keeping its verdict, goes back to the head of its section (`_with_moved_points_restored`, `agents/report_writer.py`), and only then are the parts carried over, so no checked statement is lost and no model call is made. A section the move emptied is rebuilt under the plan's topic title, with the line's label without "Your note · " as its short title. The new bottom line is drafted afresh. A review defect that names a moved point routes to the part that wrote it, as well as to the bottom line. A drafted bottom line moved nothing, so its previous pass is left as it is.

### 7.4 Key figures (D15)

A new `key_figures_table(composition) -> ReportTable | None` in `agents/report_table.py` replaces `findings_table` inside `build_table`; the options branch is unchanged.

1. **Eligible rows** are exactly today's (`_row_eligible`, `agents/report_table.py:615-632`).
2. **Label.**
   - Item = `row.subject` when set and not starting with a pronoun.
   - Measure, from the planned targets in `row.target_ids` ("plan order" is the order of `composition.sub_topics`, `utils/types.py:1774`, and of each topic's `evidence_targets`):
     1. the row's sub-topic is the one owning the most of those targets, the earlier in plan order on a tie;
     2. the measure is that sub-topic's first such target in plan order whose `unit_dimension` is set, else its first such target in plan order;
     3. a row with no planned target keeps `row.measure`.
   - Why: today's `row.measure` is the first target in sorted id order (`agents/verified_facts.py:1375-1378`), so the latte run's Tripadvisor rows, which answer `topic-01-target-01`, `topic-02-target-02` and `topic-02-target-03`, print topic-01's measure. The rule reads the row by the sub-topic it mostly answers, and prefers a target the planner stamped as a quantity; it does not depend on a stamp being present, because the planner stamps `unit_dimension` only as the model chooses (`agents/planner.py:493-496`, `:2367-2369`).
   - Label = "{Item} · {measure}", or "{Measure}" with no item; plus ", {period}" when the row has a period the label does not already contain.
   - *Amended by D40:* a row with no named item is labelled by the source that reported it, "{Source} · {Measure}" (for example "Tripadvisor · Rating, 2024"), so figures from different findings keep separate rows; items 3–4 (merge, one row per label, the cap) apply unchanged.
   - *Implementation ruling (final fix wave, 2026-10-01; the final review's P3-1).* A row read through a reader note's own topic (`note-{id}`) is not labelled by the measure its target carries, because `reader_notes.note_sub_topic` sets each of a note topic's targets' measure to one of the note's questions in full, or to the note's restatement when the note raised no question. `_key_figure_measure` still picks the target as above, and `_note_topic_measure` (`agents/report_table.py`) then replaces its measure with a short label. A note topic with one target labels its rows by the note's short subject: the topic title without "Your note: ", whitespace normalised, trailing punctuation dropped, and cut at 40 characters on a word boundary (`_short_label`; a hard cut at 40 characters when no space falls within the first 41, with trailing punctuation dropped again after the cut). A note topic with several targets labels each row by its own target's question, trimmed and cut at 40 characters the same way, because rows about one item that answer different targets would otherwise share a label and item 3's one-row-per-label rule would drop all but one; when a cut would make two of the topic's labels collide, its questions are kept whole instead, which still normalises whitespace and drops trailing punctuation. When nothing is left of the label, the target's own measure stands. The target itself keeps its measure; only the label changes.
   - A row whose measure is "stated figure" and has no item is not eligible.
   - The quoted-snippet label is removed.
3. **Merge.**
   - Rows sharing (label, primary finding `row.finding_id`, kind) form a group: the values one passage states about one item. Rows from two passages never merge, so a value is never paired with another listing's.
   - Within a group, a row whose value equals a value already in one of the group's merged rows (compared with `cosmetic_text`) only adds its finding ids to that merged row. Otherwise it goes into the first merged row holding no value with the same **value shape** (the value with each run of digits, dots and commas replaced by `#`, compared with `cosmetic_text`), else starts a new merged row. So K003 "4.2 of 5 bubbles" and K004 "87 reviews", which share their primary finding, merge; "4.7 of 5 bubbles" and "4.6 of 5 bubbles" never share a merged row.
   - Figure = the values joined " · " in row order, each with today's `_result_text` suffixes (mixed kinds, earlier editions).
   - Merged rows that end with the same label keep only the first by the cap's priority (4); the others stay in the evidence log (D36): the table cannot tell such rows apart — the latte run has ten fact rows whose subject is "Starbucks", all from Tripadvisor, with ratings from 3.5 to 4.4 of 5 (K033–K051, K093) — and a repeated label with different figures reads as a contradiction.
4. **Cap.** At most `MAX_KEY_FIGURE_ROWS = 10` merged rows, chosen by the best row's `_select_rows` priority, shown in `_display_order`.
5. **Columns** "What" | "Figure" | "Source". The Source cell text is today's `_who_text` for the group's first row (publisher, relay credit, release or stated date); its `finding_ids` are the union of the rows'. The renderer prints "{text} {markers}" in the Source column (`agents/report.py:1348-1351`).
6. **Caption.** "Showing {k} of {n} verified figures; all are in the evidence log." when truncated (`k` fact rows shown, `n` eligible), plus today's kind caption.
7. The evidence log's "Verified figures" is unchanged.

**Placement (D27).** The one table leaves the bottom line: `## Key figures` for a findings-shape table and `## Options compared` (I) for an options table, both after the topic sections.

### 7.5 Markdown structure (D16)

`render_written_report` (`agents/report.py:1612-1650`) emits, in order:
1. `# {question}`
2. The evidence line, "Evidence as of {date} · {n} sources", plus " · {value}" for each reader answer (I).
3. `## Bottom line`, then the answer paragraph. When `assembled` is set, the paragraph is instead "*Assembled from the sections below; the summary could not be written this time.*" (I). Implementation ruling (final fix wave, 2026-10-01): when the drafted bottom line keeps topic lines but no answer sentence, the paragraph is instead "*The direct answer could not be checked this time; each topic's checked line follows.*", and the writer records a recoverable `report_writer_bottom_line_no_answer` error (agent `agent.report_writer`, message "No direct-answer sentence was kept after the check; the bottom line holds the topic lines alone.", no details). The web mutes the line like the assembled one.
4. A list: "- **{label}:** {line}" for each non-note topic line, then "- **{note label}:** {✓ |✗ }{line}" for each note line (✓ or ✗ only when the outcome has a mark). Until the finalizer stamps `reader_note_lines` — that is, in the writer's own render, which the reviewer reads — each note-topic line prints as "- **Your note · {short}:** {line}" with no mark, and steering notes have no line.
5. One `## {title}` per section with points, in plan order.
6. `## Key figures` or `## Options compared`, then the table and its caption.
7. `## What we couldn't confirm`.
8. `## Sources`.
9. The evidence-log link.

A composition without `bottom_line` (from before this change) renders as today.

**Citation order.** `written_citations` (`agents/report.py:975-1013`) numbers pages as a reader meets them, and today walks bottom line → table → sections. With the table after the topics, it walks bottom line → sections → table.

**Outline for the web.** `report_outline(composition) -> list[ReportOutlineEntry]` in `agents/report.py` returns one entry per `##` heading, in order: `{heading, kind: bottom_line | topic | key_figures | options | not_confirmed | sources, label, topic_index, topic_count, note_id}`.
- Labels: "Bottom line"; each topic's short title, with note topics "{short} (your note)"; "Key figures"; "Options compared"; "Not confirmed"; "Sources".
- `render_written_report` uses the same helper to emit headings, so they cannot disagree.
- `ResearchSessionResponse` gains `report_outline: list[ReportOutlineEntryResponse] | None` via `outcome_response_fields` (`api/sessions.py:156-211`), from `outcome.composition`.

### 7.6 Web layout (D16, `Report.dc.html` B+C)

**Structure** (`web/components/ReportBody.tsx` is rewritten; `ReportStage` and `ReportRail` are unchanged except the props):
```
div.report-col                         (container: report column)
  p.cap#reportEvidence                 the evidence line, lifted out of the Markdown
  div.rep-layout[data-contents=rail|chips]
    nav.rep-contents[aria-label="Report contents"]
    div.rep-cards
      section.card.rsec[data-kind][id=rep-…]  one per outline entry
```

- **Chunks.** The Markdown is split into chunks at lines starting with `## `. The chunk before the first heading holds `# question` (suppressed) and the evidence line. Chunks pair with `status.report_outline` by position; when a heading's text does not match its entry, or there is no outline, every card renders with its heading as the eyebrow and no topic numbering.
- **Rendering.** Each chunk renders through `react-markdown` with `remarkGfm` and `remarkCitationAnchors`. The citation plugin gains a `sourceIds` option, computed first from the Sources chunk's list, so citations in every card link to `#src-n`.
- **Cards.**
  - Bottom line: eyebrow "Bottom line"; `p.lead` (`--text-lg`, 1.6) for the answer; an emphasis-only paragraph as a muted `b-sub` line; `ul.bl-list` rows `span.k` (mark + label, `--text-sm`, `--muted`; ✓ `--status-ok`, ✗ `--status-warn`) and the line. The key column is 132 px; it stacks at ≤ 480 px.
  - Topic cards: eyebrow "Topic {i} of {N}" or "Topic {i} of {N} · from your note", then `h2` title and points.
  - Key figures and Options compared: eyebrow, then the table.
  - What we couldn't confirm: eyebrow.
  - Sources: eyebrow; the list keeps its `src-n` ids; the evidence-log link line stays inside this card.
  - Fixed sections render their heading as `h2.eyebrow`, so each card keeps an `h2`. Every card body sits in a `.prose`.
- **Card box.** `.card` background and border, `--radius-lg`, padding `--space-6` (`--space-4` at ≤ 480 px), gap `--space-4`. `--report-card-w` becomes `calc(var(--reading-max) + 2 * var(--space-6) + 2px)` (`web/app/globals.css:1217`).
- **Contents.**
  - At a report-stage container width ≥ 1310 px (176 + 32 + 770 + 32 + 300), `data-contents="rail"`: a sticky 176 px column left of the cards (`.rail` rules of `progress.css:135-148`; the `aria-current="true"` link `--fg` on `--border-soft`, with `.tn` numbers).
  - Below that — including the 1252 px capture width and every phone — `data-contents="chips"`: a horizontally scrolling row of chips (`progress.css:150-152`) directly above the cards, sticky under the topbar on `--bg` with a `--border` hairline below (D28).
  - **Where the rail appears (review M15).** The report stage is the viewport less the sidebar (296 px expanded, 64 px collapsed) and the two 24 px `.viewport` gutters (`web/app/globals.css:45-47`, `:38`, `:205`). So the rail needs a viewport of at least 1654 px with the sidebar expanded, or 1422 px with it collapsed, plus a classic scrollbar's width where one shows. The 1252 px captures, and a 1568 px window with the sidebar expanded, show chips; `18b-report-cards-1920` shows the rail. Below 1081 px the sidebar is a drawer (`:957-960`) and takes no width.
  - The rail and the chips list the same entries, Sources included (spec choice: the canvas lists stop at "Not confirmed" on desktop and "Key figures" on the phone because its sample has no Sources card; the app's report has one, and every citation jumps to it).
  - The group — contents when shown, cards, the Review rail — is centred as one group.
- **Current section.** The last card whose top has passed `var(--topbar) + 56px + var(--space-4)` below the viewport's top, else the first. Clicking an entry sets it current, scrolls the card to the top (`smooth`; `auto` under reduced motion) with `scroll-margin-top` equal to that offset, and focuses the card's heading (`tabIndex=-1`, no ring). The current chip scrolls into view.
- **Evidence line on a phone.** The web renders the line (`agents/report.py:1068-1074`, plus §7.5's answer parts) as spans: `span.ev-pre` "Evidence as of ", the date, " · {n} sources", then one `span.ev-ans` " · {answer}" per reader answer. At ≤ 480 px `.ev-pre` and `.ev-ans` are hidden, leaving "2026-09-30 · 29 sources" as on the canvas's phone artboard. A line that does not start with "Evidence as of " ("No source could be checked.") renders whole.
- **Key figures on a phone.** At ≤ 480 px the Source column is hidden, and each What cell shows the source text under its label (`.kf-src`, `--text-xs`, `--muted`, hidden above 480 px), so the forecast issuer and release stay visible (D32).
- **Removed.** The "Your notes" block (`web/components/ReportBody.tsx:120-131`) and its CSS (`web/app/globals.css:1404-1410`); note lines now live in the bottom line.
- **Which notes have a line** (*owner-delegated decision, 2026-10-01; O2 item 1*). The bottom line lists the notes the run had read by the time it published (§7.2). A report whose notes all arrived after the run ended, which is every report of a replay session, has no "Your note" row in its bottom line: `web/e2e/notes.spec.ts` and the `12-report-notes` capture show exactly that.
- **Types.** `web/lib/api.ts` `ResearchSessionResponse` gains `report_outline?: ReportOutlineEntry[] | null`, optional because a response recorded before this change carries none.

**CSS** (app-only section, ported from `progress.css` with app selectors; no colour literal):

| Canvas rule | Port |
|---|---|
| `.lead`, `.bl-list`, `.bl-list .k` (`:118-122`) | as is, scoped to `.rsec[data-kind="bottom_line"]`; `.bl-list li` stacks to one column at ≤ 480 px (`:155`) |
| `.cards .rsec` (`:145-146`) | `.rep-cards > .rsec.card`, padding `--space-6`, `scroll-margin-top: calc(var(--topbar) + 56px + var(--space-4))` |
| `.rBC`, `.rail`, `.rail a`, `.rail-h`, `.rmk` (`:135-148`) | `.rep-layout[data-contents="rail"]` grid `176px minmax(0,1fr)`, gap `--space-8`, inside `@container report (min-width:1310px)`, with `#stage-report{container:report / inline-size}` |
| `.jump`, `.jump a` (`:150-152`) | `.rep-layout[data-contents="chips"] .rep-contents`, `position:sticky; top:var(--topbar)` |
| `.rtab` (`:123-126`) | the key-figures table keeps the app's `.tbl` styles; only `.kf-src` is new |
| the phone artboard's short header (`Report.dc.html:93`) | `#reportEvidence .ev-pre, #reportEvidence .ev-ans { display:none }` at ≤ 480 px |

### 7.7 Replay double

- `_reply_SectionDraft` (`e2e_evaluation/replay.py:1378-1437`) sets `short_title` to the title's first two words.
- `_reply_BottomLineDraft` (`:1441-1466`) reads the `## {coverage_id} · {title}` blocks. It returns the first statement of the first block as the one answer sentence, and the first statement of each block as that topic's line.

### 7.8 Documentation (Phase C)

| Doc | Change |
|---|---|
| `DESIGN.md` §3 inventory row 4 (`:161`) and `:219-224` | The report as cards with contents; notes in the bottom line; no "Your notes" block. |
| `DESIGN.md` §5.6 | Contents scrolling under reduced motion. |
| `docs/superpowers/specs/2026-09-25-consumer-report-format.md` | A superseding note at the top pointing to §7 of this spec. |

---

## 8. Phase D — Stop

### 8.1 API

`POST /research/{session_id}/stop` takes no body.

| Response | When |
|---|---|
| **202** with `ResearchSessionResponse` (`status: "stopped"`) | the session is `running` or `needs_input`, not finished, not publishing |
| **409** `not_stoppable` (`reason`: `finished` \| `publishing` \| `closing`) | terminal or `finished_at` set; `notes_closed` (the route decided finalize or end); the store closing |
| **404** `session_not_found` | unknown id |

`_SAFE_MESSAGES["not_stoppable"]` is "Research session can no longer be stopped." A second stop gets 409 `finished`.

### 8.2 `SessionStore.stop`

It runs synchronously in the route and gives up control only after step 5:
1. `require`; raise `NotStoppable` for the 409 cases.
2. `step` = `"check"` if the status is `needs_input`, else `active_row(session.events)`: start `planner`; a `graph.node.completed` for `planner` … `report_writer` moves to the next row; a `graph.route.decided` moves to its destination's row (`extra_pass` and `note_pass` → `researcher`; `redraft` → `report_writer`). These are DESIGN §3.5's rules (`docs/design/DESIGN.md:819-826`).
3. `session.publish(session_stopped_event(step, now, elapsed))`, with metadata `{step, stopped_at (ISO seconds), elapsed_seconds (int, now − started_at)}`, source `api`, message "The reader stopped the research."
4. Set `status = "stopped"`, `stopped_step = step`, `finished_at = now`, `current_agent = None`. Cancel a pending clarification future and clear it.
5. `task.cancel()` for the session task and every note task.

**`ResearchSession.publish`** drops every event once `status == "stopped"`, so `session.stopped` is the last event (a replay drain may wake before its own cancellation).

**`_run`'s `finally`** (`api/sessions.py:571-577`) sets `finished_at` only when it is `None`. Cancellation still propagates unchanged.

### 8.3 What stops

- The run task is cancelled at its current await.
- LangGraph cancels and awaits its node tasks (§3.10).
- The researcher's `finally` cancels its threads (§5.3); `gather`-based fan-outs cancel their children.
- `AsyncOpenAI` and `httpx` requests are closed.
- A Tavily search is cancelled with its task, once the search tool uses the async client (below).
- Memory queries run Chroma and the embedder in worker threads (§3.10). With the default local embedder they make no request. With the non-default `embedding_provider: openai`, an embedding request already in a thread finishes there and its result is discarded; this spec leaves that configuration as it is, as stated with the switch the human approved (D25).
- A page fetch in flight is cancelled with its tool call. The approved latency work (D38) fetches once per URL and shares the result between loops; a fetch task it shares must belong to the run and be cancelled with it, never left to finish on its own (the audit notes Stop must then cancel more in-flight fetches, `docs/superpowers/audits/2026-09-30-latency-audit.md:227`). Whichever of the two lands second adds a shared fetch in flight to `test_stop_cancels_inflight_calls`.
- Nothing more starts. `finalize_report` never runs, so no file and no memory entry is written.

[INFERENCE] every in-flight fake provider call observes `CancelledError` within 1 s. Test `test_stop_cancels_inflight_calls`: a stub agent awaits an `asyncio.Event` forever and records cancellation; after `stop`, it is cancelled and no later node starts.

**Making the search cancellable (D17: "all in-flight provider and HTTP calls"; D25).** Today a search runs tavily's synchronous client in a worker thread, which no cancellation can stop (§3.10). Phase D changes `tools/web_search.py`:
1. A second protocol, `AsyncSearchClient`, has `async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]`. The `client` parameter accepts `SearchClient | AsyncSearchClient | None`.
2. The default client (when `client` is `None`) becomes `AsyncTavilyClient(api_key=api_key)`, imported from `tavily`, in place of `TavilyClient(api_key=api_key)` (`:97`).
3. In `_search_with_retries`, the awaited call is `self._client.search(query=…, search_depth=…, max_results=…)` itself when `inspect.iscoroutinefunction(self._client.search)` is true, and `asyncio.to_thread(search_once)` otherwise, still inside `asyncio.wait_for(..., timeout=self._timeout_s)`. The reservation stays before the call, outside the awaited call, exactly where it is (`:160-161`); the comment at `:144-159` is rewritten to cover both paths.
4. Injected synchronous clients — the replay double, the evaluation harness and the test fakes (§3.10) — are unchanged and keep running in `asyncio.to_thread`; they return at once, so a stop never waits on one.
5. Effect on errors: a Tavily reply with a status tavily does not map (it maps 400, 401, 403, 429, 432 and 433) now raises `httpx.HTTPStatusError`, so a 5xx is retried under the tool's own `_is_retryable` (each retry reserving a budget unit, as designed), where today's `requests.HTTPError` was not retried. Every other error behaves as today. §12 R9.
6. The client is not closed at the end of a run, like the provider's `AsyncOpenAI` client today: a search for `aclose` and `close()` in `runtime/`, `main.py` and `providers/deepseek_provider.py` finds none.

Tests: `test_default_search_client_is_async` (a tool built with an API key and no client holds an `AsyncTavilyClient`); `test_search_cancelled_with_task` (an async fake whose `search` awaits an `asyncio.Event` observes `CancelledError` when the awaiting task is cancelled, and the tool reserved exactly one unit); `test_sync_search_client_runs_in_thread` (a sync fake still works); `test_search_5xx_retried` (an async fake raising `httpx.HTTPStatusError` 503 twice, then answering, yields a result after three reserved units). In `tests/test_evaluation/test_dependencies_controlled.py`, the controlled-mode tests patch `TavilyClient` (`:490`) and check `isinstance(search._client, TavilyClient)` (`:510`, `:520`); they patch `AsyncTavilyClient` instead and check that the injected client is neither class.

### 8.4 The status everywhere

| Place | Change |
|---|---|
| `api/models.py:35-42` | `SessionStatus` gains `stopped`; `ResearchSessionResponse` gains `stopped_step: str \| None = None`; `TraceMetadata.status` follows. |
| `api/sessions.py:54-56` | `TERMINAL_STATUSES` gains `stopped`, so `iter_events` ends after `session.stopped`, notes (`status != running`) and answers (`status != needs_input`) are refused, and outcomes are terminal. |
| `api/app.py:396-451` | `/report` → 409 `report_unavailable` and `/evidence` (both formats) → 409 `evidence_unavailable` when `status == "stopped"`, before the outcome checks: the codes a graph-halted failed run returns (D26). `/status`, `/trace` and `GET /research` answer normally. |
| `web/lib/api.ts:3` | `SessionStatus` gains `stopped`; the response gains `stopped_step`. |
| `web/lib/format.ts:6-36`, `:63-73` | `SessionView` gains `stoppedStep` (from `status.stopped_step` in `toSessionView`); `STATUS.stopped = {label: "Stopped by you", dot: "dot-neutral"}`; `statusNote` → "at {step label}" (§4.3); `isLive` unchanged (false). |
| `web/components/ReportStage.tsx`, `ReportRail.tsx` | Never rendered for `stopped`: `SessionScreen` routes it to `UserStoppedStage` (below). No change. |
| `web/components/Sidebar.tsx:48-58` | A stopped row has no running mark, opens the stopped stage (never Failed), and its accessible name ends " — stopped by you" (I); no visible status word (DESIGN §3.1; D29). |
| `web/lib/run-state.ts` | `RunState.stopped: {step, at, elapsedSeconds} \| null`; handler `session.stopped` sets it, `active = null`, `loop = "off"`, `arc = null`. |
| `web/components/SessionScreen.tsx:194-205` | `status === "stopped"` → `UserStoppedStage`, before the Failed and Report branches. |
| e2e `support.ts` `waitTerminal`; `web/scripts/capture-replay-events.mjs:12` | the terminal regexes gain `stopped`. |

**The stored session keeps** its events up to and including `session.stopped`, its errors (a stop adds none), notes (all `not_checked`, §4.2), the check record, `stopped_step`, `iteration` and `finished_at`. It keeps no outcome, report path or trace URL.

### 8.5 Web (D18, `Stop.dc.html`)

**Files.** New `web/components/StopControl.tsx` and `web/components/UserStoppedStage.tsx`; `web/lib/api.ts` gains `stopResearch(sessionId): Promise<ApiResult<ResearchSessionResponse>>` (one POST to `/api/research/{id}/stop`, never retried, the file's own convention, `web/lib/api.ts:86-89`). The proxy needs no change: it already forwards POST (`web/app/api/[...path]/route.ts`).

**Stop control.**
- `ConsoleProvider` gains `stop: {sessionId, onStopped(response)} | null` and `setStop`.
- `SessionScreen` sets it while `isLive(status)`, `finished_at === null`, and the stream shows neither Publishing active nor a finished graph (`chipStep(run) !== "finalize_report"` and `run.finalStatus === null`; from `/status`, `current_agent !== "finalize_report"`).
- `Topbar` renders `StopControl` after the status chip and before the replay chip.
- The button: `.btn.btn-ghost.btn-sm.btn-stop#stopBtn` with `span.stop-sq` (8 px `currentColor` square) and "Stop"; `aria-haspopup="dialog"`, `aria-expanded`; it keeps its label at every width.

**Confirm popover.**
- `div.stop-confirm[role=dialog][aria-labelledby=stopConfirmT]`, anchored below and right-aligned, 320 px, `--surface`, `--border`, `--radius-lg`, padding `--space-4`; it enters with `enter` over `--motion-base` (a fade only under reduced motion).
- Content: `p.confirm-t#stopConfirmT` "Stop this research?" (I); `p.b-sub` "It stops right away and nothing more is spent. What's done so far stays here, but no report is written." (the canvas copy, D24); "Keep going" (`.btn.btn-quiet.btn-sm`, focused on open); "Stop research" (`.btn.btn-sm.btn-danger`: `--status-danger` text, `--border` edge, danger edge on hover).
- Escape, an outside click or "Keep going" closes it and returns focus to Stop.
- *Owner-delegated decision (2026-10-01; O2 item 8, Phase D minor).* When the screen withdraws Stop for a reason other than the reader's stop (Publishing, finished, failed) while Stop or the popover holds focus, focus would fall to `<body>`. It goes to the status chip in the topbar (the element reading "Running · …" or its successor), which takes focus by script only (`tabIndex={-1}`, never a tab stop) and keeps the app's own focus style. `Topbar` passes the chip's ref to `StopControl`, which hands focus on in a layout cleanup, while the control is still in the document. A stop by the reader keeps its own hand-over (the stopped stage's first line takes focus, §8.5, before the control unmounts), and focus that was elsewhere is left where it was.
- "Stop research" disables both buttons and POSTs once:
  - 202 closes the popover, calls `onStopped` (the screen adopts the response's status at once) and `refreshSessions()`;
  - 409 replaces the body with "Too late to stop — the research is finishing." (I) and a "Close" button;
  - any other failure shows "Couldn't stop — try again" (I) and re-enables the buttons.

**Stopped stage** `#stage-user-stopped` (the service-stopped `#stage-stopped` is unchanged):
- `.ask-head`: eyebrow "Stopped by you" (I), the question as `h1.ask-q.ask-locked#user-stopped-h`, the settings strip.
- `div.stopped-note[role=status]` (`progress.css:171`): `p.b-now` "You stopped this research at {HH:MM}, {N} minutes in." (I), local 24-hour time; `N` from `elapsed_seconds` — "less than a minute in" under 60 s, "1 minute in". At step `check`: "You stopped this research at {HH:MM}, before it started." (I). Then `p.b-sub` "No report was written. The plan and what research found so far are kept below until the service restarts." (I), and "Ask again" (`.btn.btn-ghost.btn-sm#askAgain`), which POSTs `buildRequest(status.query, submission?.settings ?? DEFAULT_SETTINGS)`, records the submission and navigates to the new session.
- The pipeline card (omitted at step `check`, D33): `BriefSpine` with a new optional prop `frozen?: NodeId` (today's props are `marks`, `run` and `onToggle`, `web/components/BriefSpine.tsx:17`; review M1), passed the stopped step:
  - done and loop rows as recorded, openable;
  - the stopped row `data-state="stopped"` (node border `--muted`, `--fg` digit, `--surface` fill, name `--fg`, no halo), with subtitle "Stopped · {its live facts}" (I) — Researching's live facts line (e.g. "Stopped · 3 of 5 topics done · 41 pages read · 212 findings") and, once Phase B has landed, the step's live subtitle from §6.3–§6.7; a row with no live facts (every row but Researching before Phase B) reads "Stopped". It is openable to its frozen brief (running topics read "stopped" with a ring);
  - later rows `data-state="off"` (`--meta`), "not run", or "not run again" for rows a loop had re-armed (I);
  - no arcs, no hand-off, no note line.

**CSS** (app-only section, ported from `progress.css:159-171`; no colour literal): `.btn-stop`, `.stop-sq`, `.btn-danger` (and its hover), `.stop-confirm` (from `.confirm`, `.confirm-t`, `.confirm-btns`), `.stopped-note`, the rows `.spine-lg.briefs > li[data-state="stopped"]` and `[data-state="off"]`, and `.chip .dot-neutral{background:var(--muted)}`. Reduced motion: the popover fades without rising (the existing `enter` redefinition, `web/app/globals.css:1003-1005`).

### 8.6 Documentation (Phase D)

| Doc | Change |
|---|---|
| `DESIGN.md` §4 status table (`:969-983`) | A seventh interface status: **Stopped by you** · `stopped` · `stopped_step` · `--fg` label, neutral `--muted` dot · "Stopped by you · at {step}". The count of statuses in the heading text becomes seven. |
| `DESIGN.md` §3 inventory (`:155-163`) | A stage for a stopped session: the stopped note, the frozen pipeline, "Ask again". |
| `DESIGN.md` §3.1 sidebar (`:298-330`) | A stopped row's accessible name ends "— stopped by you"; still no visible status word. |
| `api-gaps.md` existing surface (`:20-52`) and "Gaps the front end should not close" (`:164-166`) | Add `POST /research/{id}/stop`; the cancel gap is closed by it: no partial artifact is written. |
| `README.md` / `web/README.md` "Run the app" | Mention Stop and `X-Replay-Hold-After`. |

---

## 9. Implementation order

1. **D — Stop.** The smallest phase: nothing a run researches or writes changes, and its one engine-side change is the search tool's transport (§8.3). It is the most urgent control (the live run could not be stopped once credit ran out), and it gives Phase B's visual captures their end-of-hold (§6.10).
2. **A — Notes.** It fixes the correctness failure the run exposed (E3–E8) and defines what B and C display: research notes, mixed notes, note threads and terminal outcomes.
3. **C — Report.** It changes the writer's bottom line and the table, and uses A's note helpers and outcomes (§4 item 5). B's Writing progress counts parts and the bottom line, so C comes first to avoid re-capturing fixtures twice.
4. **B — Progress.** The largest web surface. It builds on A's threads and outcomes and C's bottom-line phase, and it re-captures the replay fixtures once.

Each phase stays independently shippable. §4 lists what a phase implements when an earlier one has not landed. The latency workstream (D38) is independent of all four; §8.3 names the one test whichever of it and Phase D lands second extends.

---

## 10. Acceptance criteria

**Phase A**
- **AC1.** A research note interpreted before the planner's run returns is in `planning.completed.metadata.sub_topics` as `{coverage_id: "note-{id}", title: "Your note: {restatement}", note_id}`, and in `state.sub_topics` with one required target per new question. The plan and plan-review requests carry the new `PLANNING_NOTES` text.
- **AC2.** With 10 planned topics, `max_sub_topics = 10` and one note topic, the first researcher run researches 11 topics and records no `cap` skip.
- **AC3.** In a stub-agent test with `sub_topic_concurrency = 1` and a planned loop blocked, a research note added to the board starts its loop within one scheduler turn: its `researcher.sub_topic.started` (with `note_id`) is published before the blocked loop completes. `graph.node.completed` for the researcher follows that loop's `researcher.sub_topic.completed`.
- **AC4.** A note received while every loop has finished and interpreted within 30 s gets a thread in the same run. One interpreted after the window closed is returned by `notes_due_a_pass` after the review.
- **AC5.** `notes_due_a_pass` returns a research note without a researched topic whatever the review's status (`scored`, `incomplete`, `provider_failed`), and never one whose topic was researched (AC35 covers a failed thread). `notes_due_a_redraft` never returns a note whose only kind is new_angle, and returns a mixed note on `ignored_with_evidence` or when no review input carried it.
- **AC6.** After a note pass, the writer makes one section call per `note-*` part the pass researched, one per part with no previous section (P2-1, `agents/report_writer.py:2190-2193`; review M7), and one bottom-line call. Every other section is byte-identical to the previous composition's, with the same verdicts; the reviewer runs a full review.
- **AC7.** A steering note, or a mixed note's steering half, judged `ignored_with_evidence` buys exactly one note redraft that drafts every part (D22) and leaves `writer_redrafts` unchanged.
- **AC8.** The review packet's `reader_notes` hold the steering notes and the mixed notes, each mixed note without `new_angle` in its kinds. A disposition naming a note whose only kind is new_angle is dropped; a mixed note's disposition is kept.
- **AC9.** `note_outcome` and `note_steering_outcome` follow §5.6 row by row. No session in a terminal status (including `stopped`) reports `pending` for any note, in either field.
- **AC10.** Both worst cases a run's ten notes allow (`MAX_NOTES_PER_RUN`, `utils/types.py:1145`; the board refuses an eleventh, `runtime/notes.py:84-85`) complete under the unchanged `graph_recursion_limit` (`graph/state.py:432-448`), in stub graphs with `max_extra_passes` at its configured value (review I1):
  1. ten steering notes, each judged `no_evidence` (it buys its pass) and then `ignored_with_evidence` (it buys its redraft);
  2. ten mixed notes, each buying its pass (its new_angle half) and then its redraft (its steering half judged `ignored_with_evidence`).

  In both, each note arrives during a different Writing step, so no two notes share a pass or a redraft: ten passes and ten redrafts, the most the formula allows for.
- **AC11.** No evidence-verifier request contains a note (LB AC16 still passes).
- **AC12.** Web: the research-note ack reads per §5.7 in each step, with "now" only after the thread's `researcher.sub_topic.started`; the thread appears in the Researching checklist; the report reads "not checked" for `not_checked`, and a mixed note's caption shows both outcomes.
- **AC34.** A mixed note, kinds `new_angle` and `exclude` (review C1, D20):
  - it gets its own `note-{id}` topic, at planning or during research, like any research note;
  - every research turn, extraction, evaluator, writer and review request prints it with `(exclude)` and without `new_angle`; the planner's requests print both kinds;
  - the review's disposition on it is kept; `ignored_with_evidence` buys its redraft, after its pass when it arrived late;
  - `note_outcome` follows its targets and `note_steering_outcome` its disposition;
  - Reviewing's row and the bottom-line note line show both results, with the combined mark of §6.7 and §7.2.
- **AC35.** A note thread that ends in a provider failure sets `stop`, and no later note thread starts in that run (review I5; review 2, I-1 and M-2).
  - If the thread answered none of its targets, then after the review `notes_due_a_pass` returns its note once. The note pass reuses its `note-{id}` topic (no second topic with that id), confines the researcher to its targets, and marks the note `passed`, so no second pass follows.
  - If the thread answered a target, the note reads `covered` and owes no pass.
  - A planning-time note topic that `stop` leaves unstarted has no completed event; its note is owed its pass exactly as above, and the pass reuses the topic.
  - When every topic of that pass has spent its acquisition budget and owes no extraction, the pass records `researcher_extra_pass_unfunded` and opens no loop; the note stays `passed` and reads `not_found`.

**Phase B**
- **AC13.** Each progress type carries exactly the §6.1 keys, is absent from `state.events` and from `node.completed.event_count`, and every string value is a title, a finding content, a drafted sentence, a host, a page-word correction value or an enumerated value (§4 item 1). The two builder docstrings say the no-provider-text rule binds state-bound events. CLI plain and verbose outputs are unchanged (`is_streamed_event` is false for the four new types).
- **AC14.** Planning: slots go skeleton → drafted → checking → passed/fixed/flagged per §6.3 for scripted runs covering: sound on round 1; a local repair; a review repair then sound; a failed review. The status line shows the §6.3 text for each step. A planning-time research note shows "joins the plan", then "from your note". The subtitle ticks; the outcome includes the note clause and duration.
- **AC15.** Evaluating: the stats read "not yet" before the first batch, then counts that sum to `rated` with the §6.2 thresholds. The bar reaches 1 exactly when every batch has settled. The outcome follows §6.4.
- **AC16.** Verifying: the first event arrives before any Context Check call, and its `checked` equals the number of findings Figure Match decided (review M6); the ticker shows real findings with §6.5 words at the §6.5 pace; dropped is amber. The tally equals the completed event's counts at the end, whatever the batch size, and a batch reported twice (its two halves) counts each finding once.
- **AC17.** Writing: samples show real drafted sentences with ✓/✗ from Statement Check verdicts; `fraction` never decreases; the subtitle switches to "writing the bottom line".
- **AC18.** Reviewing: no number from a review score appears in the brief. Exactly five criteria rows show, in §6.7's order, following §6.2's mapping (D23); notes follow §6.7, a mixed note with both results and an unnamed research note with "researched next"; the verdict line and outcome follow the route table, including "Accepted · all 5 met" and the `m = 5` refusal line.
- **AC19.** Replaying each captured fixture from event 1 paints the same final briefs as the live stream (burst safety).
- **AC20.** Motion: token durations only (the ticker's hold is a dwell, not an animation). The sheen exists only on skeleton slots while Planning is the active row, and the drift only while Reviewing's call runs (D21). Under `reducedMotion: "reduce"` there is no sheen or drift animation and no transform transition in any new brief element.
- **AC21.** Replay: published timestamps are the release times; `X-Replay-Hold-After` holds at the named event until `POST /stop`; e2e at 1252 and 390 px has no horizontal scroll.

**Phase C**
- **AC22.** The bottom-line request carries `# Reader answers` (the answer lines only, without the planner's lead sentence) when there are answers and `## {coverage_id} · {title}` headers; the reply's answer (≤ 2, a third refused as "over the direct answer's two sentences") and topic lines pass the §7.1 rules and the Statement Check; the missing-outcome re-ask carries §7.1's exact text.
- **AC36.** The request's `# Reply format` shows §7.1's two examples. Both validate as `BottomLineDraft`; each has at most two `sentences`, at least one `topics` entry, only topic ids that appear as `## {id} · ` headers in its own label (review I2), and marks that are verbatim spans of their own point's text (review 2, M-1). No name `MAX_BOTTOM_LINE_SENTENCES` remains in `src/` or `tests/` (review I4).
- **AC23.** The published Markdown's `## Bottom line` holds the answer paragraph and a list with one line per kept topic line (label = short title) and one per active note (label "Your note · {short}", mark per outcome; a mixed note's line carries both results and the combined mark, D20).
- **AC24.** With the bottom-line call failing twice, the bottom line is the assembled label plus one checked line per topic that has one, with floor and dispute protections as today. A topic whose only kept point is picked has no section and no card, and `report_outline` numbers the topic sections that remain (review M9).
- **AC25.** Given the latte run's fact rows, the label-and-merge step (before eligibility and the cap) yields the merged row "Bijan Bakery · aggregate customer rating" | "4.2 of 5 bubbles · 87 reviews", no merged row holds values from two primary findings, and no label is a quoted snippet. With every row treated as eligible, the printed Key figures section comes after the topics, has at most 10 rows, no two with the same label, and the What / Figure / Source columns.
  - **Fixture.** `output/` is git-ignored (`.gitignore:224`), so the plan copies all 140 fact rows and the findings they name (id, label, source URL, verification) from the local `output/report-a02a75fd75d44d8481f34953a4ff52e1-0-quality.json` into a checked-in fixture `tests/fixtures/latte-key-figures.json`.
  - **Targets.** The plan is in no artifact, so the fixture declares the nine targets the rows cite, each with `unit_dimension` null because no artifact records it. Seven measures are observed: each is the measure the quality record prints for the rows whose first sorted target it is, as `agents/verified_facts.py:1378` picks it (for example `topic-01-target-01` "cafés named in published best-latte or best-coffee guides" and `topic-02-target-02` "aggregate customer rating"). `topic-01-target-02` and `topic-02-target-03` are first for no row, so their measures are declared "not recorded"; the label rule never chooses either, because each follows another target of its sub-topic that the same rows cite.
  - **Why this label.** K003 and K004 cite `topic-01-target-01`, `topic-02-target-02` and `topic-02-target-03`, two of them in topic-02, and share their primary finding.
  - Eligibility reads each finding's own target binding (`agents/report_table.py:591-602`), which the quality record does not keep, so it is left to the existing table tests; the `unit_dimension` preference is covered by `test_key_figures_measure_rule`.
- **AC26.** Markdown heading order is §7.5's, and `report_outline` matches the headings one to one.
- **AC27.** Web: each section is its own card; the contents rail shows at ≥ 1310 px container width and chips below, so the 1920 px capture shows the rail and the 1252 px capture chips, as does a 1568 px window with the sidebar expanded (D28); the current entry is marked while scrolling; no "Your notes" block; the Review rail is present; at 390 px the evidence line reads "{date} · {n} sources" only; no horizontal scroll at 1252 or 390 px.

**Phase D**
- **AC28.** `POST /stop` on a `running` and on a `needs_input` session returns 202 with `status: "stopped"`. `session.stopped` is the last event, the stream closes, and `/status` shows `stopped_step` and `finished_at`.
- **AC29.** An in-flight stub call is cancelled within 1 s; no later node starts; no file is written under the output directory; no memory write occurs. A search in flight through an async search client observes `CancelledError` when the session is stopped, and a tool built with no client holds an `AsyncTavilyClient`.
- **AC30.** 409 `not_stoppable` for completed, failed, a second stop, after `graph.route.decided` to finalize, and during store close; 404 for an unknown id.
- **AC31.** `/report` → 409 `report_unavailable`; `/evidence` (JSON and Markdown) → 409 `evidence_unavailable`; notes → 409 `notes_closed`; answers → 409 `not_waiting_for_input`.
- **AC32.** Replay: stopping mid-stream records a `step` equal to the web's active row at that moment.
- **AC33.** Web: Stop shows from the check through Reviewing and not while Publishing; the popover follows §8.5 (focus, Escape, 409, failure) and reads the canvas copy (D24); the stopped stage shows the chip "Stopped by you · at {step}" with a neutral dot, the card, openable finished rows, the stopped row's facts and "not run" rows; the sidebar row is labelled stopped; "Ask again" starts a new session with the same question.

---

## 11. Test plan

### 11.1 New tests

| Phase | Layer | Tests |
|---|---|---|
| A | pytest | `test_planner_appends_research_notes` (AC1); `test_planning_notes_text`; `test_researcher_note_topics_uncapped` (AC2); `test_research_note_thread_starts_ungated` (AC3); `test_late_note_waits_then_threads`, `test_note_after_window_owes_pass` (AC4); `test_notes_due_a_pass_research_notes_any_review_status`, `test_research_notes_never_redraft` (AC5); `test_writer_carries_parts_after_note_pass` (AC6); `test_note_redraft_unchanged` (AC7); `test_review_packet_steering_notes_only` (AC8); `test_note_outcome_table`, `test_terminal_sessions_never_pending` (AC9); `test_note_steering_outcome_table` (AC9); `test_recursion_limit_steering_notes_pass_then_redraft`, `test_recursion_limit_mixed_notes_during_writing` (AC10); the existing verifier-never-sees-notes test (AC11); `test_dispatcher_cancels_threads_on_cancel`; `test_steering_views_per_request` (§5.1 table); `test_mixed_kind_note_steers_and_researches` (AC34); `test_failed_note_thread_owes_one_pass` (AC35: a failed thread, an unstarted planning-time topic, a thread that answered a target, and an unfunded refusal); `test_note_thread_provider_failure_sets_stop`, `test_late_note_wait_times_out_and_closes_window`, `test_replaced_note_before_topic_gets_none`, `test_replaced_note_after_topic_reads_replaced`, `test_planner_appends_nothing_without_a_plan` (§5.2, §5.3, §5.8; review M10, M14) |
| A | Vitest | `notes.test.ts`: research-note ack text per step, "now" only after the thread starts; `run-state.test.ts`: `NoteState.kinds`, `threadStarted`; `reader-notes`/report outcome text `not_checked`, `pending` "not checked yet", a mixed note's two-outcome caption |
| B | pytest | per agent: progress keys, order and counts on scripted runs (`test_planner_progress_states` covering the four AC14 flows; `test_evaluator_progress_split`; `test_verifier_progress_samples`; `test_writer_progress_fraction_monotonic`); `test_progress_events_live_only` (AC13); `test_verifier_first_event_counts_figure_match` (AC16); `test_progress_counts_idempotent` (a batch reported as two halves, and batch size 2; AC16); `test_reviewed_event_five_criteria_mapping` (AC18); `test_reviewed_event_mixed_note_steering` (AC18); `test_reviewer_started_live`; `test_cli_unchanged_for_progress_types`; `test_replay_restamps_and_holds` (AC21) |
| B | Vitest | handlers and burst safety over re-captured fixtures (AC19); brief bodies for each step (AC14–AC18), including the five criteria, a mixed note's row, an unnamed research note and the `m = 5` refusal line; the ticker's hold, with fake timers; elapsed formatting; `brief-spine.test.tsx` rendering of each body. Planning note slots use synthesized event sequences, because a replay run never applies a note (api-gaps 3.9) and its interpreter never returns `new_angle` (`api/notes.py:267-275`). |
| B | Playwright (replay) | `progress.spec.ts`: each step's brief appears with the §6 copy, using `X-Replay-Hold-After` at `planner.progress`, `source_evaluator.progress#2`, `evidence_verifier.progress#2`, `report_writer.progress#3` and `graph.report.reviewed`, then `POST /stop`; reduced motion (AC20) |
| C | pytest | `test_bottom_line_request_reader_answers_and_ids` (AC22); `test_answer_overflow_refused`, `test_missing_outcome_reask_text` (AC22); `test_bottom_line_examples_valid_and_name_topics` (AC36); `test_bottom_line_topic_line_rules`; `test_mixed_note_line_both_results` (AC23); `test_fallback_move_drops_emptied_section` (AC24); `test_bottom_line_layout_and_markdown` (AC23); `test_note_lines_stamped_at_publication`; `test_fallback_one_line_per_topic` (AC24); `test_key_figures_labels_and_merge_latte` (AC25); `test_key_figures_measure_rule` (the majority sub-topic, the tie in plan order, the `unit_dimension` preference inside that sub-topic, a row with no planned target); `test_markdown_heading_order`, `test_report_outline_matches_headings` (AC26); `test_fingerprint_ignores_note_lines`; replay double tests for the new shapes |
| C | Vitest | `report-body.test.tsx`: chunking, outline pairing, fallback without outline, citation anchors across cards, bottom-line list parsing, key-figures phone cells, evidence-line spans (and the whole "No source could be checked." line) |
| C | Playwright (replay) | `report.spec.ts`/`report-layout.spec.ts`: cards, contents (1920 rail, 1252 and 390 chips), current marking on scroll, click-to-jump, no horizontal scroll (AC27) |
| D | pytest | `test_stop_route_codes` (AC28, AC30, AC31); `test_stop_cancels_inflight_calls` (AC29); `test_default_search_client_is_async`, `test_search_cancelled_with_task`, `test_sync_search_client_runs_in_thread`, `test_search_5xx_retried` (AC29, §8.3); `test_stop_during_needs_input`; `test_publish_after_stop_dropped`; `test_active_row_matches_web_rule` over the fixtures (AC32); `test_replay_stop_mid_stream` |
| D | Vitest | `format.test.ts` (chip), `status-chip.test.tsx`, `sidebar.test.tsx` (label), `run-state.test.ts` (`session.stopped`), `session-screen.test.tsx` (stage choice), `stop-control.test.tsx` (popover), `user-stopped-stage.test.tsx` |
| D | Playwright (replay) | `stop.spec.ts`: confirm and stop mid-run; Keep going; Escape; 409 after the run ends; Ask again; phone (AC33) |

### 11.2 Existing tests to update

| Phase | Test | Change |
|---|---|---|
| A | `tests/test_graph/test_note_routing.py`, `test_reader_notes_state.py`, `test_reader_notes_replay.py`, `tests/test_agents/test_reader_notes_planning.py`, `test_reader_notes_review.py`, `test_reader_notes_writing.py`, `tests/test_api/test_notes.py`, `test_note_route.py` | the new pass and redraft rules, the prompt text, the packet, carry-over after a note pass, the outcome signature (`terminal`) and `steering_outcome`. In `test_reader_notes_replay.py`, the evaluator, writer and review requests no longer carry the new_angle-only note n2 (`:172`, `:183`, `:209` flip), while the planner's still do (`:149`) and the loops' still do not (`:154`, `:157`) |
| B | `web/test/run-state.test.ts:29-41`, `:44-50` | handler keys (+5: the four progress types and `graph.quality.assessed`) and RunState keys (Phase D adds `session.stopped` and `stopped`) |
| B | `tests/test_agents/test_report_writer.py:2684`, `:3275`, `:1131-1132` (`_FakeChecker.__call__`); `tests/test_agents/test_report_reviewer.py:938-941`, `:2770-2773` | all five `check_statements` substitutes accept `on_batch=None` (review I3) |
| B | `web/test/briefs.test.ts`, `web/test/components/brief-spine.test.tsx`, `running-pipeline.test.tsx`, e2e `briefs.spec.ts`, `reduced-motion.spec.ts`, `visual.spec.ts` | the new bodies, and `03-running` taken at the same state |
| C | `web/e2e/report.spec.ts:9-11`, `web/e2e/layout.spec.ts:128-163` | the evidence line is `#reportEvidence`; `h2` checks inside the cards' `.prose`; the group includes the contents rail at 1920 |
| C | `tests/test_agents/test_report_table.py`, `test_report.py`, `test_report_layout.py`, `test_report_writer.py` (bottom-line assertions), `tests/test_e2e_evaluation/test_replay_doubles.py` | the Key figures table, the Markdown order, the bottom-line layout |
| C | `tests/test_agents/test_report_writer.py:21`, `:1051` | the import takes `MAX_ANSWER_SENTENCES`; the prompt assertion reads "one or two sentences" (review I4) |
| C | `tests/test_agents/test_tool_free_prompts.py:530-537` | `_example_tables()` gains `(_BOTTOM_LINE_REPLY_EXAMPLES, BottomLineDraft)` (review I2) |
| C | `tests/test_graph/test_reader_notes_replay.py:37-40` | the pinned no-note run digests are re-pinned, because the section and bottom-line prompts change |
| C | `web/test/components/report-body.test.tsx`, `reader-notes.test.tsx`, e2e `notes.spec.ts` (report block), visual `12-report-notes` | notes now in the bottom line |
| D | `web/e2e/support.ts` (`waitTerminal`), `web/scripts/capture-replay-events.mjs:12`, `web/test/components/session-screen.test.tsx` | the `stopped` status; the service-stopped tests are unchanged |
| D | `tests/test_evaluation/test_dependencies_controlled.py:472-520` | patch and check `AsyncTavilyClient` (§8.3); `tests/test_tools/test_web_search.py` keeps its synchronous fakes, which now exercise the thread path |

### 11.3 Visual captures

Run `npm run capture:visual` at 1252×853 and 390×844 after each phase and each major UI change, with a new checkpoint each time.

New captures:
- `13-planning-brief(-phone)`, `14-evaluating-brief`, `15-verifying-brief`, `16-writing-brief`, `17-reviewing-brief` (taken with `X-Replay-Hold-After`, then stopped);
- `18-report-cards(-phone)` and `18b-report-cards-1920`;
- `19-stop-confirm(-phone)`, `20-stopped(-phone)`.

`04-report` and `12-report-notes` are re-taken. Each capture is reviewed at full height against its canvas artboard.

*Implementation ruling (final fix wave, 2026-10-01).* `03-running` and `11-note-ack` are taken from a run held after its second topic completes (`X-Replay-Hold-After: researcher.sub_topic.completed#2`), and only once the Researching row has settled: every topic row and its mark at full opacity with no transition running, each mark at the end state its row names, and the subtitle's text unchanged across two reads 300 ms apart. The replay releases an event every 150 ms and the next topic completes 750 ms after the first, less than a ✓ drawing plus the checks and the shot, so a live row is never still. `09-running-extra-pass` and `12-report-notes` are each a second run in the same test.

---

## 12. Risks

| # | Risk | Handling |
|---|---|---|
| R1 | Ungated note threads raise concurrency beyond `sub_topic_concurrency`. | Bounded at ten by LB-D11a, and the run's one request budget is the spending stop. The draft also leaned on the run-wide tool lock; the approved latency work narrows that lock (D38), so this rests on the note limit and the budget alone (review M13). |
| R2 | The window-closing wait adds up to 30 s at the end of research. | Only when a note is being read at that moment; readings time out at `hitl.note_interpret_timeout_s` (15 s). |
| R3 | The model plans a topic for a new_angle subject despite the prompt, duplicating the note's topic. | Tested for the prompt text; duplication is visible, never wrong; no merge. |
| R4 | Carried parts keep statements whose findings moved to the note part after re-partitioning. | Same property as §6.9 carry-over today; statements stay truthful; the review is full. |
| R5 | The bottom line's larger shape makes "fully checked" rarer, so the re-ask is adopted less often. | Rule kept as is; attempt 1's kept lines stand. |
| R6 | With prioritization and actionability left out (D23), a scored report can be refused while all five shown criteria are ✓: acceptance also needs the run's own quality gates to have no hard failure, and a review with the mean score, complete coverage and complete dispositions (`graph/state.py:398-406`; `agents/report_reviewer.py:443-468`). | The verdict line says which, without a score: "a check the run makes itself failed" when the latest `graph.quality.assessed` lists hard failures (`graph/events.py:229-239`), else "the reviewer's overall judgement fell short" (§6.7; review 2, M-3). |
| R7 | Progress events lengthen paced replay by about 150 ms each. | [INFERENCE] about 20 per pass in the replay cases, at their small finding counts; proved or refuted by counting the progress events in the re-captured fixtures (§6.10 item 3). A live Tamil-sized pass at batch size 2 would add about 86 verifier events (§6.1), which only a live run sees. The e2e timeout is 90 s (`web/playwright.config.ts`). |
| R8 | `check_statements` substitutes break on `on_batch`. | Five are known (§3.6) and all are updated (§11.2, review I3); the plan greps for substitutes again before changing the signature. |
| R9 | (D25) The async Tavily client changes the live search transport: a 5xx is now retried, spending up to two more budget units per search, and an httpx client replaces a `requests` session. | The retry rule is the tool's own `_is_retryable`, already covered by its tests with httpx errors; `test_search_5xx_retried`; the first live run after Phase D is watched for search failures. |
| R10 | A mixed note prints to loops as a steering note, but its restatement still names its new_angle subject, so a loop may search for that subject too (D20). | Harmless: `RESEARCH_NOTES` gives rules for emphasis, exclude and scope only (`agents/reader_notes.py:37-43`), and the note's own topic researches the subject either way. |
| R11 | When the provider is down, a failed note thread's owed pass (§5.3) fails as well. | One pass per note bounds it, and D5 wants an owed pass to run whatever the review's state; each attempt still reserves from the run's budget. |

---

## 13. Open issues for the human

No decision is open. One implementation deviation from a closed decision awaits the owner's confirmation. (It was decided on 2026-10-01 under the owner's delegation: the last bullet below.)

- **D19, an unmeasured count is left out** (*implementation deviation, final fix wave, 2026-10-01; awaiting the owner's confirmation; decided 2026-10-01, below*). D19's row in §2 is unchanged: "unknown values read "not yet", never 0, —, or null". The build leaves a count's phrase out of the line where the count has not been measured, never printing it as 0: the stopped Researching row reads "Stopped · none of 3 topics done" before any page or finding is measured, its running line prints only the counts it has, and a redraft route with no defect count reads "Things to fix · …". A measured 0 still prints, in words ("no findings"). Until the owner confirms it, D19 stands as written and the build departs from it in these lines.
- **D19 decided** (*owner-delegated decision, 2026-10-01*). Under the owner's delegation ("use your best judgement") the deviation above is kept: an unmeasured count is left out of the line, never printed as 0, and a measured 0 still prints in words. D19's row in §2 keeps its original words; the lines listed above are the build's reading of it, and no decision is open.

---

## 14. Review round 1 (Fable): how each finding was resolved

Review file: `.superpowers/reviews/2026-09-30-spec-review-1.md` (on `1f186af0`). Every finding is fixed; none is rejected.

| Finding | Resolution |
|---|---|
| C1 mixed-kind notes | Fixed as the review's option (a), which the human ruled (D20): `has_steering_kind`, `steering_view` and the per-request table (§5.1); the redraft rule admits any note with a steering kind (§5.4); the packet carries mixed notes (§5.5); `note_steering_outcome` and `steering_outcome` (§5.6); the Reviewing row and the bottom-line line show both results (§6.7, §7.2); AC34 and `test_mixed_kind_note_steers_and_researches`. |
| I1 AC10 counted twenty notes | AC10 rewritten as the two real worst cases under the ten-note limit (§3.2 cites the limit, the board's refusal and the id pattern), with two named tests. |
| I2 reply examples | §7.1 gives the two replacement `_BOTTOM_LINE_REPLY_EXAMPLES` exactly, adds them to the example-table test, and AC36 plus `test_bottom_line_examples_valid_and_name_topics` check that their topic ids match their headers. The review also led to a second fix: the bottom-line request prints only the answer lines, without the planner's "Plan within these answers" lead (§7.1 Request). |
| I3 five `check_statements` substitutes | §3.6, §6.2 (Writer row), §11.2 and R8 name all five with cites. |
| I4 stale four-sentence text | §7.1 "The four-sentence cap" gives `MAX_BOTTOM_LINE_SENTENCES` its fate at every use, the re-ask's exact new text and the test updates; AC22 and AC36 check them. |
| I5 a started note thread that fails | Fixed rather than accepted: `researched_note_topic_ids` leaves out a topic whose thread ended in a provider failure before answering any of its targets, so the note keeps its one pass, and `note_pass_node` reuses the topic (§5.1, §5.3, §5.4, §5.8). A thread that answered a target has covered its note (D31) and owes nothing. AC35 and `test_failed_note_thread_owes_one_pass`. |
| M1 wrong cites | §3.12 cites the motion tokens at `web/app/globals.css:22-37`; §3.11 and §8.5 name `frozen` as a new `BriefSpine` prop beside today's three (`web/components/BriefSpine.tsx:17`). |
| M2 D-first outcome rule | §4 item 2 says exactly what Phase D changes in today's function if it lands before A. |
| M3 C depends on A | §4 item 5 lists what C, B and D take from other phases; §9 says so. |
| M4 "now" and unnamed research notes | §5.7 keys "now" on the thread's own started event; §6.7 shows an unnamed research note as "researched next"; AC12 and AC18. |
| M5 provider text in live metadata | §4 item 1 amends the two builder docstrings to bind state-bound events and keeps AC13's allow-list. |
| M6 the verifier's first `checked` | §6.1 defines `total` and `checked`, Figure-Match-decided findings included; AC16 and `test_verifier_first_event_counts_figure_match`. |
| M7 AC6 and P2-1 | AC6 adds the parts with no previous section. |
| M8 frozen and live target lists | §5.3 "What the thread sees" says which read is frozen and which is live, and why the live one changes nothing; §3.4 cites both. |
| M9 the fallback's move | §7.3 accepts that a one-point topic loses its card, and says how the outline numbers topics; AC24 and `test_fallback_move_drops_emptied_section`. |
| M10 no plan | §5.2 skips the append when `outcome.result is None`; `test_planner_appends_nothing_without_a_plan`. |
| M11 `pending`'s words | §4 item 2 and §5.7: `pending` reads "not checked yet". |
| M12 DESIGN's live-delivery paragraph | §6.11 adds `DESIGN.md:1613-1621`. |
| M13 the latency work | §3.13 and D38 record it; §6.1 makes every count cumulative and idempotent and states the event counts at batch size 2; §6.5 paces the ticker; §6.2 calls `on_batch` once per settled result; R1 no longer leans on the tool lock; §8.3 makes a shared fetch cancellable. |
| M14 missing test names | §11.1 names tests for the replaced-note cases, a thread's provider failure setting `stop`, the 30 s timeout path and the mixed note. |
| M15 where the rail appears | §7.6 gives the viewport arithmetic (1654 px with the sidebar expanded, 1422 px collapsed); D28 and AC27. |
| Reviewer on the open issues | The human ruled every one (D20–D37; the map follows the decisions table in §2). |

**Review round 2** (`.superpowers/reviews/2026-09-30-spec-review-2.md`, on `80c36c3d`): every round-1 finding resolved, no decision contradicted, the three flagged choices endorsed. Every item is fixed; none is rejected.

| Finding | Resolution |
|---|---|
| I-1 an unstarted note topic counted as researched | `researched_note_topic_ids` now counts a topic as researched only with a completed event whose stop reason is not a provider failure, or an answered target; a planning-time topic that `stop` left unstarted is owed its pass, which reuses it (§5.1, §5.3, §5.8, §6.7 "researched next" row). AC35 and `test_failed_note_thread_owes_one_pass` cover the unstarted case. |
| M-1 example mark not a verbatim span | Example 1's topic-01 line now reads "Example Tester rates Model A 4.5 out of 5 for noise.", so its verdict is a span of its text; the example test checks rule 8's spans (§7.1, AC36). |
| M-2 an unfunded note pass | §5.4 states that the unfunded-pass guard can refuse a reused topic's pass and what the note then reads; §5.8 lists it; AC35's test covers the refusal. |
| M-3 hard failures behind a refusal | Reviewing's `m = 5` refusal line has a third wording for the run's own quality gates, read from the latest `graph.quality.assessed` (new handler, §6.9); R6 names both causes. |
| M-4 the pending Reviewing meta | The static meta becomes "5 checks", plus " · your notes" when the run has a note; the old Reviewing sentence goes with the bodies that replace it (§6.7, §6.9, §6.11). |
| M-5 cite drift | `render_reader_notes` is cited at `agents/reader_notes.py:118-135`; the example-table test's coverage includes the judge's table (`:530-537`). |
