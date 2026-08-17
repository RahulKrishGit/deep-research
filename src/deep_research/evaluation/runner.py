"""Preflight: fail fast, cheaply, and safely before any experiment starts.

``preflight`` runs nine checks in a fixed order, cheapest and safest first,
so a broken local input never reaches a remote call and a remote call
never reaches a remote write. Nothing here creates a LangSmith experiment;
that is Task 23's job, once ``preflight`` has passed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deep_research.agents.errors import AgentConfigurationError
from deep_research.evaluation.cases import (
    CaseRegistryError,
    UnknownCaseError,
    case_by_id,
    validate_registry,
)
from deep_research.evaluation.config import (
    EvaluationRuntimeConfig,
    known_secret_values,
    resolve_judge_effort,
    resolve_target_effort,
)
from deep_research.evaluation.datasets import DatasetSyncError, synchronize_dataset
from deep_research.evaluation.dependencies import (
    build_controlled_dependencies,
    build_live_dependencies,
    required_credentials,
)
from deep_research.evaluation.factory import evaluation_session_id
from deep_research.evaluation.models import EvaluationCase
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import OpenAIChatProvider
from deep_research.runtime.assembly import build_agent
from deep_research.utils.config import ConfigSettings

PREFLIGHT_REASONS: tuple[str, ...] = (
    "invalid_registry",
    "unknown_case",
    "missing_credentials",
    "model_unavailable",
    "invalid_reasoning_effort",
    "agent_unbuildable",
    "dataset_unavailable",
    "output_root_unwritable",
    "guards_uninstallable",
)

# Reasons that are local-input errors under the CLI exit-code spec (Task 25):
# a broken local registry or an unknown requested case is the caller's
# mistake, not an environment or remote failure, so both map to exit code 2
# instead of the general failure code 3. Every other ``PreflightError``
# reason -- including the dataset-sync pass-through reason
# "secret_in_dataset", which is deliberately not one of ``PREFLIGHT_REASONS``
# -- maps to 3. Exposed as ``preflight_exit_code`` (a lookup function, not
# data carried on ``PreflightError`` itself) so Task 25's CLI wiring has one
# place to call instead of duplicating this table.
_LOCAL_INPUT_REASONS = frozenset({"invalid_registry", "unknown_case"})


def preflight_exit_code(reason: str) -> int:
    """The CLI exit code Task 25 must use for one ``PreflightError.reason``."""
    return 2 if reason in _LOCAL_INPUT_REASONS else 3


class PreflightError(RuntimeError):
    """A preflight check failed before any dataset write, by ``reason``.

    ``reason`` is one of ``PREFLIGHT_REASONS`` for every check this module
    owns; ``synchronize_dataset`` failures pass their own ``DatasetSyncError
    .reason`` straight through (``"dataset_unavailable"``, already listed,
    or ``"secret_in_dataset"``, which is not, since it is not one of this
    module's own nine checks).
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or f"preflight failed: {reason}")
        self.reason = reason


async def verify_model_access(client: Any, model_ids: Sequence[str]) -> None:
    """Request each model id once, in order; never fall back to another.

    Raises ``PreflightError("model_unavailable", ...)`` naming only the
    identifier that failed -- never a substitute that happens to be
    available -- and stops at the first failure, so no later model in
    ``model_ids`` is requested once one has already failed.
    """
    for model_id in model_ids:
        try:
            await client.models.retrieve(model_id)
        except Exception as error:
            raise PreflightError(
                "model_unavailable",
                f"model {model_id!r} is not accessible with the "
                "configured credentials",
            ) from error


def _validate_case_identities(cases: Sequence[EvaluationCase]) -> None:
    """Reject duplicate identities or one case id at conflicting versions.

    Mirrors ``datasets.py``'s private ``_validate_sync_case_identities``:
    the per-agent case counts are a whole-registry property already
    enforced by the bare ``validate_registry()`` call above this, so only
    the two identity rules apply to a preflight run's own (possibly
    one-agent, one-tier) case subset.
    """
    seen: dict[tuple[str, int], int] = {}
    versions: dict[str, set[int]] = {}
    for case in cases:
        seen[case.identity] = seen.get(case.identity, 0) + 1
        versions.setdefault(case.case_id, set()).add(case.version)
    duplicates = sorted(key[0] for key, count in seen.items() if count > 1)
    if duplicates:
        raise CaseRegistryError(
            f"duplicate case identities: {', '.join(duplicates)}"
        )
    conflicting = sorted(
        case_id for case_id, found in versions.items() if len(found) > 1
    )
    if conflicting:
        raise CaseRegistryError(
            f"conflicting versions for case ids: {', '.join(conflicting)}"
        )


