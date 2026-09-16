"""The pure read-evidence contract: canonical text, identity, admission.

One home for the four rules whose disagreement caused the defects this
program repairs:

* **What a read is.** Only a successful ``web_scraper`` or ``document_reader``
  call produces a :class:`~deep_research.utils.types.ReadRecord`. A search
  result, a snippet, and a ``query_memory`` recall are discovery, however
  confident or well-labelled they are — memory metadata is not validation.
* **What a work is.** :func:`resolve_work_identities` groups reads by
  normalized aliases (DOI, issuer-namespaced report number, complete-content
  hash, conservative title + year + issuer + edition) and reports ambiguity
  as ``unknown`` or ``conflicting`` rather than guessing a key.
* **What may be re-admitted.** :func:`validate_cached_read` admits a stored
  original read only against the locally resolved body, expected hash, and
  version eligibility its *caller* established — never against anything the
  model or a memory entry said about itself.
* **What crossed each boundary.** The registry reducers and the boundary
  manifest builders record what was admitted, what was deferred, and why, so
  a replay can name the first missing boundary.

Nothing here performs I/O, reads a clock, or calls a provider: every value is
a deterministic function of its arguments, and every timestamp is supplied by
the caller that owns the clock. Later tasks extend this module — Task 3 wires
read admission into the acquisition loop, Task 4 adds transport/derivation
evidence, Task 6 adds ``eligible_independent_pair``.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from urllib.parse import urlsplit

from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    BoundaryAudit,
    EvidenceDisposition,
    EvidenceUnit,
    ReadRecord,
    WorkIdentity,
)

# Which boundary one manifest describes. Later tasks add their own operations
# (retention, clustering, adjudication packet, composition) as new constants
# and pass them to ``build_boundary_audit``.
READ_ADMISSION_OPERATION = "read_admission"
PASSAGE_SELECTION_OPERATION = "passage_selection"

# The persisted disposition vocabulary, kept beside the producers that choose
# from it. ``EvidenceDisposition.stage`` and ``.reason`` are plain strings on
# the model so a snapshot written by a later release stays loadable.
DISPOSITION_STAGES = (
    "read-selection",
    "extraction",
    "retention",
    "clustering",
    "adjudication-packet",
    "composition",
)
DISPOSITION_REASONS = (
    "irrelevant",
    "out_of_scope",
    "stale_for_target",
    "duplicate_content",
    "malformed",
    "deferred_capacity",
    "unsupported_excerpt",
)

# How many aliases one identity may carry, so a malformed metadata row cannot
# grow a persisted record without limit.
MAX_WORK_ALIASES = 64

_DIGEST_LENGTH = 24
_HEX_DIGITS = frozenset("0123456789abcdef")
# DOI resolvers, and the scheme-less prefix a metadata field often carries.
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


class EvidenceContractError(ValueError):
    """A record, row, or argument violates the evidence contract."""


class EvidenceIdentityConflict(EvidenceContractError):
    """One identity carries two different bodies; nothing may overwrite."""


class MissingBoundaryManifest(LookupError):
    """A replay asked for a boundary manifest that was never recorded."""


# ---------------------------------------------------------------------------
# canonical text
# ---------------------------------------------------------------------------


def canonical_read_text(text: str) -> str:
    """Return the canonical form of extracted source text.

    Unicode is normalized to NFC and whitespace is collapsed, so the same
    passage read through two layouts is one string. Nothing else changes:
    digits, minus signs, units, and negation survive verbatim, so two passages
    that assert different things can never normalize together.
    """
    if not isinstance(text, str):
        raise EvidenceContractError("text must be a string")
    return " ".join(unicodedata.normalize("NFC", text).split())


def normalized_content_sha256(text: str) -> str:
    """Return the SHA-256 of ``text``'s canonical form.

    Raises :class:`EvidenceContractError` for text with no content: an empty
    or whitespace-only extraction has no identity, and hashing it would mint
    one that every empty document would share.
    """
    canonical = canonical_read_text(text)
    if not canonical:
        raise EvidenceContractError("content hash requires non-empty text")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def excerpt_matches(text: str, excerpt: str) -> bool:
    """True when ``excerpt`` is exactly contained in ``text``, modulo layout.

    Membership is exact after canonicalization — no fuzzy ratio, no ellipsis
    stitching, no prefix tolerance — because a near-miss excerpt is how a
    paraphrase becomes "source text".
    """
    candidate = canonical_read_text(excerpt)
    if not candidate:
        return False
    return candidate in canonical_read_text(text)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def canonical_publisher_id(metadata: Mapping[str, object]) -> str | None:
    """Return the publisher standing behind one read's metadata.

    An issuer the artifact itself evidences outranks the host that served it:
    a mirror on a CDN is the publisher it mirrors, not the CDN. With no
    evidenced issuer the serving host's registrable identity is the only
    evidence there is, and with neither the publisher is ``None`` — unknown,
    never guessed.
    """
    issuer = _text_field(metadata, "issuer")
    if issuer:
        return _identity_words(issuer)
    for key in ("serving_host", "url"):
        candidate = _text_field(metadata, key)
        if candidate:
            return publisher_identity(candidate)
    return None


def resolve_work_identities(
    metadata: Sequence[Mapping[str, object]],
) -> dict[str, WorkIdentity]:
    """Resolve one :class:`WorkIdentity` per metadata row, keyed by source_id.

    Each row is read keyed by ``source_id`` and may evidence ``doi``,
    ``report_number``, ``issuer``, ``title``, ``year``, ``edition``,
    ``complete_content_sha256``, ``extraction_complete``, and
    ``identity_links``. Unknown keys are ignored, so a later task may widen
    the row without breaking this resolver.

    Rows join on a shared alias. Strong aliases join unconditionally; the
    conservative ``title``/``year``/``issuer``/``edition`` alias joins only
    rows whose complete-content evidence does not contradict, which is what
    stops one generic title ("Annual Report") from merging two different
    documents. A group that evidences two different values in one class — two
    DOIs, two report numbers of one issuer, two complete hashes — is reported
    ``conflicting`` with ``key=None``: ambiguity is preserved, never averaged
    into a join.
    """
    rows = [_parse_row(row) for row in metadata]
    groups = _group_rows(rows)
    resolved: dict[str, WorkIdentity] = {}
    for group in groups:
        identity = _resolve_group(group)
        for row in group:
            resolved[row.source_id] = identity
    return resolved


class _Row:
    """One parsed metadata row: its identity inputs, never its raw text."""

    __slots__ = (
        "aliases",
        "derives_from",
        "edition",
        "hash",
        "issuer_id",
        "reports",
        "source_id",
        "title",
        "year",
    )

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.aliases: list[str] = []
        self.derives_from: list[str] = []
        self.reports: list[str] = []
        self.hash: str | None = None
        self.issuer_id: str | None = None
        self.title: str = ""
        self.edition: str = ""
        self.year: str = ""


def _parse_row(row: object) -> _Row:
    if not isinstance(row, Mapping):
        raise EvidenceContractError("metadata rows must be mappings")
    source_id = _text_field(row, "source_id")
    if not source_id:
        raise EvidenceContractError("metadata rows require a non-empty source_id")
    parsed = _Row(source_id)

    doi = _normalized_doi(_text_field(row, "doi"))
    if doi:
        parsed.aliases.append(f"doi:{doi}")

    issuer = _text_field(row, "issuer")
    if issuer:
        parsed.issuer_id = _identity_words(issuer)

    number = _identifier_text(_text_field(row, "report_number"))
    if number and parsed.issuer_id:
        # A report number is unique only inside the issuer's namespace.
        alias = f"report:{parsed.issuer_id}:{number}"
        parsed.reports.append(alias)
        parsed.aliases.append(alias)

    parsed.hash = _complete_content_hash(row)
    if parsed.hash:
        parsed.aliases.append(f"sha256:{parsed.hash}")

    # An evidenced link names a related version or derivation. It is recorded
    # as a relationship, never used to join: two records are the same work
    # only when they share an identity alias.
    for link in _text_sequence(row, "identity_links"):
        parsed.derives_from.append(_related_id(link))

    parsed.title = _identity_words(_text_field(row, "title"))
    parsed.year = _year_text(row.get("year"))
    parsed.edition = _identity_words(_text_field(row, "edition"))
    return parsed


def _group_rows(rows: Sequence[_Row]) -> list[list[_Row]]:
    """Union rows that share an alias into the groups they evidence."""
    if not rows:
        return []
    parent = list(range(len(rows)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for members in _strong_aliases(rows):
        for index in members:
            union(members[0], index)
    for members in _weak_aliases(rows):
        for index in members:
            union(members[0], index)

    grouped: dict[int, list[_Row]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(root(index), []).append(row)
    return list(grouped.values())


def _strong_aliases(rows: Sequence[_Row]) -> list[list[int]]:
    by_alias: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        for alias in row.aliases:
            by_alias.setdefault(alias, []).append(index)
    return list(by_alias.values())


def _weak_aliases(rows: Sequence[_Row]) -> list[list[int]]:
    """Group rows whose conservative title evidence does not contradict.

    The bucket carries the row's complete-content hash, so two documents that
    share a title, a year, an issuer, and an edition but not a body never land
    in the same group — and an unhashed row joins only other unhashed rows
    rather than borrowing a body it never evidenced.
    """
    by_alias: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        alias = _weak_alias(row, row.hash or "")
        if alias is not None:
            by_alias.setdefault(alias, []).append(index)
    return list(by_alias.values())


def _weak_alias(row: _Row, hash_bucket: str) -> str | None:
    if not (row.title and row.year and row.issuer_id):
        return None
    alias = f"title:{row.title}|{row.year}|{row.issuer_id}|{row.edition}"
    return f"{alias}|{hash_bucket}" if hash_bucket else alias


def _resolve_group(group: Sequence[_Row]) -> WorkIdentity:
    strong = [alias for row in group for alias in row.aliases]
    dois = _distinct(alias for alias in strong if alias.startswith("doi:"))
    reports = _distinct(alias for alias in strong if alias.startswith("report:"))
    hashes = _distinct(row.hash for row in group if row.hash)
    issuers = _distinct(row.issuer_id for row in group if row.issuer_id)
    aliases = sorted(set(strong))
    issuer_id = issuers[0] if len(issuers) == 1 else None

    conflicts: list[str] = []
    if len(dois) > 1:
        conflicts.append(f"{len(dois)} distinct normalized DOIs")
    if len(hashes) > 1:
        conflicts.append(f"{len(hashes)} distinct complete-content hashes")
    for issuer in issuers:
        numbers = _distinct(
            alias
            for alias in reports
            if alias.startswith(f"report:{issuer}:")
        )
        if len(numbers) > 1:
            conflicts.append(
                f"{len(numbers)} distinct report numbers for issuer {issuer!r}"
            )

    derives_from = sorted({item for row in group for item in row.derives_from})
    if conflicts:
        return WorkIdentity(
            key=None,
            aliases=aliases[:MAX_WORK_ALIASES],
            basis="conflicting strong identifiers: " + "; ".join(conflicts),
            issuer_id=issuer_id,
            derives_from_work_ids=derives_from[:MAX_WORK_ALIASES],
            identity_status="conflicting",
        )

    key, basis = _identity_key(group, dois, reports, hashes)
    return WorkIdentity(
        key=key,
        aliases=aliases[:MAX_WORK_ALIASES],
        basis=basis,
        issuer_id=issuer_id,
        derives_from_work_ids=derives_from[:MAX_WORK_ALIASES],
        identity_status="known" if key else "unknown",
    )


def _identity_key(
    group: Sequence[_Row],
    dois: list[str],
    reports: list[str],
    hashes: list[str],
) -> tuple[str | None, str]:
    if dois:
        return dois[0], "shared normalized DOI"
    if reports:
        return reports[0], "shared issuer-namespaced report number"
    if hashes:
        return f"sha256:{hashes[0]}", "identical complete-content hash"
    for row in group:
        alias = _weak_alias(row, "")
        if alias is not None:
            return alias, "title, year, issuer, and edition agree"
    return None, "no usable identity metadata"


def _distinct(values: Iterable[str | None]) -> list[str]:
    """Sorted distinct non-empty values, so a group's evidence is ordered."""
    return sorted({value for value in values if value})


