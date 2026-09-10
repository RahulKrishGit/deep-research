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

Tasks 1–14 and the final offline verification are complete and task-scoped reviewed where required. The fresh post-repair controlled candidate is `1f790b0`; the documentation is committed at the pushed branch tip, and the implementation source remains the reviewed Task 3 Windows path repair plus the approved Task 12 repairs. The five agents are individually terminal-blocked after valid same-SHA confirmations, and no further agent-specific repair is claimed.

The campaign terminal state is `CONTROLLED_BASELINES_COMPLETE_WITH_INFRASTRUCTURE_BLOCKS`. Final offline verification after the final Task 13/14 documentation update passed: full offline suite `1,944 passed, 1 deselected, 2 warnings`, Ruff passed, and `git diff --check` passed. Whole-branch review remains explicitly deferred. This branch is not a claim of successful quality evaluation, live-tier execution, merge, release, or deployment.

## 18. Approved Repair Wave 1: Cross-Channel Required Fields

- Diagnosis: `required_fields_present` inspected only `TargetOutput.result`, while production `_success_output()` intentionally keeps agent result and `state_update` as separate typed channels. This was confirmed for Source Evaluator (`evaluated_sources`), Fact Checker (`verified_claims`), Synthesizer (`report`), and Critic (`critique`). Researcher remains the control case because `findings` belongs in `result`. The original controlled artifacts remain immutable.
- RED evidence: production-shaped fixtures failed the four state-update-only cases while the Researcher result case and missing-from-both control behaved as expected.
- Fix: `src/deep_research/evaluation/evaluators.py` now treats a required field as present when its exact key exists in either mapping; empty values remain present and no field names, truthiness rules, gates, cases, thresholds, or fallback behavior changed.
- Tests: `tests/test_evaluation/test_evaluators_general.py` adds five production-boundary/control cases plus missing-from-both. Focused evaluator/target verification passed `75` tests; Ruff and `git diff --check` passed.
- Commit: `7c58abc08b83ab6da6d36cead9fa1f7316ae646a`.
- Review: Luna Max approved with no findings; review confirmed key-presence semantics, coverage of all five agents/controls, and unchanged gate ordering.
- Remaining work: Critic fallback route predicate, Critic ReAct context wiring, and judge diagnosis from typed `field_paths` (`$` and `rationale`) remain separate repair/diagnosis items. No token-budget change is authorized.

## 19. Approved Repair Wave 2: Critic Provider-Fallback Routing

- Diagnosis: `fallback_critique(reason="provider_unavailable")` intentionally returns `should_continue=False`, but `_route_consistent_passes()` sent the fallback score and gaps through ordinary score-based `route_decision()`. With budget remaining, the evaluator could require continuation and fail a correct provider-outage fallback.
- RED evidence: the typed `critic_report_review` fallback regression failed `route_consistent` before the production change; no raw provider message was needed.
- Fix: `src/deep_research/evaluation/evaluators.py` now recognizes only the exact `critic_report_review` operation plus an allow-listed typed provider-failure kind, then evaluates the fallback using its intentional stop semantics. Normal quality routing and exhausted-budget routing remain unchanged.
- Tests: `tests/test_evaluation/test_evaluators_agents.py` covers the fallback, normal-quality, and exhausted-budget paths. Focused Critic/evaluator tests passed `66`; Ruff and `git diff --check` passed.
- Review: the first Luna Max attempt was invalid because it imported the stale `.worktrees/streamlit-ui` package. That result was explicitly rejected and not used as evidence. A corrected rerun bound `PYTHONPATH` to this campaign worktree, confirmed the intended evaluator import, passed `66` tests, and was approved with no findings.
- Commit: `12f9ebef1d3faf4020da8dc3badbf58cf2acc503`.
- Remaining work: Critic ReAct context wiring and judge diagnosis from typed field paths. No token-budget change is authorized.

## 20. Approved Repair Wave 3: Critic ReAct Context Wiring

