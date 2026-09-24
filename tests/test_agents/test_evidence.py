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
    EvidenceEligibility,
    EvidenceIdentityConflict,
    MissingBoundaryManifest,
    TemporalClaim,
    boundary_audit_id,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    canonical_publisher_id,
    canonical_read_text,
    eligible_independent_pair,
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


def test_two_complete_hashes_under_one_doi_are_one_work() -> None:
    """The PDF and the HTML of one DOI are two renderings, not a conflict.

    Section 2.2 rules 2/4: distinct bytes never prove distinct works, and one
    registered identifier says they are one. Both hashes stay as aliases, so
    either rendering found later still joins the work.
    """
    rows = [
        {"source_id": "pdf", "doi": "10.1234/abc", "complete_content_sha256": "a" * 64},
        {"source_id": "html", "doi": "10.1234/abc", "complete_content_sha256": "b" * 64},
    ]
    resolved = resolve_work_identities(rows)

    for source_id in ("pdf", "html"):
        assert resolved[source_id].key == "doi:10.1234/abc"
        assert resolved[source_id].identity_status == "known"
        assert set(resolved[source_id].aliases) == {
            "doi:10.1234/abc",
            f"sha256:{'a' * 64}",
            f"sha256:{'b' * 64}",
        }


def test_two_complete_hashes_under_two_issuers_report_numbers_stay_a_conflict() -> (
    None
):
    """Without one registered identifier holding them together, bytes differ.

    Two issuers' report numbers each name a work in their own namespace, and
    they do not say the two bodies are one work, so the rule that forgives
    distinct hashes does not apply.
    """
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
            "report_number": "TR-1",
            "complete_content_sha256": "b" * 64,
        },
        {
            "source_id": "c",
            "issuer": "Other Org",
            "report_number": "TR-9",
            "complete_content_sha256": "b" * 64,
        },
    ]
    resolved = resolve_work_identities(rows)

    assert resolved["a"].key is None
    assert resolved["a"].identity_status == "conflicting"


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


# Three passages of the EIA Today in Energy page the audited run read
# (detail.php?id=64586): site navigation, the data-source note, and the
# paragraph that carries the 18.2 GW forecast.
_EIA_NAVIGATION = (
    "Solar, battery storage to lead new U.S. generating capacity additions in "
    "2025 - U.S. Energy Information Administration (EIA) Skip to "
    "sub-navigation U.S. Energy Information Administration - EIA - Independent "
    "Statistics and Analysis Menu Statistics Analysis Tools Education News "
    "Search Today in Energy Skip to page content Recent articles Browse by tag "
    "liquid fuels natural gas electricity oil/petroleum production/supply "
    "crude oil consumption/demand generation prices map states exports/imports "
    "international coal renewables weather forecasts/projections gasoline "
    "capacity steo (short-term energy outlook) Prices Archive About Glossary "
    "FAQS In-brief analysis February 24, 2025"
)
_EIA_METHODS = (
    "Data source: Preliminary Monthly Electric Generator Inventory. The "
    "inventory covers utility-scale generators of 1 megawatt or greater, "
    "including hybrid co-located plants, and is published monthly with a "
    "three-month lag behind the reporting period it describes."
)
_EIA_BATTERY_PARAGRAPH = (
    "Battery storage. In 2025, capacity growth from battery storage could set "
    "a record as we expect 18.2 GW of utility-scale battery storage to be "
    "added to the grid. U.S. battery storage already achieved record growth "
    "in 2024 when power providers added 10.3 GW of new battery storage "
    "capacity. This growth highlights the importance of battery storage when "
    "used with renewable energy, helping to balance supply and demand and "
    "improve grid stability."
)


def test_a_dossier_shows_the_reads_passages_not_the_top_of_the_page() -> None:
    """The evaluator judges the document, not a 400-character head of it.

    Audit finding #1's source half: ess-news was scored 0.10 "low", and "the
    substantive 2024 capacity number is not visible" was published about it,
    while its stored read carries the 18.2 GW sentence 2,215 characters in.
    The dossier's excerpts are whole passages in document order, and a query
    puts the passages the source is being judged for first.
    """
    from deep_research.agents.evidence import build_read_dossiers

    nav = _EIA_NAVIGATION
    methods = _EIA_METHODS
    figure = _EIA_BATTERY_PARAGRAPH
    read = _web_read(
        "https://www.eia.gov/todayinenergy/detail.php?id=64586",
        title="Solar, battery storage to lead new U.S. capacity additions",
        text="\n\n".join((nav, methods, figure)),
        passages={"chunk-0": nav, "chunk-1": methods, "chunk-2": figure},
    )

    (dossier,) = build_read_dossiers([read])

    # Document order, and the passage that carries the figure arrives whole:
    # the 400-character head it used to be clipped to stops before the
    # sentence with the number in it.
    assert len(dossier.excerpts) == 3
    assert dossier.excerpts[0].startswith("Solar, battery storage to lead")
    assert dossier.excerpts[1] == " ".join(methods.split())
    assert dossier.excerpts[2] == " ".join(figure.split())
    assert len(dossier.excerpts[2]) > 400

    (ranked,) = build_read_dossiers(
        [read],
        queries={
            read.resolved_url: "18.2 GW battery storage forecast for 2025"
        },
    )

    assert ranked.excerpts[0] == " ".join(figure.split())


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


