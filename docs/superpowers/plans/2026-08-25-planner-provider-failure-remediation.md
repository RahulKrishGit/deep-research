# Planner Provider-Failure Remediation Implementation Plan

> **For agentic workers:** Use the approved execution workflow and complete the tasks in order. Every code task requires TDD RED before GREEN, focused and neighboring tests, one full-suite run, Ruff, `git diff --check`, a fix-log update, review, and a commit. The current documentation commit is authored by Luna and changes only the three requested documentation files.

**Goal:** Correct the planner provider-failure boundary so output-limit, structured-schema, transport/HTTP, and judge failures remain distinct and safe, then run a one-variable planner-final token experiment with evidence strong enough to determine the next remediation step.

**Architecture:** Add typed safe telemetry at the provider boundary; preserve causes through ReAct and Planner; classify failures from typed causes instead of generic messages; retain sanitized evaluator diagnostics and URLs; and pass an operation-specific token override only to the planner’s final `ResearchPlanDraft` request. Keep the global 4096 cap, ReAct, judge, dataset, rubric, model, timeout, retry, concurrency, and tool controls frozen during the experiment.

**Tech Stack:** Python, Pydantic contracts, DeepSeek and OpenAI provider adapters, pytest with fake providers, Ruff, the existing `deep_research.evaluation` CLI, LangSmith EU tracing, and Markdown documentation.

## Global Constraints