- Diagnosis: `CritiqueTask` already carried report, claims, sources, and subtopic titles, but `CriticAgent.build_task()` supplied empty `guidance`; `render_react_messages()` therefore omitted the planned subtopic/search-query context from the ReAct decision. The fail-closed scripted search client correctly exposed this missing context.
- RED evidence: the first ReAct prompt did not contain the existing `Alpha` subtopic or exact planned query `alpha 2025`.
- Fix: `src/deep_research/agents/critic.py` now renders existing subtopic titles and exact planned queries into deterministic `CritiqueTask.guidance` and instructs verbatim reuse for applicable spot checks. The first full offline gate caught the new helper as a public-looking name missing from `deep_research.agents.__all__`; the helper was made private in `8271dbc`. No scenario, query string, tool isolation, budget, report, claim, source, or other-agent behavior changed.
- Tests: `tests/test_agents/test_critic.py` asserts the first ReAct provider prompt contains the planned context and instruction. Campaign-bound focused Critic/prompt tests passed `66`; the first full gate exposed `test_agent_submodule_public_names_all_reach_all` (`1,943 passed, 1 failed`), then the corrected full gate passed `1,944` with one deselected. Ruff and `git diff --check` passed.
- Review: Luna Max approved with no findings after confirming the campaign source binding and the two-file scope. The reviewer stopped before executing its own collected tests; controller verification is the authoritative executed result.
- Commits: `b7c2500bef5a071b660b77931cfd9d0322d0bf37` and integration correction `8271dbc69cd3124e897bca771d845e4c3aba53a5`.
- Remaining work: typed judge diagnosis from the immutable field paths `$` and `rationale`. No token-budget change is authorized.

## 21. Judge Boundary Diagnosis and No-Change Decision

- Evidence source: the immutable confirmation `results.json` artifacts at candidate `d6a082c`, read only through typed `judge.not_run_reason` and `judge.diagnostics[].{kind,attempt,field_paths}`.
- Observed schema paths: `$` occurred `15` times and `rationale` occurred `5` times across the five confirmation artifacts. The root path indicates a provider/schema shape failure without a safe field-level contract; `rationale` is a minority missing-field signal. There is no single repeatable `JudgeVerdict` field failure.
- Existing boundary behavior remains correct: `JudgeVerdict.rationale` is bounded to `1..2000`; the provider performs exactly one structured repair attempt; typed schema/output-limit diagnostics are preserved; judge failures never receive fabricated scores.
- Decision: no judge schema, prompt, provider, output-budget, status-aggregation, rubric, threshold, or scoring change is justified by the available typed evidence. A synthetic RED test would require guessing the provider response shape, which is prohibited by the campaign evidence rules.
- Deferred issue: status aggregation can publish an ordinary `FAILED` result when a hard gate and unscorable judge failure coexist; this requires a separate explicit precedence decision and is not bundled into the agent repairs.

## 22. Post-Repair Validation Readiness

- Repaired candidate used for the fresh controlled validation: `1f790b0e8914f1b4ba922d9d56314da99ebbcf28`.
- Offline gate: repair-focused `1,197 passed`; full offline suite `1,944 passed, 1 deselected`; Ruff and `git diff --check` passed.
- Controlled validation must use a fresh immutable evidence namespace and preserve the old `d6a082c-v3` artifacts. The global 4096-token cap, frozen cases, provider fallback contracts, judge no-score rule, and live/suite prohibition remain unchanged.

## 23. Post-Repair Controlled Validation and Error/Fix Ledger

