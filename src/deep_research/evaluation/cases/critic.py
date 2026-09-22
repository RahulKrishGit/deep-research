"""Critic evaluation cases: routing, gaps, and budget discipline."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    CriticPacket,
    CritiqueDraft,
    build_critic_packet,
    build_critique,
)
from deep_research.agents.identity import claim_fingerprint
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
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    ClaimCluster,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ResearchState,
    SubTopic,
)

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others. The two core critique
# dimensions are shared as plain tuples — rubric() copies them into a fresh
# JudgeRubric per call — so the dimension text is written once, not
# verbatim in all four rubrics.

_CRITIQUE_CORE_DIMENSIONS = (
    (
        "score_groundedness",
        "The 1-10 score reflects the report's demonstrated strengths and "
        "weaknesses.",
        "The score tracks the report's evidence: strong sourcing and "
        "verified claims score high, thin or unsupported claims score low.",
        "The score contradicts the report's demonstrated quality or is "
        "unexplained.",
    ),
    (
        "gap_precision",
        "Listed gaps are real, material, and specific to this report.",
        "Gaps name concrete missing evidence a further pass could close.",
        "Gaps are vague, invented, or restate the question.",
    ),
)

_STRONG_RUBRIC = rubric(
    "critic-strong-report",
    *_CRITIQUE_CORE_DIMENSIONS,
    (
        "critique_actionability",
        "The critique tells the next research pass exactly what to do.",
        "Every continuation comes with specific gaps and concrete "
        "recommended queries.",
        "Continuation is ordered without saying what to fix or how to "
        "find it.",
    ),
    (
        "scoring_calibration",
        "The score calibrates to the report's evidence quality, not its "
        "prose.",
        "High scores rest on verified claims and strong sources; low "
        "scores rest on thin or unsupported evidence.",
        "Scores ignore the evidence and reward confident writing.",
    ),
)

_GAPPY_RUBRIC = rubric(
    "critic-gappy-report",
    *_CRITIQUE_CORE_DIMENSIONS,
    (
        "critique_actionability",
        "The critique tells the next research pass exactly what to do.",
        "Every continuation comes with specific gaps and concrete "
        "recommended queries.",
        "Continuation is ordered without saying what to fix or how to "
        "find it.",
    ),
)

_BUDGET_RUBRIC = rubric(
    "critic-budget-exhausted",
    *_CRITIQUE_CORE_DIMENSIONS,
    (
        "route_discipline",
        "The routing decision respects the iteration budget above all.",
        "The critique stops when no macro iteration remains, whatever the "
        "score.",
        "The critique orders another pass after the budget is exhausted.",
    ),
)

_LIVE_RUBRIC = rubric(
    "critic-live-review",
    *_CRITIQUE_CORE_DIMENSIONS,
    (
        "critique_actionability",
        "The critique tells the next research pass exactly what to do.",
        "Every continuation comes with specific gaps and concrete "
        "recommended queries.",
        "Continuation is ordered without saying what to fix or how to "
        "find it.",
    ),
    (
        "scoring_calibration",
        "The score calibrates to the report's evidence quality, not its "
        "prose.",
        "High scores rest on verified claims and strong sources; low "
        "scores rest on thin or unsupported evidence.",
        "Scores ignore the evidence and reward confident writing.",
    ),
)

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

_STRONG_REPORT = (
    "## Summary\n\nUrban tree canopy measurably lowers summer surface temperatures."
    "The five sources reviewed here — covering the mechanisms, the measured surface"
    "temperature reductions, and the canopy cover and structure effects that "
    "determine delivery — consistently find reductions of roughly 1 to 5 degrees "
    "Celsius beneath tree cover during hot periods, with the largest effects where "
    "canopy is dense, mature, and irrigated. Two mechanisms dominate: shading of "
    "sunlit surfaces and evaporative cooling from leaf transpiration. Shade "
    "dominates on hard, sunlit surfaces such as asphalt and concrete; transpiration"
    "contributes most where soil moisture is adequate and the canopy is closed. The"
    "practical implication is that canopy design — species, spacing, and water "
    "availability — determines how much of the potential cooling a city actually "
    "realizes.\n\n## Canopy cooling mechanisms\n\nEPA's heat-island program "
    "documents that trees and vegetation cool urban surfaces through shading and "
    "evapotranspiration mechanisms, reducing peak surface temperatures where canopy"
    "covers roads, roofs, and parking lots "
    "(https://epa.gov/heat-islands/trees-and-vegetation). NOAA's urban heat island "
    "research reaches the same conclusion and adds that surface temperatures "
    "respond most strongly to shade, while vegetation supplies evaporative cooling "
    "that bare shade structures cannot provide "
    "(https://noaa.gov/urban-heat-islands/tree-canopy).\n\n## Measured surface "
    "temperature reductions\n\nA peer-reviewed review of field measurements finds "
    "that urban tree canopy lowers summer surface temperatures by 1 to 5 degrees "
    "Celsius, with the largest reductions at midday and over impervious surfaces "
    "(https://sciencedirect.com/tree-canopy-surface-temperature-review). A City of "
    "Phoenix monitoring study measured average reductions of 2 to 4 degrees Celsius"
    "beneath mature canopy across five districts, and found the largest cooling in "
    "neighborhoods with more than 30 percent canopy cover "
    "(https://phoenix.gov/tree-canopy-surface-temperature-study). The review also "
    "notes that reductions are measured against unshaded reference surfaces of the "
    "same type, so the figures isolate the canopy's contribution rather than "
    "seasonal weather trends.\n\n## Canopy structure and delivery factors\n\nUS "
    "Forest Service research finds the cooling effect scales with canopy cover and "
    "leaf area: dense, mature canopies cool more than sparse young plantings, and "
    "irrigated deciduous species perform best in arid summers "
    "(https://fs.usda.gov/research/urban-tree-canopy-cooling). The species and "
    "irrigation factors behind those differences, together with spacing, mean the "
    "same planted acreage delivers very different temperature outcomes — a delivery"
    "constraint city programs must design for. Park and street trees show the same "
    "scaling, but street canopies deliver the largest per-tree cooling because they"
    "shade the hottest impervious surfaces.\n\n## Limitations\n\nThe limitations "
    "and replication status of the evidence are important context for the figures "
    "above. The measured reductions come mostly from North American cities in hot, "
    "dry climates, the studies use different measurement protocols and observation "
    "periods, and the geographic scope of the evidence has not yet been widened to "
    "humid or coastal climates. The Phoenix figures rest on a single monitoring "
    "program and are not yet independently replicated, and the review excludes "
    "studies without paired shaded and unshaded measurement sites. The headline "
    "figure that canopy reduced summer surface temperatures by an average of 3 "
    "degrees Celsius across all monitored districts is therefore presented as "
    "indicative rather than established."
)

_STRONG_THEMES = (
    "shading and evapotranspiration mechanisms",
    "measured surface temperature reductions",
    "canopy cover and structure effects",
    "species and irrigation factors",
    "limitations and replication status",
    "geographic scope of the evidence",
)

# The no_spurious_gaps metric ("the critique does not list gaps that the
# report demonstrably covers, judged by the reference themes") needs the
# report's themes declared as reference data: the metric reads
# ``case.expectations.reference`` and nothing else, so an undeclared list
# would leave it nothing to judge against (the Task 12 live-case gap —
# metrics reused without matching reference data made a gate unevaluable).
# The strong case therefore carries ``reference_themes`` alongside the
# brief's pinned ``expected_route`` and ``minimum_score``; the live case
# mirrors it. Both theme lists are derived from each report's own text,
# and the tests pin them on both sides (case file and test file).

_STRONG_REFERENCE = {
    "expected_route": "end",
    "minimum_score": 7,
    "reference_themes": list(_STRONG_THEMES),
}

_STRONG_CASE = build_case(
    case_id="approve-strong-report",
    agent_name="critic",
    tier="controlled",
    title="Approve a well-cited report on tree canopy cooling",
    purpose=(
        "Critique a complete, well-cited report on urban tree canopy and summer "
        "surface temperature: five sources, four claims verified at high "
        "confidence, and one unverified headline figure disclosed in a "
        "limitations section. Task 8's Critic runs no spot check: the state "
        "carries the exact candidate, and a strong critique routes to end on "
        "the packet's evidence alone."
    ),
    state=evaluation_state(
        case_id="approve-strong-report",
        question=(
            "What is the measured effect of urban tree canopy on summer "
            "surface temperature?"
        ),
        report=_STRONG_REPORT,
        findings=(
            finding(
                "EPA documents that trees and vegetation cool urban "
                "surfaces through shading and evapotranspiration "
                "mechanisms, reducing peak surface temperatures where "
                "canopy covers roads, roofs, and parking lots.",
                url=_EPA_CANOPY_URL,
                title="EPA: trees and vegetation for heat islands",
                sub_topic_title="Canopy cooling mechanisms",
            ),
            finding(
                "NOAA's urban heat island research finds surface "
                "temperatures respond most strongly to shade, while "
                "vegetation supplies evaporative cooling that bare shade "
                "structures cannot provide.",
                url=_NOAA_CANOPY_URL,
                title="NOAA: urban heat islands and tree canopy",
                sub_topic_title="Canopy cooling mechanisms",
            ),
            finding(
                "A peer-reviewed review finds urban tree canopy lowers "
                "summer surface temperatures by 1 to 5 degrees Celsius, "
                "with the largest reductions at midday and over "
                "impervious surfaces.",
                url=_CANOPY_REVIEW_URL,
                title="ScienceDirect: tree canopy surface temperature "
                "review",
                sub_topic_title="Measured surface temperature reductions",
            ),
            finding(
                "A Phoenix monitoring study measured average reductions "
                "of 2 to 4 degrees Celsius beneath mature canopy, with "
                "the largest cooling above 30 percent canopy cover.",
                url=_PHOENIX_CANOPY_URL,
                title="Phoenix: tree canopy surface temperature study",
                sub_topic_title="Measured surface temperature reductions",
            ),
            finding(
                "US Forest Service research finds the cooling effect "
                "scales with canopy cover and leaf area, with irrigated "
                "deciduous species performing best in arid summers.",
                url=_USFS_CANOPY_URL,
                title="USFS: urban tree canopy cooling research",
                sub_topic_title="Canopy structure and delivery factors",
            ),
        ),
        claims=(
            claim(
                "Urban tree canopy lowers summer surface temperatures "
                "through shading and evapotranspiration.",
                urls=(_EPA_CANOPY_URL, _NOAA_CANOPY_URL),
                verdict="verified",
                confidence=0.90,
                verification_urls=(_CANOPY_REVIEW_URL,),
            ),
            claim(
                "Peer-reviewed field measurements find urban tree canopy "
                "reduces summer surface temperatures by 1 to 5 degrees "
                "Celsius.",
                urls=(_CANOPY_REVIEW_URL, _USFS_CANOPY_URL),
                verdict="verified",
                confidence=0.85,
                verification_urls=(_EPA_CANOPY_URL,),
            ),
            claim(
                "The cooling effect of tree canopy grows with canopy "
                "cover and leaf area.",
                urls=(_USFS_CANOPY_URL, _CANOPY_REVIEW_URL),
                verdict="verified",
                confidence=0.82,
                verification_urls=(_NOAA_CANOPY_URL,),
            ),
            claim(
                "Neighborhoods with more than 30 percent canopy cover "
                "saw the largest measured temperature reductions in "
                "Phoenix.",
                urls=(_PHOENIX_CANOPY_URL,),
                verdict="verified",
                confidence=0.80,
                verification_urls=(_CANOPY_REVIEW_URL,),
            ),
            claim(
                "Urban tree canopy reduced summer surface temperatures "
                "by an average of 3 degrees Celsius across all monitored "
                "districts.",
                urls=(_PHOENIX_CANOPY_URL,),
                verdict="unverified",
                confidence=0.55,
                verification_urls=(_USFS_CANOPY_URL,),
            ),
        ),
        sources=(
            scored_source(
                _EPA_CANOPY_URL,
                title="EPA: trees and vegetation for heat islands",
                rationale=(
                    "EPA's official heat-island program page; authoritative and "
                    "directly on"
                    "mechanism."
                ),
                authority=0.95,
                recency=0.88,
                relevance=0.92,
                overall=0.89,
            ),
            scored_source(
                _NOAA_CANOPY_URL,
                title="NOAA: urban heat islands and tree canopy",
                rationale=(
                    "Federal research agency with peer-reviewed heat-island "
                    "findings."
                ),
                authority=0.90,
                recency=0.85,
                relevance=0.90,
                overall=0.86,
            ),
            scored_source(
                _CANOPY_REVIEW_URL,
                title="ScienceDirect: tree canopy surface temperature review",
                rationale=(
                    "Peer-reviewed measurement review; corroborates the "
                    "temperature"
                    "range."
                ),
                authority=0.88,
                recency=0.82,
                relevance=0.92,
                overall=0.87,
            ),
            scored_source(
                _USFS_CANOPY_URL,
                title="USFS: urban tree canopy cooling research",
                rationale=(
                    "Federal research service with field data on canopy structure "
                    "effects."
                ),
                authority=0.85,
                recency=0.80,
                relevance=0.88,
                overall=0.82,
            ),
            scored_source(
                _PHOENIX_CANOPY_URL,
                title="Phoenix: tree canopy surface temperature study",
                rationale="City monitoring program with district-level measurements.",
                authority=0.72,
                recency=0.86,
                relevance=0.90,
                overall=0.78,
            ),
        ),
        sub_topics=(
            sub_topic(
                "Canopy cooling mechanisms",
                rationale=(
                    "How shading and evapotranspiration cool urban "
                    "surfaces."
                ),
                queries=(
                    "urban tree canopy shading evapotranspiration cooling",
                ),
                criteria=(
                    "Mechanism descriptions from at least two sources.",
                ),
                priority=1,
            ),
            sub_topic(
                "Measured surface temperature reductions",
                rationale=(
                    "Field measurements of temperature change under "
                    "canopy."
                ),
                queries=(
                    "urban tree canopy measured surface temperature "
                    "reductions",
                ),
                criteria=(
                    "Measured temperature reductions with study "
                    "attribution.",
                ),
                priority=1,
            ),
            sub_topic(
                "Canopy structure and delivery factors",
                rationale=(
                    "How species, cover, and irrigation shape the "
                    "effect."
                ),
                queries=(
                    "tree canopy cover species irrigation cooling "
                    "effects",
                ),
                criteria=(
                    "Delivery factors tied to the measured effects.",
                ),
                priority=1,
            ),
        ),
        iteration=1,
        max_iterations=3,
    ),
    dependency_scenario="critic-strong-report",
    expectations=CaseExpectations(
        reference=_STRONG_REFERENCE,
        known_source_urls=_STRONG_URLS,
        max_iterations=5,
        # The Critic declares no tools, so its ceiling is zero: a run that
        # executed one would fail this budget gate rather than pass it.
        max_tool_calls=0,
        required_output_fields=["critique"],
        deterministic_metrics=metrics(
            (
                "score_bounded",
                0.2,
                "The score is an integer in 1-10.",
            ),
            (
                "route_consistent",
                0.35,
                "should_continue matches route_decision for the produced score and "
                "remaining budget.",
            ),
            (
                "rationale_present",
                0.2,
                "A non-blank rationale that names at least one concrete report "
                "feature.",
            ),
            (
                "no_spurious_gaps",
                0.25,
                "The critique does not list gaps that the report demonstrably "
                "covers,"
                "judged by the reference themes.",
            ),
        ),
    ),
    judge_rubric=_STRONG_RUBRIC,
)

_GAPPY_REPORT = (
    "## Summary\n\nThis report examines whether municipal composting mandates "
    "reduce landfill methane. The evidence located so far covers measured methane "
    "reductions in cities with organics diversion programs; participation rates and"
    "the methodology behind landfill methane estimates remain unexamined "
    "here.\n\n## Measured methane reductions\n\nEPA's Landfill Methane Outreach "
    "Program reports that diverting source-separated organics from landfills "
    "reduces methane generation, with the effect growing as diversion programs "
    "mature (https://epa.gov/lmop/organics-diversion-methane). An organics industry"
    "blog claims that municipal composting mandates cut landfill methane emissions "
    "by 30 percent within three years of adoption, citing utility estimates "
    "(https://compostingindustry.example.com/mandate-methane-claims). The 30 "
    "percent figure is not corroborated by any independent measurement located for "
    "this report, and the claim remains unverified."
)

# The gappy report's own claim sentence carries the participation-rate
# subtopic's scripted query (pinned identically in dependencies.py and in
# the tests), the same two-sided pinning the Researcher cases use.

_GAPPY_CASE = build_case(
    case_id="request-more-research",
    agent_name="critic",
    tier="controlled",
    title="Request more research on composting mandates and methane",
    purpose=(
        "Critique a report that covers only one of three planned subtopics on "
        "composting mandates and landfill methane: participation rates and "
        "measurement methodology are missing and one of two sources is low "
        "confidence. The Critic runs no spot check, so the gaps come from the "
        "packet's open targets and unresolved claims, and the critique routes "
        "to refine."
    ),
    state=evaluation_state(
        case_id="request-more-research",
        question=(
            "How effective are municipal composting mandates at reducing "
            "landfill methane?"
        ),
        report=_GAPPY_REPORT,
        findings=(
            finding(
                "EPA's Landfill Methane Outreach Program reports that "
                "diverting source-separated organics from landfills "
                "reduces methane generation, with the effect growing as "
                "diversion programs mature.",
                url=_LMOP_URL,
                title="EPA LMOP: organics diversion and methane",
                sub_topic_title=(
                    "Measured methane reductions from composting mandates"
                ),
            ),
            finding(
                "An organics industry blog claims municipal composting "
                "mandates cut landfill methane emissions by 30 percent "
                "within three years of adoption, citing utility "
                "estimates.",
                url=_COMPOST_BLOG_URL,
                title="Composting Industry: mandate methane claims",
                sub_topic_title=(
                    "Measured methane reductions from composting mandates"
                ),
            ),
        ),
        claims=(
            claim(
                "Municipal composting mandates reduce landfill methane "
                "emissions by 30 percent within three years of "
                "adoption.",
                urls=(_COMPOST_BLOG_URL,),
                verdict="unverified",
                confidence=0.55,
                verification_urls=(_LMOP_URL,),
            ),
        ),
        sources=(
            scored_source(
                _LMOP_URL,
                title="EPA LMOP: organics diversion and methane",
                rationale="EPA program page on landfill methane from organics.",
                authority=0.90,
                recency=0.80,
                relevance=0.85,
                overall=0.81,
            ),
            scored_source(
                _COMPOST_BLOG_URL,
                title="Composting Industry: mandate methane claims",
                rationale=(
                    "Industry blog; single-claim source without independent "
                    "corroboration."
                ),
                authority=0.35,
                recency=0.70,
                relevance=0.65,
                overall=0.49,
                low_confidence=True,
            ),
        ),
        sub_topics=(
            sub_topic(
                "Participation in municipal composting mandates",
                rationale=(
                    "Household participation determines how much "
                    "organics a mandate actually diverts."
                ),
                queries=(
                    "municipal composting mandates participation rates",
                ),
                criteria=(
                    "Participation rate estimates from at least two "
                    "jurisdictions with mandates.",
                ),
                priority=2,
            ),
            sub_topic(
                "Landfill methane measurement methodology",
                rationale=(
                    "Methane estimates rest on a methodology the report "
                    "never describes."
                ),
                queries=(
                    "landfill methane measurement methodology estimates",
                ),
                criteria=(
                    "A description of how landfill methane figures are "
                    "produced.",
                ),
                priority=2,
            ),
            sub_topic(
                "Measured methane reductions from composting mandates",
                rationale=(
                    "Quantified methane cuts are the report's only "
                    "covered subtopic."
                ),
                queries=(
                    "composting mandates landfill methane reduction "
                    "measurements",
                ),
                criteria=(
                    "Measured methane reductions with source "
                    "attribution.",
                ),
                priority=1,
            ),
        ),
        iteration=1,
        max_iterations=3,
    ),
    dependency_scenario="critic-gappy-report",
    expectations=CaseExpectations(
        reference={
            "expected_route": "refine",
            "known_gaps": [
                "participation rates",
                "methane measurement methodology",
            ],
            "minimum_recommended_queries": 1,
        },
        known_source_urls=_GAPPY_URLS,
        max_iterations=5,
        max_tool_calls=0,
        required_output_fields=["critique"],
        deterministic_metrics=metrics(
            (
                "route_consistent",
                0.35,
                "should_continue matches route_decision for the produced score and "
                "remaining budget.",
            ),
            (
                "gaps_actionable",
                0.3,
                "At least one recommended query is non-empty and is not a "
                "restatement"
                "of the original question.",
            ),
            (
                "gaps_identified",
                0.2,
                "At least one reference gap theme appears in gaps or "
                "recommended_queries.",
            ),
            (
                "score_bounded",
                0.15,
                "The score is an integer in 1-10.",
            ),
        ),
    ),
    judge_rubric=_GAPPY_RUBRIC,
)

_BUDGET_REPORT = (
    "## Summary\n\nThe report asks whether congestion pricing reduces particulate "
    "pollution. The evidence behind it is thin: a single low-confidence industry "
    "blog citing preliminary monitor readings from one city, with no verified "
    "claims and no independent corroboration.\n\n## Evidence\n\nThe only cited "
    "source is an industry blog reporting that particulate concentrations fell 12 "
    "percent in the first year of the city's congestion pricing program, based on "
    "preliminary monitor readings "
    "(https://transportationblog.example.com/congestion-pricing-particulates). No "
    "peer-reviewed study, government monitoring record, or verified claim supports "
    "the figure, and no limitations section accompanies it."
)

# This case runs the final allowed macro iteration (iteration ==
# max_iterations), so route_decision must stop it regardless of the
# produced score. The scripted memory query fails with the backend
# unavailable while the scripted search still returns one result, so the
# run records the recoverable failure and still has evidence to critique.

_BUDGET_CASE = build_case(
    case_id="missing-evidence-or-budget-exhausted",
    agent_name="critic",
    tier="controlled",
    title="Route to end with thin evidence and an exhausted budget",
    purpose=(
        "Critique a thin report on congestion pricing and particulate pollution "
        "at the final allowed macro iteration: no verified claims, one "
        "low-confidence source, and a budget that forbids another pass. The "
        "critique must stop regardless of its score and say why, and it must "
        "do so without a tool: Task 8 removed the Critic's spot-check loop, so "
        "the run's failure ledger is no longer the source of this case's "
        "evidence and the case no longer requires a recorded tool failure."
    ),
    state=evaluation_state(
        case_id="missing-evidence-or-budget-exhausted",
        question="Does congestion pricing reduce particulate pollution?",
        report=_BUDGET_REPORT,
        findings=(
            finding(
                "An industry blog reports particulate concentrations "
                "fell 12 percent in the first year of a congestion "
                "pricing program, based on preliminary monitor "
                "readings.",
                url=_PARTICULATE_BLOG_URL,
                title="Transportation Blog: congestion pricing "
                "particulates",
                sub_topic_title="Particulate pollution evidence",
            ),
        ),
        claims=(),
        sources=(
            scored_source(
                _PARTICULATE_BLOG_URL,
                title="Transportation Blog: congestion pricing particulates",
                rationale=(
                    "Industry blog with preliminary monitor readings and no "
                    "corroboration."
                ),
                authority=0.30,
                recency=0.65,
                relevance=0.70,
                overall=0.46,
                low_confidence=True,
            ),
        ),
        sub_topics=(
            sub_topic(
                "Particulate pollution evidence",
                rationale=(
                    "Measured particulate changes in priced zones."
                ),
                queries=(
                    "congestion pricing particulate pollution evidence",
                ),
                criteria=(
                    "Particulate measurements attributable to pricing.",
                ),
                priority=1,
            ),
        ),
        iteration=3,
        max_iterations=3,
    ),
    dependency_scenario="critic-budget-exhausted",
    expectations=CaseExpectations(
        reference={
            "expected_route": "end",
            "reason": "budget_exhausted",
            "maximum_score": 6,
        },
        known_source_urls=_BUDGET_URLS,
        max_iterations=5,
        max_tool_calls=0,
        required_output_fields=["critique"],
        deterministic_metrics=metrics(
            (
                "route_discipline",
                0.6,
                "should_continue is False because no macro iteration remains, "
                "regardless of score.",
            ),
            (
                "conservative_score",
                0.25,
                "The score does not exceed the reference maximum given absent "
                "verified"
                "claims.",
            ),
            (
                "score_bounded",
                0.15,
                "The score is an integer in 1-10.",
            ),
        ),
        # ``must_record_recoverable_error`` is deliberately absent. It was
        # satisfied by the scripted memory failure the old spot-check loop hit;
        # a tool-free Critic cannot reach that path, and keeping the expectation
        # would make this case unsatisfiable rather than strict.
        #
        # Its 0.20 weight went to ``route_discipline`` rather than to a
        # replacement metric. ``rationale_present`` was tried and removed: with
        # no ``reference_themes`` on this case and ``Critique.rationale``
        # guaranteed non-blank, it returned ``True`` for any input at all, so a
        # fifth of the case measured nothing. What this case can genuinely fail
        # is the score ceiling, and that is what ``conservative_score`` is for.
    ),
    judge_rubric=_BUDGET_RUBRIC,
)

_LIVE_REPORT = (
    "## Summary\n\nLow-carbon cement technologies have moved from pilot plants to "
    "commercial-scale deployment in several countries, and the evidence reviewed "
    "here covers their performance, their costs, and the standards they must meet. "
    "The consistent picture is that clinker substitution and alternative fuels "
    "deliver the near-term emissions reduction potential, while the durability and "
    "long-term performance data are still accumulating.\n\n## Performance at "
    "commercial scale\n\nThe Global Cement and Concrete Association's net-zero "
    "roadmap reports that low-carbon cement technologies — clinker substitution, "
    "alternative fuels, and carbon capture — are now demonstrated at commercial "
    "scale in multiple countries, with production lines operating in Europe, North "
    "America, and Asia (https://gccassociation.org/net-zero-roadmap). The roadmap "
    "records that these plants have run for several years, that output quality has "
    "been stable across production campaigns, and that the cements have been used "
    "in a range of structural applications. The roadmap is explicit that the sector"
    "treats these lines as the template for the next decade's capacity additions "
    "rather than as niche installations.\n\n## Cost and deployment trends\n\nIEA "
    "analysis of the cement industry finds that low-carbon cement production has "
    "expanded from pilot plants to first-of-a-kind commercial lines, and that the "
    "cost premiums and price trends have been falling as capacity grows "
    "(https://iea.org/energy-system/industry/cement). The IEA notes that premiums "
    "remain material in most markets, that policy support and carbon pricing are "
    "the main levers on further cost reductions, and that announced capacity is "
    "concentrated in a handful of countries.\n\n## Standards and durability\n\nA "
    "Nature review of cement decarbonization finds that low-carbon cements at scale"
    "meet compressive strength standards in most applications, with the main "
    "outstanding question being durability and long-term performance data under "
    "field exposure (https://nature.com/articles/cement-decarbonization-at-scale). "
    "Accelerated testing supports current standards compliance, but multi-decade "
    "field records do not yet exist for the newest formulations, and standards "
    "bodies are still updating specification guidance. The review notes that "
    "purchasers and specifiers still treat the newest formulations as "
    "accepted-for-use rather than preferred, which keeps demand concentrated in "
    "public projects.\n\n## Limitations\n\nThe evidence base is young: most "
    "commercial lines have operated for under a decade, the durability and "
    "long-term performance data are still accumulating, and the emissions reduction"
    "potential figures rely on assumptions about clinker substitution rates and "
    "alternative-fuel supply. Cost data come mostly from Europe and North America, "
    "and the durability and long-term performance data under field exposure remain "
    "the main uncertainty for purchasers."
)

_LIVE_THEMES = (
    "commercial-scale deployment",
    "clinker substitution and alternative fuels",
    "cost premiums and price trends",
    "compressive strength standards",
    "durability and long-term performance data",
    "emissions reduction potential",
)

# Live-run note for Task 18's evaluator: all four metrics are evaluable
# from live state. route_consistent calls agents.critic.route_decision
# with the produced critique and the case's iteration/max_iterations/
# report — all fixed here. no_spurious_gaps is judged against the
# reference themes below, which are derived from this fixed report: the
# live run critiques this text, it does not discover it. score_bounded
# and rationale_present need only the critique itself.

_LIVE_CASE = build_case(
    case_id="critic-live-review",
    agent_name="critic",
    tier="live",
    title="Live review of low-carbon cement evidence",
    purpose=(
        "Critique a fixed, current report on low-carbon cement performance at "
        "scale, reusing the strong controlled case's four metrics unchanged. "
        "The report and its themes are fixed in the case state, so "
        "no_spurious_gaps stays gradable against the declared reference themes. "
        "The Critic declares no tools, so this case needs no live dependency of "
        "its own: what it exercises live is the review request and the model's "
        "reading of the packet."
    ),
    state=evaluation_state(
        case_id="critic-live-review",
        question=(
            "What is the current evidence for low-carbon cement performance "
            "at scale?"
        ),
        report=_LIVE_REPORT,
        findings=(
            finding(
                "The GCCA net-zero roadmap reports low-carbon cement "
                "technologies are demonstrated at commercial scale in "
                "multiple countries, with stable output quality across "
                "production campaigns.",
                url=_GCCA_URL,
                title="GCCA: net-zero roadmap",
                sub_topic_title="Performance at commercial scale",
            ),
            finding(
                "IEA analysis finds low-carbon cement production has "
                "expanded from pilot plants to first-of-a-kind "
                "commercial lines, with cost premiums falling as "
                "capacity grows.",
                url=_IEA_CE_URL,
                title="IEA: cement industry energy analysis",
                sub_topic_title="Cost and deployment trends",
            ),
            finding(
                "A Nature review finds low-carbon cements at scale meet "
                "compressive strength standards in most applications, "
                "while long-term durability data are still "
                "accumulating.",
                url=_NATURE_CE_URL,
                title="Nature: cement decarbonization at scale",
                sub_topic_title="Standards and durability",
            ),
        ),
        claims=(
            claim(
                "Low-carbon cement technologies are demonstrated at "
                "commercial scale in multiple countries.",
                urls=(_GCCA_URL, _IEA_CE_URL),
                verdict="verified",
                confidence=0.85,
                verification_urls=(_NATURE_CE_URL,),
            ),
            claim(
                "Low-carbon cements meet compressive strength standards "
                "in most applications, while long-term durability data "
                "are still accumulating.",
                urls=(_NATURE_CE_URL, _GCCA_URL),
                verdict="verified",
                confidence=0.80,
                verification_urls=(_IEA_CE_URL,),
            ),
        ),
        sources=(
            scored_source(
                _GCCA_URL,
                title="GCCA: net-zero roadmap",
                rationale="Industry association roadmap with plant-level reporting.",
                authority=0.85,
                recency=0.90,
                relevance=0.90,
                overall=0.86,
            ),
            scored_source(
                _IEA_CE_URL,
                title="IEA: cement industry energy analysis",
                rationale="International agency analysis of cement sector deployment.",
                authority=0.92,
                recency=0.90,
                relevance=0.88,
                overall=0.88,
            ),
            scored_source(
                _NATURE_CE_URL,
                title="Nature: cement decarbonization at scale",
                rationale="Peer-reviewed review of low-carbon cement performance.",
                authority=0.90,
                recency=0.85,
                relevance=0.85,
                overall=0.85,
            ),
        ),
        sub_topics=(
            sub_topic(
                "Performance at commercial scale",
                rationale=(
                    "Operating low-carbon cement production lines."
                ),
                queries=(
                    "low-carbon cement commercial scale production lines",
                ),
                criteria=(
                    "Operating plants in multiple countries with "
                    "production records.",
                ),
                priority=1,
            ),
            sub_topic(
                "Cost and deployment trends",
                rationale=(
                    "Premiums, prices, and announced capacity."
                ),
                queries=(
                    "low-carbon cement cost premiums price trends",
                ),
                criteria=(
                    "Cost and capacity figures with sources.",
                ),
                priority=1,
            ),
            sub_topic(
                "Standards and durability",
                rationale=(
                    "Standards compliance and long-term performance."
                ),
                queries=(
                    "low-carbon cement compressive strength durability",
                ),
                criteria=(
                    "Standards compliance and long-term field data.",
                ),
                priority=1,
            ),
        ),
        iteration=1,
        max_iterations=3,
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        reference={
            "expected_route": "end",
            "minimum_score": 7,
            "reference_themes": list(_LIVE_THEMES),
        },
        known_source_urls=_LIVE_URLS,
        max_iterations=5,
        max_tool_calls=0,
        required_output_fields=["critique"],
        deterministic_metrics=metrics(
            (
                "score_bounded",
                0.2,
                "The score is an integer in 1-10.",
            ),
            (
                "route_consistent",
                0.35,
                "should_continue matches route_decision for the produced score and "
                "remaining budget.",
            ),
            (
                "rationale_present",
                0.2,
                "A non-blank rationale that names at least one concrete report "
                "feature.",
            ),
            (
                "no_spurious_gaps",
                0.25,
                "The critique does not list gaps that the report demonstrably "
                "covers,"
                "judged by the reference themes.",
            ),
        ),
        # No live service is reachable from this agent: the Critic declares no
        # tools, so a live run of this case reaches only the model provider.
        required_live_dependencies=(),
    ),
    judge_rubric=_LIVE_RUBRIC,
)

# Append new cases; never prepend. ``conftest.controlled_case_for``
# takes ``cases_for(agent, "controlled")[0]``, so the first case here
# is the one every conftest-driven gate test exercises.
CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _STRONG_CASE,
    _GAPPY_CASE,
    _BUDGET_CASE,
)

LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE_CASE,)


# --- Task 8: the paired calibration contract ---------------------------------
#
# Eight paired examples, each one scripted review of one fixed candidate,
# scored by the real local build (``agents.critic.build_critique``). The
# assertions built on these are bands and *orderings*, never a demanded exact
# score: what has to hold is that a strong answer is not pulled down, that a
# minor omission does not collapse a sound answer, and that confident prose
# cannot buy acceptance.
#
# Paid semantic calibration is Task 13. Nothing here calls a provider, and the
# rates the report measures say nothing about whether a review was *right*:
# the Critic agreeing with its own scripted review is not ground truth, and
# independent report review plus source checks remain required.

CALIBRATION_QUESTION = (
    "How far can clinker substitution cut cement process emissions, and what "
    "does it cost?"
)

# The three obligations the question itself names, and the ids every candidate
# shares, so a scripted gap means the same thing in every case. Each candidate
# answers a different subset of them, which is what makes the labels real.
CALIBRATION_TARGET_IDS = {
    "mechanism": "target-mechanism",
    "emissions": "target-emissions",
    "cost": "target-cost",
}
CALIBRATION_CLUSTER_IDS = {
    "mechanism": "cluster-calibration-mechanism",
    "emissions": "cluster-calibration-emissions",
    "cost": "cluster-calibration-cost",
    "deployment": "cluster-calibration-deployment",
}
CALIBRATION_EVIDENCE_IDS = {
    "mechanism": "evidence-calibration-mechanism",
    "emissions_nature": "evidence-calibration-emissions-nature",
    "emissions_roadmap": "evidence-calibration-emissions-roadmap",
    "cost": "evidence-calibration-cost",
}
#: The statement ids ``_fill_statement_map`` mints for this candidate shape:
#: one summary point first, then the findings section's points in order.
CALIBRATION_STATEMENT_IDS = {
    "mechanism": "S001",
    "emissions": "F001",
    "cost": "F002",
}

_CALIBRATION_MECHANISM_CLAIM = (
    "Clinker substitution cuts process emissions by replacing the clinker "
    "fraction of cement with supplementary materials."
)
_CALIBRATION_EMISSIONS_CLAIM = (
    "Substituting clinker removes about 0.4 tonnes of carbon dioxide per tonne "
    "of cement produced."
)
_CALIBRATION_COST_CLAIM = (
    "Clinker substitution carries a cost premium of about 15 percent per tonne "
    "in the markets that have adopted it."
)
_CALIBRATION_DEPLOYMENT_CLAIM = (
    "Commercial-scale deployment of low-carbon cement is accelerating across "
    "several markets."
)
_CALIBRATION_MECHANISM_EVIDENCE = (
    "Substituting clinker with calcined clay or slag displaces the clinker "
    "fraction and therefore the process emissions it carries."
)
_CALIBRATION_EMISSIONS_NATURE_EVIDENCE = (
    "Each tonne of clinker avoided removes roughly 0.4 tonnes of process "
    "carbon dioxide."
)
_CALIBRATION_EMISSIONS_ROADMAP_EVIDENCE = (
    "The roadmap's own accounting puts avoided process emissions at about 0.4 "
    "tonnes of carbon dioxide per tonne of clinker displaced."
)
_CALIBRATION_COST_EVIDENCE = (
    "Adopting markets report a cost premium of roughly 15 percent per tonne "
    "for blended cement against the ordinary product."
)

#: The two URLs behind the corroborated reduction, per candidate. One pair is
#: genuinely independent — a peer-reviewed review and an agency's own analysis —
#: and the other is two pages of one association's site: the same registrable
#: publisher, so ``publisher_identity`` resolves them together and
#: ``independent_domains`` refuses the second as corroboration.
_GCCA_SECOND_URL = "https://gcca.org/net-zero-roadmap-summary"
_GCCA_PAIR_URL = "https://gcca.org/net-zero-roadmap"
_INDEPENDENT_EMISSIONS_PAIR = ("emissions_review", "emissions_agency")
_FALSE_EMISSIONS_PAIR = ("emissions_roadmap", "emissions_summary")

_CALIBRATION_REPORT = (
    "# Research report: How far can clinker substitution cut cement process "
    "emissions, and what does it cost?\n\n"
    "## Summary\n\nClinker substitution lowers process emissions by displacing "
    "the clinker fraction of cement. [1]\n\n"
    "## Findings\n\nPeer-reviewed accounting puts the reduction at roughly 0.4 "
    "tonnes of carbon dioxide per tonne of clinker displaced. [2] Adopting "
    "markets report a cost premium of about 15 percent per tonne for blended "
    "cement. [3]\n"
)

#: The polished non-answer: long, confident, and carrying no figure a read
#: supports. Its length is the point — 300+ words of prose must not outscore a
#: short answer that actually answers.
_POLISHED_NON_ANSWER_REPORT = (
    "# Research report: How far can clinker substitution cut cement process "
    "emissions, and what does it cost?\n\n"
    "## Summary\n\nDecarbonising cement is one of the defining industrial "
    "challenges of the decade, and clinker substitution sits at the centre of "
    "every credible pathway. The technology is mature, the materials are "
    "widely available, and the direction of travel across the industry is "
    "unambiguous. What follows sets out the landscape, the forces shaping it, "
    "and the considerations that will determine how far substitution can go.\n"
    "\n## A technology whose time has come\n\nClinker is the emissions-"
    "intensive component of cement, and reducing it has long been recognised "
    "as the most direct lever available to producers. Substitution "
    "technologies have advanced considerably, standards bodies have adapted "
    "their specifications, and producers across many markets have gained "
    "operational experience with blended cements. The momentum is real and it "
    "is accelerating.\n\n## The economics are moving in the right direction"
    "\n\nCost has historically been the principal obstacle, but the picture "
    "is changing. Scale effects, policy support, and carbon pricing all push "
    "in the same direction, and the premium for lower-carbon cement is "
    "narrowing in the markets that have moved first. Producers who move early "
    "position themselves well for the transition that is clearly coming.\n\n"
    "## What to watch\n\nThe pace of change will depend on policy design, on "
    "the availability of suitable supplementary materials, and on the "
    "willingness of purchasers to specify blended cements in structural "
    "applications. Each of these is a live question, and each will shape the "
    "outcome. The overall direction, however, is not in doubt. Producers who "
    "move early will be best placed to capture the opportunities that "
    "follow, and those who wait will find the transition harder and more "
    "expensive when it arrives. The industry knows this, the regulators know "
    "this, and the market is beginning to price it.\n\n"
    "## Limitations\n\nThis analysis draws on industry sources whose figures "
    "are not independently replicated.\n"
)

#: The honest-but-incomplete candidate's report: it says outright what it could
#: not establish, which is better than hiding it and is still not an answer.
_HONEST_INCOMPLETE_REPORT = (
    "# Research report: How far can clinker substitution cut cement process "
    "emissions, and what does it cost?\n\n"
    "## Summary\n\nClinker substitution lowers process emissions by displacing "
    "the clinker fraction of cement, and that much is established. [1] The "
    "question's second half is not answered here: no measured substitution "
    "share and no cost figure were established from the reads this pass "
    "acquired.\n\n"
    "## Findings\n\nSubstituting clinker with calcined clay or slag displaces "
    "the clinker fraction and therefore the process emissions it carries. [1] "
    "The report states plainly that the emissions-reduction share and the cost "
    "premium remain unquantified rather than estimating them.\n"
)

CALIBRATION_BASIS = (
    "These scores are the local build's reading of scripted reviews of fixed "
    "candidates. A model agreeing with its own scripted review is not ground "
    "truth: independent report review and source checks remain required, and "
    "the rates below measure the contract's behaviour, not whether any "
    "individual judgement was right."
)


@dataclass(frozen=True, slots=True)
class _Reading:
    """One registered read excerpt a candidate may cite."""

    evidence_id: str
    target_key: str
    url: str
    title: str
    locator: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    """One claim a candidate asserts, and how it was judged."""

    key: str
    text: str
    target_key: str
    atom: AtomicProposition
    urls: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    verdict: str
    badge: str


_MECHANISM_SPEC = _ClaimSpec(
    key="mechanism",
    text=_CALIBRATION_MECHANISM_CLAIM,
    target_key="mechanism",
    atom=AtomicProposition(
        text=_CALIBRATION_MECHANISM_CLAIM,
        subject="clinker substitution",
        predicate="states_mechanism",
    ),
    urls=(_IEA_CE_URL,),
    evidence_ids=(CALIBRATION_EVIDENCE_IDS["mechanism"],),
    verdict="insufficient_evidence",
    badge="source_supported",
)
_EMISSIONS_SPEC = _ClaimSpec(
    key="emissions",
    text=_CALIBRATION_EMISSIONS_CLAIM,
    target_key="emissions",
    atom=AtomicProposition(
        text=_CALIBRATION_EMISSIONS_CLAIM,
        subject="clinker substitution",
        predicate="states_value",
        value="0.4",
        unit="tCO2/t",
    ),
    urls=(_NATURE_CE_URL, _IEA_CE_URL),
    evidence_ids=(
        CALIBRATION_EVIDENCE_IDS["emissions_nature"],
        "evidence-calibration-emissions-agency",
    ),
    verdict="verified",
    badge="verified_pair",
)
_COST_SPEC = _ClaimSpec(
    key="cost",
    text=_CALIBRATION_COST_CLAIM,
    target_key="cost",
    atom=AtomicProposition(
        text=_CALIBRATION_COST_CLAIM,
        subject="clinker substitution",
        predicate="states_value",
        value="15",
        unit="percent premium",
    ),
    urls=(_IEA_CE_URL,),
    evidence_ids=(CALIBRATION_EVIDENCE_IDS["cost"],),
    verdict="insufficient_evidence",
    badge="source_supported",
)

_READINGS: dict[str, _Reading] = {
    "mechanism": _Reading(
        evidence_id=CALIBRATION_EVIDENCE_IDS["mechanism"],
        target_key="mechanism",
        url=_IEA_CE_URL,
        title="IEA: cement industry energy analysis",
        locator="section-mechanism",
        excerpt=_CALIBRATION_MECHANISM_EVIDENCE,
    ),
    "emissions_review": _Reading(
        evidence_id=CALIBRATION_EVIDENCE_IDS["emissions_nature"],
        target_key="emissions",
        url=_NATURE_CE_URL,
        title="Nature: cement decarbonization at scale",
        locator="section-emissions",
        excerpt=_CALIBRATION_EMISSIONS_NATURE_EVIDENCE,
    ),
    "emissions_agency": _Reading(
        evidence_id="evidence-calibration-emissions-agency",
        target_key="emissions",
        url=_IEA_CE_URL,
        title="IEA: cement industry energy analysis",
        locator="section-emissions-figures",
        excerpt=(
            "Agency analysis of cement process emissions repeats the 0.4 "
            "tonnes of carbon dioxide per tonne of clinker avoided."
        ),
    ),
    "emissions_roadmap": _Reading(
        evidence_id=CALIBRATION_EVIDENCE_IDS["emissions_roadmap"],
        target_key="emissions",
        url=_GCCA_PAIR_URL,
        title="GCCA: net-zero roadmap",
        locator="section-emissions-accounting",
        excerpt=_CALIBRATION_EMISSIONS_ROADMAP_EVIDENCE,
    ),
    "emissions_summary": _Reading(
        evidence_id="evidence-calibration-emissions-summary",
        target_key="emissions",
        url=_GCCA_SECOND_URL,
        title="GCCA: net-zero roadmap summary",
        locator="section-emissions-summary",
        excerpt=(
            "The same association's summary of that roadmap repeats the 0.4 "
            "tonnes of process carbon dioxide per tonne of clinker displaced."
        ),
    ),
    "cost": _Reading(
        evidence_id=CALIBRATION_EVIDENCE_IDS["cost"],
        target_key="cost",
        url=_IEA_CE_URL,
        title="IEA: cement industry energy analysis",
        locator="section-cost",
        excerpt=_CALIBRATION_COST_EVIDENCE,
    ),
}

#: The two readings behind the reduction, chosen per candidate: an independent
#: pair (a peer-reviewed review and an agency's own analysis) or one
#: association's roadmap with its own summary, which is the false pair. Both
#: tuples are declared with the URLs near the top of this section.


def _calibration_sub_topic() -> SubTopic:
    """The candidate's plan: the three obligations the question names."""
    return SubTopic(
        coverage_id="topic-calibration",
        title="Clinker substitution",
        rationale="The question turns on the emissions reduction and its cost.",
        search_queries=["clinker substitution emissions reduction and cost"],
        success_criteria=["A measured reduction and a cost figure, sourced."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id=CALIBRATION_TARGET_IDS["mechanism"],
                coverage_id="topic-calibration",
                question="By what mechanism does substitution cut emissions?",
                required_dimensions=["mechanism"],
                required=True,
                critical=True,
                support_policy="primary_attribution",
            ),
            EvidenceTarget(
                target_id=CALIBRATION_TARGET_IDS["emissions"],
                coverage_id="topic-calibration",
                question="How much carbon dioxide does substitution remove?",
                required_dimensions=["scale"],
                required=True,
                critical=True,
                support_policy="independent_pair",
            ),
            EvidenceTarget(
                target_id=CALIBRATION_TARGET_IDS["cost"],
                coverage_id="topic-calibration",
                question="What does substitution cost?",
                required_dimensions=["scale"],
                required=True,
                critical=True,
                support_policy="primary_attribution",
            ),
        ],
    )


