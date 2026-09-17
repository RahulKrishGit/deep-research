"""Tests for the Researcher's contracts, selection, and prompt helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.evidence import build_read_record
from deep_research.agents.prompts import AgentTask
from deep_research.agents.researcher import (
    DEFAULT_MAX_SUB_TOPICS,
    HIGH_PRIORITY_THRESHOLD,
    MAX_FINDINGS_PER_SUB_TOPIC,
    MAX_UNIQUE_SOURCES_PER_SUB_TOPIC,
    FindingDraft,
    ResearcherAgent,
    ResearchFindings,
    SubTopicFindingsDraft,
    SubTopicTask,
    bound_sub_topic_findings,
    build_findings,
    existing_sources_for,
    extraction_messages,
    is_high_priority,
    merge_react_runs,
    render_evidence,
    render_session_guidance,
    render_sub_topic_guidance,
    retrieved_finding_urls,
    select_sub_topics,
)
from deep_research.agents.steps import (
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    read_evidence_urls,
    summarize_text,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Critique,
    CritiqueGap,
    Finding,
    MemorySnapshot,
    ReadRecord,
    ResearchError,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    QEC_PASSAGE,
    FakeMemory,
    FakeSearchClient,
    qec_read_record,
    research_tools,
    search_response,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _sub_topic(
    title: str, priority: int = 1, coverage_id: str = "topic-01"
) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale=f"{title} matters.",
        search_queries=[f"{title} 2025"],
        success_criteria=[f"A named source about {title}."],
        priority=priority,
    )


def _finding(
    sub_topic: str,
    url: str,
    *,
    content: str = "Logical error rates fell below break-even.",
    confidence: float = 0.8,
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=confidence,
        related_sub_topic=sub_topic,
    )


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
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


def _gap(
    problem: str,
    *,
    coverage_id: str | None = None,
    recommended_queries: list[str] | None = None,
) -> CritiqueGap:
    """One targetable Critic gap. The plan ID is the only routing signal."""
    return CritiqueGap(
        coverage_id=coverage_id,
        problem=problem,
        recommended_queries=recommended_queries or [],
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


# A search payload is discovery only: it names candidates this loop has not
# read, so it can never make a finding survive extraction however relevant it
# looks. It stays in the transcript, and out of the allow-list.
QEC_EVIDENCE = {
    "results": [
        {
            "url": "https://example.test/qec",
            "title": "QEC 2025",
            "content": "Logical error rates fell below break-even.",
        }
    ]
}

# What a finding has to come from instead: a page this loop actually READ, at
# the URL the draft cites.
QEC_SCRAPE = {
    "url": "https://example.test/qec",
    "text": "Logical error rates fell below break-even in 2025.",
}

# The read registry's own record of that page: the scraper's title, its single
# extracted chunk, and the read id the registry derives from them. The
# extraction contract requires those registry fields on the acquisition path,
# so the scripted provider output has to carry the real ones — a draft naming
# no admitted read is now dropped instead of admitted on its URL alone.
QEC_READ = qec_read_record()

_FINDING_EXAMPLE_OUTPUT = (
    "Example JSON output:\n"
    '{"findings":[{"confidence":0.8,"content":"The example report measured a '
    '12 percent reduction.","excerpt":"The measured reduction was 12 '
    'percent.","locator":"page-4-chunk-0","read_id":'
    '"read-111111111111111111111111","source_title":"Example report",'
    '"source_url":"https://evidence.example.test/report","target_ids":'
    '["target-01"]}]}'
)


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
    assert DEFAULT_MAX_SUB_TOPICS == 7
    assert MAX_FINDINGS_PER_SUB_TOPIC == 6
    assert MAX_UNIQUE_SOURCES_PER_SUB_TOPIC == 4


def test_the_default_cap_attempts_the_whole_planner_output() -> None:
    """One pass attempts every sub-topic the Planner is allowed to produce.

    A cap below the planner's own maximum silently truncates a plan the
    Planner was told to produce, and the truncated topics never get a turn.
    """
    from deep_research.agents.planner import MAX_SUB_TOPICS

    assert DEFAULT_MAX_SUB_TOPICS == MAX_SUB_TOPICS


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
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 5, coverage_id="topic-02"),
        ],
        critique=_critique(
            gaps=[
                _gap(
                    "No evidence at all on Beta yet.",
                    coverage_id="topic-02",
                )
            ]
        ),
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Beta", "Alpha"]


def test_selection_falls_back_to_priority_when_no_gap_matches() -> None:
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 2, coverage_id="topic-01"),
            _sub_topic("Beta", 1, coverage_id="topic-02"),
        ],
        critique=_critique(gaps=[_gap("Something unrelated.")]),
    )

    selected = select_sub_topics(state)

    assert [sub_topic.title for sub_topic in selected] == ["Beta", "Alpha"]


def test_a_gap_problem_naming_a_title_never_targets_that_topic() -> None:
    """Routing is by plan ID only; a title inside the prose decides nothing."""
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 5, coverage_id="topic-02"),
        ],
        critique=_critique(
            gaps=[_gap("Beta is completely uncovered.", coverage_id=None)]
        ),
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Alpha", "Beta"]


def test_a_gap_with_an_unknown_plan_id_targets_no_topic() -> None:
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 5, coverage_id="topic-02"),
        ],
        critique=_critique(
            gaps=[_gap("Beta is uncovered.", coverage_id="topic-999")]
        ),
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Alpha", "Beta"]


def test_refinement_selection_uses_one_slot_for_unsatisfied_topic() -> None:
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 3)],
        raw_findings=[_finding(" alpha ", "https://example.test/alpha")],
        critique=_critique(),
    )

    selected = select_sub_topics(state, max_sub_topics=1)

    assert [sub_topic.title for sub_topic in selected] == ["Beta"]


def test_refinement_gap_target_is_selected_even_when_prior_findings_exist() -> None:
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 3, coverage_id="topic-02"),
        ],
        raw_findings=[_finding("Alpha", "https://example.test/alpha")],
        critique=_critique(
            gaps=[_gap("Alpha still has a critic gap.", coverage_id="topic-01")]
        ),
    )

    selected = select_sub_topics(state, max_sub_topics=1)

    assert [sub_topic.title for sub_topic in selected] == ["Alpha"]


def test_initial_selection_keeps_topics_with_prior_findings() -> None:
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 3)],
        raw_findings=[_finding("Alpha", "https://example.test/alpha")],
    )

    selected = select_sub_topics(state, max_sub_topics=2)

    assert [sub_topic.title for sub_topic in selected] == ["Alpha", "Beta"]


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


def test_session_guidance_reports_every_gap_problem() -> None:
    guidance = render_session_guidance(
        _state(
            critique=_critique(
                gaps=[
                    _gap("Alpha lacks cost evidence.", coverage_id="topic-01"),
                    _gap(
                        "Beta lacks durability evidence.",
                        coverage_id="topic-02",
                    ),
                ]
            )
        )
    )

    assert "- Alpha lacks cost evidence." in guidance
    assert "- Beta lacks durability evidence." in guidance


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


def test_sub_topic_guidance_runs_the_critic_queries_before_the_planned_ones() -> None:
    guidance = render_sub_topic_guidance(
        _sub_topic("Alpha"),
        [],
        prioritized_queries=["alpha cost 2025", "  alpha cost 2026  "],
    )

    assert "Run these queries first:" in guidance
    # The Critic's own queries come before the planner's, and a duplicate of
    # one of them is not repeated below it.
    assert guidance.index("- alpha cost 2025") < guidance.index("- Alpha 2025")
    assert guidance.count("- alpha cost 2025") == 1
    assert "- alpha cost 2026" in guidance
    # The success criteria still say when the sub-topic is done.
    assert "This sub-topic is done when:" in guidance
    assert "- A named source about Alpha." in guidance


def test_sub_topic_guidance_drops_a_critic_query_the_planner_already_lists() -> None:
    guidance = render_sub_topic_guidance(
        _sub_topic("Alpha"),
        [],
        prioritized_queries=["Alpha 2025"],
    )

    assert guidance.count("- Alpha 2025") == 1


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


@pytest.mark.parametrize(
    ("tool_name", "data", "expected"),
    [
        (
            "web_scraper",
            {"url": "https://b.test/two", "text": "body"},
            ("https://b.test/two",),
        ),
        # The body came from the URL the transport actually resolved to, so
        # that is the source a finding from it may cite.
        (
            "web_scraper",
            {
                "url": "https://b.test/asked",
                "resolved_url": "https://mirror.test/two",
                "text": "body",
            },
            ("https://mirror.test/two",),
        ),
        (
            "document_reader",
            {"source": "https://c.test/three.pdf", "chunks": ["chunk"]},
            ("https://c.test/three.pdf",),
        ),
        (
            "document_reader",
            {
                "source": "https://c.test/asked.pdf",
                "resolved_source": "https://cdn.test/three.pdf",
                "chunks": ["chunk"],
            },
            ("https://cdn.test/three.pdf",),
        ),
    ],
)
def test_retrieved_finding_urls_reads_every_read_bearing_tool(
    tool_name: str, data: object, expected: tuple[str, ...]
) -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, tool_name, data)],
        iterations=1,
        tool_calls=1,
    )

    assert retrieved_finding_urls(run) == expected


@pytest.mark.parametrize(
    ("tool_name", "data", "success"),
    [
        # A search that returned five results read none of them: every hit is a
        # candidate, and the loop has not opened a single page.
        (
            "web_search",
            {
                "results": [
                    {"title": f"Hit {index}", "url": f"https://s.test/{index}"}
                    for index in range(5)
                ]
            },
            True,
        ),
        # A failed call retrieved nothing, whatever its payload claims.
        ("web_search", {"results": [{"url": "https://a.test/one"}]}, False),
        ("web_scraper", QEC_SCRAPE, False),
        # Empty payloads carry no evidence.
        ("web_search", {"results": []}, True),
        ("web_scraper", {"url": "https://b.test/two", "text": "   "}, True),
        (
            "document_reader",
            {"source": "https://c.test/three.pdf", "chunks": []},
            True,
        ),
        ("query_memory", {"matches": []}, True),
        # A memory match with no readable content was never read either.
        ("query_memory", {"matches": [{"source_url": "https://g.test/seven"}]}, True),
        # Nor is a memory match that carries text, a source URL (direct or
        # nested), high confidence, and a previous "verified" label. Recall is
        # discovery guidance; only a same-run read or a validated cache entry
        # is evidence.
        (
            "query_memory",
            {
                "matches": [
                    {
                        "content": "A previous generated answer says 10 GW.",
                        "source_url": "https://g.test/remembered",
                        "confidence": 0.99,
                        "verified": True,
                    }
                ]
            },
            True,
        ),
        (
            "query_memory",
            {
                "matches": [
                    {
                        "content": "A previous generated answer says 10 GW.",
                        "metadata": {
                            "source_url": "https://g.test/remembered",
                            "verified": True,
                        },
                    }
                ]
            },
            True,
        ),
        # A write is never evidence.
        ("save_to_memory", {"entry_id": "1", "url": "https://f.test/six"}, True),
        # Malformed entries contribute nothing rather than raising.
        ("web_search", {"results": ["not-a-dict", {"no_url": True}]}, True),
        ("query_memory", {"matches": [{"metadata": "not-a-dict"}]}, True),
        ("document_reader", {"source": 17, "chunks": ["chunk"]}, True),
    ],
)
def test_retrieved_finding_urls_excludes_everything_that_is_not_evidence(
    tool_name: str, data: object, success: bool
) -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, tool_name, data, success=success)],
        iterations=1,
        tool_calls=1,
    )

    assert retrieved_finding_urls(run) == ()


def test_retrieved_finding_urls_deduplicates_after_normalizing() -> None:
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "document_reader",
                {"source": "HTTPS://A.test/one/", "chunks": ["chunk"]},
            ),
            _tool_step(
                2,
                "web_scraper",
                {"url": "https://a.test/one", "text": "body"},
            ),
        ],
        iterations=2,
        tool_calls=2,
    )

    assert retrieved_finding_urls(run) == ("https://a.test/one",)


def _search_only_run() -> ReActRun:
    """One successful search carrying five hits, and no page ever read."""
    return ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {
                    "results": [
                        {
                            "title": f"QEC {index}",
                            "url": f"https://candidate.test/{index}",
                            "content": "A snippet, not a page.",
                        }
                        for index in range(5)
                    ]
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )


def _loop_read_anything(run: ReActRun) -> bool:
    """The question ``extract_findings`` asks before it extracts.

    Recomputed here from the shared classifier rather than imported from the
    agent, because the point of the assertion is that the agent's own gate and
    this one cannot disagree.
    """
    return any(read_evidence_urls(step) for step in run.steps)


def test_a_search_only_loop_read_nothing_and_retrieves_no_source() -> None:
    run = _search_only_run()

    assert retrieved_finding_urls(run) == ()
    assert _loop_read_anything(run) is False


def test_the_example_url_is_not_retrieved_by_any_real_tool_shape() -> None:
    """The prompt example's URL must never clear the allow-list on its own.

    Search is the shape that could smuggle it in: the example is a *result*
    in the prompt, and no read-bearing tool ever reports it.
    """
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_search", QEC_EVIDENCE)],
        iterations=1,
        tool_calls=1,
    )

    assert retrieved_finding_urls(run) == ()
    assert "https://evidence.example.test/report" not in retrieved_finding_urls(run)


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


def test_duplicate_findings_are_folded_before_the_cap() -> None:
    """One claim, one page, one sub-topic: one finding, at its best confidence."""
    findings = [
        _finding("Alpha", "https://a.test/one", content="One.", confidence=0.5),
        _finding("Alpha", "https://a.test/one", content="One.", confidence=0.9),
        _finding("Alpha", "https://a.test/one", content="One.", confidence=0.6),
    ]

    budget = bound_sub_topic_findings(findings)

    assert [finding.confidence for finding in budget.retained] == [0.9]
    assert budget.dropped_duplicate == 2
    assert budget.dropped_cap == 0
    assert budget.sources_retained == 1


def test_the_cap_keeps_the_six_most_confident_findings() -> None:
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Claim {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.1, 0.8, 0.3, 0.6, 0.2, 0.7, 0.5, 0.4], start=1
        )
    ]

    budget = bound_sub_topic_findings(findings)

    assert [finding.confidence for finding in budget.retained] == [
        0.8,
        0.7,
        0.6,
        0.5,
        0.4,
        0.3,
    ]
    assert budget.dropped_duplicate == 0
    assert budget.dropped_cap == 2
    assert budget.sources_retained == 1


def test_the_cap_keeps_at_most_four_distinct_sources() -> None:
    findings = [
        _finding(
            "Alpha",
            f"https://s{index}.test/one",
            content=f"Claim {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.9, 0.8, 0.7, 0.6, 0.5, 0.4], start=1
        )
    ]

    budget = bound_sub_topic_findings(findings)

    assert {finding.source_url for finding in budget.retained} == {
        "https://s1.test/one",
        "https://s2.test/one",
        "https://s3.test/one",
        "https://s4.test/one",
    }
    assert budget.dropped_cap == 2
    assert budget.sources_retained == 4


def test_a_second_source_keeps_a_slot_confidence_alone_would_fill() -> None:
    """Bounding evidence must not narrow it to one publisher.

    Six findings from one page and one from an independent one: a pure
    confidence ranking would keep six of the seven and drop the single
    independent source, which is the one a reader most needs to see.
    """
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Claim {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.98, 0.96, 0.94, 0.92, 0.90, 0.88], start=1
        )
    ] + [
        _finding(
            "Alpha", "https://b.test/two", content="Independent.", confidence=0.5
        )
    ]

    budget = bound_sub_topic_findings(findings)

    assert [finding.source_url for finding in budget.retained] == [
        "https://a.test/one",
        "https://a.test/one",
        "https://a.test/one",
        "https://a.test/one",
        "https://a.test/one",
        "https://b.test/two",
    ]
    assert [finding.confidence for finding in budget.retained] == [
        0.98,
        0.96,
        0.94,
        0.92,
        0.90,
        0.5,
    ]
    assert budget.dropped_cap == 1
    assert budget.sources_retained == 2


def test_extraction_messages_carry_the_sub_topic_criteria_and_evidence() -> None:
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.",
        sub_topic=_sub_topic("Alpha"),
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", {"results": [{"title": "T", "url": "https://t.test/a"}]}),
            _tool_step(2, "web_scraper", QEC_SCRAPE),
        ],
        iterations=2,
        tool_calls=2,
    )

    messages = extraction_messages(task, run, evidence_chars=200)

    assert messages[0].role == "developer"
    body = messages[1].content
    assert "# Sub-topic\nAlpha" in body
    assert "- A named source about Alpha." in body
    assert '- [web_scraper] {"url": "https://example.test/qec"' in body
    assert body.rstrip().endswith(_FINDING_EXAMPLE_OUTPUT)


def test_extraction_evidence_keeps_search_payloads_out_of_the_evidence_block() -> None:
    """A search hit is a candidate, and the block must say so.

    The transcript keeps the whole search payload for discovery and debugging;
    the extraction call sees only candidate title/URL metadata, so a snippet
    cannot be read as if the loop had opened the page.
    """
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.",
        sub_topic=_sub_topic("Alpha"),
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_search", QEC_EVIDENCE)],
        iterations=1,
        tool_calls=1,
    )

    body = extraction_messages(task, run, evidence_chars=200)[1].content

    assert "- [web_search]" not in body
    assert "Logical error rates fell below break-even." not in body
    assert "- QEC 2025: https://example.test/qec" in body

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
        draft,
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
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
        draft,
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
    )

    assert [finding.content for finding in findings] == ["Also fine."]
    assert rejected == ["finding 1: invalid confidence"]


def test_a_finding_citing_an_unretrieved_url_is_dropped_and_named() -> None:
    """Provenance, not syntax: the exact failure a copied prompt example causes.

    ``build_findings`` previously checked only whether a URL was well formed,
    so the synthetic URL from a reply-format example would have entered
    research state as though it had been retrieved.
    """
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Copied from the example.",
                source_url="https://evidence.example.test/report",
                source_title="Example report",
                confidence=0.9,
            ),
            FindingDraft(
                content="Actually retrieved.",
                source_url="https://example.test/qec",
                source_title="QEC 2025",
                confidence=0.7,
            ),
        ]
    )

    findings, rejected = build_findings(
        draft,
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
    )

    assert [finding.content for finding in findings] == ["Actually retrieved."]
    assert rejected == ["finding 1: source url was not retrieved"]


def _registry_draft(**overrides: object) -> SubTopicFindingsDraft:
    """One finding in the acquisition path's required registry shape."""
    values: dict[str, object] = {
        "content": "Logical error rates fell below break-even.",
        "source_url": "https://example.test/qec",
        "source_title": QEC_READ.title,
        "confidence": 0.8,
        "read_id": QEC_READ.read_id,
        "locator": "chunk-0",
        "excerpt": QEC_PASSAGE,
        "target_ids": ["topic-01"],
    }
    values.update(overrides)
    return SubTopicFindingsDraft(findings=[FindingDraft(**values)])


