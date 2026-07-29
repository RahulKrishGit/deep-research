"""Tests for configuration loading."""

from pathlib import Path

import pytest
import yaml

from deep_research.utils.config import ConfigSettings, load_config


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Create a complete valid configuration file."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "max_tokens": 4096,
                },
                "langsmith": {"tracing_enabled": False, "project": "yaml-project"},
                "tavily": {"search_depth": "basic", "max_results": 5},
                "memory": {
                    "long_term": {
                        "collection_name": "test",
                        "persist_directory": "memory/",
                    },
                    "short_term": {"max_turns": 20},
                },
                "output": {"directory": "output/", "default_format": "markdown"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_default_config(config_path: Path) -> None:
    """Loading a valid config returns typed settings."""
    settings = load_config(str(config_path))

    assert isinstance(settings, ConfigSettings)
    assert settings.llm.provider == "openai"
    assert settings.llm.model == "gpt-4o"
    assert settings.tavily.max_results == 5


def test_load_config_missing_file() -> None:
    """Missing config files raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    """Invalid YAML content raises ValueError."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{invalid: yaml: broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid YAML"):
        load_config(str(config_path))


def test_load_config_empty_yaml(tmp_path: Path) -> None:
    """Empty YAML content is rejected because it is not a mapping."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid YAML"):
        load_config(str(config_path))


@pytest.mark.parametrize(
    ("environment_name", "expected_path", "value", "expected_value"),
    [
        ("LLM_PROVIDER", ("llm", "provider"), "anthropic", "anthropic"),
        ("LLM_MODEL", ("llm", "model"), "gpt-4.1", "gpt-4.1"),
        ("LLM_TEMPERATURE", ("llm", "temperature"), "0.25", 0.25),
        ("LLM_MAX_TOKENS", ("llm", "max_tokens"), "2048", 2048),
        ("LANGSMITH_TRACING", ("langsmith", "tracing_enabled"), "true", True),
        ("LANGSMITH_PROJECT", ("langsmith", "project"), "env-project", "env-project"),
        ("TAVILY_SEARCH_DEPTH", ("tavily", "search_depth"), "advanced", "advanced"),
        ("TAVILY_MAX_RESULTS", ("tavily", "max_results"), "8", 8),
        (
            "MEMORY_LONG_TERM_COLLECTION_NAME",
            ("memory", "long_term", "collection_name"),
            "env-collection",
            "env-collection",
        ),
        (
            "MEMORY_LONG_TERM_PERSIST_DIRECTORY",
            ("memory", "long_term", "persist_directory"),
            "env-memory/",
            "env-memory/",
        ),
        (
            "MEMORY_SHORT_TERM_MAX_TURNS",
            ("memory", "short_term", "max_turns"),
            "12",
            12,
        ),
        ("OUTPUT_DIRECTORY", ("output", "directory"), "env-output/", "env-output/"),
        ("OUTPUT_DEFAULT_FORMAT", ("output", "default_format"), "json", "json"),
    ],
)
def test_environment_overrides_every_yaml_leaf(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    environment_name: str,
    expected_path: tuple[str, ...],
    value: str,
    expected_value: object,
) -> None:
    """Environment variables replace YAML leaves before Pydantic validation."""
    monkeypatch.setenv(environment_name, value)

    settings = load_config(str(config_path))
    actual_value: object = settings
    for attribute in expected_path:
        actual_value = getattr(actual_value, attribute)

    assert actual_value == expected_value


@pytest.mark.parametrize(
    "missing_environment_name",
    ["OPENAI_API_KEY", "TAVILY_API_KEY"],
)
def test_load_config_strict_always_requires_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    missing_environment_name: str,
) -> None:
    """Strict mode rejects any missing required runtime secret."""
    for environment_name in (
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")
    monkeypatch.delenv(missing_environment_name)

    with pytest.raises(ValueError, match=missing_environment_name):
        load_config(str(config_path), strict=True)


@pytest.mark.parametrize(
    "blank_value",
    ["", "   "],
)
def test_load_config_strict_treats_blank_secrets_as_missing(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, blank_value: str
) -> None:
    """Strict mode rejects empty and whitespace-only runtime secrets."""
    for environment_name in (
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")
    monkeypatch.setenv("TAVILY_API_KEY", blank_value)

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        load_config(str(config_path), strict=True)


def test_load_config_strict_env_ok(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """Strict mode accepts non-empty values for all documented secrets."""
    for environment_name in (
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")

    settings = load_config(str(config_path), strict=True)

    assert settings.llm.provider == "openai"


def test_config_settings_excludes_provider_secrets(config_path: Path) -> None:
    """Provider secrets are validated at runtime, not stored in settings."""
    settings = load_config(str(config_path))

    assert "OPENAI_API_KEY" not in settings.model_dump()
    assert "LANGSMITH_API_KEY" not in settings.model_dump()
    assert "TAVILY_API_KEY" not in settings.model_dump()


def test_load_config_strict_disabled_tracing_does_not_require_langsmith(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv("TAVILY_API_KEY", "configured")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

    settings = load_config(str(config_path), strict=True)

    assert settings.langsmith.tracing_enabled is False


@pytest.mark.parametrize(
    "missing_environment_name", ["LANGSMITH_API_KEY", "LANGSMITH_PROJECT"]
)
def test_load_config_strict_enabled_tracing_requires_langsmith_credentials(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    missing_environment_name: str,
) -> None:
    for environment_name in (
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv(missing_environment_name)

    with pytest.raises(ValueError, match=missing_environment_name):
        load_config(str(config_path), strict=True)
