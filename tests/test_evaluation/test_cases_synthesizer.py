"""Synthesizer evaluation cases: citations, conflict, and write recovery."""

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
    "complete-cited-report",
    "conflict-and-limitations",
    "write-or-memory-failure",
)

_METRICS = {
    "complete-cited-report": (
        ("report_present", 0.20),
        ("citations_known", 0.30),
        ("coverage", 0.25),
        ("limitations_present", 0.15),
        ("persistence_truthful", 0.10),
    ),
    "conflict-and-limitations": (
        ("conflict_represented", 0.35),
        ("no_overstatement", 0.25),
        ("limitations_present", 0.20),
        ("citations_known", 0.20),
    ),
    "write-or-memory-failure": (
        ("report_present_in_state", 0.35),
        ("failure_recorded", 0.30),
        ("no_false_persistence_claim", 0.25),
        ("citations_known", 0.10),
    ),
    "synthesizer-live-report": (
        ("report_present", 0.20),
        ("citations_known", 0.30),
        ("coverage", 0.25),
        ("limitations_present", 0.15),
        ("persistence_truthful", 0.10),
    ),
}

# The claim texts are shared with the dependency scenarios only where the
# case pins them: the conflicted case's reference names its two contradicted
# claim texts, and the write-failure case's reference names the two failing
# tool names. The case tests pin both sides to the same literals.

_LONDON_CLAIM = (
    "Congestion pricing reduced average travel times in the central London "
    "congestion charging zone during charging hours."
)
_NYC_CLAIM = (
    "New York City's congestion pricing reduced average travel times in the "
    "Manhattan central business district."
)
_TRAVEL_SAVINGS_CLAIM = (
    "Comprehensive congestion pricing is associated with travel-time "
    "savings of 10 to 30 percent across cities."
)
_PILOT_CLAIM = (
    "A mid-sized U.S. city's congestion pricing pilot reduced travel times "
    "across its whole road network."
)

_MANDATE_VACANCY_CLAIM = (
    "Return-to-office mandates raised office vacancy rates in major U.S. "
    "metros."
)
_MANDATE_DRIVER_CLAIM = (
    "Remote-work mandates are the primary driver of downtown office vacancy."
)
_HYBRID_CLAIM = (
    "Hybrid work policies will keep commercial vacancy elevated through "
    "2030."
)

_DEPTH_CLAIM = (
    "Deep building retrofits achieve substantially larger realized energy "
    "savings than shallow ones."
)
_REALIZATION_CLAIM = (
    "Realized savings from deep retrofits are often below modeled values."
)
_BEHAVIOR_CLAIM = (
    "Occupant behavior can narrow the realized-savings gap between shallow "
    "and deep retrofits."
)

_HEAT_PUMP_COST_CLAIM = (
    "Heat-pump retrofit costs in temperate climates are now comparable to "
    "furnace replacements when incentives are counted."
)
_PAYBACK_CLAIM = (
    "Policy incentives shorten heat-pump retrofit payback periods to under "
    "ten years in temperate markets."
)

# All URLs are written in the normalized form the agent records (no
# ``www.``, no trailing slash), so a gate comparison can never
# byte-mismatch.
_TFL_URL = "https://tfl.gov.uk/congestion-charging-monitoring"
_NYC_URL = "https://nyc.gov/congestion-pricing-travel-time-evaluation"
_SDIRECT_URL = "https://sciencedirect.com/congestion-pricing-travel-time-review"
_OECD_URL = "https://oecd.org/road-pricing-travel-times"
_WORLDBANK_URL = "https://worldbank.org/urban-mobility-congestion-pricing"
_PILOT_URL = "https://transportationresearch.example.org/congestion-pricing-pilot"
_MIXED_URLS = (
    _TFL_URL,
    _NYC_URL,
    _SDIRECT_URL,
    _OECD_URL,
    _WORLDBANK_URL,
    _PILOT_URL,
)

