"""Researcher evaluation cases: coverage, conflict, and partial recovery."""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    evaluation_state,
    metrics,
    rubric,
    sub_topic,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others. The two grounding
# dimensions are shared as plain tuples — rubric() copies them into a fresh
# JudgeRubric per call — so the dimension text is written once, not
# verbatim in all four rubrics.

_GROUNDING_DIMENSIONS = (
    (
        "evidence_grounding",
        "Findings are grounded in the sources the researcher actually retrieved.",
        "Every finding cites a retrieved source and matches its content.",
        "Findings cite invented, unattributed, or unretrieved sources.",
    ),
    (
        "source_diversity",
        "Findings draw on multiple independent sources.",
        "Findings span at least three distinct registrable domains.",
        "Findings all come from a single domain or source.",
    ),
)

_COVERAGE_RUBRIC = rubric("researcher-multi-source", *_GROUNDING_DIMENSIONS)

_CONFLICT_RUBRIC = rubric(
    "researcher-conflicting",
    *_GROUNDING_DIMENSIONS,
    (
        "disagreement_handling",
        "Disagreeing evidence is preserved, not flattened into consensus.",
        "Conflicting sources are both retained and the disagreement is explicit.",
        "Disagreement is hidden or one side is silently dropped.",
    ),
)

_FAILURE_RUBRIC = rubric(
    "researcher-partial-failure",
    *_GROUNDING_DIMENSIONS,
    (
        "failure_transparency",
        "A tool failure is surfaced, not hidden, and recovery is bounded.",
        "The failed search or scrape is recorded and surviving evidence "
        "is still reported.",
        "Failures are silently dropped or the research is abandoned.",
    ),
)

_LIVE_RUBRIC = rubric("researcher-live-evidence", *_GROUNDING_DIMENSIONS)

# The scripted URLs are shared with the dependency scenarios: the case
# declares them as known_source_urls, and dependencies.py scripts the same
# URLs as search results and page bodies.

_MULTI_SOURCE_URLS = (
    "https://www.nrel.gov/heat-pump-cop-below-freezing",
    "https://www.iea.org/heat-pump-cop-report",
    "https://www.sciencedirect.com/heat-pump-cop-review",
    "https://www.nrel.gov/cold-climate-field-trial",
    "https://www.iea.org/cold-climate-heat-pumps",
    "https://www.sciencedirect.com/cold-climate-trial-results",
    "https://www.nrel.gov/backup-heating-guidance",
    "https://www.iea.org/backup-heating-report",
    "https://www.sciencedirect.com/backup-heating-analysis",
)

_GAIN_URL = "https://www.aeaweb.org/four-day-work-week-trial"
_NO_CHANGE_URL = "https://www.aeaweb.org/four-day-work-week-replication"
_CONFOUNDED_URL = "https://www.nber.org/four-day-self-selection"
_CONFLICT_URLS = (_GAIN_URL, _NO_CHANGE_URL, _CONFOUNDED_URL)

_FAILURE_URLS = (
    "https://www.nrel.gov/perovskite-encapsulation-study",
    "https://www.sciencedirect.com/perovskite-encapsulation-review",
)

_MULTI_SOURCE_SUB_TOPICS = (
    sub_topic(
        "Coefficient of performance below freezing",
        rationale=(
            "COP below freezing determines whether heat pumps can actually "
            "serve cold climates."
        ),
        queries=["heat pump COP -15C field data"],
        criteria=["COP measurements at or below -15C"],
        priority=1,
    ),
    sub_topic(
        "Cold-climate field trial outcomes",
        rationale=(
            "Field trials show real-world performance outside the lab."
        ),
        queries=["cold climate heat pump field trial results"],
        criteria=["At least one field trial outcome"],
        priority=2,
    ),
    sub_topic(
        "Backup heating requirements",
        rationale="Backup heating needs affect cost and efficiency claims.",
        queries=["heat pump backup resistance heating cold climate"],
        criteria=["Backup sizing guidance or energy impact"],
        priority=3,
    ),
)

_CONFLICT_SUB_TOPICS = (
    sub_topic(
        "Trial outcomes",
        rationale="Trial results determine the productivity claim.",
        queries=["four-day work week trial productivity results"],
        criteria=["Reported trial outcomes with effect sizes"],
        priority=1,
    ),
    sub_topic(
        "Measurement methodology",
        rationale=(
            "How productivity was measured decides whether the gains are real."
        ),
        queries=["four-day work week productivity measurement methodology"],
        criteria=["Measurement approach and sample construction"],
        priority=2,
    ),
)

_FAILURE_SUB_TOPICS = (
    sub_topic(
        "Moisture-driven degradation",
        rationale="Moisture is the dominant commercial-lifetime limit.",
        queries=["perovskite moisture-driven degradation"],
        criteria=["Degradation mechanism evidence"],
        priority=1,
    ),
    sub_topic(
        "Encapsulation approaches",
        rationale="Encapsulation is the main mitigation for moisture ingress.",
        queries=["perovskite encapsulation approaches lifetime"],
        criteria=["Encapsulation outcome data"],
        priority=2,
    ),
)

_LIVE_SUB_TOPICS = (
    sub_topic(
        "Reported cell-level energy density",
        rationale=(
            "Energy density is the headline performance metric for "
            "sodium-ion cells."
        ),
        queries=["sodium-ion battery energy density Wh/kg 2026"],
        criteria=["Cell-level Wh/kg figures from named sources"],
        priority=1,
    ),
)

