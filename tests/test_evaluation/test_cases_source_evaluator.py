"""Source Evaluator evaluation cases: ranking, signal balance, and recovery."""

from __future__ import annotations

import httpx
import pytest

from deep_research.agents.sources import source_domain
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    SCENARIOS,
    build_controlled_dependencies,
)

CONTROLLED = (
    "strong-and-weak-sources",
    "corroboration-recency-reputation",
    "reputation-provider-failure",
)

_METRICS = {
    "strong-and-weak-sources": (
        ("one_evaluation_per_source", 0.30),
        ("score_ordering", 0.30),
        ("bounded_scores", 0.20),
        ("low_confidence_flagged", 0.20),
    ),
    "corroboration-recency-reputation": (
        ("balanced_scoring", 0.40),
        ("one_evaluation_per_source", 0.25),
        ("bounded_scores", 0.20),
        ("rationale_mentions_multiple_signals", 0.15),
    ),
    "reputation-provider-failure": (
        ("all_sources_still_scored", 0.35),
        ("fallback_scores_bounded", 0.25),
        ("failure_recorded", 0.25),
        ("no_fabricated_reputation", 0.15),
    ),
    "source-evaluator-live-ranking": (
        ("one_evaluation_per_source", 0.30),
        ("score_ordering", 0.30),
        ("bounded_scores", 0.20),
        ("low_confidence_flagged", 0.20),
    ),
}

# The scripted URLs and domains are shared with the dependency scenarios:
# the cases declare them as raw_findings (and in their references), and
# dependencies.py scripts the same domains as reputations and reputation
# failures. The equality tests below pin both sides to these literals.

_IPCC_URL = "https://www.ipcc.ch/ar6-wg1"
_AMETSOC_URL = "https://journals.ametsoc.org/regional-precip"
_WEATHERBLOG_URL = "https://weatherblog.example.com/my-take"
_FORUM_URL = "https://forum.example.net/thread/1182"
_NOAA_URL = "https://www.noaa.gov/precip-assessment"
_MIXED_URLS = (
    _IPCC_URL,
    _AMETSOC_URL,
    _WEATHERBLOG_URL,
    _FORUM_URL,
    _NOAA_URL,
)

_PAPER_URL = "https://www.science.org/methane-leakage-us-2016"
_PREPRINT_URL = "https://arxiv.org/abs/methane-leakage-2026"
_INDUSTRY_URL = "https://www.ingaa.org/leakage-mitigation-2024"
_NGO_URL = "https://www.edf.org/basin-methane-measurements-2025"
_COMPETING_URLS = (_PAPER_URL, _PREPRINT_URL, _INDUSTRY_URL, _NGO_URL)

_EPA_SENSOR_URL = "https://www.epa.gov/consumer-grade-air-sensors"
_AQMD_SENSOR_URL = "https://www.aqmd.gov/sensor-field-evaluations"
_NIST_SENSOR_URL = "https://www.nist.gov/air-quality-sensor-testbed"
_CU_SENSOR_URL = "https://www.colorado.edu/consumer-sensor-assessment"
_FAILURE_URLS = (
    _EPA_SENSOR_URL,
    _AQMD_SENSOR_URL,
    _NIST_SENSOR_URL,
    _CU_SENSOR_URL,
)

_REFERENCES = {
    "strong-and-weak-sources": {
        "authoritative_urls": [_IPCC_URL, _AMETSOC_URL, _NOAA_URL],
        "weak_urls": [_WEATHERBLOG_URL, _FORUM_URL],
        "expected_low_confidence_urls": [_FORUM_URL],
    },
    "corroboration-recency-reputation": {
        "single_signal_traps": {
            "recency_only_url": _PREPRINT_URL,
            "authority_only_url": _PAPER_URL,
        },
    },
    "reputation-provider-failure": {
        "failing_domains": ["epa.gov", "aqmd.gov"],
        "succeeding_domains": ["nist.gov", "colorado.edu"],
    },
}

_RUBRIC_DIMENSIONS = {
    "strong-and-weak-sources": {"rationale_quality", "ranking_discipline"},
    "corroboration-recency-reputation": {
        "rationale_quality",
        "ranking_discipline",
        "signal_balance",
    },
    "reputation-provider-failure": {
        "rationale_quality",
        "ranking_discipline",
        "degradation_honesty",
    },
    "source-evaluator-live-ranking": {"rationale_quality", "ranking_discipline"},
}

_LIVE_DOMAINS = {"epa.gov", "who.int", "medium.com", "reddit.com"}


def _all_source_evaluator_cases():
    return [
        *cases_for("source_evaluator", "controlled"),
        *cases_for("source_evaluator", "live"),
    ]


