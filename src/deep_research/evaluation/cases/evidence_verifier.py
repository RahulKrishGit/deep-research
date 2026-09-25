"""Evidence Verifier evaluation cases: Figure Match and the Context Check.

Every controlled case seeds one page, the finding whose snippet is on it, and
the figures the snippet states. The graded behaviour is the verifier's own
enforcement (§5.2, D8): a scope correction the page carries is applied, a
relay is recorded under the organisation the page credits, and evidence words
the page does not carry drop the figure whatever the Context Check asserts.

The Context Check itself is scripted by the tests that drive these cases: no
controlled scenario registers a service, because the agent declares no tools
and reaches none.
"""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    evaluation_state,
    figure,
    finding,
    metrics,
    read_record,
    rubric,
    sub_topic,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase
from deep_research.utils.types import ReadRecord

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would let
# one case's mutations leak into the others. The dimensions are shared as
# plain tuples — rubric() copies them into a fresh JudgeRubric per call.

_CONTEXT_DIMENSIONS = (
    (
        "context_fidelity",
        "Each kept figure carries the period, scope, kind and organisation its page states.",
        "Every kept figure's recorded context matches what the passage says, and a "
        "correction is only made where the page carries the corrected wording.",
        "A figure keeps a period, scope, kind or organisation the page does not state.",
    ),
    (
        "attribution_honesty",
        "A relayed figure is credited to the organisation the page credits.",
        "A page that hands the figure to another body is recorded as relayed, under "
        "that body's name, and a page stating its own figure is recorded as own.",
        "A relay is presented as the relaying site's own figure, or credit is invented.",
    ),
)

_DROP_DIMENSIONS = (
    (
        "refusal_discipline",
        "A figure or finding the page does not support is dropped, with its reason named.",
        "Invented or unsupported evidence is dropped and the recorded reason says why.",
        "An unsupported figure is kept, or a drop is recorded with no reason.",
    ),
)


_SCOPE_RUBRIC = rubric(
    "evidence-verifier-scope-correction",
    *_CONTEXT_DIMENSIONS,
)

_RELAY_RUBRIC = rubric(
    "evidence-verifier-relay",
    *_CONTEXT_DIMENSIONS,
)

_REJECTION_RUBRIC = rubric(
    "evidence-verifier-invented-evidence",
    *_DROP_DIMENSIONS,
)

_LIVE_RUBRIC = rubric(
    "evidence-verifier-live-benchmark",
    *_CONTEXT_DIMENSIONS,
    *_DROP_DIMENSIONS,
)

# All URLs are written in the normalized form the agent records (no ``www.``,
# no trailing slash), so a gate comparison can never byte-mismatch.

_WOODMAC_URL = "https://woodmac.com/press-releases/2025-us-energy-storage"
_WOODMAC_TITLE = "2025 storage record | Wood Mackenzie"
_WOODMAC_PAGE = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery "
    "energy storage system installations in 2025, a 52% increase over 2024, "
    "across all segments. Grid-scale storage installations are forecasted to "
    "reach 13.3 GW in 2025."
)
_WOODMAC_SNIPPET = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery "
    "energy storage system installations in 2025"
)

_RELAY_URL = "https://utilitydive.com/news/storage-2025"
_RELAY_TITLE = "Storage in 2025 | Utility Dive"
_RELAY_PAGE = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW "
    "in 2025. Analysts expect further growth."
)
_RELAY_SNIPPET = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW "
    "in 2025."
)

_EIA_URL = "https://eia.gov/todayinenergy/battery-capacity-2024"
_EIA_TITLE = "U.S. battery capacity increased 66% in 2024 | U.S. Energy Information Administration"
_EIA_PAGE = (
    "U.S. battery capacity increased 66% in 2024. Generators added 10.4 "
    "gigawatts (GW) of new battery storage capacity in 2024, the second-largest "
    "generating capacity addition after solar, according to our January 2025 "
    "Preliminary Monthly Electric Generator Inventory. In 2025, capacity growth "
    "from battery storage could set a record as operators report plans to add "
    "19.6 GW of utility-scale battery storage to the grid."
)
_EIA_ACTUAL_SNIPPET = (
    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in "
    "2024"
)
_EIA_FORECAST_SNIPPET = (
    "In 2025, capacity growth from battery storage could set a record as "
    "operators report plans to add 19.6 GW of utility-scale battery storage to "
    "the grid"
)

_LIVE_PAGES = {
    _EIA_URL: (_EIA_TITLE, _EIA_PAGE),
    _WOODMAC_URL: (_WOODMAC_TITLE, _WOODMAC_PAGE),
    _RELAY_URL: (_RELAY_TITLE, _RELAY_PAGE),
}


