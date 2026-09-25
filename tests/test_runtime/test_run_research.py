"""Tests for the shared run_research entry point every front-end calls."""

from __future__ import annotations

from types import SimpleNamespace

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
from deep_research.observability import RunTelemetryCollector
from deep_research.request_budget import RequestBudget, RequestBudgetUpdate
from deep_research.runtime.assembly import ResearchRuntime
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import ResearchEvent
from tests.graph_fakes import (
    REVIEW_DIMENSIONS,
    FakeAgent,
    FakeReviewer,
    fake_report_review,
    fake_research_agents,
    fake_sub_topic,
    fake_target,
    fake_writer_update,
)

QUESTION = "How mature is quantum error correction?"


@pytest.fixture
def config_file(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    payload = {
        "graph": {"max_extra_passes": 2, "checkpointing_enabled": False},
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
            # ``ResearchRuntime`` requires a budget because the production
            # builder always constructs exactly one for the run. A stand-in
            # that omitted it would stop exercising the same constructor the
            # runtime uses, so this builds a real (undeclared-ceiling) budget
            # rather than passing ``None``.
            request_budget=RequestBudget(),
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
    assert outcome.report is not None
    assert outcome.report.startswith(f"# {QUESTION}")
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
async def test_max_extra_passes_overrides_the_configured_budget(
    config_file, tracker
) -> None:
    """An explicit ceiling wins over ``graph.max_extra_passes`` (PD-15)."""
    topic = fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )
    agents = fake_research_agents(
        planner=FakeAgent("planner", [{"sub_topics": [topic]}]),
        report_writer=FakeAgent(
            "report_writer", [], update_factory=fake_writer_update
        ),
        report_reviewer=FakeReviewer(
            [
                fake_report_review(
                    dimensions={name: 0.5 for name in REVIEW_DIMENSIONS}
                )
            ]
        ),
    )

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        max_extra_passes=1,
        runtime_builder=fake_builder(tracker, agents=agents),
    )

    assert outcome.status == "max_iterations"
    assert outcome.state.max_extra_passes == 1
    assert outcome.state.iteration == 1


@pytest.mark.asyncio
async def test_the_configured_budget_is_used_when_none_is_passed(
    config_file, tracker
) -> None:
    outcome = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert outcome.state.max_extra_passes == 2


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
    # An empty config defaults to deepseek chat plus local embeddings, so
    # the chat secret that must be missing to exercise this path is
    # DEEPSEEK_API_KEY, not OPENAI_API_KEY (which the default stack never
    # requires).
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
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
    assert "DEEPSEEK_API_KEY" in str(caught.value)


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


@pytest.mark.asyncio
async def test_run_research_publishes_typed_progress_in_order(
    config_file,
    tracker,
) -> None:
    received = []

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
        event_handler=received.append,
    )

    assert received
    assert all(isinstance(event, ResearchEvent) for event in received)
    assert received[0].event_type == "graph.session.started"
    assert any(
        event.event_type == "graph.node.started"
        and event.metadata["node"] == "planner"
        for event in received
    )
    assert received[-1].event_type == "graph.session.completed"
    assert received == outcome.state.events


@pytest.mark.asyncio
async def test_resume_forwards_the_event_handler_and_streams_the_terminal_session(
    config_file, tracker
) -> None:
    from deep_research.graph.orchestrator import build_checkpointer

    builder = fake_builder(tracker, checkpointer=build_checkpointer(enabled=True))
    shared: dict[str, object] = {}

    async def remembering_builder(settings, *, session_id, **kwargs):
        runtime = shared.get("runtime")
        if runtime is None:
            runtime = await builder(settings, session_id=session_id, **kwargs)
            shared["runtime"] = runtime
        return runtime

    await run_research(
        QUESTION,
        session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
    )

    received = []
    resumed = await run_research(
        resume_session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
        event_handler=received.append,
    )

    assert resumed.status == "completed"
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"


