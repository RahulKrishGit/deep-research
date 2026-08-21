"""The one failure type the CLI, the API, and the UI all render.

The runtime mirror of ``graph.errors``. Every reason a research run can
refuse to start is enumerated here with the corrective hint that goes with
it, so a front-end never has to invent advice and a message never has to
carry a stack trace.
"""

from __future__ import annotations

# Enumerated, project-generated hints. Never provider text and never
# ``str(exception)`` beyond the message the caller passes deliberately.
CONFIGURATION_HINTS = {
    "config_file_missing": (
        "Pass --config with the path to a config.yaml file."
    ),
    "config_invalid": (
        "Check config.yaml against the settings documented in README.md."
    ),
    "missing_secrets": (
        "Set the selected chat provider's API key (DEEPSEEK_API_KEY by "
        "default) and TAVILY_API_KEY in the environment or in a .env file "
        "next to config.yaml. OPENAI_API_KEY is required only when a "
        "provider or embedding_provider of 'openai' is configured."
    ),
    "provider_unconfigured": (
        "Check the selected provider's model, thinking, and reasoning "
        "settings and set the selected provider's API key in the "
        "environment or in a .env file next to config.yaml."
    ),
    "memory_unavailable": (
        "Check that the memory directory is writable and that chromadb is "
        "installed."
    ),
    "agents_misconfigured": (
        "This is a wiring defect rather than a setup problem; report it "
        "with the command that produced it."
    ),
    "blank_session_id": (
        "Pass a non-blank session id, or omit it to start a fresh session."
    ),
    "unsupported_output_format": (
        "Only markdown is supported in this build."
    ),
    "no_question": (
        "Pass a research question, or use --interactive."
    ),
    "no_checkpoint": (
        "Resume only works inside the process that started the session; "
        "in-memory checkpoints do not survive a new command."
    ),
    "question_and_resume": (
        "A resumed session already has its question; pass either a "
        "question or --resume, not both."
    ),
}


class ResearchConfigurationError(Exception):
    """A research run could not start. Never raised once the graph runs."""

    def __init__(self, message: str, *, reason: str, hint: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.hint = hint


def configuration_error(
    *,
    reason: str,
    message: str,
) -> ResearchConfigurationError:
    """Build one enumerated configuration failure.

    The hint comes from the enumeration, so a failure the project never
    named cannot be raised and no caller can invent its own advice.
    """
    hint = CONFIGURATION_HINTS.get(reason)
    if hint is None:
        raise ValueError(f"unknown configuration reason: {reason}")
    return ResearchConfigurationError(message, reason=reason, hint=hint)