def _cluster_for(spec: _ClaimSpec, claim_id: str) -> ClaimCluster:
    return ClaimCluster(
        cluster_id=CALIBRATION_CLUSTER_IDS[spec.key],
        proposition=spec.atom,
        evidence_ids=list(spec.evidence_ids),
        member_claim_ids=[claim_id],
        target_ids=[CALIBRATION_TARGET_IDS[spec.target_key]],
        source_urls=list(spec.urls),
        verdicts=[spec.verdict],
        verdict_evidence_status={spec.verdict: spec.badge},
    )


def _calibration_candidate(
    *,
    case_id: str,
    report: str,
    answered: Sequence[str],
    readings: Sequence[str],
    unsupported: Sequence[str] = (),
    attributed_emissions: bool = False,
) -> ResearchState:
    """Build one candidate: a report, its plan, and the records behind it.

    ``answered`` names the claim keys the composition asserts as claim-linked
    points; everything else stays open. ``readings`` names the registered read
    excerpts, so a candidate can be *given* a corroborated figure, a single
    attributed one, or a false pair. ``unsupported`` adds reader points that no
    claim and no read stands behind — the packet renders them with no evidence
    ids at all, which is the honest shape of an assertion the evidence does not
    carry. ``attributed_emissions`` drops the reduction's second reading and its
    corroboration badge, which is what a correctly attributed primary figure
    looks like: one publisher's own account, labelled as that.

    Every candidate is a real ``ResearchState`` built through the production
    composition, so a scripted review is graded against the candidate its label
    describes rather than against one shared fixture.
    """
    wanted = set(readings)
    emissions_pair = [
        name
        for name in readings
        if name in _INDEPENDENT_EMISSIONS_PAIR + _FALSE_EMISSIONS_PAIR
    ]
    emissions_spec = replace(
        _EMISSIONS_SPEC,
        urls=tuple(_READINGS[name].url for name in emissions_pair),
        evidence_ids=tuple(_READINGS[name].evidence_id for name in emissions_pair),
    )
    if attributed_emissions:
        emissions_spec = replace(
            emissions_spec,
            verdict="insufficient_evidence",
            badge="source_supported",
        )
    specs = {
        "mechanism": _MECHANISM_SPEC,
        "emissions": emissions_spec,
        "cost": _COST_SPEC,
    }
    claims_out: list[Claim] = []
    clusters: dict[str, ClaimCluster] = {}
    for key in answered:
        spec = specs[key]
        # Built directly rather than through the ``claim`` fixture helper: that
        # helper refuses a claim whose verification passage cites one of its own
        # publishers, and the false-independent-pair candidate *is* that shape.
        # The guard protects a fixture from accidentally claiming corroboration;
        # here the false pair is the candidate under test, so it is deliberate
        # and the fixture-integrity test asserts it really is a false pair.
        cited_readings = [
            reading
            for name, reading in _READINGS.items()
            if name in wanted and reading.evidence_id in spec.evidence_ids
        ]
        passages = [
            EvidencePassage(
                source_url=reading.url,
                source_title=reading.title,
                locator=f"support-{index + 1}",
                excerpt=reading.excerpt,
                stance="supports",
            )
            for index, reading in enumerate(cited_readings)
        ]
        built = Claim(
            claim_id=claim_fingerprint(spec.text),
            text=spec.text,
            source_urls=list(spec.urls),
            verdict=spec.verdict,
            confidence=0.85 if spec.verdict == "verified" else 0.6,
            evidence=[passage.excerpt for passage in passages]
            or ["Case fixture evidence."],
            contradictions=[],
            verification_evidence=passages,
            evidence_status=spec.badge,
        )
        claims_out.append(built)
        clusters[CALIBRATION_CLUSTER_IDS[key]] = _cluster_for(spec, built.claim_id)

    units = {
        _READINGS[name].evidence_id: EvidenceUnit(
            evidence_id=_READINGS[name].evidence_id,
            read_id=f"read-{_READINGS[name].evidence_id}",
            source_url=_READINGS[name].url,
            source_title=_READINGS[name].title,
            locator=_READINGS[name].locator,
            excerpt=_READINGS[name].excerpt,
            target_ids=[CALIBRATION_TARGET_IDS[_READINGS[name].target_key]],
            origin="researcher",
        )
        for name in readings
    }
    claims_by_key = {key: claim_row for key, claim_row in zip(answered, claims_out)}
    summary: list[ReportPoint] = []
    findings: list[ReportPoint] = []
    for index, key in enumerate(answered):
        spec = specs[key]
        row = claims_by_key[key]
        point = ReportPoint(
            text=spec.text,
            claim_ids=[row.claim_id],
            source_urls=list(spec.urls),
        )
        (summary if index == 0 else findings).append(point)
    findings.extend(
        ReportPoint(text=text, claim_ids=[], source_urls=[])
        for text in unsupported
    )
    composition = ReportComposition(
        question=CALIBRATION_QUESTION,
        session_id=f"evaluation-{case_id}",
        sub_topics=[_calibration_sub_topic()],
        claims=claims_out,
        sources=[
            scored_source(
                _GCCA_URL,
                title="GCCA: net-zero roadmap",
                rationale="Industry association roadmap with plant-level reporting.",
                authority=0.85,
                recency=0.90,
                relevance=0.90,
                overall=0.86,
            ),
            scored_source(
                _IEA_CE_URL,
                title="IEA: cement industry energy analysis",
                rationale="International agency analysis of the cement sector.",
                authority=0.92,
                recency=0.90,
                relevance=0.88,
                overall=0.88,
            ),
            scored_source(
                _NATURE_CE_URL,
                title="Nature: cement decarbonization at scale",
                rationale="Peer-reviewed review of low-carbon cement performance.",
                authority=0.90,
                recency=0.85,
                relevance=0.85,
                overall=0.85,
            ),
        ],
        claim_clusters=clusters,
        evidence_units=units,
        summary=summary,
        sections=[ReportSection(title="Findings", points=findings)],
    )
    return ResearchState(
        session_id=f"evaluation-{case_id}",
        original_question=CALIBRATION_QUESTION,
        report=report,
        composition=composition,
        sub_topics=[_calibration_sub_topic()],
        verified_claims=list(composition.claims),
        evaluated_sources=list(composition.sources),
    )


