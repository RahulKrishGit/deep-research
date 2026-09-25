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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import httpx

from deep_research.agents.acquisition import (
    _required_reader,
    build_read_record_from_tool_result,
)
from deep_research.agents.base import AgentCompleter
from deep_research.agents.evidence import normalized_content_sha256
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.evidence_verifier import (
    ContextCheckDraft,
    FigureCheckDraft,
    StatementCheckDraft,
    StatementVerdictDraft,
)
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlanExtensionDraft,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.report_reviewer import (
    ReportReviewDraft,
    ReviewDimensionScores,
    StatementDispositionDraft,
)
from deep_research.agents.report_writer import (
    ReportWriterDraft,
    WriterPointDraft,
)
from deep_research.agents.researcher import (
    FindingDraft,
    FindingFigureDraft,
    SubTopicFindingsDraft,
)
from deep_research.agents.source_evaluator import (
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.agents.sources import normalize_source_url, publisher_identity
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
from deep_research.utils.types import REVIEW_DIMENSIONS, ReadRecord, ResearchState

# The public progress summary stays at its shipped length. A scenario may ask
# for a different one to prove a case cannot pass by enlarging logs, but the
# default a release case runs at is the production value.
OBSERVATION_SUMMARY_CHARS = 200

# The instant every replay run stamps its dates from. A replay reproduces one
# declared run rather than printing a document today, so the harness pins the
# clock the run's own agents read instead of letting the wall clock reach them:
# the reader's ``Generated on`` line then states the day the harness printed
# the report, the plan's as-of date and the evidence timestamps follow the same
# instant, and three repetitions of a row are the same document however many
# times, and whenever, the row is run. Without it a suite that straddled
# 00:00 UTC published three differently dated reports and failed the row on a
# fact about the clock rather than about the agents.
#
# Midday of the day the real-agent rows were certified (see
# ``docs/validation/2026-09-16-real-agent-controlled-validation.md``), so the
# frozen date is one the fixtures' own era already assumes: the rows were
# authored and accepted against a clock in this year, and the questions that
# name a period are classified against it.
REPLAY_CLOCK_INSTANT = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def replay_clock() -> datetime:
    """The harness's frozen clock: the same instant however often it is read."""
    return REPLAY_CLOCK_INSTANT


class ReplayContractError(RuntimeError):
    """A scripted reply was asked for a packet the scenario did not author."""


# The fields a fixture may script on a scripted reply. Both are exactly the
# fields the reply type carries, so a scenario cannot write an override the
# double has nowhere to put.
CONTEXT_OVERRIDE_KEYS = frozenset(
    {"scope", "attribution", "organisation", "kind", "evidence_words", "verdict"}
)
STATEMENT_OVERRIDE_KEYS = frozenset({"verdict", "text", "reason"})


# The reason lines the scripted replies carry. A verdict without a reason is
# refused by its own schema, and a reason that says only "ok" would tell a
# reviewer nothing about which fixture produced it.
CONTEXT_CONFIRM_REASON = "The page states the figure as the extractor recorded it."
STATEMENT_CONSISTENT_REASON = "The sentence states only what its cited findings carry."
INVENTED_PROSE_REASON = "No page states the words this sentence rests on."


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
    # The figures this source's excerpt states, each (value, unit, period,
    # kind) exactly as ``FindingFigureDraft`` takes them. Empty is a source
    # whose scripted claim carries no discrete figure for Figure Match to
    # verify.
    figures: tuple[tuple[str, str, str | None, str | None], ...] = ()
    # The edition this page's figure belongs to, as the extraction records it
    # (``FindingDraft.vintage``): it is the release key PD-9 compares to tell a
    # revision of one fact from a second reading of it.
    vintage: str = ""
    # The scope the scripted extraction records for this page's figure
    # (``FindingDraft.measure_scope``). A page whose own words state a wider
    # basis than the extractor wrote is the shape the Context Check corrects,
    # and a fixture that recorded nothing would be a correction of nothing.
    recorded_scope: str = ""
    # What the Context Check double answers for this page's figures, keyed the
    # way the reply carries them: ``scope``, ``attribution``, ``organisation``,
    # ``kind``, ``evidence_words`` and ``verdict`` (D8's Context Check fields).
    # Empty is a page the double confirms exactly as the extractor recorded it.
    # A key outside that set is a fixture typo, not a silent no-op: the
    # Context Check reads only these fields, so an override it cannot read
    # would script nothing while looking like it scripted something.
    context: dict[str, str] = field(default_factory=dict)
    # What the Statement Check double answers for the sentence that cites this
    # page's finding: ``verdict`` (consistent, corrected, inconsistent),
    # ``text`` (a correction's replacement) and ``reason``. The double finds
    # the sentence through the registry label the writer's own request stamped
    # on this finding, so a case scripts a wording fault on the page that
    # carries the figure the sentence restates.
    statement: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.context) - CONTEXT_OVERRIDE_KEYS)
        if unknown:
            raise ValueError(
                f"the Context Check override for {self.url} names "
                f"{', '.join(unknown)}, which the reply does not carry"
            )
        unknown = sorted(set(self.statement) - STATEMENT_OVERRIDE_KEYS)
        if unknown:
            raise ValueError(
                f"the Statement Check override for {self.url} names "
                f"{', '.join(unknown)}, which the verdict does not carry"
            )
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
    # How many verified findings have to name a required target before the
    # case counts as answering anything: a case that declares three obligations
    # and publishes two of them has narrowed what it proved, whatever its exit
    # code says.
    minimum_answered_findings: int = 0
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
    # The terminal review that could not be made: the provider raises, exactly
    # as an outage would, and the run has to record the absent judgement
    # instead of accepting an unreviewed report.
    review_failure: bool = False
    # The dimension score a made review records, and the statement ids it
    # disposes of as unsupported. 0.9 with none rejected is an accepting
    # review (spec §6.3's floor is a mean of 0.80); a case that wants the
    # terminal review to refuse a report lowers the score or names the
    # sentences the evidence does not carry.
    review_score: float = 0.9
    rejected_statement_ids: tuple[str, ...] = ()
    # The Statement Check that could not be made: every batch's provider call
    # raises, exactly as an outage would, and every drafted sentence has to be
    # kept exactly as drafted with the failure recorded (§5.4). The case exists
    # because "no judgement" must not silently read as "judged consistent".
    statement_failure: bool = False
    # Prose the scripted writer drafts that no page states. The product is the
    # thing under test here: prose no citation attests must not reach the
    # reader, whatever the writer proposed. Under D8 the refusal is the
    # Statement Check's own verdict, so the double reads this text out of the
    # sentence it was handed rather than trusting a code pattern.
    invented_prose: str = ""
    # What long-term memory already holds when the run starts, written through
    # the production memory bridge before the graph is compiled. A fixture
    # states these when its subject is what the run does with a lead it has
    # already been given: memory recalled at startup is a lead, and whether it
    # is treated as a read is the run's decision, not the fixture's.
    memory_entries: tuple[MemoryEntry, ...] = ()
    # Production ``agents`` config fields this scenario pins instead of
    # inheriting the shipped default (for example {"max_sub_topics": 7}), so
    # a case whose own plan needs a different cap than whichever value
    # config.yaml carries today stays a real test of that plan rather than
    # silently starving when the production default changes under it.
    agent_overrides: dict[str, object] = field(default_factory=dict)

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


