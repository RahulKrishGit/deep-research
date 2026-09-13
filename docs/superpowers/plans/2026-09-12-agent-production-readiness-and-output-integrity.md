# Agent Production Readiness and Output Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent fail closed at the provider boundary, prevent malformed JSON or tool markup from being accepted as a valid decision, retain the effective Critic/Judge prompt-conformance techniques across all structured operations, and establish evidence-backed production readiness through offline and separately authorized live gates.

**Architecture:** Keep the shared provider-native ReAct transport and tool-free structured-output path, but harden them as separate contracts. Native ReAct accepts exactly one typed allow-listed tool call or legitimate final text and rejects mixed, unknown, legacy-JSON, fenced, or DSML shapes. Structured calls keep provider JSON Schema plus one bounded repair, while prompts carry the proven JSON keyword, unambiguous Markdown hierarchy, one reply contract, schema-valid examples, and non-contradictory scoring guidance. Content-free origin and attempt telemetry makes shape failures, SDK failures, and repaired JSON distinguishable before literal offline and live release gates are applied.

**Tech Stack:** Python 3.11+, Pydantic v2, OpenAI Python SDK 2+, DeepSeek Chat Completions and Responses-compatible structured output, OpenAI Responses, pytest/pytest-asyncio, Ruff, LangSmith evaluation harness.

**Spec:** `docs/superpowers/specs/2026-09-12-shared-native-react-tools-and-prompt-conformance-design.md`

**Remediation evidence:** `docs/superpowers/2026-09-12-shared-native-react-observation-report.md`, `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`, and the independent review of candidate `c695667`.

**Provider basis:** DeepSeek's official [JSON Output guide](https://api-docs.deepseek.com/guides/json_mode/) requires the prompt to contain the word JSON and recommends an example of the desired JSON format. It does not require two examples. Therefore this plan uses one complete schema-valid example by default and a second only when the model must see an opposite semantic/scoring case.

## Readiness definition

This plan does not promise that a probabilistic provider will never emit malformed text. It establishes the production guarantee the repository can control:

1. malformed output never crosses the provider boundary as a validated structured result or executable tool decision;
2. one schema-invalid structured attempt may be repaired once, but repair use is measured and repair exhaustion is a typed, content-free failure;
3. DSML, Markdown-fenced decisions, legacy `ReActDecision` JSON, mixed text/tool envelopes, unknown output items, and malformed native calls are rejected and never executed;
4. all release canaries must show zero malformed accepted outputs, zero structured repairs, zero repair exhaustion, zero native-envelope rejection, and zero provider fallback;
5. production readiness is granted per agent only after its own three-repetition canary passes. The system is ready only after all six agents pass.

## Global constraints

- Work only in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity` on `codex/cross-agent-planner-fix-parity`. Verify branch, HEAD, upstream divergence, and tracked status before every task. Preserve the user-owned untracked `.deepseek-runs/` and `tools/` directories.
- At the start of each PowerShell session set `$readinessRoot = (Get-Location).Path`, `$readinessPython = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe'`, and `$env:PYTHONPATH = Join-Path $readinessRoot 'src'`. Verify `deep_research.__file__` resolves inside `$readinessRoot`.
- Use TDD for every production change: add a focused failing test, run it and confirm the intended failure, implement the minimum change, rerun the focused test, then run the task gate.
- Keep `deepseek-v4-flash`, thinking enabled, configured reasoning efforts, temperature behavior, all `32768` output budgets, `max_iterations=5`, `tool_budget=10`, HTTP retry counts, the one structured repair, routing, cases, rubrics, weights, score bands, and the `0.75` live threshold unchanged.
- Do not return to prompt-encoded ReAct tool selection. DeepSeek native ReAct remains Chat Completions with real function definitions and `tool_choice="auto"`; OpenAI remains Responses with real function tools and `tool_choice="auto"`.
- Native ReAct never uses JSON Schema, `response_format`, or the structured-repair loop. Tool-free planning, extraction, scoring, verification, synthesis, Critic review, and Judge calls remain structured-output operations.
- Never execute a tool call decoded from ordinary text, DSML, XML, Markdown, a code fence, or a legacy `ReActDecision` object. Only a typed native tool-call field naming one allow-listed tool can request execution.
- Never retain raw prompts, provider responses, reasoning, tool arguments, invalid JSON, exception text, or secrets in errors, traces, probe records, artifacts, or review documents. Tests use harmless sentinels and assert object reachability as well as string surfaces.
- Preserve the current effective Critic and Judge prompt techniques: the literal `JSON object` instruction, one authoritative reply-format section, clean Markdown hierarchy, complete schema-valid weak/strong examples, the full score range, and non-contradictory weighting and band rules.
- Every non-scoring structured operation carries exactly one compact valid example unless an opposite semantic case is needed. Scoring/classification operations may carry one weak/negative and one strong/positive example. No prompt may carry more than two. A negative example is a valid weak, contradicted, or insufficient-evidence result, never malformed JSON.
- Synthetic examples use reserved `.example.test` URLs, are explicitly illustrative, validate against their real Pydantic schema, and cannot pass source provenance filters when copied into a real answer.
- Do not edit `JUDGE_SYSTEM_PROMPT`, `JUDGE_PROMPT_TEMPLATE`, `JudgeVerdict`, or the Critic review/calibration semantics unless a focused failing conformance test or a new predeclared measurement requires it. A Judge prompt change moves the pinned Judge fingerprint and requires a new Judge canary for every affected agent evaluation.
- `agent_prompt_fingerprint` hashes the shared `agents/prompts.py` module. Editing that shared module moves every agent's target prompt fingerprint and invalidates all six target canaries. Editing one agent's own prompt invalidates that agent's canary. Record every old/new fingerprint rather than treating the fingerprint as attributable to one constant.
- A semantic evaluation-case change requires a case-version bump and invalidates that case's dataset identity and canary. This remediation does not change cases.
- Tasks 1-7 are offline. No DeepSeek, OpenAI, Tavily, LangSmith, evaluation, or other paid/network call is authorized by this plan. Tasks 8 and 9 stop before each batch, state the exact request ceiling and services, and obtain explicit authorization for that batch.
- The three live-tier ledger tests in `tests/test_evaluation/test_runner.py` and `tests/test_evaluation/test_targets.py` are offline tests: their fixtures inject inert credential strings, fake search clients, fake model providers, and fake embeddings. Real shell credentials are neither required nor evidence that these tests are valid. Never exclude these tests or load real credentials to make the offline gate pass.
- Preserve every historical artifact. The two earlier native-shape batches remain diagnostic evidence only: batch 1 failed its literal gate, and batch 2 was an unplanned rerun with an ambiguous `ProviderResponseError`. Neither may be relabelled as the release gate.
- Stage exact paths only. End each implementation task with focused tests, Ruff on touched Python, `git diff --check`, and an independently reviewable commit.

