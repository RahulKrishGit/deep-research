"""Tests for the Researcher's contracts, selection, and prompt helpers."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx
import pytest

from deep_research.agents.acquisition import (
    UNMINED_QUANTITY_REASON,
    UNMINED_TARGET_REASON,
    WEB_PASSAGE_CHARS,
)
from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.evidence import build_read_record
from deep_research.agents.prompts import STRUCTURED_REQUEST_END, AgentTask
from deep_research.agents.researcher import (
    _admitted_attribution,
    _admitted_figures,
    _bare_pronoun_judgement,
    DEFAULT_MAX_SUB_TOPICS,
    HIGH_PRIORITY_THRESHOLD,
    MAX_FINDINGS_PER_SUB_TOPIC,
    MAX_OWED_BATCHES,
    MAX_OWED_PASSAGES_PER_BATCH,
    MAX_UNIQUE_SOURCES_PER_SUB_TOPIC,
    FindingDraft,
    FindingFigureDraft,
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
    render_planned_targets,
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
    ChatMessage,
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.request_budget import RequestAttemptLimitError, RequestBudget
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig, RequestBudgetConfig
from deep_research.utils.types import (
    MAX_SNIPPET_CHARS,
    AcquisitionState,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    FindingFigure,
    MemorySnapshot,
    ReadRecord,
    ResearchError,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import (
    ScriptedCompleter,
    TargetKeyedCompleter,
    finish,
    native_turn_from_decision,
    use_tool,
)
from tests.evidence_fakes import make_read, make_target
from tests.research_fakes import (
    QEC_PASSAGE,
    QEC_SOURCE_URL,
    QEC_TITLE,
    FakeMemory,
    FakeSearchClient,
    page_client,
    qec_read_record,
    research_tools,
    search_response,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
# The plan's own target ids: ``planner.target_id_for`` namespaces a target by
# the sub-topic it belongs to, so an id says which topic it came from.
PLANNED_TARGET_ID = "topic-01-target-01"
# Another obligation of the same topic, for the tests that need a binding the
# caller does not mark required.
OTHER_TARGET_ID = "topic-01-target-02"


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
    memory_context: MemorySnapshot | None = None,
    evidence_units: dict[str, EvidenceUnit] | None = None,
    read_records: dict[str, ReadRecord] | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=sub_topics or [],
        raw_findings=raw_findings or [],
        memory_context=memory_context or MemorySnapshot(),
        evidence_units=evidence_units or {},
        read_records=read_records or {},
    )


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

def _rendered_example_payloads(body: str) -> list[dict]:
    """The JSON payloads the reply-example block rendered, decoded.

    The examples are part of the request, so a test of what they teach reads
    them back the way the model does: each ``Example JSON output:`` line is one
    JSON object, compacted by ``render_structured_reply_format``.
    """
    rendered: list[dict] = []
    for chunk in body.split("Example JSON output:\n")[1:]:
        payload = chunk.splitlines()[0]
        rendered.append(json.loads(payload))
    return rendered



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
    assert DEFAULT_MAX_SUB_TOPICS == 10


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


def test_session_guidance_is_empty_without_recalled_memory() -> None:
    assert render_session_guidance(_state()) == ""


def test_session_guidance_carries_the_recalled_strategies() -> None:
    guidance = render_session_guidance(
        _state(
            memory_context=MemorySnapshot(
                suggested_strategies=["Prefer peer-reviewed sources."]
            )
        )
    )

    assert guidance == (
        "Strategies that worked before:\n- Prefer peer-reviewed sources."
    )


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


def test_the_cap_keeps_the_most_confident_findings() -> None:
    max_findings = 30
    total = max_findings + 2
    confidences = [round(0.01 * step, 4) for step in range(total, 0, -1)]
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(confidences, start=1)
    ]

    budget = bound_sub_topic_findings(findings, max_findings=max_findings)

    assert [finding.confidence for finding in budget.retained] == sorted(
        confidences, reverse=True
    )[:max_findings]
    assert budget.dropped_duplicate == 0
    assert budget.dropped_cap == 2
    assert budget.sources_retained == 1


def test_the_cap_keeps_at_most_the_configured_distinct_sources() -> None:
    max_sources = 12
    total = max_sources + 2
    confidences = [round(0.01 * step, 4) for step in range(total, 0, -1)]
    findings = [
        _finding(
            "Alpha",
            f"https://s{index}.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(confidences, start=1)
    ]

    budget = bound_sub_topic_findings(findings, max_sources=max_sources)

    assert {finding.source_url for finding in budget.retained} == {
        f"https://s{index}.test/one" for index in range(1, max_sources + 1)
    }
    assert budget.dropped_cap == 2
    assert budget.sources_retained == max_sources


def test_a_second_source_keeps_a_slot_confidence_alone_would_fill() -> None:
    """Bounding evidence must not narrow it to one publisher.

    One page over the cap, and one finding from an independent page: a pure
    confidence ranking would keep the whole first page and drop the single
    independent source, which is the one a reader most needs to see.
    """
    max_findings = 30
    count = max_findings + 1
    confidences = [round(0.99 - 0.01 * step, 4) for step in range(count)]
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(confidences, start=1)
    ] + [
        _finding(
            "Alpha", "https://b.test/two", content="Independent.", confidence=0.5
        )
    ]

    budget = bound_sub_topic_findings(findings, max_findings=max_findings)

    kept_a = sorted(confidences, reverse=True)[: max_findings - 1]
    assert [finding.source_url for finding in budget.retained] == [
        "https://a.test/one" for _ in kept_a
    ] + ["https://b.test/two"]
    assert [finding.confidence for finding in budget.retained] == [
        *kept_a,
        0.5,
    ]
    assert budget.dropped_cap == 2
    assert budget.sources_retained == 2


def test_findings_bound_to_a_required_target_are_exempt_from_the_finding_cap() -> None:
    """An answer the run was sent to get is not volume the cap may drop.

    Eight findings from one page, and the only two bound to a required target
    are the least confident of the eight: a confidence ranking drops them
    exactly when the run needs them, which is what makes them answers rather
    than more evidence.
    """
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], start=1
        )
    ]
    bound = [
        finding.model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
        for finding in findings[:2]
    ]

    budget = bound_sub_topic_findings(
        [*bound, *findings[2:]], required_target_ids=[PLANNED_TARGET_ID]
    )

    assert len(budget.retained) == 8
    assert budget.dropped_cap == 0
    assert {
        finding.content for finding in budget.retained if finding.target_ids
    } == {"Finding 1.", "Finding 2."}


def test_findings_bound_to_a_required_target_are_exempt_from_the_source_cap() -> None:
    """The source cap bounds corroboration volume, not the answer's own pages.

    One publisher per source, one over the cap, and the least confident one
    -- a publisher the source cap would drop -- is the only record of a
    required target.
    """
    max_sources = 12
    total = max_sources + 2
    confidences = [round(0.99 - 0.01 * step, 4) for step in range(total)]
    findings = [
        _finding(
            "Alpha",
            f"https://s{index}.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(confidences, start=1)
    ]
    required = findings[-1].model_copy(
        update={"target_ids": [PLANNED_TARGET_ID]}
    )

    budget = bound_sub_topic_findings(
        [*findings[:-1], required],
        required_target_ids=[PLANNED_TARGET_ID],
        max_sources=max_sources,
    )

    assert {finding.source_url for finding in budget.retained} == {
        f"https://s{index}.test/one" for index in range(1, max_sources + 1)
    } | {f"https://s{total}.test/one"}
    assert budget.dropped_cap == 1
    assert budget.sources_retained == max_sources + 1


def test_the_required_target_exemption_has_a_ceiling() -> None:
    """The exemption guarantees the answer a slot, not every restatement of it.

    A pool of bound findings well past the exemption ceiling and the source
    cap, each from its own page and all less confident than six independent
    unbound ones. Without a ceiling the exemption keeps every one of them
    and the per-sub-topic cap bounds nothing, which is not an exemption but
    a hole in the cap: two is the ceiling, the two most confident are the
    answer, and the rest are evidence like any other -- ranked ahead of the
    six unbound findings whatever their confidence, because a required
    binding outranks one that is not, and bounded by the ordinary source
    cap the same as anything else.
    """
    max_findings = 30
    bound_count = max_findings + 10
    bound = [
        _finding(
            "Alpha",
            f"https://b{index}.test/one",
            content=f"Bound {index}.",
            confidence=confidence,
        ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
        for index, confidence in enumerate(
            [round(0.10 + 0.01 * step, 4) for step in range(bound_count)],
            start=1,
        )
    ]
    others = [
        _finding(
            "Alpha",
            f"https://s{index}.test/one",
            content=f"Other {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.92, 0.90, 0.88, 0.86, 0.84, 0.82], start=1
        )
    ]

    budget = bound_sub_topic_findings(
        [*bound, *others],
        required_target_ids=[PLANNED_TARGET_ID],
        max_findings=max_findings,
        max_sources=12,
    )

    # Exempt: the two most confident bound findings. Ordinary pool: the
    # twelve next-most-confident bound findings, the source cap's own limit
    # -- a required binding outranks the six unbound sources regardless of
    # their higher confidence, so none of the six takes a slot.
    assert budget.findings_retained == 14
    assert {
        finding.content for finding in budget.retained if finding.target_ids
    } == {f"Bound {index}." for index in range(bound_count - 13, bound_count + 1)}
    assert not any(
        finding.content.startswith("Other") for finding in budget.retained
    )
    assert budget.dropped_cap == len(bound) + len(others) - 14


def test_the_exemption_ceiling_is_counted_per_required_target() -> None:
    """Two required targets each keep their own two, not two between them.

    Three findings bind each of two required obligations, and enough
    independent sources to exceed the source cap on their own. A ceiling of
    two overall would let the first target's evidence use up the second's;
    the guarantee is per obligation, so each keeps its own strongest two --
    and, since a required binding outranks unbound evidence, each
    obligation's third, non-exempt finding also outranks every one of the
    independent sources, however more confidently scored they are.
    """
    max_sources = 12
    others_count = max_sources + 6
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"A{index}.",
            confidence=confidence,
        ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
        for index, confidence in enumerate([0.30, 0.20, 0.10], start=1)
    ] + [
        _finding(
            "Alpha",
            "https://b.test/one",
            content=f"B{index}.",
            confidence=confidence,
        ).model_copy(update={"target_ids": [OTHER_TARGET_ID]})
        for index, confidence in enumerate([0.60, 0.50, 0.40], start=1)
    ] + [
        _finding(
            "Alpha",
            f"https://s{index}.test/one",
            content=f"Other {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [round(0.99 - 0.01 * step, 4) for step in range(others_count)],
            start=1,
        )
    ]

    budget = bound_sub_topic_findings(
        findings,
        required_target_ids=[PLANNED_TARGET_ID, OTHER_TARGET_ID],
        max_sources=max_sources,
    )

    assert budget.findings_retained == 16
    assert {
        target_id: {
            finding.content
            for finding in budget.retained
            if target_id in finding.target_ids
        }
        for target_id in (PLANNED_TARGET_ID, OTHER_TARGET_ID)
    } == {
        PLANNED_TARGET_ID: {"A1.", "A2.", "A3."},
        OTHER_TARGET_ID: {"B1.", "B2.", "B3."},
    }
    assert budget.dropped_cap == len(findings) - 16


def test_a_binding_to_another_target_leaves_a_finding_under_the_cap() -> None:
    """Only the ids the caller marks required buy an exemption.

    A binding is not the exemption: a finding bound to an obligation that was
    not required, or to none at all, is evidence like any other and the cap
    still ranks it by confidence.
    """
    max_findings = 30
    total = max_findings + 2
    confidences = [round(0.01 * step, 4) for step in range(1, total + 1)]
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(confidences, start=1)
    ]
    bound = [
        finding.model_copy(update={"target_ids": [OTHER_TARGET_ID]})
        for finding in findings[:2]
    ]

    budget = bound_sub_topic_findings(
        [*bound, *findings[2:]],
        required_target_ids=[PLANNED_TARGET_ID],
        max_findings=max_findings,
    )

    assert len(budget.retained) == max_findings
    assert budget.dropped_cap == 2
    assert not any(finding.target_ids for finding in budget.retained)


def test_the_cap_keeps_the_sub_topics_own_obligation_ahead_of_other_target_rows() -> None:
    """D4: the ordinary cap must not let a more confidently scored row bound
    to another sub-topic's target crowd out this sub-topic's own obligation
    -- the audited run's cap kept six of another topic's price rows this way
    and dropped the sub-topic's own restatement of its required obligation.
    """
    exempt_a = _finding(
        "Alpha", "https://a.test/one", content="Own obligation, first.", confidence=0.9
    ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
    exempt_b = _finding(
        "Alpha", "https://a.test/one", content="Own obligation, second.", confidence=0.85
    ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
    own_overflow = _finding(
        "Alpha",
        "https://a.test/one",
        content="Own obligation, third.",
        confidence=0.35,
    ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})
    other_count = 30
    other_topic_rows = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Other-target row {index}.",
            confidence=confidence,
        ).model_copy(update={"target_ids": [OTHER_TARGET_ID]})
        for index, confidence in enumerate(
            [round(0.99 - 0.01 * step, 4) for step in range(other_count)],
            start=1,
        )
    ]

    budget = bound_sub_topic_findings(
        [exempt_a, exempt_b, own_overflow, *other_topic_rows],
        required_target_ids=[PLANNED_TARGET_ID],
        own_target_ids=[PLANNED_TARGET_ID],
        max_findings=other_count,
    )

    retained_content = {finding.content for finding in budget.retained}
    assert "Own obligation, third." in retained_content
    assert f"Other-target row {other_count}." not in retained_content


def test_a_cross_topic_required_finding_outranks_the_own_optional_overflow() -> None:
    """Fable's risk note: the cross-topic sweep's own answer must not be the
    first thing an over-the-cap sub-topic drops.

    Two of another sub-topic's required-target findings escape the cap
    entirely (the exemption ceiling); its third-most-confident does not.
    Under the old rank that third finding ranked below this sub-topic's own
    optional-bound finding whatever its confidence, because the ordinary
    cap ranked "this sub-topic's own" ahead of "any required". Now any
    required binding -- this sub-topic's own or another's, the shape a
    cross-sub-topic sweep's finding takes -- outranks an own-but-optional
    one, so the required target's overflow finding keeps the sub-topic's
    one remaining slot instead of losing it to a same-page paragraph that
    answers nothing the plan requires.
    """
    cross_required_exempt_a = _finding(
        "Alpha",
        "https://a.test/one",
        content="Cross-topic required, first.",
        confidence=0.9,
    ).model_copy(update={"target_ids": [OTHER_TARGET_ID]})
    cross_required_exempt_b = _finding(
        "Alpha",
        "https://a.test/one",
        content="Cross-topic required, second.",
        confidence=0.85,
    ).model_copy(update={"target_ids": [OTHER_TARGET_ID]})
    cross_required_overflow = _finding(
        "Alpha",
        "https://a.test/one",
        content="Cross-topic required, third.",
        confidence=0.3,
    ).model_copy(update={"target_ids": [OTHER_TARGET_ID]})
    own_optional = _finding(
        "Alpha",
        "https://a.test/one",
        content="Own optional finding.",
        confidence=0.99,
    ).model_copy(update={"target_ids": [PLANNED_TARGET_ID]})

    budget = bound_sub_topic_findings(
        [
            cross_required_exempt_a,
            cross_required_exempt_b,
            cross_required_overflow,
            own_optional,
        ],
        required_target_ids=[OTHER_TARGET_ID],
        own_target_ids=[PLANNED_TARGET_ID],
        max_findings=1,
    )

    retained_content = {finding.content for finding in budget.retained}
    assert "Cross-topic required, third." in retained_content
    assert "Own optional finding." not in retained_content


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
    assert body.rstrip().endswith(STRUCTURED_REQUEST_END)

    # Both examples are part of the request, and each states only what its own
    # passage carries: the figure finding reports no date its passage never
    # wrote, and the text finding carries no figures and names the body the
    # page's words credit (review RES-5 §1, RES-6 §1, EXTRA-2).
    figure_example, rule_example = _rendered_example_payloads(body)
    (figure_finding,) = figure_example["findings"]
    assert figure_finding["data_period"] == "2024"
    assert "statement_date" not in figure_finding
    assert "release_date" not in figure_finding
    assert "attributed_issuer" not in figure_finding
    (rule_finding,) = rule_example["findings"]
    assert rule_finding["figures"] == []
    assert rule_finding["attributed_issuer"] == "the record office"
    assert rule_finding["target_ids"] == ["topic-02-target-01"]
    assert "statement_date" not in rule_finding


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
        "snippet": QEC_PASSAGE,
        "target_ids": [PLANNED_TARGET_ID],
    }
    values.update(overrides)
    return SubTopicFindingsDraft(findings=[FindingDraft(**values)])


def _build_admitted(
    draft: SubTopicFindingsDraft,
    *,
    valid_target_ids: Sequence[str] = (PLANNED_TARGET_ID,),
    dropped_target_ids: list[str] | None = None,
) -> tuple[list[Finding], list[str]]:
    return build_findings(
        draft,
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
        known_reads={QEC_READ.read_id: QEC_READ},
        valid_target_ids=valid_target_ids,
        dropped_target_ids=dropped_target_ids,
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
        _registry_draft(snippet="Logical error rates fell below 0.1 percent.")
    )

    assert findings == []
    assert rejected == ["finding 1: snippet was not admitted at locator"]


def test_a_registry_shaped_finding_is_still_admitted() -> None:
    """The enforcement is a contract, not a wall: the right shape passes."""
    findings, rejected = _build_admitted(_registry_draft())

    assert rejected == []
    assert [finding.content for finding in findings] == [
        "Logical error rates fell below break-even."
    ]
    assert findings[0].source_title == QEC_READ.title


def test_a_finding_bound_to_another_topics_target_is_admitted() -> None:
    """A read fetched for one sub-topic may answer another topic's target.

    The run that was audited read the EIA page carrying the 2025 forecast
    under the 2024-additions sub-topic, and nothing ever mined it for the
    forecast topic: extraction could only name the coverage topic that fetched
    the read, so that binding was discarded and no claim was ever bound to a
    target. The finding keeps the planned target it names, and the sub-topic
    that fetched the read stays stamped as ``related_sub_topic``.
    """
    forecast_target_id = "topic-02-target-01"
    findings, rejected = _build_admitted(
        _registry_draft(target_ids=[forecast_target_id]),
        valid_target_ids=(PLANNED_TARGET_ID, forecast_target_id),
    )

    assert rejected == []
    assert findings[0].target_ids == [forecast_target_id]
    assert findings[0].related_sub_topic == "Alpha"


def test_an_unplanned_target_id_is_dropped_and_recorded() -> None:
    """An invented id is dropped, named, and never cached into state."""
    dropped: list[str] = []
    findings, rejected = _build_admitted(
        _registry_draft(target_ids=["topic-09-target-01", PLANNED_TARGET_ID]),
        dropped_target_ids=dropped,
    )

    assert rejected == []
    assert findings[0].target_ids == [PLANNED_TARGET_ID]
    assert dropped == [
        'finding 1: dropped target id "topic-09-target-01" '
        "(it is not a planned target)"
    ]


def test_a_finding_naming_no_planned_target_is_kept_unbound() -> None:
    """An id the plan never issued is dropped; the evidence is not.

    The vocabulary the extraction can confuse a plan target with is the one
    the packet prints on every read and evidence unit — the coverage id of the
    sub-topic that fetched it (``topic-01`` against ``topic-01-target-01``).
    Rejecting the finding over that would lose the evidence entirely, so the
    id goes and the finding stays, unbound, attributed later through the
    sub-topic that fetched its read.
    """
    dropped: list[str] = []
    findings, rejected = _build_admitted(
        _registry_draft(target_ids=["topic-01"]),
        dropped_target_ids=dropped,
    )

    assert rejected == []
    assert findings[0].target_ids == []
    assert dropped == [
        'finding 1: dropped target id "topic-01" '
        "(it is not a planned target)"
    ]


def test_a_finding_naming_no_target_at_all_is_kept_unbound() -> None:
    """A plan-bound extraction that names nothing is unbindable, not invalid."""
    findings, rejected = _build_admitted(_registry_draft(target_ids=[]))

    assert rejected == []
    assert findings[0].target_ids == []


def test_a_dated_figure_keeps_its_vintage_and_date() -> None:
    """A figure's period, the date it was stated, and its data vintage differ.

    Extraction records all three so a later stage can tell the latest statement
    from an older one: the audited run published an 18.2 GW forecast from the
    December 2024 inventory as "the latest" while its own citations carried
    19.6 GW from the January 2025 one.

    The statement date is the page's own year here, not the audited run's
    2025-03-12: a statement date is admitted against the page that states it
    now (``_admitted_stated_date``), and this read's text writes "in 2025". The
    assertion that kept a full date no page carried was pinning exactly the
    unchecked copy the fix removes; the full date's own case is
    ``test_a_read_fetched_for_one_topic_yields_another_topics_finding``, whose
    EIA page writes "March 12, 2025".
    """
    findings, rejected = _build_admitted(
        _registry_draft(
            data_period="2024",
            statement_date="2025",
            vintage="January 2025 Preliminary Monthly Electric Generator Inventory",
        )
    )

    assert rejected == []
    assert findings[0].data_period == "2024"
    assert findings[0].statement_date == "2025"
    assert findings[0].vintage == (
        "January 2025 Preliminary Monthly Electric Generator Inventory"
    )


def test_an_undated_figure_records_no_dates_at_all() -> None:
    """Absence is absence: a blank is not a date a comparer may rank."""
    findings, _ = _build_admitted(
        _registry_draft(
            data_period="  ", statement_date="", vintage=None
        )
    )

    assert findings[0].data_period is None
    assert findings[0].statement_date is None
    assert findings[0].vintage is None


def test_a_statement_date_the_page_does_not_state_is_dropped() -> None:
    """A date no page carries is not a page fact, and this one is printed as one.

    ``statement_date`` is published as the finding's own statement date and is
    the Evidence Verifier's last-resort basis for resolving a relative period —
    a basis whose reader labels it a page date. The extraction copied it out of
    the model with no check at all, while the release date beside it was already
    admitted against the page's own text; a page that never wrote 2025-03-12 had
    a model's guess published as when it said so (live probe ``_page_date_basis``:
    resolved 2026 from a statement date no page carried).
    """
    findings, rejected = _build_admitted(
        _registry_draft(statement_date="2025-03-12")
    )

    assert rejected == []
    assert findings[0].statement_date is None


def test_a_date_is_not_admitted_as_a_figure() -> None:
    """A date names a day, not a measure, however the page spells it.

    The audited run recorded five application dates as stated figures, and the
    verifier then "corrected" each figure's period to the date it already was.
    The date belongs in the excerpt and the statement date, where it is
    evidence; a figure slot is for a quantity, and code refuses the date shape
    at admission rather than letting a later stage read it as a measurement.
    """
    figures, dropped = _admitted_figures(
        [
            FindingFigureDraft(value="1 August 2024", unit="date"),
            FindingFigureDraft(value="2027-08-02", unit="deadline"),
            FindingFigureDraft(value="2 Aug 2025", unit="in force"),
            FindingFigureDraft(value="10.4", unit="GW"),
        ],
        index=2,
    )

    assert [(figure.value, figure.unit) for figure in figures] == [("10.4", "GW")]
    assert dropped == [
        "finding 2: figure 1 states a date, not a measure",
        "finding 2: figure 2 states a date, not a measure",
        "finding 2: figure 3 states a date, not a measure",
    ]


def test_a_finding_whose_figure_is_a_date_keeps_its_text_and_date() -> None:
    """Refusing the figure must not cost the finding that states the date.

    The date is what the page says about when something applies: it stays in
    the excerpt the finding rests on and in its own statement date and data
    period, and only the figure slot is refused.
    """
    dropped_figures: list[str] = []

    findings, rejected = build_findings(
        _registry_draft(
            figures=[FindingFigureDraft(value="1 August 2025", unit="date")],
            statement_date="2025",
            data_period="2025",
        ),
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=("https://example.test/qec",),
        known_reads={QEC_READ.read_id: QEC_READ},
        valid_target_ids=(PLANNED_TARGET_ID,),
        dropped_figures=dropped_figures,
    )

    assert rejected == []
    (finding,) = findings
    assert finding.figures == []
    assert finding.statement_date == "2025"
    assert finding.data_period == "2025"
    assert finding.snippet == QEC_PASSAGE
    assert dropped_figures == [
        "finding 1: figure 1 states a date, not a measure"
    ]


def test_a_statement_date_the_page_states_is_kept() -> None:
    """The counterpart: the page's own date at its own precision stays."""
    findings, _ = _build_admitted(_registry_draft(statement_date="2025"))

    assert findings[0].statement_date == "2025"


