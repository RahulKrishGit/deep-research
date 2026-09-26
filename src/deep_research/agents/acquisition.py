"""Acquisition policy, read admission, caching, and decision context.

The module is deliberately provider-agnostic. A native ReAct loop supplies
tool results here; this code decides which results are admissible, retains
complete bodies, and exposes only exact selected passages to extraction.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import JsonValue

from deep_research.agents.evidence import (
    PASSAGE_SELECTION_OPERATION,
    READ_ADMISSION_OPERATION,
    EvidenceContractError,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    merge_evidence_units,
    passages_from_chunks,
    validate_cached_read,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActDecision, ReActStep, summarize_text
from deep_research.tools.base import ToolResult
from deep_research.tools.passage_selection import (
    is_link_dense,
    select_passages_by_budget,
    select_relevant_passages,
)
from deep_research.utils.types import (
    AcquisitionState,
    Finding,
    CandidateRecord,
    EvidenceDisposition,
    EvidenceUnit,
    ReadRecord,
    _canonical_acquisition_url,
)

AcquisitionAction = Literal["search", "read", "extract", "finish"]
# The agents that select evidence. Only the researcher does: the Fact Checker
# that used to verify claims is deleted, so an admission for another selector
# would name an agent this branch cannot run.
OriginName = Literal["researcher"]

# The single line a packet falls back to when even the continuation list cannot
# fit inside the configured budget. Kept short so it fits wherever a packet is
# allowed to exist at all, and never a sliced record.
_CONTEXT_OVERFLOW = "continuation_ids=packet_overflow"

# How much text one passage of a web page may carry. A page is not one passage:
# it is read as paragraph-bounded passages, so selection and packets can
# address the sentence that carries a figure instead of the site navigation
# that happens to precede it.
WEB_PASSAGE_CHARS = 600

# The end of a paragraph, separator included. ``\r\n\r\n`` and a line of spaces
# between two blocks are both a break; the cut always falls *after* it, so a
# passage keeps the whitespace that belongs to it and the split stays lossless.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t\r]*\n")

# The end of a sentence and the end of a clause, whitespace included, used when
# no paragraph break fits inside the bound. A cut at the last space inside the
# bound can fall mid-clause, so a rule's clause ends one passage and the object
# the page attaches to it opens the next: a snippet drawn from the first reads
# as the whole rule although the page states a narrower or wider one. A cut at a
# sentence, or at a comma, semicolon or colon when the sentence runs on, keeps
# the rule's own words together. A sentence ends only where what follows opens a
# new one -- a capital letter, or another letter with no case -- so "Art. 53",
# "No. 4" and "10.5 percent" are not sentence ends; an initialism followed by a
# capital ("U.S. Energy ...") still reads as one, and the cut then falls at that
# whitespace rather than at a later boundary.
_SENTENCE_END = re.compile(r"[.!?\u2026]\s+(?=[\"'(\[]?[^\Wa-z\d_])")
_CLAUSE_BREAK = re.compile(r"[,;:]\s+")

# The boundary must sit in the last half of the window: one earlier would answer
# a mid-clause cut with a passage that is mostly bound and little text, and every
# later passage would be short for the same reason.
_BOUNDARY_FLOOR_DIVISOR = 2

# How much text a page may carry and still be read as an automated-access
# shell. A browser check, a consent wall, or a denial page is always a few
# hundred characters; a real document is not, which is what keeps a report
# that merely mentions "access denied" from losing its read record.
_SHELL_CONTENT_MAX_CHARS = 2000

# What a host's error handler says instead of a document. A title carrying
# one of these *as its own label* -- not merely mentioning it in a longer
# headline -- is the publisher's name for the page it served; the same words
# in a body are the handler's message, and only classify a body too short to
# have been an article. "access denied" is deliberately absent here: a page
# that only says so is a denial, which the shell markers below already
# cover, and keeping it here would relabel every short "Access Denied" WAF
# page as a served error page instead of the automated-access shell it is.
_ERROR_PAGE_MARKERS = (
    "unexpected error",
    "404 not found",
    "page not found",
    "request rejected",
)

# The automated-access shells a *short* body announces.
_SHELL_MARKERS = (
    "enable javascript",
    "checking your browser",
    "please verify you are human",
    "are you a robot",
    "captcha",
    "robot check",
    "accept cookies to continue",
    "consent management",
    "access denied",
    "automated access",
    "just a moment",
)

# A handler and a site name are usually joined by one of these, e.g.
# "EIA - Sorry! Unexpected Error" or "Access Denied: Storage Queues Explained".
_TITLE_LABEL_SEPARATORS = re.compile(r" - | \| | – |:")

# Interjections and generic nouns a handler pads its own label with. Dropped
# from *both* a title clause and a marker before they are compared, so
# "Sorry! Unexpected Error" reduces to the same words as "unexpected error",
# and a bare "404" prefix does not stop "404 Not Found" from reducing to
# "not found" on both sides of the comparison.
_TITLE_LABEL_FILLER_WORDS = frozenset({"sorry", "404", "error"})
_TITLE_LABEL_WORD = re.compile(r"[a-z0-9]+")


def _title_label_words(text: str) -> tuple[str, ...]:
    """The words ``text`` reduces to once punctuation and filler are dropped."""
    words = _TITLE_LABEL_WORD.findall(text)
    return tuple(word for word in words if word not in _TITLE_LABEL_FILLER_WORDS)


_ERROR_PAGE_MARKER_WORDS = frozenset(
    _title_label_words(marker) for marker in _ERROR_PAGE_MARKERS
)


def _title_is_error_label(normalized_title: str) -> bool:
    """True when the title *is* an error label, not merely mentions one.

    A handler names its page directly -- the whole title, or its final
    clause once a site name is split off: "EIA - Sorry! Unexpected Error",
    "Page Not Found". A real document that happens to use the same words in
    a longer headline, "Access Denied: How Interconnection Queues Shut Out
    Storage", is not labelled by them; only the clause that equals a marker
    outright, word for word once filler is dropped, is decisive.
    """
    last_clause = _TITLE_LABEL_SEPARATORS.split(normalized_title)[-1]
    return any(
        _title_label_words(candidate) in _ERROR_PAGE_MARKER_WORDS
        for candidate in (normalized_title, last_clause)
    )

# Candidate discoveries a URL may be read on. Any URL the run was actually
# given — by a search result, a memory lead, or a document link it followed —
# stays readable after it leaves the queue, including on a host that denied a
# different page: a denial is never a publisher-wide circuit. A URL with no
# such record is the model's guess about the publisher's file layout.
_DISCOVERED_VIA = frozenset({"search", "memory", "document_link"})

# Bounded continuation batches per read. Batch one is the selection taken at
# admission; one further batch is the plan's "second bounded passage batch".
# The bound is what makes the local extract handoff terminate instead of
# growing a pending list acquisition can never leave.
_DEFAULT_PASSAGE_BATCH_LIMIT = 2

# One already-recorded finding's statement, as the packet's steering row
# carries it: long enough to recognize the sentence a later pass is about to
# mine again, short enough that the row never crowds out the evidence the
# packet is for.
_RECORDED_STATEMENT_CHARS = 200

# The disposition a selected unit gets when it states a figure in a measure
# unit the active target asks for and no finding was extracted from it — not
# by the extraction, and not by the one bounded re-extraction that follows it.
# Deliberately not ``irrelevant``: such a passage is not beside the point, it
# carries the figure the target asks for and the extraction walked past it,
# and the ledger has to be able to say so.
UNMINED_QUANTITY_REASON = "unmined_quantity"

# The same disposition for a passage that states the words of a required
# obligation the pass has answered nowhere yet and no finding was extracted
# from it, the bounded re-extraction included. Also deliberately not
# ``irrelevant``: the run held the passage that states the obligation's own
# words and still reported the obligation unbound, which the ledger has to be
# able to say.
UNMINED_TARGET_REASON = "unmined_target"

# The disposition for a unit whose own page's extraction call itself failed
# this pass (S6, RevSelectionR3 P1): the provider never actually mined this
# passage, so ``irrelevant`` -- a judgement the extraction never got the
# chance to make -- would misstate what happened. This reason is what the
# ledger shows instead. The read id is excluded from *this call's own*
# ``AcquisitionPolicy.complete_extraction`` (its ``except_read_ids``), so it
# is not marked consumed the moment it failed; that is not a durable
# "still owed" record across passes, though -- a later pass's own
# ``complete_extraction`` call consumes whatever it finds pending on its
# own terms, and ``pending_extraction_ids`` only ever holds a read at all
# when its admission deferred passages past its own budget. What actually
# gives a failed page a further chance is that its units are never removed
# from the evidence registry: a later pass with nothing new to read falls
# back to one legacy-shaped call over the whole registry (see
# ``extract_findings``'s own fallback), and the owed-batch mechanism can
# still pick its passages up if they turn out to owe a figure or a
# required target's own words.
EXTRACTION_FAILED_REASON = "extraction_failed"


def next_acquisition_action(state: AcquisitionState) -> AcquisitionAction:
    """Return the next deterministic local acquisition action."""
    if state.pending_passage_ids:
        return "extract"
    if state.remaining_calls <= 0:
        return "finish"
    if state.remaining_model_turns <= 0:
        # The loop's last tool turn: the counter is the turns left *after* this
        # one, so zero is the turn nothing follows. A search here queues
        # candidates no turn is left to read — whether or not candidates are
        # already waiting, which is why this bound is read before the queue's own
        # guard: with candidates the last turn is a read, and with an empty queue
        # there is nothing left that could be finished, so the loop stops. At one
        # turn left a search is still admissible (the next turn can read it), and
        # beside a queued read ``search_is_admissible`` is the stricter bound.
        return "read" if state.candidate_urls else "finish"
    if state.candidate_urls and (
        state.consecutive_searches >= 2
        # The call budget's last call is a read for the same reason the last
        # turn is: a search there queues candidates nothing is left to drain.
        or state.remaining_calls == 1
    ):
        return "read"
    if not state.candidate_urls and state.empty_searches >= 2:
        return "finish"
    return "read" if state.candidate_urls else "search"


def search_is_admissible(state: AcquisitionState) -> bool:
    """True when a search may run even though a queued read is expected.

    The candidate queue is a reading order, not a lock on the target's
    discovery. One search queues several candidates, and a sub-topic that needs
    two named sources — or whose first search returned poor candidates — has to
    be able to search again while those wait, or its turns go on forced reads
    and its remaining searches never happen.

    Three bounds keep the queue's discipline. ``consecutive_searches < 2`` is
    the anti-search-spam guard ``next_acquisition_action`` already enforced:
    after two searches in a row the next call is a read. ``remaining_calls > 1``
    is the last call of the budget, and ``remaining_model_turns > 1`` is the
    last turn of the loop — either one is a read, because a search there could
    queue candidates nothing is left to drain, which is how a target ends with
    candidates and no evidence. The turn bound is the one that binds in the
    shipped configuration (``agents.max_iterations`` 7 against a call budget of
    20), which is why the call bound alone never fired live.
    """
    return (
        bool(state.candidate_urls)
        and state.consecutive_searches < 2
        and state.remaining_calls > 1
        and state.remaining_model_turns > 1
    )


def allowed_acquisition_actions(
    state: AcquisitionState,
) -> tuple[AcquisitionAction, ...]:
    """Every action the policy accepts right now, the preferred one first.

    ``next_acquisition_action`` names the action the loop *prefers*; this names
    every kind ``before_action`` will accept, and it is that much longer only
    when a queued read and a fresh search are both legitimate (see
    ``search_is_admissible``). The decision packet renders this set and the
    policy judges by it, so the context the model reads and the admission it
    meets cannot disagree — a model that searches while a read is expected is
    not refused for it, and neither an extract the queue asked for nor a
    finish the budget asked for is ever joined by anything else.
    """
    expected = next_acquisition_action(state)
    if expected == "read" and search_is_admissible(state):
        return ("read", "search")
    return (expected,)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(*parts: object) -> str:
    encoded = json.dumps(
        parts, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str:
    return value if isinstance(value, str) and value.strip() else ""


def _bool(value: object, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _chunk_text(chunks: Sequence[Mapping[str, object]]) -> str:
    return "".join(
        item.get("text", "")
        for item in chunks
        if isinstance(item.get("text"), str)
    )


def _split_survives_normalization(text: str, cut: int) -> bool:
    """True when ``text`` may be divided at ``cut`` without losing a character.

    A passage is verified as verbatim text of the read body *after* NFC
    normalization, so a cut between a base character and its combining marks
    produces two passages the body does not contain: the read contract refuses
    them and the whole page is dropped. The check is exact, and the caller only
    ever backs a cut off by the length of one character.
    """
    return unicodedata.normalize("NFC", text[:cut]) + unicodedata.normalize(
        "NFC", text[cut:]
    ) == unicodedata.normalize("NFC", text)


def split_read_body(
    text: str, *, limit: int = WEB_PASSAGE_CHARS
) -> list[str]:
    """Split a read body into contiguous, non-blank, bounded passages.

    Every passage is verbatim text of the body and the passages concatenate
    back to it exactly, so the content hash of a read — and therefore its
    identity — is what one unsplit read of the same bytes would have. Cuts
    fall after a paragraph break where one fits inside the bound, otherwise
    after the last sentence or clause end in the window's second half, and
    otherwise after the last whitespace, so a passage ends mid-word only where
    the document has no whitespace to cut on and mid-clause only where the
    window's second half holds no boundary either.

    A body shorter than the bound is one passage, which is what a short page
    or a short document chunk was before; the split only ever changes how a
    long body is addressed. Both readers use it: a ``document_reader`` chunk is
    a whole PDF page of up to 8,000 characters, while the adjudication request
    holds 4,000, so without this a page longer than the request would be
    carried alone and cut and could never be a complete support.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    cuts = [0]
    position = 0
    length = len(text)
    while length - position > limit:
        window = position + limit
        breaks = [
            match.end()
            for match in _PARAGRAPH_BREAK.finditer(text, position, window)
        ]
        cut = breaks[-1] if breaks and breaks[-1] > position else 0
        if not cut:
            floor = position + limit // _BOUNDARY_FLOOR_DIVISOR
            cut = _last_boundary(text, floor, window)
        if not cut:
            whitespace = max(
                text.rfind(" ", position, window),
                text.rfind("\n", position, window),
                text.rfind("\t", position, window),
            )
            cut = whitespace + 1 if whitespace >= position else window
        if cut <= position:
            cut = window
        # Never between a base character and its combining marks.
        while cut > position + 1 and not _split_survives_normalization(
            text, cut
        ):
            cut -= 1
        cuts.append(cut)
        position = cut
    cuts.append(length)
    passages = [text[start:end] for start, end in zip(cuts, cuts[1:])]
    # A passage that holds nothing but whitespace is not a passage the contract
    # admits, and it is not text the body does not have: it joins its
    # neighbour, so the split stays lossless and every locator is readable.
    merged: list[str] = []
    for passage in passages:
        if merged and not passage.strip():
            merged[-1] += passage
        else:
            merged.append(passage)
    if len(merged) > 1 and not merged[0].strip():
        merged[1] = merged[0] + merged[1]
        merged = merged[1:]
    return merged


