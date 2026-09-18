"""Mocked CLI acceptance: the recorded report pathologies are gone.

This file is the permanent guard for the plan that began at one committed live
CLI artifact, ``docs/reports/cli-run-2026-09-13-grid-scale-battery-storage-
854cddd3.md``. Its review section — ``docs/superpowers/plans/2026-09-14-cli-
report-quality-and-agent-output-integrity.md``, "Review result this plan must
correct" — recorded five structural pathologies:

    duplicate URL rows                     156 -> 0
    false numeric scores for unscored      221 -> 0
    unused sources in reader references     89 -> 0
    critic-visible required sections       2/7 -> 7/7
    duplicate claim IDs                    > 0 -> 0

The "before" column is that artifact's own measured shape: 257 appendix rows
for 101 canonical URLs (156 duplicates), 221 rows printed as score ``0.20`` /
confidence ``low`` although only 12 citation numbers were used before the
citations section, and a Critic review that received only the first 6,000 report
characters, excluding the verified-claims, uncertainty, limitations, citations
and source-appendix surfaces.

Nothing here is pasted. The fixture is typed domain records; both artifacts are
produced by the production renderers through the production terminal finalizer;
the quality snapshot is the production quality pass; the run is driven through
the real ``deep_research.cli.main`` entry point with a scripted runner; and the
Critic view is the production ``critique_messages`` request built from the
production ``CriticAgent.build_task``. No provider, network call, API
credential, or live tier is touched, and every host in the fixture sits under
the reserved ``.example`` TLD.

Every number asserted is read from a typed object or from the published
Markdown, and each metric's surfaces are compared to each other. The only
hand-written constants are the pathology targets ``0`` and ``7/7`` above.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from deep_research.agents.critic import (
    _READER_SECTION_ORDER,
    _SECTION_NOT_PRESENT,
    CRITIC_REPORT_CHARS,
    CriticAgent,
    critique_messages,
)
from deep_research.agents.events import agent_event
from deep_research.agents.identity import (
    claim_fingerprint,
    finding_fingerprint,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    _CELL_EMPTY,
    EVIDENCE_SECTIONS,
    REPORT_SECTIONS,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    reader_citations,
    render_evidence_ledger,
    render_reader_report,
    report_as_of,
    report_scope,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActRun
from deep_research.cli import EXIT_OK, is_streamed_event, render_progress
from deep_research.cli import main as cli_main
from deep_research.graph.events import (
    node_completed_event,
    node_started_event,
    route_decided_event,
    session_started_event,
)
from deep_research.graph.nodes import finalize_report_node
from deep_research.graph.orchestrator import GraphRun
from deep_research.graph.state import dump_state, graph_quality_status, load_state
from deep_research.main import DEFAULT_CONFIG_PATH
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import build_outcome
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Claim,
    Critique,
    EvidencePassage,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import critic_tools

BATTERY_QUESTION = (
    "What are the current constraints on grid-scale battery storage deployment?"
)
SESSION_ID = "cli-acceptance-battery-storage"
FIXED_TIMESTAMP = "2026-09-13T09:00:00+00:00"
MAX_ITERATIONS = 3

# The five constraint mechanisms the recorded plan could not separate. The same
# contract example lives in ``tests/test_agents/test_planner.py``; the
# production planner holds no taxonomy of its own.
_MECHANISMS: tuple[tuple[str, str, int, str], ...] = (
    (
        "topic-01",
        "Grid connection and interconnection queue position",
        2,
        "FERC interconnection queue storage wait times 2026",
    ),
    (
        "topic-02",
        "Equipment supply chain and trade exposure",
        3,
        "battery cell supply chain tariffs 2026 United States",
    ),
    (
        "topic-03",
        "Siting, permitting, and fire safety rules",
        4,
        "NFPA 855 UL 9540A local siting permit requirements 2026",
    ),
    (
        "topic-04",
        "Wholesale market rules and storage compensation",
        5,
        "FERC Order 841 storage market participation 2026",
    ),
    (
        "topic-05",
        "Project economics and financing",
        1,
        "grid-scale battery storage levelized cost financing 2026",
    ),
)
_COVERAGE_IDS: tuple[str, ...] = tuple(row[0] for row in _MECHANISMS)
_TOPIC_TITLE: dict[str, str] = {row[0]: row[1] for row in _MECHANISMS}

# One origin source and one independently published verifier per topic.
_ORIGINS: dict[str, tuple[str, str, float, float, float]] = {
    "topic-01": (
        "https://ferc.example/interconnection-queue/2026",
        "FERC interconnection queue report, 2026",
        0.95,
        0.92,
        0.94,
    ),
    "topic-02": (
        "https://iea.example/minerals-outlook/2026",
        "IEA critical minerals outlook, 2026",
        0.96,
        0.95,
        0.90,
    ),
    "topic-03": (
        "https://nfpa.example/855-siting/2026",
        "NFPA 855 storage siting standard, 2026",
        0.93,
        0.90,
        0.95,
    ),
    "topic-04": (
        "https://iso.example/order-841-compensation/2026",
        "ISO storage compensation filing, 2026",
        0.90,
        0.88,
        0.92,
    ),
    "topic-05": (
        "https://lender.example/lcoe-financing/2026",
        "Lender cost and financing survey, 2026",
        0.88,
        0.91,
        0.90,
    ),
}
_VERIFIERS: dict[str, tuple[str, str, float, float, float]] = {
    "topic-01": (
        "https://lbnl.example/queue-review/2026",
        "Independent review of United States interconnection queues",
        0.85,
        0.86,
        0.88,
    ),
    "topic-02": (
        "https://usgs.example/minerals-review/2026",
        "Independent review of battery mineral supply",
        0.87,
        0.84,
        0.86,
    ),
    "topic-03": (
        "https://ul.example/9540a-review/2026",
        "Independent review of storage fire-safety testing",
        0.89,
        0.83,
        0.90,
    ),
    "topic-04": (
        "https://nerc.example/market-review/2026",
        "Independent review of storage market compensation",
        0.86,
        0.87,
        0.85,
    ),
    "topic-05": (
        "https://utility.example/cost-review/2026",
        "Independent review of storage cost and financing terms",
        0.84,
        0.88,
        0.86,
    ),
}
# Reviewed, deliberately never scored, and never relied on by a reader point.
# These are the records the recorded artifact printed as ``0.20`` / ``low``.
_UNSCORED: tuple[tuple[str, str, str], ...] = (
    (
        "https://developer.example/financing-notes/2026",
        "Developer financing notes, 2026",
        "unscored_cap",
    ),
    (
        "https://standards.example/fire-code-digest/2026",
        "Local fire-code digest, 2026",
        "unscored_cap",
    ),
    (
        "https://consultancy.example/queue-analysis/2026",
        "Consultancy queue analysis, 2026",
        "unscored_provider",
    ),
)

_CLAIM_TEXT: dict[str, str] = {
    "topic-01": (
        "Interconnection study backlogs, not equipment cost, set the binding "
        "schedule for most 2026 United States grid-scale storage projects, and "
        "the queue reform filings that began in 2023 have not yet shortened "
        "measured study durations."
    ),
    "topic-02": (
        "Cell and inverter lead times in the United States track tariff "
        "exposure and mineral processing concentration rather than domestic "
        "manufacturing capacity in 2026."
    ),
    "topic-03": (
        "Local fire-code adoption, not the national standard alone, decides "
        "whether a 2026 storage project can be permitted on its chosen site, "
        "and authorities having jurisdiction differ within one state."
    ),
    "topic-04": (
        "Storage compensation in United States wholesale markets still "
        "underprices fast frequency response in 2026, so a project that "
        "provides it is paid for energy rather than for the service."
    ),
    "topic-05": (
        "Financing terms, not cell prices, are the largest single swing factor "
        "in 2026 United States storage project economics, because transferable "
        "tax credits moved value from equipment to debt pricing."
    ),
    "topic-05-contradicted": (
        "Storage project economics in 2026 are driven primarily by cell prices "
        "rather than by financing terms."
    ),
}
# A second and third settled claim per topic: a real report carries more than
# one point per planned sub-topic, and the recorded artifact's problem was not
# too many points but points that cited nothing.
_DETAIL_TEMPLATES: tuple[str, ...] = (
    (
        "The {mechanism} constraint is documented for {geography} by a filing "
        "that states its own as-of date, which bounds how current this finding "
        "is for a 2026 decision."
    ),
    (
        "Public authorities in {geography} apply the {mechanism} requirement "
        "unevenly, so two projects in the same year can face materially "
        "different conditions."
    ),
)

_MECHANISM_LABEL: dict[str, str] = {
    "topic-01": "interconnection queue study and network upgrade",
    "topic-02": "import tariff and critical-mineral processing",
    "topic-03": "local fire-code adoption and permitting",
    "topic-04": "wholesale market eligibility and accreditation",
    "topic-05": "tax-credit transferability and debt pricing",
}
_GEOGRAPHY: dict[str, str] = {
    "topic-01": "the United States",
    "topic-02": "the United States and China",
    "topic-03": "state and county authorities in the United States",
    "topic-04": "ERCOT and PJM",
    "topic-05": "the United States",
}


def _detail_text(coverage_id: str, index: int) -> str:
    return _DETAIL_TEMPLATES[index].format(
        mechanism=_MECHANISM_LABEL[coverage_id],
        geography=_GEOGRAPHY[coverage_id],
    )


# --- the fixture, as the producers emit it ------------------------------------


def _topics() -> list[SubTopic]:
    return [
        SubTopic(
            coverage_id=coverage_id,
            title=title,
            rationale=f"{title} changes the decision surface.",
            search_queries=[query],
            success_criteria=[f"A read primary source answers {title}."],
            priority=priority,
        )
        for coverage_id, title, priority, query in _MECHANISMS
    ]


def _scored(
    entry: tuple[str, str, float, float, float],
    *,
    authority_delta: float = 0.0,
) -> ScoredSource:
    url, title, authority, recency, relevance = entry
    authority = round(min(1.0, authority + authority_delta), 4)
    return ScoredSource(
        url=url,
        title=title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        overall_score=round((authority + recency + relevance) / 3, 4),
        rationale="Primary material with a stated publication date.",
    )


def _unscored(entry: tuple[str, str, str]) -> ScoredSource:
    """A reviewed source that carries a status and no quality judgement."""
    url, title, status = entry
    return ScoredSource(
        url=url,
        title=title,
        rationale="No quality judgement was made for this source.",
        evaluation_status=status,  # type: ignore[arg-type]
    )


def _finding(coverage_id: str, *, content_suffix: str = "") -> Finding:
    url, title, *_ = _ORIGINS[coverage_id]
    return Finding(
        content=(
            "Read primary material measures the constraint in "
            f"{_TOPIC_TITLE[coverage_id].casefold()}{content_suffix}."
        ),
        source_url=url,
        source_title=title,
        extracted_at=FIXED_TIMESTAMP,
        confidence=0.9,
        related_sub_topic=_TOPIC_TITLE[coverage_id],
    )


def _passage(coverage_id: str, *, locator: str, contradicts: bool) -> EvidencePassage:
    verifier_url, verifier_title, *_ = _VERIFIERS[coverage_id]
    return EvidencePassage(
        source_url=verifier_url,
        source_title=verifier_title,
        locator=locator,
        excerpt=(
            "The independent review measures the same constraint the origin "
            "source reports."
            if not contradicts
            else "The independent review attributes the change to other terms."
        ),
        stance="contradicts" if contradicts else "supports",
    )


def _verified_claim(
    coverage_id: str,
    *,
    confidence_delta: float = 0.0,
) -> Claim:
    text = _CLAIM_TEXT[coverage_id]
    passage = _passage(
        coverage_id, locator="section 3, findings table", contradicts=False
    )
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[_ORIGINS[coverage_id][0]],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=round(min(1.0, 0.86 + confidence_delta), 4),
        evidence=[passage.excerpt],
        contradictions=[],
        verification_evidence=[passage],
        consumed_finding_fingerprints=[
            finding_fingerprint(_finding(coverage_id))
        ],
        consumed_coverage_ids=[coverage_id],
    )


def _detail_claim(
    coverage_id: str,
    index: int,
    *,
    confidence_delta: float = 0.0,
) -> Claim:
    text = _detail_text(coverage_id, index)
    passage = _passage(
        coverage_id,
        locator=f"section {index + 5}, supporting table",
        contradicts=False,
    )
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[_ORIGINS[coverage_id][0]],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=round(min(1.0, 0.78 + confidence_delta), 4),
        evidence=[passage.excerpt],
        contradictions=[],
        verification_evidence=[passage],
        consumed_finding_fingerprints=[
            finding_fingerprint(_finding(coverage_id))
        ],
        consumed_coverage_ids=[coverage_id],
    )


def _contradicted_claim() -> Claim:
    text = _CLAIM_TEXT["topic-05-contradicted"]
    passage = _passage(
        "topic-05", locator="section 4, cost model", contradicts=True
    )
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[_ORIGINS["topic-05"][0]],
        verdict="contradicted",
        evidence_status="verified_pair",
        confidence=0.55,
        evidence=[],
        contradictions=[passage.excerpt],
        verification_evidence=[passage],
    )


@dataclass(frozen=True, slots=True)
class _Pass:
    """One research pass's re-emitted, un-canonicalized evidence."""

    iteration: int
    sources: tuple[ScoredSource, ...]
    claims: tuple[Claim, ...]
    findings: tuple[Finding, ...]


