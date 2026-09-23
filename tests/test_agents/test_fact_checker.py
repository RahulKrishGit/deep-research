"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

import json
import re
from typing import get_args

import httpx
import pytest
from pydantic import ValidationError

import deep_research.providers.contracts as contracts_module
from deep_research.agents import fact_checker as fact_checker_module
from deep_research.agents.base import AgentRun
from deep_research.agents.claim_clusters import (
    MAX_EQUIVALENCE_ATOMS,
    AtomicPairDraft,
    ClaimCluster,
    ClaimEquivalenceDraft,
    _canonical_claim,
    _resolve_stored_cluster,
    claim_cluster_id,
    claim_meets_support_policy,
    cluster_for_atom,
    extract_atoms,
    extract_text_atoms,
)
from deep_research.agents.evidence import (
    EvidenceEligibility,
    build_evidence_unit,
    build_read_record,
    merge_boundary_audits,
)
from deep_research.agents.fact_checker import (
    DEFAULT_CLAIM_BATCH_SIZE,
    DEFAULT_CLAIM_BATCHES_PER_PASS,
    DEFAULT_FINDING_DIGEST,
    DEFAULT_MAX_CLAIMS,
    FACT_CHECK_EVIDENCE_CHARS,
    INSUFFICIENT_REASONS,
    MAX_PACKET_OMISSIONS,
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
    _packet_has_pair,
    _packet_independent_publishers,
    plan_packet_rendering,
    adjudication_messages,
    adjudication_repaired_event,
    admitted_target_ids,
    build_adjudication_packet,
    build_claim,
    build_claim_drafts,
    claim_attribution,
    claim_checked_event,
    claim_evidence_pool,
    claim_extraction_messages,
    claim_missing_read_ids,
    claim_verification_messages,
    claimed_domains_for,
    consumed_provenance,
    fact_check_completed_event,
    independent_domains,
    insufficient_claim,
    known_source_urls,
    memory_candidate_count,
    memory_recall_count,
    normalize_verdict,
    ordered_findings_for_extraction,
    partition_pending_claims,
    provider_failure_reason,
    resolve_verdict,
    retrieved_source_urls,
    supporting_publisher_count,
    union_claim_provenance,
    unshown_candidates,
    valid_verification_passages,
    validate_adjudication,
    verdict_counts,
    with_render_boundaries,
)
from deep_research.agents.identity import (
    claim_fingerprint,
    finding_fingerprint,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.synthesizer import build_canonical_packet
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
from deep_research.providers.contracts import StructuredRepairRecord
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    EVIDENCE_BADGE_LABELS,
    AcquisitionState,
    ConflictAssessment,
    QUALITY_CONTRACT_VERSION,
    Claim,
    ClaimVerdict,
    Critique,
    CritiqueGap,
    EvidenceDisposition,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    MemorySnapshot,
    RefinementTarget,
    ReportComposition,
    ReportStatement,
    ResearchState,
    ScoredSource,
    SubTopic,
    clusters_for_claims,
    merge_research_state,
    statement_claims,
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
        evidence_status="verified_pair",
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

    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert claim.verdict == "unverified"
    assert claim.evidence_status == "source_supported"
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
    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert claim.verdict == "unverified"
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
        # The passage path cannot verify, so its supported claim is
        # unverified rather than settled.
        "verified": 0,
        "unverified": 1,
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

    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert claim.verdict == "unverified"
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
    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert [claim.verdict for claim in claims] == ["unverified"]
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
        evidence_status="verified_pair",
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
        evidence_status="verified_pair",
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
    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert [claim.verdict for claim in first_snapshot] == ["unverified"]
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
        # The passage path has no pair test, so its ceiling is unverified.
        assert snapshot[0].verdict == "unverified"
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
            # This pass adjudicated a claim, so it makes its one bounded
            # equivalence call over the stored proposition and the new atom.
            ClaimEquivalenceDraft(pairs=[]),
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
        "ClaimEquivalenceDraft",
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
            ClaimEquivalenceDraft(pairs=[]),
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
        "ClaimEquivalenceDraft",
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
            ClaimEquivalenceDraft(pairs=[]),
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
        "ClaimEquivalenceDraft",
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


@pytest.mark.asyncio
async def test_a_typed_adjudication_job_re_opens_a_claim_without_prose(
    tracker: Tracker,
) -> None:
    """The typed route must land on a node that reads it.

    The critique here carries no "reverify"/"recheck" prose at all: the
    instruction is the typed ``adjudicate`` job the refinement hop persisted in
    ``refinement_targets``. Without reading that job, the Fact Checker returns
    early on a pass with no new URLs and the routed repair is a no-op.
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
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator disputes the figure."],
            ),
            ClaimEquivalenceDraft(pairs=[]),
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
                gaps=[
                    CritiqueGap(
                        claim_cluster_ids=[
                            first_claim.cluster_id or first_claim.claim_id
                        ],
                        kind="contradiction",
                        severity="major",
                        repair_action="adjudicate",
                        problem="The break-even figure needs a decision.",
                    )
                ],
                unsupported_claims=[],
                recommended_queries=[],
                should_continue=True,
                rationale="One claim is load-bearing.",
            ),
            "refinement_targets": [
                RefinementTarget(
                    claim_cluster_ids=[
                        first_claim.cluster_id or first_claim.claim_id
                    ],
                    action="adjudicate",
                    origin="critic_gap",
                    severity="major",
                    problem="The break-even figure needs a decision.",
                )
            ],
        }
    )

    second = await _research_pass(agent, tracker, carried)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "PassageVerdictDraft",
        "ClaimEquivalenceDraft",
    ]
    snapshot = _snapshot(second)
    assert snapshot[0].claim_id == first_claim.claim_id
    assert snapshot[0].contradictions == ["A regulator disputes the figure."]


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
    # Neither claim came through the packet path, so none may be settled.
    assert event.metadata["verified"] == 0
    assert event.metadata["contradicted"] == 1
    assert event.metadata["unverified"] == 1
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
    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert [claim.verdict for claim in outcome.result.claims] == [
        "unverified"
    ]

    merged = merge_research_state(state, outcome.state_update)
    assert len(merged.verified_claims) == 1

    events = outcome.state_update["events"]
    types = [event.event_type for event in events]
    assert types[0] == "fact_checker.claims.extracted"
    assert "fact_checker.claim.checked" in types
    assert types[-1] == "fact_checker.fact_check.completed"
    completed = events[-1]
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 0
    assert completed.metadata["unverified"] == 1
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
    # The legacy passage path has no evidence packet and therefore no pair
    # test, so it may not publish the strict badge: its ceiling is
    # ``unverified``, never a settled ``verified``.
    assert [claim.verdict for claim in outcome.result.claims] == [
        "unverified"
    ]
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
            ClaimEquivalenceDraft(pairs=[]),
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
    # The passage path publishes ``unverified`` at best - it has no pair test
    # to run - and an ``unverified`` claim has evidence that does not address
    # it, so it answers no obligation under any policy. The targets stay
    # outstanding instead of being credited to evidence that was never tested
    # as a pair.
    assert targets == set()


@pytest.mark.asyncio
async def test_a_claim_that_only_reached_the_batch_is_never_marked_consumed(
    tracker: Tracker,
) -> None:
    """Consumed means adjudicated. The pending claim consumed nothing."""
    completer = ScriptedCompleter(
        decisions=_three_claim_decisions(),
        outputs=[
            _obligated_draft(),
            _verdict_draft(),
            _verdict_draft(),
            ClaimEquivalenceDraft(pairs=[]),
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
        outputs=[
            _obligated_draft(),
            _verdict_draft(),
            _verdict_draft(),
            ClaimEquivalenceDraft(pairs=[]),
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
            ClaimEquivalenceDraft(pairs=[]),
            _obligated_draft(),
            _verdict_draft(),
            _verdict_draft(),
            ClaimEquivalenceDraft(pairs=[]),
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
            ClaimEquivalenceDraft(pairs=[]),
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
    # The attribution survived (the claim was resumed at all), but the
    # policy refuses to credit an ``independent_pair`` target to a claim
    # with no pair behind it.
    assert [claim.target_ids for claim in resumed] == [[]]


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
    """An insufficient claim cannot retain an ``independent_pair`` target.

    The badge is stated explicitly here: this is the *strongest* an unverified
    claim can be (the other policies' acceptance path), and the strict pair
    still refuses it.
    """
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="insufficient_evidence",
        supporting_publishers=2,
        evidence_status="source_supported",
    )
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="unverified",
        supporting_publishers=2,
        evidence_status="source_supported",
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


_SUPPORT_POLICIES = ("independent_pair", "primary_attribution", "derivation")


def _lone_publisher_support() -> Claim:
    """The shape the packet path writes for one complete primary support.

    One in-scope, complete, primary support from the claim's own single
    publisher and nothing beside it, so the claim is ``insufficient_evidence``
    beside ``source_supported``: one publisher standing behind a claim is
    attribution, not the independent pair a ``verified`` badge would show.
    """
    packet = _pair_packet(_eligibility(), _independent_second()).model_copy(
        update={"claim_target_ids": [TASK6_TARGET]}
    )
    return validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.7,
            assessments=[
                SupportAssessment(
                    evidence_id="ev-left",
                    stance="supports",
                    complete_support=True,
                    scope_compatible=True,
                    dependence="primary",
                )
            ],
            support_ids=["ev-left"],
            contradiction_ids=[],
            rationale="Only the operator's own report supports it.",
        ),
        packet,
        None,
    )


@pytest.mark.parametrize("policy", ["primary_attribution", "derivation"])
def test_a_source_supported_claim_answers_the_weaker_policies_own_target(
    policy: str,
) -> None:
    """Plan line 124: source-supported is not verified, and it is enough here.

    A primary report can support "report X estimates Y" without another
    publisher reproducing the measurement, which is what a
    ``primary_attribution``/``derivation`` target actually asks for. Demanding
    the strict pair of it — amendment defect item 6, "a strict verification
    badge was conflated with faithful primary attribution" — made all three
    policies one policy and left every official-measurement and calculation
    obligation unanswerable.
    """
    claim = _lone_publisher_support()

    assert claim.target_ids == [TASK6_TARGET]
    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status == "source_supported"
    assert supporting_publisher_count(claim) == 1

    assert admitted_target_ids(claim, {TASK6_TARGET: policy}) == [TASK6_TARGET]


def test_a_source_supported_claim_never_answers_the_strict_pair() -> None:
    """C4 stands unchanged: ``independent_pair`` still needs the verified pair.

    The same claim that now answers the weaker policies keeps earning nothing
    here, and a ``verified`` claim with one supporting publisher is refused by
    the two-publisher floor.
    """
    claim = _lone_publisher_support()

    assert admitted_target_ids(claim, {TASK6_TARGET: "independent_pair"}) == []
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="insufficient_evidence",
        supporting_publishers=2,
        evidence_status="source_supported",
    )
    assert not claim_meets_support_policy(
        support_policy="independent_pair",
        verdict="verified",
        supporting_publishers=1,
        evidence_status="verified_pair",
    )


@pytest.mark.parametrize("policy", _SUPPORT_POLICIES)
def test_a_verified_pair_keeps_answering_every_policy(policy: str) -> None:
    """The strict pair is the strongest evidence, so it satisfies every policy."""
    assert claim_meets_support_policy(
        support_policy=policy,
        verdict="verified",
        supporting_publishers=2,
        evidence_status="verified_pair",
    )


@pytest.mark.parametrize(
    ("verdict", "evidence_status"),
    [
        ("contradicted", None),
        ("contradicted", "contested"),
        ("contradicted", "verified_pair"),
        ("unverified", None),
        ("unverified", "source_supported"),
        ("insufficient_evidence", None),
        ("insufficient_evidence", "contested"),
        ("insufficient_evidence", "verified_pair"),
    ],
)
@pytest.mark.parametrize("policy", _SUPPORT_POLICIES)
def test_nothing_short_of_a_source_supported_reading_answers(
    policy: str, verdict: str, evidence_status: str | None
) -> None:
    """The one widened path is ``insufficient_evidence`` + ``source_supported``.

    A contradicted claim has evidence against it, an ``unverified`` one has
    evidence that does not address it, and an ``insufficient_evidence`` claim
    whose badge is anything else was never judged source-supported — none of
    them may be credited under any policy.
    """
    assert not claim_meets_support_policy(
        support_policy=policy,
        verdict=verdict,
        supporting_publishers=3,
        evidence_status=evidence_status,
    )


@pytest.mark.parametrize("policy", ["primary_attribution", "derivation"])
def test_a_source_supported_reading_still_needs_one_supporting_publisher(
    policy: str,
) -> None:
    """A badge with no supporting publisher behind it supports nothing."""
    assert not claim_meets_support_policy(
        support_policy=policy,
        verdict="insufficient_evidence",
        supporting_publishers=0,
        evidence_status="source_supported",
    )


def _partial_support_only() -> Claim:
    """The shape the adjudicator writes when nothing complete supports a claim.

    One passage is considered and judged a *partial* support — the
    ``complete_support``/``scope_compatible`` conjunction fails — so the
    supports list is filtered to complete supports and comes back empty while
    the candidate list does not. That is the ``no_complete_support`` branch of
    ``validate_adjudication``: the claim is honestly source-supported and
    publishes no supporting passage at all.
    """
    packet = _pair_packet(_eligibility(), _independent_second()).model_copy(
        update={"claim_target_ids": [TASK6_TARGET]}
    )
    return validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.4,
            assessments=[
                SupportAssessment(
                    evidence_id="ev-left",
                    stance="supports",
                    complete_support=False,
                    scope_compatible=True,
                    dependence="primary",
                )
            ],
            support_ids=["ev-left"],
            contradiction_ids=[],
            rationale="The passage supports only part of the claim.",
        ),
        packet,
        None,
    )


def test_a_reading_with_no_complete_support_carries_no_primary_badge() -> None:
    """No complete supporting passage, no primary-source attribution.

    ``no_complete_support`` used to write ``source_supported`` beside
    ``insufficient_evidence`` for a claim no complete support stood behind: the
    audited run published "primary-source attribution" for claims 8 and 11,
    whose recorded evidence selection was empty. The badge is the reader's
    "primary-source attribution" line, so it is written only where a complete
    supporting passage stands behind the claim. The admission floor
    (``supporting_publishers >= 1``) still holds *because* this branch records
    no supporting passage: the supports list was filtered to complete supports
    and is empty here, so the claim carries no publisher.
    """
    claim = _partial_support_only()

    assert claim.evidence_status is None
    assert "no_complete_support" in claim.audit_flags
    assert supporting_publisher_count(claim) == 0
    assert admitted_target_ids(
        claim, {TASK6_TARGET: "primary_attribution"}
    ) == []


def test_a_relay_support_is_not_primary_attribution() -> None:
    """The primary badge needs the passage the claim's issuer published.

    Claim 1 of the audited run — "EIA reported that generators … added 10.4 GW"
    — was published as "primary-source attribution", although its only
    supporting passage came from a trade-press relay of EIA's figure. A relay
    supports as a relay: the passage is still recorded behind the claim, and
    the badge is not awarded on it.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    ).model_copy(
        update={"claim_source_urls": ["https://eia.test/todayinenergy"]}
    )

    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.6,
            assessments=[_row("ev-left", "supports")],
            support_ids=["ev-left"],
            contradiction_ids=[],
            rationale="A relay repeats the agency's figure.",
        ),
        packet,
        None,
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status is None
    assert claim.insufficient_reason == "relay_source"
    assert "relay_source" in claim.audit_flags
    # The relay still supports: the passage the model selected is recorded as
    # the claim's supporting evidence, and the claim still answers nothing.
    assert [item.source_url for item in claim.verification_evidence] == [
        "https://left.test/left"
    ]