- Work in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\agent-improvement-planner` on branch `codex/evaluate-improve-planner`. `14f06b7` is the diagnosis/document-review base only; `5a50bfd` is the initial documentation commit. Implementation starts from the reviewed head containing documentation fix round 1, with its exact SHA recorded in the fix log before Task 1 starts.
- Preserve `tests/test_diagnostic_planner_deepseek_length.py` exactly as the existing untracked characterization test. Do not edit it, stage it, or commit it.
- This documentation fix round changes only the three requested docs. After this reviewed head is committed, the implementation tasks below are authorized for continuous execution immediately from that reviewed head; no deferral is required.
- Every production, test, or documentation write, including every implementation/fix wave and every fix-log update, uses `gpt-5.6-luna` at `max` reasoning effort.
- Every analysis, task review, scoped re-review, integration review, and final whole-branch review uses `gpt-5.6-sol` at `high` reasoning effort. Preflight the exact model and effort; never rely on inherited defaults.
- If Sol/high review finds a scoped issue, authorize a Luna/max TDD review-fix loop only within the affected Tasks 1–4 or final-review finding. Follow each loop with Sol/high scoped re-review. Allow at most five loops for the campaign, then stop and report the unresolved finding; do not expand unrelated scope.
- Keep `LLMConfig.max_tokens` and the global YAML `llm.max_tokens` at `4096`. Only the final planner operation may receive the operation-specific override.
- ReAct requests remain at global `4096`. Judge requests remain at their configured `4096`. Do not increase either budget in this campaign.
- The first controlled experiment uses only `planner_final_max_tokens=8192` and target reasoning effort `max`. Use `16384` only under Task 7’s evidence gate.
- Freeze target model, judge model, reasoning profiles, dataset, rubric, case set, repetitions, timeout, retry policy, concurrency, tool budgets, prompt/configuration fingerprints, and environment. Do not claim the installed Harness `maxTokens=256000` default alone causes success.
- Use the controlled tier only. Do not run a live tier or another deep-research agent. The judge is the evaluator for the selected planner run, not another target agent.
- Paid DeepSeek or LangSmith calls require immediate human confirmation immediately before the command that makes the call. Confirmation is not obtained at plan creation time and must be repeated for a later 16384 call.
- Keep `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` for approved tracing. Never print environment values or credentials.
- No secret, prompt, provider response content, tool input, evaluator input, or hidden chain-of-thought may enter logs, traces, artifacts, test snapshots, or documentation.
- Do not blindly retry identical truncated responses. Adaptive retries that change a token budget are out of scope unless separately approved.
- Every task or experiment appends a clearly dated entry to `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md` with issue, evidence, RED/GREEN, code/review status, and live link/result or an explicit not-run state.
- The supplied offline baseline is `1801 passed, 1 deselected, 2 known dependency warnings`; Ruff is clean and `git diff --check` is clean. Treat these as the campaign baseline, not as results produced by this documentation-only commit.
- Do not merge, push, open a pull request, or run paid evaluation as part of implementation unless separately authorized.

## Execution order and dependencies

1. Task 1 establishes typed provider telemetry and the nonretryable output-limit boundary. It is implemented and reviewed before Task 2 starts.
2. Task 2 consumes that boundary, preserves causes, and exclusively owns `src/deep_research/evaluation/models.py` plus the shared taxonomy contracts. It is implemented and reviewed before branching.
3. After the reviewed Task 2 SHA is recorded, Task 3 and Task 4 run in separate isolated worktrees/branches created from that exact SHA. Task 3 owns planner-final configuration; Task 4 consumes Task 2’s model/taxonomy contracts and must not modify `evaluation/models.py`.
4. Merge Task 3 first, resolve normally, then merge Task 4. After integration, rerun Task 4 focused and neighboring tests, followed by the Task 5 whole-branch review.
5. Task 5 verifies the whole branch and obtains a Sol/high review before any paid call.
6. Task 6 runs the human-confirmed focused 8192 experiment and permits the full controlled dataset only after both target and judge gates pass.
7. Task 7 chooses exactly one evidence-backed branch: a focused 16384 experiment when target length persists with no judge/evaluator failure, or residual ReAct/judge diagnosis otherwise.

Tasks 1–4 are code tasks. Tasks 5–7 are verification, review, experiment, and diagnosis gates. Every task handoff names Luna/max as the write owner and Sol/high as the analysis/review owner; no task authorizes unrelated implementation.

After Task 2’s Sol/high review, create the parallel worktrees from the same reviewed SHA with these concrete commands from the repository common worktree:

```powershell
$Task2Sha = git rev-parse HEAD
git worktree add 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\planner-remediation-task3' -b codex/planner-remediation-task3 $Task2Sha
git worktree add 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\planner-remediation-task4' -b codex/planner-remediation-task4 $Task2Sha
```

Create the integration worktree from the same Task 2 SHA, merge Task 3 before Task 4, and run the Task 4 verification again after both merges:

```powershell
git worktree add 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\planner-remediation-integration' -b codex/planner-remediation-integration $Task2Sha
Push-Location 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\planner-remediation-integration'
git merge --no-ff codex/planner-remediation-task3
git merge --no-ff codex/planner-remediation-task4
python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_reporting.py -k "diagnostic or url or provider_failure or schema"
python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_reporting.py
Pop-Location
```

The integration commands run in the integration worktree. Ordinary merge conflicts are resolved by Luna/max, followed by Sol/high integration review; do not merge the branches in the reverse order.

## Task 1: Typed output-limit failure and safe provider telemetry

**Files:**

- Modify `src/deep_research/providers/contracts.py`.
- Modify `src/deep_research/providers/deepseek_provider.py`.
- Modify `src/deep_research/providers/openai_provider.py` only where the shared provider contract or an already-supported SDK output-limit signal requires parity.
- Add focused coverage in `tests/test_deepseek_provider.py`.
- Extend neighboring coverage in `tests/test_openai_provider.py` and `tests/test_retry_policy.py` where the shared exception contract applies.
- Read only `tests/test_diagnostic_planner_deepseek_length.py`; preserve it byte-for-byte and leave it untracked.

**Handoff routing:** Production/test writes and any Task 1 fix wave use `gpt-5.6-luna` at `max`. Task analysis, review, and any scoped re-review use `gpt-5.6-sol` at `high`. A Sol finding uses the explicitly authorized scoped Luna/max TDD loop, then Sol/high re-review, within the five-loop campaign cap.

**Interfaces and behavior:**

1. Add a typed `FinishReasonCategory` finite type with exactly `stop`, `length`, `content_filter`, `tool_calls`, `insufficient_system_resource`, and `other`. Add a typed `ProviderResponseTelemetry` model in `providers/contracts.py` with `finish_reason_category`, `configured_max_tokens`, existing typed `TokenUsage`, `request_attempt`, and optional `structured_attempt`. Enforce positive attempt/cap values and forbid unrecognized fields using the project’s existing Pydantic conventions. Never store the provider’s raw finish-reason string.
2. Add `ProviderOutputLimitError` as a nonretryable `ProviderResponseError` subclass. Give it a static safe message and a typed telemetry attribute. Do not include response text, prompt text, request payloads, URLs with credentials, or exception strings.
3. Extend provider response errors with a safe failure category and optional integer HTTP status code so adapter-created transport and HTTP failures can be classified later without parsing unbounded SDK messages. Preserve existing retryability behavior.
4. In DeepSeek, count actual request invocations around the existing `with_retries` operation. Normalize the provider finish value with a helper that accepts at most 64 characters before `_choice_text` rejects the response. Map exact normalized `length` to `ProviderOutputLimitError` with the configured cap and attempt metadata. Map `stop`, `content_filter`, `tool_calls`, `insufficient_system_resource`, unknown values, empty values, non-strings, control-heavy values, and values longer than 64 characters to their finite categories, with all unrecognized or oversized values becoming `other`. Leave other non-stop responses as safe non-output-limit response errors.
5. Pass the structured attempt number into the same path for `complete_structured`. Preserve the existing two-attempt repair behavior, but make the eventual typed output-limit error identify the request and structured attempt.
6. Record only the finite finish category, cap, usage, and attempt metadata on the provider span. Keep the raw provider finish value, exact response, and request content out of spans and artifacts.
7. For OpenAI, map only a documented SDK output-limit signal when one already exists in the adapter. Do not infer a limit from response text or invent a signal when the SDK does not expose one.

**TDD and handoff sequence:**

- [ ] **RED:** First add tests that construct the existing fake response shape with `finish_reason=length` and typed usage, then assert `ProviderOutputLimitError`, `retryable is False`, exact safe telemetry, and one provider invocation even when the configured retry count is five. Add adversarial tests for all six finite categories, `None`, non-string values, empty/whitespace values, unknown values, control-heavy values, and an oversized provider string; assert every unrecognized or oversized value becomes `other`, the raw value is absent from the exception/span/artifact, and only `length` raises `ProviderOutputLimitError`. Add an assertion that the exception string contains no response or prompt data. Run:

  ```powershell
  python -m pytest -q tests/test_deepseek_provider.py -k "length or output_limit"
  ```

  Expected RED evidence: the new typed exception or telemetry assertions fail against the current generic `ProviderResponseError` path.

- [ ] **GREEN:** Implement the smallest provider-contract and adapter changes, then rerun the focused DeepSeek tests until they pass.
- [ ] Run neighboring provider/retry tests:

  ```powershell
  python -m pytest -q tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_retry_policy.py
  ```

- [ ] Run the full suite once after the task is green:

  ```powershell
  python -m pytest -q
  ```

  Record the exact pass, deselection, and warning counts in the fix log.

- [ ] Run lint and whitespace checks:

  ```powershell
  python -m ruff check src tests
  git diff --check
  ```

- [ ] Append the Task 1 issue, RED assertion, GREEN result, changed files, review status, and exact verification results to the planner fix log. State that the untracked diagnostic test was preserved.
- [ ] Review the diff for secret and response-content leakage, then commit only the Task 1 implementation, focused tests, and fix-log update with a scoped message such as `fix: preserve provider output limit telemetry`.

**Exit criteria:** A DeepSeek `length` response produces one typed nonretryable failure carrying safe usage/cap/attempt metadata; retries do not duplicate it; and no existing provider/retry contract regresses.

## Task 2: Exception propagation and evaluation failure taxonomy

**Files:**

- Modify `src/deep_research/providers/contracts.py` and `src/deep_research/providers/deepseek_provider.py` to retain typed structured diagnostics from Task 1’s contracts.
- Modify `src/deep_research/agents/react.py`.
- Modify `src/deep_research/agents/errors.py` and `src/deep_research/agents/planner.py` where operation context or cause preservation is required.
- Modify `src/deep_research/evaluation/models.py`; Task 2 exclusively owns this shared model file and the shared taxonomy contracts.
- Create `src/deep_research/evaluation/failure_taxonomy.py` for the typed cause classifier consumed by target and judge evaluation code.
- Modify `src/deep_research/evaluation/targets.py`.
- Add or update tests in `tests/test_deepseek_provider.py`, `tests/test_agents/test_react.py`, `tests/test_agents/test_planner.py`, `tests/test_evaluation/test_models.py`, `tests/test_evaluation/test_failure_taxonomy.py`, and `tests/test_evaluation/test_targets.py`.

**Handoff routing and ownership:** Production/test writes and any Task 2 fix wave use `gpt-5.6-luna` at `max`. Task analysis, review, and scoped re-review use `gpt-5.6-sol` at `high`. A Sol finding uses the explicitly authorized scoped Luna/max TDD loop, then Sol/high re-review, within the five-loop campaign cap. Task 2’s reviewed SHA is the only branch point for Tasks 3 and 4. Task 4 must not modify `src/deep_research/evaluation/models.py`, `tests/test_evaluation/test_models.py`, or the shared taxonomy contract; it consumes the reviewed Task 2 versions.

**Interfaces and behavior:**

1. Add a bounded `StructuredValidationDiagnostic` with one-based `attempt`, normalized `field_paths`, and an optional stable category. Extend `StructuredOutputError` with an immutable collection of those records. Keep existing safe-summary bounds and remove any path that could contain an input value. A document-level failure uses a stable root path.
2. Preserve diagnostics for both structured attempts. A pair of successful HTTP responses followed by two `ReActDecision` validation failures must yield two diagnostic records, not a provider reachability result.
3. Update ReAct’s provider-decision exception path to record only a safe event and re-raise the original `ProviderError` after the span closes. Keep tool failures recoverable as before. Preserve compatibility for explicitly constructed `ReActRun` values, but do not use a generic terminal-only `provider_error` result to hide a provider cause from the planner.
4. Update Planner wrapping to use operation-specific static context while preserving the original exception as its cause. Do not use reachability language for output-limit or schema failures. Preserve the cause through both ReAct decision and final `ResearchPlanDraft` paths.
5. Add the shared taxonomy contracts in `evaluation/models.py` and `evaluation/failure_taxonomy.py`, then update evaluation classification to walk the full cause chain and check specific types before generic provider errors. Use this exact target mapping: `output_limit` at provider stage; `schema_output` at validation stage; `provider_timeout`, `provider_rate_limit`, `provider_transport`, `provider_http`, and `provider_response` at provider stage. Define the evaluator-prefixed judge reasons for Task 4 to consume: `judge_output_limit`, `judge_schema_failure`, `judge_transport`, `judge_http`, and fallback `judge_provider_failure`.
6. Add an allow-listed safe details projection to evaluation failure artifacts. Output-limit details contain finish category, cap, typed usage, and attempts. Schema details contain attempt numbers and field paths. Transport/HTTP details contain type, retryability, and optional status code. Never serialize exception objects or `str(error)` from a provider cause.
7. Add the typed evaluator diagnostic contract and `JudgeFeedback` diagnostic collection in `evaluation/models.py`, but do not implement judge integration in this task. Task 4 consumes these reviewed contracts.

**TDD and handoff sequence:**

- [ ] **RED:** Add a provider structured-output test asserting two sanitized diagnostic records with attempt numbers and field paths, with no raw provider data. Add ReAct and Planner tests asserting the original provider cause is reachable through `__cause__`. Add model-contract tests for safe failure details and evaluator diagnostics, plus classifier tests for each taxonomy row; assert that output-limit and schema failures are not labeled reachability failures. Run:

  ```powershell
  python -m pytest -q tests/test_deepseek_provider.py tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_evaluation/test_models.py tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_targets.py -k "diagnostic or schema or provider or classify"
  ```

  Expected RED evidence: the current generic `StructuredOutputError`, swallowed ReAct provider error, and broad classifier fail the new assertions.

- [ ] **GREEN:** Implement cause-preserving propagation, bounded diagnostics, typed safe artifact projection, and ordered taxonomy checks. Rerun the focused tests until they pass.
- [ ] Run neighboring model and evaluation tests:

  ```powershell
  python -m pytest -q tests/test_deepseek_provider.py tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_evaluation/test_models.py tests/test_evaluation/test_targets.py
  ```

- [ ] Run the full suite once, then Ruff and the whitespace check:

  ```powershell
  python -m pytest -q
  python -m ruff check src tests
  git diff --check
  ```

- [ ] Append the Task 2 evidence to the fix log, including the two-response/two-validation case, the safe taxonomy rows, the shared-model ownership boundary, and any known unresolved judge URL gap. Do not paste provider output or exception text.
- [ ] Review all serialized fields and trace metadata for secrets, prompts, response content, evaluator inputs, and hidden reasoning. Commit the code, tests, and fix-log update with a scoped message such as `fix: classify planner provider failures safely`.
- [ ] Obtain Sol/high review of the complete Task 2 commit. Record its exact reviewed SHA as the common base before creating the isolated Task 3 and Task 4 branches.

**Exit criteria:** Cause chains survive ReAct and Planner, structured diagnostics contain only field paths and attempts, and offline artifacts distinguish all target failure families without raw provider data.

## Task 3: Operation-specific planner-final max-token configuration

**Files:**

- Work in an isolated worktree/branch created from the reviewed Task 2 SHA. Do not branch from `14f06b7`, `5a50bfd`, or an unreviewed Task 2 working tree.
- Modify `src/deep_research/utils/config.py`.
- Modify `config.yaml`.
- Modify the `StructuredCompleter` protocol in `src/deep_research/agents/base.py`.
- Modify `src/deep_research/agents/planner.py`.
- Modify `src/deep_research/providers/deepseek_provider.py` and `src/deep_research/providers/openai_provider.py`.
- Update test support in `tests/agent_fakes.py`.
- Add or update tests in `tests/test_config.py`, `tests/test_agents/test_planner.py`, `tests/test_deepseek_provider.py`, `tests/test_openai_provider.py`, and `tests/test_evaluation/test_config.py`.

**Handoff routing:** Production/test writes and any Task 3 fix wave use `gpt-5.6-luna` at `max`. Task analysis, review, and scoped re-review use `gpt-5.6-sol` at `high`. A Sol finding uses the explicitly authorized scoped Luna/max TDD loop, then Sol/high re-review, within the five-loop campaign cap. Task 3 consumes the reviewed Task 2 contracts without modifying their owning files.

**Interfaces and behavior:**

1. Add `AgentRuntimeConfig.planner_final_max_tokens: int = 4096` with the same positive-value validation style as existing runtime settings. Add the matching `agents.planner_final_max_tokens: 4096` YAML value and `AGENTS_PLANNER_FINAL_MAX_TOKENS` environment override. Ensure configuration fingerprints include the effective value.
2. Extend `StructuredCompleter.complete_structured` with `max_tokens: int | None = None` as a keyword-only optional override after `agent_name`. Update all in-repository fakes and recording completers to accept and record it.
3. Providers resolve `max_tokens` to the global configured cap when `None`; otherwise use the validated per-call value for only that request field. DeepSeek uses `max_tokens`; OpenAI uses `max_output_tokens`.
4. `PlannerAgent._request_plan` passes `self.config.planner_final_max_tokens` only for `ResearchPlanDraft`. ReAct decision calls pass no override. Judge construction and calls pass no override. Add tests that record request budgets for all three operations and assert final planner-only override behavior.
5. Keep the global YAML and environment default at 4096. A focused experiment may set `AGENTS_PLANNER_FINAL_MAX_TOKENS=8192` without changing `LLM_MAX_TOKENS`.

**TDD and handoff sequence:**

- [ ] **RED:** Add configuration, protocol, provider-request, fake-completer, planner, and evaluation-fingerprint tests first. Assert the default is 4096, an environment override reaches only final planning, ReAct and judge remain 4096, and provider request fields use the override. Run:

  ```powershell
  python -m pytest -q tests/test_config.py tests/test_agents/test_planner.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_evaluation/test_config.py -k "planner_final or max_tokens or budget or fingerprint"
  ```

  Expected RED evidence: `AgentRuntimeConfig` has no planner-final field and structured provider calls do not accept a per-call cap.

- [ ] **GREEN:** Implement the setting, protocol keyword, provider request resolution, Planner-only wiring, and test-fake updates. Rerun the focused tests until they pass.
- [ ] Run neighboring agent/config/evaluation tests:

  ```powershell
  python -m pytest -q tests/test_config.py tests/test_agents/test_planner.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_evaluation/test_config.py tests/test_runtime/test_assembly.py
  ```

- [ ] Run the full suite once, Ruff, and the whitespace check:

  ```powershell
  python -m pytest -q
  python -m ruff check src tests
  git diff --check
  ```

- [ ] Append the Task 3 RED/GREEN evidence to the fix log. Record the effective 4096/8192 operation-budget assertions and state that no ReAct or judge request changed.
- [ ] Review configuration loading for shell-over-file precedence and review serialized fingerprints for secret values. Commit the code, tests, and fix-log update with a scoped message such as `feat: isolate planner final token budget`.

**Exit criteria:** The global cap remains 4096, only final `ResearchPlanDraft` accepts the operation-specific value, and the effective budget is visible in safe configuration metadata.

## Task 4: Judge evaluator diagnostics and URL preservation

**Files:**

- Work in a separate isolated worktree/branch created from the same reviewed Task 2 SHA used for Task 3. Do not branch from Task 3 or modify Task 2’s shared model/taxonomy contracts.
- Modify `src/deep_research/evaluation/judging.py`.
- Modify `src/deep_research/evaluation/runner.py` only where `JudgeFeedback` diagnostics and URL fields must be carried into `results.json`.
- Modify `src/deep_research/evaluation/reporting.py` only where safe diagnostics must be rendered.
- Add or update tests in `tests/test_evaluation/test_judging.py`, `tests/test_evaluation/test_judge_visibility.py`, `tests/test_evaluation/test_runner.py`, and `tests/test_evaluation/test_reporting.py`.

**Handoff routing:** Production/test writes and any Task 4 fix wave use `gpt-5.6-luna` at `max`. Task analysis, review, and scoped re-review use `gpt-5.6-sol` at `high`. A Sol finding uses the explicitly authorized scoped Luna/max TDD loop, then Sol/high re-review, within the five-loop campaign cap. Task 4 consumes the reviewed Task 2 model and taxonomy contracts and must not modify `src/deep_research/evaluation/models.py` or `tests/test_evaluation/test_models.py`.

**Interfaces and behavior:**

1. Consume the typed, safe evaluator diagnostic model and `JudgeFeedback` diagnostic collection created and reviewed in Task 2. Do not add or alter model fields in this task; preserve the stable kind, optional attempt number, normalized field paths, and absence of free-form provider or evaluator messages.
2. Preserve the existing `JudgeFeedback.evaluator_trace_url` and `evaluator_source_url` fields. Make the traceable judge callback accept the installed LangSmith `run_tree` injection and capture `run_tree.get_url()` when available. Validate and retain only a URL string.
3. Preserve `evaluator_source_url` only when a URL is directly supplied by the evaluator integration. If the SDK does not expose one or the behavior is unsupported, leave it as `None`; do not derive, reconstruct, infer, or manufacture a source URL from any input, prompt, trace, or exception.
4. In `JudgeEvaluator._not_run` and `run_judge`, map typed causes to evaluator-prefixed reasons and carry safe diagnostics and available URLs into `JudgeFeedback`. Preserve `judge_provider_failure` for the unresolved generic case.
5. Keep judge comments, metadata, artifacts, and reports free of `JudgeInput`, prompts, provider response text, evaluator inputs, secrets, and hidden reasoning. A missing URL is an infrastructure diagnostic, not evidence that judging passed.
6. Add artifact round-trip tests proving the trace/source fields survive serialization and that unavailable source URLs remain explicitly unavailable.

**TDD and handoff sequence:**

- [ ] **RED:** Add fake-trace tests where a supplied `run_tree` exposes a safe URL, plus tests for a missing or unsupported source URL, typed output-limit/schema causes, and generic judge provider failure. Assert the directly supplied trace URL and directly supplied source URL survive `JudgeFeedback` and artifact round trips, assert unsupported source behavior remains `None` with no derivation, and assert evaluator inputs and provider content do not appear. Run:

  ```powershell
  python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_reporting.py -k "diagnostic or url or provider_failure or schema"
  ```

  Expected RED evidence: the current judge path returns missing diagnostics and leaves evaluator URL fields unset.

- [ ] **GREEN:** Implement safe trace/source capture, evaluator-prefixed taxonomy, typed diagnostics, and runner/reporting propagation. Rerun the focused tests until they pass.
- [ ] Run neighboring runner and reporting tests:

  ```powershell
  python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_reporting.py
  ```

- [ ] Run the full suite once, Ruff, and the whitespace check:

  ```powershell
  python -m pytest -q
  python -m ruff check src tests
  git diff --check
  ```

- [ ] Append the Task 4 evidence to the fix log. State exactly which evaluator URLs were present, which remained unavailable, and why no evaluator input or provider content was retained.
- [ ] Review serialized models, reports, and trace metadata for leakage. Commit the code, tests, and fix-log update with a scoped message such as `fix: preserve judge failure diagnostics`.

**Exit criteria:** `judge_provider_failure` has actionable safe diagnostics when available, typed judge output/schema/transport/HTTP reasons are distinct, and trace/source URLs survive artifacts without exposing evaluator data.

**Integration handoff:** After Sol/high review approves both isolated branches, Luna/max merges Task 3 first and Task 4 second into the integration branch from the reviewed Task 2 SHA. Resolve ordinary merge conflicts with the existing branch conventions; do not rewrite history or widen scope. After both merges, Luna/max reruns the Task 4 focused and neighboring test commands, then Sol/high performs the integration review before Task 5.

## Task 5: Complete offline verification and Sol/high whole-branch review

**Files:**

- Read and review the complete branch diff and history.
- Update only `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md` for the verification ledger.
- Do not make unrelated changes during this gate. Scoped Luna/max TDD review-fix loops for a Task 1–4 or final-review finding are explicitly authorized; each loop must stay within the finding’s files and be followed by Sol/high scoped re-review.

**Handoff routing:** Documentation writes and scoped review-fix writes use `gpt-5.6-luna` at `max`. Analysis, whole-branch review, and every scoped re-review use `gpt-5.6-sol` at `high`. The campaign allows a maximum of five review-fix loops total; stop after the fifth unresolved loop rather than widening scope.

**Verification:**

- [ ] Confirm the preserved untracked test is unchanged and remains untracked:

  ```powershell
  git status --short --branch
  git diff -- tests/test_diagnostic_planner_deepseek_length.py
  ```

- [ ] Run the full offline suite once from the implementation branch:

  ```powershell
  python -m pytest -q
  ```

  Record the exact result. The supplied baseline is `1801 passed, 1 deselected, 2 known dependency warnings`; a changed count must be explained by the implementation tests.

- [ ] Run Ruff and whitespace checks:

  ```powershell
  python -m ruff check src tests
  git diff --check
  ```

- [ ] Run Markdown-safe scans over the three requested docs and the fix log:

  ```powershell
  $DocScanTerms = @('T'+'BD', 'TO'+'DO', 'FIX'+'ME', 'place'+'holder', 'to be '+'determined', 'fill '+'in', 'similar to '+'task') -join '|'
  rg -n -i $DocScanTerms `
      docs/superpowers/specs/2026-08-25-planner-provider-failure-remediation-design.md `
      docs/superpowers/plans/2026-08-25-planner-provider-failure-remediation.md `
      docs/superpowers/2026-08-25-planner-evaluation-fix-log.md
  ```

  Expected result: no matches. Also inspect Markdown links, headings, task ordering, and code blocks manually.