def _strong_calibration_state() -> ResearchState:
    """Answers the mechanism, the measured reduction, and the cost."""
    return _calibration_candidate(
        case_id="calibration-strong-answer",
        report=_CALIBRATION_REPORT,
        answered=("mechanism", "emissions", "cost"),
        readings=("mechanism", *_INDEPENDENT_EMISSIONS_PAIR, "cost"),
    )


def _missing_cost_calibration_state() -> ResearchState:
    """The same candidate with the cost obligation answered nowhere."""
    return _calibration_candidate(
        case_id="calibration-missing-critical-topic",
        report=_CALIBRATION_REPORT.replace(
            " Adopting markets report a cost premium of about 15 percent per "
            "tonne for blended cement. [3]",
            "",
        ),
        answered=("mechanism", "emissions"),
        readings=("mechanism", *_INDEPENDENT_EMISSIONS_PAIR),
    )


def _unsupported_assertion_calibration_state() -> ResearchState:
    """Asserts accelerating deployment that no claim and no read carries."""
    return _calibration_candidate(
        case_id="calibration-unsupported-central-assertion",
        report=_CALIBRATION_REPORT.replace(
            " Adopting markets report a cost premium of about 15 percent per "
            "tonne for blended cement. [3]",
            "",
        ).replace(
            "## Findings\n\n",
            "## Findings\n\nCommercial-scale deployment of low-carbon cement is "
            "accelerating across several markets.\n\n",
        ),
        answered=("mechanism", "emissions"),
        readings=("mechanism", *_INDEPENDENT_EMISSIONS_PAIR),
        unsupported=(_CALIBRATION_DEPLOYMENT_CLAIM,),
    )


