"""Resolve the runtime configuration of one evaluation experiment.

This module turns ``ConfigSettings`` plus CLI overrides into a frozen
``EvaluationRuntimeConfig``: effort precedence, naming, fingerprints,
git provenance, and secret collection/redaction. It never serializes
provider secrets.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import ntpath
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, TypeAdapter, ValidationError

# The registry's version is read from the registry rather than restated
# here. This module used to carry its own ``_CASE_REGISTRY_VERSION = 1``,
# written when the case registry did not exist yet; the two constants
# agreed only because nothing had moved, and ``experiment_metadata``
# emitted the private copy. The first bump to the canonical constant would
# have left every artifact recording a version of a registry it did not
# use, which is a provenance lie in the artifact, not a cosmetic one.
from deep_research.evaluation.cases import CASE_REGISTRY_VERSION
from deep_research.evaluation.models import (
    AgentName,
    EvaluationTier,
    cli_agent_name,
)
from deep_research.utils.config import (
    ConfigSettings,
    EmbeddingProviderName,
    EvaluationConfig,
    LLMConfig,
    ProviderName,
    ReasoningEffort,
)
from deep_research.utils.types import ContractModel, JsonValue

EVALUATION_PACKAGE_VERSION = "1.1.0"
"""The evaluator package version stamped into every artifact it writes.

Bumped ``1.0.0`` → ``1.1.0`` with Task 8: the evaluators read typed gaps now
and parse the legacy string-gap shape into them, so a score this package
produces is not the score 1.0.0 produced. Stamping the version is what keeps
"the new numbers are the honest ones" checkable rather than assumed.
"""

_SECRET_ENVIRONMENT_VARIABLES = (
    "DEEPSEEK_API_KEY",
    # Not required by the DeepSeek baseline, but still redacted whenever it
    # is present: this tuple defines what gets scrubbed, not what is needed.
    "OPENAI_API_KEY",
    "LANGSMITH_API_KEY",
    "TAVILY_API_KEY",
    "LANGSMITH_WORKSPACE_ID",
)

_EFFORT_ADAPTER = TypeAdapter(ReasoningEffort)

_JUDGE_STRUCTURED_TRANSPORT = {
    "deepseek": "deepseek_responses_json_schema_v1",
    "openai": "openai_responses_parse_v1",
}

# How the *target* agents select tools. Distinct from the judge's structured
# transport above: the target ReAct turn now uses provider-native tool calls,
# so an artifact recorded before this change is not comparable to one after it.
_TARGET_REACT_TRANSPORT = {
    "deepseek": "deepseek_chat_tools_auto_v1",
    "openai": "openai_responses_tools_auto_v1",
}


def judge_structured_transport(provider: ProviderName) -> str:
    return _JUDGE_STRUCTURED_TRANSPORT[provider]


def target_react_transport(provider: ProviderName) -> str:
    return _TARGET_REACT_TRANSPORT[provider]


def _validated_effort(value: str) -> ReasoningEffort:
    """Validate against the six approved levels, listing them on failure."""
    try:
        return _EFFORT_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ValueError(
            f"invalid reasoning effort {value!r}; expected one of: "
            "none, minimal, low, medium, high, xhigh, max"
        ) from error


def resolve_target_effort(
    config: EvaluationConfig,
    agent_name: AgentName,
    *,
    override: ReasoningEffort | None,
) -> ReasoningEffort:
    """The evaluation profile's own effort: override, per-agent, then global.

    This is the *experiment-only* profile. Production parity resolves the
    target from ``LLMConfig.resolve_for`` instead (see
    ``resolve_target_profile``); this function stays the definition of what an
    evaluation-only run would use, and of the fallback for an agent that
    production does not name.
    """
    if override is not None:
        return _validated_effort(override)
    per_agent = config.target_reasoning_effort_overrides.get(agent_name)
    if per_agent is not None:
        return _validated_effort(per_agent)
    return _validated_effort(config.target_reasoning_effort)


# Where one evaluation target's model/effort came from. ``production`` is the
# only source that may be counted as release evidence: it is the profile the
# CLI runs. ``evaluation`` is the harness's own profile and ``invocation`` is
# a per-run CLI override, and both are experiments about a configuration
# rather than a measurement of the shipped one.
TargetProfileSource = Literal["production", "evaluation", "invocation"]

# Where the ``production_parity`` toggle itself came from for this run.
# ``"invocation"`` means a CLI flag (``--production-parity`` /
# ``--no-production-parity``) overrode ``config.yaml`` for this run alone;
# ``"configuration"`` means the run simply inherited ``config.yaml``'s own
# ``evaluation.production_parity``. Recording this is what lets an artifact
# distinguish a run that inherited parity from one forced to it on the
# command line -- disclosure a bare ``production_parity`` bool cannot give.
ProductionParitySource = Literal["invocation", "configuration"]


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """One evaluation target's resolved model and reasoning effort."""

    model: str
    reasoning_effort: ReasoningEffort
    source: TargetProfileSource

    @property
    def release_evidence(self) -> bool:
        return self.source == "production"