def _build_admitted(
    draft: SubTopicFindingsDraft,
) -> tuple[list[Finding], list[str]]:
    return build_findings(
        draft,
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
        known_reads={QEC_READ.read_id: QEC_READ},
        target_id="topic-01",
    )


def test_the_acquisition_path_requires_the_registry_fields() -> None:
    """A finding that names no admitted read cannot be checked, so it is out."""
    findings, rejected = _build_admitted(_registry_draft(read_id=None))

    assert findings == []
    assert rejected == [
        "finding 1: the acquisition path requires an admitted read id"
    ]


def test_an_unadmitted_read_id_is_dropped() -> None:
    findings, rejected = _build_admitted(
        _registry_draft(read_id="read-ffffffffffffffffffffffff")
    )

    assert findings == []
    assert rejected == ["finding 1: read id was not admitted"]


def test_a_finding_naming_another_reads_url_or_title_is_dropped() -> None:
    """The provider does not get to relabel the read it copied from."""
    other_url, rejected_url = _build_admitted(
        _registry_draft(source_url="https://elsewhere.example/report")
    )
    assert other_url == []
    assert rejected_url == ["finding 1: source url did not match read"]

    other_title, rejected_title = _build_admitted(
        _registry_draft(source_title="Something the model preferred")
    )
    assert other_title == []
    assert rejected_title == ["finding 1: source title did not match read"]


