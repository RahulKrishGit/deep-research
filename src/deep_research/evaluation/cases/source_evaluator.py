"""Source Evaluator evaluation cases: ranking, signal balance, and recovery."""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    evaluation_state,
    finding,
    metrics,
    rubric,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others. The two core scoring
# dimensions are shared as plain tuples — rubric() copies them into a fresh
# JudgeRubric per call — so the dimension text is written once, not
# verbatim in all four rubrics.

_SOURCE_SCORING_DIMENSIONS = (
    (
        "rationale_quality",
        "Each scored source's rationale explains why it earned its scores.",
        "Rationales cite the source's content and make the scores plausible.",
        "Rationales are generic, empty, or contradict the scores.",
    ),
    (
        "ranking_discipline",
        "Scores rank sources by credibility, not by convenience.",
        "Authoritative sources score above obviously weak ones.",
        "Weak sources score at or above authoritative ones without "
        "justification.",
    ),
)

_MIXED_RUBRIC = rubric("source-evaluator-mixed", *_SOURCE_SCORING_DIMENSIONS)

_COMPETING_RUBRIC = rubric(
    "source-evaluator-competing-signals",
    *_SOURCE_SCORING_DIMENSIONS,
    (
        "signal_balance",
        "No single signal decides a source's overall score.",
        "Overall scores blend authority, recency, reputation, and "
        "corroboration; rationales name more than one signal.",
        "One signal (recency or authority alone) visibly decided the "
        "outcome.",
    ),
)

_FAILURE_RUBRIC = rubric(
    "source-evaluator-reputation-failure",
    *_SOURCE_SCORING_DIMENSIONS,
    (
        "degradation_honesty",
        "A reputation failure is surfaced, not hidden, and scoring degrades "
        "honestly.",
        "Every source is still scored, the failure is recorded, and no "
        "source claims a reputation it never received.",
        "Sources are dropped, the failure is hidden, or scores pretend the "
        "lookup succeeded.",
    ),
)

_LIVE_RUBRIC = rubric(
    "source-evaluator-live-ranking", *_SOURCE_SCORING_DIMENSIONS
)

# The scripted URLs are shared with the dependency scenarios: the cases
# declare them as raw_findings and in their references, and dependencies.py
# scripts the same registrable domains as reputations and reputation
# failures. The case tests pin both sides to the same literals.

_IPCC_URL = "https://www.ipcc.ch/ar6-wg1"
_AMETSOC_URL = "https://journals.ametsoc.org/regional-precip"
_WEATHERBLOG_URL = "https://weatherblog.example.com/my-take"
_FORUM_URL = "https://forum.example.net/thread/1182"
_NOAA_URL = "https://www.noaa.gov/precip-assessment"

_PAPER_URL = "https://www.science.org/methane-leakage-us-2016"
_PREPRINT_URL = "https://arxiv.org/abs/methane-leakage-2026"
_INDUSTRY_URL = "https://www.ingaa.org/leakage-mitigation-2024"
_NGO_URL = "https://www.edf.org/basin-methane-measurements-2025"

_EPA_SENSOR_URL = "https://www.epa.gov/consumer-grade-air-sensors"
_AQMD_SENSOR_URL = "https://www.aqmd.gov/sensor-field-evaluations"
_NIST_SENSOR_URL = "https://www.nist.gov/air-quality-sensor-testbed"
_CU_SENSOR_URL = "https://www.colorado.edu/consumer-sensor-assessment"

_EPA_DATA_URL = "https://www.epa.gov/outdoor-air-quality-data"
_WHO_DATA_URL = "https://www.who.int/health-topics/air-pollution"
_BLOG_DATA_URL = "https://medium.com/@urbanairwatcher/public-air-quality-datasets"
_FORUM_DATA_URL = "https://www.reddit.com/r/AirQuality/comments/dataset_reliability/"

