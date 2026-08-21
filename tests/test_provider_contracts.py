from deep_research.providers import (
    ChatMessage,
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