def test_an_excerpt_the_locator_does_not_contain_is_dropped() -> None:
    """An altered number or a paraphrase is not source text."""
    findings, rejected = _build_admitted(
        _registry_draft(excerpt="Logical error rates fell below 0.1 percent.")
    )

    assert findings == []
    assert rejected == ["finding 1: excerpt was not admitted at locator"]


def test_a_finding_missing_its_target_id_is_dropped() -> None:
    findings, rejected = _build_admitted(_registry_draft(target_ids=[]))

    assert findings == []
    assert rejected == ["finding 1: target id was not admitted"]


def test_a_registry_shaped_finding_is_still_admitted() -> None:
    """The enforcement is a contract, not a wall: the right shape passes."""
    findings, rejected = _build_admitted(_registry_draft())

    assert rejected == []
    assert [finding.content for finding in findings] == [
        "Logical error rates fell below break-even."
    ]
    assert findings[0].source_title == QEC_READ.title


def test_extraction_messages_require_the_registry_shape() -> None:
    """The acquisition prompt demonstrates the shape it will accept."""
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.",
        sub_topic=_sub_topic("Alpha"),
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_scraper", QEC_SCRAPE)],
        iterations=1,
        tool_calls=1,
    )

    acquisition_body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
    )[1].content
    legacy_body = extraction_messages(task, run, evidence_chars=200)[1].content

    assert "MUST copy the read_id, locator, and excerpt" in acquisition_body
    assert '"read_id":"read-111111111111111111111111"' in acquisition_body
    assert '"locator":"page-4-chunk-0"' in acquisition_body
    # The conditional phrasing that let the model skip the registry is gone.
    assert "When a read_id, locator, and excerpt are present" not in acquisition_body
    # The legacy URL/title path keeps its own contract.
    assert "MUST copy the read_id" not in legacy_body


