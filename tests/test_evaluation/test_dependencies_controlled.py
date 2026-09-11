"""Controlled dependency bundles: deterministic, isolated, and guarded."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from deep_research.agents.sources import normalize_source_url
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    SCENARIOS,
    DependencyRecorder,
    ProhibitedDependencyError,
    _FingerprintingTool,
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

# The registry is the normal source for these fixtures. The fallback keeps
# the bundle tests useful if they are run against a minimal case catalog, but
# the populated campaign registry always wins and exercises real cases.

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
async def test_an_unscripted_search_returns_a_typed_scenario_miss(
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
    assert result.error is not None
    assert result.error.type == "ScenarioMissError"
    assert ledger.prohibited_calls == []
    assert ledger.scenario_misses == [
        "web_search: something nobody scripted"
    ]
    assert any(
        summary.tool_name == "web_search" and summary.failures == 1
        for summary in ledger.tool_calls
    )


@pytest.mark.asyncio
async def test_an_unscripted_search_is_a_scenario_miss_not_prohibited_access(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    """A fake-query miss is telemetry, not evidence of real-service access."""
    bundle = build(runtime_config_for, tracker, settings, tmp_path, planner_case)
    search = next(
        tool for tool in bundle.tools if tool.name == "web_search"
    )

    async with tracker.session_span("evaluation-1", "q"):
        result = await search.execute(query="something nobody scripted")

    ledger = bundle.recorder.ledger()
    assert result.success is False
    assert ledger.prohibited_calls == []
    assert ledger.scenario_contract_version == 2
    assert ledger.scenario_misses == [
        "web_search: something nobody scripted"
    ]
    assert ledger.real_services_used == []


@pytest.mark.asyncio
async def test_a_long_unscripted_search_is_bounded_in_scenario_telemetry(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    bundle = build(runtime_config_for, tracker, settings, tmp_path, planner_case)
    search = next(
        tool for tool in bundle.tools if tool.name == "web_search"
    )
    query = "q" * 4096

    async with tracker.session_span("evaluation-1", "q"):
        result = await search.execute(query=query)

    assert result.success is False
    misses = bundle.recorder.ledger().scenario_misses
    assert len(misses) == 1
    assert len(misses[0]) <= 256
    assert misses[0].startswith("web_search: q")
    assert misses[0] != f"web_search: {query}"


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


def test_source_payload_telemetry_fingerprints_all_remote_source_shapes() -> None:
    recorder = DependencyRecorder()
    urls = [
        "https://www.example.com/search-result/",
        "https://example.com/article",
        "https://example.com/report.pdf",
    ]

    recorder.record_source_url_payload(
        "web_search", {"results": [{"url": urls[0]}]}
    )
    recorder.record_source_url_payload("web_scraper", {"url": urls[1]})
    recorder.record_source_url_payload(
        "document_reader", {"source": urls[2]}
    )
    recorder.record_source_url_payload(
        "document_reader", {"source": "C:/local/report.pdf"}
    )

    fingerprints = recorder.ledger().source_url_fingerprints
    expected = [
        sha256(normalize_source_url(url).encode("utf-8")).hexdigest()
        for url in urls
    ]
    assert fingerprints == expected
    assert all(url not in repr(fingerprints) for url in urls)


def test_source_payload_telemetry_rejects_invalid_and_unrelated_identities() -> None:
    recorder = DependencyRecorder()
    search_url = "https://example.com/search-result"
    scraper_url = "https://example.com/article"
    document_url = "https://example.com/report.pdf"
    unrelated_url = "https://example.com/unrelated"
    invalid_urls = [
        "ftp://example.com/not-http",
        "https://",
        "https://[::1",
        "https://example.com:99999/page",
        "https://example.com:not-a-port/page",
        "example.com/no-scheme",
    ]

    recorder.record_source_url_payload(
        "web_search",
        {
            "results": [
                {"url": search_url},
                *({"url": url} for url in invalid_urls),
                {"link": unrelated_url},
            ],
            "url": unrelated_url,
        },
    )
    recorder.record_source_url_payload(
        "web_scraper",
        {"url": scraper_url, "source": unrelated_url},
    )
    recorder.record_source_url_payload(
        "document_reader",
        {"source": document_url, "url": unrelated_url},
    )
    for invalid_url in invalid_urls:
        recorder.record_source_url_payload(
            "web_scraper", {"url": invalid_url}
        )
        recorder.record_source_url_payload(
            "document_reader", {"source": invalid_url}
        )

    fingerprints = recorder.ledger().source_url_fingerprints
    expected = [
        sha256(normalize_source_url(url).encode("utf-8")).hexdigest()
        for url in (search_url, scraper_url, document_url)
    ]

    assert fingerprints == expected
    assert all(url not in repr(fingerprints) for url in invalid_urls)
    assert unrelated_url not in repr(fingerprints)


@pytest.mark.asyncio
async def test_fingerprinting_proxy_ignores_failed_source_results(tracker) -> None:
    recorder = DependencyRecorder()

    class FailedSourceTool:
        name = "web_search"
        description = "failed source tool"
        input_schema = {}
        output_schema = {"results": "array"}

        async def execute(self, **kwargs):
            del kwargs
            return type(
                "FailedResult",
                (),
                {
                    "success": False,
                    "data": {"results": [{"url": "https://example.com/failed"}]},
                },
            )()

    proxy = _FingerprintingTool(
        FailedSourceTool(), tracker, recorder=recorder
    )

    result = await proxy.execute(query="failed")

    assert result.success is False
    assert recorder.ledger().source_url_fingerprints == []


def test_source_fingerprint_overflow_is_explicit() -> None:
    recorder = DependencyRecorder()
    urls = [f"https://example.com/source-{index}" for index in range(129)]

    recorder.record_source_url_fingerprints(urls)

    ledger = recorder.ledger()
    assert len(ledger.source_url_fingerprints) == 128
    assert ledger.source_url_fingerprints_complete is False
    artifact = ledger.model_dump(mode="json")
    assert artifact["source_url_fingerprints_complete"] is False
    assert DependencyRecorder().ledger().source_url_fingerprints_complete is True


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
