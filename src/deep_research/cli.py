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

One surface is not a ``ResearchEvent`` stream: the run's request budget
publishes from provider worker threads, several of which are in flight at
once. ``RequestBudgetStream`` serializes those lines and, like the rest of the
diagnostic detail, prints them only under ``--verbose``. The terminal section
in the summary is the same data read once, from the budget's own immutable
snapshots.

Three levels of detail, and each adds its own surface rather than repeating
another: plain output streams progress and prints the summary, ``--verbose``
adds tool-call, budget and token totals, and ``--debug-events`` streams the
complete bounded event record — every recorded event, identified by its
enumerated type and source, with no event metadata rendered.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from pydantic import JsonValue

from deep_research.agents.report import error_reading, is_prior_completion
from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    SUPPORTED_OUTPUT_FORMATS,
    run_research_sync,
)
from deep_research.request_budget import (
    RequestBudgetSnapshot,
    RequestBudgetUpdate,
)
from deep_research.runtime.errors import (
    ResearchConfigurationError,
    configuration_error,
)
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import (
    GAP_MATERIAL_SEVERITIES,
    QUALITY_STATUS_ACCEPTED,
    CritiqueGap,
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
    debug_events: bool = False
    """True when ``--debug-events`` asked for the complete bounded event log.

    A fourth output level beside the summary, the progress stream, and
    ``--verbose``'s totals: the event record itself, one line per recorded
    event, printed with the enumerated type and source that identify it.
    """

    # Request-scoped budget controls. ``None`` means "this run requested
    # nothing", so the value the config file declares stays in force; the CLI
    # never rewrites the file.
    request_deepseek_attempt_ceiling: int | None = None
    request_openai_attempt_ceiling: int | None = None
    request_tavily_attempt_ceiling: int | None = None
    request_stop_fraction: float | None = None


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


def _stop_fraction(value: str) -> float:
    """Parse ``0 < F <= 1``, the interval ``RequestBudgetConfig`` validates.

    The chained comparison is the validation: it also refuses ``nan`` and
    ``inf``, which compare false against both bounds, so a caller cannot slip
    past the config model's own ``gt=0, le=1`` constraint into a validation
    error at startup instead of a usage error here.
    """
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a number"
        ) from error
    if not 0 < parsed <= 1:
        raise argparse.ArgumentTypeError(
            "must be greater than 0 and at most 1"
        )
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
        "--debug-events",
        action="store_true",
        help=(
            "print every recorded event, with its type and source "
            "(a diagnostic stream; the span lifecycle stays out)"
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
    parser.add_argument(
        "--request-deepseek-attempt-ceiling",
        type=_positive_int,
        default=None,
        metavar="N",
        help="DeepSeek transport attempts this run may reserve",
    )
    parser.add_argument(
        "--request-openai-attempt-ceiling",
        type=_positive_int,
        default=None,
        metavar="N",
        help="OpenAI transport attempts this run may reserve",
    )
    parser.add_argument(
        "--request-tavily-attempt-ceiling",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Tavily transport attempts this run may reserve",
    )
    parser.add_argument(
        "--request-stop-fraction",
        type=_stop_fraction,
        default=None,
        metavar="F",
        help=(
            "fraction of each requested ceiling this run may actually spend "
            "(0 < F <= 1); the limit is floor(ceiling * F)"
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
        debug_events=bool(namespace.debug_events),
        request_deepseek_attempt_ceiling=(
            namespace.request_deepseek_attempt_ceiling
        ),
        request_openai_attempt_ceiling=namespace.request_openai_attempt_ceiling,
        request_tavily_attempt_ceiling=namespace.request_tavily_attempt_ceiling,
        request_stop_fraction=namespace.request_stop_fraction,
    )


def request_budget_overrides(
    options: CliOptions,
) -> dict[str, JsonValue] | None:
    """The request-scoped budget overrides this command line asked for.

    A partial nested mapping, applied to the loaded settings — never an edit
    to the YAML file. Only the options actually passed appear, so every
    omitted limit keeps whatever the file declares, and a command line that
    asks for nothing produces no override at all rather than a section that
    resets the file's own values.
    """
    requested: dict[str, JsonValue] = {}
    if options.request_deepseek_attempt_ceiling is not None:
        requested["deepseek_attempt_ceiling"] = (
            options.request_deepseek_attempt_ceiling
        )
    if options.request_openai_attempt_ceiling is not None:
        requested["openai_attempt_ceiling"] = (
            options.request_openai_attempt_ceiling
        )
    if options.request_tavily_attempt_ceiling is not None:
        requested["tavily_attempt_ceiling"] = (
            options.request_tavily_attempt_ceiling
        )
    if options.request_stop_fraction is not None:
        requested["stop_fraction"] = options.request_stop_fraction
    if not requested:
        return None
    return {"request_budget": requested}


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


def is_streamed_event(
    event_type: str,
    *,
    verbose: bool,
    debug: bool = False,
) -> bool:
    """True when one event type belongs in the live progress stream.

    Type-gated on purpose. Verbose adds agent completion records and the
    enumerated failure records above, and *only* those: a report body, provider
    text, or evidence excerpt is never streamed, because none of them is an
    event and no other event type is ever rendered.

    ``debug`` is the complete record: every event type is streamed, including
    the ones neither plain output nor verbose shows. Two exclusions remain,
    and both are structural rather than editorial — the tracker's own span
    lifecycle (two records per span, which would bury the log), and the
    request-budget updates, which are a separate channel on their own switch.
    """
    if event_type.startswith(SPAN_EVENT_PREFIX):
        return False
    if debug:
        return True
    if event_type in PROGRESS_EVENT_TYPES:
        return True
    if not verbose:
        return False
    return (
        event_type.endswith(COMPLETION_EVENT_SUFFIX)
        or event_type in VERBOSE_EVENT_TYPES
    )


def render_progress(
    event: ResearchEvent,
    *,
    verbose: bool,
    debug: bool = False,
) -> str | None:
    """The one line ``event`` streams, or ``None`` when the stream skips it.

    Called the moment the graph records the event, so a run that takes minutes
    shows progress while it runs. The final summary is a different surface and
    never reprints these records; every event therefore appears exactly once.

    The debug form prefixes the enumerated ``event_type`` and ``source`` so a
    record is identifiable rather than merely readable. Only those bounded
    fields are added: the event's ``metadata`` is never rendered, so no URL,
    query, or provider value can reach the terminal through this surface.
    """
    if not is_streamed_event(event.event_type, verbose=verbose, debug=debug):
        return None
    iteration = event.metadata.get("iteration", 0)
    if debug:
        return (
            f"  [{iteration}] {event.event_type} ({event.source}): "
            f"{event.message}"
        )
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

    def __init__(
        self,
        stream: TextIO,
        *,
        verbose: bool,
        debug: bool = False,
    ) -> None:
        self._stream = stream
        self._verbose = verbose
        self._debug = debug

    def __call__(self, event: ResearchEvent) -> None:
        line = render_progress(
            event, verbose=self._verbose, debug=self._debug
        )
        if line is not None:
            print(line, file=self._stream, flush=True)


def _attempts_phrase(count: int) -> str:
    """One count, as a reader would write it: ``1 attempt``, ``2 attempts``."""
    return f"{count} attempt" + ("" if count == 1 else "s")


def _ceiling_phrase(snapshot: RequestBudgetSnapshot) -> str:
    """The declared bound, or an explicit statement that none was declared.

    An absent ceiling is printed as absent. Printing it as ``0`` would claim
    the run refused every attempt, which is the opposite of what it did.
    """
    if snapshot.ceiling is None:
        return "ceiling not set"
    return (
        f"ceiling {snapshot.ceiling}, "
        f"effective limit {snapshot.effective_limit}"
    )


# The enumerated update kinds, rendered as the half-sentence each one is.
# Nothing here interpolates the refusal's error message: the machine-readable
# reason is the kind itself.
BUDGET_UPDATE_LABELS = {
    "attempt_reserved": "attempt reserved",
    "tokens_reported": "tokens reported post-response",
    "attempt_blocked": "attempt refused at the declared ceiling",
}


def render_request_budget_update(
    update: RequestBudgetUpdate, *, verbose: bool
) -> str | None:
    """The one line one budget update streams, or ``None`` when it is skipped.

    Verbose-only, like every other diagnostic line: a plain run shows progress
    and the summary, and the budget section of that summary is verbose detail
    too. The line carries the provider category, the enumerated update kind,
    and bounded integers — the snapshot's own fields — so no provider text, no
    URL, no query, and no error message can reach the terminal here.
    """
    if not verbose:
        return None
    snapshot = update.snapshot
    label = BUDGET_UPDATE_LABELS[update.kind]
    return (
        f"  request budget: {snapshot.provider} {label} "
        f"({_attempts_phrase(snapshot.attempts)}, "
        f"{_ceiling_phrase(snapshot)})"
    )


class RequestBudgetStream:
    """Print each request-budget update as the provider thread produces it.

    The callable ``RequestBudget.set_observer`` accepts. It is invoked from
    worker threads — several provider calls are genuinely in flight at once —
    so the lock is what keeps one line whole instead of two interleaved ones.
    It is held across the write alone and never around a provider call, so it
    cannot serialize the research itself.

    Verbose-only and flushed for the same reason ``ProgressStream`` is: a
    redirected stream is block-buffered, and a diagnostic that appears only at
    process exit is not a diagnostic.
    """

    def __init__(self, stream: TextIO, *, verbose: bool) -> None:
        self._stream = stream
        self._verbose = verbose
        self._lock = threading.Lock()

    def __call__(self, update: RequestBudgetUpdate) -> None:
        line = render_request_budget_update(update, verbose=self._verbose)
        if line is None:
            return
        with self._lock:
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
    """One recorded error as its enumerated type and its reading.

    Both are project-owned: ``agents.errors`` and ``graph.errors`` build every
    record from an enumeration, and neither ever records ``str(exception)`` or
    provider text, so these lines are safe to print. The reading is
    ``agents.report.error_reading``: a skip record has one message for four
    reasons, and the reason is what says whether the topic was never
    attempted, deferred, or already answered by an earlier pass.
    """
    return f"warning: [{error.error_type}] {error_reading(error)}"


# The typed detail keys a producer records a *cause* under. Read in this order,
# because ``reason`` is the enumerated "why" a producer stamps and the other
# two name the failure class when a producer has no reason to give.
_CAUSE_DETAIL_KEYS = ("reason", "cause", "failure_type", "exception_type")


def _error_cause(error: ResearchError) -> str:
    """The recorded cause of one error, or ``""`` when none was recorded.

    Only a project-stamped detail key counts. A message is never parsed for a
    cause: "the search provider timed out" is prose, and reading it as the
    cause of a failure is how a display reason becomes a causal claim.
    """
    for key in _CAUSE_DETAIL_KEYS:
        value = error.details.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key} {value.strip()}"
    return ""


def _topic_titles(outcome: ResearchOutcome) -> dict[str, str]:
    """The plan's own title for each coverage topic id.

    This is what lets an unresolved access problem name the *question* it
    leaves unanswered instead of only the id it was filed under.
    """
    return {
        topic.coverage_id: topic.title
        for topic in outcome.state.sub_topics
        if topic.coverage_id and topic.title
    }


def _error_targets(
    errors: Sequence[ResearchError], *, titles: dict[str, str]
) -> list[str]:
    """The affected coverage topics and target ids a group's records name.

    Read from the typed ``coverage_id`` / ``target_ids`` details a producer
    stamps, never inferred from a message. A named topic carries its plan title
    beside its id, so the line says which question lost out.
    """
    tokens: list[str] = []
    for error in errors:
        coverage_id = error.details.get("coverage_id")
        if isinstance(coverage_id, str) and coverage_id.strip():
            title = titles.get(coverage_id)
            token = f'{coverage_id} "{title}"' if title else coverage_id
            if token not in tokens:
                tokens.append(token)
        named = error.details.get("target_ids")
        if isinstance(named, list):
            for item in named:
                if isinstance(item, str) and item.strip() and item not in tokens:
                    tokens.append(item)
    return tokens


# The typed details a producer stamps when it recorded *how* a failure was
# resolved — a validated cache entry that answered a refused read, say.
# Presence of a non-empty one is the resolution, and the only thing that earns
# the word "recovered": ``recoverable`` means something else entirely, so it
# can never be read as one.
_RESOLUTION_DETAIL_KEYS = ("resolved_by", "recovered_by")


def _recorded_resolution(error: ResearchError) -> bool:
    """True when some producer recorded that this failure was resolved."""
    for key in _RESOLUTION_DETAIL_KEYS:
        value = error.details.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _recovery_phrase(errors: Sequence[ResearchError]) -> str:
    """How a group's records ended: recovered, non-fatal, or fatal.

    "Recovered" is earned by a recorded resolution and nothing else: a
    producer stamps one only when something actually answered the failure.
    ``recoverable`` is not that. It is the producer's own field meaning *the
    run continued past this* — a provider-failed terminal review and a failed
    terminal write both carry it, and neither was resolved by anyone — so it
    is rendered here in the ledger's own vocabulary, ``non-fatal`` for
    ``recoverable`` and ``fatal`` for its absence. The two surfaces then say
    the same thing about the same record instead of the CLI claiming a
    recovery the ledger does not.
    """
    total = len(errors)
    resolved = sum(_recorded_resolution(error) for error in errors)
    if resolved == total:
        return "recovered"
    unresolved = [error for error in errors if not _recorded_resolution(error)]
    fatal = sum(not error.recoverable for error in unresolved)
    parts: list[str] = []
    if resolved:
        parts.append(f"recovered {resolved} of {total}")
    if fatal == len(unresolved):
        parts.append("fatal")
    elif fatal:
        parts.append(f"fatal {fatal} of {len(unresolved)}")
    else:
        parts.append("non-fatal")
    return "; ".join(parts)


def _error_breakdown(
    errors: Sequence[ResearchError], *, titles: dict[str, str]
) -> list[str]:
    """One line per (agent, type, cause) an error source actually recorded.

    The source line above this says *who* lost out; these say *what* went
    wrong, how often, how it ended, and which obligation it left open. Repeated
    identical failures collapse into one line with a count, which is the
    aggregation a reader needs — the alternative is one line per occurrence,
    which is a log rather than a summary.
    """
    groups: dict[tuple[str, str, str], list[ResearchError]] = {}
    for error in errors:
        key = (error.source, error.error_type, _error_cause(error))
        groups.setdefault(key, []).append(error)

    lines: list[str] = []
    for (_, error_type, cause), rows in groups.items():
        count = f"x{len(rows)}" if len(rows) > 1 else ""
        parts = [part for part in (count, _recovery_phrase(rows), cause) if part]
        targets = _error_targets(rows, titles=titles)
        detail = f": {', '.join(targets)}" if targets else ""
        lines.append(f"    {error_type} ({'; '.join(parts)}){detail}")
    return lines


def render_warnings(
    outcome: ResearchOutcome, *, verbose: bool = False
) -> list[str]:
    """Render repeated errors grouped by agent, type, and cause.

    The header counts the same three readings each group line reports:
    resolutions that were recorded, failures the run continued past, and
    failures no producer recorded as recoverable. Counting ``recoverable`` as
    "recovered" — as this header once did — publishes a recovery nobody made,
    so a record earns the first count only from a recorded resolution. Each
    source then gets a line, and each (type, cause) inside it gets a line that
    keeps the affected coverage topics — with the plan's own title for each,
    so an access problem that ended a pass names the question it leaves
    unanswered.

    A sub-topic skip that cost the pass nothing is not one of these errors: a
    topic whose required targets are already answered was researched on an
    earlier pass, so it is reported on its own line rather than counted as a
    coverage loss (ruling 5).

    The messages are verbose detail: they are enumerated and safe, but a
    concrete cause with its targets is what makes the run actionable.
    """
    if not outcome.errors:
        return []
    all_errors = list(outcome.errors)
    errors = [error for error in all_errors if not is_prior_completion(error)]
    satisfied = [error for error in all_errors if is_prior_completion(error)]
    titles = _topic_titles(outcome)
    lines = _warning_lines(errors, titles=titles, verbose=verbose)
    lines.extend(_skip_lines(satisfied, titles=titles, verbose=verbose))
    return lines


def _warning_lines(
    errors: Sequence[ResearchError],
    *,
    titles: dict[str, str],
    verbose: bool,
) -> list[str]:
    """The header and one block per source for the errors that lost coverage."""
    if not errors:
        return []
    resolved = sum(_recorded_resolution(error) for error in errors)
    fatal = sum(
        not _recorded_resolution(error) and not error.recoverable
        for error in errors
    )
    counts = f"{len(errors)} error" + ("" if len(errors) == 1 else "s")
    lines = [
        f"Warnings: {counts} "
        f"({resolved} recovered, {len(errors) - resolved - fatal} non-fatal, "
        f"{fatal} fatal)"
    ]
    for source, rows in _errors_by_source(errors).items():
        coverage = _named_coverage_ids(rows)
        detail = f" (coverage {', '.join(coverage)})" if coverage else ""
        lines.append(f"  {source}: {len(rows)} error"
                     + ("" if len(rows) == 1 else "s") + detail)
        lines.extend(_error_breakdown(rows, titles=titles))
        if verbose:
            lines.extend(f"    {_warning_line(error)}" for error in rows)
    return lines


def _skip_lines(
    errors: Sequence[ResearchError],
    *,
    titles: dict[str, str],
    verbose: bool,
) -> list[str]:
    """The sub-topic skips that cost the pass nothing, apart from the warnings.

    A refinement pass omits a topic whose required targets are already answered
    and which the Critic asked no new searches for: the topic was researched —
    on an earlier pass — and owes nothing now. Counting it as an error under
    its coverage id prints a coverage loss the run does not have, and the
    producer's message for all four skip reasons reads "never researched",
    which is false here. Deferred and never-attempted skips stay warnings;
    these get a line that names the topics and says what happened.
    """
    if not errors:
        return []
    targets = _error_targets(errors, titles=titles)
    detail = f": {', '.join(targets)}" if targets else ""
    count = f"{len(errors)} sub-topic" + ("" if len(errors) == 1 else "s")
    lines = [f"Skipped: {count} that owed nothing this pass{detail}"]
    if verbose:
        lines.extend(
            f"    note: [{error.error_type}] {error_reading(error)}"
            for error in errors
        )
    return lines


def _scored_cited_sources(quality: ReportQualitySnapshot) -> int:
    """How many of the cited sources carry a numeric score.

    ``scored_cited_source_ratio`` is the snapshot's own field and is defined
    as ``scored / cited``, so multiplying recovers the exact count the quality
    pass measured. Nothing is recomputed from the report.
    """
    return round(quality.scored_cited_source_ratio * quality.cited_sources)


def _verdict_lines(outcome: ResearchOutcome) -> list[str]:
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
    return [f"Quality: {outcome.quality_status}{detail}"]


def _evidence_lines(outcome: ResearchOutcome) -> list[str]:
    """The structural integrity counts, and the topics still open.

    Nothing is invented for a run no quality pass judged: with no snapshot
    there are no counts to print, and a row of zeroes would read as a clean
    report rather than as an unjudged one.
    """
    quality = outcome.quality
    if quality is None:
        return []
    lines = [
        f"Evidence: {quality.cited_sources} cited sources; "
        f"{_scored_cited_sources(quality)} scored; "
        f"{quality.verified_claims} verified, "
        f"{quality.contradicted_claims} contradicted",
        f"Integrity: {quality.duplicate_claims} duplicate claims; "
        f"{quality.duplicate_source_rows} duplicate source rows; "
        f"{quality.uncited_settled_points} uncited settled points",
    ]
    if quality.unresolved_topic_ids:
        lines.append(
            f"Open coverage: {', '.join(quality.unresolved_topic_ids)}"
        )
    return lines


def _request_budget_lines(
    snapshots: Sequence[RequestBudgetSnapshot],
) -> list[str]:
    """The three provider categories, then the tokens they reported.

    Read from the budget's own immutable snapshots in its fixed order, so the
    same run always renders the same rows and each provider category is its
    own line rather than one pooled number. The tokens are what providers
    reported *after* their responses: they are counts, not a price, and the
    line says so instead of implying a cost the run never measured.
    """
    lines = ["Request budget:"]
    input_tokens = 0
    output_tokens = 0
    for snapshot in snapshots:
        lines.append(
            f"  {snapshot.provider}: "
            f"{_attempts_phrase(snapshot.attempts)} reserved "
            f"({_ceiling_phrase(snapshot)})"
        )
        input_tokens += snapshot.input_tokens
        output_tokens += snapshot.output_tokens
    total = input_tokens + output_tokens
    counts = (
        f"{total} total ({input_tokens} in / {output_tokens} out)"
        if total
        else "none reported"
    )
    lines.append(
        f"  Tokens (reported post-response, not a cost estimate): {counts}"
    )
    return lines


def _quality_reason_line(quality: ReportQualitySnapshot) -> list[str]:
    """Why this verdict, from the typed records behind it.

    A verdict with no reason is not actionable. The specific reason here is the
    gate's own hard-failure names and the semantic review's own status — both
    enumerated values, never report prose — and the line is printed only when
    there is one, so an accepted run carries no invented qualifier.
    """
    reasons: list[str] = []
    if quality.hard_failures:
        reasons.append(
            f"{len(quality.hard_failures)} gate failure"
            + ("" if len(quality.hard_failures) == 1 else "s")
            + f" ({', '.join(quality.hard_failures)})"
        )
    status = quality.semantic_review_status.strip()
    if status and status != "scored":
        reasons.append(f"semantic review {status}")
    elif not status:
        reasons.append("no semantic review was recorded")
    if not reasons:
        return []
    return [f"Quality reasons: {'; '.join(reasons)}"]


def _coverage_line(outcome: ResearchOutcome) -> list[str]:
    """Substantive topic and target completion, and the critical reading.

    Three readings of one run, kept apart (Section 2.3). The topic count is the
    *substantive* one, not the claimed ratio on the quality line: a topic some
    claim recorded consuming is not an answered obligation, and only the
    stricter number belongs in a line about completion.
    """
    coverage = outcome.coverage
    if coverage is None:
        return []
    critical = (
        f"{coverage.answered_critical_targets}/{coverage.critical_targets} "
        "critical targets answered"
    )
    return [
        f"Coverage: {coverage.covered_topics}/{coverage.planned_topics} topics "
        f"covered (substantive, "
        f"{coverage.substantive_topic_ratio:.0%}); "
        f"{coverage.answered_targets}/{coverage.required_targets} required "
        f"targets answered; {critical}"
    ]


def _claim_lines(outcome: ResearchOutcome) -> list[str]:
    """Checked claims, counted apart from the claims that were corroborated.

    "Sixteen claims were checked" is not "sixteen claims are verified". The
    four readings below are the badges the canonical claims actually recorded,
    and they add up to the number that was checked — so the line can never
    present a check count as a corroboration count.
    """
    counts = outcome.evidence_counts
    if counts is None:
        return []
    return [
        f"Claims: {counts.checked_claims} checked; "
        f"{counts.corroborated} independently corroborated, "
        f"{counts.primary_attributed} primary-source attributed, "
        f"{counts.contested} contested, "
        f"{counts.not_established} not established"
    ]


def _source_lines(outcome: ResearchOutcome) -> list[str]:
    """Assessed versus cited, and reads versus works versus cache reuses.

    Four different quantities on one line, each labelled: every assessed source
    is not every cited one (the last run had ten and eight), a physical read
    call is not a unique validated work, and a cache reuse is not a second read
    of the network.
    """
    counts = outcome.evidence_counts
    if counts is None:
        return []
    return [
        f"Sources: {counts.assessed_sources} assessed, "
        f"{counts.cited_assessed_sources} cited; "
        f"reads {counts.read_records} "
        f"(network {counts.network_reads}, cache reuse {counts.cache_reads}), "
        f"works {counts.unique_works}, "
        f"publishers {counts.publishers}, "
        f"findings {counts.findings}"
    ]


def _review_line(outcome: ResearchOutcome) -> list[str]:
    """The semantic judgement, or the explicit record that there was none.

    A missing judgement is printed as missing. It is never rendered as a score
    of zero, and never left off the summary, because an absent review is the
    reason a run cannot be accepted — a reader who cannot see it cannot see why
    the verdict is ``partial``.
    """
    if outcome.quality is None:
        return []
    status = outcome.semantic_review_status.strip()
    if not status:
        return ["Review: no semantic review was recorded"]
    if status != "scored":
        return [f"Review: {status} (no score was recorded)"]
    score = outcome.semantic_review_score
    fingerprint = outcome.semantic_review_fingerprint.strip()
    detail = f" {score:.2f}" if score is not None else ""
    return [f"Review: scored{detail} (fingerprint {fingerprint or 'unrecorded'})"]


def _unresolved_lines(outcome: ResearchOutcome) -> list[str]:
    """The significant questions the run left open, by kind and scope.

    Read from the Critic's own material defects and the semantic review's, each
    with the coverage topic or statement it affects. A run with nothing open
    prints nothing; a run with something open names it rather than saying
    "limitations remain".
    """
    critique = outcome.state.critique
    critic_defects = (
        [
            gap
            for gap in critique.gaps
            if gap.severity in GAP_MATERIAL_SEVERITIES
        ]
        if critique is not None and critique.review_status == "reviewed"
        else []
    )
    review = outcome.state.report_review
    review_defects = (
        review.material_defects
        if review is not None and review.status == "scored"
        else []
    )
    if not critic_defects and not review_defects:
        return []
    parts: list[str] = []
    if critic_defects:
        parts.append(f"{_defect_phrase(critic_defects)} (critic)")
    if review_defects:
        parts.append(f"{_defect_phrase(review_defects)} (semantic review)")
    return [f"Unresolved: {'; '.join(parts)}"]


def _defect_phrase(gaps: Sequence[CritiqueGap]) -> str:
    """One defect list as its count and its bounded kind/scope pairs."""
    scopes = []
    for gap in gaps:
        scope = gap.coverage_id or (gap.target_ids[0] if gap.target_ids else "")
        scopes.append(f"{gap.kind} {scope}".strip())
    return f"{len(gaps)} defect" + ("" if len(gaps) == 1 else "s") + (
        f" ({', '.join(scopes)})" if scopes else ""
    )


def _elapsed_line(outcome: ResearchOutcome) -> list[str]:
    """The span the run's recorded events cover, as minutes and seconds.

    A sub-second span is reported as such rather than rounded to ``0s``: the
    record really does hold two timestamps, and "0s" is a different claim from
    "less than a second".
    """
    seconds = outcome.duration_seconds
    if seconds is None:
        return []
    if seconds < 1:
        return ["Elapsed: less than a second"]
    whole = int(round(seconds))
    minutes, remainder = divmod(whole, 60)
    rendered = f"{minutes}m {remainder}s" if minutes else f"{remainder}s"
    return [f"Elapsed: {rendered}"]


def _artifact_lines(outcome: ResearchOutcome) -> list[str]:
    """The three artifacts, and a truthful record of an incomplete publication.

    The set is published whole or advertised not at all. The distinction is on
    the lines: a path is printed when the whole set was written, and when a
    write failed the line says the path is *not advertised* — never that the
    file does not exist, because a sibling write may well have succeeded and
    left a file on disk. A run that never attempted a publication keeps the
    older wording, which is true of it.
    """
    failed = outcome.failed_publication_artifacts
    lines: list[str] = []
    if failed:
        lines.append(
            "Publication: incomplete; these writes failed: "
            f"{', '.join(failed)}. No artifact path is advertised until the "
            "whole set is written."
        )
    withheld = "not advertised; the artifact set is published whole or not at all."
    artifacts = (
        (
            "Report",
            outcome.report_path,
            "not written to disk; the report text is in the session state only.",
        ),
        (
            "Evidence ledger",
            outcome.evidence_path,
            "not written to disk; the ledger text is in the session state only.",
        ),
        ("Quality record", outcome.quality_path, withheld),
    )
    for label, path, missing in artifacts:
        if path is not None:
            lines.append(f"{label}: {path}")
        elif failed:
            lines.append(f"{label}: {withheld}")
        else:
            lines.append(f"{label}: {missing}")
    return lines


def render_summary(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the run's identity, verdict, counts, artifacts, and costs.

    The order is deliberate: what the run was, what it decided, how much of the
    question it answered, what the evidence actually supports, what is still
    open, where the artifacts are, and how long it took. The numbers on these
    lines come from the same typed records the artifacts render from, which is
    what keeps the printed summary, the reader report, the ledger and the
    quality JSON in agreement about one run.
    """
    lines = [
        f"Session ID: {outcome.session_id}",
        f"Status: {outcome.status}",
    ]
    note = STATUS_NOTES.get(outcome.status)
    if note is not None:
        lines.append(note)

    lines.extend(_verdict_lines(outcome))
    quality = outcome.quality
    if quality is not None:
        lines.extend(_quality_reason_line(quality))
    lines.extend(_coverage_line(outcome))
    lines.extend(_claim_lines(outcome))
    lines.extend(_source_lines(outcome))
    lines.extend(_review_line(outcome))
    lines.extend(_evidence_lines(outcome))

    lines.extend(_unresolved_lines(outcome))
    lines.extend(_artifact_lines(outcome))

    if outcome.trace_url is not None:
        lines.append(f"Trace: {outcome.trace_url}")

    lines.extend(_elapsed_line(outcome))

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

        if outcome.request_budget_snapshots:
            # The budget reported the tokens, per provider, so the pooled
            # tracker total would print the same tokens a second time.
            lines.extend(
                _request_budget_lines(outcome.request_budget_snapshots)
            )
        else:
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

QUALITY_STATUS_KEY = "quality_status"
"""The one key ``strict_quality_exit`` reads, and the name it reads it under."""


def strict_quality_exit(
    snapshot: Mapping[str, object],
    *,
    require_quality: bool,
) -> int:
    """The exit code a *completed* run's quality verdict earns.

    A pure helper, and deliberately a narrow one: it answers one question —
    did this run's terminal quality status accept the report? — and it is
    called only after the configuration, usage, graph-failure and interrupt
    exits have already been decided. Those stay where they are; nothing here
    can reorder them.

    The verdict is read from the enumerated status alone. Every other field a
    snapshot may carry (hard-failure names, the critic's score, the semantic
    review's status and mean, coverage ratios, claim counts) is a diagnostic,
    and none of them can buy acceptance: a report the terminal gates did not
    accept exits 4 under ``--require-quality`` even when every counter looks
    clean. A snapshot with no status at all is not accepted either — an absent
    judgement is never an acceptance, and a missing key must not read as a
    pass.

    Without ``--require-quality`` a completed run exits 0 whether or not the
    report was accepted. That 0 means "the run finished"; it is explicitly not
    a claim that the report was accepted, which is why the summary prints the
    quality status beside it on every run.
    """
    if not require_quality:
        return EXIT_OK
    if snapshot.get(QUALITY_STATUS_KEY) == QUALITY_STATUS_ACCEPTED:
        return EXIT_OK
    return EXIT_QUALITY_UNACCEPTED

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

    progress = ProgressStream(
        out, verbose=options.verbose, debug=options.debug_events
    )
    budget = RequestBudgetStream(out, verbose=options.verbose)

    try:
        question = resolve_question(options, prompt=prompt)
        print(_STARTING_NOTICE, file=out)
        outcome = runner(
            question=question,
            resume_session_id=options.resume,
            config_path=options.config,
            max_iterations=options.max_iterations,
            output_format=options.output_format,
            config_overrides=request_budget_overrides(options),
            event_handler=progress,
            request_budget_handler=budget,
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
    quality_exit = strict_quality_exit(
        {QUALITY_STATUS_KEY: outcome.quality_status},
        require_quality=options.require_quality,
    )
    if quality_exit == EXIT_QUALITY_UNACCEPTED:
        emit([_QUALITY_UNACCEPTED_NOTICE])
    return quality_exit