def _first_pass() -> _Pass:
    """Four topics evaluated; a source past the cap is reviewed, not judged."""
    covered = ("topic-01", "topic-02", "topic-03", "topic-04")
    return _Pass(
        iteration=0,
        sources=tuple(
            [
                *(_scored(_ORIGINS[cid]) for cid in covered),
                *(_scored(_VERIFIERS[cid]) for cid in covered),
                _unscored(_UNSCORED[0]),
            ]
        ),
        claims=tuple(
            [
                *(_verified_claim(cid) for cid in covered),
                *(_detail_claim(cid, 0) for cid in covered),
            ]
        ),
        findings=tuple(_finding(cid) for cid in covered),
    )


def _second_pass() -> _Pass:
    """The refinement pass: it re-emits every record with fresh numbers.

    This is the recorded pathology's input shape — every pass appended another
    copy of every source assessment and every fact, with different scores and
    confidences. Canonicalization is what collapses it, and the acceptance
    asserts the raw input really did carry the duplicates.
    """
    return _Pass(
        iteration=1,
        sources=tuple(
            [
                *(_scored(_ORIGINS[cid], authority_delta=0.01)
                  for cid in _COVERAGE_IDS),
                *(_scored(_VERIFIERS[cid], authority_delta=0.01)
                  for cid in _COVERAGE_IDS),
                *(_unscored(entry) for entry in _UNSCORED),
            ]
        ),
        claims=tuple(
            [
                *(_verified_claim(cid, confidence_delta=0.02)
                  for cid in _COVERAGE_IDS),
                *(
                    _detail_claim(cid, index, confidence_delta=0.02)
                    for cid in _COVERAGE_IDS
                    for index in range(len(_DETAIL_TEMPLATES))
                ),
                _contradicted_claim(),
            ]
        ),
        findings=tuple(_finding(cid) for cid in _COVERAGE_IDS),
    )


