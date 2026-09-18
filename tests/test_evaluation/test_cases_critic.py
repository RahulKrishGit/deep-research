"""Critic evaluation cases: routing, gaps, and budget discipline."""

from __future__ import annotations

import pytest

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    fallback_critique,
    route_decision,
)
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.cases.critic import (
    CALIBRATION_CASES,
    CALIBRATION_CLUSTER_IDS,
    CALIBRATION_STATEMENT_IDS,
    CALIBRATION_TARGET_IDS,
    calibration_case,
    calibration_packet,
    measure_critic_calibration,
)
from deep_research.evaluation.dependencies import (
    CONTROLLED_SCENARIO_CONTRACT_VERSION,
    SCENARIOS,
    build_controlled_dependencies,
)
from deep_research.evaluation.evaluators import (
    AGENT_GATE_IDS,
    METRIC_FUNCTIONS,
    deterministic_metric_scores,
    evaluate_agent_gates,
)
from deep_research.evaluation.models import ReActSummary, TargetOutput
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
        ("route_discipline", 0.60),
        ("conservative_score", 0.25),
        ("score_bounded", 0.15),
    ),
    "critic-live-review": (
        ("score_bounded", 0.20),
        ("route_consistent", 0.35),
        ("rationale_present", 0.20),
        ("no_spurious_gaps", 0.25),
    ),
}

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


def _live_critic_metric_output(gaps: list[str]) -> TargetOutput:
    case = _case("critic-live-review")
    return TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=1,
        session_id="evaluation-critic-live-review-control",
        experiment_name="critic-live-review-control",
        trace_url="https://smith.langchain.com/o/x/r/critic-live-review-control",
        completed=True,
        result={
            "critique": {
                "score": 8,
                "gaps": gaps,
                "unsupported_claims": [],
                "recommended_queries": [],
                "should_continue": False,
                "rationale": "The report's evidence and limitations are clear.",
            }
        },
        react=ReActSummary(
            iterations=1,
            tool_calls=0,
            stop_reason="finished",
            max_iterations=case.expectations.max_iterations,
            tool_budget=case.expectations.max_tool_calls,
        ),
        target_model_requested="deepseek-v4-flash",
        target_model_returned="deepseek-v4-flash",
        target_reasoning_effort="high",
    )


def test_rationale_metric_distinguishes_grounded_review_from_fallback() -> None:
    case = _case("critic-live-review")
    base = _live_critic_metric_output([])
    normal = Critique(
        score=8,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale=(
            "The report covers commercial-scale deployment and durability "
            "and long-term performance data."
        ),
    )
    fallback, _ = fallback_critique(
        reason="provider_unavailable",
        iteration=case.state.iteration,
        max_iterations=case.state.max_iterations,
    )
    fallback_errors = [
        {
            "error_type": "critic_review_provider_error",
            "source": "agent.critic",
            "message": "provider review fallback used",
            "timestamp": "2026-08-01T00:00:00+00:00",
            "recoverable": False,
            "details": {
                "operation": "critic_report_review",
                "provider_failure": {
                    "kind": "provider_failure",
                    "type": "ProviderError",
                    "retryable": False,
                    "status_code": None,
                },
            },
        }
    ]

    def production_output(
        critique: Critique,
        *,
        errors: list[dict[str, object]] | None = None,
    ) -> TargetOutput:
        serialized = critique.model_dump(mode="json")
        return base.model_copy(
            update={
                "result": serialized,
                "state_update": {"critique": serialized},
                "errors": errors or [],
            }
        )

    normal_scores = deterministic_metric_scores(
        production_output(normal),
        case,
        metric_functions=METRIC_FUNCTIONS,
    )
    fallback_scores = deterministic_metric_scores(
        production_output(fallback, errors=fallback_errors),
        case,
        metric_functions=METRIC_FUNCTIONS,
    )

    assert normal_scores["rationale_present"] == 1.0
    assert fallback_scores["rationale_present"] == 0.0
    assert fallback_scores["score_bounded"] == 1.0
    assert fallback_scores["route_consistent"] == 1.0
    assert fallback_scores["no_spurious_gaps"] == 1.0


