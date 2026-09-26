"""The Report Writer (spec §6): the parallel writer.

One call per plan sub-topic ("part"), each request listing only that part's
own verified findings (spec §6.1-6.3); each part's Statement Check starts the
moment its own draft returns, sharing one semaphore
(``agents.verifier_concurrency``) with every other part's batches and the
bottom line's (spec §6.5, decision D8); the bottom line is written last, fed
only the checked section statements (spec §6.6); a redraft re-asks only the
parts a material defect names, and carries every other part over unchanged
(spec §6.9). Code keeps only the mechanical rules spec §6.4 names -- a point
cites at least one known label belonging to its part, is not built only from
context-only findings, names its subject, and fits the length and count
shape. Every other question about a sentence's wording -- its numbers,
dates, scope, organisation, forecast or actual -- is judged once for every
drafted sentence by the Statement Check (spec §5.4, decision D8).

Left for "T4 compose" (after T2's ``report_table.build_table`` and T3's
renderer land): filling ``composition.table``, ``page_credits`` and
``unreachable``, and wiring ``ReportWriterAgent`` to T3's rewritten
``render_written_report``/``render_finding_log``. Every seam that step needs
is already in place: ``ReportComposition.parts``/``summary``/``sections`` are
built here in their final, renumbered form, ``answer_kind`` is wired from the
frozen contract, and ``ReportWriterTask.previous`` carries the prior
composition a redraft needs.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import Field, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.planner import Clock, answer_form_requirement, utc_now
from deep_research.agents.prompts import (
    AgentTask,
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.report import (
    _carried_rows,
    _figure_label_for,
    render_finding_log,
    render_written_report,
    report_as_of,
    report_scope,
    written_citations,
)
from deep_research.agents.report_table import build_table as _build_table
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.verified_facts import (
    answered_target_ids,
    answers_by_fallback,
    citable_findings,
    claimed_organisation,
    fact_rows,
    finding_answers,
    not_found_targets,
)
from deep_research.agents.wording import stated_role
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderConfigurationError,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    AcquisitionState,
    AnswerKind,
    BottomLineDraft,
    ContractModel,
    EvidenceTarget,
    FactRow,
    Finding,
    ItemMark,
    ItemMarkDraft,
    NotFoundTarget,
    PageCredit,
    ReadRecord,
    RejectedDraftPoint,
    ReportComposition,
    ReportPart,
    ReportPoint,
    ReportReview,
    ReportSection,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ReviewDefect,
    ScoredSource,
    SectionDraft,
    SubTopic,
    UnreachablePage,
    WriterPointDraft,
)

REPORT_WRITER_NAME = "report_writer"

# Controller ruling 2026-09-25 ("no strong limits"; quality and latency come
# first over every artificial content cap): MAX_POINT_CHARS 600 -> 1200,
# _DEFECT_PROBLEM_CHARS 400 -> 2000, the old DEFAULT_MAX_SECTIONS = 4 cap is
# deleted outright (every plan part gets its section, one call per part, no
# part dropped for count), and no new cap is added on points per section or
# findings per part. _SECTION_TITLE_CHARS keeps its pre-existing value (120)
# rather than the spec's 80: it is a structural backstop against a
# pathological reply, not a quality-shaping cap, and the ruling asked it to
# stay. MAX_BOTTOM_LINE_SENTENCES (4) stays: it is the top of the bottom
# line's own 2-4 sentence shape (WRI-4), not a truncation of content.
MAX_POINT_CHARS = 1200
MAX_BOTTOM_LINE_SENTENCES = 4
_SECTION_TITLE_CHARS = 120
_MARK_SPAN_CHARS = 80
CONTEXT_ONLY_RELEVANCE = 0.5
# Spec §17 Q6 chose 7 (= max_sub_topics) so every part starts at once; a
# controller override for this build raised it to 10 (at least the part
# count; the target provider allows far higher concurrency) -- see
# ``utils/config.py``'s ``writer_section_concurrency`` for the shipped value.
# This is only the fallback a caller with no configured value gets.
DEFAULT_WRITER_SECTION_CONCURRENCY = 10
# One defect's own sentence as the re-draft's request carries it: the whole
# sentence, not a clipped one -- the redraft has to see what is wrong in full
# (controller ruling: no strong limits).
_DEFECT_PROBLEM_CHARS = 2000
# F10: the first attempt runs at the resolved profile's effort (config.yaml
# model_overrides.report_writer, the one effort source); a truncated draft is
# asked once more at high, the retry this writer's own call makes.
_WRITER_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, OUTPUT_LIMIT_RETRY_EFFORT)

# Fallbacks used when a caller passes no explicit bound, mirroring the
# evidence_verifier module constants of the same shape (PD-12).
_CHECK_BATCH_SIZE_DEFAULT = 5
_CHECK_CONCURRENCY_DEFAULT = 8


# --- WRI-1'/WRI-2'/WRI-3': the section call's prompt contract ---------------

SECTION_SYSTEM_PROMPT = (
    "You write one section of a research report: the part of the question "
    "named in this request, from the verified findings listed for that part "
    "only. Code builds the table, the list of what could not be confirmed "
    "and the sources, and another request writes the bottom line from the "
    "checked sections. Every finding you may cite is listed with a label "
    "(F01, F02, ...), its verbatim snippet, and each of its figures with the "
    "figure's verified period, kind (actual or forecast), organisation and "
    "reader label. A finding also shows the researcher's own content, used "
    "only to identify what the snippet is about, and a finding with no "
    "figure shows the page passage around its snippet: take a judgement's "
    "subject from them, never from the snippet alone. A snippet is verbatim "
    "and may end mid-clause. A finding with no figure is listed with its "
    "snippet and the body the line attributes it to, and a host is where a "
    "statement was read, never the body that made it: when a page "
    "reproduces a document, state what that document provides, and keep the "
    "host to where it was read. Every sentence you write is judged against "
    "the findings it cites and may be corrected or refused."
)

SECTION_INSTRUCTION = (
    "Rules:\n"
    "- Cite by label only: every point lists in finding_labels the labels it rests on. "
    "Never write a URL.\n"
    "- State a forecast with a forecast verb (\"projects\", \"expects\", \"forecasts\"), never "
    "as a completed outcome.\n"
    "- Use only the numbers and dates of the cited findings, and keep the finding's own "
    "qualifier with the number it qualifies (\"nearly\", \"more than\", \"about\").\n"
    "- Use the scope words the finding states, never the question's.\n"
    "- Report only the part of a snippet that is complete. A snippet is verbatim and may "
    "end mid-clause, so never supply an object, a condition or an ending it does not "
    "carry, and never state a cut rule as if it were whole.\n"
    "- Name only organisations and publications the cited findings name.\n"
    "- A figure belongs to the subject its finding names (a product, a place, a version): "
    "never move a figure from one subject to another, and name the subject as the finding "
    "names it.\n"
    "- Credit what you write the way the finding's label or line decides, in your own "
    "words: a figure the label credits to the finding's organisation is that "
    "organisation's; a relayed figure or statement is \"according to <organisation>, as "
    "reported by <site>\"; a figure the label does not attribute is stated as what the "
    "page says, named as the page names its own publisher, or by its host when the page "
    "names no publisher. A host is where a statement was read, never the body that made "
    "it. Never print a label's own words (\"own figure\", \"not stated\", \"does not "
    "attribute it\") in a point.\n"
    "- State every answer the listed findings carry for this part's targets, required "
    "targets first in the listed order, each in the form its evidence takes: a figure "
    "with its period and its organisation; a forecast with its issuer and release; items "
    "with their attributes, grouped or ordered on a basis the question or the findings "
    "give, and stated as the findings state them; reasons, mechanisms or provisions as the "
    "cited findings state them. Every required target a listed finding answers is stated "
    "by a point citing that finding; a point never announces an absence of its own -- an "
    "unanswered target is code's to disclose, not yours.\n"
    f"- Keep every point under {MAX_POINT_CHARS} characters. A longer point is split "
    "at a sentence boundary and every piece kept with the same citations, so a "
    "sentence that long on its own is refused: write one fact per point.\n"
    "- A section title names the part of the question this section answers, in the "
    "question's own words where it has them: at most eight words, never a judgement or "
    "a status.\n"
    "- Never print a page's housekeeping as a point: a copyright, revision or legal "
    "line, or a disclaimer, belongs to the evidence log and answers no question. An "
    "effective date is not housekeeping when the question asks when something "
    "applies: state it as the finding states it.\n"
    "- Never state a judgement while dropping the criterion it is measured by: a "
    "judgement the finding measures by a criterion the snippet does not name is "
    "not an answer, so state the criterion with it or leave the judgement out. The "
    "same rule covers a pick made on value or for the price and the newer or "
    "alternative item the page names beside that pick: state both with the pick or "
    "leave the judgement out.\n"
    "- Every judgement, pick or verdict names the item it is about, exactly as the "
    "finding's content or passage names it: never a bare pronoun (\"it\", \"this\", "
    "\"these\") or an unnamed reference (\"the model\", \"the buds\"). Such a point is "
    "refused.\n"
    "- A point never rests only on findings listed under \"# Context only\": they are "
    "background, not citable evidence.\n"
    "- Mark each option (a product, place, service or other thing the question asks to "
    "choose among or compare) your point is about, in items: name exactly as your "
    "point's text writes it, and the same way every time it recurs; verdict the "
    "shortest span of your point's text giving that source's verdict, score or price "
    "for it; picked true only when the finding reports that its source recommends, "
    "picks or ranks the option first; by the finding label the mark rests on, required "
    "when the point cites more than one site. Leave items empty for a point about no "
    "option.\n"
    "- On a redraft, return your previous section with only the edits the listed "
    "defects need; keep every other point word for word.\n"
    "- Never use a verdict or corroboration word (verified, confirmed, corroborated, "
    "independently, insufficient evidence, contested) as the report's own assessment: "
    "inside a finding's own attributed statement, use the words the finding uses.\n"
    "- Never state the same figure twice."
)

_SECTION_REPLY_EXAMPLES = (
    (
        "Example input: ## F01: Example Tester's review (example-tester.test) | "
        "content: Example Tester's own lab measured Model A at a noise rating of "
        "4.5 out of 5. | snippet: Example Tester gives Model A a noise rating of 4.5 "
        "out of 5. | F01 | figure 1: 4.5 out of 5 | period 2026 | kind actual | "
        "organisation Example Tester | label: Example Tester's own figure; actual",
        '{"title":"Noise ratings","points":[{"text":"Example Tester gives Model A a '
        'noise rating of 4.5 out of 5.","finding_labels":["F01"],"items":[{"name":'
        '"Model A","verdict":"a noise rating of 4.5 out of 5","picked":false,"by":'
        '"F01"}]}]}',
    ),
    (
        "Example input: ## F02: Example Register entry (example-register.test) | "
        "content: Model B, a compact model, is the one to beat for the price. | "
        "snippet: it is the one to beat for the price. | passage: Model B, a compact "
        "model, is the one to beat for the price. | F02 | statement | read at "
        "example-register.test | actual",
        '{"title":"Value for money","points":[{"text":"Model B is the one to beat for '
        'the price.","finding_labels":["F02"],"items":[{"name":"Model B","verdict":'
        '"the one to beat for the price","picked":true,"by":"F02"}]}]}',
    ),
)


# --- WRI-4/WRI-5/WRI-6: the bottom-line call's prompt contract --------------

BOTTOM_LINE_SYSTEM_PROMPT = (
    "Write the bottom line: two to four sentences answering the question directly, "
    "from the checked statements listed; each was checked against the findings it "
    "cites; state nothing they do not."
)

BOTTOM_LINE_INSTRUCTION = (
    "Rules:\n"
    "- The most direct answer first, then the question's parts in order.\n"
    "- Cite by label only: every sentence lists in finding_labels the labels it "
    "rests on, and every label must be one the listed statements cite -- never a "
    "label a listed statement does not carry.\n"
    "- Credit every judgement, pick or ranking to its source exactly as the "
    "statement credits it.\n"
    "- For a question asking which option is best, say which option each source "
    "picks and by what criterion, giving each source's pick where the sources "
    "differ, and never a pick, ranking or criterion of your own.\n"
    "- A forecast keeps its issuer, its release and a forecast verb.\n"
    "- Keep the qualifiers and the criteria the statements state.\n"
    "- Never state a source's date (the report prints it), never announce an "
    "absence, never list every option (the table does), and never copy a "
    "statement word for word.\n"
    "- Every judgement, pick or verdict names the item it is about, exactly as the "
    "statement names it: never a bare pronoun or an unnamed reference. Such a "
    "sentence is refused.\n"
    "- Mark each option your sentence is about, in items, the same way a section "
    "does: name, verdict, picked, and by when the sentence cites more than one "
    "site.\n"
    f"- Keep every sentence under {MAX_POINT_CHARS} characters.\n"
    "- On a redraft, minimal edits: return your previous bottom line with only the "
    "edits the listed defects need."
)

_BOTTOM_LINE_REPLY_EXAMPLES = (
    (
        "Example input: # Checked statements ## Noise ratings - Example Tester gives "
        "Model A a noise rating of 4.5 out of 5. (cites F01)",
        '{"sentences":[{"text":"Example Tester gives Model A a noise rating of 4.5 '
        'out of 5.","finding_labels":["F01"],"items":[{"name":"Model A","verdict":'
        '"a noise rating of 4.5 out of 5","picked":false,"by":"F01"}]}]}',
    ),
)


class ReportWriterTask(AgentTask):
    session_id: str
    iteration: int = 0
    max_extra_passes: int = 0
    question: str
    as_of: str = ""
    scope: str = ""
    generated_on: str = ""
    answer_kind: AnswerKind | None = None
    """The frozen contract's answer form (spec §3.2), printed as ``Answer form:
    {answer_form_requirement(kind)}`` to both writer calls; ``None`` prints
    "not classified" (a legacy or contract-less run)."""
    sub_topics: list[SubTopic] = Field(default_factory=list)
    targets: list[EvidenceTarget] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)       # the whole verified snapshot
    sources: list[ScoredSource] = Field(default_factory=list)
    registry: list[tuple[str, Finding]] = Field(default_factory=list)
    facts: list[FactRow] = Field(default_factory=list)
    not_found: list[NotFoundTarget] = Field(default_factory=list)
    answered: dict[str, list[str]] = Field(default_factory=dict)
    reads: dict[str, ReadRecord] = Field(default_factory=dict)
    """Read id -> the page it read, so the Statement Check can see each cited
    finding's bounded passage (improvement 8). Empty for a caller with no reads
    in hand, which shows the snippet alone as before."""
    passages: dict[str, str] = Field(default_factory=dict)
    """Finding id -> its bounded registry passage (spec §6.2, D5), computed once
    at task-build time for the whole registry -- not only for cited findings."""
    defects: list[ReviewDefect] = Field(default_factory=list)
    """The material defects this draft must answer, in the review's own order.

    Empty for a first draft. Non-empty means the graph bought this writer
    re-run *for* these defects: the run already composed a report, the terminal
    review scored it and named what is materially wrong, and no research pass
    can fix it from the same evidence -- so each defect is routed to the parts
    (or the bottom line) its statement or target ids name (spec §6.9).
    """
    previous: ReportComposition | None = None
    """The prior pass's composition, read only on a redraft: a part with no
    routed defect is carried over from here unchanged (spec §6.9)."""
    acquisition_state_by_target: dict[str, AcquisitionState] = Field(default_factory=dict)
    """Keyed by ``coverage_id`` (the field name is the type's own historical
    name; every caller in this codebase keys it by sub-topic). Spec §6.7's
    ``unreachable`` is built from each required sub-topic's own
    ``denied_urls`` and ``candidate_records`` here."""


class WrittenReport(ContractModel):
    markdown: str
    evidence_markdown: str
    composition: ReportComposition
    statement_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    refused_count: int = Field(ge=0)


def finding_registry(findings: Sequence[Finding], targets: Sequence[EvidenceTarget],
                     sub_topics: Sequence[SubTopic] = ()) -> list[tuple[str, Finding]]:
    """One label per citable finding: answers to required targets first, then the rest.

    The answers are resolved against every target, so an optional sibling
    still keeps a figure about its subject off a required target's answers
    (F11), and only the required targets' answers are then ranked first. The
    plan resolves an unbound extraction's own sub-topic (improvement 1A), so a
    finding that answers a required obligation that way ranks with the rest.
    """
    citable = citable_findings(findings)
    required = {t.target_id for t in targets if t.required}
    answered = {t: ids for t, ids in answered_target_ids(
        citable, targets, sub_topics=sub_topics).items() if t in required}
    first = list(dict.fromkeys(fid for ids in answered.values() for fid in ids))
    rank = {fid: n for n, fid in enumerate(first)}
    ordered = sorted(citable, key=lambda f: rank.get(finding_fingerprint(f), len(rank)))
    return [(f"F{n:02d}", finding) for n, finding in enumerate(ordered, start=1)]


def statement_passages(findings: Sequence[Finding],
                       reads: Mapping[str, ReadRecord]) -> dict[str, str]:
    """Finding id -> the bounded passage of the page it was read from (improvement 8).

    A snippet is cut at its passage's boundary, so the condition, exception or
    object a reported rule attaches to can sit just past the cut and a sentence
    that drops it looks supported. ``context_passage`` is the same bounded
    window the Context Check judged the figure in; a finding whose read this
    task does not carry contributes nothing, and the block shows its snippet
    alone.
    """
    # Imported at call time for the same reason the checker is: the unit tests
    # substitute the checker's own names, and nothing here should bind before
    # that substitution can be seen.
    from deep_research.agents.evidence_verifier import context_passage

    passages: dict[str, str] = {}
    for finding in findings:
        read = reads.get(finding.read_id)
        if read is None:
            continue
        passages[finding_fingerprint(finding)] = context_passage(
            read, finding.locator, finding.snippet
        )
    return passages


def registry_lines(label: str, finding: Finding,
                   passages: Mapping[str, str] | None = None) -> list[str]:
    """One finding's registry block: header, content, snippet, then its figure
    or statement lines (spec §6.2, D5).

    ``content`` is the researcher's own wording, which names the referent a
    snippet leaves as a pronoun. A finding with no kept figure also shows the
    bounded ``passage`` around its snippet (the same window the Statement
    Check reads), so the writer can name only what the checker can verify.
    """
    passages = passages or {}
    lines = [
        f"## {label}: {finding.source_title} ({publisher_identity(finding.source_url)})",
        f"content: {finding.content}",
        f"snippet: {finding.snippet or finding.content}",
    ]
    number = 0
    for result in finding.verification.figure_results if finding.verification else []:
        if not result.kept or result.context is None:
            continue
        number += 1
        context = result.context
        subject = f" | subject {context.subject}" if context.subject else ""
        # Improvement 7: an unattributed figure of a relay-shaped page has no
        # organisation to claim, and naming the page's owner here is what the
        # writer turned into "<the site> states …"; the label beside it already
        # says "source does not attribute it".
        organisation = claimed_organisation(context, finding)
        lines.append(
            f"{label} | figure {number}: {result.figure.value} {result.figure.unit}{subject} | period "
            f"{context.period or 'not stated'} | kind {context.kind}"
            + (f" | organisation {organisation}" if organisation else "")
            + f" | label: {_figure_label_for(finding, context)}"
        )
    if number == 0:
        passage = passages.get(finding_fingerprint(finding), "")
        if passage:
            lines.append(f"passage: {passage}")
        # D1 (the prompt re-review): the writer's two phrases -- "the body the
        # line attributes it to" (WRI-1) and "a host is where a statement was
        # read" (WRI-2) -- map to two different words here, the same two the
        # Statement Check's own block prints for the same fact. An admitted
        # issuer is ``attributed to``; a page that names nobody is ``read at``,
        # so the writer never has to guess a body from the shape of a domain.
        admitted = finding.attributed_issuer
        attribution = (
            f"attributed to {admitted}" if admitted
            else f"read at {publisher_identity(finding.source_url)}"
        )
        date = finding.statement_date or finding.release_date or finding.data_period
        lines.append(
            f"{label} | statement | {attribution} | "
            f"{stated_role(finding.snippet or finding.content)}"
            + (f" | dated {date}" if date else "")
        )
    return lines


# --- §6.1: the partition -----------------------------------------------------


@dataclass(frozen=True)
class PartPlacement:
    """One plan sub-topic's placed findings, before any writer call runs.

    Every ``sub_topics`` entry gets one of these, in plan order, even when its
    ``findings`` list is empty: an empty part makes no call (spec §6.1).
    """

    coverage_id: str
    sub_topic_title: str
    findings: list[Finding]


def _target_answers(finding: Finding, target: EvidenceTarget,
                    targets: Sequence[EvidenceTarget], sub_topics: Sequence[SubTopic]) -> bool:
    return finding_answers(finding, target, plan_targets=targets, sub_topics=sub_topics)


def _finding_coverage_id(finding: Finding, targets: Sequence[EvidenceTarget],
                         sub_topics: Sequence[SubTopic]) -> str | None:
    """The coverage id spec §6.1 places one citable finding under, or ``None``.

    Six categories, each swept over every target in plan order before the
    next is tried: the first target any category matches decides the part.
    """
    def explicit(target: EvidenceTarget) -> bool:
        return target.target_id in finding.target_ids

    for target in targets:
        if target.required and explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if target.required and not explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if not target.required and explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if not target.required and not explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if explicit(target) and not _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    name = cosmetic_text(finding.related_sub_topic)
    for sub_topic in sub_topics:
        if cosmetic_text(sub_topic.title) == name:
            return sub_topic.coverage_id
    return None


def report_parts(
    findings: Sequence[Finding], targets: Sequence[EvidenceTarget],
    sub_topics: Sequence[SubTopic],
) -> tuple[list[PartPlacement], list[Finding]]:
    """Partition every citable finding into exactly one plan sub-topic (spec §6.1).

    Returns one ``PartPlacement`` per sub-topic, in plan order (empty ones
    included, since an empty part still needs to be recorded as such), and
    the findings placed nowhere -- recorded in the evidence log, never
    written ("T4 compose" wires that recording).
    """
    citable = citable_findings(findings)
    buckets: dict[str, list[Finding]] = {topic.coverage_id: [] for topic in sub_topics}
    unplaced: list[Finding] = []
    for finding in citable:
        coverage_id = _finding_coverage_id(finding, targets, sub_topics)
        if coverage_id is None or coverage_id not in buckets:
            unplaced.append(finding)
            continue
        buckets[coverage_id].append(finding)
    parts = [
        PartPlacement(coverage_id=topic.coverage_id, sub_topic_title=topic.title,
                     findings=buckets[topic.coverage_id])
        for topic in sub_topics
    ]
    return parts, unplaced


def sources_by_url(sources: Sequence[ScoredSource]) -> dict[str, ScoredSource]:
    """Every scored source, keyed by its normalized URL."""
    return {normalize_source_url(source.url): source for source in sources}


def is_context_only(finding: Finding, sources: Mapping[str, ScoredSource]) -> bool:
    """D16 (spec §6.2): unbound, and from a low-relevance or low-confidence source.

    Context-only findings are listed under a part's ``# Context only``
    heading rather than its verified-findings registry, and a point resting
    only on them is refused (mechanical rule 3). A bound finding is never
    context-only, whatever its source's score.
    """
    if finding.target_ids:
        return False
    source = sources.get(normalize_source_url(finding.source_url))
    if source is None:
        return False
    if source.low_confidence:
        return True
    return source.relevance_score is not None and source.relevance_score < CONTEXT_ONLY_RELEVANCE


# --- §6.3/§6.6: the section and bottom-line requests ------------------------


@dataclass(frozen=True)
class PartJob:
    """One part's request context, resolved once before its call runs."""

    coverage_id: str
    sub_topic_title: str
    order: int
    targets: list[EvidenceTarget]
    findings: list[Finding]
    """This part's placed, citable, non-context-only findings."""
    context_findings: list[Finding]
    """This part's placed findings that are context-only (spec §6.2)."""
    previous: ReportSection | None
    """The prior pass's section for this part, if one exists."""
    defects: list[ReviewDefect]
    """Defects routed to this part; non-empty only when ``redraft`` re-asks it."""
    redraft: bool
    """Whether this part's call runs at all: ``False`` means carried over
    unchanged from ``previous`` (spec §6.9), no call made."""


