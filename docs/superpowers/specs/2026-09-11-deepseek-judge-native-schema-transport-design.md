# DeepSeek Judge Native-Schema Transport Design

**Date:** 2026-09-11  
**Repository:** `RahulKrishGit/deep-research`  
**Branch:** `codex/cross-agent-planner-fix-parity`  
**Design base:** `dedccd7c129288b9753bb29a7838b8d03f9372ef`  
**Status:** Approved direction from Sol/High architecture review; implementation requires the companion Superpowers plan.

## Problem

The evaluation judge is the shared quality boundary for all six agents. Current target-side evidence is mostly interpretable, but several agent repetitions are blocked because the DeepSeek judge returns JSON that is syntactically valid yet fails the `JudgeVerdict` Pydantic contract. The observed typed failures vary between root-level `extra_forbidden` at `$` and `string_bounds` at `rationale`, sometimes changing between the initial structured attempt and the single repair attempt.

The current DeepSeek structured path uses Chat Completions `response_format={"type":"json_object"}` plus a system message containing the Pydantic JSON Schema. JSON mode guarantees JSON syntax, while schema correctness remains model/prompt dependent and is enforced locally by Pydantic.

DeepSeek's current Responses API supports provider-native structured output through `text.format={"type":"json_schema", "name": ..., "schema": ...}`. The repository already depends on `openai>=2` and already uses the OpenAI Responses API for the OpenAI provider.

## Goal

Make the DeepSeek evaluation judge structurally reliable enough to serve as the shared quality evaluator for subsequent agent diagnosis, while preserving the existing judge semantics and every frozen campaign constraint.

"Reliable" here means:
- the provider is explicitly asked to conform to the exact current `JudgeVerdict` JSON Schema;
- local Pydantic validation remains authoritative defense-in-depth;
- the current initial attempt plus exactly one repair attempt remains unchanged;
- typed judge failures remain fail-closed and never fabricate quality scores;
- no agent uses the new transport until the judge path has been separately validated.

This design does not claim that an LLM judge is objectively perfect or noiseless. It removes the identified structural-output instability so judge metrics become interpretable.

## External API facts verified on 2026-09-11

DeepSeek official API documentation states:

1. `POST /responses` supports `deepseek-v4-flash` and the OpenAI Responses API shape.
2. `text.format.type="json_schema"` accepts a `name` and a JSON `schema`, with output described as conforming to that schema.
3. `max_output_tokens` is supported.
4. `reasoning.effort` is supported, including `none`, `high`, and `max`.
5. `response.status` can be `completed`, `incomplete`, or `failed`; `incomplete_details.reason` includes `max_output_tokens` and `content_filter`.
6. `usage` exposes `input_tokens`, `output_tokens`, and reasoning-token detail.
7. A Responses `developer` input role is treated as `user`, whereas this repository's current DeepSeek Chat adapter intentionally translates `developer` to `system`.

References:
- https://api-docs.deepseek.com/api/create-response/
- https://api-docs.deepseek.com/guides/responses_api/
- https://api-docs.deepseek.com/api/create-chat-completion/
- https://api-docs.deepseek.com/guides/json_mode/

## Architecture

### 1. Judge-only transport split

Keep the existing `DeepSeekChatProvider` unchanged for target agents.

Add `DeepSeekJudgeProvider` in `src/deep_research/providers/deepseek_provider.py`. It reuses the same DeepSeek client construction, capability validation, retry policy, tracker, error taxonomy, and configuration, but overrides `complete_structured()` to call `client.responses.create(...)` with provider-native `json_schema`.

The new judge provider still satisfies the existing `StructuredCompleter` protocol. No judge/evaluator interface changes.

### 2. Preserve the exact judge prompt semantics

The existing judge renders:
- one `developer` message containing `JUDGE_SYSTEM_PROMPT`;
- one `user` message containing the frozen judge template and sanitized run blocks.

The current DeepSeek Chat path translates `developer -> system`. DeepSeek Responses treats `developer` as `user`, so the new judge transport must continue using the repository's `_translated_messages()` conversion before sending `input`.

The existing deterministic `_json_instruction(JudgeVerdict)` system message must also remain in the input even though native schema enforcement makes it redundant. Keeping it avoids mixing a prompt change with the transport change.

The repair attempt must keep the same static validation summary/guidance behavior and must never include raw provider output.

### 3. Provider-native schema plus local validation

Each attempt sends:

```python
text={
    "format": {
        "type": "json_schema",
        "name": schema.__name__,
        "schema": schema.model_json_schema(),
    }
}
```

The returned `response.output_text` is still passed through:

```python
schema.model_validate_json(text)
```

The provider schema is preventive enforcement; Pydantic remains the local source of truth.

### 4. Responses request parity