# The audited run's own EIA read: the agency's Today in Energy page, whose
# title names the agency and prints the acronym its host is named by. There is
# no "published by" imprint anywhere on the page, which is the whole difficulty:
# the one document that states the answer carries no attribution phrase, so the
# issuer anchor was dropped and the agency's own report could corroborate
# nothing.
AGENCY_URL = "https://www.eia.gov/todayinenergy/detail.php?id=67925"
AGENCY_TITLE = (
    "Battery storage capacity averaged 70% growth over the last three years - "
    "U.S. Energy Information Administration (EIA)"
)
AGENCY_TEXT = (
    "Battery storage capacity averaged 70% growth over the last three years - "
    "U.S. Energy Information Administration (EIA) In-brief analysis August 7, "
    "2026 Battery storage capacity averaged 70% growth over the last three "
    "years Data source: U.S. Energy Information Administration, Preliminary "
    "Monthly Electric Generator Inventory By the end of 2025, the U.S. power "
    "system had operational battery storage capacity of 43.6 gigawatts (GW)."
)
AGENCY_ISSUER = "U.S. Energy Information Administration"

# The relay that quotes the same inventory: Energy Global's own page, which
# names the agency in its headline and quotes it by name in its body.
RELAY_URL = (
    "https://energyglobal.com/energy-storage/13032025/"
    "eia-find-that-us-battery-capacity-increased-by-66-in-2024"
)
RELAY_TITLE = (
    "EIA find that US battery capacity increased by 66% in 2024 | Energy Global"
)
RELAY_TEXT = (
    "EIA find that US battery capacity increased by 66% in 2024 | Energy Global "
    "In the US, cumulative utility-scale battery storage capacity exceeded "
    "26 GW in 2024, according to the US Energy Information Administration "
    "(EIA)'s 'January 2025 Preliminary Monthly Electric Generator Inventory'. "
    "Generators added 10.4 GW of new battery storage capacity in 2024."
)


def test_a_first_party_read_evidences_the_issuer_its_host_is_named_by() -> None:
    """The agency's own page publishes itself: its title and host say so.

    EIA's Today in Energy page states the answer and carries no attribution
    phrase, so the issuer the model read off its masthead was dropped — and
    with it the source's role, its origin, and every chance its own report
    could ever be half of a corroboration pair. The page is served from the
    domain the agency is named by and its title names the agency and prints
    the acronym (EIA) that domain spells, so the anchor is evidenced after
    all. A relay cannot claim the same: its host is not the issuer's.
    """
    read = _web_read(AGENCY_URL, title=AGENCY_TITLE, text=AGENCY_TEXT)
    proposed = {"issuer": AGENCY_ISSUER}

    row = read_metadata_row(read, anchors=proposed)

    assert row["issuer"] == AGENCY_ISSUER
    assert canonical_publisher_id(row) == "u s energy information administration"
    assert rejected_anchor_names(read, proposed) == []


def test_a_relay_never_evidences_the_issuer_it_quotes() -> None:
    """A relay's host is not the agency's, so its mentions stay mentions.

    Energy Global names the agency by acronym in its headline and quotes it by
    name in its body. Accepting either would hand the relay the agency's
    authority and let two relays of one press release stand in as two
    first-party reports.
    """
    read = _web_read(RELAY_URL, title=RELAY_TITLE, text=RELAY_TEXT)
    proposed = {"issuer": AGENCY_ISSUER}

    row = read_metadata_row(read, anchors=proposed)

    assert "issuer" not in row
    assert rejected_anchor_names(read, proposed) == ["issuer"]
    assert canonical_publisher_id(row) == "energyglobal.com"


def test_a_domain_that_only_spells_the_acronym_is_not_the_issuer() -> None:
    """The host must *be* the spelling the issuer is named by, not contain it.

    "eia-digest.example" is a republisher whose name echoes the agency's. The
    domain label is the whole of the identity check, so a label that merely
    contains the acronym — like a title that merely mentions the name —
    publishes nobody.
    """
    read = _web_read(
        "https://eia-digest.example/today",
        title=AGENCY_TITLE,
        text=AGENCY_TEXT,
    )

    assert "issuer" not in read_metadata_row(read, anchors={"issuer": AGENCY_ISSUER})


def test_a_lookalike_host_is_not_the_issuer_however_the_title_reads() -> None:
    """The registrable domain has to be the issuer's own, suffix and all.

    "eia.news" is a commercial registration whose label spells the agency's
    acronym, and a page there can copy the agency's masthead word for word.
    The label alone cannot tell the two apart; the suffix is the half a
    registrant cannot choose, and ".gov" is issued to government bodies only
    while ".news" is sold to anyone. Accepting the lookalike would let any
    registrant publish as the agency.
    """
    read = _web_read(
        "https://eia.news/batteries",
        title="U.S. Energy Information Administration (EIA) says batteries grew",
        text=(
            "U.S. Energy Information Administration (EIA) says batteries grew "
            "Battery storage capacity grew by 70% in 2025."
        ),
    )

    assert "issuer" not in read_metadata_row(read, anchors={"issuer": AGENCY_ISSUER})


