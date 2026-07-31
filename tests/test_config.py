"""Tests for configuration loading."""

import json
import os
from pathlib import Path

import pytest
import yaml

from deep_research.observability import Tracker
from deep_research.utils.config import ConfigSettings, LLMConfig, load_config


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
                    "embedding_model": "text-embedding-3-small",
                    "model_overrides": {"planner": "gpt-4o-mini"},
                    "timeout": 45.0,
                    "retry_count": 2,
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
                    "procedural": {"strategies_path": "memory/strategies.json"},
                },
                "agents": {
                    "max_iterations": 5,
                    "tool_budget": 10,
                    "prompt_context_entries": 8,
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

def test_load_config_loads_sibling_dotenv_before_strict_validation(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """A sibling .env supplies secrets before strict validation runs."""
    for environment_name in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    (config_path.parent / ".env").write_text(
        "\n".join(
            (
                "OPENAI_API_KEY=dotenv-openai",
                "TAVILY_API_KEY=dotenv-tavily",
                "LANGSMITH_API_KEY=dotenv-langsmith",
                "LANGSMITH_PROJECT=dotenv-project",
            )
        ),
        encoding="utf-8",
    )

    settings = load_config(str(config_path), strict=True)

    assert settings.langsmith.project == "dotenv-project"
    assert os.environ["OPENAI_API_KEY"] == "dotenv-openai"
    assert os.environ["TAVILY_API_KEY"] == "dotenv-tavily"


def test_process_environment_takes_precedence_over_sibling_dotenv(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """A value injected by the process wins over the local file."""
    monkeypatch.setenv("LLM_MODEL", "shell-model")
    monkeypatch.setenv("OPENAI_API_KEY", "shell-openai")
    (config_path.parent / ".env").write_text(
        "LLM_MODEL=dotenv-model\nOPENAI_API_KEY=dotenv-openai\n",
        encoding="utf-8",
    )

    settings = load_config(str(config_path))

    assert settings.llm.model == "shell-model"
    assert os.environ["OPENAI_API_KEY"] == "shell-openai"


def test_load_config_without_sibling_dotenv_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """The normal YAML load remains valid when no .env file exists."""
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert not (config_path.parent / ".env").exists()

    settings = load_config(str(config_path))

    assert settings.llm.model == "gpt-4o"

def test_llm_config_resolves_agent_model_override(config_path: Path) -> None:
    """Return the configured agent override or the default OpenAI model."""
    settings = load_config(str(config_path))

    assert settings.llm.model_for(None) == "gpt-4o"
    assert settings.llm.model_for("researcher") == "gpt-4o"
    assert settings.llm.model_for("planner") == "gpt-4o-mini"
    assert settings.llm.embedding_model == "text-embedding-3-small"
    assert settings.llm.timeout == 45.0
    assert settings.llm.retry_count == 2


@pytest.mark.parametrize(
    ("environment_name", "expected_value"),
    [
        ("LLM_EMBEDDING_MODEL", "text-embedding-3-large"),
        ("LLM_TIMEOUT", 12.5),
        ("LLM_RETRY_COUNT", 4),
    ],
)
def test_openai_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    environment_name: str,
    expected_value: object,
) -> None:
    """Apply OpenAI runtime environment overrides before validation."""
    attribute = {
        "LLM_EMBEDDING_MODEL": "embedding_model",
        "LLM_TIMEOUT": "timeout",
        "LLM_RETRY_COUNT": "retry_count",
    }[environment_name]
    monkeypatch.setenv(environment_name, str(expected_value))

    settings = load_config(str(config_path))

    assert getattr(settings.llm, attribute) == expected_value


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("timeout", 0),
        ("retry_count", -1),
        ("max_tokens", 0),
        ("temperature", -0.1),
    ],
)
def test_llm_config_rejects_invalid_runtime_values(
    field_name: str,
    invalid_value: object,
) -> None:
    """Reject unsafe request-runtime values at configuration load time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LLMConfig(**{field_name: invalid_value})


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
        (
            "MEMORY_PROCEDURAL_STRATEGIES_PATH",
            ("memory", "procedural", "strategies_path"),
            "env-memory/strategies.json",
            "env-memory/strategies.json",
        ),
        ("AGENTS_MAX_ITERATIONS", ("agents", "max_iterations"), "9", 9),
        ("AGENTS_TOOL_BUDGET", ("agents", "tool_budget"), "3", 3),
        (
            "AGENTS_PROMPT_CONTEXT_ENTRIES",
            ("agents", "prompt_context_entries"),
            "4",
            4,
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


@pytest.mark.asyncio
async def test_dotenv_api_keys_are_absent_from_settings_and_tracker_records(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """Loaded API-key values never enter serializable config or telemetry."""
    secret_values = (
        "dotenv-openai-secret",
        "dotenv-tavily-secret",
        "dotenv-langsmith-secret",
    )
    for environment_name in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    (config_path.parent / ".env").write_text(
        "\n".join(
            (
                f"OPENAI_API_KEY={secret_values[0]}",
                f"TAVILY_API_KEY={secret_values[1]}",
                f"LANGSMITH_API_KEY={secret_values[2]}",
                "LANGSMITH_PROJECT=dotenv-project",
            )
        ),
        encoding="utf-8",
    )

    settings = load_config(str(config_path), strict=True)
    tracker = Tracker.from_config(settings.langsmith)
    async with tracker.session_span("session-1", "safe question"):
        pass

    serialized_surfaces = json.dumps(
        {
            "settings": settings.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [metric.model_dump(mode="json") for metric in tracker.metrics],
        },
        sort_keys=True,
    )
    for secret_value in secret_values:
        assert secret_value not in serialized_surfaces


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


def test_procedural_memory_path_defaults_to_the_runtime_registry(
    config_path: Path,
) -> None:
    settings = load_config(str(config_path))

    assert settings.memory.procedural.strategies_path == "memory/strategies.json"


def test_agent_runtime_defaults_bound_every_react_loop(config_path: Path) -> None:
    settings = load_config(str(config_path))

    assert settings.agents.max_iterations == 5
    assert settings.agents.tool_budget == 10
    assert settings.agents.prompt_context_entries == 8


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_iterations", 0),
        ("tool_budget", -1),
        ("prompt_context_entries", -1),
    ],
)
def test_agent_runtime_config_rejects_unbounded_values(
    field_name: str,
    invalid_value: int,
) -> None:
    """An agent loop with no upper bound is not a valid configuration."""
    from pydantic import ValidationError

    from deep_research.utils.config import AgentRuntimeConfig

    with pytest.raises(ValidationError):
        AgentRuntimeConfig(**{field_name: invalid_value})
