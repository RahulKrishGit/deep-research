"""Synthesizer evaluation cases: citation, conflict, and write recovery."""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    claim,
    evaluation_state,
    finding,
    metrics,
    rubric,
    scored_source,
    sub_topic,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase
from deep_research.utils.types import MemorySnapshot

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others. The report-writing
# dimensions are shared as plain tuples — rubric() copies them into a fresh
# JudgeRubric per call — so the dimension text is written once, not
# verbatim in all four rubrics.

_REPORT_DIMENSIONS = (
    (
        "evidence_fidelity",
        "The report says only what the recorded evidence supports.",
        "Sections and claims stay within the findings, sources, and checked "
        "claims; nothing is invented.",
        "The report asserts material no finding, source, or claim supports.",
    ),
    (
        "limitations_honesty",
        "Weaknesses and gaps are disclosed in the limitations section.",
        "The report names its limitations plainly in a dedicated section.",
        "Weaknesses are hidden or presented as strengths.",
    ),
)

_CITATION_DIMENSIONS = (
    (
        "citation_faithfulness",
        "Every citation resolves to a source the evidence actually contains.",
        "Every cited URL is a known source and supports the sentence citing "
        "it.",
        "Citations are invented, mismatched, or point outside the evidence.",
    ),
    (
        "structure_quality",
        "The report follows the required skeleton in a readable order.",
        "Summary, findings, and limitations sections are present, distinct, "
        "and well ordered.",
        "Required sections are missing, merged, or misordered.",
    ),
)

_COMPLETE_RUBRIC = rubric(
    "synthesizer-complete-report",
    *_REPORT_DIMENSIONS,
    *_CITATION_DIMENSIONS,
)

_CONFLICT_RUBRIC = rubric(
    "synthesizer-conflict-limitations",
    *_REPORT_DIMENSIONS,
    (
        "uncertainty_representation",
        "Disagreement and uncertainty are stated, not smoothed away.",
        "Conflicting claims and caveat signals appear explicitly in the "
        "report.",
        "Disagreement is flattened into a single confident position.",
    ),
)

_FAILURE_RUBRIC = rubric(
    "synthesizer-write-failure",
    *_REPORT_DIMENSIONS,
    (
        "persistence_honesty",
        "Persistence claims match what was actually written and saved.",
        "The report stays silent about saving when the write failed, and "
        "the failure is recorded.",
        "The report claims the report was saved or stored when the write "
        "failed.",
    ),
)