def test_a_commercial_domain_is_refused_even_when_its_title_names_it() -> None:
    """A .com masthead is a claim, not an ownership record.

    cleanedge.com's own title does name Clean Edge, and on an institutional
    suffix that would be first-party evidence. Nothing about a .com
    registration is controlled — "cleanedge.news" and "clean-edge.com" are one
    purchase away — so the same match would accept a squatter, and the rule
    refuses the commercial case deliberately. The cost is bounded: the pages
    it keeps are the institutionally served ones the audited run needed.
    """
    read = _web_read(
        "https://cleanedge.com/data-dive/"
        "u-s-electric-utility-scale-capacity-additions-by-fuel-type-2",
        title=(
            "U.S. Electric Utility-Scale Capacity Additions, by Fuel Type - "
            "Clean Edge"
        ),
        text=(
            "U.S. Electric Utility-Scale Capacity Additions, by Fuel Type - "
            "Clean Edge Clean Edge reported that 10.3 GW was added in 2024."
        ),
    )

    assert "issuer" not in read_metadata_row(read, anchors={"issuer": "Clean Edge"})


def test_a_national_institutional_suffix_still_carries_a_first_party_claim() -> None:
    """The rule is "institutionally registered", not "American".

    ons.gov.uk is the Office for National Statistics' own domain — the .gov.uk
    registry issues to public bodies — and its title names the office and
    prints the acronym its label spells, so the same evidence a .gov page
    carries is admitted here too.
    """
    read = _web_read(
        "https://www.ons.gov.uk/economy/energy",
        title="Energy storage in the UK - Office for National Statistics (ONS)",
        text=(
            "Energy storage in the UK - Office for National Statistics (ONS) "
            "Storage capacity grew by 1.2 GW in 2025."
        ),
    )

    row = read_metadata_row(
        read, anchors={"issuer": "Office for National Statistics"}
    )

    assert row["issuer"] == "Office for National Statistics"


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


def test_a_period_inside_a_longer_token_is_not_accepted() -> None:
    """A falsely accepted period is worse than a dropped one.

    "ABC2022-2024XYZ" is a product code and "-2022-2024" is a signed offset:
    both quotes are verbatim in the document and neither states a period, so a
    value that reads two ends out of their digits is dropped and stamps no
    freshness judgement against a period nobody wrote.
    """
    for url, text, quote in (
        (
            "https://lab.example/embedded",
            "Embedded Report. The token ABC2022-2024XYZ appears in the index.",
            "The token ABC2022-2024XYZ appears in the index.",
        ),
        (
            "https://lab.example/signed",
            "Signed Report. The offset -2022-2024 is not a period.",
            "The offset -2022-2024 is not a period.",
        ),
    ):
        read = _web_read(url, text=text)

        temporal = validated_temporal(
            read, data_period=_claim("2022-2024", quote), status="stale_data"
        )

        assert temporal.data_period is None, url
        assert temporal.status == "unknown", url


def test_a_year_and_month_is_a_date_and_not_a_period() -> None:
    """``2026-12`` is December 2026, not the period 2026 through 2012.

    A two-digit component that can be a month is a month, and the slash form
    that could equally be either is dropped rather than expanded into a year
    nobody wrote.
    """
    read = _web_read(
        "https://lab.example/monthly",
        text="Monthly Report. Published by Example Lab in 2026-12.",
    )

    dated = validated_temporal(
        read,
        publication_date=_claim(
            "2026-12", "Published by Example Lab in 2026-12."
        ),
        status="current",
    )
    ambiguous = validated_temporal(
        read,
        data_period=_claim("2026/12", "Published by Example Lab in 2026-12."),
        status="stale_data",
    )

    assert dated.publication_date == "2026-12"
    assert dated.status == "current"
    assert ambiguous.data_period is None
    assert ambiguous.status == "unknown"


def test_a_sentence_final_dot_does_not_block_a_date() -> None:
    """"in 2026." ends a sentence and still states the year 2026.

    A dot is punctuation of an identifier ("10.1234/grid.2025") and it is also
    how a sentence ends. Only the first kind may keep a date from being read, so
    the boundary has to look at what follows the dot — and a version number is
    the first kind, so it dates nothing.
    """
    year = _web_read(
        "https://lab.example/period-end",
        text="Year end. Published by Example Lab in 2026.",
    )
    month = _web_read(
        "https://lab.example/month-end",
        text="Month end. Published by Example Lab in 2026-12.",
    )

    assert read_dated_tokens(year) == ["2026"]
    assert read_dated_tokens(month) == ["2026", "2026-12"]
    assert (
        validated_temporal(
            year,
            publication_date=_claim("2026", "Published by Example Lab in 2026."),
            status="current",
        ).publication_date
        == "2026"
    )
    # A versioned number is not a sentence: the dot there is the identifier's.
    assert (
        read_dated_tokens(
            _web_read(
                "https://lab.example/versioned",
                text="Versioned Report. Release 2026-12.5 is current.",
            )
        )
        == []
    )


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


def test_the_dated_signals_are_read_from_the_document() -> None:
    """The read's years are tokens it states, never numbers inside an identifier.

    "2024" is the year the document states about itself. The "2025" in its DOI
    is the identifier's own number and the "1234" beside it is a registered
    prefix: neither is the document dating itself, so neither is read as a
    year the read carries.
    """
    read = _web_read()

    assert read_dated_tokens(read) == ["2024"]


