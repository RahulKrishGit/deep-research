"""The command line front-end for ``python -m deep_research``.

Stdlib ``argparse`` and ``print``: the design's Non-Goals rule out a rich
terminal dependency, and nothing here needs one. Everything this module
does is parse arguments, call ``run_research``, render what came back, and
choose an exit code.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    SUPPORTED_OUTPUT_FORMATS,
    run_research_sync,
)
from deep_research.runtime.errors import (
    ResearchConfigurationError,
    configuration_error,
)
from deep_research.runtime.outcome import ResearchOutcome

PROGRAM_NAME = "python -m deep_research"

_DESCRIPTION = (
    "Run a multi-agent deep research session and write a Markdown report."
)


@dataclass(frozen=True, slots=True)
class CliOptions:
    """One parsed, validated command line."""

    question: str | None
    interactive: bool
    resume: str | None
    max_iterations: int | None
    output_format: str | None
    config: str
    verbose: bool


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a whole number"
        ) from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, including its usage examples."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=_DESCRIPTION,
        epilog=(
            "examples:\n"
            f'  {PROGRAM_NAME} "What are the security implications of '
            'quantum computing?"\n'
            f'  {PROGRAM_NAME} "AI in healthcare" --max-iterations 5 '
            "--output-format markdown --verbose\n"
            f"  {PROGRAM_NAME} --interactive\n"
            f"  {PROGRAM_NAME} --resume <session_id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="the research question to investigate",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="prompt for the research question instead of passing it",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default=None,
        help=(
            "continue a checkpointed session (only works inside the process "
            "that started it; see README)"
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=_positive_int,
        default=None,
        help="macro refinement passes the critic may request",
    )
    parser.add_argument(
        "--output-format",
        default=None,
        help=(
            "report format; supported: "
            f"{', '.join(SUPPORTED_OUTPUT_FORMATS)}"
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"path to the YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print tool calls, token totals, and the full progress log",
    )
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> CliOptions:
    """Parse one command line, or exit 2 with a usage error.

    The three ways to name a session — a positional question,
    ``--interactive``, and ``--resume`` — are checked here rather than
    through a mutually exclusive group, because a positional with
    ``nargs="?"`` cannot join one and still produce a readable message.
    """
    parser = build_parser()
    namespace = parser.parse_args(argv)

    chosen = sum(
        (
            namespace.question is not None,
            bool(namespace.interactive),
            namespace.resume is not None,
        )
    )
    if chosen == 0:
        parser.error(
            "pass a research question, or use --interactive, or --resume"
        )
    if chosen > 1:
        parser.error(
            "a question, --interactive, and --resume are mutually exclusive"
        )

    return CliOptions(
        question=namespace.question,
        interactive=bool(namespace.interactive),
        resume=namespace.resume,
        max_iterations=namespace.max_iterations,
        output_format=namespace.output_format,
        config=namespace.config,
        verbose=bool(namespace.verbose),
    )


# The events a plain run shows: the session's boundaries, which agent is
# running, and every macro routing decision. Enough to see progress without
# reading a log, which is exactly what the design asks for.
PROGRESS_EVENT_TYPES = (
    "graph.session.started",
    "graph.node.started",
    "graph.refinement.started",
    "graph.route.decided",
    "graph.session.completed",
)

# Span lifecycle events the tracker writes for itself. Excluded from even
# the verbose log: there are two per span and they would bury everything.
SPAN_EVENT_PREFIX = "observability.span."

# What a non-failing but non-ideal ending means, in one sentence.
STATUS_NOTES = {
    "max_iterations": (
        "Research completed with limitations: the refinement budget was "
        "exhausted before the critic accepted the report."
    ),
    "incomplete": (
        "Research completed with limitations: the run ended without an "
        "accepted critique."
    ),
    "failed": (
        "The research run stopped on a non-recoverable failure; everything "
        "collected before it survives in the report state."
    ),
}


def render_progress(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the recorded event log as a progress summary.

    A summary rather than a live stream: ``run_research_graph`` invokes the
    graph to completion and returns one result, so the events exist only
    once the run is over. Live streaming belongs to the API's SSE endpoint.
    """
    lines = ["Progress log:"]
    for event in outcome.state.events:
        if event.event_type.startswith(SPAN_EVENT_PREFIX):
            continue
        if not verbose and event.event_type not in PROGRESS_EVENT_TYPES:
            continue
        iteration = event.metadata.get("iteration", 0)
        lines.append(f"  [{iteration}] {event.message}")
    return lines


