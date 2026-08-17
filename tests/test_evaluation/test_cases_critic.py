"""Critic evaluation cases: routing, gaps, and budget discipline."""

from __future__ import annotations

import httpx
import pytest

from deep_research.agents.critic import route_decision
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    SCENARIOS,
    build_controlled_dependencies,
)
from deep_research.utils.types import Critique

CONTROLLED = (
    "approve-strong-report",
    "request-more-research",
    "missing-evidence-or-budget-exhausted",
)

_METRICS = {
    "approve-strong-report": (
        ("score_bounded", 0.20),
        ("route_consistent", 0.35),
        ("rationale_present", 0.20),
        ("no_spurious_gaps", 0.25),
    ),
    "request-more-research": (
        ("route_consistent", 0.35),
        ("gaps_actionable", 0.30),
        ("gaps_identified", 0.20),
        ("score_bounded", 0.15),
    ),
    "missing-evidence-or-budget-exhausted": (
        ("route_discipline", 0.40),
        ("conservative_score", 0.25),
        ("failure_recorded", 0.20),
        ("score_bounded", 0.15),
    ),
    "critic-live-review": (
        ("score_bounded", 0.20),
        ("route_consistent", 0.35),
        ("rationale_present", 0.20),
        ("no_spurious_gaps", 0.25),
    ),
}

# The scenario search keys are shared with the dependency scenarios: a
# scripted client answers exactly one query per controlled case, so the
# case tests pin the query literal on both sides. The gappy case's key is
# also the participation subtopic's own search query, so the case file
# carries it; the other two keys exist only in the scenario and here.

_STRONG_SEARCH_KEY = (
    "measured effect of urban tree canopy on summer surface temperature"
)
_GAPPY_SEARCH_KEY = "municipal composting mandates participation rates"
_BUDGET_SEARCH_KEY = "congestion pricing particulate pollution evidence"

# All URLs are written in the normalized form the agent records (no
# ``www.``, no trailing slash), so a gate comparison can never
# byte-mismatch.
_EPA_CANOPY_URL = "https://epa.gov/heat-islands/trees-and-vegetation"
_NOAA_CANOPY_URL = "https://noaa.gov/urban-heat-islands/tree-canopy"
_CANOPY_REVIEW_URL = (
    "https://sciencedirect.com/tree-canopy-surface-temperature-review"
)
_USFS_CANOPY_URL = "https://fs.usda.gov/research/urban-tree-canopy-cooling"
_PHOENIX_CANOPY_URL = (
    "https://phoenix.gov/tree-canopy-surface-temperature-study"
)
_STRONG_URLS = (
    _EPA_CANOPY_URL,
    _NOAA_CANOPY_URL,
    _CANOPY_REVIEW_URL,
    _USFS_CANOPY_URL,
    _PHOENIX_CANOPY_URL,
)

_LMOP_URL = "https://epa.gov/lmop/organics-diversion-methane"
_COMPOST_BLOG_URL = (
    "https://compostingindustry.example.com/mandate-methane-claims"
)
_PARTICIPATION_URL = (
    "https://citiesclimate.example.org/composting-participation"
)
_GAPPY_URLS = (_LMOP_URL, _COMPOST_BLOG_URL, _PARTICIPATION_URL)

_PARTICULATE_BLOG_URL = (
    "https://transportationblog.example.com/congestion-pricing-particulates"
)
_WRI_URL = "https://wri.org/congestion-pricing-air-quality-evidence"
_BUDGET_URLS = (_PARTICULATE_BLOG_URL, _WRI_URL)

_GCCA_URL = "https://gccassociation.org/net-zero-roadmap"
_IEA_CE_URL = "https://iea.org/energy-system/industry/cement"
_NATURE_CE_URL = "https://nature.com/articles/cement-decarbonization-at-scale"
_LIVE_URLS = (_GCCA_URL, _IEA_CE_URL, _NATURE_CE_URL)

_KNOWN_URLS = {
    "approve-strong-report": _STRONG_URLS,
    "request-more-research": _GAPPY_URLS,
    "missing-evidence-or-budget-exhausted": _BUDGET_URLS,
    "critic-live-review": _LIVE_URLS,
}

