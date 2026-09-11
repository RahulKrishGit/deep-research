# Tool-Call Structured Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every DeepSeek structured-output request ask for its JSON through a forced tool call instead of relying on output-format enforcement, eliminating the `json_invalid` failures that still occur in roughly one live Critic repetition in four.

**Architecture:** The switch lives in the **provider**, not in the agents. All six agents already reach the model through `DeepSeekSchemaChatProvider.complete_structured`, so one transport change covers every agent without touching a single agent module. A new `llm.structured_transport` setting selects `tool_call` (default) or `json_schema`. Both transports share one extracted repair loop, so the attempt count, the bounded diagnostics, and the fail-closed error type cannot diverge between them. The judge keeps its own already-validated Responses transport.

**Tech Stack:** Python 3.12, Pydantic v2 (`ContractModel`), pytest + pytest-asyncio, ruff, OpenAI SDK against the DeepSeek base URL, LangSmith evaluation harness.

**Spec:** `docs/superpowers/2026-09-11-critic-live-call-production-readiness-design.md` and the evidence record `docs/superpowers/2026-09-11-critic-readiness-confirmation.md`.

## Global Constraints

- Work in the worktree `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity` on branch `codex/cross-agent-planner-fix-parity`. Use the root interpreter `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe`.
- **Every test command must set `PYTHONPATH` to this worktree's `src`.** The root virtualenv's editable install points at a *different* worktree (`planner-remediation-integration`), so without this the tests silently exercise the wrong source tree:
  ```powershell
  $env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
  ```
- Run every command from the worktree root.
- The existing pre-existing failures are `test_a_live_experiment_requests_one_repetition`, `test_the_ledger_records_real_services_for_a_live_run`, and `test_a_live_researcher_records_only_source_url_fingerprints`. They fail on the base commit `035d3c5` too. They are **not** regressions; do not attempt to fix them in this plan.
- All output budgets are `32768` (`llm.max_tokens`, `planner_final_max_tokens`, `critic_review_max_tokens`, `judge_max_tokens`, `react_decision_max_tokens`). Do not lower any of them.
- Do not change the judge's transport. `DeepSeekJudgeProvider` must keep using the Responses `json_schema` path, which has scored every judged repetition with zero schema diagnostics.
- Preserve: the one-repair contract (exactly two structured attempts maximum), `_StructuredValidationFailure` → `StructuredOutputError` fail-closed semantics, the bounded `StructuredValidationDiagnostic` records, the frozen cases, rubrics, metric weights, the `0.75` live threshold, and the fallback semantics.
- Never record raw provider response text, prompts, secrets, or reasoning content in a diagnostic, test, or document. The repair message may carry only the bounded validation summary and the JSON Schema.
- Every task ends with `ruff check` on the files it touched plus `git diff --check`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `src/deep_research/utils/config.py` | Runtime settings | Add `LLMConfig.structured_transport`, its env override, and a validator |
| `src/deep_research/providers/deepseek_provider.py` | DeepSeek transport | Add the tool-call attempt, extract the shared repair loop, dispatch on the setting |
| `config.yaml` | Shipped settings | Document and set `llm.structured_transport: tool_call` |
| `tests/test_config.py` | Settings contract | Transport default, env override, OpenAI rejection |
| `tests/test_deepseek_provider.py` | Transport behaviour | New tool-call tests; existing Responses tests pinned to their transport |

**Scoping note — what "all the agents" means here.** The Critic, Researcher, Fact Checker, Planner, Synthesizer, and Source Evaluator all call `complete_structured` on the provider. There is no per-agent structured-output code to convert. Tasks 1–4 therefore switch every DeepSeek agent at once, and Task 4 proves it with a test that asserts a non-Critic agent's schema also arrives as a tool definition. Task 6 covers the OpenAI adapter separately, because it uses the SDK's own `responses.parse` with different failure characteristics and cannot be validated by this campaign's canary.

---

### Task 1: Add the `structured_transport` setting