# --------------------------------------------------------------------------
# quote-first temporal grounding
# --------------------------------------------------------------------------
#
# A temporal value arrives from the model as free text, and the document's own
# words are what admits it: every field carries a verbatim quote that has to
# exist in the read, and the value has to be exactly what that quote states.
# Local code checks containment and consistency and nothing else — it never
# scans the document for a spelling of the date it was told to expect, because
# that scan is where four rounds of fabricated dates came from.


def _claim(value: str, quote: str) -> TemporalClaim:
    """One model-proposed temporal value with the quote it read it from."""
    return TemporalClaim(value=value, quote=quote)


def _dated(text: str, url: str = "https://lab.example/dated") -> ReadRecord:
    """One read whose own text is the dating haystack."""
    return _web_read(url, text=text)


def test_a_quote_the_read_does_not_state_is_not_admitted() -> None:
    """The model proposes; the read admits. A quote nobody wrote is nothing."""
    read = _dated("Undated Report. Published by Example Lab. No date is printed.")

    for quote in ("Published by Example Lab on 1998-05-14.", "1998-05-14"):
        temporal = validated_temporal(
            read,
            publication_date=_claim("1998-05-14", quote),
            status="current",
        )
        assert temporal.publication_date is None, quote
        assert temporal.status == "unknown", quote


def test_a_value_the_quote_does_not_state_is_not_admitted() -> None:
    """A quote cannot be stretched to a value it does not spell.

    The read states the year 2026 and no day, so no day may be recorded: the
    quote has to state the value, and a date the model would have preferred is
    not one the document wrote.
    """
    read = _dated("Yearly Report. Published by Example Lab in 2026.")

    fabricated = validated_temporal(
        read,
        publication_date=_claim(
            "2026-12-31", "Published by Example Lab in 2026."
        ),
        status="current",
    )
    invented = validated_temporal(
        read,
        publication_date=_claim("2019", "Published by Example Lab in 2026."),
        status="current",
    )

    assert fabricated.publication_date is None
    assert fabricated.status == "unknown"
    assert invented.publication_date is None
    assert invented.status == "unknown"


def test_a_verified_quote_is_recorded_at_the_precision_it_states() -> None:
    """The document's own words set the precision, in both directions."""
    year_only = _dated("Yearly Report. Published by Example Lab in 2026.")
    exact = _dated(
        "Dated Report. Published by Example Lab on 2026-01-15.",
        url="https://lab.example/exact",
    )

    admitted = validated_temporal(
        year_only,
        publication_date=_claim("2026", "Published by Example Lab in 2026."),
        status="current",
    )
    full = validated_temporal(
        exact,
        publication_date=_claim(
            "2026-01-15", "Published by Example Lab on 2026-01-15."
        ),
        status="current",
    )
    finer = validated_temporal(
        exact,
        publication_date=_claim(
            "2026-06-01", "Published by Example Lab on 2026-01-15."
        ),
        status="current",
    )

    assert admitted.publication_date == "2026"
    assert admitted.status == "current"
    assert full.publication_date == "2026-01-15"
    assert full.status == "current"
    # A day the quote does not state is not a day the read evidences.
    assert finer.publication_date is None
    assert finer.status == "unknown"


def test_a_date_the_document_spells_in_words_keeps_its_own_precision() -> None:
    """A page that writes its date in words dates itself by that day.

    The audited run's EIA pages carry "In-brief analysis August 7, 2026" and
    "March 12, 2025", and the evaluator's instruction reduced every one of
    them to its year, so two releases of one series could not be ranked
    against each other. The quote states the day, so the day is what is
    recorded — and a day the words never name is still refused.
    """
    read = _dated(
        "Battery storage capacity grew. In-brief analysis August 7, 2026 "
        "Battery storage capacity averaged 70% growth over three years.",
        url="https://eia.gov/todayinenergy/detail.php?id=67925",
    )
    quote = "In-brief analysis August 7, 2026"

    spelled = validated_temporal(
        read,
        publication_date=_claim("2026-08-07", quote),
        status="current",
    )
    year = validated_temporal(
        read,
        publication_date=_claim("2026", quote),
        status="current",
    )
    other_day = validated_temporal(
        read,
        publication_date=_claim("2026-08-08", quote),
        status="current",
    )

    assert spelled.publication_date == "2026-08-07"
    assert spelled.status == "current"
    # The coarser value a finer quote states stays admissible.
    assert year.publication_date == "2026"
    # A day the words do not name is not a day the quote states.
    assert other_day.publication_date is None
    assert other_day.status == "unknown"


def test_a_spelled_month_and_year_is_a_date_at_that_precision() -> None:
    """The inventory's own edition, and two ends a page spells as a period."""
    read = _dated(
        "Data source: U.S. Energy Information Administration, Preliminary "
        "Monthly Electric Generator Inventory, January 2025. Registration "
        "opens September 29, 2026 - November 12, 2026 for the series.",
        url="https://eia.gov/todayinenergy/detail.php?id=64705",
    )

    edition = validated_temporal(
        read,
        data_period=_claim(
            "2025-01",
            "Preliminary Monthly Electric Generator Inventory, January 2025",
        ),
        status="stale_data",
    )
    period = validated_temporal(
        read,
        effective_date=_claim(
            "2026-09-29-2026-11-12", "September 29, 2026 - November 12, 2026"
        ),
        status="effective",
    )
    inconsistent = validated_temporal(
        read,
        data_period=_claim(
            "2025-02",
            "Preliminary Monthly Electric Generator Inventory, January 2025",
        ),
        status="stale_data",
    )

    assert edition.data_period == "2025-01"
    assert edition.status == "stale_data"
    assert period.effective_date == "2026-09-29-2026-11-12"
    assert period.status == "effective"
    assert inconsistent.data_period is None
    assert inconsistent.status == "unknown"

