"""Tests for the Researcher's contracts, selection, and prompt helpers."""

from __future__ import annotations

from deep_research.agents.researcher import (
    DEFAULT_MAX_SUB_TOPICS,
    HIGH_PRIORITY_THRESHOLD,
    FindingDraft,
    SubTopicFindingsDraft,
    SubTopicTask,
    build_findings,
    existing_sources_for,
    extraction_messages,
    is_high_priority,
    merge_react_runs,
    render_evidence,
    render_session_guidance,
    render_sub_topic_guidance,
    select_sub_topics,
)
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchState,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _sub_topic(title: str, priority: int = 1) -> SubTopic:
    return SubTopic(
        title=title,
        rationale=f"{title} matters.",
        search_queries=[f"{title} 2025"],
        success_criteria=[f"A named source about {title}."],
        priority=priority,
    )


def _finding(sub_topic: str, url: str) -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _state(
    *,
    sub_topics: list[SubTopic] | None = None,
    raw_findings: list[Finding] | None = None,
    critique: Critique | None = None,
    memory_context: MemorySnapshot | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=sub_topics or [],
        raw_findings=raw_findings or [],
        critique=critique,
        memory_context=memory_context or MemorySnapshot(),
    )


def _critique(**overrides: object) -> Critique:
    payload: dict[str, object] = {
        "score": 4,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": True,
        "rationale": "Coverage is thin.",
    }
    payload.update(overrides)
    return Critique.model_validate(payload)


def _tool_step(
    iteration: int,
    tool_name: str,
    data: object,
    *,
    success: bool = True,
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=f"Call {tool_name}.",
        action="use_tool",
        tool_name=tool_name,
        observation=ReActObservation(
            tool_name=tool_name,
            success=success,
            summary=f"{tool_name} {'succeeded' if success else 'failed'}",
        ),
        tool_result=(
            ToolResult(
                tool_name=tool_name,
                success=True,
                data=data,
                latency_ms=1.0,
            )
            if success
            else ToolResult(
                tool_name=tool_name,
                success=False,
                error={"type": "TimeoutError", "message": "upstream timed out"},
                latency_ms=1.0,
            )
        ),
    )


def test_priority_and_selection_defaults_match_the_plan() -> None:
    assert HIGH_PRIORITY_THRESHOLD == 2
    assert DEFAULT_MAX_SUB_TOPICS == 3


def test_selection_orders_by_priority_and_caps_the_count() -> None:
    state = _state(
        sub_topics=[
            _sub_topic("Gamma", 3),
            _sub_topic("Alpha", 1),
            _sub_topic("Beta", 2),
            _sub_topic("Delta", 4),
        ]
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Alpha", "Beta"]


def test_selection_puts_critic_flagged_gaps_first(
) -> None:
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 5)],
        critique=_critique(gaps=["No evidence at all on   BETA yet."]),
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Beta", "Alpha"]


def test_selection_falls_back_to_priority_when_no_gap_matches() -> None:
    state = _state(
        sub_topics=[_sub_topic("Alpha", 2), _sub_topic("Beta", 1)],
        critique=_critique(gaps=["Something unrelated."]),
    )

    selected = select_sub_topics(state)

    assert [sub_topic.title for sub_topic in selected] == ["Beta", "Alpha"]


def test_high_priority_is_a_threshold_on_the_priority_value() -> None:
    assert is_high_priority(_sub_topic("Alpha", 1)) is True
    assert is_high_priority(_sub_topic("Beta", 2)) is True
    assert is_high_priority(_sub_topic("Gamma", 3)) is False
    assert is_high_priority(_sub_topic("Gamma", 3), threshold=3) is True


def test_existing_sources_are_scoped_to_the_sub_topic_and_deduplicated() -> None:
    state = _state(
        raw_findings=[
            _finding("Alpha", "https://example.test/one"),
            _finding("Alpha", "https://example.test/one"),
            _finding("Beta", "https://example.test/two"),
        ]
    )

    assert existing_sources_for(state, _sub_topic("Alpha")) == [
        "https://example.test/one"
    ]


def test_merging_runs_sums_the_counts_and_keeps_every_error() -> None:
    error = ResearchError(
        error_type="tool_failure",
        source="agent.researcher",
        message="web_search timed out",
        recoverable=True,
    )
    first = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        iterations=2,
        tool_calls=1,
        final_answer="First answer.",
        errors=[error],
    )
    second = ReActRun(
        agent_name="researcher",
        stop_reason="max_iterations",
        iterations=3,
        tool_calls=2,
    )

    merged = merge_react_runs("researcher", [first, second])

    assert merged.iterations == 5
    assert merged.tool_calls == 3
    assert merged.stop_reason == "max_iterations"
    assert merged.final_answer == "First answer."
    assert merged.errors == [error]


def test_merging_runs_joins_every_final_answer_in_order() -> None:
    first = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        final_answer="ANSWER-A",
    )
    second = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        final_answer="ANSWER-B",
    )

    merged = merge_react_runs("researcher", [first, second])

    assert merged.final_answer == "ANSWER-A\n\nANSWER-B"