def _clock() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _researcher(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    http: httpx.AsyncClient | None = None,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
    config: AgentRuntimeConfig | None = None,
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(tracker, search=search, memory=memory, http=http),
        config=config or AgentRuntimeConfig(max_iterations=4, tool_budget=4),
        max_sub_topics=max_sub_topics,
        clock=_clock,
    )


def test_the_researcher_declares_its_identity_and_tools() -> None:
    """Four read/discovery tools and no writer.

    The Researcher gathers evidence; nothing it holds has been evaluated yet,
    so it must not be able to keep a finding in long-term memory before the
    pass that validates it has run.
    """
    assert ResearcherAgent.name == "researcher"
    assert ResearcherAgent.allowed_tools == (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    )


def test_the_researcher_prompt_requires_reading_and_prefers_primary_sources(
    tracker: Tracker,
) -> None:
    agent = _researcher(tracker, ScriptedCompleter())

    prompt = agent.system_prompt(AgentTask(instruction="Gather evidence."))

    assert "Read a source before reporting a finding from it." in prompt
    assert "Prefer primary sources" in prompt
    assert "Record publication date and geographic applicability" in prompt
    assert "save_to_memory" not in prompt


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_sub_topics", 0),
        ("high_priority_threshold", 0),
        ("evidence_chars", 0),
    ],
)
def test_the_researcher_rejects_unbounded_construction(
    tracker: Tracker, field_name: str, invalid_value: int
) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ResearcherAgent(
            provider=ScriptedCompleter(),
            tracker=tracker,
            scratchpad=ScratchpadMemory(
                session_id="session-1", agent_name="researcher"
            ),
            tools=research_tools(tracker),
            **{field_name: invalid_value},
        )


def test_the_researcher_rejects_a_naive_clock(tracker: Tracker) -> None:
    """Fail loudly at construction, not with mysteriously empty findings.

    A caller who passes ``clock=datetime.now`` without ``tz=`` would
    otherwise only discover the mistake later, when every ``build_findings``
    call fails validation and the resulting error misleadingly blames the
    extraction model instead of the constructor misconfiguration.
    """
    with pytest.raises(AgentConfigurationError, match="timezone-aware"):
        ResearcherAgent(
            provider=ScriptedCompleter(),
            tracker=tracker,
            scratchpad=ScratchpadMemory(
                session_id="session-1", agent_name="researcher"
            ),
            tools=research_tools(tracker),
            clock=lambda: datetime.now(),
        )


def test_the_sub_topic_task_merges_session_and_sub_topic_guidance(
    tracker: Tracker,
) -> None:
    agent = _researcher(tracker, ScriptedCompleter())
    base = agent.build_task(
        _state(critique=_critique(recommended_queries=["surface code 2025"]))
    )

    task = agent.sub_topic_task(
        base, _sub_topic("Alpha", 1), ["https://example.test/one"]
    )

    assert isinstance(task, SubTopicTask)
    assert task.sub_topic.title == "Alpha"
    assert task.existing_sources == ["https://example.test/one"]
    assert 'Gather evidence for the sub-topic "Alpha"' in task.instruction
    assert "- surface code 2025" in task.guidance
    assert "Sub-topic: Alpha" in task.guidance


@pytest.mark.asyncio
async def test_extraction_stamps_findings_from_retrieved_evidence(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SubTopicFindingsDraft(
                findings=[
                    FindingDraft(
                        content="Logical error rates fell below break-even.",
                        source_url="https://example.test/qec",
                        source_title="QEC 2025",
                        confidence=0.8,
                    )
                ]
            )
        ]
    )
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_scraper", QEC_SCRAPE)],
        iterations=1,
        tool_calls=1,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert errors == []
    assert provider_failed is False
    assert findings[0].related_sub_topic == "Alpha"
    assert findings[0].extracted_at == "2026-08-01T12:00:00+00:00"


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_without_evidence(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(agent_name="researcher", stop_reason="max_iterations")

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_when_the_only_hit_is_empty(
    tracker: Tracker,
) -> None:
    """A successful query_memory with no matches is not evidence.

    Regression guard for the loosened predicate that previously counted any
    successful tool call, including a memory lookup that hit nothing, as
    "retrieved" and triggered an extraction call against an empty evidence
    section.
    """
    completer = ScriptedCompleter()
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(1, "query_memory", {"matches": []}),
            _tool_step(2, "web_search", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


def _recalled_fact_run(*, nested: bool = False) -> ReActRun:
    """One successful query_memory carrying a highly confident "verified" fact."""
    match: dict[str, object] = {"content": "Capacity is 10 GW, verified earlier."}
    if nested:
        match["metadata"] = {
            "source_url": "https://remembered.example/report",
            "verified": True,
        }
    else:
        match["source_url"] = "https://remembered.example/report"
        match["confidence"] = 0.99
        match["verified"] = True
    return ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "query_memory", {"matches": [match]})],
        iterations=1,
        tool_calls=1,
    )


@pytest.mark.parametrize("nested", [False, True])
def test_a_recalled_fact_retrieves_no_source(nested: bool) -> None:
    """Recall is guidance: the URL it names was never read in this run."""
    run = _recalled_fact_run(nested=nested)

    assert retrieved_finding_urls(run) == ()
    assert _loop_read_anything(run) is False


