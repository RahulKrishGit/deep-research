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

## 36. Task 20 Researcher Retrieval-Provenance Implementation

- Candidate: `e954f47` on `codex/cross-agent-planner-fix-parity`, pushed to `origin` before review; the initial implementation is `1c9047b` and the scope-tightening correction is `e954f47`.
- Implementation: `DependencyLedger` now has an additive bounded `source_url_fingerprints` field accepting only lower-case 64-hex SHA-256 values. Live source tools are wrapped at the evaluation dependency boundary; successful `web_search.results[*].url`, `web_scraper.url`, and remote `document_reader.source` values are normalized, deduplicated, hashed, and recorded without retaining raw URLs. The evaluator consumes those fingerprints only for live Researcher `citations_known`, `no_invented_sources`, and `sources_are_real_urls` paths. The controlled tier and all other agents remain fingerprint-blind.
- RED/GREEN evidence: model validation, all three source-payload shapes, bounded trajectory capture, live gate acceptance, and controlled-semantic isolation were added before implementation and now pass (`5` focused tests). The live target regression proves the source URL is absent from the `200`-character trajectory summary while its fingerprint remains available in the artifact.
- Verification: full offline pytest with cache disabled passed `1,985` tests, with `1` deselected and `2` dependency warnings; Ruff passed; `git diff --check` passed. No live/provider/LangSmith command ran after the repair.
- Dispatch bookkeeping: the non-browser Luna-high implementation dispatch did not become ready because the campaign branch was already occupied; the blocked worker made no changes and was not retried as a duplicate. The controller implemented the exact Sol-approved boundary locally. One Luna-max task-review worker was then dispatched against the pushed candidate with no browser or live calls; it remained silent through the extended bounded wait and was closed while still running. No review findings were returned, so this is a review-dispatch infrastructure block, not a PASS.
- Invariants preserved: no Researcher prompt/agent change, no target or judge budget change, no global token change (`llm.max_tokens == 4096`), no case/scenario/rubric/threshold/weight change, no fallback/provider semantic change, and `.deepseek-runs/` remains untracked and preserved.

## 37. Task 20 Sol High Task Review — Fix Round 1 Required

- Review surface: existing Sol High browser conversation, task-scoped review of the pushed `ac37cf7` candidate in `RahulKrishGit/deep-research` on branch `codex/cross-agent-planner-fix-parity`. No code, live evaluation, provider call, credential, raw prompt, raw response, or ignored `.deepseek-runs/` content was requested or used.
- Verdict: `NOT READY`. No Critical findings; two Important findings are load-bearing for the Task 20 provenance contract, and one Minor finding is deferred.
- I1 Important: `DependencyRecorder.record_source_url_payload()` accepts every string in `web_search.results[*].url`, `web_scraper.url`, and remote `document_reader.source`, so a malformed/non-HTTP(S) identity can be fingerprinted and then trusted by the live Researcher source gates. Fix by admitting fingerprints only for valid absolute `http`/`https` URLs with a host; add RED/GREEN coverage for malformed search, scraper, and remote document values, plus failed/unrelated payload controls. Do not change the production search tool's frozen behavior merely to make this telemetry pass.
- I2 Important: the bounded 128-entry list returns silently when a new unique fingerprint exceeds the cap, while the repository does not prove that `web_search.max_results` and existing tool limits make 128 exhaustive. Fix with bounded typed overflow/completeness telemetry (preferred) that makes truncation explicit and prevents a missing fingerprint from being reported as source invention, or with a repository-enforced finite upstream bound that proves the cap exhaustive without changing frozen Researcher behavior. Add artifact, recorder, and evaluator regressions.
- M1 Minor (deferred): add a live non-Researcher regression proving the Researcher-only wrapper guard leaves other live agents fingerprint-blind. This does not enter the Important-finding fix loop unless implementation exposes a semantic defect.
- Review conclusion: the architecture and scope are sound, but the single focused Researcher live confirmation is not authorized until both Important findings are fixed and the fix round receives a clean scoped re-review. The existing 4096 cap, prompts, iterations, target budgets, cases, scenarios, rubrics, thresholds, weights, fallback semantics, judge/provider boundary, and controlled isolation remain unchanged.

## 38. Task 20 Sol High Fix Round 1 Review — Fix Round 2 Required

- Candidate reviewed: `01407697a7a018fbcf1b384f5b14da779e05a7ea`, the pushed fix-round range `800be1f..0140769`.
- Review surface: the existing Sol High browser conversation, read-only and task-scoped. No code, commit, live/provider-network evaluation, credential, raw response, or ignored `.deepseek-runs/` content was requested or used.
- Strengths: the additive `DependencyLedger.source_url_fingerprints_complete` field is typed, backward-compatible, bounded, and explicit on overflow; the evaluator remains fail-closed without mislabeling an unverified citation as invented; malformed authorities, failed results, unrelated keys, raw-URL exclusion, and the 128/129 boundary have regression coverage; scope and the global `llm.max_tokens == 4096` cap remain unchanged.
- Verdict: `NOT READY`; no Critical findings.
- I1 Important remains open: `record_source_url_fingerprints()` validates the normalized candidate rather than the raw URL. A malformed path/query whitespace value such as `https://example.com/a b` can still be fingerprinted, while a valid bracketed IPv6 URL can be rejected after the shared normalizer removes its brackets. Validate the raw candidate for absolute HTTP(S), host, valid authority/port, and whitespace before normalization; add positive bracketed-IPv6 and negative path/query-whitespace tests. Do not change the shared normalizer or production search behavior.
- I2 Important is addressed: overflow is explicit and typed, old artifacts retain the default-complete behavior, and incomplete live Researcher provenance does not become an unearned pass or an explicit invention accusation.
- M1 remains deferred: no live non-Researcher wrapper regression was added; the existing Researcher-only runtime guard is still correct.
- Disposition: fix round 2 is required before the one focused Researcher live confirmation. Keep prompts, agent code, cases, budgets, thresholds, rubrics, weights, judge/provider/fallback semantics, and the no-target-output-limit decision unchanged. Use Luna Max for the implementation/fix subagent and the existing browser Sol High conversation for the next task-scoped review.

## 39. Task 20 Fix Round 2 Implementation — Sol High Review Pending

- Implementation baseline: `45ff39a` documentation plus `0140769` provenance-completeness code; implementation commit: `c117bf2d18dcb443b7213b16918fdfb642b1a544` (`fix: validate raw Researcher source identities`).
- Scope: raw source candidates are now validated for absolute HTTP(S), safe host/authority/port, and literal whitespace/control characters before the unchanged shared normalizer is called. Valid bracketed IPv6 identities are admitted and then normalized for hashing; invalid candidates remain excluded.
- TDD evidence: the new raw-before-normalization regression failed against `0140769` (`1 failed`) because valid bracketed IPv6 was rejected and malformed path/query whitespace was admitted; the final regression passed (`1 passed`). Focused model/dependency/target/evaluator/agent tests passed (`180`); full offline pytest passed (`1,992 passed, 1 deselected, 2 warnings`); Ruff and `git diff --check` passed.
- Invariants: authoritative successful source payload shapes, overflow/completeness telemetry, raw-URL exclusion, old-artifact compatibility, live Researcher-only gate consumption, controlled/non-Researcher semantics, Researcher code/prompts, search behavior, cases, configuration, budgets, judge/provider/fallback behavior, and global `llm.max_tokens == 4096` remain unchanged. No live, paid, provider-network, LangSmith, or suite evaluation ran.
- Review gate: the implementation is pushed and awaits a fresh task-scoped Sol High browser re-review of the fix range. The deferred live non-Researcher wrapper regression remains parked; no focused Researcher live confirmation is authorized until the review is clean.

## 40. Task 20 Sol High Fix Round 2 Re-Review — Focused Confirmation Unblocked

- Review surface: the existing Sol High browser conversation, read-only and task-scoped, against the pushed branch `codex/cross-agent-planner-fix-parity` at `63fda61`, with exact range `45ff39a..63fda61`. No whole-branch review, live/provider-network evaluation, credential, raw response, or ignored `.deepseek-runs/` content was requested or used.
- Verdict: `PASS WITH FOLLOW-UP`; no Critical or Important findings. Sol confirmed that raw source candidates are validated before normalization, malformed path/query whitespace is rejected, valid bracketed IPv6 is admitted, the earlier completeness/overflow repair remains addressed, and the Researcher-only wrapper boundary is preserved.
- Follow-up: the previously deferred Minor non-Researcher wrapper regression remains absent. The existing Researcher-only guard is correct, and Sol explicitly judged this Minor non-blocking for Task 20.
- Decision: exactly one focused live Researcher confirmation is now unblocked under the existing frozen configuration and fresh output namespace. Interpret target gates separately from the independent judge/infrastructure boundary; a judge failure cannot be promoted to a target quality result. No other live agent, suite, prompt, budget, rubric, threshold, scoring, fallback, provider, or global `llm.max_tokens == 4096` change is authorized by this review.
- Controller verification: the first direct full-suite command bound imports to the installed `streamlit-ui-polish` worktree and failed at collection with five missing-symbol errors; this was an environment-binding error, not a campaign result. The corrected source-first invocation passed `1,992` tests with `1` deselected and `2` warnings in `30.43s`; repository-wide Ruff and `git diff --check` passed. No live/provider command ran during this verification.

## 41. Task 20 Focused Researcher Confirmation — Target Gates Pass, Judge Blocked

- Candidate and scope: pushed HEAD `9319024`; exactly one Researcher live repetition ran after the Task 20 implementation and the Sol High `PASS WITH FOLLOW-UP` re-review. Existing `config.yaml` was used with no CLI overrides, the repository dotenv launcher, global `llm.max_tokens == 4096`, and a fresh namespace. No retry, other agent, or suite command ran.
- Result: exit code `1`, status `INFRASTRUCTURE FAILURE`; all `14/14` target hard gates passed and deterministic quality was `1.00`. No target hard gate failed.
- Independent judge boundary: mean score was unavailable because `judge_not_run` carried typed `judge_schema_failure` diagnostics for `rationale` on attempts 1 and 2. No target-side typed `output_limit` appeared; this remains separate from the earlier judge-only evidence and does not authorize a budget change.
- Evidence: LangSmith experiment `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/45f357c5-ff0d-46f4-89c8-2248115c6b34`; artifact `output/evaluations/task20-researcher-confirmation-9319024/researcher/task20-researcher-confirmation-9319024-researcher-live-20260911T011711Z-9319024/results.json`; SHA-256 `F4FC7B4ADD79E95D094BAF2CE6AD3A2159EEC3B1074D7CAD5EEF6C549E33B6B8`.
- Decision: the run supplies no evidence for another Researcher prompt, iteration, token-budget, provider, or agent repair. It is not reported as a full quality pass because judging was unscorable, but the target provenance gates were interpretable and passed. The sequential loop is unblocked to begin Source Evaluator diagnosis while retaining this result as judge-infrastructure-blocked evidence.

## 42. Task 19 Source Evaluator Live Diagnosis — Sol High Review Pending

- Candidate: `ce3a706c2722e2b571262fc58f3a4ecb2a88a829`; exactly one Source Evaluator live repetition ran after the documented Researcher confirmation. The existing configuration and repository dotenv launcher were used without CLI overrides; no retry, other agent, suite command, or repair ran.
- Result: exit code `1`, status `FAILED`; `13/14` target hard gates passed and deterministic quality was `0.80`. The only failed gate was `low_confidence_flagged`.
- Independent judge boundary: the judge was unscorable with typed `judge_schema_failure` diagnostics at `$` on attempt 1 and `$` plus `rationale` on attempt 2. No target-side typed `output_limit` appeared, so no token-budget change is authorized.
- Evidence: LangSmith experiment `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/71a8f884-597c-4902-b1f2-b4d8f1722a68`; artifact `output/evaluations/task19-source-evaluator-ce3a706/source-evaluator/task19-source-evaluator-ce3a706-source-evaluator-live-20260911T012237Z-ce3a706/results.json`; SHA-256 `C90E38811DD0BAF616A9A9B409B14E8E6AFC4412952AA9BE7499C2CFA2DA74F9`.
- Next gate: send the current remote branch, this report, the Source Evaluator case/gate implementation, and the typed artifact finding to Sol High in the existing browser session. Do not implement a Source Evaluator repair until Sol distinguishes a target-agent defect from an evaluator/case mismatch or live-environment signal. Keep the judge failure separate and preserve the global `llm.max_tokens == 4096` cap.

## 43. Task 19 Source Evaluator Sol High Diagnosis — Frozen Case No-Change Ruling

- Review surface: existing Sol High browser conversation, task-scoped at remote HEAD `ecf2b98`, whose parent is the live-run candidate `ce3a706`. No whole-branch review, code change, live call, credential, raw provider output, prompt, or ignored `.deepseek-runs/` content was used.
- Verdict: `PASS WITH FOLLOW-UP`; no Critical findings. The evaluator contract is aligned: production stamps `ScoredSource.low_confidence` from `overall_score < 0.40`, and the gate reads the same typed field. The judge's `$`/`rationale` schema failures remain an independent unscorable boundary.
- Important classification: `low_confidence_flagged` is a frozen case/expectation mismatch. The live case places all four sources under the same subtopic, so the designated weak source receives deterministic corroboration `1.0` and a `0.20` score floor, while the case expects an absolute low-confidence flag requiring total score below `0.40`. The controlled analogue gives the expected-low-confidence source its own subtopic and does not receive that floor. The same miss repeated in the earlier live waves and at `ce3a706`.
- No-change ruling: no Source Evaluator production, prompt, scoring, threshold, weight, case, rubric, provider, iteration, budget, or fallback repair is authorized. If the case is ever unfrozen, its topology/expectation requires a separate evaluation-design decision; it must not be “fixed” by tuning the agent.
- Next gate: exactly one focused Source Evaluator confirmation is safe. A repeated `low_confidence_flagged` result is expected frozen-contract evidence, not new agent-defect evidence; any judge failure remains infrastructure-blocked and cannot receive a fabricated score.

## 44. Task 19 Source Evaluator Focused Confirmation — Frozen Mismatch Reproduced

- Candidate and scope: `424ed6ca948c41b1bda8d6152721d42060139cf3`; exactly one focused Source Evaluator live repetition ran after the Sol-reviewed no-change ruling. Frozen configuration and a fresh namespace were used; no code, case, prompt, budget, evaluator, retry, other agent, or suite command ran.
- Result: exit code `1`, status `FAILED`; `13/14` hard gates passed, deterministic quality `0.80`, and only `low_confidence_flagged` failed. The judge was scorable on this confirmation with judge `0.77` and aggregate `0.78`.
- Evidence: LangSmith experiment `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/231a04d4-dfc8-434d-8403-8c8e18cc71c8`; artifact `output/evaluations/task19-source-evaluator-confirmation-424ed6c/source-evaluator/task19-source-evaluator-confirmation-424ed6c-source-evaluator-live-20260911T013412Z-424ed6c/results.json`; SHA-256 `68B8CACA796C6C7FF9B48B69DA9A05E8F954B59412167DE032655A1CEFE26BE7`.
- Decision: the independently scorable confirmation reproduces the same deterministic gate miss, confirming the frozen-case topology/expectation mismatch rather than a Source Evaluator implementation defect. Source Evaluator is complete for this sequential loop with no production change; Fact Checker is the next agent. The global `llm.max_tokens == 4096` and all judge/no-score and no-budget invariants remain unchanged.

## 45. Task 19 Fact Checker Live Evidence — Preserved While Sequence Paused

- Candidate: `ef143ef9660a524d26dc729d9eadae4f6cf4c296`; exactly one Fact Checker live repetition ran under the frozen configuration before the production-readiness correction paused the sequence. No retry, code, case, prompt, budget, evaluator, or suite change ran.
- Result: exit code `1`, status `FAILED`; all `15/15` target hard gates passed and deterministic quality was `1.00`. The judge score was `0.48`, aggregate `0.69`, below the configured quality threshold; no target hard gate or target-side typed `output_limit` failure appeared.
- Evidence: LangSmith experiment `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/18a62aee-ba09-4a79-9b38-890d1afbc490`; artifact `output/evaluations/task19-fact-checker-ef143ef/fact-checker/task19-fact-checker-ef143ef-fact-checker-live-20260911T013600Z-ef143ef/results.json`; SHA-256 `0059D095129C67F57C8609662B1976797C54A55B586F6E4F44DBD7EBFA9D5199`.
- Disposition: preserve this evidence, but do not treat it as sequential advancement or production readiness. Fact Checker's agent-specific diagnosis is deferred until the Source Evaluator readiness issue is resolved.

## 46. Production-Readiness Correction — Source Evaluator Must Be Reopened

- User requirement: the campaign objective is production fitness with coherent, good metrics and review readiness, not merely avoidance of an unsupported agent-code change.
- Consequence: the prior Source Evaluator no-change ruling is not a completion decision. The repeated `low_confidence_flagged` result is an evaluation-contract/production-readiness blocker until an approved decision reconciles the live-case topology/expected flag with the production scoring geometry and a clean focused confirmation is obtained.
- Sequence control: Fact Checker evidence was already collected and preserved, but no later agent run starts. The current branch is not production-ready and must not be presented for final review.
- Invariants: do not silently alter Source Evaluator code, prompt, thresholds, weights, cases, rubrics, budgets, provider behavior, or judge semantics. Obtain the next scoped plan/review first; keep `llm.max_tokens == 4096` and all raw-URL/secret-safe evidence boundaries unchanged.