def test_a_periods_second_end_is_not_also_a_stand_alone_month() -> None:
    """Re-scanning the words never states a period's end a second time alone.

    "January 2024 through March 2025" states the period; the words do not
    separately state "March 2025" as a stand-alone month the way they would
    if nothing joined it to January.
    """
    read = _dated(
        "Coverage Report. Deployment ran January 2024 through March 2025 "
        "across the program."
    )
    quote = "January 2024 through March 2025"

    period = validated_temporal(
        read, data_period=_claim("2024-01-2025-03", quote), status="stale_data"
    )
    end_alone = validated_temporal(
        read, data_period=_claim("2025-03", quote), status="stale_data"
    )

    assert period.data_period == "2024-01-2025-03"
    assert period.status == "stale_data"
    # A stated period's own second end is not a month the words state alone.
    assert end_alone.data_period is None
    assert end_alone.status == "unknown"


def test_a_bare_month_joined_to_a_dated_end_states_no_finer_than_the_year() -> None:
    """A period whose start borrows the end's year states only that year.

    "January to March 2025" and "Jan-Mar 2025" never give their start month
    a year of its own, and "between June and August 2024" joins with "and",
    the one connective a digit period never uses. None of them states its
    end's own month as a fact by itself -- the words would have to write
    "March 2025" or "August 2024" unjoined for that.
    """
    to_read = _dated("Enrollment ran January to March 2025 for the pilot.")
    hyphen_read = _dated(
        "Enrollment ran Jan-Mar 2025 for the pilot.",
        url="https://lab.example/jan-mar",
    )
    and_read = _dated(
        "Enrollment ran between June and August 2024 for the pilot.",
        url="https://lab.example/june-august",
    )

    to_year = validated_temporal(
        to_read,
        publication_date=_claim("2025", "January to March 2025"),
        status="current",
    )
    to_month = validated_temporal(
        to_read,
        publication_date=_claim("2025-03", "January to March 2025"),
        status="current",
    )
    hyphen_year = validated_temporal(
        hyphen_read,
        publication_date=_claim("2025", "Jan-Mar 2025"),
        status="current",
    )
    hyphen_month = validated_temporal(
        hyphen_read,
        publication_date=_claim("2025-03", "Jan-Mar 2025"),
        status="current",
    )
    and_year = validated_temporal(
        and_read,
        publication_date=_claim("2024", "between June and August 2024"),
        status="current",
    )
    and_month = validated_temporal(
        and_read,
        publication_date=_claim("2024-08", "between June and August 2024"),
        status="current",
    )

    assert to_year.publication_date == "2025"
    assert to_year.status == "current"
    assert to_month.publication_date is None
    assert to_month.status == "unknown"
    assert hyphen_year.publication_date == "2025"
    assert hyphen_month.publication_date is None
    assert and_year.publication_date == "2024"
    assert and_month.publication_date is None


def test_the_modal_verb_may_is_not_read_as_the_month() -> None:
    """"may 2025" is ordinary prose; only a capitalised spelling names May."""
    lowercase = _dated(
        "Board Notes. Results may 2025 look different from today's.",
        url="https://lab.example/modal-may",
    )
    capitalised = _dated(
        "Board Notes. Results were finalised May 2025.",
        url="https://lab.example/proper-may",
    )

    modal = validated_temporal(
        lowercase,
        publication_date=_claim("2025-05", "may 2025"),
        status="current",
    )
    month = validated_temporal(
        capitalised,
        publication_date=_claim("2025-05", "May 2025"),
        status="current",
    )

    assert modal.publication_date is None
    assert modal.status == "unknown"
    assert month.publication_date == "2025-05"
    assert month.status == "current"



@pytest.mark.parametrize(
    "quote",
    [
        "Published by Example Lab in 2026.",
        "Published by Example Lab in 2026,",
        "Published by Example Lab in 2026;",
        "Published by Example Lab in (2026)",
        "Published by Example Lab in 2026)",
        "Published by Example Lab in 2026:",
    ],
)
def test_punctuation_around_a_quoted_date_does_not_reject_it(
    quote: str,
) -> None:
    """A quoted date is a token: sentence punctuation ends it, not the date.

    A full stop, a comma, a semicolon, a bracket and a colon are all how a
    document ends or separates a date it states, so none of them may cost the
    value its quote.
    """
    read = _dated(f"Punctuated Report. {quote}")

    temporal = validated_temporal(
        read, publication_date=_claim("2026", quote), status="current"
    )

    assert temporal.publication_date == "2026", quote
    assert temporal.status == "current", quote