- Candidate `1f790b0e8914f1b4ba922d9d56314da99ebbcf28` was validated in the fresh immutable namespace `infrastructure-remediation/1f790b0-v1/`. Every agent completed a baseline and one same-SHA confirmation with 9/9 repetitions; no Windows output-root preflight failure recurred.
- Researcher: baseline `111/126`, mean `0.73`, SHA `a91486749bc5c3e62068e76af78ba5eb91340b0b62f9ad3383cded5701af5fef`; confirmation `112/126`, mean `0.66`, SHA `aa47a7c1004ce3ef54cb556ba88e8920f12b30f68aa5f624826ec1487529b8ce`. `required_fields_present` stayed fixed; `no_prohibited_calls`, selected coverage/budget gates, and judge `$`/`rationale` diagnostics remained.
- Source Evaluator: baseline `126/126`, mean `0.95`, SHA `b666e25086f00e9a6524fe6b649e7ed37d6ae43fea959f11dc9a6543e9028a1d`; confirmation `126/126`, mean `0.94`, SHA `b02f164458495571ab6adf8425d8015c702b32d6545d7dd2b394edf69e675291`. `required_fields_present` stayed fixed; only typed judge schema/output-limit failures prevented promotion.
- Fact Checker: baseline `119/135`, mean `0.75`, SHA `4ffd242fbc874ac1147fce5a528dfc2db0480e3e720ae4c06883586b61568600`; confirmation `119/135`, mean `0.58`, SHA `65a6e5773504ca163a157f622b775f828e51f2214d7e6a6d8c6ce2b884df14fb`. `required_fields_present` stayed fixed; `budgets_respected`/`no_prohibited_calls` and typed judge failures remained.
- Synthesizer: baseline `135/135`, mean `0.91`, SHA `86160fea440dbb157e5031ce5bbcd5f5f27696620e1d5129f1d83ca39ee85c58`; confirmation `135/135`, mean `0.92`, SHA `e157d7fea64e8e8ba9f57945a6ad61d78522b329b9f76835472c6e57bfb99a0d`. `required_fields_present` stayed fixed; only typed judge schema/output-limit failures prevented promotion.
- Critic: baseline `122/126`, mean `0.85`, SHA `71a56b57a2968dcd76063945f3750796d21d63a8c2fa4961936c426709b19bbe`; confirmation `121/126`, mean `0.79`, SHA `7a1a67a3fbb5ddd1e3e5cde9922104665ba952d79cda5e0774126b7719624998`. `required_fields_present` and `route_consistent` stayed fixed; `no_prohibited_calls` and typed judge schema/output-limit failures remained.
- Each agent's baseline/confirmation inventory, environment-matched provenance, and `terminal-state.md` is preserved under `infrastructure-remediation/1f790b0-v1/agents/<agent>/`; each terminal state is `INFRASTRUCTURE_BLOCKED` with `repair_attempts: 0`. No live-tier or suite evaluation ran.
- Error/fix ledger entries preserved the initial provenance-capture mistakes: Source Evaluator had two unusable confirmation captures (missing repository environment, then missing process overrides), and Fact Checker/Synthesizer/Critic exposed a helper limitation where default target effort was max while the approved run explicitly used high. These were retained/reconciled as non-secret evidence metadata; no provider output, prompt, evaluator input, credential, or environment dump was recorded.
- No Task 11 budget amendment is authorized: no typed target-side `output_limit` appeared. No new judge/provider repair is authorized: schema paths remain mixed (`$` and `rationale`) rather than a repeatable contract defect. No new agent-quality repair is authorized from blocked quality rows.
- The campaign terminal state remains `CONTROLLED_BASELINES_COMPLETE_WITH_INFRASTRUCTURE_BLOCKS`. The whole-branch review remains explicitly deferred until the user requests it.

## 24. Task 16: Controlled Scenario-Miss Contract Repair

- Candidate before implementation: `415223c` on isolated worktree `codex/cross-agent-planner-fix-parity`; the pre-existing unknown-search regression was preserved as the RED starting point.
- Diagnosis: the injected search double classified an unknown exact query as `ProhibitedDependencyError` and recorded it in `prohibited_calls`. That is a missing scripted scenario response, not evidence of real-service access. The Critic strong scenario also had a response key outside the case's planned-query set.
- RED evidence: the unknown-search regression failed because `prohibited_calls` was non-empty; new contract, gate, bounded-telemetry, and Critic planned-query tests were added before the production repair and failed on the missing behavior.
- Fix: `ScenarioMissError` now marks an unknown controlled search; `DependencyRecorder` records bounded `scenario_misses` separately; `DependencyLedger` carries additive `scenario_contract_version` and bounded telemetry; controlled scripts use version 2 while legacy ledger payloads remain v1-compatible. `no_prohibited_calls` remains based only on `prohibited_calls`, and unscripted HTTP access remains the true prohibited path.
- Critic contract: the strong response key now exactly matches an applicable planned query. Strong, gappy, and budget controlled scenarios are pinned to their v1 case planned queries and the current controlled-harness contract version.
- Implementation commit: `d1ee97d1ed6d7dff6a47e4efbadebba8d541e419`.
- Verification: focused Task 16 `27 passed`; relevant evaluation/case/model/target/runner tests `277 passed`; evaluation plus Critic tests `748 passed`; full offline suite `1,952 passed, 1 deselected, 2 warnings`; Ruff passed; `git diff --check` passed.
- Safety boundary: no live-provider, paid, or suite evaluation ran; no secret, prompt, evaluator input, provider response, credential, or raw exception text was recorded; `.deepseek-runs/` remains preserved and unstaged. Task 17 and whole-branch review remain deferred.