## 47. Sol High Production-Readiness Plan — Source Evaluator Live Case v2

- Review surface: the existing Sol High browser conversation, task-scoped against remote HEAD `8e0e0fb`, with the Source Evaluator v1 live and confirmation artifacts, the production scoring path, and the case/test sources. No whole-branch review, live call, provider call, credential, raw prompt/response, evaluator input, or ignored `.deepseek-runs/` content was used.
- Verdict: `NOT READY`, with a bounded repair path. The controlled Source Evaluator baseline and confirmation are green; the live failure is a v1 fixture-topology mismatch. All four findings share one generic subtopic, so the weak/forum finding receives corroboration `1.0` and cannot satisfy the unchanged low-confidence threshold geometry. Candidate `424ed6c` remains immutable v1 evidence and is not a production-readiness pass.
- Approved route: add RED coverage in `tests/test_evaluation/test_cases_source_evaluator.py` for live case version `2`, zero corroboration for the expected low-confidence URL, and positive corroboration for authoritative URLs. Then change only `_LIVE` in `src/deep_research/evaluation/cases/source_evaluator.py`: assign the forum/anecdotal finding a distinct meaningful subtopic and set `version=2`; keep URL partitions and expectations unchanged.
- Preserved invariants: no Source Evaluator agent/prompt/scoring/threshold/weight/evaluator/fallback/provider/budget change; no controlled-case mutation; v1 artifacts remain immutable; `llm.max_tokens == 4096`; no judge/provider or target-budget change; no suite/live command before offline verification and Sol High task review.
- Review gate: run focused and neighboring offline tests, the source-first full offline suite, Ruff, and `git diff --check`; push the candidate; obtain a scoped Sol High review; then allow exactly one fresh Source Evaluator v2 repetition. Review-ready acceptance is case version `2`, `14/14` hard gates, deterministic quality `1.00`, scored judge with no diagnostics, aggregate `>= 0.75`, no target/judge provider failure or fallback, zero prohibited calls, and runner status `REVIEW REQUIRED`. A v2 failure is diagnosed once without retry or threshold lowering.
- Sequence state: Fact Checker evidence from `ef143ef` stays preserved and deferred. No later agent run is authorized until the Source Evaluator v2 gate is satisfied.

## 48. Sol High Review — Source Evaluator v2 Repair Approved for One Confirmation

- Review surface: existing Sol High browser conversation, task-scoped against remote `9ae93f544a9c614dcdf4cac89ac4a54242eaf369`, exact range `081e3bf..9ae93f5`. Sol reviewed the repository branch and pasted diff; no whole-branch review, live/provider call, credential, raw prompt/response, evaluator input, or ignored `.deepseek-runs/` content was used.
- Verdict: `PASS WITH FOLLOW-UP`; no Critical or Important findings. The v2 live case version and forum-only subtopic correction match the approved production-readiness route, and the new tests exercise the real grouping/corroboration helpers with an authoritative positive-corroboration control.
- Invariants confirmed: expected URL partitions, metric IDs/weights, live scenario, controlled cases, dataset/scenario contracts, Source Evaluator production code, evaluator gate, thresholds, budgets, provider/fallback behavior, and global `llm.max_tokens == 4096` remain unchanged.
- Minor follow-up: this append-only bookkeeping record and the plan status update are required before the live call. No implementation change is required.
- Authorization: exactly one focused Source Evaluator v2 live confirmation is unblocked. Acceptance is case version `2`, `14/14` hard gates, deterministic `1.00`, scored judge with no diagnostics, aggregate `>= 0.75`, no target/judge/provider failure or fallback, zero prohibited calls, and runner status `REVIEW REQUIRED`. Do not retry, lower thresholds, run a suite, run another agent, or tune prompts/budgets/providers. Fact Checker remains deferred.

## 49. Task 19A V2 Live Preflight Blocker — Dataset Update ID Omission

- Authorized command scope: exactly one Source Evaluator v2 live confirmation using the reviewed case repair. The secret-safe launcher loaded the repository environment without printing it; no retry or other agent ran.
- Result: exit `1` before target/judge execution with `dataset synchronization failed: dataset_unavailable`. No target output, judge verdict, evaluation result artifact, quality metric, or target/provider failure evidence was produced.
- Root cause: the v2 case identity caused the existing remote v1 dataset example to enter `synchronize_dataset`'s update path. The real LangSmith `Client.update_examples` contract requires each `ExampleUpdate` payload to include the existing example `id`, but the implementation forwarded `example_payload(case)` without that field. The permissive offline fake recorded the update without validating the ID, so the existing dataset-version test did not expose the production contract.
- Classification: evaluation-harness/LangSmith dataset synchronization integration defect, not Source Evaluator quality evidence and not target-side output-limit evidence. The case v2 repair, production scoring, gates, thresholds, weights, budgets, provider/fallback behavior, and global `llm.max_tokens == 4096` remain unchanged.
- Required TDD fix: add a strict fake-driven regression for the remote-example ID in update payloads, verify RED against the current implementation, add the smallest dataset-sync repair, run the focused/full offline evaluation gate and lint, then obtain a fresh scoped Sol High review. Preserve all v1 artifacts and do not retry the paid v2 command until the review is clean.

## 50. Sol High Fix Round 1 Re-review — Dataset Identity Still Missing

- Review surface: existing Sol High browser conversation, remote branch
  `codex/cross-agent-planner-fix-parity`, exact range `82a951c..c9b7062`.
- Verdict: `NOT READY`; the single Source Evaluator v2 paid confirmation remains
  paused. Fix Round 1 closes the missing existing-example-ID layer, but the
  real LangSmith update contract also requires dataset identity when
  structured `updates` are supplied.
- Verified detail: the installed `Client.update_examples` signature accepts
  `dataset_name`/`dataset_id` and `updates`. The current production call sends
  only `updates=to_update`; the update objects include the remote example `id`
  but not `dataset_id`. The SDK can therefore reject the call locally before
  any target or judge execution.
- Classification: evaluation-harness/LangSmith dataset synchronization
  integration defect, not Source Evaluator quality evidence and not target-side
  output-limit evidence. No agent, prompt, scoring, gate, threshold, weight,
  budget, provider, fallback, case, or global `llm.max_tokens == 4096` change is
  justified.
- Fix Round 2 requirement: add a RED dataset-identity regression, make the
  strict fake validate the existing dataset ID in addition to the existing
  example ID, pass `dataset_id=dataset.id` (or an equivalent contract-preserving
  identity) in the smallest production change, run the focused/neighboring and
  source-first offline gates plus Ruff and diff check, commit/push, and obtain
  a fresh scoped Sol High review.
- Evidence preserved: the original preflight failure, Fix Round 1 commit
  `c9b7062`, its offline counts, and all v1/v2 artifacts remain immutable. No
  paid retry or later-agent run is authorized until Fix Round 2 is reviewed.

## 51. Sol High Fix Round 2 Review — PASS; One V2 Confirmation Unblocked

- Review surface: existing Sol High browser conversation, remote implementation
  range `c9b70621ec7dea2887cee3fd347d5eb9e65aefd6..4c4e6ce62f9ef3bfff20e3decf5975d08dffa34f`,
  bookkeeping HEAD `6d6bf1ce57fd54c2fd8a1c82507859237bc932c9`.
- Verdict: spec compliance `PASS`; task quality `PASS`; no Critical,
  Important, or Minor findings. The one-line production change supplies
  `dataset_id=dataset.id` to `update_examples` while retaining Fix Round 1's
  remote example ID in every structured update.
- Sol verified the strict fake rejects missing/mismatched dataset identity and
  unknown example IDs, and the regression proves the existing dataset ID,
  existing example ID, and revised `metadata.case_version == 2`. Create/reuse,
  secret scanning, version selection, and no-deletion behavior remain intact.
- Disposition: the dataset-sync blocker is closed. Exactly one fresh Source
  Evaluator v2 live confirmation is unblocked from the scoped review gate.
  Production readiness is still unproven until the live artifact satisfies
  version `2`, `14/14` hard gates, deterministic `1.00`, scored judge with no
  diagnostics, aggregate `>= 0.75`, no target/judge/provider failure or
  fallback, zero prohibited calls, no target-side output-limit evidence, and
  runner status `REVIEW REQUIRED`.
- Invariants: no Source Evaluator code/prompt/scoring/gate/threshold/weight/
  budget/provider/fallback/judge/case change; global `llm.max_tokens == 4096`;
  no retry, suite, or later-agent run. Earlier preflight and v1/v2 artifacts
  remain immutable.

## 52. Source Evaluator v2 Confirmation — Target Green, Shared Judge Schema Failure

- Command scope: exactly one fresh Source Evaluator v2 live confirmation after
  the reviewed dataset-sync repair. The secret-safe launcher loaded the
  repository environment without printing it. No retry or later-agent run was
  executed.
- Artifact:
  `output/evaluations/task19-source-evaluator-readiness-v2-3eab969/source-evaluator/task19-source-evaluator-readiness-v2-3eab969-source-evaluator-source-evaluator-live-20260911T024444Z-3eab969/results.json`
- SHA-256:
  `F9F8638E831D4FA3E8B9E137B397ED3F4D5BD84427883A075742FE627FCE3278`
- LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/2858d948-6ed8-46ef-b505-496755656d9d`;
  repetition review:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/2858d948-6ed8-46ef-b505-496755656d9d/r/01a08e5a-83f5-7c80-b593-9f154a76ce6d?poll=true`.
- Target result: case `source-evaluator-live-ranking`, version `2`, `1/1`
  completed; `14/14` hard gates passed; deterministic quality `1.00`; all four
  deterministic metrics were `1.00`; prohibited-call count `0`; target errors
  empty; no target-side typed `output_limit` or fallback diagnostic.
- Judge result: `judge_not_run` with `judge_schema_failure`; typed diagnostics
  were `schema_output`, attempt `1`, field `$`, and `schema_output`, attempt
  `2`, field `rationale`. No judge score or aggregate quality was fabricated.
- Runner result: `INFRASTRUCTURE FAILURE`. Classification is shared judge
  structured-output infrastructure, not Source Evaluator target quality and
  not target-side output-limit evidence. The target repair is supported by the
  live metrics, but production/review readiness is not established without a
  scored judge aggregate.
- Disposition: preserve the sole v2 evidence; do not retry, tune Source
  Evaluator, change budgets/thresholds/weights/cases, run Fact Checker, or run
  a suite. Next action is a typed judge-boundary diagnosis using the existing
  `$`/`rationale` field paths and the prior judge evidence.

## 53. Sol High Judge-Boundary Diagnosis — NO CHANGE; Campaign Paused

- Review surface: existing Sol High browser conversation at remote HEAD
  `41707d3`, using the typed Source Evaluator v2 artifact and the current
  judge/provider boundary. No live command, code modification, or whole-branch
  review was performed.
- Diagnosis: recurring shared judge structured-output/provider instability; no
  deterministic defect in Source Evaluator or judge code is established. The
  typed fields `$` and `rationale` do not reveal the unseen invalid provider
  output, and prior offline tests accept valid `JudgeVerdict` values including
  a rationale. A RED test for a guessed field violation would be invalid.
- Repair decision: `NO CHANGE`. Do not loosen or remove rationale constraints,
  alter JSON parsing, add another structured repair, change provider/retry or
  reasoning behavior, or raise the global `4096` cap without stronger typed
  evidence identifying one repeatable concrete contract failure.
- Readiness: Source Evaluator target side `GREEN`; overall production/review
  readiness `NOT READY` because judge status is `judge_not_run`, aggregate
  quality is unavailable, and runner status is `INFRASTRUCTURE FAILURE`.
- Sequence disposition: preserve the sole v2 confirmation; no Source
  Evaluator retry, Fact Checker run, or suite run. The campaign remains paused.

## 54. Fact Checker Live Evidence — Target Green; Judge Quality Below Threshold

- Command scope: exactly one fresh Fact Checker live repetition, explicitly
  authorized by the user while Sol High reviewed the shared judge plan. It ran
  against remote `30d9921` with the existing config and secret-safe launcher,
  no CLI effort/budget override, no code change, and no retry.
- Artifact:
  `output/evaluations/task21-fact-checker-readiness-30d9921/fact-checker/task21-fact-checker-readiness-30d9921-fact-checker-fact-checker-live-20260911T030226Z-30d9921/results.json`
- SHA-256:
  `CA5F682E801CAAF64D04A7BD15229B4EBB517E6860E0034CE36F804D8DFD8809`
- LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/ca1ecf5b-3517-46f9-897a-d44d8a7d54c5`;
  repetition review:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/ca1ecf5b-3517-46f9-897a-d44d8a7d54c5/r/01a08e6a-b353-7402-a8c3-53c3f802f4db?poll=true`.
- Target result: case `fact-checker-live-verification`, version `1`, `1/1`
  completed; `15/15` hard gates passed; deterministic quality `1.00`; all
  deterministic metrics `1.00`; prohibited calls `0`; target errors empty.
- Fallback result: typed `fallback_provider_diagnostic.kind=output_limit`,
  operation `react_decision`. It is fallback evidence, not a top-level target
  failure and not sufficient by itself for a new token-budget field.
- Quality result: judge scored `0.3675`; aggregate quality `0.6205`; runner
  `FAILED` against the configured threshold. Judge status was `scored`; no
  judge schema/transport failure occurred.
- Classification/disposition: target contract green, Fact Checker quality
  below threshold. Preserve the artifact and require Sol High diagnosis before
  any Fact Checker prompt/behavior/budget change, retry, or later-agent run.

## 55. Sol High Parallel Repair Plan — Four Disjoint Offline Streams

- Review surface: existing Sol High browser conversation; current verified
  remote branch tip at the time of planning was `c704ac9`. No live/provider
  command, repository modification by Sol, or whole-branch review was
  performed.
- Evidence disposition: all six registered agents already have live evidence;
  Planner is a passing control, Researcher and Source Evaluator are target
  green but judge-blocked, Fact Checker is target-contract green with a
  scorable below-threshold judge and a fallback `react_decision`
  `output_limit`, Synthesizer is deterministic `0.75) with a separate
  judge failure, and Critic is deterministic/judge below threshold with no
  fallback in the latest rerun. No new live run is authorized before the
  repair wave.
- Approved Stream J ownership:
  `src/deep_research/providers/deepseek_provider.py`,
  `tests/test_deepseek_provider.py), and
  `tests/test_evaluation/test_judging.py). Add RED tests for typed
  `extra_forbidden/$) and `string_bounds/rationale), implement only static
  category-aware repair guidance, preserve one repair attempt, no fabricated
  scores, secret-safe diagnostics, and `llm.max_tokens=4096).
- Diagnosis-only Stream F ownership:
  Fact Checker agent/case tests, with production editing allowed only if a
  typed target defect has a genuine local RED. The fallback remains separate
  from top-level target failure; no token/retry/prompt tuning.