def budget_runtime(
    settings, *, session_id, tracker, budget, agents=None, telemetry=None
):
    """A runtime stand-in exposing the shared run budget and its collector.

    ``ResearchRuntime`` gains ``request_budget`` in the task that owns
    ``assembly.py``; the budget surface ``run_research`` touches is only
    ``request_budget``, so a stand-in keeps this task's tests independent of
    that one. ``telemetry`` is the other surface ``run_research`` reads — the
    run's §7.3 collector, which it installs on the budget's single observer
    slot — and it defaults to ``None`` for the same reason the budget is
    explicit here: a run with no collector must behave exactly as it did
    before there was one.
    """
    return SimpleNamespace(
        session_id=session_id,
        settings=settings,
        tracker=tracker,
        graph=compile_research_graph(
            agents or fake_research_agents(), checkpointer=None
        ),
        long_term=None,
        procedural=None,
        request_budget=budget,
        run_telemetry=telemetry,
    )


def reserving_builder(tracker, budget, *, on_start=None):
    """A runtime builder whose run reserves one Tavily attempt at start."""

    async def build(settings, *, session_id, **_ignored):
        if on_start is not None:
            on_start(budget)
        return budget_runtime(
            settings, session_id=session_id, tracker=tracker, budget=budget
        )

    return build


@pytest.mark.asyncio
async def test_run_research_notifies_the_collector_and_the_handler_of_every_update(
    config_file, tracker
) -> None:
    """``RequestBudget`` holds one observer, and two parties need it.

    The CLI installs its stream here while the assembly installed the run's
    collector there, so ``run_research`` has to fan out: both see every update.
    A collector that was replaced would report a peak of zero for a run that
    had a call in flight, which is the figure §7.3 exists to report.
    """
    budget = RequestBudget()
    collector = RunTelemetryCollector()
    peaks: list[int] = []
    handler_kinds: list[str] = []

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings,
            session_id=session_id,
            tracker=tracker,
            budget=budget,
            telemetry=collector,
        )

    def event_handler(event: ResearchEvent) -> None:
        if event.event_type == "graph.session.started":
            collector.note_call_starting("researcher")
            budget.reserve("deepseek")
            peaks.append(collector.snapshot().peak_calls_in_flight)

    def record(update: RequestBudgetUpdate) -> None:
        handler_kinds.append(update.kind)

    await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        event_handler=event_handler,
        request_budget_handler=record,
    )

    assert peaks == [1]
    assert handler_kinds == ["attempt_reserved"]


@pytest.mark.asyncio
async def test_run_research_installs_the_collector_alone_and_detaches_it(
    config_file, tracker
) -> None:
    """With no handler the collector is the observer; when the call returns it is
    gone, exactly as the handler was.

    A runtime outlives one run — a resume reuses it — so an observer left
    installed would attribute a later run's attempts to this one's collector.
    """
    budget = RequestBudget()
    collector = RunTelemetryCollector()
    peaks: list[int] = []

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings,
            session_id=session_id,
            tracker=tracker,
            budget=budget,
            telemetry=collector,
        )

    def event_handler(event: ResearchEvent) -> None:
        if event.event_type == "graph.session.started":
            collector.note_call_starting("researcher")
            budget.reserve("deepseek")
            peaks.append(collector.snapshot().peak_calls_in_flight)

    await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        event_handler=event_handler,
    )

    budget.reserve("deepseek")

    assert peaks == [1]
    assert collector.snapshot().peak_calls_in_flight == 1


@pytest.mark.asyncio
async def test_a_halted_run_still_carries_the_run_telemetry(
    config_file, tracker
) -> None:
    """A halted run is still a run that was measured.

    The terminal finalizer stamps the collector for a run that reaches
    publication, and a halted run reaches none — the node is skipped and
    publishes nothing. That is exactly the run whose telemetry matters most:
    one killed by repeated 429s or a spent attempt budget is the run the
    "rate limits hit N times" advice is for. So the entry point takes the same
    reading for the pass that had no publication step, and takes it only when
    the finalizer did not.
    """
    budget = RequestBudget()
    collector = RunTelemetryCollector()
    planner = FakeAgent("planner", [{"iteration": 2}])

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings,
            session_id=session_id,
            tracker=tracker,
            budget=budget,
            agents=fake_research_agents(planner=planner),
            telemetry=collector,
        )

    def event_handler(event: ResearchEvent) -> None:
        if event.event_type == "graph.session.started":
            collector.note_call_starting("researcher")
            budget.reserve("deepseek")

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        event_handler=event_handler,
    )

    assert outcome.failed is True
    assert outcome.state.run_telemetry is not None
    assert outcome.state.run_telemetry.peak_calls_in_flight == 1
    assert outcome.state.run_telemetry.peak_agent == "researcher"
    assert outcome.quality_path is None


