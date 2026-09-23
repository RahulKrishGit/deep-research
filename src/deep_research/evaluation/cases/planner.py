"""Planner evaluation cases: decomposition, ambiguity, and recovery."""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    evaluation_state,
    metrics,
    rubric,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others.

_FOCUSED_RUBRIC = rubric(
    "planner-decomposition",
    (
        "decomposition_quality",
        "Subtopics partition the question without overlapping.",
        "Three to seven distinct subtopics that together cover the question.",
        "Overlapping, missing, or off-question subtopics.",
    ),
    (
        "search_framing",
        "Each subtopic carries queries a researcher could run unchanged.",
        "Specific, answerable queries tied to the subtopic.",
        "Vague queries that restate the subtopic title.",
    ),
)

_AMBIGUITY_RUBRIC = rubric(
    "planner-ambiguity",
    (
        "decomposition_quality",
        "Subtopics partition the question without overlapping.",
        "Three to seven distinct subtopics that together cover the question.",
        "Overlapping, missing, or off-question subtopics.",
    ),
    (
        "search_framing",
        "Each subtopic carries queries a researcher could run unchanged.",
        "Specific, answerable queries tied to the subtopic.",
        "Vague queries that restate the subtopic title.",
    ),
    (
        "ambiguity_handling",
        "An ambiguous question is scoped without inventing constraints.",
        "Scope is widened through explicit, balanced subtopics.",
        "A narrower question is silently substituted.",
    ),
)

_FAILURE_RUBRIC = rubric(
    "planner-failure-recovery",
    (
        "decomposition_quality",
        "Subtopics partition the question without overlapping.",
        "Three to seven distinct subtopics that together cover the question.",
        "Overlapping, missing, or off-question subtopics.",
    ),
    (
        "search_framing",
        "Each subtopic carries queries a researcher could run unchanged.",
        "Specific, answerable queries tied to the subtopic.",
        "Vague queries that restate the subtopic title.",
    ),
    (
        "failure_transparency",
        "A tool failure is surfaced, not hidden.",
        "The plan is complete and the failure is stated, not hidden.",
        "The failure is silently dropped or the plan is abandoned.",
    ),
)

_LIVE_RUBRIC = rubric(
    "planner-live-scope",
    (
        "decomposition_quality",
        "Subtopics partition the question without overlapping.",
        "Three to seven distinct subtopics that together cover the question.",
        "Overlapping, missing, or off-question subtopics.",
    ),
    (
        "search_framing",
        "Each subtopic carries queries a researcher could run unchanged.",
        "Specific, answerable queries tied to the subtopic.",
        "Vague queries that restate the subtopic title.",
    ),
)

_SCOPED_RUBRIC = rubric(
    "planner-scoped-targets",
    (
        "target_scoping",
        "Each obligation is answerable by a bounded read rather than a "
        "restatement of the whole question.",
        "Every target asks for one fact, measure, or comparison a single "
        "source can settle.",
        "Obligations restate the question or ask for 'relevant information'.",
    ),
    (
        "policy_fidelity",
        "A comparative obligation is phrased as a comparison and keeps the "
        "independent-pair burden that phrasing earns.",
        "The comparative target names both options and the dimension they are "
        "compared on.",
        "A single-side lookup stands in for the comparison, so the "
        "independent-evidence burden disappears.",
    ),
)

# The dimension wordings the case refuses: each names no atom field the
# coverage machinery can credit, so a target carrying only one of these
# states an obligation no source can ever be shown to answer.
_VAGUE_DIMENSION_PHRASES = (
    "relevant information",
    "good sources",
    "background",
    "details",
    "context",
    "overview",
    "key facts",
    "general information",
)