def test_a_fallback_review_fails_a_hard_gate() -> None:
    """A run with no critique must never be certifiable.

    The fallback's placeholder score of 1 with empty lists satisfies
    ``bounded_component_scores``, ``critique_actionable``, and
    ``route_consistent``, and its favourable judge reading lifted a live
    repetition's aggregate to 0.767, above the 0.75 threshold. Quality gates
    are AND-conditions, so the review gate is what stops that.
    """
    case = _case("critic-live-review")
    fallback, _ = fallback_critique(
        reason="provider_unavailable",
        iteration=case.state.iteration,
        max_iterations=case.state.max_iterations,
    )
    output = _live_critic_metric_output([]).model_copy(
        update={
            "errors": [
                {
                    "error_type": "critic_review_provider_error",
                    "source": "agent.critic",
                    "message": "provider review fallback used",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "recoverable": False,
                    "details": {
                        "operation": "critic_report_review",
                        "provider_failure": {
                            "kind": "schema_output",
                            "exception_type": "StructuredOutputError",
                        },
                    },
                }
            ]
        }
    )
    serialized = fallback.model_dump(mode="json")
    output = output.model_copy(
        update={"result": serialized, "state_update": {"critique": serialized}}
    )

    gates = {gate.gate_id: gate for gate in evaluate_agent_gates(output, case)}

    assert gates["review_produced"].passed is False
    assert "fell back" in gates["review_produced"].detail
    # The other gates still pass, which is exactly why this one is required.
    assert gates["bounded_component_scores"].passed is True
    assert gates["critique_actionable"].passed is True
    assert gates["route_consistent"].passed is True


def test_a_grounded_review_passes_the_review_gate() -> None:
    case = _case("critic-live-review")
    critique = Critique(
        score=8,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="The report covers commercial-scale deployment.",
    )
    serialized = critique.model_dump(mode="json")
    output = _live_critic_metric_output([]).model_copy(
        update={"result": serialized, "state_update": {"critique": serialized}}
    )

    gates = {gate.gate_id: gate for gate in evaluate_agent_gates(output, case)}

    assert gates["review_produced"].passed is True
    assert gates["review_produced"].detail == ""


def test_the_review_gate_is_registered_for_the_critic() -> None:
    assert "review_produced" in AGENT_GATE_IDS["critic"]


def _live_no_spurious_gaps_score(gap: str) -> float:
    case = _case("critic-live-review")
    scores = deterministic_metric_scores(
        _live_critic_metric_output([gap]),
        case,
        metric_functions=METRIC_FUNCTIONS,
    )
    return scores["no_spurious_gaps"]


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


@pytest.mark.parametrize(
    ("case_id", "scenario_name"),
    (
        ("approve-strong-report", "critic-strong-report"),
        ("request-more-research", "critic-gappy-report"),
        (
            "missing-evidence-or-budget-exhausted",
            "critic-budget-exhausted",
        ),
    ),
)
def test_controlled_scenarios_match_planned_queries_and_contract_version(
    case_id: str, scenario_name: str
) -> None:
    case = _case(case_id)
    script = SCENARIOS[scenario_name]
    planned_queries = {
        query
        for sub_topic in case.state.sub_topics
        for query in sub_topic.search_queries
    }

    # The case/reference semantics remain v1; the controlled fake scenario
    # contract is explicitly versioned so its repaired behavior is not
    # mistaken for an old v1 run.
    assert case.version == 1
    assert script.contract_version == CONTROLLED_SCENARIO_CONTRACT_VERSION
    # A tool-free Critic reaches no service, so its scenario scripts none, and
    # the case's own planned queries stay what they always were: the plan's.
    assert script.search_responses == {}
    assert planned_queries


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


def test_the_budget_case_no_longer_requires_a_tool_failure() -> None:
    """A tool-free Critic cannot hit the scripted memory failure.

    The expectation was satisfiable only by the spot-check loop Task 8
    removed. Keeping it would have made this case unsatisfiable rather than
    strict, so its 0.20 weight moved to the metric that can still fail —
    ``conservative_score`` — instead of to a replacement that cannot.
    """
    case = _case("missing-evidence-or-budget-exhausted")
    metric_ids = {
        metric.metric_id for metric in case.expectations.deterministic_metrics
    }

    assert case.expectations.must_record_recoverable_error is False
    assert "failure_recorded" not in metric_ids
    # The vacuous replacement the reviewer measured: always true for this
    # case, because it declares no reference themes and a rationale is
    # guaranteed non-blank.
    assert "rationale_present" not in metric_ids
    assert "conservative_score" in metric_ids
    weights = {
        metric.metric_id: metric.weight
        for metric in case.expectations.deterministic_metrics
    }
    assert weights["route_discipline"] == 0.60


