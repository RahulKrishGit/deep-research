"""Critic evaluation cases: routing, gaps, and budget discipline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    CriticPacket,
    CritiqueDraft,
    build_critic_packet,
    build_critique,
)
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
    ClaimCluster,
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
                0.4,
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
                "rationale_present",
                0.2,
                "A non-blank rationale that says why the run stopped; the "
                "critique is the only place a forced stop is explained, since "
                "the Critic calls no tool that could fail.",
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

# The candidate's inventory. Every calibration case reviews a candidate with
# this same shape, so a gap's ids mean the same thing in every case and the
# scripted reviews can be compared with each other.
CALIBRATION_CLUSTER_IDS = {
    "mechanism": "cluster-calibration-mechanism",
    "figure": "cluster-calibration-figure",
}
CALIBRATION_STATEMENT_IDS = {"mechanism": "S001", "figure": "F001"}
CALIBRATION_TARGET_IDS = {
    "mechanism": "target-01",
    "figure": "target-02",
    "durability": "target-03",
}
CALIBRATION_EVIDENCE_IDS = {
    "mechanism": "evidence-calibration-01",
    "figure": "evidence-calibration-02",
}

_CALIBRATION_MECHANISM_CLAIM = (
    "Clinker substitution cuts process emissions by replacing the clinker "
    "fraction of cement with supplementary materials."
)
_CALIBRATION_FIGURE_CLAIM = (
    "Substituting clinker can remove 0.4 tonnes of carbon dioxide per tonne "
    "of cement produced."
)
_CALIBRATION_MECHANISM_EVIDENCE = (
    "Substituting clinker with calcined clay or slag displaces the clinker "
    "fraction and therefore the process emissions it carries."
)
_CALIBRATION_FIGURE_EVIDENCE = (
    "Each tonne of clinker avoided removes roughly 0.4 tonnes of process "
    "carbon dioxide."
)

_CALIBRATION_REPORT = (
    "# Research report: How far can clinker substitution cut cement process "
    "emissions, and what does it cost?\n\n"
    "## Summary\n\nClinker substitution lowers process emissions by displacing "
    "the clinker fraction of cement, and the GCCA's own roadmap puts the "
    "figure at roughly 0.4 tonnes of carbon dioxide per tonne of cement "
    "avoided. [1]\n\n"
    "## Findings\n\nIndustry analysis estimates that each tonne of clinker "
    "avoided removes about 0.4 tonnes of process carbon dioxide. [2]\n"
)

# The polished non-answer: long, confident, and carrying no figure a read
# supports. Its length is the point — 300+ words of prose must not outscore a
# short answer that actually answers.
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

CALIBRATION_BASIS = (
    "These scores are the local build's reading of scripted reviews of fixed "
    "candidates. A model agreeing with its own scripted review is not ground "
    "truth: independent report review and source checks remain required, and "
    "the rates below measure the contract's behaviour, not whether any "
    "individual judgement was right."
)


def _calibration_sub_topic() -> SubTopic:
    """The candidate's plan: three critical targets, one of them unanswered."""
    return SubTopic(
        coverage_id="topic-calibration",
        title="Clinker substitution",
        rationale="The question turns on the substitution share and its cost.",
        search_queries=["clinker substitution emissions reduction share"],
        success_criteria=["A measured share with a named source."],
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
                target_id=CALIBRATION_TARGET_IDS["figure"],
                coverage_id="topic-calibration",
                question="How much carbon dioxide does substitution remove?",
                required_dimensions=["scale"],
                required=True,
                critical=True,
                support_policy="independent_pair",
            ),
            EvidenceTarget(
                target_id=CALIBRATION_TARGET_IDS["durability"],
                coverage_id="topic-calibration",
                question="What field-durability evidence exists for blended "
                "cements?",
                required_dimensions=["period"],
                required=True,
                critical=True,
                support_policy="independent_pair",
            ),
        ],
    )