---

### Task 1: Restore the structured-repair baseline to the current Judge contract

**Files:**
- Modify: `tests/test_deepseek_provider.py`
- Verify only: `src/deep_research/evaluation/models.py`
- Verify only: `src/deep_research/providers/deepseek_provider.py`

**Interfaces:**
- Preserves: `JudgeVerdict(extra="ignore")`, the 2,000-character prompt target, the 20,000-character local safety bound, and one structured repair.
- Produces: a zero-failure offline baseline whose repair tests exercise invalid data under the current schema rather than obsolete constraints.

- [ ] **Step 1: Reproduce and record the exact four failures**

Run:

```powershell
& $readinessPython -m pytest --lf -q --tb=short
```

Expected at `c695667`: exactly these four failures:

```text
test_deepseek_judge_responses_repair_succeeds_once
test_deepseek_judge_responses_repair_exhaustion_is_typed_and_safe
test_deepseek_structured_repair_guides_string_bounds[long-value]
test_deepseek_structured_repair_preserves_prior_diagnostics
```

The cause is test drift, not evidence that the repair loop skipped current validation: Judge top-level additions are deliberately ignored, and 2,001 characters are deliberately below the 20,000-character local backstop.

- [ ] **Step 2: Replace obsolete invalid fixtures with values invalid under the current contract**

Add a strict local test schema so provider-repair mechanics are not coupled to Judge calibration:

```python
class StrictBoundedRationale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=20)
```

Use it for the generic root-extra, string-bound, and preserved-diagnostic tests. For the two `DeepSeekJudgeProvider` Responses tests, make the first reply omit required `rationale`; for exhaustion, make the second reply exceed `JUDGE_RATIONALE_SCHEMA_MAX` by one character. Keep the existing assertions that the repair request and public error contain only category and field-path diagnostics, never discarded content.

- [ ] **Step 3: Pin the intentional Judge tolerance separately**

Add focused tests proving all three current behaviors:

- `test_judge_ignores_one_top_level_note_without_repair` supplies one valid verdict plus an unused top-level note, asserts the validated verdict drops the note, and asserts the Responses client was called once;
- `test_judge_accepts_rationale_above_guidance_below_backstop` supplies a 2,001-character rationale and asserts it remains valid with one Responses call;
- `test_judge_repairs_rationale_above_the_local_backstop` supplies `"x" * (JUDGE_RATIONALE_SCHEMA_MAX + 1)` followed by a valid verdict and asserts exactly two Responses calls and the expected `string_bounds` diagnostic.

These tests prevent a future “fix” from reinstating the already-measured Judge failure mode.

- [ ] **Step 4: Run the focused and full provider gates**

```powershell
& $readinessPython -m pytest tests/test_deepseek_provider.py -q -k "judge_responses_repair or structured_repair or judge_ignores_one_top_level_note or judge_accepts_rationale or judge_repairs_rationale"
& $readinessPython -m pytest tests/test_deepseek_provider.py tests/test_openai_provider.py -q
```

Expected: PASS. Commit:

```powershell
git add -- tests/test_deepseek_provider.py
git commit -m "test(providers): align repair coverage with judge contract"
```

---

### Task 2: Make SDK failures and local envelope failures unambiguous

**Files:**
- Modify: `src/deep_research/providers/contracts.py`
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `src/deep_research/evaluation/models.py`
- Modify: `src/deep_research/evaluation/failure_taxonomy.py`
- Test: `tests/test_provider_contracts.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_openai_provider.py`
- Test: `tests/test_retry_policy.py`
- Test: `tests/test_agents/test_critic.py`
- Test: `tests/test_agents/test_errors.py`
- Test: `tests/test_evaluation/test_failure_taxonomy.py`
- Test: `tests/test_evaluation/test_judging.py`
- Test: `tests/test_evaluation/test_models.py`

**Interfaces:**
- Preserves: existing `failure_category`, retry behavior, evaluation reasons, and backwards-readable artifacts.
- Produces: a required project-owned `failure_origin` that distinguishes SDK rejection from repository response validation without provider text.