def _answer_form_line(task: ReportWriterTask) -> str:
    return answer_form_requirement(task.answer_kind) if task.answer_kind else "not classified"


def _target_line(
    target: EvidenceTarget, *, answered: Mapping[str, list[str]],
    label_by_id: Mapping[str, str], findings_by_id: Mapping[str, Finding],
    sub_topics: Sequence[SubTopic],
) -> str:
    kind = "required" if target.required else "optional"
    finding_ids = answered.get(target.target_id, [])
    labels = [label_by_id[fid] for fid in finding_ids if fid in label_by_id]
    if not labels:
        return f"- {target.target_id}: {target.question} ({kind}; no listed finding answers it)"
    # §6.13: a fallback answer (no explicit binding) is labelled as such, so
    # the writer states it honestly without claiming an extraction-time bind.
    fallback_only = all(
        fid in findings_by_id and answers_by_fallback(findings_by_id[fid], target, sub_topics)
        for fid in finding_ids if fid in label_by_id
    )
    suffix = " (through its sub-topic only)" if fallback_only else ""
    return f"- {target.target_id}: {target.question} ({kind}; answered by {', '.join(labels)}{suffix})"


def _registry_block(findings: Sequence[Finding], registry: Sequence[tuple[str, Finding]],
                    passages: Mapping[str, str]) -> str:
    wanted = {finding_fingerprint(f) for f in findings}
    blocks = [
        "\n".join(registry_lines(label, finding, passages))
        for label, finding in registry
        if finding_fingerprint(finding) in wanted
    ]
    return "\n\n".join(blocks) if blocks else "(none)"


