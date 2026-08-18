"""Researcher evaluation cases: coverage, conflict, and partial recovery."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import SCENARIOS

CONTROLLED = (
    "multi-source-coverage",
    "conflicting-evidence",
    "partial-search-failure",
)

_METRICS = {
    "multi-source-coverage": (
        ("sub_topic_coverage", 0.30),
        ("source_grounding", 0.30),
        ("source_diversity", 0.25),
        ("budget_respected", 0.15),
    ),
    "conflicting-evidence": (
        ("uncertainty_preserved", 0.40),
        ("no_false_consensus", 0.25),
        ("source_grounding", 0.20),
        ("budget_respected", 0.15),
    ),
    "partial-search-failure": (
        ("partial_results_present", 0.35),
        ("failure_recorded", 0.30),
        ("no_invented_sources", 0.20),
        ("budget_respected", 0.15),
    ),
    "researcher-live-evidence": (
        ("sources_are_real_urls", 0.30),
        ("sub_topic_coverage", 0.30),
        ("source_diversity", 0.25),
        ("budget_respected", 0.15),
    ),
}

_MULTI_SOURCE_URLS = (
    "https://www.nrel.gov/heat-pump-cop-below-freezing",
    "https://www.iea.org/heat-pump-cop-report",
    "https://www.sciencedirect.com/heat-pump-cop-review",
    "https://www.nrel.gov/cold-climate-field-trial",
    "https://www.iea.org/cold-climate-heat-pumps",
    "https://www.sciencedirect.com/cold-climate-trial-results",
    "https://www.nrel.gov/backup-heating-guidance",
    "https://www.iea.org/backup-heating-report",
    "https://www.sciencedirect.com/backup-heating-analysis",
)

_GAIN_URL = "https://www.aeaweb.org/four-day-work-week-trial"
_NO_CHANGE_URL = "https://www.aeaweb.org/four-day-work-week-replication"
_CONFOUNDED_URL = "https://www.nber.org/four-day-self-selection"

_REFERENCES = {
    "multi-source-coverage": {
        "sub_topic_titles": [
            "Coefficient of performance below freezing",
            "Cold-climate field trial outcomes",
            "Backup heating requirements",
        ],
        "minimum_findings_per_sub_topic": 1,
        "minimum_distinct_domains": 3,
    },
    "conflicting-evidence": {
        "conflicting_urls": [_GAIN_URL, _NO_CHANGE_URL],
        "required_uncertainty_signals": [
            "disagree",
            "mixed",
            "confounded",
            "self-selected",
        ],
    },
    "partial-search-failure": {
        "failing_sub_topic": "Moisture-driven degradation",
        "recoverable_sources": ["web_search", "web_scraper"],
    },
}


def _all_researcher_cases():
    return [*cases_for("researcher", "controlled"), *cases_for("researcher", "live")]


def _case(case_id: str):
    return next(item for item in _all_researcher_cases() if item.case_id == case_id)


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("researcher", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("researcher", "live")

    assert len(live) == 1
    assert live[0].case_id == "researcher-live-evidence"


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("researcher", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("researcher", "live")[0]

    assert live.dependency_scenario == "live"


@pytest.mark.parametrize("case_id", CONTROLLED)
def test_each_case_declares_weighted_metrics(case_id: str) -> None:
    case = _case(case_id)

    assert case.expectations.deterministic_metrics
    assert (
        abs(
            sum(
                metric.weight
                for metric in case.expectations.deterministic_metrics
            )
            - 1.0
        )
        < 1e-9
    )


def test_the_failure_case_requires_a_recorded_recoverable_error() -> None:
    case = _case("partial-search-failure")

    assert case.expectations.must_record_recoverable_error is True


def test_every_controlled_case_carries_populated_sub_topics() -> None:
    """The Researcher reads state.sub_topics, so every controlled case must
    ship a non-empty, priority-ordered list."""
    for case in cases_for("researcher", "controlled"):
        assert case.state.sub_topics, case.case_id
        priorities = [topic.priority for topic in case.state.sub_topics]
        assert priorities == sorted(priorities), case.case_id


def test_no_case_state_contains_a_url_outside_its_known_sources() -> None:
    """Citations are gated against ``known_source_urls``; a case whose own
    state cites a URL it does not declare would fail its own gate."""
    for case in cases_for("researcher", "controlled"):
        declared = set(case.expectations.known_source_urls)
        cited = {
            finding.source_url for finding in case.state.raw_findings
        } | {source.url for source in case.state.evaluated_sources}
        assert cited <= declared, case.case_id


def test_live_cases_declare_the_real_dependencies_they_need() -> None:
    live = cases_for("researcher", "live")[0]

    assert live.expectations.required_live_dependencies == ["tavily", "http"]


@pytest.mark.parametrize("case_id", sorted(_METRICS))
def test_each_case_pins_its_metric_ids_and_weights(case_id: str) -> None:
    case = _case(case_id)

    assert tuple(
        (metric.metric_id, metric.weight)
        for metric in case.expectations.deterministic_metrics
    ) == _METRICS[case_id]


@pytest.mark.parametrize("case_id", sorted(_REFERENCES))
def test_each_case_pins_its_reference(case_id: str) -> None:
    case = _case(case_id)

    assert case.expectations.reference == _REFERENCES[case_id]


def test_each_case_has_its_own_judge_rubric_instance() -> None:
    """build_case does not copy judge_rubric, so sharing one instance across
    cases would let one case's mutations leak into the others."""
    cases = _all_researcher_cases()

    assert len({id(case.judge_rubric) for case in cases}) == len(cases)


