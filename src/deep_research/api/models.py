"""Strict request, session, trace, and error models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from deep_research.utils.config import ConfigSettings, apply_config_overrides
from deep_research.utils.types import ResearchError

SessionStatus = Literal[
    "running",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]


class ApiModel(BaseModel):
    """Base validation behavior shared by every API model.

    ``extra="forbid"`` keeps a misspelled field from being silently ignored,
    ``str_strip_whitespace`` normalizes request text before length checks,
    and ``validate_default`` runs defaults through the same validation as
    explicit input.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class ResearchRequest(ApiModel):
    """One validated request to start a research session."""

    query: str = Field(min_length=1)
    max_iterations: int | None = Field(default=None, ge=1)
    output_format: Literal["markdown"] = "markdown"
    config_overrides: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("config_overrides")
    @classmethod
    def validate_config_overrides(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Reject unknown or invalid override paths before a session starts.

        Validating against the default settings rejects paths the
        configuration never had without touching a file or a secret.
        """
        apply_config_overrides(ConfigSettings(), value)
        return value


class ResearchSessionResponse(ApiModel):
    """The immutable snapshot of one session the API returns."""

    session_id: str = Field(min_length=1)
    status: SessionStatus
    current_agent: str | None = None
    iteration: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None = None
    report_path: str | None = None
    trace_url: str | None = None
    errors: list[ResearchError] = Field(default_factory=list)


class TraceMetadata(ApiModel):
    """Session and route facts one trace response carries."""

    session_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    status: SessionStatus


class TraceResponse(ApiModel):
    """What one session's trace endpoint returns."""

    session_id: str = Field(min_length=1)
    trace_url: str | None = None
    metadata: TraceMetadata


class ValidationIssue(ApiModel):
    """One request-validation finding: location and enumerated type only.

    Rejected input values are deliberately absent: a client that sent a
    secret in a rejected field must not get it echoed back.
    """

    location: str = Field(min_length=1)
    type: str = Field(min_length=1)


class ApiErrorBody(ApiModel):
    """The structured error payload every API error response carries."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    reason: str | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class ApiErrorResponse(ApiModel):
    """The envelope wrapping one ``ApiErrorBody``."""

    error: ApiErrorBody