- Diagnosis-only Stream S ownership:
  Synthesizer agent/case tests; identify the exact typed metric and reproduce
  it offline or stop with insufficient evidence. No production/evaluator
  change from `0.75) alone.
- Diagnosis-only Stream C ownership:
  Critic agent/case tests; identify the exact typed metric, keep historical
  `critic_report_review` fallback separate, and return any evaluator
  candidate without editing shared evaluators.
- Parallel safety: the four writable surfaces are disjoint; shared docs,
  `evaluators.py), `prompts.py), config, cases/rubrics/weights, and other
  provider files are coordinator-reserved. After task-scoped Luna Max reviews,
  the coordinator will integrate approved commits, run one consolidated
  offline gate, obtain a fresh scoped Sol High review, and only then consider
  one live confirmation per affected agent.

## 56. Parallel Repair Wave Results — 2026-09-10

- The four streams completed from coordinator base `69803d9` without live,
  provider, LangSmith, or suite commands.
- Stream J implemented the approved shared DeepSeek structured-output repair
  and its TDD coverage. The worker commit was `05a6bb7`; it is integrated on
  this branch as `b1cfb91`. Scoped verification recorded 122 passing tests,
  Ruff success, and a clean diff check. The global `4096` token cap,
  `extra="forbid"`, one structured repair, and no-score judge failure
  semantics remain unchanged.
- Stream F added a Fact Checker ReAct-fallback characterization test. Its
  worker commit `255c409` is integrated as `3f0931d`. The typed live evidence
  remains target-contract green (`15/15`, deterministic `1.00`); the
  `react_decision` `output_limit` is fallback evidence, not target-side
  output-limit evidence. No production or budget change is justified.
- Stream S added a Synthesizer coverage-input characterization test. Its
  worker commit `310d8c9` is integrated as `a70c817`. The test confirms all
  required live topics reach the model input, so the typed `coverage=0.0`
  result does not establish a target input defect. No production or evaluator
  change is justified.
- Stream C identified the exact typed metric `no_spurious_gaps=0.0`. The
  covered-evidence control returned `0.0` as expected; the expressly
  acknowledged unresolved-limitation control returned `0.0` instead of the
  expected `1.0`. This is a shared evaluator defect candidate caused by
  keyword-overlap rejection of a genuine actionable limitation. The worker
  did not edit `evaluators.py`; its intentional RED test remains diagnostic
  and is not integrated until the evaluator repair is separately reviewed.
- A worker-context issue was corrected during Stream C recovery: fresh
  worktrees contain tracked plans but not ignored live `results.json` files.
  The coordinator supplied the verified typed metric and exact repository
  paths; no live artifact or secret was copied into the branch.
- Next gate: perform the scoped Sol High review of the integrated J/F/S
  changes and C's evaluator finding, then implement only the approved shared
  evaluator repair, rerun the offline gate, and defer live confirmation until
  the resulting evidence is interpretable.

## 57. Scoped Sol High Review and Packaging Disposition — 2026-09-10

- Review base: integrated branch HEAD `77bfd3d8031379d8effa8b9027d5717b7a1c59a2`,
  equal to `origin/codex/cross-agent-planner-fix-parity` before this cleanup.
  Sol High returned **NOT READY as currently packaged** and reported no
  Critical finding.
- Packaging correction: the worker commits had force-added
  `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-j-report.md`
  and
  `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-fact-checker-report.md`
  even though `.superpowers/` is ignored scratch space. Both files were
  removed from the Git index with explicit `git rm --cached` paths; local
  ignored copies are retained, and no credentials or live artifacts were
  copied into the repository. Their durable conclusions are captured in this
  tracked fix log.
- Stream disposition: J's adaptive DeepSeek structured-repair implementation
  is technically ready within its approved boundary; F and S remain
  characterization-only with no justified production or budget change; C's
  `no_spurious_gaps=0.0` unresolved-limitation control is a supported shared
  evaluator defect candidate. The next implementation is one serialized
  Luna-Max TDD repair limited to `_no_spurious_gaps_passes` and the two C
  controls. No case/rubric/threshold/weight/prompt/tool/budget/route/provider
  changes are authorized.
- Verification ledger: the integrated J/F/S wave recorded `277 passed` in
  combined focused tests, `1,251 passed` in the evaluation/agent/provider
  contract gate, and `2,000 passed, 1 deselected` in the full offline suite;
  Ruff and `git diff --check` passed. The first focused attempt used a stale
  editable install from an unrelated worktree; reinstalling with
  `python -m pip install -e ".[dev]"` from this campaign checkout corrected
  import provenance before the passing rerun. Sol did not independently run
  these commands.
- Planning-reference correction: use
  `docs/superpowers/plans/2026-09-10-cross-agent-planner-fix-parity-parallel-repair.md`,
  `docs/superpowers/plans/2026-09-10-parallel-stream-judge-brief.md`, and the
  root Stream F/S/C briefs for future worktree dispatches. The current parent
  plan remains `docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md`.
- Gate: live/provider/LangSmith/suite runs remain **NO-GO** until the C
  evaluator repair is reviewed and the full offline gate is green. The global
  `llm.max_tokens == 4096` cap and all fallback/judge failure semantics remain
  unchanged.

## 58. Stream C Evaluator Fix Round 1 — 2026-09-10

- Sol High's scoped review of `ba7b756..c644d77` returned **NOT APPROVED**
  with no Critical finding. The Important finding was a material false
  negative: exact-only matching missed the spurious paraphrase
  `The report does not cover deployment at commercial scale.` for the
  `commercial-scale deployment` reference theme. Sol required one additional
  production-path control and normalized whole-theme matching.
- The existing C worker was reused in its prepared worktree, aligned to
  `c644d77`; no new worker fork was created. It added the paraphrase control,
  reproduced the expected RED (`1.0` instead of `0.0`), and implemented only
  `_no_spurious_gaps_passes` with casefolded alphanumeric tokenization,
  local stop-word removal, and an all-meaningful-theme-tokens containment
  check. The original covered-evidence and acknowledged-limitation controls
  remain unchanged and green.
- Worker commit: `3d5fa91e332147b6914b877d35ba8ebf00db7c8f`.
  Coordinator integration commit: `1443e00`.
  Changed paths are exactly:
  `src/deep_research/evaluation/evaluators.py` and
  `tests/test_evaluation/test_cases_critic.py`.
- Fresh coordinator verification: combined Critic tests `91 passed, 1
  warning`; Ruff passed on both changed files; `git diff --check` passed. No
  live/provider/LangSmith/suite command ran. The worker reported the same
  GREEN result (`3 passed, 52 deselected` for the controls and `91 passed`
  combined).
- Gate: push the integrated fix, obtain the scoped Sol High re-review, then
  run the consolidated offline gate. Live/provider/LangSmith/suite execution
  remains **NO-GO** until both are complete. The global `4096` token cap and
  all fallback/judge semantics remain unchanged.

## 59. Stream C Evaluator Fix Round 2 — 2026-09-10

- Sol High's scoped re-review of the normalized matcher confirmed that the
  ordinary commercial-deployment paraphrase was fixed, but found one remaining
  Important false positive: the natural limitation
  `Additional durability and long-term performance data under field exposure
  are needed.` was still scored `0.0` even though the report explicitly marks
  that theme as unresolved and accumulating. Sol required a fourth
  production-path control with expected score `1.0`; weakening the whole-theme
  matcher alone was not acceptable.
- The existing Stream C worker was reused again, aligned to `872610d`; no new
  worker fork or worktree was created. The worker added the exact regression,
  observed the required RED (`1 failed, 55 deselected`, returned `0.0`), then
  changed only `_no_spurious_gaps_passes` to use sentence-local report
  evidence. A full theme-token match is exempted only when the same report
  sentence contains explicit unresolved/insufficient-language markers. The
  canonical covered-evidence, acknowledged field-record, paraphrased
  commercial-deployment, and new full-theme durability controls are all green.
- Worker commit `ab65e410c66de4990c763f7f3e964db9b34fb84a` was integrated as
  `f711f67`. Changed paths remain exactly
  `src/deep_research/evaluation/evaluators.py` and
  `tests/test_evaluation/test_cases_critic.py`; frozen cases, reports, themes,
  rubrics, weights, thresholds, prompts, tools, budgets, routes, providers,
  fallback semantics, judge behavior, and the `4096` cap are unchanged.
- Worker verification: all four production-path controls `4 passed, 52
  deselected, 1 warning`; focused Critic tests `92 passed, 1 warning`; relevant
  evaluator tests `97 passed, 1 warning`; Ruff and `git diff --check` passed.
  No live/provider/LangSmith/suite command ran, and `.deepseek-runs/` was not
  staged.
- Gate: push `f711f67`, obtain a fresh scoped Sol High re-review of this fix
  round, then run the consolidated offline gate. Live/provider/LangSmith/
  suite execution remains **NO-GO** until both gates are complete.

## 60. Stream C Evaluator Fix Round 3 — 2026-09-10

- Sol High's scoped review of `872610d..c9f31ee` confirmed that the full-theme
  durability limitation was fixed, but found a new current-case Important
  false negative: the sentence containing covered `compressive strength
  standards` also contains an unrelated `outstanding question` marker for
  durability. The prior sentence-level exemption therefore accepted the
  spurious gap `The report does not cover compressive strength standards.`
  Sol required a production-path control expected to remain `0.0` and a
  below-sentence-granularity association; the review returned **NOT APPROVED**
  with no Critical finding. A one-token future-theme sensitivity was recorded
  as Minor and deferred.
- The existing Stream C worker was reused again, aligned to `c9f31ee`; no new
  worker fork or worktree was created. An initial focused run unexpectedly
  passed because the worker detected stale editable-install import provenance;
  it corrected the source binding before accepting the TDD checkpoint. The
  valid RED was `1 failed, 4 passed, 52 deselected, 1 warning`, with the new
  compressive-strength gap returning `1.0` instead of `0.0`.
- The worker changed only `_no_spurious_gaps_passes` and its production-path
  regression, splitting report sentences into comma/semicolon/colon-delimited
  clauses before associating unresolved markers with matched theme tokens. The
  canonical covered deployment, acknowledged field-record limitation,
  commercial paraphrase, full-theme durability limitation, and new
  compressive-strength controls are all green. Worker commit
  `10acab33021ec5e0839b83aee6a7c6358475b806` was integrated as `5763f8a`.
- Worker verification: five production-path controls `5 passed, 52
  deselected, 1 warning`; focused Critic tests `93 passed, 1 warning`; relevant
  evaluator tests `97 passed, 1 warning`; Ruff and `git diff --check` passed.
  No frozen case data, live/provider/LangSmith/suite command, or
  `.deepseek-runs/` staging occurred.
- Gate: push the documented `5763f8a` state, obtain a fresh scoped Sol High
  re-review, then run the consolidated offline gate. Live/provider/LangSmith/
  suite execution remains **NO-GO** until both gates are complete.

## 61. Stream C Scoped Re-Review Disposition — 2026-09-10

- Sol High reviewed the pushed range `c9f31ee..a08dbbd` and returned
  **PASS WITH FOLLOW-UP**. It confirmed that the clause-local association fixes
  the covered `compressive strength standards` false negative while preserving
  the valid full-theme durability limitation. The review found no Critical or
  Important findings.
- The only remaining finding is the previously deferred Minor sensitivity for
  a future reference theme with one meaningful token. None of the current six
  Critic themes has that shape, so Sol required no action before the
  consolidated offline gate.
- Sol confirmed the production-path regression architecture, the exact
  implementation scope in `5763f8a`, the documentation-only follow-up in
  `a08dbbd`, the frozen case/report/theme/rubric/weight/threshold/prompt/tool/
  budget/route/provider/fallback/judge invariants, and `llm.max_tokens: 4096`.
  The reported verification counts remain coordinator evidence; Sol did not
  independently execute them.
- Gate: proceed to the consolidated offline gate. Live/provider/LangSmith/
  suite execution remains **NO-GO** until that gate is green and any findings
  are handled.

## 62. Stream C Consolidated Offline Gate — 2026-09-10

- After the Sol High `PASS WITH FOLLOW-UP` disposition for `c9f31ee..a08dbbd`,
  the coordinator ran the required offline gate at `61a4126`. The targeted
  evaluation/agent/provider contract command passed `1,256` tests with one
  existing LangSmith deprecation warning. The full no-cache suite passed
  `2,005` tests with one deselected and two existing warnings. Repository-wide
  Ruff and `git diff --check` passed.
- The campaign worktree and remote both resolve to `61a4126`; the only
  untracked item remains the pre-existing ignored `.deepseek-runs/` directory,
  which was not inspected or staged.
- Parallel repair work is complete and no additional C worker is needed. The
  remaining live work follows the sequential Task 19 control plane: Source
  Evaluator v2 is the current checkpoint, and Fact Checker remains deferred
  until the Source Evaluator confirmation is accepted. Each paid command still
  requires its own immediate authorization; no live/provider/LangSmith/suite
  command ran in this gate.

## 63. Task 21 Critic Post-Fix Live Confirmation — 2026-09-10

- Exactly one Critic live repetition ran at candidate `c74a4f2` after the
  clause-local evaluator fix, with no retry, CLI effort override, budget change,
  or next-agent run. The first invocation stopped before any provider call
  because the worktree dotenv lookup did not expose credentials; a safe
  boolean-only check confirmed the main repository `.env` contained the needed
  variables, and the same command was rerun with that environment loaded
  in-process. No credential values were printed.
- Experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/03123327-2243-42bd-a53f-11c0137f67ec`
- Repetition review:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727656d9d/projects/p/03123327-2243-42bd-a53f-11c0137f67ec/r/01a08ef8-1579-7693-ac8f-7efb89ce13c6?poll=true`
- Artifact:
  `output/evaluations/critic/cross-agent-planner-fix-parity-critic-confirmation-c74a4f2-critic-live-20260911T053652Z-c74a4f2/results.json`
- Artifact SHA-256:
  `825D4612700A25E4711997663F3DC2F153E7BF9E75A6C9822D271F3947533DD4`
- Case/version/repetition: `critic-live-review` / `1` / `1 of 1`.
  Target evidence: `14/14` hard gates; prohibited calls `0`; target errors
  empty; deterministic quality `0.80`; `score_bounded=1.00`,
  `route_consistent=1.00`, `no_spurious_gaps=1.00`, and
  `rationale_present=0.00`. The repaired `no_spurious_gaps` behavior held in
  the live path.
- Judge evidence: status `judge_not_run`, reason `judge_schema_failure`;
  typed diagnostics were `schema_output` attempt `1`, category
  `string_bounds`, field `rationale`, and `schema_output` attempt `2`,
  category `extra_forbidden`, field `$`. Aggregate quality was unavailable.
  The preserved fallback diagnostic was typed `schema_output` for
  `critic_report_review`; it is not target-side output-limit evidence.
- Runner disposition: `INFRASTRUCTURE FAILURE`. The `rationale_present=0.00`
  target metric is a separate Critic-output diagnosis candidate and must not be
  conflated with the shared judge failure. No code or budget change is made
  until Sol High reviews the typed artifact; no retry or next paid agent run is
  authorized from this result alone.

## 64. Sol High Critic Live Diagnosis — Judge/Provider Only, No Change — 2026-09-10

- Sol High reviewed the typed Critic confirmation and returned
  **JUDGE/PROVIDER ONLY — NO-CHANGE**. The live `no_spurious_gaps=1.00` result
  independently confirms that the clause-local Stream C evaluator repair held;
  the unscorable judge cannot cause that target deterministic metric.
- `rationale_present=0.00` is not evidence of an empty Critique rationale or a
  `TargetOutput`/`state_update` serializer mismatch. The Critic's
  `critic_report_review` provider failure produced the intentional generic
  `provider_unavailable` fallback Critique, whose rationale is valid but not
  grounded in a concrete report feature. That is why the grounding metric is
  `0.00` while `score_bounded`, `route_consistent`, and `no_spurious_gaps` are
  `1.00`.
- The typed fallback diagnostic
  `{kind: schema_output, operation: critic_report_review}` is target-agent
  provider evidence, distinct from the later judge diagnostics (`rationale`
  then `$`). Neither event is target-side `output_limit` evidence. The compact
  `RepetitionResult.errors=[]` means no top-level target failure; it does not
  erase the typed agent-level fallback diagnostic. This wording distinction is
  recorded as a non-blocking documentation nuance.
- Sol found no Critical or Important finding requiring code, evaluator,
  serializer, prompt, case, threshold, weight, fallback, provider-budget, or
  `llm.max_tokens` change. The optional future characterization is offline-only:
  prove normal report-grounded Critique rationale scores `1.0` while the
  existing provider fallback scores `0.0`; no production edit is authorized
  unless that characterization contradicts the current contract.
- Disposition: do not retry Critic and do not start the next paid agent run
  from this artifact alone. Preserve the live artifact, keep the global
  `4096` cap, and treat the shared judge/provider boundary as the remaining
  campaign blocker.

## 65. Parallel Offline Follow-Up and Task 22 — 2026-09-10

- After the Critic live diagnosis, Sol High approved three independent offline
  streams: a typed judge/provider audit, a Critic rationale characterization,
  and a Fact Checker quality diagnosis. No stream inspected credentials or raw
  provider responses, launched live/provider/LangSmith/suite commands, changed
  production behavior, or modified the frozen evaluation contract.
- The judge audit at branch HEAD `7449606` grouped the safe diagnostics as
  mixed `schema_output` `extra_forbidden` at `$` and `string_bounds` at
  `rationale` across agents and attempts. The existing offline judge/provider
  tests passed (`19` judge tests and `103` provider tests), and no repeatable
  current-code contract defect was identified. Required evidence for any
  future judge/provider change remains a same-SHA typed reproduction or
  deterministic offline RED that names the exact contract mismatch. No judge
  schema, repair-count, prompt, retry, or token-budget change was made.
- The Critic/Fact Checker characterization passed its focused offline suite
  (`204 passed`). It confirmed the existing contract: normal report-grounded
  Critic rationale scores `rationale_present=1.0`; the intentional
  `provider_unavailable` fallback scores `rationale_present=0.0`; fallback
  `score_bounded`, `route_consistent`, and `no_spurious_gaps` remain `1.0`.
  Fact Checker remains target-contract green (`15/15`, deterministic `1.00`);
  its `react_decision` `output_limit` is fallback evidence, not target-budget
  authorization. No production or evaluator change was justified.
- Task 22 added one evaluator-level characterization test in
  `tests/test_evaluation/test_cases_critic.py`, using the registered
  `critic-live-review` metrics, flat Critique fields in `TargetOutput.result`,
  `state_update["critique"]`, the real `fallback_critique()` helper, and the
  typed `critic_report_review` diagnostic. The test passed with the expected
  `1.0/0.0` rationale distinction and all three fallback controls at `1.0`.
- Implementation commit: `726cfe0100640ddb66f74bb4d2c139977bf85977`, pushed to
  `origin/codex/cross-agent-planner-fix-parity`. Full verification passed:
  `125` focused tests, `2,006` offline tests with one deselection, Ruff, and
  `git diff --check`. Only the pre-existing ignored `.deepseek-runs/` remains
  untracked.
- Sol High scoped review of `1aef4e1..726cfe0`: spec compliance `PASS`, task
  quality `PASS`, no Critical/Important/Minor findings. Task 22 is complete.
  This characterization creates no reason for a paid confirmation; the judge
  boundary remains `NO-CHANGE` and the next live state remains `NO-GO` until a
  separate typed production defect is reproduced, repaired, and reviewed.
- Controller post-push verification initially hit an environment-only
  collection failure because the default editable install resolved
  `deep_research` from the unrelated `streamlit-ui-polish` worktree. The
  failure was not a repository result; rerunning with this campaign worktree's
  `src` explicitly first on `sys.path` passed `2,006` tests with one
  deselection and two existing dependency warnings. Ruff and `git diff --check`
  remained green; no source change was made.

## 66. Judge Observability Follow-Up — STOP / NO-CHANGE — 2026-09-10

- A fresh read-only Luna Max audit and Sol High architecture review examined
  whether the recurring judge `$`/`rationale` failures justified adding typed
  operation, schema, prompt, or configuration fields. No source, test, tracked
  documentation, budget, or live/provider state changed.
- Existing `JudgeFeedback` and its evaluator/runner projections already retain
  typed status and not-run reason, bounded diagnostics, prompt ID, rubric
  version, judge model, prompt fingerprint, judge-configuration fingerprint,
  and trace/source URLs. The prompt fingerprint includes the frozen
  `JudgeVerdict` JSON schema; target-agent operation attribution remains a
  separate fallback diagnostic boundary. The provider already records its
  static structured-output operation and bounded attempt telemetry.
- No second judge operation is interleaved with the current one, and no typed
  projection loss, fingerprint collision, or consumer/schema mismatch was
  reproduced. Adding duplicate telemetry would change the persisted v1 artifact
  contract without resolving the observed provider response instability.
- Offline evidence at `ca369b6`: judge tests `19 passed`, provider tests `103
  passed`, targeted Ruff passed, and `git diff --check` passed. No live,
  provider, LangSmith, suite, network, credential, raw-response, or
  `.deepseek-runs` access was used.
- Sol High decision: **A — STOP / NO-CHANGE**. Reopen only if a deterministic
  offline RED demonstrates a projection mismatch, missing/colliding fingerprint,
  indistinguishable real judge operations, or a typed consumer that cannot
  represent required safe context. A paid run is not a substitute for that
  evidence.

## 67. Task 7 Consolidated DeepSeek Judge Native-Schema Offline Gate — 2026-09-11

- Base SHA: `dedccd7c129288b9753bb29a7838b8d03f9372ef`.
- Candidate code HEAD verified: `e914c13030e49ca0678f6d2cd6ee2ab76ee57228` on
  `codex/cross-agent-planner-fix-parity`.
- Historical scope check returned exactly these changed paths:
  `docs/superpowers/plans/2026-09-11-deepseek-judge-native-schema-transport.md`,
  `docs/superpowers/specs/2026-09-11-deepseek-judge-native-schema-transport-design.md`,
  `src/deep_research/evaluation/cli.py`,
  `src/deep_research/evaluation/config.py`,
  `src/deep_research/evaluation/runner.py`,
  `src/deep_research/providers/__init__.py`,
  `src/deep_research/providers/deepseek_provider.py`,
  `src/deep_research/providers/factory.py`,
  `tests/test_deepseek_provider.py`,
  `tests/test_evaluation/test_cli.py`,
  `tests/test_evaluation/test_config.py`,
  `tests/test_evaluation/test_judging.py`,
  `tests/test_evaluation/test_suite.py`, and
  `tests/test_provider_factory.py`. No agent, case, evaluator metric,
  `judging.py`, `models.py`, prompt, or `config.yaml` path changed.
- Fresh focused provider/judge/wiring gate: `314 passed`, `1 warning`,
  `20.94s`.
- Fresh evaluation/agent/provider contract gate: `1,299 passed`, `1 warning`,
  `23.87s`.
- Fresh full offline pytest: `2,043 passed`, `1 deselected`, `2 warnings`,
  `30.23s`. Warnings were the existing LangSmith `ast.Str` deprecation and
  FastAPI/Starlette `httpx` deprecation.
- Static checks passed: `python -m ruff check .` returned `All checks passed!`;
  `git diff --check` returned clean. The frozen-invariant script returned
  `frozen judge invariants: OK` with `llm.max_tokens=4096`, the unchanged
  `JudgeVerdict` rationale bounds, and `additionalProperties=False`.
- No live-provider, provider-network, LangSmith, evaluation-suite, or paid
  command ran. No credentials, `.env` content, raw provider output, full
  prompt, hidden reasoning, or `.deepseek-runs/` artifact was accessed or
  changed.
- The target `DeepSeekChatProvider` remains unchanged in behavior and on its
  existing Chat Completions path. The native `json_schema` Responses transport
  is judge-only; the global `max_tokens=4096` cap and existing retry/repair
  semantics remain in force.
- For the same logical judge settings from `config.yaml`, the historical
  judge configuration fingerprint was `99234793b79f` and the candidate
  transport-aware fingerprint is `924caf47aa0d`, with
  `deepseek_responses_json_schema_v1` recorded as the transport identifier.
  `judge_prompt_fingerprint(rubric_version=1)` remains `93edb1729cbb`; the
  candidate contains no judging, model, or prompt-source change.

## 68. Task 8 DeepSeek Judge Native-Schema Transport Review — OFFLINE COMPLETE; LIVE NO-GO

- Reviewed implementation range: `dedccd7c129288b9753bb29a7838b8d03f9372ef..e914c13030e49ca0678f6d2cd6ee2ab76ee57228`.
- Reviewed implementation head: `e914c13030e49ca0678f6d2cd6ee2ab76ee57228`.
- Evidence commit: `8c6e655777ddc79d1f47c13206f178017891f3f2`.
- Offline gate counts: `314` focused tests; `1,299` evaluation/agent/provider
  contract tests; `2,043` full offline tests with `1` deselected.
- Stage 1 cumulative implementation review (Sol High): **GO**; no Critical or
  Important findings. The transport-only fingerprint regression coverage is
  the deferred Minor.
- Stage 2 fresh architecture/readiness review (Sol High): **PASS** for
  transport correctness, contract/failure-semantic preservation, target/judge
  isolation, provenance, and conditional readiness for one future Researcher
  canary; no Critical or Important findings. The same Minor remains deferred.
- Disposition: the judge native-schema transport repair is offline-complete and
  reviewed. Live state is **NO-GO pending separate explicit authorization**.
  Researcher is the preferred first future canary because its prior target
  gates and deterministic quality were green and its unresolved boundary was
  judge schema failure. This is eligibility only; no canary or provider request
  has run.
- No provider call, live/LangSmith/evaluation-suite run, paid canary, retry,
  token increase, or mass agent evaluation ran or is authorized by this step.
  No credentials or `.env` content was accessed, and no `.deepseek-runs`
  artifact was inspected, staged, changed, or removed.

## 69. Authorized Researcher Judge-Transport Canary — PASS; STOP BEFORE NEXT AGENT

- Authorization and scope: the user explicitly authorized exactly one paid
  canary after the Task 8 Sol High **PASS**. One Researcher live repetition ran
  at branch `codex/cross-agent-planner-fix-parity`; no retry, second agent,
  suite, prompt change, budget change, or provider change ran.
- Candidate provenance: artifact metadata records `c13dd9d2701bf7491522ad4ac88c86be08800438`.
  The executable tree is identical to the Sol-reviewed implementation
  `e914c13030e49ca0678f6d2cd6ee2ab76ee57228`; the intervening commit contains
  only the tracked plan/fix-log documentation. The tracked/index tree was
  clean. The artifact's safe `git_dirty=true` bit reflects the pre-existing
  untracked `.deepseek-runs/` runtime directory, which was preserved and not
  inspected or changed.
- Frozen configuration: target and judge `deepseek-v4-flash`; Researcher target
  effort `high`; judge effort `max`; one live repetition; concurrency `1`;
  `llm.max_tokens=4096`; Planner final cap `4096`; retry count `5` with
  `1.0` initial and `16.0` maximum delay. The repository dotenv launcher was
  used without recording any environment value.
- Result: runner exit `0`, status `REVIEW REQUIRED`, one case completed, and
  one repetition completed. All `14/14` Researcher target gates passed;
  deterministic quality was `1.00`; judge quality was `0.6825`; aggregate
  quality was `0.8095`; prohibited-call count was `0`; target errors and
  fallback provider diagnostics were empty.
- Judge acceptance: `judge.status=scored`, `judge.not_run_reason=null`, and
  `judge.diagnostics=[]`. The validated judge prompt fingerprint is
  `93edb1729cbb`, matching the pre-transport fingerprint; the native-schema
  judge configuration fingerprint is `924caf47aa0d`; the artifact records
  `deepseek_responses_json_schema_v1`. No target-side typed `output_limit`
  evidence was created by the judge transport.
- Immutable evidence: `output/evaluations/researcher/cross-agent-planner-fix-parity-judge-native-schema-researcher-canary-c13dd9d-researcher-live-20260911T185526Z-c13dd9d/results.json`; SHA-256
  `DD5C68C045C12B5388EF59959DD6818BED0C3C4109C741B0093C90F7E4DA2FB5`.
  LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/f4dbc66f-7486-4181-9220-432e3468e71a`.
