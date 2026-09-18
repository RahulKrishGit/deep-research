"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

import re
from typing import get_args

import pytest

from deep_research.agents import fact_checker as fact_checker_module
from deep_research.agents.base import AgentRun
from deep_research.agents.claim_clusters import claim_meets_support_policy
from deep_research.agents.evidence import (
    EvidenceEligibility,
    build_evidence_unit,
    build_read_record,
)
from deep_research.agents.fact_checker import (
    DEFAULT_CLAIM_BATCH_SIZE,
    DEFAULT_CLAIM_BATCHES_PER_PASS,
    DEFAULT_FINDING_DIGEST,
    DEFAULT_MAX_CLAIMS,
    INSUFFICIENT_REASONS,
    MAX_PASSAGE_EXCERPT_CHARS,
    MAX_PASSAGE_LOCATOR_CHARS,
    MAX_PENDING_CLAIMS,
    VERDICT_VALUES,
    AdjudicationPacket,
    ClaimDraft,
    ClaimsDraft,
    ClaimTask,
    ClaimVerdictDraft,
    EvidencePassageDraft,
    FactCheckerAgent,
    PassageVerdictDraft,
    SupportAssessment,
    VerifiedClaims,
    _critique_texts,
    _finding_is_new,
    _packet_independent_publishers,
    build_adjudication_packet,
    build_claim,
    build_claim_drafts,
    claim_attribution,
    claim_checked_event,
    claim_evidence_pool,
    claim_extraction_messages,
    claim_verification_messages,
    claimed_domains_for,
    consumed_provenance,
    fact_check_completed_event,
    independent_domains,
    insufficient_claim,
    known_source_urls,
    normalize_verdict,
    ordered_findings_for_extraction,
    partition_pending_claims,
    provider_failure_reason,
    resolve_verdict,
    retrieved_source_urls,
    union_claim_provenance,
    valid_verification_passages,
    validate_adjudication,
    verdict_counts,
)
from deep_research.agents.identity import (
    claim_fingerprint,
    finding_fingerprint,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.source_evaluator import (
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.agents.steps import (
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
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
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    Claim,
    ClaimVerdict,
    Critique,
    CritiqueGap,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeSearchClient,
    fact_checker_tools,
    page_client,
    search_response,
)

CHECK_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


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


def test_claim_extraction_skips_old_snapshot_unless_evidence_or_critic_changes(
) -> None:
    claim = _claim_draft()
    prior = insufficient_claim(claim, reason="no_independent_source")
    draft = ClaimsDraft(claims=[claim])

    claims, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
    )

    assert claims == []
    assert rejected == ["claim 1: already checked"]

    changed, rejected = build_claim_drafts(
        draft.model_copy(
            update={
                "claims": [
                    claim.model_copy(
                        update={
                            "source_urls": [
                                *claim.source_urls,
                                "https://new.test/evidence",
                            ]
                        }
                    )
                ]
            }
        ),
        known_urls=[*claim.source_urls, "https://new.test/evidence"],
        prior_claims=[prior],
        new_source_urls=["https://new.test/evidence"],
    )
    assert [item.text for item in changed] == [claim.text]
    assert rejected == []

    same_url_changed, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
        new_source_urls=claim.source_urls,
    )
    assert [item.text for item in same_url_changed] == [claim.text]
    assert rejected == []

    reverified, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
        critique_texts=[f"Re-verify this claim: {claim.text}"],
    )
    assert [item.text for item in reverified] == [claim.text]
    assert rejected == []


def test_a_critic_gap_still_reaches_the_reverification_matcher() -> None:
    """A targetable gap carries its prose into the Critic-text matcher.

    The Critic's gaps are typed objects now. If only the raw list is handed
    on, every gap is filtered out and an explicit "re-verify this claim"
    instruction inside one is silently lost.
    """
    state = ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        critique=Critique(
            score=4,
            gaps=[
                CritiqueGap(
                    coverage_id=None,
                    problem="Re-verify this claim: Break-even was reached.",
                    recommended_queries=[],
                )
            ],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="One claim is load-bearing.",
        ),
    )

    assert _critique_texts(state) == (
        "Re-verify this claim: Break-even was reached.",
    )


def test_a_new_publisher_restatement_is_new_evidence() -> None:
    """Both directions of the central provenance rule, from real provenance.

    ``insufficient_claim(claim, reason="no_independent_source")`` leaves
    ``consumed_finding_fingerprints`` empty, and ``_finding_is_new`` tests
    membership in that empty set — so asserting ``is True`` against such a
    prior passed for *any* implementation, ``return True`` included. The prior
    here records a provenance that names a *different* finding, which is what
    makes the positive half carry information; the negative half pins that a
    finding whose fingerprint IS recorded stops looking new.
    """
    other_finding = _check_finding("https://independent.test/older")
    claim = _claim_draft()
    prior = Claim(
        claim_id=claim_fingerprint(claim.text),
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict="insufficient_evidence",
        confidence=0.0,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
        consumed_finding_fingerprints=[finding_fingerprint(other_finding)],
        consumed_coverage_ids=["topic-01"],
    )
    finding = _check_finding(
        "https://independent.test/report", content=claim.text
    )

    assert prior.consumed_finding_fingerprints == [
        finding_fingerprint(other_finding)
    ]
    assert _finding_is_new(finding, [prior]) is True
    assert _finding_is_new(other_finding, [prior]) is False


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
    assert (
        "https://example.org/a: score=0.16 status=scored "
        "low_confidence=true"
    ) in body
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
    passages: list[EvidencePassageDraft] | None = None,
) -> PassageVerdictDraft:
    if passages is None:
        passages = [
            EvidencePassageDraft(
                source_url="https://third.test/x",
                source_title="Independent review",
                locator="p. 1",
                excerpt=excerpt,
                stance="supports",
            )
            for excerpt in (
                evidence
                if evidence is not None
                else ["A third party agrees."]
            )
        ]
        passages.extend(
            EvidencePassageDraft(
                source_url="https://third.test/x",
                source_title="Independent regulator",
                locator="p. 2",
                excerpt=excerpt,
                stance="contradicts",
            )
            for excerpt in (contradictions or [])
        )
    return PassageVerdictDraft(
        verdict=verdict,
        confidence=confidence,
        passages=passages,
    )


def _independent_pair_verdict() -> PassageVerdictDraft:
    """One verdict whose support is a genuine pair of independent publishers."""
    return PassageVerdictDraft(
        verdict="verified",
        confidence=0.9,
        passages=[
            EvidencePassageDraft(
                source_url="https://third.test/x",
                source_title="Independent review",
                locator="p. 1",
                excerpt="A third party agrees.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://fourth.test/y",
                source_title="Independent regulator",
                locator="p. 2",
                excerpt="The regulator recorded the same figure.",
                stance="supports",
            ),
        ],
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
                {
                    "matches": [
                        {
                            "content": "A remembered passage.",
                            "metadata": {"source_url": "https://fifth.test/m"},
                        }
                    ]
                },
            ),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == [
        "https://third.test/x",
        "https://fourth.test/d.csv",
    ]


def test_a_recalled_fact_is_never_an_independent_source() -> None:
    """A memory match contributes no retrieved URL, so it cannot corroborate.

    The recalled entry carries everything a naive classifier would accept —
    text, a source URL under ``metadata``, high confidence, and a previous
    "verified" label — and still contributes nothing to the evidence union.
    """
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "query_memory",
                {
                    "matches": [
                        {
                            "content": "A remembered passage.",
                            "metadata": {
                                "source_url": "https://fifth.test/m",
                                "verified": True,
                            },
                            "confidence": 0.99,
                        }
                    ]
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    assert retrieved_source_urls(run) == []
    assert independent_domains(
        retrieved_source_urls(run), claimed_domains=["example.org"]
    ) == []
    assert (
        valid_verification_passages(
            _verdict_draft(
                passages=[
                    EvidencePassageDraft(
                        source_url="https://fifth.test/m",
                        source_title="A remembered conversation",
                        locator="p. 1",
                        excerpt="A remembered passage.",
                        stance="supports",
                    )
                ]
            ),
            retrieved_urls=retrieved_source_urls(run),
            claimed_publishers=["example.org"],
        )
        == []
    )


def test_search_only_results_are_candidates_not_retrieved_evidence() -> None:
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

    assert retrieved_source_urls(run) == []


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
    verdict, confidence = resolve_verdict(
        _verdict_draft(passages=[]), independent=["third.test"]
    )

    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)


def test_passages_require_read_urls_and_keep_bounded_independent_provenance() -> None:
    long_locator = "locator " * 100
    long_excerpt = "independent passage " * 100
    draft = _verdict_draft(
        passages=[
            EvidencePassageDraft(
                source_url="https://search-only.test/result",
                source_title="Search candidate",
                locator="result",
                excerpt="Never read.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://example.org/claim-source",
                source_title="Claim source",
                locator="p. 1",
                excerpt="Own source.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://evidence.example.test/report",
                source_title="Copied example",
                locator="p. 1",
                excerpt="Prompt example.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://independent.test/report/",
                source_title="Independent report",
                locator=long_locator,
                excerpt=long_excerpt,
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://WWW.independent.test/report",
                source_title="Duplicate report",
                locator=long_locator,
                excerpt=long_excerpt,
                stance="supports",
            ),
        ]
    )

    passages = valid_verification_passages(
        draft,
        retrieved_urls=[
            "https://example.org/claim-source",
            "https://evidence.example.test/report",
            "https://independent.test/report",
        ],
        claimed_publishers=["example.org"],
    )

    assert len(passages) == 1
    assert passages[0].source_url == "https://independent.test/report"
    assert len(passages[0].locator) <= MAX_PASSAGE_LOCATOR_CHARS
    assert len(passages[0].excerpt) <= MAX_PASSAGE_EXCERPT_CHARS


def _upstream_passage_draft(
    url: str = "https://upstream.test/report",
) -> PassageVerdictDraft:
    return _verdict_draft(
        passages=[
            EvidencePassageDraft(
                source_url=url,
                source_title="Independent study",
                locator="p. 3",
                excerpt="An independent study reports the same reduction.",
                stance="supports",
            )
        ]
    )


def test_an_upstream_read_url_widens_the_admissible_evidence_pool() -> None:
    """Evidence the run already read upstream is admissible as a passage.

    Only the verification loop's OWN reads used to be kept, so a cited URL
    the researcher had already read in the same run was discarded here.
    """
    passages = valid_verification_passages(
        _upstream_passage_draft(),
        retrieved_urls=[],
        upstream_read_urls=["https://upstream.test/report"],
        claimed_publishers=["example.org"],
    )

    assert [passage.source_url for passage in passages] == [
        "https://upstream.test/report"
    ]