# The no_spurious_gaps metric is judged against the themes the report
# demonstrably covers, so every case carrying that metric — the strong
# controlled case and the live case, which reuses its metrics — pins the
# report's themes in the reference. The themes were derived from each
# case's own report text, the way the Source Evaluator's live case derives
# its URL partitions from its own findings.

_STRONG_THEMES = [
    "shading and evapotranspiration mechanisms",
    "measured surface temperature reductions",
    "canopy cover and structure effects",
    "species and irrigation factors",
    "limitations and replication status",
    "geographic scope of the evidence",
]

_LIVE_THEMES = [
    "commercial-scale deployment",
    "clinker substitution and alternative fuels",
    "cost premiums and price trends",
    "compressive strength standards",
    "durability and long-term performance data",
    "emissions reduction potential",
]

_REFERENCES = {
    "approve-strong-report": {
        "expected_route": "end",
        "minimum_score": 7,
        "reference_themes": _STRONG_THEMES,
    },
    "request-more-research": {
        "expected_route": "refine",
        "known_gaps": [
            "participation rates",
            "methane measurement methodology",
        ],
        "minimum_recommended_queries": 1,
    },
    "missing-evidence-or-budget-exhausted": {
        "expected_route": "end",
        "reason": "budget_exhausted",
        "maximum_score": 6,
    },
    "critic-live-review": {
        "expected_route": "end",
        "minimum_score": 7,
        "reference_themes": _LIVE_THEMES,
    },
}

_RUBRIC_DIMENSIONS = {
    "approve-strong-report": {
        "score_groundedness",
        "gap_precision",
        "critique_actionability",
        "scoring_calibration",
    },
    "request-more-research": {
        "score_groundedness",
        "gap_precision",
        "critique_actionability",
    },
    "missing-evidence-or-budget-exhausted": {
        "score_groundedness",
        "gap_precision",
        "route_discipline",
    },
    "critic-live-review": {
        "score_groundedness",
        "gap_precision",
        "critique_actionability",
        "scoring_calibration",
    },
}

_LIVE_DOMAINS = {"gccassociation.org", "iea.org", "nature.com"}


def _all_critic_cases():
    return [
        *cases_for("critic", "controlled"),
        *cases_for("critic", "live"),
    ]


def _case(case_id: str):
    return next(
        item for item in _all_critic_cases() if item.case_id == case_id
    )


def _word_count(report: str) -> int:
    return len(report.split())


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("critic", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("critic", "live")

    assert len(live) == 1
    assert live[0].case_id == "critic-live-review"


def test_the_budget_case_starts_at_the_final_allowed_iteration() -> None:
    case = next(
        item
        for item in cases_for("critic", "controlled")
        if item.case_id == "missing-evidence-or-budget-exhausted"
    )

    assert case.state.iteration == case.state.max_iterations


def test_every_case_expects_the_critique_output() -> None:
    for case in _all_critic_cases():
        assert case.expectations.required_output_fields == ["critique"]


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("critic", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_every_case_names_its_scenario() -> None:
    assert _case("approve-strong-report").dependency_scenario == (
        "critic-strong-report"
    )
    assert _case("request-more-research").dependency_scenario == (
        "critic-gappy-report"
    )
    assert _case("missing-evidence-or-budget-exhausted").dependency_scenario == (
        "critic-budget-exhausted"
    )


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("critic", "live")[0]

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


def test_the_budget_case_requires_a_recorded_recoverable_error() -> None:
    case = _case("missing-evidence-or-budget-exhausted")

    assert case.expectations.must_record_recoverable_error is True


def test_each_case_pins_its_finding_and_claim_counts() -> None:
    expected = {
        "approve-strong-report": (5, 5),
        "request-more-research": (2, 1),
        "missing-evidence-or-budget-exhausted": (1, 0),
        "critic-live-review": (3, 2),
    }
    for case_id, (findings, claims) in expected.items():
        case = _case(case_id)
        assert len(case.state.raw_findings) == findings, case_id
        assert len(case.state.verified_claims) == claims, case_id


def test_the_live_case_declares_the_dependencies_it_needs() -> None:
    """The live Critic spot-checks with real web searches and real memory
    reads; it never fetches pages, so only tavily and memory are live
    dependencies."""
    live = cases_for("critic", "live")[0]

    assert live.expectations.required_live_dependencies == [
        "tavily",
        "memory",
    ]


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
    cases = _all_critic_cases()

    assert len({id(case.judge_rubric) for case in cases}) == len(cases)


@pytest.mark.parametrize(
    ("case_id", "dimensions"), sorted(_RUBRIC_DIMENSIONS.items())
)
def test_each_case_pins_its_rubric_dimensions(
    case_id: str, dimensions: set[str]
) -> None:
    case = _case(case_id)

    assert {
        dimension.dimension_id
        for dimension in case.judge_rubric.agent_dimensions
    } == dimensions


def test_the_strong_case_carries_five_solid_sources() -> None:
    case = _case("approve-strong-report")

    assert tuple(
        source.url for source in case.state.evaluated_sources
    ) == _STRONG_URLS
    assert all(
        source.overall_score >= 0.75
        for source in case.state.evaluated_sources
    )
    assert not any(
        source.low_confidence for source in case.state.evaluated_sources
    )


def test_the_strong_case_claims_split_verified_and_unverified() -> None:
    case = _case("approve-strong-report")

    verified = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "verified"
    ]
    unverified = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "unverified"
    ]
    assert len(verified) == 4
    assert all(claim.confidence >= 0.8 for claim in verified)
    assert len(unverified) == 1
    assert unverified[0].confidence < 0.7