**Files:**
- Modify: `src/deep_research/utils/config.py` (the `LLMConfig` field block near `max_tokens`; the `_ENVIRONMENT_OVERRIDES` mapping)
- Modify: `config.yaml` (the `llm:` block)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LLMConfig.structured_transport: Literal["tool_call", "json_schema"]`, default `"tool_call"`; environment variable `LLM_STRUCTURED_TRANSPORT`; YAML key `llm.structured_transport`. Task 2 reads `self._config.structured_transport`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_the_target_structured_transport_defaults_to_tool_call() -> None:
    """Tool calls are the default because schema enforcement alone was not enough.

    Schema-enforced output still produced ``json_invalid`` at the field root on
    both the initial attempt and the single repair in roughly one live Critic
    repetition in four, at every budget up to 32768. A forced tool call makes
    the model commit to a function invocation instead of being trusted to emit
    well-formed text.
    """
    from deep_research.utils.config import LLMConfig

    assert LLMConfig().structured_transport == "tool_call"


def test_the_transport_is_overridable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
) -> None:
    monkeypatch.setenv("LLM_STRUCTURED_TRANSPORT", "json_schema")

    settings = load_config(str(config_path))

    assert settings.llm.structured_transport == "json_schema"


def test_the_shipped_config_file_carries_the_transport() -> None:
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    assert raw["llm"]["structured_transport"] == "tool_call"


def test_the_transport_rejects_an_unknown_value() -> None:
    from pydantic import ValidationError

    from deep_research.utils.config import LLMConfig

    with pytest.raises(ValidationError):
        LLMConfig(structured_transport="magic")


def test_tool_call_transport_is_rejected_for_the_openai_provider() -> None:
    """The setting is DeepSeek-only, and says so instead of silently no-opping.

    The OpenAI adapter asks for structure through the SDK's own
    ``responses.parse``. Accepting ``tool_call`` for it would be a silent no-op
    that reads as configured-but-inactive, so it is rejected loudly.
    """
    from pydantic import ValidationError

    from deep_research.utils.config import LLMConfig

    with pytest.raises(ValidationError, match="tool_call"):
        LLMConfig(provider="openai", structured_transport="tool_call")

    assert LLMConfig(provider="openai").structured_transport == "json_schema"
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_config.py -q -k "transport"
```

Expected: FAIL. `LLMConfig` has no `structured_transport` field, so the default test fails with `AttributeError` and the rejection tests fail because no `ValidationError` is raised.

- [ ] **Step 3: Add the field and its validator**

In `src/deep_research/utils/config.py`, add to `LLMConfig` immediately after the `max_tokens` line (currently line 73, `max_tokens: int = Field(default=32768, ge=1)`):

```python
    # How a DeepSeek target agent's structured request asks for JSON.
    # ``tool_call`` forces a function invocation, so the model commits to a
    # call and cannot drift into prose; ``json_schema`` relies on the
    # provider's output-format enforcement, which still produced
    # ``json_invalid`` in roughly one live repetition in four at every budget
    # up to 32768. DeepSeek-only: the OpenAI adapter uses the SDK's own
    # ``responses.parse``, and asking for ``tool_call`` there is rejected
    # rather than silently ignored.
    structured_transport: Literal["tool_call", "json_schema"] = "tool_call"
```

`LLMConfig` already has `model_config = ConfigDict(extra="forbid")`; confirm that before continuing. Add this validator to `LLMConfig`, next to any existing validator in the class:

```python
    @model_validator(mode="after")
    def validate_structured_transport(self) -> "LLMConfig":
        if self.provider == "openai" and self.structured_transport == "tool_call":
            raise ValueError(
                "structured_transport 'tool_call' is only supported for the "
                "deepseek provider; the openai adapter structures output "
                "through the SDK's own responses.parse"
            )
        return self
```

Add `model_validator` to the pydantic import at the top of the file if it is not already there.

- [ ] **Step 4: Add the environment override**

In the `_ENVIRONMENT_OVERRIDES` mapping, immediately after the `"LLM_MAX_TOKENS": ("llm", "max_tokens"),` entry:

```python
    "LLM_STRUCTURED_TRANSPORT": ("llm", "structured_transport"),
```

- [ ] **Step 5: Set the shipped YAML value**

In `config.yaml`, in the `llm:` block, immediately after the `max_tokens: 32768` line:

```yaml
  # How target agents ask for structured JSON. tool_call forces a function
  # invocation, so the model commits to a call rather than being trusted to
  # emit well-formed text; json_schema relies on output-format enforcement,
  # which still returned non-JSON text in about one live repetition in four.
  # DeepSeek-only; the OpenAI adapter is unaffected.
  structured_transport: tool_call
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 7: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/utils/config.py tests/test_config.py
git diff --check
git add src/deep_research/utils/config.py config.yaml tests/test_config.py
git commit -m "feat(config): add a selectable structured-output transport"
```

---

### Task 2: Add the forced-tool-call attempt

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py` (add two module-level helpers before `_DeepSeekSchemaStructuredProvider`; add `_tool_call_structured_attempt` to that class)
- Test: `tests/test_deepseek_provider.py`

**Interfaces:**
- Consumes: `LLMConfig.structured_transport` (Task 1); existing `_response_telemetry`, `_set_span_result`, `_validation_diagnostic`, `_StructuredValidationFailure`, `with_retries`, `_raise_deepseek_error`, `ProviderOutputLimitError`, `ProviderResponseError`.
- Produces: `_tool_schema(schema: type[BaseModel]) -> dict[str, object]`; `_tool_call_arguments(response: Any) -> str`; `_DeepSeekSchemaStructuredProvider._tool_call_structured_attempt(messages, schema, *, model, request, metadata, configured_max_tokens, attempt) -> SchemaT`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deepseek_provider.py`. These use the existing helpers `RecordingCompletions` (line 53), `FakeDeepSeekClient` (line 79), `chat_response` (line 91), `local_tracker` (line 172), `deepseek_config` (line 199), and `TinyAnswer` (line 211).

