"""Tests for the shared safe provider-failure taxonomy."""

from __future__ import annotations

import pytest

import deep_research.evaluation.models as models_module
from deep_research.agents.errors import PlanningError
from deep_research.observability import TokenUsage
from deep_research.providers import (
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
)


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=4, output_tokens=4096),
            request_attempt=1,
            structured_attempt=2,
        )
    )


def _taxonomy_module():
    try:
        import deep_research.evaluation.failure_taxonomy as taxonomy
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 2 taxonomy module is missing: {error}")

    return taxonomy


def _schema_error() -> StructuredOutputError:
    diagnostic_type = getattr(
        __import__("deep_research.providers.contracts", fromlist=[
            "StructuredValidationDiagnostic"
        ]),
        "StructuredValidationDiagnostic",
        None,
    )
    if diagnostic_type is None:
        return StructuredOutputError("schema failed")
    return StructuredOutputError(
        "schema failed",
        diagnostics=(
            diagnostic_type(
                attempt=2,
                field_paths=("title",),
                category="schema_output",
            ),
        ),
    )


@pytest.mark.parametrize(
    ("error", "stage", "reason"),
    [
        (_output_limit_error(), "provider", "output_limit"),
        (_schema_error(), "validation", "schema_output"),
        (
            ProviderTimeoutError("timeout details must not classify by text"),
            "provider",
            "provider_timeout",
        ),
        (
            ProviderRateLimitError("rate limit details must not classify by text"),
            "provider",
            "provider_rate_limit",
        ),
        (
            ProviderResponseError(
                "transport", failure_category="transport", retryable=True
            ),
            "provider",
            "provider_transport",
        ),
        (
            ProviderResponseError(
                "http", failure_category="http", http_status_code=503, retryable=True
            ),
            "provider",
            "provider_http",
        ),
        (
            ProviderResponseError("response", failure_category="response"),
            "provider",
            "provider_response",
        ),
        (ProviderError("generic provider failure"), "provider", "provider_failure"),
    ],
)
def test_classify_failure_uses_specific_typed_causes_before_generic_provider(
    error: BaseException,
    stage: str,
    reason: str,
) -> None:
    taxonomy = _taxonomy_module()
    result = taxonomy.classify_failure(error)

    assert isinstance(result, models_module.FailureClassification)
    assert (result.stage, result.reason) == (stage, reason)
    assert "reach" not in result.reason


def test_classify_failure_walks_planner_cause_chain() -> None:
    cause = _output_limit_error()
    try:
        raise PlanningError("static planner context") from cause
    except PlanningError as error:
        result = _taxonomy_module().classify_failure(error)

    assert (result.stage, result.reason) == ("provider", "output_limit")


def test_specific_cause_wins_over_outer_generic_provider_wrapper() -> None:
    cause = _output_limit_error()
    try:
        raise ProviderError("outer provider wrapper") from cause
    except ProviderError as error:
        taxonomy = _taxonomy_module()
        result = taxonomy.classify_failure(error)
        details = taxonomy.safe_failure_details(error)

    assert (result.stage, result.reason) == ("provider", "output_limit")
    assert isinstance(details, models_module.OutputLimitFailureDetails)


@pytest.mark.parametrize(
    "error_type",
    [
        lambda: ProviderTimeoutError("timeout"),
        lambda: ProviderRateLimitError("rate limit"),
        lambda: ProviderResponseError(
            "transport", failure_category="transport", retryable=True
        ),
        lambda: ProviderResponseError(
            "http", failure_category="http", http_status_code=503
        ),
    ],
)
def test_safe_provider_details_are_allow_listed(error_type) -> None:
    error = error_type() if isinstance(error_type, type) else error_type()
    details = _taxonomy_module().safe_failure_details(error)

    assert isinstance(details, models_module.ProviderFailureDetails)
    assert set(details.model_dump(mode="json")) == {
        "kind",
        "type",
        "retryable",
        "status_code",
    }


def test_safe_failure_details_never_serialize_provider_text() -> None:
    output_limit = _output_limit_error()
    schema = _schema_error()

    output_details = _taxonomy_module().safe_failure_details(output_limit)
    schema_details = _taxonomy_module().safe_failure_details(schema)

    assert isinstance(output_details, models_module.OutputLimitFailureDetails)
    assert isinstance(schema_details, models_module.SchemaFailureDetails)
    assert "schema failed" not in schema_details.model_dump_json()
    assert "partial provider output" not in output_details.model_dump_json()