def test_a_complete_support_from_the_issuer_still_earns_the_badge() -> None:
    """The issuer test tightens the badge without emptying it.

    The same shape as the relay claim, with the packet citing the supporting
    passage's own publisher, is what the audited run's claims 2 and 5 were:
    the issuer's own account, completely supporting the claim.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    ).model_copy(update={"claim_target_ids": [TASK6_TARGET]})

    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.6,
            assessments=[_row("ev-left", "supports")],
            support_ids=["ev-left"],
            contradiction_ids=[],
            rationale="The agency's own account states the figure.",
        ),
        packet,
        None,
    )

    assert claim.evidence_status == "source_supported"
    assert "relay_source" not in claim.audit_flags
    assert admitted_target_ids(
        claim, {TASK6_TARGET: "primary_attribution"}
    ) == [TASK6_TARGET]


def test_a_clipped_candidate_is_never_judged_as_no_complete_support() -> None:
    """A passage the request could only show in part was never judged whole.

    The packet clipped every candidate to a uniform share, then recorded
    ``no_complete_support`` for claims whose figures sat past the cut: a
    verdict about text the model never saw. A candidate longer than the
    request may carry is recorded as partially shown instead — its
    incompleteness is an open question, and the claim says the passage was cut.
    """
    long_support = SUPPORT_TEXT + " " + ("Detail. " * 400)
    packet = with_render_boundaries(
        _verdict_packet(
            _eligibility(),
            _independent_second(),
            _third_origin(),
            texts=(long_support, AUDIT_TEXT, REFUTATION_TEXT),
        ),
        evidence_chars=FACT_CHECK_EVIDENCE_CHARS,
    )
    plan = plan_packet_rendering(
        packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
    )
    shown = {unit.evidence_id: text for unit, text in plan.rendered}

    assert shown["ev-left"] != " ".join(long_support.split())
    assert plan.partially_shown == ["ev-left"]

    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.6,
            assessments=[_row("ev-left", "supports")],
            support_ids=["ev-left"],
            contradiction_ids=[],
            rationale="The operator's own report states the figure.",
        ),
        packet,
        None,
    )

    assert claim.evidence_status is None
    assert "partially_shown" in claim.audit_flags
    assert "no_complete_support" not in claim.audit_flags
    assert claim.insufficient_reason == "partially_shown"
    assert admitted_target_ids(
        claim, {TASK6_TARGET: "primary_attribution"}
    ) == []


@pytest.mark.parametrize("policy", ["primary_attribution", "derivation"])
def test_an_omitted_evidence_status_is_the_conservative_reading(
    policy: str,
) -> None:
    """A caller that does not state the badge has not shown the weaker path."""
    assert not claim_meets_support_policy(
        support_policy=policy,
        verdict="insufficient_evidence",
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
async def test_a_legacy_passage_claim_retains_no_obligation_at_all(
    tracker: Tracker,
) -> None:
    """A passage-path claim cannot answer an obligation under any policy.

    The capped legacy path never publishes ``verified``, so the claim is
    written honestly as ``unverified`` - and a claim whose evidence does not
    address it answers nothing, under every policy: the weaker policies take a
    ``source_supported`` *``insufficient_evidence``* claim, never this one. The
    target stays outstanding rather than being credited to evidence that was
    never tested as a pair.
    """
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
    assert [claim.verdict for claim in outcome.result.claims] == ["unverified"]
    assert outcome.result.claims[0].evidence_status == "source_supported"
    assert outcome.result.claims[0].target_ids == []


# ---------------------------------------------------------------------------
# Task 6: the claim-specific evidence union and its strict pair rule
# ---------------------------------------------------------------------------

TASK6_CLAIM = "Wind capacity reached 10 GW in 2025."
A_URL = "https://lab-a.test/wind"
B_URL = "https://lab-b.test/audit"
A_TEXT = "Lab A reported that wind capacity reached 10 GW in 2025."
B_TEXT = "Lab B audited the figure: wind capacity reached 10 GW in 2025."
TASK6_TARGET = "target-1"

RIVAL_URL = "https://lab-c.test/rival"
RIVAL_TEXT = "Lab C measured 8 GW for the same year."
ELSEWHERE_URL = "https://lab-d.test/other"
ELSEWHERE_TEXT = "Lab D reported solar capacity."


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
        # The production adapter stamps the target a read was made for, which
        # is the link the packet's handoff audit resolves.
        target_ids=[TASK6_TARGET],
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
                dependence="primary",
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
    # The obligation survives to the published claim, so the scheduler can
    # retire an answered critical target instead of re-serving it forever.
    assert claim.target_ids == [TASK6_TARGET]
    assert claim.evidence_selection
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
                        dependence="primary",
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
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = {}
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
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
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)

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
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
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
                    dependence="primary",
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


def _obligation_state() -> ResearchState:
    """Two sub-topics, each read, and a claim citing only the first's page.

    The ids are the two the product actually mints: ``target_id_for`` writes an
    obligation per evidence target (``topic-01-target-01``), and the
    acquisition layer registers its units against the sub-topic the read was
    taken for (``topic-01``). The rival page is read for the *same* sub-topic
    as the claim's own source, and read for the other sub-topic too.
    """
    own = _ab_read("read-own", A_URL, "Lab A report", A_TEXT)
    rival = _ab_read("read-rival", RIVAL_URL, "Lab B audit", RIVAL_TEXT)
    elsewhere = _ab_read(
        "read-elsewhere", ELSEWHERE_URL, "Third topic", ELSEWHERE_TEXT
    )
    units = {
        unit.evidence_id: unit
        for unit in (
            build_evidence_unit(
                read=own,
                locator="chunk-0",
                excerpt=A_TEXT,
                origin="researcher",
                target_ids=["topic-01"],
            ),
            build_evidence_unit(
                read=rival,
                locator="chunk-0",
                excerpt=RIVAL_TEXT,
                origin="researcher",
                target_ids=["topic-01"],
            ),
            build_evidence_unit(
                read=elsewhere,
                locator="chunk-0",
                excerpt=ELSEWHERE_TEXT,
                origin="researcher",
                target_ids=["topic-02"],
            ),
        )
    }
    return ResearchState(
        session_id="session-1",
        original_question="How much wind capacity was added?",
        initial_target_ids=["topic-01-target-01"],
        sub_topics=[
            _targeted_topic("topic-01", "Alpha", "topic-01-target-01"),
            _targeted_topic("topic-02", "Beta", "topic-02-target-01"),
        ],
        read_records={read.read_id: read for read in (own, rival, elsewhere)},
        evidence_units=units,
    )


def test_the_pool_links_an_obligation_to_the_units_that_cover_it() -> None:
    """A contradicting account of the claim's own sub-topic reaches its packet.

    The claim cites the record that agrees with it and nothing else, so the
    citation link cannot admit the rival; the target link is the only way in.
    It compares the claim's obligation ids against the units' coverage ids -
    two id spaces that never meet, so it admits nothing in any case ever, and
    a document read for this exact sub-topic that disagrees with the figure is
    invisible to the adjudication that would have to settle it.
    """
    state = _obligation_state()
    draft = ClaimDraft(
        text="Wind capacity reached 10 GW in 2025.", source_urls=[A_URL]
    )

    pool = claim_evidence_pool(
        state, draft, target_ids=["topic-01-target-01"]
    )

    assert {unit.source_url for unit in pool} == {A_URL, RIVAL_URL}
    # And the link stays scoped to the claim's own sub-topic: another topic's
    # read in the same run is still not evidence for this claim.
    assert all(unit.source_url != ELSEWHERE_URL for unit in pool)


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
    dependence: str = "primary",
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
                    dependence=dependence,
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
    ("label", "complete", "scope"),
    [
        ("period-mismatch", True, False),
        ("unit-mismatch", False, True),
        ("scope-mismatch", False, False),
        ("compound-claim-partly-supported", False, True),
        ("quoted-speculation", False, True),
        ("stale-future-current-confusion", True, False),
    ],
)
def test_a_support_that_is_not_a_complete_in_scope_support_never_verifies(
    label: str, complete: bool, scope: bool
) -> None:
    """Section 2.1: each passage must support the COMPLETE atomic claim."""
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = _adjudication(packet, complete=complete, scope=scope)

    assert claim.verdict == "insufficient_evidence", label
    assert (
        "no_complete_support" in claim.audit_flags
        or "single_primary_only" in claim.audit_flags
    ), label


def test_a_faithful_primary_attribution_stays_source_supported() -> None:
    """One primary source is attribution, not independent corroboration."""
    packet = _pair_packet(_eligibility(), _independent_second())

    claim = _adjudication(packet, dependence="derivative")

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
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="contradicts",
                complete_support=right_complete,
                scope_compatible=right_scope,
                dependence="primary",
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
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="unrelated",
                complete_support=False,
                scope_compatible=False,
                dependence="primary",
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
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="contradicts",
                complete_support=True,
                scope_compatible=True,
                dependence="primary",
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
                dependence="primary",
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
        "partially_shown",
        "relay_source",
        "single_primary_only",
        "provider_unavailable",
        "schema_failed",
        "model_disagreement",
    ):
        assert INSUFFICIENT_REASONS.get(reason), reason

# ---------------------------------------------------------------------------
# Fix round 1: what the review found missing
# ---------------------------------------------------------------------------


class _RepairingAdjudicator:
    """A completer that reproduces the provider's bounded one-repair contract.

    On the first ``ClaimVerdictDraft`` request the reply is treated as
    malformed; the completer appends the provider's own repair turn to the
    *same* messages and asks again, then returns the valid draft. Both requests
    are recorded, which is what lets a test assert the packet survived the
    repair intact.
    """

    def __init__(self, *, valid, repairs: tuple[object, ...] = ()) -> None:
        self._valid = valid
        self._repairs = tuple(repairs)
        self._seen = 0
        self.requests: list[list[ChatMessage]] = []

    async def complete_react(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a sufficient packet needs no ReAct turn")

    async def complete_structured(
        self,
        messages,
        schema,
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> object:
        del agent_name, max_tokens
        if schema.__name__ == "ClaimsDraft":
            return ClaimsDraft(
                claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]
            )
        self.requests.append(list(messages))
        self._seen += 1
        if self._seen == 1:
            repaired = [
                *messages,
                ChatMessage(
                    role="system",
                    content=(
                        "The previous JSON response failed ClaimVerdictDraft "
                        "validation. Return only one JSON object that validates "
                        "against the supplied JSON Schema."
                    ),
                ),
            ]
            self.requests.append(repaired)
            return self._valid(repaired, schema)
        return self._valid(list(messages), schema)

    def drain_structured_repairs(self) -> tuple[object, ...]:
        return self._repairs


def _badge_claim(
    text: str,
    *,
    claim_id: str,
    verdict: str,
    evidence_status: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        text=text,
        source_urls=[A_URL],
        verdict=verdict,  # type: ignore[arg-type]
        evidence_status=evidence_status,  # type: ignore[arg-type]
        confidence=0.9,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
    )


def test_a_repaired_adjudication_publishes_its_diagnostic() -> None:
    """The collected repair record must be reportable, bounded and text-free.

    The provider repairs one malformed structured reply and returns the
    repaired parse, whose own finish reason is ``stop`` - identical to a clean
    first attempt. Unless the collected record is published, the categories and
    field paths of the rejected reply are gathered and then dropped.
    """
    record = StructuredRepairRecord(
        schema_name="ClaimVerdictDraft",
        diagnostics=(
            contracts_module.StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("support_ids",),
                category="missing",
            ),
        ),
    )

    event = adjudication_repaired_event(
        record, packet_fingerprint="packet-fingerprint-1"
    )

    assert event is not None
    assert event.event_type == "fact_checker.adjudication.repaired"
    assert event.metadata["packet_fingerprint"] == "packet-fingerprint-1"
    assert event.metadata["field_paths"] == ["support_ids"]
    assert event.metadata["categories"] == ["missing"]
    assert event.metadata["diagnostic_count"] == 1
    # Bounded and provider-output-free: locations and categories only.
    assert "excerpt" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_the_repair_event_reaches_the_state_update(tracker: Tracker) -> None:
    """A run whose reply was repaired reports it, and preserves the packet.

    The brief's malformed-then-valid case: the exact claim and evidence packet
    is intact in the repaired request, both requests are counted, and the
    diagnostic the provider recorded reaches the run's events.
    """
    completer = _RepairingAdjudicator(
        valid=_select_every_shown_id,
        repairs=(
            StructuredRepairRecord(
                schema_name="ClaimVerdictDraft",
                diagnostics=(
                    contracts_module.StructuredValidationDiagnostic(
                        attempt=1,
                        field_paths=("support_ids",),
                        category="missing",
                    ),
                ),
            ),
        ),
    )
    agent = _checker(tracker, completer)  # type: ignore[arg-type]

    outcome = await _task6_run(agent, _ab_state(), tracker)

    assert len(completer.requests) == 2
    first, repaired = completer.requests
    first_body = "\n".join(message.content for message in first)
    repaired_body = "\n".join(message.content for message in repaired)
    # The packet - claim text and both exact passages - survives the repair
    # byte for byte; the repair only appends.
    assert TASK6_CLAIM in repaired_body
    assert A_TEXT in repaired_body and B_TEXT in repaired_body
    assert repaired_body.startswith(first_body)
    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"
    repaired_events = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.adjudication.repaired"
    ]
    assert len(repaired_events) == 1
    assert repaired_events[0].metadata["packet_fingerprint"]


def test_the_manifest_ids_are_joinable_evidence_ids() -> None:
    """Section 2.4: the boundary lists must be the same kind of id."""
    packet = _pair_packet(_eligibility(), _independent_second())
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
                    dependence="primary",
                )
                for unit in packet.units
            ],
            support_ids=[unit.evidence_id for unit in packet.units],
            contradiction_ids=[],
            rationale="Both state the figure.",
        ),
        packet,
        None,
    )
    agent = object.__new__(FactCheckerAgent)
    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}

    FactCheckerAgent._record_packet_audit(
        agent, packet, claim, status="completed", target_ids=[TASK6_TARGET]
    )

    (audit,) = agent._adjudication_audits.values()
    inputs = set(audit.input_ids)
    # Locators used to land here; every list is now evidence ids, so the
    # manifest can be joined end to end.
    assert set(audit.selected_ids) <= inputs
    assert set(audit.accepted_ids) <= set(audit.selected_ids)
    assert audit.selected_ids
    assert all(item.startswith("ev-") for item in audit.input_ids)
    assert audit.target_ids == [TASK6_TARGET]
    assert audit.claim_cluster_ids == ["cluster-1"]


def test_a_second_pass_keeps_its_manifests_distinct_from_the_first() -> None:
    """One session's passes each keep their own manifest identity.

    A pass start clears the pass's own audit dict, so an id minted from its
    length restarts at 1: the second pass then writes a *different* manifest —
    a wider target set, in the case this is taken from — under the id the
    first pass already used. ``merge_boundary_audits`` refuses one id with two
    contents, and the fact-checker update is folded into the state as one
    mapping, so a session's second pass is rejected whole and the run halts on
    a bookkeeping collision instead of on a quality gap. The identity has to
    be unique for the *session*, not for the pass.
    """
    packet = _pair_packet(_eligibility(), _independent_second())
    agent = object.__new__(FactCheckerAgent)
    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}

    FactCheckerAgent._record_packet_audit(
        agent, packet, None, status="completed", target_ids=[TASK6_TARGET]
    )
    first_pass = dict(agent._adjudication_audits)
    # The next pass starts here: the pass's own dict is cleared, the session
    # is not.
    agent._adjudication_audits = {}
    FactCheckerAgent._record_packet_audit(
        agent,
        packet,
        None,
        status="completed",
        target_ids=[TASK6_TARGET, "topic-02-target-01"],
    )
    second_pass = dict(agent._adjudication_audits)

    assert set(first_pass).isdisjoint(second_pass)
    merged = merge_boundary_audits(first_pass, second_pass)
    assert len(merged) == 2
    assert {
        tuple(audit.target_ids) for audit in merged.values()
    } == {(TASK6_TARGET,), (TASK6_TARGET, "topic-02-target-01")}


def test_the_persisted_manifest_is_bounded_and_summarizes_its_overflow() -> None:
    """A 300-unit registry may not write a 300-row manifest for one claim."""
    units = [
        _pair_unit(f"u{index}", f"https://host{index}.test/a", "x")
        for index in range(40)
    ]
    packet = AdjudicationPacket(
        claim_id="claim-1",
        claim_text=TASK6_CLAIM,
        claim_source_urls=["https://one.test/a"],
        claim_cluster_id="cluster-1",
        units=units[:2],
        eligibility={},
        omitted=[
            EvidenceDisposition(
                item_id=unit.evidence_id,
                stage="adjudication-packet",
                reason="out_of_scope",
            )
            for unit in units[2:]
        ],
        omitted_count=len(units) - 2,
        fingerprint="fingerprint-1",
    )
    agent = object.__new__(FactCheckerAgent)
    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}

    FactCheckerAgent._record_packet_audit(
        agent, packet, None, status="completed", target_ids=[]
    )

    (audit,) = agent._adjudication_audits.values()
    assert len(audit.deferred_ids) <= MAX_PACKET_OMISSIONS
    assert any(
        item.startswith("omitted_overflow:") for item in audit.disposition_ids
    )
    # The exact count is not lost with the list.
    assert packet.omitted_count == 38


def test_a_handoff_loss_is_named_from_the_audit_not_the_test() -> None:
    """The diagnostic must come from the manifest, with the exact missing id.

    ``read-b``'s body is in the registry, the claim is linked to it, and no
    evidence unit cites it - so the passage-selection boundary is where the
    evidence stopped. The claim says ``handoff_loss``, and the manifest names
    the exact read id: the assertion reads that id out of the audit rather than
    handing it to the code under test.
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
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )

    assert packet.missing_read_ids
    assert packet.missing_read_count == len(packet.missing_read_ids)

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
                    dependence="primary",
                )
                for unit in packet.units
            ],
            support_ids=[unit.evidence_id for unit in packet.units],
            contradiction_ids=[],
            rationale="One report states the figure.",
        ),
        packet,
        None,
    )

    assert "handoff_loss" in claim.audit_flags
    assert claim.insufficient_reason == "handoff_loss"
    assert claim.verdict == "insufficient_evidence"

    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}
    FactCheckerAgent._record_packet_audit(
        agent, packet, claim, status="completed", target_ids=[TASK6_TARGET]
    )
    (audit,) = agent._adjudication_audits.values()
    handoffs = [
        item.split(":")[1]
        for item in audit.disposition_ids
        if item.startswith("read:") and item.endswith(":handoff_loss")
    ]
    # The id under test comes out of the audit, and it is the registry's own
    # read id for the document whose passage never reached the packet.
    assert handoffs == [
        read_id
        for read_id, read in state.read_records.items()
        if read.resolved_url == B_URL
    ]


