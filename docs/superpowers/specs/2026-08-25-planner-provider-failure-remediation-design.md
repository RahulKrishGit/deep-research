# Planner Provider-Failure Remediation Design

**Date:** 2026-08-25
**Status:** Approved for implementation planning
**Scope:** Planner/provider failure observability, failure taxonomy, operation-specific token budgeting, and controlled verification

This is documentation fix round 1 after the Sol/high review of the initial documentation commit.

## Goal

Make planner failures diagnosable without exposing provider data, preserve the original failure causes across the ReAct and planner layers, and test the smallest safe change that can correct the final-plan output limit. The remediation must distinguish an output limit, invalid structured output, transport or HTTP failure, and judge failure. It must also make each campaign step auditable in the planner fix log.

This design covers the implementation and the controlled experiment. The current documentation commit does not change production code or tests and does not run a paid provider evaluation.

## Execution routing and provenance

- Every production, test, or documentation write, including every fix wave, is performed by `gpt-5.6-luna` at `max` reasoning effort.
- Every analysis, task review, scoped re-review, integration review, and final whole-branch review is performed by `gpt-5.6-sol` at `high` reasoning effort. Do not rely on inherited model or effort defaults.
- Diagnosis/document-review base is `14f06b7` only. The initial documentation commit is `5a50bfd`. Implementation begins from the reviewed head containing this documentation fix round, whose exact SHA must be recorded before Task 1 starts; implementation must not restart from either earlier SHA.
- This plan explicitly authorizes a scoped Luna/max TDD review-fix loop for a Sol/high finding in Tasks 1–4 or the final review finding. Each loop receives a Sol/high scoped re-review, with a maximum of five loops for this campaign and no unrelated scope expansion.

The implementation topology is dependency-safe: Task 1 completes before Task 2 starts; Task 2 completes and receives Sol/high review before Task 3 and Task 4 branch in separate isolated worktrees from the same Task 2 SHA. Task 2 exclusively owns shared `evaluation/models.py` and taxonomy contracts. Task 4 consumes those contracts and does not modify them. Integration merges Task 3, then Task 4, resolves ordinary conflicts, reruns Task 4 focused and neighboring tests, and receives Sol/high integration review.

## Verified evidence

The following facts are the evidence baseline for this design.

| Observation | Consequence |
| --- | --- |
| Temporary instrumentation of the live target call returned `finish_reason=length`, `completion_tokens=4096`, `prompt_tokens=741`, and `total_tokens=4837`. `completion_tokens` exactly matched the configured `max_tokens=4096`. | The observed planner failure is an output-limit event at the configured cap, not evidence of provider unreachability. |
| The instrumented call completed in 33–37 seconds under a 60-second timeout. | A timeout is not the explanation for this target event. |
| `_choice_text` runs after `with_retries` and turns every non-`stop` finish into a generic nonretryable `ProviderResponseError`. With `retry_count=5`, this deterministic failure therefore sends one request. | The response-limit signal is lost at the provider boundary and the retry count does not create five identical calls. |
| Planner wrapping and evaluation classification currently present output-limit and schema failures as provider reachability failures. | Failure messages and aggregate evaluation status cannot identify the corrective action. |
| A separate ReAct structured-output failure produced two successful HTTP responses and two `ReActDecision` validation failures. Invalid field paths were not preserved. | The implementation needs bounded schema diagnostics for both attempts, not a transport diagnosis. |
| A separate `judge_provider_failure` remains unresolved because evaluator diagnostics and URLs are missing. | Judge failures remain a gated infrastructure problem until safe evaluator diagnostics are retained. |
| The installed coding Harness uses an adapter default `maxTokens=256000`, subject to overrides, streaming, and an explicit `length => max-tokens` mapping. | Harness behavior is a parity reference only. The larger default alone must not be claimed to cause success. |
| Exact finish and usage metadata came from temporary instrumentation and is not durable in `results.json`. | The fix must persist a safe subset of this metadata in typed failure details and artifacts. |
| The current offline baseline is `1801 passed, 1 deselected, 2 known dependency warnings`; Ruff is clean and `git diff --check` is clean. | These are the verification starting values for the implementation campaign. They must not be silently replaced by a live-run result. |

No prompt text, provider response content, secret, or hidden chain-of-thought belongs in an artifact, log, trace, or document.

## Root-cause model

The failure path has four distinct boundaries:

1. **Provider response boundary.** DeepSeek supplies a safe finish reason and usage record, but `_choice_text` currently collapses `length` and all other non-stop finishes into one generic exception after retry handling has completed.
2. **Structured-output boundary.** A successful HTTP response can still fail `ReActDecision` validation. The current summary is bounded, but the final exception does not retain attempt number and field paths as typed diagnostics.
3. **Agent and planner boundary.** ReAct currently records a generic provider event and stops the run without preserving a re-raiseable cause. Planner wrapping then uses reachability language for failures that may be output-limit or schema failures.
4. **Evaluation and judge boundary.** Evaluation classification walks causes but sees only generic provider errors. Judge not-run results preserve a broad category but omit the safe diagnostics and evaluator URLs needed to investigate `judge_provider_failure`.

The remediation keeps these boundaries separate. It does not turn a successful HTTP response with an invalid body into a transport failure, and it does not turn a deterministic truncation into a retry storm.

## Design

### A. Typed output-limit failure and safe provider telemetry

Add a provider-agnostic `ProviderOutputLimitError` under the existing `ProviderResponseError` hierarchy. It is nonretryable and carries a typed, safe telemetry record rather than raw response data.

Use a stable finite `FinishReasonCategory` type with exactly these values: `stop`, `length`, `content_filter`, `tool_calls`, `insufficient_system_resource`, and `other`. The provider’s raw finish-reason string is normalized to this type and is never retained in telemetry, spans, artifacts, or exception messages.

The telemetry record contains exactly these fields:

- `finish_reason_category: FinishReasonCategory` — the normalized allow-listed category;
- `configured_max_tokens: int` — the cap used for this operation;
- `usage: TokenUsage` — the existing typed usage model;
- `request_attempt: int` — the one-based request number actually sent inside the retry operation;
- `structured_attempt: int | None` — the one-based structured-output repair attempt, when applicable.

The exception message is a static, provider-safe message. It must not include a response fragment, prompt, request body, URL query, API key, or model reasoning. The exception must expose the telemetry through a typed attribute so the classifier and artifact projection do not parse `str(exception)`.

Normalize a provider value only when it is a string of at most 64 characters; strip and case-normalize it, then map exact allow-listed values. Map `None`, non-strings, empty values, control-heavy values, and strings longer than 64 characters to `other` without logging or storing the original value. Only the `length` category raises `ProviderOutputLimitError`. The implementation must include adversarial tests for every known category, unknown values, oversized values, non-string values, and proof that the raw value does not appear in the error, span, or artifact.

For DeepSeek, parse usage and the normalized finish category before requiring `stop`. When the category is `length`, raise `ProviderOutputLimitError` with the configured cap and the actual request/structured attempt. Other non-stop categories remain safe `ProviderResponseError` values with an explicit non-output-limit category. The provider span may record only the allow-listed category, configured cap, usage, and attempt numbers.

The request-attempt counter is local to the existing `with_retries` operation. It increments only when the provider request is actually invoked. A deterministic output-limit error is raised after the first response and is not sent back through retry handling.

The OpenAI adapter must preserve the same safe contract when its SDK exposes a known output-limit signal. If the adapter has no equivalent finish signal for a particular response type, it must not infer one from response text.

### B. Sanitized structured-validation diagnostics

Extend `StructuredOutputError` with a bounded collection of typed `StructuredValidationDiagnostic` records. Each record contains:

- `attempt: int`;
- `field_paths`, a tuple of normalized schema-path strings only;
- an optional stable error category such as `missing`, `type`, `value`, or `document`.

The implementation may retain a schema name if it is already available as safe metadata. It must not retain raw provider output, invalid field values, full validation messages that echo values, or a serialized exception.

For each structured attempt, normalize Pydantic locations into bounded field paths. A JSON/document-level failure uses a stable root path. Limit the number and length of paths using the existing provider-summary bounds. The repair instruction may use only these sanitized paths and stable categories. After the second failed attempt, the final `StructuredOutputError` carries diagnostics for both attempts, including the attempt number even when the two HTTP calls both succeeded.

The same diagnostic contract is used for ReAct and planner structured calls. This makes the separate observation of two successful HTTP responses and two `ReActDecision` validation failures representable without changing it into a reachability error.

### C. Exception propagation and failure taxonomy

Preserve the original exception cause through each boundary:

- ReAct records a safe event and re-raises the original `ProviderError` from the decision call after the span is closed. Tool execution failures retain their existing recoverable behavior.
- Planner wraps a provider failure with operation context while preserving the original exception as its cause; it must not replace the cause with a reachability-only message. The final ResearchPlanDraft request and the ReAct decision path have distinct operation labels.
- Evaluation classification walks the cause chain and checks specific subclasses and safe provider categories before generic `ProviderError`.
- Serialized evaluation messages are stable, allow-listed summaries. The exception cause remains available to in-process handling but is never serialized into `results.json`.

The target taxonomy is:

| Cause | `FailureStage` | Safe reason | Safe details |
| --- | --- | --- | --- |
| `ProviderOutputLimitError` | `provider` | `output_limit` | Finish category, configured cap, usage, request attempt, and structured attempt |
| `StructuredOutputError` | `validation` | `schema_output` | Attempt numbers and field paths only |
| Provider timeout or rate limit | `provider` | `provider_timeout` or `provider_rate_limit` | Type and safe retryability only |
| Provider transport error | `provider` | `provider_transport` | Type and safe retryability only |
| Provider HTTP error | `provider` | `provider_http` | Type and allow-listed status code, when available |
| Other provider response error | `provider` | `provider_response` | Type and safe retryability only |

The provider response error contract therefore needs a safe category and optional status code for adapter-created transport and HTTP errors. It must not use an unbounded SDK exception string as the category. The output-limit artifact field is `finish_reason_category`, never an arbitrary provider string.

Judge failures use a separate evaluator prefix so a target failure cannot be mistaken for a judge failure:

- `judge_output_limit` for a judge output-limit cause;
- `judge_schema_failure` for a judge structured-validation cause;
- `judge_transport` or `judge_http` for the corresponding provider cause;
- `judge_provider_failure` as the fallback for an unclassified judge provider error.

The existing unresolved `judge_provider_failure` remains a valid result. The remediation makes it more actionable; it does not convert missing evaluator evidence into a quality pass.

### D. Operation-specific planner-final token budget

Keep the global `LLMConfig.max_tokens` default and YAML value at `4096`. Add an operation-specific `AgentRuntimeConfig.planner_final_max_tokens` setting with a default of `4096`, an environment override, and inclusion in the effective configuration fingerprint.

Extend the existing structured-completion protocol with an optional per-call `max_tokens` override. The providers apply it only to the request field for that call (`max_tokens` for DeepSeek and `max_output_tokens` for OpenAI). The default remains the global configuration when the override is absent.

Only the planner’s final `ResearchPlanDraft` request passes `planner_final_max_tokens`. ReAct decision requests pass no override and remain at global `4096`. Judge requests pass no override and remain at their configured `4096`. This isolates the experiment to the final planning operation.

The first experiment uses `planner_final_max_tokens=8192` and target reasoning effort `max`. Model, dataset, rubric, target/judge configuration, global token cap, concurrency, timeout, retry policy, tool budgets, and all other variables remain frozen. Do not claim that the installed Harness’s `maxTokens=256000` default caused success; the only tested change is the planner-final budget.

Use `16384` only when the target still has an output-limit failure at `8192`. If the target failure is no longer an output limit but a residual ReAct or judge failure remains, diagnose that residual path instead. Do not run the 16384 experiment and residual diagnosis concurrently without evidence that both are required.

### E. Retry boundary

The existing retry policy remains for explicitly retryable timeout, rate-limit, transport, and HTTP failures. `ProviderOutputLimitError` and `StructuredOutputError` are deterministic response failures and are nonretryable. Identical truncated calls must not be blindly retried. Adaptive retries that change the token budget or otherwise alter a request are out of scope unless separately approved.

### F. Fix-log and experiment ledger

Every implementation or experiment task appends a clearly dated entry to `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`. Each entry has these fields:

1. issue or hypothesis;
2. evidence and exact command/result, with a direct live experiment or trace link when one exists;
3. RED status and focused test evidence;
4. GREEN status and neighboring/full verification evidence;
5. code/review status;
6. live experiment result and next gate, or an explicit statement that the call was not run.

The ledger records missing URLs as missing evidence. It never substitutes a secret, prompt, provider response, or hidden reasoning for a link.

### G. Human confirmation and execution boundary

