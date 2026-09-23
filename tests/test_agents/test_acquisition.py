"""Offline acquisition policy and read-admission regressions."""

from __future__ import annotations

import json
from collections.abc import MutableMapping, Sequence
from pathlib import Path

import httpx
import pytest

from deep_research.agents.acquisition import (
    PASSAGE_SELECTION_OPERATION,
    READ_ADMISSION_OPERATION,
    AcquisitionPolicy,
    ManifestSequence,
    build_acquisition_context,
    build_read_record_from_tool_result,
    cache_reuse_problem,
    next_acquisition_action,
)
from deep_research.agents.evidence import build_read_record, merge_evidence_units
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import (
    FindingDraft,
    SubTopicFindingsDraft,
    build_findings,
)
from deep_research.agents.steps import ReActDecision, ReActObservation, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.graph.nodes import agent_node
from deep_research.graph.state import dump_state, load_state
from deep_research.tools.base import BaseTool, ToolError, ToolResult
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.passage_selection import select_relevant_passages
from deep_research.utils.types import (
    AcquisitionState,
    BoundaryAudit,
    CandidateRecord,
    EvidenceUnit,
    ReadRecord,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import EchoTool, agent_scope, finish, use_tool
from tests.graph_fakes import FakeAgent, fake_research_state
from tests.research_fakes import (
    FakeSearchClient,
    page_client,
    research_tools,
)


def test_two_searches_require_read_but_failed_reads_do_not_deadlock() -> None:
    state = AcquisitionState(
        candidate_urls=["https://primary.example/report.pdf"],
        consecutive_searches=2,
        remaining_calls=3,
    )
    assert next_acquisition_action(state) == "read"

    state = state.model_copy(
        update={
            "candidate_urls": [],
            "denied_urls": ["https://primary.example/report.pdf"],
            "remaining_calls": 2,
            "consecutive_searches": 0,
        }
    )
    assert next_acquisition_action(state) == "search"


def test_acquisition_reducer_does_not_resurrect_consumed_candidates() -> None:
    url = "https://example.test/report.pdf"
    candidate = CandidateRecord(
        candidate_id="candidate-1",
        url=url,
        discovered_via="search",
    )
    previous = AcquisitionState(
        target_id="target-1",
        candidate_urls=[url],
        candidate_records={url: candidate},
        remaining_calls=2,
    )
    consumed = previous.model_copy(
        update={
            "candidate_urls": [],
            "attempted_urls": [url],
            "read_urls": [url],
            "candidate_records": {
                url: candidate.model_copy(update={"status": "read"})
            },
            "remaining_calls": 1,
        }
    )
    state = ResearchState(
        session_id="session-1",
        original_question="question",
        acquisition_state_by_target={"target-1": previous},
    )

    merged = merge_research_state(
        state,
        {"acquisition_state_by_target": {"target-1": consumed}},
    )

    result = merged.acquisition_state_by_target["target-1"]
    assert result.candidate_urls == []
    assert result.candidate_records[url].status == "read"
    assert result.remaining_calls == 1


def test_late_passage_is_selected() -> None:
    passages = {
        "p1": "Table of contents",
        "p80": "Queue delay prevents project commissioning.",
    }
    assert select_relevant_passages(passages, "queue delay commissioning", 1) == [
        "p80"
    ]


def test_explicit_document_suffix_routes_to_document_reader() -> None:
    policy = AcquisitionPolicy(
        state=AcquisitionState(
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
    )
    decision = ReActDecision(
        thought="Read PDF as HTML.",
        action="use_tool",
        tool_name="web_scraper",
        tool_input_json='{"url":"https://example.test/report.pdf"}',
    )

    result = policy.before_action(
        decision, {"url": "https://example.test/report.pdf"}
    )

    assert result.allowed is False
    assert "document_reader" in result.reason


def _read_result(
    *, success: bool = True, text: str = "late queue evidence"
) -> ToolResult:
    data = {
        "source": "https://example.test/report.pdf",
        "requested_source": "https://example.test/report.pdf",
        "resolved_source": "https://cdn.example.test/report.pdf",
        "title": "Report",
        "chunks": [{"text": text, "chunk_index": 0, "page": 80}],
        "content_sha256": "unused",
        "extraction_complete": True,
    }
    if success:
        from deep_research.agents.evidence import normalized_content_sha256

        data["content_sha256"] = normalized_content_sha256(text)
        return ToolResult(
            tool_name="document_reader", success=True, data=data, latency_ms=0
        )
    return ToolResult(
        tool_name="document_reader",
        success=False,
        data=data,
        error=ToolError(
            type="empty_document_content",
            message="document extraction produced no text",
        ),
        latency_ms=0,
    )


def test_failed_tool_payload_never_mints_a_read_record() -> None:
    assert (
        build_read_record_from_tool_result(
            _read_result(success=False), session_id="session-1"
        )
        is None
    )


def test_short_access_shell_is_not_admitted_as_usable_evidence() -> None:
    result = ToolResult(
        tool_name="web_scraper",
        success=True,
        data={
            "url": "https://example.test/blocked",
            "requested_url": "https://example.test/blocked",
            "resolved_url": "https://example.test/blocked",
            "title": "Checking your browser",
            "text": "Enable JavaScript and cookies to continue.",
            "content_sha256": "ignored",
            "extraction_complete": True,
        },
        latency_ms=0,
    )

    assert (
        build_read_record_from_tool_result(result, session_id="session-1")
        is None
    )


def test_shared_successful_read_cache_reselects_for_a_second_target() -> None:
    shared_reads = {}
    shared_evidence = {}
    shared_dispositions = []
    shared_audits = {}
    shared_cache = {}
    shared_network_ids = set()
    first = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-1",
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-1",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        boundary_audits=shared_audits,
        cache=shared_cache,
        network_read_ids=shared_network_ids,
    )
    result = _read_result()
    step = ReActStep(
        iteration=1,
        thought="Read source.",
        action="use_tool",
        tool_name="document_reader",
        tool_input={"source": "https://example.test/report.pdf"},
        observation=ReActObservation(
            tool_name="document_reader", success=True, summary="read"
        ),
        tool_result=result,
    )
    first.after_action(step)

    second = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-2",
            candidate_urls=["https://example.test/report.pdf"],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-2",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        boundary_audits=shared_audits,
        cache=shared_cache,
        network_read_ids=shared_network_ids,
    )
    decision = ReActDecision(
        thought="Reuse the source.",
        action="use_tool",
        tool_name="document_reader",
        tool_input_json='{"source":"https://example.test/report.pdf"}',
    )
    cached = second.before_action(decision, {"source": "https://example.test/report.pdf"})

    assert cached.allowed is True
    assert cached.charge_tool_budget is False
    assert cached.result is not None
    assert first.acquired_work_count == 1
    second.after_action(
        step.model_copy(update={"tool_result": cached.result})
    )
    assert second.acquired_work_count == 1
    assert len(shared_reads) == 1
    assert any("target-2" in unit.target_ids for unit in shared_evidence.values())