def _last_boundary(text: str, floor: int, window: int) -> int:
    """The last sentence or clause end inside ``text[floor:window]``, or ``0``.

    The last one, not the strongest: both keep a rule's clause whole, and the
    last boundary near the bound carries the most of the body a passage can
    hold, so the passages a read is addressed by stay as long as they were.
    """
    return max(
        (
            match.end()
            for pattern in (_SENTENCE_END, _CLAUSE_BREAK)
            for match in pattern.finditer(text, floor, window)
        ),
        default=0,
    )


def _payload_read_parts(
    result: ToolResult,
) -> (
    tuple[
        str, str, str, str, str, dict[str, str], bool, str | None,
        str | None, str | None,
    ]
    | None
):
    """Extract the complete read shape produced by either read tool."""
    if not result.success or (
        result.error is not None
        and result.error.type == "empty_document_content"
    ):
        return None
    data = _as_mapping(result.data)
    if data is None:
        return None
    if result.tool_name == "web_scraper":
        text = _text(data.get("text"))
        requested = _text(data.get("requested_url")) or _text(data.get("url"))
        resolved = _text(data.get("resolved_url")) or requested
        if not (text and requested and resolved):
            return None
        chunks: list[Mapping[str, object]] = [
            {"text": passage, "chunk_index": index}
            for index, passage in enumerate(split_read_body(text))
        ]
        reader = "web_scraper"
        title = _text(data.get("title")) or resolved
        # D14: the scraper's own dates, threaded straight from its payload
        # -- never re-derived here, so the extraction lives in one place
        # (``tools.web_scraper``).
        page_published = _text(data.get("page_published")) or None
        page_updated = _text(data.get("page_updated")) or None
    elif result.tool_name == "document_reader":
        raw_chunks = data.get("chunks")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            return None
        if any(not isinstance(item, Mapping) for item in raw_chunks):
            return None
        # A page is laid out as bounded passages, exactly as a web body is: the
        # reader's own chunk bound (8,000) is larger than a request (4,000), and
        # a page split here is what keeps every passage a candidate the model
        # can read whole. The page number and a document-wide chunk index are
        # kept, so the locator vocabulary is unchanged.
        chunks: list[Mapping[str, object]] = []
        # A locator is ``page-N-chunk-M``, so what must be unique is the pair:
        # a reader that numbers its chunks per page (page 1 chunk 0, page 2
        # chunk 0) keeps that scheme, and a page split here claims the next free
        # index on its own page only.
        claimed: set[tuple[object, int]] = set()
        for item in raw_chunks:
            piece_text = item.get("text")
            if not isinstance(piece_text, str) or not piece_text.strip():
                return None
            page = item.get("page")
            reported = item.get("chunk_index")
            if isinstance(reported, bool) or not isinstance(reported, int):
                # The reader's index is required by the read contract; a
                # missing or malformed one is a payload this project did not
                # produce, never a reason to invent locators.
                return None
            index = reported
            for passage in split_read_body(piece_text):
                # The reader's own index stands for the passage it labelled, and
                # a page split into several passages claims the next free
                # indices, so a multi-page document keeps the locator scheme it
                # had and no two passages share a name.
                while (page, index) in claimed:
                    index += 1
                chunk: dict[str, object] = {
                    "text": passage,
                    "chunk_index": index,
                }
                claimed.add((page, index))
                index += 1
                if page is not None:
                    chunk["page"] = page
                chunks.append(chunk)
        text = _chunk_text(chunks)
        requested = _text(data.get("requested_source")) or _text(
            data.get("source")
        )
        resolved = _text(data.get("resolved_source")) or requested
        if not (text and requested and resolved):
            return None
        reader = "document_reader"
        title = _text(data.get("title")) or resolved
        # A document has no page carrying the HTML metadata D14 reads; never
        # inventing one is the honest default.
        page_published = None
        page_updated = None
    else:
        return None
    try:
        passages = passages_from_chunks(chunks)
    except EvidenceContractError:
        return None
    if _content_limitation(text, title) is not None:
        return None
    declared_hash = _text(data.get("content_sha256")) or None
    extraction_complete = _bool(data.get("extraction_complete"), True)
    return (
        reader,
        requested,
        resolved,
        title,
        text,
        passages,
        extraction_complete,
        declared_hash,
        page_published,
        page_updated,
    )


def _content_limitation(text: str, title: str = "") -> str | None:
    """Classify a served error page or automated-access shell, or admit the read.

    A shell page is *short* and says one of a few things: a browser check, a
    consent wall, or an outright denial. The marker words alone cannot say
    that — a genuine report that *discusses* access denial, captchas, or
    consent management is a document, and refusing it a read record would
    invert the rule that a short authoritative page is never classified
    unusable by length alone. A marker therefore only classifies a shell when
    the body could not have carried a document in the first place.

    A served error page is the same refusal with a different cause: a host
    answers ``200`` with its own handler — "EIA - Sorry! Unexpected Error" —
    and the body is the handler's message, not a publication. Only a title
    that *is* the error label, word for word once filler is dropped, decides
    that regardless of length; a title that merely uses the same words in a
    longer headline — "Access Denied: How Interconnection Queues Shut Out
    Storage" — carries no such label and falls through to the checks below.
    A body mention alone classifies a short page only when its title also
    carries a marker, which is the case for a genuine handler's title and
    body agreeing on the same failure; the same words in an otherwise
    ordinary short body — a press release reporting an "unexpected error" in
    its own operations — are not the page's label and keep their read.
    """
    body = " ".join(text.split()).casefold()
    normalized_title = " ".join(title.split()).casefold()
    if _title_is_error_label(normalized_title):
        return "unusable_error_page"
    if len(body) > _SHELL_CONTENT_MAX_CHARS:
        return None
    lowered = f"{normalized_title} {body}"
    if any(marker in lowered for marker in _SHELL_MARKERS):
        return "unusable_content_shell"
    if any(marker in normalized_title for marker in _ERROR_PAGE_MARKERS) and any(
        marker in body for marker in _ERROR_PAGE_MARKERS
    ):
        return "unusable_error_page"
    return None


def build_read_record_from_tool_result(
    result: ToolResult,
    *,
    session_id: str,
    retrieved_at: str | None = None,
    target_ids: Sequence[str] = (),
) -> ReadRecord | None:
    """Build one complete ``ReadRecord`` from a successful actual payload.

    ``ToolResult.data`` is intentionally ignored for failed calls, including
    the documented ``empty_document_content`` failure. The complete chunk
    mapping is handed to the shared read contract before any passage is
    selected, so a later selector cannot create an identity conflict.
    """
    if not result.success:
        return None
    parts = _payload_read_parts(result)
    if parts is None:
        return None
    (
        reader,
        requested,
        resolved,
        title,
        text,
        passages,
        extraction_complete,
        declared_hash,
        page_published,
        page_updated,
    ) = parts
    try:
        return build_read_record(
            session_id=session_id,
            reader=reader,
            requested_url=requested,
            resolved_url=resolved,
            title=title,
            retrieved_at=retrieved_at or _utc_now_iso(),
            text=text,
            passages=passages,
            extraction_complete=extraction_complete,
            declared_content_sha256=declared_hash,
            target_ids=target_ids,
            page_published=page_published,
            page_updated=page_updated,
        )
    except (TypeError, ValueError):
        return None



@dataclass(frozen=True, slots=True)
class ReadAdmission:
    """Everything one successful read contributed to state."""

    read: ReadRecord
    evidence: dict[str, EvidenceUnit]
    dispositions: tuple[EvidenceDisposition, ...]
    boundary_audits: dict[str, Any]


def _adopt_recorded_read(
    read: ReadRecord,
    recorded: Mapping[str, ReadRecord],
) -> ReadRecord:
    """Return the run's recorded description of ``read``'s identity, if any.

    A read is identified by its reader, its resolved URL, and its body, so a
    second read of the same document — another sub-topic's retry, the Fact
    Checker repairing a handoff loss — mints the same ``read_id``. Three
    fields sit outside that identity and can still be described two ways: the
    URL the caller asked for is whatever spelling it held (``www.`` is
    normalized away, so ``https://www.example.com/page`` and
    ``https://example.com/page`` are one page with two spellings), the
    extractor can lay one body out in different chunks, and a reader that
    found no title falls back to the URL. The shared merge compares all three
    for one read id and refuses two answers — one identity carrying two bodies
    is the halt ``EvidenceIdentityConflict`` exists to raise — so the
    description the run recorded first stands, exactly as the first stored
    title does for the Researcher's own admissions, and a later admission
    contributes its selection, its target association, and what it observed
    rather than a second description of the read.
    """
    known = recorded.get(read.read_id)
    if known is None:
        return read
    return read.model_copy(
        update={
            "requested_url": known.requested_url,
            "passages": known.passages,
            "title": known.title,
        }
    )