def _attributed_primary_calibration_state() -> ResearchState:
    """Every figure attributed to the body that states it; none corroborated."""
    return _calibration_candidate(
        case_id="calibration-attributed-primary-fact",
        report=_CALIBRATION_REPORT,
        answered=("mechanism", "emissions", "cost"),
        readings=("mechanism", "emissions_agency", "cost"),
        attributed_emissions=True,
    )


def _false_pair_calibration_state() -> ResearchState:
    """Two reads behind the figure that resolve to one publisher's identity."""
    return _calibration_candidate(
        case_id="calibration-false-independent-pair",
        report=_CALIBRATION_REPORT,
        answered=("mechanism", "emissions", "cost"),
        readings=("mechanism", *_FALSE_EMISSIONS_PAIR, "cost"),
    )


def _polished_non_answer_calibration_state() -> ResearchState:
    """A long, confident report whose records answer nothing."""
    return _calibration_candidate(
        case_id="calibration-polished-verbose-non-answer",
        report=_POLISHED_NON_ANSWER_REPORT,
        answered=(),
        readings=(),
        unsupported=(
            "Decarbonising cement is one of the defining industrial challenges "
            "of the decade, and clinker substitution sits at its centre.",
            "The premium for lower-carbon cement is narrowing in the markets "
            "that have moved first.",
        ),
    )