def test_acquisition_context_keeps_ids_when_a_complete_record_overflows() -> None:
    """An overflowing record is named for continuation, never sliced.

    The passage carries a unique tail, so finding any part of it in the packet
    would prove the packet had handed over a fragment of a record it could not
    fit whole — the failure an atomic-record budget exists to prevent. The
    record's own id must appear in the continuation list instead.
    """
    tail = "ZZZCONTINUATIONPROBE"
    result = _read_result(
        text="queue delay commissioning " + "x" * 1400 + tail
    )
    read = build_read_record_from_tool_result(result, session_id="session-1")
    assert read is not None
    state = AcquisitionState(
        target_id="target-1",
        candidate_urls=["https://example.test/report.pdf"],
        pending_passage_ids=[f"{read.read_id}/page-80-chunk-0"],
        remaining_calls=2,
        remaining_model_turns=1,
    )
    context = build_acquisition_context(state, {read.read_id: read}, {}, limit=900)
    assert "target_id=target-1" in context
    assert "pending_passage_ids=" in context
    assert "continuation_ids=" in context
    assert tail not in context
    assert read.passages["page-80-chunk-0"] not in context
    assert f"passage:{read.read_id}/page-80-chunk-0" in context
    # The packet carries no heading of its own: the decision prompt's renderer
    # adds "## Acquisition context" exactly once.
    assert "## Acquisition context" not in context


@pytest.mark.asyncio
async def test_policy_rejection_is_an_observation_without_tool_budget_charge(
    tracker,
) -> None:
    policy_calls: list[str] = []

    def policy(decision: ReActDecision, tool_input: dict[str, object]):
        del tool_input
        policy_calls.append(decision.tool_name or "")
        return False

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset([EchoTool(tracker)], allowed=["echo"]),
            decide=_decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            job_id="job-1",
        )
    assert policy_calls == ["echo"]
    assert run.tool_calls == 0
    assert run.steps[0].observation is not None
    assert run.steps[0].observation.error_type == "agent_tool_policy_rejected"
    assert run.steps[0].proposal_id == "job-1/turn-1/call-0"


@pytest.mark.asyncio
async def test_policy_tracks_model_turn_capacity_separately(tracker) -> None:
    policy = AcquisitionPolicy(
        state=AcquisitionState(remaining_calls=1, remaining_model_turns=2),
        session_id="session-1",
    )

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset([EchoTool(tracker)], allowed=["echo"]),
            decide=_decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            job_id="job-1",
        )

    assert run.iterations == 2
    assert policy.state.remaining_model_turns == 0


async def _decisions(
    iteration: int, steps: tuple[object, ...]
) -> tuple[ReActDecision, ...]:
    del steps
    if iteration == 1:
        return (
            use_tool("Try echo", "echo", json.dumps({"value": "x"})),
        )
    return (finish("Done", "No call needed."),)


# ---------------------------------------------------------------------------
# document routing: discovered sources only
# ---------------------------------------------------------------------------


def _search_result(*results: tuple[str, str]) -> ToolResult:
    return ToolResult(
        tool_name="web_search",
        success=True,
        data={
            "results": [
                {"url": url, "title": title} for url, title in results
            ]
        },
        latency_ms=0,
    )


def _search_step(*results: tuple[str, str]) -> ReActStep:
    return ReActStep(
        iteration=1,
        thought="Discover candidates.",
        action="use_tool",
        tool_name="web_search",
        tool_input={"query": "agency port capacity report"},
        observation=ReActObservation(
            tool_name="web_search", success=True, summary="search"
        ),
        tool_result=_search_result(*results),
    )


def _denied_step(url: str) -> ReActStep:
    return ReActStep(
        iteration=2,
        thought="Read the landing page.",
        action="use_tool",
        tool_name="web_scraper",
        tool_input={"url": url},
        observation=ReActObservation(
            tool_name="web_scraper",
            success=False,
            summary="denied",
            error_type="access_denied",
        ),
        tool_result=ToolResult(
            tool_name="web_scraper",
            success=False,
            data=None,
            error=ToolError(type="access_denied", message="403 forbidden"),
            latency_ms=0,
        ),
    )


def _read_decision(tool_name: str, url: str) -> ReActDecision:
    return ReActDecision(
        thought="Read it.",
        action="use_tool",
        tool_name=tool_name,
        tool_input_json=json.dumps({"url": url}),
    )


def test_denied_html_allows_the_discovered_official_pdf_and_no_guess() -> None:
    """A denial is not a publisher-wide circuit; a guess is still a guess.

    The acceptance case is denied HTML -> DISCOVERED official PDF. A PDF the
    search actually returned is readable even though its host was just denied,
    while a URL the model synthesised by swapping a suffix onto that denied
    page must never be attempted, and the denied URL itself is not retried.
    """
    landing = "https://agency.example/queue-report.html"
    pdf = "https://agency.example/queue-report.pdf"
    guessed = "https://agency.example/queue-report-2024.pdf"
    policy = AcquisitionPolicy(
        state=AcquisitionState(remaining_calls=5),
        session_id="session-1",
        target_id="target-1",
    )

    policy.after_action(_search_step((landing, "Queue report")))
    policy.after_action(_denied_step(landing))
    assert landing in policy.state.denied_urls

    policy.after_action(_search_step((pdf, "Queue report (PDF)")))

    allowed = policy.before_action(
        _read_decision("document_reader", pdf), {"url": pdf}
    )
    assert allowed.allowed is True

    guessed_result = policy.before_action(
        _read_decision("document_reader", guessed), {"url": guessed}
    )
    assert guessed_result.allowed is False
    assert "synthes" in guessed_result.reason

    retried = policy.before_action(
        _read_decision("web_scraper", landing), {"url": landing}
    )
    assert retried.allowed is False
    assert "denied" in retried.reason


def test_a_long_document_that_discusses_access_denial_keeps_its_read() -> None:
    """A genuine report is not a shell just because it names one.

    The historical failure mode is the inverse: a real document that talks
    about denials, captchas, or consent walls losing its read record to a
    substring match, which refuses citable evidence outright.
    """
    paragraph = (
        "Section 4. Access denied responses. Twelve percent of automated "
        "requests to the public portal received an access denied response, "
        "and the captcha challenge rate rose after the portal migration. "
    )
    text = paragraph * 20
    result = ToolResult(
        tool_name="web_scraper",
        success=True,
        data={
            "url": "https://agency.example/portal-study",
            "requested_url": "https://agency.example/portal-study",
            "resolved_url": "https://agency.example/portal-study",
            "title": "Public records portal study",
            "text": text,
            "extraction_complete": True,
        },
        latency_ms=0,
    )

    read = build_read_record_from_tool_result(result, session_id="session-1")

    assert read is not None
    assert read.extraction_complete is True


def test_a_short_shell_body_is_still_refused_its_read() -> None:
    """The length gate narrows the marker match; it does not remove it."""
    result = ToolResult(
        tool_name="web_scraper",
        success=True,
        data={
            "url": "https://agency.example/blocked",
            "requested_url": "https://agency.example/blocked",
            "resolved_url": "https://agency.example/blocked",
            "title": "Just a moment",
            "text": "Access denied. Automated access is not permitted here.",
            "extraction_complete": True,
        },
        latency_ms=0,
    )

    assert (
        build_read_record_from_tool_result(result, session_id="session-1") is None
    )


# ---------------------------------------------------------------------------
# the local extract gate: a bounded continuation batch that terminates
# ---------------------------------------------------------------------------

_STUDY_URL = "https://agency.example/queue-study.pdf"


