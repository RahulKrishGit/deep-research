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
    ProviderRateLimitError,
    ProviderResponseError,
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