def _honest_incomplete_calibration_state() -> ResearchState:
    """A report that says outright which obligations it could not answer."""
    return _calibration_candidate(
        case_id="calibration-honest-but-incomplete-answer",
        report=_HONEST_INCOMPLETE_REPORT,
        answered=("mechanism",),
        readings=("mechanism",),
    )


def calibration_packet(case_id: str) -> CriticPacket:
    """The packet one calibration case's candidate is reviewed from.

    Built from that case's own candidate — report, plan, statements, and read
    records together — so the ids a scripted gap names are ids that candidate
    really has, and the packet's open targets are the ones its label claims.
    Nothing here calls a provider.
    """
    case = calibration_case(case_id)
    state = case.state_factory()
    return build_critic_packet(state, state.composition)


@dataclass(frozen=True, slots=True)
class CriticCalibrationCase:
    """One paired calibration example: a candidate and its scripted review.

    ``score_band`` is the range the local build's reading has to land in, not
    a demanded value — the pair calibrates direction, and an exact score
    requirement would turn a calibration contract into a golden file.
    ``expects_acceptance`` and ``expects_a_defect`` are the two independent
    facts about the candidate: whether the answer may be accepted, and whether
    it has a defect the review has to surface. They differ, deliberately: the
    one-minor-gap case has a defect and is still acceptable.
    """

    case_id: str
    title: str
    purpose: str
    score_band: tuple[int, int]
    expects_acceptance: bool
    expects_a_defect: bool
    draft: dict[str, object]
    state_factory: Callable[[], ResearchState]