def _paged_result(pages: int, *, text: str) -> ToolResult:
    """A document_reader payload carrying one extracted chunk per page."""
    chunks = [
        {"text": f"{text} page {page}", "chunk_index": 0, "page": page}
        for page in range(1, pages + 1)
    ]
    return ToolResult(
        tool_name="document_reader",
        success=True,
        data={
            "source": _STUDY_URL,
            "requested_source": _STUDY_URL,
            "resolved_source": _STUDY_URL,
            "title": "Queue study",
            "chunks": chunks,
            "extraction_complete": True,
        },
        latency_ms=0,
    )


def _document_step(result: ToolResult, url: str = _STUDY_URL) -> ReActStep:
    return ReActStep(
        iteration=1,
        thought="Read the document.",
        action="use_tool",
        tool_name="document_reader",
        tool_input={"source": url},
        observation=ReActObservation(
            tool_name="document_reader", success=True, summary="read"
        ),
        tool_result=result,
    )


def _policy(
    *,
    query: str = "queue delay commissioning",
    candidate_urls: Sequence[str] = (_STUDY_URL,),
    remaining_calls: int = 5,
    selected_passages_per_read: int = 4,
    target_id: str | None = "target-1",
    reads: dict[str, ReadRecord] | None = None,
    evidence: dict[str, EvidenceUnit] | None = None,
    dispositions: list | None = None,
) -> AcquisitionPolicy:
    return AcquisitionPolicy(
        state=AcquisitionState(
            target_id=target_id,
            candidate_urls=list(candidate_urls),
            remaining_calls=remaining_calls,
        ),
        session_id="session-1",
        target_id=target_id,
        query=query,
        selected_passages_per_read=selected_passages_per_read,
        reads=reads if reads is not None else {},
        evidence=evidence if evidence is not None else {},
        dispositions=dispositions if dispositions is not None else [],
    )


def test_a_selection_miss_hands_over_a_bounded_second_passage_batch() -> None:
    """A page that matches no query term is not "there is no evidence".

    The single chunk of a page whose text shares no term with the query is
    omitted by the first (query-scored) batch. The gate then says "extract",
    and the local step has to hand that chunk over anyway — in reader order —
    or the target is frozen out of acquisition for the rest of the pass.
    """
    policy = _policy()
    policy.after_action(
        _document_step(_paged_result(1, text="An unrelated cover page."))
    )
    read_id = next(iter(policy.reads))
    locator = f"{read_id}/page-1-chunk-0"

    assert policy.state.pending_passage_ids == [locator]
    assert next_acquisition_action(policy.state) == "extract"
    assert policy.evidence == {}

    decision = ReActDecision(
        thought="Search again.",
        action="use_tool",
        tool_name="web_search",
        tool_input_json='{"query": "another angle"}',
    )
    allowed = policy.before_action(decision, {"query": "another angle"})

    assert allowed.allowed is True
    assert policy.state.pending_passage_ids == []
    assert next_acquisition_action(policy.state) != "extract"
    (unit,) = policy.evidence.values()
    assert unit.locator == "page-1-chunk-0"
    assert unit.excerpt == policy.reads[read_id].passages["page-1-chunk-0"]


def test_the_second_passage_batch_is_bounded_and_terminates() -> None:
    """Two batches, then an explicit disposition — never a third batch.

    Twelve scored passages, four per batch: batch one takes four, the
    continuation batch takes four more, and the last four leave the pending
    list for good. The persisted state can therefore never re-enter "extract",
    which is what froze a target behind an append-only list.
    """
    policy = _policy(remaining_calls=3)
    policy.after_action(
        _document_step(_paged_result(12, text="queue delay commissioning"))
    )
    read_id = next(iter(policy.reads))

    assert len(policy.evidence) == 4
    assert len(policy.state.pending_passage_ids) == 8
    assert next_acquisition_action(policy.state) == "extract"

    policy.complete_extraction()

    assert len(policy.evidence) == 8
    assert policy.state.pending_passage_ids == []
    assert policy.state.pending_extraction_ids == []
    assert next_acquisition_action(policy.state) != "extract"

    terminal = {
        f"{read_id}/page-{page}-chunk-0" for page in range(9, 13)
    }
    assert not any(
        item in policy.state.pending_passage_ids for item in terminal
    )
    # The terminal omissions are recorded, not dropped silently: every one has
    # an explicit disposition, and the batch's selection manifest names them.
    disposed = {item.item_id for item in policy.dispositions}
    assert terminal <= disposed
    manifests = [
        audit
        for audit in policy.boundary_audits.values()
        if audit.operation == PASSAGE_SELECTION_OPERATION
        and terminal <= set(audit.deferred_ids)
    ]
    assert manifests

    # No third batch: the handoff is idempotent once the bound is reached.
    assert policy.complete_extraction() is None
    assert len(policy.evidence) == 8


def test_a_bound_of_one_batch_hands_everything_over_immediately() -> None:
    """The bound is a real parameter: one batch means no continuation."""
    policy = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-1",
            candidate_urls=[_STUDY_URL],
            remaining_calls=3,
        ),
        session_id="session-1",
        target_id="target-1",
        query="queue delay commissioning",
        passage_batch_limit=1,
    )
    policy.after_action(
        _document_step(_paged_result(6, text="queue delay commissioning"))
    )
    assert len(policy.evidence) == 4
    assert len(policy.state.pending_passage_ids) == 2

    policy.complete_extraction()

    # No continuation batch exists at this bound, so the two omitted locators
    # are terminated rather than left to gate acquisition forever.
    assert len(policy.evidence) == 4
    assert policy.state.pending_passage_ids == []
    assert next_acquisition_action(policy.state) != "extract"


def test_a_cache_entry_changed_since_its_citation_forces_a_recheck() -> None:
    """A citation-bearing cache entry is reused only at a matching fingerprint.

    The run cited this URL at one content version. The cache now holds a
    different read of it — a changed body, or a different read id — so reusing
    the stored entry would publish a citation to text nobody re-read. The
    reuse is refused instead, and the reader fetches the URL again.
    """
    record = _policy(remaining_calls=3)
    record.after_action(
        _document_step(_paged_result(4, text="queue delay commissioning"))
    )
    read = next(iter(record.reads.values()))
    cited = (read.read_id, read.content_sha256)

    assert (
        cache_reuse_problem(
            read,
            requested_url=read.resolved_url,
            cited_identity=cited,
        )
        is None
    )
    assert (
        cache_reuse_problem(
            read,
            requested_url=read.resolved_url,
            cited_identity=(read.read_id, "0" * 64),
        )
        == "content_hash_changed"
    )
    assert (
        cache_reuse_problem(
            read,
            requested_url=read.resolved_url,
            cited_identity=("read-other", read.content_sha256),
        )
        == "content_version_changed"
    )
    assert (
        cache_reuse_problem(
            read,
            requested_url="https://elsewhere.test/other",
            cited_identity=cited,
        )
        == "identity_mismatch"
    )
    # An uncited URL has no cited fingerprint to disagree with: the entry is
    # reusable, which is what keeps a plain re-read free.
    assert (
        cache_reuse_problem(
            read,
            requested_url=read.resolved_url,
            cited_identity=None,
        )
        is None
    )