- Disposition: the judge native-schema transport is empirically confirmed for
  this Researcher canary. Stop here as required by the one-call authorization;
  do not treat one repetition as universal agent production sign-off. The next
  step is a separately authorized, sequential Source Evaluator repetition and
  diagnosis only after preserving this evidence. No other live agent or suite
  run started in this step.

## 70. Authorized Source Evaluator Judge-Transport Canary — PASS; STOP BEFORE SYNTHESIZER

- Authorization and scope: the user requested sequential live evaluation of the
  remaining agents. Exactly one Source Evaluator live repetition ran after the
  Researcher canary, with no retry, parallel run, second agent, suite, prompt
  change, budget change, or provider change.
- Candidate provenance: `d902c579597d550f81b460982d614dbb0870b7d9`. The tracked
  and index tree was clean before the run; the safe artifact metadata's
  `git_dirty=true` reflects only the preserved, uninspected `.deepseek-runs/`
  runtime directory.
- Frozen configuration: target and judge `deepseek-v4-flash`; Source Evaluator
  target effort `high`; judge effort `max`; one live repetition; concurrency
  `1`; `llm.max_tokens=4096`; Planner final cap `4096`; retry count `5` with
  `1.0` initial and `16.0` maximum delay; native judge transport
  `deepseek_responses_json_schema_v1`.
- Result: runner exit `0`, status `REVIEW REQUIRED`, one case/version `2`
  completed, and one repetition completed. All `14/14` target gates passed;
  deterministic quality was `1.00`; judge quality was `0.88`; aggregate
  quality was `0.928`; prohibited-call count was `0`; target errors and
  fallback provider diagnostics were empty.
- Judge acceptance: `judge.status=scored`, `judge.not_run_reason=null`, and
  `judge.diagnostics=[]`. The validated judge prompt fingerprint was
  `93edb1729cbb`; the native-schema judge configuration fingerprint was
  `924caf47aa0d`. No target-side typed `output_limit` evidence appeared.
- Immutable evidence: `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-judge-native-schema-source-evaluator-canary-d902c57-source-evaluator-live-20260911T190947Z-d902c57/results.json`; SHA-256
  `F6A910D4F428F43B4FFC9164C1B989C4BE8B529D57C0D2FA7A7ADB909C3CEEF2`.
  LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/6d92638c-900d-4ac1-a184-2fd3e0c2d464`.
- Disposition: Source Evaluator passes this one-repetition judge-transport
  canary with no target repair indicated. Stop before Synthesizer; its paid
  command requires a separate immediate authorization under the campaign gate.

## 71. Authorized Synthesizer Judge-Transport Canary — PASS; STOP BEFORE CRITIC

- Authorization and scope: the user authorized exactly one Synthesizer paid
  live command. One live repetition ran sequentially after Source Evaluator;
  no retry, parallel run, second agent, suite, prompt change, budget change,
  or provider change ran.
- Candidate provenance: `e1b8ae5baa3cfd13cdb11448dd2e74eb7b661b7c`. The tracked
  and index tree was clean before the run; the safe artifact metadata's
  `git_dirty=true` reflects only the preserved, uninspected `.deepseek-runs/`
  runtime directory.
- Frozen configuration: target and judge `deepseek-v4-flash`; Synthesizer
  target effort `max`; judge effort `max`; one live repetition; concurrency
  `1`; `llm.max_tokens=4096`; Planner final cap `4096`; retry count `5` with
  `1.0` initial and `16.0` maximum delay; native judge transport
  `deepseek_responses_json_schema_v1`.
- Result: runner exit `0`, status `REVIEW REQUIRED`, one case and one
  repetition completed. All `15/15` target gates passed; deterministic quality
  was `0.75`; judge quality was `0.84`; aggregate quality was `0.804`;
  prohibited-call count was `0`; target errors and fallback provider
  diagnostics were empty. The deterministic metric map recorded
  `coverage=0.0`, `citations_known=1.0`, `limitations_present=1.0`,
  `persistence_truthful=1.0`, and `report_present=1.0`; this is recorded as
  evidence and does not by itself justify a production repair.
- Judge acceptance: `judge.status=scored`, `judge.not_run_reason=null`, and
  `judge.diagnostics=[]`. The validated judge prompt fingerprint was
  `93edb1729cbb`; the native-schema judge configuration fingerprint was
  `924caf47aa0d`. No target-side typed `output_limit` evidence appeared.
- Immutable evidence: `output/evaluations/synthesizer/cross-agent-planner-fix-parity-judge-native-schema-synthesizer-canary-e1b8ae5-synthesizer-live-20260911T191351Z-e1b8ae5/results.json`; SHA-256
  `D9C01DE6A6F5B91771FED39F710EEAF674D7BF885DC4694C267FC27239821516`.
  LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/166d9a5a-6503-49cf-9751-e23ef0b1d6b9`.
- Disposition: Synthesizer passes this one-repetition judge-transport canary;
  no target repair is opened from this evidence alone. Stop before Critic; its
  paid command requires a separate immediate authorization under the campaign
  gate.

## 72. Authorized Critic Canary — TARGET PROVIDER BOUNDARY; SOL HIGH DIAGNOSIS REQUIRED

- Authorization and scope: the user authorized exactly one Critic paid live
  command. One live repetition ran sequentially after Synthesizer; no retry,
  parallel run, second agent, suite, prompt change, budget change, or provider
  change ran.
- Candidate provenance: `2e8b25f5988ae222b74aee4d5272e5590cae2644`. The tracked
  and index tree was clean before the run; the safe artifact metadata's
  `git_dirty=true` reflects only the preserved, uninspected `.deepseek-runs/`
  runtime directory.
- Frozen configuration: target and judge `deepseek-v4-flash`; Critic target
  effort `max`; judge effort `max`; one live repetition; concurrency `1`;
  `llm.max_tokens=4096`; Planner final cap `4096`; retry count `5` with
  `1.0` initial and `16.0` maximum delay; native judge transport
  `deepseek_responses_json_schema_v1`.
- Result: runner exit `1`, status `FAILED`, one case and one repetition
  completed, and all `14/14` target hard gates passed. Deterministic quality was
  `0.80`; judge quality was `0.2525`; aggregate quality was `0.4715`; the case
  failed its quality threshold. The deterministic map recorded
  `no_spurious_gaps=1.0`, `rationale_present=0.0`, `route_consistent=1.0`, and
  `score_bounded=1.0`; prohibited-call count was `0` and target error list was
  empty.
- Target/provider boundary: the safe fallback projection is typed
  `{kind: schema_output, operation: critic_report_review}` and the ReAct stop
  reason is `provider_error`. This is target-side Critic report-review
  evidence, not a judge failure. The judge itself was scored with
  `judge.not_run_reason=null` and `judge.diagnostics=[]`; no target-side typed
  `output_limit` evidence appeared. Treat the target/provider classification as
  provisional pending Sol High diagnosis; do not infer that the model's
  rationale quality alone is an agent defect.
- Fingerprints: judge prompt `93edb1729cbb`; native-schema judge configuration
  `924caf47aa0d`; transport `deepseek_responses_json_schema_v1`.