def test_merging_runs_surfaces_a_provider_failure() -> None:
    runs = [
        ReActRun(agent_name="researcher", stop_reason="provider_error"),
        ReActRun(agent_name="researcher", stop_reason="finished"),
    ]

    assert merge_react_runs("researcher", runs).stop_reason == "provider_error"


def test_merging_runs_does_not_let_a_later_finish_hide_an_exhausted_budget() -> (
    None
):
    runs = [
        ReActRun(agent_name="researcher", stop_reason="max_iterations"),
        ReActRun(agent_name="researcher", stop_reason="finished"),
    ]

    assert merge_react_runs("researcher", runs).stop_reason == "max_iterations"


def test_merging_no_runs_yields_an_empty_finished_run() -> None:
    merged = merge_react_runs("researcher", [])

    assert merged.stop_reason == "finished"
    assert merged.iterations == 0
    assert merged.steps == []


def test_session_guidance_leads_with_the_critic_request() -> None:
    guidance = render_session_guidance(
        _state(
            critique=_critique(
                gaps=["No post-2024 hardware data."],
                recommended_queries=["surface code threshold 2025"],
                unsupported_claims=["Error rates halved."],
            ),
            memory_context=MemorySnapshot(
                suggested_strategies=["Prefer peer-reviewed sources."]
            ),
        )
    )

    assert guidance.startswith("The critic asked for another research pass.")
    assert "- No post-2024 hardware data." in guidance
    assert "Run these recommended queries first:" in guidance
    assert "- surface code threshold 2025" in guidance
    assert "- Error rates halved." in guidance
    assert "- Prefer peer-reviewed sources." in guidance


def test_session_guidance_is_empty_without_a_critique_or_memory() -> None:
    assert render_session_guidance(_state()) == ""


def test_sub_topic_guidance_lists_queries_criteria_and_known_sources() -> None:
    guidance = render_sub_topic_guidance(
        _sub_topic("Alpha", 2), ["https://example.test/one"]
    )

    assert "Sub-topic: Alpha" in guidance
    assert "Priority: 2 (1 is most important)" in guidance
    assert "- Alpha 2025" in guidance
    assert "- A named source about Alpha." in guidance
    assert "do not repeat them:" in guidance
    assert "- https://example.test/one" in guidance


def test_sub_topic_guidance_omits_known_sources_when_there_are_none() -> None:
    guidance = render_sub_topic_guidance(_sub_topic("Alpha"), [])

    assert "do not repeat them:" not in guidance


def test_evidence_renders_only_successful_tool_payloads() -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", {"results": ["a"]}),
            _tool_step(2, "web_scraper", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    evidence = render_evidence(run, limit=200)

    assert evidence == '- [web_search] {"results": ["a"]}'


def test_evidence_reports_when_nothing_was_retrieved() -> None:
    run = ReActRun(agent_name="researcher", stop_reason="max_iterations")

    assert render_evidence(run, limit=200) == "(no evidence retrieved)"


def test_evidence_is_clamped_to_the_configured_budget() -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_scraper", {"text": "x" * 500})],
        iterations=1,
        tool_calls=1,
    )

    line = render_evidence(run, limit=40)

    assert line.endswith("...")
    assert len(line) == len("- [web_scraper] ") + 40


def test_extraction_messages_carry_the_sub_topic_criteria_and_evidence() -> None:
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.",
        sub_topic=_sub_topic("Alpha"),
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_search", {"results": ["a"]})],
        iterations=1,
        tool_calls=1,
    )

    messages = extraction_messages(task, run, evidence_chars=200)

    assert messages[0].role == "developer"
    body = messages[1].content
    assert "## Sub-topic\nAlpha" in body
    assert "- A named source about Alpha." in body
    assert '- [web_search] {"results": ["a"]}' in body


def test_drafts_are_stamped_with_the_sub_topic_and_extraction_time() -> None:
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Logical error rates fell below break-even.",
                source_url="https://example.test/qec",
                source_title="QEC 2025",
                confidence=0.8,
            )
        ]
    )

    findings, rejected = build_findings(
        draft, sub_topic=_sub_topic("Alpha"), extracted_at=EXTRACTED_AT
    )

    assert rejected == []
    assert findings[0].related_sub_topic == "Alpha"
    assert findings[0].extracted_at == EXTRACTED_AT
    assert findings[0].confidence == 0.8


def test_malformed_drafts_are_dropped_and_named_by_field() -> None:
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Fine.",
                source_url="https://example.test/qec",
                source_title="QEC 2025",
                confidence=1.5,
            ),
            FindingDraft(
                content="Also fine.",
                source_url="https://example.test/qec",
                source_title="QEC 2025",
                confidence=0.5,
            ),
        ]
    )

    findings, rejected = build_findings(
        draft, sub_topic=_sub_topic("Alpha"), extracted_at=EXTRACTED_AT
    )

    assert [finding.content for finding in findings] == ["Also fine."]
    assert rejected == ["finding 1: invalid confidence"]