def test_a_retryable_extraction_failure_leaves_the_reads_pending() -> None:
    """A handoff that produced nothing has consumed nothing.

    ``pending_extraction_ids`` names the reads whose batch was handed to an
    extractor. When that extraction fails retryably the reads stay pending —
    the next pass (or a resumed run) can see exactly what is still owed — and
    only a successful extraction clears them.
    """
    policy = _policy(remaining_calls=3)
    policy.after_action(
        _document_step(_paged_result(8, text="queue delay commissioning"))
    )
    read_id = next(iter(policy.reads))

    policy.defer_extraction()

    assert policy.state.pending_extraction_ids == [read_id]

    policy.complete_extraction()

    assert policy.state.pending_extraction_ids == []


def _failed_read_step(
    tool_name: str,
    url: str,
    *,
    error_type: str,
    iteration: int,
) -> ReActStep:
    """One failed read attempt, as the loop hands it to the policy."""
    return ReActStep(
        iteration=iteration,
        thought="Read it.",
        action="use_tool",
        tool_name=tool_name,
        tool_input={"url": url} if tool_name == "web_scraper" else {"source": url},
        observation=ReActObservation(
            tool_name=tool_name,
            success=False,
            summary="no usable body",
            error_type=error_type,
        ),
        tool_result=ToolResult(
            tool_name=tool_name,
            success=False,
            data=None,
            error=ToolError(type=error_type, message="no usable body"),
            latency_ms=0,
        ),
    )


@pytest.mark.asyncio
async def test_a_url_that_fails_twice_records_both_attempts_without_halting() -> None:
    """The designed PDF fallback can fail twice, and the run must survive it.

    ``web_scraper`` refusing a URL as ``unsupported_content_type`` sends
    ``document_reader`` at that same URL by design. When that read fails too,
    each attempt is its own fact — a different reader, a different failure —
    so both stay visible. Giving both attempts one identity made the second a
    contradiction for an item that already had a reason;
    ``merge_evidence_dispositions`` refuses those by raising, and the node
    turns that ``ValueError`` into ``graph_invalid_agent_state``: the run ends
    failed and publishes nothing.
    """
    url = "https://agency.example/looks-like-html"
    policy = _policy(candidate_urls=[url, "https://agency.example/other"])

    refused = policy.before_action(_read_decision("web_scraper", url), {"url": url})
    policy.after_action(
        _failed_read_step(
            "web_scraper", url, error_type="unsupported_content_type", iteration=1
        ),
        {"url": url},
    )
    fallback = policy.before_action(
        _read_decision("document_reader", url), {"source": url}
    )
    policy.after_action(
        _failed_read_step(
            "document_reader",
            url,
            error_type="document_extraction_failed",
            iteration=2,
        ),
        {"source": url},
    )

    # Both reads were allowed: the fallback is the design, not a violation.
    assert refused.allowed is True
    assert fallback.allowed is True

    # The pass's own update reaches the graph intact: no halt, and both
    # attempts on the record.
    agent = FakeAgent(
        "researcher", [{"evidence_dispositions": list(policy.dispositions)}]
    )
    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert state.errors == []
    attempts = [item for item in state.evidence_dispositions if item.stage == "read-selection"]
    assert [item.reason for item in attempts] == [
        "unsupported_content_type",
        "document_extraction_failed",
    ]
    assert len({item.item_id for item in attempts}) == 2


def test_a_target_whose_budget_is_spent_refuses_every_later_tool_call() -> None:
    """``remaining_calls`` is the run's, not one pass's.

    The per-target budget persists across passes — ``merge_acquisition_states``
    keeps the minimum, so spent capacity is never resurrected — and the
    Researcher resumes that state when it opens a *fresh* React loop for the
    same unanswered target on a refinement pass. That loop's own budget gate
    sees a full budget, so a waived policy means an exhausted target may search
    and read freely while its ``remaining_calls`` sits at zero: the discipline
    the budget exists to impose (read after two searches, finish when the leads
    are gone) is enforced in the pass that spent the budget and in no pass
    after it.
    """
    policy = _policy(
        candidate_urls=["https://lab.example/queued"], remaining_calls=0
    )

    assert next_acquisition_action(policy.state) == "finish"

    searches = [
        policy.before_action(
            ReActDecision(
                thought="Search again.",
                action="use_tool",
                tool_name="web_search",
                tool_input_json=json.dumps({"query": f"queue delay cost {index}"}),
            ),
            {"query": f"queue delay cost {index}"},
        )
        for index in range(4)
    ]
    read = policy.before_action(
        _read_decision("web_scraper", "https://lab.example/queued"),
        {"url": "https://lab.example/queued"},
    )

    assert [decision.allowed for decision in searches] == [False] * 4
    assert all("budget" in decision.reason for decision in searches)
    assert read.allowed is False
    assert policy.state.remaining_calls == 0
    assert policy.state.candidate_urls == ["https://lab.example/queued"]


def test_both_targets_survive_a_second_admission_of_one_body() -> None:
    """Reuse adds an association; it never replaces the earlier target's."""
    shared_reads: dict[str, ReadRecord] = {}
    shared_evidence: dict[str, EvidenceUnit] = {}
    shared_dispositions: list = []
    shared_cache: dict[str, ReadRecord] = {}
    shared_network: set[str] = set()
    first = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-1",
            candidate_urls=[_STUDY_URL],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-1",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        cache=shared_cache,
        network_read_ids=shared_network,
    )
    first.after_action(
        _document_step(_paged_result(1, text="queue delay commissioning"))
    )

    second = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-2",
            candidate_urls=[_STUDY_URL],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-2",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        dispositions=shared_dispositions,
        cache=shared_cache,
        network_read_ids=shared_network,
    )
    cached = second.before_action(
        _read_decision("document_reader", _STUDY_URL), {"source": _STUDY_URL}
    )
    assert cached.result is not None
    second.after_action(_document_step(cached.result))

    assert len(shared_evidence) == 1
    (unit,) = shared_evidence.values()
    assert unit.target_ids == ["target-1", "target-2"]
    # The registry the state merge sees is conflict-free: one unit per
    # (read_id, locator, excerpt), with additive targets.
    assert merge_evidence_units({}, dict(shared_evidence)) == shared_evidence


class _RecordingAudits(MutableMapping[str, BoundaryAudit]):
    """One run's boundary-audit mapping, remembering every write it is handed.

    A dict shows only what survived: a manifest written once and a manifest a
    later writer replaced in place are the same single entry. Recording the
    writes is what makes the difference visible.
    """

    def __init__(self) -> None:
        self.stored: dict[str, BoundaryAudit] = {}
        self.writes: list[tuple[str, BoundaryAudit]] = []

    def __setitem__(self, audit_id: str, audit: BoundaryAudit) -> None:
        self.writes.append((audit_id, audit))
        self.stored[audit_id] = audit

    def __getitem__(self, audit_id: str) -> BoundaryAudit:
        return self.stored[audit_id]

    def __delitem__(self, audit_id: str) -> None:
        del self.stored[audit_id]

    def __iter__(self) -> object:
        return iter(self.stored)

    def __len__(self) -> int:
        return len(self.stored)