def test_a_relative_phrase_is_recorded_for_resolution_not_as_a_period() -> None:
    """A phrase that dates a figure against the page is not a period it states.

    The dates contract asks for a period "as the source writes it", and a
    news-like page writes "this year". Recorded as the period, the Evidence
    Verifier reads it as a period *the words state themselves* and refuses the
    Context Check's resolved year as ``correction_not_on_page``: the figure is
    dropped on a page that dates it, and the date is known (live probe: "this
    year" recorded, 2026 proposed, dropped). Code resolves a relative phrase
    from the page's own date and records which date it came from (D11), so the
    phrase is recorded through that path and never as an explicit period — the
    page's own words stay in the excerpt the resolution reads.
    """
    findings, rejected = _build_admitted(
        _registry_draft(
            data_period="this year",
            figures=[
                FindingFigureDraft(
                    value="12", unit="percent", period="this year", kind="actual"
                )
            ],
        )
    )

    assert rejected == []
    assert findings[0].data_period is None
    assert findings[0].figures[0].period is None


def test_a_period_the_page_states_is_kept_as_written() -> None:
    """The counterpart: a period carrying its own time is recorded verbatim."""
    findings, _ = _build_admitted(
        _registry_draft(
            data_period="2024",
            figures=[
                FindingFigureDraft(
                    value="12", unit="percent", period="Q1 2024", kind="actual"
                )
            ],
        )
    )

    assert findings[0].data_period == "2024"
    assert findings[0].figures[0].period == "Q1 2024"


# ---------------------------------------------------------------------------
# The audited run's relay: a page that states somebody else's figures
# ---------------------------------------------------------------------------
#
# cleanedge.com's data dive carries EIA's own December-2024-inventory figures
# inside an EIA-attributed paragraph, and the audited run published them as
# "Clean Edge reported", which manufactured a conflict with EIA's newer
# release. The passage is the stored read's own text.

RELAY_URL = (
    "https://cleanedge.com/data-dive/"
    "u-s-electric-utility-scale-capacity-additions-by-fuel-type-2"
)
RELAY_TITLE = (
    "U.S. Electric Utility-Scale Capacity Additions, by Fuel Type - Clean Edge"
)
RELAY_PASSAGE = (
    "Solar accounted for most of the new capacity in 2024, according to the "
    "U.S. Energy Information Administration (EIA). The EIA projects that "
    "solar capacity will grow by another 32.5 GW in 2025. And while not "
    "tracked in our chart above, a record-breaking 18.2 GW of utility-scale "
    "battery storage are projected this year, up from 10.3 GW in 2024."
)
RELAY_ATTRIBUTION = (
    "according to the U.S. Energy Information Administration (EIA)"
)

# The independent tracker's own release. Its figure is the market monitor's,
# measured across all segments, and it says so — which is what a finding has
# to record rather than silently restate in the target's scope.
TRACKER_URL = (
    "https://woodmac.com/press-releases/2024-press-releases/"
    "energy-storages-meteoric-rise-breaks-another-record"
)
TRACKER_TITLE = "Energy Storage's Meteoric Rise Breaks Another Record | Wood Mackenzie"
TRACKER_PASSAGE = (
    "The U.S. energy storage market set a new record in 2024 with 12.3 "
    "gigawatts (GW) of installations across all segments, according to the "
    "latest U.S. Energy Storage Monitor report released today by the "
    "American Clean Power Association (ACP) and Wood Mackenzie. The report "
    "shows a total of 12,314 megawatts (MW) and 37,143 megawatt hours (MWh) "
    "deployed, in an edition dated March 24, 2025."
)


def _relay_read() -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=RELAY_URL,
        resolved_url=RELAY_URL,
        title=RELAY_TITLE,
        retrieved_at=EXTRACTED_AT,
        text=RELAY_PASSAGE,
        passages={"chunk-2": RELAY_PASSAGE},
        extraction_complete=True,
    )


def _tracker_read() -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=TRACKER_URL,
        resolved_url=TRACKER_URL,
        title=TRACKER_TITLE,
        retrieved_at=EXTRACTED_AT,
        text=TRACKER_PASSAGE,
        passages={"chunk-16": TRACKER_PASSAGE},
        extraction_complete=True,
    )


def _build_relay(
    read: ReadRecord,
    locator: str,
    **overrides: object,
) -> tuple[list[Finding], list[str]]:
    """Stamp one draft against ``read`` through the acquisition path."""
    values: dict[str, object] = {
        "content": "A figure was reported.",
        "source_url": read.resolved_url,
        "source_title": read.title,
        "confidence": 0.8,
        "read_id": read.read_id,
        "locator": locator,
        "snippet": read.passages[locator],
        "target_ids": [PLANNED_TARGET_ID],
    }
    values.update(overrides)
    return build_findings(
        SubTopicFindingsDraft(findings=[FindingDraft(**values)]),
        sub_topic=_sub_topic("Alpha"),
        extracted_at=EXTRACTED_AT,
        known_urls=(read.resolved_url,),
        known_reads={read.read_id: read},
        valid_target_ids=(PLANNED_TARGET_ID,),
    )


def test_a_relayed_figure_is_attributed_to_the_body_the_page_names() -> None:
    """The relay publishes somebody else's figure; the finding says whose.

    The audited run's report printed "Clean Edge reported a lower 2024 count of
    10.3 GW", a second measurement that never existed: the page attributes
    both figures to EIA in its own words. The finding carries that body and the
    page's phrase for it, so no later stage can credit the host.
    """
    findings, rejected = _build_relay(
        _relay_read(),
        "chunk-2",
        content=(
            "A record-breaking 18.2 GW of utility-scale battery storage are "
            "projected for 2025, up from 10.3 GW in 2024, according to the "
            "U.S. Energy Information Administration (EIA)."
        ),
        attributed_issuer="U.S. Energy Information Administration (EIA)",
        attribution_quote=RELAY_ATTRIBUTION,
    )

    assert rejected == []
    assert findings[0].attributed_issuer == (
        "U.S. Energy Information Administration (EIA)"
    )
    assert findings[0].attribution_quote == RELAY_ATTRIBUTION


def test_an_attribution_quote_the_read_does_not_carry_is_not_recorded() -> None:
    """A relay claim the page never makes is not evidence about the page.

    The finding is kept — its excerpt is the source's own text — but the
    attribution is dropped with the quote that failed, because an attribution
    nothing in the read states is exactly the fabrication this field exists to
    stop.
    """
    findings, rejected = _build_relay(
        _relay_read(),
        "chunk-2",
        attributed_issuer="U.S. Energy Information Administration (EIA)",
        attribution_quote="according to the National Renewable Energy Laboratory",
    )

    assert rejected == []
    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


def test_an_attribution_that_does_not_name_the_issuer_is_not_recorded() -> None:
    """The page's phrase has to name the body the finding credits."""
    findings, _ = _build_relay(
        _relay_read(),
        "chunk-2",
        attributed_issuer="Wood Mackenzie",
        attribution_quote=RELAY_ATTRIBUTION,
    )

    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


def test_a_blank_attribution_records_nothing_at_all() -> None:
    """A page's own figure is not a relay, and a blank is not a name."""
    findings, _ = _build_relay(
        _relay_read(),
        "chunk-2",
        attributed_issuer="  ",
        attribution_quote="",
    )

    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


SURVEY_PASSAGE = (
    "Unlike the EIA, which counted 10.3 GW, our survey found 12 GW of "
    "batteries installed in 2024. Separately, the EIA reported 5 GW of wind."
)


def _survey_read() -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url="https://example.test/battery-survey",
        resolved_url="https://example.test/battery-survey",
        title="Battery Storage Survey",
        retrieved_at=EXTRACTED_AT,
        text=SURVEY_PASSAGE,
        passages={"chunk-2": SURVEY_PASSAGE},
        extraction_complete=True,
    )


def test_a_contrastive_mention_of_a_body_is_not_an_attribution() -> None:
    """"Unlike the EIA" names the body without crediting it with anything.

    The review's own case: the survey's own 12 GW figure must not be
    credited to the EIA merely because the EIA is named in a contrasting
    clause right beside it.
    """
    findings, rejected = _build_relay(
        _survey_read(),
        "chunk-2",
        content="Our survey found 12 GW of batteries installed in 2024.",
        attributed_issuer="EIA",
        attribution_quote="Unlike the EIA",
    )

    assert rejected == []
    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


def test_the_bare_name_alone_is_not_an_attribution() -> None:
    """A name with no cue beside it credits nobody with anything."""
    findings, rejected = _build_relay(
        _survey_read(),
        "chunk-2",
        content="Our survey found 12 GW of batteries installed in 2024.",
        attributed_issuer="EIA",
        attribution_quote="EIA",
    )

    assert rejected == []
    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


NEIGHBOUR_LEAD = (
    "According to the U.S. Energy Information Administration (EIA), "
    "battery installations are accelerating nationwide."
)
NEIGHBOUR_FIGURE = (
    "A record-breaking 18.2 GW of utility-scale battery storage are "
    "projected for 2025, up from 10.3 GW in 2024."
)


def _multi_passage_read(*, passages: dict[str, str], url: str) -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title="Storage Market Report",
        retrieved_at=EXTRACTED_AT,
        text=" ".join(passages.values()),
        passages=passages,
        extraction_complete=True,
    )


def test_an_attribution_in_the_neighbouring_passage_is_admitted() -> None:
    """The documented case: the attribution sits at the end of the prior chunk.

    A live call read the figure passage whose attribution sentence sits in
    the passage just before it; the excerpt's own passage is not the only
    place the read may state who a figure belongs to.
    """
    read = _multi_passage_read(
        passages={"chunk-1": NEIGHBOUR_LEAD, "chunk-2": NEIGHBOUR_FIGURE},
        url="https://example.test/neighbour-attribution",
    )

    findings, rejected = _build_relay(
        read,
        "chunk-2",
        attributed_issuer="U.S. Energy Information Administration (EIA)",
        attribution_quote=(
            "According to the U.S. Energy Information Administration (EIA)"
        ),
    )

    assert rejected == []
    assert findings[0].attributed_issuer == (
        "U.S. Energy Information Administration (EIA)"
    )


def test_an_attribution_only_in_a_far_passage_is_not_admitted() -> None:
    """A quote elsewhere in the document, but not beside the excerpt, is not read.

    The old rule searched the whole document for a matching quote; this one
    is scoped to the excerpt's own passage and its immediate neighbour, so a
    body named several passages away cannot be credited with a figure it was
    never placed beside.
    """
    read = _multi_passage_read(
        passages={
            "chunk-1": NEIGHBOUR_LEAD,
            "chunk-2": "Filler paragraph with no attribution at all here today.",
            "chunk-3": NEIGHBOUR_FIGURE,
        },
        url="https://example.test/far-attribution",
    )

    findings, rejected = _build_relay(
        read,
        "chunk-3",
        attributed_issuer="U.S. Energy Information Administration (EIA)",
        attribution_quote=(
            "According to the U.S. Energy Information Administration (EIA)"
        ),
    )

    assert rejected == []
    assert findings[0].attributed_issuer is None
    assert findings[0].attribution_quote is None


SPAN_LEAD = (
    "The operator shall file the annual return with the county office and "
    "the district inspectorate"
)
SPAN_TAIL = (
    " before the last day of the following quarter, and shall keep a copy "
    "of the return in the register for three years."
)
# The excerpt spans the cut the passage bound made: both halves are the page's
# own words, and neither passage alone carries the whole of it.
SPAN_SNIPPET = "the county office and the district inspectorate before the last day"


def test_a_snippet_spanning_its_passage_and_its_neighbour_is_admitted() -> None:
    """A rule the bound cut in two is still one quote.

    The passage bound can fall inside a sentence, so an excerpt drawn from
    the read can straddle the cut: its words are all in the read at that
    locator, but not inside either passage alone. Admitting a passage alone
    dropped a quote the page states verbatim. The window is the same one the
    attribution and relay checks read, never the whole page.
    """
    read = _multi_passage_read(
        passages={
            "chunk-1": SPAN_LEAD,
            "chunk-2": SPAN_TAIL,
            "chunk-3": "A third passage that carries nothing of the quote.",
        },
        url="https://example.test/spanning-snippet",
    )

    findings, rejected = _build_relay(read, "chunk-1", snippet=SPAN_SNIPPET)

    assert rejected == []
    [finding] = findings
    assert finding.snippet == SPAN_SNIPPET
    assert finding.locator == "chunk-1"


def test_a_verbatim_quote_cited_at_a_far_locator_is_admitted_and_relocated() -> None:
    """Admission reads the whole page; the claimed locator is only a hint.

    A quote three passages away is still the read's own words, however far
    the model's claim sits from where it actually runs: the finding is kept,
    and its locator moves to the passage the quote truly starts in.
    """
    read = _multi_passage_read(
        passages={
            "chunk-1": "The first passage states nothing a draft would quote.",
            "chunk-2": "The second passage states nothing a draft would quote.",
            "chunk-3": (
                "The operator shall file the annual return with the county "
                "office before the last day of the quarter."
            ),
        },
        url="https://example.test/far-snippet",
    )

    findings, rejected = _build_relay(
        read, "chunk-1", snippet="file the annual return with the county office"
    )

    assert rejected == []
    [finding] = findings
    assert finding.locator == "chunk-3"


def test_a_snippet_stitched_from_non_adjacent_passages_is_still_refused() -> None:
    """Two genuine spans the page never runs together are not one quote.

    The stitched text names words the page states, on either side, but never
    as one contiguous run: the passage between them states something else
    entirely, so admitting it would bind an excerpt to words the page never
    actually wrote together.
    """
    read = _multi_passage_read(
        passages={
            "chunk-1": "The operator shall file the annual return each year.",
            "chunk-2": "Filings are reviewed by the district board each spring.",
            "chunk-3": "with the county office before the last day of the quarter.",
        },
        url="https://example.test/stitched-snippet",
    )

    findings, rejected = _build_relay(
        read,
        "chunk-1",
        snippet=(
            "The operator shall file the annual return each year. with the "
            "county office before the last day of the quarter."
        ),
    )

    assert findings == []
    assert rejected == ["finding 1: snippet was not admitted at locator"]


def test_a_tracker_figure_keeps_its_segment_and_release_date() -> None:
    """An all-segment total must travel as one, not as a grid-scale figure.

    Wood Mackenzie's release states 12,314 MW across all segments; the
    grid-scale 2025 forecast is a different figure on a different basis. The
    finding records the segment the page states and the date the page carries,
    so a later stage can refuse the all-segment total for a grid-scale target
    instead of silently substituting it.
    """
    findings, rejected = _build_relay(
        _tracker_read(),
        "chunk-16",
        content=(
            "The U.S. energy storage market set a new record in 2024 with "
            "12,314 MW of installations across all segments."
        ),
        attributed_issuer="Wood Mackenzie",
        attribution_quote=(
            "the latest U.S. Energy Storage Monitor report released today by "
            "the American Clean Power Association (ACP) and Wood Mackenzie"
        ),
        measure_scope="all segments",
        release_date="2025-03-24",
    )

    assert rejected == []
    assert findings[0].measure_scope == "all segments"
    assert findings[0].release_date == "2025-03-24"


def test_a_blank_scope_or_release_date_is_recorded_as_absent() -> None:
    findings, _ = _build_relay(
        _tracker_read(), "chunk-16", measure_scope=" ", release_date=""
    )

    assert findings[0].measure_scope is None
    assert findings[0].release_date is None


def test_a_measure_scope_the_read_does_not_state_is_not_recorded() -> None:
    """A scope the page never wrote is not a fact about the page."""
    findings, rejected = _build_relay(
        _tracker_read(), "chunk-16", measure_scope="grid-scale only"
    )

    assert rejected == []
    assert findings[0].measure_scope is None


def test_a_release_date_the_read_does_not_state_is_not_recorded() -> None:
    """A release date the page never wrote is not a fact about the page."""
    findings, rejected = _build_relay(
        _tracker_read(), "chunk-16", release_date="2099-01-01"
    )

    assert rejected == []
    assert findings[0].release_date is None


def test_extraction_contract_requires_the_attribution_and_the_scope() -> None:
    """The request carries the relay rule, not only the response schema.

    Every finding the model returns is derived from the packet, so the two
    mistakes the audited run made — crediting the relay, and restating a
    figure's segment as the target's — have to be refused in the request the
    model reads, in the words it is asked to follow.
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
        planned_targets=_forecast_plan_targets(),
    )[1].content

    assert "Name the body the page's words give the statement or figure to" in body
    assert "keep the page's own words for what the finding measures" in body
    assert "state that wording in measure_scope" in body


def test_extraction_contract_requires_the_registry_copy_it_checks() -> None:
    """A finding cites the read it came from, not the URL the document names.

    The recorded run read EIA's article as a House hearing PDF, and the PDF's
    own text prints "https://www.eia.gov/todayinenergy/detail.php?id=64705".
    Citing that URL is citing a page this run never read — the membership check
    drops the finding — and it is also the relay mistake in a different
    spelling: the copy is where the figure was read, and the body it came from
    belongs in the attribution, not in the citation.
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
        planned_targets=_forecast_plan_targets(),
    )[1].content

    assert (
        "never substitute the URL or title the document names as its origin"
        in body
    )
    assert "not to drop the reader's own markers" in body


def test_extraction_contract_treats_a_disputing_passage_as_a_finding() -> None:
    """D1: a passage that disputes, qualifies or dates a target's reason,
    mechanism, provision or figure is a finding for that target too, not only
    a passage that states the mechanism outright. The audited run's read held
    a later source disputing an early claim and produced no finding for it.

    RevW4Extract P3: the example must dispute a step of the mechanism
    (what caused the decline), not the size of an unrelated effect -- an
    example that disputes something the target does not ask about would
    teach the over-binding the preceding rule guards against.
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
        planned_targets=_forecast_plan_targets(),
    )[1].content

    assert (
        "disputes, qualifies or dates a reason, mechanism, provision or "
        "figure a target asks about is also a finding for that target"
        in body
    )
    assert (
        "Later field surveys found the flooding played little part in the "
        "decline the early accounts blamed on it"
        in body
    )
    assert "a target asking what caused the decline" in body


def test_extraction_contract_kind_is_content_anchored_not_date_anchored() -> None:
    """D4 (RevW4Extract P0): ``kind`` reads what the page presents the outcome
    as, not a date neither prompt carries. A same-period or undated outlook
    the page frames as projected, expected, planned or targeted stays
    forecast whatever the run's date; only a plan, proposal, law or
    provision's own term -- enacted or not -- is actual. The audited run
    read a provision's own words as a forecast target; the reverse error
    (an undated outlook read as actual) is what the date-based wording caused.
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
        planned_targets=_forecast_plan_targets(),
    )[1].content

    assert (
        "forecast for an outcome the page presents as projected, expected, "
        "planned or targeted rather than reported as having happened"
        in body
    )
    assert "it stays a forecast whatever the run's date" in body
    assert (
        "actual for a quantity the page states as measured, reported or "
        "observed, and for a term a plan, proposal, law or provision itself "
        "sets"
        in body
    )
    assert "enacted or not, in force yet or not" in body
    assert "an outcome such a text aims at by a later date is a forecast" in body
    assert "forecast only when" not in body
    assert "as-of date when the page gives none" not in body


_EXTRACTION_CONTRACT_FORBIDDEN_WORDS = (
    "roman", "republic", "gracchi", "sallust", "sulla", "augustus", "rome",
    "headphone", "climate", "semaglutide", "valorant", "drug",
)


def test_extraction_contract_names_no_domain_word() -> None:
    """D10/D11: the extraction contract is general text, never a probe subject."""
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- evidence_id=ev-1 read_id=read-1 locator=chunk-0",
        planned_targets=_forecast_plan_targets(),
    )[1].content
    lowered = body.lower()

    for word in _EXTRACTION_CONTRACT_FORBIDDEN_WORDS:
        assert word not in lowered, word