# --- reading the requests the real agents build -------------------------------


def _packet_blocks(text: str, marker: str) -> list[tuple[str, str]]:
    """Split a packet into ``(label, body)`` pairs at its ``## F01``/``## S001`` headings.

    The headings are the labels the reply must name, so the reply is built from
    what the packet carried rather than from any order the harness remembers.
    """
    headings = list(re.finditer(rf"(?m)^## ({marker}\d+)$", text))
    blocks: list[tuple[str, str]] = []
    for position, heading in enumerate(headings):
        end = (
            headings[position + 1].start()
            if position + 1 < len(headings)
            else len(text)
        )
        blocks.append((heading.group(1), text[heading.end() : end]))
    return blocks


def _printed_line(body: str, label: str) -> str:
    """One ``label: value`` line a packet block prints, empty when it prints none."""
    match = re.search(rf"(?m)^{re.escape(label)}: (.*)$", body)
    return match.group(1).strip() if match else ""


def _printed_page(body: str) -> tuple[str, str]:
    """``(title, page owner)`` from a Context Check block's own ``page:`` line."""
    match = re.search(r"(?m)^page: (.*) \(([^()]*)\)$", body)
    if match is None:
        raise ReplayContractError("a Context Check block printed no page line")
    return match.group(1).strip(), match.group(2).strip()


def _printed_owner(body: str) -> str:
    return _printed_page(body)[1]


def _recorded_fields(body: str) -> dict[str, str]:
    """The ``recorded fields`` a Context Check block prints, by field name."""
    printed = _printed_line(body, "recorded fields")
    fields: dict[str, str] = {}
    for part in printed.split(";"):
        name, _, value = part.partition(":")
        if value.strip() and value.strip() != "none":
            fields[name.strip()] = value.strip()
    return fields


def _printed_figures(body: str) -> list[tuple[int, str, str | None, str]]:
    """Every figure a Context Check block lists: ``(number, value, period, kind)``."""
    figures: list[tuple[int, str, str | None, str]] = []
    for match in re.finditer(
        r"(?m)^  figure (\d+): (\S+) \S+ \| recorded period (.*?) \| "
        r"recorded kind (.*)$",
        body,
    ):
        period = match.group(3).strip()
        kind = match.group(4).strip()
        figures.append(
            (
                int(match.group(1)),
                match.group(2),
                None if period in ("", "none", "not stated") else period,
                "actual" if kind in ("", "none") else kind,
            )
        )
    return figures


def _cited_labels(body: str) -> list[str]:
    """The registry labels a Statement Check block's cited findings carry."""
    return re.findall(r"(?m)^  ([FS]\d+): ", body)


def _registry_rows(text: str) -> list[tuple[str, str, str, str, str, str]]:
    """Every verified figure line the writer's request lists.

    ``(label, value, unit, period, kind, organisation)``, read from the
    registry's own format, which is what the writer's prompt is built from.
    """
    rows: list[tuple[str, str, str, str, str, str]] = []
    for match in re.finditer(
        r"(?m)^(F\d+) \| figure \d+: (\S+) (\S+) \| period (.*?) \| kind (\w+) "
        r"\| organisation (.*?) \| label: ",
        text,
    ):
        rows.append(
            (
                match.group(1),
                match.group(2),
                match.group(3),
                match.group(4).strip(),
                match.group(5),
                match.group(6).strip(),
            )
        )
    return rows