def test_two_sub_topics_keep_their_own_acquisition_manifests() -> None:
    """One mapping, one run: a later sub-topic may not replace a sibling's.

    The Researcher builds one policy per sub-topic and hands every one of them
    the run's single ``boundary_audits`` mapping. A manifest id is
    fingerprinted from its job, its agent, its operation and its sequence, and
    the job and the agent are the same for every sub-topic of a run — so a
    sequence that restarts at each policy mints the id a sibling already used,
    with different contents, and the shared mapping keeps only the later one.
    Nothing raises here: the replacement happens locally, before any reducer
    reads the mapping, which is what separates it from an id collision the
    merge would refuse. Both sub-topics' manifests have to survive, each under
    its own id and each still the manifest its own write stored.
    """
    shared_reads: dict[str, ReadRecord] = {}
    shared_evidence: dict[str, EvidenceUnit] = {}
    shared_cache: dict[str, ReadRecord] = {}
    shared_network: set[str] = set()
    audits = _RecordingAudits()
    sequence = ManifestSequence()

    def _sub_topic_policy(target_id: str) -> AcquisitionPolicy:
        return AcquisitionPolicy(
            state=AcquisitionState(
                target_id=target_id,
                candidate_urls=[_STUDY_URL],
                remaining_calls=2,
            ),
            session_id="session-1",
            target_id=target_id,
            query="queue delay commissioning",
            reads=shared_reads,
            evidence=shared_evidence,
            boundary_audits=audits,
            audit_sequence=sequence,
            cache=shared_cache,
            network_read_ids=shared_network,
        )

    first = _sub_topic_policy("topic-01")
    first.after_action(
        _document_step(_paged_result(1, text="queue delay commissioning"))
    )
    second = _sub_topic_policy("topic-02")
    cached = second.before_action(
        _read_decision("document_reader", _STUDY_URL), {"source": _STUDY_URL}
    )
    assert cached.result is not None
    second.after_action(_document_step(cached.result))

    stale = [
        audit_id
        for audit_id, audit in audits.writes
        if audits.stored[audit_id] is not audit
    ]
    assert stale == [], "a later sub-topic replaced an earlier sub-topic's manifest"
    assert {
        (audit.operation, tuple(audit.target_ids))
        for audit in audits.stored.values()
    } == {
        (READ_ADMISSION_OPERATION, ("topic-01",)),
        (PASSAGE_SELECTION_OPERATION, ("topic-01",)),
        (READ_ADMISSION_OPERATION, ("topic-02",)),
        (PASSAGE_SELECTION_OPERATION, ("topic-02",)),
    }


def test_one_read_keeps_one_title_across_two_admissions() -> None:
    """A snippet title may label a read once; it may not relabel it later.

    Both admissions mint the same evidence identity, and the shared merge
    treats two different ``source_title`` values for one identity as an
    identity conflict. The first stored title therefore stands.
    """
    shared_reads: dict[str, ReadRecord] = {}
    first_url = "https://agency.example/queue-report"
    titles = ("Queue report", "Queue report (updated edition)")

    units: list[dict[str, EvidenceUnit]] = []
    for index, title in enumerate(titles, start=1):
        policy = AcquisitionPolicy(
            state=AcquisitionState(
                target_id=f"target-{index}",
                candidate_urls=[first_url],
                remaining_calls=2,
            ),
            session_id="session-1",
            target_id=f"target-{index}",
            query="queue delay",
            reads=shared_reads,
        )
        policy.after_action(_search_step((first_url, title)))
        result = ToolResult(
            tool_name="web_scraper",
            success=True,
            data={
                "url": first_url,
                "requested_url": first_url,
                "resolved_url": first_url,
                "title": "",
                "text": "Queue delay reached 34 months.",
                "extraction_complete": True,
            },
            latency_ms=0,
        )
        policy.after_action(_document_step(result, first_url))
        units.append(dict(policy.evidence))

    assert len(units[0]) == 1 and len(units[1]) == 1
    assert {unit.source_title for unit in units[0].values()} == {titles[0]}
    assert {unit.source_title for unit in units[1].values()} == {titles[0]}
    state = ResearchState(
        session_id="session-1",
        original_question="question",
        evidence_units=units[0],
    )
    merged = merge_research_state(state, {"evidence_units": units[1]})
    assert set(merged.evidence_units) == set(units[0])
    assert {unit.source_title for unit in merged.evidence_units.values()} == {
        titles[0]
    }


def test_a_reread_of_a_recorded_body_keeps_the_recorded_description() -> None:
    """One read id is described once: a second spelling is not a rewrite.

    A sub-topic can reach one page by another spelling of its URL — ``www.``
    is normalized away in the registry, so a body recorded as
    ``https://agency.example/queue-report`` may be fetched again as
    ``https://www.agency.example/queue-report``. Both admissions mint one
    ``read_id``, and ``merge_read_records`` refuses one identity carrying two
    descriptions, so the recorded one stands and the re-read adds only its own
    selection.
    """
    shared_reads: dict[str, ReadRecord] = {}
    shared_evidence: dict[str, EvidenceUnit] = {}
    url = "https://agency.example/queue-report"
    other_spelling = "https://www.agency.example/queue-report"

    def _admit(spelling: str) -> None:
        policy = AcquisitionPolicy(
            state=AcquisitionState(
                target_id="topic-01",
                candidate_urls=[spelling],
                remaining_calls=2,
            ),
            session_id="session-1",
            target_id="topic-01",
            query="queue delay",
            reads=shared_reads,
            evidence=shared_evidence,
        )
        policy.after_action(
            _document_step(
                ToolResult(
                    tool_name="web_scraper",
                    success=True,
                    data={
                        "url": spelling,
                        "requested_url": spelling,
                        "resolved_url": spelling,
                        "title": "Queue report",
                        "text": "Queue delay reached 34 months.",
                        "extraction_complete": True,
                    },
                    latency_ms=0,
                ),
                spelling,
            )
        )

    _admit(url)
    state = ResearchState(
        session_id="session-1",
        original_question="question",
        read_records=dict(shared_reads),
        evidence_units=dict(shared_evidence),
    )
    _admit(other_spelling)

    merged = merge_research_state(
        state,
        {
            "read_records": dict(shared_reads),
            "evidence_units": dict(shared_evidence),
        },
    )
    assert len(merged.read_records) == 1
    assert {read.requested_url for read in merged.read_records.values()} == {url}
    assert len(merged.evidence_units) == 1


def test_a_selected_passage_is_not_marked_used_by_a_sibling_passage() -> None:
    """Disposition is unit-level: a finding covers its own passage only.

    Both chunks of this page were selected. A finding extracted from the first
    says nothing about the second, so the second keeps its explicit
    ``irrelevant`` disposition instead of being skipped because its URL — the
    URL both passages share — produced a finding.
    """
    policy = _policy(selected_passages_per_read=2)
    policy.after_action(
        _document_step(_paged_result(2, text="queue delay commissioning"))
    )
    read = next(iter(policy.reads.values()))
    locators = sorted(policy.evidence)
    assert len(locators) == 2

    policy.record_extraction_dispositions([(read.read_id, "page-1-chunk-0")])

    reasons = {
        item.item_id: item.reason
        for item in policy.dispositions
        if item.stage == "extraction"
    }
    used = next(
        evidence_id
        for evidence_id, unit in policy.evidence.items()
        if unit.locator == "page-1-chunk-0"
    )
    unused = next(
        evidence_id
        for evidence_id, unit in policy.evidence.items()
        if unit.locator == "page-2-chunk-0"
    )
    assert used not in reasons
    assert reasons[unused] == "irrelevant"