def test_a_wrong_causal_direction_never_counts_as_a_complete_support() -> None:
    """The brief's case: the same numbers, the opposite causal claim.

    A passage that states the relationship backwards does not support the claim
    it looks like it supports, and the packet has no pair left: the claim is
    unsettled rather than settled on a reversed reading.
    """
    packet = _pair_packet(
        _eligibility(),
        _independent_second(),
        left_text="Cheap wind caused the capacity increase in 2025.",
        right_text="The capacity increase caused the drop in wind costs in 2025.",
    )
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-left",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="supports",
                complete_support=False,
                scope_compatible=True,
                dependence="primary",
            ),
        ],
        support_ids=["ev-left", "ev-right"],
        contradiction_ids=[],
        rationale="Both passages state the same two quantities.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status == "source_supported"
    assert claim.audit_flags
    # The reversed reading is not evidence for the claim: only the passage that
    # supports the complete assertion is published behind it.
    assert claim.evidence == ["Cheap wind caused the capacity increase in 2025."]
    assert all(
        "The capacity increase caused" not in excerpt
        for excerpt in claim.evidence
    )


def test_memory_candidates_are_counted_apart_from_read_support() -> None:
    """TR-01: a recall is discovery, and its count is never a read count."""
    state = _ab_state()
    state = state.model_copy(
        update={
            "memory_context": MemorySnapshot(
                similar_findings=[_check_finding(A_URL, sub_topic="Alpha")]
            )
        }
    )
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])

    counted = memory_candidate_count(state, draft)

    assert counted == 1
    # A different quantity, reported beside it: how many memory lookups ran.
    assert memory_recall_count(None) == 0
    # The packet's independent-publisher count is derived from validated read
    # support alone, and memory can never raise it.
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )
    assert _packet_independent_publishers(packet, None) == 0
    assert _packet_independent_publishers(None, None) == 0


@pytest.mark.asyncio
async def test_a_current_read_beats_a_repeated_high_confidence_memory(
    tracker: Tracker,
) -> None:
    """The brief: the reader follows the read, not the repetition.

    Memory asserts the opposite of what the run's own reads state, with high
    confidence and a remembered source URL. The claim is judged from the two
    reads - verified, because they are a real pair - and the remembered prose
    contributes no passage and no publisher. The memory candidates are reported
    beside it as their own number.
    """
    memory_claim = "Wind capacity reached 20 GW in 2025."
    claim_text = "Wind capacity did not reach 20 GW in 2025."
    state = _ab_state()
    state = state.model_copy(
        update={
            "memory_context": MemorySnapshot(
                similar_findings=[
                    _check_finding(A_URL, content=memory_claim, sub_topic="Alpha")
                ]
            )
        }
    )
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(claims=[ClaimDraft(text=claim_text, source_urls=[A_URL])]),
            _select_every_shown_id,
        ]
    )
    agent = _checker(tracker, completer)

    outcome = await _task6_run(agent, state, tracker)

    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"
    assert claim.evidence_status == "verified_pair"
    # The evidence is the reads' own text, never the remembered sentence.
    assert memory_claim not in claim.evidence
    assert A_TEXT in _adjudication_body(completer)
    events = outcome.state_update["events"]
    completed = next(
        event
        for event in events
        if event.event_type == "fact_checker.fact_check.completed"
    )
    assert completed.metadata["memory_candidates"] == 1
    assert completed.metadata["memory_recalls"] == 0
    checked = next(
        event
        for event in events
        if event.event_type == "fact_checker.claim.checked"
    )
    assert checked.metadata["memory_candidates"] == 1
    assert checked.metadata["independent_sources"] == 2


def test_the_verified_badge_is_structurally_required() -> None:
    """Critical 1's invariant, enforced where claims are built."""
    with pytest.raises(ValidationError):
        _badge_claim(TASK6_CLAIM, claim_id="claim-1", verdict="verified")
    with pytest.raises(ValidationError):
        _badge_claim(
            TASK6_CLAIM,
            claim_id="claim-1",
            verdict="verified",
            evidence_status="source_supported",
        )
    # The control: the badge is accepted with the pair behind it.
    backed = _badge_claim(
        TASK6_CLAIM,
        claim_id="claim-1",
        verdict="verified",
        evidence_status="verified_pair",
    )
    assert backed.evidence_status == "verified_pair"


def test_a_cluster_without_a_recorded_pair_badge_cannot_settle() -> None:
    """The cluster snapshot may not invent the badge its members never had."""
    claim = _badge_claim(
        TASK6_CLAIM, claim_id="claim-a", verdict="unverified"
    )
    cluster = cluster_for_atom(
        extract_atoms(claim)[0], claim=claim, created_seq=1
    )

    canonical = _canonical_claim(cluster)

    assert canonical.verdict == "unverified"
    assert canonical.evidence_status is None


def test_a_cluster_records_and_republishes_the_pair_badge() -> None:
    """The positive control: a member's badge survives into the snapshot."""
    claim = _badge_claim(
        TASK6_CLAIM,
        claim_id="claim-a",
        verdict="verified",
        evidence_status="verified_pair",
    )
    cluster = cluster_for_atom(
        extract_atoms(claim)[0], claim=claim, created_seq=1
    )

    canonical = _canonical_claim(cluster)

    assert canonical.verdict == "verified"
    assert canonical.evidence_status == "verified_pair"

# ---------------------------------------------------------------------------
# Fix round 2
# ---------------------------------------------------------------------------


class _RepairBuffering:
    """A completer whose repair buffer is served from a scripted queue.

    ``complete_structured`` answers the adjudication request from ``valid`` and
    the extraction request with one claim, so a test can decide exactly what
    the provider was holding at each drain.
    """

    def __init__(self, *, valid, drains: list[tuple[object, ...]]) -> None:
        self._valid = valid
        self._drains = list(drains)
        self.drain_calls = 0

    async def complete_react(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a sufficient packet needs no ReAct turn")

    async def complete_structured(
        self,
        messages,
        schema,
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> object:
        del agent_name, max_tokens
        if schema.__name__ == "ClaimsDraft":
            return ClaimsDraft(
                claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]
            )
        return self._valid(list(messages), schema)

    def drain_structured_repairs(self) -> tuple[object, ...]:
        self.drain_calls += 1
        if self._drains:
            return self._drains.pop(0)
        return ()


def _repair_record(schema_name: str) -> StructuredRepairRecord:
    return StructuredRepairRecord(
        schema_name=schema_name,
        diagnostics=(
            contracts_module.StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("support_ids",),
                category="missing",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_a_stale_buffered_repair_is_never_published_for_this_packet(
    tracker: Tracker,
) -> None:
    """A repair recorded before this call belongs to different work.

    The provider hands back everything buffered since the last drain, and the
    legacy verdict path never drains at all — so without discarding what was
    already held, a repair from an unrelated request would be published against
    this packet's fingerprint.
    """
    completer = _RepairBuffering(
        valid=_select_every_shown_id,
        drains=[
            (_repair_record("PassageVerdictDraft"),),  # buffered earlier
            (),  # nothing from this call
        ],
    )
    agent = _checker(tracker, completer)  # type: ignore[arg-type]

    outcome = await _task6_run(agent, _ab_state(), tracker)

    assert completer.drain_calls == 2
    repaired = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.adjudication.repaired"
    ]
    assert repaired == []
    (claim,) = outcome.result.claims
    assert claim.verdict == "verified"


@pytest.mark.asyncio
async def test_this_calls_own_repair_is_published(tracker: Tracker) -> None:
    """The control: what this call produced is reported, once."""
    completer = _RepairBuffering(
        valid=_select_every_shown_id,
        drains=[(), (_repair_record("ClaimVerdictDraft"),)],
    )
    agent = _checker(tracker, completer)  # type: ignore[arg-type]

    outcome = await _task6_run(agent, _ab_state(), tracker)

    repaired = [
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.adjudication.repaired"
    ]
    assert len(repaired) == 1
    assert repaired[0].metadata["packet_fingerprint"]


def test_the_manifest_refuses_are_visible_and_joinable() -> None:
    """A refused selection appears as refused, not as "never selected"."""
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
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-right",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                dependence="primary",
            ),
            SupportAssessment(
                evidence_id="ev-invented",
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                dependence="primary",
            ),
        ],
        support_ids=["ev-left", "ev-right", "ev-invented"],
        contradiction_ids=[],
        rationale="All three agree.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.refused_evidence_ids == ["ev-invented"]
    assert set(claim.evidence_selection) == {"ev-left", "ev-right"}
    agent = object.__new__(FactCheckerAgent)
    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}
    FactCheckerAgent._record_packet_audit(
        agent, packet, claim, status="completed", target_ids=[TASK6_TARGET]
    )
    (audit,) = agent._adjudication_audits.values()
    assert "refused:ev-invented" in audit.disposition_ids
    assert "ev-invented" not in audit.selected_ids
    assert set(audit.selected_ids) == {"ev-left", "ev-right"}