@pytest.mark.parametrize("nested", [False, True])
def test_a_recalled_fact_never_reaches_the_extraction_prompt(nested: bool) -> None:
    run = _recalled_fact_run(nested=nested)

    rendered = render_evidence(run, limit=200, discovery_payloads=False)

    assert "Capacity is 10 GW, verified earlier." not in rendered
    assert "remembered.example" not in rendered
    assert rendered == "(no evidence retrieved)"


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_extraction_makes_no_provider_call_for_a_recalled_fact(
    tracker: Tracker, nested: bool
) -> None:
    """A recalled "verified" fact is not a read this run can extract from."""
    completer = ScriptedCompleter(outputs=[_findings_draft()])
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )

    findings, errors, provider_failed = await agent.extract_findings(
        task, _recalled_fact_run(nested=nested)
    )

    assert findings == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_when_search_was_the_only_tool(
    tracker: Tracker,
) -> None:
    """Five search hits and no page read: there is nothing to extract from.

    A search result is a DISCOVERY record. Extracting from the loop that only
    searched would let the model turn snippets into findings the agent never
    read, which is the hallucination-pressure case this gate exists to stop.
    """
    completer = ScriptedCompleter(outputs=[_findings_draft()])
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )

    findings, errors, provider_failed = await agent.extract_findings(
        task, _search_only_run()
    )

    assert findings == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_when_every_tool_call_failed(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", None, success=False),
            _tool_step(2, "web_scraper", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_after_a_provider_failure(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="provider_error",
        steps=[_tool_step(1, "web_scraper", QEC_SCRAPE)],
        iterations=1,
        tool_calls=1,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_malformed_extracted_findings_become_a_recoverable_error(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SubTopicFindingsDraft(
                findings=[
                    FindingDraft(
                        content="Bad confidence.",
                        source_url="https://example.test/qec",
                        source_title="QEC 2025",
                        confidence=9.0,
                    )
                ]
            )
        ]
    )
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_scraper", QEC_SCRAPE)],
        iterations=1,
        tool_calls=1,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert errors[0].error_type == "researcher_invalid_finding"
    assert errors[0].recoverable is True
    assert errors[0].details["rejected"] == ["finding 1: invalid confidence"]
    assert provider_failed is False


@pytest.mark.asyncio
async def test_extraction_reports_a_provider_failure_without_raising(
    tracker: Tracker,
) -> None:
    """The extraction call's own provider failure must degrade, not raise.

    Regression guard for the Critical finding: previously an
    ``OpenAIProviderError`` raised by the extraction call (separate from,
    and after, the sub-topic's ReAct loop) propagated straight out of
    ``run`` uncaught, discarding every finding already collected from prior
    sub-topics. ``extract_findings`` must instead catch it, report it as a
    non-recoverable structured error, and signal the failure back to the
    caller via ``provider_failed`` rather than letting the exception escape.
    """
    completer = ScriptedCompleter(
        outputs=[
            StructuredOutputError(
                "PROVIDER_SECRET_SENTINEL",
                diagnostics=[
                    {"attempt": 1, "field_paths": ["findings"]}
                ],
            )
        ]
    )
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_scraper", QEC_SCRAPE)],
        iterations=1,
        tool_calls=1,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert provider_failed is True
    assert len(errors) == 1
    assert errors[0].error_type == "researcher_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["operation"] == "researcher_finding_extraction"
    provider = errors[0].details["provider_failure"]
    assert provider["kind"] == "schema_output"
    assert "PROVIDER_SECRET_SENTINEL" not in str(errors[0].details)


@pytest.mark.asyncio
async def test_finalize_requires_a_sub_topic_task(tracker: Tracker) -> None:
    agent = _researcher(tracker, ScriptedCompleter())

    with pytest.raises(AgentConfigurationError, match="SubTopicTask"):
        await agent.finalize(
            AgentTask(instruction="Gather evidence."),
            ReActRun(agent_name="researcher", stop_reason="finished"),
        )


def test_the_state_update_carries_findings_and_errors(tracker: Tracker) -> None:
    agent = _researcher(tracker, ScriptedCompleter())
    finding = _finding("Alpha", "https://example.test/one")
    run = ReActRun(agent_name="researcher", stop_reason="finished")

    update = agent.state_update(ResearchFindings(findings=[finding]), run)

    assert update == {"errors": [], "raw_findings": [finding]}


def _findings_draft(
    content: str = "Logical error rates fell below break-even.",
) -> SubTopicFindingsDraft:
    """One finding in the registry shape the acquisition path now requires."""
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=content,
                source_url="https://example.test/qec",
                source_title=QEC_READ.title,
                confidence=0.8,
                read_id=QEC_READ.read_id,
                locator="chunk-0",
                excerpt=QEC_PASSAGE,
                target_ids=["topic-01"],
            )
        ]
    )


def _search_and_scrape_decisions() -> list[object]:
    return [
        use_tool("Find sources.", "web_search", '{"query": "qec 2025"}'),
        use_tool(
            "Read the best source.",
            "web_scraper",
            '{"url": "https://example.test/qec"}',
        ),
        finish("I have a source-backed answer.", "Error rates fell."),
    ]


@pytest.mark.asyncio
async def test_the_researcher_creates_findings_from_search_and_scrape(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[_findings_draft()],
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 1)])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert len(outcome.result.findings) == 1
    finding = outcome.result.findings[0]
    assert finding.related_sub_topic == "Alpha"
    assert finding.source_url == "https://example.test/qec"
    assert outcome.state_update["raw_findings"] == outcome.result.findings
    assert outcome.react.tool_calls == 2


@pytest.mark.asyncio
async def test_researcher_react_handles_empty_unused_final_answer(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            ReActDecision(
                thought="Find one source.",
                action="use_tool",
                tool_name="web_search",
                tool_input_json='{"query": "qec 2025"}',
                final_answer="",
            ),
            finish("The source is enough.", "Error rates fell."),
        ],
        outputs=[_findings_draft()],
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 1)])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[0].final_answer is None


@pytest.mark.asyncio
async def test_findings_merge_into_research_state(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[_findings_draft()],
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 1)])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)
    merged = merge_research_state(state, outcome.state_update)

    assert len(merged.raw_findings) == 1
    assert [event.event_type for event in merged.events] == [
        "researcher.sub_topic.started",
        "researcher.tool_call",
        "researcher.tool_call",
        "researcher.sub_topic.completed",
        "researcher.research.completed",
    ]