def select_passages_with_lede(
    passages: Mapping[str, str],
    query: str,
    budget: int,
    *,
    lede: str,
) -> list[str]:
    """The relevance-selected passages, led by the read's own opening passage.

    Selection scores a page's passages by the query alone and admits them by
    a character budget (D1), not a fixed count: a page's chunks vary sharply
    in length, and a fixed count either starves a page of many short,
    on-topic chunks or wastes the whole allowance on a few long ones. A
    release's opening passage is its header — the title and lede the
    publisher wrote to say what the page is. It ranks with every other
    passage on the same budget, so it shares the bound like anything else
    when its own relevance earns it a place there; only when the ranking
    left it out is it added on top, past the bound when the bound was
    already full, because the opening passage is the one part of a page
    selection may not leave behind entirely.

    That guarantee holds only for a genuine header. The audited headphone
    run's reads routinely opened on the site's own navigation bar, not a
    headline, and the old unconditional rule forced that navigation into
    every packet regardless of relevance (D1). A lede :func:`is_link_dense`
    is never *forced to the front*; it still takes its normal position in
    the whole-page fill below, exactly like any other passage the ranking
    left unmatched -- ``is_link_dense`` decides only the front-of-packet
    guarantee, never whether the passage is admitted at all. Excluding it
    from admission entirely mislabelled a passage that scores nothing on a
    query it doesn't match as ``deferred_capacity`` (capacity was never the
    reason) and forced a continuation batch to admit it anyway.

    ``lede`` is the read's own first locator, not necessarily the first key of
    ``passages``: a caller selecting from a subset (a continuation batch) must
    not mistake the subset's first entry for the read's opening passage.

    Whole-page admission: once the ranked matches and the front-of-packet
    lede guarantee are settled, whatever the query matched nothing in still
    fills the budget that remains, in reader order -- the header keeps its
    place at the front only when a genuine header earned it, and every
    other passage neither rule placed at the front is ordered by where the
    page put it, a dense lede included.
    """
    ranked = select_passages_by_budget(passages, query, budget, admit_unmatched=False)
    lede_is_header = (
        lede in passages and lede not in ranked and not is_link_dense(passages[lede])
    )
    selected = [lede, *ranked] if lede_is_header else ranked
    used = sum(len(passages[locator]) for locator in selected)
    filled = set(selected)
    for locator, text in passages.items():
        if locator in filled:
            continue
        if not isinstance(locator, str) or not locator.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        text_len = len(text)
        if used + text_len > budget:
            continue
        selected.append(locator)
        used += text_len
    return selected


def _within_budget(
    order: Sequence[str], texts: Mapping[str, str], budget: int
) -> list[str]:
    """``order`` taken in sequence up to ``budget`` characters, at least one.

    A batch whose locators do not lexically match the query is still handed
    over, in reader order (D1): a selection miss is not "there is no
    evidence". The first locator is always kept, even when it alone is the
    whole budget, so the fallback never hands over nothing.
    """
    taken: list[str] = []
    used = 0
    for locator in order:
        text_len = len(texts[locator])
        if taken and used + text_len > budget:
            break
        taken.append(locator)
        used += text_len
    return taken


def admit_read_result(
    result: ToolResult,
    *,
    session_id: str,
    target_id: str | None = None,
    query: str = "source evidence",
    origin: OriginName = "researcher",
    admission_chars: int = 200_000,
    retrieved_at: str | None = None,
    sequence: int = 0,
    configuration_fingerprint: str = "acquisition-v1",
    recorded_reads: Mapping[str, ReadRecord] | None = None,
    include_lede: bool = True,
) -> ReadAdmission | None:
    """Admit a read and select exact target-bearing passages from its body.

    ``recorded_reads`` is the run's read registry. A body the run already
    holds is admitted under the description it was recorded with — the run's
    first description of a read id stands — so the selection, the evidence
    identities, and the deferred locators all describe the run's own copy of
    that body instead of a restatement of it. A caller that holds no registry
    passes nothing, which is the first admission of a read.

    ``include_lede`` guarantees the read's opening passage a place beside the
    relevance-selected ones (see :func:`select_passages_with_lede`). That is a
    rule for *extraction*, where a release's headline carries what the page
    is about. Verification passes ``False``: a claim's packet holds only the
    passages that bear on the claim, and an unrelated lede admitted there is
    noise that turns a free ``no_candidate`` outcome into a paid adjudication.
    """
    if admission_chars < 1:
        raise ValueError("admission_chars must be at least 1")
    read = build_read_record_from_tool_result(
        result,
        session_id=session_id,
        retrieved_at=retrieved_at,
        target_ids=() if target_id is None else (target_id,),
    )
    if read is None:
        return None
    if recorded_reads:
        read = _adopt_recorded_read(read, recorded_reads)
    target_ids = () if target_id is None else (target_id,)
    budget = admission_chars
    selected_locators = (
        select_passages_with_lede(
            read.passages,
            query,
            budget,
            lede=next(iter(read.passages)),
        )
        if include_lede
        else select_passages_by_budget(
            read.passages, query, budget, admit_unmatched=False
        )
    )
    evidence: dict[str, EvidenceUnit] = {}
    dispositions: list[EvidenceDisposition] = []
    for locator in selected_locators:
        unit = build_evidence_unit(
            read=read,
            locator=locator,
            excerpt=read.passages[locator],
            origin=origin,
            target_ids=target_ids,
        )
        evidence[unit.evidence_id] = unit
    selected_set = set(selected_locators)
    for locator in read.passages:
        if locator in selected_set:
            continue
        dispositions.append(
            EvidenceDisposition(
                item_id=f"{read.read_id}/{locator}",
                stage="read-selection",
                reason="deferred_capacity",
                target_ids=list(target_ids),
            )
        )
    packet_fingerprint = _fingerprint(
        read.read_id, query, selected_locators, sorted(evidence)
    )
    admission = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id=session_id,
        agent_name=origin,
        sequence=sequence,
        target_ids=target_ids,
        input_ids=(read.requested_url,),
        returned_ids=(read.read_id,),
        accepted_ids=(read.read_id,),
        disposition_ids=tuple(item.item_id for item in dispositions),
        packet_fingerprint=packet_fingerprint,
        configuration_fingerprint=configuration_fingerprint,
    )
    selection = build_boundary_audit(
        operation=PASSAGE_SELECTION_OPERATION,
        job_id=session_id,
        agent_name=origin,
        sequence=sequence,
        target_ids=target_ids,
        input_ids=tuple(read.passages),
        selected_ids=tuple(sorted(evidence)),
        returned_ids=tuple(sorted(evidence)),
        accepted_ids=tuple(sorted(evidence)),
        deferred_ids=tuple(item.item_id for item in dispositions),
        disposition_ids=tuple(item.item_id for item in dispositions),
        packet_fingerprint=packet_fingerprint,
        configuration_fingerprint=configuration_fingerprint,
        status="deferred" if dispositions else "completed",
    )
    return ReadAdmission(
        read=read,
        evidence=evidence,
        dispositions=tuple(dispositions),
        boundary_audits={
            admission.audit_id: admission,
            selection.audit_id: selection,
        },
    )


def _candidate_id(url: str) -> str:
    return "candidate-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _candidate_from_result(
    result: Mapping[str, object],
    *,
    target_id: str | None,
    reason: str,
    discovered_via: Literal["search", "memory", "document_link"],
) -> CandidateRecord | None:
    url = _text(result.get("url"))
    if not url:
        return None
    canonical = normalize_source_url(url)
    try:
        parts = urlsplit(canonical)
    except ValueError:
        return None
    if (
        not canonical
        or parts.scheme.casefold() not in {"http", "https"}
        or not parts.hostname
    ):
        return None
    target_ids = () if target_id is None else (target_id,)
    return CandidateRecord(
        candidate_id=_candidate_id(canonical),
        url=canonical,
        title=_text(result.get("title")),
        target_ids=target_ids,
        selection_reason=reason,
        discovered_via=discovered_via,
    )


def _query_from_input(tool_input: Mapping[str, object]) -> str:
    value = tool_input.get("query")
    return value if isinstance(value, str) else ""


def _url_from_input(tool_input: Mapping[str, object]) -> str:
    value = tool_input.get("url") or tool_input.get("source")
    return normalize_source_url(value) if isinstance(value, str) else ""


def _required_reader(url: str) -> str | None:
    """Route explicit document suffixes without blocking unknown URLs."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    suffix = path.casefold().rsplit(".", 1)[-1]
    if "." not in path:
        return None
    if suffix in {
        "pdf",
        "csv",
        "json",
        "txt",
        "md",
        "markdown",
        "doc",
        "docx",
        "xls",
        "xlsx",
    }:
        return "document_reader"
    if suffix in {"html", "htm", "xhtml"}:
        return "web_scraper"
    return None


def _url_stem(url: str) -> str:
    """The URL with its final path suffix removed, or ``""`` when unusable."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.hostname:
        return ""
    path = parts.path
    head, _separator, _tail = path.rpartition(".")
    if "/" in head:
        path = head
    return f"{parts.scheme.casefold()}://{parts.netloc.casefold()}{path.casefold()}"


def _synthesized_from_failed_page(url: str, state: AcquisitionState) -> bool:
    """True when ``url`` is a failed page with a different suffix swapped on.

    A model that could not read ``report.html`` habitually proposes
    ``report.pdf`` next. That is a guess about the publisher's file naming,
    not a discovered document, and it may not be attempted — not even when a
    discovery call just failed and the model is otherwise allowed to fall
    back to a URL it already had.
    """
    stem = _url_stem(url)
    if not stem:
        return False
    failed: list[str] = list(state.denied_urls)
    failed.extend(
        record.url
        for record in state.candidate_records.values()
        if record.status in {"denied", "unusable"}
    )
    for other in failed:
        if other != url and _url_stem(other) == stem:
            return True
    return False


def _url_was_discovered(url: str, state: AcquisitionState) -> bool:
    """True when the run was actually given this URL, not asked to guess it.

    A searched, remembered, or followed-document candidate stays readable after
    it leaves the queue, and a URL the run already attempted may be retried
    while its call budget lasts (a denial is checked separately). Anything else
    is the model's guess about the publisher's file layout, and guessing is how
    a denied landing page turns into a fabricated document URL. There is no
    publisher-wide circuit here: a *discovered* document on a denied host stays
    readable.
    """
    if url in state.candidate_urls or url in state.attempted_urls:
        return True
    record = state.candidate_records.get(url)
    return record is not None and record.discovered_via in _DISCOVERED_VIA


def cache_reuse_problem(
    record: ReadRecord,
    *,
    requested_url: str,
    cited_identity: tuple[str, str] | None,
) -> str | None:
    """Why a stored read may not stand in for one being requested, or ``None``.

    Three fingerprints have to agree before a cache entry is reused. **Identity**:
    the entry must be the artifact for the URL being requested, resolved either
    way, or it is a different page. **Content and version**: when this run has
    already cited that URL, the entry must be the very read the citation was
    made against — same read id, same content hash. A different read of the
    same URL is a changed body, and serving the new one under the old citation
    (or the old one after the page changed) publishes a citation to text nobody
    re-read; the reuse is refused and the URL is actually fetched.

    An uncited URL has no fingerprint to disagree with, so it stays a plain
    reuse: this rule is about citations surviving, not about disabling the
    cache. The caller resolves the cited identity from the run's own claim
    record — never from the cached entry itself, which would make the check
    vacuous — and ``validate_cached_read`` still runs behind this for the
    content hash, completeness, and passage checks it owns.
    """
    requested = _canonical_acquisition_url(requested_url)
    known = {
        _canonical_acquisition_url(record.requested_url),
        _canonical_acquisition_url(record.resolved_url),
    }
    if not requested or requested not in known:
        return "identity_mismatch"
    if cited_identity is None:
        return None
    cited_read_id, cited_hash = cited_identity
    if cited_read_id != record.read_id:
        return "content_version_changed"
    if cited_hash.strip().casefold() != record.content_sha256.strip().casefold():
        return "content_hash_changed"
    return None