**Review gate:**

- [ ] Request a whole-branch review from `gpt-5.6-sol` at high reasoning effort. The review is read-only and must inspect Tasks 1–4, the fix log, the preserved untracked test, dependency ordering, secret-safety claims, and the experiment gates. It must not make provider calls, use a live tier, or delegate to another agent.
- [ ] If the review finds a blocking issue, use one scoped Luna/max TDD review-fix loop: RED focused test or check, GREEN fix, focused and neighboring verification, full suite once, Ruff, and `git diff --check`. Follow it with a Sol/high scoped re-review, increment the campaign loop count, and stop after five loops if a blocking issue remains. Record each loop; do not expand unrelated scope.
- [ ] Append the complete offline verification and review result to the fix log. Do not request human confirmation for a paid call until this gate is green.

**Exit criteria:** Offline code and artifact checks are green, the whole branch has a Sol/high review with no unresolved blocking finding, the fix log contains exact evidence, and the environment is ready for one confirmed focused call.

## Task 6: Human-confirmed focused 8192 experiment and conditional full dataset

**Files:**

- Update `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md` immediately before and after each approved call.
- Preserve generated `results.json` and experiment metadata in their existing output location; do not copy raw prompts, provider responses, evaluator inputs, or secrets into tracked files.

**Handoff routing:** Live-call preparation and fix-log writes use `gpt-5.6-luna` at `max`. Experiment-result analysis and the decision to proceed use `gpt-5.6-sol` at `high`. Human confirmation remains required immediately before every paid call.