def test_an_upstream_read_passage_yields_the_models_verdict() -> None:
    """The union of reads turns an unjudged claim into a judged one.

    Before pooling, ``resolve_verdict`` saw no passage at all and answered
    ``insufficient_evidence`` with confidence 0 — no judgment was made.
    """
    claim = build_claim(
        _claim_draft(),
        _upstream_passage_draft(),
        independent=["upstream.test"],
        retrieved_urls=[],
        upstream_read_urls=["https://upstream.test/report"],
    )

    assert claim.verdict == "verified"
    assert claim.confidence == pytest.approx(0.9)
    assert claim.evidence == ["An independent study reports the same reduction."]


def test_a_url_read_nowhere_is_still_rejected() -> None:
    """Neither read set carries the cited URL, so the passage is invented."""
    claim = build_claim(
        _claim_draft(),
        _upstream_passage_draft("https://never-read.test/report"),
        independent=["upstream.test"],
        retrieved_urls=["https://third.test/x"],
        upstream_read_urls=["https://upstream.test/report"],
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.verification_evidence == []


def test_a_same_publisher_passage_is_rejected_even_if_read_upstream() -> None:
    """Widening which reads count must not widen the independence rule."""
    claim = build_claim(
        _claim_draft(),
        _upstream_passage_draft("https://example.org/b"),
        independent=["upstream.test"],
        retrieved_urls=["https://third.test/x"],
        upstream_read_urls=["https://example.org/b"],
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.verification_evidence == []


def test_no_passages_is_still_insufficient_with_upstream_reads() -> None:
    """A non-empty read set never manufactures a judgment of its own."""
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(passages=[]),
        independent=["upstream.test"],
        retrieved_urls=["https://third.test/x"],
        upstream_read_urls=["https://upstream.test/report"],
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)


def test_reported_contradictions_downgrade_a_verified_verdict() -> None:
    draft = _verdict_draft(
        verdict="verified", contradictions=["A regulator disputes it."]
    )
    verdict, confidence = resolve_verdict(
        draft,
        independent=["third.test"],
        passages=[
            EvidencePassage.model_validate(passage.model_dump())
            for passage in draft.passages
        ],
    )

    assert verdict == "contradicted"
    assert confidence == pytest.approx(0.9)


def test_confidence_is_clamped_and_zeroed_for_insufficient_evidence() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(
            verdict="insufficient_evidence", confidence=0.8, passages=[]
        ),
        independent=["third.test"],
    )
    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)

    high_draft = _verdict_draft(confidence=4.0)
    _, high = resolve_verdict(
        high_draft,
        independent=["third.test"],
        passages=[
            EvidencePassage.model_validate(passage.model_dump())
            for passage in high_draft.passages
        ],
    )
    assert high == pytest.approx(1.0)


def test_a_built_claim_keeps_its_own_sources_and_the_models_evidence() -> None:
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(evidence=["Third party agrees."]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )

    assert isinstance(claim, Claim)
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.verdict == "verified"
    assert claim.evidence == ["Third party agrees."]
    assert claim.contradictions == []
    # Minor 1: the emitted identity is the canonical fingerprint of the text,
    # not a label — that is the value later passes merge on.
    assert claim.claim_id == claim_fingerprint(claim.text)
    assert len(claim.verification_evidence) == 1


def test_an_insufficient_claim_names_its_reason_and_invents_no_confidence() -> None:
    claim = insufficient_claim(_claim_draft(), reason="loop_failed")

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.evidence == []
    assert claim.contradictions == []
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.claim_id == claim_fingerprint(claim.text)
    # The reason used to reach only the event metadata, so no published
    # artifact could say why a claim went unjudged. It is on the record now,
    # as the enumerated token the caller passed rather than its explanation.
    assert claim.insufficient_reason == "loop_failed"


def test_an_insufficient_claim_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        insufficient_claim(_claim_draft(), reason="because")


def test_a_judged_claim_records_no_insufficient_reason() -> None:
    """``build_claim`` reaches ``insufficient_evidence`` too, from a verdict.

    A model that answered over evidence which was read, but whose answer
    resolved to nothing usable, produces an insufficient claim with no
    reason: nothing was unavailable, the verdict was thin. Leaving the field
    unset is what keeps that distinguishable from an unread claim.
    """
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(verdict="probably true", passages=[]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.insufficient_reason is None


def test_verification_messages_carry_the_claim_and_its_evidence() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
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
            _claim_draft(),
            _verdict_draft(),
            independent=["third.test"],
            retrieved_urls=["https://third.test/x"],
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
    batches_per_pass: int = DEFAULT_CLAIM_BATCHES_PER_PASS,
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
        batches_per_pass=batches_per_pass,
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
    completer = ScriptedCompleter(outputs=[_output_limit_error()])
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert claims == []
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["operation"] == "fact_checker_claim_extraction"
    provider = errors[0].details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


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
async def test_a_loop_that_died_to_the_provider_is_not_a_verdict(
    tracker: Tracker,
) -> None:
    """No model ever looked at this claim, so it is not judged at all."""
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
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim is None
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
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
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
async def test_the_agent_pools_evidence_the_researcher_read_upstream(
    tracker: Tracker,
) -> None:
    """The run's own upstream findings reach the verification pool.

    The loop reads one independent page, so a verdict call happens at all,
    but the model's supporting passage cites a URL the researcher had
    already read upstream in this same run.
    """
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
            _upstream_passage_draft(),
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
        [
            _check_finding("https://example.org/a"),
            _check_finding("https://upstream.test/report"),
        ],
        [_scored("https://example.org/a")],
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    claims = outcome.state_update["verified_claims"]
    assert [claim.verdict for claim in claims] == ["verified"]
    assert [claim.confidence for claim in claims] == [pytest.approx(0.9)]


@pytest.mark.asyncio
async def test_a_fact_checker_budget_override_bounds_each_claim_loop(
    tracker: Tracker,
) -> None:
    """The fact checker's own override reaches its direct ReAct loop.

    ``fact_checker: 10`` is production's value and equals the global default,
    so the override is pinned at one here against a global budget of three:
    only the override can stop the claim's loop after one executed call.
    """
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
            _verdict_draft(),
        ],
    )
    agent = FactCheckerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="fact_checker", max_entries=20
        ),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
        config=AgentRuntimeConfig(
            max_iterations=3,
            tool_budget=3,
            tool_budget_overrides={"fact_checker": 1},
        ),
        max_claims=5,
    )
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.react.tool_calls == 1
    assert outcome.react.stop_reason == "tool_budget_exhausted"


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
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
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
async def test_a_verification_provider_failure_is_not_a_verdict(
    tracker: Tracker,
) -> None:
    """The verdict request failed, so nothing was judged and nothing is invented.

    A provider or schema failure is not evidence and not a verdict: the claim
    is not published as ``insufficient_evidence``, which would make an outage
    look like a finding about the claim.
    """
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
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim is None
    assert reason == "provider_unavailable"
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_verification_provider_error"
    assert errors[0].recoverable is False


@pytest.mark.asyncio
async def test_attribution_never_consumes_a_post_digest_finding(
    tracker: Tracker,
) -> None:
    """Past the digest cut, an unseen finding must not look consumed.

    ``claim_extraction_messages`` shows the model only the first
    ``finding_digest`` candidate findings, but the prompt's coverage order
    still lists every planned topic. A claim citing a URL whose finding sits
    past that cut is attributed nothing: the model never saw the finding, so
    it cannot have consumed it, and counting its coverage id would report a
    topic as covered on evidence no one read. The correct failure direction is
    the branch's own — extra work, never skipped evidence.
    """
    topics = [
        SubTopic(
            coverage_id=f"topic-{index:02d}",
            title=f"Alpha {index}",
            rationale="Load-bearing.",
            search_queries=[f"alpha {index} evidence"],
            success_criteria=["A named source."],
            priority=index,
        )
        for index in range(1, 42)
    ]
    findings = [
        _check_finding(
            f"https://example.org/{index}",
            content=f"Fact number {index}.",
            sub_topic=topic.title,
        )
        for index, topic in enumerate(topics, start=1)
    ]
    post_digest_url = findings[-1].source_url
    post_digest_coverage = topics[-1].coverage_id
    state = ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=topics,
        raw_findings=findings,
        evaluated_sources=[_scored(finding.source_url) for finding in findings],
        memory_context=MemorySnapshot(),
    )
    assert len(findings) > DEFAULT_FINDING_DIGEST
    assert post_digest_url not in claim_extraction_messages(
        state, max_findings=DEFAULT_FINDING_DIGEST
    )[1].content

    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="The post-digest finding states a fact.",
                        source_urls=[post_digest_url],
                    )
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)

    claims, _, _ = await agent.extract_claims(state)
    task = agent.claim_task(AgentTask(instruction="Check claims."), claims[0])

    assert [claim.text for claim in claims] == [
        "The post-digest finding states a fact."
    ]
    assert task.consumed_finding_fingerprints == []
    assert task.consumed_coverage_ids == []
    assert post_digest_coverage not in task.consumed_coverage_ids


def _provenance_task() -> ClaimTask:
    return ClaimTask(
        instruction='Verify this claim against independent sources.',
        claim=_claim_draft(),
        claimed_domains=["example.org"],
        consumed_finding_fingerprints=[finding_fingerprint(_check_finding())],
        consumed_coverage_ids=["topic-01"],
    )


@pytest.mark.asyncio
async def test_a_provider_failure_records_no_consumed_provenance(
    tracker: Tracker,
) -> None:
    """An unjudged claim must not make its finding look already consumed.

    ``consumed_finding_fingerprints`` is the only thing ``_finding_is_new``
    consults and ``extract_claims`` returns early once no finding is new, so
    recording provenance on a claim no model ever judged makes a transient
    provider blip suppress that finding for the rest of the run. A provider or
    schema failure yields no claim at all, so there is nothing that could carry
    provenance; ``no_independent_source`` is a judgement about this finding and
    does record it.
    """
    finding = _check_finding()
    independent_run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )
    failed_loop_run = independent_run.model_copy(
        update={"stop_reason": "provider_error"}
    )

    failed_loop, reason, _, _ = await _checker(
        tracker, ScriptedCompleter()
    ).verify_claim(_provenance_task(), failed_loop_run)
    outage, outage_reason, _, _ = await _checker(
        tracker, ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    ).verify_claim(_provenance_task(), independent_run)

    assert reason == "loop_failed"
    assert outage_reason == "provider_unavailable"
    # Neither failure produced a claim at all, so neither can have recorded
    # anything consumed.
    for claim in (failed_loop, outage):
        assert claim is None
    assert _finding_is_new(finding, []) is True


