# Last-run agent trace review and binding plan amendments

Date: 2026-09-16. Author and planner: Astra, directly in Codex at the user's request. Scope: output quality of the six agents and the CLI. This is analysis and planning, not an application implementation or a new research run.

## Baselines and method

- New branch: codex/agent-cli-quality-trace-plan, created from freshly fetched origin/main at b2fa96bd1121bafd3775374e032cf5a817c0814d.
- Main contains the prior plan amendment e2cbb8fcd38866f059709bb3c154d035a127289f. It also contains changes from the merge, including reduced public trace payloads and removal of the Streamlit UI. Preserve those changes; this plan is CLI-first.
- Historical run: session 417fa9338e10450784b459f89af98b1c, root trace 01a0a8b6-3e6c-71f2-af92-d5f0ab488a5c. Trace revision 935cc9e; application candidate 2bc6665de59d0cec3d76f0bd0cdc5bd92ba3b180.
- Retrieved all 1,079 existing spans using the LangSmith EU read API. Tool/LLM counts below use the nearest agent.* ancestor, not the graph wrapper or aggregated error rows. No provider generation, search, new source fetch, or live evaluation was started.
- A fresh project-root listing confirmed this remains the latest research.session run; the preceding three roots are the corroboration, publisher-retention, and resource runs, not newer sessions.
- The source report, evidence ledger, CLI log, relevant merged-main code, and existing tests were inspected. A small offline in-memory reproduction confirmed the memory admission defect.
- Merged-main baseline: 3,100 tests passed, 1 live test deselected, 2 dependency deprecation warnings. Imports were pointed at the new worktree's src directory. This is a regression baseline, not report-quality approval.

[Open the complete last-run trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/5186a642-2114-4f70-9931-a5e6dcdcef7a/trace/01a0a8b6-3e6c-71f2-af92-d5f0ab488a5c/run/01a0a8b6-3e6c-71f2-af92-d5f0ab488a5c).

### What is and is not observable

Available: span ancestry/timing, tool arguments, result counts/chunk counts/status, some bounded observations, effective model settings, agent result summaries, the initial Planner/Researcher state, and the final local report/ledger.

Unavailable in the queried trace: complete LLM request messages and returned drafts, full fetched document/page bodies, per-claim extraction/adjudication packets, and most later graph state payloads. A successful read with zero findings establishes poor observed yield, not that a particular passage was definitely dropped. A newly fetched copy today would not recreate the historical bytes.

Consequently the review does not claim to reconstruct private reasoning, identify the exact malformed schema field, prove an unseen A+B pair existed, or independently establish the external truth of every battery fact. The repair plan requires bounded evidence manifests and replayable selected passages, not logging full prompts or restoring removed thought/observation payloads.

### Immutable local evidence

Logical paths are under output/. These ignored historical artifacts remain in the old .worktrees/cross-agent-planner-fix-parity worktree; they were not silently copied into the new branch.

| Artifact | SHA-256 |
| --- | --- |
| report-417fa9338e10450784b459f89af98b1c-3.md | 2e6f60bb3b422a01bcceb001138be0f1e706c4fc6b6e2b604836a4d78a023597 |
| report-417fa9338e10450784b459f89af98b1c-3-evidence.md | a482e9ca905109dc7987c638bd10e481043db1692d6265dfa98dddea24b06b8d |
| cli-canary-20260915-223507-evidencepooling.log | 5e722bdc9807b03a88ec5885460b8db8d9f0b62c9a98f87e51990c5f6498eafb |

The run ended partial: Critic 5/10, four of six topics credited, 16 canonical checked claims, 5 provisionally verified, 3 unverified, 8 insufficient, and 15 stored verification passages. The reader cites 8 of 10 assessed sources. The roughly 815,664 tokens and 80 accumulated error records are not 80 failed external calls.

## Measured agent behavior

HTML/PDF counts mean tool invocations, not distinct documents or validated supporting passages. Memory is deliberately separate.

| Agent | LLM requests | Search | HTML reads | PDF reads | Memory queries | Failed read calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Planner | 6 | 0 | 0 | 0 | 10 | 0 |
| Researcher | 93 | 157 | 8 | 12 | 23 | 5 |
| Source Evaluator | 3 | 0 | 0 | 0 | 0 | 0 |
| Fact Checker | 121 | 121 | 25 | 33 | 20 | 14 |
| Synthesizer | 4 | 0 | 0 | 0 | 0 | 0 |
| Critic | 18 | 29 | 0 | 0 | 11 | 0 |
| Total | 245 | 307 | 33 | 45 | 64 | 19 |