def test_a_mirror_pair_is_joined_by_id_not_by_excerpt() -> None:
    """Two units of one document carry identical text, so text cannot be the key.

    The model selects one of them; an excerpt join would report both as
    selected and both as accepted, which is exactly the over-count the manifest
    must not make.
    """
    left = _pair_unit("left", "https://one.test/a", "identical text")
    mirror = EvidenceUnit(
        evidence_id="ev-mirror",
        read_id="read-mirror",
        source_url="https://mirror.test/a",
        source_title="mirror title",
        locator="p. 1",
        excerpt="identical text",
        target_ids=[TASK6_TARGET],
        origin="researcher",
    )
    packet = AdjudicationPacket(
        claim_id="claim-1",
        claim_text=TASK6_CLAIM,
        claim_source_urls=["https://one.test/a"],
        claim_target_ids=[TASK6_TARGET],
        claim_cluster_id="cluster-1",
        units=[left, mirror],
        eligibility={
            left.evidence_id: _eligibility(),
            mirror.evidence_id: _independent_second(),
        },
        fingerprint="fingerprint-1",
    )
    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="verified",
            confidence=0.9,
            assessments=[
                SupportAssessment(
                    evidence_id=left.evidence_id,
                    stance="supports",
                    complete_support=True,
                    scope_compatible=True,
                    dependence="primary",
                )
            ],
            support_ids=[left.evidence_id],
            contradiction_ids=[],
            rationale="One of the two copies states it.",
        ),
        packet,
        None,
    )
    agent = object.__new__(FactCheckerAgent)
    agent._session_id = "session-1"
    agent._evidence_chars = 4000
    agent._passages_per_read = 4
    agent._adjudication_audits = {}

    FactCheckerAgent._record_packet_audit(
        agent, packet, claim, status="completed", target_ids=[]
    )

    (audit,) = agent._adjudication_audits.values()
    assert list(audit.selected_ids) == ["ev-left"]
    assert list(audit.accepted_ids) == ["ev-left"]


def test_the_packet_path_carries_and_gates_the_claims_obligations() -> None:
    """The support-policy gate must filter a real list, not an empty one."""
    state = _ab_state()
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)

    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )

    assert packet.claim_target_ids == [TASK6_TARGET]
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
                    dependence="primary",
                )
                for unit in packet.units
            ],
            support_ids=[unit.evidence_id for unit in packet.units],
            contradiction_ids=[],
            rationale="Both state the figure.",
        ),
        packet,
        None,
    )
    assert claim.target_ids == [TASK6_TARGET]
    # And the gate has something to refuse: the same claim without the pair is
    # an insufficient claim, whose obligations the policy drops.
    insufficient = claim.model_copy(
        update={"verdict": "insufficient_evidence", "evidence_status": None}
    )
    assert (
        fact_checker_module.admitted_target_ids(
            insufficient, {TASK6_TARGET: "independent_pair"}
        )
        == []
    )


@pytest.mark.asyncio
async def test_a_handoff_loss_is_dropped_once_the_retrieval_repairs_it(
    tracker: Tracker,
) -> None:
    """A loss the run repaired is not a loss.

    The packet starts without ``read-b``'s passage; the bounded retrieval then
    re-reads that very document. The augmented packet must report no handoff
    loss — a stale one would teach a reader to distrust the audit.
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
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
    agent._session_id = "session-1"
    agent._passages_per_read = 4
    agent._new_reads = {}
    agent._new_evidence = {}
    agent._new_dispositions = []
    # The re-read source goes through Task 4's service like any other.
    agent._provider = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url=B_URL,
                        authority_score=0.9,
                        recency_score=0.9,
                        relevance_score=0.9,
                        rationale="Independent and dated.",
                    )
                ]
            )
        ]
    )
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )
    assert packet.missing_read_ids

    task = ClaimTask(
        instruction="Verify.",
        claim=draft,
        target_ids=[TASK6_TARGET],
        packet=packet,
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "text": B_TEXT,
                    "requested_url": B_URL,
                    "resolved_url": B_URL,
                    "title": "Lab B audit",
                },
            )
        ],
        tool_calls=1,
    )

    augmented = await FactCheckerAgent._augment_packet(agent, packet, run, task)

    assert augmented is not None
    assert augmented.missing_read_ids == []
    assert augmented.missing_read_count == 0
    # The re-read document is in the packet now, which is why the loss is gone.
    assert any(unit.source_url == B_URL for unit in augmented.units)


def _reread_agent(state: ResearchState) -> FactCheckerAgent:
    """A Fact Checker mid-pass over reads the run already holds."""
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
    agent._session_id = "session-1"
    agent._passages_per_read = 4
    agent._new_reads = {}
    agent._new_evidence = {}
    agent._new_dispositions = []
    # The re-read source goes through Task 4's service like any other.
    agent._provider = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url=A_URL,
                        authority_score=0.9,
                        recency_score=0.9,
                        relevance_score=0.9,
                        rationale="Independent and dated.",
                    )
                ]
            )
        ]
    )
    return agent


async def _reread_pass(
    state: ResearchState,
    agent: FactCheckerAgent,
    payload: dict[str, str],
) -> None:
    """One retrieval step over a body the run already holds."""
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )
    task = ClaimTask(
        instruction="Verify.",
        claim=draft,
        target_ids=[TASK6_TARGET],
        packet=packet,
    )
    await FactCheckerAgent._augment_packet(
        agent,
        packet,
        ReActRun(
            agent_name="fact_checker",
            stop_reason="finished",
            steps=[_tool_step(1, "web_scraper", payload)],
            tool_calls=1,
        ),
        task,
    )


@pytest.mark.asyncio
async def test_a_reread_of_a_recorded_body_keeps_the_recorded_description() -> None:
    """One read id is described once: a second spelling is not a second body.

    The Fact Checker repairs a handoff loss by reading a document the run
    already holds, and the URL its loop was handed can be another spelling of
    that page: the live run fetched
    ``https://www.eia.gov/todayinenergy/detail.php?id=67925`` for a read the
    Researcher had recorded as
    ``https://eia.gov/todayinenergy/detail.php?id=67925``. Both spellings are
    one body — same reader, same resolved URL, same content — so both mint one
    ``read_id``, and the two ``requested_url`` values reached
    ``merge_read_records`` as a rewrite of that identity. The node's whole
    state update was rejected, so the run published no report at all.
    """
    state = _ab_state()
    agent = _reread_agent(state)
    reread_url = "https://www.lab-a.test/wind"

    await _reread_pass(
        state,
        agent,
        {
            "text": A_TEXT,
            "requested_url": reread_url,
            "resolved_url": reread_url,
            "title": "Lab A report",
        },
    )

    recorded = next(
        read for read in state.read_records.values() if read.requested_url == A_URL
    )
    assert set(agent._new_reads) == {recorded.read_id}
    merged = merge_research_state(
        state,
        {
            "read_records": dict(agent._new_reads),
            "evidence_units": dict(agent._new_evidence),
        },
    )
    # The run's first description of that body stands; the re-read adds none.
    assert merged.read_records[recorded.read_id].requested_url == A_URL
    assert len(merged.evidence_units) == len(state.evidence_units)


@pytest.mark.asyncio
async def test_a_reread_may_not_relabel_a_recorded_passage() -> None:
    """The run's first label for a read id stands for every later admission.

    The other half of the same identity rule, and the failure the Researcher's
    own policies already hit: a reader that found no title falls back to the
    URL, so one body read twice carries two labels while minting one evidence
    identity. ``merge_evidence_units`` refuses two ``source_title`` values for
    one identity, and the Fact Checker is the admission boundary that had no
    memory of the recorded label.
    """
    state = _ab_state()
    agent = _reread_agent(state)

    await _reread_pass(
        state,
        agent,
        {
            "text": A_TEXT,
            "requested_url": A_URL,
            "resolved_url": A_URL,
            "title": "",
        },
    )

    merged = merge_research_state(
        state,
        {
            "read_records": dict(agent._new_reads),
            "evidence_units": dict(agent._new_evidence),
        },
    )
    assert {unit.source_title for unit in merged.evidence_units.values()} == {
        "Lab A report",
        "Lab B audit",
    }
    assert len(merged.evidence_units) == len(state.evidence_units)


def test_a_legacy_contradicted_claim_is_contested() -> None:
    """The legacy badge follows the verdict the passages produced."""
    contradiction = _verdict_draft(
        verdict="contradicted", contradictions=["A third party disagrees."]
    )
    claim = build_claim(
        _claim_draft(),
        contradiction,
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )

    assert claim.verdict == "contradicted"
    assert claim.evidence_status == "contested"
    # The control: a supported legacy claim is attribution, not a contest.
    supported = build_claim(
        _claim_draft(),
        _verdict_draft(verdict="verified"),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )
    assert supported.verdict == "unverified"
    assert supported.evidence_status == "source_supported"


# --------------------------------------------------------------------------
# Consolidation: the pass that adjudicates a claim is the pass that clusters it
# --------------------------------------------------------------------------
#
# ``consolidate_claims`` was built and tested in isolation, and called from
# nowhere in ``src/``: ``state.claim_clusters`` was ``{}`` on every real run,
# so ``clusters_for_claims``, ``statement_claims``, and every consumer Tasks
# 7-11 built on them read a field that was always empty. These tests drive a
# real ``FactCheckerAgent.run(state)`` and check the producer.

QUEUE_TEXTS = (
    "The 2024 interconnection queue held 10 GW of capacity.",
    "10 GW sat in the 2024 interconnection queue.",
)
QUEUE_PRIOR_PERIOD_TEXT = "The 2023 interconnection queue held 10 GW of capacity."
QUEUE_URL_A = "https://example.org/a"
QUEUE_URL_B = "https://example.org/b"


def _queue_finding(url: str, text: str) -> Finding:
    return _check_finding(url, content=text, sub_topic=ALPHA)


def _queue_state(*pairs: tuple[str, str]) -> ResearchState:
    return _check_state(
        [_queue_finding(url, text) for url, text in pairs],
        sources=[_scored(url) for url, _ in pairs],
    )


def _queue_draft(*pairs: tuple[str, str]) -> ClaimsDraft:
    return ClaimsDraft(
        claims=[ClaimDraft(text=text, source_urls=[url]) for url, text in pairs]
    )


def _proposal(*pairs: tuple[int, int]) -> ClaimEquivalenceDraft:
    """The provider's reply to the equivalence request, by position."""
    return ClaimEquivalenceDraft(
        pairs=[AtomicPairDraft(left=left, right=right) for left, right in pairs]
    )


def _judged_records(claims: list[Claim]) -> list[dict[str, object]]:
    """The adjudicated record of each claim, without its cluster linkage.

    Two runs are compared on what was judged, so the two fields consolidation
    writes are excluded: a difference about which cluster a claim joined must
    not be able to hide a difference in the verdict itself.
    """
    return [
        claim.model_dump(mode="json", exclude={"cluster_id", "cluster_aliases"})
        for claim in claims
    ]


async def _queue_pass(
    tracker: Tracker, outputs: list[object]
) -> tuple[AgentRun[VerifiedClaims], ResearchState, ScriptedCompleter]:
    """One real two-claim pass over two paraphrases, every reply scripted."""
    completer = ScriptedCompleter(
        decisions=[*_check_decisions(), *_check_decisions()], outputs=outputs
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _queue_state(
        (QUEUE_URL_A, QUEUE_TEXTS[0]), (QUEUE_URL_B, QUEUE_TEXTS[1])
    )
    async with tracker.session_span(state.session_id, state.original_question):
        return await agent.run(state), state, completer


def _paraphrase_pass(*, equivalence: object) -> list[object]:
    """The scripted replies of one two-claim pass, minus the loop decisions."""
    return [
        _queue_draft(
            (QUEUE_URL_A, QUEUE_TEXTS[0]), (QUEUE_URL_B, QUEUE_TEXTS[1])
        ),
        _verdict_draft(verdict="verified"),
        _verdict_draft(verdict="verified"),
        equivalence,
    ]


@pytest.mark.asyncio
async def test_a_pass_publishes_the_cluster_its_adjudicated_claims_join(
    tracker: Tracker,
) -> None:
    """Two paraphrases of one fact publish one cluster, and both claims say so.

    The producer Tasks 7-11 were built against and never got: before this
    wiring ``consolidate_claims`` was called from nowhere in ``src/``, so
    ``claim_clusters`` was ``{}`` on every real run while every fixture
    hand-injected a cluster and passed.
    """
    outcome, state, completer = await _queue_pass(
        tracker, _paraphrase_pass(equivalence=_proposal((1, 2)))
    )

    registry = outcome.state_update["claim_clusters"]
    assert len(registry) == 1
    published = outcome.state_update["verified_claims"]
    assert len(published) == 2
    cluster_ids = {claim.cluster_id for claim in published}
    # One id, and it is a real one: publishing the registry without stamping
    # the claims would leave both at ``None``.
    assert len(cluster_ids) == 1
    cluster_id = cluster_ids.pop()
    assert cluster_id is not None
    assert cluster_id in registry
    assert registry[cluster_id].member_claim_ids == [
        claim.claim_id for claim in published
    ]
    # ``merge_claim_cluster_registry`` raises on a key that is not its
    # cluster's own id and on a cluster re-anchored on a proposition that does
    # not mint it, and no production update had ever exercised it.
    merged = merge_research_state(state, outcome.state_update)
    assert merged.claim_clusters == registry
    # R8: consolidation spends a model call, never a tool call. The two loops
    # consumed the four tool decisions the script queued, which is the number
    # this same scenario spent before the wiring existed.
    assert outcome.react.tool_calls == 4
    assert [name for name, _, _ in completer.calls] == [
        "ClaimsDraft",
        "PassageVerdictDraft",
        "PassageVerdictDraft",
        "ClaimEquivalenceDraft",
    ]


@pytest.mark.asyncio
async def test_a_stated_period_difference_survives_a_provider_pair(
    tracker: Tracker,
) -> None:
    """The negative control: merging needs more than the provider saying so.

    The same scripted "these two are a pair" reply as the headline case, over
    two claims that differ in observation period. Without this, "both claims
    carry one cluster id" is satisfied by an implementation that merges
    everything the provider points at.
    """
    completer = ScriptedCompleter(
        decisions=[*_check_decisions(), *_check_decisions()],
        outputs=[
            _queue_draft(
                (QUEUE_URL_A, QUEUE_TEXTS[0]),
                (QUEUE_URL_B, QUEUE_PRIOR_PERIOD_TEXT),
            ),
            _verdict_draft(verdict="verified"),
            _verdict_draft(verdict="verified"),
            _proposal((1, 2)),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _queue_state(
        (QUEUE_URL_A, QUEUE_TEXTS[0]),
        (QUEUE_URL_B, QUEUE_PRIOR_PERIOD_TEXT),
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    registry = outcome.state_update["claim_clusters"]
    assert len(registry) == 2
    published = outcome.state_update["verified_claims"]
    assert len({claim.cluster_id for claim in published}) == 2
    assert {claim.cluster_id for claim in published} == set(registry)
    assert (
        merge_research_state(state, outcome.state_update).claim_clusters
        == registry
    )
    # The refusal is recorded, so a report can say why two rows stand where
    # one was proposed.
    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.fact_check.completed"
    )
    assert completed.metadata["cluster_count"] == 2
    assert completed.metadata["consolidation_diagnostics"] == [
        "equivalence_candidate_incompatible:1:2"
    ]
    assert outcome.react.tool_calls == 4


@pytest.mark.asyncio
async def test_a_refinement_pass_keeps_the_stored_cluster_identity(
    tracker: Tracker,
) -> None:
    """``existing=`` is wired, and ``drafts`` is this pass's claims only.

    A pass that handed the whole ``verified_claims`` snapshot in as ``drafts``
    and passed no ``existing`` would mint a second cluster for one fact and
    publish two rows for one assertion.
    """
    first_completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, QUEUE_TEXTS[0])),
            _verdict_draft(verdict="verified"),
        ],
    )
    first_agent = _checker_for_passes(tracker, first_completer, searches=1)
    state = _queue_state((QUEUE_URL_A, QUEUE_TEXTS[0]))
    async with tracker.session_span(state.session_id, state.original_question):
        first = await first_agent.run(state)

    first_claim = first.state_update["verified_claims"][0]
    first_cluster_id = first_claim.cluster_id
    assert first_cluster_id is not None

    carried = merge_research_state(state, first.state_update)
    second_state = carried.model_copy(
        update={
            "raw_findings": [
                *carried.raw_findings,
                _queue_finding(QUEUE_URL_B, QUEUE_TEXTS[1]),
            ],
            "evaluated_sources": [
                *carried.evaluated_sources,
                _scored(QUEUE_URL_B),
            ],
        }
    )
    second_completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_B, QUEUE_TEXTS[1])),
            _verdict_draft(verdict="verified"),
            _proposal((1, 2)),
        ],
    )
    second_agent = _checker_for_passes(tracker, second_completer, searches=1)
    async with tracker.session_span(
        second_state.session_id, second_state.original_question
    ):
        second = await second_agent.run(second_state)

    registry = second.state_update["claim_clusters"]
    assert list(registry) == [first_cluster_id]
    published = {
        claim.text: claim for claim in second.state_update["verified_claims"]
    }
    survivor = registry[first_cluster_id]
    assert sorted(survivor.member_claim_ids) == sorted(
        [first_claim.claim_id, published[QUEUE_TEXTS[1]].claim_id]
    )
    # Both claims carry pass one's identity: the refinement joined the stored
    # cluster, it did not mint a second one beside it.
    assert published[QUEUE_TEXTS[0]].cluster_id == first_cluster_id
    assert published[QUEUE_TEXTS[1]].cluster_id == first_cluster_id
    assert (
        merge_research_state(second_state, second.state_update).claim_clusters
        == registry
    )