def _calibration_composition() -> ReportComposition:
    """The candidate's typed composition: two statements, three targets."""
    mechanism = claim(
        _CALIBRATION_MECHANISM_CLAIM,
        urls=(_GCCA_URL,),
        verdict="insufficient_evidence",
        confidence=0.6,
        evidence=(_CALIBRATION_MECHANISM_EVIDENCE,),
        verification_urls=(_IEA_CE_URL,),
    )
    figure = claim(
        _CALIBRATION_FIGURE_CLAIM,
        urls=(_GCCA_URL,),
        verdict="verified",
        confidence=0.85,
        evidence=(_CALIBRATION_FIGURE_EVIDENCE,),
        verification_urls=(_NATURE_CE_URL,),
    )
    clusters = {
        CALIBRATION_CLUSTER_IDS["mechanism"]: ClaimCluster(
            cluster_id=CALIBRATION_CLUSTER_IDS["mechanism"],
            proposition=AtomicProposition(
                text=_CALIBRATION_MECHANISM_CLAIM,
                subject="clinker substitution",
                predicate="states_mechanism",
            ),
            evidence_ids=[CALIBRATION_EVIDENCE_IDS["mechanism"]],
            member_claim_ids=[mechanism.claim_id],
            target_ids=[CALIBRATION_TARGET_IDS["mechanism"]],
            source_urls=list(mechanism.source_urls),
            verdicts=["insufficient_evidence"],
            verdict_evidence_status={"insufficient_evidence": "source_supported"},
        ),
        CALIBRATION_CLUSTER_IDS["figure"]: ClaimCluster(
            cluster_id=CALIBRATION_CLUSTER_IDS["figure"],
            proposition=AtomicProposition(
                text=_CALIBRATION_FIGURE_CLAIM,
                subject="clinker substitution",
                predicate="states_value",
                value="0.4",
                unit="tCO2/t",
            ),
            evidence_ids=[CALIBRATION_EVIDENCE_IDS["figure"]],
            member_claim_ids=[figure.claim_id],
            target_ids=[CALIBRATION_TARGET_IDS["figure"]],
            source_urls=list(figure.source_urls),
            verdicts=["verified"],
            verdict_evidence_status={"verified": "verified_pair"},
        ),
    }
    units = {
        CALIBRATION_EVIDENCE_IDS["mechanism"]: EvidenceUnit(
            evidence_id=CALIBRATION_EVIDENCE_IDS["mechanism"],
            read_id="read-calibration-01",
            source_url=_IEA_CE_URL,
            source_title="IEA: cement industry energy analysis",
            locator="section-mechanism",
            excerpt=_CALIBRATION_MECHANISM_EVIDENCE,
            target_ids=[CALIBRATION_TARGET_IDS["mechanism"]],
            origin="researcher",
        ),
        CALIBRATION_EVIDENCE_IDS["figure"]: EvidenceUnit(
            evidence_id=CALIBRATION_EVIDENCE_IDS["figure"],
            read_id="read-calibration-02",
            source_url=_NATURE_CE_URL,
            source_title="Nature: cement decarbonization at scale",
            locator="section-figure",
            excerpt=_CALIBRATION_FIGURE_EVIDENCE,
            target_ids=[CALIBRATION_TARGET_IDS["figure"]],
            origin="researcher",
        ),
    }
    return ReportComposition(
        question=CALIBRATION_QUESTION,
        session_id="evaluation-critic-calibration",
        sub_topics=[_calibration_sub_topic()],
        claims=[mechanism, figure],
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
        summary=[
            ReportPoint(
                text=_CALIBRATION_MECHANISM_CLAIM,
                claim_ids=[mechanism.claim_id],
                source_urls=list(mechanism.source_urls),
            )
        ],
        sections=[
            ReportSection(
                title="Findings",
                points=[
                    ReportPoint(
                        text=_CALIBRATION_FIGURE_CLAIM,
                        claim_ids=[figure.claim_id],
                        source_urls=list(figure.source_urls),
                    )
                ],
            )
        ],
    )


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
    report: str = _CALIBRATION_REPORT


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
_DURABILITY_TARGET = CALIBRATION_TARGET_IDS["durability"]
_FIGURE_CLUSTER = CALIBRATION_CLUSTER_IDS["figure"]
_MECHANISM_STATEMENT = CALIBRATION_STATEMENT_IDS["mechanism"]

