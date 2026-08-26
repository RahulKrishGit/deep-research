# Planner Provider-Failure Remediation Implementation Plan

> **For agentic workers:** Use the approved execution workflow and complete the tasks in order. Every code task requires TDD RED before GREEN, focused and neighboring tests, one full-suite run, Ruff, `git diff --check`, a fix-log update, review, and a commit. The current documentation commit is authored by Luna and changes only the three requested documentation files.

**Goal:** Correct the planner provider-failure boundary so output-limit, structured-schema, transport/HTTP, and judge failures remain distinct and safe, then run a one-variable planner-final token experiment with evidence strong enough to determine the next remediation step.

**Architecture:** Add typed safe telemetry at the provider boundary; preserve causes through ReAct and Planner; classify failures from typed causes instead of generic messages; retain sanitized evaluator diagnostics and URLs; and pass an operation-specific token override only to the planner’s final `ResearchPlanDraft` request. Keep the global 4096 cap, ReAct, judge, dataset, rubric, model, timeout, retry, concurrency, and tool controls frozen during the experiment.

**Tech Stack:** Python, Pydantic contracts, DeepSeek and OpenAI provider adapters, pytest with fake providers, Ruff, the existing `deep_research.evaluation` CLI, LangSmith EU tracing, and Markdown documentation.

## Global Constraints

