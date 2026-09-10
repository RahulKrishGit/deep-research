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

## 6. Controlled Baseline and Confirmation Findings

The Windows output-root repair was validated at candidate `d6a082c9260e5d9f2f7c061833b4734ec9990f1f`. Every agent completed all nine rows in its baseline and its one permitted same-SHA confirmation; none reproduced the historical Windows preflight failure. The results below are immutable, and the safe inventories are under `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/infrastructure-remediation/d6a082c-v3/agents/<agent>/`.

| Agent | Baseline | Confirmation | Persistent typed evidence | Result hashes (baseline / confirmation) |
| --- | --- | --- | --- | --- |
| Researcher | exit `1`; 9/9; 111/126 gates; mean `0.56` | exit `1`; 9/9; 109/126; mean unavailable | `judge_output_limit`; fallback `output_limit` on `researcher_finding_extraction` and `react_decision` | `a90b5ed9...48836` / `b721956d...50527` |
| Source Evaluator | exit `1`; 9/9; 117/126; mean `0.92` | exit `1`; 9/9; 117/126; mean `0.91` | `required_fields_present`; typed judge output-limit/schema failures; fallback `output_limit` on `source_evaluator_scoring` | `0419ee76...edc89b` / `75517f20...523c10` |
| Fact Checker | exit `1`; 9/9; 113/135; mean `0.70` | exit `1`; 9/9; 115/135; mean `0.67` | `required_fields_present`; typed judge output-limit/schema failures; fallback `output_limit`/schema on `react_decision` | `d1f6f2d9...d002d` / `5e8c6867...415a4` |
| Synthesizer | exit `1`; 9/9; 126/135; mean `0.91` | exit `1`; 9/9; 126/135; mean `0.90` | `required_fields_present`; typed judge schema failures; fallback schema evidence | `2f55779f...6e37` / `cdaaf2ac...f46d3` |
| Critic | exit `1`; 9/9; 112/126; mean `0.87` | exit `1`; 9/9; 108/126; mean `0.64` | `required_fields_present`; `route_consistent`/`no_prohibited_calls`; typed judge schema/output-limit and fallback `critic_report_review` evidence | `b83da8b4...3ccc9` / `8595bf93...0b637` |

The shortened hashes above are display-only; each inventory records the complete SHA-256. All five terminal records set `terminal_state: INFRASTRUCTURE_BLOCKED`, `repair_attempts: 0`, and preserve both immutable result paths. The repeated required-fields failures are not treated as a source repair because the same rows also contain typed judge/provider failures that prevent a valid quality verdict. Judge-side `output_limit` is not target operation budget evidence.

## 7. Agent-Specific Repair Decision

No Task 11 budget amendment or Task 12 prompt/behavior repair is authorized by this evidence. The observed output-limit events are judge-side or fallback/provider diagnostics, not typed target-side `output_limit` evidence for an eligible non-ReAct operation. The shared Windows path repair is therefore the only source fix in this campaign. A future repair requires a new approved diagnosis after the evaluation-contract/provider issue is resolved; it must not tune prompts or budgets against these blocked verdicts.

## 11. Safe Provider Diagnostic Projection

Task 4 introduced an immutable finite `ProviderFailureSnapshot` and `provider_failure_snapshot` helper. The projection retains only allow-listed failure kind, bounded exception type, retryability, validated HTTP status, configured token cap, typed usage, attempt numbers, and bounded structured-validation diagnostics. It never retains exception messages or provider content.

Task 4's first review found that caller-supplied extras could collide with reserved safe fields. Commit `9349166` closes that collision path. Commit `808cfba` adds the public package export required by the import-surface test. Task 5 projects the snapshot through exact operation names in each non-Planner fallback without changing fallback results.

## 12. Operation-Specific Budget Decisions

The global maximum remains `4096` tokens, with retry count `5`, initial delay `1.0`, maximum delay `16.0`, controlled repetitions `3`, repetition floor `0.65`, case-average threshold `0.80`, and maximum concurrency `1` as documented campaign values.

No Task 10 budget amendment was authorized: a typed Task 9 output-limit artifact is required first, and no controlled baseline reached the provider. No global cap, evaluation input, judge budget, or frozen case was changed.

## 13. Controlled Evaluation Evidence

Task 10 used the repository environment through the safe launcher and process-only retry/token overrides. The five baselines and five confirmations reached DeepSeek/LangSmith and produced valid nine-row result artifacts. No raw credentials, prompts, provider responses, evaluator inputs, hidden reasoning, or unredacted exception strings were recorded. No `--tier live` command and no `suite` command were run; live evaluation remains outside this plan.

The historical pre-repair artifacts under the old SHA remain immutable. The current evidence is exclusively the `d6a082c-v3` namespace and the result paths named in each terminal record. The inventory helper was minimally corrected in ignored control-plane tooling to accept exact Windows `\\?\\` paths while continuing to reject wildcard/ellipsis paths; this helper change is not a source or tracked application change.

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

Tasks 1–10 and the Task 14 documentation update are complete and task-scoped reviewed where required. The current tracked candidate is `d6a082c`; the implementation source remains the reviewed Task 3 Windows path repair. The five agents are individually terminal-blocked after valid same-SHA confirmations, and no agent-specific repair is claimed.

The campaign terminal state is `CONTROLLED_BASELINES_COMPLETE_WITH_INFRASTRUCTURE_BLOCKS`. Final offline verification after the documentation update passed: focused config tests `47 passed`, `tests/test_evaluation` `697 passed`, full offline suite `1,936 passed, 1 deselected`, Ruff passed, and `git diff --check` passed. Whole-branch review remains explicitly deferred. This branch is not a claim of successful quality evaluation, live-tier execution, merge, release, or deployment.
