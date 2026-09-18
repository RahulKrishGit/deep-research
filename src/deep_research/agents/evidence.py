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
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import Field

from deep_research.agents.sources import (
    normalize_source_url,
    publisher_identity,
    source_domain,
)
from deep_research.utils.types import (
    INCOMPLETE_CONTENT_SHA256,
    QUALITY_CONTRACT_VERSION,
    BoundaryAudit,
    ContractModel,
    EvidenceDisposition,
    EvidenceUnit,
    ReadRecord,
    ScoredSource,
    SourceTemporal,
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
    the extraction it came from is complete. Completeness is read strictly: an
    absent flag leaves the hash field's own name asserting it, and anything
    else must be the boolean ``True``. A serialized ``"false"``, ``0``, or
    ``None`` never counts as complete, because a truthy string is exactly how
    a corrupt snapshot would smuggle a partial hash into an identity.
    """
    if "extraction_complete" in row and row["extraction_complete"] is not True:
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
# read-backed source identity, transport relation, and fitness signals
# ---------------------------------------------------------------------------

# The closed vocabularies a scored source's labels come from. ``unknown`` is a
# member of each: absence of evidence is an answer, never a made-up label.
SOURCE_ROLES = (
    "original_report",
    "independent_research",
    "derivative",
    "company_statement",
    "mixed",
    "unknown",
)
TRANSPORT_RELATIONS = ("original", "mirror", "syndication", "unknown")
SELF_INTEREST_LEVELS = ("none", "potential", "evidenced", "unknown")
FRESHNESS_STATUSES = (
    "current",
    "superseded",
    "stale_data",
    "projection",
    "effective",
    "unknown",
)

# The date that makes each freshness status checkable. A status whose date the
# read does not carry is recorded ``unknown``: "newly published" cannot be
# asserted from a document that never says when it was published.
_FRESHNESS_DATES = {
    "current": "publication_date",
    "superseded": "publication_date",
    "stale_data": "data_period",
    "projection": "forecast_horizon",
    "effective": "effective_date",
}

# Phrases that publish or issue a document. Attribution is a statement *about*
# the document, so only these phrases — and only immediately before the name —
# transfer ownership. "As reported by Acme" repeats someone's figure and
# "written by" is authorship; neither says who published this page, and a
# headline naming an organization does not either.
_ATTRIBUTION_PHRASES = (
    r"published\s+by",
    r"published\s+on\s+behalf\s+of",
    r"publisher\s*:",
    r"issued\s+by",
    r"issuing\s+body\s*:",
    r"prepared\s+by",
    r"produced\s+by",
    r"released\s+by",
    r"copyright",
)

# Punctuation allowed between two words of one name, and between the
# attribution phrase and the name it attributes. A name's words must be
# separated by *something*: "ExampleLab" is a different name from
# "Example Lab", for the same reason ``_identity_words`` never merges two
# words into each other.
_NAME_GAP = r"[\W_]{1,4}"
_ATTRIBUTION_GAP = r"[\s:,\u2013\u2014-]{0,4}"

# The anchors a model may propose about a document. Each is accepted only when
# the read itself carries it; everything else is dropped rather than recorded.
ANCHOR_FIELDS = ("doi", "issuer", "report_number", "year")

# One date atom: a year, a year and month, or a full day.
_DATE_ATOM = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"
# The notations a document uses to *name* a year, which still date a document
# while every other embedded year does not. The positive lookbehind consumes
# the two notation letters, so it can only match the letters named here.
_YEAR_NOTATION = r"(?:FY|CY)"
# A date is a token a document writes, not a fragment of a longer one:
# "ABC2026XYZ" is a product code, "10.1234/grid.2025" is a registered
# identifier, "/2024/report" is a path, and "Release 2026-12.5" is a version.
# None of them dates anything, and the boundary is what says so — an identifier
# can never be a verified verbatim quote, so no digit masking is needed. Only
# the documented year notations may precede a date; nothing may precede it by
# the punctuation an identifier joins its own parts with.
_TOKEN_BEFORE = rf"(?:(?<={_YEAR_NOTATION})|(?<![\w\-+./:?=&#]))"
# What may follow a date: a full stop that ends a sentence, a comma, a
# semicolon, a closing bracket, a colon — and nothing that continues it. A word
# character continues a longer word, an identifier's punctuation continues an
# identifier, and a range separator continues a period: "2022" is not a token
# of "2022-2024", because that document states a period and a period keeps both
# its ends.
_TOKEN_AFTER = (
    r"(?![A-Za-z0-9_])"
    r"(?![.\-+/:?=&#][A-Za-z0-9])"
    r"(?!\s*(?:-|\u2013|\u2014|/|to|through|until|thru)\s*\d)"
)
# How a document joins the two ends of a period it states. The second end is a
# date of its own: an abbreviated year is a spelling local code would have to
# guess at, and guessing dates is what this contract removes.
_RANGE_JOIN = r"\s*(?:-|\u2013|\u2014|/|to|through|until|thru)\s*"
# One date, or one period, the document states. A period is two dates it
# states, so both of its ends are read and neither is ever inferred.
_DATE_TOKEN_PATTERN = re.compile(
    rf"{_TOKEN_BEFORE}(?P<atom>{_DATE_ATOM})"
    rf"(?:{_RANGE_JOIN}(?P<end>{_DATE_ATOM}))?"
    rf"{_TOKEN_AFTER}",
    re.IGNORECASE,
)
# The two shapes a temporal value may have, and no others: one date, or two
# dates joined by a separator. Anything else is not a date a document can
# state, so it is dropped rather than interpreted.
_VALUE_DATE_PATTERN = re.compile(rf"^(?P<atom>{_DATE_ATOM})$")
_VALUE_PERIOD_PATTERN = re.compile(
    rf"^(?P<start>{_DATE_ATOM}){_RANGE_JOIN}(?P<end>{_DATE_ATOM})$",
    re.IGNORECASE,
)

# How much of one read's own text a dossier shows the model.
DEFAULT_DOSSIER_EXCERPTS = 4
DEFAULT_DOSSIER_EXCERPT_CHARS = 400


def read_serving_host(read: ReadRecord) -> str:
    """Return the host that served this read's bytes.

    A transport fact, kept apart from the publisher: a mirror served from a
    repository is still the work it mirrors, and this is the field that says
    where the copy came from rather than who made it.
    """
    return source_domain(read.resolved_url)


def _document_text(read: ReadRecord) -> str:
    """The document's own words: its title and the text the reader extracted.

    This is the haystack every attribution is read from. The URL is not part
    of it: where a document was *served* from never publishes it.
    """
    return " ".join([read.title, *sorted(read.passages.values())])


def _dated_text(read: ReadRecord) -> str:
    """The document text plus the URL the bytes were served from.

    A versioned path such as ``/2024/report`` dates a document just as its
    title page does, so dating (unlike attribution) reads the serving URL too.
    """
    return " ".join([_document_text(read), read.resolved_url])


def _folded_read_text(read: ReadRecord) -> str:
    """Every dating signal the read carries, as foldable identity words."""
    return _identity_words(_dated_text(read))


def read_dated_tokens(read: ReadRecord) -> list[str]:
    """Return the distinct years this read carries, in sorted order.

    Deterministic and body-derived, so it is both the temporal component of an
    assessment revision and the check that stops a model reporting a
    publication year the document never states. A year has to be something the
    read states: the digits inside a product code ("ABC2026XYZ") date nothing
    and neither does the numeric part of a DOI or a URL path, while the
    allowlisted year notations ("FY2026", "CY2026") are how a document writes
    its own year.
    """
    return sorted(_read_date_tokens(read))


# How many days each month has in a year that is not a leap year. Shape alone
# cannot tell a date from a number that looks like one: the day a document
# writes has to exist in the month and the year it names.
_MONTH_LENGTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap_year(year: int) -> bool:
    """True for a year February has a 29th day in."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap_year(year):
        return 29
    return _MONTH_LENGTHS[month - 1]


def _is_date_atom(atom: str) -> bool:
    """True when ``atom`` is a real date, not a number shaped like one.

    "2026-13" has the shape of a year and a month and is neither: there is no
    thirteenth month, so the digits are a number that merely looks like a date
    and nothing may be read from them. The day is decided the same way, against
    the length of its own month in its own year: "2026-02-31" and "2025-02-29"
    name days those months never had, while a real leap day does.
    """
    parts = atom.split("-")
    if any(not part.isdigit() for part in parts):
        return False
    if len(parts) > 1 and not 1 <= int(parts[1]) <= 12:
        return False
    if len(parts) < 3:
        return True
    return 1 <= int(parts[2]) <= _days_in_month(int(parts[0]), int(parts[1]))


# Punctuation that joins the parts of ONE token rather than separating two of
# them: the bracket of a code or a filename, and the marks a URL path or query
# string uses. A date sitting against one of these is a fragment of an
# identifier — "ABC(2026)", "report(2025).pdf", "…/report;2025", "?id,2024" —
# so it dates nothing, which no digit masking can fix and only the boundary
# can.
_GLUE_MARKS = frozenset("()[]{};,")


def _continues_token(text: str, index: int) -> bool:
    """True when ``text[index]`` carries on a token instead of ending one."""
    char = text[index]
    if char.isalnum() or char == "_":
        return True
    # A full stop is the one ambiguous mark: a sentence-final one separates,
    # while the one in "10.5" or "report.pdf" continues a token.
    return char == "." and index + 1 < len(text) and text[index + 1].isalnum()


def _mark_is_inside_a_token(text: str, mark: int, step: int) -> bool:
    """True when the mark at ``mark`` is part of a longer token.

    A mark separates only when nothing that continues a token sits on its far
    side. "Grid Storage Outlook (2025)" and "in 2025, the queue grew" separate;
    "ABC(2026)", "report(2025).pdf", "…/report;2025" and "?id,2024" do not,
    because the mark is glued to a word rather than standing on its own.
    """
    far = mark + step
    if far < 0 or far >= len(text):
        return False
    return _continues_token(text, far)


def _is_delimited_date_token(text: str, start: int, end: int) -> bool:
    """True when the date spanning ``text[start:end]`` stands as its own token.

    A date is delimited by whitespace, the ends of the text, or ordinary
    sentence punctuation — never by a mark that makes it part of a longer
    token. The pattern that found the date already refuses a word character, an
    identifier's punctuation, and a range separator; this is the remaining
    case, where the character beside the digits is punctuation that is itself
    inside a token.
    """
    if start > 0:
        before = text[start - 1]
        if before in _GLUE_MARKS and _mark_is_inside_a_token(text, start - 1, -1):
            return False
    if end < len(text):
        after = text[end]
        if after in _GLUE_MARKS and _mark_is_inside_a_token(text, end, 1):
            return False
    return True


def _read_date_tokens(read: ReadRecord) -> set[str]:
    """Every date the read states, with the coarser forms each one evidences.

    "2026-01-15" evidences the year 2026 as well as that day, while a document
    that only says "2026" evidences no month and no day at all. The boundaries
    are the same real ones every date in this module is read with, so a year
    inside a longer alphanumeric word, the digits of an identifier, and a URL
    path all date nothing. A period the read states is two dates it states, so
    both of its ends evidence their own coarser forms too.
    """
    tokens: set[str] = set()
    dated = _dated_text(read)
    for match in _DATE_TOKEN_PATTERN.finditer(dated):
        if not _is_delimited_date_token(dated, match.start(), match.end()):
            continue
        for atom in (match.group("atom"), match.group("end")):
            if atom is not None and _is_date_atom(atom):
                tokens |= _token_forms(atom)
    return tokens


def _token_forms(atom: str) -> set[str]:
    """One date atom, and every coarser date it evidences."""
    parts = atom.split("-")
    return {
        "-".join(parts[:length]) for length in range(1, len(parts) + 1)
    }


def _issuer_evidenced(read: ReadRecord, issuer: str) -> bool:
    """True when the read attributes the document to ``issuer``.

    Attribution has to be stated — "Published by Example Lab", "Publisher:
    Example Lab", "Copyright 2026 Example Lab" — and said about *this*
    document. A body mention is not attribution, which is what keeps an
    article about a company from being recorded as that company's own
    publication and inheriting its authority.
    """
    words = _identity_words(issuer).split()
    if not words:
        return False
    name = _NAME_GAP.join(re.escape(word) for word in words)
    haystack = _document_text(read)
    for phrase in _ATTRIBUTION_PHRASES:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){phrase}{_ATTRIBUTION_GAP}"
            rf"(?:the\s+)?(?:\d{{4}}\s*)?{name}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        if pattern.search(haystack):
            return True
    return False


