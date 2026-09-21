"""Spike 3: scenario-driven replay of the real agent stack."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from deep_research.agents.critic import CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimsDraft,
    ClaimVerdictDraft,
    PassageVerdictDraft,
)
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.report_review import (
    ReportReviewDraft,
    ReviewDimensionScores,
    StatementDispositionDraft,
)
from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft
from deep_research.agents.source_evaluator import SourceScoreDraft, SourceScoresDraft
from deep_research.agents.synthesizer import (
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
)
from deep_research.evaluation.dependencies import (
    _DeterministicEmbeddings,
    _InMemoryCollection,
)
from deep_research.graph.orchestrator import run_research_graph
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import LangSmithRuntimeConfig, TokenUsage, Tracker
from deep_research.providers import NativeToolCall, NativeToolTurn
from deep_research.runtime.assembly import build_runtime
from deep_research.utils.config import ConfigSettings


@dataclass(frozen=True)
class ReplaySource:
    url: str
    title: str
    text: str
    excerpt: str
    claim: str
    issuer: str = "Acme Institute"
    authority: float = 0.9
    recency: float = 0.9
    relevance: float = 0.9
    confidence: float = 0.85
    verdict: str = "insufficient_evidence"

    def __post_init__(self) -> None:
        if self.excerpt not in self.text:
            raise ValueError(f"excerpt not in text for {self.url}")


@dataclass(frozen=True)
class ReplayTopic:
    title: str
    question: str
    dimensions: tuple[str, ...]
    critical: bool
    query: str
    sources: tuple[ReplaySource, ...]
    success_criteria: tuple[str, ...] = ("A figure is quoted.",)
    rationale: str = "It answers the question."


@dataclass(frozen=True)
class ReplayExpectation:
    quality: str
    exit_code: int
    minimum_answerable_claims: int = 0
    forbidden_assertions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayScenario:
    case_id: str
    question: str
    topics: tuple[ReplayTopic, ...]
    expectation: ReplayExpectation
    version: int = 1


def sources(scenario: ReplayScenario) -> dict[str, ReplaySource]:
    return {
        source.url: source
        for topic in scenario.topics
        for source in topic.sources
    }


class ReplayCompleter:
    """Answer every provider call from the scenario, asserting the packet."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.by_url = sources(scenario)
        self.calls: list[str] = []
        self.packets: dict[str, str] = {}
        self.searches: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _text(self, messages) -> str:
        return "\n".join(message.content for message in messages)

    def _ids(self, text: str, label: str) -> list[str]:
        match = re.search(rf"{label}: ([^\n]*)", text)
        if not match or match.group(1).strip() == "(none)":
            return []
        return [
            item.strip() for item in match.group(1).split(",") if item.strip()
        ]

    def _require(self, text: str, needle: str, *, where: str) -> None:
        if needle not in text:
            raise AssertionError(f"{where}: request did not carry {needle!r}")

    def _topic_for_target(self, coverage_id: str) -> ReplayTopic:
        index = int(coverage_id.rsplit("-", 1)[-1]) - 1
        return self.scenario.topics[index]

    # -- provider surface ------------------------------------------------
    async def complete_structured(
        self, messages, schema, *, agent_name=None, max_tokens=None
    ):
        name = schema.__name__
        text = self._text(messages)
        self.calls.append(f"{agent_name}:{name}")
        self.packets[f"{agent_name}:{name}"] = text
        dump = Path("scratch/spike3-out/requests")
        dump.mkdir(parents=True, exist_ok=True)
        (dump / f"{len(self.calls):03d}-{agent_name}-{name}.txt").write_text(
            text, encoding="utf-8"
        )
        return self._reply(name, text)

    async def complete_react(
        self, messages, tools, *, agent_name=None, max_tokens=None
    ):
        text = self._text(messages)
        self.calls.append(f"{agent_name}:react")
        if agent_name == "planner":
            self._require(
                text, self.scenario.question, where="planner scoping"
            )
            return NativeToolTurn(
                model="replay",
                usage=TokenUsage(),
                final_answer="The question is scoped.",
            )
        if agent_name == "researcher":
            return self._researcher_turn(text)
        return NativeToolTurn(
            model="replay", usage=TokenUsage(), final_answer="Nothing to add."
        )

    def _researcher_turn(self, text: str) -> NativeToolTurn:
        target_match = re.search(r"- target_id=(topic-\d+)", text)
        if target_match is None:
            return NativeToolTurn(
                model="replay", usage=TokenUsage(), final_answer="Done."
            )
        topic = self._topic_for_target(target_match.group(1))
        urls = {source.url for source in topic.sources}
        read_urls = set(self._ids(text, "read_urls"))
        for source in topic.sources:
            if source.url in urls and source.url not in read_urls:
                if source.url in text:
                    return self._tool("web_scraper", {"url": source.url})
        if read_urls & urls:
            return NativeToolTurn(
                model="replay", usage=TokenUsage(), final_answer="The read is done."
            )
        self.searches.append(topic.query)
        return self._tool("web_search", {"query": topic.query})

    def _tool(self, name: str, arguments: dict[str, str]) -> NativeToolTurn:
        import json

        return NativeToolTurn(
            model="replay",
            usage=TokenUsage(),
            tool_calls=(
                NativeToolCall(
                    tool_name=name, arguments_json=json.dumps(arguments)
                ),
            ),
        )

    # -- structured replies ----------------------------------------------
    def _reply(self, name: str, text: str):
        handler = getattr(self, f"_reply_{name}", None)
        if handler is None:
            raise AssertionError(f"no scripted reply for {name}")
        return handler(text)

    def _reply_ResearchPlanDraft(self, text: str):
        self._require(text, self.scenario.question, where="plan draft")
        return ResearchPlanDraft(
            sub_topics=[
                SubTopicDraft(
                    title=topic.title,
                    rationale=topic.rationale,
                    search_queries=[topic.query],
                    success_criteria=list(topic.success_criteria),
                    priority=position,
                    evidence_targets=[
                        EvidenceTargetDraft(
                            question=topic.question,
                            required_dimensions=list(topic.dimensions),
                            critical=topic.critical,
                        )
                    ],
                )
                for position, topic in enumerate(
                    self.scenario.topics, start=1
                )
            ]
        )

    def _reply_PlanReviewDraft(self, text: str):
        self._require(text, self.scenario.question, where="plan review")
        return PlanReviewDraft(
            sound=True,
            missing_dimensions=[],
            atomicity_defects=[],
            unsupported_premises=[],
            repair_instruction="",
        )

    def _reply_SubTopicFindingsDraft(self, text: str):
        findings: list[FindingDraft] = []
        target_match = re.search(r"- target_id=(topic-\d+)", text)
        assert target_match is not None, "extraction packet carried no target"
        topic = self._topic_for_target(target_match.group(1))
        self._require(text, topic.title, where="extraction packet")
        for source in topic.sources:
            read_match = re.search(
                rf"^- read_id=(\S+) requested_url=\S+ resolved_url="
                rf"{re.escape(source.url)} ",
                text,
                re.M,
            )
            assert read_match is not None, (
                f"the packet carried no read of {source.url}"
            )
            read_id = read_match.group(1)
            evidence = re.search(
                rf"^- evidence_id=\S+ read_id={read_id} locator=(\S+) "
                r"targets=\S+ excerpt=(.*)$",
                text,
                re.M,
            )
            assert evidence is not None, (
                f"the packet registered no evidence for {source.url}"
            )
            locator, excerpt = evidence.group(1), evidence.group(2).strip()
            assert source.excerpt in excerpt, (
                f"the read of {source.url} does not entail the claim it is "
                "scripted to support"
            )
            findings.append(
                FindingDraft(
                    content=source.claim,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=source.confidence,
                    read_id=read_id,
                    locator=locator,
                    excerpt=excerpt,
                    target_ids=[target_match.group(1)],
                )
            )
        return SubTopicFindingsDraft(findings=findings)

    def _reply_SourceScoresDraft(self, text: str):
        rows = []
        for url in re.findall(r"https?://\S+", text):
            source = self.by_url.get(url.rstrip(".,)"))
            if source is None:
                continue
            rows.append(
                SourceScoreDraft(
                    url=source.url,
                    authority_score=source.authority,
                    recency_score=source.recency,
                    relevance_score=source.relevance,
                    rationale=f"{source.title} is the declared source.",
                    issuer=source.issuer,
                )
            )
        assert rows, "the source packet carried no scripted URL"
        return SourceScoresDraft(sources=rows)

    def _reply_ClaimsDraft(self, text: str):
        rows = []
        for url in re.findall(r"https?://\S+", text):
            source = self.by_url.get(url.rstrip(".,)"))
            if source is None:
                continue
            rows.append(
                {"text": source.claim, "source_urls": [source.url]}
            )
        assert rows, "the claim packet carried no scripted URL"
        return ClaimsDraft(claims=rows)

    def _reply_ClaimVerdictDraft(self, text: str):
        from deep_research.agents.fact_checker import SupportAssessment

        ids = re.findall(r"^- id: (\S+)", text, re.M)
        assessments = [
            SupportAssessment(
                evidence_id=evidence_id,
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                independent=True,
            )
            for evidence_id in ids
        ]
        verified = len(ids) >= 2
        return ClaimVerdictDraft(
            verdict="verified" if verified else "insufficient_evidence",
            confidence=0.85 if verified else 0.5,
            assessments=assessments,
            support_ids=ids,
            contradiction_ids=[],
            rationale=(
                "Two independent reads state the claim."
                if verified
                else "Only one read states the claim."
            ),
        )

    def _reply_PassageVerdictDraft(self, text: str):
        return PassageVerdictDraft(
            supports=True,
            complete_support=True,
            rationale="The passage carries the complete claim.",
        )

    def _reply_ReportDraft(self, text: str):
        claims = re.findall(
            r"^(C\d+) \[[^\]]*\] (.*) \((https?://\S+)\) coverage=(\S+)$",
            text,
            re.M,
        )
        assert claims, "the synthesis packet carried no checked claims"
        points = [
            ReportPointDraft(
                text=claim_text,
                claim_ids=[claim_id],
                source_urls=[url],
            )
            for claim_id, claim_text, url, _coverage in claims
        ]
        rows = [
            {
                "subject": "Acme widget",
                "dimension": self._dimension_for(coverage, claim_text),
                "finding": claim_text,
                "claim_ids": [claim_id],
                "source_urls": [url],
            }
            for claim_id, claim_text, url, coverage in claims
        ]
        sections = [
            ReportSectionDraft(
                title=self.scenario.topics[index].title, points=[point]
            )
            for index, point in enumerate(points)
        ]
        return ReportDraft(
            executive_summary=points,
            ranked_constraints=[],
            sections=sections,
            uncertainty_notes=[],
            answer_rows=rows,
        )

    def _dimension_for(self, coverage: str, claim_text: str) -> str:
        topic = self._topic_for_target(coverage)
        return topic.dimensions[0] if topic.dimensions else claim_text[:20]

    def _reply_CritiqueDraft(self, text: str):
        self._require(text, self.scenario.question, where="critic packet")
        return CritiqueDraft(
            score=9,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            rationale="The report answers the question from its evidence.",
        )

    def _reply_ReportReviewDraft(self, text: str):
        statement_ids = self._ids(text, "Statement ids in this packet")
        evidence_ids = self._ids(text, "Evidence ids in this packet")
        return ReportReviewDraft(
            dimensions=ReviewDimensionScores(
                completeness=1.0,
                prioritization=1.0,
                evidence_quality=1.0,
                attribution=1.0,
                uncertainty=1.0,
                readability=1.0,
                actionability=1.0,
            ),
            statement_dispositions=[
                StatementDispositionDraft(
                    statement_id=statement_id, disposition="supported"
                )
                for statement_id in statement_ids
            ],
            reviewed_statement_ids=statement_ids,
            reviewed_evidence_ids=evidence_ids,
            rationale="Every statement is carried by the evidence shown.",
        )


