"""Agent runtime exceptions and structured error recording."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

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

    def __init__(self, message: str, *, problems: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.problems = tuple(problems)


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
