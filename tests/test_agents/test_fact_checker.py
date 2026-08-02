"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

from typing import get_args

import pytest

from deep_research.agents.fact_checker import (
    VERDICT_VALUES,
    ClaimDraft,
    ClaimsDraft,
    ClaimTask,
    ClaimVerdictDraft,
    FactCheckerAgent,
    VerifiedClaims,
    build_claim,
    build_claim_drafts,
    claim_checked_event,
    claim_extraction_messages,
    claim_verification_messages,
    claimed_domains_for,
    fact_check_completed_event,
    independent_domains,
    insufficient_claim,
    known_source_urls,
    normalize_verdict,
    resolve_verdict,
    retrieved_source_urls,
    verdict_counts,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ClaimVerdict,
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import FakeSearchClient, fact_checker_tools, search_response

CHECK_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _check_finding(
    url: str = "https://example.org/a",
    *,
    content: str = "Logical error rates fell below break-even in 2025.",
    sub_topic: str = "Alpha",
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title="QEC 2025",
        extracted_at=CHECK_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _scored(url: str, *, low: bool = False) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.2 if low else 0.9,
        recency_score=0.2 if low else 0.9,
        relevance_score=0.2 if low else 0.9,
        corroboration_score=0.0 if low else 1.0,
        overall_score=0.16 if low else 0.9,
        rationale="Because.",
        low_confidence=low,
    )


def _check_state(
    findings: list[Finding] | None = None,
    sources: list[ScoredSource] | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        raw_findings=findings or [],
        evaluated_sources=sources or [],
        memory_context=MemorySnapshot(),
    )


def test_known_source_urls_are_canonical_and_deduplicated() -> None:
    state = _check_state(
        [
            _check_finding("https://example.org/a"),
            _check_finding("https://WWW.example.org/a/"),
            _check_finding("https://other.test/b"),
        ]
    )

    assert known_source_urls(state) == [
        "https://example.org/a",
        "https://other.test/b",
    ]


def test_claim_drafts_keep_only_urls_the_findings_actually_used() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Logical error rates fell below break-even in 2025.",
                source_urls=[
                    "https://WWW.example.org/a/",
                    "https://invented.test/x",
                ],
            )
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert rejected == []
    assert claims[0].source_urls == ["https://example.org/a"]


def test_a_claim_with_no_known_source_is_rejected_with_a_safe_reason() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(text="Invented.", source_urls=["https://invented.test/x"]),
            ClaimDraft(text="Real.", source_urls=["https://example.org/a"]),
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert [claim.text for claim in claims] == ["Real."]
    assert rejected == ["claim 1: no source url from the collected findings"]
    assert "invented.test" not in " ".join(rejected)


def test_a_blank_claim_is_rejected() -> None:
    draft = ClaimsDraft(
        claims=[ClaimDraft(text="   ", source_urls=["https://example.org/a"])]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert claims == []
    assert rejected == ["claim 1: blank claim text"]


def test_extraction_messages_show_findings_and_source_quality() -> None:
    state = _check_state(
        [_check_finding()],
        [_scored("https://example.org/a", low=True)],
    )

    messages = claim_extraction_messages(state, max_findings=10)

    assert messages[0].role == "developer"
    body = messages[1].content
    assert "How mature is quantum error correction?" in body
    assert "1. [Alpha] Logical error rates fell below break-even" in body
    assert "https://example.org/a: 0.16 (LOW CONFIDENCE)" in body
    assert "Return an empty list" in body


def test_extraction_messages_cap_the_number_of_findings_rendered() -> None:
    findings = [
        _check_finding(content=f"Fact number {index}.")
        for index in range(1, 6)
    ]

    messages = claim_extraction_messages(_check_state(findings), max_findings=2)

    body = messages[1].content
    assert "Fact number 1." in body
    assert "Fact number 3." not in body


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
            summary=f"{tool_name} ran",
        ),
        tool_result=(
            ToolResult(
                tool_name=tool_name, success=True, data=data, latency_ms=1.0
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


def _verdict_draft(
    *,
    verdict: str = "verified",
    confidence: float = 0.9,
    evidence: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> ClaimVerdictDraft:
    return ClaimVerdictDraft(
        verdict=verdict,
        confidence=confidence,
        evidence=evidence if evidence is not None else ["A third party agrees."],
        contradictions=contradictions or [],
    )


def _claim_draft(
    *, urls: list[str] | None = None
) -> ClaimDraft:
    return ClaimDraft(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=urls or ["https://example.org/a"],
    )


def test_verdict_values_match_the_shared_claim_verdict_type() -> None:
    assert set(VERDICT_VALUES) == set(get_args(ClaimVerdict))


def test_retrieved_urls_are_pulled_from_every_evidence_carrying_tool() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            ),
            _tool_step(
                2,
                "web_scraper",
                {"url": "https://WWW.third.test/x/", "text": "Body."},
            ),
            _tool_step(
                3,
                "document_reader",
                {"source": "https://fourth.test/d.csv", "chunks": ["a"]},
            ),
            _tool_step(
                4,
                "query_memory",
                {"matches": [{"metadata": {"source_url": "https://fifth.test/m"}}]},
            ),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == [
        "https://third.test/x",
        "https://fourth.test/d.csv",
        "https://fifth.test/m",
    ]


def test_empty_and_failed_tool_payloads_yield_no_urls() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", {"results": []}),
            _tool_step(2, "web_scraper", {"url": "https://a.test/x", "text": " "}),
            _tool_step(
                3,
                "document_reader",
                {"source": "https://b.test/d", "chunks": []},
            ),
            _tool_step(4, "web_search", None, success=False),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == []


def test_the_claims_own_domains_are_never_independent() -> None:
    assert claimed_domains_for(["https://www.example.org/a"]) == ["example.org"]
    assert independent_domains(
        ["https://example.org/other", "https://third.test/x"],
        claimed_domains=["example.org"],
    ) == ["third.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("verified", "verified"),
        ("  CONTRADICTED ", "contradicted"),
        ("insufficient evidence", "insufficient_evidence"),
        ("insufficient-evidence", "insufficient_evidence"),
        ("probably true", "insufficient_evidence"),
        ("", "insufficient_evidence"),
    ],
)
def test_verdict_normalization_is_total(raw: str, expected: str) -> None:
    assert normalize_verdict(raw) == expected


def test_a_claim_with_no_independent_source_is_insufficient() -> None:
    verdict, confidence = resolve_verdict(_verdict_draft(), independent=[])

    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)


