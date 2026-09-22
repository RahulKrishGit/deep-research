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
from typing import Any, Iterator, Literal

import httpx

from deep_research.agents.acquisition import (
    _required_reader,
    build_read_record_from_tool_result,
)
from deep_research.agents.base import AgentCompleter
from deep_research.agents.claim_clusters import ClaimEquivalenceDraft
from deep_research.agents.critic import CritiqueDraft
from deep_research.agents.evidence import normalized_content_sha256, read_identity
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
from deep_research.agents.sources import normalize_source_url
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
from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsage,
    Tracker,
)
from deep_research.providers import NativeToolCall, NativeToolTurn
from deep_research.providers.contracts import ProviderError
from deep_research.runtime.assembly import build_runtime
from deep_research.tools.base import ToolResult
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ReadRecord, ResearchState

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
    # The score the Source Evaluator is scripted to write for this page. The
    # role and the transport relation are not decoration: an ``unknown`` role
    # has no claim-specific origin, so a page left at the shipped default can
    # support a statement but can never be half of an independent pair. Both
    # fields are only recorded when the page evidences an issuer, which is why
    # a fixture states one ("Published by ...") rather than merely mentioning
    # the name in a sentence about somebody else.
    source_role: str = "original_report"
    transport_relation: str = "original"
    report_number: str = ""
    # Which search for this topic's query first surfaces the page. A page the
    # first search does not return is a page the second one found: that is how
    # a refinement round discovers evidence the opening round did not have,
    # and it is a property of the page rather than of the run.
    discovered_on_search: int = 1
    # The status the host answers with. A page that answers 403 is a page that
    # was found and refused: the run has to record the denial and stop asking,
    # which is a different fact from a page that was never found.
    status_code: int = 200
    # ``text/html`` is a page and ``application/pdf`` is a document: the
    # document path is served as real PDF bytes so the production extractor,
    # not the fixture, is what reads it.
    content_type: str = "text/html; charset=utf-8"
    # The read an earlier session stored for this page, seeded into the run's
    # cache before the graph starts. ``valid`` is a stored read of the page's
    # own text; ``stale`` is a stored read of ``cached_text``, the version that
    # session saw, which the live page no longer is; ``forged`` stores a body
    # while declaring the digest of another one, so its provenance is one no
    # reader could have earned. The last two state the body they store, because
    # an artifact whose body is unstated is not an artifact at all.
    cache_artifact: Literal["", "valid", "stale", "forged"] = ""
    cached_text: str = ""

    def __post_init__(self) -> None:
        if self.excerpt not in self.text:
            raise ValueError(f"excerpt not in text for {self.url}")
        if not self.excerpt.strip():
            raise ValueError(f"empty excerpt for {self.url}")
        if self.cached_text and self.cache_artifact in ("", "valid"):
            raise ValueError(
                f"cached_text for {self.url} states a body only a stale or "
                "forged artifact stores"
            )
        if self.cache_artifact in ("stale", "forged"):
            if not self.cached_text:
                raise ValueError(
                    f"the {self.cache_artifact} artifact for {self.url} must "
                    "state the body it stores"
                )
            if self.cached_text == self.text:
                raise ValueError(
                    f"the {self.cache_artifact} artifact for {self.url} stores "
                    "this page's own text, so it claims nothing false"
                )
        if (
            self.cache_artifact == "stale"
            and self.excerpt not in self.cached_text
        ):
            raise ValueError(
                f"the stored body of {self.url} does not state the excerpt its "
                "claim rests on"
            )


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
    # Further queries this topic's plan carries, in the order the run would
    # issue them. A topic whose second round finds the second publisher needs
    # one, because the run's own policy refuses a query it has already
    # attempted: the evidence a later round recovers is reached through the
    # plan's next query, which is what a real plan carries and a repeated
    # string is not.
    follow_up_queries: tuple[str, ...] = ()
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
    """Gaps a case tolerates because its own fixture makes them correct.

    A negative case is one whose product result is a *partial* one, and the
    run that produces it records why: a topic whose obligation is already
    discharged is skipped rather than re-researched, a resolved topic reports
    no further findings, a review that could not be made is named. Those are
    the run telling the truth about a partial pass, and a case that declared
    them unexpected would be asserting its own fixture cannot happen. Nothing
    else is tolerated: a class not listed here fails the case.
    """
    minimum_answerable_claims: int = 0
    required_invariants: tuple[str, ...] = ()
    required_report_phrases: tuple[str, ...] = ()
    """Words the published report has to contain.

    A decisive assertion like "both sources are cited" is a fact about the
    artifact, so a case states it as text the reader would see rather than as
    a count of records no reader was shown.
    """


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
    # Paraphrase pairs the scripted equivalence pass proposes, as positions in
    # the packet it was handed. A scenario that wants two paraphrases merged
    # states them; one that wants them kept apart proposes nothing.
    equivalence_pairs: tuple[tuple[int, int], ...] = ()
    # The terminal review that could not be made: the provider raises, exactly
    # as an outage would, and the run has to record the absent judgement
    # instead of accepting an unreviewed report.
    review_failure: bool = False
    # Claim text the scripted composer tries to publish that no page states.
    # The product is the thing under test here: prose no citation attests must
    # not reach the reader, whatever the writer proposed.
    invented_prose: str = ""
    # What long-term memory already holds when the run starts, written through
    # the production memory bridge before the graph is compiled. A fixture
    # states these when its subject is what the run does with a lead it has
    # already been given: memory recalled at startup is a lead, and whether it
    # is treated as a read is the run's decision, not the fixture's.
    memory_entries: tuple[MemoryEntry, ...] = ()

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
        self.recalled_targets: set[str] = set()
        self.packet_dump = packet_dump
        self.equivalence_pairs: tuple[tuple[int, int], ...] = ()
        self.review_failure: bool = False
        self.rejected_statement_ids: frozenset[str] = frozenset()
        self.critic_score: int = 9
        self.invented_prose: str = ""

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

        How many times this policy may search for one topic is the fixture's
        own declaration: a page the topic declares on the second search is a
        page that only exists after a second search, so a run whose obligation
        is still open has to be able to buy one. That is also where the policy
        stops — a topic whose pages are all read or refused ends the loop
        rather than searching for evidence that is not there.

        The reader is chosen by the same rule the product enforces: a document
        asked for with ``web_scraper`` is refused outright, so a script that
        always scraped would spend a topic's whole iteration budget on
        refusals and read none of the documents the scenario declared.
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
                return self._read(url)
            if url in text and url not in attempted_urls:
                return self._read(url)
        if all(url in read_urls or url in denied_urls for url in urls):
            # A topic with nothing to read is not a topic with nothing to do:
            # the obligations an earlier session left open are what long-term
            # memory holds, and a sub-topic that declares no page of its own
            # has only those leads to go on. Asking once per target is what
            # makes the recall a step of the run rather than a thing the
            # provider forgot to do - and the leads it returns are offered as
            # candidates, which is the decision the next packet carries them
            # into.
            target = target_id.group(1)
            if (
                not urls
                and self.scenario.memory_entries
                and target not in self.recalled_targets
            ):
                self.recalled_targets.add(target)
                return self._tool("query_memory", {"query": topic.query})
            return self._final("The reads for this topic are complete.")
        pending = [
            candidate
            for candidate in (topic.query, *topic.follow_up_queries)
            if candidate not in self.search_queries
        ]
        if not pending:
            return self._final("No further candidate is available.")
        self.search_queries.append(pending[0])
        return self._tool("web_search", {"query": pending[0]})

    def _read(self, url: str) -> NativeToolTurn:
        """The read tool the acquisition policy requires for one candidate.

        The suffix table is the product's own, not a copy of it: a fixture
        that routed documents to the scraper would be refused by the policy
        and read nothing, and a copy of the table here would be free to drift
        away from the rule the refusal is made by.
        """
        if _required_reader(url) == "document_reader":
            return self._tool("document_reader", {"source": url})
        return self._tool("web_scraper", {"url": url})

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
                    search_queries=[
                        topic.query,
                        *topic.follow_up_queries,
                    ],
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
                    source_role=source.source_role,
                    transport_relation=source.transport_relation,
                    report_number=source.report_number,
                )
            )
        if not rows:
            raise ReplayContractError(
                "the source packet carried no scripted URL"
            )
        return SourceScoresDraft(sources=rows)

    def _reply_ClaimsDraft(self, text: str) -> ClaimsDraft:
        """One claim row per distinct scripted claim, with every URL stating it.

        ``ClaimDraft.source_urls`` is a list for exactly this reason: the unit
        of a claim is the assertion, not the page, so two reads that state one
        fact are one claim carrying two sources — which is what the identity
        and independence tests downstream are given to judge. Emitting one row
        per URL instead makes the second row a duplicate of the first by the
        claim's own fingerprint, and ingestion refuses it, so a corroborated
        claim could never be presented at all.
        """
        rows: dict[str, list[str]] = {}
        for url in re.findall(r"https?://\S+", text):
            source = self.by_url.get(url.rstrip(".,)"))
            if source is None:
                continue
            urls = rows.setdefault(source.claim, [])
            if source.url not in urls:
                urls.append(source.url)
        if not rows:
            raise ReplayContractError(
                "the claim packet carried no scripted URL"
            )
        return ClaimsDraft(
            claims=[
                {"text": claim, "source_urls": urls}
                for claim, urls in rows.items()
            ]
        )

    def claim_source(self, text: str) -> ReplaySource:
        """The page a packet's verdict is scripted from: the claim's own page.

        Only a page that delivered a body counts. A page the host refused was
        never read, so no claim was ever extracted from it and its scripted
        verdict is not the verdict of the claim its text would have stated:
        letting a 403 landing page decide would script the strict badge away
        for a fact two readable documents state, and the run would be judged
        on a refusal that carried no text at all.

        And only a page *this packet selected* counts, for the claim this
        packet states it is judging. A packet prints every candidate's own
        text, so a page's declared claim is in the packet text merely by being
        a candidate in it: the moment a pool legitimately widens - to the
        sub-topic's other reads, say - a note that states no value would
        decide the verdict for the figure its neighbour measures. The search
        therefore runs over the pages the packet's own evidence ids name, and
        matches the claim statement the packet prints, never a sentence that
        happens to be quoted in it.
        """
        statement = self.claim_statement(text)
        for source in self.packet_sources(text):
            if source.status_code != 200:
                continue
            if source.claim in statement:
                return source
        raise ReplayContractError(
            "the packet carried no scripted claim"
        )

    def packet_sources(self, text: str) -> list[ReplaySource]:
        """The declared pages this packet's own evidence ids name.

        In the scenario's declared order, so a packet carrying two pages that
        state one claim resolves the way it always did. A packet that names no
        evidence selected nothing to search, so the scenario's registry stands
        in: that is the shape a caller hands in when all it has is a claim.
        """
        named = {
            source.url
            for evidence_id in re.findall(r"^- id: (\S+)", text, re.M)
            if (source := self.evidence_source(text, evidence_id)) is not None
        }
        if not named:
            return list(self.scenario.sources.values())
        return [
            source
            for source in self.scenario.sources.values()
            if source.url in named
        ]

    def claim_statement(self, text: str) -> str:
        """The claim a packet declares it is judging - its own ``# Claim``.

        The definition the packet's candidate list is an answer to. A packet
        that names no claim states none, so its whole text stands: that is the
        shape a caller hands in when all it has is a passage list.
        """
        match = re.search(r"^# Claim\n(.*?)(?=\n\n|\Z)", text, re.M | re.S)
        if match is None:
            return text
        return match.group(1)

    def _reply_ClaimVerdictDraft(self, text: str) -> ClaimVerdictDraft:
        """The verdict and the stances the scenario declared for each read.

        A page the scenario declares as a contradicting account is scripted as
        one: its row is the refutation, its id is in ``contradiction_ids``, and
        the words it disagrees with are the words the packet attributes to it.
        Scripting every read as a support would hand the run a packet in which
        nothing disagrees, so the run could never record the disagreement the
        scenario declared, and the case would be asserting the fixture's
        silence rather than the product's behaviour. Each id is attributed to
        the page the packet itself named with it.
        """
        ids = re.findall(r"^- id: (\S+)", text, re.M)
        if not ids:
            raise ReplayContractError(
                "the adjudication packet carried no evidence"
            )
        source = self.claim_source(text)
        contradicting = [
            evidence_id
            for evidence_id in ids
            if (page := self.evidence_source(text, evidence_id)) is not None
            and page.verdict == "contradicts"
        ]
        supporting = [
            evidence_id for evidence_id in ids if evidence_id not in contradicting
        ]
        if source.verdict != "verified":
            supporting = supporting[:1]
        return ClaimVerdictDraft(
            verdict=source.verdict,
            confidence=0.85 if source.verdict == "verified" else 0.5,
            assessments=[
                SupportAssessment(
                    evidence_id=evidence_id,
                    stance=(
                        "contradicts"
                        if evidence_id in contradicting
                        else "supports"
                    ),
                    complete_support=True,
                    scope_compatible=True,
                    independent=True,
                )
                for evidence_id in ids
            ],
            support_ids=supporting,
            contradiction_ids=contradicting,
            rationale=(
                "The packet's reads state the claim."
                if source.verdict == "verified"
                else "One read states the claim."
            ),
        )

    def evidence_source(self, text: str, evidence_id: str) -> ReplaySource | None:
        """The page the adjudication packet named with one evidence id."""
        match = re.search(
            rf"^- id: {re.escape(evidence_id)}\n  source: .*?\((https?://\S+)\)",
            text,
            re.M,
        )
        if match is None:
            return None
        return self.by_url.get(match.group(1))

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

    def packet_claims(
        self, text: str
    ) -> list[tuple[str, str, tuple[str, ...], str, str]]:
        """Read the packet's ``C001 [badge] text (urls) coverage=…`` rows.

        The parenthetical is the list of pages that stated the claim, not one
        page: a claim two independent reads support arrives as
        ``(https://a…, https://b…)``. Reading it as a single ``\\S+`` URL made
        every such row unparseable, so exactly the claims whose corroboration
        the case exists to show were silently dropped from the draft.

        The coverage cell is a list for the same reason: one claim stated by
        three topics is one row reading ``coverage=topic-01, topic-02,
        topic-03``, and reading it as a single token made that row unparseable
        too. The row is drafted once, under the first target it covers.

        The badge's first word is the verdict the run's own adjudication gave
        the claim - ``verified``, ``contradicted``, ``insufficient_evidence``.
        It is the product's finding about the row, and the writer's draft is
        checked against it, so the row carries it out of the packet rather
        than dropping it with the brackets.
        """
        rows = re.findall(
            r"^(C\d+) \[([^\]]*)\] (.*) \((.*?)\) coverage=(.+)$",
            text,
            re.M,
        )
        if not rows:
            raise ReplayContractError(
                "the synthesis packet carried no checked claims"
            )
        return [
            (
                claim_id,
                claim_text,
                tuple(
                    url.strip()
                    for url in urls.split(",")
                    if url.strip()
                ),
                coverage.split(",")[0].strip(),
                badge.split()[0] if badge.split() else "",
            )
            for claim_id, badge, claim_text, urls, coverage in rows
        ]

    def _reply_ReportDraft(self, text: str) -> ReportDraft:
        rows = self.packet_claims(text)
        points: list[ReportPointDraft] = []
        sections: list[ReportSectionDraft] = []
        answer_rows: list[AnswerRowDraft] = []
        notes: list[str] = []
        for claim_id, claim_text, urls, coverage, verdict in rows:
            if verdict == "contradicted":
                # The run's own adjudication refused to settle this claim, so
                # the draft may not settle it either: published as a point it
                # is the false settlement the case exists to catch, and left
                # out in silence it is a disagreement the reader was never
                # told about. It is disclosed instead - the words it carries
                # stay in the draft as the thing being disagreed with.
                notes.append(
                    f"A read account disagrees with '{claim_text}', and the "
                    "two were not reconciled."
                )
                continue
            # The row's own coverage names the topic, so a section is never
            # titled from a position in the packet.
            topic = self._topic_for_target(coverage)
            subject, dimension = self.answer_labels_for(coverage, claim_text)
            text = claim_text
            if self.invented_prose and not points:
                # A writer dressing an assertion no page made into a cited
                # point, which is the shape the attestation exists to refuse.
                text = f"{claim_text}, because {self.invented_prose}"
            points.append(
                ReportPointDraft(
                    text=text,
                    claim_ids=[claim_id],
                    source_urls=list(urls),
                )
            )
            sections.append(
                ReportSectionDraft(title=topic.title, points=[points[-1]])
            )
            answer_rows.append(
                AnswerRowDraft(
                    subject=subject,
                    dimension=dimension,
                    finding=claim_text,
                    claim_ids=[claim_id],
                    source_urls=list(urls),
                )
            )
        return ReportDraft(
            # One point per section, and no executive summary: the composer
            # refuses the same fact twice under one section, and a restatement
            # in the summary would be the same (text, cluster) pair the
            # section already carries. The answer rows are the direct answer.
            executive_summary=[],
            ranked_constraints=[],
            sections=sections,
            uncertainty_notes=notes,
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
            # The provider boundary's own failure type, not a bare exception:
            # an outage is what the caller is written to survive, and raising
            # something else would test the harness's imagination instead of
            # the product's handling of a review that could not be made.
            raise ProviderError("the semantic review was not made")
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
    """Answer searches from the scenario's declared topic inventory.

    A page carries the search number that first surfaces it, so a topic that
    declares a second page ``discovered_on_search=2`` is a topic whose opening
    round could not have read it: the second round is the only one that can,
    which is what makes a round's recovery observable rather than assumed.

    Rounds are counted per topic rather than per query string, because the
    run's own policy refuses to repeat a query it has already issued — so a
    second round is reached with a second query, and a fixture that counted
    rounds per string would never see one.
    """

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.queries: list[str] = []
        self.rounds: dict[int, int] = {}

    def topic_index_for(self, query: str) -> int | None:
        for index, topic in enumerate(self.scenario.topics):
            if query in (topic.query, *topic.follow_up_queries):
                return index
        return None

    def search(
        self,
        *,
        query: str,
        search_depth: Any = None,
        max_results: Any = None,
    ) -> dict[str, Any]:
        del search_depth, max_results
        self.queries.append(query)
        index = self.topic_index_for(query)
        if index is None:
            raise ReplayContractError(f"unscripted search {query!r}")
        topic = self.scenario.topics[index]
        round_number = self.rounds.get(index, 0) + 1
        self.rounds[index] = round_number
        return {
            "results": [
                {
                    "url": source.url,
                    "title": source.title,
                    "content": source.excerpt,
                }
                for source in topic.sources
                if source.discovered_on_search <= round_number
            ]
        }


def pdf_bytes(text: str) -> bytes:
    """A one-page PDF whose extractable text is exactly ``text``.

    The document path is exercised through a real document: the fixture writes
    bytes a PDF reader can parse, and the production extractor is what turns
    them back into the prose the claim is checked against. A fixture that
    handed the reader its own text would be testing itself.
    """
    def _escape(value: str) -> str:
        return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    lines = [line for line in text.split("\n") if line.strip()]
    body = ["BT", "/F1 11 Tf", "14 TL", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            body.append("T*")
        body.append(f"({_escape(line)}) Tj")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


class ReplayHTTP:
    """Serve the scenario's pages, and robots.txt, without a socket."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario
        self.by_url = scenario.sources
        self.fetched: list[str] = []
        # Every request the run made, refusals included: a case that proves a
        # denied URL is not retried needs the attempts, not just the bodies.
        self.requests: list[str] = []

    def fetches_of(self, url: str) -> int:
        return self.requests.count(url)

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
        self.requests.append(url)
        if source.status_code != 200:
            return httpx.Response(
                status_code=source.status_code,
                text="<html><body><p>Access denied.</p></body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        self.fetched.append(url)
        if source.content_type == "application/pdf":
            return httpx.Response(
                status_code=200,
                content=pdf_bytes(source.text),
                headers={"content-type": "application/pdf"},
                request=request,
            )
        return httpx.Response(
            status_code=200,
            text=(
                "<html><body><article><p>"
                f"{source.text}"
                "</p></article></body></html>"
            ),
            headers={"content-type": source.content_type},
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


# The session every seeded cache artifact was read by. A cache entry is
# provenance, not a fixture label: it has to name the session that read the
# bytes, and no record this run keeps may claim those bytes as its own fetch.
# One constant keeps that name out of every case's hands.
PRIOR_SESSION_ID = "earlier-session"
# When that session read them. The artifact's observation period is part of
# what a cache import carries forward, so it is stated rather than defaulted
# to the run's own clock.
PRIOR_READ_RETRIEVED_AT = "2024-11-01T09:00:00+00:00"


def prior_session_reads(scenario: ReplayScenario) -> dict[str, ReadRecord]:
    """The bodies an earlier session stored, keyed the way the cache looks up.

    Built through the product's own read contract from the payload the reader
    would have returned — a fixture that assembled a record field by field
    would be testing a shape no reader produces. What the artifact claims
    about itself is the fixture's declaration: a ``valid`` or ``stale``
    artifact stores a body and declares that body's digest, while a ``forged``
    one stores a body and declares the digest of this page's own text, which
    is provenance no reader of that body could have earned.
    """
    reads: dict[str, ReadRecord] = {}
    for url, source in scenario.sources.items():
        if not source.cache_artifact:
            continue
        body = source.cached_text or source.text
        reader = _required_reader(url) or "web_scraper"
        payload: dict[str, Any] = {
            "title": source.title,
            "extraction_complete": True,
        }
        if reader == "document_reader":
            payload.update(
                {
                    "source": url,
                    "requested_source": url,
                    "resolved_source": url,
                    "chunks": [{"text": body, "chunk_index": 0, "page": 1}],
                }
            )
        else:
            payload.update({"url": url, "requested_url": url, "text": body})
        record = build_read_record_from_tool_result(
            ToolResult(
                tool_name=reader,
                success=True,
                data=payload,
                latency_ms=0.0,
            ),
            session_id=PRIOR_SESSION_ID,
            retrieved_at=PRIOR_READ_RETRIEVED_AT,
        )
        if record is None:
            raise ReplayContractError(
                f"the stored read declared for {url} is not admissible"
            )
        if source.cache_artifact == "forged":
            record = record.model_copy(
                update={
                    "content_sha256": normalized_content_sha256(source.text)
                }
            )
        reads[url] = record
    return reads


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
    seeded_reads: dict[str, ReadRecord] = field(default_factory=dict)
    """The stored bodies the fixture declared, as they were handed to the run.

    A snapshot rather than the run's own cache: the cache is the run's to
    update — a fresh body at a stored URL replaces the index entry, which is
    exactly how a changed page stops being served from an old read — so the
    artifact a case declared is only visible from here.
    """


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
    completer.equivalence_pairs = tuple(scenario.equivalence_pairs)
    completer.review_failure = scenario.review_failure
    completer.invented_prose = scenario.invented_prose
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
    if long_term is None and scenario.memory_entries:
        # Written through the production save path rather than injected into
        # the collection: a fixture that seeded the store directly would be
        # testing a store shape no writer produces.
        written = await memory.save_many(list(scenario.memory_entries))
        if written != len(scenario.memory_entries):
            raise ReplayContractError(
                "long-term memory refused the scenario's entries"
            )
    procedural = ProceduralMemory.from_config(
        resolved.memory.procedural, tracker=tracker
    )
    # The source-cache state the run starts with, seeded before the graph is
    # compiled: what a case declares here is what an earlier session left, and
    # the run has to validate or refuse each entry as it reaches it. The run
    # gets its own copy of the mapping, because it is the run's cache to
    # update; the declaration itself is kept for the case's own assertions.
    stored_reads = prior_session_reads(scenario)
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
            read_cache=dict(stored_reads),
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
        seeded_reads=stored_reads,
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

    def answerable_claims(self) -> list[Any]:
        """The checked claims this run could still answer an obligation with.

        A claim that names no target answered nothing, however sound it is: a
        case that counts these is asking whether the run converted its
        evidence into answers, which is the fact a clean exit code does not
        carry.
        """
        return [
            claim
            for claim in self.state.verified_claims
            if getattr(claim, "target_ids", ())
        ]

    def gap_kinds(self) -> tuple[str, ...]:
        """The named ways this run fell short, in the run's own vocabulary.

        Read from the state rather than from the scenario's prose: a hard
        failure is a hard failure whether or not the case expected one, and a
        case that declares which kinds it tolerates is declaring exactly this
        list.
        """
        kinds: list[str] = []
        quality = self.state.quality
        if quality is None:
            # A pass that composed no report judged nothing, and a case that
            # ran out of iterations before composing one has to say so rather
            # than report its silence as a clean run.
            kinds.append("no_quality_snapshot")
        else:
            for failure in quality.hard_failures:
                kinds.append(f"hard:{failure}")
            if quality.unanswered_critical_target_ids:
                kinds.append("unanswered_critical_target")
            if quality.unaccounted_target_ids:
                kinds.append("unaccounted_target")
            if quality.semantic_review_status != "scored":
                # A missing judgement is not a pass: the reviewed baseline is
                # explicit that an unjudged repetition must never read as one.
                kinds.append("semantic_review_missing")
        for error in self.state.errors:
            kinds.append(f"error:{error.error_type}")
        return tuple(dict.fromkeys(kinds))


def _invariant_primary_attribution_not_verified(run: ReplayRun) -> str | None:
    """No claim read from one publisher was recorded as a corroborated pair."""
    for claim in run.state.verified_claims:
        if claim.evidence_status == "verified_pair":
            return (
                "a claim was recorded as an independent pair: "
                f"{claim.text[:60]!r}"
            )
    return None


# --- what the invariants read -----------------------------------------------
#
# Each checker below is a property of the *run*, not a restatement of its case:
# it reads the state the production agents wrote and the boundary the harness
# recorded, and it names the fact that would make the case's assertion false.
# None of them re-runs the composition or the gate - a checker that recomputed
# the product's own verdict could only ever agree with it.


def _read_index(run: ReplayRun) -> dict[str, Any]:
    """Every read this run made, reachable by either URL it is known by."""
    index: dict[str, Any] = {}
    for read in run.state.read_records.values():
        for url in (read.resolved_url, read.requested_url):
            index.setdefault(normalize_source_url(url), read)
    return index


def _identity(run: ReplayRun, url: str) -> tuple[str | None, str | None]:
    """``(publisher_id, work_id)`` for a URL this run read, or two ``None``s."""
    read = _read_index(run).get(normalize_source_url(url))
    if read is None:
        return (None, None)
    return read_identity(read)


def _supporting_urls(claim: Any) -> list[str]:
    """The pages whose passages were selected to support one claim."""
    supporting = [
        passage.source_url
        for passage in claim.verification_evidence
        if passage.stance == "supports"
    ]
    return list(dict.fromkeys(supporting or list(claim.source_urls)))


def _supporting_reads(run: ReplayRun, claim: Any) -> list[Any]:
    index = _read_index(run)
    reads = []
    for url in _supporting_urls(claim):
        read = index.get(normalize_source_url(url))
        if read is not None and read not in reads:
            reads.append(read)
    return reads


def _statement_urls(run: ReplayRun, statement: Any) -> list[str]:
    """The pages one reader statement cites, through its selected evidence."""
    units = run.state.evidence_units
    return list(
        dict.fromkeys(
            units[evidence_id].source_url
            for evidence_id in statement.evidence_ids
            if evidence_id in units
        )
    )


def _page(run: ReplayRun, url: str) -> ReplaySource | None:
    normalized = normalize_source_url(url)
    for key, source in run.scenario.sources.items():
        if normalize_source_url(key) == normalized:
            return source
    return None


def _years(text: str) -> set[str]:
    """The four-digit years a piece of prose states."""
    return set(re.findall(r"\b(?:19|20)\d{2}\b", text))


# One numbered line of the report's own reference list: the product renders
# ``N. title — url``, and a checker reading the artifact reads the URL it
# printed rather than a list it would have to derive for itself.
_REFERENCE_URL = re.compile(r"^\d+\.\s.*?(https?://\S+)\s*$")


def _figures(claims: Sequence[str]) -> list[str]:
    """The number-and-unit readings a set of claim texts states."""
    found: list[str] = []
    for claim in claims:
        for match in re.finditer(r"\d[\d.,]*\s+[a-z]+", claim):
            figure = " ".join(match.group(0).split())
            if figure not in found:
                found.append(figure)
    return found


def _verified_pairs(run: ReplayRun) -> list[Any]:
    return [
        claim
        for claim in run.state.verified_claims
        if claim.evidence_status == "verified_pair"
    ]


def _late_pages(run: ReplayRun) -> list[ReplaySource]:
    """The pages of a topic that only a later discovery round could surface."""
    return [
        source
        for topic in run.scenario.topics
        for source in topic.sources
        if source.discovered_on_search > 1
    ]


def _invariant_no_false_verification(run: ReplayRun) -> str | None:
    """A corroborated badge rests on two publishers and two complete reads.

    The badge is the strongest statement the product makes about a claim, and
    it is a claim about *provenance*: two independent accounts. Evidence that
    resolves to one publisher, to one work, or to a partial read that
    establishes no identity at all cannot carry it, and a run that records the
    badge anyway has verified nothing while telling the reader it has.
    """
    for claim in _verified_pairs(run):
        reads = _supporting_reads(run, claim)
        partial = [read for read in reads if not read.extraction_complete]
        if partial:
            return (
                f"{claim.text[:60]!r} was badged as a pair on a partial read "
                f"({partial[0].resolved_url}), which establishes no identity"
            )
        publishers = {_identity(run, url)[0] for url in _supporting_urls(claim)}
        works = {_identity(run, url)[1] for url in _supporting_urls(claim)}
        publishers.discard(None)
        works.discard(None)
        if len(publishers) < 2 or len(works) < 2:
            return (
                f"{claim.text[:60]!r} was badged as an independent pair on "
                f"{len(publishers)} publisher(s) and {len(works)} work(s)"
            )
    return None


def _invariant_mirror_not_double_counted(run: ReplayRun) -> str | None:
    """One body served twice is one work, however many hosts serve it.

    Both reads are real and both are admitted; what the mirror cannot do is
    become the second account. So the checker looks for two reads of one body
    - which is only observable if both reads happened, which is why the case
    proves the read as well as the refusal - and then requires that no claim
    rests on that one body, and that no work reaches the reader twice.

    The badge carries the first half: a corroborated claim's supporting reads
    must span more than one body, so a run that offered both copies is never
    credited with two accounts. The references carry the second, and the
    checker reads them off the published report rather than off the citation
    list the renderer worked from: one work reaches the reader as one
    reference, whatever a statement's evidence ids name, and a reference list
    naming the running copy *and* its mirror tells the reader that one work is
    two sources. Two references are the same work when the reads resolve to
    one work, which is identity the product already decided - the checker only
    relates the URLs the report itself published.
    """
    by_digest: dict[str, list[Any]] = {}
    for read in run.state.read_records.values():
        by_digest.setdefault(read.content_sha256, []).append(read)
    mirrored = [reads for reads in by_digest.values() if len(reads) > 1]
    if not mirrored:
        return "no body was read from two hosts, so nothing was mirrored"
    for claim in _verified_pairs(run):
        reads = _supporting_reads(run, claim)
        if len({read.content_sha256 for read in reads}) < 2:
            return (
                f"{claim.text[:60]!r} was badged as a pair on one body read "
                "from two hosts"
            )
    references = [
        match.group(1)
        for line in run.report.splitlines()
        if (match := _REFERENCE_URL.search(line))
    ]
    works = [work for work in (_identity(run, url)[1] for url in references) if work]
    if len(set(works)) < len(works):
        return (
            f"the reference list names {len(works)} reads of one work as "
            f"{len(references)} references"
        )
    return None


def _invariant_denied_url_not_retried(run: ReplayRun) -> str | None:
    """A refused page is asked for once, and the refusal is what was recorded."""
    refused = [
        source
        for source in run.scenario.sources.values()
        if source.status_code != 200
    ]
    if not refused:
        return "the scenario declared no refused page"
    for source in refused:
        attempts = run.replay.http.fetches_of(source.url)
        if attempts == 0:
            return f"the refused page {source.url} was never asked for"
        if attempts > 1:
            return f"the refused page {source.url} was asked for {attempts} times"
    return None


def _invariant_both_accounts_cited(run: ReplayRun) -> str | None:
    """Every account the run read is cited, and its own figure is what it says.

    An account is a topic two publishers measured - a model of the world, not
    one page. The reader has to see both of them, and each figure has to reach
    the report as the reading it was: a run that cited one population's page
    for the other's number, or that reported one figure as if it were the
    subject of the question, has merged what the question kept apart.
    """
    report = run.report.casefold()
    for topic in run.scenario.topics:
        if len({source.issuer for source in topic.sources}) < 2:
            continue
        urls = {normalize_source_url(source.url) for source in topic.sources}
        cited = [
            statement
            for statement in run.statements
            if urls
            & {
                normalize_source_url(url)
                for url in _statement_urls(run, statement)
            }
        ]
        if not cited:
            return (
                f"the account {topic.title!r} was read but never cited in the "
                "report"
            )
        for figure in _figures([source.claim for source in topic.sources]):
            if figure.casefold() not in report:
                return (
                    f"the report never states {figure!r}, the reading "
                    f"{topic.title!r} was measured at"
                )
    return None


def _invariant_refinement_recovered_evidence(run: ReplayRun) -> str | None:
    """The repair round's new page is what turned the obligation into an answer.

    A recovery that is real is a recovery the ledger shows: the late page was
    acquired, and the claim that answers the obligation is corroborated *by
    it*. A run that had the answer in hand before the repair, or that answered
    from the first round's page alone, did not recover anything.
    """
    late = _late_pages(run)
    if not late:
        return "the scenario declared no page a later round had to find"
    for source in late:
        if source.url not in run.replay.http.fetched:
            return (
                "the page only a later round could find was never read: "
                f"{source.url}"
            )
    late_urls = {normalize_source_url(source.url) for source in late}
    recovered = [
        claim
        for claim in _verified_pairs(run)
        if late_urls & {normalize_source_url(url) for url in claim.source_urls}
    ]
    if not recovered:
        return "no corroborated claim rested on the page the repair acquired"
    return None


def _invariant_forecast_not_substituted_for_observation(
    run: ReplayRun,
) -> str | None:
    """A dated statement never rests on a page dated to another period.

    A projection and an observation are different facts about the world, and
    the difference is the period each one states. So the check is the periods
    themselves: a statement that asserts a year may not cite evidence whose own
    claim is about a different one, which is exactly the substitution of a
    forecast for the figure the question asked for.
    """
    for statement in run.statements:
        stated = _years(statement.text)
        if not stated:
            continue
        for url in _statement_urls(run, statement):
            source = _page(run, url)
            if source is None:
                continue
            page_years = _years(source.claim)
            if page_years and not page_years & stated:
                return (
                    f"the statement {statement.text[:60]!r} rests on "
                    f"{source.url}, whose own claim is about "
                    f"{sorted(page_years)}"
                )
    return None


def _invariant_contradiction_recorded(run: ReplayRun) -> str | None:
    """A disagreeing account is recorded, and never settled behind the reader's back.

    The disagreement has to be visible in two places: the claim's own evidence
    (a contradicting passage was selected and the badge withheld), and the
    reader's report (the unresolved claim is disclosed). A run that recorded
    the passage and then published the number as if nothing disagreed has
    averaged the disagreement away in the one place it matters.
    """
    composition = run.state.composition
    contested = [
        claim
        for claim in run.state.verified_claims
        if any(
            passage.stance == "contradicts"
            for passage in claim.verification_evidence
        )
    ]
    if not contested:
        return "no contradicting passage was recorded for any claim"
    contested_clusters = {
        cluster_id
        for claim in contested
        for cluster_id in [claim.cluster_id, *claim.cluster_aliases]
        if cluster_id
    }
    for claim in contested:
        if claim.evidence_status == "verified_pair":
            return (
                f"{claim.text[:60]!r} was badged as a corroborated pair while "
                "an account it read disagrees"
            )
    for statement in run.statements:
        if statement.mode != "settled":
            continue
        if contested_clusters & set(statement.claim_cluster_ids):
            return (
                f"the contested reading was published as settled: "
                f"{statement.text[:60]!r}"
            )
    disclosed = bool(
        composition is not None
        and (composition.uncertainty_statements or composition.uncertainty_notes)
    )
    if not disclosed:
        return "the disagreement was recorded in the ledger and never disclosed"
    return None


def _invariant_empty_answer_answered_nothing(run: ReplayRun) -> str | None:
    """A tidy report that answered no obligation is a failed run, not a pass.

    The case exists to refuse the reading where clean structure and a
    recommendation stand in for an answer, so the checker asserts the
    emptiness itself and refuses the acceptance that would hide it.
    """
    answered = run.answered_target_ids()
    if answered:
        return f"the run answered {sorted(answered)} without usable evidence"
    quality = run.state.quality
    if quality is not None and quality.answered_targets:
        return "the quality snapshot counted answered targets"
    if run.answerable_claims():
        return "the run recorded a claim that could answer an obligation"
    if run.quality_status == "accepted":
        return "a report that answered nothing was accepted"
    lowered = f" {run.report.casefold()} "
    if " should " in lowered:
        return "the report reads as a recommendation for a question it never answered"
    return None


def _invariant_memory_leads_are_not_reads(run: ReplayRun) -> str | None:
    """A remembered claim is a lead: it is shown, and it is not evidence.

    The remembered URL may reach a decision - that is what makes it a lead
    worth spending a read on - but nothing may cite it as a source, and no read
    may have been taken from memory. A run that treated the memory record as
    its own read has fabricated the provenance of a claim.
    """
    leads = [
        entry.source_url
        for entry in run.scenario.memory_entries
        if entry.source_url
    ]
    if not leads:
        return "the scenario seeded no remembered lead"
    packets = run.replay.completer.packets_carrying
    for url in leads:
        if not packets(url):
            return f"the remembered lead {url} never reached a decision packet"
        if any(
            normalize_source_url(url) == normalize_source_url(read.resolved_url)
            or normalize_source_url(url)
            == normalize_source_url(read.requested_url)
            for read in run.state.read_records.values()
        ):
            return f"the remembered lead {url} was recorded as a read of this run"
        for claim in run.state.verified_claims:
            if any(
                normalize_source_url(cited) == normalize_source_url(url)
                for cited in claim.source_urls
            ):
                return f"a claim cited the remembered lead {url} as its source"
    return None


def _invariant_read_downloaded_once(run: ReplayRun) -> str | None:
    """One body is downloaded once, and every later answer reuses the read.

    Two obligations answered from one page is the case's premise; a second
    download of the same body is the defect. The checker reads the boundary's
    own record of what it served, so a run that fetched the page again to
    answer the second obligation is visible here rather than merely slower.
    A body that reached the run as a stored artifact is the same fact one
    session further out, and it is the cache admission the checker looks for:
    a reuse filed as this run's own network read is a reuse nothing shows.
    """
    fetched = run.replay.http.fetched
    repeated = sorted({url for url in fetched if fetched.count(url) > 1})
    if repeated:
        return f"a body was downloaded more than once: {repeated}"
    reused = [
        read
        for read in run.state.read_records.values()
        if read.acquisition_kind == "cache"
    ]
    if not reused:
        return "no later answer reused a read already made"
    return None


def _invariant_cache_provenance_is_validated(run: ReplayRun) -> str | None:
    """A stored body is validated before it is used, or it is refetched.

    A cache is provenance somebody else established, and both ways it can lie
    are visible from here. A body that validates — the digest it declares is
    the digest of the body it stores — may be reused, but only as an import:
    the run's record keeps the ``cache`` kind, the session that read the
    bytes, and the moment this run validated them, because without those a
    reader would take an earlier session's reading for this run's own fetch,
    and a stored version for the page as it is now. A body that does not
    validate may not be served at all: the URL is fetched, and the record the
    run keeps has to be the read it made itself rather than the artifact's
    read identity or its declared digest.
    """
    seeded = run.replay.seeded_reads
    if not seeded:
        return None
    fetched = run.replay.http.fetched
    records = list(run.state.read_records.values())
    for url, artifact in seeded.items():
        source = run.scenario.sources[url]
        record = next(
            (
                read
                for read in records
                if url
                in {
                    normalize_source_url(read.requested_url),
                    normalize_source_url(read.resolved_url),
                }
            ),
            None,
        )
        served = normalize_source_url(url) in {
            normalize_source_url(item) for item in fetched
        }
        if source.cache_artifact == "forged":
            if not served:
                return (
                    f"the forged stored read for {url} did not produce a fetch "
                    "of the page it claims"
                )
            if record is None:
                return f"the fetched page {url} reached no read record"
            if record.read_id == artifact.read_id:
                return f"the forged stored read for {url} is what the run recorded"
            if (
                record.acquisition_kind != "network"
                or record.origin_session_id != run.session_id
            ):
                return (
                    f"the read of {url} is not the network read this run made"
                )
            continue
        if served:
            return (
                f"the stored read for {url} was downloaded again instead of "
                "being validated from the cache"
            )
        if record is None:
            return f"the stored read for {url} reached no read record"
        if record.acquisition_kind != "cache":
            return (
                f"the stored read for {url} was filed as "
                f"{record.acquisition_kind!r}, not as this run's import"
            )
        if record.origin_session_id != PRIOR_SESSION_ID:
            return (
                f"the stored read for {url} lost the session that read it "
                f"({record.origin_session_id!r})"
            )
        if record.version_validated_at is None:
            return f"the stored read for {url} was never stamped as validated"
        if record.content_sha256 != artifact.content_sha256:
            return (
                f"the stored read for {url} was recorded as a different body "
                "than the one it stored"
            )
    return None


def _invariant_late_candidate_reached_decision(run: ReplayRun) -> str | None:
    """The candidate and the locator survive the short public summary.

    Every packet the run builds is publicly summarised, and the case exists to
    show the summary does not truncate what the next decision needs: the last
    candidate of a topic is offered in the decision packet and its material
    travels into the request that extracts from it.
    """
    for topic in run.scenario.topics:
        if not topic.sources:
            continue
        last = topic.sources[-1]
        carriers = run.replay.completer.packets_carrying(last.url)
        if len(carriers) < 2:
            return (
                f"the candidate {last.url} reached {len(carriers)} request(s), "
                "so a later pass never saw it again"
            )
    return None


def _invariant_public_summary_stayed_short(run: ReplayRun) -> str | None:
    """Every observation the packets carried stayed at its shipped length."""
    prefix = "- [observation] "
    limit = OBSERVATION_SUMMARY_CHARS + len(prefix)
    summaries = 0
    for _key, text in run.replay.completer.packet_sequence:
        for line in text.splitlines():
            if not line.startswith(prefix):
                continue
            summaries += 1
            if len(line) > limit:
                return (
                    f"an observation summary ran to {len(line)} characters, "
                    f"past the {limit} the shipped setting allows"
                )
    if not summaries:
        return "no packet carried a public observation summary"
    return None


def _invariant_required_target_reopened(run: ReplayRun) -> str | None:
    """The obligation, and not the Critic, is what sent the run back to work.

    The case is only about the obligation if nothing else could have prompted
    the second round: the Critic named no gap, and the run still went back,
    drove a second discovery round, and answered the target from what it found.
    """
    critique = run.state.critique
    if critique is not None and critique.gaps:
        return (
            "the Critic named "
            f"{len(critique.gaps)} gap(s), so the reopening had another cause"
        )
    late = _late_pages(run)
    if not late:
        return "the scenario declared no page only a second round could find"
    for source in late:
        if source.url not in run.replay.http.fetched:
            return f"the second-round page was never read: {source.url}"
    rounds = run.replay.search.rounds
    if not rounds or max(rounds.values()) < 2:
        return "the run never issued a second discovery round"
    return None


def _invariant_stalled_refinement_stopped(run: ReplayRun) -> str | None:
    """A repair that bought nothing stops, and names that as why it stopped.

    The reason is the assertion: a run that reached its iteration ceiling
    stopped because it ran out of budget, which is a different fact from a
    repair loop that recognised the round it had just bought changed nothing.
    """
    reason = run.state.repair_stop_reason
    if reason is None:
        return "the run never recorded why it stopped repairing"
    if reason != "no_progress":
        return f"the repair stopped for {reason!r}, not because it bought nothing"
    return None


def _invariant_paraphrases_merged(run: ReplayRun) -> str | None:
    """Two wordings of one fact are one identity, and the report says it once.

    A merge is observable in three places and the checker reads all three: one
    cluster carries both pages, and the reader's report cites both pages in a
    single statement rather than printing the same reading twice.
    """
    paraphrases = run.scenario.equivalence_pairs
    if not paraphrases:
        return "the scenario declared no paraphrase to merge"
    composition = run.state.composition
    if composition is None:
        return "the run composed no report to merge into"
    for left, right in paraphrases:
        merged = [
            cluster
            for cluster in (run.state.claim_clusters or {}).values()
            if len(cluster.member_claim_ids) > 1
        ]
        if not merged:
            return (
                f"no cluster absorbed a paraphrase, so {left} and {right} "
                "were never merged"
            )
    for statement in run.statements:
        urls = {normalize_source_url(url) for url in _statement_urls(run, statement)}
        if len(urls) > len(_statement_urls(run, statement)):
            return (
                f"the statement {statement.text[:60]!r} cites the same page twice"
            )
    return None


def _invariant_different_periods_stay_distinct(run: ReplayRun) -> str | None:
    """A cluster never holds two periods, or a stale figure answers a current one.

    Two readings of one measure a year apart are two facts, and the identity
    that merges them is the mechanism by which a 2023 number is published
    behind a 2024 question. The checker reads the periods the merged claims
    themselves state, so it can see the merge the provenance alone would not
    show.
    """
    for cluster in (run.state.claim_clusters or {}).values():
        periods: dict[str, str] = {}
        for claim in run.state.verified_claims:
            if claim.cluster_id != cluster.cluster_id and (
                cluster.cluster_id not in claim.cluster_aliases
            ):
                continue
            for year in _years(claim.text):
                periods.setdefault(year, claim.text[:60])
        if len(periods) > 1:
            return (
                f"the cluster {cluster.cluster_id[:12]!r} states "
                f"{sorted(periods)}, so one identity holds two periods"
            )
    return None


def _invariant_no_ranked_constraints_for_a_factual_answer(
    run: ReplayRun,
) -> str | None:
    """A measurement question is answered by its reading, not by a ranking.

    The ranking is a structure the composer is able to fill for any question,
    which is exactly why a case has to show it did not: an answer whose kind is
    not ``constraints`` and whose rows present no option as the best one is the
    answer the question asked for. The kind is read from the composition rather
    than required to be one particular word, because "factual", "historical"
    and "comparison" are three shapes of the same refusal to rank.
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    if composition.constraints:
        return (
            f"an answer that was not a constraint question carried "
            f"{len(composition.constraints)} ranked constraint row(s)"
        )
    if composition.answer_kind == "constraints":
        return "the run answered a measurement question as a constraint ranking"
    if not composition.answer_rows:
        return "the answer published no answer row"
    return None


def _invariant_mechanism_obligation_stays_unanswered(
    run: ReplayRun,
) -> str | None:
    """A cited pair of pages about an outcome is not an answer about its cause.

    The obligation asks for a causal mechanism, and every page the run read
    states what happened rather than why. So the check has two halves and needs
    both: the topic really was researched - a checked claim names the
    obligation - and the obligation is still outstanding, because evidence
    that answers "what" cannot be dressed into an answer to "why".
    """
    from deep_research.utils.types import counted_evidence_targets

    mechanism = [
        target
        for topic in run.state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
        if any(
            "causal mechanism" in dimension.casefold()
            for dimension in target.required_dimensions
        )
    ]
    if not mechanism:
        return "no obligation in this scenario asked for a mechanism"
    answered = set(run.answered_target_ids())
    for target in mechanism:
        researched = any(
            target.target_id in claim.target_ids
            for claim in run.state.verified_claims
        )
        if not researched:
            return (
                f"nothing was ever checked for {target.target_id}, so the case "
                "proves nothing about the answer it withheld"
            )
        if target.target_id in answered:
            return (
                f"the mechanism obligation {target.target_id} was answered by "
                "evidence that states only the outcome"
            )
    return None


def _invariant_review_missing_blocks_acceptance(run: ReplayRun) -> str | None:
    """No judgement is not a pass, however complete the artifacts look."""
    review = run.state.report_review
    if review is not None and review.status == "scored":
        return "the run recorded a semantic judgement after all"
    if run.quality_status == "accepted":
        return "a run with no semantic judgement was accepted"
    if not run.report.strip():
        return "no reader artifact was published"
    if not run.state.verified_claims:
        return "no checked claim survived into the artifacts"
    return None


_REPLAY_INVARIANTS: dict[str, Any] = {
    "primary_attribution_not_verified": _invariant_primary_attribution_not_verified,
    "no_false_verification": _invariant_no_false_verification,
    "mirror_not_double_counted": _invariant_mirror_not_double_counted,
    "denied_url_not_retried": _invariant_denied_url_not_retried,
    "both_accounts_cited": _invariant_both_accounts_cited,
    "refinement_recovered_evidence": _invariant_refinement_recovered_evidence,
    "forecast_not_substituted_for_observation": (
        _invariant_forecast_not_substituted_for_observation
    ),
    "contradiction_recorded": _invariant_contradiction_recorded,
    "empty_answer_answered_nothing": _invariant_empty_answer_answered_nothing,
    "memory_leads_are_not_reads": _invariant_memory_leads_are_not_reads,
    "read_downloaded_once": _invariant_read_downloaded_once,
    "cache_provenance_is_validated": _invariant_cache_provenance_is_validated,
    "late_candidate_reached_decision": _invariant_late_candidate_reached_decision,
    "public_summary_stayed_short": _invariant_public_summary_stayed_short,
    "required_target_reopened": _invariant_required_target_reopened,
    "stalled_refinement_stopped": _invariant_stalled_refinement_stopped,
    "paraphrases_merged": _invariant_paraphrases_merged,
    "different_periods_stay_distinct": _invariant_different_periods_stay_distinct,
    "no_ranked_constraints_for_a_factual_answer": (
        _invariant_no_ranked_constraints_for_a_factual_answer
    ),
    "review_missing_blocks_acceptance": _invariant_review_missing_blocks_acceptance,
    "mechanism_obligation_stays_unanswered": (
        _invariant_mechanism_obligation_stays_unanswered
    ),
}


def expectation_failures(run: ReplayRun) -> list[str]:
    """Every way this run did not meet the result its case declares.

    The declared result and the test's verdict are two facts, and this is the
    function that keeps them apart: it returns the ways the *product* fell
    short, so a case whose product result is ``partial`` with exit 4 reports no
    failures at all while a case that abstained cleanly reports every obligation
    it left unanswered. A run that reached the network, or that answered
    nothing, or that published a claim its evidence does not support fails
    here rather than being read as a clean exit.
    """
    expectation = run.scenario.expectation
    failures: list[str] = []
    if run.quality_status != expectation.terminal_quality:
        failures.append(
            f"terminal quality {run.quality_status!r} != "
            f"{expectation.terminal_quality!r}"
        )
    if run.exit_code != expectation.exit_code:
        failures.append(
            f"exit code {run.exit_code} != {expectation.exit_code}"
        )
    answered = set(run.answered_target_ids())
    missing_targets = [
        target_id
        for target_id in expectation.required_target_ids
        if target_id not in answered
    ]
    if missing_targets:
        failures.append(
            "required targets unanswered: " + ", ".join(missing_targets)
        )
    answerable = run.answerable_claims()
    if len(answerable) < expectation.minimum_answerable_claims:
        failures.append(
            f"{len(answerable)} answerable claims < "
            f"{expectation.minimum_answerable_claims} required"
        )
    report = run.report.casefold()
    for assertion in expectation.forbidden_assertions:
        if assertion.casefold() in report:
            failures.append(f"the report asserts {assertion!r}, which the case forbids")
    for phrase in expectation.required_report_phrases:
        if phrase.casefold() not in report:
            failures.append(f"the report does not state {phrase!r}")
    gaps = set(run.gap_kinds())
    for kind in expectation.required_gap_kinds:
        if kind not in gaps:
            failures.append(f"the expected gap {kind!r} was not recorded")
    unexpected = sorted(
        gaps
        - set(expectation.required_gap_kinds)
        - set(expectation.allowed_failure_classes)
    )
    if unexpected:
        failures.append(
            "failure kinds the case does not allow: " + ", ".join(unexpected)
        )
    for name in expectation.required_invariants:
        invariant = _REPLAY_INVARIANTS.get(name)
        if invariant is None:
            failures.append(f"unknown invariant {name!r}")
            continue
        problem = invariant(run)
        if problem:
            failures.append(f"invariant {name!r} broken: {problem}")
    return failures


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
    "expectation_failures",
    "network_denied",
    "offline_credentials",
    "production_config_path",
    "replay_settings",
    "run_replay_scenario",
]

