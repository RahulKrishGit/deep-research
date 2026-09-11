# DeepSeek Judge Native-Schema Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DeepSeek evaluation judge's prompt-enforced JSON-only transport with judge-only Responses API `json_schema` enforcement while preserving the exact frozen judge contract, failure semantics, retry count, prompt semantics, and 4096-token cap so later agent quality diagnosis can rely on scorable judge metrics.

**Architecture:** Keep `DeepSeekChatProvider` as the production/target-agent provider. Add a `DeepSeekJudgeProvider` that reuses the same DeepSeek configuration, client, retries, tracing, and safe diagnostics but sends only judge structured calls through `client.responses.create(..., text.format.type="json_schema")`. Select that provider only through a new judge factory used by the evaluation CLI and controlled-suite wiring; preserve local Pydantic validation and exactly one repair attempt.

**Tech Stack:** Python 3.11+, Pydantic v2, OpenAI Python SDK `>=2` used against DeepSeek's OpenAI-compatible endpoint, pytest/pytest-asyncio, Ruff, LangSmith evaluation harness (offline fakes only in this plan).

**Spec:** `docs/superpowers/specs/2026-09-11-deepseek-judge-native-schema-transport-design.md`

## Global Constraints

- Implementation base is `dedccd7c129288b9753bb29a7838b8d03f9372ef`; if branch HEAD moves, re-read the touched files and record the new base before starting.
- Execute in an isolated worktree created with `superpowers:using-git-worktrees`.
- TDD is mandatory: every production behavior change starts with a focused RED test and the worker must record the observed RED before implementation.
- No live/provider/LangSmith/evaluation-suite command, credential access, `.env` inspection, or `.deepseek-runs/` inspection is authorized.
- Do not modify any target agent implementation or target-agent prompt.
- `DeepSeekChatProvider.complete_structured()` remains the target-agent structured-output path.
- Keep `JudgeVerdict` unchanged.
- Keep `ContractModel.extra="forbid"`.
- Keep `JudgeVerdict.rationale` required with `min_length=1` and `max_length=2000`.
- Keep `JUDGE_SYSTEM_PROMPT`, `JUDGE_PROMPT_TEMPLATE`, common weights, rubrics, cases, thresholds, deterministic metrics, and fallback semantics unchanged.
- Preserve initial structured attempt + exactly one repair attempt; never add a third attempt.
- Preserve repository-owned provider retry policy; do not add SDK retries or an evaluator retry.
- Preserve judge/target reasoning-effort profiles.
- Preserve `llm.max_tokens == 4096`; do not add an operation-specific judge increase.
- Never fabricate, truncate, pad, strip fields from, or otherwise coerce a failed `JudgeVerdict` into a score.
- Do not add telemetry fields to `EvaluatorDiagnostic`, `JudgeFeedback`, or `RepetitionResult`.
- Provider-native schema enforcement is defense-in-depth; local `schema.model_validate_json(...)` remains authoritative.
- A passing offline implementation does not authorize a paid canary. Stop after the final review gate and request explicit authorization separately.

---

## File Structure and Ownership

**Modify**
- `src/deep_research/providers/deepseek_provider.py` — add judge-only Responses structured adapter and safe Responses telemetry/extraction helpers; leave target Chat behavior pinned.
- `src/deep_research/providers/factory.py` — add explicit judge-provider selection.
- `src/deep_research/providers/__init__.py` — export the judge provider and factory.
- `src/deep_research/evaluation/cli.py` — use the judge factory for the real per-agent pipeline only.
- `src/deep_research/evaluation/runner.py` — use the judge factory in the real controlled-suite provider construction only.
- `src/deep_research/evaluation/config.py` — fingerprint and expose the fixed judge structured-transport identifier.
- `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md` — record offline implementation/review evidence after code is complete.
- `docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md` — update controller state only after the final review.

**Test**
- `tests/test_deepseek_provider.py`
- `tests/test_provider_factory.py`
- `tests/test_evaluation/test_judging.py`
- `tests/test_evaluation/test_config.py`
- `tests/test_evaluation/test_runner.py`
- `tests/test_evaluation/test_cli.py` only if direct `_run_agent_pipeline` wiring can be tested without network/client construction.
- `tests/test_evaluation/test_suite.py` if its existing fakes are the cleanest place to pin suite target-vs-judge provider selection.

