"""Focused tests for the framework-light UI progress projector."""

from __future__ import annotations

from dataclasses import replace

import pytest

from deep_research.observability.tracker import TokenUsage
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.ui.progress import (
    credibility_tier,
    display_agent_name,
    display_tool_name,
    fact_check_summary,
    limitations_from_outcome,
    project_progress,
    source_summary,
    token_usage_from_outcome,
)
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
)


def event(
    event_type: str,
    *,
    source: str = "test",
    metadata: dict[str, object] | None = None,
) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type,
        source=source,
        message="Private provider message must not be rendered.",
        metadata=metadata or {},
    )


def source(
    url: str,
    *,
    overall_score: float = 0.9,
    recency_score: float = 0.8,
    relevance_score: float = 0.8,
    low_confidence: bool = False,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Source title",
        authority_score=0.8,
        recency_score=recency_score,
        relevance_score=relevance_score,
        corroboration_score=0.7,
        overall_score=overall_score,
        rationale="Structured scoring rationale.",
        low_confidence=low_confidence,
    )


def finding(url: str, topic: str) -> Finding:
    return Finding(
        content="Typed finding content.",
        source_url=url,
        source_title="Source title",
        extracted_at="2026-09-09T00:00:00+00:00",
        confidence=0.8,
        related_sub_topic=topic,
    )


def claim(verdict: str) -> Claim:
    return Claim(
        text=f"Typed {verdict} claim.",
        source_urls=["https://example.org/source"],
        verdict=verdict,
        confidence=0.8,
        evidence=["Typed evidence."],
        contradictions=[] if verdict != "contradicted" else ["Typed contradiction."],
    )


def outcome(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    critique: Critique | None = None,
    report: str | None = None,
) -> ResearchOutcome:
    state = ResearchState(
        session_id="session-1",
        original_question="Typed question",
        critique=critique,
        report=report,
    )
    return ResearchOutcome(
        session_id="session-1",
        question="Typed question",
        status="completed",
        state=state,
        trace_url=None,
        report_path=None,
        token_usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        tool_calls=(),
    )


def test_project_progress_projects_agent_iteration_subtopics_and_tools() -> None:
    events = [
        event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 1},
        ),
        event(
            "researcher.sub_topic.started",
            metadata={"index": 1, "sub_topic": "Market size", "priority": 2},
        ),
        event(
            "researcher.tool_call",
            metadata={"tool": "web_search", "success": True},
        ),
        event(
            "researcher.tool_call",
            metadata={"tool": "web_search", "success": False},
        ),
        event(
            "researcher.sub_topic.completed",
            metadata={"index": 1, "findings": 3},
        ),
        event(
            "researcher.sub_topic.started",
            metadata={"index": 2, "sub_topic": "Regulatory drivers", "priority": 1},
        ),
    ]

    summary = project_progress(events)

    assert summary.current_agent == "researcher"
    assert summary.iteration == 1
    assert summary.events_seen == len(events)
    assert [topic.model_dump() for topic in summary.sub_topics] == [
        {
            "index": 1,
            "title": "Market size",
            "status": "completed",
            "priority": 2,
            "findings": 3,
        },
        {
            "index": 2,
            "title": "Regulatory drivers",
            "status": "running",
            "priority": 1,
            "findings": None,
        },
    ]
    assert [call.model_dump() for call in summary.tool_calls] == [
        {
            "tool_name": "web_search",
            "display_label": "Web search",
            "calls": 2,
            "failures": 1,
        }
    ]


def test_project_progress_projects_recoverable_graph_errors_without_double_counting(
) -> None:
    summary = project_progress(
        [
            event(
                "researcher.tool_call",
                metadata={"tool": "web_search", "success": False},
            ),
            event(
                "graph.node.completed",
                metadata={
                    "node": "researcher",
                    "iteration": 1,
                    "event_count": 2,
                    "error_count": 1,
                },
            ),
        ]
    )

    assert summary.issue_count == 1


def test_project_progress_projects_non_tool_graph_errors() -> None:
    summary = project_progress(
        [
            event(
                "graph.node.completed",
                metadata={
                    "node": "source_evaluator",
                    "iteration": 1,
                    "event_count": 1,
                    "error_count": 2,
                },
            )
        ]
    )

    assert summary.issue_count == 2


