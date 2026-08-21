"""Typed configuration loading with environment variable overrides."""

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class MissingSecretsError(ValueError):
    """Strict-mode loading failed: required runtime secrets are absent.

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working, and a distinguishable type so a caller can tell a missing
    secret apart from an invalid configuration file without parsing text.
    """


ProviderName = Literal["deepseek", "openai"]
EmbeddingProviderName = Literal["local", "openai"]
ThinkingMode = Literal["enabled", "disabled"]
ReasoningEffort: TypeAlias = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]


class AgentModelOverride(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str | None = Field(default=None, min_length=1)
    thinking_mode: ThinkingMode | None = None
    reasoning_effort: ReasoningEffort | None = None


class EffectiveModelConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str = Field(min_length=1)
    thinking_mode: ThinkingMode
    reasoning_effort: ReasoningEffort


class LLMConfig(BaseModel):
    """Chat and embedding model and request settings."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName = "deepseek"
    model: str = Field(default="deepseek-v4-flash", min_length=1)
    # Independent of ``provider``: chat and embeddings need not share a
    # vendor, and the default stack is DeepSeek chat with local embeddings.
    embedding_provider: EmbeddingProviderName = "local"
    embedding_model: str = Field(
        default="text-embedding-3-small",
        min_length=1,
    )
    thinking_mode: ThinkingMode = "enabled"
    reasoning_effort: ReasoningEffort = "high"
    model_overrides: dict[str, str | AgentModelOverride] = Field(
        default_factory=dict
    )
    timeout: float = Field(default=60.0, gt=0)
    retry_count: int = Field(default=2, ge=0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)

    def resolve_for(self, agent_name: str | None) -> EffectiveModelConfig:
        override = None if agent_name is None else self.model_overrides.get(agent_name)
        if isinstance(override, str):
            return EffectiveModelConfig(
                model=override,
                thinking_mode=self.thinking_mode,
                reasoning_effort=self.reasoning_effort,
            )
        return EffectiveModelConfig(
            model=(
                self.model
                if override is None or override.model is None
                else override.model
            ),
            thinking_mode=(
                self.thinking_mode
                if override is None or override.thinking_mode is None
                else override.thinking_mode
            ),
            reasoning_effort=(
                self.reasoning_effort
                if override is None or override.reasoning_effort is None
                else override.reasoning_effort
            ),
        )

    def model_for(self, agent_name: str | None) -> str:
        return self.resolve_for(agent_name).model


class LangSmithConfig(BaseModel):
    """LangSmith tracing settings."""

    model_config = ConfigDict(extra="forbid")

    tracing_enabled: bool = False
    project: str = ""


class TavilyConfig(BaseModel):
    """Tavily search settings."""

    model_config = ConfigDict(extra="forbid")

    search_depth: str = "basic"
    max_results: int = 5


class LongTermMemoryConfig(BaseModel):
    """Long-term memory settings."""

    model_config = ConfigDict(extra="forbid")

    collection_name: str = "deep_research"
    persist_directory: str = "memory/"


class ShortTermMemoryConfig(BaseModel):
    """Short-term memory settings."""

    model_config = ConfigDict(extra="forbid")

    max_turns: int = 20


class ProceduralMemoryConfig(BaseModel):
    """Procedural strategy registry settings."""

    model_config = ConfigDict(extra="forbid")

    strategies_path: str = "memory/strategies.json"


class MemoryConfig(BaseModel):
    """Memory settings."""

    model_config = ConfigDict(extra="forbid")

    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    procedural: ProceduralMemoryConfig = ProceduralMemoryConfig()


class AgentRuntimeConfig(BaseModel):
    """Bounds every ReAct agent runs under.

    ``tool_budget`` may be zero: an agent with no tools still gets to think
    and finish, it just may never call one.
    """

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(default=5, ge=1)
    tool_budget: int = Field(default=10, ge=0)
    prompt_context_entries: int = Field(default=8, ge=0)
    observation_summary_chars: int = Field(default=200, ge=1)


class GraphConfig(BaseModel):
    """Bounds and durability for the macro research loop.

    ``max_iterations`` is the macro refinement budget the Critic spends;
    ``AgentRuntimeConfig.max_iterations`` is the *micro* ReAct bound inside
    one agent. They are deliberately separate numbers.

    There is no ``recursion_limit`` setting: LangGraph's superstep bound is
    derived from ``max_iterations`` and the graph's node count, and a second
    knob that could contradict the first is a bug waiting to happen.
    """

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(default=3, ge=1)
    checkpointing_enabled: bool = False


class OutputConfig(BaseModel):
    """Output settings."""

    model_config = ConfigDict(extra="forbid")

    directory: str = "output/"
    default_format: str = "markdown"


EVALUATION_AGENT_KEYS = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)

# DeepSeek V4 Flash supports exactly two enabled efforts: high and max. The
# original OpenAI baseline's low/medium levels map onto them as approved in
# the cutover spec: the two cheapest agents to high, everything else to max.
_DEFAULT_TARGET_EFFORTS: dict[str, ReasoningEffort] = {
    "planner": "max",
    "researcher": "high",
    "source_evaluator": "high",
    "fact_checker": "max",
    "synthesizer": "max",
    "critic": "max",
}


class EvaluationConfig(BaseModel):
    """Non-secret defaults for the individual-agent evaluation harness."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    controlled_repetitions: int = Field(default=3, ge=1)
    controlled_case_average_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    controlled_repetition_floor: float = Field(default=0.65, ge=0.0, le=1.0)
    live_repetitions: int = Field(default=1, ge=1)
    live_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    target_model: str = Field(default="deepseek-v4-flash", min_length=1)
    target_reasoning_effort: ReasoningEffort = "max"
    target_reasoning_effort_overrides: dict[str, ReasoningEffort] = Field(
        default_factory=lambda: dict(_DEFAULT_TARGET_EFFORTS)
    )
    judge_model: str = Field(default="deepseek-v4-flash", min_length=1)
    judge_reasoning_effort: ReasoningEffort = "max"
    # ``None`` means inherit ``llm.embedding_provider`` / ``llm.embedding_model``:
    # production is the single source of truth for the embedding selection,
    # and an evaluation run overrides it only when it sets one of these
    # explicitly. Live-tier bundles are the only consumer of the resolved
    # pair.
    embedding_provider: EmbeddingProviderName | None = None
    embedding_model: str | None = Field(default=None, min_length=1)
    # ``None`` omits the parameter for models that reject it; the spec pins
    # the judge at 0.0 and that is the default.
    judge_temperature: float | None = Field(default=0.0, ge=0.0, le=2.0)
    # Fixed at 1: repetition indexing in ``targets.py`` is only exact when
    # LangSmith runs the target sequentially.
    max_concurrency: int = Field(default=1, ge=1, le=1)
    output_directory: str = Field(default="output/evaluations/", min_length=1)
    dataset_version: int = Field(default=1, ge=1)
    rubric_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_override_keys(self) -> "EvaluationConfig":
        unknown = sorted(
            set(self.target_reasoning_effort_overrides)
            - set(EVALUATION_AGENT_KEYS)
        )
        if unknown:
            valid = ", ".join(EVALUATION_AGENT_KEYS)
            raise ValueError(
                "unknown target_reasoning_effort_overrides keys: "
                f"{', '.join(unknown)}; expected any of: {valid}"
            )
        return self


class ConfigSettings(BaseModel):
    """All non-secret application configuration."""

    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = LLMConfig()
    langsmith: LangSmithConfig = LangSmithConfig()
    tavily: TavilyConfig = TavilyConfig()
    memory: MemoryConfig = MemoryConfig()
    agents: AgentRuntimeConfig = AgentRuntimeConfig()
    graph: GraphConfig = GraphConfig()
    output: OutputConfig = OutputConfig()
    evaluation: EvaluationConfig = EvaluationConfig()


_ENVIRONMENT_OVERRIDES = {
    "LLM_PROVIDER": ("llm", "provider"),
    "LLM_MODEL": ("llm", "model"),
    "LLM_EMBEDDING_PROVIDER": ("llm", "embedding_provider"),
    "LLM_THINKING_MODE": ("llm", "thinking_mode"),
    "LLM_REASONING_EFFORT": ("llm", "reasoning_effort"),
    "LLM_EMBEDDING_MODEL": ("llm", "embedding_model"),
    "LLM_TIMEOUT": ("llm", "timeout"),
    "LLM_RETRY_COUNT": ("llm", "retry_count"),
    "LLM_TEMPERATURE": ("llm", "temperature"),
    "LLM_MAX_TOKENS": ("llm", "max_tokens"),
    "LANGSMITH_TRACING": ("langsmith", "tracing_enabled"),
    "LANGSMITH_PROJECT": ("langsmith", "project"),
    "TAVILY_SEARCH_DEPTH": ("tavily", "search_depth"),
    "TAVILY_MAX_RESULTS": ("tavily", "max_results"),
    "MEMORY_LONG_TERM_COLLECTION_NAME": ("memory", "long_term", "collection_name"),
    "MEMORY_LONG_TERM_PERSIST_DIRECTORY": ("memory", "long_term", "persist_directory"),
    "MEMORY_SHORT_TERM_MAX_TURNS": ("memory", "short_term", "max_turns"),
    "MEMORY_PROCEDURAL_STRATEGIES_PATH": (
        "memory",
        "procedural",
        "strategies_path",
    ),
    "AGENTS_MAX_ITERATIONS": ("agents", "max_iterations"),
    "AGENTS_TOOL_BUDGET": ("agents", "tool_budget"),
    "AGENTS_PROMPT_CONTEXT_ENTRIES": ("agents", "prompt_context_entries"),
    "AGENTS_OBSERVATION_SUMMARY_CHARS": ("agents", "observation_summary_chars"),
    "GRAPH_MAX_ITERATIONS": ("graph", "max_iterations"),
    "GRAPH_CHECKPOINTING_ENABLED": ("graph", "checkpointing_enabled"),
    "OUTPUT_DIRECTORY": ("output", "directory"),
    "OUTPUT_DEFAULT_FORMAT": ("output", "default_format"),
    "EVALUATION_TARGET_MODEL": ("evaluation", "target_model"),
    "EVALUATION_TARGET_REASONING_EFFORT": (
        "evaluation",
        "target_reasoning_effort",
    ),
    "EVALUATION_JUDGE_MODEL": ("evaluation", "judge_model"),
    "EVALUATION_JUDGE_REASONING_EFFORT": (
        "evaluation",
        "judge_reasoning_effort",
    ),
    "EVALUATION_OUTPUT_DIRECTORY": ("evaluation", "output_directory"),
}
_COMMON_REQUIRED_ENVIRONMENT_VARIABLES = ("TAVILY_API_KEY",)
_CHAT_PROVIDER_ENVIRONMENT_VARIABLES = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}
_EMBEDDING_PROVIDER_ENVIRONMENT_VARIABLES = {
    "local": (),
    "openai": ("OPENAI_API_KEY",),
}
_LANGSMITH_ENVIRONMENT_VARIABLES = (
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
)