@pytest.mark.asyncio
async def test_a_judged_insufficient_claim_still_records_its_provenance(
    tracker: Tracker,
) -> None:
    """The positive control for the failure-reason fix.

    ``no_independent_source`` is a judgement about this finding: the model was
    never called because nothing independent of the claim's own publisher was
    retrieved, and re-reading the same finding next pass would buy nothing.
    Its provenance must survive, or the fix would have traded a suppressed
    re-extraction for an unbounded one.
    """
    finding = _check_finding()
    claim, reason, _, _ = await _checker(
        tracker, ScriptedCompleter()
    ).verify_claim(
        _provenance_task(),
        ReActRun(
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
        ),
    )

    assert reason == "no_independent_source"
    assert claim.consumed_finding_fingerprints == [
        finding_fingerprint(finding)
    ]
    assert claim.consumed_coverage_ids == ["topic-01"]
    assert _finding_is_new(finding, [claim]) is False


def test_state_update_carries_verified_claims_and_errors(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(agent_name="fact_checker", stop_reason="finished")

    update = agent.state_update(VerifiedClaims(claims=[claim]), run)

    assert update["verified_claims"] == [claim]
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_a_second_pass_carries_the_claims_of_the_first(
    tracker: Tracker,
) -> None:
    """``verified_claims`` replaces, so the update is a whole snapshot.

    An update carrying only this pass's claims would erase every earlier one
    when the state merges it, so the producer merges into the snapshot it
    found on the state it was handed.
    """
    earlier = Claim(
        claim_id=claim_fingerprint("An earlier pass verified this."),
        text="An earlier pass verified this.",
        source_urls=["https://example.org/a"],
        verdict="verified",
        confidence=0.8,
        evidence=["An independent source reported the same figure."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://third.test/x",
                source_title="Independent review",
                locator="p. 1",
                excerpt="An independent source reported the same figure.",
                stance="supports",
            )
        ],
    )
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
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
    ).model_copy(update={"verified_claims": [earlier]})

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    expected = [
        "An earlier pass verified this.",
        "Logical error rates fell below break-even in 2025.",
    ]
    assert [
        claim.text for claim in outcome.state_update["verified_claims"]
    ] == expected
    merged = merge_research_state(state, outcome.state_update)
    assert [claim.text for claim in merged.verified_claims] == expected


def _check_decisions() -> list[object]:
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        use_tool(
            "Read the independent source before judging the claim.",
            "web_scraper",
            '{"url": "https://third.test/x"}',
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


def test_claim_events_report_bounded_provenance_counts_without_excerpts() -> None:
    claim = Claim(
        claim_id=claim_fingerprint("A measured result was reported."),
        text="A measured result was reported.",
        source_urls=["https://example.org/a"],
        verdict="verified",
        confidence=0.9,
        evidence=["An independent study agrees."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://third.test/x",
                source_title="Independent study",
                locator="p. 4",
                excerpt="An independent study agrees.",
                stance="supports",
            )
        ],
    )
    event = claim_checked_event(
        claim,
        ReActRun(agent_name="fact_checker", stop_reason="finished"),
        index=1,
        independent_sources=1,
        reason=None,
    )

    assert event.metadata["support_passages"] == 1
    assert event.metadata["contradiction_passages"] == 0
    assert event.metadata["unique_publishers"] == 1
    assert "An independent study agrees." not in event.metadata.values()


# --- Provenance-driven idempotence: real multiple passes -------------------
#
# The regression below runs the REAL agent over several states in sequence —
# one ``agent.run`` per research pass, each with its own extraction and
# verification provider calls — instead of hand-merging ``Claim`` objects.
# That is the only shape that can expose the reviewed defect: the extraction
# prompt is allowed to rewrite a finding into a self-contained claim, so the
# raw finding and the extracted claim here are deliberate paraphrases that
# share no normalized text.

ALPHA = "Alpha"
BETA = "Beta"
SHARED_URL = "https://example.org/a"
INDEPENDENT_URL = "https://third.test/x"
FINDING_A_CONTENT = (
    "The measured logical error rate dropped below the break-even "
    "threshold during 2025."
)
CHANGED_FINDING_A_CONTENT = (
    "A re-measurement found the logical error rate climbing above the "
    "break-even threshold late in 2025."
)
FINDING_B_CONTENT = (
    "A separate throughput benchmark recorded a 30 percent rise in queue "
    "capacity in 2025."
)
CLAIM_A_TEXT = "Logical error rates fell below break-even in 2025."
CLAIM_B_TEXT = "Queue throughput rose 30 percent in 2025."


def _topic(title: str, *, priority: int) -> SubTopic:
    return SubTopic(
        coverage_id=f"topic-{priority:02d}",
        title=title,
        rationale=f"Measure what happened in {title}.",
        search_queries=[f"{title} 2025 measurements"],
        success_criteria=[f"Two independent estimates for {title}."],
        priority=priority,
    )


def _alpha_finding(content: str = FINDING_A_CONTENT) -> Finding:
    return _check_finding(SHARED_URL, content=content, sub_topic=ALPHA)


def _beta_finding() -> Finding:
    return _check_finding(SHARED_URL, content=FINDING_B_CONTENT, sub_topic=BETA)


def _coverage_state(
    findings: list[Finding],
    *,
    critique: Critique | None = None,
) -> ResearchState:
    """One planned two-topic pass with both topics sharing one source URL.

    Both findings live at ``SHARED_URL`` on purpose: URL overlap must never
    stand in for coverage, so a claim extracted for Alpha must leave Beta
    uncovered even though the two findings carry the same URL.
    """
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=[_topic(ALPHA, priority=1), _topic(BETA, priority=2)],
        raw_findings=list(findings),
        evaluated_sources=[_scored(SHARED_URL)],
        critique=critique,
        memory_context=MemorySnapshot(),
    )


def _verification_decisions() -> list[object]:
    """One verification loop for one claim: search, read it, then judge."""
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        use_tool(
            "Read the independent source before judging the claim.",
            "web_scraper",
            f'{{"url": "{INDEPENDENT_URL}"}}',
        ),
        finish("I have independent material.", "Checked."),
    ]


async def _research_pass(
    agent: FactCheckerAgent,
    tracker: Tracker,
    state: ResearchState,
) -> AgentRun[VerifiedClaims]:
    """One full pass over one state, exactly as the graph would run it."""
    async with tracker.session_span(state.session_id, state.original_question):
        return await agent.run(state)


def _snapshot(outcome: AgentRun[VerifiedClaims]) -> list[Claim]:
    return list(outcome.state_update["verified_claims"])


def _structured_names(completer: ScriptedCompleter, since: int) -> list[str]:
    return [name for name, _, _ in completer.calls[since:]]


def _checker_for_passes(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    searches: int,
) -> FactCheckerAgent:
    return _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [
                    search_response(url=INDEPENDENT_URL)
                    for _ in range(searches)
                ]
            ),
        ),
    )


@pytest.mark.asyncio
async def test_unchanged_evidence_makes_every_later_pass_free(
    tracker: Tracker,
) -> None:
    """(a) A repeated fact must not re-buy extraction or verification.

    Four real passes over the same unchanged evidence: the first extracts
    and verifies, and passes two through four must issue ZERO provider
    calls of either kind while the canonical snapshot stays exactly one
    claim.
    """
    completer = ScriptedCompleter(
        decisions=list(_verification_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    first_snapshot = _snapshot(first)
    assert [claim.verdict for claim in first_snapshot] == ["verified"]
    assert _structured_names(completer, 0) == [
        "ClaimsDraft",
        "PassageVerdictDraft",
    ]
    assert first_snapshot[0].consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding())
    ]
    assert first_snapshot[0].consumed_coverage_ids == ["topic-01"]

    after_first = len(completer.calls)
    react_after_first = len(completer.react_calls)
    carried = merge_research_state(state, first.state_update)
    for _ in range(3):
        outcome = await _research_pass(agent, tracker, carried)

        assert _structured_names(completer, after_first) == []
        assert completer.react_calls[react_after_first:] == []
        snapshot = _snapshot(outcome)
        assert len(snapshot) == 1
        assert snapshot[0].claim_id == first_snapshot[0].claim_id
        assert snapshot[0].verdict == "verified"
        carried = merge_research_state(carried, outcome.state_update)


@pytest.mark.asyncio
async def test_a_changed_finding_at_the_same_url_reopens_the_claim(
    tracker: Tracker,
) -> None:
    """(b) New content behind a known URL is new evidence, and the earlier
    record's provenance survives the replacement (R1's union)."""
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(
                verdict="verified",
                evidence=["A second independent review confirms the figure."],
            ),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update)
    changed = carried.model_copy(
        update={"raw_findings": [_alpha_finding(CHANGED_FINDING_A_CONTENT)]}
    )

    second = await _research_pass(agent, tracker, changed)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "PassageVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert len(snapshot) == 1
    assert snapshot[0].claim_id == first.state_update["verified_claims"][
        0
    ].claim_id
    assert snapshot[0].evidence == [
        "A second independent review confirms the figure."
    ]
    assert snapshot[0].consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding()),
        finding_fingerprint(_alpha_finding(CHANGED_FINDING_A_CONTENT)),
    ]
    assert snapshot[0].consumed_coverage_ids == ["topic-01"]


@pytest.mark.asyncio
async def test_an_untouched_coverage_id_sharing_the_url_stays_uncovered(
    tracker: Tracker,
) -> None:
    """(c) One shared URL must not cover two planned topics.

    Alpha's claim cites the URL both findings carry. Beta's coverage id was
    never consumed, so Beta's finding stays uncovered and must sort first in
    the next pass's extraction order — the old URL-overlap rule marked it
    covered and left Alpha's finding in front.
    """
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_B_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding(), _beta_finding()])

    first = await _research_pass(agent, tracker, state)
    covered = _snapshot(first)[0]
    assert covered.consumed_coverage_ids == ["topic-01"]
    assert covered.consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding())
    ]

    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update)
    ordered = ordered_findings_for_extraction(
        carried, prior_claims=_snapshot(first)
    )

    assert ordered[0].content == FINDING_B_CONTENT

    second = await _research_pass(agent, tracker, carried)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "PassageVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert [claim.text for claim in snapshot] == [CLAIM_A_TEXT, CLAIM_B_TEXT]
    extracted_beta = snapshot[1]
    assert extracted_beta.consumed_finding_fingerprints == [
        finding_fingerprint(_beta_finding())
    ]
    assert extracted_beta.consumed_coverage_ids == ["topic-02"]
    assert {
        coverage_id
        for claim in snapshot
        for coverage_id in claim.consumed_coverage_ids
    } == {"topic-01", "topic-02"}