def _read(
    case_id: str, url: str, *, title: str, text: str
) -> ReadRecord:
    """One seeded page, titled exactly as the benchmark's own page is."""
    return read_record(url, case_id=case_id, title=title, text=text)


_SCOPE_READ = _read(
    "scope-corrected-to-all-segments", _WOODMAC_URL, title=_WOODMAC_TITLE, text=_WOODMAC_PAGE
)
_INVENTED_READ = _read(
    "invented-evidence-words-rejected", _WOODMAC_URL, title=_WOODMAC_TITLE, text=_WOODMAC_PAGE
)
_RELAY_READ = _read("relay-labelled-as-relay", _RELAY_URL, title=_RELAY_TITLE, text=_RELAY_PAGE)
_LIVE_EIA_READ = _read(
    "evidence-verifier-live-benchmark", _EIA_URL, title=_EIA_TITLE, text=_EIA_PAGE
)
_LIVE_WOODMAC_READ = _read(
    "evidence-verifier-live-benchmark", _WOODMAC_URL, title=_WOODMAC_TITLE, text=_WOODMAC_PAGE
)
_LIVE_RELAY_READ = _read(
    "evidence-verifier-live-benchmark", _RELAY_URL, title=_RELAY_TITLE, text=_RELAY_PAGE
)


# --- 1. A scope the page corrects -------------------------------------------


_SCOPE = build_case(
    case_id="scope-corrected-to-all-segments",
    agent_name="evidence_verifier",
    tier="controlled",
    title="Correct a figure's scope to the basis the page states",
    purpose=(
        "The extractor recorded Wood Mackenzie's 18.9 GW as grid-scale; the "
        "page states it 'across all segments'. The Context Check confirms "
        "the figure and corrects the scope, and code keeps the correction "
        "because its wording is in evidence words the page itself carries. "
        "The graded behaviour is verified_corrected with scope "
        "'all segments', not the recorded scope."
    ),
    state=evaluation_state(
        case_id="scope-corrected-to-all-segments",
        question="How much battery storage capacity was installed in the United States in 2025?",
        sub_topics=(
            sub_topic(
                "U.S. battery storage installations",
                rationale="The market total is the question's headline figure.",
                queries=["us battery storage installations 2025"],
                criteria=["A market-wide installation figure for 2025"],
                priority=1,
            ),
        ),
        reads=(_SCOPE_READ,),
        findings=(
            finding(
                _WOODMAC_SNIPPET,
                url=_WOODMAC_URL,
                title=_WOODMAC_TITLE,
                sub_topic_title="U.S. battery storage installations",
                snippet=_WOODMAC_SNIPPET,
                read_id=_SCOPE_READ.read_id,
                locator="body",
                figures=(figure("18.9", "gigawatts", period="2025", kind="actual"),),
                data_period="2025",
                measure_scope="Grid-scale",
            ),
        ),
    ),
    dependency_scenario="evidence-verifier-scope-correction",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            # The recorded scope is the defect; nothing else about this
            # finding may move.
            "expected_outcomes": [
                {
                    "source_url": _WOODMAC_URL,
                    "status": "verified_corrected",
                    "scope": "all segments",
                    "period": "2025",
                    "kind": "actual",
                    "attribution": "own",
                    "organisation": "Wood Mackenzie",
                },
            ],
            "recorded_scope": "Grid-scale",
            # The words the Context Check returns for the corrected figure:
            # the page's own sentence, carrying the corrected scope.
            "context_check_evidence_words": (
                "The U.S. energy storage market hit a record 18.9 gigawatts "
                "of battery energy storage system installations in 2025, a "
                "52% increase over 2024, across all segments"
            ),
        },
        known_source_urls=[_WOODMAC_URL],
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "verification_recorded",
                0.25,
                "Every finding carries the verifier's judgement of it.",
            ),
            (
                "no_invented_evidence",
                0.25,
                "Every kept figure's evidence words appear on the page it cites.",
            ),
            (
                "drop_reasons_named",
                0.20,
                "Every dropped finding or figure names its drop reason.",
            ),
            (
                "expected_outcome",
                0.30,
                "The corrected finding matches the case's expected status, "
                "scope, period, kind and attribution.",
            ),
        ),
    ),
    judge_rubric=_SCOPE_RUBRIC,
    metadata={"scenario": "scope-correction"},
)


# --- 2. A relay credited to its originator ----------------------------------