def _rendered_previous_section(section: ReportSection | None) -> str:
    if section is None:
        return "(none)"
    lines = [f"## {section.title}"]
    lines.extend(f"- {point.text}" for point in section.points)
    return "\n".join(lines)


def _rendered_previous_bottom_line(points: Sequence[ReportPoint]) -> str:
    return "\n".join(f"- {point.text}" for point in points) or "(none)"


def material_defects(review: ReportReview | None) -> list[ReviewDefect]:
    """The defects one stored review says must be closed before acceptance.

    Read from the review record, never re-derived: the reviewer's own severity
    decides what is material (``ReviewDefect.material``), and an unscored review
    has no defect list to act on. This is the same reader the routing decision
    uses, so the writer is asked about exactly the defects that bought its
    re-run.
    """
    if review is None or review.status != "scored":
        return []
    return list(review.material_defects)


def _defect_lines(defects: Sequence[ReviewDefect]) -> str:
    """One bounded line per defect: its id, kind, scope, and its own sentence.

    The sentence is the reviewer's own text; the packet spends characters on
    everything it carries, so it is still summarized to ``_DEFECT_PROBLEM_CHARS``
    -- generous enough (controller ruling: no strong limits) that a redraft
    sees the defect whole in every case observed so far.
    """
    lines: list[str] = []
    for defect in defects:
        scope = ", ".join([*defect.target_ids, *defect.statement_ids]) or "the whole report"
        lines.append(
            f"- {defect.defect_id} ({defect.kind}; {scope}): "
            + summarize_text(defect.problem, limit=_DEFECT_PROBLEM_CHARS)
        )
    return "\n".join(lines)