_NBER_URL = "https://nber.org/rto-mandates-office-vacancy"
_URBAN_URL = "https://urbaninstitute.org/rto-office-vacancy-analysis"
_TRACKER_URL = "https://commercialedge.example.com/rto-vacancy-tracker"
_BLOOMBERG_URL = "https://bloomberg.com/office-vacancy-rto-analysis"
_BROOKINGS_URL = "https://brookings.edu/remote-work-office-demand"
_CONFLICT_URLS = (
    _NBER_URL,
    _URBAN_URL,
    _TRACKER_URL,
    _BLOOMBERG_URL,
    _BROOKINGS_URL,
)

_LBNL_URL = "https://lbnl.gov/deep-retrofit-savings-analysis"
_ACEEE_URL = "https://aceee.org/retrofit-depth-field-studies"
_RETROFIT_SDIRECT_URL = "https://sciencedirect.com/retrofit-depth-realization-gap"
_RETROFIT_REVIEW_URL = "https://retrofitreview.example.org/depth-case-studies"
_FAILURE_URLS = (
    _LBNL_URL,
    _ACEEE_URL,
    _RETROFIT_SDIRECT_URL,
    _RETROFIT_REVIEW_URL,
)

_DOE_URL = "https://energy.gov/eere/buildings/heat-pump-systems"
_NREL_URL = "https://nrel.gov/research/buildings/heat-pump-retrofit-costs"
_IEA_URL = "https://iea.org/energy-system/buildings/heating"
_LIVE_URLS = (_DOE_URL, _NREL_URL, _IEA_URL)

_REFERENCES = {
    "complete-cited-report": {
        "known_citation_urls": list(_MIXED_URLS),
        "required_sections": ["summary", "findings", "limitations"],
        "minimum_cited_sources": 4,
    },
    "conflict-and-limitations": {
        "conflicting_claim_texts": [_MANDATE_VACANCY_CLAIM, _MANDATE_DRIVER_CLAIM],
        "required_caveat_signals": ["conflict", "mixed", "uncertain", "limited"],
        "forbidden_overstatement": ["proves", "conclusively", "definitively"],
    },
    "write-or-memory-failure": {
        "expected_error_sources": ["write_document", "save_to_memory"],
        "forbidden_persistence_claims": ["saved to", "written to", "stored at"],
    },
    "synthesizer-live-report": {
        "known_citation_urls": list(_LIVE_URLS),
        "required_sections": ["summary", "findings", "limitations"],
        "minimum_cited_sources": 3,
    },
}

_RUBRIC_DIMENSIONS = {
    "complete-cited-report": {
        "evidence_fidelity",
        "limitations_honesty",
        "citation_faithfulness",
        "structure_quality",
    },
    "conflict-and-limitations": {
        "evidence_fidelity",
        "limitations_honesty",
        "uncertainty_representation",
    },
    "write-or-memory-failure": {
        "evidence_fidelity",
        "limitations_honesty",
        "persistence_honesty",
    },
    "synthesizer-live-report": {
        "evidence_fidelity",
        "limitations_honesty",
        "citation_faithfulness",
        "structure_quality",
    },
}

_LIVE_DOMAINS = {"energy.gov", "nrel.gov", "iea.org"}


def _all_synthesizer_cases():
    return [
        *cases_for("synthesizer", "controlled"),
        *cases_for("synthesizer", "live"),
    ]