def test_extraction_names_every_planned_target_a_read_may_serve() -> None:
    """The extraction request carries the whole plan, not one sub-topic.

    A read is mined for every planned target: the request lists each target's
    id beside its question, so a passage about the 2025 forecast can be bound
    to the forecast target even when the sub-topic that fetched it was about
    2024.
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        acquisition_context="- read_id=read-1 targets=topic-01",
        planned_targets=_forecast_plan_targets(),
    )[1].content

    assert (
        f"- {PLANNED_TARGET_ID} [topic-01] [required]: "
        f"{_FIXTURE_TARGET_QUESTION}" in body
    )
    assert (
        f"- {FORECAST_TARGET_ID} [topic-02] [required]: "
        f"{FORECAST_TARGET_QUESTION}" in body
    )
    # A legacy request that was told no plan keeps no target list to copy
    # from: the reply example carries an id of its own, but no list to
    # bind against and no target question to answer.
    legacy_body = extraction_messages(task, run, evidence_chars=200)[1].content
    assert "# Planned targets" not in legacy_body
    assert FORECAST_TARGET_QUESTION not in legacy_body


@pytest.mark.asyncio
async def test_an_unplanned_target_id_is_recorded_on_the_run(
    tracker: Tracker,
) -> None:
    """The drop is visible in the run's own error record, not only in a log."""
    state = _forecast_plan_state()
    completer = ScriptedCompleter(
        decisions=_forecast_reading_decisions(),
        # The bound finding answers topic-02's target, so topic-01's own
        # required obligation is left unbound and the pass buys its one
        # bounded re-ask; it finds nothing more and this fixture is about the
        # drop.
        outputs=[
            _forecast_reply_with_an_invented_target_id,
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
        sub_topic_concurrency=1,
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    (finding,) = outcome.result.findings
    assert finding.target_ids == [FORECAST_TARGET_ID]
    recorded = [
        error for error in outcome.errors
        if error.error_type == "researcher_unplanned_target"
    ]
    assert [error.details["dropped_target_ids"] for error in recorded] == [
        ['finding 1: dropped target id "topic-09-target-01" '
         "(it is not a planned target)"]
    ]


@pytest.mark.asyncio
async def test_a_figure_with_no_value_or_unit_is_dropped_not_the_finding(
    tracker: Tracker,
) -> None:
    """The drop is its own error, never the "malformed finding" one.

    A figure a draft could not usably state is not a reason to distrust the
    finding it came from: the finding is kept, admitted with its other
    figure, and the drop is recorded under its own error type so an
    operator reading the run's errors is never told a finding was dropped
    that was not.
    """
    state = _forecast_plan_state()
    completer = ScriptedCompleter(
        decisions=_forecast_reading_decisions(),
        # The bound finding answers topic-02's target, so topic-01's own
        # required obligation is left unbound and the pass buys its one
        # bounded re-ask; it finds nothing more and this fixture is about the
        # unusable figure.
        outputs=[
            _forecast_reply_with_an_unusable_figure,
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
        sub_topic_concurrency=1,
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    (finding,) = outcome.result.findings
    assert finding.figures == [
        FindingFigure(value="19.6", unit="GW", kind="forecast")
    ]
    assert not any(
        error.error_type == "researcher_invalid_finding"
        for error in outcome.errors
    )
    recorded = [
        error for error in outcome.errors
        if error.error_type == "researcher_dropped_figure"
    ]
    assert [error.details["dropped_figures"] for error in recorded] == [
        ["finding 1: figure 1 has no value or unit"]
    ]


@pytest.mark.asyncio
async def test_a_reads_own_target_line_never_costs_the_run_its_evidence(
    tracker: Tracker,
) -> None:
    """Naming the coverage id is a binding mistake, not a finding to delete.

    ``targets=topic-01`` is the id the packet prints on every read and
    evidence unit, so it is the likeliest wrong id an extraction can copy.
    Dropping the finding over it would turn one vocabulary slip into a run
    with no evidence at all, so the id is dropped and recorded, the finding is
    kept unbound, and attribution falls back to the sub-topic that fetched the
    read — exactly the reading that predates this binding.
    """
    state = _forecast_plan_state()
    completer = ScriptedCompleter(
        decisions=_forecast_reading_decisions(),
        # The finding is kept unbound, so topic-01's own required obligation
        # is left unanswered and the pass buys its one bounded re-ask; it
        # finds nothing more and this fixture is about the id that was kept.
        outputs=[
            _forecast_reply_naming_the_coverage_id,
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
        sub_topic_concurrency=1,
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    (finding,) = outcome.result.findings
    assert finding.target_ids == []
    assert finding.related_sub_topic == TOPIC_01_TITLE
    recorded = [
        error for error in outcome.errors
        if error.error_type == "researcher_unplanned_target"
    ]
    assert [error.details["dropped_target_ids"] for error in recorded] == [
        ['finding 1: dropped target id "topic-01" '
         "(it is not a planned target)"]
    ]


def _acquisition_state_line(completer: ScriptedCompleter) -> str:
    """The packet's own acquisition state row, as the model read it."""
    packet = completer.react_calls[0].messages[1].content
    for line in packet.splitlines():
        if "remaining_model_turns=" in line:
            return line
    raise AssertionError("the packet carried no acquisition state line")


def _turn_field(line: str) -> str:
    """The turn count one rendered state row carries."""
    return line.split("remaining_model_turns=")[1].split()[0]


@pytest.mark.asyncio
async def test_an_extra_pass_whose_topics_have_spent_their_budget_opens_no_loop(
    tracker: Tracker,
) -> None:
    """The pass bought for a missing target must not spend turns it cannot use.

    A sub-topic's ``remaining_calls`` is the run's, not one pass's:
    ``merge_acquisition_states`` keeps the minimum and the state carries it, so
    a sub-topic whose first pass used its whole budget resumes at zero. The
    policy then refuses every call ("the acquisition budget of 'X' is spent")
    while the loop still spends its seven model turns — no call, no evidence,
    and about two and a half minutes of a thirty-minute budget, for exactly the
    sub-topics the pass was bought for. Nothing can be called and no read is
    owed, so the pass is not opened at all and the refusal is recorded.
    """
    completer = ScriptedCompleter()
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
    state = _planned_state(
        extra_pass_target_ids=["topic-02-target-01"],
        acquisition_state_by_target={
            "topic-02": AcquisitionState(target_id="topic-02", remaining_calls=0)
        },
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert completer.calls == []
    assert outcome.result.findings == []
    assert (outcome.react.iterations, outcome.react.tool_calls) == (0, 0)
    unfunded = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_extra_pass_unfunded"
    ]
    assert [error.recoverable for error in unfunded] == [True]
    assert unfunded[0].details == {
        "targets": ["topic-02-target-01"],
        "sub_topics": ["topic-02"],
        "reason": "acquisition_budget_spent",
    }
    completed = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.research.completed"
    ]
    assert [event.metadata["sub_topics_researched"] for event in completed] == [0]


@pytest.mark.asyncio
async def test_a_reused_acquisition_state_gets_the_new_loops_turn_cap(
    tracker: Tracker,
) -> None:
    """The turn cap is per loop; the call budget is the run's.

    ``start_turn`` decrements ``remaining_model_turns`` once per model turn, so
    the state a later pass resumes carries the *previous* loop's countdown —
    zero, for a target an earlier pass worked through — while the packet renders
    it beside "Iteration 1 of 4". The model then reads a loop with no turns left
    over a loop that has all of them, which invites it to stop before spending
    the budget the pass was bought for. The assertion below is the equality that
    matters: the resumed loop's first request reads the same turn count as a
    fresh loop's first request, while ``remaining_calls`` stays the run's.
    """
    fresh_completer = ScriptedCompleter(
        decisions=[finish("Nothing more is needed.", "Not established.")]
    )
    fresh_agent = _researcher(
        tracker, fresh_completer, sub_topic_concurrency=1, max_sub_topics=1
    )
    async with tracker.session_span("session-1", "q"):
        await fresh_agent.run(_planned_state())

    resumed_completer = ScriptedCompleter(
        decisions=[finish("Nothing more is needed.", "Not established.")]
    )
    resumed_agent = _researcher(
        tracker, resumed_completer, sub_topic_concurrency=1, max_sub_topics=1
    )
    state = _planned_state(
        extra_pass_target_ids=["topic-02-target-01"],
        acquisition_state_by_target={
            "topic-02": AcquisitionState(
                target_id="topic-02",
                remaining_calls=3,
                remaining_model_turns=0,
            )
        },
    )

    async with tracker.session_span("session-2", "q"):
        outcome = await resumed_agent.run(state)

    fresh_line = _acquisition_state_line(fresh_completer)
    resumed_line = _acquisition_state_line(resumed_completer)
    cap = resumed_agent.config.max_iterations

    assert "remaining_model_turns=0" not in resumed_line
    assert _turn_field(resumed_line) == _turn_field(fresh_line)
    assert _turn_field(resumed_line) == str(cap - 1)

    resumed = outcome.state_update["acquisition_state_by_target"]["topic-02"]
    assert resumed.remaining_calls == 3
    assert resumed.remaining_model_turns == cap - 1


@pytest.mark.asyncio
async def test_an_extra_pass_whose_topic_still_has_calls_opens_its_loop(
    tracker: Tracker,
) -> None:
    """The counterpart: a resumed budget with calls left is a pass worth buying."""
    completer = ScriptedCompleter(
        decisions=[finish("Nothing more is needed.", "Not established.")],
    )
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
    state = _planned_state(
        extra_pass_target_ids=["topic-02-target-01"],
        acquisition_state_by_target={
            "topic-02": AcquisitionState(target_id="topic-02", remaining_calls=3)
        },
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["T2"]
    assert [
        error
        for error in outcome.errors
        if error.error_type == "researcher_extra_pass_unfunded"
    ] == []


@pytest.mark.asyncio
async def test_an_extra_pass_with_owed_reads_opens_its_loop(
    tracker: Tracker,
) -> None:
    """A spent budget is not the same as nothing to do: owed reads can be mined.

    An extraction that was deferred (a truncated reply, or an outage) leaves its
    reads in ``pending_extraction_ids``, and mining them needs no tool call, so
    the pass is opened even though no call is possible.
    """
    completer = ScriptedCompleter(
        decisions=[finish("Nothing more is needed.", "Not established.")],
    )
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
    state = _planned_state(
        extra_pass_target_ids=["topic-02-target-01"],
        acquisition_state_by_target={
            "topic-02": AcquisitionState(
                target_id="topic-02",
                remaining_calls=0,
                pending_extraction_ids=[QEC_READ.read_id],
            )
        },
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["T2"]


@pytest.mark.asyncio
async def test_a_first_pass_is_never_skipped_for_a_spent_budget(tracker: Tracker) -> None:
    """The guard is an extra pass's alone: a first pass still gets its turn.

    A resumed state with no calls left cannot arise before the run has spent
    them, but the guard must not read any pass as the extra one: a first pass
    whose state was restored with a spent budget still runs, so the refusal is
    never a silent replacement for research the run has not attempted.
    """
    completer = ScriptedCompleter(
        decisions=[
            finish("Nothing more is needed.", "Not established."),
            finish("Nothing more is needed.", "Not established."),
            finish("Nothing more is needed.", "Not established."),
        ],
    )
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
    state = _planned_state(
        acquisition_state_by_target={
            "topic-02": AcquisitionState(target_id="topic-02", remaining_calls=0)
        },
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == ["T1", "T2", "T3"]


@pytest.mark.asyncio
async def test_a_read_fetched_for_one_topic_yields_another_topics_finding(
    tracker: Tracker,
) -> None:
    """The audited run's own miss, replayed end to end on its stored read.

    EIA 64705 was read for the 2024-additions sub-topic and its 19.6 GW
    sentence was never mined for the forecast topic. Here the read is fetched
    for topic-01 and extraction binds a finding to topic-02's forecast target,
    carrying the vintage that lets a reader tell it from the older 18.2 GW
    forecast. The reply is derived from the request, so it also proves the
    packet showed the sentence and the target.
    """
    state = _forecast_plan_state()
    completer = ScriptedCompleter(
        decisions=_forecast_reading_decisions(),
        # The bound finding answers topic-02's target, so topic-01's own
        # required obligation is left unbound and the pass buys its one
        # bounded re-ask; it finds nothing more and this fixture is about the
        # binding that crossed topics.
        outputs=[_forecast_reply, SubTopicFindingsDraft(findings=[])],
    )
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
        sub_topic_concurrency=1,
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    (finding,) = outcome.result.findings
    # Fetched for topic-01, mined for topic-02's target.
    assert finding.related_sub_topic == TOPIC_01_TITLE
    assert finding.target_ids == [FORECAST_TARGET_ID]
    assert finding.vintage == "January 2025 preliminary inventory"
    assert finding.statement_date == "2025-03-12"
    assert finding.data_period == "2025"
    # And the request the model answered carried both.
    request_body = completer.calls[0][2][1].content
    assert EIA_64705_FORECAST_SENTENCE in request_body
    assert (
        f"- {FORECAST_TARGET_ID} [topic-02: {TOPIC_02_TITLE}] [required]: "
        f"{FORECAST_TARGET_QUESTION}" in request_body
    )


# ---------------------------------------------------------------------------
# The run's own read of the page the audited run missed
# ---------------------------------------------------------------------------
#
# EIA, "U.S. battery capacity increased 66% in 2024" (Today in Energy id 64705,
# March 12 2025), as the run stored it: the navigation header first, then the
# sentences, and the 2025 forecast last. The forecast sentence is the one the
# audited run never extracted; its 18.2 GW predecessor came from another page
# and an older inventory. The text is the stored read verbatim, so the fixture
# cannot drift away from the evidence it stands in for.

EIA_64705_URL = "https://eia.gov/todayinenergy/detail.php?id=64705"
EIA_64705_TITLE = (
    "U.S. battery capacity increased 66% in 2024 - U.S. Energy Information "
    "Administration (EIA)"
)
EIA_64705_FORECAST_SENTENCE = (
    "In 2025, capacity growth from battery storage could set a record as "
    "operators report plans to add 19.6 GW of utility-scale battery storage "
    "to the grid, according to our January 2025 preliminary electric "
    "generator inventory data."
)
EIA_64705_FORECAST_EXCERPT = (
    "operators report plans to add 19.6 GW of utility-scale battery storage "
    "to the grid, according to our January 2025 preliminary electric "
    "generator inventory data."
)
EIA_64705_BODY = (
    "Skip to sub-navigation U.S. Energy Information Administration - EIA - "
    "Independent Statistics and Analysis Menu Statistics Analysis Tools "
    "Education News Search Today in Energy Skip to page content Recent "
    "articles Browse by tag liquid fuels natural gas electricity oil/petroleum "
    "production/supply crude oil consumption/demand generation prices map "
    "states exports/imports international coal renewables weather "
    "forecasts/projections gasoline capacity steo (short-term energy outlook) "
    "Prices Archive About Glossary FAQS In-brief analysis March 12, 2025 U.S. "
    "battery capacity increased 66% in 2024 Data source: U.S. Energy "
    "Information Administration, Preliminary Monthly Electric Generator "
    "Inventory , January 2025 In the United States, cumulative utility-scale "
    "battery storage capacity exceeded 26 gigawatts (GW) in 2024, according "
    "to our January 2025 Preliminary Monthly Electric Generator Inventory . "
    "Generators added 10.4 GW of new battery storage capacity in 2024, the "
    "second-largest generating capacity addition after solar. Even though "
    "battery storage capacity is growing fast, in 2024 it was only 2% of the "
    "1,230 GW of utility-scale electricity generating capacity in the United "
    "States. In 2025, capacity growth from battery storage could set a record "
    "as operators report plans to add 19.6 GW of utility-scale battery "
    "storage to the grid, according to our January 2025 preliminary electric "
    "generator inventory data."
)

TOPIC_01_TITLE = "2024 U.S. grid-scale battery capacity additions (reported actuals)"
TOPIC_02_TITLE = "Latest 2025 forecasts for U.S. grid-scale battery capacity additions"
FORECAST_TARGET_ID = "topic-02-target-01"
_FIXTURE_TARGET_QUESTION = "How much battery capacity was added in 2024?"
FORECAST_TARGET_QUESTION = (
    "What does the most recent federal statistical-agency forecast project for "
    "U.S. utility-scale battery storage capacity additions in 2025, in MW?"
)


def _plan_target(
    target_id: str, question: str, unit_dimension: str | None = None
) -> EvidenceTarget:
    """One planned obligation, named the way ``planner.target_id_for`` names it.

    ``unit_dimension`` is the base the target's own question measures in, as
    the planner stamps it: ``None`` for a question that names no unit.
    """
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=target_id.rsplit("-target-", 1)[0],
        question=question,
        measure="battery storage capacity additions",
        unit_dimension=unit_dimension,
        required=True,
    )


def _plan_topics() -> list[SubTopic]:
    """The run's plan, trimmed to the two sub-topics in play here."""
    return [
        _sub_topic(TOPIC_01_TITLE, 1, coverage_id="topic-01").model_copy(
            update={
                "evidence_targets": [
                    _plan_target(PLANNED_TARGET_ID, _FIXTURE_TARGET_QUESTION)
                ]
            }
        ),
        _sub_topic(TOPIC_02_TITLE, 2, coverage_id="topic-02").model_copy(
            update={
                "evidence_targets": [
                    _plan_target(
                        FORECAST_TARGET_ID,
                        FORECAST_TARGET_QUESTION,
                        unit_dimension="power",
                    )
                ]
            }
        ),
    ]


def _forecast_plan_targets() -> list[EvidenceTarget]:
    return [target for topic in _plan_topics() for target in topic.evidence_targets]


def _forecast_plan_state() -> ResearchState:
    return _state(sub_topics=_plan_topics())


def _forecast_reading_decisions() -> list[object]:
    """Topic-01 reads the EIA page; topic-02 is left with nothing to extract."""
    return [
        use_tool(
            "Find the 2024 addition.",
            "web_search",
            '{"query": "eia battery storage capacity 2024"}',
        ),
        use_tool(
            "Read the EIA page.",
            "web_scraper",
            f'{{"url": "{EIA_64705_URL}"}}',
        ),
        finish("The page carries the answer.", "10.4 GW in 2024."),
        finish("No source for the forecast topic.", "Not established."),
    ]


def _packet_locator_for(figure: str, packet: str) -> tuple[str, str]:
    """The ``(read_id, locator)`` of the passage carrying ``figure``.

    A scripted reply cannot know a read id before the request is built, so it
    reads one out of the packet the way the model must — and fails loudly when
    the packet never showed the passage, which is the whole failure this
    fixture exists to catch. Rows are split on their ``- `` prefix rather than
    on newlines: a stored passage keeps the source's own line breaks, so one
    row can span several lines. Whole-page admission (fix-round 3) usually
    admits a matching passage as its own unit row (``evidence_id=...``)
    rather than the raw passage dump (``passage read_id=...``); this reads
    either shape.
    """
    for row in re.split(r"(?m)^- ", packet):
        if figure not in row:
            continue
        if not row.startswith(("passage ", "evidence_id=")):
            continue
        read_id = re.search(r"read_id=(\S+)", row)
        locator = re.search(r"locator=(\S+)", row)
        assert read_id is not None and locator is not None, row
        return read_id.group(1), locator.group(1)
    raise AssertionError(f"the extraction packet showed no passage with {figure}")


def _eia_forecast_draft(
    messages: list[ChatMessage],
    *,
    target_ids: Sequence[str] = (FORECAST_TARGET_ID,),
    figures: Sequence[FindingFigureDraft] = (),
) -> SubTopicFindingsDraft:
    """The draft a model that read the packet would return for EIA 64705."""
    read_id, locator = _packet_locator_for("19.6 GW", messages[1].content)
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "EIA's March 12, 2025 In-brief analysis reports that "
                    "operators plan to add 19.6 GW of utility-scale battery "
                    "storage to the grid in 2025, according to EIA's January "
                    "2025 preliminary electric generator inventory data."
                ),
                source_url=EIA_64705_URL,
                source_title=EIA_64705_TITLE,
                confidence=0.95,
                read_id=read_id,
                locator=locator,
                snippet=EIA_64705_FORECAST_EXCERPT,
                target_ids=list(target_ids),
                figures=list(figures),
                data_period="2025",
                statement_date="2025-03-12",
                vintage="January 2025 preliminary inventory",
            )
        ]
    )


def _forecast_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    del schema
    return _eia_forecast_draft(messages)


def _forecast_reply_with_an_invented_target_id(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    del schema
    return _eia_forecast_draft(
        messages, target_ids=[FORECAST_TARGET_ID, "topic-09-target-01"]
    )


def _forecast_reply_with_an_unusable_figure(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    del schema
    return _eia_forecast_draft(
        messages,
        figures=[
            FindingFigureDraft(value="", unit="GW"),
            FindingFigureDraft(value="19.6", unit="GW", kind="forecast"),
        ],
    )


def _forecast_reply_naming_the_coverage_id(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The reply of a model that copied the read's own ``targets=`` line.

    That line names the sub-topic that fetched the read (``topic-01``), not a
    planned target, and it is the one id the packet prints next to every read
    and evidence unit — the likeliest way an extraction names the wrong
    vocabulary. The id is dropped; the finding it was attached to is not.
    """
    del schema
    return _eia_forecast_draft(messages, target_ids=["topic-01"])


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

    assert "MUST copy read_id and locator" in acquisition_body
    assert "MUST carry a snippet" in acquisition_body
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
    completer: ScriptedCompleter | TargetKeyedCompleter,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    http: httpx.AsyncClient | None = None,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
    config: AgentRuntimeConfig | None = None,
    tools: Sequence[BaseTool] | None = None,
    sub_topic_concurrency: int | None = None,
    read_admission_chars: int | None = None,
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=(
            tools
            if tools is not None
            else research_tools(tracker, search=search, memory=memory, http=http)
        ),
        config=config or AgentRuntimeConfig(max_iterations=4, tool_budget=4),
        max_sub_topics=max_sub_topics,
        sub_topic_concurrency=sub_topic_concurrency,
        read_admission_chars=read_admission_chars,
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

    assert "only a page or document you read in this loop is evidence" in prompt
    assert "Prefer primary sources" in prompt
    # The loop the policy actually enforces is stated: the packet's own
    # allowed_actions line governs, two searches are followed by a read, and
    # only a discovered URL can be read (review RES-1 §1, RES-2 §1).
    assert "The acquisition state line in this request is binding" in prompt
    assert "Two searches in a row, in one turn or across turns, are followed" in prompt
    # The loop reports no finding and records no date: an extraction step
    # reads the pages it read (review RES-1 §3).
    assert "a separate extraction step reads" in prompt
    assert "Record publication date" not in prompt
    assert "save_to_memory" not in prompt


def test_the_selection_query_is_built_from_target_questions_and_the_research_question(
    tracker: Tracker,
) -> None:
    """D3: the query must ask what a page has to state, in the plan's own
    target questions and the run's research question -- not the sub-topic's
    title and success criteria in the planner's words, which the audited run
    showed miss the pages' own vocabulary.
    """
    agent = _researcher(tracker, ScriptedCompleter())
    target = make_target(
        target_id="topic-01-target-01",
        question="Which product has the clearest voice pickup in calls?",
        required=True,
    )
    sub_topic = SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="Alpha matters.",
        search_queries=["Alpha 2025"],
        success_criteria=["A named source about Alpha."],
        priority=1,
        evidence_targets=[target],
    )
    task = SubTopicTask(instruction="Gather evidence for Alpha.", sub_topic=sub_topic)
    agent._run_source_state = ResearchState(
        session_id="session-1",
        original_question="What has the best call quality in 2026?",
        sub_topics=[sub_topic],
    )

    policy = agent._policy_for_task(task)

    assert "clearest voice pickup" in policy.query
    assert "best call quality" in policy.query
    assert "Alpha" not in policy.query


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
        _state(
            memory_context=MemorySnapshot(
                suggested_strategies=["Prefer peer-reviewed sources."]
            )
        )
    )

    task = agent.sub_topic_task(
        base, _sub_topic("Alpha", 1), ["https://example.test/one"]
    )

    assert isinstance(task, SubTopicTask)
    assert task.sub_topic.title == "Alpha"
    assert task.existing_sources == ["https://example.test/one"]
    assert 'Gather evidence for the sub-topic "Alpha"' in task.instruction
    assert "- Prefer peer-reviewed sources." in task.guidance
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(
        task, _recalled_fact_run(nested=nested)
    )

    assert findings == []
    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(
        task, _search_only_run()
    )

    assert findings == []
    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert errors == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert extraction_failure == ""
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert errors[0].error_type == "researcher_invalid_finding"
    assert errors[0].recoverable is True
    assert errors[0].details["rejected"] == ["finding 1: invalid confidence"]
    assert extraction_failure == ""


@pytest.mark.asyncio
async def test_one_truncated_extraction_does_not_end_the_pass(
    tracker: Tracker,
) -> None:
    """A truncation is a fact about one extraction, not about the provider.

    The extraction call's output-limit truncation went down the non-recoverable
    provider path, and ``extraction_provider_error`` stops the pass by design —
    so every sub-topic still queued behind the truncated one was never
    attempted (``provider_failure_stopped_processing``) and a per-sub-topic
    truncation cost the rest of the plan its evidence. The provider answered
    here; the reply was too long, so the sub-topic's reads stay owed
    (``pending_extraction_ids``) and the pass carries on to the next sub-topic.
    """
    state = _forecast_plan_state()
    completer = ScriptedCompleter(
        decisions=_forecast_reading_decisions(),
        outputs=[_output_limit_error()],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
        sub_topic_concurrency=1,
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == [TOPIC_01_TITLE, TOPIC_02_TITLE]
    truncated = [
        error
        for error in outcome.errors
        if error.error_type == "researcher_extraction_output_limit"
    ]
    assert [error.recoverable for error in truncated] == [True]
    assert [
        error for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    ] == []
    assert "researcher_extraction_provider_error" not in {
        error.error_type for error in outcome.errors
    }


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
    caller via the failure kind rather than letting the exception escape.
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

    findings, errors, extraction_failure, obligation = await agent.extract_findings(task, run)

    assert findings == []
    assert extraction_failure == "provider"
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
                snippet=QEC_PASSAGE,
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
async def test_a_second_researcher_does_not_remint_used_audit_ids(
    tracker: Tracker,
) -> None:
    """A fresh ``ResearcherAgent`` built against a state that already carries
    boundary audits must not remint sequence numbers an earlier instance
    already used for the same ``(job, agent, operation)`` triple.

    Bug 3 (latent, currently unreachable in production): each agent's audit
    sequence counter lives only on the instance, seeded to 0 in ``__init__``
    and never re-seeded from ``state.boundary_audits`` in ``run()``. A second
    construction against a state that already holds audits therefore re-mints
    ids ``merge_boundary_audits`` has already claimed for this agent and
    operation, with different contents, and ``merge_research_state`` raises
    ``EvidenceIdentityConflict``.
    """
    first_completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[_findings_draft()],
    )
    first_agent = _researcher(tracker, first_completer)
    state = _state(sub_topics=[_sub_topic("Alpha", 1, coverage_id="topic-01")])

    async with tracker.session_span("session-1", "q"):
        first_outcome = await first_agent.run(state)
    merged = merge_research_state(state, first_outcome.state_update)
    first_ids = set(merged.boundary_audits)
    # Sanity: the fixture must actually exercise the minting path, or the
    # rest of this test would pass vacuously.
    assert first_ids

    # A fresh instance, as a checkpoint restore or any other re-construction
    # against already-populated state would produce. Its own
    # ``_run_audit_sequence`` starts at 0 again. It works a *different*
    # sub-topic/target than the first run so its own acquisition state is not
    # carried over from ``merged`` (which would just skip the read as already
    # satisfied for that target) — but it scrapes the same URL, which the
    # first run's ``read_records`` already cached, so this run's read is
    # served from the shared cache. That is exactly the boundary-audit path
    # (``AcquisitionPolicy._read_observed``'s cache-admission branch) a
    # checkpoint-restored second instance would hit for any repeat read
    # anywhere in the job, and it mints a manifest whose id depends only on
    # (job, agent, operation, sequence) — never on the target — so a sequence
    # restarted at 0 collides with the first run's own sequence-0 manifest,
    # which was minted for a different target and therefore differs in
    # content.
    second_completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    second_agent = _researcher(tracker, second_completer)
    # The same merged state a checkpoint restore would hand a freshly
    # constructed agent, except the sub-topic list now names the new target a
    # later pass (for example, a Critic gap) would add — ``topic-01`` is
    # already fully read and would otherwise short-circuit with no new tool
    # call at all.
    state_for_second_run = merged.model_copy(
        update={"sub_topics": [_sub_topic("Beta", 1, coverage_id="topic-02")]}
    )

    async with tracker.session_span("session-1", "q"):
        second_outcome = await second_agent.run(state_for_second_run)
    final = merge_research_state(state_for_second_run, second_outcome.state_update)

    second_new_ids = set(final.boundary_audits) - first_ids
    # Sanity: the second run must also have minted something new, or the
    # disjointness assertion below would pass vacuously too.
    assert second_new_ids
    assert second_new_ids.isdisjoint(first_ids)
    assert len(final.boundary_audits) > len(merged.boundary_audits)


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

    The cap plus three distinct claims collapse under the cap, along with
    three restatements of one claim. An operator reading only the event
    stream has to be able to see both, and see that the retained findings
    came from a single source.

    The distinct claims are distinct *statements* — one sentence each of
    the page — because that is what makes a finding its own evidence: three
    drafts that mine the one sentence already mined are restatements, whatever
    their content says (``identity.deduplicate_findings``).
    """
    restated = "The measured error rate fell by 50 percent in 2025."
    sentences = [
        *(
            f"Claim {index}: the measured error rate fell by {index}0 percent in 2025."
            for index in range(1, MAX_FINDINGS_PER_SUB_TOPIC + 3)
        ),
        restated,
    ]
    # One passage per statement, and the run reads them from its own registry:
    # a read the session already holds is a cache reuse, so the drafts below
    # name the same read and locators the loop works from.
    passages = {f"chunk-{index}": sentence for index, sentence in enumerate(sentences)}
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=QEC_SOURCE_URL,
        resolved_url=QEC_SOURCE_URL,
        title=QEC_TITLE,
        retrieved_at=EXTRACTED_AT,
        text=" ".join(sentences),
        passages=passages,
        extraction_complete=True,
    ).model_copy(update={"target_ids": ["topic-01"]})
    def claim_draft(
        *,
        snippet: str,
        locator: str,
        content: str,
        confidence: float,
        read: ReadRecord = read,
    ) -> FindingDraft:
        return FindingDraft(
            content=content,
            source_url=QEC_SOURCE_URL,
            source_title=QEC_TITLE,
            confidence=confidence,
            read_id=read.read_id,
            locator=locator,
            snippet=snippet,
            target_ids=["topic-01"],
        )

    draft = SubTopicFindingsDraft(
        findings=[
            # Three restatements of the first claim: one sentence, three
            # confidences, so two are dropped as duplicates.
            claim_draft(
                snippet=restated,
                locator=f"chunk-{len(sentences) - 1}",
                content="Duplicated claim.",
                confidence=confidence,
            )
            for confidence in (0.4, 0.9, 0.6)
        ]
        + [
            claim_draft(
                snippet=sentence,
                locator=f"chunk-{index}",
                content=f"Distinct claim {index}.",
                confidence=0.5,
            )
            for index, sentence in enumerate(sentences[:-1])
        ]
    )
    completer = ScriptedCompleter(
        decisions=_search_and_scrape_decisions(),
        outputs=[draft],
    )
    agent = _researcher(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(
            _state(
                sub_topics=[_sub_topic("Alpha", 1)],
                read_records={read.read_id: read},
            )
        )

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    assert completed.metadata["findings"] == MAX_FINDINGS_PER_SUB_TOPIC
    assert completed.metadata["findings_dropped_duplicate"] == 2
    assert completed.metadata["findings_dropped_cap"] == 3
    assert completed.metadata["sources_retained"] == 1

    assert len(outcome.result.findings) == MAX_FINDINGS_PER_SUB_TOPIC
    assert max(
        finding.confidence for finding in outcome.result.findings
    ) == 0.9


_LONG_BODY = (
    "A named source about Alpha confirms queue delay commissioning cost. " * 130
)
_LONG_URL = "https://example.test/long-study.md"


def _document_client(document: str) -> httpx.AsyncClient:
    """A client serving one markdown document, for layout-scoped tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/markdown; charset=utf-8"},
            text=document,
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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

    The document is read as two passages and both are selected. The extracted
    finding cites the first only, so the second is not "used" — the earlier
    URL-level check would have called it used because a different passage of
    the same read produced a finding.
    """
    from deep_research.agents.acquisition import split_read_body

    document = (
        (
            "A named source about Alpha confirms queue delay commissioning "
            "cost. " * 6
        )
        + "\n\n"
        + ("A second section about Beta reports nothing relevant. " * 6)
    )
    passages = {
        f"chunk-{index}": passage
        for index, passage in enumerate(split_read_body(document))
    }
    assert sorted(passages) == ["chunk-0", "chunk-1"]
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
                    text=document,
                    passages=passages,
                    extraction_complete=True,
                ).read_id,
                locator="chunk-0",
                snippet=passages["chunk-0"],
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
        http=_document_client(document),
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
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=2),
        sub_topic_concurrency=1,
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

    The refusal comes from the acquisition policy, which holds the target's
    budget — the same override, read through
    ``AgentRuntimeConfig.tool_budget_for`` — because that budget belongs to
    the run and is resumed by every later loop for the same target, while a
    fresh loop's own gate always starts full.
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
    # The second scripted action is refused for the spent acquisition budget,
    # and the loop ends when the model — told no further calls are possible —
    # finishes.
    assert outcome.react.errors[0].error_type == "agent_tool_policy_rejected"
    assert any(
        "budget" in step.observation.summary
        for step in outcome.react.steps
        if step.observation is not None
    )
    assert outcome.react.stop_reason == "finished"


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
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
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
    *also* succeeds, but its extraction call cannot reach the provider — the
    failure specifically identified as escaping ``run`` uncaught and
    destroying every finding collected so far. Sub-topic Gamma must never be
    started at all.

    The outage is a transport failure, not an output limit: a truncated reply
    is the provider answering, and it no longer stops the pass
    (``test_one_truncated_extraction_does_not_end_the_pass`` covers that
    shape). This test's guard is the unreachable provider, which must still
    stop the queued sub-topics rather than spend their turns on a dead
    transport.
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
        outputs=[_findings_draft(), ProviderTimeoutError("timed out")],
    )
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        sub_topic_concurrency=1,
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
    assert provider["kind"] == "provider_timeout"

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
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
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
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
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
    # Order-pinned: the order-based ScriptedCompleter needs one loop at a time (D9).
    agent = _researcher(tracker, completer, sub_topic_concurrency=1)
    state = _state(
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 2)]
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(state)

    second_loop_body = completer.react_calls[1].messages[1].content
    assert "(no notes yet)" in second_loop_body


# ---------------------------------------------------------------------------
# The measure unit a target asks for, and the one bounded re-extraction for it
# ---------------------------------------------------------------------------
#
# The audited run read WoodMac's market monitor and mined nothing from it: the
# passage stating "12,314 megawatts (MW) and 37,143 megawatt hours (MWh)
# deployed" in 2024 was selected, produced no finding, was disposed of as
# "irrelevant", and the plan's own MWh target stayed unanswered — the report
# then said no MWh figure "appears among the checked claims". The fixture is
# that shape: the release's lede, then the sentence, read for a topic whose
# planned target asks for the MWh figure. It is deliberately longer than one
# passage, so the lede and the sentence are two selected units and the retry
# has to be the thing that mines the second one.

_QUANTITY_URL = (
    "https://www.woodmac.com/press-releases/us-energy-storage-monitor-2024"
)
_QUANTITY_TITLE = "U.S. Energy Storage Monitor: 2024 year in review"
_QUANTITY_LEDE = (
    "Wood Mackenzie's U.S. Energy Storage Monitor is the industry's quarterly "
    "accounting of the grid-scale, commercial, and residential battery "
    "storage markets, and this year in review reports the largest annual "
    "deployment on record across every segment the monitor covers. "
) * 2
_QUANTITY_SENTENCE = (
    "In 2024, the United States deployed 12,314 megawatts (MW) and "
    "37,143 megawatt hours (MWh) of grid-scale battery storage across all "
    "segments, according to the March 4, 2025 edition."
)
_QUANTITY_BODY = f"{_QUANTITY_LEDE}\n\n{_QUANTITY_SENTENCE}"
_QUANTITY_TARGET_QUESTION = (
    "How many megawatt hours of grid-scale battery storage energy capacity "
    "was deployed in the United States in 2024?"
)


def _quantity_topic() -> SubTopic:
    """The topic whose own target asks for the MWh figure."""
    return _sub_topic(
        "Grid-scale battery storage megawatt hours deployed in 2024", 1
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=PLANNED_TARGET_ID,
                    coverage_id="topic-01",
                    question=_QUANTITY_TARGET_QUESTION,
                    measure=(
                        "grid-scale battery storage energy capacity "
                        "deployed, in MWh"
                    ),
                    unit_dimension="energy",
                    required=True,
                )
            ]
        }
    )


