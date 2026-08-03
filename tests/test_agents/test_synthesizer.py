"""Tests for the Synthesizer's contracts, limitations, and composition."""

from __future__ import annotations

import pytest

from deep_research.agents.report import REPORT_SECTIONS, ReportSection
from deep_research.agents.synthesizer import (
    DEFAULT_MEMORY_CONFIDENCE,
    REPORT_SUMMARY_FALLBACK,
    ReportDraft,
    ReportSectionDraft,
    SynthesisTask,
    build_report_sections,
    compose_report,
    high_confidence_claims,
    limitation_reasons,
    memory_payload,
    render_revision_guidance,
    report_filename,
    report_messages,
)
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
)

SYNTH_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


def _finding(url: str = SOURCE_URL, sub_topic: str = "Alpha") -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=SYNTH_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _source(
    *,
    url: str = SOURCE_URL,
    overall: float = 0.76,
    low_confidence: bool = False,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=overall,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        confidence=confidence,
        evidence=[],
        contradictions=[],
    )


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "raw_findings": [_finding()],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _task(**overrides: object) -> SynthesisTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "session_id": "session-1",
        "iteration": 0,
        "claims": [_claim()],
        "sources": [_source()],
        "findings": [_finding()],
        "limitations": [],
    }
    payload.update(overrides)
    return SynthesisTask.model_validate(payload)


def test_a_clean_pass_records_no_limitations() -> None:
    assert limitation_reasons(_state()) == []


def test_every_weak_signal_becomes_an_enumerated_limitation() -> None:
    state = _state(
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_without_findings",
                source="agent.researcher",
                message="A high-priority sub-topic produced no findings.",
            )
        ],
        iteration=3,
        max_iterations=3,
        evaluated_sources=[_source(overall=0.1, low_confidence=True)],
        verified_claims=[_claim(verdict="contradicted", confidence=0.4)],
    )

    assert limitation_reasons(state) == [
        "errors_recorded",
        "max_iterations_reached",
        "low_confidence_sources",
        "no_verified_claims",
        "contradicted_claims",
    ]


def test_an_unscored_pass_reports_that_source_quality_is_unknown() -> None:
    reasons = limitation_reasons(_state(evaluated_sources=[]))

    assert "no_sources_evaluated" in reasons
    assert "low_confidence_sources" not in reasons


@pytest.mark.parametrize(
    ("session_id", "iteration", "expected"),
    [
        ("session-1", 0, "report-session-1-0.md"),
        ("Session_42", 2, "report-session-42-2.md"),
        ("../../etc/passwd", 1, "report-etc-passwd-1.md"),
        ("   ", 0, "report-session-0.md"),
    ],
)
def test_report_filenames_are_slugged_and_traversal_free(
    session_id: str, iteration: int, expected: str
) -> None:
    assert report_filename(session_id=session_id, iteration=iteration) == expected


def test_report_filename_rejects_a_negative_iteration() -> None:
    with pytest.raises(ValueError, match="iteration"):
        report_filename(session_id="session-1", iteration=-1)


def test_sections_keep_only_source_urls_that_reached_the_evidence() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="Break-even was reached.",
            sections=[
                ReportSectionDraft(
                    title="  Error correction  ",
                    body="Break-even was reached.\n\nScaling is open.",
                    source_urls=[
                        "https://WWW.example.org/a/",
                        "https://invented.test/x",
                        SOURCE_URL,
                    ],
                )
            ],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert [section.title for section in sections] == ["Error correction"]
    assert sections[0].source_urls == [SOURCE_URL]
    assert "\n\n" in sections[0].body
    assert rejected == ["section 1: 1 source url(s) not in evidence"]


def test_a_blank_section_is_dropped_and_named() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="",
            sections=[ReportSectionDraft(title="  ", body="Text.", source_urls=[])],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert sections == []
    assert rejected == ["section 1: blank title or body"]


