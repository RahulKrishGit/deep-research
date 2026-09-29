# Running stage: live step briefs, the one-time check, and reader notes

**Status** design approved by the human in a brainstorm on 2026-09-28, with every UI element picked on a Claude Design canvas; spec written 2026-09-28 on the main thread; awaiting the human's review. Implementation plans come after this spec, one per phase: each is authored by the `spec-plan-author` agent (Opus 5.5, max effort) and reviewed by the `spec-plan-reviewer` agent (Fable 5.1, max effort) until clean · **Date** 2026-09-28 · **Branch** `feat/live-briefs-and-reader-notes`, cut from `origin/main` `196dd3f1` (the merge of PR #27, which added the page 1→2 question flight and the running→report slide) · **Canvas** https://claude.ai/artifact/VYAhY6CvoxLBXoVgu6AMBL (private to the human; source files in `.superpowers/pick-sheet/`, which git ignores).

**Sources of truth.** The human decisions in §2 are closed, and nothing below reopens them. The design the app already implements is `docs/design/` (`DESIGN.md`, `api-gaps.md`, `prototype/`, `reference/`). The brief this work started from is `.superpowers/claude-design-handoff/BRIEF.md`. The theme rules come from `web/app/globals.css`: its lines 1–1131 are the prototype's CSS verbatim, so the rules in BRIEF.md's "Theme rules" hold.

Every `path:line` below was read at `196dd3f1` on 2026-09-28. Engine paths are under `src/deep_research/`, written `agents/…`, `graph/…`, `api/…`, `utils/…`, `runtime/…`. Web paths are under `web/`.

**Legend.**
- **[INFERENCE]** marks something not observed in code or in a command run for this spec. Each one names the test that proves or falsifies it.
- **(I)** marks illustrative copy written for this spec, to be used as written unless the plan finds a shorter true sentence.

---

## 1. Problem and scope

The running stage (page 2) shows a seven-row pipeline, a "Now" header and a counters block. It moves only once per node, because every event a node emits is published in one burst when the node finishes (`graph/orchestrator.py:304-354`, recorded as `docs/design/api-gaps.md:106`, item 3.7). During Researching, the longest step, the screen does not change for many minutes.

The reader cannot influence a run once it starts, except through an up-front "extra passes" number that they have to guess before anything has been found.

This spec delivers three things, in three phases. Each phase gets its own implementation plan.

| Phase | Delivers |
|---|---|
| **1. Live progress and step briefs** | Events published as they happen. The plan's sub-topic titles on the stream. The running row expands into a live brief (picks 1A, 2C, 3B). Removal of the "Now" header, the running-stage counters block, the pass counter and the page-1 extra-passes control. |
| **2. The one-time check** | Before planning, and only when the question leaves something material open, the reader is asked up to three questions, one at a time (pick 4B plus "Other…"). With no answer, the run starts on best guesses after 60 s. |
| **3. Reader notes** | A note line at the foot of the pipeline card (pick 6A). Each note is interpreted, acknowledged in the running step, and applied by every step from that point on except verification. Every note the review finds uncovered gets one targeted research pass that does not use the extra-pass budget. |