def _unchecked_finding() -> Finding:
    """A finding no claim consumed, so the ledger must show it as open."""
    return _finding("topic-03", content_suffix=", at a site the plan never named")


# --- fixture assembly, through production helpers -----------------------------


def _reader_points(composition: ReportComposition) -> list[ReportPoint]:
    return [
        *composition.summary,
        *composition.constraints,
        *(point for section in composition.sections for point in section.points),
    ]


def _composition(
    *,
    state: ResearchState,
    sub_topics: Sequence[SubTopic],
    sources: Sequence[ScoredSource],
    claims: Sequence[Claim],
    findings: Sequence[Finding],
    events: Sequence[Any],
) -> ReportComposition:
    """Build the reader composition the way a synthesis pass does.

    Points are built from the *canonical* claims, so a caller that hands in the
    un-merged multi-pass lists still describes each topic once, and a caller
    that omits a claim never cites it.
    """
    merged = merge_claim_snapshot([], claims)
    by_fingerprint = {claim_fingerprint(claim.text): claim for claim in merged}

    def find(text: str) -> Claim | None:
        claim = by_fingerprint.get(claim_fingerprint(text))
        if claim is None or claim.verdict != "verified":
            return None
        return claim

    primary = {
        coverage_id: claim
        for coverage_id in _COVERAGE_IDS
        if (claim := find(_CLAIM_TEXT[coverage_id])) is not None
    }

    def point(claim: Claim) -> ReportPoint:
        return ReportPoint(
            text=claim.text,
            claim_ids=[claim.claim_id],
            source_urls=list(claim.source_urls),
        )

    def topic_points(coverage_id: str) -> list[ReportPoint]:
        points: list[ReportPoint] = []
        if coverage_id in primary:
            points.append(point(primary[coverage_id]))
        points.extend(
            point(claim)
            for index in range(len(_DETAIL_TEMPLATES))
            if (claim := find(_detail_text(coverage_id, index))) is not None
        )
        return points

    return ReportComposition(
        question=BATTERY_QUESTION,
        session_id=state.session_id,
        iteration=state.iteration,
        max_iterations=state.max_iterations,
        as_of=report_as_of(findings=findings, events=events),
        scope=report_scope(sub_topics),
        sub_topics=list(sub_topics),
        claims=list(claims),
        sources=list(sources),
        findings=list(findings),
        limitations=["errors_recorded"],
        errors=list(state.errors),
        summary=[
            point(primary[coverage_id])
            for coverage_id in ("topic-01", "topic-02", "topic-05")
            if coverage_id in primary
        ],
        constraints=[
            ReportConstraint(
                text=f"{coverage_id} constrains deployment.",
                claim_ids=[primary[coverage_id].claim_id],
                source_urls=list(primary[coverage_id].source_urls),
                deployment_mechanism=_MECHANISM_LABEL[coverage_id],
                geography=_GEOGRAPHY[coverage_id],
            )
            for coverage_id in _COVERAGE_IDS
            if coverage_id in primary
        ],
        sections=[
            ReportSection(
                title=_TOPIC_TITLE[coverage_id],
                points=topic_points(coverage_id),
            )
            for coverage_id in _COVERAGE_IDS
            if topic_points(coverage_id)
        ],
        uncertainty_notes=[
            "Three reviewed sources carry a status rather than a quality "
            "judgement, so no settled point relies on them.",
            "One checked claim was contradicted by an independent source and "
            "is reported as such rather than as a settled finding.",
        ],
        rejected=[
            "A drafted paragraph that narrated an unchecked finding was "
            "refused so no reader point rests on unreviewed evidence."
        ],
    )