- [ ] **Step 1: Write RED origin-contract tests**

Define and export:

```python
ProviderFailureOrigin: TypeAlias = Literal["sdk", "local_response"]
```

Write tests requiring every `ProviderResponseError` to carry `failure_origin`, requiring `_fresh_provider_error` to copy it, and projecting it into `ProviderFailureSnapshot` and `ProviderFailureDetails`. The artifact-facing field is optional with default `None` so existing v1 artifacts remain readable; every newly constructed provider response error must set it.

Add exact cases:

```python
sdk_error = ProviderResponseError(
    "Provider request failed",
    failure_category="response",
    failure_origin="sdk",
)
local_error = ProviderResponseError(
    "Provider response was malformed",
    failure_category="response",
    failure_origin="local_response",
)
assert provider_failure_snapshot(sdk_error).failure_origin == "sdk"
assert provider_failure_snapshot(local_error).failure_origin == "local_response"
```

Run the focused tests and confirm RED because the origin field does not exist.

- [ ] **Step 2: Add the origin without changing failure semantics**

Change `ProviderResponseError.__init__` to require `failure_origin`. Set:

- `sdk` for SDK timeout-adjacent connection/HTTP/generic SDK translations;
- `local_response` for missing text, malformed usage, incomplete/output-limit response interpretation, native-envelope rejection, and other repository-owned validation;
- no free-form provider reason or SDK type beyond the existing bounded `exception_type`.

Keep the current evaluation reason mapping (`provider_transport`, `provider_http`, `provider_response`) unchanged. Add only the safe origin field to diagnostic projections.

- [ ] **Step 3: Prove the formerly ambiguous case is classifiable**

Add tests that a generic SDK `OpenAIError` becomes `failure_category="response", failure_origin="sdk"`, while a malformed native envelope becomes `failure_category="response", failure_origin="local_response"` for both providers. Also assert the two errors have identical public category/retry/status semantics except for origin.

- [ ] **Step 4: Run contract, provider, and evaluation gates**

```powershell
& $readinessPython -m pytest tests/test_provider_contracts.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_retry_policy.py tests/test_agents/test_critic.py tests/test_agents/test_errors.py tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_judging.py tests/test_evaluation/test_models.py -q
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/providers/contracts.py src/deep_research/providers/deepseek_provider.py src/deep_research/providers/openai_provider.py src/deep_research/evaluation/models.py src/deep_research/evaluation/failure_taxonomy.py tests/test_provider_contracts.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_retry_policy.py tests/test_agents/test_critic.py tests/test_agents/test_errors.py tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_judging.py tests/test_evaluation/test_models.py
git commit -m "fix(providers): distinguish sdk and local response failures"
```

---

### Task 3: Remove SDK exception objects from public traceback reachability

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `src/deep_research/providers/retry.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_openai_provider.py`

**Interfaces:**
- Preserves: typed exception classes, messages, retryability, status codes, origin, and request-attempt counts.
- Produces: public provider errors from which the exact SDK exception object is unreachable through arguments, attributes, cause/context, or traceback-frame locals.

- [ ] **Step 1: Write an identity-based reachability assertion and confirm RED**

Add a bounded test helper that walks only the public exception graph and its traceback frames, never module globals:

```python
def _exception_reaches(error: BaseException, target: object) -> bool:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current is target:
            return True
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if isinstance(linked, BaseException)
        )
        traceback = current.__traceback__
        while traceback is not None:
            if any(value is target for value in traceback.tb_frame.f_locals.values()):
                return True
            traceback = traceback.tb_next
    return False
```

For DeepSeek and OpenAI, inject an SDK `APIConnectionError` carrying harmless request/body sentinels, exhaust repository retries, and assert `_exception_reaches(caught.value, sdk_error) is False`. Confirm RED at the translator frame even though `__cause__` and `__context__` are already `None`.

- [ ] **Step 2: Return translations, then raise outside SDK handlers**

Replace raise-in-translator helpers with pure translators returning fresh project errors:

```python
def _translate_deepseek_error(error: Exception) -> ProviderError:
    if isinstance(error, sdk.APIConnectionError):
        return ProviderResponseError(
            "DeepSeek connection failed",
            retryable=True,
            failure_category="transport",
            failure_origin="sdk",
        )
```

At each SDK call site, assign the translated error inside `except`, then raise it only after leaving the handler and clearing the SDK local. Apply the same pattern to generic `OpenAIError` and to the OpenAI provider. Keep `with_retries` responsible only for retry selection and final chain detachment; update its docstring to remove the now-invalid “translator frame is out of scope” claim.

- [ ] **Step 3: Expand privacy coverage across plain, structured, Judge, and native paths**

Parametrize both providers' tests over `complete`, `complete_structured`, and `complete_react`; include `DeepSeekJudgeProvider`. Assert all of:

```python
assert caught.value.__cause__ is None
assert caught.value.__context__ is None
assert not _exception_reaches(caught.value, sdk_error)
assert request_marker not in repr(_provider_exception_surfaces(caught.value))
assert response_marker not in repr(_provider_exception_surfaces(caught.value))
```

- [ ] **Step 4: Run provider privacy and retry gates**

```powershell
& $readinessPython -m pytest tests/test_deepseek_provider.py tests/test_openai_provider.py -q -k "reach or retain or leak or retries or translates"
& $readinessPython -m pytest tests/test_deepseek_provider.py tests/test_openai_provider.py -q
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/providers/deepseek_provider.py src/deep_research/providers/openai_provider.py src/deep_research/providers/retry.py tests/test_deepseek_provider.py tests/test_openai_provider.py
git commit -m "fix(providers): sever sdk exception traceback state"
```