def test_project_progress_uses_graph_iteration_not_researcher_react_iteration() -> None:
    summary = project_progress(
        [
            event(
                "graph.node.started",
                metadata={"node": "researcher", "iteration": 1},
            ),
            event(
                "researcher.sub_topic.started",
                metadata={"index": 1, "sub_topic": "Market size"},
            ),
            event(
                "researcher.tool_call",
                metadata={
                    "tool": "web_search",
                    "success": True,
                    "iteration": 6,
                },
            ),
            event(
                "researcher.tool_call",
                metadata={
                    "tool": "web_search",
                    "success": True,
                    "iteration": 7,
                },
            ),
            event(
                "graph.refinement.started",
                metadata={"iteration": 2, "max_iterations": 4},
            ),
            event(
                "graph.node.started",
                metadata={"node": "researcher", "iteration": 2},
            ),
            event(
                "researcher.tool_call",
                metadata={
                    "tool": "web_search",
                    "success": True,
                    "iteration": 8,
                },
            ),
        ]
    )

    assert summary.iteration == 2


def test_project_progress_separates_planned_total_from_known_actual_rows() -> None:
    summary = project_progress(
        [
            event(
                "planner.planning.completed",
                metadata={"sub_topic_count": 5},
            ),
            event(
                "researcher.sub_topic.started",
                metadata={"index": 1, "sub_topic": "Grid adoption"},
            ),
            event(
                "researcher.sub_topic.started",
                metadata={"index": 2},
            ),
            event(
                "researcher.sub_topic.completed",
                metadata={
                    "index": 1,
                    "sub_topic": "Grid adoption",
                    "findings": 3,
                },
            ),
            event(
                "researcher.research.completed",
                metadata={
                    "sub_topics_planned": 5,
                    "sub_topics_researched": 1,
                    "sub_topics_skipped": 4,
                },
            ),
            event(
                "graph.node.started",
                metadata={"node": "source_evaluator", "iteration": 1},
            ),
        ]
    )

    assert summary.planned_sub_topic_count == 5
    assert [topic.model_dump() for topic in summary.sub_topics] == [
        {
            "index": 1,
            "title": "Grid adoption",
            "status": "completed",
            "priority": None,
            "findings": 3,
        }
    ]
    assert summary.research_phase_complete is True
    assert summary.current_agent == "source_evaluator"
    assert all("Queued subtopic" not in topic.title for topic in summary.sub_topics)


def test_project_progress_preserves_all_real_titled_subtopics() -> None:
    titles = ["Market size", "Grid adoption", "Policy", "Investment", "Pricing"]
    events = [
        event("planner.planning.completed", metadata={"sub_topic_count": 5})
    ]
    for index, title in enumerate(titles, start=1):
        events.extend(
            [
                event(
                    "researcher.sub_topic.started",
                    metadata={"index": index, "sub_topic": title},
                ),
                event(
                    "researcher.sub_topic.completed",
                    metadata={"index": index, "findings": 1},
                ),
            ]
        )
    events.append(
        event(
            "researcher.research.completed",
            metadata={
                "sub_topics_planned": 5,
                "sub_topics_researched": 5,
                "sub_topics_skipped": 0,
            },
        )
    )

    summary = project_progress(events)

    assert summary.planned_sub_topic_count == 5
    assert [topic.title for topic in summary.sub_topics] == titles
    assert [topic.status for topic in summary.sub_topics] == [
        "completed"
    ] * 5
    assert summary.research_phase_complete is True


def test_project_progress_preserves_subtopic_fields_on_completion() -> None:
    summary = project_progress(
        [
            event(
                "researcher.sub_topic.started",
                metadata={"index": 3, "sub_topic": "Policy", "priority": 1},
            ),
            event(
                "researcher.sub_topic.completed",
                metadata={"index": 3, "findings": 0},
            ),
        ]
    )

    assert summary.sub_topics[0].title == "Policy"
    assert summary.sub_topics[0].priority == 1
    assert summary.sub_topics[0].status == "completed"
    assert summary.sub_topics[0].findings == 0


def test_project_progress_clears_agent_after_terminal_iteration() -> None:
    summary = project_progress(
        [
            event(
                "graph.node.started",
                metadata={"node": "critic", "iteration": 4},
            ),
            event(
                "graph.session.completed",
                metadata={"iteration": 4, "status": "max_iterations"},
            ),
        ]
    )

    assert summary.current_agent is None
    assert summary.last_agent == "critic"
    assert summary.iteration == 4


