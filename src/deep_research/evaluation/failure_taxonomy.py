"""Typed, cause-chain-based classification for evaluation failures.

This module is deliberately independent of the judge implementation. Targets
use it now, and the judge integration consumes the same stable reasons and
safe diagnostic projection in a later task.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import islice

from pydantic import ValidationError

from deep_research.evaluation.models import (
    EvaluationFailureDetails,
    EvaluatorDiagnostic,
    FailureClassification,
    OutputLimitFailureDetails,
    ProviderFailureDetails,
    SchemaFailureDetails,
)
from deep_research.providers import (
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.tools.base import ToolExecutionError


def _cause_chain(error: BaseException) -> Iterator[BaseException]:
    """Yield each distinct explicit cause, including the outer wrapper."""
    candidate: BaseException | None = error
    seen: set[int] = set()
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        yield candidate
        candidate = candidate.__cause__


def _provider_response_reason(error: ProviderResponseError) -> str:
    return {
        "transport": "provider_transport",
        "http": "provider_http",
        "response": "provider_response",
        "output_limit": "provider_response",
    }[error.failure_category]


def _select_failure_cause(error: BaseException) -> BaseException | None:
    """Choose one typed cause by specificity, independent of chain order."""
    chain = tuple(_cause_chain(error))
    priority: tuple[type[BaseException], ...] = (
        ProviderOutputLimitError,
        StructuredOutputError,
        ProviderTimeoutError,
        ProviderRateLimitError,
        ProviderResponseError,
        ToolExecutionError,
        ValidationError,
        ProviderError,
    )
    for expected_type in priority:
        for candidate in chain:
            if isinstance(candidate, expected_type):
                return candidate
    return None


def classify_failure(error: BaseException) -> FailureClassification:
    """Classify the most specific typed failure anywhere in ``__cause__``."""
    candidate = _select_failure_cause(error)
    if isinstance(candidate, ProviderOutputLimitError):
        return FailureClassification(stage="provider", reason="output_limit")
    if isinstance(candidate, StructuredOutputError):
        return FailureClassification(stage="validation", reason="schema_output")
    if isinstance(candidate, ProviderTimeoutError):
        return FailureClassification(stage="provider", reason="provider_timeout")
    if isinstance(candidate, ProviderRateLimitError):
        return FailureClassification(
            stage="provider", reason="provider_rate_limit"
        )
    if isinstance(candidate, ProviderResponseError):
        return FailureClassification(
            stage="provider",
            reason=_provider_response_reason(candidate),  # type: ignore[arg-type]
        )
    if isinstance(candidate, ToolExecutionError):
        return FailureClassification(stage="tool", reason="tool_failure")
    if isinstance(candidate, ValidationError):
        return FailureClassification(
            stage="validation", reason="validation_failure"
        )
    if isinstance(candidate, ProviderError):
        return FailureClassification(stage="provider", reason="provider_failure")
    return FailureClassification(stage="unhandled", reason="unhandled_failure")


def _provider_details(
    *,
    kind: str,
    error: BaseException,
    retryable: bool,
    status_code: int | None = None,
) -> ProviderFailureDetails:
    return ProviderFailureDetails(
        kind=kind,  # type: ignore[arg-type]
        type=type(error).__name__,
        retryable=retryable,
        status_code=status_code,
    )


def safe_failure_details(error: BaseException) -> EvaluationFailureDetails | None:
    """Project only typed, bounded, non-content details from a cause chain."""
    candidate = _select_failure_cause(error)
    if isinstance(candidate, ProviderOutputLimitError):
        telemetry = candidate.telemetry
        return OutputLimitFailureDetails(
            finish_reason_category=telemetry.finish_reason_category,
            configured_max_tokens=telemetry.configured_max_tokens,
            usage=telemetry.usage,
            request_attempt=telemetry.request_attempt,
            structured_attempt=telemetry.structured_attempt,
        )
    if isinstance(candidate, StructuredOutputError):
        projected: list[EvaluatorDiagnostic] = []
        try:
            diagnostics = iter(candidate.diagnostics)
            for item in islice(diagnostics, 2):
                if not isinstance(item, StructuredValidationDiagnostic):
                    continue
                projected.append(
                    EvaluatorDiagnostic(
                        kind="schema_output",
                        attempt=item.attempt,
                        category=(
                            None
                            if item.category in (None, "schema_output")
                            else item.category
                        ),
                        field_paths=item.field_paths[:16],
                    )
                )
        except Exception:
            pass
        return SchemaFailureDetails(diagnostics=tuple(projected))
    if isinstance(candidate, ProviderTimeoutError):
        return _provider_details(
            kind="provider_timeout",
            error=candidate,
            retryable=True,
        )
    if isinstance(candidate, ProviderRateLimitError):
        return _provider_details(
            kind="provider_rate_limit",
            error=candidate,
            retryable=True,
        )
    if isinstance(candidate, ProviderResponseError):
        return _provider_details(
            kind={
                "transport": "provider_transport",
                "http": "provider_http",
                "response": "provider_response",
                "output_limit": "provider_response",
            }[candidate.failure_category],
            error=candidate,
            retryable=candidate.retryable,
            status_code=candidate.http_status_code,
        )
    if isinstance(candidate, ProviderError):
        return _provider_details(
            kind="provider_failure",
            error=candidate,
            retryable=False,
        )
    return None


# Descriptive aliases for downstream callers and hidden compatibility checks.
classify_provider_failure = classify_failure
project_failure_details = safe_failure_details
