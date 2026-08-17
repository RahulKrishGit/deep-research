"""The command line front-end for ``python -m deep_research.evaluation``.

Stdlib ``argparse`` and ``print``, mirroring ``deep_research.cli``'s style:
parse arguments, call a runner, render what came back, and choose an exit
code. The one addition over the production CLI is the exit-code table this
harness's spec requires (usage vs. infrastructure vs. failed-but-completed),
so exceptions raised by ``preflight``/``run_agent_evaluation`` are mapped
explicitly instead of being flattened into a single failure code.

``runner``/``lister`` are injected keyword arguments so every test here
drives a fake and no test opens a network connection. Their real defaults
(``_default_agent_runner``/``_default_suite_runner``/``render_listing``) do
the full production wiring -- loading config, resolving the runtime
configuration, running preflight, and (for ``agent``) calling
``run_agent_evaluation`` -- but neither default path is exercised by any
test in this module: every ``agent`` test injects a fake ``runner``, and
every ``suite`` test is an argparse-level rejection that never reaches a
runner call at all.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO

from deep_research.evaluation.cases import (
    CaseRegistryError,
    UnknownCaseError,
    case_by_id,
)
from deep_research.evaluation.config import (
    SecretLeakError,
    build_runtime_config,
    judge_llm_config,
    known_secret_values,
    resolve_git_metadata,
    target_llm_config,
)
from deep_research.evaluation.datasets import DatasetSyncError
from deep_research.evaluation.dependencies import (
    build_controlled_dependencies,
    build_live_dependencies,
)
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    EvaluationTier,
    ExperimentResult,
    UnknownAgentError,
    UnknownTierError,
    parse_agent_name,
    parse_tier,
)
from deep_research.evaluation.reporting import (
    render_experiment,
    render_listing,
    render_suite,
)
from deep_research.evaluation.runner import (
    EXPERIMENT_EXIT_CODES,
    PreflightError,
    preflight,
    preflight_exit_code,
    run_agent_evaluation,
)
from deep_research.observability import Tracker
from deep_research.providers import OpenAIChatProvider
from deep_research.utils.config import load_config

PROGRAM_NAME = "python -m deep_research.evaluation"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INFRASTRUCTURE = 3
EXIT_INTERRUPTED = 130

_REASONING_EFFORT_CHOICES = ("none", "low", "medium", "high", "xhigh", "max")

_DESCRIPTION = (
    "Run the individual-agent evaluation harness: controlled or live "
    "experiments against LangSmith, scored by deterministic gates and a "
    "judge model."
)

_EPILOG = (
    "examples:\n"
    "  # List agents, tiers, cases, repetitions, and dataset names.\n"
    f"  {PROGRAM_NAME} list\n"
    "\n"
    "  # Run all three controlled cases for one agent, three times each.\n"
    f"  {PROGRAM_NAME} agent researcher\n"
    "\n"
    "  # Run one controlled case, still with three repetitions.\n"
    f"  {PROGRAM_NAME} agent researcher --case conflicting-evidence\n"
    "\n"
    "  # Run the selected agent's single live case once.\n"
    f"  {PROGRAM_NAME} agent researcher --tier live\n"
    "\n"
    "  # Compare one agent at a different effort without editing the "
    "baseline config.\n"
    f"  {PROGRAM_NAME} agent researcher --reasoning-effort medium\n"
    "\n"
    "  # Run controlled experiments for all six agents.\n"
    f"  {PROGRAM_NAME} suite"
)


@dataclass(frozen=True, slots=True)
class CliOptions:
    """One parsed command line, canonicalized but not yet validated against
    the case registry (``case_by_id`` failures depend on both ``agent_name``
    and ``tier`` together, so that check happens in ``main``, not here).
    """

    command: str
    agent_name: AgentName | None
    tier: EvaluationTier
    case_id: str | None
    config: str
    output_directory: str | None
    experiment_prefix: str | None
    reasoning_effort: str | None
    judge_reasoning_effort: str | None
    verbose: bool


def _add_shared_options(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--config",
        default="config.yaml",
        help="path to the YAML config file (default: config.yaml)",
    )
    subparser.add_argument(
        "--tier",
        default="controlled",
        help="controlled or live (default: controlled)",
    )
    subparser.add_argument(
        "--output-directory",
        dest="output_directory",
        default=None,
        help="override the evaluation artifact root",
    )
    subparser.add_argument(
        "--experiment-prefix",
        dest="experiment_prefix",
        default=None,
        help="optional human-readable experiment name prefix",
    )
    subparser.add_argument(
        "--judge-reasoning-effort",
        dest="judge_reasoning_effort",
        choices=_REASONING_EFFORT_CHOICES,
        default=None,
        help="judge model reasoning effort override",
    )
    subparser.add_argument(
        "--verbose",
        action="store_true",
        help="print per-repetition gate and evaluator summaries",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, including its usage examples.

    ``--reasoning-effort`` and ``--case`` are added only to the ``agent``
    subparser: a user typing them on ``suite`` gets argparse's own
    unrecognized-argument error and exit 2 -- that is the enforcement, not
    a runtime check. Agent name and tier are plain strings here (no
    ``choices=``) because their real validation
    (``parse_agent_name``/``parse_tier``) also canonicalizes kebab-case
    input and needs to produce a message argparse's own generic "invalid
    choice" text cannot: it must list valid values in the harness's own
    phrasing and, for agent names, accept both kebab- and snake-case.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="list agents, tiers, cases, and dataset names"
    )
    _add_shared_options(list_parser)

    agent_parser = subparsers.add_parser(
        "agent", help="run one agent's controlled or live evaluation"
    )
    agent_parser.add_argument(
        "agent_name",
        help="the agent to evaluate (e.g. researcher, source-evaluator)",
    )
    agent_parser.add_argument(
        "--case",
        dest="case_id",
        default=None,
        help="run only this case id",
    )
    agent_parser.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        choices=_REASONING_EFFORT_CHOICES,
        default=None,
        help="target-agent reasoning effort override",
    )
    _add_shared_options(agent_parser)

    suite_parser = subparsers.add_parser(
        "suite", help="run controlled experiments for all six agents"
    )
    _add_shared_options(suite_parser)

    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> CliOptions:
    """Parse one command line, or raise for argparse's own usage errors.

    ``parse_agent_name``/``parse_tier`` run here, after argparse succeeds,
    so their exceptions (``UnknownAgentError``/``UnknownTierError``) carry
    the harness's own descriptive message rather than argparse's generic
    "invalid choice" text; ``main`` is responsible for catching them.
    """
    parser = build_parser()
    namespace = parser.parse_args(argv)

    tier = parse_tier(namespace.tier)

    agent_name: AgentName | None = None
    case_id: str | None = None
    reasoning_effort: str | None = None
    if namespace.command == "agent":
        agent_name = parse_agent_name(namespace.agent_name)
        case_id = namespace.case_id
        reasoning_effort = namespace.reasoning_effort

    return CliOptions(
        command=namespace.command,
        agent_name=agent_name,
        tier=tier,
        case_id=case_id,
        config=namespace.config,
        output_directory=namespace.output_directory,
        experiment_prefix=namespace.experiment_prefix,
        reasoning_effort=reasoning_effort,
        judge_reasoning_effort=namespace.judge_reasoning_effort,
        verbose=bool(namespace.verbose),
    )


# --- the real production runners --------------------------------------------
#
# Neither of the two functions below is exercised by any test in
# ``test_cli.py``: every ``agent`` test injects a fake ``runner``, and every
# ``suite`` test is an argparse-level rejection (``--case``/
# ``--reasoning-effort`` on ``suite``) that never reaches a runner call.
# They exist so ``python -m deep_research.evaluation agent ...`` and
# ``... suite`` do real work when nothing is injected.


async def _run_agent_pipeline(
    settings: Any,
    runtime: Any,
    cases: Sequence[EvaluationCase],
    environ: dict[str, str],
) -> ExperimentResult:
    # Imported lazily: constructing a real OpenAI/LangSmith client is a
    # meaningfully heavier import than anything else this module needs at
    # load time, and every test here injects a fake runner instead of ever
    # reaching this function.
    from langsmith import Client as LangSmithClient
    from openai import AsyncOpenAI

    openai_client = AsyncOpenAI(
        api_key=environ.get("OPENAI_API_KEY") or "sk-not-configured"
    )
    langsmith_client = LangSmithClient()
    tracker = Tracker.from_config(settings.langsmith, environ=environ)
    target_provider = OpenAIChatProvider(
        target_llm_config(runtime, settings.llm), tracker, client=openai_client
    )
    judge_provider = OpenAIChatProvider(
        judge_llm_config(runtime, settings.llm), tracker, client=openai_client
    )
    dependency_factory = (
        build_controlled_dependencies
        if runtime.tier == "controlled"
        else functools.partial(build_live_dependencies, environ=environ)
    )
    root = runtime.output_root

    await preflight(
        settings,
        runtime,
        cases=cases,
        environ=environ,
        langsmith_client=langsmith_client,
        openai_client=openai_client,
        root=root,
    )
    return await run_agent_evaluation(
        settings,
        runtime,
        cases=cases,
        target_provider_factory=lambda: target_provider,
        judge_provider_factory=lambda: judge_provider,
        tracker_factory=lambda: tracker,
        dependency_factory=dependency_factory,
        secrets=known_secret_values(environ),
        root=root,
    )


def _default_agent_runner(
    *,
    agent_name: AgentName,
    tier: EvaluationTier,
    case_id: str | None,
    config: str,
    reasoning_effort: str | None,
    judge_reasoning_effort: str | None,
    output_directory: str | None,
    experiment_prefix: str | None,
    verbose: bool,
) -> ExperimentResult:
    del verbose  # rendering-only; it does not change what is executed
    from deep_research.evaluation.cases import case_by_id, cases_for

    settings = load_config(config)
    runtime = build_runtime_config(
        settings,
        agent_name=agent_name,
        tier=tier,
        case_id=case_id,
        reasoning_effort=reasoning_effort,
        judge_reasoning_effort=judge_reasoning_effort,
        output_directory=output_directory,
        experiment_prefix=experiment_prefix,
        now=datetime.now(timezone.utc),
        git=resolve_git_metadata(),
    )
    cases = (
        [case_by_id(agent_name, tier, case_id)]
        if case_id is not None
        else list(cases_for(agent_name, tier))
    )
    environ = dict(os.environ)
    return asyncio.run(_run_agent_pipeline(settings, runtime, cases, environ))


def _default_suite_runner(
    *,
    config: str,
    judge_reasoning_effort: str | None,
    output_directory: str | None,
    experiment_prefix: str | None,
    verbose: bool,
) -> Any:
    """The real ``suite`` command execution path.

    ``run_suite_evaluation`` is Task 26's deliverable ("The suite
    command") and does not exist in ``runner.py`` yet -- Task 26 comes
    after this task in the plan's own numbering. The import below is
    deferred into this function body (never at module import time) so
    ``cli.py`` and ``deep_research.evaluation`` import cleanly today; the
    ``ImportError`` would only surface if something actually invoked the
    ``suite`` command past argument parsing with no ``runner`` injected,
    which no test in ``test_cli.py`` does (every ``suite`` test is an
    argparse-level rejection). Once Task 26 lands ``run_suite_evaluation``,
    this import starts resolving with no further change required here --
    its implementer should just confirm the keyword arguments below still
    match its real signature.
    """
    from deep_research.evaluation.runner import run_suite_evaluation

    return run_suite_evaluation(
        config=config,
        judge_reasoning_effort=judge_reasoning_effort,
        output_directory=output_directory,
        experiment_prefix=experiment_prefix,
        verbose=verbose,
    )


def _dispatch(
    options: CliOptions,
    *,
    runner: Callable[..., Any] | None,
    lister: Callable[..., list[str]] | None,
    out: TextIO,
) -> int:
    def emit(lines: Sequence[str]) -> None:
        for line in lines:
            print(line, file=out)

    if options.command == "list":
        actual_lister = lister if lister is not None else render_listing
        settings = load_config(options.config)
        emit(
            actual_lister(
                dataset_version=settings.evaluation.dataset_version,
                repetitions={
                    "controlled": settings.evaluation.controlled_repetitions,
                    "live": settings.evaluation.live_repetitions,
                },
            )
        )
        return EXIT_OK

    if options.command == "agent":
        if options.case_id is not None:
            # ``case_by_id`` depends on both ``agent_name`` and ``tier``,
            # which argparse cannot express as a single ``choices=`` -- so
            # this check happens here, before any runner (real or fake) is
            # ever called.
            case_by_id(options.agent_name, options.tier, options.case_id)
        actual_runner = runner if runner is not None else _default_agent_runner
        result = actual_runner(
            agent_name=options.agent_name,
            tier=options.tier,
            case_id=options.case_id,
            config=options.config,
            reasoning_effort=options.reasoning_effort,
            judge_reasoning_effort=options.judge_reasoning_effort,
            output_directory=options.output_directory,
            experiment_prefix=options.experiment_prefix,
            verbose=options.verbose,
        )
        emit(render_experiment(result, verbose=options.verbose))
        return EXPERIMENT_EXIT_CODES[result.status]

    # options.command == "suite"
    actual_runner = runner if runner is not None else _default_suite_runner
    result = actual_runner(
        config=options.config,
        judge_reasoning_effort=options.judge_reasoning_effort,
        output_directory=options.output_directory,
        experiment_prefix=options.experiment_prefix,
        verbose=options.verbose,
    )
    emit(render_suite(result, verbose=options.verbose))
    return EXPERIMENT_EXIT_CODES[result.status]


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., Any] | None = None,
    lister: Callable[..., list[str]] | None = None,
    stream: TextIO | None = None,
) -> int:
    """Run one command and return its exit code.

    Exit codes: 0 automated pass, 1 completed but failed gates or scores,
    2 invalid usage / unknown agent, tier, or case / invalid local case
    registry, 3 configuration / credential / dataset-sync / LangSmith
    infrastructure failure, 130 interrupted. Any other exception
    propagates: an unhandled exception is a defect, not an evaluation
    outcome -- the same rule ``src/deep_research/cli.py`` documents.

    ``sys.stderr`` is redirected to the resolved output stream for the
    duration of this call so argparse's own usage errors (unrecognized
    arguments, invalid ``choices=`` values) land in the same captured
    stream as everything else this function prints, instead of the real
    process stderr.
    """
    out = stream if stream is not None else sys.stdout
    previous_stderr = sys.stderr
    sys.stderr = out
    try:
        options = parse_arguments(argv)
        return _dispatch(options, runner=runner, lister=lister, out=out)
    except SystemExit as error:
        code = error.code
        return code if isinstance(code, int) else EXIT_USAGE
    except (
        UnknownAgentError,
        UnknownTierError,
        UnknownCaseError,
        CaseRegistryError,
    ) as error:
        print(f"error: {error}", file=out)
        return EXIT_USAGE
    except PreflightError as error:
        print(f"error: {error}", file=out)
        return preflight_exit_code(error.reason)
    except (DatasetSyncError, SecretLeakError) as error:
        print(f"error: {error}", file=out)
        return EXIT_INFRASTRUCTURE
    except KeyboardInterrupt:
        print("The evaluation was cancelled.", file=out)
        return EXIT_INTERRUPTED
    finally:
        sys.stderr = previous_stderr