---

### Task 4: Reject malformed native response shapes before agents see them

**Files:**
- Create: `src/deep_research/providers/native_output.py`
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `src/deep_research/providers/openai_provider.py`
- Test: `tests/test_provider_native_output.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_openai_provider.py`
- Test: `tests/test_agents/test_native_react_boundary.py`

**Interfaces:**
- Consumes: provider-native tool-call objects and ordinary response text.
- Produces: exactly one valid `NativeToolCall` or legitimate final answer; tool-protocol text is a typed local-response failure and is never adapted into `ReActDecision(action="finish")`.

- [ ] **Step 1: Write RED tests for every missed shape**

Add provider-parity tests for:

- typed tool call plus non-blank `message.content`;
- DeepSeek DSML in a `stop` response;
- `<tool_call>` or `<invoke>` markup in a `stop` response;
- a fenced legacy action object;
- a bare object containing `action`, `tool_name`, or `tool_input_json`;
- an OpenAI unknown output item beside a valid function call;
- an OpenAI unknown output item on the final-answer path;
- reasoning items alone or beside one valid function call remain accepted/ignored;
- an OpenAI `message` item is allowed only on a coherent final-answer path.

For every rejection assert `failure_category == "response"`, `failure_origin == "local_response"`, one provider request, no repair, and no sentinel on public surfaces.

- [ ] **Step 2: Add one shared, content-discarding text-shape detector**

In `providers/native_output.py`, add a small deterministic helper returning only a bounded literal:

```python
NativeTextViolation: TypeAlias = Literal[
    "dsml_markup",
    "tool_markup",
    "markdown_fence",
    "legacy_action_json",
]


def native_text_violation(text: str) -> NativeTextViolation | None:
    stripped = text.strip()
    lowered = stripped.casefold()
    if "<|dsml|" in lowered:
        return "dsml_markup"
    if "<tool_call" in lowered or "<invoke" in lowered:
        return "tool_markup"
    if "```" in stripped:
        return "markdown_fence"
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and {
        "action",
        "tool_name",
        "tool_input_json",
    }.intersection(payload):
        return "legacy_action_json"
    return None
```

Never attach the text or parsed payload to an exception. Unit tests must cover case variants and ordinary prose containing harmless braces.

- [ ] **Step 3: Make both provider parsers fail closed**

DeepSeek: require blank/absent content on `finish_reason="tool_calls"`; on `stop`, require no calls, non-blank text, and no `native_text_violation`.

OpenAI: explicitly allow only `reasoning`, `function_call`, and coherent final `message` output items. Reject every unknown item rather than filtering it away. Apply `native_text_violation` to `output_text` before accepting a final answer. Keep the existing exactly-one-call and allow-list checks.

- [ ] **Step 4: Prove the agent boundary never executes or silently finishes malformed text**

In `test_native_react_boundary.py`, feed a fake provider each prohibited final-text shape. Assert the run fails through the typed provider fallback path, records zero tool calls, and does not report a normal `finish` decision.

- [ ] **Step 5: Run parser and agent gates**

```powershell
& $readinessPython -m pytest tests/test_provider_native_output.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_agents/test_native_react_boundary.py -q
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/providers/native_output.py src/deep_research/providers/deepseek_provider.py src/deep_research/providers/openai_provider.py tests/test_provider_native_output.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_agents/test_native_react_boundary.py
git commit -m "fix(providers): reject tool protocol text and mixed envelopes"
```

---

### Task 5: Record successful structured repairs without recording content

**Files:**
- Modify: `src/deep_research/observability/metrics.py`
- Modify: `src/deep_research/observability/tracker.py`
- Modify: `src/deep_research/evaluation/models.py`
- Modify: `src/deep_research/evaluation/targets.py`
- Modify: `src/deep_research/evaluation/judging.py`
- Test: `tests/test_observability_metrics.py`
- Test: `tests/test_observability_tracker.py`
- Test: `tests/test_evaluation/test_models.py`
- Test: `tests/test_evaluation/test_targets.py`
- Test: `tests/test_evaluation/test_judging.py`

**Interfaces:**
- Consumes: existing safe `operation` and `attempt` values already passed to `Tracker.llm_span`.
- Produces: content-free target and Judge attempt summaries sufficient to prove whether a schema repair occurred on a successful run.

- [ ] **Step 1: Write RED attempt-ledger tests**

Extend `TokenUsageMetric` with optional, backwards-compatible fields:

```python
operation: Literal["chat", "structured_output", "react_tool_turn"] | None = None
structured_attempt: int | None = Field(default=None, ge=1, le=2)
```

Add an immutable artifact summary:

```python
class StructuredCallSummary(ContractModel):
    calls: int = Field(default=0, ge=0)
    repaired_calls: int = Field(default=0, ge=0)
    failed_attempts: int = Field(default=0, ge=0)
