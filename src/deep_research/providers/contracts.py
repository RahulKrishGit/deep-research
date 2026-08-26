from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from deep_research.observability import TokenUsage

MessageRole = Literal["developer", "system", "user", "assistant"]
FinishReasonCategory: TypeAlias = Literal[
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "insufficient_system_resource",
    "other",
]
ProviderFailureCategory: TypeAlias = Literal[
    "output_limit", "transport", "http", "response"
]
PositiveInt: TypeAlias = Annotated[int, Field(gt=0, strict=True)]


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatMessage(ProviderContract):
    role: MessageRole
    content: str = Field(min_length=1)


class ChatResult(ProviderContract):
    text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class ProviderResponseTelemetry(ProviderContract):
    finish_reason_category: FinishReasonCategory
    configured_max_tokens: PositiveInt
    usage: TokenUsage
    request_attempt: PositiveInt
    structured_attempt: PositiveInt | None = None


class ProviderError(RuntimeError):
    """Base caller-facing error for every chat provider boundary."""


class ProviderConfigurationError(ProviderError):
    """The selected provider or effective model configuration is invalid."""


class ProviderTimeoutError(ProviderError):
    """A provider request exceeded its timeout."""


class ProviderRateLimitError(ProviderError):
    """A provider rejected a request due to rate limiting."""


class ProviderResponseError(ProviderError):
    """A provider returned an unusable response or status error.

    ``retryable`` marks transient failures (connection errors, 408/409/429,
    and 5xx statuses) that the repo-owned retry policy may retry;
    deterministic 4xx and content failures default to ``False``.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        failure_category: ProviderFailureCategory = "response",
        http_status_code: int | None = None,
    ) -> None:
        if failure_category not in {"output_limit", "transport", "http", "response"}:
            raise ValueError("failure_category must be a known provider category")
        if http_status_code is not None and (
            isinstance(http_status_code, bool)
            or not isinstance(http_status_code, int)
            or not 100 <= http_status_code <= 599
        ):
            raise ValueError("http_status_code must be an HTTP status code")
        super().__init__(message)
        self.retryable = retryable
        self.failure_category = failure_category
        self.http_status_code = http_status_code

    @property
    def status_code(self) -> int | None:
        """Compatibility alias for the safe HTTP status value."""
        return self.http_status_code


class ProviderOutputLimitError(ProviderResponseError):
    """A provider response reached its configured output limit."""

    SAFE_MESSAGE = "Provider response reached the configured output limit"

    def __init__(self, telemetry: ProviderResponseTelemetry) -> None:
        if not isinstance(telemetry, ProviderResponseTelemetry):
            raise TypeError("telemetry must be ProviderResponseTelemetry")
        super().__init__(
            self.SAFE_MESSAGE,
            retryable=False,
            failure_category="output_limit",
        )
        self.telemetry = telemetry


class StructuredOutputError(ProviderError):
    """Structured output remained invalid after one repair request."""


OpenAIProviderError = ProviderError
