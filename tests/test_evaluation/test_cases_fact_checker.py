"""Fact Checker evaluation cases: verdicts, independence, and recovery."""

from __future__ import annotations

import httpx
import pytest

from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    SCENARIOS,
    build_controlled_dependencies,
)

CONTROLLED = (
    "mixed-verdicts",
    "independent-domain-evidence",
    "verification-search-failure",
)

_METRICS = {
    "mixed-verdicts": (
        ("verdict_correctness", 0.35),
        ("evidence_linked", 0.25),
        ("confidence_calibrated", 0.20),
        ("sources_known", 0.20),
    ),
    "independent-domain-evidence": (
        ("independence_enforced", 0.45),
        ("evidence_linked", 0.25),
        ("sources_known", 0.15),
        ("budget_respected", 0.15),
    ),
    "verification-search-failure": (
        ("conservative_on_failure", 0.35),
        ("partial_verification_present", 0.25),
        ("failure_recorded", 0.25),
        ("budget_respected", 0.15),
    ),
    "fact-checker-live-verification": (
        ("evidence_linked", 0.30),
        ("independence_enforced", 0.30),
        ("confidence_calibrated", 0.25),
        ("budget_respected", 0.15),
    ),
}

# The claim texts are the scenario keys AND the case references: the case
# declares them as expected verdicts (or failing-query anchors), and
# dependencies.py scripts the verification searches under those same
# strings. The equality tests below pin both sides to these literals.

_SUPPORTED_CLAIM = (
    "Small modular reactor designs must satisfy the same international "
    "safety standards as large reactors."
)
_REFUTED_CLAIM = "No small modular reactor has operated commercially."
_THIN_CLAIM = (
    "Small modular reactors will be cheaper to build than large reactors "
    "at scale."
)
_OUTAGE_CLAIM = "The 2025 grid upgrade reduced outage minutes by 40 percent."
_HEAT_CLAIM = (
    "Ocean heat content is still rising at the rate reported in 2023."
)
_ATTRIBUTION_CLAIM = (
    "The recent acceleration in ocean heat content is driven primarily by "
    "greenhouse gas forcing."
)
# A genuine, strict prefix of _HEAT_CLAIM: the reference names the first
# claim's text prefix so Task 18's failure metric can recognize the failing
# query, and the scenario keys the failing search under the full claim.
_FAILING_QUERY_PREFIX = "Ocean heat content is still rising at the rate"

_IAEA_URL = "https://iaea.org/smr-safety-assessment"
_WNA_URL = "https://world-nuclear.org/smr-safety-standards"
_POLICY_URL = "https://energypolicy.example.org/smr-commercialization"
_TRADE_URL = "https://nuclearindustry.example.com/pevek-klt-40s"
_VENDOR_URL = "https://reactorvendor.example.com/smr-cost-advantage"
_NEWSLETTER_URL = "https://industrynewsletter.example.com/smr-cost-projections"
_MIXED_URLS = (
    _IAEA_URL,
    _WNA_URL,
    _POLICY_URL,
    _TRADE_URL,
    _VENDOR_URL,
    _NEWSLETTER_URL,
)

# Scripted search results for the mixed case: two corroborating results
# across independent domains for the supportable claim, one contradicting
# result for the refutable claim, and an empty set for the thin claim.
_NRC_URL = "https://nrc.gov/smr-licensing-framework"
_ANS_URL = "https://ans.org/smr-safety-assessment"
_NEI_URL = "https://nei.org/pevek-floating-plant"

_NEWS_URL = "https://news.example.com/outage-coverage"
_WWW_NEWS_URL = "https://www.news.example.com/outage-verification"
_SYNDICATION_URL = "https://syndication.news.example.com/outage-syndication"
_REGULATOR_URL = "https://regulator.example.gov/outage-report"
_DEPENDENT_URLS = (_NEWS_URL, _WWW_NEWS_URL, _SYNDICATION_URL, _REGULATOR_URL)

_NOAA_URL = "https://noaa.gov/ohc-2025-update"
_COPERNICUS_URL = "https://copernicus.eu/ocean-heat-content-2025"
_NATURE_URL = "https://nature.com/ocean-heat-attribution"
_BLOG_URL = "https://climateblog.example.com/ocean-heat-plateau"
_FAILURE_URLS = (_NOAA_URL, _COPERNICUS_URL, _NATURE_URL, _BLOG_URL)

_IEA_URL = "https://iea.org/solar-pv-installed-capacity"
_IRENA_URL = "https://irena.org/solar-capacity-statistics"
_LIVE_URLS = (_IEA_URL, _IRENA_URL)