Add a helper next to the other response builders, after `chat_response`:

```python
def tool_call_response(
    *,
    arguments: object,
    name: str = "TinyAnswer",
    finish_reason: object = "stop",
) -> SimpleNamespace:
    """A Chat Completions response carrying exactly one tool call."""
    return SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(name=name, arguments=arguments),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=4, completion_tokens=2, total_tokens=6
        ),
    )
```

Then the tests:

```python
@pytest.mark.asyncio
async def test_tool_call_transport_sends_a_forced_function_call() -> None:
    completions = RecordingCompletions(
        tool_call_response(arguments='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")],
            TinyAnswer,
            agent_name="critic",
        )

    assert result == TinyAnswer(answer="yes", confidence=9)
    call = completions.calls[0]
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": "TinyAnswer"},
    }
    assert call["tools"][0]["type"] == "function"
    function = call["tools"][0]["function"]
    assert function["name"] == "TinyAnswer"
    # The parameters are the schema itself, minus prose-only documentation.
    assert function["parameters"]["properties"]["answer"] == {"type": "string"}
    assert function["parameters"]["required"] == ["answer", "confidence"]
    assert "title" not in function["parameters"]
    assert "description" not in function["parameters"]
    # The old Responses shape is absent, and schema JSON is not restated in
    # the prompt: the tool definition carries it.
    assert "text" not in call
    assert "response_format" not in call
    assert "JSON Schema" not in str(call["messages"])


@pytest.mark.asyncio
async def test_tool_call_transport_repairs_exactly_once() -> None:
    completions = RecordingCompletions(
        tool_call_response(arguments='{"answer":3}'),
        tool_call_response(arguments='{"answer":"yes","confidence":8}'),
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result.confidence == 8
    assert len(completions.calls) == 2
    assert (
        "previous JSON response failed TinyAnswer validation"
        in str(completions.calls[1]["messages"][-1]["content"])
    )


@pytest.mark.asyncio
async def test_tool_call_transport_exhaustion_is_typed_and_bounded() -> None:
    completions = RecordingCompletions(
        tool_call_response(arguments="I could not answer that."),
        tool_call_response(arguments="Still not JSON."),
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(contracts_module.StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    diagnostics = caught.value.diagnostics
    assert [item.attempt for item in diagnostics] == [1, 2]
    assert [item.category for item in diagnostics] == [
        "json_invalid",
        "json_invalid",
    ]
    assert "I could not answer that." not in str(caught.value)
    assert len(completions.calls) == 2


@pytest.mark.asyncio
async def test_tool_call_transport_rejects_a_prose_answer() -> None:
    """A model that ignored tool_choice is a typed response error, not a parse."""
    prose = chat_response(text="Here is my review: the report is fine.")
    completions = RecordingCompletions(prose, prose)
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(contracts_module.ProviderResponseError):
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )


@pytest.mark.asyncio
async def test_tool_call_transport_output_limit_stays_typed() -> None:
    completions = RecordingCompletions(
        tool_call_response(arguments="{}", finish_reason="length")
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(contracts_module.ProviderOutputLimitError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert caught.value.telemetry.finish_reason_category == "length"
    assert len(completions.calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q -k "tool_call_transport"
```

Expected: FAIL. The provider still sends the Responses `json_schema` request, so `call["tool_choice"]` raises `KeyError` and `RecordingResponses` is never used.

- [ ] **Step 3: Add the two module-level helpers**

In `src/deep_research/providers/deepseek_provider.py`, immediately before `class _DeepSeekSchemaStructuredProvider(DeepSeekChatProvider):`, add:

```python
def _tool_schema(schema: type[BaseModel]) -> dict[str, object]:
    """The tool's ``parameters`` object for one Pydantic schema.

    ``description`` and ``title`` are dropped at the top level: they document a
    schema for a human reader rather than constraining a value, and a tool
    definition does not need them.
    """
    payload = dict(schema.model_json_schema())
    payload.pop("description", None)
    payload.pop("title", None)
    return payload


def _tool_call_arguments(response: Any) -> str:
    """Extract the forced tool call's JSON argument string.

    Reads exactly one choice carrying exactly one tool call, and fails closed on
    every other shape. A model that ignored ``tool_choice`` and answered in
    prose is therefore a typed response error rather than a silent empty parse.
    """
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        raise ProviderResponseError("DeepSeek response contained malformed choices")
    message = getattr(choices[0], "message", None)
    tool_calls = (
        getattr(message, "tool_calls", None) if message is not None else None
    )
    if not isinstance(tool_calls, (list, tuple)) or len(tool_calls) != 1:
        raise ProviderResponseError(
            "DeepSeek response did not contain exactly one tool call"
        )
    function = getattr(tool_calls[0], "function", None)
    arguments = (
        getattr(function, "arguments", None) if function is not None else None
    )
    if not isinstance(arguments, str) or not arguments.strip():
        raise ProviderResponseError("DeepSeek tool call did not contain arguments")
    return arguments.strip()
```

