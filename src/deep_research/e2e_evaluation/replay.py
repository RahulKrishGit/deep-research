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
from deep_research.agents.evidence import TemporalClaim, normalized_content_sha256
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
    NoteDispositionDraft,
    PreviousDefectResolutionDraft,
    ReportReviewDraft,
    ReportReviewNotesDraft,
    ReviewDefectDraft,
    ReviewDimensionScores,
    ScopedReportReviewDraft,
    ScopedReportReviewNotesDraft,
    StatementDispositionDraft,
)
from deep_research.agents.report_writer import WriterPointDraft
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
from deep_research.agents.verified_facts import subject_named_in
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
from deep_research.utils.types import (
    REVIEW_DIMENSIONS,
    BottomLineDraft,
    ItemMarkDraft,
    ReadRecord,
    ResearchState,
    SectionDraft,
)

# The public progress summary stays at its shipped length. A scenario may ask
# for a different one to prove a case cannot pass by enlarging logs, but the
# default a release case runs at is the production value.
OBSERVATION_SUMMARY_CHARS = 2000

# The instant every replay run stamps its dates from. A replay reproduces one
# declared run rather than printing a document today, so the harness pins the
# clock the run's own agents read instead of letting the wall clock reach them:
# the composed report's own ``generated_on`` then states the day the harness
# printed the report, the plan's as-of date and the evidence timestamps follow
# the same instant, and three repetitions of a row are the same document
# however many times, and whenever, the row is run. Without it a suite that
# straddled 00:00 UTC published three differently dated reports and failed the
# row on a fact about the clock rather than about the agents.
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