**Do not modify**
- `src/deep_research/evaluation/models.py`
- `src/deep_research/evaluation/judging.py`, unless a RED proves an integration defect after provider/factory tasks.
- any `src/deep_research/agents/*.py`
- any case module under `src/deep_research/evaluation/cases/`
- `config.yaml` except to verify `max_tokens: 4096` remains unchanged.
- `src/deep_research/providers/contracts.py` unless a RED proves the existing finite telemetry types cannot represent Responses outcomes.

---

### Task 1: Add the judge-only DeepSeek Responses request path

**Files:**
- Modify: `tests/test_deepseek_provider.py`
- Modify: `src/deep_research/providers/deepseek_provider.py`

**Interfaces:**
- Consumes: existing `DeepSeekChatProvider`, `_build_client`, `_translated_messages`, `_json_instruction`, `_raise_deepseek_error`, `_validation_diagnostic`, `_validation_summary`, `_validation_repair_guidance`, `_resolve_max_tokens`, `with_retries`, `ProviderResponseTelemetry`.
- Produces: `DeepSeekJudgeProvider(DeepSeekChatProvider)` satisfying `StructuredCompleter.complete_structured(messages, schema, *, agent_name=None, max_tokens=None)`.

- [ ] **Step 1: Extend the fake client with a Responses recorder**

Add:

```python
class RecordingResponses:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDeepSeekClient:
    def __init__(
        self,
        completions: RecordingCompletions | None = None,
        responses: RecordingResponses | None = None,
    ) -> None:
        self.chat = SimpleNamespace(
            completions=completions or RecordingCompletions()
        )
        self.responses = responses or RecordingResponses()
```

Add:

```python
def responses_response(
    *,
    output_text: object,
    status: str = "completed",
    incomplete_reason: str | None = None,
    input_tokens: int = 8,
    output_tokens: int = 3,
    model: str = "deepseek-v4-flash",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-response",
        status=status,
        incomplete_details=(
            None
            if incomplete_reason is None
            else SimpleNamespace(reason=incomplete_reason)
        ),
        output_text=output_text,
        model=model,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )
```

- [ ] **Step 2: Write the RED test for native judge schema transport**

```python
@pytest.mark.asyncio
async def test_deepseek_judge_uses_responses_json_schema_with_prompt_parity() -> None:
    verdict_payload = _judge_payload(rationale="Grounded judge rationale.")
    responses = RecordingResponses(
        responses_response(output_text=json.dumps(verdict_payload))
    )
    client = FakeDeepSeekClient(responses=responses)
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekJudgeProvider(
        deepseek_config(), tracker, client=client
    )

    result = await provider.complete_structured(
        [
            ChatMessage(role="developer", content="judge policy"),
            ChatMessage(role="user", content="judge input"),
        ],
        JudgeVerdict,
        agent_name="judge",
    )

    assert result == JudgeVerdict.model_validate(verdict_payload)
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["max_output_tokens"] == 4096
    assert call["reasoning"] == {"effort": "high"}
    assert "temperature" not in call
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "JudgeVerdict",
            "schema": JudgeVerdict.model_json_schema(),
        }
    }
    assert call["input"][0] == {"role": "system", "content": "judge policy"}
    assert call["input"][1] == {"role": "user", "content": "judge input"}
    assert call["input"][2]["role"] == "system"
    assert "JSON Schema:" in call["input"][2]["content"]
    assert "response_format" not in call
    assert "max_tokens" not in call
    assert client.chat.completions.calls == []
```

- [ ] **Step 3: Run RED**

```bash
python -m pytest tests/test_deepseek_provider.py::test_deepseek_judge_uses_responses_json_schema_with_prompt_parity -q
```

Expected: FAIL because `DeepSeekJudgeProvider` does not exist.

- [ ] **Step 4: Add Responses request mapping without touching Chat mapping**

Add a private helper that starts from `config.resolve_for(agent_name)` and `resolve_request_settings("deepseek", effective)`:

```python
def _responses_request_options(
    config: LLMConfig,
    agent_name: str | None,
) -> tuple[EffectiveModelConfig, dict[str, object], dict[str, JsonValue]]:
    effective = config.resolve_for(agent_name)
    resolved = resolve_request_settings("deepseek", effective)
    request: dict[str, object] = {
        "model": effective.model,
        "reasoning": {
            "effort": (
                resolved.reasoning_effort
                if resolved.reasoning_effort is not None
                else "none"
            )
        },
    }
    if resolved.include_temperature:
        request["temperature"] = config.temperature
    metadata: dict[str, JsonValue] = {
        "provider": "deepseek",
        "thinking_mode": effective.thinking_mode,
        "requested_reasoning_effort": effective.reasoning_effort,
    }
    if agent_name is not None:
        metadata["agent_name"] = agent_name
    if resolved.reasoning_effort is not None:
        metadata["effective_reasoning_effort"] = resolved.reasoning_effort
    return effective, request, metadata
```