class ReplaySearch:
    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.queries: list[str] = []

    def search(self, *, query, search_depth, max_results):
        self.queries.append(query)
        for topic in self.scenario.topics:
            if topic.query == query:
                return {
                    "results": [
                        {
                            "url": source.url,
                            "title": source.title,
                            "content": source.excerpt,
                        }
                        for source in topic.sources
                    ]
                }
        raise AssertionError(f"unscripted search {query!r}")


class ReplayHTTP:
    def __init__(self, scenario: ReplayScenario) -> None:
        self.by_url = sources(scenario)
        self.fetched: list[str] = []

    async def get(self, url, **kwargs):
        import httpx

        request = httpx.Request("GET", url)
        if url.endswith("/robots.txt"):
            return httpx.Response(
                status_code=200,
                text="User-agent: *\nAllow: /\n",
                headers={"content-type": "text/plain"},
                request=request,
            )
        source = self.by_url.get(url)
        if source is None:
            raise AssertionError(f"unscripted page {url!r}")
        self.fetched.append(url)
        return httpx.Response(
            status_code=200,
            text=f"<html><body><article><p>{source.text}</p></article></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )


SCENARIO = ReplayScenario(
    case_id="smoke-positive",
    question="What is the measured efficiency of the Acme widget?",
    topics=(
        ReplayTopic(
            title="Acme widget efficiency",
            question=(
                "What measured efficiency does the official report quote?"
            ),
            dimensions=("efficiency figure",),
            critical=True,
            query="Acme widget measured efficiency official",
            sources=(
                ReplaySource(
                    url="https://official.example/measurement",
                    title="Acme measurement report 2025",
                    text=(
                        "Acme measurement report 2025. The measured "
                        "efficiency of the Acme widget is 42 percent under "
                        "the standard test. The measurement was taken in 2025."
                    ),
                    excerpt=(
                        "The measured efficiency of the Acme widget is 42 "
                        "percent under the standard test."
                    ),
                    claim=(
                        "The measured efficiency of the Acme widget is 42 "
                        "percent."
                    ),
                ),
            ),
        ),
        ReplayTopic(
            title="Acme widget test method",
            question="Which standard test measured the Acme widget?",
            dimensions=("test method",),
            critical=False,
            query="Acme widget standard test method",
            sources=(
                ReplaySource(
                    url="https://official.example/method",
                    title="Acme test method note",
                    text=(
                        "Acme test method note. The Acme widget was measured "
                        "under the standard test ST-4."
                    ),
                    excerpt=(
                        "The Acme widget was measured under the standard "
                        "test ST-4."
                    ),
                    claim=(
                        "The Acme widget was measured under the standard "
                        "test ST-4."
                    ),
                ),
            ),
        ),
        ReplayTopic(
            title="Acme widget measurement year",
            question="In which year was the Acme widget measured?",
            dimensions=("measurement year",),
            critical=False,
            query="Acme widget measurement year 2025",
            sources=(
                ReplaySource(
                    url="https://official.example/year",
                    title="Acme measurement year note",
                    text=(
                        "Acme measurement year note. The Acme measurement "
                        "was taken in 2025."
                    ),
                    excerpt="The Acme measurement was taken in 2025.",
                    claim="The Acme measurement was taken in 2025.",
                ),
            ),
        ),
    ),
    expectation=ReplayExpectation(quality="accepted", exit_code=0),
)


def _debug_statements() -> None:
    from deep_research.agents import report as report_module
    from deep_research.agents import synthesizer as synthesizer_module

    original_packet = synthesizer_module.build_canonical_packet

    def traced_packet(**kwargs):
        packet = original_packet(**kwargs)
        for claim in kwargs["claims"]:
            print(
                "PKT", claim.claim_id[:8],
                claim.verdict,
                claim.evidence_status,
                "sel=", sorted(claim.evidence_selection),
                "targets=", list(claim.target_ids),
                "cluster=", claim.cluster_id[:8],
                "urls=", list(claim.source_urls)[:1],
            )
        print("PKT-EVIDENCE-KEYS", sorted(kwargs["evidence"])[:4], len(kwargs["evidence"]))
        return packet

    synthesizer_module.build_canonical_packet = traced_packet

    original = synthesizer_module.validate_report_statements

    def traced(composition):
        print(
            "COMP evidence=", sorted(composition.evidence_units)[:4],
            len(composition.evidence_units),
            "claims=", [(c.claim_id[:8], sorted(c.evidence_selection)) for c in composition.claims],
        )
        for statement in composition.statements:
            owners = report_module._statement_owner_claims(  # noqa: SLF001
                composition, statement
            )
            print(
                "STM",
                statement.statement_id,
                "substantive=", statement.substantive,
                "evidence=", statement.evidence_ids[:2],
                "claims=", [claim.claim_id for claim in owners][:2],
                "|",
                statement.text[:70],
                "mode=", statement.mode,
                "clusters=", statement.claim_cluster_ids[:2],
            )
        try:
            return original(composition)
        except Exception as error:  # noqa: BLE001
            print("STM-ERROR", type(error).__name__, error)
            return []

    synthesizer_module.validate_report_statements = traced


async def _run() -> None:
    _debug_statements()
    root = Path("scratch/spike3-out")
    settings = ConfigSettings()
    settings = settings.model_copy(
        deep=True,
        update={
            "memory": settings.memory.model_copy(
                deep=True,
                update={
                    "long_term": settings.memory.long_term.model_copy(
                        deep=True,
                        update={
                            "persist_directory": str(root / "memory"),
                            "collection_name": "spike3",
                        },
                    ),
                    "procedural": settings.memory.procedural.model_copy(
                        deep=True,
                        update={
                            "strategies_path": str(root / "procedural.json")
                        },
                    ),
                },
            ),
            "output": settings.output.model_copy(
                deep=True, update={"directory": str(root / "documents")}
            ),
            "agents": settings.agents.model_copy(
                update={"max_iterations": 6, "tool_budget": 10}
            ),
        },
    )
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="spike", api_key=None
        )
    )
    provider = ReplayCompleter(SCENARIO)
    runtime = await build_runtime(
        settings,
        session_id="spike-3",
        tracker=tracker,
        chat_provider=provider,
        long_term=LongTermMemory(
            collection=_InMemoryCollection(),
            embeddings=_DeterministicEmbeddings(),
            tracker=tracker,
        ),
        procedural=ProceduralMemory.from_config(
            settings.memory.procedural, tracker=tracker
        ),
        tavily_api_key="",
        search_client=ReplaySearch(SCENARIO),
        http_client=ReplayHTTP(SCENARIO),
    )
    run = await run_research_graph(
        graph=runtime.graph,
        tracker=tracker,
        session_id="spike-3",
        question=SCENARIO.question,
        max_iterations=2,
    )
    state = run.state
    print("status:", run.status)
    print("calls:", provider.calls)
    for error in state.errors:
        print(
            "  -", error.error_type, "|", error.message[:160],
            "|", json.dumps(error.details)[:300] if error.details else "",
        )
    for claim in state.verified_claims:
        print(
            "  claim-evidence", claim.claim_id[:8],
            claim.evidence_status, claim.verdict,
            sorted(claim.evidence_selection),
            claim.audit_flags,
        )
    print("claims:", len(state.verified_claims))
    for claim in state.verified_claims:
        print("   ", claim.verdict, "|", claim.text[:70])
    print("quality:", state.quality)
    print("review:", None if state.report_review is None else state.report_review.status)
    print("report chars:", len(state.report or ""))
    print(state.report or "")
    (root / "packets").mkdir(parents=True, exist_ok=True)
    for key, value in provider.packets.items():
        (root / "packets" / f"{key.replace(':', '_')}.txt").write_text(
            value, encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(_run())