def _case(case_id: str):
    return next(
        item
        for item in _all_source_evaluator_cases()
        if item.case_id == case_id
    )


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("source_evaluator", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("source_evaluator", "live")

    assert len(live) == 1
    assert live[0].case_id == "source-evaluator-live-ranking"


def test_no_source_evaluator_case_expects_a_tool_call() -> None:
    """The agent declares no tools; a case budgeting for one is a mistake."""
    for case in cases_for("source_evaluator", "controlled"):
        assert case.expectations.max_tool_calls == 0


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("source_evaluator", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("source_evaluator", "live")[0]

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
    case = _case("reputation-provider-failure")

    assert case.expectations.must_record_recoverable_error is True


def test_every_controlled_case_populates_raw_findings() -> None:
    """The Source Evaluator derives its canonical source set from
    state.raw_findings, so a case with empty findings would score nothing."""
    for case in cases_for("source_evaluator", "controlled"):
        assert case.state.raw_findings, case.case_id


def test_the_live_case_declares_the_memory_dependency_it_needs() -> None:
    live = cases_for("source_evaluator", "live")[0]

    assert live.expectations.required_live_dependencies == ["memory"]
    assert live.expectations.max_tool_calls == 0


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
    cases = _all_source_evaluator_cases()

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


def test_the_mixed_case_cites_exactly_its_five_urls() -> None:
    case = _case("strong-and-weak-sources")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _MIXED_URLS


def test_the_competing_signals_case_cites_exactly_its_four_urls() -> None:
    case = _case("corroboration-recency-reputation")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _COMPETING_URLS


def test_the_reputation_failure_case_cites_exactly_its_four_urls() -> None:
    case = _case("reputation-provider-failure")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _FAILURE_URLS


def test_the_live_case_cites_four_real_stable_domains() -> None:
    case = _case("source-evaluator-live-ranking")

    assert len(case.state.raw_findings) == 4
    assert {
        source_domain(finding.source_url)
        for finding in case.state.raw_findings
    } == _LIVE_DOMAINS


def test_the_mixed_case_scripts_reputations_for_authoritative_domains() -> None:
    """The scenario's reputation keys and the case's authoritative URLs are
    two spellings of the same data; equality (not containment) pins them.
    Only ipcc.ch and noaa.gov carry reputations: the AMS journal is
    authoritative but has no scripted reputation, exactly as the brief
    scripts nothing for it."""
    script = SCENARIOS["source-evaluator-mixed"]
    case = _case("strong-and-weak-sources")

    assert dict(script.reputations) == {"ipcc.ch": 0.95, "noaa.gov": 0.92}
    assert set(script.reputations) == {
        source_domain(_IPCC_URL),
        source_domain(_NOAA_URL),
    }
    assert set(script.reputations) <= {
        source_domain(url)
        for url in case.expectations.reference["authoritative_urls"]
    }


def test_the_competing_signals_case_scripts_reputations_for_its_domains() -> None:
    script = SCENARIOS["source-evaluator-competing-signals"]
    case = _case("corroboration-recency-reputation")

    assert dict(script.reputations) == {"ingaa.org": 0.55, "edf.org": 0.70}
    traps = case.expectations.reference["single_signal_traps"]
    trap_urls = {traps["recency_only_url"], traps["authority_only_url"]}
    assert set(script.reputations) == {
        source_domain(finding.source_url)
        for finding in case.state.raw_findings
        if finding.source_url not in trap_urls
    }


def test_the_reputation_failure_case_scripts_domain_keyed_runtime_errors() -> None:
    """Failures are keyed by domain, not tool name, and must not be httpx
    exceptions: the tool retry loops triple-count those, and a reputation
    lookup is not a tool call at all."""
    script = SCENARIOS["source-evaluator-reputation-failure"]

    assert set(script.reputation_failures) == {"epa.gov", "aqmd.gov"}
    for failure in script.reputation_failures.values():
        assert isinstance(failure, RuntimeError)
        assert "reputation lookup failed" in str(failure)
        assert not isinstance(failure, httpx.HTTPError)


def test_the_reputation_failure_case_pins_both_sides_equally() -> None:
    """failing_domains/succeeding_domains in the case reference, the
    scenario's reputation maps, and the case's finding URLs must all be the
    same partition of the same four domains."""
    script = SCENARIOS["source-evaluator-reputation-failure"]
    case = _case("reputation-provider-failure")

    failing = case.expectations.reference["failing_domains"]
    succeeding = case.expectations.reference["succeeding_domains"]

    assert failing == list(script.reputation_failures)
    assert succeeding == list(script.reputations)
    assert set(failing) | set(succeeding) == {
        source_domain(url) for url in _FAILURE_URLS
    }
    assert set(failing) & set(succeeding) == set()


@pytest.mark.asyncio
async def test_the_mixed_double_serves_scripted_reputations(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The controlled reputation double answers domain-keyed scores for the
    authoritative URLs and nothing for the weak ones."""
    case = _case("strong-and-weak-sources")
    bundle = build_controlled_dependencies(
        runtime_config_for("source_evaluator"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )

    assert bundle.reputation is not None
    ipcc = await bundle.reputation.get_source_reputation(_IPCC_URL)
    noaa = await bundle.reputation.get_source_reputation(_NOAA_URL)
    forum = await bundle.reputation.get_source_reputation(_FORUM_URL)

    assert ipcc is not None and ipcc.reputation_score == 0.95
    assert noaa is not None and noaa.reputation_score == 0.92
    assert forum is None


@pytest.mark.asyncio
async def test_the_failure_double_raises_for_mapped_domains_and_serves_the_rest(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The scenario's failure injection is real: the two mapped domains
    raise the scripted RuntimeError, the other two return scripted scores."""
    case = _case("reputation-provider-failure")
    bundle = build_controlled_dependencies(
        runtime_config_for("source_evaluator"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )

    assert bundle.reputation is not None
    with pytest.raises(RuntimeError, match="reputation lookup failed"):
        await bundle.reputation.get_source_reputation(_EPA_SENSOR_URL)
    with pytest.raises(RuntimeError, match="reputation lookup failed"):
        await bundle.reputation.get_source_reputation(_AQMD_SENSOR_URL)

    nist = await bundle.reputation.get_source_reputation(_NIST_SENSOR_URL)
    cu = await bundle.reputation.get_source_reputation(_CU_SENSOR_URL)

    assert nist is not None and nist.reputation_score == 0.80
    assert cu is not None and cu.reputation_score == 0.75