def test_a_stated_period_keeps_both_its_ends() -> None:
    """A period is two ends, however the document writes the join."""
    hyphen = _dated("Period Report. The survey covers 2022-2024.")
    spelled = _dated(
        "Written Report. The survey covers 2022 to 2024.",
        url="https://lab.example/spelled",
    )
    dashed = _dated(
        "Dashed Report. The survey covers 2022\u20132024.",
        url="https://lab.example/dashed",
    )

    assert (
        validated_temporal(
            hyphen,
            data_period=_claim("2022-2024", "The survey covers 2022-2024."),
            status="stale_data",
        ).data_period
        == "2022-2024"
    )
    assert (
        validated_temporal(
            spelled,
            data_period=_claim("2022 to 2024", "The survey covers 2022 to 2024."),
            status="stale_data",
        ).data_period
        == "2022-2024"
    )
    assert (
        validated_temporal(
            dashed,
            data_period=_claim(
                "2022\u20132024", "The survey covers 2022\u20132024."
            ),
            status="stale_data",
        ).data_period
        == "2022-2024"
    )


def test_a_period_the_value_collapses_to_one_end_is_rejected() -> None:
    """A stated period is never recorded as its first end alone."""
    read = _dated("Period Report. The survey covers 2022-2024.")

    collapsed = validated_temporal(
        read,
        data_period=_claim("2022", "The survey covers 2022-2024."),
        status="stale_data",
    )
    reversed_period = validated_temporal(
        read,
        data_period=_claim("2024-2022", "The survey covers 2022-2024."),
        status="stale_data",
    )

    assert collapsed.data_period is None
    assert collapsed.status == "unknown"
    # A period never runs backwards, so the reversed value is not the period
    # the quote states either.
    assert reversed_period.data_period is None
    assert reversed_period.status == "unknown"


def test_a_period_the_quote_does_not_state_is_not_minted() -> None:
    """Two years a document never joined are not a period it states."""
    read = _dated(
        "Two Years. The 2022 survey was revised against 2024 figures."
    )

    temporal = validated_temporal(
        read,
        data_period=_claim(
            "2022-2024", "The 2022 survey was revised against 2024 figures."
        ),
        status="stale_data",
    )

    assert temporal.data_period is None
    assert temporal.status == "unknown"


def test_a_value_naming_part_of_a_longer_chain_is_not_admitted() -> None:
    """Three ends are not the first two of them."""
    read = _dated("Chained Report. The index lists 2022-2024-2026.")

    temporal = validated_temporal(
        read,
        data_period=_claim("2022-2024", "The index lists 2022-2024-2026."),
        status="stale_data",
    )

    assert temporal.data_period is None
    assert temporal.status == "unknown"


def test_an_identifier_never_dates_a_document() -> None:
    """A DOI, a URL path and a code carry numbers, and none of them is a date.

    "2025" inside "doi:10.1234/grid.2025" is the identifier's own number,
    "/2024/report" is where a file is filed, and "ABC2022-2024XYZ" is a product
    code: none of the three dates the document, mints a period, or supports a
    freshness judgement.
    """
    read = _web_read(
        "https://lab.example/2024/report",
        text=(
            "Registered Report. Published by Example Lab. "
            "doi:10.1234/grid.2025 See https://lab.example/2024/report for "
            "the file. The token ABC2022-2024XYZ indexes it."
        ),
    )
    bare_url = _web_read(
        "https://lab.example/2024/bare",
        text="Bare Report. Published by Example Lab. No date is printed.",
    )

    for field, value, quote in (
        ("publication_date", "2025", "doi:10.1234/grid.2025"),
        (
            "publication_date",
            "2024",
            "See https://lab.example/2024/report for the file.",
        ),
        ("data_period", "2022-2024", "The token ABC2022-2024XYZ indexes it."),
    ):
        temporal = validated_temporal(
            read, status="current", **{field: _claim(value, quote)}
        )
        assert getattr(temporal, field) is None, quote
        assert temporal.status == "unknown", quote

    # Where the bytes were served from is not something the document says, so
    # a quote that is only the URL is not in the read at all.
    unstated = validated_temporal(
        bare_url,
        publication_date=_claim("2024", "https://lab.example/2024/bare"),
        status="current",
    )
    assert unstated.publication_date is None
    assert unstated.status == "unknown"


def test_a_read_with_no_stated_date_keeps_no_temporal_claim() -> None:
    """No quote, no value — and no freshness judgement either."""
    read = _dated(
        "Undated Report. Published by Example Lab. The page states nothing."
    )

    for claimed in (None, TemporalClaim(), _claim("2026", ""), _claim("", "2026")):
        temporal = validated_temporal(
            read,
            publication_date=claimed,
            data_period=claimed,
            status="stale_data",
        )
        assert temporal.publication_date is None, claimed
        assert temporal.data_period is None, claimed
        assert temporal.status == "unknown", claimed


def test_a_freshness_status_rests_only_on_a_verified_quote() -> None:
    """The status is kept when the date it rests on survived, and not otherwise."""
    read = _dated("Period Report. The survey covers 2022-2024.")

    kept = validated_temporal(
        read,
        data_period=_claim("2022-2024", "The survey covers 2022-2024."),
        status="stale_data",
    )
    dropped = validated_temporal(
        read,
        data_period=_claim("1999-2001", "The survey covers 2022-2024."),
        status="stale_data",
    )

    assert kept.data_period == "2022-2024"
    assert kept.status == "stale_data"
    assert dropped.data_period is None
    assert dropped.status == "unknown"


