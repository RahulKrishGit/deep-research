"""Offline acquisition policy and read-admission regressions."""

from __future__ import annotations

import json

import pytest

from deep_research.agents.acquisition import (
    AcquisitionPolicy,
    build_acquisition_context,
    build_read_record_from_tool_result,
    next_acquisition_action,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import ReActDecision, ReActObservation, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.tools.base import ToolError, ToolResult
from deep_research.tools.passage_selection import select_relevant_passages
from deep_research.utils.types import (
    AcquisitionState,
    CandidateRecord,
    ResearchState,
    merge_research_state,
)
from tests.agent_fakes import EchoTool, agent_scope, finish, use_tool


def test_two_searches_require_read_but_failed_reads_do_not_deadlock() -> None:
    state = AcquisitionState(
        candidate_urls=["https://primary.example/report.pdf"],
        consecutive_searches=2,
        remaining_calls=3,
    )
    assert next_acquisition_action(state) == "read"

    state = state.model_copy(
        update={
            "candidate_urls": [],
            "denied_urls": ["https://primary.example/report.pdf"],
            "remaining_calls": 2,
            "consecutive_searches": 0,
        }
    )
    assert next_acquisition_action(state) == "search"


def test_acquisition_reducer_does_not_resurrect_consumed_candidates() -> None:
    url = "https://example.test/report.pdf"
    candidate = CandidateRecord(
        candidate_id="candidate-1",
        url=url,
        discovered_via="search",
    )
    previous = AcquisitionState(
        target_id="target-1",
        candidate_urls=[url],
        candidate_records={url: candidate},
        remaining_calls=2,
    )
    consumed = previous.model_copy(
        update={
            "candidate_urls": [],
            "attempted_urls": [url],
            "read_urls": [url],
            "candidate_records": {
                url: candidate.model_copy(update={"status": "read"})
            },
            "remaining_calls": 1,
        }
    )
    state = ResearchState(
        session_id="session-1",
        original_question="question",
        acquisition_state_by_target={"target-1": previous},
    )

    merged = merge_research_state(
        state,
        {"acquisition_state_by_target": {"target-1": consumed}},
    )

    result = merged.acquisition_state_by_target["target-1"]
    assert result.candidate_urls == []
    assert result.candidate_records[url].status == "read"
    assert result.remaining_calls == 1


def test_late_passage_is_selected() -> None:
    passages = {
        "p1": "Table of contents",
        "p80": "Queue delay prevents project commissioning.",
    }
    assert select_relevant_passages(passages, "queue delay commissioning", 1) == [
        "p80"
    ]


def test_explicit_document_suffix_routes_to_document_reader() -> None:
    policy = AcquisitionPolicy(
        state=AcquisitionState(
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
    )
    decision = ReActDecision(
        thought="Read PDF as HTML.",
        action="use_tool",
        tool_name="web_scraper",
        tool_input_json='{"url":"https://example.test/report.pdf"}',
    )

    result = policy.before_action(
        decision, {"url": "https://example.test/report.pdf"}
    )

    assert result.allowed is False
    assert "document_reader" in result.reason


def _read_result(
    *, success: bool = True, text: str = "late queue evidence"
) -> ToolResult:
    data = {
        "source": "https://example.test/report.pdf",
        "requested_source": "https://example.test/report.pdf",
        "resolved_source": "https://cdn.example.test/report.pdf",
        "title": "Report",
        "chunks": [{"text": text, "chunk_index": 0, "page": 80}],
        "content_sha256": "unused",
        "extraction_complete": True,
    }
    if success:
        from deep_research.agents.evidence import normalized_content_sha256

        data["content_sha256"] = normalized_content_sha256(text)
        return ToolResult(
            tool_name="document_reader", success=True, data=data, latency_ms=0
        )
    return ToolResult(
        tool_name="document_reader",
        success=False,
        data=data,
        error=ToolError(
            type="empty_document_content",
            message="document extraction produced no text",
        ),
        latency_ms=0,
    )


def test_failed_tool_payload_never_mints_a_read_record() -> None:
    assert (
        build_read_record_from_tool_result(
            _read_result(success=False), session_id="session-1"
        )
        is None
    )


def test_short_access_shell_is_not_admitted_as_usable_evidence() -> None:
    result = ToolResult(
        tool_name="web_scraper",
        success=True,
        data={
            "url": "https://example.test/blocked",
            "requested_url": "https://example.test/blocked",
            "resolved_url": "https://example.test/blocked",
            "title": "Checking your browser",
            "text": "Enable JavaScript and cookies to continue.",
            "content_sha256": "ignored",
            "extraction_complete": True,
        },
        latency_ms=0,
    )

    assert (
        build_read_record_from_tool_result(result, session_id="session-1")
        is None
    )


def test_shared_successful_read_cache_reselects_for_a_second_target() -> None:
    shared_reads = {}
    shared_evidence = {}
    shared_dispositions = []
    shared_audits = {}
    shared_cache = {}
    shared_network_ids = set()
    first = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-1",
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-1",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        boundary_audits=shared_audits,
        cache=shared_cache,
        network_read_ids=shared_network_ids,
    )
    result = _read_result()
    step = ReActStep(
        iteration=1,
        thought="Read source.",
        action="use_tool",
        tool_name="document_reader",
        tool_input={"source": "https://example.test/report.pdf"},
        observation=ReActObservation(
            tool_name="document_reader", success=True, summary="read"
        ),
        tool_result=result,
    )
    first.after_action(step)

    second = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-2",
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-2",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        boundary_audits=shared_audits,
        cache=shared_cache,
        network_read_ids=shared_network_ids,
    )
    decision = ReActDecision(
        thought="Reuse the source.",
        action="use_tool",
        tool_name="document_reader",
        tool_input_json='{"source":"https://example.test/report.pdf"}',
    )
    cached = second.before_action(decision, {"source": "https://example.test/report.pdf"})

    assert cached.allowed is True
    assert cached.charge_tool_budget is False
    assert cached.result is not None
    assert first.acquired_work_count == 1
    second.after_action(
        step.model_copy(update={"tool_result": cached.result})
    )
    assert second.acquired_work_count == 1
    assert len(shared_reads) == 1
    assert any("target-2" in unit.target_ids for unit in shared_evidence.values())


