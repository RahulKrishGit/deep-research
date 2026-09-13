"""Unit tests for the project-owned OpenAI provider boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

import deep_research.providers.openai_provider as openai_provider_module
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers import (
    NativeToolCall,
    NativeToolTurn,
    ToolDefinition,
)
from deep_research.providers.openai_provider import (
    ChatMessage,
    OpenAIChatProvider,
    ProviderConfigurationError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.utils.config import LLMConfig


def response(
    *,
    text: str = "A concise answer.",
    parsed: object | None = None,
    model: str | None = None,
    input_tokens: int = 8,
    output_tokens: int = 3,
    status: str = "completed",
    output: object = None,
    incomplete_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp-1",
        status=status,
        output=output,
        incomplete_details=(
            None
            if incomplete_reason is None
            else SimpleNamespace(reason=incomplete_reason)
        ),
        output_text=text,
        output_parsed=parsed,
        model=model,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def native_function_call(name: str, arguments: str) -> SimpleNamespace:
    """One Responses function-call item, shaped as the SDK returns it."""
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
    )


def reasoning_item(marker: str) -> SimpleNamespace:
    """One opaque reasoning item, which must never be retained or traced."""
    return SimpleNamespace(type="reasoning", summary=[], encrypted_content=marker)


def output_message_item(marker: str) -> SimpleNamespace:
    """One assistant output-message item, whose body must never be read.

    Only the item *type* is a legitimate concern of the native boundary; the
    accepted final answer comes from ``response.output_text``.
    """
    return SimpleNamespace(
        type="message",
        role="assistant",
        id="msg-1",
        content=[SimpleNamespace(type="output_text", text=marker)],
    )


class RecordingResponses:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.create_calls: list[dict[str, Any]] = []
        self.parse_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.create_calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def parse(self, **kwargs: Any) -> object:
        self.parse_calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeOpenAIClient:
    def __init__(
        self,
        *,
        responses: RecordingResponses | None = None,
        embeddings: object | None = None,
    ) -> None:
        self.responses = responses or RecordingResponses()
        self.embeddings = embeddings


def local_tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


class CapturingTracker(Tracker):
    def __init__(self) -> None:
        super().__init__(LangSmithRuntimeConfig(tracing_enabled=False))
        self.llm_inputs: list[dict[str, object]] = []

    def llm_span(self, model, inputs):
        self.llm_inputs.append(dict(inputs))
        return super().llm_span(model, inputs)


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


def _exception_reaches(error: BaseException, target: object) -> bool:
    """True when ``target`` is reachable from the error's public surface.

    Walks the exception graph and the *provider* frames on those exceptions'
    tracebacks -- never module globals, never the garbage collector, and never
    caller frames. Callers are skipped deliberately: this test necessarily
    holds the SDK object in its own local, and a caller's own reference is not
    a disclosure by the provider. A True result is therefore a path the
    provider itself opened.
    """
    pending: list[BaseException] = [error]
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
            frame = traceback.tb_frame
            filename = frame.f_code.co_filename.replace("\\", "/")
            if "/src/deep_research/providers/" in filename and any(
                value is target for value in frame.f_locals.values()
            ):
                return True
            traceback = traceback.tb_next
    return False


def openai_config(**updates: object) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "thinking_mode": "disabled",
            "reasoning_effort": "none",
            **updates,
        }
    )


@pytest.mark.asyncio
async def test_complete_parses_text_and_records_usage() -> None:
    responses = RecordingResponses(response())
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(model_overrides={"planner": "gpt-4o-mini"}),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [ChatMessage(role="user", content="Summarize this.")],
            agent_name="planner",
        )

    assert result.text == "A concise answer."
    assert result.model == "gpt-4o-mini"
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 3
    assert responses.create_calls == [
        {
            "model": "gpt-4o-mini",
            "input": [{"role": "user", "content": "Summarize this."}],
            "temperature": 0.7,
            "max_output_tokens": 32768,
        }
    ]
    token_metric = next(
        metric for metric in tracker.metrics if isinstance(metric, TokenUsageMetric)
    )
    assert token_metric.model == "gpt-4o-mini"
    assert token_metric.total_tokens == 11
    assert tracker.events[-1].metadata["success"] is True


@pytest.mark.asyncio
async def test_openai_reasoning_model_sends_resolved_effort_without_temperature() -> (
    None
):
    responses = RecordingResponses(response(text="Answer"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.6",
            thinking_mode="enabled",
            reasoning_effort="high",
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete(
            [ChatMessage(role="user", content="Answer")],
            agent_name="planner",
        )

    call = responses.create_calls[0]
    assert call["reasoning"] == {"effort": "high"}
    assert "temperature" not in call


@pytest.mark.asyncio
async def test_openai_structured_call_uses_structured_agent_override() -> None:
    parsed = Outline(title="Answer", points=[])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.6",
            thinking_mode="enabled",
            reasoning_effort="low",
            model_overrides={"critic": {"reasoning_effort": "max"}},
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="Review")],
            Outline,
            agent_name="critic",
        )

    assert responses.parse_calls[0]["model"] == "gpt-5.6"
    assert responses.parse_calls[0]["reasoning"] == {"effort": "max"}
    assert "temperature" not in responses.parse_calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "expected_reasoning"),
    [("gpt-5.6", {"effort": "none"}), ("gpt-4o", None)],
)
async def test_openai_disabled_mode_sends_only_supported_controls(
    model: str, expected_reasoning: dict[str, str] | None
) -> None:
    responses = RecordingResponses(response(text="Answer"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model=model,
            thinking_mode="disabled",
            reasoning_effort="high",
            temperature=0.25,
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="Answer")])

    call = responses.create_calls[0]
    assert call["temperature"] == 0.25
    if expected_reasoning is None:
        assert "reasoning" not in call
    else:
        assert call["reasoning"] == expected_reasoning


@pytest.mark.asyncio
async def test_openai_rejects_unsupported_effort_before_request() -> None:
    responses = RecordingResponses(response(text="must not be consumed"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.5",
            thinking_mode="enabled",
            reasoning_effort="max",
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderConfigurationError, match="gpt-5.5"):
            await provider.complete([ChatMessage(role="user", content="Answer")])

    assert responses.create_calls == []


def test_missing_api_key_fails_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        OpenAIChatProvider(openai_config(), local_tracker())


def test_explicit_empty_api_key_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        OpenAIChatProvider(openai_config(), local_tracker(), api_key="")


class Outline(BaseModel):
    title: str
    points: list[str]


@pytest.mark.asyncio
async def test_openai_structured_defaults_max_output_tokens_to_the_global_cap() -> (
    None
):
    parsed = Outline(title="Answer", points=["One", "Two"])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert responses.parse_calls[0]["max_output_tokens"] == 32768


@pytest.mark.asyncio
async def test_openai_structured_applies_the_per_call_max_tokens_override() -> None:
    parsed = Outline(title="Answer", points=["One", "Two"])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")],
            Outline,
            max_tokens=8192,
        )

    assert responses.parse_calls[0]["max_output_tokens"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_max_tokens", [0, -1])
async def test_openai_structured_rejects_non_positive_per_call_max_tokens(
    invalid_max_tokens: int,
) -> None:
    responses = RecordingResponses(response(text="must not be consumed"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    with pytest.raises(ValueError, match="max_tokens"):
        async with tracker.session_span("session-1", "question"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")],
                Outline,
                max_tokens=invalid_max_tokens,
            )

    assert responses.parse_calls == []


@pytest.mark.asyncio
async def test_complete_structured_returns_parsed_model() -> None:
    parsed = Outline(title="Answer", points=["One", "Two"])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == parsed
    assert responses.parse_calls[0]["text_format"] is Outline
    assert responses.parse_calls[0]["model"] == "gpt-4o"
    assert len([m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]) == 1


@pytest.mark.asyncio
async def test_complete_structured_repairs_once_then_succeeds() -> None:
    repaired = Outline(title="Repaired", points=["Valid"])
    responses = RecordingResponses(
        response(text='{"title": 3}', parsed=None, input_tokens=5, output_tokens=2),
        response(parsed=repaired, input_tokens=7, output_tokens=3),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == repaired
    assert len(responses.parse_calls) == 2
    repair_input = responses.parse_calls[1]["input"]
    assert repair_input[-1]["role"] == "developer"
    assert "failed Outline validation" in repair_input[-1]["content"]
    token_metrics = [m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]
    assert [metric.total_tokens for metric in token_metrics] == [7, 10]
    assert [metric.success for metric in token_metrics] == [False, True]


@pytest.mark.asyncio
async def test_openai_span_metadata_is_safe_and_capability_driven() -> None:
    responses = RecordingResponses(response(text="Answer"))
    tracker = CapturingTracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.6",
            thinking_mode="enabled",
            reasoning_effort="high",
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="Answer")])

    serialized = json.dumps(tracker.llm_inputs[0], sort_keys=True)
    assert '"provider": "openai"' in serialized
    assert '"thinking_mode": "enabled"' in serialized
    assert '"requested_reasoning_effort": "high"' in serialized
    assert "Answer" not in serialized


@pytest.mark.asyncio
async def test_openai_structured_repair_spans_record_attempt_and_schema() -> None:
    repaired = Outline(title="Repaired", points=["Valid"])
    responses = RecordingResponses(
        response(text='{"title": 3}', parsed=None),
        response(parsed=repaired),
    )
    tracker = CapturingTracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == repaired
    assert [span["attempt"] for span in tracker.llm_inputs] == [1, 2]
    assert responses.parse_calls[0]["text_format"] is Outline
    assert responses.parse_calls[1]["text_format"] is Outline


@pytest.mark.asyncio
async def test_complete_structured_raises_after_one_failed_repair() -> None:
    responses = RecordingResponses(
        response(text="invalid", parsed=None),
        response(text="still invalid", parsed=None),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError, match="Outline"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )

    assert len(responses.parse_calls) == 2
    token_metrics = [m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]
    assert [metric.success for metric in token_metrics] == [False, False]


@pytest.mark.asyncio
async def test_reasoning_effort_is_sent_on_ordinary_calls() -> None:
    responses = RecordingResponses(response(text="hello"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(model="gpt-5.6-luna", reasoning_effort="medium"),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("s1", "q"):
        await provider.complete([ChatMessage(role="user", content="hi")])

    assert responses.create_calls[0]["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_reasoning_effort_is_sent_on_structured_calls_and_the_repair() -> None:
    repaired = Outline(title="Answer", points=["One"])
    responses = RecordingResponses(
        response(text="invalid", parsed=None),
        response(parsed=repaired),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(model="gpt-5.6-luna", reasoning_effort="high"),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("s1", "q"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="hi")], Outline
        )

    assert len(responses.parse_calls) == 2
    assert all(
        call["reasoning"] == {"effort": "high"} for call in responses.parse_calls
    )


@pytest.mark.asyncio
async def test_the_provider_records_the_model_the_response_reported() -> None:
    responses = RecordingResponses(
        response(text="hello", model="gpt-5.6-luna-2026-08-01")
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(model="gpt-5.6-luna"),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )
    assert provider.last_model_returned is None

    async with tracker.session_span("s1", "q"):
        await provider.complete([ChatMessage(role="user", content="hi")])

    assert provider.last_model_returned == "gpt-5.6-luna-2026-08-01"


def test_reasoning_effort_rejects_an_unknown_level() -> None:
    with pytest.raises(ValueError):
        LLMConfig(reasoning_effort="turbo")


@pytest.mark.asyncio
async def test_empty_text_response_is_a_typed_provider_error() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(response(text="   "))),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="text output"):
            await provider.complete([ChatMessage(role="user", content="Answer")])

    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.success is False
    assert metric.error_type == "ProviderResponseError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_error", "expected_type"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
            ProviderTimeoutError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.openai.com")
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            APIStatusError(
                "failed",
                response=httpx.Response(
                    500, request=httpx.Request("POST", "https://api.openai.com")
                ),
                body=None,
            ),
            ProviderResponseError,
        ),
    ],
)
async def test_complete_translates_sdk_errors(
    sdk_error: Exception, expected_type: type[Exception]
) -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        # retry_count=0 isolates the translation contract from the retry
        # policy, which has its own dedicated tests.
        openai_config(retry_count=0),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(sdk_error)),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected_type):
            await provider.complete([ChatMessage(role="user", content="Answer")])


def outline_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        Outline.model_validate({"title": 3, "points": "invalid"})
    return exc_info.value


@pytest.mark.asyncio
async def test_complete_structured_repairs_pydantic_validation_error() -> None:
    repaired = Outline(title="Repaired", points=["Valid"])
    responses = RecordingResponses(
        outline_validation_error(), response(parsed=repaired)
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == repaired
    assert len(responses.parse_calls) == 2


@pytest.mark.asyncio
async def test_complete_structured_raises_after_two_pydantic_validation_errors() -> (
    None
):
    responses = RecordingResponses(
        outline_validation_error(), outline_validation_error()
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError, match="Outline"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )

    assert len(responses.parse_calls) == 2


@pytest.mark.asyncio
async def test_openai_structured_validation_never_retains_provider_content() -> None:
    marker = "OPENAI_PROVIDER_MARKER_7E5C"
    with pytest.raises(ValidationError) as exc_info:
        Outline.model_validate({"title": 3, "points": marker})
    validation_error = exc_info.value

    responses = RecordingResponses(validation_error, validation_error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "Create an outline"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )

    assert len(responses.parse_calls) == 2
    assert marker not in repr(responses.parse_calls[1])
    assert marker not in repr(caught.value)
    assert marker not in repr(vars(caught.value))
    assert caught.value.diagnostics[0].category == "type_mismatch"


@pytest.mark.asyncio
async def test_openai_structured_failure_drops_provider_and_request_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_marker = "OPENAI_RESPONSE_FRAME_MARKER_4F9A"
    prompt_marker = "OPENAI_PROMPT_FRAME_MARKER_8B2D"
    request_marker = "OPENAI_REQUEST_FRAME_MARKER_C671"
    repair_marker = "OPENAI_REPAIR_FRAME_MARKER_9D3E"
    with pytest.raises(ValidationError) as exc_info:
        Outline.model_validate({"title": 3, "points": response_marker})
    validation_error = exc_info.value

    responses = RecordingResponses(validation_error, validation_error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(), tracker, client=FakeOpenAIClient(responses=responses)
    )
    original_options = provider._request_options

    def marked_options(agent_name):
        effective, request, metadata = original_options(agent_name)
        return effective, {**request, "request_marker": request_marker}, metadata

    monkeypatch.setattr(provider, "_request_options", marked_options)
    monkeypatch.setattr(
        openai_provider_module,
        "validation_summary",
        lambda diagnostic: repair_marker,
    )

    with pytest.raises(StructuredOutputError) as caught:
        async with tracker.session_span("session-1", prompt_marker):
            await provider.complete_structured(
                [ChatMessage(role="user", content=prompt_marker)], Outline
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert repair_marker in repr(responses.parse_calls[1])
    surfaces = _provider_exception_surfaces(caught.value)
    assert surfaces
    assert all(
        marker not in surface
        for surface in surfaces
        for marker in (
            response_marker,
            prompt_marker,
            request_marker,
            repair_marker,
        )
    )


@pytest.mark.asyncio
async def test_complete_translates_connection_errors() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=0),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(
                APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com")
                )
            )
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.complete([ChatMessage(role="user", content="Answer")])


@pytest.mark.asyncio
async def test_complete_structured_translates_connection_errors() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=0),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(
                APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com")
                )
            )
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )


@pytest.mark.asyncio
async def test_complete_structured_translates_finish_reason_error() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=0),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(ContentFilterFinishReasonError())
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="structured output"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("output_text", [None, 42])
async def test_complete_rejects_non_string_output_text(output_text: object) -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(response(text=output_text))
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="text output"):
            await provider.complete([ChatMessage(role="user", content="Answer")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_response",
    [
        SimpleNamespace(
            output_text="Answer", usage=SimpleNamespace(input_tokens="bad")
        ),
        SimpleNamespace(
            output_text="Answer",
            output_parsed=Outline(title="Answer", points=[]),
            usage=SimpleNamespace(input_tokens=1, output_tokens="bad"),
        ),
    ],
)
async def test_provider_methods_reject_malformed_usage(
    malformed_response: object,
) -> None:
    tracker = local_tracker()
    chat = OpenAIChatProvider(
        openai_config(),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(malformed_response)),
    )

    async with tracker.session_span("session-1", "question"):
        if hasattr(malformed_response, "output_parsed"):
            with pytest.raises(ProviderResponseError, match="usage"):
                await chat.complete_structured(
                    [ChatMessage(role="user", content="Create an outline")], Outline
                )
        else:
            with pytest.raises(ProviderResponseError, match="usage"):
                await chat.complete([ChatMessage(role="user", content="Answer")])


@pytest.mark.asyncio
async def test_complete_translates_generic_openai_errors() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=0),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(OpenAIError("invalid"))),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="request failed"):
            await provider.complete([ChatMessage(role="user", content="Answer")])


def _recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``asyncio.sleep`` with a recorder for deterministic tests."""
    import asyncio

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_openai_complete_retries_transient_errors_then_succeeds(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    responses = RecordingResponses(
        APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
        response(text="Answer"),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=3, retry_initial_delay=1.0, retry_max_delay=4.0),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete([ChatMessage(role="user", content="Answer")])

    assert result.text == "Answer"
    assert len(responses.create_calls) == 2
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_openai_complete_structured_raises_after_retries_exhausted(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    error = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    responses = RecordingResponses(error, error, error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=2, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )

    assert len(responses.parse_calls) == 3
    assert slept == [1.0, 2.0]


# --- native ReAct tool turns (offline parity with DeepSeek) ------------------

WEB_SEARCH_DEFINITION = ToolDefinition(
    name="web_search",
    description="Search the web.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


def _native_provider(
    tracker: Tracker, responses: RecordingResponses
) -> OpenAIChatProvider:
    return OpenAIChatProvider(
        openai_config(),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )


@pytest.mark.asyncio
async def test_openai_native_react_asks_auto_and_parses_one_function_call() -> None:
    responses = RecordingResponses(
        response(
            text="",
            status="completed",
            output=[
                native_function_call("web_search", '{"query":"qec capacity"}')
            ],
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "find capacity evidence"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="find capacity evidence")],
            [WEB_SEARCH_DEFINITION],
            agent_name="critic",
        )

    call = responses.create_calls[0]
    assert call["tools"] == [
        {
            "type": "function",
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]
    assert call["tool_choice"] == "auto"
    assert call["input"] == [
        {"role": "user", "content": "find capacity evidence"}
    ]
    assert "text_format" not in call
    assert turn.tool_call == NativeToolCall(
        tool_name="web_search",
        arguments_json='{"query":"qec capacity"}',
    )
    assert turn.final_answer is None
    assert isinstance(turn, NativeToolTurn)


@pytest.mark.asyncio
async def test_openai_native_react_ignores_reasoning_items() -> None:
    reasoning_marker = "OPENAI_REASONING_MARKER_51C4"
    responses = RecordingResponses(
        response(
            text="",
            status="completed",
            output=[
                reasoning_item(reasoning_marker),
                native_function_call("web_search", '{"query":"qec"}'),
            ],
        )
    )
    tracker = CapturingTracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="review")], [WEB_SEARCH_DEFINITION]
        )

    assert turn.tool_call == NativeToolCall(
        tool_name="web_search", arguments_json='{"query":"qec"}'
    )
    assert reasoning_marker not in repr(tracker.llm_inputs)