```

Add `structured_calls: StructuredCallSummary = Field(default_factory=StructuredCallSummary)` to `TargetOutput` and add `structured_attempts: int | None = Field(default=None, ge=1, le=2)` to `JudgeFeedback`. Give `StructuredCallSummary` zero defaults plus a validator requiring `repaired_calls <= calls` and `failed_attempts <= calls + repaired_calls`. Tests must prove old artifacts still validate with defaults and new summaries reject negative or impossible counts.

- [ ] **Step 2: Populate metrics from already-safe span inputs**

In `Tracker.llm_span`, validate `inputs["operation"]` against the finite literal set and `inputs["attempt"]` as 1 or 2. Copy only those integers/literals into `TokenUsageMetric`; never copy input mappings or outputs.

- [ ] **Step 3: Aggregate per repetition and per Judge call**

In `targets._success_output`, filter `tracker.metrics` to the current session and count structured attempt-1 spans as calls, attempt-2 spans as repaired calls, and unsuccessful structured spans as failed attempts.

In `JudgeEvaluator.__call__`, use its dedicated tracker and the current `output.session_id` to obtain the maximum structured attempt after the awaited judge call. Put `structured_attempts` into both scored and not-run metadata and reconstruct it in `_judge_feedback_from_result`.

- [ ] **Step 4: Pin content exclusion and concurrency**

Add tests proving two concurrent session IDs cannot see each other's attempts and that serialized `TargetOutput`, `JudgeFeedback`, and metrics contain no prompt, response, arguments, rationale, or sentinel text.

- [ ] **Step 5: Run observability and evaluation gates**

```powershell
& $readinessPython -m pytest tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_evaluation/test_models.py tests/test_evaluation/test_targets.py tests/test_evaluation/test_judging.py -q
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/observability/metrics.py src/deep_research/observability/tracker.py src/deep_research/evaluation/models.py src/deep_research/evaluation/targets.py src/deep_research/evaluation/judging.py tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_evaluation/test_models.py tests/test_evaluation/test_targets.py tests/test_evaluation/test_judging.py
git commit -m "feat(evaluation): record content-free structured repair counts"
```

---

### Task 6: Lock the effective Critic/Judge JSON prompt rules across every agent

**Files:**
- Verify or modify only on a failing test: `src/deep_research/agents/prompts.py`
- Verify or modify only on a failing test: `src/deep_research/agents/planner.py`
- Verify or modify only on a failing test: `src/deep_research/agents/researcher.py`
- Verify or modify only on a failing test: `src/deep_research/agents/source_evaluator.py`
- Verify or modify only on a failing test: `src/deep_research/agents/fact_checker.py`
- Verify or modify only on a failing test: `src/deep_research/agents/synthesizer.py`
- Verify only: `src/deep_research/agents/critic.py`
- Verify only: `src/deep_research/evaluation/judging.py`
- Modify: `tests/test_agents/test_tool_free_prompts.py`
- Modify: `tests/test_agents/test_critic.py`
- Modify: `tests/test_evaluation/test_judging.py`
- Modify: `tests/test_evaluation/test_config.py`

**Interfaces:**
- Preserves: the prompt changes already measured as effective in Critic and Judge.
- Produces: a single cross-agent conformance matrix preventing later prompt drift.

- [ ] **Step 1: Pin the operation inventory and example count**

The test matrix must cover every tool-free structured operation:

| Agent | Operation | Examples | Semantic coverage |
| --- | --- | ---: | --- |
| Planner | plan finalization | 1 | valid actionable plan |
| Researcher | finding extraction | 1 | evidence-backed finding |
| Source Evaluator | source scoring | 2 | weak and strong source |
| Fact Checker | claim extraction | 1 | evidence-linked claim |
| Fact Checker | claim verification | 2 | verified and insufficient evidence |
| Synthesizer | report construction | 1 | evidence-bound report with uncertainty |
| Critic | report review | 2 | weak/refine and strong/end |
| Judge | evaluation verdict | 2 | weak and strong score profiles |

Assert every example parses with `json.loads`, validates with the exact response model, uses at most two examples, contains no Markdown fence, and uses only reserved `.example.test` URLs when it contains a URL.

- [ ] **Step 2: Pin the shared reply-shape rules**

For every rendered prompt assert:

```python
assert body.count("JSON object") == 1
assert body.count("# Reply format") == 1
assert body.rstrip().splitlines()[-1].strip().endswith("}")
assert "```" not in body
assert "## Tools" not in body
```

The last section must be the reply contract. Request-owned sections use H1 headings; embedded evidence/report content cannot close or create peer sections. Every field name is stated once in the reply contract, with no competing prose protocol.

- [ ] **Step 3: Pin scoring clarity and opposite examples**

For Source Evaluator, claim verification, Critic, and Judge, assert weak/negative and strong/positive examples are both schema-valid and semantically opposite. For Critic and Judge also assert:

- bands cover the full declared range without overlap, gaps, or contradictory endpoint language;
- example scores fall inside their labelled bands and bracket the operative threshold where one exists;
- the weighted Judge formula exactly matches `COMMON_DIMENSION_WEIGHTS` and agent-specific dimensions remain unweighted;
- examples are labelled illustrative and their values are not a target to copy.

- [ ] **Step 4: Pin provider repair instructions**

For DeepSeek Chat, DeepSeek Judge Responses, and OpenAI Responses, assert both first and repair requests contain the literal JSON instruction, the exact schema transport, and no Markdown-fence invitation. Fault-inject malformed JSON, missing fields, extra fields on strict schemas, wrong types, and out-of-bounds strings; assert one repair at most and a typed `StructuredOutputError` after a second invalid response.

- [ ] **Step 5: Check fingerprints before accepting any prompt edit**

Run:

```powershell
& $readinessPython -c "from deep_research.evaluation.config import agent_prompt_fingerprint; from deep_research.evaluation.models import AGENT_NAMES; print({name: agent_prompt_fingerprint(name) for name in AGENT_NAMES})"
& $readinessPython -c "from deep_research.evaluation.judging import judge_prompt_fingerprint; print(judge_prompt_fingerprint(rubric_version=1))"
```

Expected at the start: the recorded six target fingerprints and Judge `74b9cddfbbee`. If all conformance tests pass, make no production prompt edit. If a test proves a real gap, change only the owning prompt, record old/new fingerprints, and mark the affected canary set invalidated according to the Global Constraints. Never re-pin a fingerprint without reviewing the rendered prompt diff.

- [ ] **Step 6: Run prompt gates and commit tests only if production already conforms**

```powershell
& $readinessPython -m pytest tests/test_agents/test_tool_free_prompts.py tests/test_agents/test_critic.py tests/test_evaluation/test_judging.py tests/test_evaluation/test_config.py -q
```

Expected: PASS with the Judge fingerprint unchanged. Commit exact changed paths with:

```powershell
git add -- tests/test_agents/test_tool_free_prompts.py tests/test_agents/test_critic.py tests/test_evaluation/test_judging.py tests/test_evaluation/test_config.py
git commit -m "test(prompts): lock cross-agent json conformance"
```

---

### Task 7: Replace the unreviewable shape probe and pass the full offline release gate

**Files:**
- Create: `scripts/native_react_shape_probe.py`
- Create: `tests/test_native_react_shape_probe.py`
- Modify: `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Modify: `docs/superpowers/2026-09-12-shared-native-react-observation-report.md`
- Modify: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Replaces: ignored `output/transport-probes/native-react-v1/native_react_shape_probe.py` as evidence source.
- Produces: a checked-in, unit-tested v2 instrument plus a zero-failure offline candidate for independent review.

