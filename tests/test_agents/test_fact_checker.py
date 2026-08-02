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
    build_claim,
    build_claim_drafts,
    claim_extraction_messages,
    claim_verification_messages,
    claimed_domains_for,
    independent_domains,
    insufficient_claim,
    known_source_urls,
    normalize_verdict,
    resolve_verdict,
    retrieved_source_urls,
    verdict_counts,
)
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Claim,
    ClaimVerdict,
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
)

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
