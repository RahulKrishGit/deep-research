"""Top-level research orchestration stub."""

from typing import Any


def run_research(question: str, session_id: str | None = None) -> dict[str, Any]:
    """Execute a full research cycle.

    This is the public interface for all front-ends (CLI, API, UI).
    Later phases wire in the LangGraph orchestration here.

    Args:
        question: The research question.
        session_id: Optional stable session identifier.

    Returns:
        A dict with keys: report, session_id, sources, trace_url.

    Raises:
        NotImplementedError: Always — this is a placeholder.
    """
    raise NotImplementedError("Research execution not yet implemented")