def test_an_abbreviated_period_end_is_dropped_rather_than_expanded() -> None:
    """A year nobody wrote is not read out of an abbreviation.

    Expanding "02" to 2002 was local code spelling a date the document did not,
    so it is gone: a value naming a year the quote does not state is dropped,
    and so is the first end on its own — a period is never recorded as one end
    and never as a year that was guessed at.
    """
    read = _dated("Rolled Report. The rule applied from 1998 to 02.")

    expanded = validated_temporal(
        read,
        data_period=_claim("1998-2002", "The rule applied from 1998 to 02."),
        status="stale_data",
    )
    truncated = validated_temporal(
        read,
        data_period=_claim("1998", "The rule applied from 1998 to 02."),
        status="stale_data",
    )

    assert expanded.data_period is None
    assert expanded.status == "unknown"
    assert truncated.data_period is None
    assert truncated.status == "unknown"


def test_a_stated_year_notation_still_dates_the_document() -> None:
    """FY2026 is how a fiscal document writes its own year."""
    fiscal = _dated("Fiscal Year Report FY2026. Published by Example Lab.")
    embedded = _dated(
        "Embedded Report. ABC2026XYZ indexes it.",
        url="https://lab.example/embedded-source",
    )

    asserted = validated_temporal(
        fiscal,
        publication_date=_claim("2026", "Fiscal Year Report FY2026."),
        status="current",
    )
    unreachable = validated_temporal(
        embedded,
        publication_date=_claim("2026", "ABC2026XYZ indexes it."),
        status="current",
    )

    assert asserted.publication_date == "2026"
    assert asserted.status == "current"
    assert unreachable.publication_date is None
    assert unreachable.status == "unknown"
    # The same notation decides the read's own dating signal.
    assert "2026" in read_dated_tokens(fiscal)
    assert "2026" not in read_dated_tokens(embedded)


@pytest.mark.parametrize(
    "value",
    ["2026-13", "2026/12", "9999-00", "9999 to 00", "January 2026", "2026-1-5"],
)
def test_a_value_that_is_not_the_date_it_claims_is_rejected(value: str) -> None:
    """A number shaped like a date and not one is dropped whole."""
    read = _dated("Odd Report. The table reads 2026-13 and 2026/12.")

    temporal = validated_temporal(
        read,
        data_period=_claim(value, "The table reads 2026-13 and 2026/12."),
        status="stale_data",
    )

    assert temporal.data_period is None, value
    assert temporal.status == "unknown", value


# --------------------------------------------------------------------------
# Preconditions carried from Task 4's breaker
# --------------------------------------------------------------------------
#
# Both findings mint a temporal value and a freshness judgement at this shared
# admission boundary, so a claim built on either reads a date the document
# never stated. Each named shape gets its own test: the boundary is what
# distinguishes a token from a fragment, and a shape that passes proves
# nothing about the shapes that do not.


def test_a_parenthesised_code_never_dates_the_document() -> None:
    """"ABC(2026)" is a code with digits, not a year the document states.

    The bracket glues the digits to the word in front of them, so ``2026``
    here is a fragment of one token rather than a date of its own.
    """
    read = _dated(
        "Code Report. The token ABC(2026) indexes the table.",
        url="https://lab.example/coded",
    )

    temporal = validated_temporal(
        read,
        publication_date=_claim("2026", "The token ABC(2026) indexes the table."),
        status="current",
    )

    assert temporal.publication_date is None
    assert temporal.status == "unknown"
    assert read_dated_tokens(read) == []


def test_a_filename_and_its_extension_never_date_the_document() -> None:
    """"report(2025).pdf" names a file; the digits in it date nothing.

    The closing bracket is joined to the extension that follows it, which is
    what makes the whole run one filename rather than a year in brackets.
    """
    read = _dated(
        "File Report. See report(2025).pdf for the file.",
        url="https://lab.example/filed",
    )

    temporal = validated_temporal(
        read,
        publication_date=_claim("2025", "See report(2025).pdf for the file."),
        status="current",
    )

    assert temporal.publication_date is None
    assert temporal.status == "unknown"
    assert read_dated_tokens(read) == []


def test_a_url_path_segment_never_dates_the_document() -> None:
    """A year at the end of a URL path is where a file is filed.

    A document that prints its own URL states a location, not a date: the
    digits belong to the path, and the semicolon joins them to it.
    """
    read = _dated(
        "Link Report. Source: https://lab.example/report;2025 for the file.",
        url="https://lab.example/linked",
    )

    temporal = validated_temporal(
        read,
        publication_date=_claim(
            "2025", "Source: https://lab.example/report;2025 for the file."
        ),
        status="current",
    )

    assert temporal.publication_date is None
    assert temporal.status == "unknown"
    assert read_dated_tokens(read) == []


def test_a_url_query_parameter_never_dates_the_document() -> None:
    """A year in a query parameter is a parameter value, not a date.

    The comma joins the digits to the parameter name, so they are part of one
    URL token and cannot be read as a year the document states.
    """
    read = _dated(
        "Query Report. Source: https://lab.example/f?id,2024 for the file.",
        url="https://lab.example/queried",
    )

    temporal = validated_temporal(
        read,
        publication_date=_claim(
            "2024", "Source: https://lab.example/f?id,2024 for the file."
        ),
        status="current",
    )

    assert temporal.publication_date is None
    assert temporal.status == "unknown"
    assert read_dated_tokens(read) == []