def section_messages(task: ReportWriterTask, job: PartJob) -> list[ChatMessage]:
    """One part's section request (spec §6.3): static-first, so every part
    shares a cacheable prefix."""
    label_by_id = {finding_fingerprint(f): label for label, f in task.registry}
    findings_by_id = {finding_fingerprint(f): f for f in task.findings}
    targets_block = "\n".join(
        _target_line(t, answered=task.answered, label_by_id=label_by_id,
                     findings_by_id=findings_by_id, sub_topics=task.sub_topics)
        for t in job.targets
    ) or "(none)"
    static = [
        f"# Rules\n{SECTION_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_SECTION_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
        f"# This part of the question\n{job.sub_topic_title}\n{targets_block}",
    ]
    if job.defects:
        material.append(f"# Your previous section\n{_rendered_previous_section(job.previous)}")
        material.append(f"# Defects to fix\n{_defect_lines(job.defects)}")
    material.append(
        f"# Verified findings for this part\n{_registry_block(job.findings, task.registry, task.passages)}"
    )
    context_block = (
        _registry_block(job.context_findings, task.registry, task.passages)
        if job.context_findings else "(none)"
    )
    material.append(f"# Context only\n{context_block}")
    return [ChatMessage(role="developer", content=SECTION_SYSTEM_PROMPT),
            ChatMessage(role="user", content=render_structured_request(static, material))]


def bottom_line_messages(
    task: ReportWriterTask, sections: Sequence[ReportSection], *,
    previous: Sequence[ReportPoint] = (), defects: Sequence[ReviewDefect] = (),
) -> list[ChatMessage]:
    """The bottom-line request (spec §6.6): fed only the checked, kept section
    statements a caller passes in ``sections`` -- filtering to consistent or
    corrected verdicts is the caller's job (§6.7 assembly needs the verdicts
    this function has no access to)."""
    label_by_id = {finding_fingerprint(f): label for label, f in task.registry}
    blocks: list[str] = []
    for section in sections:
        lines: list[str] = []
        for point in section.points:
            if point.statement is None:
                continue
            labels = [label_by_id[fid] for fid in point.statement.finding_ids if fid in label_by_id]
            options = "; ".join(
                f"{mark.name}{' [picked]' if mark.picked else ''}" for mark in point.statement.items
            )
            cites = f"cites {', '.join(labels)}" if labels else "cites nothing"
            suffix = f" ({cites}; options: {options})" if options else f" ({cites})"
            lines.append(f"- {point.text}{suffix}")
        if lines:
            blocks.append(f"## {section.title}\n" + "\n".join(lines))
    statements_block = "\n\n".join(blocks) if blocks else "(none)"
    static = [
        f"# Rules\n{BOTTOM_LINE_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_BOTTOM_LINE_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
        f"# Checked statements\n{statements_block}",
    ]
    if previous:
        material.append(f"# Your previous bottom line\n{_rendered_previous_bottom_line(previous)}")
    if defects:
        material.append(f"# Defects to fix\n{_defect_lines(list(defects))}")
    return [ChatMessage(role="developer", content=BOTTOM_LINE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=render_structured_request(static, material))]


# --- redraft routing (spec §6.9) --------------------------------------------


def _route_defects(
    defects: Sequence[ReviewDefect], previous: ReportComposition | None,
    targets: Sequence[EvidenceTarget],
) -> tuple[set[str], dict[str, list[ReviewDefect]], list[ReviewDefect]]:
    """Every defect, routed by its statement ids (which part or the bottom
    line held them) and its target ids (which part owns them); a defect with
    no ids of either kind goes to every part and the bottom line."""
    all_coverage_ids = {t.coverage_id for t in targets}
    if previous is not None:
        all_coverage_ids |= {s.coverage_id for s in previous.sections if s.coverage_id}
    statement_owner: dict[str, str] = {}
    statement_in_bottom_line: set[str] = set()
    if previous is not None:
        for section in previous.sections:
            if not section.coverage_id:
                continue
            for point in section.points:
                if point.statement is not None:
                    statement_owner[point.statement.statement_id] = section.coverage_id
        for point in previous.summary:
            if point.statement is not None:
                statement_in_bottom_line.add(point.statement.statement_id)

    routed: set[str] = set()
    by_coverage: dict[str, list[ReviewDefect]] = {}
    bottom_line: list[ReviewDefect] = []
    for defect in defects:
        if not defect.statement_ids and not defect.target_ids:
            routed |= all_coverage_ids
            for coverage_id in all_coverage_ids:
                by_coverage.setdefault(coverage_id, []).append(defect)
            bottom_line.append(defect)
            continue
        ids: set[str] = set()
        goes_to_bottom_line = False
        for statement_id in defect.statement_ids:
            if statement_id in statement_in_bottom_line:
                goes_to_bottom_line = True
            owner = statement_owner.get(statement_id)
            if owner:
                ids.add(owner)
        for target_id in defect.target_ids:
            for target in targets:
                if target.target_id == target_id:
                    ids.add(target.coverage_id)
        routed |= ids
        for coverage_id in ids:
            by_coverage.setdefault(coverage_id, []).append(defect)
        if goes_to_bottom_line:
            bottom_line.append(defect)
    return routed, by_coverage, bottom_line


# --- mechanical rules (§6.4); the Statement Check judges everything else ---

#: Where a drafted point too long for ``MAX_POINT_CHARS`` may be cut: after a
#: sentence or clause end, and only where what follows starts a word. The cut is
#: verbatim -- the pieces are the drafted text's own words -- so a split never
#: invents, drops or reorders a word and every piece keeps the point's citations.
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.;!?])\s+(?=[\"“(\[]?\S)")

#: D6's guard: a judgement whose subject is a bare pronoun, unquoted or inside
#: a quotation, and that carries no option mark to name the subject instead.
_UNNAMED_SUBJECT = re.compile(
    r"^[\"“'(]*\s*(it|this|that|these|those|they|he|she)\b\s+"
    r"(is|are|was|were|has|have|had|remains|offers|delivers|makes|sounds)\b",
    re.IGNORECASE,
)
_QUOTED_SPAN = re.compile(r"[\"“]([^\"”]+)[\"”]")


def _has_unnamed_subject(text: str) -> bool:
    if _UNNAMED_SUBJECT.match(text.strip()):
        return True
    return any(_UNNAMED_SUBJECT.match(match.group(1).strip()) for match in _QUOTED_SPAN.finditer(text))


def _cosmetic_contains(haystack: str, needle: str) -> bool:
    if not needle:
        return True
    return cosmetic_text(needle).casefold() in cosmetic_text(haystack).casefold()


def _lead_in(text: str, cuts: Sequence[re.Match[str]]) -> str:
    """The point's own introduction, verbatim: up to its colon, else its first clause.

    A piece cut after a ';' begins mid-sentence -- "… (c) keep track of …" with
    no subject and none of the conditions the sentence opened with (F3) -- so
    such a piece is printed with this introduction in front of it. The colon a
    list hangs from is the list's own introduction, so everything up to and
    including it is the lead; a clause-separated point with no colon is
    introduced by its first clause. Empty only when the point has neither.
    """
    colon = text.find(":")
    if colon != -1:
        return text[:colon + 1]
    return text[:cuts[0].start()] if cuts else ""


def _split_oversize_point(text: str, limit: int = MAX_POINT_CHARS) -> list[str] | None:
    """``text`` as consecutive pieces under ``limit``, or ``None`` when one clause is over it.

    A drafted point longer than the bound is split at a sentence boundary
    into pieces that keep its citations, rather than dropped. A piece that
    begins mid-sentence -- after a ';' inside a list -- is printed with the
    point's own introduction (F3), so no piece stands without its subject or
    its conditions. A point whose own clause is longer than the bound, or
    whose introduction plus one clause is, cannot be cut into pieces that
    stand alone: it returns ``None`` and the caller refuses it as before,
    rather than print a fragment.
    """
    cuts = list(_CLAUSE_BOUNDARY.finditer(text))
    clauses = _CLAUSE_BOUNDARY.split(text)
    if any(len(clause) > limit for clause in clauses):
        return None
    lead = _lead_in(text, cuts)
    marks = [text[cut.start() - 1] for cut in cuts]
    pieces: list[str] = []
    current = ""
    for index, clause in enumerate(clauses):
        mid_sentence = index > 0 and marks[index - 1] == ";"
        opening = f"{lead} {clause}".strip() if (mid_sentence and lead) else clause
        if not current:
            current = opening
        elif len(f"{current} {clause}") <= limit:
            current = f"{current} {clause}"
        else:
            pieces.append(current)
            current = opening
        if len(current) > limit:
            return None
    if current:
        pieces.append(current)
    return pieces or None


class _Verdict(Protocol):
    """Structural shape of ``evidence_verifier.StatementVerdictDraft`` (D8).

    Read by attribute only, never imported: the verdict is applied by code
    without re-judging the wording (spec §5.4), so this module depends on the
    shape of the checker's reply rather than on the checker's own type -- and
    the tests' fake verdicts satisfy it without importing anything.
    """

    verdict: str
    corrected_text: str
    reason: str


@dataclass(frozen=True)
class _Candidate:
    """One drafted point that cleared the mechanical rules and is waiting on
    the Statement Check's verdict."""

    key: str                     # "P01.0001", "B0001"; also the temp ReportStatement.statement_id
    where: str                   # "section[topic-01].points[1]" or "bottom_line[0]"
    text: str                    # drafted, whitespace-collapsed
    finding_labels: list[str]
    findings: list[Finding]
    items: list[ItemMarkDraft]


def _finding_label(finding: Finding) -> str:
    """Every kept figure's reader label for one finding, joined into one
    string -- the Statement Check gets one label string per cited finding,
    not one per figure (D8)."""
    labels: list[str] = []
    for result in finding.verification.figure_results if finding.verification else []:
        if result.kept and result.context is not None:
            label = _figure_label_for(finding, result.context)
            if label not in labels:
                labels.append(label)
    return "; ".join(labels)


def _consider_section_point(
    point: WriterPointDraft, where: str, *, part_labels: Mapping[str, Finding],
    all_labels: Mapping[str, Finding], context_only_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
    drafted = " ".join(point.text.split())
    wanted = [label.strip() for label in point.finding_labels]

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
        ))

    if not drafted:
        refuse("empty text")
        return []
    if not wanted:
        refuse("cites no checked finding")
        return []
    unknown = [label for label in wanted if label not in all_labels]
    if unknown:
        refuse("unknown labels: " + ", ".join(unknown))
        return []
    outside = [label for label in wanted if label not in part_labels]
    if outside:
        refuse("cites a finding outside this part")
        return []
    if all(label in context_only_labels for label in wanted):
        refuse("rests only on context-only findings")
        return []
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    pieces = [drafted] if len(drafted) <= MAX_POINT_CHARS else _split_oversize_point(drafted)
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):04d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=[part_labels[label] for label in wanted], items=list(point.items))
        for n, piece in enumerate(pieces, start=1)
    ]