@pytest.mark.asyncio
async def test_a_cache_hit_is_not_reported_as_an_external_tool_call(
    tracker,
) -> None:
    """Reuse is a completed action, not a tool call, and not acquired work."""
    shared_reads: dict[str, ReadRecord] = {}
    shared_evidence: dict[str, EvidenceUnit] = {}
    shared_cache: dict[str, ReadRecord] = {}
    shared_network: set[str] = set()
    first = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-1",
            candidate_urls=[_STUDY_URL],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-1",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        cache=shared_cache,
        network_read_ids=shared_network,
    )
    first.after_action(
        _document_step(_paged_result(1, text="queue delay commissioning"))
    )
    assert first.acquired_work_count == 1

    second = AcquisitionPolicy(
        state=AcquisitionState(
            target_id="target-2",
            candidate_urls=[_STUDY_URL],
            remaining_calls=2,
        ),
        session_id="session-1",
        target_id="target-2",
        query="queue delay commissioning",
        reads=shared_reads,
        evidence=shared_evidence,
        cache=shared_cache,
        network_read_ids=shared_network,
    )

    async def decisions(
        iteration: int, steps: tuple[object, ...]
    ) -> tuple[ReActDecision, ...]:
        del steps
        if iteration == 1:
            return (
                use_tool(
                    "Reuse the body.",
                    "document_reader",
                    json.dumps({"source": _STUDY_URL}),
                ),
            )
        return (finish("Done.", "Reused."),)

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset(
                [_NeverExecutedDocumentTool(tracker)], allowed=["document_reader"]
            ),
            decide=decisions,
            max_iterations=2,
            tool_budget=2,
            tool_policy=second,
            job_id="job-1",
        )

    # The external count is what a budget or yield report reads: a reused body
    # is not a second call. Checked before the local counter so a build that
    # simply lacks the counter still fails on the reported acquisition.
    assert run.tool_calls == 0
    assert run.cache_hits == 1
    assert second.acquired_work_count == 1
    assert run.steps[0].observation is not None
    assert run.steps[0].observation.success is True


class _NeverExecutedDocumentTool(BaseTool):
    """A tool named like the reader, so only a cache hit can satisfy it."""

    name = "document_reader"
    description = "Serve a scripted document read."
    input_schema = {"source": "string", "url": "string"}
    output_schema = {"text": "string"}

    async def _execute(self, context, **kwargs):  # type: ignore[override]
        del context, kwargs
        raise AssertionError("a cache hit must never reach the tool")


# ---------------------------------------------------------------------------
# the named adversarial cases the brief lists
# ---------------------------------------------------------------------------


def _gateway_policy(
    *,
    candidate_urls: Sequence[str] = (),
    remaining_calls: int = 4,
    query: str = "queue delay commissioning",
    selected_passages_per_read: int = 4,
    cache: dict[str, ReadRecord] | None = None,
    reads: dict[str, ReadRecord] | None = None,
    evidence: dict[str, EvidenceUnit] | None = None,
    network_read_ids: set[str] | None = None,
    target_id: str = "topic-01",
) -> AcquisitionPolicy:
    return AcquisitionPolicy(
        state=AcquisitionState(
            target_id=target_id,
            candidate_urls=list(candidate_urls),
            remaining_calls=remaining_calls,
        ),
        session_id="session-1",
        target_id=target_id,
        query=query,
        selected_passages_per_read=selected_passages_per_read,
        reads=reads if reads is not None else {},
        evidence=evidence if evidence is not None else {},
        cache=cache,
        network_read_ids=network_read_ids,
    )


_PRIOR_SESSION = "session-0"
_PRIOR_TEXT = (
    "The queue delay study measured commissioning delay at 40 percent of "
    "projects in 2024."
)


def _prior_session_artifact(
    *,
    text: str = _PRIOR_TEXT,
    recorded_hash: str | None = None,
) -> ReadRecord:
    """The read an earlier session stored for the study, through its own builder.

    A cache entry is an artifact some session actually read, so it is built the
    way that session would have built it — not assembled field by field in the
    test. ``recorded_hash`` overrides the digest the artifact claims, which is
    how a forged entry is stated: a stored read whose declared provenance is
    not the provenance of the bytes it holds.
    """
    record = build_read_record(
        session_id=_PRIOR_SESSION,
        reader="document_reader",
        requested_url=_STUDY_URL,
        resolved_url=_STUDY_URL,
        title="Queue study",
        retrieved_at="2024-11-01T00:00:00+00:00",
        text=text,
        passages={"page-1-chunk-0": text},
    )
    if recorded_hash is None:
        return record
    return record.model_copy(update={"content_sha256": recorded_hash})


def test_a_prior_sessions_read_is_recorded_as_this_sessions_cache_import() -> None:
    """An imported body keeps the reading session, and says it was imported.

    The registry a sub-topic writes into is what every later consumer reads
    provenance from. Filing the import there as an original network read of
    this session would claim bytes this session never fetched — ``cache`` kind,
    the reading session, and the moment of local validation exist precisely to
    say otherwise — and the earlier session that did fetch them would vanish
    from the record. A body this session's own registry already holds is not
    re-stamped: this session's read stays the original it is.
    """
    artifact = _prior_session_artifact()
    shared_reads: dict[str, ReadRecord] = {}
    shared_cache = {_STUDY_URL: artifact}
    policy = _gateway_policy(
        candidate_urls=[_STUDY_URL],
        cache=shared_cache,
        reads=shared_reads,
    )

    decision = policy.before_action(
        use_tool(
            "Reuse the stored read.",
            "document_reader",
            json.dumps({"source": _STUDY_URL}),
        ),
        {"source": _STUDY_URL},
    )

    assert decision.result is not None
    assert decision.result.metadata["acquisition_kind"] == "cache"
    policy.after_action(_document_step(decision.result))

    imported = shared_reads[artifact.read_id]
    assert imported.acquisition_kind == "cache"
    assert imported.origin_session_id == _PRIOR_SESSION
    assert imported.version_validated_at is not None
    # The stored body is what was imported: no second copy of it appears under
    # this session's own read identity, because nothing was fetched.
    assert list(shared_reads) == [artifact.read_id]


def test_a_forged_cache_entry_is_never_admitted() -> None:
    """A stored read whose declared hash is not its body's is refused.

    Forged provenance is the case a cache makes dangerous: bytes that were
    never read can be filed under a read identity by editing the record's own
    metadata. The digest the entry declares is checked against the body it
    stores, so the entry cannot stand in for a read — the URL is fetched
    instead and the registry records this session's own network read.
    """
    forged = _prior_session_artifact(recorded_hash="f" * 64)
    shared_reads: dict[str, ReadRecord] = {}
    policy = _gateway_policy(
        candidate_urls=[_STUDY_URL],
        cache={_STUDY_URL: forged},
        reads=shared_reads,
    )

    decision = policy.before_action(
        use_tool(
            "Reuse the stored read.",
            "document_reader",
            json.dumps({"source": _STUDY_URL}),
        ),
        {"source": _STUDY_URL},
    )

    assert decision.result is None
    assert decision.allowed is True
    policy.after_action(_document_step(_paged_result(1, text=_PRIOR_TEXT)))

    assert forged.read_id not in shared_reads
    assert [read.acquisition_kind for read in shared_reads.values()] == ["network"]


