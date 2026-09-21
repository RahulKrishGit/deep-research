"""Offline agent doubles and domain builders for graph tests.

No provider, no tracker, no scratchpad, no toolset: the node wrappers
depend on the ``ResearchAgent`` protocol (``name`` plus ``run``), so a
graph test never has to assemble a real agent — which would need an API
key this repository deliberately does not have.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from deep_research.agents.base import AgentRun
from deep_research.agents.critic import failed_critique
from deep_research.agents.identity import (
    claim_fingerprint,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.report import (
    QUALITY_STATUS_NOT_GATED,
    ReportComposition,
    ReportPoint,
    render_evidence_ledger,
    render_reader_report,
    report_as_of,
    report_scope,
)
from deep_research.agents.steps import ReActRun
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.tools.base import ToolError, ToolResult
from deep_research.utils.types import (
    REVIEW_DIMENSIONS,
    Claim,
    Critique,
    CritiqueGap,
    Finding,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


class FakeAgent:
    """Serve scripted state updates instead of running a real agent.

    ``updates`` is consumed one entry per call and the final entry repeats,
    which is how "a critic that asks for one refinement and then accepts"
    is expressed without scripting every pass. A queued ``BaseException``
    is raised instead of returned, which is how agent failures are
    simulated.

    ``update_factory``, when given, computes the update from the state the
    agent was handed and takes precedence over ``updates``. That is how a
    double models an agent whose output depends on the pass — a Synthesizer
    composing from the evidence the pass collected, for instance.
    """

    def __init__(
        self,
        name: str,
        updates: Sequence[ResearchStateUpdate | BaseException] = (),
        *,
        update_factory: Callable[[ResearchState], ResearchStateUpdate]
        | None = None,
    ) -> None:
        self.name = name
        self._updates: list[Any] = list(updates) or [{}]
        self._update_factory = update_factory
        self.calls: list[ResearchState] = []

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.calls.append(state)
        if self._update_factory is not None:
            update: Any = self._update_factory(state)
        else:
            position = min(len(self.calls) - 1, len(self._updates) - 1)
            update = self._updates[position]
        if isinstance(update, BaseException):
            raise update
        return AgentRun(
            agent_name=self.name,
            result=None,
            react=ReActRun(agent_name=self.name, stop_reason="finished"),
            errors=[],
            state_update=update,
        )


def fake_sub_topic(
    title: str = "Error correction",
    *,
    coverage_id: str = "topic-01",
    priority: int = 1,
) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale="It is the bottleneck.",
        search_queries=["qec 2025"],
        success_criteria=["a logical error rate is quoted"],
        priority=priority,
    )


def fake_finding(content: str = "Break-even was reached.") -> Finding:
    return Finding(
        content=content,
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Error correction",
    )


def fake_scored_source(url: str = SOURCE_URL) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )


def fake_claim(
    text: str = "Break-even was reached in 2025.",
    *,
    url: str = SOURCE_URL,
    verdict: str = "verified",
    confidence: float = 0.8,
) -> Claim:
    return Claim(
        # Minor 1: the canonical identity, never an invented label — a
        # non-canonical id would still pass because the merge recomputes it.
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[url],
        verdict=verdict,
        confidence=confidence,
        evidence=["An independent review states the same figure."],
        contradictions=[],
        verification_evidence=[],
        # The fake's premise is a claim that already carries the badge under
        # test: ``Claim`` refuses a verified verdict with no ``verified_pair``
        # behind it, for every producer and every fixture alike.
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
        ),
    )


def fake_reader_composition(
    state: ResearchState,
    *,
    quality_status: str = QUALITY_STATUS_NOT_GATED,
) -> ReportComposition:
    """The composition one pass renders from the state's canonical snapshot.

    Built from the state it is handed so every point is claim-linked: each
    point carries its claim's exact identity and URLs, and every URL it cites
    is an assessed source the state already holds. That is what makes the
    deterministic quality gates *pass* rather than merely exist, so a test
    that asserts a clean snapshot is asserting the real gate list.

    A contradicted claim is deliberately left out of the reader points: the
    gate fails a report that presents one as settled, and this builder models
    the report the pass should have written.
    """
    claims = merge_claim_snapshot([], state.verified_claims)
    sources = merge_source_snapshot([], state.evaluated_sources)
    return ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        iteration=state.iteration,
        max_iterations=state.max_iterations,
        as_of=report_as_of(findings=state.raw_findings, events=state.events),
        scope=report_scope(state.sub_topics),
        quality_status=quality_status,
        sub_topics=list(state.sub_topics),
        claims=claims,
        sources=sources,
        findings=list(state.raw_findings),
        summary=[
            ReportPoint(
                text=claim.text,
                claim_ids=[claim.claim_id],
                source_urls=list(claim.source_urls),
            )
            for claim in claims
            if claim.verdict != "contradicted"
        ],
    )


def fake_synthesis_update(state: ResearchState) -> ResearchStateUpdate:
    """One pass's synthesis: both artifacts rendered from the pass's composition.

    The real Synthesizer composes from the canonical snapshot it is handed, so
    a double that does the same exercises the whole composition boundary —
    the graph's quality pass and the finalizer's re-render — instead of
    handing state a report string with nothing typed behind it.
    """
    composition = fake_reader_composition(state)
    return {
        "report": render_reader_report(composition).strip(),
        "report_evidence": render_evidence_ledger(composition).strip(),
        "composition": composition,
    }


def fake_research_state(**overrides: object) -> ResearchState:
    """A bare research state for graph unit tests, ready for overrides.

    Only the identity fields the graph needs to exist are set; everything
    else takes the model defaults, and any field can be overridden by
    keyword. Shared by the state and node unit tests so their fixtures
    cannot drift from each other.
    """
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def progressing_fact_checker() -> FakeAgent:
    """A Fact Checker whose every pass adjudicates one more distinct claim.

    A run that stops changing anything now stops itself, which is the point of
    the no-progress rule. Tests about the iteration bound or about resuming a
    checkpoint are therefore about a run that *is* making progress, and this
    double is what makes one: each pass checks one new claim, so the assessed
    support grows and the loop has a reason to continue.
    """

    def update(state: ResearchState) -> ResearchStateUpdate:
        settled = fake_claim(
            f"Pass {state.iteration} settled one more fact."
        )
        return {"verified_claims": [*state.verified_claims, settled]}

    return FakeAgent("fact_checker", [], update_factory=update)


def fake_critique(*, should_continue: bool, score: int = 5) -> Critique:
    return Critique(
        score=score,
        gaps=["No cost data."] if should_continue else [],
        unsupported_claims=[],
        recommended_queries=["qec cost 2025"] if should_continue else [],
        should_continue=should_continue,
        rationale="Recorded for graph tests.",
    )


def fake_failed_critique() -> Critique:
    """The critique an exhausted repair records, as the graph sees it.

    Produced by the production constructor rather than hand-built, so the
    graph tests exercise the exact object ``failed_critique`` returns: score at
    the floor, no gaps, ``should_continue=False`` — every signal a clean
    acceptance carries, plus ``review_status='failed'``.
    """
    critique, _ = failed_critique(iteration=0, max_iterations=3)
    return critique


def fake_quality(*, hard_failures: Sequence[str] = ()) -> ReportQualitySnapshot:
    """A clean quality snapshot, optionally carrying named hard failures."""
    return ReportQualitySnapshot(
        coverage_ratio=1.0,
        planned_topics=2,
        covered_topics=2,
        unresolved_topic_ids=[],
        unique_findings=2,
        unique_sources=2,
        cited_sources=2,
        scored_cited_source_ratio=1.0,
        verified_claims=2,
        contradicted_claims=0,
        duplicate_claims=0,
        duplicate_source_rows=0,
        uncited_settled_points=0,
        hard_failures=list(hard_failures),
    )


def halting_error(node: str = "researcher") -> ResearchError:
    """One already-recorded graph halt, for tests that start from a dead run."""
    return ResearchError(
        error_type="graph_invalid_agent_state",
        source=f"graph.{node}",
        message="An agent returned a state update the research state rejected.",
        recoverable=False,
    )


class FakePublisher:
    """Record what the terminal finalizer asked to write, and where.

    One double for both the filesystem and long-term memory, so a graph test
    can assert *what* was published without a tool, a tracker, or a directory.
    ``fail_documents`` names the document writes that fail (matching on a
    substring, so a test can fail just the evidence ledger), and
    ``fail_claims`` fails every memory write — both the way a tool failure
    really arrives: as a failed result, never as a raised exception.

    ``report_writes`` counts document write *attempts* — reader plus evidence
    — and ``memory_writes`` counts claim writes, which is exactly the pair the
    observed report's regression pins: one terminal publication each, however
    many refinement passes ran before it.
    """

    def __init__(
        self,
        *,
        fail_documents: Sequence[str] = (),
        fail_claims: bool = False,
    ) -> None:
        self._fail_documents = tuple(fail_documents)
        self._fail_claims = fail_claims
        self.documents: list[tuple[str, str, bool]] = []
        self.claims: list[tuple[str, dict[str, JsonValue], bool]] = []

    @property
    def report_writes(self) -> int:
        return len(self.documents)

    @property
    def memory_writes(self) -> int:
        return len(self.claims)

    @property
    def written_paths(self) -> list[str]:
        """The document paths that were written, in publication order."""
        return [filename for filename, _, ok in self.documents if ok]

    @property
    def saved_claims(self) -> list[str]:
        return [content for content, _, ok in self.claims if ok]

    def document_named(self, fragment: str) -> tuple[str, str, bool]:
        matches = [row for row in self.documents if fragment in row[0]]
        assert len(matches) == 1, f"expected one write matching {fragment!r}"
        return matches[0]

    async def publish_document(self, *, filename: str, content: str) -> ToolResult:
        failed = any(
            fragment in filename for fragment in self._fail_documents
        )
        self.documents.append((filename, content, not failed))
        if failed:
            return ToolResult(
                tool_name="write_document",
                success=False,
                error=ToolError(
                    type="ValidationError",
                    message="The document could not be written.",
                ),
                latency_ms=0.0,
            )
        return ToolResult(
            tool_name="write_document",
            success=True,
            data={
                "path": filename,
                "bytes_written": len(content.encode("utf-8")),
            },
            latency_ms=0.0,
        )

    async def publish_claim(
        self,
        *,
        content: str,
        metadata: Mapping[str, JsonValue],
    ) -> ToolResult:
        saved = not self._fail_claims
        self.claims.append((content, dict(metadata), saved))
        if not saved:
            return ToolResult(
                tool_name="save_to_memory",
                success=False,
                error=ToolError(
                    type="RuntimeError",
                    message="The memory entry could not be saved.",
                ),
                latency_ms=0.0,
            )
        return ToolResult(
            tool_name="save_to_memory",
            success=True,
            data={"saved": True},
            latency_ms=0.0,
        )


def fake_report_review(
    *,
    status: str = "scored",
    dimensions: Mapping[str, float] | None = None,
    defects: Sequence[CritiqueGap] = (),
    dispositions: Mapping[str, str] | None = None,
    reviewed_statement_ids: Sequence[str] = ("S001",),
    fingerprint: str = "packet-1",
    composition_fingerprint: str = "composition-1",
) -> ReportReview:
    """A terminal semantic review, complete unless a test says otherwise.

    ``scored`` needs the seven dimensions, a fingerprint, and no unreviewed
    statement — the contract refuses anything less — so the default is a clean
    pass and every other shape is asked for explicitly. A test that wants "the
    reviewer refused the report" passes one material defect; a test that wants
    "no judgement exists" uses ``incomplete``.
    """
    payload: dict[str, object] = {
        "status": status,
        "dimensions": {},
        "defects": list(defects),
        "per_statement_dispositions": dict(dispositions or {}),
        "reviewed_statement_ids": [],
        "reviewed_batch_ids": ["batch-01"],
        "expected_batch_ids": ["batch-01"],
        "input_fingerprint": fingerprint,
        "composition_fingerprint": composition_fingerprint,
        "rubric_version": 2,
        "rationale": "Recorded for graph tests.",
    }
    if status == "scored":
        payload["dimensions"] = dict(
            dimensions
            if dimensions is not None
            else {name: 1.0 for name in REVIEW_DIMENSIONS}
        )
        payload["reviewed_statement_ids"] = list(reviewed_statement_ids)
        payload["per_statement_dispositions"] = dict(
            dispositions
            if dispositions is not None
            else {statement_id: "supported" for statement_id in reviewed_statement_ids}
        )
    return ReportReview.model_validate(payload)


def fake_rejected_report_review(
    *,
    kind: str = "missing_support",
    severity: str = "major",
    repair_action: str = "adjudicate",
    statement_ids: Sequence[str] = ("S001",),
    target_ids: Sequence[str] = ("t1",),
) -> ReportReview:
    """A scored review that judged the report and refused it."""
    return fake_report_review(
        dimensions={name: 0.5 for name in REVIEW_DIMENSIONS},
        defects=[
            CritiqueGap(
                gap_id="review-01",
                target_ids=list(target_ids),
                statement_ids=list(statement_ids),
                kind=kind,
                severity=severity,
                repair_action=repair_action,
                problem="The cited passage does not carry this statement.",
            )
        ],
        dispositions={statement_id: "unsupported" for statement_id in statement_ids},
        reviewed_statement_ids=statement_ids,
    )


class FakeReviewer:
    """Serve scripted semantic reviews instead of calling a provider.

    ``reviews`` is consumed one entry per call and the final entry repeats, the
    same contract ``FakeAgent`` uses. ``previous`` is honoured exactly as the
    production reviewer honours it — a scored review of the identical
    fingerprint is reused with no call — so a graph test can assert that a pass
    over unchanged content costs nothing.
    """

    def __init__(self, reviews: Sequence[ReportReview] = ()) -> None:
        self._reviews = list(reviews) or [fake_report_review()]
        self.packets: list[object] = []

    @property
    def calls(self) -> int:
        return len(self.packets)

    async def review(
        self,
        packet: object,
        *,
        previous: ReportReview | None = None,
    ) -> ReportReview:
        fingerprint = getattr(packet, "fingerprint", "")
        if (
            previous is not None
            and previous.status == "scored"
            and previous.input_fingerprint == fingerprint
        ):
            return previous
        self.packets.append(packet)
        position = min(len(self.packets) - 1, len(self._reviews) - 1)
        review = self._reviews[position]
        update: dict[str, object] = {"input_fingerprint": fingerprint}
        if review.status == "scored":
            # A scored review covers the packet it was handed — that is what
            # "scored" means — so the double records the same coverage the real
            # reviewer would, rather than leaving a scored review of nothing.
            batches = list(getattr(packet, "expected_batch_ids", []))
            update["expected_batch_ids"] = batches
            update["reviewed_batch_ids"] = batches
            # The composition fingerprint too, or the judgement is attached to
            # no report the merge can recognise: ``merge_research_state`` drops
            # a stored review whose fingerprint does not match the incoming
            # composition, so a double keeping its fixture value left every
            # compiled-graph run with no review on the state at all — an
            # accepted badge beside a ``partial`` status, and nothing asserting
            # that a review survives to publication.
            update["composition_fingerprint"] = getattr(
                packet, "composition_fingerprint", ""
            )
            reviewed = list(review.reviewed_statement_ids) or list(
                getattr(packet, "expected_statement_ids", [])
            )
            update["reviewed_statement_ids"] = reviewed
            # Reading is not judging: every statement the double reports as read
            # carries the disposition the real reviewer records for it, so the
            # review it serves is one the contract would accept rather than one
            # claiming a coverage it never judged.
            dispositions = dict(review.per_statement_dispositions)
            for statement_id in reviewed:
                dispositions.setdefault(statement_id, "supported")
            update["per_statement_dispositions"] = dispositions
            if not review.reviewed_evidence_ids:
                update["reviewed_evidence_ids"] = list(
                    getattr(packet, "evidence_ids", [])
                )
        return review.model_copy(update=update)


def fake_research_agents(**overrides: object) -> ResearchAgents:
    """A full set of agents whose default pass answers the question once.

    Every slot can be replaced by keyword, which is how a test scripts a
    critic that asks for a refinement or a fact checker that fails. The
    publisher defaults to a recording double so a graph test publishes into
    memory rather than nowhere.
    """
    defaults: dict[str, object] = {
        "planner": FakeAgent("planner", [{"sub_topics": [fake_sub_topic()]}]),
        "researcher": FakeAgent(
            "researcher", [{"raw_findings": [fake_finding()]}]
        ),
        "source_evaluator": FakeAgent(
            "source_evaluator", [{"evaluated_sources": [fake_scored_source()]}]
        ),
        "fact_checker": FakeAgent(
            "fact_checker", [{"verified_claims": [fake_claim()]}]
        ),
        "synthesizer": FakeAgent(
            "synthesizer", [{"report": "# Research report: pass 1"}]
        ),
        "critic": FakeAgent(
            "critic",
            [{"critique": fake_critique(should_continue=False, score=9)}],
        ),
        "publisher": FakePublisher(),
        "report_reviewer": FakeReviewer(),
    }
    defaults.update(overrides)
    return ResearchAgents(**defaults)