_MULTI_SOURCE = build_case(
    case_id="multi-source-coverage",
    agent_name="researcher",
    tier="controlled",
    title="Cover every subtopic from multiple independent domains",
    purpose=(
        "Research all three planned subtopics and produce at least one "
        "finding per subtopic, drawing on at least three distinct "
        "registrable domains, staying within the tool budget."
    ),
    state=evaluation_state(
        case_id="multi-source-coverage",
        question="How effective are heat pumps in cold climates?",
        sub_topics=_MULTI_SOURCE_SUB_TOPICS,
    ),
    dependency_scenario="researcher-multi-source",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            "sub_topic_titles": [
                topic.title for topic in _MULTI_SOURCE_SUB_TOPICS
            ],
            "minimum_findings_per_sub_topic": 1,
            "minimum_distinct_domains": 3,
        },
        known_source_urls=list(_MULTI_SOURCE_URLS),
        max_iterations=18,
        max_tool_calls=18,
        deterministic_metrics=metrics(
            (
                "sub_topic_coverage",
                0.30,
                "Every subtopic has at least one finding or a recorded skip "
                "reason.",
            ),
            (
                "source_grounding",
                0.30,
                "Every finding's source_url is in known_source_urls.",
            ),
            (
                "source_diversity",
                0.25,
                "Findings span at least three distinct registrable domains.",
            ),
            (
                "budget_respected",
                0.15,
                "Tool calls stayed within the case budget.",
            ),
        ),
    ),
    judge_rubric=_COVERAGE_RUBRIC,
    metadata={"scenario": "normal"},
)

_CONFLICT = build_case(
    case_id="conflicting-evidence",
    agent_name="researcher",
    tier="controlled",
    title="Preserve disagreeing evidence instead of forcing consensus",
    purpose=(
        "Research two subtopics whose scripted sources deliberately "
        "disagree, and retain the disagreement rather than reporting a "
        "false consensus."
    ),
    state=evaluation_state(
        case_id="conflicting-evidence",
        question="Does a four-day work week increase productivity?",
        sub_topics=_CONFLICT_SUB_TOPICS,
    ),
    dependency_scenario="researcher-conflicting",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            "conflicting_urls": [_GAIN_URL, _NO_CHANGE_URL],
            "required_uncertainty_signals": [
                "disagree",
                "mixed",
                "confounded",
                "self-selected",
            ],
        },
        known_source_urls=list(_CONFLICT_URLS),
        max_iterations=10,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "uncertainty_preserved",
                0.40,
                "At least one finding references the disagreement, or two "
                "findings with opposing content are both retained.",
            ),
            (
                "no_false_consensus",
                0.25,
                "Not every finding asserts the same direction.",
            ),
            (
                "source_grounding",
                0.20,
                "Every finding's source_url is in known_source_urls.",
            ),
            (
                "budget_respected",
                0.15,
                "Tool calls stayed within the case budget.",
            ),
        ),
    ),
    judge_rubric=_CONFLICT_RUBRIC,
    metadata={"scenario": "challenging"},
)

_PARTIAL_FAILURE = build_case(
    case_id="partial-search-failure",
    agent_name="researcher",
    tier="controlled",
    title="Recover when one search and one scrape fail",
    purpose=(
        "Research two subtopics when the first subtopic's search fails, "
        "surviving evidence from the second subtopic is still produced, "
        "and every failure is recorded as a recoverable error."
    ),
    state=evaluation_state(
        case_id="partial-search-failure",
        question="What limits perovskite solar cell commercial lifetime?",
        sub_topics=_FAILURE_SUB_TOPICS,
    ),
    dependency_scenario="researcher-partial-failure",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={
            "failing_sub_topic": "Moisture-driven degradation",
            "recoverable_sources": ["web_search", "web_scraper"],
        },
        known_source_urls=list(_FAILURE_URLS),
        max_iterations=8,
        max_tool_calls=8,
        deterministic_metrics=metrics(
            (
                "partial_results_present",
                0.35,
                "At least one finding was produced.",
            ),
            (
                "failure_recorded",
                0.30,
                "The state update carries at least one recoverable "
                "ResearchError.",
            ),
            (
                "no_invented_sources",
                0.20,
                "No finding cites a URL outside known_source_urls.",
            ),
            (
                "budget_respected",
                0.15,
                "Tool calls stayed within the case budget.",
            ),
        ),
        must_record_recoverable_error=True,
    ),
    judge_rubric=_FAILURE_RUBRIC,
    metadata={"scenario": "failure-recovery"},
)

_LIVE = build_case(
    case_id="researcher-live-evidence",
    agent_name="researcher",
    tier="live",
    title="Research a current sodium-ion energy-density question",
    purpose=(
        "Research one current subtopic with live web search and page "
        "fetches, grounding every finding in a URL the run itself "
        "actually retrieved."
    ),
    state=evaluation_state(
        case_id="researcher-live-evidence",
        question="What is the current state of sodium-ion battery energy density?",
        sub_topics=_LIVE_SUB_TOPICS,
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["findings"],
        reference={},
        known_source_urls=[],
        max_iterations=10,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "sources_are_real_urls",
                0.30,
                "Every finding's source_url appears in the recorded tool "
                "trajectory.",
            ),
            (
                "sub_topic_coverage",
                0.30,
                "Every subtopic has at least one finding or a recorded skip "
                "reason.",
            ),
            (
                "source_diversity",
                0.25,
                "Findings span at least three distinct registrable domains.",
            ),
            (
                "budget_respected",
                0.15,
                "Tool calls stayed within the case budget.",
            ),
        ),
        required_live_dependencies=["tavily", "http"],
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _MULTI_SOURCE,
    _CONFLICT,
    _PARTIAL_FAILURE,
)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