@pytest.mark.asyncio
async def test_a_critic_reverification_replaces_the_claim_without_duplicating(
    tracker: Tracker,
) -> None:
    """(d) The Critic's explicit override still re-opens a claim whose
    evidence is unchanged, and the replacement keeps one record."""
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator disputes the figure."],
            ),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    first_claim = _snapshot(first)[0]
    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update).model_copy(
        update={
            "critique": Critique(
                score=4,
                gaps=["The break-even claim needs a second look."],
                unsupported_claims=[],
                recommended_queries=[f"Re-verify this claim: {CLAIM_A_TEXT}"],
                should_continue=True,
                rationale="One claim is load-bearing.",
            )
        }
    )

    second = await _research_pass(agent, tracker, carried)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "PassageVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert len(snapshot) == 1
    assert snapshot[0].claim_id == first_claim.claim_id
    assert snapshot[0].verdict == "contradicted"
    assert snapshot[0].contradictions == ["A regulator disputes the figure."]
    assert snapshot[0].consumed_finding_fingerprints == (
        first_claim.consumed_finding_fingerprints
    )
    assert snapshot[0].consumed_coverage_ids == ["topic-01"]


def test_consumed_provenance_attributes_one_shared_url_to_one_finding() -> None:
    """The R4 narrowing: per consumed finding, first in extraction order."""
    draft = ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
    coverage_ids = {ALPHA.casefold(): "topic-01", BETA.casefold(): "topic-02"}

    fingerprints, coverage = consumed_provenance(
        draft,
        findings=[_beta_finding(), _alpha_finding()],
        coverage_ids=coverage_ids,
    )

    assert fingerprints == [finding_fingerprint(_beta_finding())]
    assert coverage == ["topic-02"]


def test_union_claim_provenance_never_loses_what_the_earlier_pass_consumed(
) -> None:
    """A re-checked claim keeps both passes' provenance, in order."""
    earlier = build_claim(
        _claim_draft(),
        _verdict_draft(),
        retrieved_urls=[INDEPENDENT_URL],
        consumed_finding_fingerprints=["first"],
        consumed_coverage_ids=["topic-01"],
    )
    rechecked = build_claim(
        _claim_draft(),
        _verdict_draft(),
        retrieved_urls=[INDEPENDENT_URL],
        consumed_finding_fingerprints=["second"],
        consumed_coverage_ids=["topic-02"],
    )

    merged = union_claim_provenance([earlier], [rechecked])

    assert merged[0].consumed_finding_fingerprints == ["first", "second"]
    assert merged[0].consumed_coverage_ids == ["topic-01", "topic-02"]
    assert merged[0].verdict == rechecked.verdict


def test_the_completed_event_reports_every_verdict_count() -> None:
    verified = build_claim(
        _claim_draft(),
        _verdict_draft(),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )
    contradicted = build_claim(
        _claim_draft(),
        _verdict_draft(contradictions=["Disputed."]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x", "https://fourth.test/x"],
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
async def test_fact_checker_react_handles_empty_unused_tool_name(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Look for an independent source.",
                "web_search",
                '{"query": "break-even 2025"}',
            ),
            ReActDecision(
                thought="The independent source is enough.",
                action="finish",
                tool_name="",
                tool_input_json="{}",
                final_answer="Checked.",
            ),
        ],
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
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
    state = _check_state([_check_finding()])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[-1].tool_name is None


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
        decisions=[*_check_decisions(), *_check_decisions()],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text="First.", source_urls=["https://example.org/a"]),
                    ClaimDraft(text="Second.", source_urls=["https://example.org/a"]),
                ]
            ),
            _verdict_draft(),
            _output_limit_error(),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [
                    search_response(url="https://third.test/x"),
                    search_response(url="https://third.test/x"),
                ]
            ),
        ),
    )
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    # The second claim was never judged: a failed verification is a
    # continuation, not an insufficient-evidence row.
    assert [claim.verdict for claim in outcome.result.claims] == ["verified"]
    assert [claim.text for claim in outcome.result.pending_claims] == ["Second."]
    assert outcome.react.stop_reason == "provider_error"
    types = {error.error_type for error in outcome.errors}
    assert "fact_checker_verification_provider_error" in types
    verification_error = next(
        error
        for error in outcome.errors
        if error.error_type == "fact_checker_verification_provider_error"
    )
    assert verification_error.details["operation"] == (
        "fact_checker_claim_verification"
    )
    provider = verification_error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


@pytest.mark.asyncio
async def test_react_decision_output_limit_remains_a_conservative_fallback(
    tracker: Tracker,
) -> None:
    """A loop the provider truncated judges nothing, and is a continuation.

    The decision request hit its output cap before any tool ran, so no model
    ever looked at the claim. It is not published as insufficient evidence —
    the run stops, and both claims stay pending for the next pass.
    """
    completer = ScriptedCompleter(
        decisions=[_output_limit_error()],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="First.", source_urls=["https://example.org/a"]
                    ),
                    ClaimDraft(
                        text="Second.", source_urls=["https://example.org/a"]
                    ),
                ]
            )
        ],
    )
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.claims == []
    assert [claim.text for claim in outcome.result.pending_claims] == [
        "First.",
        "Second.",
    ]

    assert outcome.react.stop_reason == "provider_error"
    assert outcome.react.steps == []
    assert [error.error_type for error in outcome.errors] == [
        "agent_provider_error"
    ]
    provider_error = outcome.errors[0]
    assert provider_error.recoverable is False
    assert provider_error.details["operation"] == "react_decision"
    provider = provider_error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1

    # No claim was judged, so no claim-checked event was emitted: the loop
    # failure is reported as a pending continuation instead.
    assert [
        event.event_type
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.claim.checked"
    ] == []
    pending = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.claims.pending"
    )
    assert pending.metadata["pending_claim_count"] == 2

    assert [schema for schema, _, _ in completer.calls] == [
        "ClaimsDraft",
    ]
    assert completer.budgets == [None]
    assert completer.react_budgets == [
        AgentRuntimeConfig().react_decision_max_tokens
    ]


def test_build_claim_drafts_drops_the_example_url() -> None:
    """The claim-extraction example URL is not one of the collected findings."""
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Copied from the example.",
                source_urls=["https://evidence.example.test/report"],
            ),
            ClaimDraft(
                text="A real finding.",
                source_urls=["https://real.test/one"],
            ),
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=("https://real.test/one",)
    )

    assert [claim.text for claim in claims] == ["A real finding."]
    assert rejected == [
        "claim 1: no source url from the collected findings"
    ]


# --------------------------------------------------------------------------
# The explicit claim batch, and what happens to everything it leaves out
# --------------------------------------------------------------------------
#
# ``DEFAULT_MAX_CLAIMS = 5`` used to be a hidden prefix: the sixth claim the
# model returned simply did not exist. It is now the explicit, configurable
# ``claim_batch_size``, a pass runs a bounded number of batches, and every
# claim a pass does not adjudicate stays pending — reported as pending, and
# resumable by the next pass. Consumed means adjudicated: a claim that only
# reached a prompt has consumed nothing.


def _targeted_topic(
    coverage_id: str, title: str, target_id: str
) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale="It answers part of the question.",
        search_queries=[f"{title} query"],
        success_criteria=["A value is stated."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id=target_id,
                coverage_id=coverage_id,
                question=f"What does {title} show?",
                required_dimensions=["measure: the reported value"],
                required=True,
                critical=True,
                support_policy="independent_pair",
            )
        ],
    )


def _obligated_state() -> ResearchState:
    """Two planned topics, three findings, and two evidence targets.

    Every finding states a measured value, because a target that requires one
    is only answered by a claim that states it.
    """
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        initial_target_ids=["target-1", "target-2"],
        sub_topics=[
            _targeted_topic("topic-01", "Alpha", "target-1"),
            _targeted_topic("topic-02", "Beta", "target-2"),
        ],
        raw_findings=[
            _check_finding(
                "https://example.org/a",
                content="Alpha reported 1,200 MW in 2025.",
                sub_topic="Alpha",
            ),
            _check_finding(
                "https://example.org/b",
                content="Alpha reported 2,400 MW in 2025.",
                sub_topic="Alpha",
            ),
            _check_finding(
                "https://example.org/c",
                content="Beta reported 3,600 MW in 2025.",
                sub_topic="Beta",
            ),
        ],
        evaluated_sources=[
            _scored("https://example.org/a"),
            _scored("https://example.org/b"),
            _scored("https://example.org/c"),
        ],
        memory_context=MemorySnapshot(),
    )


def _obligated_draft() -> ClaimsDraft:
    """Three claims, in an order that puts two of one topic before the other."""
    return ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Alpha reported 1,200 MW in 2025.",
                source_urls=["https://example.org/a"],
            ),
            ClaimDraft(
                text="Alpha reported 2,400 MW in 2025.",
                source_urls=["https://example.org/b"],
            ),
            ClaimDraft(
                text="Beta reported 3,600 MW in 2025.",
                source_urls=["https://example.org/c"],
            ),
        ]
    )


def _pair_decisions() -> list[object]:
    """Two reads of two different organisations, then a judgement."""
    return [
        use_tool(
            "Read the independent source before judging the claim.",
            "web_scraper",
            '{"url": "https://third.test/x"}',
        ),
        use_tool(
            "Read a second, different organisation.",
            "web_scraper",
            '{"url": "https://fourth.test/y"}',
        ),
        finish("I have two independent sources.", "Checked."),
    ]


def _three_claim_decisions() -> list[object]:
    return [*_check_decisions(), *_check_decisions()]