**Focused run:**

- [ ] Confirm that Task 5 is green, the target/judge configuration fingerprint is frozen, and the shell has no conflicting `LLM_MAX_TOKENS` override. Set only the operation-specific environment value for this run:

  ```powershell
  $env:AGENTS_PLANNER_FINAL_MAX_TOKENS = '8192'
  ```

- [ ] Immediately before the next command, obtain explicit human confirmation for the paid DeepSeek and LangSmith call. Record confirmation time, current SHA, operation budget, target reasoning `max`, and the exact command in the fix log without recording credentials.
- [ ] Run the approved controlled focused case with the existing repository launcher/venv and the existing EU LangSmith endpoint:

  ```powershell
  python -m deep_research.evaluation agent planner `
      --config config.yaml `
      --case focused-decomposition `
      --reasoning-effort max `
      --judge-reasoning-effort max `
      --experiment-prefix planner-provider-remediation-8192 `
      --verbose
  ```

  The run must use the controlled tier, one case, the frozen repetition count, and existing dataset/rubric configuration. Do not add a live-tier flag or another agent.
- [ ] Resolve the newly created `results.json` using the existing output convention, validate it with `ExperimentResult`, and record only safe metadata: experiment name, status, target failure taxonomy, repetition counts, configuration fingerprint, safe output-limit telemetry, evaluator trace/source URLs when directly supplied, and the direct experiment URL. Unsupported `evaluator_source_url` behavior remains `None`; do not derive it.
- [ ] Treat any target failure as a failed focused gate. Count expected judge evaluations from the focused repetition set and require every expected judge evaluation to be present, completed, and successfully scored (`judge.status == "scored"` and no `not_run_reason`), with no `judge_not_run`, evaluator error, `judge_output_limit`, `judge_schema_failure`, `judge_transport`, `judge_http`, or generic `judge_provider_failure`. A judge/evaluator failure routes to Task 7 residual diagnosis and blocks the full dataset, even when target failures are zero.