@pytest.mark.asyncio
async def test_the_researcher_reports_counts_and_stop_reason_per_sub_topic(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[_findings_draft()],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    events = {
        event.event_type: event for event in outcome.state_update["events"]
    }
    completed = events["researcher.sub_topic.completed"]
    assert completed.metadata["sub_topic"] == "Alpha"
    assert completed.metadata["stop_reason"] == "finished"
    assert completed.metadata["tool_calls"] == 2
    assert completed.metadata["findings"] == 1
    assert events["researcher.research.completed"].metadata == {
        "sub_topics_planned": 1,
        "sub_topics_researched": 1,
        "sub_topics_skipped": 0,
        "findings": 1,
    }


@pytest.mark.asyncio
async def test_the_completed_event_reports_what_bounding_kept_and_dropped(
    tracker: Tracker,
) -> None:
    """Bounded evidence must be visible: what was kept, and what was not.

    Ten drafted findings collapse to six: three restatements of one claim, and
    two distinct claims over the six-finding cap. An operator reading only the
    event stream has to be able to see both, and see that the six came from a
    single source.
    """
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Duplicated claim.",
                source_url="https://example.test/qec",
                source_title=QEC_READ.title,
                confidence=confidence,
                read_id=QEC_READ.read_id,
                locator="chunk-0",
                excerpt=QEC_PASSAGE,
                target_ids=["topic-01"],
            )
            for confidence in (0.4, 0.9, 0.6)
        ]
        + [
            FindingDraft(
                content=f"Distinct claim {index}.",
                source_url="https://example.test/qec",
                source_title=QEC_READ.title,
                confidence=0.5,
                read_id=QEC_READ.read_id,
                locator="chunk-0",
                excerpt=QEC_PASSAGE,
                target_ids=["topic-01"],
            )
            for index in range(7)
        ]
    )
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[draft],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    assert completed.metadata["findings"] == 6
    assert completed.metadata["findings_dropped_duplicate"] == 2
    assert completed.metadata["findings_dropped_cap"] == 2
    assert completed.metadata["sources_retained"] == 1

    assert len(outcome.result.findings) == 6
    assert max(
        finding.confidence for finding in outcome.result.findings
    ) == 0.9


_LONG_BODY = (
    "A named source about Alpha confirms queue delay commissioning cost. " * 130
)
_LONG_URL = "https://example.test/long-study.md"


def _long_study_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/markdown; charset=utf-8"},
            text=_LONG_BODY,
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_selected_passage_no_finding_used_gets_its_own_disposition(
    tracker: Tracker,
) -> None:
    """Disposition follows the passage, not the URL the passage came from.

    The document is split into two chunks and both are selected. The extracted
    finding cites chunk 0 only, so chunk 1 is not "used" — the earlier
    URL-level check would have called it used because a different passage of
    the same read produced a finding.
    """
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Queue delay commissioning cost is confirmed.",
                source_url=_LONG_URL,
                source_title="Alpha long study",
                confidence=0.8,
                read_id=build_read_record(
                    session_id="session-1",
                    reader="document_reader",
                    requested_url=_LONG_URL,
                    resolved_url=_LONG_URL,
                    title="Alpha long study",
                    retrieved_at=EXTRACTED_AT,
                    text=_LONG_BODY,
                    passages={
                        "chunk-0": _LONG_BODY[:8000],
                        "chunk-1": _LONG_BODY[8000:],
                    },
                    extraction_complete=True,
                ).read_id,
                locator="chunk-0",
                excerpt=_LONG_BODY[:8000],
                target_ids=["topic-01"],
            )
        ]
    )
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Find the study.", "web_search", '{"query": "alpha 2025"}'),
            use_tool(
                "Read the study.",
                "document_reader",
                json.dumps({"source": _LONG_URL}),
            ),
            finish("Done.", "Answer."),
        ],
        outputs=[draft],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title="Alpha long study", url=_LONG_URL)]
        ),
        http=_long_study_client(),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    units = outcome.state_update["evidence_units"]
    assert {unit.locator for unit in units.values()} == {"chunk-0", "chunk-1"}
    used = next(
        evidence_id
        for evidence_id, unit in units.items()
        if unit.locator == "chunk-0"
    )
    unused = next(
        evidence_id
        for evidence_id, unit in units.items()
        if unit.locator == "chunk-1"
    )
    reasons = {
        (item.stage, item.item_id): item.reason
        for item in outcome.state_update["evidence_dispositions"]
    }
    assert reasons[("extraction", unused)] == "irrelevant"
    assert ("extraction", used) not in reasons


@pytest.mark.asyncio
async def test_the_completed_event_reports_work_and_target_obligation(
    tracker: Tracker,
) -> None:
    """Yield counters must not restate one another.

    ``works_retained`` is back, now that Task 4 can derive it from real work
    identity instead of from a URL count wearing a second name: the one
    finding kept here came from one read, so one source URL is one work. What
    the event also reports is the acquisition that actually happened (one
    network body, no cache reuse) and whether the active target's obligation
    advanced.
    """
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[_findings_draft()],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    metadata = completed.metadata
    assert metadata["source_urls_retained"] == 1
    assert metadata["publishers_retained"] == 1
    assert metadata["findings_retained"] == 1
    assert metadata["works_retained"] == 1
    assert metadata["successful_reads"] == 1
    assert metadata["useful_evidence_yield"] == 1
    assert metadata["acquired_work_count"] == 1
    assert metadata["cache_hits"] == 0
    assert metadata["target_obligation_completed"] is True


def _retained_read(url: str, text: str) -> ReadRecord:
    """One complete read of ``text`` served from ``url``."""
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title="QEC 2025",
        retrieved_at=EXTRACTED_AT,
        text=text,
        passages={"chunk-0": text},
    )


def test_retained_works_count_identity_not_source_urls() -> None:
    """Two URLs serving one report are one work and two source URLs.

    This is the counter Task 3 deleted rather than ship as a URL count: it is
    now derived from the reads' own complete-content identity, so a mirrored
    copy collapses onto its original while a genuinely different document
    stays a second work.
    """
    text = "Logical error rates fell below break-even in 2025."
    original = _retained_read("https://example.test/qec", text)
    mirror = _retained_read("https://mirror.test/qec", text)
    other = _retained_read(
        "https://other.test/review", "An independent review reports 2024 data."
    )
    findings = [
        _finding("Alpha", original.resolved_url),
        _finding("Alpha", mirror.resolved_url),
        _finding("Alpha", other.resolved_url),
    ]

    bounded = bound_sub_topic_findings(
        findings[:2], reads=[original, mirror]
    )
    assert bounded.source_urls_retained == 2
    assert bounded.publishers_retained == 2
    assert bounded.works_retained == 1

    distinct = bound_sub_topic_findings(
        findings, reads=[original, mirror, other]
    )
    assert distinct.source_urls_retained == 3
    assert distinct.works_retained == 2


def test_a_retained_finding_with_no_read_is_not_merged_with_another() -> None:
    """Nothing is invented for a finding whose read is not in the registry."""
    findings = [
        _finding("Alpha", "https://one.test/a"),
        _finding("Alpha", "https://two.test/b"),
    ]

    bounded = bound_sub_topic_findings(findings, reads=[])

    assert bounded.works_retained == 2


@pytest.mark.asyncio
async def test_an_empty_extraction_reports_no_completed_obligation(
    tracker: Tracker,
) -> None:
    """No accepted finding means no completed target obligation, honestly."""
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    assert completed.metadata["target_obligation_completed"] is False
    assert completed.metadata["acquired_work_count"] == 1