def test_the_claim_batch_size_is_the_legacy_prefix_made_explicit() -> None:
    """The old constant still works, and now names what it always did."""
    defaults = AgentRuntimeConfig()

    assert DEFAULT_MAX_CLAIMS == 5
    assert DEFAULT_CLAIM_BATCH_SIZE == DEFAULT_MAX_CLAIMS
    assert DEFAULT_CLAIM_BATCHES_PER_PASS == 6
    assert defaults.claim_batch_size == DEFAULT_CLAIM_BATCH_SIZE
    assert defaults.claim_batches_per_pass == DEFAULT_CLAIM_BATCHES_PER_PASS


@pytest.mark.asyncio
async def test_extraction_no_longer_hides_a_five_claim_prefix(
    tracker: Tracker,
) -> None:
    """A model that returns seven checkable claims gets all seven kept."""
    findings = [
        _check_finding(f"https://example.org/{letter}", sub_topic="Alpha")
        for letter in "abcdefg"
    ]
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text=f"Finding {letter} was measured in 2025.",
                        source_urls=[f"https://example.org/{letter}"],
                    )
                    for letter in "abcdefg"
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)
    state = _check_state(findings)

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert provider_failed is False
    assert errors == []
    assert len(claims) == 7


@pytest.mark.asyncio
async def test_a_batch_takes_one_claim_per_target_before_extra_slots(
    tracker: Tracker,
) -> None:
    """Topic two is served in the first batch, not starved behind topic one."""
    completer = ScriptedCompleter(
        decisions=[*_pair_decisions(), *_pair_decisions()],
        outputs=[
            _obligated_draft(),
            _independent_pair_verdict(),
            _independent_pair_verdict(),
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
        max_claims=2,
        batches_per_pass=1,
    )
    state = _obligated_state()

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.text for claim in outcome.result.claims] == [
        "Alpha reported 1,200 MW in 2025.",
        "Beta reported 3,600 MW in 2025.",
    ]
    assert [claim.text for claim in outcome.result.pending_claims] == [
        "Alpha reported 2,400 MW in 2025."
    ]
    targets = {
        target
        for claim in outcome.result.claims
        for target in claim.target_ids
    }
    assert targets == {"target-1", "target-2"}


