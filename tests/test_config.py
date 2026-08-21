"""Tests for configuration loading."""

import json
import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from deep_research.observability import Tracker
from deep_research.utils.config import (
    ConfigSettings,
    EffectiveModelConfig,
    EvaluationConfig,
    LLMConfig,
    MissingSecretsError,
    apply_config_overrides,
    load_config,
)


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
                    "thinking_mode": "disabled",
                    "reasoning_effort": "none",
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
                    "observation_summary_chars": 200,
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
        "DEEPSEEK_API_KEY",
        "TAVILY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    (config_path.parent / ".env").write_text(
        "\n".join(
            (
                "OPENAI_API_KEY=dotenv-openai",
                "DEEPSEEK_API_KEY=dotenv-deepseek",
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


def test_llm_defaults_select_deepseek_reasoning() -> None:
    llm = LLMConfig()

    assert llm.provider == "deepseek"
    assert llm.resolve_for(None) == EffectiveModelConfig(
        model="deepseek-v4-flash",
        thinking_mode="enabled",
        reasoning_effort="high",
    )


def test_agent_model_overrides_support_string_and_structured_forms() -> None:
    llm = LLMConfig(
        model_overrides={
            "planner": "deepseek-v4-pro",
            "critic": {
                "thinking_mode": "enabled",
                "reasoning_effort": "max",
            },
        }
    )

    assert llm.resolve_for("planner") == EffectiveModelConfig(
        model="deepseek-v4-pro",
        thinking_mode="enabled",
        reasoning_effort="high",
    )
    assert llm.resolve_for("critic") == EffectiveModelConfig(
        model="deepseek-v4-flash",
        thinking_mode="enabled",
        reasoning_effort="max",
    )
    assert llm.resolve_for("researcher") == llm.resolve_for(None)


def test_effective_model_config_is_immutable() -> None:
    effective = LLMConfig().resolve_for("planner")

    with pytest.raises(ValidationError):
        effective.model = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("provider", ["anthropic", "", "DEEPSEEK"])
def test_provider_is_a_closed_lowercase_configuration_value(provider: str) -> None:
    with pytest.raises(ValidationError):
        LLMConfig(provider=provider)


def test_structured_agent_override_rejects_provider_field() -> None:
    with pytest.raises(ValidationError, match="provider"):
        LLMConfig(model_overrides={"critic": {"provider": "openai"}})


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


@pytest.mark.parametrize(
    ("name", "value", "attribute"),
    [
        ("LLM_PROVIDER", "openai", "provider"),
        ("LLM_MODEL", "gpt-5.6", "model"),
        ("LLM_THINKING_MODE", "disabled", "thinking_mode"),
        ("LLM_REASONING_EFFORT", "max", "reasoning_effort"),
    ],
)
def test_chat_environment_overrides(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    attribute: str,
) -> None:
    monkeypatch.setenv(name, value)
    assert getattr(load_config(str(config_path)).llm, attribute) == value


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


def test_stale_reasoning_mode_key_under_llm_is_rejected(config_path: Path) -> None:
    """A stale ``reasoning_mode`` key under ``llm`` must fail loudly.

    ``reasoning_mode`` is exactly the key this branch removed from
    ``LLMConfig``. Before every config model set ``extra="forbid"``, a
    config file that still carried it loaded silently with default
    behaviour standing in for the ignored setting -- no signal that the key
    did nothing.
    """
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["llm"]["reasoning_mode"] = "pro"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(str(config_path))


@pytest.mark.parametrize(
    ("environment_name", "expected_path", "value", "expected_value"),
    [
        ("LLM_PROVIDER", ("llm", "provider"), "deepseek", "deepseek"),
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
        (
            "AGENTS_OBSERVATION_SUMMARY_CHARS",
            ("agents", "observation_summary_chars"),
            "80",
            80,
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
    ["DEEPSEEK_API_KEY", "TAVILY_API_KEY"],
)
def test_load_config_strict_missing_secrets_raise_the_typed_error(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    missing_environment_name: str,
) -> None:
    """Strict-mode missing secrets surface as ``MissingSecretsError``."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    for environment_name in (
        "DEEPSEEK_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")
    monkeypatch.delenv(missing_environment_name)

    with pytest.raises(MissingSecretsError, match=missing_environment_name):
        load_config(str(config_path), strict=True)


@pytest.mark.parametrize(
    "missing_environment_name",
    ["DEEPSEEK_API_KEY", "TAVILY_API_KEY"],
)
def test_load_config_strict_always_requires_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    missing_environment_name: str,
) -> None:
    """Strict mode rejects any missing required runtime secret."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    for environment_name in (
        "DEEPSEEK_API_KEY",
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
        "DEEPSEEK_API_KEY",
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
        "DEEPSEEK_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "configured")

    settings = load_config(str(config_path), strict=True)

    assert settings.llm.provider == "openai"


def test_strict_deepseek_requires_only_the_chat_key_with_local_embeddings(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    with pytest.raises(MissingSecretsError, match="DEEPSEEK_API_KEY"):
        load_config(str(config_path), strict=True)


def test_strict_openai_does_not_require_deepseek_key(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    assert load_config(str(config_path), strict=True).llm.provider == "openai"


def test_config_settings_excludes_provider_secrets(config_path: Path) -> None:
    """Provider secrets are validated at runtime, not stored in settings."""
    settings = load_config(str(config_path))

    assert "DEEPSEEK_API_KEY" not in settings.model_dump()
    assert "OPENAI_API_KEY" not in settings.model_dump()
    assert "LANGSMITH_API_KEY" not in settings.model_dump()
    assert "TAVILY_API_KEY" not in settings.model_dump()


@pytest.mark.asyncio
async def test_dotenv_api_keys_are_absent_from_settings_and_tracker_records(
    monkeypatch: pytest.MonkeyPatch, config_path: Path
) -> None:
    """Loaded API-key values never enter serializable config or telemetry."""
    secret_values = (
        "dotenv-deepseek-secret",
        "dotenv-openai-secret",
        "dotenv-tavily-secret",
        "dotenv-langsmith-secret",
    )
    for environment_name in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    (config_path.parent / ".env").write_text(
        "\n".join(
            (
                f"DEEPSEEK_API_KEY={secret_values[0]}",
                f"OPENAI_API_KEY={secret_values[1]}",
                f"TAVILY_API_KEY={secret_values[2]}",
                f"LANGSMITH_API_KEY={secret_values[3]}",
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
    assert settings.agents.observation_summary_chars == 200


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_iterations", 0),
        ("tool_budget", -1),
        ("prompt_context_entries", -1),
        ("observation_summary_chars", 0),
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


def test_graph_settings_default_to_a_bounded_uncheckpointed_run() -> None:
    settings = ConfigSettings()

    assert settings.graph.max_iterations == 3
    assert settings.graph.checkpointing_enabled is False


def test_graph_settings_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"graph": {"max_iterations": 5, "checkpointing_enabled": True}}
        ),
        encoding="utf-8",
    )

    settings = load_config(str(path))

    assert settings.graph.max_iterations == 5
    assert settings.graph.checkpointing_enabled is True


def test_graph_settings_take_environment_overrides(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_MAX_ITERATIONS", "7")
    monkeypatch.setenv("GRAPH_CHECKPOINTING_ENABLED", "true")

    settings = load_config(str(config_path))

    assert settings.graph.max_iterations == 7
    assert settings.graph.checkpointing_enabled is True


def test_a_zero_iteration_budget_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"graph": {"max_iterations": 0}}), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        load_config(str(path))


def test_request_overrides_deep_merge_after_environment(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_MAX_ITERATIONS", "4")

    settings = load_config(
        str(config_path),
        overrides={
            "graph": {"max_iterations": 2},
            "llm": {"model_overrides": {"critic": "gpt-4.1-mini"}},
        },
    )

    assert settings.graph.max_iterations == 2
    assert settings.llm.model == "gpt-4o"
    assert settings.llm.model_overrides == {
        "planner": "gpt-4o-mini",
        "critic": "gpt-4.1-mini",
    }


def test_unknown_config_override_is_rejected(config_path: Path) -> None:
    with pytest.raises(ValueError, match=r"graph\.iteration_limit"):
        load_config(
            str(config_path),
            overrides={"graph": {"iteration_limit": 5}},
        )


def test_config_override_does_not_mutate_the_original_settings() -> None:
    original = ConfigSettings()
    overridden = apply_config_overrides(
        original,
        {"output": {"directory": "api-output/"}},
    )

    assert original.output.directory == "output/"
    assert overridden.output.directory == "api-output/"


def test_evaluation_defaults_match_the_approved_baseline() -> None:
    evaluation = ConfigSettings().evaluation

    assert evaluation.controlled_repetitions == 3
    assert evaluation.controlled_case_average_threshold == 0.80
    assert evaluation.controlled_repetition_floor == 0.65
    assert evaluation.live_repetitions == 1
    assert evaluation.live_threshold == 0.75
    assert evaluation.target_model == "deepseek-v4-flash"
    assert evaluation.target_reasoning_effort == "max"
    assert evaluation.target_reasoning_effort_overrides == {
        "planner": "max",
        "researcher": "high",
        "source_evaluator": "high",
        "fact_checker": "max",
        "synthesizer": "max",
        "critic": "max",
    }
    assert evaluation.judge_model == "deepseek-v4-flash"
    assert evaluation.judge_reasoning_effort == "max"
    assert evaluation.embedding_provider is None
    assert evaluation.embedding_model is None
    assert evaluation.judge_temperature == 0.0
    assert evaluation.max_concurrency == 1
    assert evaluation.output_directory == "output/evaluations/"
    assert evaluation.dataset_version == 1
    assert evaluation.rubric_version == 1


def test_evaluation_rejects_an_unknown_override_key() -> None:
    with pytest.raises(ValueError) as caught:
        EvaluationConfig(
            target_reasoning_effort_overrides={"librarian": "low"}
        )

    assert "librarian" in str(caught.value)


def test_evaluation_rejects_an_invalid_effort() -> None:
    with pytest.raises(ValueError):
        EvaluationConfig(target_reasoning_effort="turbo")


def test_evaluation_rejects_concurrency_above_one() -> None:
    """Sequential execution is the whole point; a knob that breaks it is a bug."""
    with pytest.raises(ValueError):
        EvaluationConfig(max_concurrency=4)


def test_the_shipped_config_file_carries_the_evaluation_block() -> None:
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    assert raw["evaluation"]["target_model"] == "deepseek-v4-flash"
    assert raw["evaluation"]["judge_reasoning_effort"] == "max"
    assert raw["evaluation"]["max_concurrency"] == 1


def test_the_embedding_provider_defaults_to_local_and_is_independent_of_chat() -> None:
    from deep_research.utils.config import LLMConfig

    assert LLMConfig().embedding_provider == "local"
    assert LLMConfig(provider="openai").embedding_provider == "local"
    assert LLMConfig(embedding_provider="openai").provider == "deepseek"


def test_strict_mode_requires_no_openai_key_for_the_default_stack(
    monkeypatch, config_path
) -> None:
    """DeepSeek chat plus local embeddings needs no OpenAI credential."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    # ``config_path`` configures ``provider: openai`` for the other tests in
    # this file; the default stack under test here is deepseek chat plus
    # local embeddings, so exercise it against the schema defaults the same
    # way ``test_strict_deepseek_requires_only_the_chat_key_with_local_embeddings``
    # does.
    config_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    settings = load_config(str(config_path), strict=True)

    assert settings.llm.embedding_provider == "local"


def test_strict_mode_requires_the_openai_key_only_for_openai_embeddings(
    monkeypatch, config_path
) -> None:
    """Selecting ``embedding_provider: openai`` requires OPENAI_API_KEY even
    when the chat provider is not openai, proving embedding-provider
    selection is validated independently of the chat provider."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.setenv("LLM_EMBEDDING_PROVIDER", "openai")
    # ``config_path`` configures ``provider: openai`` for the other tests in
    # this file; reset it to the schema defaults (chat provider deepseek) so
    # this test exercises deepseek chat + openai embeddings, not openai chat
    # + openai embeddings, the same way
    # ``test_strict_mode_requires_no_openai_key_for_the_default_stack`` does.
    config_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    with pytest.raises(MissingSecretsError, match="OPENAI_API_KEY"):
        load_config(str(config_path), strict=True)