@pytest.mark.parametrize(
    ("case_id", "dimensions"),
    [
        (
            "multi-source-coverage",
            {"evidence_grounding", "source_diversity"},
        ),
        (
            "conflicting-evidence",
            {
                "evidence_grounding",
                "source_diversity",
                "disagreement_handling",
            },
        ),
        (
            "partial-search-failure",
            {
                "evidence_grounding",
                "source_diversity",
                "failure_transparency",
            },
        ),
        (
            "researcher-live-evidence",
            {"evidence_grounding", "source_diversity"},
        ),
    ],
)
def test_each_case_pins_its_rubric_dimensions(
    case_id: str, dimensions: set[str]
) -> None:
    case = _case(case_id)

    assert {
        dimension.dimension_id
        for dimension in case.judge_rubric.agent_dimensions
    } == dimensions


def test_the_multi_source_case_declares_all_nine_scripted_urls() -> None:
    case = _case("multi-source-coverage")

    assert tuple(case.expectations.known_source_urls) == _MULTI_SOURCE_URLS


def test_the_multi_source_case_scripts_one_search_per_subtopic_query() -> None:
    script = SCENARIOS["researcher-multi-source"]

    assert set(script.search_responses) == {
        "heat pump COP -15C field data",
        "cold climate heat pump field trial results",
        "heat pump backup resistance heating cold climate",
    }
    assert tuple(script.http_pages) == _MULTI_SOURCE_URLS
    assert tuple(script.scripted_search_urls) == _MULTI_SOURCE_URLS
    for query, response in script.search_responses.items():
        urls = [result["url"] for result in response["results"]]
        assert len(urls) == 3, query
        assert len({urlsplit(url).netloc for url in urls}) == 3, query
        assert {urlsplit(url).netloc for url in urls} == {
            "www.nrel.gov",
            "www.iea.org",
            "www.sciencedirect.com",
        }, query


def test_the_conflicting_case_scripts_disagreeing_pages() -> None:
    script = SCENARIOS["researcher-conflicting"]
    pages = " ".join(
        script.http_pages[url] for url in (_GAIN_URL, _NO_CHANGE_URL)
    )
    pages += " " + script.http_pages[_CONFOUNDED_URL]

    assert "22%" in script.http_pages[_GAIN_URL]
    assert "no significant change" in script.http_pages[_NO_CHANGE_URL]
    assert "self-selected" in script.http_pages[_CONFOUNDED_URL]
    assert "confounded" in script.http_pages[_CONFOUNDED_URL]
    assert all(
        signal in pages for signal in ("disagree", "mixed", "confounded")
    )
    assert set(script.http_pages) == {
        _GAIN_URL,
        _NO_CHANGE_URL,
        _CONFOUNDED_URL,
    }
    assert tuple(script.scripted_search_urls) == (
        _GAIN_URL,
        _NO_CHANGE_URL,
        _CONFOUNDED_URL,
    )


def test_the_partial_failure_case_scripts_runtime_errors_only() -> None:
    """Scripted failures must not be httpx exceptions: the real tools retry
    httpx timeouts and status errors (three attempts each), which would
    triple-count a scripted failure in the call ledger."""
    import httpx

    script = SCENARIOS["researcher-partial-failure"]

    search_failure = script.search_responses[
        "perovskite moisture-driven degradation"
    ]
    assert isinstance(search_failure, RuntimeError)
    assert "search backend unavailable" in str(search_failure)

    page_failure = script.http_pages[
        "https://www.sciencedirect.com/perovskite-encapsulation-review"
    ]
    assert isinstance(page_failure, RuntimeError)
    assert "connection reset" in str(page_failure)

    injected = [
        value
        for value in [
            *script.search_responses.values(),
            *script.http_pages.values(),
        ]
        if isinstance(value, Exception)
    ]
    assert injected
    assert all(not isinstance(item, httpx.HTTPError) for item in injected)


def test_the_partial_failure_case_lets_some_evidence_survive() -> None:
    script = SCENARIOS["researcher-partial-failure"]

    surviving = script.search_responses[
        "perovskite encapsulation approaches lifetime"
    ]
    assert [result["url"] for result in surviving["results"]] == [
        "https://www.nrel.gov/perovskite-encapsulation-study",
        "https://www.sciencedirect.com/perovskite-encapsulation-review",
    ]
    assert (
        script.http_pages["https://www.nrel.gov/perovskite-encapsulation-study"]
        .strip()
    )