def resolve_target_profile(
    base: LLMConfig,
    evaluation: EvaluationConfig,
    agent_name: AgentName,
    *,
    override: ReasoningEffort | None,
    production_parity: bool,
) -> TargetProfile:
    """Resolve one target's profile, preferring the production declaration.

    Precedence, and why:

    1. an explicit per-run override — an experiment the caller declared;
    2. production's own per-agent declaration (``llm.model_overrides``) when
       ``production_parity`` is on, because that is what the CLI actually
       runs, and a measurement of the shipped configuration is what the
       harness exists to produce;
    3. the evaluation profile — used when production declares nothing for
       this agent, and labelled an experiment rather than release evidence.

    ``production_parity`` is a caller-resolved value, not read from
    ``evaluation`` here: the caller (``build_runtime_config``) already
    folds a per-invocation CLI override on top of
    ``evaluation.production_parity`` once, and passing the already-resolved
    bool keeps that single resolution point instead of a second copy of the
    same precedence logic here.

    Before this, an evaluation run always used the evaluation-only profile
    while the CLI could express only one effort for all six agents, so the
    two resolved different reasoning and the corpus measured a configuration
    no release could reproduce (baseline §6.2, D-10).
    """
    if override is not None:
        return TargetProfile(
            model=base.resolve_for(agent_name).model,
            reasoning_effort=_validated_effort(override),
            source="invocation",
        )
    declared = base.model_overrides.get(agent_name)
    if production_parity and declared is not None:
        resolved = base.resolve_for(agent_name)
        return TargetProfile(
            model=resolved.model,
            reasoning_effort=resolved.reasoning_effort,
            source="production",
        )
    return TargetProfile(
        model=evaluation.target_model,
        reasoning_effort=resolve_target_effort(
            evaluation, agent_name, override=None
        ),
        source="evaluation",
    )


def resolve_judge_effort(
    config: EvaluationConfig,
    *,
    override: ReasoningEffort | None,
) -> ReasoningEffort:
    """The judge's effort never inherits from any target-agent effort."""
    if override is not None:
        return _validated_effort(override)
    return _validated_effort(config.judge_reasoning_effort)


@dataclass(frozen=True, slots=True)
class GitMetadata:
    """Provenance of the repository the experiment ran from."""

    commit: str
    short_sha: str
    dirty: bool


def resolve_git_metadata(*, run=subprocess.run) -> GitMetadata:
    """Read the current commit and worktree cleanliness.

    Unknown provenance counts as dirty on purpose: an experiment that
    cannot prove its commit must not look reproducible.
    """
    try:
        commit_result = run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        status_result = run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return GitMetadata(commit="unknown", short_sha="unknown", dirty=True)
    if commit_result.returncode != 0 or status_result.returncode != 0:
        return GitMetadata(commit="unknown", short_sha="unknown", dirty=True)
    commit = commit_result.stdout.strip()
    return GitMetadata(
        commit=commit,
        short_sha=commit[:7],
        dirty=bool(status_result.stdout.strip()),
    )


def fingerprint(payload: object) -> str:
    """A stable, order-insensitive 12-hex-character content fingerprint."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def agent_prompt_fingerprint(agent_name: AgentName) -> str:
    """Hash the agent's module source and the shared prompt library."""
    agent_source = inspect.getsource(
        importlib.import_module(f"deep_research.agents.{agent_name}")
    )
    prompts_source = inspect.getsource(
        importlib.import_module("deep_research.agents.prompts")
    )
    return fingerprint({"agent": agent_source, "prompts": prompts_source})


def dataset_name(
    agent_name: AgentName, tier: EvaluationTier, dataset_version: int
) -> str:
    return (
        f"deep-research-{cli_agent_name(agent_name)}-{tier}-v{dataset_version}"
    )


def experiment_name(
    agent_name: AgentName,
    tier: EvaluationTier,
    *,
    now: datetime,
    git_sha: str,
    prefix: str | None,
) -> str:
    base = (
        f"{cli_agent_name(agent_name)}-{tier}-"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}-{git_sha}"
    )
    if prefix and prefix.strip():
        return f"{prefix.strip()}-{base}"
    return base