def _gap(
    *,
    problem: str,
    target_ids: Sequence[str] = (),
    statement_ids: Sequence[str] = (),
    claim_cluster_ids: Sequence[str] = (),
    kind: str = "coverage",
    severity: str = "major",
    repair_action: str = "acquire",
    queries: Sequence[str] = (),
) -> dict[str, object]:
    """One scripted gap, in the exact shape ``CritiqueGapDraft`` accepts."""
    return {
        "target_ids": list(target_ids),
        "statement_ids": list(statement_ids),
        "claim_cluster_ids": list(claim_cluster_ids),
        "kind": kind,
        "severity": severity,
        "repair_action": repair_action,
        "problem": problem,
        "recommended_queries": list(queries),
    }


def _review(
    *,
    score: int,
    rationale: str,
    gaps: Sequence[dict[str, object]] = (),
    unsupported: Sequence[str] = (),
    queries: Sequence[str] = (),
) -> dict[str, object]:
    """One scripted ``CritiqueDraft`` payload."""
    return {
        "score": score,
        "gaps": list(gaps),
        "unsupported_claims": list(unsupported),
        "recommended_queries": list(queries),
        "rationale": rationale,
    }


_MECHANISM_TARGET = CALIBRATION_TARGET_IDS["mechanism"]
_EMISSIONS_TARGET = CALIBRATION_TARGET_IDS["emissions"]
_COST_TARGET = CALIBRATION_TARGET_IDS["cost"]
_EMISSIONS_CLUSTER = CALIBRATION_CLUSTER_IDS["emissions"]
_MECHANISM_STATEMENT = CALIBRATION_STATEMENT_IDS["mechanism"]