Do not alter `DeepSeekChatProvider._request_options()`.

- [ ] **Step 5: Implement `DeepSeekJudgeProvider` initial structured path**

Add a subclass in the same module so it can reuse the existing safe private helpers. Its first implementation only needs to satisfy the valid-response RED:

```python
class DeepSeekJudgeProvider(DeepSeekChatProvider):
    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> SchemaT:
        if not messages:
            raise ValueError("messages must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(
            self._config.max_tokens, max_tokens
        )
        effective, request, metadata = _responses_request_options(
            self._config, agent_name
        )
        instruction = _json_instruction(schema)
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]
        return await self._responses_structured_attempt(
            current_messages,
            schema,
            model=effective.model,
            request=request,
            metadata=metadata,
            configured_max_tokens=resolved_max_tokens,
            attempt=1,
        )
```

The initial `_responses_structured_attempt()` sends:

```python
response = await self._client.responses.create(
    **{
        **request,
        "input": messages,
        "max_output_tokens": configured_max_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
            }
        },
    }
)
text = getattr(response, "output_text", None)
if not isinstance(text, str):
    raise ProviderResponseError(
        "DeepSeek Responses output did not contain text"
    )
parsed = schema.model_validate_json(text)
self._last_model_returned = getattr(response, "model", None) or model
return parsed
```

Task 2 owns full failure parity and repair logic.

- [ ] **Step 6: Run focused GREEN plus target Chat parity**

```bash
python -m pytest   tests/test_deepseek_provider.py::test_deepseek_judge_uses_responses_json_schema_with_prompt_parity   tests/test_deepseek_provider.py::test_deepseek_plain_completion_translates_roles_and_thinking   -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat(provider): add DeepSeek judge schema transport"
```

**Task 1 review gate:** reject if target `DeepSeekChatProvider` starts using Responses, if the existing JSON-schema system instruction is removed, or if `developer -> system` parity is not asserted.

---

### Task 2: Preserve typed failures, exactly one repair, and secret safety

**Files:**
- Modify: `tests/test_deepseek_provider.py`
- Modify: `src/deep_research/providers/deepseek_provider.py`

**Interfaces:**
- Consumes: `DeepSeekJudgeProvider` from Task 1.
- Produces: Responses structured path with the same public failure taxonomy and two-attempt ceiling as the existing Chat structured path.

- [ ] **Step 1: Write RED tests for Responses usage/status normalization**

Pin:
- absent usage -> `TokenUsage()`;
- `input_tokens`/`output_tokens` must be non-negative integers;
- present `total_tokens` must equal their sum;
- booleans, strings, negative values, inconsistent totals -> `ProviderResponseError`.