_LIVE_RUBRIC = rubric(
    "synthesizer-live-report",
    *_REPORT_DIMENSIONS,
    *_CITATION_DIMENSIONS,
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
_RETROFIT_SDIRECT_URL = (
    "https://sciencedirect.com/retrofit-depth-realization-gap"
)
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

_COMPLETE = build_case(
    case_id="complete-cited-report",
    agent_name="synthesizer",
    tier="controlled",
    title="Write a cited report over six scored sources",
    purpose=(
        "Synthesize six findings across three subtopics — London, New "
        "York, and broader international evidence — into a cited report. "
        "Three claims are verified with high confidence and one is "
        "unverified, and one of the six sources is low confidence, so the "
        "report has a genuine limitations section to write. The scripted "
        "scenario lets both persistence tools succeed: the report is "
        "written to disk and the verified claims are saved to memory."
    ),
    state=evaluation_state(
        case_id="complete-cited-report",
        question=(
            "What is the evidence base for congestion pricing reducing "
            "urban travel times?"
        ),
        sub_topics=(
            sub_topic(
                "London congestion charging evidence",
                rationale=(
                    "TfL's monitoring is the longest-running congestion "
                    "pricing evidence base."
                ),
                queries=["london congestion charging travel time monitoring"],
                criteria=["Travel time or speed data from the charging zone"],
                priority=1,
            ),
            sub_topic(
                "New York congestion pricing results",
                rationale=(
                    "NYC's program is the newest large-scale test of "
                    "travel time effects."
                ),
                queries=["new york congestion pricing travel times"],
                criteria=["Central business district travel time outcomes"],
                priority=2,
            ),
            sub_topic(
                "Evidence beyond London and New York",
                rationale=(
                    "International and pilot evidence tests whether the "
                    "effect generalizes."
                ),
                queries=["congestion pricing travel time review"],
                criteria=["At least one non-New York study or pilot"],
                priority=3,
            ),
        ),
        findings=(
            finding(
                "Transport for London's monitoring shows average traffic "
                "speeds inside the central London congestion charging zone "
                "rose after the scheme began, with bus journey times across "
                "the zone falling during charging hours.",
                url=_TFL_URL,
                title="TfL: congestion charging travel time monitoring",
                sub_topic_title="London congestion charging evidence",
            ),
            finding(
                "New York City's congestion pricing evaluation found "
                "average travel times across the Manhattan central business "
                "district fell after tolling began, with cross-town trips "
                "improving most.",
                url=_NYC_URL,
                title="NYC: congestion pricing evaluation",
                sub_topic_title="New York congestion pricing results",
            ),
            finding(
                "A peer-reviewed review reports that congestion pricing "
                "reduced peak-period travel times in Stockholm and "
                "Singapore, with effects persisting beyond the first year "
                "of operation.",
                url=_SDIRECT_URL,
                title="ScienceDirect: congestion pricing review",
                sub_topic_title="Evidence beyond London and New York",
            ),
            finding(
                "OECD research on road pricing finds travel-time savings "
                "of 10 to 30 percent in cities with comprehensive "
                "congestion pricing, with larger savings when revenues "
                "fund transit alternatives.",
                url=_OECD_URL,
                title="OECD: road pricing and travel times",
                sub_topic_title="Evidence beyond London and New York",
            ),
            finding(
                "The World Bank's urban mobility review documents reduced "
                "in-vehicle travel times after congestion pricing in seven "
                "cities, with the largest gains on routes into city "
                "centers.",
                url=_WORLDBANK_URL,
                title="World Bank: urban mobility review",
                sub_topic_title="Evidence beyond London and New York",
            ),
            finding(
                "An industry research note reports mixed results from a "
                "mid-sized U.S. city's congestion pricing pilot: corridor "
                "travel times fell, but network-wide times were little "
                "changed.",
                url=_PILOT_URL,
                title="Transport Research: U.S. congestion pricing pilot",
                sub_topic_title="Evidence beyond London and New York",
            ),
        ),
        sources=(
            scored_source(
                _TFL_URL,
                title="TfL: congestion charging travel time monitoring",
                authority=0.95,
                recency=0.90,
                relevance=0.92,
                corroboration=0.85,
                overall=0.91,
                rationale=(
                    "The scheme operator's own monitoring is the primary "
                    "data source for London."
                ),
            ),
            scored_source(
                _NYC_URL,
                title="NYC: congestion pricing evaluation",
                authority=0.88,
                recency=0.90,
                relevance=0.88,
                corroboration=0.78,
                overall=0.88,
                rationale=(
                    "City evaluation of its own tolling program; recent "
                    "and directly relevant."
                ),
            ),
            scored_source(
                _SDIRECT_URL,
                title="ScienceDirect: congestion pricing review",
                authority=0.85,
                recency=0.82,
                relevance=0.88,
                corroboration=0.82,
                overall=0.86,
                rationale=(
                    "Peer-reviewed cross-city review with strong "
                    "methodology."
                ),
            ),
            scored_source(
                _OECD_URL,
                title="OECD: road pricing and travel times",
                authority=0.90,
                recency=0.80,
                relevance=0.82,
                corroboration=0.70,
                overall=0.83,
                rationale=(
                    "International policy research body; generalizes "
                    "across cities."
                ),
            ),
            scored_source(
                _WORLDBANK_URL,
                title="World Bank: urban mobility review",
                authority=0.88,
                recency=0.78,
                relevance=0.80,
                corroboration=0.65,
                overall=0.79,
                rationale=(
                    "Multilateral review of seven cities; broad but not "
                    "city-specific."
                ),
            ),
            scored_source(
                _PILOT_URL,
                title="Transport Research: U.S. congestion pricing pilot",
                authority=0.50,
                recency=0.70,
                relevance=0.70,
                corroboration=0.55,
                overall=0.62,
                rationale=(
                    "Trade research note on one pilot; no peer review, low "
                    "confidence."
                ),
                low_confidence=True,
            ),
        ),
        claims=(
            claim(
                "Congestion pricing reduced average travel times in the "
                "central London congestion charging zone during charging "
                "hours.",
                urls=[_TFL_URL, _OECD_URL],
                verdict="verified",
                confidence=0.85,
                evidence=[
                    "TfL monitoring shows average speeds rose after the "
                    "scheme began",
                    "OECD research reports travel-time savings of 10 to 30 "
                    "percent in priced cities",
                ],
            ),
            claim(
                "New York City's congestion pricing reduced average "
                "travel times in the Manhattan central business district.",
                urls=[_NYC_URL, _SDIRECT_URL],
                verdict="verified",
                confidence=0.80,
                evidence=[
                    "NYC evaluation found average CBD travel times fell "
                    "after tolling began",
                    "Peer-reviewed review reports persistent effects in "
                    "Stockholm and Singapore",
                ],
            ),
            claim(
                "Comprehensive congestion pricing is associated with "
                "travel-time savings of 10 to 30 percent across cities.",
                urls=[_OECD_URL, _WORLDBANK_URL],
                verdict="verified",
                confidence=0.76,
                evidence=[
                    "OECD road pricing research reports 10 to 30 percent "
                    "savings",
                    "World Bank review documents reduced in-vehicle times "
                    "in seven cities",
                ],
            ),
            claim(
                "A mid-sized U.S. city's congestion pricing pilot "
                "reduced travel times across its whole road network.",
                urls=[_PILOT_URL],
                verdict="unverified",
                confidence=0.55,
                evidence=[
                    "The pilot's corridor times fell while network-wide "
                    "times were little changed",
                ],
            ),
        ),
    ),
    dependency_scenario="synthesizer-complete",
    expectations=CaseExpectations(
        required_output_fields=["report"],
        reference={
            "known_citation_urls": list(_MIXED_URLS),
            "required_sections": ["summary", "findings", "limitations"],
            "minimum_cited_sources": 4,
        },
        known_source_urls=list(_MIXED_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "report_present",
                0.20,
                "The state update carries a non-empty `report`.",
            ),
            (
                "citations_known",
                0.30,
                "Every URL appearing in the report body is in "
                "`known_source_urls`.",
            ),
            (
                "coverage",
                0.25,
                "Every subtopic title appears in the report body.",
            ),
            (
                "limitations_present",
                0.15,
                "A limitations section exists.",
            ),
            (
                "persistence_truthful",
                0.10,
                "No persistence claim without a matching successful "
                "`write_document` call.",
            ),
        ),
    ),
    judge_rubric=_COMPLETE_RUBRIC,
    metadata={"scenario": "normal"},
)

