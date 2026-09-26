"""Offline agent doubles and domain builders for graph tests.

No provider, no tracker, no scratchpad, no toolset: the node wrappers
depend on the ``ResearchAgent`` protocol (``name`` plus ``run``), so a
graph test never has to assemble a real agent — which would need an API
key this repository deliberately does not have.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from deep_research.agents.base import AgentRun
from deep_research.agents.evidence import build_read_record
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    QUALITY_STATUS_NOT_GATED,
    render_finding_log,
    render_written_report,
    report_as_of,
    report_scope,
)
from deep_research.agents.steps import ReActRun
from deep_research.agents.verified_facts import (
    answered_target_ids,
    fact_rows,
    not_found_targets,
)
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.tools.base import ToolError, ToolResult
from deep_research.utils.types import (
    REVIEW_DIMENSIONS,
    EvidenceTarget,
    FigureContext,
    FigureResult,
    Finding,
    FindingFigure,
    FindingVerification,
    ReadRecord,
    ReportComposition,
    ReportPoint,
    ReportQualitySnapshot,
    ReportReview,
    ReportStatement,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"
SOURCE_TITLE = "Battery storage in 2024"
FIGURE_VALUE = "10.4"
FIGURE_UNIT = "GW"
SNIPPET = (
    f"Example Lab measured that {FIGURE_VALUE} {FIGURE_UNIT} of battery "
    "storage capacity was added in 2024."
)
PAGE_TEXT = (
    "Battery storage additions. "
    + SNIPPET
    + " Analysts expect 19.6 GW to be added in 2025."
)


class FakeAgent:
    """Serve scripted state updates instead of running a real agent.

    ``updates`` is consumed one entry per call and the final entry repeats,
    which is how "a verifier that judges one pass and drops the next" is
    expressed without scripting every pass. A queued ``BaseException`` is
    raised instead of returned, which is how agent failures are simulated.

    ``update_factory``, when given, computes the update from the state the
    agent was handed and takes precedence over ``updates``. That is how a
    double models an agent whose output depends on the pass — a Report
    Writer composing from the verified findings the pass collected.
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


def fake_target(
    target_id: str = "topic-01-target-01",
    *,
    coverage_id: str = "topic-01",
    question: str = "What did battery storage additions reach?",
    required: bool = True,
) -> EvidenceTarget:
    """A qualitative required obligation; a verified finding naming it answers it."""
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=coverage_id,
        question=question,
        measure="stated in the recorded finding",
        required=required,
    )


def fake_sub_topic(
    title: str = "Error correction",
    *,
    coverage_id: str = "topic-01",
    priority: int = 1,
    targets: Sequence[EvidenceTarget] = (),
) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale="It is the bottleneck.",
        search_queries=["battery storage 2024"],
        success_criteria=["an addition is quoted"],
        priority=priority,
        evidence_targets=list(targets) or [fake_target(coverage_id=coverage_id)],
    )


def fake_read(
    *,
    url: str = SOURCE_URL,
    title: str = SOURCE_TITLE,
    text: str = PAGE_TEXT,
) -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at="2026-08-01T11:59:00+00:00",
        text=text,
        passages={"page-1-chunk-0": text},
    )


def fake_finding(
    read: ReadRecord,
    *,
    snippet: str = SNIPPET,
    target_ids: Sequence[str] = ("topic-01-target-01",),
    context_unchecked: bool = False,
) -> Finding:
    """A verified figure-bearing finding whose snippet states its own figure.

    The verification is attached here rather than left to a producer because
    every consumer of ``verified_findings`` — the writer's registry, the
    quality gates, the finalizer's memory writes — reads it.
    """
    figure = FindingFigure(
        value=FIGURE_VALUE, unit=FIGURE_UNIT, period="2024", kind="actual"
    )
    return Finding(
        content=snippet,
        source_url=read.resolved_url,
        source_title=read.title,
        extracted_at=EXTRACTED_AT,
        confidence=0.9,
        related_sub_topic="Error correction",
        snippet=snippet,
        read_id=read.read_id,
        locator="page-1-chunk-0",
        figures=[figure],
        target_ids=list(target_ids),
        verification=FindingVerification(
            status="verified",
            figure_results=[
                FigureResult(
                    figure=figure,
                    matched=True,
                    context=FigureContext(
                        period="2024",
                        scope="utility-scale",
                        attribution="own",
                        organisation="Example Lab",
                        kind="actual",
                    ),
                )
            ],
            context_unchecked=context_unchecked,
        ),
    )