def test_the_strong_case_report_is_a_round_five_hundred_words() -> None:
    """The report the critic reads is the fixed, complete reference report:
    a summary, three findings sections citing all five URLs, and a
    limitations section."""
    case = _case("approve-strong-report")

    assert case.state.report is not None
    report = case.state.report
    assert 420 <= _word_count(report) <= 580
    assert "## Summary" in report
    for url in _STRONG_URLS:
        assert url in report
    assert "## Limitations" in report


def test_the_gappy_case_plans_three_subtopics_and_covers_one() -> None:
    case = _case("request-more-research")

    assert tuple(
        topic.title for topic in case.state.sub_topics
    ) == (
        "Participation in municipal composting mandates",
        "Landfill methane measurement methodology",
        "Measured methane reductions from composting mandates",
    )
    covered = {finding.related_sub_topic for finding in case.state.raw_findings}
    assert covered == {"Measured methane reductions from composting mandates"}


def test_the_gappy_case_has_one_low_confidence_source() -> None:
    case = _case("request-more-research")

    assert tuple(
        source.url for source in case.state.evaluated_sources
    ) == (_LMOP_URL, _COMPOST_BLOG_URL)
    low = [
        source
        for source in case.state.evaluated_sources
        if source.low_confidence
    ]
    assert len(low) == 1
    assert low[0].url == _COMPOST_BLOG_URL


def test_the_gappy_case_carries_one_unverified_claim() -> None:
    case = _case("request-more-research")

    claims = case.state.verified_claims
    assert len(claims) == 1
    assert claims[0].verdict == "unverified"


def test_the_gappy_case_report_has_no_limitations_section() -> None:
    case = _case("request-more-research")

    assert case.state.report is not None
    report = case.state.report
    assert "limitation" not in report.casefold()
    assert _word_count(report) < 250


def test_the_budget_case_has_no_verified_claims() -> None:
    case = _case("missing-evidence-or-budget-exhausted")

    assert case.state.verified_claims == []
    low = [
        source
        for source in case.state.evaluated_sources
        if source.low_confidence
    ]
    assert len(case.state.evaluated_sources) == 1
    assert len(low) == 1


def test_the_budget_case_report_is_thin_and_cites_one_source() -> None:
    case = _case("missing-evidence-or-budget-exhausted")

    assert case.state.report is not None
    report = case.state.report
    assert _word_count(report) < 200
    assert _PARTICULATE_BLOG_URL in report


def test_the_live_case_cites_three_real_stable_domains() -> None:
    case = _case("critic-live-review")

    assert len(case.state.raw_findings) == 3
    assert {
        source_domain(finding.source_url)
        for finding in case.state.raw_findings
    } == _LIVE_DOMAINS


def test_the_live_case_carries_two_verified_claims() -> None:
    case = _case("critic-live-review")

    verified = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "verified"
    ]
    assert len(verified) == 2
    assert all(claim.confidence >= 0.8 for claim in verified)