- [ ] **Step 4: Add the attempt method**

Inside `_DeepSeekSchemaStructuredProvider`, immediately before its `complete_structured` method, add:

```python
    async def _tool_call_structured_attempt(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        configured_max_tokens: int,
        attempt: int,
    ) -> SchemaT:
        """One forced-tool-call attempt on Chat Completions.

        The schema is offered as a required function and ``tool_choice`` forces
        it, so the model commits to an invocation instead of being trusted to
        emit well-formed text. The argument string is still validated locally,
        so Pydantic stays the source of truth and the one-repair flow is
        unchanged.
        """
        async with self._tracker.llm_span(
            model,
            {
                **metadata,
                "operation": "structured_output",
                "attempt": attempt,
                "message_count": len(messages),
                "structured_transport": "tool_call",
            },
        ) as span:
            _sdk = _openai_errors()
            request_attempt = 0

            async def _request() -> Any:
                nonlocal request_attempt
                request_attempt += 1
                try:
                    return await self._client.chat.completions.create(
                        **{
                            **request,
                            "messages": messages,
                            "max_tokens": configured_max_tokens,
                            "tools": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": schema.__name__,
                                        "description": (
                                            "Return the structured result as "
                                            "this function's arguments."
                                        ),
                                        "parameters": _tool_schema(schema),
                                    },
                                }
                            ],
                            "tool_choice": {
                                "type": "function",
                                "function": {"name": schema.__name__},
                            },
                        }
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    _raise_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek tool-call request failed"
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            telemetry = _response_telemetry(
                response,
                configured_max_tokens=configured_max_tokens,
                request_attempt=request_attempt,
                structured_attempt=attempt,
            )
            _set_span_result(span, telemetry)
            if telemetry.finish_reason_category == "length":
                raise ProviderOutputLimitError(telemetry)
            arguments = _tool_call_arguments(response)
            try:
                parsed = schema.model_validate_json(arguments)
            except (json.JSONDecodeError, ValidationError) as error:
                diagnostic = _validation_diagnostic(
                    error, attempt=attempt, schema=schema
                )
            else:
                self._last_model_returned = (
                    getattr(response, "model", None) or model
                )
                return parsed
            raise _StructuredValidationFailure(
                schema.__name__, diagnostic
            ) from None
```

- [ ] **Step 5: Run the tests to verify the request shape passes**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q -k "tool_call_transport_sends or tool_call_transport_rejects or tool_call_transport_output_limit"
```

Expected: PASS. The repair and exhaustion tests still fail, because `complete_structured` does not yet dispatch to the new attempt.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git diff --check
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat(provider): add a forced-tool-call structured attempt"
```

---

### Task 3: Extract the shared repair loop and dispatch on the transport

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py` (`DeepSeekChatProvider.complete_structured`, `_DeepSeekSchemaStructuredProvider.complete_structured`)
- Test: `tests/test_deepseek_provider.py`

**Interfaces:**
- Consumes: `_tool_call_structured_attempt` (Task 2); `LLMConfig.structured_transport` (Task 1).
- Produces: `DeepSeekChatProvider._structured_repair_loop(current_messages, schema, *, attempt_call, model, request, metadata, configured_max_tokens) -> SchemaT`, a module-shared helper that both transports call.

Extracting the loop is what guarantees the transport switch cannot alter the attempt count, the diagnostics, or the error type. Both transports must reach it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deepseek_provider.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "expected_attempts"),
    [
        ("json_schema", 2),
        ("tool_call", 2),
    ],
)
async def test_both_transports_allow_exactly_one_repair(
    transport: str, expected_attempts: int
) -> None:
    """The transport is the only difference; the attempt budget is shared.

    A second attempt must be reachable on either transport, and a third must
    never be. The one-repair contract is a campaign invariant, not a property
    of one transport implementation.
    """
    if transport == "json_schema":
        outcomes = [
            responses_response(output_text='{"answer":3}'),
            responses_response(output_text='{"answer":3}'),
        ]
        client = FakeDeepSeekClient(
            responses=RecordingResponses(*outcomes)
        )
        recorder = client.responses
    else:
        outcomes = [
            tool_call_response(arguments='{"answer":3}'),
            tool_call_response(arguments='{"answer":3}'),
        ]
        client = FakeDeepSeekClient(RecordingCompletions(*outcomes))
        recorder = client.chat.completions
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport=transport),
        tracker,
        client=client,
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(contracts_module.StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(recorder.calls) == expected_attempts
    assert [item.attempt for item in caught.value.diagnostics] == [1, 2]
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q -k "both_transports_allow_exactly_one_repair"
```

