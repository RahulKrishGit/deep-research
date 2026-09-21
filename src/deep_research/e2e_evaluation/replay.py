"""Offline replay of the real agent stack against scripted boundaries.

This is the release-proof harness: the six *production* agents, the real
compiled graph, the real terminal reviewer, renderer and publisher, driven
entirely from locally authored fixtures. Nothing here replaces an agent or a
hand-off. The only things substituted are the external boundaries (the chat
provider, the search client, the HTTP client, and long-term memory's vector
store), which is what makes a release case network-zero by construction.

A scenario declares:

* the pages the local fixtures serve (``ReplaySource``), each of which refuses
  an excerpt that is not literally in its own page text, so a scripted claim
  cannot be "entailed" by prose the page does not contain;
* the plan the planner is scripted to write (``ReplayTopic``);
* what the product is expected to do with it (``CaseExpectation``).

Every scripted completion asserts the packet it was asked: the request must
carry the target, the read, or the evidence the reply is written against, and
the reply is refused otherwise. A scripted reply can never populate state the
run did not produce, and it never returns a preassembled ``ResearchState``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import httpx

from deep_research.agents.base import AgentCompleter
from deep_research.agents.claim_clusters import ClaimEquivalenceDraft
from deep_research.agents.critic import CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimsDraft,
    ClaimVerdictDraft,
    PassageVerdictDraft,
    SupportAssessment,
)
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlanExtensionDraft,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.report_review import (
    ReportReviewDraft,
    ReviewBatchDraft,
    ReviewDimensionScores,
    StatementDispositionDraft,
)
from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft
from deep_research.agents.source_evaluator import (
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.agents.synthesizer import (
    AnswerRowDraft,
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
)
from deep_research.evaluation.dependencies import (
    _DeterministicEmbeddings,
    _InMemoryCollection,
)
from deep_research.graph.orchestrator import compile_research_graph
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsage,
    Tracker,
)
from deep_research.providers import NativeToolCall, NativeToolTurn
from deep_research.runtime.assembly import build_runtime
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchState

# The public progress summary stays at its shipped length. A scenario may ask
# for a different one to prove a case cannot pass by enlarging logs, but the
# default a release case runs at is the production value.
OBSERVATION_SUMMARY_CHARS = 200


class ReplayContractError(RuntimeError):
    """A scripted reply was asked for a packet the scenario did not author."""


@dataclass(frozen=True)
class ReplaySource:
    """One locally authored page, its excerpt, and the claim it carries.

    ``excerpt`` is what a scripted read must actually contain for the claim to
    be admissible: the fixture refuses to exist unless the excerpt is a
    literal substring of the page text, so a case cannot script "entailment"
    that its own page does not state.
    """

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
        if not self.excerpt.strip():
            raise ValueError(f"empty excerpt for {self.url}")


@dataclass(frozen=True)
class ReplayTopic:
    """One planned sub-topic and the obligations it carries."""

    title: str
    question: str
    dimensions: tuple[str, ...]
    critical: bool
    query: str
    sources: tuple[ReplaySource, ...]
    success_criteria: tuple[str, ...] = ("A figure is quoted.",)
    rationale: str = "It answers the question."
    required: bool = True
    # The labels the scripted writer puts on this topic's answer row. Both are
    # published only when the row's evidence attests their words, so a scenario
    # names words its own pages state — the composer repairs an unattested cell
    # to ``not stated``, which would leave the row with nothing to answer.
    answer_labels: tuple[str, str] = ("", "")


@dataclass(frozen=True)
class CaseExpectation:
    """What the product is expected to do, separated from whether the test passed.

    The two are different facts and a case that conflates them cannot report
    either honestly: a negative case whose product result is ``partial`` with
    exit 4 has *succeeded as a test*, and a positive fixture with no useful
    claims has failed however clean its exit code looked.
    """

    terminal_quality: str
    exit_code: int
    required_target_ids: tuple[str, ...] = ()
    forbidden_assertions: tuple[str, ...] = ()
    required_gap_kinds: tuple[str, ...] = ()
    allowed_failure_classes: tuple[str, ...] = ()
    minimum_answerable_claims: int = 0
    required_invariants: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayScenario:
    """One fully scripted offline replay of the real stack."""

    case_id: str
    question: str
    topics: tuple[ReplayTopic, ...]
    expectation: CaseExpectation
    version: int = 1
    max_iterations: int = 2
    # What a scripted reply may assert about the packets it is handed. Empty
    # means "the defaults the completer always enforces".
    expected_request_ids: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def sources(self) -> dict[str, ReplaySource]:
        return {
            source.url: source
            for topic in self.topics
            for source in topic.sources
        }

    def topic_for_target(self, target_id: str) -> ReplayTopic:
        """The declared topic behind a planned target or coverage id.

        Both shapes reach a scripted reply: acquisition reports the coverage
        id the plan stamped for a sub-topic, while the extraction packet
        carries the evidence target id, and a scenario must resolve either
        without knowing which packet asked.
        """
        match = re.fullmatch(r"topic-(\d+)(?:-target-(\d+))?", target_id)
        if match is None:
            raise ReplayContractError(
                f"packet named target {target_id!r}, which is not a planned "
                "target or coverage id"
            )
        index = int(match.group(1)) - 1
        if not 0 <= index < len(self.topics):
            raise ReplayContractError(
                f"packet named target {target_id!r}, which this scenario "
                "does not declare"
            )
        return self.topics[index]


def _context_line(text: str, label: str) -> str:
    """Read one ``- label=value`` line out of an acquisition context."""
    match = re.search(rf"(?m)^(?:- )?{re.escape(label)}=(\S*)$", text)
    if match is None or not match.group(1) or match.group(1) == "-":
        return ""
    return match.group(1)


def _context_ids(text: str, label: str) -> list[str]:
    value = _context_line(text, label)
    return [item for item in value.split(",") if item]


def _labelled_ids(text: str, label: str) -> list[str]:
    """Read one ``Label: a, b, c`` inventory line out of a packet."""
    match = re.search(rf"(?m)^{re.escape(label)}: (.*)$", text)
    if match is None:
        return []
    value = match.group(1).strip()
    if not value or value == "(none)":
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class ReplayCompleter(AgentCompleter):
    """Answer every provider call from the scenario, asserting the packet."""

    def __init__(
        self,
        scenario: ReplayScenario,
        *,
        packet_dump: Path | None = None,
    ) -> None:
        self.scenario = scenario
        self.by_url = scenario.sources
        self.calls: list[str] = []
        self.packets: dict[str, str] = {}
        self.packet_sequence: list[tuple[str, str]] = []
        self.search_queries: list[str] = []
        self.packet_dump = packet_dump
        self.equivalence_pairs: tuple[tuple[int, int], ...] = ()
        self.review_failure: bool = False
        self.rejected_statement_ids: frozenset[str] = frozenset()
        self.critic_score: int = 9

    # --- helpers --------------------------------------------------------
    def _text(self, messages: Sequence[Any]) -> str:
        return "\n".join(message.content for message in messages)

    def _require(self, text: str, needle: str, *, where: str) -> None:
        if needle not in text:
            raise ReplayContractError(
                f"{where}: request did not carry {needle!r}"
            )

    def _record(
        self, agent_name: str | None, schema_name: str, text: str
    ) -> None:
        key = f"{agent_name}:{schema_name}"
        self.calls.append(key)
        self.packets[key] = text
        self.packet_sequence.append((key, text))
        if self.packet_dump is not None:
            # A colon in a Windows filename opens an NTFS alternate data
            # stream, so a dump named ``001-planner:react`` lands invisibly
            # inside ``001-planner``.
            label = key.replace(":", "-")
            self.packet_dump.mkdir(parents=True, exist_ok=True)
            (self.packet_dump / f"{len(self.calls):03d}-{label}.txt").write_text(
                text, encoding="utf-8"
            )

    def packets_carrying(self, needle: str) -> list[str]:
        """The request labels whose packet carried ``needle``, in order."""
        return [
            f"{index:03d}-{key}"
            for index, (key, text) in enumerate(self.packet_sequence, start=1)
            if needle in text
        ]

    def _topic_for_target(self, target_id: str) -> ReplayTopic:
        return self.scenario.topic_for_target(target_id)

    # --- provider surface -----------------------------------------------
    async def complete_structured(
        self,
        messages: Sequence[Any],
        schema: type[Any],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        del max_tokens
        name = schema.__name__
        text = self._text(messages)
        self._record(agent_name, name, text)
        return self._reply(name, text)

    async def complete_react(
        self,
        messages: Sequence[Any],
        tools: Sequence[Any],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        del tools, max_tokens
        text = self._text(messages)
        self._record(agent_name, "react", text)
        if agent_name == "planner":
            self._require(
                text, self.scenario.question, where="planner scoping"
            )
            return self._final("The question is scoped.")
        if agent_name == "researcher":
            return self._researcher_turn(text)
        return self._final("Nothing to add.")

    def _final(self, answer: str) -> NativeToolTurn:
        return NativeToolTurn(
            model="replay", usage=TokenUsage(), final_answer=answer
        )

    def _tool(self, name: str, arguments: dict[str, str]) -> NativeToolTurn:
        return NativeToolTurn(
            model="replay",
            usage=TokenUsage(),
            tool_calls=(
                NativeToolCall(
                    tool_name=name, arguments_json=json.dumps(arguments)
                ),
            ),
        )

    def _researcher_turn(self, text: str) -> NativeToolTurn:
        """Script the acquisition policy's own next action for one topic.

        The packet prints each state field as ``- label=value``. The prototype
        this replaced read ``read_urls:`` and therefore asked again for pages
        it had already read, which the tool policy refused eight times a run.
        """
        target_id = re.search(r"- target_id=(topic-\d+)", text)
        if target_id is None:
            return self._final("Nothing further is required.")
        topic = self._topic_for_target(target_id.group(1))
        urls = [source.url for source in topic.sources]
        read_urls = set(_context_ids(text, "read_urls"))
        candidate_urls = set(_context_ids(text, "candidate_urls"))
        attempted_urls = set(_context_ids(text, "attempted_urls"))
        denied_urls = set(_context_ids(text, "denied_urls"))
        for url in urls:
            if url in read_urls or url in denied_urls:
                continue
            if url in candidate_urls:
                return self._tool("web_scraper", {"url": url})
            if url in text and url not in attempted_urls:
                return self._tool("web_scraper", {"url": url})
        if all(url in read_urls or url in denied_urls for url in urls):
            return self._final("The reads for this topic are complete.")
        if topic.query in self.search_queries:
            return self._final("No further candidate is available.")
        self.search_queries.append(topic.query)
        return self._tool("web_search", {"query": topic.query})

    # --- structured replies ----------------------------------------------
    def _reply(self, name: str, text: str) -> Any:
        handler = getattr(self, f"_reply_{name}", None)
        if handler is None:
            raise ReplayContractError(f"no scripted reply for {name}")
        return handler(text)

    def _reply_ResearchPlanDraft(self, text: str) -> ResearchPlanDraft:
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

    def _reply_PlanReviewDraft(self, text: str) -> PlanReviewDraft:
        self._require(text, self.scenario.question, where="plan review")
        return PlanReviewDraft(
            sound=True,
            missing_dimensions=[],
            atomicity_defects=[],
            unsupported_premises=[],
            repair_instruction="",
        )

    def _reply_PlanExtensionDraft(self, text: str) -> PlanExtensionDraft:
        self._require(text, self.scenario.question, where="plan extension")
        return PlanExtensionDraft(sub_topics=[])

    def _reply_SubTopicFindingsDraft(self, text: str) -> SubTopicFindingsDraft:
        target_match = re.search(r"- target_id=(topic-\d+)", text)
        if target_match is None:
            raise ReplayContractError("extraction packet carried no target")
        target_id = target_match.group(1)
        topic = self._topic_for_target(target_id)
        self._require(text, topic.title, where="extraction packet")
        findings: list[FindingDraft] = []
        for source in topic.sources:
            read_match = re.search(
                rf"^- read_id=(\S+) requested_url=\S+ resolved_url="
                rf"{re.escape(source.url)} ",
                text,
                re.M,
            )
            if read_match is None:
                # A source the run never read is not evidence, and a provider
                # cannot extract a finding from a page it was not shown. The
                # scenario's own expectations assert the reads it requires.
                continue
            read_id = read_match.group(1)
            evidence = re.search(
                rf"^- evidence_id=(\S+) read_id={read_id} locator=(\S+) "
                r"targets=(\S+) excerpt=(.*)$",
                text,
                re.M,
            )
            if evidence is None:
                raise ReplayContractError(
                    f"the packet registered no evidence for {source.url}"
                )
            locator, excerpt = evidence.group(2), evidence.group(4).strip()
            if source.excerpt not in excerpt:
                raise ReplayContractError(
                    f"the read of {source.url} does not entail the claim it "
                    "is scripted to support"
                )
            targets = evidence.group(3).split(",")
            findings.append(
                FindingDraft(
                    content=source.claim,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=source.confidence,
                    read_id=read_id,
                    locator=locator,
                    excerpt=excerpt,
                    target_ids=[value for value in targets if value != "-"],
                )
            )
        return SubTopicFindingsDraft(findings=findings)

    def _reply_SourceScoresDraft(self, text: str) -> SourceScoresDraft:
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
        if not rows:
            raise ReplayContractError(
                "the source packet carried no scripted URL"
            )
        return SourceScoresDraft(sources=rows)

    def _reply_ClaimsDraft(self, text: str) -> ClaimsDraft:
        rows = []
        for url in re.findall(r"https?://\S+", text):
            source = self.by_url.get(url.rstrip(".,)"))
            if source is None:
                continue
            rows.append(
                {"text": source.claim, "source_urls": [source.url]}
            )
        if not rows:
            raise ReplayContractError(
                "the claim packet carried no scripted URL"
            )
        return ClaimsDraft(claims=rows)

    def claim_source(self, text: str) -> ReplaySource:
        """The scenario's declared source for the claim a packet carries."""
        for source in self.scenario.sources.values():
            if source.claim in text:
                return source
        raise ReplayContractError(
            "the packet carried no scripted claim"
        )

    def _reply_ClaimVerdictDraft(self, text: str) -> ClaimVerdictDraft:
        ids = re.findall(r"^- id: (\S+)", text, re.M)
        if not ids:
            raise ReplayContractError(
                "the adjudication packet carried no evidence"
            )
        source = self.claim_source(text)
        supporting = ids if source.verdict == "verified" else ids[:1]
        return ClaimVerdictDraft(
            verdict=source.verdict,
            confidence=0.85 if source.verdict == "verified" else 0.5,
            assessments=[
                SupportAssessment(
                    evidence_id=evidence_id,
                    stance="supports",
                    complete_support=True,
                    scope_compatible=True,
                    independent=True,
                )
                for evidence_id in ids
            ],
            support_ids=supporting,
            contradiction_ids=[],
            rationale=(
                "The packet's reads state the claim."
                if source.verdict == "verified"
                else "One read states the claim."
            ),
        )

    def _reply_PassageVerdictDraft(self, text: str) -> PassageVerdictDraft:
        del text
        return PassageVerdictDraft(
            supports=True,
            complete_support=True,
            rationale="The passage carries the complete claim.",
        )

    def _reply_ClaimEquivalenceDraft(self, text: str) -> ClaimEquivalenceDraft:
        """Propose the duplicate pairs the scenario declares, and only those.

        Positions are the numbers the prompt showed; a scenario that wants two
        paraphrases merged states the positions here rather than relying on a
        model's judgement, and one that wants them kept apart proposes nothing.
        """
        del text
        return ClaimEquivalenceDraft(
            pairs=[
                {"left": left, "right": right}
                for left, right in self.equivalence_pairs
            ]
        )

    def packet_claims(self, text: str) -> list[tuple[str, str, str, str]]:
        """Read the synthesis packet's ``C001 [badge] text (url) coverage=…`` rows."""
        rows = re.findall(
            r"^(C\d+) \[[^\]]*\] (.*) \((https?://\S+)\) coverage=(\S+)$",
            text,
            re.M,
        )
        if not rows:
            raise ReplayContractError(
                "the synthesis packet carried no checked claims"
            )
        return rows

    def _reply_ReportDraft(self, text: str) -> ReportDraft:
        rows = self.packet_claims(text)
        points = [
            ReportPointDraft(
                text=claim_text,
                claim_ids=[claim_id],
                source_urls=[url],
            )
            for claim_id, claim_text, url, _coverage in rows
        ]
        answer_rows = [
            AnswerRowDraft(
                subject=self.answer_labels_for(coverage, claim_text)[0],
                dimension=self.answer_labels_for(coverage, claim_text)[1],
                finding=claim_text,
                claim_ids=[claim_id],
                source_urls=[url],
            )
            for claim_id, claim_text, url, coverage in rows
        ]
        sections = [
            ReportSectionDraft(
                title=self.scenario.topics[index].title, points=[point]
            )
            for index, point in enumerate(points)
        ]
        return ReportDraft(
            # One point per section, and no executive summary: the composer
            # refuses the same fact twice under one section, and a restatement
            # in the summary would be the same (text, cluster) pair the
            # section already carries. The answer rows are the direct answer.
            executive_summary=[],
            ranked_constraints=[],
            sections=sections,
            uncertainty_notes=[],
            answer_rows=answer_rows,
        )

    def answer_labels_for(self, coverage: str, claim_text: str) -> tuple[str, str]:
        """The subject and dimension cells for one coverage id's answer row."""
        topic = self._topic_for_target(coverage)
        subject, dimension = topic.answer_labels
        if subject and dimension:
            return subject, dimension
        return topic.title, topic.dimensions[0] if topic.dimensions else claim_text

    def _reply_CritiqueDraft(self, text: str) -> CritiqueDraft:
        self._require(text, self.scenario.question, where="critic packet")
        return CritiqueDraft(
            score=self.critic_score,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            rationale="The report answers the question from its evidence.",
        )

    def _reply_ReportReviewDraft(self, text: str) -> ReportReviewDraft:
        if self.review_failure:
            raise ValueError("the semantic review was not made")
        statement_ids = _labelled_ids(text, "Statement ids in this packet")
        evidence_ids = _labelled_ids(text, "Evidence ids in this packet")
        if not statement_ids:
            raise ReplayContractError(
                "the review packet listed no statement ids"
            )
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
                    statement_id=statement_id, disposition=self.disposition_for(
                        statement_id
                    )
                )
                for statement_id in statement_ids
            ],
            reviewed_statement_ids=statement_ids,
            reviewed_evidence_ids=evidence_ids,
            rationale="Every statement is carried by the evidence shown.",
        )

    def _reply_ReviewBatchDraft(self, text: str) -> ReviewBatchDraft:
        batch = re.search(r"(?m)^Evidence batch under review: (\S+)$", text)
        statement_ids = [
            match.strip()
            for match in re.findall(r"(?m)^- ([SAC]\d+) \[", text)
        ]
        evidence_ids = re.findall(r"(?m)^### (ev-\S+)$", text)
        if batch is None or not statement_ids:
            raise ReplayContractError(
                "the batch review packet carried no batch id or statement"
            )
        return ReviewBatchDraft(
            batch_id=batch.group(1),
            statement_dispositions=[
                StatementDispositionDraft(
                    statement_id=statement_id,
                    disposition=self.disposition_for(statement_id),
                )
                for statement_id in statement_ids
            ],
            reviewed_statement_ids=statement_ids,
            reviewed_evidence_ids=evidence_ids,
            problem="",
        )

    def disposition_for(self, statement_id: str) -> str:
        """The disposition the scenario's reviewer records for one statement.

        A primary-source claim is ``attributed`` — the exact badge that is not
        an independent pair — and the framing cells the composer wrote for
        itself are ``supported`` at that level too. A scenario can script a
        refusal for one id, which is how a case makes the terminal review
        reject rather than accept a report.
        """
        if statement_id in self.rejected_statement_ids:
            return "unsupported"
        return "supported"