# `planner-scoped-targets` has no scripted search results and no memory
# entries: a plan's scoping is decided from the question and the answer
# contract, so the scenario exists only to make those dependencies available
# and to keep the case's dependency behavior deterministic.
_SCOPED = build_case(
    case_id="scoped-evidence-targets",
    agent_name="planner",
    tier="controlled",
    title="Scope a comparative question into creditable obligations",
    purpose=(
        "Decompose a comparative question that also carries an official-"
        "instrument component: every sub-topic must carry at least one "
        "counted evidence target, every required dimension must name "
        "something evidence can credit, and the comparative obligation must "
        "keep the independent-pair policy its own wording earns instead of "
        "being downgraded to a single-side lookup."
    ),
    state=evaluation_state(
        case_id="scoped-evidence-targets",
        question=(
            "How do documented interconnection queue wait times for "
            "utility-scale solar and utility-scale wind compare in the "
            "United States, and what does the current federal "
            "interconnection rule require of each?"
        ),
    ),
    dependency_scenario="planner-scoped-targets",
    expectations=CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={
            "minimum_sub_topics": 3,
            "maximum_sub_topics": 7,
            "minimum_targets_per_sub_topic": 1,
            # The contract's own ceiling, declared here as well so a reader
            # of the case sees the bound the metric polices.
            "maximum_targets_per_sub_topic": 4,
            "required_support_policies": [
                "independent_pair",
                "primary_attribution",
            ],
            "vague_dimension_phrases": list(_VAGUE_DIMENSION_PHRASES),
        },
        known_source_urls=[],
        max_iterations=5,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "subtopic_count",
                0.20,
                "Between 3 and 7 subtopics were produced.",
            ),
            (
                "targets_declared",
                0.25,
                "Every subtopic carries 1-4 counted evidence targets; a "
                "reserved omission marker is not an obligation.",
            ),
            (
                "dimensions_are_checkable",
                0.20,
                "Every required dimension of every target is one a recorded "
                "proposition can credit.",
            ),
            (
                "support_policy_not_downgraded",
                0.20,
                "No target's recorded support policy is weaker than the "
                "policy its own question earns, and the plan covers the "
                "declared policy set.",
            ),
            (
                "no_vague_dimensions",
                0.15,
                "No target rests on a dimension that names nothing "
                "answerable.",
            ),
        ),
    ),
    judge_rubric=_SCOPED_RUBRIC,
    metadata={"scenario": "challenging"},
)

_FOCUSED = build_case(
    case_id="focused-decomposition",
    agent_name="planner",
    tier="controlled",
    title="Decompose a focused research question",
    purpose=(
        "Decompose a focused research question into 3-7 distinct, "
        "prioritized subtopics. Check coverage, non-overlap, ordering, and "
        "useful search framing."
    ),
    state=evaluation_state(
        case_id="focused-decomposition",
        question=(
            "What are the dominant degradation mechanisms in solid-state "
            "lithium batteries, and how do they limit cycle life?"
        ),
    ),
    dependency_scenario="planner-clean-memory",
    expectations=CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={
            "minimum_sub_topics": 3,
            "maximum_sub_topics": 7,
            "expected_themes": [
                "dendrite formation",
                "interfacial resistance",
                "mechanical stress and cracking",
                "cycle-life measurement",
            ],
        },
        known_source_urls=[],
        max_iterations=5,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "subtopic_count",
                0.25,
                "Between 3 and 7 subtopics were produced.",
            ),
            (
                "distinct_titles",
                0.25,
                "No two subtopic titles normalize to the same string.",
            ),
            (
                "priority_ordering",
                0.20,
                "Priorities are positive and strictly increase in order.",
            ),
            (
                "query_quality",
                0.15,
                "Every subtopic carries at least one non-trivial query "
                "that is not a copy of its title.",
            ),
            (
                "question_preserved",
                0.15,
                "The state update does not rewrite original_question.",
            ),
        ),
    ),
    judge_rubric=_FOCUSED_RUBRIC,
    metadata={"scenario": "normal"},
)

