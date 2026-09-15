"""The command line front-end for ``python -m deep_research``.

Stdlib ``argparse`` and ``print``: the design's Non-Goals rule out a rich
terminal dependency, and nothing here needs one. Everything this module
does is parse arguments, call ``run_research``, render what came back, and
choose an exit code.

Two surfaces, deliberately different. Progress streams *while* the graph
runs — via the ``event_handler`` ``run_research_sync`` already accepts — and
the final summary never reprints a streamed record. Everything the summary
prints is read from typed state or a typed event: the quality block is the
``ReportQualitySnapshot`` the terminal gates judged and the verdict
``graph.state.graph_quality_status`` derived, and the artifact paths are the
finalizer's own publication record. No report prose is parsed anywhere here.

Exit codes (also documented in ``--help``): 0 a finished run — a partial
report included, unless ``--require-quality`` was passed — 1 a configuration
failure, 2 a usage error, 3 a graph failure, 4 a report the terminal quality
gates did not accept while ``--require-quality`` was set, and 130 an
interrupt.
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
from deep_research.utils.types import (
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
)

PROGRAM_NAME = "python -m deep_research"

_DESCRIPTION = (
    "Run a multi-agent deep research session and write a Markdown report."
)

_EXIT_CODE_HELP = (
    "exit codes:\n"
    "  0  the run finished; a partial report is still a successful run\n"
    "  1  configuration error\n"
    "  2  usage error\n"
    "  3  the graph failed\n"
    "  4  --require-quality was set and the report was not accepted\n"
    "  130  interrupted\n"
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
    require_quality: bool


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
            f"{_EXIT_CODE_HELP}"
            "\n"
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
        help=(
            "print tool calls, token totals, each agent's completion "
            "records, and the typed error messages behind the warnings"
        ),
    )
    parser.add_argument(
        "--require-quality",
        action="store_true",
        help=(
            "exit 4 unless the terminal quality gates accepted the report "
            "(by default a partial report still exits 0)"
        ),
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
        require_quality=bool(namespace.require_quality),
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

# Every agent (and the graph itself) names its terminal record this way.
COMPLETION_EVENT_SUFFIX = ".completed"

# The two records ``--verbose`` adds that are not completions: the placeholder
# a halted run emits where a node's completion would go, and the single
# enumerated provider-failure event an agent can emit.
VERBOSE_EVENT_TYPES = (
    "graph.node.skipped",
    "agent.provider_failure",
)

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


def is_streamed_event(event_type: str, *, verbose: bool) -> bool:
    """True when one event type belongs in the live progress stream.

    Type-gated on purpose. Verbose adds agent completion records and the
    enumerated failure records above, and *only* those: a report body, provider
    text, or evidence excerpt is never streamed, because none of them is an
    event and no other event type is ever rendered.
    """
    if event_type.startswith(SPAN_EVENT_PREFIX):
        return False
    if event_type in PROGRESS_EVENT_TYPES:
        return True
    if not verbose:
        return False
    return (
        event_type.endswith(COMPLETION_EVENT_SUFFIX)
        or event_type in VERBOSE_EVENT_TYPES
    )


def render_progress(event: ResearchEvent, *, verbose: bool) -> str | None:
    """The one line ``event`` streams, or ``None`` when the stream skips it.

    Called the moment the graph records the event, so a run that takes minutes
    shows progress while it runs. The final summary is a different surface and
    never reprints these records; every event therefore appears exactly once.
    """
    if not is_streamed_event(event.event_type, verbose=verbose):
        return None
    iteration = event.metadata.get("iteration", 0)
    return f"  [{iteration}] {event.message}"


class ProgressStream:
    """Print every allowed progress event as ``run_research`` produces it.

    The callable ``run_research_sync`` expects as ``event_handler``: it holds
    no state of its own, so the same instance serves the whole run and the
    graph's own de-duplication (each event is delivered once, in order) is the
    only thing deciding what is printed.

    Every line is flushed as it is written. ``main`` hands this the real
    ``sys.stdout``, which is line-buffered only on a TTY: redirected to a file
    or a pipe it buffers 8 KiB, and a whole run emits far less than that, so
    without the flush "live" progress would appear only at process exit —
    exactly the non-interactive case where the user has no other signal the
    run is alive. The summary is written last and flushed at exit, so it does
    not need one here.
    """

    def __init__(self, stream: TextIO, *, verbose: bool) -> None:
        self._stream = stream
        self._verbose = verbose

    def __call__(self, event: ResearchEvent) -> None:
        line = render_progress(event, verbose=self._verbose)
        if line is not None:
            print(line, file=self._stream, flush=True)


def _errors_by_source(
    errors: Sequence[ResearchError],
) -> dict[str, list[ResearchError]]:
    """Group typed errors by the agent or tool that recorded them."""
    groups: dict[str, list[ResearchError]] = {}
    for error in errors:
        groups.setdefault(error.source, []).append(error)
    return groups


def _named_coverage_ids(errors: Sequence[ResearchError]) -> list[str]:
    """The planned coverage topics a group's records name, in first-seen order.

    Read from the typed ``coverage_id`` detail a producer stamps on the record
    (``agents.researcher`` stamps it on every unattempted sub-topic), never
    inferred from a message.
    """
    coverage_ids: list[str] = []
    for error in errors:
        candidate = error.details.get("coverage_id")
        if (
            isinstance(candidate, str)
            and candidate.strip()
            and candidate not in coverage_ids
        ):
            coverage_ids.append(candidate)
    return coverage_ids


def _warning_line(error: ResearchError) -> str:
    """One recorded error as its enumerated type and its recorded reason.

    Both are project-owned: ``agents.errors`` and ``graph.errors`` build every
    record from an enumeration, and neither ever records ``str(exception)`` or
    provider text, so these lines are safe to print.
    """
    return f"warning: [{error.error_type}] {error.message}"


def render_warnings(
    outcome: ResearchOutcome, *, verbose: bool = False
) -> list[str]:
    """Render recoverable errors grouped by the agent that recorded them.

    Plain output is one line per affected source with its error count and the
    coverage topics its records name, so a reader can see *who* contributed
    nothing and *which* planned topic lost out without reading a log. The
    messages themselves are verbose detail: they are enumerated and safe, but
    they are not what makes the run actionable.
    """
    if not outcome.errors:
        return []
    lines = ["Warnings:"]
    for source, errors in _errors_by_source(outcome.errors).items():
        coverage = _named_coverage_ids(errors)
        count = f"{len(errors)} error" + ("" if len(errors) == 1 else "s")
        detail = f" (coverage {', '.join(coverage)})" if coverage else ""
        lines.append(f"  {source}: {count}{detail}")
        if verbose:
            lines.extend(f"    {_warning_line(error)}" for error in errors)
    return lines


def _scored_cited_sources(quality: ReportQualitySnapshot) -> int:
    """How many of the cited sources carry a numeric score.

    ``scored_cited_source_ratio`` is the snapshot's own field and is defined
    as ``scored / cited``, so multiplying recovers the exact count the quality
    pass measured. Nothing is recomputed from the report.
    """
    return round(quality.scored_cited_source_ratio * quality.cited_sources)


def _quality_lines(outcome: ResearchOutcome) -> list[str]:
    """The terminal verdict, and the typed metrics it rests on.

    A fragment is printed only when the state carries it: no model review
    means no critic score, and a run no quality pass judged prints its verdict
    alone rather than a row of invented zeroes.
    """
    parts: list[str] = []
    critique = outcome.state.critique
    if critique is not None:
        parts.append(f"critic {critique.score}/10")
    quality = outcome.quality
    if quality is not None:
        parts.append(
            f"{quality.covered_topics}/{quality.planned_topics} topics "
            f"covered, {quality.coverage_ratio:.0%}"
        )
    detail = f" ({'; '.join(parts)})" if parts else ""
    lines = [f"Quality: {outcome.quality_status}{detail}"]
    if quality is None:
        return lines
    lines.append(
        f"Evidence: {quality.cited_sources} cited sources; "
        f"{_scored_cited_sources(quality)} scored; "
        f"{quality.verified_claims} verified, "
        f"{quality.contradicted_claims} contradicted"
    )
    lines.append(
        f"Integrity: {quality.duplicate_claims} duplicate claims; "
        f"{quality.duplicate_source_rows} duplicate source rows; "
        f"{quality.uncited_settled_points} uncited settled points"
    )
    if quality.unresolved_topic_ids:
        lines.append(
            f"Open coverage: {', '.join(quality.unresolved_topic_ids)}"
        )
    return lines


def render_summary(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the run's identity, quality verdict, artifacts, and costs."""
    lines = [
        f"Session ID: {outcome.session_id}",
        f"Status: {outcome.status}",
    ]
    note = STATUS_NOTES.get(outcome.status)
    if note is not None:
        lines.append(note)

    lines.extend(_quality_lines(outcome))

    if outcome.report_path is None:
        lines.append(
            "Report: not written to disk; the report text is in the session "
            "state only."
        )
    else:
        lines.append(f"Report: {outcome.report_path}")

    if outcome.evidence_path is None:
        lines.append(
            "Evidence ledger: not written to disk; the ledger text is in the "
            "session state only."
        )
    else:
        lines.append(f"Evidence ledger: {outcome.evidence_path}")

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
EXIT_QUALITY_UNACCEPTED = 4
EXIT_INTERRUPTED = 130