Expected: FAIL. The `tool_call` case raises `TypeError` because `complete_structured` never dispatches to `_tool_call_structured_attempt`.

- [ ] **Step 3: Move the loop into a shared method on `DeepSeekChatProvider`**

In `src/deep_research/providers/deepseek_provider.py`, cut the entire attempt loop out of `_DeepSeekSchemaStructuredProvider.complete_structured` (the `diagnostics = []` line through the final `raise final_error`) and re-add it as this method on **`DeepSeekChatProvider`**, placed immediately after `DeepSeekChatProvider.complete_structured`:

```python
    async def _structured_repair_loop(
        self,
        current_messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        attempt_call: Any,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        configured_max_tokens: int,
    ) -> SchemaT:
        """One initial attempt plus exactly one repair, for any transport.

        Shared so that changing transport cannot silently change the repair
        count, the bounded diagnostics, or the fail-closed error type.
        """
        diagnostics: list[StructuredValidationDiagnostic] = []
        final_error: StructuredOutputError | None = None
        for attempt in (1, 2):
            try:
                return await attempt_call(
                    current_messages,
                    schema,
                    model=model,
                    request=request,
                    metadata=metadata,
                    configured_max_tokens=configured_max_tokens,
                    attempt=attempt,
                )
            except _StructuredValidationFailure as error:
                diagnostics.append(error.diagnostic)
                if attempt == 2:
                    final_error = StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    )
                    break
                schema_json = json.dumps(
                    schema.model_json_schema(),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                repair_guidance = _validation_repair_guidance(error.diagnostic)
                repair = (
                    f"The previous JSON response failed {schema.__name__} "
                    "validation. Return only one JSON object that validates "
                    "against the supplied JSON Schema. Do not add Markdown or "
                    "explanatory text. "
                    f"Validation summary: {_validation_summary(error.diagnostic)}\n"
                    f"{repair_guidance}"
                    f"JSON Schema:\n{schema_json}"
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        if final_error is None:
            raise AssertionError("structured output attempt loop did not return")

        # Do not raise while handling the internal validation failure: that
        # would retain it through ``__context__``/``__cause__``. Clear all
        # provider-adjacent locals before the public error's traceback is
        # captured, leaving only the bounded typed diagnostics.
        self = None
        current_messages = []
        request = {}
        metadata = {}
        model = ""
        schema = BaseModel
        schema_json = ""
        repair = ""
        repair_guidance = ""
        raise final_error
```

- [ ] **Step 4: Dispatch in `_DeepSeekSchemaStructuredProvider.complete_structured`**

That method's body becomes exactly this, with the Responses branch delegating to the shared loop instead of inlining it:

```python
        if not messages:
            raise ValueError("messages must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(
            self._config.max_tokens, max_tokens
        )
        if self._config.structured_transport == "tool_call":
            effective, request, metadata = self._request_options(agent_name)
            return await self._structured_repair_loop(
                _translated_messages(messages),
                schema,
                attempt_call=self._tool_call_structured_attempt,
                model=effective.model,
                request=request,
                metadata=metadata,
                configured_max_tokens=resolved_max_tokens,
            )
        effective, request, metadata = _responses_request_options(
            self._config, agent_name
        )
        instruction = _json_instruction(schema)
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]
        return await self._structured_repair_loop(
            current_messages,
            schema,
            attempt_call=self._responses_structured_attempt,
            model=effective.model,
            request=request,
            metadata=metadata,
            configured_max_tokens=resolved_max_tokens,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q
```

Expected: PASS for the new tests. The six existing `test_schema_target_*` tests fail, because the default transport is now `tool_call` and they assert the Responses request shape. Task 4 repairs them.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/providers/deepseek_provider.py
git diff --check
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "refactor(provider): share one repair loop across both transports"
```

---

### Task 4: Pin the Responses tests and prove every agent inherits the switch

**Files:**
- Modify: `tests/test_deepseek_provider.py` (six `test_schema_target_*` tests)
- Test: `tests/test_deepseek_provider.py`, `tests/test_agents/`

**Interfaces:**
- Consumes: the transport setting (Task 1) and both attempt methods (Tasks 2–3).
- Produces: no new production symbol. Proves the claim that no agent module needs changing.

- [ ] **Step 1: Pin the six existing Responses tests to their transport**

In `tests/test_deepseek_provider.py`, the tests `test_schema_target_structured_uses_responses_json_schema` (line ~2541), `test_schema_target_responses_carries_thinking_effort` (line ~2588), `test_schema_target_validation_failure_repairs_exactly_once` (line ~2610), `test_schema_target_unparseable_output_is_json_invalid_at_root` (line ~2636), and `test_schema_target_output_limit_stays_typed` (line ~2665) each construct a provider with a `FakeDeepSeekClient(responses=...)` client. Change each construction's config argument from `deepseek_config()` to:

```python
        deepseek_config(structured_transport="json_schema"),