@pytest.mark.asyncio
async def test_a_second_fact_checker_does_not_remint_used_audit_ids(
    tracker: Tracker,
) -> None:
    """A fresh ``FactCheckerAgent`` built against a state that already carries
    boundary audits must not remint sequence numbers an earlier instance
    already used for the same ``(job, agent, operation)`` triple.

    Bug 3 (latent, currently unreachable in production): ``_audit_sequence``
    lives only on the instance, reset to 0 in ``__init__`` and never
    re-seeded from ``state.boundary_audits`` when a pass starts. A second
    construction against a state that already holds one adjudication audit
    mints its own first audit at the exact same sequence (both agents claim
    sequence 1 for their first packet) for the same job/agent/operation, with
    different packet content — so ``merge_boundary_audits`` raises
    ``EvidenceIdentityConflict``. Mirrors
    ``test_a_second_researcher_does_not_remint_used_audit_ids``.

    ``_packet_for`` bails out to the legacy passage path (no packet at all, so
    no ``_record_packet_audit`` call) whenever a state carries neither
    ``evidence_units`` nor ``read_records`` yet — the "nothing has ever been
    read in this job" case. A seed read, standing in for a Researcher's
    upstream work, unblocks the packet path. Each claim's own retrieval here
    (of ``INDEPENDENT_URL``, via ``_check_decisions``) never selects a
    passage the claim's own query counts as relevant, so its packet keeps
    zero units and both claims settle on the ``no_candidate`` shortcut rather
    than a model verdict — but that shortcut still records a packet audit
    (``task.packet`` is not ``None``, just empty), which is exactly the
    minting path this test needs.
    """
    seed_read = _ab_read(
        "read-seed", "https://seed.test/unrelated", "Seed", "Seed text."
    )
    independent_source_score = SourceScoresDraft(
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
    )

    first_completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, QUEUE_TEXTS[0])),
            independent_source_score,
        ],
    )
    first_agent = _checker_for_passes(tracker, first_completer, searches=1)
    state = _queue_state((QUEUE_URL_A, QUEUE_TEXTS[0])).model_copy(
        update={"read_records": {seed_read.read_id: seed_read}}
    )
    async with tracker.session_span(state.session_id, state.original_question):
        first = await first_agent.run(state)

    merged = merge_research_state(state, first.state_update)
    first_ids = set(merged.boundary_audits)
    # Sanity: the fixture must actually exercise the minting path, or the
    # rest of this test would pass vacuously.
    assert first_ids

    # A fresh instance, as a checkpoint restore or any other re-construction
    # against already-populated state would produce. Its own
    # ``_audit_sequence`` starts at 0 again, and it works a second, distinct
    # claim so it genuinely mints a new packet audit rather than being
    # short-circuited as already-adjudicated.
    second_state = merged.model_copy(
        update={
            "raw_findings": [
                *merged.raw_findings,
                _queue_finding(QUEUE_URL_B, QUEUE_TEXTS[1]),
            ],
            "evaluated_sources": [
                *merged.evaluated_sources,
                _scored(QUEUE_URL_B),
            ],
        }
    )
    second_completer = ScriptedCompleter(
        decisions=_check_decisions(),
        # No score draft: the first pass's assessment of the document this
        # pass re-reads is now saved on the state with that read, so the
        # service reuses it for unchanged content instead of buying it twice.
        outputs=[
            _queue_draft((QUEUE_URL_B, QUEUE_TEXTS[1])),
            _proposal((1, 2)),
        ],
    )
    second_agent = _checker_for_passes(tracker, second_completer, searches=1)
    async with tracker.session_span(
        second_state.session_id, second_state.original_question
    ):
        second = await second_agent.run(second_state)

    final = merge_research_state(second_state, second.state_update)
    second_new_ids = set(final.boundary_audits) - first_ids
    # Sanity: the second run must also have minted something new, or the
    # disjointness assertion below would pass vacuously too.
    assert second_new_ids
    assert second_new_ids.isdisjoint(first_ids)
    assert len(final.boundary_audits) > len(merged.boundary_audits)


@pytest.mark.asyncio
async def test_a_pass_that_adjudicates_nothing_consolidates_nothing(
    tracker: Tracker,
) -> None:
    """R6: nothing adjudicated means nothing to consolidate, and no call.

    With no drafts the candidate list is just the stored propositions, so the
    call could only re-merge stored identities against each other with no new
    evidence. The pass makes one structured call and publishes no
    ``claim_clusters`` key at all rather than an empty one.
    """
    completer = ScriptedCompleter(
        decisions=[_output_limit_error()],
        outputs=[
            _queue_draft(
                (QUEUE_URL_A, QUEUE_TEXTS[0]), (QUEUE_URL_B, QUEUE_TEXTS[1])
            )
        ],
    )
    agent = _checker(tracker, completer)
    state = _queue_state(
        (QUEUE_URL_A, QUEUE_TEXTS[0]), (QUEUE_URL_B, QUEUE_TEXTS[1])
    )

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.claims == []
    assert [schema for schema, _, _ in completer.calls] == ["ClaimsDraft"]
    assert completer.budgets == [None]
    assert "claim_clusters" not in outcome.state_update


@pytest.mark.asyncio
async def test_a_one_claim_pass_consolidates_without_an_equivalence_call(
    tracker: Tracker,
) -> None:
    """R6: the caller adds no ``len(atoms) <= 1`` skip of its own.

    ``consolidate_claims`` already skips its own provider call at one atom
    while still minting the cluster. A caller-side skip would produce no
    clusters at all for the single-claim pass — the common case.
    """
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, QUEUE_TEXTS[0])),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _queue_state((QUEUE_URL_A, QUEUE_TEXTS[0]))

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    assert [name for name, _, _ in completer.calls] == [
        "ClaimsDraft",
        "PassageVerdictDraft",
    ]
    registry = outcome.state_update["claim_clusters"]
    assert len(registry) == 1
    published = outcome.state_update["verified_claims"]
    assert len(published) == 1
    assert published[0].cluster_id in registry
    merged = merge_research_state(state, outcome.state_update)
    assert merged.claim_clusters == registry


@pytest.mark.asyncio
async def test_a_failed_equivalence_provider_still_publishes_the_clusters(
    tracker: Tracker,
) -> None:
    """R7: a degraded consolidation is non-halting, and still publishes.

    The ids come from ``claim_cluster_id`` — a pure function of the assertion,
    folded by local identity — so the degraded result is under-merged, never
    wrongly merged. ``{}`` would be strictly worse: it reproduces the defect
    this wiring removes.
    """
    control, _, _ = await _queue_pass(
        tracker, _paraphrase_pass(equivalence=_proposal((1, 2)))
    )
    degraded, state, completer = await _queue_pass(
        tracker,
        _paraphrase_pass(
            equivalence=ProviderTimeoutError(
                "the equivalence request timed out"
            )
        ),
    )

    assert degraded.result is not None
    assert [name for name, _, _ in completer.calls][-1] == "ClaimEquivalenceDraft"
    # Section 2.1: not one verdict or badge moved.
    assert _judged_records(degraded.state_update["verified_claims"]) == (
        _judged_records(control.state_update["verified_claims"])
    )
    assert degraded.react.stop_reason == control.react.stop_reason
    assert degraded.react.stop_reason != "provider_error"
    assert degraded.react.tool_calls == control.react.tool_calls == 4
    # The clusters are still published, and each claim still names its own.
    registry = degraded.state_update["claim_clusters"]
    assert len(registry) == 2
    assert {
        claim.cluster_id for claim in degraded.state_update["verified_claims"]
    } == set(registry)
    merged = merge_research_state(state, degraded.state_update)
    assert merged.claim_clusters == registry
    # Exactly one recoverable error, and it travels in the merged run.
    assert [error.error_type for error in degraded.errors] == [
        "fact_checker_claim_consolidation_degraded"
    ]
    recorded = degraded.errors[0]
    assert recorded.recoverable is True
    assert recorded.source == "agent.fact_checker"
    assert recorded.details["diagnostics"] == ["equivalence_provider_failed"]
    assert recorded in degraded.react.errors


@pytest.mark.asyncio
async def test_a_cluster_addressed_statement_resolves_to_the_adjudicated_claims(
    tracker: Tracker,
) -> None:
    """The consumer that was dead: a statement names a cluster, not a claim.

    ``statement_claims`` resolves a cluster id only through ``claim.claim_id``
    or ``claim.cluster_id`` / ``claim.cluster_aliases``, and a cluster id is
    not a claim id — so every cluster-addressed statement resolved to zero
    claims and ``statement_satisfies_support_policy`` could not see the
    evidence behind it.
    """
    outcome, state, _ = await _queue_pass(
        tracker, _paraphrase_pass(equivalence=_proposal((1, 2)))
    )
    merged = merge_research_state(state, outcome.state_update)
    claims = list(merged.verified_claims)
    assert len(claims) == 2
    cluster_id = claims[0].cluster_id
    assert cluster_id is not None
    assert cluster_id in merged.claim_clusters
    assert clusters_for_claims(claims, merged.claim_clusters) == [cluster_id]

    statement = ReportStatement(
        statement_id="S1",
        text="The 2024 interconnection queue held 10 GW of capacity.",
        claim_cluster_ids=[cluster_id],
    )
    composition = ReportComposition(
        question=merged.original_question,
        session_id=merged.session_id,
        claims=claims,
        claim_clusters=merged.claim_clusters,
    )

    resolved = statement_claims(composition, statement)
    # Before this wiring this was ``[]`` for every cluster-addressed
    # statement: a cluster id is not a claim id, and nothing stamped the
    # claims with the cluster they joined.
    assert resolved
    assert {claim.cluster_id for claim in resolved} == {cluster_id}
    assert {claim.claim_id for claim in resolved} <= {
        claim.claim_id for claim in claims
    }
    # A statement names a cluster, not a claim, so one named cluster resolves
    # to its representative claim; the whole member union behind it is what
    # ``clusters_for_claims`` returns, and it returns the same cluster.
    assert clusters_for_claims(resolved, merged.claim_clusters) == [cluster_id]
    # The control: the same claims with no cluster link — what every real run
    # published before this wiring — resolve a cluster-addressed statement to
    # nothing at all.
    unstamped = [
        claim.model_copy(update={"cluster_id": None, "cluster_aliases": []})
        for claim in claims
    ]
    assert (
        statement_claims(
            ReportComposition(
                question=merged.original_question,
                session_id=merged.session_id,
                claims=unstamped,
                claim_clusters=merged.claim_clusters,
            ),
            statement,
        )
        == []
    )


def _stored_cluster(index: int) -> ClaimCluster:
    """One already-persisted cluster, for a state that arrives with a registry."""
    claim_id = f"stored-{index}"
    atom = extract_text_atoms(
        f"The {1980 + index} interconnection queue held {10 + index} GW of "
        "capacity.",
        claim_id=claim_id,
    )[0]
    return cluster_for_atom(atom, claim_id=claim_id, created_seq=index + 1)