There were also two successful document-publication calls outside agent spans. PDF reads succeeded 44/45; HTML reads succeeded 15/33. Therefore access denial remains a real obstacle, but an explanation based only on inaccessible sources does not fit this run.

| Macro pass | Researcher topics attempted | Findings emitted in pass | Sources assessed cumulatively | Canonical checked claims at synthesis | Critic |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 6 | 15 | 7 | 5 | 4 |
| 1 | 5 | 6 | 9 | 10 | 4 |
| 2 | 5 | 7 | 10 | 13 | 4 |
| 3 | 4 | 8 | 10 | 16 | 5 |

Twenty verification jobs ran after four five-claim extraction batches. Job verdicts were 6 verified / 5 unverified / 9 insufficient; these are not the final canonical 5 / 3 / 8 counts. Four jobs had no new "independent" publisher and bypassed adjudication; the other 16 attempted adjudication. Two structured responses failed validation and were retried: one CritiqueDraft and one ClaimVerdictDraft. Both recorded finish_reason_category=stop, not length. Do not diagnose these as token truncation or provider outages without additional evidence.

## Findings, ownership, and decisive tests

### TR-01 — Memory is incorrectly admitted as source evidence

Confirmed code defect with trace exposure, not merely a prompt hypothesis.

The initial state already contained five previous-session findings: three copies of the Novoco queue/wait-time claim and two copies of the Utility Dive 2022 growth claim. All ten Planner memory queries returned results; this was not an empty-memory retry loop. Neither Novoco nor Utility Dive was opened by web_scraper anywhere in this trace, yet both supply final report claims and references.

agents/steps.py:_read_payload_urls treats a query_memory match with nonempty content and source_url as a read. That result feeds Researcher URL admission, render_evidence(discovery_payloads=False), Fact Checker retrieved_source_urls, and independent_domains. The memory bridge provides generated finding text/URL/confidence/session metadata, not a validated original read artifact. Existing tests explicitly bless this behavior.

An offline reproduction using only a recalled assertion and URL returned:

~~~text
classified_read_urls = ("https://remembered.example/report",)
independent_publishers = ["remembered.example"]
included_in_read_only_prompt = True
~~~

For Fact Checker span 01a0a8bb-68e2-78f0-910c-6bb99619a0bb, the actual page/document reads were four reads of the claim publisher's LBNL PDF, yet its summary says independent_sources=1. Memory queries and the shared classifier explain how that counter can increase without a fresh independent page read. Its recorded observations include the remembered queue claim.

Exact historical memory-to-draft attribution is still unavailable. Nevertheless the admission path is confirmed; "read-bearing publisher count" is not a reliable historical corroboration measure here.

Owners: Tasks 1, 2, 3, 6, 12, 13. Make ordinary memory discovery-only. Admit a cache only through validated read artifact/version provenance, not a model-supplied URL or confidence. Test memory-only A+B, duplicated stale high-confidence memories, corrected fresh evidence, and valid immutable-cache reuse. Keep legacy memory intact but do not promote it to verified evidence.

### TR-02 — Decision feedback is too small to support good next actions

Confirmed code path, visible prefix observations; its share of total quality loss is not numerically isolated.

config.yaml sets observation_summary_chars=200. react.py serializes a successful tool payload then prefix-clamps it. BaseAgent records this summary in its scratchpad, and the next native decision request is rebuilt from those notes. Full read data remains in ReActRun for extraction, but is not the normal next-decision context. Extraction separately prefix-clamps each payload to 4,000 characters.

A 200-character search observation often shows only the first title/URL; a long PDF's first serialized chunk is often its cover. This architecture encourages repeated discovery/read calls without a usable inventory of unvisited leads or already-read relevant passages. The trace shows repetition; it cannot prove what every omitted token contained.

Owners: Tasks 3 and 6. Separate user-facing/log summaries from model decision context. Pass typed candidates, attempted/read/denied states, selected locators, unresolved evidence obligations, and remaining tool/model-turn capacity. Inspect actual next provider requests in tests; bigger logging limits or final-extraction-only changes cannot satisfy this repair.

### TR-03 — Successful document access is being spent on repetition and low-value extraction