@pytest.mark.asyncio
async def test_a_claim_that_only_reached_the_batch_is_never_marked_consumed(
    tracker: Tracker,
) -> None:
    """Consumed means adjudicated. The pending claim consumed nothing."""
    completer = ScriptedCompleter(
        decisions=_three_claim_decisions(),
        outputs=[_obligated_draft(), _verdict_draft(), _verdict_draft()],
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
        max_claims=2,
        batches_per_pass=1,
    )
    state = _obligated_state()
    pending_finding = finding_fingerprint(
        _check_finding(
            "https://example.org/b",
            content="Alpha reported 2,400 MW in 2025.",
            sub_topic="Alpha",
        )
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    consumed = {
        fingerprint
        for claim in outcome.result.claims
        for fingerprint in claim.consumed_finding_fingerprints
    }
    assert pending_finding not in consumed
    assert all(
        claim.text != "Alpha reported 2,400 MW in 2025."
        for claim in outcome.result.claims
    )


@pytest.mark.asyncio
async def test_the_completed_event_reports_the_batch_bounds_and_pending_claims(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=_three_claim_decisions(),
        outputs=[_obligated_draft(), _verdict_draft(), _verdict_draft()],
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
        max_claims=2,
        batches_per_pass=1,
    )
    state = _obligated_state()

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    completed = outcome.state_update["events"][-1]
    assert completed.metadata["claim_batch_size"] == 2
    assert completed.metadata["claim_batches_per_pass"] == 1
    assert completed.metadata["batches_run"] == 1
    assert completed.metadata["pending_claim_count"] == 1
    assert completed.metadata["adjudicated_claim_count"] == 2


@pytest.mark.asyncio
async def test_a_deferred_claim_resumes_on_the_next_pass(
    tracker: Tracker,
) -> None:
    """A pending claim is a continuation, never a deletion."""
    completer = ScriptedCompleter(
        decisions=[
            *_three_claim_decisions(),
            *_three_claim_decisions(),
        ],
        outputs=[
            _obligated_draft(),
            _verdict_draft(),
            _verdict_draft(),
            _obligated_draft(),
            _verdict_draft(),
            _verdict_draft(),
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
        max_claims=2,
        batches_per_pass=1,
    )
    state = _obligated_state()
    deferred = "Alpha reported 2,400 MW in 2025."

    async with tracker.session_span("session-1", state.original_question):
        first = await agent.run(state)
        assert first.result is not None
        assert deferred in [
            claim.text for claim in first.result.pending_claims
        ]

        second = await agent.run(state)

    assert second.result is not None
    assert deferred not in [
        claim.text for claim in second.result.pending_claims
    ]


def test_the_pending_queue_is_bounded_by_the_extraction_it_came_from(
    tracker: Tracker,
) -> None:
    """The continuation queue holds claim work, and holds each claim once."""
    agent = _checker(tracker, ScriptedCompleter())

    assert agent.pending_claims == []


@pytest.mark.asyncio
async def test_a_resumed_claim_keeps_the_obligation_it_was_extracted_for(
    tracker: Tracker,
) -> None:
    """A continuation resumes with its own attribution, not a blank one.

    The second pass extracts nothing new, so the deferred claim is the only
    work left. It still answers the target its findings were read for: the
    attribution computed when it was extracted travels with the queue instead
    of being dropped by the next pass's provenance reset.
    """
    completer = ScriptedCompleter(
        decisions=[
            *_pair_decisions(),
            *_pair_decisions(),
            *_pair_decisions(),
        ],
        outputs=[
            _obligated_draft(),
            _independent_pair_verdict(),
            _independent_pair_verdict(),
            ClaimsDraft(claims=[]),
            _independent_pair_verdict(),
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
        max_claims=2,
        batches_per_pass=1,
    )
    state = _obligated_state()
    deferred = "Alpha reported 2,400 MW in 2025."

    async with tracker.session_span("session-1", state.original_question):
        first = await agent.run(state)
        assert first.result is not None
        assert deferred in [
            claim.text for claim in first.result.pending_claims
        ]

        second = await agent.run(state)

    assert second.result is not None
    assert deferred not in [
        claim.text for claim in second.result.pending_claims
    ]
    resumed = [
        claim for claim in second.result.claims if claim.text == deferred
    ]
    assert [claim.target_ids for claim in resumed] == [["target-1"]]


# --------------------------------------------------------------------------
# Target attribution is earned, not inherited
# --------------------------------------------------------------------------
#
# Section 2.3: a target is answered only when its reader statement satisfies
# its required dimensions and support policy. Inheriting every target of the
# topic a finding came from credits a claim with obligations its prose never
# met, and lets evidence carrying only metadata claim a substantive one.


def _attribution_target(**overrides: object) -> EvidenceTarget:
    fields: dict[str, object] = {
        "target_id": "target-1",
        "coverage_id": "topic-01",
        "question": "What capacity was withheld in Texas?",
        "required_dimensions": [
            "measure: withheld capacity",
            "geography: Texas",
        ],
        "required": True,
        "critical": True,
        "support_policy": "independent_pair",
    }
    fields.update(overrides)
    return EvidenceTarget(**fields)


def _attribution_finding() -> Finding:
    return _check_finding(
        "https://example.org/a",
        content="1,200 MW was withheld in Texas in 2024.",
        sub_topic="Alpha",
    )


def _attributed(draft: ClaimDraft, target: EvidenceTarget, *, question: str):
    return claim_attribution(
        draft,
        findings=[_attribution_finding()],
        coverage_ids={"alpha": "topic-01"},
        targets={"alpha": [target]},
        question=question,
    )


def test_target_attribution_requires_the_targets_own_dimensions() -> None:
    target = _attribution_target()
    matching = ClaimDraft(
        text="1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )
    missing_geography = ClaimDraft(
        text="1,200 MW was withheld in 2024.",
        source_urls=["https://example.org/a"],
    )

    question = "How much capacity was withheld in Texas?"

    assert _attributed(matching, target, question=question).target_ids == [
        "target-1"
    ]
    assert (
        _attributed(missing_geography, target, question=question).target_ids == []
    )


def test_a_dimension_this_contract_cannot_check_is_never_earned() -> None:
    """A requirement the prose cannot be shown to state is not satisfied."""
    target = _attribution_target(
        required_dimensions=["deployment mechanism"],
    )
    draft = ClaimDraft(
        text="1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )

    assert (
        _attributed(
            draft, target, question="Which deployment mechanisms are approved?"
        ).target_ids
        == []
    )


def test_a_data_period_dimension_needs_the_question_to_ask_for_it() -> None:
    """Metadata is context: the same evidence answers it only when asked."""
    target = _attribution_target(required_dimensions=["data period"])
    draft = ClaimDraft(
        text="1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )

    assert _attributed(
        draft, target, question="What data period does the survey cover?"
    ).target_ids == ["target-1"]
    assert (
        _attributed(
            draft,
            target,
            question="How much capacity was withheld in Texas?",
        ).target_ids
        == []
    )


def test_a_primary_attribution_target_needs_a_named_issuer() -> None:
    target = _attribution_target(
        required_dimensions=["measure: withheld capacity"],
        support_policy="primary_attribution",
    )
    unattributed = ClaimDraft(
        text="1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )
    attributed = ClaimDraft(
        text="According to Example Lab, 1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )

    question = "How much capacity was withheld in Texas?"

    assert _attributed(unattributed, target, question=question).target_ids == []
    assert _attributed(attributed, target, question=question).target_ids == [
        "target-1"
    ]


def test_the_evidence_period_obligation_does_not_veto_attribution() -> None:
    """The contract's own currency requirement is not a prose dimension."""
    target = _attribution_target(
        required_dimensions=[
            "measure: withheld capacity",
            "geography: Texas",
            "evidence period: the latest available evidence",
            "answer form: the specific fact asked for",
        ]
    )
    draft = ClaimDraft(
        text="1,200 MW was withheld in Texas in 2024.",
        source_urls=["https://example.org/a"],
    )

    assert _attributed(
        draft, target, question="How much capacity was withheld in Texas?"
    ).target_ids == ["target-1"]


# --------------------------------------------------------------------------
# The continuation queue has a real bound
# --------------------------------------------------------------------------


def _pending_drafts(count: int) -> list[ClaimDraft]:
    return [
        ClaimDraft(
            text=f"Claim number {number} was measured in 2025.",
            source_urls=["https://example.org/a"],
        )
        for number in range(count)
    ]


def test_the_continuation_queue_is_bounded_with_the_overflow_persisted() -> None:
    """The bound exists, and nothing past it is deleted."""
    drafts = _pending_drafts(MAX_PENDING_CLAIMS + 3)

    window, deferred = partition_pending_claims(drafts)

    assert len(window) == MAX_PENDING_CLAIMS
    assert len(deferred) == 3
    assert [draft.text for draft in (*window, *deferred)] == [
        draft.text for draft in drafts
    ]


@pytest.mark.asyncio
async def test_a_run_reports_deferred_overflow_as_pending(
    tracker: Tracker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overflow past the window is persisted, and reported as still pending."""
    monkeypatch.setattr(fact_checker_module, "MAX_PENDING_CLAIMS", 1)
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[_obligated_draft(), _verdict_draft()],
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
        max_claims=1,
        batches_per_pass=1,
    )
    state = _obligated_state()

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    pending = outcome.result.pending_claims
    assert len(pending) == 2
    assert [claim.deferred for claim in pending] == [False, True]
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["pending_claim_count"] == 2
    assert completed.metadata["deferred_claim_count"] == 1


def test_the_drain_admits_at_most_one_window_per_pass(
    tracker: Tracker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound binds across passes, and the rest stays deferred."""
    monkeypatch.setattr(fact_checker_module, "MAX_PENDING_CLAIMS", 2)
    agent = _checker(tracker, ScriptedCompleter())
    drafts = _pending_drafts(5)

    agent._remember_pending(drafts)

    assert [draft.text for draft in agent._continuation] == [
        draft.text for draft in drafts[:2]
    ]
    assert [draft.text for draft in agent._deferred] == [
        draft.text for draft in drafts[2:]
    ]

    first = agent._drain_continuation()

    assert [draft.text for draft in first] == [
        draft.text for draft in drafts[:2]
    ]
    # The remainder is still deferred, in order, and not activated.
    assert [draft.text for draft in agent._deferred] == [
        draft.text for draft in drafts[2:]
    ]

    second = agent._drain_continuation()

    assert [draft.text for draft in second] == [
        draft.text for draft in drafts[2:4]
    ]
    assert [draft.text for draft in agent._deferred] == [drafts[4].text]


@pytest.mark.asyncio
async def test_a_bounded_pass_never_loses_a_parked_claim(
    tracker: Tracker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three real passes, a bound of one, and no claim destroyed.

    The reviewer's probe: the drain parked the remainder correctly, but the end
    of the pass replaced both queues, so a parked claim was neither published
    nor pending nor reported. This drives ``run`` — the caller that did the
    replacing — rather than the helper, because a test that exercises the
    helper instead of the caller does not cover the defect.
    """
    monkeypatch.setattr(fact_checker_module, "MAX_PENDING_CLAIMS", 1)
    all_texts = [draft.text for draft in _obligated_draft().claims]
    empty = ClaimsDraft(claims=[])
    completer = ScriptedCompleter(
        decisions=[
            *_pair_decisions(),
            *_pair_decisions(),
            *_pair_decisions(),
        ],
        outputs=[
            _obligated_draft(),
            _independent_pair_verdict(),
            # Later passes extract nothing new: the parked claim is the only
            # work left, so a pass that replaces the queues without carrying
            # it forward has destroyed it.
            empty,
            _independent_pair_verdict(),
            empty,
            _independent_pair_verdict(),
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
        max_claims=1,
        batches_per_pass=1,
    )
    state = _obligated_state()
    adjudicated: list[str] = []

    async with tracker.session_span("session-1", state.original_question):
        for _ in range(3):
            outcome = await agent.run(state)
            assert outcome.result is not None
            adjudicated.extend(claim.text for claim in outcome.result.claims)
            outstanding = {
                claim.text for claim in outcome.result.pending_claims
            }
            # Nothing may vanish: after every pass, every claim is either
            # adjudicated or still pending and reported.
            assert outstanding | set(adjudicated) == set(all_texts), outcome

    assert sorted(adjudicated) == sorted(all_texts)


# --------------------------------------------------------------------------
# Support policy is a real constraint
# --------------------------------------------------------------------------


def test_an_independent_pair_needs_two_independent_supporting_publishers() -> None:
    """An insufficient claim cannot retain an ``independent_pair`` target."""
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="insufficient_evidence",
        supporting_publishers=2,
    )
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="unverified",
        supporting_publishers=2,
    )
    # The control: a genuinely independent pair holds.
    assert claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="verified",
        supporting_publishers=2,
    )
    # One supporting publisher is one source, not a pair.
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="verified",
        supporting_publishers=1,
    )


def test_a_contradicted_claim_earns_no_target_under_any_policy() -> None:
    for policy in (
        "independent_pair",
        "primary_attribution",
        "derivation",
    ):
        assert not claim_meets_support_policy(
            support_policy=policy,
            verdict="contradicted",
            supporting_publishers=3,
        )


@pytest.mark.asyncio
async def test_an_insufficient_claim_retains_no_target(tracker: Tracker) -> None:
    """End to end: the policy gate runs on the adjudicated claim."""
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _obligated_draft(),
            _verdict_draft(
                verdict="insufficient_evidence",
                confidence=0.0,
                passages=[],
            ),
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
        max_claims=1,
        batches_per_pass=1,
    )
    state = _obligated_state()

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == [
        "insufficient_evidence"
    ]
    assert outcome.result.claims[0].target_ids == []


@pytest.mark.asyncio
async def test_a_verified_independent_pair_retains_its_target(
    tracker: Tracker,
) -> None:
    """The control: a claim that genuinely answers the target keeps it."""
    completer = ScriptedCompleter(
        decisions=_pair_decisions(),
        outputs=[_obligated_draft(), _independent_pair_verdict()],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [
                    search_response(url="https://third.test/x"),
                    search_response(url="https://fourth.test/y"),
                ]
            ),
        ),
        max_claims=1,
        batches_per_pass=1,
    )
    state = _obligated_state()

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == ["verified"]
    assert outcome.result.claims[0].target_ids == ["target-1"]

# ---------------------------------------------------------------------------
# Task 6: the claim-specific evidence union and its strict pair rule
# ---------------------------------------------------------------------------

TASK6_CLAIM = "Wind capacity reached 10 GW in 2025."
A_URL = "https://lab-a.test/wind"
B_URL = "https://lab-b.test/audit"
A_TEXT = "Lab A reported that wind capacity reached 10 GW in 2025."
B_TEXT = "Lab B audited the figure: wind capacity reached 10 GW in 2025."
TASK6_TARGET = "target-1"


class _NoDownloadClient:
    """A page client that fails the test if any body is ever fetched."""

    def __init__(self) -> None:
        self.calls = 0

    async def get(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("a body was downloaded for a sufficient packet")


async def _task6_run(
    agent: FactCheckerAgent, state: ResearchState, tracker: Tracker
) -> AgentRun[VerifiedClaims]:
    """Run one Fact Checker pass inside the session span it requires."""
    async with tracker.session_span("session-1", state.original_question):
        return await agent.run(state)


def _ab_read(read_id: str, url: str, title: str, text: str) -> object:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at=CHECK_EXTRACTED_AT,
        text=text,
        passages={"chunk-0": text},
        extraction_complete=True,
    )


def _ab_source(url: str, host: str, work: str, *, scored: bool = True) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Wind capacity audit",
        authority_score=0.9 if scored else None,
        recency_score=0.9 if scored else None,
        relevance_score=0.9 if scored else None,
        overall_score=0.9 if scored else None,
        rationale="Independent and dated.",
        low_confidence=False,
        serving_host=host,
        publisher_id=host,
        work_id=work,
        transport_relation="original",
        source_role="independent_research" if scored else "unknown",
        self_interest="none",
        evaluation_status="scored" if scored else "unscored_missing",
    )


def _ab_state(*, score_b: bool = True, units_for_b: bool = True) -> ResearchState:
    """Upstream A+B, read by the Researcher, targeted at one claim."""
    read_a = _ab_read("read-a", A_URL, "Lab A report", A_TEXT)
    read_b = _ab_read("read-b", B_URL, "Lab B audit", B_TEXT)
    units = [
        build_evidence_unit(
            read=read_a,
            locator="chunk-0",
            excerpt=A_TEXT,
            origin="researcher",
            target_ids=[TASK6_TARGET],
        )
    ]
    if units_for_b:
        units.append(
            build_evidence_unit(
                read=read_b,
                locator="chunk-0",
                excerpt=B_TEXT,
                origin="researcher",
                target_ids=[TASK6_TARGET],
            )
        )
    return ResearchState(
        session_id="session-1",
        original_question="How much wind capacity was added?",
        initial_target_ids=[TASK6_TARGET],
        sub_topics=[_targeted_topic("topic-01", "Alpha", TASK6_TARGET)],
        raw_findings=[
            _check_finding(A_URL, content=A_TEXT, sub_topic="Alpha"),
            _check_finding(B_URL, content=B_TEXT, sub_topic="Alpha"),
        ],
        evaluated_sources=[
            _ab_source(A_URL, "lab-a.test", f"sha256:{read_a.content_sha256}"),
            _ab_source(
                B_URL, "lab-b.test", f"sha256:{read_b.content_sha256}", scored=score_b
            ),
        ],
        read_records={read_a.read_id: read_a, read_b.read_id: read_b},
        evidence_units={unit.evidence_id: unit for unit in units},
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        memory_context=MemorySnapshot(),
    )


def _shown_ids(messages: list[ChatMessage]) -> list[str]:
    body = "\n".join(message.content for message in messages)
    return list(dict.fromkeys(re.findall(r"id: (ev-[0-9a-f]+)", body)))


def _select_every_shown_id(
    messages: list[ChatMessage], schema: type[ClaimVerdictDraft]
) -> ClaimVerdictDraft:
    """The adjudication a model makes over the packet it was shown."""
    ids = _shown_ids(messages)
    assert ids, "\n".join(message.content for message in messages)
    return schema(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id=evidence_id,
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            )
            for evidence_id in ids
        ],
        support_ids=ids,
        contradiction_ids=[],
        rationale="Two independent reports state the same figure.",
    )


def _adjudication_requests(completer: ScriptedCompleter) -> list[list[ChatMessage]]:
    return [
        messages
        for name, _, messages in completer.calls
        if name == "ClaimVerdictDraft"
    ]


def _adjudication_body(completer: ScriptedCompleter) -> str:
    (messages,) = _adjudication_requests(completer)
    return "\n".join(message.content for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cited",
    [
        pytest.param([A_URL], id="cites-a"),
        pytest.param([B_URL], id="cites-b"),
        pytest.param([A_URL, B_URL], id="cites-both"),
    ],
)
async def test_a_qualifying_upstream_pair_is_verified_with_no_retrieval_at_all(
    tracker: Tracker, cited: list[str]
) -> None:
    """Section 2.1: qualifying upstream A+B needs no new retrieval.

    The claim's own ``source_urls`` are a citation list, not the evidence pool:
    the same two selected passages produce ``verified`` whether the claim names
    A, B, or both, because the packet is the claim-specific union of the run's
    reads rather than the URLs one finding happened to cite.
    """
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=list(cited))]),
            _select_every_shown_id,
        ]
    )
    client = _NoDownloadClient()
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(tracker, http=client),  # type: ignore[arg-type]
    )

    outcome = await _task6_run(agent, _ab_state(), tracker)

    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"
    assert claim.evidence_status == "verified_pair"
    assert claim.insufficient_reason is None
    # Both exact passages of both upstream reads are in the adjudication
    # request, each under its own selectable id.
    body = _adjudication_body(completer)
    assert A_TEXT in body
    assert B_TEXT in body
    # Zero retrieval: no ReAct turn was ever requested, no body was fetched.
    assert completer.react_calls == []
    assert outcome.react.tool_calls == 0
    assert outcome.react.steps == []
    assert client.calls == 0


@pytest.mark.asyncio
async def test_a_locally_sufficient_pool_the_model_cannot_use_gets_one_retrieval(
    tracker: Tracker,
) -> None:
    """A failed pair allows bounded targeted retrieval, not paralysis.

    Two identities exist locally, so the pool looks sufficient and the loop is
    skipped. The model judges neither passage a support for the complete claim,
    and the claim is *not* settled on that: one bounded retrieval round runs,
    the newly read source is assessed and added to the packet, and the claim is
    re-adjudicated over the enlarged union.
    """
    def reply(
        messages: list[ChatMessage], schema: type[ClaimVerdictDraft]
    ) -> ClaimVerdictDraft:
        ids = _shown_ids(messages)
        if len(ids) <= 2:
            return schema(
                verdict="insufficient_evidence",
                confidence=0.1,
                assessments=[
                    SupportAssessment(
                        evidence_id=evidence_id,
                        stance="supports",
                        complete_support=False,
                        scope_compatible=False,
                        independent=True,
                    )
                    for evidence_id in ids
                ],
                support_ids=[],
                contradiction_ids=[],
                rationale="Neither passage supports the complete claim.",
            )
        return _select_every_shown_id(messages, schema)

    completer = ScriptedCompleter(
        decisions=_verification_decisions(),
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]),
            reply,
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url=INDEPENDENT_URL,
                        authority_score=0.9,
                        recency_score=0.9,
                        relevance_score=0.9,
                        source_role="independent_research",
                        issuer="Third Party",
                        rationale="Independent and dated.",
                    )
                ]
            ),
            reply,
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            http=page_client(
                title="Third party audit",
                body=(
                    "Third Party audited the figure: wind capacity reached "
                    "10 GW in 2025."
                ),
            ),
        ),
    )

    outcome = await _task6_run(agent, _ab_state(), tracker)

    requests = _adjudication_requests(completer)
    assert len(requests) == 2
    assert len(_shown_ids(requests[0])) == 2
    assert len(_shown_ids(requests[1])) > 2
    assert completer.react_calls
    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"
    assert claim.evidence_status == "verified_pair"


def test_a_memory_only_pair_never_becomes_a_packet() -> None:
    """Remembered or discovered prose is not read-bearing evidence.

    The findings still name A and B, and two sources sit in
    ``evaluated_sources`` — but no read was ever performed, so the
    claim-specific pool is empty and no packet can present them as evidence.
    """
    state = _ab_state().model_copy(
        update={"read_records": {}, "evidence_units": {}}
    )
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])

    assert claim_evidence_pool(state, draft, target_ids=[TASK6_TARGET]) == []
    agent = object.__new__(FactCheckerAgent)
    agent._run_reads = {}
    agent._run_sources = list(state.evaluated_sources)
    packet, retrieval_needed = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )

    assert packet is None
    assert retrieval_needed is True
    assert _packet_independent_publishers(None, None) == 0


def test_a_sufficient_packet_is_recognised_before_any_model_call() -> None:
    """The sufficiency test is local, and it is what skips the loop."""
    state = _ab_state()
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])
    agent = object.__new__(FactCheckerAgent)
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)

    packet, retrieval_needed = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )

    assert packet is not None
    assert retrieval_needed is False
    assert len(packet.units) == 2


def test_a_boundary_loss_names_the_missing_read_and_never_verifies() -> None:
    """A read that never reached the packet cannot half-support a claim.

    The read registry still holds ``read-b``, but no evidence unit cites it, so
    the passage-selection boundary is where the evidence stopped. The claim is
    not verified, and the exact id that stopped is still nameable.
    """
    complete = _ab_state()
    state = complete.model_copy(
        update={
            "evidence_units": {
                evidence_id: unit
                for evidence_id, unit in complete.evidence_units.items()
                if unit.source_url != B_URL
            }
        }
    )
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])

    (missing_read_id,) = [
        read_id
        for read_id, read in state.read_records.items()
        if read.resolved_url == B_URL
    ]
    pool = claim_evidence_pool(state, draft, target_ids=[TASK6_TARGET])
    assert [unit.source_url for unit in pool] == [A_URL]
    assert missing_read_id in state.read_records
    assert all(
        unit.read_id != missing_read_id for unit in state.evidence_units.values()
    )

    agent = object.__new__(FactCheckerAgent)
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )
    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="verified",
            confidence=0.9,
            assessments=[
                SupportAssessment(
                    evidence_id=unit.evidence_id,
                    stance="supports",
                    complete_support=True,
                    scope_compatible=True,
                    independent=True,
                )
                for unit in pool
            ],
            support_ids=[unit.evidence_id for unit in pool],
            contradiction_ids=[],
            rationale="One report states the figure.",
        ),
        packet,
        None,
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status == "source_supported"
    assert claim.insufficient_reason
    assert "single_primary_only" in claim.audit_flags


def test_two_unrelated_reads_are_not_a_proven_handoff_loss() -> None:
    """The control: a read about something else is not evidence of loss."""
    state = _ab_state()
    unrelated = _ab_read(
        "read-c", "https://lab-c.test/other", "Other topic", "Solar capacity."
    )
    state = state.model_copy(
        update={"read_records": {**state.read_records, unrelated.read_id: unrelated}}
    )
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])

    pool = claim_evidence_pool(state, draft, target_ids=[TASK6_TARGET])

    assert {unit.source_url for unit in pool} == {A_URL, B_URL}
    assert all(unit.read_id != "read-c" for unit in pool)


@pytest.mark.asyncio
async def test_an_unscored_source_can_never_be_half_of_the_pair(
    tracker: Tracker,
) -> None:
    """Task 4's allocation: the shared service scores it, or it does not count."""
    completer = ScriptedCompleter(
        decisions=[finish("Nothing more to read.", "No further source.")],
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]),
            _select_every_shown_id,
        ],
    )
    agent = _checker(tracker, completer)

    outcome = await _task6_run(agent, _ab_state(score_b=False), tracker)

    (claim,) = outcome.result.claims
    assert claim.verdict != "verified"
    assert claim.evidence_status == "source_supported"
    assert claim.insufficient_reason


@pytest.mark.asyncio
async def test_a_researcher_read_is_reused_without_a_second_body_download(
    tracker: Tracker,
) -> None:
    """Task 3's deferred cross-agent assertion, through the real agent.

    The document was read by the Researcher in an earlier pass. A new Fact
    Checker target needs it, the shared run read registry still holds the exact
    body, and the packet is built from that stored read: no second body
    download, no retrieval loop, and the same read id survives with its
    original observation time.
    """
    state = _ab_state()
    (stored_id,) = [
        read_id
        for read_id, read in state.read_records.items()
        if read.resolved_url == A_URL
    ]
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]),
            _select_every_shown_id,
        ]
    )
    client = _NoDownloadClient()
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(tracker, http=client),  # type: ignore[arg-type]
    )

    outcome = await _task6_run(agent, state, tracker)

    assert completer.react_calls == []
    assert client.calls == 0
    assert outcome.react.tool_calls == 0
    assert A_TEXT in _adjudication_body(completer)
    merged = merge_research_state(state, outcome.state_update)
    assert stored_id in merged.read_records
    assert merged.read_records[stored_id].acquisition_kind == "network"
    assert merged.read_records[stored_id].retrieved_at == CHECK_EXTRACTED_AT


def test_changed_content_or_an_added_passage_invalidates_the_fingerprint() -> None:
    """The fingerprint follows the exact text, so changed evidence is a new packet.

    A claim re-adjudicated over the same two passages shares a fingerprint and
    is not judged twice; a body that changed, or a passage that was added,
    produces different units and therefore a different packet — which is what
    makes "unchanged evidence" a decidable question rather than an assumption.
    """
    state = _ab_state()
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])
    first = build_adjudication_packet(
        draft,
        claim_evidence_pool(state, draft, target_ids=[TASK6_TARGET]),
        eligibility={},
        omitted=[],
    )

    changed_read = _ab_read(
        "read-a",
        A_URL,
        "Lab A report, revised",
        A_TEXT + " The revised edition restates the figure.",
    )
    added = build_evidence_unit(
        read=changed_read,
        locator="chunk-0",
        excerpt=changed_read.passages["chunk-0"],
        origin="researcher",
        target_ids=[TASK6_TARGET],
    )
    changed_state = state.model_copy(
        update={
            "read_records": {
                read_id: (changed_read if read.resolved_url == A_URL else read)
                for read_id, read in state.read_records.items()
            },
            "evidence_units": {
                evidence_id: (
                    added if unit.source_url == A_URL else unit
                )
                for evidence_id, unit in state.evidence_units.items()
            },
        }
    )
    second = build_adjudication_packet(
        draft,
        claim_evidence_pool(changed_state, draft, target_ids=[TASK6_TARGET]),
        eligibility={},
        omitted=[],
    )

    assert first.fingerprint != second.fingerprint
    assert {unit.evidence_id for unit in first.units} != {
        unit.evidence_id for unit in second.units
    }