@pytest.mark.asyncio
async def test_characterization_a_full_registry_starves_this_passes_new_atom(
    tracker: Tracker,
) -> None:
    """CHARACTERIZATION, not a fix: this pins the behaviour that exists today.

    ``consolidate_claims`` lists every stored cluster's proposition ahead of
    this pass's own atoms, and ``equivalence_messages`` truncates that list to
    ``MAX_EQUIVALENCE_ATOMS``. Once the registry holds that many rows, this
    pass's new atom is never shown to the provider and can never merge with
    anything. Changing the ordering or the bound is a design change with its
    own adversarial cases, so this test records the starvation rather than
    removing it; the atom itself still publishes, under-merged.
    """
    stored = [_stored_cluster(index) for index in range(MAX_EQUIVALENCE_ATOMS)]
    assert len({cluster.cluster_id for cluster in stored}) == MAX_EQUIVALENCE_ATOMS
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, QUEUE_TEXTS[0])),
            _verdict_draft(verdict="verified"),
            _proposal(),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _queue_state((QUEUE_URL_A, QUEUE_TEXTS[0])).model_copy(
        update={"claim_clusters": {c.cluster_id: c for c in stored}}
    )

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    # The bound is a ceiling on what ONE call lists, not on how many atoms are
    # considered: 40 stored atoms plus this pass's one still makes one call.
    assert [name for name, _, _ in completer.calls] == [
        "ClaimsDraft",
        "PassageVerdictDraft",
        "ClaimEquivalenceDraft",
    ]
    request = "\n".join(message.content for message in completer.calls[-1][2])
    new_atom = extract_text_atoms(QUEUE_TEXTS[0], claim_id="new")[0]
    assert new_atom.text not in request
    listed = [
        line for line in request.splitlines() if re.match(r"^\d+\. ", line)
    ]
    assert len(listed) == MAX_EQUIVALENCE_ATOMS
    assert listed[0].startswith(f"1. {stored[0].proposition.text}")
    assert listed[-1].startswith(
        f"{MAX_EQUIVALENCE_ATOMS}. {stored[-1].proposition.text}"
    )
    # Starved, and still published: the atom is its own cluster rather than a
    # row that merged with anything.
    published = outcome.state_update["verified_claims"]
    assert len(published) == 1
    registry = outcome.state_update["claim_clusters"]
    new_cluster_id = published[0].cluster_id
    assert new_cluster_id == claim_cluster_id(new_atom)
    assert new_cluster_id in registry
    assert registry[new_cluster_id].proposition.text == new_atom.text
    assert registry[new_cluster_id].member_claim_ids == [published[0].claim_id]
    # The stored rows come back as themselves: nothing was merged into them
    # and nothing was dropped.
    assert len(registry) == MAX_EQUIVALENCE_ATOMS + 1
    assert set(registry) == {c.cluster_id for c in stored} | {new_cluster_id}


def _prior_paraphrase_cluster(index: int) -> ClaimCluster:
    """One stored cluster for a paraphrase of the same 2024 fact."""
    claim_id = f"prior-{index}"
    atom = extract_text_atoms(QUEUE_TEXTS[index], claim_id=claim_id)[0]
    return cluster_for_atom(atom, claim_id=claim_id, created_seq=index + 1)


@pytest.mark.asyncio
async def test_two_stored_clusters_that_merge_leave_the_absorbed_row_behind(
    tracker: Tracker,
) -> None:
    """R4, deferred: the registry has no delete path, so the row stays.

    Two clusters that were ALREADY stored merge this pass. The absorbed id
    becomes an alias on the survivor, but the earlier pass's claims are not in
    this pass's ``drafts`` and are not restamped, and
    ``merge_claim_cluster_registry`` only ever unions — so the absorbed id
    stays as a stale key, and ``_resolve_stored_cluster`` cannot reach it from
    the survivor. Compacting it means changing the reducer contract, which is
    a separate task's design decision.
    """
    stored = [_prior_paraphrase_cluster(0), _prior_paraphrase_cluster(1)]
    survivor_id, absorbed_id = (cluster.cluster_id for cluster in stored)
    assert survivor_id != absorbed_id

    new_text = "Queue capacity rose 30 percent in 2025."
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, new_text)),
            _verdict_draft(verdict="verified"),
            _proposal((1, 2)),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _check_state(
        [_queue_finding(QUEUE_URL_A, new_text)], sources=[_scored(QUEUE_URL_A)]
    ).model_copy(update={"claim_clusters": {c.cluster_id: c for c in stored}})

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    merged = merge_research_state(state, outcome.state_update)
    registry = merged.claim_clusters
    published = list(merged.verified_claims)
    assert len(published) == 1
    new_cluster_id = published[0].cluster_id
    # The two stored clusters are one fact now, and the merge kept the older
    # identity — but the absorbed row is still a row, beside this pass's own
    # new cluster.
    assert sorted(registry) == sorted(
        [survivor_id, absorbed_id, new_cluster_id]
    )
    assert registry[survivor_id].cluster_aliases == [absorbed_id]
    assert registry[absorbed_id].cluster_id == absorbed_id
    # The absorbed id DOES resolve from the survivor — that half is why the
    # merge loses no resolution.
    assert _resolve_stored_cluster(absorbed_id, [registry[survivor_id]]) is (
        registry[survivor_id]
    )
    # The stale row is the half that does not: it carries neither the
    # survivor's own id nor an alias to it, so the reducer can never resolve
    # this row and can never replace it. There is no delete path.
    assert _resolve_stored_cluster(survivor_id, [registry[absorbed_id]]) is None
    # What is NOT lost: the stored member claims still resolve through the
    # survivor's own ``member_claim_ids``.
    assert set(registry[survivor_id].member_claim_ids) >= {
        "prior-0",
        "prior-1",
    }


# ---------------------------------------------------------------------------
# Wave A: the false-verdict paths
#
# Every test here goes through ``validate_adjudication`` (the public verdict
# path) over a real packet, or through ``adjudication_messages`` plus the
# packet the validator reads. The claim is "Wind capacity reached 10 GW in
# 2025."; two candidates support it independently and the third disagrees.
# ---------------------------------------------------------------------------

SUPPORT_TEXT = "The operator reported 10 GW in 2025."
AUDIT_TEXT = "An audit confirms 10 GW in 2025."
REFUTATION_TEXT = "A separate review states capacity was 4 GW in 2025."
OFF_SCOPE_TEXT = "Capacity was 4 GW in 2023."


def _row(
    evidence_id: str,
    stance: str,
    *,
    complete: bool = True,
    scope: bool = True,
    dependence: str = "primary",
) -> SupportAssessment:
    return SupportAssessment(
        evidence_id=evidence_id,
        stance=stance,
        complete_support=complete,
        scope_compatible=scope,
        dependence=dependence,
    )


def _verdict_packet(
    *eligibilities: EvidenceEligibility,
    texts: tuple[str, ...] = (SUPPORT_TEXT, AUDIT_TEXT, REFUTATION_TEXT),
    omitted: tuple[object, ...] = (),
) -> AdjudicationPacket:
    """A packet of named candidates with the identity each one carries."""
    names = ("left", "right", "third")
    units = [
        _pair_unit(name, f"https://{name}.test/{name}", text)
        for name, text in zip(names, texts)
    ]
    return AdjudicationPacket(
        claim_id="claim-1",
        claim_text=TASK6_CLAIM,
        claim_source_urls=["https://left.test/left"],
        claim_cluster_id="cluster-1",
        units=units,
        eligibility={
            unit.evidence_id: eligibility
            for unit, eligibility in zip(units, eligibilities)
        },
        omitted=list(omitted),
        omitted_count=len(omitted),
        fingerprint="fingerprint-1",
    )


def _third_origin() -> EvidenceEligibility:
    return _eligibility(
        publisher_id="publisher-three",
        work_id="sha256:three",
        origin_group_id="publisher:publisher-three",
    )


def _two_supports_and_a_refutation(**override: object) -> ClaimVerdictDraft:
    return ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            _row("ev-left", "supports"),
            _row("ev-right", "supports"),
            _row("ev-third", "contradicts", **override),
        ],
        support_ids=["ev-left", "ev-right"],
        contradiction_ids=["ev-third"],
        rationale="Two independent reports state the same figure.",
    )


def test_a_same_scope_refutation_blocks_verified_without_complete_support() -> None:
    """``complete_support`` answers about support, and a refutation is not one.

    The model was told ``complete_support`` means "supports the WHOLE atomic
    claim" — a passage that *refutes* the claim answers no by definition, and
    the field also defaults to ``False``. Reading materiality from it let a
    same-scope refutation be filed as "not comparable" and the claim still
    settle as verified.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )

    claim = validate_adjudication(
        _two_supports_and_a_refutation(complete=False, scope=True), packet, None
    )

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "unresolved"
    assert conflict.material is True
    assert conflict.same_scope is True


def test_a_selected_contradiction_with_no_assessment_blocks_verified() -> None:
    """A selection without an assessment is a disagreement, not an absence."""
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={
            "assessments": [
                _row("ev-left", "supports"),
                _row("ev-right", "supports"),
            ]
        }
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "unresolved"
    assert conflict.material is True
    assert "ev-third" in conflict.evidence_ids


def test_an_assessed_refutation_the_model_did_not_select_blocks_verified() -> None:
    """A row that refutes is a contradiction whether or not it was listed.

    The model assessed the passage as contradicting and then left it out of
    ``contradiction_ids``; discarding it wrote ``verified`` over a packet that
    contained a refutation.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={"contradiction_ids": []}
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    (conflict,) = claim.conflict_assessments
    assert conflict.material is True
    assert conflict.resolution == "unresolved"


@pytest.mark.parametrize("stance", ["refutes", "disputes", "partial", ""])
def test_a_stance_the_contract_cannot_place_blocks_without_asserting(stance: str) -> (
    None
):
    """The vocabulary is closed, and what falls outside it is a gap.

    A stance this contract cannot place is not "supports", so the claim cannot
    settle over that passage — but neither is it ``contradicts``: the model
    never said the passage is incompatible, and publishing ``contradicted``
    over a guess fails every support policy and dominates every cluster the
    claim joins.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={
            "contradiction_ids": [],
            "assessments": [
                _row("ev-left", "supports"),
                _row("ev-right", "supports"),
                _row("ev-third", stance),
            ],
        }
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.verdict != "contradicted"
    assert claim.contradictions == []
    assert any(
        "ev-third" in conflict.evidence_ids
        and conflict.material
        and conflict.resolution == "unresolved"
        for conflict in claim.conflict_assessments
    )


def test_a_duplicate_assessment_row_makes_its_id_unusable() -> None:
    """One passage, two judgements: the conservative reading wins, and the id
    cannot then be half of a verified pair."""
    packet = _verdict_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            _row("ev-left", "supports"),
            _row("ev-left", "contradicts", complete=False),
            _row("ev-right", "supports"),
        ],
        support_ids=["ev-left", "ev-right"],
        contradiction_ids=["ev-left"],
        rationale="x",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    assert "ev-left" in claim.refused_evidence_ids
    assert claim.evidence_selection.get("ev-left") != "supports"
    assert "model_disagreement" in claim.audit_flags


def test_an_off_scope_contradiction_does_not_force_contradicted() -> None:
    """A different period is not a refutation of this claim.

    Nothing supports the claim and one passage disagrees about another period:
    the honest verdict is that the claim is not settled, not that it is false.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), texts=(OFF_SCOPE_TEXT, SUPPORT_TEXT, AUDIT_TEXT)
    )
    draft = ClaimVerdictDraft(
        verdict="insufficient_evidence",
        confidence=0.5,
        assessments=[_row("ev-left", "contradicts", complete=True, scope=False)],
        support_ids=[],
        contradiction_ids=["ev-left"],
        rationale="A different period.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status != "contested"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "resolved"
    assert conflict.material is False
    assert claim.insufficient_reason


def test_the_request_shows_every_candidate_and_omits_only_what_it_cannot() -> None:
    """A budget bounds the text, not the candidate list.

    Two long supporting pages used to consume the whole budget by themselves,
    so the candidates behind them were never shown to the model and never
    recorded as omitted — while the packet still counted them as input, and a
    contradiction or a second pair member could sit unread behind the cut.
    """
    long_support = SUPPORT_TEXT + " " + ("Detail. " * 400)
    long_audit = AUDIT_TEXT + " " + ("Detail. " * 300)
    packet = _verdict_packet(
        _eligibility(),
        _independent_second(),
        _third_origin(),
        texts=(long_support, long_audit, REFUTATION_TEXT),
    )

    body = "\n".join(
        message.content
        for message in adjudication_messages(
            packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
        )
    )

    # Every candidate is shown, each within a bounded excerpt.
    for evidence_id in ("ev-left", "ev-right", "ev-third"):
        assert f"id: {evidence_id}" in body
    assert len(body) < FACT_CHECK_EVIDENCE_CHARS * 3

    # Under a budget nothing can fit in, what is left out is recorded, never
    # silently dropped.
    tiny = with_render_boundaries(packet, evidence_chars=200)
    assert tiny.omitted_count == len(tiny.omitted) > 0
    assert {item.item_id for item in tiny.omitted} == {
        "ev-right",
        "ev-third",
    }
    assert _packet_has_pair(
        tiny, shown=[unit.evidence_id for unit in tiny.units[:1]]
    ) is False


def test_the_adjudication_request_keeps_the_pair_and_the_refutation_together() -> None:
    """The suffciency test reads the candidates the request can actually show.

    A pair the budget cannot carry is not a pair the model can certify, so
    retrieval must not be skipped on the strength of it.
    """
    long_left = SUPPORT_TEXT + " " + ("Detail. " * 900)
    packet = _verdict_packet(
        _eligibility(),
        _independent_second(),
        _third_origin(),
        texts=(long_left, AUDIT_TEXT, REFUTATION_TEXT),
    )

    assert _packet_has_pair(packet) is True
    shown = [
        unit.evidence_id
        for unit in with_render_boundaries(
            packet, evidence_chars=200
        ).units[:1]
    ]
    assert _packet_has_pair(packet, shown=shown) is False


@pytest.mark.asyncio
async def test_a_candidate_the_request_cannot_carry_is_recorded_in_the_audit(
    tracker: Tracker,
) -> None:
    """The wiring: request, verdict, and boundary audit describe one packet.

    A budget that cannot carry the whole packet must say so where an audit
    reads it. Otherwise the packet's input ids list candidates as judged while
    the request never carried them — which is how a refutation sat unread
    behind two rendered supports.
    """
    third_url = "https://lab-c.test/rival"
    third_read = _ab_read(
        "read-third", third_url, "Lab C measurement", RIVAL_TEXT + " " + ("Detail. " * 100)
    )
    third = build_evidence_unit(
        read=third_read,
        locator="chunk-0",
        excerpt=third_read.passages["chunk-0"],
        origin="researcher",
        target_ids=[TASK6_TARGET],
    )
    base = _ab_state()
    state = base.model_copy(
        update={
            "read_records": {**base.read_records, third_read.read_id: third_read},
            "evidence_units": {**base.evidence_units, third.evidence_id: third},
        }
    )
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])]
            ),
            _select_every_shown_id,
        ]
    )
    agent = _checker(tracker, completer)
    # Enough for the two pair candidates, not for the long third passage.
    agent._evidence_chars = 600

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    shown = _shown_ids([ChatMessage(role="user", content=_adjudication_body(completer))])
    assert set(shown) == {
        unit.evidence_id for unit in base.evidence_units.values()
    }
    assert third.evidence_id not in shown
    # The candidate left out is an explicit omission, not a silent absence:
    # the request never carried it, the claim says the packet was incomplete,
    # and the boundary audit names it.
    (audit,) = agent._adjudication_audits.values()
    assert audit.disposition_ids == [
        f"{third.evidence_id}:deferred_capacity"
    ]
    assert outcome.result is not None
    (claim,) = outcome.result.claims
    # And the claim cannot settle over a candidate the model never saw: the
    # pair was shown and selected, and the badge is still withheld.
    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status != "verified_pair"
    assert "packet_incomplete" in claim.audit_flags
    assert claim.insufficient_reason