@pytest.mark.asyncio
async def test_openai_native_react_returns_a_final_answer_without_a_tool() -> None:
    responses = RecordingResponses(
        response(text="  The report is complete.  ", status="completed", output=[])
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="review")], [WEB_SEARCH_DEFINITION]
        )

    assert turn.tool_call is None
    assert turn.final_answer == "The report is complete."


@pytest.mark.asyncio
async def test_openai_native_react_accepts_reasoning_beside_a_final_answer() -> None:
    """Reasoning items are stepped over by type and never read."""
    reasoning_marker = "OPENAI_REASONING_MARKER_5B77"
    responses = RecordingResponses(
        response(
            text="The report is complete.",
            status="completed",
            output=[reasoning_item(reasoning_marker)],
        )
    )
    tracker = CapturingTracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="review")], [WEB_SEARCH_DEFINITION]
        )

    assert turn.tool_call is None
    assert turn.final_answer == "The report is complete."
    assert reasoning_marker not in repr(tracker.llm_inputs)


@pytest.mark.asyncio
async def test_openai_native_react_accepts_a_coherent_final_message_item() -> None:
    """A ``message`` item is the ordinary final-answer envelope."""
    message_marker = "OPENAI_MESSAGE_MARKER_3C19"
    responses = RecordingResponses(
        response(
            text="The report is complete.",
            status="completed",
            output=[output_message_item(message_marker)],
        )
    )
    tracker = CapturingTracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="review")], [WEB_SEARCH_DEFINITION]
        )

    assert turn.tool_call is None
    assert turn.final_answer == "The report is complete."
    # The item body is never read, retained, or traced.
    assert message_marker not in repr(tracker.llm_inputs)


