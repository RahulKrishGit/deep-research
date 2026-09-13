import pytest

from deep_research.observability import TokenUsage
from deep_research.providers import (
    ChatMessage,
    NativeToolCall,
    NativeToolTurn,
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
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