def _extended_windows_path(path: Path) -> Path:
    """Return a normalized extended path on Windows only."""
    if os.name != "nt":
        return Path(path)

    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[len("\\\\?\\UNC\\") :]
    elif value.startswith("\\\\?\\"):
        value = value[len("\\\\?\\") :]
    value = ntpath.abspath(ntpath.normpath(value))
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


class EvaluationRuntimeConfig(ContractModel):
    """The frozen, resolved configuration of one evaluation experiment.

    Frozen: the effective efforts are resolved once, before any repetition
    starts, and one experiment never mixes reasoning profiles across
    repetitions.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        arbitrary_types_allowed=True,
    )

    agent_name: AgentName
    tier: EvaluationTier
    case_id: str | None = None
    repetitions: int
    max_concurrency: int
    target_model: str
    target_reasoning_effort: ReasoningEffort
    target_profile_source: TargetProfileSource
    """Where the target's model and effort came from.

    ``"production"`` alone means this run measured the configuration the CLI
    runs; ``"evaluation"`` and ``"invocation"`` are experiments about a
    configuration.
    """
    production_parity: bool
    """Whether production's own declaration was consulted for this run."""
    production_parity_source: ProductionParitySource
    """Whether ``production_parity`` came from a per-run CLI override
    (``"invocation"``) or was simply inherited from ``config.yaml``
    (``"configuration"``). Without this, an artifact cannot tell a run
    that was forced onto (or off) production parity from one that just
    inherited whatever config.yaml happened to say."""
    experiment_only: bool
    """True when the resolved profile is not the one production runs."""
    judge_model: str
    judge_reasoning_effort: ReasoningEffort
    judge_temperature: float | None
    # Every evaluation case runs with thinking on; no case needs it off, so
    # the field is deliberately single-valued rather than a free toggle.
    thinking_mode: Literal["enabled"]
    embedding_provider: EmbeddingProviderName
    embedding_model: str
    dataset_name: str
    dataset_version: int
    rubric_version: int
    experiment_name: str
    output_root: Path
    repetition_floor: float
    case_average_threshold: float
    live_threshold: float
    git: GitMetadata
    configuration_fingerprint: str
    judge_configuration_fingerprint: str
    # The judge's own operation-specific output budget. The verdict carries
    # six common dimensions, the agent-specific dimensions, and a rationale;
    # at the global cap the adapter returned judge_output_limit and no score.
    judge_max_tokens: int
    prompt_fingerprint: str
    package_version: str

    @property
    def release_evidence(self) -> bool:
        """Whether this run's target profile is the one production runs."""
        return not self.experiment_only