- [ ] **Step 1: Write RED adversarial gate tests**

The gate must fail each of these 30-record batches:

```python
twenty_nine_calls_plus_dsml_final
twenty_nine_calls_plus_fenced_action_final
twenty_nine_calls_plus_legacy_action_json_final
twenty_nine_calls_plus_unknown_tool
twenty_nine_calls_plus_non_object_arguments
twenty_nine_calls_plus_local_response_failure
twenty_nine_calls_plus_sdk_response_failure
twenty_nine_calls_plus_timeout
twenty_nine_calls_plus_output_limit
```

The exact exploit found in review—29 valid calls plus one non-blank DSML final answer—must return `passed=False` and one shape failure.

- [ ] **Step 2: Check in a content-free v2 probe**

For every call retain only:

```text
run, outcome, finish_category, allow-listed tool name,
arguments_are_object, ordinary_text_present, ordinary_text_chars,
dsml_marker_present, tool_markup_present, fence_present,
legacy_action_json_present, failure_category, failure_origin,
retryable, status_code, input_tokens, output_tokens, total_tokens
```

Never retain text, parsed payloads, arguments, prompts, reasoning, exception messages, or arbitrary provider types. Do not hard-code character or usage values.

The probe must wrap the injected SDK client's `create` method and report the actual call count. `--dry-run` must verify model, max effort, thinking, `32768`, native tools, `tool_choice="auto"`, no `response_format`, repository retry count zero, SDK retry count zero, exactly one SDK create, zero tool executions, and zero LangSmith requests. State accurately that this proves request construction only; offline agent-boundary tests prove execution behavior.

- [ ] **Step 3: Use origin, never a default catch-all classifier**

Classify `ProviderResponseError` only by its required category and origin. An unknown combination is `instrument_error` and fails the gate. Never default a generic `response` error to `malformed_envelope`.

- [ ] **Step 4: Run the probe unit tests and dry-run**

```powershell
& $readinessPython -m pytest tests/test_native_react_shape_probe.py -q
& $readinessPython scripts/native_react_shape_probe.py --dry-run --requests 30
```

Expected: PASS and a content-free inventory showing 30 logical requests, a 30-request SDK ceiling, zero tools executed, and zero network requests during dry-run.

- [ ] **Step 5: Prove the three live-tier ledger tests in the exact gate interpreter**

Use the same interpreter and `PYTHONPATH` that will run the full suite. Do not read or print credentials; these tests supply their own inert values and dependency doubles:

```powershell
& $readinessPython -c "import sys, deep_research; import deep_research.evaluation.dependencies as dependencies; print(sys.executable); print(deep_research.__file__); print(dependencies.__file__)"
& $readinessPython -m pytest tests/test_evaluation/test_runner.py::test_a_live_experiment_requests_one_repetition tests/test_evaluation/test_targets.py::test_the_ledger_records_real_services_for_a_live_run tests/test_evaluation/test_targets.py::test_a_live_researcher_records_only_source_url_fingerprints -q
```