def _written_sentence(
    value: str, unit: str, period: str, kind: str, organisation: str
) -> str:
    """The sentence one registry line becomes: the figure, and nothing else.

    The reader label carries the rest (who the figure is credited to, whether
    it is a forecast and, for a forecast, its release), because D8 moves the
    wording judgement to the Statement Check and the label to code. A line
    whose period the page did not state states no period rather than inventing
    one.
    """
    verb = "projects" if kind == "forecast" else "reports"
    stated = f" for {period}" if period and period != "not stated" else ""
    return f"{organisation} {verb} {value} {unit}{stated}."


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
        # What the scenario scripts about the run's replies. Seeded here from
        # the scenario rather than assigned by the runtime builder, so a test
        # that hands the completer a scenario gets the same behaviour a replay
        # does.
        self.review_failure: bool = scenario.review_failure
        self.statement_failure: bool = scenario.statement_failure
        self.rejected_statement_ids: frozenset[str] = frozenset(
            scenario.rejected_statement_ids
        )
        self.review_score: float = scenario.review_score
        self.invented_prose: str = scenario.invented_prose
        # The registry labels the writer's own request stamped, mapped to the
        # page each one cites. The Statement Check's request names its cited
        # findings by those labels, so this is how a scenario's per-page
        # wording override finds the sentence that rests on that page.
        self.registry_sources: dict[str, ReplaySource] = {}

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
        reasoning_effort: str | None = None,
    ) -> Any:
        del max_tokens, reasoning_effort
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
        planned = self._planned_target_ids(text, target_id)
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
            findings.append(
                FindingDraft(
                    content=source.claim,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=source.confidence,
                    read_id=read_id,
                    locator=locator,
                    snippet=source.excerpt,
                    vintage=source.vintage or None,
                    measure_scope=source.recorded_scope or None,
                    figures=[
                        FindingFigureDraft(value=v, unit=u, period=p, kind=k)
                        for v, u, p, k in source.figures
                    ],
                    target_ids=list(planned),
                )
            )
        return SubTopicFindingsDraft(findings=findings)

    def _planned_target_ids(self, text: str, coverage_id: str) -> list[str]:
        """One topic's target ids, read out of the request's target list.

        A finding names the planned targets it serves, and the plan's ids are
        the only ones the extraction may name — the coverage id the read was
        fetched for (``topic-01``) is a different vocabulary and is dropped.
        This harness can honestly do only what it does here: it knows which
        sub-topic fetched the read, not which sentence answers which
        obligation, so it names that topic's targets and leaves the binding to
        the Fact Checker's own dimension check.
        """
        catalogue = re.findall(
            rf"^- ({re.escape(coverage_id)}-target-\d+) "
            rf"\[{re.escape(coverage_id)}\]: ",
            text,
            re.M,
        )
        if not catalogue:
            raise ReplayContractError(
                f"the extraction request listed no planned targets for "
                f"{coverage_id}"
            )
        return catalogue

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

    def _reply_ContextCheckDraft(self, text: str) -> ContextCheckDraft:
        """One figure's context per finding label and figure the batch lists.

        The labels are the request's own. The Evidence Verifier numbers every
        batch from ``F01``, so two batches carry one label for two different
        findings, and a reply assembled from the scenario's page order would
        answer the second batch with the first batch's pages. Each block is
        resolved to the page whose own excerpt the block prints, and only a
        resolved page may script the batch's answer with its ``context``
        override -- an override on a page the request did not name would
        silently script nothing.
        """
        figures: list[FigureCheckDraft] = []
        for label, body in _packet_blocks(text, "F"):
            source = self.context_source(body)
            recorded = _recorded_fields(body)
            snippet = _printed_line(body, "snippet")
            override = source.context
            for number, _value, period, kind in _printed_figures(body):
                figures.append(
                    FigureCheckDraft(
                        finding=label,
                        figure=number,
                        period=period,
                        scope=override.get("scope", recorded.get("scope")),
                        attribution=override.get("attribution", "own"),  # type: ignore[arg-type]
                        organisation=override.get(
                            "organisation", _printed_owner(body)
                        ),
                        kind=override.get("kind", kind),  # type: ignore[arg-type]
                        evidence_words=override.get("evidence_words", snippet),
                        verdict=override.get("verdict", "confirm"),  # type: ignore[arg-type]
                        reason=CONTEXT_CONFIRM_REASON,
                    )
                )
        if not figures:
            raise ReplayContractError(
                "the Context Check packet listed no figure"
            )
        return ContextCheckDraft(figures=figures)

    def context_source(self, block: str) -> ReplaySource:
        """The declared page one Context Check block prints.

        Matched on the words the block itself carries -- the excerpt it quotes,
        then the title it prints -- never on a position in the packet, so the
        match survives any regrouping of the batch.
        """
        title, _ = _printed_page(block)
        snippet = _printed_line(block, "snippet")
        candidates = [
            source
            for source in self.scenario.sources.values()
            if source.excerpt in snippet or snippet in source.excerpt
        ]
        if len(candidates) > 1:
            candidates = [
                source for source in candidates if source.title == title
            ]
        if not candidates:
            candidates = [
                source
                for source in self.scenario.sources.values()
                if source.title == title
            ]
        if not candidates:
            raise ReplayContractError(
                f"the Context Check block for {title!r} names no declared page"
            )
        return candidates[0]

    def _reply_StatementCheckDraft(self, text: str) -> StatementCheckDraft:
        """One verdict per ``S<n>`` label the batch lists.

        Consistent unless the scenario scripts otherwise. A scenario's wording
        fault travels with the page the sentence rests on: the verdict is
        looked up through the registry labels the request's cited findings
        carry, and those labels are the writer's own request's, recorded here
        because the writer runs before the checker in every pass.
        """
        if self.statement_failure:
            # The provider boundary's own failure type, not a bare exception:
            # an outage is what §5.4 is written to survive, and raising
            # anything else would test the harness rather than the product.
            raise ProviderError("the statement check was not made")
        drafts: list[StatementVerdictDraft] = []
        for label, body in _packet_blocks(text, "S"):
            sentence = _printed_line(body, "sentence")
            override = self._statement_override(_cited_labels(body))
            if self.invented_prose and self.invented_prose in sentence:
                # The case's whole subject: prose no page states. No page's
                # override can express it, because it is a fact about the
                # sentence rather than about a page.
                drafts.append(
                    StatementVerdictDraft(
                        label=label,
                        verdict="inconsistent",
                        reason=INVENTED_PROSE_REASON,
                    )
                )
                continue
            verdict = override.get("verdict", "consistent")
            corrected = override.get("text", "")
            if verdict == "corrected" and not corrected.strip():
                raise ReplayContractError(
                    f"the statement override for {label} corrects the sentence "
                    "without stating the replacement text"
                )
            drafts.append(
                StatementVerdictDraft(
                    label=label,
                    verdict=verdict,  # type: ignore[arg-type]
                    corrected_text=corrected,
                    reason=override.get("reason", STATEMENT_CONSISTENT_REASON),
                )
            )
        if not drafts:
            raise ReplayContractError(
                "the Statement Check packet listed no sentence"
            )
        return StatementCheckDraft(statements=drafts)

    def _statement_override(self, cited: Sequence[str]) -> dict[str, str]:
        """The override of the first page the sentence's cited labels name."""
        for label in cited:
            source = self.registry_sources.get(label)
            if source is not None and source.statement:
                return source.statement
        return {}

    def _reply_ReportWriterDraft(self, text: str) -> ReportWriterDraft:
        """One summary point per figure line the writer's own request lists.

        The line is the registry's own format (``F01 | figure 1: 10.4 GW |
        period 2024 | kind actual | organisation Wood Mackenzie | label: ...``),
        so the draft states the figure its verified finding carries, and the
        code-built reader label carries what the sentence does not say (who the
        figure is credited to, and whether it is a forecast). Nothing here
        invents a sentence about a page the registry does not list, and a
        packet that lists no figure is a contract violation rather than an
        empty draft, because the writer is only ever called with a registry.
        """
        self.registry_sources = self._registry_index(text)
        points: list[WriterPointDraft] = []
        for row in _registry_rows(text):
            drafted = _written_sentence(*row[1:])
            if self.invented_prose and not points:
                # The case's own fault: a writer dressing a verified figure in
                # prose no page states. The Statement Check is what refuses it
                # now, so the draft carries the words and the checker's script
                # reads them.
                drafted = f"{drafted} This is because {self.invented_prose}."
            points.append(
                WriterPointDraft(text=drafted, finding_labels=[row[0]])
            )
        if not points:
            raise ReplayContractError(
                "the writer packet listed no verified figure"
            )
        return ReportWriterDraft(executive_summary=points)

    def _registry_index(self, text: str) -> dict[str, ReplaySource]:
        """The page each registry label cites, from the request's own headings."""
        index: dict[str, ReplaySource] = {}
        for match in re.finditer(r"(?m)^## (F\d+): .* \(([^()\n]*)\)$", text):
            host = match.group(2)
            source = next(
                (
                    declared
                    for declared in self.scenario.sources.values()
                    if publisher_identity(declared.url) == host
                ),
                None,
            )
            if source is not None:
                index[match.group(1)] = source
        return index

    def _reply_ReportReviewDraft(self, text: str) -> ReportReviewDraft:
        """Every dimension at the scenario's score, every statement disposed.

        The reply is keyed on the packet's statement manifest -- the ids the
        review must cover -- never on the layout of the statements themselves,
        so the packet may carry more per statement (its text, its reader label,
        the registry labels it cites) without changing this answer.
        """
        if self.review_failure:
            # The provider boundary's own failure type, not a bare exception:
            # an outage is what the caller is written to survive, and raising
            # something else would test the harness's imagination instead of
            # the product's handling of a review that could not be made.
            raise ProviderError("the semantic review was not made")
        manifest = re.search(r"(?m)^Statement ids in this packet: (.*)$", text)
        if manifest is None:
            raise ReplayContractError(
                "the review packet listed no statement manifest"
            )
        statement_ids = [
            item.strip()
            for item in manifest.group(1).split(",")
            if item.strip() and item.strip() != "(none)"
        ]
        return ReportReviewDraft(
            dimensions=ReviewDimensionScores(
                **{name: self.review_score for name in REVIEW_DIMENSIONS}
            ),
            statement_dispositions=[
                StatementDispositionDraft(
                    statement_id=statement_id,
                    disposition=self.disposition_for(statement_id),
                )
                for statement_id in statement_ids
            ],
            rationale="Every statement is carried by the evidence shown.",
        )

    def disposition_for(self, statement_id: str) -> str:
        """The disposition the scenario's reviewer records for one statement.

        A sentence the scenario lists as unsupported is the case's whole point
        -- the review that refuses a report -- and every other sentence is
        carried by the evidence the packet shows for it. A scenario can script
        a refusal for one id, which is how a case makes the terminal review
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
                update={
                    "observation_summary_chars": observation_summary_chars,
                    **scenario.agent_overrides,
                },
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

    The run's clock is the harness's (``replay_clock``), not the machine's: a
    replay reproduces one declared run, so its plan, its evidence timestamps
    and its report carry the harness's instant on every repetition of it.
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

    # Imported here rather than at module scope: the doubles answer provider
    # calls and are unit-tested without a graph run, and importing
    # ``deep_research.evaluation`` executes that whole package -- so a harness
    # whose tests only need the doubles paid for a package they never call.
    from deep_research.evaluation.dependencies import (
        _DeterministicEmbeddings,
        _InMemoryCollection,
    )

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
            # Every date the run stamps comes from the harness rather than from
            # the machine, so a repetition's report is dated by the replay and
            # not by whichever day it happened to run on. See ``replay_clock``.
            clock=replay_clock,
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

    def answering_findings(self) -> list[Any]:
        """The verified findings that name an obligation this run had to answer.

        A finding that names no required target answered nothing, however sound
        it is: a case that counts these is asking whether the run converted its
        evidence into answers, which is the fact a clean exit code does not
        carry.
        """
        from deep_research.utils.types import counted_evidence_targets

        required = {
            target.target_id
            for topic in self.state.sub_topics
            for target in counted_evidence_targets(topic.evidence_targets)
            if target.required
        }
        return [
            finding
            for finding in self.state.verified_findings
            if finding.verification is not None
            and finding.verification.status != "dropped"
            and required & set(finding.target_ids)
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
            if quality.missing_required_target_ids:
                kinds.append("missing_required_target")
            if quality.semantic_review_status != "scored":
                # A missing judgement is not a pass: the reviewed baseline is
                # explicit that an unjudged repetition must never read as one.
                kinds.append("semantic_review_missing")
        for error in self.state.errors:
            kinds.append(f"error:{error.error_type}")
        return tuple(dict.fromkeys(kinds))


def _invariant_relay_labelled_as_relay(run: ReplayRun) -> str | None:
    """A relayed figure is published as relayed, never as the host's own.

    The honesty rule is one sentence: a relay is never presented as the
    organisation it relays. So the row names the site that relays the figure as
    the host, credits the figure to the organisation the page credits, and the
    two are never the same name.
    """
    rows = [row for row in _fact_rows(run) if row.attribution == "relayed"]
    if not rows:
        return "the scenario declared no relayed figure"
    for row in rows:
        if not row.relay_host:
            return f"the relayed row {row.row_id} names no relaying site"
        if row.organisation.casefold() in row.relay_host.casefold():
            return (
                f"the relayed row {row.row_id} credits {row.organisation!r}, "
                "which is the site that relays it"
            )
    host = rows[0].relay_host or ""
    if host.casefold() not in run.report.casefold():
        return f"the report never names the relaying site {host!r}"
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


def _read_for(run: ReplayRun, url: str) -> Any | None:
    return _read_index(run).get(normalize_source_url(url))


def _read_of(run: ReplayRun, finding: Any) -> str | None:
    """The ``read_id`` a finding was extracted from, when the run recorded it."""
    read = run.state.read_records.get(finding.read_id or "")
    return read.read_id if read is not None else None


def _cited_findings(run: ReplayRun) -> list[Any]:
    """Every verified finding a published statement rests on.

    Read through the composition's own statement records, so what is checked is
    what the reader was given rather than what the registry holds.
    """
    composition = run.state.composition
    if composition is None:
        return []
    by_id = {
        finding_fingerprint(finding): finding
        for finding in composition.findings
    }
    cited: list[Any] = []
    for statement in composition.statements:
        for finding_id in statement.finding_ids:
            finding = by_id.get(finding_id)
            if finding is not None and finding not in cited:
                cited.append(finding)
    return cited


def _fact_rows(run: ReplayRun) -> list[Any]:
    composition = run.state.composition
    return list(composition.fact_rows) if composition is not None else []


def _row_read_id(run: ReplayRun, row: Any) -> str | None:
    """The ``read_id`` behind one fact row, through the finding it cites."""
    composition = run.state.composition
    if composition is None:
        return None
    for finding in composition.findings:
        if finding_fingerprint(finding) == row.finding_id:
            return finding.read_id
    return None


def _targets(run: ReplayRun) -> dict[str, Any]:
    from deep_research.utils.types import counted_evidence_targets

    return {
        target.target_id: target
        for topic in run.state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
    }


def _late_pages(run: ReplayRun) -> list[ReplaySource]:
    """The pages of a topic that only a later discovery round could surface."""
    return [
        source
        for topic in run.scenario.topics
        for source in topic.sources
        if source.discovered_on_search > 1
    ]


def _topic_of(run: ReplayRun, url: str) -> ReplayTopic | None:
    for topic in run.scenario.topics:
        if any(source.url == url for source in topic.sources):
            return topic
    return None


def _target_id_for(run: ReplayRun, topic: ReplayTopic | None) -> str | None:
    """The first planned target of a scenario topic, as the plan ids it."""
    if topic is None:
        return None
    return f"topic-{run.scenario.topics.index(topic) + 1:02d}-target-01"


def _invariant_no_false_verification(run: ReplayRun) -> str | None:
    """Every figure a published sentence rests on has a resolved context.

    §5.2's product is one context per kept figure: the period, scope,
    attribution, organisation and kind the Context Check resolved and code
    confirmed against the page. A finding cited while the verifier dropped it,
    a page nobody read, or a kept figure with no organisation, is a figure the
    report published without checking.
    """
    cited = _cited_findings(run)
    if not cited:
        return "the run published no sentence resting on a verified finding"
    for finding in cited:
        verification = finding.verification
        if verification is None or verification.status == "dropped":
            return (
                "the report cites a finding the verifier did not keep: "
                f"{finding.source_url}"
            )
        if _read_of(run, finding) is None:
            return f"the report cites a finding whose page was never read: {finding.source_url}"
        for result in verification.figure_results:
            if not result.kept:
                continue
            if result.context is None or not result.context.organisation:
                return (
                    f"a kept figure of {finding.source_url} reached the report "
                    "with no resolved context"
                )
    return None


def _invariant_mirror_not_double_counted(run: ReplayRun) -> str | None:
    """One body served twice is one work, however many hosts serve it.

    Both reads are real and both are admitted; what the mirror cannot do is
    become two facts. PD-9 merges figures by field key, so the case is read off
    the composition: the rows the mirrored reads produced are one row.
    """
    by_digest: dict[str, list[str]] = {}
    for read in run.state.read_records.values():
        by_digest.setdefault(read.content_sha256, []).append(read.read_id)
    mirrored = [reads for reads in by_digest.values() if len(reads) > 1]
    if not mirrored:
        return "the scenario declared no mirrored body"
    rows = _fact_rows(run)
    for read_ids in mirrored:
        counted = [
            row for row in rows if _row_read_id(run, row) in set(read_ids)
        ]
        if len(counted) > 1:
            return (
                "one body served twice produced "
                f"{len(counted)} fact rows: "
                + ", ".join(row.row_id for row in counted)
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
    rows = _fact_rows(run)
    for topic in run.scenario.topics:
        if len({source.issuer for source in topic.sources}) < 2:
            continue
        for source in topic.sources:
            read = _read_for(run, source.url)
            if read is None:
                return f"the account {topic.title!r} was never read: {source.url}"
            counted = [
                row for row in rows if _row_read_id(run, row) == read.read_id
            ]
            if not counted:
                return f"the account {topic.title!r} was read but never cited"
            for value, unit, _period, _kind in source.figures:
                stated = f"{value} {unit}"
                if not any(row.value == stated for row in counted):
                    return (
                        f"the report never states {stated!r}, the reading "
                        f"{source.url} was measured at"
                    )
            if source.claim.casefold() not in report and not counted:
                return f"the report never states the reading {topic.title!r} carries"
    return None


def _invariant_extra_pass_recovers_missing_target(run: ReplayRun) -> str | None:
    """The second round's new page is what turned the obligation into an answer.

    A recovery that is real is a recovery the ledger shows: the late page was
    acquired, and the obligation its topic carried is answered at the end. A
    run that had the answer in hand before the extra pass recovered nothing.
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
    answered = set(run.answered_target_ids())
    for source in late:
        target_id = _target_id_for(run, _topic_of(run, source.url))
        if target_id is not None and target_id not in answered:
            return (
                f"the obligation {target_id} the late page was bought for is "
                "still unanswered"
            )
    return None