def _quantity_decisions() -> list[object]:
    return [
        use_tool(
            "Find the market monitor.",
            "web_search",
            '{"query": "energy storage monitor 2024"}',
        ),
        use_tool(
            "Read the release.",
            "web_scraper",
            f'{{"url": "{_QUANTITY_URL}"}}',
        ),
        finish("The release states the deployment.", _QUANTITY_SENTENCE),
    ]


def _packet_passage_for(
    figure: str, packet: str
) -> tuple[str, str, str]:
    """``(read_id, locator, excerpt)`` of the packet's passage stating ``figure``.

    Derived from the request the way a model must, and it fails loudly when
    the packet never carried the passage — the whole failure this fixture
    exists to catch. Whole-page admission (fix-round 3) usually admits a
    matching passage as its own unit row (``evidence_id=...``) rather than
    the raw passage dump (``passage read_id=...``), since a locator with a
    unit is never also dumped; this reads either shape.
    """
    for row in re.split(r"(?m)^- ", packet):
        if figure not in row:
            continue
        if row.startswith("passage "):
            text_key = " text="
        elif row.startswith("evidence_id="):
            text_key = " excerpt="
        else:
            continue
        read_id = re.search(r"read_id=(\S+)", row)
        locator = re.search(r"locator=(\S+)", row)
        assert read_id is not None and locator is not None, row
        return (
            read_id.group(1),
            locator.group(1),
            row.split(text_key, 1)[1].rstrip("\n"),
        )
    raise AssertionError(f"the extraction packet showed no passage with {figure}")


def _quantity_retry_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The MWh finding the bounded second extraction is asked to return."""
    del schema
    read_id, locator, excerpt = _packet_passage_for(
        "37,143 megawatt hours", messages[1].content
    )
    assert excerpt == _QUANTITY_SENTENCE
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "Wood Mackenzie's U.S. Energy Storage Monitor reports "
                    "that the United States deployed 12,314 MW and 37,143 "
                    "MWh of grid-scale battery storage in 2024."
                ),
                source_url=_QUANTITY_URL,
                source_title=_QUANTITY_TITLE,
                confidence=0.9,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[PLANNED_TARGET_ID],
                data_period="2024",
            )
        ]
    )


def _quantity_scoped_retry_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The finding the retry returns for the monitor's own segment total."""
    del schema
    read_id, locator, excerpt = _packet_passage_for(
        "37,143 megawatt hours", messages[1].content
    )
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "The U.S. Energy Storage Monitor reports that the United "
                    "States deployed 12,314 MW of energy storage in 2024, "
                    "measured across all segments."
                ),
                source_url=_QUANTITY_URL,
                source_title=_QUANTITY_TITLE,
                confidence=0.9,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[PLANNED_TARGET_ID],
                data_period="2024",
                attributed_issuer="Wood Mackenzie",
                attribution_quote=(
                    "Wood Mackenzie's U.S. Energy Storage Monitor"
                ),
                measure_scope="all segments",
                release_date="2025-03-04",
            )
        ]
    )


def _packet_evidence_locators(packet: str) -> list[str]:
    """The locator of every evidence row one rendered packet carries, in order."""
    return [
        match.group(1)
        for match in re.finditer(
            r"(?m)^- evidence_id=\S+ read_id=\S+ locator=(\S+)", packet
        )
    ]


def _quantity_agent(tracker: Tracker, completer: ScriptedCompleter) -> ResearcherAgent:
    """The researcher that reads the market monitor for the MWh topic."""
    return _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_QUANTITY_TITLE, url=_QUANTITY_URL)]
        ),
        http=page_client(title=_QUANTITY_TITLE, body=_QUANTITY_BODY),
    )


def _extraction_requests(completer: ScriptedCompleter) -> list[str]:
    """The body of every extraction request the run made, in order."""
    return [
        call[2][1].content
        for call in completer.calls
        if call[0] == "SubTopicFindingsDraft"
    ]


@pytest.mark.asyncio
async def test_a_passage_an_earlier_pass_mined_is_not_owed_again(
    tracker: Tracker,
) -> None:
    """The re-extraction is spent on passages the *run* has not mined.

    ``_units_owing_a_figure`` asks for the selected passages no admitted finding
    used — and a later pass started from an empty admitted-key set, so the
    passage an earlier pass had already mined off the same read looked unmined
    and bought the pass's one re-extraction a second time. The keys the run
    already admitted travel into the extraction, so "no admitted finding used
    it" is read against the run's whole record rather than against this pass.
    """
    completer = ScriptedCompleter(
        decisions=_quantity_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[]), _quantity_retry_reply],
    )
    agent = _quantity_agent(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        first = await agent.run(_state(sub_topics=[_quantity_topic()]))

    # The fixture's premise: pass 0 mined the sentence with its one re-ask.
    assert len(_extraction_requests(completer)) == 2
    (earlier,) = first.state_update["raw_findings"]
    assert earlier.locator not in ("", None)

    resumed_completer = ScriptedCompleter(
        decisions=_quantity_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[]), _quantity_retry_reply],
    )
    resumed = _quantity_agent(tracker, resumed_completer)

    async with tracker.session_span("session-2", "q"):
        await resumed.run(
            _state(
                sub_topics=[_quantity_topic()],
                raw_findings=[earlier],
                evidence_units=first.state_update["evidence_units"],
                read_records=first.state_update["read_records"],
            )
        )

    # One request: the extraction that runs, and no owed re-extraction for a
    # passage the run already has a finding from.
    assert len(_extraction_requests(resumed_completer)) == 1


@pytest.mark.asyncio
async def test_a_targets_measure_unit_is_mined_by_one_bounded_re_extraction(
    tracker: Tracker,
) -> None:
    """A selected passage stating the target's own unit is mined again, once.

    The first extraction returned no finding for the passage carrying 37,143
    megawatt hours while the active topic's own target asks for megawatt
    hours: the read the target needed was in hand, and the pass was about to
    dispose of it as irrelevant. One bounded second extraction — over that
    passage alone — is asked for the figure, and its finding is the run's.
    """
    completer = ScriptedCompleter(
        decisions=_quantity_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            _quantity_retry_reply,
        ],
    )
    agent = _quantity_agent(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_quantity_topic()]))

    # The premise of the fixture: the lede and the sentence are two passages,
    # and the sentence is not the lede, so the retry is what mines the figure.
    read = next(iter(outcome.state_update["read_records"].values()))
    carrying = [
        locator
        for locator, text in read.passages.items()
        if "37,143 megawatt hours" in text
    ]
    assert len(carrying) == 1 and carrying[0] != "chunk-0"

    requests = _extraction_requests(completer)
    # One retry, never a loop: the first request and the one bounded second.
    assert len(requests) == 2
    # The first request carried both selected units; the retry carries the
    # owed unit alone, so the model is not asked to find it again in the
    # packet it was lost in.
    assert sorted(_packet_evidence_locators(requests[0])) == ["chunk-0", "chunk-1"]
    assert _packet_evidence_locators(requests[1]) == ["chunk-1"]
    # And the retry says what its packet is for.
    assert _QUANTITY_SENTENCE in requests[1]
    assert "Passages owed a finding" in requests[1]
    assert "Passages owed a finding" not in requests[0]

    (finding,) = outcome.result.findings
    assert finding.target_ids == [PLANNED_TARGET_ID]
    assert "37,143 MWh" in finding.content
    assert finding.source_url == _QUANTITY_URL
    assert finding.data_period == "2024"


@pytest.mark.asyncio
async def test_a_mined_figure_keeps_the_scope_and_attribution_the_page_states(
    tracker: Tracker,
) -> None:
    """The retry mines the tracker's figure with its own segment and body.

    The audited run's extraction had no way to record a figure whose basis
    differs from the target's: the monitor's total is measured across all
    segments, so an extraction reading "grid-scale" refused to state it at
    all and the tracker's own accounting reached no finding. The bounded
    re-extraction is asked for the figure *with* the scope and the body the
    page states, so the claim stage can see the difference instead of a
    figure silently restated in the target's terms.
    """
    completer = ScriptedCompleter(
        decisions=_quantity_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            _quantity_scoped_retry_reply,
        ],
    )
    agent = _quantity_agent(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_quantity_topic()]))

    (finding,) = outcome.result.findings
    assert finding.measure_scope == "all segments"
    assert finding.attributed_issuer == "Wood Mackenzie"
    assert finding.attribution_quote == "Wood Mackenzie's U.S. Energy Storage Monitor"
    assert finding.release_date == "2025-03-04"
    # The retry is where it came from, and the request it answered carries the
    # contract that asks for those fields.
    requests = _extraction_requests(completer)
    assert len(requests) == 2
    assert "measure_scope" in requests[0]
    assert "measure_scope" in requests[1]


@pytest.mark.asyncio
async def test_a_measure_unit_left_unmined_is_disposed_of_by_its_own_reason(
    tracker: Tracker,
) -> None:
    """The unit a retry cannot mine keeps a reason that says what happened.

    The re-extraction is one call, never a loop. When it too returns nothing,
    the selected passage that states a figure in the active target's own unit
    is recorded with its own reason, so the ledger reports that the passage
    was read and its figure was not mined — never that the passage was
    irrelevant. A selected passage that states no such figure keeps the plain
    reason, which is what it is for.
    """
    completer = ScriptedCompleter(
        decisions=_quantity_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _quantity_agent(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_quantity_topic()]))

    # Both passages of the release were selected, and the retry ran once.
    units = outcome.state_update["evidence_units"]
    assert len(units) == 2
    assert len(_extraction_requests(completer)) == 2
    unmined = next(
        unit
        for unit in units.values()
        if "37,143 megawatt hours" in unit.excerpt
    )
    lede = next(
        unit
        for unit in units.values()
        if unit.evidence_id != unmined.evidence_id
    )
    reasons = {
        item.item_id: item.reason
        for item in outcome.state_update["evidence_dispositions"]
        if item.stage == "extraction"
    }

    assert reasons[unmined.evidence_id] == UNMINED_QUANTITY_REASON
    assert reasons[unmined.evidence_id] != "irrelevant"
    assert reasons[lede.evidence_id] == "irrelevant"


# ---------------------------------------------------------------------------
# An unanswered required target, and the one bounded re-ask for its own words
# ---------------------------------------------------------------------------
#
# The audited run answered half of its question and then reported "No checked
# finding answers it" for exactly those answers: the passages stating the dates
# were selected for the topic, the extraction bound them to nothing, and no
# later stage can bind a finding that was never made. A required target with no
# unit of measure has no figure to look for (PD-7), so what a selected passage
# shows instead is the target's own words. The fixture is that shape: the
# notice's own preamble, which states none of them, then the sentence that
# states the deadline.

_OWED_URL = "https://registry.test/notices/annual-returns-2026"
_OWED_TITLE = "Annual returns: the 2026 cycle"
_OWED_PREAMBLE = (
    "The Registry's annual programme is the authority's accounting of every "
    "operator it supervises, and this notice reports the schedule for the "
    "current cycle across every category it covers. "
) * 3
_OWED_SENTENCE = (
    "A registrant that registered before the rule took effect must first "
    "file its renewal return by 30 June 2027, the notice states."
)
_OWED_BODY = f"{_OWED_PREAMBLE}\n\n{_OWED_SENTENCE}"
_OWED_TARGET_QUESTION = (
    "By what date must a registrant first file its renewal return?"
)


def _owed_topic(*, unit_dimension: str | None = None) -> SubTopic:
    """One required obligation, qualitative unless a unit is given."""
    return _sub_topic("Annual return filing deadlines", 1).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=PLANNED_TARGET_ID,
                    coverage_id="topic-01",
                    question=_OWED_TARGET_QUESTION,
                    measure="the date a registrant's first renewal return is due",
                    unit_dimension=unit_dimension,
                    required=True,
                )
            ]
        }
    )