CALIBRATION_CASES: tuple[CriticCalibrationCase, ...] = (
    CriticCalibrationCase(
        case_id="calibration-strong-answer",
        title="A complete, well-cited answer",
        purpose=(
            "All three obligations the question names — the mechanism, the "
            "measured reduction, and the cost — are answered from named "
            "sources, and the reduction carries independent corroboration."
        ),
        score_band=(8, 10),
        expects_acceptance=True,
        expects_a_defect=False,
        draft=_review(
            score=9,
            rationale=(
                "The mechanism, the measured reduction, and the cost are each "
                "attributed to a named source, the reduction carries "
                "independent corroboration, and no obligation is left open."
            ),
        ),
        state_factory=_strong_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-one-minor-gap",
        title="A sound answer with one minor gap",
        purpose=(
            "The same sound candidate, with one wording-level defect: the "
            "mechanism is stated twice in different words. A minor defect is "
            "named and does not buy another research pass."
        ),
        score_band=(7, 9),
        expects_acceptance=True,
        expects_a_defect=True,
        draft=_review(
            score=8,
            gaps=[
                _gap(
                    problem=(
                        "The summary and the first finding restate the "
                        "mechanism in different words, which reads as two "
                        "separate findings."
                    ),
                    statement_ids=[_MECHANISM_STATEMENT],
                    kind="presentation",
                    severity="minor",
                    repair_action="synthesize",
                )
            ],
            rationale=(
                "The answer is complete and corroborated; its only defect is "
                "that one mechanism is stated twice."
            ),
        ),
        state_factory=_strong_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-missing-critical-topic",
        title="A critical obligation the report never answers",
        purpose=(
            "The question asks what substitution costs; this candidate answers "
            "the mechanism and the reduction and says nothing about cost, and "
            "its records answer the cost target nowhere. It cannot be accepted."
        ),
        score_band=(1, 5),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=4,
            gaps=[
                _gap(
                    problem=(
                        "The report never states what substitution costs, which "
                        "is the second half of the question and a critical "
                        "obligation of this plan; no statement and no read "
                        "answers it."
                    ),
                    target_ids=[_COST_TARGET],
                    kind="coverage",
                    severity="critical",
                    repair_action="acquire",
                    queries=["clinker substitution cost premium per tonne"],
                )
            ],
            queries=["clinker substitution cost premium per tonne"],
            rationale=(
                "One of the three obligations is untouched, so the question is "
                "answered only in part."
            ),
        ),
        state_factory=_missing_cost_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-unsupported-central-assertion",
        title="A central assertion no read supports",
        purpose=(
            "The report asserts that commercial-scale deployment is "
            "accelerating. No claim stands behind that sentence and no read "
            "carries it, so the packet renders it as a statement with no "
            "evidence at all."
        ),
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            unsupported=[
                "Commercial-scale deployment of low-carbon cement is "
                "accelerating across several markets, which no cited source in "
                "this report measures."
            ],
            rationale=(
                "A load-bearing sentence is asserted with no evidence behind "
                "it, so part of the answer rests on nothing checkable."
            ),
        ),
        state_factory=_unsupported_assertion_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-attributed-primary-fact",
        title="An appropriately attributed primary fact",
        purpose=(
            "The mechanism and the cost are one publisher's own accounts — "
            "source-supported attribution, not independent corroboration "
            "(Section 2.1) — and both are labelled as such. A correctly "
            "attributed primary fact is not a defect."
        ),
        score_band=(7, 9),
        expects_acceptance=True,
        expects_a_defect=False,
        draft=_review(
            score=8,
            rationale=(
                "The mechanism and the cost are attributed to the issuing body "
                "that states them and are labelled as that body's own account, "
                "which is the correct reading of a primary source; the "
                "reduction behind them carries independent corroboration."
            ),
        ),
        state_factory=_attributed_primary_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-false-independent-pair",
        title="A false independent-pair claim",
        purpose=(
            "Two reads stand behind the measured reduction, but they resolve "
            "to one publisher's identity — an association's roadmap and its own "
            "summary — so there is no independent pair and nothing is "
            "corroborated (Sections 2.1 and 2.2)."
        ),
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            gaps=[
                _gap(
                    problem=(
                        "The corroboration behind the 0.4-tonne reduction is "
                        "not independent: both passages resolve to one "
                        "publisher's identity, so the independent pair the "
                        "report implies does not exist."
                    ),
                    claim_cluster_ids=[_EMISSIONS_CLUSTER],
                    kind="identity",
                    severity="major",
                    repair_action="adjudicate",
                )
            ],
            rationale=(
                "The measured reduction is presented as corroborated when its "
                "two passages share one origin."
            ),
        ),
        state_factory=_false_pair_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-polished-verbose-non-answer",
        title="A polished, verbose non-answer",
        purpose=(
            "Three hundred words of confident framing whose records answer "
            "nothing: both reader statements are unattributed framing and all "
            "three obligations are open. Length and confidence cannot buy a "
            "passing score."
        ),
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            gaps=[
                _gap(
                    problem=(
                        "The report describes the direction of travel but never "
                        "states a measured reduction, which is what the "
                        "question asks for."
                    ),
                    target_ids=[_EMISSIONS_TARGET],
                    kind="coverage",
                    severity="critical",
                    repair_action="acquire",
                    queries=["clinker substitution share of cement emissions"],
                )
            ],
            queries=["clinker substitution share of cement emissions"],
            unsupported=[
                "The premium for lower-carbon cement is narrowing in the "
                "markets that moved first, which no cited source measures."
            ],
            rationale=(
                "The prose is confident and long, but no obligation is "
                "discharged and no figure is attributed."
            ),
        ),
        state_factory=_polished_non_answer_calibration_state,
    ),
    CriticCalibrationCase(
        case_id="calibration-honest-but-incomplete-answer",
        title="An honest but substantively incomplete answer",
        purpose=(
            "The report answers the mechanism plainly and says outright that "
            "the reduction and the cost were not established. Candour is not an "
            "answer: both obligations remain open, and the candidate's records "
            "answer only the mechanism."
        ),
        score_band=(3, 6),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=5,
            gaps=[
                _gap(
                    problem=(
                        "The measured reduction is named as unestablished and "
                        "left unquantified, so the plan's reduction obligation "
                        "remains unanswered."
                    ),
                    target_ids=[_EMISSIONS_TARGET],
                    kind="coverage",
                    severity="major",
                    repair_action="acquire",
                    queries=["clinker substitution share of cement emissions"],
                ),
                _gap(
                    problem=(
                        "The cost of substitution is named as unestablished and "
                        "never quantified, and the plan's cost obligation "
                        "remains unanswered."
                    ),
                    target_ids=[_COST_TARGET],
                    kind="coverage",
                    severity="major",
                    repair_action="acquire",
                    queries=["clinker substitution cost premium per tonne"],
                ),
            ],
            queries=[
                "clinker substitution share of cement emissions",
                "clinker substitution cost premium per tonne",
            ],
            rationale=(
                "The report is honest about what it could not establish, which "
                "is better than hiding it, but two of the three obligations the "
                "question depends on remain unanswered."
            ),
        ),
        state_factory=_honest_incomplete_calibration_state,
    ),
)

