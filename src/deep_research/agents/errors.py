"""Agent runtime exceptions and structured error recording."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import JsonValue

from deep_research.providers import ProviderError, provider_failure_snapshot
from deep_research.utils.types import ResearchError


class AgentError(Exception):
    """Base class for agent runtime failures."""


class AgentConfigurationError(AgentError):
    """An agent was assembled incorrectly. Not recoverable at runtime."""


class PlanningError(AgentError):
    """The planner could not produce a valid plan. Fails the session.

    ``problems`` holds strings this project generated during validation, not
    provider text, so they are safe to log and to surface to a user.
    """

    def __init__(
        self,
        message: str,
        *,
        problems: Sequence[str] = (),
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.problems = tuple(problems)
        self.operation = operation


PlanningOperation = Literal["react_decision", "plan_draft", "react_loop"]


def planning_provider_error(operation: PlanningOperation) -> PlanningError:
    """Return static operation context for a provider failure."""
    if operation == "react_decision":
        return PlanningError(
            "The planner could not produce a scoping decision because the "
            "model provider operation failed.",
            problems=("the planner provider failed during a ReAct decision",),
            operation=operation,
        )
    if operation == "plan_draft":
        return PlanningError(
            "The planner could not produce the requested plan draft because "
            "the model provider operation failed.",
            problems=(
                "the planner provider failed while requesting the final plan draft",
            ),
            operation=operation,
        )
    return PlanningError(
        "The planner scoping phase stopped before a decision was available.",
        problems=("the planner scoping phase stopped before a decision",),
        operation=operation,
    )


def agent_error(
    *,
    agent_name: str,
    error_type: str,
    message: str,
    recoverable: bool = True,
    details: Mapping[str, JsonValue] | None = None,
) -> ResearchError:
    """Build one structured error attributed to a named agent.

    ``details`` must never contain ``str(exception)``: these records are
    copied into ``ResearchState.errors`` and provider text can carry keys,
    URLs, and paths. Record ``exception_type`` instead.
    """
    if not agent_name.strip():
        raise ValueError("agent_name must not be blank")
    return ResearchError(
        error_type=error_type,
        source=f"agent.{agent_name.strip()}",
        message=message,
        recoverable=recoverable,
        details=dict(details or {}),
    )


def agent_provider_failure_details(
    operation: str,
    error: ProviderError,
    **extra: JsonValue,
) -> dict[str, JsonValue]:
    """Build safe JSON details for a provider failure caught by an agent."""
    if not operation.strip():
        raise ValueError("operation must not be blank")
    if {"provider_failure", "exception_type"}.intersection(extra):
        raise ValueError("extra contains reserved provider failure detail keys")
    snapshot = provider_failure_snapshot(error)
    return {
        "operation": operation.strip(),
        "provider_failure": snapshot.model_dump(mode="json"),
        **extra,
    }