def _owed_decisions() -> list[object]:
    return [
        use_tool(
            "Find the notice.",
            "web_search",
            '{"query": "annual return filing schedule"}',
        ),
        use_tool("Read the notice.", "web_scraper", f'{{"url": "{_OWED_URL}"}}'),
        finish("The notice states the deadline.", _OWED_SENTENCE),
    ]


def _owed_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The finding either extraction is asked to return from the packet it saw.

    The ids come from the packet's own row, the way a model must read them;
    the snippet is the sentence that row carries, copied character for
    character, which is what the admission check requires of it.
    """
    del schema
    assert _OWED_SENTENCE in messages[1].content
    read_id, locator, _ = _packet_passage_for("30 June 2027", messages[1].content)
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "A registrant that registered before the rule took effect "
                    "must first file its renewal return by 30 June 2027."
                ),
                source_url=_OWED_URL,
                source_title=_OWED_TITLE,
                confidence=0.9,
                read_id=read_id,
                locator=locator,
                snippet=_OWED_SENTENCE,
                target_ids=[PLANNED_TARGET_ID],
            )
        ]
    )


def _owed_agent(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    body: str | None = None,
    selected: int | None = None,
) -> ResearcherAgent:
    return _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_OWED_TITLE, url=_OWED_URL)]
        ),
        http=page_client(title=_OWED_TITLE, body=body or _OWED_BODY),
        read_admission_chars=(
            None if selected is None else selected * WEB_PASSAGE_CHARS
        ),
    )


async def _run_owed_topic(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    unit_dimension: str | None = None,
    body: str | None = None,
    selected: int | None = None,
) -> AgentRun[ResearchFindings]:
    """Run one required-target topic to its end and return the whole outcome."""
    agent = _owed_agent(tracker, completer, body=body, selected=selected)
    async with tracker.session_span("session-1", "q"):
        return await agent.run(
            _state(sub_topics=[_owed_topic(unit_dimension=unit_dimension)])
        )


@pytest.mark.asyncio
async def test_an_unanswered_required_target_buys_one_re_ask_for_its_words(
    tracker: Tracker,
) -> None:
    """A required target the extraction left unbound is asked for once more.

    The first extraction returned no finding at all: the passage stating the
    deadline was selected for the topic and bound to nothing, which is how a
    run answers half a question and then reports the other half "Not found".
    One bounded re-ask — over that passage alone, with the unanswered target's
    own words — makes the binding, and its finding is the run's.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[]), _owed_reply],
    )
    outcome = await _run_owed_topic(tracker, completer)
    read = next(iter(outcome.state_update["read_records"].values()))
    stating = [
        locator
        for locator, text in read.passages.items()
        if "renewal return" in text
    ]
    # The premise of the fixture: the deadline is stated by a later passage,
    # not by the notice's own preamble, so the re-ask is what reaches it.
    assert len(stating) == 1 and stating[0] != "chunk-0"

    requests = _extraction_requests(completer)
    # One re-ask, never a loop.
    assert len(requests) == 2
    # The re-ask carries the passage that states the target's words alone, so
    # the model is not asked to find it again in the packet it was lost in.
    assert _packet_evidence_locators(requests[1]) == [stating[0]]
    assert stating[0] in _packet_evidence_locators(requests[0])
    # And the re-ask names the unanswered target by its own words, and says
    # what its packet is for; the first request says neither.
    assert _OWED_TARGET_QUESTION in requests[1]
    assert "Passages owed a finding" not in requests[0]
    assert "they are candidates, not answers" in requests[1].casefold()
    assert "they are candidates, not answers" not in requests[0].casefold()

    (finding,) = outcome.result.findings
    assert finding.target_ids == [PLANNED_TARGET_ID]
    assert finding.source_url == _OWED_URL


@pytest.mark.asyncio
async def test_a_required_target_an_admitted_finding_answers_buys_no_re_ask(
    tracker: Tracker,
) -> None:
    """The one re-ask is for an obligation left unanswered, never a confirmation.

    The first extraction bound the deadline to the target, so there is nothing
    owed and no second request: a healthy run pays nothing for the bound.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(), outputs=[_owed_reply]
    )
    outcome = await _run_owed_topic(tracker, completer)

    assert len(_extraction_requests(completer)) == 1
    (finding,) = outcome.result.findings
    assert finding.target_ids == [PLANNED_TARGET_ID]


@pytest.mark.asyncio
async def test_the_re_ask_leads_with_the_passage_that_states_the_target_most(
    tracker: Tracker,
) -> None:
    """The packet is character-bounded, so its order is its focus.

    Two passages of one read state the target's words, one of them in full and
    one in passing. A packet that led with the weaker passage is exactly the
    packet that cuts the answer out when its budget runs out — so the one that
    states more of the target's own words comes first, the way the figure
    re-ask shows the passage it was narrowed to first.
    """
    weak = "The Registry publishes its annual return in the autumn. " * 8
    strong = (
        "A registrant that registered before the rule took effect must first "
        "file its renewal return by 30 June 2027, the notice states."
    )
    body = f"{weak}\n\n{strong}"
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_OWED_TITLE, url=_OWED_URL)]
        ),
        http=page_client(title=_OWED_TITLE, body=body),
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state(sub_topics=[_owed_topic()]))

    requests = _extraction_requests(completer)
    assert len(requests) == 2
    packet = _packet_evidence_locators(requests[1])
    # The premise: both selected passages state the target's words, and the
    # stronger one is not the passage the read prints first.
    assert sorted(packet) == ["chunk-0", "chunk-1"]
    assert packet == ["chunk-1", "chunk-0"]
    assert _OWED_SENTENCE in requests[1]


@pytest.mark.asyncio
async def test_a_passage_stating_an_unanswered_target_keeps_its_own_reason(
    tracker: Tracker,
) -> None:
    """The ledger says the obligation's own words were held and unused.

    The re-ask is one call, never a loop. When it too returns nothing, the
    passage that states the unanswered required target's own words is
    recorded with its own reason, and the notice's preamble — which states
    none of them — keeps the plain one.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    outcome = await _run_owed_topic(tracker, completer)

    # Both passages of the notice were selected, and the re-ask ran once.
    units = outcome.state_update["evidence_units"]
    assert len(_extraction_requests(completer)) == 2
    unmined = next(
        unit for unit in units.values() if "renewal return" in unit.excerpt
    )
    preamble = next(
        unit for unit in units.values() if unit.evidence_id != unmined.evidence_id
    )
    reasons = {
        item.item_id: item.reason
        for item in outcome.state_update["evidence_dispositions"]
        if item.stage == "extraction"
    }

    assert reasons[unmined.evidence_id] == UNMINED_TARGET_REASON
    assert reasons[unmined.evidence_id] != "irrelevant"
    assert reasons[preamble.evidence_id] == "irrelevant"


@pytest.mark.asyncio
async def test_an_unanswered_target_its_read_never_states_buys_no_re_ask(
    tracker: Tracker,
) -> None:
    """A required target is not re-asked over a read that never states it.

    The passage states a logical error rate and none of the target's own
    words, so the read owes that obligation no answer: the bounded re-ask is
    spent only where the evidence already in hand would answer it.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search.", "web_search", '{"query": "qec"}'),
            use_tool("Read.", "web_scraper", f'{{"url": "{QEC_SOURCE_URL}"}}'),
            finish("Done.", QEC_PASSAGE),
        ],
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title="QEC 2025", url=QEC_SOURCE_URL)]
        ),
        http=page_client(title="QEC 2025", body=QEC_PASSAGE),
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state(sub_topics=[_owed_topic()]))

    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]


@pytest.mark.asyncio
async def test_a_required_figure_target_is_not_re_asked_for_its_words(
    tracker: Tracker,
) -> None:
    """Own words are the test for an obligation with no unit of measure.

    The notice states its deadline in words and carries no figure at all, and
    the required target asks for a quantity: the word re-ask is not spent on
    it, because a figure target's answer is a figure and the unit test owns
    the decision whether a passage owes it one.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    outcome = await _run_owed_topic(tracker, completer, unit_dimension="power")

    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]
    assert outcome.result.findings == []


# ---------------------------------------------------------------------------
# The bounded packets the re-extraction asks about
# ---------------------------------------------------------------------------
#
# Pre-flights 2-4 each ended at a 32,768- or 49,152-token output from about
# 10,000 tokens of input: the re-extraction was handed every owed passage of
# every read its topic had made, so one call became the whole read's packet.
# The input is bounded instead — at most MAX_OWED_PASSAGES_PER_BATCH passages
# per packet, at most MAX_OWED_BATCHES packets — and the passages that owe the
# most are the ones asked about.

_OWED_STRONG = (
    "A registrant that registered before the rule took effect must first file "
    "its renewal return by 30 June 2027, the notice states."
)
_OWED_WEAK = "A registrant must file its annual return with the registry."
_OWED_PADDING = (
    "The registry publishes its annual return notices for every category it "
    "covers, and this cycle is no exception."
)


def _owed_bulk_body(paragraphs: int = 60) -> str:
    """One notice whose selected passages outnumber a single batch.

    Every paragraph states at least one of the target's own words, so every
    passage the read selects is owed; the first paragraph states the most, so
    the packet that leads with the strongest passage leads with it.
    """
    return "\n\n".join(
        _OWED_STRONG
        if index == 0
        else (_OWED_WEAK if index % 2 else _OWED_PADDING)
        for index in range(paragraphs)
    )


@pytest.mark.asyncio
async def test_the_owed_re_ask_asks_about_at_most_one_batch_at_a_time(
    tracker: Tracker,
) -> None:
    """The re-ask's input is bounded, so its packet order is its focus.

    A read with more owed passages than one batch buys one packet per batch and
    no more: the pass that handed a single call the whole read ran away to a
    32,768-token output, and what a bounded packet cannot hold stays unmined
    rather than being asked about in a packet that cannot hold it.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    outcome = await _run_owed_topic(
        tracker,
        completer,
        body=_owed_bulk_body(),
        selected=MAX_OWED_PASSAGES_PER_BATCH + 2,
    )

    requests = _extraction_requests(completer)
    # The first extraction, then one request per batch, and never a third.
    assert len(requests) == 1 + MAX_OWED_BATCHES
    units = outcome.state_update["evidence_units"]
    first = _packet_evidence_locators(requests[1])
    second = _packet_evidence_locators(requests[2])
    assert len(first) == MAX_OWED_PASSAGES_PER_BATCH
    assert 0 < len(second) <= MAX_OWED_PASSAGES_PER_BATCH
    # Every selected passage is asked about exactly once...
    assert sorted([*first, *second]) == sorted(
        unit.locator for unit in units.values()
    )
    # ...and the passage that states the most of the target leads the packet.
    strongest = next(
        unit.locator
        for unit in units.values()
        if _OWED_STRONG[:40] in unit.excerpt
    )
    assert first[0] == strongest


@pytest.mark.asyncio
async def test_a_failed_owed_batch_costs_only_that_batch(tracker: Tracker) -> None:
    """A packet the provider cannot answer is the end of that packet alone.

    Each batch is a bounded improvement on evidence already in hand, so a
    provider failure in the first costs the first: the second is still asked,
    and the failure is recorded once for the ledger.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            ProviderTimeoutError("timed out"),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    outcome = await _run_owed_topic(
        tracker,
        completer,
        body=_owed_bulk_body(),
        selected=MAX_OWED_PASSAGES_PER_BATCH + 2,
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 1 + MAX_OWED_BATCHES
    recorded = [error.error_type for error in outcome.errors]
    assert recorded.count("researcher_re_extraction_provider_error") == 1
    # Neither packet yielded a finding, so the topic reports that too.
    assert "researcher_sub_topic_without_findings" in recorded


@pytest.mark.asyncio
async def test_only_the_owed_re_extraction_carries_its_own_output_cap(
    tracker: Tracker,
) -> None:
    """The one looping call keeps a bound; the first extraction does not.

    The owed-passage re-extraction ran away to its output cap at both 32,768
    and 49,152 tokens, so a larger cap only lengthened it: its request carries
    ``agents.re_extraction_max_tokens``. The first extraction sends no
    per-call cap, so the provider applies the global ``llm.max_tokens``.
    """
    completer = ScriptedCompleter(
        decisions=_owed_decisions(),
        outputs=[
            SubTopicFindingsDraft(findings=[]),
            SubTopicFindingsDraft(findings=[]),
        ],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_OWED_TITLE, url=_OWED_URL)]
        ),
        http=page_client(title=_OWED_TITLE, body=_OWED_BODY),
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, re_extraction_max_tokens=4321
        ),
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(_state(sub_topics=[_owed_topic()]))

    extraction_budgets = [
        budget
        for call, budget in zip(completer.calls, completer.budgets, strict=True)
        if call[0] == "SubTopicFindingsDraft"
    ]
    assert extraction_budgets == [None, 4321]


_SNIPPET = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"


def _topic() -> SubTopic:
    return SubTopic(
        coverage_id="topic-01", title="Battery storage", rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
    )


def _draft(read, **overrides) -> SubTopicFindingsDraft:
    fields = dict(
        content="EIA reports 10.4 GW of battery storage added in 2024.",
        source_url=read.resolved_url, source_title=read.title, confidence=0.9,
        read_id=read.read_id, locator="page-1-chunk-0", snippet=_SNIPPET,
        figures=[FindingFigureDraft(value="10.4", unit="gigawatts", period="2024", kind="actual")],
        data_period="2024",
    )
    fields.update(overrides)
    return SubTopicFindingsDraft(findings=[FindingDraft(**fields)])


def _build(read, draft):
    return build_findings(
        draft, sub_topic=_topic(), extracted_at="2026-09-24T00:00:00+00:00",
        known_urls=[read.resolved_url], known_reads={read.read_id: read},
    )


def test_build_findings_keeps_snippet_read_locator_and_figures() -> None:
    read = make_read()
    findings, rejected = _build(read, _draft(read))
    assert rejected == []
    [finding] = findings
    assert (finding.snippet, finding.read_id, finding.locator) == (
        _SNIPPET, read.read_id, "page-1-chunk-0"
    )
    assert finding.figures == [
        FindingFigure(value="10.4", unit="gigawatts", period="2024", kind="actual")
    ]


def test_build_findings_refuses_a_snippet_the_locator_does_not_carry() -> None:
    read = make_read()
    findings, rejected = _build(read, _draft(read, snippet="EIA says 10.4 GW was added."))
    assert findings == [] and "snippet was not admitted at locator" in rejected[0]


def test_build_findings_refuses_a_snippet_over_the_cap() -> None:
    long_text = "Battery storage grew. " * 60
    read = make_read(long_text)
    findings, rejected = _build(read, _draft(read, snippet=long_text.strip()))
    assert findings == [] and f"longer than {MAX_SNIPPET_CHARS}" in rejected[0]


def test_build_findings_refuses_a_judgement_snippet_with_a_bare_pronoun_subject() -> None:
    """RES-4 code guard (D7): a verdict whose subject is a bare pronoun or
    demonstrative, with no referent anywhere in the quoted snippet, must
    never become a finding -- the audited run reported "this is the model
    to beat" and nothing downstream could ever say what "this" was.
    """
    text = "Utility reports vary widely across regions. This is the utility to beat."
    read = make_read(text, passages={"page-1-chunk-0": text})
    findings, rejected = _build(
        read,
        _draft(
            read,
            snippet="This is the utility to beat.",
            content="This is the utility to beat.",
            figures=[],
            data_period=None,
        ),
    )
    assert findings == []
    assert "bare pronoun" in rejected[0]


def test_build_findings_admits_a_judgement_snippet_that_carries_its_own_referent() -> None:
    """The same verdict is admitted once the snippet also carries the
    neighbouring sentence that names its subject -- the adjacent-sentence
    rule RES-4 now asks the model to follow.
    """
    text = "Meridian Grid reports the most additions this year. This is the utility to beat."
    read = make_read(text, passages={"page-1-chunk-0": text})
    findings, rejected = _build(
        read,
        _draft(
            read,
            snippet=text,
            content="Meridian Grid is the utility to beat.",
            figures=[],
            data_period=None,
        ),
    )
    assert rejected == []
    [finding] = findings
    assert finding.snippet == text


def test_bare_pronoun_judgement_refuses_the_run_3_snippet_with_nameless_content() -> None:
    """RevResearcherR3: the verbatim run-3 snippet (evidence log F10) with
    content that names no product must still be refused -- the guard's job
    is the missing referent, not the snippet's own shape.
    """
    snippet = (
        "If you want great ANC, good mic quality, and support for "
        "high\u2011quality codecs like LDAC, SBC, AAC, and LC3, this is "
        "the model to beat."
    )
    assert _bare_pronoun_judgement(snippet, "This is the model to beat.") is True


@pytest.mark.parametrize(
    "content",
    [
        "The Sony WH-1000XM6 is the model to beat.",
        "Sony is the model to beat.",
        "Bose is the model to beat.",
    ],
)
def test_bare_pronoun_judgement_admits_the_run_3_snippet_when_content_names_the_product(
    content: str,
) -> None:
    """The identical snippet is admitted once content names what "this" is
    -- whether or not the product's own name opens content's own sentence
    (F5, controller decision): a plain-ASCII leading capital ("Sony",
    "Bose") is a referent unless it is a closed-class word or an
    introductory word/phrase a comma sets off.
    """
    snippet = (
        "If you want great ANC, good mic quality, and support for "
        "high\u2011quality codecs like LDAC, SBC, AAC, and LC3, this is "
        "the model to beat."
    )
    assert _bare_pronoun_judgement(snippet, content) is False


@pytest.mark.parametrize(
    "snippet",
    [
        "It is estimated that sales will double.",
        "It was found that battery life exceeds expectations.",
        "It has been shown that the results replicate.",
        "They have discontinued the retro line entirely.",
    ],
)
def test_bare_pronoun_judgement_never_triggers_on_a_dummy_or_impersonal_subject(
    snippet: str,
) -> None:
    """An impersonal "It ... that/to" construction states somebody else's
    finding, not a judgement about "it"; "They" with a non-linking verb
    (possession, not a verdict) states no judgement either, so neither ever
    reaches the referent check.
    """
    assert _bare_pronoun_judgement(snippet, "") is False


@pytest.mark.parametrize(
    "snippet",
    [
        "iPhone 15 launched in 2023. It is the best phone.",
        "\u0160koda sales rose. It is the brand to beat.",
    ],
)
def test_bare_pronoun_judgement_admits_a_referent_named_in_an_earlier_sentence(
    snippet: str,
) -> None:
    """A referent an earlier sentence names -- by an internal capital or a
    leading capital outside plain ASCII -- clears the judgement that
    follows, even with no content at all.
    """
    assert _bare_pronoun_judgement(snippet, "") is False


def test_bare_pronoun_judgement_refuses_an_ordinary_sentence_initial_word() -> None:
    """An ordinary word capitalised only because it starts a sentence is not
    a referent: English capitalises every sentence's first word regardless
    of whether it names anything.
    """
    snippet = "Prices fell sharply. This is the one to buy."
    assert _bare_pronoun_judgement(snippet, "This is the one to buy.") is True


def test_bare_pronoun_judgement_triggers_at_a_clause_boundary() -> None:
    """A judgement need not open its own sentence: "Overall, it is..."
    states one exactly as much as a sentence-initial "It is...".
    """
    assert _bare_pronoun_judgement("Overall, it is the one to beat.", "") is True


@pytest.mark.parametrize(
    "content",
    [
        "Overall, it is the model to beat.",
        "However, this is the best option.",
        "According to reviewers, it is the best.",
    ],
)
def test_bare_pronoun_judgement_refuses_content_whose_only_capital_opens_a_sentence(
    content: str,
) -> None:
    """F5 (controller decision): content's own sentence adverb or
    introductory phrase -- a word a comma sets off, or "According" before
    "to" -- still does not name the judgement's subject, even though a
    bare product name opening the same position now does.
    """
    snippet = "Utility reports vary widely across regions. This is the utility to beat."
    assert _bare_pronoun_judgement(snippet, content) is True


def test_an_unusable_figure_is_dropped_but_the_finding_is_kept() -> None:
    read = make_read()
    draft = _draft(read, figures=[
        FindingFigureDraft(value="", unit="GW"),
        FindingFigureDraft(value="10.4", unit="GW", kind="estimate"),
    ])
    dropped_figures: list[str] = []
    findings, rejected = build_findings(
        draft, sub_topic=_topic(), extracted_at="2026-09-24T00:00:00+00:00",
        known_urls=[read.resolved_url], known_reads={read.read_id: read},
        dropped_figures=dropped_figures,
    )
    assert rejected == []
    [finding] = findings
    assert finding.figures == [FindingFigure(value="10.4", unit="GW", kind=None)]
    assert any("figure 1 has no value or unit" in reason for reason in dropped_figures)


def test_a_model_reported_plan_kind_normalizes_to_forecast() -> None:
    """A live researcher pass emitted kind "plan" for EIA's 19.6 GW planned
    additions -- a forecast, not one of the closed vocabulary's two exact
    spellings. Silently dropping it to ``None`` would let a downstream
    reader print a plan without knowing it is one; it is normalised to
    ``forecast`` instead, and never raises.
    """
    read = make_read()
    draft = _draft(read, figures=[
        FindingFigureDraft(value="19.6", unit="GW", period="2025", kind="plan"),
    ])
    findings, rejected = _build(read, draft)
    assert rejected == []
    [finding] = findings
    assert finding.figures == [
        FindingFigure(value="19.6", unit="GW", period="2025", kind="forecast")
    ]


def test_planned_targets_render_their_structured_fields() -> None:
    line = render_planned_targets([make_target(organisation="EIA")])
    assert line.startswith(
        "- topic-01-target-01 [topic-01] [required]: How much"
    )
    assert "unit: power" in line and "period: 2024" in line and "organisation: EIA" in line


def test_the_sub_topic_guidance_prints_the_obligations_the_loop_owes() -> None:
    """A loop cannot aim at an obligation it was never shown.

    Coverage is judged on the plan's targets, not on the criteria printed
    beside them, so the guidance carries the sub-topic's own targets with
    their required flags (review RES-1 §2).
    """
    target = make_target()
    sub_topic = _sub_topic("Alpha").model_copy(
        update={"evidence_targets": [target]}
    )

    guidance = render_sub_topic_guidance(sub_topic, ())

    assert "The answers this sub-topic owes" in guidance
    assert (
        f"- {target.target_id} [{target.coverage_id}] [required]: "
        f"{target.question}" in guidance
    )


def test_the_extraction_request_carries_the_research_question() -> None:
    """The extractor judges what bears on the question, so it is shown it.

    Until this section existed the request carried the sub-topic's title,
    criteria and target questions but never the question they serve, so a
    page's furniture could look as relevant as its evidence (review RES-3 §3).
    """
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

    body = extraction_messages(
        task,
        run,
        evidence_chars=200,
        question="How much capacity was added?",
    )[1].content

    assert "# Research question\nHow much capacity was added?" in body


def test_a_page_title_may_carry_the_attribution_quote() -> None:
    """The heading that introduces a reproduced document is the page's words.

    A card label, a section heading or a masthead names the instrument or the
    body a page is serving, and a rule that read only the excerpt's two
    passages left such a page crediting the host that served it (review
    RES-6 §3).
    """
    read = make_read(
        "The filing is late when it arrives after the last day of the second month.",
        title="According to the record office: filing deadlines",
        passages={
            "chunk-0": (
                "The filing is late when it arrives after the last day of the "
                "second month."
            )
        },
    )

    assert _admitted_attribution(
        "the record office",
        "According to the record office",
        read=read,
        locator="chunk-0",
    ) == ("the record office", "According to the record office")