_MIXED = build_case(
    case_id="strong-and-weak-sources",
    agent_name="source_evaluator",
    tier="controlled",
    title="Rank authoritative sources above obviously weak ones",
    purpose=(
        "Score five scripted sources for a climate-modeling question: one "
        "evaluation per canonical URL, every authoritative source ranked "
        "above every obviously weak source, bounded scores, and a "
        "low-confidence flag on the forum source."
    ),
    state=evaluation_state(
        case_id="strong-and-weak-sources",
        question="How well do current climate models predict regional precipitation?",
        findings=(
            finding(
                "IPCC AR6 Working Group I assesses regional precipitation "
                "projections and reports increasing model agreement at "
                "longer timescales.",
                url=_IPCC_URL,
                title="IPCC AR6 Working Group I report",
                sub_topic_title="Regional precipitation skill",
            ),
            finding(
                "A peer-reviewed AMS journal study evaluates CMIP-class "
                "models against observed regional precipitation and finds "
                "skill varies by region and season.",
                url=_AMETSOC_URL,
                title="AMS journal regional precipitation evaluation",
                sub_topic_title="Regional precipitation skill",
            ),
            finding(
                "A personal weather blog asserts that every climate model "
                "underpredicts summer rainfall, citing a single local event.",
                url=_WEATHERBLOG_URL,
                title="Weather blog: my take on the models",
                sub_topic_title="Regional precipitation skill",
            ),
            finding(
                "A forum thread collects anecdotes that precipitation "
                "forecasts are always wrong, with no measurements or "
                "sources.",
                url=_FORUM_URL,
                title="Forum thread: are the models any good?",
                sub_topic_title="Public skepticism online",
            ),
            finding(
                "NOAA's precipitation assessment reports that current "
                "models reproduce observed regional precipitation means "
                "well in most extratropical regions.",
                url=_NOAA_URL,
                title="NOAA regional precipitation assessment",
                sub_topic_title="Regional precipitation skill",
            ),
        ),
    ),
    dependency_scenario="source-evaluator-mixed",
    expectations=CaseExpectations(
        required_output_fields=["evaluated_sources"],
        reference={
            "authoritative_urls": [_IPCC_URL, _AMETSOC_URL, _NOAA_URL],
            "weak_urls": [_WEATHERBLOG_URL, _FORUM_URL],
            "expected_low_confidence_urls": [_FORUM_URL],
        },
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "one_evaluation_per_source",
                0.30,
                "Exactly one `ScoredSource` per canonical URL, no extras.",
            ),
            (
                "score_ordering",
                0.30,
                "Every authoritative source scores above every obviously "
                "weak source.",
            ),
            (
                "bounded_scores",
                0.20,
                "All six scores are finite and in `[0,1]`.",
            ),
            (
                "low_confidence_flagged",
                0.20,
                "The forum source carries `low_confidence=True`.",
            ),
        ),
    ),
    judge_rubric=_MIXED_RUBRIC,
    metadata={"scenario": "normal"},
)

