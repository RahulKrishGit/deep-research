# Cross-Agent Planner-Fix Parity and Controlled Evaluation Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden every non-Planner agent against the transferable failure modes discovered during the Planner campaign, without regressing the now-working Planner, and use controlled evaluation evidence to decide whether any agent needs an operation-specific output-token budget or an agent-specific prompt/behavior repair.

**Architecture:** Move the confirmed empty-optional-field `ReActDecision -> ReActStep` normalization from the Planner-local provider wrapper to the shared ReAct boundary, then prove Researcher, Fact Checker, and Critic inherit the fix while Source Evaluator and Synthesizer remain unaffected because they do not run ReAct. Preserve each non-Planner agent's intentional graceful/partial-result semantics, but enrich caught provider failures with a shared, bounded, provider-content-free diagnostic snapshot so output-limit/schema/transport/HTTP failures remain distinguishable even when an agent returns a fallback result instead of raising. After offline TDD is green, run one immutable controlled baseline per non-Planner agent, diagnose from typed artifacts, and add an operation-specific max-token override only when an actual target-side `output_limit` is observed for that operation; never raise the global 4096 cap as a speculative fix.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest + pytest-asyncio, Ruff, Git worktrees, PowerShell, DeepSeek V4 Flash, LangSmith controlled evaluation, the existing fake-driven dependency bundles, and the existing `StructuredCompleter.complete_structured(..., max_tokens=...)` per-call override.

**Spec / design basis:**
- `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`
- `docs/superpowers/specs/2026-08-25-planner-provider-failure-remediation-design.md`
- `docs/superpowers/specs/2026-08-24-agent-evaluation-improvement-workflow-design.md`
- `docs/superpowers/plans/2026-08-24-planner-controlled-evaluation-improvement.md`
- Current `obra/superpowers` `brainstorming`, `writing-plans`, and `subagent-driven-development` skills as of 2026-09-08.

## Current Execution Status (Updated 2026-09-10)

This table is the authoritative task bookkeeping for the remote branch `codex/cross-agent-planner-fix-parity`. “Complete” requires implementation evidence plus the required task-scoped review; “not complete” means the task must not be represented as finished merely because its brief or offline preparation exists.

| Task | Status | Evidence / remaining work |
| --- | --- | --- |
| 1. Isolated SDD campaign and known-good base | **Complete** | Worktree/ledger initialized; editable-install provenance and offline baseline recorded. |
| 2. Shared ReAct boundary normalization | **Complete** | Commit `66340a4`; RED/GREEN focused tests, neighboring tests, Ruff, and diff checks; Luna Max review approved. |
| 3. Planner wrapper removal and cross-agent ReAct parity | **Complete** | Commit `45178ab`; parity/provider-identity regressions and task review approved. |
| 4. Safe provider-failure snapshot | **Complete** | Commits `c4e3fee` through `9349166`, plus export fix `808cfba`; two reviewed fix rounds resolved the Important findings. |
| 5. Non-Planner fallback diagnostic wiring | **Complete** | Commit `0c9c0ec`; focused, agent, evaluation, Ruff, and diff checks; Luna Max review approved. |
| 6. Evaluation artifact-boundary preservation | **Complete** | Commit `2fbf6be`; test-only boundary guard; Luna Max review approved. |
| 7. Source Evaluator/Synthesizer non-ReAct characterization | **Complete** | Commit `91d817b`; characterization tests; Luna Max review approved. |
| 8. Offline integration gate | **Complete** | Candidate `808cfba`; focused `690` passed and full offline `1,895` passed; task review approved. |
| 9. Offline typed evaluation telemetry contract | **Complete** | Commit `a763cda`; focused/evaluation/full-offline tests, exact local inventory acceptance/rejection proof, lint/diff checks, and fresh Luna-max review approved. |
| 10. Immutable controlled baselines | **Complete — five agents terminal-blocked after confirmation** | At candidate `d6a082c`, all five agents completed immutable 3-case × 3-repetition baselines and same-SHA confirmations. Windows preflight passed; typed judge/provider failures persisted, so each agent has `INFRASTRUCTURE_BLOCKED`, `repair_attempts: 0`, safe inventories, and a terminal record. |
| 11. Evidence-gated output-budget repair | **Not started / not applicable** | Requires typed Task 10 target-side `output_limit` evidence; none exists. |
| 12. Evidence-gated agent quality/trajectory repair | **In progress — evaluator repair wave 1 complete** | Sol-confirmed Task 10 evidence produced an evaluator contract repair: commit `7c58abc` fixes cross-channel `required_fields_present` with six production-shaped regression cases. Critic routing/context repairs and typed judge diagnosis remain pending. |
| 13. Full controlled validation | **Not started** | Depends on Task 10 and any evidence-supported Tasks 11–12 repairs; no live or controlled validation was run. |
| 14. Permanent cross-agent fix log | **Complete** | Updated with the candidate `d6a082c` controlled baseline/confirmation evidence, typed failure classes, hashes, and the no-budget/no-agent-repair decision. |
| 15. Final offline verification and whole-branch review | **Offline verification complete — whole-branch review deferred** | After the Task 10/14 documentation update: focused config tests `47 passed`, `tests/test_evaluation` `697 passed`, full offline suite `1,936 passed, 1 deselected`, Ruff and `git diff --check` passed. Whole-branch review remains user-deferred. |

The following boundaries remain active: `--tier live` is prohibited; Task 9 is network-zero; controlled calls require immediate per-command human authorization; no Task 10–13 result may be inferred from the absence of a Task 9 artifact; and the whole-branch review must remain deferred until the user explicitly requests it. The separately authorized GitHub push is complete, but it does not change the Task 15 review status.

## Documentation and Error/Fix Ledger Requirement (Added 2026-09-10)

Every repair wave must leave an auditable, secret-safe record of the errors and fixes used to restore the agents. Before and after each implementation or review attempt, update the ignored SDD ledger and the tracked permanent fix log with: the candidate SHA; the exact repository-relative files and tests involved; the observed typed error or failed gate; the classification and ruled-out alternatives; the smallest repair; the resulting commit; verification commands and counts; reviewer disposition; and any remaining blocker or next action. Preserve earlier failed artifacts and reports as immutable evidence; never overwrite, relabel, or delete them to make a later candidate appear successful. Do not record prompts, provider responses, evaluator inputs, hidden reasoning, raw exception messages, credentials, or environment dumps. A task cannot be marked complete until its error diagnosis, fix, review, and verification are recorded in both ledgers where applicable.

## Brainstorming Outcome

This is an **architectural** change, not a bounded patch: the confirmed bug sits on a shared runtime boundary used by multiple agents, while provider-diagnostic and token-budget behavior crosses provider, runtime, evaluation, and agent-specific fallback interfaces.

Three approaches were considered:

1. **Copy the Planner fixes into every agent.** Rejected. A Planner-local `_DecisionNormalizingCompleter` copied into Researcher, Fact Checker, and Critic would duplicate the same workaround three times, leave the real shared contract mismatch in place, and create provider-identity/wrapper-lifecycle complexity in every agent. Copying Planner prompt rules into the other agents is also incorrect because those agents have different jobs.
2. **Fix shared boundaries once, preserve agent-specific semantics, and make expensive/behavioral changes evidence-gated. Recommended.** Normalize the unused ReAct decision fields at the shared `ReActStep` construction boundary; keep each agent's current fallback/partial-result policy; preserve typed provider diagnosis in a safe shared snapshot; then use controlled experiments to justify any output-budget or prompt changes one operation/root cause at a time.
3. **Generalize all model-call policy into one new global operation-budget/failure framework immediately.** Rejected for YAGNI. The Planner campaign proved that one operation-specific budget is useful, not that every operation needs its own override. Pre-adding six token knobs would expand configuration and test surface without evidence.

### Transferability matrix from the Planner fix log and current `main`

| Planner lesson | Researcher | Source Evaluator | Fact Checker | Synthesizer | Critic | Plan decision |
| --- | --- | --- | --- | --- | --- | --- |
| RC-A: unused `ReActDecision` field may be `""`, but `ReActStep` requires non-blank optional strings | **Applies**: custom per-subtopic ReAct loop | **Does not apply on current main**: no ReAct loop | **Applies**: custom per-claim ReAct loop | **Does not apply on current main**: no ReAct loop | **Applies**: spot-check ReAct loop | Fix once in shared `react.py`; remove Planner-local workaround after parity tests |
| RC-B: Planner searched despite not needing search | Different role; do not copy wording | N/A | Different role; often must search | N/A | Different role; spot-check may search | Diagnose only if an agent's controlled trajectory shows an analogous unnecessary/prohibited call |
| RC-C: Planner produced priorities out of order | Planner-output contract only | N/A | N/A | N/A | N/A | No transplant |
| RC-D: Planner ambiguous-scope wording missed Planner-specific rubric terms | Planner rubric only | N/A | N/A | N/A | N/A | No transplant |
| Repo-owned transient retry policy | Already provider-wide | Already provider-wide | Already provider-wide | Already provider-wide | Already provider-wide | Verify effective config; no new retry implementation |
| Typed provider/output-limit/schema telemetry | Provider layer already supports it, but local catches collapse it to `exception_type` | Same | Same | Same | Same | Add safe diagnostic projection while preserving current fallback semantics |
| Planner-final per-call max-token override | Candidate only if extraction actually hits output limit | Candidate only if scoring hits output limit | Candidate only if extraction/verdict hits output limit | **Highest-risk candidate** because report draft is long, but still evidence-gated | Candidate only if review hits output limit | Never raise global 4096; add one operation-specific field only after typed evidence |
| Judge-side typed diagnostics / URL preservation | Already shared evaluation behavior | Already shared | Already shared | Already shared | Already shared | No agent-specific change |

**Important correction to the historical fix log:** RC-A's original follow-up text named all five sibling agents. On current `main`, Source Evaluator and Synthesizer explicitly override `run()` and perform no ReAct loop, so they are not exposed to the `ReActDecision -> ReActStep` blank-field crash. The transferable RC-A production fix therefore targets Researcher, Fact Checker, and Critic plus Planner's local-workaround cleanup.

## Success Criteria

The campaign is complete only when all of the following are true:

- The shared ReAct loop safely converts an unused `tool_name=""` or `final_answer=""` to `None` before `ReActStep` validation.
- The Planner's two RC-A regression tests still pass after its `_DecisionNormalizingCompleter` is removed; provider identity remains unchanged before, during, and after a Planner run.
- Researcher, Fact Checker, and Critic each have an agent-level regression proving the shared fix reaches their custom ReAct loop.
- Source Evaluator and Synthesizer have explicit tests/documentation proving they do not run ReAct and therefore do not need the RC-A workaround.
- Every caught non-Planner `ProviderError` records a bounded typed diagnostic kind without serializing `str(error)`, raw finish values, provider output, prompts, request payloads, URLs derived from secrets, or validation input values.
- Existing fallback semantics remain intact: Researcher keeps prior findings and stops later subtopics on a provider failure; Fact Checker keeps prior claims and uses `insufficient_evidence` where designed; Source Evaluator emits low-confidence fallback rows; Synthesizer emits the evidence-only report skeleton; Critic emits its existing fallback critique and routing decision.
- `llm.max_tokens` remains 4096 throughout the campaign.
- No non-Planner operation-specific token-budget field is added unless a controlled target repetition reports a typed target-side `output_limit` for that exact operation and the repair amendment documents the evidence.
- Every agent-specific source/prompt change is tied to one root-cause ID, has RED/GREEN offline evidence, a focused three-repetition controlled retest, and a reviewer gate before a full nine-repetition validation.
- No controlled case, gate, rubric, evaluator, threshold, judge prompt, dependency script, or weight is changed to make an agent pass. A demonstrated harness defect stops agent tuning and moves to a separate approved harness plan.
- The final full tracked offline suite and Ruff are green, and the final whole-branch review has no unresolved load-bearing findings.
- Each of `researcher`, `source_evaluator`, `fact_checker`, `synthesizer`, and `critic` has either an immutable nine-repetition controlled result, an explicitly reused immutable passing baseline, or a documented terminal state of `INFRASTRUCTURE_BLOCKED`, `HARNESS_DEFECT_BLOCKED`, or `ESCALATED`; no agent is silently omitted.
- Each non-Planner agent has a safe baseline-provenance record, a typed failure/quality diagnosis, stable root-cause IDs, reviewer-gated repair history, and a final terminal-state record even when no source repair is authorized.
- The permanent fix log contains one evidence-only section for each of the five non-Planner agents, including environment/retry rulings, output-budget decisions, review resolutions, deferred/parked findings, immutable artifact paths, and the final terminal state.

## Global Constraints

- Treat Planner behavior on `main` after merged PR #19 (`bc67620666bbc41c516556d45602de6dd00d7102`) as the known-good reference. The execution base must contain that merge commit as an ancestor.
- Do not re-tune Planner quality, prompts, scoring, cases, or budgets in this campaign. Planner changes are limited to deleting its now-redundant RC-A local normalization wrapper after the shared fix is proven equivalent.
- Do not copy Planner RC-B/RC-C/RC-D prompt text into sibling agents without a separate agent-specific controlled failure proving the analogous instruction ambiguity.
- Preserve `AgentRuntimeConfig.planner_final_max_tokens` and `AGENTS_PLANNER_FINAL_MAX_TOKENS` exactly as they work now.
- Preserve the global `llm.max_tokens: 4096`; ReAct decisions and judges stay at the global cap in this plan.
- Preserve the repo-owned retry policy. Verify the *effective* retry count/backoff through configuration before provider runs because the Planner campaign discovered that a repository `.env` can override `config.yaml`. Never print the value of any secret while doing this.
- Use an isolated branch/worktree: branch `codex/cross-agent-planner-fix-parity`, worktree `.worktrees/cross-agent-planner-fix-parity`.
- Use the current Superpowers SDD workspace convention: initialize this plan's workspace with `scripts/sdd-workspace` from the installed/current Superpowers skill when available; the ledger identity must name this exact plan file. If that helper is unavailable in the execution environment, create the equivalent ignored workspace at `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/` and record the ruling.
- A fresh subagent gets only its task brief, the exact interfaces it consumes, prior task decisions required by that brief, and the report path. Do not make implementers read the whole conversation history.
- One implementer task gets a spec-compliance + code-quality review before the next task. Use a fresh reviewer. After all tasks, perform one broad whole-branch review with the most capable available model.
- Batch only truly same-shape test additions. Do not batch tasks whose failures require different architectural judgment.
- `pytest` and Ruff are offline. Controlled evaluation intentionally makes paid target-model, judge-model, and LangSmith calls.
- Do not run any controlled provider command without immediate human authorization at the paid-call gate in the execution session. Do not run `--tier live` anywhere in this plan.
- Never run `python -m deep_research.evaluation suite` until each non-Planner agent has independently passed its own full controlled campaign or has a documented infrastructure-blocked terminal state.
- Never print, commit, upload, or quote credentials, hidden chain-of-thought, raw provider payloads, unredacted exception strings, prompts, evaluator inputs, or provider reasoning content.
- Use only typed artifacts, bounded trajectory summaries, safe events, safe provider diagnostics, gate IDs/details that are already allowed, concise judge rationale, and trace URLs directly supplied by LangSmith.
- Do not commit `.env`, `.superpowers/`, `output/`, evaluation artifacts, review packets, or provider-run ledgers.
- No merge, push to a shared branch, release, deployment, or live-tier run is part of this plan.