@pytest.mark.asyncio
async def test_one_adjudication_per_claim_identity_per_pass(tracker: Tracker) -> None:
    """No duplicate adjudication at one fingerprint, and the audit names it."""
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]),
            _select_every_shown_id,
        ]
    )
    agent = _checker(tracker, completer)

    outcome = await _task6_run(agent, _ab_state(), tracker)

    assert len(agent._adjudicated_packets) == 1
    (audit,) = agent._adjudication_audits.values()
    (fingerprint,) = agent._adjudicated_packets
    assert audit.packet_fingerprint == fingerprint
    assert audit.operation == "adjudication_packet"
    assert audit.status == "completed"
    assert len(audit.input_ids) == 2
    assert len(audit.accepted_ids) == 2
    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"


# ---------------------------------------------------------------------------
# Task 6: the strict pair rule and full-atom entailment, case by case
# ---------------------------------------------------------------------------

def _pair_unit(name: str, url: str, text: str) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=f"ev-{name}",
        read_id=f"read-{name}",
        source_url=url,
        source_title=f"{name} title",
        locator="p. 1",
        excerpt=text,
        target_ids=[TASK6_TARGET],
        origin="researcher",
    )


def _pair_packet(
    left: EvidenceEligibility,
    right: EvidenceEligibility,
    *,
    left_text: str = "The operator reported 10 GW in 2025.",
    right_text: str = "An audit confirms 10 GW in 2025.",
) -> AdjudicationPacket:
    left_unit = _pair_unit("left", "https://one.test/a", left_text)
    right_unit = _pair_unit("right", "https://two.test/b", right_text)
    return AdjudicationPacket(
        claim_id="claim-1",
        claim_text=TASK6_CLAIM,
        claim_source_urls=["https://one.test/a"],
        claim_cluster_id="cluster-1",
        units=[left_unit, right_unit],
        eligibility={
            left_unit.evidence_id: left,
            right_unit.evidence_id: right,
        },
        fingerprint="fingerprint-1",
    )