def test_project_progress_retains_stopping_subtopic_for_terminal_budget_stop() -> None:
    summary = project_progress(
        [
            event(
                "graph.node.started",
                metadata={"node": "researcher", "iteration": 2},
            ),
            event(
                "researcher.sub_topic.started",
                metadata={"index": 2, "sub_topic": "Last active topic"},
            ),
            event(
                "graph.session.completed",
                metadata={"iteration": 2, "status": "max_iterations"},
            ),
        ]
    )

    assert summary.current_agent is None
    assert summary.last_agent == "researcher"
    assert summary.last_sub_topic is not None
    assert summary.last_sub_topic.title == "Last active topic"
    assert [topic.title for topic in summary.sub_topics] == ["Last active topic"]
    assert summary.sub_topics[0].status == "running"


def test_project_progress_ignores_malformed_metadata() -> None:
    summary = project_progress(
        [
            event(
                "graph.node.started",
                metadata={"node": "  ", "iteration": True},
            ),
            event(
                "graph.node.started",
                metadata={"node": "  ", "iteration": -1},
            ),
            event(
                "researcher.sub_topic.started",
                metadata={"index": 0, "sub_topic": "Nope", "priority": 0},
            ),
            event(
                "researcher.sub_topic.completed",
                metadata={"index": "1", "findings": -1},
            ),
            event("researcher.tool_call", metadata={"tool": "  ", "success": False}),
        ]
    )

    assert summary.current_agent is None
    assert summary.iteration == 0
    assert summary.sub_topics == []
    assert summary.tool_calls == []
    assert summary.recent_activity == []
    assert summary.events_seen == 5


def test_project_progress_keeps_newest_three_meaningful_activities() -> None:
    events = [
        event(
            "researcher.sub_topic.started",
            metadata={"index": 1, "sub_topic": "Market size", "priority": 1},
        ),
        event(
            "researcher.tool_call",
            metadata={
                "tool": "web_search",
                "success": True,
                "sub_topic": "Market size",
            },
        ),
        event(
            "researcher.sub_topic.completed",
            metadata={"index": 1, "sub_topic": "Market size", "findings": 3},
        ),
        event(
            "researcher.sub_topic.started",
            metadata={"index": 2, "sub_topic": "Regulatory drivers", "priority": 1},
        ),
        event(
            "researcher.tool_call",
            metadata={
                "tool": "web_search",
                "success": False,
                "sub_topic": "Regulatory drivers",
            },
        ),
    ]

    activities = project_progress(events).recent_activity

    assert len(activities) == 3
    assert [activity.summary for activity in activities] == [
        "Completed Market size; 3 evidence items recorded",
        "Started Regulatory drivers",
        "A research step had an issue; continuing",
    ]
    assert all("web_search" not in activity.summary for activity in activities)
    assert all("Private provider" not in activity.summary for activity in activities)


def test_graph_node_starts_are_fallback_activity_only() -> None:
    summary = project_progress(
        [event("graph.node.started", metadata={"node": "source_evaluator"})]
    )

    assert [activity.summary for activity in summary.recent_activity] == [
        "Started Source evaluator"
    ]


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("source_evaluator", "Source evaluator"),
        ("custom_agent", "Custom agent"),
        (None, "Unknown agent"),
    ],
)
def test_display_agent_name_humanizes_identifiers(
    identifier: str | None,
    expected: str,
) -> None:
    assert display_agent_name(identifier) == expected


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("web_search", "Web search"),
        ("web_scraper", "Web scraper"),
        ("query_memory", "Memory lookup"),
        ("custom_tool", "Custom tool"),
        ("", "Unknown tool"),
    ],
)
def test_display_tool_name_uses_plain_language_labels(
    identifier: str,
    expected: str,
) -> None:
    assert display_tool_name(identifier) == expected
    assert "_" not in display_tool_name(identifier)


@pytest.mark.parametrize(
    ("source_record", "expected"),
    [
        (source("https://example.org/high", overall_score=0.75), "high"),
        (source("https://example.org/moderate", overall_score=0.74), "moderate"),
        (
            source("https://example.org/low", overall_score=0.9, low_confidence=True),
            "low",
        ),
        (
            source(
                "https://example.org/unrated",
                overall_score=0.0,
                recency_score=0.0,
                relevance_score=0.0,
                low_confidence=True,
            ),
            "unrated",
        ),
    ],
)
def test_credibility_tier_follows_the_four_defined_rules(
    source_record: ScoredSource,
    expected: str,
) -> None:
    assert credibility_tier(source_record) == expected