# ---------------------------------------------------------------------------
# The targeted extra pass (spec §6.5, §7.2)
# ---------------------------------------------------------------------------
#
# The first pass researches the whole plan; an extra pass is confined to the
# sub-topics that own a target still missing a verified finding, and the
# extraction contract of that pass lists those targets alone. Nothing here
# reads a review's findings or a checked claim: the pass's own job list
# is ``state.extra_pass_target_ids``, replaced by the graph at every write.


def _planned_state(**updates: object) -> ResearchState:
    topics = [
        SubTopic(
            coverage_id=f"topic-0{n}",
            title=f"T{n}",
            rationale="r",
            search_queries=[f"q{n}"],
            success_criteria=["c"],
            priority=n,
            evidence_targets=[make_target(f"topic-0{n}-target-01")],
        )
        for n in (1, 2, 3)
    ]
    return ResearchState(
        session_id="s", original_question="q", sub_topics=topics
    ).model_copy(update=updates)


def test_the_first_pass_runs_every_planned_sub_topic_in_priority_order() -> None:
    assert [t.coverage_id for t in select_sub_topics(_planned_state())] == [
        "topic-01",
        "topic-02",
        "topic-03",
    ]


def test_an_extra_pass_runs_only_the_sub_topics_that_own_missing_targets() -> None:
    state = _planned_state(extra_pass_target_ids=["topic-02-target-01"])

    assert [t.coverage_id for t in select_sub_topics(state)] == ["topic-02"]


@pytest.mark.asyncio
async def test_an_extra_pass_researches_only_the_missing_targets_owner(
    tracker: Tracker,
) -> None:
    """One topic runs, and the extraction is shown one target.

    The state carries the plan's two topics and names one missing target;
    only the topic that owns it is researched, the request the extraction
    answers lists that target alone, and a topic left out of the pass's own
    job list is not recorded as a coverage gap.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Find the forecast.",
                "web_search",
                '{"query": "eia battery storage forecast 2025"}',
            ),
            use_tool(
                "Read the EIA page.",
                "web_scraper",
                f'{{"url": "{EIA_64705_URL}"}}',
            ),
            finish("The page carries the forecast.", "19.6 GW in 2025."),
        ],
        # The second reply answers the one bounded re-extraction of the
        # passages a target in MW still owes a figure for: this model finds
        # nothing more in them, which leaves the first reply's finding alone.
        outputs=[_forecast_reply, SubTopicFindingsDraft(findings=[])],
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=EIA_64705_TITLE, url=EIA_64705_URL)]
        ),
        http=page_client(title=EIA_64705_TITLE, body=EIA_64705_BODY),
    )
    state = _forecast_plan_state().model_copy(
        update={"extra_pass_target_ids": [FORECAST_TARGET_ID]}
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert started == [TOPIC_02_TITLE]

    request_body = completer.calls[0][2][1].content
    assert (
        f"- {FORECAST_TARGET_ID} [topic-02: {TOPIC_02_TITLE}] [required]"
        in request_body
    )
    assert f"- {PLANNED_TARGET_ID} [topic-01]" not in request_body

    (finding,) = outcome.result.findings
    assert finding.target_ids == [FORECAST_TARGET_ID]
    # The topic outside the pass's job list is not a coverage gap.
    assert [
        error.error_type
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    ] == []
# ---------------------------------------------------------------------------
# Concurrent sub-topics under one run-wide tool lock (D9, §7.2, PD-27)
# ---------------------------------------------------------------------------
#
# ``agents.sub_topic_concurrency`` runs several sub-topic loops at once. What
# their concurrency must not change: each loop's prompt renders its own
# observations and its own acquisition context, the folds come out in plan
# order whatever order the loops finish in, the sub-topic.completed event says
# how long its own loop took, and the run-wide tool lock makes two loops that
# want the same page download it once. The order-pinned tests above construct
# their researcher with ``sub_topic_concurrency=1`` for the same reason: with
# one loop in flight the order-based ``ScriptedCompleter`` is unambiguous.

_ALPHA_URL = "https://example.test/alpha"
_BETA_URL = "https://example.test/beta"


class _QuerySearchClient(FakeSearchClient):
    """Serve each loop the page its own query asks for.

    A queue would hand the first loop the second loop's page whenever the two
    interleaved differently than the fixture assumed; keying on the query makes
    the fixture's answer independent of the order the loops run in.
    """

    def __init__(self, by_query: dict[str, dict[str, object]]) -> None:
        super().__init__()
        self._by_query = by_query

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
            }
        )
        return self._by_query[query]


def _loop_decisions(
    target_id: str, label: str, url: str = QEC_SOURCE_URL
) -> list[object]:
    """One loop's short script: find its own page, read it, finish."""
    return [
        use_tool(
            f"Search for {label}.",
            "web_search",
            json.dumps({"query": f"{label} 2025"}),
        ),
        use_tool(
            f"Read the {label} page.",
            "web_scraper",
            json.dumps({"url": url}),
        ),
        finish(f"{label} is covered.", f"{label} answer."),
    ]


def _notes(packet: str) -> str:
    """The ``## Notes so far`` section of one rendered ReAct packet."""
    return packet.split("## Notes so far", 1)[1].split("\n## ", 1)[0]


@dataclass(frozen=True)
class _WebTools:
    """The researcher's tools, plus the page bodies their fetches served."""

    tools: list[BaseTool]
    downloads: list[str]


@pytest.fixture
def web_tools(tracker: Tracker) -> _WebTools:
    """The researcher's tools over a page whose fetch really suspends.

    ``httpx.MockTransport`` awaits an async handler, which is what creates the
    interleaving the tool lock exists for: without the lock both loops pass
    their cache check before either fetch has been admitted to the cache.
    """
    downloads: list[str] = []
    body = (
        f"<html><head><title>{QEC_READ.title}</title></head>"
        f"<body><p>{QEC_PASSAGE}</p></body></html>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        downloads.append(str(request.url))
        await asyncio.sleep(0.02)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=body,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return _WebTools(
        tools=research_tools(
            tracker,
            search=FakeSearchClient([search_response(), search_response()]),
            http=client,
        ),
        downloads=downloads,
    )


def _two_topic_state() -> ResearchState:
    return _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 2, coverage_id="topic-02"),
        ]
    )


@pytest.mark.asyncio
async def test_two_loops_never_see_each_others_observations_or_acquisition_context(
    tracker: Tracker,
) -> None:
    """Loop A's second turn carries A's own observation and A's own acquisition
    context, never B's, whatever order the turns complete in: each loop gets
    its own ScratchpadMemory and its own policy (D9, §7.2)."""
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": _loop_decisions("topic-01", "Alpha", _ALPHA_URL),
            "topic-02": _loop_decisions("topic-02", "Beta", _BETA_URL),
        },
        outputs={
            "Alpha": [SubTopicFindingsDraft(findings=[])],
            "Beta": [SubTopicFindingsDraft(findings=[])],
        },
    )
    agent = _researcher(
        tracker,
        completer,
        search=_QuerySearchClient(
            {
                "Alpha 2025": search_response(title="Alpha report", url=_ALPHA_URL),
                "Beta 2025": search_response(title="Beta report", url=_BETA_URL),
            }
        ),
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=2
        ),
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(_two_topic_state())

    alpha = completer.packets("topic-01")
    beta = completer.packets("topic-02")
    assert len(alpha) == len(beta) == 3

    # Each packet names its own target and no other loop's.
    assert "- target_id=topic-01" in alpha[0]
    assert "- target_id=topic-02" not in alpha[1]
    assert "- target_id=topic-02" in beta[0]
    assert "- target_id=topic-01" not in beta[1]

    # The notes of the second turn are that loop's own observation, rendered
    # from its own scratchpad, and no sibling's.
    assert "Alpha report" in _notes(alpha[1])
    assert "Beta report" not in _notes(alpha[1])
    assert "Beta report" in _notes(beta[1])
    assert "Alpha report" not in _notes(beta[1])

    # ...and so is the acquisition context it renders: A sees A's candidate.
    assert f"- candidate_urls={_ALPHA_URL}" in alpha[1]
    assert _BETA_URL not in alpha[1]
    assert f"- candidate_urls={_BETA_URL}" in beta[1]
    assert _ALPHA_URL not in beta[1]

    # After the read, each loop's own page is the one it read.
    assert f"- read_urls={_ALPHA_URL}" in alpha[2]
    assert _BETA_URL not in alpha[2]
    assert f"- read_urls={_BETA_URL}" in beta[2]
    assert _ALPHA_URL not in beta[2]


@pytest.mark.asyncio
async def test_findings_and_events_fold_in_plan_order_not_completion_order(
    tracker: Tracker,
) -> None:
    """`runs`, `findings` and the sub-topic events come out in plan order even when
    the last plan topic finishes first."""
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": _loop_decisions("topic-01", "Alpha"),
            "topic-02": _loop_decisions("topic-02", "Beta"),
        },
        outputs={
            "Alpha": [_findings_draft("Alpha finding.")],
            "Beta": [_findings_draft("Beta finding.")],
        },
        # Alpha is the first plan topic and finishes last.
        delays={"topic-01": 0.1},
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=2
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_two_topic_state())

    # The premise, or the plan-order assertions below would hold vacuously.
    assert completer.exhausted == ["topic-02", "topic-01"]

    assert [
        finding.content for finding in outcome.result.findings
    ] == ["Alpha finding.", "Beta finding."]
    assert outcome.react.final_answer == "Alpha answer.\n\nBeta answer."
    completed = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    ]
    assert completed == ["Alpha", "Beta"]
    tool_calls = [
        event.metadata["sub_topic"]
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.tool_call"
    ]
    assert tool_calls == ["Alpha", "Alpha", "Beta", "Beta"]


@pytest.mark.asyncio
async def test_a_page_two_loops_request_at_once_is_downloaded_once_and_admitted_from_cache(
    tracker: Tracker, web_tools: _WebTools
) -> None:
    """The tool lock makes the second loop's fetch of the same URL a cache hit from
    the first loop's admission, so the run downloads the page once
    (`_invariant_read_downloaded_once`)."""
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": _loop_decisions("topic-01", "Alpha"),
            "topic-02": _loop_decisions("topic-02", "Beta"),
        },
        outputs={
            "Alpha": [SubTopicFindingsDraft(findings=[])],
            "Beta": [SubTopicFindingsDraft(findings=[])],
        },
    )
    agent = _researcher(
        tracker,
        completer,
        tools=web_tools.tools,
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=2
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_two_topic_state())

    assert web_tools.downloads == [QEC_SOURCE_URL]
    assert outcome.react.cache_hits == 1
    # Two searches and the one download reached a tool; the second loop's
    # read was served from the run's cache instead of the network.
    assert outcome.react.tool_calls == 3


@pytest.mark.asyncio
async def test_a_provider_failure_lets_running_loops_finish_and_skips_unstarted_ones(
    tracker: Tracker,
) -> None:
    """One loop dies on ProviderError; the loops already running finish, and every
    loop that never started records `provider_failure_stopped_processing`."""
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": _loop_decisions("topic-01", "Alpha"),
            "topic-02": [ProviderTimeoutError("timed out")],
            "topic-03": _loop_decisions("topic-03", "Gamma"),
        },
        outputs={"Alpha": [_findings_draft("Alpha finding.")]},
        # Beta must fail while Alpha is still running.
        delays={"topic-01": 0.05},
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response(), search_response()]),
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=2
        ),
    )
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 2, coverage_id="topic-02"),
            _sub_topic("Gamma", 3, coverage_id="topic-03"),
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
    # The loop already running when the failure landed still produced its
    # finding; the one that never acquired the gate never ran.
    assert [
        finding.content for finding in outcome.result.findings
    ] == ["Alpha finding."]
    assert completer.remaining("topic-03") == 3
    skipped = {
        error.details["sub_topic"]: error.details
        for error in outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped) == {"Gamma"}
    assert skipped["Gamma"]["coverage_id"] == "topic-03"
    assert skipped["Gamma"]["reason"] == "provider_failure_stopped_processing"
    assert any(error.recoverable is False for error in outcome.errors)


def _refused_tavily_budget() -> RequestAttemptLimitError:
    """A real budget that spent its one unit and now refuses the next."""
    budget = RequestBudget(
        RequestBudgetConfig(
            deepseek_attempt_ceiling=None,
            openai_attempt_ceiling=None,
            tavily_attempt_ceiling=1,
            stop_fraction=1.0,
        )
    )
    budget.reserve("tavily")
    with pytest.raises(RequestAttemptLimitError) as refusal:
        budget.reserve("tavily")
    return refusal.value


@pytest.mark.asyncio
async def test_an_attempt_limit_in_one_loop_still_halts_the_run(
    tracker: Tracker,
) -> None:
    """RequestAttemptLimitError in one loop is re-raised after every sibling settles,
    so the node still halts the run."""
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": [_refused_tavily_budget()],
            "topic-02": [finish("Beta is done.", "Beta answer.")],
        },
        # Beta settles while Alpha is still inside its first turn.
        delays={"topic-01": 0.05},
    )
    agent = _researcher(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=2
        ),
    )

    with pytest.raises(RequestAttemptLimitError):
        async with tracker.session_span("session-1", "q"):
            await agent.run(_two_topic_state())

    # Every sibling settled before the refusal was re-raised: Beta's whole
    # script was served, not cancelled by Alpha's halt.
    assert completer.remaining("topic-02") == 0
    # Beta's whole script was served, and Alpha's single decision was the
    # refusal itself: both loops settled before it was re-raised.
    assert completer.exhausted == ["topic-02", "topic-01"]


@pytest.mark.parametrize(
    ("configured", "constructor", "expected_peak"),
    [(2, None, 2), (5, 1, 1)],
)
@pytest.mark.asyncio
async def test_the_sub_topic_cap_is_the_configured_one_and_the_constructor_wins(
    tracker: Tracker,
    configured: int,
    constructor: int | None,
    expected_peak: int,
) -> None:
    """`agents.sub_topic_concurrency` bounds the loops in flight, and the
    constructor's own value overrides it — the seam an order-pinned test uses
    to pin one loop at a time."""
    completer = TargetKeyedCompleter(
        decisions={
            f"topic-0{index}": [finish(f"T{index} is done.", f"T{index} answer.")]
            for index in (1, 2, 3)
        },
    )
    agent = _researcher(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=4,
            sub_topic_concurrency=configured,
        ),
        sub_topic_concurrency=constructor,
    )
    state = _state(
        sub_topics=[
            _sub_topic(f"T{index}", index, coverage_id=f"topic-0{index}")
            for index in (1, 2, 3)
        ]
    )

    async with tracker.session_span("session-1", "q"):
        await agent.run(state)

    assert [call.key for call in completer.react_calls] == [
        "topic-01",
        "topic-02",
        "topic-03",
    ]
    assert completer.max_in_flight == expected_peak


@pytest.mark.asyncio
async def test_the_completed_event_carries_the_loops_own_wall_seconds(
    tracker: Tracker,
) -> None:
    """`sub_topic.completed` carries `elapsed_s`, so per-sub-topic time survives
    concurrency: the CLI prints every sub-topic event at node completion,
    where log timestamps no longer separate them."""
    completer = TargetKeyedCompleter(
        decisions={"topic-01": _loop_decisions("topic-01", "Alpha")},
        outputs={"Alpha": [SubTopicFindingsDraft(findings=[])]},
        delays={"topic-01": 0.2},
    )
    agent = _researcher(
        tracker,
        completer,
        search=FakeSearchClient([search_response()]),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(sub_topics=[_sub_topic("Alpha", 1)]))

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    elapsed = completed.metadata["elapsed_s"]
    assert isinstance(elapsed, float)
    # The scripted delay is inside the loop, and the number is the loop's own
    # wall time, rounded to a tenth.
    assert elapsed >= 0.2
    assert elapsed == round(elapsed, 1)


@pytest.mark.asyncio
async def test_an_exception_in_one_loop_stops_the_queued_sub_topics(
    tracker: Tracker,
) -> None:
    """A loop that RAISES stops the queued topics, exactly as a returned
    provider failure does.

    ``agents.sub_topic_concurrency`` is 5 and the plan's ceiling is 7, so more
    topics than slots is the ordinary case: after one loop hits the run-wide
    attempt ceiling, every still-queued topic must not start and spend model
    turns whose work the re-raised error throws away.
    """
    completer = TargetKeyedCompleter(
        decisions={
            "topic-01": [_refused_tavily_budget()],
            "topic-02": [finish("Beta is done.", "Beta answer.")],
            "topic-03": [finish("Gamma is done.", "Gamma answer.")],
        },
    )
    agent = _researcher(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=4, tool_budget=4, sub_topic_concurrency=1
        ),
    )
    state = _state(
        sub_topics=[
            _sub_topic("Alpha", 1, coverage_id="topic-01"),
            _sub_topic("Beta", 2, coverage_id="topic-02"),
            _sub_topic("Gamma", 3, coverage_id="topic-03"),
        ]
    )

    with pytest.raises(RequestAttemptLimitError):
        async with tracker.session_span("session-1", "q"):
            await agent.run(state)

    # Only the loop that raised ever reached the provider; the two queued
    # topics kept their whole scripts.
    assert [call.key for call in completer.react_calls] == ["topic-01"]
    assert completer.remaining("topic-02") == 1
    assert completer.remaining("topic-03") == 1


def test_a_figure_draft_keeps_its_subject() -> None:
    """D11: the thing a figure is about travels from the draft, as the page names it."""
    figures, dropped = _admitted_figures(
        [FindingFigureDraft(value="4.5", unit="out of 5", subject=" Model B "),
         FindingFigureDraft(value="4.2", unit="out of 5", subject="  ")],
        index=1,
    )
    assert dropped == []
    assert [figure.subject for figure in figures] == ["Model B", None]


# --- S6: per-page parallel extraction ----------------------------------------


_PACKET_READ_ID = re.compile(r"read_id=(\S+) requested_url=\S+ resolved_url=(\S+) title=(.+?) reader=")
_PACKET_PASSAGE = re.compile(r"evidence_id=\S+ read_id=(\S+) locator=(\S+) targets=\S+ excerpt=(.+)")


class _PerPageCompleter:
    """A fake keyed by the one read a per-page extraction packet names.

    ``complete_react`` serves ``decisions`` in order, exactly like
    ``ScriptedCompleter``. ``complete_structured`` reads the packet's own
    ``read_id=`` line to find which page the call is about, sleeps
    ``delay`` seconds -- a real overlap needs a genuine await point a
    scripted double never yields on otherwise -- then raises
    ``error_for[read_id]`` once if set, or returns
    ``output_factory(read_id, resolved_url, title, verbatim_snippet)``, where
    ``verbatim_snippet`` is copied straight out of the packet's own "passage"
    row, since ``build_findings`` rejects any snippet that is not the read's
    own words, found verbatim in its text. Tracks concurrency
    (``max_in_flight``) and both the call and completion order of every
    read, which is the only way to prove two pages actually overlapped
    rather than merely being scheduled to.
    """

    def __init__(
        self,
        *,
        decisions: Sequence[object] = (),
        delay: float = 0.0,
        output_factory: Callable[[str, str, str, str, str], SubTopicFindingsDraft] | None = None,
        error_for: Mapping[str, BaseException] | None = None,
        fail_at_call: int | None = None,
        fail_with: BaseException | None = None,
        delays_by_call: Mapping[int, float] | None = None,
        sync_after_react_call: int | None = None,
    ) -> None:
        self._decisions: list[Any] = list(decisions)
        self._delay = delay
        self._output_factory = output_factory or (
            lambda read_id, url, title, locator, snippet: _finding_draft(
                read_id, url, title, locator, snippet
            )
        )
        self._error_for = dict(error_for or {})
        self._fail_at_call = fail_at_call
        self._fail_with = fail_with
        self._delays_by_call = dict(delays_by_call or {})
        # The react turn (1-indexed) that must not return its decision until
        # at least one page extraction has completed: acceptance 4 needs a
        # LATER decision turn's own packet built only after an earlier page's
        # background extraction lands, and a fixed sleep would be a guess at
        # how many event-loop ticks that takes -- busy-yielding until the
        # extraction is actually observed done is exact instead.
        self._sync_after_react_call = sync_after_react_call
        self._react_call_count = 0
        self.calls: list[str | None] = []
        self.completions: list[str | None] = []
        self.structured_packets: list[str] = []
        self.react_packets: list[str] = []
        self._in_flight = 0
        self.max_in_flight = 0
        # Precise (start, end) perf_counter windows per structured call
        # (1-indexed), so a test can prove two *specific* calls overlapped
        # in time rather than merely that the completer's peak concurrency
        # was 2 at some point, which the main pass alone already produces.
        self.call_windows: dict[int, tuple[float, float]] = {}

    async def complete_react(
        self, messages, tools, *, agent_name=None, max_tokens=None
    ):
        self.react_packets.append("\n".join(message.content for message in messages))
        self._react_call_count += 1
        if self._react_call_count == self._sync_after_react_call:
            while not self.completions:
                await asyncio.sleep(0)
        if not self._decisions:
            raise AssertionError("no scripted decision left for a native ReAct turn")
        decision = self._decisions.pop(0)
        if isinstance(decision, BaseException):
            raise decision
        return native_turn_from_decision(decision)

    async def complete_structured(
        self, messages, schema, *, agent_name=None, max_tokens=None, reasoning_effort=None
    ):
        if schema is ReActDecision:
            raise AssertionError(
                "ReAct decisions must be requested through complete_react"
            )
        text = "\n".join(message.content for message in messages)
        self.structured_packets.append(text)
        match = _PACKET_READ_ID.search(text)
        read_id = match.group(1) if match else None
        resolved_url = match.group(2) if match else None
        title = match.group(3) if match else None
        passage_match = _PACKET_PASSAGE.search(text)
        locator = passage_match.group(2) if passage_match else "chunk-0"
        snippet = passage_match.group(3) if passage_match else ""
        self.calls.append(read_id)
        call_index = len(self.calls)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        started = perf_counter()
        try:
            delay = self._delays_by_call.get(call_index, self._delay)
            if delay:
                await asyncio.sleep(delay)
            if call_index == self._fail_at_call:
                raise self._fail_with or AssertionError(
                    "fail_at_call fired with no fail_with exception"
                )
            if read_id in self._error_for:
                raise self._error_for.pop(read_id)
            return self._output_factory(
                read_id or "", resolved_url or "", title or "", locator, snippet
            )
        finally:
            self._in_flight -= 1
            self.completions.append(read_id)
            self.call_windows[call_index] = (started, perf_counter())


_PAGE_A_URL = "https://a.example.test/page"
_PAGE_B_URL = "https://b.example.test/page"