def _case(case_id: str):
    return next(
        item
        for item in _all_synthesizer_cases()
        if item.case_id == case_id
    )


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("synthesizer", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("synthesizer", "live")

    assert len(live) == 1
    assert live[0].case_id == "synthesizer-live-report"


def test_every_case_expects_the_report_output() -> None:
    for case in _all_synthesizer_cases():
        assert case.expectations.required_output_fields == ["report"]


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("synthesizer", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_every_case_names_its_scenario() -> None:
    assert _case("complete-cited-report").dependency_scenario == (
        "synthesizer-complete"
    )
    assert _case("conflict-and-limitations").dependency_scenario == (
        "synthesizer-conflicted"
    )
    assert _case("write-or-memory-failure").dependency_scenario == (
        "synthesizer-write-failure"
    )


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("synthesizer", "live")[0]

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
    case = _case("write-or-memory-failure")

    assert case.expectations.must_record_recoverable_error is True


def test_each_case_pins_its_finding_and_claim_counts() -> None:
    expected = {
        "complete-cited-report": (6, 4),
        "conflict-and-limitations": (5, 3),
        "write-or-memory-failure": (4, 3),
        "synthesizer-live-report": (3, 2),
    }
    for case_id, (findings, claims) in expected.items():
        case = _case(case_id)
        assert len(case.state.raw_findings) == findings, case_id
        assert len(case.state.verified_claims) == claims, case_id


def test_the_live_case_declares_the_dependencies_it_needs() -> None:
    """The live Synthesizer performs a real document write and real memory
    saves — the one agent whose live case writes documents — and declares
    no search or HTTP tools."""
    live = cases_for("synthesizer", "live")[0]

    assert live.expectations.required_live_dependencies == [
        "documents",
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
    cases = _all_synthesizer_cases()

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


def test_the_complete_case_cites_exactly_its_six_urls() -> None:
    case = _case("complete-cited-report")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _MIXED_URLS


def test_the_conflict_case_cites_exactly_its_five_urls() -> None:
    case = _case("conflict-and-limitations")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _CONFLICT_URLS


def test_the_failure_case_cites_exactly_its_four_urls() -> None:
    case = _case("write-or-memory-failure")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _FAILURE_URLS


def test_the_live_case_cites_exactly_its_three_urls() -> None:
    case = _case("synthesizer-live-report")

    assert tuple(
        finding.source_url for finding in case.state.raw_findings
    ) == _LIVE_URLS


def test_the_live_case_cites_three_real_stable_domains() -> None:
    case = _case("synthesizer-live-report")

    assert len(case.state.raw_findings) == 3
    assert {
        source_domain(finding.source_url)
        for finding in case.state.raw_findings
    } == _LIVE_DOMAINS


def test_the_complete_case_carries_matching_evaluated_sources() -> None:
    """One ScoredSource per finding URL, no extras, no strays."""
    case = _case("complete-cited-report")

    assert tuple(
        source.url for source in case.state.evaluated_sources
    ) == _MIXED_URLS
    assert all(
        0.62 <= source.overall_score <= 0.91
        for source in case.state.evaluated_sources
    )


def test_the_complete_case_declares_three_subtopics() -> None:
    case = _case("complete-cited-report")

    assert tuple(
        topic.title for topic in case.state.sub_topics
    ) == (
        "London congestion charging evidence",
        "New York congestion pricing results",
        "Evidence beyond London and New York",
    )
    assert {topic.title for topic in case.state.sub_topics} == {
        finding.related_sub_topic for finding in case.state.raw_findings
    }


def test_the_complete_case_claims_split_verified_and_unverified() -> None:
    case = _case("complete-cited-report")

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
    assert len(verified) == 3
    assert all(claim.confidence >= 0.75 for claim in verified)
    assert len(unverified) == 1
    assert tuple(
        claim.text for claim in case.state.verified_claims
    ) == (
        _LONDON_CLAIM,
        _NYC_CLAIM,
        _TRAVEL_SAVINGS_CLAIM,
        _PILOT_CLAIM,
    )


def test_the_conflict_case_findings_split_two_against_three() -> None:
    """Two findings report vacancy rising with mandates; the other three
    report no significant, mandate-driven change."""
    case = _case("conflict-and-limitations")
    findings = case.state.raw_findings

    assert len(findings) == 5
    mandate_side = {_NBER_URL, _URBAN_URL}
    against_side = {_TRACKER_URL, _BLOOMBERG_URL, _BROOKINGS_URL}
    assert set(finding.source_url for finding in findings) == (
        mandate_side | against_side
    )

    for finding in findings:
        if finding.source_url in mandate_side:
            assert any(
                signal in finding.content.casefold()
                for signal in ("rose", "climbed", "increased")
            ), finding.source_url
        else:
            assert any(
                signal in finding.content.casefold()
                for signal in (
                    "no statistically significant",
                    "normal cyclical range",
                    "hybrid schedules",
                )
            ), finding.source_url


def test_the_conflict_case_carries_the_memory_strategy() -> None:
    case = _case("conflict-and-limitations")

    assert case.state.memory_context.suggested_strategies == [
        "state disagreements explicitly"
    ]


def test_the_conflict_case_reference_pins_the_contradicted_claims() -> None:
    case = _case("conflict-and-limitations")
    reference = case.expectations.reference

    contradicted = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "contradicted"
    ]
    insufficient = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "insufficient_evidence"
    ]
    assert len(contradicted) == 2
    assert len(insufficient) == 1

    # The two contradicted claims are exactly the texts the reference pins,
    # and both name the three against-side sources among their
    # contradictions — the two-against-three split is encoded, not assumed.
    assert [claim.text for claim in contradicted] == reference[
        "conflicting_claim_texts"
    ]
    for claim in contradicted:
        assert claim.contradictions
        assert len(claim.contradictions) >= 2, claim.text


def test_the_failure_case_carries_sources_and_verified_claims() -> None:
    case = _case("write-or-memory-failure")

    assert tuple(
        source.url for source in case.state.evaluated_sources
    ) == _FAILURE_URLS
    verified = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "verified"
    ]
    assert len(verified) == 3
    # Verified and confident enough that the Synthesizer would attempt a
    # memory save for every one of them — the save is what fails.
    assert all(claim.confidence >= 0.7 for claim in verified)