- Immutable evidence: `output/evaluations/critic/cross-agent-planner-fix-parity-judge-native-schema-critic-canary-2e8b25f-critic-live-20260911T191641Z-2e8b25f/results.json`; SHA-256
  `7E441C4DAAD0DDE3B501A1CEE137D8ADA1611524BE9C79A74C3E254ACBE96E0C`.
  LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/c53343aa-6fb3-4918-be14-3e377c428b07`.
- Disposition: stop the sequential loop at Critic. Push this evidence, obtain
  a task-scoped Sol High diagnosis in the existing browser conversation, and
  only then decide whether a concrete offline RED justifies a minimal Critic
  repair. Fact Checker remains deferred until Critic diagnosis and any
  review/fix/no-change decision are complete.

## 73. Sol High Critic Diagnosis — EXPECTED FALLBACK; NO PRODUCTION CHANGE

- Review surface: the existing Sol High browser conversation reviewed the
  pushed Critic evidence at remote HEAD `c7a86d160618f7934c18229580fc572aacb775a2`.
  That commit is documentation-only over the live candidate
  `2e8b25f5988ae222b74aee4d5272e5590cae2644`; no post-canary production-code
  drift exists.
- Classification: **1 — expected Critic target/provider structured-output
  fallback**. This is not a judge failure, not a demonstrated Critic
  context/contract defect, and not an evaluator/artifact classification bug.
- Root cause supported by typed evidence: the target Critic report-review
  structured request exhausted its existing initial attempt plus one repair on
  the normal DeepSeek Chat structured-output path, surfaced a typed
  `StructuredOutputError`, and intentionally returned the `provider_unavailable`
  fallback. The safe operation/kind projection is therefore exactly
  `{operation=critic_report_review, kind=schema_output}`.
- Code evidence: `src/deep_research/agents/critic.py:228-266` defines the
  provider-unavailable fallback semantics; `critic.py:331-349` records the
  bounded typed provider snapshot; `critic.py:462-519` catches the typed
  provider error during review; and `critic.py:598-656` returns the fallback
  result with ReAct stop reason `provider_error`. The target provider's
  unchanged two-attempt structured path is `src/deep_research/providers/deepseek_provider.py:660-815`,
  with safe snapshot mapping at `src/deep_research/providers/contracts.py:179-271`.
- Artifact/evaluator evidence: `src/deep_research/evaluation/targets.py:548-589`
  preserves a completed fallback result separately from agent-level errors;
  `src/deep_research/evaluation/runner.py:489-548` projects only top-level
  `TargetOutput.failure` into repetition errors; and
  `src/deep_research/evaluation/evaluators.py:1336-1368` intentionally accepts
  `should_continue=false` for the typed Critic fallback. The empty compact
  repetition error list is therefore not loss of the typed fallback.
- Regression evidence: `tests/test_agents/test_critic.py:354-391` pins the
  planned spot-check context, `test_critic.py:517-545` pins provider-failure
  fallback/stop behavior, and `tests/test_evaluation/test_cases_critic.py:222-312`
  characterizes fallback `rationale_present=0.0` versus grounded rationale
  `1.0`, with fallback route/score/gap gates passing. This exactly matches the
  live vector `1.0 / 0.0 / 1.0 / 1.0`.
- Judge separation: the live judge was independently `scored` with
  `judge.not_run_reason=null` and empty diagnostics. Its low `0.2525` score
  measured the intentionally generic fallback critique; it did not cause the
  target fallback. No target-side `output_limit` evidence exists.
- Sol High ruling: **NO-CHANGE**. No production Critic repair or TDD plan is
  justified without a new deterministic offline RED proving a contract,
  context, fallback-routing, projection, or output-limit classification defect.
  Do not increase tokens, add retries, loosen `CritiqueDraft`, broaden prompt
  tuning, or change thresholds from this occurrence.
- Sequence disposition: Fact Checker remains deferred until this no-change
  ruling is recorded. It is the next sequential candidate after the ruling,
  but its live command still requires a separate explicit authorization.

## 74. Authorized Fact Checker Canary — JUDGE SCHEMA FAILURE; STOP FOR DIAGNOSIS

- Authorization and scope: the user authorized one Fact Checker live
  repetition after the Critic no-change ruling. Exactly one command ran;
  there was no retry, parallel run, second agent, suite, prompt change, budget
  change, or provider change.
- Candidate provenance: `a503a7bca8203f5539c9cc0cb61117f741698bf0`. The tracked
  and index tree was clean before the run; the safe artifact metadata's
  `git_dirty=true` reflects only the preserved, uninspected `.deepseek-runs/`
  runtime directory.
- Frozen configuration: target and judge `deepseek-v4-flash`; Fact Checker
  target effort `max`; judge effort `max`; one live repetition; concurrency
  `1`; `llm.max_tokens=4096`; Planner final cap `4096`; retry count `5` with
  `1.0` initial and `16.0` maximum delay; native judge transport
  `deepseek_responses_json_schema_v1`.
- Target result: runner completed one case/repetition with all `15/15` target
  hard gates passed, deterministic quality `1.00`, zero prohibited calls, and
  no compact target errors. The fallback projection is typed
  `{kind=output_limit, operation=react_decision}` with ReAct stop reason
  `provider_error`; this is fallback telemetry, not a top-level target
  failure. It is not evidence for a target budget increase.
- Judge result: runner exit `3`, status `INFRASTRUCTURE FAILURE`,
  `judge.status=judge_not_run`, `judge.not_run_reason=judge_schema_failure`,
  and `judge_quality=null`/`aggregate_quality=null`. Typed judge diagnostics
  were `schema_output` at field `rationale` on attempts `1` and `2`. The judge
  prompt fingerprint remained `93edb1729cbb`; native-schema judge
  configuration remained `924caf47aa0d`; no target-side output-limit evidence
  was created by the judge transport.
- Immutable evidence: `output/evaluations/fact-checker/cross-agent-planner-fix-parity-judge-native-schema-fact-checker-canary-a503a7b-fact-checker-live-20260911T193525Z-a503a7b/results.json`; SHA-256
  `53D77D8D3722DAD4F5F203F69E986D9C1C23D83DC404C791A0A0FFD67F38A20`.
  LangSmith experiment:
  `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/73585ecd-8099-4d44-8bec-e20dbd126b67`.
- Disposition: stop after this repetition. The judge failure is independent
  of Fact Checker target quality and requires typed diagnosis before any
  quality conclusion or repair. Do not retry, increase tokens, or run another
  agent. Consult Sol High in the existing browser conversation and preserve
  the exact `rationale` field paths and attempts.

## 75. Sol High Fact Checker Judge Diagnosis — NATIVE-SCHEMA BOUNDARY; NO CHANGE

- Review surface: the existing Sol High browser conversation reviewed the
  pushed Fact Checker evidence at remote HEAD `eaa572a29c8106edfcf41f1c9328a1fa68d79830`.
  This commit is documentation-only over live candidate
  `a503a7bca8203f5539c9cc0cb61117f741698bf0`; there is no post-canary
  production-code drift.
- Classification: **provider/native-schema response conformance failure at
  the judge boundary; insufficient evidence for a repository change**.
  The repeated typed path `rationale` localizes the failure but does not
  establish whether the value was missing, wrong-typed, outside the
  `1..2000` bounds, or invalid in another Pydantic-specific way.
- Code evidence: `src/deep_research/providers/deepseek_provider.py:825-922`
  normalizes a completed Responses result, extracts `output_text`, sends the
  requested native JSON schema, and performs authoritative local validation;
  the status mapping is at `deepseek_provider.py:367-381`, schema request at
  `deepseek_provider.py:846-861`, and `JudgeVerdict.rationale` bounds are at
  `src/deep_research/evaluation/models.py:476-481`. The evidence supports a
  completed provider response rejected at local validation, but does not prove
  a DeepSeek implementation defect or extraction corruption.
- Harness evidence: `src/deep_research/evaluation/judging.py:288-433` maps
  typed `StructuredOutputError` to `judge_schema_failure` without a verdict;
  `src/deep_research/evaluation/failure_taxonomy.py:45-157` safely projects
  bounded diagnostics; and `src/deep_research/evaluation/runner.py:489-525`
  plus `runner.py:640-704` leaves quality unavailable and classifies the
  unscorable repetition as infrastructure failure. The observed
  `judge_not_run`, two `rationale` diagnostics, null judge/aggregate quality,
  and no fabricated score are therefore the intended contract result.
- Synthetic test decision: the repeated field path does **not** justify a
  production RED. Existing tests already cover native schema submission,
  local validation, one repair, and typed exhaustion in
  `tests/test_deepseek_provider.py:347-597`, plus the judge integration
  projection in `tests/test_evaluation/test_judging.py:220-332`. If an
  additional characterization is desired, it may inject a typed
  `StructuredOutputError` with two diagnostics whose field paths are exactly
  `("rationale",)` and leave diagnostic categories unset; that should confirm
  the existing scoreless boundary, not change production code.
- Fact Checker target decision: **no target repair and no token increase**.
  The live `{kind=output_limit, operation=react_decision}` is separate
  fallback telemetry. `src/deep_research/agents/react.py:69-281` and
  `src/deep_research/agents/fact_checker.py:799-849` intentionally preserve
  the conservative non-propagating fallback; the direct regression at
  `tests/test_agents/test_fact_checker.py:976-1045` pins cap `4096` and no
  operation-specific token override. The target/judge adapters remain
  separate (`src/deep_research/providers/factory.py:31-74`).
- Sol High ruling: **NO-CHANGE; Fact Checker quality remains UNSCORABLE**.
  Do not retry this paid repetition, fabricate a score, loosen `JudgeVerdict`,
  add retries, broaden prompts, or increase any token budget. Reopen only if
  new safe evidence demonstrates a concrete mishandled contract, extraction,
  repair, diagnostic-projection, or score-fabrication defect.
- Frozen invariants retained: `llm.max_tokens=4096`, repository retry values
  from `config.yaml:13-22`, exactly one structured repair in
  `deepseek_provider.py:925-1017`, native judge transport and fingerprints,
  frozen cases/rubrics/thresholds/weights, scoreless judge failures, and the
  typed target fallback taxonomy. Sol High performed read-only diagnosis only;
  no tests, provider calls, LangSmith calls, live evaluation, or suite run was
  executed during the review.

## 76. Sol High Synthesizer Coverage Diagnosis — NO-CHANGE — 2026-09-11

- Diagnosis: the typed Synthesizer deterministic metric was `coverage=0.0`.
  Its exact definition is `Every subtopic title appears in the report body.`
  The real model message input contains the required context: the updated
  characterization calls `SynthesizerAgent.build_task(live_case.state)`, passes
  the resulting task to `report_messages()`, and confirms every live subtopic
  title is present while `task.findings` equals the state's raw findings.
- Frozen-case characterization: the non-empty set of declared live subtopic
  titles equals the non-empty set of `finding.related_sub_topic` values. This
  proves the frozen live case carries evidence for every declared subtopic;
  case data was not changed.
- Safe-artifact limit: the available safe artifact cannot distinguish lexical
  coverage, based on literal title matching, from semantic coverage, where the
  report addresses the topic without repeating its exact title.
- Judge result: the judge was clean and scored (`judge.status=scored`,
  `judge_quality=0.84`, empty diagnostics). Sol High's final ruling is
  **NO-CHANGE** because the zero coverage observation is real, but required
  context is present and no production defect is demonstrated.
- Offline verification, in order: `python -m pytest
  tests/test_agents/test_synthesizer.py -q` — passed; `python -m pytest
  tests/test_evaluation/test_cases_synthesizer.py -q` — passed; `python -m
  pytest tests/test_agents/test_synthesizer.py
  tests/test_evaluation/test_cases_synthesizer.py -q` — passed; `python -m
  ruff check src/deep_research/agents/synthesizer.py
  tests/test_agents/test_synthesizer.py
  tests/test_evaluation/test_cases_synthesizer.py` — passed; `git diff
  --check` — passed. Exact counts and warning output are recorded in the
  task report.
- Changed paths: `tests/test_agents/test_synthesizer.py`,
  `tests/test_evaluation/test_cases_synthesizer.py`,
  `docs/superpowers/2026-09-11-deepseek-judge-native-schema-synthesizer-canary.md`,
  and this fix log.
- No live rerun, prompt change, evaluator change, token change, provider call,
  LangSmith call, suite command, or production repair occurred. The existing
  `.deepseek-runs/` directory was not inspected or modified.

## 77. Critic Live-Call Production Readiness — ROOT CAUSE FOUND; OFFLINE GATE PASSED

Spec: `docs/superpowers/specs/2026-09-11-critic-live-call-production-readiness-design.md`.
Plan: `docs/superpowers/plans/2026-09-11-critic-live-call-production-readiness.md`.
Tasks 1-6 of that plan are complete. Task 7, the paid live canary, has **not**
run and remains separately gated.

### Root cause of the live Critic failure

Section 73's NO-CHANGE ruling was reached on the artifact's evidence. A direct
offline reproduction shows the artifact was missing the decisive evidence, and
that the Critic carries a real defect.

- **Confirmed defect 1 — the spot-check loop never saw the report.**
  `render_react_messages` renders only `task.instruction` and `task.guidance`,
  and `CritiqueTask.report` was never copied into either. Measured against the
  registered `critic-live-review` case: the ReAct spot-check prompt was `1,199`
  characters and did **not** contain the `2,877`-character report, while the
  structured review prompt was `4,811` characters and did. The model was told
  to spot-check a report it had never been shown, which is why the failing
  trajectory opened with a `query_memory` call that returned nothing and then
  concluded no report text was in context. This is a Critic defect rather than
  project design: `ResearcherAgent.sub_topic_task` names its sub-topic and
  `FactCheckerAgent.claim_task` names its claim in their loop prompts, so the
  Critic was the only ReAct agent whose loop prompt omitted its own artifact.
- **Confirmed defect 2 — the artifact discarded the structured diagnostics.**
  `_fallback_provider_diagnostic` projected the snapshot onto
  `{kind, operation}` and dropped the bounded `attempt` / `category` /
  `field_paths` records. Direct execution proves those records were **already
  present** at `output.errors[].details.provider_failure.diagnostics`; they were
  lost only in the final projection, while the judge path already retained the
  same shape safely as `JudgeFeedback.diagnostics`. The evidence was never
  missing from the live run, only from the artifact that was reviewed.
- **Confirmed defect 3 — a degraded run was scored as a bad review.**
  `critique_actionable_passes` returns `True` whenever `should_continue` is not
  `True`, so the empty fallback passed that gate; with `score_bounded=1.0` and
  `route_consistent=1.0` the degraded run presented as `14/14` hard gates
  passed. The judge had no way to distinguish an honest fallback from a real
  judgement and scored the fallback's absent content as a critique that
  "delivers nothing".
- **Boundary not crossed.** The cause of the two `json_invalid` attempts is
  still **unproven**. Reasoning over the current artifact cannot separate a
  truncated completion from an empty one or from preamble text around otherwise
  valid JSON, and the spec deliberately authorizes **no** token, retry,
  repair-prompt, temperature, or budget change until the retained diagnostics
  make the cause attributable.

### Offline gate

- Full offline suite: `2,070` collected (`1` deselected), **`3 failed, 2,067
  passed`**, `2` warnings.
- **All three failures are pre-existing and unrelated.** Verified by restoring
  the session base commit `db89bdb` and re-running: that tree produced the same
  three failures and `2,051` passed. The three are
  `test_a_live_experiment_requests_one_repetition`,
  `test_the_ledger_records_real_services_for_a_live_run`, and
  `test_a_live_researcher_records_only_source_url_fingerprints`. The changes
  under this entry add `16` passing tests and introduce no new failure.
- `ruff check src tests` passed; `git diff --check` passed.
- `git diff db89bdb..HEAD -- config.yaml` is empty, so no token, retry,
  temperature, or threshold setting moved.

### Fingerprints

- Target prompt fingerprint (Critic): `242310dce29e` is superseded. The Critic
  module and the shared prompt library are hashed together, so this moved with
  defects 1 and 2's prompt text; the new value is `e984bf2d3d23`.
- Judge prompt fingerprint: `93edb1729cbb` is superseded and is now pinned by a
  test. The new value is **`77a0898f4267`**. Judge scores recorded before this
  change are **not** comparable with scores after it.
- Native-schema judge configuration fingerprint: **unchanged** at
  `924caf47aa0d`. Verified by source inspection — it hashes provider, structured
  transport, judge model, judge effort, judge temperature, thinking mode, and
  rubric version, none of which this work touched.

### Changed paths

`src/deep_research/agents/critic.py`, `src/deep_research/agents/prompts.py`,
`src/deep_research/evaluation/models.py`,
`src/deep_research/evaluation/runner.py`,
`src/deep_research/evaluation/judging.py`,
`tests/test_agents/test_critic.py`, `tests/test_evaluation/conftest.py`,
`tests/test_evaluation/test_config.py`,
`tests/test_evaluation/test_judging.py`,
`tests/test_evaluation/test_models.py`,
`tests/test_evaluation/test_runner.py`, plus this fix log.

### Commits

- `5237b8a`, `5df9eee`, `b47d90d`, `0a7c0ed`, `db89bdb` — spec, plan, and
  plan corrections.
- `16d2565` — render the report in the spot-check guidance.
- `9a89a24` — state that the report is already in the prompt.
- `47bf9e2` — retain structured diagnostics in the artifact.
- `e6a5ceb` — give the judge the provider fallback fact.
- `b8baba0` — pin the post-fallback judge fingerprints.

### Next action

Task 7 of the plan: one paid Critic live repetition on the frozen
configuration, sequentially, with a clean tree, behind separate immediate
authorization. If it fails again, the artifact's
`fallback_provider_diagnostic.diagnostics` now names whether no parseable JSON
object was produced at all (`category=json_invalid`, `field_paths=("$",)`) or
whether valid JSON arrived in the wrong shape (a named path), which is the
distinction the previous three diagnoses could not make.

## 78. DeepSeek Tool-Call Capability Gate — BLOCKED; FORCED TOOL CHOICE REJECTED UNDER THINKING

Date: 2026-09-11. Candidate: `6cf0839` on `codex/cross-agent-planner-fix-parity`.
Plan: `docs/superpowers/plans/2026-09-11-tool-call-structured-transport.md`.

### Scope and authorization

The user authorized exactly 15 DeepSeek chat-completion requests: five
`tool_choice` shapes across three unique effective request settings. No
LangSmith tracing, no Tavily or search, no application tool execution, no
evaluation harness, and no repetition. The probe is preserved at
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/probe.py`. A dry run
enumerated the inventory (**15** requests) and was reviewed before `--execute`
ran.

