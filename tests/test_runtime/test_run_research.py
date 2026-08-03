"""Tests for the shared run_research entry point every front-end calls."""

from __future__ import annotations

import pytest
import yaml

from deep_research.graph.orchestrator import compile_research_graph
from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    SUPPORTED_OUTPUT_FORMATS,
    resolve_output_format,
    run_research,
    run_research_sync,
)
from deep_research.runtime.assembly import ResearchRuntime
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from tests.graph_fakes import FakeAgent, fake_critique, fake_research_agents

QUESTION = "How mature is quantum error correction?"


@pytest.fixture
def config_file(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    payload = {
        "graph": {"max_iterations": 2, "checkpointing_enabled": False},
        "output": {"directory": str(tmp_path / "output"), "default_format": "markdown"},
        "memory": {
            "long_term": {"persist_directory": str(tmp_path / "memory")},
            "procedural": {"strategies_path": str(tmp_path / "strategies.json")},
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return str(path)


def fake_builder(tracker, *, agents=None, checkpointer=None):
    """Return a runtime_builder that skips providers, memory, and tools."""

    async def build(settings, *, session_id, **_ignored):
        return ResearchRuntime(
            session_id=session_id,
            settings=settings,
            tracker=tracker,
            graph=compile_research_graph(
                agents or fake_research_agents(), checkpointer=checkpointer
            ),
            long_term=None,
            procedural=None,
        )

    return build


def test_the_default_config_path_is_the_repository_config() -> None:
    assert DEFAULT_CONFIG_PATH == "config.yaml"


def test_markdown_is_the_only_supported_output_format() -> None:
    assert SUPPORTED_OUTPUT_FORMATS == ("markdown",)


def test_resolve_output_format_falls_back_to_the_configured_default() -> None:
    assert resolve_output_format(None, configured="markdown") == "markdown"
    assert resolve_output_format("markdown", configured="markdown") == "markdown"


def test_resolve_output_format_rejects_an_unsupported_format() -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        resolve_output_format("pdf", configured="markdown")

    assert caught.value.reason == "unsupported_output_format"
    assert "pdf" in str(caught.value)


@pytest.mark.asyncio
async def test_a_successful_run_returns_an_outcome(config_file, tracker) -> None:
    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert isinstance(outcome, ResearchOutcome)
    assert outcome.question == QUESTION
    assert outcome.status == "completed"
    assert outcome.report == "# Research report: pass 1"
    assert outcome.session_id


@pytest.mark.asyncio
async def test_the_supplied_session_id_is_used(config_file, tracker) -> None:
    outcome = await run_research(
        QUESTION,
        session_id="session-fixed",
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert outcome.session_id == "session-fixed"
    assert outcome.state.session_id == "session-fixed"


@pytest.mark.asyncio
async def test_generated_session_ids_are_unique(config_file, tracker) -> None:
    first = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )
    second = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert first.session_id != second.session_id


@pytest.mark.asyncio
async def test_max_iterations_overrides_the_configured_budget(
    config_file, tracker
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True)}]
        )
    )

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        max_iterations=1,
        runtime_builder=fake_builder(tracker, agents=agents),
    )

    assert outcome.status == "max_iterations"
    assert outcome.state.max_iterations == 1


@pytest.mark.asyncio
async def test_the_configured_budget_is_used_when_none_is_passed(
    config_file, tracker
) -> None:
    outcome = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert outcome.state.max_iterations == 2


@pytest.mark.asyncio
async def test_a_missing_config_file_is_a_configuration_failure(tracker) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION,
            config_path="no-such-config.yaml",
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "config_file_missing"


@pytest.mark.asyncio
async def test_missing_api_keys_fail_fast(tmp_path, monkeypatch, tracker) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({}), encoding="utf-8")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION,
            config_path=str(path),
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "missing_secrets"
    assert "OPENAI_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_yaml_is_a_configuration_failure(
    tmp_path, monkeypatch, tracker
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    path = tmp_path / "config.yaml"
    path.write_text("not: [valid", encoding="utf-8")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION, config_path=str(path), runtime_builder=fake_builder(tracker)
        )

    assert caught.value.reason == "config_invalid"


