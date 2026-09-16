"""Scripted whole-report cases.

The controlled registry is intentionally independent of configuration loading.
It creates typed state and deterministic tool doubles in memory, so importing
or running it cannot discover credentials or open a network client.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from deep_research.agents.base import AgentRun
from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    QUALITY_STATUS_ACCEPTED,
    ReportComposition,
    render_evidence_ledger,
    render_reader_report,
    report_as_of,
    report_scope,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActRun, ReActStep
from deep_research.agents.synthesizer import (
    evidence_report_filename,
    report_filename,
)
from deep_research.e2e_evaluation.models import (
    CASE_REGISTRY_VERSION,
    CASE_SCHEMA_VERSION,
    ControlledCase,
    SnapshotPass,
)
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Claim,
    Critique,
    CritiqueGap,
    EvidencePassage,
    Finding,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

FIXED_TIMESTAMP = "2026-09-01T00:00:00+00:00"

CONTROLLED_CASE_IDS = (
    "broad-constraints",
    "comparative-conflict",
    "refinement-evidence-recovery",
)
LIVE_CASE_IDS = tuple(f"{case_id}-live" for case_id in CONTROLLED_CASE_IDS)


class ControlledDependencyError(RuntimeError):
    """A scripted controlled dependency was used outside its contract."""


@dataclass
class ScriptedDependencies:
    """Network-zero doubles for search, reads, memory, and publication."""

    case_id: str
    search_responses: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    page_responses: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    source_rows_by_finding: dict[str, list[ScoredSource]] = field(
        default_factory=dict
    )
    calls: Counter[str] = field(default_factory=Counter)
    read_urls: list[str] = field(default_factory=list)
    publication_operations: list[str] = field(default_factory=list)
    agent_call_order: list[str] = field(default_factory=list)
    prohibited_calls: list[str] = field(default_factory=list)
    real_services_used: list[str] = field(default_factory=list)

    @property
    def network_zero(self) -> bool:
        return not self.real_services_used and not self.prohibited_calls

    @property
    def scripted(self) -> bool:
        return True

    @property
    def request_counts(self) -> dict[str, int]:
        return dict(sorted(self.calls.items()))

    def _record(self, operation: str) -> None:
        self.calls[operation] += 1

    def source_rows_for_findings(
        self, findings: Sequence[Finding]
    ) -> list[ScoredSource]:
        """Resolve scored rows from the findings actually handed upstream."""
        if not findings:
            raise ControlledDependencyError("source evaluation requires findings")
        rows: dict[str, ScoredSource] = {}
        for finding in findings:
            candidates = self.source_rows_by_finding.get(finding_fingerprint(finding))
            if not candidates:
                raise ControlledDependencyError(
                    "no scripted source rows for the handed-off finding"
                )
            for source in candidates:
                rows.setdefault(normalize_source_url(source.url), source)
        return list(rows.values())

    def record_publication(self, operation: str) -> None:
        """Record only terminal publication operations, in observed order."""
        self.publication_operations.append(operation)

    def web_search(self, query: str) -> dict[str, JsonValue]:
        self._record("web_search")
        response = self.search_responses.get(query)
        if response is None:
            raise ControlledDependencyError(
                f"no scripted web_search response for {query!r}"
            )
        return dict(response)

    def web_scraper(self, url: str) -> dict[str, JsonValue]:
        self._record("web_scraper")
        response = self.page_responses.get(url)
        if response is None:
            raise ControlledDependencyError(
                f"no scripted web_scraper response for {url!r}"
            )
        if url not in self.read_urls:
            self.read_urls.append(url)
        return dict(response)

    def document_reader(self, url: str) -> dict[str, JsonValue]:
        self._record("document_reader")
        response = self.page_responses.get(url)
        if response is None:
            raise ControlledDependencyError(
                f"no scripted document_reader response for {url!r}"
            )
        if url not in self.read_urls:
            self.read_urls.append(url)
        return dict(response)

    def query_memory(self, query: str) -> dict[str, JsonValue]:
        self._record("query_memory")
        return {"query": query, "matches": []}

    def save_to_memory(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self._record("save_to_memory")
        return {"saved": True, "fields": sorted(payload)}

    def write_document(self, *, filename: str, content: str) -> dict[str, JsonValue]:
        self._record("write_document")
        return {"path": filename, "bytes_written": len(content.encode("utf-8"))}

    def read_steps(self, urls: list[str]) -> tuple[ReActStep, ...]:
        """Produce typed read-bearing steps for provenance assertions."""
        return tuple(
            ReActStep(
                iteration=index + 1,
                thought="Read the scripted page.",
                action="use_tool",
                tool_name="web_scraper",
                tool_input={"url": url},
                tool_result={
                    "tool_name": "web_scraper",
                    "success": True,
                    "data": {"url": url, "text": "Scripted source text."},
                    "latency_ms": 0.0,
                },
            )
            for index, url in enumerate(urls)
        )


def _scripted_event(
    *,
    agent_name: str,
    event_type: str,
    message: str,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ResearchEvent:
    """Create a deterministic, bounded event emitted by a scripted agent."""
    return ResearchEvent(
        event_type=event_type,
        source=f"agent.{agent_name}",
        message=message,
        timestamp=FIXED_TIMESTAMP,
        metadata=dict(metadata or {}),
    )


def _snapshot_fingerprint(snapshot: SnapshotPass, *, kind: str) -> str:
    """Fingerprint one complete canonical source or claim snapshot."""
    if kind == "sources":
        payload = {"sources": sorted(source.url for source in snapshot.sources)}
    elif kind == "claims":
        payload = {"claims": sorted(claim.claim_id for claim in snapshot.claims)}
    else:
        raise ValueError(f"unsupported snapshot fingerprint kind {kind!r}")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass
class ScriptedGraphAgent:
    """A deterministic six-agent double that runs through graph nodes."""

    agent_name: str
    case: ControlledCase
    dependencies: ScriptedDependencies
    calls: int = 0
    input_states: list[ResearchState] = field(default_factory=list)
    output_updates: list[ResearchStateUpdate] = field(default_factory=list)
    output_snapshots: list[SnapshotPass] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.agent_name

    def _fixture_snapshot(self, state: ResearchState) -> SnapshotPass:
        index = min(state.iteration, len(self.case.passes) - 1)
        return self.case.passes[index]

    def _planned_topics(self, state: ResearchState) -> tuple[SubTopic, ...]:
        """Validate and return the plan handed off by the Planner node."""
        if not state.sub_topics:
            raise ControlledDependencyError(
                f"{self.agent_name} requires the Planner handoff"
            )
        expected = {topic.coverage_id for topic in self.case.sub_topics}
        actual = {topic.coverage_id for topic in state.sub_topics}
        if actual != expected:
            raise ControlledDependencyError(
                f"{self.agent_name} received a miswired research plan"
            )
        return tuple(state.sub_topics)

    def _fixture_topic_by_id(self) -> dict[str, SubTopic]:
        return {topic.coverage_id: topic for topic in self.case.sub_topics}

    def _coverage_ids_for_findings(
        self, findings: Sequence[Finding], topics: Sequence[SubTopic]
    ) -> list[str]:
        fixture_topics = self._fixture_topic_by_id()
        return sorted(
            topic.coverage_id
            for topic in topics
            if any(
                finding.related_sub_topic
                == fixture_topics[topic.coverage_id].title
                for finding in findings
            )
        )

    def _run_record(self) -> ReActRun:
        return ReActRun(agent_name=self.agent_name, stop_reason="finished")

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.calls += 1
        self.dependencies.agent_call_order.append(self.agent_name)
        self.input_states.append(state.model_copy(deep=True))
        snapshot = self._fixture_snapshot(state)
        update: ResearchStateUpdate = {}

        if self.agent_name == "planner":
            update["sub_topics"] = list(self.case.sub_topics)
        elif self.agent_name == "researcher":
            planned_topics = self._planned_topics(state)
            fixture_topics = self._fixture_topic_by_id()
            if state.iteration == 0:
                self.dependencies.query_memory(state.original_question)
            events: list[ResearchEvent] = []
            findings_by_coverage = {
                coverage_id: [
                    finding
                    for finding in snapshot.findings
                    if finding.related_sub_topic == fixture_topics[coverage_id].title
                ]
                for coverage_id in fixture_topics
            }
            for topic in planned_topics:
                self.dependencies.web_search(topic.search_queries[0])
                topic_findings = findings_by_coverage.get(topic.coverage_id, [])
                has_finding = bool(topic_findings)
                for finding in topic_findings:
                    self.dependencies.web_scraper(finding.source_url)
                event_type = (
                    "agent.researcher.topic.attempted"
                    if has_finding
                    else "agent.researcher.topic.skipped"
                )
                events.append(
                    _scripted_event(
                        agent_name=self.agent_name,
                        event_type=event_type,
                        message="Scripted topic attempt recorded.",
                        metadata={
                            "coverage_id": topic.coverage_id,
                            "iteration": state.iteration,
                            "reason": "scripted_evidence"
                            if has_finding
                            else "scripted_evidence_unavailable",
                        },
                    )
                )
            existing_fingerprints = {
                finding_fingerprint(finding) for finding in state.raw_findings
            }
            finding_deltas: list[Finding] = []
            for finding in snapshot.findings:
                fingerprint = finding_fingerprint(finding)
                if fingerprint in existing_fingerprints:
                    continue
                existing_fingerprints.add(fingerprint)
                finding_deltas.append(finding)
            update["raw_findings"] = finding_deltas
            update["events"] = events
        elif self.agent_name == "source_evaluator":
            planned_topics = self._planned_topics(state)
            if not state.raw_findings:
                raise ControlledDependencyError(
                    "source_evaluator requires the Researcher handoff"
                )
            sources = self.dependencies.source_rows_for_findings(state.raw_findings)
            for source in sources:
                self.dependencies.web_scraper(source.url)
            source_snapshot = SnapshotPass(
                iteration=state.iteration,
                findings=list(state.raw_findings),
                sources=sources,
                claims=list(state.verified_claims),
            )
            self.output_snapshots.append(source_snapshot.model_copy(deep=True))
            update["evaluated_sources"] = list(sources)
            update["events"] = [
                _scripted_event(
                    agent_name=self.agent_name,
                    event_type="agent.source_evaluator.snapshot.completed",
                    message="Complete source snapshot recorded.",
                    metadata={
                        "snapshot_kind": "complete",
                        "iteration": state.iteration,
                        "count": len(sources),
                        "snapshot_fingerprint": _snapshot_fingerprint(
                            source_snapshot, kind="sources"
                        ),
                        "coverage_ids": self._coverage_ids_for_findings(
                            state.raw_findings, planned_topics
                        ),
                    },
                )
            ]
        elif self.agent_name == "fact_checker":
            planned_topics = self._planned_topics(state)
            if not state.evaluated_sources:
                raise ControlledDependencyError(
                    "fact_checker requires the Source Evaluator handoff"
                )
            if not state.raw_findings:
                raise ControlledDependencyError(
                    "fact_checker requires Researcher provenance"
                )
            evaluated_urls = {
                normalize_source_url(source.url)
                for source in state.evaluated_sources
            }
            finding_coverage_ids = set(
                self._coverage_ids_for_findings(state.raw_findings, planned_topics)
            )
            claims = [
                claim
                for claim in snapshot.claims
                if set(claim.consumed_coverage_ids).intersection(
                    finding_coverage_ids
                )
                and all(
                    normalize_source_url(url) in evaluated_urls
                    for url in claim.source_urls
                )
                and all(
                    normalize_source_url(passage.source_url) in evaluated_urls
                    for passage in claim.verification_evidence
                )
            ]
            if not claims:
                raise ControlledDependencyError(
                    "fact_checker found no claims with evaluated provenance"
                )
            for claim in claims:
                for passage in claim.verification_evidence:
                    self.dependencies.web_scraper(passage.source_url)
            claim_snapshot = SnapshotPass(
                iteration=state.iteration,
                findings=list(state.raw_findings),
                sources=list(state.evaluated_sources),
                claims=claims,
            )
            self.output_snapshots.append(claim_snapshot.model_copy(deep=True))
            update["verified_claims"] = list(claims)
            update["events"] = [
                _scripted_event(
                    agent_name=self.agent_name,
                    event_type="agent.fact_checker.snapshot.completed",
                    message="Complete claim snapshot recorded.",
                    metadata={
                        "snapshot_kind": "complete",
                        "iteration": state.iteration,
                        "count": len(claims),
                        "snapshot_fingerprint": _snapshot_fingerprint(
                            claim_snapshot, kind="claims"
                        ),
                        "coverage_ids": sorted(
                            {
                                coverage_id
                                for claim in claims
                                for coverage_id in claim.consumed_coverage_ids
                            }
                        ),
                    },
                )
            ]
        elif self.agent_name == "synthesizer":
            self._planned_topics(state)
            if not state.raw_findings or not state.evaluated_sources:
                raise ControlledDependencyError(
                    "synthesizer requires Researcher and Source Evaluator output"
                )
            if not state.verified_claims:
                raise ControlledDependencyError(
                    "synthesizer requires the Fact Checker handoff"
                )
            observed = SnapshotPass(
                iteration=state.iteration,
                findings=list(state.raw_findings),
                sources=list(state.evaluated_sources),
                claims=list(state.verified_claims),
            )
            composition = _composition(case=self.case, snapshot=observed, state=state)
            update.update(
                {
                    "report": render_reader_report(composition),
                    "report_evidence": render_evidence_ledger(composition),
                    "evidence_path": (
                        f"output/evaluations/e2e/{self.case.case_id}/"
                        "evidence-ledger.md"
                    ),
                    "composition": composition,
                    "unique_source_count": len(composition.sources),
                    "unique_claim_count": len(composition.claims),
                }
            )
        elif self.agent_name == "critic":
            planned_topics = self._planned_topics(state)
            if state.composition is None or state.quality is None:
                raise ControlledDependencyError(
                    "critic requires the Synthesizer quality handoff"
                )
            covered = {
                coverage_id
                for claim in state.verified_claims
                for coverage_id in claim.consumed_coverage_ids
            }
            unresolved = set(state.quality.unresolved_topic_ids)
            targets = []
            for topic in planned_topics:
                if topic.coverage_id not in covered or topic.coverage_id in unresolved:
                    targets.append(topic.coverage_id)
            gaps = [
                CritiqueGap(
                    coverage_id=target,
                    problem="The scripted review needs a bounded evidence update.",
                    recommended_queries=[
                        topic.search_queries[0]
                        for topic in planned_topics
                        if topic.coverage_id == target
                    ]
                    or ["targeted evidence"],
                )
                for target in targets
            ]
            update["critique"] = Critique(
                score=6 if targets else 9,
                gaps=gaps,
                unsupported_claims=[],
                recommended_queries=[
                    query for gap in gaps for query in gap.recommended_queries
                ],
                should_continue=False,
                rationale="Scripted bounded critique.",
            )
            update["events"] = [
                _scripted_event(
                    agent_name=self.agent_name,
                    event_type="agent.critic.targets.recorded",
                    message="Critic target identities recorded.",
                    metadata={
                        "iteration": state.iteration,
                        "coverage_ids": targets,
                        "should_continue": False,
                    },
                )
            ]
        else:
            raise ValueError(f"unknown scripted graph agent {self.agent_name!r}")

        self.output_updates.append(deepcopy(update))
        return AgentRun(
            agent_name=self.agent_name,
            result=None,
            react=self._run_record(),
            errors=[],
            state_update=update,
        )


# The ledger's name is derived from the reader report's by production
# (``evidence_report_filename``). Read the suffix from that function rather than
# repeating the literal here, so a change to the production naming rule cannot
# leave this double classifying artifacts by a stale convention.
_LEDGER_NAME_SUFFIX = evidence_report_filename(
    session_id="probe", iteration=0
).removeprefix(
    report_filename(session_id="probe", iteration=0).removesuffix(".md")
)


@dataclass
class ScriptedGraphPublisher:
    """Terminal publisher double with safe per-repetition local artifact writes."""

    dependencies: ScriptedDependencies
    artifact_directory: Path
    document_calls: int = 0

    async def publish_document(
        self, *, filename: str, content: str
    ) -> ToolResult:
        """Write the file the finalizer named, and record *that* name.

        The target and the recorded publication operation both come from the
        real ``filename`` argument. Naming the target by call ordinal (first
        call ``report.md``, second ``evidence-ledger.md``) meant a finalizer
        regression that published both artifacts under one name, or swapped
        them, still looked like a clean publication.
        """
        self.document_calls += 1
        operation = (
            "evidence_document"
            if filename.endswith(_LEDGER_NAME_SUFFIX)
            else "reader_document"
        )
        self.dependencies.record_publication(operation)
        target = self.artifact_directory / Path(filename).name
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.dependencies.write_document(filename=str(target), content=content)
        return ToolResult(
            tool_name="write_document",
            success=True,
            data={"path": str(target), "bytes_written": len(content.encode("utf-8"))},
            latency_ms=0.0,
            metadata={"scripted": True},
        )

    async def publish_claim(
        self, *, content: str, metadata: Mapping[str, JsonValue]
    ) -> ToolResult:
        self.dependencies.record_publication("memory_claim")
        self.dependencies.save_to_memory(
            {
                "content_chars": len(content),
                "metadata_keys": sorted(metadata),
            }
        )
        return ToolResult(
            tool_name="save_to_memory",
            success=True,
            data={"saved": True},
            latency_ms=0.0,
            metadata={"scripted": True},
        )


def scripted_research_agents(
    case: ControlledCase,
    dependencies: ScriptedDependencies,
    publisher: ScriptedGraphPublisher,
) -> ResearchAgents:
    """Build all six scripted agents and the terminal publisher for one run."""
    agents = {
        name: ScriptedGraphAgent(name, case, dependencies)
        for name in (
            "planner",
            "researcher",
            "source_evaluator",
            "fact_checker",
            "synthesizer",
            "critic",
        )
    }
    return ResearchAgents(**agents, publisher=publisher)


def _topic(coverage_id: str, title: str, priority: int) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale=f"{title} changes the decision surface.",
        search_queries=[f"{title} primary evidence"],
        success_criteria=[f"A read primary source answers {title}."],
        priority=priority,
    )


def _finding(topic: SubTopic, url: str, text: str) -> Finding:
    return Finding(
        content=text,
        source_url=url,
        source_title=f"Primary source for {topic.title}",
        extracted_at=FIXED_TIMESTAMP,
        confidence=0.9,
        related_sub_topic=topic.title,
    )


def _source(url: str, title: str) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=title,
        authority_score=0.95,
        recency_score=0.90,
        relevance_score=0.92,
        overall_score=0.92,
        rationale="Scripted primary-source assessment.",
    )


def _claim(
    text: str,
    *,
    origin_url: str,
    verification_url: str,
    topic: SubTopic,
    verdict: str = "verified",
    contradiction: str | None = None,
) -> Claim:
    passage = EvidencePassage(
        source_url=verification_url,
        source_title="Independent verification source",
        locator="section-1",
        excerpt=(
            "Independent evidence supports this finding."
            if verdict != "contradicted"
            else "Independent evidence disputes the finding."
        ),
        stance="contradicts" if verdict == "contradicted" else "supports",
    )
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[origin_url],
        verdict=verdict,
        confidence=0.86 if verdict == "verified" else 0.55,
        evidence=[passage.excerpt] if verdict != "contradicted" else [],
        contradictions=[contradiction] if contradiction else [],
        verification_evidence=[passage],
        consumed_coverage_ids=[topic.coverage_id],
    )


def _composition(
    *,
    case: ControlledCase,
    snapshot: SnapshotPass,
    state: ResearchState,
) -> ReportComposition:
    claims = list(snapshot.claims)
    sources = list(snapshot.sources)
    reader_claims = [claim for claim in claims if claim.verdict != "contradicted"]
    # This case intentionally starts with a complete evidence snapshot whose
    # reader composition omits its settled points.  The production quality
    # node therefore forces a refinement with no critic targets; the next
    # synthesizer pass restores the same complete snapshot to the reader.
    if case.case_id == "broad-constraints" and snapshot.iteration == 0:
        reader_claims = []
    summary = [
        {
            "text": claim.text,
            "claim_ids": [claim.claim_id],
            "source_urls": list(claim.source_urls),
        }
        for claim in reader_claims
    ]
    # Constructing through the typed model keeps this fixture on the same
    # renderer path as production and makes claim linkage observable.
    from deep_research.utils.types import ReportPoint

    uncertainty = [
        f"Contradiction disclosed for claim {claim.claim_id}."
        for claim in claims
        if claim.verdict == "contradicted"
    ]
    return ReportComposition(
        question=case.question,
        session_id=state.session_id,
        iteration=snapshot.iteration,
        max_iterations=state.max_iterations,
        as_of=report_as_of(findings=snapshot.findings, events=state.events),
        scope=report_scope(case.sub_topics),
        quality_status=QUALITY_STATUS_ACCEPTED,
        sub_topics=list(case.sub_topics),
        claims=claims,
        sources=sources,
        findings=list(snapshot.findings),
        summary=[ReportPoint.model_validate(item) for item in summary],
        uncertainty_notes=uncertainty,
    )


def _state_for_pass(
    case: ControlledCase,
    snapshot: SnapshotPass,
    *,
    report_path: str | None = None,
    evidence_path: str | None = None,
) -> ResearchState:
    """Materialize one pass as a typed state, including both artifacts.

    Private on purpose, and paired with ``_terminal_state``: these helpers
    fabricate a state that never came through the compiled graph, so the
    evaluator's observed metric legs all fall back to ``case.passes``. They
    exist so a gate can be driven from a hand-made pass in a test; nothing on
    this package's accepted path may be derived from one, which is exactly
    what ``DeterministicEvaluation.graph_observed`` records.
    """
    state = ResearchState(
        session_id=f"controlled-{case.case_id}",
        original_question=case.question,
        sub_topics=list(case.sub_topics),
        raw_findings=list(snapshot.findings),
        evaluated_sources=list(snapshot.sources),
        verified_claims=list(snapshot.claims),
        iteration=snapshot.iteration,
        max_iterations=max(3, len(case.passes)),
        events=[
            ResearchEvent(
                event_type="graph.node.completed",
                source="graph.synthesizer",
                message="Scripted synthesis completed.",
                timestamp=FIXED_TIMESTAMP,
                metadata={"iteration": snapshot.iteration},
            )
        ],
    )
    composition = _composition(case=case, snapshot=snapshot, state=state)
    report = render_reader_report(composition)
    ledger = render_evidence_ledger(composition)
    state = state.model_copy(
        update={
            "composition": composition,
            "report": report,
            "report_evidence": ledger,
            "report_path": report_path or f"output/e2e/{case.case_id}/report.md",
            "evidence_path": evidence_path
            or f"output/e2e/{case.case_id}/evidence-ledger.md",
        }
    )
    return state.model_copy(
        update={"quality": compute_report_quality(state, composition)}
    )


def _terminal_state(
    case: ControlledCase,
    *,
    report_path: str | None = None,
    evidence_path: str | None = None,
) -> ResearchState:
    """Materialize the final pass with terminal quality/publication events.

    Private for the same reason as ``_state_for_pass``: the terminal events it
    appends are written by hand, so the state is a *simulation* of a finished
    run rather than one. It is a test seam for the deterministic gates, never
    a producer of campaign evidence.
    """
    state = _state_for_pass(
        case,
        case.final_pass(),
        report_path=report_path,
        evidence_path=evidence_path,
    )
    quality = state.quality
    accepted = quality is not None and not quality.hard_failures
    terminal_events = [
        ResearchEvent(
            event_type="graph.quality.assessed",
            source="graph.finalize_report",
            message="Terminal quality gates assessed the report.",
            timestamp=FIXED_TIMESTAMP,
            metadata={
                "iteration": state.iteration,
                "accepted": accepted,
                "hard_failure_count": len(quality.hard_failures) if quality else 0,
            },
        ),
        ResearchEvent(
            event_type="graph.report.published",
            source="graph.finalize_report",
            message="Reader report and evidence ledger published.",
            timestamp=FIXED_TIMESTAMP,
            metadata={
                "report_path": state.report_path or "",
                "evidence_path": state.evidence_path or "",
                "report_writes": 1,
                "evidence_writes": 1,
            },
        ),
        ResearchEvent(
            event_type="graph.memory.saved",
            source="graph.finalize_report",
            message="Accepted claims saved after terminal publication.",
            timestamp=FIXED_TIMESTAMP,
            metadata={"memory_writes": 1 if accepted else 0},
        ),
        ResearchEvent(
            event_type="graph.session.completed",
            source="graph",
            message="Scripted whole-report session completed.",
            timestamp=FIXED_TIMESTAMP,
            metadata={"accepted": accepted, "iteration": state.iteration},
        ),
    ]
    return state.model_copy(update={"events": [*state.events, *terminal_events]})


def _case(
    *,
    case_id: str,
    title: str,
    question: str,
    topics: list[SubTopic],
    passes: list[SnapshotPass],
    expected_contradictions: int = 0,
    expected_refinement_topics: list[str] | None = None,
) -> ControlledCase:
    return ControlledCase(
        case_id=case_id,
        version=CASE_SCHEMA_VERSION,
        title=title,
        question=question,
        sub_topics=topics,
        passes=passes,
        expected_contradictions=expected_contradictions,
        expected_refinement_topics=expected_refinement_topics or [],
        metadata={"fixture": "network-zero", "provider": "scripted"},
    )


def _build_cases() -> tuple[ControlledCase, ...]:
    broad_topics = [
        _topic("topic-01", "grid connection", 1),
        _topic("topic-02", "supply chain and trade", 2),
        _topic("topic-03", "siting and safety", 3),
        _topic("topic-04", "market rules", 4),
        _topic("topic-05", "project economics", 5),
    ]
    broad_findings: list[Finding] = []
    broad_sources: list[ScoredSource] = []
    broad_claims: list[Claim] = []
    for index, topic in enumerate(broad_topics, start=1):
        origin = f"https://controlled.example/broad/source-{index}"
        verifier = f"https://controlled.example/broad/verifier-{index}"
        broad_findings.append(
            _finding(topic, origin, f"{topic.title.title()} is a material constraint.")
        )
        broad_sources.extend(
            [
                _source(origin, f"{topic.title.title()} primary source"),
                _source(verifier, f"{topic.title.title()} verification"),
            ]
        )
        broad_claims.append(
            _claim(
                f"{topic.title.title()} materially affects deployment decisions.",
                origin_url=origin,
                verification_url=verifier,
                topic=topic,
            )
        )
    broad_pass_1 = SnapshotPass(
        iteration=0,
        findings=broad_findings,
        sources=broad_sources,
        claims=broad_claims,
    )
    broad_pass_2 = broad_pass_1.model_copy(
        update={"iteration": 1, "force_refinement": True}
    )

    comparative_topics = [
        _topic("topic-01", "benefits", 1),
        _topic("topic-02", "costs", 2),
        _topic("topic-03", "regional applicability", 3),
    ]
    comparative_origin = "https://controlled.example/compare/primary"
    comparative_verifier = "https://controlled.example/compare/reviewer"
    comparative_sources = [
        _source(comparative_origin, "Comparative primary source"),
        _source(comparative_verifier, "Comparative independent review"),
    ]
    comparative_claims = [
        _claim(
            "Intervention A has a measurable deployment benefit.",
            origin_url=comparative_origin,
            verification_url=comparative_verifier,
            topic=comparative_topics[0],
        ),
        _claim(
            "Intervention A has lower lifecycle cost than Intervention B.",
            origin_url=comparative_origin,
            verification_url=comparative_verifier,
            topic=comparative_topics[1],
            verdict="contradicted",
            contradiction="The independent review reports a higher lifecycle cost.",
        ),
        _claim(
            "Regional applicability depends on local implementation rules.",
            origin_url=comparative_origin,
            verification_url=comparative_verifier,
            topic=comparative_topics[2],
        ),
    ]
    comparative_findings = [
        _finding(topic, comparative_origin, f"Evidence for {topic.title}.")
        for topic in comparative_topics
    ]
    comparative_pass_1 = SnapshotPass(
        iteration=0,
        findings=comparative_findings,
        sources=comparative_sources,
        claims=comparative_claims,
    )
    comparative_pass_2 = comparative_pass_1.model_copy(update={"iteration": 1})

    refinement_topics = [
        _topic("topic-01", "grid interconnection", 1),
        _topic("topic-02", "safety permitting", 2),
        _topic("topic-03", "market participation", 3),
        _topic("topic-04", "equipment lead times", 4),
        _topic("topic-05", "financing", 5),
    ]
    initial_findings: list[Finding] = []
    initial_sources: list[ScoredSource] = []
    initial_claims: list[Claim] = []
    for index, topic in enumerate(refinement_topics[:3], start=1):
        origin = f"https://controlled.example/refine/source-{index}"
        verifier = f"https://controlled.example/refine/verifier-{index}"
        initial_findings.append(
            _finding(topic, origin, f"Initial evidence for {topic.title}.")
        )
        initial_sources.extend(
            [
                _source(origin, f"{topic.title.title()} source"),
                _source(verifier, f"{topic.title.title()} review"),
            ]
        )
        initial_claims.append(
            _claim(
                f"{topic.title.title()} has an evidence-backed constraint.",
                origin_url=origin,
                verification_url=verifier,
                topic=topic,
            )
        )
    refinement_pass_1 = SnapshotPass(
        iteration=0,
        findings=initial_findings,
        sources=initial_sources,
        claims=initial_claims,
        critic_targets=["topic-04", "topic-05"],
    )
    final_findings = list(initial_findings)
    final_sources = list(initial_sources)
    final_claims = list(initial_claims)
    for index, topic in enumerate(refinement_topics[3:], start=4):
        origin = f"https://controlled.example/refine/source-{index}"
        verifier = f"https://controlled.example/refine/verifier-{index}"
        final_findings.append(
            _finding(topic, origin, f"Refinement evidence for {topic.title}.")
        )
        final_sources.extend(
            [
                _source(origin, f"{topic.title.title()} source"),
                _source(verifier, f"{topic.title.title()} review"),
            ]
        )
        final_claims.append(
            _claim(
                f"{topic.title.title()} has an evidence-backed constraint.",
                origin_url=origin,
                verification_url=verifier,
                topic=topic,
            )
        )
    refinement_pass_2 = SnapshotPass(
        iteration=1,
        findings=final_findings,
        sources=final_sources,
        claims=final_claims,
        new_evidence_topics=["topic-04", "topic-05"],
    )

    return (
        _case(
            case_id=CONTROLLED_CASE_IDS[0],
            title="Broad deployment constraints",
            question="What constraints shape grid-scale deployment?",
            topics=broad_topics,
            passes=[broad_pass_1, broad_pass_2],
        ),
        _case(
            case_id=CONTROLLED_CASE_IDS[1],
            title="Comparative evidence with conflict",
            question="Compare two interventions when evidence conflicts.",
            topics=comparative_topics,
            passes=[comparative_pass_1, comparative_pass_2],
            expected_contradictions=1,
        ),
        _case(
            case_id=CONTROLLED_CASE_IDS[2],
            title="Refinement recovers missing evidence",
            question="Which constraints remain after a targeted refinement?",
            topics=refinement_topics,
            passes=[refinement_pass_1, refinement_pass_2],
            expected_refinement_topics=["topic-04", "topic-05"],
        ),
    )


CONTROLLED_CASES = _build_cases()
LIVE_CASES = tuple(
    case.model_copy(
        deep=True,
        update={
            "case_id": f"{case.case_id}-live",
            "tier": "live",
            "network_zero": False,
            "scripted_dependencies": False,
            "authorization_required": True,
            "metadata": {
                "tier": "live",
                "authorization": "required before any provider or search call",
            },
        },
    )
    for case in CONTROLLED_CASES
)


def controlled_cases() -> tuple[ControlledCase, ...]:
    """Return fresh typed definitions for the three controlled cases."""
    return tuple(case.model_copy(deep=True) for case in CONTROLLED_CASES)


def case_by_id(case_id: str, *, tier: str = "controlled") -> ControlledCase:
    """Resolve one case without loading production configuration."""
    if tier == "controlled":
        available = CONTROLLED_CASES
    elif tier == "live":
        available = LIVE_CASES
    else:
        available = ()
    if tier in {"controlled", "live"}:
        for case in available:
            if case.case_id == case_id:
                return case.model_copy(deep=True)
    raise KeyError(f"unknown {tier} whole-report case {case_id!r}")


def live_cases() -> tuple[ControlledCase, ...]:
    """Return live case definitions without executing their dependencies."""
    return tuple(case.model_copy(deep=True) for case in LIVE_CASES)


def all_cases() -> tuple[ControlledCase, ...]:
    return controlled_cases() + live_cases()


def dependencies_for(case: ControlledCase) -> ScriptedDependencies:
    """Build a fresh network-zero dependency bundle for one case."""
    if case.tier != "controlled":
        raise RuntimeError("live case dependencies require explicit authorization")
    searches: dict[str, dict[str, JsonValue]] = {}
    pages: dict[str, dict[str, JsonValue]] = {}
    source_rows_by_finding: dict[str, list[ScoredSource]] = {}
    for topic in case.sub_topics:
        query = topic.search_queries[0]
        url = f"https://controlled.example/{case.case_id}/{topic.coverage_id}"
        searches[query] = {"results": [{"url": url, "title": topic.title}]}
        pages[url] = {"url": url, "text": f"Read evidence for {topic.title}."}
    for snapshot in case.passes:
        for source in snapshot.sources:
            pages.setdefault(
                source.url,
                {"url": source.url, "text": "Scripted primary-source text."},
            )
        for finding in snapshot.findings:
            topic = next(
                (
                    topic
                    for topic in case.sub_topics
                    if topic.title == finding.related_sub_topic
                ),
                None,
            )
            source_urls = {normalize_source_url(finding.source_url)}
            if topic is not None:
                for claim in snapshot.claims:
                    if topic.coverage_id not in claim.consumed_coverage_ids:
                        continue
                    source_urls.update(
                        normalize_source_url(url) for url in claim.source_urls
                    )
                    source_urls.update(
                        normalize_source_url(passage.source_url)
                        for passage in claim.verification_evidence
                    )
            existing = source_rows_by_finding.setdefault(
                finding_fingerprint(finding), []
            )
            seen_urls = {normalize_source_url(source.url) for source in existing}
            for source in snapshot.sources:
                normalized = normalize_source_url(source.url)
                if normalized in source_urls and normalized not in seen_urls:
                    existing.append(source)
                    seen_urls.add(normalized)
    return ScriptedDependencies(
        case_id=case.case_id,
        search_responses=searches,
        page_responses=pages,
        source_rows_by_finding=source_rows_by_finding,
    )


__all__ = [
    "CASE_REGISTRY_VERSION",
    "CONTROLLED_CASE_IDS",
    "CONTROLLED_CASES",
    "LIVE_CASE_IDS",
    "LIVE_CASES",
    "ControlledDependencyError",
    "ScriptedDependencies",
    "ScriptedGraphAgent",
    "ScriptedGraphPublisher",
    "all_cases",
    "case_by_id",
    "controlled_cases",
    "dependencies_for",
    "live_cases",
    "scripted_research_agents",
]
