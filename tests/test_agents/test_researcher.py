"""Tests for the Researcher's contracts, selection, and prompt helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.researcher import (
    DEFAULT_MAX_SUB_TOPICS,
    HIGH_PRIORITY_THRESHOLD,
    FindingDraft,
    ResearcherAgent,
    ResearchFindings,
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
from deep_research.agents.steps import (
    ReActObservation,
    ReActRun,
    ReActStep,
    summarize_text,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeMemory,
    FakeSearchClient,
    research_tools,
    search_response,
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


def _clock() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _researcher(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
    config: AgentRuntimeConfig | None = None,
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(tracker, search=search, memory=memory),
        config=config or AgentRuntimeConfig(max_iterations=4, tool_budget=4),
        max_sub_topics=max_sub_topics,
        clock=_clock,
    )


def test_the_researcher_declares_its_identity_and_tools() -> None:
    assert ResearcherAgent.name == "researcher"
    assert ResearcherAgent.allowed_tools == (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
        "save_to_memory",
    )


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
        steps=[_tool_step(1, "web_search", {"results": ["a"]})],
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
        steps=[_tool_step(1, "web_search", {"results": ["a"]})],
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
        steps=[_tool_step(1, "web_search", {"results": ["a"]})],
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
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _researcher(tracker, completer)
    task = SubTopicTask(
        instruction="Gather evidence for Alpha.", sub_topic=_sub_topic("Alpha")
    )
    run = ReActRun(
        agent_name="researcher",
        stop_reason="finished",
        steps=[_tool_step(1, "web_search", {"results": ["a"]})],
        iterations=1,
        tool_calls=1,
    )

    findings, errors, provider_failed = await agent.extract_findings(task, run)

    assert findings == []
    assert provider_failed is True
    assert len(errors) == 1
    assert errors[0].error_type == "researcher_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["exception_type"] == "ProviderTimeoutError"
    assert "timed out" not in str(errors[0].details)


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


def _findings_draft(content: str = "Logical error rates fell below break-even.") -> (
    SubTopicFindingsDraft
):
    return SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content=content,
                source_url="https://example.test/qec",
                source_title="Quantum error correction in 2025",
                confidence=0.8,
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
    assert outcome.react.tool_calls == 4
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
        sub_topics=[_sub_topic("Alpha", 1), _sub_topic("Beta", 5)],
        critique=_critique(
            gaps=["Beta is completely uncovered."],
            recommended_queries=["beta throughput 2025"],
        ),
    )

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    started = outcome.state_update["events"][0]
    assert started.metadata["sub_topic"] == "Beta"
    loop_body = completer.calls[0][2][1].content
    assert "- beta throughput 2025" in loop_body
    assert "Sub-topic: Beta" in loop_body


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
    *also* succeeds, but its extraction call raises ``ProviderTimeoutError``
    — the failure specifically identified as escaping ``run`` uncaught and
    destroying every finding collected so far. Sub-topic Gamma must never be
    started at all.
    """
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search Alpha.", "web_search", '{"query": "alpha 2025"}'),
            finish("Done with Alpha.", "Alpha answer."),
            use_tool("Search Beta.", "web_search", '{"query": "beta 2025"}'),
            finish("Done with Beta.", "Beta answer."),
        ],
        outputs=[_findings_draft(), ProviderTimeoutError("timed out")],
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
            _sub_topic("Alpha", 1),
            _sub_topic("Beta", 2),
            _sub_topic("Gamma", 2),
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
    assert skipped[0].recoverable is True


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

    second_loop_body = completer.calls[1][2][1].content
    assert "(no notes yet)" in second_loop_body