_FREE_FORM_MAPPING_PATHS = frozenset({("llm", "model_overrides")})


def _merge_override_payload(
    target: dict[str, Any],
    overrides: Mapping[str, JsonValue],
    *,
    path: tuple[str, ...] = (),
) -> None:
    for key, value in overrides.items():
        current_path = (*path, key)
        if key not in target:
            raise ValueError(
                f"unknown config override: {'.'.join(current_path)}"
            )

        current = target[key]
        if current_path in _FREE_FORM_MAPPING_PATHS:
            if isinstance(current, dict) and isinstance(value, Mapping):
                current.update(deepcopy(dict(value)))
            else:
                target[key] = deepcopy(value)
        elif isinstance(current, dict) and isinstance(value, Mapping):
            _merge_override_payload(current, value, path=current_path)
        else:
            target[key] = deepcopy(value)


def apply_config_overrides(
    settings: ConfigSettings,
    overrides: Mapping[str, JsonValue],
) -> ConfigSettings:
    """Return a copy of ``settings`` with request-scoped overrides applied.

    Unknown paths raise ``ValueError`` so a caller can never silently
    override a setting that does not exist. The original settings are never
    mutated: the merged payload is validated into a fresh instance.
    """
    payload = settings.model_dump(mode="python")
    _merge_override_payload(payload, overrides)
    return ConfigSettings.model_validate(payload)