### Effective settings probed

| # | Model | Thinking | Effort | Budget |
| --- | --- | --- | --- | --- |
| 1 | `deepseek-v4-flash` | `enabled` | `high` | `32768` |
| 2 | `deepseek-v4-flash` | `disabled` (control) | `none` | `32768` |
| 3 | `deepseek-v4-flash` | `enabled` | `max` | `32768` |

The inventory deduplicated twelve agent-and-tier combinations to these three
because every agent resolves to the same model. Setting 3 is the Critic
evaluation's actual target effort, and setting 2 is the explicit disabled
control.

### Result matrix

| Setting | `function` (specific) | `"required"` | `"auto"` | omitted | `"none"` (control) |
| --- | --- | --- | --- | --- | --- |
| thinking `enabled`, `high` | **400** | **400** | OK, 1 call, valid | OK, 1 call, valid | OK, 0 calls |
| thinking `disabled` | OK, 1 call, valid | OK, 1 call, valid | OK, 1 call, valid | OK, 1 call, valid | OK, 0 calls |
| thinking `enabled`, `max` | **400** | **400** | OK, 1 call, valid | OK, 1 call, valid | OK, 0 calls |

- `function` and `required` failed with `BadRequestError`, HTTP `400`, and were
  classified `tool_choice_rejection: true` by the probe's local check.
- Every `auto` and `omitted` success returned `finish_reason=tool_calls` with
  exactly one correctly named `ProbeAnswer` call whose arguments passed
  `ProbeAnswer.model_validate_json` with `score == 1`.
- The `"none"` negative control returned `finish_reason=stop` with zero tool
  calls in all three settings, so the endpoint honours `tool_choice` rather than
  ignoring it. The control therefore passes and the matrix is trustworthy.

### Decision — `BLOCKED_CAPABILITY` for the forced-call hypothesis

Applying the plan's Task 0 Step 3 table:

- **“Function-specific has a classified tool-choice rejection for a required
  setting, but `required` succeeds for every required setting”** → does **not**
  apply. `required` fails identically at `400` wherever thinking is enabled.
- **“Only `auto` or omitted choice returns calls with the configured thinking
  mode”** → **applies.** Both forced shapes are rejected precisely when thinking
  is `enabled`, and both succeed when it is `disabled`.

This blocks the forced-call hypothesis and triggers the plan's requirement to
preserve the current fallback. It is **not** a claim that all tool calling is
impossible: unforced calls demonstrably work, including with thinking enabled,
and the plan forbids automatically enabling an unforced mode for all agents
without its own decision and acceptance criteria.

The result is consistent with the externally reported
`deepseek-ai/DeepSeek-V3#1376` (“V4 rejects `tool_choice="required"` and
specific function `tool_choice`”) and with `pydantic/pydantic-ai#5193`
(tool-based structured output failing for this model family), which is the
reason the plan required this gate before any production change.

### Consequence

**Tasks 1–6 of the transport plan are NOT executed.** No production file was
changed by this gate: `src/deep_research/`, `config.yaml`, and every test file
are untouched at `6cf0839`. The `json_schema` Responses transport already in
place from `2fe4e32` remains the active target structured transport, and
`DeepSeekJudgeProvider` is unchanged.

The residual Critic defect recorded as Finding 5 of
`docs/superpowers/2026-09-11-critic-readiness-confirmation.md` — `json_invalid`
at `$` in roughly one repetition in four to one in eight, provably not a budget
problem — therefore remains **unfixed**. The remaining untested levers are an
unforced tool mode (its own experiment, with its own acceptance criteria), the
`/beta` strict-schema endpoint, and model or prompt changes. None is authorized
by this entry.

No live response body, prompt, secret, or reasoning content is recorded above.

## 79. DeepSeek /beta Strict-Tools Probe — UNAVAILABLE; ENFORCED-JSON FAMILY ABANDONED

Date: 2026-09-11. Candidate: `7c96a58` on `codex/cross-agent-planner-fix-parity`.
Pre-registered plan: `docs/superpowers/plans/2026-09-11-deepseek-beta-strict-tools-probe.md`.

### Scope and authorization

The user authorized exactly 3 DeepSeek chat-completion requests. No LangSmith,
no search, no application tool execution, no evaluation harness, no repetition.
Probe preserved at
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/beta_strict_probe.py`.

### Results

| # | Base URL | `strict` on function | HTTP | Error | `mentions_tool_choice` |
| --- | --- | --- | --- | --- | --- |
| 1 | `api.deepseek.com` | absent | `400` | `BadRequestError` | `true` |
| 2 | `api.deepseek.com/beta` | `true` | `400` | `BadRequestError` | `true` |
| 3 | `api.deepseek.com/beta` | absent | `400` | `BadRequestError` | `true` |

All three additionally reported `mentions_beta: false` and `mentions_strict:
false`, so the rejection is about `tool_choice` and nothing else. Every check
used `thinking: {"type": "enabled"}`, `reasoning_effort: high`, and a
function-specific `tool_choice`, matching the reported behaviour of section 78.

### Decision — `BETA_STRICT_UNAVAILABLE`

Applying the pre-registered rule: check 2 returned `400`, so the beta strict
path does **not** permit forced tool calls under the configured thinking mode.
Check 1 reproduced the known `400` exactly, which is the control that makes
checks 2 and 3 comparable rather than testing a different request shape.

Because check 1 passed its own baseline test, this is a deterministic capability
verdict, not an `INCONCLUSIVE` transport failure.

**Consequences:**

- The `strict` flag is not the active ingredient; the `/beta` base URL is not
  either. The blocker is `tool_choice` combined with `thinking: enabled`.
- The entire enforced-JSON family for DeepSeek targets is now closed: standard
  endpoint forced choice is rejected (section 78), and `/beta` strict adds
  nothing (this section). No further endpoint or strict-mode probe is
  justified.
- The dormant Tasks 1–6 of
  `docs/superpowers/plans/2026-09-11-tool-call-structured-transport.md` remain
  dormant and cannot be revived by an endpoint change.
- Per the pre-registered plan, the next path is **prompt-level JSON
  conformance** for the Critic review call, which requires no provider
  capability. That path needs its own plan and its own paid-run authorization.

No live response body, prompt text, secret, or reasoning content is recorded.

## 80. Critic Review JSON Baseline Measured — 22% FAILURE; EMPTY-CONTENT EXCLUDED

Date: 2026-09-11. Candidate: `992136b` (first set) and `8d41fc1` (second set) on
`codex/cross-agent-planner-fix-parity`.
Plan: `docs/superpowers/plans/2026-09-11-critic-prompt-json-conformance.md`,
Tasks 1 and 1b.

### Why this measurement exists

Three prior levers (evidence retention `fe434cf`, schema-enforced Responses
transport `2fe4e32`, budgets to `32768` in `96fe8a9` and `035d3c5`) were each
shipped without a before-and-after baseline, so no failure rate quoted from them
was comparable with any other. This entry establishes the first measured
baseline for the residual defect.

`json_invalid` at `field_paths` `("$",)` is a property of the request the
provider receives, so it is measurable without the evaluation harness, the
judge, LangSmith, search, or repetitions.

### Method

The probe renders the production Critic review prompt through
`CriticAgent.build_task` and `critique_messages` for the registered
`critic-live-review` case, then sends it through the project's own provider
stack with the configured model, `thinking_mode: enabled`, configured effort, and
the `32768` budget. Each response is classified from the typed
`StructuredOutputError` diagnostics.

Probe preserved at
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/critic_json_baseline.py`.

### Set 1 — 30 authorized requests, no empty-content guard

| Outcome | Count |
| --- | --- |
| ok | 24 |
| `StructuredOutputError:json_invalid` | 5 |
| `StructuredOutputError:extra_forbidden` | 1 |
| **failure rate** | **0.2000** |

Measured prompt facts: `prompt_chars: 5656`, `prompt_mentions_json: false`.

### Set 2 — 20 authorized requests, with the empty-content guard

The guard added in `8d41fc1` classifies a blank `output_text` as
`category=schema_output` at the root instead of letting it reach
`model_validate_json` and be recorded as `json_invalid`. Its unit test
`test_an_empty_structured_response_is_its_own_category` pins that behaviour for
both `""` and `"   "`. The guard is therefore verified to be live before this
set ran, which is what makes the absence of `schema_output` informative.

| Outcome | Count |
| --- | --- |
| ok | 15 |
| `StructuredOutputError:json_invalid` | 5 |
| `StructuredOutputError:schema_output` (empty body) | **0** |
| **failure rate** | **0.2500** |

### Findings

- **Combined baseline: 11 failures in 50 requests, 22.0%** (20.0% then 25.0%,
  stable across two independent sets).
- **The empty-content hypothesis is excluded.** DeepSeek's JSON Output guide
  warns that the API "may occasionally return empty content", and an empty body
  previously produced exactly this `json_invalid` signature at the root. With
  the guard live, zero of 20 responses were empty. The residual failures are
  therefore **non-empty text that is not parseable JSON**, not empty content.
- **`extra_forbidden` appeared once**, meaning on that request the model returned
  valid JSON carrying undeclared fields. That is a distinct mode from
  `json_invalid` and is not addressed by an emptiness guard.