@pytest.mark.parametrize("atom", ["2026-02-31", "2025-02-29", "2026-04-31"])
def test_an_impossible_calendar_date_is_not_a_date(atom: str) -> None:
    """February has no 31st, and 2025 has no 29th of February.

    Shape is not enough: a month's length decides whether the day the digits
    name exists at all, so an impossible one is a number that merely looks
    like a date.
    """
    read = _dated(f"Impossible Report. The table reads {atom}.")

    temporal = validated_temporal(
        read,
        publication_date=_claim(atom, f"The table reads {atom}."),
        status="current",
    )

    assert temporal.publication_date is None
    assert read_dated_tokens(read) == []


def test_a_real_leap_day_is_still_a_date() -> None:
    """The positive control: 2024 is a leap year, so 2024-02-29 exists."""
    read = _dated("Leap Report. The table reads 2024-02-29.")

    temporal = validated_temporal(
        read,
        publication_date=_claim("2024-02-29", "The table reads 2024-02-29."),
        status="current",
    )

    assert temporal.publication_date == "2024-02-29"
    assert temporal.status == "current"
    assert read_dated_tokens(read) == ["2024", "2024-02", "2024-02-29"]

# --------------------------------------------------------------------------
# Task 6: the strict pair rule an independent corroboration rests on
# --------------------------------------------------------------------------


def test_same_origin_cannot_corroborate_across_publishers() -> None:
    a = EvidenceEligibility(
        publisher_id="lab-a", work_id="report-a", origin_group_id="dataset-a",
        complete_support=True, read_valid=True, corroboration_eligible=True)
    b = a.model_copy(update={"publisher_id": "news-b", "work_id": "article-b"})
    assert not eligible_independent_pair(a, b)
    independent = b.model_copy(update={"origin_group_id": "study-b"})
    assert eligible_independent_pair(a, independent)


def test_the_plan_kernel_is_the_contract() -> None:
    """Every conjunct of the ruled kernel, one at a time.

    Both sides must be a valid read of a complete support by an eligible
    contributor, all six identity fields must be known, and publisher, work and
    origin must be pairwise different. A missing conjunct is not a weaker pair:
    unknown identity can establish neither sameness nor independence.
    """
    complete = EvidenceEligibility(
        publisher_id="lab-a",
        work_id="report-a",
        origin_group_id="study-a",
        complete_support=True,
        read_valid=True,
        corroboration_eligible=True,
    )
    other = complete.model_copy(
        update={
            "publisher_id": "lab-b",
            "work_id": "report-b",
            "origin_group_id": "study-b",
        }
    )

    assert eligible_independent_pair(complete, other)
    # Symmetric: nothing about the answer depends on argument order.
    assert eligible_independent_pair(other, complete)
    for field in (
        "publisher_id",
        "work_id",
        "origin_group_id",
        "complete_support",
        "read_valid",
        "corroboration_eligible",
    ):
        weakened = complete.model_copy(
            update={field: "" if field.endswith("_id") else False}
        )
        assert not eligible_independent_pair(weakened, other), field
        assert not eligible_independent_pair(other, weakened), field


@pytest.mark.parametrize(
    "field",
    ["publisher_id", "work_id", "origin_group_id"],
)
def test_an_unknown_identity_field_can_never_be_half_of_the_pair(field: str) -> None:
    """``None`` is "cannot establish this", never a value that differs."""
    a = EvidenceEligibility(
        publisher_id="lab-a",
        work_id="report-a",
        origin_group_id="study-a",
        complete_support=True,
        read_valid=True,
        corroboration_eligible=True,
    )
    b = a.model_copy(
        update={
            "publisher_id": "lab-b",
            "work_id": "report-b",
            "origin_group_id": "study-b",
            field: None,
        }
    )

    assert not eligible_independent_pair(a, b)
    assert not eligible_independent_pair(b, a)


def test_a_mirror_and_its_original_are_one_work() -> None:
    """A mirror is usable first support and never a second, independent one."""
    original = EvidenceEligibility(
        publisher_id="lab-a",
        work_id="report-a",
        origin_group_id="study-a",
        complete_support=True,
        read_valid=True,
        corroboration_eligible=True,
    )
    mirror = original.model_copy(
        update={"publisher_id": "mirror-b", "origin_group_id": "study-a"}
    )

    assert not eligible_independent_pair(original, mirror)


def test_the_same_publisher_with_different_works_is_not_a_pair() -> None:
    """Two reports from one issuer are one voice, however different the works."""
    first = EvidenceEligibility(
        publisher_id="lab-a",
        work_id="report-a",
        origin_group_id="study-a",
        complete_support=True,
        read_valid=True,
        corroboration_eligible=True,
    )
    second = first.model_copy(
        update={"work_id": "report-b", "origin_group_id": "study-b"}
    )

    assert not eligible_independent_pair(first, second)


def test_a_partial_read_is_never_half_of_the_pair() -> None:
    """Task 1: a partial read is admissible evidence and no identity edge."""
    full = EvidenceEligibility(
        publisher_id="lab-a",
        work_id="report-a",
        origin_group_id="study-a",
        complete_support=True,
        read_valid=True,
        corroboration_eligible=True,
    )
    partial = full.model_copy(
        update={
            "publisher_id": "lab-b",
            "work_id": "report-b",
            "origin_group_id": "study-b",
            "read_valid": False,
        }
    )

    assert not eligible_independent_pair(full, partial)