def test_acquisition_context_keeps_ids_when_a_complete_record_overflows() -> None:
    result = _read_result(text="queue delay commissioning " + "x" * 120)
    read = build_read_record_from_tool_result(result, session_id="session-1")
    assert read is not None
    state = AcquisitionState(
        target_id="target-1",
        candidate_urls=["https://example.test/report.pdf"],
        pending_passage_ids=[f"{read.read_id}/page-80-chunk-0"],
        remaining_calls=2,
        remaining_model_turns=1,
    )
    context = build_acquisition_context(state, {read.read_id: read}, {}, limit=240)
    assert "target_id=target-1" in context
    assert "pending_passage_ids=" in context
    assert "continuation_ids=" in context


@pytest.mark.asyncio
async def test_policy_rejection_is_an_observation_without_tool_budget_charge(
    tracker,
) -> None:
    policy_calls: list[str] = []

    def policy(decision: ReActDecision, tool_input: dict[str, object]):
        del tool_input
        policy_calls.append(decision.tool_name or "")
        return False

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset([EchoTool(tracker)], allowed=["echo"]),
            decide=_decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            job_id="job-1",
        )
    assert policy_calls == ["echo"]
    assert run.tool_calls == 0
    assert run.steps[0].observation is not None
    assert run.steps[0].observation.error_type == "agent_tool_policy_rejected"
    assert run.steps[0].proposal_id == "job-1/turn-1/call-0"


@pytest.mark.asyncio
async def test_policy_tracks_model_turn_capacity_separately(tracker) -> None:
    policy = AcquisitionPolicy(
        state=AcquisitionState(remaining_calls=1, remaining_model_turns=2),
        session_id="session-1",
    )

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset([EchoTool(tracker)], allowed=["echo"]),
            decide=_decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            job_id="job-1",
        )

    assert run.iterations == 2
    assert policy.state.remaining_model_turns == 0


async def _decisions(
    iteration: int, steps: tuple[object, ...]
) -> tuple[ReActDecision, ...]:
    del steps
    if iteration == 1:
        return (
            use_tool("Try echo", "echo", json.dumps({"value": "x"})),
        )
    return (finish("Done", "No call needed."),)