_CONFLICT = build_case(
    case_id="conflict-and-limitations",
    agent_name="synthesizer",
    tier="controlled",
    title="Represent disagreement without overstating it",
    purpose=(
        "Five findings split two-against-three on whether return-to-office "
        "mandates change commercial vacancy, and the memory context "
        "advises stating disagreements explicitly. Two claims were "
        "contradicted by independent evidence and one has insufficient "
        "evidence, so an honest report says the evidence is mixed and "
        "never claims the question is settled. Both persistence tools "
        "succeed; the graded behavior is the report's wording, not its "
        "persistence."
    ),
    state=evaluation_state(
        case_id="conflict-and-limitations",
        question=(
            "Do remote-work mandates change commercial real-estate "
            "vacancy?"
        ),
        findings=(
            finding(
                "An NBER working paper reports that office vacancy rates "
                "rose several percentage points in metros where "
                "return-to-office mandates were widespread, controlling "
                "for leasing cycles.",
                url=_NBER_URL,
                title="NBER: return-to-office mandates and vacancy",
                sub_topic_title="Commercial real-estate vacancy trends",
            ),
            finding(
                "Urban Institute lease-data analysis finds office vacancy "
                "climbed most in cities with the strictest return-to-office "
                "mandates, with smaller effects in suburban submarkets.",
                url=_URBAN_URL,
                title="Urban Institute: office vacancy and mandates",
                sub_topic_title="Commercial real-estate vacancy trends",
            ),
            finding(
                "A commercial real-estate data tracker finds no "
                "statistically significant difference in vacancy-rate "
                "trends between mandate-heavy and mandate-light metros "
                "after 2023.",
                url=_TRACKER_URL,
                title="Commercial Edge: vacancy trend tracker",
                sub_topic_title="Commercial real-estate vacancy trends",
            ),
            finding(
                "Bloomberg analysis of office leasing finds vacancy "
                "changes after return-to-office mandates stayed within the "
                "normal cyclical range and differed little across cities.",
                url=_BLOOMBERG_URL,
                title="Bloomberg: office vacancy analysis",
                sub_topic_title="Commercial real-estate vacancy trends",
            ),
            finding(
                "Brookings research concludes remote work reduced overall "
                "office demand, but attributes most vacancy movement to "
                "hybrid schedules rather than explicit mandates.",
                url=_BROOKINGS_URL,
                title="Brookings: remote work and office demand",
                sub_topic_title="Commercial real-estate vacancy trends",
            ),
        ),
        claims=(
            claim(
                "Return-to-office mandates raised office vacancy rates in "
                "major U.S. metros.",
                urls=[_NBER_URL, _URBAN_URL],
                verdict="contradicted",
                confidence=0.60,
                evidence=[
                    "NBER working paper: vacancy rose in metros with "
                    "widespread mandates",
                    "Urban Institute: vacancy climbed most where mandates "
                    "were strictest",
                ],
                contradictions=[
                    "Commercial Edge tracker finds no significant "
                    "difference between mandate-heavy and mandate-light "
                    "metros",
                    "Bloomberg analysis finds vacancy changes within the "
                    "normal cyclical range",
                    "Brookings attributes most vacancy movement to hybrid "
                    "schedules, not explicit mandates",
                ],
            ),
            claim(
                "Remote-work mandates are the primary driver of downtown "
                "office vacancy.",
                urls=[_URBAN_URL, _NBER_URL],
                verdict="contradicted",
                confidence=0.55,
                evidence=[
                    "Urban Institute: downtown vacancy rose with mandate "
                    "strictness",
                ],
                contradictions=[
                    "Brookings: hybrid schedules, not mandates, explain "
                    "most vacancy movement",
                    "Commercial Edge tracker: no significant trend "
                    "difference after 2023",
                ],
            ),
            claim(
                "Hybrid work policies will keep commercial vacancy "
                "elevated through 2030.",
                urls=[_TRACKER_URL, _BROOKINGS_URL],
                verdict="insufficient_evidence",
                confidence=0.40,
            ),
        ),
        memory_context=MemorySnapshot(
            suggested_strategies=["state disagreements explicitly"],
        ),
    ),
    dependency_scenario="synthesizer-conflicted",
    expectations=CaseExpectations(
        required_output_fields=["report"],
        reference={
            "conflicting_claim_texts": [
                "Return-to-office mandates raised office vacancy rates in "
                "major U.S. metros.",
                "Remote-work mandates are the primary driver of downtown "
                "office vacancy.",
            ],
            "required_caveat_signals": [
                "conflict",
                "mixed",
                "uncertain",
                "limited",
            ],
            "forbidden_overstatement": ["proves", "conclusively", "definitively"],
        },
        known_source_urls=list(_CONFLICT_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "conflict_represented",
                0.35,
                "The report body mentions at least one contradicted claim "
                "or a caveat signal.",
            ),
            (
                "no_overstatement",
                0.25,
                "No forbidden overstatement word appears.",
            ),
            (
                "limitations_present",
                0.20,
                "A limitations section exists.",
            ),
            (
                "citations_known",
                0.20,
                "Every URL appearing in the report body is in "
                "`known_source_urls`.",
            ),
        ),
    ),
    judge_rubric=_CONFLICT_RUBRIC,
    metadata={"scenario": "challenging"},
)