@pytest.mark.asyncio
async def test_openai_native_react_rejects_a_message_item_beside_a_call() -> None:
    """An answer item beside a typed call is an incoherent envelope."""
    message_marker = "OPENAI_MESSAGE_MARKER_8AD4"
    responses = RecordingResponses(
        response(
            text="",
            status="completed",
            output=[
                output_message_item(message_marker),
                native_function_call("web_search", '{"query":"qec"}'),
            ],
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    error = caught.value
    assert error.failure_category == "response"
    assert error.failure_origin == "local_response"
    assert len(responses.create_calls) == 1
    surfaces = _provider_exception_surfaces(error)
    assert surfaces
    assert all(
        leaked not in surface
        for surface in [str(error), *surfaces]
        for leaked in (message_marker, '{"query":"qec"}')
    )


OPENAI_UNKNOWN_ITEM_TYPES: tuple[str, ...] = (
    "web_search_call",
    "file_search_call",
    "computer_call",
    "code_interpreter_call",
    "function_call_output",
    "image_generation_call",
)


@pytest.mark.parametrize("item_type", OPENAI_UNKNOWN_ITEM_TYPES)
@pytest.mark.asyncio
async def test_openai_native_react_rejects_an_unknown_item_beside_a_call(
    item_type: str,
) -> None:
    """An unrecognised output item is rejected, never filtered away.

    Silently dropping it would answer a question the boundary cannot see: a
    server-side tool execution would look identical to a plain function call.
    """
    unknown_marker = "OPENAI_UNKNOWN_ITEM_MARKER_6E03"
    responses = RecordingResponses(
        response(
            text="",
            status="completed",
            output=[
                SimpleNamespace(type=item_type, id="item-1", body=unknown_marker),
                native_function_call("web_search", '{"query":"qec"}'),
            ],
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    error = caught.value
    assert error.failure_category == "response"
    assert error.failure_origin == "local_response"
    assert len(responses.create_calls) == 1
    surfaces = _provider_exception_surfaces(error)
    assert surfaces
    assert all(
        leaked not in surface
        for surface in [str(error), *surfaces]
        for leaked in (unknown_marker, item_type, '{"query":"qec"}')
    )


@pytest.mark.parametrize("item_type", OPENAI_UNKNOWN_ITEM_TYPES)
@pytest.mark.asyncio
async def test_openai_native_react_rejects_an_unknown_item_on_the_final_path(
    item_type: str,
) -> None:
    """The same rejection holds when no function call is present."""
    responses = RecordingResponses(
        response(
            text="The report is complete.",
            status="completed",
            output=[SimpleNamespace(type=item_type, id="item-1")],
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    error = caught.value
    assert error.failure_category == "response"
    assert error.failure_origin == "local_response"
    assert len(responses.create_calls) == 1


PROHIBITED_TEXT_SENTINEL = "OPENAI_PROHIBITED_TEXT_SENTINEL_4D17"

# Every template stays a valid instance of its own prohibited shape while
# carrying the sentinel, so "the text is not reachable" is a real check rather
# than one an escaping artefact of ``repr`` would satisfy for free.
PROHIBITED_FINAL_TEXT: tuple[tuple[str, str], ...] = (
    (
        "dsml-markup",
        '<|DSML|tool_calls><|DSML|invoke name="web_search">'
        '{"query":"<S>"}</|DSML|invoke></|DSML|tool_calls>',
    ),
    (
        "tool-call-tag",
        '<tool_call>{"name": "web_search", "note": "<S>"}</tool_call>',
    ),
    (
        "invoke-tag",
        '<invoke name="web_search">{"query": "<S>"}</invoke>',
    ),
    (
        "fenced-legacy-action",
        "```json\n"
        '{"action": "use_tool", "tool_name": "web_search", '
        '"tool_input_json": "{}", "note": "<S>"}\n'
        "```",
    ),
    (
        "bare-legacy-action-object",
        '{"action": "use_tool", "tool_name": "web_search", '
        '"tool_input_json": "{}", "note": "<S>"}',
    ),
    ("bare-tool-name-object", '{"tool_name": "web_search", "note": "<S>"}'),
    (
        "bare-tool-input-object",
        '{"tool_input_json": "{\\"query\\": \\"<S>\\"}"}',
    ),
)


@pytest.mark.parametrize(
    ("shape", "template"),
    PROHIBITED_FINAL_TEXT,
    ids=[shape for shape, _ in PROHIBITED_FINAL_TEXT],
)
@pytest.mark.asyncio
async def test_openai_native_react_rejects_tool_protocol_text(
    shape: str,
    template: str,
) -> None:
    """Tool markup in ``output_text`` is a typed local-response failure."""
    text = template.replace("<S>", PROHIBITED_TEXT_SENTINEL)
    responses = RecordingResponses(
        response(
            text=text,
            status="completed",
            output=[output_message_item(text)],
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    error = caught.value
    assert error.failure_category == "response"
    assert error.failure_origin == "local_response"
    assert error.retryable is False
    assert error.http_status_code is None
    assert len(responses.create_calls) == 1
    surfaces = _provider_exception_surfaces(error)
    assert surfaces
    assert all(
        PROHIBITED_TEXT_SENTINEL not in surface
        for surface in [str(error), *surfaces]
    )


@pytest.mark.asyncio
async def test_openai_native_react_rejects_empty_messages_and_tools() -> None:
    provider = _native_provider(local_tracker(), RecordingResponses())

    with pytest.raises(ValueError, match="at least one item"):
        await provider.complete_react([], [WEB_SEARCH_DEFINITION])
    with pytest.raises(ValueError, match="at least one item"):
        await provider.complete_react(
            [ChatMessage(role="user", content="review")], []
        )


OPENAI_SENTINEL = "OPENAI_NATIVE_SENTINEL_7D20"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            response(
                text="",
                status="completed",
                output=[
                    native_function_call("web_search", "{}"),
                    native_function_call("web_search", "{}"),
                ],
            ),
            id="two-function-calls",
        ),
        pytest.param(
            response(
                text="",
                status="completed",
                output=[native_function_call(OPENAI_SENTINEL, "{}")],
            ),
            id="unknown-function-name",
        ),
        pytest.param(
            response(
                text="",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="web_search",
                        arguments={"marker": OPENAI_SENTINEL},
                    )
                ],
            ),
            id="non-string-arguments",
        ),
        pytest.param(
            response(
                text="",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name=None,
                        arguments="{}",
                    )
                ],
            ),
            id="malformed-call-fields",
        ),
        pytest.param(
            response(
                text=OPENAI_SENTINEL,
                status="completed",
                output=[native_function_call("web_search", "{}")],
            ),
            id="mixed-text-and-call",
        ),
        pytest.param(
            response(text="   ", status="completed", output=[]),
            id="blank-final-text",
        ),
        pytest.param(
            response(text=OPENAI_SENTINEL, status="failed", output=[]),
            id="failed-status",
        ),
        pytest.param(
            response(text=OPENAI_SENTINEL, status="cancelled", output=[]),
            id="cancelled-status",
        ),
        pytest.param(
            response(text=OPENAI_SENTINEL, status="in_progress", output=[]),
            id="in-progress-status",
        ),
        pytest.param(
            response(
                text=OPENAI_SENTINEL,
                status="incomplete",
                output=[],
                incomplete_reason=OPENAI_SENTINEL,
            ),
            id="incomplete-for-another-reason",
        ),
        pytest.param(
            response(
                text=OPENAI_SENTINEL,
                status="incomplete",
                output=[],
                incomplete_reason="max_output_tokens",
            ),
            id="incomplete-at-the-output-limit",
        ),
        pytest.param(
            response(
                text=OPENAI_SENTINEL,
                status="completed",
                output=OPENAI_SENTINEL,
            ),
            id="malformed-output-container",
        ),
        pytest.param(
            SimpleNamespace(
                id="malformed-usage",
                status="completed",
                output=[],
                incomplete_details=None,
                output_text=OPENAI_SENTINEL,
                output_parsed=None,
                model=None,
                usage=SimpleNamespace(
                    input_tokens=OPENAI_SENTINEL,
                    output_tokens=OPENAI_SENTINEL,
                    total_tokens=OPENAI_SENTINEL,
                ),
                marker=OPENAI_SENTINEL,
            ),
            id="malformed-usage",
        ),
    ],
)
@pytest.mark.asyncio
async def test_openai_native_react_fails_closed_without_leaking(
    reply: object,
) -> None:
    responses = RecordingResponses(reply)
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    assert len(responses.create_calls) == 1
    surfaces = _provider_exception_surfaces(caught.value)
    assert surfaces
    assert all(
        OPENAI_SENTINEL not in surface
        for surface in [str(caught.value), *surfaces]
    )


@pytest.mark.asyncio
async def test_openai_native_sdk_and_envelope_failures_are_distinguishable() -> None:
    """The formerly ambiguous pair: one public category, two origins."""
    tracker = local_tracker()
    sdk_provider = _native_provider(
        tracker, RecordingResponses(OpenAIError("sdk rejected the request"))
    )
    envelope_provider = _native_provider(
        tracker,
        RecordingResponses(
            response(
                text=OPENAI_SENTINEL,
                status="completed",
                output=OPENAI_SENTINEL,
            )
        ),
    )

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as sdk_caught:
            await sdk_provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )
        with pytest.raises(ProviderResponseError) as envelope_caught:
            await envelope_provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    sdk_error = sdk_caught.value
    envelope_error = envelope_caught.value

    assert sdk_error.failure_category == "response"
    assert sdk_error.failure_origin == "sdk"
    assert envelope_error.failure_category == "response"
    assert envelope_error.failure_origin == "local_response"

    # Every other public field a consumer can read stays identical.
    assert sdk_error.retryable is False
    assert envelope_error.retryable is False
    assert sdk_error.http_status_code is None
    assert envelope_error.http_status_code is None
    assert sdk_error.status_code is None
    assert envelope_error.status_code is None