## 25. Task 16 Review Disposition

- Luna-max task-scoped review: PASS; no blocking findings.
- Independent verification reproduced the focused `27 passed`, expanded `277 passed`, evaluation-plus-Critic `748 passed`, and full offline `1,952 passed, 1 deselected, 2 warnings` results. Ruff and `git diff --check` also passed.
- Review confirmed the branch and remote tip are both `5f0a1dd1fed16bd7cf28b61916c16175060de316`, with only the preserved untracked `.deepseek-runs/` evidence directory outside the tracked change set.
- Scope review found no Task 17 judge/status changes, token or target-budget changes, rubric/threshold/weight changes, or live/paid/suite evaluation. Task 17 and whole-branch review remain deferred.

## 26. Task 17: Judge Diagnostics and Status Precedence

- Candidate before implementation: `b99e88f077e7c6ddafd130fbb4583465098f107e`, on isolated worktree `codex/cross-agent-planner-fix-parity`.
- Diagnosis: structured-validation telemetry collapsed distinct local Pydantic failures into `schema_output`, while the OpenAI repair path retained provider-bearing validation text; status aggregation treated judge-only typed failures as ordinary `FAILED` even when deterministic execution and gates passed.
- RED evidence: DeepSeek category cases failed `9` tests with `89` deselected; the OpenAI provider-content regression failed `1` with `37` deselected; evaluator category projection failed `1` with `18` deselected; judge-status precedence failed `5` with `1` passed and `53` deselected. The failures were limited to the new production-shaped expectations.
- Classification and ruled-out alternatives: this is a shared provider/evaluation boundary defect. No target prompt, target budget, global `4096` cap, rubric, threshold, scoring weight, fallback contract, frozen v1 artifact, live command, paid call, or provider command was changed or authorized.
- Smallest repair: `src/deep_research/providers/validation.py` now derives bounded categories and schema-proven field paths from local stable Pydantic error types; provider failures retain only typed diagnostics and repair summaries. `contracts.py`, both provider adapters, evaluation diagnostic projection, and `runner.py` carry the finite categories and distinguish judge-only infrastructure failure from deterministic quality failure. Tests in `tests/test_deepseek_provider.py`, `tests/test_openai_provider.py`, `tests/test_evaluation/test_failure_taxonomy.py`, and `tests/test_evaluation/test_runner.py` pin malformed JSON, missing/nested/root/extra/bounded cases, provider-content exclusion, typed projection, and status precedence.
- Implementation commit: `b4d3704`.
- Verification: focused provider/evaluation/runner suite `293 passed, 1 warning`; full offline pytest `1,973 passed, 1 deselected, 2 warnings`; changed-scope Ruff `All checks passed`; new validation module format check passed; `git diff --check` passed. The warnings are dependency deprecations and Git line-ending conversion notices only.
- Review disposition: no task-scoped review was dispatched, per the explicit implementation-task instruction; controller review remains the next action. Whole-branch review remains deferred.
- Safety state: no live, paid, controlled, suite, LangSmith, or provider command ran; `.deepseek-runs/` remains preserved and unstaged; no provider content, prompt, evaluator input, credential, raw validation context, or unredacted exception text was recorded.

## 27. Task 17 Fix Round 2: DeepSeek Traceback-Context Leak

- Candidate before implementation: `8d8a79d` on `codex/cross-agent-planner-fix-parity`; the pre-existing untracked `.deepseek-runs/` evidence was preserved.
- Diagnosis: the public `StructuredOutputError` already had bounded diagnostics and no linked validation exception, but `complete_structured()` left schema-derived `instruction`, `schema_json`, and `repair` values in its provider traceback frame after the final two-attempt failure.
- RED evidence: the fake-driven DeepSeek traceback regression failed `1` test with `98` deselected when a harmless schema-description marker was reachable through provider traceback locals; the public error still had no cause or context.
- Minimal fix: replace those three request/prompt locals with safe empty values immediately before raising the existing typed error. The regression now proves the schema marker is present in both the initial instruction and repair request, but absent from the reachable typed error graph and provider traceback surfaces.
- Verification: campaign-bound DeepSeek tests `99 passed, 1 warning`; provider/taxonomy/runner suite `221 passed, 1 warning`; full offline pytest `1,980 passed, 1 deselected, 2 warnings in 27.48s`; changed-file Ruff `All checks passed!`; `git diff --check` passed with only existing LF/CRLF conversion notices.
- Implementation/test commit: `496c1ca` (`fix: scrub DeepSeek structured prompt locals`). No provider contract, retry behavior, repair count, token cap, runner precedence, target budget, evaluator threshold, frozen artifact, or fallback semantic changed. No push or review was started.