def _cached_payload(record: ReadRecord, tool_name: str) -> dict[str, JsonValue]:
    body = "".join(record.passages.values())
    if tool_name == "web_scraper":
        return {
            "url": record.requested_url,
            "requested_url": record.requested_url,
            "resolved_url": record.resolved_url,
            "title": record.title,
            "text": body,
            "content_sha256": record.content_sha256,
            "extraction_complete": record.extraction_complete,
        }
    chunks: list[dict[str, JsonValue]] = []
    for index, (locator, text) in enumerate(record.passages.items()):
        page: int | None = None
        if locator.startswith("page-"):
            try:
                page = int(locator.split("-", 2)[1])
            except (IndexError, ValueError):
                page = None
        chunk: dict[str, JsonValue] = {"text": text, "chunk_index": index}
        if page is not None:
            chunk["page"] = page
        chunks.append(chunk)
    return {
        "source": record.requested_url,
        "requested_source": record.requested_url,
        "resolved_source": record.resolved_url,
        "title": record.title,
        "chunks": chunks,
        "content_sha256": record.content_sha256,
        "extraction_complete": record.extraction_complete,
    }


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    """The result of a pre-execution acquisition policy check."""

    allowed: bool = True
    reason: str = ""
    result: ToolResult | None = None
    charge_tool_budget: bool = True


@dataclass
class ManifestSequence:
    """The manifest sequence one shared audit mapping is written under.

    A boundary-audit id is fingerprinted from its job, its agent, its
    operation and its sequence, so a sequence that restarts is not a new name:
    it is a name some earlier writer already used, under a different manifest.
    One mapping can be written by several policies — the Researcher builds one
    per sub-topic and hands every one of them the run's single
    ``boundary_audits`` — and a counter each policy owns restarts at zero, so
    the second sub-topic's first manifest replaces the first sub-topic's, with
    nothing refusing the replacement: it happens locally, before any reducer
    reads the mapping. The counter therefore belongs to whoever owns the
    mapping, and every policy writing into it claims from that one counter.
    """

    value: int = 0

    def take(self) -> int:
        """Take the next sequence, advancing the mapping's counter."""
        claimed = self.value
        self.value += 1
        return claimed

    def seed_at_least(self, value: int) -> None:
        """Raise the counter to at least ``value``, never lower it."""
        self.value = max(self.value, value)