@pytest.mark.asyncio
async def test_two_sub_topics_each_produce_findings_with_a_fresh_tool_budget(
    tracker: Tracker,
) -> None:
    """The capstone happy path: every selected sub-topic succeeds.

    Every other ``run`` test that produces findings uses exactly one
    sub-topic. This exercises two high-priority sub-topics whose loops and
    extractions both succeed, asserting the findings merge in order, the
    per-sub-topic events carry the right index, and — by setting
    ``tool_budget`` to exactly the number of tool calls one sub-topic makes
    — that the budget is genuinely fresh per sub-topic rather than a single
    pool shared and decremented across them (a shared pool would exhaust
    before Beta's second tool call and force ``tool_budget_exhausted``
    instead of ``finished``).
    """
    completer = ScriptedCompleter(
        decisions=[
            *_search_and_scrape_decisions(),
            *_search_and_scrape_decisions(),
        ],
        outputs=[
            _findings_draft("Alpha finding."),
            _findings_draft("Beta finding."),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=2),
    )
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 2)]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.react.stop_reason == "finished"
    # Four completed read/search actions, three external calls: Beta's read of
    # the same URL is served from the run's successful-read cache, which is a
    # local reuse rather than a second acquisition. The external count is what
    # the per-loop budget bounds, so it is the count that must stay split.
    assert outcome.react.tool_calls == 3
    assert outcome.react.cache_hits == 1
    assert [
        finding.related_sub_topic for finding in outcome.result.findings
    ] == ["Alpha", "Beta"]
    assert [
        finding.content for finding in outcome.result.findings
    ] == ["Alpha finding.", "Beta finding."]

    started = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    completed = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    ]
    assert len(started) == 2
    assert len(completed) == 2
    assert [event.metadata["index"] for event in started] == [1, 2]
    assert [event.metadata["index"] for event in completed] == [1, 2]
    assert [event.metadata["sub_topic"] for event in completed] == [
        "Alpha",
        "Beta",
    ]
    assert [event.metadata["findings"] for event in completed] == [1, 1]
    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_researcher_budget_override_bounds_each_sub_topic_loop(
    tracker: Tracker,
) -> None:
    """The researcher's own override reaches its direct ReAct loop.

    ``researcher: 10`` is production's value and equals the global default,
    so a wiring bug would be invisible at that value. This pins the override
    at one against a global budget of four: only the override can stop the
    loop after the first executed call.
    """
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response()]),
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=4,
            tool_budget_overrides={"researcher": 1},
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    assert outcome.react.tool_calls == 1
    assert outcome.react.stop_reason == "tool_budget_exhausted"


@pytest.mark.asyncio
async def test_the_researcher_respects_its_iteration_bound(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search again.", "web_search", '{"query": "qec 2025"}')
        ]
        * 2,
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=4),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    assert outcome.react.stop_reason == "max_iterations"
    assert outcome.react.iterations == 2


@pytest.mark.asyncio
async def test_the_researcher_prioritizes_the_gap_the_critic_named(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to retrieve.", "No new sources.")],
        outputs=[],
    )
    agent = _researcher(tracker, completer, max_sub_topics=1)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 5, coverage_id="topic-02"),
        ],
        critique=_critique(
            gaps=[
                _gap("Beta is completely uncovered.", coverage_id="topic-02")
            ],
            recommended_queries=["beta throughput 2025"],
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = outcome.state_update["events"][0]
    assert started.metadata["sub_topic"] == "Beta"
    loop_body = completer.react_calls[0].messages[1].content
    assert "- beta throughput 2025" in loop_body
    assert "Sub-topic: Beta" in loop_body


@pytest.mark.asyncio
async def test_the_researcher_runs_a_gaps_own_queries_for_its_target(
    tracker: Tracker,
) -> None:
    """A gap's queries reach the loop even when the Critic's list is empty."""
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to retrieve.", "No new sources.")],
        outputs=[],
    )
    agent = _researcher(tracker, completer, max_sub_topics=1)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 5, coverage_id="topic-02"),
        ],
        critique=_critique(
            gaps=[
                _gap(
                    "Beta is completely uncovered.",
                    coverage_id="topic-02",
                    recommended_queries=["beta durability trial 2026"],
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = outcome.state_update["events"][0]
    assert started.metadata["sub_topic"] == "Beta"
    loop_body = completer.react_calls[0].messages[1].content
    assert "Run these queries first:" in loop_body
    assert loop_body.index("- beta durability trial 2026") < loop_body.index(
        "- Beta 2025"
    )


@pytest.mark.asyncio
async def test_refinement_skips_a_satisfied_non_gap_topic_with_an_honest_reason(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Research Beta.", "No new sources.")],
        outputs=[],
    )
    agent = _researcher(tracker, completer, max_sub_topics=1)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 3, coverage_id="topic-02"),
        ],
        raw_findings=[_finding("alpha", "https://example.test/alpha")],
        critique=_critique(),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["Beta"]

    skipped = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].details == {
        "sub_topic": "Alpha",
        "coverage_id": "topic-01",
        "priority": 1,
        "reason": "interim_satisfaction",
    }
    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.research.completed"
    )
    assert completed.metadata["sub_topics_planned"] == 2
    assert completed.metadata["sub_topics_researched"] == 1
    assert completed.metadata["sub_topics_skipped"] == 1


@pytest.mark.asyncio
async def test_a_tool_failure_does_not_stop_the_loop(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search first.", "web_search", '{"query": "qec 2025"}'),
            use_tool(
                "Try the other source.",
                "web_scraper",
                '{"url": "https://example.test/qec"}',
            ),
            finish("I have one source.", "Error rates fell."),
        ],
        outputs=[_findings_draft()],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([RuntimeError("tavily is down")]),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    assert len(outcome.result.findings) == 1
    assert [error.error_type for error in outcome.errors] == [
        "agent_tool_failed"
    ]
    assert outcome.errors[0].recoverable is True
    tool_events = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.tool_call"
    ]
    assert [event.metadata["success"] for event in tool_events] == [False, True]


@pytest.mark.asyncio
async def test_a_high_priority_sub_topic_without_findings_records_a_warning(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing worth retrieving.", "No sources found.")],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    warnings = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_without_findings"
    ]
    assert len(warnings) == 1
    assert warnings[0].recoverable is True
    assert warnings[0].source == "agent.researcher"
    assert warnings[0].details["sub_topic"] == "Alpha"
    assert warnings[0].details["stop_reason"] == "finished"


@pytest.mark.asyncio
async def test_a_low_priority_sub_topic_without_findings_records_no_warning(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing worth retrieving.", "No sources found.")],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 7)]))

    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_provider_failure_stops_the_remaining_sub_topics(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[ProviderTimeoutError("timed out")],
    )
    agent = _researcher(tracker, completer)
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 2)]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.react.stop_reason == "provider_error"
    started = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert len(started) == 1
    assert any(error.recoverable is False for error in outcome.errors)