@dataclass(frozen=True, slots=True)
class _Fixture:
    """The canonical state, plus the raw input hazards it was built from."""

    state: ResearchState
    raw_source_records: int
    raw_claim_records: int


def _fixture() -> _Fixture:
    """Assemble the canonical state exactly as the producers would."""
    sub_topics = _topics()
    first, second = _first_pass(), _second_pass()
    raw_sources = [*first.sources, *second.sources]
    raw_claims = [*first.claims, *second.claims]
    raw_findings = [*first.findings, *second.findings, _unchecked_finding()]
    # The producers' own merge helpers: ``evaluated_sources`` and
    # ``verified_claims`` each carry the complete canonical snapshot.
    sources = merge_source_snapshot([], raw_sources)
    claims = merge_claim_snapshot([], raw_claims)

    events = [
        session_started_event(
            session_id=SESSION_ID,
            max_iterations=MAX_ITERATIONS,
            checkpointing=False,
        ),
        node_started_event("synthesizer", iteration=1),
        # A node completion and a tool-call record: neither is a member of
        # PROGRESS_EVENT_TYPES, so both exercise the *negative* half of the
        # stdout guard below. Without at least one non-streamed event that loop
        # `continue`d on every iteration and asserted nothing.
        node_completed_event(
            "synthesizer", iteration=1, event_count=2, error_count=0
        ),
        agent_event(
            agent_name="researcher",
            event_type="researcher.tool.completed",
            message="web_search returned 4 results.",
            metadata={"tool_name": "web_search", "success": True},
        ),
        route_decided_event(
            destination="finalize",
            reason="critique_satisfied",
            iteration=1,
            max_iterations=MAX_ITERATIONS,
            should_continue=False,
        ),
    ]
    state = ResearchState(
        session_id=SESSION_ID,
        original_question=BATTERY_QUESTION,
        sub_topics=sub_topics,
        raw_findings=raw_findings,
        evaluated_sources=sources,
        verified_claims=claims,
        iteration=1,
        max_iterations=MAX_ITERATIONS,
        critique=Critique(
            score=8,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=False,
            rationale=(
                "Every planned constraint family has a claim-linked point."
            ),
        ),
        errors=[
            ResearchError(
                error_type="researcher_topic_attempt_skipped",
                source="agents.researcher",
                message="One planned query timed out before it was attempted.",
                details={"coverage_id": "topic-03"},
            )
        ],
        events=events,
    )
    composition = _composition(
        state=state,
        sub_topics=sub_topics,
        sources=sources,
        claims=claims,
        findings=raw_findings,
        events=events,
    )
    state = state.model_copy(
        update={
            "composition": composition,
            "report": render_reader_report(composition),
            "report_evidence": render_evidence_ledger(composition),
            "unique_source_count": len(composition.sources),
            "unique_claim_count": len(composition.claims),
        }
    )
    state = state.model_copy(
        update={"quality": compute_report_quality(state, composition)}
    )
    return _Fixture(
        state=state,
        raw_source_records=len(raw_sources),
        raw_claim_records=len(raw_claims),
    )


@dataclass
class _LocalPublisher:
    """The production ``ReportPublisher`` protocol, writing under ``tmp_path``."""

    directory: Path
    documents: list[Path] = field(default_factory=list)
    memory_claims: int = 0

    async def publish_document(self, *, filename: str, content: str) -> ToolResult:
        target = self.directory / Path(filename).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.documents.append(target)
        return ToolResult(
            tool_name="write_document",
            success=True,
            data={
                "path": str(target),
                "bytes_written": len(content.encode("utf-8")),
            },
            latency_ms=0.0,
            metadata={"acceptance": True},
        )

    async def publish_claim(self, *, content: str, metadata: Any) -> ToolResult:
        del content, metadata
        self.memory_claims += 1
        return ToolResult(
            tool_name="save_to_memory",
            success=True,
            data={"saved": True},
            latency_ms=0.0,
            metadata={"acceptance": True},
        )


@dataclass
class _ScriptedRunner:
    """The CLI's ``runner``: stream the run's events, then return its outcome."""

    state: ResearchState
    calls: list[dict[str, Any]] = field(default_factory=list)
    streamed: list[str] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        handler = kwargs.get("event_handler")
        for event in self.state.events:
            self.streamed.append(event.message)
            if handler is not None:
                handler(event)
        return build_outcome(
            GraphRun(
                session_id=self.state.session_id,
                state=self.state,
                status="completed",
                trace_url=None,
            ),
            metrics=(),
        )


# --- artifact readers ---------------------------------------------------------


def _section(markdown: str, heading: str, *, end: str = "\n## ") -> str:
    start = markdown.index(heading)
    rest = markdown[start + len(heading) :]
    stop = rest.find(end)
    return rest if stop == -1 else rest[:stop]


def _table_rows(section: str) -> list[list[str]]:
    rows = [line for line in section.splitlines() if line.startswith("|")]
    assert len(rows) >= 2, "expected a Markdown table"
    return [
        [cell.strip() for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]
        for row in rows[2:]
    ]


def _row_url(cell: str) -> str:
    match = re.search(r"\((https?://[^)]+)\)\s*$", cell)
    assert match is not None, f"no source URL in table cell: {cell!r}"
    return match.group(1)


_REFERENCE = re.compile(r"^(?P<number>\d+)\.\s+(?P<title>.*?)\s+—\s+(?P<url>\S+)$")
_BULLET = re.compile(r"^- (?!\*\*)(?P<body>.+)$")
_NUMBER = re.compile(r"\[(\d+)\]")
_SCORE_COLUMNS = (3, 4, 5, 6)


def _references(report: str) -> list[dict[str, str]]:
    return [
        match.groupdict()
        for line in _section(report, REPORT_SECTIONS[-1]).splitlines()
        if (match := _REFERENCE.match(line.strip()))
    ]