_RELAY = build_case(
    case_id="relay-labelled-as-relay",
    agent_name="evidence_verifier",
    tier="controlled",
    title="Record a relayed figure under the organisation the page credits",
    purpose=(
        "Utility Dive's page states 16 GW 'according to Wood Mackenzie'. The "
        "figure survives — the page states it and the snippet is its own "
        "words — but the organisation is Wood Mackenzie, not the site that "
        "served the page, and the attribution is relayed."
    ),
    state=evaluation_state(
        case_id="relay-labelled-as-relay",
        question="What is the utility-scale battery storage installation figure for 2025?",
        sub_topics=(
            sub_topic(
                "Utility-scale storage installations",
                rationale="One figure, published by its originator and relayed.",
                queries=["utility-scale battery storage installations 2025"],
                criteria=["A utility-scale installation figure for 2025"],
                priority=1,
            ),
        ),
        reads=(_RELAY_READ,),
        findings=(
            finding(
                _RELAY_SNIPPET,
                url=_RELAY_URL,
                title=_RELAY_TITLE,
                sub_topic_title="Utility-scale storage installations",
                snippet=_RELAY_SNIPPET,
                read_id=_RELAY_READ.read_id,
                locator="body",
                figures=(figure("16", "GW", period="2025", kind="actual"),),
                data_period="2025",
            ),
        ),
    ),
    dependency_scenario="evidence-verifier-relay",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            "expected_outcomes": [
                {
                    "source_url": _RELAY_URL,
                    "status": "verified",
                    "period": "2025",
                    "kind": "actual",
                    "attribution": "relayed",
                    "organisation": "Wood Mackenzie",
                },
            ],
            "relay_host": "utilitydive.com",
            # The snippet's own words, which the page carries verbatim.
            "context_check_evidence_words": _RELAY_SNIPPET.rstrip("."),
        },
        known_source_urls=[_RELAY_URL],
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "verification_recorded",
                0.25,
                "Every finding carries the verifier's judgement of it.",
            ),
            (
                "no_invented_evidence",
                0.25,
                "Every kept figure's evidence words appear on the page it cites.",
            ),
            (
                "drop_reasons_named",
                0.20,
                "Every dropped finding or figure names its drop reason.",
            ),
            (
                "expected_outcome",
                0.30,
                "The relayed figure is recorded under the organisation the "
                "page credits, as a relay.",
            ),
        ),
    ),
    judge_rubric=_RELAY_RUBRIC,
    metadata={"scenario": "relay"},
)


# --- 3. Evidence words the page does not carry ------------------------------


_INVENTED = build_case(
    case_id="invented-evidence-words-rejected",
    agent_name="evidence_verifier",
    tier="controlled",
    title="Drop a figure whose evidence words are not on the page",
    purpose=(
        "The Context Check answers with evidence words the page does not "
        "carry — a plausible sentence that states the recorded figure — and "
        "asserts they confirm it. Code refuses the words rather than the "
        "claim: the figure is dropped with evidence_not_on_page and the "
        "finding with all_figures_dropped, so a figure no page states never "
        "reaches a report."
    ),
    state=evaluation_state(
        case_id="invented-evidence-words-rejected",
        question="How much battery storage capacity was installed in the United States in 2025?",
        sub_topics=(
            sub_topic(
                "U.S. battery storage installations",
                rationale="A page's figure is checked against the page itself.",
                queries=["us battery storage installations 2025"],
                criteria=["A market-wide installation figure for 2025"],
                priority=1,
            ),
        ),
        reads=(_INVENTED_READ,),
        findings=(
            finding(
                _WOODMAC_SNIPPET,
                url=_WOODMAC_URL,
                title=_WOODMAC_TITLE,
                sub_topic_title="U.S. battery storage installations",
                snippet=_WOODMAC_SNIPPET,
                read_id=_INVENTED_READ.read_id,
                locator="body",
                figures=(figure("18.9", "gigawatts", period="2025", kind="actual"),),
                data_period="2025",
            ),
        ),
    ),
    dependency_scenario="evidence-verifier-invented-evidence",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            "expected_outcomes": [
                {
                    "source_url": _WOODMAC_URL,
                    "status": "dropped",
                    "finding_drop_reason": "all_figures_dropped",
                    "figure_drop_reason": "evidence_not_on_page",
                },
            ],
            # The words the scripted Context Check answers with: nowhere on
            # the page, which is exactly what the case grades.
            "context_check_evidence_words": (
                "installations of 18.9 GW of grid-scale batteries in 2025"
            ),
        },
        known_source_urls=[_WOODMAC_URL],
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "verification_recorded",
                0.30,
                "Every finding carries the verifier's judgement of it.",
            ),
            (
                "no_invented_evidence",
                0.30,
                "Every kept figure's evidence words appear on the page it cites.",
            ),
            (
                "drop_reasons_named",
                0.20,
                "Every dropped finding or figure names its drop reason.",
            ),
            (
                "expected_outcome",
                0.20,
                "The unsupported figure is dropped as evidence_not_on_page.",
            ),
        ),
    ),
    judge_rubric=_REJECTION_RUBRIC,
    metadata={"scenario": "invented-evidence"},
)