```

`test_schema_target_plain_completion_stays_on_chat_completions` (line ~2569) uses `FakeDeepSeekClient(completions)` and tests the plain path, which is unaffected; leave it unchanged.

Also rename the module's section comment so it no longer claims the Responses transport is the target default. Replace the comment block beginning `# --- Schema-enforced target structured transport ---` with:

```python
# --- Target structured transports ---------------------------------------
#
# Two transports are selectable. ``json_schema`` asks the Responses endpoint to
# enforce the schema; ``tool_call`` forces a function invocation on Chat
# Completions. Live Critic canaries recorded ``json_invalid`` at ``$`` on both
# the initial attempt and the single repair under ``json_schema``, in roughly
# one repetition in four at every budget up to 32768, so ``tool_call`` is the
# default. Tests that assert a specific request shape pin the transport they
# exercise.
```

- [ ] **Step 2: Add the cross-agent inheritance test**

Append to `tests/test_deepseek_provider.py`:

```python
@pytest.mark.asyncio
async def test_every_agent_schema_becomes_a_tool_definition() -> None:
    """No agent module changes when the transport changes.

    All six agents reach the model through this one provider method, so the
    transport is a provider concern. This pins that: a non-Critic agent's
    schema arrives as a forced tool definition, driven only by the setting.
    """
    from deep_research.evaluation.models import JudgeVerdict

    completions = RecordingCompletions(
        tool_call_response(
            arguments=json.dumps(
                {
                    "scores": {
                        "role_adherence": 1.0,
                        "completeness": 1.0,
                        "groundedness": 1.0,
                        "reasoning_quality": 1.0,
                        "usefulness": 1.0,
                        "uncertainty_calibration": 1.0,
                    },
                    "rationale": "Grounded.",
                }
            ),
            name="JudgeVerdict",
        )
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="judge this")],
            JudgeVerdict,
            agent_name="synthesizer",
        )

    assert isinstance(result, JudgeVerdict)
    call = completions.calls[0]
    assert call["tools"][0]["function"]["name"] == "JudgeVerdict"
    assert call["tool_choice"]["function"]["name"] == "JudgeVerdict"
    assert "scores" in call["tools"][0]["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_the_agent_modules_do_not_mention_the_transport() -> None:
    """The switch is provider-level, so no agent module names a transport."""
    import inspect

    import deep_research.agents.critic as critic_module
    import deep_research.agents.fact_checker as fact_checker_module
    import deep_research.agents.planner as planner_module
    import deep_research.agents.researcher as researcher_module
    import deep_research.agents.source_evaluator as source_evaluator_module
    import deep_research.agents.synthesizer as synthesizer_module

    for module in (
        critic_module,
        fact_checker_module,
        planner_module,
        researcher_module,
        source_evaluator_module,
        synthesizer_module,
    ):
        source = inspect.getsource(module)
        assert "structured_transport" not in source, module.__name__
        assert "tool_choice" not in source, module.__name__
```

- [ ] **Step 3: Run the tests to verify they pass**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q
```

Expected: PASS.

- [ ] **Step 4: Prove the tool-call tests are not vacuous**

Temporarily change the default in `src/deep_research/utils/config.py` from `"tool_call"` to `"json_schema"`, then run the tool-call tests and confirm they fail:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_deepseek_provider.py -q -k "tool_call_transport"
```

Expected: FAIL (the requests go to the Responses shape). Revert the default to `"tool_call"` and re-run to confirm PASS. Do not commit while the default is flipped.

- [ ] **Step 5: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check tests/test_deepseek_provider.py
git diff --check
git add tests/test_deepseek_provider.py
git commit -m "test(provider): pin both transports and prove agents inherit the switch"
```

---

### Task 5: State the JSON contract in the review request itself

**Files:**
- Modify: `src/deep_research/agents/prompts.py` (`CRITIQUE_INSTRUCTION`)
- Modify: `src/deep_research/agents/critic.py` (`critique_messages`)
- Test: `tests/test_agents/test_critic.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. This task is independent of the transport switch and can land before or after Tasks 1–4.
- Produces: no new symbol. `CRITIQUE_INSTRUCTION` and the rendered review body change, so the Critic's target prompt fingerprint moves.

This is defence in depth, not the fix. `critique_messages` never used the word "JSON" at all: the only place JSON was requested was the trailing system message the provider appends. Task 7's canary note depends on this task having landed, because it is the reason the target prompt fingerprint changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_critic.py`:

```python
def test_the_review_request_demands_raw_json() -> None:
    """The review body must ask for JSON in words, not only via the transport.

    The structured call failed with ``json_invalid`` at the field root on both
    the initial attempt and the single repair in roughly one live repetition in
    four. ``critique_messages`` never contained the word JSON; only a trailing
    provider-built system message did. The transport is fixed separately in
    this plan; this pins the belt-and-braces prompt contract.
    """
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content

    assert "Reply with one JSON object and nothing else" in body
    assert "Do not wrap it in Markdown code fences" in body
    assert "## Reply format" in body
    # The concrete shape is the last thing the model reads.
    assert body.rstrip().endswith(
        '"recommended_queries": ["..."], "rationale": "..."}'
    )
    for field in ("score", "gaps", "unsupported_claims", "rationale"):
        assert f'"{field}"' in body


def test_the_review_request_keeps_the_quality_contract_intact() -> None:
    """The JSON demand is additive; the scoring contract is unchanged."""
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content

    assert "an integer from 1 to 10" in body
    assert "list a gap only when closing it would materially change the" in body
    assert "Do not decide whether research continues" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q -k "raw_json"
```

Expected: FAIL on `"Reply with one JSON object and nothing else"`.

- [ ] **Step 3: Extend the instruction**

In `src/deep_research/agents/prompts.py`, append these sentences to the end of `CRITIQUE_INSTRUCTION` (inside the existing tuple, after the `"from your score, your gaps, and the remaining budget."` element):

```python
    "\nReply with one JSON object and nothing else. Do not wrap it in Markdown "
    "code fences, do not write any sentence before or after it, and do not "
    "add fields beyond the ones above. The first character of your reply "
    "must be { and the last must be }."
```

- [ ] **Step 4: Add the concrete reply shape to the rendered request**

In `src/deep_research/agents/critic.py`, in `critique_messages`, change the final element of `sections` so the reply format is the last thing the model reads:

```python
        f"## Response contract\n{CRITIQUE_INSTRUCTION}",
        (
            "## Reply format\n"
            "Respond with exactly this JSON object shape and nothing else:\n"
            '{"score": 8, "gaps": ["..."], "unsupported_claims": ["..."], '
            '"recommended_queries": ["..."], "rationale": "..."}'
        ),
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q
```

Expected: PASS, including the pre-existing `test_first_spot_check_receives_planned_search_query_guidance`.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/agents/prompts.py src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git diff --check
git add src/deep_research/agents/prompts.py src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git commit -m "fix(critic): state the JSON reply contract in the review request"
```

---

### Task 6: Full offline gate

**Files:**
- No source changes.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: a recorded passing offline gate. Task 7 must not start without it.

- [ ] **Step 1: Run the full suite**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest -q
```

Expected: the three named pre-existing failures plus roughly `2098` passing tests, and **no new failures**. The plan's base commit `a2ecc1c` recorded exactly `3 failed, 2086 passed, 1 deselected`. Tasks 1–5 add roughly 13 tests. Record the exact numbers. Any failure outside those three names is a regression from this plan: stop and diagnose rather than proceeding.

- [ ] **Step 2: Run ruff and whitespace checks**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src tests
git diff --check
```

Expected: both clean.

- [ ] **Step 3: Confirm no budget or threshold moved**

```powershell
git diff a2ecc1c -- config.yaml
git diff a2ecc1c --stat
```

Expected: `config.yaml` shows only the added `structured_transport` key and its comment; no `max_tokens` value, retry value, temperature, or threshold changed.

- [ ] **Step 4: Commit the gate record**

Append the suite counts and the ruff result to
`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`, following that file's existing numbered-section format.

```powershell
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record the tool-call transport offline gate"
```

---

### Task 7: Live canary, then decide the OpenAI scope

**Files:**
- Create: `docs/superpowers/2026-09-11-tool-call-transport-canary.md`

**Interfaces:**
- Consumes: the offline gate (Task 6), the frozen configuration, the `critic-live-review` case.
- Produces: a recorded canary result and the evidence needed to decide whether the OpenAI adapter also moves to tool calls.

**This task spends money. Do not run it without the user's explicit go-ahead in this session.**

- [ ] **Step 1: Ask for authorization**

Ask the user in one message for explicit authorization for four live Critic repetitions on the frozen configuration, and wait for a yes. Four is the number that distinguishes the observed `1-in-4` failure rate from zero; three cannot.

- [ ] **Step 2: Confirm a clean tree and record the candidate**

```powershell
git status --short
git rev-parse HEAD
```

Expected: only the untracked `.deepseek-runs/` directory, which is pre-existing. Record the full commit SHA.

- [ ] **Step 3: Run four sequential repetitions**

```powershell
$env:PYTHONPATH = "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src"
$launcher = ".superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py"
foreach ($n in 1,2,3,4) {
  & "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" $launcher "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.env" "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m deep_research.evaluation agent critic --tier live --config config.yaml --output-directory output/evaluations/live-critic-toolcall --experiment-prefix critic-toolcall-<SHORT_SHA>-r$n --verbose
  Write-Host "---- r$n exit: $LASTEXITCODE ----"
}
```

Substitute `<SHORT_SHA>`. Run sequentially; no retry, no second agent, no suite.

- [ ] **Step 4: Read the typed result per repetition**

For each artifact record: case passed, aggregate quality, `deterministic_metrics`, the `fallback_provider_diagnostic` if present, `review_produced`, judge status, and `react_stop_reason`. Hash every artifact.

```powershell
Get-ChildItem "output\evaluations\live-critic-toolcall" -Recurse -File -Filter "results.json" |
  ForEach-Object { "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)  $($_.FullName)" }
