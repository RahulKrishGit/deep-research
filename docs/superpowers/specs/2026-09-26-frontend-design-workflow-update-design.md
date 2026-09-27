# Front-end design package: update to the Evidence Verifier workflow

**Status** design approved by the human (brainstorm, 2026-09-26); review rounds 1 and 2 applied (the human's rulings of 2026-09-26 in §2.1); approved by the independent reviewer in round 3 (approved with one nit, applied); awaiting the human's review · **Date** 2026-09-26 · **Branch** `feat/frontend-console` = `codex/evidence-verifier-pipeline` (`f27ac7e`) merged with `origin/main` (`6129809`, PR #23 design package), merge commit `4c34875` · **Tree** `.worktrees/frontend-console` (the only tree this work touches) · **Sub-project** 1 of 3 (this: the design package; 2: close the API gaps in FastAPI; 3: build the React/Next app — each with its own spec).

**Sources of truth.** The human decisions in §2 and the rulings recorded in §2.1 are closed. The workflow the design must match is `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md` and `docs/superpowers/specs/2026-09-25-consumer-report-format.md`, as implemented on this branch. Every `path:line` below was read in the worktree on 2026-09-26; paths are relative to the worktree root, and a path with no directory prefix such as `state.py` is under `src/deep_research/graph/`, `agents/`, `api/`, `utils/`, `runtime/` or `providers/` as its first mention says.

Legend: [INFERENCE] = not observed in code or in an artifact. (I) = illustrative copy written for this spec, to be used as-is unless the plan finds a shorter true sentence.

---

## 1. Problem and scope

The package in `docs/design/` was frozen on 2026-09-16 against the pipeline the product no longer runs. It names `fact_checker`, `synthesizer` and `critic` nodes and a `refine` loop (`docs/design/DESIGN.md:855-871`), a `Refinements` stepper of 1–5 defaulting to 3 (`DESIGN.md:448`, `:491-516`; `docs/design/prototype/index.html:1986-1988`), a claims-against-sources ledger with verdicts, confidence and corroboration (`DESIGN.md:91-108`), a Quality Snapshot of claim confidence / source authority / corroboration (`index.html:1296-1305`), an Executive Summary report body (`index.html:1248`), a scripted event stream of `fact_checker.claim.checked`, `critic.critique.completed` and `graph.refinement.started` (`index.html:2489-2525`), and an `api-gaps.md` whose `quality_status` and `evidence_path` gaps the API has since closed and whose stage numbering is off by one (`docs/design/README.md:64-71`). The engine on this branch is the Evidence Verifier pipeline publishing the consumer report format.

| In scope (this spec) | Out of scope (own specs or never) |
|---|---|
| `docs/design/DESIGN.md`, `api-gaps.md`, `README.md` | Product code of any kind |
| `docs/design/prototype/index.html`, `prototype/states.html` | Any API change (sub-project 2), including E1 in §4.5 |
| `docs/design/reference/*.png`, regenerated, plus two new renders | The React/Next app (sub-project 3) |
| `scripts/render_design_reference.mjs` (two captures) | `docs/design/open-design/` (2026-09-16 history, untouched) |
| Theme, tokens, element positions, the battery-storage example: **kept** | A second ("v2") package beside the current one |

The deliverable of this spec is the set of decisions in §2–§5. The implementation plan written after it edits `docs/design/`.

---

## 2. Decisions (human-approved 2026-09-26)

| # | Decision | What it replaces |
|---|---|---|
| Q1 | Update the docs, the prototype and the reference renders in place. Not docs-only; not a v2 package. | — |
| Q2 | Keep the Report \| Evidence toggle of DESIGN.md §2 ("A, with C as a mode"). "C" becomes an **Evidence view** replacing the claims-vs-sources ledger: claims, verdicts, confidence and corroboration no longer exist. | `DESIGN.md:91-108`, `:110-127` |
| Q3 | Draw the two loops as **two arcs in the spine gutter** (the old single arc, re-anchored): an amber extra-pass arc Reviewing → Researching and a grey redraft arc Reviewing → Writing, each drawn only when taken. The loop's reason is shown in the running header. | `DESIGN.md:687-716`, `index.html:2263-2313` |
| Q4 | Evidence view = **flat list with status filter chips**; the target is a tag on each row; a detail pane on the right. | the two-pane claim ledger |
| Q5 | Composer **Extra passes** stepper **0–2, default 1** (server default 1 at `config.yaml:211`, `ge=0` with no server maximum at `src/deep_research/api/models.py:48`; 2 is a UI ceiling because each extra pass can add a large share of a 70–110 min run). Topbar chip `pass 1 of 2` = 1 + budget. | `Refinements` 1–5 / 3 |
| Q6 | Approach A: edit in place by hand; keep the battery-storage example content; verify event names and metadata keys against a throwaway offline replay run. | — |

### 2.1 Rulings from review round 1 (human, 2026-09-26)

| # | Ruling |
|---|---|
| R1 | Running-stage counters live in a **compact block inside the existing pipeline card, under its header**. No layout change; no running-stage rail. The design text states that counters update **once per node step**. |
| R2 | `api-gaps.md` gains a Running-stage gap: events are published once per node step (`orchestrator.py:312-346`), not live per event; live per-event delivery is listed for sub-project 2. |
| R3 | The chip note for `incomplete` with a scored review is **`not accepted · {score}`** (chip and Review-card status text), because a quality gate can block acceptance while the review passed (`state.py:306-314`). |
| R4 | The three explanatory popover notes proposed in round 1 are removed; the popover keeps only the read-only effort line and its thinking-off text; the rationale moves into DESIGN.md §3.2 prose. |

---

## 3. Ground truth

### 3.1 Graph, routes, statuses

```
START → planner → researcher → source_evaluator → evidence_verifier → report_writer → report_reviewer
        → { extra_pass → researcher | writer_redraft → report_writer | finalize_report → END | END (halted) }
```

| Fact | Where |
|---|---|
| Edges as drawn above | `src/deep_research/graph/orchestrator.py:184-211` (extra_pass → researcher `:206`; writer_redraft → report_writer `:210`; finalize → END `:211`) |
| Node names; `extra_pass`, `writer_redraft`, `finalize_report` are hops/publication, not agent stages; the visible stages stay 7 | `graph/state.py:38-46`, `:55-65` |
| `report_reviewer` is a service role; a review is `scored` \| `incomplete` \| `provider_failed` | `state.py:51-52`; `utils/types.py:1149` |
| Acceptance: `scored`, all 7 dimensions, mean ≥ 0.80, no material defect, coverage complete; plus no gate hard failure — so `report_not_accepted` also covers a passed review blocked by a gate | `agents/report_reviewer.py:416-443`; `utils/types.py:1118-1131`; `state.py:85-88`, `:306-314` |
| Route destinations `extra_pass` \| `redraft` \| `finalize` \| `end`; an extra pass is bought **before** acceptance is considered whenever a required target is missing and budget remains, so a `completed` run with a not-found target has spent its extra pass (or had budget 0) | `state.py:73-76`, `:259-314` (`:295-297`) |
| `MAX_WRITER_REDRAFTS = 1`; `DEFAULT_MAX_EXTRA_PASSES = 1`; `graph.max_extra_passes: 1` | `state.py:71`, `:152`; `config.yaml:211` |
| Statement Check runs inside the report_writer node; Context Check inside evidence_verifier | `agents/report_writer.py:2005`; `agents/evidence_verifier.py:1106` |
| `graph.quality.assessed` is emitted by the writer node wrapper **after** the writer's own `graph.node.completed` | `graph/nodes.py:300-313` |
| The reviewer node emits `graph.report.reviewed`, then `graph.route.decided`, then its own `graph.node.completed`, in one node step | `nodes.py:819-841` |
| Halting types | `state.py:141-150` |
| A halting node emits `graph.node.started` and then records its error (no `graph.node.completed`); every later agent node through `report_reviewer` emits `graph.node.skipped` (reason `halted`); the reviewer's route is `ROUTE_END`, so `finalize_report` never runs and its own skip (`nodes.py:676-677`) is unreachable; the orchestrator then emits `graph.session.completed` with `status: "failed"`. `has_report` can be true on such a run, because the writer sets `state.report` on its first pass (`report_writer.py:3262`) | `nodes.py:175-186`, `:205-208`; `graph/events.py:81-88`; `state.py:292-293`; `orchestrator.py:396-402` |
| Events reach the stream **once per node step**: each `stream_mode="values"` snapshot publishes the events that superstep appended, so a node's `graph.node.started`, everything it emitted and its `graph.node.completed` arrive together when the node finishes | `orchestrator.py:312-346` |
| Planner: 1–10 sub-topics; researcher tools `web_search`, `web_scraper`, `document_reader`, `query_memory` | `agents/planner.py:82-83`; `agents/researcher.py:3023-3028` |
| Planner tool budget 1; tools `query_memory`, `web_search` (memory withheld when startup recall already gave guidance) | `config.yaml:159`; `planner.py:2999`, `:3101-3105` |
| `ScoredSource` keeps authority / recency / relevance / overall scores | `utils/types.py:625-631` |
| `FindingStatus` = `verified` \| `verified_corrected` \| `quoted` \| `dropped`; `context_unchecked` flag; drop reasons `read_not_found` \| `snippet_not_on_page` \| `all_figures_dropped` | `types.py:356`, `:363-365`, `:413-419` |

Route reason → API status (`state.py:80-135`, `:317-319`):

| Route reason | Destination | `status` | Run continues? |
|---|---|---|---|
| `report_accepted` | finalize | `completed` | no |
| `report_not_accepted` (review refused, a material defect with the redraft spent, or a gate failed) | finalize | `incomplete` | no |
| `review_unavailable` | finalize | `incomplete` | no |
| `extra_pass_requested` | extra_pass | `incomplete` while running | yes — iteration + 1 (`nodes.py:1099-1157`) |
| `extra_passes_exhausted` | finalize | `max_iterations` | no |
| `redraft_requested` | redraft | `incomplete` while running | yes — same iteration, `writer_redrafts` + 1 (`nodes.py:1160-1216`) |
| `halted` | end | `failed` | no; nothing published |

### 3.2 API surface

| Route | Where | Notes |
|---|---|---|
| `POST /research` → 202 `ResearchSessionResponse` | `api/app.py:159-188` | `max_iterations` is passed as `max_extra_passes` (`app.py:183`) |
| `GET /research/{id}/status` | `app.py:190-202` | |
| `GET /research/{id}/stream` (SSE; ids restart at 1 per subscriber) | `app.py:204-236`; `api/events.py:14-27` | |
| `GET /research/{id}/report` (`text/markdown`; 409 `session_not_complete` / `report_unavailable`) | `app.py:238-263` | |
| `GET /research/{id}/trace` | `app.py:265-269` | |
| No `/evidence`, `/health`, `/capabilities`, collection route | `app.py` (grep: none) | |

`ResearchRequest` (`models.py:38-63`): `query`, `max_iterations: int | None, ge=0` (default None → config), `output_format`, `config_overrides` (nested object validated against `ConfigSettings()`).

`ResearchSessionResponse` (`models.py:114-164`; assembled at `api/sessions.py:73-128`), 17 fields:

| Field | Type / values | Present while running? |
|---|---|---|
| `session_id`, `status`, `current_agent`, `iteration`, `started_at`, `finished_at`, `report_path`, `trace_url`, `errors[]` | as before; `status` ∈ running \| completed \| max_iterations \| incomplete \| failed (`models.py:13-19`) | yes (`current_agent` from `graph.node.started`; `iteration` from any event carrying `iteration`, `sessions.py:61-70`) |
| `evidence_path`, `quality_path`, `quality_contract_version` | str \| null | no |
| `semantic_review_status` | `scored` \| `incomplete` \| `provider_failed` \| null | no |
| `semantic_review_score` | float \| null (null = no score, never 0) | no |
| `duration_seconds` | float \| null | no |
| `coverage` | `{required_targets, answered_targets, missing_required_target_ids[], not_found_target_ids[]}` (`models.py:67-81`) | no |
| `evidence_counts` | `{read_records, network_reads, cache_reads, unique_works, publishers, source_urls, findings, assessed_sources, cited_assessed_sources, verified_findings, corrected_findings, quoted_findings, dropped_findings, context_unchecked_findings, cited_findings}` (`models.py:84-111`); **null when the composition or the quality snapshot is missing** (`runtime/outcome.py:525`) — a run halted before the writer has neither | no |

Not on the response: `quality_status`, `query`, `max_iterations`/`max_extra_passes`, token usage, tool-call totals, any report structure.

Two client-side rules that follow from the delivery facts:

1. **The pass number is read from `graph.node.started.iteration` and `graph.extra_pass.started.iteration` only**, never from `researcher.tool_call.iteration` (the ReAct step index, `researcher.py:2612`); `/status.iteration` is used only after the stream has closed. The store copies `metadata.iteration` from every event (`sessions.py:68-69`), so its stored value takes ReAct step values mid-burst; because a node step is published synchronously and ends with `graph.node.completed`, a `/status` poll likely never sees them [INFERENCE], but any SSE consumer copying `iteration` from every event would.
2. **The active row is the successor of the last `graph.node.completed`, and a loop is keyed on `graph.route.decided`** (§4.1), because on the real stream a node's `graph.node.started` arrives only when the node has already finished, and the reviewer's own completion arrives after its route decision.

### 3.3 Events the design consumes

| `event_type` | Metadata keys | Factory |
|---|---|---|
| `graph.session.started` | `session_id`, `max_extra_passes`, `checkpointing` | `graph/events.py:281-296` |
| `graph.node.started` / `.completed` / `.skipped` | `node`, `iteration` (+ `event_count`, `error_count` on completed; `reason: "halted"` on skipped) | `events.py:50-88` |
| `planner.planning.completed` | `sub_topic_count`, `repair_attempted`, `stop_reason`, `iterations`, `tool_calls` (no target count) | `agents/planner.py:2826-2840` |
| `researcher.sub_topic.started` | `sub_topic`, `priority`, `index`, `existing_sources` | `agents/researcher.py:2573-2590` |
| `researcher.tool_call` | `sub_topic`, `tool`, `proposal_id`, `iteration` (ReAct step), `success`, `error_type` | `researcher.py:2592-2618` |
| `researcher.sub_topic.completed` | `sub_topic`, `index`, `stop_reason`, `iterations`, `tool_calls`, `cache_hits`, `findings`, `findings_dropped_duplicate`, `findings_dropped_cap`, `sources_retained`, `publishers_retained`, `source_urls_retained`, `findings_retained`, `works_retained`, `successful_reads`, `useful_evidence_yield`, `acquired_work_count`, `target_obligation_completed`, `elapsed_s` | `researcher.py:2621-2688` |
| `researcher.research.completed` | `sub_topics_planned`, `sub_topics_researched`, `sub_topics_skipped`, `findings` (on an extra pass `researched` and `findings` count the pass, `planned` the whole plan) | `researcher.py:2691-2720` |
| `source_evaluator.evaluation.started` | `finding_count`, `source_count` | `agents/source_evaluator.py:920-934` |
| `source_evaluator.evaluation.completed` | `source_count`, `average_score`, `low_confidence_count`, `unique_source_count`, `scored_count`, `unscored_cap_count`, `unscored_provider_count`, `unscored_missing_count`, `reputation_hits`, `reputation_failures` | `source_evaluator.py:937-963`; count keys `:546-560` |
| `evidence_verifier.verification.completed` | `verified`, `verified_corrected`, `quoted`, `dropped`, `context_unchecked` — counted over the findings judged **this pass** | `agents/evidence_verifier.py:1135-1150`, `:1011` |
| `report_writer.report.written` | `statements`, `citations`, `refused`, `fact_rows`, `not_found`, `table`, `parts`, `failed_parts` | `agents/report_writer.py:3093-3113` |
| `graph.quality.assessed` | `iteration`, `hard_failures`, `required_target_ids`, `answered_target_ids`, `missing_required_target_ids`, `uncited_settled_points` | `events.py:175-204` |
| `graph.report.reviewed` | `iteration`, `review_status`, `mean_score`, `material_defects`, `reviewed_statements`, `input_fingerprint`, `reused` | `events.py:207-242` |
| `graph.route.decided` | `destination`, `reason`, `iteration`, `max_extra_passes`, `missing_required_target_ids` | `events.py:91-121`; emitted by the reviewer node `nodes.py:829-835` |
| `graph.extra_pass.started` | `iteration` (already advanced), `max_extra_passes`, `targets[]` | `events.py:124-144`; `nodes.py:1144-1148` |
| `graph.report.redraft_requested` | `iteration` (unchanged), `redrafts`, `material_defects` | `events.py:147-171`; `nodes.py:1203-1207` |
| `graph.report.published` | `quality_status`, `report_path`, `evidence_path`, `quality_path`, `document_writes`, `memory_writes`, `error_count` | `events.py:245-278` |
| `graph.session.completed` | `status`, `iteration`, `error_count`, `has_report` | `events.py:299-318` |

Order within one pass, as the graph records it: `graph.node.started(report_writer)` → `report_writer.report.written` → `graph.node.completed(report_writer)` → `graph.quality.assessed` → `graph.node.started(report_reviewer)` → `graph.report.reviewed` → `graph.route.decided` → `graph.node.completed(report_reviewer)` (`nodes.py:300-313`, `:819-841`). The reviewer emits a `graph.route.decided` on every pass, including the re-review after a redraft (confirmed by the round-1 replay of `scoped-redraft-after-a-named-defect`).

Recoverable error types the rail groups (never halts): `report_writer_statement_check_failed` (`report_writer.py:2023`), `evidence_verifier_context_check_failed` (`evidence_verifier.py:1106`), `evidence_verifier_statement_check_failed` (`:1405`, `:1421`), `report_writer_section_failed`, `report_writer_bottom_line_failed` (`report_writer.py:2048-2105`). `ResearchError` carries `error_type`, `source`, `message`, `recoverable` (default true), `timestamp`, `details` (`utils/types.py:1345-1351`).

### 3.4 Configuration and overrides

| Fact | Where |
|---|---|
| Overrides are nested objects merged key by key; unknown paths raise; `llm.model_overrides` is a free-form mapping whose entries are **replaced whole** (`dict.update`) | `utils/config.py:661`, `:664-686`, `:689-698` |
| Per-role `model_overrides.<role>.reasoning_effort` wins over `llm.reasoning_effort`; a role's `timeout` comes only from its own entry | `config.py:73-75`, `:85-107` |
| Configured values: provider `deepseek`, model `deepseek-flash`, thinking `enabled`, global effort `high`; per role: planner max, researcher high, source_evaluator high, evidence_verifier high, report_writer high, report_reviewer max | `config.yaml:6-16`, `:32-63` |
| DeepSeek capability `^deepseek-(flash\|v4-flash\|v4-pro)$`, modes `enabled,disabled`, efforts `high,max`, `disabled_effort=None` | `providers/capabilities.py:71-77`; resolver `:253-278` |
| Offline probe results reported by the brief (mechanism verified in the lines above; probes not re-run here): `{"llm":{"model":…}}` and `{"llm":{"thinking_mode":…}}` apply to every role; `{"llm":{"reasoning_effort":"max"}}` has **no effect** (every role has its own effort); `{"llm":{"model_overrides":{"researcher":{…}}}}` replaces the whole entry (the researcher's timeout became `None`); `{"llm":{"provider":"openai"}}` is accepted by validation; thinking disabled sends `reasoning_effort=None` | brief, 2026-09-26 |

### 3.5 Published artifacts

Reader report (`agents/report.py:1573-1609`; spec 2026-09-25 §3), in order:

| Line / block | Exact form | Where |
|---|---|---|
| Title | `# {question}` | `report.py:1581` |
| Evidence line | `Evidence as of {YYYY-MM-DD} · {n} source(s)` or `No source could be checked.` | `report.py:1066-1072` |
| Bottom line | `## Bottom line` + one paragraph, 2–4 sentences, markers before the stop | `report.py:1582` |
| Table (optional) + italic caption | options table: `Option`, ≤4 part columns, `Recommended by`, ≤8 rows; or findings table: `What was measured \| Result \| Who reported it (and when) \| Source`, ≤12 rows | caps `agents/report_table.py:51-54`; headers `:63-70`; `report.py:1584-1587` |
| Parts | `## {part title}` + `- {point} [n].` bullets, plan order | `report.py:1589-1593` |
| `## What we couldn't confirm` (omitted when empty) | plain sentences | `report.py:1595-1598` |
| `## Sources` (omitted when empty) | ordered list `n. Publisher — [Title](url) (date)` | `report.py:1600-1602`; `e2e_evaluation/runner.py:55` (`^(\d+)\. (.*)$`) |
| Link line (last) | `How this was researched: [evidence log]({report-…-evidence.md})` | `report.py:1604-1608` |
| Unchecked kept sentence with a fact row | ends ` (figure: {who reported it (and when)})` | `report.py:1168` |

Evidence log (`report.py:1903-2004`): `# Evidence log: {question}`; `## About this report`; `## Verified figures`; `## Findings` — per finding `### {label} — {source title}`, `- Source:`, `- Read:`/locator, `- Snippet:`, optional `- Passage:`, optional `- Disputes: yes`, `- Verification: verified | verified with corrections | quoted (snippet found on the page; not checked for context) | dropped ({reason}) | not checked` with `; context unchecked` appended when flagged (`:1928-1935`, `:1953`), then one line per figure `kept; period …; scope …; {label}[; evidence words: "…"][; corrected]` or `dropped ({reason})` (`:1954-1968`); `## Not found` — target question, searched queries, pages read, or `- Not searched in this run.` (`:1976-1987`); `## Pages that could not be opened`; `## Refused sentences` — `"{text}" (cited {labels}): {reason}` (`:1993-1995`); `## Dropped option marks`; `## Unplaced findings`.

Quality record: `render_quality_record` (`report.py:531`) writes `"artifacts": {name: sha256}` for each published document (`report.py:634`, `:347-357`).

### 3.6 Offline replay harness

`src/deep_research/e2e_evaluation/replay.py` (`run_replay_scenario(scenario, root=…)` `:3557-3643` → `ReplayRun.state.events`, `replay.py:2070-2078`, `utils/types.py:1960`; `network_denied()` and `offline_credentials()` context managers) and `replay_matrix.py` (`scenario_by_id` `:3588-3589`; 35 manifest entries, confirmed by importing `REPLAY_CASE_IDS` offline). Network is denied and the completer is scripted, so a run costs nothing. Cases this spec uses: `missing-target-triggers-one-extra-pass` (`:1617`, expected `accepted / 0`, `:3335-3338`), `scoped-redraft-after-a-named-defect` (`:2886`, `:3533-3536`), `extra-pass-finds-nothing` (`:739`; ends `completed` at iteration 1 with routes extra_pass@0 then report_accepted@1 and one not-found target — round-2 replay), `review-unavailable` (`:1122`, `partial / 4`), `empty-but-clean` (`:1228`, `partial / 4`). **No case is a halted run, and no replayed case emits `graph.node.skipped`** (round-1/2 replays: 72, 49 and the extra-pass-finds-nothing events). Questions are synthetic ("Acme widget").

### 3.7 The package as it stands (anchors the plan edits)

| Anchor | Where today |
|---|---|
| `:root{…}` block; tokens `--meta: #5c5c5e` (`:13`), `--warn: #f59e0b` (`:16`), `--status-warn` (`:48`), `--reading-max`/`--rail`/`--sidebar*`/`--topbar` (`:50-54`) | `docs/design/prototype/index.html:11-55` |
| Layout CSS the width checks rest on: `.app` grid `var(--sidebar) minmax(0,1fr)` (`:78`); `.viewport` gutters `--container-gutter-desktop` 24 px (`:211`); `.with-rail` grid `minmax(0,1fr) var(--rail)` with a `--space-8` 32 px gap (`:319`); `.card` padding `--space-5` 20 px + 1 px border (`:651`); `.prose{max-width:var(--reading-max)}` 720 px (`:831`); `≤ 1080 px` sidebar becomes a drawer (`:962-977`); `≤ 900 px` phone gutter 16 px (`:978-979`) | `index.html` |
| Composer model/thinking/effort segments | `index.html:1110-1128` |
| Running pipeline card: header block `#runNow`, `#runningBlurb`, `#runProgressLabel`, `#runPasses`, `#runTrack`; then `#spineWrap[data-loop]` + `#spine` | `index.html:1197-1211` |
| Report head bar `#reportMeta`, `Download Report`, `Open LangSmith trace`; body card with Executive Summary; transcribed citations table | `index.html:1233-1296` |
| Rail: Quality Snapshot, Session Facts, Cost and Usage, Errors | `index.html:1296-1334` |
| Failed stage `#failedType`, `#failedMessage`, facts, `#spineFailed`, "What survived the halt" (`findings`, `sources evaluated`, `claims checked` as `0`) | `index.html:1344-1390` |
| `settings` object; `SESSIONS` fixtures (6); `STATUS` table; `passNumber()` | `index.html:1429-1430`, `:1451-1482`, `:1484-1490`, `:1496-1499` |
| `REFINE_MIN/MAX/DEFAULT` = 1/5/3 | `index.html:1986-1988` |
| `STAGES` (7 rows, old ids/captions; Planning caption `3–7 sub-topics`) | `index.html:2213-2221` |
| Arc: `loopRows()` (anchors `critic` → `planner`), `drawLoop()`, `setLoopState()` (`off`/`flowing`/`settled`, `.loop-base/.loop-flow/.loop-head`) | `index.html:2263-2313` |
| `syncSpine()` with the `↺` mark hard-coded to the researcher row | `index.html:2386-2410` (`:2405-2407`) |
| Scripted stream `buildEvents()`; `weigh()`; `applyEvent()`; `currentStageIndex()`; blurbs; one shared `play` state | `index.html:2450-2530`, `:2538-2542`, `:2587-2657`, `:2659-2676`, `:2566` |
| `meterClass()` (`v > 0.8` is green) / `paintMeters()` | `index.html:2816-2825` |
| Client-injected Limitations lines for partial sessions | `index.html:2901-2904` |
| `window.drConsole` (`open`, `sessions`, `settings`, `stage`, `motion`, `submit`, `finish`) | `index.html:3049-3061` |
| No Report \| Evidence toggle exists in the prototype yet (the mode is a §2 decision only) | `index.html` (grep: none) |
| `states.html`: chips `:174-203`; contrast `:207-222`; failed `:377-462` (survived card `:418-427`, event tail `:446-455`); partial `:466-566` | `docs/design/prototype/states.html` |
| Render script: `shot(name, expectedStage)` asserts `drConsole.stage()`; captures 01–07; viewports `W = 1252, H = 853`, `PHONE_W = 390, PHONE_H = 844` | `scripts/render_design_reference.mjs:27-28`, `:139-147`, `:160-206` |

---

## 4. Design

### 4.1 S1 — Running stage

**Delivery model this stage is designed for.** On the real stream a node's events arrive as one burst when the node finishes (§3.1). The prototype plays one event per tick as a pacing compression (`DESIGN.md:656-661`). Every rule below is written so the screen reads the same whether the events arrive one at a time, in bursts, or as a 60-event replay: the state after event *k* depends only on events 1..k.

**Active row rule.** The active ("Now") row is the **successor of the last `graph.node.completed`** along the route, never the last `graph.node.started` — except that a loop is keyed on the route decision, which the reviewer emits before its own completion:

| Trigger | Active row |
|---|---|
| no `graph.node.completed` yet | 1 Planning |
| `graph.node.completed` for `planner` → `researcher` → `source_evaluator` → `evidence_verifier` → `report_writer` | the next row (2, 3, 4, 5, 6) |
| `graph.route.decided` (emitted inside `report_reviewer`, before its completion, `nodes.py:819-841`) | the destination's row **immediately**: `extra_pass` → 2 Researching (rows 2–6 reset to hollow); `redraft` → 5 Writing (rows 5–6 reset); `finalize` → 7 Publishing; `end` → none (halted: the failed stage follows) |
| `graph.node.completed` for `report_reviewer` | **inert in a looping pass**: after a `graph.route.decided` with destination `extra_pass` or `redraft` it neither marks row 6 `done` nor moves the active row; after `finalize` or `end` it marks row 6 `done` as any completion does |
| `graph.node.completed` for `extra_pass` / `writer_redraft` (hop nodes) | unchanged (already set by the route) |
| `graph.node.completed` for `finalize_report`, or `graph.session.completed` | none; the stage transition follows |

**Header block** (same position, `index.html:1198-1208`):

| Element | Reads | Source |
|---|---|---|
| `Now / {stage}` (`#runNow`) | the active row's label | active row rule |
| one-line blurb (`#runningBlurb`) | the row's blurb (table below) | — |
| `stage n of 7` (`#runProgressLabel`, `#runTrack` `aria-valuemax=7`) | ordinal of the active row | active row rule |
| `pass p of P` (`#runPasses`) | `p = iteration + 1` from the last `graph.node.started` / `graph.extra_pass.started`; `P = 1 + max_extra_passes` from `graph.session.started` (fallback: the value the composer submitted) | `events.py:56`, `:140-142`, `:294` |
| loop tag (new, `#runLoopTag`, hidden when none) | **extra pass**: tag `extra pass` (text and border `--status-warn`, the text-safe amber `.pop-note.warn` already uses, `index.html:798`) + `{n} required targets had no verified finding`, `n = graph.extra_pass.started.targets.length`. **redraft**: tag `redraft` (text `--muted`, border `--meta`) + `Reviewer named {n} material defects`, `n = graph.report.redraft_requested.material_defects` | `events.py:137-143`, `:162-170` |

A loop tag appears on its `graph.extra_pass.started` / `graph.report.redraft_requested` event and clears on the next `graph.route.decided` or `graph.session.completed`. A redraft does not change `iteration` (`nodes.py:1204`), so the chip stays `pass 1 of 2` while the grey tag shows; an extra pass advances it (`nodes.py:1141-1146`).

**Counters block** (R1) — a compact two-column key/value list (`--text-sm`, mono figures; one column at ≤ 900 px) inside the pipeline card, directly under the header block (after `#runTrack`, before `#spineWrap`), with the eyebrow `counted from the event stream`. No layout change; the pipeline stays the centred column (`DESIGN.md:139`). Counters **update once per node step**: the design text says so, and during the researcher — the longest stage — the research rows read `not yet` until research finishes.

| Row | Scope | Value | From |
|---|---|---|---|
| sub-topics researched | this pass | `{count} this pass` while only `researcher.sub_topic.completed` events have arrived; then `{sub_topics_researched} of {sub_topics_researched + sub_topics_skipped}` from `researcher.research.completed` | `researcher.py:2665`, `:2715-2717` |
| tool calls | whole run | number of `researcher.tool_call` events, labelled `researcher only` | `researcher.py:2606` |
| findings | this pass | `researcher.research.completed.findings` | `researcher.py:2718` |
| sources scored | whole run | `source_evaluator.evaluation.completed.source_count` | `source_evaluator.py:955` |
| verified / corrected / dropped | this pass | `evidence_verifier.verification.completed.verified` / `.verified_corrected` / `.dropped` | `evidence_verifier.py:1142-1145` |
| sentences / refused | current draft | `report_writer.report.written.statements` / `.refused` | `report_writer.py:3104-3106` |
| review score | latest review | `graph.report.reviewed.mean_score`, two decimals; muted `not scored` when null | `events.py:236` |

Rules: a counter whose event has not arrived is muted `not yet`, never `0`. On `graph.extra_pass.started` the this-pass rows reset to `not yet` and the block's caption reads `pass p`; on `graph.report.redraft_requested` the current-draft rows and the review score reset to `not yet`. The old `claims checked` counter is removed. These are the same rows the failed stage freezes (§4.2).

**Spine** — 7 rows, same positions, `data-stage` = node name:

| # | `data-stage` | Label | Caption (static → dynamic) | Blurb (I) |
|---|---|---|---|---|
| 1 | `planner` | Planning | `1–10 sub-topics` → `{sub_topic_count} sub-topics` after `planner.planning.completed` | Turning the question into sub-topics and evidence targets. |
| 2 | `researcher` | Researching | `search · scrape · read · memory`; during an extra pass `{n} missing targets only` | Searching and reading; every finding keeps a verbatim snippet. / extra pass: Researching the {n} targets still missing a verified finding. |
| 3 | `source_evaluator` | Evaluating sources | `authority · recency · relevance` | Scoring every source behind the findings. |
| 4 | `evidence_verifier` | Verifying evidence (was Checking claims) | `snippet on page · context check` | Checking each snippet is on its page, then each figure's context. |
| 5 | `report_writer` | Writing report | `verified findings only · statement check` | Drafting from verified findings; every sentence is checked against what it cites. |
| 6 | `report_reviewer` | Reviewing (was the Critic row) | `7 dimensions · accept at mean 0.80` | Scoring the report; accepted at a mean of 0.80 with no material defect. |
| 7 | `finalize_report` | Publishing | `report · evidence log · quality record` | Publishing the report, the evidence log and the quality record. |

Row states: a row is `done` on its `graph.node.completed` — **except row 6, whose completion is inert after a loop decision** (active row rule) — `skipped` on its `graph.node.skipped`, `active` by the active row rule, otherwise `pending`; `loop` = re-armed and completed again. A `graph.route.decided` with destination `extra_pass` resets rows 2–6 to hollow; with destination `redraft` it resets rows 5–6; those rows stay hollow until their own later events, so after the reviewer's burst the spine reads Planning `done`, Researching `active`, rows 3–6 hollow (extra pass), or rows 1–4 `done`, Writing `active`, row 6 hollow (redraft) — never a `done` Reviewing under hollow rows (the "progress moving backwards" defect of `DESIGN.md:711-718`). Node events for `extra_pass` and `writer_redraft` are consumed by the pass logic and never map to a row; the arcs are driven by `graph.route.decided` plus the hop's own event (table below). The `↺` mark goes on the **first re-armed row**: `researcher` for an extra pass, `report_writer` for a redraft (today hard-coded to the researcher, `index.html:2405-2407`). Step-state visuals are unchanged (`DESIGN.md:622-628`).

**Arcs** (Q3) — the existing SVG (`.loop-base`, `.loop-flow`, `.loop-head`), states `off` / `flowing` / `settled`, and motion tokens are reused; `loopRows()` takes the pair as an argument instead of hard-coding `critic` → `planner`:

| Arc | `flowing` on (rows reset and the active row moves to the destination at the same instant) | `settled` on | Cleared on | From → to (`data-stage`) | Stroke | Rows reset to hollow |
|---|---|---|---|---|---|---|
| extra pass | `graph.route.decided` with `destination: "extra_pass"` | `graph.extra_pass.started` | `graph.session.completed`, or the next `graph.route.decided` | `report_reviewer` → `researcher` | `--warn` (`index.html:16`) | 2–6; Planning keeps `done` |
| redraft | `graph.route.decided` with `destination: "redraft"` | `graph.report.redraft_requested` | same | `report_reviewer` → `report_writer` | `--meta` (`index.html:13`) | 5–6; rows 1–4 keep `done` |

`#spineWrap` carries `data-arc="extra_pass"|"redraft"` beside `data-loop`. At most one arc is lit; a new loop destination replaces the previous arc. `--meta` is a stroke only; no text is set in it. A one-pass run never shows either arc. The old `destination: "refine"` trigger, `graph.refinement.started` handler and the Planning anchor are removed (`index.html:2523-2525`, `:2645-2648`, `:2266-2267`). Under `prefers-reduced-motion` both arcs arrive already lit, as the single arc does today (`DESIGN.md:648`).

**Topbar chip**: `Running · pass p of P` (was `iteration i of n`); budget 0 reads `pass 1 of 1`.

### 4.2 S2 — Statuses, chip, failed stage

| API `status` | Also read | Chip label | Dot (unchanged) | Chip note |
|---|---|---|---|---|
| `running` | pass number from the stream (§3.2 rule 1) | Running | `dot-live` | `pass p of P` |
| `completed` | `semantic_review_score`, `coverage.not_found_target_ids` | Completed | `dot-ok` | `review accepted · {score}` + not-found clause |
| `max_iterations` | `coverage` | Partially completed | `dot-warn` | `extra passes used` + not-found clause |
| `incomplete` with `semantic_review_status == "scored"` | `semantic_review_score` | Partially completed | `dot-warn` | `not accepted · {score}` (R3) |
| `incomplete` with `semantic_review_status` ∈ {`incomplete`, `provider_failed`, null} | — | Partially completed | `dot-warn` | `review unavailable` |
| `failed` | `errors` | Failed | `dot-danger` | `halted` |

Not-found clause: `n = coverage.not_found_target_ids.length`; omitted when `n = 0`; `· 1 target not found` at 1; `· {n} targets not found` above 1. (`max_iterations` can end with an empty list when only the review's own coverage defect remained, `state.py:312-313`.) Scores print with two decimals. `completed` ⇔ route `report_accepted` (`state.py:116`, `:322-337`), so the old `quality_status` inference and the notes `quality gates accepted`, `budget exhausted`, `no critique recorded` (`index.html:1486-1488`) go. Every partial outcome opens the report stage; only `failed` opens the failed stage.

**Failed stage** (same layout, `index.html:1340-1390`):

| Block | Content |
|---|---|
| Headline (`#failedType`) | the halting type in plain words: `graph_planning_failed` → Planning failed; `graph_provider_configuration_error` → Model provider misconfigured; `graph_agent_configuration_error` → Agent misconfigured; `graph_invalid_agent_state` → Invalid agent state; `graph_invalid_route` → Invalid route; `graph_request_attempt_limit_exceeded` → Request attempt limit reached. The enumerated `error_type` stays in the facts list; the API `message` stays the sentence beneath (`DESIGN.md:916-940`) |
| Why nothing was published | (I) `A halted run skips publication: no report, evidence log or quality record was written.` Facts rows keep `report_path · Not published` and `GET /report · 409 report_unavailable` (`app.py:258-261`); there are no download buttons, disabled or otherwise |
| Where it stopped (`#spineFailed`) | the halting node's row (the last `graph.node.started` with no `graph.node.completed`) marked `active`-at-halt; every row with a `graph.node.skipped` marked `skipped`; and **Publishing marked `skipped` by the client rule `graph.session.completed.status == "failed"`** — on `status` alone, not on `has_report`, which can be true when the halt came after a first-pass writer (`report_writer.py:3262`) — because the graph never runs `finalize_report` after a halt and no event exists for it (§3.1) |
| What survived | the S1 counters frozen at the halt, caption `counted from the event stream`; an unreceived counter reads muted `not reached`. The `claims checked` row and the literal `0`s (`index.html:1386-1388`) go |

Recoverable errors (`report_writer_statement_check_failed`, a failed Context Check batch, …) never reach this stage; they stay a grouped list in the report-stage rail, as today (`DESIGN.md:956-959`).

### 4.3 S3 — Composer, settings strip, submitted stage

Composer: same box, same six starter questions, same position, same `+` popover placement rules (`DESIGN.md:459-490` stay verbatim apart from the one phrase noted in §5). Controls and what they send:

| Control | Widget | Values | Sent as | Copy inside the popover |
|---|---|---|---|---|
| Model | segmented `#segModel`, three buttons | `deepseek-flash` (default, `config.yaml:7`), `deepseek-v4-flash`, `deepseek-v4-pro` (`capabilities.py:74`) | `config_overrides.llm.model` | none |
| Thinking | segmented `#segThinking` | `enabled` \| `disabled` | `config_overrides.llm.thinking_mode` | none |
| Effort | **read-only line** `#effortLine` (the `#segEffort` control is removed) | `effort per agent: planner max · reviewer max · others high` (`config.yaml:32-63`); with thinking off: `effort: not sent (thinking disabled)` (`capabilities.py:77`) | nothing | the line itself is the only copy (R4) |
| Extra passes | bounded stepper `#stepExtra` (`EXTRA_MIN = 0`, `EXTRA_MAX = 2`, `EXTRA_DEFAULT = 1`; minus disabled at 0, plus at 2) | 0 · 1 · 2 | top-level `max_iterations` (`models.py:48`; `app.py:183`) | none |
| Output directory | free text, as before | | `config_overrides.output.directory` | as before |
| Provider | not a control | DeepSeek (configured) | — | none |

The popover explains nothing (`DESIGN.md:478-484`). The rationale — a global effort override is silently shadowed by the per-role entries and a per-agent override replaces that agent's whole entry and drops its timeout (`config.py:85-107`, `:664-686`); an extra pass is taken only for a required target with no verified finding, the server default is 1 and 2 is this console's ceiling; the provider override is accepted by validation but nothing lists valid combinations — is DESIGN.md §3.2 prose (§5).

Request body the composer sends, exactly:

```json
{"query": "…", "max_iterations": 1, "output_format": "markdown",
 "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"},
                      "output": {"directory": "output/"}}}
```

`settings` becomes `{ model, thinking, outputDir, extraPasses }` (`index.html:1429-1430`); `session.passes = 1 + extraPasses` is the `P` every surface reads; `passNumber()` stays the one place the zero-based offset lives (`DESIGN.md:873-915`).

**Settings strip** (`#runningOpts`, `#reportOpts`, `DESIGN.md:199-208`), mono chips in this order: `model deepseek-flash` · `thinking enabled` · `effort per agent` (or `effort not sent` when thinking is off) · `extra passes 1` · `out output/`. Values are shown as submitted; there is no server echo (api-gaps gap 1.3).

**Submitted stage**: unchanged — the question read back plus the strip, held ~2.2 s (`DESIGN.md:141-150`, `:1421-1426`).

**Runs submitted from the composer** play a one-pass accepted script for any budget, in the per-pass event order of the §4.6 `index.html` row: `graph.session.started` (`max_extra_passes` = the submitted budget), the seven nodes with their events, the reviewer's `graph.report.reviewed {review_status: "scored", mean_score: 0.86}` and `graph.route.decided {destination: "finalize", reason: "report_accepted"}`, then `finalize_report` with `graph.report.published` and `graph.session.completed {status: "completed"}`. Neither arc ever leaves `off`. The finished session then shows, on the report stage: chip `Completed · review accepted · 0.86` (the script's `mean_score`), coverage `n of n` with nothing not found, `evidence_counts` present, `pass 1 of P`.

### 4.4 S4 — Report stage and the Evidence view

**Header row** (same, `index.html:1233-1240`): `#reportMeta` = `session {id} · finished {finished_at} · {duration_seconds as m s} · pass {p} of {P}`; actions `Download Report`, `Open LangSmith trace` (external). `Download evidence log` is rendered **only** when the service serves the log's Markdown (E1 with `?format=markdown`, §4.5); today it serves the path (`evidence_path`) and no content, so the button is absent, never disabled.

**Toggle** (Q2): a two-button segmented control `Report | Evidence` (the `.seg` recipe of `index.html:1110`) in `.report-head-bar` between `#reportMeta` and the actions; `#stage-report[data-view="report"|"evidence"]`; `Report` is the default; the choice is per session and not persisted. The two buttons are a `role="group"` with `aria-pressed`, as the composer's segments are.

**Report side** — the server's Markdown is rendered as-is: the server owns wording, order, numbering and the table. Presentation-only adaptations:

| Markdown element | Rendering rule |
|---|---|
| `# {question}` | not repeated (the question is already the stage's `<h1>`, `index.html:1241`) |
| evidence line | one muted meta line (`.avail`) under the header row |
| `## Bottom line` + paragraph | the first card, in the old Executive Summary card's position (`index.html:1247-1251`), with its own `<h2>Bottom line</h2>` |
| table + `*caption*` | a real `<table class="tbl">` with the caption as `<p class="tbl-foot"><em>`. Options table (up to 6 columns: `Option`, ≤4 parts, `Recommended by`): inside a frame with `overflow-x: auto`, the `Option` column pinned (`position: sticky; left: 0`, card surface background). Findings table (4 columns): fits the desktop column without scrolling (534 px content box with the sidebar expanded, 720 px collapsed — §3.7 CSS row); at phone width (the `≤ 900 px` media query, `index.html:978`) it sits in the same `overflow-x: auto` frame without a pinned column, and the page itself never scrolls horizontally |
| `## {part}` + bullets | `<h2>` + `<ul>` inside the same prose card |
| `[n]` markers | each becomes `<a href="#src-n">[n]</a>`; text unchanged; a run `[1][2]` stays a run |
| `## What we couldn't confirm` | as-is |
| `## Sources` | `<ol>` with `id="src-n"` per item; the `[Title](url)` link opens in a new tab (`target="_blank" rel="noopener"`) |
| `How this was researched: [evidence log](…)` | the relative link is intercepted: with E1 it switches the toggle to `Evidence`; without E1 it renders as muted text with the same words |
| `(figure: …)` suffix on an unchecked sentence | as-is |

Removed from the report card: the transcribed citations table with scores (`index.html:1289-1296`) — sources are the server's ordered list; per-source scores live in the Evidence detail pane — and the client-injected Limitations lines for partial sessions (`index.html:2901-2904`), because `## What we couldn't confirm` is the server's.

**Rail** (same 300 px column; §3.6 meter-colour rule unchanged, `DESIGN.md:773-802`):

| # | Card | Content | Source |
|---|---|---|---|
| 1 | **Review** (replaces Quality Snapshot) | meter for `semantic_review_score` with the 0.80 acceptance line marked on the track; status text = the S2 chip note (`review accepted · 0.86`, `not accepted · 0.71`, `review unavailable`, `extra passes used`); second bar `Scored sources cited` = `cited_assessed_sources / assessed_sources`. Claim confidence, source authority and corroboration are gone. **Recorded on purpose:** a figure of exactly 0.80 paints its meter yellow, because §3.6 puts 0.80 in the middle band (`meterClass`: `v > 0.8` is green, `index.html:2818`) while review acceptance is `≥ 0.80` (`types.py:1131`); the plan must not "fix" either side. The fixtures that show it: `9ea4c220`'s `Scored sources cited` = 4 of 5 = 0.80, and the EVIDENCE finding `F03` whose source `overall_score` is 0.80 (§4.6) | `semantic_review_score`, `semantic_review_status`, `evidence_counts` |
| 2 | **Coverage** | `required targets answered {answered_targets} of {required_targets}`; `not found`: the ids of `not_found_target_ids` (ids only until E1 supplies the question text, then `T02 — {question}`) | `coverage` (`models.py:67-81`) |
| 3 | **Evidence** | eleven values: `findings`, `verified {verified_findings}`, `corrected {corrected_findings}`, `quoted {quoted_findings}`, `dropped {dropped_findings}`, `context unchecked {context_unchecked_findings}`, `cited {cited_findings}`; `reads {network_reads} network · {cache_reads} cache`; `unique works {unique_works}`; `publishers {publishers}`. When `evidence_counts` is null: muted `not measured` | `evidence_counts` (`models.py:84-111`) |
| 4 | **Session facts** | existing rows (status, started_at, finished_at, report_path, errors) plus `pass p of P` (replacing the bare `iteration`), `duration_seconds`, `evidence_path`, `quality_path`; errors grouped by `error_type` with the disclosure, as before | response fields |
| 5 | Cost and usage | unchanged (`Not recorded`): the response still carries no token or tool-call totals; the running-stage `tool calls` counter is a stream-derived, researcher-only figure and is not copied here | `DESIGN.md:1377-1393` |
| 6 | Errors | unchanged | |

**Evidence side** (Q4). The `.with-rail` grid is reused: the left column holds the filter chips and the list; the rail track holds the detail pane (sticky, stacks below 1120 px like the rail). Until E1 exists the real app shows one muted line, `not served by the service yet`, in place of the list; the prototype renders the target state from fixture data.

| Element | Rule |
|---|---|
| Filter chips | `All · Verified · Corrected · Quoted · Dropped · Not found · Refused`, each with its count; one active at a time; `All` default |
| Finding row | status pill (`verified` \| `corrected` \| `quoted` \| `dropped` \| `not checked` when the finding carries no verification, `report.py:1928-1929`) · label (`F06`) · the first sentence of the snippet, one line, ellipsised · one tag per `target_id` · secondary tag `context unchecked` when flagged. A `not checked` row appears only under `All` |
| Not-found row | pill `not found` · `{target_id}` · the target's question · `{n} queries · {n} pages read`, or `not searched in this run` when `searched` is false (`types.py:1521-1532`; `report.py:1987`) |
| Refused row | pill `refused` · the sentence · `cited {finding_labels}` (or `cited nothing`) · the reason (`types.py:1476-1486`; `report.py:1995`) |
| Selection | the first row is selected on entry; arrow keys move the selection; the selected row is `aria-selected` |
| Detail pane — finding | label + status line (the evidence-log verb, `report.py:1931-1935`); `Snippet` (quoted) and `Passage` where recorded; source title linking to the URL (new tab); Context Check line `{organisation} · {kind} · {release}` from the kept figure's context, or `context unchecked`; four small meters `authority · recency · relevance · overall` (§3.6 colours; muted `not scored` for null); one line per figure: `{value}: kept · period {period} · scope {scope}` or `{value}: dropped ({dropped_reason}) {reason}` (`types.py:368-400`) |
| Detail pane — not found | question; the searched queries as a list; the pages read as links |
| Detail pane — refused | full text; cited labels (each a link that selects that finding's row); reason; `where` |

### 4.5 S5 — `api-gaps.md` rewrite

Re-keyed to DESIGN.md §3's **five** stages plus the sidebar; this closes the README's known defect. New ids: `E1` for the evidence endpoint, then `{stage}.{n}` per stage (1 Idle, 2 Submitted, 3 Running, 4 Report, 5 Failed) and `SB.{n}` for the sidebar. The governing rule stays verbatim: where a value is unavailable the interface says so in muted text and never renders `0`, `—`, `null`, a placeholder or a disabled control. The header's route table lists the five routes of §3.2 and the 17 response fields, and states the delivery model of §3.1 (once per node step).

**Closed since 2026-09-16** (one line each, kept as a record):

| Old # | Gap | How it closed |
|---|---|---|
| 2.4 | `quality_status` | `completed` ⇔ `report_accepted` (`state.py:116`, `:322-337`); `semantic_review_status` splits the partial outcomes |
| 3.2 | quality snapshot | `coverage`, `evidence_counts`, `semantic_review_status`, `semantic_review_score` on the response (`sessions.py:73-128`) |
| 3.3 | `evidence_path` | the path is served; the content moves to E1 |
| 3.5 | claim verdicts and confidence | obsolete: no claims exist |
| 2.2 | tool-call counts | mostly closed: the verifier, writer and reviewer make no tool calls; `researcher.tool_call` misses only the planner's single budgeted call (`config.yaml:159`; `planner.py:2999`), whose count is on `planner.planning.completed.tool_calls` |

**New, listed first — E1 `GET /research/{id}/evidence`** (JSON by default; `?format=markdown` returns the published evidence log as `text/markdown`, which is what makes `Download evidence log` appear). Subsumes old 3.4, 3.6 and the coverage-id → question need. Ground truth: `ReportComposition` and `ResearchState`. Shape:

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

| Field group | Source type |
|---|---|
| finding label, status, `dropped_reason`, `context_unchecked` | `composition.finding_labels` (`types.py:1658`); `FindingVerification` (`types.py:413-419`) |
| `passage` | `composition.statement_passages` (`report.py:1940`) |
| `source` scores and statuses | `ScoredSource` (`types.py:625-640`); `organisation` = the Context Check's organisation or the page owner |
| `figures[]` | `FigureResult` + `FigureContext` (`types.py:368-400`); `release` as the evidence log prints it (`report.py:1961-1962`) |
| `not_found[]` | `NotFoundTarget` (`types.py:1521-1532`) |
| `refused[]` | `RejectedDraftPoint` (`types.py:1476-1486`) |
| `cited` | the reading `evidence_counts.cited_findings` sums |

**Still open, rewritten** (old ids in brackets; each keeps the six-column row shape of the current file):

| New # | Gap | Note |
|---|---|---|
| 1.1 | query echo [1.1] | |
| 1.2 | session list `GET /research` [1.2, 5.1] | the sidebar's collection route |
| 1.3 | effective settings echo [1.3], **now including per-role effort** | |
| 1.4 | `GET /capabilities` [1.4 + 1.5 merged] | the provider override is now accepted by validation, but nothing lists valid provider/model/mode/effort combinations |
| 1.5 | `GET /health` [1.6] | |
| 2.— | Submitted: nothing beyond stage 1 | stated explicitly |
| 3.1 | `max_iterations` echo [1.7], partially closed | `graph.session.started.max_extra_passes` is on the stream, not on `/status` |
| 3.2 | token usage absent on every stage [2.1] | |
| 3.3 | terminal stream frame [2.3] | |
| 3.4 | event identity / `Last-Event-ID` [2.6] | |
| 3.5 | halting-type vocabulary [2.5] | |
| 3.6 | shutdown-while-running status [2.7] | |
| 3.7 | **live per-event delivery** (new, R2) | events are published once per node step (`orchestrator.py:312-346`), so the running stage's counters and active row move once per node; live per-event delivery is listed for sub-project 2; workaround = the design's burst-safe rules (§4.1) |
| 4.1 | report body JSON [3.1], **downgraded to nice-to-have** | the consumer format is stable and parseable (H2 sections; source lines `^(\d+)\. `, `runner.py:55`) |
| 4.2 | report hash on the response [3.7] | quality.json already hashes both Markdown documents (`report.py:347-357`, `:634`); the response does not expose it |
| 5.1 | what survived a halt [4.1] | workaround = the stream counters |
| 5.2 | reachable states [4.2/4.3] | the partial states are now reproducible for free via replay cases `review-unavailable` and `empty-but-clean`; halted and configuration-error still need a seeded test session or `/health` |
| SB.1 | a result summary per sidebar row [5.2] | |
| SB.2 | durability [5.3] | |

**Gaps the front end should not close**: the existing list (collaboration/sharing/orgs/billing; re-run/cancel; embedded trace viewer; rendered event log; any `0` for an absent value) plus **per-agent effort editing** — an entry override replaces the whole `model_overrides.<role>` entry and drops its timeout (`config.py:664-686`).

### 4.6 S6 — Files and verification

| File | Change |
|---|---|
| `docs/design/DESIGN.md` | §5 change map below; header revision note: *Updated 2026-09-26 to the Evidence Verifier pipeline (f27ac7e); the 2026-09-16 design is otherwise unchanged.* |
| `docs/design/README.md` | the same revision note; `open-design/` described as the 2026-09-16 history, now older than the prototype; the "Known defect in this package" section (`README.md:64-71`) removed because the api-gaps rewrite fixes it; the `prototype/index.html` row's line count and token-line range re-measured after the edit, counting lines as an editor does (`wc -l`, plus one when the file has no trailing newline — today: 3,066 newlines, last byte `>`, so 3,067 lines) |
| `docs/design/api-gaps.md` | rewritten per §4.5 |
| `docs/design/prototype/index.html` (same file, same `:root`) | `STAGES` ids/labels/captions (§4.1); blurbs; `STATUS` labels/notes (§4.2); `REFINE_*` → `EXTRA_MIN/MAX/DEFAULT` = 0/2/1 and `settings.extraPasses`; model list of three + `#effortLine`, `#segEffort` removed; `buildEvents()` rewritten with the real event names and metadata keys of §3.3 in the recorded order (per pass: `graph.session.started` → planner [`node.started`, `planner.planning.completed`, `node.completed`] → researcher [`node.started`; per topic `sub_topic.started`, `tool_call`…, `sub_topic.completed`; `research.completed`; `node.completed`] → source_evaluator [`node.started`, `evaluation.started`, `evaluation.completed`, `node.completed`] → evidence_verifier [`node.started`, `verification.completed`, `node.completed`] → report_writer [`node.started`, `report.written`, `node.completed`, then `graph.quality.assessed`] → report_reviewer [`node.started`, `graph.report.reviewed`, `graph.route.decided`, `node.completed`] → extra_pass [`node.started`, `graph.extra_pass.started`, `node.completed`] \| writer_redraft [`node.started`, `graph.report.redraft_requested`, `node.completed`] \| finalize_report [`node.started`, `graph.report.published`, `node.completed`, `graph.session.completed`]); **no halted branch in `buildEvents()`**; the halted fixture carries a static `HALTED_EVENTS` list (table below); `weigh()` keys; `applyEvent()` handlers (active row rule with the loop keyed on `graph.route.decided` and the reviewer's completion inert after it, counters with scopes and resets, tags, arcs); two arcs (`loopRows(from, to)`, per-arc stroke class, `data-arc`); the counters block inside the pipeline card; the `↺` rule; report body rewritten into the consumer-format skeleton with the same battery-storage example and a findings table; rail cards (§4.4); the Evidence view (toggle, chips, list, detail pane, fixture `EVIDENCE[sessionId]` in the E1 shape); fixture sessions (table below); the composer-submitted script (§4.3); `drConsole` additions (table below) |
| `docs/design/prototype/states.html` | chip cards: `Running · pass 2 of 2`, `Completed · review accepted · 0.86`, three partial chips (`extra passes used · 1 target not found`, `not accepted · 0.71`, `review unavailable`), `Failed · halted`; failed panel headline in plain words with the enumerated type beneath; the failed section's "What survived the halt" card (`:418-427`) shows the S1 counter rows with unreceived ones reading muted `not reached` (no literal `0`, no `claims checked`); its event tail (`:446-455`) becomes the eight real frames `graph.session.started`, `graph.node.started planner`, `graph.node.skipped` × 5 (researcher, source_evaluator, evidence_verifier, report_writer, report_reviewer), `graph.session.completed failed`, with the sentence saying eight frames and the halting error shown in the failed panel rather than as a frame; the partial section shows the three partial outcomes side by side, `pass 2 of 2` in place of `3 of 3 passes` (`:484`), an Evidence-counts card in place of `Claim verdicts` (`:511`), Review + Coverage in place of `Quality snapshot` (`:552`), `Bottom line` in place of `Executive summary` (`:543`), the `Reached when` and lead paragraphs (`:471`, `:475`) reworded to the §4.2 rules; a new **options-table fixture** (6 columns) demonstrating the horizontal-scroll frame with the pinned `Option` column; the contrast table (`:207-222`) unchanged |
| `docs/design/reference/` | 01–07 regenerated; new `08-evidence.png`, `09-running-extra-pass.png` |
| `scripts/render_design_reference.mjs` | two captures added, each asserting its stage (table below) |
| `docs/design/open-design/` | untouched |

Fixture sessions (ids reused where they exist; questions stay in the battery-storage set):

| id | `status` / route reason | `iteration` / `passes` (P) | review | coverage | `evidence_counts` | Script |
|---|---|---|---|---|---|---|
| `8f2c1d90` (running, playback) | ends `completed` / `report_accepted` | 0 → 1 / 2 | scored 0.86 | 4 of 4 | present at the end | **the extra-pass run**: pass 0 review names a missing target → `extra_pass` → pass 1 accepted |
| `c3d7e5f1` (new, playback) | ends `completed` / `report_accepted` | 0 / 2 | scored 0.84 | 3 of 3 | present | **the redraft run**: pass 0 review names 1 material defect → `redraft` → re-review accepts |
| `b41e77aa` | `completed` / `report_accepted` after the extra pass was spent (routes extra_pass@0, report_accepted@1 — the shape of replay case `extra-pass-finds-nothing`; a completed run with a not-found target must have spent its extra pass or had budget 0, `state.py:295-297`) | 1 / 2 (header `pass 2 of 2`) | scored 0.86 | 3 of 4, `not_found_target_ids: ["T04"]` (`state.py:82-83`) | present | — (chip `review accepted · 0.86 · 1 target not found`) |
| `7c0d13ff` | `max_iterations` / `extra_passes_exhausted` | 1 / 2 | scored 0.74 | 3 of 4, `["T04"]` | present | — |
| `5ff1ab07` | `incomplete` / `report_not_accepted` | 0 / 1 (budget 0) | scored 0.71 | 3 of 3 | present | — |
| `9ea4c220` | `incomplete` / `review_unavailable` | 0 / 2 | `provider_failed`, score null | 2 of 2 | present, with `assessed_sources: 5`, `cited_assessed_sources: 4` (`Scored sources cited` = 0.80, painted yellow) | — |
| `2ad900b1` | `failed` / `halted` (`graph_provider_configuration_error`, halted in the planner) | 0 / 2 | none | none | null | static `HALTED_EVENTS`: `graph.session.started`, `graph.node.started {node: "planner"}`, `graph.node.skipped` for `researcher`, `source_evaluator`, `evidence_verifier`, `report_writer`, `report_reviewer` (`reason: "halted"`), `graph.session.completed {status: "failed", has_report: false}`; consumed once by the failed-stage renderer, never played |

Every finished session reuses the one battery-storage report fixture (recorded in §7 as today); `EVIDENCE` carries one fixture set for that report with at least one row per status, one not-found target (`T04`) and one refused sentence, and its finding `F03` carries a source with `overall_score: 0.80` (its detail meter paints yellow). The two playback fixtures share the single `play` state (`index.html:2566`): opening one restarts its script from the beginning; the other keeps `status: "running"` until it is reopened.

`window.drConsole` additions (existing members unchanged):

| Member | Behaviour |
|---|---|
| `view(name?)` | with `"report"` / `"evidence"` switches the toggle; without an argument returns the current view |
| `evidence(sessionId)` | opens the session on the report stage and switches to the Evidence view |
| `advanceTo(eventType)` | plays the active playback session's script synchronously up to and including the first event of that type, then pauses |
| `arc()` | `"extra_pass"`, `"redraft"` or `null` (from `#spineWrap[data-arc]` when `data-loop !== "off"`) |
| `loop()` | the `data-loop` value: `"off"` \| `"flowing"` \| `"settled"` |

Render-script captures (all through `shot(name, expectedStage)`, which keeps asserting `drConsole.stage()`):

| Capture | Steps | Assertion |
|---|---|---|
| `08-evidence` | `open("b41e77aa")` → `view("evidence")` → 900 ms | `stage() === "report"` and `view() === "evidence"` |
| `09-running-extra-pass` | `open("8f2c1d90")` → `advanceTo("graph.extra_pass.started")` → 600 ms | `stage() === "running"`, `arc() === "extra_pass"`, `loop() === "settled"` |

**Verification** (performed by the implementation plan, results recorded in its summary, nothing committed from step 1):

1. **Offline replay.** A throwaway script (deleted afterwards) runs `run_replay_scenario(scenario_by_id(case), root=<temp dir>)` for `missing-target-triggers-one-extra-pass` and `scoped-redraft-after-a-named-defect` inside `network_denied()` and `offline_credentials()` (`replay.py`), using `.venv/Scripts/python.exe` with `PYTHONPATH=src` and `PYTHONDONTWRITEBYTECODE=1`; it collects `{event_type: set(metadata keys)}` from `run.state.events` over both runs and asserts (a) every `event_type` that `buildEvents()`, the composer-submitted script or `HALTED_EVENTS` emits is in the captured set, except `graph.node.skipped`; (b) for every such event, **every metadata key the prototype emits and every key its handlers read** is in the captured key set for that type; (c) `graph.node.skipped`'s keys equal `node_skipped_event("researcher", iteration=0).metadata.keys()` from `deep_research.graph.events` (the replay matrix has no halted case). The temp root is deleted. Never the live CLI; no provider call.
2. **Browser walkthrough** of the prototype: every stage; both arcs (extra pass via `8f2c1d90`, redraft via `c3d7e5f1`), checking after each reviewer burst that Reviewing stays hollow; a composer-submitted run at Extra passes 0; each Evidence filter and the detail pane for a finding, a not-found target and a refused sentence; `prefers-reduced-motion`; the phone viewport 390×844; collapsed sidebar; the options-table frame scrolling with the pinned column, in `states.html`.
3. **Stale-name grep** over the five authoritative files (`docs/design/DESIGN.md`, `api-gaps.md`, `README.md`, `prototype/index.html`, `prototype/states.html`; not `open-design/`), case-sensitive, from the worktree root:

   ```
   rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' \
      -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' \
      -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' \
      -e '[Cc]orroboration' -e '[Cc]laim confidence' \
      docs/design/DESIGN.md docs/design/api-gaps.md docs/design/README.md \
      docs/design/prototype/index.html docs/design/prototype/states.html
   ```

   must print nothing. Today (`rg -c`, same patterns) it prints 39 lines in `DESIGN.md`, 2 in `api-gaps.md`, 0 in `README.md`, 59 in `index.html` and 6 in `states.html` — 106 lines, every one inside a section, comment, fixture or code block this spec rewrites (examples: `DESIGN.md:76`, `:448`, `:513`, `:890`; `index.html:1485`, `:2217`, `:2524`; `states.html:176`, `:483`) — and none of the kept text: `-webkit-line-clamp is the refinement` (`DESIGN.md:267`, in frozen §3.1), and `Critical minerals` / `Critical Minerals` in the battery-storage example (`index.html:1258`, `:1282-1285`). `iteration \S+ of` also catches the templated chip texts (`iteration {i} of {n}`, `iteration n of 5`). No exemption list is needed.
4. `node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference` reports `9/9 captures verified`.

---

## 5. DESIGN.md change map

Rewrite only the workflow-coupled parts; everything else stays verbatim.

| Section (lines today) | Action | Content after the change |
|---|---|---|
| Header (`:1-21`) | edit | revision note (§4.6); grounding list adds `graph/events.py`, `agents/report.py`, the two workflow specs |
| §1 (`:23-45`) | rewrite facts 2 and 4 | fact 2: `max_iterations` and `incomplete` still produce a report; `max_iterations` now means the extra-pass ceiling was spent; fact 4 adds `evidence_counts` null without a composition or quality snapshot, `semantic_review_score` null = no score |
| §2 A (`:54-71`), C (`:91-108`), recommendation (`:110-127`) | rewrite A's description, all of C, the recommendation's second paragraph | A: bottom line, table, parts, what we couldn't confirm, sources; the rail carries review, coverage, evidence counts and session facts; C = **Evidence ledger**: a flat list of findings with status filters, a target tag per row and a detail pane; B's row examples use real event names; recommendation stays "A, with C as a mode"; the prototype now implements the mode |
| §3 table + prose (`:129-197`) | edit | stage 4 "any terminal status with a report": the three partial outcomes named; `:139` stays true (the pipeline, centred; no rail); `:161-169` gains one sentence: the counters now sit inside the pipeline card under its header, update once per node step, and read `not yet` until their node finishes |
| §3.0 (`:199-208`) | rewrite | the strip: model · thinking · effort per agent · extra passes · out (§4.3) |
| §3.1 (`:210-277`) | verbatim | |
| §3.2 composer (`:279-455`) | rewrite the controls table (`:443-449`) and the effort paragraph (`:451-455`); add the rationale prose of §4.3 (R4) | §4.3 table; effort is read-only and why; extra-pass semantics; provider override accepted but unlisted |
| "The refinement budget" heading (`:457`), stepper paragraphs (`:491-516`), output/provider paragraph (`:518-523`) | rewrite | **The extra-pass budget**: floor 0 (`ge=0`), ceiling 2 (UI), default 1 (config); `pass p of P` with `P = 1 + budget`; the loop runs only for missing required targets; the composer-submitted script |
| Popover placement rules (`:459-490`) | verbatim except one phrase | `:482-484` "the effort control explaining why it is unavailable when thinking is off" becomes "the read-only effort line and its thinking-off text" (R4 left no effort control); listed as an AC17 exception |
| §3.3 (`:525-584`) | verbatim | |
| §3.4 (`:586-661`) | edit the step-state table's `loop` row (`:627`) and the pacing paragraph (`:656-661`) | `loop` = re-armed by an extra pass or a redraft; pacing weights per new node; the real stream's once-per-node bursts |
| §3.5 (`:663-771`) | rewrite | **Passes, extra passes and redrafts**: the active-row rule with the loop keyed on `graph.route.decided` and the reviewer's own completion inert after it; two arcs with triggers, strokes (`--warn`, `--meta`), anchors and reset rules (§4.1); the loop tag in the header; the `↺` rule; the pass-track history paragraph updated to `graph.extra_pass.started`; "Why this says nothing in prose" kept, with `pass p of P` as the one counter; the `:711-718` "progress moving backwards" lesson kept and extended to the reviewer's completion |
| §3.6 (`:773-802`) | **deliberate deviation from the brief's "unchanged"**: edit the first sentence (`:775-776`) and the anecdote's `claim-confidence row` (`:790`) | the meters: review score, scored sources cited, and the four source scores in the Evidence detail; the anecdote says "the first row was green"; the 0.80-is-yellow note of §4.4 with its two fixtures; thresholds and rule text unchanged |
| §4 table + rules (`:804-847`) | rewrite | §4.2 table and clause rules; rule 2 becomes `completed` ⇔ `report_accepted`; rule 6 unchanged |
| "Derived stage display" (`:849-871`) | rewrite | seven rows + `extra_pass` / `writer_redraft` as hops (not rows); the active row is the successor of the last `graph.node.completed`, with the loop keyed on `graph.route.decided`; Publishing `skipped` by the `status == "failed"` rule |
| "`iteration` in two places" (`:873-914`) | edit | `pass p of P`; the §3.2 rule that only `graph.*` events move the pass number |
| "Error rendering" (`:916-959`) | edit | halting-type headline in plain words; fixture error types are the real ones (`report_writer_statement_check_failed`, `evidence_verifier_context_check_failed`, `graph_provider_configuration_error`) |
| §5.1–5.3 (`:967-1093`) | verbatim | |
| §5.4 (`:1094-1149`) | edit one sentence | the rail track also hosts the Evidence view's detail pane; no new custom property; no running-stage rail |
| §5.5–5.6 (`:1151-1360`) | verbatim | |
| §5.7 (`:1362-1375`) | rewrite | the event sources of §3.3, the once-per-node delivery model (`orchestrator.py:312-346`) and what each surface derives from the stream |
| §5.8 (`:1377-1393`) | rewrite | **Counted from the event stream**: the S1 counter rows with their scopes and resets, the `not yet` / `not reached` rule, once-per-node updates, and why a counters block is honest now (real values arrive during the run, one node at a time); token usage still `Not recorded` |
| §6 (`:1395-1412`) | rewrite | summary table keyed to the five stages, E1 first, live delivery listed under Running |
| §7 (`:1414-1471`) | edit | `drConsole` additions; the fixture sessions and `HALTED_EVENTS`; the Evidence fixture; the one-report-for-all-sessions limitation kept |

---

## 6. Acceptance criteria

| # | Criterion (checkable by the reviewer) |
|---|---|
| AC1 | The stale-name command of §4.6 step 3 prints nothing over the five authoritative files. |
| AC2 | `STAGES` in `index.html` has exactly the seven `data-stage` ids `planner, researcher, source_evaluator, evidence_verifier, report_writer, report_reviewer, finalize_report`, in that order, with the labels and static captions of §4.1. |
| AC3 | The throwaway replay check of §4.6 step 1 passes: every event type and every emitted or read metadata key of `buildEvents()`, the composer-submitted script and `HALTED_EVENTS` is in the two-case capture, with `graph.node.skipped` checked against `node_skipped_event(...)`; the script is not in the tree afterwards. |
| AC4 | With `8f2c1d90` open, at the instant of `graph.route.decided {destination: "extra_pass"}` the spine rows 2–6 are hollow, row 1 is `done`, row 2 is `active`, `#spineWrap` has `data-loop="flowing"` and `data-arc="extra_pass"`, the arc stroke is `--warn` and runs from the `report_reviewer` bullet to the `researcher` bullet; after the reviewer's own `graph.node.completed` that follows, row 6 is still hollow and row 2 still `active`; after `graph.extra_pass.started` the loop is `settled`, the header shows the `extra pass` tag with `{n} required targets had no verified finding`, `#runPasses` reads `pass 2 of 2`, row 2's caption reads `{n} missing targets only`, the counters block caption reads `pass 2`, and its this-pass rows read `not yet`. |
| AC5 | With `c3d7e5f1` open, at the instant of `graph.route.decided {destination: "redraft"}` rows 5–6 are hollow, 1–4 stay `done`, row 5 is `active`, `data-arc="redraft"`, the arc stroke is `--meta` from `report_reviewer` to `report_writer`; after the reviewer's own `graph.node.completed` that follows, row 6 is still hollow and row 5 still `active`; after `graph.report.redraft_requested` the header shows the `redraft` tag with `Reviewer named {n} material defects`, `#runPasses` still reads `pass 1 of 2`, and the `↺` mark appears on row 5 once it completes again. |
| AC6 | A run submitted from the composer with Extra passes 0 plays to `graph.session.completed` with `data-loop` never leaving `off` and the chip reading `Running · pass 1 of 1` throughout; the finished session shows `Completed · review accepted · 0.86`, coverage `n of n` with no not-found clause, and an Evidence card with values (not `not measured`). |
| AC7 | The composer's popover has three model buttons defaulting to `deepseek-flash`, no effort control, the read-only effort line whose text changes to `effort: not sent (thinking disabled)` when thinking is off, no other explanatory copy, and an Extra passes stepper whose minus disables at 0 and plus at 2, starting at 1; the submitted request body matches §4.3 exactly for those values. |
| AC8 | The settings strip on the submitted, running and report stages shows five chips in the order `model · thinking · effort · extra passes · out`. |
| AC9 | Topbar chip notes match the §4.2 table and clause rules for each of the seven fixture outcomes (`b41e77aa` reads `review accepted · 0.86 · 1 target not found` with the header `pass 2 of 2`; `5ff1ab07` reads `not accepted · 0.71`); the failed fixture opens the failed stage; the three partial fixtures open the report stage with a `Partially completed` chip and their own note. |
| AC10 | The failed stage for `2ad900b1` shows the headline `Model provider misconfigured`, `graph_provider_configuration_error` in the facts list, no download control, `#spineFailed` with row 1 `active`, rows 2–6 `skipped` from `HALTED_EVENTS` and row 7 `skipped` by the `status == "failed"` rule, and the survived block showing muted `not reached` for every counter whose event was not received (never `0`, no `claims checked`). |
| AC11 | The report card renders, in order: the muted evidence line, `Bottom line`, the findings table with an italic caption, one `<h2>` per part with bullets whose `[n]` markers are anchors to `#src-n`, `What we couldn't confirm`, an `<ol>` Sources list whose links carry `target="_blank"`, and the muted `How this was researched: evidence log` line; no `Executive Summary`, no client-injected limitations, no citations table. |
| AC12 | At 1252×853 with the sidebar expanded (report column 576 px, card content box 534 px: `1252 − 296 − 48 − 300 − 32 − 42`, from the CSS in §3.7) and with it collapsed (`.prose` capped at 720 px), the findings table in `index.html` satisfies `table.scrollWidth <= frame.clientWidth`. At 390×844 (the render script's phone viewport) the findings table sits in an `overflow-x: auto` frame with no pinned column and the page never scrolls horizontally: `document.scrollingElement.scrollWidth <= window.innerWidth`; the frame is not required to overflow. In `states.html` at 1252×853 the options-table frame has `scrollWidth > clientWidth` and its `Option` column stays visible after scrolling the frame to the right. |
| AC13 | The rail shows Review (meter with a visible 0.80 line, status text, `Scored sources cited`), Coverage (`n of m`, the not-found id list), Evidence (eleven values from `evidence_counts`, `not measured` when null), Session facts (`pass p of P`, `duration_seconds`, `evidence_path`, `quality_path`), Cost and usage (`Not recorded`), Errors — and nothing named claim confidence, source authority or corroboration; `9ea4c220`'s `Scored sources cited` meter (4 of 5 = 0.80) and EVIDENCE finding `F03`'s `overall` detail meter (0.80) paint yellow. |
| AC14 | The `Report \| Evidence` toggle switches `#stage-report[data-view]`; the Evidence view lists every fixture row under `All`, each of the six other chips filters to rows of that status only with a matching count, and selecting a finding, a not-found target and a refused row fills the detail pane per §4.4. |
| AC15 | `drConsole.view`, `evidence`, `advanceTo`, `arc`, `loop` exist and behave per §4.6; `node scripts/render_design_reference.mjs …` prints `9/9 captures verified` and writes `01-idle … 07-idle-phone, 08-evidence, 09-running-extra-pass`. |
| AC16 | `api-gaps.md` has sections for the five stages in DESIGN.md §3 order plus a Sidebar section (SB.1 row summary, SB.2 durability), opens its gap list with E1 carrying the JSON shape and the `?format=markdown` rule of §4.5, records the five closed items, contains gap 3.7 (live per-event delivery), downgrades 4.1, and its should-not-close list ends with per-agent effort editing; `README.md` has no "Known defect" section. |
| AC17 | `DESIGN.md` sections listed as verbatim in §5 (§3.1 `:210-277`, popover placement `:459-490`, §3.3 `:525-584`, §5.1–5.3 `:967-1093`, §5.5–5.6 `:1151-1360`) are byte-identical to the merge commit `4c34875`, with two recorded exceptions: the `:482-484` phrase edit (§5) and §3.6, which differs only in its first sentence, the `:790` anecdote and the added 0.80 note; `open-design/` is byte-identical. |
| AC18 | The `:root{…}` block (`index.html:11-55` at `4c34875`) is byte-identical after the edit. |

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| The real stream delivers a node's events as one burst, so the running stage changes once per node and the researcher stage shows `not yet` counters for its whole duration | recorded in §4.1 and DESIGN.md §5.7/§5.8 (R1); api-gaps gap 3.7 lists live delivery for sub-project 2 (R2); every rule is written to be burst-safe, including the reviewer's completion being inert after a loop decision |
| Per-pass counter semantics on an extra pass: the verifier and researcher rows count this pass (`evidence_verifier.py:1011`, `researcher.py:2700-2708`), sources scored and tool calls the whole run, sentences the current draft | each row carries its scope; the block caption names the pass; reset rows read `not yet` |
| E1 does not exist, so the Evidence view and `Download evidence log` are prototype-only until sub-project 2 | the real app renders the muted `not served by the service yet` line and no button; api-gaps E1 is first in the list |
| `/status.iteration` takes ReAct step values mid-burst (`sessions.py:68-69`; `researcher.py:2612`); a poll likely never sees them [INFERENCE], an SSE consumer copying `iteration` from every event would | the console reads the pass from `graph.*` events only (§3.2 rule 1); sub-project 2 should fix the store |
| Two ambers exist: `--warn` (`#f59e0b`, `index.html:16`) for the arc stroke as approved, and `--status-warn` (oklch, `:48`) for text | the arc uses `--warn`, the tag text uses `--status-warn`, the spine rows keep `--status-*`; `--meta` is never used for text |
| The options table's sticky first column over a scrolling frame can double-paint on some engines | the pinned cell carries the card surface colour; `states.html` demonstrates it at 1252×853 (AC12) |
| The render script needs Chrome and Node 18+; a machine without them cannot regenerate `reference/` | unchanged from today; the plan's summary records the Chrome version used |

---

## 8. Out of scope

- Product code, API code, config, and the E1 endpoint itself (sub-project 2 owns E1, live per-event delivery, the settings echo, `/capabilities`, `/health`, the session list, the terminal frame, `Last-Event-ID`, the `/status.iteration` store fix).
- The React/Next application (sub-project 3).
- Any change to `docs/design/open-design/`, to the `:root` token values, to typefaces, contrast tables or motion durations.
- A rendered event log, a cancel/re-run control, an embedded trace viewer, provider selection, per-agent effort editing (api-gaps "should not close").
- Rewording, reordering or renumbering anything inside the server's Markdown; the console renders it as-is.
- New reference renders beyond the two named (`08-evidence`, `09-running-extra-pass`).