def _consider_bottom_line_point(
    point: WriterPointDraft, where: str, *, cited_by_sections: Mapping[str, Finding],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
    drafted = " ".join(point.text.split())
    wanted = [label.strip() for label in point.finding_labels]

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
        ))

    if not drafted:
        refuse("empty text")
        return []
    if not wanted:
        refuse("cites no checked finding")
        return []
    unknown = [label for label in wanted if label not in cited_by_sections]
    if unknown:
        refuse("cites a finding no checked section statement cites")
        return []
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    pieces = [drafted] if len(drafted) <= MAX_POINT_CHARS else _split_oversize_point(drafted)
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):04d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=[cited_by_sections[label] for label in wanted], items=list(point.items))
        for n, piece in enumerate(pieces, start=1)
    ]


def _apply_marks(
    marks: Sequence[ItemMarkDraft], *, final_text: str, labels: Sequence[str],
    label_urls: Mapping[str, str], dropped: list[tuple[str, str]], statement_key: str,
) -> list[ItemMark]:
    """Rule 8: each name/verdict a verbatim span of the final text, at most
    ``_MARK_SPAN_CHARS``; ``by`` resolved to its finding's page. A failing
    mark is dropped -- the point stays -- and recorded (spec §5)."""
    kept: list[ItemMark] = []
    sites = {label_urls[label] for label in labels if label in label_urls}
    for mark in marks:
        name = mark.name.strip()
        verdict = mark.verdict.strip()
        if not name or len(name) > _MARK_SPAN_CHARS or not _cosmetic_contains(final_text, name):
            dropped.append((statement_key, f"'{mark.name}' is not in the sentence"))
            continue
        if verdict and (len(verdict) > _MARK_SPAN_CHARS or not _cosmetic_contains(final_text, verdict)):
            dropped.append((statement_key, f"'{mark.verdict}' is not in the sentence"))
            continue
        by = mark.by.strip()
        if len(sites) > 1:
            source_url = label_urls.get(by)
            if source_url is None:
                dropped.append((statement_key, f"'{mark.name}' names no known finding"))
                continue
        else:
            source_url = next(iter(sites), "")
        kept.append(ItemMark(name=name, verdict=verdict, picked=mark.picked, source_url=source_url))
    return kept


def _finalize_candidate(
    candidate: _Candidate, verdicts: Mapping[str, _Verdict | None], *,
    stated_rows: set[str], task_facts: Sequence[FactRow], task_targets: Sequence[EvidenceTarget],
    label_urls: Mapping[str, str], dropped_marks: list[tuple[str, str]],
    rejected: list[RejectedDraftPoint],
) -> tuple[ReportPoint | None, str]:
    """Apply the Statement Check's verdict, the restatement dedup, and the
    marks; return the printed point (or ``None``) and its verdict string."""

    def reject(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=candidate.where, text=candidate.text,
            finding_labels=list(candidate.finding_labels), reason=reason,
        ))

    verdict = verdicts.get(candidate.key)
    text = candidate.text
    if verdict is None:
        verdict_string = "unchecked"
    else:
        verdict_string = verdict.verdict
        if verdict.verdict == "corrected":
            correction = " ".join(verdict.corrected_text.split())
            if len(correction) > MAX_POINT_CHARS:
                reject(f"corrected text longer than {MAX_POINT_CHARS} characters")
                return None, verdict_string
            if not correction:
                reject(verdict.reason)
                return None, "inconsistent"
            text = correction
        elif verdict.verdict == "inconsistent":
            reject(verdict.reason)
            return None, verdict_string

    cited_ids = {finding_fingerprint(f) for f in candidate.findings}
    rows = {row.row_id for row in _carried_rows(text, cited_ids, task_facts, task_targets)}
    if rows and rows <= stated_rows:
        reject("restates " + ", ".join(sorted(rows)))
        return None, verdict_string
    stated_rows.update(rows)

    # §6.13: a statement's target ids are explicit bindings only -- a
    # fallback answer (no target id of its own) is stated but never claims a
    # target it never named.
    explicit_targets = sorted({
        target.target_id for target in task_targets
        for finding in candidate.findings
        if target.target_id in finding.target_ids and finding_fingerprint(finding) in cited_ids
    })
    marks = _apply_marks(candidate.items, final_text=text, labels=candidate.finding_labels,
                         label_urls=label_urls, dropped=dropped_marks, statement_key=candidate.key)
    own_first = sorted(candidate.findings, key=lambda f: 0 if any(
        r.context is not None and r.context.attribution == "own"
        for r in (f.verification.figure_results if f.verification else [])) else 1)
    statement = ReportStatement(
        statement_id=candidate.key, text=text,
        finding_ids=[finding_fingerprint(f) for f in candidate.findings],
        target_ids=explicit_targets, items=marks,
    )
    point = ReportPoint(text=text, source_urls=list(dict.fromkeys(f.source_url for f in own_first)),
                        statement=statement)
    return point, verdict_string


async def _check(
    provider: AgentCompleter, candidates: Sequence[_Candidate], *, question: str,
    gate: asyncio.Semaphore, batch_size: int, fingerprint: Callable[[str], object] | None,
    passages: Mapping[str, str],
) -> tuple[Mapping[str, _Verdict | None], list[ResearchError]]:
    """Run the Statement Check over one part's (or the bottom line's)
    candidates, through the shared gate (spec §6.5, D8, PD-12)."""
    if not candidates:
        return {}, []
    # Imported at call time, not at module scope: the unit tests and the
    # offline audit harness both substitute the checker by assigning
    # ``evidence_verifier.check_statements``, and a module-level ``from``
    # would bind the real function before that assignment could be seen.
    from deep_research.agents.evidence_verifier import StatementCheckItem, check_statements

    items = [
        StatementCheckItem(label=c.key, text=c.text, findings=c.findings,
                           labels=[_finding_label(f) for f in c.findings], passages=passages)
        for c in candidates
    ]
    try:
        return await check_statements(
            provider, items, question=question, fingerprint=fingerprint,
            batch_size=batch_size, gate=gate,
        )
    except ProviderConfigurationError:
        raise
    except (ProviderError, StructuredOutputError, ValidationError) as error:
        return {}, [agent_error(
            agent_name=REPORT_WRITER_NAME,
            error_type="report_writer_statement_check_failed",
            message="The report writer's statement check failed; every drafted point was kept unchanged.",
            details={"exception_type": type(error).__name__},
        )]


# --- the section and bottom-line draft calls (F10's retry ladder) ----------


async def _attempt_section_draft(
    provider: AgentCompleter, messages: list[ChatMessage], *, agent_name: str,
    fingerprint: Callable[[str], object] | None, coverage_id: str,
) -> tuple[SectionDraft | None, list[ResearchError]]:
    errors: list[ResearchError] = []
    for attempt, effort in enumerate(_WRITER_ATTEMPT_EFFORTS, start=1):
        if fingerprint is not None:
            fingerprint(SectionDraft.__name__)
        try:
            if effort is None:
                result = await provider.complete_structured(messages, SectionDraft, agent_name=agent_name)
            else:
                result = await provider.complete_structured(
                    messages, SectionDraft, agent_name=agent_name, reasoning_effort=effort)
        except ProviderOutputLimitError as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_section_failed",
                message="A part's draft reached the provider's output limit.",
                details={"coverage_id": coverage_id, "attempt": attempt, "exception_type": type(error).__name__},
            ))
            continue
        except ProviderConfigurationError:
            raise
        except (ProviderError, StructuredOutputError, ValidationError) as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_section_failed",
                message="A part's draft failed.",
                details={"coverage_id": coverage_id, "attempt": attempt, "exception_type": type(error).__name__},
            ))
            return None, errors
        return result, errors
    errors.append(agent_error(
        agent_name=agent_name, error_type="report_writer_section_failed",
        message="A part's draft was truncated on every attempt.",
        details={"coverage_id": coverage_id, "attempt": len(_WRITER_ATTEMPT_EFFORTS)},
    ))
    return None, errors