```

- [ ] **Step 5: Write the canary note**

Create `docs/superpowers/2026-09-11-tool-call-transport-canary.md` following the structure of
`docs/superpowers/2026-09-11-critic-readiness-final-canary.md`: Scope, Configuration, Evidence with artifact SHA-256 values, Typed result table for all four repetitions, then the boundary.

The note must state, explicitly:

1. How many of the four repetitions produced a review (`review_produced` passed) and how many fell back.
2. Whether any repetition recorded a `critic_report_review` fallback, and if so its `diagnostics` categories and field paths.
3. The comparison to the `json_schema` baseline in
   `docs/superpowers/2026-09-11-critic-readiness-confirmation.md`, where the observed review-fallback rate was `1 in 4`.
4. That the target prompt fingerprint changed, because `critique_messages` gained the reply-format section, so scores before and after are not comparable on that axis.

- [ ] **Step 6: Decide the OpenAI scope from the recorded evidence**

Add a section to the same note titled `## OpenAI adapter decision`, choosing exactly one outcome and stating the evidence for it:

- **Move OpenAI to tool calls** — only if the DeepSeek tool-call repetitions produced a review in all four runs **and** at least one repetition previously failed under `json_schema` in the same session. Then open a separate plan: the OpenAI adapter uses `client.responses.parse(**{**request, "input": payload, "text_format": schema})` (`openai_provider.py:318-320`), which has different failure characteristics and cannot be validated by this campaign's DeepSeek canary.
- **Leave the OpenAI adapter unchanged** — otherwise. Record that the OpenAI adapter is out of scope because its SDK-level parse path shows no observed failure in this campaign, and that the setting is already rejected for that provider rather than silently ignored.

Do not change `openai_provider.py` in this plan either way.

- [ ] **Step 7: Commit the canary note**

```powershell
git add docs/superpowers/2026-09-11-tool-call-transport-canary.md
git commit -m "docs: record the tool-call transport canary"
```

---

## Self-Review

**Spec coverage.** The user's requirement is "convert structured output into a tool call, and if it works update all the agents". Task 1 adds the selectable transport; Task 2 implements the tool-call mechanism; Task 3 makes both transports share one repair loop; Task 4 proves every agent inherits the switch without any agent-file change, and pins the existing Responses tests to their transport; Task 5 states the JSON contract in the review request; Task 6 is the offline gate; Task 7 is the paid canary that decides the "if it works" condition and scopes the OpenAI adapter from evidence. The earlier `json_invalid` evidence and the `review_produced` gate from the previous session are the justification and are cited rather than restated.

**What this plan deliberately does not do.** It does not convert the six agent modules, because they contain no structured-output code to convert — the claim is pinned by `test_the_agent_modules_do_not_mention_the_transport`. It does not touch `openai_provider.py`, because that adapter uses a different mechanism (`responses.parse`) with different failure behaviour and no observed failure in this campaign; Task 7 makes that decision explicit and evidence-based instead of assumed.

**Type consistency.** `_tool_schema` returns `dict[str, object]` and is used only as `parameters`. `_tool_call_arguments` returns `str` and feeds `schema.model_validate_json`. `_tool_call_structured_attempt` matches the keyword signature the shared loop calls it with (`messages, schema, *, model, request, metadata, configured_max_tokens, attempt`) and returns `SchemaT`. `_structured_repair_loop` is defined on `DeepSeekChatProvider` and called as `self._structured_repair_loop(...)` from `_DeepSeekSchemaStructuredProvider`, which inherits it. `attempt_call` is passed as a bound method and called with the same keyword set it declares.

**Placeholder scan.** No step says "TBD", "implement later", "add validation", or "similar to Task N". Every code step carries runnable code. One substitution token exists by necessity — `<SHORT_SHA>` in Task 7 Step 3 — because it is the hash of a commit Task 6 has not yet produced; Task 7 Step 2 records it immediately before use.

**Known risk to watch in Task 7.** A forced tool call changes what the model is being asked to do, so a `json_invalid` rate of zero could come with a change in output quality rather than an improvement. The canary note therefore requires the aggregate quality of each passing repetition to be recorded alongside the fallback count, so a future reader can see whether quality held rather than only that the failures stopped.