# The fields a fixture may script on a scripted reply. Each is a field the
# reply type carries and a page can evidence, so a scenario cannot write an
# override the double has nowhere to put. ``finding``, ``figure`` and ``reason``
# are not here: the request's own labels decide the first two and code decides
# the last.
CONTEXT_OVERRIDE_KEYS = frozenset(
    {
        "scope",
        "attribution",
        "organisation",
        "kind",
        "evidence_words",
        "verdict",
        # D11's two proposals: a period (absolute, or one the page's own date
        # resolves from the quoted words) and the subject the figure is about.
        "period",
        "subject",
    }
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
    # The subjects of ``figures``, position for position, exactly as the
    # extraction records them (``FindingFigureDraft.subject``). A subject is
    # the thing the figure is about as the page names it, and it is what keeps
    # two figures equal in value, organisation, period and kind apart (D11),
    # so a fixture that states none for a figure is declaring that the figure
    # is about its topic as a whole rather than about a named thing.
    figure_subjects: tuple[str | None, ...] = ()
    # The date the page states for itself, as the Source Evaluator records it:
    # the value and the page's own words for it (``TemporalClaim``). The words
    # have to be the page's text verbatim, exactly as an excerpt does -- the
    # real evaluator admits a date only from a quote the read carries -- and
    # the value is what a relative period ("this year") is resolved against
    # (D11), so a fixture that declared one its page does not state would be
    # scripting a page date no reader could have earned.
    publication_date: tuple[str, str] | None = None
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
        if self.figure_subjects and len(self.figure_subjects) != len(self.figures):
            raise ValueError(
                f"{self.url} declares {len(self.figure_subjects)} subject(s) "
                f"({', '.join(repr(s) for s in self.figure_subjects)}) for "
                f"{len(self.figures)} figure(s)"
            )
        if self.publication_date is not None and (
            self.publication_date[1] not in self.text
        ):
            raise ValueError(
                f"the page date of {self.url} quotes "
                f"{self.publication_date[1]!r}, which its text does not state"
            )
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
class ExtraEvidenceTarget:
    """One additional obligation of a topic that already has a primary one.

    A plan sub-topic can carry more than one evidence target (the writer
    places every citable finding into exactly one part by its *first*
    matching target, spec §6.1), and a part with one target already answered
    and a second one still missing on the opening pass is what
    ``scoped-redraft-after-a-named-defect``'s sibling case (the P0-1
    extra-pass regression) needs: the part already has a section before the
    extra pass runs.
    """

    question: str
    measure: str
    unit_dimension: str = ""
    period: str = ""
    kind: str = ""
    required: bool = True
    geography: str = ""
    organisation: str = ""


@dataclass(frozen=True)
class ReplayTopic:
    """One planned sub-topic and the obligations it carries."""

    title: str
    question: str
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
    # The obligation's structured fields, exactly as the planner's draft
    # carries them (Task 1.4, ``EvidenceTargetDraft``): the measure it asks
    # for, the unit dimension, period and kind its evidence has to state, and
    # the geography and organisation it names. An empty unit dimension is a
    # qualitative obligation (PD-7) -- answered by a verified finding that
    # names it -- which is what a topic whose measure states no unit carries.
    measure: str = ""
    unit_dimension: str = ""
    period: str = ""
    kind: str = ""
    geography: str = ""
    organisation: str = ""
    # The labels the scripted writer puts on this topic's answer row. Both are
    # published only when the row's evidence attests their words, so a scenario
    # names words its own pages state — the composer repairs an unattested cell
    # to ``not stated``, which would leave the row with nothing to answer.
    answer_labels: tuple[str, str] = ("", "")
    # Further evidence targets this same part carries, beyond the primary
    # one above -- a part with two, one answered on the opening pass and one
    # still missing, so it already has a section before the extra pass its
    # missing target buys (D4, §6.5) ever runs.
    extra_targets: tuple[ExtraEvidenceTarget, ...] = ()


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

    required_invariants: tuple[str, ...] = ()
    required_report_phrases: tuple[str, ...] = ()
    """Words the published report has to contain.

    A decisive assertion like "both sources are cited" is a fact about the
    artifact, so a case states it as text the reader would see rather than as
    a count of records no reader was shown.
    """


@dataclass(frozen=True)
class ReplayReviewDefect:
    """One material defect the scripted *first* full review names (T5 addendum).

    Its ``target_ids`` are what routes the redraft it buys to exactly the
    part(s) that own them (spec §6.9's ``_route_defects``); a scenario with
    two or more parts and one such defect is what leaves the other part(s)
    carried over byte-identical, which is what lets the second review be
    scoped rather than a second full one. ``ReplayCompleter`` returns this
    defect on the first ``ReportReviewDraft`` reply only, and marks it
    resolved on every scoped re-review after -- a controlled case scripts one
    redraft, never a loop.
    """

    target_ids: tuple[str, ...]
    kind: str = "presentation"
    severity: str = "major"
    problem: str = "This part's section should restate its own figure more plainly."



@dataclass(frozen=True)
class ReplayScenario:
    """One fully scripted offline replay of the real stack."""

    case_id: str
    question: str
    topics: tuple[ReplayTopic, ...]
    expectation: CaseExpectation
    version: int = 1
    max_extra_passes: int = 1
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
    # The one material defect the scripted first full review names, naming
    # the part(s) its ``target_ids`` route to (T5 addendum, spec §6.9). Left
    # unset, the review never returns a defect and no redraft is bought.
    review_defect: ReplayReviewDefect | None = None
    # The scoped re-review that could not be used: the provider raises,
    # exactly as an outage would, so the run has to fall back to one fresh
    # full review rather than accept a scoped-derived judgement (T5
    # addendum). Meaningless without ``review_defect``, since nothing buys a
    # redraft (and so a scoped attempt) without one.
    scoped_review_failure: bool = False
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


def _printed_figures(
    body: str,
) -> list[tuple[int, str, str | None, str, str | None]]:
    """Every figure a Context Check block lists.

    ``(number, value, period, kind, subject)``, read from the shipped line
    format: ``figure 1: 4.5 out of 5 | recorded period 2026 | recorded kind
    actual``, with the `` | recorded subject …`` part present only when the
    figure carries one (Task 5.7b). The kind is a single word, which is what
    keeps it from swallowing the subject part a lazier group would take.
    """
    figures: list[tuple[int, str, str | None, str, str | None]] = []
    for match in re.finditer(
        r"(?m)^  figure (\d+): (\S+) (.+?) \| recorded period (.*?) \| "
        r"recorded kind (\S+)(?: \| recorded subject (.*))?$",
        body,
    ):
        period = match.group(4).strip()
        kind = match.group(5).strip()
        subject = match.group(6)
        figures.append(
            (
                int(match.group(1)),
                match.group(2),
                None if period in ("", "none", "not stated") else period,
                "actual" if kind in ("", "none") else kind,
                None if subject is None else subject.strip(),
            )
        )
    return figures


def _registry_entries(
    text: str,
) -> list[tuple[str, str, str, str, list[tuple[str, str, str, str, str, str | None]]]]:
    """Every registry entry the writer's request lists.

    ``(label, title, host, snippet, figures)``, where each figure is
    ``(value, unit, period, kind, organisation, subject)``. Read from the
    registry's own format, which is what the writer's prompt is built from: a
    figure line carries its `` | subject …`` part after the unit only when the
    Context Check resolved one (D11), so the unit group has to stop there
    rather than run on to the next part. An entry with no figure line is a
    finding whose page stated no figure: ``finding_registry`` still lists
    those, so the request really carries them and the double reads them rather
    than refusing the packet.
    """
    headers = list(re.finditer(r"(?m)^## (F\d+): (.*) \(([^()\n]*)\)$", text))
    entries: list[
        tuple[str, str, str, str, list[tuple[str, str, str, str, str, str | None]]]
    ] = []
    for position, header in enumerate(headers):
        end = (
            headers[position + 1].start()
            if position + 1 < len(headers)
            else len(text)
        )
        body = text[header.end() : end]
        figures = [
            (
                match.group(1),
                match.group(2),
                match.group(4).strip(),
                match.group(5),
                match.group(6).strip(),
                None if match.group(3) is None else match.group(3).strip(),
            )
            for match in re.finditer(
                r"(?m)^F\d+ \| figure \d+: (\S+) (.+?)(?: \| subject (.*?))? \| "
                r"period (.*?) \| kind (\w+) \| organisation (.*?) \| label: ",
                body,
            )
        ]
        entries.append(
            (
                header.group(1),
                header.group(2),
                header.group(3),
                _printed_line(body, "snippet"),
                figures,
            )
        )
    return entries


def _cited_finding_blocks(body: str) -> list[tuple[str, str]]:
    """One ``(own line, sub-lines)`` pair per cited finding in a block.

    The packet indents each finding's own line by two spaces (``  <label>:
    <figures or (no kept figures)>``) and its sub-lines by four (``    snippet:
    …``), which is what tells one finding's lines from the next's. The block's
    own lines -- ``sentence:``, ``cited findings:`` -- carry no indent.
    """
    blocks: list[tuple[str, str]] = []
    for line in body.splitlines():
        if line.startswith("  ") and not line.startswith("    "):
            blocks.append((line, ""))
            continue
        if line.startswith("    ") and blocks:
            own, sublines = blocks[-1]
            blocks[-1] = (own, f"{sublines}{line}\n")
    return blocks


def _cited_subline(lines: str, label: str) -> str:
    """One indented ``    label: value`` line under a cited finding.

    The Statement Check packet indents a finding's own lines under its label
    (``    snippet: …``, ``    attributed to: …``), which is what tells them
    from the block's own unindented ``sentence:`` and ``cited findings:``.
    """
    match = re.search(rf"(?m)^    {re.escape(label)}: (.*)$", lines)
    return match.group(1).strip() if match else ""


def _snippet_sentence(snippet: str) -> str:
    """The point a figureless finding is written as: its own words, restated.

    There is no figure to state and no reader label to build, so the draft
    carries the page's sentence -- which is what the finding is -- and the
    Statement Check judges it like any other sentence.
    """
    text = " ".join(snippet.split())
    if not text:
        return ""
    sentence = text[0].upper() + text[1:]
    return sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."


def _written_sentence(
    value: str,
    unit: str,
    period: str,
    kind: str,
    organisation: str,
    subject: str | None = None,
) -> str:
    """The sentence one registry line becomes: the figure, and nothing else.

    The reader label carries the rest (who the figure is credited to, whether
    it is a forecast and, for a forecast, its release), because D8 moves the
    wording judgement to the Statement Check and the label to code. A line
    whose period the page did not state states no period rather than inventing
    one. A line with a subject names it first, as the thing the sentence is
    about: two figures can be equal in value, organisation, period and kind
    and still be two facts ("Kettle K1" rated the same as "Kettle K2"), and a
    sentence that did not say which is which would be refused as a restatement
    of the other row.
    """
    verb = "projects" if kind == "forecast" else "reports"
    stated = f" for {period}" if period and period != "not stated" else ""
    named = f"{subject}: " if subject else ""
    return f"{named}{organisation} {verb} {value} {unit}{stated}."


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
        self.review_defect: ReplayReviewDefect | None = scenario.review_defect
        self.scoped_review_failure: bool = scenario.scoped_review_failure
        # Returned on the first ``ReportReviewDraft`` reply only: a redraft
        # buys one re-run (spec §6.9), and a controlled case scripts one,
        # never a defect that keeps reappearing after it is resolved.
        self._review_defect_returned = False
        self.invented_prose: str = scenario.invented_prose
        # Every sentence the writer double drafted, mapped to the page it was
        # drafted from. This is how a scenario's per-page wording override
        # finds the sentence that rests on that page: the sentence is the one
        # thing the writer's draft and the Statement Check's request both
        # carry, while the labels the request prints are the writer's
        # *reader* labels, which a fixture has no way to name.
        self.drafted_sources: dict[str, list[ReplaySource]] = {}

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
                            required=topic.required,
                            measure=topic.measure,
                            unit_dimension=topic.unit_dimension,
                            period=topic.period,
                            kind=topic.kind,
                            geography=topic.geography,
                            organisation=topic.organisation,
                        ),
                        *[
                            EvidenceTargetDraft(
                                question=extra.question,
                                required=extra.required,
                                measure=extra.measure,
                                unit_dimension=extra.unit_dimension,
                                period=extra.period,
                                kind=extra.kind,
                                geography=extra.geography,
                                organisation=extra.organisation,
                            )
                            for extra in topic.extra_targets
                        ],
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
                        FindingFigureDraft(value=v, unit=u, period=p, kind=k, subject=s)
                        for (v, u, p, k), s in zip(
                            source.figures, self._subjects_of(source)
                        )
                    ],
                    target_ids=list(planned),
                )
            )
        return SubTopicFindingsDraft(findings=findings)

    def _subjects_of(self, source: ReplaySource) -> tuple[str | None, ...]:
        """One subject per figure, padded when the fixture declares none.

        An empty ``figure_subjects`` is a page whose figures are about their
        topic as a whole, which is the shape every figure carried before D11.
        """
        if not source.figure_subjects:
            return (None,) * len(source.figures)
        return source.figure_subjects

    def _planned_target_ids(self, text: str, coverage_id: str) -> list[str]:
        """One topic's target ids, read out of the request's target list.

        A finding names the planned targets it serves, and the plan's ids are
        the only ones the extraction may name — the coverage id the read was
        fetched for (``topic-01``) is a different vocabulary and is dropped.
        This harness can honestly do only what it does here: it knows which
        sub-topic fetched the read, not which sentence answers which
        obligation, so it names that topic's targets and leaves the binding to
        the Fact Checker's own dimension check.

        The line's own shape is the request's: the id, the owner bracket (the
        coverage id alone, or with the sub-topic's title after a colon), an
        optional ``[required]`` marker, then the question. Only the id is read
        here, so the brackets may grow without this parser decoding them.
        """
        catalogue = re.findall(
            rf"^- ({re.escape(coverage_id)}-target-\d+) "
            rf"\[{re.escape(coverage_id)}(?:: [^\]]*)?\]"
            rf"(?: \[required\])?: ",
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
                    publication_date=(
                        TemporalClaim(
                            value=source.publication_date[0],
                            quote=source.publication_date[1],
                        )
                        if source.publication_date is not None
                        else None
                    ),
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
            for number, _value, period, kind, subject in _printed_figures(body):
                figures.append(
                    FigureCheckDraft(
                        finding=label,
                        figure=number,
                        period=override.get("period", period),
                        scope=override.get("scope", recorded.get("scope")),
                        subject=override.get("subject", subject),
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
        # The production writer's own flight keys (spec §6.7): ``P{part:02d}.``
        # for a section's own batch, ``B`` for the bottom line's, both
        # renumbered to the reader's ``S001…`` only after every check
        # finishes, so the check itself never sees an ``S`` label from that
        # path -- but a test that calls this double directly still builds its
        # own items with plain ``S00n`` labels, which stay accepted too.
        for label, body in _packet_blocks(text, r"(?:S|P\d+\.|B)"):
            sentence = _printed_line(body, "sentence")
            self._require_cited_findings(body, label)
            override = self._statement_override(sentence)
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

    def _require_cited_findings(self, body: str, label: str) -> None:
        """Every cited finding is shown with the lines the shipped format gives it.

        The packet prints each finding on its own line, then the sentence's own
        ``    snippet:``, and then the body it is ``    attributed to:`` -- that
        last line for a finding whose line reads ``(no kept figures)`` alone,
        because a kept figure's own line already states its attribution (Task
        5.7a: a second, extraction-time body could credit a different one). Both
        halves are checked, per finding: a line with no body to judge it against,
        or a body line crediting a figure that was kept, is a packet the
        production writer cannot build, and the double refuses it rather than
        answering "consistent" about evidence that was never shown.
        """
        for own, sublines in _cited_finding_blocks(body):
            figureless = "(no kept figures)" in own
            if not _cited_subline(sublines, "snippet"):
                raise ReplayContractError(
                    f"the Statement Check block {label} cites a finding with no "
                    "snippet line under it"
                )
            # A finding with no kept figure is shown with the body it is
            # ``attributed to`` when the extraction admitted one, and otherwise
            # with the site it was ``read at`` (the re-review's C1: a host is
            # where a statement was read, never the body that made it). Either
            # line satisfies the figureless case; a finding whose figure was
            # kept shows neither, because its own figure line states its
            # attribution.
            shown = bool(_cited_subline(sublines, "attributed to")) or bool(
                _cited_subline(sublines, "read at")
            )
            if figureless and not shown:
                raise ReplayContractError(
                    f"the Statement Check block {label} cites a finding with no "
                    "kept figure and prints neither the body it is attributed to "
                    "nor the site it was read at"
                )
            if shown and not figureless:
                raise ReplayContractError(
                    f"the Statement Check block {label} prints an attribution or "
                    "read-at line for a finding whose figure was kept"
                )

    def _statement_override(self, sentence: str) -> dict[str, str]:
        """The override of the page whose drafted sentence this is.

        Keyed on the sentence itself: the writer collapses whitespace the same
        way before it asks, so the two requests name one sentence one way. A
        scenario that declares an override is asking for a verdict, so the map
        has to have been filled -- an empty map means the writer was never
        asked, which is a harness fault and not something to answer
        "consistent" about.
        """
        declared = any(
            source.statement for source in self.scenario.sources.values()
        )
        if not self.drafted_sources and declared:
            raise ReplayContractError(
                "the Statement Check was asked before the writer's request was "
                "seen, so no drafted sentence can carry this scenario's override"
            )
        for source in self.drafted_sources.get(" ".join(sentence.split()), []):
            if source.statement:
                return source.statement
        return {}

    def _material_block(self, text: str, header: str) -> str:
        """The material section's own text, up to the next top-level ``# ``
        header (or the end of the request).

        A sub-header (``## F01: ...``) never matches ``^# ``, which is what
        lets this stay a simple line scan instead of a nested parser: every
        request this harness builds nests its detail under ``## ``/``### ``,
        never a second top-level ``# ``.
        """
        headers = list(re.finditer(r"(?m)^# .*$", text))
        for index, match in enumerate(headers):
            if match.group().strip() == f"# {header}":
                end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
                return text[match.end():end]
        return ""

    def _reply_SectionDraft(self, text: str) -> SectionDraft:
        """One point per figure line of this part's own registry block.

        Scoped to ``# Verified findings for this part`` alone, never
        ``# Context only``: a context-only finding is listed for the writer to
        read, not to cite (spec §6.4 rule 3), and a double that drafted from it
        would cite a label the real writer is refused for citing. The line is
        the registry's own format (``F01 | figure 1: 10.4 GW | period 2024 |
        kind actual | organisation Wood Mackenzie | label: ...``), so the
        draft states the figure its verified finding carries, and the
        code-built reader label carries what the sentence does not say (who
        the figure is credited to, and whether it is a forecast). A figure
        line that names a subject (D11) is also marked as an option: the
        subject is the mark's name and the value-and-unit span is its verdict,
        both verbatim spans of the drafted sentence (spec §6.4 rule 8), so the
        options table (§4.2) has real cells to build from a replay run.
        """
        title_block = self._material_block(text, "This part of the question")
        title = title_block.strip().splitlines()[0].strip() if title_block.strip() else "Findings"
        block = self._material_block(text, "Verified findings for this part")
        index = self._registry_index(text)
        points: list[WriterPointDraft] = []
        for label, _title, _host, snippet, figures in _registry_entries(block):
            source = index.get(label)
            if figures:
                drafted: list[tuple[str, list[ItemMarkDraft]]] = [
                    (
                        _written_sentence(*figure),
                        [ItemMarkDraft(name=figure[-1], verdict=f"{figure[0]} {figure[1]}", by=label)]
                        if figure[-1] else [],
                    )
                    for figure in figures
                ]
            else:
                # A finding whose page stated no figure. It is a registry row
                # production prints, so the draft restates the finding itself
                # rather than refusing the pass.
                drafted = [(_snippet_sentence(snippet), [])]
            if self.invented_prose and not points and drafted:
                # The case's own fault: a writer dressing a verified figure in
                # prose no page states. The Statement Check is what refuses it
                # now, so the draft carries the words and the checker's script
                # reads them.
                sentence, items = drafted[0]
                drafted[0] = (f"{sentence} This is because {self.invented_prose}.", items)
            for sentence, items in drafted:
                if not sentence:
                    continue
                if source is not None:
                    self.drafted_sources.setdefault(
                        " ".join(sentence.split()), []
                    ).append(source)
                points.append(
                    WriterPointDraft(text=sentence, finding_labels=[label], items=items)
                )
        if not points:
            raise ReplayContractError(
                "the section packet listed no finding to draft from"
            )
        return SectionDraft(title=title, points=points)

    _BOTTOM_LINE_STATEMENT = re.compile(r"^(.*) \(cites ([^;()]*)(?:; options: .*)?\)$")

    def _reply_BottomLineDraft(self, text: str) -> BottomLineDraft:
        """Up to 4 of the checked section statements' own texts, with their
        labels (spec §11.3): a bottom line built only from what a part's own
        draft already had verified, never inventing new prose. ``cites
        nothing`` (a statement with no finding label) carries no label."""
        block = self._material_block(text, "Checked statements")
        sentences: list[WriterPointDraft] = []
        for line in block.splitlines():
            if not line.startswith("- "):
                continue
            match = self._BOTTOM_LINE_STATEMENT.match(line[2:])
            if match is None:
                continue
            point_text, cites = match.group(1), match.group(2)
            labels = (
                [] if cites.strip() == "nothing"
                else [label.strip() for label in cites.split(",")]
            )
            sentences.append(WriterPointDraft(text=point_text, finding_labels=labels))
            if len(sentences) == 4:
                break
        if not sentences:
            raise ReplayContractError(
                "the bottom-line packet listed no checked statement"
            )
        return BottomLineDraft(sentences=sentences)

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
        defects: list[ReviewDefectDraft] = []
        if self.review_defect is not None and not self._review_defect_returned:
            self._review_defect_returned = True
            defects.append(
                ReviewDefectDraft(
                    kind=self.review_defect.kind,
                    severity=self.review_defect.severity,
                    target_ids=list(self.review_defect.target_ids),
                    problem=self.review_defect.problem,
                )
            )
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
            defects=defects,
            rationale="Every statement is carried by the evidence shown.",
        )

    def _reply_ReportReviewNotesDraft(self, text: str) -> ReportReviewNotesDraft:
        """The whole-report reply above, plus a verdict for every reader note listed."""
        return ReportReviewNotesDraft(
            **self._reply_ReportReviewDraft(text).model_dump(),
            note_dispositions=self.note_dispositions_for(text),
        )

    def note_dispositions_for(self, text: str) -> list[NoteDispositionDraft]:
        """``honoured`` for every reader note the review packet lists (live-briefs spec §4.6).

        Read from the request's own ``# Reader notes`` section, so a packet
        without notes gets no entry and every existing case replies exactly
        as it did: a scripted reviewer has no way to judge a note against a
        report, and the one honest default is that the report followed it.
        """
        block = self._material_block(text, "Reader notes")
        return [
            NoteDispositionDraft(note_id=note_id, status="honoured")
            for note_id in re.findall(r"(?m)^- (n\d+): ", block)
        ]

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

    def _reply_ScopedReportReviewDraft(self, text: str) -> ScopedReportReviewDraft:
        """Accept a redraft: every changed statement supported, every
        previous defect resolved, no new defect (T5 addendum).

        Generic over the packet's own content, exactly as
        ``_reply_ReportReviewDraft`` is keyed on the full packet's own
        manifest rather than on one case's specifics: the changed statement
        ids and the previous defect ids are both read back out of the
        request's own sections, so any scenario whose redraft buys a scoped
        re-review is answered the same way -- the one redraft closed
        whatever the first review named, and judgement of the untouched
        statements is exactly what the packet's own carried dispositions
        already supply.
        """
        if self.scoped_review_failure:
            # The provider boundary's own failure type: the T5 addendum's
            # fallback exists precisely for a scoped call that could not be
            # made, and raising anything else would test the harness's
            # imagination instead of the product's own fallback.
            raise ProviderError("the scoped re-review was not made")
        changed_block = self._material_block(text, "Changed statement ids").strip()
        changed_line = changed_block.splitlines()[-1] if changed_block else ""
        changed_ids = [
            item.strip()
            for item in changed_line.split(",")
            if item.strip() and item.strip() != "(none)"
        ]
        previous_defects = self._material_block(text, "Previous defects")
        defect_ids = re.findall(r"(?m)^- (\S+) \(", previous_defects)
        return ScopedReportReviewDraft(
            dimensions=ReviewDimensionScores(
                **{name: self.review_score for name in REVIEW_DIMENSIONS}
            ),
            statement_dispositions=[
                StatementDispositionDraft(
                    statement_id=statement_id,
                    disposition=self.disposition_for(statement_id),
                )
                for statement_id in changed_ids
            ],
            previous_defect_resolutions=[
                PreviousDefectResolutionDraft(
                    defect_id=defect_id,
                    resolved=True,
                    note="The redraft resolved it.",
                )
                for defect_id in defect_ids
            ],
            new_defects=[],
            rationale="The redraft closed every previous defect; nothing else changed.",
        )

    def _reply_ScopedReportReviewNotesDraft(
        self, text: str
    ) -> ScopedReportReviewNotesDraft:
        """The scoped reply above, plus a verdict for every reader note listed."""
        return ScopedReportReviewNotesDraft(
            **self._reply_ScopedReportReviewDraft(text).model_dump(),
            note_dispositions=self.note_dispositions_for(text),
        )


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
        """The obligations the run's own accounting says it answered.

        Read from the quality snapshot the writer's pass computed (PD-5,
        §6.6), never re-derived here: whether an obligation is answered is
        code's question, and a harness that answered it its own way could only
        disagree with the run it is judging. A pass that composed no report
        answered nothing.
        """
        quality = self.state.quality
        return list(quality.answered_target_ids) if quality is not None else []

    def error_types(self) -> list[str]:
        return [error.error_type for error in self.state.errors]

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
    two are never the same name. Spec §11.3: the rule is per *page*, not per
    row -- a wire service relaying four separate obligations is one relaying
    site four times over, so any other fact row read from that same host must
    carry the same ``relayed`` attribution, never a stray ``own``.
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
    relayed_hosts = {row.relay_host for row in rows if row.relay_host}
    composition = run.state.composition
    if composition is not None:
        host_by_finding_id = {
            finding_fingerprint(finding): publisher_identity(finding.source_url)
            for finding in composition.findings
        }
        for row in _fact_rows(run):
            host = host_by_finding_id.get(row.finding_id)
            if host in relayed_hosts and row.attribution != "relayed":
                return (
                    f"row {row.row_id} reads from {host!r}, a site this run "
                    "relays elsewhere, but is attributed "
                    f"{row.attribution!r}, not 'relayed'"
                )
    host = rows[0].relay_host or ""
    if host.casefold() not in run.report.casefold():
        return f"the report never names the relaying site {host!r}"
    return None


def _invariant_maker_row_is_own(run: ReplayRun) -> str | None:
    """The maker's own page credits its own figure, never as a relay.

    ``maker-notes-vs-relay``'s premise: "Example Games" is also the report's
    own title text, so a phrase match on the name cannot tell the maker's own
    row from the relay's mention of it. This reads the typed fact row for the
    maker's own page (``games.example.test``) instead.
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    by_id = {
        finding_fingerprint(finding): finding for finding in composition.findings
    }
    maker_rows = [
        row
        for row in composition.fact_rows
        if row.finding_id in by_id
        and publisher_identity(by_id[row.finding_id].source_url) == "games.example.test"
    ]
    if not maker_rows:
        return "no fact row is bound to the maker's own page"
    for row in maker_rows:
        if row.attribution != "own":
            return (
                f"the maker's own row {row.row_id} is attributed "
                f"{row.attribution!r}, not 'own'"
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


def _opening_pages(run: ReplayRun, late: Sequence[ReplaySource]) -> list[ReplaySource]:
    """The pages the opening round could acquire for the late pages' topics."""
    topics = {id(_topic_of(run, source.url)) for source in late}
    return [
        source
        for topic in run.scenario.topics
        if id(topic) in topics
        for source in topic.sources
        if source.discovered_on_search == 1
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
    """The obligation is really missing after pass 0, and the extra pass answers it.

    A recovery that is real is a recovery the ledger shows, on all three legs:
    the run bought exactly one extra pass for the one obligation that was
    missing (D4, §6.5), the late page was acquired only once that pass ran,
    and the obligation its topic carried is answered at the end. A run that
    had the answer in hand before the extra pass -- because a second
    discovery round happened inside the first pass, which is a different fact
    from an extra pass -- recovered nothing.
    """
    extra = list(run.state.extra_pass_target_ids)
    if extra != ["topic-01-target-01"]:
        return (
            "the extra pass was not bought for the missing obligation alone: "
            f"{extra}"
        )
    if run.state.iteration != 1:
        return (
            f"the run spent {run.state.iteration} extra passes, where D4's cap "
            "and the product default are one"
        )
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
    for source in _opening_pages(run, late):
        if source.figures:
            return (
                f"the opening round's page {source.url} states a figure, so the "
                "obligation was met before the extra pass"
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
    """A tidy report that answered no obligation says so, and says which.

    The case exists to refuse the reading where clean structure and a
    recommendation stand in for an answer. Under PD-23 the run does publish,
    and an accepted report that lists its unanswered obligations under Not
    found is the intended result -- what the report may not do is imply an
    answer it never had. So the check is that nothing was credited, that every
    obligation the run could not answer reached the report's own Not found
    list, and that no recommendation stands in for the answer.
    """
    answered = run.answered_target_ids()
    if answered:
        return f"the run answered {sorted(answered)} without usable evidence"
    quality = run.state.quality
    composition = run.state.composition
    missing = set(quality.missing_required_target_ids) if quality else set()
    listed = (
        {target.target_id for target in composition.not_found}
        if composition is not None
        else set()
    )
    if missing and not missing.issubset(listed):
        return (
            "obligations were left unanswered without reaching the report's own "
            f"Not found list: {sorted(missing - listed)}"
        )
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
    """The missing obligation buys one extra pass, and the pass answers it.

    Three halves, all readable from the run. The opening round ends with
    ``topic-01-target-01`` missing -- code chose it, not a judgement -- and
    spends exactly one extra pass for it (D4, §6.5). The page that answers it
    was not read before that pass: the opening round's search surfaced the
    topic's records and ran out of turns before reaching the page that states
    the figure, so the answering page was still an unread candidate when the
    pass began. And the pass is what turns the obligation into an answer.
    """
    extra = list(run.state.extra_pass_target_ids)
    if extra != ["topic-01-target-01"]:
        return (
            "the extra pass was not bought for the missing obligation alone: "
            f"{extra}"
        )
    if run.state.iteration != 1:
        return (
            f"the run spent {run.state.iteration} extra passes, where D4's cap "
            "and the product default are one"
        )
    late = _late_pages(run)
    if not late:
        return "the scenario declared no page the opening round could not reach"
    for source in late:
        if source.url not in run.replay.http.fetched:
            return (
                f"the page the extra pass was bought for was never read: "
                f"{source.url}"
            )
    if "topic-01-target-01" not in run.answered_target_ids():
        return "the extra pass did not turn the obligation into an answer"
    for source in _opening_pages(run, late):
        if source.figures:
            return (
                f"the opening round's page {source.url} states a figure, so the "
                "obligation was met before the extra pass"
            )
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
    if run.state.iteration != 1:
        return (
            f"the run spent {run.state.iteration} extra passes, where D4's cap "
            "and the product default are one"
        )
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


def _invariant_subjects_stay_apart(run: ReplayRun) -> str | None:
    """Two things rated the same are two rows, and both sentences survive.

    D11: a subject is what keeps two figures of equal value, organisation,
    period and kind apart, so the row that lost it would be one row for two
    products. The writer's own restatement guard counts a row only for the
    subject the sentence names, so the second half is the same fact seen from
    the reader's side: a sentence refused as a restatement it is not.
    """
    rows = _fact_rows(run)
    twins = [
        (left, right)
        for position, left in enumerate(rows)
        for right in rows[position + 1 :]
        if left.value == right.value
        and left.organisation == right.organisation
        and left.period == right.period
        and left.kind == right.kind
    ]
    if not twins:
        return "no two fact rows share a value, organisation, period and kind"
    for left, right in twins:
        if not left.subject or not right.subject:
            return (
                f"{left.row_id} and {right.row_id} carry one value but not two "
                "subjects"
            )
        if subject_named_in(left.subject, right.subject) or subject_named_in(
            right.subject, left.subject
        ):
            return (
                f"{left.row_id} and {right.row_id} name the same subject: "
                f"{left.subject!r} and {right.subject!r}"
            )
    refused = [
        point
        for point in (
            run.state.composition.rejected_points
            if run.state.composition is not None
            else []
        )
        if point.reason.casefold().startswith("restates")
    ]
    if refused:
        return (
            f"a point was refused as a restatement of a row it does not state: "
            f"{refused[0].reason!r}"
        )
    return None


def _invariant_versions_stay_apart(run: ReplayRun) -> str | None:
    """Two editions of two different things are two rows, and no release history.

    PD-9 folds two rows that answer one obligation and differ only in their
    release, so a subject that names *which* version the figure is about is what
    keeps a patch's notes from folding into the previous patch's. The row that
    lost the subject folds them and prints an earlier edition nobody earned.
    """
    rows = _fact_rows(run)
    for left in rows:
        for right in rows:
            if left is right:
                continue
            if not (set(left.target_ids) & set(right.target_ids)):
                continue
            if left.subject and right.subject and left.subject != right.subject:
                if left.earlier or right.earlier:
                    return (
                        f"{left.row_id} and {right.row_id} about "
                        f"{left.subject!r} and {right.subject!r} carry an earlier "
                        "edition, which is a folded revision"
                    )
    apart = [
        row
        for row in rows
        if row.subject and not row.earlier
        and any(
            other is not row
            and set(other.target_ids) & set(row.target_ids)
            and other.subject
            and other.subject != row.subject
            for other in rows
        )
    ]
    if len(apart) < 2:
        return (
            "fewer than two rows answering one obligation carry their own "
            "subject"
        )
    return None


def _invariant_one_fact_row(run: ReplayRun) -> str | None:
    """One measurement, however many spellings of its subject, is one row.

    Fable §8.6 step 3: a subject that only restates what its own target already
    says names nothing. A run that treated "United States" and "widget
    adoption" as two subjects would print two rows for one figure.
    """
    rows = _fact_rows(run)
    if len(rows) != 1:
        return (
            f"the run printed {len(rows)} fact rows for one figure: "
            f"{[row.subject for row in rows]}"
        )
    return None


def _invariant_period_resolved_from_page_date(run: ReplayRun) -> str | None:
    """A relative period is kept only from a date its own page states.

    D11 (§8.7): "this year" states no year by itself. The kept figure carries
    the period the page's date resolved it to, records the date it came from,
    and the same words on a page that states no date are refused outright --
    never published with a period no page carried.
    """
    resolved = [
        row
        for row in _fact_rows(run)
        if row.period_resolved_from == "2026-02-20"
    ]
    if not resolved:
        return (
            "no fact row records the page date 2026-02-20 as the period it "
            "resolved"
        )
    for row in resolved:
        if not row.period:
            return f"{row.row_id} resolved a period but states none"
    refused = [
        result
        for finding in run.state.verified_findings
        for result in (
            finding.verification.figure_results if finding.verification else []
        )
        if result.dropped_reason == "correction_not_on_page"
    ]
    if not refused:
        return (
            "the undated page's figure was not dropped for stating a period "
            "its page does not carry"
        )
    if "2026-02-20" not in run.report:
        return "the reader's report never names the date the period came from"
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
    # The Statement Check's own reason names the basis the page states, or the
    # wording the page does not: the refusal is what keeps the overstated
    # sentence out of the reader's report.
    if not any(
        "all segments" in point.reason.casefold()
        or "grid-scale" in point.reason.casefold()
        for point in refused
    ):
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

    §5.4: the checker never stops the run. The failure is recorded, every
    kept sentence's own verdict is ``"unchecked"`` -- never any other value,
    and never absent -- no point is refused, the printed text is exactly what
    the writer drafted (a correction is something only a check that ran could
    have made), and PD-10 counts a recorded batch failure's ``"unchecked"``
    verdict as answered, so it must never trip the ``unjudged_sentences`` gate.
    """
    if "evidence_verifier_statement_check_failed" not in run.error_types():
        return "the run recorded no Statement Check failure"
    for error in run.state.errors:
        if error.error_type == "report_writer_provider_error":
            return (
                "a false 'every part failed' error was recorded, though "
                "every part drafted and printed its sentences fine"
            )
        if not error.recoverable:
            return f"a non-recoverable error was recorded: {error.error_type}"
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    points = [
        *composition.summary,
        *(point for section in composition.sections for point in section.points),
    ]
    if not points:
        return "no drafted sentence survived the failed check"
    if composition.rejected_points:
        return "a sentence was refused by a check that never ran"
    printed = {
        point.statement.statement_id
        for point in points
        if point.statement is not None
    }
    if not printed:
        return "the published points carry no statement id"
    verdicts = composition.statement_verdicts
    if not verdicts:
        return "the composition recorded no statement verdict at all"
    if set(verdicts) != printed:
        return (
            f"the verdict map's keys {sorted(verdicts)} do not match the "
            f"printed statement ids {sorted(printed)}"
        )
    not_unchecked = sorted(
        {verdict for verdict in verdicts.values() if verdict != "unchecked"}
    )
    if not_unchecked:
        return (
            "a sentence was recorded as judged by a check that never ran: "
            f"{not_unchecked}"
        )
    drafted = run.replay.completer.drafted_sources
    for point in points:
        if point.statement is None:
            continue
        if " ".join(point.text.split()) not in drafted:
            return (
                f"the printed text of {point.statement.statement_id!r} does "
                "not match what the writer drafted, so a check that never "
                "ran somehow corrected it"
            )
    quality = run.state.quality
    if quality is not None and "unjudged_sentences" in quality.hard_failures:
        return "a recorded batch failure tripped the unjudged_sentences gate"
    return None


def _invariant_no_ranked_constraints_for_a_factual_answer(
    run: ReplayRun,
) -> str | None:
    """A measurement question is answered by its reading, not by a ranking.

    A ranking is a structure no pass of this pipeline fills, which is exactly
    why a case has to show it did not: an answer whose kind is not
    ``constraints`` is the answer the question asked for. The kind is read from
    the composition rather than required to be one particular word, because
    "factual", "historical" and "comparison" are three shapes of the same
    refusal to rank.
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    if composition.answer_kind == "constraints":
        return "the run answered a measurement question as a constraint ranking"
    # No answer-row clause: the merged writer publishes no answer table, so
    # "the answer has a row" is not a fact this run can carry.
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
    """The pages state an outcome, so no reader sentence may state a cause.

    Two facts, both readable from the artifacts. The pages the report rests on
    state what happened, never why: their own claims carry no causal marker,
    which is the fixture's whole premise. And the cause the scripted writer was
    asked to publish is refused by the Statement Check -- D8's only wording
    judge -- so nothing the reader sees asserts one.

    PD-7 is why the obligation itself is not the assertion here: a target with
    no unit dimension is answered by a verified finding that names it, and no
    field distinguishes "what happened" from "why", so this harness cannot
    build a causal obligation code would treat as different from a
    measurement. The reader-visible refusal is what the row can pin; Task 4.11
    owns what the row should say if a mechanism obligation gets a field of its
    own.
    """
    causal = ("because", "due to", "led to", "drove", "caused", "as a result")
    cited = _cited_findings(run)
    if not cited:
        return "the run published no sentence resting on a verified finding"
    for finding in cited:
        words = (finding.snippet or finding.content).casefold()
        if any(marker in words for marker in causal):
            return (
                f"a cited page states a cause ({finding.source_url}), so the "
                "case cannot show that none was available"
            )
    composition = run.state.composition
    if run.scenario.invented_prose and (
        composition is None or not composition.rejected_points
    ):
        return "the drafted cause was not refused by the Statement Check"
    report = run.report.casefold()
    for marker in causal:
        if marker in report:
            return f"the reader's report states a cause ({marker!r})"
    return None


def _invariant_no_table_printed(run: ReplayRun) -> str | None:
    """No question-shaped table when nothing qualifies (spec §4.1 rule 3).

    A run whose findings state no figure and mark no option builds neither an
    options table nor a findings table; the choice rule is structural (§4.1),
    so this reads the composition's own ``table`` field rather than pattern
    matching the report for the old placeholder sentence the table used to
    print in its place ("No figure passed the Evidence Verifier.", cut by
    spec §3.1 rule 4).
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    if composition.table is not None:
        return f"a {composition.table.shape} table printed when none should qualify"
    return None


def _owning_coverage_ids(run: ReplayRun) -> set[str]:
    """The plan part(s) a scenario's review defect names, by coverage id."""
    defect = run.scenario.review_defect
    if defect is None:
        return set()
    target_ids = set(defect.target_ids)
    return {
        topic.coverage_id
        for topic in run.state.sub_topics
        for target in topic.evidence_targets
        if target.target_id in target_ids
    }


def _invariant_scoped_review_used(run: ReplayRun) -> str | None:
    """The redraft's second review is scoped, never a second full review.

    T5 addendum: once a redrafted composition carries a part byte-identical
    to what the first review judged, the graph must ask a *scoped* re-review
    (``ScopedReportReviewDraft``) rather than falling back to a second full
    one. This checks the whole chain of facts that makes that true, not just
    the schema name: exactly the defect's own part(s) were redrafted
    (``status == "written"``), every other part carried over unchanged, the
    section-draft call count matches (one per part, plus one per redrafted
    part), and the final review still carries the first review's defect,
    marked resolved.
    """
    calls = run.replay.completer.calls
    full_reviews = calls.count("report_reviewer:ReportReviewDraft")
    scoped_reviews = calls.count("report_reviewer:ScopedReportReviewDraft")
    if full_reviews != 1:
        return f"expected exactly one full review, saw {full_reviews}"
    if scoped_reviews != 1:
        return f"expected exactly one scoped re-review, saw {scoped_reviews}"
    if run.state.writer_redrafts != 1:
        return f"expected exactly one writer redraft, saw {run.state.writer_redrafts}"
    owning = _owning_coverage_ids(run)
    if not owning:
        return "the scenario names no review defect to redraft"
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    parts_by_id = {part.coverage_id: part for part in composition.parts}
    for coverage_id, part in parts_by_id.items():
        expected_status = "written" if coverage_id in owning else "carried_over"
        if part.status != expected_status:
            return (
                f"part {coverage_id!r} has status {part.status!r}, expected "
                f"{expected_status!r}"
            )
    section_draft_calls = calls.count("report_writer:SectionDraft")
    expected_calls = len(parts_by_id) + len(owning)
    if section_draft_calls != expected_calls:
        return (
            f"expected {expected_calls} SectionDraft calls (one per part, "
            f"plus one per redrafted part), saw {section_draft_calls}"
        )
    review = run.state.report_review
    if review is None:
        return "the run recorded no final review"
    if not any(defect.resolution == "resolved" for defect in review.defects):
        return "the final review carries no defect marked resolved"
    return None


def _invariant_count_period_binds_obligation(run: ReplayRun) -> str | None:
    """A count row binds an obligation only by the period it actually states.

    ``count-unit-period``'s premise: the page states counts for two different
    years, and only the row stating 2025 may bind the 2025 obligation
    (``topic-01-target-01``) -- a row for any other period naming that target
    id is exactly the merged-obligation defect this case rejects.
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    bound = [
        row for row in composition.fact_rows if "topic-01-target-01" in row.target_ids
    ]
    if not bound:
        return "no fact row binds topic-01-target-01 at all"
    wrong_period = [row for row in bound if row.period != "2025"]
    if wrong_period:
        return (
            f"row(s) {[row.row_id for row in wrong_period]} bind "
            "topic-01-target-01 with a period other than 2025: "
            f"{[row.period for row in wrong_period]}"
        )
    return None



def _invariant_scoped_review_fallback_used(run: ReplayRun) -> str | None:
    """An invalid scoped reply falls back to exactly one full review (T5 addendum).

    The scoped attempt is made -- that is what makes the fallback observable
    -- but its reply could not be used, so the run's *final* judgement is a
    fresh full review, never a scoped-derived one: the redraft's carried
    dispositions and previous-defect resolutions play no part in the record
    the run actually publishes.
    """
    calls = run.replay.completer.calls
    full_reviews = calls.count("report_reviewer:ReportReviewDraft")
    scoped_reviews = calls.count("report_reviewer:ScopedReportReviewDraft")
    if scoped_reviews != 1:
        return f"expected exactly one scoped attempt, saw {scoped_reviews}"
    if full_reviews != 2:
        return (
            f"expected exactly two full reviews (first, then the fallback), "
            f"saw {full_reviews}"
        )
    review = run.state.report_review
    if review is None:
        return "the run recorded no final review"
    if "scoped" not in review.rationale.casefold():
        return "the final review's rationale does not record the scoped fallback"
    return None

def _invariant_extra_pass_redrafts_the_gaining_part(run: ReplayRun) -> str | None:
    """A part that gains a new finding on the extra pass is written fresh,
    never silently carried over from the opening pass's composition.

    P0-1: the extra-pass writer must not run in redraft mode off the
    previous pass's own review -- topic-02 already has a section on pass 0
    (its first target answered), and topic-02-target-02 is answered only by
    the extra pass the missing target buys (D4, §6.5). A composition that
    still marks topic-02 ``carried_over``, or whose topic-02 section does
    not cite the new finding, is exactly the bug this case exists to catch.
    """
    composition = run.state.composition
    if composition is None:
        return "the run composed no report"
    part = next((p for p in composition.parts if p.coverage_id == "topic-02"), None)
    if part is None:
        return "the composition has no topic-02 part"
    if part.status != "written":
        return f"topic-02's status is {part.status!r}, expected 'written'"
    section = next(
        (s for s in composition.sections if s.coverage_id == "topic-02"), None
    )
    if section is None:
        return "topic-02 printed no section"
    cites_new_target = any(
        "topic-02-target-02" in point.statement.target_ids
        for point in section.points
        if point.statement is not None
    )
    if not cites_new_target:
        return (
            "topic-02's section does not cite the new finding for "
            "topic-02-target-02"
        )
    return None




_REPLAY_INVARIANTS: dict[str, Any] = {
    "relay_labelled_as_relay": _invariant_relay_labelled_as_relay,
    "maker_row_is_own": _invariant_maker_row_is_own,
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
    "subjects_stay_apart": _invariant_subjects_stay_apart,
    "versions_stay_apart": _invariant_versions_stay_apart,
    "one_fact_row": _invariant_one_fact_row,
    "period_resolved_from_page_date": _invariant_period_resolved_from_page_date,
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
    "no_table_printed": _invariant_no_table_printed,
    "scoped_review_used": _invariant_scoped_review_used,
    "count_period_binds_obligation": _invariant_count_period_binds_obligation,
    "scoped_review_fallback_used": _invariant_scoped_review_fallback_used,
    "extra_pass_redrafts_the_gaining_part": (
        _invariant_extra_pass_redrafts_the_gaining_part
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
                max_extra_passes=scenario.max_extra_passes,
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

