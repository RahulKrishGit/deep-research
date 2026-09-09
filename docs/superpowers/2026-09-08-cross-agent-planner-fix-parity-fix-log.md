# Cross-Agent Planner-Fix Parity Fix Log (2026-09-08)

Permanent record of the cross-agent parity work, its reviewed evidence, the controlled-call boundary, and the terminal state. This record is compiled from the plan, the plan-owned SDD ledger, reviewed diffs, and offline verification. It contains no prompts, provider responses, evaluator inputs, secrets, or raw environment dumps.

## 1. Campaign Context and Frozen Base

- Plan: `docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md`.
- Approved base: `a983df883627f08062b9bd4c9b2c7c49c0ce1b79`.
- Planner reference merge: `bc67620666bbc41c516556d45602de6dd00d7102`.
- Branch/worktree: `codex/cross-agent-planner-fix-parity` / `.worktrees/cross-agent-planner-fix-parity`.
- Global token cap: `4096`; live tier: prohibited.
- The campaign preserves Planner behavior and adds shared ReAct/provider-boundary parity for the five non-Planner agents. The plan amendment in `cb394c0` documents the post-gate repair workflow and per-agent evidence contracts.

## 2. Planner Lessons Reviewed

The Planner campaign established the relevant transfer rules:

- Fix contract mismatches at the shared boundary when multiple agents consume the same contract; do not copy a Planner-local wrapper into each sibling.
- Preserve exact provider identity and Planner-specific error translation while removing a local workaround.
- Keep operation-specific budgets isolated from the global ReAct/judge budget.
- Treat provider, schema, harness, and quality failures as distinct typed routes.
- Use immutable evidence, bounded repair loops, fresh task-scoped review, and a permanent secret-safe fix log.

## 3. Transferability Matrix

| Planner lesson | Cross-agent application | Evidence |
| --- | --- | --- |
| Shared boundary owns shared normalization | Normalize optional ReAct fields in `run_react_loop` | `66340a4`; focused and neighboring ReAct tests |
| Local wrappers must not define shared behavior | Remove the Planner-only wrapper after parity tests pass | `45178ab`; Planner/provider-identity regressions |
| Failure details need a finite safe projection | Use `ProviderFailureSnapshot` and agent operation labels | `c4e3fee`, `9349166`, `0c9c0ec`, `808cfba` |
| Fallback semantics remain authoritative | Preserve each agent's fallback/partial-result behavior | Task 5 focused, agent, and evaluation suites |
| Artifact boundaries need explicit coverage | Assert provider diagnostics survive target serialization | `2fbf6be`; target boundary test |

## 4. Shared RC-A ReAct Boundary Fix

`ReActDecision` permits an empty string in the optional field not used by a decision, while `ReActStep` requires meaningful optional values to be either non-empty or `None`. The shared loop now projects `tool_name` and `final_answer` with `value or None` when constructing each step. The strict `ReActStep` contract remains unchanged.

Commit `66340a4` added the two direct regressions and the minimal shared fix. The focused RED/GREEN evidence and neighboring agent coverage were reviewed before proceeding.

## 5. Planner Local-Wrapper Removal / Regression Evidence

Commit `45178ab` removed `_DecisionNormalizingCompleter` from Planner after sibling parity tests proved the shared boundary handled the same empty-field cases. The change retained Planner provider identity, `preserve_provider_errors=True`, the `planning_provider_error("react_decision")` translation, and the Planner-only final-plan token field.

The parity tests cover Researcher, Fact Checker, and Critic ReAct paths with an empty unused optional field. Factory and assembly tests continue to assert exact provider identity. Planner behavior was not reimplemented in the siblings.

## 6. Researcher Findings and Repairs

No agent-specific Researcher repair was authorized or inferred from offline evidence. The shared ReAct normalization applies to its execution path, and Task 5 preserves its existing fallback semantics while projecting only finite provider diagnostics. The controlled Researcher baseline was not reached because the required credentials were unavailable; therefore no quality conclusion, budget amendment, or provider-response diagnosis is recorded for Researcher.

## 7. Source Evaluator Findings and Repairs

No agent-specific Source Evaluator repair was authorized or inferred. Task 7's characterization tests pin the expected non-ReAct schema sequence and reject `ReActDecision` for this agent. Its controlled baseline was not reached because credentials were unavailable; no output-limit or quality repair is claimed.

## 8. Fact Checker Findings and Repairs

No agent-specific Fact Checker repair was authorized or inferred. Its ReAct parity regression exercises the shared boundary, while Task 5 retains existing fallback/partial-result behavior and adds only safe typed diagnostic projection. Its controlled baseline was not reached; no quality or operation-budget diagnosis is claimed.

## 9. Synthesizer Findings and Repairs