def test_reported_contradictions_downgrade_a_verified_verdict() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(
            verdict="verified", contradictions=["A regulator disputes it."]
        ),
        independent=["third.test"],
    )

    assert verdict == "contradicted"
    assert confidence == pytest.approx(0.9)


def test_confidence_is_clamped_and_zeroed_for_insufficient_evidence() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(verdict="insufficient_evidence", confidence=0.8),
        independent=["third.test"],
    )
    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)

    _, high = resolve_verdict(
        _verdict_draft(confidence=4.0), independent=["third.test"]
    )
    assert high == pytest.approx(1.0)


def test_a_built_claim_keeps_its_own_sources_and_the_models_evidence() -> None:
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(evidence=["Third party agrees."]),
        independent=["third.test"],
    )

    assert isinstance(claim, Claim)
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.verdict == "verified"
    assert claim.evidence == ["Third party agrees."]
    assert claim.contradictions == []


def test_an_insufficient_claim_names_its_reason_and_invents_no_confidence() -> None:
    claim = insufficient_claim(_claim_draft(), reason="loop_failed")

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.evidence == []
    assert claim.contradictions == []
    assert claim.source_urls == ["https://example.org/a"]


def test_an_insufficient_claim_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        insufficient_claim(_claim_draft(), reason="because")


def test_verification_messages_carry_the_claim_and_its_evidence() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )
    task = ClaimTask(
        instruction="Verify one claim.",
        claim=_claim_draft(),
        claimed_domains=["example.org"],
    )

    messages = claim_verification_messages(
        task, run, evidence_chars=500, independent=["third.test"]
    )

    body = messages[1].content
    assert "Logical error rates fell below break-even in 2025." in body
    assert "https://example.org/a" in body
    assert "third.test" in body
    assert "insufficient_evidence" in body


def test_verdict_counts_cover_every_verdict_value() -> None:
    claims = [
        build_claim(
            _claim_draft(), _verdict_draft(), independent=["third.test"]
        ),
        insufficient_claim(_claim_draft(), reason="no_independent_source"),
    ]

    counts = verdict_counts(claims)

    assert counts == {
        "verified": 1,
        "unverified": 0,
        "contradicted": 0,
        "insufficient_evidence": 1,
    }


def _checker(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[object] | None = None,
    max_claims: int = 5,
) -> FactCheckerAgent:
    return FactCheckerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="fact_checker", max_entries=20
        ),
        tools=tools if tools is not None else fact_checker_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        max_claims=max_claims,
    )