def test_fresh_provider_error_copies_the_failure_origin() -> None:
    """The traceback-free copy must not silently drop the origin."""
    original = ProviderResponseError(
        "OpenAI response contained malformed output",
        failure_origin="local_response",
    )

    fresh = openai_provider_module._fresh_provider_error(original)

    assert fresh is not original
    assert fresh.failure_origin == "local_response"
    assert fresh.failure_category == original.failure_category
    assert fresh.retryable == original.retryable
    assert fresh.__traceback__ is None


@pytest.mark.asyncio
async def test_openai_public_error_never_reaches_the_sdk_exception(
    monkeypatch,
) -> None:
    """The SDK object must be unreachable, not merely unchained."""
    _recorded_sleeps(monkeypatch)
    request_marker = "OPENAI_REQUEST_MARKER_6C13"
    sdk_error = APIConnectionError(
        request=httpx.Request(
            "POST",
            "https://api.openai.com/v1/responses",
            content=request_marker,
        )
    )
    responses = RecordingResponses(sdk_error, sdk_error, sdk_error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=2),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    assert len(responses.create_calls) == 3
    assert caught.value.failure_category == "transport"
    assert caught.value.failure_origin == "sdk"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not _exception_reaches(caught.value, sdk_error)
    assert request_marker not in repr(
        _provider_exception_surfaces(caught.value)
    )