**Conditional full controlled dataset:**

- [ ] If and only if the focused run has zero target failures **and** the expected-judge count equals the successfully scored-judge count with zero judge/evaluator failures, obtain the required immediate human confirmation for the full controlled dataset, keeping `AGENTS_PLANNER_FINAL_MAX_TOKENS=8192` and every other variable unchanged.
- [ ] Run the existing full controlled planner command without `--case`, using the same target/judge reasoning profiles and prefix family. Validate exactly the frozen three cases and three repetitions per case. Record status, experiment URL, result path, safe taxonomy counts, and whether evaluator URLs were available.
- [ ] If the focused run has any target failure or any judge/evaluator failure, do not run the full dataset. Record the failure category, expected/completed/scored judge counts, and route to Task 7 residual diagnosis.
- [ ] If the full controlled run itself has any target or judge/evaluator failure, mark its quality result invalid for promotion, preserve only safe failure metadata, and route to Task 7 residual diagnosis rather than treating the run as a pass.
- [ ] Append both the preflight and post-run entries to the fix log, including issue, evidence, RED/GREEN state where applicable, code/review status, direct links/results, and a statement that raw data was not recorded.
- [ ] Clear the per-run environment override after the evidence is recorded:

  ```powershell
  Remove-Item Env:AGENTS_PLANNER_FINAL_MAX_TOKENS -ErrorAction SilentlyContinue
  ```