@pytest.mark.asyncio
async def test_extraction_keeps_only_claims_backed_by_real_sources(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    ),
                    ClaimDraft(
                        text="Invented.",
                        source_urls=["https://invented.test/x"],
                    ),
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert provider_failed is False
    assert [claim.text for claim in claims] == [
        "Break-even was crossed in 2025."
    ]
    assert errors[0].error_type == "fact_checker_invalid_claim"
    assert errors[0].recoverable is True


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_without_findings(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)

    claims, errors, provider_failed = await agent.extract_claims(_check_state([]))

    assert claims == []
    assert provider_failed is False
    assert errors[0].error_type == "fact_checker_no_findings"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_provider_failure_is_non_recoverable(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert claims == []
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["exception_type"] == "ProviderTimeoutError"


@pytest.mark.asyncio
async def test_a_claim_with_only_its_own_domain_retrieved_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {"url": "https://example.org/other", "text": "Same publisher."},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "no_independent_source"
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_loop_that_died_to_the_provider_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="provider_error",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "loop_failed"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_every_tool_call_failing_is_insufficient_not_unverified(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", None, success=False),
            _tool_step(2, "web_scraper", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "no_independent_source"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_independent_evidence_produces_a_model_verdict(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_verdict_draft(verdict="verified")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "verified"
    assert claim.confidence == pytest.approx(0.9)
    assert reason is None
    assert errors == []
    assert provider_failed is False


@pytest.mark.asyncio
async def test_a_contradiction_survives_a_verified_model_answer(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator published the opposite figure."],
            )
        ]
    )
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "contradicted"
    assert claim.contradictions == [
        "A regulator published the opposite figure."
    ]
    assert reason is None


@pytest.mark.asyncio
async def test_a_verification_provider_failure_is_insufficient_not_invented(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "provider_unavailable"
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_verification_provider_error"
    assert errors[0].recoverable is False


def test_state_update_carries_verified_claims_and_errors(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(agent_name="fact_checker", stop_reason="finished")

    update = agent.state_update(VerifiedClaims(claims=[claim]), run)

    assert update["verified_claims"] == [claim]
    assert update["errors"] == []


def _check_decisions() -> list[object]:
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        finish("I have independent material.", "Checked."),
    ]


def test_the_per_claim_event_reports_tool_calls_and_the_verdict() -> None:
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        iterations=2,
        tool_calls=2,
    )

    event = claim_checked_event(
        claim, run, index=1, independent_sources=0,
        reason="no_independent_source",
    )

    assert event.event_type == "fact_checker.claim.checked"
    assert event.source == "agent.fact_checker"
    assert event.metadata["verdict"] == "insufficient_evidence"
    assert event.metadata["tool_calls"] == 2
    assert event.metadata["independent_sources"] == 0
    assert event.metadata["reason"] == "no_independent_source"
    assert event.metadata["contradictions"] == 0


def test_the_completed_event_reports_every_verdict_count() -> None:
    verified = build_claim(
        _claim_draft(), _verdict_draft(), independent=["third.test"]
    )
    contradicted = build_claim(
        _claim_draft(),
        _verdict_draft(contradictions=["Disputed."]),
        independent=["third.test"],
    )

    event = fact_check_completed_event(
        [verified, contradicted], tool_calls=5
    )

    assert event.event_type == "fact_checker.fact_check.completed"
    assert event.metadata["claim_count"] == 2
    assert event.metadata["verified"] == 1
    assert event.metadata["contradicted"] == 1
    assert event.metadata["unverified"] == 0
    assert event.metadata["insufficient_evidence"] == 0
    assert event.metadata["contradiction_count"] == 1
    assert event.metadata["tool_calls"] == 5


@pytest.mark.asyncio
async def test_a_full_run_verifies_each_claim_and_reports_the_counts(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    )
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state(
        [_check_finding("https://example.org/a")],
        [_scored("https://example.org/a")],
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.agent_name == "fact_checker"
    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == ["verified"]

    merged = merge_research_state(state, outcome.state_update)
    assert len(merged.verified_claims) == 1

    events = outcome.state_update["events"]
    types = [event.event_type for event in events]
    assert types[0] == "fact_checker.claims.extracted"
    assert "fact_checker.claim.checked" in types
    assert types[-1] == "fact_checker.fact_check.completed"
    completed = events[-1]
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 1
    assert completed.metadata["contradiction_count"] == 0
    checked = next(
        event for event in events
        if event.event_type == "fact_checker.claim.checked"
    )
    assert checked.metadata["tool_calls"] >= 1


@pytest.mark.asyncio
async def test_a_run_without_findings_verifies_nothing_and_says_so(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    state = _check_state([])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.claims == []
    assert outcome.errors[0].error_type == "fact_checker_no_findings"
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["claim_count"] == 0
    assert completed.metadata["insufficient_evidence"] == 0


@pytest.mark.asyncio
async def test_a_verification_provider_failure_stops_further_claims(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text="First.", source_urls=["https://example.org/a"]),
                    ClaimDraft(text="Second.", source_urls=["https://example.org/a"]),
                ]
            ),
            ProviderTimeoutError("timed out"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == [
        "insufficient_evidence"
    ]
    assert outcome.react.stop_reason == "provider_error"
    types = {error.error_type for error in outcome.errors}
    assert "fact_checker_verification_provider_error" in types
