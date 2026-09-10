"""Unit tests for the project-owned DeepSeek chat provider boundary."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAIError,
    RateLimitError,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    create_model,
    field_validator,
)

import deep_research.providers.contracts as contracts_module
import deep_research.providers.deepseek_provider as deepseek_module
from deep_research.agents.steps import ReActDecision
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsage,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers.deepseek_provider import (
    DEEPSEEK_BASE_URL,
    ChatMessage,
    DeepSeekChatProvider,
    ProviderConfigurationError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.utils.config import LLMConfig


class RecordingCompletions:
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
    def __init__(self, completions: RecordingCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def chat_response(
    *,
    text: object = "answer",
    finish_reason: object = "stop",
    prompt_tokens: object = 4,
    completion_tokens: object = 2,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=text, reasoning_content=reasoning_content
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                prompt_tokens + completion_tokens
                if isinstance(prompt_tokens, int)
                and not isinstance(prompt_tokens, bool)
                and isinstance(completion_tokens, int)
                and not isinstance(completion_tokens, bool)
                else None
            ),
        ),
    )


class CapturingTracker(Tracker):
    def __init__(self) -> None:
        super().__init__(LangSmithRuntimeConfig(tracing_enabled=False))
        self.llm_inputs: list[dict[str, object]] = []
        self.llm_outputs: list[dict[str, object] | None] = []

    def llm_span(self, model, inputs):
        self.llm_inputs.append(dict(inputs))
        manager = super().llm_span(model, inputs)

        @asynccontextmanager
        async def capture_outputs():
            async with manager as span:
                try:
                    yield span
                finally:
                    self.llm_outputs.append(span.outputs)

        return capture_outputs()


def local_tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def _provider_exception_surfaces(error: BaseException) -> list[str]:
    """Collect public exception data and provider traceback locals only."""
    surfaces: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        surfaces.append(repr((current.args, vars(current))))
        traceback = current.__traceback__
        while traceback is not None:
            filename = traceback.tb_frame.f_code.co_filename.replace("\\", "/")
            if "/src/deep_research/providers/" in filename:
                surfaces.append(repr(traceback.tb_frame.f_locals))
            traceback = traceback.tb_next
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return surfaces


def deepseek_config(**updates: object) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            **updates,
        }
    )


class TinyAnswer(BaseModel):
    answer: str
    confidence: int


class NestedDiagnosticPayload(BaseModel):
    count: int = Field(ge=1, le=4)
    label: str = Field(min_length=2, max_length=5)


class StrictDiagnosticEnvelope(BaseModel):
    nested: NestedDiagnosticPayload
    required: str

    model_config = ConfigDict(extra="forbid")


class RootDiagnosticPayload(RootModel[list[str]]):
    pass


class OtherDiagnosticPayload(BaseModel):
    answer: str

    @field_validator("answer")
    @classmethod
    def reject_answer(cls, value: str) -> str:
        del value
        raise ValueError("answer is not accepted")


def test_deepseek_requires_key_without_injected_client(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(
        ProviderConfigurationError,
        match=r"^DEEPSEEK_API_KEY is required when no DeepSeek client is injected",
    ):
        DeepSeekChatProvider(deepseek_config(), local_tracker())


def test_explicit_blank_deepseek_key_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    with pytest.raises(
        ProviderConfigurationError,
        match=r"^DEEPSEEK_API_KEY is required when no DeepSeek client is injected",
    ):
        DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="")


def test_deepseek_builds_openai_compatible_client_with_code_owned_url(
    monkeypatch,
) -> None:
    constructed: list[dict[str, object]] = []

    class RecordingAsyncOpenAI:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(
        deepseek_module._openai_errors(), "AsyncOpenAI", RecordingAsyncOpenAI
    )
    DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="deepseek-key")

    assert constructed == [
        {
            "api_key": "deepseek-key",
            "base_url": DEEPSEEK_BASE_URL,
            "timeout": 60.0,
            "max_retries": 0,
        }
    ]


def test_deepseek_injected_client_requires_no_key_and_is_retained(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    injected = FakeDeepSeekClient(RecordingCompletions())
    provider = DeepSeekChatProvider(deepseek_config(), local_tracker(), client=injected)
    assert provider._client is injected


@pytest.mark.asyncio
async def test_deepseek_plain_completion_translates_roles_and_thinking() -> None:
    completions = RecordingCompletions(
        chat_response(text="  concise answer  ", prompt_tokens=8, completion_tokens=3)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [
                ChatMessage(role="developer", content="policy"),
                ChatMessage(role="user", content="question"),
            ],
            agent_name="planner",
        )

    assert result.text == "concise answer"
    assert result.model == "deepseek-v4-flash"
    assert result.usage.model_dump() == {
        "input_tokens": 8,
        "output_tokens": 3,
        "total_tokens": 11,
    }
    call = completions.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "question"},
    ]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 4096
    assert "temperature" not in call


@pytest.mark.asyncio
async def test_deepseek_disabled_thinking_omits_effort_and_sends_temperature() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(
            thinking_mode="disabled", reasoning_effort="max", temperature=0.2
        ),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="question")])

    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["temperature"] == 0.2


@pytest.mark.asyncio
async def test_deepseek_translation_preserves_system_user_assistant_order() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete(
            [
                ChatMessage(role="system", content="first system"),
                ChatMessage(role="user", content="second user"),
                ChatMessage(role="assistant", content="third assistant"),
                ChatMessage(role="user", content="fourth user"),
            ]
        )

    assert completions.calls[0]["messages"] == [
        {"role": "system", "content": "first system"},
        {"role": "user", "content": "second user"},
        {"role": "assistant", "content": "third assistant"},
        {"role": "user", "content": "fourth user"},
    ]


@pytest.mark.asyncio
async def test_deepseek_complete_rejects_empty_messages() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    with pytest.raises(ValueError, match="messages must contain at least one item"):
        await provider.complete([])

    assert completions.calls == []


@pytest.mark.asyncio
async def test_deepseek_absent_usage_maps_to_zero_tokens() -> None:
    completions = RecordingCompletions(
        SimpleNamespace(
            id="deepseek-response",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="answer"),
                )
            ],
        )
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [ChatMessage(role="user", content="question")]
        )

    assert result.usage.model_dump() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        SimpleNamespace(prompt_tokens=True, completion_tokens=2),
        SimpleNamespace(prompt_tokens="8", completion_tokens=2),
        SimpleNamespace(prompt_tokens=8, completion_tokens=-1),
        SimpleNamespace(prompt_tokens=None, completion_tokens=2),
        SimpleNamespace(prompt_tokens=8, completion_tokens=None),
        SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=5),
        SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens="11"),
        SimpleNamespace(prompt_tokens=8, completion_tokens=True),
    ],
)
async def test_deepseek_rejects_malformed_usage(usage: SimpleNamespace) -> None:
    response = SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content="answer")
            )
        ],
        usage=usage,
    )
    completions = RecordingCompletions(response)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="malformed usage"):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("malformed_response", "sensitive_text"),
    [
        (SimpleNamespace(), "answer"),
        (SimpleNamespace(choices=[]), "answer"),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="first"),
                    ),
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="second"),
                    ),
                ]
            ),
            "second",
        ),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop")]), "answer"),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=None),
                    )
                ]
            ),
            "answer",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="   "),
                    )
                ]
            ),
            "   ",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=42),
                    )
                ]
            ),
            "42",
        ),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
            ),
            "answer",
        ),
    ],
)
async def test_deepseek_rejects_malformed_choice_shapes(
    malformed_response: object, sensitive_text: str
) -> None:
    completions = RecordingCompletions(malformed_response)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert sensitive_text not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason", ["content_filter", "insufficient_system_resource"]
)
async def test_deepseek_terminal_finish_reasons_fail_closed(
    finish_reason: str,
) -> None:
    completions = RecordingCompletions(
        chat_response(text="partial output", finish_reason=finish_reason)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert "partial output" not in str(caught.value)


def test_output_limit_telemetry_model_is_typed_and_bounded() -> None:
    telemetry_type = getattr(contracts_module, "ProviderResponseTelemetry", None)
    assert telemetry_type is not None
    telemetry = telemetry_type(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
        structured_attempt=2,
    )

    assert telemetry.model_dump(mode="json") == {
        "finish_reason_category": "length",
        "configured_max_tokens": 4096,
        "usage": {
            "input_tokens": 8,
            "output_tokens": 4096,
            "total_tokens": 4104,
        },
        "request_attempt": 1,
        "structured_attempt": 2,
    }

    with pytest.raises(ValueError):
        telemetry_type(
            finish_reason_category="length",
            configured_max_tokens=0,
            usage=TokenUsage(),
            request_attempt=1,
        )
    with pytest.raises(ValueError):
        telemetry_type(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(),
            request_attempt=0,
        )
    with pytest.raises(ValueError):
        telemetry_type(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(),
            request_attempt=1,
            raw_finish_reason="length",
        )


def test_provider_response_telemetry_rejects_top_level_mutation() -> None:
    telemetry = contracts_module.ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
    )

    with pytest.raises(ValidationError):
        telemetry.finish_reason_category = "other"


def test_provider_response_telemetry_rejects_nested_usage_replacement() -> None:
    telemetry = contracts_module.ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
    )

    with pytest.raises(ValidationError):
        telemetry.usage = TokenUsage(input_tokens=1, output_tokens=1)


def test_provider_response_telemetry_rejects_nested_mutation_and_stays_bounded(
) -> None:
    telemetry = contracts_module.ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
    )
    serialized_before = telemetry.model_dump(mode="json")

    with pytest.raises(ValidationError):
        telemetry.usage.output_tokens = 1

    assert telemetry.model_dump(mode="json") == serialized_before
    assert json.loads(telemetry.model_dump_json()) == {
        "finish_reason_category": "length",
        "configured_max_tokens": 4096,
        "usage": {
            "input_tokens": 8,
            "output_tokens": 4096,
            "total_tokens": 4104,
        },
        "request_attempt": 1,
        "structured_attempt": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "expected_category", "expects_output_limit"),
    [
        ("stop", "stop", False),
        (" LENGTH ", "length", True),
        ("CONTENT_FILTER", "content_filter", False),
        ("tool_calls", "tool_calls", False),
        ("insufficient_system_resource", "insufficient_system_resource", False),
        ("unknown-provider-finish-raw-value", "other", False),
        (None, "other", False),
        (42, "other", False),
        ("", "other", False),
        ("   ", "other", False),
        ("\x00length", "other", False),
        ("oversized-provider-finish-raw-" + ("x" * 64), "other", False),
    ],
)
async def test_deepseek_output_limit_finish_reason_telemetry_is_finite_and_safe(
    finish_reason: object,
    expected_category: str,
    expects_output_limit: bool,
) -> None:
    completions = RecordingCompletions(
        chat_response(
            text="partial provider response content",
            finish_reason=finish_reason,
            prompt_tokens=8,
            completion_tokens=4096,
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=5),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "prompt content"):
        if expects_output_limit:
            with pytest.raises(ProviderResponseError) as caught:
                await provider.complete(
                    [ChatMessage(role="user", content="prompt content")]
                )
            assert caught.value.retryable is False
            assert type(caught.value).__name__ == "ProviderOutputLimitError"
            assert getattr(caught.value, "telemetry").model_dump(mode="json") == {
                "finish_reason_category": "length",
                "configured_max_tokens": 4096,
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 4096,
                    "total_tokens": 4104,
                },
                "request_attempt": 1,
                "structured_attempt": None,
            }
            assert "partial provider response content" not in str(caught.value)
            assert "prompt content" not in str(caught.value)
        elif expected_category == "stop":
            result = await provider.complete(
                [ChatMessage(role="user", content="prompt content")]
            )
            assert result.text == "partial provider response content"
        else:
            with pytest.raises(ProviderResponseError) as caught:
                await provider.complete(
                    [ChatMessage(role="user", content="prompt content")]
                )
            assert type(caught.value).__name__ != "ProviderOutputLimitError"
            assert "partial provider response content" not in str(caught.value)
            assert "prompt content" not in str(caught.value)

    assert len(completions.calls) == 1
    assert tracker.llm_outputs == [
        {
            "finish_reason_category": expected_category,
            "configured_max_tokens": 4096,
            "usage": {
                "input_tokens": 8,
                "output_tokens": 4096,
                "total_tokens": 4104,
            },
            "request_attempt": 1,
            "structured_attempt": None,
        }
    ]
    serialized = json.dumps(tracker.llm_outputs, sort_keys=True)
    assert "unknown-provider-finish-raw-value" not in serialized
    assert "oversized-provider-finish-raw-" not in serialized


@pytest.mark.asyncio
async def test_deepseek_structured_length_error_carries_structured_attempt() -> None:
    completions = RecordingCompletions(
        chat_response(
            text="partial structured provider response",
            finish_reason="length",
            prompt_tokens=8,
            completion_tokens=4096,
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=5),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "structured prompt"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="structured prompt")], TinyAnswer
            )

    assert len(completions.calls) == 1
    assert type(caught.value).__name__ == "ProviderOutputLimitError"
    assert getattr(caught.value, "telemetry").model_dump(mode="json") == {
        "finish_reason_category": "length",
        "configured_max_tokens": 4096,
        "usage": {
            "input_tokens": 8,
            "output_tokens": 4096,
            "total_tokens": 4104,
        },
        "request_attempt": 1,
        "structured_attempt": 1,
    }
    assert tracker.llm_outputs[0] == getattr(caught.value, "telemetry").model_dump(
        mode="json"
    )
    assert "partial structured provider response" not in str(caught.value)
    assert "structured prompt" not in str(caught.value)


@pytest.mark.asyncio
async def test_deepseek_structured_error_retains_safe_diagnostics() -> None:
    completions = RecordingCompletions(
        chat_response(
            text='{"thought":"provider-secret-one","action":"finish",'
            '"tool_input_json":"{}"}'
        ),
        chat_response(
            text='{"thought":"provider-secret-two","action":"finish",'
            '"tool_input_json":"{}"}'
        ),
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "structured prompt"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="structured prompt")],
                ReActDecision,
            )

    diagnostics = getattr(caught.value, "diagnostics", None)
    assert diagnostics is not None
    assert [item.model_dump(mode="json") for item in diagnostics] == [
        {
            "attempt": 1,
            "field_paths": ["$"],
            "category": "other_schema",
        },
        {
            "attempt": 2,
            "field_paths": ["$"],
            "category": "other_schema",
        },
    ]
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in diagnostics], sort_keys=True
    )
    assert "provider-secret-one" not in serialized
    assert "provider-secret-two" not in serialized
    assert "structured prompt" not in serialized
    assert "input_value" not in serialized
    assert "provider-secret-one" not in str(caught.value)
    assert "provider-secret-two" not in str(caught.value)


@pytest.mark.asyncio
async def test_custom_validator_data_never_reaches_repair_or_exception_graph() -> None:
    marker = "REJECTED_PROVIDER_MARKER_7E5C"

    class RejectingAnswer(BaseModel):
        answer: str

        @field_validator("answer")
        @classmethod
        def reject_answer(cls, value: str) -> str:
            raise ValueError(f"validator rejected {value}")

    completions = RecordingCompletions(
        chat_response(text=json.dumps({"answer": marker})),
        chat_response(text=json.dumps({"answer": marker})),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "safe prompt"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="safe prompt")],
                RejectingAnswer,
            )

    repair_request = json.dumps(completions.calls[1], default=repr, sort_keys=True)
    assert marker not in repair_request

    reachable: list[BaseException] = []
    pending: list[BaseException] = [caught.value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        reachable.append(current)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)

    assert [type(item).__name__ for item in reachable] == [
        "StructuredOutputError"
    ]
    for item in reachable:
        attributes = repr({"args": item.args, "private": vars(item)})
        assert marker not in attributes


@pytest.mark.asyncio
async def test_deepseek_structured_failure_drops_provider_and_request_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_marker = "DEEPSEEK_RESPONSE_FRAME_MARKER_4F9A"
    prompt_marker = "DEEPSEEK_PROMPT_FRAME_MARKER_8B2D"
    request_marker = "DEEPSEEK_REQUEST_FRAME_MARKER_C671"
    invalid_response = json.dumps(
        {"answer": response_marker, "confidence": "not-an-integer"}
    )
    completions = RecordingCompletions(
        chat_response(text=invalid_response),
        chat_response(text=invalid_response),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )
    original_options = provider._request_options

    def marked_options(agent_name):
        effective, request, metadata = original_options(agent_name)
        return effective, {**request, "request_marker": request_marker}, metadata

    monkeypatch.setattr(provider, "_request_options", marked_options)

    with pytest.raises(StructuredOutputError) as caught:
        async with tracker.session_span("session-1", prompt_marker):
            await provider.complete_structured(
                [ChatMessage(role="user", content=prompt_marker)], TinyAnswer
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    surfaces = _provider_exception_surfaces(caught.value)
    assert surfaces
    assert all(
        marker not in surface
        for surface in surfaces
        for marker in (response_marker, prompt_marker, request_marker)
    )


@pytest.mark.asyncio
async def test_mapping_key_never_reaches_structured_failure_surfaces() -> None:
    marker = "REJECTED_PROVIDER_MARKER_7E5C"

    class MappingEntry(BaseModel):
        score: int

    class MappingEnvelope(BaseModel):
        answers: dict[str, MappingEntry]

    rejected = json.dumps({"answers": {marker: {"score": "invalid"}}})
    completions = RecordingCompletions(
        chat_response(text=rejected),
        chat_response(text=rejected),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "safe prompt"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="safe prompt")],
                MappingEnvelope,
            )

    reachable: list[BaseException] = []
    pending: list[BaseException] = [caught.value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        reachable.append(current)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)

    from deep_research.evaluation.failure_taxonomy import safe_failure_details

    details = safe_failure_details(caught.value)
    assert details is not None
    surfaces = {
        "repair_request": json.dumps(
            completions.calls[1], default=repr, sort_keys=True
        ),
        "provider_diagnostics": json.dumps(
            [
                item.model_dump(mode="json")
                for item in caught.value.diagnostics
            ],
            sort_keys=True,
        ),
        "exception_strings": repr([str(item) for item in reachable]),
        "exception_attributes": repr(
            [{"args": item.args, "private": vars(item)} for item in reachable]
        ),
        "evaluation_projection": details.model_dump_json(),
    }

    leaking_surfaces = [
        name for name, value in surfaces.items() if marker in value
    ]
    assert leaking_surfaces == []
    assert [item.field_paths for item in caught.value.diagnostics] == [
        ("answers.score",),
        ("answers.score",),
    ]
    assert [item.field_paths for item in details.diagnostics] == [
        ("answers.score",),
        ("answers.score",),
    ]


@pytest.mark.asyncio
async def test_structured_validation_truncates_paths_before_repair() -> None:
    many_fields = create_model(
        "ManyRequiredFields",
        **{f"field_{index:02d}": (str, ...) for index in range(18)},
    )
    completions = RecordingCompletions(
        chat_response(text="{}"),
        chat_response(text="{}"),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="question")],
                many_fields,
            )

    assert len(completions.calls) == 2
    assert len(caught.value.diagnostics) == 2
    expected_paths = tuple(f"field_{index:02d}" for index in range(16))
    assert [item.field_paths for item in caught.value.diagnostics] == [
        expected_paths,
        expected_paths,
    ]


def test_structured_validation_diagnostic_normalizes_and_bounds_paths() -> None:
    diagnostic_type = getattr(
        contracts_module, "StructuredValidationDiagnostic", None
    )
    assert diagnostic_type is not None
    diagnostic = diagnostic_type(
        attempt=2,
        field_paths=(" sub_topics . 0 . title ",),
        category="schema_output",
    )

    assert diagnostic.field_paths == ("sub_topics.0.title",)
    with pytest.raises(ValueError):
        diagnostic_type(
            attempt=0,
            field_paths=("title",),
            category="schema_output",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schema", "invalid_json", "expected_category", "expected_paths"),
    [
        (TinyAnswer, "not-json", "json_invalid", ("$",)),
        (
            TinyAnswer,
            '{"answer":"ok"}',
            "missing",
            ("confidence",),
        ),
        (
            StrictDiagnosticEnvelope,
            '{"nested":{"count":"bad","label":"ok"},"required":"ok"}',
            "type_mismatch",
            ("nested.count",),
        ),
        (
            RootDiagnosticPayload,
            '{"not":"a list"}',
            "type_mismatch",
            ("$",),
        ),
        (
            StrictDiagnosticEnvelope,
            '{"nested":{"count":2,"label":"ok"},"required":"ok",'
            '"unexpected":"provider-value"}',
            "extra_forbidden",
            ("$",),
        ),
        (
            StrictDiagnosticEnvelope,
            '{"nested":{"count":9,"label":"ok"},"required":"ok"}',
            "numeric_bounds",
            ("nested.count",),
        ),
        (
            StrictDiagnosticEnvelope,
            '{"nested":{"count":2,"label":"x"},"required":"ok"}',
            "string_bounds",
            ("nested.label",),
        ),
        (
            OtherDiagnosticPayload,
            '{"answer":"ok"}',
            "other_schema",
            ("answer",),
        ),
    ],
)
async def test_structured_validation_diagnostic_classifies_local_pydantic_errors(
    schema: type[BaseModel],
    invalid_json: str,
    expected_category: str,
    expected_paths: tuple[str, ...],
) -> None:
    completions = RecordingCompletions(
        chat_response(text=invalid_json),
        chat_response(text=invalid_json),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "classify this"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="classify this")], schema
            )

    assert [item.category for item in caught.value.diagnostics] == [
        expected_category,
        expected_category,
    ]
    assert [item.field_paths for item in caught.value.diagnostics] == [
        expected_paths,
        expected_paths,
    ]


def test_structured_validation_category_is_finite_and_legacy_compatible() -> None:
    diagnostic_type = contracts_module.StructuredValidationDiagnostic
    for category in (
        "schema_output",
        "json_invalid",
        "missing",
        "extra_forbidden",
        "type_mismatch",
        "numeric_bounds",
        "string_bounds",
        "other_schema",
    ):
        diagnostic = diagnostic_type(
            attempt=1, field_paths=("$",), category=category
        )
        assert diagnostic.category == category

    with pytest.raises(ValidationError):
        diagnostic_type(attempt=1, field_paths=("$",), category="raw_value")


def test_structured_output_error_retains_only_two_diagnostics() -> None:
    diagnostic_type = contracts_module.StructuredValidationDiagnostic
    diagnostics = tuple(
        diagnostic_type(
            attempt=index,
            field_paths=(f"field_{index}",),
            category="schema_output",
        )
        for index in range(1, 18)
    )

    error = StructuredOutputError("safe schema failure", diagnostics=diagnostics)

    assert error.diagnostics == diagnostics[:2]


def test_provider_output_limit_error_keeps_typed_telemetry() -> None:
    telemetry = ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
    )

    error = ProviderOutputLimitError(telemetry)

    assert error.retryable is False
    assert error.failure_category == "output_limit"
    assert error.telemetry is telemetry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderTimeoutError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            APIConnectionError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderResponseError,
        ),
        (
            APIStatusError(
                "bad",
                response=httpx.Response(
                    401, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderResponseError,
        ),
    ],
)
async def test_deepseek_plain_translates_sdk_errors(raised, expected) -> None:
    completions = RecordingCompletions(raised)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        # retry_count=0 isolates the translation contract from the retry
        # policy, which has its own dedicated tests.
        deepseek_config(retry_count=0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )
    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
async def test_deepseek_plain_translates_generic_openai_errors() -> None:
    completions = RecordingCompletions(OpenAIError("invalid"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="request failed"):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
async def test_deepseek_plain_observability_is_safe_and_capability_driven() -> None:
    completions = RecordingCompletions(
        chat_response(
            text="provider answer",
            prompt_tokens=8,
            completion_tokens=3,
            reasoning_content="hidden chain of thought",
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [
                ChatMessage(role="developer", content="policy"),
                ChatMessage(role="user", content="question"),
            ],
            agent_name="planner",
        )

    assert result.text == "provider answer"
    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.model == "deepseek-v4-flash"
    assert metric.input_tokens == 8
    assert metric.output_tokens == 3
    assert metric.total_tokens == 11
    assert metric.success is True

    assert tracker.llm_inputs == [
        {
            "provider": "deepseek",
            "thinking_mode": "enabled",
            "requested_reasoning_effort": "high",
            "effective_reasoning_effort": "high",
            "agent_name": "planner",
            "operation": "chat",
            "message_count": 2,
        }
    ]
    serialized = json.dumps(
        {
            "llm_inputs": tracker.llm_inputs,
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [
                metric.model_dump(mode="json") for metric in tracker.metrics
            ],
        },
        sort_keys=True,
    )
    for sensitive in (
        "deepseek-secret",
        "policy",
        "question",
        "provider answer",
        "hidden chain of thought",
    ):
        assert sensitive not in serialized


@pytest.mark.asyncio
async def test_deepseek_plain_failure_records_typed_metric_without_leak() -> None:
    completions = RecordingCompletions(
        SimpleNamespace(
            id="deepseek-response",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="   ", reasoning_content="hidden chain of thought"
                    ),
                )
            ],
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.success is False
    assert metric.error_type == "ProviderResponseError"
    assert metric.model == "deepseek-v4-flash"
    serialized = json.dumps(
        {
            "llm_inputs": tracker.llm_inputs,
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [
                metric.model_dump(mode="json") for metric in tracker.metrics
            ],
        },
        sort_keys=True,
    )
    assert "hidden chain of thought" not in serialized
    assert "question" not in serialized
    assert str(caught.value) == "DeepSeek response contained malformed content"


@pytest.mark.asyncio
async def test_deepseek_structured_output_prompts_json_and_validates_locally() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result == TinyAnswer(answer="yes", confidence=9)
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][-1]["role"] == "system"
    instruction = call["messages"][-1]["content"]
    assert "JSON" in instruction
    assert json.dumps(
        TinyAnswer.model_json_schema(), sort_keys=True, separators=(",", ":")
    ) in instruction


@pytest.mark.asyncio
async def test_deepseek_structured_defaults_max_tokens_to_the_global_cap() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert completions.calls[0]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_deepseek_structured_applies_the_per_call_max_tokens_override() -> (
    None
):
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="decide")],
            TinyAnswer,
            max_tokens=8192,
        )

    assert completions.calls[0]["max_tokens"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_max_tokens", [0, -1])
async def test_deepseek_structured_rejects_non_positive_per_call_max_tokens(
    invalid_max_tokens: int,
) -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    with pytest.raises(ValueError, match="max_tokens"):
        async with tracker.session_span("session-1", "question"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")],
                TinyAnswer,
                max_tokens=invalid_max_tokens,
            )

    assert completions.calls == []


@pytest.mark.asyncio
async def test_deepseek_structured_length_telemetry_records_max_tokens_cap() -> (
    None
):
    completions = RecordingCompletions(
        chat_response(
            text="partial structured provider response",
            finish_reason="length",
            prompt_tokens=8,
            completion_tokens=8192,
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=5),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "structured prompt"):
        with pytest.raises(ProviderOutputLimitError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="structured prompt")],
                TinyAnswer,
                max_tokens=8192,
            )

    assert completions.calls[0]["max_tokens"] == 8192
    assert getattr(caught.value, "telemetry").configured_max_tokens == 8192
    assert tracker.llm_outputs[0]["configured_max_tokens"] == 8192
    assert "partial structured provider response" not in str(caught.value)
    assert "structured prompt" not in str(caught.value)


@pytest.mark.asyncio
async def test_deepseek_structured_rejects_empty_messages_before_sdk_call() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    with pytest.raises(ValueError, match="messages must contain at least one item"):
        await provider.complete_structured([], TinyAnswer)

    assert completions.calls == []


@pytest.mark.asyncio
async def test_deepseek_structured_repairs_once_then_succeeds() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":3}'),
        chat_response(text='{"answer":"yes","confidence":8}'),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result.confidence == 8
    assert len(completions.calls) == 2
    assert (
        "previous JSON response failed TinyAnswer validation"
        in completions.calls[1]["messages"][-1]["content"]
    )


class _LastModelSpyingCompletions(RecordingCompletions):
    """A ``RecordingCompletions`` that snapshots ``provider.last_model_returned``

    right before the repair (second) request is sent. This exposes whether a
    discarded, schema-invalid first attempt was ever recorded as "the last
    successful response" -- even transiently -- since the final value alone
    cannot tell: when the repair attempt goes on to succeed, its own
    assignment overwrites whatever the first attempt left behind either way.
    """

    def __init__(self, *outcomes: object) -> None:
        super().__init__(*outcomes)
        self.provider: DeepSeekChatProvider | None = None
        self.model_seen_before_repair: object = "not-observed"

    async def create(self, **kwargs):
        if len(self.calls) == 1:
            assert self.provider is not None
            self.model_seen_before_repair = self.provider.last_model_returned
        return await super().create(**kwargs)


@pytest.mark.asyncio
async def test_deepseek_structured_last_model_returned_ignores_failed_attempt() -> (
    None
):
    completions = _LastModelSpyingCompletions(
        SimpleNamespace(
            id="deepseek-response-1",
            model="deepseek-first-attempt-model",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"answer":3}'),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        ),
        SimpleNamespace(
            id="deepseek-response-2",
            model="deepseek-repair-attempt-model",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"answer":"yes","confidence":8}'
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        ),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )
    completions.provider = provider

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result.confidence == 8
    assert len(completions.calls) == 2
    # The discarded, schema-invalid first attempt must never be observable
    # as "the last successful response" -- not even before the repair runs.
    assert completions.model_seen_before_repair is None
    # And once the repair succeeds, it is what gets recorded.
    assert provider.last_model_returned == "deepseek-repair-attempt-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["", "not-json", '{"answer":3}'])
async def test_deepseek_structured_raises_after_exactly_one_failed_repair(
    first: str,
) -> None:
    completions = RecordingCompletions(
        chat_response(text=first), chat_response(text="still invalid")
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError, match="TinyAnswer") as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(completions.calls) == 2
    assert str(caught.value) == (
        "DeepSeek output failed TinyAnswer validation after one repair attempt"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderTimeoutError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            APIConnectionError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderResponseError,
        ),
        (
            APIStatusError(
                "bad",
                response=httpx.Response(
                    401, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderResponseError,
        ),
    ],
)
async def test_deepseek_structured_sdk_errors_are_not_repaired(
    raised, expected
) -> None:
    completions = RecordingCompletions(raised)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected):
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_deepseek_structured_generic_openai_errors_are_not_repaired() -> None:
    completions = RecordingCompletions(OpenAIError("invalid"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(
            ProviderResponseError, match="structured output request failed"
        ):
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(completions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason", ["length", "content_filter", "insufficient_system_resource"]
)
async def test_deepseek_structured_terminal_finish_reasons_are_not_repaired(
    finish_reason: str,
) -> None:
    completions = RecordingCompletions(
        chat_response(text="partial output", finish_reason=finish_reason)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(completions.calls) == 1
    assert "partial output" not in str(caught.value)


@pytest.mark.asyncio
async def test_deepseek_structured_telemetry_is_safe_and_attempted() -> None:
    completions = RecordingCompletions(
        chat_response(
            text='{"answer":3}',
            reasoning_content="hidden chain of thought",
        ),
        chat_response(
            text='{"answer":"yes","confidence":8}',
            reasoning_content="hidden chain of thought",
        ),
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")],
            TinyAnswer,
            agent_name="planner",
        )

    assert result == TinyAnswer(answer="yes", confidence=8)
    metrics = [m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]
    assert [metric.success for metric in metrics] == [False, True]
    assert tracker.llm_inputs == [
        {
            "provider": "deepseek",
            "thinking_mode": "enabled",
            "requested_reasoning_effort": "high",
            "effective_reasoning_effort": "high",
            "agent_name": "planner",
            "operation": "structured_output",
            "attempt": 1,
            "message_count": 2,
        },
        {
            "provider": "deepseek",
            "thinking_mode": "enabled",
            "requested_reasoning_effort": "high",
            "effective_reasoning_effort": "high",
            "agent_name": "planner",
            "operation": "structured_output",
            "attempt": 2,
            "message_count": 3,
        },
    ]

    exhausted = RecordingCompletions(
        chat_response(text="not-json"),
        chat_response(text="still invalid"),
    )
    failed_tracker = local_tracker()
    failed_provider = DeepSeekChatProvider(
        deepseek_config(), failed_tracker, client=FakeDeepSeekClient(exhausted)
    )
    async with failed_tracker.session_span("session-2", "question"):
        with pytest.raises(StructuredOutputError) as caught:
            await failed_provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    serialized = json.dumps(
        {
            "llm_inputs": tracker.llm_inputs,
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [
                metric.model_dump(mode="json") for metric in tracker.metrics
            ],
            "public_error": str(caught.value),
        },
        sort_keys=True,
    )
    for sensitive in (
        "deepseek-secret",
        "decide",
        '{"answer":3}',
        '{"answer":"yes","confidence":8}',
        "hidden chain of thought",
        "previous JSON response failed",
    ):
        assert sensitive not in serialized


def test_deepseek_validation_summary_uses_only_bounded_diagnostic_fields() -> None:
    diagnostic = contracts_module.StructuredValidationDiagnostic(
        attempt=1,
        field_paths=tuple(
            f"field_{index}_{'x' * 100}" for index in range(16)
        ),
        category="schema_output",
    )

    summary = deepseek_module._validation_summary(diagnostic)

    assert summary.startswith("category=schema_output; field_paths=field_0_")
    assert "attempt" not in summary
    assert len(summary) <= 1000


@pytest.mark.asyncio
async def test_last_model_returned_is_none_before_any_call() -> None:
    tracker = local_tracker()
    provider = DeepSeekChatProvider(deepseek_config(), tracker, client=object())

    assert provider.last_model_returned is None


@pytest.mark.asyncio
async def test_last_model_returned_records_the_dated_snapshot() -> None:
    """The alias is requested; whatever the API answers with is recorded."""
    completions = RecordingCompletions(
        SimpleNamespace(
            id="resp-1",
            model="DeepSeek-V4-Flash-0731",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="answer text"),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        )
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete([ChatMessage(role="user", content="hello")])

    assert result.model == "deepseek-v4-flash"
    assert provider.last_model_returned == "DeepSeek-V4-Flash-0731"


@pytest.mark.asyncio
async def test_deepseek_structured_public_cause_chain_hides_provider_output() -> None:
    completions = RecordingCompletions(
        chat_response(text="not-json"),
        chat_response(text="still invalid"),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert str(caught.value) == (
        "DeepSeek output failed TinyAnswer validation after one repair attempt"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    for sensitive in ("not-json", "still invalid", "decide"):
        assert sensitive not in str(caught.value)


def _recorded_sleeps(monkeypatch) -> list[float]:
    """Replace ``asyncio.sleep`` with a recorder for deterministic tests."""
    import asyncio

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_deepseek_plain_retries_transient_errors_then_succeeds(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    completions = RecordingCompletions(
        APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
        RateLimitError(
            "limited",
            response=httpx.Response(
                429, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
            ),
            body=None,
        ),
        chat_response(text="answer"),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=3, retry_initial_delay=1.0, retry_max_delay=4.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [ChatMessage(role="user", content="question")]
        )

    assert result.text == "answer"
    assert len(completions.calls) == 3
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_deepseek_plain_raises_after_retries_exhausted(monkeypatch) -> None:
    slept = _recorded_sleeps(monkeypatch)
    error = APIConnectionError(request=httpx.Request("POST", DEEPSEEK_BASE_URL))
    completions = RecordingCompletions(error, error, error)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=2, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError):
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 3
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_deepseek_plain_non_transient_errors_are_not_retried(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    completions = RecordingCompletions(ValueError("boom"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=5, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ValueError, match="boom"):
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert slept == []


@pytest.mark.asyncio
async def test_deepseek_structured_retries_transient_errors_then_succeeds(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    completions = RecordingCompletions(
        APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
        chat_response(text='{"answer": "yes", "confidence": 9}'),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=2, retry_initial_delay=1.0, retry_max_delay=4.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result == TinyAnswer(answer="yes", confidence=9)
    assert len(completions.calls) == 2
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_deepseek_plain_does_not_retry_deterministic_status_errors(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    completions = RecordingCompletions(
        APIStatusError(
            "bad",
            response=httpx.Response(
                401, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
            ),
            body=None,
        )
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=5, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError):
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert slept == []


@pytest.mark.asyncio
async def test_deepseek_plain_retries_server_status_errors(monkeypatch) -> None:
    slept = _recorded_sleeps(monkeypatch)
    error = APIStatusError(
        "bad",
        response=httpx.Response(
            503, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
        ),
        body=None,
    )
    completions = RecordingCompletions(error, error, chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(retry_count=3, retry_initial_delay=1.0, retry_max_delay=4.0),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete([ChatMessage(role="user", content="question")])

    assert result.text == "answer"
    assert len(completions.calls) == 3
    assert slept == [1.0, 2.0]