def test_the_live_case_carries_two_verified_claims() -> None:
    case = _case("synthesizer-live-report")

    verified = [
        claim
        for claim in case.state.verified_claims
        if claim.verdict == "verified"
    ]
    assert len(verified) == 2
    assert all(claim.confidence >= 0.75 for claim in verified)
    assert tuple(
        claim.text for claim in case.state.verified_claims
    ) == (_HEAT_PUMP_COST_CLAIM, _PAYBACK_CLAIM)


def test_the_live_case_reuses_the_complete_cases_metrics() -> None:
    complete = _case("complete-cited-report")
    live = _case("synthesizer-live-report")

    assert tuple(
        (metric.metric_id, metric.weight)
        for metric in live.expectations.deterministic_metrics
    ) == tuple(
        (metric.metric_id, metric.weight)
        for metric in complete.expectations.deterministic_metrics
    )


def test_the_complete_and_conflict_scenarios_script_no_failures() -> None:
    for case_id in ("complete-cited-report", "conflict-and-limitations"):
        script = SCENARIOS[_case(case_id).dependency_scenario]

        assert script.failures == {}
        assert script.search_responses == {}
        assert script.http_pages == {}


def test_the_write_failure_scenario_fails_exactly_the_reference_tools() -> None:
    """The write-or-memory case scripts both persistence tools to fail when
    called: a read-only filesystem for the document write and an unavailable
    memory backend for the save."""
    script = SCENARIOS["synthesizer-write-failure"]
    case = _case("write-or-memory-failure")

    assert set(script.failures) == set(
        case.expectations.reference["expected_error_sources"]
    ) == {"write_document", "save_to_memory"}

    write_failure = script.failures["write_document"]
    save_failure = script.failures["save_to_memory"]
    assert isinstance(write_failure, OSError)
    assert "read-only file system" in str(write_failure)
    assert isinstance(save_failure, RuntimeError)
    assert "long-term memory is unavailable" in str(save_failure)

    # Non-httpx, non-retryable: the real tools retry httpx timeouts and
    # status errors (three attempts each), which would triple-count a
    # scripted failure in the call ledger.
    assert not isinstance(write_failure, httpx.HTTPError)
    assert not isinstance(save_failure, httpx.HTTPError)