---

## File Structure

### Shared production files

| File | Responsibility in this campaign |
| --- | --- |
| `src/deep_research/agents/react.py` | Own the single shared normalization boundary from a validated `ReActDecision` to `ReActStep`; preserve current provider-error propagation compatibility switch. |
| `src/deep_research/agents/planner.py` | Remove `_DecisionNormalizingCompleter` and the temporary provider swap after the shared boundary is proven equivalent; retain Planner's operation-specific provider wrapping and final-plan budget. |
| `src/deep_research/providers/contracts.py` | Define one finite, immutable, provider-content-free runtime snapshot for caught provider failures, built only from already-safe typed provider fields. |
| `src/deep_research/agents/errors.py` | Convert a caught `ProviderError` plus a static operation name into JSON-safe `ResearchError.details` without `str(error)`. |
| `src/deep_research/evaluation/models.py` | Own the additive bounded artifact-side telemetry types and `RepetitionResult` fields for Task 9. |
| `src/deep_research/evaluation/evaluators.py` | Expose bounded deterministic metric details while preserving the existing scalar quality API and weighting. |
| `src/deep_research/evaluation/runner.py` | Carry typed metric details and project the four safe telemetry fields into each repetition artifact. |

### Agent production files consuming the shared provider diagnostic

| File | Provider operations whose fallback error must preserve safe typed diagnosis |
| --- | --- |
| `src/deep_research/agents/researcher.py` | ReAct decision (through shared loop) and `SubTopicFindingsDraft` extraction |
| `src/deep_research/agents/source_evaluator.py` | `SourceScoresDraft` scoring |
| `src/deep_research/agents/fact_checker.py` | claim extraction, ReAct decision, claim verdict |
| `src/deep_research/agents/synthesizer.py` | `ReportDraft` generation |
| `src/deep_research/agents/critic.py` | ReAct decision and `CritiqueDraft` review |

### Primary tests

| File | Responsibility |
| --- | --- |
| `tests/test_agents/test_react.py` | Direct RC-A shared-boundary RED/GREEN tests and provider-diagnostic ReAct tests. |
| `tests/test_agents/test_planner.py` | Existing Planner RC-A regression remains green after local wrapper deletion; provider identity/no-wrapper-stack checks. |
| `tests/test_agents/test_researcher.py` | Agent-level blank-field regression; extraction provider snapshot and partial-result semantics. |
| `tests/test_agents/test_source_evaluator.py` | Scoring provider snapshot and low-confidence fallback semantics; no-ReAct characterization. |
| `tests/test_agents/test_fact_checker.py` | Agent-level blank-field regression; extraction/verdict provider snapshots; prior-claim retention. |
| `tests/test_agents/test_synthesizer.py` | Report provider snapshot; evidence-only fallback semantics; no-ReAct characterization. |
| `tests/test_agents/test_critic.py` | Agent-level blank-field regression; review provider snapshot; fallback routing. |
| `tests/test_agents/test_errors.py` | Snapshot-to-`ResearchError.details` mapping, bounds, and secret/provider-content exclusion. |
| `tests/test_deepseek_provider.py` | Provider snapshot compatibility with output-limit/schema telemetry if the snapshot is defined in provider contracts. |
| `tests/test_openai_provider.py` | Generic provider-response/timeout/HTTP compatibility; no unsupported output-limit inference. |
| `tests/test_evaluation/test_targets.py` | Prove fallback-producing agents remain `completed=True` when designed to return a result, while their `errors` retain safe typed diagnosis; Planner raised failures remain target `failure` records. |
| `tests/test_evaluation/test_models.py` | Bounds, finite vocabulary, strict counts, and allow-listed fallback diagnostic contract tests for Task 9. |
| `tests/test_evaluation/test_evaluators_general.py` | Deterministic metric-detail and scalar-compatibility tests for Task 9. |
| `tests/test_evaluation/test_runner.py` | End-to-end local `TargetOutput` to `RepetitionResult` telemetry projection and leakage tests. |
| `tests/test_evaluation/test_reporting.py` | Local artifact round-trip coverage for the additive telemetry fields. |
| `tests/test_evaluation/test_factory.py` and `tests/test_runtime/test_assembly.py` | Preserve exact provider identity/parity after Planner-local wrapper removal. |
| `tests/test_config.py` and `tests/test_evaluation/test_config.py` | Conditional only: operation-specific budget field + environment override + fingerprint when a controlled output-limit amendment authorizes one. |

### Campaign artifacts

| Path | Responsibility |
| --- | --- |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/progress.md` | Ignored SDD recovery ledger, task completion, rulings, review findings, exact commits, provider-run gates, artifact paths, and next action. |
| `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md` | Tracked final permanent record created only after implementation begins: confirmed transferable causes, changes, RED/GREEN commands, controlled evidence, and terminal state for each agent. |
| `output/evaluations/researcher/*/results.json` | Immutable Researcher controlled evidence; ignored, never committed. The other literal roots are `output/evaluations/source-evaluator/*/results.json`, `output/evaluations/fact-checker/*/results.json`, `output/evaluations/synthesizer/*/results.json`, and `output/evaluations/critic/*/results.json`; the experiment directory is resolved from the returned `experiment_name`. |

### Post-gate per-agent campaign packets

These ignored paths are created only after the Task 9 artifact gate and before Task 10. They are operational evidence, not source changes, and must never contain prompts, provider responses, evaluator inputs, secrets, hidden reasoning, or unredacted exception strings:

| Path | Required contents and mutability rule |
| --- | --- |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/record_eval_provenance.py` | Offline-only helper that writes safe effective configuration, retry, environment-source, import, Python, branch, worktree, and Git provenance; it never prints or serializes credential values. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/record_eval_inventory.py` | Offline-only helper that validates one `results.json`, extracts only the bounded inventory schema below, and refuses to overwrite an existing inventory file. It never copies prompts, provider output, evaluator input, or exception text. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/` | Exact packet root for Researcher evidence. The same packet filenames are used under the four other literal roots: `agents/source-evaluator/`, `agents/fact-checker/`, `agents/synthesizer/`, and `agents/critic/`. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/baseline-provenance.json` | One immutable record made before the Researcher baseline command. Do not overwrite it; a later same-SHA confirmation uses `confirmation-provenance.json`, and a shared-change rerun uses `invalidation-provenance.json`. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/baseline-inventory.json` | One strictly validated, typed, bounded three-case × three-repetition inventory for the Researcher baseline. Write once after the authoritative `results.json` is resolved; use the analogous filename under each other agent root. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/confirmation-provenance.json` | Immutable provenance for the one permitted same-SHA, same-effective-config infrastructure confirmation. Create it before that paid command and never reuse it for another command. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/confirmation-inventory.json` | Immutable safe inventory for a confirmation artifact when the confirmation completes enough to validate. It is supplemental evidence and never overwrites `baseline-inventory.json`. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/invalidation-provenance.json` | Immutable provenance for a rerun required because a shared production path changed after a prior agent result. It names the invalidating commit and affected path; it is not a replacement baseline. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/invalidation-inventory.json` | Immutable safe inventory for the shared-change invalidation run. The prior inventory remains preserved and is never relabeled as current evidence. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/root-causes/*.md` | One immutable Markdown record per literal root-cause ID. Each record contains counterevidence, typed failure class, exact operation, repair-attempt count, permitted files, focused case, predicted non-target invariants, rollback, and reviewer disposition. A new hypothesis gets a new filename. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/repairs/*/attempt-*-amendment.md` | One immutable literal repair amendment per root-cause attempt. It is written before any source edit and contains the exact RED test, GREEN command, candidate command, review gate, rollback condition, and expected status transition. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/reviews/*.md` | Fresh reviewer records with only these finding dispositions: `approved`, `needs-change`, `deferred-non-load-bearing`, or `blocked-infrastructure`; each finding has a resolution or an explicit stop. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/escalation.md` | Created only after the third unsuccessful focused attempt for the same root-cause ID. It contains all three hypotheses, commits, focused artifacts, deltas, evidence, and the human escalation decision; it is never created to disguise an infrastructure or harness block. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/terminal-state.md` | One final control-plane record naming the exact terminal state, harness status/exit code, authoritative artifact or reused baseline path, final candidate SHA, invalidations, deferred/parked findings, and next action. Use the same literal record under `source-evaluator`, `fact-checker`, `synthesizer`, and `critic`. |
| `output/evaluations/researcher/*/results.json` | Immutable Researcher controlled artifacts written by the harness. The four other exact roots are `output/evaluations/source-evaluator/`, `output/evaluations/fact-checker/`, `output/evaluations/synthesizer/`, and `output/evaluations/critic/`; resolve the experiment directory from the literal `experiment_name` and never overwrite an existing `results.json`. |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/final-whole-branch-review-handoff.md` | Created only after all five agents have terminal-state records and final offline verification is green; the handoff package for the deferred broad review. It is not a review result. |

Every packet path above is write-once. Before writing any file, run `Test-Path -LiteralPath` and stop if it already exists unless the step explicitly names that file as the current append target. The five literal packet roots are `researcher`, `source-evaluator`, `fact-checker`, `synthesizer`, and `critic`; no Planner packet is created by this campaign.

The inventory helper must emit this exact field contract and no additional fields. The type words below are schema types, not values to copy into an inventory:

```text
Top level:
  schema_version: integer
  agent: internal AgentName
  cli_agent: literal kebab-case CLI name
  baseline_candidate_sha: 40-character lowercase Git SHA
  results_path: absolute path to the resolved results.json
  results_sha256: 64-character lowercase SHA-256
  experiment_name: harness experiment name
  experiment_url: direct URL or null
  dataset_name: harness dataset name
  dataset_url: direct URL or null
  configuration_fingerprint: harness fingerprint
  judge_configuration_fingerprint: harness fingerprint
  prompt_fingerprint: harness fingerprint
  target_model: effective target model
  target_reasoning_effort: effective target effort
  judge_model: effective judge model
  judge_reasoning_effort: effective judge effort
  controlled_repetitions: integer equal to 3
  cases: array of exactly three case records

Case record:
  case_id: one frozen case ID for this agent
  repetitions: array of exactly three repetition records

Repetition record:
  repetition: integer in the range 1 through 3
  completed: boolean
  failed_gate_ids: array of gate IDs
  failed_gate_details: array of safe typed gate details
  deterministic_metrics: map of exact metric ID to numeric result
  deterministic_quality: numeric result or null
  judge_status: scored, judge_not_run, or the harness-declared typed status
  judge_not_run_reason: safe typed reason or null
  judge_dimensions: map of exact dimension ID to numeric result
  judge_quality: numeric result or null
  aggregate_quality: numeric result or null
  target_failure_stage: provider, trace, artifact, setup, or null
  target_failure_reason: safe typed reason or null
  target_failure_details_kind: safe typed details.kind or null
  fallback_provider_failure_kinds: safe typed provider kinds
  fallback_provider_operations: exact safe operation names or unavailable markers
  judge_diagnostic_kinds: safe typed evaluator/provider diagnostics
  react_stop_reason: safe stop-reason enum or null
  prohibited_call_count: integer
  target_trace_url: direct URL or null
  evaluator_trace_url: direct URL or null
  evaluator_source_url: direct URL or null