_FAILURE = build_case(
    case_id="write-or-memory-failure",
    agent_name="synthesizer",
    tier="controlled",
    title="Keep the report honest when both persistence tools fail",
    purpose=(
        "Compose a full report over four findings, four sources, and "
        "three verified claims while both persistence tools fail when "
        "called: the document write hits a read-only filesystem and the "
        "memory save is unavailable. The report text must survive in the "
        "state update, both failures must be recorded as recoverable "
        "errors, and the report must not claim anything was saved, "
        "written, or stored."
    ),
    state=evaluation_state(
        case_id="write-or-memory-failure",
        question=(
            "How much does building retrofit depth affect realized energy "
            "savings?"
        ),
        findings=(
            finding(
                "Lawrence Berkeley National Laboratory analysis of "
                "building retrofits finds realized energy savings scale "
                "with retrofit depth, with deep retrofits achieving 40 to "
                "60 percent reductions.",
                url=_LBNL_URL,
                title="LBNL: deep retrofit savings analysis",
                sub_topic_title="Retrofit depth and realized savings",
            ),
            finding(
                "ACEEE field studies report that deep retrofit packages "
                "delivered larger average savings than shallow ones, "
                "though savings varied widely across buildings.",
                url=_ACEEE_URL,
                title="ACEEE: retrofit depth field studies",
                sub_topic_title="Retrofit depth and realized savings",
            ),
            finding(
                "A field study in temperate U.S. climates finds realized "
                "savings below modeled values for both shallow and deep "
                "retrofits, with the gap largest for deep packages.",
                url=_RETROFIT_SDIRECT_URL,
                title="ScienceDirect: retrofit savings realization",
                sub_topic_title="Retrofit depth and realized savings",
            ),
            finding(
                "Case-study reviews find some shallow retrofits matched "
                "deep-retrofit savings when occupant behavior was "
                "favorable, so depth alone does not determine realized "
                "savings.",
                url=_RETROFIT_REVIEW_URL,
                title="Retrofit Review: depth and occupant behavior",
                sub_topic_title="Retrofit depth and realized savings",
            ),
        ),
        sources=(
            scored_source(
                _LBNL_URL,
                title="LBNL: deep retrofit savings analysis",
                authority=0.90,
                recency=0.85,
                relevance=0.90,
                corroboration=0.80,
                overall=0.88,
                rationale=(
                    "National laboratory analysis with measured building "
                    "data."
                ),
            ),
            scored_source(
                _ACEEE_URL,
                title="ACEEE: retrofit depth field studies",
                authority=0.82,
                recency=0.80,
                relevance=0.85,
                corroboration=0.75,
                overall=0.82,
                rationale=(
                    "Field studies from an efficiency research "
                    "organization."
                ),
            ),
            scored_source(
                _RETROFIT_SDIRECT_URL,
                title="ScienceDirect: retrofit savings realization",
                authority=0.85,
                recency=0.83,
                relevance=0.85,
                corroboration=0.78,
                overall=0.84,
                rationale=(
                    "Peer-reviewed field study of the realization gap."
                ),
            ),
            scored_source(
                _RETROFIT_REVIEW_URL,
                title="Retrofit Review: depth and occupant behavior",
                authority=0.45,
                recency=0.70,
                relevance=0.72,
                corroboration=0.50,
                overall=0.60,
                rationale=(
                    "Case-study reviews without peer review; low "
                    "confidence."
                ),
                low_confidence=True,
            ),
        ),
        claims=(
            claim(
                "Deep building retrofits achieve substantially larger "
                "realized energy savings than shallow ones.",
                urls=[_LBNL_URL, _ACEEE_URL],
                verdict="verified",
                confidence=0.82,
                evidence=[
                    "LBNL: deep retrofits achieve 40 to 60 percent "
                    "reductions",
                    "ACEEE: deep packages delivered larger average "
                    "savings",
                ],
            ),
            claim(
                "Realized savings from deep retrofits are often below "
                "modeled values.",
                urls=[_RETROFIT_SDIRECT_URL, _LBNL_URL],
                verdict="verified",
                confidence=0.78,
                evidence=[
                    "ScienceDirect field study: realized savings below "
                    "modeled values, gap largest for deep packages",
                ],
            ),
            claim(
                "Occupant behavior can narrow the realized-savings gap "
                "between shallow and deep retrofits.",
                urls=[_RETROFIT_REVIEW_URL, _ACEEE_URL],
                verdict="verified",
                confidence=0.75,
                evidence=[
                    "Case-study reviews: shallow retrofits matched deep "
                    "savings when occupant behavior was favorable",
                ],
            ),
        ),
    ),
    dependency_scenario="synthesizer-write-failure",
    expectations=CaseExpectations(
        required_output_fields=["report"],
        reference={
            "expected_error_sources": ["write_document", "save_to_memory"],
            "forbidden_persistence_claims": [
                "saved to",
                "written to",
                "stored at",
            ],
        },
        known_source_urls=list(_FAILURE_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "report_present_in_state",
                0.35,
                "The report text survives in the state update even though "
                "the file write failed.",
            ),
            (
                "failure_recorded",
                0.30,
                "The run records the write and memory failures as "
                "recoverable errors.",
            ),
            (
                "no_false_persistence_claim",
                0.25,
                "No forbidden persistence phrase in the report body, and "
                "no `output_path` in the state update.",
            ),
            (
                "citations_known",
                0.10,
                "Every URL appearing in the report body is in "
                "`known_source_urls`.",
            ),
        ),
        must_record_recoverable_error=True,
    ),
    judge_rubric=_FAILURE_RUBRIC,
    metadata={"scenario": "failure-recovery"},
)