def render_warnings(outcome: ResearchOutcome) -> list[str]:
    """Render recoverable research errors as warnings.

    These are also disclosed inside the report itself:
    ``agents.synthesizer.limitation_reasons`` records ``errors_recorded``
    whenever state carries any error, and the report's Limitations section
    renders it.
    """
    return [
        f"warning: [{error.error_type}] {error.message}"
        for error in outcome.errors
    ]


def render_summary(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the run's identity, outcome, artifacts, and (verbose) costs."""
    lines = [
        f"Session ID: {outcome.session_id}",
        f"Status: {outcome.status}",
    ]
    note = STATUS_NOTES.get(outcome.status)
    if note is not None:
        lines.append(note)

    if outcome.report_path is None:
        lines.append(
            "Report: not written to disk; the report text is in the session "
            "state only."
        )
    else:
        lines.append(f"Report: {outcome.report_path}")

    if outcome.trace_url is not None:
        lines.append(f"Trace: {outcome.trace_url}")

    if verbose:
        if outcome.tool_calls:
            lines.append("Tool calls:")
            for summary in outcome.tool_calls:
                failures = (
                    f" ({summary.failures} failed)" if summary.failures else ""
                )
                lines.append(
                    f"  {summary.tool_name}: {summary.calls} calls{failures}"
                )
        else:
            lines.append("Tool calls: none recorded")

        usage = outcome.token_usage
        total = usage.total_tokens or 0
        if total:
            lines.append(
                f"Tokens: {total} total ({usage.input_tokens} in / "
                f"{usage.output_tokens} out)"
            )
        else:
            lines.append("Tokens: not available")
    return lines


EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 1
# 2 is argparse's usage error and is never returned from here.
EXIT_GRAPH_FAILED = 3
EXIT_INTERRUPTED = 130

INTERACTIVE_PROMPT = "Research question: "

_STARTING_NOTICE = (
    "Preparing the research run. A full session runs the six agents and "
    "can take several minutes."
)


def resolve_question(
    options: CliOptions,
    *,
    prompt: Callable[[str], str],
) -> str | None:
    """Return the question this invocation researches, or ``None`` to resume.

    Interactive mode asks once and runs once. The design's Testing section
    names a single "interactive input path"; a REPL is not asked for and is
    not built.

    Outer whitespace is stripped and a blank question is a configuration
    failure, matching ``run_research``'s own normalization so a rejected
    command never claims a run started.
    """
    if options.resume is not None:
        return None
    if not options.interactive:
        question = options.question
        if question is not None:
            question = question.strip()
            if not question:
                raise configuration_error(
                    reason="no_question",
                    message="No research question was entered.",
                )
        return question

    try:
        answer = prompt(INTERACTIVE_PROMPT)
    except EOFError as error:
        raise configuration_error(
            reason="no_question",
            message="No research question was entered.",
        ) from error
    answer = answer.strip()
    if not answer:
        raise configuration_error(
            reason="no_question",
            message="No research question was entered.",
        )
    return answer


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., ResearchOutcome] = run_research_sync,
    prompt: Callable[[str], str] = input,
    stream: TextIO | None = None,
) -> int:
    """Run one command and return its exit code.

    Exit codes: 0 the run produced a report, 1 a configuration failure,
    2 a usage error (raised by argparse), 3 the graph failed, 130 the user
    interrupted. Any other exception propagates: an unhandled exception is
    a defect, not a research outcome.
    """
    out = stream if stream is not None else sys.stdout
    options = parse_arguments(argv)

    def emit(lines: Sequence[str]) -> None:
        for line in lines:
            print(line, file=out)

    try:
        question = resolve_question(options, prompt=prompt)
        print(_STARTING_NOTICE, file=out)
        outcome = runner(
            question=question,
            resume_session_id=options.resume,
            config_path=options.config,
            max_iterations=options.max_iterations,
            output_format=options.output_format,
        )
    except ResearchConfigurationError as error:
        print(f"error: {error}", file=out)
        print(f"hint: {error.hint}", file=out)
        return EXIT_CONFIGURATION_ERROR
    except KeyboardInterrupt:
        print("The research run was cancelled.", file=out)
        return EXIT_INTERRUPTED

    emit(render_progress(outcome, verbose=options.verbose))
    emit(render_warnings(outcome))
    emit(render_summary(outcome, verbose=options.verbose))
    return EXIT_GRAPH_FAILED if outcome.failed else EXIT_OK
