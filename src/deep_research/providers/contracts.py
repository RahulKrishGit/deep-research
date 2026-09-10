from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import islice
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
ProviderFailureKind: TypeAlias = Literal[
    "output_limit",
    "schema_output",
    "provider_timeout",
    "provider_rate_limit",
    "provider_transport",
    "provider_http",
    "provider_response",
    "provider_failure",
]
StructuredDiagnosticCategory: TypeAlias = Literal[
    "schema_output",
    "json_invalid",
    "missing",
    "extra_forbidden",
    "type_mismatch",
    "numeric_bounds",
    "string_bounds",
    "other_schema",
]
PositiveInt: TypeAlias = Annotated[int, Field(gt=0, strict=True)]

_FIELD_PATH_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$|^[0-9]+$")
_MAX_FIELD_PATHS = 16
_MAX_FIELD_PATH_LENGTH = 128
_MAX_STRUCTURED_DIAGNOSTICS = 2


def _normalize_field_path(value: object) -> str:
    """Keep only normalized schema locations, never validation input values."""
    if not isinstance(value, str):
        return "$"
    parts = [part.strip() for part in value.strip().split(".")]
    if not parts or any(not part for part in parts):
        return "$"
    if any(
        not _FIELD_PATH_SEGMENT.fullmatch(part)
        for part in parts
    ):
        return "$"
    normalized = ".".join(parts)
    return normalized if len(normalized) <= _MAX_FIELD_PATH_LENGTH else "$"


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
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    finish_reason_category: FinishReasonCategory
    configured_max_tokens: PositiveInt
    usage: TokenUsage
    request_attempt: PositiveInt
    structured_attempt: PositiveInt | None = None


class StructuredValidationDiagnostic(ProviderContract):
    """One bounded, provider-output-free structured validation record."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    attempt: PositiveInt
    field_paths: tuple[str, ...] = Field(
        min_length=1, max_length=_MAX_FIELD_PATHS
    )
    category: StructuredDiagnosticCategory | None = None

    @field_validator("field_paths", mode="before")
    @classmethod
    def normalize_field_paths(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        if not isinstance(value, Sequence):
            raise TypeError("field_paths must be a sequence of strings")
        normalized = tuple(_normalize_field_path(item) for item in value)
        return normalized or ("$",)


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

    def __init__(
        self,
        message: str,
        *,
        diagnostics: Sequence[StructuredValidationDiagnostic] = (),
    ) -> None:
        self.diagnostics = tuple(
            item
            if isinstance(item, StructuredValidationDiagnostic)
            else StructuredValidationDiagnostic.model_validate(item)
            for item in islice(diagnostics, _MAX_STRUCTURED_DIAGNOSTICS)
        )
        super().__init__(message)

    @property
    def validation_diagnostics(self) -> tuple[StructuredValidationDiagnostic, ...]:
        """Compatibility alias for callers that name the validation records."""
        return self.diagnostics


class ProviderFailureSnapshot(ProviderContract):
    """Immutable, finite details safe to retain for an agent provider error."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    kind: ProviderFailureKind
    exception_type: str = Field(min_length=1, max_length=128)
    retryable: bool | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    configured_max_tokens: PositiveInt | None = None
    usage: TokenUsage | None = None
    request_attempt: PositiveInt | None = None
    structured_attempt: PositiveInt | None = None
    diagnostics: tuple[StructuredValidationDiagnostic, ...] = Field(
        default=(), max_length=_MAX_STRUCTURED_DIAGNOSTICS
    )


def provider_failure_snapshot(error: ProviderError) -> ProviderFailureSnapshot:
    """Project one provider error into bounded, provider-content-free data."""
    exception_type = type(error).__name__
    if isinstance(error, ProviderOutputLimitError):
        telemetry = error.telemetry
        return ProviderFailureSnapshot(
            kind="output_limit",
            exception_type=exception_type,
            retryable=error.retryable,
            configured_max_tokens=telemetry.configured_max_tokens,
            usage=telemetry.usage,
            request_attempt=telemetry.request_attempt,
            structured_attempt=telemetry.structured_attempt,
        )
    if isinstance(error, StructuredOutputError):
        diagnostics = tuple(
            item
            for item in islice(error.diagnostics, _MAX_STRUCTURED_DIAGNOSTICS)
            if isinstance(item, StructuredValidationDiagnostic)
        )
        return ProviderFailureSnapshot(
            kind="schema_output",
            exception_type=exception_type,
            diagnostics=diagnostics,
        )
    if isinstance(error, ProviderTimeoutError):
        return ProviderFailureSnapshot(
            kind="provider_timeout",
            exception_type=exception_type,
            retryable=True,
        )
    if isinstance(error, ProviderRateLimitError):
        return ProviderFailureSnapshot(
            kind="provider_rate_limit",
            exception_type=exception_type,
            retryable=True,
        )
    if isinstance(error, ProviderResponseError):
        kind: ProviderFailureKind = {
            "transport": "provider_transport",
            "http": "provider_http",
            "response": "provider_response",
            "output_limit": "provider_response",
        }[error.failure_category]
        return ProviderFailureSnapshot(
            kind=kind,
            exception_type=exception_type,
            retryable=error.retryable,
            http_status_code=error.http_status_code,
        )
    return ProviderFailureSnapshot(
        kind="provider_failure",
        exception_type=exception_type,
    )


OpenAIProviderError = ProviderError