def _invariant_forecast_not_substituted_for_observation(
    run: ReplayRun,
) -> str | None:
    """A forecast is never published as an observation of the same subject.

    A projection and an observation are different facts about the world, and
    the difference is the kind each one states. So the check is the kinds
    themselves: a row answering an obligation that asks for an actual may not
    be a forecast, and one answering a forecast obligation may not be an
    actual.
    """
    rows = _fact_rows(run)
    if not rows:
        return "the run published no fact row"
    targets = _targets(run)
    for row in rows:
        for target_id in row.target_ids:
            target = targets.get(target_id)
            if target is None or target.kind is None:
                continue
            if row.kind != target.kind:
                return (
                    f"the row {row.row_id} answers {target_id} as a "
                    f"{row.kind} where the obligation asks for a {target.kind}"
                )
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
    if run.answering_findings():
        return "the run recorded a finding that could answer an obligation"
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
        for finding in run.state.verified_findings:
            if normalize_source_url(finding.source_url) == normalize_source_url(url):
                return f"a finding cited the remembered lead {url} as its source"
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


def _invariant_missing_target_triggers_one_extra_pass(run: ReplayRun) -> str | None:
    """A missing obligation, and not a judgement, is what sent the run back.

    The case is only about the obligation if nothing else could have prompted
    the second round: the run went back for the target code computed as
    missing, drove a second discovery round, chose at most one extra pass, and
    answered the target from what it found.
    """
    extra = list(run.state.extra_pass_target_ids)
    if not extra:
        return "the run never chose a target for an extra pass"
    if len(extra) > 1:
        return (
            f"the run spent an extra pass for {len(extra)} targets, where "
            "§6.5's extra pass is for the missing ones only and at most one"
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


def _invariant_extra_pass_finds_nothing(run: ReplayRun) -> str | None:
    """A repair that bought nothing stops, publishes once, and names the gap.

    The reason is the assertion: a run that reached its ceiling stopped because
    it ran out of budget, which is a different fact from a repair loop that
    recognised the round it had just bought changed nothing and published the
    obligation under Not found rather than buying another.
    """
    extra = list(run.state.extra_pass_target_ids)
    if not extra:
        return "the run never chose a target for an extra pass"
    if len(extra) > 1:
        return f"the run bought {len(extra)} extra passes, where the cap is one"
    answered = set(run.answered_target_ids())
    still_missing = [target_id for target_id in extra if target_id not in answered]
    if not still_missing:
        return "the extra pass answered the obligation it was bought for"
    composition = run.state.composition
    listed = (
        {target.target_id for target in composition.not_found}
        if composition is not None
        else set()
    )
    if not set(still_missing) & listed:
        return (
            f"the missing obligation {still_missing} never reached the report's "
            "own Not found list"
        )
    return None


def _invariant_revision_noted(run: ReplayRun) -> str | None:
    """A revision is two editions of one fact, and the reader is told.

    PD-9: two findings that answer one obligation and carry different release
    keys are one fact with an earlier edition, never two rows for the reader to
    average. So the row that answers the obligation carries an earlier edition
    whose value differs from the one the row publishes.
    """
    revised = [row for row in _fact_rows(run) if row.earlier]
    if not revised:
        return "no fact row carries an earlier edition, so no revision was noted"
    for row in revised:
        if any(edition.value == row.value for edition in row.earlier):
            return (
                f"the earlier edition of {row.row_id} states the same value, "
                "so nothing was revised"
            )
    return None


def _invariant_scope_corrected_to_all_segments(run: ReplayRun) -> str | None:
    """The corrected scope is what reaches the reader, and the old wording does not.

    Review focus 4: an all-segment figure the extractor wrote as grid-scale.
    Under D8 the Sentence whose scope the page does not state is refused by the
    Statement Check, that finding is marked corrected, and the figure's own
    label still states the scope the page carried.
    """
    kept_scopes = [
        result.context.scope
        for finding in run.state.verified_findings
        for result in (
            finding.verification.figure_results if finding.verification else []
        )
        if result.kept and result.context is not None
    ]
    if "all segments" not in kept_scopes:
        return (
            "no kept figure carries the scope the page states: "
            f"{sorted(scope for scope in kept_scopes if scope)}"
        )
    corrected = [
        finding
        for finding in run.state.verified_findings
        if finding.verification is not None
        and finding.verification.status == "verified_corrected"
    ]
    if not corrected:
        return "the corrected figure did not mark its finding as corrected"
    composition = run.state.composition
    refused = list(composition.rejected_points) if composition is not None else []
    if not any("scope" in point.reason.casefold() for point in refused):
        return "no drafted sentence was refused for the scope it stated"
    if "grid-scale" in run.report.casefold():
        return "the reader's report still states the scope the page does not"
    return None


def _invariant_figure_not_on_page_dropped(run: ReplayRun) -> str | None:
    """A figure the passage does not state is refused, and never published.

    D8's Context Check is what judges this now: the prompt tells it to reject a
    figure its snippet or passage does not actually state, and code records the
    refusal with the checker's own reason. The reader may not see the value the
    refusal was about.
    """
    refused = [
        (finding, result)
        for finding in run.state.verified_findings
        for result in (
            finding.verification.figure_results if finding.verification else []
        )
        if result.dropped_reason == "context_rejected"
    ]
    if not refused:
        return "no figure was refused as one the page does not state"
    report = run.report.casefold()
    for finding, result in refused:
        if result.kept:
            return "a refused figure was kept after all"
        if not result.reason:
            return "a refused figure recorded no reason"
        stated = f"{result.figure.value} {result.figure.unit}".casefold()
        if stated in report:
            return f"the report states {stated!r}, a figure the page does not carry"
        for kept in finding.verification.figure_results if finding.verification else []:
            if kept is result:
                continue
            if kept.kept and kept.context is not None and not kept.context.organisation:
                return "the finding kept another figure with no resolved context"
    return None


def _invariant_evidence_words_not_on_page_rejected(run: ReplayRun) -> str | None:
    """A figure whose quoted words are not on its page is dropped, with its reason."""
    rejected = [
        result
        for finding in run.state.verified_findings
        for result in (
            finding.verification.figure_results if finding.verification else []
        )
        if result.dropped_reason == "evidence_not_on_page"
    ]
    if not rejected:
        return "no figure was dropped for evidence its page does not carry"
    for result in rejected:
        if not result.evidence_words:
            return "a figure was dropped without recording the words it quoted"
        if not result.reason:
            return "a figure was dropped without recording why"
    return None


def _invariant_statement_failure_keeps_sentences(run: ReplayRun) -> str | None:
    """A Statement Check that could not be made keeps every sentence as drafted.

    §5.4: the checker never stops the run. The failure is recorded, the
    sentences publish as drafted, and nothing records them as judged -
    "no judgement" must not read as "judged consistent". The comparison is the
    composition's own ``statement_verdicts``; while nothing writes the map it
    is empty, which passes, and a verdict recorded by a check that never ran
    fails.
    """
    if "evidence_verifier_statement_check_failed" not in run.error_types():
        return "the run recorded no Statement Check failure"
    composition = run.state.composition
    if composition is None or not (composition.summary or composition.sections):
        return "no drafted sentence survived the failed check"
    judged = sorted(
        {
            verdict
            for verdict in composition.statement_verdicts.values()
            if verdict != "unchecked"
        }
    )
    if judged:
        return (
            "a sentence was recorded as judged by a check that never ran: "
            f"{judged}"
        )
    if composition.rejected_points:
        return "a sentence was refused by a check that never ran"
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


def _invariant_review_missing_blocks_acceptance(run: ReplayRun) -> str | None:
    """No judgement is not a pass, however complete the artifacts look."""
    review = run.state.report_review
    if review is not None and review.status == "scored":
        return "the run recorded a semantic judgement after all"
    if run.quality_status == "accepted":
        return "a run with no semantic judgement was accepted"
    if not run.report.strip():
        return "no reader artifact was published"
    if not run.state.verified_findings:
        return "no verified finding survived into the artifacts"
    return None


def _invariant_mechanism_obligation_stays_unanswered(
    run: ReplayRun,
) -> str | None:
    """A cited pair of pages about an outcome is not an answer about its cause.

    The obligation asks for a causal mechanism; the pages in this scenario
    state what happened rather than why. So the check has two halves and needs
    both: findings for the obligation came from pages actually read, and the
    obligation remains outstanding. A strict atom gate correctly refuses to
    attach an outcome-only claim to the mechanism target, so requiring that
    claim to carry the target ID would reject the very behavior under test.
    """
    from deep_research.utils.types import counted_evidence_targets

    mechanism = [
        target
        for topic in run.state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
        if any(
            dimension.casefold().startswith("measure:")
            and "mechanism" in dimension.casefold()
            for dimension in target.required_dimensions
        )
    ]
    if not mechanism:
        return "no obligation in this scenario asked for a mechanism"
    reads = {
        url
        for read in run.state.read_records.values()
        for url in (read.requested_url, read.resolved_url)
    }
    answered = set(run.answered_target_ids())
    for target in mechanism:
        researched = any(
            target.target_id in finding.target_ids
            and finding.source_url in reads
            for finding in run.state.raw_findings
        )
        if not researched:
            return (
                f"no read-backed finding was gathered for {target.target_id}, "
                "so the case proves nothing about the answer it withheld"
            )
        if target.target_id in answered:
            return (
                f"the mechanism obligation {target.target_id} was answered by "
                "evidence that states only the outcome"
            )
    return None


_REPLAY_INVARIANTS: dict[str, Any] = {
    "relay_labelled_as_relay": _invariant_relay_labelled_as_relay,
    "no_false_verification": _invariant_no_false_verification,
    "mirror_not_double_counted": _invariant_mirror_not_double_counted,
    "denied_url_not_retried": _invariant_denied_url_not_retried,
    "both_accounts_cited": _invariant_both_accounts_cited,
    "extra_pass_recovers_missing_target": (
        _invariant_extra_pass_recovers_missing_target
    ),
    "forecast_not_substituted_for_observation": (
        _invariant_forecast_not_substituted_for_observation
    ),
    "empty_answer_answered_nothing": _invariant_empty_answer_answered_nothing,
    "memory_leads_are_not_reads": _invariant_memory_leads_are_not_reads,
    "read_downloaded_once": _invariant_read_downloaded_once,
    "cache_provenance_is_validated": _invariant_cache_provenance_is_validated,
    "late_candidate_reached_decision": _invariant_late_candidate_reached_decision,
    "public_summary_stayed_short": _invariant_public_summary_stayed_short,
    "missing_target_triggers_one_extra_pass": (
        _invariant_missing_target_triggers_one_extra_pass
    ),
    "extra_pass_finds_nothing": _invariant_extra_pass_finds_nothing,
    "revision_noted": _invariant_revision_noted,
    "scope_corrected_to_all_segments": _invariant_scope_corrected_to_all_segments,
    "figure_not_on_page_dropped": _invariant_figure_not_on_page_dropped,
    "evidence_words_not_on_page_rejected": (
        _invariant_evidence_words_not_on_page_rejected
    ),
    "statement_failure_keeps_sentences": (
        _invariant_statement_failure_keeps_sentences
    ),
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
    answering = run.answering_findings()
    if len(answering) < expectation.minimum_answered_findings:
        failures.append(
            f"{len(answering)} answering findings < "
            f"{expectation.minimum_answered_findings} required"
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

    The one production value this does not keep is the wall clock: the run is
    stamped from ``replay_clock``, because a replay reproduces one declared run
    and its report has to say the same thing on every repetition of it.
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
    "REPLAY_CLOCK_INSTANT",
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
    "replay_clock",
    "run_replay_scenario",
]