async def _attempt_bottom_line_draft(
    provider: AgentCompleter, messages: list[ChatMessage], *, agent_name: str,
    fingerprint: Callable[[str], object] | None,
) -> tuple[BottomLineDraft | None, list[ResearchError]]:
    errors: list[ResearchError] = []
    for attempt, effort in enumerate(_WRITER_ATTEMPT_EFFORTS, start=1):
        if fingerprint is not None:
            fingerprint(BottomLineDraft.__name__)
        try:
            if effort is None:
                result = await provider.complete_structured(messages, BottomLineDraft, agent_name=agent_name)
            else:
                result = await provider.complete_structured(
                    messages, BottomLineDraft, agent_name=agent_name, reasoning_effort=effort)
        except ProviderOutputLimitError as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_bottom_line_failed",
                message="The bottom line's draft reached the provider's output limit.",
                details={"attempt": attempt, "exception_type": type(error).__name__},
            ))
            continue
        except ProviderConfigurationError:
            raise
        except (ProviderError, StructuredOutputError, ValidationError) as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_bottom_line_failed",
                message="The bottom line's draft failed.",
                details={"attempt": attempt, "exception_type": type(error).__name__},
            ))
            return None, errors
        return result, errors
    errors.append(agent_error(
        agent_name=agent_name, error_type="report_writer_bottom_line_failed",
        message="The bottom line's draft was truncated on every attempt.",
        details={"attempt": len(_WRITER_ATTEMPT_EFFORTS)},
    ))
    return None, errors


# --- per-part orchestration (spec §6.5, §6.8, §6.9) -------------------------


@dataclass
class _PartOutcome:
    job: PartJob
    section: ReportSection | None
    status: str   # "written" | "carried_over" | "failed" | "empty"
    errors: list[ResearchError]
    verdicts: dict[str, str]                      # temp statement id -> verdict string
    rejected: list[RejectedDraftPoint] = field(default_factory=list)
    dropped_marks: list[tuple[str, str]] = field(default_factory=list)


async def _run_part(
    job: PartJob, task: ReportWriterTask, *, provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None, section_gate: asyncio.Semaphore,
    check_gate: asyncio.Semaphore, batch_size: int, label_urls: Mapping[str, str],
) -> _PartOutcome:
    if not job.findings and not job.context_findings:
        return _PartOutcome(job=job, section=None, status="empty", errors=[], verdicts={})

    if not job.redraft:
        # §6.9: carried over unchanged -- same points, verdicts and marks, no call.
        section = job.previous
        verdicts = {}
        if section is not None and task.previous is not None:
            for point in section.points:
                if point.statement is not None:
                    verdicts[point.statement.statement_id] = task.previous.statement_verdicts.get(
                        point.statement.statement_id, "unchecked")
        status = "carried_over" if section is not None else "empty"
        return _PartOutcome(job=job, section=section, status=status, errors=[], verdicts=verdicts)

    messages = section_messages(task, job)
    async with section_gate:
        draft, draft_errors = await _attempt_section_draft(
            provider, messages, agent_name=REPORT_WRITER_NAME, fingerprint=fingerprint,
            coverage_id=job.coverage_id,
        )
    if draft is None:
        return _PartOutcome(job=job, section=None, status="failed", errors=draft_errors, verdicts={})

    placed_ids = {finding_fingerprint(f) for f in [*job.findings, *job.context_findings]}
    part_labels = {label: f for label, f in task.registry if finding_fingerprint(f) in placed_ids}
    all_labels = dict(task.registry)
    context_ids = {finding_fingerprint(f) for f in job.context_findings}
    context_only_labels = {label for label, f in task.registry if finding_fingerprint(f) in context_ids}

    numbers = iter(range(1, 10_000))
    rejected: list[RejectedDraftPoint] = []
    candidates: list[_Candidate] = []
    for n, point in enumerate(draft.points):
        candidates.extend(_consider_section_point(
            point, f"section[{job.coverage_id}].points[{n}]", part_labels=part_labels,
            all_labels=all_labels, context_only_labels=context_only_labels,
            numbers=numbers, rejected=rejected, key_prefix=f"P{job.order:02d}.",
        ))

    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
    )

    stated_rows: set[str] = set()
    dropped_marks: list[tuple[str, str]] = []
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=stated_rows, task_facts=task.facts,
            task_targets=task.targets, label_urls=label_urls, dropped_marks=dropped_marks,
            rejected=rejected,
        )
        if point is not None:
            points.append(point)
            verdict_map[point.statement_id] = verdict_string

    title = " ".join(draft.title.split())[:_SECTION_TITLE_CHARS] or job.sub_topic_title
    section = ReportSection(title=title, points=points, coverage_id=job.coverage_id) if points else None
    return _PartOutcome(job=job, section=section, status="written",
                        errors=[*draft_errors, *check_errors], verdicts=verdict_map,
                        rejected=rejected, dropped_marks=dropped_marks)


def _bottom_line_fallback(
    required_sections: Sequence[ReportSection], other_sections: Sequence[ReportSection],
) -> list[ReportPoint]:
    """§6.8: up to ``MAX_BOTTOM_LINE_SENTENCES`` kept checked points, the first
    of each part with a required target, then the first of the other parts."""
    points: list[ReportPoint] = []
    for section in [*required_sections, *other_sections]:
        if section.points:
            points.append(section.points[0])
    return points[:MAX_BOTTOM_LINE_SENTENCES]


async def _run_bottom_line(
    task: ReportWriterTask, outcomes: Sequence[_PartOutcome], *, provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None, check_gate: asyncio.Semaphore,
    batch_size: int, label_urls: Mapping[str, str],
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]]]:
    """The bottom-line call (spec §6.6), started once every part task has
    finished. Returns (points, temp verdicts, errors, rejected, dropped_marks)."""
    checked_sections: list[ReportSection] = []
    cited_by_sections: dict[str, Finding] = {}
    label_by_finding_id = {finding_fingerprint(f): label for label, f in task.registry}
    for outcome in outcomes:
        if outcome.section is None:
            continue
        kept_points = [
            point for point in outcome.section.points
            if point.statement is not None
            and outcome.verdicts.get(point.statement.statement_id) in ("consistent", "corrected")
        ]
        if not kept_points:
            continue
        checked_sections.append(outcome.section.model_copy(update={"points": kept_points}))
        for point in kept_points:
            for finding_id in point.statement.finding_ids:
                label = label_by_finding_id.get(finding_id)
                if label:
                    cited_by_sections[label] = dict(task.registry)[label]

    if not checked_sections:
        if any(outcome.status != "empty" for outcome in outcomes):
            error = agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_provider_error",
                message="Every part failed; the report has no bottom line.", recoverable=False,
            )
            return [], {}, [error], [], []
        return [], {}, [], [], []

    is_redraft = bool(task.defects)
    previous_points = task.previous.summary if (is_redraft and task.previous) else []
    bottom_line_defects: list[ReviewDefect] = []
    if is_redraft:
        _, _, bottom_line_defects = _route_defects(task.defects, task.previous, task.targets)
    messages = bottom_line_messages(task, checked_sections, previous=previous_points,
                                    defects=bottom_line_defects)
    draft, draft_errors = await _attempt_bottom_line_draft(
        provider, messages, agent_name=REPORT_WRITER_NAME, fingerprint=fingerprint,
    )

    if draft is None:
        required_coverage = {t.coverage_id for t in task.targets if t.required}
        with_required = [s for s in checked_sections if s.coverage_id in required_coverage]
        others = [s for s in checked_sections if s.coverage_id not in required_coverage]
        fallback_points = _bottom_line_fallback(with_required, others)
        error = agent_error(
            agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
            message="The bottom-line draft failed twice; kept section points stand in for it.",
        )
        verdicts = {p.statement_id: "consistent" for p in fallback_points if p.statement is not None}
        return fallback_points, verdicts, [*draft_errors, error], [], []

    numbers = iter(range(1, 10_000))
    rejected: list[RejectedDraftPoint] = []
    candidates: list[_Candidate] = []
    for n, point in enumerate(draft.sentences):
        candidates.extend(_consider_bottom_line_point(
            point, f"bottom_line[{n}]", cited_by_sections=cited_by_sections,
            numbers=numbers, rejected=rejected, key_prefix="B",
        ))

    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
    )

    stated_rows: set[str] = set()
    dropped_marks: list[tuple[str, str]] = []
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=stated_rows, task_facts=task.facts,
            task_targets=task.targets, label_urls=label_urls, dropped_marks=dropped_marks,
            rejected=rejected,
        )
        if point is not None:
            points.append(point)
            verdict_map[point.statement_id] = verdict_string

    kept, overflow = points[:MAX_BOTTOM_LINE_SENTENCES], points[MAX_BOTTOM_LINE_SENTENCES:]
    for extra in overflow:
        rejected.append(RejectedDraftPoint(
            where="bottom_line", text=extra.text,
            finding_labels=list(extra.statement.finding_ids) if extra.statement else [],
            reason="over the bottom line's four sentences",
        ))
        verdict_map.pop(extra.statement_id, None)

    return kept, verdict_map, [*draft_errors, *check_errors], rejected, dropped_marks