Researcher produced zero findings in 11/20 subtopic passes. Wholesale rules produced zero in all four passes, despite 40 total tool calls. The first supply-chain pass read a USGS PDF with 217 extracted chunks and emitted no findings. The first technical pass read a CAISO PDF with 36 chunks and emitted none. Those observations establish a selection/extraction investigation, not proven support for a particular missing claim.

Researcher read the same 2025 LBNL PDF seven times; Fact Checker read it fourteen times. Across both agents, that URL accounts for 21/45 document calls. The 2022 LBNL PDF accounts for another ten. Repeated URL reads are not independent evidence; equal chunk counts do not establish identical byte hashes.

CAISO's 2024 report was read and assessed, but the retained finding mainly says the report contains a revenue section. EIA performance findings appeared in pass 2 and remained unchecked; their 2019 observations are not automatically current evidence.

Owners: Tasks 3, 5, 6, 9. Share versioned reads across agents, select substantive sections across the document, then extract before spending on another copy. Test late/paraphrased/table evidence and a metadata-only negative. Measure new eligible works and completed obligations, not raw read totals.

### TR-04 — Planner supplies stale anchors and overcomplicated success conditions

The stored six-topic plan is meaningfully broad, but several criteria lock onto 2024, 2023–2024, or 2024–2025 even though the session is September 2026. It also invents numerical matching tolerances: 10%, 5 percentage points, 15%, and 3 percentage points. Criteria combine different observations, regulation dates, technical assumptions, and multiple jurisdictions under one obligation.

For example, the supply-chain criterion asks two publishers to agree on concentration percentages and a tariff date, while wholesale criteria expect another publication's own modelling for an official rule date. These are not all the same evidentiary problem. This makes acquisition expensive and creates false insufficiency for source-owned facts.

Planner exhausted ten memory calls before producing the plan; its five decision turns did not discover new public evidence.

Owner: Task 2. Stamp as-of from the run clock, distinguish latest-available observations from policy effective dates, atomize obligations, declare scope, and assign appropriate support policy before retrieval. Do not invent tolerances to equate distinct facts. Stronger reasoning is a candidate setting, not the causal repair for lost evidence.

Trace anchor: planner 01a0a8b6-4137-7c51-9312-2d5eef653cbb; its complete stored plan is in the input of researcher node 01a0a8b6-de25-7c61-a5d9-4934b9c60eba.

### TR-05 — Source Evaluator is not primarily blocked by a source-count cap

Its four passes reported 7 → 9 → 10 → 10 assessed sources, with zero cap/missing/provider-unscored counts. The last pass reused assessments without another LLM request. The ledger correctly describes Utility Dive/Novoco as derivative, Moss Landing as a self-interested company source, and EIA observations as old.

Those useful judgments are not enforced strongly enough downstream: derivative origin, observation date, and source fitness do not prevent weak support being called verified or old figures dominating the answer. A high source score also does not mean a usable finding exists.

Owner: Task 4, with Tasks 6–7 consumers. Preserve working assessment reuse, key it by content/metadata revision, and enforce typed role/freshness/dependence at claim and statement level. Assess verifier-acquired sources too. Do not merely increase source caps or lower source-quality thresholds.

Trace anchor: final evaluator 01a0a8c9-ecd9-78c3-b49d-79d68b0a81e5.

### TR-06 — Fact Checker spends effort on repeated atoms and ambiguous support

It performed 100 ReAct turns across 20 verification jobs, almost exhausting ten calls each time, then retained only 16 canonical claims. The ledger repeats queue-growth, report-cutoff, and PJM-cycle statements under different textual identities. All four extraction batches have exactly five accepted claims. This supports fair scheduling and semantic deduplication work, but does not prove how many drafts were discarded by the prefix.

The URL-pooling change does not insert upstream excerpts into the adjudication prompt; the new-independent-retrieval early return remains. More importantly, memory admission invalidates part of the historical read/publisher interpretation.

The mineral claim's recorded RFF support uses different minerals/thresholds, and Mercom explicitly derives from the IEA report. Queue support repeats LBNL-derived information. Some "supports" passages are cover text, a table of contents, or support only part of a compound assertion. The ledger does not establish full independent support for every provisionally verified claim.