- Work in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\agent-improvement-planner` on branch `codex/evaluate-improve-planner`, starting from HEAD `14f06b7`.
- Preserve `tests/test_diagnostic_planner_deepseek_length.py` exactly as the existing untracked characterization test. Do not edit it, stage it, or commit it.
- The current documentation task changes no production code and no tests. The future implementation tasks below are the approved scope for a later execution pass.
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

1. Task 1 establishes typed provider telemetry and the nonretryable output-limit boundary.
2. Task 2 consumes that boundary, preserves causes, and adds target taxonomy plus sanitized ReAct schema diagnostics.
3. Task 3 consumes the provider protocol and adds the planner-final-only token override.
4. Task 4 consumes the failure taxonomy and preserves judge diagnostics and URLs safely.
5. Task 5 verifies the whole branch and obtains a Sol/high review before any paid call.
6. Task 6 runs the human-confirmed focused 8192 experiment and conditionally the full controlled dataset.
7. Task 7 chooses exactly one evidence-backed branch: a focused 16384 experiment when length persists, or residual ReAct/judge diagnosis when it does not.

Tasks 1–4 are code tasks. Tasks 5–7 are verification, review, experiment, and diagnosis gates; they do not authorize unrelated implementation.

## Task 1: Typed output-limit failure and safe provider telemetry

**Files:**

- Modify `src/deep_research/providers/contracts.py`.
- Modify `src/deep_research/providers/deepseek_provider.py`.
- Modify `src/deep_research/providers/openai_provider.py` only where the shared provider contract or an already-supported SDK output-limit signal requires parity.
- Add focused coverage in `tests/test_deepseek_provider.py`.
- Extend neighboring coverage in `tests/test_openai_provider.py` and `tests/test_retry_policy.py` where the shared exception contract applies.
- Read only `tests/test_diagnostic_planner_deepseek_length.py`; preserve it byte-for-byte and leave it untracked.

**Interfaces and behavior:**

1. Add a typed `ProviderResponseTelemetry` model in `providers/contracts.py` with `finish_reason`, `configured_max_tokens`, existing typed `TokenUsage`, `request_attempt`, and optional `structured_attempt`. Enforce positive attempt/cap values and forbid unrecognized fields using the project’s existing Pydantic conventions.
2. Add `ProviderOutputLimitError` as a nonretryable `ProviderResponseError` subclass. Give it a static safe message and a typed telemetry attribute. Do not include response text, prompt text, request payloads, URLs with credentials, or exception strings.
3. Extend provider response errors with a safe failure category and optional integer HTTP status code so adapter-created transport and HTTP failures can be classified later without parsing unbounded SDK messages. Preserve existing retryability behavior.
4. In DeepSeek, count actual request invocations around the existing `with_retries` operation. Parse usage before `_choice_text` rejects the response. Map `finish_reason=length` to `ProviderOutputLimitError` with the configured cap and attempt metadata. Leave other non-stop responses as safe non-output-limit response errors.
5. Pass the structured attempt number into the same path for `complete_structured`. Preserve the existing two-attempt repair behavior, but make the eventual typed output-limit error identify the request and structured attempt.
6. Record only safe finish, cap, usage, and attempt metadata on the provider span. Keep exact response and request content out of spans and artifacts.
7. For OpenAI, map only a documented SDK output-limit signal when one already exists in the adapter. Do not infer a limit from response text or invent a signal when the SDK does not expose one.

**TDD and handoff sequence:**

- [ ] **RED:** First add tests that construct the existing fake response shape with `finish_reason=length` and typed usage, then assert `ProviderOutputLimitError`, `retryable is False`, exact safe telemetry, and one provider invocation even when the configured retry count is five. Add an assertion that the exception string contains no response or prompt data. Run:

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
- Modify `src/deep_research/evaluation/models.py`.
- Modify `src/deep_research/evaluation/targets.py`.
- Add or update tests in `tests/test_deepseek_provider.py`, `tests/test_agents/test_react.py`, `tests/test_agents/test_planner.py`, `tests/test_evaluation/test_models.py`, and `tests/test_evaluation/test_targets.py`.

**Interfaces and behavior:**

1. Add a bounded `StructuredValidationDiagnostic` with one-based `attempt`, normalized `field_paths`, and an optional stable category. Extend `StructuredOutputError` with an immutable collection of those records. Keep existing safe-summary bounds and remove any path that could contain an input value. A document-level failure uses a stable root path.
2. Preserve diagnostics for both structured attempts. A pair of successful HTTP responses followed by two `ReActDecision` validation failures must yield two diagnostic records, not a provider reachability result.
3. Update ReAct’s provider-decision exception path to record only a safe event and re-raise the original `ProviderError` after the span closes. Keep tool failures recoverable as before. Preserve compatibility for explicitly constructed `ReActRun` values, but do not use a generic terminal-only `provider_error` result to hide a provider cause from the planner.
4. Update Planner wrapping to use operation-specific static context while preserving the original exception as its cause. Do not use reachability language for output-limit or schema failures. Preserve the cause through both ReAct decision and final `ResearchPlanDraft` paths.
5. Update evaluation classification to walk the full cause chain and check specific types before generic provider errors. Use this exact target mapping: `output_limit` at provider stage; `schema_output` at validation stage; `provider_timeout`, `provider_rate_limit`, `provider_transport`, `provider_http`, and `provider_response` at provider stage.
6. Add an allow-listed safe details projection to evaluation failure artifacts. Output-limit details contain finish, cap, typed usage, and attempts. Schema details contain attempt numbers and field paths. Transport/HTTP details contain type, retryability, and optional status code. Never serialize exception objects or `str(error)` from a provider cause.
7. Make judge classification consume the same typed causes while reserving evaluator-prefixed reasons for Task 4: `judge_output_limit`, `judge_schema_failure`, `judge_transport`, `judge_http`, and fallback `judge_provider_failure`.

**TDD and handoff sequence:**

- [ ] **RED:** Add a provider structured-output test asserting two sanitized diagnostic records with attempt numbers and field paths, with no raw provider data. Add ReAct and Planner tests asserting the original provider cause is reachable through `__cause__`. Add target-classifier tests for each taxonomy row and assert that output-limit and schema failures are not labeled reachability failures. Run:

  ```powershell
  python -m pytest -q tests/test_deepseek_provider.py tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_evaluation/test_targets.py -k "diagnostic or schema or provider or classify"
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

- [ ] Append the Task 2 evidence to the fix log, including the two-response/two-validation case, the safe taxonomy rows, and any known unresolved judge URL gap. Do not paste provider output or exception text.
- [ ] Review all serialized fields and trace metadata for secrets, prompts, response content, evaluator inputs, and hidden reasoning. Commit the code, tests, and fix-log update with a scoped message such as `fix: classify planner provider failures safely`.