Required: all three pass, with both printed modules resolving beneath `$readinessRoot`. At plan commit `89a9089`, this exact focused command passes even when the calling shell has no `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, or `LANGSMITH_API_KEY`; therefore a failure is an interpreter/import/test-isolation mismatch, not proof that real credentials are needed.

If any fails, stop and record the printed interpreter and module paths, `& $readinessPython -m pytest --version`, the complete traceback, and the exact command. Do not substitute `python`, exclude the tests, use real credentials, or proceed to the full gate until the mismatch is reproduced in the specified interpreter.

- [ ] **Step 6: Run the complete offline release gate from a clean process**

```powershell
$env:DEEPSEEK_API_KEY = 'sk-deepseek-offline-sentinel'
& $readinessPython -m pytest -q
& $readinessPython -m ruff check . --exclude tools,.deepseek-runs
git diff --check
```

Required: zero test failures. Do not use the observation report's old “seven standing failures” baseline; clean reproduction showed four stale tests and the three ledger tests pass.

- [ ] **Step 7: Verify frozen behavior and request independent review**

Verify budgets, routes, thresholds, case identities, Critic semantics, all target fingerprints, Judge fingerprint, output exclusion, and no production `complete_structured(..., ReActDecision, ...)` call. Use `superpowers:requesting-code-review` over the full remediation range. Any unresolved Critical or Important finding is NO-GO for live work.

Record exact commands and counts in the observation report and fix log. Commit:

```powershell
git add -- scripts/native_react_shape_probe.py tests/test_native_react_shape_probe.py docs/superpowers/2026-09-12-shared-native-react-live-validation.md docs/superpowers/2026-09-12-shared-native-react-observation-report.md docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "test(readiness): establish reviewed output integrity gate"
```

---

### Task 8: Run one new, literal native-shape release gate

**Files:**
- Create after authorization: `output/transport-probes/native-react-v2/native_react_shape_gate_30.jsonl` (ignored; never stage)
- Modify: `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Modify: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Consumes: reviewed clean Task 7 commit and the checked-in v2 probe.
- Produces: one attributable native-shape release verdict; no canary starts without PASS.

- [ ] **Step 1: Predeclare and request authorization**

Re-run `--dry-run`, record the probe commit and SHA-256, and ask authorization for exactly:

```json
{"agent":"critic","logical_requests":30,"provider_sdk_create_ceiling":30,"tool_execution_requests":0,"judge_requests":0,"langsmith_requests":0,"effort":"max","max_tokens":32768,"repository_retries":0,"sdk_retries":0}
```

Do not spend before explicit authorization for this exact batch.

- [ ] **Step 2: Execute once under a fresh v2 namespace**

```powershell
& $readinessPython scripts/native_react_shape_probe.py --execute --requests 30 --output output/transport-probes/native-react-v2/native_react_shape_gate_30.jsonl
```

- [ ] **Step 3: Apply both literal gates**

Shape integrity requires:

- 0/30 DSML, tool markup, fences, or legacy action JSON in ordinary text;
- 0/30 mixed text/tool, unknown item, unknown tool, multiple call, malformed call, or non-object arguments;
- every successful turn is one allow-listed native call or legitimate non-blank final answer;
- at least one native tool call;
- exactly 30 SDK `create` calls and zero repair/tool execution calls.

Operational availability requires:

- 0/30 timeout, rate-limit, transport, HTTP, generic SDK, output-limit, or instrument failures.

Both must pass. Any miss is FAIL. Do not rerun the same gate, reinterpret zero as statistical equivalence, or proceed to canaries. A failure starts a new diagnosis with a new experiment name and separately authorized measurement; it does not relax this gate.

- [ ] **Step 4: Record the verdict without raw content**

Record commit, dirty state, probe hash, exact SDK creates, safe counts, origin/category breakdown, output path, and PASS/FAIL. Commit only the two documentation files.

---

### Task 9: Validate structured JSON and behavior for all six agents

**Files:**
- Modify after each authorized batch: `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Modify after each authorized batch: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`
- Create per batch: fresh ignored directories under `output/evaluations/production-readiness-v2-*`

**Interfaces:**
- Consumes: PASS from Task 8 and reviewed fingerprints from Task 7.
- Produces: six independent production-readiness verdicts, including first-attempt JSON conformance for target and Judge structured calls.

- [ ] **Step 1: Predeclare each batch separately**

With three repetitions, `max_iterations=5`, at most one target structured repair, and at most one Judge structured repair, retain these authorization ceilings from the production call graph:

| Agent | Maximum logical model requests for three repetitions |
| --- | ---: |
| Planner | 33 |
| Researcher | 69 |
| Source Evaluator | 12 |
| Fact Checker | 117 |
| Synthesizer | 12 |
| Critic | 27 |

Before each agent, derive the exact case-specific ceiling and separately state DeepSeek target calls, Judge calls, configured HTTP retry multiplier, Tavily/search calls, memory calls, document calls, LangSmith usage, and fresh output namespace. Obtain authorization for that agent only.

- [ ] **Step 2: Run three isolated one-repetition canaries in order**

Order:

1. Planner;
2. Researcher;
3. Source Evaluator;
4. Fact Checker;
5. Synthesizer;
6. Critic.

Use three distinct experiment prefixes per agent and stop the sequence on the first failed repetition or batch. Do not spend a later agent's authorization early.

- [ ] **Step 3: Apply the JSON and native-shape acceptance rules**

Every agent requires all of:

- 3/3 repetitions completed and all existing hard gates passed;
- aggregate quality at or above the unchanged `0.75` threshold;
- Judge `status="scored"`, `structured_attempts == 1`, no diagnostics, and fingerprint `74b9cddfbbee`;
- target `structured_calls.repaired_calls == 0` and `failed_attempts == 0`;
- no `json_invalid`, `schema_output`, `react_decision`, provider, or output-limit fallback;
- no DSML, fenced/legacy action envelope, mixed envelope, unknown output item/tool, multiple call, or malformed native call;
- exact reviewed target transport and target prompt fingerprint recorded;
- no prohibited dependency call or secret/content leakage.

