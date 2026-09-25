"""Tests for canonical evidence identities and snapshot merges.

Every helper here is a pure function of its inputs, so these tests need no
provider, tracker, or scratchpad: a fingerprint is asserted directly and a
merge is asserted on ordinary domain records.
"""

from __future__ import annotations

from deep_research.agents.identity import (
    deduplicate_findings,
    finding_fingerprint,
    merge_source_snapshot,
)
from deep_research.utils.types import Finding, ScoredSource
from tests.evidence_fakes import figure, make_finding, make_read

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
    attributed_issuer: str | None = None,
    attribution_quote: str | None = None,
    measure_scope: str | None = None,
    release_date: str | None = None,
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
        attributed_issuer=attributed_issuer,
        attribution_quote=attribution_quote,
        measure_scope=measure_scope,
        release_date=release_date,
    )


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


def test_deduplicate_findings_keeps_the_provenance_one_round_recorded() -> None:
    """A restatement cannot erase whose figure the evidence is.

    The relay rule lives in fields a fold can drop: a first extraction records
    that the page attributes its figure to EIA, a later one of the same
    passage records a different confidence and no attribution. Keeping only
    the later record's silences would put the figure back under the host that
    carried it, which is the misattribution the fields exist to prevent.
    """
    attributed = finding(
        "Adoption rose.",
        attributed_issuer="Example Statistical Agency",
        attribution_quote="according to the Example Statistical Agency",
        measure_scope="all segments",
        release_date="2025-03-12",
    )
    restated = finding("Adoption rose.", confidence=0.9)

    (kept,) = deduplicate_findings([attributed, restated])

    assert kept.confidence == 0.9
    assert kept.attributed_issuer == "Example Statistical Agency"
    assert kept.attribution_quote == "according to the Example Statistical Agency"
    assert kept.measure_scope == "all segments"
    assert kept.release_date == "2025-03-12"


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


def test_a_duplicate_keeps_the_winners_evidence_and_fills_a_missing_one() -> None:
    read = make_read()
    snippet = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"
    rich = make_finding(read, snippet, figures=[figure("10.4", "GW", "2024", "actual")],
                        content="EIA: 10.4 GW added in 2024.")
    bare = rich.model_copy(update={"snippet": None, "read_id": None, "locator": None,
                                   "figures": [], "confidence": 0.99})
    [kept] = deduplicate_findings([rich, bare])
    assert kept.confidence == 0.99          # the winner is still the higher confidence
    assert kept.snippet == snippet          # its missing evidence is filled from the duplicate
    assert kept.figures == rich.figures