def test_the_adjudication_request_asks_for_the_packet_contract() -> None:
    """The packet request must describe the shape the packet validator reads.

    It carried the legacy instruction, which asks for a ``passages`` list that
    ``ClaimVerdictDraft`` (``extra="forbid"``) rejects — so the request asked
    for exactly what the schema refuses — and which never said what
    ``complete_support`` or ``scope_compatible`` mean, although the local
    verdict and conflict rules turn on them.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )

    body = "\n".join(
        message.content
        for message in adjudication_messages(packet, evidence_chars=4000)
    )

    # Not the legacy passage contract.
    assert "a passages list" not in body
    assert "source_url" not in body
    # The packet's own shape, and what each field decides.
    for expected in (
        "assessments",
        "support_ids",
        "contradiction_ids",
        "evidence_id",
        "stance",
        "complete_support",
        "scope_compatible",
        "dependence",
        "origin_group_id",
    ):
        assert expected in body, expected
    assert "supports the WHOLE" in body
    assert "refutes the claim is contradicts" in body


@pytest.mark.asyncio
async def test_a_refused_url_is_requested_once_in_a_fact_checker_pass(
    tracker: Tracker,
) -> None:
    """A refusal belongs to the pass, not to the claim that met it.

    Audit finding #10 (C15): ``utilitydive/…/750338`` was refused by the
    publisher and re-requested by four different claims, the EIA form page
    three times — 9 of the run's 10 scraper failures, every one of them a
    repeat of a refusal an earlier claim had already recorded. A URL this pass
    could not read is not requested again for another claim in the same pass,
    and the loop is told why.
    """
    refused_url = "https://refused.test/blocked"
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        attempted.append(str(request.url))
        return httpx.Response(403, text="publisher refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Read the publisher's page.",
                "web_scraper",
                json.dumps({"url": refused_url}),
            ),
            finish("Nothing usable was retrieved.", "no evidence"),
            use_tool(
                "Read the same page for the second claim.",
                "web_scraper",
                json.dumps({"url": refused_url}),
            ),
            finish("Nothing usable was retrieved.", "no evidence"),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL]),
                    ClaimDraft(
                        text="Wind capacity reached 11 GW in 2025.",
                        source_urls=[B_URL],
                    ),
                ]
            ),
            _select_every_shown_id,
            _select_every_shown_id,
            ClaimEquivalenceDraft(pairs=[]),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(tracker, http=client),
    )

    outcome = await _task6_run(agent, _ab_state(score_b=False), tracker)

    assert attempted == [refused_url]
    refusals = [
        error
        for error in outcome.errors
        if error.error_type == "agent_tool_policy_rejected"
    ]
    assert [error.details.get("tool") for error in refusals] == ["web_scraper"]
    # Both claims were still adjudicated: a refusal costs the claim its
    # retrieval, never its verdict.
    assert outcome.result is not None
    assert len(outcome.result.claims) == 2


@pytest.mark.asyncio
async def test_a_deferral_from_an_earlier_claim_is_not_this_packets_omission(
    tracker: Tracker,
) -> None:
    """The run's deferrals belong to the run, not to every later packet.

    ``_augment_packet`` rebuilt each packet with the instance-wide
    ``_new_dispositions`` list, which accumulates every earlier claim's
    read-selection deferrals — so claim two's packet carried claim one's
    omissions, and at claim five every packet looked incomplete.
    """
    from deep_research.utils.types import EvidenceDisposition

    state = _ab_state()
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])
    agent = _packet_agent(state)
    earlier = EvidenceDisposition(
        item_id="read-from-an-earlier-claim",
        stage="read-selection",
        reason="deferred_capacity",
        target_ids=["topic-earlier"],
    )
    agent._new_dispositions = [earlier]
    agent._new_evidence = {}
    agent._new_reads = {}
    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )
    assert packet is not None
    task = ClaimTask(
        instruction="Verify the claim.",
        claim=draft,
        packet=packet,
        target_ids=[TASK6_TARGET],
    )

    enlarged = await agent._augment_packet(
        packet,
        ReActRun(agent_name="fact_checker", stop_reason="finished"),
        task,
    )

    assert enlarged is not None
    assert all(item.item_id != earlier.item_id for item in enlarged.omitted)
    # The run still records it: the disposition is the run's diagnostic.
    assert agent._new_dispositions == [earlier]


def test_a_claim_loop_resumes_the_stored_state_of_its_own_sub_topic() -> None:
    """The stored queue is keyed by coverage id; a claim carries a target id.

    ``acquisition_state_by_target`` is the Researcher's, keyed by the
    sub-topic's ``coverage_id`` — one acquisition loop runs per sub-topic —
    while a claim's obligation carries the namespaced target id. Looking the
    queue up by that id found nothing, so the resume context
    ``build_decision_context`` exists to render was empty in every production
    run: the loop never saw the candidates the run had already queued for its
    own sub-topic, nor the URLs it had already attempted.
    """
    state = _ab_state()
    agent = _packet_agent(state)
    agent._config = AgentRuntimeConfig(
        max_iterations=3, tool_budget=3, tool_budget_overrides={"fact_checker": 2}
    )
    agent._run_acquisition_state = {
        "topic-01": AcquisitionState(
            target_id="topic-01",
            candidate_urls=["https://lab-c.test/queued"],
            attempted_urls=[A_URL],
        )
    }
    task = ClaimTask(
        instruction="Verify the claim.",
        claim=ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL]),
        target_ids=[TASK6_TARGET],
    )

    context = agent.build_decision_context(task, iteration=1, steps=[])

    assert "https://lab-c.test/queued" in context
    assert A_URL in context


@pytest.mark.asyncio
async def test_a_contradicted_member_stamps_the_clusters_stored_claim(
    tracker: Tracker,
) -> None:
    """A cluster publishes one verdict, and every member carries it.

    Pass 1 verified "The 2020 interconnection queue held 10 GW of capacity."
    Pass 2 adjudicates a paraphrase of it as contradicted, and consolidation
    merges the two into one cluster whose resolved verdict is the
    contradiction. The stored, first-pass claim was left with its own verified
    badge, so a reader was shown the contradicted fact as independently
    corroborated — the exact laundering ``resolved_verdict`` exists to stop.
    """
    text = "The 1980 interconnection queue held 10 GW of capacity."
    stored_cluster = _stored_cluster(0)
    atom = extract_text_atoms(text, claim_id="stored-0")[0]
    assert atom.text == stored_cluster.proposition.text
    stored = Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[QUEUE_URL_A],
        verdict="verified",
        confidence=0.9,
        evidence=["10 GW of capacity was queued."],
        contradictions=[],
        verification_evidence=[],
        evidence_status="verified_pair",
        cluster_id=stored_cluster.cluster_id,
    )
    completer = ScriptedCompleter(
        decisions=_check_decisions(),
        outputs=[
            _queue_draft((QUEUE_URL_A, "In 1980, the interconnection queue held 10 GW of capacity.")),
            _verdict_draft(
                verdict="contradicted",
                contradictions=["The queue held 4 GW, not 10 GW."],
            ),
            _proposal((1, 2)),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _queue_state(
        (QUEUE_URL_A, "In 1980, the interconnection queue held 10 GW of capacity.")
    ).model_copy(
        update={
            "verified_claims": [stored],
            "claim_clusters": {stored_cluster.cluster_id: stored_cluster},
        }
    )

    async with tracker.session_span(state.session_id, state.original_question):
        outcome = await agent.run(state)

    merged = merge_research_state(state, outcome.state_update)
    published = list(merged.verified_claims)
    # Both members of the cluster are published as the cluster resolved them.
    assert len(published) == 2
    assert {claim.cluster_id for claim in published} == {
        stored_cluster.cluster_id
    }
    assert {claim.verdict for claim in published} == {"contradicted"}
    assert {claim.evidence_status for claim in published} == {"contested"}
    # And the strict-pair policy is no longer satisfied by the stored claim.
    assert [
        claim.claim_id
        for claim in published
        if claim.evidence_status == "verified_pair"
    ] == []


def test_the_synthesizer_badges_a_contested_cluster_contested() -> None:
    """The writer is told what the cluster resolved, not its verified slice.

    ``build_canonical_packet`` read ``verdict_evidence["verified"]`` and the
    verified badge for every member of a cluster, so a contradicted member was
    presented to the writer as a verified pair carrying the *verified* member's
    citations — the amplifier on the same laundering.
    """
    text = "The 1980 interconnection queue held 10 GW of capacity."
    atom = extract_text_atoms(text, claim_id="stored-0")[0]
    base = cluster_for_atom(atom, claim_id="stored-0", created_seq=1)
    cluster = base.model_copy(
        update={
            "verdicts": ["contradicted", "verified"],
            "verdict_evidence": {
                "contradicted": ["https://contra.test/b"],
                "verified": ["https://verified.test/a"],
            },
            "verdict_evidence_status": {
                "contradicted": "contested",
                "verified": "verified_pair",
            },
        }
    )
    contradicted = Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=["https://contra.test/b"],
        verdict="contradicted",
        confidence=0.8,
        evidence=[],
        contradictions=["The queue held 4 GW, not 10 GW."],
        verification_evidence=[],
        evidence_status="contested",
        cluster_id=cluster.cluster_id,
    )

    packet = build_canonical_packet(
        claims=[contradicted],
        clusters={cluster.cluster_id: cluster},
        evidence={},
        targets=[],
        sources=[],
        limit=4,
    )

    (entry,) = packet.entries
    assert entry.evidence_status == "contested"
    assert entry.evidence_label == EVIDENCE_BADGE_LABELS["contested"]
    assert "https://verified.test/a" not in entry.citation_urls


def test_not_comparable_is_still_loadable_but_no_longer_produced() -> None:
    """The resolution vocabulary keeps a value the producer stopped choosing.

    An in-scope refutation is material and a scope difference resolves, so no
    adjudication writes ``not_comparable`` any more — but a record written by
    an earlier release carries it, and dropping the value would make a stored
    conflict row unloadable. Kept for loading, never produced.
    """
    claim = Claim(
        claim_id="claim-1",
        text="Wind capacity reached 10 GW in 2025.",
        source_urls=["https://left.test/left"],
        verdict="contradicted",
        confidence=0.9,
        evidence=[],
        contradictions=["Capacity was 4 GW in 2025."],
        verification_evidence=[],
        evidence_status="contested",
        conflict_assessments=[
            ConflictAssessment(
                claim_cluster_id="cluster-1",
                evidence_ids=["ev-left", "ev-right"],
                same_scope=False,
                material=False,
                resolution="not_comparable",
                rationale="Recorded by an earlier release.",
            )
        ],
    )

    assert claim.conflict_assessments[0].resolution == "not_comparable"
    # And nothing this release writes it: an unassessed refutation is
    # unresolved and material, an off-scope one is resolved.
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    for draft in (
        _two_supports_and_a_refutation().model_copy(
            update={"assessments": [_row("ev-left", "supports")]}
        ),
        _two_supports_and_a_refutation(),
    ):
        claim = validate_adjudication(draft, packet, None)
        assert all(
            row.resolution != "not_comparable"
            for row in claim.conflict_assessments
        )


def test_a_never_carried_candidate_named_as_a_contradiction_is_refused() -> None:
    """The id classes the validator refuses are refused in every role.

    A support selection for an unrendered id was refused, but a *contradiction*
    selection was not filtered by what the request carried, so the same id
    reached the passage list — which indexes the shown candidates — and raised
    instead of refusing.
    """
    long_left = SUPPORT_TEXT + " " + ("Detail. " * 200)
    packet = with_render_boundaries(
        _verdict_packet(
            _eligibility(),
            _independent_second(),
            _third_origin(),
            texts=(long_left, AUDIT_TEXT, REFUTATION_TEXT),
        ),
        evidence_chars=300,
    )
    unshown = "ev-right"
    draft = ClaimVerdictDraft(
        verdict="insufficient_evidence",
        confidence=0.5,
        assessments=[_row("ev-left", "supports")],
        support_ids=["ev-left"],
        contradiction_ids=[unshown],
        rationale="Naming a contradiction the request never carried.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert unshown in claim.refused_evidence_ids
    assert "evidence_not_admitted" in claim.audit_flags
    assert unshown not in claim.evidence_selection
    assert claim.verdict != "contradicted"


def test_a_saturated_omission_list_still_records_an_unrendered_candidate() -> None:
    """The record that a candidate was not carried cannot be the part dropped.

    ``packet.omitted`` saturates in a multi-claim run — one ``out_of_scope``
    entry per registry unit outside this claim's pool, itself capped — so
    appending the unrendered candidates behind those and truncating the list
    discarded exactly the entries that say the request did not carry them. The
    same model output was then either a crash or a false ``verified`` depending
    on how many other claims' units the registry happened to hold.
    """
    from deep_research.utils.types import EvidenceDisposition

    saturated = tuple(
        EvidenceDisposition(
            item_id=f"ev-other-{index}",
            stage="adjudication-packet",
            reason="out_of_scope",
            target_ids=["t2"],
        )
        for index in range(MAX_PACKET_OMISSIONS)
    )
    long_left = SUPPORT_TEXT + " " + ("Detail. " * 200)
    packet = with_render_boundaries(
        _verdict_packet(
            _eligibility(),
            _independent_second(),
            _third_origin(),
            texts=(long_left, AUDIT_TEXT, REFUTATION_TEXT),
            omitted=saturated,
        ),
        evidence_chars=300,
    )
    unshown = "ev-right"

    assert unshown in unshown_candidates(packet)
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[_row("ev-left", "supports"), _row(unshown, "supports")],
        support_ids=["ev-left", unshown],
        contradiction_ids=[],
        rationale="Naming an id the request never carried.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    assert unshown in claim.refused_evidence_ids
    assert unshown not in claim.evidence_selection


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_row("ev-left", "supports"), _row("ev-left", "supports", complete=False)),
        (_row("ev-left", "supports", complete=False), _row("ev-left", "supports")),
        (_row("ev-left", "supports"), _row("ev-left", "unrelated")),
    ],
)
def test_duplicate_rows_keep_the_least_creditable_judgement(
    first: SupportAssessment, second: SupportAssessment
) -> None:
    """The model's row order may not decide how much credit a passage gets.

    Keeping the first row unless the second refuted meant the most permissive
    judgement survived: a passage judged both a complete support and a partial
    one verified the claim in one row order and settled as insufficient in the
    other, and its id was published as a pair member while also being listed as
    refused.
    """
    packet = _verdict_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[first, second, _row("ev-right", "supports")],
        support_ids=["ev-left", "ev-right"],
        contradiction_ids=[],
        rationale="One passage, two judgements.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    assert "ev-left" in claim.refused_evidence_ids
    assert claim.evidence_selection.get("ev-left") != "supports"


def test_an_unclear_stance_blocks_settlement_without_asserting_contradiction() -> (
    None
):
    """A stance outside the vocabulary is a gap, not a refutation.

    ``partial`` is not ``contradicts``: the model did not state that the
    passage disagrees, so publishing a ``contradicted`` verdict asserts
    incompatibility it never claimed — and a published contradiction fails
    every support policy and dominates every cluster it joins. What it does do
    is block settlement, because a passage nobody could classify cannot be
    read as absent.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={
            "contradiction_ids": [],
            "assessments": [
                _row("ev-left", "supports"),
                _row("ev-right", "supports"),
                _row("ev-third", "partial", scope=True),
            ],
        }
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status != "contested"
    assert claim.contradictions == []
    (conflict,) = claim.conflict_assessments
    assert conflict.material is True
    assert conflict.resolution == "unresolved"