@pytest.mark.asyncio
async def test_a_provider_failure_during_extraction_keeps_prior_findings(
    tracker: Tracker,
) -> None:
    """Findings from a completed sub-topic must survive a later failure.

    Regression guard for the Critical finding: sub-topic Alpha's loop and
    extraction both succeed and produce one finding. Sub-topic Beta's loop
    *also* succeeds, but its extraction call reaches the provider output limit
    — the failure specifically identified as escaping ``run`` uncaught and
    destroying every finding collected so far. Sub-topic Gamma must never be
    started at all.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search Alpha.", "web_search", '{"query": "alpha 2025"}'),
            use_tool("Read Alpha.", "web_scraper", '{"url": "https://example.test/qec"}'),
            finish("Done with Alpha.", "Alpha answer."),
            use_tool("Search Beta.", "web_search", '{"query": "beta 2025"}'),
            use_tool("Read Beta.", "web_scraper", '{"url": "https://example.test/qec"}'),
            finish("Done with Beta.", "Beta answer."),
        ],
        outputs=[_findings_draft(), _output_limit_error()],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
    )
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1),
            _sub_topic("Beta", 2),
            _sub_topic("Gamma", 3),
        ]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert len(outcome.result.findings) == 1
    assert outcome.result.findings[0].related_sub_topic == "Alpha"

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["Alpha", "Beta"]

    extraction_errors = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_extraction_provider_error"
    ]
    assert len(extraction_errors) == 1
    assert extraction_errors[0].recoverable is False
    assert (
        extraction_errors[0].details["operation"]
        == "researcher_finding_extraction"
    )
    provider = extraction_errors[0].details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1

    # Finding 2 (stop_reason override): the merged run must report
    # "provider_error", not "finished" — Beta's extraction failure is what
    # aborted the pass, and the merged run must say so.
    assert outcome.react.stop_reason == "provider_error"

    # Finding 3 (no_findings_error ordering): Beta is priority 2, which is
    # already high-priority (HIGH_PRIORITY_THRESHOLD == 2), and its
    # extraction failed — it must get the provider-error-shaped error only,
    # never also a "no findings" warning that would mischaracterize an
    # outage as a coverage gap.
    assert "researcher_sub_topic_without_findings" not in [
        error.error_type for error in outcome.errors
    ]


@pytest.mark.asyncio
async def test_a_provider_failure_mid_loop_skips_the_remaining_high_priority_sub_topics(
    tracker: Tracker,
) -> None:
    """Sub-topics dropped by a mid-pass break must be recorded, not silently missing.

    Mirrors the seam test's regression pin for the ``max_sub_topics`` cap
    (``reason="cap"``), but for the other path that can drop a high-priority
    sub-topic: here all three sub-topics fit under ``max_sub_topics``, so
    nothing is capped, but Beta's loop hits a non-recoverable provider
    failure and ``break``s the pass before Gamma — high-priority and never
    attempted — gets a turn.
    """
    completer = ScriptedCompleter(
        decisions=[
            finish("Nothing for Alpha.", "Alpha has no sources."),
            ProviderTimeoutError("timed out"),
        ],
    )
    agent = _researcher(tracker, completer)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 2, coverage_id="topic-02"),
            _sub_topic("Gamma", 2, coverage_id="topic-03"),
        ]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["Alpha", "Beta"]

    skipped = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].details["sub_topic"] == "Gamma"
    assert skipped[0].details["reason"] == "provider_failure_stopped_processing"
    assert skipped[0].details["coverage_id"] == "topic-03"
    assert skipped[0].recoverable is True


@pytest.mark.asyncio
async def test_every_capped_sub_topic_is_recorded_with_its_coverage_id(
    tracker: Tracker,
) -> None:
    """A low-priority topic the cap dropped is skipped, and says so.

    Only high-priority topics used to get a record, so a capped topic below
    ``HIGH_PRIORITY_THRESHOLD`` vanished from ``state.errors`` and from the
    event stream with nothing naming its coverage id — the plan was silently
    short of what it promised.
    """
    completer = ScriptedCompleter(
        decisions=[finish("Nothing for Alpha.", "Alpha has no sources.")],
    )
    agent = _researcher(tracker, completer, max_sub_topics=1)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 9, coverage_id="topic-02"),
        ]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    skipped = {
        error.details["sub_topic"]: error
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped) == {"Beta"}
    assert skipped["Beta"].details["coverage_id"] == "topic-02"
    assert skipped["Beta"].details["reason"] == "cap"
    assert skipped["Beta"].details["priority"] == 9
    assert skipped["Beta"].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_records_every_unattempted_topic(
    tracker: Tracker,
) -> None:
    """After a break, every topic without a turn is named, not only the top ones."""
    completer = ScriptedCompleter(
        decisions=[
            finish("Nothing for Alpha.", "Alpha has no sources."),
            ProviderTimeoutError("timed out"),
        ],
    )
    agent = _researcher(tracker, completer)
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 2, coverage_id="topic-02"),
            _sub_topic("Gamma", 9, coverage_id="topic-03"),
            _sub_topic("Delta", 9, coverage_id="topic-04"),
        ]
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    skipped = {
        error.details["sub_topic"]: error.details
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped) == {"Gamma", "Delta"}
    assert [
        details["coverage_id"] for details in skipped.values()
    ] == ["topic-03", "topic-04"]
    assert {
        details["reason"] for details in skipped.values()
    } == {"provider_failure_stopped_processing"}


@pytest.mark.asyncio
async def test_an_attempted_low_priority_topic_gets_no_skip_record(
    tracker: Tracker,
) -> None:
    """Attempted is attempted: the record is for topics with no turn at all."""
    completer = ScriptedCompleter(
        decisions=[finish("Nothing for Alpha.", "Alpha has no sources.")],
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 9, coverage_id="topic-01")])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_long_sub_topic_title_is_clamped_in_recorded_events(
    tracker: Tracker,
) -> None:
    """``summarize_text`` must actually clamp long titles, not just be called.

    Regression guard for Finding 4: a title long enough that, left
    unclamped, would bloat every event and error that carries it.
    """
    long_title = "Quantum error correction " * 20  # well over 200 chars
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to retrieve.", "No sources found.")],
    )
    agent = _researcher(tracker, completer)
    state = _state(sub_topics=[_sub_topic(long_title, 1)])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    )
    assert started.metadata["sub_topic"] == summarize_text(long_title)
    assert started.metadata["sub_topic"] != long_title
    assert len(started.metadata["sub_topic"]) < len(long_title)


@pytest.mark.asyncio
async def test_a_plan_with_no_sub_topics_records_a_recoverable_error(
    tracker: Tracker,
) -> None:
    agent = _researcher(tracker, ScriptedCompleter())

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state())

    assert outcome.result == ResearchFindings()
    assert [error.error_type for error in outcome.errors] == [
        "researcher_no_sub_topics"
    ]
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_the_scratchpad_does_not_leak_between_sub_topics(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            finish("Nothing for Alpha.", "Alpha has no sources."),
            finish("Nothing for Beta.", "Beta has no sources."),
        ],
    )
    agent = _researcher(tracker, completer)
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 2)]
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(state)

    second_loop_body = completer.react_calls[1].messages[1].content
    assert "(no notes yet)" in second_loop_body
