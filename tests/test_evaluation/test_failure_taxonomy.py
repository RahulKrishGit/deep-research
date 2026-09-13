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
    StructuredValidationDiagnostic,
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
                "transport",
                failure_category="transport",
                retryable=True,
                failure_origin="sdk",
            ),
            "provider",
            "provider_transport",
        ),
        (
            ProviderResponseError(
                "http",
                failure_category="http",
                http_status_code=503,
                retryable=True,
                failure_origin="sdk",
            ),
            "provider",
            "provider_http",
        ),
        (
            ProviderResponseError(
                "response",
                failure_category="response",
                failure_origin="local_response",
            ),
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


def test_output_limit_cause_wins_over_outer_provider_response_wrapper() -> None:
    cause = _output_limit_error()
    try:
        raise ProviderResponseError(
            "outer response wrapper",
            failure_category="response",
            failure_origin="local_response",
        ) from cause
    except ProviderResponseError as error:
        taxonomy = _taxonomy_module()
        result = taxonomy.classify_failure(error)
        details = taxonomy.safe_failure_details(error)

    assert (result.stage, result.reason) == ("provider", "output_limit")
    assert isinstance(details, models_module.OutputLimitFailureDetails)
    assert details.request_attempt == 1
    assert details.structured_attempt == 2


def test_generic_output_limit_category_remains_provider_response() -> None:
    error = ProviderResponseError(
        "generic response without telemetry",
        failure_category="output_limit",
        failure_origin="local_response",
    )
    taxonomy = _taxonomy_module()

    result = taxonomy.classify_failure(error)
    details = taxonomy.safe_failure_details(error)

    assert (result.stage, result.reason) == ("provider", "provider_response")
    assert isinstance(details, models_module.ProviderFailureDetails)
    assert details.kind == "provider_response"


@pytest.mark.parametrize(
    "error_type",
    [
        lambda: ProviderTimeoutError("timeout"),
        lambda: ProviderRateLimitError("rate limit"),
        lambda: ProviderResponseError(
            "transport",
            failure_category="transport",
            retryable=True,
            failure_origin="sdk",
        ),
        lambda: ProviderResponseError(
            "http",
            failure_category="http",
            http_status_code=503,
            failure_origin="sdk",
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
        "failure_origin",
        "retryable",
        "status_code",
    }


def test_provider_failure_details_project_the_origin() -> None:
    """The origin reaches the artifact while category and reason stay equal."""
    sdk_error = ProviderResponseError(
        "sdk rejected the request",
        failure_category="response",
        failure_origin="sdk",
    )
    local_error = ProviderResponseError(
        "our own validation rejected the response",
        failure_category="response",
        failure_origin="local_response",
    )
    taxonomy = _taxonomy_module()

    sdk_details = taxonomy.safe_failure_details(sdk_error)
    local_details = taxonomy.safe_failure_details(local_error)

    assert isinstance(sdk_details, models_module.ProviderFailureDetails)
    assert isinstance(local_details, models_module.ProviderFailureDetails)
    assert sdk_details.failure_origin == "sdk"
    assert local_details.failure_origin == "local_response"
    # Everything the evaluation reason is built from is deliberately identical;
    # only the origin separates the two.
    assert sdk_details.kind == local_details.kind == "provider_response"
    assert sdk_details.retryable == local_details.retryable
    assert sdk_details.status_code == local_details.status_code
    assert taxonomy.classify_failure(
        sdk_error
    ) == taxonomy.classify_failure(local_error)
    assert taxonomy.classify_failure(sdk_error).reason == "provider_response"


def test_safe_failure_details_never_serialize_provider_text() -> None:
    output_limit = _output_limit_error()
    schema = _schema_error()

    output_details = _taxonomy_module().safe_failure_details(output_limit)
    schema_details = _taxonomy_module().safe_failure_details(schema)

    assert isinstance(output_details, models_module.OutputLimitFailureDetails)
    assert isinstance(schema_details, models_module.SchemaFailureDetails)
    assert "schema failed" not in schema_details.model_dump_json()
    assert "partial provider output" not in output_details.model_dump_json()


def test_safe_schema_projection_skips_malformed_diagnostics_without_raising() -> None:
    valid = StructuredValidationDiagnostic(
        attempt=2,
        field_paths=("title",),
        category="schema_output",
    )
    error = StructuredOutputError("safe schema failure", diagnostics=(valid,))
    error.diagnostics = (
        {"attempt": "REJECTED_PROVIDER_MARKER_7E5C"},
        valid,
        *([object()] * 17),
    )

    details = _taxonomy_module().safe_failure_details(error)

    assert isinstance(details, models_module.SchemaFailureDetails)
    assert details.model_dump(mode="json") == {
        "kind": "schema_output",
        "diagnostics": [
            {
                "kind": "schema_output",
                "attempt": 2,
                "field_paths": ["title"],
            }
        ],
    }
    assert "REJECTED_PROVIDER_MARKER_7E5C" not in details.model_dump_json()


def test_safe_schema_projection_preserves_a_new_typed_category() -> None:
    diagnostic = StructuredValidationDiagnostic(
        attempt=2,
        field_paths=("scores.completeness",),
        category="numeric_bounds",
    )
    error = StructuredOutputError("safe schema failure", diagnostics=(diagnostic,))

    details = _taxonomy_module().safe_failure_details(error)

    assert isinstance(details, models_module.SchemaFailureDetails)
    assert details.diagnostics[0].category == "numeric_bounds"
    assert details.model_dump(mode="json")["diagnostics"] == [
        {
            "kind": "schema_output",
            "attempt": 2,
            "field_paths": ["scores.completeness"],
            "category": "numeric_bounds",
        }
    ]