@pytest.mark.parametrize("path", ["plain", "structured"])
@pytest.mark.asyncio
async def test_openai_every_entry_point_severs_the_sdk_exception(
    monkeypatch, path: str
) -> None:
    """Severing is a property of the translator, not of the native path."""
    _recorded_sleeps(monkeypatch)
    sdk_error = APIConnectionError(
        request=httpx.Request(
            "POST",
            "https://api.openai.com/v1/responses",
            content="MARKER",
        )
    )
    responses = RecordingResponses(sdk_error, sdk_error, sdk_error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=2),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    class _Answer(BaseModel):
        answer: str = "yes"

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            if path == "plain":
                await provider.complete(
                    [ChatMessage(role="user", content="review")]
                )
            else:
                await provider.complete_structured(
                    [ChatMessage(role="user", content="review")],
                    _Answer,
                )

    # ``complete_react`` asserts the exhausted-retry count; ``complete_structured``
    # reaches the SDK through ``responses.parse``, which this fake does not count.
    assert caught.value.failure_origin == "sdk"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not _exception_reaches(caught.value, sdk_error)


@pytest.mark.asyncio
async def test_openai_native_react_failures_do_not_retain_the_prompt() -> None:
    """The prompt is provider-adjacent state too, and must be cleared."""
    prompt_marker = "OPENAI_PROMPT_MARKER_9E31"
    responses = RecordingResponses(
        response(text="   ", status="completed", output=[])
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", prompt_marker):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content=prompt_marker)],
                [WEB_SEARCH_DEFINITION],
            )

    assert prompt_marker not in repr(_provider_exception_surfaces(caught.value))