@pytest.mark.parametrize("case_id", CONTROLLED + ("critic-live-review",))
def test_every_critic_case_allows_no_tool_call(case_id: str) -> None:
    """The budget gate is the agent's declaration, spelled as a ceiling."""
    assert _case(case_id).expectations.max_tool_calls == 0


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
    """A tool-free Critic reaches no live service but the model provider.

    The case used to require tavily and memory, because the spot-check loop
    searched the web and read memory. Task 8 removed that loop, so the honest
    declaration is empty: one that still demanded a Tavily key would ask Task
    13 for a credential the agent cannot spend.
    """
    live = cases_for("critic", "live")[0]

    assert live.expectations.required_live_dependencies == []


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

    # ``evaluation_state`` orders a case's sub-topics by priority and stamps
    # ``topic-NN`` on that order, exactly as the Planner does, so the
    # covered, highest-priority sub-topic is listed first even though the
    # fixture writes its tuple starting with the two gap sub-topics.
    assert tuple(
        topic.title for topic in case.state.sub_topics
    ) == (
        "Measured methane reductions from composting mandates",
        "Participation in municipal composting mandates",
        "Landfill methane measurement methodology",
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


@pytest.mark.parametrize(
    "scenario_name",
    ("critic-strong-report", "critic-gappy-report", "critic-budget-exhausted"),
)
def test_every_critic_scenario_scripts_no_service(scenario_name: str) -> None:
    """The stored invariants of a tool-free agent's scenario.

    These scenarios used to script a search result and a memory entry for the
    spot-check loop Task 8 removed, which made them read as live coverage for
    an agent that cannot call a tool. An empty script is the honest fixture:
    the tools still exist in the bundle, and calling one is a scenario miss.
    """
    script = SCENARIOS[scenario_name]

    assert script.search_responses == {}
    assert script.http_pages == {}
    assert script.memory_entries == ()
    assert script.failures == {}
    assert script.scripted_search_urls == ()
    assert script.reputations == {}
    assert script.reputation_failures == {}


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


def test_live_no_spurious_gaps_rejects_covered_commercial_deployment() -> None:
    gap = (
        "The report is missing commercial-scale deployment, although it "
        "fully describes commercial-scale deployment."
    )

    assert _live_no_spurious_gaps_score(gap) == 0.0


def test_live_no_spurious_gaps_accepts_acknowledged_durability_limitation() -> None:
    gap = (
        "The report acknowledges the absence of multi-decade field records "
        "for newest formulations, and obtaining those records would resolve "
        "durability and long-term performance under field exposure."
    )

    assert _live_no_spurious_gaps_score(gap) == 1.0


def test_live_no_spurious_gaps_rejects_paraphrased_covered_deployment() -> None:
    gap = "The report does not cover deployment at commercial scale."

    assert _live_no_spurious_gaps_score(gap) == 0.0


def test_live_no_spurious_gaps_accepts_full_theme_acknowledged_limitation() -> None:
    gap = (
        "Additional durability and long-term performance data under field "
        "exposure are needed."
    )

    assert _live_no_spurious_gaps_score(gap) == 1.0


def test_live_no_spurious_gaps_rejects_compressive_strength_covered_gap() -> None:
    gap = "The report does not cover compressive strength standards."

    assert _live_no_spurious_gaps_score(gap) == 0.0


@pytest.mark.asyncio
async def test_a_critic_bundle_scripts_no_service_for_any_tool(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The bundle still builds, and its unscripted tools are a scenario miss.

    This test used to prove that the budget scenario's memory failure was real
    and its search recovered. Both scripted services are gone with the
    spot-check loop: a tool-free Critic reaches neither, so the honest
    invariant is the guard — the tools exist in the bundle, and calling one
    that nothing scripted is recorded as a miss rather than quietly answered.
    """
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
            query="congestion pricing particulate pollution evidence",
            max_results=5,
        )

    # Memory still answers: it is a real store with nothing seeded, not a
    # scripted outage.
    assert memory_result.success, memory_result.error
    assert memory_result.data["matches"] == []

    # Search is unscripted on purpose, and the harness says so loudly.
    assert search_result.success is False
    assert search_result.error is not None

    ledger = bundle.recorder.ledger()
    summaries = {summary.tool_name: summary for summary in ledger.tool_calls}
    assert summaries["query_memory"].calls == 1
    assert summaries["query_memory"].failures == 0
    assert summaries["web_search"].calls == 1
    assert summaries["web_search"].failures == 1
    assert ledger.prohibited_calls == []


# --- Task 8: the eight paired calibration cases ------------------------------
#
# Each case is one scripted review of one fixed report, scored by the real
# local build. The assertions are bands and orderings, never a demanded exact
# score: the point is that a strong answer is not pulled down by polish-free
# honesty, that a minor omission does not collapse a sound answer, and that
# confident prose cannot buy acceptance. Paid semantic calibration is Task 13;
# this is the contract the live run is measured against.


def test_the_calibration_cases_are_the_eight_named_cases() -> None:
    assert tuple(case.case_id for case in CALIBRATION_CASES) == (
        "calibration-strong-answer",
        "calibration-one-minor-gap",
        "calibration-missing-critical-topic",
        "calibration-unsupported-central-assertion",
        "calibration-attributed-primary-fact",
        "calibration-false-independent-pair",
        "calibration-polished-verbose-non-answer",
        "calibration-honest-but-incomplete-answer",
    )


def test_calibration_strong_answer() -> None:
    """A complete, well-cited answer is accepted near the top of the scale."""
    outcome = measure_critic_calibration().outcome("calibration-strong-answer")

    assert outcome.in_band
    assert outcome.score >= ACCEPTANCE_SCORE
    assert outcome.accepted is True
    assert outcome.defect_found is False
    assert not outcome.false_acceptance
    assert not outcome.false_rejection


def test_calibration_one_minor_gap() -> None:
    """One minor omission must not collapse an otherwise sound answer.

    The defect is real and has to be named, and it is deliberately a wording
    defect with no search behind it: naming it costs no research pass.
    """
    outcome = measure_critic_calibration().outcome("calibration-one-minor-gap")

    assert outcome.in_band
    assert outcome.score >= ACCEPTANCE_SCORE
    assert outcome.accepted is True
    assert outcome.defect_found is True
    assert not outcome.false_rejection
    assert not outcome.false_acceptance


def test_calibration_missing_critical_topic() -> None:
    """A critical planned topic the report never answers is a rejection."""
    outcome = measure_critic_calibration().outcome(
        "calibration-missing-critical-topic"
    )

    assert outcome.in_band
    assert outcome.score < ACCEPTANCE_SCORE
    assert outcome.accepted is False
    assert outcome.defect_found is True
    assert not outcome.false_acceptance


def test_calibration_unsupported_central_assertion() -> None:
    """The central conclusion rests on no read, so the answer is rejected."""
    outcome = measure_critic_calibration().outcome(
        "calibration-unsupported-central-assertion"
    )

    assert outcome.in_band
    assert outcome.score < ACCEPTANCE_SCORE
    assert outcome.accepted is False
    assert outcome.defect_found is True


def test_calibration_attributed_primary_fact() -> None:
    """Primary-source attribution is not a failure (Section 2.1).

    The statement under review carries ``source_supported`` — one publisher,
    no independent pair — and is correctly attributed. That is a legitimate
    reading, not a defect, so the case must not be rejected for it.
    """
    packet = calibration_packet()
    modes = {
        statement.statement_id: statement.mode for statement in packet.statements
    }
    assert modes[CALIBRATION_STATEMENT_IDS["mechanism"]] == "attributed"
    assert modes[CALIBRATION_STATEMENT_IDS["figure"]] == "settled"

    outcome = measure_critic_calibration().outcome(
        "calibration-attributed-primary-fact"
    )

    assert outcome.in_band
    assert outcome.score >= ACCEPTANCE_SCORE
    assert outcome.accepted is True
    assert outcome.defect_found is False
    assert not outcome.false_rejection


def test_calibration_false_independent_pair() -> None:
    """Two URLs from one publisher are not independent corroboration."""
    outcome = measure_critic_calibration().outcome(
        "calibration-false-independent-pair"
    )

    assert outcome.in_band
    assert outcome.score < ACCEPTANCE_SCORE
    assert outcome.accepted is False
    assert outcome.defect_found is True


def test_calibration_polished_verbose_non_answer() -> None:
    """Length and confidence cannot buy a passing score."""
    case = calibration_case("calibration-polished-verbose-non-answer")
    outcome = measure_critic_calibration().outcome(
        "calibration-polished-verbose-non-answer"
    )

    assert len(case.report.split()) >= 300
    assert outcome.in_band
    assert outcome.score < ACCEPTANCE_SCORE
    assert outcome.accepted is False
    assert outcome.defect_found is True


def test_calibration_honest_but_incomplete_answer() -> None:
    """Honest insufficiency is not accepted in place of a useful answer.

    Section 2.1 removed the blanket-abstention reward: a report that says
    plainly it could not answer is scored on what it established, not on its
    candour.
    """
    outcome = measure_critic_calibration().outcome(
        "calibration-honest-but-incomplete-answer"
    )

    assert outcome.in_band
    assert outcome.score < ACCEPTANCE_SCORE
    assert outcome.accepted is False
    assert outcome.defect_found is True


def test_the_calibration_orders_bands_by_evidence_not_prose() -> None:
    """Ordering, not one demanded score, is what the pairs calibrate.

    Every flag below is one direction of a pair: an attributed primary fact
    outranks an honest non-answer, which outranks a polished non-answer, and
    a sound answer with one minor gap outranks a missing critical topic.
    """
    report = measure_critic_calibration()
    score = {
        case.case_id: report.outcome(case.case_id).score
        for case in CALIBRATION_CASES
    }

    assert score["calibration-strong-answer"] > score[
        "calibration-honest-but-incomplete-answer"
    ]
    assert score["calibration-attributed-primary-fact"] > score[
        "calibration-polished-verbose-non-answer"
    ]
    assert score["calibration-honest-but-incomplete-answer"] > score[
        "calibration-polished-verbose-non-answer"
    ]
    assert score["calibration-one-minor-gap"] > score[
        "calibration-missing-critical-topic"
    ]
    assert score["calibration-strong-answer"] > score[
        "calibration-unsupported-central-assertion"
    ]


def test_every_calibration_gap_names_a_record_the_packet_carries() -> None:
    """The scripted gaps are examples of the contract, not of a loose one."""
    packet = calibration_packet()
    statement_ids = {item.statement_id for item in packet.statements}
    cluster_ids = {
        cluster_id
        for item in packet.statements
        for cluster_id in item.claim_cluster_ids
    }
    target_ids = {target.target_id for target in packet.targets}
    assert statement_ids == set(CALIBRATION_STATEMENT_IDS.values())
    assert cluster_ids == set(CALIBRATION_CLUSTER_IDS.values())
    assert target_ids == set(CALIBRATION_TARGET_IDS.values())

    for case in CALIBRATION_CASES:
        for gap in case.draft["gaps"]:
            assert set(gap.get("statement_ids", [])) <= statement_ids, case.case_id
            assert set(gap.get("claim_cluster_ids", [])) <= cluster_ids, case.case_id
            assert (
                set(gap.get("target_ids", [])) - {"question"}
            ) <= target_ids, case.case_id
            if gap.get("repair_action") == "acquire":
                assert gap["target_ids"], case.case_id


def test_every_calibration_draft_validates_against_the_packet() -> None:
    """Every scripted review is a legal ``CritiqueDraft``, checked locally."""
    report = measure_critic_calibration()

    for case in CALIBRATION_CASES:
        outcome = report.outcome(case.case_id)
        assert outcome.gap_count == len(case.draft["gaps"]), case.case_id
        assert outcome.band == case.score_band, case.case_id


def test_the_calibration_build_is_offline_and_provider_free() -> None:
    """No provider, no network: the scores here are the local build's."""
    report = measure_critic_calibration()

    assert report.provider_calls == 0
    assert len(report.outcomes) == len(CALIBRATION_CASES)