@pytest.mark.asyncio
async def test_run_research_installs_the_request_budget_handler_before_the_graph(
    config_file, tracker
) -> None:
    """The observer is installed after construction, before any graph work."""
    budget = RequestBudget()
    seen: list[str] = []
    installed_when_the_graph_started: list[bool] = []

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings, session_id=session_id, tracker=tracker, budget=budget
        )

    def event_handler(event: ResearchEvent) -> None:
        if event.event_type == "graph.session.started":
            budget.reserve("tavily")
            installed_when_the_graph_started.append(bool(seen))

    def record(update: RequestBudgetUpdate) -> None:
        seen.append(update.kind)

    await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        event_handler=event_handler,
        request_budget_handler=record,
    )

    assert installed_when_the_graph_started == [True]
    assert seen == ["attempt_reserved"]


@pytest.mark.asyncio
async def test_run_research_carries_request_budget_snapshots_into_the_outcome(
    config_file, tracker
) -> None:
    budget = RequestBudget()

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings, session_id=session_id, tracker=tracker, budget=budget
        )

    def event_handler(event: ResearchEvent) -> None:
        if event.event_type == "graph.session.started":
            budget.reserve("tavily")

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        event_handler=event_handler,
    )

    by_provider = {
        snapshot.provider: snapshot
        for snapshot in outcome.request_budget_snapshots
    }
    assert [
        snapshot.provider for snapshot in outcome.request_budget_snapshots
    ] == ["deepseek", "openai", "tavily"]
    assert by_provider["tavily"].attempts == 1
    assert by_provider["deepseek"].attempts == 0
    assert by_provider["openai"].attempts == 0


@pytest.mark.asyncio
async def test_run_research_clears_the_request_budget_handler_when_it_returns(
    config_file, tracker
) -> None:
    """The runtime outlives one call; its observer must not."""
    budget = RequestBudget()
    seen: list[str] = []

    async def builder(settings, *, session_id, **_ignored):
        return budget_runtime(
            settings, session_id=session_id, tracker=tracker, budget=budget
        )

    def record(update: RequestBudgetUpdate) -> None:
        seen.append(update.kind)

    await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        request_budget_handler=record,
    )
    budget.reserve("tavily")

    assert seen == []


@pytest.mark.asyncio
async def test_run_research_applies_request_budget_config_overrides(
    config_file, tracker
) -> None:
    observed: dict[str, object] = {}

    async def builder(settings, *, session_id, **_ignored):
        observed["tavily"] = settings.request_budget.tavily_attempt_ceiling
        observed["deepseek"] = settings.request_budget.deepseek_attempt_ceiling
        observed["stop_fraction"] = settings.request_budget.stop_fraction
        return budget_runtime(
            settings,
            session_id=session_id,
            tracker=tracker,
            budget=RequestBudget(settings.request_budget),
        )

    await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=builder,
        config_overrides={
            "request_budget": {
                "tavily_attempt_ceiling": 11,
                "stop_fraction": 0.5,
            }
        },
    )

    assert observed == {"tavily": 11, "deepseek": None, "stop_fraction": 0.5}


@pytest.mark.asyncio
async def test_a_runtime_without_a_budget_fails_loudly(config_file, tracker) -> None:
    """A builder that carries no budget is a defect, not a quiet degradation.

    ``ResearchRuntime.request_budget`` is a required field, and every declared
    ceiling is enforced through it. Reading it defensively would let a runtime
    that carries no budget — or one that names it differently — complete a run
    with its ceilings enforced while the terminal summary reported no budget
    section at all, which is exactly the "the summary matches the run"
    property the budget reporting exists to guarantee. Raising here keeps that
    contradiction impossible instead of merely unlikely.
    """

    async def builder(settings, *, session_id, **_ignored):
        stand_in = budget_runtime(
            settings,
            session_id=session_id,
            tracker=tracker,
            budget=RequestBudget(),
        )
        del stand_in.request_budget
        return stand_in

    with pytest.raises(AttributeError):
        await run_research(
            QUESTION,
            config_path=config_file,
            runtime_builder=builder,
        )
