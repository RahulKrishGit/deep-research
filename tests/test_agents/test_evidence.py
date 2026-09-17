"""Tests for the pure read-evidence contract in ``agents.evidence``.

Four jobs, one module:

* canonical text — deterministic normalization and exact excerpt membership;
* identity — publisher and work identity from read-derived metadata, with
  conflicts preserved instead of merged;
* admission — a stored original read may be re-admitted locally, and a forged,
  stale, or summarized record never may;
* persistence — the read/evidence registries and the Section 2.6 boundary
  manifests, whose reducers treat one ID with two bodies as a conflict.
"""

from __future__ import annotations

import pytest

from deep_research.agents.evidence import (
    DISPOSITION_REASONS,
    DISPOSITION_STAGES,
    PASSAGE_SELECTION_OPERATION,
    READ_ADMISSION_OPERATION,
    EvidenceContractError,
    EvidenceIdentityConflict,
    MissingBoundaryManifest,
    boundary_audit_id,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    canonical_publisher_id,
    canonical_read_text,
    excerpt_matches,
    merge_boundary_audits,
    merge_evidence_dispositions,
    merge_evidence_units,
    merge_read_records,
    normalized_content_sha256,
    passages_from_chunks,
    read_assessment_revision,
    read_dated_tokens,
    read_metadata_row,
    read_serving_host,
    rejected_anchor_names,
    require_boundary_manifest,
    resolve_read_works,
    resolve_work_identities,
    retained_work_count,
    validate_cached_read,
    validated_temporal,
)
from deep_research.agents.sources import (
    normalize_source_url,
    publisher_identity,
    source_domain,
)
from deep_research.utils.types import (
    INCOMPLETE_CONTENT_SHA256,
    QUALITY_CONTRACT_VERSION,
    EvidenceDisposition,
    EvidenceUnit,
    ReadRecord,
)

SESSION_ID = "session-1"
RETRIEVED_AT = "2026-09-16T10:00:00+00:00"
VALIDATED_AT = "2026-09-17T09:30:00+00:00"
PACKET_FINGERPRINT = "sha256:packet-1"
CONFIGURATION_FINGERPRINT = "sha256:config-1"

TEXT = (
    "Queue Study. Example Lab measured that   1,200 MW of interconnection\n"
    "capacity was withheld, and the delay did not fall below 5% in 2025."
)
PASSAGE = "Example Lab measured that 1,200 MW of interconnection capacity was withheld"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _original_read(**overrides: object) -> ReadRecord:
    """One complete, immutable original read of the fixture text."""
    record = build_read_record(
        session_id=SESSION_ID,
        reader="web_scraper",
        requested_url="https://lab.example/queue?utm_source=newsletter",
        resolved_url="https://lab.example/queue",
        title="Queue Study",
        retrieved_at=RETRIEVED_AT,
        text=TEXT,
        passages={"p-1": PASSAGE},
        declared_content_sha256=normalized_content_sha256(TEXT),
        target_ids=("target-1",),
    )
    if overrides:
        # ``model_copy`` deliberately does not revalidate: these overrides
        # stand in for a corrupt or forged stored record, which is exactly
        # what cache admission must refuse.
        record = record.model_copy(update=overrides)
    return record


def _identity_metadata(record: ReadRecord, **extra: object) -> dict[str, object]:
    """The read-derived metadata row a stronger identity is resolved from."""
    return {
        "source_id": record.read_id,
        "title": record.title,
        "year": 2025,
        "issuer": "Example Lab",
        "serving_host": source_domain(record.resolved_url),
        "complete_content_sha256": record.content_sha256,
        "extraction_complete": record.extraction_complete,
        **extra,
    }


# --------------------------------------------------------------------------
# canonical text and exact excerpt membership
# --------------------------------------------------------------------------


def test_canonical_read_text_collapses_layout_without_losing_words() -> None:
    assert canonical_read_text("  a\t b\n\nc  ") == "a b c"
    # Unicode form is normalized, never dropped: the composed and decomposed
    # spellings of the same word are one string.
    assert canonical_read_text("cafe\u0301") == canonical_read_text("caf\u00e9")


def test_normalized_content_sha256_ignores_layout_and_unicode_form() -> None:
    assert normalized_content_sha256("a  b\nc") == normalized_content_sha256("a b c")
    assert normalized_content_sha256("cafe\u0301") == (
        normalized_content_sha256("caf\u00e9")
    )
    assert len(normalized_content_sha256("a b")) == 64


def test_normalized_content_sha256_separates_what_a_read_asserts() -> None:
    """Numbers, minus signs, units, and negation never normalize away."""
    baseline = normalized_content_sha256("output fell by 5% in 2025")
    for altered in (
        "output fell by 15% in 2025",
        "output fell by -5% in 2025",
        "output rose by 5% in 2025",
        "output fell by 5 MW in 2025",
    ):
        assert normalized_content_sha256(altered) != baseline


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_normalized_content_sha256_refuses_text_with_no_content(blank: str) -> None:
    """An empty extraction can never become a content identity."""
    with pytest.raises(ValueError):
        normalized_content_sha256(blank)


def test_excerpt_matches_normalizes_whitespace_and_unicode_form() -> None:
    assert excerpt_matches(TEXT, PASSAGE) is True
    assert excerpt_matches(TEXT, PASSAGE.replace(" ", "\n  ")) is True
    assert excerpt_matches("cafe\u0301 opens", "caf\u00e9 opens") is True


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_excerpt_matches_refuses_an_empty_excerpt(blank: str) -> None:
    assert excerpt_matches(TEXT, blank) is False


@pytest.mark.parametrize(
    "near_miss",
    [
        # A different number is a different fact.
        (
            "Example Lab measured that 1,200 MW of interconnection capacity "
            "was withheld in 2026"
        ),
        # A missing minus sign.
        "the delay fell to -5% in 2025",
        # A different unit.
        "the delay did not fall below 5 GW in 2025",
        # Dropped negation.
        "the delay did fall below 5% in 2025",
        # A paraphrase is not an excerpt.
        "Example Lab measured that capacity was withheld",
    ],
)
def test_excerpt_matches_never_accepts_a_near_miss(near_miss: str) -> None:
    assert excerpt_matches(TEXT, near_miss) is False


# --------------------------------------------------------------------------
# work and publisher identity
# --------------------------------------------------------------------------


