"""Typed configuration loading with environment variable overrides."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)

    def model_for(self, agent_name: str | None) -> str:
        """Return an agent override when configured, otherwise the default."""
        if agent_name is None:
            return self.model
        return self.model_overrides.get(agent_name, self.model)

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


class MemoryConfig(BaseModel):
    """Memory settings."""

    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()


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


def load_config(config_path: str, strict: bool = False) -> ConfigSettings:
    """Load YAML config, apply environment overrides, and validate its types.

    Args:
        config_path: Path to a YAML configuration file.
        strict: Require all runtime secrets to be non-empty when true.

    Raises:
        FileNotFoundError: If ``config_path`` does not exist.
        ValueError: If YAML is invalid or strict runtime secrets are missing.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {config_path}: {error}") from error


    if not isinstance(raw_config, dict):
        raise ValueError(f"Invalid YAML in {config_path}: expected a top-level mapping")

    _apply_environment_overrides(raw_config)
    settings = ConfigSettings.model_validate(raw_config)

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
        raise ValueError(
            f"Missing required environment variables in strict mode: {names}"
        )