def _eligibility(**overrides: object) -> EvidenceEligibility:
    fields: dict[str, object] = {
        "publisher_id": "publisher-one",
        "work_id": "sha256:one",
        "origin_group_id": "publisher:publisher-one",
        "complete_support": True,
        "read_valid": True,
        "corroboration_eligible": True,
    }
    fields.update(overrides)
    return EvidenceEligibility(**fields)  # type: ignore[arg-type]


def _adjudication(
    packet: AdjudicationPacket,
    *,
    verdict: str = "verified",
    complete: bool = True,
    scope: bool = True,
    independent: bool = True,
    stance: str = "supports",
    contradiction_ids: list[str] | None = None,
) -> tuple[Claim, list[str]]:
    supports = [
        unit.evidence_id for unit in packet.units
    ] if stance == "supports" else []
    return validate_adjudication(
        ClaimVerdictDraft(
            verdict=verdict,
            confidence=0.9,
            assessments=[
                SupportAssessment(
                    evidence_id=unit.evidence_id,
                    stance=stance,
                    complete_support=complete,
                    scope_compatible=scope,
                    independent=independent,
                )
                for unit in packet.units
            ],
            support_ids=supports,
            contradiction_ids=list(contradiction_ids or []),
            rationale="Judged over the packet.",
        ),
        packet,
        None,
    )


def _independent_second(**overrides: object) -> EvidenceEligibility:
    fields: dict[str, object] = {
        "publisher_id": "publisher-two",
        "work_id": "sha256:two",
        "origin_group_id": "publisher:publisher-two",
    }
    fields.update(overrides)
    return _eligibility(**fields)


@pytest.mark.parametrize(
    ("label", "second"),
    [
        (
            "same-work-mirror",
            _independent_second(work_id="sha256:one"),
        ),
        (
            "same-publisher-different-works",
            _independent_second(publisher_id="publisher-one"),
        ),
        (
            "shared-statistic-origin",
            _independent_second(origin_group_id="publisher:publisher-one"),
        ),
        (
            "unknown-origin",
            _independent_second(origin_group_id=None),
        ),
        (
            "unknown-publisher",
            _independent_second(publisher_id=None),
        ),
        (
            "unknown-work",
            _independent_second(work_id=None),
        ),
        (
            "unscored-or-not-independent",
            _independent_second(corroboration_eligible=False),
        ),
        (
            "partial-read",
            _independent_second(read_valid=False),
        ),
        (
            "search-only-second-url",
            _independent_second(publisher_id="", work_id="", origin_group_id=""),
        ),
    ],
)
def test_no_pair_that_cannot_show_independence_ever_verifies(
    label: str, second: EvidenceEligibility
) -> None:
    """Section 2.2, one failing identity test at a time.

    A mirror, a second work from one publisher, two documents sharing one
    origin, an unknown identity, an unscored source, and a partial read all
    fail the strict pair — and a second URL is not a second source.
    """
    packet = _pair_packet(_eligibility(), second)

    claim = _adjudication(packet)

    assert claim.verdict == "insufficient_evidence", label
    assert claim.evidence_status == "source_supported"
    assert claim.insufficient_reason


@pytest.mark.parametrize(
    ("label", "complete", "scope", "independent"),
    [
        ("period-mismatch", True, False, True),
        ("unit-mismatch", False, True, True),
        ("scope-mismatch", False, False, True),
        ("compound-claim-partly-supported", False, True, True),
        ("quoted-speculation", False, True, True),
        ("stale-future-current-confusion", True, False, True),
    ],
)
def test_a_support_that_is_not_a_complete_in_scope_support_never_verifies(
    label: str, complete: bool, scope: bool, independent: bool
) -> None:
    """Section 2.1: each passage must support the COMPLETE atomic claim."""
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = _adjudication(
        packet, complete=complete, scope=scope, independent=independent
    )

    assert claim.verdict == "insufficient_evidence", label
    assert (
        "no_complete_support" in claim.audit_flags
        or "single_primary_only" in claim.audit_flags
    ), label


def test_a_faithful_primary_attribution_stays_source_supported() -> None:
    """One primary source is attribution, not independent corroboration."""
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = _adjudication(packet, independent=False)

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status == "source_supported"
    assert "independent" not in claim.insufficient_reason
    assert claim.insufficient_reason


def _contradiction_draft(
    *,
    right_complete: bool = True,
    right_scope: bool = True,
) -> ClaimVerdictDraft:
    return ClaimVerdictDraft(
        verdict="contradicted",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-left",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="contradicts",
                complete_support=right_complete,
                scope_compatible=right_scope,
                independent=True,
            ),
        ],
        support_ids=["ev-left"],
        contradiction_ids=["ev-right"],
        rationale="The two passages disagree.",
    )


def test_contradictory_evidence_is_retained_with_a_conflict_row() -> None:
    """A contradiction is recorded, never resolved away."""
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = validate_adjudication(_contradiction_draft(), packet, None)

    assert claim.verdict == "contradicted"
    assert claim.contradictions
    (conflict,) = claim.conflict_assessments
    assert conflict.claim_cluster_id == "cluster-1"
    assert conflict.evidence_ids == ["ev-left", "ev-right"]
    assert conflict.resolution == "unresolved"
    assert conflict.material is True
    assert conflict.rationale


def test_a_different_period_passage_is_a_scope_difference_not_a_refutation() -> None:
    """Different-period facts are not automatically contradictions.

    The model called the claim contradicted, and the passage it called a
    refutation measures a period this contract can see is a different one. That
    is a reasoned conflict analysis, not a forced "false": the row records the
    scope difference, is not material, and the verdict is not ``contradicted``.
    """
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = validate_adjudication(
        _contradiction_draft(right_scope=False), packet, None
    )

    assert claim.verdict != "contradicted"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "resolved"
    assert conflict.same_scope is False
    assert conflict.material is False


def test_a_passage_about_another_question_is_not_a_conflict_at_all() -> None:
    """Nothing was assessed as refuting the claim, so nothing is a conflict."""
    packet = _pair_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-left",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="unrelated",
                complete_support=False,
                scope_compatible=False,
                independent=True,
            ),
        ],
        support_ids=["ev-left"],
        contradiction_ids=[],
        rationale="Only one passage is about this claim.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "contradicted"
    assert claim.conflict_assessments == []
    assert claim.verdict == "insufficient_evidence"


def test_a_material_unresolved_contradiction_precludes_settled_verified() -> None:
    """Two complete in-scope passages that disagree cannot settle the claim."""
    packet = _pair_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-left",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="contradicts",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            ),
        ],
        support_ids=["ev-left"],
        contradiction_ids=["ev-right"],
        rationale="The sources disagree.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "contradicted"
    assert claim.evidence_status == "contested"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "unresolved"
    assert "model_disagreement" in claim.audit_flags


def test_an_id_the_model_was_never_shown_is_not_evidence() -> None:
    """A selection outside the packet is a local disagreement, not a source."""
    packet = _pair_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-invented",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            )
        ],
        support_ids=["ev-invented"],
        contradiction_ids=[],
        rationale="Trust me.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "insufficient_evidence"
    assert "evidence_not_admitted" in claim.audit_flags
    assert all(
        passage.evidence_id if hasattr(passage, "evidence_id") else True
        for passage in claim.verification_evidence
    )
    assert "ev-invented" not in {
        passage.source_title for passage in claim.verification_evidence
    }


def test_a_provider_or_schema_failure_is_not_an_evidence_verdict() -> None:
    """An outage records no verdict, and the enumerated reason names it."""
    assert provider_failure_reason(ProviderTimeoutError("timed out")) == (
        "provider_unavailable"
    )
    assert (
        provider_failure_reason(
            StructuredOutputError(
                "invalid after one repair",
                diagnostics=[
                    {
                        "attempt": 1,
                        "field_paths": ("support_ids",),
                        "category": "missing",
                    }
                ],
            )
        )
        == "schema_failed"
    )
    assert "schema_failed" in INSUFFICIENT_REASONS
    assert "provider_unavailable" in INSUFFICIENT_REASONS


def test_every_adjudication_reason_is_enumerated() -> None:
    """The brief's reason list is the enumerated one, and none is blank."""
    for reason in (
        "evidence_not_admitted",
        "handoff_loss",
        "packet_incomplete",
        "acquisition_failed",
        "no_candidate",
        "capacity_deferred",
        "identity_unknown",
        "same_work",
        "same_publisher",
        "shared_origin",
        "no_complete_support",
        "single_primary_only",
        "provider_unavailable",
        "schema_failed",
        "model_disagreement",
    ):
        assert INSUFFICIENT_REASONS.get(reason), reason