def test_source_summary_groups_related_topics_by_normalized_url() -> None:
    sources = [source("https://example.org/research")]
    findings = [
        finding("https://www.example.org/research/", "Market size"),
        finding("https://example.org/research", "Regulatory drivers"),
        finding("https://example.org/research", "Market size"),
    ]

    summary = source_summary(sources, findings)

    assert summary.total == 1
    assert summary.high == 1
    assert summary.details[0].related_sub_topics == [
        "Market size",
        "Regulatory drivers",
    ]


def test_source_summary_deduplicates_refinement_passes_and_keeps_latest_record(
) -> None:
    first = source(
        "https://www.example.org/research/",
        overall_score=0.9,
    )
    latest = source(
        "https://example.org/research",
        overall_score=0.4,
    ).model_copy(update={"rationale": "Latest refinement rationale."})

    summary = source_summary([first, latest], [])

    assert summary.total == 1
    assert summary.high == 0
    assert summary.low == 0
    assert summary.moderate == 1
    assert summary.details[0].rationale == "Latest refinement rationale."


def test_fact_check_summary_deduplicates_refinement_passes_and_keeps_latest_record(
) -> None:
    first = Claim(
        text="The deployment target is achievable.",
        source_urls=["https://www.example.org/research/"],
        verdict="verified",
        confidence=0.9,
        evidence=["First pass evidence."],
        contradictions=[],
    )
    latest = first.model_copy(
        update={
            "verdict": "contradicted",
            "confidence": 0.4,
            "evidence": ["Latest pass evidence."],
            "contradictions": ["Latest pass contradiction."],
        }
    )

    summary = fact_check_summary([first, latest])

    assert summary.verified == 0
    assert summary.contradicted == 1
    assert summary.details[0].verdict == "contradicted"
    assert summary.details[0].evidence == ["Latest pass evidence."]


@pytest.mark.parametrize(
    "verdict",
    ["verified", "unverified", "contradicted", "insufficient_evidence"],
)
def test_fact_check_summary_counts_all_claim_verdicts(verdict: str) -> None:
    summary = fact_check_summary([claim(verdict)])

    assert getattr(summary, verdict) == 1
    assert sum(
        getattr(summary, name)
        for name in (
            "verified",
            "unverified",
            "contradicted",
            "insufficient_evidence",
        )
    ) == 1
    assert summary.details[0].text == f"Typed {verdict} claim."


def test_projectors_use_structured_data_instead_of_report_markdown() -> None:
    research_outcome = outcome(
        report="SECRET REPORT TEXT with fake source and fake claim",
    )
    sources = [source("https://example.org/typed")]
    findings = [finding("https://example.org/typed", "Typed topic")]

    source_result = source_summary(sources, findings)
    fact_result = fact_check_summary([claim("verified")])

    assert research_outcome.report is not None
    assert source_result.details[0].related_sub_topics == ["Typed topic"]
    assert "SECRET REPORT" not in source_result.model_dump_json()
    assert "fake source" not in source_result.model_dump_json()
    assert fact_result.details[0].text == "Typed verified claim."
    assert "SECRET REPORT" not in fact_result.model_dump_json()


def test_token_usage_returns_none_for_zero_usage_and_value_for_nonzero() -> None:
    assert token_usage_from_outcome(outcome()) is None
    usage = token_usage_from_outcome(outcome(input_tokens=12, output_tokens=8))

    assert usage is not None
    assert usage.model_dump() == {"input_tokens": 12, "output_tokens": 8}


def test_limitations_deduplicate_critic_gaps_and_unsupported_claims() -> None:
    research_outcome = outcome(
        critique=Critique(
            score=5,
            gaps=["No cost data.", "No cost data."],
            unsupported_claims=["Unproven scale.", "No cost data."],
            recommended_queries=[],
            should_continue=True,
            rationale="Typed rationale.",
        )
    )
    research_outcome = replace(
        research_outcome,
        state=research_outcome.state.model_copy(
            update={
                "errors": [
                    ResearchError(
                        error_type="provider_error",
                        source="researcher",
                        message="Separate error.",
                    )
                ]
            }
        ),
    )

    assert limitations_from_outcome(research_outcome) == [
        "No cost data.",
        "Unproven scale.",
    ]
    assert "Separate error." not in limitations_from_outcome(research_outcome)