@pytest.mark.asyncio
async def test_openai_native_react_maps_the_output_limit() -> None:
    responses = RecordingResponses(
        response(
            text="",
            status="incomplete",
            output=[],
            incomplete_reason="max_output_tokens",
        )
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderOutputLimitError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    assert caught.value.telemetry.finish_reason_category == "length"
    assert caught.value.telemetry.configured_max_tokens == openai_config().max_tokens


@pytest.mark.asyncio
async def test_openai_native_react_sends_the_per_call_output_budget() -> None:
    responses = RecordingResponses(
        response(text="done", status="completed", output=[])
    )
    tracker = local_tracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        await provider.complete_react(
            [ChatMessage(role="user", content="review")],
            [WEB_SEARCH_DEFINITION],
            max_tokens=2048,
        )

    assert responses.create_calls[0]["max_output_tokens"] == 2048


@pytest.mark.asyncio
async def test_openai_native_react_retries_one_transient_error(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    responses = RecordingResponses(
        APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
        response(
            text="",
            status="completed",
            output=[native_function_call("web_search", '{"query":"qec"}')],
        ),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=1, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "review"):
        turn = await provider.complete_react(
            [ChatMessage(role="user", content="review")], [WEB_SEARCH_DEFINITION]
        )

    assert turn.tool_call == NativeToolCall(
        tool_name="web_search", arguments_json='{"query":"qec"}'
    )
    assert len(responses.create_calls) == 2
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_openai_native_react_records_the_attempt_count_after_a_retry(
    monkeypatch,
) -> None:
    """The retry counter is visible on the limit path and must be real."""
    slept = _recorded_sleeps(monkeypatch)
    responses = RecordingResponses(
        APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
        response(
            text="",
            status="incomplete",
            output=[],
            incomplete_reason="max_output_tokens",
        ),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=1, retry_initial_delay=1.0, retry_max_delay=16.0),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderOutputLimitError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    assert slept == [1.0]
    assert caught.value.telemetry.request_attempt == 2


@pytest.mark.asyncio
async def test_openai_native_react_span_records_counts_not_names() -> None:
    responses = RecordingResponses(
        response(
            text="",
            status="completed",
            output=[native_function_call("web_search", '{"query":"qec capacity"}')],
        )
    )
    tracker = CapturingTracker()
    provider = _native_provider(tracker, responses)

    async with tracker.session_span("session-1", "review"):
        await provider.complete_react(
            [ChatMessage(role="user", content="review")],
            [WEB_SEARCH_DEFINITION],
            agent_name="critic",
        )

    assert tracker.llm_inputs[0]["operation"] == "react_tool_turn"
    assert tracker.llm_inputs[0]["tool_count"] == 1
    assert tracker.llm_inputs[0]["message_count"] == 1
    assert tracker.llm_inputs[0]["agent_name"] == "critic"
    recorded = repr(tracker.llm_inputs)
    assert "web_search" not in recorded
    assert "qec capacity" not in recorded




@pytest.mark.asyncio
async def test_openai_native_react_exhausted_retries_chain_no_sdk_error(
    monkeypatch,
) -> None:
    """A typed error must not carry the SDK exception, which holds
    ``request`` (the prompt and tool definitions) and ``body``."""
    _recorded_sleeps(monkeypatch)
    sdk_error = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com")
    )
    sdk_error.request_marker = OPENAI_SENTINEL
    responses = RecordingResponses(sdk_error, sdk_error)
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(retry_count=1, retry_initial_delay=1.0, retry_max_delay=4.0),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "review"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete_react(
                [ChatMessage(role="user", content="review")],
                [WEB_SEARCH_DEFINITION],
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert OPENAI_SENTINEL not in repr(_provider_exception_surfaces(caught.value))
