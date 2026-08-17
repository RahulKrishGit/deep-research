"""Controlled dependency bundles: deterministic, isolated, and guarded."""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    SCENARIOS,
    ProhibitedDependencyError,
    build_controlled_dependencies,
    isolated_settings,
)
from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    CaseExpectations,
    DeterministicMetric,
    EvaluationCase,
    JudgeRubric,
)
from deep_research.utils.types import ResearchState

# The registry is empty until Tasks 10-15 land the case files, so the
# fixtures below fall back to minimal sample cases whose dependency
# scenarios are the real scenario keys. The moment a case file lands, the
# registry lookup wins and these tests exercise the real cases.

_SAMPLE_CASE_IDS = {
    "planner": "focused-decomposition",
    "researcher": "multi-source-coverage",
    "source_evaluator": "strong-and-weak-sources",
    "fact_checker": "mixed-verdicts",
    "synthesizer": "complete-cited-report",
    "critic": "approve-strong-report",
}
_SAMPLE_SCENARIOS = {
    "planner": "planner-clean-memory",
    "researcher": "researcher-multi-source",
    "source_evaluator": "source-evaluator-mixed",
    "fact_checker": "fact-checker-mixed",
    "synthesizer": "synthesizer-complete",
    "critic": "critic-strong-report",
}


def _sample_case(agent_name: AgentName) -> EvaluationCase:
    case_id = _SAMPLE_CASE_IDS[agent_name]
    return EvaluationCase(
        case_id=case_id,
        version=1,
        agent_name=agent_name,
        tier="controlled",
        title=f"{agent_name} sample case",
        purpose="Sample case for the Task 7 controlled-bundle tests.",
        state=ResearchState(
            session_id=f"evaluation-{case_id}",
            original_question="Sample research question?",
        ),
        dependency_scenario=_SAMPLE_SCENARIOS[agent_name],
        expectations=CaseExpectations(
            required_output_fields=["result"],
            max_iterations=2,
            max_tool_calls=10,
            deterministic_metrics=[
                DeterministicMetric(
                    metric_id="completeness",
                    weight=1.0,
                    description="sample metric",
                )
            ],
        ),
        judge_rubric=JudgeRubric(
            rubric_id=f"{agent_name}-sample",
            version=1,
        ),
        metadata={},
    )


def _controlled_case(agent_name: AgentName) -> EvaluationCase:
    available = cases_for(agent_name, "controlled")
    if available:
        return available[0]
    return _sample_case(agent_name)


def build(runtime_config_for, tracker, settings, tmp_path, case):
    return build_controlled_dependencies(
        runtime_config_for(case.agent_name),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )


@pytest.fixture
def controlled_case_for():
    def factory(agent_name):
        return _controlled_case(agent_name)

    return factory


@pytest.fixture
def planner_case():
    return _controlled_case("planner")


@pytest.fixture
def researcher_case():
    return _controlled_case("researcher")


@pytest.fixture
def synthesizer_case():
    return _controlled_case("synthesizer")


def test_every_case_scenario_has_a_script(all_cases) -> None:
    """A case naming a scenario nobody scripted fails before any model call."""
    for case in all_cases:
        if case.tier == "controlled":
            assert case.dependency_scenario in SCENARIOS, case.case_id


def test_controlled_memory_is_an_isolated_collection(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    bundle = build(runtime_config_for, tracker, settings, tmp_path, planner_case)

    assert bundle.collection_name.startswith("evaluation_")
    assert bundle.collection_name != settings.memory.long_term.collection_name
    assert tmp_path in bundle.strategies_path.parents
    assert bundle.strategies_path != Path(
        settings.memory.procedural.strategies_path
    )


def test_controlled_documents_land_in_an_evaluation_only_directory(
    tracker, settings, tmp_path, runtime_config_for, synthesizer_case
) -> None:
    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, synthesizer_case
    )

    assert tmp_path in bundle.document_directory.parents
    assert bundle.document_directory.is_dir()
    assert Path(settings.output.directory).resolve() not in (
        bundle.document_directory.resolve(),
    )


@pytest.mark.asyncio
async def test_an_unscripted_search_is_a_prohibited_call(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    bundle = build(runtime_config_for, tracker, settings, tmp_path, planner_case)
    search = next(
        tool for tool in bundle.tools if tool.name == "web_search"
    )

    async with tracker.session_span("evaluation-1", "q"):
        result = await search.execute(query="something nobody scripted")

    ledger = bundle.recorder.ledger()
    assert result.success is False
    assert ledger.prohibited_calls
    assert any(
        summary.tool_name == "web_search" and summary.failures == 1
        for summary in ledger.tool_calls
    )


@pytest.mark.asyncio
async def test_an_unscripted_scrape_is_a_prohibited_call(
    tracker, settings, tmp_path, runtime_config_for, researcher_case
) -> None:
    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, researcher_case
    )
    scraper = next(
        tool for tool in bundle.tools if tool.name == "web_scraper"
    )

    async with tracker.session_span("evaluation-1", "q"):
        result = await scraper.execute(url="https://unscripted.example/page")

    assert result.success is False
    assert bundle.recorder.ledger().prohibited_calls