CALIBRATION_CASES: tuple[CriticCalibrationCase, ...] = (
    CriticCalibrationCase(
        case_id="calibration-strong-answer",
        title="A complete, well-cited answer",
        purpose=(
            "Every critical target is answered from named sources, the "
            "measured figure is independently corroborated, and the report "
            "states what it does not know."
        ),
        score_band=(8, 10),
        expects_acceptance=True,
        expects_a_defect=False,
        draft=_review(
            score=9,
            rationale=(
                "The mechanism and the measured figure are both attributed to "
                "named sources, the figure carries independent corroboration, "
                "and the durability uncertainty is disclosed rather than "
                "hidden."
            ),
        ),
    ),
    CriticCalibrationCase(
        case_id="calibration-one-minor-gap",
        title="A sound answer with one minor gap",
        purpose=(
            "The same sound answer, with one wording-level defect: the "
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
    ),
    CriticCalibrationCase(
        case_id="calibration-missing-critical-topic",
        title="A critical planned topic the report never answers",
        purpose=(
            "The durability obligation is critical, planned, and answered by "
            "no statement and no read. The report is otherwise competent, and "
            "still cannot be accepted."
        ),
        report=_CALIBRATION_REPORT,
        score_band=(1, 5),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=4,
            gaps=[
                _gap(
                    problem=(
                        "The report never addresses field-durability evidence "
                        "for blended cements, which is a critical obligation "
                        "of this plan and is answered by no statement at all."
                    ),
                    target_ids=[_DURABILITY_TARGET],
                    kind="coverage",
                    severity="critical",
                    repair_action="acquire",
                    queries=["blended cement field durability trial results"],
                )
            ],
            queries=["blended cement field durability trial results"],
            rationale=(
                "One of three critical obligations is untouched, so the "
                "question is answered only in part."
            ),
        ),
    ),
    CriticCalibrationCase(
        case_id="calibration-unsupported-central-assertion",
        title="A central assertion no read supports",
        purpose=(
            "The report's central claim about deployment is presented as "
            "fact, is attributed to no cited read, and no excerpt carries it."
        ),
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            unsupported=[
                "Commercial-scale deployment is accelerating across several "
                "markets, which no cited source in this report measures."
            ],
            rationale=(
                "The claim the question turns on is asserted without a source "
                "that measures it, so the answer rests on nothing checkable."
            ),
        ),
    ),
    CriticCalibrationCase(
        case_id="calibration-attributed-primary-fact",
        title="An appropriately attributed primary fact",
        purpose=(
            "The mechanism statement carries one publisher's own account — "
            "source-supported attribution, not independent corroboration "
            "(Section 2.1). A correctly attributed primary fact is not a "
            "defect, and must not be scored as one."
        ),
        score_band=(7, 9),
        expects_acceptance=True,
        expects_a_defect=False,
        draft=_review(
            score=8,
            rationale=(
                "The mechanism is attributed to the issuing body that states "
                "it and is labelled as that body's own account, which is the "
                "correct reading of a primary source; the measured figure "
                "behind it carries independent corroboration."
            ),
        ),
    ),
    CriticCalibrationCase(
        case_id="calibration-false-independent-pair",
        title="A false independent-pair claim",
        purpose=(
            "Two URLs stand behind the measured figure, but they are one "
            "publisher's roadmap and its own summary: there is no independent "
            "pair, so nothing is corroborated (Sections 2.1 and 2.2)."
        ),
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            gaps=[
                _gap(
                    problem=(
                        "The corroboration behind the 0.4-tonne figure is not "
                        "independent: both passages come from the same "
                        "publisher's roadmap and its own summary, so the "
                        "independent pair the report implies does not exist."
                    ),
                    claim_cluster_ids=[_FIGURE_CLUSTER],
                    kind="identity",
                    severity="major",
                    repair_action="adjudicate",
                )
            ],
            rationale=(
                "The headline figure is presented as corroborated when its "
                "two passages share one origin."
            ),
        ),
    ),
    CriticCalibrationCase(
        case_id="calibration-polished-verbose-non-answer",
        title="A polished, verbose non-answer",
        purpose=(
            "Three hundred words of confident framing, no measured figure a "
            "read supports, and no obligation discharged. Length and "
            "confidence cannot buy a passing score."
        ),
        report=_POLISHED_NON_ANSWER_REPORT,
        score_band=(1, 4),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=3,
            gaps=[
                _gap(
                    problem=(
                        "The report describes the direction of travel but "
                        "never states a measured substitution share, which is "
                        "what the question asks for."
                    ),
                    target_ids=[_MECHANISM_TARGET],
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
    ),
    CriticCalibrationCase(
        case_id="calibration-honest-but-incomplete-answer",
        title="An honest but substantively incomplete answer",
        purpose=(
            "The report answers the mechanism plainly and says outright that "
            "the cost and durability evidence was not found. Candour is not "
            "an answer: the outstanding obligations are still outstanding."
        ),
        score_band=(3, 6),
        expects_acceptance=False,
        expects_a_defect=True,
        draft=_review(
            score=5,
            gaps=[
                _gap(
                    problem=(
                        "The cost of substitution is named as outstanding but "
                        "never quantified, and the plan's cost obligation "
                        "remains unanswered."
                    ),
                    target_ids=[_MECHANISM_TARGET],
                    kind="acquisition",
                    severity="major",
                    repair_action="acquire",
                    queries=["clinker substitution cost premium per tonne"],
                ),
                _gap(
                    problem=(
                        "No field-durability evidence was acquired, and the "
                        "report says so without answering the obligation."
                    ),
                    target_ids=[_DURABILITY_TARGET],
                    kind="coverage",
                    severity="major",
                    repair_action="acquire",
                    queries=["blended cement durability field exposure"],
                ),
            ],
            queries=[
                "clinker substitution cost premium per tonne",
                "blended cement durability field exposure",
            ],
            rationale=(
                "The report is honest about what it could not establish, "
                "which is better than hiding it, but two obligations the "
                "question depends on remain unanswered."
            ),
        ),
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


def calibration_packet(case_id: str = CALIBRATION_CASES[0].case_id) -> CriticPacket:
    """The packet one calibration case's candidate is reviewed from.

    The report text is the case's, and the composition is the shared one, so
    the ids a scripted gap names resolve in every case. Nothing here calls a
    provider: the packet is built from fixtures.
    """
    case = calibration_case(case_id)
    composition = _calibration_composition()
    state = ResearchState(
        session_id="evaluation-critic-calibration",
        original_question=CALIBRATION_QUESTION,
        report=case.report,
        composition=composition,
        sub_topics=[_calibration_sub_topic()],
        verified_claims=list(composition.claims),
    )
    return build_critic_packet(state, state.composition)
