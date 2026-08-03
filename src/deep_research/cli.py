"""The command line front-end for ``python -m deep_research``.

Stdlib ``argparse`` and ``print``: the design's Non-Goals rule out a rich
terminal dependency, and nothing here needs one. Everything this module
does is parse arguments, call ``run_research``, render what came back, and
choose an exit code.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.main import DEFAULT_CONFIG_PATH, SUPPORTED_OUTPUT_FORMATS

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
