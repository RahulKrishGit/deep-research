"""Tests for canonical evidence identities and snapshot merges.

Every helper here is a pure function of its inputs, so these tests need no
provider, tracker, or scratchpad: a fingerprint is asserted directly and a
merge is asserted on ordinary domain records.
"""

from __future__ import annotations

from deep_research.agents.identity import (
    claim_fingerprint,
    deduplicate_findings,
    finding_fingerprint,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.utils.types import Claim, Finding, ScoredSource

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def source(
    *,
    url: str = "https://example.test/a",
    overall_score: float = 0.75,
    low_confidence: bool = False,
    assessment_revision: str = "",
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Example source",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=overall_score,
        rationale="Relevant and independently corroborated.",
        low_confidence=low_confidence,
        assessment_revision=assessment_revision,
    )


def unscored_source(
    *,
    url: str = "https://example.test/a",
    status: str = "unscored_provider",
    assessment_revision: str = "",
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Example source",
        authority_score=None,
        recency_score=None,
        relevance_score=None,
        overall_score=None,
        rationale="The source was not scored in this pass.",
        evaluation_status=status,
        assessment_revision=assessment_revision,
    )


def claim(
    text: str = "Queue capacity fell in 2024.",
    *,
    verdict: str = "verified",
    confidence: float = 0.9,
    contradictions: list[str] | None = None,
) -> Claim:
    return Claim.model_validate(
        {
            "claim_id": claim_fingerprint(text),
            "text": text,
            "source_urls": ["https://example.test/a"],
            "verdict": verdict,
            # The fixture's premise is a claim that carries the strict badge;
            # ``Claim`` refuses a verified verdict without it.
            "evidence_status": (
                "verified_pair"
                if verdict == "verified"
                else "source_supported"
                if verdict == "insufficient_evidence"
                else None
            ),
            "confidence": confidence,
            "evidence": ["The source quotes the annual figure."],
            "contradictions": contradictions or [],
            "verification_evidence": [],
        }
    )


def finding(
    content: str = "Adoption rose.",
    *,
    source_url: str = "https://example.test/a",
    related_sub_topic: str = "Adoption",
    confidence: float = 0.8,
    source_title: str = "Example source",
    target_ids: list[str] | None = None,
    vintage: str | None = None,
    statement_date: str | None = None,
    data_period: str | None = None,
) -> Finding:
    return Finding(
        content=content,
        source_url=source_url,
        source_title=source_title,
        extracted_at=EXTRACTED_AT,
        confidence=confidence,
        related_sub_topic=related_sub_topic,
        target_ids=list(target_ids or ()),
        vintage=vintage,
        statement_date=statement_date,
        data_period=data_period,
    )


# --- claim fingerprints ---------------------------------------------------


def test_claim_fingerprint_collapses_formatting_but_preserves_facts() -> None:
    assert claim_fingerprint("Queue capacity fell in 2024.") == claim_fingerprint(
        "  queue capacity FELL in 2024  "
    )
    assert claim_fingerprint("Queue capacity fell in 2024.") != claim_fingerprint(
        "Queue capacity fell in 2025."
    )


def test_claim_fingerprint_normalizes_unicode_and_punctuation() -> None:
    assert claim_fingerprint("Rates fell 40% in 2024.") == claim_fingerprint(
        "RATES fell 40% in 2024"
    )
    # NFKC: full-width digits and a non-breaking space are formatting only.
    assert claim_fingerprint("Capacity fell in 2024.") == claim_fingerprint(
        "Capacity fell in\u00a0\uff12\uff10\uff12\uff14."
    )


def test_claim_fingerprint_preserves_numbers_units_and_comparisons() -> None:
    baseline = claim_fingerprint("The queue holds 400 ppm.")
    for different in (
        "The queue holds 401 ppm.",
        "The queue holds 400 ppb.",
        "The queue holds more than 400 ppm.",
        "The queue holds less than 400 ppm.",
        "The queue holds <400 ppm.",
        "The queue holds >400 ppm.",
    ):
        assert claim_fingerprint(different) != baseline


def test_claim_fingerprint_preserves_negation_and_geography() -> None:
    assert claim_fingerprint("The queue did not grow.") != claim_fingerprint(
        "The queue did grow."
    )
    assert claim_fingerprint("Adoption rose in India.") != claim_fingerprint(
        "Adoption rose in China."
    )


def test_claim_fingerprint_is_a_deterministic_sha256_digest() -> None:
    digest = claim_fingerprint("Queue capacity fell in 2024.")

    assert digest == claim_fingerprint("Queue capacity fell in 2024.")
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


# --- finding fingerprints -------------------------------------------------


def test_finding_fingerprint_keys_on_the_canonical_url_and_normalized_text() -> None:
    assert finding_fingerprint(
        finding("Adoption rose.", source_url="https://EXAMPLE.test/a/")
    ) == finding_fingerprint(
        finding("  adoption   ROSE. ", source_url="https://example.test/a")
    )


def test_finding_fingerprint_separates_url_topic_and_content() -> None:
    baseline = finding_fingerprint(finding("Adoption rose."))
    variants = (
        finding("Adoption rose.", source_url="https://example.test/b"),
        finding("Adoption rose.", related_sub_topic="Regulation"),
        finding("Adoption fell."),
    )

    for variant in variants:
        assert finding_fingerprint(variant) != baseline


# --- source snapshots -----------------------------------------------------


def test_the_latest_assessment_wins_without_reordering_first_seen_sources() -> None:
    first = source(url="https://EXAMPLE.test/a/", overall_score=0.4)
    second = source(url="https://example.test/b", overall_score=0.6)
    rescored = source(
        url="https://example.test/a",
        overall_score=0.91,
        low_confidence=True,
    )

    merged = merge_source_snapshot([first, second], [rescored])

    # Three assessments of two canonical URLs: the re-scored source collapses
    # onto its first-seen position, carrying the newest assessment entire.
    assert len(merged) == 2
    assert [item.overall_score for item in merged] == [0.91, 0.6]
    assert [item.url for item in merged] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert merged[0].low_confidence is True


def test_merge_source_snapshot_is_empty_for_two_empty_snapshots() -> None:
    assert merge_source_snapshot([], []) == []


def test_merge_source_snapshot_keeps_earlier_sources_absent_from_the_new_pass() -> None:
    kept = source(url="https://example.test/a")
    added = source(url="https://example.test/b")

    merged = merge_source_snapshot([kept], [added])

    assert merged == [kept, added]


def test_merge_source_snapshot_does_not_mutate_its_inputs() -> None:
    previous = [source(url="https://example.test/a", overall_score=0.4)]
    current = [source(url="https://example.test/a", overall_score=0.9)]

    merge_source_snapshot(previous, current)

    assert [item.overall_score for item in previous] == [0.4]
    assert [item.overall_score for item in current] == [0.9]


def test_provider_status_does_not_erase_a_prior_valid_score() -> None:
    scored = source(overall_score=0.75)
    failed = unscored_source()

    merged = merge_source_snapshot([scored], [failed])

    assert merged == [scored]


def test_a_new_score_replaces_a_prior_unscored_record() -> None:
    failed = unscored_source()
    scored = source(overall_score=0.91)

    merged = merge_source_snapshot([failed], [scored])

    assert merged == [scored]


def test_a_transient_status_preserves_a_score_of_the_same_revision() -> None:
    """An unchanged document keeps its assessment through an outage.

    A provider failure is an operational state, not a quality judgement, so a
    score computed from exactly the content still in hand survives it.
    """
    scored = source(overall_score=0.75, assessment_revision="assess-same")
    failed = unscored_source(assessment_revision="assess-same")

    merged = merge_source_snapshot([scored], [failed])

    assert merged == [scored]


def test_a_changed_revision_is_not_preserved_through_a_transient_status() -> None:
    """A stale score must not outlive the content it was computed from.

    The document at this URL changed, so the new assessment is about content
    the old score never saw. Keeping the old score would credit the new
    content with a judgement made about the old one — the exact failure the
    revision key exists to prevent.
    """
    scored = source(overall_score=0.87, assessment_revision="assess-old")
    failed = unscored_source(assessment_revision="assess-new")

    merged = merge_source_snapshot([scored], [failed])

    assert merged == [failed]
    assert merged[0].overall_score is None
    assert merged[0].evaluation_status == "unscored_provider"


def test_legacy_records_without_a_revision_keep_the_prior_behavior() -> None:
    """No recorded revision cannot prove the content changed.

    Every record written before this contract carries an empty revision, so
    the preservation rule must stay in force for them; a transient state
    drops no score it cannot show to be stale.
    """
    scored = source(overall_score=0.75)
    failed = unscored_source()

    assert merge_source_snapshot([scored], [failed]) == [scored]
    # One side recording a revision is not evidence of a change either: the
    # unscored record says nothing about which content it was about.
    assert merge_source_snapshot(
        [source(overall_score=0.75, assessment_revision="assess-a")],
        [unscored_source()],
    ) == [source(overall_score=0.75, assessment_revision="assess-a")]


# --- claim snapshots ------------------------------------------------------


def test_the_latest_claim_for_one_fingerprint_wins() -> None:
    stale = claim(
        "Queue capacity fell in 2024.",
        verdict="insufficient_evidence",
        confidence=0.2,
    )
    fresh = claim(
        "  queue capacity FELL in 2024  ",
        verdict="verified",
        confidence=0.9,
    )

    merged = merge_claim_snapshot([stale], [fresh])

    assert len(merged) == 1
    assert merged[0].verdict == "verified"
    assert merged[0].confidence == 0.9


def test_a_contradicted_verdict_replaces_a_stale_verified_one() -> None:
    verified = claim("Break-even was reached in 2025.", confidence=0.95)
    contradicted = claim(
        "Break-even was reached in 2025.",
        verdict="contradicted",
        confidence=0.4,
        contradictions=["Two later reviews report a missed target."],
    )

    merged = merge_claim_snapshot([verified], [contradicted])

    assert [item.verdict for item in merged] == ["contradicted"]
    assert merged[0].contradictions == [
        "Two later reviews report a missed target."
    ]


def test_merge_claim_snapshot_keeps_first_seen_order_and_earlier_claims() -> None:
    first = claim("Queue capacity fell in 2024.")
    second = claim("Break-even was reached in 2025.")
    revised = claim("Queue capacity fell in 2024.", confidence=0.5)

    merged = merge_claim_snapshot([first, second], [revised])

    assert [item.text for item in merged] == [
        "Queue capacity fell in 2024.",
        "Break-even was reached in 2025.",
    ]
    assert merged[0].confidence == 0.5
    assert merge_claim_snapshot([], []) == []


def test_merge_claim_snapshot_does_not_mutate_its_inputs() -> None:
    previous = [claim("Queue capacity fell in 2024.", confidence=0.9)]
    current = [claim("Queue capacity fell in 2024.", confidence=0.3)]

    merge_claim_snapshot(previous, current)

    assert [item.confidence for item in previous] == [0.9]
    assert [item.confidence for item in current] == [0.3]


# --- finding de-duplication ----------------------------------------------


def test_deduplicate_findings_keeps_the_higher_confidence_record() -> None:
    weak = finding("Adoption rose.", confidence=0.4, source_title="First")
    strong = finding(
        "  adoption   ROSE. ",
        confidence=0.9,
        source_url="https://EXAMPLE.test/a/",
        source_title="Second",
    )

    assert deduplicate_findings([weak, strong]) == [strong]


def test_deduplicate_findings_keeps_the_earlier_record_on_a_tie() -> None:
    first = finding("Adoption rose.", confidence=0.7, source_title="First")
    second = finding("Adoption rose.", confidence=0.7, source_title="Second")

    kept = deduplicate_findings([first, second])

    assert [item.source_title for item in kept] == ["First"]


def test_deduplicate_findings_keeps_every_binding_of_a_folded_pair() -> None:
    """A restatement cannot erase the binding an earlier one recorded.

    ``raw_findings`` is append-only across research rounds, and a later
    extraction of the same passage may name no planned target at all — the id
    it copied was not one of the plan's, which is kept rather than dropped.
    Folding on confidence alone would then delete the round-1 binding, and
    with it the only record of which obligation that evidence answers.
    """
    bound = finding(
        "Adoption rose.",
        target_ids=["topic-01-target-01"],
        vintage="January 2025 inventory",
        statement_date="2025-03-12",
        data_period="2024",
    )
    unbound = finding("Adoption rose.", confidence=0.9)

    (kept,) = deduplicate_findings([bound, unbound])

    assert kept.confidence == 0.9
    assert kept.target_ids == ["topic-01-target-01"]
    assert kept.vintage == "January 2025 inventory"
    assert kept.statement_date == "2025-03-12"
    assert kept.data_period == "2024"
    # The identity is unchanged by the fold.
    assert finding_fingerprint(kept) == finding_fingerprint(bound)


def test_deduplicate_findings_unions_both_records_bindings() -> None:
    """Two rounds can bind the same passage to two targets; both are kept."""
    winner = finding(
        "Adoption rose.",
        confidence=0.9,
        target_ids=["topic-02-target-01"],
        vintage="March 2025 inventory",
    )
    loser = finding(
        "Adoption rose.",
        target_ids=["topic-01-target-01", "topic-02-target-01"],
        vintage="January 2025 inventory",
        statement_date="2025-03-12",
    )

    (kept,) = deduplicate_findings([winner, loser])

    assert kept.target_ids == ["topic-02-target-01", "topic-01-target-01"]
    # The kept record's own dates win; the duplicate only fills what is absent.
    assert kept.vintage == "March 2025 inventory"
    assert kept.statement_date == "2025-03-12"


def test_deduplicate_findings_separates_other_urls_topics_and_content() -> None:
    kept = deduplicate_findings(
        [
            finding("Adoption rose."),
            finding("Adoption rose.", source_url="https://example.test/b"),
            finding("Adoption rose.", related_sub_topic="Regulation"),
            finding("Adoption fell."),
        ]
    )

    assert len(kept) == 4


def test_deduplicate_findings_preserves_first_seen_order_of_survivors() -> None:
    first = finding("Adoption rose.", confidence=0.8)
    second = finding("Costs fell.", confidence=0.8)

    kept = deduplicate_findings([first, second, finding("Adoption rose.")])

    assert [item.content for item in kept] == ["Adoption rose.", "Costs fell."]
    assert deduplicate_findings([]) == []