def test_the_live_case_report_is_a_round_four_hundred_words() -> None:
    """The live report is fixed in the case state — the live run critiques
    this text, it does not discover it — so the reference themes are
    declarable up front, exactly as the controlled strong case declares
    its own."""
    case = _case("critic-live-review")

    assert case.state.report is not None
    report = case.state.report
    assert 330 <= _word_count(report) <= 470
    assert "## Summary" in report
    for url in _LIVE_URLS:
        assert url in report
    assert "## Limitations" in report


def test_the_live_case_reuses_the_strong_cases_metrics() -> None:
    strong = _case("approve-strong-report")
    live = _case("critic-live-review")

    assert tuple(
        (metric.metric_id, metric.weight)
        for metric in live.expectations.deterministic_metrics
    ) == tuple(
        (metric.metric_id, metric.weight)
        for metric in strong.expectations.deterministic_metrics
    )


def test_route_decision_is_derivable_from_every_cases_state() -> None:
    """route_consistent's inputs are the produced critique plus the case's
    own state: iteration, max_iterations, and a report. Every case —
    including the live one — supplies them, so the gate can call the
    production rule without any further reference data."""
    for case in _all_critic_cases():
        critique = Critique(
            score=8,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=False,
            rationale="Solid report.",
        )
        continue_, reason = route_decision(
            score=critique.score,
            gaps=critique.gaps,
            unsupported_claims=critique.unsupported_claims,
            iteration=case.state.iteration,
            max_iterations=case.state.max_iterations,
            has_report=bool(case.state.report and case.state.report.strip()),
        )

        # The budget case sits at the final allowed iteration, so the
        # production rule must stop it before any quality signal is read;
        # the other cases accept the strong critique.
        if case.state.iteration >= case.state.max_iterations:
            assert continue_ is False
            assert reason == "max_iterations_reached"
        else:
            assert continue_ is False
            assert reason == "accepted_quality"


def test_the_budget_case_routes_to_end_no_matter_the_score() -> None:
    """route_discipline is the iteration bound beating every quality
    signal: at the final allowed iteration, should_continue is False for
    any score, any gaps, any unsupported claims."""
    case = _case("missing-evidence-or-budget-exhausted")

    for score in (1, 6, 10):
        continue_, reason = route_decision(
            score=score,
            gaps=["a material gap"],
            unsupported_claims=["an unsupported claim"],
            iteration=case.state.iteration,
            max_iterations=case.state.max_iterations,
            has_report=True,
        )

        assert continue_ is False
        assert reason == "max_iterations_reached"


def test_the_strong_scenario_scripts_one_search_and_one_memory_entry() -> None:
    script = SCENARIOS["critic-strong-report"]

    assert script.failures == {}
    assert tuple(script.search_responses) == (_STRONG_SEARCH_KEY,)
    result = script.search_responses[_STRONG_SEARCH_KEY]
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == _CANOPY_REVIEW_URL
    assert script.scripted_search_urls == (_CANOPY_REVIEW_URL,)
    assert [
        entry["content"] for entry in script.memory_entries
    ] == [
        "Prior sessions established that mature urban tree canopy "
        "measurably lowers daytime surface temperatures in warm climates.",
    ]


def test_the_gappy_scenario_scripts_one_search_and_one_memory_entry() -> None:
    script = SCENARIOS["critic-gappy-report"]

    assert script.failures == {}
    assert tuple(script.search_responses) == (_GAPPY_SEARCH_KEY,)
    result = script.search_responses[_GAPPY_SEARCH_KEY]
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == _PARTICIPATION_URL
    assert script.scripted_search_urls == (_PARTICIPATION_URL,)
    assert [
        entry["content"] for entry in script.memory_entries
    ] == [
        "Prior sessions noted that participation rates drive the climate "
        "effect of organics mandates, and that landfill methane estimates "
        "depend on measurement methodology.",
    ]


def test_the_gappy_scenario_key_is_the_participation_subtopics_query() -> None:
    """The scripted search key and the case's own subtopic query are the
    same literal, pinned on both sides so the two spellings cannot drift."""
    case = _case("request-more-research")
    participation = case.state.sub_topics[0]

    assert _GAPPY_SEARCH_KEY in participation.search_queries
    assert _GAPPY_SEARCH_KEY in SCENARIOS["critic-gappy-report"].search_responses