def _two_page_client(*, body_a: str, body_b: str) -> httpx.AsyncClient:
    """A client serving two distinct pages at two distinct URLs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        is_a = "a.example.test" in str(request.url)
        body = body_a if is_a else body_b
        title = "Page A" if is_a else "Page B"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=f"<html><head><title>{title}</title></head><body><p>{body}</p></body></html>",
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _two_page_search_response() -> dict[str, object]:
    return {
        "results": [
            {"title": "Page A", "url": _PAGE_A_URL, "content": "About page A.", "score": 0.9},
            {"title": "Page B", "url": _PAGE_B_URL, "content": "About page B.", "score": 0.85},
        ]
    }


def _two_scrape_decisions() -> list[object]:
    return [
        use_tool("Find sources.", "web_search", '{"query": "alpha 2026"}'),
        use_tool("Read page A.", "web_scraper", f'{{"url": "{_PAGE_A_URL}"}}'),
        use_tool("Read page B.", "web_scraper", f'{{"url": "{_PAGE_B_URL}"}}'),
        finish("Both sources read.", "Findings gathered."),
    ]


def _finding_draft(
    read_id: str, url: str, title: str, locator: str, snippet: str
) -> SubTopicFindingsDraft:
    """One finding for whichever read ``read_id`` names.

    ``url``/``title``/``locator``/``snippet`` all come straight off the
    packet's own "read" and "passage" rows for that read --
    ``build_findings`` rejects a draft whose source url does not match the
    read it claims, or whose snippet is not the read's own words found
    verbatim in its text, so a fake that made any of these up would have
    every finding rejected instead of admitted.
    """
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=f"Finding for {read_id}.",
                source_url=url,
                source_title=title,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=snippet,
                target_ids=["topic-01"],
            )
        ]
    )


async def _run_two_page_sub_topic(
    tracker: Tracker,
    completer: _PerPageCompleter,
    *,
    extraction_concurrency: int = 16,
    sub_topic: SubTopic | None = None,
    body_a: str = "Alpha content one.",
    body_b: str = "Alpha content two.",
):
    agent = ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(
            tracker,
            search=FakeSearchClient([_two_page_search_response()]),
            http=_two_page_client(body_a=body_a, body_b=body_b),
        ),
        config=AgentRuntimeConfig(
            max_iterations=6, tool_budget=6, extraction_concurrency=extraction_concurrency
        ),
        clock=_clock,
    )
    state = _state(sub_topics=[sub_topic or _sub_topic("Alpha", 1)])
    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)
    return outcome


def _read_ids_from_calls(calls: Sequence[str | None]) -> list[str]:
    return [read_id for read_id in calls if read_id is not None]


@pytest.mark.asyncio
async def test_two_reads_extractions_overlap_in_time(tracker: Tracker) -> None:
    """Acceptance 1: two reads' extractions overlap; wall time is under the sum.

    Each page's own call is scripted to take 0.1s. Run sequentially that is
    at least 0.2s; started in the background as each page is admitted, while
    the loop keeps going, the two calls overlap and the whole pass finishes
    in well under 0.2s.
    """
    completer = _PerPageCompleter(decisions=_two_scrape_decisions(), delay=0.1)

    started = perf_counter()
    outcome = await _run_two_page_sub_topic(tracker, completer)
    elapsed = perf_counter() - started

    assert outcome.result is not None
    assert len(outcome.result.findings) == 2
    assert elapsed < 0.2
    assert completer.max_in_flight == 2


@pytest.mark.asyncio
async def test_a_failed_page_keeps_the_other_pages_findings(tracker: Tracker) -> None:
    """Acceptance 2: one page's provider failure never costs the other page.

    Page A's own call (the first admitted, the first structured call) hits
    the output limit; page B's call succeeds. The sub-topic still reports
    page B's finding, and page A's failure is recorded as its own error --
    recoverable, not a reason to drop the whole sub-topic.
    """
    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(),
        fail_at_call=1,
        fail_with=_output_limit_error(),
    )

    outcome = await _run_two_page_sub_topic(tracker, completer)

    assert outcome.result is not None
    assert [finding.source_url for finding in outcome.result.findings] == [_PAGE_B_URL]
    assert any(
        error.error_type == "researcher_extraction_output_limit"
        for error in outcome.errors
    )


@pytest.mark.asyncio
async def test_merged_findings_are_identical_whichever_page_completes_first(
    tracker: Tracker,
) -> None:
    """Acceptance 3: the merge is by read order, not completion order.

    Run once with page A slower than page B, once with the delays swapped so
    page B finishes last instead -- the merged findings come back in the same
    (read-admission) order either way.
    """
    completer_a_last = _PerPageCompleter(
        decisions=_two_scrape_decisions(), delays_by_call={1: 0.05, 2: 0.0}
    )
    completer_b_last = _PerPageCompleter(
        decisions=_two_scrape_decisions(), delays_by_call={1: 0.0, 2: 0.05}
    )

    outcome_a_last = await _run_two_page_sub_topic(tracker, completer_a_last)
    outcome_b_last = await _run_two_page_sub_topic(tracker, completer_b_last)

    assert outcome_a_last.result is not None
    assert outcome_b_last.result is not None
    urls_a_last = [finding.source_url for finding in outcome_a_last.result.findings]
    urls_b_last = [finding.source_url for finding in outcome_b_last.result.findings]
    assert urls_a_last == [_PAGE_A_URL, _PAGE_B_URL]
    assert urls_b_last == [_PAGE_A_URL, _PAGE_B_URL]


@pytest.mark.asyncio
async def test_a_later_decision_turn_sees_an_earlier_pages_finding(
    tracker: Tracker,
) -> None:
    """Acceptance 4: findings arrive as calls complete, mid-loop.

    Page A is read at turn 2; turn 3 (which reads page B) does not return
    its own decision until page A's background extraction has completed, so
    turn 4's packet -- built only once turn 3's tool has run -- is provably
    later than page A's finding landing. It must already show that finding
    as a recorded one, not merely the read as admitted.
    """
    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(), sync_after_react_call=3
    )

    outcome = await _run_two_page_sub_topic(tracker, completer)

    assert outcome.result is not None
    assert len(completer.react_packets) == 4
    page_a_read_id = completer.calls[0]
    assert page_a_read_id is not None
    assert f"recorded finding read_id={page_a_read_id}" in completer.react_packets[3]


def _three_page_client(*, bodies: Mapping[str, str]) -> httpx.AsyncClient:
    """A client serving three distinct pages, one per host in ``bodies``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        host = request.url.host
        body = bodies[host]
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=f"<html><head><title>{host}</title></head><body><p>{body}</p></body></html>",
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_PAGE_C_URL = "https://c.example.test/page"


async def _run_three_page_sub_topic(
    tracker: Tracker, completer: _PerPageCompleter, *, extraction_concurrency: int
):
    agent = ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(
            tracker,
            search=FakeSearchClient(
                [
                    {
                        "results": [
                            {"title": "Page A", "url": _PAGE_A_URL, "content": "A.", "score": 0.9},
                            {"title": "Page B", "url": _PAGE_B_URL, "content": "B.", "score": 0.85},
                            {"title": "Page C", "url": _PAGE_C_URL, "content": "C.", "score": 0.8},
                        ]
                    }
                ]
            ),
            http=_three_page_client(
                bodies={
                    "a.example.test": "Alpha content one.",
                    "b.example.test": "Alpha content two.",
                    "c.example.test": "Alpha content three.",
                }
            ),
        ),
        config=AgentRuntimeConfig(
            max_iterations=8, tool_budget=8, extraction_concurrency=extraction_concurrency
        ),
        clock=_clock,
    )
    state = _state(sub_topics=[_sub_topic("Alpha", 1)])
    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)
    return outcome


@pytest.mark.asyncio
async def test_extraction_concurrency_bounds_the_semaphore(tracker: Tracker) -> None:
    """Acceptance 6: ``extraction_concurrency`` reaches the semaphore.

    Three pages, each extraction call delayed enough to overlap the others,
    but a concurrency cap of 2: no more than two calls are ever in flight at
    once even though all three pages are read well within that window.
    """
    completer = _PerPageCompleter(
        decisions=[
            use_tool("Find sources.", "web_search", '{"query": "alpha 2026"}'),
            use_tool("Read page A.", "web_scraper", f'{{"url": "{_PAGE_A_URL}"}}'),
            use_tool("Read page B.", "web_scraper", f'{{"url": "{_PAGE_B_URL}"}}'),
            use_tool("Read page C.", "web_scraper", f'{{"url": "{_PAGE_C_URL}"}}'),
            finish("All sources read.", "Findings gathered."),
        ],
        delay=0.05,
    )

    outcome = await _run_three_page_sub_topic(tracker, completer, extraction_concurrency=2)

    assert outcome.result is not None
    assert len(outcome.result.findings) == 3
    assert completer.max_in_flight == 2


def _owed_page_target() -> EvidenceTarget:
    return EvidenceTarget(
        target_id=PLANNED_TARGET_ID,
        coverage_id="topic-01",
        question=_OWED_TARGET_QUESTION,
        measure="the date a registrant's first renewal return is due",
        required=True,
    )


@pytest.mark.asyncio
async def test_owed_re_extraction_targets_only_the_page_that_owed_passages(
    tracker: Tracker,
) -> None:
    """Acceptance 5: a bounded re-ask retries the one page that owed it.

    Page A's own text states the required target's own words; page B's does
    not share one. Neither page's main extraction binds the target -- both
    reply with no findings -- so the target stays unanswered and the owed
    re-ask fires once, over the evidence that owes it. That packet must
    name page A's read and never page B's: ``build_acquisition_context``'s
    ``focus_ids`` path renders only the reads the focused evidence belongs
    to, and only page A's evidence was ever selected into the batch.
    """
    sub_topic = _sub_topic("Alpha", 1).model_copy(
        update={"evidence_targets": [_owed_page_target()]}
    )
    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(),
        output_factory=lambda *args: SubTopicFindingsDraft(findings=[]),
    )

    outcome = await _run_two_page_sub_topic(
        tracker,
        completer,
        sub_topic=sub_topic,
        body_a=_OWED_BODY,
        body_b="The weather in spring is generally mild across the region.",
    )

    assert outcome.result is not None
    assert len(completer.structured_packets) == 3
    page_a_read_id = completer.calls[0]
    page_b_read_id = completer.calls[1]
    retry_packet = completer.structured_packets[2]
    assert "Passages owed a finding" in retry_packet
    assert f"read_id={page_a_read_id}" in retry_packet
    # Page B's own read/evidence/passage rows are absent -- the state
    # section's own ``read_urls=`` line still names every URL the run has
    # read, which is state bookkeeping, not the evidence a page's own
    # re-extraction packet shows the model.
    assert f"read_id={page_b_read_id}" not in retry_packet
    assert f"passage read_id={page_b_read_id}" not in retry_packet


@pytest.mark.asyncio
async def test_two_pages_that_both_owe_passages_get_their_own_concurrent_re_asks(
    tracker: Tracker,
) -> None:
    """Owed re-extraction is bounded per page, not shared across a sub-topic.

    Both page A's and page B's own text share a word with the required
    target's own question, and neither page's main extraction binds it, so
    both pages owe their own re-ask. A shared 8x2 budget across the whole
    sub-topic would let whichever page ranked first crowd out the other's
    own re-ask entirely; each page must get its own, and they must overlap
    in time rather than run one after another (user ruling: no strong
    limits, only runaway guards).
    """
    sub_topic = _sub_topic("Alpha", 1).model_copy(
        update={"evidence_targets": [_owed_page_target()]}
    )
    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(),
        output_factory=lambda *args: SubTopicFindingsDraft(findings=[]),
        delays_by_call={3: 0.1, 4: 0.1},
    )

    outcome = await _run_two_page_sub_topic(
        tracker,
        completer,
        sub_topic=sub_topic,
        body_a=_OWED_BODY,
        body_b=(
            "A separate registrant category exists for late renewal "
            "filings, according to a related registry notice."
        ),
    )

    assert outcome.result is not None
    # Two main calls, then two owed retries -- one per page, never combined
    # into a single shared-budget packet.
    assert len(completer.structured_packets) == 4
    owed_packets = [
        packet
        for packet in completer.structured_packets
        if "Passages owed a finding" in packet
    ]
    assert len(owed_packets) == 2
    page_a_read_id = completer.calls[0]
    page_b_read_id = completer.calls[1]
    assert any(f"read_id={page_a_read_id}" in packet for packet in owed_packets)
    assert any(f"read_id={page_b_read_id}" in packet for packet in owed_packets)
    # The two owed calls (indices 3 and 4) overlapped in time: call 3's
    # window had not ended before call 4's began.
    start_3, end_3 = completer.call_windows[3]
    start_4, end_4 = completer.call_windows[4]
    assert start_3 < end_4 and start_4 < end_3


@pytest.mark.asyncio
async def test_two_pages_malformed_owed_retry_findings_are_prefixed_by_page(
    tracker: Tracker,
) -> None:
    """ReRevS6 P3: two owing pages' own malformed owed-retry findings stay
    distinguishable in the merged errors.

    Both pages own an owed re-ask (as above), and both owed replies name a
    snippet that is not verbatim on their own page, so ``build_findings``
    rejects both -- each as its own "finding 1: ..." -- and the merge must
    tell them apart by prefixing each with its own read id, or the two
    rejections would report identically despite coming from different pages.
    """
    sub_topic = _sub_topic("Alpha", 1).model_copy(
        update={"evidence_targets": [_owed_page_target()]}
    )
    call_count = 0

    def _factory(
        read_id: str, url: str, title: str, locator: str, snippet: str
    ) -> SubTopicFindingsDraft:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return SubTopicFindingsDraft(findings=[])
        # The owed retry: a snippet that is not the read's own words, so
        # ``build_findings`` rejects it ("snippet was not admitted at
        # locator") rather than admitting a claim it never actually reads.
        return SubTopicFindingsDraft(
            findings=[
                FindingDraft(
                    content="A claim without a real snippet.",
                    source_url=url,
                    source_title=title,
                    confidence=0.8,
                    read_id=read_id,
                    locator=locator,
                    snippet="this text is not verbatim on the page",
                    target_ids=["topic-01"],
                )
            ]
        )

    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(),
        output_factory=_factory,
    )

    outcome = await _run_two_page_sub_topic(
        tracker,
        completer,
        sub_topic=sub_topic,
        body_a=_OWED_BODY,
        body_b=(
            "A separate registrant category exists for late renewal "
            "filings, according to a related registry notice."
        ),
    )

    page_a_read_id = completer.calls[0]
    page_b_read_id = completer.calls[1]
    invalid = next(
        error for error in outcome.errors if error.error_type == "researcher_invalid_finding"
    )
    rejected = invalid.details["rejected"]
    assert any(reason.startswith(f"{page_a_read_id}: ") for reason in rejected)
    assert any(reason.startswith(f"{page_b_read_id}: ") for reason in rejected)
    # Genuinely distinguishable, not the same string twice.
    assert len(set(rejected)) == len(rejected)


@pytest.mark.asyncio
async def test_a_page_task_never_outlives_the_run_after_a_later_decision_fails(
    tracker: Tracker,
) -> None:
    """RevSelectionR3 P1: a page task started before a later decision fails
    must be cancelled and awaited, not left running after ``agent.run``
    returns -- and its own finding, already paid for, must not be thrown
    away just because a later decision in the same loop failed.

    Page A is read, its background extraction call is forced to complete
    (the sync-wait before the third decision) before the third react turn
    raises a transport failure, which ``run_react_loop`` catches
    (``propagate_provider_errors=False``) and folds into a ``ReActRun`` with
    ``succeeded=False``. Page A's own finding was already paid for by the
    time the loop gave up, and must survive; no task the loop started may
    still be running once ``agent.run`` has returned.
    """
    completer = _PerPageCompleter(
        decisions=[
            use_tool("Find sources.", "web_search", '{"query": "alpha 2026"}'),
            use_tool("Read page A.", "web_scraper", f'{{"url": "{_PAGE_A_URL}"}}'),
            ProviderTimeoutError("transport failure"),
        ],
        sync_after_react_call=3,
    )
    baseline = asyncio.all_tasks()

    outcome = await _run_two_page_sub_topic(tracker, completer)

    orphaned = [task for task in asyncio.all_tasks() - baseline if not task.done()]
    assert orphaned == []
    assert outcome.result is not None
    assert [finding.source_url for finding in outcome.result.findings] == [
        _PAGE_A_URL
    ]


@pytest.mark.asyncio
async def test_a_still_running_page_task_is_cancelled_when_the_loop_fails(
    tracker: Tracker,
) -> None:
    """RevSelectionR3 P1's own repro: a page task still in flight (not yet
    complete) when a later decision fails must be cancelled and awaited, not
    left running unobserved after ``agent.run`` returns.

    Page A's own extraction is slow (0.2s) and the third decision fails
    immediately (no sync-wait), so the task is provably still pending --
    ``page_task.done()`` is false -- at the moment the loop gives up.
    """
    completer = _PerPageCompleter(
        decisions=[
            use_tool("Find sources.", "web_search", '{"query": "alpha 2026"}'),
            use_tool("Read page A.", "web_scraper", f'{{"url": "{_PAGE_A_URL}"}}'),
            ProviderTimeoutError("transport failure"),
        ],
        delay=0.2,
    )
    baseline = asyncio.all_tasks()

    outcome = await _run_two_page_sub_topic(tracker, completer)

    orphaned = [task for task in asyncio.all_tasks() - baseline if not task.done()]
    assert orphaned == []
    assert outcome.result is not None
    assert outcome.result.findings == []


@pytest.mark.asyncio
async def test_a_failed_pages_units_are_not_labelled_irrelevant(
    tracker: Tracker,
) -> None:
    """RevSelectionR3 P1: a failed page's units get their own disposition,
    and stay owed rather than consumed.

    Page A's own extraction call hits the output limit; page B's succeeds.
    The provider never actually mined page A's selected passages, so they
    must not be judged ``irrelevant`` -- a verdict the extraction never got
    the chance to make. Whole-page admission's own "still owed" record for a
    read with nothing deferred is simply that its units are never marked
    consumed and never disappear from the registry: ``pending_extraction_
    ids`` only ever holds a read whose own admission deferred passages past
    its budget (S1's continuation-batch mechanism), which a small
    synthetic page never does, so that field is not the signal to check
    here -- the disposition, and the units still being visible to a later
    pass, are.
    """
    completer = _PerPageCompleter(
        decisions=_two_scrape_decisions(),
        fail_at_call=1,
        fail_with=_output_limit_error(),
    )

    outcome = await _run_two_page_sub_topic(tracker, completer)

    assert outcome.result is not None
    page_a_read_id = completer.calls[0]
    page_b_read_id = completer.calls[1]
    assert page_a_read_id is not None and page_b_read_id is not None
    units = outcome.state_update["evidence_units"]
    page_a_units = [
        evidence_id
        for evidence_id, unit in units.items()
        if unit.read_id == page_a_read_id
    ]
    # Still in the registry, available to a later pass, never dropped for
    # having failed.
    assert page_a_units
    reasons = {
        (item.stage, item.item_id): item.reason
        for item in outcome.state_update["evidence_dispositions"]
    }
    for evidence_id in page_a_units:
        assert reasons[("extraction", evidence_id)] == "extraction_failed"
    # Page B's own units, whose call succeeded, never get this reason.
    page_b_units = [
        evidence_id
        for evidence_id, unit in units.items()
        if unit.read_id == page_b_read_id
    ]
    for evidence_id in page_b_units:
        assert reasons.get(("extraction", evidence_id)) != "extraction_failed"


# ---------------------------------------------------------------------------
# D7/D10: the required-target sweep reaches across sub-topics
# ---------------------------------------------------------------------------
#
# Fable's audit of run 5 found the same class of miss twice: a scholar's own
# causal account and a modern historians' disagreement paragraph both sat in
# admitted text on a page the run read, and neither was ever asked about
# because the existing owed re-ask only asks a page about the *reading*
# sub-topic's own required targets. The fixtures below give the required
# target to a sub-topic that never runs this pass (``max_sub_topics=1``
# truncates the plan to the page-reading sub-topic alone, which is simpler
# than scripting two concurrent loops and proves the same thing: the sweep
# reads the plan's whole inventory, not the active loop's own share of it).

_CROSS_TOPIC_TARGET_ID = "topic-01-target-01"
_CROSS_TOPIC_TARGET_QUESTION = (
    "What explanation do published assessments give for the outage's "
    "underlying cause?"
)
_CROSS_TOPIC_TARGET_ID_2 = "topic-03-target-01"
_CROSS_TOPIC_TARGET_QUESTION_2 = (
    "What corrective measures do published assessments recommend for the "
    "outage?"
)
_CROSS_TOPIC_URL = "https://grid-notices.test/reports/substation-cycle"
_CROSS_TOPIC_TITLE = "Substation inspection cycle report"
_CROSS_TOPIC_OWN_PREAMBLE = (
    "Grid engineers publish a quarterly summary of substation maintenance "
    "activity, and this report covers every site inspected in the current "
    "cycle across the region. "
) * 3
# Padding so this chunk crosses a passage-split boundary on its own,
# ahead of the sentence a finding actually cites (RevX1Sweep P2-2): the
# skip test needs a *second*, never-cited passage that also shares a word
# with the target's own question, or the skip it names is invisible --
# the only shared-word passage is already consumed by the bound finding,
# so a broken skip check would still find nothing left to ask about.
_CROSS_TOPIC_FILLER = (
    "The report also lists every crew that took part in the inspection "
    "cycle and the equipment each crew checked along the way. "
) * 3
_CROSS_TOPIC_OVERLAP_SENTENCE = (
    "Published assessments trace the outage's underlying cause to a "
    "corroded busbar in the substation, a separate review states."
)
_CROSS_TOPIC_OVERLAP_SENTENCE_2 = (
    "Published assessments also state the outage's underlying cause may "
    "involve a failed transformer nearby, other engineers add."
)
_CROSS_TOPIC_BODY = (
    f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n"
    f"{_CROSS_TOPIC_FILLER}{_CROSS_TOPIC_OVERLAP_SENTENCE}\n\n"
    f"{_CROSS_TOPIC_OVERLAP_SENTENCE_2}"
)


def _cross_topic_reading_sub_topic(priority: int = 1) -> SubTopic:
    """The sub-topic that runs this pass and reads the shared page."""
    return _sub_topic("Substation maintenance", priority, coverage_id="topic-02")


def _cross_topic_required_sub_topic(priority: int = 2) -> SubTopic:
    """Owns the required target; ``max_sub_topics=1`` keeps it from running."""
    return _sub_topic(
        "Outage cause explanations", priority, coverage_id="topic-01"
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_CROSS_TOPIC_TARGET_ID,
                    coverage_id="topic-01",
                    question=_CROSS_TOPIC_TARGET_QUESTION,
                    measure=(
                        "the explanation published assessments give for the "
                        "outage's cause"
                    ),
                    required=True,
                )
            ]
        }
    )


def _cross_topic_required_sub_topic_2(priority: int = 3) -> SubTopic:
    """A second, differently-owned required target, also never run."""
    return _sub_topic(
        "Outage corrective measures", priority, coverage_id="topic-03"
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_CROSS_TOPIC_TARGET_ID_2,
                    coverage_id="topic-03",
                    question=_CROSS_TOPIC_TARGET_QUESTION_2,
                    measure=(
                        "the corrective measures published assessments "
                        "recommend for the outage"
                    ),
                    required=True,
                )
            ]
        }
    )


def _cross_topic_decisions() -> list[object]:
    return [
        use_tool(
            "Find the report.",
            "web_search",
            '{"query": "substation maintenance cycle"}',
        ),
        use_tool(
            "Read the report.", "web_scraper", f'{{"url": "{_CROSS_TOPIC_URL}"}}'
        ),
        finish("The report covers the cycle.", "Substation cycle reported."),
    ]


def _cross_topic_main_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The reading sub-topic's own finding, bound to no required target."""
    del schema
    read_id, locator, excerpt = _packet_passage_for(
        "inspected in the current cycle", messages[1].content
    )
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="The substation's inspection cycle covered every site.",
                source_url=_CROSS_TOPIC_URL,
                source_title=_CROSS_TOPIC_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
            )
        ]
    )