def test_doi_missing_mirror_joins_original() -> None:
    rows = [
        {
            "source_id": "original",
            "doi": "https://doi.org/10.1234/ABC",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
            "complete_content_sha256": "a" * 64,
        },
        {
            "source_id": "mirror",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
            "complete_content_sha256": "a" * 64,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["original"].key == resolved["mirror"].key
    assert resolved["original"].issuer_id == resolved["mirror"].issuer_id


def test_doi_normalization_ignores_the_resolver_prefix_and_case() -> None:
    rows = [
        {"source_id": "a", "doi": "https://doi.org/10.1234/ABC"},
        {"source_id": "b", "doi": "doi:10.1234/abc"},
        {"source_id": "c", "doi": "10.1234/abc"},
    ]
    resolved = resolve_work_identities(rows)

    assert {row.key for row in resolved.values()} == {"doi:10.1234/abc"}
    assert all(row.identity_status == "known" for row in resolved.values())


def test_a_url_in_the_doi_field_is_not_a_doi() -> None:
    """Malformed metadata is not identity: it stays explicitly unknown."""
    rows = [
        {"source_id": "a", "doi": "https://example.org/report/42"},
        {"source_id": "b", "doi": "https://example.org/report/42"},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["b"].key is None
    assert resolved["a"].identity_status == "unknown"
    assert resolved["a"].basis


def test_unknown_identity_stays_unknown() -> None:
    rows = [
        {"source_id": "a", "title": "A generic study"},
        {"source_id": "b", "title": "A generic study"},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["b"].key is None
    assert resolved["a"].identity_status == "unknown"
    assert resolved["b"].identity_status == "unknown"
    assert resolved["a"].issuer_id is None


def test_conflicting_work_identifiers_are_preserved_not_merged() -> None:
    rows = [
        {
            "source_id": "original",
            "doi": "10.1234/abc",
            "complete_content_sha256": "a" * 64,
        },
        {
            "source_id": "copy",
            "doi": "10.9999/xyz",
            "complete_content_sha256": "a" * 64,
        },
    ]
    resolved = resolve_work_identities(rows)

    for source_id in ("original", "copy"):
        identity = resolved[source_id]
        assert identity.key is None
        assert identity.identity_status == "conflicting"
        assert "conflict" in identity.basis.casefold()
        assert set(identity.aliases) == {
            "doi:10.1234/abc",
            "doi:10.9999/xyz",
            f"sha256:{'a' * 64}",
        }


def test_two_complete_hashes_for_one_doi_are_a_conflict() -> None:
    """A version difference is ambiguity, never a silent join."""
    rows = [
        {"source_id": "v1", "doi": "10.1234/abc", "complete_content_sha256": "a" * 64},
        {"source_id": "v2", "doi": "10.1234/abc", "complete_content_sha256": "b" * 64},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["v1"].key is None
    assert resolved["v2"].key is None
    assert resolved["v1"].identity_status == "conflicting"


def test_different_editions_do_not_join_through_a_shared_title() -> None:
    rows = [
        {
            "source_id": "first",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
            "edition": "1st",
        },
        {
            "source_id": "second",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
            "edition": "2nd",
        },
        {
            "source_id": "first-again",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
            "edition": "1st",
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["first"].key == resolved["first-again"].key
    assert resolved["first"].key == "title:queue study|2025|example lab|1st"
    assert resolved["second"].key != resolved["first"].key


def test_a_generic_title_never_joins_two_different_documents() -> None:
    """Same title, year, and issuer, different complete bodies: two works."""
    rows = [
        {
            "source_id": "one",
            "issuer": "Example Lab",
            "title": "Annual Report",
            "year": 2025,
            "complete_content_sha256": "a" * 64,
        },
        {
            "source_id": "two",
            "issuer": "Example Lab",
            "title": "Annual Report",
            "year": 2025,
            "complete_content_sha256": "b" * 64,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["one"].key == f"sha256:{'a' * 64}"
    assert resolved["two"].key == f"sha256:{'b' * 64}"
    assert resolved["one"].key != resolved["two"].key


@pytest.mark.parametrize(
    "unusable_hash",
    ["", "   ", "error", "n/a", "a" * 63, "a" * 65, "z" * 64, "sha256:" + "a" * 64],
)
def test_an_unusable_hash_never_establishes_identity(unusable_hash: str) -> None:
    rows = [
        {"source_id": "a", "complete_content_sha256": unusable_hash},
        {"source_id": "b", "complete_content_sha256": unusable_hash},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["b"].key is None
    assert resolved["a"].identity_status == "unknown"


def test_a_partial_extraction_hash_is_not_an_identity_edge() -> None:
    """A hash of part of a document identifies nothing."""
    rows = [
        {
            "source_id": "partial",
            "complete_content_sha256": "a" * 64,
            "extraction_complete": False,
        },
        {
            "source_id": "complete",
            "complete_content_sha256": "a" * 64,
            "extraction_complete": True,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["partial"].key is None
    assert resolved["partial"].identity_status == "unknown"
    assert resolved["complete"].key == f"sha256:{'a' * 64}"


def test_an_uppercase_hash_is_normalized_not_rejected() -> None:
    rows = [
        {"source_id": "a", "complete_content_sha256": "A" * 64},
        {"source_id": "b", "complete_content_sha256": "a" * 64},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key == f"sha256:{'a' * 64}"
    assert resolved["a"].key == resolved["b"].key


@pytest.mark.parametrize("declared", ["false", "False", "no", 0, None])
def test_a_non_boolean_completeness_flag_never_reads_as_complete(
    declared: object,
) -> None:
    """Only an absent flag or a real ``True`` licenses a content identity."""
    rows = [
        {
            "source_id": "a",
            "complete_content_sha256": "a" * 64,
            "extraction_complete": declared,
        },
        {
            "source_id": "b",
            "complete_content_sha256": "a" * 64,
            "extraction_complete": declared,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].aliases == []
    assert resolved["a"].key is None
    assert resolved["a"].identity_status == "unknown"


def test_an_absent_completeness_flag_keeps_the_named_complete_hash() -> None:
    """``complete_content_sha256`` asserts completeness by its own name."""
    rows = [{"source_id": "a", "complete_content_sha256": "a" * 64}]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key == f"sha256:{'a' * 64}"


def test_report_number_is_namespaced_by_its_issuer() -> None:
    rows = [
        {"source_id": "a", "issuer": "Example Lab", "report_number": "TR-2025-01"},
        {"source_id": "b", "issuer": "Example Lab", "report_number": "tr-2025-01."},
        {"source_id": "c", "issuer": "Other Org", "report_number": "TR-2025-01"},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key == "report:example lab:tr-2025-01"
    assert resolved["a"].key == resolved["b"].key
    assert resolved["c"].key != resolved["a"].key


def test_two_report_numbers_from_one_issuer_are_a_conflict() -> None:
    rows = [
        {
            "source_id": "a",
            "issuer": "Example Lab",
            "report_number": "TR-1",
            "complete_content_sha256": "a" * 64,
        },
        {
            "source_id": "b",
            "issuer": "Example Lab",
            "report_number": "TR-2",
            "complete_content_sha256": "a" * 64,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["a"].identity_status == "conflicting"


def test_identity_links_evidence_a_related_work_without_merging_it() -> None:
    """A stated relationship is recorded, never used as an identity join."""
    rows = [
        {
            "source_id": "story",
            "issuer": "News Desk",
            "title": "Grid queue delays",
            "year": 2026,
            "identity_links": ["https://doi.org/10.1234/abc", "10.9999/xyz"],
        },
        {
            "source_id": "report",
            "doi": "10.1234/abc",
            "issuer": "Example Lab",
            "title": "Queue Study",
            "year": 2025,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["story"].key != resolved["report"].key
    assert resolved["story"].derives_from_work_ids == [
        "doi:10.1234/abc",
        "doi:10.9999/xyz",
    ]
    assert resolved["report"].derives_from_work_ids == []


def test_an_evidenced_issuer_beats_the_serving_cdn_host() -> None:
    """A CDN host is transport; it never overrides the publisher it mirrors."""
    lab_on_cdn = {"issuer": "Example Lab", "serving_host": "cdn.example.net"}
    lab_direct = {"issuer": "Example Lab", "serving_host": "lab.example"}
    other_on_cdn = {"issuer": "Other Org", "serving_host": "cdn.example.net"}

    assert canonical_publisher_id(lab_on_cdn) == canonical_publisher_id(lab_direct)
    assert canonical_publisher_id(other_on_cdn) != canonical_publisher_id(lab_on_cdn)
    assert canonical_publisher_id({"serving_host": "news.example.co.uk"}) == (
        "example.co.uk"
    )
    assert canonical_publisher_id({"url": "https://news.example.co.uk/story"}) == (
        "example.co.uk"
    )


@pytest.mark.parametrize(
    "metadata",
    [{}, {"issuer": "   "}, {"serving_host": ""}, {"issuer": None, "url": None}],
)
def test_a_publisher_with_no_evidence_is_unknown(metadata: dict[str, object]) -> None:
    assert canonical_publisher_id(metadata) is None


def test_a_group_with_two_issuers_has_no_single_issuer() -> None:
    rows = [
        {"source_id": "a", "doi": "10.1234/abc", "issuer": "Example Lab"},
        {"source_id": "b", "doi": "10.1234/abc", "issuer": "Example University"},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key == resolved["b"].key
    assert resolved["a"].issuer_id is None
    assert resolved["b"].issuer_id is None


@pytest.mark.parametrize(
    "row",
    [
        {"doi": "10.1234/abc"},
        {"source_id": "   ", "title": "Queue Study"},
        "not-a-mapping",
    ],
)
def test_a_malformed_metadata_row_is_rejected(row: object) -> None:
    with pytest.raises(ValueError):
        resolve_work_identities([row])  # type: ignore[list-item]


def test_a_wrong_typed_identifier_is_unusable_metadata() -> None:
    """A non-string identifier identifies nothing; it never raises or joins."""
    rows = [
        {"source_id": "a", "doi": 12345},
        {"source_id": "b", "doi": 12345},
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["a"].identity_status == "unknown"


def test_resolution_is_keyed_by_source_id_and_deterministic() -> None:
    rows = [
        {"source_id": "original", "doi": "10.1234/abc"},
        {"source_id": "mirror", "title": "Queue Study", "year": 2025,
         "issuer": "Example Lab"},
    ]

    first = resolve_work_identities(rows)
    second = resolve_work_identities(rows)

    assert set(first) == {"original", "mirror"}
    assert first == second
    assert resolve_work_identities([]) == {}
    assert first["original"].aliases == sorted(first["original"].aliases)


# --------------------------------------------------------------------------
# strict read producers
# --------------------------------------------------------------------------


def test_build_read_record_requires_complete_original_text() -> None:
    """A read with no usable body is not a read."""
    for blank in ("", "   ", "\n\t"):
        with pytest.raises(EvidenceContractError):
            build_read_record(
                session_id=SESSION_ID,
                reader="web_scraper",
                requested_url="https://lab.example/queue",
                resolved_url="https://lab.example/queue",
                title="Queue Study",
                retrieved_at=RETRIEVED_AT,
                text=blank,
                passages={"p-1": "anything"},
            )
    with pytest.raises(EvidenceContractError):
        build_read_record(
            session_id=SESSION_ID,
            reader="web_scraper",
            requested_url="https://lab.example/queue",
            resolved_url="https://lab.example/queue",
            title="Queue Study",
            retrieved_at=RETRIEVED_AT,
            text=TEXT,
            passages={},
        )


def test_build_read_record_rejects_an_excerpt_that_is_not_in_the_text() -> None:
    with pytest.raises(EvidenceContractError):
        build_read_record(
            session_id=SESSION_ID,
            reader="web_scraper",
            requested_url="https://lab.example/queue",
            resolved_url="https://lab.example/queue",
            title="Queue Study",
            retrieved_at=RETRIEVED_AT,
            text=TEXT,
            passages={"p-1": "Example Lab measured that capacity was withheld."},
        )


def test_build_read_record_rejects_a_declared_hash_that_is_not_its_own_text() -> None:
    with pytest.raises(EvidenceContractError):
        build_read_record(
            session_id=SESSION_ID,
            reader="web_scraper",
            requested_url="https://lab.example/queue",
            resolved_url="https://lab.example/queue",
            title="Queue Study",
            retrieved_at=RETRIEVED_AT,
            text=TEXT,
            passages={"p-1": PASSAGE},
            declared_content_sha256="b" * 64,
        )


def test_build_read_record_rejects_an_incomplete_extraction_claiming_a_hash() -> None:
    """A partial read may exist, but never behind a hash that looks complete."""
    with pytest.raises(EvidenceContractError):
        build_read_record(
            session_id=SESSION_ID,
            reader="document_reader",
            requested_url="https://lab.example/queue.pdf",
            resolved_url="https://lab.example/queue.pdf",
            title="Queue Study",
            retrieved_at=RETRIEVED_AT,
            text=TEXT,
            passages={"p-1": PASSAGE},
            extraction_complete=False,
            declared_content_sha256=normalized_content_sha256(TEXT),
        )


def _partial_read(**overrides: object) -> ReadRecord:
    """One read of a document whose page 7 could not be extracted."""
    record = build_read_record(
        session_id=SESSION_ID,
        reader="document_reader",
        requested_url="https://lab.example/queue.pdf",
        resolved_url="https://lab.example/queue.pdf",
        title="Queue Study",
        retrieved_at=RETRIEVED_AT,
        text=TEXT,
        passages={"page-1-chunk-0": PASSAGE},
        extraction_complete=False,
        target_ids=("target-1",),
    )
    if overrides:
        record = record.model_copy(update=overrides)
    return record


def test_a_partial_extraction_is_an_admissible_read() -> None:
    """Discarding a 200-page PDF over page 7 loses evidence nobody can audit.

    The record still names its read, both URLs, its observation, and the
    locators it did read; only the content hash is withheld, because a hash
    over part of a document identifies nothing.
    """
    record = _partial_read()

    assert record.extraction_complete is False
    assert record.content_sha256 == INCOMPLETE_CONTENT_SHA256
    assert record.passages == {"page-1-chunk-0": PASSAGE}
    assert record.read_id.startswith("read-")


def test_a_partial_read_still_carries_evidence_units() -> None:
    """Section 2.4's chain needs the read to exist, not to be perfect."""
    record = _partial_read()

    unit = build_evidence_unit(
        read=record,
        locator="page-1-chunk-0",
        excerpt=PASSAGE,
        origin="researcher",
    )

    assert unit.read_id == record.read_id
    assert unit.excerpt == PASSAGE
    assert merge_read_records({record.read_id: record}, {}) == {
        record.read_id: record
    }


def test_a_partial_read_has_no_usable_content_identity() -> None:
    """Its marker hash is never an identity edge, and never a digest."""
    partial = _partial_read()
    resolved = resolve_work_identities(
        [
            {
                "source_id": "partial",
                "title": "Queue Study",
                "issuer": "Example Lab",
                "year": 2025,
                "complete_content_sha256": partial.content_sha256,
                "extraction_complete": False,
            },
            {
                "source_id": "other",
                "title": "Queue Study",
                "issuer": "Example Lab",
                "year": 2025,
                "complete_content_sha256": partial.content_sha256,
                "extraction_complete": False,
            },
        ]
    )

    assert resolved["partial"].aliases == []
    # Only the conservative title alias joins them, never a hash.
    assert resolved["partial"].key == resolved["other"].key
    assert resolved["partial"].key.startswith("title:")
    assert resolved["partial"].key != f"sha256:{partial.content_sha256}"


def test_two_different_partial_reads_of_one_source_are_two_reads() -> None:
    """A retry that recovers page 7 is a new read, not a conflicting one."""
    first = _partial_read()
    second = build_read_record(
        session_id=SESSION_ID,
        reader="document_reader",
        requested_url="https://lab.example/queue.pdf",
        resolved_url="https://lab.example/queue.pdf",
        title="Queue Study",
        retrieved_at=RETRIEVED_AT,
        text=TEXT,
        passages={
            "page-1-chunk-0": PASSAGE,
            "page-7-chunk-1": "the delay did not fall below 5% in 2025",
        },
        extraction_complete=False,
    )

    assert second.read_id != first.read_id
    merged = merge_read_records({first.read_id: first}, {second.read_id: second})
    assert set(merged) == {first.read_id, second.read_id}


def test_a_partial_read_is_never_admitted_from_the_cache() -> None:
    """Cache admission resolves a validated immutable original, not a fragment."""
    record = _partial_read()

    assert (
        validate_cached_read(
            record,
            TEXT,
            expected_content_sha256=record.content_sha256,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )
    assert (
        validate_cached_read(
            record,
            TEXT,
            expected_content_sha256=normalized_content_sha256(TEXT),
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_a_read_keeps_its_requested_and_resolved_urls_apart() -> None:
    record = _original_read()

    assert record.requested_url == "https://lab.example/queue?utm_source=newsletter"
    assert record.resolved_url == "https://lab.example/queue"
    assert record.extraction_complete is True
    assert record.content_sha256 == normalized_content_sha256(TEXT)
    assert record.acquisition_kind == "network"
    assert record.version_validated_at is None
    # The read id is reproducible from the record's own immutable fields.
    assert record.read_id


def test_a_read_preserves_raw_locator_text_and_matches_it_normalized() -> None:
    record = _original_read()

    assert record.passages["p-1"] == PASSAGE
    assert excerpt_matches(record.passages["p-1"], PASSAGE) is True
    assert (
        excerpt_matches(record.passages["p-1"], PASSAGE.replace(" ", "  "))
        is True
    )


def test_passages_from_chunks_locates_every_extracted_chunk() -> None:
    chunks = [
        {"text": "Page one body.", "chunk_index": 0, "page": 1},
        {"text": "Page two, first half.", "chunk_index": 1, "page": 2},
        {"text": "Page two, second half.", "chunk_index": 2, "page": 2},
        {"text": "A local text chunk.", "chunk_index": 3},
    ]

    assert passages_from_chunks(chunks) == {
        "page-1-chunk-0": "Page one body.",
        "page-2-chunk-1": "Page two, first half.",
        "page-2-chunk-2": "Page two, second half.",
        "chunk-3": "A local text chunk.",
    }


@pytest.mark.parametrize(
    "chunks",
    [
        [{"text": "body"}],
        [{"chunk_index": 0}],
        [{"text": "   ", "chunk_index": 0}],
        [{"text": "body", "chunk_index": "0"}],
        [
            {"text": "first", "chunk_index": 0},
            {"text": "second", "chunk_index": 0},
        ],
    ],
)
def test_a_malformed_chunk_list_is_rejected(chunks: list[dict[str, object]]) -> None:
    with pytest.raises(EvidenceContractError):
        passages_from_chunks(chunks)


# --------------------------------------------------------------------------
# cache admission of a stored original read
# --------------------------------------------------------------------------


def test_a_validated_immutable_read_is_admitted_from_the_cache() -> None:
    """The positive control: no second download, same body and observation."""
    stored = _original_read()

    admitted = validate_cached_read(
        stored,
        TEXT,
        expected_content_sha256=normalized_content_sha256(TEXT),
        version_eligible=True,
        validated_at=VALIDATED_AT,
    )

    assert admitted is not None
    assert admitted.read_id == stored.read_id
    assert admitted.content_sha256 == stored.content_sha256
    assert admitted.passages == stored.passages
    assert admitted.resolved_url == stored.resolved_url
    assert admitted.title == stored.title
    assert admitted.origin_session_id == SESSION_ID
    # The original observation period is retained, not restamped.
    assert admitted.retrieved_at == RETRIEVED_AT
    assert admitted.acquisition_kind == "cache"
    assert admitted.version_validated_at == VALIDATED_AT


def test_an_admitted_cache_read_still_resolves_its_publisher_and_work() -> None:
    stored = _original_read()
    admitted = validate_cached_read(
        stored,
        TEXT,
        expected_content_sha256=normalized_content_sha256(TEXT),
        version_eligible=True,
        validated_at=VALIDATED_AT,
    )
    assert admitted is not None

    before = resolve_work_identities([_identity_metadata(stored)])
    after = resolve_work_identities([_identity_metadata(admitted)])

    assert before[stored.read_id].key == after[admitted.read_id].key
    assert before[stored.read_id].issuer_id == after[admitted.read_id].issuer_id
    assert canonical_publisher_id(_identity_metadata(stored)) == (
        canonical_publisher_id(_identity_metadata(admitted))
    )


def test_a_forged_or_missing_cache_read_id_is_refused() -> None:
    stored = _original_read()

    for forged in (
        stored.model_copy(update={"read_id": ""}),
        # A previously cache-stamped copy is not the stored original: a cache
        # entry may never be re-admitted from another cache entry.
        stored.model_copy(update={"acquisition_kind": "cache"}),
    ):
        assert (
            validate_cached_read(
                forged,
                TEXT,
                expected_content_sha256=normalized_content_sha256(TEXT),
                version_eligible=True,
                validated_at=VALIDATED_AT,
            )
            is None
        )


def test_an_incorrect_content_hash_is_refused() -> None:
    stored = _original_read()
    recomputed = normalized_content_sha256(TEXT)

    # The stored hash disagrees with the stored text.
    assert (
        validate_cached_read(
            stored.model_copy(update={"content_sha256": "b" * 64}),
            TEXT,
            expected_content_sha256=recomputed,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )
    # The locally resolved hash disagrees with both.
    assert (
        validate_cached_read(
            stored,
            TEXT,
            expected_content_sha256="c" * 64,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )
    # A different body than the one the hash was taken from.
    assert (
        validate_cached_read(
            stored,
            "Queue Study. A completely different body.",
            expected_content_sha256=recomputed,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_a_generated_summary_is_not_original_source_text() -> None:
    """A remembered paraphrase must never be admitted as the stored body."""
    stored = _original_read().model_copy(
        update={"passages": {"p-1": "The study found capacity was withheld."}}
    )

    assert (
        validate_cached_read(
            stored,
            TEXT,
            expected_content_sha256=stored.content_sha256,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_a_stale_time_sensitive_cache_entry_is_refused() -> None:
    stored = _original_read()

    assert (
        validate_cached_read(
            stored,
            TEXT,
            expected_content_sha256=normalized_content_sha256(TEXT),
            version_eligible=False,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_cache_validation_requires_a_complete_read_and_valid_locators() -> None:
    stored = _original_read()
    recomputed = normalized_content_sha256(TEXT)

    for corrupt in (
        stored.model_copy(update={"extraction_complete": False}),
        stored.model_copy(update={"passages": {"   ": PASSAGE}}),
        stored.model_copy(update={"passages": {"p-1": "   "}}),
    ):
        assert (
            validate_cached_read(
                corrupt,
                TEXT,
                expected_content_sha256=recomputed,
                version_eligible=True,
                validated_at=VALIDATED_AT,
            )
            is None
        )

    # An empty resolved body proves nothing either.
    assert (
        validate_cached_read(
            stored,
            "   ",
            expected_content_sha256=recomputed,
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_a_serialized_completeness_flag_is_never_treated_as_complete() -> None:
    """``"false"`` is not ``True``: a corrupt snapshot must fail closed."""
    stored = _original_read()

    forged = stored.model_construct(
        **{
            **stored.__dict__,
            "extraction_complete": "false",
        }
    )

    assert (
        validate_cached_read(
            forged,
            TEXT,
            expected_content_sha256=normalized_content_sha256(TEXT),
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )
        is None
    )


def test_the_local_validation_stamp_and_expected_hash_are_caller_errors() -> None:
    stored = _original_read()

    with pytest.raises(ValueError):
        validate_cached_read(
            stored,
            TEXT,
            expected_content_sha256=normalized_content_sha256(TEXT),
            version_eligible=True,
            validated_at="2026-09-17T09:30:00",
        )
    with pytest.raises(ValueError):
        validate_cached_read(
            stored,
            TEXT,
            expected_content_sha256="not-a-hash",
            version_eligible=True,
            validated_at=VALIDATED_AT,
        )


# --------------------------------------------------------------------------
# registries
# --------------------------------------------------------------------------


def test_a_read_registry_round_trips_and_merges_new_reads() -> None:
    first = _original_read()
    second = _original_read(
        read_id="read-second",
        resolved_url="https://other.example/report",
        requested_url="https://other.example/report",
        passages={"p-2": PASSAGE},
    )

    merged = merge_read_records({first.read_id: first}, {second.read_id: second})

    assert set(merged) == {first.read_id, "read-second"}
    assert merged[first.read_id] == first
    assert merge_read_records({first.read_id: first}, {}) == {first.read_id: first}


def test_the_same_read_id_with_different_content_is_a_conflict() -> None:
    stored = _original_read()
    conflicting = stored.model_copy(update={"content_sha256": "b" * 64})

    with pytest.raises(EvidenceIdentityConflict):
        merge_read_records({stored.read_id: stored}, {stored.read_id: conflicting})


def test_one_read_id_with_one_body_keeps_the_first_observation() -> None:
    stored = _original_read()
    reread = stored.model_copy(
        update={
            "retrieved_at": "2026-09-16T12:00:00+00:00",
            "title": "Queue Study (re-read)",
            "target_ids": ["target-2"],
        }
    )

    merged = merge_read_records({stored.read_id: stored}, {stored.read_id: reread})

    assert merged[stored.read_id].retrieved_at == RETRIEVED_AT
    assert merged[stored.read_id].target_ids == ["target-1", "target-2"]
    # The label belongs to the first observation of that read id: a re-read
    # cannot silently relabel evidence a passage already cites.
    assert merged[stored.read_id].title == stored.title


def test_a_network_read_replaces_a_cache_import_of_the_same_body() -> None:
    stored = _original_read()
    imported = stored.model_copy(
        update={
            "acquisition_kind": "cache",
            "version_validated_at": VALIDATED_AT,
            "retrieved_at": "2026-09-16T12:00:00+00:00",
            "title": "Imported copy label",
        }
    )

    merged = merge_read_records({stored.read_id: imported}, {stored.read_id: stored})

    assert merged[stored.read_id].acquisition_kind == "network"
    assert merged[stored.read_id].retrieved_at == RETRIEVED_AT
    # First observation still owns the label, whichever record is preferred.
    assert merged[stored.read_id].title == "Imported copy label"


def test_evidence_ids_are_stable_when_a_stronger_identity_arrives() -> None:
    """Ids belong to the read and its passage, never to mutable assessments."""
    stored = _original_read()
    unit = build_evidence_unit(
        read=stored, locator="p-1", excerpt=PASSAGE, origin="researcher"
    )

    weak = resolve_work_identities(
        [{"source_id": stored.read_id, "title": "Queue Study"}]
    )
    # The same read, now with an evidenced DOI: the identity resolution
    # changes, and neither the read id nor the evidence id may move.
    stronger = resolve_work_identities(
        [{"source_id": stored.read_id, "doi": "10.1234/abc"}]
    )
    rebuilt = build_evidence_unit(
        read=stored, locator="p-1", excerpt=PASSAGE, origin="researcher"
    )

    assert weak[stored.read_id].key != stronger[stored.read_id].key
    assert rebuilt.evidence_id == unit.evidence_id
    assert rebuilt.read_id == stored.read_id
    assert unit.evidence_id.startswith("ev-")


def test_evidence_units_must_cite_a_locator_in_their_own_read() -> None:
    stored = _original_read()

    with pytest.raises(EvidenceContractError):
        build_evidence_unit(
            read=stored, locator="p-404", excerpt=PASSAGE, origin="researcher"
        )
    with pytest.raises(EvidenceContractError):
        build_evidence_unit(
            read=stored,
            locator="p-1",
            excerpt="Example Lab measured that capacity was withheld.",
            origin="researcher",
        )


def test_conflicting_evidence_units_for_one_id_are_a_conflict() -> None:
    stored = _original_read()
    unit = build_evidence_unit(
        read=stored,
        locator="p-1",
        excerpt=PASSAGE,
        origin="researcher",
        target_ids=("target-1",),
    )
    same = build_evidence_unit(
        read=stored,
        locator="p-1",
        excerpt=PASSAGE,
        origin="researcher",
        target_ids=("target-2",),
    )
    altered = unit.model_copy(update={"excerpt": PASSAGE.replace("1,200", "1,400")})

    merged = merge_evidence_units({unit.evidence_id: unit}, {same.evidence_id: same})
    assert merged[unit.evidence_id].target_ids == ["target-1", "target-2"]

    with pytest.raises(EvidenceIdentityConflict):
        merge_evidence_units({unit.evidence_id: unit}, {altered.evidence_id: altered})


def test_one_passage_selected_twice_keeps_its_first_selector() -> None:
    """Both agents read the same stored passage: that is reuse, not conflict.

    The unit records the agent that selected it first and unions the targets
    the later selection added, so nothing a later selector contributed is
    dropped — cross-agent reuse of one read is exactly what the registry is
    for.
    """
    stored = _original_read()
    first = build_evidence_unit(
        read=stored,
        locator="p-1",
        excerpt=PASSAGE,
        origin="researcher",
        target_ids=("target-1",),
    )
    second = build_evidence_unit(
        read=stored,
        locator="p-1",
        excerpt=PASSAGE,
        origin="fact_checker",
        target_ids=("target-2",),
    )

    merged = merge_evidence_units(
        {first.evidence_id: first}, {second.evidence_id: second}
    )

    assert merged[first.evidence_id].origin == "researcher"
    assert merged[first.evidence_id].target_ids == ["target-1", "target-2"]


def test_two_titles_for_one_evidence_id_are_a_conflict() -> None:
    """One passage has one recorded source label; disagreement is not silent."""
    stored = _original_read()
    unit = build_evidence_unit(
        read=stored, locator="p-1", excerpt=PASSAGE, origin="researcher"
    )
    relabelled = unit.model_copy(update={"source_title": "Somewhere else"})

    with pytest.raises(EvidenceIdentityConflict):
        merge_evidence_units(
            {unit.evidence_id: unit}, {relabelled.evidence_id: relabelled}
        )


def test_dispositions_persist_an_explicit_reason_per_item() -> None:
    deferred = EvidenceDisposition(
        item_id="https://lab.example/queue.pdf",
        stage="read-selection",
        reason="deferred_capacity",
        target_ids=["target-1"],
    )
    kept = merge_evidence_dispositions([], [deferred])
    assert kept == [deferred]
    assert merge_evidence_dispositions(kept, [deferred]) == [deferred]

    contradicted = EvidenceDisposition(
        item_id="https://lab.example/queue.pdf",
        stage="read-selection",
        reason="irrelevant",
        target_ids=["target-1"],
    )
    with pytest.raises(EvidenceIdentityConflict):
        merge_evidence_dispositions([deferred], [contradicted])


def test_a_disposition_requires_a_non_empty_stage_and_reason() -> None:
    with pytest.raises(ValueError):
        EvidenceDisposition(item_id="x", stage="", reason="irrelevant")
    with pytest.raises(ValueError):
        EvidenceDisposition(item_id="x", stage="read-selection", reason="")
    assert "read-selection" in DISPOSITION_STAGES
    assert "deferred_capacity" in DISPOSITION_REASONS


def test_a_disposition_records_which_retained_evidence_it_matches() -> None:
    """A duplicate names the retained evidence; disagreement is not silent."""
    item_id = "https://lab.example/queue.pdf"
    unresolved = EvidenceDisposition(
        item_id=item_id,
        stage="retention",
        reason="duplicate_content",
        retained_equivalent_id=None,
    )
    resolved = unresolved.model_copy(update={"retained_equivalent_id": "ev-kept"})
    elsewhere = unresolved.model_copy(
        update={"retained_equivalent_id": "ev-somewhere-else"}
    )

    # A later pass that resolves the retained equivalent fills it in.
    merged = merge_evidence_dispositions([unresolved], [resolved])
    assert merged[0].retained_equivalent_id == "ev-kept"
    # Two different retained equivalents for one omitted item contradict.
    with pytest.raises(EvidenceIdentityConflict):
        merge_evidence_dispositions([resolved], [elsewhere])


# --------------------------------------------------------------------------
# boundary manifests
# --------------------------------------------------------------------------


def test_a_read_admission_manifest_records_every_boundary_id() -> None:
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        target_ids=("target-1",),
        input_ids=("https://lab.example/queue", "https://lab.example/other.pdf"),
        selected_ids=("https://lab.example/queue",),
        returned_ids=("read-abc",),
        accepted_ids=("read-abc",),
        deferred_ids=("https://lab.example/other.pdf",),
        disposition_ids=("https://lab.example/other.pdf\x1fread-selection",),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )

    assert audit.operation == "read_admission"
    assert audit.audit_id == boundary_audit_id(
        job_id="job-7",
        agent_name="researcher",
        operation=READ_ADMISSION_OPERATION,
        sequence=0,
    )
    assert audit.status == "completed"
    assert audit.target_ids == ["target-1"]
    assert audit.selected_ids == ["https://lab.example/queue"]
    assert audit.returned_ids == ["read-abc"]
    assert audit.accepted_ids == ["read-abc"]
    assert audit.deferred_ids == ["https://lab.example/other.pdf"]
    assert audit.packet_fingerprint == PACKET_FINGERPRINT
    assert audit.configuration_fingerprint == CONFIGURATION_FINGERPRINT
    assert audit.claim_cluster_ids == []


def test_a_passage_selection_manifest_records_what_it_omitted() -> None:
    audit = build_boundary_audit(
        operation=PASSAGE_SELECTION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=1,
        input_ids=("read-abc",),
        selected_ids=("p-1",),
        returned_ids=("p-1",),
        accepted_ids=("p-1",),
        deferred_ids=("p-2",),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        status="deferred",
    )

    assert audit.operation == "passage_selection"
    assert audit.input_ids == ["read-abc"]
    assert audit.deferred_ids == ["p-2"]
    assert audit.status == "deferred"


def test_a_refused_cache_read_is_recorded_as_a_disposition() -> None:
    stored = _original_read()
    refused = validate_cached_read(
        stored,
        TEXT,
        expected_content_sha256=normalized_content_sha256(TEXT),
        version_eligible=False,
        validated_at=VALIDATED_AT,
    )
    assert refused is None

    disposition = EvidenceDisposition(
        item_id=stored.read_id,
        stage="read-selection",
        reason="stale_for_target",
        target_ids=["target-1"],
    )
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=(stored.requested_url,),
        returned_ids=(stored.read_id,),
        deferred_ids=(stored.read_id,),
        disposition_ids=(disposition.item_id,),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        status="deferred",
    )

    assert audit.accepted_ids == []
    assert audit.deferred_ids == [stored.read_id]
    assert audit.disposition_ids == [stored.read_id]
    assert merge_evidence_dispositions([], [disposition]) == [disposition]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", ""),
        ("agent_name", "   "),
        ("operation", ""),
        ("packet_fingerprint", ""),
        ("configuration_fingerprint", ""),
    ],
)
def test_a_manifest_without_its_required_scalars_is_rejected(
    field: str, value: str
) -> None:
    arguments: dict[str, object] = {
        "operation": READ_ADMISSION_OPERATION,
        "job_id": "job-7",
        "agent_name": "researcher",
        "sequence": 0,
        "input_ids": (),
        "packet_fingerprint": PACKET_FINGERPRINT,
        "configuration_fingerprint": CONFIGURATION_FINGERPRINT,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        build_boundary_audit(**arguments)  # type: ignore[arg-type]


def test_a_manifest_status_is_closed_and_a_sequence_is_bounded() -> None:
    arguments: dict[str, object] = {
        "operation": READ_ADMISSION_OPERATION,
        "job_id": "job-7",
        "agent_name": "researcher",
        "sequence": 0,
        "input_ids": (),
        "packet_fingerprint": PACKET_FINGERPRINT,
        "configuration_fingerprint": CONFIGURATION_FINGERPRINT,
    }

    with pytest.raises(ValueError):
        build_boundary_audit(**{**arguments, "status": "fine"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_boundary_audit(**{**arguments, "sequence": -1})  # type: ignore[arg-type]


def test_audit_ids_are_deterministic_and_schema_stamped() -> None:
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=(),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )
    repeated = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=(),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )
    other = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=1,
        input_ids=(),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )

    assert audit.audit_id == repeated.audit_id
    assert audit.audit_id != other.audit_id
    assert audit.schema_version == QUALITY_CONTRACT_VERSION
    assert audit.audit_id.startswith("audit-")


def test_conflicting_manifests_for_one_audit_are_a_conflict() -> None:
    arguments: dict[str, object] = {
        "operation": READ_ADMISSION_OPERATION,
        "job_id": "job-7",
        "agent_name": "researcher",
        "sequence": 0,
        "input_ids": ("https://lab.example/queue",),
        "packet_fingerprint": PACKET_FINGERPRINT,
        "configuration_fingerprint": CONFIGURATION_FINGERPRINT,
    }
    audit = build_boundary_audit(**arguments)  # type: ignore[arg-type]
    altered = audit.model_copy(update={"accepted_ids": ["read-abc"]})

    merged = merge_boundary_audits({audit.audit_id: audit}, {audit.audit_id: audit})
    assert merged == {audit.audit_id: audit}

    with pytest.raises(EvidenceIdentityConflict):
        merge_boundary_audits({audit.audit_id: audit}, {audit.audit_id: altered})


def test_a_missing_manifest_fails_a_replay_assertion() -> None:
    """A new-contract replay must fail loudly, never read as zero loss."""
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=(),
        packet_fingerprint=PACKET_FINGERPRINT,
        configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )

    assert require_boundary_manifest({audit.audit_id: audit}, audit.audit_id) == audit
    with pytest.raises(MissingBoundaryManifest):
        require_boundary_manifest({audit.audit_id: audit}, "audit-missing")
    with pytest.raises(MissingBoundaryManifest):
        require_boundary_manifest({}, audit.audit_id)


def test_the_read_registry_keeps_ids_independent_of_assessments() -> None:
    """Nothing on a read or evidence record is a mutable quality judgement."""
    stored = _original_read()
    unit = build_evidence_unit(
        read=stored, locator="p-1", excerpt=PASSAGE, origin="fact_checker"
    )

    assert set(ReadRecord.model_fields) == {
        "read_id",
        "requested_url",
        "resolved_url",
        "title",
        "reader",
        "retrieved_at",
        "content_sha256",
        "extraction_complete",
        "passages",
        "target_ids",
        "acquisition_kind",
        "origin_session_id",
        "version_validated_at",
    }
    assert set(EvidenceUnit.model_fields) == {
        "evidence_id",
        "read_id",
        "source_url",
        "source_title",
        "locator",
        "excerpt",
        "target_ids",
        "origin",
    }
    assert normalized_content_sha256(TEXT) not in unit.evidence_id
    assert normalize_source_url(stored.resolved_url) == stored.resolved_url


# --------------------------------------------------------------------------
# Task 4: read-derived metadata, transport relation, and retained works
# --------------------------------------------------------------------------

# One document that states its own publisher, year, and DOI the way a report's
# title page does. Every anchor the model may propose has to be found in text
# like this, never asserted from what the model believes it knows.
REPORT_TEXT = (
    "Grid Storage Outlook. Published by Example Lab. Example Lab measured "
    "that 1,200 MW of interconnection capacity was withheld in 2024. "
    "doi:10.1234/grid.2025"
)
REPORT_ANCHORS = {
    "issuer": "Example Lab",
    "doi": "10.1234/grid.2025",
    "year": "2025",
}


def _web_read(
    url: str = "https://lab.example/report",
    *,
    title: str = "Grid Storage Outlook",
    text: str = REPORT_TEXT,
    reader: str = "web_scraper",
    request_url: str | None = None,
    extraction_complete: bool = True,
    passages: dict[str, str] | None = None,
    target_ids: tuple[str, ...] = (),
    retrieved_at: str = RETRIEVED_AT,
) -> ReadRecord:
    """One read of a document, built through the shared read producer."""
    return build_read_record(
        session_id=SESSION_ID,
        reader=reader,
        requested_url=request_url or url,
        resolved_url=url,
        title=title,
        retrieved_at=retrieved_at,
        text=text,
        passages=passages or {"p-1": text},
        extraction_complete=extraction_complete,
        target_ids=target_ids,
    )


def test_a_mirror_read_keeps_the_issuer_it_evidences() -> None:
    """The serving host is not the publisher when the document names one."""
    original = _web_read("https://lab.example/report")
    mirror = _web_read("https://repository.example/mirror/report")

    assert read_serving_host(original) == "lab.example"
    assert read_serving_host(mirror) == "repository.example"
    assert canonical_publisher_id(
        read_metadata_row(original, anchors=REPORT_ANCHORS)
    ) == canonical_publisher_id(
        read_metadata_row(mirror, anchors=REPORT_ANCHORS)
    )


def test_identical_complete_content_resolves_one_work_across_hosts() -> None:
    """A copied report is one work, however many hosts serve it."""
    original = _web_read("https://lab.example/report")
    mirror = _web_read("https://repository.example/mirror/report")

    works = resolve_read_works([original, mirror])

    assert works[original.read_id] == works[mirror.read_id]
    assert works[original.read_id] == f"sha256:{original.content_sha256}"


def test_a_different_document_is_a_different_work() -> None:
    first = _web_read("https://lab.example/report")
    second = _web_read(
        "https://lab.example/other",
        title="Interconnection Queue",
        text="Interconnection Queue. Published by Example Lab. A different "
        "document about 800 MW of capacity in 2024.",
    )

    works = resolve_read_works([first, second])

    assert works[first.read_id] != works[second.read_id]


def test_a_partial_read_establishes_no_publisher_and_no_work() -> None:
    """Section 2.2/2.3: an incomplete extraction is never an identity edge.

    It stays admissible evidence for the pages it read, but it may not join a
    work or mint a publisher, because the pages it never saw could say
    anything — including that it is a different document from the one it looks
    like.
    """
    partial = _web_read(extraction_complete=False, reader="document_reader")
    complete = _web_read()

    partial_row = read_metadata_row(partial, anchors=REPORT_ANCHORS)

    assert partial_row == {"source_id": partial.read_id}
    assert canonical_publisher_id(partial_row) is None
    works = resolve_read_works([partial, complete])
    assert works[partial.read_id] != works[complete.read_id]
    assert works[partial.read_id].startswith("unresolved-")


def test_an_issuer_the_read_never_states_is_rejected() -> None:
    """A model-proposed anchor is only accepted when the document shows it."""
    read = _web_read()

    row = read_metadata_row(
        read,
        anchors={"issuer": "Acme Corporation", "doi": "10.9999/invented"},
    )

    assert "issuer" not in row
    assert "doi" not in row
    assert rejected_anchor_names(
        read, {"issuer": "Acme Corporation", "doi": "10.9999/invented"}
    ) == ["doi", "issuer"]


def test_an_evidenced_alias_is_accepted_and_names_the_work() -> None:
    read = _web_read()

    row = read_metadata_row(read, anchors=REPORT_ANCHORS)
    identity = resolve_work_identities([row])[read.read_id]

    assert row["issuer"] == "Example Lab"
    assert identity.key == "doi:10.1234/grid.2025"
    assert identity.basis == "shared normalized DOI"


def test_a_title_mention_does_not_transfer_issuer_ownership() -> None:
    """Naming an organization is not publishing: attribution must be stated.

    The article below is published by Example Lab and is *about* Acme. A model
    that reads "Acme" in the headline and returns it as the issuer would hand
    every publisher's article about a company to that company — which is how a
    third-party page becomes "the company's own statement".
    """
    read = _web_read(
        "https://news.example/analysis",
        title="Acme's battery recall, analysed by Example Lab",
        text=(
            "Acme's battery recall, analysed by Example Lab. Acme recalled "
            "4,000 packs in 2024. Published by Example Lab. doi:10.1234/"
            "grid.2025"
        ),
    )

    proposed = {"issuer": "Acme", "doi": "10.1234/grid.2025"}
    row = read_metadata_row(read, anchors=proposed)

    assert "issuer" not in row
    assert rejected_anchor_names(read, proposed) == ["issuer"]
    # The blog that served it is the only publisher identity there is.
    assert canonical_publisher_id(row) == publisher_identity(read.resolved_url)


def test_a_year_the_read_never_carries_is_rejected() -> None:
    read = _web_read()

    row = read_metadata_row(read, anchors={"year": "2019"})

    assert "year" not in row
    # The read states 2024 on its own title text; that is the year it carries.
    assert read_metadata_row(read, anchors={"year": "2024"})["year"] == "2024"
    # The year inside its DOI is the identifier's own number, not the
    # document's date: "doi:10.1234/grid.2025" registers a work, and reading
    # 2025 from it would date the document from an identifier (the same
    # fabrication class as reading 2026 from a product code).
    assert read_metadata_row(read, anchors={"year": "2025"}).get("year") is None


def test_a_body_mention_does_not_transfer_issuer_ownership() -> None:
    """A quoted organization is not the publisher of the page quoting it.

    "As reported by Acme" names whose figure is being repeated. Reading that
    as Acme's own publication would hand every third-party article about a
    company to that company, and would let it inherit the article's authority.
    """
    read = _web_read(
        "https://lab.example/analysis",
        title="Grid storage, analysed",
        text=(
            "Grid storage, analysed. As reported by Acme, 1,200 MW was "
            "withheld during 2024. Published by Example Lab."
        ),
    )

    proposed = {"issuer": "Acme"}
    row = read_metadata_row(read, anchors=proposed)

    assert "issuer" not in row
    assert rejected_anchor_names(read, proposed) == ["issuer"]
    assert (
        read_metadata_row(read, anchors={"issuer": "Example Lab"})["issuer"]
        == "Example Lab"
    )


def test_a_stated_attribution_phrase_is_accepted_with_its_fillers() -> None:
    """Genuine publication phrasing still resolves, however it is written."""
    for text in (
        "Report. Published by Example Lab on 2026-01-15.",
        "Report. Publisher: Example Lab.",
        "Report. Issued by Example Lab.",
        "Report. Prepared by Example Lab.",
        "Report. Copyright 2026 Example Lab.",
    ):
        read = _web_read(text=text)
        row = read_metadata_row(read, anchors={"issuer": "Example Lab"})
        assert row.get("issuer") == "Example Lab", text


def test_a_date_is_stored_at_the_precision_the_read_evidences() -> None:
    """A document that says "2026" cannot be stamped "2026-12-31".

    Only the year was read, so only the year may be recorded: a fabricated day
    and month would make the freshness judgement look far more precise than
    the evidence behind it.
    """
    year_only = _web_read(
        "https://lab.example/yearly",
        text="Yearly Report. Published by Example Lab in 2026.",
    )
    dated = _web_read(
        "https://lab.example/dated",
        text=(
            "Dated Report. Published by Example Lab on 2026-01-15. "
            "Example Lab measured 1,200 MW in 2024."
        ),
    )

    coarse = validated_temporal(
        year_only, publication_date="2026-12-31", status="current"
    )
    exact = validated_temporal(
        dated, publication_date="2026-01-15", status="current"
    )
    partial = validated_temporal(
        dated, publication_date="2026-06-01", status="current"
    )

    assert coarse.publication_date == "2026"
    assert coarse.status == "current"
    assert exact.publication_date == "2026-01-15"
    # A month the read never states falls back to the year it does state.
    assert partial.publication_date == "2026"
    # A prose date is recorded at the precision the read supports, not as
    # written: "January 2026" evidences 2026 and no particular day.
    assert (
        validated_temporal(
            dated, publication_date="January 2026", status="current"
        ).publication_date
        == "2026"
    )
    assert (
        validated_temporal(
            year_only, publication_date="2019", status="current"
        ).publication_date
        is None
    )


def test_a_merged_word_does_not_evidence_a_two_word_issuer() -> None:
    """Separate words do not merge into each other, in a name either.

    "Published by ExampleLab" is a different name from "Example Lab", for the
    same reason ``_identity_words`` refuses to merge two words: accepting it
    would let a string that never appears in the document become its issuer.
    """
    read = _web_read(
        "https://lab.example/merged",
        text="Merged Report. Published by ExampleLab on 2026-01-15.",
    )

    row = read_metadata_row(read, anchors={"issuer": "Example Lab"})

    assert "issuer" not in row
    # The document's own name, spelled as the document spells it, still
    # resolves — the separator has to be there, not be absent.
    assert read_metadata_row(read, anchors={"issuer": "ExampleLab"})[
        "issuer"
    ] == "ExampleLab"


def test_a_stated_period_stays_a_period() -> None:
    """A range is a period, not a date to be truncated to its first year.

    Dropping the second end of "2022-2024" is the same class of error as
    inventing a day: it destroys evidence the read states. The stored value is
    the period, normalised to one spelling, however the document wrote it.
    """
    hyphen = _web_read(
        "https://lab.example/hyphen",
        text=(
            "Period Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022-2024."
        ),
    )
    spelled = _web_read(
        "https://lab.example/spelled",
        text=(
            "Written Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022 to 2024."
        ),
    )
    dashed = _web_read(
        "https://lab.example/dashed",
        text=(
            "Dashed Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022\u20132024."
        ),
    )

    assert validated_temporal(
        hyphen, data_period="2022-2024", status="stale_data"
    ).data_period == "2022-2024"
    assert validated_temporal(
        spelled, data_period="2022 to 2024", status="stale_data"
    ).data_period == "2022-2024"
    assert validated_temporal(
        dashed, data_period="2022\u20132024", status="stale_data"
    ).data_period == "2022-2024"


def test_a_period_written_with_an_abbreviated_end_stays_a_period() -> None:
    """An abbreviation is still an end, and never a reason to drop it.

    "2022 to 24" is a period of two years, not the single year 2022. A
    two-digit year is unambiguous from the start's century — a period cannot
    end before it starts — so it is expanded to the full year and the value
    stays a period of two ends.
    """
    spelled = _web_read(
        "https://lab.example/abbreviated",
        text=(
            "Abbreviated Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022 to 24."
        ),
    )
    slashed = _web_read(
        "https://lab.example/slashed",
        text=(
            "Slashed Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022/24."
        ),
    )
    rolled = _web_read(
        "https://lab.example/rolled",
        text=(
            "Rolled Report. Published by Example Lab on 2026-01-15. "
            "The rule applied from 1998 to 02."
        ),
    )

    same_spelling = validated_temporal(
        spelled, data_period="2022 to 24", status="stale_data"
    )
    four_digits = validated_temporal(
        spelled, data_period="2022-2024", status="stale_data"
    )
    slashed_value = validated_temporal(
        slashed, data_period="2022/24", status="stale_data"
    )

    assert same_spelling.data_period == "2022-2024"
    assert four_digits.data_period == "2022-2024"
    assert slashed_value.data_period == "2022-2024"
    # The status follows the period that was preserved, both ends and all.
    assert same_spelling.status == "stale_data"
    assert four_digits.status == "stale_data"
    assert slashed_value.status == "stale_data"
    # "1998 to 02" is 2002, not 1902: the expansion never runs backwards.
    assert (
        validated_temporal(
            rolled, data_period="1998 to 02", status="stale_data"
        ).data_period
        == "1998-2002"
    )


def test_a_period_the_read_does_not_state_is_not_accepted() -> None:
    """A falsely accepted period is worse than a dropped one.

    Each of these contains digits that look like a period and is not one: a
    range embedded in a longer alphanumeric word, the first two ends of a
    longer chain, and a signed value. Reading any of them as the period would
    stamp a freshness judgement against a value the document never stated.
    """
    embedded = _web_read(
        "https://lab.example/embedded",
        text=(
            "Embedded Report. Published by Example Lab on 2026-01-15. "
            "The token ABC2022-2024XYZ appears in the index."
        ),
    )
    chained = _web_read(
        "https://lab.example/chained",
        text=(
            "Chained Report. Published by Example Lab on 2026-01-15. "
            "The index lists 2022-2024-2026."
        ),
    )
    signed = _web_read(
        "https://lab.example/signed",
        text=(
            "Signed Report. Published by Example Lab on 2026-01-15. "
            "The offset -2022-2024 is not a period."
        ),
    )

    for read in (embedded, chained, signed):
        temporal = validated_temporal(
            read, data_period="2022-2024", status="stale_data"
        )
        assert temporal.data_period is None, read.resolved_url
        assert temporal.status != "stale_data", read.resolved_url
        assert temporal.status == "unknown", read.resolved_url


def test_a_year_and_month_is_not_read_as_a_period() -> None:
    """``2026-12`` is December 2026, not the period 2026 through 2012.

    A two-digit component that can be a month is a month, and the distinction
    is never resolved by inventing a year: an abbreviated end is only read
    where the digits cannot be a month, and an ambiguous form the read does
    not disambiguate is dropped rather than expanded.
    """
    read = _web_read(
        "https://lab.example/monthly",
        text="Monthly Report. Published by Example Lab in 2026-12.",
    )

    dated = validated_temporal(
        read, publication_date="2026-12", status="current"
    )
    ambiguous = validated_temporal(
        read, data_period="2026/12", status="stale_data"
    )

    assert dated.publication_date == "2026-12"
    assert dated.status == "current"
    assert ambiguous.data_period is None
    assert ambiguous.status == "unknown"


def test_a_labelled_period_value_is_still_a_period() -> None:
    """A value that states a period is a period however it is introduced.

    "Data period: 2022 to 24" states the same period as "2022-2024"; a label,
    a prefix, surrounding punctuation and a trailing clause are all normal ways
    for a document to write a value, and none of them is a reason to collapse
    the period to its first year. A value that mentions a period *and* other
    numbers names two periods, so it is read as nothing rather than guessed at.
    """
    read = _web_read(
        "https://lab.example/labelled",
        text=(
            "Labelled Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022 to 24."
        ),
    )

    labelled = validated_temporal(
        read, data_period="Data period: 2022 to 24", status="stale_data"
    )
    prefixed = validated_temporal(
        read, data_period="Observation period 2022-2024", status="stale_data"
    )
    trailed = validated_temporal(
        read, data_period="2022-2024 (revised)", status="stale_data"
    )

    for temporal in (labelled, prefixed, trailed):
        assert temporal.data_period == "2022-2024"
        assert temporal.status == "stale_data"

    # Two periods in one value is a value that names a period twice, not a
    # period; N ends is never the first two of them.
    ambiguous = validated_temporal(
        read, data_period="2022-2024 and 2019-2020", status="stale_data"
    )
    assert ambiguous.data_period is None
    assert ambiguous.status == "unknown"


def test_an_abbreviated_period_end_must_expand_to_a_plausible_year() -> None:
    """A fabricated year is worse than a dropped period.

    "2026-13" is not the period 2026 through 2113: the two-digit end would
    have to roll into the next century to stay forward, and a document that
    wrote it meant a month it never spelled. "9999 to 00" has no year after it
    at all. Both are dropped, and a dropped period sets no freshness status.
    The positive direction still works: "1998 to 02" is genuinely 2002.
    """
    for text, value in (
        (
            "Odd Report. Published by Example Lab on 2026-01-15. "
            "The table reads 2026-13.",
            "2026-13",
        ),
        (
            "Edge Report. Published by Example Lab on 2026-01-15. "
            "The edge reads 9999 to 00.",
            "9999 to 00",
        ),
    ):
        temporal = validated_temporal(
            _web_read("https://lab.example/implausible", text=text),
            data_period=value,
            status="stale_data",
        )
        assert temporal.data_period is None, value
        assert temporal.status == "unknown", value

    rolled = _web_read(
        "https://lab.example/rolled-year",
        text=(
            "Rolled Report. Published by Example Lab on 2026-01-15. "
            "The rule applied from 1998 to 02."
        ),
    )
    assert (
        validated_temporal(
            rolled, data_period="1998 to 02", status="stale_data"
        ).data_period
        == "1998-2002"
    )


def test_only_a_year_the_read_states_with_token_boundaries_dates_it() -> None:
    """A year inside a longer word is not a year the document states.

    "ABC2026XYZ" evidences no publication year, and recording 2026 from it
    would date the document from a product code. A year notation is a
    different thing: "FY2026" is how a fiscal document writes its year, so the
    documented notations still date a document while every other embedded year
    does not.
    """
    embedded = _web_read(
        "https://lab.example/embedded-year",
        text="Embedded Report. Published by Example Lab. ABC2026XYZ in the index.",
    )
    fiscal = _web_read(
        "https://lab.example/fiscal",
        text="Fiscal Year Report FY2026. Published by Example Lab.",
    )
    calendar = _web_read(
        "https://lab.example/calendar",
        text="Calendar Year Report CY2026. Published by Example Lab.",
    )

    unreachable = validated_temporal(
        embedded, publication_date="2026", status="current"
    )
    asserted = validated_temporal(
        fiscal, publication_date="2026", status="current"
    )
    calendar_dated = validated_temporal(
        calendar, publication_date="2026", status="current"
    )

    assert unreachable.publication_date is None
    assert unreachable.status == "unknown"
    assert asserted.publication_date == "2026"
    assert asserted.status == "current"
    assert calendar_dated.publication_date == "2026"
    assert calendar_dated.status == "current"
    # The read's own dating signal moves with the notation: the notation is
    # what is stated, and what is not stated is not there.
    assert "2026" in read_dated_tokens(fiscal)
    assert "2026" not in read_dated_tokens(embedded)


def test_an_identifier_digit_run_never_dates_the_document() -> None:
    """A DOI and a URL path carry numbers, and none of them is a date.

    "10.1234/grid.2025" registers a work; "/2024/report" says where a file was
    filed. Reading either as the document's year would date it from an
    identifier, so no period and no date may be built out of them.
    """
    read = _web_read(
        "https://lab.example/2024/report",
        text=(
            "Registered Report. Published by Example Lab. "
            "doi:10.1234/grid.2025"
        ),
    )

    assert read_dated_tokens(read) == []


def test_a_value_the_read_never_states_keeps_no_part_of_itself() -> None:
    """A rejected value contributes nothing, not even its first component.

    "2026/12" is ambiguous between a year and a month and a period, so it is
    dropped whole. "2026-13" and "9999 to 00" name numbers no calendar has,
    so they are dropped whole too — none of the three leaves its first year
    behind with a freshness judgement resting on it.
    """
    read = _web_read(
        "https://lab.example/yearly",
        text="Yearly Report. Published by Example Lab in 2026.",
    )

    for value in ("2026/12", "2026-13", "9999 to 00"):
        temporal = validated_temporal(
            read, data_period=value, status="stale_data"
        )
        assert temporal.data_period is None, value
        assert temporal.status == "unknown", value


def test_a_sentence_final_dot_does_not_block_a_date() -> None:
    """"in 2026." ends a sentence and still states the year 2026.

    A dot is punctuation of an identifier ("10.1234/grid.2025"), and it is
    also how a sentence ends. Only the first kind may keep a date from being
    read, so the token boundary has to look at what follows the dot.
    """
    year = _web_read(
        "https://lab.example/period-end",
        text="Year end. Published by Example Lab in 2026.",
    )
    month = _web_read(
        "https://lab.example/month-end",
        text="Month end. Published by Example Lab in 2026-12.",
    )

    assert validated_temporal(
        year, publication_date="2026", status="current"
    ).publication_date == "2026"
    assert validated_temporal(
        month, publication_date="2026-12", status="current"
    ).publication_date == "2026-12"
    # A versioned number is not a sentence: the dot there is the identifier's.
    assert read_dated_tokens(
        _web_read(
            "https://lab.example/versioned",
            text="Versioned Report. Release 2026-12.5 is current.",
        )
    ) == []


def test_a_freshness_status_follows_the_period_it_was_computed_from() -> None:
    """The status is judged against the value that was kept, not a truncation.

    A period the read states keeps both its ends and the status that rests on
    it. A period the read does not state is dropped entirely, and the status
    that had nothing left to rest on becomes ``unknown`` rather than being
    computed against a value that was never recorded.
    """
    read = _web_read(
        "https://lab.example/period",
        text=(
            "Period Report. Published by Example Lab on 2026-01-15. "
            "The survey covers 2022-2024."
        ),
    )

    preserved = validated_temporal(
        read, data_period="2022-2024", status="stale_data"
    )
    dropped = validated_temporal(
        read, data_period="1999-2001", status="stale_data"
    )

    assert preserved.data_period == "2022-2024"
    assert preserved.status == "stale_data"
    assert dropped.data_period is None
    assert dropped.status == "unknown"


def test_the_assessment_revision_tracks_content_metadata_and_time() -> None:
    """The reuse key is content, metadata *and* the dates the read carries."""
    baseline = _web_read()
    same = _web_read()
    other_title = _web_read(title="Grid Storage Outlook (revised)")
    other_text = _web_read(
        text=REPORT_TEXT.replace("2024", "2019").replace(
            "doi:10.1234/grid.2025", "doi:10.1234/grid.2019"
        )
    )

    revision = read_assessment_revision(baseline)

    assert revision.startswith("assess-")
    # Reuse is not URL-keyed: only an identical read may share a revision.
    assert read_assessment_revision(same) == revision
    assert read_assessment_revision(other_title) != revision
    assert read_assessment_revision(other_text) != revision


def test_a_url_alone_never_shares_an_assessment_revision() -> None:
    """Two reads of one URL with different bodies are two assessments."""
    first = _web_read()
    second = _web_read(
        text=REPORT_TEXT + " The 2026 edition revised the 2024 figure."
    )

    assert first.read_id != second.read_id
    assert read_assessment_revision(first) != read_assessment_revision(second)


def test_retained_works_collapse_mirrors_and_never_invent_identity() -> None:
    """A works count that is derived from identity, not from URLs."""
    original = _web_read("https://lab.example/report")
    mirror = _web_read("https://repository.example/mirror/report")
    other = _web_read(
        "https://lab.example/other",
        title="Interconnection Queue",
        text="Interconnection Queue. Published by Example Lab. Different.",
    )

    assert (
        retained_work_count(
            [original.resolved_url, mirror.resolved_url],
            [original, mirror],
        )
        == 1
    )
    assert (
        retained_work_count(
            [
                original.resolved_url,
                mirror.resolved_url,
                other.resolved_url,
                "https://unread.example/never-read",
            ],
            [original, mirror, other],
        )
        == 3
    )


def test_the_dated_signals_are_read_from_the_document() -> None:
    """The read's years are tokens it states, never numbers inside an identifier.

    "2024" is the year the document states about itself. The "2025" in its DOI
    is the identifier's own number and the "1234" beside it is a registered
    prefix: neither is the document dating itself, so neither is read as a
    year the read carries.
    """
    read = _web_read()

    assert read_dated_tokens(read) == ["2024"]