## 28. Task 17 Review Disposition and Terminal State

- Fix-round 1 review found one remaining critical issue: DeepSeek schema-derived `instruction`, `schema_json`, and `repair` values were still reachable through traceback locals. It also confirmed the runner deterministic-before-judge-only precedence repair was closed.
- Fix-round 2 repaired only that DeepSeek traceback boundary in `496c1ca`; the documentation update is `2806c34`.
- Fix-round 2 Luna-max review verdict: `PASS`. An independent campaign-pinned object-graph/frame-local probe confirmed that DeepSeek and OpenAI public typed errors expose no provider response, request, prompt, schema, repair, raw validation object, `input_value`, raw mapping, or linked exception through reachable error graphs or traceback frames.
- Review verification: focused provider/taxonomy/runner suite `221 passed, 1 warning`; full offline pytest `1,980 passed, 1 deselected, 2 warnings`; Ruff and `git diff --check` passed. No live, paid, provider-network, LangSmith, suite, or whole-branch review ran.
- Terminal state: Task 17 is complete and pushed at `2806c34`; `.deepseek-runs/` remains preserved and untracked; global `llm.max_tokens == 4096`, target budgets, frozen v1 inputs, thresholds, rubrics, weights, fallback semantics, and no-fabricated-score behavior remain unchanged. Whole-branch review remains deferred until explicit user request.

## 29. User-Authorized Live Evaluation Amendment

- After the Task 17 offline implementation and two clean scoped review rounds, the user explicitly authorized one live-tier repetition for all six registered agents at candidate `d697ff6`. The detailed, secret-safe report is `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-live-evaluation.md`.
- Live evidence confirms the shared repairs helped: all six agents passed `required_fields_present`; Critic passed `route_consistent` and `no_prohibited_calls`; and Synthesizer's all-gates-passing judge schema failure was classified as `INFRASTRUCTURE FAILURE` without a fabricated score. Source Evaluator's mixed hard-gate plus judge failure remained `FAILED` with no score.
- Live quality was not universally repaired: Researcher failed citation/source-grounding gates, Fact Checker and Critic scored below the live threshold despite passing deterministic gates, and Source Evaluator/Synthesizer still exposed judge structured-output failures. These are documented as evidence, not as permission to tune prompts or budgets.
- No target-side typed `output_limit` failure was observed. Fact Checker's `fallback_provider_diagnostic` `output_limit` for `react_decision` remains a fallback diagnostic, so no operation-specific budget amendment is authorized; `llm.max_tokens == 4096` remains fixed.
- LangSmith experiment and local artifact links/hashes are recorded in the live report. No raw prompts, provider responses, exception text, credentials, or environment values were recorded.

## 30. Task 17 Fix Round 3: OpenAI Structured-Repair Traceback Local

