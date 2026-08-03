"""Graph runtime exceptions and enumerated graph-level error records.

The graph mirror of ``agents.errors``. Every error type the graph can
record is enumerated in ``GRAPH_ERROR_REASONS``, and the subset that stops
a run is ``state.HALTING_ERROR_TYPES``. A node halts only for one of those,
never merely because an error carried ``recoverable=False`` — agents
already record non-recoverable provider failures a research pass is
expected to survive.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from deep_research.graph.state import GRAPH_SOURCE, HALTING_ERROR_TYPES
from deep_research.utils.types import ResearchError

# Enumerated, project-generated failure messages. Never provider text and
# never str(exception): these reach ResearchState.errors, which the CLI,
# the API stream, and the UI all render.
GRAPH_ERROR_REASONS = {
    "graph_agent_configuration_error": (
        "An agent node was assembled incorrectly, so the research run "
        "stopped."
    ),
    "graph_planning_failed": (
        "The planner could not produce a research plan, so the research run "
        "stopped."
    ),
    "graph_provider_configuration_error": (
        "The model provider is not configured, so the research run stopped."
    ),
    "graph_invalid_agent_state": (
        "An agent returned a state update the research state rejected, so "
        "the research run stopped."
    ),
    "graph_invalid_route": (
        "The graph attempted a refinement pass with no budget left, so the "
        "research run stopped."
    ),
}


class GraphError(Exception):
    """Base class for research graph failures."""


class GraphConfigurationError(GraphError):
    """The graph was assembled incorrectly. Not recoverable at runtime."""


class GraphResumeError(GraphError):
    """A session could not be resumed from a checkpoint."""


def graph_error(
    *,
    error_type: str,
    node: str | None = None,
    details: Mapping[str, JsonValue] | None = None,
) -> ResearchError:
    """Build one enumerated graph-level error record.

    The message and the recoverability both come from the enumeration: an
    error the graph never named cannot be recorded, and a halting type is
    never recorded as recoverable. ``details`` must never contain
    ``str(exception)`` — record ``exception_type`` instead.
    """
    reason = GRAPH_ERROR_REASONS.get(error_type)
    if reason is None:
        raise ValueError(f"unknown graph error type: {error_type}")
    node = node.strip() if node else None
    source = GRAPH_SOURCE if not node else f"{GRAPH_SOURCE}.{node}"
    return ResearchError(
        error_type=error_type,
        source=source,
        message=reason,
        recoverable=error_type not in HALTING_ERROR_TYPES,
        details=dict(details or {}),
    )


def _from_exception(
    error_type: str,
    error: Exception,
    *,
    node: str,
) -> ResearchError:
    return graph_error(
        error_type=error_type,
        node=node,
        details={"exception_type": type(error).__name__},
    )


def agent_configuration_error(error: Exception, *, node: str) -> ResearchError:
    """Record that an agent was assembled incorrectly."""
    return _from_exception("graph_agent_configuration_error", error, node=node)


def planning_failed_error(error: Exception, *, node: str) -> ResearchError:
    """Record that the planner could not produce a plan."""
    return _from_exception("graph_planning_failed", error, node=node)


def provider_configuration_error(
    error: Exception,
    *,
    node: str,
) -> ResearchError:
    """Record that the model provider is not configured."""
    return _from_exception(
        "graph_provider_configuration_error", error, node=node
    )


def invalid_agent_state_error(error: Exception, *, node: str) -> ResearchError:
    """Record that an agent's state update was rejected by the state model."""
    return _from_exception("graph_invalid_agent_state", error, node=node)


def invalid_route_error(
    *,
    node: str,
    iteration: int,
    max_iterations: int,
) -> ResearchError:
    """Record that a refinement was attempted with no budget left.

    The router already forbids this. The guard exists because "iteration
    bounds prevent infinite loops" is the one property this graph must not
    lose to a future edit, and a second lock on that door costs three lines.
    """
    return graph_error(
        error_type="graph_invalid_route",
        node=node,
        details={"iteration": iteration, "max_iterations": max_iterations},
    )