def _literal_evidenced(read: ReadRecord, value: str) -> bool:
    """True when the read carries ``value`` as identity words.

    Folding both sides makes a line-broken or differently punctuated copy of
    an identifier still the same identifier, while keeping the check a real
    containment test rather than a similarity score.
    """
    words = _identity_words(value)
    return bool(words) and words in _folded_read_text(read)


def validate_metadata_anchors(
    read: ReadRecord,
    anchors: Mapping[str, object],
) -> dict[str, object]:
    """Return the proposed metadata anchors this read actually evidences.

    A model may only report what the document shows: an issuer the read
    attributes the document to, a DOI the read carries, a year the read
    states, a report number the read prints. Everything else is dropped, so an
    unsupported issuer, DOI, or year can never reach an identity.
    """
    accepted: dict[str, object] = {}
    issuer = _text_field(anchors, "issuer")
    if issuer and _issuer_evidenced(read, issuer):
        accepted["issuer"] = issuer
    doi = _normalized_doi(_text_field(anchors, "doi"))
    if doi and _literal_evidenced(read, doi):
        accepted["doi"] = doi
    year = _year_text(anchors.get("year"))
    if year and year in read_dated_tokens(read):
        accepted["year"] = year
    number = _identifier_text(_text_field(anchors, "report_number"))
    if number and _literal_evidenced(read, number):
        accepted["report_number"] = number
    return accepted