- Trigger: Sol High's whole-branch review identified one Important provider-boundary defect in `src/deep_research/providers/openai_provider.py`: after the second structured-output failure, the generated `repair_instruction` remained reachable through provider traceback-frame locals even though the public typed error was bounded.
- RED evidence: the focused fake-provider regression injected a unique repair marker, confirmed it was present in the second request, and initially found the marker reachable through the public exception/traceback surface (`1 failed, 38 deselected`).
- Minimal fix: `a95262f53c70b9abf6f9794337c63250bbc6af13` (`fix: scrub OpenAI repair prompt local`) clears `repair_instruction` immediately before the existing final typed raise. The two-attempt/one-repair behavior, diagnostics, exception chaining, request context, and retry boundary are unchanged.
- GREEN evidence: the focused regression passed (`1 passed, 38 deselected`); the OpenAI provider suite passed (`39 passed`); the provider/runner focused gate passed (`221 passed, 1 warning`); changed-source Ruff and `git diff --check` passed.
- Review: fresh Luna-max task review approved the two-file diff with no Critical or Important findings. Its only Minor note was that the named standalone diff path was not visible from the projectless reviewer context; the equivalent base-to-head diff was verified, and the retained package is `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/review-41512a5..a95262f.diff`.
- Safety boundary: no DeepSeek, evaluator, target-agent budget, global `llm.max_tokens == 4096`, frozen case, scenario, threshold, rubric, fallback, live, paid, or suite behavior changed. `.deepseek-runs/` remains preserved and untracked.
- Controller completion: full offline pytest passed (`1,980 passed, 1 deselected, 2 warnings`); Ruff passed; `git diff --check` passed with only Windows line-ending notices. The feature branch and `origin/codex/cross-agent-planner-fix-parity` are synchronized at `23f70c02ee91a263adbe7b1ce716050deb3c6546`.
- Follow-up boundary: do not start another paid/live run or whole-branch review from this fix round. Existing live evidence remains valid for the agent-quality interpretation; a future repeated live wave and whole-branch review are separate scheduled work.

## 31. Candidate 160c334 Live Evidence Rerun

- Authorization and scope: the user authorized the next evidence wave after the approved OpenAI fix. One live repetition ran for each registered agent, sequentially, with no retries, no suite command, no browser chat, and no source/test/config/evaluation-input changes.
- Frozen configuration: target/judge `deepseek-v4-flash`; judge effort `max`; target effort `max` by default with Researcher and Source Evaluator at `high`; live repetitions `1`; concurrency `1`; threshold `0.75`; global `llm.max_tokens=4096`.
- Results: Planner `REVIEW REQUIRED` with `16/16`; Researcher `FAILED` with `12/14`; Source Evaluator `FAILED` with `13/14`; Fact Checker `INFRASTRUCTURE FAILURE` with `15/15`; Synthesizer `INFRASTRUCTURE FAILURE` with `15/15`; Critic `FAILED` with `14/14`.
- Interpretation: required-field coverage remained intact; Critic routing/context and prohibited-call isolation remained supported; judge schema/output-limit instability persisted; no target-side typed `output_limit` appeared. Fact Checker's `react_decision` output-limit remained a fallback diagnostic only. A single noisy repetition does not establish agent-quality improvement or authorize budget/prompt changes.
- Evidence: the six artifact paths, SHA-256 values, exact commands, exit codes, typed classifications, and LangSmith links are recorded in `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-live-rerun.md`. The report uses repository-relative paths and omits secret values.
- OpenAI boundary note: this live run used the DeepSeek provider, so it does not independently exercise the OpenAI traceback-local scrub; that fix remains covered by its focused regression and offline provider suite.

## 32. Sol High Whole-Branch Review at Candidate 6a01175

- Review scope: current remote `codex/cross-agent-planner-fix-parity` at `6a01175`, compared with `origin/main` merge-base `3de6839`; Sol High used the current GitHub branch and tracked evidence rather than the stale `41512a5` review packet.
- Assessment: `Ready with follow-ups`. No Critical or Important finding blocks branch completion. The prior OpenAI traceback-local finding is closed: `repair_instruction` is scrubbed before the final raise and the regression verifies the generated repair marker across the second request and exception/traceback surfaces.
- Task verdicts: Task 16 supported; Task 17 supported; live-rerun interpretation valid and conservative. No prompt tuning, global/operation-specific token increase, suite run, or new paid run is justified by the evidence.
- Minor finding: the plan retained stale chronology at the Task 17 section and incomplete top-level Task 17 bookkeeping after the `a95262f` fix. The plan was corrected to describe the completed offline verification, live evidence, and current whole-branch review.
- Verification qualification: Sol High did not rerun tests or provider evaluations; its assessment relied on current GitHub source plus recorded evidence. The authoritative recorded offline result remains `1,980 passed, 1 deselected, 2 warnings`, with Ruff and `git diff --check` passing.
- Remaining follow-ups: characterize the shared DeepSeek judge boundary using bounded typed field paths and gather repeated target-quality evidence before any prompt/budget change. These are non-blocking post-review work.

## 33. Task 18 Judge Boundary Diagnosis — No-Change Decision

