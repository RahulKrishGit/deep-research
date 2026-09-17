"""Acquisition policy, read admission, caching, and decision context.

The module is deliberately provider-agnostic. A native ReAct loop supplies
tool results here; this code decides which results are admissible, retains
complete bodies, and exposes only exact selected passages to extraction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, MutableMapping, Sequence
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
    passages_from_chunks,
    validate_cached_read,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActDecision, ReActStep
from deep_research.tools.base import ToolResult
from deep_research.tools.passage_selection import select_relevant_passages
from deep_research.utils.types import (
    AcquisitionState,
    CandidateRecord,
    EvidenceDisposition,
    EvidenceUnit,
    ReadRecord,
)

AcquisitionAction = Literal["search", "read", "extract", "finish"]
OriginName = Literal["researcher", "fact_checker"]

# The single line a packet falls back to when even the continuation list cannot
# fit inside the configured budget. Kept short so it fits wherever a packet is
# allowed to exist at all, and never a sliced record.
_CONTEXT_OVERFLOW = "continuation_ids=packet_overflow"

# How much text a page may carry and still be read as an automated-access
# shell. A browser check, a consent wall, or a denial page is always a few
# hundred characters; a real document is not, which is what keeps a report
# that merely mentions "access denied" from losing its read record.
_SHELL_CONTENT_MAX_CHARS = 2000

# Candidate discoveries that may still be read after they leave the queue.
# ``document_link`` is deliberately absent: it is the manifest of a URL the
# model followed, never a claim that the run was given that URL.
_DISCOVERED_VIA = frozenset({"search", "memory"})


def next_acquisition_action(state: AcquisitionState) -> AcquisitionAction:
    """Return the next deterministic local acquisition action."""
    if state.pending_passage_ids:
        return "extract"
    if state.remaining_calls <= 0:
        return "finish"
    if state.candidate_urls and (
        state.consecutive_searches >= 2 or state.remaining_calls == 1
    ):
        return "read"
    if not state.candidate_urls and state.empty_searches >= 2:
        return "finish"
    return "read" if state.candidate_urls else "search"


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


def _payload_read_parts(
    result: ToolResult,
) -> tuple[str, str, str, str, str, dict[str, str], bool, str | None] | None:
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
            {"text": text, "chunk_index": 0}
        ]
        reader = "web_scraper"
        title = _text(data.get("title")) or resolved
    elif result.tool_name == "document_reader":
        raw_chunks = data.get("chunks")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            return None
        chunks = [item for item in raw_chunks if isinstance(item, Mapping)]
        if len(chunks) != len(raw_chunks):
            return None
        text = _chunk_text(chunks)
        requested = _text(data.get("requested_source")) or _text(
            data.get("source")
        )
        resolved = _text(data.get("resolved_source")) or requested
        if not (text and requested and resolved):
            return None
        reader = "document_reader"
        title = _text(data.get("title")) or resolved
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
    )


def _content_limitation(text: str, title: str = "") -> str | None:
    """Classify an automated-access shell without length-gating real sources.

    A shell page is *short* and says one of a few things: a browser check, a
    consent wall, or an outright denial. The marker words alone cannot say
    that — a genuine report that *discusses* access denial, captchas, or
    consent management is a document, and refusing it a read record would
    invert the rule that a short authoritative page is never classified
    unusable by length alone. A marker therefore only classifies a shell when
    the body could not have carried a document in the first place.
    """
    markers = (
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
    body = " ".join(text.split()).casefold()
    if len(body) > _SHELL_CONTENT_MAX_CHARS:
        return None
    lowered = f"{' '.join(title.split()).casefold()} {body}"
    if any(marker in lowered for marker in markers):
        return "unusable_content_shell"
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


def admit_read_result(
    result: ToolResult,
    *,
    session_id: str,
    target_id: str | None = None,
    query: str = "source evidence",
    origin: OriginName = "researcher",
    selected_limit: int = 4,
    retrieved_at: str | None = None,
    sequence: int = 0,
    configuration_fingerprint: str = "acquisition-v1",
) -> ReadAdmission | None:
    """Admit a read and select exact target-bearing passages from its body."""
    if selected_limit < 1:
        raise ValueError("selected_limit must be at least 1")
    read = build_read_record_from_tool_result(
        result,
        session_id=session_id,
        retrieved_at=retrieved_at,
        target_ids=() if target_id is None else (target_id,),
    )
    if read is None:
        return None
    target_ids = () if target_id is None else (target_id,)
    selected_locators = select_relevant_passages(
        read.passages, query, selected_limit
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

    A searched or remembered candidate stays readable after it leaves the
    queue, and a URL the run already attempted may be retried while its call
    budget lasts (a denial is checked separately). Anything else is the
    model's guess about the publisher's file layout, and guessing is how a
    denied landing page turns into a fabricated document URL. There is no
    publisher-wide circuit here: a *discovered* document on a denied host
    stays readable.
    """
    if url in state.candidate_urls or url in state.attempted_urls:
        return True
    record = state.candidate_records.get(url)
    return record is not None and record.discovered_via in _DISCOVERED_VIA


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
    boundary_audits: dict[str, Any] = field(default_factory=dict)
    retrieved_at: Callable[[], str] = _utc_now_iso
    selected_passages_per_read: int = 4
    configuration_fingerprint: str = "acquisition-v1"
    cache: MutableMapping[str, ReadRecord] | None = None
    network_read_ids: set[str] | None = None
    _seen_queries: set[str] = field(default_factory=set, init=False)
    _last_search_failed: bool = field(default=False, init=False)
    _network_read_ids: set[str] = field(default_factory=set, init=False)
    _sequence: int = field(default=0, init=False)
    _cache: dict[str, ReadRecord] = field(default_factory=dict, init=False)
    _document_fallback_urls: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if self.selected_passages_per_read < 1:
            raise ValueError("selected_passages_per_read must be at least 1")
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

    @property
    def acquired_work_count(self) -> int:
        """Count unique network bodies, excluding cache admissions."""
        return len(self._network_read_ids)

    def start_turn(self) -> None:
        if self.state.remaining_model_turns > 0:
            self.state = self.state.model_copy(
                update={
                    "remaining_model_turns": self.state.remaining_model_turns - 1
                }
            )

    def complete_extraction(self) -> None:
        """Consume the local extraction handoff while keeping deferred IDs.

        ``pending_passage_ids`` are deliberately not cleared here.  They name
        exact locators omitted by the current bounded packet and must survive
        the handoff so a continuation can select them later.  Only the read
        IDs whose current batch was handed to the extractor are consumed.
        """
        self.state = self.state.model_copy(
            update={
                "pending_extraction_ids": [],
            }
        )

    def record_extraction_dispositions(
        self,
        source_urls: Sequence[str],
    ) -> None:
        """Account for selected units that yielded no extracted finding."""
        allowed_urls = {normalize_source_url(url) for url in source_urls}
        # A finding may preserve the requested landing URL while the read
        # registry correctly attributes the body to its resolved/serving URL.
        # Treat the two URLs as one provenance pair without trusting either
        # value as evidence on its own.
        for read in self.reads.values():
            if (
                normalize_source_url(read.resolved_url) in allowed_urls
                or normalize_source_url(read.requested_url) in allowed_urls
            ):
                allowed_urls.update(
                    {
                        normalize_source_url(read.resolved_url),
                        normalize_source_url(read.requested_url),
                    }
                )
        target_id = self.target_id
        for evidence_id, unit in self.evidence.items():
            if target_id is not None and target_id not in unit.target_ids:
                continue
            if normalize_source_url(unit.source_url) in allowed_urls:
                continue
            self.dispositions.append(
                EvidenceDisposition(
                    item_id=evidence_id,
                    stage="extraction",
                    reason="irrelevant",
                    target_ids=list(() if target_id is None else (target_id,)),
                )
            )

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
            return ToolPolicyDecision(
                allowed=False,
                reason="acquisition policy requires local extract before another tool",
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
        fallback_read_after_failed_search = (
            requested_kind == "read"
            and expected == "search"
            and self._last_search_failed
        )
        if requested_kind != expected and not (
            expected == "finish" and self.state.remaining_calls <= 0
        ) and not fallback_read_after_failed_search:
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
        if cached is not None:
            validated = validate_cached_read(
                cached,
                "".join(cached.passages.values()),
                expected_content_sha256=cached.content_sha256,
                version_eligible=True,
                validated_at=self.retrieved_at(),
            )
            if validated is not None:
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
                    reason="the URL was not returned as a queued candidate",
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
            )
            records[url] = existing
        elif existing is not None:
            records[url] = existing.model_copy(
                update={"status": status, "read_id": read_id or existing.read_id}
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
            original = self._cache.get(requested) or self.reads.get(cached_read_id)
            if original is not None:
                validated = validate_cached_read(
                    original,
                    "".join(original.passages.values()),
                    expected_content_sha256=original.content_sha256,
                    version_eligible=True,
                    validated_at=self.retrieved_at(),
                )
                if validated is not None:
                    # Keep the original network record as the canonical body;
                    # a cache hit is a local admission, not a second read.
                    self.reads.setdefault(validated.read_id, original)
                    selected = select_relevant_passages(
                        validated.passages,
                        self.query,
                        self.selected_passages_per_read,
                    )
                    target_ids = (
                        () if self.target_id is None else (self.target_id,)
                    )
                    for locator in selected:
                        unit = build_evidence_unit(
                            read=validated,
                            locator=locator,
                            excerpt=validated.passages[locator],
                            origin=self.origin,
                            target_ids=target_ids,
                        )
                        self.evidence[unit.evidence_id] = unit
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
                        sequence=self._sequence,
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
                    self._sequence += 1
                    read_urls = list(self.state.read_urls)
                    if validated.resolved_url not in read_urls:
                        read_urls.append(validated.resolved_url)
                    self.state = self.state.model_copy(
                        update={"read_urls": read_urls}
                    )
                    self._mark_candidate(
                        requested, status="read", read_id=validated.read_id
                    )
                    return
        admission = admit_read_result(
            result,
            session_id=self.session_id,
            target_id=self.target_id,
            query=self.query,
            origin=self.origin,
            selected_limit=self.selected_passages_per_read,
            retrieved_at=self.retrieved_at(),
            sequence=self._sequence,
            configuration_fingerprint=self.configuration_fingerprint,
        )
        self._sequence += 1
        if admission is not None:
            read = admission.read
            prior = self._cache.get(read.resolved_url) or self._cache.get(
                read.requested_url
            )
            candidate = self.state.candidate_records.get(requested)
            if candidate is not None and candidate.title and (
                read.title == read.resolved_url
            ):
                read = read.model_copy(update={"title": candidate.title})
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
            self.reads[read.read_id] = read
            self.evidence.update(admission.evidence)
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
                "unsupported_content_type",
                "unsupported_document_format",
            }
            if error.type in extraction_failure_types:
                reason = error.type
            else:
                reason = (
                    "access_denied"
                    if error.type
                    in {"robots_disallowed", "access_denied", "HTTPStatusError"}
                    else "transport_failure"
                )
        else:
            # A successful result that could not satisfy the read contract is
            # still a visible attempted read; no body may disappear silently.
            reason = "malformed"
        self.dispositions.append(
            EvidenceDisposition(
                item_id=requested or "read-attempt",
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
        self._mark_candidate(requested, status=status)

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
            sequence=self._sequence,
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
        self._sequence += 1

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

    def context(self, *, limit: int) -> str:
        return build_acquisition_context(
            self.state,
            self.reads,
            self.evidence,
            limit=limit,
            target_id=self.target_id,
            dispositions=self.dispositions,
        )


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


def build_acquisition_context(
    state: AcquisitionState,
    reads: Mapping[str, ReadRecord],
    evidence: Mapping[str, EvidenceUnit],
    *,
    limit: int,
    target_id: str | None = None,
    dispositions: Sequence[EvidenceDisposition] = (),
) -> str:
    """Render complete acquisition records, with explicit continuation IDs.

    The returned body carries no section heading of its own: the decision
    prompt's renderer adds ``## Acquisition context`` exactly once, so the
    packet never spends budget on a duplicated header.

    A record is atomic for packet purposes: if it does not fit, its complete
    text is omitted and its ID is listed for continuation. No serialized
    search/PDF payload is sliced into a misleading prefix.
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
    rows: list[tuple[str, str]] = [
        ("state", f"target_id={state.target_id or '-'}"),
        (
            "state",
            f"next_action={next_acquisition_action(state)} "
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
                else "independent source"
                if not state.candidate_urls
                else "read candidate"
            ),
        ),
    ]
    for candidate in state.candidate_records.values():
        rows.append(
            (f"candidate:{candidate.candidate_id}", _render_candidate(candidate))
        )
    for read_id, read in selected_reads.items():
        rows.append((f"read:{read_id}", _render_read(read)))
        for locator, text in read.passages.items():
            rows.append(
                (
                    f"passage:{read_id}/{locator}",
                    f"passage read_id={read_id} locator={locator} text={text}",
                )
            )
    for evidence_id, unit in selected_evidence.items():
        targets = ",".join(unit.target_ids) or "-"
        rows.append(
            (
                f"evidence:{evidence_id}",
                f"evidence_id={evidence_id} read_id={unit.read_id} "
                f"locator={unit.locator} targets={targets} excerpt={unit.excerpt}",
            )
        )
    for disposition in dispositions:
        if target_id is not None and target_id not in disposition.target_ids:
            continue
        rows.append(
            (
                f"disposition:{disposition.item_id}",
                f"disposition item_id={disposition.item_id} "
                f"stage={disposition.stage} reason={disposition.reason}",
            )
        )

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
        continuation = "- continuation_ids=" + ",".join(omitted)
        if used + len(continuation) + 1 <= limit:
            lines.append(continuation)
        else:
            lines.append("- " + _CONTEXT_OVERFLOW)
    return "\n".join(lines)


__all__ = [
    "AcquisitionAction",
    "AcquisitionPolicy",
    "ReadAdmission",
    "ToolPolicyDecision",
    "admit_read_result",
    "build_acquisition_context",
    "build_read_record_from_tool_result",
    "next_acquisition_action",
    "select_relevant_passages",
]