| In scope | Out of scope |
|---|---|
| `web/`: running stage, a new check stage, settings popover, composer, status chip, report pass fact, a "Your notes" block in the report | The idle page (beyond removing the extra-passes control), the Evidence view, sidebar design, the page 1→2 and 2→3 journeys (kept exactly as PR #27 built them) |
| `api/`: `needs_input` status, `POST /research/{id}/answers`, `POST /research/{id}/notes`, the check and note interpreter services, response fields | Authentication, persistence across restarts (sessions stay in memory, `docs/design/api-gaps.md:133`), cancel or re-run |
| `graph/` and `agents/`: live event publication, the plan-titles metadata, reader answers and notes in prompts, review note dispositions, the `note_pass` route | Any change to what verification accepts; any change to the extra-pass rule for the reviewer's own gaps |
| `docs/design/DESIGN.md` §3.4, §3.5, §5.6, §5.7, §5.8 and `api-gaps.md` 3.7, amended to match | `docs/design/prototype/` and `reference/` (historical record; not changed) |
| Tests at every layer; full-page captures at 1252 and 390 px after each phase and after each major UI change | A live, paid run (governed by the existing spend rules; not a requirement of this spec) |

---

## 2. Decisions (human-approved 2026-09-28)

| # | Decision |
|---|---|
| D1 | **Row open and close (pick 1A).** The row's height opens first. Then its brief lines rise in, each fading up 4 px, 60 ms apart. Closing reverses this: the lines fade out, then the height closes. |
| D2 | **Brief content (pick 2C).** While a row runs, its subtitle becomes the live facts line (green, mono). Its body is a checklist of topics for Researching and one plain sentence for every other step. There is no separate stats row and no separate "now researching" line. |
| D3 | **Hand-off (pick 3B, overlapped).** The next row starts opening while the connector is still filling down to it. |
| D4 | **Human in the loop is "ask once, then notes".** There is no plan review, no editable question list, no required/optional toggles, no mid-run pauses and no countdown during the run. |
| D5 | **The one-time check (pick 4B).** One question at a time on page 2, in the place where the pipeline will appear. Tapping an answer moves on. One answer per question is marked "best guess". There is an **"Other…"** answer that opens a text field for the reader's own answer. **Back**, **Skip this one** and **Just start** are available. It appears only when the question leaves something material open, with at most 3 questions. |
| D6 | The check **auto-starts after 60 s with best guesses** if the reader does not answer. |
| D7 | **No assumptions UI (pick 5 dropped).** There are no dropdowns, chips or strip for re-choosing things already answered. The reader sees their answers once, in the check's summary. Any later change goes through a note. |
| D8 | **Notes (pick 6A).** A quiet, borderless line at the foot of the pipeline card, with the placeholder **"Add a note — something to focus on, leave out or change"**. It sends with Enter or with a neutral icon button (not the purple primary). |
| D9 | **Notes are interpreted and acknowledged.** A fast model classifies each note. The running step shows a line restating the interpretation, not echoing the raw text. The later of two conflicting notes wins, and its acknowledgement says which note it replaces. |
| D10 | **Verification is never affected by a note.** |
| D11 | **Every note the review finds uncovered gets exactly one targeted research pass**, outside the extra-pass budget. The guard is per note: one pass each. The run's request-attempt budget still applies (`request_budget.py:131-157`, i.e. `src/deep_research/request_budget.py`). |
| D11a | **At most 10 notes per run** (amended by the human after first review, 2026-09-28; this replaces "no cap"). The UI does not mention the limit. After the 10th accepted note, the note line is disabled, with no message. |
| D12 | **The "Now" header is removed** from the running stage. The expanded row replaces it. |
| D13 | **The running stage's counters block ("counted from the event stream") is removed.** Each row's live subtitle and outcome line carry its counts. |
| D14 | **The pass counter is removed** from the running stage and the status chip. A row that reopens says why it reopened. The internal `iteration` stays in the API and trace. |
| D15 | **The page-1 "Extra passes" control is removed**, along with its pill and its chip in the settings strip. The reviewer's own gap-filling stays fixed at the config default of 1 (`config.yaml:211`). The API keeps accepting `max_iterations`. The report's pass fact becomes plain words, for example "Went back once to fill gaps". |
| D16 | **A settings row "Ask me when the question is unclear"**, default **on**, sent with the request. |
| D17 | **Theme rules from BRIEF.md hold everywhere.** Tokens only. Colour means status. Purple appears only on the single primary button. One surface per region, with no nested boxes. Text entry looks like text. |

---

## 3. Ground truth

### 3.1 Events and their publication

| Fact | Where |
|---|---|
| `ResearchEvent` has `event_type`, `source`, `message`, `timestamp` (stamped at construction) and `metadata`. It has **no id**. It inherits `extra="forbid"`. | `utils/types.py:1337-1342`, `:101-108` |
| Agents build events with `agent_event(...)`. Graph events come from `graph_event(...)` helpers. | `agents/events.py:18-41`, `graph/events.py:25-318` |
| Every agent returns its events in `AgentRun.state_update["events"]`. The node merges them once, after `agent.run` returns. | `graph/nodes.py:209-257` |
| `graph.node.started` is merged into local state before the agent runs, but it is published only with the node's other events. | `graph/nodes.py:209-212` |
| Publication iterates `graph.astream(stream_mode="values")` and publishes `state.events[published:]`. It is index-based. | `graph/orchestrator.py:337-346` |
| `researcher.tool_call` events are built **after** each sub-topic's loop ends, from `react.steps`, so their timestamps are the sub-topic's end time. | `agents/researcher.py:2593-2618`, `:4536` |
| The researcher runs sub-topics with `asyncio.gather` under `Semaphore(sub_topic_concurrency)`. The configured value is 10 (`config.yaml:120`) and `max_sub_topics` is 10 (`config.yaml:169`), so **all planned topics run at once**. | `agents/researcher.py:4673-4719` |
| `planner.planning.completed` metadata is `sub_topic_count`, `repair_attempted`, `stop_reason`, `iterations`, `tool_calls`. **No titles are published anywhere.** | `agents/planner.py:2826-2840` |
| `researcher.sub_topic.started` metadata is `sub_topic`, `priority`, `index`, `existing_sources`. `.completed` adds `findings`, `successful_reads`, `findings_retained` and others. | `agents/researcher.py:2584-2589`, `:2667-2687` |
| `source_evaluator.evaluation.completed` carries `source_count`. `report_writer.report.written` carries `statements` and `citations`. | `agents/source_evaluator.py:920-963`, `agents/report_writer.py:3093-3113` |
| The only per-run ContextVars are the trace context and replay's `requested_case`. Replay relies on `create_task` copying context into tasks. | `observability/context.py:53`, `api/replay.py:43`, `:139-141` |
| `ProgressHandler = Callable[[ResearchEvent], None]`. `ResearchSession.publish` is synchronous and runs on the loop. | `graph/orchestrator.py:262`, `api/sessions.py:61-72` |

### 3.2 Sessions and API

| Fact | Where |
|---|---|
| `SessionStatus = Literal["running","completed","max_iterations","incomplete","failed"]` | `api/models.py:21-27` |
| `SessionStore.start` registers `running`, then `create_task(self._run(...))`. There is no waiting state. | `api/sessions.py:140-179` |
| `iter_events` replays from 0, then waits on `changed` until terminal. | `api/sessions.py:200-227` |
| `ResearchRequest` fields: `query`, `max_iterations` (ge=0, nullable), `output_format`, `config_overrides` (validated against `ConfigSettings`). `extra="forbid"`. | `api/models.py:46-72` |
| Routes: `GET/POST /research`, and `/status`, `/stream`, `/report`, `/evidence`, `/trace` under `/research/{id}`. | `api/app.py:198-374` |
| POST returns 202. A `preflight` config failure returns 500 `configuration_error`. | `api/app.py:211-240` |
| Replay: `ReplayRunner` ignores `question` and `max_extra_passes`. Scripted completions assert **substrings** (`_require`) and parse some packet lines with anchored regexes, so injected prompt text must not match those patterns. | `api/replay.py:67-127`, `e2e_evaluation/replay.py:804-808`, `:529-571`, `:1496` |
| The web proxy forwards **GET and POST only**, and allowlists request headers `accept`, `content-type`, `x-replay-case`. | `web/app/api/[...path]/route.ts:8`, `:80-81` |

### 3.3 Planner, researcher, downstream agents and routing

| Fact | Where |
|---|---|
| Planner task: `AgentTask(instruction=original_question, guidance=planner_guidance(memory_context))`. `plan_messages` renders `# Research question`, `# Answer contract`, `# Context` (= guidance), `# Scoping notes`. | `agents/planner.py:3061-3065`, `:2637-2680` |
| `derive_answer_contract(*, question, now, requested_word_limit=None)` reads only the question text. An existing contract wins. | `agents/planner.py:1296-1412` |
| `plan_review_messages` sees the question, contract and plan only. | `agents/planner.py:2775-2793` |
| `AnswerContract` fields include `geographic_scope`, `evidence_period_requirement` and `assumptions: list[str]` (required). | `utils/types.py:131-173` |
| `EvidenceTarget` has `question`, `required`, and optional `measure`, `period`, `kind`, `geography`, `organisation`. | `utils/types.py:859-883` |
| Researcher per-turn chain: `run_react_loop` → `decide` closure → `_complete_react_decision` → `render_react_messages`, with sections Task, Guidance, Notes so far, Acquisition context, Budget, How to respond. **The `decide` closure is the per-turn injection point.** | `agents/react.py:404-409`, `agents/researcher.py:4364-4376`, `agents/base.py:413-470`, `agents/prompts.py:230-274` |
| The researcher does not read the answer contract. | backend survey, `agents/researcher.py` |
| Source evaluator prompt has **an unused `# Context` slot**. | `agents/source_evaluator.py:857-869` |
| Verifier Context Check messages carry no question. | `agents/evidence_verifier.py:921-965` |
| Writer `section_messages`: `# Question`, `# Answer form`, targets, budget, defects. | `agents/report_writer.py:1091-1129` |
| Reviewer: 7 dimensions; `review_messages` include question and contract; pass rule mean ≥ 0.80 plus coverage. | `agents/report_reviewer.py:240-284`, `:1294-1344`, `:416-441` |
| `graph_route` order: halted → end; missing targets and budget → `extra_pass`; unscored → finalize; redraft if `writer_redrafts < 1` and material defects; accept; exhausted; not accepted. | `graph/state.py:259-314` |
| Graph edges: reviewer → `{extra_pass, writer_redraft, finalize_report, END}`; `extra_pass → researcher`; `writer_redraft → report_writer`. | `graph/orchestrator.py:184-211` |
| `graph_recursion_limit(max_extra_passes) = (max+1) * len(NODE_NAMES) + margin` | `graph/state.py:340-349` |
| `merge_research_state` rejects unknown keys, appends `sub_topics/raw_findings/events/errors`, and replaces other fields. New fields need a default, plus an entry in `ResearchStateUpdate`. | `utils/types.py:2165-2240`, `:1970-2000`, `:2010-2031` |

### 3.4 The web app

| Fact | Where |
|---|---|
| `RunningPipeline` renders `.ask-head`, then `.card.stack` > `.pipe-now` (the "Now" label, stage, blurb, loop tag, "stage N of 7", pass label, progress track) > `Spine withArcs` > `Counters`. | `web/components/RunningPipeline.tsx:36-66` |
| `Spine` rows are `li[data-stage][data-state][data-fed]` with `.bullet`, `.stage-name` (+ `.sr` " (in progress)"), `.stage-meta` (caption or static meta), and an optional `↺`. Arcs are measured in a `useLayoutEffect` plus a window `resize` listener, with **no ResizeObserver**. | `web/components/Spine.tsx:14-61` |
| `RunState` and the 16 `EVENT_HANDLERS`. `run-state.test.ts` asserts exactly those 16 keys. | `web/lib/run-state.ts:41-185`, `web/test/run-state.test.ts:29-38` |
| The chip's running note is `passText`: "Running · pass N of P". | `web/lib/format.ts:48-71`, `web/components/StatusChip.tsx:5-7` |
| Composer `DEFAULT_SETTINGS.extraPasses: 1`. `buildRequest` sends `max_iterations: s.extraPasses`. The pill is `#pillExtra`. The popover has the extra-passes stepper. | `web/components/Composer.tsx:21-28`, `:122-124`, `web/components/SettingsPopover.tsx:80-91` |
| The popover has **no boolean toggle**. Its rows are `.pop-row > .lbl + .seg`. `.pop-row` spacing is tightened so the panel fits at 853 px. | `web/components/SettingsPopover.tsx:56-91`, `web/app/globals.css:773-778` |
| `SessionScreen` stage selection: not found → loading → stopped → running+beat (Submitted) → running (RunningPipeline) → failed → report. `run` is a mutable ref, re-rendered with `bump()`. The stream resets `RunState` on every connect. | `web/components/SessionScreen.tsx:31-35`, `:93-132`, `:174-183` |
| The Submitted stage has no `.card`. The pipeline card appears after the beat (2170 ms, or 1270 ms under reduced motion) with `.is-arriving`. | `web/components/SubmittedStage.tsx:26-38`, `web/lib/handoff.ts:54-59`, `web/app/globals.css:228` |
| **CSS constraint:** `web/scripts/check-css-verbatim.mjs` requires `globals.css` lines 1–1131 to equal the prototype. New rules go under `/* ═══ 2026-09-27: app-only additions ═══ */` (line 1133), with no colour literals. | `web/app/globals.css:1133` |
| Connector today: `li + li::before` / `::after` anchored at `top: calc(-50% - var(--space-2))`, `height: calc(100% + var(--space-2))`. **This assumes equal row heights.** | `web/app/globals.css:487-496` |
| Reduced motion: one block zeroes every duration, then restores 240 ms opacity-only `enter`/`leave`/`arrive`. | `web/app/globals.css:991-1028` |
| Report card tests: `layout.spec.ts` measures `.report-main .card` `.first()`, and `report.spec.ts` asserts the first `.prose p.avail` is the evidence line. A new block must not be a separate `.card` before the report card, and must not sit inside `.prose` as `p.avail`. | `web/e2e/layout.spec.ts:136-140`, `web/e2e/report.spec.ts:9` |

---

## 4. Design

### 4.1 Phase 1: live progress (backend)

**E1. Event identity.**
- `ResearchEvent` gains `event_id: str`, defaulting to a new UUID4 hex per event (`utils/types.py:1337`).
- The API's SSE payload carries it unchanged. It is additive: the web app ignores unknown keys.
- Old checkpoints load with fresh ids ([INFERENCE]; proved by a checkpoint-load test in the plan).

**E2. A run-scoped live sink.**
- A new module `graph/live.py` holds a ContextVar `_LIVE_SINK` plus `publish_live(event) -> None`, which is a no-op when nothing is bound.
- `graph/orchestrator.py` `_stream_graph_result` binds a sink for the duration of the stream when an `event_handler` is supplied. The sink:
  1. calls the handler;
  2. records the event's `event_id` in a `published_ids` set.
- The snapshot loop keeps its index-based slice, but skips any event whose id is already in `published_ids`.
- Result: **every event is published exactly once, and in state order for everything not published live.** Live events arrive earlier than their node's other events, which is permitted because the web app is burst-safe (`docs/design/DESIGN.md` §5.7).
- [INFERENCE] LangGraph runs node coroutines in tasks that inherit the ContextVar. Test `test_live_sink_reaches_nodes`: a fake node calls `publish_live`, and the handler must receive the event before `graph.node.completed`.

**E3. Where events are published live.** The rule at every site: the event object published live is the same object later returned in `state_update["events"]`, so the id dedupes.

| Event | Site | Change |
|---|---|---|
| `graph.node.started` | `graph/nodes.py:209-212` | Publish live when merged. |
| `planner.planning.started`, `planner.memory.recalled` | `agents/planner.py:3111-3114` | Publish live at construction. |
| `planner.planning.completed` | `agents/planner.py:3121` | Publish live. The metadata gains `sub_topics: [{coverage_id, title}]`, with each title capped at 160 characters. Titles are plan content, not provider error text, so the rule at `agents/events.py:27-30` is respected. |
| `researcher.sub_topic.started` | `agents/researcher.py:4427-4433` | Publish live. The metadata gains `coverage_id`. |
| `researcher.tool_call` | today `agents/researcher.py:4536` | Build it **when the step's observation is recorded** (inside the loop, through the step callback), publish it live, and collect it for the sub-topic's outcome, instead of rebuilding the list after the loop. |
| `researcher.sub_topic.completed` | `agents/researcher.py:4537-4556` | Publish live. The metadata gains `coverage_id`. |
| `source_evaluator.evaluation.started` / `.completed` | `agents/source_evaluator.py:1355-1383` | Publish live. |
| `evidence_verifier.verification.completed` | `agents/evidence_verifier.py:1135-1150` | Publish live. |
| `report_writer.report.written` | `agents/report_writer.py:3093-3113` | Publish live. |

Graph routing events (`graph.route.decided`, `graph.report.reviewed`, `graph.extra_pass.started`, `graph.report.redraft_requested`) already come at node boundaries and keep their current publication.

**E4. Replay.** `ReplayRunner`'s paced queue receives live events through the same handler (`api/replay.py:95-133`), so replay stays paced and ordered. The web fixture capture script (`web/scripts/capture-replay-events.mjs`) is re-run, and the fixtures are regenerated.

**E5. Docs.** `api-gaps.md` 3.7 is marked closed, and `DESIGN.md` §5.7 "Delivery is once per node step" is rewritten (§4.9).

### 4.2 Phase 1: removals and the chip (web plus a small API default)

- **Remove `.pipe-now`** from `RunningPipeline` (`web/components/RunningPipeline.tsx:45-61`). That covers the "Now" label, stage name, blurb, loop tag, "stage N of 7", pass label and progress track.
  - The row accessible names (" (in progress)") remain the non-colour state signal.
  - DESIGN §3.4's "progress track's aria-valuenow" signal is replaced by `aria-current="step"` on the active row (§4.9).
- **Remove `<Counters>` from `RunningPipeline`** (`:63`). `Counters` stays in `FailedStage` and `StoppedStage`, which are unchanged by this spec.
- **Status chip while running:** "Running · {active step label}", for example "Running · Researching". `statusNote` for `running` no longer calls `passText`. Terminal chip texts are unchanged.
- **Extra passes, removed from the UI:**
  - `DEFAULT_SETTINGS.extraPasses`, `#pillExtra`, the popover stepper, and the "extra passes" chip in `SettingsStrip`;
  - `SubmittedSettings.extraPasses` (`web/lib/session-store.ts:4`), plus `ceiling()` and the `passes` prop plumbing in `SessionScreen`.
  - `buildRequest` omits `max_iterations`, so the API uses the config default.
  - `ResearchRequest.max_iterations` stays accepted.
- **Report pass fact** (`#repFactPass`), built from the status response. `note_passes` is added in Phase 3 and treated as 0 until then.

| `iteration` | `note_passes` | Text (I) |
|---|---|---|
| 0 | 0 | "One research round" |
| 1 | 0 | "Went back once to fill gaps" |
| 2 | 0 | "Went back twice to fill gaps" |
| n (> 2) | 0 | "Went back n times to fill gaps" |
| any | k > 0 | the above, plus " · went back {once/twice/k times} for your notes" |

### 4.3 Phase 1: the step briefs (web)

**Row anatomy.** Each spine row becomes:

```
li.spine-row[data-stage][data-state][data-fed][data-open]
  span.bullet
  div
    div.ps-head        name (.stage-name) + subtitle (.stage-meta)
    div.ps-x           grid-template-rows 0fr↔1fr
      div.ps-xi        min-height 0, overflow hidden
        div.ps-brief   the brief body
```

- **Pending** rows never open.
- **The active row** is always open.
- **Done and loop rows** are collapsed and show their outcome line. The head is a `button[aria-expanded]` that toggles `data-open`, so a finished row can be reopened by click or keyboard and closed again.
- A reopened row shows its final brief: for Researching, the final checklist; for Planning, the sub-topic titles.

**Brief content (D2, BRIEF.md tone: plain words, counts and titles, no tool names, no ids, "not yet" for unknowns).**

| Step | Running subtitle (green) | Running body | Outcome line when done |
|---|---|---|---|
| Planning | its static meta | "Breaking your question into sub-topics…" (I) | "{n} sub-topics" |
| Researching | "{done} of {n} topics done · {pages} pages read · {findings} findings", or "{n} topics · researching" before the first finishes | Checklist, one row per sub-topic title (below) | "{n} topics · {pages} pages read · {findings} findings" |
| Evaluating sources | its static meta | "Rating sources for trustworthiness and relevance" (I) | "{source_count} sources rated" |
| Verifying evidence | its static meta | "Checking {findings} findings against their pages" (I), where `findings` comes from `researcher.research.completed` | "{verified} verified · {corrected} corrected · {dropped} dropped" |
| Writing report | its static meta | "Writing the report from verified findings only" | "Report drafted · {statements} sentences · {citations} citations" |
| Reviewing | its static meta | "Reviewing the draft on 7 dimensions" | "Accepted · {score}", "Not accepted · {score}", or "Sent back to fill {k} gaps" |
| Publishing | its static meta | "Saving the report and evidence log" | "Published" |

**Researching checklist.** Topics run concurrently (§3.1), so several topics can be in progress at once. The canvas's "topic 3 of 5" wording is therefore not used; this is a correction of copy, not of the picked structure. Each row is `mark · number · title · fact`:

| Topic state | Mark | Fact text |
|---|---|---|
| waiting (not started) | ○ hollow ring, `--border` | "not yet" |
| running (`sub_topic.started`) | ● `--status-ok` dot with the `halo` loop | "reading" |
| done (`sub_topic.completed`) | ✓ drawn in `--status-ok` | "{findings} findings" |

- Pages read is the sum of `successful_reads` over completed topics. Findings is the sum of `findings_retained` over completed topics, then `researcher.research.completed.findings` once that arrives.
- [INFERENCE] `findings_retained` summed equals the research-completed total. Test `test_topic_findings_sum_matches_research_total` over the replay fixtures. If it fails, the subtitle uses the completed total only.

**Reopened rows (loops).**
- On `graph.extra_pass.started`, Researching's body gains a first line: "Going back to research {k} gaps the review found" (I), where k = `len(metadata.targets)`. The checklist then shows only the sub-topics that pass re-runs.
- On `graph.report.redraft_requested`, Writing's body gains "Rewriting to fix {n} issues the review found" (I).
- Phase 3 adds the note-pass line (§4.7).
- The return arcs are kept (`web/components/Spine.tsx:29-61`), and the loop tag's content moves into these lines.

**Motion (D1, D3). Only `transform`, `opacity`, `grid-template-rows` and the check's `stroke-dashoffset` animate.** The last has precedent in `loopflow`.

| Moment | Spec |
|---|---|
| Row opens | `grid-template-rows: 0fr→1fr` over `--motion-fluid` (420 ms), `--ease-entrance`. Then each body line: opacity 0→1 and `translateY(4px)→0`, 240 ms, delay `280ms + i*60ms`. |
| Row closes | Lines: opacity →0, 160 ms, no stagger. Then height `1fr→0fr`, 420 ms, delay 160 ms. |
| Hand-off (3B), timed from the moment row k is marked done and row k+1 becomes active | Row k content fades out (180 ms, at 0). Row k height closes (420 ms, at 100 ms). Row k subtitle cross-fades to its outcome (200 ms, at 260 ms). Connector k→k+1 fills (`--fill-line` 620 ms, at 180 ms). Row k+1 node fills (420 ms, at 600 ms). Row k+1 height opens (420 ms, at 600 ms). Row k+1 lines rise (240 ms, at `900ms + i*60ms`). Total ≈ 1.3 s. |
| Topic done | The ✓ draws: `stroke-dashoffset 14→0`, 360 ms, delay 80 ms. The ● fades out (200 ms). |
| Counts change | Numbers tween from old to new over 400 ms (rAF, tabular-nums). |
| Working signal | The existing `halo` on the active node and on running topic dots. **It remains the only loop on the screen.** |

- **Reduced motion** (extends `globals.css:991-1028` in the app-only section): heights change instantly; lines fade over 160 ms with no stagger and no translate; the ✓ appears without drawing; numbers jump; halos are pinned as today.
- **Connector geometry for rows that expand.** The rules go in the app-only section and override the verbatim ones.
  - Rows align to the top (`align-items: start`).
  - The node sits at a fixed offset: node centre = `var(--space-3) + 16.5px` from the row's top.
  - The connector is drawn by the **upper** row, `li:not(:last-child)::before/::after`, from its own node centre (`top: calc(var(--space-3) + 16.5px)`) to the next node centre (`bottom: calc(-1 * (var(--space-2) + var(--space-3) + 16.5px))`). The fill is `transform: scaleY(0→1)` from the top, and it scales when the upper row is `done`/`loop`.
  - `data-fed` stays on the lower row, for the existing tests and screen semantics.
  - The verbatim `li + li::before/::after` rules are neutralised in the app-only section (`content: none`).
- **Arc measurement** gains a `ResizeObserver` on `.spine-wrap`, plus `transitionend` re-measurement, so arcs stay attached to the nodes while rows animate (`web/test/setup.ts:44-46` already stubs `ResizeObserver`).

**State (web).**
- `RunState` gains:
  - `topics: {coverageId, title, state: "waiting"|"running"|"done", findings: number|null}[]`
  - `pagesRead`, `findingsSoFar`
  - `open: Set<string>` of rows the reader reopened
  - per-row `outcome` strings
- New handlers:
  - `researcher.sub_topic.started` (not handled today)
  - titles from `planner.planning.completed.metadata.sub_topics`
- `run-state.test.ts`'s exact-keys assertion is updated.
- Every derivation stays burst-safe: the state after event k depends only on events 1..k (DESIGN §5.7).

### 4.4 Phase 2: the one-time check (backend)

**Request.** `ResearchRequest` gains `ask_clarifying_questions: bool = True` (`api/models.py:46`).

**Config.** A new section `hitl` in `config.yaml` and `ConfigSettings` (`utils/config.py`), so `config_overrides` can set it:

```yaml
hitl:
  check_timeout_s: 20            # the check call; failure or timeout = no questions
  answer_wait_s: 60              # D6
  note_interpret_timeout_s: 15   # Phase 3
```

The check uses the configured provider and model with thinking disabled and structured output.

**Service.** `api/clarify.py` defines `ClarityChecker = Callable[[str, ResearchSettings], Awaitable[ClarityCheck]]`.
- Live mode uses the provider.
- Replay mode uses a scripted checker. It returns **no questions** unless the request carries header `X-Replay-Clarify: on`, in which case it returns a fixed set. That keeps every existing e2e flow unchanged.
- The header is added to the proxy's request allowlist (`web/app/api/[...path]/route.ts:8`).
- `create_app` gains a `clarity_checker` parameter, like `runner`.

**`ClarityCheck` contract:**

```
{ "questions": [                      # 0..3
    { "id": "q1",
      "dimension": "geography" | "period" | "purpose" | "scope",
      "text": "Which region should this cover?",
      "short": "Region",
      "options": ["United States", "European Union", "Global"],   # 2..4
      "best_guess": "Global" } ] }                                 # must be one of options
```

The prompt tells the model to ask **only** about dimensions whose answer would change the plan, never about something the question already states, and at most 3.
- Invalid JSON, a `best_guess` not among the options, or more than 3 questions: the output is dropped, and the run starts with no questions.
- The check never blocks a run.

**Lifecycle.**
1. `SessionStore.start` (`api/sessions.py:140-179`) registers the session.
2. If the flag is on, its task runs the check (timeout `check_timeout_s`).
3. With zero questions, `_run` proceeds exactly as today.
4. With questions:
   1. The status becomes **`needs_input`**, added to `SessionStatus` and non-terminal.
   2. It publishes `session.clarification.requested` with metadata `{questions, deadline_at}` (ISO, now + `answer_wait_s`).
   3. It awaits an answers future with that timeout.
5. On answers, skip or timeout:
   1. It publishes `session.clarification.answered` with metadata `{answers: [{question_id, value, source: "chosen"|"typed"|"best_guess"}], reason: "answered"|"skipped"|"timed_out"}`.
   2. The status returns to `running`.
   3. The runner is called with `reader_answers`.

**Route.** `POST /research/{id}/answers` with body `{ "answers": [{"question_id": "q1", "choice": "United States"} | {"question_id": "q2", "text": "since 2021"}], "skip": false }`.
- Returns **202** with the session response.
- **404** unknown session; **409** `not_waiting_for_input` when the status is not `needs_input`; **422** on validation (an unknown `question_id`, a choice not among the options, text over 200 chars).
- Questions left unanswered take their `best_guess`.
- The route is allowed through the proxy (POST is already forwarded).

**Engine.**
- `run_research` gains `reader_answers: Sequence[ReaderAnswer] = ()`.
- `ResearchState` gains `reader_answers: list[ReaderAnswer] = []` (replace-on-write; added to `ResearchStateUpdate`).
- `ReaderAnswer = {question_id, dimension, text (the question), value, source}`.

| Consumer | Change |
|---|---|
| `derive_answer_contract` | New keyword `reader_answers`. `geography` answers set `geographic_scope`, `period` answers set `evidence_period_requirement`, and every answer adds an assumption line: "Reader said: {short} = {value}", or "Assumed (best guess): {short} = {value}". `answer_contract_for` passes them (`agents/planner.py:3131-3142`). |
| `plan_messages` | A `# Reader answers` section after `# Answer contract`, rendered only when non-empty. |
| `plan_review_messages` | The same section, so the self-check does not flag reader-requested narrowing as a missing dimension. |
| Replay | Sections are added only when non-empty, so existing replay packets are byte-identical ([INFERENCE]; proved by the full replay suite passing unchanged). |

### 4.5 Phase 2: the check stage (web)

- **Stage selection.** `status === "needs_input"` shows a new `ClarifyStage` in `SessionScreen` (`web/components/SessionScreen.tsx:174-183`), after the Submitted beat, exactly as RunningPipeline does today. It keeps the same `.ask-head`, locked question and journey from PR #27. The check card takes the pipeline card's place and arrives with `.is-arriving`.
- **Card** (a single `.card`, D17):
  - `span.cap` "Question {i} of {n}", with step dots on the right (`--border`, the current one `--fg`);
  - `h3.card-title` holding the question text;
  - a stacked list of answer buttons, `aria-pressed`, min height 44 px. The best-guess option carries a `.cap` "best guess". Tapping an answer records it and moves on after 240 ms.
  - The last option is **"Other…"**. It opens a borderless text field (`.tx` style: transparent; a `--border-soft` ground on hover; the focus ring on focus) with a **Next** `.btn-ghost.btn-sm`, disabled while the field is empty. Enter also moves on.
  - Footer: **Back** (`.btn-quiet`, disabled on the first question) · **Skip this one** (`.btn-quiet`) · **Just start** (`.btn-ghost`). There is no purple on this card.
  - Footer cap, from `deadline_at`: "Starts with best guesses in 0:{ss} if you don't answer" (I).
- **Submit.** After the last question, or on Just start, the card POSTs the answers once, never retried (`web/lib/api.ts:92-95` convention). It shows "Starting research with: {short}: {value} (you said|best guess) · …". The running stage takes over when the stream reports `graph.node.started` for the planner.
- **Status chip:** "Waiting for you · a few quick questions", with `.dot-warn`.
- **Settings row** "Ask me when the question is unclear" as a two-button `.seg` (On/Off), in the slot freed by the removed extra-passes stepper. It is sent as `ask_clarifying_questions`.
- **RunState** gains `clarify: {questions, deadlineAt, answered}`, with handlers for the two `session.clarification.*` events.
- **Sidebar.** A `needs_input` session counts as running for the live mark (`web/components/Sidebar.tsx:48`, `:52`: `running` becomes `status === "running" || status === "needs_input"`).

### 4.6 Phase 3: reader notes (backend)

**Route.** `POST /research/{id}/notes` with body `{ "text": "…" }` (1–500 characters after trim).
- Returns **202** with `{ "note_id": "n1", "status": "received" }`.
- **409** `notes_closed` when the session is `needs_input` or terminal, or once `finalize_report` has started. **409** `note_limit_reached` when the run already has `MAX_NOTES_PER_RUN = 10` accepted notes (D11a); rejected POSTs do not count. **404** unknown session. **422** empty or too long.
- The session response gains `notes_remaining: int` (10 minus accepted notes), so the web app knows when to disable the line without a failed POST.

**Interpretation.** `api/notes.py` defines `NoteInterpreter`, injected into `create_app` like the checker. It uses the same provider path, with timeout `note_interpret_timeout_s`.

```
{ "kinds": ["emphasis"|"exclude"|"scope"|"new_angle"|"about_reader"],   # 1..3
  "restatement": "more weight on fire-safety standards",               # ≤ 120 chars, plain words
  "scope": {"geography": "United States"} | null,
  "new_questions": ["How are battery cells recycled at end of life?"], # for new_angle / scope-widening; ≤ 3
  "replaces": "n1" | null }                                            # an earlier note it contradicts
```

If the call fails, the note is kept with `kinds: ["emphasis"]` and `restatement` = the note text, and the acknowledgement says it was passed on as written.

**Events.** Both are published through `session.publish`, not the graph:
- `session.note.received` `{note_id, text}`, published immediately;
- `session.note.interpreted` `{note_id, restatement, kinds, replaces, fallback: bool}`, published when the interpreter finishes.

**The note board.**
- `runtime/notes.py` defines `NoteBoard`: an in-process, append-only list of `ReaderNote {note_id, text, received_at, received_during (node name), kinds, restatement, scope, new_questions, replaces, reviewed: bool, passed: bool, redrafted: bool}`.
- It is bound per run through a ContextVar, the same way as the live sink. `SessionStore` creates it before calling the runner, and the notes route appends to it after interpretation.
- `ResearchState` gains `reader_notes: list[ReaderNote] = []` (replace-on-write) and `note_passes: int = 0`.
- `agent_node` merges `{"reader_notes": board.snapshot()}` into the `started` state before `agent.run` (`graph/nodes.py:209-212`), so every node begins with the notes received so far. Loops already in flight read the board directly.

**Where each step uses notes (D10).** Only notes that are interpreted, and not replaced by a later note, are rendered. The rendered block is `# Reader notes` with one line per note: `- {restatement} ({kinds})`. Its lines never begin with the packet patterns the replay harness parses (§3.2).

| Step | Use |
|---|---|
| Planning | `# Reader notes` in `plan_messages` and `plan_review_messages`, and in the scoping loop's per-turn context via `BaseAgent.build_decision_context` (`agents/base.py:381-395`). A `new_angle` note received before the plan is final can become its own sub-topic. |
| Researching | In each sub-topic loop's `decide` closure (`agents/researcher.py:4364-4376`), before each model call, notes newer than the loop's last-seen id are added under `## Reader notes` in the ReAct messages. Each loop decides relevance itself. `exclude` winds a thread down. `scope` limits later searches and reads. `new_angle` notes are **not** added to running loops; they wait for the review's note pass. The extraction request (`agents/researcher.py:1646-1647`) also carries the block, so extraction respects scope. |
| Evaluating sources | The unused `# Context` slot (`agents/source_evaluator.py:858-859`) carries the block, with the instruction that notes affect **relevance only, never authority or recency**. |
| Verifying evidence | **Nothing.** The verifier's messages never contain reader notes, and a test asserts it. |
| Writing | `# Reader notes` in `section_messages` and `bottom_line_messages`. Emphasis shapes space. `exclude` removes. `scope` leaves out-of-scope findings out and says so in one sentence. `about_reader` sets the level. |
| Reviewing | `# Reader notes` in `review_messages`. The review output gains `note_dispositions: [{note_id, status: "honoured" \| "ignored_with_evidence" \| "no_evidence"}]`, optional and defaulting to `[]`, which leaves the 7-dimension rule unchanged. Notes present in the review input are marked `reviewed = true`. |
| Publishing | Notes received after `finalize_report` starts are refused with 409 (see the route above). |

**Routing (D11).** `graph_route` (`graph/state.py:259-314`) gains two note checks immediately after "halted → end", in this order:
1. **`note_pass`** when any note has the review disposition `no_evidence` and `passed == false`. `new_angle` notes are not given to running loops (see the table above), so the review normally marks them `no_evidence` and they get their pass through this same rule.
2. **Note redraft** when any note is `ignored_with_evidence` with `redrafted == false`, or when any note has `reviewed == false` because it arrived after the review input was built. This routes to the existing `writer_redraft` node and **does not count against `MAX_WRITER_REDRAFTS`**.

Each note is marked `passed` or `redrafted` when its route is taken, so **each note triggers at most one pass and one redraft**. With at most 10 notes (D11a), a run takes at most 10 note passes and 10 note redrafts. Everything after the two note checks is unchanged. A note that is still uncovered after its pass ends in the report as "Couldn't find evidence for your note: …".

**`note_pass` node.**
1. Builds one appended `SubTopic` per note that needs research. Its title is "Your note: {restatement}". Its `evidence_targets` are built from `new_questions`, with scope applied and `required = true`. The `coverage_id` is `note-{note_id}`.
2. Publishes `graph.note_pass.started` `{note_ids, targets}`.
3. Increments `note_passes`, not `iteration`.
4. Edges to `researcher`, which on a note pass researches only the `note-*` sub-topics, mirroring the extra-pass restriction.

`graph_recursion_limit` becomes `(max_extra_passes + 1) * len(NODE_NAMES) + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS) + margin`, where `NOTE_REDRAFT_STEPS` is the superstep count of one writer→reviewer redraft loop. This is the exact worst case of D11a, so the limit is never reached by notes.

**Status response.** `ResearchSessionResponse` (`api/models.py:122-174`) gains:
- `notes: [{note_id, text, restatement, outcome: "covered"|"not_found"|"pending"}]`
- `note_passes: int`
- `clarification: {questions, answers}|null`

### 4.7 Phase 3: notes in the web app

**Note line (D8).** The last element inside the pipeline `.card`, after the spine, separated by a `--border-soft` hairline:
- a borderless `input` (`.tx`, `--text-sm`), with the placeholder "Add a note — something to focus on, leave out or change";
- a neutral `.icon-btn` send with an up-arrow and `span.sr` "Add note";
- Enter sends.
- While the POST is in flight, the input is read-only. On 409 `notes_closed`, the line is replaced by a `.cap` "Notes are closed — the report is being published" (I). It is hidden in terminal stages.
- When `notes_remaining` reaches 0, or on 409 `note_limit_reached`, the input and send button become `disabled`. There is no message, and the placeholder is unchanged (D11a).

**Acknowledgements (D9).** At the top of the active row's brief, one `.ack` line per note: a muted dot, then text, fading in over 200 ms.

| Note state | Text (I) |
|---|---|
| received, not interpreted | "Reading your note…" |
| interpreted | "Got it — {restatement}{where}" |
| replaces an earlier note | "…, replacing your earlier note about {earlier restatement}" |
| fallback | "Got it — passed on as you wrote it" |

`{where}` depends on the active step when the note was interpreted:

| Active step | `{where}` (I) |
|---|---|
| Planning | ", shaping the plan" |
| Researching | ", from each topic's next search" |
| Evaluating | ", in how sources are rated and in the report" |
| Verifying | ", in the report" |
| Writing | ", in this draft" |
| Reviewing | "; the review will check it" |

When more than 2 notes exist, the brief shows the latest 2 plus "and {n} earlier notes".

**Note pass.** On `graph.note_pass.started`, Researching reopens with the first line "Researching your note: {restatement}" (I). Its checklist shows the `note-*` sub-topics. The return arc is drawn like `extra_pass`, but with the redraft stroke (`--meta`), because a note pass is not a warning (DESIGN §3.4: colour means status).

**Report "Your notes".** Inside the report card (`ReportBody` `article.card.stack`), **above** `.prose`, as a `section.reader-notes` with a hairline below:
- eyebrow "Your notes";
- one line per note: the note text, then a `.cap` outcome ("covered", or "couldn't find evidence").

It renders only when `notes` is non-empty. It is not a separate `.card` and is outside `.prose`, which satisfies `layout.spec.ts:136-140` and `report.spec.ts:9`.

### 4.8 Errors and edge cases

| Case | Behaviour |
|---|---|
| The check call fails or times out | No questions; the run starts. |
| Answers POST after the deadline | 409 `not_waiting_for_input`. The card shows "Already started with best guesses" (I) and the stage moves on with the stream. |
| Reload during `needs_input` | The stream replays `session.clarification.requested` and the card rebuilds from the stream (burst-safe). |
| Note interpreter fails | Fallback note (§4.6). |
| Note sent while a note POST is in flight | The input is read-only until the 202 arrives. |
| 10 notes already accepted | 409 `note_limit_reached`; the note line is disabled with no message (D11a). |
| Two notes conflict | The later one wins; `replaces` is shown in the acknowledgement; only the later one is rendered to agents. |
| A note arrives during Reviewing | `reviewed = false` leads to a note redraft (§4.6), and the review checks it. |
| A note arrives after `finalize_report` starts | 409 `notes_closed`. |
| Replay mode | Scripted checker (header-gated). Scripted interpreter: `restatement` = the text, `kinds = ["emphasis"]`. |
| Reduced motion | As in §4.3; ack lines fade without movement. |
| Phone (390 px) | The note line and the check card are full width. Check answers stack full width. There is no horizontal scroll, and every existing layout assertion still holds. |

### 4.9 Documentation changes

| Doc | Change |
|---|---|
| `DESIGN.md` §3.4 (646-725) | The halo stays the only loop. Row open/close, the hand-off, the drawn check and count tweens are one-shot transitions. New connector geometry for rows that expand. `aria-current="step"` replaces the progress-track signal. |
| `DESIGN.md` §3.5 (726-866) | Remove "pass p of P". A reopened row states why it reopened. Add the note-pass route and its arc stroke. |
| `DESIGN.md` §3.2 extra-pass budget (543-575) | The control is removed. The budget is fixed at the config default; the API still accepts `max_iterations`. |
| `DESIGN.md` §5.6 (1307-1511) | Reduced-motion rules for the briefs (§4.3). |
| `DESIGN.md` §5.7 (1512-1549) | Delivery is live (E1–E3); the burst-safe rule stays. |
| `DESIGN.md` §5.8 (1550-1586) | The counters block is removed from the running stage and remains on the Failed and Stopped stages. |
| `api-gaps.md` 3.7 (`:106`) | Closed by this spec. The new routes are added to the route list. |
| `web/README.md` / `README.md` "Run the app" | Mention the check, notes and `X-Replay-Clarify`. |

---

## 5. Acceptance criteria

**Phase 1**
- **AC1.** In a run with an event handler, every event is delivered exactly once. `graph.node.started` for a node arrives before any event that node emits, and before that node's `graph.node.completed`. Each `researcher.tool_call` arrives before its sub-topic's `researcher.sub_topic.completed`.
- **AC2.** `planner.planning.completed.metadata.sub_topics` lists every planned sub-topic's `coverage_id` and title, with each title at most 160 characters.
- **AC3.** The running stage has no `.pipe-now`, no `#runCounters` and no "pass" text. The chip reads "Running · {step}".
- **AC4.** The active row is open and shows its brief as described in §4.3. Done rows show their outcome line and reopen and close on click and on Enter/Space, with `aria-expanded` kept in sync.
- **AC5.** The Researching checklist shows every sub-topic title. Marks and facts follow the table in §4.3, and no count is ever rendered as `0` or `—`.
- **AC6.** The connector joins node centres for every pair of adjacent rows while rows are open, closed or animating, within ±1 px (measured by `getBoundingClientRect` in e2e). The arcs stay attached within ±2 px after a row opens.
- **AC7.** The motion timings match §4.3, recorded with the transition recorder. Under `reducedMotion: "reduce"`, no element gets a `transform` transition and heights change instantly.
- **AC8.** The composer and popover have no extra-passes control. The POST body has no `max_iterations`. The report pass fact follows the table in §4.2.
- **AC9.** Replay e2e at 1252 and 390 px has no horizontal scroll, and every existing layout, handoff, report-slide and reduced-motion test passes after the updates listed in §6.

**Phase 2**
- **AC10.** With the setting on and a clear question (replay default), the flow is identical to Phase 1: no `needs_input` status and no card.
- **AC11.** With `X-Replay-Clarify: on`:
  - the status goes `needs_input`, the card shows "Question 1 of n", and the chip reads "Waiting for you · a few quick questions";
  - choosing answers, including an "Other…" typed answer, POSTs once and shows the summary with "(you said)" and "(best guess)";
  - the planner's packet contains `# Reader answers`.
- **AC12.** With no interaction, the run starts within 60 s + 2 s with every answer `best_guess`, and `session.clarification.answered.reason == "timed_out"`.
- **AC13.** With the setting off, no check call is made.
- **AC14.** A check failure never delays the run by more than `check_timeout_s`.

**Phase 3**
- **AC15.** A note POSTed during Researching produces `session.note.received`, then `session.note.interpreted`, and the ack appears in the active row within 1 s of the interpreted event.
- **AC16.** The note block appears in the prompts of the planner (when planning), the researcher's next decision turn, the source evaluator, the writer and the reviewer. **It never appears in any evidence-verifier prompt.**
- **AC17.** A note whose review disposition is `no_evidence` routes exactly once to `note_pass`. The researcher then researches only `note-*` sub-topics. `iteration` does not change. `note_passes` increments.
- **AC18.** A note `ignored_with_evidence` routes exactly once to a redraft that does not consume `MAX_WRITER_REDRAFTS`.
- **AC19.** Notes after `finalize_report` starts return 409. The status response lists every note with its outcome, and the report shows "Your notes" above the prose.
- **AC20.** Ten notes, each triggering one pass and one redraft (in a stub-agent graph test), complete without hitting the recursion limit. An 11th note POST returns 409 `note_limit_reached`, and the web note line is `disabled` with no added text.

---

## 6. Test plan

| Layer | Tests |
|---|---|
| pytest: events | `event_id` default and uniqueness; checkpoint load; `publish_live` no-op without a sink; `test_live_sink_reaches_nodes`; exactly-once publication with the live and snapshot paths mixed; tool_call timing; planner titles metadata. |
| pytest: API | `needs_input` lifecycle (answer, skip, timeout, check failure); the answers route 202/404/409/422; notes route 202/404/409 (closed and limit)/422; `notes_remaining`; interpreter fallback; response fields; `X-Replay-Clarify` gating; the proxy forwards the new header. |
| pytest: engine | `derive_answer_contract` with answers; `# Reader answers` in plan and plan-review messages; `# Reader notes` in every consumer and **absent from the verifier** (AC16); `note_dispositions` parsing; `graph_route` note ordering and per-note guards; `note_pass` node; recursion allowance (AC20); the full replay suite unchanged when there are no answers or notes. |
| Vitest | run-state handlers (the new keys; exact-keys assertion updated); checklist and outcome derivation from fixtures (regenerated with `capture-replay-events.mjs`); Spine open/close/aria; `ClarifyStage` (Other…, Back, Skip, Just start, countdown text); note line and acks; report notes block; chip texts; composer `buildRequest` without `max_iterations`. |
| Playwright (replay) | Running with briefs, hand-off recording and connector geometry (AC6, AC7); the check flow with `X-Replay-Clarify: on`; note flow and ack; reduced motion; phone layout; report notes block. |
| Existing tests to update | `running.spec.ts:19,29`, `arcs.spec.ts:8-11,22-24`, `running-pipeline.test.tsx:10,17`, `status-chip.test.tsx`, `composer.test.tsx:13-18`, `run-state.test.ts:29-38`, `visual.spec.ts:31` (it waits on `#runNow`; switch to the active row), `layout.spec.ts:38-39` (rows keep `li[data-stage]`), `session-screen.test.tsx:117-126`. |
| Visual captures | `npm run capture:visual` at 1252×853 and 390×844 after each phase and after each major UI change (row anatomy, hand-off, check card, note line), with a new checkpoint name each time. New captures: `10-clarify(-phone)`, `11-note-ack(-phone)`, and `03-running` taken with the Researching brief open. Each is reviewed full height against the canvas picks. |

---

## 7. Risks and open issues

| # | Item | Handling |
|---|---|---|
| R1 | LangGraph context propagation for the live sink ([INFERENCE] in E2). | Tested first in Phase 1. If it fails, fall back to passing the sink through the `Tracker` the agents already hold (`agents/base.py:325`). |
| R2 | Adding `event_id` changes `ResearchEvent` equality in any test that compares events built separately ([INFERENCE]). | The Phase 1 plan greps and updates such tests. |
| R3 | The researcher's tool_call restructuring could change event order inside a sub-topic. | Order within a sub-topic stays the step order. Tests pin it. |
| R4 | Note passes lengthen runs. | Bounded by D11a (10 notes, one pass and one redraft each). The request-attempt budget remains the spending stop. |
| R5 | A reviewer that wrongly reports `no_evidence` triggers passes that find nothing. | The per-note guard bounds it to one pass per note, and the report states the gap. |
| R6 | The canvas's Researching copy ("topic 3 of 5", "N so far") assumed topics run one at a time. | Corrected in §4.3 to concurrent-topic wording. The structure of pick 2C is unchanged. |