Planner, Researcher, Fact Checker, and Critic additionally require at least one successfully executed native tool call across their three repetitions. Source Evaluator and Synthesizer require zero model-selected native tool calls.

One repaired first attempt is not a release pass even if the second attempt succeeds. It is a diagnostic signal: stop, identify the owning operation and validation category from content-free telemetry, write a focused offline regression, and design a new named measurement. Do not rerun the same canary or loosen the threshold.

- [ ] **Step 4: Validate prompt techniques in the live traces without trusting rationales**

For one target structured call per operation and every Judge call, verify the trace identifies the reviewed prompt fingerprint, JSON Schema transport, one or two valid examples, one reply-format section, and no tool advertising on structured calls. Do not use Judge rationale claims as evidence of harness state; compare them only with recorded gates and typed artifacts.

- [ ] **Step 5: Record and commit each verdict**

After each batch record exact request count spent, services, case identity, commit, clean/dirty state, fingerprints, structured attempt counts, native tool counts, hard gates, Judge status, aggregate quality, diagnostics, trace/artifact pointers, and PASS/FAIL. Commit documentation after each batch; never stage `output/`, `.deepseek-runs/`, or `tools/`.

Only after all six pass may the document state “all agents are production ready.” A partial pass must name the ready and blocked agents separately.

---

### Task 10: Close readiness and resume Critic calibration separately

**Files:**
- Modify: `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Modify: `docs/superpowers/2026-09-12-shared-native-react-observation-report.md`
- Modify: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`
- Modify only after readiness: `docs/superpowers/2026-09-12-critic-decision2-canary-report.md`
- Modify only after readiness: `docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md`

- [ ] **Step 1: Produce the final readiness matrix**

For each agent report offline status, prompt fingerprint, native transport, three canary results, structured first-attempt count, repair count, provider failures, hard gates, Judge status/fingerprint, aggregate score, and readiness verdict. Explicitly list unresolved risk; do not convert absence of failures in a small sample into a zero long-run rate.

- [ ] **Step 2: Define production monitoring and stop conditions**

Monitor only content-free counters already introduced by this plan:

- structured repair activation;
- `schema_output` repair exhaustion;
- local native-response rejection;
- SDK/transport/HTTP/output-limit failures;
- target and Judge fallback;
- prompt/configuration fingerprint drift.

Any accepted malformed output is a correctness incident. Any repair exhaustion, native-envelope rejection, or fingerprint drift suspends the affected agent's ready status pending diagnosis. Do not log the response that caused it.

- [ ] **Step 3: Resume the settled Critic sequencing only after readiness**

Use the clean native-tool Critic batch to re-evaluate Decision 2 first. Only after D2 passes, perform Decision 1's case reference change with the required case-version bump and a separately authorized three-repetition canary. Do not combine calibration changes with transport/output-integrity evidence.

- [ ] **Step 4: Final offline verification and closeout commit**

```powershell
$env:DEEPSEEK_API_KEY = 'sk-deepseek-offline-sentinel'
& $readinessPython -m pytest -q
& $readinessPython -m ruff check . --exclude tools,.deepseek-runs
git diff --check
git status --short --branch
```

Commit the final documentation with exact paths. Use `superpowers:finishing-a-development-branch` only after zero offline failures, all six live passes, no unresolved Critical/Important review finding, and no unrecorded paid batch.

---

## Plan self-review

### Evidence gaps closed

- Four baseline failures are aligned to the current intentional Judge contract rather than called “pre-existing.”
- The three live-tier ledger tests remain mandatory and are proven with self-contained doubles; shell credential presence cannot be used to pass, exclude, or explain them.
- SDK failures and local response-shape failures receive distinct typed origins; no catch-all classifier can label an SDK failure malformed.
- Exact SDK exception objects are removed from traceback-frame reachability, not merely hidden from `repr` and cause/context.
- DeepSeek mixed content/tool responses and OpenAI unknown output items fail closed.
- DSML, fenced JSON, and legacy action JSON cannot pass as non-blank final answers.
- The new probe is checked in, unit tested against the 29+1 exploit, records real usage/length flags, and counts actual SDK creates.
- Successful repairs are visible in content-free artifacts, so canaries can require first-attempt structured validity.
- The Critic/Judge JSON keyword, valid examples, Markdown hierarchy, and clear score rules are explicitly applied and pinned across all structured operations.
- Paid validation remains literal, sequential, attributable, and separately authorized.

### Invalidations

- Tasks 2 and 5 add backwards-compatible diagnostic fields; new readiness artifacts must use the new commit and cannot be mixed with earlier evidence.
- Task 4 changes production behavior on every native ReAct turn; it invalidates the two earlier shape batches and requires the new 30-request gate plus all affected agent canaries.
- A change to shared `agents/prompts.py` invalidates all six target prompt fingerprints and canaries.
- An agent-local prompt edit invalidates that agent's fingerprint and canary.
- A Judge prompt edit moves the pinned Judge fingerprint and invalidates Judge evidence for every agent canary.
- No case identity changes in Tasks 1-9. A later Critic reference change must bump its case version.

### Completion rule

The work is complete only when Tasks 1-7 pass offline, Task 8 passes literally on its first named run, each Task 9 agent batch passes all behavioral and first-attempt JSON gates, the final independent review has no unresolved Critical or Important finding, and the readiness matrix names all six agents ready. Passing schemas in unit tests alone, a repaired live response, or a high aggregate score cannot substitute for these conditions.
