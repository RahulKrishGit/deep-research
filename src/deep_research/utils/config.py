"""Typed configuration loading with environment variable overrides."""

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, JsonValue


class MissingSecretsError(ValueError):
    """Strict-mode loading failed: required runtime secrets are absent.

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working, and a distinguishable type so a caller can tell a missing
    secret apart from an invalid configuration file without parsing text.
    """


ReasoningEffort: TypeAlias = Literal[
    "none", "low", "medium", "high", "xhigh", "max"
]


class LLMConfig(BaseModel):
    """OpenAI model and request settings."""

    provider: str = Field(default="openai", min_length=1)
    model: str = Field(default="gpt-4o", min_length=1)
    embedding_model: str = Field(
        default="text-embedding-3-small",
        min_length=1,
    )
    model_overrides: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=60.0, gt=0)
    retry_count: int = Field(default=2, ge=0)
    # ``None`` omits the parameter entirely: reasoning models reject it.
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    # ``None`` omits the Responses ``reasoning`` block entirely, which is
    # required for non-reasoning models such as gpt-4o.
    reasoning_effort: ReasoningEffort | None = None
    # Pro mode is deliberately unrepresentable in this build.
    reasoning_mode: Literal["standard"] = "standard"

    def model_for(self, agent_name: str | None) -> str:
        """Return an agent override when configured, otherwise the default."""
        if agent_name is None:
            return self.model
        return self.model_overrides.get(agent_name, self.model)

    def request_options(self) -> dict[str, Any]:
        """The optional Responses parameters this configuration sends.

        Built once here rather than at three call sites so an ordinary
        call, a structured call, and a structured repair can never drift
        apart — the spec requires the resolved effort on all three.
        """
        options: dict[str, Any] = {"max_output_tokens": self.max_tokens}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            options["reasoning"] = {"effort": self.reasoning_effort}
        return options

class LangSmithConfig(BaseModel):
    """LangSmith tracing settings."""

    tracing_enabled: bool = False
    project: str = ""


class TavilyConfig(BaseModel):
    """Tavily search settings."""

    search_depth: str = "basic"
    max_results: int = 5


class LongTermMemoryConfig(BaseModel):
    """Long-term memory settings."""

    collection_name: str = "deep_research"
    persist_directory: str = "memory/"


class ShortTermMemoryConfig(BaseModel):
    """Short-term memory settings."""

    max_turns: int = 20


class ProceduralMemoryConfig(BaseModel):
    """Procedural strategy registry settings."""

    strategies_path: str = "memory/strategies.json"


class MemoryConfig(BaseModel):
    """Memory settings."""

    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    procedural: ProceduralMemoryConfig = ProceduralMemoryConfig()


class AgentRuntimeConfig(BaseModel):
    """Bounds every ReAct agent runs under.

    ``tool_budget`` may be zero: an agent with no tools still gets to think
    and finish, it just may never call one.
    """

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

    max_iterations: int = Field(default=3, ge=1)
    checkpointing_enabled: bool = False


class OutputConfig(BaseModel):
    """Output settings."""

    directory: str = "output/"
    default_format: str = "markdown"


class ConfigSettings(BaseModel):
    """All non-secret application configuration."""

    llm: LLMConfig = LLMConfig()
    langsmith: LangSmithConfig = LangSmithConfig()
    tavily: TavilyConfig = TavilyConfig()
    memory: MemoryConfig = MemoryConfig()
    agents: AgentRuntimeConfig = AgentRuntimeConfig()
    graph: GraphConfig = GraphConfig()
    output: OutputConfig = OutputConfig()


_ENVIRONMENT_OVERRIDES = {
    "LLM_PROVIDER": ("llm", "provider"),
    "LLM_MODEL": ("llm", "model"),
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
}
_REQUIRED_ENVIRONMENT_VARIABLES = (
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
)
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
        _validate_runtime_secrets(tracing_enabled=settings.langsmith.tracing_enabled)

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


def _validate_runtime_secrets(*, tracing_enabled: bool) -> None:
    """Raise when strict-mode runtime secrets are absent or blank."""
    required = list(_REQUIRED_ENVIRONMENT_VARIABLES)
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