def _cross_topic_main_reply_already_bound(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The reading sub-topic's own finding, already bound to the target."""
    del schema
    read_id, locator, excerpt = _packet_passage_for(
        "corroded busbar", messages[1].content
    )
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "Published assessments trace the outage's cause to a "
                    "corroded busbar in the substation."
                ),
                source_url=_CROSS_TOPIC_URL,
                source_title=_CROSS_TOPIC_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_CROSS_TOPIC_TARGET_ID],
            )
        ]
    )


def _cross_topic_sweep_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The bound finding the cross-topic sweep is asked to return."""
    del schema
    packet = messages[1].content
    assert "Passages owed a finding" in packet
    assert _CROSS_TOPIC_TARGET_QUESTION in packet
    read_id, locator, excerpt = _packet_passage_for("corroded busbar", packet)
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "Published assessments trace the outage's cause to a "
                    "corroded busbar in the substation."
                ),
                source_url=_CROSS_TOPIC_URL,
                source_title=_CROSS_TOPIC_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_CROSS_TOPIC_TARGET_ID],
            )
        ]
    )


def _cross_topic_agent(
    tracker: Tracker, completer: ScriptedCompleter, *, body: str = _CROSS_TOPIC_BODY
) -> ResearcherAgent:
    return _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_CROSS_TOPIC_TITLE, url=_CROSS_TOPIC_URL)]
        ),
        http=page_client(title=_CROSS_TOPIC_TITLE, body=body),
        max_sub_topics=1,
    )


async def _run_cross_topic_state(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    sub_topics: list[SubTopic],
    body: str = _CROSS_TOPIC_BODY,
) -> AgentRun[ResearchFindings]:
    agent = _cross_topic_agent(tracker, completer, body=body)
    async with tracker.session_span("session-1", "q"):
        return await agent.run(_state(sub_topics=sub_topics))


@pytest.mark.asyncio
async def test_a_page_read_for_one_topic_is_swept_for_anothers_required_target(
    tracker: Tracker,
) -> None:
    """A page read by sub-topic B, whose admitted text answers sub-topic A's
    required target, yields an owed packet for that target and a finding
    bound to it.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply, _cross_topic_sweep_reply],
    )

    outcome = await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 2
    assert "Passages owed a finding" not in requests[0]
    assert "Passages owed a finding" in requests[1]
    assert _CROSS_TOPIC_TARGET_QUESTION in requests[1]

    bound = [
        finding
        for finding in outcome.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound) == 1
    assert bound[0].source_url == _CROSS_TOPIC_URL


@pytest.mark.asyncio
async def test_a_page_that_already_answers_the_target_is_not_re_asked(
    tracker: Tracker,
) -> None:
    """A page that already answers the target is not re-asked."""
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply_already_bound],
    )

    outcome = await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
    )

    requests = _extraction_requests(completer)
    # One request: the page's own main extraction, already bound. No second,
    # cross-topic-sweep request is made for a target the read already answers.
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]

    bound = [
        finding
        for finding in outcome.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound) == 1


@pytest.mark.asyncio
async def test_the_cross_topic_sweep_sends_at_most_one_packet_per_read(
    tracker: Tracker,
) -> None:
    """The bound holds: at most one sweep packet per read, even when the read
    leaves more than one required target unbound.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply, _cross_topic_sweep_reply],
    )

    outcome = await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
            _cross_topic_required_sub_topic_2(),
        ],
    )

    requests = _extraction_requests(completer)
    # Two requests, never three: one main call and exactly one sweep packet
    # for the read, though two required targets are unbound by it.
    assert len(requests) == 2
    sweep_request = requests[1]
    assert _CROSS_TOPIC_TARGET_QUESTION in sweep_request
    assert _CROSS_TOPIC_TARGET_QUESTION_2 in sweep_request

    bound = [
        finding
        for finding in outcome.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound) == 1


_CROSS_TOPIC_MISSING_TARGET_ID = "topic-02-target-01"
_CROSS_TOPIC_MISSING_TARGET_QUESTION = (
    "What annual budget does the utility disclose for control-room "
    "renovations?"
)


def _cross_topic_reading_sub_topic_with_own_target(priority: int = 1) -> SubTopic:
    """The reading sub-topic, this time with its own required target whose
    own words are nowhere on the page. It stays missing after round 1 and
    buys an extra pass that re-admits the very same read.
    """
    return _cross_topic_reading_sub_topic(priority).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_CROSS_TOPIC_MISSING_TARGET_ID,
                    coverage_id="topic-02",
                    question=_CROSS_TOPIC_MISSING_TARGET_QUESTION,
                    measure=(
                        "the annual budget the utility discloses for "
                        "control-room renovations"
                    ),
                    required=True,
                )
            ]
        }
    )


@pytest.mark.asyncio
async def test_a_later_pass_does_not_re_sweep_a_read_the_run_already_bound(
    tracker: Tracker,
) -> None:
    """RevX1Sweep P3-1: the skip reads the run's whole record, not this
    pass's findings alone.

    Round 1 binds sub-topic A's required target from sub-topic B's read via
    the cross-topic sweep, while sub-topic B's own (unrelated) required
    target stays missing. Round 2 re-admits the very same read for
    sub-topic B (``max_sub_topics=1`` keeps sub-topic A itself from running
    again, the same way it kept it from running in round 1), with the
    plan's full target list still in view. If the skip check only consulted
    this pass's own (empty) findings, it would ask about sub-topic A's
    target again on a read that already answered it.

    This also exercises RevX1Sweep P3-2: round 1's own extraction produces
    only unbound findings for sub-topic B (its own target is never found on
    the page), so the cross-topic finding the sweep adds must not turn its
    own ``target_obligation_completed`` telemetry false.
    """
    completer1 = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply, _cross_topic_sweep_reply],
    )
    round1 = await _run_cross_topic_state(
        tracker,
        completer1,
        sub_topics=[
            _cross_topic_reading_sub_topic_with_own_target(),
            _cross_topic_required_sub_topic(),
        ],
    )
    assert len(_extraction_requests(completer1)) == 2
    bound_round1 = [
        finding
        for finding in round1.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound_round1) == 1
    # P3-2: an admitted-but-all-unbound own extraction, plus a cross-topic
    # finding the sweep adds, must still report this topic's own obligation
    # advancing exactly as it did before the cross-topic sweep existed.
    completed_round1 = next(
        event
        for event in round1.state_update["events"]
        if event.event_type == "researcher.sub_topic.completed"
    )
    assert completed_round1.metadata["target_obligation_completed"] is True

    completer2 = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[SubTopicFindingsDraft(findings=[])],
    )
    agent2 = _cross_topic_agent(tracker, completer2)
    # No ``extra_pass_target_ids``: ``_planned_targets`` would otherwise
    # confine this pass's own candidate list to it, which would leave
    # sub-topic A's target out regardless of the fix under test.
    # ``max_sub_topics=1`` (set by ``_cross_topic_agent``) is what keeps
    # sub-topic A itself from running again, exactly as it did in round 1.
    round2_state = _state(
        sub_topics=[
            _cross_topic_reading_sub_topic_with_own_target(),
            _cross_topic_required_sub_topic(),
        ],
        raw_findings=round1.state_update["raw_findings"],
        evidence_units=round1.state_update["evidence_units"],
        read_records=round1.state_update["read_records"],
    )

    async with tracker.session_span("session-2", "q"):
        await agent2.run(round2_state)

    # One request: the page's own re-admitted main call. No second,
    # cross-topic-sweep request for a target the run's own record already
    # answers, even though this pass's own findings alone do not show it.
    assert len(_extraction_requests(completer2)) == 1


# ---------------------------------------------------------------------------
# Y1: tightening the cross-topic sweep's passage selection (Fable's audit of
# run 6, Appendix 2: 42 packets and 269k output tokens bought ten weak
# findings, none of them the disagreement the sweep exists to catch -- a
# one-shared-token floor filled every packet slot with chrome (a site's own
# section menu) and bibliography entries that happened to repeat the plan's
# own subject word).
# ---------------------------------------------------------------------------

_CHROME_MENU_PASSAGE = (
    "Home Reports About Us Contact Archive Outage Explanation Categories "
    "Published Assessments Underlying Cause Notices Index Privacy Policy "
    "Terms Site Map Search Help"
)
_CHROME_EXCLUDED_BODY = f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n{_CHROME_MENU_PASSAGE}"

_TWO_TOKEN_PASSAGE = (
    "A separate maintenance log briefly notes the outage, and a "
    "technician's memo adds the underlying wiring diagram was outdated."
)
_TWO_TOKEN_BODY = f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n{_TWO_TOKEN_PASSAGE}"

_ONE_TOKEN_PASSAGE = (
    "A separate maintenance log briefly notes an unrelated outage at a "
    "different facility last spring. The log also records routine site "
    "visits, staffing levels and weather conditions for that week."
)
_ONE_TOKEN_BODY = f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n{_ONE_TOKEN_PASSAGE}"


def _two_token_sweep_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The bound finding the sweep is asked to return for the passage that
    shares exactly two non-generic tokens with the target's own question.
    """
    del schema
    packet = messages[1].content
    assert "Passages owed a finding" in packet
    read_id, locator, excerpt = _packet_passage_for("wiring diagram", packet)
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "A technician's memo attributes the outage to outdated "
                    "underlying wiring."
                ),
                source_url=_CROSS_TOPIC_URL,
                source_title=_CROSS_TOPIC_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_CROSS_TOPIC_TARGET_ID],
            )
        ]
    )


@pytest.mark.asyncio
async def test_a_chrome_passage_sharing_the_targets_words_is_not_selected(
    tracker: Tracker,
) -> None:
    """A menu of section names that happens to include the target's own
    words is never sent as an owed passage.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply],
    )

    await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
        body=_CHROME_EXCLUDED_BODY,
    )

    # One request: the page's own main extraction. The menu shares six of
    # the target's own words, well past the two-token floor, but is a menu
    # of section names, not a passage a finding can be made from.
    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]


@pytest.mark.asyncio
async def test_a_passage_with_two_non_generic_shared_tokens_is_selected(
    tracker: Tracker,
) -> None:
    """A passage sharing exactly two non-generic tokens with the required
    target's own question is sent, and its finding is bound.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply, _two_token_sweep_reply],
    )

    outcome = await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
        body=_TWO_TOKEN_BODY,
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 2
    assert "Passages owed a finding" in requests[1]
    assert "wiring diagram" in requests[1]

    bound = [
        finding
        for finding in outcome.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound) == 1


@pytest.mark.asyncio
async def test_no_packet_is_sent_when_nothing_qualifies(
    tracker: Tracker,
) -> None:
    """A passage sharing only one token of the target's own question never
    buys a packet: the floor is two, not one.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply],
    )

    await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
        body=_ONE_TOKEN_BODY,
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]


# RevY1 P0: a single, properly-punctuated sentence running past 22 words is
# not a link rail merely for its length. `is_link_dense` -- the passage
# selector's own lede-only heuristic -- flagged exactly this shape, which is
# why it was dropped in favour of the no-terminator rule.
_LONG_SENTENCE_PASSAGE = (
    "A separate maintenance review states that published assessments "
    "trace the underlying cause of the outage to equipment installed "
    "before the site's modernization project began last year."
)
_LONG_SENTENCE_BODY = f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n{_LONG_SENTENCE_PASSAGE}"


def _long_sentence_sweep_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The bound finding the sweep is asked to return for the long-sentence
    passage.
    """
    del schema
    packet = messages[1].content
    assert "Passages owed a finding" in packet
    read_id, locator, excerpt = _packet_passage_for(
        "modernization project", packet
    )
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "Published assessments trace the outage's cause to "
                    "equipment installed before a modernization project."
                ),
                source_url=_CROSS_TOPIC_URL,
                source_title=_CROSS_TOPIC_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_CROSS_TOPIC_TARGET_ID],
            )
        ]
    )


@pytest.mark.asyncio
async def test_a_long_sentence_prose_passage_is_selected(tracker: Tracker) -> None:
    """RevY1 P0: a single, properly-punctuated sentence of more than 22
    words, sharing two or more non-generic tokens, is not excluded as a
    link rail merely for running long.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply, _long_sentence_sweep_reply],
    )

    outcome = await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _cross_topic_required_sub_topic(),
        ],
        body=_LONG_SENTENCE_BODY,
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 2
    assert "Passages owed a finding" in requests[1]
    assert "modernization project" in requests[1]

    bound = [
        finding
        for finding in outcome.result.findings
        if _CROSS_TOPIC_TARGET_ID in finding.target_ids
    ]
    assert len(bound) == 1


# RevY1 P2: two required targets that share a subject word ("utility") must
# not let that word count toward the two-token floor -- otherwise a passage
# that shares only the plan's own repeated subject, plus one word specific
# to a target, would qualify on the shared word alone.
_GENERIC_TARGET_X_ID = "topic-05-target-01"
_GENERIC_TARGET_X_QUESTION = (
    "What replacement schedule does the utility announce for damaged "
    "transformers?"
)
_GENERIC_TARGET_Y_ID = "topic-06-target-01"
_GENERIC_TARGET_Y_QUESTION = (
    "What insurance claim does the utility file for storm damage?"
)
_GENERIC_TEST_FILLER = (
    "The notice covers routine filings from the past several quarters. "
    "Most of those filings concern scheduled inspections and minor repairs. "
)
_GENERIC_TEST_CANDIDATE = (
    "A brief utility notice mentions a claim filed with regulators."
)
_GENERIC_TEST_BODY = (
    f"{_CROSS_TOPIC_OWN_PREAMBLE}\n\n{_GENERIC_TEST_FILLER}{_GENERIC_TEST_CANDIDATE}"
)


def _generic_target_x_sub_topic(priority: int = 2) -> SubTopic:
    return _sub_topic(
        "Transformer replacement schedule", priority, coverage_id="topic-05"
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_GENERIC_TARGET_X_ID,
                    coverage_id="topic-05",
                    question=_GENERIC_TARGET_X_QUESTION,
                    measure=(
                        "the replacement schedule the utility announces "
                        "for damaged transformers"
                    ),
                    required=True,
                )
            ]
        }
    )


def _generic_target_y_sub_topic(priority: int = 3) -> SubTopic:
    return _sub_topic(
        "Storm damage insurance claim", priority, coverage_id="topic-06"
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_GENERIC_TARGET_Y_ID,
                    coverage_id="topic-06",
                    question=_GENERIC_TARGET_Y_QUESTION,
                    measure="the insurance claim the utility files for storm damage",
                    required=True,
                )
            ]
        }
    )


@pytest.mark.asyncio
async def test_a_passage_sharing_only_the_plans_generic_word_is_not_selected(
    tracker: Tracker,
) -> None:
    """RevY1 P2: two required targets share a subject word ("utility"); a
    passage matching only that word plus one distinctive word of one target
    is not selected -- the shared word must not count toward the two-token
    floor.
    """
    completer = ScriptedCompleter(
        decisions=_cross_topic_decisions(),
        outputs=[_cross_topic_main_reply],
    )

    await _run_cross_topic_state(
        tracker,
        completer,
        sub_topics=[
            _cross_topic_reading_sub_topic(),
            _generic_target_x_sub_topic(),
            _generic_target_y_sub_topic(),
        ],
        body=_GENERIC_TEST_BODY,
    )

    # One request: the page's own main extraction. The candidate shares
    # "utility" with both required targets' own questions and "claim" with
    # one of them, but "utility" is the plan's own shared subject word, so
    # only one non-generic token remains -- one short of the floor.
    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Passages owed a finding" not in requests[0]


# ---------------------------------------------------------------------------
# D2: the dissent re-ask (Fable's audit of run 7, CODE 1)
# ---------------------------------------------------------------------------
#
# Across four heads' audits the same class of statement has gone unmined: a
# page states a step, cause, figure or provision as fact in one place, and
# elsewhere -- on that same page or one the run read for another sub-topic
# -- carries another source's rejection, qualification or dating of it. The
# main extraction only ever asks a passage what it states, never what it
# disputes about a claim the run has already kept, so the disagreement is
# never asked about. The dissent re-ask runs after a read's own main
# extraction, the same S6 shape as the owed and cross-topic re-asks: a
# passage that carries a cue of disagreement or revision and shares words
# with a retained finding's own snippet is a candidate, and a finding it
# yields is marked ``disputes=True``, bound to the disputed finding's own
# target ids.

_DISSENT_TARGET_ID = "topic-01-target-01"
_DISSENT_TARGET_QUESTION = (
    "What trend do published assessments describe in the outreach "
    "program's enrollment?"
)
_DISSENT_URL = "https://outreach-notices.test/reports/enrollment-trend"
_DISSENT_TITLE = "Outreach program enrollment report"
_DISSENT_PREAMBLE = (
    "Community outreach coordinators publish a yearly summary of program "
    "activity, and this report reviews attendance across every site the "
    "program served during the period. "
) * 3
_DISSENT_FILLER = (
    "The report also lists every volunteer who took part in the outreach "
    "effort and the neighborhoods each volunteer visited during the year. "
) * 3
_DISSENT_STATED_SENTENCE = (
    "The annual assessment states enrollment saw a steep decline in the "
    "outreach program between 2015 and 2019."
)
_DISSENT_SENTENCE = (
    "However, later field surveys found little evidence for the decline "
    "the annual assessment describes in the outreach program."
)
_DISSENT_BODY = (
    f"{_DISSENT_PREAMBLE}\n\n"
    f"{_DISSENT_FILLER}{_DISSENT_STATED_SENTENCE}\n\n"
    f"{_DISSENT_SENTENCE}"
)
# Same body, minus the dissenting paragraph: nothing left on the page carries
# a cue of disagreement or revision.
_DISSENT_BODY_NO_CUE = (
    f"{_DISSENT_PREAMBLE}\n\n" f"{_DISSENT_FILLER}{_DISSENT_STATED_SENTENCE}"
)


def _dissent_sub_topic(priority: int = 1) -> SubTopic:
    return _sub_topic(
        "Outreach program enrollment", priority, coverage_id="topic-01"
    ).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=_DISSENT_TARGET_ID,
                    coverage_id="topic-01",
                    question=_DISSENT_TARGET_QUESTION,
                    measure="the enrollment trend published assessments describe",
                    required=False,
                )
            ]
        }
    )


def _dissent_decisions() -> list[object]:
    return [
        use_tool(
            "Find the report.",
            "web_search",
            '{"query": "outreach program enrollment trend"}',
        ),
        use_tool(
            "Read the report.", "web_scraper", f'{{"url": "{_DISSENT_URL}"}}'
        ),
        finish("The report covers enrollment.", "Enrollment trend reported."),
    ]


def _dissent_main_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The reading sub-topic's own finding, bound to its own target."""
    del schema
    read_id, locator, excerpt = _packet_passage_for(
        "steep decline", messages[1].content
    )
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "The annual assessment states enrollment saw a steep "
                    "decline in the outreach program between 2015 and 2019."
                ),
                source_url=_DISSENT_URL,
                source_title=_DISSENT_TITLE,
                confidence=0.8,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_DISSENT_TARGET_ID],
            )
        ]
    )


def _dissent_reask_reply(
    messages: list[ChatMessage], schema: type[SubTopicFindingsDraft]
) -> SubTopicFindingsDraft:
    """The dispute finding the dissent re-ask is asked to return."""
    del schema
    packet = messages[1].content
    assert "Statements these passages may dispute" in packet
    assert _DISSENT_TARGET_ID in packet
    read_id, locator, excerpt = _packet_passage_for("little evidence", packet)
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=(
                    "Later field surveys found little evidence for the "
                    "enrollment decline the annual assessment describes."
                ),
                source_url=_DISSENT_URL,
                source_title=_DISSENT_TITLE,
                confidence=0.7,
                read_id=read_id,
                locator=locator,
                snippet=excerpt,
                target_ids=[_DISSENT_TARGET_ID],
            )
        ]
    )


def _dissent_agent(
    tracker: Tracker, completer: ScriptedCompleter, *, body: str = _DISSENT_BODY
) -> ResearcherAgent:
    return _researcher(
        tracker,
        completer,
        search=FakeSearchClient(
            [search_response(title=_DISSENT_TITLE, url=_DISSENT_URL)]
        ),
        http=page_client(title=_DISSENT_TITLE, body=body),
        max_sub_topics=1,
    )


async def _run_dissent_state(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    body: str = _DISSENT_BODY,
) -> AgentRun[ResearchFindings]:
    agent = _dissent_agent(tracker, completer, body=body)
    async with tracker.session_span("session-1", "q"):
        return await agent.run(_state(sub_topics=[_dissent_sub_topic()]))


@pytest.mark.asyncio
async def test_a_dissenting_passage_yields_a_finding_bound_to_the_disputed_target(
    tracker: Tracker,
) -> None:
    """A page whose admitted text carries a passage disputing a step it also
    states elsewhere yields a re-ask packet and a finding with
    ``disputes=True`` bound to the disputed statement's own target.
    """
    completer = ScriptedCompleter(
        decisions=_dissent_decisions(),
        outputs=[_dissent_main_reply, _dissent_reask_reply],
    )

    outcome = await _run_dissent_state(tracker, completer)

    requests = _extraction_requests(completer)
    assert len(requests) == 2
    assert "Statements these passages may dispute" not in requests[0]
    assert "Statements these passages may dispute" in requests[1]

    disputes = [
        finding for finding in outcome.result.findings if finding.disputes
    ]
    assert len(disputes) == 1
    assert disputes[0].target_ids == [_DISSENT_TARGET_ID]
    assert disputes[0].source_url == _DISSENT_URL

    stated = [
        finding
        for finding in outcome.result.findings
        if not finding.disputes and _DISSENT_TARGET_ID in finding.target_ids
    ]
    assert len(stated) == 1


@pytest.mark.asyncio
async def test_a_page_with_no_dissent_cue_sends_no_dissent_packet(
    tracker: Tracker,
) -> None:
    """A page whose admitted text carries no cue of disagreement or revision
    is never sent a dissent re-ask.
    """
    completer = ScriptedCompleter(
        decisions=_dissent_decisions(),
        outputs=[_dissent_main_reply],
    )

    outcome = await _run_dissent_state(
        tracker, completer, body=_DISSENT_BODY_NO_CUE
    )

    requests = _extraction_requests(completer)
    assert len(requests) == 1
    assert "Statements these passages may dispute" not in requests[0]
    assert not any(finding.disputes for finding in outcome.result.findings)


def test_dispute_findings_are_exempt_from_the_finding_cap() -> None:
    """A dispute finding is the finding the dissent re-ask exists to protect
    (D2, Fable's audit of run 7), not corroborating volume, so a confidence
    ranking must not be the thing that drops it.

    Eight findings from one page and a finding cap of three; the two marked
    ``disputes=True`` are the least confident of the eight, well below the
    cap's own cutoff -- dropped by an ordinary confidence ranking exactly
    when the dissent re-ask exists to keep them.
    """
    findings = [
        _finding(
            "Alpha",
            "https://a.test/one",
            content=f"Finding {index}.",
            confidence=confidence,
        )
        for index, confidence in enumerate(
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], start=1
        )
    ]
    disputes = [
        finding.model_copy(
            update={"disputes": True, "target_ids": [PLANNED_TARGET_ID]}
        )
        for finding in findings[:2]
    ]

    budget = bound_sub_topic_findings([*disputes, *findings[2:]], max_findings=3)

    assert {finding.content for finding in budget.retained} == {
        "Finding 1.",
        "Finding 2.",
        "Finding 6.",
        "Finding 7.",
        "Finding 8.",
    }
    assert budget.dropped_cap == 3