```

The values are produced from the validated artifact or a directly supplied, sanitized target trace; no worker may substitute a sample score, status, path, SHA, URL, or completion value. Preserve `null` when a URL or typed field is unavailable, and omit no case or repetition. `fallback_provider_failure_kinds` and `fallback_provider_operations` are read only from safe `ResearchError.details.provider_failure` records; a missing operation is recorded as an unavailable diagnostic and cannot justify a budget amendment.

### Five non-Planner campaign contracts

The CLI uses kebab-case; internal `AgentName` values and source paths use underscores. The listed case IDs are the three frozen controlled cases for each agent. The listed operation names are the only target operations eligible for typed diagnosis or an operation-specific budget amendment; `react_decision` remains at the global `llm.max_tokens=4096` cap.

| Agent | Controlled cases and deterministic metric IDs | Target operations | Offline repair surface and invariants |
| --- | --- | --- | --- |
| Researcher (`researcher`) | `multi-source-coverage` (`sub_topic_coverage`, `source_grounding`, `source_diversity`, `budget_respected`); `conflicting-evidence` (`uncertainty_preserved`, `no_false_consensus`, `source_grounding`, `budget_respected`); `partial-search-failure` (`partial_results_present`, `failure_recorded`, `no_invented_sources`, `budget_respected`) | `react_decision`; `researcher_finding_extraction` | `src/deep_research/agents/researcher.py`, `src/deep_research/agents/prompts.py`, `tests/test_agents/test_researcher.py`; preserve prior findings, stop later subtopics after a provider failure, retain recoverable errors, and never invent source URLs. |
| Source Evaluator (`source-evaluator`) | `strong-and-weak-sources` (`one_evaluation_per_source`, `score_ordering`, `bounded_scores`, `low_confidence_flagged`); `corroboration-recency-reputation` (`balanced_scoring`, `one_evaluation_per_source`, `bounded_scores`, `rationale_mentions_multiple_signals`); `reputation-provider-failure` (`all_sources_still_scored`, `fallback_scores_bounded`, `failure_recorded`, `no_fabricated_reputation`) | `source_evaluator_scoring` | `src/deep_research/agents/source_evaluator.py`, `src/deep_research/agents/prompts.py`, `tests/test_agents/test_source_evaluator.py`; preserve one row per source, bounded fallback scores, low-confidence semantics, and explicit reputation-failure recording. This agent is non-ReAct. |
| Fact Checker (`fact-checker`) | `mixed-verdicts` (`verdict_correctness`, `evidence_linked`, `confidence_calibrated`, `sources_known`); `independent-domain-evidence` (`independence_enforced`, `evidence_linked`, `sources_known`, `budget_respected`); `verification-search-failure` (`conservative_on_failure`, `partial_verification_present`, `failure_recorded`, `budget_respected`) | `react_decision`; `fact_checker_claim_extraction`; `fact_checker_claim_verification` | `src/deep_research/agents/fact_checker.py`, `src/deep_research/agents/prompts.py`, `tests/test_agents/test_fact_checker.py`; preserve completed claims, use `insufficient_evidence`/`unverified` as designed after failed verification, retain independent-domain rules, and record recoverable failures. |
| Synthesizer (`synthesizer`) | `complete-cited-report` (`report_present`, `citations_known`, `coverage`, `limitations_present`, `persistence_truthful`); `conflict-and-limitations` (`conflict_represented`, `no_overstatement`, `limitations_present`, `citations_known`); `write-or-memory-failure` (`report_present_in_state`, `failure_recorded`, `no_false_persistence_claim`, `citations_known`) | `synthesizer_report_draft` | `src/deep_research/agents/synthesizer.py`, `src/deep_research/agents/prompts.py`, `tests/test_agents/test_synthesizer.py`; preserve the locally assembled evidence-only skeleton, truthful persistence claims, known citations, and write/memory fallback errors. This agent is non-ReAct. |
| Critic (`critic`) | `approve-strong-report` (`score_bounded`, `route_consistent`, `rationale_present`, `no_spurious_gaps`); `request-more-research` (`route_consistent`, `gaps_actionable`, `gaps_identified`, `score_bounded`); `missing-evidence-or-budget-exhausted` (`route_discipline`, `conservative_score`, `failure_recorded`, `score_bounded`) | `react_decision`; `critic_report_review` | `src/deep_research/agents/critic.py`, `src/deep_research/agents/prompts.py`, `tests/test_agents/test_critic.py`; preserve score bounds, concrete gap/routing behavior, macro-iteration limits, and the existing fallback critique/routing decision. |

### Controlled status and terminal-state contract

The harness status and process exit code are not interchangeable with the ignored ledger's workflow state. Record both exactly:

| Harness result | Exit | Required ledger routing |
| --- | ---: | --- |
| `REVIEW REQUIRED` | `0` | The nine-repetition result is eligible for a reviewer gate. If no later shared change invalidates it, record `terminal_state: REVIEW_REQUIRED` for a repaired agent or `terminal_state: UNCHANGED_BASELINE_REUSED` for an untouched passing baseline. |
| `FAILED` | `1` | Keep the complete artifact immutable. Route to `DIAGNOSING`; do not call the agent repaired. A quality failure and a typed target/provider failure must be separated before any edit. |
| `INFRASTRUCTURE FAILURE` | `3` | Perform one same-SHA, same-config confirmation. If the typed infrastructure/trace/artifact failure persists, record `INFRASTRUCTURE_BLOCKED` and stop that agent without counting a repair attempt. |
| Invalid usage or case/agent selection | `2` | Stop for a command correction; do not treat it as an evaluation result or spend another provider call. |

Use these exact control-plane terminal values in each `terminal-state.md`: `REVIEW_REQUIRED`, `UNCHANGED_BASELINE_REUSED`, `INFRASTRUCTURE_BLOCKED`, `HARNESS_DEFECT_BLOCKED`, or `ESCALATED`. `DEFERRED_NON_LOAD_BEARING` and `PARKED_INFRASTRUCTURE` are finding dispositions, not substitutes for an agent terminal state. Never add a new value to the harness `EvaluationStatus` type.

### Post-gate producer/consumer and status interface

Tasks 9–15 are one sequential control-plane. A later task may consume only the
artifact named by the preceding task and may not infer a missing status from a
score or a process exit code:

| Producer | Required output | Consumer and allowed transition |
| --- | --- | --- |
| Task 9 typed telemetry contract | Reviewed additive artifact contract; bounded deterministic metric map; exact prohibited-call count; typed nullable ReAct stop reason; narrow nullable fallback `{kind, operation}` projection; focused/evaluation/full-offline tests; offline inventory proof; Luna-max approval | Task 10 only. No controlled baseline is eligible before this row is complete. |
| Task 10 baseline acquisition | One immutable provenance record, one strictly validated three-case × three-repetition inventory, a typed failure inventory, and one per-agent routing decision | Task 11 may consume only exact target-side `output_limit` evidence; Task 12 may consume only typed quality/trajectory or non-budget provider/schema evidence; Task 13 may consume a passing immutable baseline or reviewed candidate. |
| Task 11 budget amendment | A reviewed operation-specific config/call-site candidate, offline RED/GREEN evidence, and a focused three-repetition result | Task 13 consumes the candidate only when the focused gate passes; persistent/reclassified failure returns to Task 10 diagnosis or Task 12, never directly to full validation. |
| Task 12 agent repair loop | A root-cause record, literal amendment, fresh review record, candidate commit, offline RED/GREEN evidence, and focused three-repetition result | Task 13 consumes the candidate only when the focused gate passes; three unsuccessful focused attempts for one root-cause ID produce `ESCALATED` and stop. |
| Task 13 full validation | One immutable nine-repetition artifact or explicit infrastructure/harness/escalation terminal record | Task 14 consumes the terminal record; an unchanged passing baseline is consumed without another paid run. |
| Task 14 fix log | One tracked safe section per non-Planner agent and a terminal-state table covering all five agents, updated for Tasks 10–13 evidence | Task 15 consumes the log, terminal records, and tracked source state. |
| Task 15 final offline verification | Final offline pytest/Ruff/diff evidence plus a branch-review handoff state | Whole-branch review may be dispatched only after the user explicitly asks for it. |

The ledger uses the workflow states already defined by the approved workflow:
`BASELINE_REQUIRED`, `DIAGNOSING`, `REPAIRING`, `FOCUSED_RETEST`,
`FULL_VALIDATION`, `REVIEW_REQUIRED`, `ESCALATED`, and
`INFRASTRUCTURE_BLOCKED`. The campaign phase label left by Task 8,
`CONTROLLED_BASELINES_REQUIRED`, is retained as the parent phase; each agent
gets its own workflow state and terminal-state value under that phase. Harness
status values remain exactly `REVIEW REQUIRED`, `FAILED`, and
`INFRASTRUCTURE FAILURE`, with exit codes `0`, `1`, and `3`; exit `2` is
invalid usage and is never an evaluation result.

For a baseline or candidate to receive `REVIEW_REQUIRED`, all of the following
must be explicit in its inventory: 3 cases, 3 repetitions per case, every
repetition complete, every hard gate passing, every aggregate score at least
`0.65`, every case average at least `0.80`, every expected judge result
`status == "scored"`, no judge-not-run reason, no unexplained target or
fallback provider failure, no trace/artifact/secret failure, and no prohibited
call. A case's deliberately scripted recovery behavior may produce a safe
non-provider error (for example, a search, reputation, write, or budget
failure in the named failure case); it must be the exact expected case
behavior and must pass the corresponding frozen `failure_recorded` or
conservative-output gate. It is not a target output-limit candidate.

### Frozen evaluation inputs during agent repair loops

Do not modify these while tuning an agent:

```text
src/deep_research/evaluation/cases/researcher.py
src/deep_research/evaluation/cases/source_evaluator.py
src/deep_research/evaluation/cases/fact_checker.py
src/deep_research/evaluation/cases/synthesizer.py
src/deep_research/evaluation/cases/critic.py
src/deep_research/evaluation/evaluators.py
src/deep_research/evaluation/judging.py
src/deep_research/evaluation/runner.py
src/deep_research/evaluation/reporting.py
src/deep_research/evaluation/config.py
src/deep_research/evaluation/dependencies.py
src/deep_research/evaluation/models.py
```

`src/deep_research/evaluation/targets.py` may change only in the explicit offline artifact-visibility portion of Task 9, not during any later agent quality repair. Task 9 may also modify `src/deep_research/evaluation/models.py`, `src/deep_research/evaluation/evaluators.py`, and `src/deep_research/evaluation/runner.py` only for the bounded artifact contract described below; after Task 9's Luna-max approval these evaluation files are frozen again for Tasks 10–13 except through a separately approved harness change.

---

### Task 1: Create the Isolated SDD Campaign and Freeze the Known-Good Base — COMPLETE

**Files:**
- Create, ignored: this plan's `.superpowers/sdd/.../progress.md` workspace ledger
- Create, ignored if required by the local environment: a secret-safe repo-env launcher equivalent to the Planner campaign's launcher
- Verify: `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`
- Verify: this plan file

**Interfaces:**
- Consumes: `main` containing merge commit `bc67620666bbc41c516556d45602de6dd00d7102` and this plan.
- Produces: isolated branch/worktree, exact approved-base SHA, clean offline baseline, and a recovery ledger that survives context compaction.

- [x] **Step 1: Resolve the repository and verify the Planner remediation is in the base**

```powershell
$Repository = (git rev-parse --show-toplevel).Trim()
$PlannerMerge = 'bc67620666bbc41c516556d45602de6dd00d7102'
$Plan = 'docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md'
$Branch = 'codex/cross-agent-planner-fix-parity'
$Worktree = Join-Path $Repository '.worktrees\cross-agent-planner-fix-parity'

$ApprovedBase = (git -C $Repository rev-parse main).Trim()
git -C $Repository merge-base --is-ancestor $PlannerMerge $ApprovedBase
if ($LASTEXITCODE -ne 0) { throw 'main does not contain the known-good Planner remediation merge' }
git -C $Repository cat-file -e "$ApprovedBase`:$Plan"
if ($LASTEXITCODE -ne 0) { throw 'approved base does not contain this plan' }
```

Expected: both checks exit 0 and `$ApprovedBase` is a literal 40-character SHA.

- [x] **Step 2: Create or verify the worktree without deleting anything unexpected**

```powershell
if (Test-Path -LiteralPath $Worktree) {
    if ((git -C $Worktree branch --show-current).Trim() -ne $Branch) {
        throw 'campaign worktree path belongs to another branch'
    }
} else {
    if (git -C $Repository branch --list $Branch) {
        throw 'campaign branch exists without the expected worktree; inspect manually'
    }
    git -C $Repository worktree add -b $Branch $Worktree $ApprovedBase
}
Set-Location -LiteralPath $Worktree
if (git status --porcelain) { throw 'campaign worktree is dirty before execution' }
```

Expected: clean `codex/cross-agent-planner-fix-parity` at `$ApprovedBase`.

- [x] **Step 3: Initialize the SDD workspace and ledger**

Use the current Superpowers `subagent-driven-development` workspace helper if installed. The ledger's first line must identify this plan exactly:

```markdown
# SDD ledger — plan: docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md
```

Then record these literal values beneath it:

```markdown
- Approved base: <the 40-character SHA printed in Step 1>
- Planner reference merge: bc67620666bbc41c516556d45602de6dd00d7102
- Branch: codex/cross-agent-planner-fix-parity
- Worktree: .worktrees/cross-agent-planner-fix-parity
- Global max tokens: 4096
- Live tier: prohibited
- Workflow state: OFFLINE_BASELINE_REQUIRED
```

Do not save the angle-bracket text; replace it with the actual SHA.

- [x] **Step 4: Verify interpreter/editable-install provenance**

```powershell
python -m pip install -e ".[dev]"
python -c "import pathlib, deep_research; print(pathlib.Path(deep_research.__file__).resolve())"
```

Expected: the printed module path resolves under this campaign worktree's `src`.

- [x] **Step 5: Run the complete tracked offline baseline**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
```