# --- The live case: the benchmark's own pages -------------------------------


_LIVE = build_case(
    case_id="evidence-verifier-live-benchmark",
    agent_name="evidence_verifier",
    tier="live",
    title="Verify the benchmark's EIA and Wood Mackenzie figures, live",
    purpose=(
        "The live benchmark's three pages: the EIA page stating a 2024 "
        "actual and a 2025 forecast, the Wood Mackenzie release stating a "
        "market-wide figure, and a site relaying Wood Mackenzie's "
        "utility-scale figure. The live Context Check judges each figure "
        "against its own passage; the graded rubric is whether every kept "
        "figure's scope, period, kind and attribution are the page's, and "
        "whether anything unsupported is dropped with its reason."
    ),
    state=evaluation_state(
        case_id="evidence-verifier-live-benchmark",
        question="How much battery storage capacity was added in the United States in 2024, and what is forecast for 2025?",
        sub_topics=(
            sub_topic(
                "U.S. battery storage capacity additions",
                rationale="The EIA's own inventory states the 2024 actual.",
                queries=["us battery storage capacity additions 2024"],
                criteria=["An actual installation figure for 2024"],
                priority=1,
            ),
            sub_topic(
                "2025 battery storage forecasts",
                rationale="The forecast and the market-wide figure are two bases.",
                queries=["us battery storage forecast 2025"],
                criteria=["A forecast or market-wide figure for 2025"],
                priority=2,
            ),
        ),
        reads=(_LIVE_EIA_READ, _LIVE_WOODMAC_READ, _LIVE_RELAY_READ),
        findings=(
            finding(
                _EIA_ACTUAL_SNIPPET,
                url=_EIA_URL,
                title=_EIA_TITLE,
                sub_topic_title="U.S. battery storage capacity additions",
                snippet=_EIA_ACTUAL_SNIPPET,
                read_id=_LIVE_EIA_READ.read_id,
                locator="body",
                figures=(figure("10.4", "GW", period="2024", kind="actual"),),
                data_period="2024",
            ),
            finding(
                _EIA_FORECAST_SNIPPET,
                url=_EIA_URL,
                title=_EIA_TITLE,
                sub_topic_title="2025 battery storage forecasts",
                snippet=_EIA_FORECAST_SNIPPET,
                read_id=_LIVE_EIA_READ.read_id,
                locator="body",
                figures=(figure("19.6", "GW", period="2025", kind="forecast"),),
                data_period="2025",
            ),
            finding(
                _WOODMAC_SNIPPET,
                url=_WOODMAC_URL,
                title=_WOODMAC_TITLE,
                sub_topic_title="2025 battery storage forecasts",
                snippet=_WOODMAC_SNIPPET,
                read_id=_LIVE_WOODMAC_READ.read_id,
                locator="body",
                figures=(figure("18.9", "gigawatts", period="2025", kind="actual"),),
                data_period="2025",
            ),
            finding(
                _RELAY_SNIPPET,
                url=_RELAY_URL,
                title=_RELAY_TITLE,
                sub_topic_title="2025 battery storage forecasts",
                snippet=_RELAY_SNIPPET,
                read_id=_LIVE_RELAY_READ.read_id,
                locator="body",
                figures=(figure("16", "GW", period="2025", kind="actual"),),
                data_period="2025",
            ),
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            # Live verdicts are the model's; what the harness pins is that
            # nothing is invented and nothing unsupported is kept silently.
            "benchmark_pages": sorted(_LIVE_PAGES),
        },
        known_source_urls=sorted(_LIVE_PAGES),
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "verification_recorded",
                0.40,
                "Every finding carries the verifier's judgement of it.",
            ),
            (
                "no_invented_evidence",
                0.40,
                "Every kept figure's evidence words appear on the page it cites.",
            ),
            (
                "drop_reasons_named",
                0.20,
                "Every dropped finding or figure names its drop reason.",
            ),
        ),
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

# Append new cases; never prepend. ``conftest.controlled_case_for`` takes
# ``cases_for(agent, "controlled")[0]``, so the first case here is the one
# every conftest-driven gate test exercises.
CONTROLLED_CASES: tuple[EvaluationCase, ...] = (_SCOPE, _RELAY, _INVENTED)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