def test_the_budget_scenario_fails_memory_and_recovers_via_search() -> None:
    """The memory query raises while the search still succeeds: recovery
    evidence exists for the failure the case is built around."""
    script = SCENARIOS["critic-budget-exhausted"]

    failure = script.failures["query_memory"]
    assert isinstance(failure, RuntimeError)
    assert "long-term memory is unavailable" in str(failure)

    # Non-httpx, non-retryable: the real tools retry httpx timeouts and
    # status errors (three attempts each), which would triple-count a
    # scripted failure in the call ledger.
    assert not isinstance(failure, httpx.HTTPError)

    assert tuple(script.search_responses) == (_BUDGET_SEARCH_KEY,)
    result = script.search_responses[_BUDGET_SEARCH_KEY]
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == _WRI_URL
    assert script.scripted_search_urls == (_WRI_URL,)
    assert script.memory_entries == ()

    assert set(script.failures) == {"query_memory"}


def test_every_case_declares_its_known_source_urls() -> None:
    for case_id, declared in _KNOWN_URLS.items():
        case = _case(case_id)
        assert set(case.expectations.known_source_urls) == set(declared), case_id

    # Every declared URL is already in the normalized form the agent
    # records, so nothing here can drift from a gate comparison.
    for case_id in _KNOWN_URLS:
        case = _case(case_id)
        for url in case.expectations.known_source_urls:
            assert normalize_source_url(url) == url, (case_id, url)


def test_the_scripted_result_urls_are_known_source_urls() -> None:
    """A scripted search result URL a run can legitimately see is declared
    in the case's known set, so no gate can call it invented."""
    for case_id, scenario_name in (
        ("approve-strong-report", "critic-strong-report"),
        ("request-more-research", "critic-gappy-report"),
        ("missing-evidence-or-budget-exhausted", "critic-budget-exhausted"),
    ):
        case = _case(case_id)
        script = SCENARIOS[scenario_name]
        known = set(case.expectations.known_source_urls)

        assert set(script.scripted_search_urls) <= known, case_id


def test_the_reference_themes_map_to_the_reports_actual_content() -> None:
    """no_spurious_gaps is judged by the reference themes, so a theme must
    be demonstrably present in the report the critic reads — otherwise the
    metric could only ever auto-pass. Each theme phrase must occur in the
    report text."""
    for case_id in ("approve-strong-report", "critic-live-review"):
        case = _case(case_id)
        report = case.state.report.casefold()

        themes = case.expectations.reference["reference_themes"]
        assert themes, case_id
        for theme in themes:
            assert theme in report, (case_id, theme)


@pytest.mark.asyncio
async def test_the_budget_double_fails_memory_and_serves_the_search(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The budget scenario's wiring is real: query_memory raises the
    scripted RuntimeError while web_search returns the scripted recovery
    result, and the ledger records one failed memory call and one
    successful search call — never retried, never triple-counted."""
    case = _case("missing-evidence-or-budget-exhausted")
    bundle = build_controlled_dependencies(
        runtime_config_for("critic"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )
    mem = next(tool for tool in bundle.tools if tool.name == "query_memory")
    search = next(tool for tool in bundle.tools if tool.name == "web_search")

    async with tracker.session_span("evaluation-1", "q"):
        memory_result = await mem.execute(
            query="prior findings on congestion pricing particulate pollution",
            top_k=5,
        )
        search_result = await search.execute(
            query=_BUDGET_SEARCH_KEY, max_results=5
        )

    assert memory_result.success is False
    assert memory_result.error is not None
    assert memory_result.error.type == "RuntimeError"
    assert memory_result.error.message == "long-term memory is unavailable"
    assert memory_result.error.recoverable is True

    assert search_result.success, search_result.error
    results = search_result.data["results"]
    assert results[0]["url"] == _WRI_URL

    ledger = bundle.recorder.ledger()
    summaries = {summary.tool_name: summary for summary in ledger.tool_calls}
    assert summaries["query_memory"].calls == 1
    assert summaries["query_memory"].failures == 1
    assert summaries["web_search"].calls == 1
    assert summaries["web_search"].failures == 0
    assert ledger.prohibited_calls == []