_LIVE = build_case(
    case_id="synthesizer-live-report",
    agent_name="synthesizer",
    tier="live",
    title="Write a cited report on heat-pump retrofit costs, live",
    purpose=(
        "Synthesize three current findings on heat-pump retrofit costs in "
        "temperate climates into the same report shape as the complete "
        "controlled case, and reuse its five metrics unchanged. The "
        "metrics stay meaningful because a live run is still a report "
        "over the given state: the citations come from the case's "
        "declared sources, so citations_known is gradable, and the "
        "subtopics make coverage gradable. Unlike every other agent's "
        "live case, this one performs real document writes and real "
        "memory saves."
    ),
    state=evaluation_state(
        case_id="synthesizer-live-report",
        question=(
            "What does recent evidence say about heat-pump retrofit costs "
            "in temperate climates?"
        ),
        sub_topics=(
            sub_topic(
                "Heat-pump retrofit cost benchmarks",
                rationale=(
                    "Stable cost benchmarks let a reader price a retrofit."
                ),
                queries=["heat pump retrofit installed cost"],
                criteria=["Installed cost data for temperate climates"],
                priority=1,
            ),
            sub_topic(
                "Incentives and payback periods",
                rationale=(
                    "Incentives are the main lever on effective cost."
                ),
                queries=["heat pump retrofit incentives payback"],
                criteria=["At least one incentive or payback figure"],
                priority=2,
            ),
            sub_topic(
                "Cost comparisons with incumbent systems",
                rationale=(
                    "Retrofit costs matter relative to furnace "
                    "replacement."
                ),
                queries=["heat pump versus furnace replacement cost"],
                criteria=["A comparison with an incumbent heating system"],
                priority=3,
            ),
        ),
        findings=(
            finding(
                "The U.S. Department of Energy reports that heat-pump "
                "retrofits in temperate climates now cost about the same "
                "as furnace replacements when installation incentives are "
                "counted, with operating costs below those of fossil-fuel "
                "systems.",
                url=_DOE_URL,
                title="DOE: heat pump systems",
                sub_topic_title="Cost comparisons with incumbent systems",
            ),
            finding(
                "NREL field studies of heat-pump retrofits in temperate "
                "U.S. climates find median installed costs between $8,000 "
                "and $12,000 for ducted systems, with payback periods "
                "under ten years in most markets.",
                url=_NREL_URL,
                title="NREL: heat pump retrofit costs",
                sub_topic_title="Heat-pump retrofit cost benchmarks",
            ),
            finding(
                "IEA analysis finds heat-pump retrofit costs in temperate "
                "markets have fallen over the past five years and that "
                "policy incentives shorten payback periods to seven years "
                "or less.",
                url=_IEA_URL,
                title="IEA: heat pumps in buildings",
                sub_topic_title="Incentives and payback periods",
            ),
        ),
        sources=(
            scored_source(
                _DOE_URL,
                title="DOE: heat pump systems",
                authority=0.90,
                recency=0.88,
                relevance=0.90,
                corroboration=0.82,
                overall=0.88,
                rationale=(
                    "Federal program page with current cost guidance."
                ),
            ),
            scored_source(
                _NREL_URL,
                title="NREL: heat pump retrofit costs",
                authority=0.92,
                recency=0.88,
                relevance=0.92,
                corroboration=0.84,
                overall=0.90,
                rationale=(
                    "National laboratory field studies of installed "
                    "costs."
                ),
            ),
            scored_source(
                _IEA_URL,
                title="IEA: heat pumps in buildings",
                authority=0.90,
                recency=0.86,
                relevance=0.88,
                corroboration=0.78,
                overall=0.86,
                rationale=(
                    "International agency analysis of costs and policy."
                ),
            ),
        ),
        claims=(
            claim(
                "Heat-pump retrofit costs in temperate climates are now "
                "comparable to furnace replacements when incentives are "
                "counted.",
                urls=[_DOE_URL, _NREL_URL],
                verdict="verified",
                confidence=0.85,
                evidence=[
                    "DOE: retrofit costs about the same as furnace "
                    "replacement with incentives",
                    "NREL: median installed costs of $8,000 to $12,000 for "
                    "ducted systems",
                ],
            ),
            claim(
                "Policy incentives shorten heat-pump retrofit payback "
                "periods to under ten years in temperate markets.",
                urls=[_IEA_URL, _NREL_URL],
                verdict="verified",
                confidence=0.78,
                evidence=[
                    "IEA: incentives shorten payback periods to seven "
                    "years or less",
                    "NREL: payback periods under ten years in most "
                    "markets",
                ],
            ),
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["report"],
        # Live-run note for Task 18's evaluator: the Synthesizer declares
        # only write_document and save_to_memory, so "documents" and
        # "memory" are both required live dependencies — this is the one
        # agent whose live case performs real document writes. No search
        # or HTTP tool exists, so neither "tavily" nor "http" belongs in
        # the list. The report cites the case's own declared sources, so
        # citations_known stays gradable and the case's URLs are pinned
        # up front.
        reference={
            "known_citation_urls": list(_LIVE_URLS),
            "required_sections": ["summary", "findings", "limitations"],
            "minimum_cited_sources": 3,
        },
        known_source_urls=list(_LIVE_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "report_present",
                0.20,
                "The state update carries a non-empty `report`.",
            ),
            (
                "citations_known",
                0.30,
                "Every URL appearing in the report body is in "
                "`known_source_urls`.",
            ),
            (
                "coverage",
                0.25,
                "Every subtopic title appears in the report body.",
            ),
            (
                "limitations_present",
                0.15,
                "A limitations section exists.",
            ),
            (
                "persistence_truthful",
                0.10,
                "No persistence claim without a matching successful "
                "`write_document` call.",
            ),
        ),
        required_live_dependencies=["documents", "memory"],
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

CONTROLLED_CASES: tuple[EvaluationCase, ...] = (_COMPLETE, _CONFLICT, _FAILURE)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
