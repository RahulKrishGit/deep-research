from typing import get_args

import pytest

from deep_research.observability import TokenUsage
from deep_research.providers import (
    ChatMessage,
    NativeToolCall,
    NativeToolTurn,
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderError,
    ProviderFailureOrigin,
    ProviderFailureSnapshot,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    provider_failure_snapshot,
)


def test_provider_errors_share_one_neutral_base() -> None:
    assert OpenAIProviderError is ProviderError
    for error_type in (
        ProviderConfigurationError,
        ProviderTimeoutError,
        ProviderRateLimitError,
        ProviderResponseError,
        StructuredOutputError,
    ):
        assert issubclass(error_type, ProviderError)


def test_chat_message_accepts_provider_neutral_roles() -> None:
    assert ChatMessage(role="developer", content="policy").role == "developer"
    assert ChatMessage(role="system", content="policy").role == "system"


def test_a_native_tool_turn_carries_exactly_one_tool_call() -> None:
    turn = NativeToolTurn(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        tool_call=NativeToolCall(
            tool_name="web_search",
            arguments_json='{"query":"qec capacity"}',
        ),
    )
    assert turn.final_answer is None
    assert turn.tool_call.tool_name == "web_search"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "tool_call": {
                "tool_name": "web_search",
                "arguments_json": "{}",
            },
            "final_answer": "done",
        },
    ],
)
def test_a_native_tool_turn_rejects_zero_or_two_outcomes(payload) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            **payload,
        )


def test_provider_failure_origin_is_a_closed_vocabulary() -> None:
    """Two origins, and no third: SDK rejection or our own validation."""
    assert get_args(ProviderFailureOrigin) == ("sdk", "local_response")


def test_provider_response_error_requires_a_failure_origin() -> None:
    """The origin is not inferable after the fact, so construction demands it."""
    with pytest.raises(TypeError):
        ProviderResponseError("Provider request failed")


@pytest.mark.parametrize("origin", ["sdk", "local_response"])
def test_provider_response_error_records_its_origin(origin: str) -> None:
    error = ProviderResponseError(
        "Provider request failed", failure_origin=origin
    )
    assert error.failure_origin == origin


@pytest.mark.parametrize("origin", ["", "local", "SDK", "provider", "sdk "])
def test_provider_response_error_rejects_an_unknown_origin(origin: str) -> None:
    with pytest.raises(ValueError):
        ProviderResponseError(
            "Provider request failed", failure_origin=origin
        )


def test_provider_failure_snapshot_carries_the_origin() -> None:
    """The formerly ambiguous pair is distinguishable from the snapshot alone."""
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
    assert (
        provider_failure_snapshot(local_error).failure_origin == "local_response"
    )


def test_the_two_origins_share_every_other_snapshot_field() -> None:
    """Origin is the only difference: category, retry and status agree."""
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
    sdk_snapshot = provider_failure_snapshot(sdk_error)
    local_snapshot = provider_failure_snapshot(local_error)

    assert sdk_snapshot.kind == local_snapshot.kind == "provider_response"
    assert sdk_snapshot.retryable == local_snapshot.retryable is False
    assert sdk_snapshot.http_status_code == local_snapshot.http_status_code
    assert sdk_snapshot.exception_type == local_snapshot.exception_type


def test_a_snapshot_origin_is_optional_for_existing_artifacts() -> None:
    """v1 artifacts predate the field and must still read back."""
    snapshot = ProviderFailureSnapshot(
        kind="provider_response",
        exception_type="ProviderResponseError",
    )
    assert snapshot.failure_origin is None


def test_a_timeout_snapshot_has_no_failure_origin() -> None:
    """Only response errors are ambiguous, so only they carry an origin."""
    snapshot = provider_failure_snapshot(
        ProviderTimeoutError("provider request timed out")
    )
    assert snapshot.kind == "provider_timeout"
    assert snapshot.failure_origin is None


def test_redacting_a_local_response_error_keeps_its_typed_fields() -> None:
    """Redaction replaces the message, never the locally derived failure data."""
    error = ProviderResponseError(
        "Provider response was malformed for key sk-live-secret",
        failure_origin="local_response",
        failure_category="response",
    )

    redacted = error.redacted_copy("Provider response was malformed: [REDACTED]")

    assert type(redacted) is ProviderResponseError
    assert redacted.failure_origin == "local_response"
    assert redacted.retryable is False
    assert redacted.failure_category == "response"
    assert redacted.http_status_code is None
    assert str(redacted) == "Provider response was malformed: [REDACTED]"


def test_redacting_an_sdk_error_keeps_its_typed_fields() -> None:
    """The sdk origin survives, so the remote error stays diagnosable."""
    error = ProviderResponseError(
        "Provider request failed for key sk-live-secret",
        failure_origin="sdk",
        retryable=True,
        failure_category="http",
        http_status_code=503,
    )

    redacted = error.redacted_copy("Provider request failed: [REDACTED]")

    assert type(redacted) is ProviderResponseError
    assert redacted.failure_origin == "sdk"
    assert redacted.retryable is True
    assert redacted.failure_category == "http"
    assert redacted.http_status_code == 503
    assert str(redacted) == "Provider request failed: [REDACTED]"


def test_redacting_an_output_limit_error_keeps_its_type_and_telemetry() -> None:
    """Its message is the safe constant, so the copy is not the base error."""
    telemetry = ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=512,
        usage=TokenUsage(input_tokens=40, output_tokens=512),
        request_attempt=2,
        structured_attempt=1,
    )
    error = ProviderOutputLimitError(telemetry)

    redacted = error.redacted_copy("caller supplied text is ignored here")

    assert type(redacted) is ProviderOutputLimitError
    assert isinstance(redacted, ProviderResponseError)
    assert redacted.telemetry == telemetry
    assert str(redacted) == ProviderOutputLimitError.SAFE_MESSAGE


def test_a_redacted_copy_carries_no_provider_content() -> None:
    error = ProviderResponseError(
        "Provider request failed for key sk-live-abc123",
        failure_origin="sdk",
    )

    redacted = error.redacted_copy("[REDACTED]")

    assert "sk-live-abc123" not in str(redacted)
    assert str(redacted) == "[REDACTED]"


class _TelemetryOnlyError(ProviderError):
    """A provider error whose constructor cannot rebuild it from a message."""

    def __init__(self, *, telemetry: object) -> None:
        super().__init__("provider failed")


def test_an_unrebuildable_provider_error_falls_back_to_the_base_type() -> None:
    """The base copy is fail-safe: redaction must never raise."""
    error = _TelemetryOnlyError(telemetry=object())

    with pytest.raises(TypeError):
        type(error)("[REDACTED]")

    redacted = error.redacted_copy("[REDACTED]")

    assert type(redacted) is ProviderError
    assert str(redacted) == "[REDACTED]"