**Exit criteria:** The focused experiment is human-confirmed and documented. The full controlled dataset exists only when target failures are zero, every expected judge evaluation completed and was successfully scored, and no judge/evaluator failure occurred; otherwise Task 7 uses the observed category to select one next branch.

## Task 7: Conditional 16384 experiment or residual ReAct/judge diagnosis

**Files:**

- Update `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`.
- Read the safe `results.json` and available LangSmith trace/evaluator metadata.
- Do not edit unrelated production code or tests under this conditional task.

**Handoff routing:** Fix-log writes use `gpt-5.6-luna` at `max`. Residual-failure analysis and branch selection use `gpt-5.6-sol` at `high`. No branch selection authorizes unrelated code changes.

**Branch A — output limit persists:**

- [ ] Choose this branch only when Task 6 contains a target-side `output_limit` at 8192, with safe telemetry showing the configured cap was reached, and contains no judge/evaluator failure. Do not choose it for a schema, transport/HTTP, judge, or generic failure.
- [ ] Obtain immediate human confirmation again immediately before execution. Set only:

  ```powershell
  $env:AGENTS_PLANNER_FINAL_MAX_TOKENS = '16384'
  ```

- [ ] Run one focused `focused-decomposition` controlled call with target reasoning `max`, unchanged judge budget, unchanged dataset/rubric, unchanged retry/timeout/concurrency, and a distinct experiment prefix. Record the direct experiment URL and safe result metadata. Do not run the full dataset in this conditional step.
- [ ] Clear the environment override and append the evidence. State whether the typed output-limit event disappeared, persisted, or changed category. A successful request without preserved diagnostics is not a remediation pass.