Expected: pytest matches or improves the merged Planner-remediation baseline (PR #19 recorded 1881 passed, 1 deselected, 2 known dependency warnings), Ruff is clean, and `git diff --check` exits 0. If the local Windows temp-root reproduces the documented path-length issue, use a short `--basetemp` and record that exact ruling in the ledger; do not change tracked code to fix the environment.

- [x] **Step 6: Commit no code in this task**

The task ends with a clean baseline and ledger only. Record `Task 1: complete` in the ignored ledger. No tracked commit is required.

---

### Task 2: Move RC-A Normalization to the Shared ReAct Boundary — COMPLETE

**Files:**
- Modify: `src/deep_research/agents/react.py`
- Test: `tests/test_agents/test_react.py`

**Interfaces:**
- Consumes: a `ReActDecision` whose unused optional field may be `""` after provider validation.
- Produces: every `ReActStep` stores `tool_name=None` on finish decisions and `final_answer=None` on tool decisions; meaningful non-empty values are unchanged.
- Does not change: `ReActDecision` schema, `ReActStep` schema, provider contracts, stop reasons, tool-budget behavior, or provider-error propagation mode.

- [x] **Step 1: Add the two direct failing shared-loop regression tests**

Append tests beside `test_one_step_loop_finishes_immediately` / `test_multi_step_loop_calls_a_tool_then_finishes` in `tests/test_agents/test_react.py`:

```python
@pytest.mark.asyncio
async def test_finish_decision_normalizes_empty_unused_tool_name(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Enough evidence.",
        action="finish",
        tool_name="",
        tool_input_json="{}",
        final_answer="Done.",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker),
            decide=_decider([decision]),
            max_iterations=2,
            tool_budget=0,
        )

    assert run.stop_reason == "finished"
    assert run.steps[0].tool_name is None
    assert run.steps[0].final_answer == "Done."


@pytest.mark.asyncio
async def test_tool_decision_normalizes_empty_unused_final_answer(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Check one source.",
        action="use_tool",
        tool_name="echo",
        tool_input_json='{"value": "x"}',
        final_answer="",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [decision, finish("Enough.", "Done.")]
            ),
            max_iterations=2,
            tool_budget=1,
        )

    assert run.steps[0].tool_name == "echo"
    assert run.steps[0].final_answer is None
```

- [x] **Step 2: Run them and verify RED**

```powershell
python -m pytest -q tests/test_agents/test_react.py -k "normalizes_empty_unused"
```

Expected before the fix: both tests fail at `ReActStep(...)` validation because the unused value is an empty string.

- [x] **Step 3: Apply the minimal shared-boundary fix**

In the existing `ReActStep(...)` construction in `run_react_loop`, change only the two optional values:

```python
step = ReActStep(
    iteration=iteration,
    thought=decision.thought,
    action=decision.action,
    tool_name=decision.tool_name or None,
    tool_input=tool_input,
    observation=observation,
    tool_result=tool_result,
    final_answer=decision.final_answer or None,
)
```

Do not weaken `ReActStep`'s `min_length=1` contract. Do not add a second provider wrapper. `ContractModel` already strips whitespace, so `field or None` also handles whitespace-only unused strings after model validation.

- [x] **Step 4: Verify focused and neighboring GREEN**

```powershell
python -m pytest -q tests/test_agents/test_react.py -k "normalizes_empty_unused"
python -m pytest -q tests/test_agents/test_react.py tests/test_agents/test_base.py
python -m ruff check src/deep_research/agents/react.py tests/test_agents/test_react.py
git diff --check
```

Expected: all commands pass.

- [x] **Step 5: Commit**

```powershell
git add src/deep_research/agents/react.py tests/test_agents/test_react.py
git commit -m "fix(agents): normalize optional ReAct step fields"
```

Record commit SHA and `Task 2: complete` in the ledger.

---

### Task 3: Remove the Planner-Local RC-A Workaround and Prove Cross-Agent ReAct Parity — COMPLETE

**Files:**
- Modify: `src/deep_research/agents/planner.py`
- Modify: `tests/test_agents/test_planner.py`
- Modify: `tests/test_agents/test_researcher.py`
- Modify: `tests/test_agents/test_fact_checker.py`
- Modify: `tests/test_agents/test_critic.py`
- Verify: `tests/test_evaluation/test_factory.py`
- Verify: `tests/test_runtime/test_assembly.py`

**Interfaces:**
- Consumes: Task 2's shared ReAct normalization.
- Produces: Planner no longer swaps/wraps its provider for RC-A; Researcher, Fact Checker, and Critic all complete a scripted ReAct path containing an empty unused optional field.
- Preserves: Planner's `preserve_provider_errors=True`, `planning_provider_error("react_decision")` cause wrapping, and `planner_final_max_tokens` only on `ResearchPlanDraft`.

- [x] **Step 1: Add/retain parity tests before deleting Planner code**

Keep the existing Planner RC-A regression tests unchanged. Add one agent-level regression to each custom ReAct agent using its existing test constructors/fakes. Each test must queue a valid decision whose *unused* optional field is `""` and assert the agent does not raise `ValidationError`.

Required assertions:

```text
Researcher: the sub-topic ReAct run completes and the stored step has final_answer is None on a tool decision.
Fact Checker: the per-claim verification loop completes and the stored step has tool_name is None on a finish decision.
Critic: the spot-check loop completes and the stored step has final_answer is None on a tool decision.
```

Use the existing `ScriptedCompleter` and each test module's current state/tool fixtures rather than introducing a second fake framework.

- [x] **Step 2: Verify the new sibling tests are already GREEN on Task 2**

```powershell
python -m pytest -q tests/test_agents/test_researcher.py -k "empty_unused"
python -m pytest -q tests/test_agents/test_fact_checker.py -k "empty_unused"
python -m pytest -q tests/test_agents/test_critic.py -k "empty_unused"
```

Expected: all pass because Task 2 fixed the shared boundary. If any fails for a different reason, record the exact failure as a separate root-cause candidate; do not weaken the test or copy the Planner wrapper into that agent.

- [x] **Step 3: Delete only the Planner-local normalization wrapper**

In `planner.py`:
- remove `_DecisionNormalizingCompleter` completely;
- remove imports used only by that wrapper (`Any`, `StructuredCompleter`, and `ReActDecision` if no longer used elsewhere);
- simplify `PlannerAgent.run()` so it does not replace `self._provider`;
- retain the provider-error translation:

```python
try:
    outcome = await super().run(state)
except ProviderError as error:
    raise planning_provider_error("react_decision") from error
```

Do not change `_request_plan()` or its `max_tokens=self.config.planner_final_max_tokens` argument.

- [x] **Step 4: Run the Planner and provider-identity regression set**

```powershell
python -m pytest -q tests/test_agents/test_planner.py -k "planner_regression or provider"
python -m pytest -q tests/test_evaluation/test_factory.py tests/test_runtime/test_assembly.py
python -m pytest -q tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_fact_checker.py tests/test_agents/test_critic.py
python -m ruff check src/deep_research/agents tests/test_agents
```

Expected: the Planner's known-good RC-A tests remain green and exact provider identity/parity tests pass.

- [x] **Step 5: Commit**

```powershell
git add src/deep_research/agents/planner.py `
  tests/test_agents/test_planner.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_critic.py
git commit -m "refactor(agents): share ReAct decision normalization"
```

Record commit SHA and `Task 3: complete`.

---

### Task 4: Add a Shared Safe Snapshot for Provider Failures Caught by Fallbacking Agents — COMPLETE

**Files:**
- Modify: `src/deep_research/providers/contracts.py`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/agents/errors.py`
- Test: `tests/test_agents/test_errors.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_openai_provider.py`

**Interfaces:**
- Consumes: direct `ProviderError` subclasses already emitted by the providers.
- Produces: one immutable, finite `ProviderFailureSnapshot` and `provider_failure_snapshot(error)` helper that never renders exception messages or provider content; one `agent_provider_failure_details(operation, error, **extra)` helper returning JSON-safe details.
- The snapshot is runtime/provider infrastructure, not an evaluation model; agents must not import from `deep_research.evaluation`.

- [x] **Step 1: Write RED tests for the safe finite projection**

Add tests that construct:
- `ProviderOutputLimitError` with `finish_reason_category="length"`, `configured_max_tokens=4096`, typed usage, request attempt 2, structured attempt 1;
- `StructuredOutputError` with two `StructuredValidationDiagnostic` records;
- `ProviderTimeoutError`;
- `ProviderRateLimitError`;
- `ProviderResponseError` for transport, HTTP 503 retryable, HTTP 401 nonretryable, and generic response.

For each, assert the snapshot has exactly one finite kind from:

```text
output_limit
schema_output
provider_timeout
provider_rate_limit
provider_transport
provider_http
provider_response
provider_failure
```

For output-limit, assert configured cap, usage, request attempt, and structured attempt are preserved. For schema output, assert only normalized bounded field paths and attempts are preserved. For HTTP, assert only validated status and retryability are preserved.

Add an adversarial error whose message contains `PROVIDER_SECRET_SENTINEL` and assert that sentinel is absent from both `snapshot.model_dump(mode="json")` and `repr(snapshot.model_dump(mode="json"))`.

- [x] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py -k "provider_failure_snapshot or agent_provider_failure_details"
```

Expected: collection/assertions fail because the shared snapshot/helper do not exist.

- [x] **Step 3: Implement `ProviderFailureSnapshot` in provider contracts**

Define an immutable model with only these fields:

```python
ProviderFailureKind: TypeAlias = Literal[
    "output_limit",
    "schema_output",
    "provider_timeout",
    "provider_rate_limit",
    "provider_transport",
    "provider_http",
    "provider_response",
    "provider_failure",
]

class ProviderFailureSnapshot(ProviderContract):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    kind: ProviderFailureKind
    exception_type: str = Field(min_length=1, max_length=128)
    retryable: bool | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    configured_max_tokens: PositiveInt | None = None
    usage: TokenUsage | None = None
    request_attempt: PositiveInt | None = None
    structured_attempt: PositiveInt | None = None
    diagnostics: tuple[StructuredValidationDiagnostic, ...] = Field(
        default=(), max_length=2
    )
```

Implement `provider_failure_snapshot(error: ProviderError) -> ProviderFailureSnapshot` by type, most specific first. Do not inspect `str(error)`. Map `ProviderResponseError.failure_category="output_limit"` to `provider_response` unless the concrete type is `ProviderOutputLimitError`, matching the evaluation taxonomy's existing conservative rule.

- [x] **Step 4: Implement the agent JSON helper**

In `agents/errors.py`, add:

```python
def agent_provider_failure_details(
    operation: str,
    error: ProviderError,
    **extra: JsonValue,
) -> dict[str, JsonValue]:
    if not operation.strip():
        raise ValueError("operation must not be blank")
    snapshot = provider_failure_snapshot(error)
    return {
        "operation": operation.strip(),
        "provider_failure": snapshot.model_dump(mode="json"),
        **extra,
    }
```

Do not retain a second `exception_type` outside the snapshot. Export the provider snapshot/helper from `providers.__init__` using the package's existing explicit-export style.

- [x] **Step 5: Run GREEN and neighboring provider tests**

```powershell
python -m pytest -q tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py -k "provider_failure_snapshot or agent_provider_failure_details"
python -m pytest -q tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_retry_policy.py tests/test_evaluation/test_failure_taxonomy.py
python -m ruff check src/deep_research/providers src/deep_research/agents/errors.py tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py
```

Expected: all pass; existing evaluation taxonomy behavior remains unchanged.

- [x] **Step 6: Commit**

```powershell
git add src/deep_research/providers/contracts.py `
  src/deep_research/providers/__init__.py `
  src/deep_research/agents/errors.py `
  tests/test_agents/test_errors.py `
  tests/test_deepseek_provider.py `
  tests/test_openai_provider.py
git commit -m "feat(agents): retain safe provider failure diagnostics"
```

Record commit SHA and `Task 4: complete`.

---

### Task 5: Wire Safe Provider Diagnostics into Every Non-Planner Fallback Path — COMPLETE

**Files:**
- Modify: `src/deep_research/agents/react.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/critic.py`
- Test: corresponding six `tests/test_agents/test_*.py` modules

**Interfaces:**
- Consumes: Task 4 `agent_provider_failure_details`.
- Produces: every caught provider failure has a static operation name plus the safe snapshot while retaining the agent's exact existing stop/fallback behavior.

Use these operation names verbatim:

```text
react_decision
researcher_finding_extraction
source_evaluator_scoring
fact_checker_claim_extraction
fact_checker_claim_verification
synthesizer_report_draft
critic_report_review
```

- [x] **Step 1: Add RED assertions to each existing provider-failure test**

For each path, replace assertions that only expect `{"exception_type": ...}` with assertions on:

```python
assert error.details["operation"] == "<exact operation above>"
provider = error.details["provider_failure"]
assert provider["kind"] == "output_limit"
assert provider["configured_max_tokens"] == 4096
assert provider["request_attempt"] == 1
```

Use a `ProviderOutputLimitError` in at least one test for every operation. Add one schema-output test to Researcher extraction and one transport/HTTP test to Critic or Source Evaluator so non-output categories are exercised through an agent boundary too.

Also keep/extend each test's semantic assertions:
- Researcher: earlier findings survive and later subtopics stop.
- Source Evaluator: every source still gets a low-confidence fallback row.
- Fact Checker: already-completed claims survive; failed verdict becomes `insufficient_evidence` exactly as before.
- Synthesizer: report skeleton is still composed/written from recorded evidence.
- Critic: fallback critique/routing remains unchanged.
- ReAct compatibility mode: `stop_reason == "provider_error"`, nonrecoverable ResearchError, no raised provider exception when `propagate_provider_errors=False`.

- [x] **Step 2: Run RED**

```powershell
python -m pytest -q `
  tests/test_agents/test_react.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_source_evaluator.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_synthesizer.py `
  tests/test_agents/test_critic.py `
  -k "provider or output_limit or schema"
```

Expected: new detail assertions fail because the current code records only generic exception type/counts.

- [x] **Step 3: Replace only provider-error `details` construction**

Use `agent_provider_failure_details(...)` in each provider catch. Preserve every existing `error_type`, static user-facing message, `recoverable` value, stop reason, fallback object, and loop-break rule.

Examples of the intended shape:

```python
return agent_error(
    agent_name=RESEARCHER_NAME,
    error_type="researcher_extraction_provider_error",
    message=(...),
    recoverable=False,
    details=agent_provider_failure_details(
        "researcher_finding_extraction",
        error,
        iterations=run.iterations,
        tool_calls=run.tool_calls,
    ),
)
```

```python
errors.append(
    agent_error(
        agent_name=agent_name,
        error_type="agent_provider_error",
        message="The model provider failed and the ReAct loop stopped.",
        recoverable=False,
        details=agent_provider_failure_details(
            "react_decision", error, iteration=iteration
        ),
    )
)
```

Do not change Planner's raised cause-chain behavior; when `propagate_provider_errors=True`, the shared loop still re-raises the original provider exception after recording its safe event.

- [x] **Step 4: Run focused and full agent tests**

```powershell
python -m pytest -q tests/test_agents
python -m pytest -q tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_targets.py
python -m ruff check src/deep_research/agents tests/test_agents
```

Expected: all pass.

- [x] **Step 5: Commit**

```powershell
git add src/deep_research/agents/react.py `
  src/deep_research/agents/researcher.py `
  src/deep_research/agents/source_evaluator.py `
  src/deep_research/agents/fact_checker.py `
  src/deep_research/agents/synthesizer.py `
  src/deep_research/agents/critic.py `
  tests/test_agents/test_react.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_source_evaluator.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_synthesizer.py `
  tests/test_agents/test_critic.py
git commit -m "fix(agents): preserve typed provider diagnostics in fallbacks"
```

Record commit SHA and `Task 5: complete`.

---

### Task 6: Prove Typed Fallback Diagnostics Survive the Evaluation Artifact Boundary — COMPLETE

**Files:**
- Modify: `tests/test_evaluation/test_targets.py`
- Modify `src/deep_research/evaluation/targets.py` only if a failing test demonstrates the existing `_success_output` path drops or corrupts the safe details

**Interfaces:**
- Consumes: a non-Planner agent that intentionally returns a fallback `AgentRun.result` plus `run.errors` containing Task 5 provider snapshots.
- Produces: `TargetOutput.completed=True`, `TargetOutput.failure=None`, and the safe typed provider snapshot preserved in `TargetOutput.errors` for fallback-producing agents; Planner exceptions continue to produce `completed=False` with top-level typed `failure` via the cause-chain taxonomy.

- [x] **Step 1: Add a target-level fallback test**

Extend the target harness/fakes using the existing fixture style so one non-Planner agent returns a valid fallback result and one `ResearchError` whose `details.provider_failure.kind == "output_limit"`. Assert:

```python
output = TargetOutput.model_validate(payload)
assert output.completed is True
assert output.failure is None
assert output.result is not None
assert output.errors[0]["details"]["provider_failure"]["kind"] == "output_limit"
assert output.errors[0]["details"]["provider_failure"]["configured_max_tokens"] == 4096
```

Also retain the existing Planner test that a raised output-limit cause becomes top-level `failure.reason == "output_limit"`.

- [x] **Step 2: Run the target tests**

```powershell
python -m pytest -q tests/test_evaluation/test_targets.py -k "provider or fallback or output_limit"
```

Expected: this should already be GREEN because `_success_output` serializes `run.errors`. If it is green, make no production target change; the test is the regression guard. If it fails because typed safe fields are dropped, fix only the serialization boundary required by the failing assertion, then rerun the complete target suite.

- [x] **Step 3: Run neighboring evaluation tests**

```powershell
python -m pytest -q tests/test_evaluation/test_targets.py tests/test_evaluation/test_models.py tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_runner.py
python -m ruff check src/deep_research/evaluation tests/test_evaluation/test_targets.py
```

- [x] **Step 4: Commit the test (and only a demonstrated minimal production fix if needed)**

```powershell
git add tests/test_evaluation/test_targets.py
if (git diff --name-only | Select-String 'src/deep_research/evaluation/targets.py') {
    git add src/deep_research/evaluation/targets.py
}
git commit -m "test(evaluation): preserve fallback provider diagnostics"
```

Record commit SHA and `Task 6: complete`.

---

### Task 7: Characterize Source Evaluator and Synthesizer as Non-ReAct Agents — COMPLETE

**Files:**
- Modify: `tests/test_agents/test_source_evaluator.py`
- Modify: `tests/test_agents/test_synthesizer.py`
- No production change expected

**Interfaces:**
- Produces a regression guard for the audit conclusion that RC-A does not apply to these two agents on current `main`.

- [x] **Step 1: Add test assertions that their normal provider calls never request `ReActDecision`**

Use `ScriptedCompleter.calls` / each module's existing recording provider. After a representative successful run, assert the requested schema sequence contains:

```text
Source Evaluator: SourceScoresDraft only
Synthesizer: ReportDraft only
```

and does not contain `ReActDecision`.

- [x] **Step 2: Run tests**

```powershell
python -m pytest -q tests/test_agents/test_source_evaluator.py tests/test_agents/test_synthesizer.py -k "react or schema or provider"
```

Expected: GREEN with no production edit. If a current code path unexpectedly requests `ReActDecision`, stop and record the architecture drift before proceeding; RC-A applicability must be reclassified.

- [x] **Step 3: Commit characterization tests**

```powershell
git add tests/test_agents/test_source_evaluator.py tests/test_agents/test_synthesizer.py
git commit -m "test(agents): pin non-ReAct agent architecture"
```

Record commit SHA and `Task 7: complete`.

---

### Task 8: Run the Offline Integration Gate Before Any Paid Evaluation — COMPLETE

**Files:**
- No production files
- Update ignored ledger

**Interfaces:**
- Consumes: Tasks 2-7.
- Produces: one reviewed offline candidate SHA eligible for controlled evaluation.

- [x] **Step 1: Run focused integration suites**

```powershell
python -m pytest -q `
  tests/test_agents `
  tests/test_deepseek_provider.py `
  tests/test_openai_provider.py `
  tests/test_retry_policy.py `
  tests/test_config.py `
  tests/test_evaluation/test_failure_taxonomy.py `
  tests/test_evaluation/test_targets.py `
  tests/test_evaluation/test_factory.py `
  tests/test_runtime/test_assembly.py
```

- [x] **Step 2: Run full tracked offline verification**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
git status --short
```

Expected: full suite green, Ruff clean, whitespace clean, tracked worktree clean.

- [x] **Step 3: Run the task review gate**

Dispatch a fresh reviewer on the complete diff from `$ApprovedBase` through current HEAD. Required review questions:

```text
1. Does shared RC-A normalization occur exactly once at the ReActDecision -> ReActStep boundary?
2. Is Planner behavior identical except for deleting its redundant local wrapper?
3. Do any provider diagnostic fields retain exception strings, raw finish values, provider content, validation input values, prompts, or secrets?
4. Did any fallback/partial-result behavior change unintentionally?
5. Did any global model budget, judge budget, retry classification, evaluator, gate, case, threshold, or rubric change?
6. Are Source Evaluator and Synthesizer correctly excluded from RC-A because they do not run ReAct?
```

Fix Critical/Important findings with TDD and scoped re-review before proceeding. Record review report path and rulings in the SDD ledger.

- [x] **Step 4: Freeze candidate SHA**

```powershell
$CandidateSha = (git rev-parse HEAD).Trim()
git status --porcelain
```

Expected: clean worktree. Record literal `$CandidateSha` in the ledger as `Offline candidate` and `Workflow state: CONTROLLED_BASELINES_REQUIRED`.

---

### Task 9: Repair the Evaluation Artifact Typed Telemetry Contract — COMPLETE (OFFLINE; LUNA-MAX REVIEW APPROVED)

**Purpose:** Repair only the evaluation artifact projection boundary so the already-required controlled-baseline telemetry survives into `RepetitionResult` and `results.json`. This task must not change agent behavior, frozen evaluation semantics, provider behavior, prompts, gates, thresholds, cases, rubrics, judges, model budgets, retry policy, or dependency scenarios.

**Execution model:** GPT-5.6 Luna, high reasoning.

**Required task-scoped reviewer:** one fresh GPT-5.6 Luna, max reasoning, after implementation and offline verification. Task 9 is not complete until that review approves the task. A load-bearing finding returns to Luna high, followed by offline verification and Luna-max scoped re-review.

**Network boundary:** Task 9 is network-zero. No provider, LangSmith, controlled evaluation, live evaluation, remote dataset, model, embedding, or credential-dependent call is permitted. Do not run the evaluation CLI in this task.

**Files:**

Modify only:

- `src/deep_research/evaluation/models.py`
- `src/deep_research/evaluation/evaluators.py`
- `src/deep_research/evaluation/runner.py`
- `tests/test_evaluation/test_models.py`
- `tests/test_evaluation/test_evaluators_general.py`
- `tests/test_evaluation/test_runner.py`
- `tests/test_evaluation/test_targets.py`
- `tests/test_evaluation/test_reporting.py`
- `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/record_eval_inventory.py` (ignored campaign helper; update only to project the new bounded typed fields and retain fail-closed validation)

Verify first; modify `src/deep_research/evaluation/targets.py` or `src/deep_research/evaluation/reporting.py` only if a focused RED test proves the existing typed source or normal model serialization is insufficient. The ignored inventory helper may be updated only to consume the new typed artifact fields and reject missing/unsafe telemetry. Do not change controlled cases, judges, prompts, agents, providers, configuration budgets, retry policy, or dependency scenarios.

**Consumes:** Task 8's reviewed offline candidate; typed `TargetOutput` fields for `dependencies.prohibited_calls`, `react.stop_reason`, and safe fallback errors containing `details.operation` and `details.provider_failure.kind`; the existing metric definitions, weights, and runtime vocabularies.

**Produces:** an additive backward-compatible `RepetitionResult` contract preserving the existing scalar fields and additionally preserving `deterministic_metrics`, `prohibited_call_count`, `react_stop_reason`, and `fallback_provider_diagnostic`; a local synthetic artifact proof; a fail-closed inventory proof; offline test evidence; and Luna-max approval.

#### Exact typed artifact contract

Keep `ARTIFACT_SCHEMA_VERSION` unchanged. Existing model consumers must still parse older result objects; newly generated Task 10 artifacts must physically contain the new telemetry keys.

Add bounded artifact-side vocabulary in `src/deep_research/evaluation/models.py`:

```python
_MAX_ARTIFACT_DETERMINISTIC_METRICS = 16
_MAX_ARTIFACT_METRIC_ID_LENGTH = 64
_MAX_ARTIFACT_OPERATION_LENGTH = 96
_MAX_ARTIFACT_PROHIBITED_CALL_COUNT = 10_000

ReActStopReason: TypeAlias = Literal[
    "finished",
    "sufficient",
    "max_iterations",
    "tool_budget_exhausted",
    "provider_error",
]

class FallbackProviderDiagnostic(ContractModel):
    kind: ProviderFailureKind
    operation: str = Field(
        min_length=1,
        max_length=_MAX_ARTIFACT_OPERATION_LENGTH,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
```

The stop-reason vocabulary must match the current runtime vocabulary exactly. `ReActSummary.stop_reason` must use the bounded alias rather than an unbounded string.

Add these fields to `RepetitionResult` without removing or renaming existing fields:

```python
deterministic_metrics: dict[str, UnitScore] = Field(
    default_factory=dict,
    max_length=_MAX_ARTIFACT_DETERMINISTIC_METRICS,
)
prohibited_call_count: int = Field(
    default=0,
    ge=0,
    le=_MAX_ARTIFACT_PROHIBITED_CALL_COUNT,
    strict=True,
)
react_stop_reason: ReActStopReason | None = None
fallback_provider_diagnostic: FallbackProviderDiagnostic | None = None
```

Metric IDs must be non-empty lower snake case matching `^[a-z][a-z0-9_]{0,63}$`; values must be finite `UnitScore` values in `[0.0, 1.0]`; oversized or malformed maps fail closed rather than being truncated. `prohibited_call_count` is exactly `len(output.dependencies.prohibited_calls)`, never a gate-string inference or clamp. ReAct agents preserve the exact typed stop reason; Source Evaluator and Synthesizer explicitly serialize `None`. The fallback projection is the first valid safe record in `output.errors` order and contains only `{kind, operation}`. Never copy and redact arbitrary error details.

#### Deterministic metric-detail compatibility

Add a bounded helper in `src/deep_research/evaluation/evaluators.py` that evaluates every declared metric and returns an exact metric-ID-to-`UnitScore` map. Boolean pass/fail becomes `1.0`/`0.0`; a metric implementation exception records `0.0` and continues; a missing metric implementation still raises `MissingMetricError`. Preserve the current metric definitions, weights, failure semantics, scalar `deterministic_quality`, thresholds, and public evaluator APIs. The runner must carry both the unchanged scalar and the new map.

#### Runner projection and safety

Update `build_repetition_result(...)` and its existing bookkeeping to set the four new fields from typed source values only. Preserve the meaning of all existing fields. The allow-list must exclude raw exception text, provider/model output, prompts, evaluator inputs, hidden reasoning, credentials, request payloads, arbitrary details, unbounded paths/lists/text, and raw prohibited tool names.

#### Required TDD sequence

- [x] Add RED model tests for valid metrics, metric bounds/IDs/values, strict prohibited-call counts, valid/unknown stop reasons, and the exact two-field fallback diagnostic with rejection of unsafe extra fields.
- [x] Add RED evaluator tests proving complete metric maps, Boolean conversion, exception-to-zero behavior, missing-metric failure, and unchanged weighted scalar quality.
- [x] Add RED runner tests using a local `TargetOutput` with two prohibited calls, a valid stop reason, pass/fail metrics, and a safe fallback error plus sentinel unsafe values. Prove only the four typed projections survive serialization; add Source Evaluator and Synthesizer `None` cases.
- [x] Add target characterization tests for the typed source fields and reporting round-trip tests for local `ExperimentResult` serialization. Do not force already-green characterization tests to fail.
- [x] Run the focused offline evaluation tests with `-p no:cacheprovider`; no evaluation CLI command is permitted.
- [x] Implement the smallest typed-contract change with Luna high.
- [x] Run the focused tests, `python -m pytest -q tests/test_evaluation -p no:cacheprovider`, the full offline suite, Ruff lint/targeted formatting, `git diff --check`, and an allowed-file diff review. Repository-wide format-only deviations remain documented pre-existing baseline.
- [x] Build a local synthetic three-case × three-repetition artifact and prove `record_eval_inventory.py` accepts complete typed telemetry and fails closed when empty, incomplete, extra, or malformed metric maps are supplied. Do not run a provider or LangSmith command.
- [x] Write the Task 9 ledger evidence, commit the scoped change, and obtain one fresh Luna-max task review. Record `Task 9 review: APPROVED` and `Task 9: complete` only after approval.

**Hard stop:** Task 10 cannot begin until every Task 9 offline test, inventory proof, diff check, commit, and Luna-max review gate passes. The previous 1,895-test result does not certify this new task.

---

### Infrastructure Repair Amendment: Windows Evaluation Output-Root Boundary — COMPLETE

The first Task 10 baseline attempt was run at candidate `e05ff277db4b60456be93a2e03bd55e742eb0f82`. All five agents failed during Windows preflight before producing `results.json`; the same-SHA confirmation attempts reproduced the infrastructure failure. The typed evidence identifies the failure boundary as the long local output-root path, not target quality or provider behavior.

The repair was executed as a new candidate and reviewed before resuming Task 10:

- Task 2 RED coverage: `92afc9c972677f5d39f454e42316254a9121b345`.
- Task 2 review-fix commits: `5fff3b906d7037b84d31309b2e84735ee727d2ed`, `3ca4ccdaec7c69559331e1dea0d747f98e4fc5c9`.
- Task 2 Luna-max review: approved; exact six Task 10 prefixes, host-independent path cases, real production preflight seam, repetition descendants, and `results.json` coverage accepted.
- Task 3 implementation: `d7ff281acdfac9394db75c25eb3c6fa2cafe1b41`.
- Task 3 Luna-max review: approved; only `src/deep_research/evaluation/config.py` changed, with a private `_extended_windows_path` boundary applied after the complete logical output root is assembled.
- New controlled-run source candidate: `d7ff281acdfac9394db75c25eb3c6fa2cafe1b41`; current bookkeeping descendant used for all controlled runs: `d6a082c9260e5d9f2f7c061833b4734ec9990f1f`.

The historical failed artifacts remain immutable. Task 10 may resume only from the new candidate, with fresh per-agent namespaces and the existing immediate per-command human authorization requirement.

---

### Task 10: Run Immutable Controlled Baselines for Each Non-Planner Agent — COMPLETE (FIVE AGENTS INFRASTRUCTURE-BLOCKED AFTER CONFIRMATION)

**Hard paid-call precondition:** Do not execute any provider, LangSmith, controlled-evaluation, dataset-sync, judge, or credential-dependent command until the ledger contains all of these exact lines: `Task 9 focused tests: PASS`, `Task 9 evaluation tests: PASS`, `Task 9 full offline suite: PASS`, `Task 9 inventory contract check: PASS`, `Task 9 review: APPROVED`, and `Task 9: complete`. This local-only check does not replace immediate human authorization for each paid command.

**Files:**
- No tracked production/test changes during baseline acquisition
- Update ignored ledger
- Create ignored provenance, inventory, and packet files under the five exact agent roots in the post-gate artifact table
- Create immutable `output/evaluations/researcher/*/results.json`, `output/evaluations/source-evaluator/*/results.json`, `output/evaluations/fact-checker/*/results.json`, `output/evaluations/synthesizer/*/results.json`, and `output/evaluations/critic/*/results.json` artifacts through the existing harness

**Interfaces:**
- Consumes: Task 8 candidate SHA and the existing frozen controlled datasets.
- Produces: one immutable three-case × three-repetition baseline attempt per non-Planner agent at the same candidate SHA; a safe effective-configuration/provenance record; a bounded baseline inventory; a typed failure inventory; and one explicit routing decision for each agent. No source edit is permitted in this task.

**Run order:** `researcher`, `source_evaluator`, `fact_checker`, `synthesizer`, `critic`.

The execution map is fixed before any provider command. The internal name is used in artifacts and Python; the CLI name is used in commands and output directories.

| Internal name | CLI name | Target effort | Frozen controlled cases | Exact target operations eligible for diagnosis |
| --- | --- | --- | --- | --- |
| `researcher` | `researcher` | `high` | `multi-source-coverage`, `conflicting-evidence`, `partial-search-failure` | `react_decision`, `researcher_finding_extraction` |
| `source_evaluator` | `source-evaluator` | `high` | `strong-and-weak-sources`, `corroboration-recency-reputation`, `reputation-provider-failure` | `source_evaluator_scoring` |
| `fact_checker` | `fact-checker` | `max` | `mixed-verdicts`, `independent-domain-evidence`, `verification-search-failure` | `react_decision`, `fact_checker_claim_extraction`, `fact_checker_claim_verification` |
| `synthesizer` | `synthesizer` | `max` | `complete-cited-report`, `conflict-and-limitations`, `write-or-memory-failure` | `synthesizer_report_draft` |
| `critic` | `critic` | `max` | `approve-strong-report`, `request-more-research`, `missing-evidence-or-budget-exhausted` | `react_decision`, `critic_report_review` |

- [ ] **Step 1: Preflight effective model/retry/budget configuration without printing secrets**

Run this offline from the campaign worktree. Do not create a second worktree, reset the branch, or remove any existing ignored packet. The command must print only the candidate SHA and path/state facts:

```powershell
$CampaignRoot = (git rev-parse --show-toplevel).Trim()
$LedgerPath = Join-Path $CampaignRoot '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\progress.md'
$PacketRoot = Join-Path $CampaignRoot '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity'
$RunWithEnv = Join-Path $PacketRoot 'run_with_repo_env.py'
$RepoEnv = Join-Path $CampaignRoot '.env'
$EnvSource = if (Test-Path -LiteralPath $RepoEnv) { $RepoEnv } else { '-' }
$CandidateSha = (git rev-parse HEAD).Trim()
if ($CandidateSha -notmatch '^[0-9a-f]{40}$') { throw 'Task 8 candidate is not a literal 40-character SHA' }
if (git status --porcelain) { throw 'tracked worktree is dirty at the Task 10 gate' }
if (-not (Test-Path -LiteralPath $LedgerPath)) { throw 'Task 8 ledger is missing' }
if (-not (Select-String -LiteralPath $LedgerPath -SimpleMatch 'Workflow state: CONTROLLED_BASELINES_REQUIRED')) {
    throw 'Task 8 did not leave the campaign at CONTROLLED_BASELINES_REQUIRED'
}
New-Item -ItemType Directory -Force -Path $PacketRoot | Out-Null
foreach ($CliAgent in @('researcher', 'source-evaluator', 'fact-checker', 'synthesizer', 'critic')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PacketRoot "agents\$CliAgent") | Out-Null
}
Write-Output "candidate_sha=$CandidateSha"
Write-Output "campaign_root=$CampaignRoot"
Write-Output "packet_root=$PacketRoot"
Write-Output "env_source_present=$([bool](Test-Path -LiteralPath $RepoEnv))"
```

Expected: the candidate SHA is the clean Task 8 SHA; all five literal packet roots exist; no credential value is printed; and no provider or LangSmith call occurs.

- [ ] **Step 2: Create the two offline-only packet helpers before any paid command**

Create these ignored files with `apply_patch`; do not add them to Git. The helpers must refuse to overwrite an existing output path, must use UTF-8 JSON, and must return a nonzero exit code on validation failure.

`record_eval_provenance.py` has this exact interface:

```text
python .superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/record_eval_provenance.py \
  --config config.yaml \
  --agent researcher \
  --output .superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/baseline-provenance.json
```

It loads `config.yaml` with the repository's existing `load_config(..., strict=False)` behavior, snapshots only environment-variable names and presence/source (`process`, `repo_dotenv`, or `absent`), and writes these safe fields: `schema_version`, UTC creation time, internal/CLI agent name, candidate Git SHA/short SHA/branch/dirty bit, absolute worktree path, Python executable/version, resolved `deep_research` import path, config path, effective target/judge model and effort, `llm.max_tokens`, `agents.planner_final_max_tokens`, `llm.retry_count`, `llm.retry_initial_delay`, `llm.retry_max_delay`, controlled repetitions/floor/case-average threshold/max concurrency, dataset/rubric versions, LangSmith endpoint, configuration/judge/prompt fingerprints, and boolean presence for `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and `OPENAI_API_KEY`. It must never write any environment value, `.env` value, prompt, request, response, exception text, or full environment dump.

`record_eval_inventory.py` has this exact interface:

```text
python .superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/record_eval_inventory.py \
  --agent researcher \
  --results $ResultsPath \
  --provenance .superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/baseline-provenance.json \
  --output .superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/agents/researcher/baseline-inventory.json
```

`$ResultsPath` must be the one literal path resolved by Step 7; it must not contain a wildcard, an ellipsis, or a guessed experiment directory. The helper strictly validates `ExperimentResult`, the agent/tier/case identities, three repetitions per case, the provenance SHA, and the results SHA-256. It writes only the bounded fields named by the inventory contract in the post-gate section: case/repetition IDs, completion, failed gate IDs/details, deterministic metric maps, judge dimensions, aggregate scores, typed target failure stage/reason/detail kind, safe fallback provider kinds/operations, typed judge status/reason/diagnostic kinds, ReAct stop reason, prohibited-call count, direct trace/evaluator/experiment URLs, configuration/prompt/judge fingerprints, model/effort values, and no raw messages. If a successful fallback `TargetOutput` is visible only in a target trace, the worker first writes a `target-output-projection.json` containing only its typed `operation` and provider snapshot fields; if the trace does not expose a safe projection, the inventory records `fallback_diagnostic_visibility: unavailable` and the agent cannot be promoted on that evidence.

Expected: `python ... --help` for both helpers is offline; the helpers have no provider imports or network calls; and a secret scan over either helper's output finds no known secret.

- [ ] **Step 3: Resolve effective configuration and retry/environment provenance**

Use the existing launcher or inherited environment to print only the following JSON object; do not print `os.environ`, `.env`, or any secret value:

```powershell
$env:CAMPAIGN_AGENT = 'researcher'
python $RunWithEnv $EnvSource python -c "import json, os; from deep_research.utils.config import load_config; s=load_config('config.yaml', strict=False); print(json.dumps({'agent':os.environ['CAMPAIGN_AGENT'],'llm_model':s.llm.model,'llm_max_tokens':s.llm.max_tokens,'retry_count':s.llm.retry_count,'retry_initial_delay':s.llm.retry_initial_delay,'retry_max_delay':s.llm.retry_max_delay,'planner_final_max_tokens':s.agents.planner_final_max_tokens,'target_model':s.evaluation.target_model,'target_effort':s.evaluation.target_reasoning_effort_overrides.get(os.environ['CAMPAIGN_AGENT'], s.evaluation.target_reasoning_effort),'judge_model':s.evaluation.judge_model,'judge_effort':s.evaluation.judge_reasoning_effort,'controlled_repetitions':s.evaluation.controlled_repetitions,'repetition_floor':s.evaluation.controlled_repetition_floor,'case_average_threshold':s.evaluation.controlled_case_average_threshold,'max_concurrency':s.evaluation.max_concurrency,'dataset_version':s.evaluation.dataset_version,'rubric_version':s.evaluation.rubric_version}, sort_keys=True))"
Remove-Item Env:CAMPAIGN_AGENT
```

Repeat the probe with `source_evaluator`, `fact_checker`, `synthesizer`, and `critic` before their own provenance files. The required effective values are: `target_model=deepseek-v4-flash`; target effort `high` for Researcher and Source Evaluator and `max` for Fact Checker, Synthesizer, and Critic; `judge_model=deepseek-v4-flash`; `judge_effort=max`; `llm.max_tokens=4096`; `agents.planner_final_max_tokens=4096`; controlled repetitions `3`; repetition floor `0.65`; case-average threshold `0.80`; maximum concurrency `1`; dataset version `1`; and rubric version `1`.

The retry ruling is exact: `retry_count=5`, `retry_initial_delay=1.0`, and `retry_max_delay=16.0`, producing the repository-owned 1/2/4/8/16-second backoff for retryable failures. If the repository `.env` changes any of these values, set only the non-secret process overrides below before the next provenance helper and record the ruling in the ledger; never edit `.env` in this campaign:

```powershell
$env:LLM_RETRY_COUNT = '5'
$env:LLM_RETRY_INITIAL_DELAY = '1.0'
$env:LLM_RETRY_MAX_DELAY = '16.0'
$env:AGENTS_PLANNER_FINAL_MAX_TOKENS = '4096'
```

If model, target/judge effort, global max tokens, repetition, threshold, concurrency, dataset, or rubric values differ after the process overrides, stop with `INFRASTRUCTURE_BLOCKED` for the affected agent and do not spend a baseline call. A configuration mismatch is not a quality failure and is not repaired by a prompt edit.

- [ ] **Step 4: Write one immutable baseline-provenance record per agent**

Run the provenance helper once per literal agent, before that agent's first baseline command. Do not reuse one agent's file for another agent and do not overwrite a file that already exists:

```powershell
python $PacketRoot\record_eval_provenance.py --config config.yaml --agent researcher --output $PacketRoot\agents\researcher\baseline-provenance.json
python $PacketRoot\record_eval_provenance.py --config config.yaml --agent source_evaluator --output $PacketRoot\agents\source-evaluator\baseline-provenance.json
python $PacketRoot\record_eval_provenance.py --config config.yaml --agent fact_checker --output $PacketRoot\agents\fact-checker\baseline-provenance.json
python $PacketRoot\record_eval_provenance.py --config config.yaml --agent synthesizer --output $PacketRoot\agents\synthesizer\baseline-provenance.json
python $PacketRoot\record_eval_provenance.py --config config.yaml --agent critic --output $PacketRoot\agents\critic\baseline-provenance.json
```

Expected: five files, each naming the same clean Task 8 candidate SHA and its own effective target effort; each file contains only safe non-secret provenance; and each file's SHA-256 is recorded in the ignored ledger. If a file exists, inspect its safe fields and reuse it only when its candidate SHA, effective configuration, and file hash match the current gate; otherwise stop and create no replacement until the discrepancy is ruled on.

- [ ] **Step 5: Obtain immediate human authorization before each baseline command**

The controller must ask immediately before each command: “Authorize this paid controlled command for the named agent at the recorded candidate SHA? It runs the three frozen controlled cases with three repetitions each against `deepseek-v4-flash`, the recorded target effort, judge effort `max`, global `4096` tokens, the configured retry policy, and LangSmith controlled tracing; it does not use `--tier live`.” The controller must state the literal command and expected artifact path. One authorization covers one command only; do not pre-authorize the five-command sequence or reuse authorization for a confirmation, focused retest, or full validation.

- [ ] **Step 6: Run each immutable full controlled baseline separately**

Run exactly one command after the matching authorization. These commands intentionally omit `--case`, so each requests all three frozen cases and the harness's configured three repetitions:

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent researcher --tier controlled --config config.yaml --reasoning-effort high --judge-reasoning-effort max --experiment-prefix cross-agent-planner-fix-parity-baseline-researcher --verbose
$ResearcherBaselineExit = $LASTEXITCODE
if ($ResearcherBaselineExit -notin 0, 1, 2, 3) { throw "Unexpected Researcher exit code: $ResearcherBaselineExit" }
```

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent source-evaluator --tier controlled --config config.yaml --reasoning-effort high --judge-reasoning-effort max --experiment-prefix cross-agent-planner-fix-parity-baseline-source-evaluator --verbose
$SourceEvaluatorBaselineExit = $LASTEXITCODE
if ($SourceEvaluatorBaselineExit -notin 0, 1, 2, 3) { throw "Unexpected Source Evaluator exit code: $SourceEvaluatorBaselineExit" }
```

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent fact-checker --tier controlled --config config.yaml --reasoning-effort max --judge-reasoning-effort max --experiment-prefix cross-agent-planner-fix-parity-baseline-fact-checker --verbose
$FactCheckerBaselineExit = $LASTEXITCODE
if ($FactCheckerBaselineExit -notin 0, 1, 2, 3) { throw "Unexpected Fact Checker exit code: $FactCheckerBaselineExit" }
```

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent synthesizer --tier controlled --config config.yaml --reasoning-effort max --judge-reasoning-effort max --experiment-prefix cross-agent-planner-fix-parity-baseline-synthesizer --verbose
$SynthesizerBaselineExit = $LASTEXITCODE
if ($SynthesizerBaselineExit -notin 0, 1, 2, 3) { throw "Unexpected Synthesizer exit code: $SynthesizerBaselineExit" }
```

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent critic --tier controlled --config config.yaml --reasoning-effort max --judge-reasoning-effort max --experiment-prefix cross-agent-planner-fix-parity-baseline-critic --verbose
$CriticBaselineExit = $LASTEXITCODE
if ($CriticBaselineExit -notin 0, 1, 2, 3) { throw "Unexpected Critic exit code: $CriticBaselineExit" }
```

Do not run `python -m deep_research.evaluation suite`, do not add `--tier live`, and do not edit source/config/evaluation inputs when a command returns `1`, `2`, or `3`. Exit `2` is a command/case correction with no evaluation result; exit `3` is an infrastructure/preflight result; exit `1` is completed-but-not-passing and requires typed diagnosis.

- [ ] **Step 7: Resolve, hash, and strictly validate each baseline artifact**

Immediately before each paid command, capture the existing matching result paths in a variable named for that literal agent. The resolver must compare its post-command result list with that pre-command list; never select an older artifact. For Researcher, use this exact pattern and repeat it with the literal values for the other four agents and prefixes:

```powershell
$BeforeResearcherResults = @(
    Get-ChildItem -LiteralPath 'output\evaluations\researcher' -Filter 'results.json' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)
$ResearcherResults = @(
    Get-ChildItem -LiteralPath 'output\evaluations\researcher' -Filter 'results.json' -Recurse -ErrorAction Stop |
        Where-Object {
            $_.Directory.Name -like 'cross-agent-planner-fix-parity-baseline-researcher-researcher-controlled-*' -and
            $_.FullName -notin $BeforeResearcherResults
        } |
        Sort-Object LastWriteTimeUtc -Descending
)
if ($ResearcherResults.Count -ne 1) { throw 'Researcher baseline did not produce exactly one new results.json' }
$ResearcherResultsPath = $ResearcherResults[0].FullName
$env:RESULTS_PATH = $ResearcherResultsPath
$env:EXPECTED_AGENT = 'researcher'
$env:EXPECTED_SHA = $CandidateSha
python -c "import json, os; from pathlib import Path; from deep_research.evaluation.models import ExperimentResult; r=ExperimentResult.model_validate_json(Path(os.environ['RESULTS_PATH']).read_text(encoding='utf-8')); expected={'multi-source-coverage','conflicting-evidence','partial-search-failure'}; assert r.agent_name == os.environ['EXPECTED_AGENT']; assert r.tier == 'controlled'; assert {c.case_id for c in r.cases} == expected; assert all(len(c.repetitions) == 3 for c in r.cases); assert r.metadata.get('git_commit') == os.environ['EXPECTED_SHA']; print(json.dumps({'status':r.status,'experiment_name':r.experiment_name,'cases':len(r.cases),'repetitions':sum(len(c.repetitions) for c in r.cases),'configuration_fingerprint':r.metadata.get('configuration_fingerprint')}, sort_keys=True))"
Remove-Item Env:RESULTS_PATH
Remove-Item Env:EXPECTED_AGENT
Remove-Item Env:EXPECTED_SHA
Get-FileHash -Algorithm SHA256 -LiteralPath $ResearcherResultsPath
```

The Source Evaluator, Fact Checker, Synthesizer, and Critic validations use the same command with these exact case sets and roots: `strong-and-weak-sources|corroboration-recency-reputation|reputation-provider-failure` under `output/evaluations/source-evaluator`; `mixed-verdicts|independent-domain-evidence|verification-search-failure` under `output/evaluations/fact-checker`; `complete-cited-report|conflict-and-limitations|write-or-memory-failure` under `output/evaluations/synthesizer`; and `approve-strong-report|request-more-research|missing-evidence-or-budget-exhausted` under `output/evaluations/critic`. The validator must print only status, experiment name, counts, and fingerprints. A valid `FAILED` artifact with all 9 repetitions remains immutable evidence; it is not overwritten or relabeled.

- [ ] **Step 8: Write one immutable bounded inventory per valid baseline**

Invoke `record_eval_inventory.py` with the resolved literal path, matching provenance file, and matching output path:

```powershell
python $PacketRoot\record_eval_inventory.py --agent researcher --results $ResearcherResultsPath --provenance $PacketRoot\agents\researcher\baseline-provenance.json --output $PacketRoot\agents\researcher\baseline-inventory.json
```

Repeat for the other four agents with their own resolved path and packet root. The helper must reject a result whose candidate SHA, agent, tier, case set, repetition count, configuration fingerprint, or prompt/judge fingerprint disagrees with provenance. Preserve direct experiment, dataset, target-trace, evaluator-trace, and evaluator-source URLs exactly when the harness supplies them; retain `null` when the integration supplies none. Never derive a URL from a prompt, case input, exception, environment value, or another trace.

For every repetition, extract the complete typed row before diagnosing: all gate IDs/details, deterministic metrics, judge status and all common/agent-specific dimensions, unrounded aggregate, target failure stage/reason/details kind, fallback provider kind/operation, judge not-run reason/diagnostics, ReAct stop reason, prohibited-call count, latency/token counts when already typed, and direct trace URLs. Do not copy a provider exception message, response fragment, prompt, evaluator input, hidden reasoning, or raw trajectory into the inventory.

- [ ] **Step 9: Separate provider/infrastructure, harness, and quality/trajectory evidence**

Apply this precedence to every failed or suspicious row; record the selected class and the evidence that ruled out the other classes in the ledger before writing a source amendment:

| Evidence | Required class and action |
| --- | --- |
| `TargetOutput.failure.stage` is `provider`, `trace`, `artifact`, or `setup`; a safe `details.kind` is `output_limit`, `schema_output`, `provider_timeout`, `provider_rate_limit`, `provider_transport`, `provider_http`, `provider_response`, or `provider_failure`; a fallback error carries a safe `provider_failure.kind`; or a judge is `judge_not_run` with a typed provider/transport/output-limit reason | Provider/infrastructure. Preserve the artifact, identify the exact target/judge operation if visible, perform the one same-SHA confirmation in Step 11, and do not edit prompts or add a budget field. A `judge_output_limit` is a judge-side infrastructure finding, not a target operation budget candidate. |
| Offline evidence proves that a frozen case, deterministic evaluator, gate, judge adapter, artifact projection, or status calculation contradicts its declared contract | Harness/evaluator defect. Do not tune the agent. Record the offline reproduction, affected artifact paths, and `HARNESS_DEFECT_BLOCKED`; request a separate approved harness plan and a new baseline. |
| The target completed with no unexplained typed provider/infrastructure failure, the required traces and judges are valid, and a hard gate, deterministic metric, visible trajectory, state update, or judge dimension shows an agent behavior defect | Deterministic quality/trajectory. Create one agent+operation root-cause record and route to Task 11 only if the exact target operation is a typed output-limit; otherwise route to Task 12. |
| A known failure case records its expected scripted search/reputation/write/memory/budget recovery and passes its frozen recovery gate | Expected case behavior. Keep it in evidence, do not classify it as an LLM provider failure, and do not create a repair solely because the safe recovery error exists. |

`provider_failure.kind` is never inferred from `str(error)`, a judge score, a timeout-looking duration, or a completed HTTP request. If the operation or typed class cannot be established from safe evidence, record `diagnostic_visibility: unavailable`, do not add a token budget, and route the agent to the infrastructure confirmation/blocked path.

- [ ] **Step 10: Create separate operation-scoped diagnoses and route each agent**

For every observed failure, create a new file under the exact agent root, for example `agents/researcher/root-causes/researcher-react-decision-001.md` or `agents/source-evaluator/root-causes/source-evaluator-scoring-001.md`. The stable ID is formed from the literal CLI name, the operation in kebab case, and a three-digit sequence, such as `researcher-react-decision-001`; record the exact snake-case operation separately. Use these operation spellings only: `react_decision`, `researcher_finding_extraction`, `source_evaluator_scoring`, `fact_checker_claim_extraction`, `fact_checker_claim_verification`, `synthesizer_report_draft`, and `critic_report_review`. A single agent may therefore have multiple independent records; never combine extraction and verification, ReAct and final review, or two agents under one ID.

Each root-cause file must contain literal values for: agent/internal name, CLI name, exact operation, case ID, repetition numbers, immutable baseline inventory path and SHA-256, direct trace/experiment URLs, typed class and safe details, failed gates/metrics/judge dimensions, visible trajectory/state evidence, at least one counterexample or counterevidence item, falsifiable hypothesis, why transport/schema/harness alternatives are ruled out, smallest permitted repair surface, predicted target and non-target invariants, current focused-attempt count, rollback commit, reviewer disposition, and next action. A passing agent receives an explicit `baseline-pass-no-repair.md` record with `diagnosis: no repair required` and an empty root-cause-ID list; do not manufacture a defect ID for a passing baseline.

Route each agent exactly once after its baseline inventory:

```text
REVIEW REQUIRED + all nine rows valid + no unexplained target/judge/provider issue -> write no-repair record; Task 13 may reuse the immutable baseline.
Typed target-side output_limit + exact eligible non-ReAct operation -> Task 11.
Typed target-side schema/provider/transport/HTTP failure, typed fallback issue, or deterministic quality/trajectory failure -> Task 12 after diagnosis.
Persistent provider/trace/artifact/setup failure after the one confirmation -> terminal_state INFRASTRUCTURE_BLOCKED; stop that agent.
Offline-proven harness/evaluator defect -> terminal_state HARNESS_DEFECT_BLOCKED; stop that agent and request a separate plan.
Three unsuccessful focused repairs for one unchanged root-cause ID -> terminal_state ESCALATED; write escalation.md and stop that root cause.
```

- [x] **Step 11: Make the one allowed same-SHA infrastructure confirmation**

If a baseline command exits `3`, produces an incomplete artifact, lacks required trace/artifact evidence, or contains an unclassified/persistent provider or judge failure that prevents a valid quality verdict, write the matching `confirmation-provenance.json` before a new command. Obtain a new immediate human authorization, then rerun the exact same agent command with the exact same candidate SHA, case set, model/effort, effective retry values, global `4096`, and configuration fingerprints, changing only the experiment prefix to `cross-agent-planner-fix-parity-confirmation-$CliAgent`, where `$CliAgent` is set to one literal value from the five-agent execution map.

If the same typed infrastructure/trace/artifact failure persists, preserve both immutable results/provenance records, write `terminal-state.md` with `terminal_state: INFRASTRUCTURE_BLOCKED`, `harness_status: INFRASTRUCTURE FAILURE` when that is the harness status, the actual exit code, and `repair_attempts: 0`, and stop that agent. If the confirmation produces a valid 3×3 artifact, write `confirmation-inventory.json`, keep the first attempt immutable, and use the valid evidence for diagnosis; the confirmation is not a repair attempt. A transient provider failure that disappears is recorded as provider evidence, not silently erased.

- [x] **Step 12: Close Task 10 without editing source or running a suite**

Task 10 closed at candidate `d6a082c` after all five agents produced valid baseline and confirmation inventories. The confirmation results preserved typed judge/provider failures and no Windows path preflight failure. Each agent's terminal record is `INFRASTRUCTURE_BLOCKED` with `repair_attempts: 0`; no target-side output-limit evidence exists, so Tasks 11 and 12 remain not applicable and no paid suite/live run is authorized by this plan.

Before advancing, verify that every agent has a baseline provenance file, a valid baseline or explicit infrastructure confirmation record, a safe inventory or a documented artifact-unavailable stop, a no-repair or operation-scoped diagnosis, and a ledger entry naming the next task. Run:

```powershell
git diff --name-only
git status --short
if (git diff --name-only | Where-Object { $_ -notlike '.superpowers/*' -and $_ -notlike 'output/*' }) { throw 'Task 10 changed tracked source/test/config/evaluation files' }
```

Expected: no tracked changes beyond the already reviewed Task 9 candidate; no `suite` command has run; and no agent is silently omitted. Do not begin Task 11 or Task 12 until the required Task 10 baseline decision exists for that agent.

---

### Task 11: Evidence-Gated Operation-Specific Output-Budget Repair — NOT STARTED / NOT APPLICABLE

**Files:** conditional; modify only for an agent/operation that produced a typed target-side `output_limit`
- `src/deep_research/utils/config.py`
- `config.yaml`
- the exact agent file containing the failing structured request
- `tests/test_config.py`
- `tests/test_evaluation/test_config.py`
- the exact agent test module
- provider tests only if the existing `max_tokens` override contract itself is broken (not expected)

**Interfaces:**
- Consumes: one Task 10 artifact proving target-side `output_limit`, including exact operation name and configured cap 4096.
- Produces: a single agent-operation-specific budget field; only that request passes it to `complete_structured(max_tokens=...)`; ReAct and judge calls remain `None`/global 4096.

Candidate field names are fixed by operation:

```text
researcher_finding_extraction -> agents.researcher_extraction_max_tokens / AGENTS_RESEARCHER_EXTRACTION_MAX_TOKENS
source_evaluator_scoring -> agents.source_evaluator_scoring_max_tokens / AGENTS_SOURCE_EVALUATOR_SCORING_MAX_TOKENS
fact_checker_claim_extraction -> agents.fact_checker_extraction_max_tokens / AGENTS_FACT_CHECKER_EXTRACTION_MAX_TOKENS
fact_checker_claim_verification -> agents.fact_checker_verdict_max_tokens / AGENTS_FACT_CHECKER_VERDICT_MAX_TOKENS
synthesizer_report_draft -> agents.synthesizer_report_max_tokens / AGENTS_SYNTHESIZER_REPORT_MAX_TOKENS
critic_report_review -> agents.critic_review_max_tokens / AGENTS_CRITIC_REVIEW_MAX_TOKENS
```

**There is intentionally no ReAct-decision budget field in this campaign.** If a ReAct decision itself reaches 4096, treat it as a separate trajectory/model-behavior investigation; the Planner campaign deliberately kept ReAct at the global cap.

- [ ] **Step 1: Write the literal repair amendment into the SDD ledger before editing code**

The amendment must include:

```text
Root-cause ID
agent + exact operation
baseline artifact path and repetition(s)
typed provider_failure.kind / configured cap / attempt(s)
why the failure is output truncation rather than transport/schema/quality
exact config field/env name from the mapping above
candidate focused override: 8192
files allowed to change
literal RED test name/code
focused pytest command
paid focused controlled command
rollback condition
```

No source edit before this record exists.

- [ ] **Step 2: Add RED config and call-budget tests**

Follow the Planner Task 3 pattern: the fake provider's `budgets` list must prove all neighboring operations stay `None` and exactly the affected operation receives the configured value. Assert the effective configuration fingerprint changes when and only when the new field changes.

- [ ] **Step 3: Implement the one field and one call-site override**

Default the new field to `4096`, `ge=1`, include the exact environment mapping above, add `config.yaml` value 4096, and pass `self.config.<field>` only to the affected structured request.

Do not modify `llm.max_tokens` or any judge/ReAct call.

- [ ] **Step 4: Offline GREEN**

Run the affected agent tests, config tests, provider max-token tests, evaluation fingerprint tests, then the full offline suite and Ruff.

- [ ] **Step 5: Fresh review before any paid retest**

Write `agents/<cli-agent>/reviews/budget-attempt-<attempt>.md` with a fresh reviewer disposition. The reviewer must confirm the exact operation field, default `4096`, process override `8192`, unchanged global/ReAct/judge `4096`, unchanged retry policy, unchanged frozen inputs, and the RED/GREEN evidence. `needs-change` blocks the paid command; `approved` is required. Allow at most five review/fix loops for the campaign, then stop and record the unresolved finding.

- [ ] **Step 7: Human-confirmed focused three-repetition budget test**

Use the literal row for the diagnosed agent and operation. Set `$CliAgent`, `$CaseId`, `$ReasoningEffort`, `$BudgetEnv`, `$RootCauseId`, and `$Attempt` to the recorded literal values; set only `$BudgetEnv` to `8192`; run the safe config probe; and obtain immediate authorization for this one command:

```powershell
[Environment]::SetEnvironmentVariable($BudgetEnv, '8192', 'Process')
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent $CliAgent --tier controlled --config config.yaml --case $CaseId --reasoning-effort $ReasoningEffort --judge-reasoning-effort max --experiment-prefix ("cross-agent-planner-fix-parity-focused-{0}-{1}-attempt-{2}" -f $CliAgent, $RootCauseId, $Attempt) --verbose
$FocusedExit = $LASTEXITCODE
[Environment]::SetEnvironmentVariable($BudgetEnv, $null, 'Process')
if ($FocusedExit -notin 0, 1, 2, 3) { throw "Unexpected focused exit code: $FocusedExit" }
```

There is no ReAct budget variable. Resolve exactly one new `results.json` with the pre-command artifact list and inventory it under the exact packet root. A focused pass requires three repetitions, all hard gates, every aggregate at least `0.65`, case average at least `0.80`, scored judges, no target-side output-limit, no prohibited call, and no new non-target failure. If output-limit persists at `8192`, create a new diagnosis, remove unsupported changes, and do not try `16384` automatically.

- [ ] **Step 8: Commit only evidence-supported budget repair**

After the focused inventory passes and the fresh review is approved, commit only the exact config field, call site, tests, and ledger reference with `fix(<literal-cli-agent>): isolate <literal-operation> output budget`; record SHA and artifact paths. Three unsuccessful focused attempts for the same root-cause ID produce `escalation.md` and `terminal_state: ESCALATED`; a provider/trace failure uses the one confirmation and `INFRASTRUCTURE_BLOCKED`, not a repair attempt. Never modify ReAct, judge, global `llm.max_tokens`, or an unrelated agent.

---

### Task 12: Evidence-Gated Agent-Specific Quality / Trajectory Repair Loop — IN PROGRESS

**Files:** conditional per diagnosed root cause; never edit frozen evaluation inputs, evaluators, judges, or the SDD ledger's source-of-truth definitions.

**Allowed agent-specific surfaces:** Researcher `src/deep_research/agents/researcher.py`, its prompt in `src/deep_research/agents/prompts.py`, and `tests/test_agents/test_researcher.py`; Source Evaluator uses `source_evaluator.py` and `test_source_evaluator.py`; Fact Checker uses `fact_checker.py` and `test_fact_checker.py`; Synthesizer uses `synthesizer.py` and `test_synthesizer.py`; Critic uses `critic.py` and `test_critic.py`. A shared runtime/prompt change invalidates every affected agent's evidence and requires new baselines. No Planner tuning is allowed.

**Interfaces:** consumes one typed Task 10/11 diagnosis; produces one minimal agent-specific repair, offline RED/GREEN evidence, fresh review approval, and one focused three-repetition result. Permitted quality categories are `agent_prompt`, `agent_local_validation`, `agent_tool_policy`, and `agent_fallback_state`. Provider/infrastructure, schema-output, harness, and output-limit findings remain on their typed routes.

- [ ] **Step 1: Record the operation-scoped amendment before editing**

Write `agents/$CliAgent/repairs/$RootCauseId/attempt-$Attempt-amendment.md` with literal agent/CLI/operation, cases and repetitions, immutable inventory and trace paths, typed class, falsifiable hypothesis, counterevidence, ruled-out alternatives, exact allowed files, RED test node, smallest change, target/non-target invariants, focused command, rollback, and expected status. Never combine ReAct with extraction/verification/review or two agents.

- [ ] **Step 2: TDD one minimal repair**

Set `$TestNode` to the literal mapped agent test file and run `python -m pytest -q $TestNode -p no:cacheprovider`; the root-cause test must fail for the recorded defect. Make the smallest prompt, behavior, or state change for that operation only. Preserve: Researcher prior findings/no invented URLs; Source Evaluator one row/source, bounded fallback, and low-confidence semantics; Fact Checker prior claims, independent domains, and `insufficient_evidence`/`unverified`; Synthesizer evidence-only assembly, truthful persistence, and known citations; Critic bounded scores, actionable gaps, routing, and macro limits.

- [ ] **Step 3: Run offline GREEN and isolation checks**

Run `python -m pytest -q $TestNode -p no:cacheprovider`, the complete literal agent test module, `python -m pytest -q -p no:cacheprovider`, `python -m ruff check src tests`, and `git diff --check`. Confirm no frozen input, evaluator, judge, retry, global budget, Planner path, or unrelated agent changed. If RED is not reproduced or GREEN changes a non-target invariant, revert the candidate and return to diagnosis.

- [ ] **Step 4: Fresh review and one authorized focused retest**

Write `agents/$CliAgent/reviews/attempt-$Attempt.md`; a fresh reviewer must resolve Critical/Important findings and approve the exact diff before any paid command. Obtain immediate authorization for one command, set `$CliAgent`, `$CaseId`, `$ReasoningEffort`, `$RootCauseId`, and `$Attempt` to their literal recorded values, then run:

```powershell
python $RunWithEnv $EnvSource python -m deep_research.evaluation agent $CliAgent --tier controlled --config config.yaml --case $CaseId --reasoning-effort $ReasoningEffort --judge-reasoning-effort max --experiment-prefix ("cross-agent-planner-fix-parity-focused-{0}-{1}-attempt-{2}" -f $CliAgent, $RootCauseId, $Attempt) --verbose
```

Resolve one new result path and inventory every gate, deterministic metric, judge dimension/status, trajectory stop reason, typed provider/fallback kind and operation, prohibited-call count, and safe URL. The focused gate requires three repetitions, all hard gates, aggregate floor `0.65`, case average `0.80`, scored judges, no infrastructure/harness failure, target improvement, and no non-target regression.

- [ ] **Step 5: Enforce routing and the attempt limit**

Typed provider/trace/artifact/setup failure gets the one same-SHA confirmation and then `INFRASTRUCTURE_BLOCKED`; an offline harness contradiction gets `HARNESS_DEFECT_BLOCKED`; a target-side `output_limit` gets Task 11; a failed quality hypothesis gets a new root-cause ID. After three unsuccessful focused attempts for one unchanged ID, write `escalation.md`, set `terminal_state: ESCALATED`, and stop. Infrastructure and harness blocks do not consume repair attempts; unrelated causes are never bundled.

- [ ] **Step 6: Commit only a passing cohesive repair**

After focused approval, commit only the agent-specific files with `fix($CliAgent): repair $Operation behavior`, record SHA and evidence, and route to Task 13. A failed or deferred attempt remains immutable evidence and is not called repaired.

---

### Task 13: Full Controlled Validation for Every Repaired Agent — NOT STARTED

**Files:**
- No tracked changes during validation
- Update ignored ledger and ignored artifacts

**Interfaces:**
- Consumes: the final candidate commit for one agent after Tasks 11/12.
- Produces: one authoritative nine-repetition full controlled artifact at that commit.

- [ ] **Step 1: Re-run the full offline suite and verify clean Git state**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
if (git status --porcelain) { throw 'tracked worktree must be clean before controlled validation' }
```

- [ ] **Step 2: Obtain immediate human confirmation for this agent's full controlled run**

State exact candidate SHA, agent, command, model/reasoning configuration, effective non-secret budgets, and cost/network scope.

- [ ] **Step 3: Run the full controlled dataset**

```powershell
python -m deep_research.evaluation agent <agent> `
  --config config.yaml `
  --experiment-prefix cross-agent-planner-fix-parity-final `
  --verbose
```

- [ ] **Step 4: Validate terminal state**

A repaired agent is green only when:

```text
all expected repetitions completed
all hard gates passed
case averages/floors meet the frozen contract
all expected judges scored, or any judge failure is separately typed and makes the quality verdict explicitly non-promotable
no target-side output_limit/schema/provider failure remains unexplained
no fallback provider_failure snapshot is silently present in a supposedly clean case
no prohibited-call regression appeared
```

If a typed provider infrastructure failure prevents a valid quality verdict after the approved retry policy, record `INFRASTRUCTURE_BLOCKED` for that run; do not disguise it as an agent-quality failure.

- [ ] **Step 5: Record unchanged agents too**

If an agent passed its Task 10 baseline and required no repair, its immutable baseline artifact is its authoritative validation; do not spend money rerunning it merely for symmetry unless later shared code changes touched its execution path. If later shared code did touch it, rerun only after immediate human confirmation.

---

### Task 14: Create the Permanent Cross-Agent Fix Log — COMPLETE

**Files:**
- Create: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Consumes: SDD ledger, git history, reviewed diffs, and validated controlled artifacts.
- Produces: a tracked, secret-safe permanent record analogous to the Planner fix log.

- [x] **Step 1: Write the log with these exact sections**

```markdown
# Cross-Agent Planner-Fix Parity Fix Log (2026-09-08)

## 1. Campaign Context and Frozen Base
## 2. Planner Lessons Reviewed
## 3. Transferability Matrix
## 4. Shared RC-A ReAct Boundary Fix
## 5. Planner Local-Wrapper Removal / Regression Evidence
## 6. Controlled Baseline and Confirmation Findings
## 7. Agent-Specific Repair Decision
## 11. Safe Provider Diagnostic Projection
## 12. Operation-Specific Budget Decisions
## 13. Controlled Evaluation Evidence
## 14. Environment / Retry / Worktree Rulings
## 15. Review Findings and Resolutions
## 16. Deferred Non-Load-Bearing Findings
## 17. Final Verification and Terminal State
```

For an agent that needed no change, say so and cite the exact controlled artifact/gates that justified no change. Do not manufacture a repair section.

- [x] **Step 2: Secret/data-leakage scan**

The fix log may contain commit SHAs, case IDs, gate IDs, finite typed reasons, non-secret config values/fingerprints, counts, scores, experiment URLs directly supplied by LangSmith, and static error messages. It must not contain prompts, provider responses, evaluator inputs, secrets, hidden reasoning, raw exception strings, or raw environment dumps.

- [x] **Step 3: Commit**

```powershell
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record cross-agent planner-fix parity evidence"
```

Record `Task 14: complete` only after the log is updated with the final controlled evidence.

---

### Task 15: Final Offline Verification Complete; Whole-Branch Review Deferred

**Files:**
- No new production scope
- May modify only files already changed by this plan to resolve final review findings

**Interfaces:**
- Produces: review-clean branch and final execution handoff; no merge/push.

- [x] **Step 1: Run authoritative full verification**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
git status --short
git log --oneline --decorate --max-count=30
```

Expected: full suite green; Ruff/whitespace green; clean tracked worktree.

- [x] **Step 2: Run reserved-data leakage checks over changed tracked files**

Inspect the diff from `$ApprovedBase` and verify no added production/logging path contains:

```text
str(error)
repr(error)
raw provider response/reasoning content
prompt/request payload serialization
raw finish_reason values
secret/environment dumps
unbounded validation input values
```

Legitimate test assertions may mention these strings only to prove they are absent.

Result: the added tracked documentation contains only bounded typed failure names, counts, hashes, repository-relative artifact paths, and explicit statements that raw provider content and secrets are excluded. No raw exception, prompt, evaluator input, environment dump, or secret value was added.

- [ ] **Step 3: Dispatch the final whole-branch reviewer**

Use the most capable available model. Supply:
- this plan path;
- the Planner fix log and provider-remediation design paths;
- base SHA and HEAD SHA;
- the complete diff/review package;
- permanent cross-agent fix log;
- no conversation history.

Required review focus:

```text
correctness and regression risk
shared-vs-local responsibility boundaries
Planner behavior preservation
agent fallback semantics
provider diagnostic safety/bounds
operation budget isolation
controlled-evaluation integrity
secret/provider-content leakage
missing tests for interfaces changed across tasks
```

- [ ] **Step 4: Resolve final findings once**

For any Critical/Important finding, dispatch one fix task with TDD and then one scoped re-review. Adjudicate remaining Minor findings into the permanent fix log rather than expanding scope indefinitely.

- [ ] **Step 5: Re-run full verification after any final fix**

Run the same full pytest/Ruff/whitespace commands from Step 1 and record final HEAD SHA and counts in the ledger/fix log.

- [ ] **Step 6: Stop before integration side effects**

Do not merge, push, open a PR, deploy, or begin live evaluation as part of this plan. Hand the clean reviewed branch to `superpowers:finishing-a-development-branch` (or the user's chosen integration workflow) as a separate action.

---

## Subagent-Driven Development Execution Contract

At execution time, use the current `superpowers:subagent-driven-development` process rather than giving one agent this entire plan as a monolithic prompt:

1. Initialize/verify the plan-specific SDD workspace and recovery ledger.
2. Before Task 1, run the plan preflight consistency scan: for every pair of tasks sharing a file/interface, record producer/consumer compatibility in the ledger and rule on conflicts before dispatch.
3. Extract each task into a task brief. Give the implementer the brief path as its exact requirements, plus only prior interfaces/rulings it needs.
4. Use a fresh implementer for each judgment-bearing task; batch only same-shape mechanical tests.
5. Implementer runs tests, commits, self-reviews, writes its report artifact, and returns only status/commit/test summary/concerns.
6. Generate a review package and dispatch a fresh task reviewer for spec compliance and code quality.
7. Fix/re-review findings before marking the task complete. Escalate stuck fix loops per the current SDD skill rather than repeatedly using the same failing context.
8. Continue through the plan without pausing between offline tasks. Stop only at the explicit paid-provider confirmation gates, security/destructive/external-side-effect gates, or if the plan is so inconsistent that every path is guesswork.
9. After the final task, run one broad whole-branch review and then the branch-finishing workflow.

## Recommended Subagent Model Routing

Use explicit model selection for every remaining implementation or review dispatch. The user's required routing for this campaign is authoritative:

| Work | Model | Reasoning |
| --- | --- | --- |
| Task 9 implementation and review-finding fixes | GPT-5.6 Luna | **high** |
| Task 9 task-scoped review and scoped re-review | GPT-5.6 Luna | **max** |
| Task 11 budget implementation/fixes | GPT-5.6 Luna | **high** |
| Task 11 task-scoped review | GPT-5.6 Luna | **max** |
| Task 12 agent implementation/fixes | GPT-5.6 Luna | **high** |
| Task 12 task-scoped review | GPT-5.6 Luna | **max** |
| Task 13 implementation fixes | GPT-5.6 Luna | **high** |
| Task 13 task-scoped review | GPT-5.6 Luna | **max** |
| Task 14 documentation/fix-log updates | GPT-5.6 Luna | **high** unless purely mechanical |
| Task 14 task-scoped review | GPT-5.6 Luna | **max** |
| Task 15 whole-branch review | **Do not dispatch** until the user explicitly requests it | deferred |

Task 10 and Task 13 controlled target/judge runs must use the frozen evaluation model configuration. Implementation-model routing is not permission to change target model, judge model, reasoning effort, retry policy, or token budgets. High/max workers may require longer bounded controller waits; do not interpret slow reasoning as a repository-helper loop or launch duplicate workers.

The original task-specific routing notes remain useful for completed tasks and are retained below:

| Work | Suggested capability |
| --- | --- |
| Task 2 shared ReAct two-line implementation after tests are written | cheap/fast implementation model |
| Task 3 multi-agent parity + Planner wrapper removal | standard coding model |
| Task 4 provider diagnostic contract design/implementation | strong standard or high reasoning model |
| Task 5 repetitive agent wiring after Task 4 contract is fixed | standard model; batch same-shape edits if reviewer surface remains coherent |
| Task 6-7 characterization tests | cheap-to-standard model |
| Controlled artifact diagnosis / root-cause amendment | most capable reasoning model |
| Agent-specific prompt/trajectory repair | standard/high depending on evidence complexity |
| Task reviews | at least standard; high for provider contracts/evaluation semantics |
| Final whole-branch review | most capable available model |

## Explicit Non-Goals

- Task 9 is network-zero: do not run `python -m deep_research.evaluation ...`, instantiate real providers, call LangSmith, sync datasets, or supply credentials.
- Task 9 may change only the bounded artifact contract in the listed evaluation files and its offline tests; it may not change cases, rubrics, judges, prompts, gates, thresholds, dependency scenarios, retry behavior, model budgets, provider wrappers, or agent behavior.
- Preserve `ARTIFACT_SCHEMA_VERSION`, global `llm.max_tokens=4096`, and the existing Planner-specific final-output budget.
- Never persist raw provider/evaluator content, exception text, prompts, credentials, request payloads, hidden reasoning, or arbitrary diagnostic dictionaries. If typed visibility is insufficient, fail closed.
- DeepSeek wrapper/bridge repair is deferred and has no dependency or allowed change in Task 9.
- No live-tier evaluation.
- No end-to-end graph quality campaign.
- No change to Planner quality prompts or Planner scoring.
- No global increase above `llm.max_tokens=4096`.
- No blanket creation of token-budget settings for agents that never demonstrated output truncation.
- No judge-budget change; judge-side output-limit remediation is a separate scope from the target-agent audit.
- No replacement of the existing retry policy.
- No evaluation-rubric/case/gate adjustment to improve scores.
- No unrelated refactor of large agent modules.
- No provider switch or reasoning-effort tuning unless a later separately approved evidence-backed amendment explicitly calls for it.

## Plan Self-Review Checklist

Before execution, the controller must confirm:

- Every confirmed transferable Planner issue maps to a task or an explicit no-change rationale.
- New Task 9 appears immediately after completed Task 8 and blocks Task 10.
- No path permits Task 8 to transition directly to a controlled baseline.
- Task 9 preserves bounded deterministic metric details, exact prohibited-call count, finite ReAct stop reason, and nullable `{kind, operation}` fallback projection.
- Task 9 preserves the existing weighted scalar `deterministic_quality` and does not change metric definitions, weights, thresholds, gates, or artifact schema version.
- Task 9 has focused RED/GREEN, full offline evaluation, full offline suite, inventory accept/reject proofs, and one Luna-max approval before Task 10.
- RC-A is fixed at the shared boundary rather than copied across agents.
- The historical fix-log statement about Source Evaluator/Synthesizer has been reconciled with current no-ReAct architecture.
- Planner-local wrapper removal happens only after shared RED/GREEN proof.
- Provider fallback diagnostics retain finite typed information but not provider/error text.
- Partial/fallback semantics are explicitly tested for all five non-Planner agents.
- Token-budget changes are conditional on typed output-limit evidence and isolated to one operation.
- ReAct and judge budgets remain global 4096.
- Paid commands are behind immediate human confirmation gates.
- Frozen evaluation inputs are clearly listed.
- No task requires changing a case/rubric/gate to pass.
- Every tracked task ends with a test/review/commit boundary appropriate for a fresh subagent.
- The permanent fix log is part of completion, so future agents do not have to reconstruct this campaign from chat history.
- Final Task 15 offline verification is rerun after Task 9 and any later tracked repair; whole-branch review remains paused until explicit user request.