_REFERENCES = {
    "mixed-verdicts": {
        "expected_verdicts": {
            _SUPPORTED_CLAIM: "verified",
            _REFUTED_CLAIM: "contradicted",
            _THIN_CLAIM: "insufficient_evidence",
        },
    },
    "independent-domain-evidence": {
        "dependent_domain_family": "news.example.com",
        "independent_urls": [_REGULATOR_URL],
        "minimum_independent_domains": 2,
    },
    "verification-search-failure": {
        "failing_query_prefix": _FAILING_QUERY_PREFIX,
        "conservative_verdicts": ["insufficient_evidence", "unverified"],
    },
    "fact-checker-live-verification": {
        "minimum_independent_domains": 2,
    },
}

_RUBRIC_DIMENSIONS = {
    "mixed-verdicts": {
        "evidence_grounding",
        "claim_fidelity",
        "verdict_discipline",
        "evidence_linkage",
    },
    "independent-domain-evidence": {
        "evidence_grounding",
        "claim_fidelity",
        "independence_rigor",
    },
    "verification-search-failure": {
        "evidence_grounding",
        "claim_fidelity",
        "conservatism_under_failure",
    },
    "fact-checker-live-verification": {
        "evidence_grounding",
        "claim_fidelity",
    },
}

_LIVE_DOMAINS = {"iea.org", "irena.org"}


def _all_fact_checker_cases():
    return [
        *cases_for("fact_checker", "controlled"),
        *cases_for("fact_checker", "live"),
    ]