@dataclass(frozen=True, slots=True)
class VerifiedPass:
    """One pass's verified evidence: a read and the finding it backs."""

    read: ReadRecord
    finding: Finding

    def update(self) -> ResearchStateUpdate:
        """What the Researcher's pass records: the finding and the page it read.

        The finding is *raw* here — the Evidence Verifier is the agent that
        stamps a verification, so a double that verified it for the researcher
        would hide exactly the boundary those two nodes were split on.
        """
        return {
            "raw_findings": [self.finding],
            "read_records": {self.read.read_id: self.read},
        }

    def verified_update(self) -> ResearchStateUpdate:
        """What the Evidence Verifier's pass records: the verified snapshot."""
        return {"verified_findings": [self.finding]}


def verified_pass(**overrides: object) -> VerifiedPass:
    """A default verified pass: one read, one finding that answers topic-01."""
    read = fake_read(**{  # type: ignore[arg-type]
        key: value for key, value in overrides.items() if key in {"url", "title", "text"}
    })
    finding = fake_finding(
        read,
        **{  # type: ignore[arg-type]
            key: value
            for key, value in overrides.items()
            if key in {"snippet", "target_ids", "context_unchecked"}
        },
    )
    return VerifiedPass(read=read, finding=finding)


def fake_scored_source(url: str = SOURCE_URL) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=SOURCE_TITLE,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )


def fake_writer_composition(
    state: ResearchState,
    *,
    quality_status: str = QUALITY_STATUS_NOT_GATED,
) -> ReportComposition:
    """The composition one writer pass renders from the verified snapshot.

    Built from the state it is handed, so every point carries the exact
    finding identity the state already holds: that is what makes the
    deterministic quality gates *pass* rather than merely exist, so a test
    asserting a clean snapshot is asserting the real gate list.
    """
    findings = list(state.verified_findings)
    targets = [target for topic in state.sub_topics for target in topic.evidence_targets]
    answered = answered_target_ids(findings, targets)
    points: list[ReportPoint] = []
    labels: dict[str, str] = {}
    verdicts: dict[str, str] = {}
    for number, finding in enumerate(findings, start=1):
        finding_id = finding_fingerprint(finding)
        labels[f"F{number:02d}"] = finding_id
        statement_id = f"S{number:03d}"
        statement = ReportStatement(
            statement_id=statement_id,
            text=finding.snippet or finding.content,
            target_ids=list(finding.target_ids),
            finding_ids=[finding_id],
        )
        verdicts[statement_id] = "consistent"
        points.append(
            ReportPoint(
                text=statement.text,
                source_urls=[finding.source_url],
                statement=statement,
            )
        )
    return ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        iteration=state.iteration,
        max_extra_passes=state.max_extra_passes,
        as_of=report_as_of(
            findings=findings, reads=list(state.read_records.values())
        ),
        scope=report_scope(state.sub_topics),
        quality_status=quality_status,
        sub_topics=list(state.sub_topics),
        sources=list(state.evaluated_sources),
        findings=findings,
        fact_rows=fact_rows(findings, targets),
        not_found=not_found_targets(
            state.sub_topics, answered, state.acquisition_state_by_target
        ),
        finding_labels=labels,
        statement_verdicts=verdicts,
        summary=points,
        generated_on="2026-08-01",
    )