- **The prompt violates DeepSeek's documented JSON Output requirement.** The
  vendor guide requires the word "json" in the prompt and an example of the
  desired JSON format; the measured prompt contains neither
  (`prompt_mentions_json: false`, and zero JSON lines from the earlier prompt
  shape measurement in section 78's investigation).

### Consequence

The residual defect is now a measured `22%` failure rate with a named,
vendor-documented remedy that is not yet implemented. The next change is the
prompt-level JSON contract in Task 2 of the plan, followed by an identical probe
at the same sample size for comparison.

The empty-content guard in `8d41fc1` is retained regardless of the outcome: it
removes a genuine misclassification on a shared provider path and mirrors the
non-empty requirement `_choice_text` already applies on the chat path. The full
offline gate after that change recorded `3 failed, 2087 passed, 1 deselected`,
which is the three named pre-existing failures plus the one new test.

No live response body, prompt text, secret, or reasoning content is recorded.

## 81. Critic JSON-Shaped Prompt — MEASURED NO IMPROVEMENT; VENDOR REMEDY #2 EXHAUSTED

Date: 2026-09-11. Candidate: `9ea5a88` on `codex/cross-agent-planner-fix-parity`.
Plan: `docs/superpowers/plans/2026-09-11-critic-prompt-json-conformance.md`,
Tasks 2 and 3.

### What changed

`CRITIQUE_INSTRUCTION` gained a JSON demand, and `critique_messages` gained a
`## Reply format` section naming all five fields. The example uses the
placeholder `<integer 1-10>` rather than a concrete number, because a concrete
score would anchor the model's scoring and change critique semantics while
appearing to fix formatting.

The new Critic target prompt fingerprint is `28617e508492`. The previous value
was `2c519fadb064`, so scores recorded before and after this change are not
comparable on that axis.

### Measurement

The identical probe and sample size were used, on the same case, model,
thinking mode, effort, budget, and transport. Probe preserved at
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/critic_json_baseline.py`.

| Condition | Requests | Failures | Rate | `prompt_mentions_json` |
| --- | --- | --- | --- | --- |
| Baseline (no JSON signal) | 30 | 6 | `0.2000` | `false` |
| Baseline with empty-content guard | 20 | 5 | `0.2500` | `false` |
| **Baseline combined** | **50** | **11** | **`0.2200`** | `false` |
| **JSON-shaped prompt** | **30** | **8** | **`0.2667`** | **`true`** |

Per-run outcomes for the post-change set: 8 `json_invalid`, 22 ok, and **zero**
`extra_forbidden`.

### Finding — no improvement

The prompt change is confirmed live (`prompt_mentions_json: true`, prompt grew
from `5,656` to `6,066` chars) and did **not** reduce the failure rate. The
observed `26.7%` against a `22.0%` baseline is not an improvement; at these
sample sizes the difference is not statistically meaningful in either
direction, so the honest reading is **no effect**.

This exhausts DeepSeek's documented JSON Output remedy #2 for the remaining
defect. Requirement #1 (`response_format`) and #3 (budget) were already
satisfied, and requirement #4 (empty content) was excluded by section 80's
measurement, where zero of 20 guarded responses were empty. All four documented
requirements are now accounted for, and the `json_invalid` failure survives.

One weak secondary signal: `extra_forbidden` appeared once in the baseline and
zero times with the shape example. That is consistent with the field list
discouraging undeclared fields, but one observation is not evidence, and
`json_invalid` is the dominant mode.

### Retained change

`9ea5a88` is **retained** rather than reverted. It is additive, it
matches vendor guidance, it costs nothing measurable, and it removed the only
`extra_forbidden` observation. Reverting it would reintroduce a prompt that
names neither JSON nor its own output shape. Its unit tests pin the contract and
forbid a concrete example score.

### Where this leaves the defect

The residual `critic_report_review` `json_invalid` failure is now:

- measured at roughly **22-27%** of review calls;
- **not** truncation (a `length` finish reason raises `ProviderOutputLimitError`
  first, and the budget is `32768`);
- **not** empty content (section 80: zero of 20 guarded responses were empty);
- **not** fixed by the vendor's documented prompting remedy (this section).

Remaining untested levers, in the order the plan ranks them:

1. **Reduce reasoning effort for the review call only.** At `reasoning_effort:
   max`, output capacity may be consumed by reasoning before the JSON object is
   emitted, which would present as non-empty, non-JSON text. This is testable
   with the same cheap probe and is the next candidate.
2. **Micro-decompose the review into smaller structured calls** (for example
   score plus rationale in one call, gaps plus queries in another), mirroring
   the Fact Checker's one-call-per-claim shape, so each target is small and
   fixed.
3. **Accept containment.** The `review_produced` gate in `cccc139` already
   prevents a fallback from being certified as a pass, so the defect degrades
   about one repetition in four rather than producing a false pass.

No lever is authorized by this entry. No live response body, prompt text,
secret, or reasoning content is recorded.

## 82. Critic json_invalid ROOT CAUSE FOUND — DSML TOOL MARKUP EMITTED WITH NO TOOLS OFFERED

Date: 2026-09-11. Candidate: `fcc05ac` on `codex/cross-agent-planner-fix-parity`.
Probes: `output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/shape_probe.py`
and `family_probe.py` (shape probe 30 requests; family probe 20 requests; both
authorized).

### What was measured

The shape probe recorded a bounded structural signature for every response and
stored no content:

| First-attempt shape | Count | Length | First/last char |
| --- | --- | --- | --- |
| `starts_with_brace` (a real JSON object) | 14 / 30 | 3,837-5,191 | `{` / `}` |
| `prose_no_json` | 16 / 30 | 424-660 | `<` / `>` |

No fenced output, no truncated JSON, no prose-prefixed JSON, and no empty body.
Every failure shares one form: a short response that begins with `<` and ends
with `>`.

The family probe then recorded a redacted profile of those short responses. Every
one of them contains the identifier **`DSML`** together with `invoke`,
`parameter`, `name`, `string`, and `true`, plus the tool names `web_search` and
`query_memory`. No response content was stored.

`DSML` is DeepSeek's native tool-call markup. The model is attempting to **invoke
a tool**, and because the request offers none, the markup is returned as ordinary
message text instead of a tool call, where local JSON validation rejects it.

### Root cause

The Critic's review request offers no tools, but its system prompt tells the model
to use them.

- Verified: the review request payload contains exactly
  `input`, `max_output_tokens`, `model`, `reasoning`, `text`.
  **No `tools` and no `tool_choice`.**
- Verified: `critique_messages` renders no tool definitions.
- Verified: `CRITIC_SYSTEM_PROMPT` states "Use web_search to spot-check a
  suspected gap or a figure that looks wrong, and query_memory to compare this
  report against what previous sessions established."

The agent has two provider calls. The ReAct spot-check loop correctly offers both
tools. The structured review call offers none, but reuses the same system prompt.
The model follows the instruction, attempts a tool invocation, and emits DSML
markup into a channel with no tool support.

### Why this explains every earlier observation

| Observation | Explanation |
| --- | --- |
| `json_invalid` at `field_paths` `("$",)` | DSML markup is not JSON at all. |
| Intermittent, roughly 50% of first attempts | A sampling choice: answer directly, or try the tools first. |
| Unaffected by `max_tokens` raises | The response is short, not truncated. |
| Unaffected by prompt-level JSON wording | The model is not attempting malformed JSON; it is not attempting JSON. |
| Empty-content hypothesis failed | The body is non-empty markup. |
| Repair rescues most cases | The repair instruction demands JSON, and the model then complies. |
| 3 of 30 hard failures | Cases where the repair attempt also emitted DSML markup. |

### Contribution and correction of the record

The system prompt line that advertises the tools was added in `9a89a24`
("fix(critic): state that the report is already in front of it"). It was a
reasonable-looking mitigation at the time, and it is plausibly a contributor to
this failure mode rather than an innocent bystander. This entry corrects the
record accordingly: the campaign's own earlier fix may have introduced the
dominant residual failure.

### Effective settings for this probe

- Model `deepseek-v4-flash`, thinking `enabled`, **`reasoning_effort: high`**
  (the resolved critic request setting).
- Note the discrepancy recorded in the audit: the *live evaluation* profile
  resolves target effort `max`, while this prompt path runs `high`. Any
  reasoning-effort experiment must state which it changes.

### Consequence

A first-class fix is now available, and it is not a tuning change. The two
candidate directions are:

1. **Do not advertise tools the review call cannot use.** Give the structured
   review its own system prompt that drops the tool-invocation sentence, keeping
   the tool-aware prompt for the ReAct spot-check loop only.
2. **Offer the tools to the review call** so a tool request is honoured as a real
   tool call rather than emitted as text.

The first is smaller and removes the mismatch; the second changes the review
call's contract and cost profile. Neither is authorized by this entry.

### Verification available cheaply

The same 30-request shape probe can measure a candidate fix by counting
`prose_no_json` first attempts, which is a direct, content-free measure of this
exact defect. No live evaluation repetition is needed to test it.

No live response body, prompt text, secret, or reasoning content is recorded.

## 83. Critic Review Prompt Revised — TOOL-FREE PROMPT, CALIBRATED EXAMPLES, H1 ENVELOPE

Date: 2026-09-11. Candidate: `57d4b68` plus uncommitted working-tree changes on
`codex/cross-agent-planner-fix-parity`. Artifacts:
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/PROMPT_FOR_APPROVAL.txt`
and `FULL_PROMPT.txt`.

This entry records the prompt revision built on the section 82 root cause. **No
live request has been sent for this revision.** It is the version awaiting user
approval, and the paid-run ledger is unchanged by this entry.

### What changed

1. **The review call no longer advertises tools.** `critique_messages` now carries
   `CRITIC_REVIEW_SYSTEM_PROMPT`, which names no tool and states that everything
   needed is printed in the request. `CRITIC_SYSTEM_PROMPT` is retained for the
   ReAct spot-check loop, the one call that does offer tools. The section 82
   defect was a prompt telling the model to use tools on a request carrying
   `tools: NOT SENT`.
2. **`CRITIQUE_INSTRUCTION` no longer restates JSON demands**, so the wire
   carries exactly one statement of the output contract, in `# Reply format`.
3. **The reply-format skeleton is replaced by two complete, valid JSON
   instances**, labelled `Weak report, score 3:` and `Strong report, score 9:`.
   The previous angle-bracket skeleton (`{"score": <integer 1-10>, ...}`) was
   neither a schema nor an example, while the DeepSeek JSON Output guide asks for
   "an example of the desired JSON format".
4. **A `# How to choose the score` band table was added** (1-3 / 4-6 / 7-8 /
   9-10), phrased as report quality. It deliberately does not publish the
   acceptance threshold: routing is computed locally and `CRITIQUE_INSTRUCTION`
   already forbids the model from deciding continuation.
5. **The report is quoted in its own Markdown fence** (` ```report `), with an
   explicit sentence that the report's own headings belong to the report.
   Superseded the angle-bracket markers first written here; see section 84.
6. **Every request section heading was promoted from H2 to H1.**

### The examples were mis-calibrated on first draft, and that was caught offline

The first version of the two-example change labelled the anchors 3 and 9 but
wrote rationales that read like band 4-6 and band 7-8 respectively. Since a model
calibrates against what an example *demonstrates* rather than the number attached
to it, that pair would have pulled scores toward the middle of the scale — the
opposite of the intent. Both rationales were rewritten to sit visibly inside
their own band: the weak anchor now turns on a claim that "rests on no cited
source at all" (band 1-3), and the strong anchor on an answer that is "complete"
with "strong and diverse" sourcing that states its uncertainty (band 9-10).

This was found by reading the rendered prompt, not by a live run, and it is now
locked by a structural test rather than by prose assertion.

### Why H1 for the request envelope

`REPORT_SECTIONS` in `report.py` defines the canonical report body as **H2**
headings (`## Executive summary`, `## Findings`, `## Limitations`, …) with H3
sub-groups. The review request also used H2 for all ten of its own sections. The
two were therefore at the same heading level, and `## Recorded problems` directly
followed the report's `## Limitations` with only the markers to separate them.

At H1 every request heading outranks the report, so the report reads as content
nested inside `# Report under review`. The report carries exactly one H1 in
production — the title `# Research report: <question>` from `REPORT_TITLE_PREFIX`
— which sits immediately after the BEGIN marker and is not a section heading. The
boundary is now carried by the markers *and* the heading level, not by markers
alone.

### Considered and rejected: pretty-printed examples

Indented examples were proposed and measured. Cost is negligible: +72 characters
(~20 tokens) across both examples, in a prompt of roughly 5,500 tokens. They are
also safe for the parser, which is `schema.model_validate_json` and therefore
whitespace-agnostic; a pretty-printed instance of the strong example was verified
to validate into `CritiqueDraft`.

They were rejected on signal, not on cost or correctness. The instruction is
"Return exactly one JSON object … with no text before or after it", and a
one-line example embodies that, whereas an 11-to-15-line indented block reads as
a document region — closer to the prose register this model already drifts into,
having emitted DSML tool markup as prose. Pretty examples would also put two JSON
registers in one request, because the provider-appended schema message is
deliberately compact (`_json_instruction` uses `sort_keys=True,
separators=(",", ":")`) and is one line. Compact examples keep one register.
The DeepSeek JSON Output guide requires an example but says nothing about
indentation, so it does not settle this either way.

### Offline verification

- `python -m pytest`: **3 failed, 2100 passed, 1 deselected**. The three failures
  are the pre-existing evaluation failures recorded throughout this log and are
  untouched by this change.
- `python -m ruff check src tests`: `All checks passed!`
- New tests lock each decision: the review prompt names no tool; both examples
  parse as JSON with exactly the five fields; the examples bracket the acceptance
  score; each example demonstrates the band it is labelled with; no request
  section sits at H2; the band table leaks no routing threshold.

### What this entry does not claim

The measured root cause was tool markup, so this revision targets that mismatch.
It does **not** claim the `json_invalid` rate is fixed: section 81 measured that
prompt-level JSON wording alone moved 22.0% to 26.7%, which is no improvement.
The direct, content-free test of the fix is the section 82 shape probe — a
30-request run counting `prose_no_json` first attempts against the 16/30 (53%)
baseline — and that run is not authorized by this entry.

No live response body, prompt text, secret, or reasoning content is recorded.

## 84. Report Delimiter Changed from Angle-Bracket Markers to a Markdown Fence

Date: 2026-09-11. Candidate: `57d4b68` plus uncommitted working-tree changes on
`codex/cross-agent-planner-fix-parity`. Same revision as section 83; this entry
supersedes the delimiter choice described there.

**No live request has been sent for this revision.** The paid-run ledger is
unchanged.

### What changed

The report was delimited by `<<<REPORT BEGIN>>>` and `<<<REPORT END>>>`. It is
now quoted in a Markdown fence whose opening fence carries the info string
`report`:

    ```report
    ...verbatim report...
    ```

The opening fence is the begin marker and the closing fence is the end marker, so
no separate sentinel text is needed. `_REPORT_BEGIN` and `_REPORT_END` are
removed. The system prompt sentence "the report under review between its BEGIN
and END markers" was updated to "inside its own fenced block" — leaving it would
have contradicted the request it accompanies.

### Why a fence rather than angles

A fence is the one Markdown construct that delimits a verbatim region, and
**fenced content is not parsed as Markdown**, so the report's own `##` sections
cannot be read as sections of the request at all. This does the boundary work more
completely than heading level alone, and it removes the pseudo-XML delimiter the
user rejected.

### The fence length is computed, not fixed

A synthesised report can quote a fenced block of its own. A fixed three-backtick
fence would then be closed early by the report's content, exposing the remainder
of the report as if it were request text. `_report_fence` therefore measures the
longest backtick run in the report and emits one backtick more, with a floor of
three: a report containing a three-backtick run is enclosed in a four-backtick
fence, and the closing fence is matched by exact tick count.

### New risk introduced, and how it is detected

The request now demonstrates a fenced block, and models sometimes mirror input
formatting in output. A reply wrapped in its own json code fence would fail JSON
validation. Three existing guards apply: the `# Reply format` section shows
unfenced single-line examples and requires "no text before or after it", the
provider-appended schema message already forbids Markdown, and the section 82
shape probe classifies a fenced reply as its own `fenced` shape. A regression of
this kind is therefore measurable by the same 30-request probe that measures the
original defect, at no extra cost.

### Offline verification

- `python -m pytest tests/test_agents/`: **427 passed**.
- `python -m pytest`: **3 failed, 2102 passed, 1 deselected** — the three
  pre-existing evaluation failures, untouched here.
- `python -m ruff check src tests`: `All checks passed!`
- Two mutation checks were run, and both guards failed as required before being
  restored: reverting one request section to H2 fails
  `test_no_request_heading_can_be_confused_with_a_report_heading`; pinning the
  fence to three backticks fails
  `test_a_report_containing_a_fence_cannot_close_the_enclosing_fence`.
- `PROMPT_FOR_APPROVAL.txt` and `FULL_PROMPT.txt` regenerated: no `<<<` or `>>>`
  remains in either artifact.

### Envelope constraint recorded

`test_no_request_line_uses_angle_bracket_markers` constrains the request envelope
only, never the report body: the report is provider-written text and may
legitimately contain angle brackets. Asserting their absence across the whole
message would have been a test that the report's content must not look a certain
way, which is not a property this system controls.

No live response body, prompt text, secret, or reasoning content is recorded.

## 85. Critic shape probe after the prompt revision — 0/30 FAILURES; PROMPT PROVENANCE VERIFIED

Date: 2026-09-12. Candidate: `57d4b68` plus uncommitted working-tree changes on
`codex/cross-agent-planner-fix-parity`. Probe:
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/shape_probe.py`,
30 requests, user-authorized. Verification:
`verify_probe_prompt.py` (free; builds and hashes messages, contacts no provider).

### Result

| | Baseline (section 82) | This run |
| --- | --- | --- |
| First-attempt `prose_no_json` | **16 / 30 (53%)** | **0 / 30 (0%)** |
| First-attempt `starts_with_brace` | 14 / 30 | **30 / 30** |
| First-attempt `fenced` | 0 / 30 | 0 / 30 |
| Response length (valid) | 3,837-5,191 | 2,233-3,662 |
| Undeclared keys | none recorded | none recorded |
| Outcomes | repair rescued 13, 3 hard-failed | 30 `ok`, no repair needed |

All 30 first attempts were balanced-brace JSON objects beginning `{` and ending
`}`. No response contained a code fence, so the mirroring risk introduced by
fencing the report (section 84) **did not materialize** in 30 attempts.

By the rule of three, 0/30 bounds the first-attempt failure rate at about 10%
with 95% confidence, against a measured 53% at baseline and a `json_invalid` rate
of 22% (11/50) in the live evaluation. Wall time was 3m48s for 30 requests.

### CORRECTION: the effort was `high`, and the prompt fix is isolated

This entry first claimed the run used `reasoning_effort: max` and that effort was
therefore a confound. **That was wrong, and the opposite is true.**

Loading the API key required the main repository's `.env`, which sets
`LLM_REASONING_EFFORT=high`, and `load_config` applies environment overrides
*after* the YAML file (`config.py:469`), so the environment value won: the probe
ran at **`high`**, the same effort as the section 82 baseline. Verified by running
the probe's own dry run with and without that file loaded: it reports `high` with
the file and `max` without it.

The consequence is stronger than what was claimed. Baseline and post-change run
share the prompt's only other variable, so **16/30 -> 0/30 is attributable to the
prompt revision alone**, with effort held constant.

The gap this leaves is the other one: the live evaluation runs at `max`
(`evaluation.target_reasoning_effort`), which the probe did **not** measure. The
shipping configuration's prompt behaviour is still unmeasured by this probe.

### Prompt provenance — the question "was the right prompt sent"

The probe builds its messages with `critique_messages` from the imported package,
so provenance was verified rather than assumed. `verify_probe_prompt.py` reports:

- `deep_research.__file__` and `critic.__file__` both resolve inside **this
  worktree's** `src`, not the root virtualenv's editable install, which points at
  a different branch.
- `_REPORT_BEGIN` and `_REPORT_END` are **absent** from the imported module and
  `_report_fence` is present, which can only be true of the revised revision.
- `prompt_chars = 7727`, matching the probe's own dry run.
- Both messages appear **verbatim in `FULL_PROMPT.txt`**, the artifact put in
  front of the user for approval, with `developer sha256[:16] = e7d73e8a96ae2957`
  and `user sha256[:16] = f07e1d252d96ddbf`.
- The request carries the fence info string, `# How to choose the score` with both
  band ranges, both examples at scores 3 and 9, no angle-bracket markers, and no
  tool name in the developer message.
- The reply format still shows **two unfenced** example lines plus "with no text
  before or after it".

### Response length fell, and that is unexplained

Valid responses shortened from 3,837-5,191 to 2,233-3,662 characters. Two changes
could account for it and this run does not separate them: the examples now
demonstrate a terse shape, and the effort change may alter verbosity. A shorter
critique is not automatically a thinner one — every response is a complete,
schema-valid object — but the content length is recorded here because it changed,
not because it is understood.

### Environment gotcha found while launching

A first launch sent **zero** requests and failed with
`ProviderConfigurationError: DEEPSEEK_API_KEY is required`. `load_config` calls
`load_dotenv(dotenv_path=path.parent / ".env")`, resolved against the directory of
the config file, and this worktree contains only `.env.example`; the real `.env`
is in the main repository root. Probes run from a worktree must therefore load
that file into the process environment first. The failed launch contacted no
provider and incurred no cost.

### What this entry does not claim

This is a **request-shape** measurement, not an evaluation. It shows the review
call now returns valid JSON on the first attempt. It does **not** measure whether
the critic's *scores* are well calibrated, whether routing decisions are correct,
or whether the evaluation aggregate (0.6 x judge + 0.4 x deterministic) clears the
0.75 threshold. Those require an authorized live evaluation run, which this entry
does not authorize.

Paid-run ledger: **30 requests** (this probe). One failed launch sent 0.

No live response body, prompt text, secret, or reasoning content is recorded.

## 86. Judge Schema Failures — `strict` REJECTED, RATIONALE BOUND EXCEEDED ON 5/30 FIRST ATTEMPTS

Date: 2026-09-12. Candidate: `7460952` plus uncommitted working-tree changes on
`codex/cross-agent-planner-fix-parity`. Probe:
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/judge_probe.py`,
34 requests total, user-authorized. Diagnosis only: **no fix is implemented or
proposed as proven by this entry.**

### Phase 1: every recorded judge failure, not a sample

A scan of all local evaluation artifacts found 18 repetitions with a judge
problem: `judge_schema_failure` 11 (2 live), `judge_output_limit` 6 (2 live),
`no_evaluable_output` 1. Live field paths were `string_bounds` on `rationale`
and `extra_forbidden` at `$`.

`judge_output_limit` is **already resolved**: both live occurrences are at
`750472b` (no judge budget) and `7f84378` (`judge_max_tokens: 8192`); `96fe8a9`
raised the budget to 32768 and **no output-limit failure has occurred in the 11
live repetitions since**. No prompt work is indicated for that class.

`judge_schema_failure` is **current**: the most recent run (`035d3c5` r4)
failed `string_bounds` on `rationale` and then `extra_forbidden`.

### The prose prompt states no output contract

`JudgeVerdict` requires `scores` and `rationale`, with
`rationale: min_length=1, max_length=2000`, and forbids extra properties. The
prose prompt mentions **none** of `rationale`, `json`, `example`, `field`,
`character`, `object`, `keys`, or `2000`; its only hit for `score` concerns
dimension scores. The contract exists only in the appended schema message
(1,326 chars, no example instance), against a 1,393-char prose prompt.

### Diagnostic A: `strict` is rejected by the transport

`text.format` is sent with no `strict` flag, so `maxLength` and
`additionalProperties` are advisory rather than enforced. Adding `strict: true`
to an otherwise identical request returned **HTTP 400** on 3 of 3 attempts. A
**control** — the same hand-built request with the flag omitted — was
**ACCEPTED** and returned a schema-valid verdict, which attributes the 400 to
`strict` itself rather than to the request shape.

**This closes the transport lever.** The prompt is the only available lever for
this failure class, exactly as the user's question anticipated. It is the same
outcome as the earlier `/beta` strict-tools rejection (sections 78-79): this
provider does not expose schema enforcement.

### Diagnostic B: the bound violation is systematic, not marginal

30 production judge requests at `reasoning_effort: max`:

| Measure | Value |
| --- | --- |
| First attempts violating `maxLength=2000` | **5 / 30 (16.7%)**, `string_too_long` |
| Rationale length, median | **1,735** (265 below the cap) |
| Rationale length, min / max | 1,339 / 2,263 |
| First attempts with extra top-level keys | **0 / 30** |
| Keys emitted | `agent_specific`, `rationale`, `scores` every time |
| Common dimensions scored | 6 every time |
| Final outcomes | 30 `ok` — the repair flow rescued all 5 |

The distribution sits against the bound rather than near the middle: the median
leaves only 265 characters of headroom, so exceeding 2,000 is ordinary
variation, not an outlier. The repair attempt recovered all five in this probe,
which is why this input yielded no terminal `judge_schema_failure`; the live
artifacts show that when the repair itself violates a constraint the judge does
not run at all.

`extra_forbidden` did not reproduce in 30 attempts, so it is rare compared with
the bound violation and should not drive the revision.

### Hypothesis, stated before any fix

"The judge's `string_bounds` failures occur because the request relies on a
schema constraint that the transport does not enforce and the prose never
states. Stating the output contract in prose, including the 2000-character
bound, will reduce the first-attempt violation rate below 5/30."

This is **not yet tested**. The revision and its re-measurement are not
authorized by this entry.

### Input fidelity and its limits

The judge input was reconstructed, not captured: the artifacts store no target
output content, so the critique was rebuilt to the size the live evaluation
produced (2,233-3,662 chars of JSON). The rubric, case, and prompt are the real
frozen ones. A reconstructed input can shift the absolute rate, so the 16.7%
figure characterizes this input; the live artifact rate of 2 schema failures in
17 repetitions is the production reference point and is consistent with it.

### Cost record

34 requests authorized and 34 spent: 3 for diagnostic A, 1 for its control, 30
for diagnostic B. A first attempt at diagnostic B sent **zero** requests — it
failed locally because the probe never opened a session span, and `tracker.py:582`
raises `RuntimeError` for a child span without one, before any HTTP call. The
probe was corrected and re-run.

No live response body, prompt text, secret, or reasoning content is recorded.

## 87. Judge Prompt Revised — CONTRACT, SCALE BANDS, TWO EXAMPLES, EXPLICIT WEIGHTS; BOUND DECOUPLED

Date: 2026-09-12. Candidate: `f51301d` plus uncommitted working-tree changes on
`codex/cross-agent-planner-fix-parity`. Probes:
`output/transport-probes/265e51af79c543eaaa8c9e90a07dc555/judge_probe.py` and
`verify_judge_probe.py`. 99 requests total, all user-authorized.

### What changed

1. **The request has proper heading levels.** Every section this instruction owns
   is `#`; the judged run's sixteen blocks stay `##` and therefore nest beneath
   "The run to judge". Previously a block named `## rubric` read as a section of
   the instruction rather than as material — the same collision the Critic
   request had with its report.
2. **A response contract names the three fields** and what each means.
3. **A band table covers the whole 0.0-1.0 scale.** The rubric's own anchors
   state only `1.0` and `0.0`, so two judges could agree on both endpoints and
   still differ by 0.3 on a middling run.
4. **Two complete examples bracket the scale** (weak `<= 0.4`, strong `>= 0.8`),
   generated from the rubric's own dimension ids, labelled as placeholders.
5. **The weighting is stated as arithmetic**: each dimension with its weight, the
   total, and that agent-specific dimensions carry no weight at all.
6. **The length bound is decoupled from its enforcement.**

### The bound: 2000 stated, 20000 enforced, neither declared to the model

The prompt states "in at most 2000 characters ... The limit is hard and a longer
answer is rejected outright". The schema the model receives declares **no** length
for `rationale` — only `minLength: 1`. The 20000 bound is enforced locally after
the response returns, by a validator that raises `string_too_long` so the shared
classifier still reports `string_bounds` and the one-repair flow still applies.

The stated limit is deliberately harder than the enforced one, and that is an
explicit decision rather than an oversight: **the statement is the steering
device** that keeps the model inside the range, and the wider enforcement is the
**safety net** that stops the minority of overshoots from costing a score.

An earlier iteration declared `maxLength: 20000` in the schema, which put two
different numbers for the same field in front of the model — 2000 in prose, 20000
in the schema. That was removed: the request now carries exactly one length
signal. A test asserts the declared property is exactly
`{"minLength": 1, "title": "Rationale", "type": "string"}`.

Removing the bound entirely was considered and rejected: with no bound the only
backstop is `max_tokens`, and hitting that is a truncated response — a
`ProviderOutputLimitError` the attempt loop does not catch, so it is never
repaired. A bound keeps the failure repairable.

### `extra="ignore"` scoped to `JudgeVerdict`

`ContractModel` forbids extra properties project-wide. Naming the three fields
invited the model to add a fourth: measured at **0 of 30** first attempts when no
field was named, and **11 of 30** once they were, with `agent_specific_note`,
`agent_specific_notes`, `rationale_note` and `final_note` observed. An explicit
prohibition in the prompt did not reduce it, and in one repair the model invented
a further key rather than repeating.

`JudgeVerdict` therefore sets `extra="ignore"`, and only that model. An added key
carries nothing this system reads — `judge_quality` consumes `scores` alone and
`rationale` is a recorded comment — so losing an entire evaluation repetition
over it is disproportionate. Drift is still caught: `scores` and `rationale`
remain required, so a rename or an omission still reports `missing` and still
fails; nested extras inside `scores` remain forbidden.

### Three measurements, and a regression found and fixed

| First attempts, 30 requests at `max` | Baseline | Revision | Revision + `extra="ignore"` |
| --- | --- | --- | --- |
| `string_too_long` (length) | 5 | **0** | **0** |
| Extra key added | 0 | **11** | 9 (tolerated) |
| `json_invalid` (unparseable) | 0 | 0 | 2 (both repaired) |
| **Terminal failures** | **0** | **3 (10%)** | **0** |
| Rationale median | 1,735 | 1,716 | 1,724 |

The middle column is a regression this work created: fixing length by naming the
fields cost 3 of 30 runs outright, worse end-to-end than the baseline it was
meant to improve. It was caught by measurement, not by reasoning, and the third
column is the correction.

Two things the measurements do **not** show: the prose guidance has not moved the
rationale median (1,735 -> 1,724 across the revision), so the length improvement
comes from the widened enforcement rather than from the prompt asking for it; and
the 2 `json_invalid` attempts are new, both repaired, and unexplained.

### Fingerprint history

`77a0898f4267` (recorded on the `035d3c5` live artifacts) -> `6ca1874ed3bc` ->
`0c90bd2185d8` -> **`74b9cddfbbee`**. `judge_prompt_fingerprint` covers
`JudgeVerdict.model_json_schema()` as well as the prompt text, so the bound and
`extra` changes move it too. That is stricter than strictly necessary — the bound
cannot affect a score — but it is the safe direction, and the pin caught each
change rather than letting one pass silently.

### What this entry does not claim

Judge **score** quality and calibration are not measured here: no statement is
made about whether the bands produce better or more consistent scores, only that
the request now returns a valid verdict reliably. A live evaluation remains the
end-to-end gate.

Paid-run ledger for the judge work: **99 requests** — 3 for the `strict`
capability probe, 1 for its control, 30 for the original rationale measurement, 5
on a superseded configuration that was killed mid-run, 30 for the corrected
revision, 30 for the `extra="ignore"` confirmation. Of these, 5 were spent on a
configuration that never shipped.

No live response body, prompt text, secret, or reasoning content is recorded.

## 88. Critic Live Canary — THREE REPETITIONS; DEFECT CLASS FIXED, ONE CALIBRATION FINDING

Date: 2026-09-12. Candidate: `bc3f472`. Command:
`python -m deep_research.evaluation agent critic --tier live` — the critic canary,
which runs the target agent, all fifteen deterministic gates, and the
LLM-as-a-judge evaluator, and no other agent. Artifacts:
`output/evaluations/live-critic-canary/critic/*/results.json`.

Cost: **20 model calls**, measured from the recorded traces (7, 6, 7 per
repetition, against an estimate of 5-8 derived from four earlier traces).

### Result

| Repetition | Status | Gates | Deterministic | Judge | Aggregate | Fallback | ReAct stop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | REVIEW REQUIRED | **15/15** | 0.75 | 0.7755 | **0.7653** | none | finished |
| 2 | REVIEW REQUIRED | **15/15** | **1.00** | 0.7520 | **0.8512** | none | finished |
| 3 | REVIEW REQUIRED | **15/15** | **1.00** | 0.8150 | **0.8890** | none | finished |

All three: `react_stop_reason = finished`, no `fallback_provider_diagnostic`,
`prohibited_call_count = 0`, `errors = []`, judge `status = scored` with **zero
diagnostics**, and aggregate above the 0.75 live threshold. Provenance:
`target_prompt_fingerprint = bf86f19981a6` (the revised prompt of section 83),
target effort `max`, judge effort `max`, judge transport
`deepseek_responses_json_schema_v1`. `git_dirty` is true only because the
untracked `output/`-adjacent artifact directories count as dirty; the tracked tree
is clean.

### The defect class is fixed

Section 82's root cause produced `json_invalid` on the review call, which meant no
critique, no judge, and an unscorable repetition. That did not occur: three
consecutive canaries produced a critique, ran the judge, and passed every hard
gate.

Three repetitions alone would be weak evidence — against the prior live rate of
4 of 17 repetitions carrying `json_invalid` (about 24%), three clean runs occur
with probability `0.76^3`, roughly 44%. The strength comes from combining this
with the section 85 shape probe: **33 consecutive critic review calls with no
provider fallback and no judge failure**, which under the same 24% prior is about
`0.76^33`, roughly 1 in 8000. No claim is made that the rate is exactly zero.

### Finding: the score, not the critique, is the weak dimension

The critic returned **score 5 with `should_continue = True` in all three
repetitions**, while the case's reference expectation is route `end` with
`minimum_score: 7`. That expectation is not gate-enforced: it reaches the judge as
reference material, and the judge penalises it through the agent-specific
dimensions.

The judge's own verdicts show the same shape every time:

| Dimension | Rep 1 | Rep 2 | Rep 3 |
| --- | --- | --- | --- |
| `critique_actionability` | 0.90 | 0.90 | 0.90 |
| `gap_precision` | 0.88 | 0.85 | 0.85 |
| `score_groundedness` | 0.75 | 0.60 | 0.80 |
| `scoring_calibration` | 0.70 | 0.60 | 0.80 |

The critique's actionable content is graded consistently high; the **score** is
the weakest agent-specific dimension in every repetition. One hypothesis, not
established here: the band table added in section 83 makes 5 the consistent
reading of a report that carries no quantitative results, while the case's
`minimum_score: 7` was written before those bands existed. Resolving it is a
policy choice — revisit the case expectation, or revisit the critic's band
placement — and this entry does neither.

### Finding: one intermittent heuristic failure

Repetition 1's `deterministic_quality` is 0.75 because `no_spurious_gaps`
returned 0. The check fails when a gap's tokens contain all tokens of a reference
theme and no single clause of the report both carries those tokens and an
unresolved marker. The trigger was a gap containing the theme
`commercial-scale deployment` whose actual point was that the deployment is
unquantified — that the report names no plant, company, country or capacity
figure — which the fixture report genuinely does not. The token-subset heuristic
cannot separate "the theme is missing" from "the theme is present but
unquantified".

Assessed as a heuristic false positive, corroborated by the judge scoring
`gap_precision` 0.85-0.88 on the same gaps. The trigger appeared in 1 of 3
repetitions, so it is intermittent rather than systematic. The gap text was read
from the recorded trace for this diagnosis and is not reproduced here.

### Which bar the canary meets

- Against the stated live threshold, aggregate `>= 0.75`: **3 of 3 pass**.
- Against the stricter bar used in earlier canary dispositions,
  `deterministic_quality == 1.00`: **2 of 3 pass**, the exception being the
  heuristic above.
- Against agreement with the case's reference expectation: **0 of 3**, the
  calibration finding above.

### What this entry does not claim

It says nothing about any other agent, and it does not establish that the critic's
score calibration is correct — only that it is consistently at odds with this
case's reference expectation. One case, one repetition per run, three runs.

No live response body, prompt text, secret, or reasoning content is recorded.

## 89. External Review of the Critic Calibration Decisions — VERIFIED, TWO CORRECTIONS ACCEPTED, `unsupported_claims` CLARIFIED

Date: 2026-09-12. Candidate: `7fdedf5` plus working-tree changes on
`codex/cross-agent-planner-fix-parity`. Artifacts:
`docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md` (the
brief), `...-review-prompt.md` (the review prompt). **No paid calls were made for
this entry.**

### What was reviewed

Section 88 left two calibration questions open. A brief and a review prompt were
written for an external reviewer, who returned a recommendation on both. Every
substantive claim in that review was then checked against the codebase and the
artifacts before anything was implemented.

### Claims verified as correct

| Claim | Evidence |
| --- | --- |
| `agent_prompt_fingerprint` hashes the **shared** `agents.prompts` module, so a one-sentence edit moves every agent's recorded `target_prompt_fingerprint` | `config.py:154-162` hashes `inspect.getsource(deep_research.agents.prompts)`. The brief had described the blast radius as the Critic's alone; wrong. |
| A semantic case change **requires** a case version bump | Dataset identity is `(case_id, case_version)` (`datasets.py:5-7`, `:207`); `build_case(..., version: int = 1)` defaults, so `version=2` must be passed explicitly. |
| The live reference **is** pinned in tests | `test_cases_critic.py:160-164` pins `expected_route: "end"` and `minimum_score: 7`. |
| The canaries' unsupported-claim counts are **4, 8, 5**, not four | Read from the three judge rationales. Section 88 reported repetition 1's count as if it were all three. |
| Rep 1's judge asserts `no_spurious_gaps` passed while the artifact records 0.0 | Its rationale reads "every gate including critique_actionable and no_spurious_gaps passed". |
| The `results.json` artifacts omit the primary target JSON | The critique is recoverable only from the recorded trace. |
| 162 tests pass at the tip | Reproduced exactly: `162 passed` across the critic agent, critic cases, and judging test modules; ruff clean. |

One claim was found to be **worse than the review stated**: `no_spurious_gaps` is
not merely a metric the judge misreported — it is **not a gate at all**, and the
judge's input carries `gate_results` but never the deterministic metrics. The judge
therefore asserted a fact about a check it had no access to. This invalidates the
corroboration section 88 offered for its `no_spurious_gaps` diagnosis: the judge
*perceives*, it does not verify.

### Where the review overstated, and the review accepted the narrowing

1. **"Any non-empty `unsupported_claims` list forces another research pass."** True
   only when budget remains, a report exists, `score >= 7`, and `gaps` is empty —
   `route_decision` checks in that order (`critic.py:274-284`). The review agreed
   its wording overstated the marginal effect.
2. **"An impossible evidence standard."** Too strong. The Critic's ReAct loop has
   `web_search` and `query_memory`, the case declares
   `required_live_dependencies=("tavily", "memory")`, and it used them — its
   unsupported-claims reasoning cites live figures. The accurate criticism is that
   strictness asks it to infer *exhaustive* citation entailment from a deliberately
   non-exhaustive digest plus limited spot checks. The review agreed.
3. **The band-table inference for Decision 2** ("7-8 requires claims to be
   attributed") is corroborative, not decisive, because "attributed" admits both
   readings. The review agreed and supplied stronger evidence.

The review's own qualification of the fingerprint point is also correct and is
recorded: the pin is a **drift alarm, not an attribution mechanism** — artifacts
also record `git_commit`, so a clean committed revision is explained by its diff,
and attribution is lost only for a dirty tree whose exact source snapshot was not
kept.

### Decision 2 implemented

`CRITIQUE_INSTRUCTION` (`prompts.py:207`) now defines `unsupported_claims`
leniently with an explicit override:

- a statement is unsupported when it is *neither* clearly attributed to one of the
  report's cited sources *nor* backed by a verified claim;
- it must not be marked unsupported *solely* because it lacks a separate
  verified-claim entry, because the claim digest is deliberately partial;
- **a contrary claim verdict or spot-check evidence still makes it unsupported,
  however it is cited** — the override that stops leniency from laundering
  citations.

Two tests added: a structural prompt test pinning all three parts of that
distinction and asserting the superseded strict wording is gone
(`test_prompts.py`), and the Critic target-fingerprint pin plus a test documenting
the shared-module scope (`test_config.py`). The fingerprint moved
`bf86f19981a6 -> 2c0bd1210e21`.

### Decision 1 deferred, per the agreed order

Decision 1 (case v2 with an expected 4-6 band and `refine` route) is **not**
applied. The agreed sequencing is: apply Decision 2, canary it, then decide. If the
canary moves the fixture consistently to 7/end, Decision 1 is paused rather than
applied mechanically; if the fixture stays in 4-6/refine, it becomes a case-v2
change with the expected band and route, a version bump, a dataset sync, updated
pinned tests in `test_cases_critic.py`, and its own canary.

The three-repetition canary for Decision 2 costs about 20 model calls and is **not
authorized by this entry**.

### Corrections applied to the committed brief

The brief and the review prompt both carried errors this entry fixes: a fixed HEAD
that could not stay true, "four unsupported_claims" generalised from one run, the
judge-corroboration argument, "no quantitative content at all" (the report does
contain vague quantities such as "several years"), the "1 in 8,000" figure quoted
as a probability when it treats an estimate as known and assumes i.i.d. calls, "the
cost is near zero", and the understated fingerprint blast radius. The brief's
section 9 now lists these as accepted weaknesses, and both documents point at the
commit range instead of a mutable tip.

No live response body, prompt text, secret, or reasoning content is recorded.