def test_sections_past_the_cap_are_dropped_and_named() -> None:
    draft = ReportDraft(
        executive_summary="",
        sections=[
            ReportSectionDraft(title=f"S{index}", body="Text.", source_urls=[])
            for index in range(3)
        ],
        uncertainty_notes="",
    )

    sections, rejected = build_report_sections(
        draft, known_urls=[SOURCE_URL], max_sections=2
    )

    assert [section.title for section in sections] == ["S0", "S1"]
    assert rejected == ["section 3: past the section cap"]


def test_build_report_sections_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError, match="max_sections"):
        build_report_sections(
            ReportDraft(executive_summary="", sections=[], uncertainty_notes=""),
            known_urls=[],
            max_sections=0,
        )


def test_only_confident_verified_claims_are_kept_for_memory() -> None:
    claims = [
        _claim(confidence=0.9),
        _claim(text="Weakly verified.", confidence=0.5),
        _claim(text="Unverified.", verdict="unverified", confidence=0.9),
    ]

    kept = high_confidence_claims(claims, threshold=DEFAULT_MEMORY_CONFIDENCE)

    assert [claim.confidence for claim in kept] == [0.9]
    assert kept[0].verdict == "verified"


def test_a_memory_payload_carries_the_claim_and_its_attribution() -> None:
    content, metadata = memory_payload(_claim(), session_id="session-1")

    assert content == "Logical error rates fell below break-even in 2025."
    assert metadata["entry_type"] == "finding"
    assert metadata["session_id"] == "session-1"
    assert metadata["agent_id"] == "synthesizer"
    assert metadata["source_url"] == SOURCE_URL
    assert metadata["confidence"] == pytest.approx(0.8)


def test_revision_guidance_repeats_the_critic_feedback() -> None:
    state = _state(
        critique=Critique(
            score=4,
            gaps=["No cost data."],
            unsupported_claims=["Costs fell tenfold."],
            recommended_queries=["qec cost 2025"],
            should_continue=True,
            rationale="Thin sourcing.",
        )
    )

    guidance = render_revision_guidance(state)

    assert "No cost data." in guidance
    assert "Costs fell tenfold." in guidance
    # Recommended queries are the Researcher's business, not the writer's.
    assert "qec cost 2025" not in guidance
    assert render_revision_guidance(_state()) == ""


def test_a_composed_report_carries_its_counts_and_every_section() -> None:
    report = compose_report(
        _task(),
        summary="Break-even was reached.",
        sections=[
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=[SOURCE_URL],
            )
        ],
        uncertainty_notes="Vendor numbers remain unaudited.",
        limitations=["errors_recorded"],
    )

    for heading in REPORT_SECTIONS:
        assert heading in report.markdown
    assert report.section_count == 1
    assert report.citation_count == 1
    assert report.source_count == 1
    assert report.path is None
    assert report.saved_findings == 0


def test_a_report_composed_without_a_model_still_cites_its_claims() -> None:
    report = compose_report(
        _task(),
        summary=REPORT_SUMMARY_FALLBACK,
        sections=[],
        uncertainty_notes="",
        limitations=["report_generation_failed"],
    )

    assert REPORT_SUMMARY_FALLBACK in report.markdown
    assert "(no findings were reported)" in report.markdown
    assert "[1] (confidence 0.80)" in report.markdown
    assert "The model provider failed while this report was written" in (
        report.markdown
    )


def test_report_messages_carry_every_input_the_writer_needs() -> None:
    messages = report_messages(
        _task(guidance="Close the cost gap.", limitations=["errors_recorded"]),
        finding_digest=10,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "## Research question" in body
    assert "## Context" in body
    assert "Close the cost gap." in body
    assert "## Verified and checked claims" in body
    assert "[verified 0.80]" in body
    assert "## Retrieved findings" in body
    assert "## Source quality" in body
    assert "## Known limitations" in body
    assert "## Response contract" in body


def test_report_messages_drop_the_context_section_without_guidance() -> None:
    body = report_messages(_task(), finding_digest=10, claim_digest=10)[1].content

    assert "## Context" not in body