**Exit criteria:** Cause chains survive ReAct and Planner, structured diagnostics contain only field paths and attempts, and offline artifacts distinguish all target failure families without raw provider data.

## Task 3: Operation-specific planner-final max-token configuration

**Files:**

- Modify `src/deep_research/utils/config.py`.
- Modify `config.yaml`.
- Modify the `StructuredCompleter` protocol in `src/deep_research/agents/base.py`.
- Modify `src/deep_research/agents/planner.py`.
- Modify `src/deep_research/providers/deepseek_provider.py` and `src/deep_research/providers/openai_provider.py`.
- Update test support in `tests/agent_fakes.py`.
- Add or update tests in `tests/test_config.py`, `tests/test_agents/test_planner.py`, `tests/test_deepseek_provider.py`, `tests/test_openai_provider.py`, and `tests/test_evaluation/test_config.py`.

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

- Modify `src/deep_research/evaluation/models.py`.
- Modify `src/deep_research/evaluation/judging.py`.
- Modify `src/deep_research/evaluation/runner.py` only where `JudgeFeedback` diagnostics and URL fields must be carried into `results.json`.
- Modify `src/deep_research/evaluation/reporting.py` only where safe diagnostics must be rendered.
- Add or update tests in `tests/test_evaluation/test_judging.py`, `tests/test_evaluation/test_judge_visibility.py`, `tests/test_evaluation/test_models.py`, `tests/test_evaluation/test_runner.py`, and `tests/test_evaluation/test_reporting.py`.

**Interfaces and behavior:**

1. Add a typed, safe evaluator diagnostic model in `evaluation/models.py` with a stable kind, optional attempt number, normalized field paths, and no free-form provider or evaluator message. Add a collection of these diagnostics to `JudgeFeedback` using the project’s existing model collection convention.
2. Preserve the existing `JudgeFeedback.evaluator_trace_url` and `evaluator_source_url` fields. Make the traceable judge callback accept the installed LangSmith `run_tree` injection and capture `run_tree.get_url()` when available. Validate and retain only a URL string.
3. Preserve a source URL only when it is supplied by the evaluator/source metadata already available to the integration. If the SDK does not expose one, leave `evaluator_source_url` as `None` and retain a stable safe unavailable state. Never manufacture a URL from an input, prompt, or exception.
4. In `JudgeEvaluator._not_run` and `run_judge`, map typed causes to evaluator-prefixed reasons and carry safe diagnostics and available URLs into `JudgeFeedback`. Preserve `judge_provider_failure` for the unresolved generic case.
5. Keep judge comments, metadata, artifacts, and reports free of `JudgeInput`, prompts, provider response text, evaluator inputs, secrets, and hidden reasoning. A missing URL is an infrastructure diagnostic, not evidence that judging passed.
6. Add artifact round-trip tests proving the trace/source fields survive serialization and that unavailable source URLs remain explicitly unavailable.

**TDD and handoff sequence:**

- [ ] **RED:** Add fake-trace tests where a supplied `run_tree` exposes a safe URL, plus tests for a missing source URL, typed output-limit/schema causes, and generic judge provider failure. Assert URLs and safe diagnostics survive `JudgeFeedback` and artifact round trips, while evaluator inputs and provider content do not appear. Run:

  ```powershell
  python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_models.py -k "diagnostic or url or provider_failure or schema"
  ```

  Expected RED evidence: the current judge path returns missing diagnostics and leaves evaluator URL fields unset.