INTERACTIVE_PROMPT = "Research question: "

_STARTING_NOTICE = (
    "Preparing the research run. A full session runs the six agents and "
    "can take several minutes."
)

_QUALITY_UNACCEPTED_NOTICE = (
    "error: the report was not accepted by the terminal quality gates; "
    "--require-quality was set, so this run exits 4."
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

    Progress streams through the handler passed to ``runner`` while the run
    happens; the summary printed afterwards reports the outcome and never
    reprints a streamed record.

    Exit codes: 0 the run finished — a partial report is still 0 unless
    ``--require-quality`` was passed; 1 a configuration failure; 2 a usage
    error (raised by argparse); 3 the graph failed; 4 ``--require-quality``
    was set and the terminal quality status was not ``accepted``; 130 the user
    interrupted. Any other exception propagates: an unhandled exception is a
    defect, not a research outcome.
    """
    out = stream if stream is not None else sys.stdout
    options = parse_arguments(argv)

    def emit(lines: Sequence[str]) -> None:
        for line in lines:
            print(line, file=out)

    progress = ProgressStream(out, verbose=options.verbose)

    try:
        question = resolve_question(options, prompt=prompt)
        print(_STARTING_NOTICE, file=out)
        outcome = runner(
            question=question,
            resume_session_id=options.resume,
            config_path=options.config,
            max_iterations=options.max_iterations,
            output_format=options.output_format,
            event_handler=progress,
        )
    except ResearchConfigurationError as error:
        print(f"error: {error}", file=out)
        print(f"hint: {error.hint}", file=out)
        return EXIT_CONFIGURATION_ERROR
    except KeyboardInterrupt:
        print("The research run was cancelled.", file=out)
        return EXIT_INTERRUPTED

    emit(render_warnings(outcome, verbose=options.verbose))
    emit(render_summary(outcome, verbose=options.verbose))
    if outcome.failed:
        return EXIT_GRAPH_FAILED
    if options.require_quality and not outcome.accepted:
        emit([_QUALITY_UNACCEPTED_NOTICE])
        return EXIT_QUALITY_UNACCEPTED
    return EXIT_OK