def build_runtime_config(
    settings: ConfigSettings,
    *,
    agent_name: AgentName,
    tier: EvaluationTier,
    case_id: str | None,
    reasoning_effort: ReasoningEffort | None,
    judge_reasoning_effort: ReasoningEffort | None,
    output_directory: str | None,
    experiment_prefix: str | None,
    now: datetime,
    git: GitMetadata,
    production_parity: bool | None = None,
) -> EvaluationRuntimeConfig:
    """Resolve settings plus CLI overrides into one frozen runtime config.

    ``production_parity`` is the CLI's own per-invocation override
    (``None`` when neither ``--production-parity`` nor
    ``--no-production-parity`` was passed, meaning "inherit
    config.yaml"). It is resolved once, here, against
    ``evaluation.production_parity`` and the resolved value is what both
    ``resolve_target_profile`` and the runtime config's own
    ``production_parity`` field use -- never a mutated copy of
    ``evaluation``, which would desync from ``configuration_fingerprint``
    (that fingerprint hashes ``settings.model_dump()`` directly).
    """
    evaluation = settings.evaluation
    parity = (
        evaluation.production_parity
        if production_parity is None
        else production_parity
    )
    parity_source: ProductionParitySource = (
        "configuration" if production_parity is None else "invocation"
    )
    profile = resolve_target_profile(
        settings.llm,
        evaluation,
        agent_name,
        override=reasoning_effort,
        production_parity=parity,
    )
    judge_effort = resolve_judge_effort(
        evaluation, override=judge_reasoning_effort
    )
    # An evaluation-only profile is non-release evidence exactly when it
    # disagrees with what production would run for this agent. Agreement means
    # the experiment is still measuring the shipped configuration even though
    # the value came from the evaluation block.
    production = settings.llm.resolve_for(agent_name)
    experiment_only = profile.source != "production" and (
        profile.model != production.model
        or profile.reasoning_effort != production.reasoning_effort
    )
    resolved_dataset_name = dataset_name(
        agent_name, tier, evaluation.dataset_version
    )
    resolved_experiment_name = experiment_name(
        agent_name,
        tier,
        now=now,
        git_sha=git.short_sha,
        prefix=experiment_prefix,
    )
    # ``None`` means inherit production's selection: the evaluation harness
    # runs agents the way production does unless an explicit override says
    # otherwise. The two fields resolve independently of each other, the
    # same way ``build_runtime`` (``runtime/assembly.py``) always passes
    # both ``llm.embedding_provider`` and ``llm.embedding_model`` regardless
    # of which provider is selected.
    resolved_embedding_provider = (
        evaluation.embedding_provider
        if evaluation.embedding_provider is not None
        else settings.llm.embedding_provider
    )
    resolved_embedding_model = (
        evaluation.embedding_model
        if evaluation.embedding_model is not None
        else settings.llm.embedding_model
    )
    configuration_fingerprint = fingerprint(
        {
            "application": settings.model_dump(mode="json"),
            "target_model": profile.model,
            "target_reasoning_effort": profile.reasoning_effort,
            "target_profile_source": profile.source,
            "target_react_transport": target_react_transport(
                settings.llm.provider
            ),
            "thinking_mode": "enabled",
            "dataset_version": evaluation.dataset_version,
            "rubric_version": evaluation.rubric_version,
            "package_version": EVALUATION_PACKAGE_VERSION,
        }
    )
    judge_configuration_fingerprint = fingerprint(
        {
            "provider": settings.llm.provider,
            "structured_transport": judge_structured_transport(
                settings.llm.provider
            ),
            "judge_model": evaluation.judge_model,
            "judge_reasoning_effort": judge_effort,
            "judge_temperature": evaluation.judge_temperature,
            "thinking_mode": "enabled",
            "rubric_version": evaluation.rubric_version,
        }
    )
    root = Path(output_directory or evaluation.output_directory)
    return EvaluationRuntimeConfig(
        agent_name=agent_name,
        tier=tier,
        case_id=case_id,
        repetitions=(
            evaluation.controlled_repetitions
            if tier == "controlled"
            else evaluation.live_repetitions
        ),
        max_concurrency=evaluation.max_concurrency,
        target_model=profile.model,
        target_reasoning_effort=profile.reasoning_effort,
        target_profile_source=profile.source,
        production_parity=parity,
        production_parity_source=parity_source,
        experiment_only=experiment_only,
        judge_model=evaluation.judge_model,
        judge_reasoning_effort=judge_effort,
        judge_temperature=evaluation.judge_temperature,
        thinking_mode="enabled",
        embedding_provider=resolved_embedding_provider,
        embedding_model=resolved_embedding_model,
        dataset_name=resolved_dataset_name,
        dataset_version=evaluation.dataset_version,
        rubric_version=evaluation.rubric_version,
        experiment_name=resolved_experiment_name,
        output_root=_extended_windows_path(
            root / cli_agent_name(agent_name) / resolved_experiment_name
        ),
        repetition_floor=evaluation.controlled_repetition_floor,
        case_average_threshold=evaluation.controlled_case_average_threshold,
        live_threshold=evaluation.live_threshold,
        git=git,
        configuration_fingerprint=configuration_fingerprint,
        judge_configuration_fingerprint=judge_configuration_fingerprint,
        judge_max_tokens=settings.agents.judge_max_tokens,
        prompt_fingerprint=agent_prompt_fingerprint(agent_name),
        package_version=EVALUATION_PACKAGE_VERSION,
    )


def target_llm_config(
    runtime: EvaluationRuntimeConfig, base: LLMConfig
) -> LLMConfig:
    """The LLM config the target agent actually runs under.

    ``provider`` is inherited from the application config, so an evaluation
    run always talks to the same vendor production does. ``model_overrides``
    is cleared on purpose: the profile is frozen on the runtime config before
    any repetition starts, and a live override table would let a later edit
    change the effort mid-experiment.

    When the frozen profile came from production, this re-resolves it against
    ``base`` and refuses a disagreement. A configuration edited between the
    runtime config being built and the provider being constructed is exactly
    the case a silent fallback would hide — the run would then be reported
    under a fingerprint it no longer matches.
    """
    if runtime.target_profile_source == "production":
        resolved = base.resolve_for(runtime.agent_name)
        if (
            resolved.model != runtime.target_model
            or resolved.reasoning_effort != runtime.target_reasoning_effort
        ):
            raise ValueError(
                "the frozen production profile no longer matches the "
                f"configured llm for {runtime.agent_name}: this run was "
                f"frozen at {runtime.target_model}/"
                f"{runtime.target_reasoning_effort} and production now "
                f"resolves {resolved.model}/{resolved.reasoning_effort}"
            )
    return base.model_copy(
        update={
            "provider": base.provider,
            "model": runtime.target_model,
            "model_overrides": {},
            "reasoning_effort": runtime.target_reasoning_effort,
            "thinking_mode": runtime.thinking_mode,
        }
    )