No agent-specific Synthesizer repair was authorized or inferred. Task 7's characterization tests pin its expected non-ReAct schema sequence and reject `ReActDecision`. Its controlled baseline was not reached because credentials were unavailable; no repair or no-change quality conclusion is claimed.

## 10. Critic Findings and Repairs

No agent-specific Critic repair was authorized or inferred. Its ReAct parity regression covers an empty unused optional field, and Task 5 preserves the existing fallback path while adding bounded diagnostics. Its controlled baseline was not reached; no quality or output-limit conclusion is claimed.

## 11. Safe Provider Diagnostic Projection

Task 4 introduced an immutable finite `ProviderFailureSnapshot` and `provider_failure_snapshot` helper. The projection retains only allow-listed failure kind, bounded exception type, retryability, validated HTTP status, configured token cap, typed usage, attempt numbers, and bounded structured-validation diagnostics. It never retains exception messages or provider content.

Task 4's first review found that caller-supplied extras could collide with reserved safe fields. Commit `9349166` closes that collision path. Commit `808cfba` adds the public package export required by the import-surface test. Task 5 projects the snapshot through exact operation names in each non-Planner fallback without changing fallback results.

## 12. Operation-Specific Budget Decisions

The global maximum remains `4096` tokens, with retry count `5`, initial delay `1.0`, maximum delay `16.0`, controlled repetitions `3`, repetition floor `0.65`, case-average threshold `0.80`, and maximum concurrency `1` as documented campaign values.

No Task 10 budget amendment was authorized: a typed Task 9 output-limit artifact is required first, and no controlled baseline reached the provider. No global cap, evaluation input, judge budget, or frozen case was changed.

## 13. Controlled Evaluation Evidence

Task 9 was prepared but did not reach a provider or LangSmith request. The required `DEEPSEEK_API_KEY` and `LANGSMITH_API_KEY` were unavailable in the execution environment. All five agents therefore ended at `INFRASTRUCTURE_BLOCKED` with harness status `NOT_REACHED`, exit status `1`, and zero repair attempts.

There are no controlled result inventories, provider traces, evaluator URLs, or live experiment links to claim. No `--tier live` command was run. The missing credentials are recorded by name only; their values are not recorded.

## 14. Environment / Retry / Worktree Rulings

- The campaign worktree is isolated at `.worktrees/cross-agent-planner-fix-parity` on `codex/cross-agent-planner-fix-parity`.
- The SDD helper's installed executable bit was corrected locally; invocation through Bash was the recorded environment ruling.
- The exact offline pytest command can encounter a missing `socksio` dependency when inherited proxy variables are present. The reproducible process-only workaround unsets uppercase and lowercase HTTP(S)/FTP proxy variables; it does not modify tracked code.
- The campaign uses the fixed retry policy documented in the plan: five retries with `1.0`/`16.0` exponential bounds.
- The broad whole-branch review remains explicitly deferred by human instruction. Only task-scoped reviews were performed.

## 15. Review Findings and Resolutions

- Task 2: approved; no Critical, Important, or Minor findings.
- Task 3: approved; no Critical or Important findings; report-provenance caveats were recorded.
- Task 4: one Important reserved-extra collision was fixed in `9349166` and passed scoped re-review; the missing package export was fixed in `808cfba` and passed a second scoped re-review.
- Task 5: fresh Luna Max review approved the six-agent-module diff with no Critical, Important, or Minor findings.
- Task 6: approved; the production target serializer remained unchanged.
- Task 7: approved; characterization tests pin the non-ReAct architecture.
- Task 8: approved; the offline candidate was `808cfba` before documentation/rebase commits, with focused `690` passed and full offline `1,895` passed, one deselected, and two dependency warnings.
- The deferred minor note is that trace output can show `"(empty)"` before functional step normalization; it is non-load-bearing and does not justify scope expansion.

## 16. Deferred Non-Load-Bearing Findings

- Explicit generic output-limit mapping/export coverage may be expanded only in a later coverage review.
- Trace span text normalization may be improved separately from functional `ReActStep` normalization.
- Controlled quality, budget, and provider-response findings remain unknown until credentials are supplied and each paid command is individually authorized.
- Whole-branch review is not performed in this campaign because it was explicitly deferred.

## 17. Final Verification and Terminal State

Tasks 1–8 are complete and task-reviewed. The offline candidate is the `808cfba` campaign state; the current branch additionally contains the reviewed plan amendment, the current `origin/main` Streamlit documentation commit through rebase, and this permanent fix log.

The campaign terminal state is `CONTROLLED_BASELINES_REQUIRED` / five-agent `INFRASTRUCTURE_BLOCKED`. The branch is ready for the separately authorized GitHub push, but it is not a claim of successful paid evaluation, live-tier execution, merge, release, or deployment.
