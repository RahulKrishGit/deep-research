"""The one research entry point every front-end calls.

The CLI, the HTTP API, and the UI all go through ``run_research``. It owns
the session lifecycle — load configuration, build a runtime, recall memory,
drive the graph, summarize the result — and nothing else. Everything it
composes lives in ``deep_research.runtime`` and ``deep_research.graph``.

Failures split cleanly in two. Anything that can go wrong before the graph
starts is a ``ResearchConfigurationError`` with an enumerated hint. Once the
graph runs, failure is a *status*, not an exception: the graph records a
halt in state and returns everything it collected. Any other exception is a
defect and is deliberately allowed to propagate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias
from uuid import uuid4

from deep_research.graph.errors import GraphResumeError
from deep_research.graph.orchestrator import (
    resume_research_graph,
    run_research_graph,
)
from deep_research.runtime.assembly import ResearchRuntime, build_runtime
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.runtime.recall import recall_memory_context
from deep_research.utils.config import ConfigSettings, load_config

DEFAULT_CONFIG_PATH = "config.yaml"

# Only Markdown is written in this build. The parent design pins this:
# "Writes Markdown in the first build. HTML and PDF export are out of scope
# until the Markdown path is stable."
SUPPORTED_OUTPUT_FORMATS = ("markdown",)

RuntimeBuilder: TypeAlias = Callable[..., Awaitable[ResearchRuntime]]


def new_session_id() -> str:
    """Return a fresh session identifier for one research run."""
    return uuid4().hex


def resolve_output_format(requested: str | None, *, configured: str) -> str:
    """Return the report format this run will write, or refuse to start."""
    chosen = (requested or configured or "").strip().casefold()
    if chosen not in SUPPORTED_OUTPUT_FORMATS:
        supported = ", ".join(SUPPORTED_OUTPUT_FORMATS)
        raise configuration_error(
            reason="unsupported_output_format",
            message=(
                f"Unsupported output format {chosen or '(blank)'!r}; "
                f"supported formats: {supported}"
            ),
        )
    return chosen


def load_settings(config_path: str) -> ConfigSettings:
    """Load configuration in strict mode, or refuse to start.

    Strict mode is not optional: the parent design requires missing API
    keys to fail fast at startup with a clear configuration error rather
    than surfacing as a provider failure inside a research pass.
    """
    try:
        return load_config(config_path, strict=True)
    except FileNotFoundError as error:
        raise configuration_error(
            reason="config_file_missing", message=str(error)
        ) from error
    except ValueError as error:
        message = str(error)
        reason = (
            "missing_secrets"
            if "environment variables" in message
            else "config_invalid"
        )
        raise configuration_error(reason=reason, message=message) from error


async def run_research(
    question: str | None = None,
    *,
    session_id: str | None = None,
    resume_session_id: str | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
    max_iterations: int | None = None,
    output_format: str | None = None,
    runtime_builder: RuntimeBuilder = build_runtime,
) -> ResearchOutcome:
    """Run one research session, or continue a checkpointed one.

    ``runtime_builder`` is injected rather than imported at the call site so
    a test can drive the real graph with scripted agents and no provider.
    """
    if question is None and resume_session_id is None:
        raise configuration_error(
            reason="no_question",
            message="No research question was supplied.",
        )
    if question is not None and resume_session_id is not None:
        raise configuration_error(
            reason="no_question",
            message=(
                "A research question and a resumed session cannot be "
                "combined; a resumed session already has its question."
            ),
        )

    settings = load_settings(config_path)
    resolve_output_format(
        output_format, configured=settings.output.default_format
    )

    effective_session_id = resume_session_id or session_id or new_session_id()
    runtime = await runtime_builder(
        settings, session_id=effective_session_id
    )

    if resume_session_id is not None:
        try:
            run = await resume_research_graph(
                graph=runtime.graph,
                tracker=runtime.tracker,
                session_id=resume_session_id,
                max_iterations=max_iterations,
            )
        except GraphResumeError as error:
            raise configuration_error(
                reason="no_checkpoint",
                message=(
                    f"Session {resume_session_id} cannot be resumed: {error}"
                ),
            ) from error
    else:
        assert question is not None  # narrowed by the guards above
        memory_context = await recall_memory_context(
            question=question,
            long_term=runtime.long_term,
            procedural=runtime.procedural,
        )
        run = await run_research_graph(
            graph=runtime.graph,
            tracker=runtime.tracker,
            session_id=effective_session_id,
            question=question,
            max_iterations=(
                settings.graph.max_iterations
                if max_iterations is None
                else max_iterations
            ),
            memory_context=memory_context,
        )

    return build_outcome(run, metrics=runtime.tracker.metrics)


def run_research_sync(**kwargs: Any) -> ResearchOutcome:
    """Run one research session from synchronous code.

    Keyword-only so a caller cannot silently pass a question positionally
    into ``asyncio.run``'s argument list.
    """
    return asyncio.run(run_research(**kwargs))