class ReplaySearch:
    """Answer searches from the scenario's declared topic inventory."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.queries: list[str] = []

    def search(
        self,
        *,
        query: str,
        search_depth: Any = None,
        max_results: Any = None,
    ) -> dict[str, Any]:
        del search_depth, max_results
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
        raise ReplayContractError(f"unscripted search {query!r}")


class ReplayHTTP:
    """Serve the scenario's pages, and robots.txt, without a socket."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.by_url = scenario.sources
        self.fetched: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        del kwargs
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
            raise ReplayContractError(f"unscripted page {url!r}")
        self.fetched.append(url)
        return httpx.Response(
            status_code=200,
            text=(
                "<html><body><article><p>"
                f"{source.text}"
                "</p></article></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )


@contextmanager
def network_denied() -> Iterator[list[str]]:
    """Refuse every socket connect, and record what was attempted.

    Guarding the socket layer rather than one HTTP client is deliberate: it is
    the boundary every transport — httpx, requests, the provider SDKs, the
    vector store, LangSmith — has to cross.
    """
    import socket

    attempts: list[str] = []
    original_socket = socket.socket
    original_socketpair = socket.socketpair
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    class _DeniedSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, address: Any) -> None:
            attempts.append(str(address))
            raise AssertionError(f"network access attempted: {address!r}")

        def connect_ex(self, address: Any) -> int:
            attempts.append(str(address))
            raise AssertionError(f"network access attempted: {address!r}")

    def _denied_create_connection(
        address: Any, *args: Any, **kwargs: Any
    ) -> Any:
        del args, kwargs
        attempts.append(str(address))
        raise AssertionError(f"network access attempted: {address!r}")

    def _denied_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        del kwargs
        attempts.append(str(args[0]) if args else "")
        raise AssertionError("network name resolution attempted")

    def _permitted_socketpair(*args: Any, **kwargs: Any) -> Any:
        # asyncio builds its event loop's self-pipe out of a socket pair. On
        # Windows the pure-Python fallback does that with two loopback sockets
        # that ``connect`` to each other, which is process-local plumbing and
        # not network access. The pair is built through the real constructor
        # for the duration of the call only.
        socket.socket = original_socket  # type: ignore[misc]
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            socket.socket = _DeniedSocket  # type: ignore[misc]

    socket.socket = _DeniedSocket  # type: ignore[misc]
    socket.socketpair = _permitted_socketpair  # type: ignore[assignment]
    socket.create_connection = _denied_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = _denied_getaddrinfo  # type: ignore[assignment]
    try:
        yield attempts
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socket.socketpair = original_socketpair  # type: ignore[assignment]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]


def replay_settings(
    scenario: ReplayScenario,
    *,
    root: Path,
    observation_summary_chars: int = OBSERVATION_SUMMARY_CHARS,
    base: ConfigSettings | None = None,
) -> ConfigSettings:
    """The production settings, redirected to local storage and the replay.

    ``base`` is the settings the run was actually started with — the CLI's own
    load of the shipped ``config.yaml`` — so a replay keeps every production
    value and moves only the storage paths a test must own.
    """
    settings = ConfigSettings() if base is None else base
    return settings.model_copy(
        deep=True,
        update={
            "memory": settings.memory.model_copy(
                deep=True,
                update={
                    "long_term": settings.memory.long_term.model_copy(
                        deep=True,
                        update={
                            "persist_directory": str(root / "memory"),
                            "collection_name": f"replay-{scenario.case_id}",
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
                deep=True,
                update={"observation_summary_chars": observation_summary_chars},
            ),
        },
    )


@dataclass
class ReplayRuntime:
    """The real runtime, its scripted boundaries, and the agents it built."""

    runtime: Any
    scenario: ReplayScenario
    completer: ReplayCompleter
    search: ReplaySearch
    http: ReplayHTTP
    tracker: Tracker
    agents: Any
    long_term: LongTermMemory
    procedural: ProceduralMemory


async def build_replay_runtime(
    scenario: ReplayScenario,
    *,
    root: Path,
    session_id: str,
    settings: ConfigSettings | None = None,
    observation_summary_chars: int = OBSERVATION_SUMMARY_CHARS,
    long_term: LongTermMemory | None = None,
    packet_dump: Path | None = None,
) -> ReplayRuntime:
    """Build the production runtime with every external boundary scripted.

    ``build_runtime`` compiles the graph, so the six agents it constructed are
    captured as it hands them to the compiler: the release proof has to assert
    the runtime really holds the production classes, not a double wearing
    their names.
    """
    resolved = settings or replay_settings(
        scenario,
        root=root,
        observation_summary_chars=observation_summary_chars,
    )
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="replay", api_key=None
        )
    )
    completer = ReplayCompleter(scenario, packet_dump=packet_dump)
    search = ReplaySearch(scenario)
    http = ReplayHTTP(scenario)
    captured: dict[str, Any] = {}
    original_compile = compile_research_graph

    def _capturing_compile(agents: Any, **kwargs: Any) -> Any:
        captured["agents"] = agents
        return original_compile(agents, **kwargs)

    memory = long_term or LongTermMemory(
        collection=_InMemoryCollection(),
        embeddings=_DeterministicEmbeddings(),
        tracker=tracker,
    )
    procedural = ProceduralMemory.from_config(
        resolved.memory.procedural, tracker=tracker
    )
    import deep_research.runtime.assembly as assembly

    assembly.compile_research_graph = _capturing_compile
    try:
        runtime = await build_runtime(
            resolved,
            session_id=session_id,
            tracker=tracker,
            chat_provider=completer,
            long_term=memory,
            procedural=procedural,
            tavily_api_key="",
            search_client=search,
            http_client=http,
        )
    finally:
        assembly.compile_research_graph = original_compile
    if "agents" not in captured:
        raise ReplayContractError(
            "the production runtime did not compile a research graph"
        )
    return ReplayRuntime(
        runtime=runtime,
        scenario=scenario,
        completer=completer,
        search=search,
        http=http,
        tracker=tracker,
        agents=captured["agents"],
        long_term=memory,
        procedural=procedural,
    )


@dataclass
class ReplayRun:
    """One completed replay: the real state, its graph run, and its artifacts."""

    scenario: ReplayScenario
    session_id: str
    state: ResearchState
    graph_run: Any
    replay: ReplayRuntime
    exit_code: int
    cli_output: list[str]

    @property
    def quality_status(self) -> str:
        from deep_research.graph.state import graph_quality_status

        return graph_quality_status(self.state)

    @property
    def report(self) -> str:
        return self.state.report or ""

    @property
    def statements(self) -> list[Any]:
        composition = self.state.composition
        return list(composition.statements) if composition is not None else []

    def answered_target_ids(self) -> list[str]:
        from deep_research.utils.types import (
            counted_evidence_targets,
            target_is_answered,
        )

        return [
            target.target_id
            for topic in self.state.sub_topics
            for target in counted_evidence_targets(topic.evidence_targets)
            if target_is_answered(self.state, target)
        ]

    def error_types(self) -> list[str]:
        return [error.error_type for error in self.state.errors]


def production_config_path() -> Path:
    """The shipped ``config.yaml`` the real CLI would load.

    The CLI resolves the path against the working directory, which is where a
    real user runs it from; a harness that runs from elsewhere still loads the
    repository's own file rather than silently defaulting to nothing.
    """
    from deep_research.main import DEFAULT_CONFIG_PATH

    local = Path(DEFAULT_CONFIG_PATH)
    if local.is_file():
        return local
    candidate = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_PATH
    if candidate.is_file():
        return candidate
    raise ReplayContractError(f"no shipped {DEFAULT_CONFIG_PATH} to run against")

@contextmanager
def offline_credentials() -> Iterator[None]:
    """Declare the credential *names* the CLI's strict load insists on.

    ``load_config(..., strict=True)`` refuses a run whose configured stack has
    no credentials at all, and the shipped settings enable LangSmith tracing.
    A replay dials nothing — every transport is scripted and ``network_denied``
    proves it — so these are placeholders that exist only to let the shipped
    entrypoint reach its own runtime build, and tracing is off for the same
    reason. Values are restored on exit.
    """
    import os

    provided = {
        "DEEPSEEK_API_KEY": "replay-offline-placeholder",
        "TAVILY_API_KEY": "replay-offline-placeholder",
        "LANGSMITH_TRACING": "false",
    }
    previous = {name: os.environ.get(name) for name in provided}
    os.environ.update(provided)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_replay_scenario(
    scenario: ReplayScenario,
    *,
    root: Path,
    session_id: str | None = None,
    repetition: int = 1,
    observation_summary_chars: int = OBSERVATION_SUMMARY_CHARS,
    long_term: LongTermMemory | None = None,
    settings: ConfigSettings | None = None,
    require_quality: bool = True,
    packet_dump: Path | None = None,
) -> ReplayRun:
    """Run one scenario through the real CLI entrypoint and return its run.

    The entrypoint is the production ``deep_research.cli.main``: argument
    parsing, the progress stream, the summary rendering and the exit-code
    mapping are all the shipped ones. What is injected is the *runtime*, and
    the runtime is built by ``runtime.assembly.build_runtime`` with scripted
    external clients — so the six agents, the graph, the reviewer, the
    renderer and the publisher are production code end to end.
    """
    import asyncio
    from io import StringIO

    from deep_research.cli import main as cli_main
    from deep_research.main import run_research

    resolved_session = session_id or f"replay-{scenario.case_id}-r{repetition}"
    holder: dict[str, Any] = {}

    async def builder(current: ConfigSettings, *, session_id: str) -> Any:
        # The CLI's own load of the shipped config.yaml is what the run is
        # started with; the replay redirects its storage surfaces and keeps
        # every other production value, unless the caller pinned settings.
        effective = settings or replay_settings(
            scenario,
            root=root,
            observation_summary_chars=observation_summary_chars,
            base=current,
        )
        replay = await build_replay_runtime(
            scenario,
            root=root,
            session_id=session_id,
            settings=effective,
            long_term=long_term,
            packet_dump=packet_dump,
        )
        holder["settings"] = effective
        holder["replay"] = replay
        return replay.runtime

    def runner(**kwargs: Any) -> Any:
        outcome = asyncio.run(
            run_research(
                question=kwargs.get("question"),
                session_id=resolved_session,
                config_path=str(production_config_path()),
                max_iterations=scenario.max_iterations,
                output_format=kwargs.get("output_format"),
                config_overrides=kwargs.get("config_overrides"),
                runtime_builder=builder,
                event_handler=kwargs.get("event_handler"),
                request_budget_handler=kwargs.get("request_budget_handler"),
            )
        )
        holder["outcome"] = outcome
        return outcome

    argv = [scenario.question]
    if require_quality:
        argv.append("--require-quality")
    stream = StringIO()
    with offline_credentials():
        exit_code = cli_main(argv, runner=runner, stream=stream)
    replay = holder["replay"]
    outcome = holder["outcome"]
    return ReplayRun(
        scenario=scenario,
        session_id=resolved_session,
        state=outcome.state,
        graph_run=outcome,
        replay=replay,
        exit_code=exit_code,
        cli_output=stream.getvalue().splitlines(),
    )


__all__ = [
    "OBSERVATION_SUMMARY_CHARS",
    "CaseExpectation",
    "ReplayCompleter",
    "ReplayContractError",
    "ReplayHTTP",
    "ReplayRun",
    "ReplayRuntime",
    "ReplayScenario",
    "ReplaySearch",
    "ReplaySource",
    "ReplayTopic",
    "build_replay_runtime",
    "network_denied",
    "offline_credentials",
    "production_config_path",
    "replay_settings",
    "run_replay_scenario",
]