def _text_field(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()


def _text_sequence(row: Mapping[str, object], key: str) -> list[str]:
    value = row.get(key)
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _identity_words(value: str) -> str:
    """Normalize a name or title used as identity text.

    Case and typography fold; words do not. "Example Lab" and "example lab"
    are one issuer, while "Example Lab, Inc." stays a different one, because
    dropping the qualifier would merge two organizations that are not the
    same publisher.
    """
    folded = unicodedata.normalize("NFKC", value).casefold()
    kept = "".join(char if char.isalnum() else " " for char in folded)
    return " ".join(kept.split())


def _identifier_text(value: str) -> str:
    """Normalize a report number or opaque identifier, keeping punctuation.

    Case and surrounding whitespace fold; ``-``, ``.`` and ``/`` are part of
    the identifier and stay, so ``TR-2025-01`` never collapses onto
    ``TR202501``.
    """
    collapsed = unicodedata.normalize("NFC", value).casefold().strip()
    return collapsed.strip(" .,;:")


def _normalized_doi(value: str) -> str:
    """Return the normalized DOI inside ``value``, or an empty string."""
    candidate = unicodedata.normalize("NFC", value).strip()
    lowered = candidate.casefold()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    normalized = _identifier_text(candidate)
    if not normalized.startswith("10.") or "/" not in normalized:
        # A URL, a title, or a free-text note in the DOI field is not a DOI.
        return ""
    return normalized


def _related_id(value: str) -> str:
    """Normalize one evidenced link to a related work as a stable id."""
    doi = _normalized_doi(value)
    if doi:
        return f"doi:{doi}"
    parts = urlsplit(value.strip())
    if parts.scheme and parts.netloc:
        return f"link:{normalize_source_url(value)}"
    return f"link:{_identifier_text(value)}"


def _complete_content_hash(row: Mapping[str, object]) -> str | None:
    """The row's complete-content hash, or ``None`` when it is unusable.

    Only a full 64-digit hexadecimal digest is an identity edge, and only when
    the row does not declare its extraction incomplete: a placeholder, an
    error string, a truncated digest, or a hash taken from part of a document
    identifies no work.
    """
    complete = row.get("extraction_complete")
    if isinstance(complete, bool) and not complete:
        return None
    value = row.get("complete_content_sha256")
    if not isinstance(value, str):
        return None
    candidate = value.strip().casefold()
    if len(candidate) != 64 or not set(candidate) <= _HEX_DIGITS:
        return None
    return candidate


def _year_text(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        digits = "".join(char for char in value if char.isdigit())
        return digits if digits and len(digits) == 4 else ""
    return ""


# ---------------------------------------------------------------------------
# strict read producers
# ---------------------------------------------------------------------------


def build_read_id(
    *,
    session_id: str,
    reader: str,
    resolved_url: str,
    content_sha256: str,
) -> str:
    """Return the stable identity of one read body inside one session.

    Derived only from immutable read fields, so the same body read twice in a
    session is one read — and a re-read that returns different content is a
    different one. Identity resolution, scoring, and target association are
    deliberately absent from the key: they are assessments, and an assessment
    must never renumber evidence.
    """
    return "read-" + _fingerprint(
        session_id,
        reader,
        normalize_source_url(resolved_url),
        content_sha256.strip().casefold(),
    )[:_DIGEST_LENGTH]


def build_read_record(
    *,
    session_id: str,
    reader: str,
    requested_url: str,
    resolved_url: str,
    title: str,
    retrieved_at: str,
    text: str,
    passages: Mapping[str, str],
    extraction_complete: bool = True,
    declared_content_sha256: str | None = None,
    target_ids: Sequence[str] = (),
) -> ReadRecord:
    """Build the one admissible read record for a successful read.

    Every field a replay needs must be present and consistent: a body with no
    content, a passage that is not verbatim text of that body, a declared hash
    that disagrees with the body it was taken from, or an extraction the
    reader itself reported incomplete all fail here rather than becoming
    evidence. ``text`` is the complete extracted document; ``passages`` maps
    the locator the reader reported to the text at that locator.
    """
    if reader not in ("web_scraper", "document_reader"):
        raise EvidenceContractError(
            "a read record requires the reader that produced it"
        )
    requested = " ".join(requested_url.split())
    resolved = normalize_source_url(resolved_url)
    if not requested or not resolved:
        raise EvidenceContractError(
            "a read record requires both the requested and the resolved URL"
        )
    if not extraction_complete:
        raise EvidenceContractError(
            "an incomplete extraction is not an admissible read"
        )
    if not session_id.strip():
        raise EvidenceContractError("a read record requires its session id")

    canonical = canonical_read_text(text)
    if not canonical:
        raise EvidenceContractError("a read record requires non-empty content")
    if not passages:
        raise EvidenceContractError("a read record requires at least one passage")

    checked: dict[str, str] = {}
    for locator, passage_text in passages.items():
        if not isinstance(locator, str) or not locator.strip():
            raise EvidenceContractError("a passage requires a non-empty locator")
        if not isinstance(passage_text, str) or not excerpt_matches(
            canonical, passage_text
        ):
            raise EvidenceContractError(
                f"passage {locator!r} is not verbatim text of the read body"
            )
        checked[locator] = passage_text

    content_sha256 = normalized_content_sha256(canonical)
    if declared_content_sha256 is not None and (
        declared_content_sha256.strip().casefold() != content_sha256
    ):
        raise EvidenceContractError(
            "the declared content hash does not match the read body"
        )

    return ReadRecord(
        read_id=build_read_id(
            session_id=session_id,
            reader=reader,
            resolved_url=resolved,
            content_sha256=content_sha256,
        ),
        requested_url=requested,
        resolved_url=resolved,
        title=" ".join(title.split()) or resolved,
        reader=reader,
        retrieved_at=retrieved_at,
        content_sha256=content_sha256,
        extraction_complete=True,
        passages=checked,
        target_ids=list(target_ids),
        origin_session_id=session_id,
    )


def passages_from_chunks(
    chunks: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Map a ``document_reader`` chunk list onto locator -> extracted text.

    The locator names where the text sits in the document — page and chunk
    index where the reader reported a page, chunk index otherwise — so a
    passage can be cited and re-read without carrying the whole body. A
    malformed chunk is a bug in the payload this project produced, not
    model output, and raises instead of silently dropping extracted text.
    """
    passages: dict[str, str] = {}
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise EvidenceContractError("every chunk must be a mapping")
        text = chunk.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EvidenceContractError("every chunk requires non-empty text")
        index = chunk.get("chunk_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise EvidenceContractError("every chunk requires an integer index")
        page = chunk.get("page")
        if page is None:
            locator = f"chunk-{index}"
        elif isinstance(page, bool) or not isinstance(page, int):
            raise EvidenceContractError("a page number must be an integer")
        else:
            locator = f"page-{page}-chunk-{index}"
        if locator in passages:
            raise EvidenceContractError(f"duplicate locator {locator!r}")
        passages[locator] = text
    if not passages:
        raise EvidenceContractError("a chunk list must not be empty")
    return passages


def build_evidence_id(*, read_id: str, locator: str, excerpt: str) -> str:
    """Return the stable identity of one exact passage of one read."""
    canonical = canonical_read_text(excerpt)
    if not canonical:
        raise EvidenceContractError("an evidence unit requires an excerpt")
    return "ev-" + _fingerprint(
        read_id, canonical_read_text(locator), canonical
    )[:_DIGEST_LENGTH]


def build_evidence_unit(
    *,
    read: ReadRecord,
    locator: str,
    excerpt: str,
    origin: str,
    target_ids: Sequence[str] = (),
) -> EvidenceUnit:
    """Build the one evidence unit for a passage of an admitted read.

    The excerpt must be verbatim text of the locator it cites, checked against
    the registry's own stored passages: an excerpt the read does not contain
    is not evidence, however plausible it reads.
    """
    if origin not in ("researcher", "fact_checker"):
        raise EvidenceContractError(
            "an evidence unit requires the agent that selected it"
        )
    passage = read.passages.get(locator)
    if passage is None:
        raise EvidenceContractError(
            f"read {read.read_id!r} has no locator {locator!r}"
        )
    if not excerpt_matches(passage, excerpt):
        raise EvidenceContractError(
            f"excerpt is not text of locator {locator!r}"
        )
    return EvidenceUnit(
        evidence_id=build_evidence_id(
            read_id=read.read_id, locator=locator, excerpt=excerpt
        ),
        read_id=read.read_id,
        source_url=read.resolved_url,
        source_title=read.title,
        locator=locator,
        excerpt=excerpt,
        target_ids=list(target_ids),
        origin=origin,
    )


# ---------------------------------------------------------------------------
# cache admission
# ---------------------------------------------------------------------------


def validate_cached_read(
    record: ReadRecord,
    canonical_text: str,
    *,
    expected_content_sha256: str,
    version_eligible: bool,
    validated_at: str,
) -> ReadRecord | None:
    """Admit a stored original read as this session's evidence, or refuse it.

    ``record`` is the original network read its caller resolved out of the
    local read registry, ``canonical_text`` is the stored body, and
    ``expected_content_sha256`` and ``version_eligible`` are what the caller
    established locally about that artifact. None of these may come from model
    output or from a memory entry's self-declared metadata: a memory record is
    not validation, and a claimed read ID resolves to nothing unless a real
    read is stored under it.

    The returned record is stamped locally as a cache import and keeps the
    original read's identity, publisher-relevant fields, and observation
    period, so no second body download and no synthetic ``retrieved_at`` are
    needed. ``None`` means refused: the caller records an
    :class:`~deep_research.utils.types.EvidenceDisposition` for it. Task 3
    owns when this is called; this function owns what it may accept.
    """
    _require_aware_timestamp(validated_at, name="validated_at")
    expected = expected_content_sha256.strip().casefold()
    if len(expected) != 64 or not set(expected) <= _HEX_DIGITS:
        raise EvidenceContractError(
            "expected_content_sha256 must be a resolved stored hash"
        )

    stored_hash = str(getattr(record, "content_sha256", "") or "").strip().casefold()
    if not str(getattr(record, "read_id", "") or "").strip():
        # A forged or missing read ID resolves to nothing in the registry.
        return None
    if getattr(record, "acquisition_kind", None) != "network":
        # A cache import is not an original artifact: re-admitting one would
        # let a stamped copy stand in for a read nobody performed.
        return None
    if not version_eligible:
        return None
    if not getattr(record, "extraction_complete", False):
        return None

    resolved_text = canonical_read_text(canonical_text)
    if not resolved_text:
        return None
    recomputed = normalized_content_sha256(resolved_text)
    if not (stored_hash == expected == recomputed):
        return None

    passages = getattr(record, "passages", None)
    if not passages:
        return None
    for locator, passage_text in passages.items():
        if not isinstance(locator, str) or not locator.strip():
            return None
        if not excerpt_matches(resolved_text, passage_text):
            # A stored passage that is not in the stored body is a summary or
            # a rewrite, not original source text.
            return None

    return record.model_copy(
        update={
            "acquisition_kind": "cache",
            "version_validated_at": validated_at,
        }
    )


def _require_aware_timestamp(value: object, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError(f"{name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceContractError(
            f"{name} must be a valid ISO 8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceContractError(f"{name} must be timezone-aware")


# ---------------------------------------------------------------------------
# registries
# ---------------------------------------------------------------------------


def merge_read_records(
    previous: Mapping[str, ReadRecord],
    current: Mapping[str, ReadRecord],
) -> dict[str, ReadRecord]:
    """Fold new reads into the registry, refusing one ID with two bodies.

    A read ID names an immutable body. Two records under one ID that agree on
    that body are the same read — the earlier observation and both target
    associations are kept — while a disagreement is a conflict that raises,
    because overwriting either one would silently re-point stored evidence.
    """
    merged: dict[str, ReadRecord] = dict(previous)
    for read_id, record in current.items():
        existing = merged.get(read_id)
        if existing is None:
            merged[read_id] = record
            continue
        if _read_body(existing) != _read_body(record):
            raise EvidenceIdentityConflict(
                f"read {read_id!r} already names a different body"
            )
        merged[read_id] = _combine_reads(existing, record)
    return merged


def _read_body(record: ReadRecord) -> tuple[object, ...]:
    """The immutable facts of a read; assessments and stamps are not here."""
    return (
        record.reader,
        record.requested_url,
        record.resolved_url,
        record.content_sha256,
        record.extraction_complete,
        tuple(sorted(record.passages.items())),
    )


def _combine_reads(existing: ReadRecord, incoming: ReadRecord) -> ReadRecord:
    preferred = existing
    if existing.acquisition_kind == "cache" and incoming.acquisition_kind == "network":
        preferred = incoming
    targets = list(existing.target_ids)
    for target_id in incoming.target_ids:
        if target_id not in targets:
            targets.append(target_id)
    observed = min(
        existing.retrieved_at,
        incoming.retrieved_at,
        key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
    )
    return preferred.model_copy(
        update={"retrieved_at": observed, "target_ids": targets}
    )


def merge_evidence_units(
    previous: Mapping[str, EvidenceUnit],
    current: Mapping[str, EvidenceUnit],
) -> dict[str, EvidenceUnit]:
    """Fold new evidence units in, unioning targets and refusing rewrites."""
    merged: dict[str, EvidenceUnit] = dict(previous)
    for evidence_id, unit in current.items():
        existing = merged.get(evidence_id)
        if existing is None:
            merged[evidence_id] = unit
            continue
        if (
            existing.read_id != unit.read_id
            or existing.locator != unit.locator
            or existing.excerpt != unit.excerpt
            or existing.source_url != unit.source_url
        ):
            raise EvidenceIdentityConflict(
                f"evidence {evidence_id!r} already names a different passage"
            )
        targets = list(existing.target_ids)
        for target_id in unit.target_ids:
            if target_id not in targets:
                targets.append(target_id)
        merged[evidence_id] = existing.model_copy(
            update={"target_ids": targets}
        )
    return merged


def merge_evidence_dispositions(
    previous: Sequence[EvidenceDisposition],
    current: Sequence[EvidenceDisposition],
) -> list[EvidenceDisposition]:
    """Append dispositions, refusing two reasons for one item at one stage."""
    merged: list[EvidenceDisposition] = []
    index_by_key: dict[tuple[str, str], int] = {}
    for item in (*previous, *current):
        key = (item.stage, item.item_id)
        position = index_by_key.get(key)
        if position is None:
            index_by_key[key] = len(merged)
            merged.append(item)
            continue
        known = merged[position]
        if known.reason != item.reason:
            raise EvidenceIdentityConflict(
                f"item {item.item_id!r} already has reason {known.reason!r} "
                f"at stage {item.stage!r}"
            )
        targets = list(known.target_ids)
        for target_id in item.target_ids:
            if target_id not in targets:
                targets.append(target_id)
        merged[position] = known.model_copy(update={"target_ids": targets})
    return merged


def merge_boundary_audits(
    previous: Mapping[str, BoundaryAudit],
    current: Mapping[str, BoundaryAudit],
) -> dict[str, BoundaryAudit]:
    """Fold manifests in, refusing one audit ID with two different contents."""
    merged: dict[str, BoundaryAudit] = dict(previous)
    for audit_id, audit in current.items():
        existing = merged.get(audit_id)
        if existing is None:
            merged[audit_id] = audit
            continue
        if existing != audit:
            raise EvidenceIdentityConflict(
                f"audit {audit_id!r} already records another boundary"
            )
    return merged


# ---------------------------------------------------------------------------
# boundary manifests
# ---------------------------------------------------------------------------


def boundary_audit_id(
    *,
    job_id: str,
    agent_name: str,
    operation: str,
    sequence: int,
) -> str:
    """Return the stable ID of one boundary manifest.

    Deterministic from what the manifest is about, so a later replay can
    recompute the ID it expects to find and fail on a missing one instead of
    reading an absent manifest as zero loss.
    """
    if not job_id.strip() or not agent_name.strip() or not operation.strip():
        raise EvidenceContractError(
            "a boundary audit id requires a job, an agent, and an operation"
        )
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise EvidenceContractError("sequence must be a non-negative integer")
    return "audit-" + _fingerprint(
        job_id, agent_name, operation, str(sequence)
    )[:_DIGEST_LENGTH]


def build_boundary_audit(
    *,
    operation: str,
    job_id: str,
    agent_name: str,
    sequence: int,
    input_ids: Sequence[str],
    packet_fingerprint: str,
    configuration_fingerprint: str,
    target_ids: Sequence[str] = (),
    claim_cluster_ids: Sequence[str] = (),
    selected_ids: Sequence[str] = (),
    returned_ids: Sequence[str] = (),
    accepted_ids: Sequence[str] = (),
    deferred_ids: Sequence[str] = (),
    disposition_ids: Sequence[str] = (),
    status: str = "completed",
) -> BoundaryAudit:
    """Build one Section 2.6 boundary manifest for one handoff.

    A manifest records what went in, what came out, what was accepted, what
    was deferred, and the reasons for everything that did not cross — so an
    audit can name the first boundary where evidence was lost. Every ID list
    is explicit and may be empty; the scalars that identify the boundary and
    the packet it judged may not be.
    """
    return BoundaryAudit(
        audit_id=boundary_audit_id(
            job_id=job_id,
            agent_name=agent_name,
            operation=operation,
            sequence=sequence,
        ),
        job_id=job_id,
        agent_name=agent_name,
        operation=operation,
        target_ids=list(target_ids),
        claim_cluster_ids=list(claim_cluster_ids),
        input_ids=list(input_ids),
        selected_ids=list(selected_ids),
        returned_ids=list(returned_ids),
        accepted_ids=list(accepted_ids),
        deferred_ids=list(deferred_ids),
        disposition_ids=list(disposition_ids),
        packet_fingerprint=packet_fingerprint,
        schema_version=QUALITY_CONTRACT_VERSION,
        configuration_fingerprint=configuration_fingerprint,
        status=status,  # type: ignore[arg-type]
    )


def require_boundary_manifest(
    audits: Mapping[str, BoundaryAudit],
    audit_id: str,
) -> BoundaryAudit:
    """Return the manifest a replay needs, or fail loudly.

    A new-contract replay asserts against this: a missing manifest raises
    instead of contributing empty ID lists, which would read as "nothing was
    lost here" — the exact failure mode a boundary audit exists to prevent.
    """
    audit = audits.get(audit_id)
    if audit is None:
        raise MissingBoundaryManifest(
            f"no boundary manifest recorded for audit id {audit_id!r}"
        )
    return audit


def _fingerprint(*parts: str) -> str:
    """SHA-256 over ``parts``, separated so no two fields can forge a join."""
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