def load_config(
    config_path: str,
    strict: bool = False,
    *,
    overrides: Mapping[str, JsonValue] | None = None,
) -> ConfigSettings:
    """Load YAML config, apply environment overrides, and validate its types.

    Args:
        config_path: Path to a YAML configuration file.
        strict: Require all runtime secrets to be non-empty when true.
        overrides: Request-scoped overrides applied after environment
            values and validated against the final settings.

    Raises:
        FileNotFoundError: If ``config_path`` does not exist.
        ValueError: If YAML is invalid or an override names an unknown path.
        MissingSecretsError: If strict mode finds required runtime secrets
            absent.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    load_dotenv(dotenv_path=path.parent / ".env", override=False)

    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {config_path}: {error}") from error


    if not isinstance(raw_config, dict):
        raise ValueError(f"Invalid YAML in {config_path}: expected a top-level mapping")

    _apply_environment_overrides(raw_config)
    settings = ConfigSettings.model_validate(raw_config)
    if overrides:
        settings = apply_config_overrides(settings, overrides)
    if strict:
        _validate_runtime_secrets(
            provider=settings.llm.provider,
            embedding_provider=settings.llm.embedding_provider,
            tracing_enabled=settings.langsmith.tracing_enabled,
        )

    return settings


def _apply_environment_overrides(config: dict[str, Any]) -> None:
    """Apply present environment values to their nested config paths."""
    for environment_name, path in _ENVIRONMENT_OVERRIDES.items():
        value = os.getenv(environment_name)
        if value is None:
            continue

        target = config
        for key in path[:-1]:
            nested_value = target.get(key)
            if not isinstance(nested_value, dict):
                nested_value = {}
                target[key] = nested_value
            target = nested_value
        target[path[-1]] = value


def _validate_runtime_secrets(
    *,
    provider: ProviderName,
    embedding_provider: EmbeddingProviderName,
    tracing_enabled: bool,
) -> None:
    """Raise when strict-mode runtime secrets are absent or blank.

    Only the credentials the configured stack actually uses are required:
    the default DeepSeek-chat/local-embeddings stack needs no OpenAI key
    at all. De-duplicated in first-seen order so selecting OpenAI for both
    chat and embeddings names ``OPENAI_API_KEY`` once.
    """
    required: list[str] = []
    for name in (
        *_CHAT_PROVIDER_ENVIRONMENT_VARIABLES[provider],
        *_EMBEDDING_PROVIDER_ENVIRONMENT_VARIABLES[embedding_provider],
        *_COMMON_REQUIRED_ENVIRONMENT_VARIABLES,
    ):
        if name not in required:
            required.append(name)
    if tracing_enabled:
        required.extend(_LANGSMITH_ENVIRONMENT_VARIABLES)
    missing = [
        environment_name
        for environment_name in required
        if not os.getenv(environment_name, "").strip()
    ]
    if missing:
        names = ", ".join(missing)
        raise MissingSecretsError(
            f"Missing required environment variables in strict mode: {names}"
        )