@pytest.mark.asyncio
async def test_no_question_and_no_resume_is_a_configuration_failure(
    config_file, tracker
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            config_path=config_file, runtime_builder=fake_builder(tracker)
        )

    assert caught.value.reason == "no_question"


@pytest.mark.asyncio
async def test_a_whitespace_only_question_is_a_configuration_failure(
    config_file, tracker
) -> None:
    async def builder(settings, *, session_id, **_ignored):
        raise AssertionError("blank inputs must fail before runtime setup")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            "   ", config_path=config_file, runtime_builder=builder
        )

    assert caught.value.reason == "no_question"
    assert "blank" in str(caught.value)


@pytest.mark.asyncio
async def test_a_whitespace_only_resume_session_id_is_a_configuration_failure(
    config_file, tracker
) -> None:
    async def builder(settings, *, session_id, **_ignored):
        raise AssertionError("blank inputs must fail before runtime setup")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            resume_session_id="   ", config_path=config_file, runtime_builder=builder
        )

    assert caught.value.reason == "blank_session_id"
    assert "resume" in str(caught.value)


@pytest.mark.asyncio
async def test_a_whitespace_only_session_id_is_a_configuration_failure(
    config_file, tracker
) -> None:
    async def builder(settings, *, session_id, **_ignored):
        raise AssertionError("blank inputs must fail before runtime setup")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION, session_id="   ", config_path=config_file, runtime_builder=builder
        )

    assert caught.value.reason == "blank_session_id"


@pytest.mark.asyncio
async def test_question_whitespace_is_normalized_before_the_run(
    config_file, tracker
) -> None:
    outcome = await run_research(
        f"  {QUESTION}  ",
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert outcome.question == QUESTION


@pytest.mark.asyncio
async def test_question_and_resume_together_get_their_own_reason(
    config_file, tracker
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION,
            resume_session_id="session-1",
            config_path=config_file,
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "question_and_resume"
    assert "already has its question" in caught.value.hint


@pytest.mark.asyncio
async def test_resume_without_a_checkpoint_reports_the_known_limitation(
    config_file, tracker
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            resume_session_id="session-gone",
            config_path=config_file,
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "no_checkpoint"
    assert "session-gone" in str(caught.value)


@pytest.mark.asyncio
async def test_resume_of_a_session_that_never_ran_reports_no_checkpoint(
    config_file, tracker
) -> None:
    """A checkpointer is active, but no checkpoint exists for the session."""
    from deep_research.graph.orchestrator import build_checkpointer

    builder = fake_builder(tracker, checkpointer=build_checkpointer(enabled=True))

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            resume_session_id="never-ran",
            config_path=config_file,
            runtime_builder=builder,
        )

    assert caught.value.reason == "no_checkpoint"


@pytest.mark.asyncio
async def test_resume_works_against_a_live_checkpoint(config_file, tracker) -> None:
    """Resume is real; only its cross-process durability is missing."""
    from deep_research.graph.orchestrator import build_checkpointer

    builder = fake_builder(tracker, checkpointer=build_checkpointer(enabled=True))
    shared: dict[str, object] = {}

    async def remembering_builder(settings, *, session_id, **kwargs):
        runtime = shared.get("runtime")
        if runtime is None:
            runtime = await builder(settings, session_id=session_id, **kwargs)
            shared["runtime"] = runtime
        return runtime

    first = await run_research(
        QUESTION,
        session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
    )
    resumed = await run_research(
        resume_session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
    )

    assert first.session_id == "session-1"
    assert resumed.session_id == "session-1"
    assert resumed.question == QUESTION


def test_run_research_sync_drives_the_async_entry_point(
    config_file, tracker
) -> None:
    outcome = run_research_sync(
        question=QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert outcome.status == "completed"


@pytest.mark.asyncio
async def test_run_research_applies_config_overrides(
    config_file,
    tracker,
) -> None:
    observed = {}

    async def builder(settings, *, session_id, **_ignored):
        observed["directory"] = settings.output.directory
        return await fake_builder(tracker)(settings, session_id=session_id)

    await run_research(
        QUESTION,
        config_path=config_file,
        config_overrides={"output": {"directory": "request-output/"}},
        runtime_builder=builder,
    )

    assert observed == {"directory": "request-output/"}