def fake_writer_update(state: ResearchState) -> ResearchStateUpdate:
    """One pass's writing: both artifacts rendered from the pass's composition.

    The real Report Writer composes from the verified snapshot it is handed,
    so a double that does the same exercises the whole composition boundary —
    the graph's quality pass and the finalizer's re-render — instead of
    handing state a report string with nothing typed behind it.
    """
    composition = fake_writer_composition(state)
    return {
        "report": render_written_report(composition),
        "report_evidence": render_finding_log(composition),
        "composition": composition,
        "unique_source_count": len(
            {finding.source_url for finding in state.verified_findings}
        ),
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


def fake_quality(*, hard_failures: Sequence[str] = ()) -> ReportQualitySnapshot:
    """A clean quality snapshot, optionally carrying named hard failures."""
    return ReportQualitySnapshot(hard_failures=list(hard_failures))


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
    ``fail_findings`` fails every memory write — both the way a tool failure
    really arrives: as a failed result, never as a raised exception.

    ``report_writes`` counts document write *attempts* — reader, evidence log
    and quality record — and ``memory_writes`` counts finding writes, which is
    the pair the observed report's regression pins: one terminal publication
    each, however many extra passes ran before it.
    """

    def __init__(
        self,
        *,
        fail_documents: Sequence[str] = (),
        fail_findings: bool = False,
    ) -> None:
        self._fail_documents = tuple(fail_documents)
        self._fail_findings = fail_findings
        self.documents: list[tuple[str, str, bool]] = []
        self.findings: list[tuple[str, dict[str, JsonValue], bool]] = []

    @property
    def report_writes(self) -> int:
        return len(self.documents)

    @property
    def memory_writes(self) -> int:
        return len(self.findings)

    @property
    def written_paths(self) -> list[str]:
        """The document paths that were written, in publication order."""
        return [filename for filename, _, ok in self.documents if ok]

    @property
    def saved_findings(self) -> list[str]:
        return [content for content, _, ok in self.findings if ok]

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

    async def publish_finding(
        self,
        *,
        content: str,
        metadata: Mapping[str, JsonValue],
    ) -> ToolResult:
        saved = not self._fail_findings
        self.findings.append((content, dict(metadata), saved))
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
    defects: Sequence[object] = (),
    dispositions: Mapping[str, str] | None = None,
    reviewed_statement_ids: Sequence[str] = ("S001",),
    missing_required_target_ids: Sequence[str] = (),
    fingerprint: str = "packet-1",
    composition_fingerprint: str = "composition-1",
) -> ReportReview:
    """A terminal review, complete unless a test says otherwise.

    ``scored`` needs the seven dimensions, a fingerprint, and no unreviewed
    statement — the contract refuses anything less — so the default is a clean
    pass and every other shape is asked for explicitly. ``missing_required_
    target_ids`` is normally stamped by the graph from the quality snapshot,
    never produced by the reviewer; a test may set it to model what the node
    will write.
    """
    payload: dict[str, object] = {
        "status": status,
        "dimensions": {},
        "defects": list(defects),
        "per_statement_dispositions": dict(dispositions or {}),
        "reviewed_statement_ids": [],
        "missing_required_target_ids": list(missing_required_target_ids),
        "input_fingerprint": fingerprint,
        "composition_fingerprint": composition_fingerprint,
        "rubric_version": 2,
        "rationale": "Recorded for graph tests.",
    }
    if status == "scored":
        payload["dimensions"] = dict(
            dimensions
            if dimensions is not None
            else {name: 0.9 for name in REVIEW_DIMENSIONS}
        )
        payload["reviewed_statement_ids"] = list(reviewed_statement_ids)
        payload["per_statement_dispositions"] = dict(
            dispositions
            if dispositions is not None
            else {statement_id: "supported" for statement_id in reviewed_statement_ids}
        )
    return ReportReview.model_validate(payload)


class FakeReviewer:
    """Serve scripted reviews instead of calling a provider.

    ``reviews`` is consumed one entry per call and the final entry repeats, the
    same contract ``FakeAgent`` uses. ``previous`` is honoured exactly as the
    production reviewer honours it — a scored review of the identical
    fingerprint is reused with no call — so a graph test can assert that a pass
    over unchanged content costs nothing.
    """

    def __init__(
        self,
        reviews: Sequence[ReportReview | BaseException] = (),
        *,
        records: Sequence[tuple[ResearchError, ...]] = (),
    ) -> None:
        self._reviews = list(reviews) or [fake_report_review()]
        self._records = list(records)
        self.packets: list[object] = []
        self.review_records: tuple[ResearchError, ...] = ()

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
        self.review_records = (
            self._records[min(position, len(self._records) - 1)] if self._records else ()
        )
        review = self._reviews[position]
        if isinstance(review, BaseException):
            raise review
        update: dict[str, object] = {"input_fingerprint": fingerprint}
        if review.status == "scored":
            # The composition fingerprint travels with every scored review, or
            # the judgement is attached to
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
        return review.model_copy(update=update)

    async def review_scoped(self, scoped: object) -> ReportReview:
        """Serve the next scripted review for a scoped re-review call.

        Mirrors ``.review()``'s bookkeeping over ``scoped.base`` (the packet a
        full review of the same content would build), since that is what the
        merged record is stamped against (T5 addendum). A queued
        ``BaseException`` is raised instead of returned, the same contract
        ``.review()`` and ``FakeAgent`` honour, so a graph test can script a
        scoped call that fails outright (P2: the fallback-to-full-review
        gate).
        """
        base = getattr(scoped, "base", scoped)
        fingerprint = getattr(base, "fingerprint", "")
        self.packets.append(scoped)
        position = min(len(self.packets) - 1, len(self._reviews) - 1)
        self.review_records = (
            self._records[min(position, len(self._records) - 1)] if self._records else ()
        )
        review = self._reviews[position]
        if isinstance(review, BaseException):
            raise review
        update: dict[str, object] = {"input_fingerprint": fingerprint}
        if review.status == "scored":
            update["composition_fingerprint"] = getattr(
                base, "composition_fingerprint", ""
            )
            reviewed = list(review.reviewed_statement_ids) or list(
                getattr(base, "expected_statement_ids", [])
            )
            update["reviewed_statement_ids"] = reviewed
            dispositions = dict(review.per_statement_dispositions)
            for statement_id in reviewed:
                dispositions.setdefault(statement_id, "supported")
            update["per_statement_dispositions"] = dispositions
        return review.model_copy(update=update)


def fake_research_agents(**overrides: object) -> ResearchAgents:
    """A full set of agents whose default pass answers the question once.

    Every slot can be replaced by keyword, which is how a test scripts a
    verifier that drops a finding or a reviewer that refuses the report. The
    publisher defaults to a recording double so a graph test publishes into
    memory rather than nowhere.
    """
    one = verified_pass()
    defaults: dict[str, object] = {
        "planner": FakeAgent(
            "planner",
            [{"sub_topics": [fake_sub_topic(targets=[fake_target()])]}],
        ),
        "researcher": FakeAgent("researcher", [one.update()]),
        "source_evaluator": FakeAgent(
            "source_evaluator", [{"evaluated_sources": [fake_scored_source()]}]
        ),
        "evidence_verifier": FakeAgent(
            "evidence_verifier", [one.verified_update()]
        ),
        "report_writer": FakeAgent(
            "report_writer", [], update_factory=fake_writer_update
        ),
        "publisher": FakePublisher(),
        "report_reviewer": FakeReviewer(
            [fake_report_review(reviewed_statement_ids=("S001",))]
        ),
    }
    defaults.update(overrides)
    return ResearchAgents(**defaults)