- [ ] **GREEN:** Implement safe trace/source capture, evaluator-prefixed taxonomy, typed diagnostics, and runner/reporting propagation. Rerun the focused tests until they pass.
- [ ] Run neighboring runner and reporting tests:

  ```powershell
  python -m pytest -q tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_models.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_reporting.py
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

## Task 5: Complete offline verification and Sol/high whole-branch review

**Files:**

- Read and review the complete branch diff and history.
- Update only `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md` for the verification ledger.
- Do not change production code or tests during this gate unless a review finding is converted into a separately approved follow-up task.

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
- [ ] Resolve every blocking review finding with a focused TDD fix loop and rerun the affected focused tests, full suite, Ruff, and `git diff --check`. Record the review result and commit any approved fix before proceeding.
- [ ] Append the complete offline verification and review result to the fix log. Do not request human confirmation for a paid call until this gate is green.

**Exit criteria:** Offline code and artifact checks are green, the whole branch has a Sol/high review with no unresolved blocking finding, the fix log contains exact evidence, and the environment is ready for one confirmed focused call.

## Task 6: Human-confirmed focused 8192 experiment and conditional full dataset

**Files:**

- Update `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md` immediately before and after each approved call.
- Preserve generated `results.json` and experiment metadata in their existing output location; do not copy raw prompts, provider responses, evaluator inputs, or secrets into tracked files.

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
- [ ] Resolve the newly created `results.json` using the existing output convention, validate it with `ExperimentResult`, and record only safe metadata: experiment name, status, target failure taxonomy, repetition counts, configuration fingerprint, safe output-limit telemetry, evaluator trace/source URLs when present, and the direct experiment URL.
- [ ] Treat any target failure as a failed focused gate. Continue to the full dataset only if target failures are zero, not merely when a command exits successfully.

**Conditional full controlled dataset:**

- [ ] If and only if the focused run has zero target failures, obtain the required immediate human confirmation for the full controlled dataset, keeping `AGENTS_PLANNER_FINAL_MAX_TOKENS=8192` and every other variable unchanged.
- [ ] Run the existing full controlled planner command without `--case`, using the same target/judge reasoning profiles and prefix family. Validate exactly the frozen three cases and three repetitions per case. Record status, experiment URL, result path, safe taxonomy counts, and whether evaluator URLs were available.
- [ ] If the focused run has any target failure, do not run the full dataset. Record the failure category and stop at the evidence gate.
- [ ] Append both the preflight and post-run entries to the fix log, including issue, evidence, RED/GREEN state where applicable, code/review status, direct links/results, and a statement that raw data was not recorded.
- [ ] Clear the per-run environment override after the evidence is recorded:

  ```powershell
  Remove-Item Env:AGENTS_PLANNER_FINAL_MAX_TOKENS -ErrorAction SilentlyContinue
  ```

**Exit criteria:** The focused experiment is human-confirmed and documented. The full controlled dataset exists only when target failures are zero; otherwise Task 7 uses the observed category to select one next branch.

## Task 7: Conditional 16384 experiment or residual ReAct/judge diagnosis

**Files:**

- Update `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`.
- Read the safe `results.json` and available LangSmith trace/evaluator metadata.
- Do not edit unrelated production code or tests under this conditional task.

**Branch A — output limit persists:**

- [ ] Choose this branch only when Task 6 contains a target-side `output_limit` at 8192, with safe telemetry showing the configured cap was reached. Do not choose it for a schema, transport/HTTP, judge, or generic failure.
- [ ] Obtain immediate human confirmation again immediately before execution. Set only:

  ```powershell
  $env:AGENTS_PLANNER_FINAL_MAX_TOKENS = '16384'
  ```

- [ ] Run one focused `focused-decomposition` controlled call with target reasoning `max`, unchanged judge budget, unchanged dataset/rubric, unchanged retry/timeout/concurrency, and a distinct experiment prefix. Record the direct experiment URL and safe result metadata. Do not run the full dataset in this conditional step.
- [ ] Clear the environment override and append the evidence. State whether the typed output-limit event disappeared, persisted, or changed category. A successful request without preserved diagnostics is not a remediation pass.

**Branch B — output limit does not persist:**

- [ ] Choose this branch when the 8192 result has no target output-limit event but shows a residual ReAct schema, transport/HTTP, or judge failure, or when the unresolved `judge_provider_failure` still lacks evaluator diagnostics/URLs.
- [ ] Do not run 16384. Use only the safe artifact fields and available trace/evaluator URLs to identify the next diagnosis. If a code correction is required, stop and request a separately approved scope; do not expand this plan silently.
- [ ] Append the diagnosis, evidence links, missing-evidence statement, and next decision to the fix log.

The two branches are mutually exclusive. Do not run both without new evidence and a separate approval.

## Final documentation and commit gate

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
