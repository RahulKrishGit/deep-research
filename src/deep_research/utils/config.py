"""Typed configuration loading with environment variable overrides."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class LLMConfig(BaseModel):
    """Language model settings."""

    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 4096


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
    "LLM_TEMPERATURE": ("llm", "temperature"),
    "LLM_MAX_TOKENS": ("llm", "max_tokens"),
    "LANGSMITH_TRACING_ENABLED": ("langsmith", "tracing_enabled"),
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
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "TAVILY_API_KEY",
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

    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise ValueError(f"Invalid YAML in {config_path}: expected a top-level mapping")

    _apply_environment_overrides(raw_config)
    settings = ConfigSettings.model_validate(raw_config)

    if strict:
        _validate_runtime_secrets()

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


def _validate_runtime_secrets() -> None:
    """Raise when strict-mode runtime secrets are absent or blank."""
    missing = [
        environment_name
        for environment_name in _REQUIRED_ENVIRONMENT_VARIABLES
        if not os.getenv(environment_name, "").strip()
    ]
    if missing:
        names = ", ".join(missing)
        raise ValueError(
            f"Missing required environment variables in strict mode: {names}"
        )