Pin status mapping:
- `completed` -> `stop`;
- `incomplete/max_output_tokens` -> `length`;
- `incomplete/content_filter` -> `content_filter`;
- `failed`, unknown, malformed -> `other`.

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_deepseek_provider.py -q -k "responses_usage or responses_status"
```

Expected: FAIL.

- [ ] **Step 3: Implement bounded Responses telemetry helpers**

```python
def _responses_usage_from_response(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    # Apply the same finite integer checks as Chat usage.
    ...


def _responses_finish_reason(response: Any) -> FinishReasonCategory:
    status = getattr(response, "status", None)
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if reason == "max_output_tokens":
            return "length"
        if reason == "content_filter":
            return "content_filter"
    return "other"
```

Build `ProviderResponseTelemetry` with `structured_attempt=attempt`.

- [ ] **Step 4: Write RED output-limit test**

```python
@pytest.mark.asyncio
async def test_deepseek_judge_responses_output_limit_is_typed() -> None:
    responses = RecordingResponses(
        responses_response(
            output_text="partial",
            status="incomplete",
            incomplete_reason="max_output_tokens",
            output_tokens=4096,
        )
    )
    provider = DeepSeekJudgeProvider(
        deepseek_config(),
        CapturingTracker(),
        client=FakeDeepSeekClient(responses=responses),
    )

    with pytest.raises(ProviderOutputLimitError) as caught:
        await provider.complete_structured(
            [ChatMessage(role="user", content="judge input")],
            JudgeVerdict,
            agent_name="judge",
        )

    assert caught.value.telemetry.finish_reason_category == "length"
    assert caught.value.telemetry.configured_max_tokens == 4096
    assert caught.value.telemetry.structured_attempt == 1
```

Run and verify RED.

- [ ] **Step 5: Implement status/output-limit handling**

Inside `_responses_structured_attempt()`:
1. open the existing `tracker.llm_span(...)` with `operation="structured_output"` and `attempt`;
2. use existing `with_retries(...)`;
3. translate SDK exceptions via `_raise_deepseek_error(...)`;
4. build and write bounded telemetry;
5. `length` -> `ProviderOutputLimitError(telemetry)`;
6. any category except `stop` -> safe `ProviderResponseError("DeepSeek Responses request did not complete cleanly")`;
7. only then inspect `output_text`.

Never place `response.error`, incomplete details, or raw output in public exception text.

- [ ] **Step 6: Write RED tests for local validation and two-attempt ceiling**

Use two completed fake Responses:
1. attempt 1: extra root property;
2. attempt 2: empty or overlong rationale.

Assert:
- exactly two `responses.create` calls;
- zero Chat Completions calls;
- final exception `StructuredOutputError`;
- typed attempt/category/path diagnostics;
- no third request;
- native `json_schema` present on both requests;
- second input contains the existing static repair guidance;
- second input contains no raw attempt-1 output.

- [ ] **Step 7: Verify RED**

```bash
python -m pytest tests/test_deepseek_provider.py -q -k "judge_responses and repair"
```

Expected: FAIL because Task 1 has no repair loop.

- [ ] **Step 8: Implement the existing one-repair loop for Responses**

Mirror the current finite loop `for attempt in (1, 2)`. Use `_validation_diagnostic`, `_validation_summary`, and `_validation_repair_guidance`. The repair message must remain static and bounded; do not include provider output.

Before raising final `StructuredOutputError`, clear provider-adjacent locals using the same pattern already used by `DeepSeekChatProvider.complete_structured()`.

- [ ] **Step 9: Add RED/GREEN secret-surface coverage**

Adapt `_provider_exception_surfaces()` using markers for:
- provider output;
- prompt;
- request;
- schema description.

Assert none are present in public exception strings, exception attributes, provider traceback locals after final failure, or projected safe diagnostics.

- [ ] **Step 10: Run all DeepSeek provider tests**

```bash
python -m pytest tests/test_deepseek_provider.py -q
```

Expected: PASS; all existing Chat structured-output tests remain green.

- [ ] **Step 11: Commit**

```bash
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "test(provider): preserve judge failure semantics"
```

**Task 2 review gate:** Critical if there is a third structured attempt, raw provider content becomes reachable, output-limit is converted into schema failure, or existing Chat behavior changes.

---
### Task 3: Add explicit judge-provider selection

**Files:**
- Modify: `tests/test_provider_factory.py`
- Modify: `src/deep_research/providers/factory.py`
- Modify: `src/deep_research/providers/__init__.py`

**Interfaces:**
- Consumes: `DeepSeekJudgeProvider`.
- Produces: `build_judge_provider(config: LLMConfig, tracker: Tracker, *, api_key: str | None = None)`.

- [ ] **Step 1: Write RED factory-selection tests**

Add:

```python
@pytest.mark.parametrize(
    ("provider_name", "model", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", DeepSeekJudgeProvider),
        ("openai", "gpt-4o", OpenAIChatProvider),
    ],
)
def test_judge_factory_builds_the_selected_judge_adapter(
    provider_name, model, expected, tracker, monkeypatch
) -> None:
    ...
```

Also assert:
- `build_chat_provider(deepseek...)` remains `DeepSeekChatProvider`;
- explicit API key is passed through;
- unknown provider still raises `ProviderConfigurationError`;
- no cross-provider fallback occurs.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_provider_factory.py -q -k "judge_factory"
```

Expected: FAIL because `build_judge_provider` is absent.

- [ ] **Step 3: Implement the explicit factory**

In `providers/factory.py`:

```python
JudgeAdapter: TypeAlias = OpenAIChatProvider | DeepSeekJudgeProvider

def build_judge_provider(
    config: LLMConfig,
    tracker: Tracker,
    *,
    api_key: str | None = None,
) -> JudgeAdapter:
    if config.provider == "deepseek":
        return DeepSeekJudgeProvider(config, tracker, api_key=api_key)
    if config.provider == "openai":
        return OpenAIChatProvider(config, tracker, api_key=api_key)
    raise ProviderConfigurationError(
        f"Unsupported chat provider {config.provider!r}; "
        "accepted values: deepseek, openai"
    )
```

Do not change `build_chat_provider(...)`.

Export `DeepSeekJudgeProvider`, `JudgeAdapter`, and `build_judge_provider` from `providers/__init__.py`.

- [ ] **Step 4: Run provider factory tests**

```bash
python -m pytest tests/test_provider_factory.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add   src/deep_research/providers/factory.py   src/deep_research/providers/__init__.py   tests/test_provider_factory.py
git commit -m "feat(provider): select judge transport explicitly"
```

**Task 3 review gate:** reject any implementation that changes `build_chat_provider()` behavior or silently falls back from DeepSeek to OpenAI.

---

### Task 4: Wire only the evaluation judge to the new factory

**Files:**
- Modify: `src/deep_research/evaluation/cli.py`
- Modify: `src/deep_research/evaluation/runner.py`
- Test: `tests/test_evaluation/test_runner.py`
- Test: `tests/test_evaluation/test_suite.py`
- Test: `tests/test_evaluation/test_cli.py` only if needed for direct pipeline wiring

**Interfaces:**
- Consumes: `build_judge_provider(...)`.
- Produces: per-agent and controlled-suite pipelines with distinct target and judge provider construction.

- [ ] **Step 1: Write a RED controlled-suite wiring test**

Using the existing suite fakes/monkeypatch style, record calls separately:

```python
target_builds: list[str] = []
judge_builds: list[str] = []

def fake_chat_provider(config, tracker, *, api_key=None):
    target_builds.append(config.model)
    return target_provider_double

def fake_judge_provider(config, tracker, *, api_key=None):
    judge_builds.append(config.model)
    return judge_provider_double
```

Monkeypatch `runner_module.build_chat_provider` and `runner_module.build_judge_provider`, run the existing fully offline fake `run_suite_evaluation(...)`, and assert:
- target factory called once per agent;
- judge factory called once per agent;
- target factory is never used for the judge;
- no network/client request occurred.

If `test_suite.py` has a cleaner existing suite harness, place this test there instead of duplicating runner scaffolding.

- [ ] **Step 2: Run RED**

Run only the new wiring test. Expected: FAIL because runner still uses `build_chat_provider` for the judge.

- [ ] **Step 3: Change suite wiring only**

In `runner.py`, import `build_judge_provider` and change only:

```python
judge_provider = build_chat_provider(...)
```

to:

```python
judge_provider = build_judge_provider(
    judge_llm_config(runtime, settings.llm),
    tracker,
    api_key=chat_key,
)
```

Leave target construction untouched.

- [ ] **Step 4: Add equivalent per-agent CLI RED coverage**

Prefer a small pure monkeypatch test around `_run_agent_pipeline` only if all LangSmith/client creation can be replaced with local fakes.

If direct `_run_agent_pipeline` testing would require network-facing construction, extract only provider construction into this private helper in `cli.py`:

```python
def _build_pipeline_providers(settings, runtime, tracker, *, chat_key):
    return (
        build_chat_provider(
            target_llm_config(runtime, settings.llm),
            tracker,
            api_key=chat_key,
        ),
        build_judge_provider(
            judge_llm_config(runtime, settings.llm),
            tracker,
            api_key=chat_key,
        ),
    )
```

Test this helper directly. Do not create a cross-module abstraction solely for testing.

- [ ] **Step 5: Run RED and implement CLI wiring**

Expected RED: judge path still uses `build_chat_provider`.

Change only judge construction to `build_judge_provider`.

- [ ] **Step 6: Run wiring tests**

```bash
python -m pytest   tests/test_evaluation/test_runner.py   tests/test_evaluation/test_suite.py   tests/test_evaluation/test_cli.py   -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add   src/deep_research/evaluation/cli.py   src/deep_research/evaluation/runner.py   tests/test_evaluation/test_runner.py   tests/test_evaluation/test_suite.py   tests/test_evaluation/test_cli.py
git commit -m "feat(evaluation): isolate judge provider transport"
```

Only add test files that actually changed.

**Task 4 review gate:** target providers must remain exactly on their pre-task factory path; no target agent may receive `DeepSeekJudgeProvider`.

---

### Task 5: Refingerprint the judge configuration for the transport change

**Files:**
- Modify: `tests/test_evaluation/test_config.py`
- Modify: `src/deep_research/evaluation/config.py`

**Interfaces:**
- Produces: safe deterministic transport identifier in experiment metadata and judge configuration fingerprint.
- Does not change `JudgeFeedback`, `EvaluatorDiagnostic`, `RepetitionResult`, or artifact schema version.

- [ ] **Step 1: Write RED tests for transport provenance**

Add:

```python
assert judge_structured_transport("deepseek") == (
    "deepseek_responses_json_schema_v1"
)
assert judge_structured_transport("openai") == (
    "openai_responses_parse_v1"
)
```

Add tests that:
- `experiment_metadata(...)[ "judge_structured_transport" ]` equals the expected identifier;
- `experiment_metadata(...)[ "judge_provider" ]` equals the configured provider;
- judge configuration fingerprint is stable for identical config;
- changing provider/transport input changes the judge configuration fingerprint;
- `judge_prompt_fingerprint(...)` is untouched by this task.

Do not hard-code a 12-character hash value.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_evaluation/test_config.py -q -k "judge and transport"
```

Expected: FAIL because transport provenance is absent.

- [ ] **Step 3: Implement the transport identifier**

In `config.py`:

```python
_JUDGE_STRUCTURED_TRANSPORT = {
    "deepseek": "deepseek_responses_json_schema_v1",
    "openai": "openai_responses_parse_v1",
}

def judge_structured_transport(provider: ProviderName) -> str:
    return _JUDGE_STRUCTURED_TRANSPORT[provider]
```

Extend `judge_configuration_fingerprint`:

```python
judge_configuration_fingerprint = fingerprint(
    {
        "provider": settings.llm.provider,
        "structured_transport": judge_structured_transport(
            settings.llm.provider
        ),
        "judge_model": evaluation.judge_model,
        "judge_reasoning_effort": judge_effort,
        "judge_temperature": evaluation.judge_temperature,
        "thinking_mode": "enabled",
        "rubric_version": evaluation.rubric_version,
    }
)
```

Extend `experiment_metadata(...)` with:

```python
"judge_provider": settings.llm.provider,
"judge_structured_transport": judge_structured_transport(
    settings.llm.provider
),
```

Do not add a field to `EvaluationRuntimeConfig` unless a concrete consumer proves it necessary.

- [ ] **Step 4: Run config tests**

```bash
python -m pytest tests/test_evaluation/test_config.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/deep_research/evaluation/config.py tests/test_evaluation/test_config.py
git commit -m "chore(evaluation): fingerprint judge transport"
```

**Task 5 review gate:** `judge_prompt_fingerprint` must remain based on the same prompt/schema/rubric definition; no scoring semantic may be folded into this provenance-only change.

---

### Task 6: Prove the new provider integrates with the unchanged judge contract

**Files:**
- Modify: `tests/test_evaluation/test_judging.py`
- Test only; production changes are forbidden unless this task produces a genuine RED revealing an integration defect.

**Interfaces:**
- Consumes: unchanged `run_judge(...)` and new `DeepSeekJudgeProvider`.
- Proves: existing judge abstraction is transport-independent.

- [ ] **Step 1: Add a real-adapter/offline successful-judge test**

Construct `DeepSeekJudgeProvider` with a fake DeepSeek client whose `responses.create` returns a valid `JudgeVerdict` JSON string.

Call:

```python
feedback = await run_judge(
    provider,
    clean_target_output,
    planner_case,
    clean_gate_report,
    runtime=runtime_config_for("planner"),
    secrets=(),
)
```

Assert:
- `feedback.status == "scored"`;
- weighted `judge_quality` matches the verdict scores;
- `feedback.verdict` equals the typed verdict;
- `feedback.diagnostics == ()`;
- one Responses call;
- zero Chat calls;
- `feedback.prompt_fingerprint == judge_prompt_fingerprint(rubric_version=1)`.

- [ ] **Step 2: Run the test**

Expected: PASS after Tasks 1-5. If RED, diagnose the integration mismatch before changing `judging.py`.

- [ ] **Step 3: Add a real-adapter/offline exhausted-schema test**

Return two locally-invalid completed Responses despite native schema mode. Call `run_judge(...)` and assert:
- `status == "judge_not_run"`;
- `not_run_reason == "judge_schema_failure"`;
- `judge_quality is None`;
- two bounded diagnostics;
- exactly two provider calls;
- no fabricated verdict.

- [ ] **Step 4: Add a real-adapter output-limit integration test**

Return `status="incomplete"` and `reason="max_output_tokens"`. Assert:
- `judge_not_run`;
- `not_run_reason == "judge_output_limit"`;
- one bounded `output_limit` diagnostic;
- no schema diagnostic;
- one provider call.

- [ ] **Step 5: Run judge integration tests**

```bash
python -m pytest   tests/test_evaluation/test_judging.py   tests/test_deepseek_provider.py   -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add tests/test_evaluation/test_judging.py
git commit -m "test(evaluation): verify native-schema judge integration"
```

**Task 6 stop condition:** if passing requires changing `JudgeVerdict`, prompt text, weights, retry count, thresholds, or error semantics, STOP and return to Sol/High architecture review.

---
### Task 7: Run the consolidated offline readiness gate

**Files:**
- No production modification expected.
- Modify docs only after all verification is green.

**Interfaces:**
- Produces: evidence package for scoped code review.
- Does not authorize provider/live execution.

- [ ] **Step 1: Verify implementation diff scope**

```bash
git diff --name-status dedccd7c129288b9753bb29a7838b8d03f9372ef...HEAD
```

Expected production paths are limited to:
- `src/deep_research/providers/deepseek_provider.py`
- `src/deep_research/providers/factory.py`
- `src/deep_research/providers/__init__.py`
- `src/deep_research/evaluation/cli.py`
- `src/deep_research/evaluation/runner.py`
- `src/deep_research/evaluation/config.py`

plus planned tests/docs.

Any agent, case, evaluator metric, `judging.py`, `models.py`, prompt, or `config.yaml` change requires explicit review before proceeding.

- [ ] **Step 2: Run focused provider/judge/wiring tests**

```bash
python -m pytest   tests/test_deepseek_provider.py   tests/test_provider_factory.py   tests/test_evaluation/test_judging.py   tests/test_evaluation/test_config.py   tests/test_evaluation/test_runner.py   tests/test_evaluation/test_cli.py   tests/test_evaluation/test_suite.py   -q
```

Expected: PASS.

- [ ] **Step 3: Run evaluation/agent/provider contract gate**

```bash
python -m pytest   tests/test_evaluation   tests/test_agents   tests/test_deepseek_provider.py   tests/test_provider_factory.py   -q
```

Expected: PASS. Repository pytest defaults exclude the `live` marker.

- [ ] **Step 4: Run full offline pytest**

```bash
python -m pytest -q
```

Expected: PASS with only known dependency warnings/deselections. Record fresh counts; do not copy historical counts.

- [ ] **Step 5: Run static checks**

```bash
python -m ruff check .
git diff --check
```

Expected: PASS.

- [ ] **Step 6: Verify frozen contract values**

Use the same config loader import path already used by repository tests:

```bash
python - <<'PY'
from pathlib import Path

import yaml

from deep_research.evaluation.models import JudgeVerdict

raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
schema = JudgeVerdict.model_json_schema()

assert raw["llm"]["max_tokens"] == 4096
assert schema["properties"]["rationale"]["minLength"] == 1
assert schema["properties"]["rationale"]["maxLength"] == 2000
assert schema["additionalProperties"] is False
print("frozen judge invariants: OK")
PY
```

This deliberately reads only `config.yaml`; it must not call `load_config()`, load dotenv files, or inspect credential-bearing environment variables.

- [ ] **Step 7: Record verification evidence**

Append one fix-log section containing:
- base SHA;
- candidate HEAD;
- exact changed paths;
- fresh focused/full test counts;
- Ruff result;
- `git diff --check` result;
- confirmation no live/provider/LangSmith/evaluation-suite command ran;
- `max_tokens=4096`;
- target `DeepSeekChatProvider` unchanged in behavior;
- old/new judge configuration fingerprint for the same logical judge config;
- unchanged prompt fingerprint.

Never include credentials, raw provider outputs, full prompts, or hidden reasoning.

- [ ] **Step 8: Commit verification docs**

```bash
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record judge native-schema offline gate"
```

---

### Task 8: Obtain two-stage review and stop before any paid canary

**Files:**
- Review only first.
- Modify after approval:
  - `docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md`
  - `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Produces: reviewed judge-ready candidate or exact blocker list.
- No provider execution.

- [ ] **Step 1: Task-scoped implementation review**

Use `superpowers:requesting-code-review` with:

```text
BASE_SHA=dedccd7c129288b9753bb29a7838b8d03f9372ef
HEAD_SHA=<candidate-head>
```

Review requirements:
- native `json_schema` only for DeepSeek judge;
- target Chat path unchanged;
- existing schema instruction and prompt roles preserved;
- exactly one repair;
- failure taxonomy and secret scrubbing preserved;
- no judge/model/evaluator semantic relaxation;
- 4096 unchanged.

Critical/Important findings block progression.

- [ ] **Step 2: Fresh Sol/High architecture/code review**

Provide:
- exact base/head;
- remote diff;
- focused/full offline evidence;
- official DeepSeek Responses API contract;
- no raw provider output or credentials.

Require separate verdicts for:
1. transport correctness;
2. contract/failure-semantic preservation;
3. target/judge isolation;
4. provenance;
5. readiness for one future canary.

Any unresolved Critical or Important finding -> NO-GO.

- [ ] **Step 3: Update controller docs only after approval**

Update parent plan/fix log to:
- mark judge transport repair offline-complete/reviewed;
- keep live state `NO-GO pending explicit authorization`;
- identify Researcher as preferred first canary because its target gates/deterministic quality are already green and its prior unresolved boundary was judge schema failure.

Commit:

```bash
git add   docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md   docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: gate native-schema judge canary"
```

- [ ] **Step 4: STOP**

Do not run any provider request. Return candidate SHA and review disposition to the user and request separate authorization for one paid canary.

---

## Future Canary Gate — Documentation Only, Not Authorized by This Plan

The implementation worker must not execute this section.

### Preferred first canary: Researcher

Reason:
- target hard gates previously green;
- deterministic quality previously `1.00`;
- no target production repair currently justified;
- prior unresolved boundary was judge `rationale` schema failure.

A single Researcher live repetition is eligible only after:
1. Tasks 1-8 complete;
2. exact implementation range has no unresolved Critical/Important review finding;
3. current branch tip equals reviewed candidate;
4. user explicitly authorizes that one paid command.

### Canary acceptance

The new judge transport is empirically confirmed only if the single repetition has:
- all pre-existing Researcher target hard gates green;
- deterministic quality at its expected passing level;
- `judge.status == "scored"`;
- `judge.not_run_reason is None`;
- `judge.diagnostics == ()`;
- unchanged `JudgeVerdict` validates;
- `judge_quality` present and in `[0, 1]`;
- aggregate quality available;
- prompt fingerprint equal to pre-transport fingerprint for the same rubric;
- judge configuration fingerprint equal to the reviewed native-schema fingerprint;
- no target-side output-limit evidence created by the judge transport change.

If judge is not scored, STOP after that repetition. Preserve typed evidence; do not retry automatically and do not increase tokens.

### After a successful judge canary

Resume sequential agent diagnosis only; do not mass-run all agents.

Recommended order:
1. Researcher — judge canary/control.
2. Source Evaluator — target deterministic-green, previously judge-blocked.
3. Synthesizer — combine stable judge metrics with deterministic `0.75`; require concrete quality evidence before any production repair.
4. Critic — keep intentional `critic_report_review` fallback separate from judge quality.
5. Fact Checker — re-evaluate prior low judge score under stable judge before authorizing any agent repair.
6. Planner remains the passing control.

For each agent:
- one repetition;
- preserve artifact/hash/typed diagnostics;
- separate target defects from judge/provider defects;
- require a concrete offline RED before production repair;
- scoped review;
- one focused confirmation only after explicit authorization.

---

## Plan Self-Review

### Spec coverage
- Judge-only native schema enforcement: Tasks 1-4.
- Prompt/role parity: Task 1.
- Failure taxonomy, output limit, one repair, secret safety: Task 2.
- No target transport change: Tasks 1, 3, 4, 7.
- Provenance/fingerprints: Task 5.
- Unchanged judge semantics: Task 6 and frozen-invariant gate in Task 7.
- Full offline verification: Task 7.
- Human/code review and no-live stop: Task 8.
- Later agent-evaluation sequence: Future Canary Gate.

### Placeholder scan
No placeholder markers, deferred implementation notes, or unspecified test steps remain in the execution tasks.

### Type/interface consistency
- `DeepSeekJudgeProvider` satisfies existing `StructuredCompleter`.
- `build_judge_provider` returns a provider usable by existing `judge_provider_factory`.
- `run_judge`/`JudgeEvaluator` interfaces remain unchanged.
- No new fields are required on `JudgeFeedback`, `EvaluatorDiagnostic`, `RepetitionResult`, or `EvaluationRuntimeConfig`.

## Execution Handoff

Recommended mode: **Subagent-Driven Development**.
- fresh worker for each task;
- task-scoped review after each task;
- coordinator integrates only reviewed commits;
- final Sol/High gate after consolidated offline verification.

Inline execution is possible with `superpowers:executing-plans`, but the provider transport, factory wiring, provenance, and integration tests are clean review boundaries, so fresh workers plus review are safer.