def _renumber(
    bottom_line_points: list[ReportPoint], sections: list[ReportSection],
) -> tuple[list[ReportPoint], list[ReportSection], dict[str, str]]:
    """Spec §6.7: statement ids renumbered S001… in render order (bottom
    line, then sections in plan order), so the output is identical whatever
    order the calls completed in."""
    remap: dict[str, str] = {}
    counter = iter(range(1, 100_000))

    def renumber_point(point: ReportPoint) -> ReportPoint:
        if point.statement is None:
            return point
        new_id = f"S{next(counter):03d}"
        remap[point.statement.statement_id] = new_id
        new_statement = point.statement.model_copy(update={"statement_id": new_id})
        return point.model_copy(update={"statement": new_statement})

    new_summary = [renumber_point(point) for point in bottom_line_points]
    new_sections = [
        section.model_copy(update={"points": [renumber_point(point) for point in section.points]})
        for section in sections
    ]
    return new_summary, new_sections, remap


# --- §6.7 assembly (T4 compose): the table, page credits, unreachable ------


def _findings_by_url(findings: Sequence[Finding]) -> dict[str, Finding]:
    """Normalized source URL -> the first citable finding seen for it."""
    by_url: dict[str, Finding] = {}
    for finding in findings:
        normalized = normalize_source_url(finding.source_url)
        if normalized not in by_url:
            by_url[normalized] = finding
    return by_url


def _page_credit(
    url: str, *, findings_by_url: Mapping[str, Finding], reads: Mapping[str, ReadRecord],
    sources: Sequence[ScoredSource],
) -> PageCredit:
    """Spec §6.7/§8: the publisher from the page's own words (or the host),
    and the date in order: the Source Evaluator's validated
    ``publication_date``; else the read's own ``page_published``; else its
    ``page_updated`` (``date_kind`` marks which); never a figure's
    ``statement_date`` or its vintage -- those are the figure's, not the
    page's.
    """
    # Imported at call time, matching this module's other evidence_verifier
    # seams: no import cycle (evidence_verifier never imports this module at
    # top level), and it keeps the substitution point tests use consistent.
    from deep_research.agents.evidence_verifier import evaluated_page_date, page_owner

    finding = findings_by_url.get(url)
    read = reads.get(finding.read_id) if finding is not None else None
    publisher = page_owner(read) if read is not None else publisher_identity(url)
    if read is not None:
        validated = evaluated_page_date(sources, read)
        if validated:
            return PageCredit(publisher=publisher, date=validated, date_kind="published")
        if read.page_published:
            return PageCredit(publisher=publisher, date=read.page_published, date_kind="published")
        if read.page_updated:
            return PageCredit(publisher=publisher, date=read.page_updated, date_kind="updated")
    return PageCredit(publisher=publisher, date=None, date_kind=None)


def _unreachable_pages(
    sub_topics: Sequence[SubTopic], acquisition_state_by_target: Mapping[str, AcquisitionState],
) -> list[UnreachablePage]:
    """Spec §6.7/§10: for each sub-topic with >= 1 required target, its own
    denied URLs with the candidate record's title and denial reason,
    deduplicated, in plan order."""
    pages: list[UnreachablePage] = []
    seen: set[str] = set()
    for topic in sub_topics:
        if not any(target.required for target in topic.evidence_targets):
            continue
        state = acquisition_state_by_target.get(topic.coverage_id)
        if state is None:
            continue
        for url in state.denied_urls:
            normalized = normalize_source_url(url)
            if normalized in seen:
                continue
            seen.add(normalized)
            record = state.candidate_records.get(normalized) or state.candidate_records.get(url)
            pages.append(UnreachablePage(
                url=url,
                title=record.title if record is not None else "",
                reason=(record.denial_reason or "") if record is not None else "",
            ))
    return pages


def _assemble_composition(composition: ReportComposition, task: ReportWriterTask) -> ReportComposition:
    """Spec §6.7's remaining bullets, in the stated order: the table (driven
    by ``answer_kind``, already frozen on ``composition``), then page
    credits for every URL the table or a kept statement now cites, then the
    unreachable pages. Table builders and citation order are pure functions
    of ``composition`` alone (T2/T3); nothing here re-reads a page.
    """
    table = _build_table(composition)
    composition = composition.model_copy(update={"table": table})
    findings_by_url = _findings_by_url(composition.findings)
    page_credits = {
        citation.url: _page_credit(
            citation.url, findings_by_url=findings_by_url, reads=task.reads, sources=task.sources,
        )
        for citation in written_citations(composition)
    }
    composition = composition.model_copy(update={"page_credits": page_credits})
    unreachable = _unreachable_pages(task.sub_topics, task.acquisition_state_by_target)
    return composition.model_copy(update={"unreachable": unreachable})


async def compose_written_report(
    task: ReportWriterTask,
    *,
    provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None = None,
    batch_size: int | None = None,
    concurrency: int | None = None,
    section_concurrency: int = DEFAULT_WRITER_SECTION_CONCURRENCY,
) -> ReportComposition:
    """Partition, draft every part in parallel, pipeline each part's
    Statement Check off its own draft, then write the bottom line last from
    the checked section statements (spec §6). A redraft re-asks only the
    parts a material defect names (§6.9); every other part is carried over
    unchanged. ``batch_size``/``concurrency`` are the Statement Check's
    bounds (``None`` uses this module's defaults); ``section_concurrency``
    bounds how many section drafts run at once.
    """
    resolved_batch_size = batch_size if batch_size is not None else _CHECK_BATCH_SIZE_DEFAULT
    resolved_concurrency = concurrency if concurrency is not None else _CHECK_CONCURRENCY_DEFAULT
    label_urls = {label: f.source_url for label, f in task.registry}

    if not task.registry:
        empty = ReportComposition(
            question=task.question, session_id=task.session_id, iteration=task.iteration,
            max_extra_passes=task.max_extra_passes, as_of=task.as_of, scope=task.scope,
            sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
            summary=[], sections=[], parts=[], rejected=[], rejected_points=[],
            fact_rows=list(task.facts), not_found=list(task.not_found), finding_labels={},
            statement_verdicts={}, statement_passages=task.passages, generated_on=task.generated_on,
            errors=[agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_no_verified_findings",
                message="No verified finding could be cited; the report is the recorded facts alone.",
            )],
            answer_kind=task.answer_kind,
        )
        return _assemble_composition(empty, task)

    citable = citable_findings(task.findings)
    placements, _unplaced = report_parts(citable, task.targets, task.sub_topics)
    src_by_url = sources_by_url(task.sources)
    previous_sections_by_coverage = {
        s.coverage_id: s for s in (task.previous.sections if task.previous else []) if s.coverage_id
    }
    is_redraft = bool(task.defects)
    if is_redraft:
        routed_coverage_ids, defects_by_coverage, _ = _route_defects(task.defects, task.previous, task.targets)
    else:
        routed_coverage_ids, defects_by_coverage = set(), {}

    jobs: list[PartJob] = []
    for order, placement in enumerate(placements):
        regular = [f for f in placement.findings if not is_context_only(f, src_by_url)]
        context = [f for f in placement.findings if is_context_only(f, src_by_url)]
        targets_here = [t for t in task.targets if t.coverage_id == placement.coverage_id]
        previous_section = previous_sections_by_coverage.get(placement.coverage_id)
        if not is_redraft:
            redraft_this, defects_here = True, []
        elif placement.coverage_id in routed_coverage_ids:
            redraft_this, defects_here = True, defects_by_coverage.get(placement.coverage_id, [])
        else:
            redraft_this, defects_here = False, []
        jobs.append(PartJob(
            coverage_id=placement.coverage_id, sub_topic_title=placement.sub_topic_title,
            order=order, targets=targets_here, findings=regular, context_findings=context,
            previous=previous_section, defects=defects_here, redraft=redraft_this,
        ))

    section_gate = asyncio.Semaphore(max(1, section_concurrency))
    check_gate = asyncio.Semaphore(max(1, resolved_concurrency))
    outcomes: list[_PartOutcome | None] = [None] * len(jobs)

    async def run_and_store(index: int, job: PartJob) -> None:
        outcomes[index] = await _run_part(
            job, task, provider=provider, fingerprint=fingerprint, section_gate=section_gate,
            check_gate=check_gate, batch_size=resolved_batch_size, label_urls=label_urls,
        )

    try:
        async with asyncio.TaskGroup() as group:
            for index, job in enumerate(jobs):
                group.create_task(run_and_store(index, job))
    except* ProviderConfigurationError as caught:
        raise caught.exceptions[0]

    resolved_outcomes: list[_PartOutcome] = [outcome for outcome in outcomes if outcome is not None]

    (bottom_line_points, bottom_line_verdicts, bottom_line_errors,
     bottom_line_rejected, bottom_line_dropped_marks) = await _run_bottom_line(
        task, resolved_outcomes, provider=provider, fingerprint=fingerprint,
        check_gate=check_gate, batch_size=resolved_batch_size, label_urls=label_urls,
    )

    sections = [outcome.section for outcome in resolved_outcomes if outcome.section is not None]
    new_summary, new_sections, remap = _renumber(bottom_line_points, sections)

    temp_verdicts: dict[str, str] = dict(bottom_line_verdicts)
    for outcome in resolved_outcomes:
        temp_verdicts.update(outcome.verdicts)
    statement_verdicts = {remap[old]: verdict for old, verdict in temp_verdicts.items() if old in remap}

    parts_records = [
        ReportPart(
            coverage_id=job.coverage_id, sub_topic_title=job.sub_topic_title,
            finding_ids=[finding_fingerprint(f) for f in [*job.findings, *job.context_findings]],
            context_finding_ids=[finding_fingerprint(f) for f in job.context_findings],
            status=outcome.status,
        )
        for job, outcome in zip(jobs, resolved_outcomes)
    ]

    all_errors: list[ResearchError] = list(bottom_line_errors)
    all_rejected: list[RejectedDraftPoint] = []
    raw_dropped_marks: list[tuple[str, str]] = list(bottom_line_dropped_marks)
    for outcome in resolved_outcomes:
        all_errors.extend(outcome.errors)
        all_rejected.extend(outcome.rejected)
        raw_dropped_marks.extend(outcome.dropped_marks)
    all_rejected.extend(bottom_line_rejected)
    dropped_marks = [f"{remap.get(key, key)}: {message}" for key, message in raw_dropped_marks]

    composed = ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_extra_passes=task.max_extra_passes, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=new_summary, sections=new_sections, parts=parts_records,
        rejected=[r.reason for r in all_rejected], rejected_points=all_rejected,
        fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_fingerprint(f) for label, f in task.registry},
        statement_verdicts=statement_verdicts, statement_passages=task.passages,
        generated_on=task.generated_on, errors=all_errors, answer_kind=task.answer_kind,
        dropped_marks=dropped_marks,
    )
    return _assemble_composition(composed, task)