CALIBRATION_CASES_BY_ID = {case.case_id: case for case in CALIBRATION_CASES}


def calibration_case(case_id: str) -> CriticCalibrationCase:
    """One calibration case by id, or a loud failure naming the known ids."""
    try:
        return CALIBRATION_CASES_BY_ID[case_id]
    except KeyError:
        known = ", ".join(CALIBRATION_CASES_BY_ID)
        raise KeyError(
            f"unknown critic calibration case {case_id!r}; expected one of: "
            f"{known}"
        ) from None


@dataclass(frozen=True, slots=True)
class CriticCalibrationOutcome:
    """What the local build made of one scripted calibration review."""

    case_id: str
    score: int
    band: tuple[int, int]
    accepted: bool
    expects_acceptance: bool
    defect_found: bool
    expects_a_defect: bool
    gap_count: int
    material_gap_count: int
    reason: str

    @property
    def in_band(self) -> bool:
        return self.band[0] <= self.score <= self.band[1]

    @property
    def false_acceptance(self) -> bool:
        """Accepted an answer that should have been rejected."""
        return self.accepted and not self.expects_acceptance

    @property
    def false_rejection(self) -> bool:
        """Rejected an answer that should have been accepted."""
        return (not self.accepted) and self.expects_acceptance

    @property
    def missed_defect(self) -> bool:
        """The candidate had a defect and the review named none."""
        return self.expects_a_defect and not self.defect_found


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class CriticCalibrationReport:
    """The three calibration error rates, each over its own denominator.

    Reported separately on purpose. False acceptance, false rejection, and
    missed defects are three different mistakes: folding them into one
    accuracy figure hides which of them a change made worse. The denominator
    is always the cases that could make that mistake — only an answer that
    should be rejected can be falsely accepted, and only a candidate with a
    defect can have that defect missed — so the three rates are not
    commensurable and are never summed.
    """

    outcomes: tuple[CriticCalibrationOutcome, ...]
    provider_calls: int = 0
    basis: str = CALIBRATION_BASIS
    self_agreement_is_ground_truth: bool = False

    def outcome(self, case_id: str) -> CriticCalibrationOutcome:
        return next(
            item for item in self.outcomes if item.case_id == case_id
        )

    @property
    def cases_expecting_rejection(self) -> int:
        return sum(1 for item in self.outcomes if not item.expects_acceptance)

    @property
    def cases_expecting_acceptance(self) -> int:
        return sum(1 for item in self.outcomes if item.expects_acceptance)

    @property
    def cases_expecting_a_defect(self) -> int:
        return sum(1 for item in self.outcomes if item.expects_a_defect)

    @property
    def false_acceptances(self) -> int:
        return sum(1 for item in self.outcomes if item.false_acceptance)

    @property
    def false_rejections(self) -> int:
        return sum(1 for item in self.outcomes if item.false_rejection)

    @property
    def missed_defects(self) -> int:
        return sum(1 for item in self.outcomes if item.missed_defect)

    @property
    def false_acceptance_rate(self) -> float:
        """Accepted-but-should-reject, over the cases that should reject."""
        return _rate(self.false_acceptances, self.cases_expecting_rejection)

    @property
    def false_rejection_rate(self) -> float:
        """Rejected-but-should-accept, over the cases that should accept."""
        return _rate(self.false_rejections, self.cases_expecting_acceptance)

    @property
    def missed_defect_rate(self) -> float:
        """Defect named nowhere, over the candidates that carry one."""
        return _rate(self.missed_defects, self.cases_expecting_a_defect)


def measure_critic_calibration(
    *,
    score_overrides: Mapping[str, int] | None = None,
    gap_overrides: Mapping[str, Sequence[dict[str, object]]] | None = None,
) -> CriticCalibrationReport:
    """Read every calibration case with the real local build, offline.

    The overrides exist so a test can inject exactly one mistake and see which
    of the three counts moves; no production caller passes them. Nothing here
    reaches a provider, which is why ``provider_calls`` is a constant zero
    rather than a measurement.
    """
    scores = dict(score_overrides or {})
    gaps = {key: list(value) for key, value in (gap_overrides or {}).items()}
    outcomes: list[CriticCalibrationOutcome] = []
    for case in CALIBRATION_CASES:
        draft = dict(case.draft)
        if case.case_id in scores:
            draft["score"] = scores[case.case_id]
        if case.case_id in gaps:
            draft["gaps"] = gaps[case.case_id]
        packet = calibration_packet(case.case_id)
        critique, reason = build_critique(
            CritiqueDraft.model_validate(draft),
            iteration=0,
            max_iterations=3,
            known_coverage_ids={
                topic.coverage_id for topic in packet.sub_topics
            },
            packet=packet,
        )
        accepted = not critique.should_continue and critique.score >= (
            ACCEPTANCE_SCORE
        )
        outcomes.append(
            CriticCalibrationOutcome(
                case_id=case.case_id,
                score=critique.score,
                band=case.score_band,
                accepted=accepted,
                expects_acceptance=case.expects_acceptance,
                defect_found=bool(critique.gaps)
                or bool(critique.unsupported_claims),
                expects_a_defect=case.expects_a_defect,
                gap_count=len(critique.gaps),
                material_gap_count=sum(
                    1 for gap in critique.gaps if gap.material
                ),
                reason=reason,
            )
        )
    return CriticCalibrationReport(outcomes=tuple(outcomes))
