"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    build_claim_drafts,
    claim_extraction_messages,
    known_source_urls,
)
from deep_research.utils.types import (
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