- Candidate: `42a2b4a6f0d1654706b37e4c90c1711df41b2dbe`; read-only diagnosis across 22 preserved `results.json` artifacts and 192 repetitions from the controlled and two live waves.
- Typed grouping: judge `schema_output` paths varied between `$`, `rationale`, and combined paths across agents, waves, and attempts. Judge `output_limit` appeared intermittently as a judge-side `not_run_reason`; it did not carry an operation. Operation-bearing `react_decision`/other fallback diagnostics were kept separate and were not promoted to target failures.
- Decision: no synthetic RED test, judge schema change, provider parsing change, judge budget, target budget, prompt, rubric, threshold, scoring, retry, status, or fallback change is justified. `$` does not identify a particular missing `JudgeVerdict` field, and inventing a provider response shape would violate the typed-evidence boundary.
- Invariants: no target-side typed `output_limit` appeared; global `llm.max_tokens=4096`, one repair attempt, no fabricated judge score, and all existing fallback semantics remain unchanged. No paid/provider-network/evaluation command ran during diagnosis.
- Evidence report: `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/task-18-judge-boundary-diagnosis-report.md`. The branch and remote remained at `42a2b4a`; `.deepseek-runs/` was preserved and untracked.
- Next action: keep the current judge/provider/evaluation implementation unchanged. Future work should gather a stable typed category, exact operation, attempt, and schema-proven field path before opening a new RED-test/repair decision.

## 34. Task 19 Sequential Live-Agent Loop — Researcher Diagnosis Pending

- Candidate: `c368849ac1da0280f5d1ea8428ad8b3974929fce`; one user-authorized live repetition was run for Researcher only. No retry or other agent run occurred.
- Evidence: the tracked report is `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-sequential-live-researcher.md`; its preserved artifact has SHA-256 `715B268D060617907021CB0283258A64AEAFB0A49D90B44F9A8CA158722754A0`.
- Observed result: `FAILED`, `12/14` hard gates, deterministic quality `0.70`; `citations_known` and `no_invented_sources` failed; `react_stop_reason=max_iterations`; `prohibited_call_count=0`.
- Independent typed boundary: the judge was unscorable after `judge_schema_failure` diagnostics at `rationale` and `$`. No target-side typed `output_limit` appeared, so no budget amendment is permitted.
- Classification: Researcher quality/trajectory signal requiring Sol High diagnosis; not yet a confirmed production defect. Judge/provider instability remains separate and supplies no Researcher score.
- Next action: push this evidence, consult Sol High in the existing browser session with the current branch and repository-relative artifacts, then either implement the smallest reviewed Researcher repair or record an explicit no-change ruling. Run offline verification and one focused Researcher confirmation before advancing to Source Evaluator.

## 35. Task 19 Sol High Researcher Review — Provenance Repair Authorized

- Candidate reviewed: `fa805ab2cf11adf6620eecffd5b502c56537465d`; the pushed tracked report is `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-sequential-live-researcher.md`.
- Sol High diagnosis: the Researcher source-gate failure is not yet a confirmed agent/prompt defect. Successful tool payloads can contain source identities beyond the short trajectory projection, while `citations_known`, `no_invented_sources`, and `sources_are_real_urls` inspect that lossy projection. The current live artifact cannot distinguish a genuinely invented URL from a valid URL truncated out of the trajectory.
- Independent boundaries: judge `schema_output` at `rationale`/`$` remains shared judge/provider evidence; `max_iterations` is a separate trajectory signal; no target-side typed `output_limit` exists.
- Smallest approved task: additive bounded SHA-256 fingerprints of normalized source identities from successful `web_search.results[*].url`, `web_scraper.url`, and remote `document_reader.source` payloads; use only for live Researcher source-provenance gates. Preserve raw-URL-free telemetry, old artifact validation, controlled semantics, all budgets, prompts, cases, rubrics, thresholds, weights, fallback behavior, judge code, and global `llm.max_tokens == 4096`.
- Current task: Task 20 is ready for Luna-high implementation and Luna-max task review. No Researcher agent/prompt repair is allowed until a focused confirmation shows a finding URL absent from the complete retrieval fingerprint set.
- Dispatch blocker: the first Luna-high implementation worker reached the required native ChatGPT sign-in gate before accessing the repository and was closed. No source, test, plan, or artifact change occurred; relaunch is pending user sign-in and `ready` confirmation.