async def preflight(
    settings: ConfigSettings,
    runtime: EvaluationRuntimeConfig,
    *,
    cases: Sequence[EvaluationCase],
    environ: Mapping[str, str],
    langsmith_client: Any,
    openai_client: Any,
    root: Path,
) -> None:
    """Run the nine preflight checks in order; raise on the first failure.

    Each check is cheaper and safer than the next: local, in-process
    checks (registry, case lookup, reasoning efforts, credential presence)
    come before any client call, and the one client call that only reads
    (model access) comes before every client call that could write
    (``synchronize_dataset``). Nothing before the last step ever touches a
    remote dataset.
    """
    # 1. The registry as a whole is sound, and this run's own case subset
    # has no duplicate or conflicting-version identities.
    try:
        validate_registry()
        _validate_case_identities(cases)
    except CaseRegistryError as error:
        raise PreflightError("invalid_registry", str(error)) from error

    # 2. A specifically requested case actually exists.
    if runtime.case_id is not None:
        try:
            smoke_case = case_by_id(
                runtime.agent_name, runtime.tier, runtime.case_id
            )
        except UnknownCaseError as error:
            raise PreflightError("unknown_case", str(error)) from error
    else:
        if not cases:
            raise PreflightError(
                "unknown_case", "no cases were supplied to preflight"
            )
        smoke_case = cases[0]

    # 3. Reasoning efforts re-resolve without error against the current
    # settings file -- catches a hand-edited config between the runtime
    # config being built and preflight running.
    try:
        resolve_target_effort(settings.evaluation, runtime.agent_name, override=None)
        resolve_judge_effort(settings.evaluation, override=None)
    except ValueError as error:
        raise PreflightError("invalid_reasoning_effort", str(error)) from error

    # 4. Every credential this run will actually need is present and
    # non-blank. OpenAI and LangSmith are required for every tier (model
    # access below, dataset sync at the end). For a live-tier run,
    # ``required_credentials`` also names ``TAVILY_API_KEY`` for any agent
    # whose declared tools reach it -- a controlled bundle never
    # constructs a real Tavily client (it always injects the scripted
    # search double), so the controlled tier only needs the fixed pair.
    # Checking the live tier's full credential set here, before step 5's
    # real network call, means a missing Tavily key is caught as
    # ``missing_credentials`` up front instead of surfacing later, at
    # step 7, as the less-specific ``guards_uninstallable``.
    required = (
        required_credentials(runtime.agent_name)
        if runtime.tier == "live"
        else ("OPENAI_API_KEY", "LANGSMITH_API_KEY")
    )
    missing = [
        variable
        for variable in required
        if not environ.get(variable, "").strip()
    ]
    if missing:
        raise PreflightError(
            "missing_credentials",
            "missing required credentials: " + ", ".join(missing),
        )

    # 5. Every model this run will call is actually reachable. Never a
    # substitute: a missing model fails preflight by name, full stop.
    model_ids = [runtime.target_model, runtime.judge_model]
    if runtime.tier == "live":
        model_ids.append(runtime.embedding_model)
    await verify_model_access(openai_client, model_ids)

    # 6. The output root can be created and actually written to.
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".preflight-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise PreflightError("output_root_unwritable", str(error)) from error

    # 7. The controlled or live dependency bundle builds once, for one
    # representative case -- a smoke test of the guard wiring itself, kept
    # under a dedicated subdirectory so it never collides with a real
    # repetition's own output paths.
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    smoke_root = root / "_preflight"
    try:
        if runtime.tier == "controlled":
            bundle = build_controlled_dependencies(
                runtime,
                smoke_case,
                tracker=tracker,
                settings=settings,
                root=smoke_root,
            )
        else:
            bundle = build_live_dependencies(
                runtime,
                smoke_case,
                tracker=tracker,
                settings=settings,
                root=smoke_root,
                environ=environ,
            )
    except Exception as error:
        raise PreflightError("guards_uninstallable", str(error)) from error

    # 8. The agent itself builds with those tools -- never deferred to the
    # first real repetition.
    provider = OpenAIChatProvider(settings.llm, tracker, client=openai_client)
    try:
        build_agent(
            runtime.agent_name,
            bundle.settings,
            tracker=tracker,
            provider=provider,
            tools=bundle.tools,
            session_id=evaluation_session_id(
                runtime, case_id=smoke_case.case_id, repetition=1
            ),
            reputation=bundle.reputation,
        )
    except AgentConfigurationError as error:
        raise PreflightError("agent_unbuildable", str(error)) from error

    # 9. The dataset itself is reachable and secret-free. The last check,
    # because it is the only one that can write.
    try:
        synchronize_dataset(
            langsmith_client,
            agent_name=runtime.agent_name,
            tier=runtime.tier,
            dataset_version=runtime.dataset_version,
            rubric_version=runtime.rubric_version,
            cases=cases,
            secrets=known_secret_values(environ),
        )
    except DatasetSyncError as error:
        raise PreflightError(error.reason, str(error)) from error