def finding_memory_payload(finding: Finding, *, session_id: str) -> tuple[str, dict[str, object]]:
    """What long-term memory keeps of one cited finding of an accepted report."""
    figures = [
        f"{r.figure.value} {r.figure.unit} ({r.context.kind}, {r.context.period or 'period not stated'}, {r.context.organisation})"
        for r in (finding.verification.figure_results if finding.verification else [])
        if r.kept and r.context is not None
    ]
    metadata: dict[str, object] = {
        "session_id": session_id, "finding_id": finding_fingerprint(finding),
        "source_url": finding.source_url, "source_title": finding.source_title,
        "figures": "; ".join(figures),
        "verification": finding.verification.status if finding.verification else "unchecked",
        "context_unchecked": bool(finding.verification and finding.verification.context_unchecked),
    }
    return finding.snippet or finding.content, metadata


def report_written_event(result: WrittenReport) -> ResearchEvent:
    composition = result.composition
    failed_parts = [p.coverage_id for p in composition.parts if p.status == "failed"]
    return agent_event(
        agent_name=REPORT_WRITER_NAME,
        event_type="report_writer.report.written",
        message=(
            f"Wrote {result.statement_count} statement(s) citing {result.citation_count} "
            f"source(s); {result.refused_count} drafted point(s) refused."
        ),
        metadata={
            "statements": result.statement_count,
            "citations": result.citation_count,
            "refused": result.refused_count,
            "fact_rows": len(composition.fact_rows),
            "not_found": len(composition.not_found),
            "table": composition.table.shape if composition.table else None,
            "parts": len(composition.parts),
            "failed_parts": failed_parts,
        },
    )


class ReportWriterAgent(BaseAgent[WrittenReport]):
    """Write the reader-facing prose of a research report from verified findings.

    Runs no ReAct loop: one call drafts each plan part's section, pipelined
    against its own Statement Check, and one more writes the bottom line
    last. Everything structural -- the table, the list of what could not be
    confirmed, the labels and the sources -- is rendered locally from the
    verified snapshot, so the required sections exist and every fact is
    cited even when a part's call fails.
    """

    name = REPORT_WRITER_NAME
    description = "Write the reader-facing prose of a research report from verified findings."
    allowed_tools = ("write_document", "save_to_memory")

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
        clock: Clock = utc_now,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
            model_profile=model_profile,
        )
        probe = clock()
        if probe.tzinfo is None or probe.utcoffset() is None:
            raise AgentConfigurationError(
                "ReportWriterAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._clock = clock

    @property
    def output_schema(self) -> type[WrittenReport]:
        return WrittenReport

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SECTION_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> ReportWriterTask:
        """Bind this run to the verified findings recorded so far."""
        targets = [target for topic in state.sub_topics for target in topic.evidence_targets]
        findings = list(state.verified_findings)
        citable = citable_findings(findings)
        answered = answered_target_ids(citable, targets, sub_topics=state.sub_topics)
        src_by_url = sources_by_url(state.evaluated_sources)
        findings_by_id = {finding_fingerprint(f): f for f in citable}
        # §6.13: the Not-found computation excludes context-only answers, so
        # a required target answered only that way reads as unconfirmed; the
        # gate's own ``answered`` (below, unfiltered) still accounts for it.
        answered_for_report: dict[str, list[str]] = {}
        for target_id, finding_ids in answered.items():
            kept = [
                fid for fid in finding_ids
                if fid not in findings_by_id or not is_context_only(findings_by_id[fid], src_by_url)
            ]
            if kept:
                answered_for_report[target_id] = kept
        return ReportWriterTask(
            instruction=state.original_question,
            session_id=state.session_id,
            iteration=state.iteration,
            max_extra_passes=state.max_extra_passes,
            question=state.original_question,
            as_of=report_as_of(findings=findings, reads=list(state.read_records.values())),
            scope=report_scope(state.sub_topics),
            generated_on=self._clock().date().isoformat(),
            answer_kind=state.answer_contract.answer_kind if state.answer_contract else None,
            sub_topics=list(state.sub_topics),
            targets=targets,
            findings=findings,
            sources=list(state.evaluated_sources),
            registry=finding_registry(findings, targets, state.sub_topics),
            facts=fact_rows(findings, targets, state.sub_topics),
            not_found=not_found_targets(state.sub_topics, answered_for_report, state.acquisition_state_by_target),
            answered=answered,
            reads=dict(state.read_records),
            passages=statement_passages(findings, state.read_records),
            defects=material_defects(state.report_review),
            previous=state.composition,
            acquisition_state_by_target=dict(state.acquisition_state_by_target),
        )

    async def _compose_result(self, task: ReportWriterTask) -> WrittenReport:
        composition = await compose_written_report(
            task, provider=self.provider, fingerprint=self.fingerprint_call,
            batch_size=self.config.verifier_batch_size,
            concurrency=self.config.verifier_concurrency,
            section_concurrency=self.config.writer_section_concurrency,
        )
        return WrittenReport(
            markdown=render_written_report(composition),
            evidence_markdown=render_finding_log(composition),
            composition=composition,
            statement_count=len(composition.statements),
            citation_count=len(written_citations(composition)),
            refused_count=len(composition.rejected_points),
        )

    async def finalize(self, task: AgentTask, run: ReActRun) -> WrittenReport | None:
        """Adapt composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this hook
        signature has nowhere to return.
        """
        del run
        if not isinstance(task, ReportWriterTask):
            raise AgentConfigurationError(
                "ReportWriterAgent.finalize requires a ReportWriterTask"
            )
        return await self._compose_result(task)

    def state_update(
        self,
        result: WrittenReport | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The ``BaseAgent`` hook's own answer; ``run`` never calls this."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
            update["report_evidence"] = result.evidence_markdown
            update["composition"] = result.composition
            update["unique_source_count"] = result.citation_count
            update["events"] = [report_written_event(result)]
        return update

    def _require_tool(self, name: str) -> BaseTool:
        """The declared tool the terminal writer needs, or a loud failure.

        ``allowed_tools`` is validated at construction, so a missing tool here
        means the agent was assembled around this method's back.
        """
        tool = self.toolset.get(name)
        if tool is None:
            raise AgentConfigurationError(f"{name} was not injected")
        return tool

    async def publish_document(
        self,
        *,
        filename: str,
        content: str,
    ) -> ToolResult:
        """Write one composed artifact through ``write_document``.

        Called only by the terminal finalizer. Returns the tool's own result,
        including a failure: a write that did not happen is an outcome the
        finalizer records, not an exception it has to catch.
        """
        tool = self._require_tool("write_document")
        return await tool.execute(filename=filename, content=content)

    async def publish_finding(
        self,
        *,
        content: str,
        metadata: Mapping[str, object],
    ) -> ToolResult:
        """Save one verified claim through ``save_to_memory``.

        Called only by the terminal finalizer, and only for a report whose
        terminal quality status is ``accepted``.
        """
        tool = self._require_tool("save_to_memory")
        return await tool.execute(content=content, metadata=dict(metadata))

    async def run(self, state: ResearchState) -> AgentRun[WrittenReport]:
        """Partition, draft and compose the report, recording every count.

        No ReAct loop runs, so the returned ``ReActRun`` is synthetic with
        zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when there was something to cite and every
        non-empty part's draft failed (spec §6.8): an empty verified
        snapshot is not a failure, it is an honest report of no evidence.
        """
        task = self.build_task(state)
        async with self.tracker.agent_span(self.name) as span:
            result = await self._compose_result(task)
            span.set_outputs({
                "agent_name": self.name,
                "statement_count": result.statement_count,
                "citation_count": result.citation_count,
                "refused_count": result.refused_count,
            })
        composition = result.composition
        any_written = any(part.status in ("written", "carried_over") for part in composition.parts)
        stop_reason = "finished" if (any_written or not task.registry) else "provider_error"
        react = ReActRun(
            agent_name=self.name,
            stop_reason=stop_reason,
            errors=list(composition.errors),
        )
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=list(composition.errors),
            state_update={
                "report": result.markdown,
                "report_evidence": result.evidence_markdown,
                "composition": result.composition,
                "unique_source_count": result.citation_count,
                "errors": list(composition.errors),
                "events": [report_written_event(result)],
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