def _case(case_id: str):
    return next(
        item
        for item in _all_fact_checker_cases()
        if item.case_id == case_id
    )


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("fact_checker", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("fact_checker", "live")

    assert len(live) == 1
    assert live[0].case_id == "fact-checker-live-verification"


def test_every_case_expects_the_verified_claims_output() -> None:
    for case in _all_fact_checker_cases():
        assert case.expectations.required_output_fields == ["verified_claims"]


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("fact_checker", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("fact_checker", "live")[0]

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
    case = _case("verification-search-failure")

    assert case.expectations.must_record_recoverable_error is True


def test_every_controlled_case_populates_raw_findings() -> None:
    """The Fact Checker extracts claims from state.raw_findings, so a case
    with empty findings would check nothing."""
    for case in cases_for("fact_checker", "controlled"):
        assert case.state.raw_findings, case.case_id


def test_the_live_case_declares_the_dependencies_it_needs() -> None:
    """The live case verifies with live web search and memory recall; it
    does not require HTTP page fetches (search snippets suffice)."""
    live = cases_for("fact_checker", "live")[0]

    assert live.expectations.required_live_dependencies == ["tavily", "memory"]


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
    cases = _all_fact_checker_cases()

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


def test_the_mixed_case_cites_exactly_its_six_urls() -> None:
    case = _case("mixed-verdicts")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _MIXED_URLS


def test_the_dependent_domains_case_cites_exactly_its_four_urls() -> None:
    case = _case("independent-domain-evidence")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _DEPENDENT_URLS


def test_the_search_failure_case_cites_exactly_its_four_urls() -> None:
    case = _case("verification-search-failure")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _FAILURE_URLS


def test_the_live_case_cites_exactly_its_two_urls() -> None:
    case = _case("fact-checker-live-verification")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _LIVE_URLS


def test_the_live_case_cites_two_real_stable_domains() -> None:
    case = _case("fact-checker-live-verification")

    assert len(case.state.raw_findings) == 2
    assert {
        source_domain(finding.source_url)
        for finding in case.state.raw_findings
    } == _LIVE_DOMAINS


def test_the_mixed_case_carries_matching_evaluated_sources() -> None:
    """One ScoredSource per finding URL, no extras, no strays."""
    case = _case("mixed-verdicts")

    assert tuple(
        source.url for source in case.state.evaluated_sources
    ) == _MIXED_URLS


def test_the_mixed_case_scripts_one_search_per_referenced_claim() -> None:
    """The scenario keys are exactly the three claim texts the reference
    pins, and each response matches its intended verdict class."""
    script = SCENARIOS["fact-checker-mixed"]
    case = _case("mixed-verdicts")

    expected_verdicts = case.expectations.reference["expected_verdicts"]
    assert set(script.search_responses) == set(expected_verdicts)
    assert set(script.search_responses) == {
        _SUPPORTED_CLAIM,
        _REFUTED_CLAIM,
        _THIN_CLAIM,
    }

    corroborating = script.search_responses[_SUPPORTED_CLAIM]["results"]
    refuting = script.search_responses[_REFUTED_CLAIM]["results"]
    thin = script.search_responses[_THIN_CLAIM]["results"]

    # The supported claim: corroborating results across independent domains
    # (never iaea.org / world-nuclear.org, the claim's own publishers).
    assert len(corroborating) == 2
    claimed = {"iaea.org", "world-nuclear.org"}
    corroborating_domains = {
        source_domain(result["url"]) for result in corroborating
    }
    assert corroborating_domains == {"nrc.gov", "ans.org"}
    assert not (corroborating_domains & claimed)

    # The refuted claim: exactly one contradicting result.
    assert len(refuting) == 1
    assert refuting[0]["url"] == _NEI_URL
    assert "commercial" in refuting[0]["content"].casefold()

    # The thin claim: an empty result set, so the loop retrieves nothing.
    assert thin == []


def test_the_dependent_domains_case_scripts_only_the_same_family() -> None:
    """One scripted search, returning two more results from the
    news.example.com family and nothing from anywhere else."""
    script = SCENARIOS["fact-checker-dependent-domains"]
    case = _case("independent-domain-evidence")

    assert set(script.search_responses) == {_OUTAGE_CLAIM}
    results = script.search_responses[_OUTAGE_CLAIM]["results"]

    assert [result["url"] for result in results] == [
        "https://news.example.com/outage-minutes-fall",
        "https://syndication.news.example.com/outage-minutes-fall",
    ]
    family = case.expectations.reference["dependent_domain_family"]
    assert all(
        source_domain(result["url"]) == family
        or source_domain(result["url"]).endswith(f".{family}")
        for result in results
    )


def test_the_dependent_domains_case_findings_wear_three_hats() -> None:
    """The three news-family findings collapse to one registrable domain
    once normalized; only the regulator finding is genuinely independent."""
    case = _case("independent-domain-evidence")
    reference = case.expectations.reference
    family = reference["dependent_domain_family"]

    news_urls = [finding.source_url for finding in case.state.raw_findings][:3]
    hosts = {
        source_domain(normalize_source_url(url)) for url in news_urls
    }
    assert hosts == {"news.example.com", "syndication.news.example.com"}
    assert all(host == family or host.endswith(f".{family}") for host in hosts)

    assert reference["independent_urls"] == [_REGULATOR_URL]
    regulator_host = source_domain(normalize_source_url(_REGULATOR_URL))
    assert regulator_host == "regulator.example.gov"
    assert not regulator_host.endswith(f".{family}")


def test_the_search_failure_case_scripts_runtime_errors_only() -> None:
    """The failing search maps to a non-httpx RuntimeError: the real tool
    retries httpx timeouts and status errors (three attempts each), which
    would triple-count a scripted failure in the call ledger."""
    script = SCENARIOS["fact-checker-search-failure"]
    case = _case("verification-search-failure")

    assert set(script.search_responses) == {_HEAT_CLAIM, _ATTRIBUTION_CLAIM}

    search_failure = script.search_responses[_HEAT_CLAIM]
    assert isinstance(search_failure, RuntimeError)
    assert "search backend unavailable" in str(search_failure)
    assert not isinstance(search_failure, httpx.HTTPError)

    injected = [
        value
        for value in script.search_responses.values()
        if isinstance(value, Exception)
    ]
    assert injected
    assert all(not isinstance(item, httpx.HTTPError) for item in injected)

    prefix = case.expectations.reference["failing_query_prefix"]
    assert _HEAT_CLAIM.startswith(prefix)
    assert len(prefix) < len(_HEAT_CLAIM)


def test_the_search_failure_case_lets_the_second_claim_succeed() -> None:
    """The second verification search returns two independent corroborating
    results, none of them from the claim's own publisher."""
    script = SCENARIOS["fact-checker-search-failure"]

    results = script.search_responses[_ATTRIBUTION_CLAIM]["results"]
    assert [result["url"] for result in results] == [
        "https://agu.org/ocean-heat-attribution",
        "https://gcos.wmo.int/ocean-heat-bulletin",
    ]
    assert {
        source_domain(result["url"]) for result in results
    } == {"agu.org", "gcos.wmo.int"}
    assert "nature.com" not in {
        source_domain(result["url"]) for result in results
    }


def test_every_controlled_case_declares_its_scripted_urls() -> None:
    """known_source_urls must equal the normalized findings plus the
    normalized scripted result URLs: the Fact Checker normalizes every URL
    it records, so a byte-literal www or trailing-slash variant would
    silently mismatch a normalized claim citation."""
    expected: dict[str, set[str]] = {
        "mixed-verdicts": set(_MIXED_URLS) | {_NRC_URL, _ANS_URL, _NEI_URL},
        "independent-domain-evidence": {
            normalize_source_url(url) for url in _DEPENDENT_URLS
        }
        | {
            "https://news.example.com/outage-minutes-fall",
            "https://syndication.news.example.com/outage-minutes-fall",
        },
        "verification-search-failure": set(_FAILURE_URLS)
        | {
            "https://agu.org/ocean-heat-attribution",
            "https://gcos.wmo.int/ocean-heat-bulletin",
        },
    }
    for case_id, declared in expected.items():
        case = _case(case_id)
        assert set(case.expectations.known_source_urls) == declared, case_id

    # Every declared URL is already in the normalized form the agent
    # records, so nothing here can drift from a gate comparison.
    for case_id in expected:
        case = _case(case_id)
        for url in case.expectations.known_source_urls:
            assert normalize_source_url(url) == url, (case_id, url)


def test_the_live_case_drops_sources_known_and_pins_independence() -> None:
    """A live run legitimately discovers URLs the case cannot pre-declare,
    so sources_known is dropped; independence_enforced still needs its
    minimum-domains reference."""
    case = _case("fact-checker-live-verification")

    metric_ids = {
        metric.metric_id for metric in case.expectations.deterministic_metrics
    }
    assert "sources_known" not in metric_ids
    assert "independence_enforced" in metric_ids
    assert case.expectations.reference == {
        "minimum_independent_domains": 2
    }


@pytest.mark.asyncio
async def test_the_mixed_double_serves_all_three_scripted_searches(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The scripted search double serves the three claim-keyed searches:
    corroboration for the supported claim, one contradicting result for the
    refuted claim, nothing for the thin claim — and an unscripted query is
    prohibited."""
    case = _case("mixed-verdicts")
    bundle = build_controlled_dependencies(
        runtime_config_for("fact_checker"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )
    search = next(tool for tool in bundle.tools if tool.name == "web_search")

    async with tracker.session_span("evaluation-1", "q"):
        supported = await search.execute(query=_SUPPORTED_CLAIM)
        refuted = await search.execute(query=_REFUTED_CLAIM)
        thin = await search.execute(query=_THIN_CLAIM)

    assert supported.success, supported.error
    assert [result["url"] for result in supported.data["results"]] == [
        _NRC_URL,
        _ANS_URL,
    ]
    assert refuted.success, refuted.error
    assert [result["url"] for result in refuted.data["results"]] == [_NEI_URL]
    assert thin.success, thin.error
    assert thin.data["results"] == []

    # An unscripted query is prohibited: it surfaces as a failed tool result
    # (BaseTool.execute converts every client exception into a ToolResult)
    # and is recorded in the ledger as a prohibited call.
    async with tracker.session_span("evaluation-1", "q"):
        prohibited = await search.execute(query="something nobody scripted")

    assert prohibited.success is False
    assert prohibited.error is not None
    assert prohibited.error.type == "ProhibitedDependencyError"
    assert bundle.recorder.ledger().prohibited_calls == [
        "tavily.search('something nobody scripted')"
    ]


@pytest.mark.asyncio
async def test_the_search_failure_double_raises_once_and_is_recorded(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The scripted RuntimeError surfaces as one failed tool result — never
    retried, never triple-counted — and the succeeding claim still works."""
    case = _case("verification-search-failure")
    bundle = build_controlled_dependencies(
        runtime_config_for("fact_checker"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )
    search = next(tool for tool in bundle.tools if tool.name == "web_search")

    async with tracker.session_span("evaluation-1", "q"):
        failed = await search.execute(query=_HEAT_CLAIM)
        succeeded = await search.execute(query=_ATTRIBUTION_CLAIM)

    assert failed.success is False
    assert failed.error is not None
    assert failed.error.type == "RuntimeError"
    assert failed.error.message == "search backend unavailable"

    assert succeeded.success, succeeded.error
    assert [result["url"] for result in succeeded.data["results"]] == [
        "https://agu.org/ocean-heat-attribution",
        "https://gcos.wmo.int/ocean-heat-bulletin",
    ]

    ledger = bundle.recorder.ledger()
    summaries = {summary.tool_name: summary for summary in ledger.tool_calls}
    assert summaries["web_search"].calls == 2
    assert summaries["web_search"].failures == 1
    assert ledger.prohibited_calls == []