For the judge:
- `model`: unchanged resolved judge model.
- `max_output_tokens`: exactly the current resolved max-token value; global default remains `4096`.
- thinking enabled: `reasoning={"effort": resolved.reasoning_effort}`.
- thinking disabled, if exercised in unit tests: `reasoning={"effort":"none"}`.
- temperature: sent only when the capability resolver says it is active.
- no new retry layer.
- SDK retries remain disabled; repo-owned retries remain authoritative.

### 5. Response and failure mapping

Normalize Responses outcomes into the existing project taxonomy:

| Responses outcome | Existing project interpretation |
| --- | --- |
| `status="completed"` | `finish_reason_category="stop"` |
| `status="incomplete"`, reason `max_output_tokens` | `ProviderOutputLimitError`, category `length` |
| `status="incomplete"`, reason `content_filter` | safe non-output-limit `ProviderResponseError` |
| `status="failed"` or unknown terminal/malformed shape | safe `ProviderResponseError` |
| completed but local Pydantic validation fails | `_StructuredValidationFailure` -> existing one-repair flow |
| second local validation failure | `StructuredOutputError` with bounded typed diagnostics |

No raw response, prompt, schema body, API key, or provider error text may survive in public exception diagnostics or traceback locals after final structured failure.

### 6. Explicit judge factory

Add `build_judge_provider(...)` to `src/deep_research/providers/factory.py`.

- DeepSeek target provider: `DeepSeekChatProvider`.
- DeepSeek judge provider: `DeepSeekJudgeProvider`.
- OpenAI target and judge provider: existing `OpenAIChatProvider`.
- unknown provider: fail closed; never cross-provider fallback.

Export the new class/factory through `src/deep_research/providers/__init__.py`.

### 7. Evaluation wiring

Only two real evaluation construction sites change:
- `src/deep_research/evaluation/cli.py::_run_agent_pipeline`
- `src/deep_research/evaluation/runner.py::run_suite_evaluation`

Both continue to build the target through `build_chat_provider(...)` and build only the judge through `build_judge_provider(...)`.

`run_agent_evaluation(...)`, `JudgeEvaluator`, `run_judge(...)`, `JudgeFeedback`, and the deterministic evaluators do not need a new interface.

### 8. Provenance

The prompt fingerprint must remain unchanged because `JUDGE_SYSTEM_PROMPT`, `JUDGE_PROMPT_TEMPLATE`, `JudgeVerdict`, weights, and rubric remain unchanged.

The judge configuration fingerprint must change because transport changed. Extend its fingerprint payload with:
- provider name;
- a fixed transport identifier:
  - DeepSeek: `deepseek_responses_json_schema_v1`
  - OpenAI: `openai_responses_parse_v1`

Add the same safe transport identifier to experiment metadata as `judge_structured_transport`.

Do not add fields to `EvaluatorDiagnostic`, `JudgeFeedback`, or `RepetitionResult`. No artifact schema-version bump is needed because `ExperimentResult.metadata` is already an open JSON mapping.

## Frozen invariants

Implementation must not change:

- `JudgeVerdict` fields or bounds.
- `ContractModel.extra="forbid"`.
- `JudgeVerdict.rationale` required, `min_length=1`, `max_length=2000`.
- `JUDGE_SYSTEM_PROMPT`.
- `JUDGE_PROMPT_TEMPLATE`.
- common judge dimension weights.
- agent-specific rubrics or anchors.
- deterministic metrics.
- cases, reference data, thresholds, or weights.
- target-agent prompts or code.
- target-agent DeepSeek structured transport.
- fallback semantics.
- one structured repair attempt.
- provider retry policy.
- target or judge reasoning-effort profiles.
- global `llm.max_tokens == 4096`.
- no fabricated judge scores when judging fails.

No live/provider/LangSmith/evaluation-suite command is authorized by this design.

## Offline readiness definition

The judge transport is ready for a later canary only when:

1. DeepSeek judge requests use Responses `json_schema` with the exact `JudgeVerdict.model_json_schema()`.
2. Prompt role/content parity is pinned by tests.
3. Valid responses produce the same `JudgeVerdict` and weighted judge score as before.
4. Output-limit, transport, schema, and malformed-response failures preserve the existing typed taxonomy.
5. Exactly two structured attempts maximum are proven.
6. Secret/traceback scrubbing tests pass.
7. Target DeepSeek providers are proven to remain on Chat Completions.
8. OpenAI judge behavior is unchanged.
9. Judge prompt fingerprint is unchanged while judge configuration fingerprint changes because of transport.
10. Focused tests, evaluation/provider contract tests, full offline pytest, Ruff, and `git diff --check` are green.
11. A fresh Sol/High review of the exact implementation range has no unresolved Critical or Important finding.

Only after those conditions are satisfied may the coordinator ask the user to authorize one paid judge canary. The implementation plan itself must stop before that call.