def _marker_numbers(text: str) -> set[int]:
    return {int(number) for number in _NUMBER.findall(text)}


def _stdout_metrics(stdout: str) -> dict[str, int | str]:
    """Read the CLI's own summary metrics, or fail loudly.

    The patterns are anchored and complete: a metric the CLI stops printing, or
    a new field it starts printing, fails this test rather than being ignored.
    """
    evidence = re.search(
        r"^Evidence: (?P<cited>\d+) cited sources; (?P<scored>\d+) scored; "
        r"(?P<verified>\d+) verified, (?P<contradicted>\d+) contradicted$",
        stdout,
        re.MULTILINE,
    )
    integrity = re.search(
        r"^Integrity: (?P<claims>\d+) duplicate claims; "
        r"(?P<rows>\d+) duplicate source rows; "
        r"(?P<uncited>\d+) uncited settled points$",
        stdout,
        re.MULTILINE,
    )
    quality = re.search(
        r"^Quality: (?P<status>\S+) \(critic (?P<score>\d+)/10; "
        r"(?P<covered>\d+)/(?P<planned>\d+) topics covered, "
        r"(?P<ratio>\d+)%\)$",
        stdout,
        re.MULTILINE,
    )
    assert evidence is not None, stdout
    assert integrity is not None, stdout
    assert quality is not None, stdout
    return {
        "quality_status": quality.group("status"),
        "critic_score": int(quality.group("score")),
        "covered_topics": int(quality.group("covered")),
        "planned_topics": int(quality.group("planned")),
        "coverage_ratio": int(quality.group("ratio")),
        "cited_sources": int(evidence.group("cited")),
        "scored_cited_sources": int(evidence.group("scored")),
        "verified_claims": int(evidence.group("verified")),
        "contradicted_claims": int(evidence.group("contradicted")),
        "duplicate_claims": int(integrity.group("claims")),
        "duplicate_source_rows": int(integrity.group("rows")),
        "uncited_settled_points": int(integrity.group("uncited")),
    }


def _critic_blocks(body: str) -> dict[str, str]:
    """Every fenced reader-report surface the Critic request quotes."""
    lines = body.splitlines()
    blocks: dict[str, str] = {}
    index = 0
    while index < len(lines):
        opening = re.fullmatch(
            r"(?P<ticks>`{3,})(?P<info>reader-[a-z-]+)", lines[index]
        )
        if opening is None:
            index += 1
            continue
        closing = index + 1
        while closing < len(lines) and lines[closing] != opening.group("ticks"):
            closing += 1
        blocks[opening.group("info")] = "\n".join(lines[index + 1 : closing])
        index = closing + 1
    return blocks


def _critic_body(state: ResearchState) -> str:
    """The exact user message the Critic's review call would send."""
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="cli-acceptance", api_key=None
        )
    )
    agent = CriticAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id=state.session_id, agent_name="critic", max_entries=10
        ),
        tools=critic_tools(tracker),
    )
    return critique_messages(
        agent.build_task(state),
        ReActRun(agent_name="critic", stop_reason="finished"),
    )[1].content


def _critic_quality(body: str) -> dict[str, Any]:
    block = _section(body, "# Deterministic quality snapshot", end="\n# ")
    return json.loads(block.split("\n", 1)[1])


# --- metric agreement ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Metric:
    """One quantity, read from every surface that actually carries it.

    Every leg measures the same thing in the same unit, so ``agree`` is an
    equality across surfaces rather than a repetition of an expectation.
    """

    legs: dict[str, int]

    def agree(self) -> bool:
        return len(set(self.legs.values())) == 1

    def describe(self) -> str:
        return ", ".join(f"{name}={value}" for name, value in self.legs.items())


# --- the acceptance -----------------------------------------------------------


