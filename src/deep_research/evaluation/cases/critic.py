"""Critic evaluation cases: routing, gaps, and budget discipline."""

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
        "limitations section. The scripted scenario gives the spot-check loop "
        "one corroborating search result and one prior memory finding, so a "
        "strong critique can route to end on the evidence alone."
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
            ),
            claim(
                "Peer-reviewed field measurements find urban tree canopy "
                "reduces summer surface temperatures by 1 to 5 degrees "
                "Celsius.",
                urls=(_CANOPY_REVIEW_URL, _USFS_CANOPY_URL),
                verdict="verified",
                confidence=0.85,
            ),
            claim(
                "The cooling effect of tree canopy grows with canopy "
                "cover and leaf area.",
                urls=(_USFS_CANOPY_URL, _CANOPY_REVIEW_URL),
                verdict="verified",
                confidence=0.82,
            ),
            claim(
                "Neighborhoods with more than 30 percent canopy cover "
                "saw the largest measured temperature reductions in "
                "Phoenix.",
                urls=(_PHOENIX_CANOPY_URL,),
                verdict="verified",
                confidence=0.80,
            ),
            claim(
                "Urban tree canopy reduced summer surface temperatures "
                "by an average of 3 degrees Celsius across all monitored "
                "districts.",
                urls=(_PHOENIX_CANOPY_URL,),
                verdict="unverified",
                confidence=0.55,
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
                corroboration=0.80,
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
                corroboration=0.78,
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
                corroboration=0.85,
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
                corroboration=0.75,
                overall=0.82,
            ),
            scored_source(
                _PHOENIX_CANOPY_URL,
                title="Phoenix: tree canopy surface temperature study",
                rationale="City monitoring program with district-level measurements.",
                authority=0.72,
                recency=0.86,
                relevance=0.90,
                corroboration=0.65,
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
        max_tool_calls=10,
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
        "measurement methodology are missing, one of two sources is low "
        "confidence, and the report's only claim is unverified. The scripted "
        "scenario gives the spot-check loop a search result on participation "
        "rates and a memory entry naming both gaps, so the critique can list "
        "them and route to refine."
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
                corroboration=0.70,
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
                corroboration=0.25,
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
        max_tool_calls=10,
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
        "scripted memory query fails with the memory backend unavailable while "
        "the search still succeeds, so the run records the failure as "
        "recoverable and still produces a critique that must stop regardless of "
        "its score."
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
                corroboration=0.20,
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
        max_tool_calls=10,
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
                "failure_recorded",
                0.2,
                "The run records the scripted recoverable failure in its failure "
                "ledger.",
            ),
            (
                "score_bounded",
                0.15,
                "The score is an integer in 1-10.",
            ),
        ),
        must_record_recoverable_error=True,
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
    title="Live spot-check review of low-carbon cement evidence",
    purpose=(
        "Critique a fixed, current report on low-carbon cement performance at "
        "scale using live web searches and live memory reads, reusing the "
        "strong controlled case's four metrics unchanged. The report and its "
        "themes are fixed in the case state, so no_spurious_gaps stays gradable "
        "against the declared reference themes."
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
            ),
            claim(
                "Low-carbon cements meet compressive strength standards "
                "in most applications, while long-term durability data "
                "are still accumulating.",
                urls=(_NATURE_CE_URL, _GCCA_URL),
                verdict="verified",
                confidence=0.80,
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
                corroboration=0.80,
                overall=0.86,
            ),
            scored_source(
                _IEA_CE_URL,
                title="IEA: cement industry energy analysis",
                rationale="International agency analysis of cement sector deployment.",
                authority=0.92,
                recency=0.90,
                relevance=0.88,
                corroboration=0.82,
                overall=0.88,
            ),
            scored_source(
                _NATURE_CE_URL,
                title="Nature: cement decarbonization at scale",
                rationale="Peer-reviewed review of low-carbon cement performance.",
                authority=0.90,
                recency=0.85,
                relevance=0.85,
                corroboration=0.78,
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
        max_tool_calls=10,
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
        required_live_dependencies=("tavily", "memory"),
    ),
    judge_rubric=_LIVE_RUBRIC,
)

CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _STRONG_CASE,
    _GAPPY_CASE,
    _BUDGET_CASE,
)

LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE_CASE,)