@dataclass
class AcquisitionPolicy:
    """Stateful policy and post-call reducer for one acquisition consumer."""

    state: AcquisitionState
    session_id: str
    target_id: str | None = None
    query: str = "source evidence"
    origin: OriginName = "researcher"
    reads: dict[str, ReadRecord] = field(default_factory=dict)
    evidence: dict[str, EvidenceUnit] = field(default_factory=dict)
    dispositions: list[EvidenceDisposition] = field(default_factory=list)
    findings: Sequence[Finding] = ()
    """The findings this run has already recorded, for the packet's steering.

    The decision packet lists them beside the evidence, so a later pass is not
    asked to mine a passage the run already holds a finding from. They are the
    run's record and the caller that has the state supplies them; the packet
    renders only those whose read is in the packet's own scope.
    """
    boundary_audits: dict[str, Any] = field(default_factory=dict)
    retrieved_at: Callable[[], str] = _utc_now_iso
    read_admission_chars: int = 200_000
    passage_batch_limit: int = _DEFAULT_PASSAGE_BATCH_LIMIT
    configuration_fingerprint: str = "acquisition-v1"
    cache: MutableMapping[str, ReadRecord] | None = None
    network_read_ids: set[str] | None = None
    on_read_admitted: Callable[[str], None] | None = None
    """S6: called with a read's id the moment it is admitted (cache or
    network, but only once per read), so a caller can start that page's own
    extraction call in the background while the ReAct loop keeps running.
    Never called for a failed/refused read attempt, and never twice for one
    read id -- a read the run already held (a cache reuse, or a body two
    candidate URLs both resolve to) is not admitted a second time."""

    audit_sequence: ManifestSequence | None = None
    """The manifest counter of the mapping this policy writes into.

    Supplied by the caller when several policies of one run share one
    ``boundary_audits`` mapping, as the Researcher's per-sub-topic policies
    do. A policy with none of its own gets one of its own, which is correct
    exactly when it is the only writer of its mapping.
    """
    _seen_queries: set[str] = field(default_factory=set, init=False)
    _last_search_failed: bool = field(default=False, init=False)
    _network_read_ids: set[str] = field(default_factory=set, init=False)
    _own_sequence: ManifestSequence = field(
        default_factory=ManifestSequence, init=False
    )
    _cache: dict[str, ReadRecord] = field(default_factory=dict, init=False)
    _document_fallback_urls: set[str] = field(default_factory=set, init=False)
    _read_titles: dict[str, str] = field(default_factory=dict, init=False)
    _packet_overflow_evidence_ids: set[str] = field(
        default_factory=set, init=False
    )
    _read_admitted_notified: set[str] = field(default_factory=set, init=False)
    _passage_batches: dict[str, int] = field(default_factory=dict, init=False)
    _validated_cache_reads: dict[str, ReadRecord] = field(
        default_factory=dict, init=False
    )
    """A cache hit's ``validate_cached_read`` result, stashed by the
    ``ToolPolicyDecision`` short-circuit under its read id and consumed by
    ``_read_observed`` -- so one cache hit costs one validation instead of
    the short-circuit and the reducer each validating it separately."""

    def __post_init__(self) -> None:
        if self.read_admission_chars < 1:
            raise ValueError("read_admission_chars must be at least 1")
        if self.passage_batch_limit < 1:
            raise ValueError("passage_batch_limit must be at least 1")
        if self.state.target_id is None and self.target_id is not None:
            self.state = self.state.model_copy(update={"target_id": self.target_id})
        if self.cache is not None:
            self._cache = self.cache  # share the run-level read registry
        if self.network_read_ids is not None:
            self._network_read_ids = self.network_read_ids
        # Only original network records may seed a cache. A stamped cache copy
        # cannot validate itself and must never become a second origin.
        for record in self.reads.values():
            if record.acquisition_kind == "network" and record.extraction_complete:
                self._cache.setdefault(record.resolved_url, record)
                self._cache.setdefault(record.requested_url, record)
        # Every read already admitted by this or an earlier consumer keeps the
        # title it was first stored under. Re-resolving it here is what keeps
        # two admissions of one body from disagreeing about its title.
        for record in (*self.reads.values(), *self._cache.values()):
            self._read_titles.setdefault(record.read_id, record.title)

    @property
    def acquired_work_count(self) -> int:
        """Count unique network bodies, excluding cache admissions."""
        return len(self._network_read_ids)

    def _next_sequence(self) -> int:
        """Take the next manifest sequence for the mapping this policy writes.

        The run's counter when one was supplied, this policy's own otherwise:
        the caller who shares a mapping is the caller who knows the counter
        has to be shared with it.
        """
        return (self.audit_sequence or self._own_sequence).take()

    def start_turn(self) -> None:
        if self.state.remaining_model_turns > 0:
            self.state = self.state.model_copy(
                update={
                    "remaining_model_turns": self.state.remaining_model_turns - 1
                }
            )

    def complete_extraction(
        self, *, except_read_ids: Collection[str] = ()
    ) -> None:
        """Terminate the local extraction handoff, except the given reads.

        ``pending_extraction_ids`` are the reads whose current batch was handed
        to the extractor. A passage batch still owed is taken now — bounded,
        at most once per read — and whatever the bound leaves over is recorded
        in that batch's selection manifest and dropped from the pending list,
        so persisted state can never re-enter "extract" for the rest of the
        run. Only the read IDs whose batch was handed over are consumed here,
        and they are consumed *because the extraction succeeded*: a handoff
        that produced no result is deferred instead (``defer_extraction``).

        ``except_read_ids`` (S6, RevSelectionR3 P1): a page whose own
        extraction call failed keeps its read id in
        ``pending_extraction_ids`` -- its passages were never actually
        mined, so the batch stays owed for that read alone, exactly like a
        whole-topic ``defer_extraction`` did before per-page calls existed,
        while every read whose own call succeeded is still consumed here.
        """
        self.extract_passage_batch()
        kept = set(except_read_ids)
        self.state = self.state.model_copy(
            update={
                "pending_extraction_ids": [
                    read_id
                    for read_id in self.state.pending_extraction_ids
                    if read_id in kept
                ],
            }
        )

    def defer_extraction(self) -> int:
        """Hand over the current batch, and keep it owed.

        Called when the extraction that was supposed to consume this batch
        failed retryably — a provider outage, or a reply that never validated.
        The passages were handed to an extractor that produced nothing, so the
        read ids stay in ``pending_extraction_ids``: the work is still owed,
        the next pass or a resumed run can see exactly which reads are
        outstanding, and no later stage may read the batch as consumed.
        """
        return self.extract_passage_batch()

    def record_extraction_dispositions(
        self,
        admitted: Sequence[tuple[str, str]],
        *,
        unmined_quantity_ids: Sequence[str] = (),
        unmined_target_ids: Sequence[str] = (),
        failed_read_ids: Collection[str] = (),
    ) -> None:
        """Account for exactly the selected units no accepted finding used.

        Unit-level, never URL-level: ``admitted`` is the ``(read_id, locator)``
        of every extracted finding that was admitted against the registry. A
        selected passage is "used" only when *that* passage produced a finding —
        matching on ``source_url`` marked a passage used because a different
        passage of the same read did, which is the approximation this replaces.
        Every other selected unit for the active target gets its explicit
        reason here, so no selected passage can disappear unaccounted for.

        ``unmined_quantity_ids`` names the units whose own text states a figure
        in a measure unit the active target asks for — the passages a bounded
        re-extraction was run over, and which it too left unmined. They are
        disposed of as :data:`UNMINED_QUANTITY_REASON`, never as
        ``irrelevant``: the extraction walked past the figure the target asks
        for, which is a different fact from the passage being beside the
        point, and it is the fact the ledger and the Critic have to read.

        ``unmined_target_ids`` is the same fact for the other bounded
        re-extraction: the units that state the words of a required target the
        pass answered nowhere yet and yielded no finding even after the pass
        asked about them. They keep their own reason for the same purpose (a
        unit in both lists keeps the figure's: a unit that carries the target's
        own unit carries the sharper fact). A unit the bounded packets never
        asked about is not named here: the reason says the extraction was asked
        again over that passage, and only a packet that carried it can say so.

        A unit ``build_acquisition_context`` itself packed out of the
        extraction call's own packet (``self._packet_overflow_evidence_ids``,
        RevSelectionR3 P2) is disposed of as ``deferred_capacity``, never
        ``irrelevant``: the extraction never saw it at all, so it cannot have
        judged it beside the point. Capacity, not relevance, is why it is
        unaccounted for.

        ``failed_read_ids`` (S6, RevSelectionR3 P1): every unit of a page
        whose own extraction call failed this pass is disposed of as
        :data:`EXTRACTION_FAILED_REASON`, checked before every other reason —
        a call that never returned cannot have walked past a figure or an
        obligation's own words, and it certainly never judged the passage
        beside the point. ``admitted``/``unmined_quantity_ids``/
        ``unmined_target_ids`` are this pass's own findings and owed-batch
        results, which a failed page's units are never part of, but the
        precedence check is explicit rather than assumed.
        """
        used = {(read_id, locator) for read_id, locator in admitted}
        unmined_quantities = set(unmined_quantity_ids)
        unmined_targets = set(unmined_target_ids)
        overflowed = self._packet_overflow_evidence_ids
        failed_reads = set(failed_read_ids)
        target_id = self.target_id
        known = {(item.stage, item.item_id) for item in self.dispositions}
        for evidence_id, unit in self.evidence.items():
            if target_id is not None and target_id not in unit.target_ids:
                continue
            if (unit.read_id, unit.locator) in used:
                continue
            if ("extraction", evidence_id) in known:
                continue
            if unit.read_id in failed_reads:
                reason = EXTRACTION_FAILED_REASON
            elif evidence_id in unmined_quantities:
                reason = UNMINED_QUANTITY_REASON
            elif evidence_id in unmined_targets:
                reason = UNMINED_TARGET_REASON
            elif evidence_id in overflowed:
                reason = "deferred_capacity"
            else:
                reason = "irrelevant"
            self.dispositions.append(
                EvidenceDisposition(
                    item_id=evidence_id,
                    stage="extraction",
                    reason=reason,
                    target_ids=list(() if target_id is None else (target_id,)),
                )
            )

    def _pending_groups(self) -> tuple[dict[str, list[str]], list[str]]:
        """Split pending locator ids into resolvable reads and orphaned ids."""
        groups: dict[str, list[str]] = {}
        unresolved: list[str] = []
        for item in self.state.pending_passage_ids:
            read_id, _separator, locator = item.partition("/")
            read = self.reads.get(read_id)
            if read is None or locator not in read.passages:
                unresolved.append(item)
                continue
            locators = groups.setdefault(read_id, [])
            if locator not in locators:
                locators.append(locator)
        return groups, unresolved

    def extract_passage_batch(self) -> int:
        """Hand over the second bounded passage batch, then terminate.

        The plan's decision order reads a non-empty ``pending_passage_ids`` as
        "extract". A pending list that only ever grew froze the target out of
        acquisition for the rest of the pass: every later tool call is
        correctly rejected, and no local step ever consumed the ids. The
        handoff is therefore bounded — ``passage_batch_limit`` batches per
        read, the second being the plan's continuation batch — and
        terminating: whatever the bound leaves over is recorded in this
        batch's selection manifest and dropped from the pending list.

        A batch whose locators do not lexically match the query is still handed
        over, in reader order: a selection miss is not "there is no evidence".
        Returns the number of new evidence units admitted.
        """
        groups, unresolved = self._pending_groups()
        if not groups:
            if unresolved:
                # Nothing resolvable is left, so the gate closes rather than
                # naming locators no stored read can supply.
                self.state = self.state.model_copy(
                    update={"pending_passage_ids": []}
                )
            return 0
        target_ids = () if self.target_id is None else (self.target_id,)
        selected_ids: list[str] = []
        terminal: list[str] = [*unresolved]
        carried: list[str] = []
        handed_over: list[str] = []
        admitted = 0
        for read_id, locators in groups.items():
            read = self.reads[read_id]
            taken = self._passage_batches.get(read_id, 1)
            if taken >= self.passage_batch_limit:
                terminal.extend(
                    f"{read_id}/{locator}" for locator in locators
                )
                continue
            handed_over.append(read_id)
            texts = {locator: read.passages[locator] for locator in locators}
            budget = self.read_admission_chars
            selected = select_passages_with_lede(
                texts,
                self.query,
                budget,
                lede=next(iter(read.passages)),
            )
            if not selected:
                selected = _within_budget(locators, read.passages, budget)
            self._passage_batches[read_id] = taken + 1
            units: dict[str, EvidenceUnit] = {}
            for locator in selected:
                unit = build_evidence_unit(
                    read=read,
                    locator=locator,
                    excerpt=read.passages[locator],
                    origin=self.origin,
                    target_ids=target_ids,
                )
                units[unit.evidence_id] = unit
            self._assign_evidence(units)
            admitted += len(units)
            selected_ids.extend(sorted(units))
            chosen = set(selected)
            leftovers = [
                f"{read_id}/{locator}"
                for locator in locators
                if locator not in chosen
            ]
            if self._passage_batches[read_id] >= self.passage_batch_limit:
                terminal.extend(leftovers)
            else:
                carried.extend(leftovers)
        audit = build_boundary_audit(
            operation=PASSAGE_SELECTION_OPERATION,
            job_id=self.session_id,
            agent_name=self.origin,
            sequence=self._next_sequence(),
            target_ids=target_ids,
            input_ids=tuple(self.state.pending_passage_ids),
            selected_ids=tuple(selected_ids),
            returned_ids=tuple(selected_ids),
            accepted_ids=tuple(selected_ids),
            deferred_ids=tuple(terminal),
            disposition_ids=tuple(terminal),
            packet_fingerprint=_fingerprint(
                "continuation-batch", self.query, selected_ids, terminal
            ),
            configuration_fingerprint=self.configuration_fingerprint,
            status="deferred" if terminal else "completed",
        )
        self.boundary_audits[audit.audit_id] = audit
        # The reads whose batch was handed over are recorded as owed until the
        # extraction that consumes them succeeds. Clearing the list here (as
        # this used to do by never filling it) meant a failed extraction became
        # indistinguishable from a completed one.
        self.state = self.state.model_copy(
            update={
                "pending_passage_ids": list(dict.fromkeys(carried)),
                "pending_extraction_ids": list(
                    dict.fromkeys(
                        [*self.state.pending_extraction_ids, *handed_over]
                    )
                ),
            }
        )
        return admitted

    def _resolve_read_title(self, read: ReadRecord, requested: str) -> ReadRecord:
        """Resolve one read's title once per read id; the first value wins.

        A search snippet title is a *lead* label, and the reader's own title for
        the same body can differ between two admissions of the same URL. Both
        admissions mint the same evidence identity ``(read_id, locator,
        excerpt)``, and the shared merge refuses two different ``source_title``
        values for one identity — so a later admission re-labelling an
        already-cited passage raised. The first stored title therefore stands
        for every later admission of the same ``read_id``.
        """
        known = self._read_titles.get(read.read_id)
        if known is not None:
            return read.model_copy(update={"title": known})
        candidate = self.state.candidate_records.get(
            normalize_source_url(requested)
        )
        title = read.title
        if (
            candidate is not None
            and candidate.title
            and read.title == read.resolved_url
        ):
            title = candidate.title
        self._read_titles[read.read_id] = title
        return read.model_copy(update={"title": title})

    def _assign_evidence(self, units: Mapping[str, EvidenceUnit]) -> None:
        """Add units to the shared registry, unioning target associations.

        One passage has one evidence identity per ``(read_id, locator,
        excerpt)`` and the registry is shared across targets, so a plain
        assignment lets the last target to select a passage erase the earlier
        target's association. The shared merge keeps the first origin, unions
        targets, and still refuses a genuine rewrite. Mutation stays in place:
        sibling policies hold this same mapping.
        """
        for evidence_id, unit in units.items():
            existing = self.evidence.get(evidence_id)
            if existing is None:
                self.evidence[evidence_id] = unit
                continue
            self.evidence[evidence_id] = merge_evidence_units(
                {evidence_id: existing}, {evidence_id: unit}
            )[evidence_id]

    def _expected_for_tool(self, tool_name: str) -> AcquisitionAction:
        if tool_name in {"web_search", "query_memory"}:
            return "search"
        if tool_name in {"web_scraper", "document_reader"}:
            return "read"
        return "finish"

    def before_action(
        self,
        decision: ReActDecision,
        tool_input: Mapping[str, object] | None = None,
        *_args: object,
    ) -> ToolPolicyDecision:
        """Validate one requested action immediately before execution."""
        if decision.action != "use_tool":
            return ToolPolicyDecision()
        tool_name = decision.tool_name or ""
        expected = next_acquisition_action(self.state)
        requested_kind = self._expected_for_tool(tool_name)
        if requested_kind == "finish":
            return ToolPolicyDecision(
                allowed=False,
                reason="the requested tool is outside the acquisition boundary",
            )
        if expected == "extract":
            # The local extract step is bounded and terminating, so it runs
            # here instead of only rejecting: the continuation batch is handed
            # over now (no tool call, no budget charge) and the pending list is
            # drained when the batch bound is reached. A refusal that never
            # consumed anything froze the target out of acquisition for the
            # rest of the pass.
            self.extract_passage_batch()
            expected = next_acquisition_action(self.state)
            if expected == "extract":
                return ToolPolicyDecision(
                    allowed=False,
                    reason=(
                        "acquisition policy requires local extract before "
                        "another tool"
                    ),
                )
        # Let the loop's own budget gate produce the canonical exhausted
        # observation when this persisted state is paired with a loop that
        # has already spent its configured external budget.  The normal
        # positive-capacity finish path remains a policy rejection.
        if expected == "finish" and self.state.remaining_calls > 0:
            return ToolPolicyDecision(
                allowed=False,
                reason="acquisition policy has no candidate work remaining",
            )
        if expected == "finish" and self.state.remaining_calls <= 0:
            # The loop's gate above holds only inside the pass that spent the
            # budget. On a refinement pass the Researcher opens a fresh loop
            # for the same unanswered target, and that loop's budget is full,
            # so nothing else refuses these calls: a spent target would search
            # and read freely while its ``remaining_calls`` stayed at zero.
            # The budget is the run's — ``merge_acquisition_states`` keeps the
            # minimum so spent capacity is never resurrected — so the policy
            # says so here instead of deferring to a gate that cannot fire.
            return ToolPolicyDecision(
                allowed=False,
                reason=(
                    f"the acquisition budget of {self.state.target_id!r} is "
                    "spent; no further tool calls are possible"
                ),
            )
        fallback_read_after_failed_search = (
            requested_kind == "read"
            and expected == "search"
            and self._last_search_failed
        )
        # The accepted set, not only the preferred action: a search is
        # admissible beside a queued read while the queue's own discipline is
        # not binding (at most two searches in a row, never the last call). A
        # target with several named sources, or a first search that returned
        # poor candidates, otherwise spends every turn on forced reads and can
        # never search again. ``allowed_acquisition_actions`` is the same
        # function the decision packet renders, so the model is told exactly
        # what this gate accepts.
        if (
            requested_kind not in allowed_acquisition_actions(self.state)
            and not fallback_read_after_failed_search
        ):
            return ToolPolicyDecision(
                allowed=False,
                reason=(
                    f"acquisition policy requires {expected} before {requested_kind}"
                ),
            )
        values = tool_input or {}
        if requested_kind == "search":
            query = _query_from_input(values)
            folded = " ".join(query.split()).casefold()
            if folded and folded in self._seen_queries:
                return ToolPolicyDecision(
                    allowed=False,
                    reason="the same discovery query was already attempted",
                )
            return ToolPolicyDecision()

        url = _url_from_input(values)
        required_reader = _required_reader(url)
        if (
            required_reader is not None
            and tool_name != required_reader
            and not (
                tool_name == "document_reader"
                and url in self._document_fallback_urls
            )
        ):
            return ToolPolicyDecision(
                allowed=False,
                reason=f"this URL must be read with {required_reader}",
            )
        if url in self.state.denied_urls:
            return ToolPolicyDecision(
                allowed=False,
                reason="this exact URL was denied; acquire a different candidate",
            )
        cached = self._cache.get(url)
        if cached is not None and cache_reuse_problem(
            cached,
            requested_url=url,
            cited_identity=None,
        ) is None:
            validated = validate_cached_read(
                cached,
                "".join(cached.passages.values()),
                expected_content_sha256=cached.content_sha256,
                version_eligible=True,
                validated_at=self.retrieved_at(),
            )
            if validated is not None:
                self._validated_cache_reads[validated.read_id] = validated
                result = ToolResult(
                    tool_name=tool_name,
                    success=True,
                    data=_cached_payload(validated, tool_name),
                    latency_ms=0.0,
                    metadata={
                        "acquisition_kind": "cache",
                        "cached_read_id": validated.read_id,
                    },
                )
                return ToolPolicyDecision(
                    result=result,
                    charge_tool_budget=False,
                )
        if not _url_was_discovered(url, self.state):
            if _synthesized_from_failed_page(url, self.state):
                return ToolPolicyDecision(
                    allowed=False,
                    reason=(
                        "the URL was synthesized by swapping a suffix onto a "
                        "denied or failed page; only a discovered document "
                        "may be read"
                    ),
                )
            if not self._last_search_failed:
                return ToolPolicyDecision(
                    allowed=False,
                    reason=(
                        "the URL was not discovered by a search result, a "
                        "memory lead, or a document link; a synthesized or "
                        "guessed URL may not be read"
                    ),
                )
        return ToolPolicyDecision()

    def __call__(
        self,
        decision: ReActDecision,
        tool_input: Mapping[str, object] | None = None,
        *_args: object,
    ) -> ToolPolicyDecision:
        return self.before_action(decision, tool_input, *_args)

    def _queue_candidate(self, candidate: CandidateRecord) -> None:
        url = candidate.url
        existing = self.state.candidate_records.get(url)
        records = dict(self.state.candidate_records)
        if existing is not None:
            candidate = existing.model_copy(
                update={
                    "title": existing.title or candidate.title,
                    "target_ids": list(
                        dict.fromkeys([*existing.target_ids, *candidate.target_ids])
                    ),
                    "selection_reason": existing.selection_reason,
                    "status": existing.status,
                    "read_id": existing.read_id,
                }
            )
        records[url] = candidate
        queue = list(self.state.candidate_urls)
        if candidate.status == "queued" and url not in queue:
            queue.append(url)
        self.state = self.state.model_copy(
            update={"candidate_urls": queue, "candidate_records": records}
        )

    def _search_observed(
        self,
        result: ToolResult,
        tool_input: Mapping[str, object],
    ) -> None:
        query = _query_from_input(tool_input)
        folded = " ".join(query.split()).casefold()
        if folded:
            self._seen_queries.add(folded)
        records: list[Mapping[str, object]] = []
        data = _as_mapping(result.data)
        if (
            result.success
            and data is not None
            and isinstance(data.get("results"), list)
        ):
            records = [
                item for item in data["results"] if isinstance(item, Mapping)
            ]
        reason = "candidate returned by discovery for the active target"
        for item in records:
            candidate = _candidate_from_result(
                item,
                target_id=self.target_id,
                reason=reason,
                discovered_via="search",
            )
            if candidate is not None:
                self._queue_candidate(candidate)
        self.state = self.state.model_copy(
            update={
                "consecutive_searches": self.state.consecutive_searches + 1,
                "empty_searches": (
                    0
                    if records and result.success
                    else self.state.empty_searches + 1
                ),
            }
        )
        self._last_search_failed = not result.success

    def _memory_observed(self, result: ToolResult) -> None:
        if not result.success:
            self.state = self.state.model_copy(
                update={
                    "consecutive_searches": self.state.consecutive_searches + 1,
                    "empty_searches": self.state.empty_searches + 1,
                }
            )
            return
        data = _as_mapping(result.data)
        matches = data.get("matches") if data is not None else None
        if not isinstance(matches, list):
            matches = []
        for item in matches:
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            url = _text(item.get("source_url")) or _text(
                metadata_map.get("source_url")
            )
            candidate = _candidate_from_result(
                {"url": url, "title": _text(item.get("title"))},
                target_id=self.target_id,
                reason="memory lead requiring original-source read admission",
                discovered_via="memory",
            )
            if candidate is not None:
                self._queue_candidate(candidate)
        self.state = self.state.model_copy(
            update={"consecutive_searches": self.state.consecutive_searches + 1}
        )

    def _mark_candidate(
        self,
        url: str,
        *,
        status: Literal["read", "denied", "unusable"],
        read_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        url = normalize_source_url(url)
        records = dict(self.state.candidate_records)
        existing = records.get(url)
        if existing is None and url:
            # A model may refine a search lead to the publisher's canonical
            # document path (for example, a landing page to its PDF).  Keep
            # that refinement in the same candidate manifest so its terminal
            # disposition is not hidden in an attempted-url side channel.
            existing = CandidateRecord(
                candidate_id=_candidate_id(url),
                url=url,
                target_ids=(
                    [] if self.target_id is None else [self.target_id]
                ),
                selection_reason="same-publisher path refinement",
                discovered_via="document_link",
                status=status,
                read_id=read_id,
                denial_reason=reason,
            )
            records[url] = existing
        elif existing is not None:
            records[url] = existing.model_copy(
                update={
                    "status": status,
                    "read_id": read_id or existing.read_id,
                    # I2: a candidate's denial reason tracks its current
                    # status -- ``None`` once it is read, so a URL denied on
                    # one attempt and read on a later one never keeps a stale
                    # refusal beside a successful read.
                    "denial_reason": reason,
                }
            )
        queue = [item for item in self.state.candidate_urls if item != url]
        denied = list(self.state.denied_urls)
        if status == "denied" and url not in denied:
            denied.append(url)
        self.state = self.state.model_copy(
            update={
                "candidate_urls": queue,
                "candidate_records": records,
                "denied_urls": denied,
            }
        )

    def _attempt_item_id(self, requested: str, tool_name: str) -> str:
        """The disposition identity of one failed read attempt.

        An attempt is a URL *plus the reader that made it*. When
        ``web_scraper`` refuses a page as ``unsupported_content_type`` the
        policy sends ``document_reader`` at the same URL by design, and those
        two failures are two facts about one URL rather than one fact
        restated. Keying both to the URL alone made the second a contradiction
        for an item that already had a reason — ``merge_evidence_dispositions``
        refuses those by design, and the node turns that ``ValueError`` into
        ``graph_invalid_agent_state``, ending the run with nothing published.

        The trailing number covers the repetition left over: a discovered URL
        may be retried while the call budget lasts, and a retry can fail
        differently (a transport failure this pass, a refusal the next). The
        count is read off the shared, run-level disposition list the caller
        passes in — the Researcher's policies all write into one — so an
        attempt number is stable across the passes of a run instead of
        restarting with each policy.
        """
        base = f"{requested or 'read-attempt'}#{tool_name}"
        earlier = sum(
            1
            for item in self.dispositions
            if item.stage == "read-selection"
            and item.item_id.startswith(f"{base}#")
        )
        return f"{base}#{earlier + 1}"

    def _notify_read_admitted(self, read_id: str) -> None:
        """S6: tell ``on_read_admitted`` about ``read_id``, once only.

        A read the run already held under this policy -- a cache reuse of a
        body an earlier admission already notified about, or two candidate
        URLs resolving to the same body -- must not start a second
        background extraction call for the page it already scheduled one
        for.
        """
        if self.on_read_admitted is None or read_id in self._read_admitted_notified:
            return
        self._read_admitted_notified.add(read_id)
        self.on_read_admitted(read_id)

    def _read_observed(
        self,
        result: ToolResult,
        tool_input: Mapping[str, object],
    ) -> None:
        requested = _url_from_input(tool_input)
        attempted = list(self.state.attempted_urls)
        if requested and requested not in attempted:
            attempted.append(requested)
        cached_read_id = result.metadata.get("cached_read_id")
        is_cache = result.metadata.get("acquisition_kind") == "cache"
        self.state = self.state.model_copy(
            update={
                "attempted_urls": attempted,
                "remaining_calls": max(
                    self.state.remaining_calls - (0 if is_cache else 1),
                    0,
                ),
                "consecutive_searches": 0,
            }
        )
        self._last_search_failed = False
        if is_cache and isinstance(cached_read_id, str):
            validated = self._validated_cache_reads.pop(cached_read_id, None)
            if validated is None:
                original = self._cache.get(requested) or self.reads.get(
                    cached_read_id
                )
                if original is not None:
                    validated = validate_cached_read(
                        original,
                        "".join(original.passages.values()),
                        expected_content_sha256=original.content_sha256,
                        version_eligible=True,
                        validated_at=self.retrieved_at(),
                    )
            if validated is not None:
                # The import is what this session holds: ``cache`` kind, the
                # session that read the bytes, and the moment this one
                # validated them. A body this registry already holds is left
                # alone — a body this run read itself, or one an earlier
                # sub-topic of this run already imported, stays the record it
                # was filed as rather than being re-stamped by each reuse.
                self.reads.setdefault(validated.read_id, validated)
                validated = self._resolve_read_title(validated, requested)
                selected = select_passages_with_lede(
                    validated.passages,
                    self.query,
                    self.read_admission_chars,
                    lede=next(iter(validated.passages)),
                )
                target_ids = (
                    () if self.target_id is None else (self.target_id,)
                )
                units: dict[str, EvidenceUnit] = {}
                for locator in selected:
                    unit = build_evidence_unit(
                        read=validated,
                        locator=locator,
                        excerpt=validated.passages[locator],
                        origin=self.origin,
                        target_ids=target_ids,
                    )
                    units[unit.evidence_id] = unit
                self._assign_evidence(units)
                omitted = [
                    locator
                    for locator in validated.passages
                    if locator not in set(selected)
                ]
                self._record_deferred_passages(
                    validated, omitted, target_ids
                )
                self._record_selection_audits(
                    validated,
                    selected,
                    omitted,
                    target_ids,
                )
                admission_audit = build_boundary_audit(
                    operation=READ_ADMISSION_OPERATION,
                    job_id=self.session_id,
                    agent_name=self.origin,
                    sequence=self._next_sequence(),
                    target_ids=target_ids,
                    input_ids=(validated.requested_url,),
                    returned_ids=(validated.read_id,),
                    accepted_ids=(validated.read_id,),
                    packet_fingerprint=_fingerprint(
                        validated.read_id, "cache", validated.content_sha256
                    ),
                    configuration_fingerprint=self.configuration_fingerprint,
                )
                self.boundary_audits[admission_audit.audit_id] = (
                    admission_audit
                )
                read_urls = list(self.state.read_urls)
                if validated.resolved_url not in read_urls:
                    read_urls.append(validated.resolved_url)
                self.state = self.state.model_copy(
                    update={"read_urls": read_urls}
                )
                self._mark_candidate(
                    requested, status="read", read_id=validated.read_id
                )
                self._notify_read_admitted(validated.read_id)
                return
        admission = admit_read_result(
            result,
            session_id=self.session_id,
            target_id=self.target_id,
            query=self.query,
            origin=self.origin,
            admission_chars=self.read_admission_chars,
            retrieved_at=self.retrieved_at(),
            sequence=self._next_sequence(),
            configuration_fingerprint=self.configuration_fingerprint,
            recorded_reads=self.reads,
        )
        if admission is not None:
            read = self._resolve_read_title(admission.read, requested)
            if read.title != admission.read.title:
                # Rebuild the units so the preserved title crosses the same
                # admission boundary as the body, without changing identity.
                admission = ReadAdmission(
                    read=read,
                    evidence={
                        unit.evidence_id: unit.model_copy(
                            update={"source_title": read.title}
                        )
                        for unit in admission.evidence.values()
                    },
                    dispositions=admission.dispositions,
                    boundary_audits=admission.boundary_audits,
                )
            prior = self._cache.get(read.resolved_url) or self._cache.get(
                read.requested_url
            )
            self.reads[read.read_id] = read
            self._assign_evidence(admission.evidence)
            self.dispositions.extend(admission.dispositions)
            if (
                prior is not None
                and prior.read_id != read.read_id
                and prior.content_sha256 != read.content_sha256
            ):
                known = {
                    (item.stage, item.item_id) for item in self.dispositions
                }
                target_ids = (
                    () if self.target_id is None else (self.target_id,)
                )
                for unit in self.evidence.values():
                    if unit.read_id != prior.read_id:
                        continue
                    if self.target_id is not None and (
                        self.target_id not in unit.target_ids
                    ):
                        continue
                    if ("read-selection", unit.evidence_id) in known:
                        continue
                    self.dispositions.append(
                        EvidenceDisposition(
                            item_id=unit.evidence_id,
                            stage="read-selection",
                            reason="stale_for_target",
                            target_ids=list(target_ids),
                        )
                    )
            self.boundary_audits.update(admission.boundary_audits)
            if read.extraction_complete:
                # A newly admitted body at the same URL is a new content
                # version. Replace the URL index so stale evidence cannot be
                # served forever after a legitimate revalidation.
                self._cache[read.resolved_url] = read
                self._cache[read.requested_url] = read
            self._network_read_ids.add(read.read_id)
            target_ids = () if self.target_id is None else (self.target_id,)
            omitted = [
                locator
                for locator in read.passages
                if f"{read.read_id}/{locator}"
                in {item.item_id for item in admission.dispositions}
            ]
            self._record_deferred_passages(read, omitted, target_ids)
            read_urls = list(self.state.read_urls)
            if read.resolved_url not in read_urls:
                read_urls.append(read.resolved_url)
            self.state = self.state.model_copy(update={"read_urls": read_urls})
            self._mark_candidate(requested, status="read", read_id=read.read_id)
            self._notify_read_admitted(read.read_id)
            return
        error = result.error
        if (
            error is not None
            and error.type == "unsupported_content_type"
            and result.tool_name == "web_scraper"
        ):
            # The requested URL may look like HTML while the final response
            # is a PDF/data document. Let document_reader follow that exact
            # URL once; do not broaden this to access denials.
            if requested:
                self._document_fallback_urls.add(requested)
        limitation = (
            _content_limitation(
                _text((_as_mapping(result.data) or {}).get("text")),
                _text((_as_mapping(result.data) or {}).get("title")),
            )
            if result.success
            else None
        )
        if limitation is not None:
            reason = limitation
        elif error is not None:
            extraction_failure_types = {
                "empty_document_content",
                "document_extraction_failed",
                "empty_page_content",
                "client_rendered_page",
                "unsupported_content_type",
                "unsupported_document_format",
            }
            if error.type in extraction_failure_types:
                reason = error.type
            elif error.type == "HTTPStatusError":
                # web_scraper reports every exhausted HTTP status failure
                # under this one exception name; the status code, not the
                # exception type, says whether the page was refused, missing,
                # or failed some other way (RevDatesR3 P2) -- collapsing all
                # three into "access_denied" described a missing page as a
                # refusal.
                status_code = error.details.get("status_code")
                if status_code in {401, 402, 403, 451}:
                    reason = "access_denied"
                elif status_code in {404, 410}:
                    reason = "not_found"
                else:
                    reason = "http_error"
            elif error.type in {"robots_disallowed", "access_denied"}:
                reason = "access_denied"
            else:
                reason = "transport_failure"
        else:
            # A successful result that could not satisfy the read contract is
            # still a visible attempted read; no body may disappear silently.
            reason = "malformed"
        self.dispositions.append(
            EvidenceDisposition(
                item_id=self._attempt_item_id(requested, result.tool_name),
                stage="read-selection",
                reason=reason,
                target_ids=list(
                    () if self.target_id is None else (self.target_id,)
                ),
            )
        )
        denied = bool(
            error is not None
            and (
                error.type in {"robots_disallowed", "access_denied", "HTTPStatusError"}
                or error.details.get("status_code") in {401, 402, 403, 451}
            )
        )
        status: Literal["denied", "unusable"] = "denied" if denied else "unusable"
        self._mark_candidate(requested, status=status, reason=reason)

    def _record_deferred_passages(
        self,
        read: ReadRecord,
        omitted: Sequence[str],
        target_ids: Sequence[str],
    ) -> None:
        """Keep omitted locators pending for a later bounded extraction pass."""
        if not omitted:
            return
        pending = list(self.state.pending_passage_ids)
        known_dispositions = {
            (item.stage, item.item_id) for item in self.dispositions
        }
        for locator in omitted:
            item_id = f"{read.read_id}/{locator}"
            if item_id not in pending:
                pending.append(item_id)
            if ("read-selection", item_id) not in known_dispositions:
                self.dispositions.append(
                    EvidenceDisposition(
                        item_id=item_id,
                        stage="read-selection",
                        reason="deferred_capacity",
                        target_ids=list(target_ids),
                    )
                )
        extraction = list(self.state.pending_extraction_ids)
        if read.read_id not in extraction:
            extraction.append(read.read_id)
        self.state = self.state.model_copy(
            update={
                "pending_passage_ids": pending,
                "pending_extraction_ids": extraction,
            }
        )

    def _record_selection_audits(
        self,
        read: ReadRecord,
        selected: Sequence[str],
        omitted: Sequence[str],
        target_ids: Sequence[str],
    ) -> None:
        """Record the cache path's same selection boundary as network reads."""
        packet_fingerprint = _fingerprint(
            read.read_id, self.query, list(selected), list(omitted)
        )
        audit = build_boundary_audit(
            operation=PASSAGE_SELECTION_OPERATION,
            job_id=self.session_id,
            agent_name=self.origin,
            sequence=self._next_sequence(),
            target_ids=target_ids,
            input_ids=tuple(read.passages),
            selected_ids=tuple(
                self.evidence_id_for(read.read_id, locator)
                for locator in selected
            ),
            returned_ids=tuple(
                self.evidence_id_for(read.read_id, locator)
                for locator in selected
            ),
            accepted_ids=tuple(
                self.evidence_id_for(read.read_id, locator)
                for locator in selected
            ),
            deferred_ids=tuple(f"{read.read_id}/{locator}" for locator in omitted),
            disposition_ids=tuple(f"{read.read_id}/{locator}" for locator in omitted),
            packet_fingerprint=packet_fingerprint,
            configuration_fingerprint=self.configuration_fingerprint,
            status="deferred" if omitted else "completed",
        )
        self.boundary_audits[audit.audit_id] = audit

    def evidence_id_for(self, read_id: str, locator: str) -> str:
        """Resolve a selected unit ID without trusting a provider identifier."""
        for evidence_id, unit in self.evidence.items():
            if unit.read_id == read_id and unit.locator == locator:
                return evidence_id
        return f"{read_id}/{locator}"

    def after_action(
        self,
        step: ReActStep,
        tool_input: Mapping[str, object] | None = None,
    ) -> None:
        """Apply one result immediately, including cache/read manifests."""
        result = step.tool_result
        if result is None:
            return
        parsed = tool_input or step.tool_input
        if step.tool_name == "web_search":
            self.state = self.state.model_copy(
                update={
                    "remaining_calls": max(self.state.remaining_calls - 1, 0)
                }
            )
            self._search_observed(result, parsed)
        elif step.tool_name == "query_memory":
            self.state = self.state.model_copy(
                update={
                    "remaining_calls": max(self.state.remaining_calls - 1, 0)
                }
            )
            self._memory_observed(result)
        elif step.tool_name in {"web_scraper", "document_reader"}:
            self._read_observed(result, parsed)

    def context(
        self,
        *,
        limit: int,
        for_decision: bool = False,
        read_ids: Sequence[str] | None = None,
    ) -> str:
        omitted: list[str] = []
        text = build_acquisition_context(
            self.state,
            self.reads,
            self.evidence,
            limit=limit,
            target_id=self.target_id,
            dispositions=self.dispositions,
            findings=self.findings,
            query=self.query,
            for_decision=for_decision,
            omitted_evidence_ids=omitted,
            read_ids=read_ids,
        )
        if for_decision:
            # A decision turn's own packet never determines extraction
            # overflow at all (RevSelectionR3 P3): every decision turn calls
            # this with ``read_ids=None``, and treating that the same as the
            # legacy whole-sub-topic extraction packet's own ``read_ids is
            # None`` wiped whatever per-page overflow an earlier page
            # extraction call in this same policy's lifetime had already
            # recorded. Left untouched here, whatever the last extraction
            # call recorded stands until the next one.
            pass
        elif read_ids is None:
            # The single whole-sub-topic packet (the pre-S6 one-call
            # extraction path): this call's own overflow is the whole story,
            # so it replaces whatever an earlier call in this same policy's
            # lifetime recorded.
            self._packet_overflow_evidence_ids = set(omitted)
        else:
            # S6: one page's own extraction packet. Several pages of one
            # sub-topic build their own packet independently, so each call's
            # overflow is folded in rather than erasing an earlier page's --
            # a single slot here would report only the last page extracted.
            self._packet_overflow_evidence_ids |= set(omitted)
        return text


def _recorded_statement(finding: Finding) -> str:
    """One finding's own statement, clamped for the packet's steering row.

    The snippet is the page's verbatim sentence and the content is the model's
    restatement: the snippet is what tells a later pass "this exact sentence is
    already mined", so it is preferred, and the content stands in for a record
    that carries none. Bounded because the packet spends characters on it and
    the row exists to be recognized, not to be read closely.
    """
    statement = finding.snippet or finding.content
    return summarize_text(statement, limit=_RECORDED_STATEMENT_CHARS)


def _render_candidate(record: CandidateRecord) -> str:
    targets = ",".join(record.target_ids) or "-"
    title = record.title or "(untitled)"
    return (
        f"candidate_id={record.candidate_id} url={record.url} title={title} "
        f"targets={targets} reason={record.selection_reason} "
        f"via={record.discovered_via} status={record.status} "
        f"read_id={record.read_id or '-'}"
    )


def _render_read(record: ReadRecord) -> str:
    targets = ",".join(record.target_ids) or "-"
    return (
        f"read_id={record.read_id} requested_url={record.requested_url} "
        f"resolved_url={record.resolved_url} title={record.title} "
        f"reader={record.reader} extraction_complete={record.extraction_complete} "
        f"content_sha256={record.content_sha256} targets={targets}"
    )


def _ranked_locators(passages: Mapping[str, str], query: str) -> list[str]:
    """Every locator of ``passages``, ranked against ``query``, all included.

    :func:`select_relevant_passages` drops a locator that shares no term with
    the query; a packet dump must still show it (D2 renders every passage of
    a selected read), so the passages the query ranked are followed by
    whatever it left out, in their own original order.
    """
    ranked = select_relevant_passages(passages, query, len(passages))
    rest = [locator for locator in passages if locator not in ranked]
    return [*ranked, *rest]


def build_acquisition_context(
    state: AcquisitionState,
    reads: Mapping[str, ReadRecord],
    evidence: Mapping[str, EvidenceUnit],
    *,
    limit: int,
    target_id: str | None = None,
    dispositions: Sequence[EvidenceDisposition] = (),
    focus_ids: Sequence[str] = (),
    findings: Sequence[Finding] = (),
    query: str | None = None,
    for_decision: bool = False,
    omitted_evidence_ids: list[str] | None = None,
    read_ids: Sequence[str] | None = None,
) -> str:
    """Render complete acquisition records, with explicit continuation IDs.

    The returned body carries no section heading of its own: the decision
    prompt's renderer adds ``## Acquisition context`` exactly once, so the
    packet never spends budget on a duplicated header.

    A record is atomic for packet purposes: if it does not fit, its complete
    text is omitted and its ID is listed for continuation. No serialized
    search/PDF payload is sliced into a misleading prefix.

    ``focus_ids`` names the units an *owed re-extraction* packet is about.
    Such a packet renders only the state rows, the focused units' own rows
    (evidence, passage, read header), and nothing else: with whole-page
    admission (D1 fix-round 2) a read's own admitted set can be hundreds of
    units, and an owed call that also re-sent the whole rest of the packet
    would resend a page the first extraction call already saw in full.
    ``dispositions`` are never rendered here at all -- they stay in
    ``state``/the run's own disposition list, not duplicated into packet
    text that scales with the unit count.

    ``query`` orders each read's own passage dump, and its own unit rows, by
    rank against it (D2), spending the packet's budget on the passages that
    answer the query first, instead of the read's raw document order that
    put a page's own navigation ahead of the mid-page chunk that actually
    answered it. ``None`` (a caller with no query of its own) keeps the dump
    in document order, exactly as before. A locator with its own unit row is
    never *also* dumped: doubling every admitted passage is exactly what
    made whole-page admission blow the packet's budget on its own duplicate.

    ``for_decision=True`` gives a ReAct decision turn's own packet a
    different row plan from an extraction packet's: candidates and recorded
    findings before reads, and a read's units as one-line stubs (no excerpt
    text) rather than full passage dumps. Whole-page admission means a
    single read's units alone can exceed the whole decision budget, and a
    routing choice needs a candidate's title, target ids and status far
    more than it needs the page text an extraction call already has; the
    extraction packet (``for_decision=False``, the default) keeps today's
    order and full excerpts unchanged.

    ``omitted_evidence_ids`` is an out-parameter (RevSelectionR3 P2): when a
    caller passes a list, every evidence unit that did not fit this packet's
    own budget has its evidence id appended to it, in packing order. A unit
    the extraction call never saw is a capacity fact, not a relevance one --
    a caller that records a disposition for it must not call it
    ``irrelevant`` when the model was never shown it to judge at all.

    ``read_ids`` scopes the whole packet to those reads alone (S6): a
    per-page extraction call's own packet, in the normal row plan (reads,
    then evidence with full excerpts, then recorded findings, candidates,
    and the deduplicated passage dump) rather than the ``focus_ids`` shape,
    which doubles a unit's text as both an evidence row and a passage row --
    affordable for owed re-extraction's handful of passages, not for a whole
    page's worth of units. ``None`` renders every read the other filters
    admit, exactly as before.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit < len(_CONTEXT_OVERFLOW):
        return _CONTEXT_OVERFLOW[:limit]
    selected_evidence = {
        evidence_id: unit
        for evidence_id, unit in evidence.items()
        if target_id is None or target_id in unit.target_ids
    }
    selected_read_ids = {unit.read_id for unit in selected_evidence.values()}
    selected_reads = {
        read_id: read
        for read_id, read in reads.items()
        if target_id is None
        or target_id in read.target_ids
        or read_id in selected_read_ids
    }
    if read_ids is not None:
        allowed_read_ids = set(read_ids)
        selected_evidence = {
            evidence_id: unit
            for evidence_id, unit in selected_evidence.items()
            if unit.read_id in allowed_read_ids
        }
        selected_reads = {
            read_id: read
            for read_id, read in selected_reads.items()
            if read_id in allowed_read_ids
        }
    focused = [
        evidence_id
        for evidence_id in dict.fromkeys(focus_ids)
        if evidence_id in selected_evidence
    ]
    focus_rows: list[tuple[str, str]] = []
    focused_reads: set[str] = set()
    for evidence_id in focused:
        unit = selected_evidence[evidence_id]
        targets = ",".join(unit.target_ids) or "-"
        focus_rows.append(
            (
                f"evidence:{evidence_id}",
                f"evidence_id={evidence_id} read_id={unit.read_id} "
                f"locator={unit.locator} targets={targets} excerpt={unit.excerpt}",
            )
        )
        read = selected_reads.get(unit.read_id)
        if read is not None and unit.locator in read.passages:
            focus_rows.append(
                (
                    f"passage:{read.read_id}/{unit.locator}",
                    f"passage read_id={read.read_id} locator={unit.locator} "
                    f"text={read.passages[unit.locator]}",
                )
            )
        if read is not None and read.read_id not in focused_reads:
            focused_reads.add(read.read_id)
            focus_rows.append((f"read:{read.read_id}", _render_read(read)))
    focused_ids = {identifier for identifier, _row in focus_rows}
    rows: list[tuple[str, str]] = [
        ("state", f"target_id={state.target_id or '-'}"),
        (
            "state",
            f"next_action={next_acquisition_action(state)} "
            f"allowed_actions={','.join(allowed_acquisition_actions(state))} "
            f"remaining_calls={state.remaining_calls} "
            f"remaining_model_turns={state.remaining_model_turns}",
        ),
        (
            "state",
            "pending_passage_ids="
            + (",".join(state.pending_passage_ids) or "-")
            + " pending_extraction_ids="
            + (",".join(state.pending_extraction_ids) or "-"),
        ),
        (
            "state",
            "candidate_urls=" + (",".join(state.candidate_urls) or "-"),
        ),
        (
            "state",
            "attempted_urls=" + (",".join(state.attempted_urls) or "-"),
        ),
        ("state", "denied_urls=" + (",".join(state.denied_urls) or "-")),
        (
            "state",
            "read_urls=" + (",".join(state.read_urls) or "-"),
        ),
        (
            "state",
            "consecutive_searches="
            f"{state.consecutive_searches} empty_searches={state.empty_searches}",
        ),
        (
            "state",
            "next_required_support_type="
            + (
                "target-bearing passage"
                if state.pending_passage_ids
                else "another source"
                if not state.candidate_urls
                else "read candidate"
            ),
        ),
        *focus_rows,
    ]
    if focused:
        # Owed re-extraction: focus rows are the whole packet. The rest of
        # this function's rows would resend the full read set an earlier,
        # unbounded extraction call already saw.
        return _packed_rows(rows, limit)[0]
    if for_decision:
        # A ReAct decision turn's own row plan: candidates and recorded
        # findings ahead of reads, and units as one-line stubs with no
        # excerpt. Whole-page admission means one read's units alone can
        # exceed the whole decision budget (D1 fix-round 2), so the
        # candidate rows a routing choice actually needs must never sit
        # behind them in the greedy pack.
        for candidate in state.candidate_records.values():
            rows.append(
                (f"candidate:{candidate.candidate_id}", _render_candidate(candidate))
            )
        recorded_ids: set[str] = set()
        for finding in findings:
            if finding.read_id not in selected_reads:
                continue
            identifier = f"recorded:{finding.read_id}/{finding.locator or '-'}"
            if identifier in recorded_ids:
                continue
            recorded_ids.add(identifier)
            rows.append(
                (
                    identifier,
                    f"recorded finding read_id={finding.read_id} "
                    f"locator={finding.locator or '-'} "
                    f"statement={_recorded_statement(finding)}",
                )
            )
        for read_id, read in selected_reads.items():
            rows.append((f"read:{read_id}", _render_read(read)))
        for evidence_id, unit in selected_evidence.items():
            targets = ",".join(unit.target_ids) or "-"
            rows.append(
                (
                    f"evidence:{evidence_id}",
                    f"evidence_id={evidence_id} read_id={unit.read_id} "
                    f"locator={unit.locator} targets={targets}",
                )
            )
        return _packed_rows(rows, limit)[0]
    # The reads the selected evidence came from are rendered immediately
    # ahead of that evidence, because ``build_findings`` requires every
    # finding to copy its read's own resolved_url and title verbatim: a
    # bounded packet that shows the evidence without the read record it
    # cites cannot become an admissible finding at all. The audited run's
    # topic-01 packet dropped every read row behind a reorder that put
    # evidence first, and every tracker finding the model drafted was
    # rejected for a source url or title that matched no admitted read.
    for read_id, read in selected_reads.items():
        if f"read:{read_id}" not in focused_ids:
            rows.append((f"read:{read_id}", _render_read(read)))
    # The selected units come before the reads' own passage dumps, because
    # they are what the packet is *for*: each row carries the excerpt the
    # selection chose. A caller's budget is bounded, and context the caller
    # did not select never crowds out the evidence it did.
    unit_keys: set[str] = set()
    for evidence_id, unit in selected_evidence.items():
        unit_keys.add(f"passage:{unit.read_id}/{unit.locator}")
        if f"evidence:{evidence_id}" in focused_ids:
            continue
        targets = ",".join(unit.target_ids) or "-"
        rows.append(
            (
                f"evidence:{evidence_id}",
                f"evidence_id={evidence_id} read_id={unit.read_id} "
                f"locator={unit.locator} targets={targets} excerpt={unit.excerpt}",
            )
        )
    # What the run already mined, after the evidence and before the passage
    # dumps: the rows are what stop a later pass re-mining a sentence it already
    # holds, and putting them ahead of the dumps means a bounded packet keeps
    # them — the dumps are the bulk, and the whole point of the steering is that
    # those passages need not be mined again. One row per (read, locator,
    # statement); a repeated identifier is the same passage saying the same
    # thing, which the fold has already collapsed in state.
    recorded_ids: set[str] = set()
    for finding in findings:
        if finding.read_id not in selected_reads:
            continue
        identifier = f"recorded:{finding.read_id}/{finding.locator or '-'}"
        if identifier in recorded_ids:
            continue
        recorded_ids.add(identifier)
        rows.append(
            (
                identifier,
                f"recorded finding read_id={finding.read_id} "
                f"locator={finding.locator or '-'} "
                f"statement={_recorded_statement(finding)}",
            )
        )
    for candidate in state.candidate_records.values():
        rows.append(
            (f"candidate:{candidate.candidate_id}", _render_candidate(candidate))
        )
    # A locator with its own unit row above is never dumped again: with
    # whole-page admission almost every passage of a selected read has one,
    # so re-rendering it here would double the packet for no reason.
    for read_id, read in selected_reads.items():
        order = (
            _ranked_locators(read.passages, query)
            if query is not None
            else list(read.passages)
        )
        for locator in order:
            key = f"passage:{read_id}/{locator}"
            if key in focused_ids or key in unit_keys:
                continue
            text = read.passages[locator]
            rows.append(
                (
                    key,
                    f"passage read_id={read_id} locator={locator} text={text}",
                )
            )
    text, omitted = _packed_rows(rows, limit)
    if omitted_evidence_ids is not None:
        for identifier in omitted:
            if identifier.startswith("evidence:"):
                omitted_evidence_ids.append(identifier[len("evidence:") :])
    return text


# How many omitted ids the continuation line names outright before it
# collapses to a count: with whole-page admission an overflowing sub-topic
# can omit hundreds of ids, and a continuation line that lists all of them
# is itself long enough to blow the packet's own remaining budget.
_CONTINUATION_IDS_SHOWN = 20


def _packed_rows(
    rows: Sequence[tuple[str, str]], limit: int
) -> tuple[str, list[str]]:
    """Greedily pack ``rows`` into ``limit`` characters, atomic per row.

    Returns the rendered text and the identifiers of every row that did not
    fit -- a caller marking the units a downstream call never saw needs to
    know which ones those were, never just that some were omitted.
    """
    lines: list[str] = []
    used = 0
    omitted: list[str] = []
    for identifier, row in rows:
        rendered = f"- {row}\n"
        if used + len(rendered) <= limit:
            lines.append(rendered.rstrip("\n"))
            used += len(rendered)
        else:
            omitted.append(identifier)
    if omitted:
        shown = omitted[:_CONTINUATION_IDS_SHOWN]
        remaining = len(omitted) - len(shown)
        continuation = "- continuation_ids=" + ",".join(shown)
        if remaining:
            continuation += f" (and {remaining} more)"
        if used + len(continuation) + 1 <= limit:
            lines.append(continuation)
        else:
            lines.append("- " + _CONTEXT_OVERFLOW)
    return "\n".join(lines), omitted

__all__ = [
    "AcquisitionAction",
    "AcquisitionPolicy",
    "ReadAdmission",
    "ToolPolicyDecision",
    "UNMINED_QUANTITY_REASON",
    "admit_read_result",
    "allowed_acquisition_actions",
    "build_acquisition_context",
    "build_read_record_from_tool_result",
    "next_acquisition_action",
    "search_is_admissible",
    "select_passages_with_lede",
    "select_relevant_passages",
    "split_read_body",
]