def test_mocked_cli_acceptance_has_no_recorded_report_pathology(
    tmp_path: Path,
) -> None:
    """The published report, its ledger, the CLI summary, and the Critic agree.

    One run, and four surfaces of it: what the CLI printed, what the terminal
    research state holds, what was published to disk, and what the Critic's
    review request actually contained.
    """
    fixture = _fixture()
    pre_state = fixture.state
    quality = pre_state.quality
    assert quality is not None
    assert quality.hard_failures == [], quality.hard_failures

    # The fixture must have carried the historical hazards, or every guard
    # below would be vacuous: the un-merged multi-pass lists are longer than
    # the canonical snapshots they fold into.
    canonical_urls = {
        normalize_source_url(source.url)
        for source in pre_state.evaluated_sources
    }
    canonical_claims = {
        claim_fingerprint(claim.text) for claim in pre_state.verified_claims
    }
    assert fixture.raw_source_records > len(canonical_urls)
    assert fixture.raw_claim_records > len(canonical_claims)

    # The real terminal finalizer publishes both artifacts.
    publisher = _LocalPublisher(directory=tmp_path)
    final_state = load_state(
        asyncio.run(finalize_report_node(publisher)(dump_state(pre_state)))
    )
    assert len(publisher.documents) == 2
    assert graph_quality_status(final_state) == "accepted"
    assert publisher.memory_claims > 0

    report_path = Path(final_state.report_path or "")
    evidence_path = Path(final_state.evidence_path or "")
    assert report_path.is_file() and evidence_path.is_file()

    # The real CLI entry point, with a scripted runner.
    stdout = io.StringIO()
    runner = _ScriptedRunner(state=final_state)
    exit_code = cli_main(
        [BATTERY_QUESTION, "--require-quality"],
        runner=runner,
        stream=stdout,
    )
    captured = stdout.getvalue()
    assert exit_code == EXIT_OK
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["question"] == BATTERY_QUESTION
    assert call["resume_session_id"] is None
    assert call["max_iterations"] is None
    assert call["output_format"] is None
    assert call["config_path"] == DEFAULT_CONFIG_PATH
    assert callable(call["event_handler"])
    # Every event the run recorded was delivered to the handler once, in state
    # order, and the CLI streamed exactly the enumerated progress events —
    # each once, in order — and no other record.
    assert runner.streamed == [event.message for event in final_state.events]
    expected_progress = [
        line
        for event in final_state.events
        if (line := render_progress(event, verbose=False)) is not None
    ]
    assert expected_progress
    cursor = -1
    for line in expected_progress:
        position = captured.index(f"{line}\n")
        assert position > cursor
        cursor = position
    for event in final_state.events:
        if is_streamed_event(event.event_type, verbose=False):
            continue
        assert f" {event.message}\n" not in captured
    assert "Research session started." in captured
    # At least one recorded event is not a progress event, so the negative
    # loop above really skipped something rather than skipping nothing.
    assert any(
        not is_streamed_event(event.event_type, verbose=False)
        for event in final_state.events
    )
    # No report text reaches stdout, on any line. The report body is what the
    # two artifacts exist to carry, and the progress stream is the one place a
    # report could be printed by accident.
    assert final_state.report
    for line in final_state.report.splitlines():
        if line.strip():
            assert line.strip() not in captured

    # Both Markdown artifacts, read back off disk.
    report = report_path.read_text(encoding="utf-8")
    ledger = evidence_path.read_text(encoding="utf-8")
    assert report == final_state.report
    assert ledger == final_state.report_evidence
    # The plan's separation constraint, asserted in the direction that the
    # five targets below never checked: every ledger-only section must be
    # ABSENT from the reader report, and the reader report must be the shorter
    # document. Appending the source-assessment table to the reader report
    # would have left all five of them green.
    for heading in EVIDENCE_SECTIONS:
        assert heading not in report, f"{heading} leaked into the reader report"
        assert heading in ledger
    assert len(report) < len(ledger)

    composition = final_state.composition
    assert composition is not None

    # --- surfaces -------------------------------------------------------------
    printed = _stdout_metrics(captured)

    source_rows = _table_rows(_section(ledger, "## Source assessment"))
    registry_rows = _table_rows(_section(ledger, "## Checked claim registry"))
    reviewed_not_cited = _section(ledger, "## Reviewed but not cited")
    references = _references(report)
    reference_urls = [entry["url"] for entry in references]
    reference_numbers = {int(entry["number"]) for entry in references}
    cited_numbers = _marker_numbers(
        report[: report.index(REPORT_SECTIONS[-1])]
    )

    artifact_source_urls = [_row_url(row[1]) for row in source_rows]
    scored_rows = [row for row in source_rows if row[2] == "scored"]
    unscored_rows = [row for row in source_rows if row[2] != "scored"]
    unscored_rows_with_numbers = sum(
        1
        for row in unscored_rows
        if any(row[column] != _CELL_EMPTY for column in _SCORE_COLUMNS)
    )
    fabricated_cells = sum(
        1
        for row in unscored_rows
        for column in _SCORE_COLUMNS
        if row[column] != _CELL_EMPTY
    )
    scored_referenced = len(
        set(reference_urls) & {_row_url(row[1]) for row in scored_rows}
    )
    verdict_counts = {
        verdict: sum(1 for row in registry_rows if row[2] == verdict)
        for verdict in ("verified", "contradicted", "unverified",
                        "insufficient_evidence")
    }
    uncertainty = _section(report, REPORT_SECTIONS[3])
    contradicted_in_report = sum(
        1
        for line in uncertainty.splitlines()
        if _BULLET.match(line) and "contradicting passage" in line
    )
    methodology = _section(report, REPORT_SECTIONS[4])
    reviewed_line = re.search(
        r"(?P<reviewed>\d+) reviewed source\(s\): (?P<scored>\d+) scored, "
        r"(?P<unscored>\d+) unscored\.",
        methodology,
    )
    planned_line = re.search(
        r"(?P<planned>\d+) planned sub-topic\(s\): (?P<ids>.+)$",
        methodology,
        re.MULTILINE,
    )
    assert reviewed_line is not None, methodology
    assert planned_line is not None, methodology
    planned_ids = re.findall(r"topic-\d+", planned_line.group("ids"))
    # Artifact-side settled points: the executive-summary and finding bullets,
    # plus the constraint table's own row label. Each must carry a citation.
    settled_lines = [
        line
        for heading in (REPORT_SECTIONS[0], REPORT_SECTIONS[2])
        for line in _section(report, heading).splitlines()
        if _BULLET.match(line)
    ]
    constraint_rows = _table_rows(_section(report, REPORT_SECTIONS[1]))
    covered_ids_in_report = sorted(
        {
            coverage_id
            for row in constraint_rows
            for coverage_id in re.findall(r"topic-\d+", row[0])
        }
    )
    uncited_settled = sum(
        1 for line in settled_lines if not _marker_numbers(line)
    ) + sum(1 for row in constraint_rows if not _marker_numbers(row[0]))

    critic_body = _critic_body(final_state)
    critic_blocks = _critic_blocks(critic_body)
    # The plan's target is 7/7: the report's identity block plus its six reader
    # sections. Pinned as a literal. The expression it replaces,
    # ``1 + len(_READER_SECTION_ORDER)``, is computed from the same private
    # constant the request is built from, so a change that dropped one reader
    # section from both ``REPORT_SECTIONS`` and ``_READER_SECTION_ORDER`` would
    # read 6 == 6 == 6 and pass here at 6/6 where the plan requires 7/7.
    assert len(_READER_SECTION_ORDER) == 6
    required_surfaces = 7
    assert len(REPORT_SECTIONS) == 6
    critic_present = sum(
        1
        for block in critic_blocks.values()
        if block.strip() != _SECTION_NOT_PRESENT
    )

    # --- one quantity, every surface that carries it -------------------------
    metrics = {
        "duplicate_source_rows": _Metric(
            {
                "stdout": int(printed["duplicate_source_rows"]),
                "state": quality.duplicate_source_rows,
                "artifact": len(artifact_source_urls)
                - len(set(artifact_source_urls)),
            }
        ),
        "unscored_sources_with_a_numeric_score": _Metric(
            {
                "state": sum(
                    1
                    for source in composition.sources
                    if source.evaluation_status != "scored"
                    and any(
                        score is not None
                        for score in (
                            source.authority_score,
                            source.recency_score,
                            source.relevance_score,
                            source.overall_score,
                        )
                    )
                ),
                # No stdout leg: the CLI prints no per-source score, so this
                # quantity is proved on the typed record and the artifact.
                "artifact": unscored_rows_with_numbers,
            }
        ),
        "scored_cited_sources": _Metric(
            {
                "stdout": int(printed["scored_cited_sources"]),
                # Labelled ``derived``, not ``state``: this leg re-evaluates the
                # CLI's own expression (``ratio * cited_sources``), so its
                # agreement with stdout is true by construction. The
                # independent reading is the artifact leg.
                "derived": round(
                    quality.scored_cited_source_ratio * quality.cited_sources
                ),
                "artifact": scored_referenced,
            }
        ),
        "used_sources_in_reader_references": _Metric(
            {
                "stdout": int(printed["cited_sources"]),
                "state": quality.cited_sources,
                # Equality of these two is exactly "zero unused references":
                # every listed reference number is used in the report body.
                "artifact_listed": len(reference_numbers),
                "artifact_used": len(cited_numbers),
            }
        ),
        "critic_visible_required_sections": _Metric(
            {
                # No stdout leg: the CLI prints no section count.
                "required": required_surfaces,
                "artifact": 1
                + sum(1 for heading in REPORT_SECTIONS if heading in report),
                "critic": critic_present,
            }
        ),
        "duplicate_claim_ids": _Metric(
            {
                "stdout": int(printed["duplicate_claims"]),
                "state": quality.duplicate_claims,
                "artifact": len(registry_rows)
                - len({row[1] for row in registry_rows}),
            }
        ),
        "uncited_settled_points": _Metric(
            {
                "stdout": int(printed["uncited_settled_points"]),
                "state": quality.uncited_settled_points,
                "artifact": uncited_settled,
            }
        ),
        "verified_claims": _Metric(
            {
                "stdout": int(printed["verified_claims"]),
                "state": quality.verified_claims,
                "artifact": verdict_counts["verified"],
            }
        ),
        "contradicted_claims": _Metric(
            {
                "stdout": int(printed["contradicted_claims"]),
                "state": quality.contradicted_claims,
                "artifact": contradicted_in_report,
            }
        ),
        "planned_topics": _Metric(
            {
                "stdout": int(printed["planned_topics"]),
                "state": quality.planned_topics,
                "artifact": len(planned_ids),
            }
        ),
        "covered_topics": _Metric(
            {
                "stdout": int(printed["covered_topics"]),
                "state": quality.covered_topics,
                # Labelled ``echo``: this counts topic ids inside constraint-row
                # text the fixture itself authored, so it repeats the fixture
                # rather than independently reading the report. The stdout and
                # state legs carry the comparison.
                "echo": len(covered_ids_in_report),
            }
        ),
    }
    print(
        "\n".join(
            [
                "",
                "--- captured CLI stdout ---",
                captured.rstrip("\n"),
                "--- artifact shape ---",
                f"report chars={len(report)} ledger chars={len(ledger)} "
                f"critic per-section budget={CRITIC_REPORT_CHARS}",
                f"source rows={len(source_rows)} "
                f"(scored={len(scored_rows)} unscored={len(unscored_rows)})",
                f"claim rows={len(registry_rows)}",
                f"reader references={len(reference_numbers)} "
                f"citation numbers used={len(cited_numbers)}",
                f"reviewed-but-not-cited entries="
                f"{reviewed_not_cited.count('- https://')}",
                f"critic report surfaces={critic_present}/{required_surfaces}",
                "--- metric agreement ---",
                *(f"{name}: {metrics[name].describe()}" for name in metrics),
            ]
        )
    )
    # Every metric must read the same on every surface that carries it. All
    # disagreements are collected so one run names every broken surface.
    disagreements = {
        name: metric.describe()
        for name, metric in metrics.items()
        if not metric.agree()
    }
    assert disagreements == {}, disagreements

    # --- the five recorded pathologies, as the plan states them -------------
    # 156 duplicate URL rows -> 0: one row per canonical source.
    assert metrics["duplicate_source_rows"].legs["state"] == 0
    assert len(source_rows) == quality.unique_sources
    # 221 false numeric scores for unscored sources -> 0.
    assert unscored_rows_with_numbers == 0
    assert fabricated_cells == 0
    assert reviewed_line.group("scored") == str(len(scored_rows))
    assert reviewed_line.group("unscored") == str(len(unscored_rows))
    # 89 unused sources in reader references -> 0.
    assert reference_numbers == cited_numbers
    assert len(reference_urls) == len(set(reference_urls))
    assert set(reference_urls) <= set(artifact_source_urls)
    assert (
        reviewed_not_cited.count("- https://")
        == len(source_rows) - len(references)
        == len(unscored_rows)
    )
    # 2/7 critic-visible required sections -> 7/7, on a report longer than the
    # per-section review budget.
    assert len(critic_blocks) == required_surfaces
    assert critic_present == required_surfaces
    assert _SECTION_NOT_PRESENT not in critic_body
    assert len(report) > CRITIC_REPORT_CHARS
    assert critic_blocks["reader-references"].endswith(reference_urls[-1])
    assert "reviewed source(s)" in critic_blocks["reader-methodology"]
    # The deterministic quality snapshot reaches the Critic unchanged.
    assert _critic_quality(critic_body) == quality.model_dump(mode="json")
    # duplicate claim IDs > 0 -> 0: one row per checked claim, no repeated fact.
    assert metrics["duplicate_claim_ids"].legs["state"] == 0
    assert len(registry_rows) == len(composition.claims)
    assert len({row[4] for row in registry_rows}) == len(registry_rows)
    assert verdict_counts["verified"] == sum(
        1 for claim in composition.claims if claim.verdict == "verified"
    )
    assert verdict_counts["contradicted"] == 1

    # --- stdout identity, verdict, and artifact paths ------------------------
    assert printed["quality_status"] == "accepted"
    assert printed["critic_score"] == 8
    assert printed["coverage_ratio"] == 100
    assert (
        f"Report: {report_path}" in captured
        and f"Evidence ledger: {evidence_path}" in captured
    )
    assert f"Session ID: {SESSION_ID}" in captured
    assert "Status: completed" in captured
    assert "Warnings:" in captured