Owners: Tasks 1, 5, 6. Repair evidence admission, claim-specific passage union, atomic entailment, stable identity, pending work, and selected-source independence together. A two-URL counter or an A+B prompt cannot substitute for these contracts. Preserve the appropriate refusal to verify incompletely supported cases; do not make the score rise by liberalizing support.

For the worker's F1: this trace cannot resolve every historical (a) versus (b), because full extraction packets are absent. Add (c): supposed upstream read evidence may be generated memory rather than a fresh page. Audit admission first, then acquired bytes → selected passages → drafts → accepted claims. Mark unavailable comparisons not_diagnosable.

### TR-07 — Refinement can declare a topic satisfied from one raw finding

agents/researcher.py:_refinement_satisfied_sub_topics skips a topic when it has any prior raw finding and the Critic did not name that topic as a gap. This is not substantive target completion.

The log records topic-04 skipped for interim_satisfaction in pass 1; topic-02 skipped in passes 2 and 3; topic-05 skipped in pass 3. Economics still lacks current cost/revenue/financing; safety lacks ordinance/standard analysis. Meanwhile wholesale spends another ten calls each pass with no findings.

Owners: Tasks 9–11. Required target assessments must reopen unmet obligations even when Critic gaps omit them. A skipped repair is not "a planned sub-topic was never researched": CLI language must distinguish prior completion, deferred work, and never attempted work. Avoid repeating stale context or recreating completed atoms.

### TR-08 — Synthesis exposes rather than repairs a weak evidence selection

All four synthesis calls returned without provider failure; report claim counts grew 5 → 10 → 13 → 16 while section count stalled at four. The final reader has eight references, not six. It labels every ranked deployment mechanism "not stated", repeats old queue figures and uncertainty inventories, and leaks C001/C011. It correctly refuses to call 2035 projections current costs, but does not provide a substantive current economics answer.

A numeric figure absent from checked claims still appears in the uncertainty prose ("890 GW"), despite the sentence saying it is not being reported. The scope and methodology also assert stronger statement linkage/non-repetition than the prose demonstrates. Editorial diagnostics can introduce reader-visible facts too.

Owners: Tasks 7, 10, 11. Use an answer-shaped outline, evidence-based distinctions and mechanisms, primary attribution where appropriate, and complete statement support checking including caveats, scope, tables, and methodology. State an unresolved question without leaking unchecked numbers. Do not invent mechanisms or claim that every limitation is unique when it is visibly repeated.

Trace anchor: final synthesizer 01a0a8cc-ea17-7ab0-ba2d-fb17d0dccae6.

### TR-09 — Critic is an inefficient reviewer, but the low score is not itself the defect

Scores were 4 → 4 → 4 → 5, gaps 5 → 5 → 4 → 6. Every Critic pass used ten search/memory calls; it cannot open a page. Its searches cannot establish the missing support. The final low score is consistent with a broad report still missing important answers; raising scores would not fix those omissions.

One CritiqueDraft schema error recovered. Exact malformed fields and complete gap wording are absent from the trace. Missing payloads must not be diagnosed as a particular prompt error.

Owners: Tasks 8 and 10. Review the exact complete report/evidence packet without discovery or prior-score coaching; emit defect-specific repair actions; test schema repair preserves the input packet and does not invent a verdict. Calibrate against both strong source-attributed answers and polished non-answers.

Trace anchor: final critic 01a0a8cd-643a-7331-bff8-b07d24e1b746.

## Priority and acceptance consequences

Repair in this order within the existing task dependencies:

1. Source evidence admission and auditable read/claim lineage.
2. Decision context, document passage selection, reusable reads, and useful target yield.
3. Planner obligations, atomic claims, grounded verification, and required-target-driven refinement.
4. Evidence-bound composition, calibrated editing, complete report review, and consistent CLI publication.
5. Real-agent negative/positive replays, cold-memory live repetitions, and a separately declared warm-memory challenge.

The [amended implementation plan](../plans/2026-09-16-evidence-integrity-and-agent-production-readiness.md) remains the sole execution program. Its eighteen offline cases include memory-only, validated-cache, late-candidate decision-context, and missed-Critic-gap controls. Existing historical cases remain. Implementation checkboxes are not marked complete by this review.

Quality readiness remains unproven. It requires the configured agents to deliver substantive supported answers through the actual CLI, with every important defect closed and independent source review passing. This review did not lower the Critic threshold, start paid runs, modify production code, delete memories, or change safety mechanisms.