@pytest.mark.asyncio
async def test_a_three_search_batch_reserves_the_last_call_for_the_read(
    tracker,
) -> None:
    """One native batch of [search, search, search, read] on the last call.

    Every proposal gets its own observation: three policy rejections that cost
    no external budget, and the reserved read, which is the only charged call.
    """
    policy = _gateway_policy(
        candidate_urls=["https://example.test/qec"], remaining_calls=1
    )

    async def decisions(
        iteration: int, steps: tuple[object, ...]
    ) -> tuple[ReActDecision, ...]:
        del steps
        if iteration == 1:
            return (
                use_tool("Search one.", "web_search", '{"query": "qec 2024"}'),
                use_tool("Search two.", "web_search", '{"query": "qec 2023"}'),
                use_tool("Search three.", "web_search", '{"query": "qec 2022"}'),
                use_tool(
                    "Read the reserved candidate.",
                    "web_scraper",
                    '{"url": "https://example.test/qec"}',
                ),
            )
        return (finish("Done.", "Answer."),)

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset(
                research_tools(tracker, http=page_client()),
                allowed=["web_search", "web_scraper"],
            ),
            decide=decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            summary_limit=200,
            job_id="job-1",
        )

    first_turn = run.steps[:4]
    assert [step.tool_name for step in first_turn] == [
        "web_search",
        "web_search",
        "web_search",
        "web_scraper",
    ]
    assert [step.observation is not None for step in first_turn] == [True] * 4
    assert [
        step.observation.error_type for step in first_turn[:3]
    ] == ["agent_tool_policy_rejected"] * 3
    assert first_turn[3].observation.success is True
    # Only the reserved read spent external budget.
    assert run.tool_calls == 1
    assert policy.state.remaining_calls == 0


@pytest.mark.asyncio
async def test_the_third_search_result_reaches_the_next_decision_packet(
    tracker,
) -> None:
    """The 200-character summary is a log; the packet is the decision input.

    The third result sits after two long URLs, so the public observation
    summary cannot carry it. The next complete_react request must name the
    exact candidate URL with its state, or the model cannot read what it found.
    """
    long_first = "https://agency.example/" + "a-very-long-path-segment/" * 8
    third = "https://agency.example/queue-report-2024.pdf"
    policy = _gateway_policy()
    search = FakeSearchClient(
        [
            {
                "results": [
                    {
                        "title": "Long overview",
                        "url": long_first,
                        "content": "Queue " * 60,
                    },
                    {
                        "title": "Background",
                        "url": "https://agency.example/background",
                        "content": "Delay " * 60,
                    },
                    {
                        "title": "Queue report 2024",
                        "url": third,
                        "content": "Queue delay reached 34 months.",
                    },
                ]
            }
        ]
    )
    requests: list[tuple[object, ...]] = []

    async def decisions(
        iteration: int, steps: tuple[object, ...]
    ) -> tuple[ReActDecision, ...]:
        requests.append(steps)
        if iteration == 1:
            return (
                use_tool(
                    "Search for the report.",
                    "web_search",
                    '{"query": "interagency queue delay report"}',
                ),
            )
        return (finish("Done.", "Answer."),)

    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=AgentToolset(
                research_tools(tracker, search=search),
                allowed=["web_search"],
            ),
            decide=decisions,
            max_iterations=2,
            tool_budget=1,
            tool_policy=policy,
            summary_limit=200,
            job_id="job-1",
        )

    # The public summary is the truncated log; the decision packet is what the
    # next request carries, and it must name the exact candidate.
    assert len(run.steps[0].observation.summary) <= 200
    assert len(requests) == 2
    candidate = policy.state.candidate_records[third]
    assert candidate.status == "queued"
    packet = policy.context(limit=24000)
    assert f"url={third}" in packet
    assert f"candidate_id={candidate.candidate_id}" in packet
    assert "status=queued" in packet
    assert "attempted_urls=" in packet
    assert "denied_urls=" in packet


def test_two_empty_searches_end_with_no_candidate_instead_of_deadlock() -> None:
    """The no-candidate path: two empty searches, then finish."""
    policy = _gateway_policy(remaining_calls=4)
    for _ in range(2):
        policy.after_action(
            _search_step_result(_search_result(), query="empty query")
        )

    assert policy.state.empty_searches == 2
    assert policy.state.candidate_urls == []
    assert next_acquisition_action(policy.state) == "finish"
    rejection = policy.before_action(
        ReActDecision(
            thought="Search once more.",
            action="use_tool",
            tool_name="web_search",
            tool_input_json='{"query": "yet another"}',
        ),
        {"query": "yet another"},
    )
    assert rejection.allowed is False
    assert "no candidate work" in rejection.reason
    # A finish decision is never gated: the loop can always stop.
    assert (
        policy.before_action(
            finish("Nothing.", "No candidate source was found."), {}
        ).allowed
        is True
    )


def test_the_same_url_proposed_seven_times_keeps_every_target() -> None:
    """Seven targets reuse one body, and all seven associations survive."""
    url = "https://agency.example/queue-study.pdf"
    shared_reads: dict[str, ReadRecord] = {}
    shared_evidence: dict[str, EvidenceUnit] = {}
    shared_cache: dict[str, ReadRecord] = {}
    shared_network: set[str] = set()
    policies = [
        _gateway_policy(
            candidate_urls=[url],
            remaining_calls=2,
            target_id=f"target-{index}",
            reads=shared_reads,
            evidence=shared_evidence,
            cache=shared_cache,
            network_read_ids=shared_network,
        )
        for index in range(1, 8)
    ]

    policies[0].after_action(
        _document_step(_paged_result(1, text="queue delay commissioning"), url)
    )
    for policy in policies[1:]:
        cached = policy.before_action(
            _read_decision("document_reader", url), {"source": url}
        )
        assert cached.result is not None
        policy.after_action(
            _document_step(cached.result, url)
        )

    assert len(shared_reads) == 1
    assert len(shared_network) == 1
    assert policies[-1].acquired_work_count == 1
    assert len(shared_evidence) == 1
    (unit,) = shared_evidence.values()
    assert unit.target_ids == [f"target-{index}" for index in range(1, 8)]


def _search_step_result(
    result: ToolResult, *, query: str, iteration: int = 1
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought="Search.",
        action="use_tool",
        tool_name="web_search",
        tool_input={"query": query},
        observation=ReActObservation(
            tool_name="web_search", success=result.success, summary="search"
        ),
        tool_result=result,
    )