@pytest.mark.parametrize(
    "status",
    ["scored", "unscored_cap", "unscored_provider", "unscored_missing"],
)
def test_a_scored_source_needs_every_number_and_an_unscored_one_forbids_them(
    status: str,
) -> None:
    """The mechanism behind the 221 fabricated scores, asserted directly.

    ``ScoredSource`` refuses the recorded artifact's shape: a source nobody
    judged cannot carry a number, and a source claiming ``scored`` cannot omit
    one. The rule is a type-level property rather than a renderer behaviour,
    which is why the ledger cannot print a fabricated score even for a caller
    that hands it a pre-contract snapshot.
    """
    numbers = {
        "authority_score": 0.2,
        "recency_score": 0.2,
        "relevance_score": 0.2,
        "overall_score": 0.2,
    }
    if status == "scored":
        scored = ScoredSource(
            url="https://reviewed.example/page",
            title="Reviewed page",
            rationale="All four numbers were supplied for a scored source.",
            **numbers,
        )
        assert scored.evaluation_status == "scored"
        with pytest.raises(ValidationError):
            ScoredSource(
                url="https://reviewed.example/page",
                title="Reviewed page",
                rationale="A scored source with no numbers.",
                evaluation_status="scored",
            )
        return

    unscored = ScoredSource(
        url="https://reviewed.example/page",
        title="Reviewed page",
        rationale="A status is stated and no judgement is implied.",
        evaluation_status=status,  # type: ignore[arg-type]
    )
    assert unscored.overall_score is None
    with pytest.raises(ValidationError):
        ScoredSource(
            url="https://reviewed.example/page",
            title="Reviewed page",
            rationale="An unscored source must not carry numbers.",
            evaluation_status=status,  # type: ignore[arg-type]
            **numbers,
        )