def judge_llm_config(
    runtime: EvaluationRuntimeConfig, base: LLMConfig
) -> LLMConfig:
    """The judge's LLM config, independent of every target-agent setting.

    ``temperature`` is applied only when the evaluation config actually
    pins one: ``LLMConfig.temperature`` is non-optional, and the selected
    provider's capability table — not a ``None`` here — decides whether the
    parameter is sent at all. For DeepSeek with thinking enabled it never is.
    """
    update: dict[str, object] = {
        "provider": base.provider,
        "model": runtime.judge_model,
        "model_overrides": {},
        "reasoning_effort": runtime.judge_reasoning_effort,
        "thinking_mode": runtime.thinking_mode,
    }
    if runtime.judge_temperature is not None:
        update["temperature"] = runtime.judge_temperature
    return base.model_copy(update=update)


def known_secret_values(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The non-blank, non-trivial secret values present in ``environ``.

    Values shorter than 8 characters are dropped: a short or blank value
    would match harmless text everywhere. De-duplicated in fixed order.
    """
    values: list[str] = []
    seen: set[str] = set()
    for name in _SECRET_ENVIRONMENT_VARIABLES:
        value = environ.get(name, "").strip()
        if len(value) >= 8 and value not in seen:
            seen.add(value)
            values.append(value)
    return tuple(values)


def contains_secret(payload: object, secrets: Sequence[str]) -> list[str]:
    """Dotted JSON paths of every string in ``payload`` holding a secret."""
    paths: list[str] = []

    def walk(value: object, path: str) -> None:
        if isinstance(value, str):
            if any(secret and secret in value for secret in secrets):
                paths.append(path)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(payload, "")
    return paths


def redact_secrets(payload: object, secrets: Sequence[str]) -> object:
    """A structurally identical copy with every secret replaced."""
    if isinstance(payload, str):
        for secret in secrets:
            if secret:
                payload = payload.replace(secret, "[REDACTED]")
        return payload
    if isinstance(payload, Mapping):
        return {
            key: redact_secrets(value, secrets) for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_secrets(item, secrets) for item in payload]
    return payload


class SecretLeakError(RuntimeError):
    """A payload destined for LangSmith contained a known secret value.

    The message lists where the secret appeared; it never contains the
    secret itself.
    """

    @classmethod
    def for_paths(cls, paths: Sequence[str]) -> "SecretLeakError":
        return cls("a known secret value appeared at: " + ", ".join(paths))


def experiment_metadata(
    runtime: EvaluationRuntimeConfig, settings: ConfigSettings
) -> dict[str, JsonValue]:
    """Everything the experiment metadata block must record, never secrets."""
    return {
        "agent": runtime.agent_name,
        "tier": runtime.tier,
        "git_commit": runtime.git.commit,
        "git_dirty": runtime.git.dirty,
        "target_model": runtime.target_model,
        "target_reasoning_effort": runtime.target_reasoning_effort,
        "target_profile_source": runtime.target_profile_source,
        "production_parity": runtime.production_parity,
        "production_parity_source": runtime.production_parity_source,
        "release_evidence": runtime.release_evidence,
        "target_react_transport": target_react_transport(
            settings.llm.provider
        ),
        "thinking_mode": runtime.thinking_mode,
        "target_model_configuration": target_llm_config(
            runtime, settings.llm
        ).model_dump(mode="json"),
        "configuration_fingerprint": runtime.configuration_fingerprint,
        "judge_provider": settings.llm.provider,
        "judge_structured_transport": judge_structured_transport(
            settings.llm.provider
        ),
        "judge_model": runtime.judge_model,
        "judge_reasoning_effort": runtime.judge_reasoning_effort,
        "judge_temperature": runtime.judge_temperature,
        "judge_configuration_fingerprint": (
            runtime.judge_configuration_fingerprint
        ),
        "case_registry_version": CASE_REGISTRY_VERSION,
        "rubric_version": runtime.rubric_version,
        "dependency_mode": runtime.tier,
        "target_prompt_fingerprint": runtime.prompt_fingerprint,
        "evaluation_package_version": runtime.package_version,
        "experiment_name": runtime.experiment_name,
        "dataset_name": runtime.dataset_name,
        "dataset_version": runtime.dataset_version,
    }