def test_every_case_declares_its_known_source_urls() -> None:
    expected: dict[str, set[str]] = {
        "complete-cited-report": set(_MIXED_URLS),
        "conflict-and-limitations": set(_CONFLICT_URLS),
        "write-or-memory-failure": set(_FAILURE_URLS),
        "synthesizer-live-report": set(_LIVE_URLS),
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


@pytest.mark.asyncio
async def test_the_complete_double_writes_and_saves(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """The complete scenario's tools both succeed, land their artifacts, and
    record their outcomes in the repetition ledger."""
    case = _case("complete-cited-report")
    bundle = build_controlled_dependencies(
        runtime_config_for("synthesizer"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )
    writer = next(tool for tool in bundle.tools if tool.name == "write_document")
    saver = next(
        tool for tool in bundle.tools if tool.name == "save_to_memory"
    )

    async with tracker.session_span("evaluation-1", "q"):
        written = await writer.execute(
            filename="report-1.md", content="# Report\n\nBody."
        )
        saved = await saver.execute(
            content="A verified finding worth keeping.",
            metadata={"entry_type": "finding", "confidence": 0.8},
        )

    assert written.success, written.error
    assert written.data == {"path": "report-1.md", "bytes_written": 15}
    assert (bundle.document_directory / "report-1.md").is_file()

    assert saved.success, saved.error
    assert saved.data["entry_id"]

    ledger = bundle.recorder.ledger()
    summaries = {summary.tool_name: summary for summary in ledger.tool_calls}
    assert summaries["write_document"].calls == 1
    assert summaries["write_document"].failures == 0
    assert summaries["save_to_memory"].calls == 1
    assert summaries["save_to_memory"].failures == 0
    assert ledger.document_writes == 1
    assert ledger.memory_writes == 1
    assert ledger.prohibited_calls == []


@pytest.mark.asyncio
async def test_the_write_failure_double_fails_both_tools(
    tracker, settings, tmp_path, runtime_config_for
) -> None:
    """Both persistence tools are present and both fail when called: the
    document write hits a read-only filesystem and the memory save raises.
    The failure is the case's point, so the ledger records both as one
    failed call each — never retried, never triple-counted."""
    case = _case("write-or-memory-failure")
    bundle = build_controlled_dependencies(
        runtime_config_for("synthesizer"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )
    writer = next(tool for tool in bundle.tools if tool.name == "write_document")
    saver = next(
        tool for tool in bundle.tools if tool.name == "save_to_memory"
    )

    # The output directory was replaced by a plain file, so the write fails
    # deterministically on every platform — the case must never depend on
    # read-only permissions that a CI user can bypass.
    assert bundle.document_directory.is_file()

    async with tracker.session_span("evaluation-1", "q"):
        written = await writer.execute(
            filename="report-1.md", content="# Report\n\nBody."
        )
        saved = await saver.execute(
            content="A verified finding worth keeping.",
            metadata={"entry_type": "finding", "confidence": 0.8},
        )

    assert written.success is False
    assert written.error is not None
    assert written.error.type == "FileExistsError"
    assert written.error.recoverable is True

    assert saved.success is False
    assert saved.error is not None
    assert saved.error.type == "RuntimeError"
    assert saved.error.message == "long-term memory is unavailable"
    assert saved.error.recoverable is True

    ledger = bundle.recorder.ledger()
    summaries = {summary.tool_name: summary for summary in ledger.tool_calls}
    assert summaries["write_document"].calls == 1
    assert summaries["write_document"].failures == 1
    assert summaries["save_to_memory"].calls == 1
    assert summaries["save_to_memory"].failures == 1
    assert ledger.document_writes == 0
    assert ledger.memory_writes == 0
    assert ledger.prohibited_calls == []
    assert not (bundle.document_directory / "report-1.md").exists()