Paid DeepSeek or LangSmith calls require immediate human confirmation immediately before the command that executes them. Confirmation must occur after offline verification and review, not at plan creation time. The controlled campaign uses the approved baseline model/configuration, no live tier, and no other deep-research agents. The judge is an evaluator for the selected planner run, not an additional target agent.

Use the configured EU LangSmith endpoint for approved tracing. Do not print environment values or include credentials in commands, logs, artifacts, or documentation.

## Safe artifact projection

The provider exception remains the source of truth in process. Evaluation artifacts receive only an allow-listed projection. The proposed `EvaluationFailure` details contain one of the following safe shapes:

- output-limit: `failure_kind`, `finish_reason_category`, `configured_max_tokens`, typed usage, `request_attempt`, and optional `structured_attempt`;
- schema output: `failure_kind`, attempt numbers, and normalized field paths;
- transport or HTTP: `failure_kind`, exception type, retryability, and optional integer status code;
- judge: the evaluator-prefixed reason, safe diagnostics, `evaluator_trace_url`, and `evaluator_source_url` only when directly supplied by the evaluator integration. Unsupported or missing source URLs remain `None`; there is no source-URL derivation or fallback.

Do not serialize `BaseException`, `__cause__`, prompt messages, request payloads, response text, tool inputs, evaluator inputs, or chain-of-thought. Safe URL fields contain only validated URLs returned by the tracing/evaluation integration; when a URL is unavailable, retain `None` and the stable unavailable state rather than fabricating a link.

## Controlled experiment gates

The experiment sequence is intentionally one-dimensional:

1. Finish the typed failure, propagation, taxonomy, operation-specific configuration, and evaluator-diagnostic implementation.
2. Run focused offline RED/GREEN and neighboring tests, then the full suite once; run Ruff and `git diff --check`.
3. Obtain a Sol/high whole-branch review. This is a review-only activity and does not call a provider or launch another agent. If it finds a scoped issue, Luna/max may run the affected Task 1–4 TDD fix loop, followed by Sol/high scoped re-review, up to the campaign-wide five-loop maximum.
4. Immediately before execution, obtain human confirmation for the paid focused run at `8192` with target reasoning `max`.
5. If and only if focused target failures are zero, every expected judge evaluation completed, every judge result is successfully scored, and there are zero judge/evaluator failures, run the full controlled dataset at the same `8192` setting. A missing judge result, `judge_not_run`, evaluator error, or any judge failure blocks the full run and routes to residual diagnosis. Record links and safe results.
6. If output-limit failures persist at `8192` and the focused run has no judge/evaluator failure, obtain a new immediate confirmation and run one focused `16384` experiment with every other variable frozen. If output-limit failures do not persist, or any judge/evaluator failure exists, investigate the remaining ReAct or judge evidence instead. Never combine these branches without evidence.

The campaign is not successful merely because a request completes. Success requires a typed and correctly classified result, preserved safe diagnostics, no leakage, unchanged ReAct/judge budgets, every expected judge evaluation successfully scored with no judge/evaluator failure, and documented live evidence.

## Non-goals

- Changing the global 4096 token default.
- Increasing ReAct or judge budgets as part of the planner experiment.
- Adding adaptive retry or retrying identical truncated requests.
- Treating the coding Harness cap as proof of production behavior.
- Running a live tier, broad end-to-end campaign, or any paid call without immediate confirmation.
- Capturing raw model output, prompts, evaluator inputs, secrets, or hidden chain-of-thought.

## Acceptance criteria

The implementation is ready for the controlled experiment when:

- the output-limit exception has typed safe telemetry and is nonretryable;
- structured validation failures retain bounded field paths and attempt numbers without raw provider data;
- ReAct and planner exception causes survive to evaluation classification;
- output-limit, schema-output, transport, HTTP, and judge failure categories are distinct in safe artifacts;
- only final `ResearchPlanDraft` uses the operation-specific budget, with 8192 as the first experiment and 16384 conditional on persistent length;
- evaluator diagnostics and URLs are preserved when available and remain explicitly unavailable when not exposed;
- the full controlled dataset gate requires zero focused target failures, all expected judge evaluations completed and successfully scored, and zero judge/evaluator failures;
- all writes use Luna/max and all analysis/review gates use Sol/high, with scoped review-fix loops capped at five rounds;
- all focused, neighboring, and full offline checks are green, Ruff is clean, and `git diff --check` is clean;
- the fix log has a dated entry for every task and experiment;
- the human confirmation gate and secret-safety rules are documented and followed.