@pytest.mark.asyncio
async def test_a_late_csv_row_reaches_the_extraction_packet(tracker) -> None:
    """A CSV row past the first passage batch still reaches extraction.

    The reader chunks rows two at a time, the first batch takes the two
    highest-scoring chunks, and the row carrying the units and the footnote is
    in neither. Only the bounded continuation batch can hand it over; without
    one, the target is frozen and the measurement never reaches extraction.
    """
    body = (
        "project,queue_delay_months,commissioning_cost_musd,note\n"
        "Alpha,12,40,queue delay commissioning cost measured\n"
        "Bravo,18,55,queue delay commissioning cost measured\n"
        "Charlie,21,63,queue delay commissioning cost measured\n"
        "Delta,25,71,queue delay commissioning cost measured\n"
        "Echo,29,84,queue delay commissioning cost measured\n"
        "Foxtrot,31,97,queue delay commissioning cost measured\n"
        "Basin C,41,152,units are months and million USD; commissioning "
        "cost measured\n"
        "Footnote,3,,12 percent reported a negative net benefit\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/csv; charset=utf-8"},
            text=body,
            request=request,
        )

    url = "https://agency.example/queue-delay.csv"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with client, tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(
            tracker, client=client, csv_rows_per_chunk=2
        ).execute(source=url)

    assert result.success is True
    policy = _gateway_policy(
        candidate_urls=[url], remaining_calls=3, selected_passages_per_read=2
    )
    policy.after_action(_document_step(result, url))

    assert len(policy.state.pending_passage_ids) > 0
    assert not any(
        "Basin C,41,152" in unit.excerpt for unit in policy.evidence.values()
    )

    policy.complete_extraction()

    selected = " ".join(unit.excerpt for unit in policy.evidence.values())
    assert "Basin C,41,152" in selected
    assert "units are months and million USD" in selected
    assert "12 percent reported a negative net benefit" in selected
    assert policy.state.pending_passage_ids == []
    assert all(unit.excerpt in body for unit in policy.evidence.values())


@pytest.mark.asyncio
async def test_a_scanned_or_unparseable_document_states_its_limitation(
    tracker, monkeypatch
) -> None:
    """No fabricated text: the failure is named, and no read is minted."""
    class ScannedPage:
        def extract_text(self) -> str:
            return ""

    class ScannedPdf:
        pages = [ScannedPage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"not a pdf at all",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scanned_url = "https://agency.example/scanned.pdf"
    broken_url = "https://agency.example/broken.pdf"
    async with client, tracker.session_span("session-1", "question"):
        broken = await DocumentReaderTool(tracker, client=client).execute(
            source=broken_url
        )
        monkeypatch.setattr(
            "deep_research.tools.document_reader.pdfplumber.open",
            lambda _: ScannedPdf(),
        )
        scanned = await DocumentReaderTool(tracker, client=client).execute(
            source=scanned_url
        )

    assert scanned.success is False
    assert scanned.error is not None
    assert scanned.error.type == "document_extraction_failed"
    assert broken.success is False
    assert broken.error is not None
    assert broken.error.type == "document_extraction_failed"

    policy = _gateway_policy(
        candidate_urls=[scanned_url, broken_url], remaining_calls=4
    )
    policy.after_action(_document_step(scanned, scanned_url))
    policy.after_action(_document_step(broken, broken_url))

    reasons = {
        item.item_id: item.reason
        for item in policy.dispositions
        if item.stage == "read-selection"
    }
    # A disposition names the attempt: the URL and the reader that made it.
    assert reasons[f"{scanned_url}#document_reader#1"] == (
        "document_extraction_failed"
    )
    assert reasons[f"{broken_url}#document_reader#1"] == (
        "document_extraction_failed"
    )
    assert policy.reads == {}
    assert policy.evidence == {}


# ---------------------------------------------------------------------------
# the mandated offline documents
# ---------------------------------------------------------------------------

_FIXTURE_QUERY = "interconnection queue delay commissioning cost"


async def _read_fixture(tracker, filename: str) -> ToolResult:
    """Serve one committed fixture through the real reader, offline."""
    body = Path("tests/fixtures/documents", filename).read_bytes()
    url = f"https://agency.example/{filename}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/markdown; charset=utf-8"},
            content=body,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with client, tracker.session_span("session-1", "question"):
        return await DocumentReaderTool(
            tracker, client=client, chunk_chars=250
        ).execute(source=url)


def _fixture_sub_topic() -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title="Queue delay cost",
        rationale="The question asks what delay costs.",
        search_queries=["interconnection queue delay commissioning cost"],
        success_criteria=["A measured queue delay with its unit."],
        priority=1,
    )


@pytest.mark.asyncio
async def test_the_report_fixture_table_units_and_footnotes_reach_extraction(
    tracker,
) -> None:
    """A cover/contents report's late table and footnotes reach the packet."""
    filename = "usgs-shaped-report.md"
    result = await _read_fixture(tracker, filename)

    assert result.success is True
    assert result.data is not None
    assert result.data["extraction_complete"] is True
    document = "".join(chunk["text"] for chunk in result.data["chunks"])
    url = result.data["resolved_source"]
    policy = _gateway_policy(
        candidate_urls=[url], remaining_calls=3, query=_FIXTURE_QUERY
    )
    policy.after_action(_document_step(result, url))

    first_units = " ".join(unit.excerpt for unit in policy.evidence.values())
    assert policy.state.pending_passage_ids
    assert "Basin C is withheld" not in first_units

    first_packet = policy.context(limit=24000)
    # The complete read record stays visible in the packet — it is truncated
    # only as a whole record, never mid-passage — while the passage that no
    # batch has selected yet is not yet citable evidence.
    assert "Basin C is withheld" in first_packet

    policy.complete_extraction()

    packet = policy.context(limit=24000)
    assert "Queue delay (months)" in packet
    assert "Basin A | 34 | 128" in packet
    assert "million USD" in packet
    assert "Basin C is withheld" in packet
    selected = " ".join(unit.excerpt for unit in policy.evidence.values())
    assert "Basin C is withheld" in selected
    assert policy.state.pending_passage_ids == []
    # Every excerpt is verbatim text of the document that was actually read.
    for unit in policy.evidence.values():
        assert unit.excerpt in document


@pytest.mark.asyncio
async def test_a_contents_only_fixture_cannot_supply_a_measurement(
    tracker,
) -> None:
    """Without the data release there is nothing to report, and nothing to add."""
    filename = "usgs-shaped-contents-only.md"
    result = await _read_fixture(tracker, filename)

    assert result.success is True
    assert result.data is not None
    document = "".join(chunk["text"] for chunk in result.data["chunks"])
    url = result.data["resolved_source"]
    policy = _gateway_policy(
        candidate_urls=[url], remaining_calls=3, query=_FIXTURE_QUERY
    )
    policy.after_action(_document_step(result, url))
    policy.complete_extraction()

    packet = policy.context(limit=24000)
    assert "Basin A" not in packet
    assert "34" not in packet
    for unit in policy.evidence.values():
        assert unit.excerpt in document

    read = build_read_record_from_tool_result(result, session_id="session-1")
    assert read is not None
    (locator,) = list(read.passages)[:1]
    draft = SubTopicFindingsDraft(
        findings=[
            FindingDraft(
                content="Basin A's queue delay was 34 months.",
                source_url=read.resolved_url,
                source_title=read.title,
                confidence=0.9,
                read_id=read.read_id,
                locator=locator,
                excerpt="Basin A | 34 | 128",
                target_ids=["topic-01"],
            )
        ]
    )

    findings, rejected = build_findings(
        draft,
        sub_topic=_fixture_sub_topic(),
        extracted_at="2026-08-01T12:00:00+00:00",
        known_urls=[read.resolved_url],
        known_reads={read.read_id: read},
        target_id="topic-01",
    )

    assert findings == []
    assert rejected == ["finding 1: excerpt was not admitted at locator"]