@pytest.mark.asyncio
async def test_a_scripted_search_succeeds_and_is_recorded(
    tracker, settings, tmp_path, runtime_config_for, researcher_case
) -> None:
    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, researcher_case
    )
    script = SCENARIOS[researcher_case.dependency_scenario]
    search = next(tool for tool in bundle.tools if tool.name == "web_search")
    query = next(iter(script.search_responses))

    async with tracker.session_span("evaluation-1", "q"):
        result = await search.execute(query=query)

    ledger = bundle.recorder.ledger()
    assert result.success, result.error
    assert ledger.prohibited_calls == []
    assert any(
        summary.tool_name == "web_search" and summary.calls == 1
        for summary in ledger.tool_calls
    )
    assert ledger.real_services_used == []


@pytest.mark.asyncio
async def test_a_scripted_scrape_succeeds_and_is_recorded(
    tracker, settings, tmp_path, runtime_config_for, researcher_case
) -> None:
    """A scripted scrape works end to end, robots.txt included."""
    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, researcher_case
    )
    script = SCENARIOS[researcher_case.dependency_scenario]
    scraper = next(tool for tool in bundle.tools if tool.name == "web_scraper")
    url = next(iter(script.http_pages))

    async with tracker.session_span("evaluation-1", "q"):
        result = await scraper.execute(url=url)

    ledger = bundle.recorder.ledger()
    assert result.success, result.error
    assert ledger.prohibited_calls == []
    assert any(
        summary.tool_name == "web_scraper"
        and summary.calls == 1
        and summary.failures == 0
        for summary in ledger.tool_calls
    )


def test_controlled_bundles_never_receive_a_tavily_key(
    tracker, settings, tmp_path, runtime_config_for, researcher_case, monkeypatch
) -> None:
    """Even with a key in the environment, controlled mode must not use it.

    ``WebSearchTool`` folds the key into ``TavilyClient(api_key=...)`` at
    construction rather than storing it, so the proof is constructive: the
    patched constructor raises, and building a bundle must never call it.
    """
    from deep_research.tools import web_search as web_search_module

    def explode(api_key=None, **kwargs):
        raise AssertionError(
            f"TavilyClient must not be constructed in controlled mode "
            f"(received api_key={api_key!r}, {sorted(kwargs)})"
        )

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-should-never-be-used")
    monkeypatch.setattr(web_search_module, "TavilyClient", explode)

    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, researcher_case
    )
    search = next(tool for tool in bundle.tools if tool.name == "web_search")

    assert search._client is not None


def test_controlled_tools_never_hold_a_real_network_client(
    tracker, settings, tmp_path, runtime_config_for, researcher_case
) -> None:
    """The injected clients are the scripted doubles, never Tavily/httpx.

    The tools construct a real client only when none is injected, so an
    injected double is what makes the guard constructive rather than
    advisory: no code path in controlled mode can reach the network.
    """
    import httpx
    from tavily import TavilyClient

    bundle = build(
        runtime_config_for, tracker, settings, tmp_path, researcher_case
    )
    search = next(tool for tool in bundle.tools if tool.name == "web_search")
    scraper = next(tool for tool in bundle.tools if tool.name == "web_scraper")
    reader = next(tool for tool in bundle.tools if tool.name == "document_reader")

    assert search._client is not None
    assert not isinstance(search._client, TavilyClient)
    assert scraper._client is not None
    assert not isinstance(scraper._client, httpx.AsyncClient)
    assert reader._client is not None
    assert not isinstance(reader._client, httpx.AsyncClient)


def test_each_repetition_gets_its_own_bundle(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    """No mutable collaborator may be shared between repetitions."""
    first = build_controlled_dependencies(
        runtime_config_for("planner"),
        planner_case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        repetition=1,
    )
    second = build_controlled_dependencies(
        runtime_config_for("planner"),
        planner_case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        repetition=2,
    )

    assert first.recorder is not second.recorder
    assert first.long_term is not second.long_term
    assert first.document_directory != second.document_directory
    assert first.collection_name != second.collection_name
    assert {tool.name for tool in first.tools} == {
        tool.name for tool in second.tools
    }
    assert all(
        left is not right
        for left, right in zip(first.tools, second.tools, strict=True)
    )


def test_isolated_settings_never_point_at_production_paths(
    settings, tmp_path, runtime_config_for
) -> None:
    isolated = isolated_settings(
        settings,
        runtime_config_for("planner"),
        case_id="focused-decomposition",
        repetition=1,
        root=tmp_path,
    )

    assert isolated.memory.long_term.collection_name.startswith("evaluation_")
    assert str(tmp_path) in isolated.memory.long_term.persist_directory
    assert str(tmp_path) in isolated.memory.procedural.strategies_path
    assert str(tmp_path) in isolated.output.directory
    assert isolated.agents == settings.agents  # bounds stay production's
    assert isolated.graph == settings.graph


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_a_bundle_exists_for_every_agent(
    tracker, settings, tmp_path, runtime_config_for, controlled_case_for,
    agent_name,
) -> None:
    bundle = build_controlled_dependencies(
        runtime_config_for(agent_name),
        controlled_case_for(agent_name),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )

    assert bundle.tools
    assert bundle.recorder.ledger().prohibited_calls == []


def test_prohibited_dependency_error_names_the_service() -> None:
    error = ProhibitedDependencyError("tavily", "search")

    assert "tavily" in str(error)
    assert "controlled" in str(error)