_COMPETING = build_case(
    case_id="corroboration-recency-reputation",
    agent_name="source_evaluator",
    tier="controlled",
    title="Blend competing signals instead of letting one decide",
    purpose=(
        "Score four sources that pull in different directions — an old "
        "authoritative paper, a recent unreviewed preprint, an industry "
        "page with a remembered reputation, and an independent NGO "
        "measurement — so no single signal decides any overall score and "
        "the rationales mention more than one signal."
    ),
    state=evaluation_state(
        case_id="corroboration-recency-reputation",
        question=(
            "What is the measured methane leakage rate from US natural "
            "gas systems?"
        ),
        findings=(
            finding(
                "A 2016 peer-reviewed study estimated the US natural gas "
                "leakage rate at 2.3 percent of production using top-down "
                "atmospheric measurements.",
                url=_PAPER_URL,
                title="Science: methane leakage in the US natural gas system",
                sub_topic_title="Leakage rate measurements",
            ),
            finding(
                "A 2026 preprint reports a single-site leakage estimate of "
                "0.4 percent from an unreviewed measurement campaign.",
                url=_PREPRINT_URL,
                title="arXiv preprint: single-site methane survey",
                sub_topic_title="New measurement campaigns",
            ),
            finding(
                "A 2024 industry association page reports that voluntary "
                "programs have reduced distribution-system leakage, citing "
                "member-reported data.",
                url=_INDUSTRY_URL,
                title="INGAA: leakage mitigation progress",
                sub_topic_title="Leakage rate measurements",
            ),
            finding(
                "A 2025 independent NGO campaign measured leakage across "
                "major production basins and found rates consistent with "
                "the 2016 top-down estimate.",
                url=_NGO_URL,
                title="EDF: basin-scale methane measurements",
                sub_topic_title="Leakage rate measurements",
            ),
        ),
    ),
    dependency_scenario="source-evaluator-competing-signals",
    expectations=CaseExpectations(
        required_output_fields=["evaluated_sources"],
        reference={
            "single_signal_traps": {
                "recency_only_url": _PREPRINT_URL,
                "authority_only_url": _PAPER_URL,
            },
        },
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "balanced_scoring",
                0.40,
                "No source's `overall_score` equals any single component "
                "score to within 0.01 — a tell that one signal decided the "
                "outcome.",
            ),
            (
                "one_evaluation_per_source",
                0.25,
                "Exactly one `ScoredSource` per canonical URL, no extras.",
            ),
            (
                "bounded_scores",
                0.20,
                "All six scores are finite and in `[0,1]`.",
            ),
            (
                "rationale_mentions_multiple_signals",
                0.15,
                "At least one source's rationale mentions more than one of "
                "authority, recency, reputation, or corroboration.",
            ),
        ),
    ),
    judge_rubric=_COMPETING_RUBRIC,
    metadata={"scenario": "challenging"},
)

_FAILURE = build_case(
    case_id="reputation-provider-failure",
    agent_name="source_evaluator",
    tier="controlled",
    title="Keep scoring every source when reputation lookups fail",
    purpose=(
        "Score four sources while the reputation lookup fails for two of "
        "their domains: every source still receives a bounded score, the "
        "failure is recorded as recoverable, and no source carries a "
        "reputation it never received."
    ),
    state=evaluation_state(
        case_id="reputation-provider-failure",
        question="How reliable are consumer-grade air quality sensors?",
        findings=(
            finding(
                "EPA guidance reports that consumer-grade air quality "
                "sensors can disagree with reference monitors by 30-50 "
                "percent for fine particulate matter.",
                url=_EPA_SENSOR_URL,
                title="EPA: consumer air sensor guidance",
                sub_topic_title="Sensor accuracy",
            ),
            finding(
                "South Coast AQMD field evaluations found low-cost PM2.5 "
                "sensors track reference monitors on average but drift "
                "without calibration.",
                url=_AQMD_SENSOR_URL,
                title="AQMD: low-cost sensor field evaluation",
                sub_topic_title="Sensor accuracy",
            ),
            finding(
                "NIST's sensor testbed reports that consumer air quality "
                "monitor performance varies widely across models under "
                "controlled conditions.",
                url=_NIST_SENSOR_URL,
                title="NIST: air quality sensor testbed",
                sub_topic_title="Sensor accuracy",
            ),
            finding(
                "A university lab assessment found consumer sensors "
                "reliably rank environments by relative PM2.5 levels even "
                "when absolute readings differ.",
                url=_CU_SENSOR_URL,
                title="CU Boulder: consumer sensor lab assessment",
                sub_topic_title="Sensor accuracy",
            ),
        ),
    ),
    dependency_scenario="source-evaluator-reputation-failure",
    expectations=CaseExpectations(
        required_output_fields=["evaluated_sources"],
        reference={
            "failing_domains": ["epa.gov", "aqmd.gov"],
            "succeeding_domains": ["nist.gov", "colorado.edu"],
        },
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "all_sources_still_scored",
                0.35,
                "One evaluation per canonical source despite the failures.",
            ),
            (
                "fallback_scores_bounded",
                0.25,
                "Every source scored without reputation data still carries "
                "finite scores in `[0,1]`.",
            ),
            (
                "failure_recorded",
                0.25,
                "The state update carries at least one recoverable "
                "ResearchError.",
            ),
            (
                "no_fabricated_reputation",
                0.15,
                "A source whose reputation lookup failed does not carry the "
                "maximum authority score.",
            ),
        ),
        must_record_recoverable_error=True,
    ),
    judge_rubric=_FAILURE_RUBRIC,
    metadata={"scenario": "failure-recovery"},
)