**Branch B — output limit does not persist:**

- [ ] Choose this branch when the 8192 result has no target output-limit event, or when any judge/evaluator failure exists, or when the unresolved `judge_provider_failure` still lacks evaluator diagnostics/URLs. Any judge/evaluator failure takes this residual-diagnosis route and blocks the full dataset.
- [ ] Do not run 16384. Use only the safe artifact fields and available trace/evaluator URLs to identify the next diagnosis. If a code correction is required, stop and request a separately approved scope; do not expand this plan silently.
- [ ] Append the diagnosis, evidence links, missing-evidence statement, and next decision to the fix log.

The two branches are mutually exclusive. Do not run both without new evidence and a separate approval.

## Final documentation and commit gate

**Handoff routing:** Documentation writes use `gpt-5.6-luna` at `max`; final analysis and commit-scope review use `gpt-5.6-sol` at `high`.

- [ ] Self-review the design and plan together for contradictions, missing dependencies, unbounded data, unclear experiment gates, and commands that change more than the named variable.
- [ ] Confirm the current documentation commit stages only:

  - `docs/superpowers/specs/2026-08-25-planner-provider-failure-remediation-design.md`
  - `docs/superpowers/plans/2026-08-25-planner-provider-failure-remediation.md`
  - `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`

- [ ] Confirm `tests/test_diagnostic_planner_deepseek_length.py` remains untracked and unchanged.
- [ ] Run the Markdown-safe scans and `git diff --check` before staging.
- [ ] Commit the three documentation files with:

  ```powershell
  git add -- docs/superpowers/specs/2026-08-25-planner-provider-failure-remediation-design.md docs/superpowers/plans/2026-08-25-planner-provider-failure-remediation.md docs/superpowers/2026-08-25-planner-evaluation-fix-log.md
  git diff --cached --check
  git commit -m "docs: plan planner provider failure remediation"
  ```

- [ ] Verify the commit contains exactly those three paths and the working tree still reports only the preserved untracked test.