@pytest.mark.parametrize("stance", ["contradicts", "partial"])
def test_a_refutation_with_no_stated_scope_blocks_settlement(stance: str) -> None:
    """An unstated scope is not a claim that the scope differs.

    ``scope_compatible`` defaulted to False, so a passage the model called
    incompatible — or could not classify — while saying nothing about the
    period was read as "a different period" and *resolved*, which dismissed the
    conflict and let the claim settle as verified. Only an explicit ``false``
    is a scope difference; silence is an open question.
    """
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={
            "contradiction_ids": [],
            "assessments": [
                _row("ev-left", "supports"),
                _row("ev-right", "supports"),
                SupportAssessment(evidence_id="ev-third", stance=stance),
            ],
        }
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "unresolved"
    assert conflict.material is True


def test_an_explicitly_different_scope_still_resolves() -> None:
    """The control: a stated scope difference is not over-blocked."""
    packet = _verdict_packet(
        _eligibility(), _independent_second(), _third_origin()
    )
    draft = _two_supports_and_a_refutation().model_copy(
        update={
            "assessments": [
                _row("ev-left", "supports"),
                _row("ev-right", "supports"),
                _row("ev-third", "contradicts", complete=True, scope=False),
            ]
        }
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict == "verified"
    (conflict,) = claim.conflict_assessments
    assert conflict.resolution == "resolved"
    assert conflict.material is False


def test_a_support_with_no_stated_scope_is_still_not_admitted() -> None:
    """An unstated scope never admits a support, exactly as before."""
    packet = _verdict_packet(_eligibility(), _independent_second())
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            SupportAssessment(
                evidence_id="ev-left",
                stance="supports",
                complete_support=True,
                dependence="primary",
            ),
            _row("ev-right", "supports"),
        ],
        support_ids=["ev-left", "ev-right"],
        contradiction_ids=[],
        rationale="One support left its scope unstated.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    assert "ev-left" not in claim.evidence_selection


def test_a_crowded_packet_shows_every_passage_it_can_carry_whole() -> None:
    """Every passage the request carries is the passage, not a prefix of it.

    A packet of whole-page candidates used to be clipped to a uniform share
    until all of them fitted, which is how a claim whose figure sat past the
    cut came to be judged over text nobody saw. The request now carries the
    passages it can show whole and records the rest as deferred, so no
    candidate is judged on a fragment and none is silently dropped.
    """
    text = SUPPORT_TEXT + " " + ("Detail. " * 45)
    units = [
        _pair_unit(name, f"https://{name}.test/{name}", text)
        for name in ("left", "right", "third", "fourth", "fifth", "sixth")
    ]
    packet = AdjudicationPacket(
        claim_id="claim-1",
        claim_text=TASK6_CLAIM,
        claim_source_urls=["https://left.test/left"],
        claim_cluster_id="cluster-1",
        units=units,
        eligibility={
            unit.evidence_id: _eligibility(
                publisher_id=f"publisher-{unit.evidence_id}",
                work_id=f"sha256:{unit.evidence_id}",
                origin_group_id=f"publisher:publisher-{unit.evidence_id}",
            )
            for unit in units
        },
        fingerprint="fingerprint-1",
    )

    plan = plan_packet_rendering(
        packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
    )
    body = "\n".join(
        message.content
        for message in adjudication_messages(
            packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
        )
    )

    # A passage-sized candidate is carried whole, and the plan says so.
    assert plan.unrendered == []
    assert plan.partially_shown == []
    for unit, text_shown in plan.rendered:
        assert text_shown == " ".join(unit.excerpt.split())
    for unit in units:
        assert f"id: {unit.evidence_id}" in body
    assert (
        len(body.split("# Claim")[1].split("# Response contract")[0])
        <= FACT_CHECK_EVIDENCE_CHARS * 2
    )


def test_a_candidate_the_request_cannot_carry_whole_is_deferred_or_marked() -> None:
    """Whole-page candidates no longer fit, so the request says what it did.

    One page-sized candidate is longer than a request may carry for a single
    candidate: it is shown in part and *recorded* as partially shown, so a
    verdict may not read it as a complete passage. The candidates behind it
    that no longer fit are omitted and deferred by name.
    """
    page_sized = SUPPORT_TEXT + " " + ("Detail. " * 400)
    packet = _verdict_packet(
        _eligibility(),
        _independent_second(),
        _third_origin(),
        texts=(page_sized, AUDIT_TEXT, REFUTATION_TEXT),
    )

    plan = plan_packet_rendering(
        packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
    )

    assert len(plan.rendered) + len(plan.unrendered) == len(packet.units)
    assert plan.partially_shown == ["ev-left"]

    bounded = with_render_boundaries(
        packet, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
    )
    assert set(bounded.partially_shown_ids) == {"ev-left"}
    body = "\n".join(
        message.content
        for message in adjudication_messages(
            bounded, evidence_chars=FACT_CHECK_EVIDENCE_CHARS
        )
    )
    for unit in packet.units:
        assert f"id: {unit.evidence_id}" in body


def test_packet_incomplete_is_about_this_claims_own_candidates() -> None:
    """Another claim's unit, and a run-wide deferral, are not this claim's gaps.

    ``packet_incomplete`` used to fire for every omission the packet carried,
    which includes every registry unit that belongs to a different claim and
    every read-selection deferral the run accumulated earlier — so in any run
    with more than one claim the flag replaced the real reason and the ledger
    printed ``packet_incomplete`` where ``single_primary_only`` was the answer.
    """
    from deep_research.utils.types import EvidenceDisposition

    packet = _verdict_packet(
        _eligibility(),
        _independent_second(),
        omitted=(
            EvidenceDisposition(
                item_id="ev-another-claim",
                stage="adjudication-packet",
                reason="out_of_scope",
                target_ids=["t2"],
            ),
            EvidenceDisposition(
                item_id="read-deferred-earlier",
                stage="read-selection",
                reason="deferred_capacity",
                target_ids=["t9"],
            ),
        ),
    )
    draft = ClaimVerdictDraft(
        verdict="insufficient_evidence",
        confidence=0.6,
        assessments=[_row("ev-left", "supports")],
        support_ids=["ev-left"],
        contradiction_ids=[],
        rationale="One source states it.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert "packet_incomplete" not in claim.audit_flags
    assert claim.insufficient_reason == "single_primary_only"


def test_a_sub_topic_scoped_read_is_reported_as_a_handoff_loss() -> None:
    """The scope the pool uses is the scope the loss is measured in.

    The Researcher registers a read against the sub-topic it was taken for
    (``topic-01``) while a claim's obligations are evidence targets
    (``topic-01-target-01``). Comparing the two id spaces directly reported no
    loss at all, so a read whose passage never reached the packet read as
    "nothing supports this" instead of as the handoff that lost it.
    """
    state = _obligation_state()
    lost = _ab_read(
        "read-lost", "https://lab-e.test/lost", "Lab E report", A_TEXT
    ).model_copy(update={"target_ids": ["topic-01"]})
    state = state.model_copy(
        update={"read_records": {**state.read_records, lost.read_id: lost}}
    )
    draft = ClaimDraft(
        text="Wind capacity reached 10 GW in 2025.", source_urls=[A_URL]
    )

    pool = claim_evidence_pool(
        state, draft, target_ids=["topic-01-target-01"]
    )
    missing = claim_missing_read_ids(
        state.read_records,
        draft,
        pool,
        target_ids=["topic-01-target-01"],
        sub_topics=list(state.sub_topics),
    )

    assert lost.read_id in missing
    # The control: another topic's read is still not this claim's loss.
    assert "read-elsewhere" not in missing
    packet, _ = FactCheckerAgent._packet_for(
        _packet_agent(state),
        state,
        draft,
        target_ids=["topic-01-target-01"],
    )
    assert packet is not None
    assert lost.read_id in packet.missing_read_ids
    claim = validate_adjudication(
        ClaimVerdictDraft(
            verdict="insufficient_evidence",
            confidence=0.6,
            assessments=[
                _row(unit.evidence_id, "supports") for unit in pool[:1]
            ],
            support_ids=[unit.evidence_id for unit in pool[:1]],
            contradiction_ids=[],
            rationale="One source states it.",
        ),
        packet,
        None,
    )
    assert "handoff_loss" in claim.audit_flags
    assert claim.insufficient_reason == "handoff_loss"


def test_the_request_carries_the_passages_that_bear_on_the_claim() -> None:
    """The candidates that share the claim's words are the ones offered first.

    The request shows what it can carry whole, so pool order decides what the
    model reads. Claim 4 of the audited run was shown a page's opening while
    the sentence carrying its 18.2 GW figure sat in the same packet, deferred
    behind candidates that happened to be admitted earlier. The pool is now
    ordered by how much each passage bears on the claim itself, and the
    passages that bear least are the ones recorded as deferred.
    """
    state = _ab_state()
    noise_text = "Detail. " * 120
    noise_read = _ab_read(
        "read-noise", "https://lab-noise.test/notes", "Lab notes", noise_text
    )
    noise_unit = build_evidence_unit(
        read=noise_read,
        locator="chunk-0",
        excerpt=noise_text,
        origin="researcher",
        target_ids=[TASK6_TARGET],
    )
    # Registry order puts the passage that shares nothing with the claim first,
    # and no candidate is pair-eligible — so nothing but the pool's own order
    # decides which candidates the request offers first.
    state = state.model_copy(
        update={
            "read_records": {
                noise_read.read_id: noise_read,
                **state.read_records,
            },
            "evidence_units": {
                noise_unit.evidence_id: noise_unit,
                **state.evidence_units,
            },
            "evaluated_sources": [],
        }
    )
    agent = _packet_agent(state)
    # Enough for the two passages that state the figure, not for the noise.
    agent._evidence_chars = 600
    draft = ClaimDraft(text=TASK6_CLAIM, source_urls=[A_URL])

    packet, _ = FactCheckerAgent._packet_for(
        agent, state, draft, target_ids=[TASK6_TARGET]
    )

    assert packet is not None
    final = with_render_boundaries(packet, evidence_chars=600)
    body = "\n".join(
        message.content
        for message in adjudication_messages(final, evidence_chars=600)
    )
    assert A_TEXT in body
    assert B_TEXT in body
    assert "Detail. Detail." not in body
    assert any(
        item.item_id == noise_unit.evidence_id
        and item.reason == "deferred_capacity"
        for item in final.omitted
    )


def _packet_agent(state: ResearchState) -> FactCheckerAgent:
    """A checker with exactly what ``_packet_for`` reads."""
    agent = object.__new__(FactCheckerAgent)
    agent._evidence_chars = FACT_CHECK_EVIDENCE_CHARS
    agent._run_reads = dict(state.read_records)
    agent._run_sources = list(state.evaluated_sources)
    agent._sub_topics = list(state.sub_topics)
    return agent


def test_an_id_the_request_never_carried_is_never_admitted() -> None:
    """§2.5/Task 6: the id must be one the model was shown.

    The request fits one pair candidate and not the other, and the model names
    both as supports. Crediting the id it never saw would make a passage the
    model was not shown half of a verified pair — the false-verified vector
    this refusal closes. It is refused exactly as an id that is not in the
    packet at all.
    """
    long_left = SUPPORT_TEXT + " " + ("Detail. " * 200)
    packet = with_render_boundaries(
        _verdict_packet(
            _eligibility(),
            _independent_second(),
            _third_origin(),
            texts=(long_left, AUDIT_TEXT, REFUTATION_TEXT),
        ),
        evidence_chars=300,
    )
    rendered = {
        unit.evidence_id
        for unit, _ in plan_packet_rendering(
            packet, evidence_chars=300
        ).rendered
    }
    unshown = "ev-right"
    assert rendered == {"ev-left"}
    assert unshown not in rendered
    draft = ClaimVerdictDraft(
        verdict="verified",
        confidence=0.9,
        assessments=[
            _row("ev-left", "supports"),
            _row(unshown, "supports"),
        ],
        support_ids=["ev-left", unshown],
        contradiction_ids=[],
        rationale="Naming an id the request never carried.",
    )

    claim = validate_adjudication(draft, packet, None)

    assert claim.verdict != "verified"
    assert claim.evidence_status != "verified_pair"
    assert unshown in claim.refused_evidence_ids
    assert "evidence_not_admitted" in claim.audit_flags
    assert unshown not in claim.evidence_selection