def rejected_anchor_names(
    read: ReadRecord,
    anchors: Mapping[str, object],
) -> list[str]:
    """The proposed anchor names this read did not evidence, sorted.

    Recorded so a reviewer can tell "the model proposed nothing" from "the
    model proposed a publisher and the document did not support it".
    """
    accepted = validate_metadata_anchors(read, anchors)
    rejected = [
        name
        for name in ANCHOR_FIELDS
        if name not in accepted and _anchor_proposed(anchors, name)
    ]
    return sorted(rejected)


def _anchor_proposed(anchors: Mapping[str, object], name: str) -> bool:
    value = anchors.get(name)
    if name == "year":
        return bool(_year_text(value))
    return bool(_text_field(anchors, name))


def read_metadata_row(
    read: ReadRecord,
    *,
    anchors: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the identity row one read contributes, anchored to the read.

    A complete read contributes its title, its serving host, its complete
    content hash, and whichever proposed anchors the read evidences. A partial
    read contributes nothing but its own id: it is admissible evidence for the
    pages it read, but it is never an identity or equality edge, because the
    pages it never saw could say anything.
    """
    if not read.extraction_complete:
        return {"source_id": read.read_id}
    row: dict[str, object] = {
        "source_id": read.read_id,
        "title": read.title,
        "serving_host": read_serving_host(read),
        "complete_content_sha256": read.content_sha256,
        "extraction_complete": True,
    }
    row.update(validate_metadata_anchors(read, anchors or {}))
    return row


def read_identity(
    read: ReadRecord,
    *,
    anchors: Mapping[str, object] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(publisher_id, work_id)`` for one read.

    Both are ``None`` when the read establishes neither — an opaque URL with
    no evidenced issuer, or a partial read. Unknown identity is preserved as
    ``None`` rather than guessed, because unknown identity can establish
    neither sameness nor independence.
    """
    row = read_metadata_row(read, anchors=anchors)
    identity = resolve_work_identities([row]).get(read.read_id)
    work_id = identity.key if identity is not None else None
    return canonical_publisher_id(row), work_id


def _content_fingerprint(read: ReadRecord) -> str:
    """The read's content identity: its digest, or its partial-read body."""
    if read.extraction_complete:
        return read.content_sha256
    return _fingerprint(
        *(
            f"{locator}\x1e{canonical_read_text(text)}"
            for locator, text in sorted(read.passages.items())
        )
    )


def compute_assessment_revision(
    *,
    content_sha256: str,
    extraction_complete: bool,
    metadata_fingerprint: str,
    temporal_fingerprint: str,
) -> str:
    """Combine the three components of one source assessment's revision.

    The revision is the reuse key: content, read-derived metadata, and the
    dating signals must all be unchanged for a stored assessment to still
    describe the source in front of it. A URL alone is not enough, which is
    why no component may be dropped.
    """
    return "assess-" + _fingerprint(
        content_sha256,
        "complete" if extraction_complete else "partial",
        metadata_fingerprint,
        temporal_fingerprint,
    )[: _DIGEST_LENGTH]


def read_assessment_revision(
    read: ReadRecord,
    *,
    anchors: Mapping[str, object] | None = None,
) -> str:
    """Return the assessment revision one read's content, metadata, and dates form."""
    accepted = validate_metadata_anchors(read, anchors or {})
    metadata_fingerprint = _fingerprint(
        normalize_source_url(read.resolved_url),
        read_serving_host(read),
        _identity_words(read.title),
        *(f"{name}={accepted[name]}" for name in sorted(accepted)),
    )
    temporal_fingerprint = _fingerprint(*read_dated_tokens(read))
    return compute_assessment_revision(
        content_sha256=_content_fingerprint(read),
        extraction_complete=read.extraction_complete,
        metadata_fingerprint=metadata_fingerprint,
        temporal_fingerprint=temporal_fingerprint,
    )


def resolve_read_works(reads: Sequence[ReadRecord]) -> dict[str, str]:
    """Resolve one work key per read, joining only on evidenced aliases.

    Rows join exactly as :func:`resolve_work_identities` joins them — a shared
    DOI, issuer-namespaced report number, or complete-content hash, or the
    conservative title/year/issuer alias. A read whose identity cannot be
    established keeps its own key instead of being merged with another
    unknown, so the result never claims two documents are one work on the
    strength of a URL.
    """
    parsed = [_parse_row(read_metadata_row(read)) for read in reads]
    keys: dict[str, str] = {}
    for group in _group_rows(parsed):
        identity = _resolve_group(group)
        key = identity.key or "unresolved-" + _fingerprint(
            *(row.source_id for row in group)
        )[: _DIGEST_LENGTH]
        for row in group:
            keys[row.source_id] = key
    return keys


def resolve_read_work_keys(
    reads: Sequence[ReadRecord],
) -> dict[str, str]:
    """Map each read's canonical URLs to the work key it belongs to.

    Both URLs are indexed so a caller holding either one — a finding names the
    URL it cited — finds the read's work. This is a lookup alias only: the
    work key itself comes from the read's own identity evidence, never from
    the URL that was requested.
    """
    keys = resolve_read_works(reads)
    by_url: dict[str, str] = {}
    for read in reads:
        key = keys.get(read.read_id)
        if key is None:
            continue
        for url in (read.resolved_url, read.requested_url):
            by_url.setdefault(normalize_source_url(url), key)
    return by_url


def retained_work_count(
    source_urls: Sequence[str],
    reads: Sequence[ReadRecord],
) -> int:
    """Count the distinct works behind already-retained sources.

    A works count, not a URL count under a second name: two URLs serving the
    same complete document are one work, and a finding whose read is not in
    the registry counts as its own unresolved entry rather than being folded
    into a neighbour it was never shown to match.
    """
    by_url = resolve_read_work_keys(reads)
    distinct: set[str] = set()
    for url in source_urls:
        canonical = normalize_source_url(url)
        distinct.add(by_url.get(canonical, f"unresolved:{canonical}"))
    return len(distinct)


def source_origin_id(source: ScoredSource) -> str | None:
    """The claim-specific evidence origin this source can contribute, or ``None``.

    ``None`` means the source may not be half of an independent pair: an
    unscored source carries no assessment, a derivative or mixed document
    repeats someone else's work, and an unknown issuer establishes neither
    identity nor independence. A mirror is not ``None`` — it can be the first
    primary support — but it must never present as a *second* origin beside
    the work it mirrors.

    The origin is the discriminator, so it may only separate sources that are
    demonstrably different. A DOI or an issuer-namespaced report number is an
    evidenced alias for a work, and the origin is that work. A bare content
    hash is not: it names a set of bytes, and two copies of one report that
    were re-typeset hash differently while remaining one work — so a
    hash-only source contributes *its publisher's* origin instead. Two copies
    from one publisher therefore share one origin however their bytes differ,
    while two genuinely different documents from different publishers keep
    theirs apart.
    """
    if source.evaluation_status != "scored":
        return None
    if source.source_role in ("derivative", "mixed", "unknown"):
        return None
    if source.work_id and _strong_work_alias(source.work_id):
        return f"work:{source.work_id}"
    if source.publisher_id:
        return f"publisher:{source.publisher_id}"
    return None


# Work aliases that name a work rather than a set of bytes. ``report:`` is
# already namespaced by its issuer, so both separate two different works from
# one publisher, and both are inherited by a copy that carries them.
_STRONG_WORK_ALIASES = ("doi:", "report:")


def _strong_work_alias(work_id: str) -> bool:
    return work_id.startswith(_STRONG_WORK_ALIASES)


class EvidenceEligibility(ContractModel):
    """What one passage can contribute to an *independent corroboration pair*.

    Section 2.1's ``verified`` badge is the strict one: two selected passages,
    each supporting the COMPLETE atomic claim, whose known canonical publishers
    and works differ, whose claim-specific evidence origins are independent,
    and with no unresolved material contradiction defeating settlement. This
    record carries exactly the local half of that test — the identity fields
    Task 1/Task 4 resolved, and the three booleans the semantic half produced —
    so the pair rule is one pure function of two of them.

    Every identity field is *nullable*, and ``None`` never means "different":
    unknown identity establishes neither sameness nor independence, so a pair
    with an unknown publisher, work, or origin cannot stand (Section 2.2).
    """

    publisher_id: str | None = None
    work_id: str | None = None
    origin_group_id: str | None = None
    complete_support: bool = False
    read_valid: bool = False
    corroboration_eligible: bool = False


def eligible_independent_pair(
    a: EvidenceEligibility, b: EvidenceEligibility
) -> bool:
    """True only when ``a`` and ``b`` may corroborate each other independently.

    Both must be a valid read of a passage that completely supports the claim,
    contributed by a source eligible to corroborate at all; all six identity
    fields must be known; and publisher, work, and claim-specific origin must be
    pairwise different. A mirror, a second work from one publisher, two
    documents sharing one origin, and any pair with an unknown identity all
    fail — a URL count is never corroboration.
    """
    return (
        a.read_valid
        and b.read_valid
        and a.complete_support
        and b.complete_support
        and a.corroboration_eligible
        and b.corroboration_eligible
        and all(
            (
                a.publisher_id,
                b.publisher_id,
                a.work_id,
                b.work_id,
                a.origin_group_id,
                b.origin_group_id,
            )
        )
        and a.publisher_id != b.publisher_id
        and a.work_id != b.work_id
        and a.origin_group_id != b.origin_group_id
    )


def _vocabulary(value: object, allowed: Sequence[str], *, default: str) -> str:
    candidate = " ".join(str(value or "").split()).casefold()
    return candidate if candidate in allowed else default


def validated_source_role(claimed: object, *, issuer_evidenced: bool) -> str:
    """The role this read can support, or ``unknown``.

    Naming what a document is — its own report, someone else's statistic, a
    company's statement — requires knowing who published it, so a role with no
    evidenced issuer is recorded as unknown rather than believed.
    """
    role = _vocabulary(claimed, SOURCE_ROLES, default="unknown")
    if role != "unknown" and not issuer_evidenced:
        return "unknown"
    return role


def validated_transport_relation(
    claimed: object,
    *,
    issuer_evidenced: bool,
) -> str:
    """The transport relation this read can support, or ``unknown``.

    A mirror or a syndicated copy is a statement that the document came from
    somewhere else, so it can only be recorded when the read names the
    publisher it came from. Without that there is nothing to inherit and the
    relation stays unknown.
    """
    relation = _vocabulary(claimed, TRANSPORT_RELATIONS, default="unknown")
    if relation in ("mirror", "syndication") and not issuer_evidenced:
        return "unknown"
    return relation


def validated_self_interest(claimed: object, *, role: str) -> str:
    """The self-interest level this source carries.

    A company's own statement about its own product is self-interested by
    construction, so that role can never be recorded as disinterested — the
    label is part of what the source is, not a quality a high score can
    offset.
    """
    level = _vocabulary(claimed, SELF_INTEREST_LEVELS, default="unknown")
    if role == "company_statement" and level != "evidenced":
        return "evidenced"
    return level


class TemporalClaim(ContractModel):
    """One temporal value a model proposes, with the quote it read it from.

    Both halves are needed for the claim to mean anything: ``value`` is the
    model's own normalisation of a date and ``quote`` is the document's words.
    Either one empty is a field the document does not state, which is recorded
    as no value at all rather than as a date the model believed.
    """

    value: str = ""
    quote: str = ""


def validated_temporal(
    read: ReadRecord | None,
    *,
    publication_date: object = None,
    data_period: object = None,
    forecast_horizon: object = None,
    effective_date: object = None,
    status: object = "",
) -> SourceTemporal:
    """Keep the dates a quoted read evidences apart, and what freshness rests on.

    Each field is admitted the way an evidence excerpt is: the model proposes a
    value *and* the document's own phrase for it, and the read admits the claim
    only when that phrase is in the document verbatim and states exactly the
    value it was attached to. A field whose quote does not verify is not
    admitted at all — no value, and no freshness judgement resting on it —
    because a date the model inferred is not a date the document wrote. With no
    read there is nothing to verify against, so nothing is recorded.
    """
    dates = {
        "publication_date": _quoted_date(read, publication_date),
        "data_period": _quoted_date(read, data_period),
        "forecast_horizon": _quoted_date(read, forecast_horizon),
        "effective_date": _quoted_date(read, effective_date),
    }
    claimed = _vocabulary(status, FRESHNESS_STATUSES, default="unknown")
    required = _FRESHNESS_DATES.get(claimed)
    if required is None or dates.get(required) is None:
        claimed = "unknown"
    return SourceTemporal(**dates, status=claimed)  # type: ignore[arg-type]


def _quoted_date(read: ReadRecord | None, claimed: object) -> str | None:
    """The temporal value a verbatim quote in the read states, or ``None``.

    Containment is exact — the quote has to be the document's own words, not a
    paraphrase of them — and consistency is local to that quote: the value is
    admitted only when the quote states it. Nothing here scans the document for
    a date the model was expected to find, which is where a fabricated year
    came from.
    """
    claim = _temporal_claim(claimed)
    if read is None or claim is None:
        return None
    value = canonical_read_text(claim.value)
    quote = canonical_read_text(claim.quote)
    if not value or not quote:
        return None
    if not excerpt_matches(_document_text(read), quote):
        return None
    return _stated_value(quote, value)


def _temporal_claim(claimed: object) -> TemporalClaim | None:
    """``claimed`` as a quoted claim, or ``None`` when it is not one.

    A bare date with no quote behind it is what this contract replaced: it is a
    claim nothing can verify, so it is dropped rather than trusted.
    """
    if claimed is None or isinstance(claimed, TemporalClaim):
        return claimed
    if isinstance(claimed, Mapping):
        return TemporalClaim.model_validate(dict(claimed))
    return None


def _stated_value(quote: str, value: str) -> str | None:
    """The value a verified quote states, normalised, or ``None``.

    The model reads the date; this only checks that the document says what the
    model says it says. One verification serves the whole contract: the quote
    must state the value's own date — or, for a period, both of its ends in
    order — as its own token rather than as a fragment of an identifier that
    happens to contain the same digits.
    """
    single = _VALUE_DATE_PATTERN.match(value)
    if single is not None:
        atom = single.group("atom")
        if not _is_date_atom(atom):
            return None
        return atom if _quote_states(quote, (atom,)) else None
    period = _VALUE_PERIOD_PATTERN.match(value)
    if period is None:
        return None
    start, end = period.group("start"), period.group("end")
    if not (_is_date_atom(start) and _is_date_atom(end)) or end < start:
        # A period never runs backwards, and a thirteenth month is not an end
        # a calendar has, so neither is a period the document stated.
        return None
    if not _quote_states(quote, (start, end)):
        return None
    return f"{start}-{end}"


def _quote_states(quote: str, atoms: tuple[str, ...]) -> bool:
    """True when ``quote`` states exactly this date, or exactly this period.

    One date is admitted from a date token the quote writes at the same
    precision or a finer one: the year 2026 is what "2026-01-15" states about a
    date nobody wrote a month or a day for. A period is admitted only from a
    period the quote writes with those two ends, so two years a document never
    joined are not a period, and a stated period is never recorded as one of
    its ends.
    """
    for stated in _stated_dates(quote):
        if len(atoms) == 1:
            (atom,) = atoms
            if len(stated) == 1 and (
                stated[0] == atom or stated[0].startswith(f"{atom}-")
            ):
                return True
        elif stated == atoms:
            return True
    return False


def _stated_dates(quote: str) -> list[tuple[str, ...]]:
    """Every date and period the quote states, each as its own token.

    A number shaped like a date and not one — "2026-13" — is not a date the
    quote states, and neither is a fragment of an identifier: the digits of
    "doi:10.1234/grid.2025" are the identifier's own numbers, and the boundary
    is what says so.
    """
    stated: list[tuple[str, ...]] = []
    for match in _DATE_TOKEN_PATTERN.finditer(quote):
        if not _is_delimited_date_token(quote, match.start(), match.end()):
            continue
        atoms = tuple(
            atom
            for atom in (match.group("atom"), match.group("end"))
            if atom is not None
        )
        if all(_is_date_atom(atom) for atom in atoms):
            stated.append(atoms)
    return stated


class ReadDossier(ContractModel):
    """One read-backed dossier: everything the model may judge, all read-derived.

    The read itself is carried so a caller can validate the model's proposed
    anchors against the same bytes it was shown, and the revision is computed
    before the call so a stored assessment can be reused without one.
    """

    read: ReadRecord
    url: str = Field(min_length=1)
    serving_host: str = Field(min_length=1)
    excerpts: list[str] = Field(default_factory=list)
    assessment_revision: str = Field(min_length=1)
    cited_sub_topics: list[str] = Field(default_factory=list)


def build_read_dossiers(
    reads: Sequence[ReadRecord],
    *,
    cited_sub_topics: Mapping[str, Sequence[str]] | None = None,
    excerpt_chars: int = DEFAULT_DOSSIER_EXCERPT_CHARS,
    max_excerpts: int = DEFAULT_DOSSIER_EXCERPTS,
) -> list[ReadDossier]:
    """Build one read-backed dossier per canonical URL, in first-seen order.

    One URL has one current read: the complete read wins over a partial one,
    and the latest observation wins over an earlier one, so a re-read that
    recovered a lost page is assessed instead of the failure it replaced.
    """
    if excerpt_chars < 1 or max_excerpts < 1:
        raise EvidenceContractError(
            "a dossier requires at least one excerpt of at least one character"
        )
    cited = {
        normalize_source_url(url): list(topics)
        for url, topics in (cited_sub_topics or {}).items()
    }
    chosen: dict[str, ReadRecord] = {}
    for read in reads:
        url = normalize_source_url(read.resolved_url)
        current = chosen.get(url)
        if current is None or _prefer_read(read, current):
            chosen[url] = read
    return [
        ReadDossier(
            read=read,
            url=url,
            serving_host=read_serving_host(read),
            excerpts=_dossier_excerpts(
                read, excerpt_chars=excerpt_chars, max_excerpts=max_excerpts
            ),
            assessment_revision=read_assessment_revision(read),
            cited_sub_topics=cited.get(url, []),
        )
        for url, read in chosen.items()
    ]


def _prefer_read(candidate: ReadRecord, current: ReadRecord) -> bool:
    if candidate.extraction_complete != current.extraction_complete:
        return candidate.extraction_complete
    return candidate.retrieved_at >= current.retrieved_at


def _dossier_excerpts(
    read: ReadRecord,
    *,
    excerpt_chars: int,
    max_excerpts: int,
) -> list[str]:
    """The read's own text at its first locators, bounded for the prompt.

    Deliberately the document's words rather than a finding's paraphrase: the
    judgement being asked for is about the document, and a paraphrase is the
    model's own earlier summary of it.
    """
    excerpts: list[str] = []
    for locator in sorted(read.passages):
        text = " ".join(read.passages[locator].split())
        if not text:
            continue
        excerpts.append(text[:excerpt_chars])
        if len(excerpts) == max_excerpts:
            break
    return excerpts


# ---------------------------------------------------------------------------
# strict read producers
# ---------------------------------------------------------------------------


def build_read_id(
    *,
    session_id: str,
    reader: str,
    resolved_url: str,
    content_sha256: str,
    passages: Mapping[str, str] | None = None,
) -> str:
    """Return the stable identity of one read body inside one session.

    Derived only from immutable read fields, so the same body read twice in a
    session is one read — and a re-read that returns different content is a
    different one. Identity resolution, scoring, and target association are
    deliberately absent from the key: they are assessments, and an assessment
    must never renumber evidence.

    A partial extraction has no content hash to identify it by, so the
    locator-keyed text it did read is folded in instead; without that, every
    partial read of one URL in a session would share an ID, and a retry that
    recovers a lost page would look like a conflicting rewrite of the same
    read rather than a second one.
    """
    digest = content_sha256.strip().casefold()
    salt = ""
    if digest == INCOMPLETE_CONTENT_SHA256:
        if not passages:
            raise EvidenceContractError(
                "a partial read requires the passages it did read"
            )
        salt = _fingerprint(
            *(
                f"{locator}\x1e{canonical_read_text(text)}"
                for locator, text in sorted(passages.items())
            )
        )
    return "read-" + _fingerprint(
        session_id,
        reader,
        normalize_source_url(resolved_url),
        digest,
        salt,
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
    content, a passage that is not verbatim text of that body, and a declared
    hash that disagrees with the body it was taken from all fail here rather
    than becoming evidence. ``text`` is the extracted document; ``passages``
    maps the locator the reader reported to the text at that locator.

    ``extraction_complete=False`` records a read that lost part of its
    document — a PDF page that would not parse, a scanned page with no text —
    as a read of the locators it did extract, with
    ``INCOMPLETE_CONTENT_SHA256`` in place of a content hash. Discarding such
    a document outright would throw away evidence that Section 2.1 admits (an
    exact excerpt with a locator from a successful same-run read), while
    letting it keep a digest would identify a work nobody fully read.
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

    if extraction_complete:
        content_sha256 = normalized_content_sha256(canonical)
        if declared_content_sha256 is not None and (
            declared_content_sha256.strip().casefold() != content_sha256
        ):
            raise EvidenceContractError(
                "the declared content hash does not match the read body"
            )
    else:
        declared = (declared_content_sha256 or "").strip().casefold()
        if declared and declared != INCOMPLETE_CONTENT_SHA256:
            raise EvidenceContractError(
                "an incomplete extraction must not declare a content hash"
            )
        content_sha256 = INCOMPLETE_CONTENT_SHA256

    return ReadRecord(
        read_id=build_read_id(
            session_id=session_id,
            reader=reader,
            resolved_url=resolved,
            content_sha256=content_sha256,
            passages=checked,
        ),
        requested_url=requested,
        resolved_url=resolved,
        title=" ".join(title.split()) or resolved,
        reader=reader,
        retrieved_at=retrieved_at,
        content_sha256=content_sha256,
        extraction_complete=extraction_complete,
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

    Only a *complete* original is ever admitted here. A partial read is
    admissible evidence in its own right, but it has no content identity, so
    there is nothing for a later version to be checked against and the marker
    it carries is refused rather than treated as an expected hash.
    """
    _require_aware_timestamp(validated_at, name="validated_at")
    expected = expected_content_sha256.strip().casefold()
    if expected == INCOMPLETE_CONTENT_SHA256:
        return None
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
    if getattr(record, "extraction_complete", None) is not True:
        # Strictly typed: a serialized ``"false"`` is not completeness, and a
        # truthy string must never pass a truthiness test here.
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
    that body are the same read — the earlier observation, the first
    observation's title, and both target associations are kept — while a
    disagreement about the body is a conflict that raises, because
    overwriting either one would silently re-point stored evidence.
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
    """The immutable facts of a read; assessments and stamps are not here.

    ``title`` is deliberately outside the body: the same bytes fetched twice
    can be labelled differently (a re-read whose extractor fell back to the
    URL), and that is not a second body. The rule for which label survives is
    explicit in :func:`_combine_reads` rather than accidental.
    """
    return (
        record.reader,
        record.requested_url,
        record.resolved_url,
        record.content_sha256,
        record.extraction_complete,
        tuple(sorted(record.passages.items())),
    )


def _combine_reads(existing: ReadRecord, incoming: ReadRecord) -> ReadRecord:
    """One record for one read ID: first observation wins where they differ.

    The earlier ``retrieved_at`` and the earlier ``title`` are the record's
    history — a later re-read cannot restate when the evidence was observed or
    relabel a passage that already cites it. A same-session network read does
    replace a cache import of the same body, because it is the stronger
    observation, and target associations always union so nothing a later
    selector contributed is dropped.
    """
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
        update={
            "retrieved_at": observed,
            "title": existing.title,
            "target_ids": targets,
        }
    )


def merge_evidence_units(
    previous: Mapping[str, EvidenceUnit],
    current: Mapping[str, EvidenceUnit],
) -> dict[str, EvidenceUnit]:
    """Fold new evidence units in, unioning targets and refusing rewrites.

    One passage read by both agents is reuse, not conflict: the unit keeps the
    agent that selected it first and the union of every target that later
    selected it, so the second selection adds associations instead of being
    silently dropped. A different recorded source label for that one passage
    is a disagreement about what the passage is, and raises.
    """
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
            or existing.source_title != unit.source_title
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
    """Append dispositions, refusing contradictions for one item at one stage.

    One item has one reason per stage. A repeat with the same reason unions its
    targets, and a later pass may fill in the retained equivalent it resolved —
    but two different retained equivalents, or two different reasons, describe
    two different omissions wearing one identity, so they raise.
    """
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
        retained = known.retained_equivalent_id
        if retained is None:
            retained = item.retained_equivalent_id
        elif (
            item.retained_equivalent_id is not None
            and item.retained_equivalent_id != retained
        ):
            raise EvidenceIdentityConflict(
                f"item {item.item_id!r} already points at retained evidence "
                f"{retained!r} at stage {item.stage!r}"
            )
        targets = list(known.target_ids)
        for target_id in item.target_ids:
            if target_id not in targets:
                targets.append(target_id)
        merged[position] = known.model_copy(
            update={
                "target_ids": targets,
                "retained_equivalent_id": retained,
            }
        )
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