_LIVE = build_case(
    case_id="source-evaluator-live-ranking",
    agent_name="source_evaluator",
    tier="live",
    title="Rank current public air quality datasets by trustworthiness",
    purpose=(
        "Score four current, real sources on public urban air quality "
        "datasets with live memory-backed reputation reads, ranking the "
        "authoritative sources above the personal blog and the forum "
        "thread."
    ),
    state=evaluation_state(
        case_id="source-evaluator-live-ranking",
        question=(
            "How trustworthy are the leading public datasets on urban "
            "air quality?"
        ),
        findings=(
            finding(
                "EPA publishes hourly and annual urban air quality data "
                "from the Air Quality System monitoring network, including "
                "PM2.5 and ozone.",
                url=_EPA_DATA_URL,
                title="EPA: outdoor air quality data",
                sub_topic_title="Urban air quality datasets",
            ),
            finding(
                "WHO compiles urban air quality statistics from official "
                "national reporting into its ambient air quality database.",
                url=_WHO_DATA_URL,
                title="WHO: ambient air quality database",
                sub_topic_title="Urban air quality datasets",
            ),
            finding(
                "A personal blog compares public urban air quality datasets "
                "and recommends one over the others based on personal "
                "experience.",
                url=_BLOG_DATA_URL,
                title="Personal blog: my dataset comparison",
                sub_topic_title="Urban air quality datasets",
            ),
            finding(
                "A forum thread shares anecdotes about discrepancies "
                "between two public urban air quality datasets.",
                url=_FORUM_DATA_URL,
                title="Reddit thread: dataset discrepancies",
                sub_topic_title="Urban air quality datasets",
            ),
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["evaluated_sources"],
        # Live-run note for Task 18's evaluator: the Source Evaluator
        # declares no tools and derives its canonical source set from
        # state.raw_findings (group_findings_by_url), so the URLs a live
        # run scores are exactly the four fixed findings below — only the
        # reputation reads are live (required_live_dependencies=["memory"]).
        # The reference therefore partitions those same URLs, in case 1's
        # shape, and score_ordering / low_confidence_flagged are checked
        # URL-exactly against them, with no auto-pass fallback.
        reference={
            "authoritative_urls": [_EPA_DATA_URL, _WHO_DATA_URL],
            "weak_urls": [_BLOG_DATA_URL, _FORUM_DATA_URL],
            "expected_low_confidence_urls": [_FORUM_DATA_URL],
        },
        max_iterations=1,
        max_tool_calls=0,
        deterministic_metrics=metrics(
            (
                "one_evaluation_per_source",
                0.30,
                "Exactly one `ScoredSource` per canonical URL, no extras.",
            ),
            (
                "score_ordering",
                0.30,
                "Every authoritative source scores above every obviously "
                "weak source.",
            ),
            (
                "bounded_scores",
                0.20,
                "All six scores are finite and in `[0,1]`.",
            ),
            (
                "low_confidence_flagged",
                0.20,
                "The forum source carries `low_confidence=True`.",
            ),
        ),
        required_live_dependencies=["memory"],
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _MIXED,
    _COMPETING,
    _FAILURE,
)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