_AMBIGUOUS = build_case(
    case_id="ambiguous-scope",
    agent_name="planner",
    tier="controlled",
    title="Resolve an ambiguous research question",
    purpose=(
        "Decompose an ambiguous question into balanced subtopics covering "
        "benefits, risks, and evidence quality without inventing "
        "constraints the question never named."
    ),
    state=evaluation_state(
        case_id="ambiguous-scope",
        question="Is AI good for healthcare?",
    ),
    dependency_scenario="planner-ambiguous-scope",
    expectations=CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={
            "minimum_sub_topics": 4,
            "maximum_sub_topics": 7,
            "forbidden_assumptions": [
                "specific country",
                "specific vendor",
                "specific year",
            ],
            "required_balance": ["benefits", "risks", "evidence quality"],
        },
        known_source_urls=[],
        max_iterations=5,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "subtopic_count",
                0.20,
                "Between 4 and 7 subtopics were produced.",
            ),
            (
                "distinct_titles",
                0.20,
                "No two subtopic titles normalize to the same string.",
            ),
            (
                "balanced_coverage",
                0.30,
                "At least one subtopic addresses benefits and at least one "
                "addresses risks or harms.",
            ),
            (
                "no_invented_constraints",
                0.30,
                "No subtopic title or query names a country, vendor, or "
                "year absent from the question.",
            ),
        ),
    ),
    judge_rubric=_AMBIGUITY_RUBRIC,
    metadata={"scenario": "challenging"},
)

_FAILURE = build_case(
    case_id="planning-tool-failure",
    agent_name="planner",
    tier="controlled",
    title="Plan around a memory tool failure",
    purpose=(
        "Produce a complete plan after query_memory fails, record the "
        "recoverable error, and stay within the tool-call budget."
    ),
    state=evaluation_state(
        case_id="planning-tool-failure",
        question=(
            "What evidence supports intermittent fasting for metabolic "
            "health?"
        ),
    ),
    dependency_scenario="planner-memory-failure",
    expectations=CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={
            "minimum_sub_topics": 3,
            "expected_error_sources": ["query_memory"],
        },
        known_source_urls=[],
        max_iterations=5,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "plan_still_valid",
                0.40,
                "3-7 distinct subtopics were produced despite the failure.",
            ),
            (
                "failure_recorded",
                0.35,
                "The state update carries at least one recoverable "
                "ResearchError.",
            ),
            (
                "bounded_recovery",
                0.25,
                "Tool calls stayed within the case budget.",
            ),
        ),
        must_record_recoverable_error=True,
    ),
    judge_rubric=_FAILURE_RUBRIC,
    metadata={"scenario": "failure-recovery"},
)

_LIVE = build_case(
    case_id="planner-live-scope",
    agent_name="planner",
    tier="live",
    title="Scope a current sodium-ion deployment question",
    purpose=(
        "Decompose a current, time-sensitive deployment question into "
        "prioritized subtopics using live memory recall; web search is "
        "optional."
    ),
    state=evaluation_state(
        case_id="planner-live-scope",
        question=(
            "What are the current operational constraints on grid-scale "
            "sodium-ion battery deployment?"
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["sub_topics"],
        reference={
            "minimum_sub_topics": 3,
            "maximum_sub_topics": 7,
        },
        known_source_urls=[],
        max_iterations=5,
        max_tool_calls=10,
        # The same five metrics as focused-decomposition, so controlled and
        # live scores are directly comparable — but built fresh, because
        # build_case stores mutable lists by reference.
        deterministic_metrics=metrics(
            (
                "subtopic_count",
                0.25,
                "Between 3 and 7 subtopics were produced.",
            ),
            (
                "distinct_titles",
                0.25,
                "No two subtopic titles normalize to the same string.",
            ),
            (
                "priority_ordering",
                0.20,
                "Priorities are positive and strictly increase in order.",
            ),
            (
                "query_quality",
                0.15,
                "Every subtopic carries at least one non-trivial query "
                "that is not a copy of its title.",
            ),
            (
                "question_preserved",
                0.15,
                "The state update does not rewrite original_question.",
            ),
        ),
        required_live_dependencies=["memory"],
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

# Append new cases; never prepend. ``conftest.controlled_case_for``
# takes ``cases_for(agent, "controlled")[0]``, so the first case here
# is the one every conftest-driven gate test exercises.
CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _FOCUSED,
    _AMBIGUOUS,
    _FAILURE,
    _SCOPED,
)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