def test_the_renderers_collapse_repeated_records_onto_one_row_each() -> None:
    """The mechanism behind the 156 duplicate rows, asserted directly.

    The recorded appendix had 257 rows for 101 canonical URLs because every
    refinement pass appended another copy. Both renderers fold their input
    through the canonical snapshot helpers, so a duplicated input still renders
    one row per canonical identity.
    """
    sub_topics = _topics()
    first, second = _first_pass(), _second_pass()
    raw_sources = [*first.sources, *second.sources]
    raw_claims = [*first.claims, *second.claims]
    assert len({source.url for source in raw_sources}) < len(raw_sources)
    assert len({claim.claim_id for claim in raw_claims}) < len(raw_claims)

    state = ResearchState(
        session_id=SESSION_ID,
        original_question=BATTERY_QUESTION,
        sub_topics=sub_topics,
        iteration=1,
        max_iterations=MAX_ITERATIONS,
    )
    composition = _composition(
        state=state,
        sub_topics=sub_topics,
        sources=raw_sources,
        claims=raw_claims,
        findings=list(first.findings),
        events=[],
    )
    assert len(composition.sources) == len(
        {source.url for source in raw_sources}
    )
    assert len(composition.claims) == len(
        {claim_fingerprint(claim.text) for claim in raw_claims}
    )

    ledger = render_evidence_ledger(composition)
    source_rows = _table_rows(_section(ledger, "## Source assessment"))
    registry_rows = _table_rows(_section(ledger, "## Checked claim registry"))
    assert len(source_rows) == len(composition.sources)
    assert len(registry_rows) == len(composition.claims)
    assert len({row[1] for row in registry_rows}) == len(registry_rows)
    assert [_row_url(row[1]) for row in source_rows] == [
        source.url for source in composition.sources
    ]


def test_reader_references_hold_only_sources_a_reader_point_cites() -> None:
    """The mechanism behind the 89 unused references, asserted directly.

    A composition that reviews thirteen sources must number exactly the ones
    its statements resolve to: the five the model cited, plus the independent
    verification sources that actually carried the verdicts (Task 7 — a
    verified claim cites its selected support, not only the finding that first
    raised it). The rest stay in the ledger.
    """
    fixture = _fixture()
    composition = fixture.state.composition
    assert composition is not None
    cited = {citation.url for citation in reader_citations(composition)}
    assert len(cited) < len(composition.sources)
    assert {
        url for point in _reader_points(composition) for url in point.source_urls
    } <= cited

    report = render_reader_report(composition)
    references = _references(report)
    assert len(references) == len(cited)
    assert {entry["url"] for entry in references} == cited
    body = report[: report.index(REPORT_SECTIONS[-1])]
    assert _marker_numbers(body) == {int(entry["number"]) for entry in references}

    ledger = render_evidence_ledger(composition)
    not_cited = _section(ledger, "## Reviewed but not cited")
    for source in composition.sources:
        if source.url in cited:
            continue
        assert source.url in not_cited
        assert source.url not in {entry["url"] for entry in references}


def test_the_critic_sees_every_required_surface_of_a_long_report() -> None:
    """The mechanism behind the 2/7 critic view, asserted directly.

    The recorded review received only the first 6,000 characters, which dropped
    the verified-claims, uncertainty, limitations, citations and appendix
    surfaces. Every reader section is now quoted under its own independent cap.
    """
    state = _fixture().state
    quality = state.quality
    assert quality is not None
    report = state.report or ""
    ledger = state.report_evidence or ""
    assert len(report) > CRITIC_REPORT_CHARS

    body = _critic_body(state)
    blocks = _critic_blocks(body)
    # Pinned as a literal for the same reason as the acceptance test: a
    # constant-derived target cannot notice one reader section leaving both
    # ``REPORT_SECTIONS`` and ``_READER_SECTION_ORDER``.
    assert len(_READER_SECTION_ORDER) == 6
    required = 7

    assert len(blocks) == required
    assert _SECTION_NOT_PRESENT not in body
    assert set(blocks) == {
        "reader-identity",
        *(f"reader-{key}" for key, _ in _READER_SECTION_ORDER),
    }
    for heading in REPORT_SECTIONS:
        assert heading in report
    for heading in EVIDENCE_SECTIONS:
        # Present in the ledger AND absent from the reader report: the
        # separation constraint is a two-sided property, and asserting only
        # the ledger half left a regression that appended the evidence tables
        # to the reader report entirely undetected.
        assert heading in ledger
        assert heading not in report
    # The last reader surface survives even though the report is over budget.
    assert blocks["reader-references"].startswith("1. ")
    assert "reviewed source(s)" in blocks["reader-methodology"]
    assert _critic_quality(body) == quality.model_dump(mode="json")
